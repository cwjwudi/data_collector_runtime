"""真实 MariaDB 验证；--mariadb-home 指向官方 Windows ZIP 解压目录。

仅创建 _Build 下的临时实例，不连接已有服务。无参数时明确跳过实测。
"""
import argparse
import asyncio
import importlib.util
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import pymysql

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_Prj/SD_SMA_DATA_COLLECTOR'))
sys.path.insert(0, str(ROOT / '_Prj/SD_SMA_DATA_COLLECTOR_QUERY_WEB'))
from database.db_manager import DatabaseManager
from database.data_storage import DataStorageProcessor
from app.database import QueryDatabase
from app.models import HistoryQueryRequest

DATABASE = 'sma_validation'
# 数据库级 GRANT 会把下划线当作通配符，反引号本身不会取消通配。
DATABASE_GRANT = DATABASE.replace('_', r'\_')
GRANTS = {
    'collector': 'SELECT, INSERT, UPDATE, CREATE, ALTER, INDEX',
    'query': 'SELECT',
    'maintenance': 'SELECT, INSERT, CREATE, DROP, ALTER, LOCK TABLES, EVENT',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mariadb-home', type=Path)
    args = parser.parse_args()
    if args.mariadb_home is None:
        print('managed_mariadb_check: SKIP live validation (requires --mariadb-home)')
        return
    binary = args.mariadb_home.resolve() / 'bin'
    for name in ('mariadb-install-db.exe', 'mariadbd.exe', 'mariadb.exe', 'mariadb-dump.exe'):
        assert (binary / name).is_file(), binary / name
    for name in list(os.environ):
        if name.startswith('SD_SMA_DB_'):
            del os.environ[name]
    build = ROOT / '_Build/mariadb-validation'
    build.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='instance-', dir=build) as temporary:
        directory = Path(temporary)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        subprocess.run([str(binary / 'mariadb-install-db.exe'),
                        f'--datadir={directory / "data"}', f'--port={port}'],
                       check=True, capture_output=True, timeout=90)
        with (directory / 'server.log').open('wb') as log:
            process = subprocess.Popen([
                str(binary / 'mariadbd.exe'), '--no-defaults',
                f'--basedir={binary.parent}', f'--datadir={directory / "data"}',
                '--bind-address=127.0.0.1', f'--port={port}', '--console',
            ], stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
            admin = None
            try:
                admin = wait_ready(process, port)
                check_database(admin, port, binary, directory)
            finally:
                if admin is not None:
                    try:
                        with admin.cursor() as cursor:
                            cursor.execute('SHUTDOWN')
                    finally:
                        admin.close()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)
        print('managed_mariadb_check: live assertions passed; instance stopped and removed')


def wait_ready(process, port):
    for _ in range(100):
        assert process.poll() is None, 'MariaDB exited before readiness'
        try:
            return pymysql.connect(host='127.0.0.1', port=port, user='root',
                                   autocommit=True, charset='utf8mb4')
        except pymysql.OperationalError:
            time.sleep(0.2)
    raise AssertionError('MariaDB readiness timeout')


def check_database(admin, port, binary, directory):
    passwords = {name: secrets.token_urlsafe(24) for name in GRANTS}
    with admin.cursor() as cursor:
        cursor.execute('SELECT VERSION()')
        print('Server:', cursor.fetchone()[0], flush=True)
        cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (secrets.token_urlsafe(24),))
        cursor.execute(f'CREATE DATABASE {DATABASE} CHARACTER SET utf8mb4')
        cursor.execute('CREATE DATABASE outside_business')
        cursor.execute('CREATE TABLE outside_business.secret (id INT)')
        cursor.execute('CREATE DATABASE smaXvalidation')
        cursor.execute('CREATE TABLE smaXvalidation.secret (id INT)')
        for name, grants in GRANTS.items():
            cursor.execute(f"CREATE USER '{name}'@'127.0.0.1' IDENTIFIED BY %s", (passwords[name],))
            cursor.execute(f"GRANT {grants} ON `{DATABASE_GRANT}`.* TO '{name}'@'127.0.0.1'")
            print(f'GRANT {name}: {grants} ON `{DATABASE_GRANT}`.*', flush=True)
    configs = {name: dict(type='mysql', host='127.0.0.1', port=port,
                         name=DATABASE, username=name, password=password)
               for name, password in passwords.items()}
    collector = DatabaseManager(configs['collector'])
    query = QueryDatabase(configs['query'])
    try:
        assert collector.connect()
        processor = DataStorageProcessor(collector)
        processor.group_data_points['Data_Test'] = ['BatchCode', 'Value']
        processor.group_partition_interval_years['Data_Test'] = 0
        processor.group_dict_fields['Data_Test'] = ['BatchCode']
        processor.group_indexes_configs['Data_Test'] = [{'columns': ['BatchCode']}]
        assert processor.initialize_tables_for_runtime()
        processor.group_data_points['Data_Test'].append('Added')
        processor.column_types_cache.clear()
        processor.ensured_tables.clear()
        assert processor.initialize_tables_for_runtime()
        assert 'Added' in collector.get_table_column_names('Data_Test')
        assert collector.execute_query('SHOW INDEX FROM Data_Test WHERE Column_name = "BatchCode"')
        records = [dict(group_name='Data_Test', collection_time=datetime(2026, 11, 4, 12, i),
                        data={'BatchCode': {'value': '批次甲'}, 'Value': {'value': str(i)},
                              'Added': {'value': "中文 ' \\ emoji 😀"}}) for i in range(3)]
        outcome = asyncio.run(processor._process_group_data('Data_Test', records))
        assert outcome['success'] == 3, outcome
        assert collector.execute_update('UPDATE Data_Test SET Value=:value WHERE id=1', {'value': '9'}) == 1
        assert collector.execute_query('SELECT value FROM dict_BatchCode') == [('批次甲',)]
        request = HistoryQueryRequest(table='Data_Test', batch_field='BatchCode', batch_code='批次甲',
                                      start_time=datetime(2026, 11, 4), end_time=datetime(2026, 11, 5),
                                      sort_dir='asc', page_size=2)
        before = query.query_history(request)
        assert before.total == 3 and len(before.rows) == 2
        assert before.rows[0]['Value'] == '9' and before.rows[0]['Added'] == "中文 ' \\ emoji 😀"
        second = query.query_history(request.model_copy(update={'page': 2}))
        assert len(second.rows) == 1 and second.rows[0]['Value'] == '2'
        print('PASS collector: create, add column, index, batch insert, update, dictionary; history: filter and pagination', flush=True)
        check_boundaries(configs)
        check_backup(configs['maintenance'], binary, directory)
        after = query.query_history(request)
        assert after.rows == before.rows and after.total == before.total
        assert query.query_history(request.model_copy(update={'page': 2})).rows == second.rows
        assert collector.execute_query('SELECT value FROM dict_BatchCode') == [('批次甲',)]
        print('PASS backup/restore: all history rows and dictionary match', flush=True)
    finally:
        collector.disconnect()
        query.engine.dispose()


def check_boundaries(configs):
    for name, config in configs.items():
        with pymysql.connect(host=config['host'], port=config['port'], user=name,
                             password=config['password'], database=DATABASE, autocommit=True) as connection:
            statements = ['SELECT * FROM outside_business.secret',
                          'INSERT INTO outside_business.secret VALUES (1)',
                          'DROP TABLE outside_business.secret', 'SELECT * FROM mysql.user',
                          'SELECT * FROM smaXvalidation.secret',
                          'CREATE DATABASE forbidden_database', "CREATE USER 'forbidden'@'localhost'",
                          'SET GLOBAL max_connections=200',
                          f"GRANT SELECT ON {DATABASE}.* TO 'query'@'127.0.0.1'"]
            if name == 'query':
                statements += ['INSERT INTO Data_Test (collection_time) VALUES (NOW())',
                               'CREATE TABLE forbidden_table (id INT)', 'ALTER TABLE Data_Test ADD forbidden INT',
                               'UPDATE Data_Test SET Value=0', 'DELETE FROM Data_Test', 'DROP TABLE Data_Test']
            if name == 'collector':
                statements += ['DELETE FROM Data_Test', 'DROP TABLE Data_Test']
            with connection.cursor() as cursor:
                for statement in statements:
                    try:
                        cursor.execute(statement)
                    except pymysql.MySQLError as error:
                        assert error.args[0] in (1044, 1142, 1227), (statement, error.args)
                    else:
                        raise AssertionError(f'{name} unexpectedly permitted: {statement}')
            print(f'PASS {name}: {len(statements)} denied permission probes', flush=True)


def check_backup(config, binary, directory):
    os.environ['SD_SMA_DATA_ROOT'] = str(directory)
    os.environ['SD_SMA_DB_ADMIN_CONFIG_DIR'] = str(directory / 'config')
    os.environ['SD_SMA_DB_ADMIN_BACKUP_DIR'] = str(directory / 'exports')
    os.environ['SD_SMA_SESSION_SECRET'] = secrets.token_urlsafe(32)
    spec = importlib.util.spec_from_file_location('mariadb_validation_admin', Path(__file__).parent / 'app/main.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.save_config({'mysql_tools': {'mysql': str(binary / 'mariadb.exe'),
                                       'mysqldump': str(binary / 'mariadb-dump.exe')}})
    conn = module.DbConnection(host=config['host'], port=config['port'], username=config['username'],
                               password=config['password'], database=DATABASE)
    backup = module.backup_mysql_job('validation', conn, DATABASE)
    source = Path(backup['path'])
    original = source.read_bytes()
    source.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    try:
        module.restore_verified_backup_job('validation', conn, DATABASE, backup['filename'])
    except RuntimeError as error:
        assert 'SHA-256 does not match' in str(error), str(error)
    else:
        raise AssertionError('Corrupt backup was accepted')
    finally:
        source.write_bytes(original)
    print('PASS restore rejects a backup with mismatched SHA-256', flush=True)
    with module.connect_mysql(conn, DATABASE) as connection:
        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE Data_Test')
            cursor.execute('DROP TABLE dict_BatchCode')
    module.restore_verified_backup_job('validation', conn, DATABASE, backup['filename'])
    for key, value in {'HOST': config['host'], 'PORT': str(config['port']), 'USERNAME': config['username'],
                       'PASSWORD': config['password'], 'DATABASE': DATABASE}.items():
        os.environ['SD_SMA_DB_' + key] = value
    os.environ['SD_SMA_MANAGED_DATABASE'] = '1'
    os.environ['SD_SMA_DB_TOOLS_DIR'] = str(binary)
    status = module.managed_storage_status()
    assert status['available'] and status['size_bytes'] > 0 and status['free_bytes'] > 0, status
    assert module.resolve_mysql_tool('mysql') == str(binary / 'mariadb.exe')
    os.environ['SD_SMA_DB_PASSWORD'] = 'wrong-test-password'
    assert module.managed_storage_status()['error'] == 'credentials'
    print('PASS managed storage status: real database capacity and actionable credential error', flush=True)


if __name__ == '__main__':
    main()
