"""Lightweight asyncua client with connection reuse for OPC UA writeback."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from asyncua import Client, ua
from asyncua.ua.uaerrors import UaStatusCodeError

from .config_manager import normalize_opcua_endpoint_url

logger = logging.getLogger(__name__)

POOL_IDLE_SEC = 90.0
CONNECT_TIMEOUT_SEC = 5


@dataclass
class _PoolEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    client: Client | None = None
    event_loop: asyncio.AbstractEventLoop | None = None
    last_used: float = 0.0
    endpoint_url: str = ""
    username: str = ""
    password_fp: str = ""


_pool: _PoolEntry | None = None
_heartbeat_type_cache: dict[tuple[str, str, str, str], ua.VariantType | None] = {}


def _password_fingerprint(password: str) -> str:
    if not password:
        return ""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


async def _safe_disconnect(client: Client | None) -> None:
    if not client:
        return
    try:
        await client.disconnect()
    except Exception:
        logger.debug("OPC UA disconnect ignored", exc_info=True)


async def _invalidate_client(entry: _PoolEntry) -> None:
    client = entry.client
    entry.client = None
    entry.event_loop = None
    _heartbeat_type_cache.clear()
    await _safe_disconnect(client)


async def _prune_if_idle(entry: _PoolEntry) -> None:
    if entry.client and (time.monotonic() - entry.last_used > POOL_IDLE_SEC):
        await _invalidate_client(entry)


def _config_mismatch(
    entry: _PoolEntry,
    endpoint_url: str,
    username: str,
    password: str,
) -> bool:
    fp = _password_fingerprint(password)
    return (
        entry.endpoint_url != endpoint_url
        or entry.username != username
        or entry.password_fp != fp
    )


async def _ensure_connected(
    endpoint_url: str,
    username: str = "",
    password: str = "",
) -> Client | None:
    global _pool

    endpoint_url = normalize_opcua_endpoint_url(endpoint_url)
    if not endpoint_url:
        logger.warning("OPC UA endpoint_url is empty or invalid")
        return None

    if _pool is None:
        _pool = _PoolEntry()

    entry = _pool
    async with entry.lock:
        current_loop = asyncio.get_running_loop()
        if entry.client is not None and entry.event_loop is not current_loop:
            await _invalidate_client(entry)
        if _config_mismatch(entry, endpoint_url, username, password):
            await _invalidate_client(entry)
        entry.endpoint_url = endpoint_url
        entry.username = username
        entry.password_fp = _password_fingerprint(password)

        await _prune_if_idle(entry)

        if entry.client is None:
            client = Client(url=endpoint_url, timeout=CONNECT_TIMEOUT_SEC)
            if username:
                client.set_user(username)
                client.set_password(password or "")
            try:
                await client.connect()
            except Exception as exc:
                await _invalidate_client(entry)
                raise exc
            entry.client = client
            entry.event_loop = current_loop

        entry.last_used = time.monotonic()
        return entry.client


async def _read_variant_type(node: Any) -> ua.VariantType | None:
    try:
        vt = await node.read_data_type_as_variant_type()
        return vt if isinstance(vt, ua.VariantType) else None
    except (UaStatusCodeError, Exception):
        logger.debug("OPC UA read_data_type failed for node", exc_info=True)
    return None


_HEARTBEAT_VARIANT_TYPES = (
    ua.VariantType.Boolean,
    ua.VariantType.SByte,
    ua.VariantType.Byte,
    ua.VariantType.Int16,
    ua.VariantType.UInt16,
    ua.VariantType.Int32,
    ua.VariantType.UInt32,
    ua.VariantType.Int64,
    ua.VariantType.UInt64,
)


def _coerce_scalar(value: Any, variant_type: ua.VariantType | None) -> Any:
    if variant_type == ua.VariantType.Boolean:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "y", "on")
        return bool(value)
    if variant_type in (
        ua.VariantType.SByte,
        ua.VariantType.Byte,
        ua.VariantType.Int16,
        ua.VariantType.UInt16,
        ua.VariantType.Int32,
        ua.VariantType.UInt32,
        ua.VariantType.Int64,
        ua.VariantType.UInt64,
    ):
        if isinstance(value, bool):
            return 1 if value else 0
        return int(value)
    if variant_type in (ua.VariantType.Float, ua.VariantType.Double):
        return float(value)
    if variant_type == ua.VariantType.String:
        return "" if value is None else str(value)
    return value


def _coerce_heartbeat_value(value: Any, variant_type: ua.VariantType | None) -> Any:
    """Heartbeat only supports BOOL and integer node types; always write logical 1."""
    if variant_type is None:
        # Fallback when datatype cannot be read: prefer integer 1.
        if isinstance(value, bool):
            return 1 if value else 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 1
    if variant_type not in _HEARTBEAT_VARIANT_TYPES:
        raise TypeError(f"heartbeat unsupported variant type: {variant_type}")
    return _coerce_scalar(1 if value else 0, variant_type)


async def _write_value_attribute(node: Any, value: Any) -> bool:
    variant_type = await _read_variant_type(node)
    clean_value = _coerce_scalar(value, variant_type)
    data_value = ua.DataValue(ua.Variant(clean_value, variant_type))
    try:
        await node.write_attribute(ua.AttributeIds.Value, data_value)
        return True
    except UaStatusCodeError as exc:
        logger.warning(
            "OPC UA write rejected node_id=%s code=%s (%s)",
            node,
            int(exc.code),
            exc,
        )
        return False
    except Exception as exc:
        logger.warning("OPC UA write failed node_id=%s: %s", node, exc)
        return False


async def write_scalar(
    endpoint_url: str,
    node_id: str,
    value: Any,
    *,
    username: str = "",
    password: str = "",
) -> bool:
    if not endpoint_url or not node_id:
        return False
    try:
        client = await _ensure_connected(endpoint_url, username, password)
        if client is None:
            return False
        node = client.get_node(node_id)
        return await _write_value_attribute(node, value)
    except Exception as exc:
        logger.warning("OPC UA write_scalar failed node_id=%s: %s", node_id, exc)
        if _pool is not None:
            async with _pool.lock:
                await _invalidate_client(_pool)
        return False


async def write_heartbeat(
    endpoint_url: str,
    node_id: str,
    *,
    username: str = "",
    password: str = "",
) -> bool:
    """Write logical 1 adapted to BOOL or integer node types only."""
    if not endpoint_url or not node_id:
        return False
    try:
        client = await _ensure_connected(endpoint_url, username, password)
        if client is None:
            return False
        node = client.get_node(node_id)
        cache_key = (normalize_opcua_endpoint_url(endpoint_url), username, _password_fingerprint(password), node_id)
        if cache_key in _heartbeat_type_cache:
            variant_type = _heartbeat_type_cache[cache_key]
        else:
            variant_type = await _read_variant_type(node)
            _heartbeat_type_cache[cache_key] = variant_type
        clean_value = _coerce_heartbeat_value(1, variant_type)
        if variant_type is None:
            data_value = ua.DataValue(ua.Variant(clean_value))
        else:
            data_value = ua.DataValue(ua.Variant(clean_value, variant_type))
        await node.write_attribute(ua.AttributeIds.Value, data_value)
        return True
    except TypeError as exc:
        _heartbeat_type_cache.pop(
            (normalize_opcua_endpoint_url(endpoint_url), username, _password_fingerprint(password), node_id),
            None,
        )
        logger.warning("OPC UA heartbeat rejected node_id=%s: %s", node_id, exc)
        return False
    except Exception as exc:
        logger.warning("OPC UA write_heartbeat failed node_id=%s: %s", node_id, exc)
        if _pool is not None:
            async with _pool.lock:
                await _invalidate_client(_pool)
        return False


async def _write_array_whole(
    node: Any,
    values: list[Any],
    *,
    string_max_len: int | None = None,
) -> bool:
    variant_type = await _read_variant_type(node)
    element_type = _array_element_type(variant_type) if variant_type is not None else None
    if element_type is None and variant_type is not None:
        element_type = variant_type

    coerced: list[Any] = []
    for item in values:
        try:
            clipped = _clip_string(item, string_max_len)
            coerced.append(_coerce_scalar(clipped, element_type))
        except (TypeError, ValueError):
            coerced.append(_default_for_type(element_type))

    # B&R 等 PLC：整数组写必须走 write_attribute(Value)，write_value 常报 BadWriteNotSupported
    try:
        if element_type is not None:
            data_value = ua.DataValue(ua.Variant(coerced, element_type))
        else:
            data_value = ua.DataValue(ua.Variant(coerced))
        await node.write_attribute(ua.AttributeIds.Value, data_value)
        return True
    except UaStatusCodeError as exc:
        logger.debug("OPC UA whole-array write_attribute rejected: %s", exc)
        return False
    except Exception as exc:
        logger.debug("OPC UA whole-array write_attribute failed: %s", exc)
        return False


def _array_element_type(variant_type: ua.VariantType) -> ua.VariantType | None:
    name = getattr(variant_type, "name", None) or str(variant_type)
    mapping = {
        "Int32Array": ua.VariantType.Int32,
        "UInt32Array": ua.VariantType.UInt32,
        "StringArray": ua.VariantType.String,
        "DoubleArray": ua.VariantType.Double,
        "FloatArray": ua.VariantType.Float,
        "BooleanArray": ua.VariantType.Boolean,
    }
    if name in mapping:
        return mapping[name]
    if name.endswith("Array"):
        base = name[:-5]
        try:
            return ua.VariantType[base]
        except KeyError:
            return None
    return variant_type


def _default_for_type(variant_type: ua.VariantType | None) -> Any:
    if variant_type == ua.VariantType.Boolean:
        return False
    if variant_type == ua.VariantType.String:
        return ""
    if variant_type is not None:
        return 0
    return 0


async def _write_array_elementwise(
    node: Any,
    values: list[Any],
    *,
    string_max_len: int | None = None,
) -> bool:
    variant_type = await _read_variant_type(node)
    element_type = _array_element_type(variant_type) if variant_type else None
    ok = True
    for idx, item in enumerate(values):
        try:
            child = await node.get_child(idx)
        except Exception:
            try:
                child = await node.get_child(str(idx))
            except Exception as exc:
                logger.warning("OPC UA array child %d not found: %s", idx, exc)
                ok = False
                continue
        clipped = _clip_string(item, string_max_len)
        if not await _write_value_attribute(
            child,
            clipped if element_type is None else _coerce_scalar(clipped, element_type),
        ):
            ok = False
    return ok


def _clip_string(value: Any, string_max_len: int | None) -> Any:
    if string_max_len is None or string_max_len <= 0:
        return value
    if isinstance(value, str):
        return value[:string_max_len]
    return value


async def write_array(
    endpoint_url: str,
    node_id: str,
    values: list[Any],
    *,
    username: str = "",
    password: str = "",
    string_max_len: int | None = None,
) -> bool:
    if not endpoint_url or not node_id:
        return False
    try:
        client = await _ensure_connected(endpoint_url, username, password)
        if client is None:
            return False
        node = client.get_node(node_id)
        try:
            existing = await node.read_value()
            if isinstance(existing, list):
                target_len = len(existing)
                if len(values) < target_len:
                    element_type = _array_element_type(await _read_variant_type(node))
                    if element_type is None:
                        element_type = await _read_variant_type(node)
                    default = _default_for_type(element_type if isinstance(element_type, ua.VariantType) else None)
                    values = list(values) + [default] * (target_len - len(values))
                elif len(values) > target_len:
                    values = values[:target_len]
        except Exception:
            logger.debug("OPC UA could not read array length before write", exc_info=True)
        if await _write_array_whole(node, values, string_max_len=string_max_len):
            return True
        return await _write_array_elementwise(node, values, string_max_len=string_max_len)
    except Exception as exc:
        logger.warning("OPC UA write_array failed node_id=%s: %s", node_id, exc)
        if _pool is not None:
            async with _pool.lock:
                await _invalidate_client(_pool)
        return False


async def check_connection(
    endpoint_url: str,
    username: str = "",
    password: str = "",
) -> dict[str, Any]:
    """Connect to OPC UA server, read basic server info, then disconnect (no pool reuse)."""
    endpoint = normalize_opcua_endpoint_url(endpoint_url)
    if not endpoint:
        return {"ok": False, "message": "Endpoint URL 无效或为空，请在配置页填写 IP 与端口后保存"}

    client = Client(url=endpoint, timeout=CONNECT_TIMEOUT_SEC)
    if username:
        client.set_user(username)
        client.set_password(password or "")

    try:
        await client.connect()
        namespaces = await client.get_namespace_array()
        product_name = ""
        server_state = ""
        try:
            product_name = str(
                await client.get_node(ua.ObjectIds.Server_ServerStatus_BuildInfo_ProductName).read_value()
                or ""
            )
        except Exception:
            logger.debug("OPC UA test read product name failed", exc_info=True)
        try:
            server_state = str(
                await client.get_node(ua.ObjectIds.Server_ServerStatus_State).read_value()
            )
        except Exception:
            logger.debug("OPC UA test read server state failed", exc_info=True)

        return {
            "ok": True,
            "status": "ok",
            "message": "OPC UA 连接成功",
            "endpoint_url": endpoint,
            "product_name": product_name,
            "server_state": server_state,
            "namespace_count": len(namespaces) if namespaces else 0,
        }
    except Exception as exc:
        logger.warning("OPC UA test connection failed endpoint=%s: %s", endpoint, exc)
        return {
            "ok": False,
            "status": "error",
            "message": f"OPC UA 连接失败: {exc}",
            "endpoint_url": endpoint,
        }
    finally:
        await _safe_disconnect(client)


async def read_scalar(
    endpoint_url: str,
    node_id: str,
    *,
    username: str = "",
    password: str = "",
) -> Any:
    """Read a scalar node value; invalidate the pool on failure so the next call reconnects."""
    try:
        client = await _ensure_connected(endpoint_url, username, password)
        if client is None:
            raise RuntimeError("OPC UA not connected")
        node = client.get_node(node_id)
        return await node.read_value()
    except Exception:
        if _pool is not None:
            async with _pool.lock:
                await _invalidate_client(_pool)
        raise


async def read_scalars(
    endpoint_url: str,
    node_ids: list[str],
    *,
    username: str = "",
    password: str = "",
) -> list[Any]:
    """Read multiple scalar nodes in one OPC UA service call."""
    if not node_ids:
        return []
    try:
        client = await _ensure_connected(endpoint_url, username, password)
        if client is None:
            raise RuntimeError("OPC UA not connected")
        values = list(await client.read_values([client.get_node(node_id) for node_id in node_ids]))
        if len(values) != len(node_ids):
            raise RuntimeError(
                f"OPC UA batch read returned {len(values)} values for {len(node_ids)} nodes"
            )
        return values
    except Exception:
        if _pool is not None:
            async with _pool.lock:
                await _invalidate_client(_pool)
        raise


def is_connected() -> bool:
    """Return True when the pooled OPC UA client is present."""
    return _pool is not None and _pool.client is not None


async def close_pool() -> None:
    """Disconnect pooled client and release the OPC UA session."""
    global _pool
    if _pool is None:
        return
    async with _pool.lock:
        await _invalidate_client(_pool)
    _pool = None


def reset_pool_for_tests() -> None:
    """Clear connection pool without disconnect (legacy test helper)."""
    global _pool
    _pool = None
    _heartbeat_type_cache.clear()
