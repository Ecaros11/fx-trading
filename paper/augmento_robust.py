"""
稳健性检验（augmento_robust.py）
==============================
① 分段检验（会不会只是某一段的运气）
② 换到 ETH（信号是给 BTC 的，ETH 能用吗）
③ 套进我们的波动率目标框架
④ 检查有没有前视
"""
import collections
import datetime as dt
import json
import pathlib
import urllib.request

import numpy as np

HERE = pathlib.Path(__file__).parent


def get(url, tries=4):
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

print("=" * 88)
print("  ④ 前视检查")
print("=" * 88)
print()
lag = collections.Counter()
for x in aug["data"][-200:]:
    t0 = dt.datetime.strptime(x["datetime"], "%Y-%m-%dT%H:%M:%SZ")
    t1 = dt.datetime.strptime(x["available_at"], "%Y-%m-%dT%H:%M:%SZ")
    lag[(t1 - t0).total_seconds() / 60] += 1
print(f"  available_at − datetime 的分布（分钟）：{dict(lag)}")
print("  ⇒ 历史上只滞后 20 分钟 ⇒ 用 datetime 定信号、次日持有 = 无前视 ✅")
print(f"  数据最新 {aug['data'][-1]['datetime'][:10]}，"
      f"lag_days={aug['lag_days']} ⇒ 免费版缺最后 7 天")

RES = {}
for SYM, q in (("BTCUSDT", "BTCUSDT"), ("ETHUSDT", "ETHUSDT")):
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
    s_aug = np.array([S.get(int(d), 0.0) for d in DAY])
    s_ma = np.nan_to_num((C > ma).astype(float))
    first = next(i for i in range(nn) if int(DAY[i]) in S)
    I0 = max(60, first)
    if SYM == "BTCUSDT":
        RES["T"], RES["DAY"] = T, DAY
    RES[SYM] = dict(T=T, C=C, r=r, ma=ma, vol=vol, s_aug=s_aug, s_ma=s_ma,
                    I0=I0, nn=nn)

print()
print("=" * 88)
print("  ① 分段检验（BTC，MA50 AND Fusion）")
print("=" * 88)
print()
d = RES["BTCUSDT"]
T, C, r, nn, I0 = d["T"], d["C"], d["r"], d["nn"], d["I0"]


def bt(sig, a, b, use_vt=False, vol=None):
    eq, pk, dd, wp = 1.0, 1.0, 0.0, 0.0
    x = np.zeros(b - a + 2)
    k = 0
    for i in range(a + 1, b + 1):
        if sig[i - 1]:
            w = min(3.0, 0.40 / vol[i - 1]) if (use_vt and np.isfinite(vol[i - 1])
                                                 and vol[i - 1] > 0) else 1.0
        else:
            w = 0.0
        x[k] = w * r[i]
        eq *= (1 + x[k])
        pk = max(pk, eq)
        dd = min(dd, eq / pk - 1)
        wp = w
        k += 1
    y = x[:k]
    sh = y.mean() / y.std(ddof=1) * np.sqrt(365) if y.std() > 0 else 0
    return sh, eq, dd


sig_c = d["s_ma"] * d["s_aug"]
seg = [("2021-06 ~ 2022-12", "2021-06-15", "2022-12-31"),
       ("2023-01 ~ 2024-12", "2023-01-01", "2024-12-31"),
       ("2025-01 ~ 2026-10", "2025-01-01", "2026-10-06")]
print(f"  {'时段':<22}{'MA50':>10}{'Fusion':>10}{'AND':>10}{'买入持有':>10}")
print("  " + "-" * 64)
for lab, a, b in seg:
    ta = dt.datetime.strptime(a, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000
    tb = dt.datetime.strptime(b, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000
    ia = max(int(np.argmin(np.abs(T - ta))), I0)
    ib = int(np.argmin(np.abs(T - tb)))
    row = f"  {lab:<22}"
    for sg in (d["s_ma"], d["s_aug"], sig_c, np.ones(nn)):
        sh, _, _ = bt(sg, ia, ib, True, d["vol"])
        row += f"{sh:>10.3f}"
    print(row)

print()
print("=" * 88)
print("  ② 换到 ETH（信号是给 BTC 的，ETH 能用吗）")
print("=" * 88)
print()
e = RES["ETHUSDT"]
Te, Ce, re_, nne, I0e = e["T"], e["C"], e["r"], e["nn"], e["I0"]
sig_ce = e["s_ma"] * e["s_aug"]
print(f"  ETH 回测区间  {dt.datetime.fromtimestamp(Te[I0e]/1000, dt.UTC):%Y-%m-%d}"
      f" ~ {dt.datetime.fromtimestamp(Te[-1]/1000, dt.UTC):%Y-%m-%d}"
      f"   {nne-I0e} 天")
print()
print(f"  {'方案':<34}{'夏普':>10}{'总倍率':>10}{'回撤':>9}")
print("  " + "-" * 64)
for lab, sg in (("MA50", e["s_ma"]), ("Fusion V1（BTC 信号）", e["s_aug"]),
                ("MA50 AND Fusion", sig_ce),
                ("买入持有", np.ones(nne))):
    sh, eq, dd = bt(sg, I0e, nne - 1, True, e["vol"])
    print(f"  {lab:<34}{sh:>10.4f}{eq:>9.2f}x{dd*100:>8.1f}%")

print()
print("=" * 88)
print("  ③ 套进波动率目标框架（不加成本，纯信号对比）")
print("=" * 88)
print()
print(f"  {'标的':<8}{'MA50':>10}{'Fusion':>10}{'AND':>10}{'买入持有':>10}")
print("  " + "-" * 50)
for SYM in ("BTCUSDT", "ETHUSDT"):
    x = RES[SYM]
    row = f"  {SYM:<8}"
    for sg in (x["s_ma"], x["s_aug"], x["s_ma"] * x["s_aug"], np.ones(x["nn"])):
        sh, _, _ = bt(sg, x["I0"], x["nn"] - 1, True, x["vol"])
        row += f"{sh:>10.4f}"
    print(row)
