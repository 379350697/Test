# 个人实盘 Go/No-Go 清单（QuantX）

> 目标：在不引入企业级复杂流程的前提下，用最小动作保障个人实盘安全。

## A. 安全（必须全部满足）
- [ ] API Key 仅从环境变量读取（不进 Git、不写代码常量）。
- [ ] 交易所权限最小化：仅交易权限，关闭提现权限。
- [ ] 账户启用 2FA 与设备风控（交易所侧）。
- [ ] 运行机器开启磁盘加密/登录口令。

## B. 上线前演练（必须）
- [ ] `dry_run=True` 连续运行 >= 24h。
- [ ] readiness 检查全绿（`assert_ready` 不抛错）。
- [ ] 白名单 symbols/每轮订单数/每轮名义金额上限已配置。
- [ ] 异常告警链路可达（webhook 可正常发送）。

## C. 灰度上线（必须）
- [ ] 首日仅 1-2 个白名单品种。
- [ ] 单笔风险和 max_order_notional 设置为平时 20%-30%。
- [ ] 观察日志与告警连续 2-3 个交易周期无异常后再放大。

## D. 回滚策略（必须）
- [ ] 出现连续下单失败/风控误触发时立即切回 `dry_run=True`。
- [ ] 保留最近 N 天 JSONL 日志与 OMS/Audit 文件用于复盘。
- [ ] 每次参数变更记录一行变更日志（时间、参数、原因）。

## E. 推荐命令（上线前）
```bash
pytest -q
python -m ruff check .
python -m mypy quantx tests
python -m pip_audit
python -m bandit -q -r quantx -c .bandit
```


## F. 日终复盘（推荐）
```bash
quantx replay-daily --events runtime/events.jsonl --oms runtime/oms/events.jsonl --audit runtime/audit/events.jsonl --json
```
