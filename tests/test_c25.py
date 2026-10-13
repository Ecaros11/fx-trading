"""Closed-bar memory, budget-before-cap, manual-live archives and migration guard."""
import copy,math,pathlib,statistics,sys,unittest,json,contextlib,io
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'paper'));sys.path.insert(0,str(ROOT/'tests'))
import ma50_core as core
import ma50_live as live
import daily_check as daily
import execution_ledger as ledger
import test_ma50 as regression
from test_ma50 import bars,positions
from test_daily_check import decision,book,NOW

def sequence(returns):
    data=bars(len(returns)+1);price=100.
    for i,b in enumerate(data):
        if i:price*=1+returns[i-1]
        b.update(o=price,c=price,h=price*1.01,l=price*.99)
    return data

class C25FormulaTests(unittest.TestCase):
    def triggered(self):return sequence([0.001]*34+[-.03,.02,-.03,.02,-.03])
    def test_down3_is_net_fall_not_three_consecutive_down_days(self):
        data=self.triggered();x=core.c25_indicators(data)
        self.assertGreater(data[-2]['c'],data[-3]['c'])
        self.assertTrue(x['c25_down3']);self.assertTrue(x['c25_trigger']);self.assertEqual(x['c25_factor'],.25)
    def test_exact_simple_returns_sample_sd_and_prefix_invariance(self):
        data=self.triggered();x=core.c25_indicators(data)
        ret=[b['c']/a['c']-1 for a,b in zip(data,data[1:])]
        self.assertAlmostEqual(x['c25_sd5'],statistics.stdev(ret[-5:])*math.sqrt(365))
        self.assertAlmostEqual(x['c25_sd30'],statistics.stdev(ret[-30:])*math.sqrt(365))
        self.assertEqual(x,core.c25_indicators(copy.deepcopy(data)))
    def test_memory_is_latest_two_bars_and_expires_after_two_untriggered_bars(self):
        r=[.001]*34+[-.03,.02,-.03,.02,-.03];one=core.c25_indicators(sequence(r+[.15]))
        self.assertFalse(one['c25_trigger']);self.assertTrue(one['c25_previous_trigger']);self.assertEqual(one['c25_factor'],.25)
        two=core.c25_indicators(sequence(r+[.15,.15]));self.assertFalse(two['c25_active']);self.assertEqual(two['c25_factor'],1.)
        self.assertEqual(one,core.c25_indicators(sequence(r+[.15])))  # Invocation does not expire memory.
    def test_zero_sd30_is_defined_no_trigger_not_infinite_ratio(self):
        x=core.c25_indicators(sequence([0.]*39));self.assertIsNone(x['c25_ratio']);self.assertFalse(x['c25_active'])
    def test_bad_data_pauses_instead_of_disabling_c(self):
        for kind in ('short','gap','negative','nan'):
            data=self.triggered()
            if kind=='short':data=data[-31:]
            if kind=='gap':data[-10]['t']-=86400000
            if kind=='negative':data[-10]['c']=-1
            if kind=='nan':data[-10]['c']=float('nan')
            with self.assertRaises(ValueError):core.c25_indicators(data)
    def test_budget_before_leverage_cap_and_exit_priority(self):
        with patch.object(live,'RISK_OVERLAY','c25'),patch.object(live,'TARGET_VOL_OVERRIDE',.6):
            x=live.advice(1000,2000,.1,True,budget_factor=.25)
            self.assertAlmostEqual(x['position'],1.5);self.assertAlmostEqual(x['target_vol'],.15)
            self.assertIsNone(x['sharpe']);self.assertIsNone(x['drawdown'])
            self.assertEqual(live.advice(1000,2000,.1,False,budget_factor=.25)['position'],0)
            self.assertEqual(live.advice(1000,2000,1.21,True,budget_factor=.25)['position'],0)
    def test_recovery_restores_base_budget_without_changing_order_gate(self):
        with patch.object(live,'RISK_OVERLAY','c25'):
            self.assertAlmostEqual(live.advice(1000,2000,.6,True,budget_factor=1.)['position'],1.)
            p=core.plan_order(1000,1000,.99,core.PositionState(long_qty=1.),core.ExchangeRules(),1000)
            self.assertEqual(p.status,'hold')
    def test_known_position_still_selects_original_b_branch(self):
        with patch.object(live,'RISK_OVERLAY','c25'):
            data=bars();flat=live.signal_of(data,0);held=live.signal_of(data,.1);unknown=live.signal_of(data)
            self.assertEqual(flat['signal_branch'],'entry');self.assertEqual(held['signal_branch'],'holding');self.assertIsNone(unknown['long'])
            self.assertIn('c25_factor',unknown)

class C25LiveTests(regression.Isolated):
    risk_overlay='c25'
    run_flow=regression.FlowTests.run_flow
    def triggered_bars(self):
        data=json.loads((ROOT/'tests/fixtures/c25/20240315_closed_daily.json').read_bytes())
        shift=self.data[-1]['t']-data[-1]['t']
        for bar in data:
            bar['t']+=shift
            if 'closeTime' in bar:bar['closeTime']+=shift
        return data
    def test_real_trigger_flows_into_report_order_and_archive(self):
        data=self.triggered_bars()
        with patch.object(live,'RISK_OVERLAY','c25'):
            out,rec=self.run_flow(data)
        self.assertEqual(rec['c25_active'],'True');self.assertEqual(rec['risk_budget_factor'],'0.25')
        self.assertEqual(rec['target_vol'],'0.15');self.assertEqual(rec['decision_reason'],'in_market')
        expected=.15/live.realized_vol(data)
        self.assertAlmostEqual(float(rec['target_position']),expected,places=4)
        self.assertEqual(rec['side'],'SELL');self.assertIn('有效目标波动 15.0%',out)
        live.do_archive(rec);saved=ledger.load_decisions(live.ARCHIVE.with_name('ma50_decisions.json'))[-1]
        self.assertEqual(saved['target_vol'],'0.15');self.assertEqual(saved['c25_active'],'True')
    def test_opt_in_record_and_base_record_keep_distinct_identity(self):
        with patch.object(live,'RISK_OVERLAY','none'):_,base=self.run_flow()
        with patch.object(live,'RISK_OVERLAY','c25'):
            out,c=self.run_flow()
        self.assertEqual(base['strategy_id'],core.STRATEGY_ID);self.assertEqual(c['strategy_id'],core.C25_STRATEGY_ID)
        self.assertNotEqual(base['rule_version'],c['rule_version']);self.assertEqual(c['base_target_vol'],'0.6')
        self.assertIn('C25',out);self.assertIn('c25_previous_trigger',c)
        live.do_archive(c)
        rows=ledger.load_decisions(live.ARCHIVE.with_name('ma50_decisions.json'))
        self.assertEqual(rows[-1]['c25_trigger'],c['c25_trigger'])
    def test_c25_cannot_reopen_after_b_exit_same_bar(self):
        with patch.object(live,'RISK_OVERLAY','none'):_,old=self.run_flow()
        old.update(decision_id='exit',decision_ms=str(self.now-1000),validation_status='order',side='SELL',target_qty='0')
        ledger.append_decision(live.ARCHIVE.with_name('ma50_decisions.json'),old)
        with patch.object(live,'RISK_OVERLAY','c25'):
            _,rec=self.run_flow(poss=positions(long=0))
        self.assertEqual(rec['execution_guard'],'same_bar_exit');self.assertEqual(rec['validation_status'],'blocked')
    def test_base_cannot_reopen_after_c25_exit_same_bar(self):
        with patch.object(live,'RISK_OVERLAY','c25'):_,old=self.run_flow()
        old.update(decision_id='exit',decision_ms=str(self.now-1000),validation_status='order',side='SELL',target_qty='0')
        ledger.append_decision(live.ARCHIVE.with_name('ma50_decisions.json'),old)
        with patch.object(live,'RISK_OVERLAY','none'):_,rec=self.run_flow(poss=positions(long=0))
        self.assertEqual(rec['execution_guard'],'same_bar_exit')
    def test_daily_summary_identifies_c25_budget_not_fixed_quarter_position(self):
        rec=decision(strategy_id=core.C25_STRATEGY_ID,risk_overlay='c25',signal_branch='entry',c25_trigger='True',c25_previous_trigger='False',risk_budget_factor='.25',target_vol='.15')
        check=daily.execution_check(rec,[],book(),True)
        text,_=daily.operation_summary(rec,check,{},True);self.assertIn('B＋C25',text);self.assertIn('.25',text)
    def test_cli_rejects_unresearched_combination_and_b_stat_sync(self):
        for args in (['--target-vol','40'],['--risk-overlay','c25','--target-vol','40'],['--risk-overlay','c25','--sync-methods'],['--sync-methods']):
            with patch.object(sys,'argv',['ma50_live.py']+args):
                with self.assertRaises(SystemExit) as caught:live.main()
            self.assertEqual(caught.exception.code,2)
    def test_default_daily_matches_explicit_c25_records_and_summary(self):
        self.assertEqual(live.DEFAULT_RISK_OVERLAY,'c25')
        results=[]
        def dispatch(args,module):
            self.assertEqual(module.RISK_OVERLAY,'c25')
            text,rec=self.run_flow(self.triggered_bars());check=daily.execution_check(rec,[],book(),True)
            summary,_=daily.operation_summary(rec,check,{},True)
            self.assertIn('B＋C25',text);self.assertIn('B＋C25',summary)
            self.assertIn('B＋C25策略计算明细',daily.render_report(summary,text,'',''))
            live.do_archive(rec);results.append(rec);return 0
        for extra in ([],['--risk-overlay','c25']):
            with patch.object(sys,'argv',['ma50_live.py','--daily']+extra),patch.object(daily,'run_daily',side_effect=dispatch),self.quiet():
                self.assertEqual(live.main(),0)
        for key in ('strategy_id','rule_version','risk_overlay','target_position','target_vol','risk_budget_factor','side','order_qty','c25_active'):
            self.assertEqual(results[0][key],results[1][key])
        self.assertEqual(results[0]['strategy_id'],core.C25_STRATEGY_ID)
        self.assertEqual(results[0]['target_vol'],'0.15');self.assertEqual(results[0]['c25_active'],'True')
        saved=ledger.load_decisions(live.ARCHIVE.with_name('ma50_decisions.json'))
        self.assertEqual(len(saved),2);self.assertTrue(all(x['strategy_id']==core.C25_STRATEGY_ID for x in saved))
    def test_explicit_none_cli_is_required_to_return_to_b(self):
        def dispatch(args,module):
            self.assertEqual(module.RISK_OVERLAY,'none');self.assertEqual(module.strategy_id(),core.STRATEGY_ID);return 0
        with patch.object(sys,'argv',['ma50_live.py','--daily','--risk-overlay','none']),patch.object(daily,'run_daily',side_effect=dispatch):
            self.assertEqual(live.main(),0)
    def test_default_history_backfill_and_offline_selfcheck_dispatch(self):
        for flag,name in [('history','history'),('backfill','backfill')]:
            with patch.object(sys,'argv',['ma50_live.py','--'+flag]),patch.object(live,name) as handler:
                self.assertEqual(live.main(),0);handler.assert_called_once();self.assertEqual(live.RISK_OVERLAY,'c25')
        with patch.object(sys,'argv',['ma50_live.py','--selfcheck','--offline']),patch.object(live,'selfcheck_c25',return_value=True) as handler,patch.object(live,'cached_rules',return_value=(core.ExchangeRules(),'fixture')):
            self.assertEqual(live.main(),0);handler.assert_called_once()
    def test_c25_selfcheck_ignores_b_table_but_detects_wrong_budget(self):
        with patch.object(live,'METHODS',[]),self.quiet():self.assertTrue(live.selfcheck())
        original=live.advice
        def wrong(*args,**kwargs):
            x=original(*args,**kwargs);return x|{'position':x['position']+.1}
        with patch.object(live,'advice',side_effect=wrong),self.quiet():self.assertFalse(live.selfcheck())

if __name__=='__main__':unittest.main()
