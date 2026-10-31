"""P1 tools that cannot succeed, P2 calibrated admission, P3 bounded Web reserve (run b10ab481:
258 000 tokens of discovery, three admission refusals, no reading). Offline only."""
import json
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5.models import Article
from agents.EnnoScholarV5.persistence import Budget, admission_sizing
from agents.EnnoScholarV5.runtime import ScientificAdapter, unusable_tools
from agents.EnnoScholarV5.toolkit import empty_library_guidance
from .test_live_integration import app_env, call, run_sync, scientific_state

# Real ledger of b10ab481: (offline point estimate, actual input, output, hosted Web used, Web offered).
LEDGER = [(31215, 21683, 645, 0, True), (32924, 31002, 114, 1, True), (33041, 22399, 558, 0, True),
    (33374, 22701, 54, 0, True), (32227, 30855, 362, 1, True), (49776, 42692, 464, 1, True),
    (46939, 28710, 55, 0, True), (47785, 29964, 116, 0, True), (45101, 28361, 505, 0, True)]
LIMIT, HOLD, MAX_OUTPUT, WEB_RESERVE = 400000, 60000, 7000, 128000


def settled(point, actual, output, web, sizing=None, at=0):
    metrics = {'point_input_tokens': point, 'admission_input_tokens': (sizing or {}).get('input_tokens', point),
        **({'admission_sizing': sizing} if sizing else {})}
    return {'status': 'settled', 'input_tokens': actual, 'output_tokens': output, 'web': True, 'admitted_at': f'{at:04d}',
        'request_metrics': metrics, 'meta': {'parts': {'web_calls': .01 if web else 0.}}}


def replay(rule):
    history, used, rows = [], 0, []
    for i, (point, actual, output, web, offered) in enumerate(LEDGER):
        sizing = rule(history, point)
        requested = sizing['input_tokens'] + MAX_OUTPUT + (sizing['web_tokens'] if offered else 0)
        rows.append({'call': i, 'used': used, 'requested': requested, 'admitted': used + requested <= LIMIT - HOLD,
            'covers_actual': sizing['input_tokens'] + (sizing['web_tokens'] if web else 0) >= actual})
        history.append(settled(point, actual, output, web, sizing if sizing.get('calibrated') else None, i))
        used += actual + output
    return rows


def old_rule(history, point):
    return {'input_tokens': point, 'web_tokens': WEB_RESERVE}


def new_rule(history, point):
    return admission_sizing(history, point, WEB_RESERVE)


# --- P2 / P3 on the real measurements ---------------------------------------------------------

def test_real_b10ab481_old_admission_refuses_the_last_three_web_calls():
    rows = replay(old_rule)
    assert [r['call'] for r in rows if not r['admitted']] == [6, 7, 8]          # the observed refusals


def test_real_b10ab481_calibrated_admission_admits_all_and_never_underestimates():
    rows = replay(new_rule)
    assert all(r['admitted'] for r in rows)
    assert all(r['covers_actual'] for r in rows)                                 # no call above its admission
    assert rows[1]['requested'] > 150000                                          # first Web call: full reserve
    assert all(r['requested'] < 90000 for r in rows[2:])                          # then sized from measurements


def test_sizing_bounds_and_self_correction():
    assert admission_sizing([], 30000, WEB_RESERVE) == {'point_input_tokens': 30000, 'samples': 0, 'calibrated': False,
        'input_tokens': 30000, 'web_tokens': WEB_RESERVE, 'web_samples': 0}       # no measurement: unchanged
    tiny = [settled(30000, 100, 10, 0)]
    assert admission_sizing(tiny, 30000, WEB_RESERVE)['input_tokens'] == 15000    # never below half the estimate
    under = [settled(30000, 36000, 10, 0)]
    assert admission_sizing(under, 30000, WEB_RESERVE)['input_tokens'] == 41400   # an underestimate raises it
    web = [settled(30000, 21000, 10, 0, at=0), settled(30000, 21000+60000, 10, 1, at=1)]
    assert admission_sizing(web, 30000, WEB_RESERVE)['web_tokens'] == WEB_RESERVE  # never above the configured reserve
    small = [settled(30000, 21000, 10, 0, at=0), settled(30000, 21000+2000, 10, 1, at=1)]
    assert admission_sizing(small, 30000, WEB_RESERVE)['web_tokens'] == 32000      # floor
    calibrated = admission_sizing([settled(30000, 21000, 10, 0)], 30000, WEB_RESERVE)
    exceeded = [settled(30000, 21000, 10, 0, at=0), settled(30000, calibrated['input_tokens'] + 1, 10, 0, calibrated, 1)]
    assert admission_sizing(exceeded, 30000, WEB_RESERVE)['calibrated'] is False   # an exceeded calibration stops


def test_admission_through_the_meter_uses_the_session_measurements(app_env):
    make, factory, _ = app_env
    from .test_live_integration import finish
    svc, sid, tr = make([call('set_turn_goal', GOAL, 'g'), finish()],
        config=ScholarV4Config(api_key='offline', web_search_enabled=False))
    result = run_sync(svc, sid, REQUEST)
    with factory() as db:
        entries = db.get(Budget, result['id']).data['entries']
    first, second = entries[0]['request_metrics'], entries[1]['request_metrics']
    assert first['admission_sizing']['calibrated'] is False
    assert second['admission_sizing']['calibrated'] and second['admission_input_tokens'] < second['point_input_tokens']
    assert entries[1]['reserved_tokens'] < entries[0]['reserved_tokens'] + (second['point_input_tokens'] - first['point_input_tokens'])
    assert result['usage']['estimated_cost_usd'] == pytest.approx(sum(e['cost_usd'] for e in entries))  # settled on usage


# --- P1 ------------------------------------------------------------------------------------------

def test_research_controller_reviews_are_not_offered_in_original_mode():
    assert unusable_tools({'discovery_mode': 'original'}) == {'review_discovery', 'review_research_coverage'}
    active = {'turn_goal': {'deliverable': 'literature_search'}}                  # V4 controller (legacy state)
    assert unusable_tools(active) == set()


def test_empty_library_search_says_so_and_a_filled_one_is_unchanged():
    empty = {'articles': [], 'delivery': {'guidance': 'Évaluer'}, 'corpus_total': 0, 'reused_analyses': [],
        'memory_page': {'has_more': True, 'next_offset': 11},
        'discovery_attention': {'priority': 'synthesize', 'guidance': 'Évaluer les candidats conservés'}}
    guided = empty_library_guidance(empty, {})
    assert guided['discovery_attention']['priority'] == 'discover' and 'search_scientific' in guided['delivery']['guidance']
    assert 'offset=11' in guided['discovery_attention']['guidance'] and empty['discovery_attention']['priority'] == 'synthesize'
    assert empty_library_guidance(empty, {'paper_x': object()}) is empty           # library not empty: V4 output kept
    hits = {**empty, 'articles': [{'id': 'paper_x'}]}
    assert empty_library_guidance(hits, {}) is hits


# --- Full path: search → selection → reading → card, through LangGraph ------------------------

REQUEST = "Fais-moi un état de l'art récent sur le recyclage enzymatique du plastique PET, en comparant les principales méthodes."
GOAL = {"requested_article_count": None, "deliverable": "literature_search", "lock_ids": [], "focus_lock_ids": [],
    "exact_quote": "", "reading_depth": "deep", "understanding": "État de l'art du recyclage enzymatique du PET.",
    "comparison_requested": True, "search_needed": True, "article_ids": None, "article_scope": "open",
    "references_ambiguous": False, "constraints": [], "research_need": None}
DOI = '10.9999/pet-hydrolase'


def test_search_selection_reading_and_card_without_unusable_tools(app_env):
    from .test_block3_reading_cards import card
    make, factory, _ = app_env
    sources = Mock()
    found = Article(title='Engineered thermostable PET hydrolase', doi=DOI, authors=['A Author'], year=2025,
        url='https://fixture.invalid/pet', verified=True, verification_sources=['https://fixture.invalid/pet'],
        abstract='An engineered hydrolase depolymerises 90 % of PET within 10 h at 72 °C.')
    sources.search.return_value = ([found], [])
    sources.last_provider_stats = {'openalex': {'returned': 1}}
    sources.verify.side_effect = lambda article, *a, **k: article.model_copy(update={'verified': True,
        'verification_sources': ['https://fixture.invalid/pet']})
    outputs, chosen = [], []

    def collected(sent):
        return call('search_collected_articles', {'query': 'PET hydrolase', 'limit': 8, 'offset': 0,
            'exclude_presented': True, 'family': None, 'approach_ids': []}, 'local')

    def search(sent):
        outputs.append(next(json.loads(i['output']) for i in sent['input']
            if i.get('type') == 'function_call_output' and i.get('call_id') == 'local'))
        return call('search_scientific', {'query': 'thermostable PET hydrolase recycling', 'sources': ['openalex'],
            'limit_per_source': 8, 'offset': 0, 'family': 'Hydrolases'}, 'search')

    def read(sent):
        found_ids = next(json.loads(i['output']) for i in sent['input']
            if i.get('type') == 'function_call_output' and i.get('call_id') == 'search')['articles']
        chosen.append(found_ids[0]['id'])
        return call('analyze_article', {'article_ids': [chosen[0]], 'action': 'read', 'research_context': 'Résultats et conditions.',
            'passage_query': '', 'passage_offset': 0, 'passages_per_article': 4, 'analysis': None}, 'read')

    def final(sent):
        AID = chosen[0]
        read_out = next(json.loads(i['output']) for i in sent['input']
            if i.get('type') == 'function_call_output' and i.get('call_id') == 'read')
        ids = [p['evidence_id'] for p in read_out['reading'][0]['passages']]
        return call('finalize_response', {'answer': f'Une hydrolase thermostable dépolymérise 90 % du PET en 10 h [{AID}] [{ids[0]}].',
            'article_ids': [AID], 'evidence_ids': ids[:1], 'coverage_sufficient': False,
            'remaining_uncertainties': ['Lecture fondée sur le résumé.'], 'analyses': [{'article_id': AID, 'analysis': card(ids[:1])}],
            'displayed_article_ids': None}, 'final')

    svc, sid, tr = make([call('set_turn_goal', GOAL, 'goal'), collected, search, read, final], sources=sources)
    result = run_sync(svc, sid, REQUEST)
    saved = scientific_state(factory, sid)
    AID = chosen[0] if chosen else None
    offered = [{t.get('name') or t.get('type') for t in r['tools']} for r in tr.requests]
    assert all(not ({'review_discovery', 'review_research_coverage'} & names) for names in offered)
    assert len({frozenset(n) for n in offered}) == 1                             # stable prefix: one tool list
    assert outputs, (result.get('reason'), len(tr.requests), result.get('answer', '')[:300])
    assert outputs[0]['discovery_attention']['priority'] == 'discover'
    assert result['answer'] and not result.get('diagnostic')
    from agents.EnnoScholarV5.persistence import Operation
    with factory() as db:
        ops = [(o.request.get('tool'), json.dumps((o.response or {}).get('output'), ensure_ascii=False)[:1200])
            for o in db.query(Operation).order_by(Operation.revision) if o.kind == 'action']
        finals = [o for o in ops if o[0] == 'finalize_response'] or ops
    assert AID and saved['articles'][AID]['analysis'].get('results'), (result.get('reason'), [o[0] for o in ops],
        len(tr.requests), sum(saved['evidence'].get(e, {}).get('article_id') == AID for e in saved.get('shown_evidence_ids', [])),
        [o[1] for o in ops if o[0] == 'finalize_response'])                                   # card saved from what was read
    assert len(tr.requests) == 5 and sources.search.call_count == 1
