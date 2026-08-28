from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config_loader import ConfigLoader
from core.config_models import FIXED_INDEX_COLUMNS
from core.secret_store import (
    migrate_password_mapping,
    password_is_configured,
    prepare_password_mapping,
)


class CollectorConfigManager:
    ALLOWED_TOP_LEVEL_KEYS = {
        "communications",
        "connections",
        "points",
        "groups",
        "database",
        "logging",
        "persistent_queue",
    }
    REMOVED_TRIGGER_TYPES = {"query"}

    def __init__(
        self,
        collector_config_dir: Path,
    ):
        self.collector_config_dir = collector_config_dir
        self.runtime_settings_path = collector_config_dir / ".collector_runtime_settings.json"

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"配置文件必须是 JSON 对象: {path}")
        return data

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        candidate = Path(filename).name.strip()
        if not candidate or candidate in {".", ".."} or candidate.startswith("."):
            raise ValueError("文件名无效")
        if not candidate.lower().endswith(".json"):
            raise ValueError("文件名必须以 .json 结尾")
        return candidate

    def sanitize_filename(self, filename: str) -> str:
        return self._sanitize_filename(filename)

    @staticmethod
    def default_runtime_settings() -> dict[str, Any]:
        return {
            "auto_start_enabled": False,
            "auto_start_config": "",
            "auto_start_delay_seconds": 3,
        }

    def load_runtime_settings(self) -> dict[str, Any]:
        raw = self._load_json(self.runtime_settings_path)
        defaults = self.default_runtime_settings()
        settings = {
            **defaults,
            **{key: raw.get(key) for key in defaults if key in raw},
        }
        settings["auto_start_enabled"] = bool(settings.get("auto_start_enabled"))
        settings["auto_start_config"] = str(settings.get("auto_start_config") or "").strip()
        try:
            delay = int(settings.get("auto_start_delay_seconds", defaults["auto_start_delay_seconds"]))
        except (TypeError, ValueError):
            delay = defaults["auto_start_delay_seconds"]
        settings["auto_start_delay_seconds"] = max(0, min(delay, 300))
        return settings

    def save_runtime_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        normalized = self.default_runtime_settings()
        normalized["auto_start_enabled"] = bool(settings.get("auto_start_enabled"))
        normalized["auto_start_config"] = str(settings.get("auto_start_config") or "").strip()
        try:
            delay = int(settings.get("auto_start_delay_seconds", normalized["auto_start_delay_seconds"]))
        except (TypeError, ValueError):
            delay = normalized["auto_start_delay_seconds"]
        normalized["auto_start_delay_seconds"] = max(0, min(delay, 300))

        if normalized["auto_start_enabled"]:
            filename = self._sanitize_filename(normalized["auto_start_config"])
            target = (self.collector_config_dir / filename).resolve()
            if target.parent != self.collector_config_dir.resolve():
                raise ValueError("非法路径")
            if not target.is_file():
                raise ValueError(f"配置文件不存在: {filename}")
            normalized["auto_start_config"] = filename

        self._write_json(self.runtime_settings_path, normalized)
        return normalized

    @classmethod
    def default_payload(cls) -> dict[str, Any]:
        return {
            "communications": [],
            "connections": [],
            "points": [],
            "groups": [],
            "database": {
                "type": "mysql",
                "name": "",
                "host": "127.0.0.1",
                "port": 3306,
                "username": "",
                "password": "",
                "password_configured": False,
                "data_groups": [],
            },
            "logging": {
                "level": "INFO",
                "output_dir": "logs",
                "backup_days": 14,
                "rotation_when": "midnight",
                "rotation_interval": 1,
                "console_enabled": True,
            },
            "persistent_queue": {
                "enabled": False,
                "path": "runtime/queue/collector_outbox.db",
                "synchronous": "FULL",
                "busy_timeout_ms": 5000,
                "lease_seconds": 60.0,
                "retry_interval_seconds": 5.0,
                "max_retry_interval_seconds": 300.0,
                "max_attempts": 0,
                "completed_retention_days": 1,
                "cleanup_interval_seconds": 3600.0,
                "max_queue_rows": 1000000,
            },
        }

    @classmethod
    def sanitize_for_ui(cls, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
        if not isinstance(payload, dict):
            raise ValueError("配置必须是 JSON 对象")

        sanitized = cls.default_payload()
        stats = {"query_groups_hidden": 0, "query_fields_hidden": 0}

        for key in cls.ALLOWED_TOP_LEVEL_KEYS:
            if key not in payload:
                continue
            if key == "groups":
                continue
            sanitized[key] = payload[key]

        groups = payload.get("groups", [])
        if isinstance(groups, list):
            clean_groups: list[dict[str, Any]] = []
            for item in groups:
                if not isinstance(item, dict):
                    continue
                trigger = str(item.get("trigger", "")).lower()
                if trigger in cls.REMOVED_TRIGGER_TYPES:
                    raise ValueError("当前版本已删除 trigger=query 功能，请先从配置中移除查询组")
                group_copy = copy.deepcopy(item)
                if "query_config" in group_copy:
                    raise ValueError("当前版本已删除 groups[].query_config 字段，请先从配置中移除")
                if "output_mode" in group_copy:
                    raise ValueError("当前版本已删除 groups[].output_mode 字段，请先从配置中移除")
                clean_groups.append(group_copy)
            sanitized["groups"] = clean_groups

        # 保证关键结构类型正确，避免前端渲染失败
        for list_key in ("communications", "connections", "points", "groups"):
            if not isinstance(sanitized.get(list_key), list):
                sanitized[list_key] = []
        if not isinstance(sanitized.get("database"), dict):
            sanitized["database"] = cls.default_payload()["database"]
        database = dict(sanitized["database"])
        database["password_configured"] = password_is_configured(database, "SD_SMA_DB_PASSWORD")
        database["password"] = ""
        database.pop("password_enc", None)
        database.pop("clear_password", None)
        sanitized["database"] = database
        if not isinstance(sanitized.get("logging"), dict):
            sanitized["logging"] = cls.default_payload()["logging"]
        if not isinstance(sanitized.get("persistent_queue"), dict):
            sanitized["persistent_queue"] = cls.default_payload()["persistent_queue"]

        return sanitized, stats

    def _migrate_database_secret(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        migrated = copy.deepcopy(payload)
        database, changed = migrate_password_mapping(
            self.collector_config_dir,
            migrated.get("database", {}),
        )
        database.pop("password_configured", None)
        migrated["database"] = database
        return migrated, changed

    def _prepare_for_storage(
        self,
        payload: dict[str, Any],
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stored = copy.deepcopy(payload)
        existing_database = (existing or {}).get("database", {})
        database = prepare_password_mapping(
            self.collector_config_dir,
            stored.get("database", {}),
            existing_database if isinstance(existing_database, dict) else {},
        )
        database.pop("password_configured", None)
        stored["database"] = database
        return stored

    def _validate_scope(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ValueError("配置必须是 JSON 对象")

        unknown_keys = [k for k in payload.keys() if k not in self.ALLOWED_TOP_LEVEL_KEYS]
        if unknown_keys:
            raise ValueError(f"包含不支持的顶层字段: {unknown_keys}")

        groups = payload.get("groups", [])
        if not isinstance(groups, list):
            raise ValueError("groups 必须是数组")

        self._validate_points_unique(payload)

        for group in groups:
            if not isinstance(group, dict):
                raise ValueError("groups 数组元素必须是对象")
            trigger = str(group.get("trigger", "")).lower()
            if trigger in self.REMOVED_TRIGGER_TYPES:
                raise ValueError("当前版本已删除 trigger=query")
            if "query_config" in group:
                raise ValueError("当前版本已删除 groups[].query_config 字段")
            if "output_mode" in group:
                raise ValueError("当前版本已删除 groups[].output_mode 字段")

            # 验证 indexes 配置
            indexes = group.get("indexes")
            if indexes is not None:
                if not isinstance(indexes, list):
                    raise ValueError(f"数据组 '{group.get('name', '<unknown>')}' 的 indexes 必须是数组")
                data_points = group.get("data_points", [])
                seen_names: set[str] = set()
                for i, idx_def in enumerate(indexes):
                    if not isinstance(idx_def, dict):
                        raise ValueError(f"数据组 '{group.get('name', '<unknown>')}' 的 indexes[{i}] 必须是对象")
                    columns = idx_def.get("columns", [])
                    if not isinstance(columns, list) or len(columns) == 0:
                        raise ValueError(f"数据组 '{group.get('name', '<unknown>')}' 的 indexes[{i}].columns 必须是非空数组")
                    allowed_index_columns = set(data_points) | FIXED_INDEX_COLUMNS
                    for col in columns:
                        if not isinstance(col, str) or col not in allowed_index_columns:
                            raise ValueError(
                                f"数据组 '{group.get('name', '<unknown>')}' 的 indexes[{i}].columns "
                                f"引用了不存在的点位或固定字段: {col}"
                            )
                    # 校验索引名
                    name = str(idx_def.get("name", "")).strip()
                    if name:
                        if len(name) > 64:
                            raise ValueError(
                                f"数据组 '{group.get('name', '<unknown>')}' 的 indexes[{i}].name 超过 64 字符"
                            )
                        if name in seen_names:
                            raise ValueError(
                                f"数据组 '{group.get('name', '<unknown>')}' 的 indexes[{i}].name 重复: {name}"
                            )
                        seen_names.add(name)

    @staticmethod
    def _validate_points_unique(payload: dict[str, Any]) -> None:
        points = payload.get("points", [])
        if not isinstance(points, list):
            raise ValueError("校验失败：points 必须是数组")

        seen_names: set[str] = set()
        seen_paths: set[str] = set()
        for idx, point in enumerate(points):
            if not isinstance(point, dict):
                raise ValueError(f"校验失败：points[{idx}] 必须是对象")

            name = str(point.get("name", "")).strip()
            path = str(point.get("path", "")).strip()

            if not name:
                raise ValueError(f"校验失败：points[{idx}].name 不能为空")
            if not path:
                raise ValueError(f"校验失败：points[{idx}].path 不能为空")

            if name in seen_names:
                raise ValueError(f"校验失败：points.name 重复：{name}")
            if path in seen_paths:
                raise ValueError(f"校验失败：points.path 重复：{path}")

            seen_names.add(name)
            seen_paths.add(path)

    def _validate_by_loader(self, payload: dict[str, Any]) -> None:
        try:
            ConfigLoader._parse_config(payload)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"配置校验失败: {exc}") from exc

    def get_template(self) -> dict[str, Any]:
        return self.default_payload()

    def list_config_files(self) -> list[str]:
        self.collector_config_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for path in self.collector_config_dir.glob("*.json"):
            if path.is_file() and not path.name.startswith("."):
                files.append(path.name)
        return sorted(files)

    def load_config_file(self, filename: str) -> dict[str, Any]:
        safe_name = self._sanitize_filename(filename)
        source = (self.collector_config_dir / safe_name).resolve()
        if source.parent != self.collector_config_dir.resolve():
            raise ValueError("非法路径")
        if not source.exists():
            raise ValueError(f"配置文件不存在: {safe_name}")

        payload = self._load_json(source)
        payload, migrated = self._migrate_database_secret(payload)
        if migrated:
            tmp_source = source.with_suffix(f"{source.suffix}.tmp")
            self._write_json(tmp_source, payload)
            tmp_source.replace(source)
        sanitized, stats = self.sanitize_for_ui(payload)
        return {
            "filename": safe_name,
            "payload": sanitized,
            "hidden": stats,
        }

    def delete_config_file(self, filename: str) -> Path:
        safe_name = self._sanitize_filename(filename)
        source = (self.collector_config_dir / safe_name).resolve()
        if source.parent != self.collector_config_dir.resolve():
            raise ValueError("非法路径")
        if not source.exists():
            raise ValueError(f"配置文件不存在: {safe_name}")
        source.unlink()
        return source

    def save_template(self, payload: dict[str, Any]) -> None:
        self._validate_scope(payload)
        self._validate_by_loader(payload)

    def validate_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_scope(payload)
        self._validate_by_loader(payload)
        return {"ok": True}

    def export_to_path(self, payload: dict[str, Any], output_path: Path) -> Path:
        self._validate_scope(payload)
        self._validate_by_loader(payload)
        stored = self._prepare_for_storage(payload)
        self._write_json(output_path, stored)
        return output_path

    def write_collector_config(self, payload: dict[str, Any], filename: str) -> Path:
        self._validate_scope(payload)
        self._validate_by_loader(payload)

        safe_name = self._sanitize_filename(filename)
        self.collector_config_dir.mkdir(parents=True, exist_ok=True)
        target = (self.collector_config_dir / safe_name).resolve()
        existing: dict[str, Any] = {}
        if target.exists():
            existing, migrated = self._migrate_database_secret(self._load_json(target))
            if migrated:
                migration_tmp = target.with_suffix(f"{target.suffix}.tmp")
                self._write_json(migration_tmp, existing)
                migration_tmp.replace(target)
        stored = self._prepare_for_storage(payload, existing)

        backup_dir = (self.collector_config_dir / "_backup").resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)

        if target.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = backup_dir / f"{target.stem}_{timestamp}{target.suffix}"
            # Config backups only need file contents. copy2() also copies file
            # metadata and can fail with WinError 127 on early Windows 10 builds.
            shutil.copyfile(target, backup_file)

        tmp_target = target.with_suffix(f"{target.suffix}.tmp")
        self._write_json(tmp_target, stored)
        tmp_target.replace(target)
        return target

    @staticmethod
    def build_point_from_node(
        node_id: str,
        display_name: str,
        description: str = "",
        datatype: str | None = None,
    ) -> dict[str, str]:
        raw_name = display_name.strip() or "point"
        normalized = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in raw_name)
        normalized = normalized.strip("_") or "point"
        if normalized[0].isdigit():
            normalized = f"p_{normalized}"
        point = {
            "name": normalized,
            "path": node_id,
            "description": description or display_name or node_id,
        }
        if datatype:
            point["datatype"] = datatype
        return point

    @staticmethod
    def append_point(payload: dict[str, Any], point_item: dict[str, Any]) -> dict[str, Any]:
        new_payload = copy.deepcopy(payload)
        points = new_payload.setdefault("points", [])
        if not isinstance(points, list):
            raise ValueError("points 字段不是数组")

        point_item = dict(point_item)
        new_name = str(point_item.get("name", "")).strip()
        new_path = str(point_item.get("path", "")).strip()

        for item in points:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            path = str(item.get("path", "")).strip()
            if new_name and name == new_name:
                raise ValueError(f"校验失败：points.name 重复：{new_name}")
            if new_path and path == new_path:
                raise ValueError(f"校验失败：points.path 重复：{new_path}")

        if not new_name:
            raise ValueError("校验失败：points.name 不能为空")
        if not new_path:
            raise ValueError("校验失败：points.path 不能为空")

        point_item["name"] = new_name
        point_item["path"] = new_path
        points.append(point_item)
        return new_payload

