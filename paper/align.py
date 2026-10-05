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


def realized_vol(r, i, win=20):
    """
    截至第 i-1 根收盘的年化已实现波动（不含当期收益 ⇒ 无前视）。
    r 必须是 daily_ret(C) 的结果。
    """
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
        self.vol20 = np.full(self.n, np.nan)
        for i in range(21, self.n):
            self.vol20[i] = realized_vol(self.r, i, 20)

    def weight(self, tv=None, lev=1.405, cap=3.0):
        """目标仓位（占权益倍数）。tv=None → 固定 lev"""
        if tv is None:
            return np.nan_to_num(self.sig * lev)
        raw = np.where(np.isfinite(self.vol20) & (self.vol20 > 1e-9),
                       tv / np.where(self.vol20 > 1e-9, self.vol20, 1.0), 0.0)
        return np.nan_to_num(self.sig * np.clip(raw, 0, cap))

    def net(self, tv=None, lev=1.405, warmup=0):
        """
        返回对齐正确的净收益序列（长度 n，前 warmup 项为 0）。
        调用方自己切 [warmup:]。
        """
        w = self.weight(tv, lev)
        wl = lag(w)                                  # ✅ 关键：滞后一天
        turn = np.abs(np.diff(np.concatenate([[0.0], wl])))
        fr = lag(self.FR)
        net = wl * self.r - turn * self.fee - wl * fr
        if warmup:
            net[:warmup] = 0.0
        return net

    def net_lookahead(self, tv=None, lev=1.405):
        """
        ⚠️ 只用于对照演示/诊断，严禁用于任何结论。
        这就是那个让夏普虚高 2.3~2.7 倍的错误写法。
        """
        w = self.weight(tv, lev)
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
