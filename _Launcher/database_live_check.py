"""Opt-in real switch acceptance on owned temporary instances; never uses existing database services."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import psutil
import pymysql
from opcua import Server

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / '_Prj/SD_SMA_COMMON'), str(ROOT / '_Prj/SD_SMA_DB_ADMIN')]
from managed_mariadb_check import isolated_database
from managed_database_check import TemporaryDatabase
from launcher_security import LauncherSecurityStore, DATABASE_SERVICES


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def call(port, path, body=None):
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=160) as response:
        return json.load(response)


def stop_launcher(process):
    children = psutil.Process(process.pid).children(recursive=True)
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    process.terminate()
    psutil.wait_procs(children, timeout=10)
    process.wait(timeout=10)


def run(maria_home, mysql_home):
    with ExitStack() as stack:
        maria_admin, maria_port, maria_bin, directory = stack.enter_context(isolated_database(maria_home))
        mysql_admin, mysql_port, mysql_bin, _ = stack.enter_context(isolated_database(mysql_home, mariadb=False))
        package = directory / 'package'
        static = package / '_Launcher/static'
        shutil.copytree(ROOT / '_Launcher/static', static)
        shutil.copytree(ROOT / '_Prj/SD_SMA_COMMON/sd_sma_common/web', package / '_Prj/SD_SMA_COMMON/sd_sma_common/web')
        tools = package / '_Database/bin'
        tools.mkdir(parents=True)
        for source in [*(maria_bin.glob('*.dll')), maria_bin / 'mariadb.exe', maria_bin / 'mariadb-dump.exe']:
            shutil.copy2(source, tools / source.name)
        data = package / 'data'
        plc = Server()
        plc_port = free_port()
        plc.set_endpoint(f'opc.tcp://127.0.0.1:{plc_port}')
        namespace = plc.register_namespace('sd-sma-isolated-switch-check')
        value_node = plc.get_objects_node().add_variable(namespace, 'Value', 'source_b')
        plc.start()
        stack.callback(plc.stop)
        password = secrets.token_urlsafe(24)
        for admin, name in ((maria_admin, 'external_b'), (mysql_admin, 'external_a')):
            with admin.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE {name} CHARACTER SET utf8mb4')
                cursor.execute(f'CREATE TABLE {name}.identity_marker (value VARCHAR(20))')
                cursor.execute(f'INSERT INTO {name}.identity_marker VALUES (%s)', (name,))
                cursor.execute("CREATE USER 'operator'@'127.0.0.1' IDENTIFIED BY %s", (password,))
                cursor.execute(f"GRANT ALL PRIVILEGES ON `{name.replace('_', chr(92)+'_')}`.* TO 'operator'@'127.0.0.1'")
        credential_a = dict(id='external-a', engine='mysql', host='127.0.0.1', port=mysql_port,
                            username='operator', password=password, database='external_a')
        credential_b = dict(credential_a, id='external-b', port=maria_port, database='external_b')
        root_password = secrets.token_urlsafe(24)
        with mysql_admin.cursor() as cursor:
            cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (root_password,))
        root_credential = dict(credential_a, id='external-root', username='root', password=root_password)
        security_path = data / 'secrets/launcher_security.json'
        store = LauncherSecurityStore(security_path)
        store.disable_pin()
        store.upsert_credential(credential_a)
        store.upsert_credential(credential_b)
        store.upsert_credential(root_credential)
        store.apply_database_binding(dict(mode='external', external_credential_id='external-a'))
        config = json.loads((ROOT / '_Launcher/launcher_config.json').read_text(encoding='utf-8'))
        config.update(python=str(ROOT / '.venv/Scripts/python.exe'), auto_install_missing=False, open_browser=False)
        config['managed_database'] = dict(enabled=True, port=free_port(), database='sd_sma')
        config['management']['port'] = management_port = free_port()
        for service in config['services']:
            source = ROOT / service['cwd']
            destination = package / service['cwd']
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.db', '*.db-*',
                            'logs', 'state', 'secrets', 'backups', 'exports', 'config', 'docs'))
            service['cwd'] = str(destination)
            service['port'] = free_port()
            service['host'] = '127.0.0.1'
        config_path = package / '_Launcher/launcher_config.json'
        config_path.write_text(json.dumps(config), encoding='utf-8')
        collector_dir = data / 'config/collector'
        collector_dir.mkdir(parents=True)
        collector_config = dict(
            points=[dict(name='Value', path=value_node.nodeid.to_string(), description='test-only')],
            groups=[dict(name='Data_Switch', interval_seconds=0.1, trigger='time', description='test-only',
                         data_points=['Value'], batch_insert_size=100, partition_interval_years=0)],
            communications=[dict(name='plc', type='opcua', host='127.0.0.1', port=plc_port)],
            connections=[dict(name='plc', communication='plc', data_groups=['Data_Switch'])],
            database=dict(type='mysql', name='ignored', data_groups=['Data_Switch']),
            persistent_queue=dict(enabled=True, path=str(data / 'test-outbox.db')))
        (collector_dir / 'switch.json').write_text(json.dumps(collector_config), encoding='utf-8')
        env = dict(os.environ, SD_SMA_LAUNCHER_DIR=str(package / '_Launcher'), SD_SMA_DATA_ROOT=str(data),
                   PYTHONIOENCODING='utf-8', PYTHONPATH=str(ROOT / '_Prj/SD_SMA_COMMON'))
        for key in list(env):
            if key.startswith('SD_SMA_DB_') or key in ('SD_SMA_MANAGED_DATABASE', 'SD_SMA_DATABASE_SWITCH_HOLD'):
                del env[key]
        def start_launcher():
            log = stack.enter_context((directory / f'launcher-{time.time_ns()}.log').open('wb'))
            process = subprocess.Popen(['cmd', '/c', str(ROOT / 'start.bat'), '--config', str(config_path), '--no-browser'],
                                       cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    status = call(management_port, '/api/launcher/status')
                    if all(item['state'] == 'running' for item in status['services']):
                        return process
                except (OSError, ValueError):
                    pass
                if process.poll() is not None:
                    break
                time.sleep(0.2)
            stop_launcher(process)
            raise AssertionError('Isolated launcher did not become ready; inspect its temporary log')
        launcher = start_launcher()
        try:
            assert not (data / 'secrets/managed_database.dpapi').exists()
            current = call(management_port, '/api/launcher/database')
            assert current['binding']['external_credential_id'] == 'external-a'
            print('PASS external startup: four real services and management ready with missing managed credentials/instance', flush=True)
            result = call(management_port, '/api/launcher/database/apply', dict(mode='external', external_credential_id='external-root'))
            assert result['ok'] and all(row['target']['username'] == 'root' for row in result['services'].values()), result
            assert call(management_port, '/api/launcher/database/apply', dict(mode='external', external_credential_id='external-a'))['ok']
            print('PASS external root and scoped operator both drive the three real services', flush=True)
            original = security_path.read_bytes()
            assert call(management_port, '/api/launcher/database/test', dict(mode='external', external_credential_id='external-b'))['ok']
            assert security_path.read_bytes() == original
            # Change only the owned candidate account after real preflight/commit to fail service readiness.
            cancel_fault = threading.Event()
            injected_fault = threading.Event()
            def invalidate_candidate():
                while not cancel_fault.wait(0.005):
                    saved = json.loads(security_path.read_text(encoding='utf-8'))
                    if saved.get('database_binding', {}).get('external_credential_id') == 'external-b':
                        with maria_admin.cursor() as cursor:
                            cursor.execute("ALTER USER 'operator'@'127.0.0.1' IDENTIFIED BY %s", (secrets.token_urlsafe(24),))
                        injected_fault.set()
                        return
            fault_thread = threading.Thread(target=invalidate_candidate, daemon=True)
            fault_thread.start()
            try:
                result = call(management_port, '/api/launcher/database/apply', dict(mode='external', external_credential_id='external-b'))
                assert injected_fault.is_set() and not result['ok'] and result['rollback']['ok'], result
                assert result['binding']['external_credential_id'] == 'external-a', result
                print('PASS real candidate startup failure after preflight: old binding and real service connections restored', flush=True)
            finally:
                cancel_fault.set()
                fault_thread.join(timeout=10)
                with maria_admin.cursor() as cursor:
                    cursor.execute("ALTER USER 'operator'@'127.0.0.1' IDENTIFIED BY %s", (password,))
            for credential_id in ('external-b', 'external-a'):
                if credential_id == 'external-a':
                    collector_port = next(row['port'] for row in config['services'] if row['name'] == 'collector_web')
                    call(collector_port, '/api/collector/start', {'filename': 'switch.json'})
                    for _ in range(100):
                        state = call(collector_port, '/api/collector/status')
                        if (state.get('snapshot') or {}).get('storage', {}).get('queue_size', 0) > 0:
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError('Simulated PLC did not produce an old-target pending record')
                result = call(management_port, '/api/launcher/database/apply', dict(mode='external', external_credential_id=credential_id))
                assert result['ok'], result
                expected = 'external_b' if credential_id == 'external-b' else 'external_a'
                assert all(row['target']['name'] == expected for row in result['services'].values()), result
                if credential_id == 'external-a':
                    assert result['services']['collector_web']['running']
                    assert result['services']['collector_web']['phase'] == 'running'
                    with maria_admin.cursor() as cursor:
                        cursor.execute('SELECT COUNT(*) FROM external_b.Data_Switch')
                        assert cursor.fetchone()[0] > 0
                    call(collector_port, '/api/collector/stop', {})
                    print('PASS active collection: real simulated OPC UA, old-target partial batch drained, new-target runtime verified and resumed', flush=True)
            # Same-target credential change goes through the complete apply path.
            changed_password = secrets.token_urlsafe(24)
            with mysql_admin.cursor() as cursor:
                cursor.execute("ALTER USER 'operator'@'127.0.0.1' IDENTIFIED BY %s", (changed_password,))
            result = call(management_port, '/api/launcher/database/apply', dict(mode='external', external_credential_id='external-a',
                          credential={'password': changed_password}))
            assert result['ok'], result
            stop_launcher(launcher)
            launcher = start_launcher()
            assert call(management_port, '/api/launcher/database')['binding']['external_credential_id'] == 'external-a'
            print('PASS live external A/B switches, password change, read-only unsaved test, persisted selection after Launcher restart', flush=True)
            call(management_port, '/api/launcher/services/query_web/stop', {})
            for _ in range(100):
                state = next(row for row in call(management_port, '/api/launcher/status')['services'] if row['name'] == 'query_web')
                if state['state'] == 'stopped':
                    break
                time.sleep(0.1)
            result = call(management_port, '/api/launcher/database/apply', dict(mode='external', external_credential_id='external-b'))
            assert result['ok'] and 'query_web' not in result['services'], result
            state = next(row for row in call(management_port, '/api/launcher/status')['services'] if row['name'] == 'query_web')
            assert state['pid'] is None and not state['desired_running']
            call(management_port, '/api/launcher/services/query_web/start', {})
            for _ in range(100):
                state = next(row for row in call(management_port, '/api/launcher/status')['services'] if row['name'] == 'query_web')
                if state['state'] == 'running':
                    break
                time.sleep(0.1)
            assert state['state'] == 'running', state
            print('PASS stopped real Query service remains stopped across selection change', flush=True)
            managed = TemporaryDatabase(package, data, config['managed_database'])
            managed.binary = maria_bin
            managed.initialize()
            log = stack.enter_context((directory / 'managed.log').open('wb'))
            server = subprocess.Popen([str(maria_bin / 'mariadbd.exe'), f'--defaults-file={managed.directory / "my.ini"}', '--console'],
                                      stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                for credential in managed.credentials().values():
                    managed.wait_ready(credential)
                result = call(management_port, '/api/launcher/database/apply', {'mode': 'managed'})
                assert result['ok'], result
                assert len({row['target']['username'] for row in result['services'].values()}) == 3
                result = call(management_port, '/api/launcher/database/apply', dict(mode='external', external_credential_id='external-b'))
                assert result['ok'], result
                print('PASS live external to managed to external; managed accounts remain distinct', flush=True)
            finally:
                server.terminate()
                server.wait(timeout=20)
            for admin, name in ((maria_admin, 'external_b'), (mysql_admin, 'external_a')):
                with admin.cursor() as cursor:
                    cursor.execute(f'SELECT value FROM {name}.identity_marker')
                    assert cursor.fetchall() == ((name,),)
            print('PASS original histories remain on their own targets; no automatic migration or merge', flush=True)
        finally:
            if launcher.poll() is None:
                stop_launcher(launcher)
    print('database_live_check: all live assertions passed; temporary instances stopped and removed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mariadb-home', type=Path)
    parser.add_argument('--mysql-home', type=Path)
    args = parser.parse_args()
    if not args.mariadb_home or not args.mysql_home:
        print('database_live_check: SKIP live switching (requires --mariadb-home and --mysql-home)')
        return
    run(args.mariadb_home.resolve(), args.mysql_home.resolve())


if __name__ == '__main__':
    main()
