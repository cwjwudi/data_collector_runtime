"""查询条件解析自检：显式列表优先，旧批次号配置收成一条「批次号」。

运行: python query_conditions_check.py
"""

from app.config_manager import ConfigManager


def _view(**extra):
    base = {
        "batch_source": {"table": "dict_BatchCode", "field": "value"},
        "batch_field": "",
        "per_group": {
            "Data_Main": {"batch_field": "BatchCode"},
        },
    }
    base.update(extra)
    return base


def _check_prune() -> None:
    profile = {
        "query_view": {
            "views": {
                "table": {"per_group": {"data_product": {"columns": []}, "sensor_group_1": {"columns": []}}},
                "alarm": {"per_group": {"alarm_group_1": {"columns": []}}},
            },
            "group_baselines": {
                "data_product": "data_product",
                "sensor_group_1": "sensor_group_1_20260310",
            },
        },
        "plugins": {
            "modules": {
                "general": {
                    "bind_group": "sensor_group_1",
                    "pages": {"1": {"bind_group": "data_product"}, "2": {"bind_group": "motor_group"}},
                },
                "alarm": {"pages": {}},
            }
        },
    }
    report = ConfigManager.prune_editor_profile(profile, {"data_product", "data_alarm"})
    assert report["removed_groups"] == ["alarm_group_1", "sensor_group_1"], report
    assert "sensor_group_1" in report["removed_baselines"]
    assert report["removed_plugin_modules"] == ["alarm"]
    assert profile["plugins"]["modules"]["general"]["bind_group"] == ""
    assert profile["plugins"]["modules"]["general"]["pages"]["1"]["bind_group"] == "data_product"
    assert profile["plugins"]["modules"]["general"]["pages"]["2"]["bind_group"] == ""
    kept = ConfigManager.prune_editor_profile(
        {"query_view": {"views": {"table": {"per_group": {"sensor_group_1": {}}}}, "group_baselines": {}}, "plugins": {"modules": {}}},
        None,
    )
    assert kept["database_reached"] is False
    assert kept["removed_groups"] == []


def main() -> None:
    _check_prune()
    legacy = ConfigManager.conditions_from_view(_view(), "Data_Main")
    assert legacy == [
        {
            "name": "批次号",
            "source_table": "dict_BatchCode",
            "source_field": "value",
            "bind_field": "BatchCode",
        }
    ], legacy

    no_bind = ConfigManager.conditions_from_view(_view(), None)
    assert no_bind == [], no_bind

    preview = ConfigManager.conditions_from_view(_view(), None, allow_empty_bind=True)
    assert preview[0]["bind_field"] == "", preview

    explicit = ConfigManager.conditions_from_view(
        _view(
            query_conditions=[
                {
                    "name": "操作员",
                    "source_table": "dict_Operator",
                    "source_field": "value",
                    "bind_field": "Operator",
                }
            ]
        ),
        "Data_Main",
    )
    assert explicit[0]["name"] == "操作员", explicit
    assert "批次号" not in [item["name"] for item in explicit]

    cleared = ConfigManager.conditions_from_view(_view(query_conditions=[]), "Data_Main")
    assert cleared == [], cleared

    grouped = ConfigManager.conditions_from_view(
        _view(
            per_group={
                "Data_Main": {
                    "batch_field": "BatchCode",
                    "query_conditions": [
                        {
                            "name": "配方",
                            "source_table": "dict_Recipe",
                            "source_field": "value",
                            "bind_field": "Recipe",
                        }
                    ],
                }
            }
        ),
        "Data_Main",
    )
    assert grouped[0]["source_table"] == "dict_Recipe", grouped

    try:
        ConfigManager.normalize_query_conditions(
            [
                {"name": "批次号", "source_table": "a", "source_field": "b", "bind_field": "c"},
                {"name": "批次号", "source_table": "d", "source_field": "e", "bind_field": "f"},
            ]
        )
    except ValueError as exc:
        assert "重复" in str(exc), exc
    else:
        raise AssertionError("重复名称应拒绝")

    try:
        ConfigManager.normalize_query_conditions([{"name": "只有名称"}])
    except ValueError as exc:
        assert "绑定列" in str(exc), exc
    else:
        raise AssertionError("缺字段应拒绝")

    dict_conditions = ConfigManager.conditions_from_dict_tables(
        ["AlarmState", "BatchCode", "AlarmText", "id", "collection_time"],
        {"AlarmState": {"label_zh": "报警状态"}, "BatchCode": {"label_zh": "批次号"}},
        ["dict_alarmstate", "dict_batchcode"],
        skip_fields={"collection_time"},
    )
    assert [item["bind_field"] for item in dict_conditions] == ["AlarmState", "BatchCode"], dict_conditions
    assert dict_conditions[0]["source_table"] == "dict_alarmstate", dict_conditions
    assert dict_conditions[0]["name"] == "报警状态", dict_conditions
    assert dict_conditions[1]["name"] == "批次号", dict_conditions

    print("query_conditions_check: all assertions passed")


if __name__ == "__main__":
    main()
