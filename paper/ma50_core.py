"""Shared B daily signals and exchange constraints; no network or writes."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import math
import statistics

MA_WINDOW = 50  # Legacy diagnostic period; B decisions use ENTRY/EXIT_MA_WINDOW.
STRATEGY_ID = "B_ma35_in_ma40_out_volume10_vwma10_v1"
C25_STRATEGY_ID = "B_C25_down3_sd5_30_125_memory2_v1"
C25_CONFIG = dict(down=3, short=5, long=30, threshold=1.25, factor=.25, memory=2)
ENTRY_MA_WINDOW = 35
EXIT_MA_WINDOW = 40
VOLUME_WINDOW = 10
VWMA_WINDOW = 10
SIGNAL_LOOKBACK = max(ENTRY_MA_WINDOW, EXIT_MA_WINDOW, VOLUME_WINDOW + 1)
VOL_WINDOW = 10
TARGET_VOL = 0.60
VOL_CAP = 1.20
MAX_LEVERAGE = 3
FEE = 0.0005
SOFT_REBALANCE = 0.05


def finite(value, name, minimum=None):
    value = float(value)
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        raise ValueError(f"{name} 无效")
    return value


def decimal(value):
    return Decimal(str(value))


def signal_indicators(bars):
    """Only completed bars supplied by the caller; volume is ETH base volume."""
    if len(bars) < SIGNAL_LOOKBACK:
        raise ValueError("B信号至少需要40根完成日线")
    tail = bars[-SIGNAL_LOOKBACK:]
    closes = [finite(b["c"], "收盘", 0) for b in tail]
    volumes = [finite(b["v"], "ETH成交量", 0) for b in tail]
    if min(closes) <= 0:
        raise ValueError("收盘价必须为正")
    baseline = math.fsum(volumes[-VOLUME_WINDOW-1:-1]) / VOLUME_WINDOW
    denominator = math.fsum(volumes[-VWMA_WINDOW:])
    if baseline <= 0 or denominator <= 0:
        raise ValueError("量能均值或VWMA成交量分母无效")
    return dict(close=closes[-1],
                ma35=math.fsum(closes[-ENTRY_MA_WINDOW:])/ENTRY_MA_WINDOW,
                ma40=math.fsum(closes[-EXIT_MA_WINDOW:])/EXIT_MA_WINDOW,
                volume=volumes[-1], volume_mean10=baseline,
                volume_ratio10=volumes[-1]/baseline,
                vwma10=math.fsum(c*v for c,v in zip(closes[-VWMA_WINDOW:],volumes[-VWMA_WINDOW:]))/denominator)


def signal_decision(indicators, held_qty, rvol):
    """Select entry or exit from verified actual quantity, never virtual signals."""
    if held_qty is None:
        return None, "position_unknown", "unknown"
    held_qty = finite(held_qty, "实际多仓", 0)
    rvol = finite(rvol, "已实现波动", 0)
    if rvol <= 0:
        raise ValueError("波动率必须为正")
    branch = "holding" if held_qty > 0 else "entry"
    if rvol > VOL_CAP:
        return False, "vol_cap", branch
    if held_qty > 0:
        active = indicators["close"] > indicators["ma40"]
        return active, "in_market" if active else "exit_ma40", branch
    for ok, reason in ((indicators["close"] > indicators["ma35"], "entry_ma35"),
                       (indicators["volume_ratio10"] >= 1, "entry_volume"),
                       (indicators["close"] > indicators["vwma10"], "entry_vwma")):
        if not ok:
            return False, reason, branch
    return True, "in_market", branch


def c25_indicators(bars):
    """C25 uses latest TWO closed-bar triggers, never invocation count.

    down3 means close[t] < close[t-3], not three consecutive down days.
    Sample SD of simple close-to-close returns; factor applies BEFORE caps.
    Caller supplies completed bars only, just as for the B indicators.
    """
    required = C25_CONFIG['long'] + C25_CONFIG['memory']
    if len(bars) < required:
        raise ValueError("C25至少需要32根完成日线")
    tail = bars[-required:]
    times = [finite(b['t'], "C25日线时刻", 0) for b in tail]
    if any(isinstance(b['t'],bool) for b in tail) or any(not t.is_integer() or t % 86400000 for t in times) or any(
            t-u != 86400000 for u,t in zip(times,times[1:])):
        raise ValueError("C25日线有缺口、重复或时刻无效")
    closes = [finite(b['c'], "C25收盘", 0) for b in tail]
    if min(closes) <= 0:
        raise ValueError("C25收盘必须为正")
    returns = [closes[i]/closes[i-1]-1 for i in range(1,len(closes))]
    checks=[]
    for lag in (0,1):
        end = len(returns)-lag
        sd5 = statistics.stdev(returns[end-5:end])*math.sqrt(365)
        sd30 = statistics.stdev(returns[end-30:end])*math.sqrt(365)
        index = len(closes)-1-lag
        down = closes[index] < closes[index-3]
        checks.append(dict(bar_ms=int(times[index]), down3=down,
                           sd5=sd5, sd30=sd30,
                           ratio=sd5/sd30 if sd30 else None,
                           trigger=down and sd5 > 1.25*sd30))
    active = any(x['trigger'] for x in checks)
    latest,previous=checks
    return dict(c25_active=active,c25_factor=.25 if active else 1.,
                c25_trigger=latest['trigger'],c25_previous_trigger=previous['trigger'],
                c25_down3=latest['down3'],c25_sd5=latest['sd5'],c25_sd30=latest['sd30'],
                c25_ratio=latest['ratio'],c25_previous_bar_ms=previous['bar_ms'])


def validate_funding(rows, start_ms=None, end_ms=None, interval_hours=8):
    """Validate prices/timestamps and every expected settlement in [start, end).

    interval_hours=None is only for a synthetic daily aggregate research series.
    A known interval change requires an explicit schedule, never silent guessing.
    """
    if not isinstance(rows, list) or not rows:
        raise ValueError("资金费记录为空或格式无效")
    result = []; seen = set()
    for row in rows:
        stamp = finite(row["t"], "资金费时间", 0)
        if not stamp.is_integer() or isinstance(row["t"], bool):
            raise ValueError("资金费时间不是有效整数毫秒")
        t = int(stamp)
        if t in seen: raise ValueError("资金费时间重复")
        if row.get("symbol", "ETHUSDT") != "ETHUSDT":
            raise ValueError("资金费标的错误")
        normalized = dict(row, t=t, rate=finite(row["rate"], "资金费率"))
        if row.get("markPrice") is not None:
            mark = finite(row["markPrice"], "资金费标记价格", 0)
            if mark <= 0: raise ValueError("资金费标记价格必须为正")
            normalized["markPrice"] = mark
        result.append(normalized); seen.add(t)
    result.sort(key=lambda r:r["t"])
    if interval_hours is not None and start_ms is not None and end_ms is not None:
        hours = finite(interval_hours, "资金费结算间隔", 1)
        if not hours.is_integer() or 24 % int(hours):
            raise ValueError("资金费结算间隔必须为24小时的整数因数")
        interval = int(hours) * 3600000
        slots = set()
        for row in result:
            t = row["t"]
            if start_ms <= t < end_ms:
                slot = t // interval * interval
                if t-slot >= 60000: raise ValueError("资金费结算时间未对齐已知间隔")
                if slot in slots: raise ValueError("同一结算时点有多条资金费")
                slots.add(slot)
        first = (int(start_ms)+interval-1)//interval*interval
        missing = next((t for t in range(first,int(end_ms),interval) if t not in slots),None)
        if missing is not None:
            raise ValueError(f"资金费缺少结算时点 {missing}，禁止按零费用回测")
    return result


def floor_qty(value, step):
    return float((decimal(value) / decimal(step)).to_integral_value(rounding=ROUND_FLOOR) * decimal(step))


def ceil_qty(value, step):
    return float((decimal(value) / decimal(step)).to_integral_value(rounding=ROUND_CEILING) * decimal(step))


@dataclass(frozen=True)
class ExchangeRules:
    symbol: str = "ETHUSDT"
    min_notional: float = 20.0
    step_size: float = 0.001
    min_qty: float = 0.001
    market_max_qty: float = 2000.0

    def __post_init__(self):
        if self.symbol != "ETHUSDT":
            raise ValueError("交易规则标的不是 ETHUSDT")
        for name in ("min_notional", "step_size", "min_qty", "market_max_qty"):
            if finite(getattr(self, name), name, 0) <= 0:
                raise ValueError(f"{name} 必须为正")
        if self.market_max_qty < self.min_qty:
            raise ValueError("数量上下限冲突")

    @classmethod
    def from_symbol(cls, row):
        if row.get("symbol") != "ETHUSDT" or row.get("contractType") != "PERPETUAL" or row.get("status") != "TRADING":
            raise ValueError("ETHUSDT 合约不可交易或标的信息不完整")
        filters = {f["filterType"]: f for f in row["filters"]}
        lot, market = filters["LOT_SIZE"], filters["MARKET_LOT_SIZE"]
        steps = [decimal(lot["stepSize"]), decimal(market["stepSize"])]
        steps = [s for s in steps if s > 0]
        # Least common multiple handles filters whose increments are not powers of ten.
        scale = max(-s.as_tuple().exponent for s in steps)
        factor = 10 ** scale
        ints = [int(s * factor) for s in steps]
        step = math.lcm(*ints) / factor
        return cls(row["symbol"], float(filters["MIN_NOTIONAL"]["notional"]), step,
                   max(float(lot["minQty"]), float(market["minQty"])),
                   min(float(lot["maxQty"]), float(market["maxQty"])))


def max_position(leverage=MAX_LEVERAGE, fee=FEE):
    return leverage / (1 + leverage * fee)


def strategy_position(trend_long, rvol, equity, target_vol=TARGET_VOL, fee=FEE,
                      vol_cap=VOL_CAP, min_notional=20.0):
    equity = finite(equity, "权益", 0)
    rvol = finite(rvol, "已实现波动", 0)
    if equity <= 0 or rvol <= 0:
        raise ValueError("权益和波动率必须为正")
    if not trend_long:
        return 0.0, "trend_off"
    if vol_cap is not None and rvol > vol_cap:
        return 0.0, "vol_cap"
    if target_vol is None:
        raw = max(1.0, min_notional / equity)
    else:
        target_vol = finite(target_vol, "目标波动", 0)
        if target_vol <= 0:
            raise ValueError("目标波动必须为正")
        raw = target_vol / rvol
    return min(max_position(fee=fee), raw), "in_market"


@dataclass(frozen=True)
class PositionState:
    long_qty: float = 0.0
    short_qty: float = 0.0
    hedge: bool = False
    leverage: int = MAX_LEVERAGE
    margin_type: str = "isolated"
    liquidation_price: float = 0.0
    mark_price: float | None = None
    isolated_wallet: float | None = None
    isolated_margin: float | None = None


def parse_positions(rows, hedge):
    if not isinstance(rows, list):
        raise ValueError("持仓响应不是数组")
    own = [p for p in rows if p.get("symbol") == "ETHUSDT"]
    if not own:
        raise ValueError("持仓响应缺少 ETHUSDT，不能假定空仓")
    sides = {}
    for p in own:
        side = p.get("positionSide", "BOTH")
        if side in sides:
            raise ValueError("持仓响应有重复方向")
        sides[side] = p
    if hedge:
        if "LONG" not in sides or "SHORT" not in sides:
            raise ValueError("双向持仓响应缺少 LONG/SHORT")
        long_row = sides["LONG"]
        long_qty = finite(long_row["positionAmt"], "多仓数量", 0)
        short_amt = finite(sides["SHORT"]["positionAmt"], "空仓数量")
        if short_amt > 0:
            raise ValueError("空仓数量符号异常")
        short_qty = abs(short_amt)
    else:
        if "BOTH" not in sides:
            raise ValueError("单向账户缺少 BOTH 持仓")
        long_row = sides["BOTH"]
        amount = finite(long_row["positionAmt"], "持仓数量")
        long_qty, short_qty = max(0, amount), max(0, -amount)
    leverage = finite(long_row.get("leverage"), "杠杆", 1)
    if not leverage.is_integer():
        raise ValueError("杠杆不是整数")
    mode = long_row.get("marginType")
    if mode not in ("isolated", "cross"):
        raise ValueError("保证金模式未知")
    mark = None if long_row.get("markPrice") in (None,"") else finite(long_row["markPrice"],"持仓标记价",0)
    if mark is not None and mark <= 0:
        raise ValueError("持仓标记价必须为正")
    wallet = None if long_row.get("isolatedWallet") in (None,"") else finite(long_row["isolatedWallet"],"逐仓钱包")
    margin = None if long_row.get("isolatedMargin") in (None,"") else finite(long_row["isolatedMargin"],"逐仓权益")
    if mode == "isolated" and wallet is not None and margin is not None and long_row.get("unRealizedProfit") not in (None,""):
        pnl = finite(long_row["unRealizedProfit"],"持仓浮盈")
        if abs(margin-wallet-pnl) > max(.0001,abs(margin)*1e-6):
            raise ValueError("逐仓权益与钱包及浮盈不一致")
    return PositionState(long_qty, short_qty, bool(hedge), int(leverage), mode,
                         finite(long_row.get("liquidationPrice") or 0, "强平价", 0),
                         mark, wallet, margin)


@dataclass(frozen=True)
class OrderPlan:
    status: str
    reason: str
    target_qty: float = 0.0
    quantity: float = 0.0
    side: str = ""
    position_side: str = ""
    reduce_only: bool = False
    min_order_qty: float = 0.0
    required_funds: float = 0.0

    @property
    def actionable(self):
        return self.status == "order"

    @property
    def action(self):
        if self.status == "blocked":
            return "暂停"
        if self.status != "order":
            return "不动"
        if self.side == "SELL":
            return "平多" if self.target_qty == 0 else "减多"
        return "加多"


def plan_order(equity, price, target_position, state, rules, available=None,
               fee=FEE, soft_pct=SOFT_REBALANCE, constrained=True, price_buffer=0.001,
               rebalance_both=True):
    equity = finite(equity, "权益", 0)
    price = finite(price, "价格", 0)
    weight = finite(target_position, "目标仓位", 0)
    fee = finite(fee, "手续费", 0)
    soft_pct = finite(soft_pct, "软调仓比例", 0)
    price_buffer = finite(price_buffer, "价格预算", 0)
    if equity <= 0 or price <= 0:
        return OrderPlan("blocked", "权益或价格无效")
    if state is None:
        return OrderPlan("blocked", "持仓未知，无法给出买卖数量")
    if state.short_qty > 0:
        return OrderPlan("blocked", "发现独立空头持仓；只做多工具暂停建议，请先核对空头")
    held = finite(state.long_qty, "多仓数量", 0)
    validation_price = price * (1 + price_buffer)
    max_qty = equity / (validation_price * (1 / MAX_LEVERAGE + fee))
    target = min(equity * weight / price, max_qty)
    target = floor_qty(target, rules.step_size) if constrained else target
    side_name = "LONG" if state.hedge else "BOTH"
    raw_delta = float(decimal(target) - decimal(held))
    reducing = raw_delta < -1e-10
    # Minimum notional applies only to increases; ordinary held-position changes
    # use the symmetric band. Full exits and exposure above 3x bypass that band.
    # rebalance_both=False preserves the historical increase-only research policy.
    if reducing:
        quantity = floor_qty(-raw_delta, rules.step_size) if constrained else -raw_delta
        minimum = ceil_qty(rules.min_qty, rules.step_size) if constrained else 0
    else:
        quantity = floor_qty(max(0, raw_delta), rules.step_size) if constrained else max(0, raw_delta)
        minimum = (ceil_qty(max(rules.min_qty, rules.min_notional / price,
                               0 if rebalance_both else soft_pct * equity / price), rules.step_size) if constrained else 0)
    if quantity <= 1e-10 or (constrained and quantity < minimum - 1e-10):
        return OrderPlan("hold", "差额不足适用数量门限", target, min_order_qty=minimum)
    ordinary = (held > 0 and weight > 0 and
                decimal(held) * decimal(price) <= decimal(equity) * decimal(MAX_LEVERAGE))
    if constrained and rebalance_both and ordinary:
        threshold = decimal(soft_pct) * decimal(equity)
        if decimal(quantity) * decimal(price) < threshold:
            band_qty = ceil_qty(float(threshold / decimal(price)), rules.step_size)
            return OrderPlan("hold", f"普通双向调仓差额不足权益 {soft_pct:.0%}", target,
                             min_order_qty=max(minimum, band_qty))
    if constrained and quantity > rules.market_max_qty + 1e-10:
        return OrderPlan("blocked", "数量超过市价单上限；需要拆单，当前不生成单笔指令", target,
                         min_order_qty=minimum)
    if not reducing:
        if state.margin_type != "isolated":
            return OrderPlan("blocked", "先将 ETHUSDT 设为逐仓，再重新读取账户", target)
        if state.leverage != MAX_LEVERAGE:
            return OrderPlan("blocked", "先将 ETHUSDT 杠杆设为 3x，再重新读取账户", target)
        if available is None:
            return OrderPlan("blocked", "可用保证金未知，禁止加仓", target)
        available = finite(available, "可用保证金", 0)
        required = quantity * validation_price * (1 / state.leverage + fee)
        if required > available + 1e-9:
            return OrderPlan("blocked", "可用保证金不足（含本次手续费）", target,
                             min_order_qty=minimum, required_funds=required)
    else:
        required = quantity * price * fee
    return OrderPlan("order", "减仓" if reducing else "增仓", target, quantity,
                     "SELL" if reducing else "BUY", side_name,
                     reducing and not state.hedge, minimum, required)
