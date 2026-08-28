"""
数据采集器
负责根据配置定时或触发式采集数据
"""

import asyncio
import logging
import math
import sys
import os
import time
from typing import Dict, List, Callable, Any, Iterator, Optional, Set, Tuple
from datetime import datetime, timedelta
from collections import Counter

# 处理相对导入问题
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_models import DataGroup, DataPoint, TriggerMode, TriggerType
from communication.opcua_client import OpcUaClient
from communication.communication_manager import CommunicationManager


_LOCAL_CADENCE_EPOCH = datetime(1970, 1, 1)


class _AlignedCadenceState:
    """运行期自然时间节拍状态；不跨进程恢复。"""

    def __init__(self, interval: float, now_wall: datetime):
        self.interval = float(interval)
        self.next_slot_seconds = 0.0
        self.recovery_pending = False
        self.retry_not_before_monotonic = 0.0
        self.rebase(interval, now_wall)

    @staticmethod
    def _wall_seconds(now_wall: datetime) -> float:
        return (now_wall - _LOCAL_CADENCE_EPOCH).total_seconds()

    def rebase(self, interval: float, now_wall: datetime) -> None:
        """从严格晚于当前时间的下一个自然边界开始。"""
        self.interval = float(interval)
        now_seconds = self._wall_seconds(now_wall)
        self.next_slot_seconds = (
            math.floor(now_seconds / self.interval) + 1
        ) * self.interval
        self.recovery_pending = False
        self.retry_not_before_monotonic = 0.0

    def seconds_until_next(self, now_wall: datetime) -> float:
        wall_delay = self.next_slot_seconds - self._wall_seconds(now_wall)
        if wall_delay > 0:
            return wall_delay
        return max(
            wall_delay,
            self.retry_not_before_monotonic - time.monotonic(),
        )

    def retry_ready(self) -> bool:
        return time.monotonic() >= self.retry_not_before_monotonic

    def defer_retry(self) -> None:
        self.retry_not_before_monotonic = time.monotonic() + min(
            1.0, max(0.05, self.interval)
        )

    def due_slots(
        self,
        now_wall: datetime,
        limit: int,
    ) -> tuple[List[datetime], int, int]:
        """返回最近的到期节拍、总到期数和因上限丢弃的旧节拍数。"""
        now_seconds = self._wall_seconds(now_wall)
        if now_seconds + 1e-9 < self.next_slot_seconds:
            return [], 0, 0

        total = int(
            math.floor(
                (now_seconds - self.next_slot_seconds + 1e-9) / self.interval
            )
        ) + 1
        retained = min(total, max(1, int(limit)))
        truncated = total - retained
        first = self.next_slot_seconds + truncated * self.interval
        slots = [
            _LOCAL_CADENCE_EPOCH + timedelta(seconds=first + index * self.interval)
            for index in range(retained)
        ]
        return slots, total, truncated

    def commit(self, total_due: int) -> None:
        self.next_slot_seconds += int(total_due) * self.interval
        self.recovery_pending = False
        self.retry_not_before_monotonic = 0.0


def _is_opcua_transient(exc: BaseException) -> bool:
    """手动断线、PLC 关机、对端不可达等：用简短日志即可，不必打 ERROR 全栈。"""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, OSError):
        code = getattr(exc, "errno", None)
        if code in {10054, 10053, 10051, 10050, 10060, 10061, 110, 111, 113}:
            return True
    return False
class DataCollector:
    """数据采集器类"""
    
    def __init__(self, communication_manager: CommunicationManager):
        """
        初始化数据采集器
        
        Args:
            communication_manager: 通信管理器实例
        """
        self.comm_manager = communication_manager
        self.logger = logging.getLogger(__name__)
        self.data_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self.group_disabled_callbacks: List[Callable[[str], None]] = []
        self.collectors = {}  # 存储各个数据组的采集任务
        self.trigger_reset_confirm_attempts = 3
        self.trigger_reset_confirm_delay = 0.05
        self.trigger_stuck_reset_retry_interval = 1.0
        self.dynamic_interval_poll_seconds = 0.5
        self.group_enable_poll_seconds = 1.0
        self.metrics: Counter[str] = Counter()
        self._interval_warning_state: Dict[str, str] = {}
        self._group_enable_warning_state: Dict[str, str] = {}

    @staticmethod
    def _normalize_group_enable_value(value: Any) -> Optional[bool]:
        """Return a valid external group-enable state, or None for invalid values."""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value == 1:
                return True
            if value == 0:
                return False
        return None

    async def _run_group_collection(
        self,
        group: DataGroup,
        group_points: List[DataPoint],
        opcua_client: OpcUaClient,
        trigger_point: Optional[DataPoint],
        interval_point: Optional[DataPoint],
        variable_group_points: List[DataPoint],
    ) -> None:
        if group.trigger == TriggerType.TIME:
            await self._time_triggered_collection(
                group, group_points, opcua_client, interval_point
            )
            return
        if group.trigger == TriggerType.VARIABLE:
            if group.is_parallel:
                await self._parallel_variable_triggered_collection(
                    group, group_points, trigger_point, opcua_client
                )
            else:
                await self._variable_triggered_collection(
                    group, group_points, trigger_point, opcua_client
                )
            return
        if group.trigger == TriggerType.TIME_AND_VARIABLE:
            if group.trigger_mode == TriggerMode.SUBSCRIPTION:
                await self._time_and_variable_subscription_collection(
                    group,
                    group_points,
                    trigger_point,
                    opcua_client,
                    interval_point,
                    variable_group_points,
                )
            else:
                await self._time_and_variable_collection(
                    group,
                    group_points,
                    trigger_point,
                    opcua_client,
                    interval_point,
                    variable_group_points,
                )
            return
        raise ValueError(f"不支持的数据组触发类型: {group.trigger} ({group.name})")

    async def _enable_controlled_collection(
        self,
        group: DataGroup,
        enable_point: DataPoint,
        group_points: List[DataPoint],
        opcua_client: OpcUaClient,
        trigger_point: Optional[DataPoint],
        interval_point: Optional[DataPoint],
        variable_group_points: List[DataPoint],
    ) -> None:
        """Start/stop one group's collector according to its external OPC UA point."""
        active_task: Optional[asyncio.Task] = None
        previous_state: Optional[bool] = None
        try:
            while True:
                try:
                    result = await opcua_client.read_data_points([enable_point])
                    raw_value = result.get(enable_point.name, {}).get("value")
                    enabled = self._normalize_group_enable_value(raw_value)
                    if enabled is None:
                        signature = repr(raw_value)
                        if self._group_enable_warning_state.get(group.name) != signature:
                            self._group_enable_warning_state[group.name] = signature
                            self.logger.warning(
                                "采集组 %s 的外部启用点 %s 返回无效值 %r；仅接受 1/True 或 0/False，保持上一状态",
                                group.name,
                                enable_point.name,
                                raw_value,
                            )
                        self.metrics["group_enable_invalid"] += 1
                    else:
                        self._group_enable_warning_state.pop(group.name, None)
                        if enabled != previous_state:
                            self.logger.info(
                                "采集组 %s 外部控制状态变为 %s（点位 %s=%r）",
                                group.name,
                                "启用" if enabled else "停用",
                                enable_point.name,
                                raw_value,
                            )
                            self.metrics[
                                "group_enable_transitions_to_enabled"
                                if enabled
                                else "group_enable_transitions_to_disabled"
                            ] += 1

                        if enabled and active_task is None:
                            active_task = asyncio.create_task(
                                self._run_group_collection(
                                    group,
                                    group_points,
                                    opcua_client,
                                    trigger_point,
                                    interval_point,
                                    variable_group_points,
                                )
                            )
                        elif not enabled and active_task is not None:
                            active_task.cancel()
                            await asyncio.gather(active_task, return_exceptions=True)
                            active_task = None
                            self._notify_group_disabled(group.name)
                        previous_state = enabled

                    if active_task is not None and active_task.done():
                        await active_task
                    await asyncio.sleep(self.group_enable_poll_seconds)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.metrics["group_enable_read_failed"] += 1
                    if _is_opcua_transient(exc):
                        self.logger.warning(
                            "采集组 %s 读取外部启用点 %s 失败，保持上一状态: %s",
                            group.name,
                            enable_point.name,
                            exc,
                        )
                    else:
                        self.logger.error(
                            "采集组 %s 外部启用控制发生错误，保持上一状态: %s",
                            group.name,
                            exc,
                            exc_info=True,
                        )
                    await asyncio.sleep(max(1.0, self.group_enable_poll_seconds))
        finally:
            if active_task is not None:
                active_task.cancel()
                await asyncio.gather(active_task, return_exceptions=True)

    async def _read_boolean_trigger_value(
        self,
        trigger_point: DataPoint,
        opcua_client: OpcUaClient,
    ) -> Any:
        try:
            trigger_data = await opcua_client.read_data_points([trigger_point])
            return trigger_data.get(trigger_point.name, {}).get('value')
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "读取触发点 %s 用于复位确认失败: %s",
                trigger_point.name,
                exc,
            )
            return None

    @staticmethod
    def _is_false_trigger_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip().lower() in {"false", "0", "off", ""}
        return value is False or value == 0

    @staticmethod
    def _create_fixed_cadence_anchor(
        now_wall: datetime,
        now_monotonic: float,
    ) -> tuple[float, datetime]:
        anchor_wall = now_wall.replace(microsecond=0)
        anchor_monotonic = now_monotonic - (now_wall - anchor_wall).total_seconds()
        return anchor_monotonic, anchor_wall

    @staticmethod
    def _fixed_cadence_deadline(
        anchor_monotonic: float,
        tick_index: int,
        interval: float,
    ) -> float:
        return anchor_monotonic + tick_index * interval

    @staticmethod
    def _fixed_cadence_collection_time(
        anchor_wall: datetime,
        tick_index: int,
        interval: float,
    ) -> datetime:
        return anchor_wall + timedelta(seconds=tick_index * interval)

    @classmethod
    def _advance_fixed_cadence_tick(
        cls,
        anchor_monotonic: float,
        current_tick_index: int,
        interval: float,
        now_monotonic: float,
    ) -> tuple[int, int]:
        next_tick_index = current_tick_index + 1
        next_deadline = cls._fixed_cadence_deadline(
            anchor_monotonic,
            next_tick_index,
            interval,
        )
        lag = now_monotonic - next_deadline
        if lag < interval:
            return next_tick_index, 0

        skipped_ticks = int(lag // interval)
        return next_tick_index + skipped_ticks, skipped_ticks

    @staticmethod
    def _normalize_collection_interval(value: Any) -> float:
        """Convert an OPC UA interval value to a positive finite number of seconds."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("采集间隔必须是数值")
        interval = float(value)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("采集间隔必须是大于 0 的有限数值")
        return interval

    def _warn_interval_once(self, group: DataGroup, signature: str, message: str, *args: Any) -> None:
        if self._interval_warning_state.get(group.name) == signature:
            return
        self._interval_warning_state[group.name] = signature
        self.logger.warning(message, *args)

    async def _read_collection_interval(
        self,
        group: DataGroup,
        interval_point: Optional[DataPoint],
        opcua_client: OpcUaClient,
        current_interval: float,
    ) -> tuple[float, bool]:
        """Read a dynamic interval and keep the last valid value on any failure."""
        if interval_point is None:
            return current_interval, False

        try:
            result = await opcua_client.read_data_points([interval_point])
            value = result.get(interval_point.name, {}).get("value")
        except Exception as exc:  # noqa: BLE001
            self.metrics["dynamic_interval_read_failed"] += 1
            self._warn_interval_once(
                group,
                f"read:{type(exc).__name__}:{exc}",
                "采集组 %s 读取动态间隔点 %s 失败，继续使用 %.6gs: %s",
                group.name,
                interval_point.name,
                current_interval,
                exc,
            )
            return current_interval, False

        try:
            new_interval = self._normalize_collection_interval(value)
        except ValueError as exc:
            self.metrics["dynamic_interval_invalid"] += 1
            self._warn_interval_once(
                group,
                f"invalid:{value!r}",
                "采集组 %s 的动态间隔点 %s 返回无效值 %r，继续使用 %.6gs: %s",
                group.name,
                interval_point.name,
                value,
                current_interval,
                exc,
            )
            return current_interval, False

        recovered = self._interval_warning_state.pop(group.name, None)
        if recovered is not None:
            self.logger.info(
                "采集组 %s 的动态间隔点 %s 已恢复，当前值 %.6gs",
                group.name,
                interval_point.name,
                new_interval,
            )

        if math.isclose(new_interval, current_interval, rel_tol=0.0, abs_tol=1e-9):
            return current_interval, False

        self.metrics["dynamic_interval_changed"] += 1
        self.logger.info(
            "采集组 %s 动态采集间隔变更: %.6gs -> %.6gs（点位 %s）",
            group.name,
            current_interval,
            new_interval,
            interval_point.name,
        )
        return new_interval, True

    async def _reset_boolean_trigger_with_confirm(
        self,
        group: DataGroup,
        trigger_point: DataPoint,
        opcua_client: OpcUaClient,
        reason: str,
    ) -> bool:
        """
        将布尔触发点复位为 False，并读回确认。
        返回 True 表示已经确认 PLC/OPC UA 侧为 False，可恢复内部上升沿状态。
        """
        attempts = max(1, int(self.trigger_reset_confirm_attempts))
        delay = max(0.0, float(self.trigger_reset_confirm_delay))

        for attempt in range(1, attempts + 1):
            success = await opcua_client.write_boolean_value(trigger_point.path, False)
            if success and delay:
                await asyncio.sleep(delay)

            confirmed_value = await self._read_boolean_trigger_value(trigger_point, opcua_client)
            if success and self._is_false_trigger_value(confirmed_value):
                if attempt > 1 or reason != "上升沿采集后":
                    self.logger.info(
                        "触发点复位并确认成功: group=%s, point=%s, reason=%s, attempt=%s",
                        group.name,
                        trigger_point.name,
                        reason,
                        attempt,
                    )
                else:
                    self.logger.debug(f"已复位触发点：{trigger_point.name}")
                return True

            self.logger.warning(
                "触发点复位未确认: group=%s, point=%s, reason=%s, attempt=%s/%s, "
                "write_success=%s, readback=%r",
                group.name,
                trigger_point.name,
                reason,
                attempt,
                attempts,
                success,
                confirmed_value,
            )
            if attempt < attempts and delay:
                await asyncio.sleep(delay)

        self.logger.error(
            "触发点复位最终失败，后续上升沿可能被卡住: group=%s, point=%s, reason=%s",
            group.name,
            trigger_point.name,
            reason,
        )
        return False
    
    def _extract_parallel_point_values(
        self,
        group_name: str,
        point_name: str,
        point_data: Dict[str, Any],
        triggered_indices: List[int],
    ) -> Optional[Dict[str, Any]]:
        """
        从并行触发读取结果中提取与触发索引对齐的值列表。

        - 数组：按触发索引取值
        - 标量（如全局 BatchCode）：广播到每个触发索引
        - None：跳过该点
        """
        raw_value = point_data.get('value')
        if raw_value is None:
            self.logger.warning(
                f"并行触发组 {group_name} 数据点 {point_name} 值为 None，跳过该点"
            )
            return None

        if isinstance(raw_value, (list, tuple)):
            extracted = [raw_value[i] for i in triggered_indices if i < len(raw_value)]
        else:
            # 标量点（如当前批次号）广播到本次所有触发行
            extracted = [raw_value] * len(triggered_indices)
            self.logger.debug(
                "并行触发组 %s 数据点 %s 为标量，已广播到 %s 个触发索引",
                group_name,
                point_name,
                len(triggered_indices),
            )

        return {
            'value': extracted,
            'timestamp': point_data.get('timestamp'),
            'path': point_data.get('path'),
            'triggered_indices': triggered_indices,
        }

    def _build_parallel_rows(
        self,
        group: DataGroup,
        data_points: List[DataPoint],
        data: Dict[str, Dict[str, Any]],
        triggered_indices: List[int],
        trigger_point: DataPoint,
    ) -> Tuple[List[Dict[str, Any]], Dict[int, List[str]]]:
        """Build complete scalar rows and report every rejected trigger index.

        A trigger index is accepted only when every configured point has a non-None
        value for that index. Scalars are broadcast. Array values are never silently
        truncated: a short array rejects only the affected indices.
        """
        rows: List[Dict[str, Any]] = []
        rejected: Dict[int, List[str]] = {}
        now = datetime.now()
        for index in triggered_indices:
            row_data: Dict[str, Any] = {}
            errors: List[str] = []
            for point in data_points:
                point_data = data.get(point.name)
                if not isinstance(point_data, dict):
                    errors.append(f"{point.name}:missing")
                    continue
                raw_value = point_data.get("value")
                if raw_value is None:
                    errors.append(f"{point.name}:none")
                    continue
                if isinstance(raw_value, (list, tuple)):
                    if index >= len(raw_value):
                        errors.append(f"{point.name}:index_out_of_range({len(raw_value)})")
                        continue
                    value = raw_value[index]
                else:
                    value = raw_value
                if value is None:
                    errors.append(f"{point.name}:none_at_index")
                    continue
                row_data[point.name] = {
                    "value": value,
                    "timestamp": point_data.get("timestamp"),
                    "path": point_data.get("path") or point.path,
                }
            if errors:
                rejected[index] = errors
                continue
            rows.append(
                {
                    "group_name": group.name,
                    "collection_time": now,
                    "trigger_type": "variable",
                    "trigger_point": trigger_point.name,
                    "is_parallel": False,
                    "trigger_index": index,
                    "data": row_data,
                }
            )
        return rows, rejected

    async def _reset_parallel_triggers_with_confirm(
        self,
        group: DataGroup,
        trigger_point: DataPoint,
        current_values: List[Any],
        indices: Set[int],
        opcua_client: OpcUaClient,
        reason: str,
    ) -> Set[int]:
        """Reset selected array indices and return those confirmed False by readback."""
        if not indices:
            return set()
        attempts = max(1, int(self.trigger_reset_confirm_attempts))
        delay = max(0.0, float(self.trigger_reset_confirm_delay))
        desired = list(current_values)
        valid_indices = {index for index in indices if 0 <= index < len(desired)}
        for index in valid_indices:
            desired[index] = False
        remaining = set(valid_indices)
        for attempt in range(1, attempts + 1):
            success = await opcua_client.write_array_value(trigger_point.path, desired)
            if success and delay:
                await asyncio.sleep(delay)
            readback = await self._read_boolean_trigger_value(trigger_point, opcua_client)
            if isinstance(readback, (list, tuple)):
                confirmed_false = {
                    index
                    for index in remaining
                    if index < len(readback) and self._is_false_trigger_value(readback[index])
                }
                # A successful array write followed by True is ambiguous: the PLC
                # may already have emitted the next event. Clearing it a second time
                # would deterministically swallow that event. Treat it as an
                # acknowledged reset plus immediate reassertion; the caller sets the
                # previous state False so the next poll consumes the new high level.
                reasserted = {
                    index
                    for index in remaining
                    if success
                    and index < len(readback)
                    and not self._is_false_trigger_value(readback[index])
                }
                if reasserted:
                    self.metrics["parallel_trigger_reasserted_during_confirm"] += len(reasserted)
                    self.logger.info(
                        "并行触发复位后立即再次置位，将作为新事件留待下一轮采集: "
                        "group=%s, point=%s, indices=%s",
                        group.name,
                        trigger_point.name,
                        sorted(reasserted),
                    )
                remaining -= confirmed_false | reasserted
            if not remaining:
                self.metrics["parallel_trigger_reset_confirmed"] += len(valid_indices)
                return valid_indices
            self.logger.warning(
                "并行触发复位未完全确认: group=%s, point=%s, reason=%s, "
                "attempt=%s/%s, write_success=%s, remaining=%s",
                group.name,
                trigger_point.name,
                reason,
                attempt,
                attempts,
                success,
                sorted(remaining),
            )
            if attempt < attempts and delay:
                await asyncio.sleep(delay)
        self.metrics["parallel_trigger_reset_failed"] += len(remaining)
        return valid_indices - remaining

    def _iter_scalar_collection_rows(self, collection_data: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        """
        将并行采集结果（各数据点 value 为等长列表）拆成多行，每行各点为标量。
        若结构不符合并行列表形态，则原样返回一行。
        """
        data = collection_data.get('data') or {}
        if not collection_data.get('is_parallel') or not data:
            yield collection_data
            return
        lengths: List[int] = []
        for name, info in data.items():
            if not isinstance(info, dict):
                self.logger.warning(
                    f"并行组 {collection_data.get('group_name')} 数据点 {name} 结构异常，不拆分行"
                )
                yield collection_data
                return
            v = info.get('value')
            if not isinstance(v, (list, tuple)):
                self.logger.warning(
                    f"并行组 {collection_data.get('group_name')} 数据点 {name} 值非列表，不拆分行"
                )
                yield collection_data
                return
            lengths.append(len(v))
        if not lengths:
            yield collection_data
            return
        n = lengths[0]
        if any(L != n for L in lengths):
            self.logger.warning(
                f"并行组 {collection_data.get('group_name')} 各点列表长度不一致 {lengths}，不拆分行"
            )
            yield collection_data
            return
        triggered_indices: List[int] = collection_data.get('triggered_indices') or []
        if len(triggered_indices) != n:
            triggered_indices = list(range(n))
        for j in range(n):
            row_data: Dict[str, Any] = {}
            for name, info in data.items():
                v = info['value']
                row_data[name] = {
                    'value': v[j],
                    'timestamp': info.get('timestamp'),
                    'path': info.get('path'),
                }
            yield {
                'group_name': collection_data['group_name'],
                'collection_time': collection_data['collection_time'],
                'trigger_type': collection_data['trigger_type'],
                'trigger_point': collection_data.get('trigger_point'),
                'is_parallel': False,
                'trigger_index': triggered_indices[j],
                'data': row_data,
            }
    
    def register_data_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        注册数据回调函数
        
        Args:
            callback: 当采集到数据时调用的函数
        """
        self.data_callbacks.append(callback)

    def _emit_collection(self, collection_data: Dict[str, Any]) -> None:
        """统一补齐采集元数据后通知下游。"""
        collection_data.setdefault("is_backfill", False)
        for callback in self.data_callbacks:
            callback(collection_data)

    async def _collect_aligned_time_if_due(
        self,
        group: DataGroup,
        data_points: List[DataPoint],
        opcua_client: OpcUaClient,
        cadence: _AlignedCadenceState,
        *,
        context: str,
    ) -> bool:
        """到期时读取一次快照，并将其展开到全部欠拍节拍。"""
        slots_before_read, _, _ = cadence.due_slots(
            datetime.now(), group.max_backfill_ticks
        )
        if not slots_before_read or not cadence.retry_ready():
            return False

        try:
            data = await opcua_client.read_data_points(data_points)
        except asyncio.CancelledError:
            raise
        except Exception:
            cadence.recovery_pending = True
            cadence.defer_retry()
            self.metrics["cadence_recovery_failures"] += 1
            raise

        valid_data = {
            name: info for name, info in data.items() if info.get("value") is not None
        }
        if not valid_data:
            cadence.recovery_pending = True
            cadence.defer_retry()
            self.metrics["cadence_recovery_failures"] += 1
            self.logger.warning(
                "采集组 %s（%s）所有数据点读取失败，保留当前节拍等待恢复补采",
                group.name,
                context,
            )
            return False

        invalid_points = [
            name for name, info in data.items() if info.get("value") is None
        ]
        if invalid_points:
            self.logger.warning(
                "采集组 %s（%s）以下数据点读取失败，已过滤：%s",
                group.name,
                context,
                invalid_points,
            )

        slots, total_due, truncated = cadence.due_slots(
            datetime.now(), group.max_backfill_ticks
        )
        if not slots:
            # 读取期间系统时间向后调整；不提交也不制造重复节拍。
            return False

        is_backfill_batch = cadence.recovery_pending or total_due > 1
        if truncated:
            self.metrics["cadence_backfill_truncated_ticks"] += truncated
            self.logger.warning(
                "采集组 %s 欠拍 %s 条，超过上限 %s；放弃最旧 %s 条，仅补最近节拍",
                group.name,
                total_due,
                group.max_backfill_ticks,
                truncated,
            )

        try:
            for planned_time in slots:
                self._emit_collection(
                    {
                        "group_name": group.name,
                        "collection_time": planned_time,
                        "trigger_type": "time",
                        "is_backfill": is_backfill_batch,
                        "data": valid_data,
                    }
                )
        except Exception:
            cadence.recovery_pending = True
            cadence.defer_retry()
            self.metrics["cadence_recovery_failures"] += 1
            raise

        cadence.commit(total_due)
        if is_backfill_batch:
            self.metrics["cadence_backfill_batches"] += 1
            self.metrics["cadence_backfill_rows"] += len(slots)
            self.logger.warning(
                "采集组 %s 已使用恢复快照补采节拍: rows=%s, first=%s, last=%s",
                group.name,
                len(slots),
                slots[0],
                slots[-1],
            )
        return True

    def register_group_disabled_callback(self, callback: Callable[[str], None]) -> None:
        """Register a callback invoked after an enabled group has fully stopped."""
        self.group_disabled_callbacks.append(callback)

    def _notify_group_disabled(self, group_name: str) -> None:
        for callback in self.group_disabled_callbacks:
            try:
                callback(group_name)
            except Exception:  # noqa: BLE001
                self.metrics["group_disable_callback_failed"] += 1
                self.logger.error(
                    "采集组 %s 停用后的缓存刷新通知失败",
                    group_name,
                    exc_info=True,
                )
    
    async def start_collection(self, data_groups: List[DataGroup], 
                             data_points_dict: Dict[str, DataPoint]) -> None:
        """
        启动数据采集
        
        Args:
            data_groups: 数据组列表
            data_points_dict: 数据点字典 (name -> DataPoint)
        """
        for group in data_groups:
            # 获取该组对应的通信客户端
            opcua_client = self.comm_manager.get_client_for_group(group.name)
            if not opcua_client:
                self.logger.error(f"找不到数据组 {group.name} 对应的通信客户端，跳过该组")
                continue
            
            # 获取该组对应的数据点
            group_points = [data_points_dict[name] for name in group.data_points]
            variable_group_points = []
            for point in group_points:
                source_name = group.variable_point_overrides.get(point.name, point.name)
                source_point = data_points_dict[source_name]
                variable_group_points.append(
                    DataPoint(
                        name=point.name,
                        path=source_point.path,
                        description=point.description,
                        datatype=point.datatype,
                    )
                )
            interval_point = (
                data_points_dict[group.interval_point]
                if group.interval_point
                else None
            )
            trigger_point = (
                data_points_dict[group.trigger_point]
                if group.trigger_point
                else None
            )
            enable_point = (
                data_points_dict[group.enable_point]
                if group.enable_point
                else None
            )

            if enable_point is None:
                task = asyncio.create_task(
                    self._run_group_collection(
                        group,
                        group_points,
                        opcua_client,
                        trigger_point,
                        interval_point,
                        variable_group_points,
                    )
                )
                self.logger.info("启动采集组: %s（未配置外部启停，始终启用）", group.name)
            else:
                task = asyncio.create_task(
                    self._enable_controlled_collection(
                        group,
                        enable_point,
                        group_points,
                        opcua_client,
                        trigger_point,
                        interval_point,
                        variable_group_points,
                    )
                )
                self.logger.info(
                    "启动采集组外部启停监控: %s（点位 %s）",
                    group.name,
                    enable_point.name,
                )
            self.collectors[group.name] = task

    
    async def stop_collection(self) -> None:
        """停止所有数据采集"""
        collectors = list(self.collectors.items())
        for name, task in collectors:
            task.cancel()
            self.logger.info(f"停止采集组: {name}")

        if collectors:
            results = await asyncio.gather(
                *(task for _, task in collectors),
                return_exceptions=True,
            )
            for (name, _), result in zip(collectors, results):
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    self.logger.error("停止采集组 %s 时发生错误: %s", name, result, exc_info=result)
        self.collectors.clear()
    
    async def _time_triggered_collection(
        self,
        group: DataGroup,
        data_points: List[DataPoint],
        opcua_client: OpcUaClient,
        interval_point: Optional[DataPoint] = None,
    ) -> None:
        """时间触发的数据采集；未启用强制对齐时保持原有跳拍语义。"""
        if group.force_cadence_alignment:
            await self._aligned_time_triggered_collection(
                group, data_points, opcua_client, interval_point
            )
            return

        interval = float(group.interval_seconds)
        interval, _ = await self._read_collection_interval(
            group, interval_point, opcua_client, interval
        )
        anchor_monotonic, anchor_wall = self._create_fixed_cadence_anchor(
            datetime.now(), time.monotonic()
        )
        tick_index = 0

        while True:
            try:
                next_deadline = self._fixed_cadence_deadline(
                    anchor_monotonic, tick_index, interval
                )
                wait = next_deadline - time.monotonic()
                while wait > 0:
                    poll_seconds = (
                        max(0.001, float(self.dynamic_interval_poll_seconds))
                        if interval_point is not None
                        else wait
                    )
                    await asyncio.sleep(min(wait, poll_seconds))
                    new_interval, changed = await self._read_collection_interval(
                        group, interval_point, opcua_client, interval
                    )
                    if changed:
                        interval = new_interval
                        anchor_monotonic = time.monotonic()
                        anchor_wall = datetime.now()
                        tick_index = 1
                        next_deadline = self._fixed_cadence_deadline(
                            anchor_monotonic, tick_index, interval
                        )
                    wait = next_deadline - time.monotonic()

                planned_collection_time = self._fixed_cadence_collection_time(
                    anchor_wall, tick_index, interval
                )
                data = await opcua_client.read_data_points(data_points)
                valid_data = {
                    name: info
                    for name, info in data.items()
                    if info.get("value") is not None
                }
                if not valid_data:
                    self.logger.warning(
                        "采集组 %s 所有数据点读取失败，跳过本次采集", group.name
                    )
                else:
                    invalid_points = [
                        name
                        for name, info in data.items()
                        if info.get("value") is None
                    ]
                    if invalid_points:
                        self.logger.warning(
                            "采集组 %s 以下数据点读取失败，已过滤：%s",
                            group.name,
                            invalid_points,
                        )
                    self._emit_collection(
                        {
                            "group_name": group.name,
                            "collection_time": planned_collection_time,
                            "trigger_type": "time",
                            "data": valid_data,
                        }
                    )

                tick_index, skipped_ticks = self._advance_fixed_cadence_tick(
                    anchor_monotonic,
                    tick_index,
                    interval,
                    time.monotonic(),
                )
                if skipped_ticks:
                    self.logger.warning(
                        "采集组 %s 固定节拍落后，跳过 %s 个已错过节拍，下一计划时间=%s",
                        group.name,
                        skipped_ticks,
                        self._fixed_cadence_collection_time(
                            anchor_wall, tick_index, interval
                        ),
                    )
            except asyncio.CancelledError:
                self.logger.info("时间触发采集组 %s 已取消", group.name)
                break
            except Exception as exc:  # noqa: BLE001
                if _is_opcua_transient(exc):
                    self.logger.warning(
                        "时间触发采集组 %s OPC UA 暂不可用，5s 后重试: %s: %s",
                        group.name,
                        type(exc).__name__,
                        exc,
                    )
                else:
                    self.logger.error(
                        "时间触发采集组 %s 发生错误: %s",
                        group.name,
                        exc,
                        exc_info=True,
                    )
                await asyncio.sleep(5)
                tick_index, skipped_ticks = self._advance_fixed_cadence_tick(
                    anchor_monotonic,
                    tick_index,
                    interval,
                    time.monotonic(),
                )
                if skipped_ticks:
                    self.logger.warning(
                        "采集组 %s 异常恢复后跳过 %s 个已错过节拍，下一计划时间=%s",
                        group.name,
                        skipped_ticks,
                        self._fixed_cadence_collection_time(
                            anchor_wall, tick_index, interval
                        ),
                    )

    async def _aligned_time_triggered_collection(
        self,
        group: DataGroup,
        data_points: List[DataPoint],
        opcua_client: OpcUaClient,
        interval_point: Optional[DataPoint] = None,
    ) -> None:
        """按本机自然时间边界采集，并在运行期恢复后补齐欠拍。"""
        interval = float(group.interval_seconds)
        interval, _ = await self._read_collection_interval(
            group, interval_point, opcua_client, interval
        )
        cadence = _AlignedCadenceState(interval, datetime.now())

        while True:
            try:
                wait = cadence.seconds_until_next(datetime.now())
                while wait > 0:
                    poll_seconds = (
                        max(0.001, float(self.dynamic_interval_poll_seconds))
                        if interval_point is not None
                        else wait
                    )
                    await asyncio.sleep(min(wait, poll_seconds))
                    new_interval, changed = await self._read_collection_interval(
                        group, interval_point, opcua_client, interval
                    )
                    if changed:
                        interval = new_interval
                        cadence.rebase(interval, datetime.now())
                    wait = cadence.seconds_until_next(datetime.now())

                collected = await self._collect_aligned_time_if_due(
                    group,
                    data_points,
                    opcua_client,
                    cadence,
                    context="强制对齐定时",
                )
                if not collected:
                    await asyncio.sleep(min(1.0, max(0.05, interval)))
            except asyncio.CancelledError:
                self.logger.info("强制对齐时间采集组 %s 已取消", group.name)
                break
            except Exception as exc:  # noqa: BLE001
                if _is_opcua_transient(exc):
                    self.logger.warning(
                        "强制对齐采集组 %s OPC UA 暂不可用，保留欠拍并等待恢复: %s",
                        group.name,
                        exc,
                    )
                else:
                    self.logger.error(
                        "强制对齐采集组 %s 发生错误，保留欠拍: %s",
                        group.name,
                        exc,
                        exc_info=True,
                    )
                await asyncio.sleep(min(1.0, max(0.05, interval)))

    def _get_variable_trigger_poll_interval(self, group: DataGroup) -> float:
        """获取 variable 类触发模式的触发点轮询间隔（秒）。"""
        if group.trigger_interval_seconds is not None:
            return float(group.trigger_interval_seconds)
        return float(group.interval_seconds)

    async def _open_trigger_subscription(
        self,
        group: DataGroup,
        trigger_point: DataPoint,
        opcua_client: OpcUaClient,
    ) -> tuple[Optional[asyncio.Queue], Optional[str]]:
        """为订阅模式创建本地事件队列；断线重建由 OpcUaClient 负责。"""
        if group.trigger_mode != TriggerMode.SUBSCRIPTION:
            return None, None

        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        def on_value(value: Any) -> None:
            if queue.full():
                try:
                    queue.get_nowait()
                    self.metrics["subscription_events_dropped"] += 1
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(value)
            self.metrics["subscription_events_received"] += 1

        token = await opcua_client.subscribe_data_change(trigger_point, on_value)
        self.logger.info(
            "采集组 %s 已启用触发点订阅: %s",
            group.name,
            trigger_point.name,
        )
        return queue, token

    async def _next_trigger_value(
        self,
        trigger_point: DataPoint,
        opcua_client: OpcUaClient,
        subscription_queue: Optional[asyncio.Queue],
        *,
        retry_timeout: Optional[float] = None,
    ) -> Any:
        """取得下一个触发值；订阅异常待确认时才使用定时读回。"""
        if subscription_queue is not None:
            if retry_timeout is None:
                return await subscription_queue.get()
            try:
                return await asyncio.wait_for(
                    subscription_queue.get(),
                    timeout=max(0.001, retry_timeout),
                )
            except asyncio.TimeoutError:
                self.metrics["subscription_retry_reads"] += 1

        trigger_data = await opcua_client.read_data_points([trigger_point])
        return trigger_data.get(trigger_point.name, {}).get("value")

    async def _time_and_variable_subscription_collection(
        self,
        group: DataGroup,
        data_points: List[DataPoint],
        trigger_point: DataPoint,
        opcua_client: OpcUaClient,
        interval_point: Optional[DataPoint] = None,
        variable_data_points: Optional[List[DataPoint]] = None,
    ) -> None:
        """定时采集与触发订阅共存；等待事件时仍保持固定时间节拍。"""
        interval = float(group.interval_seconds)
        variable_data_points = variable_data_points or data_points
        interval, _ = await self._read_collection_interval(
            group, interval_point, opcua_client, interval
        )
        aligned_cadence = (
            _AlignedCadenceState(interval, datetime.now())
            if group.force_cadence_alignment
            else None
        )
        anchor_monotonic, anchor_wall = self._create_fixed_cadence_anchor(
            datetime.now(), time.monotonic()
        )
        tick_index = 0
        previous_trigger_state = None
        last_stuck_reset_attempt_at = 0.0
        retry_needed = False
        queue, token = await self._open_trigger_subscription(
            group, trigger_point, opcua_client
        )
        if queue is None or token is None:
            raise RuntimeError(f"采集组 {group.name} 未能创建触发订阅")

        async def collect_time_if_due() -> None:
            nonlocal tick_index
            if aligned_cadence is not None:
                await self._collect_aligned_time_if_due(
                    group,
                    data_points,
                    opcua_client,
                    aligned_cadence,
                    context="time_and_variable 订阅定时",
                )
                return
            deadline = self._fixed_cadence_deadline(
                anchor_monotonic, tick_index, interval
            )
            if time.monotonic() < deadline:
                return
            planned_time = self._fixed_cadence_collection_time(
                anchor_wall, tick_index, interval
            )
            data = await opcua_client.read_data_points(data_points)
            valid = {
                name: info
                for name, info in data.items()
                if info.get("value") is not None
            }
            if valid:
                self._emit_collection(
                    {
                        "group_name": group.name,
                        "collection_time": planned_time,
                        "trigger_type": "time",
                        "data": valid,
                    }
                )
            else:
                self.logger.warning(
                    "采集组 %s（time_and_variable 定时）所有数据点读取失败",
                    group.name,
                )
            tick_index, skipped = self._advance_fixed_cadence_tick(
                anchor_monotonic,
                tick_index,
                interval,
                time.monotonic(),
            )
            if skipped:
                self.logger.warning(
                    "采集组 %s（time_and_variable 定时）跳过 %s 个已错过节拍",
                    group.name,
                    skipped,
                )

        try:
            while True:
                try:
                    await collect_time_if_due()
                    if aligned_cadence is not None:
                        wait_timeout = max(
                            0.001,
                            aligned_cadence.seconds_until_next(datetime.now()),
                        )
                    else:
                        deadline = self._fixed_cadence_deadline(
                            anchor_monotonic, tick_index, interval
                        )
                        wait_timeout = max(0.001, deadline - time.monotonic())
                    if interval_point is not None:
                        wait_timeout = min(
                            wait_timeout,
                            max(0.001, float(self.dynamic_interval_poll_seconds)),
                        )
                    if retry_needed:
                        wait_timeout = min(
                            wait_timeout, self.trigger_stuck_reset_retry_interval
                        )

                    got_trigger_event = True
                    try:
                        current_trigger_value = await asyncio.wait_for(
                            queue.get(), timeout=wait_timeout
                        )
                    except asyncio.TimeoutError:
                        got_trigger_event = False
                        current_trigger_value = None

                    new_interval, changed = await self._read_collection_interval(
                        group, interval_point, opcua_client, interval
                    )
                    if changed:
                        interval = new_interval
                        if aligned_cadence is not None:
                            aligned_cadence.rebase(interval, datetime.now())
                        else:
                            anchor_monotonic = time.monotonic()
                            anchor_wall = datetime.now()
                            tick_index = 1

                    if not got_trigger_event and retry_needed:
                        current_trigger_value = await self._next_trigger_value(
                            trigger_point, opcua_client, None
                        )
                        got_trigger_event = True

                    if not got_trigger_event:
                        await collect_time_if_due()
                        continue
                    if current_trigger_value is None:
                        retry_needed = True
                        self.logger.warning(
                            "time_and_variable 采集组 %s 读取触发点失败",
                            group.name,
                        )
                        continue

                    if previous_trigger_state is None:
                        previous_trigger_state = False

                    update_previous_state = True
                    if not previous_trigger_state and current_trigger_value:
                        self.logger.info(
                            "检测到订阅上升沿触发信号（time_and_variable）: %s",
                            group.name,
                        )
                        data = await opcua_client.read_data_points(
                            variable_data_points
                        )
                        valid = {
                            name: info
                            for name, info in data.items()
                            if info.get("value") is not None
                        }
                        if not valid:
                            update_previous_state = False
                            retry_needed = True
                            self.logger.warning(
                                "采集组 %s（订阅触发）所有数据点读取失败，保留触发待重试",
                                group.name,
                            )
                        else:
                            reset_confirmed = True
                            if group.reset_trigger_after_read:
                                reset_confirmed = (
                                    await self._reset_boolean_trigger_with_confirm(
                                        group,
                                        trigger_point,
                                        opcua_client,
                                        "订阅上升沿采集后",
                                    )
                                )
                            if reset_confirmed and group.reset_trigger_after_read:
                                current_trigger_value = False
                                last_stuck_reset_attempt_at = 0.0
                            retry_needed = (
                                group.reset_trigger_after_read
                                and not reset_confirmed
                            )
                            collection_data = {
                                "group_name": group.name,
                                "collection_time": datetime.now(),
                                "trigger_type": "variable",
                                "trigger_point": trigger_point.name,
                                "data": valid,
                            }
                            self._emit_collection(collection_data)
                    elif (
                        group.reset_trigger_after_read
                        and previous_trigger_state
                        and current_trigger_value
                    ):
                        now = time.monotonic()
                        if (
                            now - last_stuck_reset_attempt_at
                            >= self.trigger_stuck_reset_retry_interval
                        ):
                            last_stuck_reset_attempt_at = now
                            reset_confirmed = (
                                await self._reset_boolean_trigger_with_confirm(
                                    group,
                                    trigger_point,
                                    opcua_client,
                                    "订阅触发点持续高电平",
                                )
                            )
                            retry_needed = not reset_confirmed
                            if reset_confirmed:
                                current_trigger_value = False

                    if update_previous_state:
                        previous_trigger_state = current_trigger_value
                    await collect_time_if_due()

                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    retry_needed = True
                    if _is_opcua_transient(exc):
                        self.logger.warning(
                            "time_and_variable 订阅采集组 %s OPC UA 暂不可用: %s",
                            group.name,
                            exc,
                        )
                    else:
                        self.logger.error(
                            "time_and_variable 订阅采集组 %s 异常: %s",
                            group.name,
                            exc,
                            exc_info=True,
                        )
                    await asyncio.sleep(1)
        finally:
            await opcua_client.unsubscribe_data_change(token)
            self.logger.info(
                "time_and_variable 订阅采集组 %s 已取消", group.name
            )

    async def _time_and_variable_collection(
        self,
        group: DataGroup,
        data_points: List[DataPoint],
        trigger_point: DataPoint,
        opcua_client: OpcUaClient,
        interval_point: Optional[DataPoint] = None,
        variable_data_points: Optional[List[DataPoint]] = None,
    ) -> None:
        """
        按 interval_seconds 定时采集；同时以 trigger_interval_seconds 为周期采样 trigger_point，
        上升沿时立即采集一次（行为与同组 variable 模式一致，含可选复位）。
        """
        interval = float(group.interval_seconds)
        variable_data_points = variable_data_points or data_points
        interval, _ = await self._read_collection_interval(
            group, interval_point, opcua_client, interval
        )
        trigger_interval = float(group.trigger_interval_seconds)
        aligned_cadence = (
            _AlignedCadenceState(interval, datetime.now())
            if group.force_cadence_alignment
            else None
        )
        anchor_monotonic, anchor_wall = self._create_fixed_cadence_anchor(
            datetime.now(),
            time.monotonic(),
        )
        tick_index = 0
        previous_trigger_state = None  # None 表示首次读取，只初始化不触发
        last_stuck_reset_attempt_at = 0.0

        async def do_time_collect(planned_collection_time: datetime) -> None:
            data = await opcua_client.read_data_points(data_points)
            valid_data = {
                name: info for name, info in data.items() if info.get('value') is not None
            }
            if not valid_data:
                self.logger.warning(
                    f"采集组 {group.name}（time_and_variable 定时）所有数据点读取失败，跳过本次采集"
                )
            else:
                invalid_points = [name for name, info in data.items() if info.get('value') is None]
                if invalid_points:
                    self.logger.warning(
                        f"采集组 {group.name}（定时）以下数据点读取失败，已过滤：{invalid_points}"
                    )
                collection_data = {
                    'group_name': group.name,
                    'collection_time': planned_collection_time,
                    'trigger_type': 'time',
                    'data': valid_data,
                }
                self._emit_collection(collection_data)

        async def collect_time_if_due() -> None:
            nonlocal tick_index
            if aligned_cadence is not None:
                await self._collect_aligned_time_if_due(
                    group,
                    data_points,
                    opcua_client,
                    aligned_cadence,
                    context="time_and_variable 轮询定时",
                )
                return
            next_deadline = self._fixed_cadence_deadline(
                anchor_monotonic,
                tick_index,
                interval,
            )
            if time.monotonic() < next_deadline:
                return

            planned_collection_time = self._fixed_cadence_collection_time(
                anchor_wall,
                tick_index,
                interval,
            )
            await do_time_collect(planned_collection_time)

            tick_index, skipped_ticks = self._advance_fixed_cadence_tick(
                anchor_monotonic,
                tick_index,
                interval,
                time.monotonic(),
            )
            if skipped_ticks:
                self.logger.warning(
                    "采集组 %s（time_and_variable 定时）固定节拍落后，跳过 %s 个已错过节拍，下一个计划时间=%s",
                    group.name,
                    skipped_ticks,
                    self._fixed_cadence_collection_time(
                        anchor_wall,
                        tick_index,
                        interval,
                    ),
                )

        while True:
            try:
                await collect_time_if_due()

                if aligned_cadence is not None:
                    until_next = aligned_cadence.seconds_until_next(datetime.now())
                else:
                    next_time_deadline = self._fixed_cadence_deadline(
                        anchor_monotonic,
                        tick_index,
                        interval,
                    )
                    until_next = next_time_deadline - time.monotonic()
                sleep_for = min(trigger_interval, max(0.001, until_next))
                await asyncio.sleep(sleep_for)

                new_interval, changed = await self._read_collection_interval(
                    group, interval_point, opcua_client, interval
                )
                if changed:
                    interval = new_interval
                    if aligned_cadence is not None:
                        aligned_cadence.rebase(interval, datetime.now())
                    else:
                        anchor_monotonic = time.monotonic()
                        anchor_wall = datetime.now()
                        tick_index = 1

                trigger_data = await opcua_client.read_data_points([trigger_point])
                current_trigger_value = trigger_data.get(trigger_point.name, {}).get('value', False)

                # 首次读取仅初始化状态，不触发采集
                if previous_trigger_state is None:
                    # A high value is an unacknowledged PLC event even when the
                    # collector has just started or reconnected.
                    previous_trigger_state = False

                update_previous_state = True
                if not previous_trigger_state and current_trigger_value:
                    self.logger.info(
                        f"检测到上升沿触发信号（time_and_variable）: {group.name}"
                    )
                    data = await opcua_client.read_data_points(variable_data_points)
                    valid_data = {
                        name: info for name, info in data.items() if info.get('value') is not None
                    }
                    if not valid_data:
                        self.logger.warning(
                            f"采集组 {group.name}（time_and_variable 变量触发）所有数据点读取失败，跳过本次采集"
                        )
                        if group.reset_trigger_after_read:
                            update_previous_state = False
                            self.logger.warning(
                                "采集组 %s 本轮变量数据读取失败，保持触发内部状态为 False，下一周期继续重试读取",
                                group.name,
                            )
                    else:
                        invalid_points = [
                            name for name, info in data.items() if info.get('value') is None
                        ]
                        if invalid_points:
                            self.logger.warning(
                                f"采集组 {group.name}（变量触发）以下数据点读取失败，已过滤：{invalid_points}"
                            )
                        if group.reset_trigger_after_read:
                            reset_confirmed = await self._reset_boolean_trigger_with_confirm(
                                group,
                                trigger_point,
                                opcua_client,
                                "上升沿采集后",
                            )
                            if reset_confirmed:
                                current_trigger_value = False
                                last_stuck_reset_attempt_at = 0.0
                        else:
                            self.logger.debug(f"根据配置跳过触发点复位：{trigger_point.name}")

                        collection_data = {
                            'group_name': group.name,
                            'collection_time': datetime.now(),
                            'trigger_type': 'variable',
                            'trigger_point': trigger_point.name,
                            'data': valid_data,
                        }
                        self._emit_collection(collection_data)

                elif group.reset_trigger_after_read and previous_trigger_state and current_trigger_value:
                    now_for_reset = time.monotonic()
                    if now_for_reset - last_stuck_reset_attempt_at >= self.trigger_stuck_reset_retry_interval:
                        last_stuck_reset_attempt_at = now_for_reset
                        self.logger.warning(
                            "触发点持续为 True，尝试补复位: group=%s, point=%s",
                            group.name,
                            trigger_point.name,
                        )
                        reset_confirmed = await self._reset_boolean_trigger_with_confirm(
                            group,
                            trigger_point,
                            opcua_client,
                            "触发点持续高电平",
                        )
                        if reset_confirmed:
                            current_trigger_value = False

                if update_previous_state:
                    previous_trigger_state = current_trigger_value

                await collect_time_if_due()

            except asyncio.CancelledError:
                self.logger.info(f"time_and_variable 采集组 {group.name} 已取消")
                break
            except Exception as e:
                if _is_opcua_transient(e):
                    self.logger.warning(
                        "time_and_variable 采集组 %s OPC UA 暂不可用，5s 后重试: %s: %s",
                        group.name,
                        type(e).__name__,
                        e,
                    )
                else:
                    self.logger.error(f"time_and_variable 采集组 {group.name} 发生错误: {e}", exc_info=True)
                await asyncio.sleep(
                    min(1.0, max(0.05, interval))
                    if aligned_cadence is not None
                    else 5
                )
                if aligned_cadence is None:
                    tick_index, skipped_ticks = self._advance_fixed_cadence_tick(
                        anchor_monotonic,
                        tick_index,
                        interval,
                        time.monotonic(),
                    )
                    if skipped_ticks:
                        self.logger.warning(
                            "采集组 %s（time_and_variable 定时）异常恢复后跳过 %s 个已错过节拍，下一个计划时间=%s",
                            group.name,
                            skipped_ticks,
                            self._fixed_cadence_collection_time(
                                anchor_wall,
                                tick_index,
                                interval,
                            ),
                        )
    
    async def _variable_triggered_collection(self, group: DataGroup,
                                           data_points: List[DataPoint],
                                           trigger_point: DataPoint,
                                           opcua_client: OpcUaClient) -> None:
        """变量触发的数据采集 - 实现上升沿触发逻辑"""
        poll_interval = self._get_variable_trigger_poll_interval(group)
        # 记录上一次的触发点状态，用于检测上升沿
        previous_trigger_state = None  # None 表示首次读取，只初始化不触发
        last_stuck_reset_attempt_at = 0.0
        subscription_queue, subscription_token = await self._open_trigger_subscription(
            group, trigger_point, opcua_client
        )
        subscription_retry_needed = False

        while True:
            try:
                # 检查触发点状态
                current_trigger_value = await self._next_trigger_value(
                    trigger_point,
                    opcua_client,
                    subscription_queue,
                    retry_timeout=(
                        self.trigger_stuck_reset_retry_interval
                        if subscription_retry_needed
                        else None
                    ),
                )
                if current_trigger_value is None:
                    subscription_retry_needed = subscription_queue is not None
                    self.logger.warning("变量触发组 %s 读取触发点失败", group.name)
                    if subscription_queue is None:
                        await asyncio.sleep(poll_interval)
                    continue

                # 首次读取仅初始化状态，不触发采集
                if previous_trigger_state is None:
                    # Establish a local false pre-state so a pending high PLC
                    # trigger is collected instead of discarded on startup.
                    previous_trigger_state = False
                    self.logger.debug(f"变量触发组 {group.name} 初始化触发状态: {current_trigger_value}")

                update_previous_state = True
                # 上升沿检测：从False变为True
                if not previous_trigger_state and current_trigger_value:
                    self.logger.info(f"检测到上升沿触发信号: {group.name}")
                    
                    # 读取实际数据
                    data = await opcua_client.read_data_points(data_points)
                                        
                    # 过滤掉值为 None 的数据点
                    valid_data = {name: info for name, info in data.items() if info.get('value') is not None}
                                        
                    # 检查是否有有效数据
                    if not valid_data:
                        self.logger.warning(f"变量触发组 {group.name} 所有数据点读取失败，跳过本次采集")
                        if group.reset_trigger_after_read:
                            update_previous_state = False
                            subscription_retry_needed = subscription_queue is not None
                            self.logger.warning(
                                "变量触发组 %s 本轮数据读取失败，保持触发内部状态为 False，下一周期继续重试读取",
                                group.name,
                            )
                    else:
                        # 记录无效数据点（可选）
                        invalid_points = [name for name, info in data.items() if info.get('value') is None]
                        if invalid_points:
                            self.logger.warning(f"变量触发组 {group.name} 以下数据点读取失败，已过滤：{invalid_points}")
                                            
                        # 根据配置决定是否复位触发点
                        if group.reset_trigger_after_read:
                            reset_confirmed = await self._reset_boolean_trigger_with_confirm(
                                group,
                                trigger_point,
                                opcua_client,
                                "上升沿采集后",
                            )
                            if reset_confirmed:
                                current_trigger_value = False
                                last_stuck_reset_attempt_at = 0.0
                                subscription_retry_needed = False
                            else:
                                subscription_retry_needed = subscription_queue is not None
                        else:
                            self.logger.debug(f"根据配置跳过触发点复位：{trigger_point.name}")
                            subscription_retry_needed = False
                                            
                        # 添加元数据
                        collection_data = {
                            'group_name': group.name,
                            'collection_time': datetime.now(),
                            'trigger_type': 'variable',
                            'trigger_point': trigger_point.name,
                            'data': valid_data
                        }
                                            
                        # 调用回调函数
                        self._emit_collection(collection_data)

                elif group.reset_trigger_after_read and previous_trigger_state and current_trigger_value:
                    now_for_reset = time.monotonic()
                    if now_for_reset - last_stuck_reset_attempt_at >= self.trigger_stuck_reset_retry_interval:
                        last_stuck_reset_attempt_at = now_for_reset
                        self.logger.warning(
                            "触发点持续为 True，尝试补复位: group=%s, point=%s",
                            group.name,
                            trigger_point.name,
                        )
                        reset_confirmed = await self._reset_boolean_trigger_with_confirm(
                            group,
                            trigger_point,
                            opcua_client,
                            "触发点持续高电平",
                        )
                        if reset_confirmed:
                            current_trigger_value = False
                            subscription_retry_needed = False
                        else:
                            subscription_retry_needed = subscription_queue is not None
                
                # 更新上一次的状态
                if update_previous_state:
                    previous_trigger_state = current_trigger_value
                
                # 短暂等待后继续检查
                if subscription_queue is None:
                    await asyncio.sleep(poll_interval)
                
            except asyncio.CancelledError:
                if subscription_token:
                    await opcua_client.unsubscribe_data_change(subscription_token)
                self.logger.info(f"变量触发采集组 {group.name} 已取消")
                break
            except Exception as e:
                if _is_opcua_transient(e):
                    self.logger.warning(
                        "变量触发采集组 %s OPC UA 暂不可用，5s 后重试: %s: %s",
                        group.name,
                        type(e).__name__,
                        e,
                    )
                else:
                    self.logger.error(f"变量触发采集组 {group.name} 发生错误: {e}", exc_info=True)
                await asyncio.sleep(5)  # 错误后等待5秒重试

    async def _parallel_variable_triggered_collection(self, group: DataGroup,
                                                       data_points: List[DataPoint],
                                                       trigger_point: DataPoint,
                                                       opcua_client: OpcUaClient) -> None:
        """并行变量触发的数据采集 - trigger_point 为布尔数组；
        data_points 可为数组（按索引取值）或标量（广播到每个触发索引）。"""
        poll_interval = self._get_variable_trigger_poll_interval(group)
        previous_trigger_state = None
        pending_reset_indices: Set[int] = set()
        subscription_queue, subscription_token = await self._open_trigger_subscription(
            group, trigger_point, opcua_client
        )
        subscription_retry_needed = False

        while True:
            try:
                # 读取触发点数组
                current_trigger_values = await self._next_trigger_value(
                    trigger_point,
                    opcua_client,
                    subscription_queue,
                    retry_timeout=(
                        self.trigger_stuck_reset_retry_interval
                        if subscription_retry_needed
                        else None
                    ),
                )

                if current_trigger_values is None:
                    subscription_retry_needed = subscription_queue is not None
                    self.logger.warning(f"并行触发组 {group.name} 读取触发点失败")
                    if subscription_queue is None:
                        await asyncio.sleep(poll_interval)
                    continue

                current_trigger_values = list(current_trigger_values)

                # 首次读取仅初始化状态，不触发
                if previous_trigger_state is None:
                    # Every high bit represents a row still owned by the PLC.
                    # Consume pending rows after collector restart/reconnect.
                    previous_trigger_state = [False] * len(current_trigger_values)
                    self.logger.info(f"并行触发组 {group.name} 初始化触发状态，数组长度={len(current_trigger_values)}")

                if len(previous_trigger_state) != len(current_trigger_values):
                    self.logger.warning(
                        "并行触发组 %s 触发数组长度变化: previous=%s, current=%s，重新初始化状态",
                        group.name,
                        len(previous_trigger_state),
                        len(current_trigger_values),
                    )
                    previous_trigger_state = list(current_trigger_values)
                    pending_reset_indices = {
                        index for index in pending_reset_indices if index < len(current_trigger_values)
                    }
                    subscription_retry_needed = bool(pending_reset_indices)
                    if subscription_queue is None:
                        await asyncio.sleep(poll_interval)
                    continue

                # Accepted rows whose acknowledgement previously failed must not be
                # collected twice; keep retrying only their PLC reset.
                stuck_pending = {
                    index
                    for index in pending_reset_indices
                    if index < len(current_trigger_values) and current_trigger_values[index]
                }
                if stuck_pending:
                    confirmed = await self._reset_parallel_triggers_with_confirm(
                        group,
                        trigger_point,
                        list(current_trigger_values),
                        stuck_pending,
                        opcua_client,
                        "已入队触发持续高电平",
                    )
                    pending_reset_indices -= confirmed
                    for index in confirmed:
                        current_trigger_values[index] = False
                        previous_trigger_state[index] = False
                    subscription_retry_needed = bool(pending_reset_indices)

                # 检测上升沿索引
                triggered_indices = []
                for i, (prev, curr) in enumerate(zip(previous_trigger_state, current_trigger_values)):
                    if not prev and curr:
                        triggered_indices.append(i)

                if triggered_indices:
                    self.logger.info(f"并行触发组 {group.name} 检测到上升沿，触发索引: {triggered_indices}")

                    self.metrics["parallel_edges_detected"] += len(triggered_indices)
                    # Read once, then validate every trigger index independently.
                    data = await opcua_client.read_data_points(data_points)
                    rows, rejected = self._build_parallel_rows(
                        group, data_points, data, triggered_indices, trigger_point
                    )
                    accepted_indices: Set[int] = set()
                    for row in rows:
                        try:
                            self._emit_collection(row)
                        except Exception as exc:
                            index = int(row["trigger_index"])
                            rejected.setdefault(index, []).append(f"callback:{exc}")
                            self.logger.error(
                                "并行触发组 %s 索引 %s 入队回调失败，不确认触发: %s",
                                group.name,
                                index,
                                exc,
                                exc_info=True,
                            )
                        else:
                            accepted_indices.add(int(row["trigger_index"]))

                    self.metrics["parallel_rows_accepted"] += len(accepted_indices)
                    self.metrics["parallel_rows_rejected"] += len(rejected)
                    for index, reasons in sorted(rejected.items()):
                        self.logger.warning(
                            "并行触发组 %s 索引 %s 数据不完整，不入队且不复位: %s",
                            group.name,
                            index,
                            reasons,
                        )

                    if group.reset_trigger_after_read and accepted_indices:
                        confirmed = await self._reset_parallel_triggers_with_confirm(
                            group,
                            trigger_point,
                            list(current_trigger_values),
                            accepted_indices,
                            opcua_client,
                            "完整数据已进入内存队列",
                        )
                        pending_reset_indices |= accepted_indices - confirmed
                        for index in confirmed:
                            current_trigger_values[index] = False

                    # Rejected indices deliberately retain the pre-edge False state,
                    # so a still-high PLC bit is retried on the next poll.
                    next_previous = list(current_trigger_values)
                    for index in rejected:
                        if index < len(next_previous):
                            next_previous[index] = False
                    for index in pending_reset_indices:
                        if index < len(next_previous):
                            next_previous[index] = True
                    previous_trigger_state = next_previous
                    subscription_retry_needed = bool(rejected or pending_reset_indices)
                else:
                    previous_trigger_state = list(current_trigger_values)
                    subscription_retry_needed = bool(pending_reset_indices)

                if subscription_queue is None:
                    await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                if subscription_token:
                    await opcua_client.unsubscribe_data_change(subscription_token)
                self.logger.info(f"并行触发采集组 {group.name} 已取消")
                break
            except Exception as e:
                if _is_opcua_transient(e):
                    self.logger.warning(
                        "并行触发采集组 %s OPC UA 暂不可用，5s 后重试: %s: %s",
                        group.name,
                        type(e).__name__,
                        e,
                    )
                else:
                    self.logger.error(f"并行触发采集组 {group.name} 发生错误: {e}", exc_info=True)
                await asyncio.sleep(5)

