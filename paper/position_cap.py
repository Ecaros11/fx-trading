"""
仓位会不会超过 1.98x（做到 50% 距离的上限）
==========================================
要 50% 距离：保证金 = 名义 × 0.504 ≤ 权益
           ⇒ 仓位 w ≤ 1/0.504 = 1.984x
"""
import importlib.util
import json
import pathlib

import numpy as np

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
sa = importlib.util.spec_from_file_location("al", HERE / "align.py")
al = importlib.util.module_from_spec(sa)
sa.loader.exec_module(al)

CAP = 1.20
LIMIT = 1 / (0.50 + 0.004)          # 1.9841

bars = json.loads((ROOT / "data/crypto/ETHUSDT.json").read_text(encoding="utf-8"))
C = np.array([b["c"] for b in bars], float)
nn = len(C)
r = np.zeros(nn)
r[1:] = C[1:] / C[:-1] - 1
V = np.full(nn, np.nan)
for i in range(al.VOL_WIN + 1, nn):
    V[i] = r[i - al.VOL_WIN:i].std(ddof=1) * np.sqrt(365)
ma = np.full(nn, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
sig = C > ma

print("=" * 88)
print("  ① 门槛：仓位 ≤ 多少才能做到 50% 距离")
print("=" * 88)
print()
print(f"  要 50% 距离：保证金 = 名义 × (50% + 0.4%) ≤ 权益")
print(f"  ⇒ 仓位 w ≤ 1 / 0.504 = {LIMIT:.4f}x")
print()
print(f"  60% 档仓位 = min(3, 0.60 ÷ 波动)")
print(f"  ⇒ w ≤ {LIMIT:.4f} ⟺ 波动 ≥ 0.60/{LIMIT:.4f} = {0.60/LIMIT*100:.1f}%")
print()
print(f"  ⇒ ⚠️ 只要 10 日波动 < {0.60/LIMIT*100:.1f}%，就【做不到】50% 距离")

print()
print("=" * 88)
print("  ② 历史上波动率 < 30.2% 的比例（= 做不到 50% 的日子）")
print("=" * 88)
print()
on = [(i, V[i - 1]) for i in range(61, nn)
      if sig[i - 1] and np.isfinite(V[i - 1]) and 0 < V[i - 1] <= CAP]
vols = np.array([v for _, v in on])
ws = np.minimum(3.0, 0.60 / vols)

for p in (1, 5, 10, 25, 50, 75, 90, 95, 99):
    print(f"     {p:>3} 分位波动  {np.percentile(vols, p)*100:>6.1f}%"
          f"     对应仓位 {np.percentile(ws, p):>6.3f}x")

print()
n_over = int((ws > LIMIT).sum())
print(f"  在场天数            {len(ws)}")
print(f"  仓位 > {LIMIT:.3f}x 的天数  {n_over}（{n_over/len(ws)*100:.1f}%）")
print(f"  ⇒ 🔴 {n_over/len(ws)*100:.1f}% 的时间【做不到】50% 距离")

print()
print("=" * 88)
print("  ③ 换个角度：各目标距离分别有多少时间做得到")
print("=" * 88)
print()
print(f"  {'目标距离':>10}{'仓位上限':>10}{'对应波动':>10}{'可做到的比例':>14}")
print("  " + "-" * 46)
for t in (0.30, 0.40, 0.45, 0.50, 0.55, 0.60):
    lim = 1 / (t + 0.004)
    pct = (ws <= lim).mean() * 100
    print(f"  {t*100:>9.0f}%{lim:>9.3f}x{0.60/lim*100:>9.1f}%{pct:>13.1f}%")

print()
print("=" * 88)
print("  ④ 你的当前情况")
print("=" * 88)
print()
v_now = V[-1]
w_now = min(3.0, 0.60 / v_now) if np.isfinite(v_now) else 0
print(f"  10 日波动        {v_now*100:.2f}%")
print(f"  目标仓位         min(3, 0.60/{v_now*100:.2f}%) = {w_now:.3f}x")
print(f"  能否做到 50%？   {'✅ 能' if w_now <= LIMIT else '🔴 不能'}"
      f"（上限 {LIMIT:.3f}x）")
print()
eq = 89.10
nom = eq * w_now
print(f"  权益 {eq:.2f}U ⇒ 名义 {nom:.2f}U")
print(f"  50% 距离需要保证金  {nom*0.504:.2f}U")
print(f"  但全部权益只有      {eq:.2f}U")
print(f"  ⇒ 缺口 {nom*0.504-eq:+.2f}U")

print()
print("=" * 88)
print("  ⑤ 结论")
print("=" * 88)
print()
print("  ⚠️ 你的问题反过来了 —— 不是【会不会】涨到 3.0x，")
print("     而是【它现在就在 3.0x，而且大部分时间都在高位】：")
print()
print(f"     仓位 > {LIMIT:.3f}x 的时间占比：{n_over/len(ws)*100:.1f}%")
print(f"     仓位中位数：{np.median(ws):.3f}x")
print(f"     仓位 = 3.0x（被上限截断）的时间占比：{(ws >= 2.999).mean()*100:.1f}%")
print()
print("  ⇒ 所以在 60% 档下，用追加保证金到 50% 距离这个方案")
print(f"     只在 {100-n_over/len(ws)*100:.1f}% 的时间有效")
print("     ⇒ 它【不是】一个可以长期依赖的保护措施")
