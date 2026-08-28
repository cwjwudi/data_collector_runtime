from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


@dataclass
class ManagedService:
    config: dict[str, Any]
    desired_running: bool = True
    state: str = "stopped"
    process: Any | None = None
    started_monotonic: float | None = None
    restart_count: int = 0
    last_exit_code: int | None = None
    last_error: str = ""
    transition_at: str = field(default_factory=_utc_now)
    health_ready: bool = False
    restart_at: float | None = None
    failure_times: list[float] = field(default_factory=list)

    @property
    def name(self) -> str:
        return str(self.config.get("name", ""))


class ServiceSupervisor:
    """Own independent child-service lifecycle and expose thread-safe state."""

    def __init__(
        self,
        services: list[dict[str, Any]],
        state_path: Path,
        *,
        start_process: Callable[[dict[str, Any]], Any],
        stop_process: Callable[[Any], None],
        health_check: Callable[[dict[str, Any]], bool],
        poll_interval_seconds: float = 1.0,
        startup_timeout_seconds: float = 30.0,
        max_restarts: int = 3,
        restart_window_seconds: float = 60.0,
        restart_max_delay_seconds: float = 30.0,
        psutil_module: Any | None = None,
    ) -> None:
        self.state_path = state_path
        self.start_process = start_process
        self.stop_process = stop_process
        self.health_check = health_check
        self.poll_interval_seconds = poll_interval_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self.max_restarts = max_restarts
        self.restart_window_seconds = restart_window_seconds
        self.restart_max_delay_seconds = restart_max_delay_seconds
        persisted = self._load_desired_state()
        self._services = {
            str(item["name"]): ManagedService(
                config=dict(item),
                desired_running=bool(persisted.get(str(item["name"]), item.get("enabled", True))),
            )
            for item in services
        }
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        if psutil_module is None:
            try:
                import psutil as psutil_module
            except ImportError:
                psutil_module = None
        self._psutil = psutil_module
        self._logical_cpu_count = max(1, int(psutil_module.cpu_count(logical=True) or 1)) if psutil_module else 1
        self._metric_processes: dict[int, Any] = {}

    def _load_desired_state(self) -> dict[str, bool]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            values = raw.get("desired_running", {})
            if isinstance(values, dict):
                return {str(key): bool(value) for key, value in values.items()}
        except (OSError, ValueError, TypeError):
            pass
        return {}

    def _persist_desired_state(self) -> None:
        _atomic_json(
            self.state_path,
            {"version": 1, "desired_running": {name: item.desired_running for name, item in self._services.items()}},
        )

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._shutdown.clear()
            self._thread = threading.Thread(target=self._run, name="service-supervisor", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._shutdown.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        with self._lock:
            processes = [item.process for item in self._services.values() if item.process is not None]
            for item in self._services.values():
                item.process = None
                item.state = "stopped"
                item.health_ready = False
                item.transition_at = _utc_now()
        for process in processes:
            try:
                self.stop_process(process)
            except Exception as exc:  # noqa: BLE001
                print(f"[supervisor] shutdown failed: {exc}")

    def command(self, name: str, action: str) -> dict[str, Any]:
        if action not in {"start", "stop", "restart"}:
            raise ValueError("Unsupported service action")
        with self._lock:
            item = self._require(name)
            if action == "start":
                item.desired_running = True
                item.last_error = ""
                item.restart_at = None
            elif action == "stop":
                item.desired_running = False
                item.restart_at = None
            else:
                item.desired_running = True
                item.restart_at = None
                if item.process is not None:
                    self._stop_locked(item)
            self._persist_desired_state()
            self._wake.set()
            return self._status_locked(item)

    def restart_if_running(self, names: list[str]) -> None:
        for name in names:
            with self._lock:
                item = self._services.get(name)
                should_restart = bool(item and item.desired_running and item.process is not None)
            if should_restart:
                self.command(name, "restart")

    def set_listen_host(self, host: str) -> None:
        if host not in {"0.0.0.0", "127.0.0.1"}:
            raise ValueError("Unsupported listen host")
        with self._lock:
            for item in self._services.values():
                if item.process is not None:
                    self._stop_locked(item)
                item.config["host"] = host
                item.last_error = ""
                item.restart_at = None
            self._wake.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            services = [self._status_locked(item) for item in self._services.values()]
        return {
            "launcher": {"state": "stopping" if self._shutdown.is_set() else "running", "time": _utc_now()},
            "services": services,
        }

    def processes(self) -> list[Any]:
        with self._lock:
            return [item.process for item in self._services.values() if item.process is not None]

    def _require(self, name: str) -> ManagedService:
        try:
            return self._services[name]
        except KeyError as exc:
            raise KeyError(f"Unknown service: {name}") from exc

    def _status_locked(self, item: ManagedService) -> dict[str, Any]:
        process = item.process
        pid = int(process.process.pid) if process is not None else None
        cpu_percent: float | None = None
        cpu_core_percent: float | None = None
        memory_mb: float | None = None
        child_count = 0
        if pid:
            try:
                cpu_percent, cpu_core_percent, memory_mb, child_count = self._process_tree_metrics(pid)
            except Exception:
                pass
        uptime = round(time.monotonic() - item.started_monotonic, 1) if item.started_monotonic else 0.0
        open_url = str(item.config.get("open_url", ""))
        parsed_url = urlsplit(open_url)
        url_path = parsed_url.path or "/"
        if parsed_url.query:
            url_path += f"?{parsed_url.query}"
        return {
            "name": item.name,
            "title": str(item.config.get("title", item.name)),
            "state": item.state,
            "desired_running": item.desired_running,
            "health_ready": item.health_ready,
            "pid": pid,
            "host": str(item.config.get("host", "127.0.0.1")),
            "port": int(item.config.get("port", 0)),
            "url": open_url,
            "url_path": url_path,
            "uptime_seconds": uptime,
            "restart_count": item.restart_count,
            "last_exit_code": item.last_exit_code,
            "last_error": item.last_error,
            "transition_at": item.transition_at,
            "cpu_percent": cpu_percent,
            "cpu_core_percent": cpu_core_percent,
            "memory_mb": memory_mb,
            "child_count": child_count,
        }

    def _process_tree_metrics(self, pid: int) -> tuple[float, float, float, int]:
        if self._psutil is None:
            raise RuntimeError("psutil is unavailable")
        root = self._cached_metric_process(pid)
        candidates = [root, *root.children(recursive=True)]
        cpu_core = 0.0
        rss = 0
        active = 0
        for candidate in candidates:
            try:
                process = self._cached_metric_process(int(candidate.pid), candidate)
                if not process.is_running():
                    continue
                cpu_core += max(0.0, float(process.cpu_percent(interval=None)))
                rss += int(process.memory_info().rss)
                active += 1
            except Exception:
                continue
        self._prune_metric_processes()
        return (
            round(cpu_core / self._logical_cpu_count, 1),
            round(cpu_core, 1),
            round(rss / (1024 * 1024), 1),
            max(0, active - 1),
        )

    def _cached_metric_process(self, pid: int, candidate: Any | None = None) -> Any:
        cached = self._metric_processes.get(pid)
        if cached is not None:
            try:
                if cached.is_running():
                    return cached
            except Exception:
                pass
            self._metric_processes.pop(pid, None)
        process = candidate or self._psutil.Process(pid)
        process.cpu_percent(interval=None)
        self._metric_processes[pid] = process
        return process

    def _prune_metric_processes(self) -> None:
        for pid, process in list(self._metric_processes.items()):
            try:
                if process.is_running():
                    continue
            except Exception:
                pass
            self._metric_processes.pop(pid, None)

    def _run(self) -> None:
        while not self._shutdown.is_set():
            with self._lock:
                for item in self._services.values():
                    self._reconcile_locked(item)
            self._wake.wait(self.poll_interval_seconds)
            self._wake.clear()

    def _reconcile_locked(self, item: ManagedService) -> None:
        now = time.monotonic()
        if not item.desired_running:
            if item.process is not None:
                self._stop_locked(item)
            return
        if item.process is None:
            if item.restart_at is not None and now < item.restart_at:
                item.state = "restarting"
                return
            self._start_locked(item)
            return

        code = item.process.process.poll()
        if code is not None:
            item.last_exit_code = int(code)
            self._close_dead_process_locked(item)
            self._schedule_restart_locked(item, now, f"process exited with code {code}")
            return

        if item.state == "starting":
            if self.health_check(item.config):
                item.state = "running"
                item.health_ready = True
                item.transition_at = _utc_now()
            elif item.started_monotonic and now - item.started_monotonic >= self.startup_timeout_seconds:
                self._stop_locked(item)
                self._schedule_restart_locked(item, now, "health check timed out")
        elif item.state == "running":
            item.health_ready = self.health_check(item.config)

    def _start_locked(self, item: ManagedService) -> None:
        item.state = "starting"
        item.transition_at = _utc_now()
        item.health_ready = False
        try:
            item.process = self.start_process(item.config)
            item.started_monotonic = time.monotonic()
            item.restart_at = None
        except Exception as exc:  # noqa: BLE001
            item.process = None
            self._schedule_restart_locked(item, time.monotonic(), str(exc))

    def _stop_locked(self, item: ManagedService) -> None:
        process = item.process
        item.state = "stopping"
        item.transition_at = _utc_now()
        item.health_ready = False
        item.process = None
        item.started_monotonic = None
        if process is not None:
            try:
                self.stop_process(process)
            except Exception as exc:  # noqa: BLE001
                item.last_error = str(exc)
        item.state = "stopped"
        item.transition_at = _utc_now()

    def _close_dead_process_locked(self, item: ManagedService) -> None:
        process = item.process
        item.process = None
        item.started_monotonic = None
        item.health_ready = False
        if process is not None:
            try:
                self.stop_process(process)
            except Exception:
                pass

    def _schedule_restart_locked(self, item: ManagedService, now: float, error: str) -> None:
        cutoff = now - self.restart_window_seconds
        item.failure_times = [value for value in item.failure_times if value > cutoff]
        item.last_error = error
        if len(item.failure_times) >= self.max_restarts:
            item.state = "failed"
            item.desired_running = False
            item.restart_at = None
            item.transition_at = _utc_now()
            self._persist_desired_state()
            return
        delay = min(2 ** len(item.failure_times), self.restart_max_delay_seconds)
        item.failure_times.append(now)
        item.restart_count += 1
        item.state = "restarting"
        item.restart_at = now + delay
        item.transition_at = _utc_now()
