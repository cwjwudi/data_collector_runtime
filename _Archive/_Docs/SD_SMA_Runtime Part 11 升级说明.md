# SD_SMA_Runtime Part 11 升级说明 —— 继承 mapp Services 合规体系

本文回答两个问题：

1. 在客户当前架构（Hypervisor + mapp Services + mappView 全屏锁定 + WebViewer 分权打开本套服务网页）下，SD_SMA_Runtime **如何继承 mapp Services 已有的 Part 11 合规能力**；
2. 为守住这个继承关系，Runtime 侧**必须补齐哪些升级**，以及分阶段的实施手段。

通用合规背景（Part 11 是什么、谁负责、第三方能做什么）见同目录《21 CFR Part 11 客户设备合规说明.md》，本文不重复。对外口径同样遵循该文结论：**只声称 Part 11 capable，不声称"认证"**。

---

## 1. 客户系统架构

```
┌─────────────────────── B&R Hypervisor（同一台工控机）───────────────────────┐
│                                                                              │
│  RTOS 侧：Automation Runtime (AR)          GPOS 侧：Windows                  │
│  ├── mapp Services                         ├── 浏览器全屏锁定（Kiosk）        │
│  │   ├── mapp UserX   ← 用户/角色/密码策略  │    └── mappView HMI            │
│  │   ├── mapp AuditTrail ← 审计追踪         │        └── WebViewer 控件 ──┐   │
│  │   └── 电子签名（UserX 重认证）            │                            │   │
│  ├── mappView Server                       ├── SD_SMA_Runtime（后台服务）◄┘   │
│  └── OPC UA Server                         │    ├── Launcher      :8090      │
│                                            │    ├── Collector     :8091      │
│           内部虚拟网络（Hypervisor 共享内存/虚拟网卡）                         │
│                                            │    ├── Query Web     :8092      │
│                                            │    ├── DB Admin      :8093      │
│                                            │    └── Report Copy   :8094      │
└──────────────────────────────────────────────────────────────────────────────┘
```

操作员唯一的交互入口是全屏锁定的 mappView：登录、权限、审计、电子签名全部由 AR 侧 mapp Services 承担；Runtime 的网页通过 WebViewer 控件呈现，页面可见性由 mapp UserX 角色控制。

## 2. 合规判定：这个方案是否成立

**结论：架构方向成立，是标准的"合规前门 + 封闭系统"继承模式；但仅靠"锁屏 + WebViewer 按权限开页"还不能声称合规，因为继承目前只发生在 UI 导航层，存在四个断点（见第 4 节）。补齐断点后，Runtime 可以作为 mapp 合规体系的组成部分，纳入客户的 Part 11 验证范围。**

判定依据：Part 11 的合规声明属于客户的**整套受控系统**，而不属于任何单个组件。mapp Services 提供的是经 B&R 验证支持的技术能力（Part 11 capable）；Runtime 要"继承"这份合法性，条件是——**不引入绕过这些控制的旁路，且自身产生的记录同样落入受控范围**。锁定 GPOS、以 mappView 为唯一入口，正是构建 §11.10(d)"限制系统访问"的正确手段；断点在于 HTTP 服务本身、操作归属和审计覆盖。

## 3. 继承了什么 —— mapp Services 承担的合规义务

以下能力**由 mapp Services 原生承担，Runtime 不重复自建**（这是本次升级最重要的设计决策：放弃任何自研用户/审计方案，包括历史上的 M1 原型）：

| Part 11 要求 | 承担组件 | Runtime 的继承方式 |
| --- | --- | --- |
| 唯一账号、角色权限 §11.10(d)(g) | mapp UserX | WebViewer 页面可见性绑定 UserX 角色；Runtime 不设自己的用户库 |
| 密码策略、锁定、会话超时 §11.300 | mapp UserX | 完全复用，Runtime 无密码体系 |
| 审计追踪 §11.10(e) | mapp AuditTrail | mappView 侧操作原生入审计；Runtime 侧操作**上交**至 AuditTrail（升级项 R3） |
| 电子签名 §11.50/11.70/11.200 | mapp UserX 重认证 | 需要签名的操作留在 mappView 侧完成，Runtime 页面不做签名控件 |
| 操作顺序强制 §11.10(f) | mappView 页面流 | 关键操作入口只存在于 mappView |

继承的直接收益：Runtime 升级范围从"自建全套 Part 11 功能"收缩为"守住继承边界"，工作量和验证面都大幅缩小。

## 4. 继承的断点 —— 现状下四个失效点

仅有 Kiosk 锁定和 WebViewer 分权时，以下路径绕过了 mapp 的控制，检查官或客户审计一问即破：

**断点 A：网络旁路。** WebViewer 只是"UI 上的门"，Runtime 的 HTTP 服务（8090–8094）监听在端口上，GPOS 上的任何进程、局域网内的任何主机都可以不经 mappView 直接调用 API 读写数据。锁屏锁不住网络请求。

**断点 B：操作无归属。** WebViewer 打开页面时，Runtime 后端并不知道背后是哪个 UserX 用户。页面里发生的任何写操作（改配置、触发导出、删数据）在记录上都是匿名的，违反可归属性（ALCOA 的 Attributable，§11.10(e) 的 Who）。

**断点 C：审计断链。** mapp AuditTrail 只记录 mappView 侧的操作。Runtime 页面内的操作、后台服务的配置变更不会出现在审计里；Runtime 现有的本地 JSONL 日志可被文件级改写，不具备防篡改性。

**断点 D：自有记录不受控。** Runtime 采集入库的数据、生成的报表文件是 Part 11 意义上的电子记录，其完整性（防改删）、时间戳可信度（GPOS 时钟无对时源）、保留归档不会因为前门合规而自动合规。

## 5. 升级方向与手段

每个断点对应一项升级，全部遵循"复用 mapp / 平台已有机制，不自建"的原则。

### R1 网络封闭（堵断点 A）—— P0

- Runtime 全部服务只绑定 Hypervisor 内部虚拟网卡地址（或 localhost），不监听外部网卡；
- Windows 防火墙白名单：8090–8094 仅允许来自 mappView Server / 内部网段的连接；
- 效果：WebViewer 成为事实上的唯一访问路径，mapp UserX 的登录与角色门禁从"UI 约定"变成"网络强制"。这是整个继承声明的地基，实施成本最低（改绑定地址 + 防火墙规则），必须最先做。

### R2 身份传递（堵断点 B）—— P0

- PLC 侧：用户在 mappView 登录后，PLC 逻辑为该会话生成短时效一次性 token（用户名 + 角色 + 过期时间），经 OPC UA 暴露；
- WebViewer 打开 Runtime 页面时在 URL 中携带 token；
- Runtime 收到请求后通过 OPC UA 回查 PLC 校验 token，校验通过则将 UserX 身份绑定到本次会话；
- 强制规则：**无有效身份的请求一律只读**，所有写操作拒绝。
- 效果：Runtime 侧每个操作都能落到具体 UserX 用户，归属链闭合。

### R3 审计上交（堵断点 C）—— P0

- Runtime 所有写操作、配置变更、数据导出、报表生成，通过 OPC UA 以自定义事件写入 mapp AuditTrail（携带 R2 传递的用户身份、时间戳、旧值/新值）；
- 审计的存储、防篡改、查阅、归档全部复用 AuditTrail 的合规实现，Runtime 不自建防篡改存储；
- 本地 JSONL 日志降级为技术诊断日志，从合规声明中移除；删除其中的原地改写（coalesce）逻辑，避免审计员误解。
- 效果：全系统只有一条审计链，且它已经是合规的那条。

### R4 页面按角色分级（强化继承）—— P1

- Runtime 网页明确划分为只读页（趋势/查询/报表浏览）与操作页（DB Admin、配置、导出）；
- 操作页仅对 R2 校验出的高权限角色开放，与 mappView 侧的 WebViewer 可见性配置一一对应，双层一致；
- 默认只读：新增页面未显式声明权限时按只读处理。

### R5 自有记录完整性（堵断点 D 之一）—— P1

- 采集数据表与审计相关表使用仅 INSERT 权限的专用数据库账号（append-only）；
- 报表 PDF 生成时计算 SHA-256，连同生成人、时间经 R3 通道记入 AuditTrail，提供校验比对工具；
- 证书级 PDF 签章（PAdES）暂缓，客户审计明确要求时再立项。

### R6 时间同步（堵断点 D 之二）—— P1

- AR 侧配置 SNTP 客户端指向站点时间源；
- GPOS Windows 通过 Hypervisor 时间服务或同一 NTP 源对时；
- Runtime 全部时间戳统一 UTC 存储、本地时区展示。审计里 AR 侧与 Runtime 侧时间戳必须可对齐，否则审计链在时间轴上自相矛盾。

### R7 Kiosk 加固（客户 IT 协同项）—— P1

- Windows Assigned Access / Shell 替换实现真正的 Kiosk（而非仅浏览器全屏）；
- 禁用 USB 存储、任务管理器、快捷键逃逸；
- 该项属于客户设备管控 SOP 范畴，由我方提供配置基线，客户 IT 落地并纳入其变更控制。

## 6. 升级后的合规声明方式

升级完成后，对客户/审计方的正确表述为：

> SD_SMA_Runtime 作为 B&R mapp Services 合规体系内的数据采集与报表组件运行：系统唯一操作入口为 mappView（mapp UserX 认证与授权），Runtime 服务仅在系统内部网络可达，所有可归属操作经由 mapp UserX 身份执行并记录于 mapp AuditTrail，记录时间戳与 AR 同源。Runtime 为 Part 11 capable 组件，随整机纳入客户的计算机化系统验证（CSV）范围。

配套义务（缺一则上述声明不成立）：

- Runtime 纳入客户 URS / 风险评估 / IQ-OQ-PQ 验证包，作为受控组件出现；
- 我方提供：功能规格、R1–R7 配置基线、版本发布说明；Runtime 升级走客户变更控制；
- 客户侧 SOP 覆盖：账号管理（UserX）、审计追踪定期复核、备份、时间同步核查。

## 7. 实施优先级汇总

| 阶段 | 项目 | 一句话验收标准 |
| --- | --- | --- |
| P0 | R1 网络封闭 | 从外部网卡访问 8090–8094 全部失败；WebViewer 内访问正常 |
| P0 | R2 身份传递 | 无 token 的写请求被拒；审计中每条写操作有 UserX 用户名 |
| P0 | R3 审计上交 | 在 Runtime 页面改一个配置，mappView 审计页能查到该条目 |
| P1 | R4 页面分级 | 低权限用户在 WebViewer 中看不到、直连也打不开操作页 |
| P1 | R5 记录完整性 | 用应用账号 UPDATE 审计/采集表失败；报表哈希可校验 |
| P1 | R6 时间同步 | AR 与 Windows 时钟偏差在阈值内，审计时间轴一致 |
| P1 | R7 Kiosk 加固 | 现场无法从全屏 HMI 逃逸到桌面 |

P0 三项完成后即可开始按第 6 节口径与客户对齐验证计划；P1 完成后整体进入客户 CSV 流程。
