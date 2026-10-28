"""查询 SQL 渲染与 PLC 写回自检。离线 FakePlc，不连真实 OPC UA。

运行: python sql_writeback_check.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from app.database import compile_history_query
from app.models import HistoryCursor, HistoryQueryRequest, QueryFilter
from app.sql_writeback import (
    PLC_STRING_PAYLOAD_BYTES,
    SqlWritebackConfig,
    SqlWritebackQueue,
    prepare_sql_payload,
    quote_literal,
    render_bound_sql,
    write_executed_sql,
)

COLUMNS = ["id", "collection_time", "value", "name", "code"]
ENDPOINT = "opc.tcp://127.0.0.1:4840/"
NODE = "ns=6;s=::DataRev:sQuerySql"


class FakePlc:
    def __init__(self, *, fail: bool = False, explode: bool = False):
        self.fail = fail
        self.explode = explode
        self.writes: list[tuple[str, str, str]] = []

    async def write_scalar(
        self,
        endpoint_url: str,
        node_id: str,
        value,
        *,
        username: str = "",
        password: str = "",
    ) -> bool:
        if self.explode:
            raise RuntimeError("plc down")
        if self.fail:
            return False
        self.writes.append((endpoint_url, node_id, str(value)))
        return True


def _request(**extra) -> HistoryQueryRequest:
    base = dict(
        table="sensor_group_1_y2024_span1",
        columns=["collection_time", "value", "missing_col"],
        start_time=datetime(2024, 1, 1, 0, 0, 0),
        end_time=datetime(2024, 1, 2, 0, 0, 0),
        time_field="collection_time",
        filters=[
            QueryFilter(field="name", op="like", value="%abc_"),
            QueryFilter(field="code", op="in", value=["a", "b'c"]),
            QueryFilter(field="missing_col", op="eq", value="skip"),
        ],
        page=2,
        page_size=10,
        sort_by="collection_time",
        sort_dir="desc",
        pagination_mode="offset",
    )
    base.update(extra)
    return HistoryQueryRequest(**base)


def check_render_matches_executed_sql() -> None:
    compiled = compile_history_query(_request(), COLUMNS, dialect="mysql")
    expected = (
        "SELECT `collection_time`, `value` FROM `sensor_group_1_y2024_span1` "
        "WHERE (`collection_time` >= '2024-01-01 00:00:00' AND `collection_time` <= '2024-01-02 00:00:00') "
        "AND `name` LIKE '%abc_' AND `code` IN ('a', 'b\\'c') "
        "ORDER BY `collection_time` DESC"
    )
    assert compiled.executed_sql == expected, compiled.executed_sql
    assert "LIMIT" not in compiled.executed_sql
    assert compiled.data_sql.endswith("LIMIT :limit_value OFFSET :offset_value")
    assert compiled.missing_columns == ["missing_col"]
    assert ":start_time" not in compiled.executed_sql
    assert compiled.data_sql.count(":f_") == 3

    sqlite_sql = compile_history_query(_request(), COLUMNS, dialect="sqlite").executed_sql
    assert "IN ('a', 'b''c')" in sqlite_sql
    assert "b\\'c" not in sqlite_sql

    capped = compile_history_query(_request(page_size=1000, page=1), COLUMNS, dialect="mysql", page_size_cap=500)
    assert "LIMIT" not in capped.executed_sql
    assert capped.data_params["limit_value"] == 500

    cursor = compile_history_query(
        _request(
            pagination_mode="cursor",
            page_size=10,
            sort_dir="asc",
            cursor=HistoryCursor(sort_value=datetime(2024, 1, 1, 8, 0, 0), id=7),
            filters=[],
        ),
        COLUMNS,
        dialect="mysql",
    )
    assert "LIMIT" not in cursor.executed_sql
    assert cursor.data_params["limit_value"] == 11
    assert "OFFSET" not in cursor.executed_sql
    assert "__cursor_sort" in cursor.data_sql and "__cursor_id" in cursor.data_sql
    assert "__cursor_sort" not in cursor.executed_sql and "__cursor_id" not in cursor.executed_sql
    assert "`id`" not in cursor.executed_sql
    assert cursor.executed_sql.endswith("ORDER BY `collection_time` ASC")


def check_literal_escaping() -> None:
    assert quote_literal("a\\b'c", "mysql") == "'a\\\\b\\'c'"
    assert quote_literal("a\\b'c", "sqlite") == "'a\\b''c'"
    assert render_bound_sql("WHERE a = :f_1 AND b = :f_10", {"f_1": 1, "f_10": 10}, "mysql") == (
        "WHERE a = 1 AND b = 10"
    )


def check_length_policy() -> None:
    short, warning = prepare_sql_payload("SELECT 1", PLC_STRING_PAYLOAD_BYTES)
    assert short == "SELECT 1" and warning is None
    payload, warning = prepare_sql_payload("S" * 81, PLC_STRING_PAYLOAD_BYTES)
    assert payload == ""
    assert warning is not None and "81" in warning and "80" in warning
    assert SqlWritebackConfig.from_page(None) is None
    assert SqlWritebackConfig.from_page({"enabled": False, "node": "ns=2;s=Sql"}) is None
    assert SqlWritebackConfig.from_page({"enabled": True, "node": "  "}) is None
    page = SqlWritebackConfig.from_page(
        {"enabled": 1, "node": " ns=2;s=Sql ", "max_len": 99999}
    )
    assert page is not None
    assert page.node_id == "ns=2;s=Sql"
    assert page.max_len == 4096


def check_fake_plc_writeback() -> None:
    plc = FakePlc()
    sql = "SELECT 1"
    assert asyncio.run(
        write_executed_sql(
            enabled=False,
            endpoint_url=ENDPOINT,
            node_id=NODE,
            sql=sql,
            writer=plc.write_scalar,
        )
    ) is None
    assert plc.writes == []

    missing = asyncio.run(
        write_executed_sql(
            enabled=True,
            endpoint_url="",
            node_id=NODE,
            sql=sql,
            writer=plc.write_scalar,
        )
    )
    assert missing and "NodeId" in missing
    assert plc.writes == []

    ok = asyncio.run(
        write_executed_sql(
            enabled=True,
            endpoint_url=ENDPOINT,
            node_id=NODE,
            sql=sql,
            username="u",
            password="p",
            writer=plc.write_scalar,
        )
    )
    assert ok is None
    assert plc.writes == [(ENDPOINT, NODE, sql)]

    long_sql = "SELECT " + ("x" * 100)
    cleared = asyncio.run(
        write_executed_sql(
            enabled=True,
            endpoint_url=ENDPOINT,
            node_id=NODE,
            sql=long_sql,
            max_bytes=80,
            writer=plc.write_scalar,
        )
    )
    assert cleared and "未下发截断语句" in cleared
    assert plc.writes[-1] == (ENDPOINT, NODE, "")

    plc.fail = True
    failed = asyncio.run(
        write_executed_sql(
            enabled=True,
            endpoint_url=ENDPOINT,
            node_id=NODE,
            sql=sql,
            writer=plc.write_scalar,
        )
    )
    assert failed and "失败" in failed

    plc.fail = False
    plc.explode = True
    exploded = asyncio.run(
        write_executed_sql(
            enabled=True,
            endpoint_url=ENDPOINT,
            node_id=NODE,
            sql=sql,
            writer=plc.write_scalar,
        )
    )
    assert exploded and "plc down" in exploded


async def check_background_queue() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    writes = []

    async def write(binding, sql):
        writes.append(sql)
        if sql == "first":
            started.set()
            await release.wait()  # 模拟离线连接长时间等待。
            raise RuntimeError("PLC offline")
        finished.set()
        return "write failed"

    queue = SqlWritebackQueue(write)
    binding = {"plugin_key": "test_1", "sql_writeback": {"enabled": True, "node": NODE}}
    queue.submit(binding, "first")
    await asyncio.wait_for(started.wait(), 1)
    # submit 已返回，PLC 写回仍卡在 release；新查询不会等待旧写回。
    queue.submit(binding, "obsolete")
    queue.submit(binding, "latest")
    assert writes == ["first"]
    release.set()
    await asyncio.wait_for(finished.wait(), 1)
    assert writes == ["first", "latest"]
    await queue.close()

    started.clear()
    release.clear()
    queue.submit(binding, "first")
    await asyncio.wait_for(started.wait(), 1)
    queue.submit(binding, "discard on shutdown")
    await asyncio.wait_for(queue.close(), 1)
    assert writes == ["first", "latest", "first"]
    queue.submit({"plugin_key": "disabled"}, "skip")
    assert not queue._pending


def main() -> None:
    check_literal_escaping()
    check_render_matches_executed_sql()
    check_length_policy()
    check_fake_plc_writeback()
    asyncio.run(check_background_queue())
    print("sql_writeback_check: ok")


if __name__ == "__main__":
    main()
