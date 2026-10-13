"""B migration: state branches, exact indicators and archived execution evidence."""
import copy
import math
import pathlib
import sys
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'paper'));sys.path.insert(0,str(ROOT/'tests'))
import ma50_core as core
import ma50_live as live
from align import panel, simulate
import test_ma50 as regression
from test_ma50 import bars, positions, funding
from test_daily_check import decision, book, fill, NOW
import daily_check as daily
import execution_ledger as ledger


class SignalTests(unittest.TestCase):
    def values(self, **changes):
        result=dict(close=100,ma35=99,ma40=101,volume_ratio10=1,vwma10=99)
        return result | changes
    def test_entry_does_not_require_ma40(self):
        self.assertEqual(core.signal_decision(self.values(),0,.6),(True,'in_market','entry'))
        self.assertEqual(core.signal_decision(self.values(),.1,.6),(False,'exit_ma40','holding'))
    def test_held_position_ignores_all_entry_filters(self):
        x=self.values(ma35=110,ma40=99,volume_ratio10=.1,vwma10=110)
        self.assertEqual(core.signal_decision(x,.1,.6),(True,'in_market','holding'))
        self.assertFalse(core.signal_decision(x,0,.6)[0])
    def test_equality_boundaries(self):
        self.assertEqual(core.signal_decision(self.values(ma35=100),0,.6)[1],'entry_ma35')
        self.assertEqual(core.signal_decision(self.values(ma40=100),.1,.6)[1],'exit_ma40')
        self.assertEqual(core.signal_decision(self.values(vwma10=100),0,.6)[1],'entry_vwma')
        self.assertTrue(core.signal_decision(self.values(),0,.6)[0])
        self.assertEqual(core.signal_decision(self.values(volume_ratio10=.99999),0,.6)[1],'entry_volume')
    def test_risk_cap_kept_in_both_branches(self):
        for held in (0,.1):
            x=self.values(ma40=99)
            self.assertTrue(core.signal_decision(x,held,1.2)[0])
            self.assertEqual(core.signal_decision(x,held,1.200001)[1],'vol_cap')
    def test_unknown_position_is_not_flat(self):
        self.assertEqual(core.signal_decision(self.values(),None,.6),(None,'position_unknown','unknown'))
        sig=live.signal_of(bars())
        self.assertIsNone(sig['long']);self.assertEqual(sig['signal_branch'],'unknown')
    def test_exact_volume_and_vwma_windows(self):
        data=bars();data[-11]['v']=2000;data[-1]['v']=3000
        x=core.signal_indicators(data)
        self.assertAlmostEqual(x['volume_mean10'],1100)
        self.assertAlmostEqual(x['volume_ratio10'],3000/1100)
        expected=math.fsum(b['c']*b['v'] for b in data[-10:])/12000
        self.assertAlmostEqual(x['vwma10'],expected)
        self.assertAlmostEqual(x['ma35'],math.fsum(b['c'] for b in data[-35:])/35)
        self.assertAlmostEqual(x['ma40'],math.fsum(b['c'] for b in data[-40:])/40)
    def test_invalid_volume_cannot_create_signal(self):
        for bad in (None,-1,float('nan'),float('inf')):
            data=bars();data[-1]['v']=bad
            with self.assertRaises((ValueError,TypeError)):live.signal_of(data,0)
        data=bars()
        for b in data[-11:]:b['v']=0
        with self.assertRaises(ValueError):live.signal_of(data,0)
        data=bars();del data[-1]['v']
        with self.assertRaises(KeyError):live.signal_of(data,0)
    def test_only_forty_bars_required_and_ma50_not_relabelled(self):
        sig=live.signal_of(bars(40),0)
        self.assertIsNotNone(sig);self.assertIsNone(sig['ma50'])
        self.assertIsNone(live.signal_of(bars(39),0))
        sig=live.signal_of(bars(),0)
        self.assertAlmostEqual(sig['ma50'],math.fsum(b['c'] for b in bars()[-50:])/50)
    def test_price_only_backtest_rejected(self):
        data=bars()
        with self.assertRaises(ValueError):panel([b['c'] for b in data],[0]*len(data))
        bad=copy.deepcopy(data);del bad[-2]['v']
        with self.assertRaises(KeyError):simulate(bad,funding(data))


class LiveBTests(regression.Isolated):
    run_flow = regression.FlowTests.run_flow
    def save_exit(self, **changes):
        _,rec=self.run_flow()
        rec.update(decision_id='prior-exit',decision_ms=str(self.now-1000),
                   validation_status='order',side='SELL',target_qty='0',
                   held_qty_at_signal='.04',order_qty='.04')
        rec.update(changes)
        ledger.append_decision(live.ARCHIVE.with_name('ma50_decisions.json'),rec)
        return rec
    def test_volume_failure_blocks_entry_but_not_held_position(self):
        data=copy.deepcopy(self.data);data[-1]['v']=100
        _,flat=self.run_flow(data,poss=positions(long=0))
        self.assertEqual(flat['decision_reason'],'entry_volume')
        self.assertEqual(flat['signal_branch'],'entry');self.assertEqual(flat['target_qty'],'0.000')
        _,held=self.run_flow(data,poss=positions(long=.04))
        self.assertEqual(held['decision_reason'],'in_market');self.assertEqual(held['signal_branch'],'holding')
        self.assertEqual(held['strategy_id'],core.STRATEGY_ID)
    def test_account_failure_never_archives_flat_entry_branch(self):
        for fail in ('account','positions','commission','orders','algos'):
            _,rec=self.run_flow(fail=fail)
            self.assertEqual(rec['signal_branch'],'unknown');self.assertEqual(rec['signal'],'未核验持仓')
            self.assertEqual(rec['order_qty'],'')
    def test_migration_keeps_ma50_field_diagnostic(self):
        _,rec=self.run_flow()
        self.assertNotEqual(rec['ma50'],f"{float(rec['ma35']):.2f}")
        self.assertEqual(rec['ma50'],f"{math.fsum(b['c'] for b in self.data[-50:])/50:.2f}")
    def test_same_day_rule_migration_cannot_certify_b_execution(self):
        prior=decision(decision_id='old',decision_ms=str(NOW-60000),rule_version='old-ma50')
        rec=decision(rule_version='new-b',strategy_id=core.STRATEGY_ID,signal_branch='entry')
        check=daily.execution_check(rec,[prior],book([fill()]),True)
        self.assertTrue(check['rule_changed']);self.assertIn('旧建议',check['match'])
        out,status=daily.operation_summary(rec,check,{},True)
        self.assertEqual(status,'review');self.assertIn('策略B',out)
    def test_same_bar_exit_does_not_invite_immediate_reentry(self):
        prior=decision(decision_id='exit',decision_ms=str(NOW-60000),
                       side='SELL',target_qty='0',strategy_id=core.STRATEGY_ID)
        rec=decision(strategy_id=core.STRATEGY_ID,signal_branch='entry',held_qty_at_signal='0')
        check=daily.execution_check(rec,[prior],book([fill(side='SELL')]),True)
        self.assertTrue(check['same_bar_exit'])
        out,status=daily.operation_summary(rec,check,{},True)
        self.assertEqual(status,'review');self.assertIn('等待下一根',out)
        later=decision(strategy_id=core.STRATEGY_ID,signal_branch='entry',bar_ms=str(int(rec['bar_ms'])+86400000))
        self.assertFalse(daily.execution_check(later,[prior],book([fill(side='SELL')]),True)['same_bar_exit'])
    def test_guard_survives_missing_or_not_yet_synced_fills(self):
        prior=decision(decision_id='exit',decision_ms=str(NOW-60000),
                       side='SELL',target_qty='0',strategy_id=core.STRATEGY_ID)
        rec=decision(strategy_id=core.STRATEGY_ID,signal_branch='entry',held_qty_at_signal='0')
        for evidence,ok in ((book(),True),(None,False)):
            check=daily.execution_check(rec,[prior],evidence,ok)
            self.assertTrue(check['same_bar_exit'])
            out,state=daily.operation_summary(rec,check,{},ok)
            self.assertEqual(state,'review');self.assertIn('等待下一根',out)
    def test_standalone_check_blocks_same_bar_entry_without_ledger(self):
        self.save_exit()
        out,rec=self.run_flow(poss=positions(long=0))
        self.assertEqual(rec['validation_status'],'blocked')
        self.assertEqual(rec['execution_guard'],'same_bar_exit')
        self.assertEqual(rec['order_qty'],'');self.assertNotIn('【买入',out)
        self.assertIn('等待下一根',out)
        self.assertEqual(rec['entry_eligible'],'True')  # Raw signal stays distinct from execution permission.
        ledger.append_decision(live.ARCHIVE.with_name('ma50_decisions.json'),rec)
        self.assertEqual(self.run_flow(poss=positions(long=0))[1]['execution_guard'],'same_bar_exit')
    def test_prior_reduction_or_other_bar_account_strategy_does_not_block_entry(self):
        options=[dict(target_qty='.01'),dict(bar_ms=str(self.data[-1]['t']-86400000)),
                 dict(account_scope='other'),dict(strategy_id='old-ma50'),dict(validation_status='blocked')]
        for changes in options:
            path=live.ARCHIVE.with_name('ma50_decisions.json')
            if path.exists():path.unlink()
            self.save_exit(**changes)
            _,rec=self.run_flow(poss=positions(long=0))
            self.assertEqual(rec['validation_status'],'order',changes)
            self.assertEqual(rec['side'],'BUY',changes)
    def test_partial_exit_still_allows_risk_reduction(self):
        self.save_exit()
        _,rec=self.run_flow(bars(rising=False),poss=positions(long=.01))
        self.assertEqual(rec['validation_status'],'order');self.assertEqual(rec['side'],'SELL')
    def test_corrupt_history_blocks_new_entry_but_not_exit(self):
        live.ARCHIVE.with_name('ma50_decisions.json').write_text('{broken')
        out,rec=self.run_flow(poss=positions(long=0))
        self.assertEqual(rec['validation_status'],'blocked');self.assertNotIn('【买入',out)
        _,rec=self.run_flow(bars(rising=False))
        self.assertEqual(rec['validation_status'],'order');self.assertEqual(rec['side'],'SELL')
    def test_future_or_bad_b_history_blocks_new_entry(self):
        for changes in (dict(decision_ms=str(self.now+1)),dict(target_qty='nan'),dict(bar_ms='invalid')):
            path=live.ARCHIVE.with_name('ma50_decisions.json')
            if path.exists():path.unlink()
            self.save_exit(**changes)
            _,rec=self.run_flow(poss=positions(long=0))
            self.assertEqual(rec['validation_status'],'blocked',changes)
    def test_unknown_account_scope_cannot_bypass_guard(self):
        with self.assertRaises(ledger.DataError):ledger.has_exit_on_bar([],None,0,NOW,core.STRATEGY_ID)
    def test_full_report_identifies_current_b_strategy(self):
        report=daily.render_report('summary','details','','')
        self.assertIn('B策略计算明细',report);self.assertNotIn('MA50计算明细',report)


if __name__=='__main__':unittest.main()
