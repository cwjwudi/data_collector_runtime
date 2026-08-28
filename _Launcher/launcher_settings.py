from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


FIREWALL_RULE_NAME = "SD SMA Runtime 8090-8094"
NETWORK_HOSTS = {"global": "0.0.0.0", "local": "127.0.0.1"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class NetworkSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (OSError, ValueError, TypeError):
            pass
        return {"version": 1, "network_mode": "global"}

    @property
    def mode(self) -> str:
        with self._lock:
            value = str(self._data.get("network_mode", "global"))
            return value if value in NETWORK_HOSTS else "global"

    def save_mode(self, mode: str) -> None:
        if mode not in NETWORK_HOSTS:
            raise ValueError("网络模式必须是 global 或 local")
        with self._lock:
            self._data["network_mode"] = mode
            _atomic_json(self.path, self._data)


class FirewallManager:
    """Maintain one port-only rule. Failures are returned as UI warnings."""

    def __init__(self, runner: Callable[..., Any] = subprocess.run) -> None:
        self.runner = runner

    def apply(self, mode: str) -> str:
        if os.name != "nt":
            return "非 Windows 环境未修改防火墙"
        delete = self._run(
            [
                "netsh.exe", "advfirewall", "firewall", "delete", "rule",
                f"name={FIREWALL_RULE_NAME}",
            ]
        )
        if mode == "local":
            return "" if delete.returncode == 0 else self._warning(delete)
        add = self._run(
            [
                "netsh.exe", "advfirewall", "firewall", "add", "rule",
                f"name={FIREWALL_RULE_NAME}", "dir=in", "action=allow",
                "protocol=TCP", "localport=8090-8094", "profile=any",
            ]
        )
        return "" if add.returncode == 0 else self._warning(add)

    def _run(self, command: list[str]) -> Any:
        try:
            return self.runner(command, capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return type("FailedCommand", (), {"returncode": 1, "stdout": "", "stderr": str(exc)})()

    @staticmethod
    def _warning(result: Any) -> str:
        detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "")).strip()
        if detail:
            detail = detail.replace("\r", " ").replace("\n", " ")[:240]
        return f"监听已切换，但 Windows 防火墙规则修改失败{f'：{detail}' if detail else ''}"


class NetworkAccessController:
    def __init__(
        self,
        store: NetworkSettingsStore,
        supervisor: Any,
        firewall: FirewallManager | None = None,
    ) -> None:
        self.store = store
        self.supervisor = supervisor
        self.firewall = firewall or FirewallManager()
        self.management_server: Any | None = None
        self._lock = threading.RLock()
        self._applying = False
        self._last_error = ""
        self._warning = ""
        self._changed_at = _utc_now()

    def attach_management_server(self, server: Any) -> None:
        self.management_server = server

    @property
    def host(self) -> str:
        return NETWORK_HOSTS[self.store.mode]

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self.store.mode,
                "host": self.host,
                "applying": self._applying,
                "last_error": self._last_error,
                "warning": self._warning,
                "changed_at": self._changed_at,
            }

    def apply(self, mode: str) -> dict[str, Any]:
        if mode not in NETWORK_HOSTS:
            raise ValueError("网络模式必须是 global 或 local")
        with self._lock:
            if self._applying:
                raise RuntimeError("网络模式正在切换，请稍候")
            old_mode = self.store.mode
            if mode == old_mode:
                return self.status()
            if self.management_server is None:
                raise RuntimeError("管理服务器尚未就绪")
            self._applying = True
            self._last_error = ""
            try:
                self._warning = self.firewall.apply(mode) if mode == "global" else ""
                self.store.save_mode(mode)
                self.supervisor.set_listen_host(NETWORK_HOSTS[mode])
                self._changed_at = _utc_now()
                self.management_server.rebind_async(
                    NETWORK_HOSTS[mode],
                    lambda error: self._finish_apply(old_mode, mode, error),
                )
            except Exception as error:  # noqa: BLE001
                self._rollback(old_mode, error)
                raise RuntimeError(self._last_error) from error
            return self.status()

    def _finish_apply(self, old_mode: str, new_mode: str, error: Exception | None) -> None:
        with self._lock:
            if error is None:
                if new_mode == "local":
                    self._warning = self.firewall.apply("local")
                self._applying = False
                self._changed_at = _utc_now()
                return
            self._rollback(old_mode, error)

    def _rollback(self, old_mode: str, error: Exception) -> None:
        self._last_error = f"网络模式切换失败，已恢复原设置：{error}"
        try:
            self.store.save_mode(old_mode)
            self.supervisor.set_listen_host(NETWORK_HOSTS[old_mode])
            firewall_warning = self.firewall.apply(old_mode)
            if firewall_warning:
                self._warning = firewall_warning
        except Exception as rollback_error:  # noqa: BLE001
            self._last_error += f"；回滚失败：{rollback_error}"
        self._applying = False
        self._changed_at = _utc_now()


def apply_network_host(services: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    host = NETWORK_HOSTS[mode if mode in NETWORK_HOSTS else "global"]
    result = []
    for service in services:
        item = dict(service)
        item["host"] = host
        result.append(item)
    return result
