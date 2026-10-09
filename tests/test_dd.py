"""Isolated tests for DD data integrity, cash flows and local persistence."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'paper'));sys.path.insert(0,str(ROOT))
import dd_live as d
import dd_support as s
NOW=int(dt.datetime(2026,10,8,12,tzinfo=dt.UTC).timestamp()*1000)

def income(i,t=None,amount='1',kind='TRANSFER',asset='USDT'):
    return {'time':NOW-1000 if t is None else t,'income':str(amount),'incomeType':kind,'asset':asset,'tranId':str(i),'symbol':''}

def snap(t,e,w=None,u=0,**extra):
    clock=dt.datetime.fromtimestamp(t/1000,dt.UTC)
    return dict(date=clock.date().isoformat(),ts=clock.isoformat(),equity=str(e),wallet=str(e-u if w is None else w),unrealized=str(u),_time=t,**extra)

def book(rows=(),start=NOW-2*s.DAY,end=NOW):
    return {'rows':s.normalize_income(list(rows)),'ranges':[[start,end]],'start':start,'end':end,'complete':True}

class IncomeTests(unittest.TestCase):
    def api(self,rows,limit=1000):
        calls=[]
        def fapi(path,params=None,signed=True):
            calls.append((path,params,signed))
            if path.endswith('/time'):return {'serverTime':NOW}
            i=(params['page']-1)*params['limit'];return rows[i:i+params['limit']]
        return type('BN',(),{'fapi':staticmethod(fapi)})(),calls
    def test_explicit_window_and_page_no_symbol_filter(self):
        bn,calls=self.api([income(i) for i in range(2501)])
        r=s.fetch_income(bn,end_ms=NOW)
        self.assertEqual(len(r['rows']),2501);self.assertEqual(len(calls),3)
        for _,p,signed in calls:self.assertIn('startTime',p);self.assertIn('endTime',p);self.assertNotIn('symbol',p);self.assertTrue(signed)
    def test_equal_millisecond_events_not_skipped(self):
        bn,_=self.api([income(i,t=NOW-1) for i in range(2001)])
        self.assertEqual(len(s.fetch_income(bn,end_ms=NOW)['rows']),2001)
    def test_out_of_order_batch_retained_and_sorted(self):
        rows=[income(i,t=NOW-10-i) for i in range(5)];bn,_=self.api(rows)
        r=s.fetch_income(bn,end_ms=NOW);self.assertEqual([x['time'] for x in r['rows']],sorted(x['time'] for x in rows))
    def test_failure_after_full_page_is_not_partial_success(self):
        class BN:
            def fapi(self,path,p,signed=True):
                if p['page']>1:raise TimeoutError('lost')
                return [income(i) for i in range(1000)]
        with self.assertRaises(TimeoutError):s.fetch_income(BN(),end_ms=NOW)
    def test_repeated_page_fails(self):
        bn=type('BN',(),{'fapi':lambda *a,**k:[income(i) for i in range(1000)]})()
        with self.assertRaisesRegex(s.DataError,'未前进'):s.fetch_income(bn,end_ms=NOW)
    def test_page_cap_is_not_claimed_complete(self):
        bn,_=self.api([income(i) for i in range(1000)])
        with self.assertRaisesRegex(s.DataError,'保护上限'):s.fetch_income(bn,end_ms=NOW,max_pages=1)
    def test_empty_valid_window_is_complete_without_fabricated_capital(self):
        bn,_=self.api([]);r=s.fetch_income(bn,end_ms=NOW)
        self.assertTrue(r['complete']);self.assertEqual(r['rows'],[])
    def test_wrong_range_rejected(self):
        bn,_=self.api([income(1,t=NOW+1)])
        with self.assertRaises(s.DataError):s.fetch_income(bn,end_ms=NOW)
    def test_bad_response_rejected(self):
        bn=type('BN',(),{'fapi':lambda *a,**k:{'code':-1}})()
        with self.assertRaises(s.DataError):s.fetch_income(bn,end_ms=NOW)
    def test_nonfinite_income_and_fractional_time_rejected(self):
        for r in (income(1,amount='nan'),income(1,amount='inf'),income(1,t=NOW-.5)):
            with self.subTest(r=r):
                with self.assertRaises(s.DataError):s.normalize_income([r])
    def test_duplicate_id_conflict_rejected(self):
        with self.assertRaises(s.DataError):s.normalize_income([income(1),income(1,amount='2')])
    def test_duplicate_numeric_format_not_a_conflict(self):
        rows=s.normalize_income([income(1,amount='1.0'),income(1,amount='1.00000000')])
        self.assertEqual(len(rows),1);self.assertEqual(s.total(rows),1)
    def test_assets_not_added_as_if_usdt(self):
        rows=s.normalize_income([income(1,amount=100),income(2,amount=2,asset='BNB')])
        self.assertEqual(s.total(rows,'TRANSFER'),100)
    def test_wallet_curve_anchored_to_actual_ending_balance(self):
        b=book([income(1,amount=10),income(2,amount=-2,kind='COMMISSION')])
        curve,end=s.wallet_curve(b,108);self.assertEqual(curve[0][1],100);self.assertEqual(end,108)
    def test_same_time_netting_avoids_fake_intermediate_peak(self):
        b=book([income(1,amount=100),income(2,amount=-100)])
        curve,_=s.wallet_curve(b,100);self.assertEqual(max(v for _,v in curve),100)

class AccountingTests(unittest.TestCase):
    def test_history_never_uses_future_peak(self):
        rows=[snap(NOW-2*s.DAY,100),snap(NOW-s.DAY,80),snap(NOW,200)]
        _,values,_=s.observed_drawdowns(rows);self.assertEqual(values[0],0);self.assertAlmostEqual(values[1],-.2);self.assertEqual(values[2],0)
    def test_new_high_current_equity_has_zero_drawdown(self):
        peak,_,dd=s.observed_drawdowns([snap(NOW-1,100)],120);self.assertEqual(peak,120);self.assertEqual(dd,0)
    def test_zero_peak_does_not_divide_by_zero(self):
        _,values,dd=s.observed_drawdowns([snap(NOW,0)],0);self.assertIsNone(values[0]);self.assertIsNone(dd)
    def test_deposit_does_not_count_as_return(self):
        rows=[snap(NOW-s.DAY,100),snap(NOW,600)];b=book([income(1,t=NOW-s.DAY//2,amount=500)])
        nav,_=s.adjusted_nav(rows,b);self.assertEqual(nav[-1]['nav'],1);self.assertEqual(nav[-1]['dd'],0)
    def test_withdrawal_does_not_count_as_loss(self):
        rows=[snap(NOW-s.DAY,100),snap(NOW,20)];b=book([income(1,t=NOW-s.DAY//2,amount=-80)])
        nav,_=s.adjusted_nav(rows,b);self.assertEqual(nav[-1]['nav'],1);self.assertEqual(nav[-1]['dd'],0)
    def test_coin_conversion_is_cash_into_usdt_not_trading_profit(self):
        rows=[snap(NOW-s.DAY,100),snap(NOW,150)]
        nav,_=s.adjusted_nav(rows,book([income(1,amount=50,kind='COIN_SWAP_DEPOSIT')]))
        self.assertEqual(nav[-1]['nav'],1)
    def test_converted_funds_advance_verified_usdt_capital(self):
        rows=[snap(NOW-s.DAY,100,capital='100',capital_source='user_verified')]
        value,_=d.confirmed_capital(rows,book([income(1,amount=50,kind='COIN_SWAP_DEPOSIT')]),NOW)
        self.assertEqual(value,150)
    def test_derived_verified_capital_continues_next_period(self):
        rows=[snap(NOW-s.DAY,100,capital='100',capital_source='verified_baseline_plus_transfers')]
        value,_=d.confirmed_capital(rows,book([income(1,amount=50)]),NOW);self.assertEqual(value,150)
    def test_transfer_timing_weights_capital(self):
        rows=[snap(NOW-s.DAY,100),snap(NOW,220)];b=book([income(1,t=NOW-s.DAY//2,amount=100)])
        nav,_=s.adjusted_nav(rows,b);self.assertAlmostEqual(nav[-1]['nav'],1+20/150)
    def test_missing_flow_coverage_never_assumed_zero(self):
        nav,note=s.adjusted_nav([snap(NOW-s.DAY,100),snap(NOW,90)],book(start=NOW-1));self.assertIsNone(nav);self.assertIn('不完整',note)
    def test_zero_capital_base_rejects_nav(self):
        nav,_=s.adjusted_nav([snap(NOW-s.DAY,0),snap(NOW,100)],book([income(1,amount=100)]));self.assertIsNone(nav)
    def test_unknown_transfer_type_not_guessed(self):
        nav,_=s.adjusted_nav([snap(NOW-s.DAY,100),snap(NOW,200)],book([income(1,amount=100,kind='INTERNAL_TRANSFER')]));self.assertIsNone(nav)
    def test_legacy_capital_is_not_trusted(self):
        rows=[snap(NOW-1,100,capital='500')]
        value,_=d.confirmed_capital(rows,book(),NOW);self.assertIsNone(value)
    def test_verified_capital_advanced_by_later_transfers(self):
        rows=[snap(NOW-s.DAY,100,capital='100',capital_source='user_verified')]
        value,_=d.confirmed_capital(rows,book([income(1,amount=500)]),NOW);self.assertEqual(value,600)
    def test_reference_line_formula_and_breach_sign(self):
        self.assertAlmostEqual(s.threshold_status(50,100,.543)['target_equity'],45.7)
        x=s.threshold_status(35,100);self.assertTrue(x['breached']);self.assertLess(x['amount_above_line'],0);self.assertLess(x['percentage_points'],0)
    def test_wallet_peak_never_used_as_equity_peak(self):
        peak,_,dd=s.observed_drawdowns([snap(NOW-1,90,100,-10)],80);self.assertEqual(peak,90);self.assertAlmostEqual(dd,80/90-1)

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=pathlib.Path(self.tmp.name)/'equity.csv';self.cache=self.path.with_suffix('.json')
    def tearDown(self):self.tmp.cleanup()
    def save(self,e=100,now=None):return s.write_snapshot(self.path,e,e,0,now=now or dt.datetime.fromtimestamp(NOW/1000,dt.UTC),account_scope='scope')
    def test_same_day_high_preserved(self):
        now=dt.datetime.fromtimestamp(NOW/1000,dt.UTC);self.save(120,now);self.save(80,now+dt.timedelta(seconds=1))
        rows=s.load_snapshot_rows(self.path);self.assertEqual(len(rows),2);self.assertEqual(max(float(r['equity']) for r in rows),120)
    def test_microsecond_samples_preserved(self):
        now=dt.datetime.fromtimestamp(NOW/1000,dt.UTC);self.save(120,now);self.save(80,now+dt.timedelta(microseconds=1))
        self.assertEqual(len(s.load_snapshot_rows(self.path)),2)
    def test_concurrent_snapshot_writes_no_loss(self):
        now=dt.datetime.fromtimestamp(NOW/1000,dt.UTC)
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda i:self.save(100+i,now+dt.timedelta(seconds=i)),range(12)))
        self.assertEqual(len(s.load_snapshot_rows(self.path)),12)
    def test_atomic_failure_preserves_old_and_cleans_temp(self):
        self.save();before=self.path.read_bytes()
        with patch.object(s.os,'replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.save(80,dt.datetime.fromtimestamp(NOW/1000+1,dt.UTC))
        self.assertEqual(self.path.read_bytes(),before);self.assertEqual(list(self.path.parent.glob('*.tmp')),[])
    def test_bom_legacy_and_unknown_columns_preserved(self):
        self.path.write_text('date,ts,equity,wallet,unrealized,capital,custom\n2026-10-07,2026-10-07T00:00:00Z,100,100,0,100,keep\n',encoding='utf-8-sig');self.save()
        rows=s.load_snapshot_rows(self.path);self.assertEqual(rows[0]['custom'],'keep');self.assertEqual(rows[1]['capital_source'],'unknown')
    def test_corrupt_snapshot_not_silently_skipped_or_overwritten(self):
        self.path.write_text('date,equity,wallet,unrealized\n2026-10-07,nan,1,0\n');before=self.path.read_bytes()
        with self.assertRaises(s.DataError):self.save()
        self.assertEqual(self.path.read_bytes(),before)
    def test_different_account_not_merged(self):
        self.save()
        with self.assertRaises(s.DataError):s.load_snapshot_rows(self.path,'other')
        s.cache_income(self.cache,book(),'scope')
        with self.assertRaises(s.DataError):s.cache_income(self.cache,book(),'other')
    def test_repeated_cache_import_does_not_double_income(self):
        a=s.cache_income(self.cache,book([income(1)]),'scope');b=s.cache_income(self.cache,book([income(1)]),'scope')
        self.assertEqual(len(b['rows']),1);self.assertEqual(s.total(b['rows']),1)
    def test_partial_cache_never_claimed_complete(self):
        with self.assertRaises(s.DataError):s.cache_income(self.cache,dict(book(),complete=False),'scope')
        self.assertFalse(self.cache.exists())
    def test_cached_row_outside_coverage_rejected(self):
        b=book([income(1)]);b['schema_version']=1;b['account_scope']='scope';b['ranges']=[[NOW-10,NOW-5]]
        self.cache.write_text(json.dumps(b))
        with self.assertRaises(s.DataError):s.load_income_cache(self.cache)
    def test_cache_preserves_old_coverage(self):
        s.cache_income(self.cache,book(start=NOW-4*s.DAY,end=NOW-2*s.DAY),'scope')
        b=s.cache_income(self.cache,book(start=NOW-2*s.DAY,end=NOW),'scope');self.assertTrue(s.covered(b,NOW-4*s.DAY,NOW))

class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.stack=contextlib.ExitStack()
        self.stack.enter_context(patch.object(d,'EQ_LOG',self.root/'eq.csv'));self.stack.enter_context(patch.object(d,'INCOME_CACHE',self.root/'income.json'))
        self.stack.enter_context(patch.object(d,'EXECUTION_LOG',self.root/'execution.json'));self.stack.enter_context(patch.object(d,'DECISION_LOG',self.root/'decisions.json'))
        self.account={'multiAssetsMargin':False,'totalMarginBalance':'90','totalWalletBalance':'100','totalUnrealizedProfit':'-10','availableBalance':'50'}
    def tearDown(self):self.stack.close();self.tmp.cleanup()
    def run_cli(self,args=(),account=None,rows=None,fail=None):
        acct=self.account if account is None else account
        class BN:
            key='test-key'
            def futures_account(self):
                if fail=='account':raise TimeoutError('account')
                return acct
            def fapi(self,path,p=None,signed=True):
                if path.endswith('/time'):return {'serverTime':NOW}
                if fail=='income':raise TimeoutError('income')
                return [] if rows is None else rows
            def positions(self):
                return [dict(symbol='ETHUSDT',positionSide='BOTH',positionAmt='0',entryPrice='0',markPrice='2000',
                   liquidationPrice='0',isolatedWallet='0',isolatedMargin='0',unRealizedProfit='0',
                   marginType='isolated',leverage='3',isAutoAddMargin='false',updateTime=NOW)]
            def user_trades(self,**kwargs):return []
        out=io.StringIO()
        # Deterministic retry without sleeps or network.
        original=d.api_retry
        with patch.object(d,'bn_api',return_value=BN()),patch.object(d,'api_retry',lambda fn,**k:original(fn,base=0)),patch.object(sys,'argv',['dd_live.py']+list(args)),contextlib.redirect_stdout(out):code=d.main()
        return code,out.getvalue()
    def test_default_labels_unknown_capital_and_separate_metrics(self):
        code,out=self.run_cli();self.assertEqual(code,0);self.assertIn('无法核实',out);self.assertIn('不是已证明的累计本金',out);self.assertIn('钱包回撤',out)
    def test_snapshot_failure_does_not_create_bogus_record(self):
        code,_=self.run_cli(['--snapshot'],fail='income');self.assertEqual(code,1);self.assertFalse(d.EQ_LOG.exists());self.assertFalse(d.INCOME_CACHE.exists())
    def test_bad_account_values_and_multiasset_rejected(self):
        for patch_values in ({'totalMarginBalance':'nan'},{'totalMarginBalance':'0'},{'multiAssetsMargin':True}):
            code,_=self.run_cli(['--snapshot'],account=self.account|patch_values);self.assertEqual(code,1);self.assertFalse(d.EQ_LOG.exists())
    def test_valid_snapshot_and_verified_capital(self):
        code,out=self.run_cli(['--snapshot','--capital','100']);self.assertEqual(code,0)
        rows=s.load_snapshot_rows(d.EQ_LOG);self.assertEqual(rows[0]['capital_source'],'user_verified');self.assertIn('-10.00%',out)
    def test_history_offline_and_no_credentials(self):
        s.write_snapshot(d.EQ_LOG,100,100,0,now=dt.datetime.fromtimestamp(NOW/1000,dt.UTC))
        with patch.object(d,'bn_api',side_effect=AssertionError('must be offline')),patch.object(sys,'argv',['dd_live.py','--history']),contextlib.redirect_stdout(io.StringIO()):self.assertEqual(d.main(),0)
    def test_api_initialization_failure_returns_nonzero(self):
        with patch.object(d,'bn_api',side_effect=RuntimeError('no credentials')),patch.object(sys,'argv',['dd_live.py']),contextlib.redirect_stdout(io.StringIO()):self.assertEqual(d.main(),1)
    def test_snapshot_and_history_mutually_exclusive(self):
        with patch.object(sys,'argv',['dd_live.py','--snapshot','--history']),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as e:d.main()
        self.assertEqual(e.exception.code,2)
    def test_generic_symbols_and_no_guessed_liquidation_price(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):d.position_report([{'symbol':'BTCUSDT','positionAmt':'-.01','positionSide':'BOTH','entryPrice':'60000','markPrice':'61000','liquidationPrice':'0'}])
        self.assertIn('BTCUSDT 空仓',out.getvalue());self.assertIn('无法计算距离',out.getvalue());self.assertNotIn('ETH',out.getvalue())
    def test_timestamp_safety_survives_resync(self):
        import binance_api
        with patch.object(binance_api.BN,'__init__',return_value=None),patch.object(binance_api.BN,'_ts',return_value=10000):self.assertEqual(d.bn_api()._ts(),5000)

if __name__=='__main__':unittest.main()
