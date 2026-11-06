"""旧数据库凭据兼容：真实配置读写、运行时注入及前端草稿/表单回归；仅使用临时文件。"""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "_Prj/SD_SMA_DATA_COLLECTOR"
QUERY = ROOT / "_Prj/SD_SMA_DATA_COLLECTOR_QUERY_WEB"
sys.path[:0] = [str(ROOT / "_Prj/SD_SMA_COMMON"), str(COLLECTOR), str(QUERY)]

from core.config_loader import ConfigLoader
from web_config.config_manager import CollectorConfigManager
from app.config_manager import UnifiedConfigStore
from app.database import QueryDatabase
from sd_sma_common.database_settings import database_credentials_from_env

LEGACY = {"username": "legacy-user", "password": "legacy-password", "password_enc": "obsolete",
          "password_configured": True, "clear_password": True}


def main():
    environment = dict(os.environ)
    try:
        for key in list(os.environ):
            if key.startswith("SD_SMA_DB_") or key == "SD_SMA_OPCUA_PASSWORD":
                del os.environ[key]
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            check_collector(directory / "collector")
            check_query(directory / "query")
        check_frontend()
    finally:
        os.environ.clear()
        os.environ.update(environment)
    print("database_settings_check: all assertions passed")


def check_collector(directory):
    directory.mkdir()
    manager = CollectorConfigManager(directory)
    payload = manager.get_template()
    assert not LEGACY.keys() & payload["database"].keys()
    payload["database"].update(LEGACY, name="keep-db", auto_create=True)
    path = directory / "legacy.json"
    original = json.dumps(payload, ensure_ascii=False)
    path.write_text(original, encoding="utf-8")
    for credentials in [None, ("", ""), ("managed-user", "managed-password")]:
        set_credentials(credentials)
        loaded = ConfigLoader.load_from_file(str(path)).database
        assert (loaded.username, loaded.password) == (credentials or ("", ""))
        assert loaded.name == "keep-db" and loaded.auto_create and loaded.data_groups == []
        public = manager.load_config_file(path.name)["payload"]
        assert not LEGACY.keys() & public["database"].keys()
        assert path.read_text(encoding="utf-8") == original
    manager.validate_template(payload)
    exported = manager.export_to_path(payload, directory / "export.json")
    assert not LEGACY.keys() & json.loads(exported.read_text(encoding="utf-8"))["database"].keys()
    manager.write_collector_config(payload, path.name)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["database"] == {key: value for key, value in payload["database"].items() if key not in LEGACY}
    assert payload["database"]["password"] == LEGACY["password"]
    central = dict(SD_SMA_DB_CREDENTIAL_MANAGED="1", SD_SMA_DB_ENGINE="mysql",
                   SD_SMA_DB_HOST="central-db", SD_SMA_DB_PORT="3307", SD_SMA_DB_DATABASE="central_data")
    os.environ.update(central)
    try:
        loaded = ConfigLoader.load_from_file(str(path)).database
        assert (loaded.type, loaded.host, loaded.port, loaded.name) == ("mysql", "central-db", 3307, "central_data")
        assert not loaded.auto_create and loaded.data_groups == payload['database']['data_groups']
    finally:
        for key in central:
            del os.environ[key]
    assert ConfigLoader.load_from_file(str(path)).database.auto_create


def check_query(directory):
    store = UnifiedConfigStore(directory)
    profile = store.get_active_config()
    assert not LEGACY.keys() & profile["app_settings"]["database"].keys()
    profile["app_settings"]["database"].update(LEGACY, type="sqlite", name=":memory:")
    profile["opcua"].update(username="plc-user", password="plc-password")
    path = directory / store.get_active_profile_name()
    original = json.dumps(profile, ensure_ascii=False)
    path.write_text(original, encoding="utf-8")
    tree = ast.parse((QUERY / "app/main.py").read_text(encoding="utf-8"))
    names = {"_normalize_app_settings", "_load_app_settings", "_save_app_settings"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    scope = {"Any": object, "config_store": store, "database_credentials_from_env": database_credentials_from_env}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "app/main.py", "exec"), scope)
    for credentials in [None, ("", ""), ("managed-user", "managed-password")]:
        set_credentials(credentials)
        runtime = store.get_app_settings()["database"]
        assert (runtime["username"], runtime["password"]) == (credentials or ("", ""))
        wrapped = scope["_load_app_settings"]()["database"]
        assert wrapped["username"] == runtime["username"] and wrapped["password"] == runtime["password"]
        for public in [store.get_active_config()["app_settings"], store.get_public_app_settings(),
                       store.get_public_profile()["app_settings"], scope["_normalize_app_settings"](profile["app_settings"])]:
            assert not LEGACY.keys() & public["database"].keys()
        assert store.get_opcua_settings()["password"] == "plc-password"
        assert path.read_text(encoding="utf-8") == original
    mysql = QueryDatabase({**store.get_app_settings()["database"], "type": "mysql", "host": "127.0.0.1", "port": 3306})
    assert mysql.engine.url.username == "managed-user" and mysql.engine.url.password == "managed-password"
    mysql.engine.dispose()
    public = scope["_save_app_settings"](profile["app_settings"])
    assert not LEGACY.keys() & public["database"].keys()
    assert not LEGACY.keys() & json.loads(path.read_text(encoding="utf-8"))["app_settings"]["database"].keys()
    submitted = json.loads(json.dumps(profile))
    submitted["opcua"]["password"] = ""
    public, report = store.replace_active_profile(submitted)
    assert report["database_reached"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert not LEGACY.keys() & saved["app_settings"]["database"].keys()
    assert saved["opcua"]["username"] == "plc-user" and saved["opcua"]["password"] == "plc-password"
    assert not LEGACY.keys() & public["app_settings"]["database"].keys()
    assert profile["app_settings"]["database"]["password"] == LEGACY["password"]
    store.create_profile("copy.json")
    assert not LEGACY.keys() & json.loads((directory / "copy.json").read_text(encoding="utf-8"))["app_settings"]["database"].keys()


def set_credentials(credentials):
    for key, value in zip(("SD_SMA_DB_USERNAME", "SD_SMA_DB_PASSWORD"), credentials or (None, None)):
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def check_frontend():
    check = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const [collector, query, html] = process.argv.slice(1).map(p => fs.readFileSync(p, 'utf8'));
for (const source of [collector, query]) new vm.Script(source);
function functions(source, names) {
  return names.map(name => source.match(new RegExp('function ' + name + '\\([^]*?\\n\\}'))[0]).join('\n');
}
const keys = ['username', 'password', 'password_enc', 'password_configured', 'clear_password'];
const context = vm.createContext({});
vm.runInContext(functions(collector, ['createDefaultConfig', 'normalizeConfig']), context);
const config = vm.runInContext(`normalizeConfig({database: {name: 'keep-db', username: 'old', password: 'old',
  password_enc: 'old', password_configured: true, clear_password: true}})`, context);
for (const key of keys) assert.equal(key in config.database, false);
assert.equal(config.database.name, 'keep-db');
const fields = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], {value: '', checked: false}]));
for (const id of ['appDbUsername', 'appDbPassword', 'appDbClearPassword', 'appDbType', 'appDbName', 'appDbHost', 'appDbPort']) {
  assert.equal(id in fields, false);
  assert.equal(query.includes(id), false, 'removed control still referenced: ' + id);
}
const form = vm.createContext({document: {getElementById(id) {assert.ok(id in fields, id); return fields[id];}}});
vm.runInContext(functions(query, ['getAppSettingsPayload', 'fillAppSettingsForm']), form);
vm.runInContext(`let currentProfile = {app_settings: {database: {
  type: 'mysql', name: 'keep-db', host: 'central-db', port: 3307, launcher_managed: true}}};
  fillAppSettingsForm(currentProfile.app_settings)`, form);
fields.appRequestsPerMinute.value = '180';
const payload = vm.runInContext('getAppSettingsPayload()', form);
assert.equal(payload.database.name, 'keep-db');
assert.equal(payload.database.host, 'central-db');
assert.equal(payload.database.port, 3307);
assert.equal(payload.query_limits.requests_per_minute, 180);
for (const key of keys) assert.equal(key in payload.database, false);
const nodes = [];
function element(tag) {
  const node = {tag, children: [], textContent: '', classList: {add() {}},
    appendChild(child) {this.children.push(child);}, addEventListener() {},
    set innerHTML(value) {this.children = [];}};
  nodes.push(node);
  return node;
}
const panel = element('div');
context.document = {createElement: element, getElementById: () => panel};
context.SdI18n = {t: key => key};
context.currentConfig = config;
config.database.auto_create = true;
config.database.host = 'hidden-db-host';
vm.runInContext(functions(collector, ['renderDatabase', 'getGroupNameOptions', 'appendHeaders',
  'createHeaderCell', 'createRow', 'createInput', 'createSelect', 'createCheckbox', 'createMultiSelect']), context);
for (const enabled of [false, true]) {
  config.persistent_queue.enabled = enabled;
  nodes.length = 0;
  vm.runInContext('renderDatabase()', context);
  const text = nodes.map(node => node.textContent).join(' ');
  assert.ok(text.includes('collector.database.dataGroups'));
  assert.ok(text.includes('持久化队列'));
  assert.ok(!text.includes('自动创建数据库') && !text.includes('ROOT'));
  assert.ok(!nodes.some(node => node.value === 'hidden-db-host' || node.textContent === 'mysql'));
  assert.equal(nodes.filter(node => node.type === 'checkbox').length, 1);
  assert.equal(config.database.auto_create, true, 'Rendering must not rewrite the old config');
}
console.log('PASS managed database controls removed; query limits, data groups, queue and saved database preserved');
'''
    subprocess.run(["node", "-e", check, str(COLLECTOR / "web_config/static/config.js"),
                    str(QUERY / "app/static/config.js"), str(QUERY / "app/static/config.html")], check=True)


if __name__ == "__main__":
    main()
