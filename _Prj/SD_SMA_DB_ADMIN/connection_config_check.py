"""连接配置回归：真实配置 API 与页面脚本；所有写入均在临时目录。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent


def check_api(directory):
    for name in list(os.environ):
        if name.startswith('SD_SMA_DB_'):
            del os.environ[name]
    os.environ['SD_SMA_DB_ADMIN_CONFIG_DIR'] = str(directory / 'config')
    os.environ['SD_SMA_DB_ADMIN_BACKUP_DIR'] = str(directory / 'backups')
    os.environ['SD_SMA_DATA_ROOT'] = str(directory)
    os.environ['SD_SMA_SESSION_SECRET'] = 'connection-config-check'
    spec = importlib.util.spec_from_file_location('db_admin_config_check_app', ROOT / 'app/main.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        admin = client.get('/admin')
        config = client.get('/config')
        assert admin.status_code == config.status_code == 200
        assert 'id="dbPassword"' not in admin.text
        assert 'href="/config"' in admin.text and 'id="dbPassword"' in config.text
        assert 'id="outputDir"' not in admin.text and 'id="btnChooseOutputDir"' not in admin.text
        assert 'id="outputDir"' in config.text and 'id="btnSaveOutputDir"' in config.text
        assert client.get('/common/i18n.js').status_code == 200
        assert client.get('/static/filesystem.js').status_code == 200
        original_config = module.load_config()
        legacy_output = directory / 'legacy-output'
        legacy_output.mkdir()
        module.save_config({**original_config, 'last_output_dir': str(legacy_output)})
        assert client.get('/api/config').json()['output_dir'] == str(legacy_output)
        export_path = directory / 'exports'
        response = client.post('/api/config/output', json={'output_dir': str(export_path)})
        assert response.status_code == 200 and export_path.is_dir()
        assert client.get('/api/config').json()['output_dir'] == str(export_path)
        assert module.resolve_output_dir('') == export_path
        assert export_path in module.backup_roots()
        listing = client.get('/api/filesystem/entries', params={'path': str(export_path), 'purpose': 'directory'})
        assert listing.status_code == 200 and listing.json()['current'] == str(export_path)
        unavailable = directory / 'regular-file'
        unavailable.write_text('not a directory', encoding='utf-8')
        assert client.post('/api/config/output', json={'output_dir': str(unavailable)}).status_code == 400
        assert module.resolve_output_dir(None) == export_path
        assert client.post('/api/config/output', json={'output_dir': ''}).status_code == 200
        assert module.resolve_output_dir(None) == module.backup_dir()
        payload = dict(host='127.0.0.1', port=3306, username='root', password='test-only-password')
        saved = client.post('/api/config/connection', json=payload)
        assert saved.status_code == 200
        assert saved.json()['default_connection']['password'] == ''
        public = client.get('/api/config').json()['default_connection']
        assert public['password_configured'] and not public['password_managed']
        assert 'test-only-password' not in json.dumps(public)
        assert module.connection_password(module.DbConnection()) == payload['password']
        assert module.connection_password(module.DbConnection(password='new-test-password')) == 'new-test-password'
        client.post('/api/config/connection', json={**payload, 'password': ''})
        assert module.connection_password(module.DbConnection()) == payload['password']
        os.environ['SD_SMA_DB_PASSWORD'] = 'managed-test-password'
        os.environ['SD_SMA_DB_CREDENTIAL_MANAGED'] = '1'
        os.environ['SD_SMA_DB_HOST'] = 'managed-host'
        managed = client.get('/api/config').json()['default_connection']
        assert managed['password_managed'] and managed['host'] == 'managed-host'
        assert module.connection_password(module.DbConnection(password='ignored')) == 'managed-test-password'
        os.environ['SD_SMA_MANAGED_DATABASE'] = '1'
        os.environ['SD_SMA_DB_HOST'] = '127.0.0.1'
        os.environ['SD_SMA_DB_PORT'] = '1'
        managed = client.get('/api/config').json()['default_connection']
        assert managed['managed_storage'] and not any(key in managed for key in ('host', 'port', 'username'))
        assert 'managed-test-password' not in json.dumps(managed)
        assert client.post('/api/config/connection', json=payload).status_code == 409
        status = client.get('/api/storage/status').json()
        assert status == {'managed': True, 'available': False, 'error': 'unavailable'}
        assert 'id="managedStoragePanel"' in admin.text and 'id="managedStoragePanel"' in config.text
        os.environ.pop('SD_SMA_MANAGED_DATABASE')
        external = client.get('/api/storage/status').json()
        assert external == {'managed': False, 'external': True, 'available': False, 'error': 'unavailable'}
        assert 'free_bytes' not in external
        assert client.post('/api/config/connection', json=payload).status_code == 409
        assert client.post('/api/internal/database/pause').status_code == 403
        os.environ['SD_SMA_WEB_TOKEN'] = 'test-only-controller'
        headers = {'X-SD-SMA-Token': 'test-only-controller'}
        import threading
        release = threading.Event()
        try:
            module.start_job('test busy job', lambda job_id: release.wait(10))
            assert client.post('/api/internal/database/pause', headers=headers).status_code == 409
        finally:
            release.set()
        import time
        for _ in range(100):
            if all(job['status'] != 'running' for job in module._jobs.values()):
                break
            time.sleep(0.01)
        assert client.post('/api/internal/database/pause', headers=headers).status_code == 200
        assert client.post('/api/config/output', json={'output_dir': ''}).status_code == 409
        assert client.post('/api/internal/database/resume', headers=headers).status_code == 200


def check_browser_script():
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = process.argv[1];
const source = fs.readFileSync(root + '/config.js', 'utf8');
const admin = fs.readFileSync(root + '/admin.js', 'utf8');
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    value: '', type: 'password', options: [], textContent: '',
    addEventListener(type, handler) { this[type] = handler; },
    setAttribute() {}, reportValidity() { return true; },
  });
  return elements.get(id);
};
let stored = JSON.stringify({host: 'stale', username: 'stale', password: 'old-browser-secret', database: 'sample', outputDir: 'stale-directory'});
const requests = [];
let fail = false;
let failOutput = false;
const saved = {engine: 'mysql', host: '127.0.0.1', port: 3306, username: 'root', password: '', password_configured: true};
const window = {localStorage: {getItem: () => stored, setItem: (key, value) => {stored = value;}}};
const context = vm.createContext({
  document: {getElementById: element}, window,
  SdI18n: {t: key => key},
  fetch: async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({url, body});
    if (url === '/api/config/output') return {ok: !failOutput, json: async () => failOutput ?
      {detail: 'directory unavailable'} : {output_dir: body.output_dir}};
    return {ok: true, json: async () => url === '/api/config' ? {default_connection: saved, output_dir: 'saved-exports', backup_dir: 'default-backups'} :
      (url === '/api/connect/test' ? {ok: !fail, message: 'access denied'} : {default_connection: saved})};
  },
});
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  vm.runInContext(source, context);
  await flush();
  assert.equal(element('outputDir').value, 'saved-exports');
  element('outputDir').value = 'new-exports';
  await vm.runInContext('saveOutputDirectory()', context);
  assert.equal(requests.at(-1).url, '/api/config/output');
  assert.equal(requests.at(-1).body.output_dir, 'new-exports');
  assert.equal(element('outputHint').textContent, 'dbConfig.output.saved');
  failOutput = true;
  await vm.runInContext('saveOutputDirectory()', context);
  assert.equal(element('outputHint').textContent, 'dbConfig.output.failed');
  assert.equal(element('btnSaveOutputDir').disabled, false);
  element('btnUseDefaultOutputDir').click();
  assert.equal(element('outputDir').value, 'default-backups');
  element('dbPassword').value = 'temporary-test-password';
  await vm.runInContext('testConnection()', context);
  assert.equal(requests.at(-1).body.password, 'temporary-test-password');
  assert.equal(requests.filter(r => r.url === '/api/config/connection').length, 0);
  await vm.runInContext('testConnection(true)', context);
  assert.equal(requests.at(-1).body.password, 'temporary-test-password');
  assert.equal(requests.at(-1).url, '/api/config/connection');
  assert.equal(element('dbPassword').value, '');
  assert.equal(element('connectionHint').textContent, 'dbConfig.status.saved');
  assert.ok(!stored.includes('temporary-test-password'));
  fail = true;
  element('dbPassword').value = 'incorrect-test-password';
  const saves = requests.filter(r => r.url === '/api/config/connection').length;
  await vm.runInContext('testConnection(true)', context);
  assert.equal(requests.filter(r => r.url === '/api/config/connection').length, saves);
  assert.equal(element('dbPassword').value, 'incorrect-test-password');
  assert.equal(element('btnSaveConnection').disabled, false);
  saved.password_managed = true;
  await vm.runInContext('init()', context);
  assert.equal(element('connectionFields').disabled, true);
  assert.equal(element('btnSaveConnection').disabled, true);
  saved.managed_storage = true;
  await vm.runInContext('init()', context);
  assert.equal(element('connectionPanel').hidden, true);
  let storage = {managed: true, available: true, version: '11.4.13', size_bytes: 1024, free_bytes: 4096};
  const storageContext = vm.createContext({document: {getElementById: element}, SdI18n: {t: key => key},
    fetch: async () => ({ok: true, json: async () => storage})});
  vm.runInContext(fs.readFileSync(root + '/storage.js', 'utf8'), storageContext);
  await vm.runInContext('refreshManagedStorage()', storageContext);
  assert.equal(element('managedStoragePanel').hidden, false);
  assert.equal(element('managedStorageStatus').textContent, 'dbStorage.status.online');
  for (const code of ['credentials', 'disk', 'unavailable']) {
    storage = {managed: true, available: false, error: code};
    await vm.runInContext('refreshManagedStorage()', storageContext);
    assert.equal(element('managedStorageStatus').textContent, `dbStorage.status.${code}`);
  }
  storage = {managed: false};
  await vm.runInContext('refreshManagedStorage()', storageContext);
  assert.equal(element('managedStoragePanel').hidden, true);
  vm.runInContext('fetch = async () => { throw new Error(); }', storageContext);
  await vm.runInContext('refreshManagedStorage()', storageContext);
  assert.equal(element('managedStorageStatus').textContent, 'dbStorage.status.requestFailed');
  assert.equal(element('refreshManagedStorage').disabled, false);
  const adminContext = vm.createContext({document: {getElementById: element}, window, SdI18n: {t: key => key}});
  vm.runInContext(admin.replace(/init\(\)\.catch\([^\n]+\);\s*$/, ''), adminContext);
  vm.runInContext('loadSavedState()', adminContext);
  assert.ok(!stored.includes('old-browser-secret'));
  assert.ok(!stored.includes('stale-directory'));
  element('databaseSelect').value = 'sample';
  vm.runInContext("savedConnection = {host: 'server-from-config', username: 'saved-user'}", adminContext);
  const payload = vm.runInContext('getConnectionPayload()', adminContext);
  assert.equal(payload.host, 'server-from-config');
  assert.equal(payload.database, 'sample');
  vm.runInContext('saveState()', adminContext);
  assert.equal(JSON.parse(stored).password, undefined);
  assert.equal(JSON.parse(stored).outputDir, undefined);
  console.log('connection_config_check: API, password submission, save/failure, managed mode and browser state OK');
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    subprocess.run(['node', '-e', script, str(ROOT / 'app/static')], check=True)


def main():
    with tempfile.TemporaryDirectory(prefix='db_admin_config_check_') as directory:
        check_api(Path(directory))
    check_browser_script()


if __name__ == '__main__':
    main()
