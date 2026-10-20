"""HMI UserBridge session: one-time OPC UA exchange -> host-level signed cookie.

See _Archive/_Docs/UserBridge 开发计划.md P3. Cookie is host-scoped (shared across 8090–8094).
PLC seq is the revocation signal; there is no cookie TTL.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

COOKIE_NAME = "sdsma_session"
CODE_PARAM = "sdsma_code"
MACHINE_TOKEN_ENV = "SD_SMA_WEB_TOKEN"
MACHINE_TOKEN_HEADER = "X-SD-SMA-Token"
DEFAULT_ENDPOINT = "opc.tcp://127.0.0.1:4840"
DEFAULT_USER = "sdsma"
DEFAULT_PASSWORD = "123"  # matches Physical/.../User.user Description for sdsma
EXEMPT_PATHS = frozenset({"/api/health"})
MAX_SLOTS = 4
ROLES_PENDING = 3

logger = logging.getLogger(__name__)


class SessionBridgeError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SessionIdentity:
    slot: int
    seq: int
    user_id: str
    roles: str
    ip_address: str = ""


@dataclass
class PlcSession:
    seq: int
    active: bool
    user_id: str
    roles: str
    code: str
    code_used: bool
    roles_pending: bool = False


class PlcBackend(Protocol):
    async def exchange(self, code: str) -> tuple[int, int, int, str, str, str]:
        """Return Status, Slot, Seq, UserId, Roles, IpAddress."""

    async def read_slot(self, slot: int) -> PlcSession:
        ...

    async def close(self) -> None:
        ...


def load_or_create_secret(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    try:
        os.write(fd, secret.encode("ascii"))
    finally:
        os.close(fd)
    return secret


def secret_from_env(data_root: Path | None = None) -> bytes:
    raw = (os.getenv("SD_SMA_SESSION_SECRET") or "").strip()
    if raw:
        return raw.encode("utf-8")
    file_raw = (os.getenv("SD_SMA_SESSION_SECRET_FILE") or "").strip()
    if file_raw:
        return Path(file_raw).read_text(encoding="utf-8").strip().encode("utf-8")
    root = data_root
    if root is None:
        env_root = (os.getenv("SD_SMA_DATA_ROOT") or "").strip()
        root = Path(env_root) if env_root else Path(".")
    return load_or_create_secret(root / "secrets" / "session.secret").encode("utf-8")


def machine_token_ok(provided: str | None) -> bool:
    expected = (os.getenv(MACHINE_TOKEN_ENV) or "").strip()
    if not expected or not provided or not provided.strip():
        return False
    return secrets.compare_digest(provided.strip(), expected)


def sign_cookie(identity: SessionIdentity, secret: bytes) -> str:
    payload = json.dumps(
        {
            "slot": identity.slot,
            "seq": identity.seq,
            "user_id": identity.user_id,
            "roles": identity.roles,
            "ip": identity.ip_address,
        },
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    mac = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"v1.{body}.{mac}"


def parse_cookie(value: str | None, secret: bytes) -> SessionIdentity | None:
    if not value:
        return None
    parts = value.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None
    body, mac = parts[1], parts[2]
    expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    pad = "=" * (-len(body) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(body + pad).decode("utf-8"))
        return SessionIdentity(
            slot=int(data["slot"]),
            seq=int(data["seq"]),
            user_id=str(data["user_id"]),
            roles=str(data.get("roles") or ""),
            ip_address=str(data.get("ip") or ""),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _extract_code(query: str) -> str:
    if query.startswith("sdsma_code="):
        return query[11:]
    return query


def _is_access_denied(exc: BaseException) -> bool:
    blob = f"{type(exc).__name__} {exc}".lower()
    return "baduseraccessdenied" in blob or "useraccessdenied" in blob


def _needs_session(path: str, method: str, allowed: set[str]) -> bool:
    if path in allowed:
        return False
    if path.startswith("/api/"):
        return True
    return method.upper() not in {"GET", "HEAD", "OPTIONS"}


def attach_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        path="/",
    )


class SessionBridge:
    def __init__(self, secret: bytes, plc: PlcBackend) -> None:
        self.secret = secret
        self.plc = plc

    @classmethod
    def from_env(cls, plc: PlcBackend | None = None) -> SessionBridge:
        secret = secret_from_env()
        return cls(secret, plc if plc is not None else LivePlc.from_env())

    def cookie_for(self, identity: SessionIdentity) -> str:
        return sign_cookie(identity, self.secret)

    def identity_from_cookie(self, value: str | None) -> SessionIdentity | None:
        return parse_cookie(value, self.secret)

    async def is_alive(self, identity: SessionIdentity) -> bool:
        try:
            sess = await self.plc.read_slot(identity.slot)
        except Exception:
            logger.debug("session seq check failed", exc_info=True)
            return False
        return (
            sess.active
            and sess.seq == identity.seq
            and sess.user_id == identity.user_id
        )

    async def exchange(self, code: str) -> SessionIdentity:
        code = (code or "").strip().lower()
        if len(code) != 32 or any(c not in "0123456789abcdef" for c in code):
            raise SessionBridgeError("invalid code", status=1)
        last_status = 1
        for _ in range(8):
            status, slot, seq, user_id, roles, ip_address = await self.plc.exchange(code)
            last_status = status
            if status == 0:
                return SessionIdentity(slot, seq, user_id, roles, ip_address)
            if status == ROLES_PENDING:
                await asyncio.sleep(0.05)
                continue
            if status == 2:
                recovered = await self._recover_used(code)
                if recovered is not None:
                    return recovered
                raise SessionBridgeError("code already used", status=2)
            raise SessionBridgeError("unknown code", status=status)
        raise SessionBridgeError("roles still pending", status=last_status)

    async def _recover_used(self, code: str) -> SessionIdentity | None:
        # Parallel WebViewers share one code; first Exchange consumes it, the rest
        # still carry the used code in the URL. codeQuery stays until logout.
        for slot in range(MAX_SLOTS):
            sess = await self.plc.read_slot(slot)
            if sess.active and sess.code_used and sess.code == code:
                return SessionIdentity(slot, sess.seq, sess.user_id, sess.roles, "")
        return None

    async def resolve_request(self, request: Request) -> SessionIdentity | None:
        existing = self.identity_from_cookie(request.cookies.get(COOKIE_NAME))
        if existing is not None and await self.is_alive(existing):
            return existing
        code = (request.query_params.get(CODE_PARAM) or "").strip()
        if not code:
            return None
        return await self.exchange(code)


class LivePlc:
    """asyncua transport to UserBridge on AR."""

    def __init__(
        self,
        endpoint: str,
        username: str,
        password: str,
        credentials: list[tuple[str, str]] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.username = username
        self.password = password
        self._credentials = credentials or [(username, password)]
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._ns = int(os.getenv("SD_SMA_OPCUA_NS", "6"))
        self._method_parent: Any = None
        self._method_nodeid: Any = None

    @classmethod
    def from_env(cls) -> LivePlc:
        user = os.getenv("SD_SMA_RUNTIME_USER") or DEFAULT_USER
        password = os.getenv("SD_SMA_RUNTIME_PASSWORD") or DEFAULT_PASSWORD
        chain = [(user, password), ("br", "123"), ("", "")]
        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str]] = []
        for item in chain:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return cls(
            os.getenv("SD_SMA_OPCUA_URL") or DEFAULT_ENDPOINT,
            unique[0][0],
            unique[0][1],
            credentials=unique,
        )

    async def close(self) -> None:
        async with self._lock:
            await self._drop_client()

    async def _drop_client(self) -> None:
        client = self._client
        self._client = None
        self._method_parent = None
        self._method_nodeid = None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                logger.debug("OPC UA disconnect ignored", exc_info=True)

    async def _connected(self) -> Any:
        if self._client is not None:
            return self._client
        last_error: Exception | None = None
        for username, password in self._credentials:
            try:
                return await self._connect_as(username, password)
            except Exception as exc:
                last_error = exc
                if not _is_access_denied(exc):
                    raise
                logger.warning(
                    "OPC UA login %r denied, trying next credential",
                    username or "anonymous",
                )
                await self._drop_client()
        raise SessionBridgeError(
            f"OPC UA login failed: {last_error}"
        ) from last_error

    async def _connect_as(self, username: str, password: str) -> Any:
        from asyncua import Client

        self.username = username
        self.password = password
        client = Client(url=self.endpoint, timeout=5)
        if username:
            client.set_user(username)
            client.set_password(password)
        await client.connect()
        self._client = client
        await self._bind_method(client)
        return client

    async def _bind_method(self, client: Any) -> None:
        from asyncua import ua

        parent_id = ua.NodeId.from_string(f"ns={self._ns};s=::UserBridge")
        method_id = ua.NodeId.from_string(f"ns={self._ns};s=::UserBridge:SdSmaExchange")
        parent = client.get_node(parent_id)
        try:
            await parent.read_browse_name()
            self._method_parent = parent
            self._method_nodeid = method_id
            return
        except Exception:
            logger.debug("UserBridge parent ns=%s failed, scanning", self._ns, exc_info=True)
        for ns in range(1, 12):
            try:
                node = client.get_node(ua.NodeId.from_string(f"ns={ns};s=::UserBridge:SdSmaExchange"))
                await node.read_browse_name()
                self._ns = ns
                self._method_parent = client.get_node(ua.NodeId.from_string(f"ns={ns};s=::UserBridge"))
                self._method_nodeid = node.nodeid
                return
            except Exception:
                continue
        raise SessionBridgeError("SdSmaExchange method not found")

    def _node(self, path: str) -> Any:
        from asyncua import ua

        return self._client.get_node(ua.NodeId.from_string(f"ns={self._ns};s={path}"))

    async def exchange(self, code: str) -> tuple[int, int, int, str, str, str]:
        from asyncua import ua

        async with self._lock:
            client = await self._connected()
            try:
                result = await self._method_parent.call_method(
                    self._method_nodeid,
                    ua.Variant(code, ua.VariantType.String),
                )
            except Exception:
                await self._drop_client()
                await self._connected()
                result = await self._method_parent.call_method(
                    self._method_nodeid,
                    ua.Variant(code, ua.VariantType.String),
                )
        values = result if isinstance(result, (list, tuple)) else [result]
        while len(values) < 6:
            values.append("" if len(values) >= 3 else 0)
        return (
            int(values[0] or 0),
            int(values[1] or -1),
            int(values[2] or 0),
            str(values[3] or ""),
            str(values[4] or ""),
            str(values[5] or ""),
        )

    async def read_slot(self, slot: int) -> PlcSession:
        async with self._lock:
            await self._connected()
            base = f"::AsGlobalPV:gSdSmaSession[{slot}]"
            seq, active, user_id, roles, code_query, code_used, pending = await asyncio.gather(
                self._node(f"{base}.seq").read_value(),
                self._node(f"{base}.active").read_value(),
                self._node(f"{base}.userId").read_value(),
                self._node(f"{base}.roles").read_value(),
                self._node(f"{base}.codeQuery").read_value(),
                self._node(f"{base}.codeUsed").read_value(),
                self._node(f"{base}.rolesPending").read_value(),
            )
        return PlcSession(
            seq=int(seq or 0),
            active=bool(active),
            user_id=str(user_id or ""),
            roles=str(roles or ""),
            code=_extract_code(str(code_query or "")),
            code_used=bool(code_used),
            roles_pending=bool(pending),
        )


def install_session_middleware(app: Any, bridge: SessionBridge | None = None, *, exempt: set[str] | None = None) -> SessionBridge:
    """HMI cookie or machine token. Document GET is served without a session so
    WebViewer first paint (empty query) is HTML, not JSON 401. /api/* still 401s.
    Do not 303 after exchange: iframe+Set-Cookie on a redirect hop often drops.
    """

    session = bridge or SessionBridge.from_env()
    allowed = exempt if exempt is not None else set(EXEMPT_PATHS)

    @app.middleware("http")
    async def sdsma_session_guard(request: Request, call_next: Any) -> Response:
        if request.url.path in allowed:
            return await call_next(request)
        if machine_token_ok(request.headers.get(MACHINE_TOKEN_HEADER)):
            request.state.sdsma_identity = None
            request.state.sdsma_machine = True
            return await call_next(request)
        request.state.sdsma_machine = False
        had_code = CODE_PARAM in request.query_params
        error: str | None = None
        try:
            identity = await session.resolve_request(request)
        except SessionBridgeError as exc:
            identity = None
            error = str(exc)
        except Exception as exc:
            logger.exception("session resolve failed")
            identity = None
            error = f"会话交换失败: {exc}"
        if identity is None:
            request.state.sdsma_identity = None
            if _needs_session(request.url.path, request.method, allowed) or (had_code and error):
                return JSONResponse(
                    status_code=401,
                    content={"detail": error or "需要 mappView 用户会话"},
                )
            return await call_next(request)
        request.state.sdsma_identity = identity
        response = await call_next(request)
        if had_code or COOKIE_NAME not in request.cookies:
            attach_cookie(response, session.cookie_for(identity))
        return response

    app.state.sdsma_bridge = session
    return session
