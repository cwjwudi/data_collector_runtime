# 21 CFR Part 11 客户设备合规说明

本文说明客户设备如何满足 FDA 21 CFR Part 11（电子记录 / 电子签名），以及第三方机构实际能做什么。  
**结论：FDA 不颁发 Part 11 证书。** 客户设备要做的不是“拿一张官方认证”，而是证明电子记录和电子签名在实际使用中可靠、可追溯、可接受检查。

本文是合规路径说明，不是法律意见。具体项目是否适用 Part 11，以客户所在行业的 predicate rule、实际记录使用方式，以及质量/法规负责人为准。

---

## 1. Part 11 是什么

21 CFR Part 11 是美国 FDA 对**电子记录、电子签名**的法规，规定在什么条件下电子记录/电子签名可以等同于纸质记录和手写签名。

它**不是** CE、UL、ISO 13485、510(k) 那种产品认证体系。市场上常见的 “Part 11 Certified” 基本是供应商或咨询公司的符合性声明，**FDA 不承认、也不授权任何第三方发证**。

官方文本与现行解释：

- 法规：[21 CFR Part 11](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11)
- 范围与执行尺度：[Part 11, Electronic Records; Electronic Signatures — Scope and Application](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/part-11-electronic-records-electronic-signatures-scope-and-application)（FDA, 2003）

FDA 对该法规作**窄解释**：只有 predicate rule 要求保留、且实际业务依赖电子形式的记录，才按 Part 11 管。法规本身仍有效；检查时仍会看访问控制、权限、电子签名、系统文档等控制是否到位。

---

## 2. 谁真正要满足这项要求

义务主体是**受 FDA 监管、并且用电子记录替代纸质记录的使用方**（药厂、器械厂、临床、实验室等），不是设备出厂标签。

Part 11 通常在同时满足以下条件时适用：

1. 记录属于 predicate rule 要求保留的记录，例如：
   - 21 CFR 211（药品 GMP）
   - 21 CFR 820（医疗器械质量体系）
   - 21 CFR 58（GLP）
   - 以及其它 FDA 要求提交或保存的记录
2. 实际业务**依赖电子记录**做放行、审核、追溯，而不只是打印一张纸。

判定参考：

- 设备只出纸质报告，且工厂完全以纸质为准，一般不触发 Part 11。
- 操作员在电脑上改数据、电子签字、电子放行，就可能适用。
- 电子和纸质并存（hybrid）时，以实际依赖哪份记录为准，并应书面记录该决定。

设备商能做的是把系统做成 **Part 11 capable / ready**（功能具备），并提供验证资料包。客户现场的账号、SOP、备份、变更控制，才是 FDA 检查时看的内容。

---

## 3. 客户设备实际怎么做

常见路径：

**法规适用判断 → 差距分析 → 技术能力 → 现场验证 → 程序与培训 → 持续维护**

需要对外背书时，再请第三方做评估，便于销售和客户审计。

### 3.1 先判定要不要做

列出这台设备产生、存储、传输的记录（批次数据、报警、校准、电子签名、审计追踪等），对照客户所在行业的 predicate rule，写成书面结论。

建议对每类记录事先决定：放行和审核是依赖电子记录，还是依赖纸质记录。该决定可写在 SOP 或规格文件中。

### 3.2 技术能力（设备侧必须具备）

对照 Part 11 核心条款，设备至少要能支持：

| 能力 | 对应要求 |
| --- | --- |
| 唯一用户、权限、登录控制 | §11.10(d)(g)、§11.300 |
| 计算机生成、带时戳、不可随意关闭的审计追踪 | §11.10(e) |
| 记录防篡改；修改后仍能看到旧值 | §11.10(a)(e) |
| 电子签名：签署含义、日期时间、不可否认 | §11.50、§11.70、§11.200 |
| 签字与记录绑定，拷贝后仍能对应 | §11.70 |
| 记录长期可读，并可拷贝给检查官 | §11.10(b)(c) |
| 系统文档、变更受控 | §11.10(k) |

**功能具备不等于已经合规。** 没有现场验证和 SOP，功能等于没有落地。

### 3.3 现场验证（客户侧必须做）

行业标准做法是 GAMP 5 风险分级，再做 IQ / OQ / PQ；或按 FDA 近年的 Computer Software Assurance（CSA）按风险减负。CSA 是减少低风险过度测试，不是免验证。

典型交付物：

- URS
- 风险评估
- 功能 / 设计规格
- 供应商评估
- IQ / OQ / PQ 报告及偏差
- 培训记录
- SOP（账号、备份、时间同步、变更、审计追踪复核）

FDA 检查看的是这套包，不是墙上的“认证牌”。

### 3.4 程序与人员

书面规定：谁能签、签了负什么责、密码怎么管、离职如何关闭账号。  
技术再好，共用账号或关闭审计追踪，检查同样无法通过。

### 3.5 上市后 / 交付后维护

升级、打补丁、换数据库、改权限，都要走变更控制，并评估对审计追踪和电子签名的影响。

---

## 4. 有没有第三方机构

**有机构做评估、验证、差距分析；没有机构能代替 FDA 颁发 Part 11 证书。**

第三方实际能提供：

- 差距分析 / 符合性评估报告
- 计算机化系统验证（CSV）或 CSA 验证包
- 供应商审计、数据完整性（ALCOA+）审计
- 给客户看的 Part 11 assessment / statement of compliance

常见服务类型（均为商业服务，不是 FDA 授权发证）：

| 类型 | 例子 | 实际产出 |
| --- | --- | --- |
| 国际检测 / 认证公司兼做生命科学评估 | SGS、TÜV SÜD、Bureau Veritas、Intertek、UL、NSF | 符合性评估、验证支持 |
| 专业 CSV / 数据完整性咨询 | USDM、Ofni、GAMP 咨询公司、四大生命科学团队 | 差距分析、验证包、审计 |
| 国内 GMP / CSV 咨询 | 各地验证咨询公司 | 验证报告或符合性评估 |

这类报告的价值是：客户采购尽职调查、内部审计、应对客户问卷。FDA 现场检查时，检查官仍会自己看系统、抽审计追踪、问操作员，不会因为有第三方证书就跳过。

人员可以考 ISPE / GAMP 相关资质。那是**人员能力**，不是设备认证。

国家药监局和 FDA 都不会颁发 Part 11 证书。

---

## 5. 和真正“认证”的区别

| 事项 | 是否官方认证 | 说明 |
| --- | --- | --- |
| 21 CFR Part 11 | 否 | 符合性 + 验证 + 检查 |
| FDA 510(k) / PMA / De Novo | 是（器械上市路径） | 管设备能不能卖，不管电子记录是否满足 Part 11 |
| ISO 13485、IEC 62304 | 是（管理体系 / 软件过程） | 有帮助，不能替代 Part 11 |
| ISO 27001 | 是（信息安全） | 有帮助，范围不同 |
| EU GMP Annex 11 | 否（欧盟计算机化系统要求） | 欧洲对应思路，同样不是证书 |

卖到美国药厂或器械厂的设备，客户通常会同时要：

- Part 11 能力说明
- 验证支持包
- 数据完整性说明

若设备本身是医疗器械，再另走 510(k) 等上市路径。两件事可以并行，不能互相替代。

---

## 6. 对外口径

设备商对外应说：**Part 11 capable，并提供验证支持包**。  
不要说 “FDA Part 11 认证通过”。

客户侧最短闭环：

1. 书面判定哪些记录适用 Part 11
2. 设备功能覆盖审计追踪、权限、电子签名、记录保全
3. 现场 IQ / OQ / PQ（或同等风险验证）
4. SOP + 培训 + 变更控制
5. 需要对外背书时，再请 SGS、TÜV 或 CSV 咨询公司做一次独立评估

---

## 7. 参考

- [21 CFR Part 11 — Electronic Records; Electronic Signatures](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11)
- [FDA Guidance: Part 11, Electronic Records; Electronic Signatures — Scope and Application](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/part-11-electronic-records-electronic-signatures-scope-and-application)
- FDA *General Principles of Software Validation*
- ISPE GAMP 5（计算机化系统验证行业方法）
- 数据完整性原则 ALCOA+（Attributable, Legible, Contemporaneous, Original, Accurate，以及 Complete, Consistent, Enduring, Available）
