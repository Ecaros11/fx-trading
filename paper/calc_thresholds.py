"""
算各版本的最低本金门槛（calc_thresholds.py）
==========================================
门槛定义：达到该本金后，≥90% 的【有仓位日】都能下出 20U 最小单。
即  本金 × 有仓位日的第 10 分位仓位 ≥ 20U
"""
import collections
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
SYM = "ETHUSDT"
MIN_NOTIONAL = 20.0

j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
C = np.array([b["c"] for b in j], float)
n = len(C)
r = np.zeros(n)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(n, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
sig = np.nan_to_num((C > ma).astype(float))
vol20 = np.full(n, np.nan)
for i in range(21, n):
    vol20[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)

print("=" * 84)
print("  各版本的门槛（本金 × 有仓位日第10分位仓位 ≥ 20U）")
print("=" * 84)
print()
print(f"  {'版本':<20}{'原始仓位10分位':>16}{'平均仓位':>11}{'门槛本金':>12}")
print("  " + "-" * 62)

out = {}
for lab, tv in (("固定 1.405x", None), ("波动率目标 40%", 0.40),
                ("波动率目标 25%", 0.25), ("波动率目标 15%", 0.15)):
    if tv is None:
        w = sig * 1.405
    else:
        w = sig * np.clip(tv / vol20, 0, 3)
    w = np.nan_to_num(w)[60:]
    on = w[w > 0]
    p10 = np.percentile(on, 10)
    need = MIN_NOTIONAL / p10
    out[lab] = (tv, p10, on.mean(), need)
    print(f"  {lab:<20}{p10:>16.3f}{on.mean():>11.3f}{need:>11.1f}U")

print()
print("  ── 检查：门槛本金下，实际有多少比例的日子能下出单 ──")
print()
print(f"  {'版本':<20}{'门槛本金':>10}{'能下单比例':>13}{'平均名义':>11}{'最大名义':>11}")
print("  " + "-" * 68)
for lab, (tv, p10, avg, need) in out.items():
    if tv is None:
        w = sig * 1.405
    else:
        w = sig * np.clip(tv / vol20, 0, 3)
    w = np.nan_to_num(w)[60:]
    on = w[w > 0]
    notion = on * need
    print(f"  {lab:<20}{need:>9.1f}U{(notion >= MIN_NOTIONAL).mean()*100:>12.1f}%"
          f"{notion.mean():>10.1f}U{notion.max():>10.1f}U")

print()
print("  ── 生成 METHODS 常量 ──")
print()
for lab, (tv, p10, avg, need) in out.items():
    tv_s = "None" if tv is None else f"{tv:.2f}"
    print(f'    ("{lab}", {tv_s}, {need:.1f}),')
