from __future__ import annotations

import json
import secrets
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

_COMMON_ROOT = Path(__file__).resolve().parent.parent / "_Prj" / "SD_SMA_COMMON"
if _COMMON_ROOT.is_dir() and str(_COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMMON_ROOT))

from sd_sma_common.filesystem_browser import FilesystemBrowser, FilesystemBrowserError, windows_removable_roots


SERVICE_FOLDERS = {
    "collector_web": "collector",
    "query_web": "query_web",
    "db_admin": "db_admin",
    "report_copy": "report_copy",
}

KNOWN_KEYS = {
    "collector_web": {"communications", "connections", "data_groups", "database", "points", "logging"},
    "query_web": {"version", "name", "app_settings", "opcua", "query_view"},
    "db_admin": {"mysql_tools", "backup_dir", "default_connection", "allowed_browse_roots"},
    "report_copy": {"report_source_dir", "destination_folder", "allowed_extensions", "copy_subdirectories"},
}

BUNDLE_FOLDER_ALIASES = {
    "collector_web": ("collector", "collector_web", "SD_SMA_DATA_COLLECTOR"),
    "query_web": ("query_web", "SD_SMA_DATA_COLLECTOR_QUERY_WEB"),
    "db_admin": ("db_admin", "SD_SMA_DB_ADMIN"),
    "report_copy": ("report_copy", "SD_SMA_REPORT_COPY"),
}


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _sanitize_secrets(value: Any, warnings: list[str], location: str = "") -> Any:
    if isinstance(value, list):
        return [_sanitize_secrets(item, warnings, f"{location}[]") for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, child in value.items():
        lowered = str(key).lower()
        child_location = f"{location}.{key}" if location else str(key)
        if lowered in {"password", "password_enc", "clear_password", "password_configured"}:
            if child not in (None, "", False):
                warnings.append(f"已移除外部敏感字段：{child_location}")
            continue
        result[str(key)] = _sanitize_secrets(child, warnings, child_location)
    return result


class ConfigImportManager:
    def __init__(self, data_root: Path, *, preview_seconds: int = 10 * 60) -> None:
        self.data_root = data_root
        self.import_box = data_root / "ImportBox"
        self.settings_path = data_root / "config" / "launcher" / "import_settings.json"
        self.preview_seconds = preview_seconds
        self._lock = threading.RLock()
        self._previews: dict[str, dict[str, Any]] = {}
        self.import_box.mkdir(parents=True, exist_ok=True)

    def settings(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
            roots = raw.get("allowed_import_roots", [])
            if isinstance(roots, list):
                return {"allowed_import_roots": [str(item) for item in roots if str(item).strip()]}
        except (OSError, ValueError, TypeError):
            pass
        return {"allowed_import_roots": []}

    def update_settings(self, roots: list[str]) -> dict[str, Any]:
        normalized: list[str] = []
        for value in roots:
            path = Path(str(value)).expanduser().resolve(strict=False)
            if not path.is_absolute() or not path.is_dir():
                raise ValueError(f"目录不存在：{value}")
            if str(path).startswith("\\\\"):
                raise ValueError("首版不允许网络路径")
            if str(path) not in normalized:
                normalized.append(str(path))
        result = {"version": 1, "allowed_import_roots": normalized}
        _atomic_json(self.settings_path, result)
        return {"allowed_import_roots": normalized}

    def browser(self) -> FilesystemBrowser:
        roots = [self.import_box, *windows_removable_roots(), *self.settings()["allowed_import_roots"]]
        return FilesystemBrowser(roots)

    def roots(self) -> list[dict[str, object]]:
        return self.browser().public_roots()

    def entries(self, path: str) -> dict[str, object]:
        return self.browser().entries(path, extensions=(".json",), include_files=True)

    def inspect(self, service: str, paths: list[str]) -> dict[str, Any]:
        if service not in {*SERVICE_FOLDERS, "all"}:
            raise ValueError("未知目标服务")
        if not paths:
            raise ValueError("请选择 JSON 文件或目录")
        browser = self.browser()
        if service == "all":
            if len(paths) != 1:
                raise ValueError("整包导入必须选择一个配置根目录")
            root, _allowed_root = browser.authorize(paths[0])
            if not root.is_dir():
                raise ValueError("整包导入必须选择配置根目录")
            source_dirs = self._bundle_source_dirs(root)
            warnings: list[str] = []
            files: list[dict[str, Any]] = []
            for target_service, source_dir in source_dirs.items():
                candidates = self._candidate_files(target_service, [source_dir], browser)
                files.extend(self._read_files(target_service, candidates, warnings))
            return self._save_preview("all", list(SERVICE_FOLDERS), files, warnings)

        candidates = self._candidate_files(service, paths, browser)
        warnings: list[str] = []
        files = self._read_files(service, candidates, warnings)
        return self._save_preview(service, [service], files, warnings)

    def _candidate_files(
        self, service: str, paths: list[str | Path], browser: FilesystemBrowser
    ) -> list[Path]:
        candidates: list[Path] = []
        for raw_path in paths:
            authorized, _root = browser.authorize(raw_path)
            if authorized.is_dir():
                candidates.extend(sorted(authorized.glob("*.json")))
            elif authorized.is_file() and authorized.suffix.lower() == ".json":
                candidates.append(authorized)
            else:
                raise ValueError(f"只允许 JSON 文件或目录：{authorized}")
        unique = {str(path.resolve(strict=True)): path.resolve(strict=True) for path in candidates}
        candidates = list(unique.values())
        if service in {"db_admin", "report_copy"} and len(candidates) != 1:
            raise ValueError("该服务每次必须且只能导入一个 JSON 文件")
        if not candidates:
            raise ValueError("所选位置没有 JSON 文件")
        return candidates

    def _read_files(self, service: str, candidates: list[Path], warnings: list[str]) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for source in candidates:
            try:
                raw = json.loads(source.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                raise ValueError(f"无法解析 {source.name}：{exc}") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"{source.name} 顶层必须是 JSON 对象")
            if not (set(raw) & KNOWN_KEYS[service]):
                raise ValueError(f"{source.name} 不像 {service} 的配置文件")
            sanitized = _sanitize_secrets(raw, warnings)
            target_name = "default.json" if service in {"db_admin", "report_copy"} else source.name
            stat = source.stat()
            files.append(
                {
                    "service": service,
                    "source": str(source),
                    "target_name": target_name,
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                    "data": sanitized,
                }
            )
        return files

    def _bundle_source_dirs(self, root: Path) -> dict[str, Path]:
        bases = [root]
        if (root / "config").is_dir():
            bases.insert(0, root / "config")
        found: dict[str, Path] = {}
        for service, aliases in BUNDLE_FOLDER_ALIASES.items():
            for base in bases:
                for alias in aliases:
                    candidate = base / alias
                    if not candidate.is_dir():
                        continue
                    config_child = candidate / "config"
                    found[service] = config_child if config_child.is_dir() else candidate
                    break
                if service in found:
                    break
        missing = [SERVICE_FOLDERS[item] for item in SERVICE_FOLDERS if item not in found]
        if missing:
            raise ValueError(f"配置文件夹缺少：{', '.join(missing)}")
        return found

    def _save_preview(
        self,
        requested_service: str,
        services: list[str],
        files: list[dict[str, Any]],
        warnings: list[str],
    ) -> dict[str, Any]:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._prune_previews()
            self._previews[token] = {
                "expires": time.monotonic() + self.preview_seconds,
                "service": requested_service,
                "services": services,
                "files": files,
                "warnings": warnings,
            }
        target_dir = self.data_root / "config"
        return {
            "preview_token": token,
            "service": requested_service,
            "services": services,
            "target_dir": str(target_dir / SERVICE_FOLDERS[services[0]]) if len(services) == 1 else str(target_dir),
            "files": [
                {
                    "service": row["service"],
                    "source": row["source"],
                    "target_name": row["target_name"],
                    "size": row["size"],
                    "will_overwrite": (
                        target_dir / SERVICE_FOLDERS[row["service"]] / row["target_name"]
                    ).exists(),
                }
                for row in files
            ],
            "warnings": warnings,
        }

    def apply(self, token: str) -> dict[str, Any]:
        with self._lock:
            preview = self._previews.pop(token, None)
        if not preview or float(preview.get("expires", 0)) <= time.monotonic():
            raise ValueError("导入预览已过期，请重新检查")
        service = str(preview["service"])
        services = [str(item) for item in preview.get("services") or [service]]
        files = list(preview["files"])
        for row in files:
            source = Path(row["source"])
            try:
                stat = source.stat()
            except OSError as exc:
                raise ValueError(f"源文件已不可用：{source}") from exc
            if int(stat.st_size) != int(row["size"]) or int(stat.st_mtime_ns) != int(row["mtime_ns"]):
                raise ValueError(f"检查后源文件发生变化：{source.name}")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_dir = self.data_root / "backups" / "config_import" / stamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        try:
            for row in files:
                row_service = str(row["service"])
                target_dir = self.data_root / "config" / SERVICE_FOLDERS[row_service]
                service_backup_dir = backup_dir / row_service
                target_dir.mkdir(parents=True, exist_ok=True)
                service_backup_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / str(row["target_name"])
                if target.exists():
                    shutil.copy2(target, service_backup_dir / target.name)
                _atomic_json(target, row["data"])
                written.append(str(target))
        except Exception:
            for row in files:
                row_service = str(row["service"])
                target_dir = self.data_root / "config" / SERVICE_FOLDERS[row_service]
                target = target_dir / str(row["target_name"])
                backup = backup_dir / row_service / target.name
                if backup.exists():
                    shutil.copy2(backup, target)
                elif str(target) in written:
                    target.unlink(missing_ok=True)
            raise
        return {
            "ok": True,
            "service": service,
            "services": services,
            "files": written,
            "backup_dir": str(backup_dir),
            "targets": [
                {"service": str(row["service"]), "target_name": str(row["target_name"])} for row in files
            ],
            "target_names": [str(row["target_name"]) for row in files] if len(services) == 1 else [],
            "warnings": list(preview.get("warnings", [])),
        }

    def rollback(self, result: Mapping[str, Any]) -> None:
        service = str(result.get("service", ""))
        if service not in {*SERVICE_FOLDERS, "all"}:
            raise ValueError("未知回滚服务")
        backup_dir = Path(str(result.get("backup_dir", ""))).resolve(strict=True)
        expected_backup_root = (self.data_root / "backups" / "config_import").resolve(strict=True)
        try:
            backup_dir.relative_to(expected_backup_root)
        except ValueError as exc:
            raise ValueError("回滚备份目录越界") from exc
        targets = list(result.get("targets") or [])
        if not targets:
            targets = [{"service": service, "target_name": name} for name in result.get("target_names", [])]
        for row in targets:
            row_service = str(row.get("service", ""))
            if row_service not in SERVICE_FOLDERS:
                raise ValueError("回滚目标服务无效")
            safe_name = Path(str(row.get("target_name", ""))).name
            target_dir = self.data_root / "config" / SERVICE_FOLDERS[row_service]
            target = target_dir / safe_name
            backup = backup_dir / row_service / safe_name
            if backup.exists():
                shutil.copy2(backup, target)
            else:
                target.unlink(missing_ok=True)

    def _prune_previews(self) -> None:
        now = time.monotonic()
        self._previews = {
            token: preview for token, preview in self._previews.items() if float(preview.get("expires", 0)) > now
        }


__all__ = ["ConfigImportManager", "FilesystemBrowserError"]
