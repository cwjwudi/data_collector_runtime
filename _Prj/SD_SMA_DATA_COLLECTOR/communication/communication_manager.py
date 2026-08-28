"""
通信管理器
负责管理多个OPC UA通信连接
"""

import asyncio
import logging
import sys
import os
from typing import Dict, List, Optional

# 处理相对导入问题
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_models import Communication, Connection, AppConfig
from communication.opcua_client import OpcUaClient


class CommunicationManager:
    """通信管理器类 - 管理多个OPC UA客户端连接"""
    
    def __init__(self, config: AppConfig):
        """
        初始化通信管理器
        
        Args:
            config: 应用配置
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # 存储所有OPC UA客户端 {communication_name: OpcUaClient}
        self.clients: Dict[str, OpcUaClient] = {}
        
        # 连接状态跟踪
        self.connection_status: Dict[str, bool] = {}
        
    async def initialize_connections(self) -> bool:
        """
        初始化所有通信连接
        
        Returns:
            bool: 初始化是否成功
        """
        try:
            self.logger.info("开始初始化通信连接...")
            
            # 为每个通信创建OPC UA客户端
            for comm in self.config.communications:
                self.logger.info(f"初始化通信: {comm.name} ({comm.type}) -> {comm.server_url}")
                
                # 创建OPC UA客户端
                client = OpcUaClient(
                    server_url=comm.server_url,
                    max_retries=10000,
                    retry_delay=5.0,
                    health_check_interval=30
                )
                
                self.clients[comm.name] = client
                self.connection_status[comm.name] = False
            
            # 连接所有客户端
            connect_results = await asyncio.gather(
                *[self._connect_client(name, client) for name, client in self.clients.items()],
                return_exceptions=True
            )
            
            # 检查连接结果。初次连接失败不再阻止系统启动，后台重连会持续等待 PLC 上线。
            success_count = 0
            for i, (name, result) in enumerate(zip(self.clients.keys(), connect_results)):
                if isinstance(result, Exception):
                    self.logger.error(f"连接通信 {name} 失败: {result}")
                elif result:
                    success_count += 1
                    self.connection_status[name] = True
                    self.logger.info(f"通信 {name} 连接成功")
                else:
                    self.logger.warning(f"通信 {name} 当前未连接，后台将继续自动重连")
            
            self.logger.info(f"通信连接初始化完成: {success_count}/{len(self.clients)} 成功")
            return bool(self.clients)
            
        except Exception as e:
            self.logger.error(f"初始化通信连接时发生错误: {e}", exc_info=True)
            return False
    
    async def _connect_client(self, name: str, client: OpcUaClient) -> bool:
        """
        连接单个客户端
        
        Args:
            name: 通信名称
            client: OPC UA客户端
            
        Returns:
            bool: 连接是否成功
        """
        try:
            return await client.connect()
        except Exception as e:
            self.logger.error(f"连接客户端 {name} 时发生错误: {e}", exc_info=True)
            return False
    
    async def disconnect_all(self) -> None:
        """断开所有连接"""
        self.logger.info("正在断开所有通信连接...")
        
        disconnect_tasks = []
        for name, client in self.clients.items():
            if client:
                disconnect_tasks.append(self._disconnect_client(name, client))
        
        if disconnect_tasks:
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)
        
        self.clients.clear()
        self.connection_status.clear()
        self.logger.info("所有通信连接已断开")
    
    async def _disconnect_client(self, name: str, client: OpcUaClient) -> None:
        """
        断开单个客户端连接
        
        Args:
            name: 通信名称
            client: OPC UA客户端
        """
        try:
            await client.disconnect()
            self.logger.info(f"已断开通信连接: {name}")
        except Exception as e:
            self.logger.error(f"断开通信 {name} 时发生错误: {e}", exc_info=True)
    
    def get_client(self, communication_name: str) -> Optional[OpcUaClient]:
        """
        获取指定通信的客户端
        
        Args:
            communication_name: 通信名称
            
        Returns:
            Optional[OpcUaClient]: OPC UA客户端，如果不存在返回None
        """
        return self.clients.get(communication_name)
    
    def get_client_for_group(self, group_name: str) -> Optional[OpcUaClient]:
        """
        根据数据组名称获取对应的通信客户端
        
        Args:
            group_name: 数据组名称
            
        Returns:
            Optional[OpcUaClient]: 对应的OPC UA客户端
        """
        comm_name = self.config.get_communication_for_group(group_name)
        if comm_name:
            return self.get_client(comm_name)
        return None
    
    def get_groups_for_communication(self, communication_name: str) -> List[str]:
        """
        获取指定通信对应的所有数据组
        
        Args:
            communication_name: 通信名称
            
        Returns:
            List[str]: 数据组名称列表
        """
        return self.config.get_groups_for_communication(communication_name)
    
    def get_connection_status(self) -> Dict[str, bool]:
        """
        获取所有通信的连接状态
        
        Returns:
            Dict[str, bool]: 通信名称到连接状态的映射
        """
        status: Dict[str, bool] = {}
        for name, client in self.clients.items():
            status[name] = bool(client and client.is_connected())
        self.connection_status.update(status)
        return status
    
    def is_connected(self, communication_name: str) -> bool:
        """
        检查指定通信是否已连接
        
        Args:
            communication_name: 通信名称
            
        Returns:
            bool: 是否已连接
        """
        client = self.get_client(communication_name)
        return client is not None and client.is_connected() if client else False
    
    async def reconnect(self, communication_name: str) -> bool:
        """
        重新连接指定的通信
        
        Args:
            communication_name: 通信名称
            
        Returns:
            bool: 重连是否成功
        """
        client = self.get_client(communication_name)
        if not client:
            self.logger.error(f"找不到通信客户端: {communication_name}")
            return False
        
        try:
            # 先断开现有连接
            await client.disconnect()
            
            # 重新连接
            success = await client.connect()
            self.connection_status[communication_name] = success
            
            if success:
                self.logger.info(f"通信 {communication_name} 重连成功")
            else:
                self.logger.error(f"通信 {communication_name} 重连失败")
            
            return success
            
        except Exception as e:
            self.logger.error(f"重连通信 {communication_name} 时发生错误: {e}", exc_info=True)
            self.connection_status[communication_name] = False
            return False
