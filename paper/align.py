"""Cash-flow backtest using the same MA50 decisions and order constraints as live.

The first funding settlement at a UTC daily boundary belongs to the position
held before the daily rebalance. Trade price defaults to the daily open; this
is a price approximation, not an intraday liquidation or real-fill simulator.
"""
import numpy as np
from ma50_core import (MA_WINDOW, VOL_WINDOW, VOL_CAP, TARGET_VOL, FEE, SOFT_REBALANCE,
                       ExchangeRules, PositionState, plan_order, strategy_position, decimal, validate_funding)

DAY = 86_400_000
PY = 365.0


def lag(w):
    w = np.asarray(w, float)
    return np.r_[0.0, w[:-1]]


def daily_ret(C):
    C = np.asarray(C, float)
    if len(C) and (not np.isfinite(C).all() or np.any(C <= 0)):
        raise ValueError("收盘价必须为正且有限")
    return np.r_[0.0, np.diff(C) / C[:-1]] if len(C) else np.array([])


def sma(x, k):
    x = np.asarray(x, float)
    out = np.full(len(x), np.nan)
    if len(x) >= k:
        cs = np.cumsum(np.r_[0.0, x])
        out[k - 1:] = (cs[k:] - cs[:-k]) / k
    return out


def realized_vol(r, i, win=VOL_WINDOW):
    values = np.asarray(r, float)[i - win:i]
    return float(values.std(ddof=1) * np.sqrt(PY)) if i >= win + 1 else np.nan



def _returns(x):
    x = np.asarray(x, float)
    if x.ndim != 1 or not len(x) or not np.isfinite(x).all() or np.any(x < -1):
        raise ValueError("收益必须是一维非空有限序列，且单期不能低于-100%")
    return x


def sharpe(x, ann=PY):
    x = _returns(x)
    if not np.isfinite(ann) or ann <= 0:
        raise ValueError("年化周期必须为正且有限")
    sd = x.std(ddof=1) if len(x) > 1 else 0.0
    return float(x.mean() / sd * np.sqrt(ann)) if sd else 0.0


def max_dd(x):
    x = _returns(x)
    eq = np.r_[1.0, np.cumprod(1 + x)]
    return float(np.min(eq / np.maximum.accumulate(eq) - 1))


def cagr(x, ann=PY):
    x = _returns(x)
    if not np.isfinite(ann) or ann <= 0:
        raise ValueError("年化周期必须为正且有限")
    end = float(np.prod(1 + x))
    return end ** (ann / len(x)) - 1 if end > 0 else -1.0


def simulate(bars, funding, target_vol=TARGET_VOL, initial_equity=1000.0,
             fee=FEE, warmup=60, rules=None, constrained=False,
             slippage=0.0, fixed_lev=None, contributions=None, price_buffer=0.0,
             vol_window=VOL_WINDOW, soft_pct=SOFT_REBALANCE, rebalance_both=True,
             funding_interval_hours=8):
    n = len(bars)
    if n <= warmup or not np.isfinite([initial_equity, fee, slippage]).all() or initial_equity < 0 or fee < 0 or slippage < 0:
        raise ValueError("回测参数或样本长度无效")
    if isinstance(vol_window, bool) or not isinstance(vol_window, (int, np.integer)) or vol_window < 2 or warmup < max(MA_WINDOW, vol_window + 1):
        raise ValueError("波动窗口必须为至少2日的整数，预热期必须覆盖信号与波动窗口")
    cash = np.zeros(n) if contributions is None else np.asarray(contributions, dtype=float)
    if cash.shape != (n,) or not np.isfinite(cash).all() or np.any(cash < 0) or np.any(cash[:warmup] != 0):
        raise ValueError("定投入金必须与日线等长、非负有限，且不在预热期入金")
    if initial_equity == 0 and cash[warmup] <= 0:
        raise ValueError("零初始本金需要在首个回测日入金")
    C = np.array([b['c'] for b in bars], float)
    O = np.array([b.get('o', C[max(0, i-1)]) for i, b in enumerate(bars)], float)
    if not np.isfinite(C).all() or not np.isfinite(O).all() or np.any(C <= 0) or np.any(O <= 0):
        raise ValueError("回测价格无效")
    times = np.array([int(b['t']) for b in bars])
    if any(isinstance(b["t"], bool) or float(b["t"]) != int(b["t"]) for b in bars) or np.any(times % DAY) or np.any(np.diff(times) != DAY):
        raise ValueError("回测日线不连续、有重复或倒序")
    r = daily_ret(C); average = sma(C, MA_WINDOW)
    vols = np.full(n, np.nan)
    for i in range(vol_window + 1, n):
        vols[i] = realized_vol(r, i, win=vol_window)
    funding = validate_funding(funding, int(times[warmup]), int(times[-1])+DAY, funding_interval_hours)
    events = {}
    seen = set()
    for event in funding:
        t = int(event['t']); rate = float(event['rate'])
        if t in seen or not np.isfinite(rate):
            raise ValueError("资金费有重复或无效记录")
        seen.add(t); events.setdefault(t // DAY, []).append(event)
    if rules is None:
        rules = ExchangeRules()
    equity = float(initial_equity); held = 0.0
    net = np.zeros(n); equities = np.full(n, equity)
    quantities = np.zeros(n); fees = np.zeros(n); funding_cost = np.zeros(n)
    targets = np.zeros(n); actions = []
    for i in range(warmup, n):
        equity += cash[i]
        before = equity  # Start-of-day deposits are capital, never trading return.
        equity += held * (O[i] - C[i-1])
        daily_events = sorted(events.get(int(times[i] // DAY), []), key=lambda e:e['t'])
        if not daily_events:
            raise ValueError(f"资金费缺少日期 {int(times[i] // DAY)}，禁止按零费用回测")
        boundary, later = [], []
        for event in daily_events:
            (boundary if event['t'] - times[i] < 60_000 else later).append(event)
        for event in boundary:
            mark = float(event.get('markPrice') or O[i])
            cost = held * mark * float(event['rate'])
            equity -= cost; funding_cost[i] += cost
        if equity <= 0:
            net[i] = -1; equities[i:] = 0; break
        trend = bool(C[i-1] > average[i-1])
        if fixed_lev is None:
            weight, reason = strategy_position(trend, vols[i], equity, target_vol,
                                               fee + slippage, VOL_CAP, rules.min_notional)
        else:
            weight = min(fixed_lev, 3 / (1 + 3 * (fee + slippage))) if trend and vols[i] <= VOL_CAP else 0
            reason = "in_market" if weight else "off"
        targets[i] = weight
        available = max(0, equity - held * O[i] / 3)
        plan = plan_order(equity, O[i], weight, PositionState(long_qty=held), rules,
                          available, fee + slippage, soft_pct=soft_pct, constrained=constrained,
                          price_buffer=price_buffer, rebalance_both=rebalance_both)
        if plan.actionable:
            delta = plan.quantity if plan.side == 'BUY' else -plan.quantity
            cost = abs(delta) * O[i] * (fee + slippage)
            equity -= cost; fees[i] = cost
            held = float(decimal(held) + decimal(delta)) if constrained else held + delta
            actions.append({'i':i,'side':plan.side,'quantity':plan.quantity,'reason':reason})
        for event in later:
            mark = float(event.get('markPrice') or O[i])
            cost = held * mark * float(event['rate'])
            equity -= cost; funding_cost[i] += cost
        equity += held * (C[i] - O[i])
        equity = max(0, equity)
        net[i] = equity / before - 1; equities[i] = equity; quantities[i] = held
        if equity == 0:
            equities[i:] = 0; break
    return {'net':net,'equity':equities,'contributions':cash.copy(),'quantity':quantities,'target':targets,
            'fees':fees,'funding_cost':funding_cost,'actions':actions,
            'rebalance_policy':'symmetric' if rebalance_both else 'increase_only',
            'soft_rebalance_pct':soft_pct,'exchange_constraints':constrained,
            'model':'daily_open_cashflow; midnight funding before rebalance; no liquidation model',
            'funding_price_approximation':any((not e.get('markPrice') or e.get('markPriceApproximate')) for e in funding if e['t']>=times[warmup])}


class panel:
    """Compatibility adapter. Pass actual bars/funding for event-level accounting."""
    def __init__(self, C, FR, fee=FEE, bars=None, funding=None):
        self.C = np.asarray(C, float); self.FR = np.asarray(FR, float)
        if len(self.C) != len(self.FR) or not np.isfinite(self.FR).all():
            raise ValueError("资金费和价格长度/数值不匹配")
        self.n = len(self.C); self.fee = fee
        self.r = daily_ret(self.C); self.ma50 = sma(self.C, MA_WINDOW)
        self.sig = (self.C > self.ma50).astype(float)
        self.vol = np.full(self.n, np.nan)
        for i in range(VOL_WINDOW + 1,self.n):self.vol[i] = realized_vol(self.r,i)
        self.bars = bars or [{'t':(10957+i)*DAY,'o':float(self.C[max(0,i-1)]),
                             'c':float(c)} for i,c in enumerate(self.C)]
        self._actual_funding = funding is not None
        self.funding = funding if funding is not None else [
            {'t':b['t']+8*3_600_000,'rate':float(self.FR[i]),'markPrice':float(b['o'])}
            for i,b in enumerate(self.bars)]

    def weight(self,tv=None,lev=1.405,cap=None,vol_cap='auto',vol_lag=0,sig_lag=1):
        if vol_lag != 0 or sig_lag != 1:
            raise ValueError("只支持无前视、无额外滞后的权重")
        result = np.zeros(self.n)
        for i in range(VOL_WINDOW + 1,self.n):
            if tv is None:
                result[i] = lev if self.sig[i-1] and self.vol[i] <= VOL_CAP else 0
            elif self.vol[i] > 0:
                result[i] = strategy_position(bool(self.sig[i-1]),self.vol[i],1,tv,self.fee)[0]
        if cap is not None:result = np.minimum(result,cap)
        return result

    def net(self,tv=None,lev=1.405,warmup=60,**kwargs):
        if kwargs:raise ValueError("已移除可能错位的回测选项")
        return simulate(self.bars,self.funding,tv,1,self.fee,warmup=warmup,
                        fixed_lev=lev if tv is None else None,
                        funding_interval_hours=8 if self._actual_funding else None)['net']

    def net_lookahead(self,tv=None,lev=1.405):
        # Demonstration only; no use in validation or published statistics.
        w = np.zeros(self.n)
        for i in range(VOL_WINDOW + 1,self.n):
            if self.vol[i] > 0:
                w[i] = strategy_position(bool(self.sig[i]),self.vol[i],1,tv,self.fee)[0] if tv is not None else self.sig[i]*lev
        return w*self.r-np.abs(np.diff(np.r_[0,w]))*self.fee-w*self.FR


def assert_no_lookahead(panel_obj,tv=None,lev=1.405,**kwargs):
    # Validate causality structurally instead of requiring a higher fake Sharpe.
    weights = panel_obj.weight(tv,lev)
    for i in range(60,len(weights)):
        if not panel_obj.sig[i-1] and weights[i] != 0:
            raise AssertionError("空仓信号对应非零仓位")
    return sharpe(panel_obj.net(tv,lev)[60:]),sharpe(panel_obj.net_lookahead(tv,lev)[60:])
