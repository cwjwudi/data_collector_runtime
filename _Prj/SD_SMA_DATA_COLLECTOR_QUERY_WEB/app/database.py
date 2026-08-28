from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .models import HistoryQueryRequest
from .table_list_writeback import pick_start_time_column
from .table_partition import (
    default_baseline_table,
    list_group_names_from_tables,
    list_tables_for_group,
    normalize_partition_time,
    table_group_info,
)

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class HistoryQueryResult:
    total: int | None
    columns: list[str]
    rows: list[dict[str, Any]]
    missing_columns: list[str]
    has_more: bool = False
    next_cursor: dict[str, Any] | None = None

    def __iter__(self):
        # 保持旧调用方的四元组解包兼容。
        yield self.total
        yield self.columns
        yield self.rows
        yield self.missing_columns


def _safe_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid identifier: {name}")
    return f"`{name}`"


class QueryDatabase:
    def __init__(self, db_config: dict[str, Any]):
        self.db_type = str(db_config.get("type", "mysql")).lower()
        self.db_config = db_config
        self.engine = self._create_engine()

    def _create_engine(self) -> Engine:
        if self.db_type == "mysql":
            # 数据库密码优先取环境变量，配置文件无需保存明文口令
            password = os.environ.get("SD_SMA_DB_PASSWORD") or str(self.db_config.get("password", ""))
            encoded_password = quote_plus(password)
            conn_str = (
                f"mysql+pymysql://{self.db_config.get('username', '')}:{encoded_password}"
                f"@{self.db_config.get('host', '127.0.0.1')}:{self.db_config.get('port', 3306)}"
                f"/{self.db_config.get('name', '')}?charset=utf8mb4"
            )
        elif self.db_type == "sqlite":
            conn_str = f"sqlite:///{self.db_config.get('name', 'query.db')}"
        else:
            raise ValueError(f"Unsupported database type: {self.db_type}")

        return create_engine(conn_str, pool_pre_ping=True, echo=False)

    def ping(self) -> bool:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True

    def list_tables(self) -> list[str]:
        with self.engine.connect() as conn:
            if self.db_type == "mysql":
                rows = conn.execute(text("SHOW TABLES")).fetchall()
                return [str(row[0]) for row in rows]

            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            ).fetchall()
            return [str(row[0]) for row in rows]

    def list_groups(self) -> list[str]:
        return list_group_names_from_tables(self.list_tables())

    def list_tables_by_group(self, group: str) -> list[str]:
        return list_tables_for_group(self.list_tables(), group)

    def get_group_schema_report(self, group: str, baseline_table: str | None = None) -> dict[str, Any]:
        tables = self.list_tables_by_group(group)
        table_kinds: dict[str, str] = {}
        for table_name in tables:
            info = table_group_info(table_name)
            if info is not None:
                table_kinds[table_name] = info.kind

        if not tables:
            return {
                "group": group,
                "tables": [],
                "table_kinds": {},
                "consistent": True,
                "baseline_table": None,
                "mismatches": [],
            }

        chosen_baseline = (
            baseline_table
            if baseline_table in tables
            else default_baseline_table(group, tables)
        )
        if not chosen_baseline:
            chosen_baseline = tables[-1]

        baseline_columns = self.list_columns(chosen_baseline)
        baseline_set = set(baseline_columns)

        mismatches: list[dict[str, Any]] = []
        consistent = True
        for table in tables:
            cols = self.list_columns(table)
            col_set = set(cols)
            missing = [c for c in baseline_columns if c not in col_set]
            extra = [c for c in cols if c not in baseline_set]
            if missing or extra:
                consistent = False
                mismatches.append(
                    {
                        "table": table,
                        "missing_vs_baseline": missing,
                        "extra_vs_baseline": extra,
                    }
                )

        return {
            "group": group,
            "tables": tables,
            "table_kinds": table_kinds,
            "consistent": consistent,
            "baseline_table": chosen_baseline,
            "baseline_columns": baseline_columns,
            "mismatches": mismatches,
        }

    def lookup_batch_start_time(
        self,
        master_table: str,
        batch_column: str,
        batch_value: Any,
        start_time_column: str,
    ) -> datetime | None:
        table_ident = _safe_ident(master_table)
        available_columns = set(self.list_columns(master_table))
        if batch_column not in available_columns:
            logger.warning(
                "批次主表 %s 缺少批次字段 %s",
                master_table,
                batch_column,
            )
            return None

        resolved_start_column = pick_start_time_column(available_columns, start_time_column)
        if not resolved_start_column:
            logger.warning(
                "批次主表 %s 缺少开批时间字段（期望 %s）",
                master_table,
                start_time_column,
            )
            return None

        batch_ident = _safe_ident(batch_column)
        start_ident = _safe_ident(resolved_start_column)
        sql = (
            f"SELECT {start_ident} FROM {table_ident} "
            f"WHERE {batch_ident} = :batch_value "
            f"ORDER BY {start_ident} DESC LIMIT 1"
        )
        with self.engine.connect() as conn:
            row = conn.execute(text(sql), {"batch_value": batch_value}).first()
        if not row:
            return None
        return normalize_partition_time(row[0])

    def list_columns(self, table: str) -> list[str]:
        table_ident = _safe_ident(table)
        with self.engine.connect() as conn:
            if self.db_type == "mysql":
                rows = conn.execute(text(f"SHOW COLUMNS FROM {table_ident}")).fetchall()
                return [str(row[0]) for row in rows]

            rows = conn.execute(text(f"PRAGMA table_info({table_ident})")).fetchall()
            return [str(row[1]) for row in rows]

    def list_batch_codes(self, source_table: str, source_field: str, limit: int = 1000) -> list[str]:
        """Read the small batch dictionary used by the query page."""
        safe_limit = max(1, min(int(limit), 1000))
        table_ident = _safe_ident(source_table)
        field_ident = _safe_ident(source_field)
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT DISTINCT {field_ident} FROM {table_ident} "
                    f"WHERE {field_ident} IS NOT NULL AND {field_ident} <> '' "
                    f"ORDER BY {field_ident} LIMIT :limit"
                ),
                {"limit": safe_limit},
            ).fetchall()
        return [str(row[0]) for row in rows]

    def query_history(
        self,
        req: HistoryQueryRequest,
        *,
        page_size_cap: int | None = 500,
    ) -> HistoryQueryResult:
        table_ident = _safe_ident(req.table)
        available_columns = self.list_columns(req.table)
        column_set = set(available_columns)
        missing_columns: list[str] = []

        if req.columns:
            selected = [col for col in req.columns if col in column_set]
            missing_columns = [col for col in req.columns if col not in column_set]
        else:
            selected = available_columns

        if req.time_field not in column_set:
            raise ValueError(f"time_field not found in table: {req.time_field}")
        if req.sort_by not in column_set:
            raise ValueError(f"sort_by not found in table: {req.sort_by}")
        if not selected:
            raise ValueError("No valid columns selected")

        safe_cols = ", ".join(_safe_ident(c) for c in selected)
        time_conditions: list[str] = []
        filter_conditions: list[str] = []
        params: dict[str, Any] = {}

        if req.start_time:
            time_conditions.append(f"{_safe_ident(req.time_field)} >= :start_time")
            params["start_time"] = req.start_time
        if req.end_time:
            time_conditions.append(f"{_safe_ident(req.time_field)} <= :end_time")
            params["end_time"] = req.end_time

        batch_condition = ""
        batch_code = str(req.batch_code or "").strip()
        if batch_code:
            if not req.batch_field or req.batch_field not in column_set:
                raise ValueError(f"batch_field not found in table: {req.batch_field}")
            batch_condition = f"{_safe_ident(req.batch_field)} = :batch_code"
            params["batch_code"] = batch_code

        for idx, f in enumerate(req.filters):
            if f.field not in column_set:
                continue

            field_ident = _safe_ident(f.field)
            key = f"f_{idx}"

            if f.op == "eq":
                filter_conditions.append(f"{field_ident} = :{key}")
                params[key] = f.value
            elif f.op == "ne":
                filter_conditions.append(f"{field_ident} != :{key}")
                params[key] = f.value
            elif f.op == "gt":
                filter_conditions.append(f"{field_ident} > :{key}")
                params[key] = f.value
            elif f.op == "gte":
                filter_conditions.append(f"{field_ident} >= :{key}")
                params[key] = f.value
            elif f.op == "lt":
                filter_conditions.append(f"{field_ident} < :{key}")
                params[key] = f.value
            elif f.op == "lte":
                filter_conditions.append(f"{field_ident} <= :{key}")
                params[key] = f.value
            elif f.op == "like":
                filter_conditions.append(f"{field_ident} LIKE :{key}")
                params[key] = str(f.value)
            elif f.op == "in":
                values = f.value if isinstance(f.value, list) else [f.value]
                placeholders = []
                for i, value in enumerate(values):
                    in_key = f"{key}_{i}"
                    placeholders.append(f":{in_key}")
                    params[in_key] = value
                if placeholders:
                    filter_conditions.append(f"{field_ident} IN ({', '.join(placeholders)})")

        primary_conditions: list[str] = []
        time_group = f"({' AND '.join(time_conditions)})" if time_conditions else ""
        if batch_condition and time_group:
            joiner = " OR " if req.combine_mode == "or" else " AND "
            primary_conditions.append(f"({batch_condition}{joiner}{time_group})")
        elif batch_condition:
            primary_conditions.append(batch_condition)
        elif time_group:
            primary_conditions.append(time_group)

        base_conditions = primary_conditions + filter_conditions
        base_where_clause = ""
        if base_conditions:
            base_where_clause = " WHERE " + " AND ".join(base_conditions)

        direction = req.sort_dir.upper()
        order_columns = [f"{_safe_ident(req.sort_by)} {direction}"]
        cursor_supported = req.pagination_mode == "cursor" and "id" in column_set and req.sort_by != "id"
        if cursor_supported:
            order_columns.append(f"`id` {direction}")
        order_clause = " ORDER BY " + ", ".join(order_columns)
        page = max(req.page, 1)
        requested_page_size = max(req.page_size, 1)
        page_size = requested_page_size if page_size_cap is None else min(requested_page_size, page_size_cap)
        offset = (page - 1) * page_size

        where_clause = base_where_clause
        if cursor_supported and req.cursor is not None:
            comparator = ">" if req.sort_dir == "asc" else "<"
            cursor_condition = (
                f"({_safe_ident(req.sort_by)} {comparator} :cursor_sort_value OR "
                f"({_safe_ident(req.sort_by)} = :cursor_sort_value AND `id` {comparator} :cursor_id))"
            )
            where_clause += (" AND " if where_clause else " WHERE ") + cursor_condition
            params["cursor_sort_value"] = req.cursor.sort_value
            params["cursor_id"] = req.cursor.id

        count_sql = f"SELECT COUNT(*) AS cnt FROM {table_ident}{base_where_clause}"
        select_clause = safe_cols
        if cursor_supported:
            select_clause += f", {_safe_ident(req.sort_by)} AS `__cursor_sort`, `id` AS `__cursor_id`"
        data_sql = f"SELECT {select_clause} FROM {table_ident}{where_clause}{order_clause} LIMIT :limit_value"
        if req.pagination_mode == "offset":
            data_sql += " OFFSET :offset_value"

        params_with_page = dict(params)
        params_with_page["limit_value"] = page_size + 1 if req.pagination_mode == "cursor" else page_size
        if req.pagination_mode == "offset":
            params_with_page["offset_value"] = offset

        with self.engine.connect() as conn:
            total = int(conn.execute(text(count_sql), params).scalar() or 0) if req.include_total else None
            fetched_rows = conn.execute(text(data_sql), params_with_page).mappings().all()

        has_more = req.pagination_mode == "cursor" and len(fetched_rows) > page_size
        rows = fetched_rows[:page_size]
        next_cursor: dict[str, Any] | None = None
        if cursor_supported and has_more and rows:
            last_row = dict(rows[-1])
            sort_value = last_row.get("__cursor_sort")
            if isinstance(sort_value, datetime):
                sort_value = sort_value.isoformat(sep=" ")
            next_cursor = {"sort_value": sort_value, "id": int(last_row["__cursor_id"])}

        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            converted = {}
            for key, value in dict(row).items():
                if key in {"__cursor_sort", "__cursor_id"}:
                    continue
                if isinstance(value, datetime):
                    converted[key] = value.isoformat(sep=" ", timespec="seconds")
                else:
                    converted[key] = value
            normalized_rows.append(converted)

        return HistoryQueryResult(
            total=total,
            columns=selected,
            rows=normalized_rows,
            missing_columns=missing_columns,
            has_more=has_more,
            next_cursor=next_cursor,
        )
