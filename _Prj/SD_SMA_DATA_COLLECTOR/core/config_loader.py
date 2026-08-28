"""
配置加载器
负责从JSON文件加载和验证配置
"""

import json
import math
import os
from pathlib import Path
from typing import Dict, Any
from .config_models import (
    DataPoint, DataGroup, OpcUaConfig, DatabaseConfig, AppConfig,
    TriggerMode, TriggerType, Communication, Connection, LoggingConfig, InsertFeedbackConfig,
    BatchUpsertConfig, IndexConfig, PersistentQueueConfig, FIXED_INDEX_COLUMNS
)
from .secret_store import migrate_password_mapping, resolve_password


class ConfigLoader:
    """配置加载器类"""
    
    @staticmethod
    def load_from_file(file_path: str) -> AppConfig:
        """
        从JSON文件加载配置
        
        Args:
            file_path: 配置文件路径
            
        Returns:
            AppConfig: 应用配置对象
            
        Raises:
            FileNotFoundError: 配置文件不存在
            ValueError: 配置格式错误
        """
        try:
            path = Path(file_path)
            with path.open('r', encoding='utf-8') as f:
                config_data = json.load(f)
            database, migrated = migrate_password_mapping(path.parent, config_data.get('database', {}))
            if migrated:
                config_data['database'] = database
                tmp_path = path.with_name(f".{path.name}.secret-migration.tmp")
                with tmp_path.open('w', encoding='utf-8') as f:
                    json.dump(config_data, f, ensure_ascii=False, indent=2)
                tmp_path.replace(path)
            return ConfigLoader._parse_config(config_data, config_dir=path.parent)
        except FileNotFoundError:
            raise FileNotFoundError(f"配置文件未找到: {file_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"配置文件格式错误: {e}")
    
    @staticmethod
    def _parse_config(config_data: Dict[str, Any], config_dir: Path | None = None) -> AppConfig:
        """解析配置数据"""
        if 'http_server' in config_data:
            raise ValueError("配置包含已删除的 http_server 字段")

        # 解析数据点
        points = []
        for point_data in config_data.get('points', []):
            point = DataPoint(
                name=point_data['name'],
                path=point_data['path'],
                description=point_data['description'],
                datatype=point_data.get('datatype')  # 可选的 datatype 属性
            )
            points.append(point)
        
        # 解析数据组
        groups = []
        for group_data in config_data.get('groups', []):
            if group_data.get('trigger') == 'query':
                raise ValueError(
                    f"数据组 '{group_data.get('name', '<unknown>')}' 使用了已删除的 trigger=query 功能"
                )
            if 'query_config' in group_data:
                raise ValueError(
                    f"数据组 '{group_data.get('name', '<unknown>')}' 包含已删除的 query_config 字段"
                )
            if 'output_mode' in group_data:
                raise ValueError(
                    f"数据组 '{group_data.get('name', '<unknown>')}' 包含已删除的 output_mode 字段"
                )

            feedback_data = group_data.get('insert_feedback')
            insert_feedback = None
            if feedback_data is not None:
                insert_feedback = InsertFeedbackConfig(
                    feedback_point=feedback_data.get('feedback_point', ''),
                    code_success=feedback_data.get('code_success', 0),
                    code_unique_conflict=feedback_data.get('code_unique_conflict', 1),
                    code_db_error=feedback_data.get('code_db_error', 2),
                    code_other_error=feedback_data.get('code_other_error', 3)
                )

            batch_upsert_data = group_data.get('batch_upsert')
            batch_upsert = None
            if batch_upsert_data is not None:
                batch_upsert = BatchUpsertConfig(
                    enabled=bool(batch_upsert_data.get('enabled', False)),
                    start_time_point=batch_upsert_data.get('start_time_point'),
                    end_time_point=batch_upsert_data.get('end_time_point'),
                    update_only_when_end_time_is_null=batch_upsert_data.get('update_only_when_end_time_is_null', True),
                    reject_when_end_time_exists=batch_upsert_data.get('reject_when_end_time_exists', True),
                    allow_idempotent_same_end_time=batch_upsert_data.get('allow_idempotent_same_end_time', False),
                )

            # 解析索引配置
            indexes_data = group_data.get('indexes')
            indexes = None
            if indexes_data is not None:
                indexes = []
                for idx_entry in indexes_data:
                    raw_name = idx_entry.get('name', '').strip()
                    indexes.append(IndexConfig(
                        columns=idx_entry.get('columns', []),
                        name=raw_name if raw_name else None,
                        unique=bool(idx_entry.get('unique', False)),
                        index_type=idx_entry.get('index_type', 'btree'),
                    ))

            raw_trigger_interval = group_data.get('trigger_interval_seconds')
            raw_trigger_mode = group_data.get('trigger_mode', TriggerMode.POLL.value)
            # 兼容手工把下拉框值直接保存为 "subscription" 的配置。
            if (
                isinstance(raw_trigger_interval, str)
                and raw_trigger_interval.strip().lower() == TriggerMode.SUBSCRIPTION.value
            ):
                raw_trigger_mode = TriggerMode.SUBSCRIPTION.value
                raw_trigger_interval = None

            group = DataGroup(
                name=group_data['name'],
                interval_seconds=group_data['interval_seconds'],
                trigger=TriggerType(group_data['trigger']),
                description=group_data['description'],
                data_points=group_data['data_points'],
                enable_point=group_data.get('enable_point'),
                variable_point_overrides=(
                    {}
                    if group_data.get('variable_point_overrides') is None
                    else group_data.get('variable_point_overrides')
                ),
                interval_point=group_data.get('interval_point'),
                force_cadence_alignment=group_data.get('force_cadence_alignment', False),
                max_backfill_ticks=group_data.get('max_backfill_ticks', 1000),
                trigger_interval_seconds=raw_trigger_interval,
                trigger_mode=TriggerMode(str(raw_trigger_mode).strip().lower()),
                trigger_point=group_data.get('trigger_point'),
                reset_trigger_after_read=group_data.get('reset_trigger_after_read', True),
                partition_interval_years=int(
                    group_data['partition_interval_years']
                    if group_data.get('partition_interval_years') is not None
                    else 1
                ),
                recreate_interval_days=int(group_data.get('recreate_interval_days', 1) or 1),
                batch_insert_size=group_data.get('batch_insert_size', 100),
                is_parallel=group_data.get('is_parallel', False),
                unique_key_point=group_data.get('unique_key_point'),
                insert_feedback=insert_feedback,
                batch_upsert=batch_upsert,
                indexes=indexes,
            )
            groups.append(group)
        
        # 解析通信配置
        communications = []
        communications_data = config_data.get('communications', [])
        for comm_data in communications_data:
            comm = Communication(
                name=comm_data['name'],
                type=comm_data['type'],
                host=comm_data.get('host', '127.0.0.1'),
                port=comm_data.get('port', 4840)
            )
            communications.append(comm)
        
        # 解析连接配置
        connections = []
        connections_data = config_data.get('connections', [])
        for conn_data in connections_data:
            conn = Connection(
                name=conn_data['name'],
                communication=conn_data['communication'],
                data_groups=conn_data['data_groups'],
                heartbeat=conn_data.get('heartbeat', None) 
            )
            connections.append(conn)
        
        # 解析OPC UA配置 (为了向后兼容保留)
        opcua_data = config_data.get('opcua', {})
        opcua = OpcUaConfig(
            host=opcua_data.get('host', '127.0.0.1'),
            port=opcua_data.get('port', 4840)
        )
        
        # 解析数据库配置
        db_data = config_data.get('database', {})
                
        # 处理向后兼容性：如果存在旧的 data_group 字段，则转换为 data_groups
        data_groups = db_data.get('data_groups', [])
        if not data_groups and 'data_group' in db_data:
            data_groups = [db_data['data_group']] if db_data['data_group'] else []

        auto_create = db_data.get('auto_create', False)
        if not isinstance(auto_create, bool):
            raise ValueError("database.auto_create 必须是布尔值")
                
        database = DatabaseConfig(
            type=db_data.get('type', 'sqlite'),
            name=os.environ.get('SD_SMA_DB_DATABASE') or db_data.get('name', 'data.db'),
            host=os.environ.get('SD_SMA_DB_HOST') or db_data.get('host', '127.0.0.1'),
            port=int(os.environ.get('SD_SMA_DB_PORT') or db_data.get('port', 3306)),
            username=os.environ.get('SD_SMA_DB_USERNAME') or db_data.get('username', ''),
            # 环境变量优先；持久化配置只接受 password_enc。
            password=(
                resolve_password(config_dir, db_data, 'SD_SMA_DB_PASSWORD')
                if config_dir is not None
                else (os.environ.get('SD_SMA_DB_PASSWORD') or str(db_data.get('password', '') or ''))
            ),
            auto_create=auto_create,
            data_groups=data_groups
        )
                
        # 解析日志配置
        logging_data = config_data.get('logging', {})
        logging_config = LoggingConfig(
            level=logging_data.get('level', 'INFO'),
            output_dir=logging_data.get('output_dir'),
            backup_days=logging_data.get('backup_days', 14),
            rotation_when=logging_data.get('rotation_when', 'midnight'),
            rotation_interval=logging_data.get('rotation_interval', 1),
            console_enabled=logging_data.get('console_enabled', True)
        )
                
        queue_data = config_data.get('persistent_queue', {})
        persistent_queue = PersistentQueueConfig(
            enabled=bool(queue_data.get('enabled', False)),
            path=str(queue_data.get('path', 'runtime/queue/collector_outbox.db')),
            synchronous=str(queue_data.get('synchronous', 'FULL')).upper(),
            busy_timeout_ms=int(queue_data.get('busy_timeout_ms', 5000)),
            lease_seconds=float(queue_data.get('lease_seconds', 60.0)),
            retry_interval_seconds=float(queue_data.get('retry_interval_seconds', 5.0)),
            max_retry_interval_seconds=float(queue_data.get('max_retry_interval_seconds', 300.0)),
            max_attempts=int(queue_data.get('max_attempts', 0)),
            completed_retention_days=int(queue_data.get('completed_retention_days', 1)),
            cleanup_interval_seconds=float(queue_data.get('cleanup_interval_seconds', 3600.0)),
            max_queue_rows=int(queue_data.get('max_queue_rows', 1_000_000)),
        )

        # 创建应用配置
        config = AppConfig(
            points=points,
            groups=groups,
            opcua=opcua,
            database=database,
            communications=communications,
            connections=connections,
            logging=logging_config,
            persistent_queue=persistent_queue,
        )
        
        # 验证配置
        ConfigLoader._validate_config(config)
        
        return config
    
    @staticmethod
    def _validate_config(config: AppConfig) -> None:
        """验证配置的有效性"""
        queue = config.persistent_queue
        if queue.synchronous not in {"OFF", "NORMAL", "FULL", "EXTRA"}:
            raise ValueError("persistent_queue.synchronous must be OFF/NORMAL/FULL/EXTRA")
        if queue.busy_timeout_ms < 0 or queue.lease_seconds <= 0:
            raise ValueError("persistent_queue busy_timeout_ms/lease_seconds 配置无效")
        if queue.retry_interval_seconds <= 0 or queue.max_retry_interval_seconds < queue.retry_interval_seconds:
            raise ValueError("persistent_queue 重试间隔配置无效")
        if (queue.max_attempts < 0 or queue.completed_retention_days < 0
                or queue.cleanup_interval_seconds < 1 or queue.max_queue_rows <= 0):
            raise ValueError("persistent_queue 容量或保留配置无效")

        # 检查数据点名称唯一性
        point_names = [point.name for point in config.points]
        if len(point_names) != len(set(point_names)):
            raise ValueError("数据点名称必须唯一")
        
        # 检查数据组引用的数据点是否存在
        batch_upsert_groups = [
            group for group in config.groups
            if group.batch_upsert and group.batch_upsert.enabled
        ]
        if len(batch_upsert_groups) > 1:
            names = [group.name for group in batch_upsert_groups]
            raise ValueError(f"同一配置中只能启用一张 batch_upsert 批次主表: {names}")

        point_name_set = set(point_names)
        points_by_name = {point.name: point for point in config.points}
        for group in config.groups:
            if group.enable_point is not None:
                if not isinstance(group.enable_point, str):
                    raise ValueError(
                        f"数据组 '{group.name}' 的 enable_point 必须是点位名称"
                    )
                group.enable_point = group.enable_point.strip()
                if not group.enable_point:
                    group.enable_point = None
                elif group.enable_point not in point_name_set:
                    raise ValueError(
                        f"数据组 '{group.name}' 的外部启用点位不存在: {group.enable_point}"
                    )

            if group.trigger in {TriggerType.TIME, TriggerType.TIME_AND_VARIABLE}:
                if isinstance(group.interval_seconds, bool):
                    raise ValueError(
                        f"数据组 '{group.name}' 的 interval_seconds 必须为数值"
                    )
                try:
                    group.interval_seconds = float(group.interval_seconds)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"数据组 '{group.name}' 的 interval_seconds 必须为数值"
                    ) from None
                if not math.isfinite(group.interval_seconds) or group.interval_seconds <= 0:
                    raise ValueError(
                        f"数据组 '{group.name}' 的 interval_seconds 必须是大于 0 的有限数值"
                    )

            if group.interval_point:
                if group.trigger not in {TriggerType.TIME, TriggerType.TIME_AND_VARIABLE}:
                    raise ValueError(
                        f"数据组 '{group.name}' 仅在 trigger=time 或 time_and_variable 时支持 interval_point"
                    )
                if group.interval_point not in point_name_set:
                    raise ValueError(
                        f"数据组 '{group.name}' 的采集间隔点位不存在: {group.interval_point}"
                    )

            if not isinstance(group.force_cadence_alignment, bool):
                raise ValueError(
                    f"数据组 '{group.name}' 的 force_cadence_alignment 必须是布尔值"
                )
            if group.force_cadence_alignment and group.trigger not in {
                TriggerType.TIME,
                TriggerType.TIME_AND_VARIABLE,
            }:
                raise ValueError(
                    f"数据组 '{group.name}' 仅在 trigger=time 或 time_and_variable 时支持 "
                    "force_cadence_alignment"
                )
            if isinstance(group.max_backfill_ticks, bool):
                raise ValueError(
                    f"数据组 '{group.name}' 的 max_backfill_ticks 必须是整数"
                )
            try:
                group.max_backfill_ticks = int(group.max_backfill_ticks)
            except (TypeError, ValueError):
                raise ValueError(
                    f"数据组 '{group.name}' 的 max_backfill_ticks 必须是整数"
                ) from None
            if group.max_backfill_ticks < 1 or group.max_backfill_ticks > 100000:
                raise ValueError(
                    f"数据组 '{group.name}' 的 max_backfill_ticks 必须在 1 到 100000 之间"
                )

            if group.partition_interval_years < 0 or group.partition_interval_years > 10:
                raise ValueError(
                    f"数据组 '{group.name}' 的 partition_interval_years 必须在 0 到 10 之间"
                    f"（0=不分表）"
                )

            for point_name in group.data_points:
                if point_name not in point_name_set:
                    raise ValueError(f"数据组 '{group.name}' 引用了不存在的数据点: {point_name}")

            overrides = group.variable_point_overrides
            if not isinstance(overrides, dict):
                raise ValueError(
                    f"数据组 '{group.name}' 的 variable_point_overrides 必须是对象"
                )
            if overrides and group.trigger != TriggerType.TIME_AND_VARIABLE:
                raise ValueError(
                    f"数据组 '{group.name}' 仅在 trigger=time_and_variable 时支持 "
                    "variable_point_overrides"
                )
            normalized_overrides: Dict[str, str] = {}
            for logical_name, source_name in overrides.items():
                if not isinstance(logical_name, str) or not logical_name.strip():
                    raise ValueError(
                        f"数据组 '{group.name}' 的 variable_point_overrides 键必须是非空点位名"
                    )
                if not isinstance(source_name, str) or not source_name.strip():
                    raise ValueError(
                        f"数据组 '{group.name}' 的 variable_point_overrides[{logical_name}] "
                        "必须是非空点位名"
                    )
                logical_name = logical_name.strip()
                source_name = source_name.strip()
                if logical_name not in group.data_points:
                    raise ValueError(
                        f"数据组 '{group.name}' 的 variable_point_overrides 逻辑点不在 "
                        f"data_points 中: {logical_name}"
                    )
                if source_name not in point_name_set:
                    raise ValueError(
                        f"数据组 '{group.name}' 的 variable 替代点不存在: {source_name}"
                    )
                logical_type = (points_by_name[logical_name].datatype or '').strip().lower()
                source_type = (points_by_name[source_name].datatype or '').strip().lower()
                if logical_type and source_type and logical_type != source_type:
                    raise ValueError(
                        f"数据组 '{group.name}' 的 variable 点位类型不一致: "
                        f"{logical_name}({logical_type}) -> {source_name}({source_type})"
                    )
                normalized_overrides[logical_name] = source_name
            group.variable_point_overrides = normalized_overrides

            if group.unique_key_point:
                if group.unique_key_point not in group.data_points:
                    raise ValueError(
                        f"数据组 '{group.name}' 的 unique_key_point 必须包含在 data_points 中: {group.unique_key_point}"
                    )

            if group.insert_feedback:
                if not group.insert_feedback.feedback_point or not str(group.insert_feedback.feedback_point).strip():
                    raise ValueError(
                        f"数据组 '{group.name}' 的 insert_feedback.feedback_point 不能为空"
                    )
                if group.insert_feedback.feedback_point not in point_name_set:
                    raise ValueError(
                        f"数据组 '{group.name}' 的 insert_feedback.feedback_point 必须引用 points 中已定义的数据点名称: "
                        f"{group.insert_feedback.feedback_point}"
                    )

                for field_name in (
                    "code_success",
                    "code_unique_conflict",
                    "code_db_error",
                    "code_other_error",
                ):
                    value = getattr(group.insert_feedback, field_name)
                    if not isinstance(value, int):
                        raise ValueError(
                            f"数据组 '{group.name}' 的 insert_feedback.{field_name} 必须为整数"
                        )
                    if value < 0 or value > 0xFFFFFFFF:
                        raise ValueError(
                            f"数据组 '{group.name}' 的 insert_feedback.{field_name} 超出 UDINT 范围(0~4294967295)"
                        )

            if group.batch_upsert and group.batch_upsert.enabled:
                if not group.unique_key_point:
                    raise ValueError(
                        f"数据组 '{group.name}' 启用了 batch_upsert 时必须配置 unique_key_point"
                    )

                if not group.batch_upsert.start_time_point or group.batch_upsert.start_time_point not in group.data_points:
                    raise ValueError(
                        f"数据组 '{group.name}' 的 batch_upsert.start_time_point 必须配置且存在于 data_points 中"
                    )

                if not group.batch_upsert.end_time_point or group.batch_upsert.end_time_point not in group.data_points:
                    raise ValueError(
                        f"数据组 '{group.name}' 的 batch_upsert.end_time_point 必须配置且存在于 data_points 中"
                    )

                if not group.batch_upsert.update_only_when_end_time_is_null:
                    raise ValueError(
                        f"数据组 '{group.name}' 当前仅支持 batch_upsert.update_only_when_end_time_is_null=true"
                    )

                if not group.batch_upsert.reject_when_end_time_exists:
                    raise ValueError(
                        f"数据组 '{group.name}' 当前仅支持 batch_upsert.reject_when_end_time_exists=true"
                    )

            if group.trigger == TriggerType.VARIABLE:
                if not group.trigger_point:
                    raise ValueError(f"触发类型为variable的数据组 '{group.name}' 必须指定trigger_point")
                if group.trigger_point not in point_name_set:
                    raise ValueError(f"数据组 '{group.name}' 的触发点不存在: {group.trigger_point}")
                if group.trigger_mode == TriggerMode.POLL:
                    if group.trigger_interval_seconds is None:
                        group.trigger_interval_seconds = group.interval_seconds
                    try:
                        trigger_interval = float(group.trigger_interval_seconds)
                    except (TypeError, ValueError):
                        raise ValueError(
                            f"数据组 '{group.name}' 的 trigger_interval_seconds 必须为数值（trigger=variable）"
                        ) from None
                    if trigger_interval <= 0:
                        raise ValueError(
                            f"数据组 '{group.name}' 的 trigger_interval_seconds 必须大于 0（trigger=variable）"
                        )

            if group.trigger == TriggerType.TIME_AND_VARIABLE:
                if group.is_parallel:
                    raise ValueError(
                        f"数据组 '{group.name}' 的触发类型 time_and_variable 不支持并行模式，请将 is_parallel 设为 false"
                    )
                if not group.trigger_point:
                    raise ValueError(
                        f"触发类型为time_and_variable的数据组 '{group.name}' 必须指定 trigger_point"
                    )
                if group.trigger_point not in point_name_set:
                    raise ValueError(f"数据组 '{group.name}' 的触发点不存在: {group.trigger_point}")
                if group.trigger_mode == TriggerMode.POLL:
                    if group.trigger_interval_seconds is None:
                        raise ValueError(
                            f"数据组 '{group.name}' 使用 time_and_variable 轮询时必须配置 "
                            "trigger_interval_seconds（秒）"
                        )
                    try:
                        tri = float(group.trigger_interval_seconds)
                    except (TypeError, ValueError):
                        raise ValueError(
                            f"数据组 '{group.name}' 的 trigger_interval_seconds 必须为数值"
                        ) from None
                    if tri <= 0:
                        raise ValueError(
                            f"数据组 '{group.name}' 的 trigger_interval_seconds 必须大于 0"
                        )

            # 验证索引配置
            if group.indexes:
                seen_names: set[str] = set()
                for idx_idx, idx_cfg in enumerate(group.indexes):
                    if not idx_cfg.columns:
                        raise ValueError(
                            f"数据组 '{group.name}' 的 indexes[{idx_idx}].columns 不能为空"
                        )
                    allowed_index_columns = set(group.data_points) | FIXED_INDEX_COLUMNS
                    for col in idx_cfg.columns:
                        if not isinstance(col, str) or col not in allowed_index_columns:
                            raise ValueError(
                                f"数据组 '{group.name}' 的 indexes[{idx_idx}].columns "
                                f"引用了不存在的点位或固定字段: {col}"
                            )
                    # 校验自定义索引名
                    if idx_cfg.name:
                        if len(idx_cfg.name) > 64:
                            raise ValueError(
                                f"数据组 '{group.name}' 的 indexes[{idx_idx}].name "
                                f"超过 MySQL 64 字符限制: {idx_cfg.name}"
                            )
                        if idx_cfg.name in seen_names:
                            raise ValueError(
                                f"数据组 '{group.name}' 的 indexes[{idx_idx}].name 重复: {idx_cfg.name}"
                            )
                        seen_names.add(idx_cfg.name)

        # 检查数据库配置引用的数据组是否存在
        group_names = [group.name for group in config.groups]
        for data_group_name in config.database.data_groups:
            if data_group_name not in group_names:
                raise ValueError(f"数据库配置引用了不存在的数据组: {data_group_name}")
        
        # 检查通信配置
        if config.communications:
            comm_names = [comm.name for comm in config.communications]
            # 检查通信名称唯一性
            if len(comm_names) != len(set(comm_names)):
                raise ValueError("通信名称必须唯一")
            
            # 检查目前只支持opcua类型
            for comm in config.communications:
                if comm.type != "opcua":
                    raise ValueError(f"目前只支持opcua类型的通信，不支持: {comm.type}")
            
            # 检查连接配置
            if config.connections:
                conn_names = [conn.name for conn in config.connections]
                # 检查连接名称唯一性
                if len(conn_names) != len(set(conn_names)):
                    raise ValueError("连接名称必须唯一")
                
                # 检查连接引用的通信是否存在
                for conn in config.connections:
                    if conn.communication not in comm_names:
                        raise ValueError(f"连接 '{conn.name}' 引用了不存在的通信: {conn.communication}")
                    
                    # 检查连接引用的数据组是否存在
                    for group_name in conn.data_groups:
                        if group_name not in group_names:
                            raise ValueError(f"连接 '{conn.name}' 引用了不存在的数据组: {group_name}")
                
                # 检查每个数据组只能被一个连接引用
                referenced_groups = set()
                for conn in config.connections:
                    for group_name in conn.data_groups:
                        if group_name in referenced_groups:
                            raise ValueError(f"数据组 '{group_name}' 被多个连接引用")
                        referenced_groups.add(group_name)
                
                # 检查所有数据组都被引用
                all_referenced = referenced_groups == set(group_names)
                if not all_referenced:
                    missing_groups = set(group_names) - referenced_groups
                    raise ValueError(f"以下数据组未被任何连接引用: {missing_groups}")
