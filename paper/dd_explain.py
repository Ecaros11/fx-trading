"""
回撤口径详解（dd_explain.py）
============================
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
bars, _ = m.complete_bars(bars)
C = np.array([b["c"] for b in bars], float)
fr = json.loads((ROOT / "data" / "funding" / "ETHUSDT.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
CD = np.array([int(b["t"] // 86400000) for b in bars])
FR = np.nan_to_num(np.array(
    [float(np.sum(agg[int(d)])) if int(d) in agg else np.nan for d in CD]))
P = al.panel(C, FR)
W = 60
x = P.net(0.60)[W:]
x = x[np.isfinite(x)]

print("=" * 88)
print("  一、回撤的定义")
print("=" * 88)
print()
print("  回撤(d) = 权益(d) / 历史峰值权益 − 1")
print()
print("  ⚠️ 关键：分母是【历史峰值】，不是初始本金")
print("     所以它衡量的是「从最高点跌下来多少」")
print()

# 逐年回撤
DAY = [__import__("datetime").datetime.fromtimestamp(b["t"] / 1000,
                                                     __import__("datetime").UTC)
       for b in bars][W:]
eq = np.cumprod(1 + x)
peak = np.maximum.accumulate(eq)
dd = eq / peak - 1
print("=" * 88)
print("  二、回测里最大回撤发生的时刻")
print("=" * 88)
print()
i_worst = int(np.argmin(dd))
print(f"  最坏回撤 {dd[i_worst]*100:.1f}%  发生在 {DAY[i_worst]:%Y-%m-%d}")
# 找对应的峰值
ipk = int(np.argmax(eq[:i_worst + 1]))
print(f"  峰值在     {DAY[ipk]:%Y-%m-%d}（权益 {eq[ipk]:.2f}x）")
print(f"  谷底在     {DAY[i_worst]:%Y-%m-%d}（权益 {eq[i_worst]:.2f}x）")
print(f"  持续       {(DAY[i_worst]-DAY[ipk]).days} 天（{(DAY[i_worst]-DAY[ipk]).days/30:.1f} 个月）")
# 多久恢复
after = eq[i_worst:]
rec = np.where(after >= eq[ipk])[0]
if len(rec):
    print(f"  恢复到峰值 {DAY[i_worst + rec[0]]:%Y-%m-%d}"
          f"（又用了 {(DAY[i_worst+rec[0]]-DAY[i_worst]).days} 天）")
else:
    print(f"  到数据结束仍未恢复")

print()
print("=" * 88)
print("  三、所有超过 −30% 的回撤段")
print("=" * 88)
print()
segs = []
inseg = False
for i in range(len(dd)):
    if dd[i] < -0.30 and not inseg:
        inseg = True
        st = i
    elif dd[i] >= -0.30 and inseg:
        inseg = False
        segs.append((st, i))
if inseg:
    segs.append((st, len(dd) - 1))
print(f"  共 {len(segs)} 段，最深的几段：")
segs2 = sorted(segs, key=lambda s: dd[s[0]:s[1] + 1].min())
print(f"  {'起点':>12}{'谷底':>12}{'最深':>9}{'持续':>8}{'恢复':>10}")
print("  " + "-" * 54)
for st, en in segs2[:8]:
    lo = dd[st:en + 1].min()
    pk = eq[st - 1]
    rec = np.where(eq[en:] >= pk)[0]
    r = f"{len(rec) and (DAY[en+rec[0]]-DAY[st]).days} 天" if len(rec) else "未恢复"
    print(f"  {DAY[st]:%Y-%m-%d}{DAY[st+int(np.argmin(dd[st:en+1]))]:>13}"
          f"{lo*100:>8.1f}%{(DAY[en]-DAY[st]).days:>7}天{r:>11}")

print()
print("=" * 88)
print("  四、−60% 这条线该怎么看")
print("=" * 88)
print()
print(f"  历史最坏            {dd.min()*100:.1f}%")
print(f"  规则触发线          −60.0%")
print(f"  ⇒ 触发线比历史最坏深 {abs(-0.60 - dd.min())*100:.1f}pp")
print()
print("  ⚠️ 它【不是止损线】，而是【重新评估线】：")
print("     · 触及它时，你已经亏了 60% —— 那时止损已经晚了")
print("     · 它的意思是「这次比历史任何一次都坏」")
print("     · ⇒ 说明策略的前提可能失效了，该停下来查原因")
print()
print("  🔑 但真正该问的不是「跌到 −60% 怎么办」，而是：")
print("     「我能不能扛住 −52.5%？」")
print()

# 用当前权益算实际金额
EQ = 75.94
print(f"  以你当前 {EQ:.2f} U 为例：")
print(f"     历史最坏 −52.5%  ⇒  跌到 {EQ*(1-0.525):.2f} U（亏 {EQ*0.525:.2f} U）")
print(f"     触发线   −60.0%  ⇒  跌到 {EQ*0.40:.2f} U（亏 {EQ*0.60:.2f} U）")
print(f"     3x 强平  −32.9%  ⇒  跌到 {EQ*(1-0.329):.2f} U —— 但那是【单日】的事")
print()
print("  ⚠️ 注意两个 −32.9% 不是一回事：")
print("     · 3x 强平线：标的跌 32.9% ⇒ 那一天就归零")
print("     · 回撤 −52.5%：权益从峰值跌 52.5% ⇒ 可能历时几个月")
