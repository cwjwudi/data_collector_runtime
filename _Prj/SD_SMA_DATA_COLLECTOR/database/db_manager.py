"""
数据库连接管理器
支持MySQL和SQLite数据库连接
"""

import logging
import os
import time
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, date
import sqlite3
try:
    import pymysql
    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError, OperationalError


class DatabaseManager:
    """数据库管理器类"""
    
    def __init__(self, db_config: Dict[str, Any], group_configs: Dict[str, Any] = None):
        """
        初始化数据库管理器
        
        Args:
            db_config: 数据库配置字典
            group_configs: 数据组配置字典，键为group_name，值为包含recreate_interval_days等参数的字典
        """
        self.db_config = db_config
        self.group_configs = group_configs or {}
        self.engine = None
        self.SessionLocal = None
        self.current_table_names = {}  # 使用字典存储不同group的当前表名
        self.table_created_dates = {}  # 存储不同group的表创建日期
        self.logger = logging.getLogger(__name__)
        # SQL 打印控制：默认 debug；设置 SD_SMA_SQL_LOG_INFO=true 可提升为 info
        self.sql_log_info = os.getenv("SD_SMA_SQL_LOG_INFO", "").lower() in ("1", "true", "yes", "on")
        # 异常日志控制：默认不打印 traceback，避免日志噪音；需要时可通过环境变量开启
        self.log_traceback = os.getenv("SD_SMA_DB_TRACEBACK", "").lower() in ("1", "true", "yes", "on")
        # 断连重连控制：默认尝试 3 次，每次间隔 2 秒
        self.reconnect_attempts = max(1, int(os.getenv("SD_SMA_DB_RECONNECT_ATTEMPTS", "3")))
        self.reconnect_interval_seconds = max(0.0, float(os.getenv("SD_SMA_DB_RECONNECT_INTERVAL", "2")))
        self.disconnect_log_interval_seconds = max(1.0, float(os.getenv("SD_SMA_DB_DISCONNECT_LOG_INTERVAL", "30")))
        self._connection_healthy = False
        self._last_disconnect_log_ts = 0.0

    def _log_db_error(self, action: str, exc: Exception) -> None:
        """以清晰、简短形式记录数据库错误，避免整段 traceback 淹没业务日志。"""
        db_type = self.db_config.get("type", "unknown")
        db_host = self.db_config.get("host", "-")
        db_port = self.db_config.get("port", "-")
        db_name = self.db_config.get("name", "-")

        if isinstance(exc, OperationalError):
            # SQLAlchemy OperationalError 通常封装为 (code, message)
            code = "unknown"
            message = str(exc)
            try:
                if getattr(exc, "orig", None) and getattr(exc.orig, "args", None):
                    code = exc.orig.args[0]
                    if len(exc.orig.args) > 1:
                        message = exc.orig.args[1]
            except Exception:
                pass
            self.logger.error(
                "数据库%s失败(type=%s host=%s port=%s db=%s code=%s): %s",
                action,
                db_type,
                db_host,
                db_port,
                db_name,
                code,
                message,
                exc_info=self.log_traceback,
            )
            return

        self.logger.error(
            "数据库%s失败(type=%s host=%s port=%s db=%s): %s",
            action,
            db_type,
            db_host,
            db_port,
            db_name,
            exc,
            exc_info=self.log_traceback,
        )

    def _log_mysql_sql(self, sql: str, params: Optional[Dict[str, Any]] = None, force_info: bool = False) -> None:
        """打印 MySQL SQL 语句与参数"""
        if self.db_config.get('type', '').lower() != 'mysql':
            return
        sql_text = " ".join(sql.split())
        message = f"MySQL SQL: {sql_text}"
        if params:
            message += f" | params={params}"
        if force_info or self.sql_log_info:
            self.logger.info(message)
        else:
            self.logger.debug(message)

    def _mark_connection_lost(self, action: str, exc: Exception) -> None:
        """记录数据库断连事件（只在状态从健康变为异常时打印一次断连提示）。"""
        now = time.time()
        should_log_detail = self._connection_healthy or (
            now - self._last_disconnect_log_ts >= self.disconnect_log_interval_seconds
        )
        if self._connection_healthy:
            self.logger.warning("检测到数据库连接断开（动作=%s），将尝试重连", action)
        self._connection_healthy = False
        if should_log_detail:
            self._last_disconnect_log_ts = now
            self._log_db_error(action, exc)

    @staticmethod
    def _is_connection_operational_error(exc: Exception) -> bool:
        """判断 OperationalError 是否更像连接断开，而非 SQL 语义错误（如缺列 1054）。"""
        # pymysql / MySQLdb: args[0] 常为 errno
        code = None
        if getattr(exc, "orig", None) is not None:
            orig = exc.orig
            if getattr(orig, "args", None):
                code = orig.args[0]
        if code is None and getattr(exc, "args", None):
            code = exc.args[0]
        # 常见连接类错误；1054/1064/1241 等是 SQL 语义错误，不应重连
        connection_codes = {
            2006,  # MySQL server has gone away
            2013,  # Lost connection during query
            2003,  # Can't connect
            2002,  # Can't connect via socket
            1927,  # Connection was killed
        }
        if isinstance(code, int):
            return code in connection_codes
        text = str(exc).lower()
        return any(
            token in text
            for token in (
                "server has gone away",
                "lost connection",
                "can't connect",
                "connection refused",
                "not connected",
            )
        )

    def _attempt_reconnect(self, trigger_action: str) -> bool:
        """尝试重连数据库，并打印重连尝试日志。"""
        for idx in range(1, self.reconnect_attempts + 1):
            self.logger.warning(
                "数据库重连尝试 %s/%s（触发动作=%s）",
                idx,
                self.reconnect_attempts,
                trigger_action,
            )
            if self.connect():
                self._connection_healthy = True
                self.logger.info("数据库重连成功（触发动作=%s）", trigger_action)
                return True
            if idx < self.reconnect_attempts and self.reconnect_interval_seconds > 0:
                time.sleep(self.reconnect_interval_seconds)
        self.logger.error("数据库重连失败（触发动作=%s）", trigger_action)
        return False

    def ensure_connection(self, trigger_action: str = "健康检查") -> bool:
        """
        主动探测并确保数据库连接可用。
        用于后台健康检查场景：即使当前无插入/查询，也能打印断连与重连日志。
        """
        if not self.engine:
            self.logger.warning("数据库引擎未初始化，无法执行%s", trigger_action)
            self._connection_healthy = False
            return False

        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            if not self._connection_healthy:
                self.logger.info("数据库连接已恢复（动作=%s）", trigger_action)
            self._connection_healthy = True
            return True
        except OperationalError as e:
            self._mark_connection_lost(trigger_action, e)
            return self._attempt_reconnect(trigger_action)
        except Exception as e:
            self._mark_connection_lost(trigger_action, e)
            return self._attempt_reconnect(trigger_action)
        
        
    def connect(self) -> bool:
        """
        建立数据库连接
        
        Returns:
            bool: 连接是否成功
        """
        try:
            if self.db_config['type'].lower() == 'mysql':
                if not MYSQL_AVAILABLE:
                    raise ImportError("MySQL 驱动未安装，请安装 pymysql")
                connection_string = self._mysql_url(include_database=True)
            else:  # sqlite
                connection_string = f"sqlite:///{self.db_config['name']}"

            self.engine = self._create_engine(connection_string)

            try:
                self._test_engine_connection(self.engine)
            except OperationalError as exc:
                should_create = (
                    self.db_config['type'].lower() == 'mysql'
                    and self.db_config.get('auto_create', False)
                    and self._is_unknown_database_error(exc)
                )
                if not should_create:
                    raise

                self.engine.dispose()
                self.engine = None
                self._create_mysql_database()
                self.engine = self._create_engine(connection_string)
                self._test_engine_connection(self.engine)

            self.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self.engine
            )

            # 初始化表日期信息
            self._initialize_table_dates()
            
            self.logger.info(f"成功连接到{self.db_config['type']}数据库: {self.db_config['name']}")
            self._connection_healthy = True
            return True
            
        except Exception as e:
            self._log_db_error("连接", e)
            self._connection_healthy = False
            return False
    
    def disconnect(self) -> None:
        """断开数据库连接"""
        if self.engine:
            self.engine.dispose()
            self.logger.info("数据库连接已关闭")
        self._connection_healthy = False
    
    def get_session(self):
        """
        获取数据库会话
        
        Returns:
            Session: SQLAlchemy会话对象
        """
        if not self.SessionLocal:
            raise RuntimeError("数据库未连接")
        return self.SessionLocal()
    
    def create_data_table(self, table_name: str, columns: Dict[str, str],
                          indexes: Optional[List[Dict[str, Any]]] = None) -> bool:
        """
        创建数据表

        Args:
            table_name: 表名
            columns: 列定义字典 {列名: 数据类型}
            indexes: 索引配置列表，每项包含：
                - columns: List[str] 索引列
                - unique: bool 是否唯一索引
                - index_type: str "btree" / "hash"

        Returns:
            bool: 创建是否成功
        """
        try:
            # 构建CREATE TABLE语句
            column_definitions = []
            for col_name, col_type in columns.items():
                column_definitions.append(f"`{col_name}` {col_type}")

            # 添加通用字段
            column_definitions.extend([
                "`id` INTEGER PRIMARY KEY AUTOINCREMENT",
                "`collection_time` DATETIME NOT NULL",
                "`is_backfill` INTEGER NOT NULL DEFAULT 0",
                "`created_at` DATETIME DEFAULT CURRENT_TIMESTAMP"
            ])

            if self.db_config['type'].lower() == 'mysql':
                column_definitions[-4] = "`id` BIGINT AUTO_INCREMENT PRIMARY KEY"
                column_definitions[-2] = "`is_backfill` TINYINT(1) NOT NULL DEFAULT 0"
                auto_increment = "AUTO_INCREMENT"
            else:
                auto_increment = "AUTOINCREMENT"

            create_sql = f"""
            CREATE TABLE IF NOT EXISTS `{table_name}` (
                {', '.join(column_definitions)}
            )
            """
            self._log_mysql_sql(create_sql, force_info=True)

            with self.engine.connect() as conn:
                conn.execute(text(create_sql))
                conn.commit()

            self.current_table_name = table_name
            self.table_created_date = datetime.now().date()
            self.logger.info(f"成功创建数据表: {table_name}")

            # 表可能已存在（IF NOT EXISTS），补齐配置新增列
            if not self.ensure_table_columns(table_name, columns):
                return False

            return True

        except Exception as e:
            self._log_db_error("创建数据表", e)
            return False

    def get_table_column_names(self, table_name: str) -> Optional[List[str]]:
        """读取已有表的列名；表不存在时返回空列表，查询失败返回 None。"""
        try:
            is_mysql = self.db_config.get('type', '').lower() == 'mysql'
            if is_mysql:
                sql = (
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name"
                )
                rows = self.execute_query(sql, {"table_name": table_name})
                return [row[0] for row in rows]
            sql = f"PRAGMA table_info(`{table_name}`)"
            rows = self.execute_query(sql)
            return [row[1] for row in rows]
        except Exception as e:
            self._log_db_error("读取表列信息", e)
            return None

    def ensure_table_columns(self, table_name: str, columns: Dict[str, str]) -> bool:
        """
        确保已有表包含配置中的全部业务列；缺失列自动 ALTER TABLE ADD COLUMN。
        不修改已有列类型，避免破坏历史数据。
        """
        required_columns = dict(columns)
        required_columns.setdefault(
            "is_backfill",
            "TINYINT(1) NOT NULL DEFAULT 0"
            if self.db_config.get('type', '').lower() == 'mysql'
            else "INTEGER NOT NULL DEFAULT 0",
        )

        existing = self.get_table_column_names(table_name)
        if existing is None:
            return False

        existing_lower = {name.lower() for name in existing}
        missing = [
            (col_name, col_type)
            for col_name, col_type in required_columns.items()
            if col_name.lower() not in existing_lower
        ]
        if not missing:
            return True

        try:
            with self.engine.connect() as conn:
                for col_name, col_type in missing:
                    alter_sql = (
                        f"ALTER TABLE `{table_name}` "
                        f"ADD COLUMN `{col_name}` {col_type}"
                    )
                    self._log_mysql_sql(alter_sql, force_info=True)
                    conn.execute(text(alter_sql))
                    self.logger.info(
                        "表 %s 已补齐缺失列: %s %s",
                        table_name,
                        col_name,
                        col_type,
                    )
                conn.commit()
            return True
        except Exception as e:
            self._log_db_error("补齐表列", e)
            return False

    def create_indexes(self, table_name: str, indexes: List[Dict[str, Any]]) -> None:
        """在指定表上创建索引（MySQL 5.0~8.0 / SQLite 兼容）"""
        is_mysql = self.db_config.get('type', '').lower() == 'mysql'
        for idx_cfg in indexes:
            columns = idx_cfg.get('columns', [])
            if not columns:
                continue

            unique = bool(idx_cfg.get('unique', False))
            index_type = str(idx_cfg.get('index_type', 'btree')).lower()

            # 索引名：优先使用自定义名，否则自动生成
            custom_name = str(idx_cfg.get('name', '') or '').strip()
            if custom_name:
                index_name = custom_name
            else:
                col_suffix = '_'.join(columns)
                prefix = "uidx" if unique else "idx"
                index_name = f"{prefix}_{table_name}_{col_suffix}"
                if len(index_name) > 64:
                    index_name = index_name[:64]

            # USING 子句
            if is_mysql:
                using_clause = f" USING {index_type.upper()}"
            else:
                # SQLite 仅 btree，hash 降级
                using_clause = "" if index_type == "hash" else ""

            col_list = ", ".join(f"`{c}`" for c in columns)
            unique_clause = "UNIQUE" if unique else ""

            def _build_sql(if_not_exists: bool) -> str:
                ifn = "IF NOT EXISTS " if if_not_exists else ""
                return (
                    f"CREATE {unique_clause} INDEX {ifn}`{index_name}` "
                    f"ON `{table_name}` ({col_list}){using_clause}"
                )

            self._log_mysql_sql(_build_sql(True), force_info=True)

            try:
                with self.engine.connect() as conn:
                    if is_mysql:
                        # MySQL 5.0~5.6 不支持 IF NOT EXISTS，需要手动检查
                        check_sql = (
                            f"SHOW INDEX FROM `{table_name}` WHERE Key_name = :key_name"
                        )
                        result = conn.execute(text(check_sql), {"key_name": index_name})
                        if result.fetchone():
                            self.logger.debug(
                                "索引已存在，跳过: %s ON %s", index_name, table_name,
                            )
                            continue
                        conn.execute(text(_build_sql(False)))
                    else:
                        conn.execute(text(_build_sql(True)))
                    conn.commit()
                self.logger.info(
                    "成功创建索引: %s ON %s (%s)%s",
                    index_name, table_name, col_list,
                    " UNIQUE" if unique else "",
                )
            except Exception as e:
                # 索引创建失败不应阻塞主流程，记录警告即可
                self.logger.warning(
                    "创建索引失败: %s ON %s (%s): %s",
                    index_name, table_name, col_list, e,
                )
    
    def get_recreate_interval_days(self, group_name: str = None) -> int:
        """
        获取指定数据组的分表间隔天数
        
        Args:
            group_name: 数据组名称
            
        Returns:
            int: 分表间隔天数
        """
        # 这里需要通过外部传入group配置来获取参数
        # 暂时返回默认值，后续会通过参数传递解决
        return 365

    def _resolve_group_name(self, group_name: str = None) -> str:
        if group_name is not None:
            return group_name

        data_groups = self.db_config.get('data_groups', [])
        if data_groups:
            return data_groups[0]
        return 'default'

    @staticmethod
    def _normalize_partition_time(partition_time: Optional[Any] = None) -> datetime:
        if isinstance(partition_time, datetime):
            return partition_time
        if isinstance(partition_time, date):
            return datetime(partition_time.year, partition_time.month, partition_time.day)
        return datetime.now()

    @staticmethod
    def _format_year_table_name(
        group_name: str,
        partition_time: datetime,
        partition_interval_years: int = 1,
    ) -> str:
        interval = max(1, int(partition_interval_years or 1))
        year = partition_time.year
        suffix_year = year - ((year - 1) % interval)
        return f"{group_name}_y{suffix_year:04d}_span{interval}"

    @staticmethod
    def _parse_partitioned_table_name(table_name: str) -> Optional[Tuple[str, datetime, int]]:
        if "_y" not in table_name:
            return None

        group_name, suffix = table_name.rsplit("_y", 1)
        if not group_name or "_span" not in suffix:
            return None

        year_text, interval_text = suffix.split("_span", 1)
        if len(year_text) != 4 or not year_text.isdigit() or not interval_text.isdigit():
            return None

        try:
            interval = max(1, int(interval_text))
            return group_name, datetime(int(year_text), 1, 1), interval
        except ValueError:
            return None
    
    def get_current_table_name(
        self,
        group_name: str = None,
        partition_time: Optional[Any] = None,
        fixed_table: bool = False,
        partition_interval_years: Optional[int] = None,
    ) -> str:
        """
        获取当前使用的表名（根据group_name和日期自动切换）
        
        Args:
            group_name: 数据组名称，如果为None则使用默认配置
            
        Returns:
            str: 当前表名
        """
        group_name = self._resolve_group_name(group_name)
        effective_time = self._normalize_partition_time(partition_time)

        if partition_interval_years is None and group_name in self.group_configs:
            partition_interval_years = self.group_configs[group_name].get(
                'partition_interval_years', 1
            )
        if partition_interval_years is None:
            partition_interval_years = 1

        # fixed_table 或 partition_interval_years=0：不分表，表名无年份后缀
        if fixed_table or int(partition_interval_years) == 0:
            self.current_table_names[group_name] = group_name
            self.table_created_dates[group_name] = effective_time
            return group_name

        new_table_name = self._format_year_table_name(
            group_name,
            effective_time,
            partition_interval_years,
        )
        current_table_name = self.current_table_names.get(group_name)

        if current_table_name != new_table_name:
            self.logger.info(
                "Group '%s' switching to year table %s (partition_time=%s)",
                group_name,
                new_table_name,
                effective_time,
            )
            self.current_table_names[group_name] = new_table_name
            self.table_created_dates[group_name] = effective_time

        return self.current_table_names[group_name]
    
    def execute_query(self, sql: str, params: Optional[Dict] = None, _retry_on_disconnect: bool = True) -> list:
        """
        执行查询语句
        
        Args:
            sql: SQL查询语句
            params: 查询参数
            
        Returns:
            list: 查询结果
        """
        try:
            self._log_mysql_sql(sql, params)
            with self.engine.connect() as conn:
                result = conn.execute(text(sql), params or {})
                self._connection_healthy = True
                return result.fetchall()
        except OperationalError as e:
            if self._is_connection_operational_error(e):
                self._mark_connection_lost("执行查询", e)
                if _retry_on_disconnect and self._attempt_reconnect("执行查询"):
                    return self.execute_query(sql, params, _retry_on_disconnect=False)
            else:
                self._log_db_error("执行查询", e)
            raise
        except Exception as e:
            self._log_db_error("执行查询", e)
            raise
    
    def execute_insert(self, table_name: str, data: Dict[str, Any], _retry_on_disconnect: bool = True) -> bool:
        """
        插入数据
        
        Args:
            table_name: 表名
            data: 要插入的数据字典
            
        Returns:
            bool: 插入是否成功
        """
        try:
            columns = ', '.join([f"`{k}`" for k in data.keys()])
            placeholders = ', '.join([f":{k}" for k in data.keys()])
            
            sql = f"INSERT INTO `{table_name}` ({columns}) VALUES ({placeholders})"
            self._log_mysql_sql(sql, data)
            
            with self.engine.connect() as conn:
                conn.execute(text(sql), data)
                conn.commit()
            self._connection_healthy = True
            
            return True
        except OperationalError as e:
            if self._is_connection_operational_error(e):
                self._mark_connection_lost("插入数据", e)
                if _retry_on_disconnect and self._attempt_reconnect("插入数据"):
                    return self.execute_insert(table_name, data, _retry_on_disconnect=False)
            else:
                self._log_db_error("插入数据", e)
            return False
        except Exception as e:
            self._log_db_error("插入数据", e)
            return False

    @staticmethod
    def _is_unknown_database_error(exc: Exception) -> bool:
        """Return True only for MySQL error 1049 (unknown database)."""
        original = getattr(exc, "orig", None)
        args = getattr(original, "args", None) or getattr(exc, "args", None) or ()
        return bool(args) and args[0] == 1049

    def _mysql_url(self, *, include_database: bool) -> URL:
        return URL.create(
            drivername="mysql+pymysql",
            username=str(self.db_config.get("username", "")),
            password=str(self.db_config.get("password", "")),
            host=str(self.db_config.get("host", "127.0.0.1")),
            port=int(self.db_config.get("port", 3306)),
            database=str(self.db_config.get("name", "")) if include_database else None,
            query={"charset": "utf8mb4"},
        )

    @staticmethod
    def _create_engine(connection_url, **kwargs):
        return create_engine(
            connection_url,
            pool_pre_ping=True,
            echo=False,
            **kwargs,
        )

    @staticmethod
    def _test_engine_connection(engine) -> None:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    @staticmethod
    def _quote_mysql_identifier(value: Any) -> str:
        name = str(value)
        if not name or "\x00" in name or len(name) > 64:
            raise ValueError("MySQL 数据库名不能为空、不能包含 NUL，且长度不能超过 64 个字符")
        return f"`{name.replace('`', '``')}`"

    def _create_mysql_database(self) -> None:
        database_name = self.db_config.get("name", "")
        quoted_name = self._quote_mysql_identifier(database_name)
        sql = (
            f"CREATE DATABASE IF NOT EXISTS {quoted_name} "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        admin_engine = None
        try:
            admin_engine = self._create_engine(
                self._mysql_url(include_database=False),
                isolation_level="AUTOCOMMIT",
            )
            self.logger.warning(
                "MySQL 数据库 %s 不存在，正在自动创建；当前账号必须具备服务器 CREATE 权限",
                database_name,
            )
            self._log_mysql_sql(sql, force_info=True)
            with admin_engine.connect() as conn:
                conn.execute(text(sql))
            self.logger.info("成功自动创建 MySQL 数据库: %s", database_name)
        except Exception:
            self.logger.error(
                "自动创建 MySQL 数据库 %s 失败，请确认账号具备服务器 CREATE 权限；建议使用 ROOT 账号完成初始化",
                database_name,
            )
            raise
        finally:
            if admin_engine is not None:
                admin_engine.dispose()

    def execute_insert_many(
        self,
        table_name: str,
        rows: List[Dict[str, Any]],
        _retry_on_disconnect: bool = True,
    ) -> int:
        """
        使用同一连接和同一事务批量插入多条数据。

        SQLAlchemy 会将参数列表交给数据库驱动的 executemany；事务成功时统一提交，
        任一记录失败时整批回滚。成功返回写入条数，失败返回 -1。
        """
        if not rows:
            return 0

        columns = list(rows[0].keys())
        if not columns:
            self.logger.error("批量插入失败: table=%s, 首条记录没有字段", table_name)
            return -1

        expected_columns = set(columns)
        if any(set(row.keys()) != expected_columns for row in rows):
            self.logger.error(
                "批量插入失败: table=%s, 同一批次记录字段集合不一致",
                table_name,
            )
            return -1

        normalized_rows = [
            {column: row[column] for column in columns}
            for row in rows
        ]
        quoted_columns = ', '.join(f"`{column}`" for column in columns)
        placeholders = ', '.join(f":{column}" for column in columns)
        sql = f"INSERT INTO `{table_name}` ({quoted_columns}) VALUES ({placeholders})"

        try:
            self._log_mysql_sql(sql)
            self.logger.debug(
                "执行批量插入: table=%s, rows=%s, columns=%s",
                table_name,
                len(normalized_rows),
                len(columns),
            )
            with self.engine.begin() as conn:
                conn.execute(text(sql), normalized_rows)
            self._connection_healthy = True
            return len(normalized_rows)
        except OperationalError as e:
            if self._is_connection_operational_error(e):
                self._mark_connection_lost("批量插入数据", e)
                if _retry_on_disconnect and self._attempt_reconnect("批量插入数据"):
                    return self.execute_insert_many(
                        table_name,
                        rows,
                        _retry_on_disconnect=False,
                    )
            else:
                self._log_db_error("批量插入数据", e)
            return -1
        except Exception as e:
            self._log_db_error("批量插入数据", e)
            return -1

    def execute_update(self, sql: str, params: Optional[Dict[str, Any]] = None, _retry_on_disconnect: bool = True) -> int:
        """
        执行更新语句并返回受影响行数

        Args:
            sql: SQL 更新语句
            params: SQL 参数

        Returns:
            int: 受影响行数，失败返回 -1
        """
        try:
            self._log_mysql_sql(sql, params)
            with self.engine.connect() as conn:
                result = conn.execute(text(sql), params or {})
                conn.commit()
            self._connection_healthy = True
            return int(result.rowcount or 0)
        except OperationalError as e:
            self._mark_connection_lost("执行更新", e)
            if _retry_on_disconnect and self._attempt_reconnect("执行更新"):
                return self.execute_update(sql, params, _retry_on_disconnect=False)
            return -1
        except Exception as e:
            self._log_db_error("执行更新", e)
            return -1

    def record_exists(self, table_name: str, column_name: str, value: Any) -> bool:
        """
        检查指定列值是否已存在

        Args:
            table_name: 表名
            column_name: 列名
            value: 待检查值

        Returns:
            bool: 是否存在匹配记录
        """
        if value is None:
            sql = f"SELECT 1 FROM `{table_name}` WHERE `{column_name}` IS NULL LIMIT 1"
            rows = self.execute_query(sql)
        else:
            sql = f"SELECT 1 FROM `{table_name}` WHERE `{column_name}` = :value LIMIT 1"
            rows = self.execute_query(sql, {"value": value})
        return len(rows) > 0

    def _initialize_table_dates(self) -> None:
        """
        初始化数据库中所有表的日期信息
        扫描现有表，解析日期后缀，为每个group保存最新的日期
        """
        sql = ""
        tables = []
        try:
            # 获取所有表名
            if self.db_config['type'].lower() == 'mysql':
                sql = "SHOW TABLES"
            else:  # sqlite
                sql = "SELECT name FROM sqlite_master WHERE type='table'"
            
            tables = self.execute_query(sql)
            # 解析表名并按 group 分类
            group_latest_tables = {}
            parsed_count = 0
            skipped_count = 0
            for table_row in tables:
                # SQLAlchemy 2.x 通常返回 Row；统一尝试读取第 1 列作为表名
                try:
                    table_name = table_row[0]
                except Exception:
                    table_name = table_row
                table_name_str = str(table_name).strip()

                parsed_table = self._parse_partitioned_table_name(table_name_str)
                if not parsed_table:
                    skipped_count += 1
                    self.logger.debug("Skip non-partitioned table: %s", table_name_str)
                    continue

                group_name, parsed_date, _interval = parsed_table
                if (group_name not in group_latest_tables or
                    parsed_date > group_latest_tables[group_name][0]):
                    group_latest_tables[group_name] = (parsed_date, table_name_str)
                parsed_count += 1

            self.logger.debug(
                "扫描表完成: total=%s, parsed=%s, skipped=%s",
                len(tables),
                parsed_count,
                skipped_count,
            )

            # 更新实例变量
            group_latest_dates = {
                group_name: latest[0]
                for group_name, latest in group_latest_tables.items()
            }
            self.table_created_dates.update(group_latest_dates)
            
            # 为每个group生成对应的当前表名
            for group_name, (_latest_date, current_table_name) in group_latest_tables.items():
                self.current_table_names[group_name] = current_table_name
            
            if group_latest_dates:
                self.logger.info(f"初始化表日期信息完成: {group_latest_dates}")
            else:
                self.logger.info("未找到符合条件的表，将使用默认表名策略")
                
        except Exception as e:
            sample_row_type = type(tables[0]).__name__ if tables else "N/A"
            self.logger.debug("初始化表日期失败上下文: sql=%s, sample_row_type=%s", sql, sample_row_type)
            self._log_db_error("初始化表日期", e)
