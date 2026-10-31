"""Block 3, deep reading and scientific cards: D1 compared works found during the request, D2 cards
of studied works, D3 depth asked by the request (unread versus inaccessible), D4 alternatives tried
before an abstract-only reading, R1/R3 existing work. Offline fixtures, no live transport."""
import copy
import hashlib
import json
import time
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.evidence import SEGMENTATION, passages, snapshot
from agents.EnnoScholarV5.models import Article
from .test_graph import case
from .test_live_integration import app_env, call, run_sync, scientific_state

A_DOI, B_DOI = '10.9999/block3-a', '10.9999/block3-b'
A = 'paper_' + hashlib.sha256(A_DOI.encode()).hexdigest()[:20]
B = 'paper_' + hashlib.sha256(B_DOI.encode()).hexdigest()[:20]
REQUEST = ('Compare les résultats expérimentaux de cet article avec une autre étude récente : valeurs mesurées, '
    'conditions expérimentales et conclusions.')
RESULTS = ('The PPM8:2 electrolyte reaches an ionic conductivity of 4.22 × 10−4 S cm−1 and a transference number of 0.624. '
    'The symmetric cell cycles for 500 h at 0.1 mA cm−2 with a stable polarization. ') * 3
METHODS = {'3.3. Cathode Preparation': 'The LiFePO4 cathode had an areal loading of approximately 1.0 mg cm−2. ' * 6,
    '3.5. Electrochemical Measurements': 'Ionic conductivity was measured by EIS at room temperature in SS/GPE/SS cells. ' * 6}


def article_a():
    chunks = [{'text': RESULTS, 'section': '2. Results / 2.3. Ionic Transport', 'page': None},
        *({'text': t, 'section': '3. Materials and Methods / ' + s, 'page': None} for s, t in METHODS.items())]
    text = '\n\n'.join(c['text'] for c in chunks)
    sha = hashlib.sha256(text.encode()).hexdigest()
    fulltext = {'access_status': 'fulltext_extracted', 'access_level': 'fulltext', 'text': text, 'chunks': chunks,
        'url': 'https://fixture.invalid/a', 'source_kind': 'jats', 'segmentation': SEGMENTATION, 'truncated': False,
        'document_version': {'version_id': 'document_' + sha, 'document_sha256': sha, 'label': 'doi:' + A_DOI,
            'identifiers': {'doi': A_DOI}, 'version_status': 'verified_publisher_version', 'url': 'https://fixture.invalid/a'},
        'attempts': [{'stage': 'extraction', 'outcome': 'success', 'url': 'https://fixture.invalid/a'}]}
    a = Article(id=A, canonical_work_id=A, title='Printed gel polymer electrolytes', doi=A_DOI, authors=['Ada Author'],
        year=2026, url='https://fixture.invalid/a', verified=True, verification_sources=['https://fixture.invalid/a'],
        abstract='Printed gel polymer electrolytes for lithium metal batteries.', fulltext=fulltext,
        access_status='fulltext_extracted', access_level='fulltext', fulltext_available=True)
    a.documents = {fulltext['document_version']['version_id']: copy.deepcopy(fulltext)}
    return a


def article_b(attempts=None):
    attempts = attempts if attempts is not None else [
        {'url': 'https://doi.org/' + B_DOI, 'stage': 'download', 'http_status': 403, 'error': 'HTTPStatusError'}]
    return Article(id=B, canonical_work_id=B, title='In-situ gel polymer electrolyte', doi=B_DOI, authors=['Bo Writer'],
        year=2025, url='https://fixture.invalid/b', verified=True, verification_sources=['https://fixture.invalid/b'],
        abstract='The electrolyte reaches 1.77×10−3 S cm−1 at 25 °C and cycles over 3000 h.',
        fulltext={'attempts': attempts}, access_status='access_blocked' if attempts else 'fulltext_available',
        access_level='abstract_only')


def base_state(goal=None):
    state = copy.deepcopy(case('chemistry')['state'])
    state['articles'][A] = article_a().durable()
    state['articles'][B] = article_b().durable()
    state['discovery_mode'] = 'original'
    state['messages'].append({'id': 'req-block3', 'role': 'user', 'content': REQUEST})
    state['turn_goal'] = goal or {'deliverable': 'comparison', 'reading_depth': 'deep', 'article_ids': [A],
        'references': {'article_ids': [A], 'scope': 'primary', 'message_id': 'req-block3'}}
    return state


def received(state, aid, sections):
    """Passages of `aid` received in the current request (as acknowledged Responses do)."""
    rows = [r for r in passages(Article.model_validate(state['articles'][aid])) if any(s in r['section'] for s in sections)]
    source = snapshot(Article.model_validate(state['articles'][aid]))
    state.setdefault('source_snapshots', {})[source['fingerprint']] = source
    for r in rows:
        state.setdefault('evidence', {})[r['evidence_id']] = r
        state.setdefault('shown_evidence_ids', []).append(r['evidence_id'])
    knowledge = state.setdefault('v5_request_knowledge', {'request_id': 'req-block3', 'keys': []})
    knowledge['request_id'] = 'req-block3'
    knowledge.setdefault('keys', []).extend('passage:' + r['evidence_id'] for r in rows)
    return [r['evidence_id'] for r in rows]


def toolkit(state):
    from .test_scientific_chain import adapter
    return adapter().toolkit(state)


def card(results, setup=()):
    empty = {k: [] for k in ('problem', 'technical_mechanism', 'experimental_setup', 'objective', 'method', 'contribution',
        'hypotheses', 'results', 'limitations', 'generalization', 'open_questions', 'relevance')}
    return {**empty, 'results': [{'statement': 'Conductivité 4.22 × 10−4 S cm−1 pour PPM8:2.', 'evidence_ids': list(results),
        'origin': 'source_statement'}], 'experimental_setup': [{'statement': 'Mesures EIS à température ambiante.',
        'evidence_ids': list(setup), 'origin': 'source_statement'}] if setup else [],
        'method': [{'statement': 'Conductivité mesurée par spectroscopie d’impédance.', 'evidence_ids': list(setup),
        'origin': 'source_statement'}] if setup else [],
        'method_family': 'Gel polymer electrolyte', 'analysis_limit': 'Lecture ciblée des résultats et mesures, non exhaustive.'}


# --- D1 ----------------------------------------------------------------------------------------

def test_d1_a_work_found_during_the_request_makes_the_comparison_required():
    state = base_state()
    received(state, A, ['Results'])
    assert workflow.request_article_ids(state) == [A]                        # one declared reference
    assert workflow.comparison_required(state, [A, B])                       # the cited study counts
    assert workflow.comparison_article_ids(state, [A, B]) == [A, B]
    negative = {'article_ids': [A, B], 'relations': [], 'assessment': 'Aucune relation fiable : B lu par son résumé.'}
    workflow.after_comparison(state, negative, {'saved': True})
    assert not workflow.comparison_required(state, [A, B])
    other = base_state()
    received(other, A, ['Results'])
    workflow.after_comparison(other, {**negative, 'article_ids': [B, 'paper_unrelated']}, {'saved': True})
    assert workflow.comparison_required(other, [A, B])                      # must include the reference


def test_d1_legacy_two_references_and_non_comparisons_are_unchanged():
    state = base_state({'deliverable': 'comparison', 'references': {'article_ids': [A, B], 'scope': 'exclusive'}})
    assert workflow.comparison_required(state) and workflow.comparison_required(state, [A])
    state['turn_goal']['deliverable'] = 'answer'
    assert not workflow.comparison_required(state, [A, B])


# --- D2 ----------------------------------------------------------------------------------------

def test_d2_studied_cited_works_need_a_card_partial_or_inline():
    state = base_state()
    ids = received(state, A, ['Results'])
    t = toolkit(state)
    args = {'article_ids': [A, B], 'evidence_ids': ids[:1]}
    assert set(workflow.cards_to_save(state, t, args)) == {A}                # B was not studied: nothing imposed
    assert workflow.cards_to_save(state, t, {**args, 'analyses': [{'article_id': A, 'analysis': card(ids[:1])}]}) == {}
    assert workflow.cards_to_save(state, t, {'article_ids': [B], 'evidence_ids': []}) == {}   # A read but not used


@pytest.mark.parametrize('goal', [
    {'deliverable': 'answer', 'reading_depth': 'none'},
    {'deliverable': 'deep_reading', 'reading_depth': 'deep', 'constraints': ['no_reanalysis']},
    {'deliverable': 'deep_reading'},                                         # documentary look-up of a table
])
def test_d2_documentary_requests_impose_no_card(goal):
    state = base_state({**goal, 'references': {'article_ids': [A], 'scope': 'exclusive'}})
    ids = received(state, A, ['Results'])
    assert workflow.cards_to_save(state, toolkit(state), {'article_ids': [A], 'evidence_ids': ids}) == {}


def test_d2_an_abstract_card_is_updated_after_a_full_text_reading():
    state = base_state()
    ids = received(state, A, ['Results'])
    t = toolkit(state)
    t.registry[A].analysis = {'results': [{'statement': 'From the abstract.', 'evidence_ids': ['ev_abstract'], 'origin': 'source_statement'}],
        'evidence': []}
    t.registry[A].analysis_basis = 'abstract'
    state['evidence']['ev_abstract'] = {**state['evidence'][ids[0]], 'evidence_id': 'ev_abstract', 'basis': 'abstract'}
    t.store.save()
    assert set(workflow.cards_to_save(state, toolkit(state), {'article_ids': [A], 'evidence_ids': ids})) == {A}


# --- D3 ----------------------------------------------------------------------------------------

def test_d3_requested_dimensions_follow_the_request_and_its_objective():
    state = base_state()
    assert workflow.requested_dimensions(state) == ['conditions', 'limits', 'methods', 'results']   # deep comparison
    state['turn_goal'].pop('reading_depth')
    assert workflow.requested_dimensions(state) == ['conditions', 'results']   # wording + comparison objective
    state['turn_goal']['deliverable'] = 'deep_reading'
    assert set(workflow.requested_dimensions(state)) >= {'methods', 'results', 'limits'}


def test_d3_unread_requested_sections_with_local_text_are_listed():
    state = base_state()
    ids = received(state, A, ['Results'])
    t = toolkit(state)
    gaps = workflow.reading_gaps(state, t, {'article_ids': [A, B]})
    assert {g['section'] for g in gaps[A]} == {'3. Materials and Methods / ' + s for s in METHODS}
    assert all(g['dimension'] == 'conditions' and g['read_passages'] == 0 for g in gaps[A])
    # Covered by the inline card, or by reading the methods: no gap.
    copied = copy.deepcopy(state)
    setup = received(copied, A, ['3.5'])
    state['evidence'].update({e: copied['evidence'][e] for e in setup})        # registered, not received here
    assert workflow.reading_gaps(state, t, {'article_ids': [A], 'analyses': [{'article_id': A, 'analysis': card(ids, setup)}]}) == {}
    read_state = copy.deepcopy(state)
    received(read_state, A, ['Methods'])
    assert workflow.reading_gaps(read_state, toolkit(read_state), {'article_ids': [A]}) == {}


def test_d3_unread_is_never_reported_as_inaccessible():
    state = base_state()
    t = toolkit(state)
    assert workflow.documentary_status(state, t, A) == 'texte intégral disponible'
    assert workflow.documentary_status(state, t, B) == 'inaccessible (récupération échouée)'
    state['articles'][B] = article_b(attempts=[]).durable()
    assert workflow.documentary_status(state, toolkit(state), B) == 'texte intégral non recherché'


def test_d2_d3_gate_refuses_once_per_request_then_publishes():
    from agents.EnnoScholarV5.runtime import ScientificAdapter
    from experiments.ennoscholar_v5.adapter import Decision
    state = base_state()
    ids = received(state, A, ['Results'])
    workflow.after_comparison(state, {'article_ids': [A, B], 'relations': [], 'assessment': 'Aucune relation fiable.'},
        {'saved': True})
    adapter = ScientificAdapter(ScholarV4Config(api_key='offline', web_search_enabled=False), Mock(), sources=Mock())
    final = Decision('finalize_response', {'answer': 'Comparaison prudente.', 'article_ids': [A, B], 'evidence_ids': ids[:1],
        'coverage_sufficient': False, 'remaining_uncertainties': [], 'analyses': None, 'displayed_article_ids': None})
    first = adapter.workflow_checks(state, adapter.toolkit(state), final)
    assert first['rejection_code'] == 'analysis_persistence_required' and first['pending_analysis_article_ids'] == [A]
    assert first['unread_requested_sections'][A] and first['documentary_status'][B].startswith('inaccessible')
    assert adapter.workflow_checks(state, adapter.toolkit(state), final) is None   # never blocked twice


# --- Full path: 259f55-like comparison through LangGraph --------------------------------------

def test_comparison_with_a_found_work_saves_relations_card_and_reads_requested_methods(app_env):
    make, factory, _ = app_env
    sources = Mock()
    sources.verify.side_effect = lambda article, *a, **k: article
    state = base_state()
    seen = {'outputs': {}}

    def outputs(sent):
        seen['outputs'].update({i['call_id']: json.loads(i['output']) for i in sent['input']
            if i.get('type') == 'function_call_output'})

    def ev(section):
        saved = scientific_state(factory, sid)
        return [e for e in saved['shown_evidence_ids'] if saved['evidence'][e]['article_id'] == A and section in saved['evidence'][e]['section']]

    def final(analyses=None):
        def respond(sent):
            outputs(sent)
            return call('finalize_response', {'answer': 'A atteint 4.22 × 10−4 S cm−1 ; B, lu par son résumé, 1.77×10−3 S cm−1. '
                f'[{A}] [{B}] ' + ' '.join(f'[{e}]' for e in ev('Results')[:1]), 'article_ids': [A, B],
                'evidence_ids': ev('Results')[:1], 'coverage_sufficient': False,
                'remaining_uncertainties': ['B : texte intégral inaccessible.'],
                'analyses': analyses(sent) if analyses else None, 'displayed_article_ids': None}, f'final{len(seen["outputs"])}')
        return respond

    def compare(sent):
        outputs(sent)
        return call('compare_articles', {'article_ids': [A, B], 'research_context': 'Comparer résultats et conditions.',
            'conclusions': None, 'relations': [], 'assessment': 'Aucune relation fiable : B lu seulement par son résumé.'}, 'cmp')

    def read_methods(sent):
        outputs(sent)
        return call('analyze_article', {'article_ids': [A], 'action': 'read', 'research_context': 'Conditions expérimentales.',
            'passage_query': '', 'section_filter': 'Methods', 'passage_offset': 0, 'passages_per_article': 8, 'analysis': None}, 'methods')

    def with_card(sent):
        return [{'article_id': A, 'analysis': card(ev('Results')[:1], ev('3.5')[:1])}]

    read = {'article_ids': [A], 'action': 'read', 'research_context': 'Résultats mesurés.', 'passage_query': 'conductivity',
        'section_filter': 'Results', 'passage_offset': 0, 'passages_per_article': 4, 'analysis': None}
    goal = {'requested_article_count': 2, 'deliverable': 'comparison', 'lock_ids': [], 'focus_lock_ids': [], 'exact_quote': '',
        'reading_depth': 'deep', 'understanding': 'Comparer les résultats et conditions de A à une étude récente.',
        'comparison_requested': True, 'search_needed': False, 'article_ids': [A], 'article_scope': 'primary',
        'references_ambiguous': False, 'constraints': [], 'research_need': None}
    # Natural order: read, refused once for the card and the requested methods (D2/D3), read the
    # methods, refused once for the unsaved comparison of the found study (D1), compare, publish.
    svc, sid, transport = make([call('set_turn_goal', goal, 'goal'), call('get_article', {'identifiers': [B_DOI]}, 'found'),
        call('analyze_article', read, 'results'), final(), read_methods, final(with_card), compare, final(with_card)],
        state, sources=sources)
    result = run_sync(svc, sid, REQUEST)
    saved = scientific_state(factory, sid)
    from agents.EnnoScholarV5.persistence import Operation
    with factory() as db:
        finals = [o.response.get('output') for o in db.query(Operation).order_by(Operation.revision)
            if o.kind == 'action' and o.request.get('tool') == 'finalize_response' and o.response]
    assert len(finals) == 3 and finals[-1].get('published'), (result.get('reason'), [json.dumps(f, ensure_ascii=False)[:500] for f in finals])
    first, second = finals[0], finals[1]
    assert first['rejection_code'] == 'analysis_persistence_required' and first['pending_analysis_article_ids'] == [A]  # D2
    assert {g['section'] for g in first['unread_requested_sections'][A]} >= {'3. Materials and Methods / 3.5. Electrochemical Measurements'}  # D3
    assert first['documentary_status'][B].startswith('inaccessible') and first['documentary_status'][A] == 'texte intégral disponible'
    assert second['rejection_code'] == 'comparison_not_saved' and set(second['article_ids']) == {A, B}         # D1
    assert result['answer'] and not result.get('fallback')
    assert saved['articles'][A]['analysis'].get('experimental_setup') and saved['articles'][A]['analysis'].get('results')
    assert any(set(d['article_ids']) == {A, B} for d in saved['v5_comparison_decisions'])
    assert not saved['articles'][B].get('analysis')                            # nothing imposed on the unread study


# --- D4: every authorised representation is tried before an abstract-only reading -------------

def test_d4_all_known_representations_are_tried_before_abstract_only(monkeypatch):
    from agents.EnnoScholarV5.tools import fulltext
    tried = []

    def forbidden(url, *args, **kwargs):
        tried.append(url)
        import httpx
        raise httpx.HTTPStatusError('403', request=httpx.Request('GET', url), response=httpx.Response(403))
    monkeypatch.setattr(fulltext.base, 'fetch_public', forbidden)
    b = article_b(attempts=[])
    b.pdf_url = 'https://publisher.invalid/pdfdirect/' + B_DOI
    b.work_urls = ['https://doi.org/' + B_DOI, 'https://repository.invalid/record/1']
    b.document_candidates = [{'url': 'https://mirror.invalid/b.pdf', 'is_oa': True, 'format': 'pdf'}]
    result = fulltext.get_fulltext(b, Mock(), time.monotonic()+60, config=ScholarV4Config(api_key='offline'))
    assert not result.get('text')
    assert {b.pdf_url, *b.work_urls, 'https://mirror.invalid/b.pdf'} <= set(tried)


# --- R1 / R3: existing work ---------------------------------------------------------------------

def test_r1_a_new_article_added_to_a_drafted_section_is_read_before_it_is_used():
    state = base_state({'deliverable': 'draft', 'references': {'article_ids': [A], 'scope': 'primary'}})
    t = toolkit(state)
    section = {'id': 'section_r1', 'title': '1. Electrolytes', 'article_ids': [B, A], 'drafts': [{'text': 'Ancienne version.'}]}
    prep = workflow.section_preparation(state, t, section)
    assert prep['sources'][A]['state'] == 'to_analyse'                       # local text: read and save before use
    assert prep['sources'][B]['state'] == 'unavailable'                      # retrieval failed: stated limit
    assert any(a['article_id'] == A and a['kind'] == 'local' for a in prep['actions'])
    assert section['drafts'] == [{'text': 'Ancienne version.'}]              # the saved draft is not rewritten


def test_r3_a_card_missing_core_dimensions_with_local_text_is_deepened_not_ready():
    state = base_state({'deliverable': 'draft', 'references': {'article_ids': [A], 'scope': 'primary'}})
    ids = received(state, A, ['Results'])
    t = toolkit(state)
    from agents.EnnoScholarV4.tools.article_analysis import ArticleAnalyzer
    from agents.EnnoScholarV4.toolkit import Analysis
    article = t.registry[A]
    ArticleAnalyzer.save(article, Analysis.model_validate(card(ids[:1])), t.evidence, t.shown_evidence, t.snapshots)
    t.store.save()
    prep = workflow.section_preparation(state, toolkit(state), {'id': 's', 'title': '1', 'article_ids': [A]})
    assert prep['sources'][A]['state'] == 'to_deepen' and prep['sources'][A]['missing']
