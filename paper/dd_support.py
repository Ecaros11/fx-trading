"""Validated DD data, cash-flow accounting and atomic local storage (stdlib only)."""
import csv
import datetime as dt
from decimal import Decimal
import io
import json
import math
import os
import pathlib
import tempfile
import time
from contextlib import contextmanager

DAY=86400000
CASH_TYPES={'TRANSFER','COIN_SWAP_DEPOSIT','COIN_SWAP_WITHDRAW','AUTO_EXCHANGE'}

class DataError(ValueError):pass

def number(value,name):
    if isinstance(value,bool):raise DataError(f'{name}不能是布尔值')
    value=float(value)
    if not math.isfinite(value):raise DataError(f'{name}不是有限数')
    return value

def timestamp(value):
    t=number(value,'时间')
    if t<0 or not t.is_integer():raise DataError('时间必须是非负整数毫秒')
    return int(t)

def account_values(acct):
    if not isinstance(acct,dict) or acct.get('multiAssetsMargin') is not False:
        raise DataError('账户资产模式未知或为多资产模式，不能按USDT口径计算')
    eq=number(acct['totalMarginBalance'],'权益');wallet=number(acct['totalWalletBalance'],'钱包')
    unreal=number(acct['totalUnrealizedProfit'],'未实现盈亏')
    if abs(eq-wallet-unreal)>max(.00002,abs(eq)*1e-9):raise DataError('账户权益不等于钱包加未实现盈亏')
    return eq,wallet,unreal

def normalize_income(rows,start=None,end=None):
    if not isinstance(rows,list):raise DataError('流水响应不是数组')
    result={}
    for row in rows:
        if not isinstance(row,dict):raise DataError('流水记录格式无效')
        t=timestamp(row['time']);value=number(row['income'],'流水金额')
        kind=row['incomeType'];asset=row['asset'];identity=str(row['tranId'])
        if not isinstance(kind,str) or not kind or not isinstance(asset,str) or not asset or identity in ('','None'):
            raise DataError('流水缺少类型、币种或唯一标识')
        if start is not None and not start<=t<=end:raise DataError('流水时间超出请求区间')
        amount=Decimal(str(row['income']));text=format(amount,'f')
        if '.' in text:text=text.rstrip('0').rstrip('.')
        normalized=dict(row,time=t,income='0' if amount==0 else text,tranId=identity)
        key=(kind,identity)
        if key in result and result[key]!=normalized:raise DataError('同一流水标识有冲突内容')
        result[key]=normalized
    return sorted(result.values(),key=lambda r:(r['time'],r['incomeType'],r['tranId']))

def fetch_income(bn,end_ms=None,start_ms=None,max_pages=100,limit=1000):
    if end_ms is None:end_ms=int(bn.fapi('/fapi/v1/time',signed=False)['serverTime'])
    end_ms=timestamp(end_ms);start_ms=max(0,end_ms-89*DAY) if start_ms is None else timestamp(start_ms)
    if start_ms>end_ms or end_ms-start_ms>89*DAY:raise DataError('流水窗口须在89天内，不能声称超过接口保留历史')
    if max_pages<1 or limit<1 or limit>1000:raise DataError('流水分页参数无效')
    rows=[];seen=set()
    # A fixed window and page cursor retain events sharing the same millisecond.
    for page in range(1,max_pages+1):
        batch=bn.fapi('/fapi/v1/income',{'startTime':start_ms,'endTime':end_ms,'page':page,'limit':limit},signed=True)
        if not isinstance(batch,list) or len(batch)>limit:raise DataError('流水分页响应无效')
        normalized=normalize_income(batch,start_ms,end_ms)
        ids={(r['incomeType'],r['tranId']) for r in normalized}
        if batch and not ids-seen:raise DataError('流水分页未前进，不能视为完整')
        rows.extend(normalized);seen.update(ids)
        if len(batch)<limit:
            return {'rows':normalize_income(rows),'ranges':[[start_ms,end_ms]],'start':start_ms,'end':end_ms,'complete':True}
    raise DataError('流水达到分页保护上限，不能视为完整')

def total(rows,kind=None):
    return number(float(sum((Decimal(r['income']) for r in rows if r['asset']=='USDT' and (kind is None or r['incomeType']==kind)),Decimal(0))),'流水合计')

def cash_total(rows):
    return number(float(sum((Decimal(r['income']) for r in rows if r['asset']=='USDT' and r['incomeType'] in CASH_TYPES),Decimal(0))),'USDT资金净投入合计')

def wallet_curve(book,ending_wallet):
    ending_wallet=number(ending_wallet,'钱包');rows=[r for r in book['rows'] if r['asset']=='USDT' and book['start']<=r['time']<=book['end']]
    cash=Decimal(str(ending_wallet))-sum((Decimal(r['income']) for r in rows),Decimal(0))
    curve=[(book['start']-1,number(float(cash),'重建钱包余额'))];groups={}
    for row in rows:groups[row['time']]=groups.get(row['time'],Decimal(0))+Decimal(row['income'])
    for t,value in sorted(groups.items()):cash+=value;curve.append((t,number(float(cash),'重建钱包余额')))
    return curve,float(cash)

@contextmanager
def file_lock(path,timeout=10):
    path=pathlib.Path(str(path)+'.lock');path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        if f.tell()==0:f.write(b'0');f.flush()
        deadline=time.monotonic()+timeout
        while True:
            try:
                f.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>=deadline:raise TimeoutError('DD记录写入锁超时')
                time.sleep(.05)
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f,fcntl.LOCK_UN)

def atomic_write(path,text):
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8-sig',newline='') as f:f.write(text);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def load_snapshot_rows(path,account_scope=None):
    path=pathlib.Path(path)
    if not path.exists():return []
    with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    seen=set()
    for row in rows:
        day=dt.date.fromisoformat(row['date'])
        raw=row.get('ts') or row['date']+'T00:00:00Z'
        clock=dt.datetime.fromisoformat(raw.replace('Z','+00:00'))
        if clock.tzinfo is None:raise DataError('权益快照时区未知')
        clock=clock.astimezone(dt.UTC)
        if clock.date()!=day:raise DataError('权益快照日期与时刻不一致')
        key=clock.isoformat()
        if key in seen:raise DataError('权益快照时刻重复')
        seen.add(key)
        e=number(row['equity'],'快照权益');w=number(row['wallet'],'快照钱包');u=number(row['unrealized'],'快照未实现盈亏')
        if abs(e-w-u)>max(.00002,abs(e)*1e-9):raise DataError('快照权益与钱包及浮盈不一致')
        if row.get('capital'):number(row['capital'],'快照本金')
        if account_scope and row.get('account_scope') and row['account_scope']!=account_scope:
            raise DataError('权益快照属于不同账户凭证，不能混用')
        row['_time']=clock.timestamp()*1000
    return sorted(rows,key=lambda r:r['_time'])

def write_snapshot(path,eq,wallet,unreal,capital=None,now=None,account_scope='',capital_source='user_verified'):
    eq=number(eq,'权益');wallet=number(wallet,'钱包');unreal=number(unreal,'未实现盈亏')
    if abs(eq-wallet-unreal)>max(.00002,abs(eq)*1e-9):raise DataError('快照输入权益不一致')
    if capital is not None:capital=number(capital,'已核实本金')
    now=dt.datetime.now(dt.UTC) if now is None else now
    if now.tzinfo is None:raise DataError('快照时区未知')
    now=now.astimezone(dt.UTC)
    row={'date':now.date().isoformat(),'ts':now.isoformat().replace('+00:00','Z'),
         'equity':f'{eq:.6f}','wallet':f'{wallet:.6f}','unrealized':f'{unreal:.6f}',
         'capital':'' if capital is None else f'{capital:.6f}',
         'capital_source':'unknown' if capital is None else capital_source,
         'schema_version':'2','account_scope':account_scope}
    with file_lock(path):
        rows=load_snapshot_rows(path,account_scope)
        rows=[r for r in rows if r.get('ts')!=row['ts']]+[dict(row,_time=now.timestamp()*1000)]
        rows.sort(key=lambda r:r['_time'])
        keys=['date','ts','equity','wallet','unrealized','capital','capital_source','schema_version','account_scope']
        keys+=sorted(set().union(*(r.keys() for r in rows))-set(keys)-{'_time'})
        buf=io.StringIO(newline='');w=csv.DictWriter(buf,fieldnames=keys);w.writeheader()
        w.writerows({k:v for k,v in r.items() if k!='_time'} for r in rows)
        atomic_write(path,buf.getvalue())
    return len(rows)

def load_income_cache(path,account_scope=None):
    path=pathlib.Path(path)
    if not path.exists():return None
    book=json.loads(path.read_text(encoding='utf-8-sig'))
    if book.get('schema_version')!=1 or book.get('complete') is not True:raise DataError('DD流水缓存版本或完整性未知')
    if account_scope and book.get('account_scope')!=account_scope:raise DataError('DD流水缓存账户凭证变化，不能混用')
    book['rows']=normalize_income(book['rows'])
    for a,b in book['ranges']:
        if timestamp(a)>timestamp(b):raise DataError('流水缓存覆盖区间无效')
    if any(not any(a<=r['time']<=b for a,b in book['ranges']) for r in book['rows']):raise DataError('流水记录超出已覆盖区间')
    return book

def cache_income(path,book,account_scope):
    if book.get('complete') is not True:raise DataError('不得保存未完成分页的流水')
    with file_lock(path):
        old=load_income_cache(path,account_scope)
        combined=normalize_income((old['rows'] if old else [])+book['rows'])
        ranges=sorted((old['ranges'] if old else [])+book['ranges']);merged=[]
        for a,b in ranges:
            if merged and a<=merged[-1][1]+1:merged[-1][1]=max(b,merged[-1][1])
            else:merged.append([a,b])
        value=dict(book,rows=combined,ranges=merged,account_scope=account_scope,schema_version=1)
        atomic_write(path,json.dumps(value,ensure_ascii=False,indent=2))
    return value

def covered(book,start,end):
    return bool(book and any(a<=start and end<=b for a,b in book['ranges']))

def observed_drawdowns(rows,current_equity=None):
    peak=None;values=[]
    for row in rows:
        value=number(row['equity'],'权益');peak=value if peak is None else max(peak,value)
        values.append(value/peak-1 if peak>0 else None)
    if current_equity is not None:peak=current_equity if peak is None else max(peak,current_equity)
    current=current_equity/peak-1 if current_equity is not None and peak and peak>0 else None
    return peak,values,current

def adjusted_nav(rows,book):
    if len(rows)<2:return None,'至少需要两个权益采样点'
    # Modified Dietz uses timing weights; it is a sampled approximation, not
    # exact intraday TWR, which needs valuations immediately before transfers.
    nav=peak=1.;result=[{'time':rows[0]['_time'],'nav':1.,'dd':0.}]
    for prev,row in zip(rows,rows[1:]):
        a=prev['_time'];b=row['_time'];e0=number(prev['equity'],'权益');e1=number(row['equity'],'权益')
        if b<=a:return None,'采样时刻未前进，不能计算净值'
        if not covered(book,a,b):return None,'采样区间流水不完整，无法排除入金出金影响'
        flows=[r for r in book['rows'] if a<r['time']<=b and r['asset']=='USDT']
        if any('TRANSFER' in r['incomeType'] and r['incomeType'] not in CASH_TYPES for r in flows):
            return None,'发现尚未分类的划转类型，不能猜测外部现金流'
        cash=[r for r in flows if r['incomeType'] in CASH_TYPES]
        amount=sum((Decimal(r['income']) for r in cash),Decimal(0))
        weighted=sum((float(r['income'])*(b-r['time'])/(b-a) for r in cash),0.)
        denominator=e0+weighted
        if e0<=0 or denominator<=0:return None,'采样期资本基数非正，净值链无法可靠延续'
        ret=(e1-e0-float(amount))/denominator
        if not math.isfinite(ret) or ret< -1:return None,'现金流调整收益无效'
        nav*=1+ret;peak=max(peak,nav);result.append({'time':b,'nav':nav,'dd':nav/peak-1})
    return result,'按入出金时点加权的Modified Dietz采样近似；未捕获采样间浮盈峰值'

def threshold_status(equity,peak,drawdown=.6):
    if not 0<drawdown<1 or peak<=0:raise DataError('回撤参考线参数无效')
    target=peak*(1-drawdown);dd=equity/peak-1
    return {'target_equity':target,'amount_above_line':equity-target,'percentage_points':(dd+drawdown)*100,'breached':equity<=target}
