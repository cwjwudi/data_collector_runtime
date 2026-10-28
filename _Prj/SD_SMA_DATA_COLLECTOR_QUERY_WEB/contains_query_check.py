"""包含文字查询回归：真实 SQLite SQL、配置规整及两种查询页的控件切换。"""
import ast
from types import SimpleNamespace
from pathlib import Path
import sqlite3
import subprocess

from app.config_manager import ConfigManager
from app.database import compile_history_query
from app.models import HistoryQueryRequest, QueryFilter


def main():
    condition = {"name": "报警信息", "method": "contains", "bind_field": "AlarmText"}
    normalized = ConfigManager.normalize_query_conditions([condition])[0]
    assert normalized["source_table"] == normalized["source_field"] == ""
    assert ConfigManager.conditions_from_view({"per_group": {"alarm": {"query_conditions": [condition]}}}, "alarm") == [normalized]
    assert ConfigManager.normalize_query_conditions([{**condition, "source_table": "old", "source_field": "old"}]) == [normalized]
    exact = {"name": "状态", "source_table": "dict_state", "source_field": "value", "bind_field": "state"}
    assert ConfigManager.normalize_query_conditions([exact]) == [exact]
    for bad in [{**condition, "method": "unknown"}, {**condition, "bind_field": ""}]:
        try:
            ConfigManager.normalize_query_conditions([bad])
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    tree = ast.parse((Path(__file__).parent / "app/main.py").read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ("_condition_filter", "_condition_value_payload")]
    scope = {"QueryFilter": QueryFilter, "Any": object, "ConfigManager": ConfigManager,
             "cfg": SimpleNamespace(resolve_query_conditions=lambda *a, **kw: [normalized, exact]),
             "db": SimpleNamespace(list_tables=lambda: [])}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "app/main.py", "exec"), scope)
    fuzzy = scope["_condition_filter"]("table", "alarm", ["AlarmText"], condition_name="报警信息", condition_value="电机", query_mode="time", batch_code=None)
    assert fuzzy.op == "contains" and fuzzy.value == "电机" and fuzzy.field == "AlarmText"
    precise = scope["_condition_filter"]("table", "alarm", ["state"], condition_name="状态", condition_value="1", query_mode="time", batch_code=None)
    assert precise.op == "eq" and precise.value == "1"
    assert scope["_condition_value_payload"]("table", "报警信息", "alarm")["items"] == []
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE alarms (id INTEGER PRIMARY KEY, collection_time TEXT, AlarmText TEXT)")
    texts = ["第二个电机温度异常", "电机启动失败", "风机异常", "负载100%", "代号A_B", "注意!", "负载1000", "代号ACB"]
    conn.executemany("INSERT INTO alarms VALUES (?, '2026-10-28', ?)", enumerate(texts, 1))
    for keyword, expected in [("电机", texts[:2]), ("%", [texts[3]]), ("_", [texts[4]]), ("!", [texts[5]]), ("不存在", [])]:
        req = HistoryQueryRequest(table="alarms", columns=["AlarmText"], sort_by="id", sort_dir="asc", filters=[QueryFilter(field="AlarmText", op="contains", value=keyword)])
        sql = compile_history_query(req, ["id", "collection_time", "AlarmText"], dialect="sqlite")
        assert [r[0] for r in conn.execute(sql.data_sql, sql.data_params)] == expected
        assert [r[0] for r in conn.execute(sql.executed_sql)] == expected
        assert conn.execute(sql.count_sql, sql.count_params).fetchone()[0] == len(expected)
    static = Path(__file__).parent / "app/static"
    check = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
(async () => {
for (const file of process.argv.slice(1)) {
 const source = fs.readFileSync(file, 'utf8');
 const start = source.indexOf('async function loadConditionValues(');
 const end = source.indexOf('\n  async function loadQueryConditions', start) > 0
   ? source.indexOf('\n  async function loadQueryConditions', start) : source.indexOf('\nasync function reloadQueryConditions', start);
 const elements = {};
 class Element {
   constructor(tag) {this.tagName=tag;this.value='';this.children=[];this.className='';}
   set innerHTML(v) {this.children=[];}
   get options() {return this.children;}
   appendChild(e) {this.children.push(e);}
   setAttribute() {}
   replaceWith(e) {elements[this.id]=e;}
 }
 for (const id of ['queryConditionValue','queryCondition','viewName','group','queryWarnings']) {elements[id]=new Element('select');elements[id].id=id;}
 elements.queryCondition.value='报警信息';
 const condition = {name:'报警信息',method:'contains'};
 let calls=0;
 const context = vm.createContext({document:{getElementById:id=>elements[id],createElement:tag=>new Element(tag)},
   SdI18n:{t:k=>k},t:k=>k,advancedOpcuaMode:false,currentQueryConditions:[condition],selectedCondition:()=>condition,
   currentBinding:{view_name:'table',resolved_group:'alarm'},encodeURIComponent,
   fetchJson:async()=>{calls++;return {items:['运行']};},
   appendSelectOption:(el,value,label)=>{const option=new Element('option');option.value=value;el.appendChild(option);}});
 vm.runInContext(source.slice(start,end),context);
 await vm.runInContext('loadConditionValues("电机")',context);
 assert.equal(elements.queryConditionValue.tagName,'input');
 assert.equal(elements.queryConditionValue.value,'电机');assert.equal(calls,0);
 condition.method='eq';
 await vm.runInContext('loadConditionValues("运行")',context);
 assert.equal(elements.queryConditionValue.tagName,'select');
 assert.equal(elements.queryConditionValue.value,'运行');assert.equal(calls,1);
}
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run(["node", "-e", check, str(static / "query.js"), str(static / "specialized_query.js")], check=True)
    for name in ["config", "query", "specialized_query"]:
        subprocess.run(["node", "--check", str(static / f"{name}.js")], check=True)
    print("contains_query_check: all assertions passed")


if __name__ == "__main__":
    main()
