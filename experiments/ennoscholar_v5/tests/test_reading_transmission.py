"""Run 48d2c500: large multi-article indexes, repeated notices, reading progress and card depth.
Offline fixtures only; no provider, network or real database."""
import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.evidence import passages, snapshot
from agents.EnnoScholarV5.models import Article
from agents.EnnoScholarV5.science import analysis_depth, passage_index
from experiments.ennoscholar_v5.adapter import Decision
from experiments.ennoscholar_v5.policy import known_notices, reusable
from .test_scientific_chain import adapter

SECTIONS = ['Abstract', '1. Introduction', '2. Methods', '3. Results', '4. Limitations']


def work(n, per_section=8):
    chunks = [{'text': f'{section} of work {n}, paragraph {i}: the hydrolase converts {i*7} % of PET at {60+i} °C '
        f'within {i+2} h in buffer {n}. ' * 2, 'section': section, 'page': 1+j} for j, section in enumerate(SECTIONS)
        for i in range(per_section)]
    aid = f'paper_work{n}'
    return Article(id=aid, canonical_work_id=aid, title=f'Work {n} on PET hydrolases', doi=f'10.9999/work{n}',
        authors=['A Author'], year=2025,
        url=f'https://fixture.invalid/{n}', verified=True, verification_sources=[f'https://fixture.invalid/{n}'],
        abstract=f'Abstract of work {n}.', access_status='fulltext_extracted', access_level='fulltext', fulltext_available=True,
        fulltext={'text': '\n\n'.join(c['text'] for c in chunks), 'chunks': chunks, 'url': f'https://fixture.invalid/{n}'})


def state_with(*articles):
    state = {'articles': {a.id: a.durable() for a in articles}, 'evidence': {}, 'source_snapshots': {},
        'shown_evidence_ids': [], 'messages': [{'id': 'req', 'role': 'user', 'content': 'Analyse en profondeur ces travaux.'}],
        'turn_goal': {'deliverable': 'deep_reading', 'reading_depth': 'deep'}, 'v5_request_knowledge': {'request_id': 'req', 'keys': []}}
    for a in articles:
        s = snapshot(a)
        state['source_snapshots'][s['fingerprint']] = s
    return state


def receive(state, article, sections, *, examine=False):
    rows = [r for r in passages(article) if any(r['section'] == s for s in sections)]
    for r in rows:
        state['evidence'][r['evidence_id']] = r
        state['shown_evidence_ids'].append(r['evidence_id'])
    return [r['evidence_id'] for r in rows]


def index(t, ids, offset=0, limit=80, section=None):
    return t.execute('analyze_article', {'article_ids': ids, 'action': 'index', 'research_context': 'Index.',
        'section_filter': section, 'index_offset': offset, 'index_limit': limit, 'passage_query': '', 'passage_offset': 0,
        'passages_per_article': 4, 'analysis': None}, time.monotonic()+30)


# --- 1. Large multi-article index: shortened, exact cursors, nothing credited unsent ---------

def test_large_multi_article_index_is_shortened_with_exact_cursors():
    arts = [work(1), work(2), work(3)]
    state = state_with(*arts)
    t = adapter().toolkit(state)
    decision = Decision('analyze_article', {'action': 'index', 'article_ids': [a.id for a in arts], 'index_limit': 80})
    full = index(t, [a.id for a in arts])
    assert len(json.dumps(full, ensure_ascii=False)) > 30000                                        # the 80/60-entry case
    fitted = workflow.fit_output(state, t, decision, copy.deepcopy(full), 9000)
    assert not fitted.get('transmission_deferred') and len(json.dumps(fitted, ensure_ascii=False)) + 300 <= 9000
    for before, after in zip(full['indexes'], fitted['indexes']):
        kept = after['passages']
        assert kept == before['passages'][:len(kept)]                           # a prefix, in order
        if len(kept) < len(before['passages']):
            assert after['next_offset'] == before['passages'][len(kept)]['position'] and after['transmission']
    credited = {k for k in workflow.delivery_keys(decision, fitted)}
    assert len(credited) == sum(len(r['passages']) for r in fitted['indexes'])   # only transmitted entries
    # Following the cursors returns every remaining entry exactly once, in document order.
    for before, after in zip(full['indexes'], fitted['indexes']):
        seen, cursor = [p['evidence_id'] for p in after['passages']], after['next_offset']
        while cursor is not None:
            page = index(t, [after['article_id']], offset=cursor, limit=7)['indexes'][0]
            seen += [p['evidence_id'] for p in page['passages']]
            cursor = page['next_offset']
        assert seen == [p['evidence_id'] for p in before['passages']]


def test_index_cursor_is_stable_when_passages_are_read_or_examined_between_pages():
    a = work(1)
    state = state_with(a)
    t = adapter().toolkit(state)
    first = passage_index(state, a, limit=10)
    receive(state, a, ['Abstract'])                                              # presented between pages
    state.setdefault('scientific_reading', {})['tasks'] = [{'article_id': a.id, 'examined_evidence_ids': []}]
    second = passage_index(state, a, offset=first['next_offset'], limit=10)
    ordered = [r['evidence_id'] for r in passages(a)]
    assert [p['evidence_id'] for p in first['passages'] + second['passages']] == ordered[:20]


def test_a_too_small_room_defers_the_index_without_crediting_it():
    arts = [work(1)]
    state = state_with(*arts)
    t = adapter().toolkit(state)
    decision = Decision('analyze_article', {'action': 'index', 'article_ids': [arts[0].id]})
    fitted = workflow.fit_output(state, t, decision, index(t, [arts[0].id]), 400)
    assert fitted.get('transmission_deferred') and not workflow.delivery_keys(decision, fitted)


# --- 2. Identical and overlapping get_article batches --------------------------------------------

def deliver_notices(state, t, ids):
    decision = SimpleNamespace(tool='get_article', arguments={'identifiers': ids})
    output = t.execute('get_article', {'identifiers': ids}, time.monotonic()+30)
    workflow.record_delivery(state, decision, output, confirmed=False)
    workflow.record_delivery(state, decision, output)                            # acknowledged by Sol
    return output


def test_identical_or_reordered_batches_get_a_short_receipt():
    arts = [work(1), work(2)]
    state = state_with(*arts)
    t = adapter().toolkit(state)
    full = deliver_notices(state, t, [arts[0].id, arts[1].id])
    _, receipt, reason = reusable(state, Decision('get_article', {'identifiers': [arts[1].doi, arts[0].id]}), [])
    assert reason == 'same_operation' and receipt['articles'] == [] and len(receipt['already_received']) == 2
    assert len(json.dumps(receipt)) < len(json.dumps(full)) / 3


def test_overlapping_batch_fetches_only_new_or_changed_notices():
    arts = [work(1), work(2), work(3)]
    state = state_with(*arts)
    t = adapter().toolkit(state)
    deliver_notices(state, t, [arts[0].id, arts[1].id])
    assert reusable(state, Decision('get_article', {'identifiers': [arts[0].id, arts[2].id]}), [])[1] is None
    out = adapter().toolkit(state).execute('get_article', {'identifiers': [arts[0].id, arts[2].id]}, time.monotonic()+30)
    assert [a['id'] for a in out['articles']] == [arts[2].id]
    assert [r['id'] for r in out['already_received']] == [arts[0].id]
    # A documentary change is useful information: the changed notice is sent again.
    state['articles'][arts[1].id]['assessment'] = {'decision': 'retain', 'reason': 'Méthodes pertinentes.'}
    assert arts[1].id not in known_notices(state, [arts[1].id])
    # Not acknowledged (deferred or never received): not counted as received.
    other = state_with(work(4))
    workflow.record_delivery(other, SimpleNamespace(tool='get_article', arguments={}), {'articles': [{'id': 'paper_work4'}]},
        confirmed=False)
    assert not known_notices(other, ['paper_work4'])


# --- 3. Working memory keeps cursors, received notices and dimensions to read ----------------

def test_working_memory_keeps_index_cursors_notices_and_dimensions_to_read():
    a, b = work(1), work(2)
    state = state_with(a, b)
    t = adapter().toolkit(state)
    deliver_notices(state, t, [a.id, b.id])
    decision = SimpleNamespace(tool='analyze_article', arguments={'action': 'index'})
    page = index(t, [a.id], limit=10)
    workflow.record_delivery(state, decision, page)
    memory = workflow.working_memory(state)
    row = next(r for r in memory['articles'] if r['article_id'] == a.id)
    assert row['index_cursors']['toutes sections']['index_offset'] == page['indexes'][0]['next_offset']
    assert set(row['to_read']) == {'methods', 'results', 'limits'}                # the dimensions this request asks for
    assert b.id in memory['notices_received']


# --- 4. Provenance, body coverage and sufficiency -----------------------------------------------

def claims(ids):
    return [{'statement': 'Observation sourcée.', 'evidence_ids': ids[:1], 'origin': 'source_statement'}]


def saved_card(state, article, by_field):
    t = adapter().toolkit(state)
    from agents.EnnoScholarV4.tools.article_analysis import ArticleAnalyzer
    from agents.EnnoScholarV4.toolkit import Analysis
    empty = {k: [] for k in ('problem', 'technical_mechanism', 'experimental_setup', 'objective', 'method', 'contribution',
        'hypotheses', 'results', 'limitations', 'generalization', 'open_questions', 'relevance')}
    analysis = Analysis.model_validate({**empty, **by_field, 'method_family': 'Hydrolase', 'analysis_limit': 'Lecture ciblée.'})
    card = t.registry[article.id]
    ArticleAnalyzer.save(card, analysis, t.evidence, t.shown_evidence, t.snapshots)
    t.store.save()
    return Article.model_validate(state['articles'][article.id])


def test_a_card_from_the_pdf_abstract_and_introduction_is_never_a_finished_analysis():
    a = work(1)
    state = state_with(a)
    intro = receive(state, a, ['Abstract', '1. Introduction'])
    card = saved_card(state, a, {'method': claims(intro), 'results': claims(intro[3:]), 'limitations': claims(intro[5:])})
    assert card.access_status == 'fulltext_analyzed' and card.analysis_basis == 'fulltext'    # V4 provenance, kept
    depth = analysis_depth(state, card)
    assert depth['depth'] == 'introductory' and depth['provenance'] == 'fulltext'
    assert set(depth['to_read']) == {'methods', 'results', 'limits', 'conditions'}
    t = adapter().toolkit(state)
    assert not workflow.fulltext_core(card, t.evidence, t.snapshots)
    prep = workflow.section_preparation(state, t, {'id': 's', 'title': '1', 'article_ids': [a.id]})
    assert prep['sources'][a.id]['state'] == 'to_deepen' and 'introduction' in prep['sources'][a.id]['label']


def test_body_reading_gives_partial_then_complete_depth_and_legacy_cards_keep_working():
    a = work(1)
    state = state_with(a)
    methods, results, limits = (receive(state, a, [s]) for s in ('2. Methods', '3. Results', '4. Limitations'))
    partial = saved_card(state, a, {'method': claims(methods), 'results': claims(results)})
    assert analysis_depth(state, partial)['depth'] == 'partial'
    assert analysis_depth(state, partial)['to_read'] == ['limits', 'conditions']
    complete = saved_card(state, a, {'limitations': claims(limits), 'experimental_setup': claims(methods[1:])})
    assert analysis_depth(state, complete)['depth'] == 'complete'
    assert workflow.fulltext_core(complete, state['evidence'], state['source_snapshots'])
    # A legacy abstract-only card keeps its meaning.
    abstract = Article(id='paper_abs', title='Abstract only', abstract='Hydrolase converts 90 % of PET.', verified=True)
    legacy = state_with(abstract)
    rows = receive(legacy, abstract, ['Abstract'])
    card = saved_card(legacy, abstract, {'results': claims(rows)})
    assert analysis_depth(legacy, card)['depth'] == 'abstract_only'


# --- Mini path: search → index → methods/results → card saved → resume -------------------------

def test_search_index_read_save_then_resume_without_rereading(app_env):
    from .test_live_integration import call, run_sync, scientific_state
    from .test_block3_reading_cards import card
    import httpx
    make, factory, _ = app_env
    found = work(7)
    sources = Mock()
    sources.search.return_value = ([found], [])
    sources.last_provider_stats = {'openalex': {'returned': 1}}
    sources.verify.side_effect = lambda article, *a, **k: article.model_copy(update={'verified': True,
        'verification_sources': ['https://fixture.invalid/7']})
    chosen = []

    def result(sent, call_id):
        return next(json.loads(i['output']) for i in sent['input'] if i.get('type') == 'function_call_output' and i.get('call_id') == call_id)

    def index_call(sent):
        chosen.append(result(sent, 'search')['articles'][0]['id'])
        return call('analyze_article', {'article_ids': chosen, 'action': 'index', 'research_context': 'Méthodes et résultats.',
            'section_filter': None, 'index_offset': 0, 'index_limit': 80, 'passage_query': '', 'passage_offset': 0,
            'passages_per_article': 4, 'analysis': None}, 'index')

    def read_call(sent):
        locators = result(sent, 'index')['indexes'][0]['passages']
        picked = [p['evidence_id'] for p in locators if p['section'] in {'2. Methods', '3. Results'}][:4]
        return call('analyze_article', {'article_ids': chosen, 'action': 'read', 'research_context': 'Méthodes et résultats.',
            'evidence_ids': picked, 'passage_query': '', 'passage_offset': 0, 'passages_per_article': 4, 'analysis': None}, 'read')

    def save_call(sent):
        ids = [p['evidence_id'] for p in result(sent, 'read')['reading'][0]['passages']]
        analysis = card(ids[:1], ids[1:2])
        analysis['method'] = [{'statement': 'Hydrolase en tampon.', 'evidence_ids': ids[:1], 'origin': 'source_statement'}]
        return call('analyze_article', {'article_ids': chosen, 'action': 'save', 'research_context': 'Fiche.',
            'passage_query': '', 'passage_offset': 0, 'passages_per_article': 4, 'analysis': analysis}, 'save')

    goal = {'requested_article_count': None, 'deliverable': 'deep_reading', 'lock_ids': [], 'focus_lock_ids': [], 'exact_quote': '',
        'reading_depth': 'deep', 'understanding': 'Lire méthodes et résultats.', 'comparison_requested': False, 'search_needed': True,
        'article_ids': None, 'article_scope': 'open', 'references_ambiguous': False, 'constraints': [], 'research_need': None}
    drop = httpx.RemoteProtocolError('peer closed connection')

    def final(sent):
        return call('finalize_response', {'answer': f'Fiche partielle enregistrée [{chosen[0]}].', 'article_ids': chosen,
            'evidence_ids': [], 'coverage_sufficient': False, 'remaining_uncertainties': ['Limites non lues.'],
            'analyses': None, 'displayed_article_ids': None}, 'final')

    search = call('search_scientific', {'query': 'PET hydrolase', 'sources': ['openalex'], 'limit_per_source': 8,
        'offset': 0, 'family': 'Hydrolases'}, 'search')
    svc, sid, tr = make([call('set_turn_goal', goal, 'goal'), search, index_call, read_call, save_call, drop, final, final],
        sources=sources)
    first = run_sync(svc, sid, 'Analyse en profondeur les méthodes et résultats des hydrolases du PET.')
    saved = scientific_state(factory, sid)
    aid = chosen[0]
    depth = analysis_depth(saved, Article.model_validate(saved['articles'][aid]))
    assert first['reason'] == 'uncertain_model_call' and depth['depth'] == 'partial'          # saved before the drop
    assert depth['coverage']['methods'] == 'body' and depth['coverage']['results'] == 'body'
    svc.reconcile_run(1, first['id'])
    svc.resume_run(1, first['id'])
    svc.pool.shutdown(wait=True)
    resumed = svc.run(1, first['id'])
    from agents.EnnoScholarV5.persistence import Operation
    with factory() as db:
        finals = [o.response['output'] for o in db.query(Operation).order_by(Operation.revision)
            if o.kind == 'action' and o.request.get('tool') == 'finalize_response' and o.response]
    assert resumed['answer'] and len(tr.requests) == 8 and sources.search.call_count == 1     # nothing re-run
    # Asked once to read the requested limits (local text, never read), then published.
    assert finals[0]['rejection_code'] == 'requested_sections_unread' and finals[-1].get('published')
    assert {g['dimension'] for g in finals[0]['unread_requested_sections'][aid]} == {'limits'}
    after = scientific_state(factory, sid)
    assert after['articles'][aid]['analysis'] == saved['articles'][aid]['analysis']


from .test_live_integration import app_env  # noqa: E402,F401  (fixture)
