"""Sanitized provider errors, scoped API budgets, telemetry and consistent config."""
import copy
import json
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5.config import ConfigurationError,runtime_config
from agents.EnnoScholarV5.persistence import Budget
from agents.EnnoScholarV5.service import SessionBusy
from .test_live_integration import app_env,run_sync,call,NoSocketLoop,scientific_state
from .test_plan_edit_scope import lithium,outline
from .test_startup import local_install,configure


def test_block3_401_is_released_and_never_displays_historical_bibliography(app_env):
    make,factory,_=app_env
    error=httpx.Response(401,json={'error':{'message':'do not persist confidential fixture-key','type':'invalid_api_key'}})
    before=lithium();svc,sid,tr=make([error],before)
    result=run_sync(svc,sid,'Analyse en profondeur un nouvel article scientifique.')
    assert len(tr.requests)==1 and result['outcome']=='provider_authentication_error'
    assert '401' in result['answer'] and 'confidential' not in json.dumps(result)
    assert 'Notices réunies' not in result['answer'] and 'Lithium' not in result['answer']
    assert result['diagnostic']['retryable'] is False
    assert result['usage']['estimated_cost_usd']==0 and result['usage']['uncertain_tokens']==0
    with factory() as db:assert db.get(Budget,result['id']).data['entries'][0]['status']=='released'
    data=scientific_state(factory,sid)
    assert data['articles']==before['articles'] and data['outline']==before['outline']


def test_block3_timeout_preserves_uncertainty_and_blocks_automatic_retry(app_env):
    make,factory,_=app_env
    error=httpx.ReadTimeout('do not persist timeout secret',request=httpx.Request('POST','https://api.openai.com/v1/responses'))
    svc,sid,tr=make([error])
    result=run_sync(svc,sid,'Recherche les travaux pertinents.')
    assert len(tr.requests)==1 and result['outcome']=='provider_timeout'
    assert result['usage']['uncertain_tokens']>0 and result['usage']['uncertain_usd']>0
    assert 'secret' not in json.dumps(result)
    assert result['diagnostic']['retryable'] is False
    with pytest.raises(SessionBusy,match='incertain'):svc.resume_run(1,result['id'])
    assert len(tr.requests)==1


def test_block3_per_call_metrics_compare_estimation_to_returned_usage(app_env):
    make,factory,_=app_env;state=lithium()
    svc,sid,tr=make([call('manage_outline',outline('rename',state['outline']['sections'][2]['id'],'Limites'))],state)
    result=run_sync(svc,sid,'Renomme la section 3 en Limites.')
    assert len(result['call_metrics'])==1
    metrics=result['call_metrics'][0]['request_metrics']
    assert metrics['task']=='plan_edit' and metrics['phase']=='decision'
    assert metrics['actual_input_tokens']==100 and metrics['actual_output_tokens']==50
    assert metrics['max_output_tokens']==900 and metrics['block_bytes']['tools']>0
    assert metrics['input_estimation_error']==metrics['point_input_tokens']-100
    assert not metrics['billing_guarantee']
    assert result['resource_scopes']['session_resources']['confirmed_tokens']==150


def test_block3_session_budget_endpoint_is_explicit_owned_and_v5_only(app_env,monkeypatch):
    from routers import scholar_v5
    from core.deps import get_current_user
    make,factory,_=app_env;svc,sid,tr=make([])
    monkeypatch.setenv('ENNOSCHOLAR_V5_TEST_ENABLED','1')
    monkeypatch.setattr(scholar_v5,'get_service',lambda:svc)
    app=FastAPI();app.include_router(scholar_v5.router)
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=1)
    client=TestClient(app,backend_options={'loop_factory':NoSocketLoop})
    path=f'/scholar-v5/sessions/{sid}/resource-budget'
    assert client.get(path).json()['limit_usd'] is None
    assert client.patch(path,json={'limit_usd':.4,'expected_revision':0}).json()['limit_usd']==.4
    assert client.patch(path,json={'limit_usd':.3,'expected_revision':0}).status_code==422
    assert client.patch(path,json={'limit_usd':-1,'expected_revision':1}).status_code==422
    assert client.patch(path,json={'limit_usd':None,'expected_revision':1}).json()['limit_usd'] is None
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=2)
    assert client.get(path).status_code==404
    monkeypatch.setenv('ENNOSCHOLAR_V5_TEST_ENABLED','0')
    assert client.get(path).status_code==404
    assert not tr.requests


@pytest.mark.parametrize('value',['nan','inf','-inf','-1','0','invalid','1.5'])
def test_block3_invalid_integer_caps_are_refused_in_local_and_env_profiles(local_install,monkeypatch,value):
    env={'ENNOSCHOLAR_V4_MAX_TOKENS':value}
    before=copy.deepcopy(env)
    with pytest.raises(ValueError):configure(local_install,env)
    assert env==before
    monkeypatch.setenv('ENNOSCHOLAR_V4_MAX_TOKENS',value)
    with pytest.raises(ConfigurationError):runtime_config()


def test_block3_integer_decimal_and_optional_caps_are_same_in_local_and_env(local_install,monkeypatch):
    env={'ENNOSCHOLAR_V4_MAX_TOKENS':'200000.0','ENNOSCHOLAR_V5_MISSION_MAX_TOKENS':'2000000.0',
        'ENNOSCHOLAR_V5_MISSION_MAX_TOOLS':'40.0','ENNOSCHOLAR_V5_SESSION_BUDGET_USD':'1.25'}
    info=configure(local_install,env)
    assert env['ENNOSCHOLAR_V4_MAX_TOKENS']=='200000' and env['ENNOSCHOLAR_V5_MISSION_MAX_TOKENS']=='2000000'
    for key in ('ENNOSCHOLAR_V4_MAX_TOKENS','ENNOSCHOLAR_V5_MISSION_MAX_TOKENS',
                'ENNOSCHOLAR_V5_MISSION_MAX_TOOLS','ENNOSCHOLAR_V5_SESSION_BUDGET_USD'):monkeypatch.setenv(key,env[key])
    cfg=runtime_config()
    assert (cfg.max_tokens,cfg.mission_max_tool_calls,cfg.session_budget_usd)==(200000,40,1.25)


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-.1])
def test_block3_invalid_session_default_is_not_silently_accepted(value):
    from agents.EnnoScholarV5.config import ScholarV5Config
    with pytest.raises(ConfigurationError):runtime_config(ScholarV5Config(api_key='offline',session_budget_usd=value))
