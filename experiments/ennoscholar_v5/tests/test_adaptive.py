"""Adaptive progression: real archived science, deterministic offline decisions."""
import copy
from dataclasses import replace
import json
from unittest.mock import Mock
import pytest

from .test_graph import case,setup,read,LI,FANG,BISZ,STOKES,payload
from experiments.ennoscholar_v5.adapter import V4Adapter,Decision,ScriptedSol
from experiments.ennoscholar_v5.graph import Runner
from experiments.ennoscholar_v5.checkpoints import sqlite_checkpoints
from experiments.ennoscholar_v5.policy import semantic_key,reusable,useful_need
from experiments.ennoscholar_v5.repository import RevisionConflict
from experiments.ennoscholar_v5.progress import project,Scope
from agents.EnnoScholarV4.evidence import passages
from agents.EnnoScholarV4.models import Article


def search(query='copper durability alternative',purpose=''):
    return Decision('search_scientific',{'query':query,'sources':['openalex'],
        'offset':0,'limit_per_source':8,'family':'Catalyst durability'},purpose=purpose)


def fixtures(s):
    src=Mock();src.search.return_value=([Article.model_validate(s['articles'][LI])],[])
    src.metadata.side_effect=AssertionError('No remote metadata')
    return src


def test_repeated_queries_with_no_new_articles_pause_without_a_search_loop(tmp_path):
    c,s,scope,repo=setup(tmp_path);sources=fixtures(s)
    sol=ScriptedSol([search('alternative copper formulation'),search('different copper formulation'),search('third formulation')])
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(sources=sources,fixture_sources=True),sol,saver).start()
    assert out['__interrupt__'][0].value['reason']=='stagnation'
    assert len(sol.calls)==2 and sources.search.call_count==2
    assert out['progress']['counts']['missing']==5
    assert (LI,'read_local') in {(t['article_id'],t['kind']) for t in out['progress']['tasks'].values()}


def test_identical_provider_query_is_not_reexecuted(tmp_path):
    c,s,scope,repo=setup(tmp_path);sources=fixtures(s)
    sol=ScriptedSol([search(),search(),search()])
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(sources=sources,fixture_sources=True),sol,saver).start()
    assert sources.search.call_count==1 and len(sol.calls)==2
    assert out['usage']['tools']==1
    assert repo.completed_actions(scope)[-1]['response']['reuse_reason']=='same_operation'


def test_same_discovery_tools_have_identical_candidates_with_and_without_graph(tmp_path):
    c,s,scope,repo=setup(tmp_path);direct=copy.deepcopy(s)
    source1=fixtures(s);source2=fixtures(s);d=search()
    V4Adapter(sources=source1,fixture_sources=True).execute(direct,d,remaining_seconds=30)
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(sources=source2,fixture_sources=True),ScriptedSol([d,Decision('pause')]),saver).start()
    final=repo.load(scope)[1]
    assert final['articles']==direct['articles']
    assert final['evidence']==direct['evidence'] and final['source_snapshots']==direct['source_snapshots']
    assert len(final['articles'])==50 and out['progress']['counts']['proposed']==5
    assert source1.search.call_count==source2.search.call_count==1


def test_an_archived_document_and_already_extracted_pages_are_reused():
    s=case('stokes')['state']
    for args in ({'article_id':STOKES,'page_start':None,'page_limit':None,'candidate_urls':None},
            {'article_id':STOKES,'page_start':1,'page_limit':20,'candidate_urls':None}):
        key,output,reason=reusable(s,Decision('get_fulltext',args),[])
        assert output['reused_local'] and output['next_page']==21 and reason=='archived_document'
    _,output,_=reusable(s,Decision('get_fulltext',{'article_id':STOKES,'page_start':21,'page_limit':9}),[])
    assert output is None  # The nine remaining pages stay accessible.


def test_an_explicit_missing_information_can_request_another_representation():
    s=case('stokes')['state']
    d=Decision('get_fulltext',{'article_id':STOKES,'page_start':None,
        'complement_reason':'The archived representation lacks the methods appendix'})
    assert reusable(s,d,[])[1] is None


def test_same_passages_for_a_different_scientific_question_remain_possible():
    s=case('stokes')['state'];d=replace(read(STOKES),purpose='Architecture',arguments={**read(STOKES).arguments,'revisit':True})
    previous=[{'response':{'semantic_key':semantic_key(s,d),'output':{'reading':[]}}}]
    assert reusable(s,d,previous)[2]=='same_operation'
    assert reusable(s,replace(d,purpose='Validation protocol'),previous)[1] is None


def test_successful_identical_analysis_is_not_generated_or_saved_twice():
    s=case('stokes')['state'];d=Decision('analyze_article',{'article_ids':[STOKES],'action':'save','analysis':{'method':[]}})
    previous=[{'response':{'semantic_key':semantic_key(s,d),'output':{'analysis_saved':True}}}]
    assert reusable(s,d,previous)[1]=={'analysis_saved':True}
    changed=copy.deepcopy(s);changed['articles'][STOKES]['fulltext']['document_version']['version_id']='unverified-new-version'
    assert reusable(changed,d,previous)[1] is None  # Revalidate with V4, never transfer proof blindly.


@pytest.mark.parametrize('failure',[{'error':'ReadTimeout'}, {'failure_code':'scientific_body_unavailable','access_level':'abstract_only'}])
def test_failed_access_is_not_an_automatic_retry_or_a_success(failure):
    s=case('chemistry')['state'];d=Decision('get_fulltext',{'article_id':FANG,'candidate_urls':None})
    previous=[{'response':{'semantic_key':semantic_key(s,d),'output':failure}}]
    assert reusable(s,d,previous)[1]['not_executed']
    # V4 retry_at still enforces retry timing/status/attempt limits inside the actual tool.
    assert reusable(s,replace(d,retry_reason='Transient server outage resolved; explicit retry'),previous)[1] is None
    other=replace(d,arguments={**d.arguments,'candidate_urls':['https://example.org/author-manuscript.pdf']})
    assert reusable(s,other,previous)[1] is None


def test_failed_representations_and_retries_stay_with_the_correct_article():
    from agents.EnnoScholarV4.tools.fulltext import retry_at
    s=case('chemistry')['state'];scope=Scope(1,'fixture',s.get('project_id'))
    s['articles'][FANG]['fulltext']={'attempts':[
        {'url':'https://example.org/one','stage':'download','http_status':404},
        {'url':'https://example.org/two','stage':'download','http_status':403,'attempt_number':2}]}
    p=project(s,scope=scope)
    assert len(p['articles'][FANG]['access_attempts'])==2
    assert not p['articles'][BISZ]['access_attempts']
    assert all(retry_at(a) is None for a in s['articles'][FANG]['fulltext']['attempts'])
    assert any(t['kind']=='recover_document' and t['article_id']==FANG for t in p['tasks'].values())


def test_stokes_mislabelled_methods_are_locally_readable_by_proof_id_and_writer():
    s=case('stokes')['state'];before=copy.deepcopy(s['articles'][STOKES]['analysis'])
    adapter=V4Adapter();a=Article.model_validate(s['articles'][STOKES])
    methods=[p for p in passages(a) if p.get('page') in (18,19)]
    assert methods  # Heading errors have not hidden/deleted the body passages.
    eid=methods[0]['evidence_id']
    d=read(STOKES,evidence_ids=[eid],revisit=True)
    output=adapter.execute(s,d,remaining_seconds=30)
    assert any(p['evidence_id']==eid for row in output['reading'] for p in row.get('passages',[]))
    packet=adapter.writing_packet(s,{'id':'methods','title':'Methods and architecture',
        'article_ids':[STOKES],'aim':'Compare the saved methods','drafts':[]})
    text=json.dumps(packet,ensure_ascii=False)
    saved_methods=before.get('technical_mechanism') or before.get('method')
    assert STOKES in text and saved_methods[0]['statement'] in text
    assert s['articles'][STOKES]['analysis']==before


def test_unknown_evidence_still_refuses_an_analysis_save():
    s=case('chemistry')['state'];before=copy.deepcopy(s['articles'][LI]['analysis'])
    d=Decision('analyze_article',{**read(LI).arguments,'action':'save','analysis':payload('ev_wrong_article')})
    output=V4Adapter().execute(s,d,remaining_seconds=30)
    assert output.get('error') and s['articles'][LI]['analysis']==before


def test_a_historical_checkpoint_cannot_replay_against_future_science(tmp_path):
    c,s,scope,repo=setup(tmp_path)
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        r=Runner(scope,repo,V4Adapter(),ScriptedSol([Decision('pause')]),saver);r.start()
        opid='manual-test-change';rev,state=repo.load(scope)
        repo.begin(scope,opid,rev,'test',{});repo.claim(scope,opid);repo.finish(scope,opid,rev,state,{})
        with pytest.raises(RevisionConflict):r.resume()


def test_no_new_search_for_an_exact_article_without_a_scientific_purpose():
    s=case('stokes')['state'];scope=Scope(1,'fixture',s.get('project_id'))
    s['articles']={STOKES:s['articles'][STOKES]}
    s['research_progress'].pop('requested_count',None)
    p=project(s,scope=scope)
    assert not useful_need(p,search())
    assert useful_need(p,search(purpose='Compare external replication studies explicitly requested'))


def final_partial(s):
    doi=s['articles'][LI]['doi']
    return Decision('finalize_response',{'answer':f'La recherche reste incomplète. [Article](https://doi.org/{doi}) : '
        'le texte est disponible, mais sa lecture et sa comparaison restent à effectuer.',
        'article_ids':[LI],'evidence_ids':[],'coverage_sufficient':False,
        'remaining_uncertainties':['Comparaison non terminée'],'displayed_article_ids':[LI]})


def test_partial_publication_keeps_the_verified_results_and_can_resume(tmp_path):
    c,s,scope,repo=setup(tmp_path)
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        first=Runner(scope,repo,V4Adapter(),ScriptedSol([final_partial(s)]),saver).start()
        assert first['status']=='partial' and first['response_ref']
        assert first['progress']['counts']['missing']==5  # Cumulative proposal list stays distinct from this reply.
        out=Runner(scope,repo,V4Adapter(),ScriptedSol([read(LI),Decision('pause')]),saver).resume()
        assert out['progress']['articles'][LI]['read_evidence_ids']
        assert out['usage']['llm_calls']==3


def test_invalid_bibliography_is_not_declared_valid_by_the_graph(tmp_path):
    s=case('chemistry')['state'];s['articles'][LI]['verified']=False
    s['articles'][LI]['metadata_errors']=['Unresolved DOI/title conflict']
    c,s,scope,repo=setup(tmp_path,state=s)
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(),ScriptedSol([final_partial(s),Decision('pause')]),saver).start()
    attempts=repo.load(scope)[1]['finalization_attempts']
    assert attempts[-1]['rejection_code']=='invalid_bibliography' and not attempts[-1]['published']
    assert not out.get('response_ref')


def test_context_preparation_failure_is_not_an_uncertain_paid_call(tmp_path):
    c,s,scope,repo=setup(tmp_path);adapter=V4Adapter();sol=ScriptedSol([Decision('pause')])
    adapter.prepare=Mock(side_effect=ValueError('Local context failure before API'))
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        with pytest.raises(ValueError):Runner(scope,repo,adapter,sol,saver).start()
    assert not sol.calls
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(),sol,saver).resume()
    assert len(sol.calls)==1 and out['__interrupt__'][0].value['reason']=='model_requested_pause'


def test_original_scientific_instructions_and_bounded_work_view_are_transmitted(tmp_path):
    from agents.EnnoScholarV4.original import SYSTEM,DISCOVERY_GUIDANCE
    from experiments.ennoscholar_v5.progress import model_work_view
    c,s,scope,repo=setup(tmp_path);sol=ScriptedSol([Decision('pause')])
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(),sol,saver).start()
    assert sol.calls[0]['instructions']==SYSTEM+'\n'+DISCOVERY_GUIDANCE
    view=model_work_view(out['progress'])
    assert len(json.dumps(view,ensure_ascii=False))<=10000
    assert {r['article_id'] for r in view['articles'] if r['in_request_scope']} == {
        aid for aid,a in out['progress']['articles'].items() if a['in_request_scope']}
    assert view['counts']['missing']==5 and view['pending_by_kind']['read_local']>0
    assert view['omitted_articles']>0
    assert any('v5_work_state' in x.get('content','') for x in sol.calls[0]['inputs'])
    assert len(json.dumps(sol.calls[0]['inputs'],ensure_ascii=False))<=V4Adapter().config.max_context_chars


def test_cached_metadata_cannot_hide_a_new_analysis_or_consultant_decision():
    s=case('chemistry')['state'];d=Decision('get_article',{'identifiers':[LI]})
    previous=[{'response':{'semantic_key':semantic_key(s,d),'output':{'articles':[]}}}]
    assert reusable(s,d,previous)[2]=='same_operation'
    changed=copy.deepcopy(s);changed['articles'][LI]['analysis']={'method':[{'statement':'new supported claim'}]}
    assert reusable(changed,d,previous)[1] is None
    changed=copy.deepcopy(s);changed['articles'][LI]['selection_override']=False
    assert reusable(changed,d,previous)[1] is None
