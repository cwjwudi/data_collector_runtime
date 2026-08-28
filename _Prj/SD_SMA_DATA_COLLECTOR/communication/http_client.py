"""
HTTP 客户端模块
负责将数据发送到远程 HTTP 服务器
"""

import aiohttp
import asyncio
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
import json


class HttpClient:
    """HTTP 客户端类"""
    
    def __init__(self, 
                 base_url: str,
                 endpoint: str = '/api/data',
                 timeout: int = 30,
                 max_retries: int = 3,
                 retry_delay: float = 1.0,
                 headers: Optional[Dict[str, str]] = None):
        """
        初始化 HTTP 客户端
        
        Args:
            base_url: HTTP 服务器基础 URL（如：http://localhost:8080）
            endpoint: API 端点路径（如：/api/data）
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            headers: 自定义请求头字典
        """
        self.base_url = base_url.rstrip('/')
        self.endpoint = endpoint.lstrip('/')
        self.full_url = f"{self.base_url}/{self.endpoint}"
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.custom_headers = headers or {}
        self.logger = logging.getLogger(__name__)
        
        # Session 将在首次使用时创建
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 HTTP session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self.logger.debug(f"创建新的 HTTP session: {self.full_url}")
        return self._session
    
    async def send_data(self, data: Dict[str, Any]) -> bool:
        """
        发送数据到 HTTP 服务器
        
        Args:
            data: 要发送的数据字典
            
        Returns:
            bool: 发送是否成功
        """
        for attempt in range(self.max_retries):
            try:
                session = await self._get_session()
                
                # 准备请求头
                headers = {'Content-Type': 'application/json'}
                headers.update(self.custom_headers)
                
                # 发送 POST 请求
                async with session.post(self.full_url, json=data, headers=headers) as response:
                    if response.status == 200:
                        self.logger.info(f"数据成功发送到 HTTP 服务器：{self.full_url}")
                        return True
                    else:
                        error_text = await response.text()
                        self.logger.warning(f"HTTP 服务器返回异常状态码：{response.status}, {error_text}")
                        
                        if attempt < self.max_retries - 1:
                            self.logger.info(f"等待 {self.retry_delay} 秒后重试...")
                            await asyncio.sleep(self.retry_delay)
                        else:
                            self.logger.error(f"发送数据失败，已达到最大重试次数：{response.status}")
                            return False
                            
            except asyncio.TimeoutError:
                self.logger.warning(f"HTTP 请求超时（{self.timeout}秒），第 {attempt + 1} 次重试")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                else:
                    self.logger.error("HTTP 请求超时，已达到最大重试次数")
                    return False
                    
            except aiohttp.ClientError as e:
                self.logger.error(f"HTTP 客户端错误：{e}", exc_info=True)
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                else:
                    return False
                    
            except Exception as e:
                self.logger.error(f"发送数据时发生未知错误：{e}", exc_info=True)
                return False
        
        return False
    
    async def send_batch_data(self, data_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        批量发送数据到 HTTP 服务器
        
        Args:
            data_list: 数据字典列表
            
        Returns:
            Dict[str, Any]: 发送结果统计信息
        """
        results = {
            'total': len(data_list),
            'success': 0,
            'failed': 0,
            'errors': []
        }
        
        for i, data in enumerate(data_list):
            success = await self.send_data(data)
            if success:
                results['success'] += 1
            else:
                results['failed'] += 1
                results['errors'].append(f"第 {i+1} 条数据发送失败")
        
        self.logger.info(f"批量发送完成：成功 {results['success']}/{results['total']}")
        return results
    
    async def close(self) -> None:
        """关闭 HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.info("HTTP session 已关闭")
