"""Installed MariaDB provisioning and machine-bound credentials; no service registration here."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import time

import pymysql


ACCOUNTS = {
    'collector_web': ('sma_collector', 'SELECT, INSERT, UPDATE, CREATE, ALTER, INDEX'),
    'query_web': ('sma_query', 'SELECT'),
    'db_admin': ('sma_maintenance', 'SELECT, INSERT, CREATE, DROP, ALTER, LOCK TABLES, EVENT'),
}


def protect(data: bytes, *, decrypt: bool = False) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.c_void_p)]

    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    result = Blob()
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    # LOCAL_MACHINE needs the directory ACL as well; encryption alone does not restrict local users.
    if not operation(ctypes.byref(source), None, None, None, None, 1 if decrypt else 5, ctypes.byref(result)):
        raise RuntimeError('Cannot access machine-protected database credentials')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class ManagedDatabase:
    def __init__(self, package_root: Path, data_root: Path, settings: dict):
        self.package_root = package_root.resolve()
        self.data_root = data_root.resolve()
        self.port = int(settings['port'])
        self.database = str(settings['database'])
        if not 1024 <= self.port <= 65535 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', self.database):
            raise ValueError('Invalid managed database port or name')
        self.binary = self.package_root / '_Database/bin'
        self.directory = self.data_root / 'database'
        self.secret_file = self.data_root / 'secrets/managed_database.dpapi'

    def initialize(self) -> None:
        self._check_files()
        assert_port_free(self.port)
        self._secure_secrets()
        if self.secret_file.exists():
            stored = self._load()
        else:
            if self.directory.exists():
                raise RuntimeError('Existing database has no credentials; restore its original machine credentials, do not reinitialize')
            stored = {
                'port': self.port, 'database': self.database, 'ready': False,
                'root_password': secrets.token_urlsafe(32),
                'passwords': {service: secrets.token_urlsafe(32) for service in ACCOUNTS},
            }
            self._save(stored)
        if self.directory.exists():
            if not (self.directory / 'my.ini').is_file():
                raise RuntimeError('Existing managed database is incomplete; refusing to replace it')
        else:
            if stored['ready']:
                raise RuntimeError('Managed database files are missing; restore a backup instead of reinitializing')
            self._provision(stored)
        if not stored['ready']:
            stored['ready'] = True
            self._save(stored)

    def credentials(self) -> dict:
        stored = self._load()
        if not stored['ready'] or not (self.directory / 'my.ini').is_file():
            raise RuntimeError('Managed database initialization is incomplete; rerun the installer')
        return {
            service: dict(engine='mysql', host='127.0.0.1', port=self.port,
                          database=self.database, username=username,
                          password=stored['passwords'][service], managed_storage=True)
            for service, (username, _) in ACCOUNTS.items()
        }

    def wait_ready(self, credential: dict, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                with pymysql.connect(host=credential['host'], port=credential['port'],
                                     user=credential['username'], password=credential['password'],
                                     database=credential['database'], connect_timeout=2,
                                     read_timeout=2, write_timeout=2) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT 1')
                        assert cursor.fetchone()[0] == 1
                return
            except pymysql.MySQLError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Managed storage unavailable; check SD_SMA_DB service and rerun the installer if credentials are missing') from None
                time.sleep(0.25)

    def _check_files(self):
        for name in ('mariadb-install-db.exe', 'mariadbd.exe', 'mariadb.exe', 'mariadb-dump.exe'):
            if not (self.binary / name).is_file():
                raise RuntimeError(f'Managed database binary missing: {name}')

    def _secure_secrets(self):
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise RuntimeError('Database initialization requires an elevated administrator process')
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)
        script = """
$ErrorActionPreference = 'Stop'
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
    $identity = New-Object Security.Principal.SecurityIdentifier($sid)
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
}
Set-Acl -LiteralPath '""" + str(self.secret_file.parent).replace("'", "''") + "' -AclObject $acl"
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-Command', script],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
            env={key: value for key, value in os.environ.items() if key.lower() != 'psmodulepath'},
        )
        if result.returncode:
            raise RuntimeError('Cannot restrict database credential directory to SYSTEM and Administrators')

    def _load(self):
        try:
            stored = json.loads(protect(self.secret_file.read_bytes(), decrypt=True))
            if stored['port'] != self.port or stored['database'] != self.database:
                raise ValueError('settings changed')
            if not isinstance(stored['ready'], bool) or not re.fullmatch(r'[A-Za-z0-9_-]{40,}', stored['root_password']):
                raise ValueError('invalid state')
            for service in ACCOUNTS:
                if not re.fullmatch(r'[A-Za-z0-9_-]{40,}', stored['passwords'][service]):
                    raise ValueError('invalid credential')
            return stored
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RuntimeError('Managed database credentials missing, damaged or settings changed; existing data was not modified') from error

    def _save(self, stored):
        temporary = self.secret_file.with_suffix('.tmp')
        temporary.write_bytes(protect(json.dumps(stored).encode('utf-8')))
        temporary.replace(self.secret_file)

    def _provision(self, stored):
        staging = self.data_root / 'database.initializing'
        # Only this unpublished staging directory is disposable after an interrupted first install.
        if staging.resolve().parent != self.data_root:
            raise RuntimeError('Initialization directory is outside the data root')
        if staging.exists():
            shutil.rmtree(staging)
        self._run([str(self.binary / 'mariadb-install-db.exe'), f'--datadir={staging}', '--skip-networking'])
        statements = [
            "DELETE FROM mysql.global_priv WHERE User='' OR (User='root' AND Host<>'localhost');",
            "DELETE FROM mysql.db WHERE User IN ('', 'PUBLIC');",
            'FLUSH PRIVILEGES;', 'DROP DATABASE IF EXISTS test;',
            f'CREATE DATABASE `{self.database}` CHARACTER SET utf8mb4;',
        ]
        grant_database = self.database.replace('_', r'\_')
        for service, (username, grants) in ACCOUNTS.items():
            password = stored['passwords'][service]
            statements.extend([
                f"CREATE USER '{username}'@'127.0.0.1' IDENTIFIED BY '{password}';",
                f"GRANT {grants} ON `{grant_database}`.* TO '{username}'@'127.0.0.1';",
            ])
        statements.append(f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{stored['root_password']}';")
        self._run([str(self.binary / 'mariadbd.exe'), '--no-defaults',
                   f'--basedir={self.binary.parent}', f'--datadir={staging}',
                   '--bootstrap', '--skip-networking', '--console'], '\n'.join(statements) + '\n')
        (staging / 'my.ini').write_text(
            '[mariadbd]\n'
            f'basedir="{self.binary.parent.as_posix()}"\n'
            f'datadir="{self.directory.as_posix()}"\n'
            f'port={self.port}\nbind-address=127.0.0.1\n'
            'character-set-server=utf8mb4\ncollation-server=utf8mb4_unicode_ci\n'
            'default-storage-engine=InnoDB\n', encoding='utf-8',
        )
        staging.replace(self.directory)

    @staticmethod
    def _run(command, sql=None):
        result = subprocess.run(command, input=sql.encode('utf-8') if sql else None,
                                capture_output=True, timeout=120,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            # Bootstrap stderr can contain SQL credentials; never forward it to installer logs.
            raise RuntimeError(f'Database initialization failed at {Path(command[0]).name} (exit {result.returncode}); rerun the installer')


def assert_port_free(port: int):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            raise RuntimeError(f'Managed database port {port} is occupied; stop the conflicting process before installation') from None
