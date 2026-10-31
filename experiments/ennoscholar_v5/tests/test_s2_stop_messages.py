"""S2: actual LangGraph/service/ASGI messages, isolated SQL and simulated Responses.

Financial refusals are injected at the RunMeter admission boundary: no ceiling is
changed and no HTTP is sent. Other stops cross the SDK's mock HTTP transport.
The exported API messages are rendered by check_s2_ui.cjs with the actual chat
Response and Markdown components, without starting the application.
"""
import copy
from datetime import datetime,timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.EnnoScholarV5.persistence import RunMeter,Budget
from agents.EnnoScholarV5.finance import BudgetRefused
from agents.EnnoScholarV5.session import ScholarV4Run,ScholarV4Session
from experiments.ennoscholar_v5.progress import Scope
from .test_live_integration import app_env,call,run_sync,scientific_state,NoSocketLoop
from .test_plan_edit_scope import lithium,outline

UI_CASES=Path(__file__).resolve().parents[1]/'.artifacts/s2-ui-cases.json'


@pytest.fixture(scope='module',autouse=True)
def fresh_export():
    UI_CASES.write_text('{}',encoding='utf8')


def visible_api(svc,sid,result,monkeypatch,name,expected):
    from routers import scholar_v5
    from core.deps import get_current_user
    monkeypatch.setenv('ENNOSCHOLAR_V5_TEST_ENABLED','1')
    monkeypatch.setattr(scholar_v5,'get_service',lambda:svc)
    app=FastAPI();app.include_router(scholar_v5.router)
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=1)
    client=TestClient(app,backend_options={'loop_factory':NoSocketLoop})
    run=client.get('/scholar-v5/runs/'+result['id']).json()
    session=client.get('/scholar-v5/sessions/'+sid).json()
    messages=[m for m in session['messages'] if m.get('run_id')==result['id'] and m['role']=='assistant']
    # This is the list rendered by the frontend, not just an unshown run field.
    cases=json.loads(UI_CASES.read_text(encoding='utf8'))
    cases[name]={'message':messages[-1] if messages else None,'expected':expected,'run_answer':run['answer']}
    UI_CASES.write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf8')
    assert messages, 'Stop answer exists only on the run, not in the chat message list'
    assert messages[-1]['content']==run['answer']==result['answer']
    for fragment in expected:assert fragment.casefold() in messages[-1]['content'].casefold()
    return run,messages[-1]


@pytest.mark.parametrize('axis,outcome,fragment',[
    ('tokens','tokens_exhausted','plafond global de tokens'),
    ('usd','budget_insufficient','plafond financier'),
    ('seconds','time_exhausted','délai global'),
    ('tools','tools_exhausted','plafond global des outils'),
    ('web','web_exhausted','plafond global du Web')])
def test_s2_budget_causes_reach_chat_without_http(app_env,monkeypatch,axis,outcome,fragment):
    make,_,_=app_env;svc,sid,tr=make([])
    def refuse(meter,*a,**kw):
        raise BudgetRefused({'refused_budget':'v5_mission','axis':axis,'classification':'job_budget',
            'used':meter.limits[axis],'requested':1,'limit':meter.limits[axis]})
    monkeypatch.setattr(RunMeter,'admit',refuse)
    result=run_sync(svc,sid,'Analyse les publications utiles.')
    visible_api(svc,sid,result,monkeypatch,axis,[fragment,'conservés'])
    assert result['outcome']==outcome and result['partial'] and not tr.requests


@pytest.mark.parametrize('classification',['admission_refused','synthesis_reserve','section_save_reserve'])
def test_s2_refused_reservation_is_not_spent_budget(app_env,monkeypatch,classification):
    make,_,_=app_env;svc,sid,tr=make([])
    def refuse(meter,*a,**kw):
        raise BudgetRefused({'refused_budget':'v5_synthesis','axis':'tokens','classification':classification,
            'used':100,'requested':meter.limits['tokens'],'limit':meter.limits['tokens']})
    monkeypatch.setattr(RunMeter,'admit',refuse)
    result=run_sync(svc,sid,'Analyse les résultats scientifiques.')
    visible_api(svc,sid,result,monkeypatch,classification,['réservation','conservés'])
    assert result['outcome']=='reservation_insufficient'
    assert 'épuisé' not in result['answer'] and 'atteint' not in result['answer']
    assert result['usage']['input_tokens']==result['usage']['estimated_cost_usd']==0 and not tr.requests


@pytest.mark.parametrize('reason,fragment',[
    ('stagnation','progrès'),('cancelled','demandée'),
    ('uncertain_model_call','rapprochement'),('uncertain_tool_call','vérifier'),
    ('finalization_failed','validation de la réponse'),('provider_protocol_arguments','format'),
    ('consultant_review','consultant')])
def test_s2_other_graph_stops_have_visible_reason_and_next_action(app_env,monkeypatch,reason,fragment):
    make,_,_=app_env
    svc,sid,tr=make([call('pause',{'reason':reason})]*(2 if reason=='stagnation' else 1))
    result=run_sync(svc,sid,'Examine les publications utiles.')
    visible_api(svc,sid,result,monkeypatch,reason,[fragment,'conservés'])
    assert result['partial'] and len(tr.requests)==(2 if reason=='stagnation' else 1)


@pytest.mark.parametrize('kind,fragment', [('401','HTTP 401'),('timeout','délai de réponse'),('500','HTTP 500')])
def test_s2_provider_error_reaches_chat_without_historical_science(app_env,monkeypatch,kind,fragment):
    make,factory,_=app_env;state=lithium()
    choice=(httpx.ReadTimeout('private-test-value',request=httpx.Request('POST','https://api.openai.com/v1/responses'))
        if kind=='timeout' else httpx.Response(int(kind),json={'error':{'message':'private-test-value'}}))
    svc,sid,tr=make([choice],state)
    result=run_sync(svc,sid,'Analyse un nouvel article scientifique.')
    visible_api(svc,sid,result,monkeypatch,kind,[fragment,'conservés'])
    assert 'private-test-value' not in json.dumps(result)
    assert 'Notices réunies' not in result['answer'] and 'Lithium' not in result['answer']
    after=scientific_state(factory,sid)
    assert after['outline']==state['outline'] and after['articles']==state['articles'] and len(tr.requests)==1


def test_s2_human_validation_and_success_are_already_visible(app_env,monkeypatch):
    make,factory,_=app_env;state=lithium(False)
    section=state['outline']['sections'][2]
    svc,sid,tr=make([call('write_section',{'section_id':section['id'],'action':'save','text':'Proposition non enregistrée.',
        'instructions':'Révision','article_ids':[],'evidence_ids':[]})],state)
    result=run_sync(svc,sid,'Réécris la section 3.')
    visible_api(svc,sid,result,monkeypatch,'human',['validation du plan attendue','conservées'])
    assert result['outcome']=='human_validation_required' and result['partial']
    assert scientific_state(factory,sid)['outline']==state['outline']
    tr.choices[:]=[call('manage_outline',outline('rename',section['id'],'Limites scientifiques'))]
    done=run_sync(svc,sid,'Renomme la section 3 en Limites scientifiques.')
    visible_api(svc,sid,done,monkeypatch,'success',['Titre de la section 3 enregistré'])
    assert done['status']=='completed' and not done['partial']


@pytest.mark.parametrize('axis,fragment',[('tokens','plafond global de tokens'),('seconds','délai global'),
    ('usd','plafond financier'),('tools','plafond global des outils'),('web','plafond global du Web')])
def test_s2_writing_results_do_not_mask_stop_cause(app_env,monkeypatch,axis,fragment):
    make,factory,_=app_env;state=lithium()
    svc,sid,tr=make([],state)
    def refuse(meter,*a,**kw):
        raise BudgetRefused({'refused_budget':'v5_global','axis':axis,'classification':'job_budget',
            'used':meter.limits[axis],'requested':1,'limit':meter.limits[axis]})
    monkeypatch.setattr(RunMeter,'admit',refuse)
    result=run_sync(svc,sid,'Rédige la section 3.')
    visible_api(svc,sid,result,monkeypatch,'writing_'+axis,[fragment,'conservés'])
    assert scientific_state(factory,sid)['outline']==state['outline']
    assert 'job_budget' not in result['answer'] and not tr.requests


@pytest.mark.parametrize('classification',['admission_refused','synthesis_reserve','section_save_reserve'])
def test_s2_writing_reservation_does_not_claim_a_save(app_env,monkeypatch,classification):
    make,factory,_=app_env;state=lithium();svc,sid,tr=make([],state)
    def refuse(meter,*a,**kw):
        raise BudgetRefused({'refused_budget':'v5_synthesis','axis':'tokens','classification':classification,
            'used':100,'requested':meter.limits['tokens'],'limit':meter.limits['tokens']})
    monkeypatch.setattr(RunMeter,'admit',refuse)
    result=run_sync(svc,sid,'Rédige la section 3.')
    visible_api(svc,sid,result,monkeypatch,'writing_'+classification,['réservation','conservés'])
    assert 'a servi à enregistrer' not in result['answer']
    assert scientific_state(factory,sid)['outline']==state['outline'] and not tr.requests


def test_s2_saved_section_survives_and_is_visible_next_to_provider_error(app_env,monkeypatch):
    from .test_ready_section_drafting import real
    from .test_section_save_reserve import corrected_write,SECTION
    make,factory,_=app_env;state=real()['start']
    svc,sid,tr=make([call('write_section',corrected_write(),'save'),httpx.Response(401,json={'error':{'message':'bad key'}})],state)
    result=run_sync(svc,sid,'Continue la rédaction de l’état de l’art en suivant le plan validé.')
    data=scientific_state(factory,sid)
    section=next(s for s in data['outline']['sections'] if s['id']==SECTION)
    assert len(section['drafts'])==1, result
    visible_api(svc,sid,result,monkeypatch,'saved_then_error',['HTTP 401','Sections enregistrées',section['title']])
    assert result['outcome']=='provider_authentication_error' and len(tr.requests)==2


def test_s2_worker_interruption_is_visible_and_idempotent(app_env,monkeypatch):
    make,factory,_=app_env;svc,sid,tr=make([])
    rid=svc.open_run(1,sid,'Analyse les documents conservés.')
    RunMeter(svc,Scope(1,sid),rid,svc.config)
    with factory() as db:
        db.get(ScholarV4Session,sid).lease_until=datetime.utcnow()-timedelta(seconds=1)
        db.get(ScholarV4Run,rid).status='running';db.commit()
    result=svc.run(1,rid)
    visible_api(svc,sid,result,monkeypatch,'worker',['interrompue','même budget'])
    svc.run(1,rid);svc.read(1,sid)
    messages=[m for m in scientific_state(factory,sid)['messages'] if m.get('run_id')==rid and m['role']=='assistant']
    assert len(messages)==1 and not tr.requests


@pytest.mark.parametrize('axis,fragment',[('tokens','plafond global de tokens'),('usd','plafond financier'),
    ('tools','plafond global des outils'),('seconds','délai global')])
def test_s2_real_meter_depletion_reaches_chat(app_env,monkeypatch,axis,fragment):
    """Synthetic completed receipts consume the existing ceilings; admission and
    stop classification are real, with no injected BudgetRefused exception."""
    make,_,_=app_env;svc,sid,tr=make([])
    original=RunMeter.__init__
    def consumed(meter,*a,**kw):
        original(meter,*a,**kw)
        if axis=='tools':
            meter.record_tools(meter.limits['tools'],operation_id='fixture-completed-tools')
        elif axis=='seconds':
            meter.base_seconds=meter.limits['seconds'];meter.flush_time()
        else:
            ticket=meter.admit('fixture','completed-call','','gpt-6.1-sol',.001,
                {'estimated_input_tokens':10,'max_output_tokens':5})
            meter.mark_sent('completed-call')
            meter.settle(ticket,meter.limits['usd'] if axis=='usd' else .8,
                usage={'input_tokens':meter.limits['tokens'] if axis=='tokens' else 100,'output_tokens':0})
    monkeypatch.setattr(RunMeter,'__init__',consumed)
    result=run_sync(svc,sid,'Examine les articles scientifiques.')
    visible_api(svc,sid,result,monkeypatch,'meter_'+axis,[fragment,'conservés'])
    assert result['partial'] and not tr.requests


def test_s2_web_depletion_only_disables_web_not_local_science(app_env,monkeypatch):
    from agents.EnnoScholarV4.config import ScholarV4Config
    make,_,_=app_env
    svc,sid,tr=make([call('pause',{'reason':'consultant_review'})],
        config=ScholarV4Config(api_key='offline',web_search_enabled=True))
    original=RunMeter.__init__
    def consumed(meter,*a,**kw):
        original(meter,*a,**kw)
        meter.record_tools(0,web=meter.limits['web'],operation_id='fixture-completed-web')
    monkeypatch.setattr(RunMeter,'__init__',consumed)
    result=run_sync(svc,sid,'Examine les données déjà conservées.')
    visible_api(svc,sid,result,monkeypatch,'web_local',['consultant','conservés'])
    assert len(tr.requests)==1 and not any(t.get('type')=='web_search' for t in tr.requests[0]['tools'])
    assert result['reason']=='consultant_review'  # Web cap did not stop useful local work.


def test_s2_local_interruption_never_republishes_old_science(app_env,monkeypatch):
    from experiments.ennoscholar_v5.graph import Runner
    make,factory,_=app_env;state=lithium();svc,sid,tr=make([],state)
    def crash(*a,**kw):raise RuntimeError('private-interruption-value')
    monkeypatch.setattr(Runner,'start',crash)
    result=run_sync(svc,sid,'Examine un nouveau sujet scientifique.')
    visible_api(svc,sid,result,monkeypatch,'local_crash',['interrompue','même budget'])
    assert 'private-interruption-value' not in json.dumps(result) and 'Lithium' not in result['answer']
    assert scientific_state(factory,sid)['outline']==state['outline'] and not tr.requests


def test_s2_error_before_any_new_section_does_not_republish_old_manuscript(app_env,monkeypatch):
    make,factory,_=app_env;state=lithium()
    svc,sid,tr=make([httpx.Response(401,json={'error':{'message':'bad key'}})],state)
    result=run_sync(svc,sid,'Rédige la section 3.')
    visible_api(svc,sid,result,monkeypatch,'writing_error_no_save',['HTTP 401','conservés'])
    for s in state['outline']['sections']:
        for draft in s.get('drafts',[]):
            assert not draft.get('raw_text') or draft['raw_text'] not in result['answer']
    assert scientific_state(factory,sid)['outline']==state['outline'] and len(tr.requests)==1


def test_s2_card_actually_saved_before_provider_error_is_acknowledged(app_env,monkeypatch):
    from .test_graph import case,read,payload,LI
    make,factory,_=app_env;state=case('chemistry')['state']
    def save(sent):
        output=next(json.loads(i['output']) for i in reversed(sent['input']) if i.get('type')=='function_call_output')
        eid=output['reading'][0]['passages'][0]['evidence_id']
        args=read(LI).arguments;args.update(action='save',analysis=payload(eid))
        return call('analyze_article',args,'save-card')
    svc,sid,tr=make([call('analyze_article',read(LI).arguments,'read-card'),save,
        httpx.Response(401,json={'error':{'message':'bad key'}})],state)
    result=run_sync(svc,sid,'Analyse la publication '+LI+' à partir du document conservé.')
    visible_api(svc,sid,result,monkeypatch,'saved_card_then_error',['HTTP 401','1 opération(s) de sauvegarde','fiches restent consultables'])
    assert scientific_state(factory,sid)['articles'][LI]['analysis']!=state['articles'][LI]['analysis']
    assert len(tr.requests)==3
