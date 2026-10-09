"""Daily workflow tests use isolated files and fake read-only clients."""
import contextlib
import copy
import datetime as dt
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'paper'));sys.path.insert(0,str(ROOT))
import daily_check as daily
import ma50_live as ma
import dd_live as dd
from dd_support import atomic_write
DAY=86400000
START=int(dt.datetime(2026,10,9,tzinfo=dt.UTC).timestamp()*1000)
NOW=START+3600000
SCOPE='scope-a'


def decision(**changes):
    row=dict(decision_id='current',decision_ms=str(NOW),account_scope=SCOPE,symbol='ETHUSDT',
             bar_ms=str(START-DAY),date='2026-10-08',validation_status='order',action='加多',
             side='BUY',position_side='LONG',order_qty='.01',target_qty='.05',
             held_qty_at_signal='.04',available_at_signal='50',equity_at_signal='100',
             reference_price='2000',target_position='1',realized_vol_pct='60',
             rule_version='same',decision_reason='in_market',notes='增仓')
    row.update(changes);return row


def book(fills=None,**changes):
    row=dict(account_scope=SCOPE,trade_ranges=[[START,NOW-1000]],trades=fills or [])
    row.update(changes);return row


def fill(**changes):
    row=dict(symbol='ETHUSDT',time=NOW-2000,side='BUY',positionSide='LONG',qty='.01',orderId=1)
    row.update(changes);return row


class EvidenceTests(unittest.TestCase):
    def prior(self,**kw):
        values=dict(decision_id='prior',decision_ms=str(NOW-60000));values.update(kw)
        return decision(**values)
    def test_running_does_not_mean_execution(self):
        c=daily.execution_check(decision(),[self.prior()],book(),True)
        self.assertEqual(c['prior_runs'],1);self.assertEqual(c['fills'],0)
        self.assertTrue(c['evidence_ok']);self.assertIn('未见',c['match'])
    def test_fills_not_orders_and_partial_execution(self):
        c=daily.execution_check(decision(),[self.prior()],book([fill(qty='.003'),fill(qty='.002')]),True)
        self.assertEqual(c['fills'],2);self.assertEqual(c['orders'],1)
        self.assertIn('可能部分执行',c['match'])
    def test_quantity_match_is_only_candidate(self):
        c=daily.execution_check(decision(),[self.prior()],book([fill()]),True)
        self.assertIn('相符',c['match']);self.assertIn('候选',c['match'])
        self.assertIn('未确认',c['attribution'])
    def test_opposing_trades_cannot_be_netted_into_execution(self):
        c=daily.execution_check(decision(),[self.prior()],
            book([fill(qty='.02'),fill(side='SELL',qty='.01',orderId=2)]),True)
        self.assertIn('不能自动归因',c['match'])
    def test_excess_quantity_warning(self):
        c=daily.execution_check(decision(),[self.prior()],book([fill(qty='.02')]),True)
        self.assertIn('超过',c['match'])
    def test_no_advice_but_trade_requires_manual_review(self):
        c=daily.execution_check(decision(),[self.prior(validation_status='hold',order_qty='')],book([fill()]),True)
        self.assertIn('人工操作',c['match'])
    def test_account_scopes_never_mix(self):
        c=daily.execution_check(decision(),[self.prior(account_scope='other')],book(account_scope='other'),True)
        self.assertEqual(c['prior_runs'],0);self.assertEqual(c['fills'],0);self.assertFalse(c['evidence_ok'])
    def test_gap_and_stale_cache_are_not_no_execution_proof(self):
        for b in (book(trade_ranges=[[START,START+1000],[NOW-10000,NOW-1000]]),
                  book(trade_ranges=[[START,NOW-16*60000]])):
            c=daily.execution_check(decision(),[self.prior()],b,True)
            self.assertFalse(c['evidence_ok']);self.assertNotIn('未见',c['match'])
    def test_sync_failure_does_not_accept_old_book(self):
        c=daily.execution_check(decision(),[],book(),False)
        self.assertFalse(c['evidence_ok'])
    def test_future_cutoff_or_decision_is_invalid(self):
        for ds,b in (([],book(trade_ranges=[[START,NOW+1]])),
                     ([self.prior(decision_ms=str(NOW+1))],book())):
            c=daily.execution_check(decision(),ds,b,True)
            self.assertFalse(c['evidence_ok'])
    def test_session_boundary_is_0800_beijing_not_midnight(self):
        now=START+20*3600000 # Next Beijing date 04:00, still same strategy session.
        prev=self.prior(decision_ms=str(START+13*3600000))
        c=daily.execution_check(decision(decision_ms=str(now)),[prev],book(trade_ranges=[[START,now]]),True)
        self.assertEqual(c['prior_runs'],1);self.assertEqual(c['session_start_ms'],START)
        c=daily.execution_check(decision(decision_ms=str(START+DAY)),[prev],book(),True)
        self.assertEqual(c['prior_runs'],0)
    def test_latest_hold_does_not_attribute_to_older_order(self):
        ds=[self.prior(),self.prior(decision_id='hold',decision_ms=str(NOW-30000),validation_status='hold',order_qty='')]
        c=daily.execution_check(decision(),ds,book([fill()]),True)
        self.assertEqual(c['last_decision_id'],'hold');self.assertIn('没有可执行',c['match'])
    def test_cashflow_or_price_change_is_observation_not_causal_claim(self):
        c=daily.execution_check(decision(),[self.prior(equity_at_signal='90',reference_price='2100')],book(),True)
        self.assertTrue(any('账户权益' in x for x in c['changes']))
        self.assertTrue(any('参考价' in x for x in c['changes']))


class SummaryTests(unittest.TestCase):
    def show(self,rec,check=None,ok=True):
        check=check or daily.execution_check(rec,[],book([fill()]),ok)
        return daily.operation_summary(rec,check,{'account_scope':SCOPE,'nav_dd':-.1},ok)
    def test_regular_repeat_requests_review(self):
        out,status=self.show(decision())
        self.assertEqual(status,'review');self.assertIn('先复核',out)
        self.assertIn('0.05 ETH',out)
    def test_exit_not_hidden_by_same_day_trade_or_sync_failure(self):
        rec=decision(side='SELL',target_qty='0',target_position='0',order_qty='.04',decision_reason='trend_off')
        for ok in (True,False):
            out,state=self.show(rec,ok=ok)
            self.assertIn('优先核对并卖出平多',out);self.assertIn('0.00 ETH',out)
    def test_overexposure_reduction_keeps_priority(self):
        rec=decision(side='SELL',held_qty_at_signal='.2',target_qty='.149',target_position='2.99',order_qty='.051')
        out,_=self.show(rec)
        self.assertIn('优先核对并卖出减多',out)
    def test_hold_displays_actual_not_unreachable_target(self):
        rec=decision(validation_status='hold',action='不动',side='',order_qty='',target_qty='.043')
        out,state=self.show(rec)
        self.assertIn('本次保持 0.04 ETH',out);self.assertNotIn('本次保持 0.043',out)
    def test_blocked_never_shows_an_executable_order(self):
        out,state=self.show(decision(validation_status='blocked',held_qty_at_signal='',order_qty=''))
        self.assertEqual(state,'blocked');self.assertIn('暂停数量建议',out)
        self.assertNotIn('今日动作：买入',out)
    def test_no_account_scope_does_not_show_other_account_drawdown(self):
        rec=decision(account_scope='other')
        c=daily.execution_check(rec,[],None,False)
        out,_=daily.operation_summary(rec,c,{'account_scope':SCOPE,'nav_dd':-.9},True)
        self.assertNotIn('-90.00%',out)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.order=[];self.saved=[]
        self.rec=decision(validation_status='hold',action='不动',side='',order_qty='')
        def sync(argv,result):
            self.assertEqual(argv,['--snapshot']);self.order.append('dd')
            result.update(ledger=book(),account_scope=SCOPE,nav_dd=-.1);print('DD DETAIL');return 0
        def run(args):
            self.assertTrue(args.check and args.archive);self.order.append('ma')
            return 'MA DETAIL',dict(self.rec)
        def archive(rec):
            self.order.append('archive');self.saved.append(dict(rec))
        self.live=SimpleNamespace(ARCHIVE=self.root/'data/live/ma50_log.csv',ROOT=self.root,
                                  run=run,do_archive=archive,_atomic_write=atomic_write)
        self.dd=SimpleNamespace(main=sync)
    def tearDown(self):self.tmp.cleanup()
    def invoke(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code=daily._run_daily(SimpleNamespace(console=False),self.live,self.dd)
        self.output=output.getvalue()
        self.reports=list((self.root/'data/reports').glob('daily_*.json'))
        return code
    def test_dd_first_then_fresh_plan_archive_and_report(self):
        self.assertEqual(self.invoke(),0)
        self.assertEqual(self.order,['dd','ma','archive'])
        report=json.loads(self.reports[0].read_text(encoding='utf-8-sig'))
        self.assertEqual(report['decision']['daily_prior_runs'],'0')
        self.assertEqual(report['status'],'ready');self.assertIn('每日操作摘要',self.output)
        self.assertNotIn('DD DETAIL',self.output)
    def test_dd_failure_still_generates_ma_exit_but_returns_failure(self):
        def fail(argv,result):raise TimeoutError('sync unavailable')
        self.dd.main=fail
        self.rec.update(validation_status='order',side='SELL',order_qty='.04',target_qty='0',target_position='0')
        self.assertEqual(self.invoke(),1);self.assertIn('优先核对并卖出平多',self.output)
        self.assertEqual(len(self.saved),1)
    def test_ma_failure_still_preserves_dd_report(self):
        self.live.run=lambda args:(_ for _ in ()).throw(ValueError('bad candles'))
        self.assertEqual(self.invoke(),1);self.assertEqual(self.saved,[])
        text=self.reports[0].with_suffix('.md').read_text(encoding='utf-8')
        self.assertIn('DD DETAIL',text);self.assertIn('bad candles',text)
    def test_archive_failure_cannot_report_complete_success(self):
        self.live.do_archive=lambda rec:(_ for _ in ()).throw(OSError('disk full'))
        self.assertEqual(self.invoke(),1)
        self.assertIn('记录保存失败',self.output)
        self.assertEqual(json.loads(self.reports[0].read_text(encoding='utf-8-sig'))['status'],'failed')
    def test_report_write_failure_keeps_summary_and_details_but_fails(self):
        for suffix in ('.md', '.json'):
            with self.subTest(suffix=suffix):
                def write(path, text):
                    if path.suffix == suffix: raise OSError('disk full')
                    atomic_write(path, text)
                self.live._atomic_write=write
                self.assertEqual(self.invoke(),1)
                self.assertIn('每日操作摘要',self.output)
                self.assertIn('日检报告未全部保存',self.output)
                self.assertIn('MA DETAIL',self.output);self.assertIn('DD DETAIL',self.output)
                self.assertNotIn('完整日检报告：',self.output)
                self.assertTrue(self.saved)
    def test_malformed_prior_file_is_review_not_clean_history(self):
        p=self.live.ARCHIVE.with_name('ma50_decisions.json');p.parent.mkdir(parents=True);p.write_text('{broken')
        self.assertEqual(self.invoke(),1);self.assertIn('同日记录无法核对',self.output)
    def test_daily_dispatch_uses_configured_target_and_forbids_other_modes(self):
        with patch.object(sys,'argv',['ma50_live.py','--daily','--target-vol','40']),patch.object(daily,'run_daily',return_value=0) as call:
            old=ma.TARGET_VOL_OVERRIDE
            try:
                self.assertEqual(ma.main(),0);self.assertEqual(ma.TARGET_VOL_OVERRIDE,.4);call.assert_called_once()
            finally:ma.TARGET_VOL_OVERRIDE=old
        with patch.object(sys,'argv',['ma50_live.py','--daily','--history']),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):ma.main()

if __name__=='__main__':unittest.main()
