"""dict_fields 字典字段自动维护自检（离线 SQLite，无需 PLC / MySQL）。

运行: python dict_fields_check.py
覆盖: 字典表幂等建表/幂等插入、批次值收集、同步失败隔离。
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import DatabaseManager
from database.data_storage import DataStorageProcessor, _DICT_FIELD_BATCH_CAP


def _make_processor(db_manager: DatabaseManager) -> DataStorageProcessor:
    processor = DataStorageProcessor(db_manager, 10)
    processor.group_data_points["Data_Audit"] = ["AuditTime", "AuditOperator", "BatchCode"]
    processor.group_dict_fields["Data_Audit"] = ["AuditOperator"]
    return processor


def _make_item(operator: str) -> dict:
    return {"group_name": "Data_Audit", "data": {"AuditOperator": {"value": operator}}}


def check_db_manager_idempotent(db_manager: DatabaseManager) -> None:
    assert db_manager.ensure_dict_table("dict_AuditOperator"), "字典表创建失败"
    assert db_manager.ensure_dict_table("dict_AuditOperator"), "字典表重复创建应幂等"
    assert db_manager.insert_dict_values_ignore("dict_AuditOperator", ["alice", "bob"]), "首次插入失败"
    assert db_manager.insert_dict_values_ignore("dict_AuditOperator", ["alice", "carol"]), "重复插入应幂等"
    rows = db_manager.execute_query("SELECT value FROM dict_AuditOperator ORDER BY value")
    assert [r[0] for r in rows] == ["alice", "bob", "carol"], f"字典表内容不符: {rows}"
    # 空白值/None 被清洗掉
    assert db_manager.insert_dict_values_ignore("dict_AuditOperator", ["", None, "  "]), "空值插入应安全忽略"


def check_collect_values(processor: DataStorageProcessor) -> None:
    items = [_make_item("alice"), _make_item("bob"), _make_item("alice"), _make_item("")]
    collected = processor._collect_dict_values("Data_Audit", items)
    assert collected == {"AuditOperator": {"alice", "bob"}}, f"值收集不符: {collected}"

    # 未配置字典字段的组不收集
    assert processor._collect_dict_values("Data_Recipe", items) == {}, "未配置组不应收集"

    # 配置了不属于本组数据点的字段被忽略
    processor.group_dict_fields["Data_Audit"] = ["AuditOperator", "NotFound"]
    collected = processor._collect_dict_values("Data_Audit", items)
    assert set(collected) == {"AuditOperator"}, f"非法字段应被忽略: {collected}"


def check_sync_and_failure_isolation(processor: DataStorageProcessor) -> None:
    items = [_make_item("alice"), _make_item("bob")]

    async def run() -> None:
        await processor._sync_dict_fields("Data_Audit", items)  # 正常路径
        # 误配高频列：超过单批上限时跳过且不抛异常
        big_items = [_make_item(f"user_{i}") for i in range(_DICT_FIELD_BATCH_CAP + 1)]
        await processor._sync_dict_fields("Data_Audit", big_items)

    asyncio.run(run())
    rows = processor.db_manager.execute_query("SELECT COUNT(*) FROM dict_AuditOperator")
    # alice/bob 幂等重插不增行, 总数 = 幂等测试写入的 alice/bob/carol 共 3 行
    assert rows[0][0] == 3, f"超限批次不应写入字典表, 实际 {rows[0][0]} 行"


def check_startup_precreate(db_manager: DatabaseManager) -> None:
    """启动期 initialize_tables_for_runtime 应预建字典表（不等首批数据）。"""
    processor = _make_processor(db_manager)
    assert processor.initialize_tables_for_runtime(), "启动期建表失败"
    assert "dict_AuditOperator" in processor.ensured_tables, "启动期未预建字典表"
    rows = db_manager.execute_query("SELECT COUNT(*) FROM dict_AuditOperator")
    assert rows[0][0] == 0, "预建字典表应为空表"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "dict_check.db"
        db_manager = DatabaseManager({"type": "sqlite", "name": str(db_path), "data_groups": ["Data_Audit"]})
        assert db_manager.connect(), "SQLite 连接失败"
        try:
            processor = _make_processor(db_manager)
            check_startup_precreate(db_manager)
            check_db_manager_idempotent(db_manager)
            check_collect_values(processor)
            check_sync_and_failure_isolation(processor)
        finally:
            db_manager.disconnect()
    print("dict_fields_check: all assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
