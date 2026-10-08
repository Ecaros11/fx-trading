"""
对齐工具（align.py）—— 只此一处定义，避免每个脚本各写一遍再各错一遍
=================================================================
事故背景
--------
2026-10-06：发现所有"仓位 × 收益"的统计都含【前视偏差】。
错误写法：

    r = np.zeros(n)
    r[1:] = C[1:] / C[:-1] - 1      # 前面垫 0 补长度，方便切片
    net = w * r                     # 🔴 w[i] 配 r[i]

    w[i] = sig(收盘[i])             信号在 i 收盘才知道
    r[i] = C[i]/C[i-1] - 1          i-1 → i 的收益（i 收盘时已经走完）

    ⇒ 拿"今天收盘才知道的信号"去赚"今天已经涨完的行情"

后果（实测）：夏普虚高 2.3~2.7 倍、回撤减半、年化虚高到 526%（真值 65.5%）。

正确写法
--------
在第 i 根收盘算出 sig[i]，持有到第 i+1 根收盘：

    ret[i] = w[i-1] × (C[i]/C[i-1] - 1)

常见正确写法（无需垫 0）：

    r  = np.diff(C) / C[:-1]        # 长度 n-1，r[i] = C[i] → C[i+1]
    w_ = w[:-1]                     # 长度 n-1，w_[i] = sig(收盘[i])
    net = w_ * r                    # ✅ 对齐

用法
----
    from align import lag, panel

    p = panel(C, FR)                # 统一算好所有对齐的序列
    net = p.w_lag(tv=0.40) * p.r - ...
"""
import numpy as np

# ══════════════════════════════════════════════════════════════════════
#  ⚠️ 这两个常量必须与 ma50_live.py 完全一致
#     否则 --selfcheck 会拿"20 日窗口的策略"去对比"10 日窗口的表格"，
#     报出一堆假警报，而真正的口径不一致反而检测不到。
#
#  2026-10-07 修：这两个常量以前是硬编码的 20 / 无上限，
#     工具改成 10 日 + VOL_CAP 后 align.py 没跟着改 —— 结构性缺陷。
#     现在改成从 ma50_live.py 直接读取，杜绝再次漂移。
# ══════════════════════════════════════════════════════════════════════
def _read_tool_constants():
    import importlib.util
    import pathlib
    p = pathlib.Path(__file__).with_name("ma50_live.py")
    spec = importlib.util.spec_from_file_location("_ml_const", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # ⚠️ 2026-10-07：#3 仓位上限也要从工具读 ——
    #    顶格时逐仓开不出来（w/ceil(w)+w·FEE>1），工具已改成
    #    MAX_POS = n/(1+n·FEE)；回测必须同步，否则又是"工具 vs 回测"脱节。
    # ⚠️ 2026-10-08：feasible_pos 也要读进来 ——
    #    回测的权重同样可能落在坏区间 (n/(1+n·FEE), n]
    #    （实测 2022-03-21：w=0.9998949 ⇒ ceil=1 ⇒ lhs=1.0003949 ⇒ 开不出来）
    return mod.VOL_WINDOW, mod.VOL_CAP, mod.MAX_POS, mod.feasible_pos


VOL_WIN, VOL_CAP, MAX_POS, FEASIBLE_POS = _read_tool_constants()      # 10, 1.20

PY = 365.0


def lag(w):
    """把仓位序列滞后一天：lag(w)[i] = w[i-1]（首项补 0）"""
    w = np.asarray(w, float)
    return np.concatenate([[0.0], w[:-1]])


# ──────────────────────────────────────────────────────────────────
# ⚠️ 手续费系数 = 1，不是 2。不要再改回去。
# ──────────────────────────────────────────────────────────────────
# 2026-10-06：这里原来写的是 `turn * self.fee * 2`，多算了一倍手续费。
# 证据（显式现金流，权益 100U，一个完整往返 0 → 1.405 → 0）：
#
#     t1 买入  w=1.405  成交额 140.500  手续费 0.0703
#     t3 卖出  w=0.000  成交额 140.500  手续费 0.0703
#     实际合计 = 0.1405 U
#
#     |Δw| 之和 = |1.405−0| + |0−1.405| = 2.810
#     系数 1：2.810 × 100 × 0.0005     = 0.1405  ✅ 与现金流一致
#     系数 2：2.810 × 100 × 0.0005 × 2 = 0.2810  ❌ 重复计算
#
# ⇒ |Δw| 的【和】已经等于"买入成交额 + 卖出成交额"之和（以权益为单位）。
#   再乘 2 就是把手续费算两遍。
#
# 交叉验证：文档 §3.1 写"换手 18.5 次/年 × 1.405 × 0.0005 = 1.30%/年"，
#          那个 1.30% 是系数 1 的结果；系数 2 会得到 2.60%，与文档矛盾。
# ──────────────────────────────────────────────────────────────────


def daily_ret(C):
    """
    日收益，与 lag(sig) 对齐。
    返回长度 n 的数组，ret[i] = C[i]/C[i-1] - 1（ret[0] = 0）。
    ⚠️ 只能配 lag(仓位) 使用。直接配未滞后的仓位就是前视。
    """
    C = np.asarray(C, float)
    r = np.zeros(len(C))
    r[1:] = C[1:] / C[:-1] - 1
    return r


def sma(x, k):
    x = np.asarray(x, float)
    o = np.full(len(x), np.nan)
    if len(x) >= k:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        o[k - 1:] = (cs[k:] - cs[:-k]) / k
    return o


def realized_vol(r, i, win=None):
    """
    截至第 i-1 根收盘的年化已实现波动（不含当期收益 ⇒ 无前视）。
    r 必须是 daily_ret(C) 的结果。

    win=None ⇒ 用 VOL_WIN（从 ma50_live.py 读，V2 = 10 日）
    """
    if win is None:
        win = VOL_WIN
    if i < win + 1:
        return np.nan
    w = r[i - win:i]
    if len(w) < 2:
        return np.nan
    return float(w.std(ddof=1) * np.sqrt(PY))


def sharpe(x, ann=PY):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 30 or x.std() == 0:
        return np.nan
    return x.mean() / x.std() * np.sqrt(ann)


def max_dd(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    eq = np.cumprod(1 + x)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def cagr(x, ann=PY):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    eq = np.cumprod(1 + x)
    if eq[-1] <= 0:
        return -1.0
    return float(eq[-1] ** (ann / len(x)) - 1)


class panel:
    """
    一次算好所有对齐序列，之后所有脚本都用它，不再手写对齐。

    C   收盘价数组
    FR  每日资金费（多头付为正），长度与 C 相同
    fee 单边手续费
    """

    def __init__(self, C, FR, fee=0.0005):
        self.C = np.asarray(C, float)
        self.FR = np.asarray(FR, float)
        self.fee = fee
        self.n = len(self.C)
        self.r = daily_ret(self.C)                 # 配 lag(w) 使用
        self.ma50 = sma(self.C, 50)
        self.sig = np.nan_to_num((self.C > self.ma50).astype(float))
        # 已实现波动（滞后，无前视）
        # ⚠️ 窗口和上限都从 ma50_live.py 读 —— 不要在这里写死
        self.vol = np.full(self.n, np.nan)
        for i in range(VOL_WIN + 1, self.n):
            self.vol[i] = realized_vol(self.r, i, VOL_WIN)

    # ⚠️ cap 的默认值用工具里的 MAX_POS（≈2.995507），不是硬编码 3.0 ——
    #    顶格 w=3.0 时逐仓保证金 = 100% 权益 + 手续费 > 权益 ⇒ 开不出来。
    def weight(self, tv=None, lev=1.405, cap=None, vol_cap="auto",
               vol_lag=0, sig_lag=1):
        """
        目标仓位（占权益倍数）。

        ⚠️ 两个对齐参数（2026-10-07 #4 + 回归修复）—— 分别控制两个量：

          sig_lag=1（默认，正确）：趋势信号用【上一根收盘】判定的
             sig_lag=0（前视，只用于对照）：用【当根收盘】判定
             ⇒ 前者实盘可得，后者用到当天收盘价

          vol_lag=0（默认，匹配实盘）：波动率用 std(r[i-win:i])
             vol_lag=1（旧回测口径）：用 std(r[i-win-1:i-1])，早一天

        正确组合 = sig_lag=1 + vol_lag=0   ← 实盘会得到的
        前视组合 = sig_lag=0 + vol_lag=0   ← net_lookahead() 用

        tv=None → 固定 lev；vol_cap='auto' → 用工具里的 VOL_CAP。
        """
        s_ = lag(self.sig) if sig_lag == 1 else self.sig
        if tv is None:
            w_fix = s_ * lev
            w_fix = np.nan_to_num(lag(w_fix) if vol_lag != 0 else w_fix)
            return np.array([FEASIBLE_POS(x) if x > 0 else 0.0 for x in w_fix])
        if cap is None:
            cap = MAX_POS
        vc = VOL_CAP if vol_cap == "auto" else vol_cap
        if vol_lag == 0:
            vol = self.vol
            s_v = s_
        else:
            # 旧口径：vol 与 sig 一起退一天（由 net() 补 lag）
            vol = self.vol
            s_v = self.sig if sig_lag == 1 else self.sig
        raw = np.where(np.isfinite(vol) & (vol > 1e-9),
                       tv / np.where(vol > 1e-9, vol, 1.0), 0.0)
        if vc is not None:
            raw = np.where(vol > vc, 0.0, raw)      # 波动率过高 ⇒ 空仓
        _w = np.nan_to_num(s_v * np.clip(raw, 0, cap))
        # ⚠️ 逐元素过 feasible_pos，保证逐仓能真正开出来
        #    （与工具的 target_position 保持一致）
        return np.array([FEASIBLE_POS(x) if x > 0 else 0.0 for x in _w])

    def net(self, tv=None, lev=1.405, warmup=0, vol_cap="auto", vol_lag=0,
            sig_lag=1):
        """
        返回对齐正确的净收益序列（长度 n，前 warmup 项为 0）。
        调用方自己切 [warmup:]。

        ⚠️ vol_lag=0（默认）⇒ weight() 已内含 sig 滞后，这里【不再 lag】。
           vol_lag=1 ⇒ 旧口径，weight() 不含滞后，这里补一次 lag。
        """
        w = self.weight(tv, lev, cap=None, vol_cap=vol_cap, vol_lag=vol_lag,
                        sig_lag=sig_lag)
        if vol_lag != 0:
            w = lag(w)                       # 旧口径才需要补滞后
        turn = np.abs(np.diff(np.concatenate([[0.0], w])))
        fr = lag(self.FR)
        net = w * self.r - turn * self.fee - w * fr
        if warmup:
            net[:warmup] = 0.0
        return net

    def net_lookahead(self, tv=None, lev=1.405):
        """
        ⚠️ 只用于对照演示/诊断，严禁用于任何结论。

        这就是那个让夏普虚高 2.3~2.7 倍的错误写法：
            w[i] = sig[i] × f(vol[i])      ← 用【当天收盘】决定当天仓位

        ⚠️ 2026-10-07 回归修复：原来这里调 self.weight(tv, lev)，
           而 weight 默认 sig_lag=1 已经滞后了 sig ⇒ 两者权重完全相同
           ⇒ 比值退化成 1.00x，检测器【失效】。
           现在显式用 sig_lag=0（不滞后）才会得到真正的前视值。
        """
        w = self.weight(tv, lev, vol_lag=0, sig_lag=0)
        turn = np.abs(np.diff(np.concatenate([[0.0], w])))
        return w * self.r - turn * self.fee - w * self.FR


def assert_no_lookahead(panel_obj, tv=None, lev=1.405, tol=0.35, warmup=60):
    """
    自检：正确口径的夏普必须显著低于前视口径。
    若两者接近，说明这个脚本又写错了对齐。
    返回 (正确夏普, 前视夏普)。
    """
    a = sharpe(panel_obj.net(tv, lev)[warmup:])
    b = sharpe(panel_obj.net_lookahead(tv, lev)[warmup:])
    if not (b > a * (1 + tol)):
        raise AssertionError(
            f"对齐检查失败：正确 {a:.3f} / 前视 {b:.3f} —— 比值只有 {b/a:.2f}x，"
            f"应该 > {1+tol:.2f}x。这个脚本可能又把仓位配到同期收益上了。")
    return a, b
