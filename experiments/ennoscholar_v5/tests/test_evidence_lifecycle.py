"""Current proof authority and dependent views; archived content is preserved."""
import copy
import json
import time
import pytest

from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.evidence import (exact_source_valid, source_integrity_valid,
    proof_status, usable_proof, set_proof_status, reusable_science)
from agents.EnnoScholarV5.models import Article
from agents.EnnoScholarV5.science import (passage_states, reading_status, save_review,
    CoverageReview, Measurement, validate_measurements)
from agents.EnnoScholarV5.argumentation import argument_packet, bounded_argument_packet, relation_current
from . import test_scientific_chain as sc
from . import test_comparison_memory as cm
from .test_graph import payload, read
from .test_live_integration import app_env


def fixture():
    state, ids, eids, args = sc.comparison_case()
    state = json.loads(json.dumps(state))
    state['scientific_relations'] = []
    state['research_gaps'] = []
    return state, ids, eids, args


def execute(state, name, args):
    return sc.adapter().toolkit(state).execute(name, args, time.monotonic()+60)


@pytest.mark.parametrize('status', ['withdrawn', 'historical', 'replaced'])
def test_lifecycle_changes_usability_without_erasing_the_source_or_card(status):
    state, ids, eids, _ = fixture()
    eid = eids[0]
    old = copy.deepcopy(state['evidence'][eid])
    copies = copy.deepcopy(state['articles'][ids[0]]['analysis'])
    replacement = next(p['evidence_id'] for p in copies['evidence'] if p['evidence_id'] != eid)
    set_proof_status(state, eid, status, replacement=replacement if status == 'replaced' else None)
    state = json.loads(json.dumps(state))
    assert proof_status(state, eid) == status
    assert not usable_proof(state, eid)
    assert source_integrity_valid(state['evidence'][eid], state['source_snapshots'])
    assert not exact_source_valid(state['evidence'][eid], state['source_snapshots'])
    assert state['evidence'][eid]['exact_quote'] == old['exact_quote']
    assert state['articles'][ids[0]]['analysis'] == copies
    assert eid not in passage_states(state, Article.model_validate(state['articles'][ids[0]]))['analyzed']


def test_missing_registry_entry_is_not_restored_from_an_embedded_copy():
    state, ids, eids, _ = fixture()
    eid = eids[0]
    article = Article.model_validate(state['articles'][ids[0]])
    del state['evidence'][eid]
    assert proof_status(state, eid) == 'missing'
    assert eid not in passage_states(state, article)['saved']
    reused = reusable_science(article, state['source_snapshots'], state['evidence'])
    if reused:
        assert eid not in reused[2]
    assert any(p['evidence_id'] == eid for p in article.analysis['evidence'])


def test_legacy_proof_and_idempotent_lifecycle_checkpoint(app_env):
    from agents.EnnoScholarV5.persistence import ApplicationRepository
    from experiments.ennoscholar_v5.progress import Scope
    state, ids, eids, _ = fixture()
    assert usable_proof(state, eids[0])  # old schema has no lifecycle field
    make, _, _ = app_env
    service, sid, transport = make([], state)
    scope = Scope(1, sid)
    repo = ApplicationRepository(service, scope)
    revision, saved = repo.load(scope)
    set_proof_status(saved, eids[0], 'withdrawn', reason='Offline withdrawal')
    repo.begin(scope, 'lifecycle-test', revision, 'action', {'tool': 'local_lifecycle_checkpoint'})
    assert repo.claim(scope, 'lifecycle-test')
    repo.finish(scope, 'lifecycle-test', revision, saved, {'saved': True})
    restored = ApplicationRepository(service, scope).load(scope)[1]
    assert proof_status(restored, eids[0]) == 'withdrawn'
    count = len(restored['evidence_lifecycle_history'])
    set_proof_status(restored, eids[0], 'withdrawn')
    assert len(restored['evidence_lifecycle_history']) == count
    assert not transport.requests


def test_reading_cannot_silently_reactivate_a_withdrawn_proof():
    state, ids, eids, _ = fixture()
    set_proof_status(state, eids[0], 'withdrawn')
    with pytest.raises(ValueError):
        execute(state, 'analyze_article', {**read(ids[0]).arguments, 'evidence_ids': [eids[0]], 'revisit': True})
    assert proof_status(state, eids[0]) == 'withdrawn'


def test_reuse_transfers_withdrawal_markers_without_delivery_credit():
    from .test_orchestration_reliability import prepared, receive
    state = prepared()
    receive(state)
    eid = next(iter(state['evidence']))
    aid = state['evidence'][eid]['article_id']
    set_proof_status(state, eid, 'withdrawn')
    result = reusable_science(Article.model_validate(state['articles'][aid]), state['source_snapshots'], state['evidence'])
    assert result and result[2][eid]['lifecycle_status'] == 'withdrawn'
    imported = {'evidence': result[2], 'source_snapshots': result[1], 'shown_evidence_ids': []}
    assert not usable_proof(imported, eid) and not imported['shown_evidence_ids']


def test_review_stales_when_its_proof_disappears():
    state, ids, eids, _ = fixture()
    article = Article.model_validate(state['articles'][ids[0]])
    save_review(state, article, CoverageReview(question='Methods', sufficient=True,
        dimensions=[{'dimension': 'methods', 'status': 'verified', 'scope': 'examined_passages',
            'evidence_ids': [eids[0]], 'reason': 'Source read'}]))
    assert reading_status(state, article)['question_sufficient']
    del state['evidence'][eids[0]]
    assert not reading_status(state, article)['question_sufficient']


def test_secondary_relation_proof_and_saved_gap_are_revalidated():
    state, ids, eids, args = fixture()
    extra = copy.deepcopy(state['evidence'][eids[0]])
    extra['evidence_id'] = 'secondary-proof'
    state['evidence'][extra['evidence_id']] = extra
    state['shown_evidence_ids'].append(extra['evidence_id'])
    args['relations'][0]['experimental_results'] = [{'statement': 'Source result',
        'evidence_ids': [extra['evidence_id']], 'origin': 'source_statement'}]
    assert execute(state, 'compare_articles', args)['saved']
    gap_args = {'article_ids': ids, 'research_context': 'Source limits', 'gaps': [
        {'statement': 'A source limitation', 'origin': 'author_limit', 'article_ids': [ids[0]],
            'evidence_ids': [extra['evidence_id']], 'scope': 'This source'}]}
    execute(state, 'identify_research_gaps', gap_args)
    toolkit = sc.adapter().toolkit(state)
    assert argument_packet(state, toolkit, ids)['research_gaps']
    set_proof_status(state, extra['evidence_id'], 'withdrawn')
    toolkit = sc.adapter().toolkit(state)
    assert not relation_current(toolkit, state['scientific_relations'][0])
    packet = argument_packet(state, toolkit, ids)
    assert not packet['research_gaps'] and packet['historical_gap_bundles'] == 1
    assert state['research_gaps']  # historical data remains stored


def test_proof_changes_invalidate_a_delivered_matrix():
    state = cm.request_state()
    cm.deliver(state, 293)
    args = cm.actions()[297]['request']['arguments']
    assert workflow.comparison_check(state, args)
    eid = next(eid for eid, p in state['evidence'].items() if p['article_id'] in cm.THREE)
    set_proof_status(state, eid, 'withdrawn')
    assert workflow.comparison_check(state, args) is None


def test_invalid_claims_do_not_shift_the_valid_claim_cursor():
    state, ids, eids, _ = fixture()
    aid, eid = ids[0], eids[0]
    state['articles'][aid]['analysis'] = {'method': [
        {'statement': 'Unavailable', 'evidence_ids': ['missing'], 'origin': 'source_statement'},
        *[{'statement': text, 'evidence_ids': [eid], 'origin': 'source_statement'} for text in ('A', 'B')]]}
    toolkit = sc.adapter().toolkit(state)
    first = bounded_argument_packet(argument_packet(state, toolkit, [aid]), 100000)
    cursor = first['next_claim_offsets'][aid]
    following = bounded_argument_packet(argument_packet(state, toolkit, [aid], claim_offsets={aid: cursor}), 100000)
    assert [c['statement'] for c in first['papers'][0]['analysis']['method']] == ['A', 'B']
    assert not following['papers'][0]['analysis'].get('method')
    assert first['papers'][0]['unavailable_claims'] == 1


def test_invalid_core_proof_cannot_qualify_a_fulltext_card():
    state, ids, eids, _ = fixture()
    article = Article.model_validate(state['articles'][ids[0]])
    claim = {'statement': 'Previously read', 'evidence_ids': [eids[0]], 'origin': 'source_statement'}
    article.analysis = {'method': [claim], 'results': [claim], 'limitations': [claim]}
    assert workflow.fulltext_core(article, state['evidence'], state['source_snapshots'])
    set_proof_status(state, eids[0], 'withdrawn')
    assert not workflow.fulltext_core(article, state['evidence'], state['source_snapshots'])


def test_explicit_scientific_correction_keeps_only_one_active_interpretation():
    state, ids, eids, _ = fixture()
    aid, eid = ids[0], eids[0]
    state['articles'][aid]['analysis'] = {}
    args = {**read(aid).arguments, 'action': 'save', 'analysis': payload(eid)}
    old = {'statement': 'Initial interpretation', 'evidence_ids': [eid], 'origin': 'inference'}
    args['analysis']['results'] = [old]
    assert execute(state, 'analyze_article', args)['saved']
    args['analysis']['results'] = [{**old, 'statement': 'Revised interpretation'}]
    args['replace_claims'] = [{'field': 'results', 'statement': old['statement'], 'evidence_ids': [eid]}]
    assert execute(state, 'analyze_article', args)['saved']
    analysis = state['articles'][aid]['analysis']
    assert [c['statement'] for c in analysis['results']] == ['Revised interpretation']
    assert analysis['superseded_claims'][0]['claim'] == {**old, 'basis': 'fulltext'}


def test_failed_correction_keeps_the_previous_card_and_history():
    state, ids, eids, _ = fixture()
    aid = ids[0]
    old = copy.deepcopy(state['articles'][aid]['analysis'])
    claim = old['results'][0]
    args = {**read(aid).arguments, 'action': 'save', 'analysis': payload('missing-proof'),
        'replace_claims': [{'field': 'results', 'statement': claim['statement'], 'evidence_ids': claim['evidence_ids']}]}
    with pytest.raises(ValueError):
        execute(state, 'analyze_article', args)
    assert state['articles'][aid]['analysis'] == old


@pytest.mark.parametrize('value,unit,accepted', [('14', 'h', True), ('55', 'mg', True), ('55', 'h', False),
    ('14', 'mg', False), ('5', 'mg', False)])
def test_values_and_units_must_be_bound_in_the_same_source(value, unit, accepted):
    from agents.EnnoScholarV5.evidence import passages, snapshot
    text = 'The stability duration is 14 h. The catalyst loading is 55 mg.'
    article = Article(id='binding', title='Fixture', doi='10.9999/binding', url='https://fixture.invalid/binding',
        fulltext={'text': text, 'url': 'https://fixture.invalid/binding',
            'chunks': [{'text': text, 'section': 'Results', 'page': 1}]})
    proof, source = passages(article)[0], snapshot(article)
    state = {'evidence': {proof['evidence_id']: proof}, 'source_snapshots': {source['fingerprint']: source},
        'shown_evidence_ids': [proof['evidence_id']]}
    measurement = Measurement(metric='Duration or loading', value_raw=value, unit=unit,
        experimental_conditions='Reported source conditions', measurement_method='Reported method',
        evidence_ids=[proof['evidence_id']], origin='observed_result')
    if accepted:
        assert validate_measurements(state, article, [measurement])
    else:
        with pytest.raises(ValueError):
            validate_measurements(state, article, [measurement])


def test_relation_permutations_and_negative_receipts_are_not_new_objects():
    state, ids, eids, args = fixture()
    assert execute(state, 'compare_articles', args)['saved']
    args['relations'][0]['article_ids'].reverse()
    args['relations'][0]['evidence_ids'].reverse()
    assert execute(state, 'compare_articles', args)['saved']
    assert len(state['scientific_relations']) == 1
    state['v5_comparison_decisions'] = []
    negative = {'article_ids': ids, 'relations': [], 'assessment': 'No reliable relation'}
    workflow.after_comparison(state, negative, {'saved': True})
    workflow.after_comparison(state, negative, {'saved': True})
    assert len(state['v5_comparison_decisions']) == 1


def test_n05_extension_keeps_the_unchanged_passage_id():
    # Real Stokes record: a read passage keeps its ID when pages of the same PDF are
    # added and saved (the V5 extension path); its previous entry is archived.
    from .test_graph import case, STOKES
    from agents.EnnoScholarV5.evidence import passages, snapshot
    state = copy.deepcopy(case('stokes')['state'])
    t = sc.adapter().toolkit(state)
    before = passages(t.registry[STOKES])[0]
    state['evidence'][before['evidence_id']] = before
    state['source_snapshots'][before['source_fingerprint']] = snapshot(t.registry[STOKES])
    article = t.registry[STOKES]
    article.fulltext['chunks'] = [*article.fulltext['chunks'], {'text': 'An additional methods page.', 'section': 'Methods', 'page': 29}]
    article.fulltext['text'] = '\n\n'.join(c['text'] for c in article.fulltext['chunks'])
    t.save()
    saved = Article.model_validate(state['articles'][STOKES])
    after = next(p for p in passages(saved) if p['exact_quote'] == before['exact_quote'] and p['page'] == before['page'])
    assert after['evidence_id'] == before['evidence_id']
    assert state['evidence_versions'][before['evidence_id']] == [before]
    assert exact_source_valid(state['evidence'][before['evidence_id']], state['source_snapshots'])
    assert exact_source_valid(before, state['source_snapshots'])


def test_f01_cross_article_number_swap_is_rejected():
    state = cm.request_state()
    for revision in [293, 295, 299, 303]:
        cm.deliver(state, revision)
    result = cm.compare(state, relations=[cm.relation(
        statement='Cu–SiOx reste stable pendant 14 h, YSZ/CuO pendant 55 h.')], assessment='Stability comparison')
    assert not result.get('saved')
