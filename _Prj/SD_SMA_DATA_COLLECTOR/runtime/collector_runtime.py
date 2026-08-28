"""
数据采集系统运行时核心
抽离自 main.py，仅保留运行时与业务逻辑，不包含 CLI 参数解析。
"""

import asyncio
import logging
import os
import re
import sys
from dataclasses import asdict
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Optional

from communication.data_collector import DataCollector
from communication.communication_manager import CommunicationManager
from communication.heartbeat_manager import HeartbeatManager
from communication.opcua_feedback_writer import OpcUaFeedbackWriter
from core.config_loader import ConfigLoader
from core.config_models import AppConfig
from database.data_storage import DataStorageProcessor
from database.db_manager import DatabaseManager

# 禁止 OPC UA 库的高频 info/debug 日志；需要协议明细时可通过环境变量临时开启。
logging.getLogger("opcua").setLevel(logging.WARNING)
logging.getLogger("asyncua").setLevel(
    getattr(
        logging,
        os.getenv("SD_SMA_ASYNCUA_LOG_LEVEL", "WARNING").upper(),
        logging.WARNING,
    )
)


class DataCollectionSystem:
    """数据采集系统主类（运行时核心）"""

    @staticmethod
    def _rotated_log_namer(default_name: str) -> str:
        """
        将轮转文件名从 data_collector.log.YYYY-MM-DD
        转换为 data_collector.YYYY-MM-DD.log
        """
        match = re.match(r"^(?P<prefix>.+)\.log\.(?P<suffix>.+)$", default_name)
        if not match:
            return default_name
        return f"{match.group('prefix')}.{match.group('suffix')}.log"

    def __init__(self, config_file: str, *, extra_log_handlers: Optional[list[logging.Handler]] = None):
        self.config_file = config_file
        self._extra_log_handlers = list(extra_log_handlers or [])
        self.config: Optional[AppConfig] = None
        self.communication_manager: Optional[CommunicationManager] = None
        self.heartbeat_manager: Optional[HeartbeatManager] = None
        self.data_collector: Optional[DataCollector] = None
        self.db_manager: Optional[DatabaseManager] = None
        self.storage_processor: Optional[DataStorageProcessor] = None
        self.db_health_task: Optional[asyncio.Task] = None
        self.db_health_check_interval = max(1.0, float(os.getenv("SD_SMA_DB_HEALTH_CHECK_INTERVAL", "5")))
        self.running = False
        self.setup_logging()

    def setup_logging(self) -> None:
        configured_level = None
        if self.config and getattr(self.config, "logging", None):
            configured_level = getattr(self.config.logging, "level", None)
        log_level_name = (configured_level or os.getenv("SD_SMA_LOG_LEVEL", "INFO")).upper()
        log_level = getattr(logging, log_level_name, logging.INFO)
        log_file = self._get_log_file_path()
        rotation_when, rotation_interval, backup_days, console_enabled = self._get_log_rotation_settings()
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - "
            "[pid=%(process)d tid=%(threadName)s] %(message)s"
        )
        file_handler = TimedRotatingFileHandler(
            filename=log_file,
            when=rotation_when,
            interval=rotation_interval,
            backupCount=backup_days,
            encoding="utf-8",
        )
        file_handler.namer = self._rotated_log_namer
        file_handler.setFormatter(formatter)
        file_handler.setLevel(log_level)

        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        root_logger.handlers.clear()
        for h in self._extra_log_handlers:
            root_logger.addHandler(h)
        root_logger.addHandler(file_handler)
        if console_enabled:
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            stream_handler.setLevel(log_level)
            root_logger.addHandler(stream_handler)

        self.logger = logging.getLogger(__name__)

    def get_runtime_snapshot(self) -> dict[str, Any]:
        """
        供监视面板使用的轻量状态（可在采集运行时轮询）。
        """
        from sqlalchemy import text

        db_ok = False
        if self.db_manager and getattr(self.db_manager, "engine", None):
            try:
                with self.db_manager.engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                db_ok = True
            except Exception:
                db_ok = False

        opcua_detail: dict[str, bool] = {}
        opcua_states: dict[str, str] = {}
        opcua_subscriptions: dict[str, int] = {}
        if self.communication_manager and self.communication_manager.clients:
            for name, client in self.communication_manager.clients.items():
                try:
                    opcua_detail[name] = bool(client.is_connected())
                    opcua_states[name] = client.get_connection_state()
                    opcua_subscriptions[name] = client.get_subscription_count()
                except Exception:
                    opcua_detail[name] = False
                    opcua_states[name] = "unknown"
                    opcua_subscriptions[name] = 0
        opcua_all = bool(opcua_detail) and all(opcua_detail.values())

        storage_metrics = (
            self.storage_processor.get_runtime_metrics()
            if self.storage_processor
            else {
                "queue_size": 0,
                "retry_queue_size": 0,
                "dead_letter_queue_size": 0,
                "inflight_size": 0,
            }
        )
        collection_metrics = (
            dict(self.data_collector.metrics)
            if self.data_collector
            else {}
        )
        return {
            "collector_running": bool(self.running),
            "config_path": self.config_file,
            "initialized": self.config is not None and self.data_collector is not None,
            "database_connected": db_ok,
            "opcua": {
                "by_name": opcua_detail,
                "state_by_name": opcua_states,
                "subscriptions_by_name": opcua_subscriptions,
                "all_connected": opcua_all,
            },
            "storage": storage_metrics,
            "collection": collection_metrics,
        }

    async def initialize(self) -> bool:
        try:
            self.logger.info("正在加载配置...")
            self.config = ConfigLoader.load_from_file(self.config_file)
            self.setup_logging()
            self.logger.info("日志目录: %s", os.path.abspath(os.path.dirname(self._get_log_file_path())))

            self.logger.info("正在初始化通信管理器...")
            self.communication_manager = CommunicationManager(self.config)
            if not await self.communication_manager.initialize_connections():
                self.logger.error("通信管理器初始化失败")
                return False

            self.logger.info("正在初始化心跳管理器...")
            self.heartbeat_manager = HeartbeatManager(self.config, self.communication_manager)

            group_configs = {}
            for group in self.config.groups:
                group_configs[group.name] = {
                    "recreate_interval_days": group.recreate_interval_days,
                    "partition_interval_years": group.partition_interval_years,
                    "batch_insert_size": group.batch_insert_size,
                }

            self.logger.info("正在初始化数据库...")
            self.db_manager = DatabaseManager(
                {
                    "type": self.config.database.type,
                    "name": self.config.database.name,
                    "host": self.config.database.host,
                    "port": self.config.database.port,
                    "username": self.config.database.username,
                    "password": self.config.database.password,
                    "auto_create": self.config.database.auto_create,
                },
                group_configs,
            )

            if not self.db_manager.connect():
                self.logger.error("数据库连接失败")
                return False

            default_batch_size = 10
            if self.config.groups:
                default_batch_size = self.config.groups[0].batch_insert_size

            points_dict = {point.name: point for point in self.config.points}

            self.storage_processor = DataStorageProcessor(
                self.db_manager,
                default_batch_size,
                points_dict,
                self._write_insert_feedback,
                persistent_queue_config=asdict(self.config.persistent_queue),
            )

            for group in self.config.groups:
                self.storage_processor.group_batch_sizes[group.name] = group.batch_insert_size
                self.storage_processor.group_unique_key_points[group.name] = group.unique_key_point
                self.storage_processor.group_data_points[group.name] = list(group.data_points)
                self.storage_processor.group_partition_interval_years[group.name] = group.partition_interval_years
                if group.batch_upsert:
                    batch_time_config = {
                        "start_time_point": group.batch_upsert.start_time_point,
                        "end_time_point": group.batch_upsert.end_time_point,
                        "update_only_when_end_time_is_null": group.batch_upsert.update_only_when_end_time_is_null,
                        "reject_when_end_time_exists": group.batch_upsert.reject_when_end_time_exists,
                        "allow_idempotent_same_end_time": group.batch_upsert.allow_idempotent_same_end_time,
                    }
                    if group.batch_upsert.start_time_point or group.batch_upsert.end_time_point:
                        self.storage_processor.group_batch_time_configs[group.name] = batch_time_config
                    if group.batch_upsert.enabled:
                        self.storage_processor.group_batch_upsert_configs[group.name] = batch_time_config
                        self.storage_processor.batch_master_group_name = group.name
                        self.storage_processor.batch_master_config = batch_time_config
                        self.storage_processor.batch_master_unique_key_point = group.unique_key_point
                if group.insert_feedback:
                    feedback_point_name = group.insert_feedback.feedback_point
                    feedback_point_path = points_dict[feedback_point_name].path
                    self.storage_processor.group_insert_feedback_configs[group.name] = {
                        "feedback_point_name": feedback_point_name,
                        "feedback_point": feedback_point_path,
                        "code_success": group.insert_feedback.code_success,
                        "code_unique_conflict": group.insert_feedback.code_unique_conflict,
                        "code_db_error": group.insert_feedback.code_db_error,
                        "code_other_error": group.insert_feedback.code_other_error,
                    }
                if group.indexes:
                    self.storage_processor.group_indexes_configs[group.name] = [
                        {
                            "columns": idx.columns,
                            "name": idx.name or "",
                            "unique": idx.unique,
                            "index_type": idx.index_type,
                        }
                        for idx in group.indexes
                    ]
                self.logger.debug("设置组 %s 的batch_size为 %s", group.name, group.batch_insert_size)

            if not self.storage_processor.initialize_tables_for_runtime():
                self.logger.error("数据表初始化检查失败")
                return False

            self.data_collector = DataCollector(self.communication_manager)
            self.data_collector.register_data_callback(self._on_data_received)
            self.data_collector.register_group_disabled_callback(
                self._on_group_disabled
            )

            self.logger.info("系统初始化完成")
            return True

        except Exception as exc:  # noqa: BLE001
            self.logger.error("系统初始化失败: %s", exc, exc_info=True)
            return False

    def _on_data_received(self, collection_data: dict) -> None:
        if self.storage_processor:
            self.storage_processor.add_data(collection_data)
            self.logger.debug(
                "接收到数据: 组名=%s, 触发类型=%s",
                collection_data["group_name"],
                collection_data["trigger_type"],
            )

    def _on_group_disabled(self, group_name: str) -> None:
        if self.storage_processor:
            self.storage_processor.request_group_flush(group_name)

    async def start(self) -> None:
        if not self.config or not self.data_collector:
            raise RuntimeError("系统未初始化")

        try:
            self.running = True

            if self.heartbeat_manager:
                await self.heartbeat_manager.start_heartbeats()

            await self.storage_processor.start_processing()

            self.db_health_task = asyncio.create_task(
                self._monitor_database_connection(),
                name="db_health_monitor",
            )
            self.logger.info("数据库健康检查任务已启动（间隔 %.1f 秒）", self.db_health_check_interval)

            data_points_dict = {point.name: point for point in self.config.points}

            target_group_names = self.config.database.data_groups
            if not target_group_names:
                raise ValueError("数据库配置中未指定任何数据组")

            target_groups = [g for g in self.config.groups if g.name in target_group_names]
            if len(target_groups) != len(target_group_names):
                missing_groups = set(target_group_names) - set(g.name for g in target_groups)
                raise ValueError(f"未找到指定的数据组：{missing_groups}")

            self.logger.info("启动数据采集，目标数据组：%s", [g.name for g in target_groups])
            await self.data_collector.start_collection(target_groups, data_points_dict)

            self.logger.info("数据采集系统已启动")
            await self._wait_for_shutdown()

        except Exception as exc:  # noqa: BLE001
            self.logger.error("启动系统时发生错误: %s", exc, exc_info=True)
            raise

    async def stop(self) -> None:
        self.logger.info("正在停止数据采集系统...")
        self.running = False

        try:
            if self.heartbeat_manager:
                await self.heartbeat_manager.stop_heartbeats()
        except Exception as exc:  # noqa: BLE001
            self.logger.error("停止心跳管理器时发生错误：%s", exc, exc_info=True)

        try:
            if self.data_collector:
                await self.data_collector.stop_collection()
        except Exception as exc:  # noqa: BLE001
            self.logger.error("停止数据采集器时发生错误：%s", exc, exc_info=True)

        try:
            if self.db_health_task:
                self.db_health_task.cancel()
                try:
                    await self.db_health_task
                except asyncio.CancelledError:
                    self.logger.info("数据库健康检查任务已停止")
        except Exception as exc:  # noqa: BLE001
            self.logger.error("停止数据库健康检查任务时发生错误：%s", exc, exc_info=True)

        try:
            if self.storage_processor:
                await self.storage_processor.stop_processing()
        except Exception as exc:  # noqa: BLE001
            self.logger.error("停止数据存储处理器时发生错误: %s", exc, exc_info=True)

        try:
            if self.communication_manager:
                await self.communication_manager.disconnect_all()
        except Exception as exc:  # noqa: BLE001
            self.logger.error("断开OPC UA连接时发生错误: %s", exc, exc_info=True)

        try:
            if self.db_manager:
                self.db_manager.disconnect()
        except Exception as exc:  # noqa: BLE001
            self.logger.error("断开数据库连接时发生错误：%s", exc, exc_info=True)

        self.logger.info("数据采集系统已停止")

    async def _wait_for_shutdown(self) -> None:
        try:
            while self.running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("收到 KeyboardInterrupt，准备关闭系统...")
        except asyncio.CancelledError:
            self.logger.info("收到取消信号，准备关闭系统...")

    async def _monitor_database_connection(self) -> None:
        """后台数据库健康检查：即使无采集数据也能持续输出断连/重连日志。"""
        while self.running:
            try:
                if self.db_manager:
                    await asyncio.to_thread(self.db_manager.ensure_connection, "后台健康检查")
                await asyncio.sleep(self.db_health_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                self.logger.error("数据库健康检查异常：%s", exc, exc_info=True)
                await asyncio.sleep(self.db_health_check_interval)

    async def _write_insert_feedback(self, group_name: str, feedback_point: str, status_code: int) -> bool:
        try:
            if not self.communication_manager:
                self.logger.warning("通信管理器未初始化，无法写入插入反馈")
                return False

            opcua_client = self.communication_manager.get_client_for_group(group_name)
            if not opcua_client:
                self.logger.warning("组 %s 未找到对应通信客户端，无法写入反馈", group_name)
                return False

            data_writer = OpcUaFeedbackWriter(opcua_client)
            success = await data_writer.write_udint_feedback(feedback_point, status_code)
            if success:
                self.logger.debug(
                    "组 %s 已写入插入反馈: point=%s, code=%s",
                    group_name,
                    feedback_point,
                    status_code,
                )
            else:
                self.logger.warning(
                    "组 %s 写入插入反馈失败: point=%s, code=%s",
                    group_name,
                    feedback_point,
                    status_code,
                )
            return success
        except Exception as exc:  # noqa: BLE001
            self.logger.error("组 %s 写入插入反馈异常: %s", group_name, exc, exc_info=True)
            return False

    def _get_log_file_path(self) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        configured_log_dir = None
        if self.config and getattr(self.config, "logging", None):
            configured_log_dir = self.config.logging.output_dir
        log_dir = configured_log_dir or os.getenv(
            "SD_SMA_LOG_DIR",
            os.path.join(base_dir, "logs"),
        )
        if not os.path.isabs(log_dir):
            log_dir = os.path.join(base_dir, log_dir)
        return os.path.join(log_dir, "data_collector.log")

    def _get_log_rotation_settings(self) -> tuple[str, int, int, bool]:
        default_when = "midnight"
        default_interval = 1
        default_backup_days = 14
        default_console_enabled = True

        logging_config = getattr(self.config, "logging", None) if self.config else None
        if not logging_config:
            return default_when, default_interval, default_backup_days, default_console_enabled

        raw_when = str(getattr(logging_config, "rotation_when", default_when)).strip()
        normalized_when = raw_when.upper()
        valid_when = {"S", "M", "H", "D", "MIDNIGHT", "W0", "W1", "W2", "W3", "W4", "W5", "W6"}
        if normalized_when not in valid_when:
            normalized_when = "MIDNIGHT"
        when = "midnight" if normalized_when == "MIDNIGHT" else normalized_when

        try:
            rotation_interval = int(getattr(logging_config, "rotation_interval", default_interval))
        except (TypeError, ValueError):
            rotation_interval = default_interval
        if rotation_interval < 1:
            rotation_interval = default_interval

        try:
            backup_days = int(getattr(logging_config, "backup_days", default_backup_days))
        except (TypeError, ValueError):
            backup_days = default_backup_days
        if backup_days < 1:
            backup_days = default_backup_days

        console_enabled = bool(getattr(logging_config, "console_enabled", default_console_enabled))
        return when, rotation_interval, backup_days, console_enabled


async def run_collection_mode(config_file: str):
    """运行采集模式"""
    system = DataCollectionSystem(config_file)

    try:
        if await system.initialize():
            await system.start()
        else:
            print("系统初始化失败")
    except KeyboardInterrupt:
        print("\n收到中断信号，正在停止系统...")
    except asyncio.CancelledError:
        print("\n收到取消信号，正在停止系统...")
    except Exception as exc:  # noqa: BLE001
        print(f"系统运行错误: {exc}")
        import traceback

        traceback.print_exc()
    finally:
        try:
            await system.stop()
        except Exception as exc:  # noqa: BLE001
            print(f"停止系统时发生错误: {exc}")


