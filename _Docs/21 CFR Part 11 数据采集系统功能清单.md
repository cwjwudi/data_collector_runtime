# 21 CFR Part 11 数据采集系统功能清单

## 一、用户与身份管理

### 1. 独立用户账号
**优先级：必须做**

- [ ] 每个用户必须拥有唯一账号
- [ ] 禁止多人共用同一操作账号
- [ ] 用户账号必须可启用、停用、锁定
- [ ] 删除用户时，不得删除其历史操作记录
- [ ] 历史记录中必须永久保留原 User ID

建议数据库字段：

```text
User
- UserID
- LoginName
- DisplayName
- PasswordHash
- Status
- CreatedAt
- DisabledAt
- LastLoginAt
```

---

### 2. 密码策略
**优先级：必须做**

- [ ] 密码必须加密存储，不得明文保存
- [ ] 支持最低密码长度
- [ ] 支持复杂度策略
- [ ] 支持连续登录失败锁定
- [ ] 支持密码修改
- [ ] 修改密码需要验证原密码
- [ ] 建议支持密码有效期
- [ ] 不得允许管理员查看用户原密码

建议记录：

```text
PasswordPolicy
- MinLength
- RequireNumber
- RequireUppercase
- RequireSpecialChar
- ExpireDays
- MaxFailedAttempts
- LockoutMinutes
```

---

### 3. 登录与会话管理
**优先级：必须做**

- [ ] 用户登录必须进行身份验证
- [ ] 登录成功必须生成 Session
- [ ] Session 应具有超时机制
- [ ] 长时间无操作后自动退出
- [ ] 用户退出必须注销 Session
- [ ] 一个 Session 必须可以追溯到具体用户
- [ ] 关键操作应重新验证用户身份

建议记录：

```text
Session
- SessionID
- UserID
- LoginTime
- LogoutTime
- ClientIP
- ComputerName
- ApplicationVersion
```

---

## 二、权限控制

### 4. 角色权限体系
**优先级：必须做**

建议至少设置：

```text
Operator
Engineer
Supervisor
Administrator
Auditor
```

权限需要细分，例如：

```text
查看数据
导出数据
修改批次信息
修改系统配置
确认报警
修改工艺参数
管理用户
管理权限
查看 Audit Trail
执行电子签名
```

必须满足：

- [ ] 不同角色拥有不同权限
- [ ] 默认最小权限原则
- [ ] 权限变更必须记录
- [ ] 普通用户不能提升自身权限
- [ ] 管理员也不能删除 Audit Trail

---

### 5. 数据库权限隔离
**优先级：强烈建议**

采集服务使用独立数据库账号，例如：

```text
collector_user
```

仅允许：

```text
INSERT
SELECT
```

针对原始数据表建议禁止：

```text
UPDATE
DELETE
DROP
TRUNCATE
```

应用程序用户不要直接拥有数据库管理员权限。

---

# 三、原始数据完整性

## 6. 原始采集数据不可覆盖
**优先级：必须做**

推荐 Raw Data 表采用：

```text
INSERT ONLY
```

例如：

```text
RawProcessData
- RecordID
- Timestamp
- TagID
- Value
- Quality
- BatchID
- Source
- SequenceNumber
```

必须满足：

- [ ] 原始记录一旦写入不得直接覆盖
- [ ] 不允许业务程序直接修改原值
- [ ] 不允许普通用户删除原始记录
- [ ] 如果需要修正，采用 Correction Record

---

## 7. 数据修正机制
**优先级：必须做，如果业务允许修改数据**

不得：

```sql
UPDATE RawData SET Value = xxx
```

建议：

```text
Correction
- CorrectionID
- OriginalRecordID
- OriginalValue
- CorrectedValue
- Reason
- UserID
- Timestamp
- SignatureID
```

必须满足：

- [ ] 原始值永久保留
- [ ] 新值可追溯
- [ ] 修改人可追溯
- [ ] 修改时间可追溯
- [ ] 修改原因必须填写
- [ ] 修改行为进入 Audit Trail

---

## 8. 数据唯一性和顺序检查
**优先级：强烈建议**

建议采集数据带：

```text
SequenceNumber
```

例如：

```text
10001
10002
10003
10004
```

可检测：

```text
重复数据
丢失数据
乱序数据
```

对于 OPC UA 采集，可以同时记录：

```text
SourceTimestamp
ServerTimestamp
ReceiveTimestamp
```

---

## 9. 数据质量状态
**优先级：强烈建议**

OPC UA 数据不要只保存 Value。

建议同时保存：

```text
Value
Quality
SourceTimestamp
ServerTimestamp
```

例如：

```text
Value = 82.3
Quality = Good
```

或：

```text
Quality = BadCommunicationError
```

这样才能区分：

```text
真实值为 0
```

和：

```text
通信故障导致数据无效
```

---

# 四、Audit Trail 审计追踪

## 10. Audit Trail 核心功能
**优先级：必须做**

Audit Trail 至少包含：

```text
AuditID
Timestamp
UserID
SessionID
Action
ObjectType
ObjectID
OldValue
NewValue
Reason
ComputerName
ClientIP
ApplicationVersion
```

---

## 11. 必须记录的操作

### 用户管理

- [ ] 用户创建
- [ ] 用户启用
- [ ] 用户禁用
- [ ] 用户锁定
- [ ] 用户解锁
- [ ] 密码重置
- [ ] 权限变更

### 登录行为

- [ ] 登录成功
- [ ] 登录失败
- [ ] 用户退出
- [ ] Session 超时
- [ ] 账号锁定

### 数据操作

- [ ] 数据修改
- [ ] 数据修正
- [ ] 批次信息修改
- [ ] 数据删除尝试
- [ ] 数据导出

### 配置操作

- [ ] OPC UA 服务器修改
- [ ] Tag 增加
- [ ] Tag 删除
- [ ] Tag 修改
- [ ] 采样周期修改
- [ ] 报警参数修改
- [ ] 配方修改
- [ ] 数据保留策略修改

### 系统操作

- [ ] 软件启动
- [ ] 软件停止
- [ ] 采集服务启动
- [ ] 采集服务停止
- [ ] 数据库连接中断
- [ ] OPC UA 连接中断
- [ ] 时间修改
- [ ] 版本升级

---

## 12. Audit Trail 不可篡改
**优先级：必须做**

必须做到：

- [ ] 普通用户不能修改 Audit Trail
- [ ] 普通管理员不能删除 Audit Trail
- [ ] Audit Trail 不允许直接编辑
- [ ] Audit Trail 必须跟随数据保存周期保存

建议：

```text
INSERT ONLY
```

更加严格可以增加：

```text
Hash
PreviousHash
```

形成链式校验。

---

# 五、电子签名

## 13. 电子签名
**优先级：视业务而定**

如果系统涉及：

```text
审批
放行
审核
确认
批次关闭
关键参数修改
```

建议加入电子签名。

签名至少包括：

```text
UserID
Timestamp
Meaning
RecordID
```

Meaning 示例：

```text
Reviewed
Approved
Released
Verified
Confirmed
```

---

## 14. 电子签名重新认证
**优先级：必须做，如果启用电子签名**

重要操作不能单纯点击：

```text
确认
```

建议弹窗要求：

```text
用户名
密码
签名含义
```

然后重新验证身份。

---

## 15. 签名与记录绑定
**优先级：必须做**

签名必须绑定：

```text
具体数据
具体版本
具体批次
```

防止：

```text
先签名
然后修改记录
```

修改后应使原签名失效或者形成新版本。

---

# 六、批次管理

## 16. 批次生命周期
**优先级：强烈建议**

建议定义：

```text
Created
Running
Completed
Reviewed
Approved
Archived
```

状态变化必须：

- [ ] 有权限控制
- [ ] 有时间戳
- [ ] 有操作用户
- [ ] 写入 Audit Trail

---

## 17. 批次数据锁定
**优先级：强烈建议**

批次完成之后：

```text
Completed
```

原则上禁止修改原始过程数据。

批准以后：

```text
Approved
```

应进一步限制修改。

---

# 七、时间管理

## 18. 统一时间源
**优先级：必须做**

建议：

```text
PLC
IPC
Database Server
Application Server
```

统一通过：

```text
NTP
```

同步时间。

---

## 19. 时间修改记录
**优先级：必须做**

如果系统时间发生改变：

```text
2026-08-25 10:00
→
2026-08-25 09:55
```

必须记录：

```text
旧时间
新时间
操作用户
原因
```

---

## 20. 时间存储规范
**优先级：强烈建议**

数据库推荐保存：

```text
UTC
```

显示层再转换：

```text
Local Time
```

并记录时区。

---

# 八、系统配置管理

## 21. 配置版本管理
**优先级：强烈建议**

例如：

```text
OPC UA Endpoint
NodeID
SamplingInterval
AlarmLimit
BatchRule
```

发生变化时不要直接覆盖而不留记录。

建议：

```text
ConfigVersion
ConfigHistory
```

---

## 22. 配置修改原因
**优先级：强烈建议**

关键配置修改时要求输入：

```text
Reason for Change
```

例如：

```text
Changed sampling interval from 1000 ms to 500 ms.

Reason:
Production requirement CR-2026-031
```

---

# 九、软件版本管理

## 23. 软件版本号
**优先级：必须做**

每个正式版本必须有唯一版本号，例如：

```text
1.0.0
1.0.1
1.1.0
```

数据库最好记录：

```text
ApplicationVersion
CollectorVersion
DatabaseSchemaVersion
```

---

## 24. Change Control
**优先级：必须做**

每次正式修改要有：

```text
Change Request
Impact Assessment
Implementation
Testing
Approval
Release
```

不能：

```text
工程师现场直接改代码
→
编译
→
替换 EXE
```

完全没有记录。

---

# 十、数据库设计

## 25. 推荐核心表

建议至少包含：

```text
User
Role
Permission
UserRole

Session

RawData
Batch

Correction

AuditTrail

ElectronicSignature

SystemEvent

Configuration
ConfigurationHistory

SoftwareVersion
```

---

# 十一、系统事件

## 26. 系统事件记录
**优先级：强烈建议**

至少记录：

```text
应用程序启动
应用程序关闭

OPC UA Connected
OPC UA Disconnected

Database Connected
Database Disconnected

Collection Started
Collection Stopped

Buffer Full
Write Failed
Recovery Started
Recovery Completed
```

---

# 十二、采集可靠性

## 27. 断线缓存
**优先级：强烈建议**

例如数据库断开：

```text
PLC
 ↓
Collector
 ↓
Database X
```

此时不能简单丢弃数据。

建议：

```text
Local Buffer
```

数据库恢复后：

```text
Re-Send
```

---

## 28. 数据补传追踪
**优先级：强烈建议**

补传数据建议记录：

```text
OriginalTimestamp
ReceiveTimestamp
InsertTimestamp
SequenceNumber
```

这样可以知道：

```text
数据实际什么时候产生
```

以及：

```text
什么时候被补写数据库
```

---

## 29. 重复数据检查
**优先级：强烈建议**

建议唯一键：

```text
SourceID
+
SequenceNumber
```

或者：

```text
TagID
+
SourceTimestamp
+
SequenceNumber
```

避免补传时产生重复记录。

---

# 十三、备份与恢复

## 30. 数据备份
**优先级：必须做**

必须建立：

```text
数据库备份
配置备份
Audit Trail 备份
```

---

## 31. 恢复测试
**优先级：必须做**

不能只是：

```text
每天备份
```

还要定期验证：

```text
备份能不能恢复
```

应形成恢复测试记录。

---

## 32. 数据保存周期
**优先级：必须做**

需要明确：

```text
Raw Data 保留多久
Audit Trail 保留多久
Batch 保留多久
Backup 保留多久
```

并与客户法规要求一致。

---

# 十四、数据导出

## 33. 人类可读格式
**优先级：必须做**

支持输出：

```text
PDF
CSV
```

等可读格式。

---

## 34. 完整电子格式
**优先级：强烈建议**

建议可以导出：

```text
CSV
XML
JSON
```

同时包含：

```text
数据
时间
质量
用户
Audit Trail
Signature
```

---

## 35. 导出审计
**优先级：强烈建议**

记录：

```text
谁
什么时候
导出了什么
哪个批次
什么时间范围
```

---

# 十五、报警与异常

## 36. 系统异常报警
**优先级：强烈建议**

至少包括：

```text
OPC UA 通信中断
数据库断开
磁盘空间不足
数据写入失败
时间同步异常
数据队列堆积
采集线程异常
```

---

## 37. 报警确认
**优先级：视业务而定**

如果允许用户确认报警，应记录：

```text
Alarm
AcknowledgedBy
AcknowledgedAt
Comment
```

---

# 十六、安全

## 38. 数据库连接安全
**优先级：强烈建议**

- [ ] 数据库密码不得直接写死在源码
- [ ] 配置文件中的密码应加密
- [ ] 尽可能使用 TLS
- [ ] 数据库账户最小权限

---

## 39. OPC UA 安全
**优先级：强烈建议**

尽量使用：

```text
Sign
或
SignAndEncrypt
```

避免长期使用：

```text
SecurityPolicy=None
```

---

## 40. 应用完整性
**优先级：建议**

可以记录：

```text
Executable Hash
Configuration Hash
```

用于确认现场软件是否被替换。

---

# 十七、系统验证文件

## 41. URS
**必须准备**

User Requirement Specification

例如：

```text
URS-DI-001
系统必须保存 OPC UA 原始采集数据。
```

---

## 42. FS
**必须准备**

Functional Specification

描述：

```text
系统采用 RawData 表保存采集数据。
RawData 禁止 UPDATE 和 DELETE。
```

---

## 43. SDS / Design Specification
**强烈建议**

包括：

```text
软件架构
数据库结构
服务划分
权限模型
网络结构
数据流
```

---

## 44. Risk Assessment
**必须准备**

分析：

```text
数据丢失
数据修改
用户冒用
数据库故障
通信故障
时间错误
```

可能导致的影响。

---

## 45. IQ
**视部署情况**

Installation Qualification

检查：

```text
服务器
操作系统
数据库版本
软件版本
配置
网络
```

---

## 46. OQ
**必须准备**

Operational Qualification。

例如：

```text
OQ-AUD-001

操作：
Engineer01 修改批次名称。

Expected:
生成 Audit Trail。

包含：
UserID
OldValue
NewValue
Timestamp
Reason
```

---

## 47. PQ
**视客户要求**

Performance Qualification。

在实际使用环境中验证系统：

```text
连续采集
实际批次
实际 PLC
实际 OPC UA
实际数据库
```

---

## 48. Traceability Matrix
**必须准备**

建立：

```text
法规
↓
URS
↓
FS
↓
测试
```

例如：

```text
21 CFR Part 11
11.10(e)

↓
URS-AUD-001

↓
FS-AUD-001

↓
OQ-AUD-001
```

---

# 十八、建议的软件整体架构

建议：

```text
PLC
 │
 │ OPC UA
 ▼
Acquisition Service
 │
 ├── Buffer
 ├── Sequence Check
 ├── Quality Check
 │
 ▼
Raw Data Database
 │
 ├── RawData
 ├── Batch
 └── SystemEvent

Application API
 │
 ├── Authentication
 ├── Authorization
 ├── Audit Trail
 ├── Electronic Signature
 └── Configuration Management

Web / Desktop UI
 │
 ├── Trending
 ├── Batch
 ├── Report
 ├── Audit Trail
 └── Administration
```

---

# 十九、开发优先级

## 第一阶段：必须优先完成

- [ ] 独立用户
- [ ] Role / Permission
- [ ] 密码安全
- [ ] Session
- [ ] Raw Data Append Only
- [ ] Audit Trail
- [ ] 修改原因
- [ ] 系统时间同步
- [ ] 软件版本管理
- [ ] 数据备份
- [ ] 数据恢复测试

---

## 第二阶段：建议完成

- [ ] Correction
- [ ] Batch Lock
- [ ] Configuration History
- [ ] Sequence Number
- [ ] OPC UA Quality
- [ ] 断线缓存
- [ ] 自动补传
- [ ] System Event
- [ ] 数据导出审计

---

## 第三阶段：面向正式制药客户

- [ ] Electronic Signature
- [ ] Signature Reauthentication
- [ ] IQ
- [ ] OQ
- [ ] PQ
- [ ] Risk Assessment
- [ ] Traceability Matrix
- [ ] Change Control
- [ ] 第三方 Part 11 Gap Assessment

---

# 二十、第三方审查前最低建议状态

在联系第三方做 21 CFR Part 11 Assessment 之前，系统至少应该已经具备：

```text
User / Role
        ↓
Authentication
        ↓
Authorization

Raw Data
        ↓
Append Only

所有关键修改
        ↓
Audit Trail

关键确认
        ↓
Electronic Signature

软件修改
        ↓
Version + Change Control

系统功能
        ↓
URS → FS → OQ

法规要求
        ↓
Traceability Matrix
```

如果这些都已经具备，再请第三方做 Gap Assessment，通常会比“先请第三方，再边审边开发”效率高很多。