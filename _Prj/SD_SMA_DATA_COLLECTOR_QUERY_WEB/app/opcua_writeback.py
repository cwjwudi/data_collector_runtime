"""Plugin page OPC UA writeback: column arrays + cursor."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from . import opcua_client

logger = logging.getLogger(__name__)

MAX_ARRAY_LEN = 50


@dataclass(frozen=True)
class OpcUaWritebackConfig:
    cursor_node: str
    column_nodes: dict[str, str]

    @classmethod
    def from_binding(cls, raw: Any) -> OpcUaWritebackConfig | None:
        if not isinstance(raw, dict):
            return None
        columns_raw = raw.get("columns")
        if not isinstance(columns_raw, dict) or not columns_raw:
            return None
        column_nodes: dict[str, str] = {}
        for name, node_id in columns_raw.items():
            col = str(name or "").strip()
            nid = str(node_id or "").strip()
            if col and nid:
                column_nodes[col] = nid
        if not column_nodes:
            return None
        cursor_node = str(raw.get("cursor", "") or "").strip()
        return cls(cursor_node=cursor_node, column_nodes=column_nodes)


def should_writeback(writeback: OpcUaWritebackConfig | None, endpoint_url: str) -> bool:
    return writeback is not None and bool(endpoint_url) and bool(writeback.column_nodes)


def build_array_values(
    rows: list[dict[str, Any]],
    column: str,
    *,
    max_len: int = MAX_ARRAY_LEN,
    string_default: str = "",
    numeric_default: int | float = 0,
) -> list[Any]:
    """Build a fixed-length array from row dicts for one column."""
    n = min(len(rows), max_len)
    is_string = _looks_string_column(rows, column, n)
    default: Any = string_default if is_string else numeric_default
    values: list[Any] = []
    for i in range(max_len):
        if i < n:
            raw = rows[i].get(column)
            if raw is None:
                values.append(default)
            else:
                values.append(
                    coerce_cell(
                        format_cell_for_opcua(raw),
                        string_default=string_default,
                        numeric_default=numeric_default,
                    )
                )
        else:
            values.append(default)
    return values


def _looks_string_column(rows: list[dict[str, Any]], column: str, n: int) -> bool:
    for i in range(n):
        val = rows[i].get(column)
        if val is None:
            continue
        if isinstance(val, (str, datetime, date)):
            return True
        if isinstance(val, (int, float, bool)):
            return False
    return False


def format_cell_for_opcua(value: Any) -> Any:
    """Normalize DB cell values before OPC UA coercion (e.g. datetime -> string)."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return value


def coerce_cell(
    value: Any,
    *,
    string_default: str = "",
    numeric_default: int | float = 0,
) -> Any:
    if value is None:
        return numeric_default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return string_default
        try:
            if "." in stripped:
                return float(stripped)
            return int(stripped)
        except ValueError:
            return value
    try:
        return str(value)
    except Exception:
        return string_default


def _writeback_sync(
    endpoint_url: str,
    username: str,
    password: str,
    writeback: OpcUaWritebackConfig,
    columns: list[str],
    rows: list[dict[str, Any]],
    cursor: int,
) -> None:
    async def _run() -> None:
        for col, node_id in writeback.column_nodes.items():
            if col not in columns:
                logger.debug("OPC UA writeback skip unbound column in result: %s", col)
                continue
            values = build_array_values(rows, col)
            ok = await opcua_client.write_array(
                endpoint_url,
                node_id,
                values,
                username=username,
                password=password,
            )
            if ok:
                logger.debug("OPC UA writeback array col=%s node=%s rows=%d", col, node_id, min(len(rows), MAX_ARRAY_LEN))
            else:
                logger.warning("OPC UA writeback array failed col=%s node=%s", col, node_id)

        if writeback.cursor_node:
            ok = await opcua_client.write_scalar(
                endpoint_url,
                writeback.cursor_node,
                cursor,
                username=username,
                password=password,
            )
            if ok:
                logger.debug("OPC UA writeback cursor=%s node=%s", cursor, writeback.cursor_node)
            else:
                logger.warning("OPC UA writeback cursor failed node=%s", writeback.cursor_node)

    try:
        asyncio.run(_run())
    except RuntimeError:
        # Already inside an event loop (e.g. async route)
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_run())
        else:
            loop.run_until_complete(_run())
    except Exception as exc:
        logger.warning("OPC UA writeback failed: %s", exc)


async def write_after_query_async(
    endpoint_url: str,
    username: str,
    password: str,
    writeback: OpcUaWritebackConfig | None,
    columns: list[str],
    rows: list[dict[str, Any]],
    cursor: int,
) -> None:
    if not should_writeback(writeback, endpoint_url) or writeback is None:
        return

    for col, node_id in writeback.column_nodes.items():
        if col not in columns:
            logger.debug("OPC UA writeback skip unbound column in result: %s", col)
            continue
        values = build_array_values(rows, col)
        ok = await opcua_client.write_array(
            endpoint_url,
            node_id,
            values,
            username=username,
            password=password,
        )
        if ok:
            logger.debug("OPC UA writeback array col=%s node=%s rows=%d", col, node_id, min(len(rows), MAX_ARRAY_LEN))
        else:
            logger.warning("OPC UA writeback array failed col=%s node=%s", col, node_id)

    if writeback.cursor_node:
        ok = await opcua_client.write_scalar(
            endpoint_url,
            writeback.cursor_node,
            cursor,
            username=username,
            password=password,
        )
        if ok:
            logger.debug("OPC UA writeback cursor=%s node=%s", cursor, writeback.cursor_node)
        else:
            logger.warning("OPC UA writeback cursor failed node=%s", writeback.cursor_node)


async def write_cursor_only_async(
    endpoint_url: str,
    username: str,
    password: str,
    writeback: OpcUaWritebackConfig | None,
    cursor: int,
) -> None:
    if writeback is None or not endpoint_url or not writeback.cursor_node:
        return
    ok = await opcua_client.write_scalar(
        endpoint_url,
        writeback.cursor_node,
        cursor,
        username=username,
        password=password,
    )
    if ok:
        logger.debug("OPC UA cursor-only write cursor=%s", cursor)
    else:
        logger.warning("OPC UA cursor-only write failed node=%s", writeback.cursor_node)


def write_after_query(
    endpoint_url: str,
    username: str,
    password: str,
    writeback: OpcUaWritebackConfig | None,
    columns: list[str],
    rows: list[dict[str, Any]],
    cursor: int,
) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _writeback_sync(endpoint_url, username, password, writeback, columns, rows, cursor)
        return

    async def _schedule() -> None:
        try:
            await write_after_query_async(
                endpoint_url, username, password, writeback, columns, rows, cursor
            )
        except Exception as exc:
            logger.warning("OPC UA writeback async task failed: %s", exc)

    asyncio.get_running_loop().create_task(_schedule())


def write_cursor_only(
    endpoint_url: str,
    username: str,
    password: str,
    writeback: OpcUaWritebackConfig | None,
    cursor: int,
) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(
                write_cursor_only_async(endpoint_url, username, password, writeback, cursor)
            )
        except Exception as exc:
            logger.warning("OPC UA cursor write failed: %s", exc)
        return

    async def _schedule() -> None:
        try:
            await write_cursor_only_async(endpoint_url, username, password, writeback, cursor)
        except Exception as exc:
            logger.warning("OPC UA cursor async task failed: %s", exc)

    asyncio.get_running_loop().create_task(_schedule())
