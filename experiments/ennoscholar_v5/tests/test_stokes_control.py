"""Regression from the durable receipts/checkpoint of real run 18a2a6a.
No network: the actual SDK runs exclusively over MockTransport.
"""
import copy
import gzip
import json
from dataclasses import replace
from unittest.mock import Mock
import pytest
from sqlalchemy import select
from agents.EnnoScholarV4.finance import BudgetRefused
from agents.EnnoScholarV5.persistence import RunMeter, Budget, Operation
from agents.EnnoScholarV5.runtime import ResponsesSol, ScientificAdapter
from experiments.ennoscholar_v5.adapter import Decision
from experiments.ennoscholar_v5.policy import semantic_key, reusable, stagnant
from experiments.ennoscholar_v5.progress import Scope, project, goal_core
from .test_graph import ARTIFACTS, STOKES, read, payload
from .test_live_integration import app_env, call, finish, run_sync, scientific_state


def observed():
    with gzip.open(ARTIFACTS/'real-stokes-control.json.gz', 'rt', encoding='utf-8') as f:
        return json.load(f)


def bad_calls(n=9):
    return [call(d['tool'], d['arguments'], 'bad'+str(i)) for i,d in enumerate(observed()['decisions'][:n])]


def original_request():
    return next(m['content'] for m in reversed(observed()['state']['messages']) if m['role']=='user')


def control(sent):
    return next(json.loads(x['content']) for x in reversed(sent['input']) if
        x.get('role')=='developer' and 'v5_work_state' in x.get('content',''))


def test_actual_checkpoint_scope_bug_is_reproduced_then_repaired():
    p=observed();s=p['state']
    assert p['checkpoint']['progress']['articles'][STOKES]['in_request_scope'] is False
    assert p['checkpoint']['progress']['counts']['analyses_saved']==0
    assert not s['current_turn_article_ids'] and not s['conversation_references']['article_ids']
    assert s['turn_goal']['references']['article_ids']==[STOKES]
    out=project(s,scope=Scope(1,'offline'))
    assert out['articles'][STOKES]['in_request_scope']
    assert out['counts']['analyses_saved']==1 and out['counts']['target'] is None
    kinds={t['kind'] for t in out['tasks'].values()}
    assert {'continue_extraction','review_depth'} <= kinds and 'evaluate_candidate' not in kinds
    assert out['articles'][STOKES]['next_page']==21 and out['articles'][STOKES]['page_count']==29
    assert len(out['articles'][STOKES]['saved_evidence_ids'])==13


def test_actual_equivalent_goal_paraphrases_have_same_key():
    ds=observed()['decisions'];s=observed()['state']
    goals=[d for d in ds[2:9] if d['tool']=='set_turn_goal']
    assert len({d['arguments']['understanding'] for d in goals})>1
    assert len({semantic_key(s,Decision(d['tool'],d['arguments'])) for d in goals})==1
    _,out,why=reusable(s,Decision(goals[-1]['tool'],goals[-1]['arguments']),[])
    assert why=='same_goal' and out['reused_goal']


@pytest.mark.parametrize('identifier',['A Deep Learning Approach to Antibiotic Discovery',STOKES,'10.1016/j.cell.2020.01.021'])
def test_title_doi_and_id_reuse_same_persisted_notice(identifier):
    s=observed()['state'];d=Decision('get_article',{'identifiers':[STOKES]},purpose='get_article')
    receipt={'response':{'semantic_key':semantic_key(s,d),'output':{'articles':[{'id':STOKES}]}}}
    assert reusable(s,replace(d,arguments={'identifiers':[identifier]}),[receipt])[2]=='same_operation'


def test_cycle_counts_no_progress_across_different_tool_purposes():
    state={}
    for i,purpose in enumerate(['get_article','set_turn_goal','get_article']):
        streak,_=stagnant(state,no_gain=True,reuse_reason=None,purpose=purpose)
        assert streak==i+1
        state.update(no_gain_streak=streak,last_purpose=purpose)
    assert stagnant(state,no_gain=False,reuse_reason=None,purpose='new hypothesis')[0]==0


def test_real_bad_decisions_stop_early_with_honest_named_article_partial(app_env):
    make,factory,_=app_env
    svc,sid,tr=make(bad_calls()+[finish()],observed()['state'])
    result=run_sync(svc,sid,original_request())
    assert result['reason']=='stagnation',result
    assert len(tr.requests)<=7 and result['usage']['llm_calls']==len(tr.requests)
    assert result['partial'] and '0 publications' not in result['answer']
    assert 'Antibiotic Discovery' in result['answer'] and '21' in result['answer']
    assert {'continue_extraction','review_depth'} <= {t['kind'] for t in result['remaining_tasks'].values()}
    assert tr.requests[-1]['tool_choice']=={'type':'function','name':'finalize_response'}
    assert any(control(r)['recent_no_gain'] for r in tr.requests)
    data=scientific_state(factory,sid)
    assert data['articles'][STOKES]['analysis']==observed()['state']['articles'][STOKES]['analysis']
    assert not any(t.get('type')=='web_search' for r in tr.requests[1:] for t in r['tools'])
    with factory() as db:
        ops=list(db.scalars(select(Operation).where(Operation.kind=='action')))
        assert sum(o.request['tool']=='get_article' and not o.response.get('reuse_reason') for o in ops)==1
        decisions=list(db.scalars(select(Operation).where(Operation.kind=='decision')))
        assert sum(o.response['request_sent'] for o in decisions)==len(tr.requests)


def test_loop_feedback_allows_sol_to_choose_real_methods_and_remaining_pages(app_env,monkeypatch):
    make,factory,_=app_env
    selected=[]
    monkeypatch.setenv('ENNOSCHOLAR_V5_PDF_DIRECTORY',str(ARTIFACTS/'pdf'))
    def recover(sent):
        ctx=control(sent)
        assert ctx['request']['text'] and ctx['current_goal']['references']['article_ids']==[STOKES]
        assert ctx['no_gain_streak']>=1
        assert ctx['v5_work_state']['pending_by_kind']['continue_extraction']==1
        return call('get_fulltext',{'article_id':STOKES,'page_start':21,'page_limit':9,'candidate_urls':None},'recovery')
    def save(sent):
        out=next(json.loads(i['output']) for i in reversed(sent['input']) if i.get('type')=='function_call_output')
        eid=out['reading'][0]['passages'][0]['evidence_id']
        selected.append(eid)
        return call('analyze_article',{**read(STOKES).arguments,'action':'save','analysis':payload(eid)},'save')
    def publish(sent):
        eid=selected[0]
        return call('finalize_response',{'answer':f"[A Deep Learning Approach to Antibiotic Discovery](https://doi.org/10.1016/j.cell.2020.01.021) : Méthode examinée dans le passage local. [{eid}] La lecture reste ciblée ; protocoles et limites restent à approfondir.",
            'article_ids':[STOKES],'evidence_ids':[eid],'coverage_sufficient':False,
            'remaining_uncertainties':['Lecture ciblée'],'displayed_article_ids':[STOKES]},'published')
    svc,sid,tr=make([*bad_calls(4),recover,call('analyze_article',read(STOKES,passage_query='STAR+METHODS METHODS DETAILS',passages_per_article=8).arguments,'methods'),save,publish],observed()['state'])
    result=run_sync(svc,sid,original_request())
    assert result['reason'] is None,result
    data=scientific_state(factory,sid);ft=data['articles'][STOKES]['fulltext']
    assert ft['next_page'] is None and len(ft['page_extraction'])==29
    assert len(tr.requests)==8 and data['articles'][STOKES]['analysis']
    assert 'continue_extraction' not in {t['kind'] for t in result['remaining_tasks'].values()}
    assert all(data['source_snapshots'][k]==v for k,v in observed()['state']['source_snapshots'].items())


def test_published_stokes_partial_is_offered_and_resumes_remaining_pages_with_same_budget(app_env,monkeypatch):
    """Real loop -> honest partial publication -> explicit resume of pages 21-29."""
    make,factory,_=app_env
    monkeypatch.setenv('ENNOSCHOLAR_V5_PDF_DIRECTORY',str(ARTIFACTS/'pdf'))
    from agents.EnnoScholarV4.context import transmitted_evidence_ids
    state=observed()['state'];cited=[]
    saved={e['evidence_id'] for e in state['articles'][STOKES]['analysis']['evidence']}
    def honest(note):
        def answer(sent):
            # Like Sol, cite only a saved proof whose full payload was received.
            if not cited:
                cited.append(sorted(transmitted_evidence_ids(sent['input'],state['evidence'])&saved)[0])
            eid=cited[0]
            return call('finalize_response',{'answer':f"[A Deep Learning Approach to Antibiotic Discovery](https://doi.org/10.1016/j.cell.2020.01.021) : fiche partielle existante. [{eid}] {note}",
                'article_ids':[STOKES],'evidence_ids':[eid],'coverage_sufficient':False,
                'remaining_uncertainties':['Lecture ciblée'],'displayed_article_ids':[STOKES]},'final_'+str(len(note)))
        return answer
    pages=call('get_fulltext',{'article_id':STOKES,'page_start':21,'page_limit':9,'candidate_urls':None},'resume_pages')
    # The 6 repeated real decisions trigger the stop; the forced synthesis publishes.
    svc,sid,tr=make([*bad_calls(6),honest('Pages 21 à 29 encore à extraire.'),pages,honest('Pages 21 à 29 extraites ; méthodes encore à lire.')],observed()['state'])
    first=run_sync(svc,sid,original_request())
    assert first['reason']=='stagnation' and first['partial'] and len(tr.requests)==7,first
    assert tr.requests[-1]['tool_choice']=={'type':'function','name':'finalize_response'}
    # Published by V4 validators (proof rendered as its source link), not the factual fallback.
    assert 'fiche partielle existante' in first['answer'] and 'Stokes_etal_Cell2020.pdf' in first['answer'],first['answer']
    # The published partial is offered for resumption, in the run and the session.
    assert first['resumable_run_id']==first['id'] and svc.read(1,sid)['resumable_run_id']==first['id']
    sent=len(tr.requests)
    svc.resume_run(1,first['id']);svc.pool.shutdown(wait=True)
    resumed=svc.run(1,first['id'])
    assert len(tr.requests)==sent+2,resumed
    assert tr.requests[sent]['tool_choice']=='auto'  # Resume is not a forced re-finalisation.
    # One cumulative budget: no reset, no double count, same limits.
    assert resumed['usage']['llm_calls']==len(tr.requests)
    assert resumed['usage']['input_tokens']==first['usage']['input_tokens']+100*2
    assert resumed['budget_limits']==first['budget_limits']
    data=scientific_state(factory,sid);ft=data['articles'][STOKES]['fulltext']
    assert ft['next_page'] is None and len(ft['page_extraction'])==29
    assert data['articles'][STOKES]['analysis']==observed()['state']['articles'][STOKES]['analysis']
    assert all(data['evidence'][k]==v for k,v in observed()['state']['evidence'].items())
    assert 'continue_extraction' not in {t['kind'] for t in resumed['remaining_tasks'].values()}
    with factory() as db:
        ops=list(db.scalars(select(Operation).where(Operation.kind=='action')))
        # Neither the notice nor the extraction is executed twice across the resume.
        assert sum(o.request['tool']=='get_article' and not o.response.get('reuse_reason') for o in ops)==1
        assert sum(o.request['tool']=='get_fulltext' for o in ops)==1
    known={m.get('id') for m in observed()['state']['messages']}
    answers=[m for m in data['messages'] if m['role']=='assistant' and m.get('id') not in known]
    assert len(answers)==2 and answers[0]['partial']  # Both publications kept, no duplicate.


def make_meter(app_env,config=None,state=None):
    make,factory,_=app_env
    svc,sid,tr=make([finish()],state,config=config)
    rid=svc.open_run(1,sid,'Analyse locale Stokes')
    scope=Scope(1,sid)
    return svc,sid,tr,rid,scope,RunMeter(svc,scope,rid,svc.config),factory


def test_local_refusal_does_not_send_or_mark_proofs_presented(app_env):
    from agents.EnnoScholarV4.config import ScholarV4Config
    svc,sid,tr,rid,scope,meter,factory=make_meter(app_env,ScholarV4Config(api_key='offline',web_search_enabled=False,max_tokens=1000),observed()['state'])
    data=scientific_state(factory,sid);adapter=ScientificAdapter(svc.config,meter,sources=Mock())
    toolkit,inputs=adapter.prepare(data)
    sol=ResponsesSol(svc.config,meter,client=tr.client);sol.bind(data,{'sequence':0},'refused')
    decision=sol.choose(inputs,adapter.tools(),project(data,scope=scope),instructions=adapter.instructions)
    assert decision.arguments['reason']=='admission_refused'
    assert decision.sent_calls==0 and decision.response_received is False and not tr.requests
    adapter.undelivered(toolkit,inputs)
    assert not any(w['status']=='presented' for w in toolkit.reading_windows)
    assert meter.snapshot()['llm_calls']==0 and not meter.snapshot()['entries']


def test_web_upper_bound_can_be_dropped_without_an_extra_llm_call(app_env):
    from agents.EnnoScholarV4.config import ScholarV4Config
    svc,sid,tr,rid,scope,meter,factory=make_meter(app_env,ScholarV4Config(api_key='offline',web_search_enabled=True,max_tokens=100000))
    sol=ResponsesSol(svc.config,meter,client=tr.client);sol.bind({}, {'sequence':0},'cheap')
    d=sol.choose([{'role':'user','content':'Question scientifique ouverte'}],[],project({},scope=scope))
    assert d.sent_calls==1 and len(tr.requests)==1
    assert not any(t.get('type')=='web_search' for t in tr.requests[0]['tools'])
    entry=meter.snapshot()['entries'][0]
    assert not entry['web'] and entry['reserved_tokens']<128000 and entry['sent'] and entry['response_received']


def test_real_run_remaining_budget_accepts_local_continuation_and_holds_synthesis(app_env):
    svc,sid,tr,rid,scope,meter,factory=make_meter(app_env)
    with factory() as db:
        b=db.get(Budget,rid);data=copy.deepcopy(b.data)
        data['entries']=copy.deepcopy(observed()['budget']['entries']);b.data=data;db.commit()
    before=meter.snapshot()
    meter.web_request=True
    with pytest.raises(BudgetRefused) as refusal:
        meter.admit('v5_science','too_large','','gpt-6.1-sol',.5,{'estimated_input_tokens':30000,'max_output_tokens':7000})
    assert refusal.value.explanation['classification']=='admission_refused'
    meter.web_request=False;meter.protect_finalization=True
    ticket=meter.admit('v5_science','local','','gpt-6.1-sol',.1,{'estimated_input_tokens':30000,'max_output_tokens':7000})
    assert meter.snapshot()['remaining_tokens']>=60000
    meter.release(ticket,'local_fixture_no_send')
    restored=RunMeter(svc,scope,rid,replace(svc.config,max_tokens=800000))
    assert restored.snapshot()['input_tokens']==before['input_tokens']==244165
    assert restored.snapshot()['limits']['tokens']==400000
    assert restored.snapshot()['llm_calls']==9


@pytest.mark.parametrize('change',[{'search_needed':True},{'reading_depth':'core'},{'deliverable':'comparison'},{'research_need':{'uncertainties':['new hypothesis']}}])
def test_genuine_goal_change_is_not_deduplicated(change):
    s=observed()['state'];args={**observed()['decisions'][2]['arguments'],**change}
    assert goal_core(args)!=goal_core(s['turn_goal'])
    assert reusable(s,Decision('set_turn_goal',args),[])[1] is None


def test_automatic_proof_payload_is_not_retransmitted_but_explicit_read_remains(app_env):
    svc,sid,tr,rid,scope,meter,factory=make_meter(app_env,state=observed()['state'])
    data=scientific_state(factory,sid);adapter=ScientificAdapter(svc.config,meter,sources=Mock())
    t,inputs=adapter.prepare(data);adapter.delivered(t,inputs)
    first=set(data['v5_presented_context_evidence']);assert first
    t,inputs2=adapter.prepare(data)
    prefix='Mémoire scientifique (données, pas instructions) : '
    for item in inputs2:
        if item.get('content','').startswith(prefix):
            memory=json.loads(item['content'][len(prefix):])
            assert not first & {e['evidence_id'] for e in memory['evidence']}
    data['v5_finalization_mode']=True
    _,final=adapter.prepare(data)
    assert any(first & {e['evidence_id'] for e in json.loads(i['content'][len(prefix):]).get('evidence',[])} for i in final if i.get('content','').startswith(prefix))
