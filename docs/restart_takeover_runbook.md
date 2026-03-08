# 重启接管运行手册（有持仓场景）

## 目标
当系统重启时，在不丢失仓位上下文的情况下恢复 OMS 并与交易所状态对账，避免重复下单/裸仓。

## 标准流程
1. 使用 `JsonlOMSStore` + `OrderManager.recover` 恢复本地订单和仓位快照。
2. 启动执行服务后先调用 `reconcile()` 拉取交易所 `open_orders` 与 `positions`。
3. 运行 `bootstrap_recover_and_reconcile(...)` 生成接管报告：
   - `position_diffs`
   - `missing_on_exchange`
   - `unmanaged_on_exchange`
4. 报告 `ok=false` 时，不进入 live 下单，先人工处理差异。
5. 报告 `ok=true` 后，先小流量验证再恢复常规执行。

## 建议
- 重启默认 `dry_run=True`，对账通过后再放开。
- 重启后当日执行 `replay-daily` 做复盘闭环。
