"""Cross-tool continuity: real V5 graph/SDK/service, mock HTTP and isolated SQL.

No scientific attribution, extraction-identity or comparability fix is claimed.
The figure fixture is generic: no real-run identifier or special case in code.
"""
import copy
import json
from unittest.mock import Mock

import pytest
import httpx
from sqlalchemy import select

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.finance import BudgetRefused
from agents.EnnoScholarV5 import consulted, workflow
from agents.EnnoScholarV5.evidence import passages, exact_source_valid
from agents.EnnoScholarV5.models import Article
from agents.EnnoScholarV5.persistence import Budget, RunMeter, Operation
from agents.EnnoScholarV5.runtime import ScientificAdapter
from agents.EnnoScholarV5.toolkit import definitions, base
from agents.EnnoScholarV5.tools import extraction
from experiments.ennoscholar_v5.adapter import Decision
from experiments.ennoscholar_v5.progress import Scope, project
from .test_html_currency import AID, DOI, URL, article, stored, session_with
from .test_live_integration import app_env, call, run_sync, scientific_state


def sources():
    src = Mock()
    src.verify.side_effect = lambda a, *args, **kwargs: a
    return src


def prepared():
    return session_with(article(stored(extraction, marker='v5_html_floats_1')), old_proof=False)[0]


def rows(state):
    return passages(Article.model_validate(state['articles'][AID]))


def selected(state):
    all_rows = rows(state)
    return [next(r for r in all_rows if 'fig-a.webp' in r['exact_quote']),
        next(r for r in all_rows if r.get('table') and '0.02124' in r['exact_quote'])]


def reading(eids):
    return {'article_ids': [AID], 'action': 'read', 'research_context': 'Légendes et cellules exactes.',
        'passage_query': '', 'passage_offset': 0, 'passages_per_article': 8,
        'evidence_ids': eids, 'analysis': None}


def goal(search=False):
    return {'requested_article_count': None, 'deliverable': 'answer', 'lock_ids': [], 'focus_lock_ids': [],
        'exact_quote': '', 'reading_depth': 'none', 'understanding': 'Consultation documentaire ciblée.',
        'comparison_requested': False, 'search_needed': search, 'article_ids': [AID], 'article_scope': 'exclusive',
        'references_ambiguous': False, 'constraints': [] if search else ['no_search', 'no_download', 'no_reanalysis'],
        'research_need': None}


def final(eids, answer):
    return call('finalize_response', {'answer': answer, 'article_ids': [AID], 'evidence_ids': eids,
        'coverage_sufficient': False, 'remaining_uncertainties': ['Image non interprétée visuellement.'],
        'analyses': None, 'displayed_article_ids': None}, 'final')


def receive(state, chosen=None):
    adapter = ScientificAdapter(ScholarV4Config(api_key='offline', web_search_enabled=False), Mock(), sources=sources())
    t = adapter.toolkit(state)
    args = reading(chosen or [r['evidence_id'] for r in selected(state)])
    result = t.execute('analyze_article', args, __import__('time').monotonic()+30)
    adapter.delivered(t, [call('analyze_article', args, 'read'),
        {'type': 'function_call_output', 'call_id': 'read', 'output': json.dumps(result)}])
    return adapter, result


@pytest.mark.parametrize('finalizing', [False, True])
def test_figure_and_table_survive_other_tools_and_reach_final_response(app_env, finalizing):
    make, factory, _ = app_env
    state = prepared()
    chosen = selected(state)
    eids = [r['evidence_id'] for r in chosen]
    def finish(sent):
        # Neither read output is still in the latest protocol exchange.
        assert not any(i.get('call_id') == 'read' for i in sent['input'])
        packet = next(json.loads(i['content']) for i in sent['input'] if i.get('role') == 'developer'
            and isinstance(i.get('content'), str) and i['content'].startswith('{')
            and json.loads(i['content']).get('kind') == 'current_request_consulted_material')
        assert {r['evidence_id'] for r in packet['evidence']} >= set(eids)
        assert 'fig-a.webp' in json.dumps(packet) and '0.02124' in json.dumps(packet)
        return final(eids, f'[{AID}]\n'+'\n'.join(r['exact_quote']+f" [{r['evidence_id']}]" for r in chosen))
    choices = [call('set_turn_goal', goal(), 'goal'), call('analyze_article', reading(eids), 'read'),
        call('get_article', {'identifiers': [AID]}, 'notice'), call('read_conversation', {'offset': 0, 'limit': 1}, 'conversation'), finish]
    svc, sid, tr = make(choices, state, sources=sources())
    if finalizing:
        # Reserve boundary at the fifth decision, before HTTP. Same real forced
        # finalization route as A; no budget or scientific decision is increased.
        original = RunMeter.admit
        def refuse_once(meter, *args, **kwargs):
            if len(tr.requests) == 4 and getattr(meter, 'protect_finalization', False):
                raise BudgetRefused({'refused_budget': 'v5_synthesis', 'axis': 'tokens', 'classification': 'synthesis_reserve'})
            return original(meter, *args, **kwargs)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(RunMeter, 'admit', refuse_once)
            result = run_sync(svc, sid, 'Donne les légendes et les cellules déjà disponibles de cet article.')
    else:
        result = run_sync(svc, sid, 'Donne les légendes et les cellules déjà disponibles de cet article.')
    assert 'fig-a.webp' in result['answer'] and '0.02124' in result['answer'], result
    assert len(tr.requests) == 5
    saved = scientific_state(factory, sid)
    assert all(exact_source_valid(saved['evidence'][e], saved['source_snapshots']) for e in eids)
    assert not saved['articles'][AID].get('analysis')
    assert not any(s.get('url', '').endswith('fig-a.webp') for s in saved.get('web_sources', []))
    with factory() as db:
        read_ops = [o for o in db.scalars(select(Operation).where(Operation.scope == Scope(1, sid).key))
            if o.kind == 'action' and o.request['tool'] == 'analyze_article']
        assert len(read_ops) == 1


@pytest.mark.parametrize('change', ['new_request', 'unshown', 'invalid_quote', 'missing_snapshot'])
def test_consulted_material_never_uses_unreceived_or_invalid_history(change):
    state = prepared(); receive(state)
    assert consulted.packet(state, 12000)['evidence']
    if change == 'new_request':
        state['messages'].append({'id': 'another', 'role': 'user', 'content': 'Nouveau sujet.'})
    elif change == 'unshown':
        state['shown_evidence_ids'] = []
    elif change == 'invalid_quote':
        for e in state['evidence'].values(): e['exact_quote'] = 'Invented quote.'
    else:
        state['source_snapshots'] = {}
    assert not consulted.packet(state, 12000)
    assert not consulted.partial_response(state)


def test_consulted_packet_is_bounded_whole_and_explicit_about_omissions():
    state = prepared(); receive(state)
    before = copy.deepcopy(state)
    packet = consulted.packet(state, 600)
    assert packet and len(json.dumps(packet, ensure_ascii=False)) <= 600
    assert packet['omitted_total'] and not packet['evidence']
    assert packet['omitted_evidence_ids']
    assert state == before  # no source rewrites, new receipts or history mutations


@pytest.mark.parametrize('variant', ['order', 'offset', 'filter', 'subset'])
def test_index_progress_is_per_locator_not_order_filter_or_window(variant):
    state = prepared()
    d = Decision('analyze_article', {'action': 'index', 'article_ids': [AID]})
    entries = [{'evidence_id': 'one'}, {'evidence_id': 'two'}]
    def output(items, **extra):
        return {'indexes': [{'article_id': AID, 'source_fingerprint': 'version-a', 'offset': 0,
            'passages': items, **extra}]}
    assert workflow.novel_delivery(state, d, output(entries))
    altered = output(list(reversed(entries))) if variant == 'order' else (
        output(entries, offset=20) if variant == 'offset' else output(entries, section_filter='Methods')
        if variant == 'filter' else output(entries[:1]))
    assert not workflow.novel_delivery(state, d, altered)
    assert workflow.novel_delivery(state, d, output([{'evidence_id': 'new'}]))
    changed = output(entries); changed['indexes'][0]['source_fingerprint'] = 'version-b'
    assert workflow.novel_delivery(state, d, changed)  # a genuine version stays open


def test_archived_html_is_refreshed_before_graph_reuse(app_env, monkeypatch):
    from agents.EnnoScholarV5.tools import fulltext
    make, factory, _ = app_env
    state, proof = session_with(article(stored()))
    before = copy.deepcopy(state['source_snapshots'])
    def forbidden(*args, **kwargs): raise AssertionError('Unexpected download')
    monkeypatch.setattr(fulltext.base, 'fetch_public', forbidden)
    def finish(sent):
        current = scientific_state(factory, sid)
        assert current['articles'][AID]['fulltext']['html_extractor'] == 'v5_html_floats_1'
        assert any(c.get('figure') for c in current['articles'][AID]['fulltext']['chunks'])
        return final([], 'Document déjà disponible ; lecture non achevée.')
    svc, sid, tr = make([call('get_fulltext', {'article_id': AID, 'candidate_urls': None,
        'page_start': None, 'page_limit': None}, 'archive'), finish], state, sources=sources())
    run_sync(svc, sid, 'Consulte le document déjà téléchargé.')
    current = scientific_state(factory, sid)
    assert all(current['source_snapshots'][k] == v for k, v in before.items())
    assert exact_source_valid(current['evidence'][proof], current['source_snapshots'])
    assert len(current['superseded_html_extractions'][AID]) == 1


@pytest.mark.parametrize('axis', ['tokens', 'usd', 'seconds', 'tools'])
def test_resource_stop_restitutes_current_verified_quotes_without_extra_http(app_env, monkeypatch, axis):
    make, factory, _ = app_env; state = prepared(); eids = [r['evidence_id'] for r in selected(state)]
    svc, sid, tr = make([call('analyze_article', reading(eids), 'read'),
        call('read_conversation', {'offset': 0, 'limit': 1}, 'ack')], state, sources=sources())
    admit = RunMeter.admit
    def stop(meter, *args, **kwargs):
        if len(tr.requests) >= 2:
            raise BudgetRefused({'refused_budget': 'v5_global', 'axis': axis, 'classification': 'job_budget'})
        return admit(meter, *args, **kwargs)
    monkeypatch.setattr(RunMeter, 'admit', stop)
    result = run_sync(svc, sid, 'Restitue les passages et leurs légendes.')
    assert len(tr.requests) == 2 and result['status'] == 'partial'
    assert 'fig-a.webp' in result['answer'] and '0.02124' in result['answer']
    assert 'sans nouvelle analyse' in result['answer']
    assert set(result['cited_evidence_ids']) >= set(eids)


@pytest.mark.parametrize('counts', [[1, 1, 1, 1], [2, 1, 2, 0]])
def test_web_exhaustion_preserves_local_reading_and_finalization(app_env, counts):
    make, factory, _ = app_env; state = prepared(); chosen = selected(state); eids = [r['evidence_id'] for r in chosen]
    def native(count, prefix):
        return [{'type': 'web_search_call', 'id': prefix+str(i), 'status': 'completed',
            'action': {'type': 'search', 'query': 'fixture', 'sources': []}} for i in range(count)]
    def finish(sent):
        assert not any(t.get('type') == 'web_search' for t in sent['tools'])
        return final(eids, f'[{AID}]\n'+'\n'.join(r['exact_quote']+f" [{r['evidence_id']}]" for r in chosen))
    choices = [[*native(counts[0], 'a'), call('set_turn_goal', goal(True), 'goal')],
        [*native(counts[1], 'b'), call('analyze_article', reading(eids), 'read')],
        [*native(counts[2], 'c'), call('get_fulltext', {'article_id': AID, 'candidate_urls': None,
            'page_start': None, 'page_limit': None}, 'reuse')]]
    if counts[3]:
        choices.append([*native(counts[3], 'd'), call('read_conversation', {'offset': 0, 'limit': 1}, 'ack')])
    choices.append(finish)
    svc, sid, tr = make(choices, state, sources=sources(), config=ScholarV4Config(api_key='offline', web_search_enabled=True))
    result = run_sync(svc, sid, 'Recherche puis restitue le tableau et la figure disponibles.')
    assert 'fig-a.webp' in result['answer'] and '0.02124' in result['answer'], result
    with factory() as db:
        budget = db.get(Budget, result['id']).data
        assert budget['limits']['web'] == 4 and budget['web'] == sum(counts)
        assert all(e['status'] == 'settled' for e in budget['entries'])
    if sum(counts) > 4:
        # Adversarial provider: record the real overrun, never hide/clamp it.
        assert any(e.get('status') == 'native_web_received' and e.get('exceeded') for e in result['activity'])
    assert len(tr.requests) == len(choices)


def test_schema_compaction_preserves_every_tool_field_and_scientific_description():
    original, compact = base.definitions(True), definitions(True)
    assert [t['name'] for t in compact] == [t['name'] for t in original]
    for before, after in zip(original, compact):
        assert before['description'] == after['description']
        assert before['strict'] == after['strict'] is True
        a, b = before['parameters'], after['parameters']
        assert set(a['properties']) == set(b['properties'])
        assert a['required'] == b['required']
        assert a['additionalProperties'] == b['additionalProperties'] is False
        for name in a['properties']:
            assert a['properties'][name].get('description') == b['properties'][name].get('description')
    assert len(json.dumps(compact)) < len(json.dumps(original))
    assert 'title' in next(t for t in compact if t['name'] == 'manage_outline')['parameters']['properties']


def test_documentary_goal_has_no_forced_analysis_task():
    state = prepared(); receive(state)
    state['turn_goal'] = {**goal(), 'references': {'article_ids': [AID], 'scope': 'exclusive'}}
    result = project(state, scope=Scope(1, 'fixture'))
    assert not any(t['kind'] == 'save_analysis' for t in result['tasks'].values())
    assert not state['articles'][AID].get('analysis')


def test_new_article_search_to_graphic_publication_has_one_acquisition_and_read(app_env, monkeypatch):
    from .test_html_currency import HTML
    from agents.EnnoScholarV5.tools import fulltext
    make, factory, _ = app_env; src = sources()
    src.search.return_value = ([article()], [])
    src.last_provider_stats = {'openalex': {'returned': 1}}
    fetched = []
    monkeypatch.setattr(fulltext.base, 'fetch_public', lambda url, *a, **k:
        fetched.append(url) or (HTML, 'text/html', URL))
    seen = {}
    def acquire(sent):
        assert AID in scientific_state(factory, sid)['articles']
        return call('get_fulltext', {'article_id': AID, 'candidate_urls': [URL],
            'page_start': None, 'page_limit': None}, 'ft')
    def read(sent):
        seen['rows'] = selected(scientific_state(factory, sid))
        return call('analyze_article', reading([r['evidence_id'] for r in seen['rows']]), 'read')
    def finish(sent):
        chosen = seen['rows']
        return final([r['evidence_id'] for r in chosen], f'[{AID}]\n'+
            '\n'.join(r['exact_quote']+f" [{r['evidence_id']}]" for r in chosen))
    query = {'query': 'documented battery parameters', 'sources': ['openalex', 'semantic_scholar', 'arxiv'],
        'limit_per_source': 1, 'offset': 0, 'family': 'battery'}
    svc, sid, tr = make([call('search_scientific', query, 'search'), acquire, read, finish], sources=src)
    result = run_sync(svc, sid, 'Trouve un article et donne les cellules du tableau ainsi que la légende et la référence graphique.')
    assert 'fig-a.webp' in result['answer'] and '0.02124' in result['answer'], result
    assert len(tr.requests) == 4 and src.search.call_count == 1 and fetched == [URL]
    assert scientific_state(factory, sid)['articles'][AID]['fulltext']['html_extractor'] == 'v5_html_floats_1'


def test_crash_after_read_commit_restores_memory_without_repeating_read_or_budget(app_env):
    from agents.EnnoScholarV5.persistence import ApplicationRepository
    from agents.EnnoScholarV5.runtime import ResponsesSol
    from experiments.ennoscholar_v5.graph import Runner
    make, factory, checkpoints = app_env; state = prepared(); chosen = selected(state)
    eids = [r['evidence_id'] for r in chosen]
    def finish(sent):
        assert 'fig-a.webp' in json.dumps(sent['input'])
        return final(eids, f'[{AID}]\n'+chosen[0]['exact_quote']+f' [{eids[0]}]')
    svc, sid, tr = make([call('set_turn_goal', goal(), 'goal'), call('analyze_article', reading(eids), 'read'),
        call('read_conversation', {'offset': 0, 'limit': 1}, 'ack'), finish], state, sources=sources())
    rid = svc.open_run(1, sid, 'Consulte et conserve les passages exacts puis restitue leurs légendes.')
    scope = Scope(1, sid); repo = ApplicationRepository(svc, scope)
    meter = RunMeter(svc, scope, rid, svc.config)
    adapter = ScientificAdapter(svc.config, meter, sources=svc.sources)
    sol = ResponsesSol(svc.config, meter, client=tr.client)
    crashed = []
    def crash(opid):
        op = repo.operation(scope, opid)
        if op['kind'] == 'action' and op['request']['tool'] == 'analyze_article' and not crashed:
            crashed.append(opid)
            raise RuntimeError('simulated loss after durable read')
    with checkpoints(None) as saver:
        with pytest.raises(RuntimeError, match='durable read'):
            Runner(scope, repo, adapter, sol, saver, after_commit=crash).start()
    before = meter.snapshot()
    restored_meter = RunMeter(svc, scope, rid, svc.config)
    assert restored_meter.limits == before['limits']
    assert restored_meter.snapshot()['input_tokens'] == before['input_tokens']
    restored = ScientificAdapter(svc.config, restored_meter, sources=svc.sources)
    with checkpoints(None) as saver:
        result = Runner(scope, repo, restored,
            ResponsesSol(svc.config, restored_meter, client=tr.client), saver).resume()
    assert len(tr.requests) == 4 and result['usage']['llm_calls'] == 4
    assert repo.operation(scope, crashed[0])['status'] == 'complete'
    _, current = repo.load(scope)
    assert len([w for w in current['reading_windows'] if w.get('evidence_ids') == eids]) == 1
    assert set(consulted.evidence_ids(current)) >= set(eids)


@pytest.mark.parametrize('change', ['unshown', 'invalid', 'unrelated', 'invented_url'])
def test_graphic_allowlist_never_accepts_unverified_or_unrelated_urls(change):
    state = prepared(); receive(state)
    t = ScientificAdapter(ScholarV4Config(api_key='offline'), Mock(), sources=sources()).toolkit(state)
    ids = consulted.evidence_ids(state)
    assert 'https://cdn.fixture.invalid/blobs/fig-a.webp' in consulted.graphic_urls(t, ids)
    if change == 'unshown': t.shown_evidence = []
    elif change == 'invalid':
        for eid in ids: t.evidence[eid]['exact_quote'] = 'invented'
    elif change == 'unrelated': ids = [eid for eid in ids if t.evidence[eid].get('table')]
    else:
        assert 'https://invented.invalid/chart.webp' not in consulted.graphic_urls(t, ids)
        return
    assert not consulted.graphic_urls(t, ids)


def test_claim_and_comparison_proofs_are_retained_by_the_same_received_material_view():
    from agents.EnnoScholarV5.original import evidence_projection
    state = prepared(); receive(state)
    eids = consulted.evidence_ids(state)
    # Simulate a new request that receives these sources via a comparison packet,
    # not a fresh read; storage and all validity checks are unchanged.
    state['messages'].append({'id': 'compare', 'role': 'user', 'content': 'Compare les données disponibles.'})
    packet = {'packet': {'papers': [{'proofs': [evidence_projection(state['evidence'][e]) for e in eids]}]}}
    workflow.acknowledge_inputs(state, [call('compare_articles', {'article_ids': [AID]}, 'cmp'),
        {'type': 'function_call_output', 'call_id': 'cmp', 'output': json.dumps(packet)}])
    assert set(consulted.evidence_ids(state)) == set(eids)


def test_empty_provider_response_is_an_error_not_success_or_an_automatic_loop(app_env):
    make, factory, _ = app_env
    svc, sid, tr = make([[]])
    result = run_sync(svc, sid, 'Recherche les données utiles.')
    assert len(tr.requests) == 1 and result['status'] == 'partial'
    assert result['outcome'] == 'provider_error' and 'ProviderEmptyResponse' in result['answer']
    assert not result.get('coverage_sufficient')
    with factory() as db:
        ledger = db.get(Budget, result['id']).data['entries']
        assert len(ledger) == 1 and ledger[0]['status'] == 'settled'


def test_provider_error_keeps_current_received_quotes_without_old_answer(app_env):
    make, factory, _ = app_env; state = prepared(); chosen = selected(state)
    state['last_response'] = {'answer': 'OBSOLETE ANSWER MUST NOT RETURN', 'coverage_sufficient': True}
    error = httpx.Response(401, json={'error': {'message': 'offline refusal', 'type': 'invalid_api_key'}})
    svc, sid, tr = make([call('analyze_article', reading([r['evidence_id'] for r in chosen]), 'read'),
        call('read_conversation', {'offset': 0, 'limit': 1}, 'ack'), error], state, sources=sources())
    result = run_sync(svc, sid, 'Consulte les passages et restitue les légendes.')
    assert len(tr.requests) == 3 and 'HTTP 401' in result['answer']
    assert 'fig-a.webp' in result['answer'] and '0.02124' in result['answer']
    assert 'OBSOLETE ANSWER' not in result['answer']


def test_schema_volume_measurement_is_offline_and_not_supplier_token_savings(tmp_path):
    from agents.EnnoScholarV5.token_estimation import estimate
    original, compact = base.definitions(True), definitions(True)
    before = {'instructions': ScientificAdapter.instructions, 'tools': original, 'input': [], 'max_output_tokens': 7000}
    after = {**before, 'tools': compact}
    a, b = estimate(before), estimate(after)
    measured = {'before_tools_bytes': a['block_bytes']['tools'], 'after_tools_bytes': b['block_bytes']['tools'],
        'before_estimated_input_tokens': a['point_input_tokens'], 'after_estimated_input_tokens': b['point_input_tokens'],
        'supplier_usage_measured': False, 'descriptions_and_fields_preserved': True}
    (tmp_path/'schema-volume.json').write_text(json.dumps(measured, indent=2), encoding='utf8')
    assert measured['after_tools_bytes'] < measured['before_tools_bytes']


def test_graphic_publication_rejects_invented_url_and_reaches_chat_api(app_env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from routers import scholar_v5
    from core.deps import get_current_user
    from .test_live_integration import NoSocketLoop
    make, factory, _ = app_env; state = prepared(); chosen = selected(state)
    eids = [r['evidence_id'] for r in chosen]
    def repair(sent):
        out = next(json.loads(i['output']) for i in sent['input']
            if i.get('type') == 'function_call_output' and i.get('call_id') == 'final')
        assert out.get('output', out)['rejection_code'] == 'unknown_url'
        return final(eids, f'[{AID}]\n'+chosen[0]['exact_quote']+f' [{eids[0]}]')
    svc, sid, tr = make([call('analyze_article', reading(eids), 'read'),
        final(eids, f'[{AID}] https://invented.invalid/graph.webp [{eids[0]}]'), repair], state, sources=sources())
    result = run_sync(svc, sid, 'Donne la légende et sa référence graphique exacte.')
    assert 'fig-a.webp' in result['answer'] and 'invented.invalid' not in result['answer']
    assert len(tr.requests) == 3
    monkeypatch.setenv('ENNOSCHOLAR_V5_TEST_ENABLED', '1')
    monkeypatch.setattr(scholar_v5, 'get_service', lambda: svc)
    app = FastAPI(); app.include_router(scholar_v5.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    client = TestClient(app, backend_options={'loop_factory': NoSocketLoop})
    delivered = client.get('/scholar-v5/runs/'+result['id']).json()
    session = client.get('/scholar-v5/sessions/'+sid).json()
    messages = [m for m in session['messages'] if m.get('run_id') == result['id'] and m['role'] == 'assistant']
    assert delivered['answer'] == messages[-1]['content'] == result['answer']


@pytest.mark.parametrize('provenance', ['new', 'reused', 'legacy'])
def test_candidate_scope_preserves_discoveries_and_explicit_reuse(provenance):
    state = prepared(); state['articles'] = {AID: state['articles'][AID]}
    mid = workflow.current_request(state)['id']
    state['v5_new_search'] = True
    state['current_turn_article_ids'] = state['v5_explicit_context_ids'] = []
    state['conversation_references'] = {'article_ids': []}
    state['turn_goal'] = {'deliverable': 'literature_search', 'references': {'article_ids': []}}
    state['v5_request_start'] = {'request_id': mid, 'article_ids': [AID]}
    fresh = article().model_copy(update={'id': 'paper_new', 'canonical_work_id': 'paper_new',
        'doi': '10.9999/new', 'title': 'New candidate'})
    state['articles']['paper_new'] = fresh.durable()
    if provenance == 'reused':
        # A cached query returned an old candidate for the new request. The
        # same hook runs for receipt replays, without injecting its old analysis.
        adapter = ScientificAdapter(ScholarV4Config(api_key='offline'), Mock(), sources=sources())
        adapter.finish_exchange(state, Decision('search_collected_articles', {}, call_id='cached'),
            {'articles': [{'id': AID}]})
    elif provenance == 'legacy':
        state['v5_request_start'].pop('article_ids')
    tasks = project(state, scope=Scope(1, 'fixture'))['tasks'].values()
    candidates = {t['article_id'] for t in tasks if t['kind'] == 'evaluate_candidate'}
    assert 'paper_new' in candidates
    assert (AID in candidates) == (provenance != 'new')


@pytest.mark.parametrize('failure', ['401', 'empty'])
def test_explicit_resume_clears_previous_provider_failure_without_refilling_budget(app_env, failure):
    from .test_live_integration import finish
    make, factory, _ = app_env
    first = [] if failure == 'empty' else httpx.Response(401,
        json={'error': {'message': 'offline authentication refusal', 'type': 'invalid_api_key'}})
    svc, sid, tr = make([first, finish('Réponse de la nouvelle tentative ; aucun résultat scientifique inventé.')])
    result = run_sync(svc, sid, 'Recherche les publications utiles.')
    assert len(tr.requests) == 1 and result['partial']
    with factory() as db: before = copy.deepcopy(db.get(Budget, result['id']).data)
    resumed = svc.resume_run(1, result['id'])
    svc.pool.shutdown(wait=True)
    current = svc.run(1, resumed['id'])
    assert len(tr.requests) == 2 and current['id'] == result['id']
    assert 'HTTP 401' not in current['answer'] and 'ProviderEmptyResponse' not in current['answer']
    assert 'nouvelle tentative' in current['answer']
    with factory() as db:
        after = db.get(Budget, result['id']).data
        assert before['limits'] == after['limits']
        assert len(after['entries']) == len(before['entries'])+1
        assert after['entries'][0] == before['entries'][0]


def test_search_index_read_and_answer_choose_only_from_actual_responses_inputs(app_env, monkeypatch):
    """The scripted scientist has no private SQL access for its tool decisions."""
    from .test_html_currency import HTML
    from agents.EnnoScholarV5.tools import fulltext
    make, factory, _ = app_env; src = sources(); fetched = []
    src.search.return_value = ([article()], [])
    src.last_provider_stats = {'openalex': {'returned': 1}}
    monkeypatch.setattr(fulltext.base, 'fetch_public', lambda url, *a, **k:
        fetched.append(url) or (HTML, 'text/html', URL))
    def output(sent, cid):
        value = next(json.loads(i['output']) for i in sent['input']
            if i.get('type') == 'function_call_output' and i.get('call_id') == cid)
        return value.get('output', value)
    seen = {}
    def acquire(sent):
        seen['aid'] = output(sent, 'search')['articles'][0]['id']
        return call('get_fulltext', {'article_id': seen['aid'], 'candidate_urls': None,
            'page_start': None, 'page_limit': None}, 'ft')
    def index(sent):
        assert output(sent, 'ft')['access_status'] == 'fulltext_extracted'
        return call('analyze_article', {**reading(None), 'article_ids': [seen['aid']],
            'action': 'index', 'index_offset': 0, 'index_limit': 80}, 'index')
    def read(sent):
        locators = output(sent, 'index')['indexes'][0]['passages']
        figure = next(r for r in locators if 'Figure 1' in (r.get('section') or ''))
        cell = next(r for r in locators if '0.02124' in r['preview'])
        seen['eids'] = [figure['evidence_id'], cell['evidence_id']]
        return call('analyze_article', {**reading(seen['eids']), 'article_ids': [seen['aid']]}, 'read')
    def publish(sent):
        proofs = output(sent, 'read')['reading'][0]['passages']
        assert {r['evidence_id'] for r in proofs} == set(seen['eids'])
        return final(seen['eids'], f"[{seen['aid']}]\n"+
            '\n'.join(r['exact_quote']+f" [{r['evidence_id']}]" for r in proofs))
    query = {'query': 'battery transport parameters', 'sources': ['openalex'],
        'limit_per_source': 1, 'offset': 0, 'family': 'battery'}
    svc, sid, tr = make([call('search_scientific', query, 'search'), acquire, index, read, publish], sources=src)
    result = run_sync(svc, sid, 'Trouve un article et restitue une cellule, la légende et la référence de sa figure.')
    assert len(tr.requests) == 5 and src.search.call_count == 1 and fetched == [URL]
    assert 'fig-a.webp' in result['answer'] and '0.02124' in result['answer'], result
