"""
OPC UA feedback writer.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from communication.opcua_client import OpcUaClient


class OpcUaFeedbackWriter:
    """Write scalar feedback values to OPC UA nodes."""

    def __init__(self, opcua_client: OpcUaClient):
        self.opcua_client = opcua_client
        self.logger = logging.getLogger(__name__)

    async def write_udint_feedback(self, node_path: str, value: int) -> bool:
        """Write a UDINT(UInt32) feedback value to a single node."""
        try:
            if not node_path:
                self.logger.warning("反馈节点为空，跳过 UDINT 反馈写入")
                return False

            if value < 0 or value > 0xFFFFFFFF:
                self.logger.error("UDINT 反馈值越界: %s", value)
                return False

            return await self.opcua_client.write_uint32_value(node_path, value)
        except Exception as exc:  # noqa: BLE001
            self.logger.error("写入 UDINT 反馈失败: %s", exc, exc_info=True)
            return False
