from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from .secret_store import (
    migrate_password_mapping,
    password_is_configured,
    prepare_password_mapping,
    resolve_password,
)

_OPCUA_DEFAULT_HOST = "127.0.0.1"
_OPCUA_DEFAULT_PORT = 4840


def normalize_opcua_endpoint_url(endpoint_url: str) -> str:
    """Normalize UI/profile endpoint strings to opc.tcp://host:port/."""
    raw = str(endpoint_url or "").strip()
    if not raw:
        return ""

    text = re.sub(r"^opc\s+tcp:", "opc.tcp:", raw, flags=re.IGNORECASE)
    if not text.lower().startswith("opc.tcp://"):
        host_port = re.match(r"^([^/:]+):(\d+)(?:/.*)?$", text)
        if host_port:
            text = f"opc.tcp://{host_port.group(1)}:{host_port.group(2)}/"
        elif re.match(r"^[A-Za-z0-9_.-]+$", text):
            text = f"opc.tcp://{text}:{_OPCUA_DEFAULT_PORT}/"
        else:
            return ""
    elif not text.endswith("/"):
        text = f"{text}/"

    match = re.match(r"^opc\.tcp://([^/:]+):(\d+)/", text, flags=re.IGNORECASE)
    if not match or not match.group(1) or int(match.group(2)) <= 0:
        return ""
    return text


class UnifiedConfigStore:
    def __init__(
        self,
        config_dir: Path,
        *,
        legacy_app_settings_path: Path | None = None,
        legacy_query_view_config_path: Path | None = None,
        legacy_plugin_config_path: Path | None = None,
    ):
        self.config_dir = config_dir
        self.active_profile_path = config_dir / ".active_query_config"
        self.legacy_app_settings_path = legacy_app_settings_path
        self.legacy_query_view_config_path = legacy_query_view_config_path
        self.legacy_plugin_config_path = legacy_plugin_config_path
        self.legacy_filenames = {
            "app_settings.json",
            "query_view_config.json",
            "plugins_config.json",
        }
        self._cache_lock = threading.RLock()
        self._active_config_cache: dict[str, Any] | None = None
        self._active_config_signature: tuple[Any, ...] | None = None
        self._cached_active_name: str | None = None
        self._cached_pointer_signature: tuple[int, int] | None = None
        self.ensure_default_profile()

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _invalidate_active_cache(self) -> None:
        with self._cache_lock:
            self._active_config_cache = None
            self._active_config_signature = None
            self._cached_active_name = None
            self._cached_pointer_signature = None

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _clone_json(data: Any) -> Any:
        return json.loads(json.dumps(data, ensure_ascii=False))

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _default_app_settings(cls) -> dict[str, Any]:
        return {
            "database": {
                "type": "mysql",
                "name": "",
                "host": "127.0.0.1",
                "port": 3306,
                "username": "",
            },
            "query_limits": {
                "requests_per_minute": 120,
                "default_window_hours": 24,
                "max_window_hours": 168,
            },
        }

    @classmethod
    def _default_opcua_settings(cls) -> dict[str, Any]:
        return {
            "endpoint_url": "",
            "username": "",
            "heartbeat_node": "",
            "poll_interval_ms": 500,
        }

    @staticmethod
    def _clamp_poll_interval_ms(raw: Any) -> int:
        try:
            value = int(raw if raw is not None else 500)
        except (TypeError, ValueError):
            value = 500
        return max(50, min(value, 5000))

    @classmethod
    def _normalize_opcua_settings(cls, raw: Any) -> dict[str, Any]:
        data = raw if isinstance(raw, dict) else {}
        normalized = {
            "endpoint_url": normalize_opcua_endpoint_url(str(data.get("endpoint_url", "") or "")),
            "username": str(data.get("username", "") or ""),
            "heartbeat_node": str(data.get("heartbeat_node", "") or "").strip(),
            "poll_interval_ms": cls._clamp_poll_interval_ms(data.get("poll_interval_ms", 500)),
        }
        if data.get("password_enc"):
            normalized["password_enc"] = str(data["password_enc"])
        return normalized

    @classmethod
    def _default_query_view_config(cls) -> dict[str, Any]:
        return {
            "default_page_size": 50,
            "max_page_size": 500,
            "views": {
                "table": {
                    "title": "Table Query",
                    "description": "Generic table history query",
                    "time_field": "collection_time",
                    "batch_source": {},
                    "columns": [],
                    "sort_by": "collection_time",
                    "sort_dir": "desc",
                    "page_size": 50,
                    "default_filters": [],
                    "per_table": {},
                    "per_group": {},
                },
                "alarm": {
                    "title": "Alarm List",
                    "description": "Alarm history query",
                    "time_field": "ts",
                    "batch_source": {},
                    "columns": ["ts", "Status", "severity", "code", "msg", "Batch"],
                    "sort_by": "ts",
                    "sort_dir": "desc",
                    "page_size": 100,
                    "default_filters": [{"field": "Status", "op": "ne", "value": "0"}],
                    "per_table": {},
                    "per_group": {},
                },
                "audit": {
                    "title": "Audit List",
                    "description": "Audit history query",
                    "time_field": "ts",
                    "batch_source": {},
                    "columns": ["ts", "operator", "action", "result", "msg"],
                    "sort_by": "ts",
                    "sort_dir": "desc",
                    "page_size": 100,
                    "default_filters": [{"field": "action", "op": "like", "value": "%"}],
                    "per_table": {},
                    "per_group": {},
                },
            },
            "group_baselines": {},
        }

    @classmethod
    def _is_usable_query_view_config(cls, data: Any) -> bool:
        return isinstance(data, dict) and isinstance(data.get("views"), dict) and bool(data.get("views"))

    @classmethod
    def _default_profile_template(
        cls,
        profile_name: str,
        *,
        app_settings: dict[str, Any] | None = None,
        opcua: dict[str, Any] | None = None,
        query_view: dict[str, Any] | None = None,
        plugins: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "version": 1,
            "name": profile_name,
            "app_settings": cls._clone_json(app_settings if isinstance(app_settings, dict) else cls._default_app_settings()),
            "opcua": cls._clone_json(opcua if isinstance(opcua, dict) else cls._default_opcua_settings()),
            "query_view": cls._clone_json(
                query_view if cls._is_usable_query_view_config(query_view) else cls._default_query_view_config()
            ),
            "plugins": cls._clone_json(plugins if isinstance(plugins, dict) else {"modules": {}}),
        }

    @classmethod
    def _blank_query_view_from(cls, source: Any) -> dict[str, Any]:
        if not cls._is_usable_query_view_config(source):
            return cls._default_query_view_config()

        source_dict = source if isinstance(source, dict) else {}
        source_views = source_dict.get("views", {})
        views: dict[str, Any] = {}
        for view_name, raw_view in source_views.items():
            if not isinstance(raw_view, dict):
                continue
            views[str(view_name)] = {
                "title": str(raw_view.get("title", view_name)),
                "description": str(raw_view.get("description", "")),
                "time_field": str(raw_view.get("time_field", "collection_time")),
                "batch_field": str(raw_view.get("batch_field", "")),
                "batch_source": cls._clone_json(raw_view.get("batch_source", {})) if isinstance(raw_view.get("batch_source"), dict) else {},
                "columns": cls._clone_json(raw_view.get("columns", [])) if isinstance(raw_view.get("columns"), list) else [],
                "sort_by": str(raw_view.get("sort_by", "collection_time")),
                "sort_dir": "asc" if str(raw_view.get("sort_dir", "desc")).lower() == "asc" else "desc",
                "page_size": max(1, cls._safe_int(raw_view.get("page_size", 50), 50)),
                "default_filters": (
                    cls._clone_json(raw_view.get("default_filters", []))
                    if isinstance(raw_view.get("default_filters"), list)
                    else []
                ),
                "per_table": {},
                "per_group": {},
            }

        if not views:
            return cls._default_query_view_config()

        default_page_size = max(1, cls._safe_int(source_dict.get("default_page_size", 50), 50))
        max_page_size = max(default_page_size, cls._safe_int(source_dict.get("max_page_size", 500), 500))
        return {
            "default_page_size": default_page_size,
            "max_page_size": max_page_size,
            "views": views,
            "group_baselines": {},
        }

    def _normalize_profile_data(self, data: Any, profile_name: str) -> tuple[dict[str, Any], bool]:
        normalized = dict(data) if isinstance(data, dict) else {}
        changed = not isinstance(data, dict)

        if normalized.get("version") != 1:
            normalized["version"] = 1
            changed = True
        if str(normalized.get("name") or "") != profile_name:
            normalized["name"] = profile_name
            changed = True

        if not isinstance(normalized.get("app_settings"), dict):
            normalized["app_settings"] = self._default_app_settings()
            changed = True
        app_settings = dict(normalized.get("app_settings", {}))
        database = app_settings.get("database")
        migrated_database, database_changed = migrate_password_mapping(
            self.config_dir,
            database if isinstance(database, dict) else {},
        )
        if not isinstance(database, dict):
            migrated_database = dict(self._default_app_settings()["database"])
            database_changed = True
        if database_changed or migrated_database != database:
            app_settings["database"] = migrated_database
            normalized["app_settings"] = app_settings
            changed = True
        if not isinstance(normalized.get("opcua"), dict):
            normalized["opcua"] = self._default_opcua_settings()
            changed = True
        else:
            migrated_opcua, opcua_password_changed = migrate_password_mapping(
                self.config_dir,
                normalized.get("opcua"),
            )
            opcua_normalized = self._normalize_opcua_settings(migrated_opcua)
            if opcua_normalized != normalized.get("opcua"):
                normalized["opcua"] = opcua_normalized
                changed = True
            changed = changed or opcua_password_changed
        if not self._is_usable_query_view_config(normalized.get("query_view")):
            normalized["query_view"] = self._default_query_view_config()
            changed = True

        plugins = normalized.get("plugins")
        if not isinstance(plugins, dict):
            normalized["plugins"] = {"modules": {}}
            changed = True
        elif not isinstance(plugins.get("modules"), dict):
            normalized["plugins"] = {**plugins, "modules": {}}
            changed = True

        return normalized, changed

    @staticmethod
    def _safe_profile_name(filename: str) -> str:
        name = Path(str(filename or "")).name.strip()
        if not name:
            raise ValueError("config filename is required")
        if not name.lower().endswith(".json"):
            name = f"{name}.json"
        if name.startswith(".") or "/" in name or "\\" in name:
            raise ValueError("invalid config filename")
        return name

    def _profile_path(self, filename: str) -> Path:
        name = self._safe_profile_name(filename)
        target = (self.config_dir / name).resolve()
        if target.parent != self.config_dir.resolve():
            raise ValueError("invalid config filename")
        return target

    def _build_legacy_profile(self) -> dict[str, Any]:
        return self._default_profile_template(
            "default",
            app_settings=self._load_json(self.legacy_app_settings_path) if self.legacy_app_settings_path else None,
            query_view=self._load_json(self.legacy_query_view_config_path) if self.legacy_query_view_config_path else None,
            plugins=self._load_json(self.legacy_plugin_config_path) if self.legacy_plugin_config_path else {"modules": {}},
        )

    def _profile_files(self) -> list[Path]:
        return sorted(
            path
            for path in self.config_dir.glob("*.json")
            if path.name not in self.legacy_filenames
        )

    def ensure_default_profile(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        profiles = self._profile_files()
        if not profiles:
            data, _ = self._normalize_profile_data(self._build_legacy_profile(), "default")
            self._write_json(self.config_dir / "default.json", data)
            self.active_profile_path.write_text("default.json", encoding="utf-8")
            return
        if not self.active_profile_path.exists():
            active_name = "default.json" if (self.config_dir / "default.json").exists() else profiles[0].name
            self.active_profile_path.write_text(active_name, encoding="utf-8")

    def list_profiles(self) -> list[dict[str, Any]]:
        self.ensure_default_profile()
        active = self.get_active_profile_name()
        profiles: list[dict[str, Any]] = []
        for path in self._profile_files():
            data = self._load_json(path)
            profiles.append(
                {
                    "filename": path.name,
                    "name": str(data.get("name", path.stem)),
                    "active": path.name == active,
                }
            )
        return profiles

    def get_active_profile_name(self) -> str:
        self.ensure_default_profile()
        raw = self.active_profile_path.read_text(encoding="utf-8").strip()
        name = self._safe_profile_name(raw or "default.json")
        if not self._profile_path(name).exists():
            profiles = self._profile_files()
            name = "default.json" if (self.config_dir / "default.json").exists() else profiles[0].name
            self.active_profile_path.write_text(name, encoding="utf-8")
        return name

    def set_active_profile(self, filename: str) -> dict[str, Any]:
        name = self._safe_profile_name(filename)
        path = self._profile_path(name)
        if not path.exists():
            raise FileNotFoundError(f"config profile not found: {name}")
        self.active_profile_path.write_text(name, encoding="utf-8")
        self._invalidate_active_cache()
        return self.get_active_config()

    def create_profile(self, filename: str) -> dict[str, Any]:
        name = self._safe_profile_name(filename)
        path = self._profile_path(name)
        if path.exists():
            raise FileExistsError(f"config profile already exists: {name}")

        active = self.get_active_config()
        data = self._default_profile_template(
            Path(name).stem,
            app_settings=active.get("app_settings") if isinstance(active.get("app_settings"), dict) else None,
            opcua=active.get("opcua") if isinstance(active.get("opcua"), dict) else None,
            query_view=self._blank_query_view_from(active.get("query_view")),
            plugins={"modules": {}},
        )
        self._write_json(path, data)
        self.active_profile_path.write_text(name, encoding="utf-8")
        self._invalidate_active_cache()

        return {
            "status": "created",
            "filename": name,
            "active": self.get_active_profile_name(),
            "profiles": self.list_profiles(),
        }

    def delete_profile(self, filename: str) -> dict[str, Any]:
        name = self._safe_profile_name(filename)
        path = self._profile_path(name)
        profiles = self._profile_files()
        if not path.exists() or path not in profiles:
            raise FileNotFoundError(f"config profile not found: {name}")
        if len(profiles) <= 1:
            path.unlink()
            if self.active_profile_path.exists():
                self.active_profile_path.unlink()
            self._invalidate_active_cache()
            self.ensure_default_profile()
            return {
                "status": "reset",
                "filename": name,
                "active": self.get_active_profile_name(),
                "profiles": self.list_profiles(),
            }

        was_active = name == self.get_active_profile_name()
        path.unlink()

        if was_active:
            remaining = self._profile_files()
            self.active_profile_path.write_text(remaining[0].name, encoding="utf-8")
        self._invalidate_active_cache()

        return {
            "status": "deleted",
            "filename": name,
            "active": self.get_active_profile_name(),
            "profiles": self.list_profiles(),
        }

    def get_active_config_with_revision(self) -> tuple[dict[str, Any], tuple[Any, ...]]:
        """Return an isolated active config and a revision that tracks external file changes."""
        with self._cache_lock:
            pointer_signature = self._file_signature(self.active_profile_path)
            if (
                self._cached_active_name is not None
                and pointer_signature == self._cached_pointer_signature
            ):
                active_name = self._cached_active_name
            else:
                active_name = self.get_active_profile_name()
                pointer_signature = self._file_signature(self.active_profile_path)
                self._cached_active_name = active_name
                self._cached_pointer_signature = pointer_signature

            path = self._profile_path(active_name)
            profile_signature = self._file_signature(path)
            signature: tuple[Any, ...] = (
                active_name,
                pointer_signature,
                profile_signature,
            )
            if self._active_config_cache is not None and signature == self._active_config_signature:
                return self._clone_json(self._active_config_cache), signature

            data = self._load_json(path)
            data, changed = self._normalize_profile_data(data, path.stem)
            if changed:
                self._write_json(path, data)
                profile_signature = self._file_signature(path)
                signature = (active_name, pointer_signature, profile_signature)

            self._active_config_cache = data
            self._active_config_signature = signature
            return self._clone_json(data), signature

    def get_active_config(self) -> dict[str, Any]:
        data, _revision = self.get_active_config_with_revision()
        return data

    def save_active_config(self, data: dict[str, Any]) -> None:
        path = self._profile_path(self.get_active_profile_name())
        normalized, _ = self._normalize_profile_data(data, path.stem)
        self._write_json(path, normalized)
        self._invalidate_active_cache()

    def get_app_settings(self) -> dict[str, Any]:
        settings = dict(self.get_active_config().get("app_settings", {}))
        database = dict(settings.get("database", {}))
        database["host"] = os.environ.get("SD_SMA_DB_HOST") or database.get("host", "127.0.0.1")
        database["port"] = int(os.environ.get("SD_SMA_DB_PORT") or database.get("port", 3306))
        database["username"] = os.environ.get("SD_SMA_DB_USERNAME") or database.get("username", "")
        database["name"] = os.environ.get("SD_SMA_DB_DATABASE") or database.get("name", "")
        database["password"] = resolve_password(
            self.config_dir,
            database,
            env_name="SD_SMA_DB_PASSWORD",
            label="数据库",
        )
        database.pop("password_enc", None)
        settings["database"] = database
        return settings

    def get_public_app_settings(self) -> dict[str, Any]:
        config = self.get_active_config()
        settings = dict(config.get("app_settings", {}))
        database = dict(settings.get("database", {}))
        database["host"] = os.environ.get("SD_SMA_DB_HOST") or database.get("host", "127.0.0.1")
        database["port"] = int(os.environ.get("SD_SMA_DB_PORT") or database.get("port", 3306))
        database["username"] = os.environ.get("SD_SMA_DB_USERNAME") or database.get("username", "")
        database["name"] = os.environ.get("SD_SMA_DB_DATABASE") or database.get("name", "")
        database.pop("password", None)
        database.pop("password_enc", None)
        database["password_configured"] = password_is_configured(
            config.get("app_settings", {}).get("database", {}),
            "SD_SMA_DB_PASSWORD",
        )
        settings["database"] = database
        return settings

    def save_app_settings(self, data: dict[str, Any]) -> None:
        config = self.get_active_config()
        existing_settings = config.get("app_settings", {})
        existing_database = (
            existing_settings.get("database", {})
            if isinstance(existing_settings, dict)
            else {}
        )
        settings = dict(data)
        settings["database"] = prepare_password_mapping(
            self.config_dir,
            settings.get("database", {}),
            existing_database if isinstance(existing_database, dict) else {},
        )
        config["app_settings"] = settings
        self.save_active_config(config)

    def get_query_view_config(self) -> dict[str, Any]:
        return dict(self.get_active_config().get("query_view", {}))

    def save_query_view_config(self, data: dict[str, Any]) -> None:
        config = self.get_active_config()
        config["query_view"] = data
        self.save_active_config(config)

    def get_plugins_config(self) -> dict[str, Any]:
        data = self.get_active_config().get("plugins", {"modules": {}})
        return data if isinstance(data, dict) else {"modules": {}}

    def save_plugins_config(self, data: dict[str, Any]) -> None:
        config = self.get_active_config()
        config["plugins"] = data
        self.save_active_config(config)

    def get_opcua_settings(self) -> dict[str, Any]:
        stored = self._normalize_opcua_settings(self.get_active_config().get("opcua"))
        runtime = dict(stored)
        runtime["password"] = resolve_password(
            self.config_dir,
            stored,
            env_name="SD_SMA_OPCUA_PASSWORD",
            label="OPC UA",
        )
        runtime.pop("password_enc", None)
        return runtime

    def get_public_opcua_settings(self) -> dict[str, Any]:
        config = self.get_active_config()
        stored = self._normalize_opcua_settings(config.get("opcua"))
        public = dict(stored)
        public.pop("password_enc", None)
        public["password_configured"] = password_is_configured(
            config.get("opcua"),
            "SD_SMA_OPCUA_PASSWORD",
        )
        return public

    def save_opcua_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        config = self.get_active_config()
        existing = config.get("opcua", {})
        stored = prepare_password_mapping(
            self.config_dir,
            data,
            existing if isinstance(existing, dict) else {},
        )
        normalized = self._normalize_opcua_settings(stored)
        config["opcua"] = normalized
        self.save_active_config(config)
        return self.get_public_opcua_settings()


class ConfigManager:
    def __init__(self, config_store: UnifiedConfigStore):
        self.config_store = config_store

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_query_view_config(self) -> dict[str, Any]:
        return self.config_store.get_query_view_config()

    def _write_query_view_config(self, data: dict[str, Any]) -> None:
        self.config_store.save_query_view_config(data)

    @staticmethod
    def _validate_query_view_config(data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise ValueError("Query view config payload must be a JSON object")

        default_page_size = int(data.get("default_page_size", 50))
        max_page_size = int(data.get("max_page_size", 500))
        if default_page_size < 1:
            raise ValueError("default_page_size must be greater than 0")
        if max_page_size < 1:
            raise ValueError("max_page_size must be greater than 0")
        if default_page_size > max_page_size:
            raise ValueError("default_page_size must be less than or equal to max_page_size")

        views = data.get("views")
        if not isinstance(views, dict) or not views:
            raise ValueError("views must be a non-empty object")

        for view_name, view in views.items():
            if not isinstance(view, dict):
                raise ValueError(f"view '{view_name}' must be an object")

            columns = view.get("columns", [])
            if not isinstance(columns, list):
                raise ValueError(f"view '{view_name}'.columns must be a list")

            filters = view.get("default_filters", [])
            if not isinstance(filters, list):
                raise ValueError(f"view '{view_name}'.default_filters must be a list")

            batch_source = view.get("batch_source", {})
            if batch_source is not None and not isinstance(batch_source, dict):
                raise ValueError(f"view '{view_name}'.batch_source must be an object")
            if isinstance(batch_source, dict):
                source_table = str(batch_source.get("table", "") or "").strip()
                source_field = str(batch_source.get("field", "") or "").strip()
                if bool(source_table) != bool(source_field):
                    raise ValueError(f"view '{view_name}'.batch_source table/field must both be set or both be empty")

            sort_dir = str(view.get("sort_dir", "desc")).lower()
            if sort_dir not in {"asc", "desc"}:
                raise ValueError(f"view '{view_name}'.sort_dir must be asc or desc")

            if "page_size" in view:
                page_size = int(view["page_size"])
                if page_size < 1 or page_size > max_page_size:
                    raise ValueError(f"view '{view_name}'.page_size out of range")

            per_table = view.get("per_table", {})
            if per_table is not None and not isinstance(per_table, dict):
                raise ValueError(f"view '{view_name}'.per_table must be an object")
            if isinstance(per_table, dict):
                for table_name, table_cfg in per_table.items():
                    if not isinstance(table_cfg, dict):
                        raise ValueError(f"view '{view_name}'.per_table.{table_name} must be an object")
                    if "columns" in table_cfg and not isinstance(table_cfg["columns"], list):
                        raise ValueError(f"view '{view_name}'.per_table.{table_name}.columns must be a list")
                    if "sort_dir" in table_cfg:
                        sdir = str(table_cfg["sort_dir"]).lower()
                        if sdir not in {"asc", "desc"}:
                            raise ValueError(f"view '{view_name}'.per_table.{table_name}.sort_dir must be asc or desc")
                    if "page_size" in table_cfg:
                        page_size = int(table_cfg["page_size"])
                        if page_size < 1 or page_size > max_page_size:
                            raise ValueError(f"view '{view_name}'.per_table.{table_name}.page_size out of range")
            per_group = view.get("per_group", {})
            if per_group is not None and not isinstance(per_group, dict):
                raise ValueError(f"view '{view_name}'.per_group must be an object")
            if isinstance(per_group, dict):
                for group_name, group_cfg in per_group.items():
                    if not isinstance(group_cfg, dict):
                        raise ValueError(f"view '{view_name}'.per_group.{group_name} must be an object")
                    if "columns" in group_cfg and not isinstance(group_cfg["columns"], list):
                        raise ValueError(f"view '{view_name}'.per_group.{group_name}.columns must be a list")
                    if "batch_source" in group_cfg:
                        group_source = group_cfg["batch_source"]
                        if not isinstance(group_source, dict):
                            raise ValueError(f"view '{view_name}'.per_group.{group_name}.batch_source must be an object")
                        group_source_table = str(group_source.get("table", "") or "").strip()
                        group_source_field = str(group_source.get("field", "") or "").strip()
                        if bool(group_source_table) != bool(group_source_field):
                            raise ValueError(
                                f"view '{view_name}'.per_group.{group_name}.batch_source table/field "
                                "must both be set or both be empty"
                            )
                    if "sort_dir" in group_cfg:
                        sdir = str(group_cfg["sort_dir"]).lower()
                        if sdir not in {"asc", "desc"}:
                            raise ValueError(f"view '{view_name}'.per_group.{group_name}.sort_dir must be asc or desc")
                    if "page_size" in group_cfg:
                        page_size = int(group_cfg["page_size"])
                        if page_size < 1 or page_size > max_page_size:
                            raise ValueError(f"view '{view_name}'.per_group.{group_name}.page_size out of range")

        group_baselines = data.get("group_baselines", {})
        if group_baselines is not None and not isinstance(group_baselines, dict):
            raise ValueError("group_baselines must be an object")

    def save_query_view_config(self, data: dict[str, Any]) -> None:
        self._validate_query_view_config(data)
        self._write_query_view_config(data)

    def get_query_views(self) -> dict[str, Any]:
        config = self.get_query_view_config()
        views = config.get("views", {})
        if not isinstance(views, dict):
            return {}
        return views

    def resolve_batch_source(self, view_name: str, group: str | None = None) -> dict[str, str]:
        config = self.get_query_view_config()
        self._validate_query_view_config(config)
        views = config.get("views", {})
        if view_name not in views:
            raise ValueError(f"Unknown view_name: {view_name}")

        view = views[view_name]
        raw_source = view.get("batch_source", {})
        if group:
            per_group = view.get("per_group", {})
            group_config = per_group.get(group) if isinstance(per_group, dict) else None
            if isinstance(group_config, dict) and "batch_source" in group_config:
                raw_source = group_config.get("batch_source", {})

        source = raw_source if isinstance(raw_source, dict) else {}
        return {
            "table": str(source.get("table", "") or "").strip(),
            "field": str(source.get("field", "") or "").strip(),
        }

    def get_query_batch_source(self, view_name: str) -> dict[str, str]:
        source = self.resolve_batch_source(view_name)
        return {"view_name": view_name, **source}

    def update_query_batch_source(self, view_name: str, table: str, field: str) -> None:
        config = self.get_query_view_config()
        self._validate_query_view_config(config)
        views = config.get("views", {})
        if view_name not in views:
            raise ValueError(f"Unknown view_name: {view_name}")

        source_table = str(table or "").strip()
        source_field = str(field or "").strip()
        if bool(source_table) != bool(source_field):
            raise ValueError("batch_source table/field must both be set or both be empty")
        views[view_name]["batch_source"] = {"table": source_table, "field": source_field}
        self._validate_query_view_config(config)
        self._write_query_view_config(config)

    @staticmethod
    def _normalize_column_defs(column_defs: list[Any]) -> tuple[list[str], dict[str, dict[str, str]]]:
        names: list[str] = []
        labels: dict[str, dict[str, str]] = {}
        for item in column_defs:
            if isinstance(item, str):
                names.append(item)
                labels[item] = {"label_en": item, "label_zh": item}
            elif isinstance(item, dict) and item.get("name"):
                name = str(item["name"])
                names.append(name)
                labels[name] = {
                    "label_en": str(item.get("label_en", name)),
                    "label_zh": str(item.get("label_zh", name)),
                }
        return names, labels

    def resolve_query_view(self, view_name: str, table: str | None = None, group: str | None = None) -> dict[str, Any]:
        config = self.get_query_view_config()
        self._validate_query_view_config(config)
        views = config["views"]
        if view_name not in views:
            raise ValueError(f"Unknown view_name: {view_name}")

        view = views[view_name]
        resolved = {
            "name": view_name,
            "title": view.get("title", view_name),
            "time_field": view.get("time_field", "collection_time"),
            "batch_field": view.get("batch_field", ""),
            "sort_by": view.get("sort_by", "collection_time"),
            "sort_dir": str(view.get("sort_dir", "desc")).lower(),
            "default_page_size": int(config.get("default_page_size", 50)),
            "max_page_size": int(config.get("max_page_size", 500)),
            "page_size": int(view.get("page_size", config.get("default_page_size", 50))),
            "columns": [],
            "column_labels": {},
            "default_filters": view.get("default_filters", []),
            "description": view.get("description", ""),
        }
        base_columns, base_labels = self._normalize_column_defs(view.get("columns", []))
        resolved["columns"] = base_columns
        resolved["column_labels"] = base_labels

        if group:
            per_group = view.get("per_group", {})
            if isinstance(per_group, dict):
                group_override = per_group.get(group)
                if isinstance(group_override, dict):
                    if "columns" in group_override and isinstance(group_override["columns"], list):
                        gcols, glabels = self._normalize_column_defs(group_override["columns"])
                        resolved["columns"] = gcols
                        resolved["column_labels"] = glabels
                    if "time_field" in group_override and group_override["time_field"]:
                        resolved["time_field"] = str(group_override["time_field"])
                    if "batch_field" in group_override:
                        resolved["batch_field"] = str(group_override["batch_field"] or "")
                    if "sort_by" in group_override and group_override["sort_by"]:
                        resolved["sort_by"] = str(group_override["sort_by"])
                    if "sort_dir" in group_override and str(group_override["sort_dir"]).lower() in {"asc", "desc"}:
                        resolved["sort_dir"] = str(group_override["sort_dir"]).lower()
                    if "page_size" in group_override:
                        page_size = int(group_override["page_size"])
                        page_size = min(max(page_size, 1), resolved["max_page_size"])
                        resolved["page_size"] = page_size

        # 当传入 group 时，按需求仅使用 group 级配置，不再让 per_table 覆盖。
        if table and group is None:
            per_table = view.get("per_table", {})
            if isinstance(per_table, dict):
                override = per_table.get(table)
                if isinstance(override, dict):
                    if "columns" in override and isinstance(override["columns"], list):
                        table_columns, table_labels = self._normalize_column_defs(override["columns"])
                        resolved["columns"] = table_columns
                        resolved["column_labels"] = table_labels
                    if "time_field" in override and override["time_field"]:
                        resolved["time_field"] = str(override["time_field"])
                    if "batch_field" in override:
                        resolved["batch_field"] = str(override["batch_field"] or "")
                    if "sort_by" in override and override["sort_by"]:
                        resolved["sort_by"] = str(override["sort_by"])
                    if "sort_dir" in override and str(override["sort_dir"]).lower() in {"asc", "desc"}:
                        resolved["sort_dir"] = str(override["sort_dir"]).lower()
                    if "page_size" in override:
                        page_size = int(override["page_size"])
                        page_size = min(max(page_size, 1), resolved["max_page_size"])
                        resolved["page_size"] = page_size

        return resolved

    def get_group_baseline(self, group: str) -> str | None:
        config = self.get_query_view_config()
        group_baselines = config.get("group_baselines", {})
        if not isinstance(group_baselines, dict):
            return None
        value = group_baselines.get(group)
        if value is None:
            return None
        return str(value)

    def set_group_baseline(self, group: str, baseline_table: str) -> None:
        config = self.get_query_view_config()
        self._validate_query_view_config(config)
        group_baselines = config.setdefault("group_baselines", {})
        if not isinstance(group_baselines, dict):
            raise ValueError("group_baselines must be an object")
        group_baselines[group] = baseline_table
        self._validate_query_view_config(config)
        self._write_query_view_config(config)

    def get_query_table_config(self, view_name: str, table: str) -> dict[str, Any]:
        resolved = self.resolve_query_view(view_name, table)
        columns = []
        for name in resolved["columns"]:
            labels = resolved["column_labels"].get(name, {"label_en": name, "label_zh": name})
            columns.append(
                {
                    "name": name,
                    "label_en": labels.get("label_en", name),
                    "label_zh": labels.get("label_zh", name),
                }
            )
        return {
            "view_name": view_name,
            "table": table,
            "sort_by": resolved["sort_by"],
            "sort_dir": resolved["sort_dir"],
            "page_size": resolved["page_size"],
            "columns": columns,
        }

    def update_query_table_config(
        self,
        view_name: str,
        table: str,
        sort_by: str,
        sort_dir: str,
        page_size: int,
        columns: list[dict[str, Any]],
    ) -> None:
        config = self.get_query_view_config()
        self._validate_query_view_config(config)
        views = config.get("views", {})
        if view_name not in views:
            raise ValueError(f"Unknown view_name: {view_name}")

        view = views[view_name]
        per_table = view.setdefault("per_table", {})
        if not isinstance(per_table, dict):
            raise ValueError("per_table config must be an object")

        normalized_columns: list[dict[str, str]] = []
        seen: set[str] = set()
        for col in columns:
            if not isinstance(col, dict):
                continue
            name = str(col.get("name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            normalized_columns.append(
                {
                    "name": name,
                    "label_en": str(col.get("label_en", name)),
                    "label_zh": str(col.get("label_zh", name)),
                }
            )

        per_table[table] = {
            "columns": normalized_columns,
            "sort_by": str(sort_by),
            "sort_dir": "asc" if str(sort_dir).lower() == "asc" else "desc",
            "page_size": max(1, int(page_size)),
        }

        self._validate_query_view_config(config)
        self._write_query_view_config(config)

    def get_query_group_config(self, view_name: str, group: str, baseline_table: str) -> dict[str, Any]:
        resolved = self.resolve_query_view(view_name=view_name, table=baseline_table, group=group)
        columns = []
        for name in resolved["columns"]:
            labels = resolved["column_labels"].get(name, {"label_en": name, "label_zh": name})
            columns.append(
                {
                    "name": name,
                    "label_en": labels.get("label_en", name),
                    "label_zh": labels.get("label_zh", name),
                }
            )
        return {
            "view_name": view_name,
            "group": group,
            "baseline_table": baseline_table,
            "time_field": resolved["time_field"],
            "batch_field": resolved["batch_field"],
            "sort_by": resolved["sort_by"],
            "sort_dir": resolved["sort_dir"],
            "page_size": resolved["page_size"],
            "columns": columns,
        }

    def update_query_group_config(
        self,
        view_name: str,
        group: str,
        time_field: str,
        batch_field: str,
        sort_by: str,
        sort_dir: str,
        page_size: int,
        columns: list[dict[str, Any]],
        baseline_table: str | None = None,
    ) -> None:
        config = self.get_query_view_config()
        self._validate_query_view_config(config)
        views = config.get("views", {})
        if view_name not in views:
            raise ValueError(f"Unknown view_name: {view_name}")

        view = views[view_name]
        per_group = view.setdefault("per_group", {})
        if not isinstance(per_group, dict):
            raise ValueError("per_group config must be an object")

        normalized_columns: list[dict[str, str]] = []
        seen: set[str] = set()
        for col in columns:
            if not isinstance(col, dict):
                continue
            name = str(col.get("name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            normalized_columns.append(
                {
                    "name": name,
                    "label_en": str(col.get("label_en", name)),
                    "label_zh": str(col.get("label_zh", name)),
                }
            )

        per_group[group] = {
            "columns": normalized_columns,
            "time_field": str(time_field),
            "batch_field": str(batch_field or ""),
            "sort_by": str(sort_by),
            "sort_dir": "asc" if str(sort_dir).lower() == "asc" else "desc",
            "page_size": max(1, int(page_size)),
        }

        if baseline_table:
            group_baselines = config.setdefault("group_baselines", {})
            if not isinstance(group_baselines, dict):
                raise ValueError("group_baselines must be an object")
            group_baselines[group] = baseline_table

        self._validate_query_view_config(config)
        self._write_query_view_config(config)

    def list_configured_groups_by_view(self, view_name: str) -> list[str]:
        config = self.get_query_view_config()
        self._validate_query_view_config(config)
        views = config.get("views", {})
        if view_name not in views:
            raise ValueError(f"Unknown view_name: {view_name}")
        view = views[view_name]
        per_group = view.get("per_group", {})
        if not isinstance(per_group, dict):
            return []
        return sorted(str(name) for name in per_group.keys())

    def delete_query_group_config(self, view_name: str, group: str) -> bool:
        config = self.get_query_view_config()
        self._validate_query_view_config(config)
        views = config.get("views", {})
        if view_name not in views:
            raise ValueError(f"Unknown view_name: {view_name}")
        view = views[view_name]
        per_group = view.setdefault("per_group", {})
        if not isinstance(per_group, dict):
            raise ValueError("per_group config must be an object")
        existed = group in per_group
        if existed:
            del per_group[group]
            self._validate_query_view_config(config)
            self._write_query_view_config(config)
        return existed
