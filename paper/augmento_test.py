"""
Augmento Fusion V1 vs 我们的 MA50（augmento_test.py）
==================================================
数据源（全部公开，无需 key）：
  · 情绪信号  https://augmento.ai/api/v1/market-indicator/1
  · BTC 日线  Binance data-api.binance.vision

对比：
  ① Fusion V1 单独（bullish 持多，其余空仓）—— 按他们的规则
  ② 我们的 MA50 单独
  ③ MA50 AND Fusion（两个都看多才在场）
  ④ MA50 OR  Fusion（任一 Demo 看多就在场）
"""
import collections
import datetime as dt
import json
import pathlib
import sys
import urllib.request

import numpy as np

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent


def get(url, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if k == tries - 1:
                raise
    return None


# ── 情绪信号 ──
sig_path = HERE / "_aug.json"
if sig_path.exists():
    aug = json.loads(sig_path.read_text(encoding="utf-8"))
else:
    aug = get("https://augmento.ai/api/v1/market-indicator/1")
    sig_path.write_text(json.dumps(aug), encoding="utf-8")
rows = aug["data"]
print("=" * 90)
print("  Augmento Fusion V1")
print("=" * 90)
print()
print(f"  名称        {aug['name']}")
print(f"  标的        {aug['reference_asset_symbol']}")
print(f"  免费版延迟  {aug['lag_days']} 天")
print(f"  条数        {len(rows)}")
print(f"  范围        {rows[0]['datetime'][:10]} ~ {rows[-1]['datetime'][:10]}")
cnt = collections.Counter(x["market_indicator_human"] for x in rows)
print(f"  分布        {dict(cnt)}")

# 信号字典：date -> 1/0
S = {}
for x in rows:
    d = int(dt.datetime.strptime(x["datetime"][:10], "%Y-%m-%d")
            .replace(tzinfo=dt.UTC).timestamp() // 86400)
    S[d] = 1.0 if x["market_indicator_human"] == "bullish" else 0.0
print(f"  bullish 天数 {sum(S.values()):.0f} / {len(S)}"
      f"（{sum(S.values())/len(S)*100:.0f}%）")

# ── BTC 日线 ──
print()
print("  拉 BTC 日线…")
K = get("https://data-api.binance.vision/api/v3/klines"
        "?symbol=BTCUSDT&interval=1d&limit=1000")
K2 = get("https://data-api.binance.vision/api/v3/klines"
         "?symbol=BTCUSDT&interval=1d&limit=1000&endTime=" + str(K[0][0] - 1))
kk = K2 + K
T = np.array([int(x[0]) for x in kk], float)
C = np.array([float(x[4]) for x in kk], float)
nn = len(C)
print(f"  {nn} 根   {dt.datetime.fromtimestamp(T[0]/1000, dt.UTC):%Y-%m-%d}"
      f" ~ {dt.datetime.fromtimestamp(T[-1]/1000, dt.UTC):%Y-%m-%d}")

r = np.zeros(nn)
r[1:] = C[1:] / C[:-1] - 1
ma50 = np.full(nn, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma50[49:] = (cs[50:] - cs[:-50]) / 50


def sma(x, k):
    o = np.full(len(x), np.nan)
    c = np.cumsum(np.insert(x, 0, 0.0))
    o[k - 1:] = (c[k:] - c[:-k]) / k
    return o


# 逐日信号（按交易日索引）
DAY = np.array([int(t // 86400000) for t in T], int)
s_aug = np.array([S.get(int(d), 0.0) for d in DAY])
s_ma = np.nan_to_num((C > ma50).astype(float))

print()
print("=" * 90)
print("  回测（重合区间，双方信号都有数据的部分）")
print("=" * 90)
print()
# 起始 = 情绪信号第一天 && MA50 可用
start = max(60, int(np.argmax(s_aug > -1)) if (s_aug > -1).any() else 60)
first_aug = next(i for i in range(nn) if int(DAY[i]) in S)
I0 = max(60, first_aug)
n = nn - I0
print(f"  回测区间  {dt.datetime.fromtimestamp(T[I0]/1000, dt.UTC):%Y-%m-%d}"
      f" ~ {dt.datetime.fromtimestamp(T[-1]/1000, dt.UTC):%Y-%m-%d}   {n} 天")
print()


def bt(sig, label):
    eq, pk, dd, wp = 1.0, 1.0, 0.0, 0.0
    x = np.zeros(nn)
    on = 0
    for i in range(I0 + 1, nn):
        w = sig[i - 1]
        x[i] = w * r[i]
        eq *= (1 + x[i])
        pk = max(pk, eq)
        dd = min(dd, eq / pk - 1)
        on += int(w > 0)
        wp = w
    y = x[I0:]
    sh = y.mean() / y.std(ddof=1) * np.sqrt(365) if y.std() > 0 else 0
    return sh, eq, dd, on


buyhold = C[-1] / C[I0]
print(f"  {'方案':<34}{'夏普':>9}{'总倍率':>11}{'回撤':>9}{'在场':>7}")
print("  " + "-" * 72)
for lab, sig in (
        ("① Fusion V1（情绪，按仓库规则）", s_aug),
        ("② 我们的 MA50", s_ma),
        ("③ MA50 AND Fusion", s_ma * s_aug),
        ("④ MA50 OR  Fusion", np.clip(s_ma + s_aug, 0, 1)),
        ("⑤ 买入持有", np.ones(nn))):
    sh, eq, dd, on = bt(sig, lab)
    print(f"  {lab:<34}{sh:>9.4f}{eq:>10.2f}x{dd*100:>8.1f}%{on:>7}")

print()
print("=" * 90)
print("  信号重叠度")
print("=" * 90)
print()
a, b = s_aug[I0:], s_ma[I0:]
print(f"  两个都说做多    {int(((a>0)&(b>0)).sum()):>5} 天"
      f"  （占 MA50 在场天数的 {((a>0)&(b>0)).sum()/max((b>0).sum(),1)*100:.0f}%）")
print(f"  只有 MA50 说多  {int(((a==0)&(b>0)).sum()):>5} 天")
print(f"  只有 Fusion 说多 {int(((a>0)&(b==0)).sum()):>5} 天")
print(f"  两个都空仓      {int(((a==0)&(b==0)).sum()):>5} 天")
