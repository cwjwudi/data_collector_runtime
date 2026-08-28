"""Plugin page batch table-name OPC UA writeback."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import opcua_client
from .table_partition import (
    build_table_name_array,
    normalize_partition_time,
    resolve_matching_partitioned_tables,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_TABLES = 50
DEFAULT_STRING_MAX_LEN = 80
MODE_CURSOR = "cursor"
MODE_ADVANCED = "advanced"
MODE_OPCUA_ALIASES = {MODE_ADVANCED, "opcua", "opcua_trigger", "rising_edge"}

_START_TIME_COLUMN_CANDIDATES = (
    "dtBtachStartTime",
    "dtBatchStartTime",
    "batch_start_time",
    "start_time",
)


def pick_start_time_column(available_columns: set[str], preferred: str) -> str | None:
    if preferred in available_columns:
        return preferred
    pairs = {
        "dtBatchStartTime": "dtBtachStartTime",
        "dtBtachStartTime": "dtBatchStartTime",
    }
    alt = pairs.get(preferred)
    if alt and alt in available_columns:
        return alt
    for candidate in _START_TIME_COLUMN_CANDIDATES:
        if candidate in available_columns:
            return candidate
    return None


@dataclass(frozen=True)
class AdvancedOpcuaTriggerConfig:
    query_node: str
    prev_page_node: str
    next_page_node: str
    batch_no_node: str
    trigger_node: str
    poll_interval_ms: int = 500

    @property
    def has_pagination(self) -> bool:
        return bool(self.prev_page_node or self.next_page_node)

    @property
    def has_snapshot_query(self) -> bool:
        return bool(self.query_node)

    @property
    def has_batch_writeback_trigger(self) -> bool:
        return bool(self.batch_no_node and self.trigger_node)

    @classmethod
    def from_raw(cls, raw: Any) -> AdvancedOpcuaTriggerConfig | None:
        if not isinstance(raw, dict):
            return None
        query_node = str(raw.get("query_node", "") or "").strip()
        prev_page_node = str(raw.get("prev_page_node", "") or "").strip()
        next_page_node = str(raw.get("next_page_node", "") or "").strip()
        batch_no_node = str(raw.get("batch_no_node", "") or "").strip()
        trigger_node = str(raw.get("trigger_node", "") or "").strip()
        has_partial_batch_trigger = bool(trigger_node) != bool(batch_no_node)
        if has_partial_batch_trigger:
            return None
        if not query_node and not prev_page_node and not next_page_node and not (trigger_node and batch_no_node):
            return None
        # poll_interval_ms kept for backward-compatible JSON; runtime uses global opcua setting.
        poll_interval_ms = int(raw.get("poll_interval_ms", 500) or 500)
        return cls(
            query_node=query_node,
            prev_page_node=prev_page_node,
            next_page_node=next_page_node,
            batch_no_node=batch_no_node,
            trigger_node=trigger_node,
            poll_interval_ms=max(50, min(poll_interval_ms, 5000)),
        )


@dataclass(frozen=True)
class TableListWritebackConfig:
    enabled: bool
    batch_column: str
    buffer_node: str
    start_time_column: str = ""
    batch_master_table: str = ""
    max_tables: int = DEFAULT_MAX_TABLES
    string_max_len: int = DEFAULT_STRING_MAX_LEN
    mode: str = MODE_CURSOR
    advanced: AdvancedOpcuaTriggerConfig | None = None

    @property
    def is_advanced_mode(self) -> bool:
        return self.mode == MODE_ADVANCED and self.advanced is not None

    @property
    def has_batch_writeback(self) -> bool:
        return bool(
            self.is_advanced_mode
            and self.advanced
            and self.advanced.has_batch_writeback_trigger
            and self.batch_column
            and self.buffer_node
        )

    @classmethod
    def from_binding(cls, raw: Any, *, bind_group: str | None = None) -> TableListWritebackConfig | None:
        if not isinstance(raw, dict):
            return None
        if not bool(raw.get("enabled")):
            return None

        mode = str(raw.get("mode", MODE_CURSOR) or MODE_CURSOR).strip().lower()
        if mode in MODE_OPCUA_ALIASES:
            mode = MODE_ADVANCED
        elif mode != MODE_CURSOR:
            mode = MODE_CURSOR

        batch_column = str(raw.get("batch_column", "") or "").strip()
        buffer_node = str(raw.get("buffer_node", "") or "").strip()
        start_time_column = str(raw.get("start_time_column", "") or "").strip()
        batch_master_table = str(raw.get("batch_master_table", "") or "").strip()
        if not batch_master_table and bind_group:
            batch_master_table = str(bind_group).strip()

        max_tables = int(raw.get("max_tables", DEFAULT_MAX_TABLES) or DEFAULT_MAX_TABLES)
        string_max_len = int(raw.get("string_max_len", DEFAULT_STRING_MAX_LEN) or DEFAULT_STRING_MAX_LEN)

        advanced = None
        if mode == MODE_CURSOR:
            advanced_raw = raw.get("advanced")
            if isinstance(advanced_raw, dict):
                query_node = str(advanced_raw.get("query_node", "") or "").strip()
                prev_page_node = str(advanced_raw.get("prev_page_node", "") or "").strip()
                next_page_node = str(advanced_raw.get("next_page_node", "") or "").strip()
                trigger_node = str(advanced_raw.get("trigger_node", "") or "").strip()
                batch_no_node = str(advanced_raw.get("batch_no_node", "") or "").strip()
                if query_node or prev_page_node or next_page_node or (trigger_node and batch_no_node):
                    mode = MODE_ADVANCED

        if mode == MODE_ADVANCED:
            advanced = AdvancedOpcuaTriggerConfig.from_raw(raw.get("advanced"))
            if advanced is None:
                return None
            if advanced.has_batch_writeback_trigger and (not batch_column or not buffer_node):
                return None
        elif not batch_column or not buffer_node:
            return None

        return cls(
            enabled=True,
            batch_column=batch_column,
            buffer_node=buffer_node,
            start_time_column=start_time_column,
            batch_master_table=batch_master_table,
            max_tables=max(1, min(max_tables, DEFAULT_MAX_TABLES)),
            string_max_len=max(1, min(string_max_len, DEFAULT_STRING_MAX_LEN)),
            mode=mode,
            advanced=advanced,
        )


def should_write_table_list(
    config: TableListWritebackConfig | None,
    endpoint_url: str,
    cursor: int,
) -> bool:
    if config is None or config.is_advanced_mode:
        return False
    return bool(endpoint_url) and bool(config.buffer_node) and cursor >= 0


def resolve_batch_start_time(
    row: dict[str, Any],
    config: TableListWritebackConfig,
    *,
    lookup_start_time,
) -> datetime | None:
    if config.start_time_column:
        value = row.get(config.start_time_column)
        parsed = normalize_partition_time(value)
        if parsed is not None:
            return parsed

    batch_value = row.get(config.batch_column)
    if batch_value is None or str(batch_value).strip() == "":
        return None
    return lookup_start_time(config.batch_master_table, config.batch_column, batch_value)


def resolve_table_names_for_batch_no(
    batch_no: str,
    config: TableListWritebackConfig,
    *,
    list_tables,
    lookup_start_time,
) -> list[str]:
    """Advanced mode: batch string from PLC → master-table lookup → partition tables."""
    batch_value = str(batch_no or "").strip()
    if not batch_value:
        return empty_table_name_array(config)

    batch_start = lookup_start_time(
        config.batch_master_table,
        config.batch_column,
        batch_value,
    )
    if batch_start is None:
        logger.warning(
            "Table list writeback skipped: batch %r not found in master table %s",
            batch_value,
            config.batch_master_table,
        )
        return build_table_name_array(
            config.batch_master_table,
            [],
            max_tables=config.max_tables,
            string_max_len=config.string_max_len,
        )

    detail_tables = resolve_matching_partitioned_tables(list_tables(), batch_start)
    return build_table_name_array(
        config.batch_master_table,
        detail_tables,
        max_tables=config.max_tables,
        string_max_len=config.string_max_len,
    )


def resolve_table_names_for_row(
    row: dict[str, Any],
    config: TableListWritebackConfig,
    *,
    list_tables,
    lookup_start_time,
) -> list[str]:
    batch_start = resolve_batch_start_time(row, config, lookup_start_time=lookup_start_time)
    if batch_start is None:
        logger.warning(
            "Table list writeback skipped: cannot resolve batch start time (batch_column=%s)",
            config.batch_column,
        )
        return build_table_name_array(
            config.batch_master_table,
            [],
            max_tables=config.max_tables,
            string_max_len=config.string_max_len,
        )

    detail_tables = resolve_matching_partitioned_tables(list_tables(), batch_start)
    return build_table_name_array(
        config.batch_master_table,
        detail_tables,
        max_tables=config.max_tables,
        string_max_len=config.string_max_len,
    )


def empty_table_name_array(config: TableListWritebackConfig) -> list[str]:
    return build_table_name_array(
        "",
        [],
        max_tables=config.max_tables,
        string_max_len=config.string_max_len,
    )


async def write_table_list_async(
    endpoint_url: str,
    username: str,
    password: str,
    config: TableListWritebackConfig | None,
    table_names: list[str] | None,
    *,
    cursor: int,
) -> None:
    if config is None or not endpoint_url:
        return

    if cursor < 0 or table_names is None:
        values = empty_table_name_array(config)
    else:
        values = table_names

    ok = await opcua_client.write_array(
        endpoint_url,
        config.buffer_node,
        values,
        username=username,
        password=password,
        string_max_len=config.string_max_len,
    )
    if ok:
        filled = sum(1 for item in values if str(item or "").strip())
        logger.info(
            "OPC UA table list writeback ok node=%s cursor=%s filled=%d",
            config.buffer_node,
            cursor,
            filled,
        )
    else:
        logger.warning("OPC UA table list writeback failed node=%s", config.buffer_node)
