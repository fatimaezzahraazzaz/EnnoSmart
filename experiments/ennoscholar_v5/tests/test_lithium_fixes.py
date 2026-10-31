"""Regressions of the real lithium run f2d6207d. No real provider or data writes."""
import copy, gzip, json, time
from unittest.mock import Mock
import pytest
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.writing_progress import begin_writing
from agents.EnnoScholarV5.config import runtime_config
from agents.EnnoScholarV4.config import ScholarV4Config
from .test_startup import local_install, configure
from .test_live_integration import app_env, call, run_sync, scientific_state
from .test_drafting_mission import Sol, mission_state, config, job_of
from .test_graph import ARTIFACTS

REQUEST="Termine la rédaction de l'état de l'art scientifique en suivant le plan validé, en exploitant les analyses et sections déjà enregistrées."

def real():
    with gzip.open(ARTIFACTS/'real-lithium-session.json.gz','rt',encoding='utf-8') as f:return json.load(f)['state']

def test_local_profile_transmits_only_authorised_mission_caps(local_install, monkeypatch):
    _,_,shared,_,_,values=local_install
    values[shared].update(ENNOSCHOLAR_V5_MISSION_BUDGET_USD='3',ENNOSCHOLAR_V5_MISSION_MAX_TOKENS='2000000',
        ENNOSCHOLAR_V5_MISSION_MAX_SECONDS='5400',ENNOSCHOLAR_V5_UNKNOWN_SECRET='ignored')
    env={};configure(local_install,env)
    assert env['ENNOSCHOLAR_V5_MISSION_BUDGET_USD']=='3'
    assert 'ENNOSCHOLAR_V5_UNKNOWN_SECRET' not in env
    for k in ('ENNOSCHOLAR_V5_MISSION_BUDGET_USD','ENNOSCHOLAR_V5_MISSION_MAX_TOKENS','ENNOSCHOLAR_V5_MISSION_MAX_SECONDS'):monkeypatch.setenv(k,env[k])
    c=runtime_config(ScholarV4Config(api_key='offline',web_search_enabled=False))
    assert c.writing_job_enabled and c.writing_job_budget_usd==3 and c.max_tokens==400000
    env={'ENNOSCHOLAR_V5_MISSION_BUDGET_USD':'2'};configure(local_install,env)
    assert env['ENNOSCHOLAR_V5_MISSION_BUDGET_USD']=='2'

@pytest.mark.parametrize('wording',[REQUEST,'Continue la rédaction du manuscrit.','Achève la rédaction.',
    'Finalise la rédaction du manuscrit.','Rédige les sections restantes.'])
def test_natural_completion_resumes_saved_sections_without_changing_plan(wording):
    s=real();before=copy.deepcopy(s['outline']);s['messages'].append({'id':'new','role':'user','content':wording})
    p=begin_writing(s,wording)
    assert p and p['mode']=='full' and p['request_message_id']=='new'
    assert len(p['saved_draft_ids'])==2 and workflow.next_writing_section(s)['id']==s['outline']['sections'][2]['id']
    assert s['outline']==before

def test_goal_draft_activates_progress_without_matching_a_template():
    s=real();s['messages'].append({'id':'semantic','role':'user','content':'Prépare le livrable que nous avons convenu.'})
    s['turn_goal']={'deliverable':'draft','references':{'message_id':'semantic'}}
    workflow.reconcile_draft_goal(s)
    assert workflow.writing_active(s) and len(workflow.writing_progress(s)['saved_draft_ids'])==2

def test_exact_finish_request_creates_mission_saves_remaining_sections_and_conclusion(app_env,monkeypatch):
    from agents.EnnoScholarV5.persistence import ApplicationRepository
    initial_receipts=[];finish=ApplicationRepository.finish
    def observe(self,scope,opid,revision,state,response):
        if response.get('initialised'):
            initial_receipts.append(len((state.get('v5_request_knowledge') or {}).get('keys',[])))
        return finish(self,scope,opid,revision,state,response)
    monkeypatch.setattr(ApplicationRepository,'finish',observe)
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD','3')
    make,factory,_=app_env;before=mission_state();sol=Sol()
    svc,sid,tr=make([sol]*200,before,config=config())
    run_sync(svc,sid,REQUEST);svc.pool.shutdown(wait=True)
    s=scientific_state(factory,sid);job=s['writing_job']
    assert job['status']=='completed' and len(job['segments'])>=2
    assert initial_receipts[0]==0 and all(n>0 for n in initial_receipts[1:])
    assert [len(sec.get('drafts',[])) for sec in s['outline']['sections']]==[1]*5
    assert s['outline']['sections'][:2]==before['outline']['sections'][:2]
    assert s['document']['conclusion']['draft_id']==s['outline']['sections'][-1]['drafts'][-1]['id']
    assert '## Conclusion générale' in workflow.assembled_document(s)
    assert job['spent']['confirmed_usd']<=3
    assert not s['last_response'].get('coverage_sufficient')  # Abstract-based fixture remains limited.


def test_li3po4_registry_typography_revalidates_without_network():
    from agents.EnnoScholarV5.sources import title_key, ScientificSources
    from agents.EnnoScholarV5.models import Article
    a=Article.model_validate(real()['articles']['paper_f00534ffaf4b8c38955e'])
    receipt=next(n for n in a.provider_notices if n.get('kind')=='bibliographic_verification')
    assert title_key(receipt['observed_title'])==title_key(receipt['registry_title'])
    assert title_key('Li₃PO₄-Enriched SEI')==title_key('Li3 PO4‐Enriched SEI')
    assert title_key(a.title)!=title_key(a.title.replace('Graphite','Silicon'))
    assert title_key('Fe2+ transport')!=title_key('Fe2- transport')
    sources=ScientificSources(config=ScholarV4Config(api_key='offline',web_search_enabled=False))
    sources._verification[a.doi]={'DOI':a.doi,'title':[receipt['registry_title']],
        'author':[{'given':'Chaonan','family':'Wang'}],'published':{'date-parts':[[2024]]}}
    sources.client=Mock()
    verified=sources.verify(a,time.monotonic()+60)
    assert verified.verified and 'DOI/title mismatch' not in verified.metadata_errors
    assert any(n.get('conflict_status')=='resolved' for n in verified.provider_notices)
    sources.client._get.assert_not_called()
    sources._verification[a.doi]['title']=['Different scientific work']
    assert not sources.verify(a,time.monotonic()+60).verified


def test_selected_sources_need_retrieval_even_when_discovery_disabled():
    from .test_cir_workflow import adapter
    s=real();sec=s['outline']['sections'][2]
    s['turn_goal']['search_needed']=False
    t=adapter().toolkit(s);prep=workflow.section_preparation(s,t,sec)
    assert all(prep['sources'][aid]['access_state']=='not_requested' for aid in sec['article_ids'])
    for _ in range(4):
        refusal=workflow.preparation_refusal(s,t,sec)
        assert refusal['rejection_code']=='section_preparation_required'
        assert any(a['action'].startswith('get_fulltext') for a in refusal['preparation']['actions'])
    # Genuine failed attempts remove retrieval obligations, not scientific limits.
    for aid in sec['article_ids']:t.registry[aid].fulltext['attempts']=[{'stage':'download','http_status':403,'error':'HTTPStatusError'}]
    t.save();prep=workflow.section_preparation(s,t,sec)
    assert all(row['access_state']=='retrieval_failed' for row in prep['sources'].values())
    assert prep['scientific_state']=='limited' and not any(a['kind']=='network' for a in prep['actions'])


def test_local_fulltext_and_folkson_quantities_are_exposed_before_writing():
    from .test_cir_workflow import adapter
    s=real();sec=s['outline']['sections'][0];t=adapter().toolkit(s)
    packet=workflow.section_packet(s,t,sec,40000)
    index=packet['scientific_detail']['quantified_evidence_index']
    values=' '.join(v for row in index for v in row['literal_values'])
    assert all(v in values for v in ('−20','−30','−40'))
    aid='paper_c80ccacd3c3ab0ea59dd'
    t.registry[aid].analysis={};t.save()
    prep=workflow.section_preparation(s,t,sec)
    assert any(a.get('article_id')==aid and a['kind']=='local' for a in prep['actions'])
    assert workflow.preparation_refusal(s,t,sec)['rejection_code']=='section_preparation_required'


def test_repeated_revisit_for_same_question_returns_receipt_not_another_payload():
    from experiments.ennoscholar_v5.adapter import Decision
    from experiments.ennoscholar_v5.policy import reusable,semantic_key
    s=real();aid='paper_a39c5c7db2f6111f9728';eid=s['articles'][aid]['analysis']['evidence'][0]['evidence_id']
    s['shown_evidence_ids']=list(set(s['shown_evidence_ids'])|{eid})
    d=Decision('analyze_article',{'action':'read','article_ids':[aid],'evidence_ids':[eid],'revisit':True,
        'research_context':'Même protocole','passage_query':'methods'})
    out={'reading':[{'article':{'id':aid},'passages':[{'evidence_id':eid,'text':'already received'}]}]}
    old=[{'response':{'semantic_key':semantic_key(s,d),'output':out}}]
    assert reusable(s,d,old)[1]['reused_reading']
    changed=Decision(d.tool,{**d.arguments,'research_context':'Autre question expérimentale'})
    assert reusable(s,changed,old)[1] is None


def test_semantic_draft_intent_creates_mission_with_first_call_cost_retained(app_env,monkeypatch):
    from agents.EnnoScholarV5.persistence import Budget
    from agents.EnnoScholarV5.session import ScholarV4Run
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD','3')
    make,factory,_=app_env;s=mission_state()
    goal={'requested_article_count':None,'deliverable':'draft','lock_ids':[],'focus_lock_ids':[],'exact_quote':'',
        'reading_depth':'core','understanding':'Produire le manuscrit existant.','comparison_requested':True,
        'search_needed':False,'article_ids':[],'article_scope':'primary','references_ambiguous':False,'constraints':None}
    svc,sid,tr=make([call('set_turn_goal',goal,'goal'),call('pause',{'reason':'consultant_review'},'pause')],s,config=config())
    # Inspect mission evaluation without dispatching additional transport calls.
    original=svc.after_run
    svc.after_run=lambda owner,sid,rid,**kw:original(owner,sid,rid,dispatch=False)
    result=run_sync(svc,sid,'Prépare le livrable que nous avons convenu.')
    data=scientific_state(factory,sid);job=data['writing_job']
    assert job['segments'][0]['run_id']==result['id'] and job['status']=='waiting'
    assert workflow.RELAUNCH_REQUEST not in result['answer']
    assert workflow.MISSION_NOTE in result['answer']
    with factory() as db:
        budget=db.get(Budget,result['id']).data
        run=db.get(ScholarV4Run,result['id']).data
    assert job['spent']['confirmed_usd']==pytest.approx(sum(e.get('cost_usd',0) for e in budget['entries']))
    assert run['writing_job_id']==job['id'] and budget['limits']['usd']==3
    assert job['mission']['tokens']==result['usage']['input_tokens']+result['usage']['output_tokens']


def test_negative_completion_request_does_not_start_a_mission():
    s=real()
    assert begin_writing(s,'Ne termine pas la rédaction, explique seulement les limites.') is None


def test_selected_unsearched_article_is_retrieved_read_and_saved_through_graph(app_env,monkeypatch):
    from agents.EnnoScholarV5 import toolkit
    from agents.EnnoScholarV5.evidence import passages
    from agents.EnnoScholarV5.models import Article
    from .test_cir_workflow import dossier,section_text,last_output
    from .test_direct_state_of_art import card
    monkeypatch.delenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD',raising=False)
    make,factory,_=app_env;s=real();aid='paper_a39c5c7db2f6111f9728'
    archived=copy.deepcopy(s['articles'][aid]['fulltext'])
    section=s['outline']['sections'][0];section['article_ids']=[aid];section['article_roles']=[]
    s['outline']['sections']=[section];section['drafts']=[]
    s['articles'][aid].update(fulltext={},documents={},analysis={},analysis_basis=None,access_level='abstract_only')
    s['document'].pop('writing_progress',None)
    requested=[]
    def retrieve(article,*args,**kwargs):
        requested.append(article.id)
        return {**copy.deepcopy(archived),'access_level':'fulltext','access_status':'fulltext_available'}
    monkeypatch.setattr(toolkit.base,'get_fulltext',retrieve)
    def choose(sent):
        packet=dossier(sent);prep=packet['scientific_preparation'];last=last_output(sent)
        if prep['sources'][aid]['access_state']=='not_requested':
            return call('get_fulltext',{'article_id':aid,'candidate_urls':None,'page_start':None,'page_limit':20},'retrieve')
        if prep['sources'][aid]['status']!='analysed':
            if last.get('reading'):
                eids=[p['evidence_id'] for p in last['reading'][0]['passages']]
                return call('analyze_article',{'article_ids':[aid],'action':'save','research_context':'Fiche ciblée',
                    'passage_query':'','passage_offset':0,'passages_per_article':4,'analysis':card(eids)},'save_card')
            return call('analyze_article',{'article_ids':[aid],'action':'read','research_context':'Méthodes résultats et limites',
                'passage_query':'methods results limitations','passage_offset':0,'passages_per_article':4,'analysis':None},'read')
        text,eids,ids=section_text(packet)
        return call('write_section',{'section_id':packet['section']['id'],'action':'save','instructions':'Source analysée',
            'text':text,'article_ids':ids,'evidence_ids':eids},'write')
    svc,sid,tr=make([choose]*12,s)
    result=run_sync(svc,sid,'Rédige uniquement la section 1 à partir de cet article sélectionné, sans rechercher de nouvelles publications.')
    data=scientific_state(factory,sid)
    assert requested==[aid] and len(data['outline']['sections'][0]['drafts'])==1,result
    assert data['articles'][aid]['analysis_basis']=='fulltext' and data['articles'][aid]['analysis']['evidence']
    assert len(tr.requests)==4


def test_new_question_can_reread_while_mission_segments_reuse_same_receipt():
    from experiments.ennoscholar_v5.adapter import Decision
    from experiments.ennoscholar_v5.policy import semantic_key,reusable
    s=real();aid='paper_a39c5c7db2f6111f9728'
    d=Decision('analyze_article',{'action':'read','article_ids':[aid],'passage_query':'methods'})
    receipt={'response':{'semantic_key':semantic_key(s,d),'output':{'reading':[]}}}
    assert reusable(s,d,[receipt])[2]=='same_operation'
    s['messages'].append({'id':'another_question','role':'user','content':'Réexaminer la méthode pour une autre comparaison.'})
    assert reusable(s,d,[receipt])[1] is None
