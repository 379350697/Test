# 日志与错误码最小规范（个人版）

## 1) 结构化日志最小字段
每条日志建议至少包含：
- `ts`: ISO8601 UTC 时间
- `category`: `trade/system/alert`
- `event`: 事件名
- `level`: `DEBUG/INFO/WARN/ERROR`
- `stage`: `bootstrap/pretrade/orders/execute/reconcile/runtime`
- `symbol` / `client_order_id`（可为空）
- `payload`: 额外上下文

当前 `LogEvent` 已支持上述主字段，满足最小可观测性需求。

## 2) 错误码使用建议
- `QX-READY-*`：上线门禁与 readiness 失败
- `QX-EXEC-*`：执行链路/订单提交流程失败

建议错误信息格式：
- `QX-XXX-YYY:detail`

## 3) 个人运行环境日志轮转建议
`JsonlEventLogger` 支持按文件大小轮转：
- `max_bytes=0`：关闭轮转（默认）
- `max_bytes>0`：启用轮转
- `backup_count`：保留历史分片数量（默认 3）

示例：
```python
logger = JsonlEventLogger("runtime/events.jsonl", max_bytes=5_000_000, backup_count=5)
```
