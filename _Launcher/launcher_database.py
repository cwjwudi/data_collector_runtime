"""Unified database selection: read-only preflight, quiesce, commit, verify and rollback."""
from __future__ import annotations

import http.client
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

import pymysql

from launcher_security import DATABASE_SERVICES
from sd_sma_common.database_control import collector_pending_outboxes, database_target


REQUIRED = {
    'collector_web': {'SELECT', 'INSERT', 'UPDATE', 'CREATE', 'ALTER', 'INDEX'},
    'query_web': {'SELECT'},
    'db_admin': {'SELECT', 'INSERT', 'CREATE', 'DROP', 'ALTER', 'LOCK TABLES', 'EVENT'},
}


def connection_preflight(credential: dict, required: set) -> dict:
    """Inspect grants without creating a database, table, account or test row."""
    try:
        with pymysql.connect(host=credential['host'], port=credential['port'], user=credential['username'],
                             password=credential['password'], database=credential['database'],
                             connect_timeout=5, read_timeout=5, write_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT VERSION(), DATABASE(), CURRENT_ROLE()')
                version, database, role = cursor.fetchone()
                cursor.execute('SHOW GRANTS FOR CURRENT_USER')
                grants = [row[0] for row in cursor.fetchall()]
                if role and role != 'NONE':
                    if 'MariaDB' in version:
                        cursor.execute('SHOW GRANTS FOR ' + '`' + role.replace('`', '``') + '`')
                    else:
                        cursor.execute('SHOW GRANTS FOR CURRENT_USER USING ' + role)
                    grants.extend(row[0] for row in cursor.fetchall())
                allowed = set()
                for grant in grants:
                    match = re.match(r'GRANT (.+?) ON (.+)\.\* TO ', grant, re.I)
                    if not match:
                        continue
                    scope = match[2].strip('`').replace('``', '`')
                    cursor.execute('SELECT %s LIKE %s', (database, scope))
                    if scope == '*' or scope.replace('\\', '') == database or cursor.fetchone()[0]:
                        privileges = {value.strip().upper() for value in match[1].split(',')}
                        allowed.update(required if 'ALL PRIVILEGES' in privileges else privileges)
                cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                cipher = cursor.fetchone()
        missing = sorted(required - allowed)
        return dict(ok=not missing, connected=True, version=version, database=database,
                    missing=missing, tls=bool(cipher and cipher[1]),
                    error='permissions' if missing else '',
                    permission_check='grants_only', backup_restore_verified=False)
    except pymysql.MySQLError as error:
        code = error.args[0] if error.args else 0
        kind = {1044: 'permissions', 1045: 'authentication', 1049: 'database_missing',
                1251: 'authentication_plugin', 2059: 'authentication_plugin',
                2026: 'tls', 3159: 'tls_required'}.get(code, 'unavailable')
        return dict(ok=False, connected=False, error=kind, code=code)
    except (RuntimeError, ValueError, OSError) as error:
        detail = str(error).lower()
        kind = 'authentication_plugin' if any(value in detail for value in ('pynacl', 'plugin', 'cryptography')) else 'authentication_or_tls'
        return dict(ok=False, connected=False, error=kind)


class DatabaseController:
    def __init__(self, security, supervisor, collector_dir: Path, collector_cwd: Path, managed=None):
        self.security = security
        self.supervisor = supervisor
        self.collector_dir = collector_dir
        self.collector_cwd = collector_cwd
        self.managed = managed
        self.lock = threading.Lock()
        self.last_result = None

    @property
    def applying(self) -> bool:
        return self.lock.locked()

    def status(self) -> dict:
        public = self.security.public_credentials()
        targets = {}
        for name in DATABASE_SERVICES:
            try:
                credential = self.security.credential_for_service(name)
                targets[name] = dict(target=self._target(credential) if credential else None,
                                     source='managed' if credential and credential.get('managed_storage') else
                                     'external' if credential else 'local')
                service = next(row for row in self.supervisor.status()['services'] if row['name'] == name)
                targets[name]['state'] = service['state']
                if service['state'] == 'running':
                    try:
                        actual = self.rpc(name)
                        targets[name]['configured_target'] = targets[name]['target']
                        targets[name]['target'] = actual['target']
                        targets[name]['connected'] = actual['connected']
                        if name == 'db_admin':
                            targets[name]['tools'] = actual.get('tools', {})
                    except (OSError, ValueError, RuntimeError):
                        targets[name]['error'] = '无法确认服务实际连接'
            except RuntimeError as error:
                targets[name] = {'error': str(error)}
        binding = public['database_binding']
        return dict(binding=binding, legacy=binding is None, targets=targets, applying=self.applying,
                    managed_installed=bool(self.managed), error=public['error'], last_result=self.last_result)

    def candidate(self, payload: dict) -> tuple[dict, dict[str, dict]]:
        if not isinstance(payload, dict):
            raise ValueError('数据库配置必须为对象')
        mode = payload.get('mode')
        if mode == 'managed':
            if self.managed is None:
                raise ValueError('当前环境未安装内置数据库')
            try:
                credentials = self.managed.credentials()
            except (OSError, RuntimeError, ValueError):
                raise ValueError('内置数据库不可用，请检查原实例和凭据文件；不会重新初始化') from None
            return {'mode': mode}, credentials
        if mode != 'external':
            raise ValueError('请选择内置或外部数据库')
        incoming = payload.get('credential') or {}
        if not isinstance(incoming, dict):
            raise ValueError('外部连接参数无效')
        credential_id = str(payload.get('external_credential_id') or incoming.get('id') or secrets.token_hex(8))
        existing = self.security.credential_by_id(credential_id) or {}
        credential = {key: incoming.get(key, existing.get(key))
                      for key in ('host', 'port', 'username', 'database', 'name')}
        credential.update(id=credential_id, engine='mysql')
        for field in ('host', 'username', 'database'):
            credential[field] = str(credential.get(field) or '').strip()
            if not credential[field]:
                raise ValueError('外部数据库必须填写地址、库名、用户名和密码')
        credential['password'] = str(incoming.get('password') or existing.get('password') or '')
        if not credential['password']:
            raise ValueError('新外部档案必须填写密码')
        credential['port'] = int(credential.get('port') or 3306)
        if not 1 <= credential['port'] <= 65535 or not re.fullmatch(r'[\w-]+', credential_id):
            raise ValueError('数据库端口或档案 ID 无效')
        if not re.fullmatch(r'[A-Za-z0-9_]+', credential['database']):
            raise ValueError('业务库名仅允许字母、数字和下划线（沿用 DB Admin 限制）')
        credential['name'] = str(credential.get('name') or 'SD SMA external')
        return dict(mode=mode, external_credential_id=credential_id), dict.fromkeys(DATABASE_SERVICES, credential)

    def test(self, payload: dict) -> dict:
        binding, credentials = self.candidate(payload)
        results = self._preflight(credentials)
        return dict(ok=all(item['ok'] for item in results.values()), binding=binding, services=results,
                    target=self._target(credentials['collector_web']))

    def _preflight(self, credentials: dict) -> dict:
        results = {name: connection_preflight(credentials[name], REQUIRED[name]) for name in DATABASE_SERVICES}
        admin = next(row for row in self.supervisor.status()['services'] if row['name'] == 'db_admin')
        if admin['state'] == 'running':
            runtime = self.rpc('db_admin')
            results['db_admin']['tools'] = runtime.get('tools', {})
            results['db_admin']['tools_available'] = runtime.get('tools_available', False)
            if not runtime.get('tools_available'):
                results['db_admin'].update(ok=False, error='client_tools')
        return results

    def apply(self, payload: dict) -> dict:
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('数据库配置正在应用，请勿重复操作')
        try:
            return self._apply(payload)
        finally:
            self.lock.release()

    def _apply(self, payload: dict) -> dict:
        binding, credentials = self.candidate(payload)
        preflight = self._preflight(credentials)
        if not all(item['ok'] for item in preflight.values()):
            self.last_result = dict(ok=False, stage='preflight', services=preflight)
            return self.last_result
        before = {item['name']: item for item in self.supervisor.status()['services']}
        if any(before[name]['state'] not in ('running', 'stopped', 'failed') for name in DATABASE_SERVICES):
            raise RuntimeError('服务正在启动或停止，请稍后重试')
        running = [name for name in DATABASE_SERVICES if before[name]['pid']]
        snapshot = self.security.database_snapshot()
        collector = None
        paused = []
        changed = False
        stopped = False
        resuming = False
        try:
            if 'db_admin' in running:
                self.rpc('db_admin', 'pause')
                paused.append('db_admin')
            if 'collector_web' in running:
                collector = self.rpc('collector_web')
                paused.append('collector_web')
                drained = self.rpc('collector_web', 'pause')
                sizes = drained['storage']
                keys = ('queue_size', 'retry_queue_size', 'dead_letter_queue_size', 'inflight_size',
                        'outbox_pending_size', 'outbox_processing_size', 'outbox_retry_size', 'outbox_dead_letter_size')
                if any(sizes.get(key, 0) for key in keys) or drained.get('outboxes'):
                    raise RuntimeError('旧库待写或死信任务尚未清空，请恢复旧库并处理完成后重试')
            if collector_pending_outboxes(self.collector_dir, self.collector_cwd):
                raise RuntimeError('旧库持久化任务尚未完成，未切换数据库')
            self.supervisor.database_hold(True)
            stopped = True
            self.supervisor.stop_for_database(list(DATABASE_SERVICES))
            self.security.apply_database_binding(binding, credentials['collector_web'] if binding['mode'] == 'external' else None)
            changed = True
            states = self._start_and_verify(running, credentials, collector)
            resuming = True
            self._release(running, collector)
            states = {name: self.rpc(name) for name in running}
            self.last_result = dict(ok=True, stage='complete', services=states, binding=binding)
        except Exception as error:
            rollback = dict(ok=True, services={})
            try:
                if resuming and 'collector_web' in running:
                    drained = self.rpc('collector_web', 'pause')
                    if drained.get('outboxes') or any(drained['storage'].get(key, 0) for key in
                            ('queue_size', 'retry_queue_size', 'dead_letter_queue_size',
                             'outbox_pending_size', 'outbox_processing_size', 'outbox_retry_size', 'outbox_dead_letter_size')):
                        raise RuntimeError('新库尚有待写任务，禁止向旧库回放')
                if stopped:
                    self.supervisor.stop_for_database(list(DATABASE_SERVICES))
                if changed:
                    self.security.restore_database(snapshot)
                if stopped:
                    old = {name: self.security.credential_for_service(name) for name in DATABASE_SERVICES}
                    rollback['services'] = self._start_and_verify(running, old, collector)
                self._release(paused if not stopped else running, collector)
            except Exception:
                rollback['ok'] = False
                try:
                    # A live collector may own memory-only failed rows; keep its paused host alive.
                    self.supervisor.stop_for_database([name for name in DATABASE_SERVICES if name != 'collector_web'])
                except Exception:
                    pass
            self.last_result = dict(ok=False, stage='rollback' if changed or stopped else 'quiesce',
                                    error=str(error) if isinstance(error, RuntimeError) else '数据库切换失败',
                                    rollback=rollback, binding=self.security.public_credentials()['database_binding'],
                                    services={row['name']: row for row in self.supervisor.status()['services']})
        finally:
            self.supervisor.database_hold(False)
        return self.last_result

    def _start_and_verify(self, running: list, credentials: dict, collector: dict | None) -> dict:
        for name in running:
            self.supervisor.command(name, 'start')
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            states = {row['name']: row for row in self.supervisor.status()['services']}
            if all(states[name]['state'] == 'running' and states[name]['health_ready'] for name in running):
                break
            if any(states[name]['state'] == 'failed' for name in running):
                raise RuntimeError('新配置服务启动失败')
            time.sleep(0.1)
        else:
            raise RuntimeError('数据库配置服务启动超时')
        if collector and collector['running'] and 'collector_web' in running:
            self.rpc('collector_web', 'prepare', {'filename': collector['filename']})
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                state = self.rpc('collector_web')
                if state['phase'] == 'paused' and state['task_active']:
                    break
                if state['phase'] == 'idle':
                    raise RuntimeError('采集运行态初始化失败')
                time.sleep(0.1)
            else:
                raise RuntimeError('采集运行态初始化超时')
        results = {}
        for name in running:
            actual = self.rpc(name)
            if name == 'db_admin' and not actual.get('tools_available', False):
                raise RuntimeError('DB Admin 数据库客户端工具不可用，请修复安装或工具配置')
            if not actual['connected'] or (credentials[name] and actual['target'] != self._target(credentials[name])):
                raise RuntimeError('服务实际连接未通过目标一致性检查：' + name)
            results[name] = actual
        return results

    def _release(self, names: list, collector: dict | None) -> None:
        for name in ('db_admin', 'collector_web'):
            if name in names:
                self.rpc(name, 'resume')
        if collector and collector['running'] and 'collector_web' in names:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                actual = self.rpc('collector_web')
                if actual['running'] and actual['phase'] == 'running' and actual['task_active']:
                    return
                time.sleep(0.1)
            raise RuntimeError('采集状态恢复失败')

    def rpc(self, name: str, action: str = '', body=None) -> dict:
        service = next(item for item in self.supervisor.status()['services'] if item['name'] == name)
        connection = http.client.HTTPConnection('127.0.0.1', service['port'], timeout=75)
        try:
            path = '/api/internal/database' + ('/' + action if action else '')
            connection.request('POST' if action else 'GET', path, json.dumps(body or {}),
                               {'Content-Type': 'application/json', 'X-SD-SMA-Token': os.environ['SD_SMA_WEB_TOKEN']})
            response = connection.getresponse()
            data = json.loads(response.read())
            if response.status != 200:
                raise RuntimeError(str(data.get('detail') or '服务数据库控制失败'))
            return data
        finally:
            connection.close()

    @staticmethod
    def _target(credential: dict) -> dict:
        return database_target(dict(type=credential['engine'], host=credential['host'], port=credential['port'],
                                    name=credential['database'], username=credential['username']))
