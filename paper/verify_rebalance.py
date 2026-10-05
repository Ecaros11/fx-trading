"""
最重要的一问：波动率目标版需要每天调仓吗？
=======================================
代码里 action 是这样算的：
    "action": ("买入" if sig["long"] else "卖出")
              if (prev_long != long) else "不动"
    # 注释：action 只看【信号切换】，不看仓位大小变化。

但波动率目标版的仓位是 每天变化的（target_vol / 已实现波动）。
如果 action 永远只写"信号切换"时的动作，那：
  · 输出会说"无需操作"
  · 但实际需要每天重新计算数量
  · 换手和手续费会被系统性低估
"""
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
SYM = "ETHUSDT"
PY = 365.0
FEE = 0.0005

d1 = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
C = np.array([b["c"] for b in d1], float)
n = len(C)
r = np.zeros(n)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(n, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
sig = np.nan_to_num((C > ma).astype(float))
vol20 = np.full(n, np.nan)
for i in range(21, n):
    vol20[i] = r[i - 20:i].std(ddof=1) * np.sqrt(PY)

print("=" * 96)
print("  ① 两个版本的仓位序列长什么样")
print("=" * 96)
for lab, tv in (("固定 1.405x", None), ("波动率目标 40%", 0.40), ("波动率目标 25%", 0.25)):
    w = sig * 1.405 if tv is None else sig * np.clip(tv / vol20, 0, 3)
    w = np.nan_to_num(w)[60:]
    on = w[w > 0]
    chg = np.abs(np.diff(w))
    print(f"  {lab:<16} 有仓日 {len(on):>5}   平均仓位 {on.mean():.3f}   "
          f"仓位区间 [{on.min():.3f}, {on.max():.3f}]")
    print(f"  {'':<16} 仓位变动>0.01 的日子 {int((chg > 0.01).sum()):>5} "
          f"({(chg > 0.01).mean()*100:.1f}%)")

print()
print("=" * 96)
print("  ② 关键：action 只看信号切换，会漏掉多少调仓")
print("=" * 96)
w40 = np.nan_to_num(sig * np.clip(0.40 / vol20, 0, 3))[60:]
s = sig[60:]
n_flip_signal = int((np.diff(s) != 0).sum())
n_rebal = int((np.abs(np.diff(w40)) > 0.01).sum())
print(f"  波动率目标 40%：")
print(f"    action 记录的（信号切换）      {n_flip_signal:>5} 次")
print(f"    实际需要调仓的（仓位变化>1%）   {n_rebal:>5} 次")
print(f"    ⇒ 漏掉 {n_rebal - n_flip_signal} 次（{(n_rebal-n_flip_signal)/max(n_rebal,1)*100:.0f}%）")

print()
print("=" * 96)
print("  ③ 这会不会低估成本？")
print("=" * 96)
yrs = (d1[-1]["t"] - d1[0]["t"]) / 1000 / 86400 / 365
for lab, tv in (("固定 1.405x", None), ("波动率目标 40%", 0.40), ("波动率目标 25%", 0.25)):
    w = sig * 1.405 if tv is None else sig * np.clip(tv / vol20, 0, 3)
    w = np.nan_to_num(w)
    turn = np.abs(np.diff(np.concatenate([[0.0], w])))
    # 只算信号切换时的换手（= 全仓进出的那部分）
    flips = np.diff(sig) != 0
    turn_flip_only = np.zeros(len(w))
    turn_flip_only[1:][flips] = np.abs(np.diff(w))[flips] if flips.sum() else 0
    print(f"  {lab:<16} 全部换手 {turn.sum()/yrs:>6.1f}/年   "
          f"只算信号切换 {turn_flip_only.sum()/yrs:>6.1f}/年   "
          f"手续费 {turn.sum()*FEE*2/yrs*100:>5.2f}%/年 vs "
          f"{turn_flip_only.sum()*FEE*2/yrs*100:.2f}%/年")

print()
print("=" * 96)
print("  ④ 实际输出会怎么说")
print("=" * 96)
print("""
  在波动率目标版下，若今天信号没切换，工具会打印：

     （与上一日一致，无需操作）

  而归档里 action = "不动"

  但真实需要：每天用新的已实现波动重算目标数量，并按差额下单。
  这是一条【每天都要执行】的指令，不是"不动"。
""")
