"""统一自检入口:发现并运行仓库内所有 *_check.py,任一失败则退出码 1。

用法: python selfcheck.py
约定: 自检脚本必须 `python <路径>` 直接可运行、exit 0 即通过;
      需要跨服务 import 时,默认已注入仓库根与 _Prj/SD_SMA_COMMON 到 PYTHONPATH
      (与各服务 sys.path 注入 COMMON_ROOT 的惯例一致)。

ponytail: PYTHONPATH 只硬编码 COMMON 一个额外路径;若未来出现需要其他
_Prj 路径的自检脚本,升级为在每个 _check.py 顶部自行 sys.path 注入。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMON = ROOT / "_Prj" / "SD_SMA_COMMON"

# 归档/产物/PLC 工程/运行时目录不扫
SKIP_DIRS = {
    "_Archive", "_Build", ".git", ".venv", ".cursor", "wheelhouse",
    "__pycache__", "ar_test", "logs", "backups", "secrets", "node_modules",
}
TIMEOUT_S = 180


def find_checks() -> list[Path]:
    return sorted(
        p for p in ROOT.rglob("*_check.py")
        if not any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts)
    )


def run_one(path: Path) -> tuple[bool, str]:
    env = os.environ.copy()
    extra = os.pathsep.join((str(ROOT), str(COMMON)))
    env["PYTHONPATH"] = extra + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else extra
    try:
        r = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(path.parent), env=env,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT > {TIMEOUT_S}s"
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-20:])
    return r.returncode == 0, tail


def main() -> int:
    checks = find_checks()
    if not checks:
        print("selfcheck: no *_check.py found, nothing to do")
        return 0
    failed: list[str] = []
    for p in checks:
        rel = p.relative_to(ROOT)
        print(f"[run ] {rel}")
        t0 = time.monotonic()
        ok, tail = run_one(p)
        dt = time.monotonic() - t0
        if ok:
            print(f"[PASS] {rel}  ({dt:.1f}s)")
        else:
            failed.append(str(rel))
            print(f"[FAIL] {rel}  ({dt:.1f}s)")
            for line in tail.splitlines():
                print(f"       {line}")
    print(f"\nselfcheck: {len(checks) - len(failed)}/{len(checks)} passed")
    if failed:
        print("failed: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())