"""Offline parity against frozen B research decisions; never reads credentials."""
import datetime as dt
import hashlib
import json
import pathlib
import sys
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'tests/fixtures/strategy_b'
sys.path.insert(0,str(ROOT/'paper'))
import ma50_live as live
import ma50_core as core


def verify():
    with patch.object(live,'RISK_OVERLAY','none'):
        return _verify_b()


def _verify_b():
    protected=[p for p in (ROOT/'data/live').glob('*') if p.is_file()]
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    frozen=FIXTURES/'ma50_backtest_20261008/input_daily.json'
    volume=FIXTURES/'ma50_volume_research_20261010/volume_fields.json'
    bars=json.loads(frozen.read_bytes())
    volumes={b['t']:b['v'] for b in json.loads(volume.read_bytes())}
    bars=[b | {'v':volumes[b['t']]} for b in bars]
    index={b['t']:i for i,b in enumerate(bars)}
    study=FIXTURES/'open_strategy_search_20261011'
    random=FIXTURES/'random_two_strategy_comparison_20261012'
    paths=[study/f'r2406v00_slip{s}_decisions.json' for s in (2,7)]
    paths+=sorted(random.glob('r2406v00_random*_slip*_decisions.json'))
    assert len(paths)==14
    summary=[];maximum=0.;total=orders=0
    for path in paths:
        slip=int(path.stem.split('_slip')[1].split('_')[0])/10000
        fee=.0005+slip
        decisions=json.loads(path.read_bytes())
        trades=json.loads(path.with_name(path.name.replace('_decisions','_trades')).read_bytes())
        trade_index={t['time']:t for t in trades if t['reason']!='terminal_close'}
        count=0
        for d in decisions:
            i=index[d['time']//86400000*86400000]-1
            sig=live.signal_of(bars[:i+1],d['q'])
            weight=core.strategy_position(sig['long'],sig['rvol'],d['equity'],fee=fee)[0]
            err=abs(weight-d['weight']);maximum=max(maximum,err)
            assert err<1e-10,(path.name,d['time'],weight,d['weight'])
            assert abs(sig['rvol']-d['vol'])<1e-12
            plan=core.plan_order(d['equity'],d['price'],weight,
                                 core.PositionState(long_qty=d['q']),core.ExchangeRules(),d['free'],fee)
            trade=trade_index.get(d['time'])
            assert plan.actionable == (trade is not None),(path.name,d['time'],plan,trade)
            if trade:
                delta=plan.quantity if plan.side=='BUY' else -plan.quantity
                assert abs(delta-trade['delta'])<1e-9,(path.name,d['time'],delta,trade['delta'])
                count+=1
        total+=len(decisions);orders+=count
        summary.append(dict(path=str(path.relative_to(ROOT)),decisions=len(decisions),orders=count))
    assert before=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    result=dict(strategy_id=core.STRATEGY_ID,created_utc=dt.datetime.now(dt.UTC).isoformat(),
                paths=len(paths),decisions=total,orders=orders,max_weight_error=maximum,
                matched=True,live_records_unchanged=True,details=summary,
                inputs={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (frozen,volume)})
    output=ROOT/'data/reports/strategy_b_migration_20261012';output.mkdir(exist_ok=True)
    (output/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('details','inputs')},ensure_ascii=False))
    return result


if __name__=='__main__':verify()
