"""
反推 Fusion V1 + 尝试复现（augmento_reverse.py）
============================================
可长期回溯的数据（能覆盖 5 年）：
  · 资金费（Binance，我们有全历史）
  · 恐惧贪婪指数（alternative.me，有全历史）
  · 价格派生：动量、波动、成交量
不可长期回溯（Binance 只给 30 条）：
  · 多空账户比 / 大户比 / taker / OI
"""
import collections
import datetime as dt
import json
import pathlib
import urllib.request

import numpy as np

HERE = pathlib.Path(__file__).parent


def get(url, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if k == tries - 1:
                raise


aug = json.loads((HERE / "_aug.json").read_text(encoding="utf-8"))
S = {}
for x in aug["data"]:
    d = int(dt.datetime.strptime(x["datetime"][:10], "%Y-%m-%d")
            .replace(tzinfo=dt.UTC).timestamp() // 86400)
    S[d] = 1.0 if x["market_indicator_human"] == "bullish" else 0.0

# BTC 日线
K = get("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1000")
K2 = get("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d"
         "&limit=1000&endTime=" + str(K[0][0] - 1))
kk = K2 + K
T = np.array([int(x[0]) for x in kk], float)
C = np.array([float(x[4]) for x in kk], float)
V = np.array([float(x[5]) for x in kk], float)
nn = len(C)
r = np.zeros(nn)
r[1:] = C[1:] / C[:-1] - 1
DAY = np.array([int(t // 86400000) for t in T], int)
sig = np.array([S.get(int(d), np.nan) for d in DAY])


def sma(x, k):
    o = np.full(len(x), np.nan)
    c = np.cumsum(np.insert(x, 0, 0.0))
    o[k - 1:] = (c[k:] - c[:-k]) / k
    return o


# ── 恐惧贪婪指数 ──
try:
    fg = get("https://api.alternative.me/fng/?limit=0&format=json")
    FG = {}
    for x in fg["data"]:
        d = int(dt.datetime.strptime(x["timestamp"], "%Y-%m-%d").replace(
            tzinfo=dt.UTC).timestamp() // 86400) if False else \
            int(int(x["timestamp"]) // 86400)
        FG[d] = float(x["value"])
    fgv = np.array([FG.get(int(d), np.nan) for d in DAY])
    print(f"  恐惧贪婪指数：{len(FG)} 条，"
          f"{dt.datetime.fromtimestamp(min(FG)*86400, dt.UTC):%Y-%m-%d} ~ "
          f"{dt.datetime.fromtimestamp(max(FG)*86400, dt.UTC):%Y-%m-%d}")
except Exception as e:
    print(f"  恐惧贪婪指数拉取失败：{type(e).__name__}")
    fgv = np.full(nn, np.nan)

# ── 候选代理变量 ──
cand = {}
cand["20日动量"] = np.concatenate([np.full(20, np.nan), C[20:] / C[:-20] - 1])
cand["50日动量"] = np.concatenate([np.full(50, np.nan), C[50:] / C[:-50] - 1])
cand["距MA50"] = C / sma(C, 50) - 1
cand["20日波动"] = np.array([np.nan if i < 21 else r[i - 20:i].std(ddof=1)
                            for i in range(nn)])
cand["量比(20日)"] = V / sma(V, 20)
cand["恐惧贪婪"] = fgv
cand["RSI14"] = np.full(nn, np.nan)
d_ = np.zeros(nn)
d_[1:] = C[1:] - C[:-1]
g_, l_ = sma(np.maximum(d_, 0), 14), sma(np.maximum(-d_, 0), 14)
cand["RSI14"] = 100 - 100 / (1 + g_ / np.where(l_ == 0, np.nan, l_))

print()
print("=" * 84)
print("  ① 与 Fusion V1 信号的相关性（只看信号有值的日子）")
print("=" * 84)
print()
m = np.isfinite(sig)
print(f"  可用样本 {int(m.sum())} 天")
print()
print(f"  {'代理变量':<20}{'与 signal 的相关':>18}{'bullish时均值':>15}"
      f"{'neutral时':>13}{'差':>10}")
print("  " + "-" * 76)
for k, v in cand.items():
    mm = m & np.isfinite(v)
    if mm.sum() < 100:
        print(f"  {k:<20}{'样本不足':>18}")
        continue
    corr = np.corrcoef(v[mm], sig[mm])[0, 1]
    a, b = v[mm & (sig > 0)], v[mm & (sig == 0)]
    print(f"  {k:<20}{corr:>+18.3f}{np.mean(a):>15.4f}{np.mean(b):>13.4f}"
          f"{np.mean(a)-np.mean(b):>+10.4f}")

print()
print("=" * 84)
print("  ② 用最简单的组合复现（线性判别）")
print("=" * 84)
print()
# 只用两个最相关的
best = sorted(cand.items(),
              key=lambda kv: -abs(np.corrcoef(
                  kv[1][m & np.isfinite(kv[1])],
                  sig[m & np.isfinite(kv[1])])[0, 1])
              if (m & np.isfinite(kv[1])).sum() > 100 else 0)[:3]
print("  最相关的三个：" + "，".join(f"{k}({np.corrcoef(v[m & np.isfinite(v)], sig[m & np.isfinite(v)])[0,1]:+.3f})"
                              for k, v in best))
print()


def bt(sig_, a, b, vol=None, label=""):
    eq, pk, dd = 1.0, 1.0, 0.0
    xs = []
    for i in range(a + 1, b + 1):
        w = sig_[i - 1] if sig_ is not None else 0.0
        xs.append(w * r[i])
        eq *= (1 + xs[-1])
        pk = max(pk, eq)
        dd = min(dd, eq / pk - 1)
    y = np.array(xs)
    sh = y.mean() / y.std(ddof=1) * np.sqrt(365) if y.std() > 0 else 0
    return sh, eq, dd


I0 = max(60, int(np.argmax(m)))
print(f"  回测区间 {dt.datetime.fromtimestamp(T[I0]/1000, dt.UTC):%Y-%m-%d} ~ "
      f"{dt.datetime.fromtimestamp(T[-1]/1000, dt.UTC):%Y-%m-%d}")
print()
print(f"  {'方案':<34}{'夏普':>10}{'总倍率':>10}{'回撤':>9}")
print("  " + "-" * 64)

ma50 = np.nan_to_num((C > sma(C, 50)).astype(float))
sh, eq, dd = bt(ma50, I0, nn - 1)
print(f"  {'MA50':<34}{sh:>10.4f}{eq:>9.2f}x{dd*100:>8.1f}%")
sh, eq, dd = bt(np.nan_to_num(sig), I0, nn - 1)
print(f"  {'Fusion V1（原版，作基准）':<34}{sh:>10.4f}{eq:>9.2f}x{dd*100:>8.1f}%")

# 简单复现：动量 > 0 且 恐惧贪婪 > 50
for lab, cond in (
        ("动量20 > 0", cand["20日动量"] > 0),
        ("恐惧贪婪 > 50", cand["恐惧贪婪"] > 50),
        ("恐惧贪婪 > 25", cand["恐惧贪婪"] > 25),
        ("动量20>0 且 恐惧贪婪>50", (cand["20日动量"] > 0) & (cand["恐惧贪婪"] > 50)),
        ("距MA50>0 且 恐惧贪婪>50", (cand["距MA50"] > 0) & (cand["恐惧贪婪"] > 50))):
    sg = np.nan_to_num(cond.astype(float))
    sh, eq, dd = bt(sg, I0, nn - 1)
    print(f"  {lab:<34}{sh:>10.4f}{eq:>9.2f}x{dd*100:>8.1f}%")
