# SD SMA 管理中心数据库统一管理升级计划与 TODO

日期：2026-11-05。状态：P0—P5 本机实现与验证已完成；P6 本机构建/smoke 已通过，最终虚拟机发布验收及发布材料确认仍暂缓。适用工作树：`data_collector_runtime_managed`。

本文放在 DB Admin 的 `docs/`，作为跨 Launcher、采集、查询和数据库运维的持续交接入口，方便后续 AI 按阶段执行。现有托管版实现与历史验证见 [managed_mariadb_handoff.md](managed_mariadb_handoff.md)；其中已勾选事项不代表本方案已完成。

## 1. 目标与首版范围

用户在 **SD SMA 管理中心（8090）→ 数据库管理** 选择：

| 模式 | 用户操作 | 产品行为 |
| --- | --- | --- |
| 内置数据库 | 选择“使用 SD SMA 内置数据库”并应用 | 复用随安装包部署的 MariaDB、业务库和各服务账号；不要求用户填写内置密码 |
| 外部数据库 | 填写地址、端口、库名、用户名和密码，测试后应用 | 采集、查询、DB Admin 统一使用该外部业务库；连接信息由 Launcher 下发 |

“内置数据库”在本文指现有托管 MariaDB，不是另外引入 SQLite 产品模式。现有 SQLite 代码兼容性保留，首版不做 SQLite/MySQL 数据转换。外部首版以 MySQL、MariaDB 为目标，准确支持版本必须经过真实测试后列出，不承诺所有兼容协议的数据库都可用。

首版包括：模式选择、外部连接档案、连接与权限诊断、统一应用、状态查看、既有 DB Admin 运维入口、失败回退，以及安装后重启仍保持选择。数据库列表、表、备份、恢复和 CSV 操作继续复用 DB Admin，从管理中心直接进入相应页面；首版不在 8090 复制第二套运维界面。

首版不包括：自动迁移历史数据、自动合并已有库、自动创建外部用户和授权、远程数据库服务启停、数据库内核自动升级、主从/高可用、通用 SQL 控制台，以及 Part 11 功能。此处“后续升级”指应用功能升级，不包括 MariaDB 引擎升级。

## 2. 当前代码基线（实施前必须复核）

本轮只阅读源码和既有交接文档，没有连接真实数据库，也没有把历史交接记录当成本轮测试结果。

| 已有能力 | 代码入口 | 实施时的用法或缺口 |
| --- | --- | --- |
| 内置 MariaDB 初始化、随机账号、DPAPI 凭据 | `_Launcher/managed_database.py` | 复用 `ManagedDatabase`；缺文件/凭据时禁止重建已有实例 |
| 中央凭据与服务分配 | `_Launcher/launcher_security.py` | 复用 `LauncherSecurityStore`；当前允许三个服务分配到不同连接 |
| 中央凭据接口与连接测试 | `_Launcher/launcher_web.py` | 现有测试只证明连接成功；保存/分配后会重启相关运行服务，没有整体应用流程 |
| 服务启动时注入连接环境变量 | `_Launcher/sd_sma_launcher.py` | 复用 `service_with_launcher_credential()`；需补齐模式解析、应用和启动失败处理 |
| 管理中心页面 | `_Launcher/static/index.html`、`app.js`、`styles.css` | 当前入口叫“数据库密码”；内置可用时，JS 仍把空分配显示为“不使用中央凭据”，需要纠正 |
| 数据库列表、表、备份恢复、CSV、任务与容量状态 | `_Prj/SD_SMA_DB_ADMIN/app/main.py`、`app/static/` | 复用现有实现；`/api/storage/status` 当前主要面向内置模式 |
| 采集与查询连接读取 | 采集 `core/config_loader.py`、查询 `app/config_manager.py` | 已读取 Launcher 注入字段；各页面、公开配置和实际连接应保持一致 |
| 待写数据持久化 | 采集 `database/persistent_queue/queue_store.py`、`database/data_storage.py` | 队列可在重启后继续处理，切库前必须处理旧任务，不能直接换连接重启 |
| 安装与服务启动 | `_Launcher/installer/`、`_Launcher/scripts/build_installer_package.ps1` | 当前主服务 XML 依赖 `SD_SMA_DB`，手动入口也先启动内置库；外部模式需解除这种强制依赖 |

本轮开始时工作树已有未提交修改，包含采集/查询配置页面、公共文案及新增的 `sd_sma_common/database_settings.py`、`database_settings_check.py`。这些不是本文实现的内容；下一位 AI 必须先看 `git status` 和实际 diff，保留已有工作，不能凭本文覆盖或重做。

### 2.1 凭据事实与 root 的处理

- 外部表单支持用户提供 root 和密码，也支持权限足够的专用账号；不把用户名必须等于 `root` 写成校验条件。能否执行某项操作以实际权限为准。
- root 适合管理员配置和需要的管理操作；长期运行更建议使用业务库范围的专用账号。MySQL 区分系统管理账号与普通业务账号，参见 [官方账号分类](https://dev.mysql.com/doc/refman/8.4/en/account-categories.html)。首版沿用已有档案机制，不顺带实现自动建号/授权系统。
- 当前内置凭据由 `ManagedDatabase` 使用机器 DPAPI 保护；外部档案的 `LauncherSecurityStore.upsert_credential()` 则把密码明文写入 `DATA_ROOT/secrets/launcher_security.json`，公开接口隐藏密码。**不能把“位于 secrets 目录”或“API 不返回”写成“已加密存储”。**
- 本方案不默认改写整套密码存储策略，也不把外部 root 密码复制到三个服务配置。实施时保留当前存储语义并如实说明本地文件可读者的权限影响；若另行要求外部凭据加密，再单独设计兼容迁移和换机行为。
- 编辑档案时空密码表示保留已有密码；新增外部档案要求提供用户名、密码及连接参数。页面不回填密码、不写 localStorage，不在日志、错误详情、导出配置或命令行输出真实密码。
- 远程账号必须允许从运行 SD SMA 的主机登录；不能为方便而自动开放远程 root、修改外部防火墙或降低认证要求。认证插件、TLS 要求须在预检中识别；不能以关闭服务器安全配置作为兼容方案。

## 3. 配置和职责设计

### 3.1 配置只维护一份

1. `_Launcher/launcher_config.json` 继续作为服务、监听端口、健康检查和环境变量模板的来源；其中 `managed_database` 保存内置数据库能力及初始化参数，不能简单用 `enabled=false` 代表临时切到外部，否则会影响已有安装识别与后续切回。
2. 建议在现有 `LauncherSecurityStore` 的持久化数据中增加一个简短的 `database_binding`：`mode` 为 `managed` 或 `external`，外部模式引用 `external_credential_id`。只存选择和档案引用，密码继续存在已有凭据档案里；最终字段名可结合当前代码调整。
3. 三个服务的有效连接统一由该选择推导：内置模式仍用各自账号；外部首版可复用一个已提供的档案。不要把内置三账号合并为 root，也不要要求外部必须提供三套账号。
4. 原有 `assignments` 只作旧配置兼容；新的统一模式不能再由三个独立下拉框分别改变。首次显式应用时，在同一次保存中写统一选择并处理旧分配，避免存在两套相互覆盖的有效配置。
5. 旧版本没有 `database_binding` 时保持原有解析行为。若旧分配恰好对应同一外部档案或全部使用内置，可展示识别结果；旧配置混用多个库时标成“旧版按服务配置”，列出差异，等待用户主动统一，不能静默改库。
6. 新配置引用丢失、损坏或外部不可达时显示错误，不自动退回内置或各服务本地配置。未选中内置模式时，不应为了加载其凭据而阻止管理中心启动。

不新建通用配置框架。运行时模式解析和整体应用编排留在 Launcher；真正跨服务复用的数据处理只放 `sd_sma_common/`，优先复用工作树已有的 `database_settings.py`。

### 3.2 管理界面

- 把“数据库密码”入口整合为“数据库管理”：当前模式、目标服务器与库、连接结果、版本、三个服务的实际连接来源，以及进入 DB Admin 的按钮。
- 内置模式显示是否安装、是否可用、版本、业务库占用和本机剩余磁盘；源码/便携环境缺少内置组件时显示“当前环境未安装”，不能伪装可用或自动下载注册服务。
- 外部模式显示连接表单，提供“测试连接”和“应用配置”；测试不保存、不重启、不建库。库不存在和权限不足应分别显示。
- 外部容量只显示可查询到的业务库占用；不要把 SD SMA 本机磁盘剩余量当成远程数据库服务器磁盘余量。
- 应用前明确显示旧库、新库、受影响服务及“历史数据不会自动迁移”；保存草稿/连接测试成功都不能显示为切换完成。
- 管理中心直达 DB Admin 时复用现有会话机制，不把数据库密码放入 URL。运维页显示当前实际目标，破坏性操作继续使用既有确认机制。
- 修改到的页面按公共 `base.css`、`i18n.js`、`zh.js` 规范迁移，服务专属样式保留；不借此重写管理中心其他页面。新文案统一 i18n，静态引用更新版本号。

### 3.3 API 最小增量

优先扩展现有凭据 CRUD、测试接口，保留已有调用兼容。预计只需补充“统一状态/选择”和“整体应用”接口；以下为建议，不是已经存在的路由：

| 建议接口 | 职责 |
| --- | --- |
| `GET /api/launcher/database` | 返回持久化选择、实际服务目标及脱敏状态，区分内置能力与当前模式 |
| `POST /api/launcher/database/test` | 接受未保存的连接参数或档案引用，测试连接、版本、目标库与能力；不修改有效配置 |
| `POST /api/launcher/database/apply` | 统一检查、切换三个服务并返回逐服务结果；中途失败返回实际状态与回退结果 |

新接口沿用现有管理权限、Origin 检查和会话行为，校验在入口完成。不要新增一套登录系统，也不要默认改变 PIN 策略。删除正在生效的外部档案应拒绝，并提示先切换；编辑正在生效的档案也必须经过同样的整体应用流程，不能绕过它直接触发逐服务重启。

## 4. 切换和数据保护流程

### 4.1 先准备，再统一应用

1. 使用候选连接做预检：连接、版本、库存在性、认证/TLS 条件、必要能力及客户端工具匹配。预检失败只返回诊断，保持当前配置和进程不变。不要用 `SELECT 1` 宣称完整备份恢复权限已验证。
2. 外部业务库不存在时，提供单独的“创建业务库”动作或明确建库指引。若实现按钮，仅在用户明确操作且账号具备权限时执行；禁止测试连接顺带建库、修改账号或授权。
3. 用户确认应用后，记录旧选择与三个服务原先的运行状态；暂停采集的新任务生产，把内存缓冲及旧库待写数据处理完。存在 `pending/processing/retry/dead_letter` 或未完成写入时阻止切换并显示原因，不清空队列，也不把旧任务发送到新库。
4. 等待当前备份、恢复、导入等数据库任务结束后再切换；首版忙碌时直接拒绝应用，避免中途杀掉恢复或导出任务。需要一个普通的应用互斥以防重复点击，不引入分布式锁或通用任务引擎。
5. 关闭受影响服务的旧连接后，一次性保存统一选择并按新配置启动原本运行的服务。Report Copy 不参与换库；原本停止的服务保持停止。不能在采集器仍向旧库写入时先让查询指向新库。
6. 检查三个服务应运行者的进程、实际数据库连接及目标一致性。当前部分健康地址只是 HTML 页面，不能仅凭 HTTP 200 宣称数据库切换成功。采集运行态需单独确认，不能只验证 8091 Web 进程。
7. 新目标在验证完成前保持采集暂停；成功后按原采集状态恢复，返回逐服务实际结果。若失败，恢复旧选择并按旧配置恢复原运行状态；旧库也不可用时停止受影响服务并显示回退失败，保持 8090 可访问，不把混用状态显示为成功。

优先复用现有 CollectorHost 停止/启动、存储排空、任务状态和 Supervisor 能力；缺少跨进程状态时增加小范围内部接口，并沿用现有访问控制。不得假设 `stop_processing()` 调用结束就一定写完，必须检查实际队列/缓冲结果。禁用持久化队列时同样检查内存未写任务。

首版不支持带未处理旧任务强制换库。旧库已经断线且有积压时，保留任务并提示恢复旧库后排空；“给每条任务绑定目标并跨库回放”是另一个需求，不在本次实现。

### 4.2 切换不等于历史迁移

- 切到空库后历史查询为空属于预期，不是自动丢数据；旧数据仍留在旧库，页面必须提前说明。
- 首版采用可控停机：暂停采集并确认排空 → 备份旧业务库 → 恢复到兼容的目标业务库 → 校验 → 应用目标连接 → 恢复采集。现有统一连接注入会覆盖 DB Admin 的目标，实施前需明确目标恢复方式；首版允许通过目标端配套客户端/DBA 离线恢复，不为迁移绕开统一连接约束。
- 有写入后切回旧库不会同步期间新增数据；连接回退只恢复选择和进程，不能撤销数据库里已经发生的 DDL 或写入。不得用删表删库模拟回滚。
- MariaDB 与 MySQL 的导出文件和客户端存在兼容限制，必须按服务端、导出工具、恢复工具的组合实测。MariaDB 官方明确说明部分 dump 文件的 sandbox 指令不被 MySQL 客户端解释，见 [mariadb-dump 文档](https://mariadb.com/docs/server/clients-and-utilities/backup-restore-and-import-clients/mariadb-dump)。不承诺跨引擎一键迁移。
- 备份期间避免采集建表、补列等并发 DDL；大库停机时长、视图/过程/事件/触发器完整性须另行验证，不能由小型业务表自检推导。

## 5. 分阶段 TODO（每阶段可单独交给 AI）

下列框按实际验证更新；每阶段完成后填写日期、改动文件、验证命令、实际输出和遗留项，不凭计划文字勾选。

### P0：复核并固定兼容规则

- [x] 阅读 `AGENTS.md`、本文、原托管版交接文档，核对分支和现有未提交修改。
- [x] 列出 `credential_for_service()`、`service_with_launcher_credential()`、`create_management_app()`、配置读取和数据库连接函数的全部调用者、自检、安装脚本及打包复制清单。
- [x] 核对现有纯 CLI/源码启动无 Launcher 凭据时的行为，保留已约定的支持边界，不重新引入服务页面保存密码。
- [x] 记录旧配置三种基线：内置默认、同一外部档案、混合服务分配；确定只在用户主动应用时转换。
- [x] 用项目 `.venv` 跑基线自检，记录实际 `x/y passed`；失败先识别既有问题，不覆盖别人修改。

2026-11-05 完成；调用范围、兼容表、回归实现与实际结果见第 9 节。P0 交付时下一阶段为 P1；后续连续实施结果见第 10 节。

完成条件：下一阶段有明确字段、优先级、旧配置兼容表和真实基线结果。

### P1：统一选择和凭据解析

- [x] 在 `launcher_security.py` 实现单一有效选择及旧配置兼容；原子保存引用与必要分配变更。
- [x] 在 `sd_sma_launcher.py` 根据选择推导三个服务连接，保留内置分账号，不向 Report Copy 注入数据库凭据。
- [x] 明确禁止丢失引用时静默降级；有效档案的编辑/删除不能绕过整体应用。
- [x] 更新 `_Launcher/launcher_auth_check.py`、`managed_database_check.py`；必要时新增一个聚焦的 `database_binding_check.py`，覆盖选择、旧配置、公开脱敏和错误引用。

完成条件：不改 UI 也能验证三个服务指向相同目标；旧安装未主动应用前行为保持兼容。

### P2：启动链路支持外部模式

- [x] 修改 Launcher 初始化顺序：先判定选择，外部模式不依赖内置凭据文件、3307 端口或数据库服务；管理中心可在数据库故障时启动并用于修复配置。
- [x] 调整 `Start-SD_SMA.ps1`、`Manage-SD_SMA-Database.ps1` 和 `SD_SMA_Service.xml`：主服务不再硬依赖 `SD_SMA_DB`；内置模式的启动和就绪检查由现有入口按需完成。
- [x] 核对 `Install-SD_SMA-Service.ps1` 的实际 Windows 服务注册/刷新，不能只改 XML 而漏掉已安装服务的依赖。
- [x] 切换不自动删除、重新初始化或停止已存在内置实例；首版允许内置服务保留运行，节省其资源的启停策略后续单独处理。
- [x] 在覆盖安装及构建中保留用户选择和凭据，包内默认配置不覆盖 DATA_ROOT 的已选模式；不为此增加“无内置组件”的第二套安装包。
- [x] 更新 `installer_mode_check.py`、`installer_build_check.py` 及相关 Launcher 自检，覆盖外部选择下内置服务缺失/不可用仍能启动管理中心的路径。

完成条件：手动和 Windows 服务启动均遵守选择；内置故障不会封死外部模式或管理中心。

### P3：预检与整体应用

- [x] 在 `launcher_web.py` 增加最小状态、候选测试与应用接口，复用凭据 CRUD 和权限检查。
- [x] 按第 4 节串联 `launcher_supervisor.py`、CollectorHost 和 DB Admin 任务状态；补齐暂停、排空、目标确认所需的最小接口。
- [x] 验证读取、采集写入/建表补列、查询和运维所需能力；测试连接不写业务表，真实能力测试仅在独立测试库进行。缺权限返回具体受限操作，不自动扩大授权。
- [x] 给库创建单独入口或指引；用户只配置已有库时不强制要求建库权限。
- [x] 实现应用失败回退并报告逐服务结果；原本停止的服务及采集状态不能被意外开启。
- [x] 用真实解析、持久化和应用实现写 assert 自检；仅进程/外部连接故障注入使用必要的替身。覆盖队列未排空、任务忙碌、候选失败、启动失败与回退失败。

完成条件：无错误目标写入、无隐式清队列、无半成功被报告成整体成功；候选失败不影响当前运行。

### P4：管理中心与服务页面一致

- [x] 在管理中心完成模式选择、外部表单、预检结果、整体应用结果、故障恢复入口和 DB Admin 直达按钮。
- [x] 迁移修改到的页面公共样式与中文文案，更新 `sd_sma_common/web/zh.js` 和缓存版本。
- [x] 检查采集 `web_config/static/config.html`、`config.js`，查询 `app/static/config.html`、`config.js` 和两边配置读写，统一显示 Launcher 管理的实际目标；保留工作树已有用户名/密码集中化改动。
- [x] 更新 DB Admin `app/main.py`、`app/static/config.*`、`storage.js` 等实际受影响位置，区分外部数据库占用与本机磁盘，避免第二套可覆盖统一选择的连接配置入口。
- [x] 更新已有 `connection_config_check.py`、`database_settings_check.py`、`web_i18n_check.py` 等对应检查，覆盖不回填密码、未保存测试、失败按钮恢复和连接来源显示。

完成条件：用户可在 8090 选择并应用数据库、直接进入既有运维页面，各服务显示的目标与实际连接一致。

### P5：真实数据库与切换验收

- [x] 使用独立临时库验证内置 MariaDB、至少一个明确版本的外部 MySQL 和一个外部 MariaDB；记录服务端/驱动/客户端工具版本，不连接生产库测试。
- [x] 复用并扩展 `managed_mariadb_check.py` 和 `managed_database_check.py` 的真实模式，验证采集建表补列、中文/特殊字符写入、历史分页查询、字典表及备份恢复。
- [x] 验证 root 可配置、权限足够的非 root 可配置、只读账号诊断受限；错误密码、不可达、库不存在、TLS/认证插件不兼容均有明确结果。
- [x] 验证内置→外部、外部→内置、外部 A→外部 B、同目标换密码，以及配置保存后 Launcher 重启。
- [x] 验证有队列积压/内存缓冲/死信时拒绝切库，有备份恢复任务时拒绝切库；已暂停与正在采集两种状态分别验证。
- [x] 验证失败回退、删除生效档案、旧版混合分配和缺内置凭据时管理中心仍可访问；不同目标放可区分数据，以实际读写证明没有串库。
- [x] 验证旧库历史保留、切到空库有明确提示、切回不会声称自动合并；未声明支持的跨引擎恢复不计为通过。

完成条件：形成实际验证矩阵，标明通过、失败、未执行及原因；自检默认跳过真实数据库的条目不能算真实数据库验收通过。

### P6：构建与发布前验收

- [x] 检查 `_Launcher/scripts/build_installer_package.ps1` 的模块复制清单、默认模式、内置组件和工具打包；沿用现有 `build.bat`，不手改 `_Build` 产物。
- [x] 跑完整自检和 i18n 检查，按改动跑安装包构建及暂存 smoke；记录实际结果。
- [ ] 待用户提供并允许使用干净 Windows 虚拟机后，验证离线新装、手动/服务启动、重启、覆盖安装保持外部选择、卸载保留数据、恢复内置选择与业务备份恢复。
- [ ] 与原交接文档的最终发布验收一起确认许可证、客户端匹配、实际权限及数据保留；未经要求不发布、不推送、不打标签。

完成条件：构建检查与真实安装验收分别有记录。原文档注明最终虚拟机验收暂缓，本文不是解除暂缓的授权。

## 6. 后续 AI 的执行约束与命令

原计划按单阶段交付；用户于本轮明确要求继续 P1 及所有后续阶段，故连续实施并逐项验证。最终虚拟机验收暂缓仍有效。共享函数修改前搜索所有调用方。只修改本阶段必要代码，不扩展到 PLC 通讯、Part 11 或数据库新内核版本。新增非平凡逻辑须有 `*_check.py`，遵守根 `AGENTS.md`。

在仓库根执行，所有 Python 命令显式使用本项目虚拟环境；缺失时说明问题，不回退系统 Python。

```powershell
git status --short --branch
git diff --stat
& .\.venv\Scripts\python.exe selfcheck.py

# 修改公共文案/页面时；在当前 PowerShell 会话设置模块搜索路径
$env:PYTHONPATH = (Join-Path (Get-Location) '_Prj\SD_SMA_COMMON')
& .\.venv\Scripts\python.exe -m sd_sma_common.web_i18n_check

# 仅实际涉及构建/安装阶段时执行
.\build.bat installer
```

真实数据库验证命令沿用原交接文档的带 `--mariadb-home` 入口，按本机已验证工具路径填写；不自动下载或升级数据库。启动项目仍使用 `start.bat`。

每阶段交付写明：改动文件、执行的验证命令及最后的 `x/y passed`、真实数据库是否执行、剩余项。提交信息一行中文，格式如 `feat: 增加管理中心数据库统一切换`；仅在用户要求提交时执行提交。

## 7. 可直接交给下一位 AI 的提示词

> 请在 `C:\Users\BR\codex_ws\data_collector_runtime_managed` 继续 SD SMA 管理中心数据库统一管理升级。先读根 `AGENTS.md`、`_Prj/SD_SMA_DB_ADMIN/docs/database_management_upgrade_plan.md` 和同目录 `managed_mariadb_handoff.md`，查看分支、工作树 diff 与当前实现，保留已有未提交修改。
>
> 产品目标：在管理中心选择内置 MariaDB 或外部 MySQL/MariaDB。外部填写地址、端口、库名、用户名和密码，支持 root 及权限足够的专用账号。采集、查询和 DB Admin 使用统一选择，管理操作复用 DB Admin。连接测试不改变当前配置；正式切换必须暂停采集、处理完旧任务、保留旧数据，失败报告实际回退结果。切换不自动迁移历史数据。外部模式不能依赖内置服务才能打开管理中心。
>
> 以最新 TODO 和第 10 节为准：P1—P5 与 P6 本机构建已完成，不要重新实施。继续前先遵守用户对最终虚拟机验收的暂缓要求；未明确恢复时只处理用户新提出的开发问题。动手前复核涉及的调用方、自检、配置和打包清单。新增逻辑补真实实现的 assert 自检，使用仓库 `.venv\Scripts\python.exe` 跑 `selfcheck.py`，改页面/文案加跑 i18n 检查。完成后更新 TODO 和实测记录，报告改动文件、验证输出和遗留项。不要触碰归档与生产数据库，不增加自动建号、远程 root 授权或 Part 11 功能，不发布。最终虚拟机发布验收仍待用户提供环境并明确恢复。

## 8. 本次文档交付记录

2026-11-05：新增本计划并在原托管版交接文档增加链接。仅静态检查：核对源码入口、配置及安装启动依赖，执行 `git diff --check`；本次未改 Python，不运行统一自检，不声称有新的 `x/y passed`。未执行数据库连接、实际切换、构建或安装；以上 TODO 均未实施。

## 9. P0 兼容基线与交接（2026-11-05）

本节为上述规划交付之后的实际执行记录。工作分支为 `variant/managed-mariadb`，开始时暂存区为空。已有未提交修改为 COMMON 的 `password_store_check.py`、`web/zh.js`，采集器的 `config/sample_config.json`、`core/config_loader.py`、`web_config/config_manager.py`、`web_config/static/config.html` 和 `config.js`，查询的 `app/config_manager.py`、`app/main.py`、`app/static/config.html` 和 `config.js`，以及原交接文档。未跟踪文件为 COMMON 的 `database_settings.py`、`database_settings_check.py` 和本计划。已逐项阅读 diff 与新增源码，全部保留；未重置、提交或覆盖这些实现。

### 9.1 调用方与配置传播范围

以下路径相对仓库根；只检索 `_Launcher/`、`_Prj/` 源码，不以运行目录或归档作为依据。

| 函数 / 入口 | 当前全部直接调用方及相关检查 |
| --- | --- |
| `launcher_security.py:credential_for_service()` | `sd_sma_launcher.py:service_with_launcher_credential()`、`main()` 中的 `managed_start()`；`managed_database_check.py:live()` 及本轮新增 `check_legacy_routing()` |
| `sd_sma_launcher.py:service_with_launcher_credential()` | `main()` 中的 `managed_start()`；`managed_database_check.py:live()`、`check_legacy_routing()`；其后 `start_service()` 合并父进程环境与服务 env 后启动子进程 |
| `launcher_web.py:create_management_app()` | `sd_sma_launcher.py:main()`；`launcher_auth_check.py` 的 PIN 与搬迁静态目录检查。当前没有统一数据库状态/应用接口 |
| 凭据编辑、删除、分配与测试 | `launcher_web.py:save_credential()`、`delete_credential()` 通过 `services_for_credential()` 找服务后 `restart_if_running()`；`assign_credential()` 调 `assign()` 后单服务重启；`test_credential()` 通过 `credential_by_id()` 读取并 `pymysql.connect()`。`password_store_check.py` 直接检查档案存储和读取；`managed_database_check.py:live()` 检查外部分配/清除 |
| 采集配置读取 | `runtime/collector_runtime.py:DataCollectionSystem.initialize()` 调 `ConfigLoader.load_from_file()`，后者调用 `_parse_config()`；`web_config/config_manager.py:_validate_by_loader()` 也调 `_parse_config()`。`web_config/main.py` 创建 `CollectorConfigManager`；COMMON 的 `database_settings_check.py` 检查读写/导出和表单；`managed_database_check.py:check_config_override()` 检查注入 |
| 采集连接 | `DataCollectionSystem.initialize()` 构造 `DatabaseManager` 并 `connect()`；其后台健康检查调用 `ensure_connection()`，断线走 `_attempt_reconnect()` → `connect()`。`connect()` 使用 `_mysql_url()`、`_create_engine()`、`_test_engine_connection()`；自动建库走 `_create_mysql_database()` 并复用这三处。自检直接构造方为 `dict_fields_check.py`、DB Admin 的 `managed_mariadb_check.py`；后者只在显式 live 模式连接临时库 |
| 查询配置读取 | `app/main.py` 创建 `UnifiedConfigStore`，启动时 `_load_app_settings()`，后续 `set_active_config_profile()`、`create_config_profile()`、`delete_config_profile()`、`save_config_profile()`、`save_app_settings()` 重新加载并 `_apply_runtime_settings()`；`_save_app_settings()` 调 store 的保存方法。公开配置接口调用 `get_public_app_settings()`；`get_app_settings()` 另被 COMMON 的 `password_store_check.py`、`database_settings_check.py` 及 `managed_database_check.py` 调用 |
| 查询连接 | `app/main.py` 启动及 `_apply_runtime_settings()` 构造 `QueryDatabase`；`ConfigManager.probe_groups()` 被 `UnifiedConfigStore.replace_active_profile()` 调用，为导入探测构造临时 `QueryDatabase` 并 `ping()`；自检构造方为 COMMON 的 `database_settings_check.py` 及 DB Admin 的 `managed_mariadb_check.py`。`QueryDatabase._create_engine()` 决定实际 SQLAlchemy 目标 |
| 跨服务凭据处理 | `database_credentials_from_env()` 被采集 `ConfigLoader._parse_config()`、查询 `UnifiedConfigStore.get_app_settings()`、`replace_active_profile()` 及 `main.py:_load_app_settings()` 调用；`without_database_credentials()` 被采集 `sanitize_for_ui()`、`_prepare_for_storage()` 与查询 `_normalize_profile_data()`、`replace_active_profile()` 调用。COMMON 的 `database_settings_check.py` 覆盖以上真实配置路径；P1 不重写这些已有改动 |
| DB Admin 配置 | `app/main.py:get_config()`、`persist_default_connection()` 调 `default_connection()`；`save_connection_config()` 调 `persist_default_connection()`。无 Launcher 注入时仍有本地默认连接及请求参数路径；`connection_config_check.py` 通过真实 API 覆盖此行为。统一模式下需要在 P4 收口 |
| DB Admin 连接 | `connect_mysql()` 被 `list_mysql_databases()`、`list_mysql_tables()`、`ensure_mysql_connection()`、`_database_storage_bytes()`、`_table_storage_bytes()`、`export_csv_job()`、`import_csv_job()`、`managed_storage_status()` 调用，另有 `managed_mariadb_check.py:check_backup()`。`managed_connection()` 被 `connect_mysql()`、`_run_mysqldump_backup()`、`restore_verified_backup_job()`、`export_csv_job()`、`managed_storage_status()` 调用；`connection_password()` 被上述连接、备份、恢复、CSV 导出使用。CSV 导出另有独立 `pymysql.connect()` 流式连接，不能漏改；`connection_config_check.py` 直接检查密码解析 |
| 内置就绪与临时数据库 | `ManagedDatabase.wait_ready()` 由 Launcher 的 `--check-database` 分支与 `managed_start()` 调用，内部直接 `pymysql.connect()`；`managed_database_check.py:live()` 覆盖临时实例。DB Admin 的 `managed_mariadb_check.py` 自建临时实例和随机账号，仅显式传 `--mariadb-home` 执行 |

安装与打包传播链已核对：

- `start.bat` → `_Launcher/start.bat` → `sd_sma_launcher.py`；纯 CLI 为采集 `main.py` → `run_collection_mode()` → `DataCollectionSystem.initialize()`。本轮不启动运行服务。
- `build.bat` → `_Launcher/build.bat`，源码包使用 `scripts/build_portable_package.ps1`：显式复制 `sd_sma_launcher.py`、`resource_monitor.py`、`launcher_supervisor.py`、`launcher_security.py`、`managed_database.py`、`launcher_imports.py`、`launcher_web.py`、`launcher_settings.py`、静态目录及配置；按服务清单复制各项目，并总是包含 `SD_SMA_COMMON`，因此已有 `database_settings.py` 会进入包。过滤缓存、日志等产物。新增 Launcher 模块时必须同步复制清单。
- `scripts/build_installer_package.ps1` 复用源码包，Nuitka 编译 Launcher 后删除以上 Launcher 源模块；复制 WinSW、MariaDB、工具、VC 运行库、`SD_SMA_Service.xml`、`SD_SMA_DB.xml` 及安装目录下的各 PowerShell 入口。staging smoke 后才启用包内 `managed_database.enabled=true`。P0 未新增运行模块，无需调整打包清单。
- `installer/SD_SMA.iss` 调预检 → `Initialize-SD_SMA.ps1` → `Manage-SD_SMA-Database.ps1 -Action Initialize`，服务模式再调 `Install-SD_SMA-Service.ps1`。已有主服务使用 WinSW `refresh`，新安装使用 `install`，随后 `start`。
- `Initialize-SD_SMA.ps1` 仅在 DATA_ROOT 缺少 Launcher 配置时复制默认值，服务默认目录仅在空目录时填充，不复制外部凭据；`Start-SD_SMA.ps1` 当前无条件启动内置服务并检查登录，`SD_SMA_Service.xml` 当前硬依赖 `SD_SMA_DB`。这些是 P2 的修改范围，不能只改 Launcher 或只删 XML。
- 安装检查为 `installer_mode_check.py`（安装器编译、脚本语法、预检错误）与 `installer_build_check.py`（工具探测、依赖包校验）；它们当前不覆盖外部模式启动。完整构建、安装、WinSW refresh 的实际依赖清除仍留 P2/P6 验证。

### 9.2 本轮固定的字段与优先级

P1 使用现有安全配置内的 `database_binding`，精确形态为 `{"mode":"managed"}` 或 `{"mode":"external","external_credential_id":"档案ID"}`。未主动应用前不添加此字段；`managed_database.enabled` 继续表示内置组件能力，不表示当前选择。统一选择仅覆盖 `collector_web`、`query_web`、`db_admin`；Report Copy 不参与。

| 配置情形 | 当前源码行为 / 本轮基线 | P1 起必须保持的兼容与转换规则 |
| --- | --- | --- |
| 无 binding、三服务无分配、有内置凭据 | 分别使用各自内置账号，目标相同 | 保持；只展示识别结果，主动应用内置时再写 binding |
| 无 binding、三服务均分配同一外部 ID | 三服务均使用该档案；root 和非 root 一样解析 | 保持；主动应用外部时写该 ID |
| 无 binding、混合分配 | 每服务外部档案优先，无分配者用其内置账号；允许三库混用 | 保持原行为，展示“旧版按服务配置”及差异；不得因读取/重启而静默统一 |
| 无 binding、无内置账号、无分配 | `credential_for_service()` 返回 None，Launcher 不注入 DB 字段；保留服务 env/父环境与各服务既有配置行为 | 保留源码/CLI 支持边界，不重新启用采集/查询页面保存账号密码 |
| 有 managed binding（尚待 P1 实现） | 当前没有此分支 | 忽略旧三服务分配；要求各自内置账号，不合并为 root。缺组件/凭据报告配置错误，管理中心恢复路径由 P2 完成 |
| 有 external binding（尚待 P1 实现） | 当前没有此分支 | 三服务解析同一 ID；用户名允许 root 或专用账号；不加载内置密码。缺失引用/非法选择不得返回 None 或退回内置/本地配置 |

首次显式应用在同一次 `_atomic_json()` 保存中写 binding 并移除上述三个服务的旧 assignments；保留其他安全配置、PIN 和档案。binding 存在时旧逐服务分配入口必须拒绝修改。生效外部档案不得通过旧 CRUD 直接编辑或删除；P1 先拒绝，P3 再提供经过整体应用的修改路径。外部参数校验在入口一次完成；解析层不增加第二套配置框架。损坏安全文件当前被 `_load()` 吞掉并返回默认数据，这是待修的诊断缺口，不能把它算作新模式的合法回退。

纯 CLI/源码边界依据现有未提交实现固定如下：采集与查询只从 `SD_SMA_DB_USERNAME` / `SD_SMA_DB_PASSWORD` 取账号密码，未设置或设置为空均不采用旧文件中的账号密码。引擎、地址、端口、库名仍由非空注入值覆盖本地设置；无注入时保留文件中的这些字段。SQLite 继续可用，MySQL 的既有文件密码不再是 CLI 登录来源，调用方需提供环境变量或经 Launcher 启动。配置读取不改写旧文件，保存/导出时去除 DB 凭据；OPC UA 密码独立保留。DB Admin 无注入时的旧本地连接行为暂留；P4 必须收口到统一模式。

### 9.3 本轮实现与验证

本轮生产连接解析与服务启动未改动。扩展现有 `_Launcher/managed_database_check.py`，在默认离线检查中调用新增 `check_legacy_routing()`：使用临时安全文件和真实 `LauncherSecurityStore` / `service_with_launcher_credential()`，验证内置分账号、同一外部 root、同一专用账号、混合分配、六项连接字段注入及内置标记、服务 env 不被原地修改、公开凭据脱敏、读取/重新加载不重写旧文件，以及无 Launcher 凭据时不注入、普通 Report Copy 不注入。没有复制解析逻辑或 mock 数据库来宣称连接成功。

基线命令（修改前，当前源码与已有未提交改动）：

```powershell
& .\.venv\Scripts\python.exe selfcheck.py
```

实际输出 `selfcheck: 18/18 passed`，退出码 0；与原交接文档的 17/17 是不同时间的结果，不混用。

聚焦检查命令：

```powershell
& .\.venv\Scripts\python.exe _Launcher/managed_database_check.py
```

实际退出码 0，输出包括：

```text
PASS machine DPAPI roundtrip, corruption rejection, port conflict
PASS legacy routing: managed defaults, shared root/operator, mixed assignments, reload without rewrite
PASS source without Launcher credentials: no injection; ordinary Report Copy has no database credentials
PASS collector and query configurations use injected engine and credentials
SKIP live provisioning (requires --mariadb-home); administrator ACL and services not exercised
```

交付复检命令（项目解释器实际为 Python 3.12.13）：

```powershell
& .\.venv\Scripts\python.exe selfcheck.py
$env:PYTHONPATH = (Join-Path (Get-Location) '_Prj\SD_SMA_COMMON')
& .\.venv\Scripts\python.exe -m sd_sma_common.web_i18n_check
git diff --check
```

实际结果：完整自检 `selfcheck: 18/18 passed`，退出码 0；i18n `web_i18n_check: OK`，字典 284 条、引用 284 处，退出码 0；`git diff --check` 退出码 0，无空白错误（有 Git LF/CRLF 提示）。检查总数未增加，因为扩展的是已有自检。安装自检中的临时 fixture 编译和脚本检查通过，不等于构建了完整产品安装包。

未执行真实 MySQL/MariaDB 实例验证、真实 PLC、整体切换/排空/回退、生产数据库访问、产品构建或安装；未注册/启停系统服务，未变更外部账号权限。默认自检中 live provisioning / live validation 为显式跳过，不算真实数据库验收。P1—P6 不因本轮离线检查而勾选；最终虚拟机发布验收继续暂缓，不推送、不发布。


## 10. P1—P6 连续执行记录（2026-11-05）

本轮用户明确授权继续 P1 及后续全部阶段；因此在 P0 基线上连续实施，不再受原“每轮一阶段”限制。仍在 `variant/managed-mariadb`，保留开始时全部未提交修改，不提交、不推送、不发布。以下为本轮实际源码与新验证，历史 P0/托管版记录不替代本轮结果。

### 10.1 实现与调用范围

| 阶段 | 改动文件与最终行为 |
| --- | --- |
| P1 | `_Launcher/launcher_security.py` 增加统一 binding、一次性文件入口校验、延迟内置凭据读取、三服务解析、原子保存/恢复数据库档案与分配；旧版无 binding 保留逐服务解析。生效档案（含旧分配）不能经旧 CRUD 直接编辑/删除；旧分配 API 在管理中心中拒绝改库。`sd_sma_launcher.py` 复用注入函数，Report Copy 不接收中央数据库凭据。新增 `database_binding_check.py`，扩展既有 `managed_database_check.py` 的基线检查。 |
| P2 | `sd_sma_launcher.py` 先读取选择，再按需解析内置账号；管理中心先启动，业务服务的数据库错误留在各服务状态。`managed_database.py:start_installed_service()` 仅在实际使用内置时启动已有产品服务。`installer/Start-SD_SMA.ps1` 不再先强制启动内置库；`SD_SMA_Service.xml` 移除硬依赖，`Install-SD_SMA-Service.ps1` 注册/refresh 后明确清除旧 SCM 依赖。`Manage-SD_SMA-Database.ps1` 外部模式跳过初始化、服务启动和内置端口预检；覆盖安装的预检仍处理已存在的产品数据库服务，避免替换文件时被占用，不创建/重建实例。安全配置解析失败只输出固定错误，不回显含密码的 JSON。更新 `installer_mode_check.py` 与 `installer_build_check.py`。 |
| P3 | 新增 `_Launcher/launcher_database.py:DatabaseController`，由 `main()` 构造并传入 `create_management_app()`；新增 GET 状态、POST 候选预检、POST 整体应用。复用 PIN/Origin 权限和现有 machine token；普通应用锁防止重复提交，切换时其他管理写操作拒绝。`launcher_supervisor.py` 增加同步确认停止及重启时的采集 hold。COMMON 新增 `database_control.py`，提供三服务实际连接契约、内部访问控制及 SQLite outbox 只读计数；不打开会自动恢复 lease 的队列对象。 |
| P3 采集 | `database/data_storage.py:drain_for_database_switch()` 先等已有处理任务完成，再将旧缓存交给真实批量写入；失败保留 retry/dead_letter，不复用会取消任务/重置批次上下文的普通停机路径。`web_config/collector_host.py` 与 `main.py` 提供暂停、排空、准备新运行态、恢复；保留上一次未写内存引用，存在未写记录时拒绝重新启动覆盖它。重启新 Web 进程期间关闭自动采集，初始化/连接验证通过前保持生产暂停。 |
| P3 运维/查询 | DB Admin `app/main.py` 在任务锁内拒绝忙碌切换，并在暂停期间阻止新任务及其他运维写操作；内部状态复用现有客户端解析，检查两个工具能实际执行。查询 `app/main.py` 验证实际 QueryDatabase 引擎和连接；三个服务的内部接口都必须提供现有 `X-SD-SMA-Token`，不能借用不拒绝访客的普通会话中间件放行。新增 `database_switch_check.py`，仅替换进程与远程 I/O 边界，真实执行 store/supervisor/apply 和 SQLite 存储。 |
| P4 | 管理中心 `static/index.html`、`app.js`、新 `database.js` 用统一模式、未保存测试、整体确认与逐服务结果替代三组分配下拉框；显示当前/下次启动目标，DB Admin 链接复用 host 会话且无密码 URL。`styles.css` 移除公共按钮/表单/面板副本，放入 COMMON `web/base.css` 的 Launcher 触屏变体；新数据库文案加入 `web/zh.js`，保留其他管理子页布局。 |
| P4 服务页面 | COMMON `database_settings.py:effective_database_settings()` 被采集 `sanitize_for_ui()`、查询公开设置/整份导入复用，纠正引擎/地址/端口/库名显示与运行时不一致；新 metadata 不写入业务配置。采集/查询 `config.html`、`config.js` 集中管理时连接字段只读。DB Admin `main.py` 对所有中央管理连接拒绝本地覆盖，`static/config.js` 隐藏第二套配置，`storage.js` 显示外部业务库占用且不返回远程磁盘余量。受 COMMON 字典/CSS 或配置脚本影响的 HTML 缓存版本更新为 `20261105_02`；查询示范页的 i18n 引擎本身未改，保留旧引擎版本。 |
| 检查/构建 | 扩展 `_Launcher/launcher_auth_check.py`（执行实际数据库页 JS）、DB Admin `connection_config_check.py`、COMMON `web_i18n_check.py`（发现独立 Launcher JS）；扩展 `managed_mariadb_check.py` 支持显式 MySQL home/客户端组合，并提取可复用的 `isolated_database()`；新增 `database_live_check.py` 通过 `start.bat` 启动真实四服务。两个现有构建脚本复制/编译 `launcher_database.py`；Nuitka 显式包含 `sqlite3`，避免动态 COMMON 引用漏包。 |

共享函数修改前已重新检索当前调用者；新增生产调用集中在上述 controller 与三个内部接口，原 P0 兼容检查继续通过。未新增运行依赖或数据库版本。新文档仍写入本文件与既有 handoff，无顶层说明文件。

### 10.2 切换结果与安全边界

- 候选测试不保存、不重启、不建库、不改授权；返回实际连接/版本、TLS 状态、授权缺项和客户端可用性。权限判断基于服务端 `SHOW GRANTS`（含当前启用角色），不声称已实际写过每一种 DDL 或完整备份恢复。[MySQL 官方说明](https://dev.mysql.com/doc/refman/8.0/en/show-grants.html)、[MariaDB 官方说明](https://mariadb.com/docs/server/reference/sql-statements/administrative-sql-statements/show/show-grants)。
- 正式应用：预检 → 拒绝忙碌运维任务并冻结入口 → 暂停旧任务生产 → 等待在途写入、处理缓存并检查所有配置引用的 outbox → 同步停止旧三个进程 → 原子保存 → 只启动原本运行者 → 核对真实连接及采集初始化 → 恢复原采集状态。Report Copy 不参与切库。
- 失败返回 stage、实际 binding、逐服务状态与 rollback 结果；候选失败不改变有效配置。新库验证失败恢复旧档案/旧分配及原运行状态。回退也失败时不报告成功；为避免丢失可能存在的内存任务，保留采集宿主而停止其他数据库服务，实际状态中保留 PID/状态供修复，不能将此误读为三个进程均已结束。不要强制结束仍持有未写任务的采集宿主。
- 不清空队列，不给旧任务换目标，不删历史，不自动迁移/合并数据。新目标的初始化 DDL 或恢复采集后已经写入的数据不会被选择回退撤销；切回不会同步期间新增数据。
- 外部 root 与专用账号一样处理；不修改外部用户/授权。外部密码仍沿用本地 secrets JSON 明文存储，公开接口、URL、页面回填及服务配置不输出密码；没有擅自加密迁移。
- 此版本表单仍为基础 TCP 连接参数；强制 TLS 或当前驱动不支持的认证插件明确拒绝，不自动关闭服务器保护。受信任 TLS/自定义 CA 的正向连接流程未纳入本次支持矩阵，不将拒绝诊断算作 TLS 接入成功。

### 10.3 实测矩阵

| 内容 | 实际执行与结果 |
| --- | --- |
| 完整离线回归 | 当前项目 Python 3.12.13，`selfcheck: 21/21 passed`。其中默认 live 入口均显式 SKIP，下面独立带参数运行才计为真实数据库验证。 |
| MariaDB | 独立临时 MariaDB **11.4.13**，PyMySQL **1.1.0**，配套 MariaDB client **15.2** / dump **10.19**（均来自 11.4.13）；真实建表、补列、索引、中文/特殊字符/emoji、批量写入、字典表、分页筛选、SQL 篡改拒绝与备份恢复通过。 |
| MySQL | 独立临时 MySQL Community **8.0.24**，PyMySQL **1.1.0**，MySQL client/dump **8.0.24**；相同业务与备份恢复通过。另用随包 MariaDB 11.4.13 客户端组合对同版本 MySQL 重跑通过。以上只证明该版本/小型业务表组合，不外推所有版本或对象。 |
| 权限/认证 | 两种服务端均验证 root、业务库范围 operator、只读缺项、错误密码、库不存在、不可达；原采集/查询/运维 35 项越权拒绝继续通过。临时 MariaDB 的 ed25519 用户在未装 PyNaCl 的本项目驱动中返回认证插件不兼容；临时 MySQL 开启强制安全传输时返回 TLS-required。仅改变测试实例，未改生产服务。 |
| 内置初始化 | 显式运行 `managed_database_check.py --mariadb-home ...`：DPAPI 往返/损坏拒绝、初始化中断重试、实例复用、三独立账号、精确库授权、缺数据拒绝通过。仅用户自有临时目录替换管理员 ACL 步骤，不能算生产 ACL/Windows 服务验收。 |
| 真实启动/切换 | `database_live_check.py` 从 `start.bat` 启动独立临时数据根的真实 Launcher 和四服务；启用内置能力但内置凭据/实例缺失时，外部模式仍全部就绪。实际 root/专用账号、外部 A(MySQL)→B(MariaDB)→A、外部→内置→外部、同目标换密码、Launcher 重启保持选择通过；内置三个账号仍各自独立。 |
| 活跃采集 | 独立本地 OPC UA 模拟器产生真实记录；未满 100 行的旧目标缓存先排空，目标确认后恢复真实采集运行态。没有连接真实 PLC。原本停止的 Query 服务经真实切换后仍停止；Report Copy 持续运行。 |
| 真实失败回退 | 候选预检通过、binding 落盘后，测试线程只修改自有候选实例账号密码，使新服务连接失败；真实应用恢复原 binding 和原服务连接，返回 `ok=false`、`rollback.ok=true`，没有把半成功报为完成。 |
| 队列与运维阻断 | 真实 SQLite 内存/持久化存储验证旧缓存写完、写失败保留；真实 outbox 的 pending/processing/retry/dead_letter 均拒绝切库且记录未变。DB Admin 真实任务管理/API 在任务忙碌时拒绝暂停，冻结后拒绝新写操作。controller 的进程/远程边界故障注入覆盖候选失败、启动失败、回退失败、内存积压与原本停止状态；未把故障替身算作真实服务器故障。 |
| 历史保留 | 两个实际目标放入不同标记，切换后仍留在各自业务库；未自动合并/迁移；前端应用确认明确告知空库历史为空、切回不合并新数据。 |
| 页面 | 浏览器实际打开临时管理台：模式/外部参数/权限提示/运维入口正常；选择已有档案不回填密码；缺内置组件报错后按钮恢复。Node 执行真实 `database.js` 验证未保存测试、失败按钮恢复、应用成功及无密码 URL。i18n 当前 `web_i18n_check: OK`，字典 **323** 条、引用 **323** 处。 |
| 安装入口 | 实际 Inno fixture 编译、全部 PowerShell 语法通过；用临时 ProgramData 验证外部 Initialize/Start 跳过缺失内置组件且不改选择；服务查询替身下内置端口占用仍不阻止外部 Preflight。SCM refresh 依赖清除仅源码/脚本检查，实际 Windows 服务安装与重启仍留虚拟机。 |

### 10.4 本轮复现命令与输出

在仓库根，所有 Python 使用本项目解释器。为使 Windows 自检错误输出可读，设置 UTF-8 输出；不改系统 Python/编码设置。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
& .\.venv\Scripts\python.exe selfcheck.py
$env:PYTHONPATH = (Join-Path (Get-Location) '_Prj\SD_SMA_COMMON')
& .\.venv\Scripts\python.exe -m sd_sma_common.web_i18n_check
& .\.venv\Scripts\python.exe _Launcher/managed_database_check.py --mariadb-home _Build/mariadb-validation/mariadb-11.4.13-winx64
& .\.venv\Scripts\python.exe _Prj/SD_SMA_DB_ADMIN/managed_mariadb_check.py --mariadb-home _Build/mariadb-validation/mariadb-11.4.13-winx64
& .\.venv\Scripts\python.exe _Prj/SD_SMA_DB_ADMIN/managed_mariadb_check.py --mysql-home 'C:\Program Files\MySQL\MySQL Server 8.0'
& .\.venv\Scripts\python.exe _Prj/SD_SMA_DB_ADMIN/managed_mariadb_check.py --mysql-home 'C:\Program Files\MySQL\MySQL Server 8.0' --client-home _Build/mariadb-validation/mariadb-11.4.13-winx64
& .\.venv\Scripts\python.exe _Launcher/database_live_check.py --mariadb-home _Build/mariadb-validation/mariadb-11.4.13-winx64 --mysql-home 'C:\Program Files\MySQL\MySQL Server 8.0'
.\build.bat installer
git diff --check
```

本轮独立实测最后输出：`managed_mariadb_check: live assertions passed; instance stopped and removed`（三个客户端组合分别执行）；`database_live_check: all live assertions passed; temporary instances stopped and removed`。日志在 `_Build/database-upgrade-mariadb.log`、`database-upgrade-mysql.log`、`database-upgrade-client-pair.log`、`database-upgrade-live-switch.log`；完整自检日志为 `_Build/database-upgrade-selfcheck.log`。这些均为忽略的验证产物，不提交。

构建曾在编译版 smoke 发现 `ModuleNotFoundError: sqlite3`；已修改构建源脚本显式包含该标准库并重跑，未手改 `_Build`。重新构建最终退出码 0，编译版 Launcher 输出 `[smoke] ok`，Inno 成功生成候选安装包。没有手改生成产物。

- 命令：`build.bat installer`（本轮以 `cmd /d /c "build.bat installer < nul"` 避免脚本末尾 pause 阻塞日志采集）；日志 `_Build/database-upgrade-installer.log`。
- 候选产物：`_Build/SD_SMA_Installer_Package/SD_SMA_ManagedDB_Setup_1.0.2.exe`，**132,913,001** 字节，SHA-256 **01612970AB8914A7B83AF6B9F66288C64C9A630D8E8A8F4061A9D43C408551BC**。同名旧候选包已被此构建替换；这是开发候选包，不是已验收发布包。
- 构建后比较源与 staging 的 `Manage-SD_SMA-Database.ps1`、`SD_SMA_Service.xml`、`Install-SD_SMA-Service.ps1`、`database.js`，SHA-256 全部一致，确认没有遗漏本轮后续修改；staging `secrets/` 已按现有清理流程删除。
- 交付前完整自检再次得到 `selfcheck: 21/21 passed`，`git diff --check` 退出码 0（仅 LF/CRLF 提示）；i18n 为 `web_i18n_check: OK — 字典 323 条, 引用 323 处, 底座挂载正常`。

### 10.5 保持暂缓的范围

P6 最后的真实 Windows 虚拟机生命周期验收和发布材料确认不勾选。未安装或操作真实 Windows 数据库服务，未访问生产数据库，未创建远程 root 或扩大外部数据库权限，未发布。未覆盖真实 B&R PLC、压力/大库、并发 DDL、视图/存储过程/事件/触发器完整备份恢复、TLS 正向接入、所有 MySQL/MariaDB 版本，以及真实 SCM refresh/安装 ACL。Nuitka 的 Windows Runtime DLL 提示仍存在，安装器沿用已固定的离线 VC++ 包；全新系统效果待虚拟机验证。
