# B策略冻结验证数据

此目录的30份JSON用于 `tests/verify_b_migration.py`，合计约2.3MB，全部来自公开历史市场数据及研究回测，不包含实盘账户或API密钥。

- `ma50_backtest_20261008/input_daily.json`：冻结的UTC日线价格。
- `ma50_volume_research_20261010/volume_fields.json`：ETH基础成交量。
- `open_strategy_search_20261011/`：B全历史两档滑点的决策及交易结果。
- `random_two_strategy_comparison_20261012/`：六段随机区间、两档滑点的B决策及交易结果。

原内容逐文件复制并验证哈希；旧研究目录完整归档在 `data/backups/historical_research_20261012.zip`。这些数据是防止B实现偏离既定规则的固定对照，不能当作未来收益保证。
