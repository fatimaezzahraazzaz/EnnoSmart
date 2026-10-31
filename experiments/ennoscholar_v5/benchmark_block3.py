"""Paired offline replay of the three archived requests. Never contacts a provider.

Source trees may differ; fixtures, scripted decisions, estimator and price catalogue
are held constant. Reported usage is SIMULATED, not a supplier measurement.
"""
import argparse
import copy
import gzip
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import types
import time

LIVE = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, default=LIVE)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source_root.resolve()
    output = args.output.resolve()
    artifacts = (LIVE/'experiments/ennoscholar_v5/.artifacts').resolve()
    if not output.is_relative_to(artifacts):
        raise ValueError('Benchmark output must stay in isolated V5 artifacts')
    artifacts.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='benchmark-block3-', dir=artifacts)).resolve()
    assert temp.is_relative_to(artifacts)
    os.environ.update(TMP=str(temp),TEMP=str(temp),TMPDIR=str(temp))
    tempfile.tempdir = str(temp)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(LIVE/'experiments/ennoscholar_v5/.deps'), str(source/'backend_api'), str(source)]
    os.environ.update(DATABASE_URL='sqlite:///:memory:', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
        LANGSMITH_TRACING='false', LANGCHAIN_TRACING_V2='false', OPENAI_API_KEY='offline')
    for name in ('OPENAI_BASE_URL', 'ENNOSCHOLAR_V5_MISSION_BUDGET_USD', 'ENNOSCHOLAR_V5_SESSION_BUDGET_USD'):
        os.environ.pop(name, None)
    dotenv = types.ModuleType('dotenv')
    dotenv.load_dotenv = lambda *a, **k: False
    dotenv.dotenv_values = lambda *a, **k: {}
    sys.modules['dotenv'] = dotenv

    def audit(event, values):
        if event in {'socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system'}:
            raise AssertionError('Offline benchmark: network and subprocess forbidden')
        if event == 'sqlite3.connect' and values[0] != ':memory:' and not Path(values[0]).resolve().is_relative_to(temp):
            raise AssertionError('Offline benchmark: real database forbidden')
        if event == 'open' and isinstance(values[0], (str, bytes)):
            p = Path(os.fsdecode(values[0]))
            if p.name.startswith('.env') or p.name.endswith('.env'):
                raise AssertionError('Offline benchmark: no credential files')
            flags = values[2] if len(values) > 2 else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                if not p.resolve().is_relative_to(temp) and p.resolve() != output:
                    raise AssertionError('Offline benchmark: writes restricted to isolated output')

    sys.addaudithook(audit)
    import httpx
    from unittest.mock import Mock
    from sqlalchemy import select
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV5 import toolkit
    from agents.EnnoScholarV5.persistence import Budget, Operation
    from experiments.ennoscholar_v5.tests.test_live_integration import app_env, call, run_sync, scientific_state, Transport
    assert Path(toolkit.__file__).resolve().is_relative_to(source)
    with gzip.open(artifacts/'block3-reference.json.gz', 'rt', encoding='utf8') as f:
        fixture = json.load(f)
    old_respond = Transport.respond

    def respond(self, request):
        response = old_respond(self, request)
        sent = json.loads(request.content)
        marker = 'data: '
        event = json.loads(response.text.split(marker, 1)[1].split('\n\n', 1)[0])
        result = event['response']
        # Same estimator and zero cache assumption on BOTH trees. No invented
        # provider token count or cache gain is passed off as historical billing.
        input_bytes = sum(len(json.dumps(sent.get(k, []), ensure_ascii=False).encode()) for k in ('input', 'tools'))
        input_bytes += len((sent.get('instructions') or '').encode())
        tokens = math.ceil(input_bytes/4)
        out_tokens = math.ceil(len(json.dumps(result['output'], ensure_ascii=False).encode())/4)
        result['usage'] = {'input_tokens': tokens, 'output_tokens': out_tokens,
            'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}}
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
            text='event: response.completed\ndata: '+json.dumps(event)+'\n\ndata: [DONE]\n\n')

    Transport.respond = respond
    toolkit.base.get_fulltext = lambda *a, **k: copy.deepcopy(fixture['failed_fulltext'])
    root = temp/'data'; root.mkdir()
    env = app_env.__wrapped__(root)
    make, factory, _ = next(env)
    results = []
    try:
        state = copy.deepcopy(fixture['state'])
        state['v5_final_verification'] = {'attempted': list(state['articles']), 'ids': []}
        for article in state['articles'].values():
            article['verified'], article['metadata_errors'] = True, []
        cfg = ScholarV4Config(api_key='offline', web_search_enabled=False, prompt_cache_mode='explicit')
        svc, sid, tr = make([], state, config=cfg, sources=Mock())
        for index, case in enumerate(fixture['scenarios']):
            before = scientific_state(factory, sid)
            queue = copy.deepcopy(case['decisions'])
            seen = []

            def choose(sent):
                if not queue:
                    raise AssertionError('Script exhausted: replay no longer matches this scenario')
                d = queue.pop(0)
                current = scientific_state(factory, sid)
                new = next((s['id'] for s in current.get('outline', {}).get('sections', [])
                    if 'Perspectives futures' in s['title']), None)

                def replace_ids(value):
                    if isinstance(value, str):
                        return value.replace('section_531111ecbdd44728aa70c66e0cd5cea2', new) if new else value
                    if isinstance(value, dict): return {k: replace_ids(v) for k, v in value.items()}
                    if isinstance(value, list): return [replace_ids(v) for v in value]
                    return value

                seen.append(d['tool'])
                if d['tool'] == 'v5_continue':
                    text = next(x['arguments']['answer'] for x in queue if x['tool'] == 'finalize_response')
                    return {'type': 'message', 'id': 'msg', 'status': 'completed', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
                calls = [d, *d.get('calls', [])]
                return [call(x['tool'], replace_ids(x['arguments']), f'bench_{index}_{len(seen)}_{j}') for j, x in enumerate(calls)]

            start = len(tr.requests)
            with factory() as db:
                previous_operations = len(list(db.scalars(select(Operation).where(Operation.kind=='action'))))
            tr.choices[:] = [choose]*32
            started = time.perf_counter()
            result = run_sync(svc, sid, case['request'])
            wall_seconds = time.perf_counter()-started
            after = scientific_state(factory, sid)
            if not tr.requests[start:] or str(result.get('reason')).startswith(('provider_error','v5_interrupted')):
                raise AssertionError('Replay failed before completing its scripted workflow: '+str(result.get('reason')))
            with factory() as db:
                budget = copy.deepcopy(db.get(Budget, result['id']).data)
                ops = list(db.scalars(select(Operation).where(Operation.kind == 'action').order_by(Operation.revision)))[previous_operations:]
            saved = [s for s in after.get('outline', {}).get('sections', []) if s.get('drafts')]
            results.append({'request': case['request'], 'usage_simulated': result['usage'],
                'provider_requests': len(tr.requests)-start,
                'payload_bytes': [len(json.dumps(q, ensure_ascii=False).encode()) for q in tr.requests[start:]],
                'request_blocks': [{'input_bytes':len(json.dumps(q.get('input',[]),ensure_ascii=False).encode()),
                    'tools_bytes':len(json.dumps(q.get('tools',[]),ensure_ascii=False).encode()),
                    'instructions_bytes':len((q.get('instructions') or '').encode()),
                    'max_output_tokens':q.get('max_output_tokens')} for q in tr.requests[start:]],
                'wall_seconds_offline':wall_seconds, 'outcome':result.get('outcome'),
                'operations':[{'tool':o.request['tool'],'action':o.request.get('arguments',{}).get('action'),
                    'reuse_reason':(o.response or {}).get('reuse_reason'),
                    'saved':(o.response or {}).get('output',{}).get('saved'),
                    'rejection_code':(o.response or {}).get('output',{}).get('rejection_code')} for o in ops],
                'decisions_consumed': seen, 'scripted_decisions_remaining': len(queue),
                'outline': [{'id': s['id'], 'title': s['title'], 'drafts': len(s.get('drafts', []))} for s in after['outline']['sections']],
                'saved_sections': len(saved), 'old_drafts_preserved': all(
                    s.get('drafts') == next((t.get('drafts') for t in after['outline']['sections'] if t['id'] == s['id']), [])
                    for s in before['outline']['sections'] if s.get('drafts')),
                'ledger_entries': [{k: e.get(k) for k in ('status', 'reserved_tokens', 'reserved_usd', 'input_tokens', 'output_tokens', 'cost_usd')}
                    for e in budget['entries']], 'reason': result.get('reason'), 'answer': result['answer']})
    finally:
        try: next(env)
        except StopIteration: pass
    report = {'source_root': str(source), 'transport': 'httpx.MockTransport', 'estimator': 'serialized UTF8 bytes / 4',
        'cache': 'zero hits/writes on both replays', 'simulation_only': True, 'scenarios': results}
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf8')
    print(json.dumps({'output': str(output), 'calls': [r['provider_requests'] for r in results],
        'tokens': [r['usage_simulated']['input_tokens']+r['usage_simulated']['output_tokens'] for r in results]}))


if __name__ == '__main__':
    main()
