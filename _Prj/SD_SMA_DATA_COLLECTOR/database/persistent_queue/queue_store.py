"""Crash-safe SQLite outbox for collected PLC records."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class QueueCapacityError(RuntimeError):
    """Raised when accepting another record would exceed the configured limit."""


def _json_default(value: Any) -> Dict[str, str]:
    if isinstance(value, datetime):
        return {"__sd_sma_type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__sd_sma_type__": "date", "value": value.isoformat()}
    raise TypeError(f"Unsupported outbox value: {type(value).__name__}")


def _json_object_hook(value: Dict[str, Any]) -> Any:
    marker = value.get("__sd_sma_type__")
    if marker == "datetime":
        return datetime.fromisoformat(value["value"])
    if marker == "date":
        return date.fromisoformat(value["value"])
    return value


class PersistentQueueStore:
    """Synchronous SQLite store with transactional state transitions."""

    SCHEMA_VERSION = 2

    def __init__(self, path: str, *, synchronous: str = "FULL", busy_timeout_ms: int = 5000,
                 max_queue_rows: int = 1_000_000, completed_retention_days: int = 1,
                 recover_on_open: bool = True) -> None:
        self.path = str(Path(path).expanduser().resolve())
        self.max_queue_rows = max(1, int(max_queue_rows))
        self.completed_retention_days = max(0, int(completed_retention_days))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path, timeout=max(0.1, busy_timeout_ms / 1000), check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        sync = synchronous.upper()
        if sync not in {"OFF", "NORMAL", "FULL", "EXTRA"}:
            raise ValueError(f"Unsupported SQLite synchronous mode: {synchronous}")
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute(f"PRAGMA synchronous={sync}")
            self._connection.execute(f"PRAGMA busy_timeout={max(0, int(busy_timeout_ms))}")
            self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        self.recovered_on_open = self.recover_processing() if recover_on_open else 0

    def _migrate(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS outbox_records (
                    id TEXT PRIMARY KEY, group_name TEXT NOT NULL, payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT, lease_owner TEXT, lease_expires_at TEXT, last_error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
                )""")
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbox_ready "
                "ON outbox_records(status, next_attempt_at, created_at)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbox_completed_retention "
                "ON outbox_records(status, completed_at)"
            )
            self._connection.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat()

    def enqueue(self, payload: Dict[str, Any], event_id: Optional[str] = None) -> str:
        record_id = event_id or uuid.uuid4().hex
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_json_default)
        now = self._now()
        with self._lock, self._connection:
            active = self._connection.execute(
                "SELECT COUNT(*) FROM outbox_records "
                "WHERE status IN ('pending','processing','retry','dead_letter')"
            ).fetchone()[0]
            if active >= self.max_queue_rows:
                raise QueueCapacityError(f"Persistent queue capacity reached: {active}/{self.max_queue_rows}")
            self._connection.execute(
                "INSERT INTO outbox_records "
                "(id, group_name, payload_json, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (record_id, str(payload.get("group_name", "")), payload_json, now, now),
            )
        return record_id

    def load_ready(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        now = self._now()
        sql = ("SELECT id, payload_json FROM outbox_records WHERE status='pending' OR "
               "(status='retry' AND (next_attempt_at IS NULL OR next_attempt_at<=?)) "
               "ORDER BY created_at, rowid")
        params: List[Any] = [now]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"], object_hook=_json_object_hook)
            payload["_outbox_id"] = row["id"]
            result.append(payload)
        return result

    def attempts(self, record_ids: Iterable[str]) -> int:
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            row = self._connection.execute(
                f"SELECT MAX(attempts) FROM outbox_records WHERE id IN ({placeholders})", ids
            ).fetchone()
        return int(row[0] or 0)

    def mark_processing(self, record_ids: Iterable[str], lease_seconds: float = 60) -> None:
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            return
        now_dt = datetime.now().astimezone()
        expires = datetime.fromtimestamp(now_dt.timestamp() + max(1.0, float(lease_seconds)), tz=now_dt.tzinfo)
        self._update_many(ids, "status='processing', attempts=attempts+1, lease_owner=?, "
                          "lease_expires_at=?, updated_at=?", [uuid.uuid4().hex, expires.isoformat(), now_dt.isoformat()])

    def mark_completed(self, record_ids: Iterable[str]) -> None:
        self._set_terminal(record_ids, "completed", None)

    def mark_dead_letter(self, record_ids: Iterable[str], error: str) -> None:
        self._set_terminal(record_ids, "dead_letter", error)

    def mark_retry(self, record_ids: Iterable[str], error: str, delay_seconds: float) -> None:
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            return
        now_dt = datetime.now().astimezone()
        retry_at = datetime.fromtimestamp(now_dt.timestamp() + max(0.0, float(delay_seconds)), tz=now_dt.tzinfo)
        self._update_many(ids, "status='retry', next_attempt_at=?, lease_owner=NULL, "
                          "lease_expires_at=NULL, last_error=?, updated_at=?",
                          [retry_at.isoformat(), str(error)[:4000], now_dt.isoformat()])

    def _set_terminal(self, record_ids: Iterable[str], status: str, error: Optional[str]) -> None:
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            return
        now = self._now()
        self._update_many(ids, "status=?, lease_owner=NULL, lease_expires_at=NULL, "
                          "last_error=?, completed_at=?, updated_at=?",
                          [status, error[:4000] if error else None, now, now])

    def _update_many(self, ids: List[str], assignment: str, values: List[Any]) -> None:
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connection:
            self._connection.execute(
                f"UPDATE outbox_records SET {assignment} WHERE id IN ({placeholders})", [*values, *ids]
            )

    def recover_processing(self) -> int:
        now = self._now()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE outbox_records SET status='pending', lease_owner=NULL, "
                "lease_expires_at=NULL, updated_at=? WHERE status='processing'", (now,)
            )
        return max(0, cursor.rowcount)

    def counts(self) -> Dict[str, int]:
        result = {name: 0 for name in ("pending", "processing", "retry", "dead_letter", "completed")}
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, COUNT(*) AS count FROM outbox_records GROUP BY status"
            ).fetchall()
        for row in rows:
            result[row["status"]] = row["count"]
        return result

    def list_records(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        sql = ("SELECT id, group_name, status, attempts, next_attempt_at, last_error, "
               "created_at, updated_at, completed_at, payload_json FROM outbox_records")
        params: List[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY created_at, rowid LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        result = []
        for row in rows:
            record = dict(row)
            record["payload"] = json.loads(record.pop("payload_json"), object_hook=_json_object_hook)
            result.append(record)
        return result

    def replay_dead_letters(self, record_ids: Optional[Iterable[str]] = None) -> int:
        now = self._now()
        ids = list(dict.fromkeys(record_ids or []))
        where = "status='dead_letter'"
        params: List[Any] = [now]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            where += f" AND id IN ({placeholders})"
            params.extend(ids)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE outbox_records SET status='pending', attempts=0, next_attempt_at=NULL, "
                f"last_error=NULL, completed_at=NULL, updated_at=? WHERE {where}", params
            )
        return max(0, cursor.rowcount)

    def purge_completed(self) -> int:
        now_dt = datetime.now().astimezone()
        cutoff = datetime.fromtimestamp(now_dt.timestamp() - self.completed_retention_days * 86400,
                                        tz=now_dt.tzinfo).isoformat()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM outbox_records WHERE status='completed' AND completed_at<=?", (cutoff,)
            )
        return max(0, cursor.rowcount)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
