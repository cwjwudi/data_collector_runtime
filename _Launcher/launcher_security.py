from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Mapping


PBKDF2_ITERATIONS = 310_000
DPAPI_LOCAL_MACHINE = 0x4


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(value: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_protect(value: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is unavailable")
    source, source_buffer = _blob_from_bytes(value)
    target = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "SD SMA Launcher", None, None, None, DPAPI_LOCAL_MACHINE, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)
        del source_buffer


def _dpapi_unprotect(value: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is unavailable")
    source, source_buffer = _blob_from_bytes(value)
    target = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)
        del source_buffer


def protect_secret(value: str) -> str:
    raw = value.encode("utf-8")
    if os.name == "nt":
        return "dpapi:" + base64.urlsafe_b64encode(_dpapi_protect(raw)).decode("ascii")
    # Source-only fallback keeps tests and non-Windows development usable. Installed Windows builds never use it.
    key = hashlib.sha256((str(Path.home()) + "\0SD_SMA_LAUNCHER_DEV").encode("utf-8")).digest()
    masked = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(raw))
    return "dev:" + base64.urlsafe_b64encode(masked).decode("ascii")


def unprotect_secret(value: str) -> str:
    prefix, encoded = str(value).split(":", 1)
    raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
    if prefix == "dpapi":
        return _dpapi_unprotect(raw).decode("utf-8")
    if prefix == "dev" and os.name != "nt":
        key = hashlib.sha256((str(Path.home()) + "\0SD_SMA_LAUNCHER_DEV").encode("utf-8")).digest()
        return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(raw)).decode("utf-8")
    raise ValueError("Credential was not encrypted for this machine")


class LauncherSecurityStore:
    def __init__(self, path: Path, *, session_seconds: int = 15 * 60) -> None:
        self.path = path
        self.session_seconds = session_seconds
        self._lock = threading.RLock()
        self._sessions: dict[str, float] = {}
        self._failures = 0
        self._blocked_until = 0.0
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw.setdefault("version", 1)
                raw.setdefault("credentials", [])
                raw.setdefault("assignments", {})
                return raw
        except (OSError, ValueError, TypeError):
            pass
        return {"version": 1, "credentials": [], "assignments": {}}

    def _save(self) -> None:
        _atomic_json(self.path, self._data)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @property
    def pin_configured(self) -> bool:
        return bool(self._data.get("pin_hash") and self._data.get("pin_salt"))

    @property
    def pin_mode(self) -> str:
        """Return the PIN policy while accepting legacy security files."""
        if self.pin_configured:
            return "enabled"
        return "disabled" if self._data.get("pin_mode") == "disabled" else "undecided"

    @property
    def pin_enabled(self) -> bool:
        return self.pin_mode == "enabled"

    def setup_pin(self, pin: str) -> str:
        with self._lock:
            if self.pin_enabled:
                raise ValueError("管理员 PIN 已配置")
            self._validate_pin(pin)
            salt = secrets.token_bytes(16)
            self._data["pin_mode"] = "enabled"
            self._data["pin_salt"] = base64.b64encode(salt).decode("ascii")
            self._data["pin_hash"] = base64.b64encode(self._pin_hash(pin, salt)).decode("ascii")
            self._save()
            return self._new_session()

    def disable_pin(self) -> None:
        with self._lock:
            self._data["pin_mode"] = "disabled"
            self._data.pop("pin_hash", None)
            self._data.pop("pin_salt", None)
            self._sessions.clear()
            self._failures = 0
            self._blocked_until = 0.0
            self._save()

    def unlock(self, pin: str) -> str:
        with self._lock:
            now = time.monotonic()
            if now < self._blocked_until:
                raise PermissionError(f"尝试次数过多，请在 {int(self._blocked_until - now) + 1} 秒后重试")
            if not self.pin_enabled:
                raise ValueError("管理员 PIN 未启用")
            salt = base64.b64decode(str(self._data["pin_salt"]))
            actual = self._pin_hash(pin, salt)
            expected = base64.b64decode(str(self._data["pin_hash"]))
            if not hmac.compare_digest(actual, expected):
                self._failures += 1
                if self._failures >= 5:
                    self._blocked_until = now + min(300, 30 * (2 ** (self._failures - 5)))
                raise PermissionError("PIN 不正确")
            self._failures = 0
            self._blocked_until = 0.0
            return self._new_session()

    def _validate_pin(self, pin: str) -> None:
        if not pin.isdigit() or not 6 <= len(pin) <= 12:
            raise ValueError("PIN 必须是 6–12 位数字")

    @staticmethod
    def _pin_hash(pin: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PBKDF2_ITERATIONS)

    def _new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = time.monotonic() + self.session_seconds
        return token

    def verify_session(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            expiry = self._sessions.get(token, 0.0)
            if expiry <= time.monotonic():
                self._sessions.pop(token, None)
                return False
            self._sessions[token] = time.monotonic() + self.session_seconds
            return True

    def lock(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)

    def public_credentials(self) -> dict[str, Any]:
        with self._lock:
            rows = []
            for raw in self._data.get("credentials", []):
                row = {key: value for key, value in raw.items() if key != "password_protected"}
                row["password_configured"] = bool(raw.get("password_protected"))
                rows.append(row)
            return {"credentials": rows, "assignments": dict(self._data.get("assignments", {}))}

    def upsert_credential(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            credential_id = str(payload.get("id") or secrets.token_hex(8)).strip()
            if not credential_id or not all(ch.isalnum() or ch in "-_" for ch in credential_id):
                raise ValueError("凭据 ID 无效")
            rows = list(self._data.get("credentials", []))
            existing = next((dict(row) for row in rows if str(row.get("id")) == credential_id), {})
            result = {
                **existing,
                "id": credential_id,
                "name": str(payload.get("name") or existing.get("name") or credential_id).strip(),
                "engine": str(payload.get("engine") or existing.get("engine") or "mysql").lower(),
                "host": str(payload.get("host") or existing.get("host") or "127.0.0.1").strip(),
                "port": int(payload.get("port") or existing.get("port") or 3306),
                "username": str(payload.get("username") or existing.get("username") or "root").strip(),
                "database": str(payload.get("database") or existing.get("database") or "").strip(),
            }
            password = str(payload.get("password") or "")
            if password:
                result["password_protected"] = protect_secret(password)
            elif not result.get("password_protected"):
                raise ValueError("新凭据必须输入密码")
            rows = [row for row in rows if str(row.get("id")) != credential_id]
            rows.append(result)
            self._data["credentials"] = rows
            self._save()
            return next(row for row in self.public_credentials()["credentials"] if row["id"] == credential_id)

    def delete_credential(self, credential_id: str) -> None:
        with self._lock:
            self._data["credentials"] = [
                row for row in self._data.get("credentials", []) if str(row.get("id")) != credential_id
            ]
            assignments = dict(self._data.get("assignments", {}))
            self._data["assignments"] = {
                service: value for service, value in assignments.items() if str(value) != credential_id
            }
            self._save()

    def assign(self, service: str, credential_id: str | None) -> None:
        with self._lock:
            assignments = dict(self._data.get("assignments", {}))
            if credential_id:
                if not any(str(row.get("id")) == credential_id for row in self._data.get("credentials", [])):
                    raise KeyError("凭据档案不存在")
                assignments[service] = credential_id
            else:
                assignments.pop(service, None)
            self._data["assignments"] = assignments
            self._save()

    def credential_for_service(self, service: str) -> dict[str, Any] | None:
        with self._lock:
            credential_id = str(self._data.get("assignments", {}).get(service, ""))
            row = next(
                (dict(item) for item in self._data.get("credentials", []) if str(item.get("id")) == credential_id),
                None,
            )
        if row is None:
            return None
        protected = str(row.pop("password_protected", ""))
        row["password"] = unprotect_secret(protected) if protected else ""
        return row

    def credential_by_id(self, credential_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = next(
                (dict(item) for item in self._data.get("credentials", []) if str(item.get("id")) == credential_id),
                None,
            )
        if row is None:
            return None
        protected = str(row.pop("password_protected", ""))
        row["password"] = unprotect_secret(protected) if protected else ""
        return row

    def services_for_credential(self, credential_id: str) -> list[str]:
        with self._lock:
            return [
                str(service)
                for service, assigned in self._data.get("assignments", {}).items()
                if str(assigned) == credential_id
            ]
