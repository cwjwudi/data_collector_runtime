from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from .config_manager import ConfigManager, UnifiedConfigStore, normalize_opcua_endpoint_url
from .database import QueryDatabase
from .models import (
    GroupBaselineUpdateRequest,
    HistoryQueryRequest,
    HistoryQueryResponse,
    QueryBatchSourceUpdateRequest,
    QueryGroupConfigUpdateRequest,
    PluginCursorRequest,
    PluginQueryRequest,
    OpcUaSettingsRequest,
    QueryTableConfigUpdateRequest,
    QueryFilter,
    ViewHistoryQueryRequest,
)
from . import opcua_client
from .opcua_writeback import OpcUaWritebackConfig, write_after_query_async, write_cursor_only_async
from .table_list_writeback import (
    TableListWritebackConfig,
    resolve_table_names_for_batch_no,
    resolve_table_names_for_row,
    write_table_list_async,
)
from .table_partition import table_group_info, list_tables_for_group
from .plugin_opcua_monitor import PluginOpcuaMonitor, PluginRuntimeSnapshot
from .logging_config import setup_logging

setup_logging()

BASE_DIR = Path(__file__).resolve().parent.parent
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


def _resolve_config_dir(env_name: str, default: Path) -> Path:
    raw = os.getenv(env_name)
    if not raw:
        return default.resolve()
    value = raw.replace("${QUERY_WEB_ROOT}", str(BASE_DIR))
    path = Path(os.path.expandvars(value))
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


CONFIG_DIR = _resolve_config_dir("SD_SMA_QUERY_WEB_CONFIG_DIR", BASE_DIR / "config")
APP_SETTINGS_PATH = CONFIG_DIR / "app_settings.json"
QUERY_VIEW_CONFIG_PATH = CONFIG_DIR / "query_view_config.json"
PLUGIN_CONFIG_PATH = CONFIG_DIR / "plugins_config.json"


def _normalize_app_settings(data: dict[str, Any] | None) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    raw_database = source.get("database")
    database = raw_database if isinstance(raw_database, dict) else {}
    raw_query_limits = source.get("query_limits")
    query_limits = raw_query_limits if isinstance(raw_query_limits, dict) else {}
    db_type = str(database.get("type", "mysql") or "mysql").lower()
    if db_type not in {"mysql", "sqlite"}:
        raise ValueError("database.type 仅支持 mysql 或 sqlite")

    return {
        "database": {
            "type": db_type,
            "name": str(database.get("name", "") or ""),
            "host": str(database.get("host", "127.0.0.1") or "127.0.0.1"),
            "port": int(database.get("port", 3306) or 3306),
            "username": str(database.get("username", "") or ""),
            "password": str(database.get("password", "") or ""),
            "password_configured": bool(database.get("password_configured", False)),
            "clear_password": bool(database.get("clear_password", False)),
        },
        "query_limits": {
            "requests_per_minute": max(int(query_limits.get("requests_per_minute", 120) or 120), 0),
            "default_window_hours": max(int(query_limits.get("default_window_hours", 24) or 24), 1),
            "max_window_hours": max(int(query_limits.get("max_window_hours", 168) or 168), 1),
        },
    }


def _load_app_settings() -> dict[str, Any]:
    return _normalize_app_settings(config_store.get_app_settings())


def _save_app_settings(data: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_app_settings(data)
    config_store.save_app_settings(normalized)
    return _normalize_app_settings(config_store.get_public_app_settings())


def _apply_runtime_settings(updated_settings: dict[str, Any]) -> None:
    global settings, db, QUERY_LIMITS, RATE_LIMIT_PER_MINUTE, DEFAULT_WINDOW_HOURS, MAX_WINDOW_HOURS

    settings = updated_settings
    db = QueryDatabase(settings.get("database", {}))
    QUERY_LIMITS = settings.get("query_limits", {})
    RATE_LIMIT_PER_MINUTE = int(QUERY_LIMITS.get("requests_per_minute", 120))
    DEFAULT_WINDOW_HOURS = int(QUERY_LIMITS.get("default_window_hours", 24))
    MAX_WINDOW_HOURS = int(QUERY_LIMITS.get("max_window_hours", 168))
    _clear_plugin_snapshots()


config_store = UnifiedConfigStore(
    CONFIG_DIR,
    legacy_app_settings_path=APP_SETTINGS_PATH,
    legacy_query_view_config_path=QUERY_VIEW_CONFIG_PATH,
    legacy_plugin_config_path=PLUGIN_CONFIG_PATH,
)
settings = _load_app_settings()
db = QueryDatabase(settings.get("database", {}))
cfg = ConfigManager(config_store)
QUERY_LIMITS = settings.get("query_limits", {})
RATE_LIMIT_PER_MINUTE = int(QUERY_LIMITS.get("requests_per_minute", 120))
DEFAULT_WINDOW_HOURS = int(QUERY_LIMITS.get("default_window_hours", 24))
MAX_WINDOW_HOURS = int(QUERY_LIMITS.get("max_window_hours", 168))
_REQUEST_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
PLUGIN_KEY_PATTERN = re.compile(r"^([A-Za-z0-9_]+)_([1-5])$")
DEFAULT_LOOKUP_START_TIME_COLUMN = "dtBatchStartTime"
MAX_PLUGIN_SNAPSHOT_ROWS = 100_000
_plugin_query_cache: dict[str, dict[str, Any]] = {}
_plugin_data_snapshots: dict[str, "PluginDataSnapshot"] = {}
_plugin_snapshot_locks: dict[str, asyncio.Lock] = {}
_plugin_snapshot_tasks: dict[str, asyncio.Task[tuple[HistoryQueryResponse, PluginRuntimeSnapshot]]] = {}
_plugin_opcua_monitor: PluginOpcuaMonitor | None = None
_plugin_opcua_monitor_task: asyncio.Task | None = None
_monitor_config_revision: tuple[Any, ...] | None = None
_monitor_config_snapshot: dict[str, Any] | None = None

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginDataSnapshot:
    plugin_key: str
    table: str
    page_size: int
    columns: list[str]
    rows: list[dict[str, Any]]
    display_columns: list[dict[str, Any]]
    warnings: list[str]
    created_at: str


def _clear_plugin_snapshots(plugin_key: str | None = None) -> None:
    if plugin_key is None:
        tasks = list(_plugin_snapshot_tasks.values())
        _plugin_snapshot_tasks.clear()
        for task in tasks:
            if not task.done():
                task.get_loop().call_soon_threadsafe(task.cancel)
        _plugin_data_snapshots.clear()
        _plugin_query_cache.clear()
        _plugin_snapshot_locks.clear()
    else:
        task = _plugin_snapshot_tasks.pop(plugin_key, None)
        if task is not None and not task.done():
            task.get_loop().call_soon_threadsafe(task.cancel)
        _plugin_data_snapshots.pop(plugin_key, None)
        _plugin_query_cache.pop(plugin_key, None)
        _plugin_snapshot_locks.pop(plugin_key, None)
    if _plugin_opcua_monitor is not None:
        _plugin_opcua_monitor.clear_runtime(plugin_key)


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    global _plugin_opcua_monitor, _plugin_opcua_monitor_task
    if os.getenv("SD_SMA_DISABLE_OPCUA_MONITOR", "").strip().lower() not in {"1", "true", "yes"}:
        _plugin_opcua_monitor = PluginOpcuaMonitor(
            get_runtime_config=_get_monitor_runtime_config,
            on_snapshot_query=_monitor_query_plugin_snapshot,
            on_page_change=_monitor_change_plugin_page,
            on_trigger=_monitor_trigger_writeback,
        )
        _plugin_opcua_monitor_task = asyncio.create_task(_plugin_opcua_monitor.run())
    yield
    if _plugin_opcua_monitor is not None:
        _plugin_opcua_monitor.stop()
    if _plugin_opcua_monitor_task is not None:
        _plugin_opcua_monitor_task.cancel()
        try:
            await _plugin_opcua_monitor_task
        except asyncio.CancelledError:
            pass
    _plugin_opcua_monitor = None
    _plugin_opcua_monitor_task = None
    await opcua_client.close_pool()


app = FastAPI(title="SD SMA Query Web", version="0.1.0", lifespan=_app_lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")


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


def _raise_db_error(exc: Exception) -> None:
    detail = (
        "数据库连接失败，请检查 "
        "当前统一 config 中 app_settings.database 的 host/port/name/username/password 是否正确。"
    )
    raise HTTPException(status_code=503, detail=f"{detail} 原因: {exc}") from exc


def _client_id(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _enforce_rate_limit(request: Request) -> None:
    if RATE_LIMIT_PER_MINUTE <= 0:
        return
    client = _client_id(request)
    now = time.time()
    bucket = _REQUEST_BUCKETS[client]
    threshold = now - 60.0
    while bucket and bucket[0] < threshold:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后重试")
    bucket.append(now)


def _now_like(start_time: datetime | None, end_time: datetime | None) -> datetime:
    if end_time and end_time.tzinfo:
        return datetime.now(end_time.tzinfo)
    if start_time and start_time.tzinfo:
        return datetime.now(start_time.tzinfo)
    return datetime.now()


def _apply_time_guardrails(
    start_time: datetime | None,
    end_time: datetime | None,
) -> tuple[datetime | None, datetime | None, list[str]]:
    warnings: list[str] = []
    default_window = timedelta(hours=max(DEFAULT_WINDOW_HOURS, 1))
    max_window = timedelta(hours=max(MAX_WINDOW_HOURS, 1))

    if not start_time and not end_time:
        end_time = _now_like(start_time, end_time)
        start_time = end_time - default_window
        warnings.append(f"未指定时间范围，默认查询最近 {DEFAULT_WINDOW_HOURS} 小时")
    elif not start_time and end_time:
        start_time = end_time - default_window
        warnings.append(f"缺少开始时间，自动使用结束时间前 {DEFAULT_WINDOW_HOURS} 小时")
    elif start_time and not end_time:
        end_time = _now_like(start_time, end_time)
        warnings.append("缺少结束时间，自动使用当前时间")

    if start_time and end_time:
        if start_time > end_time:
            start_time, end_time = end_time, start_time
            warnings.append("开始/结束时间顺序已自动纠正")
        if (end_time - start_time) > max_window:
            start_time = end_time - max_window
            warnings.append(f"查询窗口超限，已截断为最近 {MAX_WINDOW_HOURS} 小时")

    return start_time, end_time, warnings


def _resolve_batch_field(view: dict[str, Any], available_columns: list[str]) -> str | None:
    configured = str(view.get("batch_field", "") or "").strip()
    if configured and configured in available_columns:
        return configured
    return None


def _load_plugin_config() -> dict[str, Any]:
    data = config_store.get_plugins_config()
    if not isinstance(data, dict):
        return {"modules": {}}
    if "modules" not in data or not isinstance(data["modules"], dict):
        data["modules"] = {}
    return data


def _save_plugin_config(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("plugins 配置必须为 JSON 对象")
    modules = data.get("modules")
    if not isinstance(modules, dict):
        raise ValueError("plugins 配置必须包含 modules 对象")

    for module_name, module_cfg in modules.items():
        if not isinstance(module_cfg, dict):
            raise ValueError(f"模块配置无效: {module_name}")
        pages = module_cfg.get("pages")
        if not isinstance(pages, dict):
            raise ValueError(f"模块 {module_name} 缺少 pages 配置")
        for idx in ["1", "2", "3", "4", "5"]:
            if idx not in pages:
                raise ValueError(f"模块 {module_name} 缺少第 {idx} 页配置")
            if not isinstance(pages[idx], dict):
                raise ValueError(f"模块 {module_name} 第 {idx} 页配置无效")

    config_store.save_plugins_config(data)


def _resolve_plugin_binding(
    plugin_key: str,
    plugin_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    match = PLUGIN_KEY_PATTERN.match(plugin_key)
    if not match:
        raise ValueError("插件路径必须是 <module>_<1-5> 格式")
    module_name, page_index = match.group(1), match.group(2)

    plugin_cfg = plugin_cfg if isinstance(plugin_cfg, dict) else _load_plugin_config()
    modules = plugin_cfg.get("modules", {})
    module_cfg = modules.get(module_name)
    if not isinstance(module_cfg, dict):
        raise ValueError(f"未找到模块配置: {module_name}")

    pages = module_cfg.get("pages", {})
    if not isinstance(pages, dict):
        raise ValueError(f"模块 pages 配置无效: {module_name}")
    page_cfg = pages.get(page_index)
    if not isinstance(page_cfg, dict):
        raise ValueError(f"模块 {module_name} 的第 {page_index} 页未配置")

    enabled = bool(page_cfg.get("enabled", True))
    if not enabled:
        raise ValueError(f"模块 {module_name} 第 {page_index} 页已禁用")

    bind_group = page_cfg.get("bind_group", module_cfg.get("bind_group"))
    # 插件绑定改为只绑定 group，table 由插件页右上角手动选择。
    bind_table = None
    view_name = str(page_cfg.get("view_name", module_cfg.get("view_name", "table")))
    page_size = int(page_cfg.get("page_size", module_cfg.get("page_size", 10)))

    if not bind_group and not bind_table:
        raise ValueError("插件必须至少配置 bind_group 或 bind_table")

    opcua_writeback_raw = page_cfg.get("opcua_writeback")
    if opcua_writeback_raw is None:
        opcua_writeback_raw = module_cfg.get("opcua_writeback")

    table_list_writeback_raw = page_cfg.get("table_list_writeback")
    if table_list_writeback_raw is None:
        table_list_writeback_raw = module_cfg.get("table_list_writeback")

    return {
        "plugin_key": plugin_key,
        "module": module_name,
        "page_index": int(page_index),
        "title": str(page_cfg.get("title", module_cfg.get("title", plugin_key))),
        "view_name": view_name,
        "bind_group": bind_group,
        "bind_table": bind_table,
        "page_size": max(1, page_size),
        "target": str(page_cfg.get("target", module_cfg.get("target", ""))),
        "opcua_writeback": opcua_writeback_raw,
        "table_list_writeback": table_list_writeback_raw,
    }


def _get_opcua_connection() -> dict[str, Any]:
    settings = config_store.get_opcua_settings()
    endpoint = normalize_opcua_endpoint_url(str(settings.get("endpoint_url", "") or ""))
    return {
        "endpoint_url": endpoint,
        "username": str(settings.get("username", "") or ""),
        "password": str(settings.get("password", "") or ""),
        "heartbeat_node": str(settings.get("heartbeat_node", "") or "").strip(),
        "poll_interval_ms": int(settings.get("poll_interval_ms", 500) or 500),
    }


def _get_table_list_writeback_config(binding: dict[str, Any]) -> TableListWritebackConfig | None:
    return TableListWritebackConfig.from_binding(
        binding.get("table_list_writeback"),
        bind_group=binding.get("bind_group"),
    )


def _resolve_batch_master_table(binding: dict[str, Any], config: TableListWritebackConfig) -> str:
    explicit = str(config.batch_master_table or "").strip()
    bind_group = str(binding.get("bind_group") or "").strip()
    if explicit and (not bind_group or explicit != bind_group):
        return explicit
    if not bind_group:
        return explicit

    try:
        group_tables = list_tables_for_group(db.list_tables(), bind_group)
        for table_name in group_tables:
            info = table_group_info(table_name)
            if info is not None and info.kind == "fixed":
                return table_name
        saved_baseline = cfg.get_group_baseline(bind_group)
        report = db.get_group_schema_report(bind_group, saved_baseline)
        baseline = report.get("baseline_table")
        if baseline:
            return str(baseline)
    except Exception:
        logger.debug("resolve batch master table failed group=%s", bind_group, exc_info=True)
    return explicit or bind_group


def _resolve_lookup_start_time_column(
    binding: dict[str, Any],
    config: TableListWritebackConfig,
    raw_config: dict[str, Any] | None,
) -> str:
    lookup_column = ""
    is_advanced = config.is_advanced_mode
    if isinstance(raw_config, dict):
        lookup_column = str(raw_config.get("lookup_start_time_column") or "").strip()
        # 基础模式才使用 UI「开批时间列」；OPC UA 模式忽略 start_time_column
        if not lookup_column and not is_advanced:
            lookup_column = str(raw_config.get("start_time_column") or "").strip()
    if not lookup_column and not is_advanced:
        lookup_column = str(config.start_time_column or "").strip()

    if not lookup_column:
        bind_group = str(binding.get("bind_group") or "").strip()
        view_name = str(binding.get("view_name") or "table").strip()
        if bind_group:
            try:
                master_table = _resolve_batch_master_table(binding, config)
                view = cfg.resolve_query_view(view_name, table=master_table, group=bind_group)
                time_field = str(view.get("time_field", "") or "").strip()
                if time_field:
                    lookup_column = time_field
            except Exception:
                logger.debug("resolve lookup start time from view failed", exc_info=True)

    if not lookup_column:
        writeback = binding.get("opcua_writeback")
        if isinstance(writeback, dict):
            for name in (writeback.get("columns") or {}):
                lower = str(name).lower()
                if "start" in lower and ("time" in lower or "btach" in lower or "batch" in lower):
                    lookup_column = str(name)
                    break

    if not lookup_column:
        lookup_column = DEFAULT_LOOKUP_START_TIME_COLUMN
    return lookup_column


def _enrich_table_list_config(binding: dict[str, Any], config: TableListWritebackConfig) -> TableListWritebackConfig:
    master_table = _resolve_batch_master_table(binding, config)
    if master_table != config.batch_master_table:
        return replace(config, batch_master_table=master_table)
    return config


def _lookup_start_time_factory(
    binding: dict[str, Any],
    config: TableListWritebackConfig,
    raw_config: dict[str, Any] | None,
):
    lookup_column = _resolve_lookup_start_time_column(binding, config, raw_config)

    def _lookup(master_table: str, batch_column: str, batch_value: Any):
        return db.lookup_batch_start_time(
            master_table,
            batch_column,
            batch_value,
            lookup_column,
        )

    return _lookup


def _cache_plugin_query(plugin_key: str, rows: list[dict[str, Any]], target_table: str) -> None:
    _plugin_query_cache[plugin_key] = {
        "rows": list(rows),
        "target_table": target_table,
    }


def _get_cached_plugin_row(plugin_key: str, cursor: int) -> dict[str, Any] | None:
    cached = _plugin_query_cache.get(plugin_key)
    if not cached:
        return None
    rows = cached.get("rows") or []
    if cursor < 0 or cursor >= len(rows):
        return None
    row = rows[cursor]
    return row if isinstance(row, dict) else None


async def _run_table_list_writeback(
    binding: dict[str, Any],
    *,
    cursor: int,
    row: dict[str, Any] | None = None,
) -> None:
    config = _get_table_list_writeback_config(binding)
    if config is None or config.is_advanced_mode:
        return

    config = _enrich_table_list_config(binding, config)

    opcua = _get_opcua_connection()
    endpoint = opcua.get("endpoint_url", "")
    if not endpoint:
        return

    raw_config = binding.get("table_list_writeback")
    lookup_start_time = _lookup_start_time_factory(
        binding,
        config,
        raw_config if isinstance(raw_config, dict) else None,
    )

    table_names = None
    if cursor >= 0 and row is not None:
        table_names = resolve_table_names_for_row(
            row,
            config,
            list_tables=db.list_tables,
            lookup_start_time=lookup_start_time,
        )

    try:
        await write_table_list_async(
            endpoint,
            opcua.get("username", ""),
            opcua.get("password", ""),
            config,
            table_names,
            cursor=cursor,
        )
    except Exception as exc:
        logger.warning("OPC UA table list writeback hook failed: %s", exc)


async def _run_advanced_trigger_writeback(binding: dict[str, Any], batch_no: str) -> bool:
    config = _get_table_list_writeback_config(binding)
    if config is None or not config.is_advanced_mode:
        return False

    config = _enrich_table_list_config(binding, config)
    batch_value = str(batch_no or "").strip()
    if not batch_value:
        logger.warning("OPC UA advanced trigger skipped: empty batch number plugin=%s", binding.get("plugin_key"))
        return False

    opcua = _get_opcua_connection()
    endpoint = opcua.get("endpoint_url", "")
    if not endpoint:
        return False

    raw_config = binding.get("table_list_writeback")
    lookup_start_time = _lookup_start_time_factory(
        binding,
        config,
        raw_config if isinstance(raw_config, dict) else None,
    )
    table_names = resolve_table_names_for_batch_no(
        batch_value,
        config,
        list_tables=db.list_tables,
        lookup_start_time=lookup_start_time,
    )

    try:
        await write_table_list_async(
            endpoint,
            opcua.get("username", ""),
            opcua.get("password", ""),
            config,
            table_names,
            cursor=0 if batch_value else -1,
        )
        filled = sum(1 for item in table_names if str(item or "").strip())
        logger.info(
            "OPC UA advanced trigger writeback ok plugin=%s batch=%r tables=%d",
            binding.get("plugin_key"),
            batch_value,
            filled,
        )
        return True
    except Exception as exc:
        logger.warning("OPC UA advanced trigger writeback failed: %s", exc)
        return False


def _iter_advanced_plugin_bindings(
    plugin_cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    plugin_cfg = plugin_cfg if isinstance(plugin_cfg, dict) else _load_plugin_config()
    modules = plugin_cfg.get("modules", {})
    if not isinstance(modules, dict):
        return []

    bindings: list[dict[str, Any]] = []
    for module_name, module_cfg in modules.items():
        if not isinstance(module_cfg, dict):
            continue
        pages = module_cfg.get("pages", {})
        if not isinstance(pages, dict):
            continue
        for page_index in ["1", "2", "3", "4", "5"]:
            page_cfg = pages.get(page_index)
            if not isinstance(page_cfg, dict) or not bool(page_cfg.get("enabled", True)):
                continue
            plugin_key = f"{module_name}_{page_index}"
            try:
                binding = _resolve_plugin_binding(plugin_key, plugin_cfg)
            except ValueError:
                continue
            config = _get_table_list_writeback_config(binding)
            if config is None or not config.is_advanced_mode or config.advanced is None:
                continue
            enriched = dict(binding)
            enriched["_table_list_config"] = config
            enriched["_table_list_advanced"] = {
                "query_node": config.advanced.query_node,
                "prev_page_node": config.advanced.prev_page_node,
                "next_page_node": config.advanced.next_page_node,
                "batch_no_node": config.advanced.batch_no_node,
                "trigger_node": config.advanced.trigger_node,
            }
            bindings.append(enriched)
    return bindings


def _get_monitor_runtime_config() -> dict[str, Any]:
    """Compile monitor bindings once per active-config revision."""
    global _monitor_config_revision, _monitor_config_snapshot

    active, revision = config_store.get_active_config_with_revision()
    if _monitor_config_snapshot is not None and revision == _monitor_config_revision:
        return _monitor_config_snapshot

    plugin_cfg = active.get("plugins", {"modules": {}})
    if not isinstance(plugin_cfg, dict):
        plugin_cfg = {"modules": {}}
    snapshot = {
        "revision": repr(revision),
        "bindings": _iter_advanced_plugin_bindings(plugin_cfg),
        "opcua": _get_opcua_connection(),
    }
    _monitor_config_revision = revision
    _monitor_config_snapshot = snapshot
    logger.info(
        "OPC UA monitor config rebuilt revision=%s bindings=%d",
        snapshot["revision"],
        len(snapshot["bindings"]),
    )
    return snapshot


def _plugin_runtime_snapshot(
    plugin_key: str,
    *,
    binding: dict[str, Any],
    view: dict[str, Any],
    target_table: str,
    start_time: datetime | None,
    end_time: datetime | None,
    total: int | None,
    columns: list[str],
    rows: list[dict[str, Any]],
    page: int,
    page_size: int,
    warnings: list[str],
    has_more: bool = False,
) -> PluginRuntimeSnapshot:
    start_iso = start_time.isoformat(sep=" ") if isinstance(start_time, datetime) else None
    end_iso = end_time.isoformat(sep=" ") if isinstance(end_time, datetime) else None
    if total is None:
        total_pages = max(1, page + (1 if has_more else 0))
        total_records = 0
    else:
        total_pages = max(1, (total + page_size - 1) // page_size) if page_size > 0 else 1
        total_records = total
    return PluginRuntimeSnapshot(
        plugin_key=plugin_key,
        page=min(max(page, 1), total_pages),
        total_pages=total_pages,
        total_records=total_records,
        table=target_table,
        start_time=start_iso,
        end_time=end_iso,
        columns=list(columns),
        rows=list(rows),
        display_columns=[
            {
                "name": c,
                "label_en": view.get("column_labels", {}).get(c, {}).get("label_en", c),
                "label_zh": view.get("column_labels", {}).get(c, {}).get("label_zh", c),
            }
            for c in columns
        ],
        warnings=list(warnings),
    )


def _sync_plugin_runtime(snapshot: PluginRuntimeSnapshot) -> None:
    if _plugin_opcua_monitor is None:
        return
    existing = _plugin_opcua_monitor.get_runtime(snapshot.plugin_key)
    if existing is not None:
        snapshot.revision = existing.revision
        snapshot.last_trigger_batch = existing.last_trigger_batch
        snapshot.last_writeback_ok = existing.last_writeback_ok
    _plugin_opcua_monitor.update_runtime(snapshot)


def _execute_plugin_query(
    binding: dict[str, Any],
    *,
    page: int,
    page_size: int | None = None,
    table: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    query_mode: str = "time",
    batch_code: str | None = None,
    pagination_mode: str = "offset",
    page_cursor: Any = None,
    include_total: bool = True,
    cursor: int = -1,
    snapshot_all: bool = False,
) -> tuple[HistoryQueryResponse, PluginRuntimeSnapshot]:
    plugin_key = binding["plugin_key"]
    target_table = table or binding["bind_table"]
    target_group = binding["bind_group"]
    warnings: list[str] = []

    if target_group:
        schema_report = db.get_group_schema_report(target_group, cfg.get_group_baseline(target_group))
        available_tables = schema_report.get("tables", [])
        if table and table not in available_tables:
            raise ValueError(f"所选表不属于 group {target_group}: {table}")
    if not target_table and target_group:
        saved_baseline = cfg.get_group_baseline(target_group)
        schema_report = db.get_group_schema_report(target_group, saved_baseline)
        target_table = schema_report.get("baseline_table")
        if not schema_report.get("consistent", True):
            warnings.append("当前 group 表结构不一致，已按基准表查询")
    if not target_table:
        raise ValueError("插件未绑定可用数据表")

    view = cfg.resolve_query_view(binding["view_name"], table=target_table, group=target_group)
    table_list_cfg = _get_table_list_writeback_config(binding)
    if table_list_cfg is not None and table_list_cfg.is_advanced_mode:
        resolved_start, resolved_end = None, None
        resolved_batch_field, resolved_batch_code = None, None
        pagination_mode, page_cursor, include_total = "offset", None, True
        if start_time is not None or end_time is not None:
            warnings.append("高级 OPC UA 模式：已忽略时间范围限制")
        if batch_code:
            warnings.append("高级 OPC UA 模式：已忽略批次号条件")
    elif query_mode == "batch":
        if start_time is not None or end_time is not None:
            raise ValueError("按批次号查询时不能填写时间条件")
        resolved_batch_code = str(batch_code or "").strip() or None
        if resolved_batch_code is None:
            raise ValueError("按批次号查询时必须选择 BatchCode")
        resolved_batch_field = _resolve_batch_field(view, db.list_columns(target_table))
        if resolved_batch_field is None:
            raise ValueError("当前插件绑定的 Group 未配置有效的批次号绑定字段")
        resolved_start, resolved_end = None, None
    else:
        if str(batch_code or "").strip():
            raise ValueError("按时间查询时不能填写 BatchCode")
        if pagination_mode == "cursor" and (start_time is None or end_time is None):
            raise ValueError("按时间查询时必须填写开始时间和结束时间")
        resolved_start, resolved_end, time_warnings = _apply_time_guardrails(start_time, end_time)
        resolved_batch_field, resolved_batch_code = None, None
        warnings.extend(time_warnings)

    if snapshot_all:
        resolved_page_size = MAX_PLUGIN_SNAPSHOT_ROWS + 1
    else:
        resolved_page_size = page_size if page_size else int(binding["page_size"])
        resolved_page_size = min(max(resolved_page_size, 1), int(view["max_page_size"]))

    history_req = HistoryQueryRequest(
        table=target_table,
        columns=view.get("columns", []),
        start_time=resolved_start,
        end_time=resolved_end,
        time_field=view["time_field"],
        filters=[],
        batch_field=resolved_batch_field,
        batch_code=resolved_batch_code,
        page=max(page, 1),
        page_size=resolved_page_size,
        sort_by=view["sort_by"],
        sort_dir=view["sort_dir"],
        pagination_mode=pagination_mode,
        cursor=page_cursor,
        include_total=include_total,
    )
    query_result = db.query_history(history_req, page_size_cap=None) if snapshot_all else db.query_history(history_req)
    total, columns, rows, missing_columns = query_result
    if missing_columns:
        warnings.append("部分配置列在当前表中不存在，已自动忽略")

    if not snapshot_all:
        _cache_plugin_query(plugin_key, rows, target_table)
    snapshot = _plugin_runtime_snapshot(
        plugin_key,
        binding=binding,
        view=view,
        target_table=target_table,
        start_time=resolved_start,
        end_time=resolved_end,
        total=total,
        columns=columns,
        rows=rows,
        page=history_req.page,
        page_size=history_req.page_size,
        warnings=warnings,
        has_more=getattr(query_result, "has_more", False),
    )
    if not snapshot_all:
        _sync_plugin_runtime(snapshot)

    response = HistoryQueryResponse(
        total=total,
        page=history_req.page,
        page_size=history_req.page_size,
        columns=columns,
        rows=rows,
        display_columns=snapshot.display_columns,
        missing_columns=missing_columns,
        warnings=warnings,
        pagination_mode=history_req.pagination_mode,
        has_more=getattr(query_result, "has_more", False),
        next_cursor=getattr(query_result, "next_cursor", None),
    )
    return response, snapshot


def _snapshot_lock(plugin_key: str) -> asyncio.Lock:
    lock = _plugin_snapshot_locks.get(plugin_key)
    if lock is None:
        lock = asyncio.Lock()
        _plugin_snapshot_locks[plugin_key] = lock
    return lock


def _runtime_from_data_snapshot(data: PluginDataSnapshot, page: int) -> PluginRuntimeSnapshot:
    total_records = len(data.rows)
    total_pages = max(1, (total_records + data.page_size - 1) // data.page_size)
    resolved_page = min(max(int(page), 1), total_pages)
    start = (resolved_page - 1) * data.page_size
    rows = data.rows[start : start + data.page_size]
    return PluginRuntimeSnapshot(
        plugin_key=data.plugin_key,
        page=resolved_page,
        total_pages=total_pages,
        total_records=total_records,
        table=data.table,
        columns=list(data.columns),
        rows=[dict(row) for row in rows],
        display_columns=[dict(column) for column in data.display_columns],
        warnings=list(data.warnings),
    )


def _response_from_runtime(snapshot: PluginRuntimeSnapshot, page_size: int) -> HistoryQueryResponse:
    return HistoryQueryResponse(
        total=snapshot.total_records,
        page=snapshot.page,
        page_size=page_size,
        columns=list(snapshot.columns),
        rows=[dict(row) for row in snapshot.rows],
        display_columns=[dict(column) for column in snapshot.display_columns],
        warnings=list(snapshot.warnings),
        pagination_mode="offset",
        has_more=snapshot.page < snapshot.total_pages,
        next_cursor=None,
    )


async def _activate_plugin_snapshot(
    binding: dict[str, Any],
    *,
    table: str | None = None,
) -> tuple[HistoryQueryResponse, PluginRuntimeSnapshot]:
    plugin_key = binding["plugin_key"]
    existing_task = _plugin_snapshot_tasks.get(plugin_key)
    if existing_task is not None and not existing_task.done():
        logger.info("Plugin snapshot request merged with in-flight query plugin=%s", plugin_key)
        return await asyncio.shield(existing_task)

    task = asyncio.create_task(_activate_plugin_snapshot_once(binding, table=table))
    _plugin_snapshot_tasks[plugin_key] = task
    try:
        return await asyncio.shield(task)
    finally:
        if _plugin_snapshot_tasks.get(plugin_key) is task and task.done():
            _plugin_snapshot_tasks.pop(plugin_key, None)


async def _activate_plugin_snapshot_once(
    binding: dict[str, Any],
    *,
    table: str | None = None,
) -> tuple[HistoryQueryResponse, PluginRuntimeSnapshot]:
    plugin_key = binding["plugin_key"]
    total_started = time.perf_counter()
    async with _snapshot_lock(plugin_key):
        db_started = time.perf_counter()
        response, full_runtime = await asyncio.to_thread(
            _execute_plugin_query,
            binding,
            page=1,
            table=table,
            cursor=-1,
            snapshot_all=True,
        )
        db_elapsed = time.perf_counter() - db_started
        if response.total is not None and response.total > MAX_PLUGIN_SNAPSHOT_ROWS:
            raise ValueError(
                f"快照记录数 {response.total} 超过上限 {MAX_PLUGIN_SNAPSHOT_ROWS}，请缩小绑定表或调整快照策略"
            )
        if len(response.rows) > MAX_PLUGIN_SNAPSHOT_ROWS:
            raise ValueError(f"快照记录数超过上限 {MAX_PLUGIN_SNAPSHOT_ROWS}")

        convert_started = time.perf_counter()
        data = PluginDataSnapshot(
            plugin_key=plugin_key,
            table=full_runtime.table,
            page_size=int(binding["page_size"]),
            columns=list(response.columns),
            rows=response.rows,
            display_columns=[dict(column) for column in response.display_columns],
            warnings=list(response.warnings),
            created_at=datetime.now().isoformat(sep=" ", timespec="seconds"),
        )
        page_runtime = _runtime_from_data_snapshot(data, 1)
        existing = _plugin_opcua_monitor.get_runtime(plugin_key) if _plugin_opcua_monitor else None
        page_runtime.revision = (existing.revision if existing else 0) + 1

        # Replace only after the complete database read succeeds.
        _plugin_data_snapshots[plugin_key] = data
        _cache_plugin_query(plugin_key, page_runtime.rows, data.table)
        if _plugin_opcua_monitor is not None:
            _plugin_opcua_monitor.update_runtime(page_runtime)
        convert_elapsed = time.perf_counter() - convert_started
        await _run_plugin_writeback(binding, page_runtime.columns, page_runtime.rows, -1)
        logger.info(
            "Plugin snapshot activated plugin=%s rows=%d pages=%d db_ms=%.1f convert_ms=%.1f total_ms=%.1f created_at=%s",
            plugin_key,
            len(data.rows),
            page_runtime.total_pages,
            db_elapsed * 1000.0,
            convert_elapsed * 1000.0,
            (time.perf_counter() - total_started) * 1000.0,
            data.created_at,
        )
        return _response_from_runtime(page_runtime, data.page_size), page_runtime


async def _read_plugin_snapshot_page(
    binding: dict[str, Any],
    page: int,
) -> tuple[HistoryQueryResponse, PluginRuntimeSnapshot] | None:
    plugin_key = binding["plugin_key"]
    async with _snapshot_lock(plugin_key):
        data = _plugin_data_snapshots.get(plugin_key)
        if data is None:
            logger.info("Plugin snapshot page ignored before query plugin=%s page=%s", plugin_key, page)
            return None
        runtime = _runtime_from_data_snapshot(data, page)
        existing = _plugin_opcua_monitor.get_runtime(plugin_key) if _plugin_opcua_monitor else None
        runtime.revision = (existing.revision if existing else 0) + 1
        _cache_plugin_query(plugin_key, runtime.rows, data.table)
        if _plugin_opcua_monitor is not None:
            _plugin_opcua_monitor.update_runtime(runtime)
        await _run_plugin_writeback(binding, runtime.columns, runtime.rows, -1)
        return _response_from_runtime(runtime, data.page_size), runtime


async def _monitor_query_plugin_snapshot(plugin_key: str) -> PluginRuntimeSnapshot | None:
    try:
        binding = _resolve_plugin_binding(plugin_key)
        _response, runtime = await _activate_plugin_snapshot(binding)
        return runtime
    except Exception:
        logger.warning("OPC UA monitor snapshot query failed plugin=%s", plugin_key, exc_info=True)
        return None


async def _monitor_change_plugin_page(plugin_key: str, page: int) -> PluginRuntimeSnapshot | None:
    try:
        binding = _resolve_plugin_binding(plugin_key)
        result = await _read_plugin_snapshot_page(binding, page)
        return result[1] if result is not None else None
    except Exception:
        logger.warning("OPC UA monitor page change failed plugin=%s page=%s", plugin_key, page, exc_info=True)
        return None


async def _monitor_trigger_writeback(plugin_key: str, batch_no: str) -> bool:
    try:
        binding = _resolve_plugin_binding(plugin_key)
        return await _run_advanced_trigger_writeback(binding, batch_no)
    except Exception:
        logger.warning("OPC UA monitor trigger failed plugin=%s", plugin_key, exc_info=True)
        return False


async def _run_plugin_writeback(
    binding: dict[str, Any],
    columns: list[str],
    rows: list[dict[str, Any]],
    cursor: int,
) -> None:
    opcua = _get_opcua_connection()
    endpoint = opcua.get("endpoint_url", "")
    if not endpoint:
        logger.warning(
            "OPC UA writeback skipped: endpoint_url is empty or invalid; save IP/port in config page"
        )
        return
    writeback = OpcUaWritebackConfig.from_binding(binding.get("opcua_writeback"))
    try:
        await write_after_query_async(
            opcua.get("endpoint_url", ""),
            opcua.get("username", ""),
            opcua.get("password", ""),
            writeback,
            columns,
            rows,
            cursor,
        )
    except Exception as exc:
        logger.warning("OPC UA writeback hook failed: %s", exc)


async def _run_cursor_writeback(
    binding: dict[str, Any],
    cursor: int,
    row: dict[str, Any] | None = None,
) -> None:
    opcua = _get_opcua_connection()
    writeback = OpcUaWritebackConfig.from_binding(binding.get("opcua_writeback"))
    try:
        await write_cursor_only_async(
            opcua.get("endpoint_url", ""),
            opcua.get("username", ""),
            opcua.get("password", ""),
            writeback,
            cursor,
        )
    except Exception as exc:
        logger.warning("OPC UA cursor writeback failed: %s", exc)

    if row is None and cursor >= 0:
        row = _get_cached_plugin_row(binding["plugin_key"], cursor)
    await _run_table_list_writeback(binding, cursor=cursor, row=row)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "app" / "static" / "query.html")


@app.get("/query")
def query_page() -> FileResponse:
    return FileResponse(BASE_DIR / "app" / "static" / "query.html")


@app.get("/config")
def config_page() -> FileResponse:
    return FileResponse(BASE_DIR / "app" / "static" / "config.html")


@app.get("/plugins/{plugin_page}.html")
def plugin_page(plugin_page: str) -> FileResponse:
    # 路径仅用于标识插件，实际页面使用统一 SpecializedQuery 组件
    try:
        _resolve_plugin_binding(plugin_page)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(BASE_DIR / "app" / "static" / "specialized_query.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        db.ping()
        return {"status": "ok"}
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Health check failed: {exc}") from exc


@app.get("/api/db/check")
def check_database(request: Request) -> dict[str, Any]:
    _enforce_rate_limit(request)
    try:
        db.ping()
        groups = db.list_groups()
        return {
            "status": "ok",
            "db_type": db.db_type,
            "database": db.db_config.get("name", ""),
            "groups_count": len(groups),
        }
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database check failed: {exc}") from exc


@app.get("/api/config/app-settings")
def get_app_settings() -> dict[str, Any]:
    return _normalize_app_settings(config_store.get_public_app_settings())


@app.get("/api/config/profiles")
def list_config_profiles() -> dict[str, Any]:
    return {
        "active": config_store.get_active_profile_name(),
        "profiles": config_store.list_profiles(),
    }


@app.post("/api/config/profiles/active")
def set_active_config_profile(payload: dict[str, Any]) -> dict[str, Any]:
    filename = str(payload.get("filename", "")).strip()
    try:
        config_store.set_active_profile(filename)
        _apply_runtime_settings(_load_app_settings())
        return {
            "status": "loaded",
            "active": config_store.get_active_profile_name(),
            "profiles": config_store.list_profiles(),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/profiles/create")
def create_config_profile(payload: dict[str, Any]) -> dict[str, Any]:
    filename = str(payload.get("filename", "")).strip()
    try:
        result = config_store.create_profile(filename)
        _apply_runtime_settings(_load_app_settings())
        return result
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/profiles/delete")
def delete_config_profile(payload: dict[str, Any]) -> dict[str, Any]:
    filename = str(payload.get("filename", "")).strip()
    try:
        result = config_store.delete_profile(filename)
        _apply_runtime_settings(_load_app_settings())
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/app-settings")
def save_app_settings(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        public_settings = _save_app_settings(payload)
        _apply_runtime_settings(_load_app_settings())
        return {"status": "saved", "settings": public_settings}
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/meta/groups")
def meta_groups(request: Request) -> dict[str, Any]:
    _enforce_rate_limit(request)
    try:
        return {"groups": db.list_groups()}
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"List groups failed: {exc}") from exc


@app.get("/api/meta/tables")
def meta_tables(group: str, request: Request) -> dict[str, Any]:
    _enforce_rate_limit(request)
    try:
        tables = db.list_tables_by_group(group)
        table_kinds: dict[str, str] = {}
        for table_name in tables:
            info = table_group_info(table_name)
            if info is not None:
                table_kinds[table_name] = info.kind
        return {"tables": tables, "table_kinds": table_kinds}
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"List tables failed: {exc}") from exc


@app.get("/api/meta/group-schema")
def meta_group_schema(group: str, request: Request) -> dict[str, Any]:
    _enforce_rate_limit(request)
    try:
        baseline = cfg.get_group_baseline(group)
        report = db.get_group_schema_report(group=group, baseline_table=baseline)
        report["saved_baseline_table"] = baseline
        return report
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Group schema check failed: {exc}") from exc


@app.get("/api/meta/columns")
def meta_columns(table: str, request: Request) -> dict[str, Any]:
    _enforce_rate_limit(request)
    try:
        return {"columns": db.list_columns(table)}
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/meta/database-tables")
def meta_database_tables(request: Request) -> dict[str, list[str]]:
    _enforce_rate_limit(request)
    try:
        return {"tables": db.list_tables()}
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/meta/batch-codes")
def meta_batch_codes(view_name: str, request: Request, group: str | None = None) -> dict[str, Any]:
    _enforce_rate_limit(request)
    try:
        source = cfg.resolve_batch_source(view_name, group)
        if not source["table"] or not source["field"]:
            raise HTTPException(status_code=400, detail="当前 View 未配置批次号来源表和字段")
        available_columns = db.list_columns(source["table"])
        if source["field"] not in available_columns:
            raise HTTPException(
                status_code=400,
                detail=f"批次号来源字段不存在于表 {source['table']}: {source['field']}",
            )
        return {
            "items": db.list_batch_codes(source["table"], source["field"], limit=1000),
            "source": source,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取批次号来源失败: {exc}") from exc


@app.post("/api/history", response_model=HistoryQueryResponse)
def history(req: HistoryQueryRequest, request: Request) -> HistoryQueryResponse:
    _enforce_rate_limit(request)
    warnings: list[str] = []
    if req.pagination_mode == "offset":
        start_time, end_time, warnings = _apply_time_guardrails(req.start_time, req.end_time)
        req.start_time = start_time
        req.end_time = end_time
    try:
        query_result = db.query_history(req)
        total, columns, rows, missing_columns = query_result
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return HistoryQueryResponse(
        total=total,
        page=req.page,
        page_size=req.page_size,
        columns=columns,
        rows=rows,
        display_columns=[{"name": c, "label_en": c, "label_zh": c} for c in columns],
        missing_columns=missing_columns,
        warnings=warnings,
        pagination_mode=req.pagination_mode,
        has_more=getattr(query_result, "has_more", False),
        next_cursor=getattr(query_result, "next_cursor", None),
    )


@app.get("/api/query/views")
def get_query_views() -> dict[str, Any]:
    try:
        return {"views": cfg.get_query_views()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/query/configured-groups")
def get_configured_groups(view_name: str) -> dict[str, Any]:
    try:
        groups = cfg.list_configured_groups_by_view(view_name)
        return {"view_name": view_name, "groups": groups}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/plugins/resolve/{plugin_key}")
def resolve_plugin(plugin_key: str, request: Request) -> dict[str, Any]:
    _enforce_rate_limit(request)
    try:
        binding = _resolve_plugin_binding(plugin_key)
        resolved_table = binding["bind_table"]
        resolved_group = binding["bind_group"]
        schema_report = None
        if resolved_group:
            saved_baseline = cfg.get_group_baseline(resolved_group)
            schema_report = db.get_group_schema_report(resolved_group, saved_baseline)
        if not resolved_table and resolved_group:
            saved_baseline = cfg.get_group_baseline(resolved_group)
            resolved_table = schema_report.get("baseline_table")
        if not resolved_table:
            raise ValueError("无法解析插件绑定表")
        binding["resolved_table"] = resolved_table
        binding["resolved_group"] = resolved_group
        binding["schema_report"] = schema_report
        view = cfg.resolve_query_view(
            binding["view_name"], table=resolved_table, group=resolved_group
        )
        binding["batch_field"] = _resolve_batch_field(view, db.list_columns(resolved_table)) or ""
        binding["batch_source"] = cfg.resolve_batch_source(
            binding["view_name"], resolved_group
        )
        return binding
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/plugins/runtime-state/{plugin_key}")
def get_plugin_runtime_state(
    plugin_key: str,
    request: Request,
    since_revision: int | None = None,
) -> dict[str, Any]:
    _enforce_rate_limit(request)
    if _plugin_opcua_monitor is None:
        raise HTTPException(status_code=404, detail="OPC UA monitor not running")
    snapshot = _plugin_opcua_monitor.get_runtime(plugin_key)
    if snapshot is None:
        try:
            binding = _resolve_plugin_binding(plugin_key)
            config = _get_table_list_writeback_config(binding)
            if config is None or not config.is_advanced_mode:
                raise HTTPException(status_code=404, detail="插件未启用高级 OPC UA 模式")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if since_revision == 0:
            return {"plugin_key": plugin_key, "revision": 0, "changed": False}
        return {
            "plugin_key": plugin_key,
            "mode": "advanced",
            "page": 1,
            "total_pages": 1,
            "total_records": 0,
            "columns": [],
            "rows": [],
            "display_columns": [],
            "warnings": [],
            "revision": 0,
            "has_snapshot": False,
            "snapshot_created_at": None,
            "changed": True,
        }
    if since_revision is not None and since_revision == snapshot.revision:
        return {
            "plugin_key": plugin_key,
            "revision": snapshot.revision,
            "changed": False,
        }
    result = snapshot.to_api_dict()
    data_snapshot = _plugin_data_snapshots.get(plugin_key)
    result["has_snapshot"] = data_snapshot is not None
    result["snapshot_created_at"] = data_snapshot.created_at if data_snapshot else None
    result["changed"] = True
    return result


@app.post("/api/plugins/query/{plugin_key}", response_model=HistoryQueryResponse)
async def query_plugin(plugin_key: str, payload: PluginQueryRequest, request: Request) -> HistoryQueryResponse:
    _enforce_rate_limit(request)
    try:
        binding = _resolve_plugin_binding(plugin_key)
        table_list_config = _get_table_list_writeback_config(binding)
        if table_list_config is not None and table_list_config.is_advanced_mode:
            response, _runtime = await _activate_plugin_snapshot(binding, table=payload.table)
            return response
        response, _snapshot = _execute_plugin_query(
            binding,
            page=payload.page,
            page_size=payload.page_size,
            table=payload.table,
            start_time=payload.start_time,
            end_time=payload.end_time,
            query_mode=payload.query_mode,
            batch_code=payload.batch_code,
            pagination_mode=payload.pagination_mode,
            page_cursor=payload.page_cursor,
            include_total=payload.include_total,
            cursor=-1 if payload.cursor is None else int(payload.cursor),
        )
        cursor = -1 if payload.cursor is None else int(payload.cursor)
        await _run_plugin_writeback(binding, response.columns, response.rows, cursor)
        await _run_table_list_writeback(
            binding,
            cursor=cursor,
            row=response.rows[cursor] if 0 <= cursor < len(response.rows) else None,
        )
        return response
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/plugins/cursor/{plugin_key}")
async def update_plugin_cursor(
    plugin_key: str,
    payload: PluginCursorRequest,
    request: Request,
) -> dict[str, str]:
    _enforce_rate_limit(request)
    try:
        binding = _resolve_plugin_binding(plugin_key)
        cursor = int(payload.cursor)
        row = _get_cached_plugin_row(plugin_key, cursor) if cursor >= 0 else None
        await _run_cursor_writeback(binding, cursor, row=row)
        return {"status": "ok"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/history/by-view", response_model=HistoryQueryResponse)
def history_by_view(req: ViewHistoryQueryRequest, request: Request) -> HistoryQueryResponse:
    _enforce_rate_limit(request)
    target_table = req.table
    target_group = req.group

    if not target_table:
        if not target_group:
            raise HTTPException(status_code=400, detail="table 或 group 必须提供一个")
        saved_baseline = cfg.get_group_baseline(target_group)
        schema_report = db.get_group_schema_report(target_group, saved_baseline)
        target_table = schema_report.get("baseline_table")
        if not target_table:
            raise HTTPException(status_code=400, detail=f"group 没有可用数据表: {target_group}")

    try:
        view = cfg.resolve_query_view(req.view_name, table=target_table, group=target_group)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    merged_filters: list[QueryFilter] = []
    warnings: list[str] = []
    for item in view.get("default_filters", []):
        if isinstance(item, dict):
            merged_filters.append(QueryFilter(**item))
    merged_filters.extend(req.filters)

    page_size = req.page_size if req.page_size else int(view["page_size"])
    page_size = min(max(page_size, 1), int(view["max_page_size"]))

    try:
        available_columns = db.list_columns(target_table)
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取表结构失败: {exc}") from exc

    time_field = view["time_field"]
    if time_field not in available_columns:
        if "collection_time" in available_columns:
            time_field = "collection_time"
        elif available_columns:
            time_field = available_columns[0]
        warnings.append(f"视图时间字段不存在，已回退为 {time_field}")

    sort_by = view["sort_by"]
    if sort_by not in available_columns:
        sort_by = time_field
        warnings.append(f"视图排序字段不存在，已回退为 {sort_by}")

    batch_code = str(req.batch_code or "").strip() or None
    if req.query_mode == "batch":
        if req.start_time is not None or req.end_time is not None:
            raise HTTPException(status_code=400, detail="按批次号查询时不能填写时间条件")
        if batch_code is None:
            raise HTTPException(status_code=400, detail="按批次号查询时必须选择 BatchCode")
        batch_field = _resolve_batch_field(view, available_columns)
        if batch_field is None:
            raise HTTPException(status_code=400, detail="当前 Group 未配置有效的批次号绑定字段")
        start_time = None
        end_time = None
    else:
        if batch_code is not None:
            raise HTTPException(status_code=400, detail="按时间查询时不能填写 BatchCode")
        if req.start_time is None or req.end_time is None:
            raise HTTPException(status_code=400, detail="按时间查询时必须填写开始时间和结束时间")
        batch_field = None
        start_time = req.start_time
        end_time = req.end_time

    if req.pagination_mode == "offset" and req.query_mode == "time":
        start_time, end_time, time_warnings = _apply_time_guardrails(start_time, end_time)
        warnings.extend(time_warnings)

    history_req = HistoryQueryRequest(
        table=target_table,
        columns=req.columns if req.columns is not None else view.get("columns", []),
        start_time=start_time,
        end_time=end_time,
        time_field=time_field,
        filters=merged_filters,
        batch_field=batch_field,
        batch_code=batch_code,
        combine_mode="and",
        page=req.page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=view["sort_dir"],
        pagination_mode=req.pagination_mode,
        cursor=req.cursor,
        include_total=req.include_total,
    )

    try:
        query_result = db.query_history(history_req)
        total, columns, rows, missing_columns = query_result
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if missing_columns:
        warnings.append("部分配置列在当前表中不存在，已自动忽略")
    if total == 0:
        warnings.append("当前查询条件下没有数据")
    if target_group:
        warnings.append(f"group={target_group}，本次使用基准表 {target_table} 查询")

    return HistoryQueryResponse(
        total=total,
        page=history_req.page,
        page_size=history_req.page_size,
        columns=columns,
        rows=rows,
        display_columns=[
            {
                "name": c,
                "label_en": view.get("column_labels", {}).get(c, {}).get("label_en", c),
                "label_zh": view.get("column_labels", {}).get(c, {}).get("label_zh", c),
            }
            for c in columns
        ],
        missing_columns=missing_columns,
        warnings=warnings,
        pagination_mode=history_req.pagination_mode,
        has_more=getattr(query_result, "has_more", False),
        next_cursor=getattr(query_result, "next_cursor", None),
    )


@app.get("/api/config/query-view")
def get_query_view_config() -> dict[str, Any]:
    return cfg.get_query_view_config()


@app.post("/api/config/query-view")
def save_query_view_config(payload: dict[str, Any]) -> dict[str, str]:
    try:
        cfg.save_query_view_config(payload)
        _clear_plugin_snapshots()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved"}


@app.get("/api/config/query-table")
def get_query_table_config(view_name: str, table: str) -> dict[str, Any]:
    try:
        return cfg.get_query_table_config(view_name, table)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/query-table")
def save_query_table_config(payload: QueryTableConfigUpdateRequest) -> dict[str, str]:
    try:
        cfg.update_query_table_config(
            view_name=payload.view_name,
            table=payload.table,
            sort_by=payload.sort_by,
            sort_dir=payload.sort_dir,
            page_size=payload.page_size,
            columns=[c.model_dump() for c in payload.columns],
        )
        _clear_plugin_snapshots()
        return {"status": "saved"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/config/group-baseline")
def get_group_baseline(group: str) -> dict[str, Any]:
    return {"group": group, "baseline_table": cfg.get_group_baseline(group)}


@app.post("/api/config/group-baseline")
def set_group_baseline(payload: GroupBaselineUpdateRequest) -> dict[str, str]:
    cfg.set_group_baseline(payload.group, payload.baseline_table)
    _clear_plugin_snapshots()
    return {"status": "saved"}


@app.get("/api/config/query-batch-source")
def get_query_batch_source(view_name: str) -> dict[str, str]:
    try:
        return cfg.get_query_batch_source(view_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/plugins/snapshot-page/{plugin_key}", response_model=HistoryQueryResponse)
async def plugin_snapshot_page(
    plugin_key: str,
    payload: PluginQueryRequest,
    request: Request,
) -> HistoryQueryResponse:
    _enforce_rate_limit(request)
    try:
        binding = _resolve_plugin_binding(plugin_key)
        config = _get_table_list_writeback_config(binding)
        if config is None or not config.is_advanced_mode:
            raise ValueError("插件未启用高级 OPC UA 快照模式")
        result = await _read_plugin_snapshot_page(binding, payload.page)
        if result is None:
            raise ValueError("尚未建立查询快照，请先点击查询")
        return result[0]
    except (OperationalError, SQLAlchemyError) as exc:
        _raise_db_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/query-batch-source")
def save_query_batch_source(payload: QueryBatchSourceUpdateRequest) -> dict[str, Any]:
    source_table = str(payload.table or "").strip()
    source_field = str(payload.field or "").strip()
    if bool(source_table) != bool(source_field):
        raise HTTPException(status_code=400, detail="批次号来源表和字段必须同时设置或同时留空")
    if source_table:
        try:
            available_columns = db.list_columns(source_table)
        except (OperationalError, SQLAlchemyError) as exc:
            _raise_db_error(exc)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"读取批次号来源表失败: {exc}") from exc
        if source_field not in available_columns:
            raise HTTPException(
                status_code=400,
                detail=f"批次号来源字段不存在于表 {source_table}: {source_field}",
            )
    try:
        cfg.update_query_batch_source(payload.view_name, source_table, source_field)
        _clear_plugin_snapshots()
        return {"status": "saved", **cfg.get_query_batch_source(payload.view_name)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/config/query-group")
def get_query_group_config(view_name: str, group: str) -> dict[str, Any]:
    saved_baseline = cfg.get_group_baseline(group)
    schema_report = db.get_group_schema_report(group=group, baseline_table=saved_baseline)
    baseline_table = schema_report.get("baseline_table")
    if not baseline_table:
        raise HTTPException(status_code=400, detail=f"group 没有可用数据表: {group}")
    data = cfg.get_query_group_config(view_name=view_name, group=group, baseline_table=baseline_table)
    data["schema_report"] = schema_report
    return data


@app.post("/api/config/query-group")
def save_query_group_config(payload: QueryGroupConfigUpdateRequest) -> dict[str, str]:
    if payload.batch_field:
        baseline_table = payload.baseline_table or cfg.get_group_baseline(payload.group)
        if not baseline_table:
            raise HTTPException(status_code=400, detail="配置批次号绑定字段时必须提供基准表")
        try:
            available_columns = db.list_columns(baseline_table)
        except (OperationalError, SQLAlchemyError) as exc:
            _raise_db_error(exc)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"读取基准表字段失败: {exc}") from exc
        if payload.batch_field not in available_columns:
            raise HTTPException(
                status_code=400,
                detail=f"批次号绑定字段不存在于基准表 {baseline_table}: {payload.batch_field}",
            )
    cfg.update_query_group_config(
        view_name=payload.view_name,
        group=payload.group,
        time_field=payload.time_field,
        batch_field=payload.batch_field,
        sort_by=payload.sort_by,
        sort_dir=payload.sort_dir,
        page_size=payload.page_size,
        columns=[c.model_dump() for c in payload.columns],
        baseline_table=payload.baseline_table,
    )
    _clear_plugin_snapshots()
    return {"status": "saved"}


@app.delete("/api/config/query-group")
def delete_query_group_config(view_name: str, group: str) -> dict[str, Any]:
    try:
        existed = cfg.delete_query_group_config(view_name=view_name, group=group)
        _clear_plugin_snapshots()
        return {"status": "deleted" if existed else "not_found", "view_name": view_name, "group": group}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/config/plugins")
def get_plugins_config() -> dict[str, Any]:
    return _load_plugin_config()


@app.post("/api/config/plugins")
def save_plugins_config(payload: dict[str, Any]) -> dict[str, str]:
    try:
        _save_plugin_config(payload)
        _clear_plugin_snapshots()
        return {"status": "saved"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/config/opcua")
def get_opcua_config() -> dict[str, Any]:
    return config_store.get_public_opcua_settings()


async def _run_opcua_connection_check(
    endpoint_url: str,
    username: str,
    password: str,
) -> dict[str, Any]:
    endpoint = normalize_opcua_endpoint_url(endpoint_url)
    saved = config_store.get_opcua_settings()
    if not endpoint:
        endpoint = normalize_opcua_endpoint_url(str(saved.get("endpoint_url", "") or ""))
    if not username:
        username = str(saved.get("username", "") or "")
    if not password:
        password = str(saved.get("password", "") or "")

    result = await opcua_client.check_connection(endpoint, username, password)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("message", "OPC UA 连接失败")))
    return result


@app.post("/api/config/opcua")
async def save_opcua_config(payload: OpcUaSettingsRequest) -> dict[str, Any]:
    try:
        if payload.test_only:
            return await _run_opcua_connection_check(
                payload.endpoint_url,
                payload.username,
                payload.password,
            )
        return config_store.save_opcua_settings(payload.model_dump(exclude={"test_only"}))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/opcua/check")
async def check_opcua_connection(request: Request, payload: OpcUaSettingsRequest | None = None) -> dict[str, Any]:
    _enforce_rate_limit(request)
    body = payload or OpcUaSettingsRequest()
    return await _run_opcua_connection_check(body.endpoint_url, body.username, body.password)
