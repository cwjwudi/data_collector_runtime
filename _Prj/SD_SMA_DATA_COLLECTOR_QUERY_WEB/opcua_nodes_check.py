"""OPC UA 点位列表规整：名称和 NodeId 都要有，重复拒绝，空行跳过。

运行: python opcua_nodes_check.py
"""

from app.config_manager import UnifiedConfigStore


def main() -> None:
    assert UnifiedConfigStore._normalize_opcua_nodes(None) == []
    assert UnifiedConfigStore._normalize_opcua_nodes([{"name": "", "node_id": ""}]) == []
    nodes = UnifiedConfigStore._normalize_opcua_nodes(
        [{"name": "心跳", "node_id": "ns=6;s=::DataRev:bHeartBeat"}]
    )
    assert nodes == [{"name": "心跳", "node_id": "ns=6;s=::DataRev:bHeartBeat"}], nodes

    settings = UnifiedConfigStore._normalize_opcua_settings({"nodes": nodes, "poll_interval_ms": 500})
    assert settings["nodes"] == nodes, settings

    try:
        UnifiedConfigStore._normalize_opcua_nodes([{"name": "心跳"}])
    except ValueError as exc:
        assert "NodeId" in str(exc), exc
    else:
        raise AssertionError("缺 NodeId 应拒绝")

    try:
        UnifiedConfigStore._normalize_opcua_nodes(
            [
                {"name": "心跳", "node_id": "ns=6;s=::A"},
                {"name": "心跳2", "node_id": "ns=6;s=::A"},
            ]
        )
    except ValueError as exc:
        assert "重复" in str(exc), exc
    else:
        raise AssertionError("重复 NodeId 应拒绝")

    print("opcua_nodes_check: all assertions passed")


if __name__ == "__main__":
    main()
