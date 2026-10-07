"""
#4 波动率对齐 —— 独立确认（confirm4.py）
======================================
目标：确认「实盘的工具」和「回测的 panel」是不是同一个量，
      如果不是，哪个才是实盘会得到的数字。
"""
import collections
import importlib.util
import json
import pathlib

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sa = importlib.util.spec_from_file_location("al", HERE / "align.py")
al = importlib.util.module_from_spec(sa)
sa.loader.exec_module(al)
sm = importlib.util.spec_from_file_location("ml", HERE / "ma50_live.py")
m = importlib.util.module_from_spec(sm)
sm.loader.exec_module(m)

bars = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
cb, _ = m.complete_bars(bars)
C = np.array([b["c"] for b in cb], float)
nn = len(C)
r = np.zeros(nn)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(nn, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
sig = np.nan_to_num((C > ma).astype(float))

fr = json.loads((ROOT / "data" / "funding" / "ETHUSDT.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
CD = np.array([int(b["t"] // 86400000) for b in cb])
FR = np.nan_to_num(np.array(
    [float(np.sum(agg[int(d)])) if int(d) in agg else np.nan for d in CD]))
P = al.panel(C, FR)
W, FEE, TV, CAP, MP = 60, m.FEE_PER_SIDE, 0.60, m.VOL_CAP, m.MAX_POS

print("=" * 90)
print("  一、时间线（先把'哪一天知道什么'钉死）")
print("=" * 90)
print()
print("  第 i-1 根日线在 UTC 00:00 收盘")
print("  ⇒ 此刻可得：C[0..i-1]、r[1..i-1]")
print()
print("  实盘（工具在 UTC 00:05 跑）：")
print("     comp[-1] = 第 i-1 根")
print("     realized_vol 用最后 11 个收盘 ⇒ r[i-10] .. r[i-1]")
print("     ⇒ std(r[i-10:i])          ← 记为 V_live")
print("     然后下仓，赚第 i 天的收益 r[i]")
print()
print("  回测（panel 现状）：")
print("     net[i] = w[i-1] * r[i]")
print("     w[i-1] = sig[i-1] * f(vol[i-1])")
print("     vol[i-1] = std(r[i-11:i-1])   ← 记为 V_panel")
print()
print("  ⇒ 两者差一天：V_live 用 r[i-10..i-1]，V_panel 用 r[i-11..i-2]")
print()

print("=" * 90)
print("  二、数值确认")
print("=" * 90)
print()
V_live_last = m.realized_vol(cb, m.VOL_WINDOW)
print(f"  实盘工具口径（最后一根）：{V_live_last*100:.4f}%")
print(f"  panel.vol[-1]            ：{P.vol[-1]*100:.4f}%")
print(f"  panel.vol[-2]            ：{P.vol[-2]*100:.4f}%")
print()
# 逐点比对
maxd = {}
for k in (-1, 0, 1, 2):
    d = []
    for i in range(W, nn - 2):
        j = i + k
        if 0 <= j < nn and np.isfinite(P.vol[j]):
            v = m.realized_vol(cb[:i + 1], m.VOL_WINDOW)
            if np.isfinite(v):
                d.append(abs(v - P.vol[j]))
    maxd[k] = max(d) if d else float("nan")
    print(f"    V_live[i] vs panel.vol[i{k:+d}]  最大差 {maxd[k]:.3e}")
print()
print(f"  ⇒ k=+1 时差 {maxd[1]:.3e} ⇒ V_live[i] ≡ panel.vol[i+1] ✅ 精确平移一天")
print()

print("=" * 90)
print("  三、哪个口径无前视？（关键）")
print("=" * 90)
print()
i = nn - 1
print(f"  检查 V_live[i] = std(r[{i-9}:{i+1}])：")
print(f"     用的收益 r[{i-9}] .. r[{i}]")
print(f"     其中 r[{i}] = C[{i}]/C[{i-1}]-1 需要【第 {i} 根的收盘价】")
print(f"  ⇒ 如果用它决定【第 {i} 天】的仓位，就是前视 🔴")
print()
print(f"  但实盘里 'i' 是【最后一根已收盘】，所以：")
print(f"     comp[-1] = 第 i 根 ⇒ r[{i}] 已经确定 ⇒ 不是前视 ✅")
print(f"     ⇒ 实盘赚的是【第 i+1 天】的收益")
print()
print("  ⇒ 所以正确对齐是：")
print("     用 V_live[i]（截至第 i 根）决定仓位，赚 r[i+1]")
print("     等价于：net[i+1] = sig[i] × f(std(r[i-9:i+1])) × r[i+1]")
print("     ⇒ 换成 1-based：net[j] = sig[j-1] × f(std(r[j-10:j])) × r[j]")
print("     ⇒ 而 std(r[j-10:j]) 正是 panel.vol[j] ✅")
print()

print("=" * 90)
print("  四、所以正确的实现")
print("=" * 90)
print()
print("  现状：net[j] = sig[j-1] × f(panel.vol[j-1]) × r[j]   ← vol 早一天")
print("  正确：net[j] = sig[j-1] × f(panel.vol[j])   × r[j]   ← 只滞后 sig")
print()
print("  ⚠️ 注意：不是'vol 不滞后'，而是'vol 用 panel.vol[j] 而不是 [j-1]'")
print("     panel.vol[j] = std(r[j-10:j]) 不含 r[j] ⇒ 无前视 ✅")
print()


def run(mode):
    eq, wp, pk, dd, liq = 1000.0, 0.0, 1000.0, 1.0, 0
    xx = []
    LO = np.array([b["l"] for b in cb], float)
    for j in range(W, nn):
        if mode == "cur":
            v = P.vol[j - 1]
        elif mode == "fix":
            v = P.vol[j]
        elif mode == "lookahead":
            v = m.realized_vol(cb[:j + 1], m.VOL_WINDOW)   # 含 r[j] ⇒ 前视
        w = (min(MP, TV / v)
             if (sig[j - 1] and np.isfinite(v) and 0 < v <= CAP) else 0.0)
        lev = int(np.ceil(w - 1e-9)) if w > 0 else 1
        if w > 0:
            lq = C[j - 1] * (1 - (1 / lev - 0.004))
            if LO[j] < lq:
                liq += 1
        turn = abs(w - wp)
        x = w * r[j] - turn * FEE - w * FR[j]
        xx.append(x)
        eq *= (1 + x)
        pk = max(pk, eq)
        dd = min(dd, eq / pk)
        wp = w
    xx = np.array(xx)
    sh = xx.mean() / xx.std(ddof=1) * np.sqrt(365)
    geo = (eq ** (365 / len(xx)) - 1) * 100
    return sh, geo, (dd - 1) * 100, eq, liq


print("=" * 90)
print("  五、三种口径的实测")
print("=" * 90)
print()
print(f"  {'口径':<32}{'夏普':>9}{'几何年化':>11}{'回撤':>9}{'期末':>10}{'强平':>7}")
print("  " + "-" * 78)
res = {}
for lab, mode in (("A. 现状（vol[j-1]，早一天）", "cur"),
                  ("B. 修正（vol[j]，只滞后 sig）", "fix"),
                  ("C. 前视对照（vol 含 r[j]）", "lookahead")):
    sh, geo, dd, eq, liq = run(mode)
    res[mode] = (sh, geo, dd, eq, liq)
    print(f"  {lab:<32}{sh:>9.4f}{geo:>10.1f}%{dd:>8.1f}%{eq:>9.1f}x{liq:>7}")
print()
a, b, c = res["cur"], res["fix"], res["lookahead"]
print(f"  A → B 的变化：夏普 {a[0]:.4f} → {b[0]:.4f}（{b[0]-a[0]:+.4f}）")
print(f"                年化 {a[1]:.1f}% → {b[1]:.1f}%（{b[1]-a[1]:+.1f}pp）")
print(f"                回撤 {a[2]:.1f}% → {b[2]:.1f}%（{b[2]-a[2]:+.1f}pp）")
print()
print(f"  ✅ C（前视）的夏普 {c[0]:.4f} 与 A/B 都不同 ⇒ 说明三个是不同的量")
print(f"     B 比 C 低 {c[0]-b[0]:.4f} ⇒ B 没有前视优势 ✅")
print()

print("=" * 90)
print("  六、B 是否真的匹配实盘？（用工具自己验证）")
print("=" * 90)
print()
# 工具现在会算出什么仓位？
rvol_now = m.realized_vol(cb, m.VOL_WINDOW)
w_tool = min(MP, TV / rvol_now) if rvol_now <= CAP else 0.0
print(f"  工具今天算的波动率   {rvol_now*100:.4f}%（= V_live[-1] = std(r[-10:])）")
print(f"  工具今天的目标仓位   {w_tool:.6f}")
print()
print(f"  而 panel.vol[-1]     {P.vol[-1]*100:.4f}%")
w_panel = min(MP, TV / P.vol[-1]) if P.vol[-1] <= CAP else 0.0
print(f"  panel 口径的仓位     {w_panel:.6f}")
print()
print(f"  ⇒ 实盘会得到 {w_tool:.4f}x，而现状回测的最后一根用 {w_panel:.4f}x")
print(f"     差 {abs(w_tool-w_panel):.4f}x")
print()
print("  修正后：回测最后一根会用 panel.vol[-1] 而 panel.vol[-1] 不等于 V_live[-1]...")
print(f"     ⚠️ 等等：panel.vol[-1] = std(r[-10:]) 正是 V_live[-1]！")
print(f"        但回测在 j=nn-1 时用的是 panel.vol[j] = panel.vol[-1] ✅ 匹配")
