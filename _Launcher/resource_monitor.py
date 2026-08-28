from __future__ import annotations

import csv
import io
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


CSV_FIELDS = (
    "timestamp",
    "scope",
    "name",
    "pid",
    "status",
    "cpu_percent",
    "cpu_core_percent",
    "memory_mb",
    "memory_percent",
    "memory_available_mb",
    "vms_mb",
    "threads",
    "handles",
    "child_count",
    "read_mb",
    "write_mb",
    "uptime_seconds",
    "restart_count",
    "error",
)


def _load_psutil() -> Any:
    # Local import lets source deployments install dependencies before monitoring starts,
    # while still allowing Nuitka to discover and bundle psutil for the installer launcher.
    import psutil

    return psutil


@dataclass(frozen=True)
class ResourceMonitorSettings:
    enabled: bool = True
    sample_interval_seconds: float = 5.0
    console_interval_seconds: float = 60.0
    include_child_processes: bool = True
    max_log_bytes: int = 50 * 1024 * 1024
    backup_count: int = 5
    alert_cpu_percent: float = 80.0
    alert_memory_mb: float = 1024.0
    alert_sustain_seconds: float = 60.0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "ResourceMonitorSettings":
        source = data or {}
        alerts = source.get("alerts") if isinstance(source.get("alerts"), Mapping) else {}

        def positive_float(value: Any, default: float) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return default
            return parsed if parsed > 0 else default

        def non_negative_float(value: Any, default: float) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return default
            return parsed if parsed >= 0 else default

        def positive_int(value: Any, default: int) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return default
            return parsed if parsed > 0 else default

        max_log_mb = positive_float(source.get("max_log_mb"), 50.0)
        return cls(
            enabled=bool(source.get("enabled", True)),
            sample_interval_seconds=positive_float(source.get("sample_interval_seconds"), 5.0),
            console_interval_seconds=positive_float(source.get("console_interval_seconds"), 60.0),
            include_child_processes=bool(source.get("include_child_processes", True)),
            max_log_bytes=max(1, int(max_log_mb * 1024 * 1024)),
            backup_count=positive_int(source.get("backup_count"), 5),
            alert_cpu_percent=non_negative_float(alerts.get("cpu_percent"), 80.0),
            alert_memory_mb=non_negative_float(alerts.get("memory_mb"), 1024.0),
            alert_sustain_seconds=non_negative_float(alerts.get("sustain_seconds"), 60.0),
        )


class ResourceMonitor:
    """Best-effort system, launcher and service-process resource sampler."""

    def __init__(
        self,
        settings: ResourceMonitorSettings,
        log_dir: Path,
        *,
        psutil_module: Any | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
        emit: Callable[[str], None] = print,
    ) -> None:
        self.settings = settings
        self.log_dir = log_dir
        self.metrics_path = log_dir / "resource_metrics.csv"
        self.alerts_path = log_dir / "resource_alerts.log"
        self._psutil = psutil_module or _load_psutil()
        self._monotonic = monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now().astimezone())
        self._emit = emit
        self._last_sample_at: float | None = None
        self._last_console_at: float | None = None
        self._process_cache: dict[int, Any] = {}
        self._alert_states: dict[tuple[str, str], tuple[float, bool]] = {}
        self._restart_counts: dict[str, int] = {}
        self._logical_cpu_count = max(1, int(self._psutil.cpu_count(logical=True) or 1))

    def note_restart(self, service_name: str) -> None:
        self._restart_counts[service_name] = self._restart_counts.get(service_name, 0) + 1

    def maybe_sample(self, processes: Iterable[Any]) -> None:
        now = self._monotonic()
        if self._last_sample_at is not None:
            elapsed = now - self._last_sample_at
            if elapsed < self.settings.sample_interval_seconds:
                return
        self._last_sample_at = now
        try:
            rows = self.collect(processes)
            self._append_csv(rows)
            self._update_alerts(rows, now)
            if self._last_console_at is None or now - self._last_console_at >= self.settings.console_interval_seconds:
                self._emit_console_summary(rows)
                self._last_console_at = now
        except Exception as exc:  # Monitoring must never stop the managed services.
            self._emit(f"[resource] sample failed: {exc}")

    def collect(self, processes: Iterable[Any]) -> list[dict[str, Any]]:
        timestamp = self._wall_clock().isoformat(timespec="seconds")
        rows = [self._system_row(timestamp)]
        rows.append(self._process_row(timestamp, "launcher", "launcher", os.getpid(), include_children=False))
        for service in processes:
            pid = int(service.process.pid)
            row = self._process_row(
                timestamp,
                "service",
                str(service.name),
                pid,
                include_children=self.settings.include_child_processes,
            )
            row["restart_count"] = self._restart_counts.get(str(service.name), 0)
            rows.append(row)
        self._prune_process_cache()
        return rows

    def _system_row(self, timestamp: str) -> dict[str, Any]:
        memory = self._psutil.virtual_memory()
        disk = self._psutil.disk_io_counters()
        return self._row(
            timestamp=timestamp,
            scope="system",
            name="system",
            status="running",
            cpu_percent=round(float(self._psutil.cpu_percent(interval=None)), 2),
            memory_mb=self._mb(memory.used),
            memory_percent=round(float(memory.percent), 2),
            memory_available_mb=self._mb(memory.available),
            read_mb=self._mb(disk.read_bytes) if disk is not None else "",
            write_mb=self._mb(disk.write_bytes) if disk is not None else "",
        )

    def _process_row(
        self,
        timestamp: str,
        scope: str,
        name: str,
        pid: int,
        *,
        include_children: bool,
    ) -> dict[str, Any]:
        try:
            root = self._cached_process(pid)
            candidates = [root]
            if include_children:
                candidates.extend(root.children(recursive=True))

            active: list[Any] = []
            for candidate in candidates:
                try:
                    proc = self._cached_process(int(candidate.pid), candidate)
                    if proc.is_running():
                        active.append(proc)
                except Exception:
                    continue

            cpu_core_percent = 0.0
            rss = 0
            vms = 0
            threads = 0
            handles = 0
            read_bytes = 0
            write_bytes = 0
            for proc in active:
                try:
                    cpu_core_percent += max(0.0, float(proc.cpu_percent(interval=None)))
                    memory = proc.memory_info()
                    rss += int(memory.rss)
                    vms += int(memory.vms)
                    threads += int(proc.num_threads())
                    if hasattr(proc, "num_handles"):
                        handles += int(proc.num_handles())
                    io_counters = proc.io_counters()
                    read_bytes += int(io_counters.read_bytes)
                    write_bytes += int(io_counters.write_bytes)
                except Exception:
                    continue

            total_memory = max(1, int(self._psutil.virtual_memory().total))
            return self._row(
                timestamp=timestamp,
                scope=scope,
                name=name,
                pid=pid,
                status="running" if active else "unavailable",
                cpu_percent=round(cpu_core_percent / self._logical_cpu_count, 2),
                cpu_core_percent=round(cpu_core_percent, 2),
                memory_mb=self._mb(rss),
                memory_percent=round(rss * 100.0 / total_memory, 2),
                vms_mb=self._mb(vms),
                threads=threads,
                handles=handles,
                child_count=max(0, len(active) - 1),
                read_mb=self._mb(read_bytes),
                write_mb=self._mb(write_bytes),
                uptime_seconds=max(0, int(time.time() - float(root.create_time()))),
            )
        except Exception as exc:
            self._process_cache.pop(pid, None)
            return self._row(
                timestamp=timestamp,
                scope=scope,
                name=name,
                pid=pid,
                status="unavailable",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _cached_process(self, pid: int, candidate: Any | None = None) -> Any:
        cached = self._process_cache.get(pid)
        if cached is not None:
            try:
                if cached.is_running():
                    return cached
            except Exception:
                pass
            self._process_cache.pop(pid, None)
        process = candidate or self._psutil.Process(pid)
        # Prime psutil's non-blocking CPU counter; the first value is expected to be zero.
        process.cpu_percent(interval=None)
        self._process_cache[pid] = process
        return process

    def _prune_process_cache(self) -> None:
        for pid, process in list(self._process_cache.items()):
            try:
                if process.is_running():
                    continue
            except Exception:
                pass
            self._process_cache.pop(pid, None)

    def _append_csv(self, rows: list[dict[str, Any]]) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore")
        needs_header = not self.metrics_path.exists() or self.metrics_path.stat().st_size == 0
        if needs_header:
            writer.writeheader()
        writer.writerows(rows)
        payload = buffer.getvalue().encode("utf-8")
        if self._needs_rotation(self.metrics_path, len(payload)):
            self._rotate(self.metrics_path)
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            payload = buffer.getvalue().encode("utf-8")
        with self.metrics_path.open("ab") as handle:
            handle.write(payload)

    def _update_alerts(self, rows: list[dict[str, Any]], now: float) -> None:
        for row in rows:
            if row["scope"] not in {"launcher", "service"} or row["status"] != "running":
                continue
            self._update_one_alert(
                row,
                "cpu_percent",
                self.settings.alert_cpu_percent,
                now,
                "%",
            )
            self._update_one_alert(
                row,
                "memory_mb",
                self.settings.alert_memory_mb,
                now,
                "MB",
            )

    def _update_one_alert(
        self,
        row: dict[str, Any],
        metric: str,
        threshold: float,
        now: float,
        unit: str,
    ) -> None:
        if threshold <= 0:
            return
        value = float(row[metric])
        key = (str(row["name"]), metric)
        state = self._alert_states.get(key)
        if value >= threshold:
            since, active = state if state is not None else (now, False)
            if not active and now - since >= self.settings.alert_sustain_seconds:
                active = True
                self._write_alert("ALERT", row["name"], metric, value, threshold, unit)
            self._alert_states[key] = (since, active)
            return
        if state is not None and state[1]:
            self._write_alert("RECOVERED", row["name"], metric, value, threshold, unit)
        self._alert_states.pop(key, None)

    def _write_alert(
        self,
        status: str,
        name: str,
        metric: str,
        value: float,
        threshold: float,
        unit: str,
    ) -> None:
        timestamp = self._wall_clock().isoformat(timespec="seconds")
        line = f"{timestamp} {status} {name} {metric}={value:.2f}{unit} threshold={threshold:.2f}{unit}\n"
        payload = line.encode("utf-8")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if self._needs_rotation(self.alerts_path, len(payload)):
            self._rotate(self.alerts_path)
        with self.alerts_path.open("ab") as handle:
            handle.write(payload)
        self._emit(f"[resource] {line.rstrip()}")

    def _emit_console_summary(self, rows: list[dict[str, Any]]) -> None:
        summary = []
        for row in rows:
            if row["status"] != "running":
                summary.append(f"{row['name']}=unavailable")
                continue
            summary.append(f"{row['name']} CPU={row['cpu_percent']}% MEM={row['memory_mb']}MB")
        self._emit("[resource] " + " | ".join(summary))

    def _needs_rotation(self, path: Path, incoming_bytes: int) -> bool:
        try:
            return path.exists() and path.stat().st_size + incoming_bytes > self.settings.max_log_bytes
        except OSError:
            return False

    def _rotate(self, path: Path) -> None:
        oldest = path.with_name(f"{path.name}.{self.settings.backup_count}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.settings.backup_count - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                source.replace(path.with_name(f"{path.name}.{index + 1}"))
        if path.exists():
            path.replace(path.with_name(f"{path.name}.1"))

    @staticmethod
    def _mb(value: int | float) -> float:
        return round(float(value) / (1024 * 1024), 2)

    @staticmethod
    def _row(**values: Any) -> dict[str, Any]:
        row = {field: "" for field in CSV_FIELDS}
        row.update(values)
        return row
