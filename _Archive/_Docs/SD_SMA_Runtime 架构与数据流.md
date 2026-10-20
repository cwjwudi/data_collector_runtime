# SD_SMA_Runtime 架构与数据流

> AI 与新人共用的架构说明。事实以代码为准，发现出入先改文档。
> 入口速查见根目录 `AGENTS.md`。

## 1. 总览

独立运行时工程：一个统一 Launcher 拉起并守护 4 个 FastAPI 服务（Uvicorn 承载）。
核心链路：B&R PLC --(OPC UA)--> Collector --(SQLite Outbox)--> MySQL/SQLite --> Query Web 查询展示。

```
浏览器 / mappView HMI
        │  HTTP 8090–8094（SessionBridge 签名 Cookie 单点登录）
        ▼
┌─────────────────────────────────────────────┐
│ SD SMA Launcher :8090（管理台 + 服务守护）      │
│  ├─ Collector   :8091  采集配置 + 采集运行时     │
│  ├─ Query Web   :8092  历史查询                │
│  ├─ DB Admin    :8093  数据库运维              │
│  └─ Report Copy :8094  报表归档                │
└─────────────────────────────────────────────┘
        │ OPC UA 4840              │ SQL
        ▼                          ▼
   B&R PLC / ARSim           MySQL / MariaDB
```

## 2. Launcher（`_Launcher/`）

- 入口 `sd_sma_launcher.py`，配置 `launcher_config.json`：服务清单、端口、健康检查路径、
  注入各服务的环境变量，**全部以此文件为准**。
- 关键机制（各属一个模块）：
  - `launcher_supervisor.py`：进程守护、健康检查、异常退避重启
  - `launcher_security.py`：DPAPI + PBKDF2 凭据保护、管理会话令牌
  - `launcher_settings.py`：监听模式切换（全局/本机）、防火墙规则
  - `launcher_imports.py`：现场配置包（`ImportBox/`）校验、应用与密钥剥离
  - `launcher_web.py`：管理台 Web（服务启停/日志/凭证/文件浏览）
  - `resource_monitor.py`：CPU/内存采样与阈值告警（默认关闭）
- **DATA_ROOT 解析**：`SD_SMA_DATA_ROOT` 环境变量优先，否则 = 包根目录。
  源码运行 = 仓库根；Windows 服务安装 = ProgramData。`config/`、`logs/`、`backups/`、
  `secrets/`、`ImportBox/` 均在 DATA_ROOT 下，源码树中的这些目录只是种子模板。
- 启动时把 `_Prj/SD_SMA_COMMON` 注入 `sys.path`，各服务同样自行注入。

## 3. 四个服务

### 3.1 Collector 采集服务 :8091（`_Prj/SD_SMA_DATA_COLLECTOR/`）

- Web 入口 `web_config/main.py`：采集配置 UI（`/dashboard`）、OPC UA 节点在线浏览、
  采集运行时启停、配置校验/导入导出（密码字段脱敏）。
- CLI 入口 `main.py --config <json>`：不经 Web 直接跑采集，核心在 `runtime/collector_runtime.py`。
- 内部引擎分层：
  - `communication/`：`communication_manager`（多连接编排）、`opcua_client`（会话/重连）、
    `heartbeat_manager`（心跳写 PLC）、`opcua_feedback_writer`（入库结果回写 PLC）、
    `http_client`（远程 HTTP 上报）
  - `core/`：`data_collector`（采集主控：周期/变化触发、节拍对齐、漏采补偿）、
    `config_loader` + `config_models`（配置装载校验）、`secret_store`（密钥存储）
  - `database/`：`data_storage`（批量写入、自动建表/补列/建索引、分区轮转）、
    `db_manager`（MySQL/SQLite 连接池与健康检查）、
    `persistent_queue/`（SQLite Outbox：认领租约、重试状态机、死信重放、崩溃恢复）
- 配置目录 env：`SD_SMA_COLLECTOR_CONFIG_DIR`（Launcher 注入 `${DATA_ROOT}/config/collector`）。

### 3.2 Query Web 历史查询服务 :8092（`_Prj/SD_SMA_DATA_COLLECTOR_QUERY_WEB/`）

- 入口 `app/main.py`：历史/批次数据查询（`/query`），查询视图/查询表/分组基线可配置，
  插件化查询 + 快照缓存 + 游标翻页。
- 特色模块：`opcua_writeback`（查询结果回写 PLC）、`table_list_writeback`（批次数据表清单回写）、
  `table_partition`（分区表分组）、`plugin_opcua_monitor`（插件运行监视）。
- 配置目录 env：`SD_SMA_QUERY_WEB_CONFIG_DIR`。

### 3.3 DB Admin 数据库运维服务 :8093（`_Prj/SD_SMA_DB_ADMIN/`）

- 入口 `app/main.py`：连接测试、库表结构浏览、mysqldump 整库/单表备份还原、
  CSV 导入导出、异步作业与取消、敏感操作二次确认（`/admin`）。
- 连接凭据用 Fernet 加密存储（key 文件被 .gitignore 排除）。
- 配置目录 env：`SD_SMA_DB_ADMIN_CONFIG_DIR`；备份目录 env：`SD_SMA_DB_ADMIN_BACKUP_DIR`
  （默认 `${DATA_ROOT}/backups`）。

### 3.4 Report Copy 报表归档服务 :8094（`_Prj/SD_SMA_REPORT_COPY/`）

- 入口 `app/main.py`：报表源目录枚举与勾选、目标盘/目录选择（含可移动介质，ctypes 枚举盘符）、
  分块复制 + 进度速率跟踪、扩展名白名单与路径安全校验。
- 配置目录 env：`SD_SMA_REPORT_COPY_CONFIG_DIR`。

## 4. 共享包 SD_SMA_COMMON（`_Prj/SD_SMA_COMMON/sd_sma_common/`）

- `session_bridge.py`：UserBridge 单点登录。一次性 `?sdsma_code` 经 OPC UA 换发
  主机级签名 Cookie（`sdsma_session`，8090–8094 共享）；PLC 侧 seq 即撤销信号，无 TTL。
  机器间调用走 `X-SD-SMA-Token` 头。四个服务均 `install_session_middleware` 安装，
  `/api/health` 豁免。设计见 `_Docs/UserBridge 开发计划.md`（P3）。
- `filesystem_browser.py`：白名单根目录受限的文件浏览（DB Admin / Report Copy 用）。
- `session_bridge_check.py`：离线自检（FakePlc），共享逻辑改动的验收底线。

## 5. 数据流

### 5.1 采集写入链路（Collector 进程内）

```
OPC UA 节点
  → CommunicationManager / OpcUaClient（会话、断线重连）
  → DataCollector（周期/变化触发、节拍对齐、瞬态重试）
  → PersistentQueueStore（先落 SQLite，崩溃可恢复）
  → DataStorageProcessor（批量写、自动建表/补列/索引、分区轮转）
  → MySQL 明细表 + 批次主表
  → OpcUaFeedbackWriter 把入库结果回写 PLC 确认
```

失败补偿：写入失败指数退避重投，超上限入死信队列，支持人工重放。

### 5.2 查询链路

Query Web 按视图配置读 MySQL → 页面展示/CSV 导出；
查询完成后可选 `opcua_writeback` 回写结果到 PLC、`table_list_writeback` 回写批次表清单。

### 5.3 运维链路

DB Admin → mysqldump 备份到 `backups/`（或还原）；CSV 导出/导入。
DB Admin 不经过 Collector，直连数据库。

### 5.4 归档链路

Report Copy：报表源目录（默认 `D:/SMA_Report`，可配）→ 勾选 → 复制到目标盘/移动介质，
全程扩展名白名单 + 路径白名单校验。

### 5.5 登录链路

```
mappView/浏览器带 ?sdsma_code 访问任意 8090–8094
  → SessionBridge 拿 code 经 OPC UA 找 PLC 换 (slot, seq, user, roles)
  → 签发主机级签名 Cookie，五端口通用
  → 后续请求校验 Cookie + PLC seq（seq 变即失效，等于 PLC 侧踢人）
```

## 6. 配置与密钥约定

- 仓库内 `config/` 四个子目录是**脱敏模板**；现场真实配置在 DATA_ROOT，不进仓库。
- 各类 key/secret：fernet key（`config/**/.sd_sma_*_fernet.key`）、`secrets/session.secret`
  均被 .gitignore 排除；`launcher_imports` 导入现场配置包时会剥离密钥。
- 配置导出一律脱敏（Collector 导出、Launcher 配置包）。

## 7. 与 B&R 侧的关系

`ar_test/` 是 B&R Automation Studio 测试工程（ARSim 或真实 PLC），OPC UA Server
在 `opc.tcp://<host>:4840`，内含 UserRoleSystem（SessionBridge 的 `sdsma` 用户在此定义）。
它是本运行时的**测试对端**，由 Automation Studio 维护，不在本仓库的构建/发布范围内。

## 8. 冻结与暂缓

- `FDA21Cfr11/`：Part 11 合规资料包，整体冻结（只读）。
- Part 11 相关功能（审计上传、电子签名、身份传递 R1–R3）暂缓开发，
  `_Docs/` 中的计划类文档仅存档，不作为开发依据。

## 9. 构建与分发

- `build.bat source` → `_Build/SD_SMA_Runtime_Package`：源码便携包，整目录拷到目标机跑 `start.bat`。
- `build.bat installer` → `_Build/SD_SMA_Installer_Package`：轻量安装包
  （一个 Launcher EXE + 共享 Python 运行时，Nuitka 编译，可选 Windows 服务/Inno Setup）。
- 构建脚本在 `_Launcher/scripts/`（PowerShell）。`_Build/`、`wheelhouse/` 不入库。
