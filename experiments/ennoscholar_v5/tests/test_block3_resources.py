"""Canonical commitments: simulated calls and temporary application SQL only."""
import copy
from datetime import datetime, timezone, timedelta
from dataclasses import replace

import pytest

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5 import writing_job, persistence
from agents.EnnoScholarV5.config import runtime_config, ConfigurationError
from agents.EnnoScholarV5.persistence import Budget, RunMeter
from agents.EnnoScholarV5.session import ScholarV4Session, ScholarV4Run
from agents.EnnoScholarV5.finance import BudgetRefused
from experiments.ennoscholar_v5.progress import Scope
from .test_live_integration import app_env


def setup_mission(app_env, monkeypatch, **limits):
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD', '3')
    make, factory, _ = app_env
    cfg = ScholarV4Config(api_key='offline', web_search_enabled=False, **limits)
    svc, sid, tr = make([], config=cfg)
    rid = svc.open_run(1, sid, "Rédige l'état de l'art complet.")
    meter = RunMeter(svc, Scope(1, sid), rid, cfg)
    return svc, sid, rid, meter, factory, tr


def next_segment(svc, sid, rid, factory):
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        data = copy.deepcopy(row.data)
        job = data['writing_job']
        job.update(status='waiting', current_run_id=None, initializing=True)
        job['segments'].append({'run_id': rid, 'status': 'partial', 'spent': {}, 'saved_sections': []})
        row.data, row.active_run_id = data, None
        db.get(ScholarV4Run, rid).status = 'partial'
        db.commit()
    return svc.open_run(1, sid, '', job_id=job['id'])


def job_of(factory, sid):
    with factory() as db:
        return copy.deepcopy(db.get(ScholarV4Session, sid).data['writing_job'])


def admit(meter, target='call', tokens=25, usd=.01):
    return meter.admit('v5_science', target, '', 'gpt-6.1-sol', usd,
        {'estimated_input_tokens': tokens-5, 'max_output_tokens': 5})


def test_block3_tools_and_web_are_global_across_segments(app_env, monkeypatch):
    svc, sid, rid, first, factory, tr = setup_mission(app_env, monkeypatch, max_tool_calls=4, max_web_calls=1)
    first.record_tools(1, web=1, operation_id='first-tools')
    rid2 = next_segment(svc, sid, rid, factory)
    job = job_of(factory, sid)
    capped = writing_job.segment_config(svc.config, job)
    assert (capped.max_tool_calls, capped.max_web_calls) == (2, 0)
    # Even an incorrectly larger per-run config cannot replenish the mission.
    second = RunMeter(svc, Scope(1, sid), rid2, svc.config)
    second.web_request = True
    with pytest.raises(BudgetRefused) as error:
        admit(second)
    assert error.value.explanation['axis'] == 'web'
    second.record_tools(2, operation_id='second-tools')
    with pytest.raises(BudgetRefused):
        second.record_tools(1, operation_id='excess')
    assert job_of(factory, sid)['mission']['tools'] == 4
    assert not tr.requests


def test_block3_uncertain_tokens_late_settlement_and_repeat_are_not_double_counted(app_env, monkeypatch):
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_MAX_TOKENS', '100')
    svc, sid, rid, first, factory, _ = setup_mission(app_env, monkeypatch)
    ticket = admit(first, tokens=60)
    first.mark_sent('call')
    first.unknown(ticket, 'timeout')
    assert job_of(factory, sid)['mission']['tokens'] == 60
    rid2 = next_segment(svc, sid, rid, factory)
    second = RunMeter(svc, Scope(1, sid), rid2, svc.config)
    with pytest.raises(BudgetRefused):
        admit(second, tokens=50)
    first.settle(ticket, .003, usage={'input_tokens': 12, 'output_tokens': 2})
    first.settle(ticket, .003, usage={'input_tokens': 12, 'output_tokens': 2})
    job = job_of(factory, sid)
    assert job['mission']['tokens'] == 14 and job['mission']['uncertain_tokens'] == 0
    assert job['spent']['confirmed_usd'] == pytest.approx(.003)
    assert job['spent']['uncertain_usd'] == 0
    admit(second, target='allowed', tokens=50)
    assert job_of(factory, sid)['mission']['tokens'] == 64


def test_block3_run_and_job_projection_share_one_commit_after_a_crash(app_env, monkeypatch):
    svc, sid, rid, meter, factory, _ = setup_mission(app_env, monkeypatch)
    ticket = admit(meter)
    original = persistence.refresh_job_resources
    with monkeypatch.context() as fault:
        fault.setattr(persistence, 'refresh_job_resources', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('crash between writes')))
        with pytest.raises(RuntimeError):
            meter.settle(ticket, .004, usage={'input_tokens': 10, 'output_tokens': 1})
    with factory() as db:
        assert db.get(Budget, rid).data['entries'][0]['status'] == 'reserved'
        assert db.get(ScholarV4Run, rid).data['ledger'][0]['status'] == 'reserved'
    resumed = RunMeter(svc, Scope(1, sid), rid, svc.config)
    resumed.settle(ticket, .004, usage={'input_tokens': 10, 'output_tokens': 1})
    resumed.settle(ticket, .004, usage={'input_tokens': 10, 'output_tokens': 1})
    with factory() as db:
        assert db.get(Budget, rid).data['entries'] == db.get(ScholarV4Run, rid).data['ledger']
    assert resumed.snapshot()['cost_usd'] == pytest.approx(.004)
    assert job_of(factory, sid)['resources']['confirmed_usd'] == pytest.approx(.004)


def test_block3_late_receipt_updates_an_archived_mission_without_touching_the_new_one(app_env,monkeypatch):
    svc,sid,rid,meter,factory,_=setup_mission(app_env,monkeypatch)
    ticket=admit(meter,tokens=60);meter.unknown(ticket,'timeout')
    with factory() as db:
        row=db.get(ScholarV4Session,sid);data=copy.deepcopy(row.data)
        old=data.pop('writing_job')
        old['segments']=[{'run_id':rid,'status':'partial','spent':{},'saved_sections':[]}]
        data['writing_job_history']=[old]
        data['writing_job']={'id':'new-job','marker':'untouched'}
        row.data=data;db.commit()
    meter.settle(ticket,.002,usage={'input_tokens':10,'output_tokens':2})
    with factory() as db:
        data=db.get(ScholarV4Session,sid).data
        assert data['writing_job']=={'id':'new-job','marker':'untouched'}
        assert data['writing_job_history'][0]['mission']['tokens']==12
        assert data['writing_job_history'][0]['spent']['confirmed_usd']==pytest.approx(.002)
        assert db.get(ScholarV4Run,rid).data['ledger']==db.get(Budget,rid).data['entries']


def test_block3_remaining_deadline_bounds_segment_and_call_admission(app_env, monkeypatch):
    svc, sid, rid, meter, factory, tr = setup_mission(app_env, monkeypatch)
    with factory() as db:
        row = db.get(ScholarV4Session, sid);data = copy.deepcopy(row.data)
        data['writing_job']['mission']['max_seconds'] = 600
        data['writing_job']['mission']['started_at'] = (datetime.now(timezone.utc)-timedelta(seconds=595)).isoformat()
        row.data = data;db.commit()
    cap = writing_job.segment_config(svc.config, job_of(factory, sid))
    assert 0 < cap.timeout_seconds <= 5
    assert 0 < meter.snapshot()['remaining_seconds'] <= 5
    with factory() as db:
        row = db.get(ScholarV4Session, sid);data = copy.deepcopy(row.data)
        data['writing_job']['mission']['started_at'] = (datetime.now(timezone.utc)-timedelta(seconds=601)).isoformat()
        row.data = data;db.commit()
    with pytest.raises(BudgetRefused):
        admit(meter)
    assert not tr.requests


def test_block3_hosted_web_receipt_is_atomic_and_not_counted_twice(app_env, monkeypatch):
    svc, sid, rid, meter, factory, _ = setup_mission(app_env, monkeypatch)
    meter.web_request = True
    ticket = admit(meter)
    meter.settle(ticket, .01, usage={'input_tokens': 10, 'output_tokens': 1}, meta={'parts': {'web_calls': .01}})
    meter.record_tools(0, web=1, operation_id='call:web')
    again = RunMeter(svc, Scope(1, sid), rid, svc.config)
    assert (again.snapshot()['web'], again.snapshot()['tools']) == (1, 1)
    assert job_of(factory, sid)['mission']['web'] == 1


def test_block3_optional_session_cap_counts_other_runs_without_changing_defaults(app_env, monkeypatch):
    monkeypatch.delenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD', raising=False)
    make, factory, _ = app_env;svc, sid, _ = make([])
    assert svc.session_budget(1, sid)['limit_usd'] is None
    svc.set_session_budget(1, sid, .02, expected_revision=0)
    rid = svc.open_run(1, sid, 'Bonjour');first = RunMeter(svc, Scope(1, sid), rid, svc.config)
    t = admit(first);first.settle(t, .015, usage={'input_tokens': 1, 'output_tokens': 1})
    with factory() as db:
        row = db.get(ScholarV4Session, sid);row.active_run_id = None
        db.get(ScholarV4Run, rid).status = 'completed';db.commit()
    rid2 = svc.open_run(1, sid, 'Autre demande');second = RunMeter(svc, Scope(1, sid), rid2, svc.config)
    with pytest.raises(BudgetRefused) as error:admit(second)
    assert error.value.explanation['refused_budget'] == 'v5_session'
    with pytest.raises(ValueError):svc.set_session_budget(1, sid, .03, expected_revision=0)
    svc.set_session_budget(1, sid, None, expected_revision=1)
    admit(second)


@pytest.mark.parametrize('field', ['max_cost_usd', 'timeout_seconds'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_block3_nonfinite_config_is_refused_at_the_v5_boundary(field, value):
    with pytest.raises(ConfigurationError):runtime_config(ScholarV4Config(api_key='offline', **{field: value}))


def test_block3_mission_decimal_integer_is_normalized(monkeypatch):
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_MAX_TOKENS', '2000000.0')
    assert writing_job.mission_limits()['max_tokens'] == 2000000
