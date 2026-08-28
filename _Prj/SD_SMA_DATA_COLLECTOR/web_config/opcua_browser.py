from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from asyncua import Client


@dataclass
class OpcUaConnectionInfo:
    server_url: str = ""
    connected: bool = False


class OpcUaBrowserService:
    """Web 配置页使用的异步 OPC UA 浏览器。"""

    def __init__(self) -> None:
        self._client: Client | None = None
        self._info = OpcUaConnectionInfo()
        self._lock = asyncio.Lock()

    async def connect(self, host: str, port: int) -> dict[str, Any]:
        server_url = f"opc.tcp://{host}:{port}"
        async with self._lock:
            await self._disconnect_unlocked()
            client = Client(server_url, timeout=4)
            await client.connect()
            self._client = client
            self._info = OpcUaConnectionInfo(server_url=server_url, connected=True)
        return {"connected": True, "server_url": server_url}

    async def disconnect(self) -> None:
        async with self._lock:
            await self._disconnect_unlocked()

    async def _disconnect_unlocked(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self._info.connected = False

    def status(self) -> dict[str, Any]:
        return {
            "connected": self._info.connected,
            "server_url": self._info.server_url,
        }

    def _require_client(self) -> Client:
        if self._client is None or not self._info.connected:
            raise ValueError("OPC UA 未连接，请先执行连接")
        return self._client

    async def browse(self, node_id: str | None = None) -> list[dict[str, Any]]:
        client = self._require_client()
        node = client.get_root_node() if not node_id else client.get_node(node_id)
        children = await node.get_children()
        rows: list[dict[str, Any]] = []
        for child in children:
            try:
                display_name = (await child.read_display_name()).Text
            except Exception:
                display_name = str(child)
            try:
                node_class = (await child.read_node_class()).name
            except Exception:
                node_class = "Unknown"
            try:
                has_children = bool(await child.get_children())
            except Exception:
                has_children = False
            rows.append(
                {
                    "node_id": child.nodeid.to_string(),
                    "display_name": display_name,
                    "node_class": node_class,
                    "has_children": has_children,
                }
            )
        return rows

    async def node_meta(self, node_id: str) -> dict[str, Any]:
        client = self._require_client()
        node = client.get_node(node_id)
        meta: dict[str, Any] = {"node_id": node_id}
        try:
            meta["display_name"] = (await node.read_display_name()).Text
        except Exception:
            meta["display_name"] = node_id
        try:
            meta["datatype"] = str(await node.read_data_type_as_variant_type())
        except Exception:
            meta["datatype"] = "Unknown"
        try:
            meta["access_level"] = [str(item) for item in await node.get_access_level()]
        except Exception:
            meta["access_level"] = []
        return meta
