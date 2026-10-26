# web_config

`SD_SMA_DATA_COLLECTOR` 的独立配置网页模块。

## 功能

- 读取/保存采集配置模板
- 配置校验（复用 `core.config_loader.ConfigLoader`）
- 导出配置文件
- 直写 `config/`（自动备份 + 原子替换）
- OPC UA 浏览并一键生成 `points` 配置
- 顶部常驻操作反馈，加入点位、校验和保存后无需滚动到底部查看结果
- “数据库”页签提供持久化队列开关及完整参数配置；启用后数据先写入 SQLite outbox，异常重启可恢复待写数据
- 数据库密码以 `password` 写入本机配置，页面不回显且不会写入 localStorage
- 数据组可选择外部 OPC UA 启用点位（`1/True` 启用、`0/False` 停用）

## 约束

- 密码配置无需独立密钥；配置文件包含密码，不得提交或公开
- 仅支持采集配置字段：
  - `communications / connections / points / groups / database / logging`
- 已删除历史查询任务配置：
  - 禁止 `trigger=query`
  - 禁止 `groups[].query_config`
  - 禁止 `groups[].output_mode`
  - 禁止顶层 `http_server`

## 启动

```bash
pip install -r ../requirements.txt
python -m uvicorn web_config.main:app --host 0.0.0.0 --port 8091
```

访问：`http://localhost:8091`

