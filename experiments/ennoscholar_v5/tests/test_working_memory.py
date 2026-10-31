"""Regressions from real run f147eded (session 3445749b, nine CO2 articles, 28/10).

Observed: two decisions read the nine cards, then five decisions re-requested
the same claims by field subsets (229 claims, none new). Each was credited as
progress, so stagnation never triggered and the token budget was spent without
any new reading or save. Scripted Sol over the real state and Responses SDK
(simulated HTTP): these tests prove what is credited and what stays visible,
not how the real Sol will choose.
"""
import copy
import gzip
import json
import re
from types import SimpleNamespace
from unittest.mock import Mock
from sqlalchemy import select
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.models import Article
from agents.EnnoScholarV5.evidence import passages
from agents.EnnoScholarV5.persistence import Operation
from agents.EnnoScholarV5.runtime import ScientificAdapter, MEMORY_PREFIX
from .test_graph import ARTIFACTS, read, payload
from .test_live_integration import app_env, call, run_sync, scientific_state

NINE = ['paper_8ba44ae1e2f0383cdf4b', 'paper_77167a12e30e685dd624', 'paper_58c0ee6deccbb222b0c5',
    'paper_96ff11926e02db7e1b35', 'paper_985d8c9ad0be085e50c1', 'paper_9dd10ef6befd58991cd0',
    'paper_55bb2ecb80ab1dbe916e', 'paper_606fdde9135cae73eba2', 'paper_4bcdfdf3560046d9b59e']


def real():
    with gzip.open(ARTIFACTS/'real-f147-run.json.gz', 'rt', encoding='utf-8') as f:
        return json.load(f)


def adapter():
    return ScientificAdapter(ScholarV4Config(api_key='offline', web_search_enabled=False), Mock(), sources=Mock())


def real_decisions():
    return [o['response']['decision'] for o in real()['operations'] if o['kind'] == 'decision']


def actions(revisions):
    return [o for o in real()['operations'] if o['kind'] == 'action' and o['revision'] in revisions]


def as_response(decision, tag):
    items = [decision, *decision.get('calls', [])]
    return [call(d['tool'], d['arguments'], f'{tag}_{i}') for i, d in enumerate(items)]


def working(sent):
    item = next((i for i in sent['input'] if isinstance(i.get('content'), str) and i['content'].startswith(workflow.WORKING_PREFIX)), None)
    return json.loads(item['content'][len(workflow.WORKING_PREFIX):]) if item else None


def test_known_claims_are_not_novel_but_new_passages_and_indexes_are():
    state = copy.deepcopy(real()['before'])
    state['messages'].append({'id': 'request_f147', 'role': 'user', 'content': real()['run']['request']})
    first = actions({204})[0]
    claims = SimpleNamespace(tool='get_analysis_claims', arguments=first['request']['arguments'])
    assert workflow.novel_delivery(state, claims, first['response']['output'])
    # Same claims again in the same response, then after their confirmed delivery.
    assert not workflow.novel_delivery(state, claims, first['response']['output'])
    workflow.acknowledge_inputs(state, [{'type': 'function_call', 'call_id': 'c', 'name': 'get_analysis_claims',
        'arguments': json.dumps(first['request']['arguments'])},
        {'type': 'function_call_output', 'call_id': 'c', 'output': json.dumps(first['response']['output'], ensure_ascii=False)}])
    subset = {**first['response']['output'], 'claims': first['response']['output']['claims'][:2]}
    assert not workflow.novel_delivery(state, SimpleNamespace(tool='get_analysis_claims', arguments={
        **first['request']['arguments'], 'fields': ['results']}), subset)
    reading = actions({215})[0]
    decision = SimpleNamespace(tool='analyze_article', arguments=reading['request']['arguments'])
    assert workflow.novel_delivery(state, decision, reading['response']['output'])  # Passages never read.
    assert not workflow.novel_delivery(state, decision, reading['response']['output'])
    assert not workflow.novel_delivery(state, decision, {'not_executed': True, 'reason': 'deferred_transmission_budget'})


def test_working_memory_keeps_consulted_claims_and_deferred_passages_for_nine_articles():
    state = copy.deepcopy(real()['before'])
    state['messages'].append({'id': 'request_f147', 'role': 'user', 'content': real()['run']['request']})
    state['turn_goal'] = {**state['turn_goal'], 'deliverable': 'deep_reading',
        'references': {**(state['turn_goal'].get('references') or {}), 'article_ids': NINE}}
    state['current_turn_article_ids'] = NINE
    state['v5_finalization_mode'] = False  # Reset by the graph at every decision.
    inputs = []
    for i, op in enumerate(actions(set(range(204, 212)))):  # The real batch reading the eight cards.
        inputs += [{'type': 'function_call', 'call_id': f'c{i}', 'name': op['request']['tool'], 'arguments': json.dumps(op['request']['arguments'])},
            {'type': 'function_call_output', 'call_id': f'c{i}', 'output': json.dumps(op['response']['output'], ensure_ascii=False)}]
    workflow.acknowledge_inputs(state, inputs)
    withheld = actions({216})[0]
    workflow.remember_deferred(state, SimpleNamespace(tool='analyze_article', arguments=withheld['request']['arguments']),
        withheld['response']['output'])
    view = workflow.working_memory(state)
    rows = {r['article_id']: r for r in view['articles']}
    assert len(rows) == 8 and all(rows[aid]['consulted'] for aid in NINE[:8])  # Ninth: 403, no card.
    assert len(json.dumps(view, ensure_ascii=False)) <= workflow.WORKING_MEMORY_CHARS
    assert sum(r['consulted_total'] for r in rows.values()) >= 75
    deferred = {e for r in rows.values() for e in r.get('deferred_evidence_ids', [])}
    assert deferred and deferred <= {e for ids in withheld['response']['output']['reading'] for e in ids.get('withheld_evidence_ids', [])}
    # The next request shows it, takes its room from the memory budget, and does not duplicate claims.
    ad = adapter()
    _, prepared = ad.prepare(state)
    assert any(isinstance(i.get('content'), str) and i['content'].startswith(workflow.WORKING_PREFIX) for i in prepared)
    assert len(json.dumps(prepared, ensure_ascii=False)) + workflow.CONTROL_RESERVE <= ad.config.max_context_chars
    memory = next(json.loads(i['content'][len(MEMORY_PREFIX):]) for i in prepared if isinstance(i.get('content'), str)
        and i['content'].startswith(MEMORY_PREFIX))
    refs = [c for c in [*memory['selected_articles'], *memory['consulted_articles']] if c.get('saved_card_ref')]
    assert refs and all(c['saved_card_ref']['consulted_claims'] == 'mémoire de travail de cette demande' for c in refs)


def test_real_f147_decisions_now_stop_rereading_instead_of_spending_the_budget(app_env):
    """The real nine-article request and Sol's real decisions, replayed through the graph."""
    make, factory, _ = app_env
    decisions = [d for d in real_decisions() if d['tool'] not in {'pause', 'finalize_response'}]
    final = next(d for d in real_decisions() if d['tool'] == 'finalize_response')
    queue = [as_response(d, f'd{i}') for i, d in enumerate(decisions)]
    seen = []
    def respond(sent):
        seen.append(sent)
        if sent['tool_choice'] != 'auto':
            return call('finalize_response', final['arguments'], 'final')
        return queue.pop(0)
    svc, sid, tr = make([respond]*12, real()['before'])
    result = run_sync(svc, sid, real()['run']['request'])
    # Real run: 9 calls, stop on the synthesis reserve. Now: stagnation after three
    # decisions that only re-sent known claims, then one forced synthesis.
    assert result['reason'] == 'stagnation', result
    assert len(tr.requests) == result['usage']['llm_calls'] <= 7
    with factory() as db:
        ops = list(db.scalars(select(Operation).where(Operation.kind == 'action').order_by(Operation.revision)))
    claims = [o for o in ops if o.request['tool'] == 'get_analysis_claims']
    # A re-request is credited only if it carries a claim never sent before in
    # this request (e.g. beyond the first page of a card); pure re-reads are not.
    sent_before, credited, rereads = set(), 0, 0
    for o in claims:
        out = o.response['output']
        got = {(out.get('article_id'), c.get('field'), c.get('statement')) for c in out.get('claims', [])}
        if o.response.get('reuse_reason') is None and not o.response['no_gain'] and o.request['arguments'].get('fields'):
            assert got - sent_before, o.request['arguments']
            credited += 1
        if got and got <= sent_before:
            rereads += 1
            assert o.response['no_gain']
        sent_before |= got
    assert rereads >= 5 and credited <= 2
    # Consulted claims stayed visible in later requests.
    later = [working(s) for s in seen[2:] if s['tool_choice'] == 'auto']
    assert later and all(w and len(w['articles']) >= 8 for w in later)
    assert sum(r['consulted_total'] for r in later[-1]['articles']) >= 75
    data = scientific_state(factory, sid)
    assert all(data['articles'][aid].get('analysis') == real()['before']['articles'][aid].get('analysis') for aid in NINE)
    assert result['budget_limits'] == real()['run']['budget_limits']


def test_after_reading_cards_sol_can_index_read_and_enrich_a_card(app_env):
    """From the real first decision, a scripted continuation to a gap of 7e1b35."""
    make, factory, _ = app_env
    state = real()['before']
    aid = 'paper_96ff11926e02db7e1b35'
    first = next(d for d in real_decisions() if d['tool'] == 'get_analysis_claims')
    old = copy.deepcopy(state['articles'][aid]['analysis'])
    rows = passages(Article.model_validate(state['articles'][aid]))
    shown = set(state['shown_evidence_ids'])
    target = next(r for r in rows if r['evidence_id'] not in shown and re.search(r'\d', r['text'])
        and 'Methods' in str(r.get('section')))
    picked = {}
    def to_index(sent):
        memory = working(sent)
        assert memory and any(r['article_id'] == aid and r['consulted'] for r in memory['articles'])
        return call('analyze_article', {**read(aid).arguments, 'action': 'index', 'section_filter': target['section'],
            'index_limit': 40}, 'index')
    def to_read(sent):
        index = next(json.loads(i['output']) for i in sent['input'] if i.get('call_id') == 'index' and i.get('type') == 'function_call_output')
        picked['eid'] = next(p['evidence_id'] for p in index['indexes'][0]['passages'] if p['evidence_id'] == target['evidence_id'])
        return call('analyze_article', {**read(aid).arguments, 'evidence_ids': [picked['eid']]}, 'read')
    def to_save(sent):
        card = payload(picked['eid'])
        card['method'] = [{'statement': 'Protocole expérimental décrit dans la section Méthodes du texte intégral.',
            'evidence_ids': [picked['eid']], 'origin': 'source_statement'}]
        return call('analyze_article', {**read(aid).arguments, 'action': 'save', 'analysis': card}, 'save')
    svc, sid, tr = make([as_response(first, 'cards'), to_index, to_read, to_save, call('pause', {'reason': 'consultant_review'}, 'p')], state)
    result = run_sync(svc, sid, real()['run']['request'])
    assert result['reason'] in {'consultant_review', 'model_requested_pause'} and len(tr.requests) == 5, result
    data = scientific_state(factory, sid)
    new = data['articles'][aid]['analysis']
    assert picked['eid'] in {e['evidence_id'] for e in new['evidence']}
    # Enriched without losing the previous claims and proofs.
    assert {e['evidence_id'] for e in old['evidence']} <= {e['evidence_id'] for e in new['evidence']}
    assert all(c in new.get(f, []) for f, cs in old.items() if isinstance(cs, list) for c in cs if isinstance(c, dict) and 'statement' in c)
    with factory() as db:
        ops = {o.request['tool']+':'+str(o.request['arguments'].get('action')): o.response['no_gain']
            for o in db.scalars(select(Operation).where(Operation.kind == 'action'))}
    assert ops['analyze_article:index'] is False and ops['analyze_article:read'] is False and ops['analyze_article:save'] is False
