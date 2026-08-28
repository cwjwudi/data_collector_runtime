"""检查采集配置：打印数据组/数据库配置，并对配置中实际存在的组做数据点重复分析。"""
from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "sample_config.json"

GroupOverlap = tuple[str, str, set[str], set[str], set[str]]


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def analyze_group_overlaps(groups: list[dict[str, Any]]) -> list[GroupOverlap]:
    """对实际存在的组两两对比，返回 (组A, 组B, 重复点, A独有, B独有)。"""
    results: list[GroupOverlap] = []
    for group_a, group_b in combinations(groups, 2):
        points_a = set(group_a.get("data_points", []))
        points_b = set(group_b.get("data_points", []))
        results.append(
            (
                str(group_a.get("name", "")),
                str(group_b.get("name", "")),
                points_a & points_b,
                points_a - points_b,
                points_b - points_a,
            )
        )
    return results


def print_report(config: dict[str, Any]) -> None:
    groups = [g for g in config.get("groups", []) if isinstance(g, dict)]

    print("=== 数据组配置 ===")
    for group in groups:
        print(f"组名: {group.get('name')}")
        print(f"  触发方式: {group.get('trigger')}")
        print(f"  数据点: {group.get('data_points')}")
        print(f"  触发点: {group.get('trigger_point')}")
        print()

    print("=== 数据库配置 ===")
    print(f"数据组: {config.get('database', {}).get('data_groups')}")

    print("\n=== 详细分析 ===")
    for group in groups:
        print(f"{group.get('name')} 应该采集: {group.get('data_points')}")

    for name_a, name_b, shared, only_a, only_b in analyze_group_overlaps(groups):
        print(f"\n[{name_a} vs {name_b}]")
        print(f"重复的数据点: {shared}")
        print(f"{name_a} 独有: {only_a}")
        print(f"{name_b} 独有: {only_b}")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    config_path = Path(args[0]) if args else DEFAULT_CONFIG_PATH
    print_report(load_config(config_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
