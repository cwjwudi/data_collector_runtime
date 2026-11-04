# SD SMA 托管数据库版开发交接

## 基线与工作方式

- 分支为 `variant/managed-mariadb`，从标准版 `v1.0.2`（`dd602f0ebc91a54d497399725defe21f3800ef8e`）分出。
- 在独立 worktree `data_collector_runtime_managed` 开发，使用该目录自己的 `.venv` 和 `_Build`。开始前运行 `git status --short --branch`，并阅读根目录 `AGENTS.md`。
- 标准版继续从 `main` 发布。变体按阶段提交，提交信息使用仓库规定的一行中文格式。主线需要的修复按提交挑入变体分支。发布通过验收后可在变体提交上使用独立标签，例如 `managed-db-v0.1.0`。安装包使用构建脚本接受的数字版本，显示名和文件名标明托管数据库版。
- 第一版用干净 Windows 虚拟机安装。现有服务和 ProgramData 在机器级共享，同机并装标准版与托管版属于独立需求。

## 可直接发给下一位 AI 的任务

> 在当前工作树继续开发 SD SMA 托管数据库版。先核对当前分支 `variant/managed-mariadb`，阅读 `AGENTS.md` 和本文件，确认下面的基线和 MariaDB 实测结果。数据库实测及构建、安装、账号启动、客户界面实现已完成。用户于 2026-11-04 明确暂缓最后的虚拟机发布验收，待其搭建环境后再继续；不要提前执行最后一步。按 TODO 顺序分阶段交付，所有变体改动留在当前分支。未经要求不推送或发布。
>
> 目标是干净 Windows x64 机器断网运行一个安装包，安装完成后客户只需配置 PLC。安装包带 MariaDB Community Server，自动建立本机数据库服务、业务库和运行账号。Launcher 管理连接凭据，客户通过 SD SMA 查看存储状态、备份和恢复。沿用 `build.bat installer`、Inno Setup、Launcher 和 DB Admin。
>
> 第一版覆盖新装、重启、手动和服务两种启动、同版覆盖安装、卸载保留数据以及换机恢复。旧版升级、旧库迁移和同机并装在本期范围之外。保留应用通过 PyMySQL 连接外部数据库的能力。每阶段汇报改动文件、验证命令、实际结果和遗留项。

## 开发 TODO

- [x] **数据库实测。** 以 MariaDB Community Server 11.4 LTS 为候选，用官方 Windows x64 发行包在测试实例中跑真实的采集建表、补列、写入、历史查询、备份和恢复。记录准确版本、来源、SHA-256、用例与实际所需权限。采集账号限定在业务库内写入、建表和补列，查询账号只读，运维账号限定在业务库的备份恢复。用运行账号验证权限边界。2026-11-04 完成，证据与范围见下文。
- [x] **构建。** 在 `_Launcher/scripts/build_installer_package.ps1` 固定 Windows x64 服务端和配套客户端工具的来源，构建遇到依赖缺失或哈希不符时停止。客户安装过程离线。保留现有暂存区编译版 Launcher `--smoke` 检查。测试安装包的外部依赖应在干净虚拟机上覆盖。
- [x] **安装和卸载。** 从 `_Launcher/installer/SD_SMA.iss` 和现有初始化脚本接入 `SD_SMA_DB`。实例使用独立固定端口，只监听 `127.0.0.1`。二进制放 Program Files，实例数据放 `ProgramData\SmartData\SD SMA`。检查端口占用，确保安装步骤失败能反馈到安装器。同版覆盖安装复用实例和凭据，卸载移除产品服务并默认保留数据库文件。
- [x] **账号和启动。** 首次安装预建业务库并为各服务生成每机独立的随机密码。使用机器范围 Windows DPAPI 配合 SYSTEM 和管理员目录权限保护凭据，复用 Launcher 按服务下发连接信息的方式。初始化凭据不得进入命令行参数、日志、普通配置或页面接口。服务模式配置数据库依赖，手动模式由启动入口确保数据库启动，Launcher 等实际连接可用后再启动依赖模块。明确初始化中断后重试的行为，已有实例和凭据不得被覆盖。
- [x] **客户界面。** 复用 DB Admin 的备份和恢复入口，展示托管存储的状态和可操作错误。客户无需填写数据库地址、端口、用户名和密码。涉及文案时更新公共 i18n 字典及静态资源缓存版本。
- [ ] **发布验收。** 更新相关 `*_check.py`，保留自检的 `x/y passed` 结果。在干净 Windows 虚拟机验证断网安装、采集与查询、端口冲突、初始化失败、重启、覆盖安装保留数据、卸载保留数据和新机器恢复业务备份。归档安装包 SHA-256、验收结果，以及随包数据库和工具的许可证、版权声明和对应源码交付材料。

以上勾选表示实现与本机检查完成，不代表 Windows 安装生命周期已经验收。最后一项按用户要求暂缓。

## 复现命令

在当前 worktree 的根目录执行。

```powershell
git status --short --branch
& .\.venv\Scripts\python.exe --version
& .\.venv\Scripts\python.exe selfcheck.py
.\build.bat installer
```

新增工作树缺少虚拟环境时，可用同级标准版工作树已确认可用的项目解释器创建本地环境。

```powershell
& ..\data_collector_runtime\.venv\Scripts\python.exe -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r _Launcher\requirements-unified.txt 'nuitka==4.3.1' zstandard 'httpx2==2.16.0'
```

安装包构建需要 Inno Setup 6 和可用的 C 编译工具。新工作树还要在 `_Launcher/installer/tools/` 准备 WinSW 2.12.0 的 `WinSW-x64.exe` 和 `WinSW-LICENSE.txt`。本次从同级标准版工作树复制，先按构建脚本校验 SHA-256，两个文件均由 Git 忽略。自检所需的 `httpx2` 只安装在开发环境。按 `AGENTS.md`，运行自检和安装 Python 依赖均显式指定当前 worktree 的解释器。

## 标准版基线实测

- 开发环境使用 Python 3.12.13、Nuitka 4.3.1、FastAPI 0.162.0、Starlette 1.10.0 和 httpx2 2.16.0。打包暂存步骤按现有脚本选择的 Python 基础运行时为 3.12.14。
- `selfcheck.py` 首轮为 `11/15 passed`，四个检查均因 Starlette TestClient 缺少 `httpx2` 而失败。补齐开发依赖后复跑得到 `selfcheck: 15/15 passed`。日志位于 `_Build/baseline-v1.0.2-selfcheck.log`。
- `build.bat installer` 首轮因新工作树缺少 WinSW 停止。补齐并校验 WinSW 后，构建退出码为 0，编译版 Launcher 的暂存区检查输出 `[smoke] ok`，Inno Setup 成功出包。日志位于 `_Build/baseline-v1.0.2-installer.log`。
- 标准版基线产物为 `_Build/SD_SMA_Installer_Package/SD_SMA_Setup_1.0.2.exe`，大小为 62,467,333 字节，SHA-256 为 `304736A6EA3A18375367702E9D2B15B38FD2020C4531FE8D8B35F3E7C0BA70DD`。构建从本机 MySQL 安装目录带入了备份客户端工具。
- 产物用于开发对照。暂存区启动验证在构建机上完成。打包脚本从本机 Codex Python 运行时复制了额外工具和库，Nuitka 也报告无法随包纳入 Windows Runtime DLL。托管版发版应在受控构建环境检查打包清单，并在干净虚拟机确认 Windows 运行库要求。
- 下一项是 MariaDB 实例验证。此基线未执行 MariaDB 或实际 Windows 安装、重启、卸载与换机恢复。

## MariaDB 实测复现入口

新增 `_Prj/SD_SMA_DB_ADMIN/managed_mariadb_check.py`，直接调用采集器存储处理器、历史查询实现和 DB Admin 备份恢复函数。仅在 `_Build/mariadb-validation/` 下创建临时实例，监听本机临时端口，不注册服务；运行结束后关闭进程并删除实例。测试账号密码每次随机生成，不写入命令行或配置文件。

候选包为 MariaDB Community Server 11.4.13 Windows x64 ZIP：

- [官方文件列表](https://dlm.mariadb.com/browse/mariadb_server/11.4.13/winx64-packages/)
- [官方归档包](https://archive.mariadb.org/mariadb-11.4.13/winx64-packages/mariadb-11.4.13-winx64.zip)
- [官方 SHA-256 清单](https://archive.mariadb.org/mariadb-11.4.13/winx64-packages/sha256sums.txt)
- 实测大小：95,024,126 字节；整包 SHA-256 与官方清单一致：`d62986d433eeebfde218560b276103831604a61e929e87f1a17f5aebd80257e2`。归档站下载较慢，使用 [Princeton 镜像的同一发行包](https://mirror.math.princeton.edu/pub/mariadb/mariadb-11.4.13/winx64-packages/mariadb-11.4.13-winx64.zip) 补齐后校验；本机留存为 `_Build/mariadb-validation/mariadb-verified.zip`。

```powershell
& .\.venv\Scripts\python.exe _Prj\SD_SMA_DB_ADMIN\managed_mariadb_check.py --mariadb-home _Build\mariadb-validation\mariadb-11.4.13-winx64
& .\.venv\Scripts\python.exe selfcheck.py
```

统一自检不下载或启动数据库：新增检查无参数时输出 `SKIP live validation`。必须另外执行上述带路径命令，不能用统一自检的 passed 数量代替数据库实测。

### 2026-11-04 实际结果

- 环境：Windows 11 专业版 x64，系统版本 `10.0.26200`，当前 worktree 的 Python 3.12.13。
- 服务端返回 `11.4.13-MariaDB`；同包 `mariadb-dump.exe` 为 client 10.19，`mariadb.exe` 为 client 15.2，均显示来自 11.4.13-MariaDB Win64。
- 采集：`DataStorageProcessor.initialize_tables_for_runtime` 建表、重复启动补列、索引存在性检查通过；实际存储处理器批量写入 3 条记录，`DatabaseManager.execute_update` 更新成功，字典字段幂等同步成功。数据包含中文、单引号、反斜线和 emoji。
- 查询：只读账号调用 `QueryDatabase.query_history`，按时间及批次筛选，验证总数、两页记录和更新后的值。
- 运维：业务库账号直接调用 `backup_mysql_job` 和 `restore_verified_backup_job`，生成 SQL 与 manifest；同长度篡改 SQL 后 SHA-256 校验拒绝恢复；删除测试业务表后重新恢复，全部历史记录及字典值与备份前一致。
- 越权：采集账号 11 项、查询账号 15 项、运维账号 9 项操作被 MariaDB 拒绝。包含其他业务库的读写删表、相似库名 `smaXvalidation` 的读取、读取 `mysql.user`、创建其他数据库、创建用户、设置全局变量、转授权；另验证采集账号不能删行/删表，查询账号不能写入、更新、删除或执行 DDL。
- 最终实测输出：`managed_mariadb_check: live assertions passed; instance stopped and removed`。日志：`_Build/mariadb-validation/live-check.log`。
- 统一自检：`selfcheck: 16/16 passed`，日志：`_Build/mariadb-validation/selfcheck.log`。原有 15 项加上显式跳过的 live 入口；真实数据库成功证据以独立实测日志为准。
- 本阶段只新增自检并更新本交接文档，生产代码无需修改。没有注册数据库服务，测试实例及随机账号随临时数据目录删除。

### 本阶段验证的授权

以下权限均只授予业务库 `sma_validation`，账号 host 为 `127.0.0.1`，不授予 `GRANT OPTION`，不授予 `*.*` 或 `mysql.*` 权限。安装阶段应替换为正式业务库名，并保留授权语句中的通配符转义。

**已复现的授权陷阱：** `GRANT ... ON sma_validation.*` 中 `_` 会匹配任意单字符；实测采集账号因此能读到 `smaXvalidation.secret`。改为 ``GRANT ... ON `sma\_validation`.*`` 后相似库名访问被拒绝。单独加反引号并不足够，初始化脚本必须转义库名中的 `_`（若允许 `%`，也需转义）。失败证据在 `_Build/mariadb-validation/grant-wildcard.log`，最终检查已保留该拒绝用例。

| 账号 | 已实测可用的权限 | 用途 |
|---|---|---|
| 采集 | `SELECT, INSERT, UPDATE, CREATE, ALTER, INDEX` | 元数据/历史读取、采集写入、批次更新、建表补列、独立建索引 |
| 查询 | `SELECT` | 表和列信息、历史与字典查询 |
| 运维 | `SELECT, INSERT, CREATE, DROP, ALTER, LOCK TABLES, EVENT` | 导出业务表、恢复建表/插入/启停键及锁表；兼容现有 `--events` 参数 |

额外做过一次运维减权实验：去掉 `EVENT` 后，现有备份命令的 `show events` 返回 1044；加回业务库级 `EVENT` 即通过。失败证据在 `_Build/mariadb-validation/reduced-grants.log`。运维账号未授予 `INDEX, SHOW VIEW, TRIGGER` 也可完成本次纯业务表备份恢复。

本次库内没有存储过程、视图、事件或触发器，不能由此声称这些对象可完整备份恢复。现有 `--routines` 在此用例下无需读取系统库；若未来业务引入这些对象，必须增加对象级用例并重新评估权限，不能直接扩大到全局权限。库级 `ALTER` 也允许修改/删除业务列，`DROP` 允许删除业务库，`EVENT` 允许管理业务库事件；数据库授权不能把它们严格限制为“只补列”或“只恢复”，本阶段验证的是业务库边界。

未覆盖真实 PLC 通讯、压力/大库、并发 DDL 与备份、Windows 服务启动和干净虚拟机安装。安装、重启、覆盖安装、卸载保留数据及换机恢复仍按后续 TODO 验收；本次在同一临时实例内清空业务表后恢复，不等同于换机恢复。备份期间应避免采集端执行建表/补列等 DDL，单事务导出不能保证跨并发 DDL 的一致性（[MariaDB 官方导出说明](https://mariadb.com/docs/server/clients-and-utilities/backup-restore-and-import-clients/mariadb-dump)）。


## 2026-11-04 托管版开发完成，最终验收暂缓

用户明确要求先不执行最终验收；待用户搭好干净 Windows x64 虚拟机再继续。此阶段未安装本机系统服务、未修改真实 ProgramData，也未推送、打标签或发布。以下记录保留在既有交接文档，便于下一轮从最后一项继续。

### 实现范围

- `_Launcher/installer/mariadb-package.json` 固定 MariaDB 11.4.13 官方 ZIP；`vc-runtime-package.json` 固定微软 VC++ x64 14.44.35211.0 离线安装包。两个包均放入 Git 忽略的 `installer/tools/`，缺失或 SHA-256 不符直接停止构建。VC 包下载后 Authenticode 状态为 Valid，签名者 Microsoft Corporation，SHA-256 为 `cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b`。固定下载地址见清单。
- 构建只使用指定解释器或本仓库 `.venv`；本次 Python 基础运行时和编译解释器均为 3.12.13。MariaDB 服务端、客户端与原包许可证一并带入。保留编译版 Launcher 暂存区 smoke，随后清除 smoke 生成的配置、日志和秘密材料，再启用安装包的托管模式。源码开发配置仍默认关闭托管数据库。
- Inno Setup 先检查/安装离线 VC 运行库，相同或更新版本跳过；要求重启则中止本轮安装并提示重启后重跑。随后检查安装归属、停止产品服务、检查端口。所有初始化步骤检查退出码并将错误反馈到安装器。VC 安装方式参考 [微软部署说明](https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files)。
- `Manage-SD_SMA-Database.ps1` 和 `SD_SMA_DB.xml` 管理独立 LocalSystem 数据库服务。默认只监听 `127.0.0.1:3307`，库名 `sd_sma`；程序目录 `_Database`，数据目录 `ProgramData/SmartData/SD SMA/database`。主服务依赖数据库，手动入口先启动并验证数据库登录。
- `managed_database.py` 实现无网络初始化、随机账号、机器 DPAPI、SYSTEM/Administrators 目录 ACL、真实连接就绪检查。清除发行包的匿名账号、非 localhost root 和测试库默认授权，并对库名中的 `_` 转义。密码只经 bootstrap 标准输入传递，不写普通配置、命令行或公开接口。
- 首次失败只可重建专用 `database.initializing` 临时目录，复用已生成凭据；已发布实例复用数据及密码。已有数据缺凭据、凭据损坏或已就绪实例丢失数据时拒绝重新创建空库。卸载默认保留数据库及凭据；换机应恢复业务备份，不能复制机器绑定 DPAPI 文件。
- Launcher 保持按服务注入配置，采集和查询支持注入数据库引擎。外部数据库仍可通过 Launcher 分配连接；清除分配后恢复托管默认值。DB Admin 使用随包客户端，新增存储可用性、版本、占用和剩余磁盘显示，区分连接/凭据/磁盘错误，隐藏托管连接表单。相关文案使用公共 i18n，并更新缓存版本。

### 本机验证记录

```powershell
& .\.venv\Scripts\python.exe selfcheck.py
$env:PYTHONPATH = '_Prj/SD_SMA_COMMON'
& .\.venv\Scripts\python.exe -m sd_sma_common.web_i18n_check
& .\.venv\Scripts\python.exe _Launcher/managed_database_check.py --mariadb-home _Build/mariadb-validation/mariadb-11.4.13-winx64
& .\.venv\Scripts\python.exe _Prj/SD_SMA_DB_ADMIN/managed_mariadb_check.py --mariadb-home _Build/mariadb-validation/mariadb-11.4.13-winx64
.\build.bat installer
```

- 统一检查：`selfcheck: 17/17 passed`，日志 `_Build/managed-development-selfcheck.log`。包括真实 DPAPI 往返/损坏拒绝、占用端口拒绝、缺包/错哈希拒绝、实际 Inno 编译和全部安装/构建 PowerShell 脚本语法检查。
- i18n：字典 283 项、引用 283 项，挂载与文案检查通过。前端状态脚本使用 Node 执行并检查成功、失败和外部数据库分支；未做浏览器视觉验收。
- 初始化真实实例：`_Build/managed-provisioning.log`，中断重试、重复初始化不改凭据、账号登录、root/匿名账号清理、精确库授权、数据丢失拒绝全部通过。当前进程不是提升后的管理员，因此实例测试仅跳过目录 ACL 设置，并另外验证生产初始化拒绝非管理员；不能将此作为生产 ACL 或服务验收。
- 业务真实实例：`_Build/managed-storage-live.log`，真实采集建表/补列/写入、分页查询、35 项越权拒绝、SQL 哈希拒绝、备份恢复、存储容量及凭据错误通过。临时实例均关闭并清理。

### 下一轮仅剩最终验收

待用户提供可重置的干净虚拟机后，覆盖断网且未装 VC 运行库的新装、服务/手动启动、重启、端口冲突、初始化中断、同版覆盖、卸载保留数据、重装复用及另一台机器业务备份恢复。检查管理员/SYSTEM 可访问秘密而普通用户不可访问，核实进程参数、日志、配置和 API 不泄露密码。真实 PLC、压力/大库和并发 DDL 仍未覆盖。暂存区 smoke 只证明编译服务能启动，不能替代这些验收。

正式发布前仍须归档最终验收结果、数据库及工具的许可证/版权和对应源码交付材料；当前仅已带入 MariaDB 原包 COPYING/CREDITS/THIRDPARTY 与 WinSW 许可证。不要将开发候选包当作已验收发布包。

### 当前开发候选包

- 构建命令：build.bat installer，退出码 0，编译版 Launcher 输出 [smoke] ok。日志：_Build/managed-installer-build.log。
- 产物：_Build/SD_SMA_Installer_Package/SD_SMA_ManagedDB_Setup_1.0.2.exe；大小 132073975 字节；SHA-256：AC70E0F6A7B8EBA5C3EF044307F7FB79B6BD7F3847BABD6A07C379784D984C87。
- Nuitka 仍提示未内嵌 Windows Runtime DLL；安装器已包含并先行安装固定版本的微软离线运行库，不依赖客户联网。全新系统效果留待最终虚拟机验收。
- 代码提交：2782503（安装、凭据和启动），df74d6e（客户存储界面）。
