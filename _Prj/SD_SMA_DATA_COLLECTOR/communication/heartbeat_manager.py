"""
心跳管理器
负责定时向 OPC UA 服务器写入心跳信号
"""

import asyncio
import logging
import sys
import os
import time
from typing import Dict, List, Optional

# 处理相对导入问题
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_models import Connection, AppConfig
from communication.communication_manager import CommunicationManager


class HeartbeatManager:
    """心跳管理器类 - 管理多个 OPC UA 心跳信号"""
    
    def __init__(self, config: AppConfig, communication_manager: CommunicationManager):
        """
        初始化心跳管理器
        
        Args:
            config: 应用配置
            communication_manager: 通信管理器实例
        """
        self.config = config
        self.comm_manager = communication_manager
        self.logger = logging.getLogger(__name__)
        # 点位名称到 OPC UA 地址映射，用于 heartbeat 通过 points 引用
        self.point_path_map = {point.name: point.path for point in self.config.points}
        
        # 存储所有心跳任务 {connection_name: asyncio.Task}
        self.heartbeat_tasks: Dict[str, asyncio.Task] = {}
        
        # 心跳间隔（秒）
        self.heartbeat_interval = 1.0
        self.log_throttle_interval = max(
            1.0,
            float(os.getenv("SD_SMA_HEARTBEAT_LOG_THROTTLE_INTERVAL", "30")),
        )
        self._last_skip_log_at: Dict[str, float] = {}
        self._suppressed_skip_logs: Dict[str, int] = {}
        self._last_failure_log_at: Dict[str, float] = {}
        self._suppressed_failure_logs: Dict[str, int] = {}
        
    async def start_heartbeats(self) -> None:
        """
        启动所有心跳信号
        
        遍历所有连接配置，如果配置了 heartbeat 字段，则启动对应的心跳任务
        """
        try:
            self.logger.info("开始启动心跳信号...")
            
            if not self.config.connections:
                self.logger.debug("没有连接配置，跳过心跳启动")
                return
            
            for connection in self.config.connections:
                if connection.heartbeat:
                    heartbeat_path = self._resolve_heartbeat_path(connection)
                    if not heartbeat_path:
                        self.logger.error(
                            f"心跳配置无效：{connection.name} -> {connection.heartbeat}，"
                            f"请使用 points 中已定义的点位名称"
                        )
                        continue
                    self.logger.info(
                        f"启动心跳信号：{connection.name}, 点位：{connection.heartbeat}, 地址：{heartbeat_path}"
                    )
                    
                    # 获取对应的通信客户端
                    opcua_client = self.comm_manager.get_client(connection.communication)
                    
                    if not opcua_client:
                        self.logger.error(f"找不到通信客户端：{connection.communication}，无法启动心跳 {connection.name}")
                        continue
                    
                    # 创建心跳任务
                    task = asyncio.create_task(
                        self._heartbeat_loop(connection.name, heartbeat_path, opcua_client),
                        name=f"heartbeat_{connection.name}"
                    )
                    self.heartbeat_tasks[connection.name] = task
                else:
                    self.logger.debug(f"连接 {connection.name} 未配置心跳信号，跳过")
            
            self.logger.info(f"心跳信号启动完成，共 {len(self.heartbeat_tasks)} 个心跳任务")
            
        except Exception as e:
            self.logger.error(f"启动心跳信号时发生错误：{e}", exc_info=True)

    def _resolve_heartbeat_path(self, connection: Connection) -> Optional[str]:
        """将连接 heartbeat 配置解析为 OPC UA 节点地址。"""
        heartbeat_ref = (connection.heartbeat or "").strip()
        if not heartbeat_ref:
            return None

        if heartbeat_ref in self.point_path_map:
            return self.point_path_map[heartbeat_ref]

        # 兼容旧配置：仍允许直接填写 OPC UA 地址，后续建议统一迁移为 points 引用。
        if heartbeat_ref.startswith("ns="):
            self.logger.warning(
                f"连接 {connection.name} 的 heartbeat 仍在使用直接地址配置，"
                f"建议改为 points 点位名称：{heartbeat_ref}"
            )
            return heartbeat_ref

        return None
    
    async def stop_heartbeats(self) -> None:
        """
        停止所有心跳信号
        """
        try:
            self.logger.info("正在停止心跳信号...")
            
            for name, task in self.heartbeat_tasks.items():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    self.logger.info(f"心跳信号已停止：{name}")
            
            self.heartbeat_tasks.clear()
            self.logger.info("所有心跳信号已停止")
            
        except Exception as e:
            self.logger.error(f"停止心跳信号时发生错误：{e}", exc_info=True)
    
    async def _heartbeat_loop(self, connection_name: str, heartbeat_address: str, 
                             opcua_client) -> None:
        """
        心跳循环 - 每隔 1 秒写入一次心跳信号
        
        Args:
            connection_name: 连接名称
            heartbeat_address: 心跳信号的 OPC UA 地址
            opcua_client: OPC UA 客户端实例
        """
        self.logger.info(f"心跳循环已启动：{connection_name}, 地址：{heartbeat_address}")
        
        while True:
            try:
                # 检查客户端是否连接
                if not opcua_client.is_connected():
                    self._log_throttled_warning(
                        self._last_skip_log_at,
                        self._suppressed_skip_logs,
                        connection_name,
                        "通信客户端未连接，跳过心跳写入：%s",
                        connection_name,
                    )
                    await asyncio.sleep(self.heartbeat_interval)
                    continue
                
                # 写入心跳信号（值为 1）
                success = await self._write_heartbeat(opcua_client, heartbeat_address)
                
                if success:
                    self._clear_throttle_state(connection_name)
                    self.logger.debug(f"心跳信号已写入：{connection_name} -> {heartbeat_address}")
                else:
                    self._log_throttled_warning(
                        self._last_failure_log_at,
                        self._suppressed_failure_logs,
                        connection_name,
                        "心跳写入失败：%s -> %s",
                        connection_name,
                        heartbeat_address,
                    )
                
                # 等待下次心跳
                await asyncio.sleep(self.heartbeat_interval)
                
            except asyncio.CancelledError:
                self.logger.info(f"心跳循环已取消：{connection_name}")
                break
            except Exception as e:
                self.logger.error(f"心跳循环发生错误：{connection_name}: {e}", exc_info=True)
                await asyncio.sleep(self.heartbeat_interval)
    
    async def _write_heartbeat(self, opcua_client, heartbeat_address: str) -> bool:
        """
        写入心跳信号到指定地址
        
        Args:
            opcua_client: OPC UA 客户端实例
            heartbeat_address: 心跳信号的 OPC UA 地址
            
        Returns:
            bool: 写入是否成功
        """
        try:
            return await opcua_client.write_uint16_value(heartbeat_address, 1)
            
        except Exception as e:
            self.logger.warning(f"写入心跳信号失败：{heartbeat_address}: {e}", exc_info=True)
            return False

    def _log_throttled_warning(
        self,
        last_log_at: Dict[str, float],
        suppressed: Dict[str, int],
        key: str,
        message: str,
        *args,
    ) -> None:
        now = time.monotonic()
        last = last_log_at.get(key, 0.0)
        if not last or now - last >= self.log_throttle_interval:
            suppressed_count = suppressed.pop(key, 0)
            suffix = f"（期间合并 {suppressed_count} 次同类日志）" if suppressed_count else ""
            self.logger.warning(message + suffix, *args)
            last_log_at[key] = now
            return

        suppressed[key] = suppressed.get(key, 0) + 1
        self.logger.debug(message + "（已合并日志）", *args)

    def _clear_throttle_state(self, connection_name: str) -> None:
        skipped = self._suppressed_skip_logs.pop(connection_name, 0)
        failures = self._suppressed_failure_logs.pop(connection_name, 0)
        self._last_skip_log_at.pop(connection_name, None)
        self._last_failure_log_at.pop(connection_name, None)
        if skipped or failures:
            self.logger.info(
                "心跳恢复：%s，之前合并了 %d 次未连接日志、%d 次写入失败日志",
                connection_name,
                skipped,
                failures,
            )
    
    def get_heartbeat_status(self) -> Dict[str, bool]:
        """
        获取所有心跳任务的运行状态
        
        Returns:
            Dict[str, bool]: 心跳任务名称到运行状态的映射
        """
        status = {}
        for name, task in self.heartbeat_tasks.items():
            status[name] = not task.done() and not task.cancelled()
        return status
