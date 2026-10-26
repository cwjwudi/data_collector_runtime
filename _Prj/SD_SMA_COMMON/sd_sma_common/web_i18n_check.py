"""公共前端底座(base.css / i18n.js / zh.js)与示范页迁移一致性自检。

离线运行: python -m sd_sma_common.web_i18n_check
校验:
1. zh.js 字典可解析、i18n.js 引擎接口齐全、base.css 含公共组件选择器
2. 各服务页面 data-i18n* 引用的 key 都在字典里(防漏配)
3. 业务 JS 里 t('key') 引用的 key 都在字典里
4. 字典无死 key(每个 key 至少被一处引用,防堆积)
"""

import ast
import json
import re
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / "web"
REPO_ROOT = WEB_DIR.parents[3]
QUERY_STATIC = REPO_ROOT / "_Prj" / "SD_SMA_DATA_COLLECTOR_QUERY_WEB" / "app" / "static"
CACHE_VERSION = "20261020_03"


def load_dict() -> dict:
    text = (WEB_DIR / "zh.js").read_text(encoding="utf-8")
    marker = "window.SD_SMA_I18N_ZH"
    start = text.index(marker) + len(marker)
    payload = text[text.index("=", start) + 1 :].strip().rstrip(";").strip()
    return json.loads(re.sub(r"/\*.*?\*/", "", payload, flags=re.S))


def check_engine() -> None:
    i18n = (WEB_DIR / "i18n.js").read_text(encoding="utf-8")
    for fn in ("function t(", "function apply(", "function register(", "function setLang("):
        assert fn in i18n, f"i18n.js 缺少 {fn}"
    assert "DICT_WINDOW_KEY + String(lang).toUpperCase()" in i18n, "i18n.js 字典名须按大写语言码查找"
    base = (WEB_DIR / "base.css").read_text(encoding="utf-8")
    for sel in (".page-nav", ".layout", ".panel", ".row", "button", ".muted", ".hint", ":root"):
        assert sel in base, f"base.css 缺少公共选择器 {sel}"
    # 示范页引用公共底座；业务脚本独立递增缓存版本。
    html = (QUERY_STATIC / "query.html").read_text(encoding="utf-8")
    assert f"/common/base.css?v={CACHE_VERSION}" in html, "query.html 未引用 base.css"
    assert f"/common/i18n.js?v={CACHE_VERSION}" in html, "query.html 未引用 i18n.js"
    assert "/static/query.js?v=20261026_01" in html, "query.js 版本号未更新"


def check_collector_assets() -> None:
    """执行真实挂载语句，避免导入采集器触发运行时配置或连接 PLC。"""
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient

    root = REPO_ROOT / "_Prj" / "SD_SMA_DATA_COLLECTOR" / "web_config"
    tree = ast.parse((root / "main.py").read_text(encoding="utf-8"))
    mounts = [node for node in tree.body if isinstance(node, ast.Expr)
              and isinstance(node.value, ast.Call)
              and ast.unparse(node.value.func) == "app.mount"
              and node.value.args and isinstance(node.value.args[0], ast.Constant)
              and node.value.args[0].value == "/common"]
    assert len(mounts) == 1, "采集服务必须挂载 /common"
    app = FastAPI()
    exec(compile(ast.Module(body=mounts, type_ignores=[]), str(root / "main.py"), "exec"),
         {"app": app, "StaticFiles": StaticFiles, "COMMON_ROOT": WEB_DIR.parent.parent})
    html = (root / "static" / "config.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script src="([^"]+)"', html)
    assert scripts.index(next(s for s in scripts if s.startswith("/common/zh.js?"))) < scripts.index(
        next(s for s in scripts if s.startswith("/common/i18n.js?"))) < scripts.index(
        next(s for s in scripts if s.startswith("/static/config.js?"))), "语言脚本须先于配置脚本加载"
    with TestClient(app) as client:
        for src in scripts:
            if src.startswith("/common/"):
                response = client.get(src)
                assert response.status_code == 200, f"{src}: {response.status_code}"
                assert "javascript" in response.headers["content-type"], src


def collect_used_keys() -> set:
    used = set()
    for html in [*QUERY_STATIC.glob("*.html"), * (REPO_ROOT / "_Launcher/static").glob("*.html")]:
        for m in re.finditer(r'data-i18n(?:-placeholder|-aria)?="([^"]+)"', html.read_text(encoding="utf-8")):
            used.add(m.group(1))
    for js in [*QUERY_STATIC.glob("*.js"), REPO_ROOT / "_Launcher/static/app.js", REPO_ROOT / "_Prj/SD_SMA_DATA_COLLECTOR/web_config/static/config.js"]:
        for m in re.finditer(r"\bt\(\s*'([^']+)'", js.read_text(encoding="utf-8")):
            used.add(m.group(1))
    return used


def main() -> int:
    check_engine()
    check_collector_assets()
    zh = load_dict()
    assert isinstance(zh, dict) and len(zh) >= 40, f"zh.js 字典异常小: {len(zh)} 条"

    used = collect_used_keys()
    missing = sorted(k for k in used if k not in zh)
    assert not missing, f"引用了字典里不存在的 key: {missing}"
    dead = sorted(k for k in zh if k not in used)
    assert not dead, f"字典里无人引用的死 key: {dead}"

    # 示范页业务 JS 不应再残留写死的用户文案(检查已迁移的告警/提示路径)
    query_js = (QUERY_STATIC / "query.js").read_text(encoding="utf-8")
    for legacy in ("请选择批次号", "批次号来源：", "暂无可用 view", "开始时间不能大于结束时间"):
        assert legacy not in query_js, f"query.js 仍硬编码文案: {legacy}"

    print(f"web_i18n_check: OK — 字典 {len(zh)} 条, 引用 {len(used)} 处, 底座挂载正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
