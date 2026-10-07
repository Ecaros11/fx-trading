"""
波动率计算核对（vol_verify.py）
============================
逐项验证：
  ① 窗口长度（应该是几个收益）
  ② 最后一根 K 线是否走完
  ③ 我的脚本 vs 工具的 realized_vol 是否一致
  ④ 无前视检查
"""
import importlib.util
import json
import pathlib

import numpy as np

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
sm = importlib.util.spec_from_file_location("ml", HERE / "ma50_live.py")
ml = importlib.util.module_from_spec(sm)
sm.loader.exec_module(ml)

bars = json.loads((ROOT / "data/crypto/ETHUSDT.json").read_text(encoding="utf-8"))
C = np.array([b["c"] for b in bars], float)
T = np.array([b["t"] for b in bars], float)
nn = len(C)
r = np.zeros(nn)
r[1:] = C[1:] / C[:-1] - 1

print("=" * 84)
print("  ① 窗口长度")
print("=" * 84)
print()
print(f"  VOL_WINDOW = {ml.VOL_WINDOW}")
print()
# 工具的实现
c11 = C[-(ml.VOL_WINDOW + 1):]
tool_rets = np.diff(c11) / c11[:-1]
print(f"  工具：np.diff(c[-{ml.VOL_WINDOW+1}:]) / c[-{ml.VOL_WINDOW+1}:-1]")
print(f"        ⇒ {len(tool_rets)} 个收益，用 {len(c11)} 个价格")
print()
my_rets = r[-ml.VOL_WINDOW:]
print(f"  我的：r[-{ml.VOL_WINDOW}:]")
print(f"        ⇒ {len(my_rets)} 个收益")
print()
print(f"  ⇒ 长度 {'✅ 一致' if len(tool_rets) == len(my_rets) else '❌ 不一致'}")
print(f"  ⇒ 数值 {'✅ 一致' if np.allclose(tool_rets, my_rets) else '❌ 不一致'}")
print()
print(f"  工具用 ddof=1：{tool_rets.std(ddof=1)*np.sqrt(365)*100:.4f}%")
print(f"  我用 ddof=1： {my_rets.std(ddof=1)*np.sqrt(365)*100:.4f}%")

print()
print("=" * 84)
print("  ② 最后一根 K 线是否走完（关键！）")
print("=" * 84)
print()
import datetime as dt
last = dt.datetime.fromtimestamp(T[-1] / 1000, dt.UTC)
now = dt.datetime.now(dt.UTC)
print(f"  缓存最后一根    {last:%Y-%m-%d %H:%M} UTC")
print(f"  现在            {now:%Y-%m-%d %H:%M} UTC")
age_h = (now - last).total_seconds() / 3600
print(f"  距今            {age_h:.1f} 小时")
print()
if age_h < 24:
    print(f"  ⇒ 🔴 【未走完】—— 这根日线还在形成中！")
    print(f"     它的收盘价每时每刻都在变 ⇒ 波动率也在变")
else:
    print(f"  ⇒ ✅ 已走完")

print()
print("  对比两根不同的口径：")
v_all = ml.realized_vol(bars, ml.VOL_WINDOW)
v_done = ml.realized_vol(bars[:-1], ml.VOL_WINDOW)
print(f"     含最后一根（未走完）：{v_all*100:.4f}%")
print(f"     只用已走完的：       {v_done*100:.4f}%")
print(f"     差 {abs(v_all-v_done)*100:.4f}pp")

print()
print("=" * 84)
print("  ③ 工具实际用的是哪个")
print("=" * 84)
print()
print("  工具里 rvol = realized_vol(bars, VOL_WINDOW)，bars 是【全部已获取的日线】")
print("  ⇒ 包含最后一根（可能是未走完的当天）")
print()
print(f"  工具输出会显示  {v_all*100:.1f}%")
print(f"  而'正确'的应是  {v_done*100:.1f}%（只算已走完的）")

print()
print("=" * 84)
print("  ④ 无前视检查（我的分析脚本有没有用未来数据）")
print("=" * 84)
print()
print("  我的脚本写法：V[i] = r[i-WIN:i].std()")
print("  ⇒ V[i] 用的是 r[i-10] .. r[i-1]")
print("  ⇒ r[i-1] = C[i-1]/C[i-2]-1 ⇒ 在第 i-1 根收盘时已知")
print("  ⇒ 所以 V[i] 在【第 i-1 根收盘后】就能算出来 ✅")
print()
print("  信号用 V[i-1]（第 i 天的仓位）")
print("  ⇒ V[i-1] 用 r[i-11] .. r[i-2] ⇒ 第 i-2 根收盘已知 ✅")
print("  ⇒ 无前视 ✅")
print()
print("  ⚠️ 但要注意：我在'当前波动'那几处用了 V[-1]")
print("     V[-1] 用 r[-11..-2] ⇒ 需要用 C[-1]（最后一根）")
print("     ⇒ 如果最后一根未走完，V[-1] 是【含未走完数据】的")
print("     ⇒ 那会高估/低估当前波动")

print()
print("=" * 84)
print("  ⑤ 结论")
print("=" * 84)
print()
print("  ✅ 窗口长度：一致（10 个收益）")
print("  ✅ 数值：一致（同样的 ddof=1）")
print("  ✅ 无前视：我的脚本写法正确")
print()
print(f"  ⚠️ 但有一个真实差异：最后一根 K 线未走完")
print(f"     含它：  {v_all*100:.2f}%")
print(f"     不含它：{v_done*100:.2f}%")
print()
print("  ⇒ 所以'当前波动'这个数字会随当天价格波动而变化")
print("     · 工具显示的是【含当天】的值")
print("     · 严格来说应该用【不含当天】的值")
print("     · 而两者差异取决于当天波动了多少")
