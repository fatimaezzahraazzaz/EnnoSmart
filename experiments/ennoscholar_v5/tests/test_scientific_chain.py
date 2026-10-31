"""Scientific progression over the archived CO2 session; no real provider."""
import copy
import gzip
import json
import time
import re
from unittest.mock import Mock
import pytest
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5.runtime import ScientificAdapter
from agents.EnnoScholarV5.science import CoverageReview, passage_states, reading_status, save_review, passage_index
from agents.EnnoScholarV5.models import Article
from experiments.ennoscholar_v5.progress import project, Scope, model_work_view, examined_passages
from .test_graph import ARTIFACTS, case, STOKES, read, payload
from .test_live_integration import app_env, call, finish, run_sync, scientific_state


def archived():
    with gzip.open(ARTIFACTS/'real-cir-depth-run.json.gz','rt',encoding='utf-8') as f:
        return json.load(f)


def adapter():
    return ScientificAdapter(ScholarV4Config(api_key='offline',web_search_enabled=False),Mock(),sources=Mock())


def test_nine_targeted_articles_and_all_sections_reach_work_view():
    p=archived();s=p['state'];projection=project(s,scope=Scope(1,p['session_id']))
    ids={aid for aid,a in projection['articles'].items() if a['in_request_scope']}
    assert len(ids)==9
    view=model_work_view(projection)
    rows={r['article_id']:r for r in view['articles'] if r['in_request_scope']}
    assert set(rows)==ids
    assert all(len(rows[aid]['sections'])==len(projection['articles'][aid]['reading_map']) for aid in ids)
    assert all(rows[aid]['section_count']==len(rows[aid]['sections']) for aid in ids)
    assert len(json.dumps(view,ensure_ascii=False))<=10000


def test_presented_is_not_examined_or_analyzed_including_jats_without_pages():
    s=copy.deepcopy(archived()['state'])
    aid=next(aid for aid,a in s['articles'].items() if a.get('fulltext',{}).get('source_kind')=='jats')
    a=Article.model_validate(s['articles'][aid]);idx=passage_index(s,a,limit=80)
    eid=next(p['evidence_id'] for p in idx['passages'] if p['evidence_id'] not in {e['evidence_id'] for e in a.analysis.get('evidence',[])})
    from agents.EnnoScholarV5.evidence import passages,snapshot
    row=next(r for r in passages(a) if r['evidence_id']==eid)
    s.setdefault('evidence',{})[eid]=row;src=snapshot(a);s.setdefault('source_snapshots',{})[src['fingerprint']]=src
    s['shown_evidence_ids']=list(set(s.get('shown_evidence_ids',[]))|{eid})
    states=passage_states(s,a)
    assert eid in states['presented'] and eid not in states['examined'] and eid not in states['analyzed']
    assert eid not in examined_passages(s,aid,passages(a))
    assert any(r['evidence_id']==eid for r in passage_index(s,a,limit=80)['passages'])


def test_question_review_can_close_depth_without_exhaustive_reading_and_stales_on_change():
    s=copy.deepcopy(case('stokes')['state']);a=Article.model_validate(s['articles'][STOKES])
    eid=a.analysis['evidence'][0]['evidence_id']
    review=CoverageReview(question='Quelle méthode est rapportée ?',sufficient=True,
        dimensions=[{'dimension':'methods','status':'verified','scope':'examined_passages','evidence_ids':[eid],'reason':'Méthode documentée'},
            {'dimension':'metrics','status':'unread','scope':'examined_passages','evidence_ids':[],'reason':'Hors question','essential':False}],uncertainties=['Résultats non réévalués'])
    save_review(s,a,review)
    status=reading_status(s,a)
    assert status['question_sufficient'] and status['states']['examined']<status['states']['available']
    p=project(s,scope=Scope(1,'offline'))
    assert not any(t['kind']=='review_depth' and t['article_id']==STOKES for t in p['tasks'].values())
    a.analysis['analysis_limit']='Nouvelle lecture'
    assert not reading_status(s,a)['question_sufficient']
    s['messages'].append({'id':'new_request','role':'user','content':'Autre question'})
    assert not reading_status(s,a)['review_current']


def test_essential_unknown_or_unpresented_evidence_cannot_be_reviewed_as_sufficient():
    s=copy.deepcopy(case('stokes')['state']);a=Article.model_validate(s['articles'][STOKES])
    review=CoverageReview(question='Résultats ?',sufficient=True,dimensions=[{'dimension':'results','status':'unread','reason':'Non lu'}])
    with pytest.raises(ValueError,match='essentielle'):save_review(s,a,review)
    review=CoverageReview(question='Résultats ?',sufficient=True,dimensions=[{'dimension':'results','status':'verified','reason':'Non lu','evidence_ids':['ev_unknown']}])
    with pytest.raises(ValueError,match='présentées'):save_review(s,a,review)


def test_index_tool_returns_section_cursor_without_reading_or_saving_claims():
    s=copy.deepcopy(archived()['state']);aid=next(aid for aid,a in s['articles'].items() if a.get('fulltext',{}).get('text'))
    shown=list(s.get('shown_evidence_ids',[]));analysis=copy.deepcopy(s['articles'][aid]['analysis'])
    t=adapter().toolkit(s)
    args={**read(aid).arguments,'action':'index','index_limit':2}
    first=t.execute('analyze_article',args,time.monotonic()+60)['indexes'][0]
    assert first['passages'] and first['next_offset']==2
    second=t.execute('analyze_article',{**args,'index_offset':2},time.monotonic()+60)['indexes'][0]
    assert not {p['evidence_id'] for p in first['passages']} & {p['evidence_id'] for p in second['passages']}
    assert s.get('shown_evidence_ids',[])==shown and s['articles'][aid]['analysis']==analysis


def test_known_cards_are_references_after_actual_delivery_and_new_claims_remain_accessible():
    from agents.EnnoScholarV5.runtime import MEMORY_PREFIX
    s=copy.deepcopy(case('stokes')['state']);ad=adapter()
    t,first=ad.prepare(s)
    def claims(inputs):
        m=next(json.loads(i['content'][len(MEMORY_PREFIX):]) for i in inputs if i.get('content','').startswith(MEMORY_PREFIX))
        return [c for a in [*m.get('selected_articles',[]),*m.get('consulted_articles',[])] for cs in a.get('analysis',{}).values() for c in cs]
    assert claims(first)
    _,not_sent=ad.prepare(s)
    assert claims(not_sent)  # Preparing twice cannot acknowledge a card.
    ad.delivered(t,first)
    _,second=ad.prepare(s)
    assert len(claims(second))<len(claims(first))
    assert any('saved_card_ref' in i.get('content','') for i in second)
    assert s['articles'][STOKES]['analysis']==case('stokes')['state']['articles'][STOKES]['analysis']
    s['v5_finalization_mode']=True
    _,final=ad.prepare(s)
    assert claims(final)  # A scientific synthesis can still use the saved claims.


def test_deferred_ids_and_cursors_survive_and_are_prioritized_without_false_presentation():
    from agents.EnnoScholarV5 import workflow
    from .test_cir_workflow import real_batch
    s=copy.deepcopy(archived()['state']);ad=adapter();decision,raw=real_batch()[0]
    t=ad.toolkit(s)
    # Build an actual queued window, then fit to a deliberately small local room.
    for row in raw['reading']:
        t.reading_windows.append({'window_id':row['reading_window_id'],'article_id':row['article']['id'],
            'status':'queued','evidence_ids':[p['evidence_id'] for p in row['passages']]})
    t.save()
    fitted=workflow.fit_output(s,t,decision,copy.deepcopy(raw),4500)
    workflow.remember_deferred(s,decision,fitted)
    tasks=[task for task in s['scientific_reading']['tasks'] if task.get('deferred_evidence_ids')]
    assert tasks
    restored=json.loads(json.dumps(s))
    for task in tasks:
        aid=task['article_id'];index=passage_index(restored,Article.model_validate(restored['articles'][aid]),limit=80)
        candidates={p['evidence_id'] for p in index['passages']}
        assert candidates & set(task['deferred_evidence_ids'])
        assert any(r['status']=='pending' and r['offset']==decision.arguments['passage_offset'] for r in task['deferred_reads'].values())


def measurement_case():
    s=copy.deepcopy(archived()['state'])
    for aid,a in s['articles'].items():
        for e in a.get('analysis',{}).get('evidence',[]):
            match=re.search(r'(?<![\d.,+\-−])(\d+(?:[.,]\d+)?)\s*%',e['exact_quote'])
            if match and e['evidence_id'] in s.get('shown_evidence_ids',[]):
                return s,aid,e,match.group(1)
    raise AssertionError('No quantified source in archived CO2 cards')


def test_measurements_merge_with_old_claims_and_proofs_and_are_retrievable():
    s,aid,e,value=measurement_case();old=copy.deepcopy(s['articles'][aid]['analysis'])
    args={**read(aid).arguments,'action':'save','analysis':payload(e['evidence_id']),
        'measurements':[{'metric':'Valeur rapportée en pourcentage','value_raw':value,'unit':'%',
            'experimental_conditions':'Conditions décrites dans le passage cité','measurement_method':'Mesure rapportée par les auteurs',
            'evidence_ids':[e['evidence_id']],'origin':'observed_result','source_kind':'text'}]}
    t=adapter().toolkit(s);out=t.execute('analyze_article',args,time.monotonic()+60)
    assert out['saved'] and out['measurements_saved']==1 and out['coverage']['dimensions']
    t.execute('analyze_article',args,time.monotonic()+60)
    current=s['articles'][aid]['analysis']
    assert len(current['structured_measurements'])==1
    assert {e['evidence_id'] for e in old['evidence']} <= {e['evidence_id'] for e in current['evidence']}
    assert all(c in current.get(field,[]) for field,claims in old.items() if isinstance(claims,list) and claims and isinstance(claims[0],dict) and 'statement' in claims[0] for c in claims)
    page=t.execute('get_analysis_claims',{'article_id':aid,'fields':['metrics'],'offset':0},time.monotonic()+60)
    assert page['structured_measurements'][0]['value_raw']==value
    assert page['structured_measurements'][0]['experimental_conditions']


@pytest.mark.parametrize('change',[{'value_raw':'987654321.123'},{'unit':'unité inventée'},{'evidence_ids':['ev_unknown']}])
def test_invented_measurements_are_rejected_before_existing_card_changes(change):
    s,aid,e,value=measurement_case();before=copy.deepcopy(s['articles'][aid]['analysis'])
    measurement={'metric':'Pourcentage rapporté','value_raw':value,'unit':'%',
        'experimental_conditions':'Voir preuve','measurement_method':'Mesure source','evidence_ids':[e['evidence_id']],
        'origin':'author_interpretation',**change}
    args={**read(aid).arguments,'action':'save','analysis':payload(e['evidence_id']),'measurements':[measurement]}
    with pytest.raises(ValueError):adapter().toolkit(s).execute('analyze_article',args,time.monotonic()+60)
    assert s['articles'][aid]['analysis']==before


def comparison_case():
    from agents.EnnoScholarV5.evidence import snapshot
    s=copy.deepcopy(archived()['state']);found=[]
    for aid,a in s['articles'].items():
        fp=snapshot(Article.model_validate(a))['fingerprint']
        proof=next((e for e in a.get('analysis',{}).get('evidence',[]) if e['source_fingerprint']==fp and e['evidence_id'] in s.get('shown_evidence_ids',[])),None)
        if proof:found.append((aid,proof['evidence_id']))
        if len(found)==2:break
    assert len(found)==2
    ids=[aid for aid,_ in found];eids=[eid for _,eid in found]
    s['turn_goal']={'deliverable':'comparison','references':{'article_ids':ids,'scope':'exclusive'}}
    relation={'article_ids':ids,'kind':'complementarity','statement':'Approches complémentaires, dont les conditions doivent rester explicites.',
        'origin':'comparative_inference','conditions':'Conditions propres à chaque protocole ; aucun classement numérique.',
        'experimental_results':[],'evidence_ids':eids,'comparable_protocols':False,'uncertainties':['Protocoles à confronter']}
    args={'article_ids':ids,'research_context':'Comparer approches et limites','conclusions':None,'relations':[relation]}
    return s,ids,eids,args


def test_existing_comparison_validator_saves_reuses_and_restores_relations():
    from agents.EnnoScholarV5.science import comparison_work
    s,ids,eids,args=comparison_case();t=adapter().toolkit(s)
    before=comparison_work(s)
    assert before[0]['status']=='consider_comparison'
    first=t.execute('compare_articles',args,time.monotonic()+60)
    assert first['saved'] and len(s['scientific_relations'])==1
    t.execute('compare_articles',args,time.monotonic()+60)
    assert len(s['scientific_relations'])==1
    restored=json.loads(json.dumps(s));other=adapter().toolkit(restored)
    result=other.execute('compare_articles',{**args,'relations':None},time.monotonic()+60)
    assert result['scientific_relations'][0]['evidence_ids']==eids
    assert comparison_work(restored)[0]['existing_relation_ids']
    p=project(restored,scope=Scope(1,'offline'))
    assert not any(task['kind']=='consider_comparison' for task in p['tasks'].values())
    restored['turn_goal']['deliverable']='deep_reading'
    assert comparison_work(restored)==[]  # No automatic comparison or manuscript for a simple article question.


def test_noncomparable_protocols_cannot_be_saved_as_superiority():
    s,ids,eids,args=comparison_case()
    args['relations'][0].update(kind='conditional_superiority',comparable_protocols=False,
        experimental_results=[{'statement':'Résultat cité','evidence_ids':[eids[0]],'origin':'source_statement'}])
    with pytest.raises(ValueError,match='protocoles comparables'):adapter().toolkit(s).execute('compare_articles',args,time.monotonic()+60)
    assert not s.get('scientific_relations')


def test_structured_different_conditions_block_numeric_superiority_even_when_flag_true():
    s,ids,eids,args=comparison_case()
    for aid,eid,condition in zip(ids,eids,['électrolyte A','électrolyte B']):
        s['articles'][aid]['analysis']['structured_measurements']={'existing':{'metric':'FE','unit':'%',
            'experimental_conditions':condition,'measurement_method':'Méthode source','evidence_ids':[eid]}}
    args['relations'][0].update(kind='conditional_superiority',comparable_protocols=True,
        experimental_results=[{'statement':'Résultat cité','evidence_ids':[eids[0]],'origin':'source_statement'}])
    with pytest.raises(ValueError,match='protocoles des mesures différents'):adapter().toolkit(s).execute('compare_articles',args,time.monotonic()+60)


def test_comparison_cache_is_invalidated_when_saved_science_changes():
    from experiments.ennoscholar_v5.policy import semantic_key
    from experiments.ennoscholar_v5.adapter import Decision
    s,ids,eids,args=comparison_case();decision=Decision('compare_articles',args)
    before=semantic_key(s,decision)
    s['articles'][ids[0]]['analysis']['analysis_limit']='Nouvelle condition scientifique documentée'
    assert semantic_key(s,decision)!=before


def test_index_and_review_receipts_follow_request_card_and_delivery():
    from experiments.ennoscholar_v5.policy import semantic_key
    from experiments.ennoscholar_v5.adapter import Decision
    s=copy.deepcopy(archived()['state']);aid=next(iter(s['articles']))
    for action in ('index','review'):
        d=Decision('analyze_article',{'article_ids':[aid],'action':action})
        key=semantic_key(s,d)
        s['messages'].append({'id':action,'role':'user','content':'Nouvelle question'})
        assert semantic_key(s,d)!=key
        key=semantic_key(s,d);s['articles'][aid]['analysis']['analysis_limit']=action
        assert semantic_key(s,d)!=key
        key=semantic_key(s,d);s.setdefault('shown_evidence_ids',[]).append('receipt_'+action)
        assert semantic_key(s,d)!=key


def test_section_packet_uses_full_relations_measurements_and_preserves_plan():
    from agents.EnnoScholarV5 import workflow
    from agents.EnnoScholarV5.evidence import snapshot
    s,ids,eids,args=comparison_case()
    for aid,eid in zip(ids,eids):
        s['articles'][aid]['analysis']['structured_measurements']={'fixture':{
            'id':'measurement_fixture','metric':'Résultat structuré de test','value_raw':'42','unit':'%',
            'experimental_conditions':'Conditions explicitement propres au protocole', 'measurement_method':'Méthode documentée',
            'evidence_ids':[eid],'origin':'author_interpretation',
            'source_fingerprints':[snapshot(Article.model_validate(s['articles'][aid]))['fingerprint']]}}
    args['relations'][0]['experimental_results']=[{'statement':'Résultat comparatif partiel, sans supériorité.',
        'evidence_ids':eids,'origin':'inference'}]
    t=adapter().toolkit(s);t.execute('compare_articles',args,time.monotonic()+60)
    section={'id':'section_test','title':'Méthodes, résultats et limites','article_ids':ids,
        'article_roles':[{'article_id':ids[0],'role':'conditions expérimentales'}, {'article_id':ids[1],'role':'limitations'}]}
    s['outline']={'title':'Plan validé','status':'approved','plan_revision':2,'sections':[section]}
    before=copy.deepcopy(s['outline'])
    packet=workflow.section_packet(s,t,section,40000)
    detail=packet['scientific_detail']
    assert detail['relations'][0]['experimental_results']==args['relations'][0]['experimental_results']
    assert len(detail['structured_measurements'])==2
    assert all(m['experimental_conditions'] and m['measurement_method'] and m['origin'] for m in detail['structured_measurements'])
    assert set(detail['source_coverage'])==set(ids)
    assert set(eids)<={e['evidence_id'] for e in detail['evidence']}
    assert packet['proof_backed_claims'] and len(json.dumps(packet,ensure_ascii=False))<=40000
    assert s['outline']==before
    s['articles'][ids[0]]['analysis']['analysis_limit']='Source scientifique enrichie depuis comparaison'
    assert not workflow.section_packet(s,adapter().toolkit(s),section,40000)['scientific_detail']['relations']


def test_source_role_prioritizes_whole_claims_without_changing_v4():
    from agents.EnnoScholarV5.writing_context import section_dossier
    from agents.EnnoScholarV4.writing_context import section_dossier as original
    s,ids,eids,args=comparison_case();aid=ids[0];eid=eids[0]
    common={'evidence_ids':[eid],'origin':'source_statement'}
    s['articles'][aid]['analysis']['results']=[
        {'statement':'Résultat général de référence.',**common},
        {'statement':'Conditions thermiques et stabilité cyclique.',**common}]
    section={'id':'role_test','title':'Résultats','article_ids':[aid],
        'article_roles':[{'article_id':aid,'role':'stabilité cyclique'}]}
    t=adapter().toolkit(s)
    packet=section_dossier(s,t,section,20000)
    results=[c for c in packet['proof_backed_claims'] if c['field']=='results']
    assert results[0]['statement']=='Conditions thermiques et stabilité cyclique.'
    assert original.__module__=='agents.EnnoScholarV4.writing_context'
    assert set(results[0]['evidence_ids'])<={e['evidence_id'] for e in packet['evidence']}


def test_scripted_science_chain_through_responses_graph_and_resume(app_env, monkeypatch):
    """Real archived text, scripted scientific choices; no claim about prose quality."""
    from agents.EnnoScholarV5.evidence import passages
    from agents.EnnoScholarV5 import workflow
    from experiments.ennoscholar_v5 import graph
    from .test_cir_workflow import dossier, section_text, confirm_after_preparation, preparation_refusals
    make,factory,_=app_env
    s,ids,eids,comparison=comparison_case();aid=ids[0]
    rows=passages(Article.model_validate(s['articles'][aid]))
    row=next(r for r in rows if r['evidence_id'] not in s.get('shown_evidence_ids',[]) and re.search(r'(?<![\d.])\d+(?:\.\d+)?\s*%',r['text']))
    target=row['evidence_id']
    s.pop('outline',None)
    adapter().toolkit(s).execute('manage_outline',{'action':'create','title':'Confrontation des méthodes',
        'section_id':None,'target_section_id':None,'position':None,'article_ids':None,
        'sections':[{'title':'Approches, résultats et limites','description':'Comparer les conditions avec leurs preuves.',
            'article_ids':ids,'article_roles':[{'article_id':a,'role':'Méthodes et résultats dans leurs conditions'} for a in ids]}]},time.monotonic()+60)
    from agents.EnnoScholarV4.manuscript import approve_outline
    approve_outline(s,1)  # Section 1 of a plan the consultant approved.
    outline_before=copy.deepcopy(s['outline'])
    def output(sent,tag):
        return next(json.loads(i['output']) for i in sent['input'] if i.get('type')=='function_call_output' and i['call_id']==tag)
    def choose_read(sent):
        index=output(sent,'index')['indexes'][0]
        assert any(p['evidence_id']==target and not p['presented'] for p in index['passages'])
        return call('analyze_article',{**read(aid).arguments,'evidence_ids':[target]},'read')
    def choose_save(sent):
        passage=output(sent,'read')['reading'][0]['passages'][0]
        assert passage['evidence_id']==target
        value=re.search(r'(?<![\d.])(\d+(?:\.\d+)?)\s*%',row['text']).group(1)
        card=payload(target)
        card['metrics']=[{'statement':f'Le passage rapporte {value} % ; les conditions doivent être précisées.',
            'evidence_ids':[target],'origin':'source_statement'}]
        return call('analyze_article',{**read(aid).arguments,'action':'save','analysis':card,'measurements':[
            {'metric':'Pourcentage rapporté dans la source','value_raw':value,'unit':'%',
                'experimental_conditions':'Conditions du passage cité ; comparabilité non établie',
                'measurement_method':'Méthode décrite dans la source ; approfondissement requis',
                'evidence_ids':[target],'origin':'observed_result'}]},'save')
    def choose_review(sent):
        assert output(sent,'save')['measurements_saved']==1
        return call('analyze_article',{**read(aid).arguments,'action':'review','coverage_review':{
            'question':'Quels résultats et limites peut-on comparer ?','sufficient':False,
            'dimensions':[{'dimension':'metrics','status':'verified','scope':'examined_passages','evidence_ids':[target],
                'reason':'Valeur littérale conservée'}, {'dimension':'experimental_protocol','status':'unread',
                'scope':'examined_passages','evidence_ids':[],'reason':'Comparabilité non établie'}],
            'uncertainties':['Protocoles non comparables à ce stade']}},'review')
    def choose_compare(sent):
        assert not output(sent,'review')['coverage']['question_sufficient']
        comparison['relations'][0]['evidence_ids']=[target,eids[1]]
        return call('compare_articles',comparison,'compare')
    def choose_write(sent):
        packet=dossier(sent)
        assert packet['scientific_detail']['relations']
        assert packet['scientific_detail']['structured_measurements'][0]['evidence_ids']==[target]
        assert 'Protocoles non comparables à ce stade' in packet['scientific_detail']['source_coverage'][aid]['uncertainties']
        text,evidence,cited=section_text(packet)
        return call('write_section',{'section_id':packet['section']['id'],'action':'save','instructions':'Section autorisée, comparaison partielle.',
            'text':text,'article_ids':cited,'evidence_ids':evidence},'write')
    choices=[call('analyze_article',{**read(aid).arguments,'action':'index','section_filter':row.get('section'),
        'index_limit':80},'index'),choose_read,choose_save,choose_review,choose_compare,choose_write,
        confirm_after_preparation(lambda sent: None), confirm_after_preparation(lambda sent: None)]
    svc,sid,tr=make(choices,s)
    original=graph.Runner.__init__;crashed=[]
    def init(self,scope,repository,*args,**kwargs):
        def after(opid):
            op=repository.operation(scope,opid)
            if op['kind']=='action' and op['request']['tool']=='compare_articles' and not crashed:
                crashed.append(opid);raise RuntimeError('offline interruption after saved relation')
        kwargs.setdefault('after_commit',after)
        original(self,scope,repository,*args,**kwargs)
    monkeypatch.setattr(graph.Runner,'__init__',init)
    first=run_sync(svc,sid,'Rédige uniquement la section 1 du plan, avec une comparaison partielle et ses limites.')
    assert first['reason'].startswith('v5_interrupted'),first
    from agents.EnnoScholarV5.persistence import Budget
    with factory() as db:
        budget_before=copy.deepcopy(db.get(Budget,first['id']).data)
    saved=scientific_state(factory,sid)
    assert len(saved['scientific_relations'])==1 and saved['articles'][aid]['analysis']['structured_measurements']
    monkeypatch.setattr(graph.Runner,'__init__',original)
    svc.resume_run(1,first['id']);svc.pool.shutdown(wait=True)
    result=svc.run(1,first['id']);saved=scientific_state(factory,sid)
    assert result['writing_progress']['status']=='draft_complete',result
    # One more response when the section's preparation was refused once and its text confirmed.
    refused=len(preparation_refusals(factory))
    assert len(tr.requests)==6+refused and result['usage']['llm_calls']==len(tr.requests)
    assert len(saved['scientific_relations'])==1 and len(saved['outline']['sections'][0]['drafts'])==1
    assert saved['outline']['sections'][0]['article_ids']==outline_before['sections'][0]['article_ids']
    assert saved['outline']['plan_revision']==outline_before['plan_revision']
    assert result['budget_limits']==budget_before['limits']
    with factory() as db:
        budget_after=db.get(Budget,first['id']).data
        assert {e['id'] for e in budget_before['entries']} < {e['id'] for e in budget_after['entries']}
        assert len(budget_before['entries'])==5 and len(budget_after['entries'])==6+refused
    assert target in saved['shown_evidence_ids']


def test_failed_claim_lookup_does_not_break_delivery_of_other_results():
    from agents.EnnoScholarV5 import workflow
    s=copy.deepcopy(case('stokes')['state'])
    workflow.acknowledge_inputs(s,[call('get_analysis_claims',{'article_id':'unknown','fields':[],'offset':0},'missing'),
        {'type':'function_call_output','call_id':'missing','output':json.dumps({'error':'Unknown article'})}])
    assert 'unknown' not in workflow.knowledge(s).get('cards',{})


def test_already_delivered_reading_reuses_identifiers_without_reinjecting_text():
    from experiments.ennoscholar_v5.policy import reusable, semantic_key
    from experiments.ennoscholar_v5.adapter import Decision
    s=copy.deepcopy(case('stokes')['state']);eid=s['shown_evidence_ids'][0]
    d=read(STOKES,evidence_ids=[eid]);output={'reading':[{'passages':[{'evidence_id':eid,'text':'previous full payload'}]}]}
    prior=[{'response':{'semantic_key':semantic_key(s,d),'output':output}}]
    _,replayed,reason=reusable(s,d,prior)
    assert reason=='same_operation' and replayed['presented_evidence_ids']==[eid] and 'reading' not in replayed
    revisit=Decision(d.tool,{**d.arguments,'revisit':True})
    assert reusable(s,revisit,prior)[1] is None
    s['shown_evidence_ids'].remove(eid)
    assert reusable(s,d,prior)[1]==output  # A result not actually delivered still needs its full payload.


def test_distinct_long_jats_section_names_are_not_collapsed():
    from experiments.ennoscholar_v5.progress import reading_map
    prefix='Conditions expérimentales détaillées et protocoles '
    rows=[{'section':prefix+ending,'page':None,'evidence_id':'ev_'+ending} for ending in ('A','B')]
    groups,pages=reading_map(rows,set())
    assert len(groups)==2 and pages==[]
