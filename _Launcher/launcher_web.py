from __future__ import annotations

import asyncio
import socket
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pymysql
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from launcher_imports import ConfigImportManager, FilesystemBrowserError
from launcher_security import LauncherSecurityStore


SESSION_COOKIE = "sd_sma_launcher_admin"
MANAGED_DB_SERVICES = {"collector_web", "query_web", "db_admin"}


def create_management_app(
    supervisor: Any,
    security: LauncherSecurityStore,
    importer: ConfigImportManager,
    static_dir: Path,
    network: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="SD SMA Launcher", version="1.0")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def local_origin_guard(request: Request, call_next: Any) -> Response:
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin", "").strip()
            origin_host = urlsplit(origin).netloc.lower() if origin else ""
            request_host = request.headers.get("host", "").strip().lower()
            if origin and (urlsplit(origin).scheme not in {"http", "https"} or origin_host != request_host):
                return Response("Forbidden origin", status_code=403)
        return await call_next(request)

    def require_admin(request: Request) -> str:
        if security.pin_mode == "undecided":
            raise HTTPException(428, "请先选择是否启用管理员 PIN")
        if security.pin_mode == "disabled":
            return "pin-disabled"
        token = request.cookies.get(SESSION_COOKIE)
        if not security.verify_session(token):
            raise HTTPException(401, "需要管理员 PIN 解锁")
        return str(token)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/launcher/status")
    def status() -> dict[str, Any]:
        result = supervisor.status()
        result["security"] = {
            "pin_configured": security.pin_configured,
            "pin_enabled": security.pin_enabled,
            "pin_mode": security.pin_mode,
        }
        if network is not None:
            result["network"] = network.status()
        return result

    @app.post("/api/launcher/auth/setup")
    async def setup_pin(request: Request, response: Response) -> dict[str, Any]:
        body = await request.json()
        try:
            token = security.setup_pin(str(body.get("pin", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", max_age=security.session_seconds)
        return {"ok": True, "unlocked": True, "pin_mode": security.pin_mode}

    @app.post("/api/launcher/auth/disable")
    def disable_pin(request: Request, response: Response) -> dict[str, Any]:
        if security.pin_enabled:
            require_admin(request)
        security.disable_pin()
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True, "unlocked": True, "pin_mode": security.pin_mode}

    @app.post("/api/launcher/auth/unlock")
    async def unlock(request: Request, response: Response) -> dict[str, Any]:
        body = await request.json()
        try:
            token = security.unlock(str(body.get("pin", "")))
        except PermissionError as exc:
            raise HTTPException(429 if "秒后" in str(exc) else 401, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", max_age=security.session_seconds)
        return {"ok": True, "unlocked": True}

    @app.post("/api/launcher/auth/lock")
    def lock(request: Request, response: Response) -> dict[str, Any]:
        security.lock(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @app.get("/api/launcher/auth/session")
    def session(request: Request) -> dict[str, Any]:
        return {
            "unlocked": security.pin_mode == "disabled" or security.verify_session(request.cookies.get(SESSION_COOKIE)),
            "pin_configured": security.pin_configured,
            "pin_enabled": security.pin_enabled,
            "pin_mode": security.pin_mode,
        }

    @app.get("/api/launcher/settings/network")
    def network_settings() -> dict[str, Any]:
        if network is None:
            raise HTTPException(503, "网络设置不可用")
        return network.status()

    @app.put("/api/launcher/settings/network")
    async def update_network_settings(request: Request) -> dict[str, Any]:
        require_admin(request)
        if network is None:
            raise HTTPException(503, "网络设置不可用")
        body = await request.json()
        try:
            return network.apply(str(body.get("mode", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/launcher/services/{name}/{action}")
    def service_action(name: str, action: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            return {"ok": True, "service": supervisor.command(name, action)}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/launcher/credentials")
    def credentials(request: Request) -> dict[str, Any]:
        require_admin(request)
        return security.public_credentials()

    @app.post("/api/launcher/credentials")
    async def save_credential(request: Request) -> dict[str, Any]:
        require_admin(request)
        body = await request.json()
        credential_id = str(body.get("id", ""))
        affected_before = security.services_for_credential(credential_id) if credential_id else []
        try:
            saved = security.upsert_credential(body)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        affected = sorted(set(affected_before + security.services_for_credential(str(saved["id"]))))
        supervisor.restart_if_running(affected)
        return {"ok": True, "credential": saved, "restarted_services": affected}

    @app.delete("/api/launcher/credentials/{credential_id}")
    def delete_credential(credential_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        affected = security.services_for_credential(credential_id)
        security.delete_credential(credential_id)
        supervisor.restart_if_running(affected)
        return {"ok": True, "restarted_services": affected}

    @app.put("/api/launcher/credentials/assignments/{service}")
    async def assign_credential(service: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        if service not in MANAGED_DB_SERVICES:
            raise HTTPException(400, "该服务不使用 Launcher 数据库凭据")
        body = await request.json()
        credential_id = str(body.get("credential_id") or "") or None
        try:
            security.assign(service, credential_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        supervisor.restart_if_running([service])
        return {"ok": True, "service": service, "credential_id": credential_id}

    @app.post("/api/launcher/credentials/{credential_id}/test")
    def test_credential(credential_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        credential = security.credential_by_id(credential_id)
        if credential is None:
            raise HTTPException(404, "凭据档案不存在")
        try:
            connection = pymysql.connect(
                host=str(credential.get("host", "127.0.0.1")),
                port=int(credential.get("port", 3306)),
                user=str(credential.get("username", "root")),
                password=str(credential.get("password", "")),
                database=str(credential.get("database", "")) or None,
                connect_timeout=5,
                read_timeout=5,
                write_timeout=5,
            )
            connection.close()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"连接失败：{exc}") from exc
        return {"ok": True, "message": "数据库连接成功"}

    @app.get("/api/launcher/filesystem/roots")
    def filesystem_roots(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {"roots": importer.roots()}

    @app.get("/api/launcher/filesystem/entries")
    def filesystem_entries(path: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            return importer.entries(path)
        except FilesystemBrowserError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/launcher/import/settings")
    def import_settings(request: Request) -> dict[str, Any]:
        require_admin(request)
        return importer.settings()

    @app.put("/api/launcher/import/settings")
    async def update_import_settings(request: Request) -> dict[str, Any]:
        require_admin(request)
        body = await request.json()
        try:
            return importer.update_settings(list(body.get("allowed_import_roots") or []))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/launcher/import/inspect")
    async def inspect_import(request: Request) -> dict[str, Any]:
        require_admin(request)
        body = await request.json()
        try:
            return importer.inspect(str(body.get("service", "")), list(body.get("paths") or []))
        except (ValueError, FilesystemBrowserError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/launcher/import/apply")
    async def apply_import(request: Request) -> dict[str, Any]:
        require_admin(request)
        body = await request.json()
        try:
            result = importer.apply(str(body.get("preview_token", "")))
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        service_names = [str(item) for item in result.get("services") or [result["service"]]]
        before = {item["name"]: item for item in supervisor.status()["services"]}
        running_services = [
            name
            for name in service_names
            if before.get(name, {}).get("desired_running") and before.get(name, {}).get("pid")
        ]
        if running_services:
            supervisor.restart_if_running(running_services)
            deadline = asyncio.get_running_loop().time() + 35.0
            while asyncio.get_running_loop().time() < deadline:
                current = {item["name"]: item for item in supervisor.status()["services"]}
                if all(
                    current.get(name, {}).get("state") == "running"
                    and current.get(name, {}).get("health_ready")
                    for name in running_services
                ):
                    break
                if any(current.get(name, {}).get("state") == "failed" for name in running_services):
                    deadline = 0.0
                    break
                await asyncio.sleep(0.25)
            else:
                current = {}
            if not all(
                current.get(name, {}).get("state") == "running"
                and current.get(name, {}).get("health_ready")
                for name in running_services
            ):
                importer.rollback(result)
                for name in running_services:
                    supervisor.command(name, "start")
                raise HTTPException(500, "新配置健康检查失败，已恢复原配置并重新启动服务")
        result["restarted_services"] = running_services
        result["restarted_if_running"] = bool(running_services)
        return result

    return app


class ManagementServer:
    def __init__(self, app: FastAPI, host: str = "127.0.0.1", port: int = 8090) -> None:
        self.host = host
        self.port = port
        self.app = app
        self.server = self._new_server(host)
        self.thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def _new_server(self, host: str) -> uvicorn.Server:
        return uvicorn.Server(uvicorn.Config(self.app, host=host, port=self.port, log_level="info", access_log=False))

    def start(self) -> None:
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            probe_host = "127.0.0.1" if self.host == "0.0.0.0" else self.host
            if sock.connect_ex((probe_host, self.port)) == 0:
                raise RuntimeError(f"管理端口已被占用：{self.host}:{self.port}")
        self.server = self._new_server(self.host)
        self.thread = threading.Thread(target=self.server.run, name="launcher-web", daemon=True)
        self.thread.start()
        deadline = threading.Event()
        for _ in range(100):
            if self.server.started:
                return
            if self.thread and not self.thread.is_alive():
                break
            deadline.wait(0.05)
        raise RuntimeError("Launcher 管理页面启动失败")

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self.server.should_exit = True
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=5.0)
        self.thread = None

    def rebind_async(self, host: str, callback: Any, delay_seconds: float = 0.5) -> None:
        def worker() -> None:
            error: Exception | None = None
            old_host = self.host
            with self._lock:
                try:
                    self._stop_locked()
                    self.host = host
                    self._start_locked()
                except Exception as exc:  # noqa: BLE001
                    error = exc
                    try:
                        self._stop_locked()
                        self.host = old_host
                        self._start_locked()
                    except Exception as rollback_error:  # noqa: BLE001
                        error = RuntimeError(f"{exc}; management rollback failed: {rollback_error}")
            callback(error)

        timer = threading.Timer(delay_seconds, worker)
        timer.daemon = True
        timer.start()
