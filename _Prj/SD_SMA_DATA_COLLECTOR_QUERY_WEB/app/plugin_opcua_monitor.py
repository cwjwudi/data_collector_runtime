"""Background OPC UA rising-edge monitor for advanced plugin table-list writeback."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import opcua_client

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_MS = 500
RECONNECT_INITIAL_SEC = 0.5
RECONNECT_MAX_SEC = 10.0


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


class RisingEdgeDetector:
    """Detect 0→1 transitions per logical key."""

    def __init__(self) -> None:
        self._last: dict[str, bool | None] = {}

    def check(self, key: str, value: Any) -> bool:
        current = _as_bool(value)
        previous = self._last.get(key)
        self._last[key] = current
        if previous is None:
            return False
        return current and not previous

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._last.clear()
        else:
            self._last.pop(key, None)

    def set_last(self, key: str, value: Any) -> None:
        self._last[key] = _as_bool(value)


@dataclass
class PluginRuntimeSnapshot:
    plugin_key: str
    page: int = 1
    total_pages: int = 1
    total_records: int = 0
    table: str = ""
    start_time: str | None = None
    end_time: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    display_columns: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    revision: int = 0
    last_trigger_batch: str = ""
    last_writeback_ok: bool | None = None

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "plugin_key": self.plugin_key,
            "mode": "advanced",
            "page": self.page,
            "total_pages": self.total_pages,
            "total_records": self.total_records,
            "table": self.table,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "columns": list(self.columns),
            "rows": list(self.rows),
            "display_columns": list(self.display_columns),
            "warnings": list(self.warnings),
            "revision": self.revision,
            "last_trigger_batch": self.last_trigger_batch,
            "last_writeback_ok": self.last_writeback_ok,
        }


QueryHandler = Callable[[str, int], Awaitable[PluginRuntimeSnapshot | None]]
SnapshotQueryHandler = Callable[[str], Awaitable[PluginRuntimeSnapshot | None]]
TriggerHandler = Callable[[str, str], Awaitable[bool]]
BindingIterator = Callable[[], list[dict[str, Any]]]
OpcuaConnectionProvider = Callable[[], dict[str, str]]
RuntimeConfigProvider = Callable[[], dict[str, Any]]


class PluginOpcuaMonitor:
    """Poll OPC UA nodes and drive plugin pagination / table-list writeback."""

    def __init__(
        self,
        *,
        iter_bindings: BindingIterator | None = None,
        get_opcua: OpcuaConnectionProvider | None = None,
        get_runtime_config: RuntimeConfigProvider | None = None,
        on_snapshot_query: SnapshotQueryHandler,
        on_page_change: QueryHandler,
        on_trigger: TriggerHandler,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
    ) -> None:
        self._iter_bindings = iter_bindings
        self._get_opcua = get_opcua
        self._get_runtime_config = get_runtime_config
        self._on_snapshot_query = on_snapshot_query
        self._on_page_change = on_page_change
        self._on_trigger = on_trigger
        self._poll_interval_ms = max(50, min(int(poll_interval_ms), 5000))
        self._stop = asyncio.Event()
        self._edges = RisingEdgeDetector()
        self._runtime: dict[str, PluginRuntimeSnapshot] = {}
        self._reconnect_delay_sec = 0.0
        self._was_connected = True
        self._last_opcua: dict[str, Any] = {}
        self._metrics_started = time.monotonic()
        self._metrics_polls = 0
        self._metrics_nodes = 0
        self._metrics_elapsed_sec = 0.0

        if self._get_runtime_config is None and (self._iter_bindings is None or self._get_opcua is None):
            raise ValueError("get_runtime_config or both legacy config providers are required")

    def get_runtime(self, plugin_key: str) -> PluginRuntimeSnapshot | None:
        return self._runtime.get(plugin_key)

    def update_runtime(self, snapshot: PluginRuntimeSnapshot) -> None:
        existing = self._runtime.get(snapshot.plugin_key)
        if existing is None:
            self._runtime[snapshot.plugin_key] = snapshot
            return
        existing.page = snapshot.page
        existing.total_pages = snapshot.total_pages
        existing.total_records = snapshot.total_records
        existing.table = snapshot.table
        existing.start_time = snapshot.start_time
        existing.end_time = snapshot.end_time
        existing.columns = list(snapshot.columns)
        existing.rows = list(snapshot.rows)
        existing.display_columns = list(snapshot.display_columns)
        existing.warnings = list(snapshot.warnings)
        if snapshot.revision > existing.revision:
            existing.revision = snapshot.revision

    def clear_runtime(self, plugin_key: str | None = None) -> None:
        if plugin_key is None:
            self._runtime.clear()
        else:
            self._runtime.pop(plugin_key, None)

    def stop(self) -> None:
        self._stop.set()

    def _resolve_poll_interval_ms(self) -> int:
        """Use global OPC UA poll interval from connection settings."""
        try:
            opcua = self._last_opcua or (self._get_opcua() if self._get_opcua is not None else {}) or {}
            raw = opcua.get("poll_interval_ms", self._poll_interval_ms)
            return max(50, min(int(raw), 5000))
        except Exception:
            return self._poll_interval_ms

    def _next_reconnect_delay_sec(self) -> float:
        """Light backoff: 0.5 → 1 → 2 → 4 → 8 → 10 (capped)."""
        if self._reconnect_delay_sec <= 0:
            return RECONNECT_INITIAL_SEC
        return min(self._reconnect_delay_sec * 2.0, RECONNECT_MAX_SEC)

    def _wait_timeout_sec(self, poll_ok: bool) -> float:
        """Normal poll interval when healthy; reconnect backoff when disconnected."""
        if poll_ok:
            if self._reconnect_delay_sec > 0 or not self._was_connected:
                logger.info("OPC UA connection restored; resuming normal poll")
            self._reconnect_delay_sec = 0.0
            self._was_connected = True
            return self._resolve_poll_interval_ms() / 1000.0

        if self._was_connected:
            logger.warning("OPC UA disconnected; will keep reconnecting with backoff")
        self._was_connected = False
        self._reconnect_delay_sec = self._next_reconnect_delay_sec()
        logger.info("OPC UA reconnect scheduled in %.1fs", self._reconnect_delay_sec)
        return self._reconnect_delay_sec

    async def run(self) -> None:
        logger.info("Plugin OPC UA monitor started (default interval=%dms)", self._poll_interval_ms)
        try:
            while not self._stop.is_set():
                try:
                    poll_ok = await self._poll_once()
                except Exception:
                    logger.warning("Plugin OPC UA monitor poll failed", exc_info=True)
                    poll_ok = False
                wait_sec = self._wait_timeout_sec(poll_ok)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=wait_sec)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Plugin OPC UA monitor stopped")

    async def _poll_once(self) -> bool:
        """Poll bindings and heartbeat. Return False when OPC UA I/O fails (triggers reconnect backoff)."""
        started = time.perf_counter()
        if self._get_runtime_config is not None:
            runtime_config = self._get_runtime_config() or {}
            bindings = list(runtime_config.get("bindings") or [])
            opcua = dict(runtime_config.get("opcua") or {})
        else:
            bindings = self._iter_bindings() if self._iter_bindings is not None else []
            opcua = self._get_opcua() if self._get_opcua is not None else {}
        self._last_opcua = opcua
        endpoint = opcua.get("endpoint_url", "")
        if not endpoint:
            self._record_poll_metrics(started, 0, len(bindings))
            return True

        username = opcua.get("username", "")
        password = opcua.get("password", "")
        heartbeat_node = str(opcua.get("heartbeat_node", "") or "").strip()

        node_ids: list[str] = []
        for binding in bindings:
            advanced = binding.get("_table_list_advanced") or {}
            for field in ("query_node", "prev_page_node", "next_page_node", "trigger_node"):
                node_id = str(advanced.get(field, "") or "").strip()
                if node_id and node_id not in node_ids:
                    node_ids.append(node_id)

        values_by_node: dict[str, Any] = {}
        ok = True
        if node_ids:
            try:
                values = await opcua_client.read_scalars(
                    endpoint,
                    node_ids,
                    username=username,
                    password=password,
                )
                values_by_node = dict(zip(node_ids, values))
            except Exception:
                logger.debug("OPC UA batch read failed nodes=%d", len(node_ids), exc_info=True)
                ok = False

        if ok:
            for binding in bindings:
                if not await self._poll_binding(
                    binding,
                    endpoint,
                    username,
                    password,
                    values_by_node,
                ):
                    ok = False
                    break

        # Global heartbeat: write even when no advanced plugin pages are active.
        if heartbeat_node:
            if not await self._write_heartbeat(endpoint, heartbeat_node, username, password, "global"):
                ok = False

        self._record_poll_metrics(
            started,
            len(node_ids) + (1 if heartbeat_node else 0),
            len(bindings),
        )
        return ok

    def _record_poll_metrics(
        self,
        started: float,
        operation_count: int,
        binding_count: int,
    ) -> None:
        self._metrics_polls += 1
        self._metrics_nodes += operation_count
        self._metrics_elapsed_sec += time.perf_counter() - started
        now = time.monotonic()
        if now - self._metrics_started < 60.0:
            return
        logger.info(
            "OPC UA monitor performance polls=%d operations=%d avg_poll_ms=%.2f bindings=%d",
            self._metrics_polls,
            self._metrics_nodes,
            self._metrics_elapsed_sec * 1000.0 / max(self._metrics_polls, 1),
            binding_count,
        )
        self._metrics_started = now
        self._metrics_polls = 0
        self._metrics_nodes = 0
        self._metrics_elapsed_sec = 0.0

    async def _poll_binding(
        self,
        binding: dict[str, Any],
        endpoint: str,
        username: str,
        password: str,
        values_by_node: dict[str, Any],
    ) -> bool:
        """Return False when an OPC UA read fails; True when skipped or reads succeed."""
        config = binding.get("_table_list_config")
        advanced = binding.get("_table_list_advanced")
        if config is None or advanced is None:
            return True

        plugin_key = str(binding.get("plugin_key", ""))
        if not plugin_key:
            return True

        query_node = str(advanced.get("query_node", "") or "").strip()
        prev_node = str(advanced.get("prev_page_node", "") or "").strip()
        next_node = str(advanced.get("next_page_node", "") or "").strip()
        batch_node = str(advanced.get("batch_no_node", "") or "").strip()
        trigger_node = str(advanced.get("trigger_node", "") or "").strip()

        nodes_to_read: list[tuple[str, str]] = []
        if query_node:
            nodes_to_read.append(("query", query_node))
        if prev_node:
            nodes_to_read.append(("prev", prev_node))
        if next_node:
            nodes_to_read.append(("next", next_node))
        if trigger_node:
            nodes_to_read.append(("trigger", trigger_node))

        if not nodes_to_read:
            return True

        values = {label: values_by_node.get(node_id) for label, node_id in nodes_to_read}

        edge_key_query = f"{plugin_key}:query"
        if query_node and self._edges.check(edge_key_query, values.get("query")):
            logger.info("OPC UA snapshot-query rising edge plugin=%s", plugin_key)
            await self._handle_snapshot_query(plugin_key)
            await self._reset_bool_node(endpoint, query_node, username, password)

        edge_key_prev = f"{plugin_key}:prev"
        if prev_node and self._edges.check(edge_key_prev, values.get("prev")):
            logger.info("OPC UA prev-page rising edge plugin=%s", plugin_key)
            await self._handle_page_delta(plugin_key, -1)
            await self._reset_bool_node(endpoint, prev_node, username, password)

        edge_key_next = f"{plugin_key}:next"
        if next_node and self._edges.check(edge_key_next, values.get("next")):
            logger.info("OPC UA next-page rising edge plugin=%s", plugin_key)
            await self._handle_page_delta(plugin_key, 1)
            await self._reset_bool_node(endpoint, next_node, username, password)

        edge_key_trigger = f"{plugin_key}:trigger"
        if trigger_node and self._edges.check(edge_key_trigger, values.get("trigger")):
            batch_no = ""
            if batch_node:
                try:
                    batch_no = str(
                        await opcua_client.read_scalar(
                            endpoint,
                            batch_node,
                            username=username,
                            password=password,
                        )
                        or ""
                    ).strip()
                except Exception:
                    logger.warning(
                        "OPC UA batch read failed plugin=%s node=%s",
                        plugin_key,
                        batch_node,
                        exc_info=True,
                    )
                    return False
            logger.info(
                "OPC UA trigger rising edge plugin=%s batch_no=%r",
                plugin_key,
                batch_no,
            )
            ok = await self._on_trigger(plugin_key, batch_no)
            snapshot = self._runtime.setdefault(plugin_key, PluginRuntimeSnapshot(plugin_key=plugin_key))
            snapshot.last_trigger_batch = batch_no
            snapshot.last_writeback_ok = ok
            snapshot.revision += 1
            logger.info(
                "OPC UA trigger writeback finished plugin=%s ok=%s batch_no=%r",
                plugin_key,
                ok,
                batch_no,
            )
            await self._reset_bool_node(endpoint, trigger_node, username, password)

        return True

    async def _handle_snapshot_query(self, plugin_key: str) -> None:
        previous = self._runtime.get(plugin_key)
        updated = await self._on_snapshot_query(plugin_key)
        if updated is None:
            logger.warning("OPC UA snapshot query failed plugin=%s", plugin_key)
            return
        updated.revision = (previous.revision if previous else 0) + 1
        self._runtime[plugin_key] = updated
        logger.info(
            "OPC UA snapshot query finished plugin=%s page=%s/%s rows=%d total=%d",
            plugin_key,
            updated.page,
            updated.total_pages,
            len(updated.rows),
            updated.total_records,
        )

    async def _write_heartbeat(
        self,
        endpoint: str,
        node_id: str,
        username: str,
        password: str,
        plugin_key: str,
    ) -> bool:
        """Write logical 1 (typed); PLC clears to 0/FALSE and times out if Query Web stops."""
        if not node_id:
            return True
        ok = await opcua_client.write_heartbeat(
            endpoint,
            node_id,
            username=username,
            password=password,
        )
        if ok:
            logger.debug("OPC UA heartbeat plugin=%s node=%s value=1", plugin_key, node_id)
        else:
            logger.warning("OPC UA heartbeat write failed plugin=%s node=%s", plugin_key, node_id)
        return ok

    async def _reset_bool_node(
        self,
        endpoint: str,
        node_id: str,
        username: str,
        password: str,
    ) -> None:
        if not node_id:
            return
        ok = await opcua_client.write_scalar(
            endpoint,
            node_id,
            False,
            username=username,
            password=password,
        )
        if ok:
            logger.debug("OPC UA reset node=%s to FALSE", node_id)
        else:
            logger.warning("OPC UA reset failed node=%s", node_id)

    async def _handle_page_delta(self, plugin_key: str, delta: int) -> None:
        snapshot = self._runtime.get(plugin_key)
        current_page = snapshot.page if snapshot else 1
        total_pages = max(snapshot.total_pages if snapshot else 1, 1)
        requested = current_page + delta
        target = max(1, min(requested, total_pages))
        # 已到首页/末页时仍重新查询并回写当前页数据
        updated = await self._on_page_change(plugin_key, target)
        if updated is not None:
            updated.revision = (snapshot.revision if snapshot else 0) + 1
            self._runtime[plugin_key] = updated
            logger.info(
                "OPC UA page query finished plugin=%s page=%s/%s rows=%d",
                plugin_key,
                updated.page,
                updated.total_pages,
                len(updated.rows),
            )
