# MA50 与账户回撤工具

项目只保留两个命令行工具及其运行依赖：

- `paper/ma50_live.py`：ETHUSDT 日线 MA50 信号、目标仓位、手动调仓建议、报告与信号归档。
- `paper/dd_live.py`：读取合约账户，计算已核实本金盈亏、权益/钱包及现金流调整的采样回撤，并保存权益快照、实际成交与成本账本。

## 安装与运行

需要 Python 3.12 或更新版本，以及 uv。从项目根目录运行：

```powershell
uv sync --locked
```

根目录 `.env` 保存 `BINANCE_KEY` / `BINANCE_SECRET`，也兼容 `BINANCE_API_KEY` / `BINANCE_API_SECRET`。两个工具共用此文件；不要提交密钥。

当前已验证可用的连接方式是本地代理 `127.0.0.1:1080`。客户端默认使用此代理；可在 `.env` 用 `BINANCE_PROXY` 指定其他代理。下面的进程环境变量也可供其他联网工具使用：

```powershell
$env:HTTP_PROXY = "http://127.0.0.1:1080"
$env:HTTPS_PROXY = "http://127.0.0.1:1080"
```

日常命令：

```powershell
uv run --locked paper/ma50_live.py --console --check --archive
uv run --locked paper/dd_live.py --snapshot
```

其他模式：

```powershell
uv run --locked paper/ma50_live.py --history
uv run --locked paper/ma50_live.py --selfcheck
uv run --locked paper/ma50_live.py --backfill
uv run --locked paper/ma50_live.py --rebuild
uv run --locked paper/ma50_live.py --sync-methods
uv run --locked paper/ma50_live.py --console --check --target-vol 25
uv run --locked paper/dd_live.py
uv run --locked paper/dd_live.py --history
uv run --locked paper/dd_live.py --ledger
uv run --locked paper/dd_live.py --ledger-history
```

也可以进入 `paper` 目录后按原来的方式运行 `uv run ma50_live.py ...` / `uv run dd_live.py ...`；uv 会向上查找根目录配置。

当前默认方案为 MA50、10日波动窗口、60%目标波动、120%波动退出上限，普通加减仓采用权益5%门限；新开仓、目标归零和超过3倍权益的减仓不受软门限限制。详细执行条件见 `ma50_rules.md`。

MA50 使用 UTC 日线，检查时点为每天 UTC 00:00 之后，即北京时间 08:00 之后。`--archive` 保存信号，`--backfill` 回填前向收益；`--rebuild` 重建行情缓存；`--sync-methods` 修改 MA50 工具内的历史统计表；`--snapshot` 写入当天权益记录。客户端只实现只读交易所接口，工具不会自动下单。

## 依赖关系

| 工具/模块 | 项目内依赖 | 第三方库 | 本地文件 |
|---|---|---|---|
| `ma50_live.py` | 根目录 `binance_api.py`；同目录 `ma50_core.py`；归档需要 `execution_ledger.py` 和 `dd_support.py`；自检和同步统计表需要 `align.py` | NumPy | `.env`；ETH 日线、资金费；交易规则缓存；信号日志 |
| `dd_live.py` | 根目录 `binance_api.py`；同目录 `dd_support.py`、`execution_ledger.py` | 无，全部使用标准库 | `.env`；权益快照日志 |
| `align.py` | 与实时工具共用 `ma50_core.py` 的策略和订单校验 | NumPy | 无额外数据文件，由调用方传入 |
| `binance_api.py` | 无其他项目模块 | 无，HTTP、签名和 JSON 均使用标准库 | 根目录 `.env` |

`ma50_live.py` 和 `align.py` 都使用 NumPy；`dd_live.py` 单独运行不需要 NumPy；`dd_support.py` 只使用标准库。已移除未使用的 websockets 依赖，环境配置统一为根目录 `pyproject.toml` 和 `uv.lock`。

`ma50_core.py` 是纯决策与订单校验模块，使用 dataclasses、decimal 和 math。其余标准库包括 argparse、ast、contextlib、csv、datetime、hashlib、hmac、io、json、os、pathlib、sys、tempfile、time 和 urllib，无需额外安装。

## 保留的数据

| 路径 | 用途 | 恢复方式 |
|---|---|---|
| `data/crypto/ETHUSDT.json` | MA50 历史日线 | 可通过 `--rebuild` 重新下载 |
| `data/funding/ETHUSDT.json` | 历史资金费、成本和回测 | 运行时增量刷新；保留已有完整历史 |
| `data/live/exchange_rules.json` | 交易规则的离线回退缓存 | 联网读取成功时更新 |
| `data/live/ma50_log.csv` | 实际信号归档和前向检验记录 | 历史执行记录需要保留 |
| `data/live/ma50_decisions.json` | 升级后逐次建议时刻、参考价和规则版本 | 不补造旧时刻；保留本地文件 |
| `data/live/execution_ledger.json` | 实际成交、成本、查询范围和逐仓快照 | 首次近89天，后续增量；保留本地历史 |
| `data/live/execution_*.csv` | 成交、流水和逐仓采样导出 | JSON为权威记录，同步后重新导出 |
| `data/live/equity_log.csv` | 含浮动盈亏的权益快照 | 历史快照需要保留 |
| `data/live/dd_income.json` | DD完整流水及覆盖区间缓存 | 默认DD运行成功后更新；断档不当作零流水 |

`data/reports/` 是自动生成的报告目录，运行 MA50 时会重新创建。策略说明保留在 `ma50_rules.md`；其中引用的研究脚本已随清理备份，不参与工具运行。

## 验证与边界

```powershell
uv run --locked python -m unittest discover -s tests -v
uv run --locked paper/ma50_live.py --selfcheck
uv run --locked paper/ma50_live.py --selfcheck --offline
```

已修复趋势和波动风控未统一、规则标的误读、缺口继续出建议、账户异常、双向持仓相抵、数量预算、分页恢复与归档问题。失败的检查和暂停建议返回非零状态；旧历史记录保留为旧版未验证。

早期缺失的资金费标记价格用历史标记价格 8h K 线开盘价近似，缓存和自检会明确标注。

回测按数量现金流与资金费结算计费，成交价仍使用日线开盘价近似，未验证真实滑点和盘中强平。任何档位在低波动时都可能接近 3 倍；波动率上限不能保证未来不强平。详细口径见 `ma50_rules.md`。

测试全部使用隔离临时文件和模拟接口，不会下单或改写生产日志。

## DD口径

DD默认联网查询并更新流水缓存；`--snapshot`才增加权益采样，同一天多次记录保留；`--history`完全离线。API流水默认最近7天、最多保留近3个月，工具显式拉取89天并完整分页，不再声称可自动获得全部账户累计本金。已核实本金可用`--capital`配合`--snapshot`建立基准；未知时明确显示无法核实。钱包、权益采样与现金流调整后的净值采样回撤分开展示，详细计算和限制见 `dd_rules.md`。

## 实际成交与成本记录

--snapshot现在同时同步ETHUSDT实际成交、手续费、资金费和逐仓采样；实际交易后再运行一次，保留交易后状态。--ledger只同步账本，--ledger-history完全离线。MA50的--archive另保存每次建议的时刻、参考价与规则版本，原每日CSV仍取最新建议。

成本按共同覆盖区间计算“已实现盈亏－USDT成交手续费＋ETH资金费”，与流水对账，不重复扣费。非USDT手续费不混加，候选关联不是已确认执行。净额不含浮盈亏，不是账户总收益或策略夏普。详见[实际成交记录说明](execution_rules.md)。
