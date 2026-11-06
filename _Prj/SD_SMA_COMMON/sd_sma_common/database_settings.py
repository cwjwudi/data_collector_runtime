"""Database credentials belong to Launcher, never to service configuration files."""

import os
from typing import Any


def without_database_credentials(database: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in database.items() if key not in {
        "username", "password", "password_enc", "password_configured", "clear_password", "launcher_managed",
    }}


def database_credentials_from_env() -> dict[str, str]:
    return {
        "username": os.environ.get("SD_SMA_DB_USERNAME", ""),
        "password": os.environ.get("SD_SMA_DB_PASSWORD", ""),
    }


def effective_database_settings(database: dict[str, Any]) -> dict[str, Any]:
    result = without_database_credentials(database)
    for field, key in (('type', 'ENGINE'), ('host', 'HOST'), ('port', 'PORT'), ('name', 'DATABASE')):
        value = os.getenv('SD_SMA_DB_' + key)
        if value:
            result[field] = int(value) if field == 'port' else value
    result.update(database_credentials_from_env())
    return result
