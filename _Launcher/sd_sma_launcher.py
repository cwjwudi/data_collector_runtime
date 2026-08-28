from __future__ import annotations

import argparse
import atexit
import http.client
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from resource_monitor import ResourceMonitor, ResourceMonitorSettings


def resolve_launcher_dir() -> Path:
    """Return the installed launcher directory for source and Nuitka builds."""
    override = os.environ.get("SD_SMA_LAUNCHER_DIR", "").strip()
    if override:
        return Path(override).resolve()
    if globals().get("__compiled__") is not None:
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent


def resolve_data_root(package_root: Path) -> Path:
    """Return the writable runtime root (ProgramData for service installs)."""
    override = os.environ.get("SD_SMA_DATA_ROOT", "").strip()
    if not override:
        return package_root.resolve()
    expanded = os.path.expandvars(override)
    return Path(expanded).resolve()


LAUNCHER_DIR = resolve_launcher_dir()
PACKAGE_ROOT = LAUNCHER_DIR.parent
DATA_ROOT = resolve_data_root(PACKAGE_ROOT)
DEFAULT_CONFIG = LAUNCHER_DIR / "launcher_config.json"
LAUNCHER_LOG_DIR = DATA_ROOT / "logs" / "launcher"
LAUNCHER_LOG_FILE = LAUNCHER_LOG_DIR / "launcher.log"
COMMON_ROOT = PACKAGE_ROOT / "_Prj" / "SD_SMA_COMMON"
if COMMON_ROOT.is_dir() and str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from launcher_imports import ConfigImportManager  # noqa: E402
from launcher_security import LauncherSecurityStore  # noqa: E402
from launcher_settings import (  # noqa: E402
    NETWORK_HOSTS,
    NetworkAccessController,
    NetworkSettingsStore,
    apply_network_host,
)
from launcher_supervisor import ServiceSupervisor  # noqa: E402
from launcher_web import ManagementServer, create_management_app  # noqa: E402

DEFAULT_SERVICE_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_SERVICE_LOG_BACKUP_COUNT = 5

# Unified package-root folders used by launcher/portable package.
SERVICE_DATA_DIRS: dict[str, str] = {
    "collector_web": "collector",
    "query_web": "query_web",
    "db_admin": "db_admin",
    "report_copy": "report_copy",
}
CONFIG_SEED_SKIP_DIR_NAMES = {
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    "__pycache__",
    "venv",
    ".venv",
    "logs",
    "dist",
    "node_modules",
    "_backup",
    "exports",
    "backups",
}
CONFIG_SEED_SKIP_FILE_SUFFIXES = {".pyc", ".pyo", ".log"}

# Crash-restart defaults; override via SD_SMA_RESTART_* environment variables.
DEFAULT_RESTART_MAX_RESTARTS = 3
DEFAULT_RESTART_WINDOW_SECONDS = 60.0
DEFAULT_RESTART_BASE_DELAY_SECONDS = 1.0
DEFAULT_RESTART_BACKOFF_FACTOR = 2.0
DEFAULT_RESTART_MAX_DELAY_SECONDS = 30.0


@dataclass(frozen=True)
class RestartDecision:
    action: str  # "restart" | "give_up"
    delay_seconds: float = 0.0


@dataclass
class RestartPolicy:
    """Pure crash-restart decision logic for one service (no process handling).

    - exit code 0: never restart (normal shutdown).
    - exit code != 0: restart with exponential backoff (1s/2s/4s... capped at
      max_delay_seconds) until max_restarts failures accumulate within
      window_seconds, then give up. max_restarts=0 disables restarts.
    """

    max_restarts: int = DEFAULT_RESTART_MAX_RESTARTS
    window_seconds: float = DEFAULT_RESTART_WINDOW_SECONDS
    base_delay_seconds: float = DEFAULT_RESTART_BASE_DELAY_SECONDS
    backoff_factor: float = DEFAULT_RESTART_BACKOFF_FACTOR
    max_delay_seconds: float = DEFAULT_RESTART_MAX_DELAY_SECONDS
    _failure_times: list[float] = field(default_factory=list, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RestartPolicy":
        source: Mapping[str, str] = os.environ if env is None else env

        def read_int(name: str, default: int) -> int:
            try:
                return int(source[name])
            except (KeyError, ValueError):
                return default

        def read_float(name: str, default: float) -> float:
            try:
                return float(source[name])
            except (KeyError, ValueError):
                return default

        return cls(
            max_restarts=read_int("SD_SMA_RESTART_MAX_RESTARTS", DEFAULT_RESTART_MAX_RESTARTS),
            window_seconds=read_float("SD_SMA_RESTART_WINDOW_SECONDS", DEFAULT_RESTART_WINDOW_SECONDS),
            base_delay_seconds=read_float(
                "SD_SMA_RESTART_BASE_DELAY_SECONDS", DEFAULT_RESTART_BASE_DELAY_SECONDS
            ),
            backoff_factor=read_float("SD_SMA_RESTART_BACKOFF_FACTOR", DEFAULT_RESTART_BACKOFF_FACTOR),
            max_delay_seconds=read_float(
                "SD_SMA_RESTART_MAX_DELAY_SECONDS", DEFAULT_RESTART_MAX_DELAY_SECONDS
            ),
        )

    def decide(self, exit_code: int, now: float) -> RestartDecision:
        if exit_code == 0:
            return RestartDecision(action="give_up")
        cutoff = now - self.window_seconds
        self._failure_times = [t for t in self._failure_times if t > cutoff]
        failures_before = len(self._failure_times)
        if failures_before >= self.max_restarts:
            return RestartDecision(action="give_up")
        self._failure_times.append(now)
        delay = min(
            self.base_delay_seconds * (self.backoff_factor ** failures_before),
            self.max_delay_seconds,
        )
        return RestartDecision(action="restart", delay_seconds=delay)


@dataclass
class ServiceProcess:
    name: str
    title: str
    url: str
    log_path: Path
    process: subprocess.Popen
    log_file: Any
    log_pump: Any = None


class WindowsProcessJob:
    """Best-effort Job Object that kills managed descendants when closed."""

    def __init__(self) -> None:
        self._handle: Any = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            )
            if not ok:
                kernel32.CloseHandle(handle)
                return
            self._handle = handle
            self._kernel32 = kernel32
        except Exception:
            self._handle = None

    def assign(self, process: subprocess.Popen) -> bool:
        if self._handle is None or os.name != "nt":
            return False
        try:
            return bool(self._kernel32.AssignProcessToJobObject(self._handle, int(process._handle)))
        except Exception:
            return False

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._kernel32.CloseHandle(self._handle)
            finally:
                self._handle = None


_PROCESS_JOB = WindowsProcessJob()
atexit.register(_PROCESS_JOB.close)
_SHUTDOWN_EVENT = threading.Event()


class SizeRotatingLogWriter:
    """Append process output to a size-rotated log file (uvicorn.log, .1, .2, ...)."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = DEFAULT_SERVICE_LOG_MAX_BYTES,
        backup_count: int = DEFAULT_SERVICE_LOG_BACKUP_COUNT,
        encoding: str = "utf-8",
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max(1024, int(max_bytes))
        self.backup_count = max(1, int(backup_count))
        self.encoding = encoding
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fp = self.path.open("a", encoding=self.encoding, newline="")
        self._closed = False
        try:
            self._bytes_written = self.path.stat().st_size
        except OSError:
            self._bytes_written = 0

    def write(self, data: str) -> int:
        if not data:
            return 0
        with self._lock:
            if self._closed:
                return 0
            payload = data.encode(self.encoding, errors="replace")
            self._rotate_if_needed(len(payload))
            self._fp.write(data)
            self._fp.flush()
            self._bytes_written += len(payload)
            return len(data)

    def flush(self) -> None:
        with self._lock:
            if not self._closed:
                self._fp.flush()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._fp.flush()
            finally:
                self._fp.close()

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if self._bytes_written + incoming_bytes < self.max_bytes:
            return
        self._fp.flush()
        self._fp.close()
        for idx in range(self.backup_count - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{idx}")
            dst = self.path.with_name(f"{self.path.name}.{idx + 1}")
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
        rotated = self.path.with_name(f"{self.path.name}.1")
        if self.path.exists():
            if rotated.exists():
                rotated.unlink()
            self.path.rename(rotated)
        self._fp = self.path.open("a", encoding=self.encoding, newline="")
        self._bytes_written = 0


def _pump_process_output(stream: Any, writer: SizeRotatingLogWriter) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            writer.write(line)
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass
        writer.close()


class _TeeTextIO:
    """Write the same text to console and launcher log file."""

    def __init__(self, primary: TextIO, secondary: TextIO) -> None:
        self._primary = primary
        self._secondary = secondary

    def write(self, data: str) -> int:
        written = self._primary.write(data)
        try:
            self._secondary.write(data)
            self._secondary.flush()
        except OSError:
            pass
        return written

    def flush(self) -> None:
        self._primary.flush()
        try:
            self._secondary.flush()
        except OSError:
            pass

    def isatty(self) -> bool:
        return bool(getattr(self._primary, "isatty", lambda: False)())

    @property
    def encoding(self) -> str:
        return getattr(self._primary, "encoding", "utf-8") or "utf-8"

    def fileno(self) -> int:
        return self._primary.fileno()


_LAUNCHER_LOG_HANDLE: TextIO | None = None
_ORIGINAL_STDOUT: TextIO | None = None
_ORIGINAL_STDERR: TextIO | None = None


def setup_launcher_file_logging() -> Path:
    """Mirror launcher stdout/stderr into logs/launcher/launcher.log."""
    global _LAUNCHER_LOG_HANDLE, _ORIGINAL_STDOUT, _ORIGINAL_STDERR

    LAUNCHER_LOG_DIR.mkdir(parents=True, exist_ok=True)
    if _LAUNCHER_LOG_HANDLE is not None:
        return LAUNCHER_LOG_FILE

    log_handle = LAUNCHER_LOG_FILE.open("a", encoding="utf-8")
    log_handle.write("\n")
    log_handle.write(f"===== launcher start {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    log_handle.flush()

    _LAUNCHER_LOG_HANDLE = log_handle
    _ORIGINAL_STDOUT = sys.stdout
    _ORIGINAL_STDERR = sys.stderr
    sys.stdout = _TeeTextIO(_ORIGINAL_STDOUT, log_handle)
    sys.stderr = _TeeTextIO(_ORIGINAL_STDERR, log_handle)
    atexit.register(close_launcher_file_logging)
    return LAUNCHER_LOG_FILE


def close_launcher_file_logging() -> None:
    global _LAUNCHER_LOG_HANDLE, _ORIGINAL_STDOUT, _ORIGINAL_STDERR

    if _ORIGINAL_STDOUT is not None:
        sys.stdout = _ORIGINAL_STDOUT
        _ORIGINAL_STDOUT = None
    if _ORIGINAL_STDERR is not None:
        sys.stderr = _ORIGINAL_STDERR
        _ORIGINAL_STDERR = None
    if _LAUNCHER_LOG_HANDLE is not None:
        try:
            _LAUNCHER_LOG_HANDLE.write(f"===== launcher end {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
            _LAUNCHER_LOG_HANDLE.flush()
            _LAUNCHER_LOG_HANDLE.close()
        except OSError:
            pass
        _LAUNCHER_LOG_HANDLE = None


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a JSON object: {path}")
    return data


def resolve_path(value: str | os.PathLike[str], *, base: Path = PACKAGE_ROOT) -> Path:
    raw = str(value)
    raw = raw.replace("${PACKAGE_ROOT}", str(PACKAGE_ROOT))
    raw = raw.replace("${LAUNCHER_DIR}", str(LAUNCHER_DIR))
    raw = raw.replace("${DATA_ROOT}", str(DATA_ROOT))
    raw = os.path.expandvars(raw)
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def repair_venv_config(venv_dir: Path, python_home: Path) -> bool:
    """Repair absolute venv paths after a portable package has been relocated."""
    config_path = venv_dir / "pyvenv.cfg"
    python_exe = python_home / "python.exe"
    if not config_path.is_file() or not python_exe.is_file():
        return False

    resolved_venv = venv_dir.resolve()
    resolved_home = python_home.resolve()
    resolved_python = python_exe.resolve()
    desired = {
        "home": str(resolved_home),
        "executable": str(resolved_python),
        "command": f"{resolved_python} -m venv {resolved_venv}",
    }

    original = config_path.read_text(encoding="utf-8", errors="replace")
    rewritten: list[str] = []
    seen: set[str] = set()
    for line in original.splitlines():
        key = line.split("=", 1)[0].strip().lower() if "=" in line else ""
        if key in desired:
            rewritten.append(f"{key} = {desired[key]}")
            seen.add(key)
        else:
            rewritten.append(line)
    for key in ("home", "executable", "command"):
        if key not in seen:
            rewritten.append(f"{key} = {desired[key]}")

    updated = "\r\n".join(rewritten) + "\r\n"
    if original.replace("\r\n", "\n") == updated.replace("\r\n", "\n"):
        return False

    temp_path = config_path.with_name(f"{config_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="") as file:
        file.write(updated)
    temp_path.replace(config_path)
    return True


def repair_bundled_venv() -> bool:
    return repair_venv_config(PACKAGE_ROOT / ".venv", PACKAGE_ROOT / "_Python")


def find_python(config: dict[str, Any]) -> Path:
    configured = resolve_path(str(config.get("python", "")))
    if configured.is_file():
        return configured

    if config.get("allow_current_python_fallback", False):
        current = Path(sys.executable).resolve()
        print(f"[env] configured Python not found: {configured}")
        print(f"[env] using current Python instead: {current}")
        return current

    raise FileNotFoundError(
        f"Configured Python does not exist: {configured}\n"
        "Edit _Launcher/launcher_config.json or build the portable package first."
    )


def apply_proxy_env(env: dict[str, str], config: dict[str, Any]) -> None:
    proxy = config.get("proxy") if isinstance(config.get("proxy"), dict) else {}
    if not proxy.get("enabled", False):
        return
    url = str(proxy.get("url", "")).strip()
    if not url:
        return
    no_proxy = str(proxy.get("no_proxy", "localhost,127.0.0.1,::1"))
    env["HTTP_PROXY"] = url
    env["HTTPS_PROXY"] = url
    env["NO_PROXY"] = no_proxy
    env["http_proxy"] = url
    env["https_proxy"] = url
    env["no_proxy"] = no_proxy


def expand_config_value(value: str) -> str:
    raw = str(value)
    raw = raw.replace("${PACKAGE_ROOT}", str(PACKAGE_ROOT))
    raw = raw.replace("${LAUNCHER_DIR}", str(LAUNCHER_DIR))
    raw = raw.replace("${DATA_ROOT}", str(DATA_ROOT))
    return os.path.expandvars(raw)


def resolve_service_env(service: dict[str, Any]) -> dict[str, str]:
    raw_env = service.get("env", {})
    if not isinstance(raw_env, dict):
        return {}

    resolved: dict[str, str] = {}
    path_suffixes = ("_DIR", "_PATH", "_FILE")
    for key, value in raw_env.items():
        env_key = str(key).strip()
        if not env_key:
            continue
        expanded = expand_config_value(str(value))
        if env_key.endswith(path_suffixes):
            path = Path(expanded)
            if not path.is_absolute():
                path = DATA_ROOT / path
            expanded = str(path.resolve())
        resolved[env_key] = expanded
    return resolved


def safe_env_display(name: str, value: str) -> str:
    return "***" if any(part in name.upper() for part in ("PASSWORD", "SECRET", "TOKEN", "API_KEY")) else value


def service_folder_name(service: dict[str, Any]) -> str:
    name = str(service.get("name", "")).strip()
    if name in SERVICE_DATA_DIRS:
        return SERVICE_DATA_DIRS[name]
    return name or "service"


def config_dir_from_service(service: dict[str, Any]) -> Path:
    env = resolve_service_env(service)
    for key, value in env.items():
        if key.endswith("_CONFIG_DIR"):
            return Path(value)
    return DATA_ROOT / "config" / service_folder_name(service)


def log_dir_from_service(service: dict[str, Any]) -> Path:
    env = resolve_service_env(service)
    if "SD_SMA_LOG_DIR" in env:
        return Path(env["SD_SMA_LOG_DIR"])
    return DATA_ROOT / "logs" / service_folder_name(service)


def _config_dir_is_empty(path: Path) -> bool:
    if not path.exists():
        return True
    if not path.is_dir():
        return False
    for item in path.rglob("*"):
        if item.is_file():
            return False
    return True


def _should_skip_seed_path(relative: Path) -> bool:
    for part in relative.parts:
        if part in CONFIG_SEED_SKIP_DIR_NAMES:
            return True
    if relative.suffix.lower() in CONFIG_SEED_SKIP_FILE_SUFFIXES:
        return True
    return False


def seed_config_dir(target: Path, source: Path) -> int:
    """Copy initial config files from project config into unified config dir.

    Returns number of files copied.
    """
    if not source.is_dir():
        return 0

    copied = 0
    target.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if _should_skip_seed_path(relative):
            continue
        dest = target / relative
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)
        copied += 1
    return copied


def ensure_report_copy_log_dir(config_dir: Path) -> None:
    config_file = config_dir / "default.json"
    if not config_file.is_file():
        return
    try:
        data = load_json(config_file)
    except (OSError, ValueError, json.JSONDecodeError):
        return

    desired = "${DATA_ROOT}/logs/report_copy"
    if data.get("log_dir") == desired:
        return
    data["log_dir"] = desired
    tmp = config_file.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(config_file)


def neutralize_collector_relative_log_dirs(config_dir: Path) -> int:
    """Clear relative logging.output_dir so SD_SMA_LOG_DIR can take effect."""
    changed = 0
    if not config_dir.is_dir():
        return changed
    for path in config_dir.glob("*.json"):
        try:
            data = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        logging_cfg = data.get("logging")
        if not isinstance(logging_cfg, dict):
            continue
        output_dir = logging_cfg.get("output_dir")
        if not isinstance(output_dir, str):
            continue
        raw = output_dir.strip()
        if not raw:
            continue
        # Keep absolute paths; clear relative ones so launcher env wins.
        if Path(raw).is_absolute() or raw.startswith("${"):
            continue
        logging_cfg["output_dir"] = ""
        data["logging"] = logging_cfg
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        tmp.replace(path)
        changed += 1
    return changed


def bootstrap_runtime_dirs(config: dict[str, Any]) -> None:
    """Create unified config/logs folders and seed empty configs from _Prj."""
    LAUNCHER_LOG_DIR.mkdir(parents=True, exist_ok=True)

    for service in config.get("services", []):
        if not isinstance(service, dict):
            continue
        name = str(service.get("name", "")).strip() or "service"
        folder = service_folder_name(service)
        config_dir = config_dir_from_service(service)
        log_dir = log_dir_from_service(service)
        config_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        # Always keep a reserved per-service log folder under logs/<folder>.
        (DATA_ROOT / "logs" / folder).mkdir(parents=True, exist_ok=True)

        if _config_dir_is_empty(config_dir):
            packaged_defaults = PACKAGE_ROOT / "config" / folder
            cwd = resolve_path(str(service.get("cwd", "")))
            source = packaged_defaults if packaged_defaults != config_dir and packaged_defaults.is_dir() else cwd / "config"
            copied = seed_config_dir(config_dir, source)
            if copied:
                print(f"[bootstrap] seeded {name} config from {source} -> {config_dir} ({copied} files)")
            else:
                print(f"[bootstrap] created empty config dir for {name}: {config_dir}")
        else:
            print(f"[bootstrap] using existing config for {name}: {config_dir}")

        if name == "report_copy":
            ensure_report_copy_log_dir(config_dir)
        if name == "collector_web":
            cleared = neutralize_collector_relative_log_dirs(config_dir)
            if cleared:
                print(f"[bootstrap] cleared relative logging.output_dir in {cleared} collector config file(s)")

        print(f"[bootstrap] logs for {name}: {log_dir}")


def run_python(
    python: Path,
    args: list[str],
    *,
    config: dict[str, Any],
    cwd: Path | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    apply_proxy_env(env, config)
    return subprocess.run(
        [str(python), *args],
        cwd=str(cwd or PACKAGE_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def import_check(python: Path, import_name: str, config: dict[str, Any]) -> bool:
    snippet = (
        "import importlib.util, sys; "
        f"sys.exit(0 if importlib.util.find_spec({import_name!r}) else 1)"
    )
    result = run_python(python, ["-c", snippet], config=config)
    return result.returncode == 0


def pip_check(python: Path, config: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return run_python(python, ["-m", "pip", "check"], config=config)


def install_requirements(python: Path, config: dict[str, Any]) -> None:
    requirements = resolve_path(str(config.get("requirements", "_Launcher/requirements-unified.txt")))
    if not requirements.is_file():
        raise FileNotFoundError(f"Requirements file not found: {requirements}")

    wheelhouse = resolve_path(str(config.get("wheelhouse", "wheelhouse")))
    args = ["-m", "pip", "install", "-r", str(requirements)]
    if wheelhouse.is_dir():
        args.extend(["--no-index", "--find-links", str(wheelhouse)])

    print("[env] installing missing dependencies...")
    result = run_python(python, args, config=config)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise RuntimeError("pip install failed")


def check_environment(python: Path, config: dict[str, Any], *, install_missing: bool) -> None:
    version = run_python(
        python,
        ["-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        config=config,
        check=True,
    ).stdout.strip()
    print(f"[env] Python: {python}")
    print(f"[env] Version: {version}")

    required = config.get("required_imports", [])
    missing: list[str] = []
    for item in required:
        if not isinstance(item, dict):
            continue
        package = str(item.get("package", "")).strip()
        import_name = str(item.get("import", package)).strip()
        if not import_name:
            continue
        if import_check(python, import_name, config):
            print(f"[env] ok: import {import_name}")
        else:
            print(f"[env] missing: {package or import_name}")
            missing.append(package or import_name)

    if missing:
        if install_missing:
            install_requirements(python, config)
            for item in required:
                import_name = str(item.get("import", item.get("package", ""))).strip()
                if import_name and not import_check(python, import_name, config):
                    raise RuntimeError(f"Package still missing after install: {import_name}")
        else:
            raise RuntimeError(
                "Missing Python packages: "
                + ", ".join(missing)
                + "\nRun _Launcher/scripts/build_portable_package.ps1 or enable auto_install_missing."
            )

    result = pip_check(python, config)
    if result.returncode == 0:
        print("[env] pip check: ok")
    else:
        print("[env] pip check reported dependency issues:")
        print(result.stdout.strip() or result.stderr.strip())
        raise RuntimeError("pip check failed")


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def assert_service_paths(config: dict[str, Any]) -> None:
    services = config.get("services", [])
    if not isinstance(services, list) or not services:
        raise ValueError("Config must contain a non-empty services array.")

    for service in services:
        cwd = resolve_path(str(service.get("cwd", "")))
        if not cwd.is_dir():
            raise FileNotFoundError(f"Service cwd not found: {service.get('name')}: {cwd}")


def assert_ports_free(config: dict[str, Any]) -> None:
    for service in config.get("services", []):
        host = str(service.get("host", "127.0.0.1"))
        port = int(service.get("port", 0))
        if port <= 0:
            raise ValueError(f"Invalid port for service {service.get('name')}: {port}")
        if is_port_open(host, port):
            raise RuntimeError(f"Port already in use: {host}:{port} ({service.get('name')})")


def wait_for_http(
    url: str,
    timeout_seconds: float,
    shutdown_event: threading.Event | None = None,
) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if shutdown_event is not None and shutdown_event.is_set():
            return False
        conn: http.client.HTTPConnection | None = None
        try:
            if parsed.scheme == "https":
                conn = http.client.HTTPSConnection(host, port, timeout=2.0)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=2.0)
            conn.request("GET", path, headers={"Host": host, "Connection": "close"})
            response = conn.getresponse()
            response.read()
            if response.status < 500:
                return True
        except OSError:
            pass
        finally:
            if conn is not None:
                conn.close()
        time.sleep(0.5)
    return False


def start_service(python: Path, config: dict[str, Any], service: dict[str, Any]) -> ServiceProcess:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    apply_proxy_env(env, config)

    name = str(service["name"])
    title = str(service.get("title", name))
    cwd = resolve_path(str(service["cwd"]))
    app = str(service["app"])
    host = str(service.get("host", "127.0.0.1"))
    port = int(service["port"])
    # Process stdout/stderr go under logs/<service>/, not logs/launcher/.
    log_dir = log_dir_from_service(service)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "uvicorn.log"
    log_writer = SizeRotatingLogWriter(log_path)
    service_env = resolve_service_env(service)
    common_root = PACKAGE_ROOT / "_Prj" / "SD_SMA_COMMON"
    if common_root.is_dir():
        existing_pythonpath = str(service_env.get("PYTHONPATH") or env.get("PYTHONPATH") or "")
        service_env = {
            **service_env,
            "PYTHONPATH": str(common_root) + (os.pathsep + existing_pythonpath if existing_pythonpath else ""),
        }
    # Let query_web also write its own rotated app.log when SD_SMA_LOG_DIR is set.
    if "SD_SMA_LOG_DIR" not in service_env:
        service_env = {**service_env, "SD_SMA_LOG_DIR": str(log_dir)}
    command = [
        str(python),
        "-m",
        "uvicorn",
        app,
        "--host",
        host,
        "--port",
        str(port),
    ]

    print(f"[start] {title}: http://{host}:{port}")
    print(f"[start] log: {log_path} (rotate {DEFAULT_SERVICE_LOG_MAX_BYTES // (1024 * 1024)}MB x{DEFAULT_SERVICE_LOG_BACKUP_COUNT})")
    for env_key, env_value in service_env.items():
        print(f"[start] env {env_key}={safe_env_display(env_key, env_value)}")
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env={**env, **service_env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if os.name == "nt" and not _PROCESS_JOB.assign(process):
        print(f"[job] warning: could not attach {title} to launcher Job Object")
    log_pump = threading.Thread(
        target=_pump_process_output,
        args=(process.stdout, log_writer),
        name=f"log-pump-{name}",
        daemon=True,
    )
    log_pump.start()
    return ServiceProcess(
        name=name,
        title=title,
        url=str(service.get("open_url", f"http://{host}:{port}")),
        log_path=log_path,
        process=process,
        log_file=log_writer,
        log_pump=log_pump,
    )


def start_services(python: Path, config: dict[str, Any]) -> list[ServiceProcess]:
    return [start_service(python, config, service) for service in config.get("services", [])]


def make_service_restarter(python: Path, config: dict[str, Any]) -> Callable[[ServiceProcess], ServiceProcess]:
    services = {
        str(service.get("name", "")): service
        for service in config.get("services", [])
        if isinstance(service, dict)
    }

    def restart(proc: ServiceProcess) -> ServiceProcess:
        service = services.get(proc.name)
        if service is None:
            raise KeyError(f"Unknown service for restart: {proc.name}")
        return start_service(python, config, service)

    return restart


def wait_for_services(
    config: dict[str, Any],
    processes: list[ServiceProcess],
    shutdown_event: threading.Event | None = None,
) -> bool:
    by_name = {p.name: p for p in processes}
    for service in config.get("services", []):
        name = str(service["name"])
        proc = by_name[name]
        host = str(service.get("host", "127.0.0.1"))
        port = int(service["port"])
        health_path = str(service.get("health_path", "/"))
        url = f"http://{host}:{port}{health_path}"
        print(f"[health] waiting for {proc.title}: {url}")
        ok = wait_for_http(url, timeout_seconds=30.0, shutdown_event=shutdown_event)
        if not ok:
            if shutdown_event is not None and shutdown_event.is_set():
                return False
            if proc.process.poll() is not None:
                raise RuntimeError(f"{proc.title} exited early. See log: {proc.log_path}")
            raise RuntimeError(f"{proc.title} did not become ready in time. See log: {proc.log_path}")
        print(f"[health] ready: {proc.title}")
    return True


def open_browser_tabs(config: dict[str, Any], processes: list[ServiceProcess], *, no_browser: bool) -> None:
    if no_browser or not config.get("open_browser", True):
        return
    for proc in processes:
        print(f"[open] {proc.url}")
        webbrowser.open(proc.url)


def close_service_handles(proc: ServiceProcess) -> None:
    """Release stdout pipe, log pump thread and log writer of one service."""
    try:
        if proc.process.stdout is not None:
            proc.process.stdout.close()
    except OSError:
        pass
    if proc.log_pump is not None:
        proc.log_pump.join(timeout=2.0)
    try:
        proc.log_file.close()
    except OSError:
        pass


def terminate_processes(processes: list[ServiceProcess]) -> None:
    for proc in processes:
        if proc.process.poll() is None:
            print(f"[stop] {proc.title}")
            proc.process.terminate()
    deadline = time.monotonic() + 10.0
    for proc in processes:
        while proc.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        if proc.process.poll() is None:
            print(f"[stop] force kill: {proc.title}")
            proc.process.kill()
        close_service_handles(proc)


def monitor(
    processes: list[ServiceProcess],
    *,
    restart_service: Callable[[ServiceProcess], ServiceProcess] | None = None,
    resource_monitor: ResourceMonitor | None = None,
    policy_factory: Callable[[], RestartPolicy] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    poll_interval_seconds: float = 1.0,
    shutdown_event: threading.Event | None = None,
) -> int:
    print("")
    print("Services are running. Press Ctrl+C to stop all services.")
    print("")
    if policy_factory is None:
        policy_factory = RestartPolicy.from_env
    policies: dict[str, RestartPolicy] = {}
    while True:
        if shutdown_event is not None and shutdown_event.is_set():
            print("[stop] shutdown requested")
            return 0
        if resource_monitor is not None:
            resource_monitor.maybe_sample(processes)
        for index, proc in enumerate(processes):
            code = proc.process.poll()
            if code is None:
                continue
            print(f"[exit] {proc.title} exited with code {code}. See log: {proc.log_path}")
            if code == 0 or restart_service is None:
                return code
            policy = policies.setdefault(proc.name, policy_factory())
            decision = policy.decide(code, clock())
            if decision.action != "restart":
                print(f"[restart] giving up on {proc.title}: crash limit reached")
                return code
            # Release old handles before spawning the replacement (no leaks).
            close_service_handles(proc)
            print(f"[restart] restarting {proc.title} in {decision.delay_seconds:g}s (exit code {code})")
            sleep(decision.delay_seconds)
            processes[index] = restart_service(proc)
            if resource_monitor is not None:
                resource_monitor.note_restart(proc.name)
        sleep(poll_interval_seconds)


def install_signal_handlers(shutdown_event: threading.Event) -> None:
    """Translate console/service termination signals into orderly shutdown."""

    def request_shutdown(signum: int, _frame: Any) -> None:
        print(f"[stop] received signal {signum}")
        shutdown_event.set()

    supported = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        supported.append(signal.SIGBREAK)
    for signum in supported:
        try:
            signal.signal(signum, request_shutdown)
        except (OSError, ValueError):
            pass


def make_resource_monitor(config: Mapping[str, Any]) -> ResourceMonitor | None:
    raw_settings = config.get("resource_monitor")
    settings = ResourceMonitorSettings.from_mapping(raw_settings if isinstance(raw_settings, Mapping) else None)
    if not settings.enabled:
        print("[resource] monitoring disabled")
        return None
    try:
        monitor = ResourceMonitor(settings, LAUNCHER_LOG_DIR)
    except Exception as exc:  # Resource monitoring is optional and must not block services.
        print(f"[resource] monitoring unavailable: {exc}")
        return None
    print(f"[resource] metrics: {monitor.metrics_path}")
    print(
        "[resource] sampling every "
        f"{settings.sample_interval_seconds:g}s; console every {settings.console_interval_seconds:g}s"
    )
    return monitor


def service_health_once(service: Mapping[str, Any]) -> bool:
    host = str(service.get("host", "127.0.0.1"))
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = int(service.get("port", 0))
    path = str(service.get("health_path", "/"))
    connection: http.client.HTTPConnection | None = None
    try:
        connection = http.client.HTTPConnection(host, port, timeout=0.4)
        connection.request("GET", path, headers={"Host": host, "Connection": "close"})
        response = connection.getresponse()
        response.read()
        return response.status < 500
    except OSError:
        return False
    finally:
        if connection is not None:
            connection.close()


def service_with_launcher_credential(
    service: Mapping[str, Any], security: LauncherSecurityStore
) -> dict[str, Any]:
    result = dict(service)
    credential = security.credential_for_service(str(service.get("name", "")))
    if credential is None:
        return result
    environment = dict(service.get("env") or {})
    environment.update(
        {
            "SD_SMA_DB_HOST": str(credential.get("host", "")),
            "SD_SMA_DB_PORT": str(credential.get("port", "")),
            "SD_SMA_DB_USERNAME": str(credential.get("username", "")),
            "SD_SMA_DB_DATABASE": str(credential.get("database", "")),
            "SD_SMA_DB_PASSWORD": str(credential.get("password", "")),
        }
    )
    environment["SD_SMA_DB_CREDENTIAL_MANAGED"] = "1"
    result["env"] = environment
    return result


def wait_for_supervisor_ready(supervisor: ServiceSupervisor, timeout_seconds: float = 35.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        services = supervisor.status()["services"]
        if services and all(item["state"] == "running" for item in services):
            return True
        if any(item["state"] == "failed" for item in services):
            return False
        if _SHUTDOWN_EVENT.wait(0.25):
            return False
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SD SMA unified launcher")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to launcher_config.json")
    parser.add_argument("--check", action="store_true", help="Only check Python and package dependencies")
    parser.add_argument("--smoke", action="store_true", help="Start services, wait for health checks, then stop")
    parser.add_argument("--install-missing", action="store_true", help="Install missing dependencies before start")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser tabs")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _SHUTDOWN_EVENT.clear()
    install_signal_handlers(_SHUTDOWN_EVENT)
    launcher_log = setup_launcher_file_logging()
    print(f"[log] launcher file: {launcher_log}")
    print(f"[data] runtime root: {DATA_ROOT}")

    config_path = resolve_path(args.config, base=Path.cwd()) if args.config != str(DEFAULT_CONFIG) else DEFAULT_CONFIG
    config = load_json(config_path)
    install_missing = args.install_missing or bool(config.get("auto_install_missing", False))

    try:
        if repair_bundled_venv():
            print(f"[env] repaired bundled Python paths: {PACKAGE_ROOT / '.venv' / 'pyvenv.cfg'}")
        python = find_python(config)
        check_environment(python, config, install_missing=install_missing)
        assert_service_paths(config)
        bootstrap_runtime_dirs(config)
        if args.check:
            print("[check] ok")
            return 0
        security = LauncherSecurityStore(DATA_ROOT / "secrets" / "launcher_security.json")
        importer = ConfigImportManager(DATA_ROOT)
        network_store = NetworkSettingsStore(DATA_ROOT / "state" / "network.json")
        runtime_services = apply_network_host(
            [dict(item) for item in config.get("services", []) if isinstance(item, dict)],
            network_store.mode,
        )

        def managed_start(service: dict[str, Any]) -> ServiceProcess:
            return start_service(python, config, service_with_launcher_credential(service, security))

        supervisor = ServiceSupervisor(
            runtime_services,
            DATA_ROOT / "state" / "services.json",
            start_process=managed_start,
            stop_process=lambda process: terminate_processes([process]),
            health_check=service_health_once,
        )
        management = config.get("management") if isinstance(config.get("management"), dict) else {}
        management_host = NETWORK_HOSTS[network_store.mode]
        management_port = int(management.get("port", 8090))
        network = NetworkAccessController(network_store, supervisor)
        app = create_management_app(supervisor, security, importer, LAUNCHER_DIR / "static", network)
        management_server = ManagementServer(app, host=management_host, port=management_port)
        network.attach_management_server(management_server)
        try:
            management_server.start()
            local_management_url = f"http://127.0.0.1:{management_port}"
            print(f"[management] {local_management_url} ({network_store.mode})")
            supervisor.start()
            if args.smoke:
                if not wait_for_supervisor_ready(supervisor):
                    raise RuntimeError("One or more managed services did not become ready")
                print("[smoke] ok")
                return 0
            if not args.no_browser and bool(config.get("open_browser", True)):
                webbrowser.open(local_management_url)
            resource_monitor = make_resource_monitor(config)
            print("")
            print("Launcher management is running. Press Ctrl+C to stop.")
            while not _SHUTDOWN_EVENT.wait(0.5):
                if management_server.thread is not None and not management_server.thread.is_alive():
                    raise RuntimeError("Launcher management server exited unexpectedly")
                if resource_monitor is not None:
                    resource_monitor.maybe_sample(supervisor.processes())
            return 0
        finally:
            management_server.stop()
            supervisor.shutdown()
    except KeyboardInterrupt:
        print("")
        print("[stop] interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001
        print("")
        print(f"[error] {exc}")
        return 1
    finally:
        close_launcher_file_logging()


if __name__ == "__main__":
    raise SystemExit(main())
