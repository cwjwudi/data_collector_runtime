"""DPAPI and credential routing checks; --mariadb-home runs real isolated provisioning.

Live mode replaces only the administrator-only ACL step for a user-owned temporary
directory. Service registration and production ACL acceptance belong to the VM stage.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import sys

import pymysql

from launcher_security import LauncherSecurityStore
from managed_database import ManagedDatabase, assert_port_free, protect
from sd_sma_launcher import service_with_launcher_credential, safe_env_display


class TemporaryDatabase(ManagedDatabase):
    def _secure_secrets(self):
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mariadb-home', type=Path)
    args = parser.parse_args()
    payload = b'dpapi-selfcheck-not-a-real-password'
    encrypted = protect(payload)
    assert payload not in encrypted and protect(encrypted, decrypt=True) == payload
    try:
        protect(encrypted[:-5], decrypt=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Damaged DPAPI blob accepted')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.listen()
        try:
            assert_port_free(port)
        except RuntimeError as error:
            assert 'occupied' in str(error)
        else:
            raise AssertionError('Busy database port accepted')
    print('PASS machine DPAPI roundtrip, corruption rejection, port conflict')
    check_legacy_routing()
    check_config_override()
    if args.mariadb_home:
        live(args.mariadb_home.resolve(), port)
    else:
        print('SKIP live provisioning (requires --mariadb-home); administrator ACL and services not exercised')


def check_legacy_routing():
    services = ('collector_web', 'query_web', 'db_admin')
    managed = {
        name: dict(engine='mysql', host='127.0.0.1', port=3307, database='sd_sma',
                   username=f'sma_{name}', password=f'test-only-{name}', managed_storage=True)
        for name in services
    }
    root = dict(id='external-root', engine='mysql', host='root.example.invalid', port=3306,
                database='external_a', username='root', password='test-only-root')
    operator = dict(id='external-operator', engine='mysql', host='operator.example.invalid', port=3308,
                    database='external_b', username='sma_operator', password='test-only-operator')
    cases = (
        ('managed-default', {}, managed),
        ('shared-root', dict.fromkeys(services, root['id']), dict.fromkeys(services, root)),
        ('shared-operator', dict.fromkeys(services, operator['id']), dict.fromkeys(services, operator)),
        ('mixed', {'collector_web': root['id'], 'db_admin': operator['id']},
         {'collector_web': root, 'query_web': managed['query_web'], 'db_admin': operator}),
    )
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for label, assignments, expected in cases:
            path = directory / f'{label}.json'
            legacy = dict(version=1, credentials=[root, operator], assignments=assignments)
            path.write_text(json.dumps(legacy), encoding='utf-8')
            original = path.read_bytes()
            store = LauncherSecurityStore(path, managed_credentials=managed)
            for name in services:
                credential = store.credential_for_service(name)
                assert credential == expected[name], (label, name)
                service = {'name': name, 'env': {'KEEP': 'unchanged', 'SD_SMA_DB_HOST': 'stale'}}
                before = json.dumps(service)
                env = service_with_launcher_credential(service, store)['env']
                assert json.dumps(service) == before
                for field, key in (('host', 'HOST'), ('port', 'PORT'), ('database', 'DATABASE'),
                                   ('username', 'USERNAME'), ('password', 'PASSWORD'), ('engine', 'ENGINE')):
                    assert env[f'SD_SMA_DB_{key}'] == str(credential[field]), (label, name, field)
                assert env['SD_SMA_DB_CREDENTIAL_MANAGED'] == '1' and env['KEEP'] == 'unchanged'
                assert env['SD_SMA_MANAGED_DATABASE'] == ('1' if credential.get('managed_storage') else '')
                credential['password'] = 'mutated-copy'
                assert store.credential_for_service(name) == expected[name]
            report = {'name': 'report_copy', 'env': {'KEEP': 'unchanged'}}
            assert service_with_launcher_credential(report, store) == report
            public = store.public_credentials()
            assert public['assignments'] == assignments and public['managed_database']
            for credential in [root, operator, *managed.values()]:
                assert credential['password'] not in json.dumps(public)
            assert path.read_bytes() == original, label
            reloaded = LauncherSecurityStore(path, managed_credentials=managed)
            assert [reloaded.credential_for_service(name) for name in services] == [expected[name] for name in services]
            assert path.read_bytes() == original, label
        missing = directory / 'source-without-launcher.json'
        store = LauncherSecurityStore(missing)
        for name in (*services, 'report_copy'):
            service = {'name': name, 'env': {'KEEP': 'unchanged'}}
            assert store.credential_for_service(name) is None
            assert service_with_launcher_credential(service, store) == service
        assert not missing.exists()
    print('PASS legacy routing: managed defaults, shared root/operator, mixed assignments, reload without rewrite')
    print('PASS source without Launcher credentials: no injection; ordinary Report Copy has no database credentials')


def check_config_override():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / '_Prj/SD_SMA_DATA_COLLECTOR'))
    sys.path.insert(0, str(root / '_Prj/SD_SMA_DATA_COLLECTOR_QUERY_WEB'))
    from core.config_loader import ConfigLoader
    from app.config_manager import UnifiedConfigStore
    environment = dict(os.environ)
    try:
        os.environ.update(SD_SMA_DB_ENGINE='mysql', SD_SMA_DB_DATABASE='managed_test',
                          SD_SMA_DB_PASSWORD='test-only', SD_SMA_DB_USERNAME='sma_query')
        assert ConfigLoader._parse_config({'database': {'type': 'sqlite'}}).database.type == 'mysql'
        with tempfile.TemporaryDirectory() as temporary:
            store = UnifiedConfigStore(Path(temporary))
            store.save_app_settings({'database': {'type': 'sqlite', 'name': 'local.db'}})
            settings = store.get_app_settings()['database']
            assert settings['type'] == 'mysql' and settings['name'] == 'managed_test'
            assert settings['username'] == 'sma_query' and settings['password'] == 'test-only'
            assert 'test-only' not in json.dumps(store.get_public_app_settings())
    finally:
        os.environ.clear()
        os.environ.update(environment)
    print('PASS collector and query configurations use injected engine and credentials')


def live(home, port):
    with tempfile.TemporaryDirectory(prefix='sma-managed-') as temporary:
        root = Path(temporary)
        database = TemporaryDatabase(root, root / 'data', {'port': port, 'database': 'sd_sma'})
        database.binary = home / 'bin'
        if not ctypes.windll.shell32.IsUserAnAdmin():
            try:
                ManagedDatabase._secure_secrets(database)
            except RuntimeError as error:
                assert 'elevated' in str(error)
            else:
                raise AssertionError('Non-administrator provisioning was accepted')
        # Fail after the real system-table installation, then rerun against its unpublished staging directory.
        original_run = database._run
        def fail_bootstrap(command, sql=None):
            if sql:
                raise RuntimeError('injected interruption before bootstrap')
            original_run(command, sql)
        database._run = fail_bootstrap
        try:
            database.initialize()
        except RuntimeError as error:
            assert 'injected interruption' in str(error)
        else:
            raise AssertionError('Injected initialization failure was ignored')
        before = database._load()
        database._run = original_run
        database.initialize()
        assert database._load()['passwords'] == before['passwords']
        assert not (database.data_root / 'database.initializing').exists()
        secret_bytes = database.secret_file.read_bytes()
        database.initialize()
        assert database.secret_file.read_bytes() == secret_bytes
        credentials = database.credentials()
        store = LauncherSecurityStore(root / 'launcher.json', managed_credentials=credentials)
        store.disable_pin()
        public = json.dumps(store.public_credentials())
        assert store.public_credentials()['managed_database']
        for service, credential in credentials.items():
            assert credential['password'].encode() not in secret_bytes
            assert credential['password'] not in public
            assert credential['password'] not in store.path.read_text()
            env = service_with_launcher_credential({'name': service}, store)['env']
            assert env['SD_SMA_DB_PASSWORD'] == credential['password']
            assert env['SD_SMA_DB_ENGINE'] == 'mysql' and env['SD_SMA_MANAGED_DATABASE'] == '1'
            assert credential['password'] not in safe_env_display('SD_SMA_DB_PASSWORD', credential['password'])
        external = store.upsert_credential(dict(name='external', host='127.0.0.2', username='external', password='test-only'))
        store.assign('query_web', external['id'])
        assert store.credential_for_service('query_web')['host'] == '127.0.0.2'
        store.assign('query_web', None)
        assert store.credential_for_service('query_web') == credentials['query_web']
        print('PASS interrupted initialization retry, same-instance reuse, encrypted credentials, external override')
        process = subprocess.Popen([str(database.binary / 'mariadbd.exe'),
                                    f'--defaults-file={database.directory / "my.ini"}', '--console'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        admin = None
        try:
            for credential in credentials.values():
                database.wait_ready(credential)
            admin = pymysql.connect(host='127.0.0.1', port=port, user='root',
                                    password=before['root_password'], autocommit=True)
            with admin.cursor() as cursor:
                cursor.execute('CREATE DATABASE sdXsma')
                cursor.execute('CREATE TABLE sdXsma.hidden (id INT)')
                cursor.execute('SELECT User,Host FROM mysql.user')
                users = cursor.fetchall()
                assert not any(user == '' or (user == 'root' and host != 'localhost') for user, host in users), users
            for credential in credentials.values():
                with pymysql.connect(host='127.0.0.1', port=port, user=credential['username'],
                                     password=credential['password'], database='sd_sma') as connection:
                    with connection.cursor() as cursor:
                        for statement in ('SELECT * FROM sdXsma.hidden', 'CREATE DATABASE test', 'CREATE DATABASE test_escape'):
                            try:
                                cursor.execute(statement)
                            except pymysql.MySQLError as error:
                                assert error.args[0] in (1044, 1142)
                            else:
                                raise AssertionError(f'Unexpected grant: {statement}')
            print('PASS real MariaDB logins, bootstrap root lockdown, exact database grant boundary')
        finally:
            if admin:
                with admin.cursor() as cursor:
                    cursor.execute('SHUTDOWN')
                admin.close()
            else:
                process.terminate()
            process.wait(timeout=30)
        assert database.secret_file.read_bytes() == secret_bytes
        # A ready instance whose data disappeared must not silently start with an empty database.
        database.directory.rename(database.data_root / 'retained-database')
        try:
            database.initialize()
        except RuntimeError as error:
            assert 'files are missing' in str(error)
        else:
            raise AssertionError('Missing existing data was silently replaced')
        assert database.secret_file.read_bytes() == secret_bytes
        print('PASS missing existing data rejected without replacing credentials')


if __name__ == '__main__':
    main()
