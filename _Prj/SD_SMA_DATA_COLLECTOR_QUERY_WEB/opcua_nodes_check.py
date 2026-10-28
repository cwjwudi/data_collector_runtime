"""OPC UA 点位列表规整：名称和 NodeId 都要有，重复拒绝，空行跳过。

运行: python opcua_nodes_check.py
"""

from pathlib import Path
import subprocess

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

    script = Path(__file__).parent / "app/static/config.js"
    check = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const nodes = [{name: '心跳', node_id: 'ns=6;s=::HeartBeat'}];
const conditions = [{name: '报警', method: 'contains', source_table: '', source_field: '', bind_field: 'AlarmText'}];
const row = data => ({querySelector(selector) {
  const key = selector.match(/data-field="([^"]+)"/)[1];
  return key in data ? {value: data[key]} : null;
}});
const context = vm.createContext({document: {getElementById(id) {
  return {querySelectorAll: () => (id === 'opcuaNodeList' ? nodes : conditions).map(row)};
}}});
for (const [name, end] of [['syncOpcuaNodesFromDom', 'const OPCUA_NODE_INPUT'], ['readConditionRowsFromDom', 'function fillConditionSelect']]) {
 const start = source.indexOf('function ' + name + '(');
 vm.runInContext(source.slice(start, source.indexOf(end, start)), context);
}
vm.runInContext('let opcuaNodes = []; let queryConditionRows = [];', context);
assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext('syncOpcuaNodesFromDom()', context))), nodes);
assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext('readConditionRowsFromDom()', context))), conditions);
nodes.length = 0;
assert.equal(vm.runInContext('syncOpcuaNodesFromDom().length', context), 0);
'''
    subprocess.run(["node", "-e", check, str(script)], check=True)
    print("opcua_nodes_check: all assertions passed")


if __name__ == "__main__":
    main()
