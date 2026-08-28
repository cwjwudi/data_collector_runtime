from __future__ import annotations

import asyncio
import ipaddress
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .collector_host import get_collector_host
from .config_manager import CollectorConfigManager
from .models import (
    CollectorStartRequest,
    CollectorStartupSettingsRequest,
    ConfigExportRequest,
    ConfigValidateRequest,
    ConfigWriteRequest,
    OpcUaConnectRequest,
)
from .opcua_browser import OpcUaBrowserService

BASE_DIR = Path(__file__).resolve().parent
COLLECTOR_ROOT = BASE_DIR.parent
AUTH_TOKEN_ENV = "SD_SMA_WEB_TOKEN"
AUTH_TOKEN_HEADER = "X-SD-SMA-Token"
AUTH_EXEMPT_PATHS = {"/api/health"}


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    h = host.strip().lower()
    if h in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return True
    try:
        return ipaddress.ip_address(h.strip("[]")).is_loopback
    except ValueError:
        return False


def _remote_token_ok(provided: str | None) -> bool:
    expected = (os.getenv(AUTH_TOKEN_ENV) or "").strip()
    if not expected or not provided or not provided.strip():
        return False
    return secrets.compare_digest(provided.strip(), expected)


def _resolve_config_dir(env_name: str, default: Path, *, base: Path) -> Path:
    raw = os.getenv(env_name)
    if not raw:
        return default.resolve()
    value = raw.replace("${COLLECTOR_ROOT}", str(COLLECTOR_ROOT))
    value = value.replace("${WEB_CONFIG_ROOT}", str(BASE_DIR))
    path = Path(os.path.expandvars(value))
    if not path.is_absolute():
        path = base / path
    return path.resolve()


collector_config_dir = _resolve_config_dir(
    "SD_SMA_COLLECTOR_CONFIG_DIR",
    COLLECTOR_ROOT / "config",
    base=COLLECTOR_ROOT,
)
export_dir = collector_config_dir / "exports"

cfg = CollectorConfigManager(
    collector_config_dir=collector_config_dir,
)
opcua_browser = OpcUaBrowserService()


async def _auto_start_collector() -> None:
    host = get_collector_host()
    try:
        settings = cfg.load_runtime_settings()
        if not settings.get("auto_start_enabled"):
            return
        filename = str(settings.get("auto_start_config") or "").strip()
        if not filename:
            host.record_error("自动启动已启用，但未设置配置文件")
            return
        delay = int(settings.get("auto_start_delay_seconds", 3) or 0)
        if delay > 0:
            await asyncio.sleep(delay)
        await host.start(filename, collector_config_dir)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        host.record_error(f"自动启动采集失败: {exc}")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    startup_task = asyncio.create_task(_auto_start_collector(), name="sd_sma_collector_auto_start")
    try:
        yield
    finally:
        if not startup_task.done():
            startup_task.cancel()
            try:
                await startup_task
            except asyncio.CancelledError:
                pass
        await get_collector_host().shutdown()


app = FastAPI(title="SD SMA Collector Config Web", version="1.5.1", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def enforce_remote_token(request: Request, call_next):
    if request.url.path in AUTH_EXEMPT_PATHS:
        return await call_next(request)
    client_host = request.client.host if request.client else None
    if _is_loopback_host(client_host):
        return await call_next(request)
    if _remote_token_ok(request.headers.get(AUTH_TOKEN_HEADER)):
        return await call_next(request)
    return JSONResponse(
        status_code=403,
        content={"detail": f"非本机访问需在请求头 {AUTH_TOKEN_HEADER} 提供有效令牌（服务端环境变量 {AUTH_TOKEN_ENV}）"},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "home.html")


@app.get("/dashboard")
def dashboard_page() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "dashboard.html")


@app.get("/config")
def config_page() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "config.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/api/config/template")
def get_template() -> dict[str, Any]:
    try:
        return cfg.get_template()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/config/files")
def list_config_files() -> dict[str, Any]:
    try:
        return {"files": cfg.list_config_files()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/config/file")
def get_config_file(filename: str) -> dict[str, Any]:
    try:
        return cfg.load_config_file(filename)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/config/file")
def delete_config_file(filename: str) -> dict[str, Any]:
    try:
        path = cfg.delete_config_file(filename)
        return {"status": "deleted", "path": str(path)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/file/delete")
def delete_config_file_post(body: dict[str, Any]) -> dict[str, Any]:
    try:
        filename = str(body.get("filename", "")).strip()
        if not filename:
            raise ValueError("filename 不能为空")
        path = cfg.delete_config_file(filename)
        return {"status": "deleted", "path": str(path)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/template")
def save_template(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        cfg.save_template(payload)
        return {"status": "saved"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/validate")
def validate_template(req: ConfigValidateRequest) -> dict[str, Any]:
    try:
        return cfg.validate_template(req.payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/export")
def export_config(req: ConfigExportRequest) -> dict[str, Any]:
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
        safe_name = cfg.sanitize_filename(req.filename)
        path = cfg.export_to_path(req.payload, export_dir / safe_name)
        return {"status": "exported", "path": str(path)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/write")
def write_config(req: ConfigWriteRequest) -> dict[str, Any]:
    try:
        path = cfg.write_collector_config(payload=req.payload, filename=req.filename)
        return {"status": "written", "path": str(path)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/points/from-node")
def append_point_from_node(body: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = body.get("payload", {})
        node_id = str(body.get("node_id", "")).strip()
        display_name = str(body.get("display_name", "")).strip()
        description = str(body.get("description", "")).strip()
        datatype = str(body.get("datatype", "")).strip()
        if not node_id:
            raise ValueError("node_id 不能为空")

        point_item = cfg.build_point_from_node(
            node_id=node_id,
            display_name=display_name or node_id,
            description=description,
            datatype=datatype or None,
        )
        new_payload = cfg.append_point(payload, point_item)
        return {"status": "ok", "point": point_item, "payload": new_payload}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/opcua/status")
def opcua_status() -> dict[str, Any]:
    return opcua_browser.status()


@app.post("/api/opcua/connect")
async def opcua_connect(req: OpcUaConnectRequest) -> dict[str, Any]:
    try:
        return await opcua_browser.connect(host=req.host, port=req.port)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"连接失败: {exc}") from exc


@app.post("/api/opcua/disconnect")
async def opcua_disconnect() -> dict[str, Any]:
    await opcua_browser.disconnect()
    return {"status": "disconnected"}


@app.get("/api/opcua/browse")
async def opcua_browse(node_id: str | None = None) -> dict[str, Any]:
    try:
        return {"items": await opcua_browser.browse(node_id=node_id)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/opcua/node")
async def opcua_node_meta(node_id: str) -> dict[str, Any]:
    try:
        return await opcua_browser.node_meta(node_id=node_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/collector/status")
def collector_status() -> dict[str, Any]:
    return get_collector_host().status()


@app.get("/api/collector/logs")
def collector_logs(cursor: int = 0, limit: int = 200) -> dict[str, Any]:
    host = get_collector_host()
    return host.get_log_handler().get_lines_since(cursor=cursor, limit=limit)


@app.get("/api/collector/startup-settings")
def get_collector_startup_settings() -> dict[str, Any]:
    try:
        return cfg.load_runtime_settings()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/collector/startup-settings")
def save_collector_startup_settings(req: CollectorStartupSettingsRequest) -> dict[str, Any]:
    try:
        settings = cfg.save_runtime_settings(req.dict())
        return {"status": "saved", "settings": settings}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/collector/start")
async def collector_start(req: CollectorStartRequest) -> dict[str, Any]:
    try:
        return await get_collector_host().start(req.filename, collector_config_dir)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/collector/stop")
async def collector_stop() -> dict[str, Any]:
    return await get_collector_host().stop()
