"""
恐惧贪婪指数能不能强化 MA50（fng.py）
===================================
数据：alternative.me 恐惧贪婪指数（2018-02 至今，免费全历史）
测试：
  ① 单独用作信号（各档阈值）
  ② 作为 MA50 的过滤器（AND）
  ③ 作为反向指标（超买/超卖）
  ④ 变化率 / 平滑
  ⑤ 分段稳健性
两个标的：ETHUSDT（我们的）和 BTCUSDT
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


# ── 恐惧贪婪 ──
raw = get("https://api.alternative.me/fng/?limit=0&format=json")
FG = {}
for x in raw["data"]:
    d = int(int(x["timestamp"]) // 86400)
    FG[d] = float(x["value"])
print("=" * 88)
print("  恐惧贪婪指数")
print("=" * 88)
print()
ds = sorted(FG)
print(f"  条数    {len(FG)}")
print(f"  范围    {dt.datetime.fromtimestamp(ds[0]*86400, dt.UTC):%Y-%m-%d}"
      f" ~ {dt.datetime.fromtimestamp(ds[-1]*86400, dt.UTC):%Y-%m-%d}")
v = np.array(list(FG.values()))
print(f"  分布    中位 {np.median(v):.0f}   均值 {v.mean():.1f}"
      f"   最低 {v.min():.0f}   最高 {v.max():.0f}")
h = collections.Counter()
for x in v:
    h["0-24 极度恐惧" if x < 25 else ("25-49 恐惧" if x < 50 else
      ("50-74 贪婪" if x < 75 else "75-100 极度贪婪"))] += 1
print(f"  分档    {dict(h)}")

RES = {}
for q in ("ETHUSDT", "BTCUSDT"):
    K = get(f"https://data-api.binance.vision/api/v3/klines"
            f"?symbol={q}&interval=1d&limit=1000")
    K2 = get(f"https://data-api.binance.vision/api/v3/klines"
             f"?symbol={q}&interval=1d&limit=1000&endTime={K[0][0]-1}")
    K3 = get(f"https://data-api.binance.vision/api/v3/klines"
             f"?symbol={q}&interval=1d&limit=1000&endTime={K2[0][0]-1}")
    kk = K3 + K2 + K
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
    fg = np.array([FG.get(int(d), np.nan) for d in DAY])
    RES[q] = dict(T=T, C=C, r=r, ma=ma, vol=vol, fg=fg, nn=nn, DAY=DAY)
    ok = np.isfinite(fg)
    print(f"\n  {q}: {nn} 根，其中 {int(ok.sum())} 天有 F&G 值"
          f"（{dt.datetime.fromtimestamp(T[int(np.argmax(ok))]/1000, dt.UTC):%Y-%m-%d} 起）")


def bt(x, sig, a, b, use_vt=True):
    eq, pk, dd = 1000.0, 1000.0, 0.0
    xs = []
    for i in range(a + 1, b + 1):
        if sig[i - 1] and use_vt and np.isfinite(x["vol"][i - 1]) and x["vol"][i - 1] > 0:
            w = min(3.0, 0.40 / x["vol"][i - 1])
        else:
            w = 1.0 if sig[i - 1] else 0.0
        xs.append(w * x["r"][i])
        eq *= (1 + xs[-1])
        pk = max(pk, eq)
        dd = min(dd, eq / pk - 1)
    y = np.array(xs)
    sh = y.mean() / y.std(ddof=1) * np.sqrt(365) if y.std() > 0 else 0
    return sh, eq, dd


for q in ("ETHUSDT", "BTCUSDT"):
    x = RES[q]
    nn, T, fg = x["nn"], x["T"], x["fg"]
    ok = np.isfinite(fg)
    I0 = max(61, int(np.argmax(ok)))
    ma50 = np.nan_to_num((x["C"] > x["ma"]).astype(float))
    bsh, beq, bdd = bt(x, ma50, I0, nn - 1)
    print()
    print("=" * 88)
    print(f"  {q}   回测 {dt.datetime.fromtimestamp(T[I0]/1000, dt.UTC):%Y-%m-%d}"
          f" ~ {dt.datetime.fromtimestamp(T[-1]/1000, dt.UTC):%Y-%m-%d}"
          f"   {nn-1-I0} 天")
    print(f"  基准 MA50：夏普 {bsh:.4f}   期末 {beq:,.0f}   回撤 {bdd*100:.1f}%")
    print("=" * 88)
    print()
    print(f"  {'方案':<36}{'夏普':>10}{'Δ':>10}{'回撤':>9}{'在场':>7}")
    print("  " + "-" * 74)
    rows = []

    def add(lab, sg):
        sg = np.nan_to_num(sg)
        sh, eq, dd = bt(x, sg, I0, nn - 1)
        on = int(np.nansum(sg[I0:]))
        rows.append((lab, sh, sh - bsh, dd, on))
        tag = "  ✅" if sh > bsh + 0.01 else ("  ⚠️" if sh < bsh - 0.01 else "  ≈")
        print(f"  {lab:<36}{sh:>10.4f}{sh-bsh:>+10.4f}{dd*100:>8.1f}%{on:>7}{tag}")

    add("MA50（基准）", ma50)
    print("  ── ① F&G 单独 ──")
    for th in (25, 40, 50, 60, 75):
        add(f"F&G > {th} 就做多", (fg > th).astype(float))
    for th in (25, 50, 75):
        add(f"F&G < {th} 就做多（反向）", (fg < th).astype(float))
    print("  ── ② 作为 MA50 的过滤器 ──")
    for th in (25, 40, 50, 60):
        add(f"MA50 且 F&G > {th}", ma50 * (fg > th))
    add("MA50 且 F&G < 75（不追极度贪婪）", ma50 * (fg < 75))
    add("MA50 且 F&G < 90", ma50 * (fg < 90))
    print("  ── ③ 变化率 / 平滑 ──")
    fg5 = np.full(nn, np.nan)
    for i in range(5, nn):
        seg = fg[i - 5:i]
        fg5[i] = np.nanmean(seg) if np.isfinite(seg).any() else np.nan
    add("MA50 且 F&G 5日均 > 40", ma50 * (fg5 > 40))
    add("MA50 且 F&G 5日均 > 50", ma50 * (fg5 > 50))
    chg = np.full(nn, np.nan)
    chg[5:] = fg[5:] - fg[:-5]
    add("MA50 且 F&G 5日上升", ma50 * (chg > 0))
    add("MA50 且 F&G 5日下降", ma50 * (chg < 0))

    print()
    print("  ── ① 分段稳健性（最好的那个）──")
    best = max(rows, key=lambda x_: x_[1])
    print(f"     最好的是：{best[0]}（夏普 {best[1]:.4f}）")
    seg = [(I0, I0 + (nn - I0) // 3), (I0 + (nn - I0) // 3, I0 + 2 * (nn - I0) // 3),
           (I0 + 2 * (nn - I0) // 3, nn - 1)]
    for a, b in seg:
        s1, _, _ = bt(x, ma50, a, b)
        print(f"     {dt.datetime.fromtimestamp(T[a]/1000, dt.UTC):%Y-%m} ~ "
              f"{dt.datetime.fromtimestamp(T[b]/1000, dt.UTC):%Y-%m}   "
              f"MA50 {s1:>7.3f}")
