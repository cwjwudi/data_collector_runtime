"""把已执行的查询 SQL 写回 PLC OPC UA 节点。写入失败只告警。"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Awaitable, Callable

from . import opcua_client

logger = logging.getLogger(__name__)

# B&R STRING 默认 80 个字符，内存 81 字节（含 NUL）。按 UTF-8 字节限制可写内容。
PLC_STRING_PAYLOAD_BYTES = 80
PLC_STRING_MAX_CONFIG = 4096

_PARAM_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")

SqlWriter = Callable[..., Awaitable[bool]]


class SqlWritebackQueue:
    """串行后台写回；同一插件等待期间只保留最新查询，避免旧 SQL 最后覆盖新 SQL。"""

    def __init__(self, write: Callable[..., Awaitable[str | None]]):
        self._write = write
        self._pending: dict[str, tuple[dict[str, Any], str]] = {}
        self._task: asyncio.Task | None = None

    def submit(self, binding: dict[str, Any], sql: str) -> None:
        if SqlWritebackConfig.from_page(binding.get("sql_writeback")) is None or not sql.strip():
            return
        self._pending[binding["plugin_key"]] = (binding, sql)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while self._pending:
            key = next(iter(self._pending))
            binding, sql = self._pending.pop(key)
            try:
                warning = await self._write(binding, sql)
                if warning:
                    logger.warning("SQL background writeback plugin=%s: %s", key, warning)
            except Exception:
                logger.exception("SQL background writeback failed plugin=%s", key)

    async def close(self) -> None:
        self._pending.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


def clamp_sql_max_len(raw: Any) -> int:
    try:
        value = int(raw if raw is not None else PLC_STRING_PAYLOAD_BYTES)
    except (TypeError, ValueError):
        value = PLC_STRING_PAYLOAD_BYTES
    return max(1, min(value, PLC_STRING_MAX_CONFIG))


@dataclass(frozen=True)
class SqlWritebackConfig:
    """一个插件页自己的 SQL 接收节点。未启用或没有 NodeId 时不写。"""

    node_id: str
    max_len: int = PLC_STRING_PAYLOAD_BYTES

    @classmethod
    def from_page(cls, raw: Any) -> SqlWritebackConfig | None:
        if not isinstance(raw, dict) or not bool(raw.get("enabled")):
            return None
        node_id = str(raw.get("node") or raw.get("node_id") or "").strip()
        if not node_id:
            return None
        return cls(node_id=node_id, max_len=clamp_sql_max_len(raw.get("max_len", PLC_STRING_PAYLOAD_BYTES)))


def quote_literal(value: Any, dialect: str) -> str:
    """把绑定参数写成与该方言驱动一致的字面量。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, ".15g")
    if isinstance(value, datetime):
        fmt = "%Y-%m-%d %H:%M:%S.%f" if value.microsecond else "%Y-%m-%d %H:%M:%S"
        return "'" + value.strftime(fmt) + "'"
    if isinstance(value, date):
        return "'" + value.isoformat() + "'"
    text = str(value)
    if str(dialect or "").lower() == "mysql":
        text = (
            text.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\0", "\\0")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\x1a", "\\Z")
        )
    else:
        text = text.replace("'", "''")
    return "'" + text + "'"


def render_bound_sql(sql: str, params: dict[str, Any], dialect: str) -> str:
    """把 :name 占位符换成已转义字面量。一次替换，避免 :f_1 吃掉 :f_10。"""

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise KeyError(name)
        return quote_literal(params[name], dialect)

    return _PARAM_RE.sub(repl, sql)


def prepare_sql_payload(sql: str, max_bytes: int) -> tuple[str, str | None]:
    """超长时不下发半条语句。返回 (要写入的文本, 告警或 None)。

    截断后的 SQL 不能给 PLC 报表编辑器执行，所以超限改为写入空串，清掉节点上一条旧语句。
    """
    limit = max(1, int(max_bytes))
    raw = str(sql or "").encode("utf-8")
    if len(raw) <= limit:
        return str(sql or ""), None
    warning = (
        f"SQL 长度 {len(raw)} 字节超过 PLC STRING 上限 {limit} 字节"
        "（默认 STRING 约 81 字节含结束符），已清空写回节点，未下发截断语句"
    )
    return "", warning


async def write_executed_sql(
    *,
    enabled: bool,
    endpoint_url: str,
    node_id: str,
    sql: str,
    max_bytes: int = PLC_STRING_PAYLOAD_BYTES,
    username: str = "",
    password: str = "",
    writer: SqlWriter | None = None,
) -> str | None:
    """写回参数已内联的 SQL。返回给查询页展示的告警；异常不向外抛。"""
    if not enabled:
        return None
    node = str(node_id or "").strip()
    endpoint = str(endpoint_url or "").strip()
    if not endpoint or not node:
        warning = "SQL 写回已启用，但未配置 OPC UA 端点或目标 NodeId，已跳过"
        logger.warning(warning)
        return warning

    payload, length_warning = prepare_sql_payload(sql, max_bytes)
    if length_warning:
        logger.warning(length_warning)
    write = writer or opcua_client.write_scalar
    try:
        ok = await write(
            endpoint,
            node,
            payload,
            username=username,
            password=password,
        )
    except Exception as exc:
        warning = f"SQL 写回失败: {exc}"
        logger.warning(warning)
        return _join_warnings(length_warning, warning)
    if not ok:
        warning = f"SQL 写回失败 node={node}"
        logger.warning(warning)
        return _join_warnings(length_warning, warning)
    logger.info("SQL writeback ok node=%s bytes=%d", node, len(payload.encode("utf-8")))
    return length_warning


def _join_warnings(*parts: str | None) -> str | None:
    text = "；".join(part for part in parts if part)
    return text or None
