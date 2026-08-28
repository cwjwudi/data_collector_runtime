"""
配置模块数据模型
定义数据点、数据组和数据库配置的数据结构
"""

from typing import List, Optional, Dict
from dataclasses import dataclass, field
from enum import Enum


FIXED_INDEX_COLUMNS = frozenset({"collection_time", "created_at", "is_backfill"})


class TriggerType(Enum):
    """触发类型枚举"""
    TIME = "time"
    VARIABLE = "variable"
    TIME_AND_VARIABLE = "time_and_variable"


class TriggerMode(Enum):
    """变量触发点的获取方式。"""
    POLL = "poll"
    SUBSCRIPTION = "subscription"


@dataclass
class DataPoint:
    """数据点配置"""
    name: str
    path: str
    description: str
    datatype: Optional[str] = None  # 可选的数据类型，如 "datetime", "int", "float", "string" 等


@dataclass
class InsertFeedbackConfig:
    """插入反馈配置（UDINT）"""
    feedback_point: str  # 引用 points 中定义的数据点名称
    code_success: int = 0
    code_unique_conflict: int = 1
    code_db_error: int = 2
    code_other_error: int = 3


@dataclass
class BatchUpsertConfig:
    """批次更新配置（唯一键冲突时按条件补写结批时间）"""
    enabled: bool = False
    start_time_point: Optional[str] = None
    end_time_point: Optional[str] = None
    update_only_when_end_time_is_null: bool = True
    reject_when_end_time_exists: bool = True
    allow_idempotent_same_end_time: bool = False


@dataclass
class IndexConfig:
    """索引配置"""
    columns: List[str]  # 索引列（支持复合索引）
    name: Optional[str] = None  # 自定义索引名，留空则自动生成
    unique: bool = False  # 是否唯一索引
    index_type: str = "btree"  # btree / hash（MySQL 支持 hash，SQLite 仅 btree）

    def __post_init__(self):
        if self.index_type not in ("btree", "hash"):
            raise ValueError(f"不支持的索引类型: {self.index_type}，仅支持 btree / hash")


@dataclass
class DataGroup:
    """数据组配置"""
    name: str
    interval_seconds: float
    trigger: TriggerType
    description: str
    data_points: List[str]
    enable_point: Optional[str] = None  # 外部启停点位：1/True 启用，0/False 停用；未配置时始终启用
    interval_point: Optional[str] = None  # time/time_and_variable：动态采集间隔点位，值单位为秒
    trigger_interval_seconds: Optional[float] = None  # variable/time_and_variable：触发变量采样周期（秒）
    trigger_mode: TriggerMode = TriggerMode.POLL  # poll=轮询；subscription=OPC UA 数据变化订阅
    trigger_point: Optional[str] = None
    reset_trigger_after_read: bool = True  # 是否在读取后复位触发点
    partition_interval_years: int = 1  # 0=不分表；1..10=分表间隔年份
    recreate_interval_days: int = 365  # 旧版字段：保留兼容，不参与分表逻辑
    batch_insert_size: int = 100  # 批量插入大小
    is_parallel: bool = False  # 并行触发：trigger 为布尔数组；data_points 可为数组或标量（标量广播到各触发行）
    unique_key_point: Optional[str] = None  # 唯一性校验键（按组）
    insert_feedback: Optional[InsertFeedbackConfig] = None  # 插入反馈配置（UDINT）
    batch_upsert: Optional[BatchUpsertConfig] = None  # 批次更新配置（唯一冲突时按 end_time 条件更新）
    indexes: Optional[List[IndexConfig]] = None  # 索引配置列表
    variable_point_overrides: Dict[str, str] = field(default_factory=dict)
    force_cadence_alignment: bool = False  # 按自然时间边界对齐，并在运行期恢复后补齐欠拍
    max_backfill_ticks: int = 1000  # 一次恢复最多补写的节拍数


@dataclass
class Communication:
    """通信配置"""
    name: str
    type: str  # 目前只支持 "opcua"
    host: str = "127.0.0.1"
    port: int = 4840
    
    @property
    def server_url(self) -> str:
        """获取完整的服务器URL"""
        return f"opc.tcp://{self.host}:{self.port}"


@dataclass
class Connection:
    """连接配置 - 定义哪些数据组使用哪个通信"""
    name: str
    communication: str  # 通信名称
    data_groups: List[str]  # 使用此通信的数据组列表
    heartbeat: Optional[str] = None  # 心跳信号的 OPC UA 地址（可选）


@dataclass
class OpcUaConfig:
    """OPC UA服务器配置 (为了向后兼容保留)"""
    host: str = "127.0.0.1"
    port: int = 4840
    
    @property
    def server_url(self) -> str:
        """获取完整的OPC UA服务器URL"""
        return f"opc.tcp://{self.host}:{self.port}"


@dataclass
class DatabaseConfig:
    """数据库配置"""
    type: str  # "mysql" 或 "sqlite"
    name: str
    host: str = "127.0.0.1"
    port: int = 3306
    username: str = ""
    password: str = ""
    auto_create: bool = False  # MySQL：目标数据库不存在时自动创建（需要服务器 CREATE 权限）
    data_groups: List[str] = None  # 支持多个数据组
    
    def __post_init__(self):
        # 保持向后兼容性
        if self.data_groups is None:
            self.data_groups = []


@dataclass
class LoggingConfig:
    """日志配置"""
    level: str = "INFO"  # 日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）
    output_dir: Optional[str] = None  # 日志输出目录，未配置时使用默认目录
    backup_days: int = 14  # 日志保留天数
    rotation_when: str = "midnight"  # 轮转周期: S/M/H/D/midnight/W0-W6
    rotation_interval: int = 1  # 轮转周期倍数
    console_enabled: bool = True  # 是否输出到控制台


@dataclass
class PersistentQueueConfig:
    """SQLite outbox configuration for crash-safe PLC acknowledgement."""
    enabled: bool = False
    path: str = "runtime/queue/collector_outbox.db"
    synchronous: str = "FULL"
    busy_timeout_ms: int = 5000
    lease_seconds: float = 60.0
    retry_interval_seconds: float = 5.0
    max_retry_interval_seconds: float = 300.0
    max_attempts: int = 0
    completed_retention_days: int = 1
    cleanup_interval_seconds: float = 3600.0
    max_queue_rows: int = 1_000_000


@dataclass
class AppConfig:
    """应用总配置"""
    points: List[DataPoint]
    groups: List[DataGroup]
    opcua: OpcUaConfig  # 为了向后兼容保留
    database: DatabaseConfig
    communications: List[Communication] = None  # 新增：通信配置列表
    connections: List[Connection] = None  # 新增：连接配置列表
    logging: LoggingConfig = None  # 日志配置
    persistent_queue: PersistentQueueConfig = None
    
    def __post_init__(self):
        # 保持向后兼容性
        if self.communications is None:
            self.communications = []
        if self.connections is None:
            self.connections = []
        if self.logging is None:
            self.logging = LoggingConfig()
        if self.persistent_queue is None:
            self.persistent_queue = PersistentQueueConfig()
        
        # 如果没有communications配置，从opcua配置创建默认通信
        if not self.communications and self.opcua:
            default_comm = Communication(
                name="default",
                type="opcua",
                host=self.opcua.host,
                port=self.opcua.port
            )
            self.communications = [default_comm]
            
            # 如果没有connections配置，创建默认连接
            if not self.connections:
                # 默认将所有数据组关联到default通信
                group_names = [group.name for group in self.groups]
                default_conn = Connection(
                    name="default_connection",
                    communication="default",
                    data_groups=group_names
                )
                self.connections = [default_conn]
        
        # 创建通信到数据组的映射，便于快速查找
        self._comm_to_groups_map: Dict[str, List[str]] = {}
        for conn in self.connections:
            self._comm_to_groups_map[conn.communication] = conn.data_groups
    
    def get_groups_for_communication(self, comm_name: str) -> List[str]:
        """获取指定通信对应的数据组列表"""
        return self._comm_to_groups_map.get(comm_name, [])
    
    def get_communication_for_group(self, group_name: str) -> Optional[str]:
        """获取指定数据组对应的通信名称"""
        for conn in self.connections:
            if group_name in conn.data_groups:
                return conn.communication
        return None
