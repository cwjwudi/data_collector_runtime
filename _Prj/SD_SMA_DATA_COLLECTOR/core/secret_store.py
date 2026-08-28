"""Encrypt short-lived credentials before they are persisted in collector config files."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from cryptography.fernet import Fernet, InvalidToken


KEY_FILENAME = ".sd_sma_collector_fernet.key"
ENCRYPTED_FIELD = "password_enc"


def _load_or_create_key(config_dir: Path) -> bytes:
    key_path = config_dir / KEY_FILENAME
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        return key_path.read_bytes().strip()
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return key


def encrypt_secret(config_dir: Path, plain: str | None) -> str | None:
    value = str(plain or "")
    if not value:
        return None
    return Fernet(_load_or_create_key(config_dir)).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(config_dir: Path, token: str | None) -> str:
    if not token:
        return ""
    try:
        return Fernet(_load_or_create_key(config_dir)).decrypt(str(token).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError(
            f"数据库密码密文无法解密，请确认 {config_dir / KEY_FILENAME} 与配置文件匹配，"
            "或重新输入密码保存配置"
        ) from exc


def prepare_password_mapping(
    config_dir: Path,
    submitted: Mapping[str, Any] | None,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a storage-safe mapping; blank input preserves the existing ciphertext."""
    result = dict(submitted or {})
    old = dict(existing or {})
    plain = str(result.pop("password", "") or "")
    clear_requested = bool(result.pop("clear_password", False))

    if clear_requested:
        result.pop(ENCRYPTED_FIELD, None)
    elif plain:
        result[ENCRYPTED_FIELD] = encrypt_secret(config_dir, plain)
    elif old.get(ENCRYPTED_FIELD):
        result[ENCRYPTED_FIELD] = old[ENCRYPTED_FIELD]
    else:
        result.pop(ENCRYPTED_FIELD, None)
    return result


def migrate_password_mapping(config_dir: Path, raw: Mapping[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Convert a legacy plaintext password field to ciphertext."""
    source = dict(raw or {})
    had_password_field = "password" in source
    plain = str(source.pop("password", "") or "")
    if plain and not source.get(ENCRYPTED_FIELD):
        source[ENCRYPTED_FIELD] = encrypt_secret(config_dir, plain)
    return source, had_password_field


def resolve_password(config_dir: Path, raw: Mapping[str, Any] | None, env_name: str) -> str:
    env_value = os.getenv(env_name)
    if env_value:
        return env_value
    data = dict(raw or {})
    if data.get(ENCRYPTED_FIELD):
        return decrypt_secret(config_dir, str(data[ENCRYPTED_FIELD]))
    # Only in-memory validation payloads may still contain plaintext.
    return str(data.get("password", "") or "")


def password_is_configured(raw: Mapping[str, Any] | None, env_name: str) -> bool:
    data = dict(raw or {})
    return bool(os.getenv(env_name) or data.get(ENCRYPTED_FIELD) or data.get("password"))
