"""
数据存储处理器
负责接收采集数据并批量写入数据库
"""

import asyncio
import logging
from typing import Dict, List, Any, Optional, Callable, Awaitable, Set
from datetime import datetime
from collections import deque
# 处理相对导入问题
import sys
import os
import time
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import DatabaseManager
from database.persistent_queue import PersistentQueueStore


class DataStorageProcessor:
    """数据存储处理器类"""

    STATUS_SUCCESS = "success"
    STATUS_UNIQUE_CONFLICT = "unique_conflict"
    STATUS_DB_ERROR = "db_error"
    STATUS_OTHER_ERROR = "other_error"
    
    def __init__(
        self,
        db_manager: DatabaseManager,
        batch_size: int = 100,
        points_dict: dict = None,
        insert_feedback_callback: Optional[Callable[[str, str, int], Awaitable[bool]]] = None,
        persistent_queue_config: Optional[Dict[str, Any]] = None,
        max_queue_size: int = 100000,
    ):
        """
        初始化数据存储处理器
        
        Args:
            db_manager: 数据库管理器实例
            batch_size: 默认批量插入大小（用于向后兼容）
            points_dict: 数据点字典 {point_name: DataPoint}，用于获取 datatype 信息
        """
        self.db_manager = db_manager
        self.default_batch_size = batch_size
        self.group_batch_sizes = {}  # 存储各组的batch_size配置
        self.group_unique_key_points = {}  # 存储各组唯一键点配置
        self.group_batch_upsert_configs = {}  # 存储各组批次更新配置
        self.group_batch_time_configs = {}  # 存储各组开批/结批时间点配置
        self.group_open_batch_partition_times = {}  # 存储未结批批次的开批时间
        self.group_data_points = {}  # 存储各组数据点列表
        self.group_partition_interval_years = {}  # 存储各组分表间隔年份
        self.group_insert_feedback_configs = {}  # 存储各组反馈配置
        self.group_indexes_configs = {}  # 存储各组索引配置
        self.points_dict = points_dict or {}  # 数据点配置字典
        self.insert_feedback_callback = insert_feedback_callback
        self.data_queue = deque()
        self.retry_queue = deque()
        self.dead_letter_queue = deque()
        self.max_queue_size = max(1, int(max_queue_size))
        self.persistent_queue_config = dict(persistent_queue_config or {})
        self.persistent_store: Optional[PersistentQueueStore] = None
        self.dropped_records_total = 0
        self._last_drop_log_ts = 0.0
        self._drop_log_interval_seconds = 60.0
        self.processing_task = None
        self.running = False
        self.logger = logging.getLogger(__name__)
        self.column_types_cache = {}  # 缓存列类型信息
        self.ensured_tables = set()  # 记录已确保存在的数据表，避免重复 CREATE TABLE
        self._batch_ready_event: Optional[asyncio.Event] = None # 某组达到 batch大小时唤醒处理循环
        self._group_flush_requests: Set[str] = set()
        self.batch_master_group_name: Optional[str] = None
        self.batch_master_config: Dict[str, Any] = {}
        self.batch_master_unique_key_point: Optional[str] = None
        self.current_batch_context: Optional[Dict[str, Any]] = None
        self._closed_batch_context: Optional[Dict[str, Any]] = None
        self._batch_close_flush_requested = False
        self.retry_interval_seconds = max(
            0.05,
            float(
                self.persistent_queue_config.get("retry_interval_seconds")
                or os.getenv("SD_SMA_DB_RETRY_INTERVAL", "1.0")
            ),
        )
        self.shutdown_flush_timeout_seconds = max(
            0.0, float(os.getenv("SD_SMA_SHUTDOWN_FLUSH_TIMEOUT", "30.0"))
        )
        self._retry_ready_at = 0.0
        self.outbox_cleanup_interval_seconds = max(
            1.0,
            float(self.persistent_queue_config.get("cleanup_interval_seconds", 3600.0)),
        )
        self._next_outbox_cleanup_at = 0.0
        self.metrics: Counter[str] = Counter()
        if self.persistent_queue_config.get("enabled", False):
            self.persistent_store = PersistentQueueStore(
                self.persistent_queue_config.get("path", "runtime/queue/collector_outbox.db"),
                synchronous=self.persistent_queue_config.get("synchronous", "FULL"),
                busy_timeout_ms=self.persistent_queue_config.get("busy_timeout_ms", 5000),
                max_queue_rows=self.persistent_queue_config.get("max_queue_rows", 1_000_000),
                completed_retention_days=self.persistent_queue_config.get("completed_retention_days", 1),
            )
            self.metrics["outbox_rows_recovered_processing"] += self.persistent_store.recovered_on_open
    
    def add_data(self, collection_data: Dict[str, Any]) -> None:
        """
        添加采集数据到队列
        
        Args:
            collection_data: 采集的数据
        """
        queued_data = collection_data
        if self.persistent_store is not None:
            record_id = self.persistent_store.enqueue(collection_data)
            queued_data = dict(collection_data)
            queued_data["_outbox_id"] = record_id
            self.metrics["outbox_rows_accepted"] += 1
        elif len(self.data_queue) >= self.max_queue_size:
            self.data_queue.popleft()
            self.dropped_records_total += 1
            self.metrics["queue_rows_dropped"] += 1
            now = time.monotonic()
            if not self._last_drop_log_ts or now - self._last_drop_log_ts >= self._drop_log_interval_seconds:
                self.logger.warning(
                    "内存队列达到上限，已丢弃最旧数据；累计丢弃=%s, max_queue_size=%s。"
                    "建议启用 persistent_queue。",
                    self.dropped_records_total,
                    self.max_queue_size,
                )
                self._last_drop_log_ts = now
        self.data_queue.append(queued_data)
        self.logger.debug(f"数据已添加到队列，当前队列大小: {len(self.data_queue)}")
        
        group_name = collection_data.get('group_name')
        if group_name is not None:
            if collection_data.get("is_backfill"):
                self._group_flush_requests.add(group_name)
                self.metrics["backfill_flush_requests"] += 1
                ev = self._batch_ready_event
                if ev is not None and not ev.is_set():
                    ev.set()

            # 批次主表的任何一次触发（开批、结批或后续被数据库拒绝）
            # 都必须立即唤醒处理循环，以便强制刷写当前队列快照。
            if self._is_batch_master_record(collection_data):
                ev = self._batch_ready_event
                if ev is not None and not ev.is_set():
                    ev.set()

            batch_size = self.group_batch_sizes.get(group_name, self.default_batch_size)
            group_count = sum(1 for x in self.data_queue if x.get('group_name') == group_name)
            if group_count >= batch_size:
                ev = self._batch_ready_event
                if ev is not None and not ev.is_set():
                    ev.set()

    def request_group_flush(self, group_name: str) -> int:
        """Wake the storage loop to flush one group's current partial batch."""
        queued_rows = sum(
            1 for item in self.data_queue if item.get("group_name") == group_name
        )
        self.metrics["group_flush_requests"] += 1
        if queued_rows <= 0:
            self.metrics["group_flush_empty"] += 1
            self.logger.info("采集组 %s 已停用，当前没有待刷新的批量缓存", group_name)
            return 0

        self._group_flush_requests.add(group_name)
        self.metrics["group_flush_rows_requested"] += queued_rows
        ev = self._batch_ready_event
        if ev is not None and not ev.is_set():
            ev.set()
        self.logger.info(
            "采集组 %s 已停用，请求立即刷新批量缓存: rows=%s",
            group_name,
            queued_rows,
        )
        return queued_rows

    async def start_processing(self) -> None:
        """启动数据处理任务"""
        if self.running:
            return
        
        restored = self._restore_ready_outbox_rows()
        if restored:
            self.logger.warning("从持久化队列恢复待写数据: rows=%s", restored)

        self._run_outbox_maintenance_if_due(force=True)

        self.running = True
        self._batch_ready_event = asyncio.Event()
        self.processing_task = asyncio.create_task(self._process_data_loop())
        self.logger.info("数据存储处理器已启动")
    
    async def stop_processing(self) -> None:
        """Stop processing after a bounded, table-safe flush of both deques."""
        self.running = False
        ev = self._batch_ready_event
        if ev is not None:
            ev.set()
        if self.processing_task:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self.processing_task),
                    timeout=max(0.1, self.shutdown_flush_timeout_seconds),
                )
            except asyncio.TimeoutError:
                self.processing_task.cancel()
                await asyncio.gather(self.processing_task, return_exceptions=True)
            except asyncio.CancelledError:
                pass
            self.processing_task = None

        deadline = time.monotonic() + self.shutdown_flush_timeout_seconds
        while self.data_queue or self.retry_queue:
            pending = list(self.retry_queue) + list(self.data_queue)
            self.retry_queue.clear()
            self.data_queue.clear()
            self.metrics["shutdown_rows_flush_attempted"] += len(pending)
            await self._process_batch(pending, requeue_db_failures=True)
            if not self.data_queue and not self.retry_queue:
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(min(self.retry_interval_seconds, max(0.0, deadline - time.monotonic())))

        remaining = len(self.data_queue) + len(self.retry_queue)
        self.metrics["shutdown_rows_remaining"] = remaining
        if remaining:
            self.logger.error(
                "停机刷新超时，内存中仍有 %s 行未写入数据库: data_queue=%s, retry_queue=%s",
                remaining,
                len(self.data_queue),
                len(self.retry_queue),
            )
        else:
            self.logger.info("停机前所有内存队列数据均已刷新")

        self._batch_ready_event = None
        self._group_flush_requests.clear()
        self.ensured_tables.clear()
        self.group_open_batch_partition_times.clear()
        self.current_batch_context = None
        self._closed_batch_context = None
        self._batch_close_flush_requested = False
        self.logger.info("数据存储处理器已停止，remaining=%s", remaining)

        if self.persistent_store is not None:
            self._run_outbox_maintenance_if_due(force=True)

    def initialize_tables_for_runtime(self) -> bool:
        """
        启动时集中检查需要的数据表。
        启用 batch_upsert 主表时，先确保主表，并确保不分表明细组；
        若能恢复未结批批次上下文，再按开批年份确保分表明细表。
        未配置 batch_upsert 的旧配置继续按当前年份做一次兼容建表。
        """
        if self.batch_master_group_name:
            if not self._ensure_group_table(self.batch_master_group_name, fixed_table=True):
                return False
            ok = self._ensure_non_partitioned_detail_tables()
            if not self._load_open_batch_context():
                return ok
            return self._ensure_detail_tables_for_current_batch() and ok

        ok = True
        for group_name in self.group_data_points:
            table_name = self.db_manager.get_current_table_name(
                group_name,
                partition_time=datetime.now(),
                partition_interval_years=self.group_partition_interval_years.get(group_name, 1),
            )
            ok = self._ensure_group_table(group_name, table_name=table_name) and ok
        return ok

    def _ensure_non_partitioned_detail_tables(self) -> bool:
        """确保 partition_interval_years=0 的明细组固定表存在（不依赖批次上下文）。"""
        ok = True
        for group_name in self.group_data_points:
            if group_name == self.batch_master_group_name:
                continue
            interval = int(self.group_partition_interval_years.get(group_name, 1) or 0)
            if interval != 0:
                continue
            table_name = self.db_manager.get_current_table_name(
                group_name,
                partition_interval_years=0,
            )
            ok = self._ensure_group_table(group_name, table_name=table_name) and ok
        return ok

    def _get_group_column_types(self, group_name: str) -> Dict[str, str]:
        if group_name in self.column_types_cache:
            return self.column_types_cache[group_name]

        column_types: Dict[str, str] = {}
        for point_name in self.group_data_points.get(group_name, []):
            point_config = self.points_dict.get(point_name)
            datatype = getattr(point_config, 'datatype', None) if point_config else None
            column_types[point_name] = (
                self._get_db_type_from_datatype(datatype)
                if datatype
                else "VARCHAR(255)"
            )
        self.column_types_cache[group_name] = column_types
        return column_types

    def _ensure_group_table(
        self,
        group_name: str,
        table_name: Optional[str] = None,
        fixed_table: bool = False,
    ) -> bool:
        if table_name is None:
            table_name = self.db_manager.get_current_table_name(group_name, fixed_table=fixed_table)

        column_types = self._get_group_column_types(group_name)
        indexes = self.group_indexes_configs.get(group_name)
        return self._ensure_table_exists(table_name, column_types, indexes=indexes)

    def _is_table_ready_for_insert(self, table_name: str) -> bool:
        if table_name in self.ensured_tables:
            return True
        # 兼容旧的单元测试/直调场景：未注入 group_data_points 时没有启动期建表上下文。
        return not self.group_data_points

    def _ensure_detail_tables_for_current_batch(self) -> bool:
        batch_context = self.current_batch_context or self._closed_batch_context
        if not batch_context:
            return True

        ok = True
        for group_name in self.group_data_points:
            if group_name == self.batch_master_group_name:
                continue
            table_name = self.db_manager.get_current_table_name(
                group_name,
                partition_time=batch_context["start_time"],
                partition_interval_years=self.group_partition_interval_years.get(group_name, 1),
            )
            ok = self._ensure_group_table(group_name, table_name=table_name) and ok
        return ok

    def _load_open_batch_context(self) -> bool:
        if not self.batch_master_group_name or not self.batch_master_unique_key_point:
            return False

        start_time_point = self.batch_master_config.get("start_time_point")
        end_time_point = self.batch_master_config.get("end_time_point")
        if not start_time_point or not end_time_point:
            return False

        table_name = self.db_manager.get_current_table_name(
            self.batch_master_group_name,
            fixed_table=True,
        )
        sql = (
            f"SELECT `{self.batch_master_unique_key_point}`, `{start_time_point}` "
            f"FROM `{table_name}` "
            f"WHERE `{end_time_point}` IS NULL ORDER BY `id` DESC LIMIT 1"
        )
        try:
            rows = self.db_manager.execute_query(sql)
        except Exception as exc:
            self.logger.warning("读取未结批批次失败，将等待下一次开批数据: %s", exc)
            return False

        if not rows:
            return False

        batch_no, start_time_value = rows[0][0], rows[0][1]
        start_time = self._parse_datetime_value(start_time_value)
        if not start_time:
            self.logger.warning("未结批批次的开批时间无法解析: %s", start_time_value)
            return False

        self.current_batch_context = {
            "batch_no": batch_no,
            "start_time": start_time,
        }
        self.logger.info("恢复未结批批次上下文: batch_no=%s, start_time=%s", batch_no, start_time)
        return True
    
    async def _process_data_loop(self) -> None:
        """数据处理循环"""
        while self.running:
            try:
                self._run_outbox_maintenance_if_due()
                self._restore_ready_outbox_rows()
                if self.retry_queue and time.monotonic() >= self._retry_ready_at:
                    retry_data = list(self.retry_queue)
                    self.retry_queue.clear()
                    self.metrics["db_rows_retried"] += len(retry_data)
                    await self._process_batch(retry_data, requeue_db_failures=True)
                    continue
                if self.data_queue and time.monotonic() < self._retry_ready_at:
                    await self._wait_for_batch_ready_or_timeout(
                        min(1.0, self._retry_ready_at - time.monotonic())
                    )
                    continue
                # 检查是否有足够的数据进行批量处理
                if self._has_enough_data_for_batch():
                    # 按组分别处理数据
                    await self._process_data_by_groups()
                else:
                    # 队列有数据但未达批量：睡眠或等待 add_data 达到 batch 时的唤醒（避免单次涌入多行仍等满 1 秒）
                    if self.data_queue:
                        await self._wait_for_batch_ready_or_timeout(1.0)
                    else:
                        await self._wait_for_batch_ready_or_timeout(0.1)
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"数据处理过程中发生错误: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _wait_for_batch_ready_or_timeout(self, timeout: float) -> None:
        ev = self._batch_ready_event
        if ev is None:
            await asyncio.sleep(timeout)
            return

        wait_ready = asyncio.create_task(ev.wait())
        wait_timeout = asyncio.create_task(asyncio.sleep(timeout))
        tasks = {wait_ready, wait_timeout}

        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            ev.clear()
    
    def _has_enough_data_for_batch(self) -> bool:
        """
        检查是否有足够的数据进行批量处理
        """
        if not self.data_queue:
            return False
            
        # 按组统计数据量
        group_counts = {}
        temp_queue = list(self.data_queue)
        
        for data_item in temp_queue:
            if self._is_batch_master_record(data_item):
                return True

            group_name = data_item['group_name']
            group_counts[group_name] = group_counts.get(group_name, 0) + 1

        if self._group_flush_requests.intersection(group_counts):
            return True
        
        # 检查是否有任何一个组达到了其batch_size
        for group_name, count in group_counts.items():
            batch_size = self.group_batch_sizes.get(group_name, self.default_batch_size)
            if count >= batch_size:
                return True
        
        return False
    
    async def _process_data_by_groups(self) -> None:
        """
        按组分别处理数据
        """
        # 按组分组数据
        grouped_data = {}
        
        # 先收集所有可以处理的数据
        temp_queue = list(self.data_queue)
        processable_data = []
        unprocessable_data = []
        
        group_counts = {}
        requested_groups = set(self._group_flush_requests)
        force_flush_all = False
        for data_item in temp_queue:
            group_name = data_item['group_name']
            group_counts[group_name] = group_counts.get(group_name, 0) + 1
            if self._is_batch_master_record(data_item):
                force_flush_all = True

        if force_flush_all:
            self.logger.info(
                "批次主表触发全队列立即写入: master_group=%s, queue_size=%s, group_counts=%s",
                self.batch_master_group_name,
                len(temp_queue),
                group_counts,
            )
        
        # 分离可处理和不可处理的数据
        for data_item in temp_queue:
            group_name = data_item['group_name']
            batch_size = self.group_batch_sizes.get(group_name, self.default_batch_size)
            
            if (
                force_flush_all
                or group_name in requested_groups
                or group_counts[group_name] >= batch_size
            ):
                processable_data.append(data_item)
            else:
                unprocessable_data.append(data_item)
        
        # 更新队列
        self.data_queue.clear()
        for data_item in unprocessable_data:
            self.data_queue.append(data_item)
        
        # 按组处理可处理的数据
        grouped_processable = {}
        for data_item in processable_data:
            group_name = data_item['group_name']
            if group_name not in grouped_processable:
                grouped_processable[group_name] = []
            grouped_processable[group_name].append(data_item)
        
        process_order = list(grouped_processable.keys())
        if self.batch_master_group_name in grouped_processable:
            process_order.remove(self.batch_master_group_name)
            process_order.insert(0, self.batch_master_group_name)

        # 分别处理每个组的数据
        for group_name in process_order:
            group_data_list = grouped_processable[group_name]
            is_requested_flush = group_name in requested_groups
            self._outbox_mark_processing(group_data_list)
            outcomes = await self._process_group_data(group_name, group_data_list)
            if outcomes.get(self.STATUS_DB_ERROR, 0):
                if self._outbox_mark_retry(group_data_list, "target database write failed"):
                    self._requeue_failed_batch(group_data_list, to_data_queue=True)
                else:
                    self._retain_dead_letter(group_data_list)
                    self._group_flush_requests.discard(group_name)
            elif outcomes.get(self.STATUS_OTHER_ERROR, 0):
                self._outbox_mark_dead_letter(group_data_list, "non-retryable data conversion error")
                self._retain_dead_letter(group_data_list)
                self._group_flush_requests.discard(group_name)
            else:
                self._outbox_mark_completed(group_data_list)
                self._group_flush_requests.discard(group_name)
                if is_requested_flush:
                    self.metrics["group_flush_rows_committed"] += len(group_data_list)
                    self.logger.info(
                        "采集组 %s 停用缓存已立即写入: rows=%s",
                        group_name,
                        len(group_data_list),
                    )

        if self._batch_close_flush_requested:
            self.current_batch_context = None
            self._closed_batch_context = None
            self._batch_close_flush_requested = False
    
    async def _process_batch(
        self,
        batch_data: List[Dict[str, Any]],
        *,
        requeue_db_failures: bool = False,
    ) -> Dict[str, int]:
        """
        处理一批数据
        
        Args:
            batch_data: 批量数据列表
        """
        if not batch_data:
            return self._empty_outcomes()
        
        try:
            # 按组名分组数据，确保每个组的数据单独处理
            grouped_data = {}
            for data_item in batch_data:
                group_name = data_item['group_name']
                if group_name not in grouped_data:
                    grouped_data[group_name] = []
                grouped_data[group_name].append(data_item)
            
            process_order = list(grouped_data.keys())
            if self.batch_master_group_name in grouped_data:
                process_order.remove(self.batch_master_group_name)
                process_order.insert(0, self.batch_master_group_name)

            # 分别处理每个组的数据
            totals = self._empty_outcomes()
            for group_name in process_order:
                group_data_list = grouped_data[group_name]
                self._outbox_mark_processing(group_data_list)
                outcomes = await self._process_group_data(group_name, group_data_list)
                for status, count in outcomes.items():
                    totals[status] += count
                if requeue_db_failures and outcomes.get(self.STATUS_DB_ERROR, 0):
                    if self._outbox_mark_retry(group_data_list, "target database write failed"):
                        self._requeue_failed_batch(group_data_list)
                    else:
                        self._retain_dead_letter(group_data_list)
                elif outcomes.get(self.STATUS_OTHER_ERROR, 0):
                    self._outbox_mark_dead_letter(group_data_list, "non-retryable data conversion error")
                    self._retain_dead_letter(group_data_list)
                else:
                    self._outbox_mark_completed(group_data_list)

            if self._batch_close_flush_requested:
                self.current_batch_context = None
                self._closed_batch_context = None
                self._batch_close_flush_requested = False
            return totals
            
        except Exception as e:
            self.logger.error(f"批处理数据失败: {e}", exc_info=True)
            if requeue_db_failures:
                if self._outbox_mark_retry(batch_data, str(e)):
                    self._requeue_failed_batch(batch_data)
                else:
                    self._retain_dead_letter(batch_data)
            outcomes = self._empty_outcomes()
            outcomes[self.STATUS_DB_ERROR] = len(batch_data)
            return outcomes

    def _empty_outcomes(self) -> Dict[str, int]:
        return {
            self.STATUS_SUCCESS: 0,
            self.STATUS_UNIQUE_CONFLICT: 0,
            self.STATUS_DB_ERROR: 0,
            self.STATUS_OTHER_ERROR: 0,
        }

    @staticmethod
    def _outbox_ids(batch_data: List[Dict[str, Any]]) -> List[str]:
        return [item["_outbox_id"] for item in batch_data if item.get("_outbox_id")]

    def _outbox_mark_processing(self, batch_data: List[Dict[str, Any]]) -> None:
        if self.persistent_store is not None:
            self.persistent_store.mark_processing(
                self._outbox_ids(batch_data),
                self.persistent_queue_config.get("lease_seconds", 60.0),
            )

    def _outbox_mark_completed(self, batch_data: List[Dict[str, Any]]) -> None:
        ids = self._outbox_ids(batch_data)
        if self.persistent_store is not None and ids:
            self.persistent_store.mark_completed(ids)
            self.metrics["outbox_rows_completed"] += len(ids)

    def _outbox_mark_retry(self, batch_data: List[Dict[str, Any]], error: str) -> bool:
        ids = self._outbox_ids(batch_data)
        if self.persistent_store is not None and ids:
            attempts = self.persistent_store.attempts(ids)
            max_attempts = int(self.persistent_queue_config.get("max_attempts", 0) or 0)
            if max_attempts and attempts >= max_attempts:
                self.persistent_store.mark_dead_letter(ids, f"retry limit reached: {error}")
                self.metrics["outbox_rows_dead_letter"] += len(ids)
                return False
            max_delay = float(self.persistent_queue_config.get("max_retry_interval_seconds", 300.0))
            delay = min(self.retry_interval_seconds * (2 ** max(0, attempts - 1)), max_delay)
            self.persistent_store.mark_retry(ids, error, delay)
            self.metrics["outbox_rows_retry"] += len(ids)
        return True

    def _outbox_mark_dead_letter(self, batch_data: List[Dict[str, Any]], error: str) -> None:
        ids = self._outbox_ids(batch_data)
        if self.persistent_store is not None and ids:
            self.persistent_store.mark_dead_letter(ids, error)
            self.metrics["outbox_rows_dead_letter"] += len(ids)

    def _restore_ready_outbox_rows(self) -> int:
        if self.persistent_store is None:
            return 0
        restored = self.persistent_store.load_ready()
        known_ids = {
            item.get("_outbox_id")
            for item in (*self.data_queue, *self.retry_queue, *self.dead_letter_queue)
            if item.get("_outbox_id")
        }
        restored = [item for item in restored if item.get("_outbox_id") not in known_ids]
        self.data_queue.extend(restored)
        self.metrics["outbox_rows_recovered"] += len(restored)
        if restored and self._batch_ready_event is not None:
            self._batch_ready_event.set()
        return len(restored)

    def _run_outbox_maintenance_if_due(self, *, force: bool = False) -> int:
        """Periodically remove expired completed rows while the collector is running."""
        if self.persistent_store is None:
            return 0

        now = time.monotonic()
        if not force and now < self._next_outbox_cleanup_at:
            return 0

        self._next_outbox_cleanup_at = now + self.outbox_cleanup_interval_seconds
        try:
            purged = self.persistent_store.purge_completed()
        except Exception:
            self.metrics["outbox_maintenance_failures"] += 1
            self.logger.error("持久化队列定期清理失败", exc_info=True)
            return 0

        self.metrics["outbox_maintenance_runs"] += 1
        self.metrics["outbox_rows_purged"] += purged
        if purged:
            self.logger.info("持久化队列已清理过期成功记录: rows=%s", purged)
        return purged

    def _requeue_failed_batch(
        self, batch_data: List[Dict[str, Any]], *, to_data_queue: bool = False
    ) -> None:
        if not batch_data:
            return
        target_queue = self.data_queue if to_data_queue else self.retry_queue
        if to_data_queue:
            target_queue.extendleft(reversed(batch_data))
            self.metrics["db_rows_retried"] += len(batch_data)
        else:
            target_queue.extend(batch_data)
        self._retry_ready_at = time.monotonic() + self.retry_interval_seconds
        self.metrics["db_rows_queued_for_retry"] += len(batch_data)
        ev = self._batch_ready_event
        if ev is not None and not ev.is_set():
            ev.set()
        self.logger.warning(
            "数据库批次写入失败，已保留到内存重试队列: rows=%s, retry_queue=%s",
            len(batch_data),
            len(target_queue),
        )

    def _retain_dead_letter(self, batch_data: List[Dict[str, Any]]) -> None:
        if not batch_data:
            return
        self.dead_letter_queue.extend(batch_data)
        self.metrics["db_rows_retained_dead_letter"] += len(batch_data)
        self.logger.error(
            "存在不可自动重试的数据错误，记录已保留在内存 dead-letter 队列: "
            "rows=%s, dead_letter_queue=%s",
            len(batch_data),
            len(self.dead_letter_queue),
        )
    
    async def _process_group_data(
        self, group_name: str, group_data_list: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        处理单个组的数据
        
        Args:
            group_name: 组名
            group_data_list: 该组的数据列表
        """
        try:
            if not group_data_list:
                return self._empty_outcomes()

            # 获取第一个数据项来确定表结构
            sample_data = group_data_list[0]
            # 传递group_name给数据库管理器
            table_name = self._get_table_name_for_data_item(group_name, sample_data)
            unique_key_point = self.group_unique_key_points.get(group_name)
            batch_upsert_config = self.group_batch_upsert_configs.get(group_name)

            # 准备插入数据
            if group_name not in self.column_types_cache:
                self._infer_column_types(sample_data)
            
            # 转换数据格式并逐条处理（支持唯一性检查）
            outcome_counts = {
                self.STATUS_SUCCESS: 0,
                self.STATUS_UNIQUE_CONFLICT: 0,
                self.STATUS_DB_ERROR: 0,
                self.STATUS_OTHER_ERROR: 0,
            }
            runtime_table_checks: set[str] = set()

            # 普通数据组不需要逐条唯一性检查或批次 upsert，先完成转换，
            # 再按目标表和列集合交给数据库驱动 executemany，一批只提交一次事务。
            if not unique_key_point and not batch_upsert_config:
                outcome_counts = await self._bulk_insert_group_data(group_name, group_data_list)
                total_records = sum(outcome_counts.values())
                self.logger.debug(
                    "组 %s 批量插入完成: success=%s, unique_conflict=%s, db_error=%s, other_error=%s, total=%s",
                    group_name,
                    outcome_counts[self.STATUS_SUCCESS],
                    outcome_counts[self.STATUS_UNIQUE_CONFLICT],
                    outcome_counts[self.STATUS_DB_ERROR],
                    outcome_counts[self.STATUS_OTHER_ERROR],
                    total_records,
                )
                await self._write_insert_feedback_by_outcome(group_name, outcome_counts)
                self._record_storage_outcomes(outcome_counts)
                return outcome_counts

            for data_item in group_data_list:
                table_name = self._get_table_name_for_data_item(group_name, data_item)
                if not self._is_table_ready_for_insert(table_name):
                    if table_name in runtime_table_checks:
                        outcome_counts[self.STATUS_DB_ERROR] += 1
                        continue
                    runtime_table_checks.add(table_name)
                    if not await asyncio.to_thread(
                        self._ensure_group_table, group_name, table_name=table_name
                    ):
                        self.logger.error("组 %s 的目标表运行期检查失败: %s", group_name, table_name)
                        outcome_counts[self.STATUS_DB_ERROR] += 1
                        continue

                insert_data = self._convert_to_db_format(data_item)
                if not insert_data:
                    outcome_counts[self.STATUS_OTHER_ERROR] += 1
                    continue

                if unique_key_point:
                    unique_value = insert_data.get(unique_key_point)
                    if not self._has_data_value(unique_value):
                        if batch_upsert_config:
                            start_time_point = batch_upsert_config.get("start_time_point")
                            end_time_point = batch_upsert_config.get("end_time_point")
                            self.logger.error(
                                "组 %s 批次记录缺少有效批次号，拒绝写入，避免按空批次处理: %s=%r, %s=%r, %s=%r",
                                group_name,
                                unique_key_point,
                                unique_value,
                                start_time_point or "start_time",
                                insert_data.get(start_time_point) if start_time_point else None,
                                end_time_point or "end_time",
                                insert_data.get(end_time_point) if end_time_point else None,
                            )
                        else:
                            self.logger.warning(
                                f"组 {group_name} 配置了 unique_key_point={unique_key_point}，"
                                f"但当前记录该值为空，按失败处理"
                            )
                        outcome_counts[self.STATUS_OTHER_ERROR] += 1
                        continue
                    try:
                        exists = await asyncio.to_thread(
                            self.db_manager.record_exists, table_name, unique_key_point, unique_value
                        )
                        if exists:
                            if batch_upsert_config and batch_upsert_config.get("end_time_point"):
                                handled_status = await asyncio.to_thread(
                                    self._handle_batch_upsert_conflict,
                                    group_name,
                                    table_name,
                                    unique_key_point,
                                    unique_value,
                                    insert_data,
                                    batch_upsert_config,
                                )
                                outcome_counts[handled_status] += 1
                            else:
                                outcome_counts[self.STATUS_UNIQUE_CONFLICT] += 1
                                self.logger.warning(
                                    f"组 {group_name} 唯一性冲突，跳过插入: {unique_key_point}={unique_value}"
                                )
                            continue

                        if batch_upsert_config:
                            end_time_point = batch_upsert_config.get("end_time_point")
                            if end_time_point:
                                insert_data[end_time_point] = None
                                self.logger.info(
                                    "组 %s 首次插入批次，强制将 %s 置空: %s=%s",
                                    group_name,
                                    end_time_point,
                                    unique_key_point,
                                    unique_value,
                                )
                    except Exception as query_error:
                        self.logger.error(
                            f"组 {group_name} 唯一性校验失败: {query_error}",
                            exc_info=True
                        )
                        outcome_counts[self.STATUS_DB_ERROR] += 1
                        continue

                if await asyncio.to_thread(self.db_manager.execute_insert, table_name, insert_data):
                    outcome_counts[self.STATUS_SUCCESS] += 1
                    if batch_upsert_config and unique_key_point:
                        end_time_point = batch_upsert_config.get("end_time_point")
                        start_time_point = batch_upsert_config.get("start_time_point")
                        if end_time_point and insert_data.get(end_time_point) is None:
                            self.logger.info(
                                "组 %s 开批成功: %s=%s, %s=%s, table=%s",
                                group_name,
                                unique_key_point,
                                insert_data.get(unique_key_point),
                                start_time_point or "start_time",
                                insert_data.get(start_time_point) if start_time_point else None,
                                table_name,
                            )
                            if group_name == self.batch_master_group_name:
                                self._set_current_batch_context(
                                    insert_data.get(unique_key_point),
                                    insert_data.get(start_time_point) if start_time_point else None,
                                    data_item.get('collection_time'),
                                )
                else:
                    outcome_counts[self.STATUS_DB_ERROR] += 1

            total_records = sum(outcome_counts.values())
            self.logger.debug(
                "组 %s 批量插入完成: success=%s, unique_conflict=%s, db_error=%s, other_error=%s, total=%s, table=%s",
                group_name,
                outcome_counts[self.STATUS_SUCCESS],
                outcome_counts[self.STATUS_UNIQUE_CONFLICT],
                outcome_counts[self.STATUS_DB_ERROR],
                outcome_counts[self.STATUS_OTHER_ERROR],
                total_records,
                table_name,
            )

            await self._write_insert_feedback_by_outcome(group_name, outcome_counts)
            self._record_storage_outcomes(outcome_counts)
            return outcome_counts
            
        except Exception as e:
            self.logger.error(f"处理组 {group_name} 数据失败: {e}", exc_info=True)
            await self._write_insert_feedback_by_outcome(
                group_name,
                {
                    self.STATUS_SUCCESS: 0,
                    self.STATUS_UNIQUE_CONFLICT: 0,
                    self.STATUS_DB_ERROR: 0,
                    self.STATUS_OTHER_ERROR: 1,
                },
            )
            outcomes = self._empty_outcomes()
            outcomes[self.STATUS_DB_ERROR] = len(group_data_list)
            self._record_storage_outcomes(outcomes)
            return outcomes

    def _record_storage_outcomes(self, outcomes: Dict[str, int]) -> None:
        self.metrics["db_rows_committed"] += outcomes.get(self.STATUS_SUCCESS, 0)
        self.metrics["db_rows_unique_conflict"] += outcomes.get(self.STATUS_UNIQUE_CONFLICT, 0)
        self.metrics["db_rows_failed"] += outcomes.get(self.STATUS_DB_ERROR, 0)
        self.metrics["db_rows_dead_letter"] += outcomes.get(self.STATUS_OTHER_ERROR, 0)

    async def _bulk_insert_group_data(
        self,
        group_name: str,
        group_data_list: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """转换普通组记录，并按目标表与列集合执行批量插入。"""
        outcome_counts = {
            self.STATUS_SUCCESS: 0,
            self.STATUS_UNIQUE_CONFLICT: 0,
            self.STATUS_DB_ERROR: 0,
            self.STATUS_OTHER_ERROR: 0,
        }
        rows_by_target: Dict[tuple, List[Dict[str, Any]]] = {}
        runtime_table_checks: set[str] = set()

        for data_item in group_data_list:
            try:
                table_name = self._get_table_name_for_data_item(group_name, data_item)
            except Exception as exc:
                self.logger.error(
                    "组 %s 无法确定批量插入目标表: %s",
                    group_name,
                    exc,
                    exc_info=True,
                )
                outcome_counts[self.STATUS_DB_ERROR] += 1
                continue

            if not self._is_table_ready_for_insert(table_name):
                if table_name in runtime_table_checks:
                    outcome_counts[self.STATUS_DB_ERROR] += 1
                    continue
                runtime_table_checks.add(table_name)
                if not await asyncio.to_thread(
                    self._ensure_group_table, group_name, table_name=table_name
                ):
                    self.logger.error("组 %s 的目标表运行期检查失败: %s", group_name, table_name)
                    outcome_counts[self.STATUS_DB_ERROR] += 1
                    continue

            insert_data = self._convert_to_db_format(data_item)
            if not insert_data:
                outcome_counts[self.STATUS_OTHER_ERROR] += 1
                continue

            target_key = (table_name, tuple(insert_data.keys()))
            rows_by_target.setdefault(target_key, []).append(insert_data)

        for (table_name, _columns), rows in rows_by_target.items():
            inserted_count = await asyncio.to_thread(
                self.db_manager.execute_insert_many, table_name, rows
            )
            if inserted_count == len(rows):
                outcome_counts[self.STATUS_SUCCESS] += inserted_count
                self.logger.debug(
                    "组 %s 批量写入成功: table=%s, rows=%s",
                    group_name,
                    table_name,
                    inserted_count,
                )
            elif isinstance(inserted_count, int) and inserted_count >= 0:
                outcome_counts[self.STATUS_SUCCESS] += inserted_count
                outcome_counts[self.STATUS_DB_ERROR] += len(rows) - inserted_count
                self.logger.error(
                    "组 %s 批量写入数量不完整: table=%s, expected=%s, inserted=%s",
                    group_name,
                    table_name,
                    len(rows),
                    inserted_count,
                )
            else:
                outcome_counts[self.STATUS_DB_ERROR] += len(rows)
                self.logger.error(
                    "组 %s 批量写入失败: table=%s, rows=%s",
                    group_name,
                    table_name,
                    len(rows),
                )

        return outcome_counts

    async def _write_insert_feedback_by_outcome(self, group_name: str, outcome_counts: Dict[str, int]) -> None:
        """
        按组批次处理结果写入反馈点（UDINT）
        """
        feedback_config = self.group_insert_feedback_configs.get(group_name)
        if not feedback_config:
            return

        feedback_point = feedback_config.get("feedback_point")
        if not feedback_point:
            return

        success_count = outcome_counts.get(self.STATUS_SUCCESS, 0)
        unique_conflict_count = outcome_counts.get(self.STATUS_UNIQUE_CONFLICT, 0)
        db_error_count = outcome_counts.get(self.STATUS_DB_ERROR, 0)
        other_error_count = outcome_counts.get(self.STATUS_OTHER_ERROR, 0)
        total_count = success_count + unique_conflict_count + db_error_count + other_error_count

        if total_count == 0:
            return

        if success_count == total_count:
            status_code = feedback_config.get("code_success", 0)
        elif unique_conflict_count > 0:
            status_code = feedback_config.get("code_unique_conflict", 1)
        elif db_error_count > 0:
            status_code = feedback_config.get("code_db_error", 2)
        else:
            status_code = feedback_config.get("code_other_error", 3)

        if not self.insert_feedback_callback:
            self.logger.warning(
                f"组 {group_name} 配置了 insert_feedback，但未设置反馈回调，跳过写入"
            )
            return

        try:
            await self.insert_feedback_callback(group_name, feedback_point, status_code)
        except Exception as callback_error:
            self.logger.error(
                f"组 {group_name} 写入插入反馈失败: {callback_error}",
                exc_info=True
            )

    def _get_batch_time_config(self, group_name: str) -> Optional[Dict[str, Any]]:
        return (
            self.group_batch_time_configs.get(group_name)
            or self.group_batch_upsert_configs.get(group_name)
        )

    def _set_current_batch_context(
        self,
        batch_no: Any,
        start_time_value: Any,
        fallback_time: Optional[datetime] = None,
    ) -> bool:
        start_time = self._parse_datetime_value(start_time_value) or fallback_time
        if not start_time:
            self.logger.error("无法建立批次上下文，开批时间为空或无法解析: %s", start_time_value)
            return False

        self.current_batch_context = {
            "batch_no": batch_no,
            "start_time": start_time,
        }
        self._closed_batch_context = None
        if not self._ensure_detail_tables_for_current_batch():
            self.logger.error("批次明细表检查失败: batch_no=%s, start_time=%s", batch_no, start_time)
            return False
        self.logger.info("当前批次上下文已更新: batch_no=%s, start_time=%s", batch_no, start_time)
        return True

    def _mark_current_batch_closed(
        self,
        batch_no: Any,
        start_time_value: Any,
        fallback_time: Optional[datetime] = None,
    ) -> None:
        if not self.current_batch_context:
            self._set_current_batch_context(batch_no, start_time_value, fallback_time)

        if self.current_batch_context:
            self._closed_batch_context = dict(self.current_batch_context)
            self._ensure_detail_tables_for_current_batch()
        self._batch_close_flush_requested = True

    def _get_batch_context_for_writes(self) -> Optional[Dict[str, Any]]:
        return self.current_batch_context or self._closed_batch_context

    @staticmethod
    def _has_data_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return True

    @staticmethod
    def _get_data_point_value(collection_data: Dict[str, Any], point_name: Optional[str]) -> Any:
        if not point_name:
            return None

        data_points = collection_data.get('data') or {}
        point_data = data_points.get(point_name)
        if isinstance(point_data, dict):
            return point_data.get('value')
        return point_data

    @staticmethod
    def _parse_datetime_value(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        if value is None:
            return None

        text_value = str(value).strip()
        if not text_value:
            return None

        try:
            iso_value = text_value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(iso_value)
            return parsed.replace(tzinfo=None)
        except ValueError:
            pass

        datetime_formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M:%S.%f',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%f',
            '%Y/%m/%d %H:%M:%S',
            '%Y%m%d%H%M%S',
            '%Y-%m-%d',
            '%Y/%m/%d',
            '%Y%m%d',
            'DT#%Y-%m-%d-%H:%M:%S',
            '%a %b %d %H:%M:%S %Y',
            '%a %b  %d %H:%M:%S %Y',
        ]
        for datetime_format in datetime_formats:
            try:
                return datetime.strptime(text_value, datetime_format)
            except ValueError:
                continue

        return None

    def _get_batch_start_time(self, group_name: str, collection_data: Dict[str, Any]) -> Optional[datetime]:
        batch_time_config = self._get_batch_time_config(group_name)
        if not batch_time_config:
            return None

        start_time_point = batch_time_config.get("start_time_point")
        start_time_value = self._get_data_point_value(collection_data, start_time_point)
        return self._parse_datetime_value(start_time_value)

    def _is_batch_close_record(self, collection_data: Dict[str, Any]) -> bool:
        group_name = collection_data.get('group_name')
        if not group_name or group_name != self.batch_master_group_name:
            return False

        batch_time_config = self.batch_master_config or self._get_batch_time_config(group_name)
        if not batch_time_config:
            return False

        end_time_point = batch_time_config.get("end_time_point")
        end_time_value = self._get_data_point_value(collection_data, end_time_point)
        if not self._has_data_value(end_time_value):
            return False

        unique_key_point = self.batch_master_unique_key_point
        if unique_key_point:
            unique_value = self._get_data_point_value(collection_data, unique_key_point)
            if not self._has_data_value(unique_value):
                self.logger.warning(
                    "批次主表结批记录缺少有效批次号，忽略本次结批触发: %s=%r, %s=%r",
                    unique_key_point,
                    unique_value,
                    end_time_point or "end_time",
                    end_time_value,
                )
                return False

        start_time_point = batch_time_config.get("start_time_point")
        start_time_value = self._get_data_point_value(collection_data, start_time_point)
        start_time = self._parse_datetime_value(start_time_value)
        end_time = self._parse_datetime_value(end_time_value)

        if start_time and end_time:
            return end_time > start_time
        return True

    def _is_batch_master_record(self, collection_data: Dict[str, Any]) -> bool:
        """判断记录是否来自已配置的批次主表，不区分开批、结批或处理结果。"""
        group_name = collection_data.get('group_name')
        return bool(
            self.batch_master_group_name
            and group_name == self.batch_master_group_name
        )

    def _get_table_name_for_data_item(self, group_name: str, collection_data: Dict[str, Any]) -> str:
        if group_name == self.batch_master_group_name:
            return self.db_manager.get_current_table_name(group_name, fixed_table=True)

        interval = int(self.group_partition_interval_years.get(group_name, 1) or 0)
        # 不分表组不依赖批次开批年份，直接使用固定表名
        if interval == 0:
            return self.db_manager.get_current_table_name(
                group_name,
                partition_interval_years=0,
            )

        if self.batch_master_group_name:
            batch_context = self._get_batch_context_for_writes()
            if not batch_context:
                raise RuntimeError(
                    f"数据组 {group_name} 没有可用的批次上下文，无法确定分表年份"
                )
            return self.db_manager.get_current_table_name(
                group_name,
                partition_time=batch_context["start_time"],
                partition_interval_years=interval,
            )

        partition_time = self._get_batch_start_time(group_name, collection_data)
        if partition_time is None:
            partition_time = collection_data.get('collection_time')

        return self.db_manager.get_current_table_name(
            group_name,
            partition_time=partition_time,
            partition_interval_years=interval,
        )
    
    def _infer_column_types(self, sample_data: Dict[str, Any]) -> Dict[str, str]:
        """
        推断列的数据类型，优先使用配置中的 datatype
        
        Args:
            sample_data: 样本数据
            
        Returns:
            Dict[str, str]: 列名到数据类型的映射
        """
        group_name = sample_data['group_name']
        
        if group_name in self.column_types_cache:
            return self.column_types_cache[group_name]
        
        column_types = {}
        data_points = sample_data['data']
        
        for point_name, point_data in data_points.items():
            value = point_data.get('value')
            
            # 优先使用配置中的 datatype
            if point_name in self.points_dict:
                point_config = self.points_dict[point_name]
                datatype = getattr(point_config, 'datatype', None)
                
                if datatype:
                    # 根据配置的 datatype 确定数据库类型
                    column_type = self._get_db_type_from_datatype(datatype)
                    column_types[point_name] = column_type
                    self.logger.debug(f"列类型从配置推断 (point: {point_name}, datatype: {datatype}, db_type: {column_type})")
                    continue
            
            # 如果没有配置 datatype，回退到基于值的推断
            if value is not None:
                if isinstance(value, bool):
                    column_types[point_name] = "BOOLEAN"
                elif isinstance(value, int):
                    column_types[point_name] = "INTEGER"
                elif isinstance(value, float):
                    column_types[point_name] = "DOUBLE"
                elif isinstance(value, datetime):
                    column_types[point_name] = "DATETIME"
                else:
                    column_types[point_name] = "VARCHAR(255)"
            else:
                column_types[point_name] = "VARCHAR(255)"
        
        self.column_types_cache[group_name] = column_types
        self.logger.info(f"推断列类型: {column_types}")
        return column_types
    
    def _get_db_type_from_datatype(self, datatype: str) -> str:
        """
        根据配置的 datatype 转换为数据库类型
        
        Args:
            datatype: 数据类型字符串
            
        Returns:
            str: 数据库类型字符串
        """
        datatype_lower = datatype.lower().strip()
        
        # datetime 类型映射
        if datatype_lower == 'datetime':
            return 'DATETIME'
        
        # 整数类型映射
        elif datatype_lower in ['int', 'integer']:
            return 'INTEGER'
        
        # 浮点数类型映射
        elif datatype_lower in ['float', 'double', 'real']:
            return 'DOUBLE'
        
        # 字符串类型映射
        elif datatype_lower in ['str', 'string', 'varchar', 'text']:
            return 'VARCHAR(255)'
        
        # 布尔类型映射
        elif datatype_lower in ['bool', 'boolean']:
            return 'BOOLEAN'
        
        # 默认返回 VARCHAR
        else:
            self.logger.warning(f"未知的 datatype: {datatype}，使用 VARCHAR(255)")
            return 'VARCHAR(255)'
    
    def _ensure_table_exists(self, table_name: str, column_types: Dict[str, str],
                              indexes: Optional[List[Dict[str, Any]]] = None) -> bool:
        """
        确保数据表存在

        Args:
            table_name: 表名
            column_types: 列类型定义
            indexes: 索引配置列表

        Returns:
            bool: 表是否存在或创建成功
        """
        try:
            if table_name not in self.ensured_tables:
                # 首次使用该表时尝试创建（IF NOT EXISTS）
                success = self.db_manager.create_data_table(table_name, column_types)
                if not success:
                    return False
                self.ensured_tables.add(table_name)

            # 始终尝试确保索引存在（CREATE INDEX IF NOT EXISTS 幂等）
            if indexes:
                self.db_manager.create_indexes(table_name, indexes)

            return True

        except Exception as e:
            self.logger.error(f"确保表存在时出错: {e}", exc_info=True)
            return False
    
    def _convert_to_db_format(self, collection_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        将采集数据转换为数据库格式
        
        Args:
            collection_data: 采集的数据
            
        Returns:
            Optional[Dict[str, Any]]: 转换后的数据，失败时返回None
        """
        try:
            db_data = {}
            collection_time = collection_data['collection_time']
            
            # 添加通用字段
            db_data['collection_time'] = collection_time
            db_data['is_backfill'] = 1 if collection_data.get('is_backfill') else 0
            
            # 添加数据点值
            data_points = collection_data['data']
            for point_name, point_data in data_points.items():
                value = point_data.get('value')
                
                # 如果配置了 datatype，则进行类型转换
                if point_name in self.points_dict:
                    point_config = self.points_dict[point_name]
                    datatype = getattr(point_config, 'datatype', None)
                    
                    if datatype and value is not None:
                        # 根据 datatype 进行类型转换
                        converted_value = self._convert_value_by_datatype(value, datatype, point_name)
                        db_data[point_name] = converted_value
                    else:
                        # 没有配置 datatype 或值为 None，直接存储
                        db_data[point_name] = value
                else:
                    # 数据点不在配置中，直接存储
                    db_data[point_name] = value
            
            return db_data
            
        except Exception as e:
            self.logger.error(f"数据格式转换失败: {e}", exc_info=True)
            return None
    
    def _convert_value_by_datatype(self, value: Any, datatype: str, point_name: str) -> Any:
        """
        根据 datatype 转换值类型
        
        Args:
            value: 原始值
            datatype: 数据类型字符串（如 "datetime", "int", "float", "string"）
            point_name: 数据点名称（用于日志记录）
            
        Returns:
            转换后的值
        """
        try:
            datatype_lower = datatype.lower().strip()
            
            if datatype_lower == 'datetime':
                # datetime 类型：将字符串转换为 datetime 对象
                if isinstance(value, str):
                    # 尝试多种常见的时间格式
                    datetime_formats = [
                        '%Y-%m-%d %H:%M:%S',
                        '%Y-%m-%d %H:%M:%S.%f',
                        '%Y-%m-%dT%H:%M:%S',
                        '%Y-%m-%dT%H:%M:%S.%f',
                        '%Y-%m-%dT%H:%M:%SZ',
                        '%Y/%m/%d %H:%M:%S',
                        '%Y%m%d%H%M%S',
                        'DT#%Y-%m-%d-%H:%M:%S',  # 支持 DT#2022-03-19-17:41:48 这种格式中的日期部分解析逻辑需特殊处理，但标准strptime不支持前缀，因此下面会添加特殊处理
                        '%a %b %d %H:%M:%S %Y',  # ctime 风格，如 Tue Apr 28 15:46:18 2026
                        '%a %b  %d %H:%M:%S %Y',  # 同上，个位数日期时日在月名后双空格，如 Tue Apr  8 15:46:18 2026
                    ]
                    
                    for fmt in datetime_formats:
                        try:
                            dt = datetime.strptime(value, fmt)
                            self.logger.debug(f"成功将 '{value}' 转换为 datetime (格式: {fmt})")
                            return dt
                        except ValueError:
                            continue
                    
                    # 如果所有格式都失败，记录警告并返回原始值
                    self.logger.warning(f"无法将 '{value}' 解析为 datetime (point: {point_name})，使用原始值")
                    return value
                elif isinstance(value, datetime):
                    # 已经是 datetime 对象，直接返回
                    return value
                else:
                    self.logger.warning(f"datetime 类型的值不是字符串: {type(value)} (point: {point_name})")
                    return value
            
            elif datatype_lower in ['int', 'integer']:
                # 整数类型
                try:
                    return int(value)
                except (ValueError, TypeError):
                    self.logger.warning(f"无法将 '{value}' 转换为 int (point: {point_name})")
                    return value
            
            elif datatype_lower in ['float', 'double', 'real']:
                # 浮点数类型
                try:
                    return float(value)
                except (ValueError, TypeError):
                    self.logger.warning(f"无法将 '{value}' 转换为 float (point: {point_name})")
                    return value
            
            elif datatype_lower in ['str', 'string', 'varchar', 'text']:
                # 字符串类型
                return str(value) if value is not None else None
            
            elif datatype_lower in ['bool', 'boolean']:
                # 布尔类型
                if isinstance(value, bool):
                    return value
                elif isinstance(value, str):
                    return value.lower() in ['true', '1', 'yes', 'on']
                else:
                    return bool(value)
            
            else:
                # 未知类型，返回原始值
                self.logger.debug(f"未知的 datatype: {datatype} (point: {point_name})，使用原始值")
                return value
                
        except Exception as e:
            self.logger.error(
                f"类型转换失败 (point: {point_name}, datatype: {datatype}): {e}",
                exc_info=True
            )
            return value

    @staticmethod
    def _values_equal(left: Any, right: Any) -> bool:
        """宽松比较两个值是否等价，用于幂等判定。"""
        if left is None and right is None:
            return True
        if isinstance(left, datetime) and isinstance(right, datetime):
            return left == right
        return str(left) == str(right)

    def _handle_batch_upsert_conflict(
        self,
        group_name: str,
        table_name: str,
        unique_key_point: str,
        unique_value: Any,
        insert_data: Dict[str, Any],
        batch_upsert_config: Dict[str, Any],
    ) -> str:
        """
        处理批次唯一冲突场景：
        - 仅当同批次且 end_time 为空时，允许更新 end_time；
        - 若 end_time 已存在，按唯一冲突处理；
        - 可选允许同值 end_time 幂等重放。
        """
        end_time_point = batch_upsert_config.get("end_time_point")
        start_time_point = batch_upsert_config.get("start_time_point")
        allow_idempotent = bool(batch_upsert_config.get("allow_idempotent_same_end_time", False))
        new_end_time = insert_data.get(end_time_point)

        if new_end_time is None:
            self.logger.warning(
                "组 %s 批次冲突但未提供结批时间，拒绝写入: %s=%s",
                group_name,
                unique_key_point,
                unique_value,
            )
            return self.STATUS_UNIQUE_CONFLICT

        try:
            update_sql = (
                f"UPDATE `{table_name}` "
                f"SET `{end_time_point}` = :new_end_time "
                f"WHERE `{unique_key_point}` = :unique_value AND `{end_time_point}` IS NULL"
            )
            affected_rows = self.db_manager.execute_update(
                update_sql,
                {"new_end_time": new_end_time, "unique_value": unique_value},
            )
            if affected_rows < 0:
                self.logger.error(
                    "组 %s 批次补结批失败(更新语句执行异常): %s=%s",
                    group_name,
                    unique_key_point,
                    unique_value,
                )
                return self.STATUS_DB_ERROR

            if affected_rows > 0:
                self.logger.info(
                    "组 %s 批次补结批成功: %s=%s, %s=%s",
                    group_name,
                    unique_key_point,
                    unique_value,
                    end_time_point,
                    new_end_time,
                )
                if group_name == self.batch_master_group_name:
                    self._mark_current_batch_closed(
                        unique_value,
                        insert_data.get(start_time_point) if start_time_point else None,
                    )
                return self.STATUS_SUCCESS

            if allow_idempotent:
                check_sql = (
                    f"SELECT `{end_time_point}` "
                    f"FROM `{table_name}` "
                    f"WHERE `{unique_key_point}` = :unique_value LIMIT 1"
                )
                rows = self.db_manager.execute_query(check_sql, {"unique_value": unique_value})
                if rows:
                    existing_end_time = rows[0][0]
                    if self._values_equal(existing_end_time, new_end_time):
                        self.logger.info(
                            "组 %s 批次结批幂等重放，按成功处理: %s=%s, %s=%s",
                            group_name,
                            unique_key_point,
                            unique_value,
                            end_time_point,
                            new_end_time,
                        )
                        if group_name == self.batch_master_group_name:
                            self._mark_current_batch_closed(
                                unique_value,
                                insert_data.get(start_time_point) if start_time_point else None,
                            )
                        return self.STATUS_SUCCESS

            self.logger.warning(
                "组 %s 批次已结批，拒绝重复写入: %s=%s",
                group_name,
                unique_key_point,
                unique_value,
            )
            return self.STATUS_UNIQUE_CONFLICT
        except Exception as update_error:
            self.logger.error(
                "组 %s 处理批次冲突失败: %s",
                group_name,
                update_error,
                exc_info=True,
            )
            return self.STATUS_DB_ERROR
    
    def get_queue_size(self) -> int:
        """获取队列大小"""
        return len(self.data_queue)

    def get_runtime_metrics(self) -> Dict[str, int]:
        snapshot = dict(self.metrics)
        outbox_counts = self.persistent_store.counts() if self.persistent_store is not None else {}
        snapshot.update(
            {
                "queue_size": len(self.data_queue),
                "retry_queue_size": len(self.retry_queue),
                "dead_letter_queue_size": len(self.dead_letter_queue),
                "inflight_size": 0,
                "outbox_enabled": int(self.persistent_store is not None),
                "outbox_pending_size": outbox_counts.get("pending", 0),
                "outbox_processing_size": outbox_counts.get("processing", 0),
                "outbox_retry_size": outbox_counts.get("retry", 0),
                "outbox_dead_letter_size": outbox_counts.get("dead_letter", 0),
                "outbox_completed_size": outbox_counts.get("completed", 0),
                "outbox_file_bytes": (
                    os.path.getsize(self.persistent_store.path)
                    if self.persistent_store is not None and os.path.exists(self.persistent_store.path)
                    else 0
                ),
            }
        )
        return snapshot
