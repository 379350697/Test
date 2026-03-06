# QuantX：区块链量化交易系统（Python 3.10+）

QuantX 是一个面向研究到部署全流程的量化交易系统，覆盖：

1. **回测**：支持 `N 策略 × M 标的 × K 周期` 并行（`ProcessPoolExecutor` 真 CPU 并行）
2. **稳定性评分**：5 维综合评分（质量 / 风险 / 稳健性 / 成本敏感度 / 过度交易惩罚）
3. **参数优化**：网格搜索 + 随机扫描 + Walk-forward
4. **机会雷达**：观察列表扫描 + 历史上下文 + 风险解释
5. **专业报告**：每次运行产出 JSON + Markdown + 图表（权益曲线）
6. **模拟/实盘部署**：安全开关（arm）+ 一键平仓（close_all）+ kill switch
7. **Agent 原生**：所有命令支持 `--json`，并附带 6 个 YAML skills
8. **可复现性**：记录策略版本、策略规格哈希、策略源码哈希、参数哈希、数据哈希、Python 环境
9. **风控机制**：仓位上限、回撤止损、冷却周期、下单频率限制；下一根开盘成交模型

## 内置策略（7 个）

- `dca`：定投（`buy_interval`, `buy_amount_usdt`）
- `ma_crossover`：均线交叉（`fast_period`, `slow_period`, `ma_type`）
- `macd`：MACD（`fast_period`, `slow_period`, `signal_period`）
- `breakout`：唐奇安突破（`lookback`）
- `rsi_reversal`：RSI 反转（`rsi_period`, `oversold`, `overbought`）
- `bollinger_bands`：布林带（`bb_period`, `bb_std`）
- `grid`：网格（`grid_count`, `grid_spacing_pct`）

## 个性化策略扩展（策略仓）

你可以创建自己的策略文件或策略目录（策略仓），并通过 `--strategy-repo` 动态加载，不需要改 QuantX 主代码。

### 策略文件示例

```python
# user_strategies/my_strategy.py
from quantx.strategies import BaseStrategy

class MyPulseStrategy(BaseStrategy):
    name = "my_pulse"
    version = "0.1.0"
    category = "custom"
    author = "you"
    description = "个性化动量策略"
    default_params = {"lookback": 12}
    tags = ["custom", "momentum"]
    risk_profile = "medium"

    def signal(self, candles, i):
        lb = int(self.params.get("lookback", 12))
        if i < lb:
            return 0
        return 1 if candles[i].close > candles[i-lb].close else -1
```

### 查看可用策略（内置 + 自定义）

```bash
quantx strategy-list --strategy-repo user_strategies --json
```

### 使用自定义策略回测

```bash
quantx backtest \
  --file data/demo.csv \
  --strategy my_pulse \
  --params '{"lookback": 18}' \
  --strategy-repo user_strategies \
  --report-dir outputs/my_pulse \
  --json
```

> 所有自定义策略会自动纳入可复现性追踪（策略规格哈希 + 策略源码哈希）并写入报告。

## 快速开始

```bash
python -m pip install -e .
quantx data-generate --out data/demo.csv --bars 1200 --json
quantx data-inspect --file data/demo.csv --json
```

### 单策略回测 + 报告

```bash
quantx backtest \
  --file data/demo.csv \
  --strategy ma_crossover \
  --params '{"fast_period": 10, "slow_period": 30, "ma_type": "ema"}' \
  --report-dir outputs/ma_demo \
  --json
```

### N × M × K 并行批量回测

```bash
quantx batch \
  --file data/demo.csv \
  --strategies '[["dca", {"buy_interval": 24, "buy_amount_usdt": 50}], ["macd", {}]]' \
  --symbols '["BTCUSDT", "ETHUSDT"]' \
  --timeframes '["1h", "4h"]' \
  --workers 4 \
  --json
```

### 参数优化

```bash
# 网格
quantx optimize --file data/demo.csv --strategy breakout \
  --method grid --space '{"lookback": [10,20,30,40]}' --json

# 随机
quantx optimize --file data/demo.csv --strategy rsi_reversal \
  --method random --space '{"rsi_period": [6,30,"int"], "oversold": [15,40,"float"], "overbought": [60,90,"float"]}' \
  --samples 40 --json
```

### Walk-forward

```bash
quantx walk-forward --file data/demo.csv --strategy macd --params '{}' --splits 4 --json
```

### 机会雷达

```bash
quantx radar \
  --files '{"BTCUSDT":"data/demo.csv","ETHUSDT":"data/demo.csv"}' \
  --strategy bollinger_bands \
  --params '{}' \
  --json
```

### 模拟/实盘执行

```bash
quantx deploy --mode paper --symbol BTCUSDT --json
```

## Agent Skills

`quantx/skills/` 中提供 6 个 YAML skills：

- `backtest_skill.yaml`
- `batch_skill.yaml`
- `optimize_skill.yaml`
- `walk_forward_skill.yaml`
- `radar_skill.yaml`
- `deploy_skill.yaml`

## 最佳实践

- 回测前先 `data-inspect`
- 建议至少 30 天数据，覆盖多市场状态
- 先 batch 比较策略，再针对优胜者优化参数
- Walk-forward 建议 ≥3 切分
- 部署前先模拟盘，确认稳定性评分分项与成本敏感度


## 深度数据与微观结构回测（新增）

### 1) 交易所历史 K 线拉取（分钟/秒级）

```bash
quantx data-fetch-klines --exchange binance --symbol BTCUSDT --timeframe 1m --limit 1000 --out data/btc_1m.csv --json
# 若交易所支持 1s：
quantx data-fetch-klines --exchange binance --symbol BTCUSDT --timeframe 1s --limit 1000 --out data/btc_1s.csv --json
```

### 2) Tick 数据回测模式

```bash
quantx data-generate-tick --out data/tick_demo.csv --ticks 5000 --json
quantx backtest-tick --file data/tick_demo.csv --symbol BTCUSDT --threshold-bps 4 --report-dir outputs/tick --json
```

### 3) Orderbook 深度快照回放模式（L2+）

```bash
quantx data-generate-orderbook --out data/ob_demo.csv --rows 1000 --levels 10 --json
quantx backtest-orderbook --file data/ob_demo.csv --symbol BTCUSDT --shock-coeff 0.15 --report-dir outputs/orderbook --json
```

- `backtest-tick` 使用 tick-level 价格序列，适合高频/短周期信号验证。
- `backtest-orderbook` 使用 L2+ 快照并引入冲击成本（impact）近似，更贴近实盘滑点与冲击成本。
- 两种模式与主系统一致，均输出 `JSON + Markdown + 图表` 与可复现 metadata 字段。


## 进阶能力（本次补齐）

- 多周期回测（in-sample / out-of-sample）：`backtest-inout`
- 蒙特卡洛模拟：`monte-carlo`
- 绩效指标扩展：Sharpe、Sortino、Max Drawdown、Calmar
- 订单类型：market/limit/iceberg/twap/vwap（执行模拟）
- 滑点/流动性：orderbook 回放中引入 impact 估计
- 多经纪商路由：执行端支持 `broker_quotes` 做 Smart Order Routing
- 实时监控：`monitor`（回撤告警阈值）
- 日志分析：`log-analyze`（订单/错误/kill switch 统计）
- 机器学习自适应：`ml-online`（在线更新占位）
- A/B测试：`ab-test`（策略A vs 策略B）
- AI/LLM 情感分析入口：`sentiment`

### 优先级工作流（强烈建议）

1. 先建数据管道（K线/tick/orderbook）
2. 先开发简单策略（例如 MA 交叉 + ATR 止损，可放策略仓）
3. 再做严谨回测（含 in/out sample、walk-forward、monte-carlo）
4. 再加风控与执行约束（滑点、流动性、订单类型、SOR）
5. 上实盘前只用小仓位并开启监控告警
6. 迭代优化（A/B 测试 + 在线学习）

> 忽略任意一个环节都可能导致系统在实盘中出现不可控风险。
