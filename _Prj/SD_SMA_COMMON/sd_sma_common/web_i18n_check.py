"""公共前端底座(base.css / i18n.js / zh.js / en.js)与示范页迁移一致性自检。

离线运行: python -m sd_sma_common.web_i18n_check
校验:
1. zh.js / en.js 字典可解析且键一致、i18n.js 引擎接口齐全、base.css 含公共组件选择器
2. 查询页与 COMMON demo 页 data-i18n* / t('key') 引用的 key 都在字典里(防漏配)
3. 字典无死 key(每个 key 至少被一处引用,防堆积)
"""

import json
import re
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / "web"
REPO_ROOT = WEB_DIR.parents[3]
QUERY_STATIC = REPO_ROOT / "_Prj" / "SD_SMA_DATA_COLLECTOR_QUERY_WEB" / "app" / "static"
CACHE_VERSION = "20261021_01"


def load_dict(filename: str, marker: str) -> dict:
    text = (WEB_DIR / filename).read_text(encoding="utf-8")
    start = text.index(marker) + len(marker)
    payload = text[text.index("=", start) + 1 :].strip().rstrip(";").strip()
    return json.loads(re.sub(r"/\*.*?\*/", "", payload, flags=re.S))


def check_engine() -> None:
    i18n = (WEB_DIR / "i18n.js").read_text(encoding="utf-8")
    for fn in ("function t(", "function apply(", "function register(", "function setLang("):
        assert fn in i18n, f"i18n.js 缺少 {fn}"
    assert "DICT_WINDOW_KEY + String(lang).toUpperCase()" in i18n, "i18n.js 字典名须按大写语言码查找"
    assert "var LANGS = ['zh', 'en']" in i18n or 'var LANGS = ["zh", "en"]' in i18n, "i18n.js LANGS 须含 zh/en"
    base = (WEB_DIR / "base.css").read_text(encoding="utf-8")
    for sel in (".page-nav", ".layout", ".panel", ".row", "button", ".muted", ".hint", ":root"):
        assert sel in base, f"base.css 缺少公共选择器 {sel}"
    # 示范业务页确实引用了公共底座且版本号统一,防缓存旧脚本
    html = (QUERY_STATIC / "query.html").read_text(encoding="utf-8")
    assert f"/common/base.css?v={CACHE_VERSION}" in html, "query.html 未引用 base.css"
    assert f"/common/i18n.js?v={CACHE_VERSION}" in html, "query.html 未引用 i18n.js"
    assert f"/static/query.js?v={CACHE_VERSION}" in html, "query.js 版本号未更新"
    # COMMON 主题/多语言示范页
    demo = (WEB_DIR / "demo_theme_i18n.html").read_text(encoding="utf-8")
    assert f"base.css?v={CACHE_VERSION}" in demo, "demo 未引用 base.css"
    assert f"en.js?v={CACHE_VERSION}" in demo, "demo 未引用 en.js"
    assert f"i18n.js?v={CACHE_VERSION}" in demo, "demo 未引用 i18n.js"
    assert (WEB_DIR / "en.js").is_file(), "缺少 en.js"


def _scan_keys_in_text(text: str, used: set) -> None:
    for m in re.finditer(r'data-i18n(?:-placeholder|-aria)?="([^"]+)"', text):
        used.add(m.group(1))
    for m in re.finditer(r"\bt\(\s*'([^']+)'", text):
        used.add(m.group(1))
    for m in re.finditer(r'\.t\(\s*[\'"]([^\'"]+)[\'"]', text):
        used.add(m.group(1))


def collect_used_keys() -> set:
    used = set()
    for html in QUERY_STATIC.glob("*.html"):
        _scan_keys_in_text(html.read_text(encoding="utf-8"), used)
    for js in QUERY_STATIC.glob("*.js"):
        _scan_keys_in_text(js.read_text(encoding="utf-8"), used)
    for path in WEB_DIR.glob("demo_theme_i18n.*"):
        if path.suffix in {".html", ".js"}:
            _scan_keys_in_text(path.read_text(encoding="utf-8"), used)
    return used


def main() -> int:
    check_engine()
    zh = load_dict("zh.js", "window.SD_SMA_I18N_ZH")
    en = load_dict("en.js", "window.SD_SMA_I18N_EN")
    assert isinstance(zh, dict) and len(zh) >= 40, f"zh.js 字典异常小: {len(zh)} 条"
    assert isinstance(en, dict) and len(en) >= 40, f"en.js 字典异常小: {len(en)} 条"
    only_zh = sorted(set(zh) - set(en))
    only_en = sorted(set(en) - set(zh))
    assert not only_zh, f"en.js 缺少 key: {only_zh}"
    assert not only_en, f"en.js 多出 key: {only_en}"

    used = collect_used_keys()
    missing = sorted(k for k in used if k not in zh)
    assert not missing, f"引用了字典里不存在的 key: {missing}"
    dead = sorted(k for k in zh if k not in used)
    assert not dead, f"字典里无人引用的死 key: {dead}"

    # 示范页业务 JS 不应再残留写死的用户文案(检查已迁移的告警/提示路径)
    query_js = (QUERY_STATIC / "query.js").read_text(encoding="utf-8")
    for legacy in ("请选择批次号", "批次号来源：", "暂无可用 view", "开始时间不能大于结束时间"):
        assert legacy not in query_js, f"query.js 仍硬编码文案: {legacy}"

    print(
        f"web_i18n_check: OK — 字典 {len(zh)} 条(zh=en), 引用 {len(used)} 处, "
        f"LANGS=zh/en, demo 挂载正常"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
