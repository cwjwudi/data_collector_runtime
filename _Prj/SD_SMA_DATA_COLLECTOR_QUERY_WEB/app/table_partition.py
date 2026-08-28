"""Year-partitioned table name parsing (aligned with SD_SMA_DATA_COLLECTOR db_manager)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

TableKind = Literal["fixed", "partitioned", "legacy_date"]

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class PartitionedTable:
    physical_name: str
    group_name: str
    bucket_year: int
    interval_years: int


@dataclass(frozen=True)
class TableGroupInfo:
    physical_name: str
    group_name: str
    kind: TableKind


_LEGACY_DATE_TABLE_RE = re.compile(r"^(.+)_(\d{8})$")

def normalize_partition_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00").split("+")[0])
        except ValueError:
            return None
    return None


def bucket_year_for(batch_start: datetime, interval_years: int) -> int:
    interval = max(1, int(interval_years or 1))
    year = batch_start.year
    return year - ((year - 1) % interval)


def partition_bucket_contains(batch_start: datetime, bucket_year: int, interval_years: int) -> bool:
    return bucket_year_for(batch_start, interval_years) == int(bucket_year)


def parse_partitioned_table_name(table_name: str) -> PartitionedTable | None:
    if "_y" not in table_name:
        return None

    group_name, suffix = table_name.rsplit("_y", 1)
    if not group_name or "_span" not in suffix:
        return None

    year_text, interval_text = suffix.split("_span", 1)
    if len(year_text) != 4 or not year_text.isdigit() or not interval_text.isdigit():
        return None

    try:
        return PartitionedTable(
            physical_name=table_name,
            group_name=group_name,
            bucket_year=int(year_text),
            interval_years=max(1, int(interval_text)),
        )
    except ValueError:
        return None


def is_legacy_date_table(table_name: str) -> bool:
    return bool(_LEGACY_DATE_TABLE_RE.match(table_name))


def table_group_info(table_name: str) -> TableGroupInfo | None:
    """Classify a physical table into group + kind (fixed / partitioned / legacy_date)."""
    parsed = parse_partitioned_table_name(table_name)
    if parsed is not None:
        return TableGroupInfo(
            physical_name=parsed.physical_name,
            group_name=parsed.group_name,
            kind="partitioned",
        )

    legacy_match = _LEGACY_DATE_TABLE_RE.match(table_name)
    if legacy_match is not None:
        return TableGroupInfo(
            physical_name=table_name,
            group_name=legacy_match.group(1),
            kind="legacy_date",
        )

    if _IDENT_RE.match(table_name):
        return TableGroupInfo(
            physical_name=table_name,
            group_name=table_name,
            kind="fixed",
        )
    return None


def table_belongs_to_group(table_name: str, group: str) -> bool:
    info = table_group_info(table_name)
    return info is not None and info.group_name == group


def list_group_names_from_tables(all_tables: list[str]) -> list[str]:
    groups: set[str] = set()
    for table_name in all_tables:
        info = table_group_info(table_name)
        if info is not None:
            groups.add(info.group_name)
    return sorted(groups)


def list_tables_for_group(all_tables: list[str], group: str) -> list[str]:
    matched = [name for name in all_tables if table_belongs_to_group(name, group)]
    return sort_tables_for_group(group, matched)


def sort_tables_for_group(group: str, tables: list[str]) -> list[str]:
    def sort_key(name: str) -> tuple:
        info = table_group_info(name)
        if info is None:
            return (9, name)
        if info.kind == "fixed":
            return (0, name)
        if info.kind == "partitioned":
            parsed = parse_partitioned_table_name(name)
            if parsed is None:
                return (1, name)
            return (1, f"{parsed.bucket_year:04d}_{parsed.interval_years:02d}", name)
        legacy_match = _LEGACY_DATE_TABLE_RE.match(name)
        if legacy_match is not None:
            return (2, legacy_match.group(2), name)
        return (3, name)

    return sorted(tables, key=sort_key)


def default_baseline_table(group: str, tables: list[str]) -> str | None:
    if not tables:
        return None
    if group in tables:
        return group
    ordered = sort_tables_for_group(group, tables)
    return ordered[-1]


def resolve_matching_partitioned_tables(
    all_tables: list[str],
    batch_start: datetime,
) -> list[str]:
    """Return physical detail table names matching batch_start, sorted for stable slots."""
    matched: list[PartitionedTable] = []
    for name in all_tables:
        if is_legacy_date_table(name):
            continue
        parsed = parse_partitioned_table_name(name)
        if parsed is None:
            continue
        if partition_bucket_contains(batch_start, parsed.bucket_year, parsed.interval_years):
            matched.append(parsed)

    matched.sort(key=lambda item: (item.group_name.lower(), item.physical_name.lower()))
    return [item.physical_name for item in matched]


def build_table_name_array(
    batch_master_table: str,
    detail_tables: list[str],
    *,
    max_tables: int = 50,
    string_max_len: int = 80,
) -> list[str]:
    limit = max(1, int(max_tables))
    string_len = max(1, int(string_max_len))

    def clip(name: str) -> str:
        return str(name or "")[:string_len]

    values: list[str] = [clip(batch_master_table)]
    for table_name in detail_tables:
        if len(values) >= limit:
            break
        values.append(clip(table_name))

    if len(values) < limit:
        values.extend([""] * (limit - len(values)))
    return values[:limit]
