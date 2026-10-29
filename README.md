# SD SMA Runtime

独立运行时工程，仅包含统一 Launcher 及其管理的四个服务：

- SD SMA Collector
- SD SMA Query Web
- SD SMA DB Admin
- SD SMA Report Copy

另包含共享包 `SD_SMA_COMMON` 和 DB Admin 的完整数据库客户端工具源目录。

## 当前版本：v1.0.2（2026-10-29）

- 修复中文 SQL 写回错误。
- 修复 DB Admin 界面密码传输问题并优化页面布局。
- 优化时间输入与 API 限流验证。
- 安装时可选择注册为 Windows 服务或手动启动。

安装包：`SD_SMA_Setup_1.0.2.exe`，构建后位于 `_Build/SD_SMA_Installer_Package/`。

## 运行

开发机首次运行可直接执行：

```bat
start.bat
```

Launcher 管理中心使用 `http://127.0.0.1:8090/`，四个服务使用 8091–8094 端口。

## 构建

```bat
build.bat source
build.bat installer
```

构建结果写入 `_Build`。源码仓库中的配置均为脱敏模板，不包含现场密码、密钥、日志、备份或服务状态。
