"""公共前端底座与已迁移页面 i18n/主题一致性自检。

离线运行: python3 _Prj/SD_SMA_COMMON/sd_sma_common/web_i18n_check.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / "web"
REPO_ROOT = WEB_DIR.parents[3]
QUERY_STATIC = REPO_ROOT / "_Prj" / "SD_SMA_DATA_COLLECTOR_QUERY_WEB" / "app" / "static"
COLL_STATIC = REPO_ROOT / "_Prj" / "SD_SMA_DATA_COLLECTOR" / "web_config" / "static"
CACHE_VERSION = "20261021_03"

# Pages already on /common + i18n (expand as migration continues)
MIGRATED_HTML = [
    QUERY_STATIC / "query.html",
    QUERY_STATIC / "config.html",
    QUERY_STATIC / "index.html",
    WEB_DIR / "demo_theme_i18n.html",
    COLL_STATIC / "dashboard.html",
]
MIGRATED_JS = [
    QUERY_STATIC / "query.js",
    QUERY_STATIC / "config.js",
    WEB_DIR / "demo_theme_i18n.js",
    COLL_STATIC / "dashboard.js",
]


def load_dict(filename: str, marker: str) -> dict:
    text = (WEB_DIR / filename).read_text(encoding="utf-8")
    start = text.index(marker) + len(marker)
    payload = text[text.index("=", start) + 1 :].strip().rstrip(";").strip()
    return json.loads(re.sub(r"/\*.*?\*/", "", payload, flags=re.S))


def check_engine() -> None:
    i18n = (WEB_DIR / "i18n.js").read_text(encoding="utf-8")
    for fn in ("function t(", "function apply(", "function register(", "function setLang("):
        assert fn in i18n, f"i18n.js 缺少 {fn}"
    assert "DICT_WINDOW_KEY + String(lang).toUpperCase()" in i18n
    assert "var LANGS = ['zh', 'en']" in i18n or 'var LANGS = ["zh", "en"]' in i18n
    base = (WEB_DIR / "base.css").read_text(encoding="utf-8")
    for sel in (".page-nav", ".layout", ".panel", ".row", "button", ".muted", ".hint", ":root"):
        assert sel in base, f"base.css 缺少 {sel}"

    query = (QUERY_STATIC / "query.html").read_text(encoding="utf-8")
    assert f"/common/base.css?v={CACHE_VERSION}" in query
    assert f"/common/i18n.js?v={CACHE_VERSION}" in query
    assert f"/common/en.js?v={CACHE_VERSION}" in query
    assert f"/static/query.js?v={CACHE_VERSION}" in query

    config = (QUERY_STATIC / "config.html").read_text(encoding="utf-8")
    assert f"/common/base.css?v={CACHE_VERSION}" in config
    assert f"/common/i18n.js?v={CACHE_VERSION}" in config
    assert f"/static/config.js?v={CACHE_VERSION}" in config

    demo = (WEB_DIR / "demo_theme_i18n.html").read_text(encoding="utf-8")
    assert f"base.css?v={CACHE_VERSION}" in demo
    assert f"en.js?v={CACHE_VERSION}" in demo

    dash = (COLL_STATIC / "dashboard.html").read_text(encoding="utf-8")
    assert f"/common/base.css?v={CACHE_VERSION}" in dash
    assert f"/common/i18n.js?v={CACHE_VERSION}" in dash

    main = (REPO_ROOT / "_Prj" / "SD_SMA_DATA_COLLECTOR" / "web_config" / "main.py").read_text(
        encoding="utf-8"
    )
    assert 'mount("/common"' in main, "Collector 未挂载 /common"


def _scan_keys_in_text(text: str, used: set) -> None:
    for m in re.finditer(r'data-i18n(?:-placeholder|-aria)?="([^"]+)"', text):
        used.add(m.group(1))
    for m in re.finditer(r"\bt\(\s*'([^']+)'", text):
        used.add(m.group(1))
    for m in re.finditer(r'\.t\(\s*[\'"]([^\'"]+)[\'"]', text):
        used.add(m.group(1))


def collect_used_keys() -> set:
    used: set = set()
    for path in MIGRATED_HTML + MIGRATED_JS:
        assert path.is_file(), f"缺少已迁移文件 {path}"
        _scan_keys_in_text(path.read_text(encoding="utf-8"), used)
    return used


def main() -> int:
    check_engine()
    zh = load_dict("zh.js", "window.SD_SMA_I18N_ZH")
    en = load_dict("en.js", "window.SD_SMA_I18N_EN")
    assert len(zh) >= 40 and len(en) >= 40
    only_zh = sorted(set(zh) - set(en))
    only_en = sorted(set(en) - set(zh))
    assert not only_zh, f"en.js 缺少 key: {only_zh[:20]}..."
    assert not only_en, f"en.js 多出 key: {only_en[:20]}..."

    used = collect_used_keys()
    missing = sorted(k for k in used if k not in zh)
    assert not missing, f"引用了字典里不存在的 key: {missing[:30]}"
    dead = sorted(k for k in zh if k not in used)
    assert not dead, f"字典里无人引用的死 key ({len(dead)}): {dead[:40]}"

    query_js = (QUERY_STATIC / "query.js").read_text(encoding="utf-8")
    for legacy in ("请选择批次号", "批次号来源：", "暂无可用 view", "开始时间不能大于结束时间"):
        assert legacy not in query_js

    # Migrated pages should not keep duplicated base chrome selectors in page CSS
    q_css = (QUERY_STATIC / "styles.css").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\.page-nav \{", q_css) is None, "Query styles.css 仍含 .page-nav 副本"
    c_css = (COLL_STATIC / "styles.css").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\.page-nav \{", c_css) is None, "Collector styles.css 仍含 .page-nav 副本"

    print(
        f"web_i18n_check: OK — 字典 {len(zh)} 条(zh=en), 引用 {len(used)} 处, "
        f"已迁移页 {len(MIGRATED_HTML)}, LANGS=zh/en"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
