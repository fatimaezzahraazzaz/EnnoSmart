"""Authorized live non-regression comparisons; never replace stored diagnostics.

Reads the three baseline reports in read-only SQL transactions. Model inputs
are solely their raw DE fulltexts. Baseline answers are used only afterwards.
All new caches, traces and results stay in the workspace validation directory.
"""
from __future__ import annotations
import hashlib
import argparse
import json
import shutil
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from lock_diagnostic_run18 import read_run
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.full_runtime import build_full_runtime
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalFastPathConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_fast_path import build_passages, GlobalFastExtractor
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent


def dump(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


def summarize(core):
    locks=[e for e in core['entities'] if e['kind']=='lock']
    fast=core.get('audit',{}).get('global_fast_path',{})
    return {'locks':locks,'lock_count':len(locks),
            'statuses':dict(Counter(e.get('metadata',{}).get('qualification_status','NOT_EVALUATED') for e in locks)),
            'fact_counts':dict(Counter(i['kind'] for i in core['extractions'])),
            'project_context_count':sum(1 for i in core['extractions'] if i.get('metadata',{}).get('source_role')=='context'),
            'completeness':fast.get('completeness_audit',{}), 'fast':fast}


def prepare(name,rid,folder):
    run,artifact,report,_=read_run(rid)
    v2=report['diagnostic_v2']; core=v2['core']
    corpus=Path(report['fulltext_corpus']['path'])
    raw={}
    for path in corpus.glob('*.json'):
        if path.name=='manifest.json':continue
        data=json.loads(path.read_text(encoding='utf-8'))
        if str(data.get('content_origin','')).lower().startswith('cir_final'):
            raise RuntimeError('Final CIR is not an authorized extraction input')
        raw[str(data['document_id'])]=data
    docs=[DocumentInput(str(d['document_id']),d['name'],raw[str(d['document_id'])]['text'].strip()) for d in core['documents']]
    job=folder/name;job.mkdir(parents=True)
    dump(job/'before.json',summarize(core))
    config=GlobalFastPathConfig()
    passages=build_passages(docs,config.passage_max_chars)
    preflight=GlobalFastExtractor(None,config).preflight(docs)
    dump(job/'input_manifest.json',{'baseline_run_id':rid,'documents':[{'id':d.document_id,'name':d.name,
         'chars':len(d.text),'sha256':hashlib.sha256(d.text.encode()).hexdigest()} for d in docs],
         'passages':len(passages),'preflight':preflight})
    return name,docs,v2.get('domain_tags'),job


def execute(job, reuse=None):
    name,docs,tags,folder=job;start=time.monotonic()
    print(f'[REAL VALIDATION] START {name}',flush=True)
    runtime=build_full_runtime(domain_tags=tags,fast_cache_dir=str(folder/'cache'))
    runtime.core.debug_directory=folder/'debug'
    try:
        if reuse is not None:
            previous=reuse/name
            old_manifest=json.loads((previous/'input_manifest.json').read_text(encoding='utf-8'))
            new_manifest=json.loads((folder/'input_manifest.json').read_text(encoding='utf-8'))
            if old_manifest['documents']!=new_manifest['documents']:
                raise ValueError('Cannot reuse Fast facts from a changed corpus')
            if (previous/'cache').is_dir():
                shutil.copytree(previous/'cache',folder/'cache',dirs_exist_ok=True)
            trace=Path(json.loads((previous/'execution.json').read_text(encoding='utf-8'))['debug'])
            events=[json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()]
            checked=next(row['data']['facts'] for row in events if row['stage']=='fast.validated_facts')
            fast=runtime.core.fast_extractor
            registry={p.passage_id:p for p in build_passages(docs,fast.config.passage_max_chars)}
            checked=[fast._validate(row,registry,provenance_reviewed=bool(row.get('provenance_repaired') and row.get('provenance_reason'))) for row in checked]
            fast.debug_sink=SimpleNamespace(path=trace)
            fast._write_cache(fast._fingerprint(docs),checked)
            fast.debug_sink=None
            dump(folder/'reused_fast.json',{'source_trace':str(trace),'validated_fact_count':len(checked),
                 'reason':'Exact same raw corpus; reuse actual validated model output, never baseline answers'})
        result=runtime.run(docs).to_dict()
        dump(folder/'after_full.json',result)
        dump(folder/'after.json',summarize(result['core']))
        events=[json.loads(line) for line in Path(runtime.core.last_debug_reference).read_text(encoding='utf-8').splitlines()]
        sent=next(row['data'] for row in events if row['stage']=='qualification.input_candidates')
        dump(folder/'qualifier_input.json',sent)
        audit=result['core']['audit']
        summary={'project':name,'elapsed_seconds':round(time.monotonic()-start,2),'locks':len(sent),
                 'lock_coverage_complete':audit.get('lock_coverage_complete'),
                 'lock_intergroup_validated':audit.get('lock_intergroup_validated'),'debug':runtime.core.last_debug_reference}
    except Exception as exc:
        summary={'project':name,'error':f'{type(exc).__name__}:{exc}', 'debug':runtime.core.last_debug_reference,
                 'elapsed_seconds':round(time.monotonic()-start,2)}
    dump(folder/'execution.json',summary)
    print('[REAL VALIDATION] END '+json.dumps(summary,ensure_ascii=True),flush=True)
    return summary


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--reuse-fast-from',type=Path)
    args=parser.parse_args()
    folder=ROOT/'ennodiagnostic_validations'/('lock_invariants_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'))
    folder.mkdir(parents=True)
    print('[REAL VALIDATION] OUTPUT '+str(folder),flush=True)
    jobs=[prepare(name,rid,folder) for name,rid in [('AI-CODE',26),('ANAX',24),('ROAD',23)]]
    outcomes=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(execute,job,args.reuse_fast_from) for job in jobs]
        for future in as_completed(futures):
            outcomes.append(future.result());dump(folder/'summary.json',outcomes)


if __name__=='__main__': main()
