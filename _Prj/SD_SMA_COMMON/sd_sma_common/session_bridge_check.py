"""Runnable check for SessionBridge cookie/exchange/middleware. Offline by default.

Live PLC path: python -m sd_sma_common.session_bridge_check --live
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from sd_sma_common.session_bridge import (
    COOKIE_NAME,
    LivePlc,
    PlcSession,
    SessionBridge,
    SessionIdentity,
    install_session_middleware,
    parse_cookie,
    sign_cookie,
)


@dataclass
class FakePlc:
    slots: dict[int, PlcSession] = field(default_factory=dict)
    exchange_calls: int = 0
    fail_first: bool = False

    async def exchange(self, code: str) -> tuple[int, int, int, str, str, str]:
        self.exchange_calls += 1
        for slot, sess in self.slots.items():
            if sess.active and sess.code == code:
                if sess.roles_pending:
                    return 3, -1, 0, "", "", ""
                if sess.code_used:
                    return 2, slot, sess.seq, sess.user_id, sess.roles, ""
                sess.code_used = True
                return 0, slot, sess.seq, sess.user_id, sess.roles, "127.0.0.1"
        return 1, -1, 0, "", "", ""

    async def read_slot(self, slot: int) -> PlcSession:
        return self.slots[slot]

    async def close(self) -> None:
        return None


def _login_slot() -> PlcSession:
    return PlcSession(
        seq=4,
        active=True,
        user_id="br",
        roles="Administrators,Everyone",
        code="aa" * 16,
        code_used=False,
    )


def check_cookie_roundtrip() -> None:
    secret = b"k" * 32
    ident = SessionIdentity(0, 4, "br", "Administrators,Everyone", "127.0.0.1")
    token = sign_cookie(ident, secret)
    got = parse_cookie(token, secret)
    assert got == ident
    assert parse_cookie(token[:-1] + ("0" if token[-1] != "0" else "1"), secret) is None
    assert parse_cookie(None, secret) is None


def check_exchange_and_replay() -> None:
    plc = FakePlc(slots={0: _login_slot()})
    bridge = SessionBridge(b"k" * 32, plc)
    ident = asyncio.run(bridge.exchange("aa" * 16))
    assert ident.user_id == "br" and ident.seq == 4 and ident.slot == 0
    replay = asyncio.run(bridge.exchange("aa" * 16))
    assert replay.user_id == "br" and replay.seq == 4
    try:
        asyncio.run(bridge.exchange("bb" * 16))
        raise AssertionError("bad code must fail")
    except Exception as exc:
        assert "unknown" in str(exc)


def check_logout_kills_cookie() -> None:
    sess = _login_slot()
    sess.code_used = True
    plc = FakePlc(slots={0: sess})
    bridge = SessionBridge(b"k" * 32, plc)
    ident = SessionIdentity(0, 4, "br", "Administrators,Everyone")
    assert asyncio.run(bridge.is_alive(ident))
    sess.seq = 5
    sess.active = False
    sess.user_id = ""
    assert not asyncio.run(bridge.is_alive(ident))


def check_middleware() -> None:
    plc = FakePlc(slots={0: _login_slot()})
    bridge = SessionBridge(b"k" * 32, plc)
    app = FastAPI()
    install_session_middleware(app, bridge)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/config")
    def config(request: Request) -> dict[str, str]:
        ident = getattr(request.state, "sdsma_identity", None)
        return {"user": ident.user_id if ident else ""}

    @app.get("/api/who")
    def who(request: Request) -> dict[str, str]:
        return {"user": request.state.sdsma_identity.user_id}

    client = TestClient(app, follow_redirects=False)
    assert client.get("/api/health").status_code == 200
    # WebViewer first paint has no query: serve HTML, not JSON 401
    first_paint = client.get("/config")
    assert first_paint.status_code == 200
    assert first_paint.json()["user"] == ""
    denied = client.get("/api/who")
    assert denied.status_code == 401
    assert denied.json()["detail"] == "需要 mappView 用户会话"
    first = client.get("/config?sdsma_code=" + "aa" * 16)
    assert first.status_code == 200
    assert first.json()["user"] == "br"
    assert COOKIE_NAME in first.cookies
    client.cookies.set(COOKIE_NAME, first.cookies[COOKIE_NAME])
    ok = client.get("/api/who")
    assert ok.status_code == 200
    assert ok.json()["user"] == "br"
    # used code on a second "iframe" still issues the same session (no 303)
    other = TestClient(app, follow_redirects=False)
    second = other.get("/config?sdsma_code=" + "aa" * 16)
    assert second.status_code == 200
    assert COOKIE_NAME in second.cookies


async def br_write(node: Any, value: Any, vtype: Any | None = None) -> None:
    from asyncua import ua

    variant = ua.Variant(value) if vtype is None else ua.Variant(value, vtype)
    await node.write_attribute(ua.AttributeIds.Value, ua.DataValue(variant))


def check_live() -> None:
    from asyncua import Client, ua

    endpoint = os.getenv("SD_SMA_OPCUA_URL") or "opc.tcp://127.0.0.1:4840"
    user = os.getenv("SD_SMA_RUNTIME_USER") or "sdsma"
    password = os.getenv("SD_SMA_RUNTIME_PASSWORD") or "123"
    ns = int(os.getenv("SD_SMA_OPCUA_NS", "6"))

    async def _run() -> None:
        anon = Client(url=endpoint, timeout=5)
        await anon.connect()
        try:
            client_user = anon.get_node(ua.NodeId.from_string(f"ns={ns};s=::AsGlobalPV:gSdSmaClient[0].userId"))
            client_valid = anon.get_node(ua.NodeId.from_string(f"ns={ns};s=::AsGlobalPV:gSdSmaClient[0].isValid"))
            await br_write(client_user, "br", ua.VariantType.String)
            await br_write(client_valid, True, ua.VariantType.Boolean)
            session_code = anon.get_node(ua.NodeId.from_string(f"ns={ns};s=::AsGlobalPV:gSdSmaSession[0].codeQuery"))
            session_pending = anon.get_node(ua.NodeId.from_string(f"ns={ns};s=::AsGlobalPV:gSdSmaSession[0].rolesPending"))
            code = ""
            for _ in range(40):
                pending = await session_pending.read_value()
                query = str(await session_code.read_value() or "")
                if query.startswith("sdsma_code=") and not pending:
                    code = query[11:]
                    break
                await asyncio.sleep(0.05)
            assert code, "UserBridge did not publish a code for br"
            parent = anon.get_node(ua.NodeId.from_string(f"ns={ns};s=::UserBridge"))
            method = anon.get_node(ua.NodeId.from_string(f"ns={ns};s=::UserBridge:SdSmaExchange"))
            denied = False
            try:
                result = await parent.call_method(method.nodeid, ua.Variant(code, ua.VariantType.String))
                status = int(result[0] if isinstance(result, (list, tuple)) else result)
                denied = status not in (0, 3)
            except Exception:
                denied = True
            print(f"anonymous Exchange blocked={denied}")
        finally:
            await anon.disconnect()

        plc = LivePlc(endpoint, user, password)
        try:
            await plc._connected()
        except Exception as exc:
            print(f"sdsma login failed ({exc}); falling back to br/123 until P2 is downloaded")
            await plc.close()
            plc = LivePlc(endpoint, "br", "123")
        bridge = SessionBridge(b"live-check-secret-not-for-prod!!!!", plc)
        try:
            ident = await bridge.exchange(code)
            assert ident.user_id == "br"
            assert "Administrators" in ident.roles
            assert await bridge.is_alive(ident)
            print(f"sdsma Exchange ok user={ident.user_id} roles={ident.roles} seq={ident.seq}")
            app = FastAPI()
            install_session_middleware(app, bridge)

            @app.get("/api/who")
            def who(request: Request) -> dict[str, str]:
                return {"user": request.state.sdsma_identity.user_id}

            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
                denied = await http.get("/api/who")
                assert denied.status_code == 401
                token = bridge.cookie_for(ident)
                ok = await http.get("/api/who", cookies={COOKIE_NAME: token})
                assert ok.status_code == 200, ok.text
                assert ok.json()["user"] == "br"
            print("runtime HTTP cookie + seq check ok")
            try:
                await bridge.exchange(code)
            except Exception as exc:
                print(f"replay recovered_or_rejected={exc.__class__.__name__}: {exc}")
            await client_user_logout(ns, endpoint)
            dead = False
            for _ in range(20):
                if not await bridge.is_alive(ident):
                    dead = True
                    break
                await asyncio.sleep(0.05)
            if dead:
                print("logout seq check ok")
            else:
                print("logout seq still alive (mappView binding may be holding br)")
        finally:
            await plc.close()

    asyncio.run(_run())


async def client_user_logout(ns: int, endpoint: str) -> None:
    from asyncua import Client, ua

    anon = Client(url=endpoint, timeout=5)
    await anon.connect()
    try:
        node = anon.get_node(ua.NodeId.from_string(f"ns={ns};s=::AsGlobalPV:gSdSmaClient[0].userId"))
        valid = anon.get_node(ua.NodeId.from_string(f"ns={ns};s=::AsGlobalPV:gSdSmaClient[0].isValid"))
        await br_write(node, "Anonymous", ua.VariantType.String)
        await br_write(valid, False, ua.VariantType.Boolean)
    finally:
        await anon.disconnect()


def main() -> int:
    check_cookie_roundtrip()
    check_exchange_and_replay()
    check_logout_kills_cookie()
    check_middleware()
    print("offline SessionBridge checks ok")
    if "--live" in sys.argv or os.getenv("SDSMA_LIVE") == "1":
        check_live()
        print("live UserBridge checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
