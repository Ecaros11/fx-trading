"""Offline regression tests for MA50 decisions, account failures and cash flows."""
import ast
from concurrent.futures import ThreadPoolExecutor
import contextlib
import copy
import datetime as dt
import io
import json
import pathlib
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'paper'))
import ma50_live as m
import binance_api
from ma50_core import (ExchangeRules,PositionState,plan_order,strategy_position,
                       parse_positions,floor_qty,ceil_qty,MAX_LEVERAGE)
from align import simulate,panel,max_dd
DAY=86400000


def bars(count=80,rising=True):
    today=int(dt.datetime.now(dt.UTC).timestamp()*1000)//DAY*DAY
    start=today-count*DAY
    result=[]
    for i in range(count):
        close=(2500+5*i if rising else 3200-5*i)+15*np.sin(i)
        opening=result[-1]['c'] if result else close
        result.append({'t':start+i*DAY,'o':float(opening),'h':float(max(opening,close)+30),
                       'l':float(min(opening,close)-30),'c':float(close),'v':1000.0})
    return result


def funding(data):
    return [{'t':b['t']+h*3600000,'rate':.0001,'markPrice':b['o']}
            for b in data for h in (0,8,16)]


def symbol(name='ETHUSDT',notional=20,maxqty=2000):
    return {'symbol':name,'contractType':'PERPETUAL','status':'TRADING','filters':[
        {'filterType':'LOT_SIZE','stepSize':'0.001','minQty':'0.001','maxQty':'10000'},
        {'filterType':'MARKET_LOT_SIZE','stepSize':'0.001','minQty':'0.001','maxQty':str(maxqty)},
        {'filterType':'MIN_NOTIONAL','notional':str(notional)}]}


def positions(long=.04,short=0,hedge=True,lev=3,mode='isolated'):
    common={'symbol':'ETHUSDT','leverage':str(lev),'marginType':mode,
            'entryPrice':'2700','unRealizedProfit':'0','liquidationPrice':'1500'}
    if hedge:return [dict(common,positionSide='LONG',positionAmt=str(long)),dict(common,positionSide='SHORT',positionAmt=str(-short))]
    return [dict(common,positionSide='BOTH',positionAmt=str(long-short))]


class OrderTests(unittest.TestCase):
    def setUp(self):self.rules=ExchangeRules();self.state=PositionState()
    def order(self,**kwargs):
        params=dict(equity=90,price=2700,target_position=3,state=self.state,rules=self.rules,available=90)
        params.update(kwargs);return plan_order(**params)
    def test_rounding_stays_within_budget(self):
        p=self.order();self.assertTrue(p.actionable);self.assertLessEqual(p.required_funds,90);self.assertLess(p.quantity,.1)
    def test_price_buffer_is_budgeted(self):
        p=self.order();self.assertAlmostEqual(p.required_funds,p.quantity*2700*1.001*(1/3+.0005))
    def test_zero_available_blocks_increase(self):self.assertEqual(self.order(available=0).status,'blocked')
    def test_unknown_available_blocks_increase(self):self.assertEqual(self.order(available=None).status,'blocked')
    def test_unknown_position_blocks(self):self.assertEqual(self.order(state=None).status,'blocked')
    def test_nan_price_rejected(self):
        with self.assertRaises(ValueError):self.order(price=float('nan'))
    def test_infinite_equity_rejected(self):
        with self.assertRaises(ValueError):self.order(equity=float('inf'))
    def test_step_decimal_rounding(self):self.assertEqual(floor_qty(.3,.1),.3);self.assertEqual(ceil_qty(.3001,.1),.4)
    def test_oneway_dust_exit(self):
        p=self.order(target_position=0,state=PositionState(long_qty=.005),available=0)
        self.assertTrue(p.actionable);self.assertEqual(p.side,'SELL');self.assertTrue(p.reduce_only);self.assertEqual(p.action,'平多')
    def test_hedge_dust_exit(self):
        p=self.order(target_position=0,state=PositionState(long_qty=.005,hedge=True),available=0)
        self.assertTrue(p.actionable);self.assertEqual(p.position_side,'LONG');self.assertFalse(p.reduce_only)
    def test_ordinary_reduction_below_five_percent_holds(self):
        p=self.order(equity=10000,target_position=.026,price=2700,state=PositionState(long_qty=.1),available=0)
        self.assertEqual(p.status,'hold');self.assertIn('5%',p.reason)
    def test_symmetric_five_percent_boundaries(self):
        cases=[(1,1.049,'hold',''),(1,1.05,'order','BUY'),
               (1.1,1.051,'hold',''),(1.1,1.05,'order','SELL')]
        for held,target,status,side in cases:
            with self.subTest(held=held,target=target):
                p=self.order(equity=1000,price=1000,target_position=target,
                             state=PositionState(long_qty=held),available=1000)
                self.assertEqual(p.status,status);self.assertEqual(p.side,side)
                if p.actionable:self.assertEqual(p.quantity,.05)
    def test_gate_uses_step_rounded_trade_notional(self):
        p=self.order(equity=1001,price=1000,target_position=1050.1/1001,
                     state=PositionState(long_qty=1),available=1000)
        self.assertEqual(p.status,'hold')  # Rounded trade is 50, threshold is 50.05.
    def test_initial_entry_below_soft_threshold_allowed(self):
        p=self.order(equity=10000,price=100,target_position=.01,available=10000)
        self.assertTrue(p.actionable);self.assertEqual(p.side,'BUY')
        self.assertLess(p.quantity*100,10000*.05)
    def test_initial_entry_still_requires_exchange_notional(self):
        p=self.order(equity=10000,price=100,target_position=.001,available=10000)
        self.assertEqual(p.status,'hold')
    def test_full_exit_below_soft_threshold_allowed(self):
        p=self.order(equity=10000,price=2700,target_position=0,
                     state=PositionState(long_qty=.005),available=0)
        self.assertTrue(p.actionable);self.assertEqual(p.action,'平多');self.assertEqual(p.quantity,.005)
    def test_exposure_above_three_times_bypasses_soft_threshold(self):
        p=self.order(equity=1000,price=1000,target_position=3,
                     state=PositionState(long_qty=3.001),available=0)
        self.assertTrue(p.actionable);self.assertEqual(p.side,'SELL');self.assertLess(p.quantity*1000,50)
    def test_legacy_increase_only_reference_can_be_reproduced(self):
        p=self.order(equity=10000,target_position=.026,price=2700,
                     state=PositionState(long_qty=.1),available=0,soft_pct=.02,rebalance_both=False)
        self.assertTrue(p.actionable);self.assertEqual(p.side,'SELL')
    def test_short_side_not_netted(self):self.assertEqual(self.order(state=PositionState(.04,.04,True)).status,'blocked')
    def test_cross_increase_blocked(self):self.assertEqual(self.order(state=PositionState(margin_type='cross')).status,'blocked')
    def test_wrong_leverage_increase_blocked(self):self.assertEqual(self.order(state=PositionState(leverage=20)).status,'blocked')
    def test_wrong_leverage_exit_permitted(self):self.assertTrue(self.order(target_position=0,state=PositionState(.04,leverage=20),available=0).actionable)
    def test_market_limit_enforced(self):self.assertEqual(self.order(equity=1e7,available=1e7).status,'blocked')
    def test_min_qty_enforced(self):
        p=self.order(equity=10,available=10,rules=ExchangeRules(min_qty=.1));self.assertFalse(p.actionable)
    def test_bear_has_zero_target(self):self.assertEqual(strategy_position(False,.2,90)[0],0)
    def test_vol_cap_has_zero_target(self):self.assertEqual(strategy_position(True,1.3,90), (0,'vol_cap'))
    def test_25_percent_not_false_213_cap(self):self.assertGreater(strategy_position(True,.05,500,.25)[0],2.13)
    def test_parse_hedge_keeps_gross_sides(self):
        s=parse_positions(positions(.04,.04),True);self.assertEqual(s.long_qty,.04);self.assertEqual(s.short_qty,.04)
    def test_missing_side_rejected(self):
        with self.assertRaises(ValueError):parse_positions(positions()[:1],True)
    def test_duplicate_side_rejected(self):
        with self.assertRaises(ValueError):parse_positions(positions()+positions()[:1],True)


    def test_invalid_price_buffer_rejected(self):
        for value in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):self.order(price_buffer=value)
    def test_quantity_display_tracks_step(self):
        with patch.object(m,'STEP_SIZE',.0001):self.assertEqual(m.format_qty(.0123),'0.0123')


    def test_exact_step_reduction_not_lost_to_float_subtraction(self):
        p=self.order(equity=100,price=100,target_position=.029,state=PositionState(long_qty=.03),available=0,soft_pct=0)
        self.assertTrue(p.actionable);self.assertEqual(p.side,'SELL');self.assertEqual(p.quantity,.001)


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ma50_regression_');self.directory=pathlib.Path(self.tmp.name)
        self.data=bars();self.funds=funding(self.data);self.now=(self.data[-1]['t']+DAY)+3600000
        self.stack=contextlib.ExitStack()
        for name,relative in [('ROOT','root'),('CACHE','candles.json'),('FUND','funding.json'),('ARCHIVE','history.csv'),('_RULES_CACHE','rules.json')]:
            self.stack.enter_context(patch.object(m,name,self.directory/relative))
        for name,value in [('MIN_NOTIONAL',20.0),('STEP_SIZE',.001),('MIN_QTY',.001),('MARKET_MAX_QTY',2000.0),('TARGET_VOL_OVERRIDE',.6)]:
            self.stack.enter_context(patch.object(m,name,value))
        m.CACHE.write_text(json.dumps(self.data));m.FUND.write_text(json.dumps(self.funds))
        self.write_rules()
    def tearDown(self):self.stack.close();self.tmp.cleanup()
    def write_rules(self,**updates):
        row={'symbol':'ETHUSDT','fetched':dt.datetime.now(dt.UTC).isoformat(),'rules':vars(ExchangeRules())};row.update(updates)
        m._RULES_CACHE.write_text(json.dumps(row))
    def quiet(self):return contextlib.redirect_stdout(io.StringIO())


class DataTests(Isolated):
    def test_actual_eth_selection(self):
        fake=SimpleNamespace(fapi=lambda *a,**k:{'symbols':[symbol('BTCUSDT',50,120),symbol()]})
        m.load_exchange_rules(fake);self.assertEqual(m.MIN_NOTIONAL,20);self.assertEqual(m.MARKET_MAX_QTY,2000)
        self.assertEqual(json.loads(m._RULES_CACHE.read_text())['symbol'],'ETHUSDT')
    def test_rule_status_rejected(self):
        row=symbol();row['status']='BREAK'
        with self.assertRaises(ValueError):ExchangeRules.from_symbol(row)
    def test_api_suspension_never_falls_back_to_trading_cache(self):
        row=symbol();row['status']='BREAK'
        before=m._RULES_CACHE.read_bytes()
        with self.assertRaises(m.DataError):m.load_exchange_rules(SimpleNamespace(fapi=lambda *a,**k:{'symbols':[row]}))
        self.assertEqual(m._RULES_CACHE.read_bytes(),before)
    def test_api_missing_symbol_never_falls_back(self):
        with self.assertRaises(m.DataError):m.load_exchange_rules(SimpleNamespace(fapi=lambda *a,**k:{'symbols':[symbol('BTCUSDT')]}))
    def test_cache_write_failure_still_uses_fresh_api_rules(self):
        with patch.object(m,'_atomic_write',side_effect=OSError('disk full')):
            source=m.load_exchange_rules(SimpleNamespace(fapi=lambda *a,**k:{'symbols':[symbol(notional=25)]}))
        self.assertEqual(m.MIN_NOTIONAL,25);self.assertIn('缓存保存失败',source)
    def test_cache_without_symbol_rejected(self):
        row=json.loads(m._RULES_CACHE.read_text());del row['symbol'];m._RULES_CACHE.write_text(json.dumps(row))
        with self.assertRaises(m.DataError):m.load_exchange_rules()
    def test_cache_expired_rejected(self):
        self.write_rules(fetched=(dt.datetime.now(dt.UTC)-dt.timedelta(days=8)).isoformat())
        with self.assertRaises(m.DataError):m.load_exchange_rules()
    def test_api_and_cache_failure_no_defaults(self):
        m._RULES_CACHE.write_text('{}')
        fake=SimpleNamespace(fapi=lambda *a,**k:(_ for _ in ()).throw(ConnectionError()))
        with self.assertRaises(m.DataError):m.load_exchange_rules(fake)
    def test_valid_cache_fallback(self):
        fake=SimpleNamespace(fapi=lambda *a,**k:(_ for _ in ()).throw(ConnectionError()))
        self.assertIn('API 失败',m.load_exchange_rules(fake))
    def test_unfinished_bar_excluded(self):
        one=self.data[-1];self.assertEqual(m.complete_bars([one],one['t']+int(DAY*.99))[0],[])
    def test_api_close_time_respected(self):
        one=dict(self.data[-1],closeTime=self.now+100);self.assertEqual(m.complete_bars([one],self.now)[0],[])
    def test_gap_rejected(self):
        with self.assertRaises(m.DataError):m.validate_bars(self.data[:-3]+self.data[-2:],continuous=True)
    def test_duplicate_rejected(self):
        with self.assertRaises(m.DataError):m.validate_bars(self.data+[self.data[-1]])
    def test_nan_candle_rejected(self):
        damaged=copy.deepcopy(self.data);damaged[-1]['c']=float('nan')
        with self.assertRaises(ValueError):m.validate_bars(damaged)
    def test_invalid_ohlc_rejected(self):
        damaged=copy.deepcopy(self.data);damaged[-1]['l']=damaged[-1]['h']+1
        with self.assertRaises(m.DataError):m.validate_bars(damaged)
    def test_funding_rebuild_multiple_pages(self):
        generated=[{'symbol':'ETHUSDT','fundingTime':self.data[0]['t']+i*28800000,'fundingRate':'.0001','markPrice':'2700'} for i in range(2501)]
        m.FUND.write_text('{broken');calls=[]
        def api(path,params,signed=False):
            calls.append(params['startTime']);return [r for r in generated if r['fundingTime']>=params['startTime']][:1000]
        add,err=m.refresh_funding(SimpleNamespace(fapi=api),end_ms=generated[-1]['fundingTime']+1)
        self.assertIsNone(err);self.assertEqual(add,2501);self.assertEqual(len(calls),3);self.assertEqual(len(m.load_funding()),2501)
    def test_funding_interrupted_keeps_previous(self):
        before=m.FUND.read_bytes();start=self.funds[-1]['t']+1;calls=[]
        def api(path,params,signed=False):
            calls.append(1)
            if len(calls)>1:raise TimeoutError()
            return [{'fundingTime':start+i*28800000,'fundingRate':'.0001','markPrice':'2700'} for i in range(1000)]
        _,err=m.refresh_funding(SimpleNamespace(fapi=api),end_ms=start+2000*28800000)
        self.assertIsNotNone(err);self.assertEqual(m.FUND.read_bytes(),before)
    def test_funding_duplicate_rejected(self):
        m.FUND.write_text(json.dumps(self.funds+self.funds[:1]))
        with self.assertRaises(m.DataError):m.load_funding()


    def test_legacy_marks_paginate_and_label_approximation(self):
        start=self.data[60]['t'];rows=[{'t':start+i*28800000,'rate':.0001} for i in range(1501)]
        before={'t':start-28800000,'rate':.0001};real={'t':start+1501*28800000,'rate':.0001,'markPrice':3000}
        calls=[]
        def api(path,params,signed=False):
            self.assertEqual(path,'/fapi/v1/markPriceKlines');calls.append(params['startTime'])
            return [[r['t'],'2700'] for r in rows if params['startTime']<=r['t']<=params['endTime']][:1500]
        result=m.enrich_funding_marks(SimpleNamespace(fapi=api),[before]+rows+[real],start)
        self.assertEqual(len(calls),2);self.assertNotIn('markPrice',before);self.assertEqual(real['markPrice'],3000)
        self.assertTrue(all(r['markPriceApproximate'] and r['markPriceSource']=='mark_kline_open_8h' for r in result[1:-1]))
    def test_missing_legacy_mark_fails(self):
        with self.assertRaises(m.DataError):m.enrich_funding_marks(SimpleNamespace(fapi=lambda *a,**k:[]),[{'t':self.data[60]['t'],'rate':.0001}],self.data[60]['t'])
    def test_mark_failure_preserves_funding_cache(self):
        rows=copy.deepcopy(self.funds);del rows[180]['markPrice'];m.FUND.write_text(json.dumps(rows));before=m.FUND.read_bytes()
        def api(path,params,signed=False):
            if path.endswith('/fundingRate'):
                return [{'fundingTime':r['t'],'fundingRate':r['rate']} for r in rows if r['t']>=params['startTime']]
            raise TimeoutError('mark price unavailable')
        _,err=m.refresh_funding(SimpleNamespace(fapi=api),end_ms=self.now)
        self.assertIsNotNone(err);self.assertEqual(m.FUND.read_bytes(),before)
    def test_empty_refresh_not_success_and_preserves_cache(self):
        before=m.CACHE.read_bytes()
        _,_,st=m.refresh(SimpleNamespace(fapi=lambda *a,**k:[]),self.now)
        self.assertFalse(st['ok']);self.assertIn('空数据',st['err']);self.assertEqual(m.CACHE.read_bytes(),before)
    def test_funding_refresh_rejects_missing_intraday_settlement(self):
        rows=[r for r in self.funds if r['t']!=self.data[65]['t']+8*3600000]
        m.FUND.write_text(json.dumps(rows));before=m.FUND.read_bytes()
        _,err=m.refresh_funding(SimpleNamespace(fapi=lambda *a,**k:[]),end_ms=self.now)
        self.assertIn('缺少结算时点',err);self.assertEqual(m.FUND.read_bytes(),before)
    def test_funding_cache_rejects_fractional_timestamp(self):
        rows=copy.deepcopy(self.funds);rows[180]['t']+=.5;m.FUND.write_text(json.dumps(rows))
        with self.assertRaises(ValueError):m.load_funding()
    def test_funding_cache_rejects_wrong_symbol(self):
        rows=copy.deepcopy(self.funds);rows[180]['symbol']='BTCUSDT';m.FUND.write_text(json.dumps(rows))
        with self.assertRaises(ValueError):m.load_funding()
    def test_selfcheck_labels_approximate_prices(self):
        rows=copy.deepcopy(self.funds);rows[180]['markPriceApproximate']=True;m.FUND.write_text(json.dumps(rows))
        out=io.StringIO()
        with contextlib.redirect_stdout(out):m.selfcheck()
        self.assertIn('8h 开盘近似',out.getvalue())


class FlowTests(Isolated):
    def run_flow(self,data=None,acct=None,poss=None,fail=None,orders=None,algos=None,ticker=None,times=None):
        data=self.data if data is None else data
        account={'totalMarginBalance':'90','availableBalance':'90','multiAssetsMargin':False,'canTrade':True}
        if acct:account.update(acct)
        position_rows=positions() if poss is None else poss
        now=self.now
        clock_values=iter(times) if times is not None else None
        class FakeBN:
            def __init__(self):pass
            def fapi(self,path,params=None,signed=True):
                if 'exchangeInfo' in path:return {'symbols':[symbol('BTCUSDT',50,120),symbol()]}
                if path.endswith('/time'):return {'serverTime':next(clock_values) if clock_values is not None else now}
                if 'ticker/price' in path:return {'symbol':'ETHUSDT','price':'2700'} if ticker is None else ticker
                if path.endswith('/openOrders'):
                    if fail=='orders':raise TimeoutError()
                    return [] if orders is None else orders
                if path.endswith('/openAlgoOrders'):
                    if fail=='algos':raise TimeoutError()
                    return [] if algos is None else algos
                if 'positionSide/dual' in path:return {'dualSidePosition':True}
                if 'commissionRate' in path:
                    if fail=='commission':raise TimeoutError()
                    return {'symbol':'ETHUSDT','takerCommissionRate':'.0005'}
                raise AssertionError(path)
            def futures_account(self):
                if fail=='account':raise ConnectionError()
                return account
            def positions(self,*args):
                if fail=='positions':raise ConnectionError()
                return position_rows
        with patch.object(binance_api,'BN',FakeBN),patch.object(m,'refresh',lambda bn,now_ms=None:(data,[],{'ok':True})):
            return m.run(SimpleNamespace(check=True,archive=False))
    def test_bear_only_closes_long(self):
        out,rec=self.run_flow(bars(rising=False));self.assertEqual(rec['signal'],'空仓');self.assertEqual(rec['side'],'SELL');self.assertEqual(rec['action'],'平多');self.assertNotIn('【买入',out)
    def test_vol_cap_matches_archive(self):
        data=copy.deepcopy(self.data)
        for i in range(len(data)-11,len(data)):
            data[i]['c']=3500 if i%2 else 2900;data[i]['h']=max(data[i]['o'],data[i]['c'])+30;data[i]['l']=min(data[i]['o'],data[i]['c'])-30
        out,rec=self.run_flow(data);self.assertEqual(rec['decision_reason'],'vol_cap');self.assertEqual(rec['side'],'SELL');self.assertEqual(rec['action'],'平多');self.assertNotIn('波动率无效',out)
    def test_report_and_archive_identify_symmetric_policy(self):
        out,rec=self.run_flow()
        self.assertIn('普通双向调仓门限 5%',out)
        self.assertEqual(rec['rebalance_policy'],'symmetric')
        self.assertEqual(rec['soft_rebalance_pct'],'0.0500')
        self.assertIn('rebalance_policy',m.FIELDS);self.assertIn('soft_rebalance_pct',m.FIELDS)
    def test_gap_no_advice(self):
        with self.assertRaises(m.DataError):self.run_flow(self.data[:-3]+self.data[-2:])
    def test_missing_latest_no_advice(self):
        out,rec=self.run_flow(self.data[:-1]);self.assertIsNone(rec);self.assertIn('暂停',out)
    def test_zero_available_no_buy(self):
        out,rec=self.run_flow(acct={'availableBalance':'0'},poss=positions(long=0));self.assertEqual(rec['validation_status'],'blocked');self.assertEqual(rec['order_qty'],'');self.assertNotIn('【买入',out)
    def test_low_statistical_threshold_not_hard_block(self):
        out,rec=self.run_flow(acct={'totalMarginBalance':'40','availableBalance':'40'},poss=positions(long=0));self.assertEqual(rec['validation_status'],'order');self.assertEqual(rec['side'],'BUY')
    def test_position_failure_no_secondary_crash(self):
        out,rec=self.run_flow(fail='positions');self.assertEqual(rec['validation_status'],'blocked');self.assertEqual(rec['order_qty'],'');self.assertNotIn('UnboundLocalError',out)
    def test_account_failure_no_action(self):
        out,rec=self.run_flow(fail='account');self.assertEqual(rec['validation_status'],'blocked');self.assertEqual(rec['side'],'')
    def test_commission_failure_no_action(self):
        out,rec=self.run_flow(fail='commission');self.assertEqual(rec['validation_status'],'blocked');self.assertEqual(rec['side'],'')
    def test_hedge_short_not_ignored(self):
        out,rec=self.run_flow(poss=positions(short=.04));self.assertEqual(rec['validation_status'],'blocked');self.assertIn('空头',out)
    def test_account_cannot_trade_or_status_unknown_blocks(self):
        for flag in (False,None,'true'):
            with self.subTest(flag=flag):
                _,rec=self.run_flow(acct={'canTrade':flag})
                self.assertEqual(rec['validation_status'],'blocked');self.assertEqual(rec['order_qty'],'')
    def test_multi_asset_mode_blocks(self):
        out,rec=self.run_flow(acct={'multiAssetsMargin':True});self.assertEqual(rec['validation_status'],'blocked')
    def test_unfilled_regular_order_blocks_duplicate_advice(self):
        out,rec=self.run_flow(orders=[{'symbol':'ETHUSDT','side':'BUY'}])
        self.assertEqual(rec['validation_status'],'blocked');self.assertEqual(rec['order_qty'],'');self.assertIn('未成交委托',out)
    def test_conditional_order_not_ignored_on_exit(self):
        out,rec=self.run_flow(bars(rising=False),algos=[{'symbol':'ETHUSDT','orderType':'STOP_MARKET'}])
        self.assertEqual(rec['validation_status'],'blocked');self.assertEqual(rec['order_qty'],'');self.assertIn('条件1',out)
    def test_order_query_failure_blocks_quantity(self):
        for fail in ('orders','algos'):
            with self.subTest(fail=fail):
                out,rec=self.run_flow(fail=fail)
                self.assertEqual(rec['validation_status'],'blocked');self.assertEqual(rec['order_qty'],'')
    def test_wrong_order_symbol_or_response_blocks(self):
        for value in ({'code':-1},[{'symbol':'BTCUSDT'}],[{}]):
            with self.subTest(value=value):
                _,rec=self.run_flow(orders=value);self.assertEqual(rec['validation_status'],'blocked')
    def test_invalid_ticker_rejected_before_report(self):
        for value in ('0','-1','nan','inf'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):self.run_flow(ticker={'symbol':'ETHUSDT','price':value})
        with self.assertRaises(m.DataError):self.run_flow(ticker={'symbol':'BTCUSDT','price':'2700'})
    def test_main_blocked_exits_nonzero(self):
        with patch.object(sys,'argv',['ma50_live.py','--check']),patch.object(m,'run',lambda a:('暂停',{'validation_status':'blocked'})),self.quiet():
            self.assertEqual(m.main(),1)
    def test_bad_target_argument_nonzero(self):
        with patch.object(sys,'argv',['ma50_live.py','--target-vol','nan']),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ex:m.main()
            self.assertEqual(ex.exception.code,2)


class LedgerTests(Isolated):
    def test_constant_leverage_actual_rebalancing_cost(self):
        r=simulate(self.data,self.funds,initial_equity=100,fee=.001,warmup=50,fixed_lev=2)
        self.assertGreater(np.count_nonzero(r['fees'][51:]),0)
    def test_midnight_cost_uses_old_quantity(self):
        events=copy.deepcopy(self.funds)
        for e in events:e['rate']=0
        e=next(e for e in events if e['t']==self.data[51]['t']);e['rate']=.01
        r=simulate(self.data,events,initial_equity=100,fee=.001,warmup=50,fixed_lev=2)
        self.assertAlmostEqual(r['funding_cost'][51],r['quantity'][50]*e['markPrice']*.01)
    def test_intraday_cost_uses_new_quantity(self):
        events=copy.deepcopy(self.funds)
        for e in events:e['rate']=0
        e=next(e for e in events if e['t']==self.data[50]['t']+8*3600000);e['rate']=.01
        r=simulate(self.data,events,initial_equity=100,fee=.001,warmup=50,fixed_lev=2)
        self.assertAlmostEqual(r['funding_cost'][50],r['quantity'][50]*e['markPrice']*.01)
    def test_missing_funding_day_fails(self):
        events=[e for e in self.funds if e['t']//DAY!=self.data[65]['t']//DAY]
        with self.assertRaises(ValueError):simulate(self.data,events)
    def test_future_data_does_not_change_past(self):
        short=simulate(self.data[:70],self.funds);full=simulate(self.data,self.funds)
        np.testing.assert_allclose(short['net'],full['net'][:70],rtol=0,atol=1e-12)
    def test_weight_uses_previous_close(self):
        P=panel([b['c'] for b in self.data],[0]*len(self.data));w=P.weight(.6)
        self.assertTrue(np.all(w[~np.r_[False,P.sig[:-1].astype(bool)]]==0))
    def test_initial_peak_in_drawdown(self):self.assertAlmostEqual(max_dd([-.1]),-.1)
    def test_dynamic_uses_same_simulator(self):
        result=m.dynamic_drawdown(self.data,m.funding_by_day(),90)
        expected=simulate(self.data,self.funds,.6,90,rules=m.current_rules(),constrained=True,price_buffer=.001)
        self.assertAlmostEqual(result['end'],expected['equity'][-1])


    def test_dca_deposit_is_not_return(self):
        cash=np.zeros(len(self.data));cash[60]=100;cash[70]=50
        r=simulate(self.data,self.funds,initial_equity=0,contributions=cash,fixed_lev=0)
        self.assertEqual(r['equity'][69],100);self.assertEqual(r['equity'][70],150)
        np.testing.assert_array_equal(r['net'],np.zeros(len(self.data)))
    def test_dca_first_deposit_equals_initial_capital(self):
        cash=np.zeros(len(self.data));cash[60]=100
        a=simulate(self.data,self.funds,initial_equity=0,contributions=cash,constrained=True,price_buffer=.001)
        b=simulate(self.data,self.funds,initial_equity=100,constrained=True,price_buffer=.001)
        np.testing.assert_allclose(a['equity'][60:],b['equity'][60:]);np.testing.assert_array_equal(a['net'],b['net'])
    def test_dca_midnight_funding_still_uses_old_quantity(self):
        cash=np.zeros(len(self.data));cash[60]=100;cash[70]=50
        events=copy.deepcopy(self.funds)
        for e in events:e['rate']=0
        event=next(e for e in events if e['t']==self.data[70]['t']);event['rate']=.01
        r=simulate(self.data,events,initial_equity=0,contributions=cash,fixed_lev=2)
        self.assertAlmostEqual(r['funding_cost'][70],r['quantity'][69]*event['markPrice']*.01)
    def test_missing_intraday_settlement_rejected(self):
        events=[e for e in self.funds if e['t']!=self.data[65]['t']+8*3600000]
        with self.assertRaisesRegex(ValueError,'缺少结算时点'):simulate(self.data,events)
    def test_funding_boundary_jitter_still_charges_old_position(self):
        events=copy.deepcopy(self.funds)
        for e in events:
            if e['t']%DAY==0:e['t']+=47
        a=simulate(self.data,self.funds);b=simulate(self.data,events)
        np.testing.assert_array_equal(a['funding_cost'],b['funding_cost'])
    def test_invalid_funding_mark_fails_even_if_no_position(self):
        for value in (-1,0,float('nan'),float('inf')):
            with self.subTest(value=value):
                events=copy.deepcopy(self.funds);events[180]['markPrice']=value
                with self.assertRaises(ValueError):simulate(self.data,events,fixed_lev=0)
    def test_duplicate_settlement_slot_rejected(self):
        events=self.funds+[dict(self.funds[180],t=self.funds[180]['t']+1)]
        with self.assertRaisesRegex(ValueError,'同一结算时点'):simulate(self.data,events)
    def test_unaligned_daily_timestamp_rejected(self):
        data=[dict(b,t=b['t']+1) for b in self.data]
        with self.assertRaises(ValueError):simulate(data,self.funds)
    def test_fractional_daily_timestamp_rejected(self):
        data=[dict(b,t=b['t']+.5) for b in self.data]
        with self.assertRaises(ValueError):simulate(data,self.funds)
    def test_explicit_four_hour_funding_schedule(self):
        events=[{'t':b['t']+h*3600000,'rate':.0001,'markPrice':b['o']} for b in self.data for h in range(0,24,4)]
        r=simulate(self.data,events,funding_interval_hours=4);self.assertTrue(np.isfinite(r['equity']).all())
        with self.assertRaises(ValueError):simulate(self.data,events)
    def test_invalid_dca_input_rejected(self):
        for cash in ([0],np.full(len(self.data),float('nan')),np.full(len(self.data),-1),np.ones(len(self.data))):
            with self.assertRaises(ValueError):simulate(self.data,self.funds,contributions=cash)


    def test_explicit_default_vol_window_matches_default(self):
        a=simulate(self.data,self.funds);b=simulate(self.data,self.funds,vol_window=10)
        np.testing.assert_array_equal(a['net'],b['net'])
    def test_twenty_day_vol_uses_exact_prior_returns(self):
        r=simulate(self.data,self.funds,vol_window=20,initial_equity=100,target_vol=.15)
        i=60;closes=np.array([b['c'] for b in self.data]);ret=np.r_[0,np.diff(closes)/closes[:-1]]
        expected=strategy_position(bool(closes[i-1]>np.mean(closes[i-50:i])),ret[i-20:i].std(ddof=1)*np.sqrt(365),100,.15)[0]
        self.assertAlmostEqual(r['target'][i],expected)
    def test_twenty_day_window_prefix_causality(self):
        a=simulate(self.data[:70],self.funds,vol_window=20);b=simulate(self.data,self.funds,vol_window=20)
        np.testing.assert_allclose(a['net'],b['net'][:70],rtol=0,atol=1e-12)
    def test_bad_vol_window_rejected(self):
        for window in (0,1,20.5,True,60):
            with self.assertRaises(ValueError):simulate(self.data,self.funds,vol_window=window)


    def test_flat_targets_leave_no_floating_quantity_dust(self):
        data=copy.deepcopy(self.data)
        for i in range(68,len(data)):
            data[i]['c']=2600-20*(i-68);data[i]['o']=data[i-1]['c'];data[i]['h']=max(data[i]['o'],data[i]['c'])+30;data[i]['l']=min(data[i]['o'],data[i]['c'])-30
        r=simulate(data,self.funds,initial_equity=100,constrained=True)
        self.assertTrue(np.any(r['target'][60:]==0))
        np.testing.assert_array_equal(r['quantity'][r['target']==0],np.zeros(np.count_nonzero(r['target']==0)))


class StorageTests(Isolated):
    def record(self,i=0):
        b=self.data[i];return {'symbol':'ETHUSDT','date':dt.datetime.fromtimestamp(b['t']/1000,dt.UTC).strftime('%Y-%m-%d'),
            'bar_ms':str(b['t']),'bar_close':str(b['c']),'ma50':'2700','dist_ma50_pct':'1',
            'signal':'做多','action':'不动','entry_ref':str(b['c']),'validation_status':'hold'}
    def test_atomic_failure_retains_old_file(self):
        target=self.directory/'atomic.json';target.write_text('old')
        with patch.object(m.os,'replace',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):m._atomic_write(target,'new')
        self.assertEqual(target.read_text(encoding="utf-8"),'old');self.assertEqual(list(self.directory.glob('*.tmp')),[])
    def test_archive_keeps_unknown_legacy_fields(self):
        row=self.record();row['custom']='keep';m.save_archive([row]);self.assertEqual(m.load_archive()[0]['custom'],'keep')
    def test_same_day_archive_update_keeps_unknown_fields(self):
        row=self.record();row['external_note']='keep';m.save_archive([row])
        with self.quiet():m.do_archive(self.record())
        self.assertEqual(m.load_archive()[0]['external_note'],'keep')
    def test_backfill_bad_row_does_not_destroy_other_records(self):
        bad=self.record();bad['bar_ms']='invalid';bad['entry_ref']='nan'
        m.save_archive([bad,self.record(1)])
        fake=SimpleNamespace(fapi=lambda *a,**k:{'serverTime':self.now})
        with patch.object(binance_api,'BN',return_value=fake),patch.object(m,'refresh',return_value=(self.data,[],{'ok':True})),self.quiet():m.backfill()
        rows=m.load_archive();self.assertEqual(rows[0]['status'],'failed');self.assertEqual(rows[1]['status'],'complete')
    def test_archive_update_preserves_backfill(self):
        row=self.record();row['price_return_10d']='1.234';m.save_archive([row])
        with self.quiet():m.do_archive(self.record())
        self.assertEqual(m.load_archive()[0]['price_return_10d'],'1.234')
    def test_concurrent_archive_updates_no_loss(self):
        with self.quiet(),ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda i:m.do_archive(self.record(i)),range(12)))
        self.assertEqual(len(m.load_archive()),12)
    def test_legacy_status_marked(self):
        row=self.record();del row['validation_status'];m.save_archive([row]);self.assertEqual(m.load_archive()[0]['validation_status'],'legacy_unverified')
    def test_selfcheck_failure_exit(self):
        with patch.object(sys,'argv',['ma50_live.py','--selfcheck','--offline']),patch.object(m,'selfcheck',return_value=False),self.quiet():self.assertEqual(m.main(),1)
    def test_stats_sync_twice_preserves_functions(self):
        target=self.directory/'source.py';target.write_text(pathlib.Path(m.__file__).read_text(encoding='utf-8'),encoding='utf-8')
        names=lambda s:{n.name for n in ast.parse(s).body if isinstance(n,ast.FunctionDef)}
        before=names(target.read_text(encoding="utf-8"))
        with patch.object(m,'__file__',str(target)),self.quiet():m.sync_methods();m.sync_methods()
        self.assertEqual(names(target.read_text(encoding="utf-8")),before)
    def test_history_labels_price_return(self):
        m.save_archive([self.record()]);out=io.StringIO()
        with contextlib.redirect_stdout(out):m.history()
        self.assertIn('价格涨跌',out.getvalue())


class ClientTests(unittest.TestCase):
    def test_client_rejects_non_read_requests(self):
        bn=binance_api.BN.__new__(binance_api.BN)
        with self.assertRaises(binance_api.BNError):bn._request('POST','https://example.invalid',signed=False)
    def test_midpoint_clock_sync(self):
        bn=binance_api.BN.__new__(binance_api.BN);bn._request=lambda *a,**k:{'serverTime':2500}
        with patch.object(binance_api.time,'time',side_effect=[1,3]):self.assertEqual(bn.sync_time(),500)
    def test_help_formats_percent_and_exits_successfully(self):
        out=io.StringIO()
        with patch.object(sys,'argv',['ma50_live.py','--help']),contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as result:m.main()
        self.assertEqual(result.exception.code,0);self.assertIn('5~60%',out.getvalue())
    def test_windows_console_encoding_configured(self):
        output=SimpleNamespace(reconfigure=lambda **k:None);error=SimpleNamespace(reconfigure=lambda **k:None)
        with patch.object(m.sys,'stdout',output),patch.object(m.sys,'stderr',error),patch.object(sys,'argv',['ma50_live.py','--history']),patch.object(m,'history'):
            with patch.object(output,'reconfigure') as configure:
                self.assertEqual(m.main(),0);configure.assert_called_once_with(encoding='utf-8',errors='replace')
    def test_default_proxy_applied(self):
        with patch.object(binance_api,'load_env',return_value={'BINANCE_KEY':'test','BINANCE_SECRET':'test'}),patch.object(binance_api.BN,'sync_time',return_value=0),patch.object(binance_api.urllib.request,'build_opener') as opener:
            binance_api.BN();handler=opener.call_args.args[0]
            self.assertEqual(handler.proxies['https'],binance_api.PROXY)



class RiskAuditTests(Isolated):
    run_flow = FlowTests.run_flow
    def test_liquidation_distance_uses_position_mark_not_ticker(self):
        p=positions()
        p[0].update(markPrice='3000',isolatedWallet='20',isolatedMargin='32',unRealizedProfit='12')
        out,rec=self.run_flow(poss=p)
        self.assertIn('相对标记价 -50.0%',out)
        self.assertEqual(rec['liquidation_distance_pct'],'50.000000')
        self.assertEqual(rec['mark_price_at_position'],'3000.0')
        self.assertEqual(rec['isolated_wallet_at_position'],'20.0')
    def test_missing_mark_is_unknown_not_ticker_fallback(self):
        out,rec=self.run_flow(poss=positions())
        self.assertIn('强平距离未知',out)
        self.assertEqual(rec['liquidation_distance_pct'],'')
    def test_invalid_mark_and_isolated_equity_pause(self):
        for change in ({'markPrice':'0'},{'markPrice':'nan'},
                       {'isolatedWallet':'20','isolatedMargin':'50','unRealizedProfit':'0'}):
            p=positions();p[0].update(change)
            with self.subTest(change=change):
                out,rec=self.run_flow(poss=p)
                self.assertEqual(rec['validation_status'],'blocked')


class ReadinessAuditTests(Isolated):
    run_flow = FlowTests.run_flow
    def test_slow_account_query_discards_executable_quantity(self):
        out,rec=self.run_flow(times=[self.now,self.now,self.now+120001])
        self.assertEqual(rec['validation_status'],'blocked')
        self.assertEqual(rec['order_qty'],'')
        self.assertNotIn('【买入',out)
        self.assertIn('120秒',out)
    def test_clock_backwards_blocks(self):
        _,rec=self.run_flow(times=[self.now,self.now,self.now-1])
        self.assertEqual(rec['validation_status'],'blocked')
    def test_rollover_inside_sampling_blocks_old_daily_signal(self):
        midnight=(self.now//DAY+1)*DAY
        out,rec=self.run_flow(times=[midnight-1000,midnight-500,midnight+500])
        self.assertEqual(rec['validation_status'],'blocked')
        self.assertNotIn('【买入',out)
        self.assertIn('日线边界',out)
    def test_fresh_sampling_still_produces_same_order(self):
        _,a=self.run_flow()
        _,b=self.run_flow(times=[self.now,self.now,self.now+120000])
        self.assertEqual(a['order_qty'],b['order_qty'])
        self.assertEqual(a['validation_status'],b['validation_status'])

class PerformanceMetricAuditTests(unittest.TestCase):
    def test_sharpe_matches_independent_sample_standard_deviation(self):
        from align import sharpe
        values=[.03,-.02,.01,0]
        mean=sum(values)/len(values)
        sd=(sum((x-mean)**2 for x in values)/(len(values)-1))**.5
        self.assertAlmostEqual(sharpe(values),mean/sd*365**.5)
    def test_cashflow_neutral_deposit_and_initial_drawdown(self):
        from align import cagr, max_dd
        r=[90/100-1,209/(90+100)-1]
        self.assertAlmostEqual(max_dd(r),-.1)
        self.assertAlmostEqual(cagr(r,ann=2),-.01)
    def test_invalid_returns_never_publish_performance(self):
        from align import cagr, max_dd, sharpe
        for fn in (cagr,max_dd,sharpe):
            for values in ([],[float('nan')],[float('inf')],[-1.01],[[.01,.02]]):
                with self.subTest(fn=fn.__name__,values=values):
                    with self.assertRaises(ValueError):fn(values)
    def test_complete_loss_stays_complete_loss(self):
        from align import cagr,max_dd
        self.assertEqual(cagr([.1,-1,.2]),-1)
        self.assertEqual(max_dd([.1,-1,.2]),-1)

if __name__=='__main__':unittest.main()
