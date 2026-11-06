"""Small internal database-switch contract shared by the three database services."""
import os
import json
from contextlib import closing
import sqlite3
from pathlib import Path

import pymysql
from fastapi import HTTPException, Request

from .session_bridge import machine_token_ok, MACHINE_TOKEN_HEADER


def require_database_controller(request: Request) -> None:
    if not machine_token_ok(request.headers.get(MACHINE_TOKEN_HEADER)):
        raise HTTPException(403, 'Launcher database controller required')


def database_target(config: dict) -> dict:
    return {key: config.get(key) for key in ('type', 'host', 'port', 'name', 'username')}


def database_connection_status(config: dict) -> dict:
    target = database_target(config)
    try:
        if config.get('type') == 'sqlite':
            path = config.get('name') or ':memory:'
            with closing(sqlite3.connect(':memory:' if path == ':memory:' else Path(path).resolve().as_uri() + '?mode=ro',
                                         uri=path != ':memory:')) as connection:
                connection.execute('SELECT 1')
            return dict(target=target, connected=True)
        with pymysql.connect(host=config['host'], port=int(config['port']), user=config['username'],
                             password=config['password'], database=config['name'],
                             connect_timeout=5, read_timeout=5, write_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT DATABASE(), VERSION()')
                database, version = cursor.fetchone()
        return dict(target=target, connected=database == config['name'], version=version)
    except Exception:
        return dict(target=target, connected=False, error='database_unavailable')


def injected_database() -> dict:
    return dict(type=os.getenv('SD_SMA_DB_ENGINE', 'mysql'), host=os.getenv('SD_SMA_DB_HOST', ''),
                port=int(os.getenv('SD_SMA_DB_PORT') or 3306), name=os.getenv('SD_SMA_DB_DATABASE', ''),
                username=os.getenv('SD_SMA_DB_USERNAME', ''), password=os.getenv('SD_SMA_DB_PASSWORD', ''))


def pending_outbox(path: Path) -> dict:
    """Read without opening PersistentQueueStore, which would recover leases or create files."""
    if not path.is_file():
        return {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        return dict(connection.execute("SELECT status, COUNT(*) FROM outbox_records "
                                       "WHERE status != 'completed' GROUP BY status").fetchall())


def collector_pending_outboxes(directory: Path, cwd: Path) -> dict:
    result = {}
    for filename in directory.glob('*.json'):
        payload = json.loads(filename.read_text(encoding='utf-8'))
        queue = payload.get('persistent_queue') or {}
        if queue.get('enabled'):
            path = Path(queue.get('path', 'runtime/queue/collector_outbox.db'))
            counts = pending_outbox(path if path.is_absolute() else cwd / path)
            if any(counts.values()):
                result[filename.name] = counts
    return result
