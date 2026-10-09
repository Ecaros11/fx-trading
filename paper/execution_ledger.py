"""Read-only ETHUSDT execution evidence, exact costs, and isolated-margin samples."""
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import pathlib
import re
import uuid
from dd_support import (DataError, DAY, timestamp, account_values, normalize_income,
                        file_lock, atomic_write)

SYMBOL = 'ETHUSDT'
RETENTION = 89 * DAY
OVERLAP = 3 * DAY
ASSET = re.compile(r'^[A-Z0-9_]{1,30}$')

def amount(value, name, minimum=None):
    if isinstance(value, bool):
        raise DataError(name+'不能是布尔值')
    try:
        x = Decimal(str(value))
    except InvalidOperation:
        raise DataError(name+'不是数值') from None
    if not x.is_finite() or (minimum is not None and x < minimum):
        raise DataError(name+'无效')
    return '0' if x == 0 else format(x.normalize(), 'f')

def integer(value, name):
    x = Decimal(amount(value, name, 0))
    if x != x.to_integral_value():
        raise DataError(name+'必须为整数')
    return int(x)

def asset(value):
    if not isinstance(value, str) or not ASSET.fullmatch(value):
        raise DataError('币种或标的无效')
    return value

def iso(t):
    return dt.datetime.fromtimestamp(t / 1000, dt.UTC).isoformat().replace('+00:00','Z')

def ranges(values):
    merged = []
    for a, b in sorted(values):
        a, b = timestamp(a), timestamp(b)
        if a > b:
            raise DataError('账本覆盖区间倒序')
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a,b])
    return merged

def merge_rows(rows, keys, name):
    result = {}
    for row in rows:
        key = tuple(row[k] for k in keys)
        if key in result and row != result[key]:
            raise DataError('同一'+name+'标识有冲突；保留旧账本')
        result[key] = row
    return list(result.values())

def normalize_trades(rows, start=None, end=None):
    if not isinstance(rows, list):
        raise DataError('成交响应不是数组')
    out = []
    for r in rows:
        if not isinstance(r, dict) or r.get('symbol') != SYMBOL:
            raise DataError('成交标的不是ETHUSDT或格式无效')
        t = integer(r['time'],'成交时间')
        if start is not None and not start <= t <= end:
            raise DataError('成交时间超出请求区间')
        if r.get('side') not in ('BUY','SELL') or r.get('positionSide') not in ('BOTH','LONG','SHORT'):
            raise DataError('成交方向无效')
        if type(r.get('maker')) is not bool or type(r.get('buyer')) is not bool:
            raise DataError('成交maker/buyer状态无效')
        if r['buyer'] != (r['side']=='BUY'):
            raise DataError('成交买卖字段不一致')
        p=amount(r['price'],'成交价格',0);q=amount(r['qty'],'成交数量',0)
        if Decimal(p)<=0 or Decimal(q)<=0:
            raise DataError('成交价格和数量必须为正')
        quote=amount(r['quoteQty'],'成交名义',0)
        # Binance rounds quoteQty; allow one cent, never substitute a fabricated fill.
        if abs(Decimal(quote)-Decimal(p)*Decimal(q)) > max(Decimal('.01'),Decimal(quote)*Decimal('0.000001')):
            raise DataError('成交名义与价格数量不一致')
        if r.get('marginAsset','USDT') != 'USDT':
            raise DataError('ETHUSDT保证金币种不是USDT')
        out.append({'symbol':SYMBOL,'id':integer(r['id'],'成交ID'),
          'orderId':integer(r['orderId'],'订单ID'),'time':t,'side':r['side'],
          'positionSide':r['positionSide'],'price':p,'qty':q,'quoteQty':quote,
          'commission':amount(r['commission'],'成交手续费'),
          'commissionAsset':asset(r['commissionAsset']),
          'realizedPnl':amount(r['realizedPnl'],'已实现盈亏'),'maker':r['maker'],'buyer':r['buyer']})
    out=merge_rows(out,('symbol','id'),'成交')
    return sorted(out,key=lambda r:(r['time'],r['id']))

def fetch_trades(bn, start, end, limit=1000, max_calls=1000):
    """Bisect full time windows; never advance by last timestamp and lose same-ms fills."""
    start,end=timestamp(start),timestamp(end)
    if start>end or end-start>RETENTION or not 1<=limit<=1000 or max_calls<1:
        raise DataError('成交查询窗口或保护参数无效')
    pending=[]
    for a in range(start,end+1,7*DAY):
        pending.append((a,min(end,a+7*DAY-1)))
    rows=[];calls=0
    while pending:
        a,b=pending.pop()
        calls+=1
        if calls>max_calls:
            raise DataError('成交分页达到保护上限，不能视为完整')
        batch=bn.user_trades(symbol=SYMBOL,limit=limit,startTime=a,endTime=b)
        if not isinstance(batch,list) or len(batch)>limit:
            raise DataError('成交分页响应无效')
        clean=normalize_trades(batch,a,b)
        if len(batch)==limit:
            if a==b:
                raise DataError('同一毫秒成交达到接口上限，不能证明完整；请用交易所导出核对')
            mid=(a+b)//2
            pending.extend(((a,mid),(mid+1,b)))
        else:
            rows.extend(clean)
    return {'rows':normalize_trades(rows),'ranges':[[start,end]],'calls':calls,'complete':True}

def normalize_snapshot(acct, positions, start, end):
    eq,wallet,unreal=account_values(acct)
    if not isinstance(positions,list):
        raise DataError('持仓快照不是数组')
    own=[p for p in positions if p.get('symbol')==SYMBOL]
    sides=[p.get('positionSide') for p in own]
    if set(sides) not in ({'BOTH'},{'LONG','SHORT'}) or len(sides)!=len(set(sides)):
        raise DataError('ETHUSDT持仓响应缺失、重复或模式无效')
    fields=('positionAmt','entryPrice','markPrice','liquidationPrice','isolatedWallet','isolatedMargin','unRealizedProfit')
    normalized=[]
    for p in own:
        row={'symbol':SYMBOL,'positionSide':p['positionSide']}
        for f in fields:
            row[f]=amount(p[f],f,0 if f in ('entryPrice','markPrice','liquidationPrice') else None)
        q=Decimal(row['positionAmt'])
        if row['positionSide']=='LONG' and q<0 or row['positionSide']=='SHORT' and q>0:
            raise DataError('持仓方向与数量符号不一致')
        if Decimal(row['markPrice'])<=0 or q and Decimal(row['entryPrice'])<=0:
            raise DataError('持仓价格无效')
        if p.get('marginType') not in ('isolated','cross'):
            raise DataError('持仓保证金模式未知')
        row['marginType']=p['marginType'];row['leverage']=integer(p['leverage'],'杠杆')
        if row['leverage']<1:
            raise DataError('持仓杠杆无效')
        auto=p.get('isAutoAddMargin')
        if auto not in (True,False,'true','false'):
            raise DataError('自动追加保证金状态未知')
        row['isAutoAddMargin']=auto is True or auto=='true'
        row['updateTime']=integer(p['updateTime'],'持仓更新时间')
        if row['marginType']=='isolated':
            error=Decimal(row['isolatedMargin'])-Decimal(row['isolatedWallet'])-Decimal(row['unRealizedProfit'])
            if abs(error)>max(Decimal('.0001'),abs(Decimal(row['isolatedMargin']))*Decimal('.000001')):
                raise DataError('逐仓权益与逐仓钱包及浮盈不一致')
        normalized.append(row)
    return {'id':uuid.uuid4().hex,'observed_start_ms':timestamp(start),'observed_end_ms':timestamp(end),
            'account':{'equity':amount(acct['totalMarginBalance'],'权益'),
                       'wallet':amount(acct['totalWalletBalance'],'钱包'),
                       'unrealized':amount(acct['totalUnrealizedProfit'],'浮盈'),
                       'available':amount(acct['availableBalance'],'可用余额',0)},
            'positions':sorted(normalized,key=lambda p:p['positionSide']),
            'atomic':False}

def empty(scope):
    return {'schema_version':1,'symbol':SYMBOL,'account_scope':scope,'trades':[],'income':[],
            'trade_ranges':[],'income_ranges':[],'snapshots':[],'runs':[]}

def validate(book, scope=None):
    if not isinstance(book,dict) or book.get('schema_version')!=1 or book.get('symbol')!=SYMBOL:
        raise DataError('成交账本版本或标的无效')
    if not isinstance(book.get('account_scope'),str) or not book['account_scope']:
        raise DataError('成交账本账户归属未知')
    if scope is not None and book['account_scope']!=scope:
        raise DataError('成交账本凭证归属变化，不能混用')
    book['trades']=normalize_trades(book['trades']);book['income']=normalize_income(book['income'])
    for row in book['income']:
        asset(row['asset']);asset(row['incomeType'])
        if row.get('symbol'):
            asset(row['symbol'])
        if row.get('tradeId') not in (None,''):
            integer(row['tradeId'],'流水成交ID')
    for field,rows in (('trade_ranges',book['trades']),('income_ranges',book['income'])):
        book[field]=ranges(book[field])
        if any(not any(a<=row['time']<=b for a,b in book[field]) for row in rows):
            raise DataError('账本记录超出已查询区间')
    for field in ('snapshots','runs'):
        if not isinstance(book[field],list):
            raise DataError('账本快照或运行历史无效')
        if len({x['id'] for x in book[field]})!=len(book[field]):
            raise DataError('账本快照或运行ID重复')
    for snap in book['snapshots']:
        start,end=timestamp(snap['observed_start_ms']),timestamp(snap['observed_end_ms'])
        if start>end or snap.get('atomic') is not False:
            raise DataError('快照时段或原子性标记无效')
        ac=snap['account']
        # Validate every persisted position, not only new API samples.
        normalize_snapshot({'multiAssetsMargin':False,'totalMarginBalance':ac['equity'],
            'totalWalletBalance':ac['wallet'],'totalUnrealizedProfit':ac['unrealized'],
            'availableBalance':ac['available']},snap['positions'],start,end)
        if abs(Decimal(amount(ac['equity'],'快照权益'))-Decimal(amount(ac['wallet'],'快照钱包'))-Decimal(amount(ac['unrealized'],'快照浮盈')))>Decimal('.0001'):
            raise DataError('账本快照权益不一致')
    return book

def load_ledger(path, scope=None):
    path=pathlib.Path(path)
    if not path.exists():
        return None
    return validate(json.loads(path.read_text(encoding='utf-8-sig')),scope)

def sync_ledger(path,bn,scope,income_book,snapshot,decisions_path=None):
    """All fetching/validation completes before the canonical ledger's atomic commit."""
    if not scope or income_book.get('complete') is not True or income_book.get('account_scope',scope)!=scope:
        raise DataError('账户归属或流水完整性不可验证')
    end=timestamp(income_book['end']);floor=max(0,end-RETENTION)
    old=load_ledger(path,scope)
    last=max((b for a,b in old['trade_ranges']),default=floor) if old else floor
    if last>end:
        raise DataError('账本已查询时刻晚于本次服务器截止')
    start=max(floor,last-OVERLAP) if old else floor
    fresh=fetch_trades(bn,start,end)
    income=normalize_income(income_book['rows'])
    iranges=ranges(income_book['ranges'])
    if any(not any(a<=r['time']<=b for a,b in iranges) for r in income):
        raise DataError('新流水超出完整分页区间')
    if snapshot['observed_start_ms']>snapshot['observed_end_ms']:
        raise DataError('账户采样时段倒序')
    with file_lock(path):
        current=load_ledger(path,scope) or empty(scope)
        old_count=len(current['trades'])
        current['trades']=normalize_trades(current['trades']+fresh['rows'])
        current['income']=normalize_income(current['income']+income)
        current['trade_ranges']=ranges(current['trade_ranges']+fresh['ranges'])
        current['income_ranges']=ranges(current['income_ranges']+iranges)
        current['snapshots'].append(snapshot)
        current['snapshots'].sort(key=lambda x:(x['observed_end_ms'],x['id']))
        current['runs'].append({'id':uuid.uuid4().hex,'cutoff_ms':end,
           'trade_query_start':start,'trade_query_end':end,'trade_calls':fresh['calls'],
           'new_trades':len(current['trades'])-old_count,
           'snapshot_id':snapshot['id']})
        validate(current,scope)
        atomic_write(path,json.dumps(current,ensure_ascii=False,indent=2))
        # Derived exports can be regenerated; JSON is the sole authoritative commit.
        try:
            export_ledger(path,current,decisions_path)
        except Exception as error:
            raise DataError('完整JSON账本已保存，但CSV导出失败；重跑可恢复：'+str(error)) from error
    return current

def write_csv(path, rows, fields):
    buf=io.StringIO(newline='')
    w=csv.DictWriter(buf,fieldnames=fields);w.writeheader()
    w.writerows({k:r.get(k,'') for k in fields} for r in rows)
    atomic_write(path,buf.getvalue())

def load_decisions(path):
    path=pathlib.Path(path)
    if not path.exists():
        return []
    book=json.loads(path.read_text(encoding='utf-8-sig'))
    if book.get('schema_version')!=1 or not isinstance(book.get('records'),list):
        raise DataError('决策记录损坏或版本未知')
    ids=set()
    for row in book['records']:
        ident=row['decision_id']
        if not isinstance(ident,str) or not ident or ident in ids:
            raise DataError('决策ID缺失或重复')
        ids.add(ident)
        timestamp(row['decision_ms'])
    return book['records']

def append_decision(path, record):
    row=dict(record)
    # Legacy imports lack a server-time/key-bound decision; never fabricate attribution.
    if not row.get('decision_id') or not row.get('decision_ms'):
        return
    timestamp(row['decision_ms'])
    if row.get('reference_price'):
        amount(row['reference_price'],'决策参考价',0)
    with file_lock(path):
        rows=load_decisions(path)
        hit=next((x for x in rows if x['decision_id']==row['decision_id']),None)
        if hit is not None and hit!=row:
            raise DataError('同一决策ID内容冲突')
        if hit is None:
            rows.append(row)
        rows.sort(key=lambda x:(int(x['decision_ms']),x['decision_id']))
        atomic_write(path,json.dumps({'schema_version':1,'records':rows},ensure_ascii=False,indent=2))

def candidate(fill, decisions, scope):
    # Informational time/side candidate, never proof that a manual order followed advice.
    prior=[d for d in decisions if d.get('account_scope')==scope and d.get('symbol')==SYMBOL
           and 0<=fill['time']-int(d['decision_ms'])<=DAY]
    if not prior:
        return {}
    latest=max(prior,key=lambda d:(int(d['decision_ms']),d['decision_id']))
    if latest.get('validation_status')!='order' or latest.get('side')!=fill['side'] or latest.get('position_side')!=fill['positionSide']:
        return {}
    ref=Decimal(amount(latest['reference_price'],'建议参考价',0))
    if ref<=0:
        return {}
    drift=(Decimal(fill['price'])/ref-1)*10000
    if fill['side']=='SELL':
        drift=-drift
    return {'candidate_decision_id':latest['decision_id'],'reference_price':str(ref),
            'adverse_price_difference_bps':amount(drift,'价格差'),
            'candidate_status':'time_side_only_not_verified'}

def export_ledger(path, book, decisions_path=None):
    directory=pathlib.Path(path).parent
    decisions=load_decisions(decisions_path) if decisions_path else []
    trade_rows=[dict(r,time_utc=iso(r['time']),**candidate(r,decisions,book['account_scope'])) for r in book['trades']]
    fields=['time_utc','symbol','id','orderId','side','positionSide','qty','price','quoteQty','commission','commissionAsset','realizedPnl','maker',
            'candidate_decision_id','reference_price','adverse_price_difference_bps','candidate_status']
    write_csv(directory/'execution_trades.csv',trade_rows,fields)
    cost_rows=[]
    for r in book['income']:
        cost_rows.append(dict(r,time_utc=iso(r['time'])))
    write_csv(directory/'execution_income.csv',cost_rows,['time_utc','symbol','incomeType','asset','income','tranId','tradeId'])
    snapshots=[]
    for sample in book['snapshots']:
        for p in sample['positions']:
            snapshots.append(dict(p,snapshot_id=sample['id'],observed_start_utc=iso(sample['observed_start_ms']),
                                  observed_end_utc=iso(sample['observed_end_ms']),**sample['account']))
    write_csv(directory/'execution_snapshots.csv',snapshots,['snapshot_id','observed_start_utc','observed_end_utc','equity','wallet','unrealized','available',
             'symbol','positionSide','positionAmt','entryPrice','markPrice','liquidationPrice','isolatedWallet','isolatedMargin','marginType','leverage','isAutoAddMargin'])

def sums(rows, amount_key, asset_key, predicate=lambda r:True):
    out={}
    for r in rows:
        if predicate(r):
            a=r[asset_key];out[a]=out.get(a,Decimal(0))+Decimal(r[amount_key])
    return {k:amount(v,'合计') for k,v in sorted(out.items())}

def summary(book):
    trades=book['trades'];income=book['income'];D=Decimal
    fees=sums(trades,'commission','commissionAsset')
    funding=sums(income,'income','asset',lambda r:r.get('symbol')==SYMBOL and r['incomeType']=='FUNDING_FEE')
    realized=sum((D(t['realizedPnl']) for t in trades),D(0))
    joint=ranges([[max(a,c),min(b,d)] for a,b in book['trade_ranges'] for c,d in book['income_ranges'] if max(a,c)<=min(b,d)])
    inside=lambda t:any(a<=t<=b for a,b in joint)
    netfills=[t for t in trades if inside(t['time'])]
    netrealized=sum((D(t['realizedPnl']) for t in netfills),D(0))
    netfees=sums(netfills,'commission','commissionAsset')
    netfund=sums(income,'income','asset',lambda x:x.get('symbol')==SYMBOL and x['incomeType']=='FUNDING_FEE' and inside(x['time']))
    trade_fee=D(netfees.get('USDT','0'))
    funding_usdt=D(netfund.get('USDT','0'))
    other=sums(income,'income','asset',lambda r:r.get('symbol')==SYMBOL and r['incomeType'] not in ('COMMISSION','REALIZED_PNL','FUNDING_FEE'))
    reconciliation=[]
    for a,b in book['trade_ranges']:
        for c,d in book['income_ranges']:
            lo,hi=max(a,c),min(b,d)
            if lo>hi:
                continue
            ft=[t for t in trades if lo<=t['time']<=hi]
            ir=[x for x in income if lo<=x['time']<=hi and x.get('symbol')==SYMBOL]
            f=sums(ft,'commission','commissionAsset')
            c=sums(ir,'income','asset',lambda x:x['incomeType']=='COMMISSION')
            dif={a:amount(D(f.get(a,'0'))+D(c.get(a,'0')),'手续费对账差额') for a in sorted(set(f)|set(c))}
            pnl=sum((D(x['income']) for x in ir if x['incomeType']=='REALIZED_PNL' and x['asset']=='USDT'),D(0))
            fillpnl=sum((D(x['realizedPnl']) for x in ft),D(0))
            reconciliation.append({'start':lo,'end':hi,'commission_difference_by_asset':dif,
                                    'realized_pnl_difference_usdt':amount(fillpnl-pnl,'盈亏对账差额')})
    return {'fills':len(trades),'orders':len({t['orderId'] for t in trades}),'commission_by_asset':fees,'funding_income_by_asset':funding,
            'fill_realized_pnl_usdt':amount(realized,'已实现盈亏'),
            'net_realized_usdt':amount(netrealized-trade_fee+funding_usdt,'已实现净额') if joint else None,
            'net_ranges':joint,'net_components':{'realized':amount(netrealized,'盈亏'),'commission_usdt':amount(trade_fee,'费用'),'funding_income_usdt':amount(funding_usdt,'资金费')},
            'other_eth_income_by_asset':other,'reconciliation':reconciliation,
            'trade_ranges':book['trade_ranges'],'income_ranges':book['income_ranges'],
            'net_note':'净额仅在成交与流水共同覆盖区间计算：已实现盈亏－USDT成交手续费＋ETH资金费；不含浮盈亏、其他收入或非USDT手续费，不是账户总收益'}

def print_summary(book):
    x=summary(book)
    print('\nETHUSDT实际成交与成本账本')
    print(f"  {x['fills']}笔成交 / {x['orders']}个订单；{len(book['snapshots'])}次持仓保证金采样")
    print(f"  已实现盈亏 {x['fill_realized_pnl_usdt']} USDT")
    print('  实际手续费（正数为扣费）：'+json.dumps(x['commission_by_asset'],ensure_ascii=False))
    print('  资金费收入（负数为支付）：'+json.dumps(x['funding_income_by_asset'],ensure_ascii=False))
    print(f"  共同覆盖区间已实现净额 {x['net_realized_usdt']} USDT" if x['net_ranges'] else '  无共同查询覆盖，净额无法计算')
    print('  净额组成：'+json.dumps(x['net_components'],ensure_ascii=False))
    for c in x['reconciliation']:
        ok=all(abs(Decimal(v))<=Decimal('.00001') for v in c['commission_difference_by_asset'].values()) and abs(Decimal(c['realized_pnl_difference_usdt']))<=Decimal('.00001')
        print(f"  流水对账 {iso(c['start'])}～{iso(c['end'])}："+('一致' if ok else '存在差额，请核查')+
              '；手续费差额 '+json.dumps(c['commission_difference_by_asset'],ensure_ascii=False)+
              '；已实现盈亏差额 '+c['realized_pnl_difference_usdt']+' USDT')
    if len(x['trade_ranges'])>1 or len(x['income_ranges'])>1:
        print('  查询覆盖存在断档，未把缺口当作零交易或零费用。')
    for label,rs in (('成交',book['trade_ranges']),('流水',book['income_ranges'])):
        print('  '+label+'覆盖：'+', '.join(iso(a)+'～'+iso(b) for a,b in rs))
    if any(a!='USDT' and Decimal(v) for a,v in x['commission_by_asset'].items()):
        print('  有非USDT手续费，未兑换，USDT净额不能视为完整成本净收益。')
    if x['other_eth_income_by_asset']:
        print('  另有ETHUSDT其他类型流水：'+json.dumps(x['other_eth_income_by_asset'],ensure_ascii=False)+'；未混入上述净额。')
    print('  '+x['net_note'])
    print('  查询覆盖为API已完整请求区间，不证明是全部账户历史；快照不是原子快照。')

