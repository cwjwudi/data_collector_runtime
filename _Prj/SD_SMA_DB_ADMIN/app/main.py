from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import pymysql
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent.parent
COMMON_ROOT = BASE_DIR.parent / "SD_SMA_COMMON"
if COMMON_ROOT.is_dir() and str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from sd_sma_common import FilesystemBrowser, FilesystemBrowserError, windows_removable_roots

AUTH_TOKEN_ENV = "SD_SMA_WEB_TOKEN"
AUTH_TOKEN_HEADER = "X-SD-SMA-Token"
AUTH_EXEMPT_PATHS = {"/api/health"}
BACKUP_DIR_ENV = "SD_SMA_DB_ADMIN_BACKUP_DIR"
JOB_LOGGER = logging.getLogger("sd_sma.db_admin.job")


def configure_console_logging() -> None:
    """Route useful logs to the process console for _Launcher; mute HTTP access INFO."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    # Access / request spam is INFO by default; keep WARNING+ only.
    for name in ("uvicorn.access", "uvicorn.protocols.http", "uvicorn.protocols.http.h11_impl"):
        logging.getLogger(name).setLevel(logging.WARNING)


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


def _resolve_config_dir() -> Path:
    raw = os.getenv("SD_SMA_DB_ADMIN_CONFIG_DIR")
    if not raw:
        return (BASE_DIR / "config").resolve()
    value = raw.replace("${DB_ADMIN_ROOT}", str(BASE_DIR))
    path = Path(os.path.expandvars(value))
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


CONFIG_DIR = _resolve_config_dir()
CONFIG_FILE = CONFIG_DIR / "default.json"
SECRET_KEY_FILE = CONFIG_DIR / ".sd_sma_db_admin_fernet.key"
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


class DbConnection(BaseModel):
    engine: str = "mysql"
    host: str = "127.0.0.1"
    port: int = 3306
    database: str = ""
    username: str = ""
    password: str = ""
    sqlite_path: str = ""


class BackupRequest(BaseModel):
    connection: DbConnection
    database: str
    output_dir: str = ""


class BackupTableRequest(BaseModel):
    connection: DbConnection
    database: str
    table: str
    output_dir: str = ""


class TableRequest(BaseModel):
    connection: DbConnection
    database: str


class CsvExportRequest(BaseModel):
    connection: DbConnection
    database: str
    table: str
    output_dir: str = ""


class ConfirmationRequest(BaseModel):
    action: str
    database: str
    table: str = ""


class RestoreBackupRequest(BaseModel):
    connection: DbConnection
    database: str
    filename: str
    confirmation_token: str


class ImportServerCsvRequest(BaseModel):
    connection: DbConnection
    database: str
    table: str
    filename: str
    truncate: bool = False
    force: bool = False
    confirmation_token: str


class CopyBackupsRequest(BaseModel):
    filenames: list[str]
    destination_dir: str


class CopyBackupFilesRequest(BaseModel):
    paths: list[str]
    destination_dir: str


class DeleteBackupFilesConfirmationRequest(BaseModel):
    paths: list[str]


class DeleteBackupFilesRequest(BaseModel):
    paths: list[str]
    confirmation_token: str


class JobCancelled(RuntimeError):
    pass


class JobFailedWithResult(RuntimeError):
    def __init__(self, message: str, result: dict[str, Any]) -> None:
        super().__init__(message)
        self.result = result


def load_config() -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_json(CONFIG_FILE, cfg)


def _secret_key() -> bytes:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_KEY_FILE.exists():
        return SECRET_KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    SECRET_KEY_FILE.write_bytes(key)
    try:
        SECRET_KEY_FILE.chmod(0o600)
    except OSError:
        pass
    return key


def _encrypt_password(value: str) -> str:
    return Fernet(_secret_key()).encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt_password(value: str) -> str:
    if not value:
        return ""
    try:
        return Fernet(_secret_key()).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("数据库密码密文无法在本机解密，请在 Launcher 中重新设置凭据") from exc


def _config_path(value: str | None, default: Path) -> Path:
    if not value:
        return default
    raw = value.replace("${DB_ADMIN_ROOT}", str(BASE_DIR)).replace("${CONFIG_DIR}", str(CONFIG_DIR))
    path = Path(os.path.expandvars(raw))
    if not path.is_absolute():
        path = CONFIG_DIR / path
    return path.resolve()


def backup_dir() -> Path:
    cfg = load_config()
    configured = (os.getenv(BACKUP_DIR_ENV) or "").strip()
    if not configured:
        configured = str(cfg.get("backup_dir") or "${DB_ADMIN_ROOT}/backups")
    path = _config_path(configured, BASE_DIR / "backups")
    path.mkdir(parents=True, exist_ok=True)
    return path


def filesystem_browser(purpose: str) -> tuple[FilesystemBrowser, set[str], bool]:
    cfg = load_config()
    configured = cfg.get("allowed_browse_roots") or []
    if not isinstance(configured, list):
        configured = []
    roots = [backup_dir(), *configured, *windows_removable_roots()]
    normalized = purpose.strip().lower()
    if normalized == "sql":
        return FilesystemBrowser(roots), {".sql"}, True
    if normalized == "csv":
        return FilesystemBrowser(roots), {".csv"}, True
    if normalized == "directory":
        return FilesystemBrowser(roots), set(), False
    if normalized == "removable-directory":
        return FilesystemBrowser(windows_removable_roots()), set(), False
    raise FilesystemBrowserError("Unsupported browse purpose")


def last_output_dir() -> Path | None:
    cfg = load_config()
    raw = str(cfg.get("last_output_dir") or "").strip()
    if not raw:
        return None
    path = _config_path(raw, backup_dir())
    return path if path.is_dir() else None


def persist_last_output_dir(path: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_dir():
        return
    cfg = load_config()
    if str(cfg.get("last_output_dir") or "") == str(resolved):
        return
    cfg["last_output_dir"] = str(resolved)
    save_config(cfg)


def backup_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in (backup_dir(), last_output_dir()):
        if candidate is None:
            continue
        key = str(candidate.resolve())
        if key in seen or not candidate.is_dir():
            continue
        seen.add(key)
        roots.append(candidate.resolve())
    return roots


def resolve_output_dir(value: str | None) -> Path:
    raw = (value or "").strip()
    if not raw:
        return backup_dir()
    raw = raw.replace("${DB_ADMIN_ROOT}", str(BASE_DIR)).replace("${CONFIG_DIR}", str(CONFIG_DIR))
    path = Path(os.path.expandvars(raw))
    if not path.is_absolute():
        path = CONFIG_DIR / path
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"Output directory is not available: {path}")
    return path


_TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    "mysqldump": ("mysqldump.exe", "mariadb-dump.exe", "mysqldump", "mariadb-dump"),
    "mysql": ("mysql.exe", "mariadb.exe", "mysql", "mariadb"),
}


def _tool_not_found_message(name: str) -> str:
    return (
        f"找不到 MySQL/MariaDB 客户端工具: {name}。"
        "请在 mysql_tools 中配置绝对路径，或将客户端加入 PATH，"
        "或在项目 _tools / 同级 _tools 下放置客户端。"
    )


def _mysql_tools_search_roots() -> list[Path]:
    """Project-local _tools first, then sibling _tools next to the project folder."""
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in (BASE_DIR / "_tools", BASE_DIR.parent / "_tools"):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen or not resolved.is_dir():
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def _find_tool_under_roots(aliases: tuple[str, ...], roots: list[Path]) -> str | None:
    scored: list[tuple[int, int, str]] = []
    for root_index, root in enumerate(roots):
        for alias in aliases:
            for match in root.rglob(alias):
                if not match.is_file():
                    continue
                # Prefer earlier roots, then binaries under a bin/ directory.
                bin_penalty = 0 if match.parent.name.lower() == "bin" else 1
                scored.append((root_index, bin_penalty, str(match.resolve())))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1], item[2].lower()))
    return scored[0][2]


def resolve_mysql_tool(name: str) -> str:
    cfg = load_config()
    tools = cfg.get("mysql_tools") if isinstance(cfg.get("mysql_tools"), dict) else {}
    configured = str(tools.get(name) or "").strip()
    aliases = _TOOL_ALIASES.get(name, (name, f"{name}.exe"))

    if configured:
        configured_path = Path(os.path.expandvars(configured))
        if configured_path.is_file():
            return str(configured_path.resolve())
        # Bare command name in config: resolve via PATH / aliases below.
        if Path(configured).name == configured:
            which_hit = shutil.which(configured)
            if which_hit:
                return which_hit
            for alias in aliases:
                which_hit = shutil.which(alias)
                if which_hit:
                    return which_hit

    for alias in aliases:
        which_hit = shutil.which(alias)
        if which_hit:
            return which_hit

    found = _find_tool_under_roots(aliases, _mysql_tools_search_roots())
    if found:
        return found

    raise FileNotFoundError(_tool_not_found_message(name))


def mysql_tool(name: str) -> str:
    """Resolve a MySQL/MariaDB client tool; raises FileNotFoundError with a clear message."""
    return resolve_mysql_tool(name)


def mysql_dump_client_is_mariadb(tool_path: str) -> bool:
    try:
        completed = subprocess.run(
            [tool_path, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    text = f"{completed.stdout or ''}{completed.stderr or ''}"
    return "MariaDB" in text


def mysql_client_plugin_args(tool_path: str) -> list[str]:
    """Return an explicit plugin directory for packaged MariaDB/MySQL clients."""
    try:
        tool = Path(tool_path).resolve()
    except OSError:
        return []
    candidates = (
        tool.parent.parent / "lib" / "plugin",
        tool.parent / "lib" / "plugin",
        tool.parent,
    )
    for candidate in candidates:
        if (candidate / "caching_sha2_password.dll").is_file():
            return [f"--plugin-dir={candidate.resolve()}"]
    return []


def default_connection() -> dict[str, Any]:
    cfg = load_config()
    conn = cfg.get("default_connection") if isinstance(cfg.get("default_connection"), dict) else {}
    base = DbConnection().model_dump()
    base.update({key: value for key, value in conn.items() if key not in {"password", "password_enc"}})
    for key, env_name in {
        "host": "SD_SMA_DB_HOST",
        "port": "SD_SMA_DB_PORT",
        "username": "SD_SMA_DB_USERNAME",
        "database": "SD_SMA_DB_DATABASE",
    }.items():
        if os.getenv(env_name):
            base[key] = int(os.environ[env_name]) if key == "port" else os.environ[env_name]
    base["password"] = ""
    base["password_configured"] = bool(
        os.getenv("SD_SMA_DB_PASSWORD") or conn.get("password_enc") or conn.get("password")
    )
    base["password_managed"] = bool(os.getenv("SD_SMA_DB_CREDENTIAL_MANAGED"))
    return base


def persist_default_connection(conn: DbConnection) -> dict[str, Any]:
    cfg = load_config()
    current = dict(cfg.get("default_connection") or {}) if isinstance(cfg.get("default_connection"), dict) else {}
    legacy_password = str(current.get("password") or "")
    payload = conn.model_dump()
    for key in ("engine", "host", "port", "username", "database"):
        if key in payload:
            current[key] = payload[key]
    current.pop("password", None)
    if os.getenv("SD_SMA_DB_CREDENTIAL_MANAGED"):
        current.pop("password_enc", None)
    elif conn.password:
        current["password_enc"] = _encrypt_password(conn.password)
    elif legacy_password and not current.get("password_enc"):
        current["password_enc"] = _encrypt_password(legacy_password)
    cfg["default_connection"] = current
    save_config(cfg)
    return default_connection()


def connection_password(conn: DbConnection) -> str:
    managed = os.getenv("SD_SMA_DB_PASSWORD")
    if managed is not None:
        return managed
    if conn.password:
        return conn.password
    cfg = load_config()
    current = cfg.get("default_connection") if isinstance(cfg.get("default_connection"), dict) else {}
    if current.get("password_enc"):
        return _decrypt_password(str(current["password_enc"]))
    # One-time compatibility for legacy files; the next save migrates it.
    return str(current.get("password") or "")


def managed_connection(conn: DbConnection) -> DbConnection:
    """Apply all Launcher-managed connection fields, not only the password."""
    updates: dict[str, Any] = {}
    for key, env_name in {
        "host": "SD_SMA_DB_HOST",
        "port": "SD_SMA_DB_PORT",
        "username": "SD_SMA_DB_USERNAME",
        "database": "SD_SMA_DB_DATABASE",
    }.items():
        value = os.getenv(env_name)
        if value:
            updates[key] = int(value) if key == "port" else value
    return conn.model_copy(update=updates) if updates else conn

def safe_identifier(value: str, label: str) -> str:
    text = (value or "").strip()
    if not SAFE_NAME_RE.match(text):
        raise ValueError(f"{label} can only contain letters, digits, and underscores: {value!r}")
    return text


def quote_ident(value: str) -> str:
    return f"`{safe_identifier(value, 'identifier')}`"


def connect_mysql(conn: DbConnection, database: str | None = None, *, autocommit: bool = True):
    conn = managed_connection(conn)
    return pymysql.connect(
        host=conn.host or "127.0.0.1",
        port=int(conn.port or 3306),
        user=conn.username or "",
        password=connection_password(conn),
        database=database or conn.database or None,
        charset="utf8mb4",
        autocommit=autocommit,
        connect_timeout=10,
        read_timeout=3600,
        write_timeout=3600,
    )


def list_mysql_databases(conn: DbConnection) -> list[dict[str, Any]]:
    hidden = ("information_schema", "performance_schema", "mysql", "sys")
    with connect_mysql(conn) as db:
        with db.cursor() as cur:
            cur.execute(
                "SELECT table_schema, COALESCE(SUM(data_length + index_length), 0) "
                "FROM information_schema.tables "
                "WHERE table_schema NOT IN (%s, %s, %s, %s) "
                "GROUP BY table_schema "
                "ORDER BY table_schema",
                hidden,
            )
            sized = {
                str(row[0]): int(row[1] or 0)
                for row in cur.fetchall()
                if row and row[0] is not None
            }
            cur.execute("SHOW DATABASES")
            names = [str(row[0]) for row in cur.fetchall() if row and row[0] is not None]
    return [
        {"name": name, "size_bytes": sized.get(name, 0)}
        for name in names
        if name not in set(hidden)
    ]


def list_mysql_tables(conn: DbConnection, database: str) -> list[dict[str, Any]]:
    dbname = safe_identifier(database, "database")
    with connect_mysql(conn) as db:
        with db.cursor() as cur:
            cur.execute(
                "SELECT table_name, COALESCE(data_length + index_length, 0) "
                "FROM information_schema.tables "
                "WHERE table_schema=%s AND table_type='BASE TABLE' "
                "ORDER BY table_name",
                (dbname,),
            )
            rows = cur.fetchall()
    return [{"name": str(row[0]), "size_bytes": int(row[1] or 0)} for row in rows if row and row[0] is not None]


def ensure_mysql_connection(conn: DbConnection) -> dict[str, Any]:
    with connect_mysql(conn) as db:
        with db.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = str(cur.fetchone()[0])
    return {"ok": True, "engine": "mysql", "version": version}


def ensure_sqlite_connection(path: str) -> dict[str, Any]:
    if not path:
        raise ValueError("sqlite_path is required")
    with sqlite3.connect(path) as db:
        db.execute("SELECT 1")
    return {"ok": True, "engine": "sqlite", "path": path}


_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
MAX_FINISHED_JOBS = 200
MAX_JOB_LOG_LINES = 1000
_confirmations: dict[str, dict[str, Any]] = {}
_confirmations_lock = threading.Lock()
CONFIRMATION_TTL_SECONDS = 120
_protected_export_paths: dict[str, int] = {}
_protected_export_paths_lock = threading.Lock()


def _prune_finished_jobs_locked() -> None:
    finished = [job_id for job_id, job in _jobs.items() if job.get("status") != "running"]
    overflow = len(finished) - MAX_FINISHED_JOBS
    if overflow <= 0:
        return
    finished.sort(key=lambda job_id: str(_jobs[job_id].get("created_at", "")))
    for job_id in finished[:overflow]:
        _jobs.pop(job_id, None)


def append_job_log(job_id: str, message: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        logs = job.setdefault("logs", [])
        logs.append(f"[{stamp}] {message}")
        if len(logs) > MAX_JOB_LOG_LINES:
            del logs[: len(logs) - MAX_JOB_LOG_LINES]
    JOB_LOGGER.info("job=%s %s", job_id[:8], message)


def update_job(job_id: str, **updates: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            updates.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
            job.update(updates)


def set_job_progress(job_id: str, progress: int, phase: str | None = None) -> None:
    updates: dict[str, Any] = {"progress": max(0, min(100, int(progress)))}
    if phase is not None:
        updates["phase"] = phase
    update_job(job_id, **updates)


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    public = {k: v for k, v in job.items() if not k.startswith("_")}
    started = float(job.get("_started_monotonic") or time.monotonic())
    stopped = float(job.get("_finished_monotonic") or time.monotonic())
    elapsed = max(0.0, stopped - started)
    progress = int(public.get("progress") or 0)
    public["elapsed_seconds"] = round(elapsed, 1)
    public["eta_seconds"] = None
    if public.get("status") == "running" and 0 < progress < 100:
        public["eta_seconds"] = round((elapsed / progress) * (100 - progress), 1)
    return public


def issue_confirmation(action: str, database: str, table: str = "") -> str:
    action_name = (action or "").strip().lower()
    if action_name not in {"restore-backup", "import-server-csv"}:
        raise ValueError("Unsupported confirmation action")
    database_name = safe_identifier(database, "database")
    table_name = safe_identifier(table, "table") if table else ""
    if action_name == "import-server-csv" and not table_name:
        raise ValueError("table is required for CSV import confirmation")
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _confirmations_lock:
        expired = [key for key, item in _confirmations.items() if float(item["expires_at"]) <= now]
        for key in expired:
            _confirmations.pop(key, None)
        _confirmations[token] = {
            "action": action_name,
            "database": database_name,
            "table": table_name,
            "expires_at": now + CONFIRMATION_TTL_SECONDS,
        }
    return token


def consume_confirmation(token: str, action: str, database: str, table: str = "") -> None:
    with _confirmations_lock:
        item = _confirmations.pop((token or "").strip(), None)
    if not item or float(item["expires_at"]) <= time.monotonic():
        raise HTTPException(400, "Confirmation token is invalid, expired, or already used")
    expected = (action, safe_identifier(database, "database"), safe_identifier(table, "table") if table else "")
    actual = (item.get("action"), item.get("database"), item.get("table"))
    if actual != expected:
        raise HTTPException(400, "Confirmation token does not match this operation")


def _reserve_export_paths(paths: list[str], *, exclusive: bool = False) -> list[str]:
    keys = sorted({os.path.normcase(str(Path(path).resolve())) for path in paths})
    with _protected_export_paths_lock:
        for key in keys:
            current = _protected_export_paths.get(key, 0)
            if current < 0 or (exclusive and current > 0):
                raise HTTPException(409, f"Backup file is being used by another operation: {key}")
        for key in keys:
            if exclusive:
                _protected_export_paths[key] = -1
            else:
                _protected_export_paths[key] = _protected_export_paths.get(key, 0) + 1
    return keys


def _release_export_paths(keys: list[str], *, exclusive: bool = False) -> None:
    with _protected_export_paths_lock:
        for key in keys:
            current = _protected_export_paths.get(key, 0)
            if exclusive or current <= 1:
                _protected_export_paths.pop(key, None)
            else:
                _protected_export_paths[key] = current - 1


def start_job(
    title: str,
    target,
    *args: Any,
    protected_paths: list[str] | None = None,
) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    now = datetime.now().isoformat(timespec="seconds")
    reserved_keys = _reserve_export_paths(protected_paths or [])
    with _jobs_lock:
        cfg = load_config()
        max_running = max(1, int(cfg.get("max_concurrent_jobs") or 2))
        running = sum(1 for job in _jobs.values() if job.get("status") == "running")
        if running >= max_running:
            _release_export_paths(reserved_keys)
            raise HTTPException(429, f"Too many running jobs (limit={max_running})")
        _jobs[job_id] = {
            "id": job_id,
            "title": title,
            "status": "running",
            "progress": 0,
            "phase": "starting",
            "created_at": now,
            "updated_at": now,
            "logs": [],
            "result": None,
            "_started_monotonic": time.monotonic(),
            "_cancel_event": threading.Event(),
        }
        _prune_finished_jobs_locked()

    def runner() -> None:
        try:
            append_job_log(job_id, "Job started")
            result = target(job_id, *args)
            update_job(
                job_id,
                status="done",
                progress=100,
                phase="done",
                result=result,
                _finished_monotonic=time.monotonic(),
            )
            append_job_log(job_id, "Job finished")
        except JobCancelled as exc:
            append_job_log(job_id, f"CANCELLED: {exc}")
            update_job(
                job_id,
                status="cancelled",
                phase="cancelled",
                error=str(exc),
                _finished_monotonic=time.monotonic(),
            )
        except JobFailedWithResult as exc:
            append_job_log(job_id, f"ERROR: {exc}")
            update_job(
                job_id,
                status="failed",
                phase="failed",
                error=str(exc),
                result=exc.result,
                _finished_monotonic=time.monotonic(),
            )
        except Exception as exc:  # noqa: BLE001
            append_job_log(job_id, f"ERROR: {exc}")
            update_job(
                job_id,
                status="failed",
                phase="failed",
                error=str(exc),
                _finished_monotonic=time.monotonic(),
            )
        finally:
            _release_export_paths(reserved_keys)

    try:
        thread = threading.Thread(target=runner, name=f"db-admin-job-{job_id[:8]}", daemon=True)
        thread.start()
    except BaseException:
        _release_export_paths(reserved_keys)
        raise
    return _public_job(_jobs[job_id])


def _job_cancel_event(job_id: str) -> threading.Event | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        event = job.get("_cancel_event") if job else None
    return event if isinstance(event, threading.Event) else None


def _raise_if_cancelled(job_id: str) -> None:
    event = _job_cancel_event(job_id)
    if event and event.is_set():
        raise JobCancelled("Job cancellation was requested")


def _cli_progress_mapper(
    job_id: str,
    *,
    phase: str,
    start: int,
    end: int,
) -> Callable[[float], None]:
    last = [-1]

    def hook(fraction: float) -> None:
        clamped = max(0.0, min(1.0, float(fraction)))
        value = start + int(clamped * (end - start))
        value = max(start, min(end, value))
        if value != last[0]:
            last[0] = value
            set_job_progress(job_id, value, phase)

    return hook


def run_cli(
    cmd: list[str],
    *,
    env: dict[str, str],
    stdin_path: Path | None = None,
    stdout_path: Path | None = None,
    timeout_seconds: int | None = None,
    cancel_event: threading.Event | None = None,
    progress_hook: Callable[[float], None] | None = None,
    progress_total_bytes: int | None = None,
) -> None:
    feed_stdin = stdin_path is not None and progress_hook is not None
    stdin_file = None if feed_stdin else (stdin_path.open("rb") if stdin_path else None)
    stdout_file = stdout_path.open("wb") if stdout_path else subprocess.PIPE
    process: subprocess.Popen[bytes] | None = None
    stop_helpers = threading.Event()
    helper_threads: list[threading.Thread] = []
    feed_error: list[BaseException] = []
    stderr = b""

    def _stdout_monitor() -> None:
        total = max(1, int(progress_total_bytes or 0))
        while not stop_helpers.is_set():
            try:
                size = stdout_path.stat().st_size if stdout_path and stdout_path.is_file() else 0
                if progress_hook:
                    progress_hook(min(0.99, size / total))
            except OSError:
                pass
            stop_helpers.wait(0.5)

    def _stdin_feeder(proc: subprocess.Popen[bytes]) -> None:
        assert stdin_path is not None and progress_hook is not None and proc.stdin is not None
        try:
            total = int(progress_total_bytes or 0)
            if total <= 0:
                try:
                    total = max(1, stdin_path.stat().st_size)
                except OSError:
                    total = 1
            sent = 0
            with stdin_path.open("rb") as src:
                while not stop_helpers.is_set():
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    proc.stdin.write(chunk)
                    sent += len(chunk)
                    progress_hook(min(1.0, sent / total))
            try:
                proc.stdin.close()
            except OSError:
                pass
        except (BrokenPipeError, ValueError):
            # ValueError: "write to closed file" when peer closes stdin early (Windows).
            pass
        except BaseException as exc:  # noqa: BLE001
            feed_error.append(exc)
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass

    def _stop_process(proc: subprocess.Popen[bytes]) -> bytes:
        stop_helpers.set()
        proc.terminate()
        try:
            _out, err = proc.communicate(timeout=10)
            return err or b""
        except subprocess.TimeoutExpired:
            proc.kill()
            _out, err = proc.communicate()
            return err or b""

    def _wait_with_stdin_feeder(proc: subprocess.Popen[bytes], feeder: threading.Thread) -> bytes:
        """Wait without communicate() while feeder owns stdin (communicate closes stdin immediately)."""
        deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
        while True:
            feeder.join(timeout=0.5)
            cancelled = cancel_event is not None and cancel_event.is_set()
            timed_out = deadline is not None and time.monotonic() >= deadline
            if cancelled or timed_out:
                err = _stop_process(proc)
                feeder.join(timeout=5)
                if cancelled:
                    raise JobCancelled("External database command was cancelled")
                raise TimeoutError(f"External database command exceeded timeout_seconds={timeout_seconds}")
            if feeder.is_alive():
                continue
            if proc.poll() is not None:
                break
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                continue
        err = proc.stderr.read() if proc.stderr else b""
        if proc.stderr:
            proc.stderr.close()
        return err or b""

    try:
        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if feed_stdin else stdin_file,
                stdout=stdout_file,
                stderr=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            tool = Path(str(cmd[0])).name if cmd else "mysql"
            logical = (
                "mysqldump"
                if "dump" in tool.lower()
                else ("mysql" if "mysql" in tool.lower() or "mariadb" in tool.lower() else tool)
            )
            raise FileNotFoundError(_tool_not_found_message(logical)) from exc

        if feed_stdin:
            feeder = threading.Thread(target=_stdin_feeder, args=(process,), daemon=True)
            helper_threads.append(feeder)
            feeder.start()
            stderr = _wait_with_stdin_feeder(process, feeder)
        else:
            if stdout_path is not None and progress_hook is not None and int(progress_total_bytes or 0) > 0:
                monitor = threading.Thread(target=_stdout_monitor, daemon=True)
                helper_threads.append(monitor)
                monitor.start()
            deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
            while True:
                try:
                    _, stderr = process.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    cancelled = cancel_event is not None and cancel_event.is_set()
                    timed_out = deadline is not None and time.monotonic() >= deadline
                    if not cancelled and not timed_out:
                        continue
                    stderr = _stop_process(process)
                    if cancelled:
                        raise JobCancelled("External database command was cancelled")
                    raise TimeoutError(f"External database command exceeded timeout_seconds={timeout_seconds}")
    finally:
        stop_helpers.set()
        for thread in helper_threads:
            thread.join(timeout=5)
        if stdin_file:
            stdin_file.close()
        if stdout_path and stdout_file:
            stdout_file.close()
    if feed_error:
        raise feed_error[0]
    if process is None:
        raise RuntimeError("External database command failed to start")
    if progress_hook is not None:
        progress_hook(1.0)
    if process.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or f"Command failed with code {process.returncode}")


def _sha256_file(
    path: Path,
    chunk_size: int = 8 * 1024 * 1024,
    progress_hook: Callable[[float], None] | None = None,
) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size if path.is_file() else 0
    done = 0
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)
            done += len(chunk)
            if progress_hook and total > 0:
                progress_hook(min(1.0, done / total))
    if progress_hook:
        progress_hook(1.0)
    return digest.hexdigest()


def _database_storage_bytes(conn: DbConnection, database: str) -> int:
    with connect_mysql(conn) as db:
        with db.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(data_length + index_length), 0) "
                "FROM information_schema.tables WHERE table_schema=%s",
                (database,),
            )
            return int(cur.fetchone()[0] or 0)


def _table_storage_bytes(conn: DbConnection, database: str, table: str) -> int:
    with connect_mysql(conn) as db:
        with db.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(data_length + index_length, 0) "
                "FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name=%s AND table_type='BASE TABLE'",
                (database, table),
            )
            row = cur.fetchone()
            return int((row[0] if row else 0) or 0)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".partial")
    try:
        with temp.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _run_mysqldump_backup(
    job_id: str,
    conn: DbConnection,
    *,
    database: str,
    table: str | None,
    output_dir: str,
    estimated_bytes: int,
    out: Path,
    manifest_extra: dict[str, Any],
) -> dict[str, Any]:
    conn = managed_connection(conn)
    partial = out.with_suffix(out.suffix + ".partial")
    reserve_factor = float(load_config().get("backup_free_space_factor") or 1.5)
    required_bytes = max(64 * 1024 * 1024, int(estimated_bytes * reserve_factor))
    free_bytes = shutil.disk_usage(out.parent).free
    if free_bytes < required_bytes:
        raise RuntimeError(
            f"Insufficient disk space: free={free_bytes}, required={required_bytes}, estimated={estimated_bytes}"
        )
    dump_tool = resolve_mysql_tool("mysqldump")
    target_label = f"{database}.{table}" if table else database
    append_job_log(job_id, f"Running mysqldump for {target_label}")
    append_job_log(job_id, f"Estimated bytes: {estimated_bytes}; free bytes: {free_bytes}")
    set_job_progress(job_id, 5, "dumping")
    env = os.environ.copy()
    env["MYSQL_PWD"] = connection_password(conn)
    cmd = [
        dump_tool,
        *mysql_client_plugin_args(dump_tool),
        "--host",
        conn.host or "127.0.0.1",
        "--port",
        str(int(conn.port or 3306)),
        "--user",
        conn.username or "",
        "--default-character-set=utf8mb4",
        "--single-transaction",
        "--routines",
        "--events",
        "--triggers",
        "--add-drop-table",
    ]
    if not mysql_dump_client_is_mariadb(dump_tool):
        cmd.append("--column-statistics=0")
    cmd.append(database)
    if table:
        cmd.append(table)
    timeout_seconds = max(60, int(load_config().get("cli_timeout_seconds") or 86400))
    started_at = datetime.now().isoformat(timespec="seconds")
    dump_total = max(estimated_bytes, 1)
    try:
        run_cli(
            cmd,
            env=env,
            stdout_path=partial,
            timeout_seconds=timeout_seconds,
            cancel_event=_job_cancel_event(job_id),
            progress_hook=_cli_progress_mapper(job_id, phase="dumping", start=5, end=90),
            progress_total_bytes=dump_total,
        )
        if not partial.is_file() or partial.stat().st_size == 0:
            raise RuntimeError("mysqldump produced an empty backup")
        set_job_progress(job_id, 90, "checksumming")
        sha256 = _sha256_file(
            partial,
            progress_hook=_cli_progress_mapper(job_id, phase="checksumming", start=90, end=95),
        )
        size_bytes = partial.stat().st_size
        partial.replace(out)
        manifest = {
            "format_version": 1,
            "status": "complete",
            "database": database,
            "filename": out.name,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "estimated_bytes": estimated_bytes,
            "started_at": started_at,
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            **manifest_extra,
        }
        manifest_path = out.with_suffix(out.suffix + ".manifest.json")
        _atomic_json(manifest_path, manifest)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    persist_last_output_dir(out.parent)
    append_job_log(job_id, f"Backup file: {out}")
    return {
        "filename": out.name,
        "path": str(out),
        "size_bytes": size_bytes,
        "sha256": sha256,
        "manifest": manifest_path.name,
        "download_url": f"/api/download/{out.name}",
        "scope": manifest.get("scope"),
        "table": manifest.get("table"),
    }


def backup_mysql_job(job_id: str, conn: DbConnection, database: str, output_dir: str = "") -> dict[str, Any]:
    dbname = safe_identifier(database, "database")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out = resolve_output_dir(output_dir) / f"{dbname}_backup_{stamp}.sql"
    estimated_bytes = _database_storage_bytes(conn, dbname)
    return _run_mysqldump_backup(
        job_id,
        conn,
        database=dbname,
        table=None,
        output_dir=output_dir,
        estimated_bytes=estimated_bytes,
        out=out,
        manifest_extra={"scope": "database", "estimated_database_bytes": estimated_bytes},
    )


def backup_mysql_table_job(
    job_id: str, conn: DbConnection, database: str, table: str, output_dir: str = ""
) -> dict[str, Any]:
    dbname = safe_identifier(database, "database")
    table_name = safe_identifier(table, "table")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out = resolve_output_dir(output_dir) / f"{dbname}_{table_name}_backup_{stamp}.sql"
    estimated_bytes = _table_storage_bytes(conn, dbname, table_name)
    return _run_mysqldump_backup(
        job_id,
        conn,
        database=dbname,
        table=table_name,
        output_dir=output_dir,
        estimated_bytes=estimated_bytes,
        out=out,
        manifest_extra={"scope": "table", "table": table_name},
    )


def _find_export_file(filename: str, *, suffixes: set[str]) -> Path | None:
    safe = Path(filename).name
    if safe != filename or Path(safe).suffix.lower() not in suffixes:
        return None
    best: Path | None = None
    best_mtime = -1.0
    for root in backup_roots():
        candidate = root / safe
        if not candidate.is_file():
            continue
        mtime = candidate.stat().st_mtime
        if mtime >= best_mtime:
            best = candidate
            best_mtime = mtime
    return best


def _completed_backup(filename: str) -> tuple[Path, dict[str, Any]]:
    safe = Path(filename).name
    if safe != filename or Path(safe).suffix.lower() != ".sql":
        raise ValueError("Invalid backup filename")
    path = _find_export_file(safe, suffixes={".sql"})
    if path is None:
        raise FileNotFoundError("Completed backup or manifest not found")
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    if not path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Completed backup or manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("status") != "complete" or manifest.get("filename") != safe:
        raise ValueError("Backup manifest is invalid or incomplete")
    if int(manifest.get("size_bytes") or -1) != path.stat().st_size:
        raise ValueError("Backup size does not match manifest")
    return path, manifest


def _completed_export_path(value: str | os.PathLike[str]) -> tuple[Path, dict[str, Any]]:
    if ".." in Path(value).parts:
        raise FilesystemBrowserError("Parent path segments are not allowed")
    browser = FilesystemBrowser(backup_roots())
    path, _root = browser.authorize(value)
    if not path.is_file() or path.suffix.lower() not in {".sql", ".csv"}:
        raise ValueError("Managed backup file must be a completed SQL or CSV export")
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Completed manifest not found: {path.name}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("status") != "complete":
        raise ValueError(f"Export manifest is invalid or incomplete: {path.name}")
    if manifest.get("filename") != path.name:
        raise ValueError(f"Export manifest filename does not match: {path.name}")
    if int(manifest.get("size_bytes") or -1) != path.stat().st_size:
        raise ValueError(f"Export size does not match manifest: {path.name}")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(manifest.get("sha256") or "")):
        raise ValueError(f"Export manifest SHA-256 is invalid: {path.name}")
    return path, manifest


def _prepare_managed_export_paths(values: list[str]) -> list[tuple[Path, dict[str, Any]]]:
    if not values:
        raise ValueError("Select at least one completed SQL or CSV file")
    if len(values) > 500:
        raise ValueError("At most 500 files can be managed at once")
    prepared: list[tuple[Path, dict[str, Any]]] = []
    seen: set[str] = set()
    for value in values:
        path, manifest = _completed_export_path(value)
        key = os.path.normcase(str(path))
        if key in seen:
            raise ValueError(f"Duplicate managed file path: {path}")
        seen.add(key)
        prepared.append((path, manifest))
    return prepared


def backup_file_roots() -> dict[str, Any]:
    return {"roots": FilesystemBrowser(backup_roots()).public_roots()}


def backup_file_entries(path: str) -> dict[str, Any]:
    if ".." in Path(path).parts:
        raise FilesystemBrowserError("Parent path segments are not allowed")
    browser = FilesystemBrowser(backup_roots())
    listing = browser.entries(path, extensions={".sql", ".csv"}, include_files=True)
    entries: list[dict[str, Any]] = []
    for entry in listing["entries"]:
        if entry["type"] == "directory":
            entries.append(entry)
            continue
        try:
            managed_path, manifest = _completed_export_path(str(entry["path"]))
        except (FilesystemBrowserError, FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            continue
        entries.append(
            {
                **entry,
                "path": str(managed_path),
                "kind": managed_path.suffix.lower().removeprefix("."),
                "sha256": str(manifest.get("sha256") or ""),
                "completed_at": str(manifest.get("completed_at") or entry.get("modified_at") or ""),
                "scope": str(manifest.get("scope") or ""),
                "database": str(manifest.get("database") or ""),
                "table": str(manifest.get("table") or ""),
            }
        )
    return {**listing, "entries": entries}


def _managed_path_signature(prepared: list[tuple[Path, dict[str, Any]]]) -> tuple[str, ...]:
    return tuple(sorted(os.path.normcase(str(path)) for path, _manifest in prepared))


def issue_delete_backup_files_confirmation(paths: list[str]) -> str:
    prepared = _prepare_managed_export_paths(paths)
    signature = _managed_path_signature(prepared)
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _confirmations_lock:
        expired = [key for key, item in _confirmations.items() if float(item["expires_at"]) <= now]
        for key in expired:
            _confirmations.pop(key, None)
        _confirmations[token] = {
            "action": "delete-backup-files",
            "paths": signature,
            "expires_at": now + CONFIRMATION_TTL_SECONDS,
        }
    return token


def consume_delete_backup_files_confirmation(token: str, prepared: list[tuple[Path, dict[str, Any]]]) -> None:
    with _confirmations_lock:
        item = _confirmations.pop((token or "").strip(), None)
    if not item or float(item["expires_at"]) <= time.monotonic():
        raise HTTPException(400, "Deletion confirmation token is invalid, expired, or already used")
    expected = ("delete-backup-files", _managed_path_signature(prepared))
    actual = (item.get("action"), tuple(item.get("paths") or ()))
    if actual != expected:
        raise HTTPException(400, "Deletion confirmation token does not match the selected files")


def delete_backup_files(paths: list[str], confirmation_token: str) -> dict[str, Any]:
    prepared = _prepare_managed_export_paths(paths)
    consume_delete_backup_files_confirmation(confirmation_token, prepared)
    reserved_keys = _reserve_export_paths([str(path) for path, _manifest in prepared], exclusive=True)
    renamed: list[tuple[Path, Path]] = []
    deletion_id = uuid.uuid4().hex[:12]
    try:
        prepared = _prepare_managed_export_paths([str(path) for path, _manifest in prepared])
        for path, _manifest in prepared:
            manifest_path = path.with_suffix(path.suffix + ".manifest.json")
            for original in (path, manifest_path):
                tombstone = original.with_name(f".{original.name}.delete-{deletion_id}")
                if tombstone.exists():
                    raise RuntimeError(f"Temporary deletion target already exists: {tombstone.name}")
                original.replace(tombstone)
                renamed.append((original, tombstone))
    except BaseException:
        for original, tombstone in reversed(renamed):
            if tombstone.exists() and not original.exists():
                try:
                    tombstone.replace(original)
                except OSError:
                    pass
        raise
    finally:
        if len(renamed) != len(prepared) * 2:
            _release_export_paths(reserved_keys, exclusive=True)

    failed_cleanup: list[dict[str, str]] = []
    try:
        for _original, tombstone in renamed:
            try:
                tombstone.unlink()
            except OSError as exc:
                failed_cleanup.append({"path": str(tombstone), "error": str(exc)})
    finally:
        _release_export_paths(reserved_keys, exclusive=True)
    if failed_cleanup:
        raise RuntimeError(f"Files were removed from the catalog but temporary cleanup failed: {failed_cleanup}")
    return {
        "deleted": [str(path) for path, _manifest in prepared],
        "count": len(prepared),
        "size_bytes": sum(int(manifest.get("size_bytes") or 0) for _path, manifest in prepared),
    }


def _removable_destination(value: str) -> tuple[Path, Path]:
    browser = FilesystemBrowser(windows_removable_roots())
    destination, drive_root = browser.authorize(value)
    if not drive_root.is_dir():
        raise FilesystemBrowserError("The removable drive is no longer available")
    destination.mkdir(parents=True, exist_ok=True)
    destination, current_root = FilesystemBrowser(windows_removable_roots()).authorize(destination)
    if current_root != drive_root or not destination.is_dir():
        raise FilesystemBrowserError("The destination is no longer on the selected removable drive")
    return destination, current_root


def _copy_job_file(
    job_id: str,
    source: Path,
    temporary: Path,
    *,
    completed_bytes: int,
    total_bytes: int,
    phase: str,
) -> int:
    temporary.unlink(missing_ok=True)
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, temporary.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            _raise_if_cancelled(job_id)
            writer.write(chunk)
            completed_bytes += len(chunk)
            set_job_progress(
                job_id,
                max(1, min(96, int(completed_bytes / max(total_bytes, 1) * 96))),
                phase,
            )
        writer.flush()
        os.fsync(writer.fileno())
    if temporary.stat().st_size != source.stat().st_size:
        raise RuntimeError(f"Copied size does not match source: {source.name}")
    return completed_bytes


def copy_backup_files_job(job_id: str, paths: list[str], destination_dir: str) -> dict[str, Any]:
    selected = _prepare_managed_export_paths(paths)
    destination, drive_root = _removable_destination(destination_dir)
    append_job_log(job_id, f"Preflight destination: {destination}")
    prepared: list[dict[str, Any]] = []
    total_bytes = 0
    required_free_bytes = 0
    target_names: set[str] = set()
    for index, (source, manifest) in enumerate(selected, start=1):
        _raise_if_cancelled(job_id)
        set_job_progress(job_id, 0, f"preflight {index}/{len(selected)}")
        target_key = os.path.normcase(source.name)
        if target_key in target_names:
            raise RuntimeError(f"Multiple selected files have the same destination name: {source.name}")
        target_names.add(target_key)
        source_manifest = source.with_suffix(source.suffix + ".manifest.json")
        target = destination / source.name
        target_manifest = target.with_suffix(target.suffix + ".manifest.json")
        source_bytes = source.stat().st_size
        manifest_bytes = source_manifest.stat().st_size
        total_bytes += source_bytes + manifest_bytes
        skip_sql = False
        if target.exists():
            if not target.is_file():
                raise RuntimeError(f"Destination name is not a file: {target.name}")
            append_job_log(job_id, f"Checking existing file: {target.name}")
            target_sha256 = _sha256_file(
                target,
                progress_hook=lambda _value: _raise_if_cancelled(job_id),
            )
            if not secrets.compare_digest(target_sha256, str(manifest.get("sha256") or "")):
                raise RuntimeError(f"A different backup with the same name already exists: {target.name}")
            skip_sql = True
            required_free_bytes += manifest_bytes
        else:
            required_free_bytes += source_bytes + manifest_bytes
        prepared.append(
            {
                "source": source,
                "source_manifest": source_manifest,
                "target": target,
                "target_manifest": target_manifest,
                "manifest": manifest,
                "skip_sql": skip_sql,
            }
        )

    try:
        free_bytes = int(shutil.disk_usage(drive_root).free)
    except OSError as exc:
        raise RuntimeError(f"Unable to read removable drive free space: {exc}") from exc
    if required_free_bytes > free_bytes:
        raise RuntimeError(
            f"Removable drive free space is insufficient: need {required_free_bytes} bytes, available {free_bytes} bytes"
        )

    copied: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []
    verified: list[str] = []
    completed_bytes = 0
    for index, item in enumerate(prepared, start=1):
        _raise_if_cancelled(job_id)
        source = item["source"]
        target = item["target"]
        target_manifest = item["target_manifest"]
        sql_temp = target.with_name(f".{target.name}.{job_id[:8]}.tmp")
        manifest_temp = target_manifest.with_name(f".{target_manifest.name}.{job_id[:8]}.tmp")
        committed_sql = False
        committed_manifest = False
        was_skipped = bool(item["skip_sql"])
        append_job_log(job_id, f"{index}/{len(prepared)} Copying {source.name}")
        try:
            if was_skipped:
                completed_bytes += source.stat().st_size
            else:
                completed_bytes = _copy_job_file(
                    job_id,
                    source,
                    sql_temp,
                    completed_bytes=completed_bytes,
                    total_bytes=total_bytes,
                    phase=f"copying {index}/{len(prepared)} {source.name}",
                )
                set_job_progress(job_id, min(98, int(completed_bytes / max(total_bytes, 1) * 96)), "verifying")
                actual_sha256 = _sha256_file(
                    sql_temp,
                    progress_hook=lambda _value: _raise_if_cancelled(job_id),
                )
                if not secrets.compare_digest(actual_sha256, str(item["manifest"].get("sha256") or "")):
                    raise RuntimeError(f"SHA-256 verification failed after copying: {source.name}")
                sql_temp.replace(target)
                committed_sql = True

            completed_bytes = _copy_job_file(
                job_id,
                item["source_manifest"],
                manifest_temp,
                completed_bytes=completed_bytes,
                total_bytes=total_bytes,
                phase=f"finalizing {index}/{len(prepared)} {source.name}",
            )
            _raise_if_cancelled(job_id)
            manifest_temp.replace(target_manifest)
            committed_manifest = True
            if was_skipped:
                skipped.append(source.name)
            else:
                copied.append(source.name)
            verified.append(source.name)
            append_job_log(job_id, f"Verified: {target}")
        except BaseException as exc:
            sql_temp.unlink(missing_ok=True)
            manifest_temp.unlink(missing_ok=True)
            if committed_sql and not committed_manifest:
                target.unlink(missing_ok=True)
            if isinstance(exc, JobCancelled):
                raise
            failed.append({"filename": source.name, "error": str(exc)})
            raise JobFailedWithResult(
                str(exc),
                {
                    "destination": str(destination),
                    "copied": copied,
                    "skipped": skipped,
                    "failed": failed,
                    "verified": verified,
                    "total_bytes": total_bytes,
                },
            ) from exc

    set_job_progress(job_id, 99, "finalizing")
    return {
        "destination": str(destination),
        "copied": copied,
        "skipped": skipped,
        "failed": failed,
        "verified": verified,
        "total_bytes": total_bytes,
    }


def copy_backups_job(job_id: str, filenames: list[str], destination_dir: str) -> dict[str, Any]:
    safe_names: list[str] = []
    seen: set[str] = set()
    for filename in filenames:
        safe = Path(str(filename)).name
        if safe != filename or Path(safe).suffix.lower() != ".sql":
            raise ValueError(f"Invalid backup filename: {filename}")
        key = os.path.normcase(safe)
        if key in seen:
            raise ValueError(f"Duplicate backup filename: {safe}")
        seen.add(key)
        safe_names.append(safe)
    paths: list[str] = []
    for safe in safe_names:
        source, _manifest = _completed_backup(safe)
        paths.append(str(source))
    return copy_backup_files_job(job_id, paths, destination_dir)


def _completed_csv_export(filename: str) -> tuple[Path, dict[str, Any]]:
    safe = Path(filename).name
    if safe != filename or Path(safe).suffix.lower() != ".csv":
        raise ValueError("Invalid CSV filename")
    path = _find_export_file(safe, suffixes={".csv"})
    if path is None:
        raise FileNotFoundError("Completed CSV export or manifest not found")
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    if not path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Completed CSV export or manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("status") != "complete" or manifest.get("filename") != safe:
        raise ValueError("CSV manifest is invalid or incomplete")
    if int(manifest.get("size_bytes") or -1) != path.stat().st_size:
        raise ValueError("CSV size does not match manifest")
    return path, manifest


def restore_verified_backup_job(job_id: str, conn: DbConnection, database: str, filename: str) -> dict[str, Any]:
    conn = managed_connection(conn)
    source, manifest = _completed_backup(filename)
    append_job_log(job_id, f"Verifying SHA-256 for {source.name}")
    set_job_progress(job_id, 2, "verifying")
    actual_sha256 = _sha256_file(
        source,
        progress_hook=_cli_progress_mapper(job_id, phase="verifying", start=2, end=8),
    )
    if not secrets.compare_digest(actual_sha256, str(manifest.get("sha256") or "")):
        raise RuntimeError("Backup SHA-256 does not match manifest")
    dbname = safe_identifier(database, "database")
    append_job_log(job_id, f"Restoring verified backup into database {dbname}")
    set_job_progress(job_id, 8, "restoring")
    env = os.environ.copy()
    env["MYSQL_PWD"] = connection_password(conn)
    mysql_tool_path = resolve_mysql_tool("mysql")
    cmd = [
        mysql_tool_path,
        *mysql_client_plugin_args(mysql_tool_path),
        "--host",
        conn.host or "127.0.0.1",
        "--port",
        str(int(conn.port or 3306)),
        "--user",
        conn.username or "",
        "--default-character-set=utf8mb4",
        dbname,
    ]
    timeout_seconds = max(60, int(load_config().get("cli_timeout_seconds") or 86400))
    run_cli(
        cmd,
        env=env,
        stdin_path=source,
        timeout_seconds=timeout_seconds,
        cancel_event=_job_cancel_event(job_id),
        progress_hook=_cli_progress_mapper(job_id, phase="restoring", start=8, end=95),
        progress_total_bytes=source.stat().st_size,
    )
    set_job_progress(job_id, 95, "finalizing")
    return {"source": source.name, "database": dbname, "sha256": actual_sha256, "scope": manifest.get("scope")}


def export_csv_job(job_id: str, conn: DbConnection, database: str, table: str, output_dir: str = "") -> dict[str, Any]:
    conn = managed_connection(conn)
    dbname = safe_identifier(database, "database")
    table_name = safe_identifier(table, "table")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = resolve_output_dir(output_dir) / f"{dbname}_{table_name}_{stamp}.csv"
    append_job_log(job_id, f"Exporting {dbname}.{table_name} to CSV")
    rows = 0
    total_rows = 0
    with connect_mysql(conn, dbname) as count_db:
        with count_db.cursor() as count_cur:
            count_cur.execute(f"SELECT COUNT(*) FROM {quote_ident(table_name)}")
            total_rows = int(count_cur.fetchone()[0] or 0)
    set_job_progress(job_id, 5 if total_rows else 90, "exporting")
    next_progress = 7
    with pymysql.connect(
        host=conn.host or "127.0.0.1",
        port=int(conn.port or 3306),
        user=conn.username or "",
        password=connection_password(conn),
        database=dbname,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.SSCursor,
    ) as db:
        with db.cursor() as cur, out.open("w", encoding="utf-8-sig", newline="") as f:
            cur.execute(f"SELECT * FROM {quote_ident(table_name)}")
            columns = [item[0] for item in cur.description or []]
            writer = csv.writer(f)
            writer.writerow(columns)
            while True:
                _raise_if_cancelled(job_id)
                batch = cur.fetchmany(1000)
                if not batch:
                    break
                writer.writerows(batch)
                rows += len(batch)
                if total_rows:
                    progress = 5 + int((rows / total_rows) * 90)
                    if progress >= next_progress:
                        set_job_progress(job_id, progress, "exporting")
                        next_progress = progress + 2
    set_job_progress(job_id, 90, "checksumming")
    sha256 = _sha256_file(out, progress_hook=_cli_progress_mapper(job_id, phase="checksumming", start=90, end=95))
    size_bytes = out.stat().st_size
    manifest = {
        "format_version": 1,
        "status": "complete",
        "kind": "csv",
        "database": dbname,
        "table": table_name,
        "filename": out.name,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "rows": rows,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
    }
    manifest_path = out.with_suffix(out.suffix + ".manifest.json")
    _atomic_json(manifest_path, manifest)
    persist_last_output_dir(out.parent)
    set_job_progress(job_id, 95, "finalizing")
    append_job_log(job_id, f"CSV file: {out}")
    return {
        "filename": out.name,
        "path": str(out),
        "rows": rows,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "manifest": manifest_path.name,
        "download_url": f"/api/download/{out.name}",
    }


def _resolve_csv_import_columns(
    csv_columns: list[str],
    table_columns: list[str],
    *,
    force: bool,
) -> tuple[list[str], list[str], list[str]]:
    """Return (import_columns, skipped_csv_columns, missing_table_columns)."""
    csv_list = [str(c) for c in csv_columns]
    table_list = [str(c) for c in table_columns]
    table_set = set(table_list)
    csv_set = set(csv_list)
    skipped = [c for c in csv_list if c not in table_set]
    missing = [c for c in table_list if c not in csv_set]
    if not force:
        if skipped or missing:
            raise ValueError(
                "CSV columns do not match table columns. "
                f"extra_in_csv={skipped}; missing_in_csv={missing}. "
                "Enable force import to allow mismatched fields (requires truncate)."
            )
        return csv_list, [], []
    import_cols = [c for c in csv_list if c in table_set]
    if not import_cols:
        raise ValueError("Force import found no overlapping columns between CSV and table")
    return import_cols, skipped, missing


def import_csv_job(
    job_id: str,
    conn: DbConnection,
    database: str,
    table: str,
    csv_path: str,
    truncate: bool,
    *,
    force: bool = False,
    verify_sha256: str | None = None,
) -> dict[str, Any]:
    dbname = safe_identifier(database, "database")
    table_name = safe_identifier(table, "table")
    source = Path(csv_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if force:
        truncate = True
    if verify_sha256:
        append_job_log(job_id, f"Verifying SHA-256 for {source.name}")
        set_job_progress(job_id, 2, "verifying")
        actual = _sha256_file(source, progress_hook=_cli_progress_mapper(job_id, phase="verifying", start=2, end=5))
        if not secrets.compare_digest(actual, verify_sha256):
            raise RuntimeError("CSV SHA-256 does not match manifest")
    mode = "force" if force else "strict"
    append_job_log(job_id, f"Importing CSV into {dbname}.{table_name} ({mode})")
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as counter:
            total_rows = max(sum(1 for _ in counter) - 1, 0)
    except OSError:
        total_rows = 0
    set_job_progress(job_id, 5 if total_rows else 50, "importing")
    next_progress = 7
    rows = 0
    with connect_mysql(conn, dbname, autocommit=False) as db:
        with db.cursor() as cur, source.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            csv_columns = list(reader.fieldnames or [])
            if not csv_columns:
                raise ValueError("CSV header is empty")
            for col in csv_columns:
                safe_identifier(col, "csv column")
            cur.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)}")
            table_columns = [str(row[0]) for row in cur.fetchall() if row and row[0] is not None]
            columns, skipped, missing = _resolve_csv_import_columns(csv_columns, table_columns, force=force)
            if force:
                if skipped:
                    append_job_log(job_id, f"Force import skipping CSV columns: {skipped}")
                if missing:
                    append_job_log(job_id, f"Force import leaving table columns unset: {missing}")
            if truncate:
                append_job_log(job_id, "Deleting target rows transactionally before import")
                cur.execute(f"DELETE FROM {quote_ident(table_name)}")
            cols = ", ".join(quote_ident(c) for c in columns)
            placeholders = ", ".join(["%s"] * len(columns))
            sql = f"INSERT INTO {quote_ident(table_name)} ({cols}) VALUES ({placeholders})"
            batch: list[tuple[Any, ...]] = []
            for row in reader:
                _raise_if_cancelled(job_id)
                batch.append(tuple(row.get(c, "") for c in columns))
                if len(batch) >= 500:
                    cur.executemany(sql, batch)
                    rows += len(batch)
                    batch.clear()
                    if total_rows:
                        progress = 5 + int((rows / total_rows) * 90)
                        if progress >= next_progress:
                            set_job_progress(job_id, progress, "importing")
                            next_progress = progress + 2
            if batch:
                cur.executemany(sql, batch)
                rows += len(batch)
        db.commit()
    set_job_progress(job_id, 95, "finalizing")
    append_job_log(job_id, f"Imported rows: {rows}")
    return {
        "database": dbname,
        "table": table_name,
        "rows": rows,
        "source": source.name,
        "columns": columns,
        "force": force,
    }


def import_verified_csv_job(
    job_id: str,
    conn: DbConnection,
    database: str,
    table: str,
    filename: str,
    truncate: bool,
    force: bool = False,
) -> dict[str, Any]:
    source, manifest = _completed_csv_export(filename)
    return import_csv_job(
        job_id,
        conn,
        database,
        table,
        str(source),
        truncate,
        force=force,
        verify_sha256=str(manifest.get("sha256") or ""),
    )


def register_local_export_file(source_path: str) -> dict[str, Any]:
    source = Path(source_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"File not found: {source}")
    suffix = source.suffix.lower()
    if suffix not in {".sql", ".csv"}:
        raise ValueError("Only .sql and .csv files can be registered")
    safe_name = source.name
    if Path(safe_name).name != safe_name:
        raise ValueError("Invalid filename")
    target_dir = backup_dir()
    target = target_dir / safe_name
    if target.resolve() != source:
        counter = 1
        while target.exists():
            stem = source.stem
            target = target_dir / f"{stem}_{counter}{suffix}"
            counter += 1
        shutil.copy2(source, target)
    sha256 = _sha256_file(target)
    size_bytes = target.stat().st_size
    completed_at = datetime.now().isoformat(timespec="seconds")
    if suffix == ".sql":
        manifest = {
            "format_version": 1,
            "status": "complete",
            "scope": "external",
            "filename": target.name,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "completed_at": completed_at,
        }
    else:
        manifest = {
            "format_version": 1,
            "status": "complete",
            "kind": "csv",
            "scope": "external",
            "filename": target.name,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "completed_at": completed_at,
        }
    manifest_path = target.with_suffix(target.suffix + ".manifest.json")
    _atomic_json(manifest_path, manifest)
    return {
        "filename": target.name,
        "path": str(target),
        "size_bytes": size_bytes,
        "sha256": sha256,
        "kind": "sql" if suffix == ".sql" else "csv",
        "manifest": manifest_path.name,
    }


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    # Uvicorn may reconfigure logging at startup; re-apply after the app is ready.
    configure_console_logging()
    yield


configure_console_logging()
app = FastAPI(title="SD SMA DB Admin", version="0.1.0", lifespan=_app_lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")


@app.middleware("http")
async def enforce_remote_token(request: Request, call_next):
    if request.url.path in AUTH_EXEMPT_PATHS:
        return await call_next(request)
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            origin_host = parsed.netloc.lower()
            request_host = (request.headers.get("host") or "").lower()
            if parsed.scheme not in {"http", "https"} or origin_host != request_host:
                return JSONResponse(status_code=403, content={"detail": "Cross-origin state-changing request is forbidden"})
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
def root() -> FileResponse:
    return FileResponse(BASE_DIR / "app" / "static" / "admin.html")


@app.get("/admin")
def admin_page() -> FileResponse:
    return FileResponse(BASE_DIR / "app" / "static" / "admin.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    cfg = load_config()
    connection = default_connection()
    remembered = last_output_dir()
    return {
        "default_connection": connection,
        "backup_dir": str(backup_dir()),
        "last_output_dir": str(remembered) if remembered else "",
        "mysql_tools": cfg.get("mysql_tools") or {},
        "max_concurrent_jobs": max(1, int(cfg.get("max_concurrent_jobs") or 2)),
    }


@app.post("/api/config/connection")
def save_connection_config(conn: DbConnection) -> dict[str, Any]:
    saved = persist_default_connection(conn)
    return {"ok": True, "default_connection": saved}


@app.get("/api/filesystem/roots")
def filesystem_roots(purpose: str = "directory") -> dict[str, Any]:
    try:
        browser, _, _ = filesystem_browser(purpose)
        return {"roots": browser.public_roots()}
    except FilesystemBrowserError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/filesystem/entries")
def filesystem_entries(path: str, purpose: str = "directory") -> dict[str, Any]:
    try:
        browser, extensions, include_files = filesystem_browser(purpose)
        return browser.entries(path, extensions=extensions, include_files=include_files)
    except FilesystemBrowserError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/backup-files/roots")
def get_backup_file_roots() -> dict[str, Any]:
    return backup_file_roots()


@app.get("/api/backup-files/entries")
def get_backup_file_entries(path: str) -> dict[str, Any]:
    try:
        return backup_file_entries(path)
    except (FilesystemBrowserError, OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/register-file")
def register_file(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        kind = str(payload.get("kind") or "sql").strip().lower()
        browser, extensions, _ = filesystem_browser(kind)
        selected, _ = browser.authorize(str(payload.get("path") or ""))
        if not selected.is_file() or selected.suffix.lower() not in extensions:
            raise FilesystemBrowserError(f"Selected file must be {kind.upper()}")
        registered = register_local_export_file(str(selected))
        return {"selected": registered["path"], **registered}
    except (FilesystemBrowserError, OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/connect/test")
def test_connection(conn: DbConnection) -> dict[str, Any]:
    try:
        if (conn.engine or "mysql").lower() == "sqlite":
            return ensure_sqlite_connection(conn.sqlite_path)
        return ensure_mysql_connection(conn)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)}


@app.post("/api/confirmations")
def create_confirmation(req: ConfirmationRequest) -> dict[str, Any]:
    try:
        token = issue_confirmation(req.action, req.database, req.table)
        return {"token": token, "expires_in_seconds": CONFIRMATION_TTL_SECONDS}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/databases")
def databases(conn: DbConnection) -> dict[str, Any]:
    try:
        if (conn.engine or "mysql").lower() != "mysql":
            raise ValueError("Database list currently supports MySQL only")
        return {"databases": list_mysql_databases(conn)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/tables")
def tables(req: TableRequest) -> dict[str, Any]:
    try:
        return {"tables": list_mysql_tables(req.connection, req.database)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/backup")
def create_backup(req: BackupRequest) -> dict[str, Any]:
    job = start_job(f"Backup {req.database}", backup_mysql_job, req.connection, req.database, req.output_dir)
    return {"job": job}


@app.post("/api/backup-table")
def create_table_backup(req: BackupTableRequest) -> dict[str, Any]:
    job = start_job(
        f"Backup table {req.database}.{req.table}",
        backup_mysql_table_job,
        req.connection,
        req.database,
        req.table,
        req.output_dir,
    )
    return {"job": job}


@app.post("/api/export-csv")
def export_csv(req: CsvExportRequest) -> dict[str, Any]:
    job = start_job(f"Export CSV {req.table}", export_csv_job, req.connection, req.database, req.table, req.output_dir)
    return {"job": job}


@app.get("/api/backups")
def list_backups() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in backup_roots():
        for manifest_path in root.glob("*.sql.manifest.json"):
            try:
                filename = manifest_path.name.removesuffix(".manifest.json")
                if filename in seen:
                    continue
                _, manifest = _completed_backup(filename)
                seen.add(filename)
                items.append(
                    {
                        "filename": filename,
                        "size_bytes": int(manifest["size_bytes"]),
                        "sha256": str(manifest["sha256"]),
                        "completed_at": str(manifest.get("completed_at") or ""),
                        "scope": str(manifest.get("scope") or "database"),
                        "database": str(manifest.get("database") or ""),
                        "table": str(manifest.get("table") or ""),
                    }
                )
            except (OSError, ValueError, json.JSONDecodeError, FileNotFoundError):
                continue
    items.sort(key=lambda item: item["completed_at"], reverse=True)
    return {"backups": items}


@app.post("/api/copy-backups")
def copy_backups(req: CopyBackupsRequest) -> dict[str, Any]:
    sources = [
        str(path)
        for filename in req.filenames
        if (path := _find_export_file(filename, suffixes={".sql"})) is not None
    ]
    job = start_job(
        f"Copy {len(req.filenames)} backup(s) to removable drive",
        copy_backups_job,
        req.filenames,
        req.destination_dir,
        **({"protected_paths": sources} if sources else {}),
    )
    return {"job": job}


@app.post("/api/copy-backup-files")
def copy_backup_files(req: CopyBackupFilesRequest) -> dict[str, Any]:
    try:
        prepared = _prepare_managed_export_paths(req.paths)
    except (FilesystemBrowserError, FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    sources = [str(path) for path, _manifest in prepared]
    job = start_job(
        f"Copy {len(sources)} backup file(s) to external device",
        copy_backup_files_job,
        sources,
        req.destination_dir,
        protected_paths=sources,
    )
    return {"job": job}


@app.post("/api/backup-files/delete-confirmation")
def create_delete_backup_files_confirmation(req: DeleteBackupFilesConfirmationRequest) -> dict[str, Any]:
    try:
        token = issue_delete_backup_files_confirmation(req.paths)
        return {"token": token, "expires_in_seconds": CONFIRMATION_TTL_SECONDS}
    except (FilesystemBrowserError, FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/delete-backup-files")
def delete_backup_files_api(req: DeleteBackupFilesRequest) -> dict[str, Any]:
    try:
        return delete_backup_files(req.paths, req.confirmation_token)
    except HTTPException:
        raise
    except (FilesystemBrowserError, FileNotFoundError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/csv-exports")
def list_csv_exports() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in backup_roots():
        for manifest_path in root.glob("*.csv.manifest.json"):
            try:
                filename = manifest_path.name.removesuffix(".manifest.json")
                if filename in seen:
                    continue
                _, manifest = _completed_csv_export(filename)
                seen.add(filename)
                items.append(
                    {
                        "filename": filename,
                        "size_bytes": int(manifest["size_bytes"]),
                        "sha256": str(manifest["sha256"]),
                        "completed_at": str(manifest.get("completed_at") or ""),
                        "database": str(manifest.get("database") or ""),
                        "table": str(manifest.get("table") or ""),
                        "rows": int(manifest.get("rows") or 0),
                    }
                )
            except (OSError, ValueError, json.JSONDecodeError, FileNotFoundError):
                continue
    items.sort(key=lambda item: item["completed_at"], reverse=True)
    return {"exports": items}


@app.post("/api/restore-backup")
def restore_backup(req: RestoreBackupRequest) -> dict[str, Any]:
    consume_confirmation(req.confirmation_token, "restore-backup", req.database)
    try:
        source, _manifest = _completed_backup(req.filename)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    job = start_job(
        f"Restore backup {req.database}",
        restore_verified_backup_job,
        req.connection,
        req.database,
        req.filename,
        protected_paths=[str(source)],
    )
    return {"job": job}


@app.post("/api/import-server-csv")
def import_server_csv(req: ImportServerCsvRequest) -> dict[str, Any]:
    consume_confirmation(req.confirmation_token, "import-server-csv", req.database, req.table)
    try:
        source, _manifest = _completed_csv_export(req.filename)
    except (OSError, ValueError, json.JSONDecodeError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    truncate = True if req.force else bool(req.truncate)
    job = start_job(
        f"Import CSV {req.table}",
        import_verified_csv_job,
        req.connection,
        req.database,
        req.table,
        req.filename,
        truncate,
        bool(req.force),
        protected_paths=[str(source)],
    )
    return {"job": job}


@app.get("/api/jobs")
def list_jobs() -> dict[str, Any]:
    with _jobs_lock:
        jobs = [_public_job(job) for job in _jobs.values()]
    jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"jobs": jobs[:50]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"job": _public_job(job)}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job.get("status") != "running":
            raise HTTPException(409, "Only a running job can be cancelled")
        event = job.get("_cancel_event")
        if not isinstance(event, threading.Event):
            raise HTTPException(409, "Job does not support cancellation")
        event.set()
    append_job_log(job_id, "Cancellation requested")
    return {"ok": True, "job_id": job_id}


@app.get("/api/download/{filename}")
def download(filename: str, request: Request):
    safe = Path(filename).name
    path = _find_export_file(safe, suffixes={".sql", ".csv"})
    if safe != filename or path is None:
        raise HTTPException(404, "File not found")
    size = path.stat().st_size
    range_header = request.headers.get("range")
    start = 0
    end = max(0, size - 1)
    status_code = 200
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match or (not match.group(1) and not match.group(2)):
            return JSONResponse(status_code=416, content={"detail": "Invalid byte range"}, headers={"Content-Range": f"bytes */{size}"})
        if match.group(1):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else end
        else:
            suffix_length = int(match.group(2))
            start = max(0, size - suffix_length)
        if start >= size or start > end:
            return JSONResponse(status_code=416, content={"detail": "Byte range is outside file"}, headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)
        status_code = 206

    length = 0 if size == 0 else end - start + 1

    def iterator():
        remaining = length
        with path.open("rb") as file:
            file.seek(start)
            while remaining > 0:
                chunk = file.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Disposition": f'attachment; filename="{safe}"',
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(iterator(), status_code=status_code, media_type="application/octet-stream", headers=headers)
