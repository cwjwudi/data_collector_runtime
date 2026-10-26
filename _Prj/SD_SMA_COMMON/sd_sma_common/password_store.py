"""Plain configuration passwords; public responses must omit their values."""
from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Any, Mapping


def migrate_password_mapping(config_dir: Path, raw: Mapping[str, Any] | None) -> tuple[dict[str, Any], bool]:
    data = dict(raw or {})
    changed = "password_enc" in data
    if data.pop("password_enc", None) and not data.get("password"):
        logging.getLogger(__name__).warning("Legacy password discarded in %s; enter the password again", config_dir)
    return data, changed


def prepare_password_mapping(config_dir: Path, submitted: Mapping[str, Any] | None, existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data, _ = migrate_password_mapping(config_dir, submitted)
    old, _ = migrate_password_mapping(config_dir, existing)
    clear = bool(data.pop("clear_password", False))
    data.pop("password_configured", None)
    data["password"] = "" if clear else str(data.get("password") or old.get("password") or "")
    return data


def resolve_password(config_dir: Path, raw: Mapping[str, Any] | None, env_name: str, label: str = "") -> str:
    value = os.getenv(env_name)
    return value if value is not None else str((raw or {}).get("password") or "")


def password_is_configured(raw: Mapping[str, Any] | None, env_name: str) -> bool:
    return bool(resolve_password(Path(), raw, env_name))
