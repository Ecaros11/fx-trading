"""
回测用的杠杆 vs 实盘会变的杠杆（dynamic_vs_const.py）
==================================================
回测：w = sig × 1.353        （常量）
实盘：w = sig × max(1.0, 20/权益)   （权益越小，仓位越大）

两者在权益变化时【不一样】。差多少？
"""
import collections
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
SYM = "ETHUSDT"
COST = 0.0005
MIN_NOTIONAL = 20.0

j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
C = np.array([b["c"] for b in j], float)
T = np.array([b["t"] for b in j], float)
n = len(C)
r = np.zeros(n)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(n, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
sig = np.nan_to_num((C > ma).astype(float))
fd = json.loads((ROOT / "data" / "funding" / f"{SYM}.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fd:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
dmap = {int(k): float(np.sum(v)) for k, v in agg.items()}
cd = np.array([int(t // 86400000) for t in T])
FR = np.nan_to_num(np.array([dmap.get(int(d), np.nan) for d in cd]))

START, LEV0 = 60, 1.353


def run(dynamic):
    """dynamic=True: 仓位随权益变；False: 恒定 LEV0"""
    eq = 14.80
    pr = np.zeros(n)
    w_prev = 0.0
    ws = []
    for i in range(1, n):
        # 昨日收盘决定今日仓位
        if sig[i - 1]:
            lev = max(1.0, MIN_NOTIONAL / eq) if dynamic else LEV0
            w = lev
        else:
            w = 0.0
        turn = abs(w - w_prev)
        pr[i] = w * r[i] - turn * COST - w * FR[i]
        eq *= (1 + pr[i])
        w_prev = w
        ws.append(w)
    return pr, np.array(ws), eq


print("=" * 84)
print(f"  {SYM}   回测杠杆（恒定 {LEV0}x） vs 实盘杠杆（随权益变）")
print("=" * 84)
print()


def stat(x, lab):
    x = x[START:]
    mu = x.mean() * 365
    vol = x.std(ddof=1) * np.sqrt(365)
    eqc = np.cumprod(1 + x)
    dd = (eqc / np.maximum.accumulate(eqc) - 1).min()
    print(f"  {lab:<26}{mu*vol/vol*0+mu*100:>9.1f}%{vol*100:>9.1f}%"
          f"{mu/vol:>8.3f}{dd*100:>10.1f}%")
    return dd


print(f"  {'口径':<26}{'年化':>9}{'波动':>9}{'夏普':>8}{'最大回撤':>10}")
print("  " + "-" * 62)
p_c, w_c, e_c = run(False)
p_d, w_d, e_d = run(True)
dd_c = stat(p_c, f"恒定 {LEV0}x（工具现在报的）")
dd_d = stat(p_d, "随权益变（实盘会这样）")

print()
print(f"  期末权益：恒定 {e_c:.2f}U   动态 {e_d:.2f}U")
print()
print(f"  {'':<26}{'仓位中位':>10}{'仓位最大':>10}{'仓位最小':>10}")
print("  " + "-" * 58)
for lab, w in ((f"恒定 {LEV0}x", w_c), ("随权益变", w_d)):
    on = w[w > 0]
    print(f"  {lab:<26}{np.median(on):>10.3f}{on.max():>10.3f}{on.min():>10.3f}")

print()
print("=" * 84)
print("  结论")
print("=" * 84)
print(f"""
  回撤：恒定 {dd_c*100:.1f}%  vs  动态 {dd_d*100:.1f}%
  ⇒ 差 {(dd_d-dd_c)*100:+.1f}pp

  为什么会有差：权益缩水时，max(1.0, 20/权益) 会自动【加杠杆】
  来维持 20 USDT 的名义 —— 这让下跌时亏得更快，
  所以动态口径的回撤比恒定口径【更深】。
""")
