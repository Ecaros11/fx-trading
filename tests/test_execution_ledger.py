"""Offline execution-ledger tests. All API responses and paths are isolated."""
import contextlib,copy,datetime as dt,io,json,pathlib,sys,tempfile,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'paper'))
import execution_ledger as e
import dd_live as d
import ma50_live as m
NOW=1791504000000
def trade(i=1,t=NOW-100,**kw):
 return dict(symbol='ETHUSDT',id=i,orderId=i//2+1,time=t,side='BUY',positionSide='LONG',
             price='2000',qty='.01',quoteQty='20',commission='.01',commissionAsset='USDT',
             realizedPnl='0',buyer=True,maker=False,**{})|kw
def income(i=1,t=NOW-100,kind='COMMISSION',value='-.01',**kw):
 return dict(time=t,tranId=str(i),symbol='ETHUSDT',incomeType=kind,asset='USDT',
             income=str(value),tradeId='1')|kw
def account():
 return dict(multiAssetsMargin=False,totalMarginBalance='90',totalWalletBalance='100',
             totalUnrealizedProfit='-10',availableBalance='40')
def positions():
 return [dict(symbol='ETHUSDT',positionSide='LONG',positionAmt='.01',entryPrice='3000',
  markPrice='2000',liquidationPrice='1000',isolatedWallet='20',isolatedMargin='10',unRealizedProfit='-10',
  marginType='isolated',leverage='3',isAutoAddMargin='false',updateTime=NOW),
  dict(symbol='ETHUSDT',positionSide='SHORT',positionAmt='0',entryPrice='0',markPrice='2000',
  liquidationPrice='0',isolatedWallet='0',isolatedMargin='0',unRealizedProfit='0',
  marginType='isolated',leverage='3',isAutoAddMargin='false',updateTime=NOW)]
def snapshot():
 return e.normalize_snapshot(account(),positions(),NOW-1000,NOW)
def book(rows=(),start=NOW-e.DAY,end=NOW):
 return dict(complete=True,rows=list(rows),ranges=[[start,end]],start=start,end=end)
class API:
 def __init__(self,rows=()):
  self.rows=list(rows);self.calls=[]
 def user_trades(self,**p):
  self.calls.append(p)
  return sorted([x for x in self.rows if p['startTime']<=x['time']<=p['endTime']],key=lambda x:(x['time'],x['id']),reverse=True)[:p['limit']]

class PaginationTests(unittest.TestCase):
 def test_explicit_eth_windows_under_seven_days(self):
  api=API();r=e.fetch_trades(api,NOW-89*e.DAY,NOW)
  self.assertEqual(r['rows'],[]);self.assertTrue(r['complete'])
  self.assertEqual(len(api.calls),13)
  self.assertTrue(all(x['symbol']=='ETHUSDT' and x['endTime']-x['startTime']<7*e.DAY and 'fromId' not in x for x in api.calls))
 def test_full_newest_pages_bisected_without_losing_older_fills(self):
  rows=[trade(i,t=NOW-3000+i) for i in range(2501)];api=API(rows)
  r=e.fetch_trades(api,NOW-4000,NOW)
  self.assertEqual(len(r['rows']),2501)
  self.assertEqual([x['id'] for x in r['rows']],list(range(2501)))
 def test_equal_millisecond_fills_preserved(self):
  rows=[trade(i,t=NOW-1) for i in range(999)]+[trade(1001,t=NOW-2)]
  self.assertEqual(len(e.fetch_trades(API(rows),NOW-10,NOW)['rows']),1000)
 def test_saturated_single_millisecond_fails_closed(self):
  with self.assertRaisesRegex(e.DataError,'同一毫秒'):
   e.fetch_trades(API([trade(i,t=NOW-1) for i in range(1000)]),NOW-10,NOW)
 def test_network_failure_never_returns_complete_page(self):
  class Failed(API):
   def user_trades(self,**p):
    if len(self.calls):raise TimeoutError('network')
    return super().user_trades(**p)
  with self.assertRaises(TimeoutError):e.fetch_trades(Failed(),NOW-8*e.DAY,NOW)
 def test_protection_cap(self):
  with self.assertRaisesRegex(e.DataError,'保护上限'):e.fetch_trades(API(),NOW-8*e.DAY,NOW,max_calls=1)
 def test_invalid_range_response_symbol_and_time(self):
  for row in (trade(symbol='BTCUSDT'),trade(t=NOW+1)):
   with self.subTest(row=row):
    api=type('API',(),{'user_trades':lambda self,**kw:[row]})()
    with self.assertRaises(e.DataError):e.fetch_trades(api,NOW-1000,NOW)

class DataTests(unittest.TestCase):
 def test_duplicate_fill_conflict_rejected(self):
  with self.assertRaisesRegex(e.DataError,'冲突'):
   e.normalize_trades([trade(),trade(commission='.02')])
 def test_numeric_format_duplicates_and_large_ids_exact(self):
  rows=e.normalize_trades([trade(i=9007199254740999),trade(i=9007199254740999,qty='0.0100000',price='2000.000000')])
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['id'],9007199254740999)
 def test_invalid_numeric_and_boolean_fields(self):
  for kw in ({'price':'nan'},{'qty':'0'},{'id':True},{'time':NOW-.5},{'maker':'false'},{'buyer':False},{'quoteQty':'200'},{'commissionAsset':'=BAD'}):
   with self.subTest(kw=kw),self.assertRaises(e.DataError):e.normalize_trades([trade(**kw)])
 def test_snapshot_retains_actual_isolated_wallet_and_risk(self):
  s=snapshot();p=s['positions'][0]
  self.assertEqual(p['isolatedWallet'],'20');self.assertEqual(p['liquidationPrice'],'1000')
  self.assertFalse(p['isAutoAddMargin']);self.assertFalse(s['atomic'])
 def test_missing_duplicate_or_inconsistent_position_rejected(self):
  for p in ([],positions()[:1],positions()+positions()[:1],[positions()[0]|{'isolatedMargin':'50'},positions()[1]]):
   with self.subTest(p=p),self.assertRaises(e.DataError):e.normalize_snapshot(account(),p,NOW-1000,NOW)
 def test_nonfinite_account_rejected(self):
  with self.assertRaises(e.DataError):e.normalize_snapshot(account()|{'totalWalletBalance':'nan'},positions(),NOW-1000,NOW)
 def ledger(self,trades,incomes,tr=None,ir=None):
  b=e.empty('scope');b.update(trades=e.normalize_trades(trades),income=e.normalize_income(incomes),
     trade_ranges=tr or [[NOW-e.DAY,NOW]],income_ranges=ir or [[NOW-e.DAY,NOW]])
  return e.validate(b)
 def test_costs_not_double_counted_and_other_symbols_excluded(self):
  t=trade(realizedPnl='10',commission='.1')
  ir=[income(kind='COMMISSION',value='-.1'),income(2,kind='REALIZED_PNL',value='10'),
      income(3,kind='FUNDING_FEE',value='-.5'),income(4,kind='FUNDING_FEE',value='-999',symbol='BTCUSDT')]
  x=e.summary(self.ledger([t],ir))
  self.assertEqual(x['net_realized_usdt'],'9.4')
  self.assertEqual(x['reconciliation'][0]['commission_difference_by_asset']['USDT'],'0')
  self.assertEqual(x['reconciliation'][0]['realized_pnl_difference_usdt'],'0')
 def test_non_usdt_commission_not_added_to_usdt(self):
  x=e.summary(self.ledger([trade(realizedPnl='10',commission='.1',commissionAsset='BNB')],[]))
  self.assertEqual(x['commission_by_asset'],{'BNB':'0.1'});self.assertEqual(x['net_realized_usdt'],'10')
 def test_negative_fee_rebate_and_received_funding(self):
  x=e.summary(self.ledger([trade(realizedPnl='1',commission='-.1')],[income(kind='FUNDING_FEE',value='.2')]))
  self.assertEqual(x['net_realized_usdt'],'1.3')
 def test_net_uses_common_coverage_only(self):
  x=e.summary(self.ledger([trade(realizedPnl='5')],[income(kind='FUNDING_FEE',value='-1'),income(2,t=NOW-2*e.DAY,kind='FUNDING_FEE',value='-100')],
                          ir=[[NOW-3*e.DAY,NOW]]))
  self.assertEqual(x['funding_income_by_asset']['USDT'],'-101');self.assertEqual(x['net_realized_usdt'],'3.99')
 def test_no_joint_coverage_is_unknown_not_zero(self):
  x=e.summary(self.ledger([],[],tr=[[NOW-3*e.DAY,NOW-2*e.DAY]],ir=[[NOW-e.DAY,NOW]]))
  self.assertIsNone(x['net_realized_usdt'])
 def test_reconciliation_difference_visible(self):
  x=e.summary(self.ledger([trade(commission='.2')],[income(value='-.1')]))
  self.assertEqual(x['reconciliation'][0]['commission_difference_by_asset']['USDT'],'0.1')

class StorageTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=pathlib.Path(self.tmp.name)/'execution_ledger.json'
 def tearDown(self):self.tmp.cleanup()
 def sync(self,rows=(),incomes=(),end=NOW,scope='scope',api=None):
  return e.sync_ledger(self.path,api or API(rows),scope,book(incomes,end=end),snapshot())
 def test_idempotent_sync_no_duplicate_fills_or_costs(self):
  self.sync([trade()],[income()]);b=self.sync([trade()],[income()])
  self.assertEqual(len(b['trades']),1);self.assertEqual(len(b['income']),1)
  self.assertEqual(len(b['snapshots']),2);self.assertEqual(b['runs'][-1]['new_trades'],0)
 def test_incremental_overlap_and_old_records_preserved(self):
  self.sync([trade()]);api=API([trade(2,t=NOW+e.DAY-1)])
  b=self.sync(end=NOW+e.DAY,api=api)
  self.assertEqual(len(b['trades']),2);self.assertEqual(api.calls[0]['startTime'],NOW-3*e.DAY)
 def test_failure_does_not_advance_or_replace_old_ledger(self):
  self.sync([trade()]);old=self.path.read_bytes()
  api=type('Broken',(),{'user_trades':lambda *a,**kw:(_ for _ in ()).throw(TimeoutError('offline'))})()
  with self.assertRaises(TimeoutError):self.sync(api=api)
  self.assertEqual(self.path.read_bytes(),old)
 def test_conflicting_fill_preserves_old_ledger(self):
  self.sync([trade()]);old=self.path.read_bytes()
  with self.assertRaises(e.DataError):self.sync([trade(price='2100',quoteQty='21')])
  self.assertEqual(self.path.read_bytes(),old)
 def test_scope_change_partial_income_and_future_cursor_rejected(self):
  self.sync();old=self.path.read_bytes()
  with self.assertRaises(e.DataError):self.sync(scope='other')
  with self.assertRaises(e.DataError):e.sync_ledger(self.path,API(),'scope',book()|{'complete':False},snapshot())
  with self.assertRaises(e.DataError):self.sync(end=NOW-1)
  self.assertEqual(self.path.read_bytes(),old)
 def test_atomic_failure_preserves_json(self):
  self.sync();old=self.path.read_bytes()
  with patch.object(e,'atomic_write',side_effect=OSError('disk full')),self.assertRaises(OSError):self.sync([trade()])
  self.assertEqual(self.path.read_bytes(),old)
 def test_csv_failure_reports_that_json_was_committed(self):
  with patch.object(e,'export_ledger',side_effect=OSError('locked CSV')),self.assertRaisesRegex(e.DataError,'JSON账本已保存'):
   self.sync([trade()])
  self.assertEqual(len(e.load_ledger(self.path)['trades']),1)
 def test_export_fills_orders_snapshots(self):
  self.sync([trade(),trade(2)],[income()])
  with (self.path.parent/'execution_trades.csv').open(encoding='utf-8-sig') as f:
   self.assertIn('orderId',f.readline());self.assertIn('2000',f.read())
  self.assertTrue((self.path.parent/'execution_snapshots.csv').exists())
 def test_concurrent_sync_no_lost_records(self):
  with ThreadPoolExecutor(max_workers=4) as pool:
   list(pool.map(lambda i:self.sync([trade(i)]),range(1,5)))
  b=e.load_ledger(self.path);self.assertEqual(len(b['trades']),4);self.assertEqual(len(b['snapshots']),4)
 def test_retention_gap_remains_visible(self):
  self.sync()
  b=self.sync(end=NOW+100*e.DAY)
  self.assertEqual(len(b['trade_ranges']),2)
 def test_corrupt_existing_snapshot_rejected(self):
  self.sync();b=json.loads(self.path.read_text(encoding='utf-8-sig'))
  b['snapshots'][0]['positions'][0]['isolatedWallet']='nan';self.path.write_text(json.dumps(b))
  with self.assertRaises(e.DataError):e.load_ledger(self.path)

class DecisionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=pathlib.Path(self.tmp.name)/'ma50_decisions.json'
 def tearDown(self):self.tmp.cleanup()
 def decision(self,i='one',t=NOW-200,**kw):
  return dict(decision_id=i,decision_ms=str(t),account_scope='scope',symbol='ETHUSDT',date='2026-10-08',
   validation_status='order',side='BUY',position_side='LONG',reference_price='2000',signal='做多',action='加多')|kw
 def test_same_day_decisions_preserved_and_retry_idempotent(self):
  e.append_decision(self.path,self.decision());e.append_decision(self.path,self.decision('two'))
  e.append_decision(self.path,self.decision())
  self.assertEqual(len(e.load_decisions(self.path)),2)
 def test_conflict_does_not_replace_decision(self):
  e.append_decision(self.path,self.decision());old=self.path.read_bytes()
  with self.assertRaises(e.DataError):e.append_decision(self.path,self.decision(reference_price='2100'))
  self.assertEqual(old,self.path.read_bytes())
 def test_legacy_not_given_fabricated_time(self):
  e.append_decision(self.path,dict(date='2026-10-08'))
  self.assertFalse(self.path.exists())
 def test_candidate_is_only_tentative_and_directional_price_difference(self):
  t=e.normalize_trades([trade(price='2020',quoteQty='20.2')])[0]
  c=e.candidate(t,[self.decision()],'scope')
  self.assertEqual(c['candidate_status'],'time_side_only_not_verified');self.assertEqual(c['adverse_price_difference_bps'],'100')
  t=trade(side='SELL',buyer=False,price='1980',quoteQty='19.8')
  c=e.candidate(t,[self.decision(side='SELL')],'scope')
  self.assertEqual(c['adverse_price_difference_bps'],'100')
 def test_future_wrong_scope_old_and_superseded_decisions_never_match(self):
  t=trade()
  for decisions in ([self.decision(t=NOW)], [self.decision(account_scope='other')], [self.decision(t=NOW-2*e.DAY)],
                    [self.decision(),self.decision('later',t=NOW-150,validation_status='hold')]):
   with self.subTest(decisions=decisions):self.assertEqual(e.candidate(t,decisions,'scope'),{})
 def test_ma50_archive_appends_every_run_but_daily_csv_is_latest(self):
  archive=self.path.parent/'ma50_log.csv'
  with patch.object(m,'ARCHIVE',archive),contextlib.redirect_stdout(io.StringIO()):
   m.do_archive(self.decision());m.do_archive(self.decision('two',t=NOW-100))
  self.assertEqual(len(e.load_decisions(self.path)),2)
  with archive.open(encoding='utf-8-sig') as f:self.assertEqual(len(list(__import__('csv').DictReader(f))),1)

class IntegrationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.stack=contextlib.ExitStack()
  for name,file in (('EQ_LOG','eq.csv'),('INCOME_CACHE','dd_income.json'),('EXECUTION_LOG','execution_ledger.json'),('DECISION_LOG','decisions.json')):
   self.stack.enter_context(patch.object(d,name,self.root/file))
 def tearDown(self):self.stack.close();self.tmp.cleanup()
 def cli(self,args,fail_trade=False):
  class BN(API):
   key='dummy-secret-not-saved'
   def futures_account(self):return account()
   def positions(self):return positions()
   def fapi(self,path,p=None,signed=True):
    if path.endswith('/time'):return {'serverTime':NOW}
    return [income()] if p['page']==1 else []
   def user_trades(self,**kw):
    if fail_trade:raise TimeoutError('trades')
    return super().user_trades(**kw)
  out=io.StringIO()
  with patch.object(d,'bn_api',return_value=BN([trade()])),patch.object(sys,'argv',['dd_live.py']+args),contextlib.redirect_stdout(out):
   code=d.main()
  return code,out.getvalue()
 def test_snapshot_syncs_trades_and_equity_without_credentials_in_files(self):
  code,out=self.cli(['--snapshot']);self.assertEqual(code,0)
  self.assertTrue(d.EQ_LOG.exists());self.assertEqual(len(e.load_ledger(d.EXECUTION_LOG)['trades']),1)
  for p in self.root.glob('*.json'):self.assertNotIn('dummy-secret-not-saved',p.read_text(encoding='utf-8-sig'))
 def test_standalone_ledger_does_not_add_dd_equity_sample(self):
  code,out=self.cli(['--ledger']);self.assertEqual(code,0);self.assertFalse(d.EQ_LOG.exists())
 def test_history_is_fully_offline(self):
  self.cli(['--ledger'])
  with patch.object(d,'bn_api',side_effect=AssertionError('must not access network')),patch.object(sys,'argv',['dd_live.py','--ledger-history']),contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(d.main(),0)
 def test_trade_failure_is_nonzero_and_equity_sample_survives(self):
  code,out=self.cli(['--snapshot'],fail_trade=True)
  self.assertEqual(code,1);self.assertTrue(d.EQ_LOG.exists());self.assertFalse(d.EXECUTION_LOG.exists())
  self.assertIn('同步失败',out)

class AuditRegressionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=pathlib.Path(self.tmp.name)/'ledger.json'
 def tearDown(self):self.tmp.cleanup()
 def test_numeric_auto_margin_flags_rejected(self):
  for value in (0,1,0.0,1.0,None,'TRUE','0'):
   p=positions();p[0]['isAutoAddMargin']=value
   with self.subTest(value=value),self.assertRaises(e.DataError):
    e.normalize_snapshot(account(),p,NOW-1000,NOW)
 def test_valid_auto_margin_flags_preserved(self):
  for value,expected in ((True,True),(False,False),('true',True),('false',False)):
   p=positions();p[0]['isAutoAddMargin']=value
   self.assertIs(e.normalize_snapshot(account(),p,NOW-1000,NOW)['positions'][0]['isAutoAddMargin'],expected)
 def test_old_queryable_gap_is_recovered_and_merged(self):
  b=e.empty('scope');b['trade_ranges']=[[NOW-8*e.DAY,NOW-7*e.DAY],[NOW-e.DAY,NOW]]
  e.atomic_write(self.path,json.dumps(b))
  missed=trade(77,t=NOW-6*e.DAY)
  result=e.sync_ledger(self.path,API([missed]),'scope',book(),snapshot())
  self.assertEqual([t['id'] for t in result['trades']],[77])
  self.assertEqual(result['trade_ranges'],[[NOW-8*e.DAY,NOW]])
  self.assertEqual(result['runs'][-1]['trade_queries'],[[NOW-7*e.DAY+1,NOW]])
 def test_gap_outside_retention_is_not_fabricated(self):
  b=e.empty('scope');b['trade_ranges']=[[NOW-100*e.DAY,NOW-99*e.DAY],[NOW-e.DAY,NOW]]
  e.atomic_write(self.path,json.dumps(b));api=API()
  result=e.sync_ledger(self.path,api,'scope',book(),snapshot())
  self.assertEqual(result['trade_ranges'],[[NOW-100*e.DAY,NOW-99*e.DAY],[NOW-89*e.DAY,NOW]])
  self.assertTrue(all(c['startTime']>=NOW-89*e.DAY for c in api.calls))
 def test_failed_gap_query_retains_old_file_and_coverage(self):
  b=e.empty('scope');b['trade_ranges']=[[NOW-8*e.DAY,NOW-7*e.DAY],[NOW-e.DAY,NOW]]
  e.atomic_write(self.path,json.dumps(b));before=self.path.read_bytes()
  class Failed(API):
   def user_trades(self,**kw):raise TimeoutError('gap')
  with self.assertRaises(TimeoutError):e.sync_ledger(self.path,Failed(),'scope',book(),snapshot())
  self.assertEqual(self.path.read_bytes(),before)
 def result(self,trades,incomes):
  b=e.empty('scope');b.update(trades=e.normalize_trades(trades),income=e.normalize_income(incomes),
    trade_ranges=[[NOW-e.DAY,NOW]],income_ranges=[[NOW-e.DAY,NOW]])
  return b
 def test_swapped_trade_costs_fail_even_when_totals_match(self):
  b=self.result([trade(1,commission='.1'),trade(2,commission='.2')],
    [income(1,value='-.2',tradeId='1'),income(2,value='-.1',tradeId='2')])
  x=e.summary(b)
  self.assertEqual(x['reconciliation'][0]['commission_difference_by_asset']['USDT'],'0')
  self.assertEqual(len(x['reconciliation'][0]['per_trade_differences']),2)
  self.assertFalse(x['reconciled'])
 def test_reconciliation_failure_prints_provisional_and_returns_false(self):
  b=self.result([trade(commission='.2')],[income(value='-.1')]);out=io.StringIO()
  with contextlib.redirect_stdout(out):ok=e.print_summary(b)
  self.assertFalse(ok);self.assertIn('暂算净额',out.getvalue())
 def test_missing_referenced_fill_is_not_reconciled(self):
  b=self.result([],[income(value='0',tradeId='77')]);x=e.summary(b)
  self.assertFalse(x['reconciled']);self.assertEqual(x['reconciliation'][0]['income_trade_ids_missing_from_fills'],['77'])
 def test_absent_trade_id_is_explicit_aggregate_only(self):
  b=self.result([trade()],[income(tradeId='')]);out=io.StringIO()
  with contextlib.redirect_stdout(out):e.print_summary(b)
  self.assertIn('只核对合计',out.getvalue())
 def test_offline_cost_difference_returns_nonzero(self):
  b=self.result([trade(commission='.2')],[income(value='-.1')]);e.atomic_write(self.path,json.dumps(b))
  with patch.object(d,'EXECUTION_LOG',self.path),patch.object(d,'bn_api',side_effect=AssertionError('offline')), \
       patch.object(sys,'argv',['dd_live.py','--ledger-history']),contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(d.main(),1)
 def test_sync_cost_difference_still_saves_evidence_but_returns_nonzero(self):
  eq=self.path.parent/'eq.csv';cache=self.path.parent/'income.json'
  class BN(API):
   key='fake'
   def futures_account(self):return account()
   def positions(self):return positions()
   def fapi(self,path,p=None,signed=True):
    return {'serverTime':NOW} if path.endswith('/time') else [income(value='-.1')]
  with patch.object(d,'EQ_LOG',eq),patch.object(d,'INCOME_CACHE',cache),patch.object(d,'EXECUTION_LOG',self.path), \
       patch.object(d,'DECISION_LOG',self.path.parent/'decisions.json'),patch.object(d,'bn_api',return_value=BN([trade(commission='.2')])), \
       patch.object(sys,'argv',['dd_live.py','--snapshot']),contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(d.main(),1)
  self.assertTrue(eq.exists());self.assertEqual(len(e.load_ledger(self.path)['trades']),1)

if __name__=='__main__':unittest.main()

