from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Iterable


class FilesystemBrowserError(ValueError):
    """Raised when a requested path is outside the configured browse roots."""


def windows_removable_roots() -> list[Path]:
    """Return mounted removable drive roots without depending on a user session."""
    if os.name != "nt":
        return []
    try:
        import ctypes

        mask = int(ctypes.windll.kernel32.GetLogicalDrives())
        roots: list[Path] = []
        for index in range(26):
            if not (mask & (1 << index)):
                continue
            root = f"{chr(ord('A') + index)}:\\"
            if int(ctypes.windll.kernel32.GetDriveTypeW(root)) == 2:
                roots.append(Path(root))
        return roots
    except Exception:
        return []


def _resolved(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((str(path), str(root)))
        return os.path.normcase(common) == os.path.normcase(str(root))
    except ValueError:
        return False


class FilesystemBrowser:
    """Enumerate files without exposing paths outside explicit server-side roots."""

    def __init__(self, roots: Iterable[str | os.PathLike[str]]) -> None:
        unique: dict[str, Path] = {}
        for value in roots:
            raw = str(value or "").strip()
            if not raw:
                continue
            root = _resolved(raw)
            unique.setdefault(os.path.normcase(str(root)), root)
        self.roots = tuple(unique.values())

    def public_roots(self) -> list[dict[str, object]]:
        return [
            {
                "name": root.name or str(root),
                "path": str(root),
                "exists": root.is_dir(),
            }
            for root in self.roots
        ]

    def authorize(self, value: str | os.PathLike[str]) -> tuple[Path, Path]:
        if not str(value or "").strip():
            raise FilesystemBrowserError("Path is required")
        candidate = _resolved(value)
        for root in self.roots:
            if _is_within(candidate, root):
                return candidate, root
        raise FilesystemBrowserError("Path is outside the allowed browse roots")

    def entries(
        self,
        value: str | os.PathLike[str],
        *,
        extensions: Iterable[str] = (),
        include_files: bool = True,
    ) -> dict[str, object]:
        current, root = self.authorize(value)
        if not current.is_dir():
            raise FilesystemBrowserError("Directory does not exist")
        allowed_extensions = {
            str(item).lower() if str(item).startswith(".") else f".{str(item).lower()}"
            for item in extensions
            if str(item).strip()
        }
        result: list[dict[str, object]] = []
        try:
            children = sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError as exc:
            raise FilesystemBrowserError(f"Directory is not readable: {exc}") from exc
        for child in children:
            try:
                resolved_child = child.resolve(strict=False)
                if not _is_within(resolved_child, root):
                    continue
                is_dir = child.is_dir()
                if not is_dir and (not include_files or (allowed_extensions and child.suffix.lower() not in allowed_extensions)):
                    continue
                stat = child.stat()
            except OSError:
                continue
            result.append(
                {
                    "name": child.name,
                    "path": str(resolved_child),
                    "type": "directory" if is_dir else "file",
                    "size": 0 if is_dir else int(stat.st_size),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                }
            )
        parent = ""
        if current != root:
            candidate_parent = current.parent.resolve(strict=False)
            if _is_within(candidate_parent, root):
                parent = str(candidate_parent)
        return {"current": str(current), "parent": parent, "entries": result}
