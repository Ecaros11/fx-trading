# 实际成交、成本与逐仓记录

现有两个工具积累这些只读记录，不下单、不更改杠杆或保证金。成交来自账户userTrades，成本来自income，风险来自账户及positionRisk。

## 每天怎么用

~~~powershell
uv run --locked paper/ma50_live.py --console --check --archive
uv run --locked paper/dd_live.py --snapshot
~~~

若进行了实际交易，成交后再运行一次DD的--snapshot。它保存权益采样，同时同步成交、成本及逐仓快照，无需第三个工具。没有成交时仍保存当时的风险状态；这是按运行时同步，不是全天监控。

仅同步成交账本、不增加DD权益采样：

~~~powershell
uv run --locked paper/dd_live.py --ledger
~~~

离线查看成交与成本，不需要网络或密钥：

~~~powershell
uv run --locked paper/dd_live.py --ledger-history
~~~

--history仍查看原DD权益历史。默认DD诊断不额外写成交账本；--snapshot和--ledger才同步。--capital只属于原已核实本金功能，不能与ledger/history模式混用。

## 文件

| 文件 | 内容 |
|---|---|
| data/live/execution_ledger.json | 权威账本：成交、全账户流水、已完整查询区间、逐仓/账户快照、同步运行记录 |
| data/live/execution_trades.csv | 成交ID、订单ID、时刻、方向、数量、价格、名义、手续费与币种、已实现盈亏、maker |
| data/live/execution_income.csv | 全账户成本与资金流水，保留类型、币种、标的、tranId及tradeId |
| data/live/execution_snapshots.csv | 权益/钱包/可用余额，以及ETH多空数量、开仓价、标记价、强平价、逐仓钱包与权益、自动追加状态 |
| data/live/ma50_decisions.json | 升级后逐次追加的建议时刻、参考价、数量、校验状态、凭证摘要及规则版本 |
| data/live/ma50_log.csv | 原每天最新建议视图及价格前向诊断；同日更新，不等同逐次决策日志 |

JSON为权威提交，CSV是派生视图，后续同步可重导出。JSON已提交但CSV写入失败时，工具显示“完整JSON账本已保存，但CSV导出失败”并返回非零状态；关闭占用CSV的程序后重跑。文件之间没有跨文件事务。原DD权益采样可能已保存而成交同步失败；工具明确报告并返回非零状态，已验证的权益采样保留。

新账本、CSV和逐次决策文件已加入git忽略，不保存API密钥。凭证摘要变化时拒绝混用数据；摘要不是稳定的交易所账户UID，换钥匙后需核实归属。

## 历史范围与完整性

当前官方成交及收入接口仅支持近3个月。首次显式查询近89天，不声称为全部账户历史。之后成交从已保存覆盖终点回看3天并增量查询；DD收入缓存按原近89天完整分页刷新，持续保留旧记录。3天重叠用于常规迟到保护；交易所迟补更早的成交不一定被增量重读，不是无限历史补正保证。

成交请求切分到不超过7天。满1000条时二分时间窗，不用“最后一条时间+1毫秒”跳过同毫秒成交。如果单一毫秒仍满1000条，或达到请求保护上限，则拒绝称完整，保留旧账本。流水复用page分页。

以(symbol,id)去重成交，以(incomeType,tranId)去重流水；同ID内容冲突则失败，不覆盖旧记录。Decimal计算金额，不混加币种。长期中断超过可查询保留期会保留独立覆盖区间并显示断档，不把缺口当零费用。

“完整查询覆盖”仅指固定请求区间分页完成，不证明交易所永不迟补或区间外没有历史交易。

## 成本公式

~~~text
共同覆盖区间已实现净额
= 成交realizedPnl合计
− 成交commission中的USDT手续费合计
+ ETHUSDT的FUNDING_FEE收入合计
~~~

手续费正数为扣费，负数为返还；资金费收入正数为收到，负数为支付。采用实际账户金额，不按行情费率乘当前数量猜测。

流水COMMISSION与REALIZED_PNL是成交手续费与已实现盈亏的另一种记录，用于对账，不再次计入净额。按币种核对“成交手续费+流水COMMISSION”，盈亏核对“成交realizedPnl−流水REALIZED_PNL”。只在共同覆盖区间核对，差额明确显示，不自行改为零。

上述净额不含浮盈亏、其他收入、非USDT手续费，不等于账户总收益、净值收益、夏普或MA50策略收益。非USDT成本及其他ETH流水另行提示；BTC或其他合约成本不归入ETH。全账户划转/兑换保留，原DD净值逻辑排除现金流。

## 建议与执行

不补造旧归档缺少的运行时刻，也不将旧成交自动认定为MA50执行。从升级后开始，--archive保存独立决策ID、服务器时刻、实时参考价查询时刻、数量和校验状态、规则指纹；同日重复运行全部保留，每日CSV仍取最新建议。

规则指纹包含共享决策源码及此次MA/波动窗口/目标档位/退出阈值/杠杆/调仓门限/交易规则配置，用于辨认变化。

成交CSV只给出“时间和方向候选”：同凭证、同标的、在最新建议后24小时内，买卖及持仓方向一致。最新建议若为不动、暂停或不同方向，则不关联。候选不是确认归属，不能证明手动订单执行了建议，未据此计算执行率或策略夏普。

不利价格差用基点表示：买入(成交价/参考价−1)×10000，卖出符号反转。它包含等待期间的行情变动和执行差价，不称为纯滑点。当前没有用户确认的订单到决策绑定，也不把分笔成交误当成多次建议执行。

## 快照与故障验证

账户/持仓是多次请求，保留采样开始和结束时刻、atomic=false；不能伪称原子快照。逐仓钱包与逐仓权益需校验；强平价来自API，不可验证时同步失败，不用公式猜测替代。

验证覆盖分页、同毫秒成交、ID冲突、网络与写盘失败、并发、重复同步、凭证隔离、断档、币种、成本去重、返佣、实际逐仓字段、前瞻候选和离线模式。API客户端仍仅允许GET。

来源：[币安成交接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)、[币安账户与收入接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)。

