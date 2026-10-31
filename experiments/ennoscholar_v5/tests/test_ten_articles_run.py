"""Regressions from real V5 run 97df6cf6 (ten CO2-to-ethylene articles, 28/10).

Real run: 11 Responses calls, ~335 000 tokens, 30 notices (12 off-topic from
other conversations), Sol's six-work synthesis refused for four unverified
DOIs, then "0 publications proposées". All HTTP and registries are simulated:
these tests check execution, accounting and honesty, not Sol's judgement.
"""
import copy
import gzip
import json
import re
from unittest.mock import Mock
from sqlalchemy import select
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.service import ScholarV4Service
from agents.EnnoScholarV4.session import ScholarV4Session as V4Session, sync_records as v4_sync
from agents.EnnoScholarV5.models import Article
from agents.EnnoScholarV5.persistence import ApplicationRepository, RunMeter, Operation
from agents.EnnoScholarV5.runtime import ScientificAdapter, ResponsesSol
from agents.EnnoScholarV5.service import ScholarV5Service
from experiments.ennoscholar_v5.graph import Runner
from experiments.ennoscholar_v5.progress import Scope, project
from .test_graph import ARTIFACTS
from .test_live_integration import app_env, call, run_sync, scientific_state

OFF_TOPIC = re.compile(r"mesh|polycube|cellpose|omnipose|anomaly|delamination", re.I)


def real():
    with gzip.open(ARTIFACTS/'real-ten-articles-run.json.gz', 'rt', encoding='utf-8') as f:
        return json.load(f)


def weights():
    return json.load(open(ARTIFACTS/'real-memory-term-weights.json'))['weights']


def co2(title):
    """The run's subject vocabulary; the 12 off-topic works match none of it."""
    return bool(re.search(r"CO2|carbon dioxide|ethylene|copper", title, re.I)) and not OFF_TOPIC.search(title)


def seed_memory(svc, factory):
    """The run's 30 works as stored science of two earlier V4 conversations."""
    state = real()['state']
    v4 = ScholarV4Service(factory, svc.config)
    ids = []
    for keep in (lambda a: not co2(a['title']), lambda a: co2(a['title'])):
        sid = v4.create(1)['id']
        part = {aid: a for aid, a in state['articles'].items() if keep(a)}
        with factory() as db:
            row = db.get(V4Session, sid)
            row.data = {**row.data, 'articles': copy.deepcopy(part), 'evidence': copy.deepcopy(state['evidence']),
                'source_snapshots': copy.deepcopy(state['source_snapshots'])}
            v4_sync(db, row)
            db.commit()
        ids.append(sid)
    v4.pool.shutdown(wait=True)
    return ids


def titles(found):
    rows = found['articles'] if isinstance(found, dict) else found
    return [p['article']['title'] for p in rows]


def test_memory_drops_off_topic_works_and_keeps_related_co2_work(app_env, monkeypatch):
    make, factory, _ = app_env
    svc, sid, _ = make([])
    sources = seed_memory(svc, factory)
    with factory() as db:
        before = [copy.deepcopy(db.get(V4Session, s).data) for s in sources]
    # Term specificity measured read-only on the real memory (2 780 works).
    real_weights = weights()
    monkeypatch.setattr(ScholarV5Service, 'memory_term_weights', lambda self, owner, terms: {t: real_weights[t] for t in terms})
    from agents.EnnoScholarV5.memory import V4ReadOnlyMemory
    query = 'copper CO2 ethylene high current density stability selectivity'
    raw = V4ReadOnlyMemory(svc, sid).article_memory(1, sid, query, limit=8, include_index=True)
    assert any(OFF_TOPIC.search(t) for t in titles(raw))  # The run's pollution, reproduced.
    got = svc.article_memory(1, sid, query, limit=8, include_index=True)
    assert got['articles'] and not any(OFF_TOPIC.search(t) for t in titles(got))
    assert not any(OFF_TOPIC.search(r['title']) for r in got['candidate_index']) and got['filtered_out'] > 0
    assert all(co2(t) for t in titles(got))
    # Different terminology ("carbon dioxide", "multi-carbon") remains reachable.
    related = svc.article_memory(1, sid, 'carbon dioxide electroreduction multicarbon', limit=8, include_index=True)
    assert any('Carbon Dioxide' in t for t in [*titles(related), *(r['title'] for r in related['candidate_index'])])
    # An exact request is never filtered; stored conversations are unchanged.
    mesh = next(aid for aid, a in real()['state']['articles'].items() if 'CubeFlipNet' in a['title'])
    assert any('CubeFlipNet' in t for t in titles(svc.article_memory(1, sid, mesh, exact=True)))
    with factory() as db:
        assert [db.get(V4Session, s).data for s in sources] == before


def test_memory_weights_are_counted_from_stored_works(app_env):
    make, factory, _ = app_env
    svc, sid, _ = make([])
    seed_memory(svc, factory)
    w = svc.memory_term_weights(1, ['co2', 'mesh', 'high'])
    assert w['mesh'] > w['high'] and w['co2'] > 0  # 'high' is in most of the 30 works.


def found_articles():
    """The 12 notices OpenAlex returned in the run, without later local enrichment."""
    state = real()['state']
    search = next(o for o in real()['operations'] if o['kind'] == 'action' and o['request']['tool'] == 'search_scientific')
    ids = [a['id'] for a in search['response']['output']['articles']]+[r['id'] for r in search['response']['output']['batch_index']]
    return [Article(**{k: state['articles'][aid][k] for k in ('title', 'doi', 'authors', 'year', 'abstract', 'url')
        if state['articles'][aid].get(k) is not None}, sources=['openalex']) for aid in ids]


def registry_sources(confirmed):
    sources = Mock()
    sources.search.return_value = (found_articles(), [])
    sources.last_provider_stats = {'openalex': {'returned': 12}}
    checked = []
    def verify(article, deadline, expected_title=''):
        checked.append(article.doi)
        out = article.model_copy(deep=True)
        out.verified = article.doi in confirmed
        out.metadata_errors = [] if out.verified else ['registry_unconfirmed']
        return out
    sources.verify.side_effect = verify
    sources.metadata.side_effect = PermissionError('no network')
    return sources, checked


def answer(ids, state):
    rows = [f"- **{state['articles'][aid]['title']}** — DOI : {state['articles'][aid]['doi']} [{aid}]" for aid in ids]
    return "Présélection sur notices et résumés, sans lecture du texte intégral :\n"+"\n".join(rows)


REQUEST = None


def test_batched_calls_finalisation_verification_and_repair_replay(app_env):
    """One Sol response may carry several calls; refused publication is repaired."""
    make, factory, _ = app_env
    run = real()
    goal = next(o for o in run['operations'] if o['kind'] == 'decision')['response']['decision']['arguments']
    search = next(o for o in run['operations'] if o['kind'] == 'action' and o['request']['tool'] == 'search_scientific')['request']['arguments']
    picks = {}
    unconfirmed = 'Interfacially coupled Cu-cluster/GaN photocathode for efficient CO2 to ethylene conversion'

    def inspect(sent):
        # Sol saw the search window: it opens six notices and drafts too early.
        out = [json.loads(i['output']) for i in sent['input'] if i.get('type') == 'function_call_output']
        assert len(out) == 2 and any('articles' in o for o in out)  # Both calls answered.
        found = {a['title']: a['id'] for o in out for a in o.get('articles', [])}
        found.update({r['title']: r['id'] for o in out for r in o.get('batch_index', [])})
        picks['ids'] = [found[t] for t in found if 'ethylene' in t.lower()][:5]+[found[unconfirmed]]
        state = scientific_state(factory, sid)
        return [call('get_article', {'identifiers': picks['ids']}, 'open'),
            call('finalize_response', {'answer': answer(picks['ids'], state), 'article_ids': picks['ids'], 'evidence_ids': [],
                'coverage_sufficient': False, 'remaining_uncertainties': ['Textes non lus'], 'displayed_article_ids': picks['ids']}, 'early')]

    def publish(sent):
        outs = {i['call_id']: json.loads(i['output']) for i in sent['input'] if i.get('type') == 'function_call_output'}
        assert outs['early']['not_executed'] and outs['early']['reason'] == 'deferred_until_results_reviewed'
        state = scientific_state(factory, sid)
        return call('finalize_response', {'answer': answer(picks['ids'], state), 'article_ids': picks['ids'], 'evidence_ids': [],
            'coverage_sufficient': False, 'remaining_uncertainties': ['Textes non lus'], 'displayed_article_ids': picks['ids']}, 'first')

    def repair(sent):
        refusal = next(json.loads(i['output']) for i in sent['input'] if i.get('call_id') == 'first' and i.get('type') == 'function_call_output')
        assert refusal['rejection_code'] == 'invalid_bibliography'
        keep = refusal['verified_article_ids']
        state = scientific_state(factory, sid)
        picks['published'] = keep
        return call('finalize_response', {'answer': answer(keep, state)+"\n\nUne référence n'a pas pu être confirmée par les registres.",
            'article_ids': keep, 'evidence_ids': [], 'coverage_sufficient': False,
            'remaining_uncertainties': ['Référence non confirmée exclue'], 'displayed_article_ids': keep}, 'repaired')

    found = {a.title: a.doi for a in found_articles()}
    sources, checked = registry_sources({doi for t, doi in found.items() if t != unconfirmed})
    svc, sid, tr = make([[call('set_turn_goal', goal, 'goal'), call('search_scientific', search, 'search')],
        inspect, publish, repair], config=ScholarV4Config(api_key='offline', web_search_enabled=False), sources=sources)
    result = run_sync(svc, sid, run['run']['request'])
    assert result['reason'] is None and not result.get('fallback'), result
    # 4 Responses calls for 6 tool calls (the real run: 11 calls, no publication).
    assert len(tr.requests) == result['usage']['llm_calls'] == 4
    assert all(r['parallel_tool_calls'] for r in tr.requests)
    with factory() as db:
        actions = list(db.scalars(select(Operation).where(Operation.kind == 'action').order_by(Operation.revision)))
    assert [a.request['tool'] for a in actions if a.request['tool'] != 'initialise'] == [
        'set_turn_goal', 'search_scientific', 'get_article', 'finalize_response', 'finalize_response', 'finalize_response']
    assert all(a.status == 'complete' for a in actions)
    # The deferred call is never counted as executed nor as a finalisation attempt.
    assert result['usage']['tools'] == 5
    data = scientific_state(factory, sid)
    assert data['v5_tool_counters']['finalization_attempts'] == 2
    # Publication-time verification: each cited, unverified DOI checked once.
    assert sorted(checked) == sorted({found[t] for t in found if t in {a.title for a in found_articles()}} & set(checked))
    assert len(checked) == len(set(checked)) == len(picks['ids'])
    assert set(result['cited_article_ids']) == set(picks['published']) and len(picks['published']) == 5
    assert not data['articles'][picks['ids'][-1]]['verified']  # Unconfirmed stays distinguished.
    assert sources.search.call_count == 1


def test_crash_inside_a_batch_resumes_without_repeating_receipts(app_env):
    make, factory, checkpoints = app_env
    run = real()
    search = next(o for o in run['operations'] if o['kind'] == 'action' and o['request']['tool'] == 'search_scientific')['request']['arguments']
    sources, _ = registry_sources(set())
    svc, sid, tr = make([[call('search_scientific', search, 's'), call('search_collected_articles',
        {'query': 'copper ethylene', 'limit': 6, 'exclude_presented': False, 'family': None, 'offset': 0, 'approach_ids': []}, 'c'),
        call('get_article', {'identifiers': ['10.1038/s41467-024-52022-0']}, 'g')],
        call('pause', {'reason': 'consultant_review'}, 'p')], sources=sources)
    rid = svc.open_run(1, sid, run['run']['request'])
    scope = Scope(1, sid)
    repo = ApplicationRepository(svc, scope)
    meter = RunMeter(svc, scope, rid, svc.config)
    adapter = ScientificAdapter(svc.config, meter, sources=sources)
    sol = ResponsesSol(svc.config, meter, client=tr.client)
    crashed = []
    def crash(opid):
        op = repo.operation(scope, opid)
        if op['kind'] == 'action' and op['request']['tool'] == 'search_collected_articles' and not crashed:
            crashed.append(opid)
            raise RuntimeError('power loss inside the batch')
    with checkpoints(None) as saver:
        with __import__('pytest').raises(RuntimeError):
            Runner(scope, repo, adapter, sol, saver, after_commit=crash).start()
    with checkpoints(None) as saver:
        resumed = Runner(scope, repo, adapter, sol, saver).resume()
    assert len(tr.requests) == 2 and sources.search.call_count == 1
    with factory() as db:
        tools = [o.request['tool'] for o in db.scalars(select(Operation).where(Operation.kind == 'action').order_by(Operation.revision))]
    assert tools == ['search_scientific', 'search_collected_articles', 'get_article', 'pause']
    # Every function call of the batch has exactly one output in the next request.
    outs = [i['call_id'] for i in tr.requests[1]['input'] if i.get('type') == 'function_call_output']
    assert sorted(outs) == ['c', 'g', 's']
    assert resumed['usage']['llm_calls'] == 2


def test_refused_forced_synthesis_gets_one_repair_then_publishes(app_env, monkeypatch):
    """Before: a refusal during the forced synthesis stopped the run."""
    from .test_stokes_control import bad_calls, observed, original_request
    from .test_graph import ARTIFACTS as A, STOKES
    from agents.EnnoScholarV4.context import transmitted_evidence_ids
    make, factory, _ = app_env
    monkeypatch.setenv('ENNOSCHOLAR_V5_PDF_DIRECTORY', str(A/'pdf'))
    state = observed()['state']
    saved = {e['evidence_id'] for e in state['articles'][STOKES]['analysis']['evidence']}
    unread = next(e for e in state['evidence'] if e not in saved and state['evidence'][e]['article_id'] == STOKES)
    def final(eid_from_sent, tag):
        def respond(sent):
            eid = eid_from_sent(sent)
            return call('finalize_response', {'answer': f"[A Deep Learning Approach to Antibiotic Discovery](https://doi.org/10.1016/j.cell.2020.01.021) : fiche partielle. [{eid}]",
                'article_ids': [STOKES], 'evidence_ids': [eid], 'coverage_sufficient': False,
                'remaining_uncertainties': ['Lecture ciblée'], 'displayed_article_ids': [STOKES]}, tag)
        return respond
    first = final(lambda sent: unread, 'wrong')
    second = final(lambda sent: sorted(transmitted_evidence_ids(sent['input'], state['evidence']) & saved)[0], 'fixed')
    svc, sid, tr = make([*bad_calls(6), first, second], state)
    result = run_sync(svc, sid, original_request())
    assert len(tr.requests) == 8 and all(r['tool_choice'] == {'type': 'function', 'name': 'finalize_response'} for r in tr.requests[-2:])
    assert not any(r['parallel_tool_calls'] for r in tr.requests[-2:])
    refusal = next(json.loads(i['output']) for i in tr.requests[-1]['input'] if i.get('call_id') == 'wrong' and i.get('type') == 'function_call_output')
    assert refusal['rejection_code'] == 'unread_or_unknown_evidence'
    assert result['reason'] == 'stagnation' and 'fiche partielle' in result['answer'] and not result.get('fallback'), result
    assert scientific_state(factory, sid)['v5_tool_counters']['finalization_attempts'] == 2


def test_real_final_state_fallback_reports_what_was_established():
    """The run's own end state: no '0 publications', no discovered work called validated."""
    run = real()
    state = copy.deepcopy(run['state'])
    draft = next(o for o in run['operations'] if o['kind'] == 'decision' and o['response']['decision']['tool'] == 'finalize_response')
    state['v5_final_verification'] = {'attempted': [], 'ids': draft['response']['decision']['arguments']['article_ids']}
    adapter = ScientificAdapter(ScholarV4Config(api_key='offline', web_search_enabled=False), Mock(), sources=Mock())
    p = project(state, scope=Scope(1, 'x'))
    out = ScholarV5Service.selection_fallback(adapter, state, p, 'synthesis_reserve')
    text = out['answer']
    assert '0 publications proposées' not in text
    counts = out['selection_counts']
    assert counts['collected'] == 30 and counts['proposed_by_sol'] == 6 and counts['verified'] == 2
    assert set(out['primary_article_ids']) == {'paper_606fdde9135cae73eba2', 'paper_985d8c9ad0be085e50c1'}
    assert counts['restituted_with_analysis'] == 2 and counts['remaining'] > 0
    assert "une notice découverte n'est pas une publication validée" in text
    assert 'dont 2 à bibliographie vérifiée' in text
    # Unverified works are not presented as established publications.
    for aid in set(counts and draft['response']['decision']['arguments']['article_ids'])-set(out['primary_article_ids']):
        assert state['articles'][aid]['title'] not in text
