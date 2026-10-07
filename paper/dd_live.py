"""
我的实际回撤（dd_live.py）—— 可复用版
====================================
任何时候跑都可以。数据全部从 API + 本地记录现取。

用法：
    uv run dd_live.py                # 算当前回撤
    uv run dd_live.py --snapshot     # 把今天的权益记进本地（建议每天跑）
    uv run dd_live.py --history      # 看权益记录

原理：
    回撤 = 当前权益 ÷ 历史峰值权益 − 1
    峰值来自两条来源，取最大值：
      ① 本地快照（data/live/equity_log.csv）—— 精确，但从现在开始积累
      ② API 流水重建 —— 能回溯，但只含【已实现】部分
"""
import argparse
import csv
import datetime as dt
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
EQ_LOG = ROOT / "data" / "live" / "equity_log.csv"
MMR = 0.004


def api_retry(fn, what="", tries=3, base=1.5):
    """
    带重试的 API 调用。

    ⚠️ 2026-10-07：dd_live.py 原来直接调 bn.futures_account()/positions()，
       网络抖动（TimeoutError）会让整个脚本崩掉并抛 traceback。
       对一个每天要跑的工具，应该重试 + 降级，而不是崩。

    返回 (ok, value, errmsg)
    """
    import time as _t
    last = None
    for k in range(tries):
        try:
            return True, fn(), None
        except Exception as e:
            last = e
            if k < tries - 1:
                _t.sleep(base * (k + 1))
    return False, None, f"{type(last).__name__}: {str(last)[:100]}"


def bn_api():
    from binance_api import BN
    return BN()


def net_capital(bn):
    """累计净转入（分页）。"""
    rows, start = [], None
    for _ in range(10):
        p = {"incomeType": "TRANSFER", "limit": 1000}
        if start:
            p["startTime"] = start
        try:
            r = bn.fapi("/fapi/v1/income", p, signed=True)
        except Exception:
            break
        if not r:
            break
        rows.extend(r)
        if len(r) < 1000:
            break
        start = int(r[-1]["time"]) + 1
    return sum(float(x["income"]) for x in rows), rows


def rebuild_curve(bn, max_pages=20):
    """用 API 流水重建【已实现】权益曲线。"""
    rows, start = [], None
    for _ in range(max_pages):
        p = {"limit": 1000}
        if start:
            p["startTime"] = start
        try:
            r = bn.fapi("/fapi/v1/income", p, signed=True)
        except Exception:
            break
        if not r:
            break
        rows.extend(r)
        if len(r) < 1000:
            break
        start = int(r[-1]["time"]) + 1
    if not rows:
        return [], 0.0
    rows.sort(key=lambda x: int(x["time"]))
    cash, curve = 0.0, []
    for x in rows:
        cash += float(x["income"])
        curve.append((int(x["time"]), cash))
    return curve, cash


def load_snapshots():
    if not EQ_LOG.exists():
        return []
    out = []
    with EQ_LOG.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out.append((r["date"], float(r["equity"]), float(r["wallet"]),
                            float(r["unrealized"]), float(r.get("capital") or 0)))
            except Exception:
                continue
    return out


def save_snapshot(eq, wal, unreal, capital):
    EQ_LOG.parent.mkdir(parents=True, exist_ok=True)
    today = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    rows = []
    if EQ_LOG.exists():
        with EQ_LOG.open(encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("date") != today]
    rows.append({"date": today, "ts": f"{today}T00:00:00Z",
                 "equity": f"{eq:.6f}", "wallet": f"{wal:.6f}",
                 "unrealized": f"{unreal:.6f}", "capital": f"{capital:.6f}"})
    rows.sort(key=lambda r: r["date"])
    with EQ_LOG.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "ts", "equity", "wallet",
                                          "unrealized", "capital"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true",
                    help="把今天的权益记进本地（建议每天跑）")
    ap.add_argument("--history", action="store_true", help="看权益记录")
    a = ap.parse_args()

    bn = bn_api()
    ok, acct, err = api_retry(bn.futures_account, what="读账户")
    if not ok:
        print(f"  \u274c 读账户失败（重试 3 次）：{err}")
        print("     \u00b7 检查网络 / 代理（本项目需要 127.0.0.1:1080）")
        print("     \u00b7 或稍后重跑：uv run dd_live.py")
        sys.exit(1)
    eq = float(acct.get("totalMarginBalance", 0))
    wal = float(acct.get("totalWalletBalance", 0))
    unreal = float(acct.get("totalUnrealizedProfit", 0))
    cap, trows = net_capital(bn)

    if a.snapshot:
        n = save_snapshot(eq, wal, unreal, cap)
        print(f"  [OK] 已记录 {dt.datetime.now(dt.UTC):%Y-%m-%d}："
              f"权益 {eq:.4f}  钱包 {wal:.4f}  浮亏 {unreal:+.4f}")
        print(f"       {EQ_LOG}（共 {n} 天）")
        # ⚠️ 2026-10-07：顺手把回撤也打出来 —— 用户要的是"每天一条命令就知道
        #    自己亏到哪了"，不必再跑一次默认模式。
        snaps_now = load_snapshots()
        curve_now, _ = rebuild_curve(bn)
        pk = [max(v for _, v in curve_now)] if curve_now else []
        if snaps_now:
            pk.append(max(e for _, e, _, _, _ in snaps_now))
        if pk:
            PEAK = max(pk)
            dd = eq / PEAK - 1
            print()
            print(f"  ┌─ 回撤 ─────────────────────────────────────────────")
            print(f"  │  峰值权益   {PEAK:>10,.4f} U")
            print(f"  │  当前权益   {eq:>10,.4f} U")
            print(f"  │  回撤       {dd*100:>+9.2f}%")
            print(f"  │  历史最坏   {-54.30:>+9.2f}%   还有 "
                  f"{abs(-54.3 - dd*100):>5.1f}pp")
            print(f"  │  -60% 线    {-60.00:>+9.2f}%   还有 "
                  f"{abs(-60.0 - dd*100):>5.1f}pp")
            print(f"  └────────────────────────────────────────────────────")
        return

    snaps = load_snapshots()
    if a.history:
        if not snaps:
            print("  还没有记录。跑 `uv run dd_live.py --snapshot` 开始积累。")
            return
        print("=" * 76)
        print("  权益记录")
        print("=" * 76)
        print(f"  {'日期':<12}{'权益':>11}{'钱包':>11}{'浮亏':>11}{'回撤':>10}")
        pk = 0.0
        for d, e, w, u, c in snaps:
            pk = max(pk, e)
            print(f"  {d:<12}{e:>11.4f}{w:>11.4f}{u:>+11.4f}"
                  f"{(e/pk-1)*100 if pk else 0:>9.2f}%")
        return

    print("=" * 76)
    print(f"  我的实际回撤   （{dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC）")
    print("=" * 76)
    print()
    print(f"  本金（累计净转入）   {cap:>12,.4f} USDT")
    print(f"  当前权益             {eq:>12,.4f} USDT")
    print(f"  钱包余额             {wal:>12,.4f} USDT")
    print(f"  未实现盈亏           {unreal:>+12,.4f} USDT")
    print()
    ok_p, poss, err_p = api_retry(bn.positions, what="读持仓")
    if not ok_p:
        print(f"  \u26a0\ufe0f  读持仓失败（重试 3 次）：{err_p}")
        print("     \u00b7 跳过持仓信息，继续算回撤（回撤不依赖持仓明细）")
        poss = []
    for p in (poss or []):
        amt = float(p["positionAmt"])
        if abs(amt) < 1e-9:
            continue
        ent, mark = float(p["entryPrice"]), float(p["markPrice"])
        lev = int(float(p.get("leverage", 3)))
        lq = ent * (1 - 1 / lev + MMR)
        print(f"  持仓 {amt} ETH @ {ent:,.2f}  标记 {mark:,.2f}  {lev}x  "
              f"名义 {abs(amt)*mark:,.2f} U")
        print(f"       强平价 {lq:,.2f}（距今 {(lq/mark-1)*100:+.1f}%）")
    print()

    print("-" * 76)
    print("  (1) 相对本金")
    print("-" * 76)
    if cap > 0:
        print(f"      {eq:,.2f} / {cap:,.2f} - 1 = {(eq/cap-1)*100:>+.2f}%"
              f"   ({'亏' if eq < cap else '赚'} {abs(cap-eq):,.2f} U)")
    else:
        print("      [!] 未找到入金记录，无法算")
    print()

    curve, realized = rebuild_curve(bn)
    print("-" * 76)
    print("  (2) 相对峰值")
    print("-" * 76)
    peaks, src = [], []
    if snaps:
        p_s = max(e for _, e, _, _, _ in snaps)
        peaks.append(p_s)
        src.append(f"本地快照 {len(snaps)} 天（{snaps[0][0]} ~ {snaps[-1][0]}）"
                   f"  峰值 {p_s:,.2f} U")
    if cap > 0 and curve:
        # ⚠️ curve 的累计里【已经包含 TRANSFER】，
        #    所以峰值直接取它的最大值，不要再加 cap（会算两次）
        p_r = max(v for _, v in curve)
        src.append(f"API 流水 {len(curve)} 条重建  峰值 {p_r:,.2f} U")
        peaks.append(p_r)
    for s in src:
        print(f"      - {s}")
    if not peaks:
        print("      [!] 缺少数据，无法算")
        return
    PEAK = max(peaks)
    dd = eq / PEAK - 1
    print(f"      => 取最大值 {PEAK:,.2f} U")
    print(f"      => 回撤 = {eq:,.2f} / {PEAK:,.2f} - 1 = {dd*100:>+.2f}%")
    print()

    print("-" * 76)
    print("  (3) 放在历史里看")
    print("-" * 76)
    print(f"      你的回撤       {dd*100:>+8.2f}%")
    print(f"      历史最坏       {-52.50:>+8.2f}%   还有 {abs(-52.5-dd*100):>5.1f}pp")
    print(f"      -60% 触发线    {-60.00:>+8.2f}%   还有 {abs(-60.0-dd*100):>5.1f}pp")
    print()
    print(f"      若到历史最坏 => 权益 {PEAK*0.475:>7,.2f} U"
          f"（再亏 {eq-PEAK*0.475:>6,.2f} U）")
    print(f"      若到触发线   => 权益 {PEAK*0.40:>7,.2f} U"
          f"（再亏 {eq-PEAK*0.40:>6,.2f} U）")
    print()

    print("-" * 76)
    print("  (4) 数据完整性")
    print("-" * 76)
    print(f"      TRANSFER {len(trows)} 条   全部流水 {len(curve)} 条")
    if not snaps:
        print("      [!] 还没有本地权益记录 => 峰值精度有限")
        print("          建议每天跑 `uv run dd_live.py --snapshot`")
    print()
    print("=" * 76)


if __name__ == "__main__":
    main()
