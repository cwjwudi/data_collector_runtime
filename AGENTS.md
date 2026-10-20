# AGENTS.md — AI 协作入口

## 项目是什么

SD SMA Runtime：独立运行时工程。统一 Launcher 管理 4 个 FastAPI 服务，
从 B&R PLC 经 OPC UA 采集数据写入 MySQL/SQLite，并提供历史查询、数据库运维、报表归档。

## 入口与端口

| 用途 | 入口 |
|---|---|
| 启动（开发机） | `start.bat` → `_Launcher/sd_sma_launcher.py` |
| Launcher 配置 | `_Launcher/launcher_config.json`（服务/端口/健康检查/环境变量的唯一来源） |
| 构建 | `build.bat source`（便携包）/ `build.bat installer`（安装包）→ 输出 `_Build/` |

| 端口 | 进程 | ASGI 入口（cwd 相对仓库根） |
|---|---|---|
| 8090 | Launcher 管理台 | `_Launcher/launcher_web.py` |
| 8091 | SD SMA Collector | `_Prj/SD_SMA_DATA_COLLECTOR/web_config/main.py` → `web_config.main:app` |
| 8092 | SD SMA Query Web | `_Prj/SD_SMA_DATA_COLLECTOR_QUERY_WEB/app/main.py` → `app.main:app` |
| 8093 | SD SMA DB Admin | `_Prj/SD_SMA_DB_ADMIN/app/main.py` → `app.main:app` |
| 8094 | SD SMA Report Copy | `_Prj/SD_SMA_REPORT_COPY/app/main.py` → `app.main:app` |

采集器另有 CLI 入口：`_Prj/SD_SMA_DATA_COLLECTOR/main.py --config <json>`（不经 Web，直接跑采集）。

## 共享代码

跨服务代码只放 `_Prj/SD_SMA_COMMON/sd_sma_common/`（现有 `session_bridge` 单点登录、
`filesystem_browser` 白名单文件浏览）。各服务用 `sys.path` 注入 COMMON_ROOT 引用，
**不要复制粘贴副本**。

## 数据根目录 DATA_ROOT

默认 = 仓库根，可用环境变量 `SD_SMA_DATA_ROOT` 覆盖（Windows 服务安装时指向 ProgramData）。
`config/`、`logs/`、`backups/`、`secrets/`、`ImportBox/` 都在 DATA_ROOT 下。

## 目录红线

- `logs/`、`secrets/`、`backups/`、`ImportBox/`、`state/` 是运行时产物：**不提交、不作为代码依据**
- 仓库内配置必须脱敏；fernet key 已被 .gitignore 覆盖，真实密钥永不入库
- `__pycache__/`、`_Build/`、`.venv/`、`wheelhouse/` 为再生产物，不提交

## 冻结区（暂缓，只读）

- **`_Archive/`**：归档区。原 `_Docs/` 全部文档已归档至 `_Archive/_Docs/`，只读封存，
  不再更新；仅在用户明确要求时阅读引用。
  - `FDA21Cfr11/`：FDA 21 CFR Part 11 合规资料包（原文 PDF + 汇报报告）。
  - Part 11 计划类文档（Part 11 升级说明、合规性审查清单、三个月计划任务表）
    仅为规划存档。**不要据此实现任何 Part 11 功能**（审计上传、电子签名、身份传递等），
    相关开发与测试整体暂缓。

## 代码约定

- 修 bug 先 grep 所有调用方，在共享函数修根因，不在单个调用处贴补丁
- 非平凡逻辑留一个可运行自检（assert 式脚本，无框架，命名 `*_check.py`）。范例：
  `_Prj/SD_SMA_COMMON/sd_sma_common/session_bridge_check.py`
  （`python -m sd_sma_common.session_bridge_check` 离线 FakePlc 自检；`--live` 连真实 PLC）
- 详细架构与数据流见（已归档）`_Archive/_Docs/SD_SMA_Runtime 架构与数据流.md`

## AI 交付要求（DoD）

每次改动的交付必须满足，缺一不算完成：

1. **自检跑过并贴结果**：改动涉及 Python 代码时，交付前运行
   `python selfcheck.py`（仓库根，自动发现并运行所有 `*_check.py`），
   汇报中附上最后的 `x/y passed` 行；新增逻辑必须新增/更新对应 `*_check.py`。
2. **文档不散落**：不新建顶层 `.md`/`.html`；说明写进 commit message 或代码 docstring。
   确需成文，放 `_Prj/<服务>/docs/` 下并在汇报中说明理由。
3. **汇报三要素**：改了哪些文件、怎么验证的（自检输出/命令）、遗留什么（未覆盖项、
   已知边界）。没有验证项时写"仅静态检查"。
4. **提交规范**：commit message 一行中文，`<类型>: <摘要>`，类型取
   `feat / fix / refactor / docs / chore`；不动 `logs/`、`secrets/`、`backups/`、`state/`。
