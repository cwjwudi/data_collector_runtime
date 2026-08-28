from __future__ import annotations

import ctypes
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = BASE_DIR.parent.parent
DATA_ROOT = Path(os.path.expandvars(os.getenv("SD_SMA_DATA_ROOT", str(PACKAGE_ROOT)))).resolve()
COMMON_ROOT = BASE_DIR.parent / "SD_SMA_COMMON"
if COMMON_ROOT.is_dir() and str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from sd_sma_common import FilesystemBrowser, FilesystemBrowserError

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


def _resolve_config_dir() -> Path:
    raw = os.getenv("SD_SMA_REPORT_COPY_CONFIG_DIR")
    if not raw:
        return (BASE_DIR / "config").resolve()
    value = raw.replace("${REPORT_COPY_ROOT}", str(BASE_DIR))
    value = value.replace("${PACKAGE_ROOT}", str(PACKAGE_ROOT))
    value = value.replace("${DATA_ROOT}", str(DATA_ROOT))
    path = Path(os.path.expandvars(value))
    if not path.is_absolute():
        path = PACKAGE_ROOT / path
    return path.resolve()


CONFIG_DIR = _resolve_config_dir()
CONFIG_FILE = CONFIG_DIR / "default.json"
DEFAULT_CONFIG: dict[str, Any] = {
    "report_source_dir": "D:/SMA_Report",
    "destination_folder": "SMA_Report",
    "allowed_extensions": [".pdf"],
    "copy_subdirectories": True,
    "overwrite_by_default": False,
    "allowed_target_roots": [],
    "allowed_source_roots": [],
    "log_dir": "${DATA_ROOT}/logs/report_copy",
}
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_NAME_RE = re.compile(r"^[A-Za-z]:\\$")


class AppConfig(BaseModel):
    report_source_dir: str = "D:/SMA_Report"
    destination_folder: str = "SMA_Report"
    allowed_extensions: list[str] = Field(default_factory=lambda: [".pdf"])
    copy_subdirectories: bool = True
    overwrite_by_default: bool = False
    allowed_target_roots: list[str] = Field(default_factory=list)
    allowed_source_roots: list[str] = Field(default_factory=list)
    log_dir: str = "${DATA_ROOT}/logs/report_copy"


class CopyRequest(BaseModel):
    drive: str
    files: list[str] = Field(default_factory=list)
    folders: list[str] = Field(default_factory=list)
    destination_folder: str = ""
    overwrite: bool = False


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def load_config() -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if CONFIG_FILE.is_file():
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data = loaded
    merged = {**DEFAULT_CONFIG, **data}
    cfg = AppConfig(**merged)
    result = _model_dump(cfg)
    result["allowed_extensions"] = normalize_extensions(result["allowed_extensions"])
    return result


def save_config(payload: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    allowed_keys = set(DEFAULT_CONFIG)
    for key, value in payload.items():
        if key in allowed_keys:
            cfg[key] = value
    cfg = _model_dump(AppConfig(**cfg))
    cfg["allowed_extensions"] = normalize_extensions(cfg["allowed_extensions"])
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    tmp.replace(CONFIG_FILE)
    return public_config(cfg)


def expand_path(raw: str, *, default_base: Path = BASE_DIR) -> Path:
    value = str(raw or "").strip()
    value = value.replace("${REPORT_COPY_ROOT}", str(BASE_DIR))
    value = value.replace("${PACKAGE_ROOT}", str(PACKAGE_ROOT))
    value = value.replace("${DATA_ROOT}", str(DATA_ROOT))
    value = value.replace("${CONFIG_DIR}", str(CONFIG_DIR))
    path = Path(os.path.expandvars(value))
    if not path.is_absolute():
        path = default_base / path
    return path.resolve()


def normalize_extensions(values: list[str]) -> list[str]:
    result: list[str] = []
    for item in values or []:
        ext = str(item).strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        if re.match(r"^\.[a-z0-9]+$", ext) and ext not in result:
            result.append(ext)
    return result or [".pdf"]


def public_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    source = expand_path(str(cfg["report_source_dir"]))
    configured_log_dir = os.getenv("SD_SMA_LOG_DIR", "").strip() or str(cfg["log_dir"])
    log_dir = expand_path(configured_log_dir, default_base=DATA_ROOT)
    data = dict(cfg)
    data["report_source_dir_resolved"] = str(source)
    data["report_source_exists"] = source.is_dir()
    data["log_dir_resolved"] = str(log_dir)
    return data


def report_root() -> Path:
    return expand_path(str(load_config()["report_source_dir"]))


def log_dir() -> Path:
    cfg = load_config()
    configured = os.getenv("SD_SMA_LOG_DIR", "").strip() or str(cfg["log_dir"])
    path = expand_path(configured, default_base=DATA_ROOT)
    path.mkdir(parents=True, exist_ok=True)
    return path


def append_operation_log(message: str) -> None:
    try:
        path = log_dir() / f"report_copy_{datetime.now():%Y%m%d}.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


def ensure_relative_path(value: str) -> Path:
    raw = str(value or "").strip().replace("\\", "/")
    path = Path(raw)
    if not raw or path.drive or path.is_absolute() or ".." in path.parts:
        raise ValueError("非法报表路径")
    return path


def _resolve_report_entry(value: str, *, entry_type: str) -> tuple[Path, Path]:
    root = report_root().resolve()
    rel = ensure_relative_path(value)
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("报表路径超出配置目录") from exc
    if entry_type == "file" and not target.is_file():
        raise FileNotFoundError(target)
    if entry_type == "folder" and not target.is_dir():
        raise FileNotFoundError(target)
    return root, target


def resolve_report_file(value: str) -> Path:
    _root, target = _resolve_report_entry(value, entry_type="file")
    ext = target.suffix.lower()
    if ext not in normalize_extensions(load_config()["allowed_extensions"]):
        raise ValueError(f"不允许的文件类型: {ext}")
    return target


def resolve_report_folder(value: str) -> Path:
    _root, target = _resolve_report_entry(value, entry_type="folder")
    return target


def collect_selected_reports(req: CopyRequest) -> list[tuple[str, Path]]:
    """Resolve files and recursively expand selected folders, removing overlaps."""
    root = report_root().resolve()
    allowed = set(normalize_extensions(load_config()["allowed_extensions"]))
    selected: dict[Path, tuple[str, Path]] = {}

    def add_file(src: Path) -> None:
        resolved = src.resolve()
        try:
            rel = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("报表路径超出配置目录") from exc
        if resolved.suffix.lower() not in allowed:
            return
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        selected.setdefault(resolved, (rel, resolved))

    for rel in req.files:
        add_file(resolve_report_file(rel))
    for rel in req.folders:
        folder = resolve_report_folder(rel)
        for item in folder.rglob("*"):
            if item.is_file() and item.suffix.lower() in allowed:
                add_file(item)

    return sorted(selected.values(), key=lambda item: item[0].lower())


def sanitize_destination_folder(value: str | None) -> Path:
    raw = (value or load_config()["destination_folder"] or "SMA_Report").strip()
    raw = raw.replace("\\", "/").strip("/")
    path = Path(raw)
    if not raw or path.drive or path.is_absolute() or ".." in path.parts:
        raise ValueError("非法目标文件夹")
    return path


def normalize_drive_root(value: str) -> str:
    text = str(value or "").strip()
    if len(text) == 2 and text[1] == ":":
        text += "\\"
    text = text.replace("/", "\\")
    if len(text) == 3:
        text = text[0].upper() + ":\\"
    if not DRIVE_NAME_RE.match(text):
        raise ValueError(f"非法盘符: {value}")
    return text


def _windows_drives() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    kernel32 = ctypes.windll.kernel32
    bitmask = kernel32.GetLogicalDrives()
    drives: list[dict[str, Any]] = []
    for i in range(26):
        if not (bitmask & (1 << i)):
            continue
        root = f"{chr(65 + i)}:\\"
        drive_type = int(kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))
        if drive_type not in {DRIVE_REMOVABLE, DRIVE_FIXED}:
            continue
        label = ctypes.create_unicode_buffer(261)
        fs_name = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_ulong()
        max_component = ctypes.c_ulong()
        flags = ctypes.c_ulong()
        if not kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            label,
            ctypes.sizeof(label),
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(flags),
            fs_name,
            ctypes.sizeof(fs_name),
        ):
            label.value = ""
            fs_name.value = ""
        free_bytes = ctypes.c_ulonglong()
        total_bytes = ctypes.c_ulonglong()
        total_free = ctypes.c_ulonglong()
        if not kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(root),
            ctypes.byref(free_bytes),
            ctypes.byref(total_bytes),
            ctypes.byref(total_free),
        ):
            free_bytes.value = 0
            total_bytes.value = 0
        drives.append(
            {
                "root": root,
                "label": label.value,
                "file_system": fs_name.value,
                "drive_type": drive_type,
                "is_removable": drive_type == DRIVE_REMOVABLE,
                "free_bytes": int(free_bytes.value),
                "total_bytes": int(total_bytes.value),
            }
        )
    return drives


def allowed_target_roots() -> set[str]:
    roots: set[str] = set()
    for item in load_config().get("allowed_target_roots", []):
        try:
            roots.add(normalize_drive_root(str(item)))
        except ValueError:
            continue
    return roots


def list_drives() -> list[dict[str, Any]]:
    configured = allowed_target_roots()
    drives = _windows_drives()
    seen = {drive["root"] for drive in drives}
    for root in configured:
        if root not in seen and Path(root).exists():
            drives.append(
                {
                    "root": root,
                    "label": "",
                    "file_system": "",
                    "drive_type": DRIVE_FIXED,
                    "is_removable": False,
                    "free_bytes": 0,
                    "total_bytes": 0,
                }
            )
    for drive in drives:
        drive["is_allowed"] = bool(drive["is_removable"] or drive["root"] in configured)
    return drives


def ensure_target_drive(value: str) -> Path:
    root = normalize_drive_root(value)
    drives = {drive["root"]: drive for drive in list_drives()}
    drive = drives.get(root)
    if not drive:
        raise ValueError(f"未检测到目标盘符: {root}")
    if not drive.get("is_allowed"):
        raise ValueError(f"目标盘符未被允许: {root}")
    return Path(root)


def source_filesystem_browser() -> FilesystemBrowser:
    cfg = load_config()
    roots: list[str | Path] = [report_root(), *(cfg.get("allowed_source_roots") or [])]
    roots.extend(drive["root"] for drive in list_drives() if drive.get("is_removable"))
    return FilesystemBrowser(roots)


def format_report(item: Path, root: Path) -> dict[str, Any]:
    stat = item.stat()
    rel = item.relative_to(root).as_posix()
    return {
        "path": rel,
        "name": item.name,
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "extension": item.suffix.lower(),
    }


def list_reports(query: str = "") -> dict[str, Any]:
    cfg = load_config()
    root = report_root().resolve()
    if not root.is_dir():
        return {"root": str(root), "exists": False, "reports": [], "count": 0}
    allowed = set(normalize_extensions(cfg["allowed_extensions"]))
    iterator = root.rglob("*") if cfg.get("copy_subdirectories", True) else root.glob("*")
    q = query.strip().lower()
    reports = [
        format_report(item, root)
        for item in iterator
        if item.is_file()
        and item.suffix.lower() in allowed
        and (not q or q in item.name.lower() or q in item.relative_to(root).as_posix().lower())
    ]
    reports.sort(key=lambda item: item["modified_at"], reverse=True)
    return {"root": str(root), "exists": True, "reports": reports, "count": len(reports)}


_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
MAX_FINISHED_JOBS = 200


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
        job.setdefault("logs", []).append(f"[{stamp}] {message}")


def update_job(job_id: str, **updates: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        updates.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
        job.update(updates)


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    data = {k: v for k, v in job.items() if not k.startswith("_")}
    started = float(job.get("_started_monotonic") or time.monotonic())
    stopped = float(job.get("_finished_monotonic") or time.monotonic())
    data["elapsed_seconds"] = round(max(0.0, stopped - started), 1)
    return data


def start_job(title: str, target, *args: Any) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    now = datetime.now().isoformat(timespec="seconds")
    with _jobs_lock:
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
        }
        _prune_finished_jobs_locked()

    def runner() -> None:
        try:
            append_job_log(job_id, "任务开始")
            result = target(job_id, *args)
            update_job(
                job_id,
                status="done",
                progress=100,
                phase="done",
                result=result,
                _finished_monotonic=time.monotonic(),
            )
            append_job_log(job_id, "任务完成")
        except Exception as exc:  # noqa: BLE001
            update_job(
                job_id,
                status="failed",
                phase="failed",
                error=str(exc),
                _finished_monotonic=time.monotonic(),
            )
            append_job_log(job_id, f"ERROR: {exc}")
            append_operation_log(f"FAILED {title}: {exc}")

    thread = threading.Thread(target=runner, name=f"report-copy-{job_id[:8]}", daemon=True)
    thread.start()
    return public_job(_jobs[job_id])


def copy_file_chunked(src: Path, dest: Path, job_id: str, copied_bytes: int, total_bytes: int) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f".{job_id[:8]}.tmp")
    with src.open("rb") as source, tmp.open("wb") as target:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)
            copied_bytes += len(chunk)
            progress = int((copied_bytes / max(total_bytes, 1)) * 100)
            update_job(job_id, progress=max(1, min(99, progress)), phase=f"copying {src.name}")
    if tmp.stat().st_size != src.stat().st_size:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"复制校验失败: {src.name}")
    tmp.replace(dest)
    return copied_bytes


def copy_reports_job(job_id: str, req: CopyRequest) -> dict[str, Any]:
    cfg = load_config()
    target_drive = ensure_target_drive(req.drive)
    dest_folder = sanitize_destination_folder(req.destination_folder or cfg["destination_folder"])
    selected = collect_selected_reports(req)
    if not selected:
        raise ValueError("请选择至少一个报表文件或文件夹")

    total_bytes = sum(src.stat().st_size for _, src in selected)
    drive_info = next((drive for drive in list_drives() if drive["root"] == str(target_drive)), None)
    free_bytes = int((drive_info or {}).get("free_bytes") or 0)
    if free_bytes and total_bytes > free_bytes:
        raise RuntimeError("U盘剩余空间不足")

    copied: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []
    copied_bytes = 0
    overwrite = bool(req.overwrite)
    root = report_root().resolve()
    destination_root = target_drive / dest_folder
    append_job_log(job_id, f"目标目录: {destination_root}")
    append_operation_log(
        f"START drive={target_drive} dest={destination_root} files={len(selected)} "
        f"selected_files={len(req.files)} selected_folders={len(req.folders)}"
    )

    for index, (rel_text, src) in enumerate(selected, start=1):
        try:
            rel = src.relative_to(root).as_posix()
            dest = destination_root / Path(rel)
            update_job(job_id, phase=f"{index}/{len(selected)} {src.name}")
            append_job_log(job_id, f"复制: {rel}")
            if dest.exists() and not overwrite:
                skipped.append(rel_text)
                copied_bytes += src.stat().st_size
                update_job(job_id, progress=int((copied_bytes / max(total_bytes, 1)) * 100))
                append_job_log(job_id, f"跳过同名文件: {rel}")
                continue
            copied_bytes = copy_file_chunked(src, dest, job_id, copied_bytes, total_bytes)
            copied.append(rel_text)
            append_operation_log(f"OK {rel} -> {dest} bytes={src.stat().st_size}")
        except Exception as exc:  # noqa: BLE001
            failed.append({"path": rel_text, "error": str(exc)})
            append_job_log(job_id, f"失败: {rel_text}: {exc}")
            append_operation_log(f"FILE_FAILED {rel_text}: {exc}")

    if failed and not copied:
        raise RuntimeError("全部文件复制失败")
    return {
        "destination": str(destination_root),
        "copied": copied,
        "skipped": skipped,
        "failed": failed,
        "total_bytes": total_bytes,
    }


app = FastAPI(title="SD SMA Report Copy", version="0.1.0")
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "app" / "static" / "index.html")


@app.get("/config")
def config_page() -> FileResponse:
    return FileResponse(BASE_DIR / "app" / "static" / "config.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return public_config()


@app.post("/api/config")
def post_config(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return save_config(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/filesystem/roots")
def filesystem_roots(purpose: str = "source") -> dict[str, Any]:
    try:
        if purpose != "source":
            raise FilesystemBrowserError("Unsupported browse purpose")
        return {"roots": source_filesystem_browser().public_roots()}
    except FilesystemBrowserError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/filesystem/entries")
def filesystem_entries(path: str, purpose: str = "source") -> dict[str, Any]:
    try:
        if purpose != "source":
            raise FilesystemBrowserError("Unsupported browse purpose")
        return source_filesystem_browser().entries(path, include_files=False)
    except FilesystemBrowserError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/drives")
def get_drives() -> dict[str, Any]:
    return {"drives": list_drives()}


@app.get("/api/reports")
def get_reports(q: str = "") -> dict[str, Any]:
    try:
        return list_reports(q)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/copy")
def start_copy(req: CopyRequest) -> dict[str, Any]:
    try:
        if not req.files and not req.folders:
            raise ValueError("请选择至少一个报表文件或文件夹")
        job = start_job(f"复制报表到 {normalize_drive_root(req.drive)}", copy_reports_job, req)
        return {"job": job}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/jobs")
def list_jobs() -> dict[str, Any]:
    with _jobs_lock:
        jobs = [public_job(job) for job in _jobs.values()]
    jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"jobs": jobs[:50]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"job": public_job(job)}
