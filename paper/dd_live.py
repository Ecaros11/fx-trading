"""USDT account DD tool: timestamped equity observations and cash-flow diagnostics.

--history is local/offline. API income has limited retention; visible-window
transfers are never claimed to be lifetime capital. --capital can seed a verified
capital observation. --snapshot retains multiple daily samples. No orders sent.
"""
import argparse
import math
import datetime as dt
import pathlib
import sys
import hashlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from dd_support import (DataError, number, account_values, fetch_income, total, cash_total, CASH_TYPES, wallet_curve,
    load_snapshot_rows, write_snapshot, load_income_cache, cache_income, covered,
    observed_drawdowns, adjusted_nav, threshold_status, DAY)
EQ_LOG = ROOT / "data" / "live" / "equity_log.csv"
INCOME_CACHE = ROOT / "data" / "live" / "dd_income.json"
EXECUTION_LOG = ROOT / 'data' / 'live' / 'execution_ledger.json'
DECISION_LOG = ROOT / 'data' / 'live' / 'ma50_decisions.json'


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
    class DDReadClient(BN):
        def _ts(self):
            # Conservative five-second lead budget avoids future timestamps
            # when proxy request/response latency is asymmetric; survives resync.
            return super()._ts()-5000
    return DDReadClient()


def net_capital(bn):
    """Net USDT transfers in the explicit visible window; not lifetime capital."""
    book=fetch_income(bn)
    rows=[r for r in book['rows'] if r['asset']=='USDT' and r['incomeType'] in CASH_TYPES]
    return cash_total(rows),rows


def rebuild_curve(bn,max_pages=100):
    """Visible-window wallet curve anchored to the current wallet, not zero."""
    _,wallet,_=account_values(bn.futures_account())
    return wallet_curve(fetch_income(bn,max_pages=max_pages),wallet)


def load_snapshots():
    # Legacy tuple adapter retained for callers; the CLI uses timestamped rows.
    return [(r['date'],float(r['equity']),float(r['wallet']),float(r['unrealized']),
             float(r['capital']) if r.get('capital') else None) for r in load_snapshot_rows(EQ_LOG)]


def save_snapshot(eq,wal,unreal,capital=None,now=None,account_scope='',capital_source='user_verified'):
    return write_snapshot(EQ_LOG,eq,wal,unreal,capital,now,account_scope,capital_source)


def percent(value):
    return '数据不足' if value is None else f'{value*100:+.2f}%'


def confirmed_capital(rows,book,now_ms,override=None):
    if override is not None:return number(override,'已核实累计净转入'),'本次用户指定'
    verified=[r for r in rows if r.get('capital_source') in ('user_verified','verified_baseline_plus_transfers') and r.get('capital') and r['_time']<=now_ms]
    if not verified:return None,'历史本金未核实；API近89天流水不能证明累计本金'
    baseline=verified[-1]
    if not covered(book,baseline['_time'],now_ms):return None,'已核实本金之后的划转区间不完整'
    flows=[r for r in book['rows'] if baseline['_time']<r['time']<=now_ms and r['asset']=='USDT']
    if any('TRANSFER' in r['incomeType'] and r['incomeType'] not in CASH_TYPES for r in flows):
        return None,'发现尚未分类的划转类型，无法延续本金'
    return float(baseline['capital'])+cash_total(flows),'已核实快照本金 + 后续USDT转入/转出及兑换资金'


def history_report(rows,book):
    if not rows:print('还没有权益记录。运行 --snapshot 开始采样。');return
    _,drawdowns,_=observed_drawdowns(rows)
    nav,note=adjusted_nav(rows,book)
    print('权益历史（UTC；每行只使用当时及此前的峰值）')
    print('时刻                          权益USDT    钱包USDT   权益采样回撤    净值采样回撤')
    for i,row in enumerate(rows):
        print(f"{row.get('ts') or row['date']:<29} {float(row['equity']):>10.4f} {float(row['wallet']):>10.4f} {percent(drawdowns[i]):>12} {percent(nav[i]['dd'] if nav else None):>14}")
    print('权益采样回撤包含出入金影响；净值为现金流调整的采样近似。')
    print('净值说明：'+note)
    if any(r.get('schema_version')!='2' for r in rows):print('旧记录的账户归属和采样时刻未重新核验，保留为旧版记录。')


def position_report(rows):
    if not isinstance(rows,list):raise DataError('持仓响应不是数组')
    for p in rows:
        amt=number(p['positionAmt'],'持仓数量')
        if abs(amt)<1e-9:continue
        symbol=p['symbol'];entry=number(p['entryPrice'],'开仓价');mark=number(p['markPrice'],'标记价')
        if not isinstance(symbol,str) or mark<=0 or entry<0:raise DataError('持仓价格或标的无效')
        side=p.get('positionSide','BOTH')
        direction='空仓' if side=='SHORT' or (side=='BOTH' and amt<0) else '多仓'
        print(f'持仓 {symbol} {direction} {abs(amt):g}；开仓价 {entry:,.2f}；标记价 {mark:,.2f}；名义 {abs(amt)*mark:,.2f}')
        raw=p.get('liquidationPrice')
        if raw is None or raw=='':print('  强平价：API未提供，不能用简化保证金公式替代');continue
        liquidation=number(raw,'强平价')
        if liquidation<=0:print('  强平价：API没有有效正数，无法计算距离');continue
        print(f'  API强平价 {liquidation:,.2f}；相对标记价 {(liquidation/mark-1)*100:+.2f}%')


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    ap=argparse.ArgumentParser(description='USDT账户采样回撤及现金流诊断；只读接口')
    group=ap.add_mutually_exclusive_group()
    group.add_argument('--snapshot',action='store_true',help='保存本次权益采样，保留同日其他采样')
    group.add_argument('--history',action='store_true',help='离线查看本地权益历史与流水缓存')
    group.add_argument('--ledger',action='store_true',help='同步实际成交、成本和持仓保证金账本，不增加DD权益采样')
    group.add_argument('--ledger-history',action='store_true',help='离线查看实际成交与成本账本')
    ap.add_argument('--capital',type=float,help='用户已核实的当前累计净投入USDT（含兑换资金）；不自动把近89天流水当累计本金')
    a=ap.parse_args()
    if a.capital is not None and (a.history or a.ledger_history or a.ledger or not math.isfinite(a.capital)):
        ap.error('capital须为有限数，只用于默认诊断或snapshot，不适用于history或ledger模式')
    try:
        if a.ledger_history:
            from execution_ledger import load_ledger, print_summary
            ledger=load_ledger(EXECUTION_LOG)
            if ledger is None: print('还没有实际成交账本；运行 --snapshot 或 --ledger 建立记录。')
            else: print_summary(ledger)
            return 0
        if a.history:
            history_report(load_snapshot_rows(EQ_LOG),load_income_cache(INCOME_CACHE));return 0
        bn=bn_api()
        sample_start_ms=int(bn.fapi('/fapi/v1/time',signed=False)['serverTime'])
        ok,acct,err=api_retry(bn.futures_account,what='读账户')
        if not ok:raise DataError('账户读取失败：'+err)
        eq,wallet,unreal=account_values(acct)
        end_ms=int(bn.fapi('/fapi/v1/time',signed=False)['serverTime'])
        now=dt.datetime.fromtimestamp(end_ms/1000,dt.UTC)
        scope=hashlib.sha256(bn.key.encode()).hexdigest()[:24]
        rows=load_snapshot_rows(EQ_LOG,scope)
        if rows and rows[-1]['_time']>end_ms:raise DataError('本地权益记录晚于当前服务器时刻')
        # Never cache or claim a partial history after a paging/network failure.
        ok,fresh,err=api_retry(lambda:fetch_income(bn,end_ms=end_ms),what='读取完整可见流水')
        if not ok:raise DataError('流水读取不完整：'+err)
        book=cache_income(INCOME_CACHE,fresh,scope)
        capital,capital_note=confirmed_capital(rows,book,end_ms,a.capital)
        current={'date':now.date().isoformat(),'ts':now.isoformat(),'equity':str(eq),
                 'wallet':str(wallet),'unrealized':str(unreal),'_time':end_ms,'schema_version':'2'}
        sample_rows=[r for r in rows if r['_time']<end_ms]+[current]
        peak,_,dd=observed_drawdowns(sample_rows,eq)
        nav,nav_note=adjusted_nav(sample_rows,book)
        curve,ending=wallet_curve(fresh,wallet)
        wallet_peak=max(wallet,max(v for _,v in curve));wallet_dd=wallet/wallet_peak-1 if wallet_peak>0 else None
        print('='*72)
        print(f'USDT账户回撤诊断  {now:%Y-%m-%d %H:%M:%S} UTC')
        print(f'当前权益 {eq:,.4f}；钱包 {wallet:,.4f}；未实现盈亏 {unreal:+,.4f} USDT')
        print('账户、流水及持仓为多次只读请求，并非原子账户快照。')
        print()
        print('本金与现金流')
        print(f"  已完整分页区间 {dt.datetime.fromtimestamp(fresh['start']/1000,dt.UTC):%Y-%m-%d} ~ {now:%Y-%m-%d}；{len(fresh['rows'])}条流水")
        print(f"  该区间USDT净转入 {total(fresh['rows'],'TRANSFER'):,.4f}（不是已证明的累计本金）")
        print(f"  该区间USDT净资金投入 {cash_total(fresh['rows']):,.4f}（含USDT兑换资金，仍不是已证明的累计本金）")
        other=sorted({r['asset'] for r in fresh['rows'] if r['asset']!='USDT'})
        if other:print('  其他币种未与USDT金额直接相加：'+', '.join(other))
        if capital is None:print('  相对累计本金：无法核实；'+capital_note)
        else:
            print(f'  已核实累计净投入 {capital:,.4f} USDT（{capital_note}）')
            print(f'  相对本金盈亏 {eq-capital:+,.4f} USDT；比例 {percent(eq/capital-1) if capital>0 else "本金非正，不计算比例"}')
        print()
        print('回撤口径')
        print(f'  权益采样峰值 {peak:,.4f} USDT；权益采样回撤 {percent(dd)}（含出入金影响）')
        print(f'  净值采样回撤 {percent(nav[-1]["dd"] if nav else None)}；'+nav_note)
        print(f'  近89天钱包参考峰值 {wallet_peak:,.4f} USDT；钱包回撤 {percent(wallet_dd)}（不含历史浮盈，含划转）')
        print('  钱包峰值与权益峰值分开计算；没有记录的历史浮盈峰值无法恢复。')
        if any(r.get('schema_version')!='2' for r in rows):print('  旧快照的账户归属/采样时刻未核验；旧capital不作为已确认本金。')
        if dd is not None and peak>0:
            line=threshold_status(eq,peak)
            state='已达到或超过' if line['breached'] else '尚未达到'
            print(f"  权益采样-60%观察线（不自动操作）：{state}；参考权益 {line['target_equity']:,.4f} USDT；差额 {line['amount_above_line']:+,.4f} USDT，{line['percentage_points']:+.2f}pp")
        print()
        ok,positions,err=api_retry(bn.positions,what='读取持仓')
        status=0
        if ok:
            try:position_report(positions)
            except (ValueError,KeyError,TypeError) as error:print(f'持仓诊断不可用：{error}');status=1
        else:print('持仓诊断读取失败：'+err);status=1
        if a.snapshot:
            n=save_snapshot(eq,wallet,unreal,capital,now,scope,
                'user_verified' if a.capital is not None else 'verified_baseline_plus_transfers')
            print(f'已保存本次采样：{EQ_LOG}（共{n}次，不覆盖同日较早采样）')
        if a.snapshot or a.ledger:
            if not ok:
                print('实际成交账本未同步：持仓采样失败。');status=1
            else:
                try:
                    from execution_ledger import normalize_snapshot, sync_ledger, print_summary
                    sample_end_ms=int(bn.fapi('/fapi/v1/time',signed=False)['serverTime'])
                    snapshot=normalize_snapshot(acct,positions,sample_start_ms,sample_end_ms)
                    ledger=sync_ledger(EXECUTION_LOG,bn,scope,book,snapshot,DECISION_LOG)
                    print_summary(ledger)
                    print(f'实际成交账本已保存：{EXECUTION_LOG}')
                except Exception as error:
                    print(f'实际成交账本同步失败：{type(error).__name__}: {error}');status=1
        print('权益采样未捕获日内全部峰值；现金流调整是采样近似，不能据此证明没有强平风险。')
        return status
    except Exception as error:
        print(f'DD检查失败，结果不可验证：{type(error).__name__}: {error}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
