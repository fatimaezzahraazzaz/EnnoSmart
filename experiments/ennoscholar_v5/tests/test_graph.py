"""Real trace states, real V4 tools, scripted Sol, official persistent savers."""
import copy
from dataclasses import replace
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from experiments.ennoscholar_v5.adapter import Decision, ScriptedSol, V4Adapter
from experiments.ennoscholar_v5.checkpoints import sqlite_checkpoints
from experiments.ennoscholar_v5.graph import Runner
from experiments.ennoscholar_v5.progress import Scope,project
from experiments.ennoscholar_v5.repository import Repository, RevisionConflict
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.evidence import exact_source_valid,passages
from agents.EnnoScholarV4.models import Article

ROOT=Path(__file__).resolve().parents[3]
ARTIFACTS=Path(__file__).resolve().parents[1]/'.artifacts'
LI='paper_77167a12e30e685dd624'
FANG='paper_60a461ff4f7691d3a285'
BISZ='paper_c22423b316f7580bd6b6'
STOKES='paper_7545a91a2f4cb5bcdefd'


def case(name):
    with gzip.open(ARTIFACTS/(name+'.json.gz'),'rt',encoding='utf-8') as stream:return json.load(stream)


def setup(tmp_path,name='chemistry',state=None):
    c=case(name);s=copy.deepcopy(c['state'] if state is None else state)
    scope=Scope(c['owner_id'],c['session_id'],s.get('project_id'))
    repo=Repository(tmp_path/'science.sqlite3',writable_root=tmp_path);repo.seed(scope,s)
    return c,s,scope,repo


def read(aid,**kwargs):
    return Decision('analyze_article',{'article_ids':[aid],'action':'read','research_context':'Existing consultant request',
        'passage_query':'method results limits','passage_offset':0,'passages_per_article':4,'analysis':None,**kwargs})


def payload(eid):
    # This mirrors the original validated schema, without inventing a result.
    fields=('problem','objective','method','contribution','hypotheses','results','limitations','generalization',
        'open_questions','relevance','technical_mechanism','experimental_setup')
    return {**{k:[] for k in fields},'method':[{'statement':'Méthode examinée dans le passage local.',
        'evidence_ids':[eid],'origin':'source_statement'}],'method_family':'Source locale',
        'analysis_limit':'Lecture ciblée ; résultats et protocoles restent à examiner.'}


def test_real_pending_work_is_not_just_unsaved_readings():
    c=case('chemistry');scope=Scope(c['owner_id'],c['session_id'],c['state'].get('project_id'))
    p=project(c['state'],scope=scope)
    assert p['counts']['target']==10 and p['counts']['proposed']==5 and p['counts']['missing']==5
    assert len(p['articles'])==50
    kinds={(t['article_id'],t['kind']) for t in p['tasks'].values()}
    assert (LI,'read_local') in kinds
    assert (FANG,'recover_document') in kinds and (BISZ,'recover_document') in kinds
    assert (LI,'save_analysis') not in kinds
    assert p['counts']['analyses_saved']==2
    assert len(c['state']['research_progress']['pending_analysis_ids'])==1  # V4's narrower view.


def test_stokes_retains_page_cursor_and_specific_reading_limits():
    c=case('stokes');s=c['state'];scope=Scope(c['owner_id'],c['session_id'],s.get('project_id'))
    p=project(s,scope=scope);a=p['articles'][STOKES]
    assert a['document']=='partial' and a['next_page']==21 and a['page_count']==29
    assert a['analysis_saved'] and a['reading_coverage']=='targeted_passages'
    assert p['counts']['analyses_saved']==1  # Not zero because no numbered list was displayed.
    assert len(a['read_evidence_ids'])==27 and len(a['saved_evidence_ids'])==13
    assert any(t['kind']=='continue_extraction' and t['next_page']==21 for t in p['tasks'].values())
    assert any(t['kind']=='review_depth' for t in p['tasks'].values())


def current_read_id(inputs):
    for item in reversed(inputs):
        if item.get('role')=='developer':
            try:data=json.loads(item['content'])
            except ValueError:continue
            output=data.get('v5_saved_tool_result',{})
            for row in output.get('reading',[]):
                for passage in row.get('passages',[]):
                    return passage['evidence_id']
    raise AssertionError('No exact passage in the current model input')


def test_more_than_six_useful_turns_then_explicit_restart_without_resetting_usage(tmp_path):
    c,s,scope,repo=setup(tmp_path)
    choices=[replace(read(LI,passage_offset=i*4),cost_usd=.01,input_tokens=100) for i in range(7)]
    choices.append(Decision('pause'))
    sol=ScriptedSol(choices)
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        r=Runner(scope,repo,V4Adapter(),sol,saver)
        out=r.start(historical_usage={'cost_usd':c['run']['usage']['estimated_cost_usd']})
        assert out['__interrupt__'][0].value['reason']=='model_requested_pause'
        assert len(sol.calls)==8 and out['progress']['counts']['missing']==5
        before=out['usage']['cost_usd']
    # A NEW graph/checkpointer object, not just an in-memory continuation.
    sol2=ScriptedSol([read(LI,passage_offset=28),Decision('pause')])
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        r=Runner(scope,Repository(tmp_path/'science.sqlite3',writable_root=tmp_path),V4Adapter(),sol2,saver)
        out=r.resume()
        assert out['slice']==1 and out['usage']['llm_calls']==10 and out['usage']['cost_usd']==before
        assert out['progress']['articles'][LI]['read_evidence_ids']
        assert out['progress']['counts']['missing']==5 and out['__interrupt__']


def test_read_save_resume_reuses_the_actual_li_xml_and_unchanged_other_science(tmp_path):
    c,s,scope,repo=setup(tmp_path)
    protected=copy.deepcopy({aid:a for aid,a in s['articles'].items() if aid!=LI})
    def save(inputs,progress):
        eid=current_read_id(inputs)
        return Decision('analyze_article',{'article_ids':[LI],'action':'save','research_context':'Methods',
            'passage_query':'','passage_offset':0,'passages_per_article':4,'analysis':payload(eid)})
    sol=ScriptedSol([read(LI),save,Decision('pause')])
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(),sol,saver).start()
    final=repo.load(scope)[1]
    assert final['articles'][LI]['analysis'] and final['articles'][LI]['analysis_basis']=='fulltext'
    assert final['articles'][LI]['fulltext']==s['articles'][LI]['fulltext']
    assert {aid:a for aid,a in final['articles'].items() if aid!=LI}==protected
    assert out['progress']['counts']['missing']==5
    assert all(exact_source_valid(e,final['source_snapshots']) for e in final['evidence'].values())


@pytest.mark.parametrize('page_limit,next_page',[(2,23),(9,None)])
def test_actual_stokes_pdf_continues_locally_without_touching_old_proofs(tmp_path,monkeypatch,page_limit,next_page):
    c,s,scope,repo=setup(tmp_path,'stokes')
    monkeypatch.setenv('ENNOSCHOLAR_V4_PDF_DIRECTORY',str(ARTIFACTS/'pdf'))
    pdf=ARTIFACTS/'pdf'/s['articles'][STOKES]['fulltext']['pdf_file'];digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
    d=Decision('get_fulltext',{'article_id':STOKES,'candidate_urls':None,'page_start':21,'page_limit':page_limit})
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(),ScriptedSol([d,Decision('pause')]),saver).start()
    final=repo.load(scope)[1];ft=final['articles'][STOKES]['fulltext']
    assert ft['next_page']==next_page and ft['page_count']==29 and ft['truncated']==(next_page is not None)
    assert all(ft['page_extraction'][str(p)]['status']=='complete' for p in range(21,21+page_limit))
    assert final['evidence']==s['evidence'] and final['articles'][STOKES]['analysis']==s['articles'][STOKES]['analysis']
    assert hashlib.sha256(pdf.read_bytes()).hexdigest()==digest
    assert out['progress']['articles'][STOKES]['analysis_saved']
    assert out['progress']['articles'][STOKES]['document']==('partial' if next_page else 'extracted')
    assert out['progress']['articles'][STOKES]['reading_coverage']=='targeted_passages'


def test_graph_does_not_claim_to_fix_pdf_headings_or_title_conflicts():
    s=case('stokes')['state'];a=s['articles'][STOKES]
    assert any(c.get('page')==18 and c['section']=='REFERENCES' for c in a['fulltext']['chunks'])
    from agents.EnnoScholarV4.sources import title_key
    assert title_key('CO 2 Electrolysis')!=title_key('CO<sub>2</sub> Electrolysis')


def test_crash_after_tool_commit_is_recovered_without_duplicate_read(tmp_path):
    c,s,scope,repo=setup(tmp_path)
    adapter=V4Adapter();execute=adapter.execute;calls=[]
    adapter.execute=lambda *a,**k:(calls.append('tool') or execute(*a,**k))
    def crash(opid):
        if repo.operation(scope,opid)['kind']=='action':raise RuntimeError('simulated process crash after scientific commit')
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        r=Runner(scope,repo,adapter,ScriptedSol([read(LI)]),saver,after_commit=crash)
        with pytest.raises(RuntimeError):r.start()
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,adapter,ScriptedSol([Decision('pause')]),saver).resume()
    assert calls==['tool'] and out['usage']['tools']==1
    assert out['progress']['articles'][LI]['read_evidence_ids']


def test_crash_after_model_receipt_does_not_call_model_again(tmp_path):
    c,s,scope,repo=setup(tmp_path)
    sol=ScriptedSol([read(LI)])
    def crash(opid):
        if repo.operation(scope,opid)['kind']=='decision':raise RuntimeError('crash after model receipt')
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        with pytest.raises(RuntimeError):Runner(scope,repo,V4Adapter(),sol,saver,after_commit=crash).start()
    sol2=ScriptedSol([Decision('pause')])
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(),sol2,saver).resume()
    assert len(sol.calls)==1 and len(sol2.calls)==1
    assert out['progress']['articles'][LI]['read_evidence_ids']


def test_uncertain_call_is_not_automatically_rebilled(tmp_path):
    c,s,scope,repo=setup(tmp_path)
    def broken(*args):raise RuntimeError('provider response unknown')
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        with pytest.raises(RuntimeError):Runner(scope,repo,V4Adapter(),ScriptedSol([broken]),saver).start()
    second=ScriptedSol([read(LI)])
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(),second,saver).resume()
    assert not second.calls and out['__interrupt__'][0].value['reason']=='uncertain_model_call'


@pytest.mark.parametrize('usage',[{'cost_usd':4.},{'input_tokens':400000},{'tools':48},{'seconds':600}])
def test_job_budgets_do_not_reset_on_resume(tmp_path,usage):
    c,s,scope,repo=setup(tmp_path);sol=ScriptedSol([read(LI)])
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        r=Runner(scope,repo,V4Adapter(),sol,saver);out=r.start(historical_usage=usage)
        assert out['__interrupt__'][0].value['reason']=='job_budget' and not sol.calls
        with pytest.raises(ValueError):r.resume()


def test_known_next_reservation_is_refused_before_call(tmp_path):
    c,s,scope,repo=setup(tmp_path);sol=ScriptedSol([Decision('get_article',{'identifiers':[LI]},cost_usd=.3)])
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(),sol,saver).start(historical_usage={'cost_usd':3.9})
    assert not sol.calls and out['__interrupt__'][0].value['reason']=='job_budget'


def test_user_project_and_session_isolation(tmp_path):
    c,s,scope,repo=setup(tmp_path)
    for bad in (replace(scope,owner_id=scope.owner_id+1),replace(scope,project_id='foreign'),replace(scope,session_id='other')):
        with pytest.raises(PermissionError):repo.load(bad)
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        foreign=Runner(replace(scope,owner_id=999999),repo,V4Adapter(),ScriptedSol([]),saver)
        with pytest.raises(PermissionError):foreign.start()


def test_checkpoint_contains_only_progress_and_references(tmp_path):
    c,s,scope,repo=setup(tmp_path,'stokes')
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        r=Runner(scope,repo,V4Adapter(),ScriptedSol([Decision('pause')]),saver);r.start()
        for snapshot in r.graph.get_state_history(r.config):
            text=json.dumps(snapshot.values,ensure_ascii=False)
            assert 'exact_quote' not in text and '"fulltext":' not in text and 'source_snapshots' not in text
            assert s['articles'][STOKES]['fulltext']['text'][:150] not in text
    with sqlite3.connect(tmp_path/'science.sqlite3') as db:
        assert db.execute('select count(*) from sessions').fetchone()[0]==1


def test_discovery_stays_open_and_consultant_rejection_is_preserved(tmp_path):
    s=case('chemistry')['state']
    s['articles'][LI]['selection_override']=False
    c,s,scope,repo=setup(tmp_path,state=s)
    # New article is unexpected and is NOT automatically selected by the graph.
    unexpected=Article(title='Unexpected electrochemical discovery',doi='10.1234/new',authors=['Author'],year=2026)
    sources=Mock();sources.search.return_value=([unexpected],[])
    sources.metadata.side_effect=AssertionError('No metadata request')
    adapter=V4Adapter(sources=sources,fixture_sources=True)
    d=Decision('search_scientific',{'query':'copper stability alternative mechanism','sources':['openalex','semantic_scholar','arxiv'],
        'offset':0,'limit_per_source':12,'family':'Open scientific choice'})
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,adapter,ScriptedSol([d,Decision('pause')]),saver).start()
    final=repo.load(scope)[1]
    assert len(final['articles'])==51 and any(a['doi']=='10.1234/new' for a in final['articles'].values())
    new=next(a for a in final['articles'].values() if a['doi']=='10.1234/new')
    assert not new.get('selected') and new['selection_override'] is None
    assert final['articles'][LI]['selection_override'] is False
    assert out['progress']['counts']['target']==10
    assert sources.search.call_count==1


def test_no_live_model_is_instantiated(tmp_path):
    c,s,scope,repo=setup(tmp_path)
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        with pytest.raises(PermissionError):Runner(scope,repo,V4Adapter(),object(),saver)


def test_postgres_uses_the_official_saver_without_a_connection():
    from langgraph.checkpoint.postgres import PostgresSaver
    import inspect
    assert 'conn' in inspect.signature(PostgresSaver).parameters
    from experiments.ennoscholar_v5.checkpoints import postgres_checkpoints
    with pytest.raises(ValueError):
        with postgres_checkpoints('never-connect',schema='public'):pass
