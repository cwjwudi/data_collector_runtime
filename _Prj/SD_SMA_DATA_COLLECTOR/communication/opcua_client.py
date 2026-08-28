"""基于 asyncua 的 OPC UA 客户端、订阅管理与重连状态机。"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from asyncua import Client, ua
from asyncua.ua import uaerrors

from core.config_models import DataPoint


DataChangeCallback = Callable[[Any], Optional[Awaitable[None]]]


class ConnectionState(str, Enum):
    """OPC UA 会话生命周期。"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"


@dataclass
class _SubscriptionRegistration:
    token: str
    point: DataPoint
    callback: DataChangeCallback
    sampling_interval_ms: float
    handle: Optional[int] = None


class _DataChangeHandler:
    """asyncua 订阅回调适配器；回调中只做轻量队列投递。"""

    def __init__(self, owner: "OpcUaClient"):
        self._owner = owner

    async def datachange_notification(self, node, value, _data) -> None:
        await self._owner._dispatch_data_change(node, value)

    async def status_change_notification(self, status) -> None:
        if getattr(status, "is_bad", lambda: False)():
            await self._owner._mark_disconnected(
                ConnectionError(f"OPC UA 订阅状态异常: {status}")
            )


class OpcUaClient:
    """异步 OPC UA 客户端。

    - 所有网络操作原生 await，不再使用工作线程。
    - 同一端点只运行一个连接/重连流程。
    - 断线后重建会话和已登记的数据变化订阅。
    """

    def __init__(
        self,
        server_url: str,
        max_retries: int = 1000,
        retry_delay: float = 5.0,
        health_check_interval: int = 30,
    ):
        self.server_url = server_url
        self.max_retries = max_retries  # 兼容旧参数；状态机不会达到上限后停止。
        self.retry_delay = max(0.1, float(retry_delay))
        self.health_check_interval = max(0.0, float(health_check_interval))
        self.logger = logging.getLogger(__name__)

        self.operation_timeout = max(
            0.1, float(os.getenv("SD_SMA_OPCUA_OPERATION_TIMEOUT", "4.0"))
        )
        self.connect_timeout = max(
            self.operation_timeout,
            float(
                os.getenv("SD_SMA_OPCUA_CONNECT_TIMEOUT", str(self.operation_timeout))
            ),
        )
        self.max_inflight_requests = max(
            1, int(os.getenv("SD_SMA_OPCUA_MAX_INFLIGHT", "4"))
        )
        self.subscription_period_ms = max(
            20.0, float(os.getenv("SD_SMA_OPCUA_SUBSCRIPTION_PERIOD_MS", "100"))
        )
        self.log_throttle_interval = max(
            1.0, float(os.getenv("SD_SMA_OPCUA_LOG_THROTTLE_INTERVAL", "30"))
        )

        self.client: Optional[Client] = None
        self.connected = False  # 保留给现有状态面板和测试使用。
        self.state = ConnectionState.DISCONNECTED
        self.current_retry_count = 0
        self.is_reconnecting = False

        self._closing = False
        self._connect_lock = asyncio.Lock()
        self._request_semaphore = asyncio.Semaphore(self.max_inflight_requests)
        self._connected_event = asyncio.Event()
        self._reconnect_wakeup = asyncio.Event()
        self._reconnect_task: Optional[asyncio.Task] = None
        self.health_check_task: Optional[asyncio.Task] = None

        self._subscription_lock = asyncio.Lock()
        self._subscription = None
        self._subscription_handler = _DataChangeHandler(self)
        self._subscriptions: Dict[str, _SubscriptionRegistration] = {}
        self._subscription_routes: Dict[str, List[str]] = {}

        self._last_reconnect_failure_log_at = 0.0
        self._suppressed_reconnect_failures = 0
        self._last_write_failure_log_at: Dict[str, float] = {}
        self._suppressed_write_failures: Dict[str, int] = {}

    def _create_client(self) -> Client:
        # 应用层状态机负责重连和重建订阅，避免库内自动重连与应用重连竞争。
        return Client(
            url=self.server_url,
            timeout=self.operation_timeout,
            watchdog_intervall=max(0.2, min(1.0, self.operation_timeout / 2)),
            **(
                {"auto_reconnect": False}
                if "auto_reconnect" in inspect.signature(Client).parameters
                else {}
            ),
        )

    async def connect(self) -> bool:
        """立即尝试建立一次连接；失败后由后台状态机持续重试。"""
        self._closing = False
        await self._ensure_background_tasks()
        if self.is_connected():
            return True
        success = await self._connect_once(reconnecting=False)
        if not success:
            self._reconnect_wakeup.set()
        return success

    async def disconnect(self) -> None:
        """停止后台任务，删除订阅并关闭会话。"""
        self._closing = True
        self.state = ConnectionState.STOPPING
        self.connected = False
        self._connected_event.clear()
        self._reconnect_wakeup.set()

        tasks = [
            task
            for task in (self.health_check_task, self._reconnect_task)
            if task and not task.done() and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.health_check_task = None
        self._reconnect_task = None

        await self._drop_session()
        self.state = ConnectionState.DISCONNECTED
        self.is_reconnecting = False
        self.logger.info("已断开 OPC UA 服务器连接: %s", self.server_url)

    async def _ensure_background_tasks(self) -> None:
        if self._closing:
            return
        if not self._reconnect_task or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(
                self._reconnect_loop(),
                name=f"opcua_reconnect_{self.server_url}",
            )
        if self.health_check_interval > 0 and (
            not self.health_check_task or self.health_check_task.done()
        ):
            self.health_check_task = asyncio.create_task(
                self._health_check_loop(),
                name=f"opcua_health_{self.server_url}",
            )

    async def _connect_once(self, *, reconnecting: bool) -> bool:
        async with self._connect_lock:
            if self._closing:
                return False
            if self.is_connected():
                return True

            self.state = (
                ConnectionState.RECONNECTING
                if reconnecting
                else ConnectionState.CONNECTING
            )
            self.is_reconnecting = reconnecting
            new_client = self._create_client()
            try:
                await asyncio.wait_for(
                    new_client.connect(),
                    timeout=self.connect_timeout,
                )
                # 在暴露 connected 前完成旧会话清理和订阅恢复。
                await self._drop_session()
                self.client = new_client
                self.connected = True
                self.state = ConnectionState.CONNECTED
                await self._restore_subscriptions()
            except asyncio.CancelledError:
                await self._safe_disconnect(new_client)
                raise
            except Exception as exc:  # noqa: BLE001
                await self._safe_disconnect(new_client)
                self.client = None
                self.connected = False
                self.state = ConnectionState.DISCONNECTED
                self._connected_event.clear()
                self._log_reconnect_failure(exc)
                return False
            finally:
                self.is_reconnecting = False

            self._connected_event.set()
            if self._suppressed_reconnect_failures:
                self.logger.info(
                    "OPC UA 连接恢复，之前合并了 %d 次失败日志",
                    self._suppressed_reconnect_failures,
                )
            self._last_reconnect_failure_log_at = 0.0
            self._suppressed_reconnect_failures = 0
            self.current_retry_count = 0
            self.logger.info(
                "%s，已连接到 %s，恢复订阅数=%d",
                "重连成功" if reconnecting else "成功连接 OPC UA 服务器",
                self.server_url,
                len(self._subscriptions),
            )
            return True

    async def _reconnect_loop(self) -> None:
        """单一后台重连状态机，采用有上限的指数退避。"""
        while not self._closing:
            try:
                await self._reconnect_wakeup.wait()
                self._reconnect_wakeup.clear()
                delay = 0.0
                while not self._closing and not self.is_connected():
                    if delay:
                        try:
                            await asyncio.wait_for(
                                self._reconnect_wakeup.wait(), timeout=delay
                            )
                            self._reconnect_wakeup.clear()
                        except asyncio.TimeoutError:
                            pass
                    self.current_retry_count += 1
                    if await self._connect_once(reconnecting=True):
                        break
                    delay = min(
                        self.retry_delay,
                        max(0.5, 2 ** min(self.current_retry_count - 1, 4)),
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                self.logger.error("OPC UA 重连状态机异常: %s", exc, exc_info=True)
                await asyncio.sleep(min(self.retry_delay, 1.0))

    async def _attempt_reconnect(self, *, wait_before_attempt: bool = True) -> bool:
        """兼容现有调用；并发调用通过连接锁合并为一个实际连接尝试。"""
        await self._ensure_background_tasks()
        if self.is_connected():
            return True
        if wait_before_attempt:
            await asyncio.sleep(self.retry_delay)
        self.current_retry_count += 1
        success = await self._connect_once(reconnecting=True)
        if not success:
            self._reconnect_wakeup.set()
        return success

    async def _ensure_connected(self) -> bool:
        if self.is_connected():
            return True
        await self._ensure_background_tasks()
        self._reconnect_wakeup.set()
        try:
            await asyncio.wait_for(
                self._connected_event.wait(),
                timeout=self.connect_timeout + 0.5,
            )
        except asyncio.TimeoutError:
            return False
        return self.is_connected()

    async def _mark_disconnected(
        self,
        error: BaseException,
        expected_client: Optional[Client] = None,
    ) -> None:
        """原子地废弃故障会话并唤醒唯一重连任务。"""
        async with self._connect_lock:
            if expected_client is not None and self.client is not expected_client:
                return
            if self._closing:
                return
            failed_client = self.client
            self.client = None
            self.connected = False
            self.state = ConnectionState.DISCONNECTED
            self._connected_event.clear()
            async with self._subscription_lock:
                self._subscription = None
                self._subscription_routes.clear()
                for registration in self._subscriptions.values():
                    registration.handle = None
        if failed_client:
            await self._safe_disconnect(failed_client)
        self.logger.warning(
            "OPC UA 会话转为断开状态，准备重连: %s: %s",
            type(error).__name__,
            error,
        )
        await self._ensure_background_tasks()
        self._reconnect_wakeup.set()

    async def _drop_session(self) -> None:
        async with self._subscription_lock:
            subscription = self._subscription
            self._subscription = None
            self._subscription_routes.clear()
            for registration in self._subscriptions.values():
                registration.handle = None
        if subscription is not None:
            try:
                await asyncio.wait_for(
                    subscription.delete(), timeout=self.operation_timeout
                )
            except Exception:
                pass

        old_client = self.client
        self.client = None
        self.connected = False
        self._connected_event.clear()
        if old_client:
            await self._safe_disconnect(old_client)

    async def _safe_disconnect(self, client: Client) -> None:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=self.operation_timeout)
        except Exception:
            pass

    async def _run_operation(
        self,
        action: str,
        operation: Callable[[Client], Awaitable[Any]],
    ) -> Any:
        if not await self._ensure_connected():
            raise ConnectionError("OPC UA 客户端未连接且当前重连尚未成功")

        async with self._request_semaphore:
            client = self.client
            if not client or not self.connected:
                raise ConnectionError("OPC UA 客户端不可用")
            try:
                return await asyncio.wait_for(
                    operation(client), timeout=self.operation_timeout
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if self._is_connection_error(exc):
                    await self._mark_disconnected(exc, expected_client=client)
                    if isinstance(exc, asyncio.TimeoutError):
                        raise TimeoutError(
                            f"OPC UA {action}超时（>{self.operation_timeout:.1f}s）"
                        ) from exc
                raise

    async def read_data_points(self, data_points: List[DataPoint]) -> Dict[str, Any]:
        """使用一次 OPC UA Read 请求读取多个点；连接故障后重连并重试一次。"""
        if not data_points:
            return {}
        timestamp = datetime.now()

        async def _batch(client: Client) -> List[Any]:
            return await client.read_values(
                [client.get_node(point.path) for point in data_points]
            )

        try:
            values = await self._run_operation("批量读取", _batch)
        except Exception as exc:  # noqa: BLE001
            if self._is_connection_error(exc):
                self.logger.warning("批量读取遇连接错误，等待重连后重试: %s", exc)
                if await self._ensure_connected():
                    try:
                        values = await self._run_operation("重连后批量读取", _batch)
                    except Exception as retry_exc:  # noqa: BLE001
                        self.logger.warning(
                            "重连后批量读取仍失败，回退逐点读取: %s", retry_exc
                        )
                        return await self._read_data_points_sequential(
                            data_points, timestamp
                        )
                else:
                    raise ConnectionError("OPC UA 重连未在限定时间内成功") from exc
            else:
                self.logger.warning("批量读取失败，回退逐点读取: %s", exc)
                return await self._read_data_points_sequential(data_points, timestamp)

        if len(values) != len(data_points):
            self.logger.warning(
                "批量读取返回数量(%d)与请求(%d)不一致，回退逐点读取",
                len(values),
                len(data_points),
            )
            return await self._read_data_points_sequential(data_points, timestamp)
        return {
            point.name: {
                "value": value,
                "timestamp": timestamp,
                "path": point.path,
            }
            for point, value in zip(data_points, values)
        }

    async def read_value_by_path(self, point_path: str) -> Any:
        """读取单个路径，供诊断/压力测试使用。"""

        async def _read(client: Client) -> Any:
            return await client.get_node(point_path).read_value()

        return await self._run_operation(f"读取节点 {point_path}", _read)

    async def _read_data_points_sequential(
        self,
        data_points: List[DataPoint],
        timestamp: datetime,
    ) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for point in data_points:
            try:

                async def _read(client: Client, path: str = point.path):
                    return await client.get_node(path).read_value()

                value = await self._run_operation(f"读取数据点 {point.name}", _read)
                results[point.name] = {
                    "value": value,
                    "timestamp": timestamp,
                    "path": point.path,
                }
            except Exception as exc:  # noqa: BLE001
                results[point.name] = {
                    "value": None,
                    "timestamp": timestamp,
                    "error": str(exc),
                    "path": point.path,
                }
                if self._is_connection_error(exc):
                    for remaining in data_points[len(results) :]:
                        results[remaining.name] = {
                            "value": None,
                            "timestamp": timestamp,
                            "error": "前序点连接读取失败，本轮已中止",
                            "path": remaining.path,
                        }
                    break
                self.logger.warning("读取数据点 %s 失败: %s", point.name, exc)
        return results

    async def write_scalar_value(
        self,
        point_path: str,
        value: Any,
        variant_type: ua.VariantType,
        value_label: str = "值",
    ) -> bool:
        async def _write(client: Client) -> None:
            node = client.get_node(point_path)
            try:
                access = await node.get_access_level()
                user_access = await node.get_user_access_level()
                if (
                    ua.AccessLevel.CurrentWrite not in access
                    or ua.AccessLevel.CurrentWrite not in user_access
                ):
                    raise PermissionError("节点不可写")
            except PermissionError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "无法读取写权限，继续尝试写入 %s: %s", point_path, exc
                )
            # asyncua 的 write_value(value, variant_type) 默认附带 SourceTimestamp。
            # B&R Embedded OPC UA Server 拒绝带状态/时间戳组合的写请求
            # （BadWriteNotSupported），因此显式构造无时间戳 DataValue。
            data_value = ua.DataValue(ua.Variant(value, variant_type))
            await node.write_value(data_value)

        try:
            await self._run_operation(f"写入{value_label}", _write)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_write_failure(point_path, value_label, exc)
            return False

    async def write_array_value(self, point_path: str, values: list) -> bool:
        return await self.write_scalar_value(
            point_path, list(values), ua.VariantType.Boolean, "布尔数组"
        )

    async def write_boolean_value(self, point_path: str, value: bool) -> bool:
        return await self.write_scalar_value(
            point_path, bool(value), ua.VariantType.Boolean, "布尔值"
        )

    async def write_uint16_value(self, point_path: str, value: int) -> bool:
        return await self.write_scalar_value(
            point_path, int(value), ua.VariantType.UInt16, "UINT16"
        )

    async def write_uint32_value(self, point_path: str, value: int) -> bool:
        return await self.write_scalar_value(
            point_path, int(value), ua.VariantType.UInt32, "UINT32"
        )

    async def subscribe_data_change(
        self,
        point: DataPoint,
        callback: DataChangeCallback,
        *,
        sampling_interval_ms: float = 50.0,
    ) -> str:
        """登记数据变化订阅；当前断线时保留登记并在重连后自动创建。"""
        token = uuid.uuid4().hex
        registration = _SubscriptionRegistration(
            token=token,
            point=point,
            callback=callback,
            sampling_interval_ms=max(0.0, float(sampling_interval_ms)),
        )
        async with self._subscription_lock:
            self._subscriptions[token] = registration
            if self.is_connected():
                await self._activate_registration(registration)
        await self._ensure_background_tasks()
        self._reconnect_wakeup.set()
        self.logger.info(
            "已登记 OPC UA 数据变化订阅: point=%s, path=%s",
            point.name,
            point.path,
        )
        return token

    async def unsubscribe_data_change(self, token: str) -> None:
        async with self._subscription_lock:
            registration = self._subscriptions.pop(token, None)
            if not registration:
                return
            key = registration.point.path
            route = self._subscription_routes.get(key, [])
            self._subscription_routes[key] = [item for item in route if item != token]
            if not self._subscription_routes[key]:
                self._subscription_routes.pop(key, None)
            if self._subscription is not None and registration.handle is not None:
                try:
                    await self._subscription.unsubscribe(registration.handle)
                except Exception as exc:  # noqa: BLE001
                    self.logger.debug("取消订阅失败（会话可能已失效）: %s", exc)

    async def _restore_subscriptions(self) -> None:
        async with self._subscription_lock:
            self._subscription = None
            self._subscription_routes.clear()
            for registration in self._subscriptions.values():
                registration.handle = None
            for registration in self._subscriptions.values():
                await self._activate_registration(registration)

    async def _activate_registration(
        self, registration: _SubscriptionRegistration
    ) -> None:
        client = self.client
        if not client:
            return
        if self._subscription is None:
            self._subscription = await client.create_subscription(
                self.subscription_period_ms,
                self._subscription_handler,
            )
        node = client.get_node(registration.point.path)
        self._subscription_routes.setdefault(registration.point.path, []).append(
            registration.token
        )
        try:
            registration.handle = await self._subscription.subscribe_data_change(
                node,
                queuesize=10,
                sampling_interval=registration.sampling_interval_ms,
            )
        except Exception:
            route = self._subscription_routes.get(registration.point.path, [])
            self._subscription_routes[registration.point.path] = [
                item for item in route if item != registration.token
            ]
            raise

    async def _dispatch_data_change(self, node, value: Any) -> None:
        path = node.nodeid.to_string()
        # asyncua 会把字符串 NodeId 标准化；配置路径通常同样是标准形式。
        tokens = list(self._subscription_routes.get(path, []))
        if not tokens:
            # 某些服务器返回的 NodeId 字符串格式与配置文本略有差异。
            tokens = [
                token
                for token, registration in self._subscriptions.items()
                if registration.point.path == path
                or str(getattr(node, "nodeid", "")) == registration.point.path
            ]
        for token in tokens:
            registration = self._subscriptions.get(token)
            if not registration:
                continue
            try:
                result = registration.callback(value)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                self.logger.error(
                    "订阅回调失败: point=%s, error=%s",
                    registration.point.name,
                    exc,
                    exc_info=True,
                )

    async def _health_check_loop(self) -> None:
        while not self._closing:
            try:
                await asyncio.sleep(max(0.2, self.health_check_interval))
                if not self.is_connected():
                    self._reconnect_wakeup.set()
                    continue

                async def _check(client: Client) -> None:
                    await client.check_connection()

                await self._run_operation("健康检查", _check)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("健康检查发现连接异常: %s", exc)
                self._reconnect_wakeup.set()

    def is_connected(self) -> bool:
        return (
            self.connected
            and self.client is not None
            and self.state == ConnectionState.CONNECTED
        )

    def get_connection_state(self) -> str:
        return self.state.value

    def get_subscription_count(self) -> int:
        """返回当前会话中已成功创建 monitored item 的数量。"""
        if not self.is_connected():
            return 0
        return sum(
            registration.handle is not None
            for registration in self._subscriptions.values()
        )

    def _is_connection_error(self, error: BaseException) -> bool:
        if isinstance(
            error,
            (
                TimeoutError,
                asyncio.TimeoutError,
                ConnectionError,
                OSError,
                uaerrors.BadConnectionClosed,
                uaerrors.BadSessionClosed,
                uaerrors.BadSessionIdInvalid,
                uaerrors.BadSecureChannelClosed,
                uaerrors.BadServerNotConnected,
                uaerrors.BadTimeout,
            ),
        ):
            return True
        text = str(error).lower()
        return any(
            keyword in text
            for keyword in (
                "connection",
                "disconnected",
                "closed",
                "timeout",
                "network",
                "socket",
                "winerror 10054",
                "forcibly closed",
                "broken pipe",
                "session",
                "secure channel",
            )
        )

    def _is_transient_connect_failure(self, error: BaseException) -> bool:
        return self._is_connection_error(error)

    def _log_reconnect_failure(self, error: BaseException) -> None:
        now = time.monotonic()
        if (
            self._last_reconnect_failure_log_at
            and now - self._last_reconnect_failure_log_at < self.log_throttle_interval
        ):
            self._suppressed_reconnect_failures += 1
            self.logger.debug(
                "OPC UA 重连失败（日志已合并）: %s: %s",
                type(error).__name__,
                error,
            )
            return
        self._last_reconnect_failure_log_at = now
        self.logger.warning(
            "OPC UA 连接失败，后台将继续重试: %s: %s",
            type(error).__name__,
            error,
        )

    def _take_throttled_log(
        self,
        last_log_at: Dict[str, float],
        suppressed: Dict[str, int],
        key: str,
    ) -> tuple[bool, int]:
        now = time.monotonic()
        last = last_log_at.get(key, 0.0)
        if not last or now - last >= self.log_throttle_interval:
            suppressed_count = suppressed.pop(key, 0)
            last_log_at[key] = now
            return True, suppressed_count
        suppressed[key] = suppressed.get(key, 0) + 1
        return False, 0

    def _log_write_failure(
        self, point_path: str, value_label: str, error: BaseException
    ) -> None:
        should_log, suppressed = self._take_throttled_log(
            self._last_write_failure_log_at,
            self._suppressed_write_failures,
            point_path,
        )
        if should_log:
            suffix = f"（合并了之前 {suppressed} 次）" if suppressed else ""
            self.logger.warning(
                "写入%s失败: point=%s, error=%s%s",
                value_label,
                point_path,
                error,
                suffix,
            )
