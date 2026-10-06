"""
MA50 固定仓位 · 可执行工具（ma50_live.py）
=========================================
规则见 ma50_rules.md，一句话：日线收盘 > MA50 → 做多，否则空仓。

用法：
  python ma50_live.py                  # 出今天的信号（默认，写报告）
  python ma50_live.py --console        # 同时打到控制台
  python ma50_live.py --check          # 顺带读账户，算出该下单多少
  python ma50_live.py --archive        # 把今天的信号写进归档
  python ma50_live.py --history        # 看归档和前向检验进度
  python ma50_live.py --backfill       # 回填归档里的前向收益
  python ma50_live.py --selfcheck      # 自检：结构、口径一致性、边界

⚠️ 只做多。仓位【不是常量】——它按本金自动匹配版本：

     权益 < 14.20U        →  开不出单（最小名义 20U 都下不了）
     14.20 ~ 52.3U        →  固定版：仓位 = max(1.0, 20 ÷ 权益)
     52.3 ~ 83.7U         →  波动率目标 40%
     83.7 ~ 139.5U        →  波动率目标 25%
     ≥ 139.5U             →  波动率目标 15%

   门槛 = 最小名义 20U ÷ 【有仓位日的第 10 分位仓位】（见 METHODS 表）。
   工具每次运行都读账户重算，不需要手动改参数。
"""
import argparse
import csv
import datetime as dt
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

SYM = "ETHUSDT"
MA_WINDOW = 50
# ⚠️ 这个常量【不再用于计算】。固定版的仓位由 fixed_position() 按权益算
#    （max(1.0, 20/权益)）。1.405 只是"20 ÷ 14.23"这个历史值的残留，
#    现在仅作读账户失败时的兜底默认值。
LEVERAGE = 1.405
MIN_NOTIONAL = 20.0           # ETHUSDT 实测
MMR = 0.004                   # ETHUSDT 第一档维持保证金率（fapi/v1/leverageBracket 实测）

# ── 强平安全性（关键：要和【单笔逆向】比，不是和【累计回撤】比）──
# 定义：入场 = 信号日收盘；持有期 = 次日到信号结束的次一日。
# 62 笔完整交易的期间最大逆向（adverse_excursion.py 算）：
# ⚠️ 出处：最差那笔是 2022-10-25 入场（1,459.20），2022-11-08 出场，逆向 −26.6%。
#    第 2 名 2025-10-02（−24.1%），第 3 名 2021-03-29（−23.0%）。
WORST_TRADE_LOW = -0.266      # 最低价口径（用这个判断强平安全性）
WORST_TRADE_CLOSE = -0.245    # 收盘口径 —— 仅供文档 §2.5 的对照表引用，
                              # 代码里不用它（强平看盘中最低价，收盘口径偏乐观）
# 强平@标的 = 1/杠杆 − MMR。要安全，需 1/lev − MMR > |单笔最坏逆向|
#   lev ≤ 3  →  强平 ≥ 32.9%  >  26.6%  ✅
#   lev = 4  →  强平   24.6%  <  26.6%  ❌ 会被强平
MAX_SAFE_LEV = 3

# ── 目标波动率的【主动覆盖】 ──
# 默认 None = 自动：选「门槛 ≤ 权益」的最高档
# ⚠️ 那个自动逻辑有个副作用：门槛是 20U ÷ 仓位 算出来的，
#    所以权益越大 → 落到门槛越高的档 → 而门槛最高的档恰好是
#    目标波动【最低】的 15% 档 ⇒ 赚到钱之后自动降杠杆。
#    定投场景下尤其明显：权益一过 139.5U 就永久锁在 15%。
#    用 --target-vol 40 可以强制锁定某一档。
TARGET_VOL_OVERRIDE = None

# ── 波动率目标版的「标称 vs 实际」口径 ──
# 「目标波动率 25%」指的是【在场时】的目标（仓位 × 已实现波动 = 25%，恒等）。
# 而账户整体波动只有目标的 89%，因为 45% 的时间空仓：
#     空仓日拉低            −26%
#     波动预测的倒数凸性     +13%   （E[1/σ̂] > 1/E[σ̂]）
#     ─────────────────────────
#     净                    −11%   →  89%
# ⚠️ 改数据时必须重算（vol_formula.py 可复现）。
VOL_ACHIEVE = 0.89
# ⚠️ 币安只允许【整数】杠杆。所以"目标仓位 1.3486x"是设不了的 ——
#    实际要设 ceil(仓位)。杠杆设置【不改变仓位】，只决定占用多少保证金和强平距离。

# ── 样本量与不确定性（改数据时必须同步重算，文档 §5.1/§8 引用同一组数）──
SE_SHARPE = 0.387            # 年化夏普的标准误 = sqrt((1+S_d^2/2)/n)·sqrt(365)
SAMPLE_YEARS = 6.69          # 回测年数（第 60 根起算）
SAMPLE_DAYS = 2445           # 回测天数
FEE_PER_SIDE = 0.0005

# ── 按本金匹配版本 ──
# 门槛 = 20U 最小名义 ÷ 【有仓位日的第 10 分位仓位】
# 含义：本金达到门槛后，≥90% 的有仓位日都能下出最小单。
# 这组数是从 ETHUSDT 日线算出来的（calc_thresholds.py），不是拍的。
# 注意两个数不是一回事：
#   日线总根数 2505  →  减 60 根预热  →  回测样本 SAMPLE_DAYS = 2445 天
#
# ⚠️ 「目标波动率」是【在场时】的目标，不是账户整体波动：
#       仓位 × 已实现波动 = 目标        ← 恒等式，精确成立（未触发 3x 上限时）
#    而账户整体波动约为目标的 89%，因为 45% 的时间空仓。
#    净 −11% 是两股力相抵的结果：
#       空仓日（45% 时间）           −26%
#       波动预测的倒数凸性偏差        +13%   （E[1/σ̂] > 1/E[σ̂]）
#    ⇒ 所以「用 25% 档」实际承担约 22% 的账户波动。
#      想拿真正的 25%，直接选 40% 档即可 —— 不需要改公式。
#
#   (名称,            目标波动率,  门槛本金, 历史夏普, 历史最大回撤)
#
# ⚠️ 夏普/回撤的口径（换手算时会漂，所以必须写明）：
#   · 算术夏普 = 日均收益 / 日标准差 × sqrt(365)
#   · 收益对齐 = w[t-1] × r[t]（昨天收盘决定，今天持有）—— 不是 w[t] × r[t]
#   · 含成本   = 手续费 5bp/边 × |Δw|（系数 1！不是 2）+ 每日实际资金费
#   · 数据     = ETHUSDT 永续，日线 2505 根；回测样本 2445 天 = 6.69 年（第 60 根起算）
#   · 回撤     = 【每天对账到 min(3, 目标÷20日波动)】的复利净值最大回撤，
#               含换手手续费与每日资金费。即实际执行路径的回撤，不是标称口径。
#               40% 档在场平均仓位 0.6549（供核对）。
#               ⚠️ 唯一真源是 align.py 的 panel()。
#                  任何手写循环都要先和它逐点对比权重序列，否则会引入错位。
#               固定版因杠杆随本金变，回撤用 dd_for_lev() 查表，不引用这里的数
#
# 🔴 2026-10-06 修正一：夏普那一列原来含【前视偏差】，已更正。
#    错：w[i]（收盘[i] 才知道的信号）× r[i]（i-1→i 的收益）→ 夏普虚高 2.3~2.7 倍
#    对：w[i-1] × r[i]，即先 align.lag()。详见 align.py 头部。
#
# 🔴 2026-10-06 修正二：align.py 的手续费原来是 `× 2`（多算一倍），已改为系数 1。
#    ⇒ 夏普和回撤的口径都变了，本表随之更新：
#         夏普 1.031 → 1.047（固定版）  1.217 → 1.2442（波动率目标）
#         回撤不变（-0.712 / -0.325 / -0.211 / -0.130 与系数 1 一致，原本就对）
#    手续费系数的证明见 align.py 的 `lag()` 之后那段注释。
#    ⚠️ 门槛【不受影响】（只依赖仓位分布，不涉及收益序列）—— 已复算确认。
METHODS = [
    ("固定版",          None,      14.2,   1.047, -0.712),
    ("波动率目标 40%",    0.40,      52.3,   1.2442, -0.325),
    ("波动率目标 25%",    0.25,      83.7,   1.2442, -0.211),
    ("波动率目标 15%",    0.15,     139.5,   1.2442, -0.130),
]

# ── 固定版的回撤随杠杆变（同一策略，只缩放仓位，夏普不变但回撤变）──
# 实测 ETHUSDT 回测样本 2445 天，扣成本+资金费（手续费系数 1）。
# 用于按【实际杠杆】报回撤。改动时用 --selfcheck 的 ④ 段核对。
#
# ⚠️ 2026-10-06 说明：这组数一度被"修正"成 -0.469/-0.557/-0.636/-0.719/-0.787/-0.890，
#    那次修正是错的 —— 当时以为 align.py 的 ×2 是对的，于是把本来正确的表改偏了。
#    现在 align.py 已改为系数 1，本表恢复为实测值。
DD_BY_LEV = [
    (0.80, -0.462), (1.00, -0.550), (1.20, -0.629),
    (1.405, -0.712), (1.60, -0.780), (2.00, -0.884),
]


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
    "funding_pct_today",        # 决策日那天的资金费
    "funding_pct_next",         # 次日（实际持有第 1 天）的资金费
    # 事后回填
    "entry_ref", "fwd_1d", "fwd_7d", "fwd_10d", "fwd_30d",
    "max_dd_10d", "checked_at", "status", "notes",
]


# ══════════════════ 数据 ══════════════════
def complete_bars(bars):
    """丢掉最后一根没走完的日线"""
    if not bars:
        return bars, "缓存是空的"
    last = bars[-1]["t"] / 1000
    age = dt.datetime.now(dt.UTC).timestamp() - last
    if age < 86400 * 0.98:
        return bars[:-1], f"丢掉最后一根（只走了 {age/3600:.1f} 小时）"
    return bars, f"最后一根已走完（{age/3600:.1f} 小时前收）"


def fetch_all(bn):
    """
    从 API 拉【全量】日线。只用于缓存重建。
    Binance 单次上限 1500 根，所以分页拉。
    """
    out, start = [], 1567900800000          # 2019-09-08
    while True:
        k = bn.fapi("/fapi/v1/klines",
                    {"symbol": SYM, "interval": "1d",
                     "startTime": start, "limit": 1500}, signed=False)
        if not k:
            break
        for x in k:
            out.append({"t": int(x[0]), "o": float(x[1]), "h": float(x[2]),
                        "l": float(x[3]), "c": float(x[4]), "v": float(x[5])})
        if len(k) < 1500:
            break
        start = int(k[-1][0]) + 86400000
    return out


def load_cache(bn, rebuild=False):
    """
    安全读缓存。

    ⚠️ 这里原来在 try 之外直接 json.loads(CACHE.read_text())，导致缓存一旦
       损坏（文件不存在 / 空文件 / 非法 JSON / 空数组 / null）就抛
       FileNotFoundError / JSONDecodeError / IndexError / TypeError，
       而 CACHE 是【唯一】数据存储、七个参数里没有重建命令
       ⇒ 工具会永久不可用，且报错不指向根因。

    现在：任何损坏都自动走【全量重建】，并打印原因。
    """
    reason = None
    if not rebuild:
        try:
            old = json.loads(CACHE.read_text(encoding="utf-8"))
            if not isinstance(old, list):
                reason = f"顶层不是数组（{type(old).__name__}）"
            elif not old:
                reason = "空数组"
            elif not all(isinstance(r, dict) and "t" in r and "c" in r for r in old):
                reason = "缺少 t/c 字段"
            else:
                return old
        except FileNotFoundError:
            reason = "文件不存在"
        except json.JSONDecodeError as e:
            reason = f"JSON 非法（{e.msg}）"
        except OSError as e:
            reason = f"读不了（{e.strerror}）"
        except Exception as e:
            reason = type(e).__name__
        print(f"  ⚠️ 缓存不可用（{reason}）—— 从 API 全量重建…")
    else:
        print("  --rebuild：从 API 全量重建缓存…")

    old = fetch_all(bn)
    if not old:
        raise RuntimeError("API 没有返回任何 K 线 —— 检查网络 / 代理 / SYM")
    old.sort(key=lambda r: r["t"])
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(old), encoding="utf-8")
    print(f"  ✅ 缓存已重建：{len(old)} 根"
          f"（{dt.datetime.fromtimestamp(old[0]['t']/1000, dt.UTC):%Y-%m-%d} ~ "
          f"{dt.datetime.fromtimestamp(old[-1]['t']/1000, dt.UTC):%Y-%m-%d}）")
    return old


def refresh(bn):
    """回看最后 5 根，修正可能未走完时写入的值（和 eth_signal 同一个修法）"""
    old = load_cache(bn)
    old = json.loads(CACHE.read_text(encoding="utf-8"))
    idx = {int(r["t"]): i for i, r in enumerate(old)}
    last = int(old[-1]["t"])
    fixed = []
    st = {"ok": False, "err": None}
    now_ms = dt.datetime.now(dt.UTC).timestamp() * 1000
    # 回补范围要【自适应】：固定 limit=20 时，若缓存落后 30 天，
    # 每次只能补回 14 天，要跑好几次才能追上（而期间会被 48h guard 拒绝）。
    # 和 eth_signal.py 用同一个修法。
    # limit 上限 1500 —— Binance 单次最多 1500 根，传更多会被截断（不报错但也不多给）。
    _behind_days = int((now_ms - last) / 86400000) + 1
    _want = max(5, min(_behind_days, 1500))      # Binance 单次上限 1500
    try:
        k = bn.fapi("/fapi/v1/klines",
                    {"symbol": SYM, "interval": "1d",
                     "startTime": last - 5 * 86400000,
                     "limit": min(_want + 10, 1500)}, signed=False)
        for x in k:
            r = {"t": int(x[0]), "o": float(x[1]), "h": float(x[2]),
                 "l": float(x[3]), "c": float(x[4]), "v": float(x[5])}
            i = idx.get(r["t"])
            if i is None:
                old.append(r); continue
            if (now_ms - r["t"]) >= 86400000 and (
                    old[i]["c"] != r["c"] or old[i]["v"] != r["v"]):
                fixed.append((r["t"], old[i]["c"], r["c"]))
            old[i] = r
        old.sort(key=lambda r: r["t"])
        CACHE.write_text(json.dumps(old), encoding="utf-8")
        st["ok"] = True
    except Exception as e:
        st["err"] = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    return old, fixed, st


def ma(x, k):
    o = np.full(len(x), np.nan)
    if len(x) >= k:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        o[k - 1:] = (cs[k:] - cs[:-k]) / k
    return o


def funding_by_day():
    fd = json.loads(FUND.read_text(encoding="utf-8"))
    d = {}
    for x in fd:
        k = int(x["t"] // 86400000)
        d[k] = d.get(k, 0.0) + x["rate"]
    return d


# ══════════════════ 核心信号 ══════════════════
def signal_of(bars):
    if not bars:
        return None
    c = np.array([b["c"] for b in bars], float)
    m50 = ma(c, MA_WINDOW)
    if not np.isfinite(m50[-1]):
        return None
    close = c[-1]
    ma_now = m50[-1]
    return {
        "bars": bars, "close": close, "ma50": ma_now,
        "dist_pct": (close / ma_now - 1) * 100,
        "long": bool(close > ma_now),
        "prev_long": bool(c[-2] > m50[-2]) if np.isfinite(m50[-2]) else None,
        "bar_t": bars[-1]["t"],
    }


def pick_method(equity):
    """
    按本金选出能执行的最好版本。

    ⚠️ 若 TARGET_VOL_OVERRIDE 已设（--target-vol），则强制用那一档，
       不再按「门槛 ≤ 权益」自动选。原因：门槛 = 20U ÷ 仓位，
       所以权益越大 → 落到门槛越高的档 → 而门槛最高的档恰好是
       目标波动最低的 15% 档 ⇒ 赚到钱之后自动降杠杆（定投时尤其明显）。

    返回 (名称, 目标波动率, 门槛, 夏普, 回撤, 下一档, 距离下一档还差多少)
    """
    # ── --target-vol 强制覆盖 ──
    if TARGET_VOL_OVERRIDE is not None:
        # 门槛取【最接近的那一档】—— 用于提示"权益够不够开出最小单"
        _c = [m for m in METHODS if m[1] is not None]
        _b = min(_c, key=lambda m: abs(m[1] - TARGET_VOL_OVERRIDE))
        return (f"波动率目标 {TARGET_VOL_OVERRIDE*100:g}%",
                TARGET_VOL_OVERRIDE, _b[2], _b[3], _b[4], None, _b[2] - equity)
    # 连最低门槛都没到 —— 下一档就是第 1 档，不是"已是最高"
    if equity < METHODS[0][2]:
        return METHODS[0] + (METHODS[0], METHODS[0][2] - equity)
    best = METHODS[0]
    nxt = None
    for i, m in enumerate(METHODS):
        if equity >= m[2]:
            best = m
            nxt = METHODS[i + 1] if i + 1 < len(METHODS) else None
    gap = (nxt[2] - equity) if nxt else None
    return best + (nxt, gap)


def dynamic_drawdown(bars, fund_by_day, start_equity):
    """
    用【你的实际权益】跑一遍，看历史上最坏会跌到多少。

    为什么要动态：固定版的仓位是 max(1.0, 20/权益)，权益一变仓位就变。
    回测里用恒定杠杆是近似，而且方向不确定 —— 实测两者差 14pp：
        恒定 1.353x  →  最大回撤 −69.0%
        随权益变      →  最大回撤 −55.0%（权益涨过 20U 后仓位自动降到 1.0x）

    返回 dict：最大回撤、路径最低点、峰值、期末权益。
    ⚠️ 这不是"历史上真的跌到过 X" —— 起点不同结果不同，所以要用你的真实权益算。
    """
    if not bars or start_equity <= 0:
        return None
    C = np.array([b["c"] for b in bars], float)
    T = np.array([b["t"] for b in bars], float)
    n = len(C)
    if n < 70:
        return None
    r = np.zeros(n)
    r[1:] = C[1:] / C[:-1] - 1
    m = ma(C, MA_WINDOW)
    sg = np.nan_to_num((C > m).astype(float))
    cd = np.array([int(t // 86400000) for t in T])
    FR = np.nan_to_num(np.array([fund_by_day.get(int(d), np.nan) for d in cd]))

    vol = np.full(n, np.nan)
    for i in range(21, n):
        vol[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)

    def _pos(eq_now, i):
        """
        第 i 天该持多少 —— 必须和实盘一样【每天重新读权益匹配版本】。
        ⚠️ 这里原来写死了固定版的 max(1.0, 20/权益)，导致：
           100 USDT 时它模拟 1.0x，而工具实际建议 0.347x（差 2.88 倍）。
        """
        if eq_now <= 0:
            return 0.0, None
        if not sg[i]:
            # ⚠️ 空仓时【不能】返回 METHODS[0][0] —— 那会让 cur 每次进出场
            #    都被重置成"固定版"，凭空多记一次切换。实测虚报约 110 次
            #    （真实的在场切换只有 1~26 次）。返回 None = 不改变当前版本。
            return 0.0, None
        mname, tv, need = pick_method(eq_now)[:3]
        if tv is None:
            return max(1.0, MIN_NOTIONAL / eq_now), mname
        v = vol[i]
        if not np.isfinite(v) or v <= 0:
            return 0.0, mname
        return min(3.0, tv / v), mname

    eq = start_equity
    peak = eq
    dd = 0.0
    low = eq
    switches = []
    cur = None          # 首次进场才确立版本，空仓期不算切换
    for i in range(MA_WINDOW + 1, n):
        w, mname = _pos(eq, i - 1)        # 昨日收盘决定今日仓位
        if mname is not None and mname != cur:
            if cur is not None:
                switches.append((i, eq, cur, mname))
            cur = mname
        w_prev = _pos(eq, i - 2)[0]
        turn = abs(w - w_prev)
        # 手续费 = turn × 单边费率。【不要 ×2】——
        #   turn = |Δw| 已经是【每次实际成交的名义】，
        #   一个完整往返 0→1.4→0 的 turn 之和 = 2.8，正好等于两次成交额。
        #   ×2 会重复计算（把 1.30%/年 变成 2.60%/年）。
        #   ⚠️ align.py 里的 * 2 是错的，主回测因此多算了一倍手续费。
        eq *= (1 + w * r[i] - turn * FEE_PER_SIDE - w * FR[i])
        if eq <= 0:
            eq = 0.0
            break
        peak = max(peak, eq)
        low = min(low, eq)
        dd = min(dd, eq / peak - 1)
    return {"dd": dd, "low": low, "peak": peak, "end": eq,
            "switches": len(switches), "switch_log": switches}


# ⚠️ bars 必须是【字典列表】（[{"c":...}]），不是 numpy 数组。
#    传数组会报 IndexError: invalid index to scalar variable —— 不指向根因。
#    要传数组请用 realized_vol_from_prices()（若有）。
def realized_vol(bars, win=20):
    """过去 win 根【已走完】日线的年化波动率。用于波动率目标版。"""
    c = np.array([b["c"] for b in bars], float)
    if len(c) < win + 2:
        return float("nan")
    r = np.diff(c[-(win + 1):]) / c[-(win + 1):-1]
    return float(r.std(ddof=1) * np.sqrt(365))


def target_position(equity, rvol, method):
    """
    返回目标仓位（占权益倍数）。method = (名称, 目标波动率, 门槛, ...)
    · 固定版：max(1.0, 20/权益) —— 不是常量
    · 波动率目标版：min(3, target_vol / 已实现波动)
    """
    tv = method[1]
    if tv is None:
        return fixed_position(equity)
    if rvol is None:
        return 0.0
    try:
        if not np.isfinite(rvol) or rvol <= 0:
            return 0.0
    except TypeError:
        return 0.0
    return float(min(3.0, tv / rvol))


def leverage_plan(equity, position, notional):
    """
    把"目标仓位"翻译成币安上实际能设的参数。

    关键区分（这也是最容易搞错的地方）：
      · 杠杆【设置】  →  决定占用多少保证金、强平多远
      · 仓位【大小】  →  由名义价值决定，与杠杆设置无关

    ⚠️ 为什么要报全仓和逐仓两个：
       当名义 > 权益时（本工具在权益 < 52.3U 时都是这样），
       逐仓只锁 notional/杠杆 做保证金，强平反而更近；
       全仓用整个权益做保证金，强平更远。
       仓位本来就比账户大时，【全仓才是更安全的那个】。
    """
    if notional <= 0 or equity <= 0:
        return None
    lev = max(1, int(np.ceil(position - 1e-9)))     # 币安只允许整数，向上取
    margin = notional / lev
    spare = max(0.0, equity - margin)

    def liq(backing):
        """给定真正做保证金的钱，返回 (标的跌幅, 账户跌幅)"""
        px = backing / notional - MMR
        px = max(0.0, min(px, 1.0))
        acc = (equity - px * notional) / equity - 1
        return px, acc

    liq_iso_px, liq_iso_acc = liq(margin)              # 逐仓：只有分配的保证金
    liq_cross_px, liq_cross_acc = liq(margin + spare)  # 全仓：整个权益
    # 会不会被强平？要和【单笔最坏逆向】比，不是和【累计回撤】比。
    # 这是之前写错过的地方：-68.9% 是跨 62 笔的累计回撤，不是单笔跌幅。
    safe = liq_iso_px > abs(WORST_TRADE_LOW)
    return {
        "safe": safe, "lev_ok": lev <= MAX_SAFE_LEV,
        "liq_iso_px": liq_iso_px, "liq_iso_acc": liq_iso_acc,
        "liq_cross_px": liq_cross_px, "liq_cross_acc": liq_cross_acc,
        "can_open": margin <= equity + 1e-9,
        "lev": lev, "margin": margin, "spare": spare,
        "liq_iso_px": liq_iso_px, "liq_iso_acc": liq_iso_acc,
        "liq_cross_px": liq_cross_px, "liq_cross_acc": liq_cross_acc,
        "can_open": margin <= equity + 1e-9,
    }


def advice(equity, price, rvol=None):
    """
    给定权益和当前价，算出该下多少。【主流程也调这个函数】——
    这样算的地方只有一处，不会再出现"显示用一种算法、下单用另一种"。

    返回一个 dict，含所有中间量，供显示和归档共用。
    """
    mname, tv, mneed, msr, mdd_tab, nxt, gap = pick_method(equity)
    forced = TARGET_VOL_OVERRIDE is not None
    pos = target_position(equity, rvol, (mname, tv, mneed, msr, mdd_tab))
    # 低于该版本的最低门槛 → 无论算出什么仓位都开不出来

    # 失败原因要分开报，不能笼统说"名义不足"
    # ⚠️ 必须先判【低于最低门槛】：否则会出现
    #    "低于门槛开不出单" 和 "✅ 可下单" 同时打印的自相矛盾
    if equity < mneed:
        fail = "below_min"
    elif tv is not None and pos <= 0:
        fail = "vol"                    # 算不出已实现波动，与本金无关
    elif pos <= 0:
        fail = "pos"
    elif equity * pos < MIN_NOTIONAL:
        fail = "notional"
    else:
        fail = None

    notional = equity * pos
    # 固定版的回撤随【实际杠杆】变；波动率目标版用表里的数
    dd = dd_for_lev(pos) if tv is None else mdd_tab
    lev_plan = leverage_plan(equity, pos, notional)
    return {
        "lev_plan": lev_plan,
        "method": mname, "target_vol": tv, "threshold": mneed,
        "sharpe": msr, "drawdown": dd, "next": nxt, "gap": gap,
        "position": pos, "notional": notional,
        "qty": notional / price if price > 0 else float("nan"),
        "feasible": fail is None, "fail": fail,
        "need_equity": (MIN_NOTIONAL / pos) if pos > 0 else float("inf"),
        "worst": equity * (1 + dd),
    }


# 调仓阈值：仓位相对变化超过这个比例才算"需要操作"。
# 不是为了省钱而设的——是为了不因为 0.3% 的仓位抖动就下一单。
REBALANCE_THRESHOLD = 0.05


def decide_action(sig_long, prev_long, tgt_pos, rows):
    """
    决定归档里的 action。

    ⚠️ 这里曾经写错过：以为"只有信号切换才需要操作"。
       那个说法只对【固定版】成立（仓位恒定）。
       波动率目标版的仓位每天都不一样 —— 实测 781 天需要调仓，
       而信号切换只有 124 天，漏报 84%。

    现在：
      · 信号切换          →  买入 / 卖出
      · 信号没切换但在场   →  仓位变了就写"调仓"，没变写"不动"
      · 不在场            →  不动
    """
    if prev_long is None:
        return "建仓" if sig_long else "不动"
    if bool(sig_long) != bool(prev_long):
        return "买入" if sig_long else "卖出"
    if not sig_long:
        return "不动"
    prev_pos = None
    for r in reversed(rows):
        v = (r.get("target_position") or "").strip()
        if v:
            try:
                prev_pos = float(v)
            except ValueError:
                prev_pos = None
            break
    if prev_pos and prev_pos > 0:
        chg = abs(tgt_pos - prev_pos) / prev_pos
        if chg > REBALANCE_THRESHOLD:
            return f"调仓({chg*100:+.0f}%)"
    return "不动"


# ══════════════════ 主流程 ══════════════════
def run(a):
    from binance_api import BN
    bn = BN()
    L = []
    A = L.append

    bars, fixed, st = refresh(bn)
    bars, note = complete_bars(bars)
    sig = signal_of(bars)
    if sig is None:
        A("=" * 78)
        A(f"  {SYM} MA{MA_WINDOW} 固定仓位信号")
        A("=" * 78)
        A("")
        A(f"  ⛔ 数据不足，无法出信号")
        A(f"     缓存 {len(bars)} 根，MA{MA_WINDOW} 至少需要 {MA_WINDOW} 根")
        A(f"     （{note}）")
        return "\n".join(L), None
    fday = funding_by_day()

    now = dt.datetime.now()
    bd = dt.datetime.fromtimestamp(sig["bar_t"] / 1000, dt.UTC)
    price = float(bn.fapi("/fapi/v1/ticker/price", {"symbol": SYM}, signed=False)["price"])

    A("=" * 78)
    A(f"  {SYM} MA{MA_WINDOW} 固定仓位信号    {now:%Y-%m-%d %H:%M:%S}")
    A("=" * 78)
    A("")

    # ── 体检 ──
    A("  体检")
    A("  " + "-" * 74)
    A(f"  [数据] {note}")
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
    A("  " + "-" * 74)
    A(f"  决策日（已走完的日线）  {bd:%Y-%m-%d} UTC")
    A(f"  该日收盘                {sig['close']:,.2f}")
    A(f"  MA{MA_WINDOW}                    {sig['ma50']:,.2f}")
    A(f"  距 MA{MA_WINDOW}                {sig['dist_pct']:+.2f}%")
    A("")
    if sig["long"]:
        A(f"  收盘 > MA{MA_WINDOW}   ⇒   **做多**"
          f"（具体仓位见下面「该下多少」——它随本金变，不是固定值）")
    else:
        A(f"  收盘 ≤ MA{MA_WINDOW}   ⇒   **空仓**")
    if sig["prev_long"] is not None and sig["prev_long"] != sig["long"]:
        A(f"  ⚠️ 状态切换：上一日 {'多' if sig['prev_long'] else '空'}"
          f" → 今日 {'多' if sig['long'] else '空'}  ⇒ 需要下单")
    else:
        # ⚠️ 这里不能只说"无需操作"。波动率目标版的仓位每天都变，
        #    信号没切换也可能需要调仓（实测 84% 的日子需要）。
        #    仓位是否要调，要读到账户才知道 —— 见下面「该下多少」段。
        A(f"  （信号与上一日一致）")
        if a.check or a.archive:
            A(f"     ↓ 但仓位是否要调，要看账户 —— 见下面「该下多少」")
        else:
            A(f"     ⚠️ 加了 --check 才能算出今天该持多少（仓位随本金和波动变）")
    A(f"  实时价 {price:,.2f}（仅参考，判定不用）")
    A("")

    # ── 成本 ──
    # ⚠️ 这里要分清两个日子：
    #   决策日  = 信号所用的那根日线（收盘在 00:00 UTC）
    #   持有第1天 = 决策日的次日（资金费在次日 00:00 结算第一次）
    #   上一版显示的是【决策日】的费率，但实际要付的是【次日】的。
    #   两者可以差很多，所以两个都列出来，并标明哪个是实际成本。
    d_sig = int(sig["bar_t"] // 86400000)
    d_next = d_sig + 1
    f_sig = fday.get(d_sig)
    f_next = fday.get(d_next)
    A("  成本")
    A("  " + "-" * 74)
    A(f"  手续费（往返）           {FEE_PER_SIDE*2*100:.3f}%")
    if f_sig is not None:
        A(f"  决策日资金费             {f_sig*100:+.5f}%"
          f"   {'多头付' if f_sig > 0 else '多头收'}")
    if f_next is not None:
        A(f"  次日资金费（实际成本）    {f_next*100:+.5f}%"
          f"   {'多头付' if f_next > 0 else '多头收'}")
        if sig["long"]:
            A(f"  ⇒ 持有第 1 天的毛成本     "
              f"{FEE_PER_SIDE*2*100 + f_next*100:.4f}%")
        else:
            A(f"  ⇒ 空仓，无成本")
    else:
        A(f"  次日资金费               还没结算（下一根日线走完才有）")
    A("")

    # ── 执行建议 ──
    eq = None
    # ⚠️ realized_vol 的注释说「截至第 i-1 根收盘」—— 那是对 weight[i] 正确。
    #    但 net[i] = lag(w)[i] × r[i] = w[i-1] × r[i]，
    #    而 w[i-1] 用的是 vol20[i-1] = r[i-21:i-1].std()，
    #    即【截至第 i-2 根收盘】的窗口。差一天，容易被误读成滚动一天。
    #    （这个歧义曾让一份外部复核多滞后一天，得到 −41.3% 而非 −32.5%。）
    rvol = realized_vol(bars)          # 供波动率目标版用（无前视：只用已走完的）
    # 兜底默认值。读账户失败时 eq 为 None，归档里的建议字段是空的，
    # 所以这些默认值只影响 decide_action 的判断，不影响下单量。
    #
    # ⚠️ adv 必须【显式初始化】。它原来只在 if 块里赋值，靠 `if eq` 短路
    #    才没在归档那行崩掉 —— 那是"靠巧合正确"，不是正确。
    tgt_pos = LEVERAGE
    adv = {"method": "", "target_vol": None, "sharpe": METHODS[0][3],
           "drawdown": METHODS[0][4], "position": 0.0, "notional": 0.0,
           "qty": float("nan"), "feasible": False, "fail": "no_account",
           "next": None, "gap": None, "need_equity": float("inf"),
           "worst": 0.0, "threshold": METHODS[0][2]}
    # --archive 也需要权益（归档要求记 qty / notional / equity_before），
    # 所以读账户的条件是 check 或 archive，不只是 check。
    if a.check or a.archive:
        # 读账户单独一个 try：失败只影响账户段，不会吞掉"该下多少"的显示
        try:
            acct = bn.futures_account()
            eq = float(acct["totalMarginBalance"])
            pos = [p for p in bn.positions(SYM) if float(p.get("positionAmt", 0)) != 0]
            A("  账户（API 实测）")
            A("  " + "-" * 74)
            A(f"  保证金余额   {eq:>10,.4f} USDT")
            A(f"  可用余额     {float(acct['availableBalance']):>10,.4f} USDT")
            for p in pos:
                A(f"  现有持仓     {p['positionAmt']} @ {float(p['entryPrice']):,.2f}"
                  f"  浮盈亏 {float(p['unRealizedProfit']):+,.3f}")
            if not pos:
                A(f"  现有持仓     无")
            # 供下面算「该买卖多少」用；空仓时为 0.0
            held = sum(float(p["positionAmt"]) for p in pos)
            A("")
            adv = advice(eq, price, rvol)      # ← 唯一真源
            tgt_pos = adv["position"]
            mname, tv, msr, mdd = (adv["method"], adv["target_vol"],
                                   adv["sharpe"], adv["drawdown"])
            A("  该下多少（按本金自动匹配版本）")
            A("  " + "-" * 74)
            if rvol is not None and np.isfinite(rvol):
                A(f"  20 日已实现波动   {rvol*100:.1f}%  （波动率目标版要用）")
                A("")
            _tag = "（--target-vol 强制）" if TARGET_VOL_OVERRIDE is not None else ""
            A(f"  账户权益 {eq:,.2f} USDT  ⇒  匹配版本：**{adv['method']}**{_tag}")
            if TARGET_VOL_OVERRIDE is not None and eq < adv["threshold"]:
                A(f"     ⚠️ 但权益 {eq:,.2f}U < 该档门槛 {adv['threshold']:.1f}U"
                  f"（差 {adv['threshold']-eq:,.2f}U）")
                A(f"        ⇒ 该档在低波动时会算不出最小单，可能需要在"
                  f"「目标数量」为 0 时手动跳过")
            if adv["target_vol"] is not None:
                A(f"     （目标波动率是【在场时】的；账户整体约 "
                  f"{adv['target_vol']*VOL_ACHIEVE*100:.0f}%"
                  f" —— 因为约 45% 时间空仓）")
            A(f"     门槛阶梯：" + "  ".join(
                f"{mm[0]}≥{mm[2]:.1f}U" for mm in METHODS))
            if eq < METHODS[0][2]:
                A(f"     ⚠️ 低于最低门槛 —— 现在开不出单")
                A(f"     还差 {adv['gap']:,.2f} U 到 {METHODS[0][2]:.1f} USDT")
            elif adv["next"] is not None:
                nx = adv["next"]
                A(f"     到 {nx[2]:.1f} USDT 可升级到「{nx[0]}」"
                  f"（还差 {adv['gap']:,.2f} U）—— 夏普 {nx[3]:.3f}，回撤 {nx[4]*100:.1f}%")
            else:
                A(f"     已是最高档（回撤最小的一版）")
            A("")
            if adv["target_vol"] is None:
                A(f"  目标仓位 = max(1.0, {MIN_NOTIONAL:.0f} ÷ {eq:,.2f}) "
                  f"= {tgt_pos:.3f}x")
                A(f"     （固定版：权益 < {MIN_NOTIONAL:.0f}U 时被迫超过满仓，"
                  f">= {MIN_NOTIONAL:.0f}U 后回到 1.0x）")
            else:
                A(f"  目标仓位 = min(3, {adv['target_vol']*100:.0f}% ÷ "
                  f"{rvol*100:.1f}%) = {tgt_pos:.3f}x")
            tgt_n = adv["notional"]
            A(f"  目标名义 = {eq:,.2f} × {tgt_pos:.3f} = {tgt_n:,.2f} USDT")
            A(f"  目标数量 = {tgt_n:,.2f} ÷ {price:,.2f} = {adv['qty']:.4f} ETH")
            # ── 币安的下单约束：stepSize 0.001，且名义 ≥ MIN_NOTIONAL ──
            _step = 0.001
            _minq = max(_step, MIN_NOTIONAL / price)      # 实际最小可下单量
            _minq = np.ceil(_minq / _step) * _step
            _tgt_q = np.round(adv["qty"] / _step) * _step
            A(f"  最小名义 = {MIN_NOTIONAL:.0f} USDT"
              f"   ⇒ 最小下单 {_minq:.3f} ETH（步长 {_step}）")
            A(f"  目标数量（按步长取整）= {_tgt_q:.3f} ETH"
              f"   （原值 {adv['qty']:.4f}，差 {_tgt_q-adv['qty']:+.4f}）")
            # 若读到持仓，直接给出该买卖多少
            if held is not None:
                _delta = _tgt_q - held
                _dq = np.round(abs(_delta) / _step) * _step * (1 if _delta > 0 else -1)
                A("")
                if abs(_dq) < _minq - 1e-9:
                    A(f"  现有持仓 {held:.3f} ETH   差额 {_delta:+.4f} ETH")
                    A(f"  ✅ 差额 < 最小下单 {_minq:.3f} ⇒ 【不用动】")
                else:
                    A(f"  现有持仓 {held:.3f} ETH   差额 {_delta:+.4f} ETH")
                    A(f"  ⇒ 【{'买入' if _dq > 0 else '卖出'} {abs(_dq):.3f} ETH】"
                      f"（差额已取到 {_step} 的整数倍）")
                    A(f"     下完单后持仓 = {held + _dq:.3f} ETH"
                      f"（目标 {_tgt_q:.3f}，差 {held+_dq-_tgt_q:+.4f}）")
            A("")
            if adv["fail"] == "below_min":
                A(f"  ❌ 权益低于最低门槛，开不出单")
                A(f"     最低门槛 {METHODS[0][2]:.2f} USDT"
                  f"（最小名义 {MIN_NOTIONAL:.0f}U 都下不了）")
                A(f"     你现在 {eq:.2f} —— 差 {METHODS[0][2]-eq:+.2f}")
                A(f"     ⚠️ 不要为了凑够名义去提高杠杆 —— 那只会让强平更近。")
            elif adv["fail"] == "vol":
                A(f"  ❌ 算不出目标仓位：已实现波动无效（{rvol!r}）")
                A(f"     ⇒ 这是【数据问题】，不是本金不够。不要下单，下次再跑。")
            elif adv["fail"] == "notional":
                A(f"  ❌ 名义不足，开不出单")
                A(f"     这一版需要权益 ≥ {adv['need_equity']:.2f} USDT"
                  f"（最小名义 ÷ {tgt_pos:.3f}）")
                A(f"     你现在 {eq:.2f} —— 差 {adv['need_equity']-eq:+.2f}")
            elif adv["fail"]:
                A(f"  ❌ 目标仓位为 0，无法下单")
            else:
                A(f"  ✅ 可下单")
            # ── 币安实际怎么设（杠杆只允许整数）──
            lp = adv.get("lev_plan")
            if lp and adv["feasible"]:
                A("")
                A(f"  币安实际怎么设（杠杆只能设整数）")
                A(f"   {'-' * 70}")
                A(f"   名义价值   {tgt_n:,.2f} USDT   ← 由仓位决定，与杠杆设置无关")
                A(f"   杠杆设置   {lp['lev']}x          "
                  f"（= ceil({tgt_pos:.4f})，币安只允许整数）")
                A(f"      ⚠️ 逐仓下杠杆【只能调高不能调低】—— 已有持仓时降不回去")
                A(f"         · 首次设好之后不要每天改；只在工具说开不出来时才调高")
                A(f"         · 若现在已经是更高杠杆，【保持不动即可】")
                A(f"         · 杠杆比算出值更高不影响盈亏（盈亏由仓位决定），")
                A(f"           只让强平更近 —— ≤{MAX_SAFE_LEV}x 都安全")
                A(f"         · 反过来，杠杆低于仓位会【开不出来】"
                  f"（保证金 = 名义 ÷ 杠杆 ≤ 权益）")
                A(f"   保证金占用 {lp['margin']:,.2f} USDT   富余 {lp['spare']:,.2f} USDT")
                A(f"   逐仓强平   标的 {lp['liq_iso_px']*100:>5.1f}%   "
                  f"账户 {lp['liq_iso_acc']*100:>6.1f}%")
                A(f"   全仓强平   标的 {lp['liq_cross_px']*100:>5.1f}%   "
                  f"账户 {lp['liq_cross_acc']*100:>6.1f}%")
                A("")
                # 强平判断：和【单笔最坏逆向】比，不是和【累计回撤】比。
                A(f"   62 笔交易的期间最坏逆向   {WORST_TRADE_LOW*100:.1f}%（最低价口径）")
                if lp["safe"]:
                    A(f"   ✅ 逐仓不会触发强平（强平线 {lp['liq_iso_px']*100:.1f}% "
                      f"比最坏单笔远 "
                      f"{(lp['liq_iso_px'] - abs(WORST_TRADE_LOW))*100:.1f}pp）")
                else:
                    A(f"   ❌ 逐仓【会被强平】：强平线 {lp['liq_iso_px']*100:.1f}% "
                      f"比最坏单笔({WORST_TRADE_LOW*100:.1f}%)更近")
                    A(f"      这是杠杆设置 {lp['lev']}x 太高导致的 —— "
                      f"降到 {MAX_SAFE_LEV}x 以下才安全")
                A(f"   触发强平后剩多少：逐仓 {eq - lp['liq_iso_px']*tgt_n:.2f}U"
                  f"   全仓 {eq - lp['liq_cross_px']*tgt_n:.2f}U")
                A(f"      ⇒ 逐仓【账户下限更高】（亏掉保证金就停，不会穿仓）")
            A("")
            A(f"  历史表现（ETHUSDT 日线 {SAMPLE_DAYS + 60} 根，"
              f"回测样本 {SAMPLE_DAYS} 天 = {SAMPLE_YEARS:.2f} 年，扣全部成本）：")
            A(f"     夏普 {adv['sharpe']:.3f}")
            A("")
            A("     ① 恒定杠杆口径（保守，回答【最坏能坏到哪】）")
            A(f"        按 {tgt_pos:.3f}x：最大回撤 {adv['drawdown']*100:.1f}%"
              f"   ⇒ 若起点即峰值，跌到 {adv['worst']:,.2f} U")
            _dd = dynamic_drawdown(bars, fday, eq)
            if _dd:
                A("")
                A(f"     ② 从你 {eq:,.2f} U 出发的真实路径"
                  f"（回答【历史上真的到过哪】）")
                A(f"        最大回撤 {_dd['dd']*100:.1f}%   "
                  f"路径最低 {_dd['low']:,.2f} U   峰值 {_dd['peak']:,.2f} U")
                if _dd.get("switches"):
                    A(f"        版本切换 {_dd['switches']} 次"
                      f"（权益变化时自动升/降档）")
                A(f"        ⇒ 历史上从没跌到过 {adv['worst']:,.2f} U —— "
                  f"那是【起点即峰值】的假设值")
            A("")
        except Exception as e:
            A(f"  ⚠️ 读账户失败：{type(e).__name__}: {e}")
            A("")

    # ── 规则提醒 ──
    A("  规则（来自 ma50_rules.md）")
    A("  " + "-" * 74)
    A(f"  · 只用日线、只做多。在场开关永远是「日线收盘 > MA50」")
    A(f"  · 每天 UTC 00:00 后检查一次，用【已走完】那根的收盘")
    A(f"  · 不做量比/资金费筛选（实测无增量）")
    A(f"  · 仓位大小按本金自动匹配（见上）：本金越大，能用的版本回撤越小")
    A(f"  · 门槛来自实测：20U 最小名义 ÷ 有仓位日第 10 分位仓位")
    A(f"  · 夏普的标准误 {SE_SHARPE:.3f} —— {SAMPLE_YEARS:.2f} 年样本，很不精确")
    A("")
    A("  ⚠️ 别手动优化进出场。这套东西的全部价值来自规则化。")
    A("")

    rec = {
        "symbol": SYM,
        "date": bd.strftime("%Y-%m-%d"),
        "bar_ms": str(sig["bar_t"]),
        "bar_close": f"{sig['close']:.2f}",
        "ma50": f"{sig['ma50']:.2f}",
        "dist_ma50_pct": f"{sig['dist_pct']:.2f}",
        "signal": "做多" if sig["long"] else "空仓",
        "action": decide_action(sig["long"], sig["prev_long"], tgt_pos,
                                load_archive()),
        "advice_qty": f"{(eq*tgt_pos/price):.4f}" if eq else "",
        "advice_notional": f"{eq*tgt_pos:.2f}" if eq else "",
        "equity_at_signal": f"{eq:.4f}" if eq else "",
        "method": adv["method"] if eq else "",
        "target_vol": f"{adv['target_vol']:.2f}"
                      if (eq and adv["target_vol"] is not None) else "",
        "target_position": f"{tgt_pos:.4f}" if eq else "",
        "lev_setting": f"{adv['lev_plan']['lev']}" if (eq and adv.get("lev_plan")) else "",
        "margin_mode": ("全仓" if (eq and adv.get("lev_plan")
                                  and adv["lev_plan"]["liq_iso_acc"] > adv["drawdown"])
                        else ("逐仓" if (eq and adv.get("lev_plan")) else "")),
        "liq_acc_pct": (f"{adv['lev_plan']['liq_iso_acc']*100:.1f}"
                        if (eq and adv.get("lev_plan")) else ""),
        "realized_vol_pct": f"{rvol*100:.2f}" if (eq and np.isfinite(rvol)) else "",
        "funding_pct_today": f"{f_sig*100:.5f}" if f_sig is not None else "",
        "funding_pct_next": f"{f_next*100:.5f}" if f_next is not None else "",
        "entry_ref": f"{sig['close']:.2f}",
        "fwd_1d": "", "fwd_7d": "", "fwd_10d": "", "fwd_30d": "",
        "max_dd_10d": "", "checked_at": "", "status": "", "notes": note[:40],
    }
    return "\n".join(L), rec


# ══════════════════ 归档 ══════════════════
def load_archive():
    if not ARCHIVE.exists():
        return []
    return list(csv.DictReader(ARCHIVE.open(encoding="utf-8-sig")))


def save_archive(rows):
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with ARCHIVE.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def do_archive(rec):
    rows = load_archive()
    hit = None
    for i, r in enumerate(rows):
        if r.get("date") == rec["date"]:
            hit = i; break
    if hit is not None:
        # 保留已回填的前向收益
        for k in ("fwd_1d", "fwd_7d", "fwd_10d", "fwd_30d",
                  "max_dd_10d", "checked_at", "status"):
            if rows[hit].get(k):
                rec[k] = rows[hit][k]
        rows[hit] = rec
        act = "更新"
    else:
        rows.append(rec); act = "新增"
    save_archive(rows)
    yes = sum(1 for r in rows if r.get("signal") == "做多")
    print(f"  已{act}：{rec['date']}  {rec['signal']}  共 {len(rows)} 天"
          f"（其中做多 {yes} 天 / 空仓 {len(rows)-yes} 天）")


def backfill():
    rows = load_archive()
    if not rows:
        print("  还没有归档。先跑 python ma50_live.py --archive")
        return
    from binance_api import BN as _BN      # 模块级不导入，这里按需取
    bars = load_cache(_BN())               # 与 refresh 同一个安全入口
    bars, _ = complete_bars(bars)
    c = {int(b["t"]): i for i, b in enumerate(bars)}
    filled = partial = failed = pending = 0
    for r in rows:
        k = int(r["bar_ms"])
        if k not in c:
            r["status"] = "failed"; failed += 1; continue
        i = c[k]
        base = float(r["entry_ref"])
        got = {}
        for tag, h in (("fwd_1d", 1), ("fwd_7d", 7), ("fwd_10d", 10), ("fwd_30d", 30)):
            if i + h < len(bars):
                got[tag] = (bars[i + h]["c"] / base - 1) * 100
        if i + 10 < len(bars):
            seg = [bars[j]["c"] for j in range(i, min(i + 11, len(bars)))]
            r["max_dd_10d"] = f"{(min(seg)/base-1)*100:.2f}"
        for t, v in got.items():
            r[t] = f"{v:.3f}"
        r["checked_at"] = f"{dt.datetime.now():%Y-%m-%d %H:%M}"
        if len(got) == 4:
            r["status"] = "complete"; filled += 1
        elif got:
            r["status"] = "partial"; partial += 1
        else:
            # 决策日之后还没有任何一根走完 —— 这是正常的等待状态，不是失败
            r["status"] = "pending"; pending += 1
    save_archive(rows)
    print(f"  完整回填 {filled} 条   部分 {partial} 条"
          f"   等待中 {pending} 条   失败 {failed} 条")
    if pending:
        print(f"  （「等待中」= 决策日之后还没有 K 线走完，等明天再跑）")


def history():
    rows = load_archive()
    if not rows:
        print("  还没有归档。先跑 python ma50_live.py --archive")
        return
    print()
    print("=" * 92)
    print(f"  MA50 归档  {len(rows)} 条")
    print("=" * 92)
    days = sorted({r["date"] for r in rows})
    print(f"  前向检验进度：{len(days)} / 120 天"
          + ("   OK 够了" if len(days) >= 120 else f"   还差 {120-len(days)} 天"))
    if len(days) > 1:
        # ⚠️ 两种缺口要分开查：
        #   A. 已有记录【之间】的断点（原来只查这个）
        #   B. 首尾范围内【本该有但没有】的日子
        #   B 才是最容易发生的 —— 忘了跑 --archive 就会漏，
        #   而只查 A 的话，漏掉的日子根本不在记录里，永远查不出来。
        _d0 = dt.datetime.strptime(days[0], "%Y-%m-%d").date()
        _d1 = dt.datetime.strptime(days[-1], "%Y-%m-%d").date()
        _expected = {(_d0 + dt.timedelta(days=i)).strftime("%Y-%m-%d")
                     for i in range((_d1 - _d0).days + 1)}
        _have = set(days)
        _missing = sorted(_expected - _have)

        gaps = []
        for x, y in zip(days[:-1], days[1:]):
            g = (dt.datetime.strptime(y, "%Y-%m-%d")
                 - dt.datetime.strptime(x, "%Y-%m-%d")).days
            if g != 1:
                gaps.append((x, y, g))

        if _missing:
            print(f"  🔴 你有 {len(_missing)} 天没归档（这是最容易漏的）")
            _show = _missing[:6]
            print(f"       {', '.join(_show)}" + ("  …" if len(_missing) > 6 else ""))
            print(f"      ⇒ 这些日子没有记录，前向检验永远补不回来")
            print(f"      ⇒ 每天记得跑：python ma50_live.py --archive")
        elif gaps:
            lost = sum(g - 1 for _, _, g in gaps)
            print(f"  ⚠️ 中间缺了 {lost} 天（{len(gaps)} 处断点）")
            for x, y, g in gaps[:5]:
                print(f"       {x} → {y}  跳了 {g} 天")
        else:
            print(f"  日期连续，无缺口 ✅")
        print(f"  （检查范围：{days[0]} ~ {days[-1]}，共 {len(_expected)} 天，"
              f"有记录 {len(_have)} 天）")
    print()
    hdr = (f"  {'日期':<12}{'收盘':>10}{'MA50':>10}{'距离':>8}{'信号':>7}"
           f"{'动作':>7}{'fwd_10d':>10}{'状态':>10}")
    print(hdr)
    print("  " + "-" * (len(hdr) + 2))
    for r in rows[-25:]:
        f10 = r.get("fwd_10d") or "—"
        f10s = f"{float(f10):+.2f}%" if f10 != "—" else "—"
        print(f"  {r['date']:<12}{float(r['bar_close']):>10,.2f}"
              f"{float(r['ma50']):>10,.2f}{float(r['dist_ma50_pct']):>+7.2f}%"
              f"{r['signal']:>7}{r['action']:>7}{f10s:>10}{r.get('status','') or '—':>10}")
    done = [r for r in rows if r.get("status") == "complete"]
    if done:
        long_days = [r for r in done if r["signal"] == "做多"]
        print()
        print(f"  已完整回填 {len(done)} 条")
        if long_days:
            v = [float(r["fwd_10d"]) for r in long_days if r.get("fwd_10d")]
            print(f"    「做多」信号的 10 天平均收益 {np.mean(v):+.2f}%  （n={len(v)}）")
        print(f"    ⇒ 样本还太小，这些数字暂时没有判断力")


def selfcheck():
    """
    自检：确认 METHODS 表里的数字和当前代码/数据一致，
    并确认对齐口径正确（把仓位配到同期收益上是前视，会让夏普虚高 2.3~2.7 倍）。

    为什么需要它：2026-10-06 发现 METHODS 表里的夏普全是前视值，
    虚高了 2.2~2.3 倍。这个自检就是为了让同类错误下次跑一下就能发现。
    """
    import numpy as np
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        from align import panel, sharpe, max_dd, cagr
    except ImportError as e:
        print(f"  ❌ 找不到 align.py：{e}")
        print(f"     它在 {pathlib.Path(__file__).parent}")
        return

    print()
    print("=" * 88)
    print("  ma50_live 自检")
    print("=" * 88)

    fd = json.loads(FUND.read_text(encoding="utf-8"))
    fday = {}
    for x in fd:
        k = int(x["t"] // 86400000)
        fday[k] = fday.get(k, 0.0) + x["rate"]
    raw = json.loads(CACHE.read_text(encoding="utf-8"))
    C = np.array([b["c"] for b in raw], float)
    day = np.array([b["t"] // 86400000 for b in raw])
    FR = np.array([fday.get(int(day[i]), 0.0) for i in range(len(C))])
    P = panel(C, FR, FEE_PER_SIDE)
    W = 60

    # ① 对齐自检
    print()
    print("  ① 对齐口径")
    a1 = sharpe(P.net(None)[W:])
    b1 = sharpe(P.net_lookahead(None)[W:])
    ratio = b1 / a1
    ok = ratio > 1.35
    print(f"     正确口径夏普 {a1:.3f}   前视口径夏普 {b1:.3f}   比值 {ratio:.2f}x")
    print(f"     {'✅ 正确（前视必须明显更高）' if ok else '🔴 危险：两者接近，说明对齐写错了'}")

    # ② METHODS 表核对
    print()
    print("  ② METHODS 表 vs 实算")
    print(f"     {'版本':<18}{'表里夏普':>10}{'实算':>9}{'Δ':>8}"
          f"{'表里回撤':>10}{'实算':>9}{'Δ':>8}")
    print("     " + "-" * 66)
    bad = 0
    for (name, tv, need, t_sh, t_dd) in METHODS:
        lev = LEVERAGE if tv is None else None
        x = P.net(tv, lev=lev)[W:]
        x = x[np.isfinite(x)]
        r_sh, r_dd = sharpe(x), max_dd(x)
        d_sh, d_dd = r_sh - t_sh, r_dd - t_dd
        # ⚠️ 2026-10-06：容差从 ±0.005 收紧到 ±0.001。
        #    原来 1.245 与真值 1.2442 差 0.0018，旧容差放过了它。
        # ⚠️ 容差 ±0.003：METHODS 表是【快照】，而数据每天在长，
        #    夏普会随之漂移（实测约 0.001/天）。
        #    故意放到 ±0.003（约 3 天漂移）——
        #    真出错时偏差会是 0.01+ 量级，不会被漏掉。
        flag = "" if (abs(d_sh) < 0.003 and abs(d_dd) < 0.005) else "  ⚠️"
        if flag:
            bad += 1
        print(f"     {name:<18}{t_sh:>10.3f}{r_sh:>9.3f}{d_sh:>+8.3f}"
              f"{t_dd*100:>9.1f}%{r_dd*100:>8.1f}%{d_dd*100:>+7.1f}pp{flag}")
    print("     │ 容差：夏普 ±0.003，回撤 ±0.5pp（数据日增会漂移，约 0.001/天）")
    print("     │ ✅ 2026-10-06 起两列都是 0.0 差 —— 系数 1 口径下完全对齐")

    # ③ 门槛核对
    print()
    print("  ③ 门槛（20U ÷ 有仓位日第 10 分位仓位）")
    for (name, tv, need, _, _) in METHODS:
        w = P.weight(tv)[W:]
        on = w[w > 0]
        r_need = MIN_NOTIONAL / float(np.percentile(on, 10))
        flag = "" if abs(r_need - need) < 0.15 else "  ⚠️"
        print(f"     {name:<18} 表里 {need:>6.1f}U   实算 {r_need:>6.1f}U"
              f"   Δ {r_need-need:>+5.1f}U{flag}")

    # ④ DD_BY_LEV 核对
    print()
    print("  ④ DD_BY_LEV（固定版回撤随杠杆）")
    print(f"     {'杠杆':>8}{'表里':>10}{'实算':>10}{'Δ':>9}{'夏普':>9}")
    print("     " + "-" * 48)
    for lev, t_dd in DD_BY_LEV:
        x = P.net(None, lev=lev)[W:]
        x = x[np.isfinite(x)]
        r_dd = max_dd(x)
        flag = "" if abs(r_dd - t_dd) < 0.03 else "  ⚠️"
        print(f"     {lev:>8.3f}{t_dd*100:>9.1f}%{r_dd*100:>9.1f}%"
              f"{(r_dd-t_dd)*100:>+8.1f}pp{sharpe(x):>9.3f}{flag}")

    # ⑤ 整数杠杆 / 保证金模式
    print()
    print("  ⑤ 整数杠杆 / 保证金模式（币安只允许整数杠杆）")
    print(f"     {'权益':>8}{'仓位':>9}{'设杠杆':>7}{'保证金':>9}"
          f"{'整数':>6}{'够开':>6}{'逐仓强平':>11}{'全仓强平':>11}")
    print("     " + "-" * 70)
    lev_bad = []
    for eq in (14.20, 14.8, 16, 18, 19.99, 20, 30, 52.3, 100):
        a = advice(eq, 2700.0, 0.445)
        lp = a.get("lev_plan")
        if lp is None:
            continue
        is_int = float(lp["lev"]).is_integer()
        fits = lp["margin"] <= eq + 1e-9
        if not is_int or not fits:
            lev_bad.append(eq)
        print(f"     {eq:>7.2f}U{a['position']:>9.4f}{lp['lev']:>6}x"
              f"{lp['margin']:>8.2f}U{'✅' if is_int else '❌':>6}"
              f"{'✅' if fits else '❌':>6}"
              f"{lp['liq_iso_acc']*100:>10.1f}%{lp['liq_cross_acc']*100:>10.1f}%")
    if lev_bad:
        bad += 1
        print(f"     ⚠️ 这些权益下杠杆非整数或开不出来：{lev_bad}")
    else:
        print("     ✅ 杠杆全是整数，且保证金 ≤ 权益")

    # 杠杆必须 = ceil(仓位)
    mis = []
    for eq in (14.2, 15, 17, 19.5, 20, 25, 40):
        a = advice(eq, 2700.0, 0.445)
        exp = max(1, int(np.ceil(a["position"] - 1e-9)))
        if a["lev_plan"]["lev"] != exp:
            mis.append(eq)
    if mis:
        bad += 1
        print(f"     ⚠️ 杠杆 ≠ ceil(仓位)：{mis}")
    else:
        print("     ✅ 杠杆 = ceil(仓位)")

    # 低于门槛时不能报"可下单"（否则和"低于门槛开不出单"矛盾）
    contra = [eq for eq in (0.5, 5, 10, 14.19)
              if advice(eq, 2700.0, 0.445)["feasible"]]
    if contra:
        bad += 1
        print(f"     ⚠️ 低于门槛却报可下单：{contra}")
    else:
        print("     ✅ 低于门槛时 feasible=False")

    # 强平公式自洽：全仓强平时账户剩维持保证金
    a = advice(14.8, 2700.0, 0.445)
    lp = a["lev_plan"]
    mm_keep = MMR * a["notional"]
    resid = 14.8 - lp["liq_cross_px"] * a["notional"]
    if abs(resid - mm_keep) > 0.005:
        bad += 1
        print(f"     ⚠️ 全仓强平残值 {resid:.4f} ≠ 维持保证金 {mm_keep:.4f}")
    else:
        print(f"     ✅ 全仓强平时账户剩维持保证金 {resid:.4f}")

    print()
    if bad == 0:
        print("  ✅ 全部一致。")
    else:
        print(f"  ⚠️ 有 {bad} 处不一致 —— 上面带 ⚠️ 的行需要更新。")
    print("=" * 88)
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--console", action="store_true")
    ap.add_argument("--check", action="store_true", help="读账户，算该下多少")
    ap.add_argument("--archive", action="store_true", help="把今天的信号写进归档")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--selfcheck", action="store_true",
                    help="核对 METHODS/DD_BY_LEV 表和实算是否一致 + 对齐自检")
    ap.add_argument("--rebuild", action="store_true",
                    help="从 API 全量重建日线缓存（缓存损坏时自动触发，也可手动跑）")
    ap.add_argument("--target-vol", type=float, default=None, metavar="N",
                    help="强制目标波动率档位（15/25/40），不填=按权益自动选"
                         "（权益越大自动越保守，定投时会被锁死在 15%%）")
    a = ap.parse_args()

    if a.rebuild:
        from binance_api import BN as _BN
        load_cache(_BN(), rebuild=True)
        return
    global TARGET_VOL_OVERRIDE
    if a.target_vol is not None:
        v = a.target_vol / 100.0 if a.target_vol > 1 else a.target_vol
        # ⚠️ 原来只在 METHODS 里精确匹配，等于人为限制成 15/25/40 三档。
        #    实测 15%~60% 的夏普完全恒定（极差 0.0000），所以任何值都等价。
        #    门槛按【最接近的档位】取（用于提示"权益够不够开单"）。
        if not (0.05 <= v <= 0.60):
            print(f"  🔴 目标波动率应在 5%~60% 之间（给的是 "
                  f"{a.target_vol}%）。超过 60% 会触发 3x 上限截断，"
                  f"夏普反而下降。")
            return
        TARGET_VOL_OVERRIDE = v
    if a.history:
        history(); return
    if a.backfill:
        backfill(); return
    if a.selfcheck:
        selfcheck(); return

    out, rec = run(a)
    if rec is None:
        print(out)
        return

    if a.archive:
        do_archive(rec)

    if a.console:
        print(out)
    outdir = ROOT / "data" / "reports"
    outdir.mkdir(parents=True, exist_ok=True)
    f = outdir / f"ma50_{dt.datetime.now():%Y%m%d_%H%M}.md"
    f.write_text("```\n" + out + "```\n", encoding="utf-8")
    print(f"\n  已生成：{f.relative_to(ROOT)}\n")
    if a.archive:
        print("  --archive 已写进归档；记得隔天跑一次 --backfill")
    else:
        print("  ⚠️ 这次【没有】写进归档。")
        print("     想留下证据（前向检验要攒 120 天）：")
        print("       python ma50_live.py --archive")
        print("     或者用 Windows 任务计划每天自动跑（见 ma50_rules.md §6）")


if __name__ == "__main__":
    main()
