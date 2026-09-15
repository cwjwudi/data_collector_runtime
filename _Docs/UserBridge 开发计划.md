# UserBridge（HMI 用户身份桥）开发计划

日期：2026-09-15  
目标工程：`ar_test/SD_SMA_SCADA_DEMO`（AS 4.12.6 / mapp 5.24.3 / mappView 5.24.4 / AR J4.93，配置 `cp1586`）  
对应清单项：C03（匿名/共享口令访问）、C05 前置（用户身份来源）

## 1. 现状（决定方案的事实）

| 事实 | 依据 | 影响 |
|---|---|---|
| mappView 认证模式 = `AuthenticationModeRbac`，未装 MpUserX / MpAudit | `Config.mappviewcfg`、`mappServices/Package.pkg` 为空 | 用户与角色全部来自 AR UserRoleSystem（`User.user` / `Role.role`），身份桥直接用 `ArUser` 库读角色，不依赖 MpUserX；将来加 MpUserX 5.x 也兼容（它仍落在 UserRoleSystem 上） |
| `::SYSTEM:clientInfo.slotId / userId / isValid / ipAddress` 在 5.24.4 存在 | `TechnologyPackages/mappView/5.24.4/Variables/SystemVariables.svar` | 可用 slotId 做列表绑定选择器（B&R FAQ 方案） |
| WebViewer `query` 属性可绑定 | `WebViewer.widget` `projectable="true"` | 一次性 code 通过 URL query 注入 Runtime 页面 |
| AS 4.12 支持 `.uam` 方法声明 + `UaSrv_MethodOperate` | `ObjectCatalog/Elements/OpcUaMethods`、`Library_2/AsOpcUas` | 用 OPC UA Method 做原子换票，避免变量握手竞态 |
| Runtime 五个服务分布在 8091–8094 等端口 | `Config.content` 内 4 个 WebViewer | Cookie 不按端口隔离：一次换票、host 级 Cookie 五个服务共用 |
| AR 4.x 无密码学随机源 | `ArSsl` 无随机函数，`BrRand` 为 libc `rand()` | code 用 BrRand + seq 混合，标注 `ponytail:` 升级路径 |

## 2. 方案

```
mappView 客户端 ──(session 列表绑定, slotId 选择)──> gSdSmaClient[slot]  (isValid/userId/ipAddress/slotId)
                                                      │
                                    UserBridge 任务：检测登录/登出边沿
                                                      │  seq++ ; 生成 32 位 hex code ; ArUser 查角色
                                                      ▼
                                              gSdSmaSession[slot]  (seq/active/userId/roles/codeQuery)
                                                      │
WebViewer.query <──(列表绑定, slotId 选择)──────────── codeQuery = "sdsma_code=<hex>"
      │  iframe 打开 http://localhost:809x/config?sdsma_code=...
      ▼
Runtime 服务 ──OPC UA Method SdSmaExchange(code)──> PLC 校验：active ∧ 未用过 ∧ 角色已解析
                                                      返回 Status/Slot/Seq/UserId/Roles/IpAddress ; codeUsed := TRUE
Runtime 设 host 级签名 Cookie{slot,seq,user,roles} ; 每次请求读 gSdSmaSession[slot].seq 校验会话仍有效
```

规则：
- code 与一次登录绑定（不设 TTL，用户可能登录后几分钟才打开 Config 页）；只可换票一次；登出/换用户 seq++ 并清空。
- 未登录（`userId = 'Anonymous'` 或 `isValid = FALSE`）→ `active = FALSE`，`codeQuery = ''`，Runtime 得不到 code。
- 角色未解析完成时换票返回 `Status = 3`，Runtime 重试；ArUser 出错则 `roles = ''`（拒绝优先）。
- Method 返回 Status：0 OK / 1 code 未知 / 2 已使用 / 3 角色解析中。

## 3. 文件清单（本次）

PLC（`Logical/`）
- `Libraries/ArUser`、`Libraries/AsOpcUas`、`Libraries/AsOpcUac`：从 `Library_2` 复制，登记 `Libraries/Package.pkg`
- `SMA/GlobalUser.typ`、`SMA/GlobalUser.var`：`SdSmaClientInfo_typ`、`SdSmaSession_typ`、`gSdSmaClient[0..3]`、`gSdSmaSession[0..3]`
- `SMA/UserBridge/{IEC.prg, Main.st, Variables.var, Methods.uam}`：边沿检测、code 生成、ArUser 角色查询、`SdSmaExchange` 方法处理

配置（`Physical/cp1586/5APC3100_KBU2_000/`）
- `Cpu.sw`：任务 `UserBridge`（Cyclic#4），库 ArUser/AsOpcUas/AsOpcUac
- `Connectivity/OpcUA/OpcUaMap.uad`：暴露 `gSdSmaClient`、`gSdSmaSession`，启用方法 `SdSmaExchange`
- `mappView/content_SdSma.binding`：4 条 clientInfo→PLC 列表绑定；4 条 `codeQuery`→WebViewer.query 列表绑定（8091/8092/8093/8094）
- `mappView/Package.pkg`、`mappView/Visualizat.vis`：登记绑定集

## 4. 验证（ArSim, as4_default 工具链）

1. 构建 `cp1586` 0 error → 启动 ArSim → 下载。
2. Python `asyncua` 客户端（模拟 mappView 写 `gSdSmaClient[0]`）：
   - 写 `userId='br', isValid=TRUE` → `gSdSmaSession[0].active=TRUE, seq=1, codeQuery 非空, roles='Administrators,Everyone'`
   - 调 `SdSmaExchange(code)` → Status 0，UserId/Roles 正确
   - 再调一次 → Status 2
   - 错误 code → Status 1
   - 写 `userId='Anonymous'` → `active=FALSE, seq=2, codeQuery=''`
3. mappView 浏览器手动验证：登录 `br` 后 Config 页 iframe URL 带 `?sdsma_code=`（需人工在浏览器完成）。

### 4.1 验证结果（2026-09-15，ArSim J4.93 / AS 4.12.6）

- 第 2 步 Python 闭环全部通过（login→code→Exchange=0→重放=2→错码=1→logout 清空）。
- 第 3 步已在浏览器完成：mappView 登录 `br` 后 `gSdSmaClient[0]={br,127.0.0.1}`、`gSdSmaSession[0].roles='Administrators,Everyone'`，Config 页四个 iframe 的 src 均带同一个 `?sdsma_code=<32hex>`。
- 期间发现并修复一个致命缺陷：ArUser FB 在 Busy 时翻转 `Execute`、或列表销毁后再调用 `ArUserGetFirst/GetNext`，会触发 `ArException`（`syncArUserGetFirst`），ArSim 重启进入 Service。现改为“Execute 保持 TRUE 直到 Done/Error，同周期复位再调一次”，且销毁列表后不再触碰 FB。
- ArSim 部署经验：`plc_download_ruc` 对空白 ArSim 会报 `An initial installation is required` 然后挂起；需用 PVITransfer `InstallMode=ForceInitialInstallation InstallRestriction=AllowPartitioning` 并带 `-Conn` 参数（pil 内 Connection 行无效，2 秒“SUCCESSFUL”实际未写入）。之后的增量用 `Consistent`。异常后 ArSim 停在 Service，用 pil `WarmStart` 恢复。

## 5. 后续阶段（不在本次）

- P2 权限收紧：新增角色 `SdSmaRuntime` + 服务账号（需 AS 编辑器生成口令哈希），`.uad` ACL：`gSdSmaSession` 与方法仅该角色可读/可调；`gSdSmaClient` 保持 Everyone 可写（mappView 客户端写入需要）。
- P3 Runtime 侧：`SD_SMA_COMMON` 增加 `SessionBridge`（asyncua 调方法、签名 Cookie、seq 活性校验），五个服务中间件替换 loopback 放行与共享 PIN。
- P4 MpAudit：换票成功/失败、会话失效写 MpAuditCustomEvent（"MpAudit 锚 + Runtime 完整记录"）。

## 6. 已知上限（ponytail）

- code 随机源为 libc `rand()` 混合 seq/时间，非密码学安全；升级路径：AR 6 `ArCrypto`/`AsCrypt` 随机或让 Runtime 提供挑战值由 PLC HMAC 签名。
- `gSdSmaSession[*].codeQuery` 对所有 HMI 客户端可读（OPC UA ACL 无法按数组元素区分）；当前 `MaxClientConnections=1` 无影响，多客户端时依赖一次性消费 + P2 ACL。
- 每登录一个 code、五个 WebViewer 共用：靠 host 级 Cookie；首个换票成功后其余 iframe 携带的已用 code 被 Runtime 忽略（有 Cookie 即可）。
