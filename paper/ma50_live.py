"""ETHUSDT MA50 manual trading tool.

Daily completed close above MA50 and realized volatility at most 120% permits
long exposure. Default target volatility is 60%. All quantities pass shared
exchange/account constraints before display and archive. No orders are sent.
See README.md for CLI modes and ma50_rules.md for accounting assumptions.
"""
import argparse
import csv
import datetime as dt
import os
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ma50_core import (ExchangeRules, PositionState, parse_positions, plan_order,
                       strategy_position, finite, max_position, OrderPlan)
import tempfile
import hashlib
import uuid
from contextlib import contextmanager
from dataclasses import asdict
import ma50_core as strategy

class DataError(ValueError):
    pass

def format_qty(value):
    places = max(0, -strategy.decimal(STEP_SIZE).normalize().as_tuple().exponent)
    return f"{value:.{places}f}"


def current_rules():
    return ExchangeRules(SYM, MIN_NOTIONAL, STEP_SIZE, MIN_QTY, MARKET_MAX_QTY)

def cached_rules():
    data = json.loads(_RULES_CACHE.read_text(encoding="utf-8"))
    if data.get("symbol") != SYM:
        raise DataError("规则缓存标的未验证，需联网重建")
    fetched = dt.datetime.fromisoformat(data["fetched"])
    if fetched.tzinfo is None or not -300 <= (dt.datetime.now(dt.UTC)-fetched).total_seconds() <= 7*86400:
        raise DataError("交易规则缓存无效或已过期")
    return ExchangeRules(**data["rules"]), f"本地缓存（{data['fetched']}）"

def validate_bars(bars, continuous=False):
    if not isinstance(bars, list) or not bars:
        raise DataError("日线缓存为空或格式无效")
    previous = None
    for b in bars:
        t = int(b["t"])
        if t != b["t"] or t % 86400000:
            raise DataError("日线时间未对齐 UTC")
        if previous is not None and (t <= previous or (continuous and t-previous != 86400000)):
            raise DataError("日线有缺口、重复或倒序")
        v = {k: finite(b[k], k, 0) for k in ("o","h","l","c","v")}
        if min(v[k] for k in ("o","h","l","c")) <= 0 or not v["l"] <= min(v["o"],v["c"]) <= max(v["o"],v["c"]) <= v["h"]:
            raise DataError("日线 OHLC 无效")
        previous = t
    return bars

def kline_record(x):
    return {"t":int(x[0]), "o":float(x[1]), "h":float(x[2]), "l":float(x[3]),
            "c":float(x[4]), "v":float(x[5]), "closeTime":int(x[6])}

def load_funding():
    try:
        return strategy.validate_funding(json.loads(FUND.read_text(encoding="utf-8")))
    except (ValueError,TypeError,KeyError) as error:
        raise DataError(f"资金费缓存无效：{error}") from None


SYM = "ETHUSDT"
MA_WINDOW = strategy.MA_WINDOW


VOL_WINDOW = strategy.VOL_WINDOW
DEFAULT_TARGET_VOL = strategy.TARGET_VOL
VOL_CAP = strategy.VOL_CAP


LEVERAGE = 1.405
MIN_NOTIONAL = 20.0


STEP_SIZE = 0.001
MIN_QTY = 0.001
MARKET_MAX_QTY = 2000.0
EXCHANGE_RULES_SRC = "默认值（尚未加载）"
_RULES_CACHE = ROOT / "data" / "live" / "exchange_rules.json"


def load_exchange_rules(bn=None):
    global MIN_NOTIONAL, STEP_SIZE, MIN_QTY, MARKET_MAX_QTY, EXCHANGE_RULES_SRC
    if bn is None:
        rules, source = cached_rules()
    else:
        try:
            info = bn.fapi("/fapi/v1/exchangeInfo", signed=False)
        except Exception as api_error:
            try: rules, source = cached_rules()
            except Exception as cache_error:
                raise DataError(f"规则不可验证（API: {type(api_error).__name__}；缓存: {cache_error}）") from None
            source += "；API 失败"
        else:
            # An authoritative suspension or invalid symbol must never be
            # hidden by a previously trading cache. Fallback is for fetch errors.
            try:
                matches = [s for s in info["symbols"] if s.get("symbol") == SYM]
                if len(matches) != 1: raise DataError("ETHUSDT 规则缺失或重复")
                rules = ExchangeRules.from_symbol(matches[0])
            except (ValueError,TypeError,KeyError) as error:
                raise DataError(f"API交易规则无效，暂停建议：{error}") from None
            source = "API 已验证"
            try:
                _atomic_write(_RULES_CACHE,json.dumps({"symbol":SYM,
                    "fetched":dt.datetime.now(dt.UTC).isoformat(),"rules":asdict(rules)},ensure_ascii=False))
            except OSError:
                source += "；缓存保存失败（使用本次API规则）"
    MIN_NOTIONAL, STEP_SIZE, MIN_QTY, MARKET_MAX_QTY = (rules.min_notional,rules.step_size,rules.min_qty,rules.market_max_qty)
    EXCHANGE_RULES_SRC = source
    return source


SOFT_REBALANCE_PCT = strategy.SOFT_REBALANCE
MMR = 0.004


WORST_TRADE_LOW = -0.266
WORST_TRADE_CLOSE = -0.245


MAX_SAFE_LEV = 3


TARGET_VOL_OVERRIDE = DEFAULT_TARGET_VOL

TARGET_VOL_EXPLICIT = False


VOL_ACHIEVE = 0.89


SE_SHARPE = 0.387
SAMPLE_YEARS = 6.71
SAMPLE_DAYS = 2448
FEE_PER_SIDE = strategy.FEE


METHODS_ASOF = '2026-10-09'

METHODS = [('固定参考 1.405x', None, 6.7, 1.1211, -0.727), ('波动率目标 60%', 0.6, 31.7, 1.2296, -0.543), ('波动率目标 40%', 0.4, 47.5, 1.2191, -0.393), ('波动率目标 25%', 0.25, 76.1, 1.2204, -0.26), ('波动率目标 15%', 0.15, 126.8, 1.22, -0.162)]


DD_BY_LEV = [(0.8, -0.498), (1.0, -0.585), (1.2, -0.661), (1.405, -0.727), (1.6, -0.779), (2.0, -0.862)]


def dd_for_lev(lev):
    """按实际杠杆线性插值查最大回撤（超出范围取端点）"""
    if lev <= DD_BY_LEV[0][0]:
        return DD_BY_LEV[0][1]
    if lev >= DD_BY_LEV[-1][0]:
        return DD_BY_LEV[-1][1]
    for (l0, d0), (l1, d1) in zip(DD_BY_LEV, DD_BY_LEV[1:]):
        if l0 <= lev <= l1:
            t = (lev - l0) / (l1 - l0)
            return d0 + t * (d1 - d0)
    return DD_BY_LEV[-1][1]


def fixed_position(equity):
    """
    固定版的仓位。不是常量 1.405 —— 那个数只是 20 ÷ 14.23。

    正确逻辑：
      · 权益 < 20U  →  被迫用 20/equity（否则下不出最小单）
      · 权益 ≥ 20U  →  用 1.0x（满仓）。不该再放大，
                       因为放大只增加回撤、不改变夏普。
    """
    if equity <= 0:
        return 0.0
    return max(1.0, MIN_NOTIONAL / equity)

ARCHIVE = ROOT / "data" / "live" / "ma50_log.csv"
CACHE = ROOT / "data" / "crypto" / f"{SYM}.json"
FUND = ROOT / "data" / "funding" / f"{SYM}.json"

FIELDS = [
    "symbol", "date", "bar_ms", "bar_close", "ma50", "dist_ma50_pct",
    "signal", "action", "advice_qty", "advice_notional",
    "equity_at_signal", "method", "target_vol",
    "realized_vol_pct", "target_position",
    "lev_setting", "margin_mode", "liq_acc_pct",
    "funding_pct_today",
    "funding_pct_next",

    "entry_ref", "fwd_1d", "fwd_7d", "fwd_10d", "fwd_30d",
    "max_dd_10d", "checked_at", "status", "notes",
]

FIELDS += ["rebalance_policy","soft_rebalance_pct","trend_signal","decision_reason","side","position_side","reduce_only","order_qty","target_qty","validation_status","price_return_1d","price_return_7d","price_return_10d","price_return_30d","price_drawdown_10d"]
FIELDS += ["held_qty_at_signal","available_at_signal","daily_check_status","daily_prior_runs","daily_fill_count","daily_ledger_cutoff_ms"]
FIELDS += ["decision_id","decision_ms","decision_at","account_scope","reference_price","reference_price_ms","rule_version","mark_price_at_position","liquidation_price_at_position","liquidation_distance_pct","isolated_wallet_at_position","isolated_margin_at_position"]


def _atomic_write(path, text, encoding="utf-8"):
    path = pathlib.Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name+".",suffix=".tmp",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding=encoding,newline="") as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def validate_pending_orders(bn):
    # Both sources are required: UI protective orders use the Algo API.
    counts = {}
    for name, endpoint in (("普通", "/fapi/v1/openOrders"), ("条件", "/fapi/v1/openAlgoOrders")):
        rows = bn.fapi(endpoint, {"symbol":SYM}, signed=True)
        if not isinstance(rows,list) or any(not isinstance(r,dict) or r.get("symbol")!=SYM for r in rows):
            raise DataError(f"{name}委托响应标的或格式无效")
        counts[name] = len(rows)
    if any(counts.values()):
        raise DataError(f"存在未成交委托（普通{counts['普通']}、条件{counts['条件']}）；请核对既有委托及成交后的持仓再调仓")
    return counts


def complete_bars(bars, now_ms=None):
    now_ms = int(dt.datetime.now(dt.UTC).timestamp()*1000) if now_ms is None else int(now_ms)
    done = [b for b in bars if int(b.get("closeTime",int(b["t"])+86400000-1)) < now_ms]
    return done, f"已完成 {len(done)} 根；剔除未完成 {len(bars)-len(done)} 根"


def check_bar_continuity(bars, max_gap_h=25.0):
    gaps = [(a["t"],b["t"],(b["t"]-a["t"])/3600000) for a,b in zip(bars,bars[1:])
            if b["t"]-a["t"] != 86400000]
    return gaps, ("日期连续" if not gaps else f"发现 {len(gaps)} 处缺口、重复或倒序")


def enrich_funding_marks(bn, rows, first_trade):
    missing=[r for r in rows if r["t"]>=first_trade and not r.get("markPrice")]
    if not missing:return rows
    interval=28800000;start=min(r["t"] for r in missing)//interval*interval
    end=max(r["t"] for r in missing)//interval*interval+interval-1;marks={}
    for _ in range(100):
        batch=bn.fapi("/fapi/v1/markPriceKlines",{"symbol":SYM,"interval":"8h","startTime":start,"endTime":end,"limit":1500},signed=False)
        if not batch:break
        for x in batch:
            price=finite(x[1],"历史标记价格",0)
            if price<=0:raise DataError("历史标记价格必须为正")
            marks[int(x[0])]=price
        next_start=int(batch[-1][0])+interval
        if next_start<=start:raise DataError("标记价格分页没有前进")
        start=next_start
        if len(batch)<1500 or start>end:break
    else:raise DataError("标记价格分页超过保护上限")
    for row in missing:
        key=row["t"]//interval*interval
        if key not in marks:raise DataError("回测持有期的历史标记价格仍缺失")
        row["markPrice"]=marks[key];row["markPriceSource"]="mark_kline_open_8h";row["markPriceApproximate"]=True
    return rows

def refresh_funding(bn, end_ms=None, rebuild=False):
    try: old = load_funding()
    except Exception: old = []; rebuild = True
    try:
        candles=load_cache();first=int(candles[0]["t"])
        first_trade=int(candles[min(60,len(candles)-1)]["t"])
        rebuild = rebuild or any(r["t"]>=first_trade and not r.get("markPrice") for r in old)
        start = first if rebuild else old[-1]["t"]+1
        end_ms = int(dt.datetime.now(dt.UTC).timestamp()*1000) if end_ms is None else int(end_ms)
        merged = {int(r["t"]):r for r in old}; received = 0
        for _ in range(100):
            batch = bn.fapi("/fapi/v1/fundingRate",{"symbol":SYM,"startTime":start,"endTime":end_ms,"limit":1000},signed=False)
            if not batch: break
            for x in batch:
                if x.get("symbol",SYM) != SYM: raise DataError("资金费标的错误")
                t = int(x["fundingTime"]); rate = finite(x["fundingRate"],"资金费率")
                if not start <= t <= end_ms: raise DataError("资金费返回范围异常")
                row = {"t":t,"rate":rate}
                if x.get("markPrice"):
                    row["markPrice"] = finite(x["markPrice"],"标记价格",0)
                    if row["markPrice"]<=0:raise DataError("资金费标记价格必须为正")
                if "markPrice" not in row and merged.get(t,{}).get("markPrice"):
                    for key in ("markPrice","markPriceSource","markPriceApproximate"):
                        if key in merged[t]:row[key]=merged[t][key]
                merged[t] = row; received += 1
            next_start = max(int(x["fundingTime"]) for x in batch)+1
            if next_start <= start: raise DataError("资金费分页没有前进")
            start = next_start
            if len(batch)<1000 or start>end_ms: break
        else: raise DataError("资金费分页超过保护上限")
        if rebuild and not received: raise DataError("资金费重建未获取历史数据")
        strategy.validate_funding(list(merged.values()),first_trade,end_ms//86400000*86400000)
        if received:
            values=enrich_funding_marks(bn,sorted(merged.values(),key=lambda r:r["t"]),first_trade)
            _atomic_write(FUND,json.dumps(values))
        return len(merged)-len(old),None
    except Exception as error: return 0,f"{type(error).__name__}: {error}"


def fetch_all(bn):
    out, start = [], 1567900800000
    for _ in range(100):
        batch = bn.fapi("/fapi/v1/klines",{"symbol":SYM,"interval":"1d","startTime":start,"limit":1500},signed=False)
        if not batch: break
        next_start = int(batch[-1][0])+86400000
        if next_start <= start: raise DataError("日线分页没有前进")
        out.extend(kline_record(x) for x in batch); start = next_start
        if len(batch) < 1500: break
    else: raise DataError("日线分页超过保护上限")
    return validate_bars(out,continuous=True)


def load_cache(bn=None, rebuild=False):
    if not rebuild:
        try: return validate_bars(json.loads(CACHE.read_text(encoding="utf-8")))
        except Exception:
            if bn is None: raise DataError("日线缓存无效，需联网 --rebuild") from None
    if bn is None: raise DataError("缓存重建需要网络")
    bars = fetch_all(bn); _atomic_write(CACHE,json.dumps(bars)); return bars


def refresh(bn, now_ms=None):
    old = load_cache(bn); fixed = []; st = {"ok":False,"err":None}
    try:
        merged = {b["t"]:b for b in old}; start = old[-1]["t"]-5*86400000; received = 0
        for _ in range(100):
            batch = bn.fapi("/fapi/v1/klines",{"symbol":SYM,"interval":"1d","startTime":start,"limit":1500},signed=False)
            if not batch: break
            for x in batch:
                received += 1
                row = kline_record(x); prior = merged.get(row["t"])
                if prior and prior["c"] != row["c"]: fixed.append((row["t"],prior["c"],row["c"]))
                merged[row["t"]] = row
            next_start = int(batch[-1][0])+86400000
            if next_start<=start: raise DataError("日线分页没有前进")
            start = next_start
            if len(batch)<1500: break
        else: raise DataError("日线分页超过保护上限")
        if not received: raise DataError("行情API返回空数据，禁止将缓存视为刷新成功")
        old = sorted(merged.values(),key=lambda b:b["t"]); validate_bars(old)
        _atomic_write(CACHE,json.dumps(old)); st["ok"] = True
        st["fund_add"],st["fund_err"] = refresh_funding(bn,end_ms=now_ms)
    except Exception as error: st["err"] = f"{type(error).__name__}: {error}"
    return old,fixed,st


def ma(x, k):
    o = np.full(len(x), np.nan)
    if len(x) >= k:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        o[k - 1:] = (cs[k:] - cs[:-k]) / k
    return o


def funding_by_day():
    result = {}
    for row in load_funding():
        day = int(row["t"]//86400000); result[day] = result.get(day,0)+row["rate"]
    return result


def funding_settle_count():
    result = {}
    for row in load_funding():
        day = int(row["t"]//86400000); result[day] = result.get(day,0)+1
    return result


def signal_of(bars):
    if len(bars)<MA_WINDOW: return None
    c = np.array([b["c"] for b in bars],float); average = ma(c,MA_WINDOW)
    rv = realized_vol(bars); prior_vol = realized_vol(bars[:-1])
    if not np.isfinite(rv) or rv<=0: raise DataError("波动率无效")
    trend = bool(c[-1]>average[-1]); active = trend and (VOL_CAP is None or rv<=VOL_CAP)
    prior = (bool(c[-2]>average[-2]) and np.isfinite(prior_vol) and prior_vol>0 and (VOL_CAP is None or prior_vol<=VOL_CAP)) if np.isfinite(average[-2]) else None
    return {"bars":bars,"close":float(c[-1]),"ma50":float(average[-1]),
        "dist_pct":float((c[-1]/average[-1]-1)*100),"trend_long":trend,"long":bool(active),
        "prev_long":prior,"rvol":rv,"reason":"trend_off" if not trend else ("vol_cap" if not active else "in_market"),"bar_t":bars[-1]["t"]}


def pick_method(equity):
    if TARGET_VOL_OVERRIDE is None:
        return METHODS[0] + (None,None)
    base = min((m for m in METHODS if m[1] is not None),key=lambda m:abs(m[1]-TARGET_VOL_OVERRIDE))
    need = base[2]*base[1]/TARGET_VOL_OVERRIDE
    return (f"波动率目标 {TARGET_VOL_OVERRIDE*100:g}%",TARGET_VOL_OVERRIDE,need,base[3],base[4],None,None)


def dynamic_drawdown(bars, fund_by_day, start_equity):
    if not bars or len(bars)<=60 or start_equity<=0: return None
    from align import simulate
    funding = load_funding()
    result = simulate(bars,funding,TARGET_VOL_OVERRIDE,start_equity,FEE_PER_SIDE,
                      rules=current_rules(),constrained=True,price_buffer=.001)
    equity = np.r_[start_equity,result["equity"][60:]]
    dd = float(np.min(equity/np.maximum.accumulate(equity)-1))
    return {"dd":dd,"low":float(equity.min()),"peak":float(equity.max()),
            "end":float(equity[-1]),"switches":0,"switch_log":[],"model":result["model"]}


def realized_vol(bars, win=None):
    win = VOL_WINDOW if win is None else win
    if len(bars)<win+1: return float("nan")
    c = np.array([b["c"] for b in bars[-win-1:]],float)
    if not np.isfinite(c).all() or np.any(c<=0): return float("nan")
    return float((np.diff(c)/c[:-1]).std(ddof=1)*np.sqrt(365))


MAX_LEV_SET = strategy.MAX_LEVERAGE


def max_pos_for(nlev, fee=None):
    """
    整数杠杆 nlev 下【能真正开出来】的最大仓位。
    w/n + w·fee ≤ 1  ⟺  w ≤ n/(1 + n·fee)
    """
    f = FEE_PER_SIDE if fee is None else fee
    return nlev / (1.0 + nlev * f)


MAX_POS = max_pos_for(MAX_LEV_SET)


def feasible_pos(w, fee=None):
    return min(max(0, finite(w,"仓位")),max_pos_for(MAX_LEV_SET,fee))


def target_position(equity, rvol, method):
    return strategy_position(True,rvol,equity,method[1],FEE_PER_SIDE,VOL_CAP,MIN_NOTIONAL)[0]


def leverage_plan(equity, position, notional):
    if notional<=0 or equity<=0: return None
    return {"lev":MAX_LEV_SET,"margin":notional/MAX_LEV_SET,
            "spare":max(0,equity-notional/MAX_LEV_SET),
            "can_open":notional/MAX_LEV_SET+notional*FEE_PER_SIDE<=equity+1e-9,
            "lev_ok":True}


def advice(equity, price, rvol=None, trend_long=True, fee=None):
    fee = FEE_PER_SIDE if fee is None else fee
    try:
        equity = finite(equity,"权益",0); price = finite(price,"价格",0)
        if equity<=0 or price<=0: raise ValueError("价格和权益必须为正")
        name,tv,need,sh,dd,nxt,gap = pick_method(equity)
        position,reason = strategy_position(trend_long,rvol,equity,tv,fee,VOL_CAP,MIN_NOTIONAL)
        notional = equity*position
        fail = "notional" if position>0 and notional<MIN_NOTIONAL else None
        return {"method":name,"target_vol":tv,"threshold":need,"position":position,
            "notional":notional,"qty":notional/price,"feasible":fail is None,"fail":fail,
            "reason":reason,"sharpe":sh,"drawdown":dd,"next":nxt,"gap":gap,
            "need_equity":MIN_NOTIONAL/position if position else float("inf"),
            "worst":equity*(1+dd),"lev_plan":leverage_plan(equity,position,notional)}
    except (ValueError,TypeError,OverflowError):
        return {"method":"—","target_vol":TARGET_VOL_OVERRIDE,"position":0.0,"notional":0.0,
            "qty":0.0,"feasible":False,"fail":"bad_input","reason":"invalid_data","lev_plan":None}


REBALANCE_THRESHOLD = strategy.SOFT_REBALANCE


def order_decision(equity, price, target_pos, held=None):
    state = None if held is None else PositionState(long_qty=max(0,held),short_qty=max(0,-held))
    plan = plan_order(equity,price,target_pos,state,current_rules(),available=equity)
    dq = plan.quantity if plan.side=="BUY" else -plan.quantity
    return (None if held is None else plan.actionable),dq,plan.min_order_qty,plan.target_qty


def decide_action(sig_long, prev_long, tgt_pos, rows, do_order=None, today=None, order=None):
    if order is not None: return order.action
    if do_order is False: return "不动"
    if do_order is True: return "调仓"
    return "未查持仓"



# Bound the whole price/account sampling interval, not the position updateTime
# (which can legitimately stay old when an unchanged position is queried).
MAX_SNAPSHOT_AGE_MS = 120_000

def validate_decision_freshness(bar_ms, reference_ms, decision_ms):
    if not 0 <= decision_ms-reference_ms <= MAX_SNAPSHOT_AGE_MS:
        raise DataError("行情与账户查询跨度超过120秒或时钟倒退，请重新运行")
    if bar_ms != (decision_ms//86400000-1)*86400000:
        raise DataError("查询已跨越UTC日线边界，信号不是最近完成日线，请重新运行")


def run(a):
    from binance_api import BN
    bn = BN()
    # ⚠️ 2026-10-08 P0a：先加载交易所规则（MIN_NOTIONAL/stepSize 动态化）
    _rules_src = load_exchange_rules(bn)
    L = []
    A = L.append

    server_ms = int(bn.fapi("/fapi/v1/time",signed=False)["serverTime"])
    bars, fixed, st = refresh(bn,now_ms=server_ms)
    bars, note = complete_bars(bars,server_ms)
    if not st["ok"]:
        return f"⛔ 行情刷新失败，暂停建议：{st['err']}", None
    validate_bars(bars)
    validate_bars(bars[-max(MA_WINDOW,VOL_WINDOW+1):],continuous=True)
    if bars[-1]["t"] != (server_ms//86400000-1)*86400000:
        return "⛔ 缺少最近已完成 UTC 日线，暂停建议", None
    # ⚠️ 数据连续性检查（2026-10-07 加）——
    #    波动率窗口内若漏掉一天大跌，波动率会虚低 ⇒ 仓位偏大 ⇒ 过度杠杆。
    #    这是唯一指向【危险方向】的失效，所以必须显式拦住。
    gaps, gapnote = check_bar_continuity(bars)
    gap_in_win = any(
        (b["t"] - a["t"]) / 3600000.0 > 25.0
        for i, (a, b) in enumerate(zip(bars, bars[1:]))
        if i >= max(0, len(bars) - VOL_WINDOW - 2))
    sig = signal_of(bars)
    if sig is None:
        A("=" * 78)
        A(f"  {SYM} MA{MA_WINDOW} 趋势信号  "
          f"[V2: {VOL_WINDOW}日波动"
          + (f" + {TARGET_VOL_OVERRIDE*100:g}%档" if TARGET_VOL_OVERRIDE else "")
          + "]")
        A("=" * 78)
        A("")
        A(f"  ⛔ 数据不足，无法出信号")
        A(f"     缓存 {len(bars)} 根，MA{MA_WINDOW} 至少需要 {MA_WINDOW} 根")
        A(f"     （{note}）")
        return "\n".join(L), None
    try:
        fday = funding_by_day()
    except Exception as error:
        return f"⛔ 资金费缓存不可用，暂停建议：{type(error).__name__}", None

    now = dt.datetime.now()
    bd = dt.datetime.fromtimestamp(sig["bar_t"] / 1000, dt.UTC)
    reference_price_ms = int(bn.fapi("/fapi/v1/time",signed=False)["serverTime"])
    ticker = bn.fapi("/fapi/v1/ticker/price", {"symbol": SYM}, signed=False)
    if ticker.get("symbol",SYM)!=SYM: raise DataError("实时价格标的错误")
    price = finite(ticker["price"],"实时价格",0)
    if price<=0: raise DataError("实时价格必须为正")

    A("=" * 78)
    A(f"  {SYM} MA{MA_WINDOW} 趋势信号  "
      f"[V2: {VOL_WINDOW}日波动"
      + (f" + {TARGET_VOL_OVERRIDE*100:g}%档" if TARGET_VOL_OVERRIDE else "")
      + f"]    {now:%Y-%m-%d %H:%M:%S}")
    A("=" * 78)
    A("")

    # ── 体检 ──
    A("  体检")
    A("  " + "-" * 74)
    A(f"  [数据] {note}")
    A(f"  [规则] 最小名义 {MIN_NOTIONAL:.0f}U / 步长 {STEP_SIZE}"
      f" / 市价上限 {MARKET_MAX_QTY:g} ETH"
      f"   （{EXCHANGE_RULES_SRC}）")
    # ⚠️ 连续性检查结果（窗口内缺口 = 危险方向）
    if gaps:
        if gap_in_win:
            A(f"  [数据] 🔴 {gapnote}")
            A("          ⇒ 波动率可能虚低 ⇒ 仓位偏大 ⇒ 请先跑 --rebuild 重建缓存")
            for t0, t1, dh in gaps[:3]:
                d0 = dt.datetime.fromtimestamp(t0 / 1000, dt.UTC)
                d1 = dt.datetime.fromtimestamp(t1 / 1000, dt.UTC)
                A(f"             {d0:%Y-%m-%d} → {d1:%Y-%m-%d}（隔 {dh:.0f} 小时）")
        else:
            A(f"  [数据] ⚠️ {gapnote}")
    else:
        A(f"  [数据] ✅ {gapnote}")
    _last_d = dt.datetime.fromtimestamp(bars[-1]["t"] / 1000, dt.UTC)
    if fixed:
        for t, c0, c1 in fixed:
            d = dt.datetime.fromtimestamp(t / 1000, dt.UTC)
            A(f"  [K线修正] {d:%Y-%m-%d} 收盘 {c0:,.2f} → {c1:,.2f}"
              f"（{(c1/c0-1)*100:+.2f}%）")
    elif st["ok"]:
        # ⚠️ 这里必须把「最后一根是哪天」写出来。
        #    上一版只写「无需修正」，读起来像「一切正常」——
        #    但如果 API 只返回了一部分数据（补了一截就停），
        #    缓存可能仍然很旧，而这一行不会告诉你。
        #    把日期和年龄摆出来，落后就一眼能看见。
        A(f"  [K线修正] 回看最后 5 根：无需修正"
          f"（缓存最后一根 {_last_d:%Y-%m-%d}）")
    else:
        A(f"  [数据刷新] 🔴 拉取失败：{st['err']} —— 用的是过期缓存"
          f"（最后一根 {_last_d:%Y-%m-%d}）")

    _last = dt.datetime.fromtimestamp(bars[-1]["t"] / 1000, dt.UTC)
    _age = (dt.datetime.now(dt.UTC) - _last).total_seconds() / 3600
    if _age > 48:
        A("")
        A(f"  ⛔ 日线太旧（{_age:.1f} 小时），拒绝出信号")
        A(f"     最后一根 {_last:%Y-%m-%d}")
        return "\n".join(L), None
    A("")

    # ── 信号 ──
    A("  信号")
    A(f"  决策日 {bd:%Y-%m-%d} UTC  收盘 {sig['close']:,.2f}  MA50 {sig['ma50']:,.2f}")
    A(f"  趋势：{'高于' if sig['trend_long'] else '不高于'} MA50；10 日年化波动 {sig['rvol']*100:.1f}%")
    A(f"  有效信号：{'做多' if sig['long'] else '空仓'}")
    if sig["reason"]=="vol_cap": A("  波动率超过 120% 上限，主动空仓；这不是数据错误")
    A(f"  实时价 {price:,.2f}")
    A("")

    # ── 成本：仅展示已结算信息，未来资金费不是已知值 ──
    d_sig = int(sig["bar_t"]//86400000); d_next = d_sig+1
    f_sig, f_next = fday.get(d_sig), fday.get(d_next)
    counts = funding_settle_count()
    A("  成本")
    A(f"  手续费参考：单边 {FEE_PER_SIDE*100:.3f}% 名义；账户实际费率在数量验证时读取")
    if f_sig is not None: A(f"  决策日资金费已结算合计 {f_sig*100:+.5f}%（{counts.get(d_sig,0)} 次）")
    if f_next is not None: A(f"  当前持有日已结算合计 {f_next*100:+.5f}%（{counts.get(d_next,0)} 次；当天可能未完成）")
    A("  每次资金费由该结算时点的持仓承担；00:00 调整前持仓与调整后持仓须区分")
    A("  未来资金费和成交滑点未知，上述日合计不是你的实际账户费用")
    A("")

    # ── 执行建议：唯一 OrderPlan 同时供显示和归档使用 ──
    eq = None; held = None; state = None; tgt_pos = 0.0; available = None
    rvol = sig["rvol"]
    adv = {"method":"","target_vol":TARGET_VOL_OVERRIDE,"position":0.0,"notional":0.0,"qty":0.0,"reason":sig["reason"]}
    plan = OrderPlan("hold","未读取账户")
    account_checked = False
    decision_ms = None
    if a.check or a.archive:
        plan = OrderPlan("blocked","账户或持仓尚未验证")
        try:
            acct = bn.futures_account()
            if not isinstance(acct.get("multiAssetsMargin"),bool):
                raise DataError("保证金资产模式未知")
            if acct["multiAssetsMargin"] is True:
                raise DataError("多资产保证金模式暂不支持，暂停数量建议")
            if acct.get("canTrade") is not True:
                raise DataError("账户交易资格不可用或未知，暂停数量建议")
            eq = finite(acct["totalMarginBalance"],"权益",0)
            available = finite(acct["availableBalance"],"可用余额",0)
            mode = bn.fapi("/fapi/v1/positionSide/dual",signed=True)
            if not isinstance(mode.get("dualSidePosition"),bool): raise DataError("账户持仓模式未知")
            state = parse_positions(bn.positions(SYM),mode["dualSidePosition"])
            held = state.long_qty
            commission = bn.fapi("/fapi/v1/commissionRate",{"symbol":SYM},signed=True)
            fee = finite(commission["takerCommissionRate"],"市价手续费",0)
            if commission.get("symbol")!=SYM: raise DataError("手续费标的错误")
            adv = advice(eq,price,rvol,trend_long=sig["trend_long"],fee=fee)
            tgt_pos = adv["position"]
            if adv.get("fail")=="bad_input": raise DataError("仓位输入无效")
            pending = validate_pending_orders(bn)
            decision_ms = int(bn.fapi("/fapi/v1/time",signed=False)["serverTime"])
            validate_decision_freshness(sig["bar_t"],reference_price_ms,decision_ms)
            plan = plan_order(eq,price,tgt_pos,state,current_rules(),available,fee)
            account_checked = True
            A("  账户与调仓")
            A("  ETHUSDT 未成交委托：普通0、条件0（已查询两个接口）")
            A(f"  权益 {eq:.4f} USDT；可用保证金 {available:.4f} USDT")
            A(f"  {'双向' if state.hedge else '单向'}模式；多仓 {held:.3f} ETH；空仓 {state.short_qty:.3f} ETH")
            A(f"  目标仓位 {tgt_pos:.4f}x；目标持仓 {format_qty(plan.target_qty)} ETH")
            A(f"  普通双向调仓门限 {strategy.SOFT_REBALANCE:.0%} 权益 = {eq*strategy.SOFT_REBALANCE:.4f} USDT（取整后的本次交易名义）")
            A(f"  ETHUSDT 最小名义 {MIN_NOTIONAL:g}U；数量步长 {STEP_SIZE:g}；市价数量上限 {MARKET_MAX_QTY:g}")
            if plan.actionable:
                verb = "买入（开多/加多）" if plan.side=="BUY" else ("卖出（平多）" if plan.target_qty==0 else "卖出（减多）")
                A(f"  ⇒ 【{verb} {format_qty(plan.quantity)} ETH】")
                A(f"     方向 {plan.position_side}；{'仅减仓' if plan.reduce_only else '按指定持仓方向操作'}")
                A(f"     数量已向下按步长取整；保证金和手续费已核对；实际成交前仍应核对价格")
            elif plan.status=="blocked": A(f"  ⛔ 暂停建议：{plan.reason}")
            else: A(f"  ⇒ 【不用动】（{plan.reason}）")
            A(f"  策略增仓要求：逐仓、3x；当前 {state.margin_type}、{state.leverage}x")
            if state.margin_type=="isolated" and state.isolated_wallet is not None:
                A(f"  实际逐仓钱包 {state.isolated_wallet:.4f} USDT"+
                  (f"；逐仓权益 {state.isolated_margin:.4f} USDT" if state.isolated_margin is not None else ""))
            if state.liquidation_price>0:
                if state.mark_price is None:
                    A(f"  当前 API 强平价 {state.liquidation_price:,.2f}；持仓标记价缺失，强平距离未知")
                else:
                    A(f"  持仓标记价 {state.mark_price:,.2f}；当前 API 强平价 {state.liquidation_price:,.2f}；相对标记价 {(state.liquidation_price/state.mark_price-1)*100:+.1f}%")
            A("  账户、持仓和价格来自多次只读请求，不是原子快照")
            A("  波动率上限不能保证避免突发暴跌或未来强平；低波动下任何档位均可能接近 3x")
        except Exception as error:
            plan = OrderPlan("blocked",f"账户依赖失败：{type(error).__name__}: {error}")
            A(f"  ⛔ 暂停建议：{plan.reason}")
        A("")

    # ── 规则提醒 ──
    A("  每天 UTC 00:00 后读取完成日线；只做多；默认目标波动 60%")
    A(f"  普通加减仓门限均为权益 {strategy.SOFT_REBALANCE:.0%}；新开仓、目标归零或持仓名义超过3倍权益时不受软门限限制")
    A("  减仓不使用增仓的最小名义门限；双向持仓模式必须明确 LONG")
    if st.get("fund_err"): A(f"  ⚠️ 资金费刷新失败，成本数据未验证：{st['fund_err']}")
    A("  数量建议仅对应本次采样；价格或持仓变化后重新运行，执行前核对订单")
    A("  历史统计使用日线开盘价近似成交与逐次资金费；不构成真实成交或零强平验证")
    A("")

    if decision_ms is None:
        decision_ms = int(bn.fapi("/fapi/v1/time",signed=False)["serverTime"])
    if not (a.check or a.archive):
        validate_decision_freshness(sig["bar_t"],reference_price_ms,decision_ms)
    rule_config = dict(ma=MA_WINDOW,vol=VOL_WINDOW,target_vol=adv.get("target_vol"),
                       vol_cap=strategy.VOL_CAP,leverage=strategy.MAX_LEVERAGE,
                       rebalance_pct=strategy.SOFT_REBALANCE,rules=asdict(current_rules()))
    rule_version = hashlib.sha256(pathlib.Path(strategy.__file__).read_bytes() +
                                  json.dumps(rule_config,sort_keys=True).encode()).hexdigest()
    rec = {
        "decision_id":uuid.uuid4().hex,"decision_ms":str(decision_ms),
        "decision_at":dt.datetime.fromtimestamp(decision_ms/1000,dt.UTC).isoformat(),
        "account_scope":hashlib.sha256(bn.key.encode()).hexdigest()[:24] if getattr(bn,"key",None) else "",
        "reference_price":str(price),"reference_price_ms":str(reference_price_ms),"rule_version":rule_version,
        "symbol":SYM,"date":bd.strftime("%Y-%m-%d"),"bar_ms":str(sig["bar_t"]),
        "bar_close":f"{sig['close']:.2f}","ma50":f"{sig['ma50']:.2f}","dist_ma50_pct":f"{sig['dist_pct']:.2f}",
        "signal":"做多" if sig["long"] else "空仓","trend_signal":"做多" if sig["trend_long"] else "空仓",
        "decision_reason":sig["reason"],"action":plan.action if account_checked else "未验证",
        "side":plan.side,"position_side":plan.position_side,"reduce_only":str(plan.reduce_only),
        "order_qty":f"{format_qty(plan.quantity)}" if plan.actionable else "",
        "target_qty":f"{format_qty(plan.target_qty)}" if account_checked else "",
        "validation_status":plan.status if (a.check or a.archive) else "signal_only",
        "rebalance_policy":"symmetric","soft_rebalance_pct":f"{strategy.SOFT_REBALANCE:.4f}",
        "advice_qty":f"{format_qty(plan.target_qty)}" if account_checked else "",
        "advice_notional":f"{plan.target_qty*price:.2f}" if account_checked else "",
        "held_qty_at_signal":str(held) if account_checked else "",
        "available_at_signal":str(available) if account_checked else "",
        "equity_at_signal":f"{eq:.4f}" if account_checked else "","method":adv.get("method","") if account_checked else "",
        "target_vol":f"{adv['target_vol']:.2f}" if account_checked and adv.get("target_vol") is not None else "",
        "target_position":f"{tgt_pos:.4f}" if account_checked else "","lev_setting":str(state.leverage) if account_checked else "",
        "margin_mode":state.margin_type if account_checked else "","liq_acc_pct":"",
        "mark_price_at_position":str(state.mark_price) if account_checked and state.mark_price is not None else "",
        "liquidation_price_at_position":str(state.liquidation_price) if account_checked else "",
        "liquidation_distance_pct":f"{(1-state.liquidation_price/state.mark_price)*100:.6f}" if account_checked and state.mark_price and state.liquidation_price>0 else "",
        "isolated_wallet_at_position":str(state.isolated_wallet) if account_checked and state.isolated_wallet is not None else "",
        "isolated_margin_at_position":str(state.isolated_margin) if account_checked and state.isolated_margin is not None else "",
        "realized_vol_pct":f"{rvol*100:.2f}","funding_pct_today":f"{f_sig*100:.5f}" if f_sig is not None else "",
        "funding_pct_next":f"{f_next*100:.5f}" if f_next is not None else "","entry_ref":f"{sig['close']:.2f}",
        "fwd_1d":"","fwd_7d":"","fwd_10d":"","fwd_30d":"","max_dd_10d":"","checked_at":"","status":"", "notes":plan.reason,
    }
    return "\n".join(L), rec


@contextmanager
def archive_lock(timeout=10):
    import time as clock
    lock = pathlib.Path(str(ARCHIVE)+".lock");lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open("a+b") as f:
        if f.tell()==0: f.write(b"0"); f.flush()
        deadline = clock.monotonic()+timeout
        while True:
            try:
                f.seek(0)
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if clock.monotonic()>=deadline: raise DataError("日志被其他进程占用")
                clock.sleep(.05)
        try: yield
        finally:
            f.seek(0)
            if os.name=="nt": msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(f,fcntl.LOCK_UN)

def _save_archive_unlocked(rows):
    import io
    extra = sorted(set().union(*(r.keys() for r in rows))-set(FIELDS)) if rows else []
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf,fieldnames=FIELDS+extra);writer.writeheader()
    for row in rows:
        row = dict(row)
        if not row.get("validation_status"): row["validation_status"]="legacy_unverified"
        writer.writerow(row)
    _atomic_write(ARCHIVE,buf.getvalue(),encoding="utf-8-sig")

def load_archive():
    if not ARCHIVE.exists(): return []
    with ARCHIVE.open(encoding="utf-8-sig",newline="") as f: return list(csv.DictReader(f))


def save_archive(rows):
    with archive_lock(): _save_archive_unlocked(rows)


def do_archive(rec):
    # Authoritative per-run evidence; daily CSV remains a convenient latest-day view.
    from execution_ledger import append_decision
    append_decision(ARCHIVE.with_name("ma50_decisions.json"),rec)
    with archive_lock():
        rows=load_archive();hit=next((i for i,r in enumerate(rows) if r.get("date")==rec["date"]),None)
        if hit is not None:
            for key in ("fwd_1d","fwd_7d","fwd_10d","fwd_30d","max_dd_10d",
                        "price_return_1d","price_return_7d","price_return_10d","price_return_30d",
                        "price_drawdown_10d","checked_at","status"):
                if rows[hit].get(key): rec[key]=rows[hit][key]
            merged=dict(rows[hit]);merged.update(rec);rows[hit]=merged
        else: rows.append(rec)
        rows.sort(key=lambda r:r["date"]);_save_archive_unlocked(rows)
    print(f"  已归档 {rec['date']}：{rec['signal']} / {rec['action']}")


def backfill():
    from binance_api import BN
    bn=BN();now_ms=int(bn.fapi("/fapi/v1/time",signed=False)["serverTime"])
    bars,_,st=refresh(bn,now_ms);bars,_=complete_bars(bars,now_ms)
    if not st["ok"]: raise DataError("回填行情刷新失败")
    validate_bars(bars,continuous=True)
    if not bars or bars[-1]["t"]!=(now_ms//86400000-1)*86400000:
        raise DataError("回填缺少最新完成日线")
    index={int(b["t"]):i for i,b in enumerate(bars)};filled=0
    with archive_lock():
        rows=load_archive()
        for row in rows:
            try:
                i=index.get(int(row["bar_ms"]));base=finite(row["entry_ref"],"价格诊断参考价",0)
            except (ValueError,TypeError,KeyError):
                row["status"]="failed";continue
            if i is None or base<=0: row["status"]="failed";continue
            got=0
            for h in (1,7,10,30):
                if i+h<len(bars):
                    value=f"{(bars[i+h]['c']/base-1)*100:.3f}"
                    row[f"price_return_{h}d"]=value;row[f"fwd_{h}d"]=value;got+=1
            if i+10<len(bars):
                prices=np.array([base]+[bars[j]["c"] for j in range(i+1,i+11)])
                row["price_drawdown_10d"]=f"{np.min(prices/np.maximum.accumulate(prices)-1)*100:.3f}"
                # Keep the legacy field as the original lowest-close/from-entry metric.
                row["max_dd_10d"]=f"{(prices.min()/base-1)*100:.3f}"
            row["status"]="complete" if got==4 else ("partial" if got else "pending")
            row["checked_at"]=dt.datetime.now(dt.UTC).isoformat();filled+=got>0
        _save_archive_unlocked(rows)
    print(f"  回填 {filled} 条：ETH 价格诊断，不是含成本的策略收益")


def history():
    rows=load_archive()
    if not rows: print("还没有归档。");return
    days=sorted({r["date"] for r in rows});legacy=sum(r.get("validation_status") in (None,"","legacy_unverified") for r in rows)
    print(f"MA50 归档：{len(days)} 天；旧版未验证记录 {legacy} 条")
    if len(days)>1:
        first=dt.date.fromisoformat(days[0]);last=dt.date.fromisoformat(days[-1])
        missing={str(first+dt.timedelta(days=i)) for i in range((last-first).days+1)}-set(days)
        if missing:print(f"缺少 {len(missing)} 天：{', '.join(sorted(missing)[:8])}")
    print("日期         有效信号  动作    ETH后10天价格涨跌  状态")
    for row in rows[-25:]:
        value=row.get("price_return_10d") or row.get("fwd_10d") or "—"
        print(f"{row['date']}  {row.get('signal','—')}  {row.get('action','—')}  {value}  {row.get('validation_status','旧版未验证')}")
    print("价格涨跌未含变仓、手续费或资金费；记录天数不能直接证明策略有效。")


def sync_methods():
    import ast
    from align import panel,sharpe,max_dd,cagr
    raw,_=complete_bars(load_cache());validate_bars(raw,continuous=True);funding=load_funding()
    if any(r["t"]>=raw[60]["t"] and not r.get("markPrice") for r in funding):raise DataError("需先补齐回测持有期资金费标记价格")
    C=np.array([b["c"] for b in raw]);days=funding_by_day();FR=np.array([days.get(int(b["t"]//86400000),0) for b in raw])
    P=panel(C,FR,FEE_PER_SIDE,bars=raw,funding=funding);W=60;rows=[]
    for name,tv,_,_,_ in METHODS:
        x=P.net(tv,lev=LEVERAGE)[W:];on=P.weight(tv)[W:];on=on[on>0]
        need=MIN_NOTIONAL/MAX_POS if tv is None else MIN_NOTIONAL/float(np.percentile(on,10))
        rows.append((name,tv,round(need,1),round(sharpe(x),4),round(max_dd(x),3)))
        print(f"{name}: 几何年化 {cagr(x)*100:.2f}%；回撤 {max_dd(x)*100:.2f}%；倍数 {np.prod(1+x):.3f}")
    dd_rows=[(lev,round(max_dd(P.net(None,lev=lev)[W:]),3)) for lev,_ in DD_BY_LEV]
    replacements={"METHODS":rows,"DD_BY_LEV":dd_rows,"METHODS_ASOF":dt.datetime.now(dt.UTC).strftime("%Y-%m-%d"),
                  "SAMPLE_DAYS":len(raw)-W,"SAMPLE_YEARS":round((len(raw)-W)/365,2)}
    path=pathlib.Path(__file__);source=path.read_text(encoding="utf-8");tree=ast.parse(source);lines=source.splitlines(keepends=True)
    assignments=[n for n in tree.body if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id in replacements]
    if len(assignments)!=len(replacements):raise DataError("统计表声明不完整，拒绝修改源码")
    for n in sorted(assignments,key=lambda n:n.lineno,reverse=True):
        name=n.targets[0].id;lines[n.lineno-1:n.end_lineno]=[f"{name} = {replacements[name]!r}\n"]
    updated="".join(lines);updated_tree=ast.parse(updated)
    if {n.name for n in tree.body if isinstance(n,ast.FunctionDef)}!={n.name for n in updated_tree.body if isinstance(n,ast.FunctionDef)}:
        raise DataError("统计同步影响到函数，拒绝保存")
    _atomic_write(path,updated)
    print("统计表已同步：现金流模型；不含真实滑点和盘中强平验证")


def selfcheck():
    from align import panel,sharpe,max_dd
    errors=[]
    try:
        raw,_=complete_bars(load_cache());validate_bars(raw,continuous=True);funding=load_funding()
        if len(raw)<70:raise DataError("自检至少需要 70 根完成日线")
        if any(r["t"]>=raw[60]["t"] and not r.get("markPrice") for r in funding):raise DataError("持有期资金费缺少结算标记价格，请联网更新")
        if any(r.get("markPriceApproximate") for r in funding):print("⚠️ 部分早期标记价使用 API 8h 开盘近似，统计不等同账户实际扣费")
        C=np.array([b["c"] for b in raw]);day_rates=funding_by_day();FR=np.array([day_rates.get(int(b["t"]//86400000),0) for b in raw])
        P=panel(C,FR,FEE_PER_SIDE,bars=raw,funding=funding);W=60
        for name,tv,need,expected_sh,expected_dd in METHODS:
            x=P.net(tv,lev=LEVERAGE)[W:];w=P.weight(tv)[W:];on=w[w>0]
            actual_need=MIN_NOTIONAL/MAX_POS if tv is None else MIN_NOTIONAL/float(np.percentile(on,10))
            if abs(sharpe(x)-expected_sh)>.003 or abs(max_dd(x)-expected_dd)>.005:
                errors.append(f"{name} 历史表超出容差")
            if abs(actual_need-need)>.15:errors.append(f"{name} 统计分位门槛错误")
            if np.any((w>0)&(~P.sig[W-1:-1].astype(bool))):errors.append(f"{name} 趋势空仓仍有目标")
            if np.any((w>0)&(P.vol[W:]>VOL_CAP)):errors.append(f"{name} 波动上限未生效")
        for lev,expected_dd in DD_BY_LEV:
            if abs(max_dd(P.net(None,lev=lev)[W:])-expected_dd)>.005:errors.append(f"{lev}x 回撤表错误")
        for tv in (.15,.25,.4,.6):
            position,_=strategy_position(True,.05,90,tv)
            plan=plan_order(90,2700,position,PositionState(),current_rules(),90)
            if plan.actionable and plan.side=="BUY" and plan.required_funds>90+1e-9:
                errors.append("取整后的保证金超预算")
    except Exception as error:errors.append(f"{type(error).__name__}: {error}")
    for error in errors:print(f"❌ {error}")
    if not errors:print("✅ 全部一致：规则、数据、策略开关、数量预算和现金流统计")
    else:print("自检失败；不会按成功状态退出。统计漂移请运行 --sync-methods。")
    return not errors


def main():
    # Windows redirected consoles may default to GBK, which cannot encode status symbols.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap=argparse.ArgumentParser(description="ETHUSDT MA50 手动交易辅助；只读交易所")
    for flag in ("console","check","archive","history","backfill","selfcheck","rebuild","sync-methods"):
        ap.add_argument("--"+flag,action="store_true")
    ap.add_argument("--daily",action="store_true",help="统一日检：DD采样及成交同步、MA50检查归档、同日核对与操作摘要")
    ap.add_argument("--target-vol",type=float,default=None,help="目标波动率 5~60%%，默认 60")
    ap.add_argument("--offline",action="store_true",help="仅用于 history/selfcheck/sync-methods，使用已验证缓存")
    a=ap.parse_args()
    if a.daily and any((a.history,a.backfill,a.selfcheck,a.rebuild,a.sync_methods,a.offline)):
        ap.error("--daily不能与历史、回填、自检、重建、统计同步或离线模式合用")
    global TARGET_VOL_OVERRIDE,TARGET_VOL_EXPLICIT
    value=DEFAULT_TARGET_VOL if a.target_vol is None else (a.target_vol/100 if a.target_vol>1 else a.target_vol)
    if not np.isfinite(value) or not .05<=value<=.60:ap.error("目标波动率必须在 5%~60%")
    TARGET_VOL_OVERRIDE=value;TARGET_VOL_EXPLICIT=a.target_vol is not None
    try:
        if a.daily:
            from daily_check import run_daily
            return run_daily(a, sys.modules[__name__])
        if a.history:history();return 0
        if a.offline and not (a.selfcheck or a.sync_methods):ap.error("offline 只用于历史/自检/统计同步")
        if a.selfcheck or a.sync_methods:
            if a.offline:load_exchange_rules()
            else:
                from binance_api import BN
                bn=BN();load_exchange_rules(bn);_,err=refresh_funding(bn)
                if err:raise DataError(err)
            if a.sync_methods:sync_methods();return 0
            return 0 if selfcheck() else 1
        if a.rebuild:
            from binance_api import BN
            bn=BN();load_cache(bn,rebuild=True);_,err=refresh_funding(bn,rebuild=True)
            if err:raise DataError(err)
            return 0
        if a.backfill:backfill();return 0
        out,rec=run(a)
        if rec is None:print(out);return 1
        if a.archive:do_archive(rec)
        if a.console:print(out)
        outdir=ROOT/"data/reports";outdir.mkdir(parents=True,exist_ok=True)
        stamp=dt.datetime.now(dt.UTC).astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        report=outdir/f"ma50_{stamp}.md";_atomic_write(report,"```\n"+out+"\n```\n")
        print(f"已生成：{report}")
        return 1 if rec.get("validation_status")=="blocked" else 0
    except Exception as error:
        print(f"⛔ 运行失败，未生成交易指令：{type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
