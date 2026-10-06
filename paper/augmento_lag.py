"""
延迟 7 天后还有效吗（augmento_lag.py）
====================================
免费版 lag_days=7 ⇒ 今天只能拿到 7 天前的信号
如果延迟 7 天后仍有效 ⇒ 可以用；否则不能用
"""
import collections
import datetime as dt
import json
import pathlib
import urllib.request

import numpy as np

HERE = pathlib.Path(__file__).parent


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


aug = json.loads((HERE / "_aug.json").read_text(encoding="utf-8"))
S = {}
for x in aug["data"]:
    d = int(dt.datetime.strptime(x["datetime"][:10], "%Y-%m-%d")
            .replace(tzinfo=dt.UTC).timestamp() // 86400)
    S[d] = 1.0 if x["market_indicator_human"] == "bullish" else 0.0

res = {}
for q in ("BTCUSDT", "ETHUSDT"):
    K = get(f"https://data-api.binance.vision/api/v3/klines"
            f"?symbol={q}&interval=1d&limit=1000")
    K2 = get(f"https://data-api.binance.vision/api/v3/klines"
             f"?symbol={q}&interval=1d&limit=1000&endTime={K[0][0]-1}")
    kk = K2 + K
    T = np.array([int(x[0]) for x in kk], float)
    C = np.array([float(x[4]) for x in kk], float)
    nn = len(C)
    r = np.zeros(nn)
    r[1:] = C[1:] / C[:-1] - 1
    ma = np.full(nn, np.nan)
    cs = np.cumsum(np.insert(C, 0, 0.0))
    ma[49:] = (cs[50:] - cs[:-50]) / 50
    vol = np.full(nn, np.nan)
    for i in range(21, nn):
        vol[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)
    DAY = np.array([int(t // 86400000) for t in T], int)
    s_raw = np.array([S.get(int(d), 0.0) for d in DAY])
    s_ma = np.nan_to_num((C > ma).astype(float))
    first = next(i for i in range(nn) if int(DAY[i]) in S)
    res[q] = dict(T=T, C=C, r=r, vol=vol, s_raw=s_raw, s_ma=s_ma,
                  I0=max(60, first), nn=nn)


def bt(x, sig, a, b):
    eq, pk, dd, wp = 1.0, 1.0, 0.0, 0.0
    xs = np.zeros(b - a + 2)
    k = 0
    for i in range(a + 1, b + 1):
        if sig[i - 1] and np.isfinite(x["vol"][i - 1]) and x["vol"][i - 1] > 0:
            w = min(3.0, 0.40 / x["vol"][i - 1])
        else:
            w = 0.0
        xs[k] = w * x["r"][i]
        eq *= (1 + xs[k])
        pk = max(pk, eq)
        dd = min(dd, eq / pk - 1)
        wp = w
        k += 1
    y = xs[:k]
    sh = y.mean() / y.std(ddof=1) * np.sqrt(365) if y.std() > 0 else 0
    return sh, eq, dd


print("=" * 90)
print("  ① 信号本身有预测力吗（不同滞后期）")
print("=" * 90)
print()
print("  方法：看「bullish」信号出现后，未来 N 天的累计收益")
print()
for q in ("BTCUSDT", "ETHUSDT"):
    x = res[q]
    I0, nn = x["I0"], x["nn"]
    print(f"  ── {q} ──")
    print(f"     {'滞后期':>8}{'bullish后N天收益':>18}{'neutral后N天':>16}{'差':>10}")
    for lag in (1, 7, 14, 30):
        gb, gn = [], []
        for i in range(I0, nn - lag):
            fwd = x["C"][i + lag] / x["C"][i] - 1
            (gb if x["s_raw"][i] > 0 else gn).append(fwd)
        if gb and gn:
            print(f"     {lag:>7}天{np.mean(gb)*100:>17.2f}%{np.mean(gn)*100:>15.2f}%"
                  f"{(np.mean(gb)-np.mean(gn))*100:>+9.2f}pp")
    print()

print("=" * 90)
print("  ② 延迟 7 天后，组合策略还有效吗")
print("=" * 90)
print()
for q in ("BTCUSDT", "ETHUSDT"):
    x = res[q]
    I0, nn = x["I0"], x["nn"]
    print(f"  ── {q} ──")
    print(f"     {'方案':<38}{'夏普':>10}{'总倍率':>10}{'回撤':>9}")
    print("     " + "-" * 64)
    trials = [("MA50 单独", x["s_ma"]),
              ("Fusion 单独（无延迟）", x["s_raw"]),
              ("MA50 AND Fusion（无延迟）", x["s_ma"] * x["s_raw"])]
    for lag in (3, 7, 14, 30):
        s_lag = np.concatenate([np.zeros(lag), x["s_raw"][:-lag]])
        trials.append((f"MA50 AND Fusion（延迟 {lag} 天）", x["s_ma"] * s_lag))
    for lab, sg in trials:
        sh, eq, dd = bt(x, sg, I0, nn - 1)
        print(f"     {lab:<38}{sh:>10.4f}{eq:>9.2f}x{dd*100:>8.1f}%")
    print()
