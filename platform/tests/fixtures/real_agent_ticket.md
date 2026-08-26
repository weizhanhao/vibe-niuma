# 策略体检指标 — 增加「胜率」

## 需求

当前 `GET /api/rules/<id>/metrics` 策略体检返回 5 个指标：夏普、最大回撤、年化、持仓周期、交易日天数。需要增加第 6 个指标：**胜率**（已平仓交易的盈利比例）。

## 涉及文件

| 文件 | 改动 |
|---|---|
| `backend/engine/strategy_metrics.py` | 新增 `_win_rate()` 纯函数，`compute_strategy_metrics()` 返回 dict 中增加 `win_rate` |
| `backend/api/routes.py` | 更新 docstring（`1670` 行附近） |
| `frontend/rules.html` | 在指标展示区增加胜率一行（`614-621` 行附近） |
| `tests/test_strategy_metrics.py` | 新增 `_win_rate` 的单元测试 |
| `tests/test_rules_metrics_api.py` | `test_metrics_endpoint_returns_all_keys` 中增加 `win_rate` key 断言 |

## 实现细节

### 1. `_win_rate(trades)` — 纯函数

- 输入：`PaperTrade` 列表（与 `_avg_holding_days` 共用同一数据源）
- 算法：沿用 `_avg_holding_days` 的 FIFO 配对逻辑（按 `code` + `trade_time` 排序，buy 入队、sell 出队），对每对 buy-sell 判断 `sell.price > buy.price` 则为盈利
- 口径：`win_rate = 盈利交易对数 / 总交易对数 × 100`
- 无已平仓交易时返回 `None`
- 跳过 `excluded_from_stats=True` 和 `source="ex_rights"` 的记录（与 `_avg_holding_days` 一致）

### 2. `compute_strategy_metrics()` 返回 dict 增加 `win_rate` 键

```python
return {
    "sharpe": ...,
    "max_drawdown": ...,
    "annualized_return": ...,
    "avg_holding_days": ...,
    "trading_days": ...,
    "win_rate": _win_rate(trades),   # 新增
}
```

### 3. API 端点

`routes.py` 中 `rules_metrics` 的 docstring 更新为 `"""策略体检: 夏普/最大回撤/年化/持仓周期/胜率。"""`，代码无需改动（返回 dict 自动包含新 key）。

### 4. 前端展示

在 `rules.html` 指标行（夏普/最大回撤/年化/持仓/免责声明）中增加胜率显示：

```html
<span><span style="color:var(--text-tertiary)">胜率 </span><span style="font-weight:700" :class="(ruleMetrics[r.id].win_rate ?? 0)>=50?'text-up':'text-down'" x-text="ruleMetrics[r.id].win_rate != null ? ruleMetrics[r.id].win_rate.toFixed(1)+'%' : '—'"></span></span>
```

### 5. 测试

- **单元测试** (`test_strategy_metrics.py`)：新增 `test_win_rate_basic`（2 盈 1 亏 → 66.7%）、`test_win_rate_no_closed_trades`（仅有买入无卖出 → `None`）、`test_win_rate_all_profitable`（全盈利 → 100%）、`test_win_rate_all_loss`（全亏损 → 0%）
- **集成测试** (`test_rules_metrics_api.py`)：`test_metrics_endpoint_returns_all_keys` 的 key 列表增加 `"win_rate"`，`test_metrics_empty_rule_safe` 中增加 `assert data["win_rate"] is None`

## 预期工作量

~30 分钟