"""Same archived science + same scripted decisions, never a quality benchmark of Sol.

Run from the experimental worktree with the existing Python executable, -B.
The offline bootstrap blocks network, dotenv and writes outside this worktree.
"""
from experiments.ennoscholar_v5 import run_offline as _offline
import copy
import gzip
import json
import os
from pathlib import Path
import time
import uuid

from experiments.ennoscholar_v5.adapter import Decision,ScriptedSol,V4Adapter
from experiments.ennoscholar_v5.checkpoints import sqlite_checkpoints
from experiments.ennoscholar_v5.graph import Runner
from experiments.ennoscholar_v5.progress import Scope,project
from experiments.ennoscholar_v5.repository import Repository
from agents.EnnoScholarV4.evidence import passages,exact_source_valid
from agents.EnnoScholarV4.models import Article

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'.artifacts'
LI='paper_77167a12e30e685dd624';STOKES='paper_7545a91a2f4cb5bcdefd'


def load(name):
    with gzip.open(DATA/(name+'.json.gz'),'rt',encoding='utf-8') as stream:return json.load(stream)


def save_visible_quote(aid):
    def choose(inputs,progress):
        for item in reversed(inputs):
            if item.get('role')!='developer':continue
            try:output=json.loads(item['content']).get('v5_saved_tool_result',{})
            except ValueError:continue
            for row in output.get('reading',[]):
                for p in row.get('passages',[]):
                    # This is an extraction fixture, not a model-written scientific analysis.
                    quote=p.get('exact_quote') or p.get('text')
                    if not quote:continue
                    fields=('problem','objective','method','contribution','hypotheses','results','limitations',
                        'generalization','open_questions','relevance','technical_mechanism','experimental_setup')
                    analysis={**{f:[] for f in fields},'method':[{'statement':quote[:700],
                        'evidence_ids':[p['evidence_id']],'origin':'source_statement'}],
                        'method_family':'Offline exact-source fixture',
                        'analysis_limit':'Test de transport d’un passage exact ; analyse scientifique non exhaustive.'}
                    return Decision('analyze_article',{'article_ids':[aid],'action':'save','research_context':'Methods',
                        'passage_query':'','passage_offset':0,'passages_per_article':4,'analysis':analysis})
        raise AssertionError('No source passage delivered')
    return choose


def choices(name,s):
    aid=LI if name=='chemistry' else STOKES
    result=[]
    if name=='stokes':
        result.append(Decision('get_fulltext',{'article_id':aid,'candidate_urls':None,'page_start':21,'page_limit':9}))
        # New page extraction may change unregistered passage-window IDs. Ask
        # the ordinary reader for the enriched representation, not a stale ID
        # computed before extraction. Persisted evidence IDs remain preserved.
        exact={'revisit':True,'passage_query':'STAR METHODS neural network architecture training ensemble Chemprop'}
    else:exact={}
    result.append(Decision('analyze_article',{'article_ids':[aid],'action':'read','research_context':'Methods',
        'passage_query':'method results limits','passage_offset':0,'passages_per_article':4,'analysis':None,**exact}))
    return [*result,save_visible_quote(aid),Decision('pause')]


def direct_replay(s,decisions):
    adapter=V4Adapter();sol=ScriptedSol(decisions);last=None;tools=0
    scope=Scope(1,'direct',s.get('project_id'))
    for _ in decisions:
        t,inputs=adapter.prepare(s,last)
        d=sol.choose(inputs,[],project(s,scope=scope),instructions=adapter.instructions)
        adapter.delivered(t,inputs)
        if d.tool=='pause':break
        last=adapter.execute(s,d,remaining_seconds=600);tools+=1
    return s,tools,len(sol.calls),sum(len(json.dumps(call['inputs'],ensure_ascii=False)) for call in sol.calls)


def compare():
    folder=DATA/('comparison-'+uuid.uuid4().hex);folder.mkdir()
    assert folder.resolve().is_relative_to(ROOT.resolve())
    os.environ['ENNOSCHOLAR_V4_PDF_DIRECTORY']=str(DATA/'pdf')
    report={'scope':'Offline plumbing comparison; scripted choices, no scientific quality or paid-cost measurement',
        'model':'gpt-6.1-sol','paid_api_calls':0,'cases':{}}
    for name in ('chemistry','stokes'):
        c=load(name);scope=Scope(c['owner_id'],c['session_id'],c['state'].get('project_id'))
        s=copy.deepcopy(c['state']);started=time.monotonic()
        direct,tools,calls,input_chars=direct_replay(copy.deepcopy(s),choices(name,s));direct_seconds=time.monotonic()-started
        repo=Repository(folder/(name+'-science.sqlite3'),writable_root=folder);repo.seed(scope,s)
        sol=ScriptedSol(choices(name,s));started=time.monotonic()
        with sqlite_checkpoints(folder/(name+'-graph.sqlite3')) as saver:
            runner=Runner(scope,repo,V4Adapter(),sol,saver);out=runner.start()
            checkpoint_bytes=max(len(json.dumps(snapshot.values,ensure_ascii=False).encode())
                for snapshot in runner.graph.get_state_history(runner.config))
        graph_seconds=time.monotonic()-started;final=repo.load(scope)[1]
        assert direct['articles']==final['articles']
        assert direct['evidence']==final['evidence'] and direct['source_snapshots']==final['source_snapshots']
        assert all(exact_source_valid(e,final['source_snapshots']) for e in final['evidence'].values())
        aid=LI if name=='chemistry' else STOKES
        # The existing writer obtains the same saved source statement locally.
        packet=V4Adapter().writing_packet(final,{'id':'methods','title':'Methods and architecture',
            'article_ids':[aid],'drafts':[]})
        methods=final['articles'][aid]['analysis'].get('method',[])
        assert any(claim['statement']==row.get('statement') for claim in methods for row in packet.get('proof_backed_claims',[]))
        report['cases'][name]={'run_id':c['run_id'],'recorded_v4_run_seconds':c['run'].get('duration_seconds'),
            'recorded_v4_usage':c['run'].get('usage'),
            'before':project(s,scope=scope)['counts'],'after':out['progress']['counts'],
            'source_tools_identical':True,'direct_tools':tools,'graph_tools':out['usage']['tools'],
            'direct_scripted_decisions':calls,'graph_scripted_decisions':len(sol.calls),
            'direct_input_chars_total':input_chars,
            'graph_input_chars_total':sum(len(json.dumps(call['inputs'],ensure_ascii=False)) for call in sol.calls),
            'direct_offline_seconds':round(direct_seconds,3),'graph_offline_seconds':round(graph_seconds,3),
            'max_checkpoint_state_bytes':checkpoint_bytes,'canonical_session_bytes':len(json.dumps(final).encode()),
            'next_page':final['articles'][aid]['fulltext'].get('next_page'),
            'reading_coverage':final['articles'][aid]['analysis'].get('reading_coverage'),
            'pending_tasks':len(out['progress']['tasks']),'science_reaches_existing_writer':True,
            'stop_reason':out['__interrupt__'][0].value['reason']}
    (DATA/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


def restart_demo(phase):
    c=load('chemistry');s=c['state'];scope=Scope(c['owner_id'],c['session_id'],s.get('project_id'))
    science=DATA/'process-restart-science.sqlite3'
    if phase=='start' and science.exists():raise ValueError('Do not overwrite an existing experiment')
    if phase=='resume' and not science.exists():raise ValueError('Run start in another process first')
    repo=Repository(science,writable_root=DATA)
    if phase=='start':repo.seed(scope,s)
    offset=0 if phase=='start' else 4
    decisions=[Decision('analyze_article',{'article_ids':[LI],'action':'read',
        'research_context':'Local continuation','passage_query':'method results limits',
        'passage_offset':offset,'passages_per_article':4,'analysis':None}),Decision('pause')]
    with sqlite_checkpoints(DATA/'process-restart-graph.sqlite3') as saver:
        runner=Runner(scope,repo,V4Adapter(),ScriptedSol(decisions),saver)
        out=runner.start() if phase=='start' else runner.resume()
    result={'phase':phase,'llm_simulated_calls':out['usage']['llm_calls'],'tool_calls':out['usage']['tools'],
        'read_evidence_count':len(out['progress']['articles'][LI]['read_evidence_ids']),
        'counts':out['progress']['counts'],'cost_usd':out['usage']['cost_usd']}
    assert result['llm_simulated_calls']==(2 if phase=='start' else 4)
    assert result['tool_calls']==(1 if phase=='start' else 2) and result['counts']['missing']==5
    target=DATA/'process-restart.json'
    report=json.loads(target.read_text(encoding='utf-8')) if phase=='resume' else []
    if phase=='resume':assert result['read_evidence_count']>report[0]['read_evidence_count']
    report.append(result);target.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['start','resume'])
    args=parser.parse_args()
    restart_demo(args.phase) if args.phase else compare()
