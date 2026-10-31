"""Regressions from real run fd41608c (session 3445749b, three CO2 articles, 28/10).

Observed: Sol called compare_articles four times only to read the matrix (no
relations), each reworded research_context made the same 33k-character matrix
look new, nothing was saved (scientific_relations stayed empty) and the
"14 h at 400 mA cm-2" stability of YSZ/CuO, read in a passage and present in the
card, never reached Sol as a claim. Scripted Sol over the real state: these
tests prove what is saved, credited and kept visible, not how the real Sol
will choose.
"""
import copy
import gzip
import json
from unittest.mock import Mock
from sqlalchemy import select
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.persistence import Operation
from agents.EnnoScholarV5.runtime import ScientificAdapter
from agents.EnnoScholarV5.tools.outline import resolve_section
from experiments.ennoscholar_v5.adapter import Decision
from .test_graph import ARTIFACTS
from .test_live_integration import app_env, call, run_sync, scientific_state

CU_SIOX, CUBES, YSZ = 'paper_77167a12e30e685dd624', 'paper_58c0ee6deccbb222b0c5', 'paper_96ff11926e02db7e1b35'
THREE = [CU_SIOX, CUBES, YSZ]
EV_14H = 'ev_44664a4dd6cb29c2f1e55d2f'  # YSZ/CuO: no change over 14 h at 400 mA cm-2.
EV_55H = 'ev_2f1ae3e72f881746f77de43d'  # Cu-SiOx: FE > 60 % for 55 h.


def real():
    with gzip.open(ARTIFACTS/'real-fd41-run.json.gz', 'rt', encoding='utf-8') as f:
        return json.load(f)


def actions():
    return {o['revision']: o for o in real()['operations'] if o['kind'] == 'action'}


def decisions():
    return [o['response']['decision'] for o in real()['operations'] if o['kind'] == 'decision']


def request_state():
    state = copy.deepcopy(real()['before'])
    state['messages'].append({'id': real()['run']['request_message_id'], 'role': 'user', 'content': real()['run']['request']})
    goal = actions()[291]['request']['arguments']
    state['turn_goal'] = {**state['turn_goal'], 'deliverable': 'comparison', 'article_ids': THREE,
        'references': {**state['turn_goal']['references'], 'article_ids': THREE}, 'understanding': goal['understanding']}
    state['current_turn_article_ids'] = THREE
    state['v5_finalization_mode'] = False
    state['v5_request_knowledge'] = {'request_id': real()['run']['request_message_id'], 'keys': [], 'failed_sources': [], 'cards': {}}
    return state


def deliver(state, revision):
    """Sol received this real result (acknowledged as Responses does at the next decision)."""
    op = actions()[revision]
    workflow.acknowledge_inputs(state, [{'type': 'function_call', 'call_id': f'c{revision}', 'name': op['request']['tool'],
        'arguments': json.dumps(op['request']['arguments'])}, {'type': 'function_call_output', 'call_id': f'c{revision}',
        'output': json.dumps(op['response']['output'], ensure_ascii=False)}])


def adapter():
    return ScientificAdapter(ScholarV4Config(api_key='offline', web_search_enabled=False), Mock(), sources=Mock())


def relation(**overrides):
    """A cautious relation built from the real exact quotes of both works."""
    row = {'article_ids': [CU_SIOX, YSZ], 'kind': 'alternative', 'origin': 'comparative_inference',
        'statement': "Stabilités non directement comparables : FE éthylène supérieure à 60 % pendant 55 h pour Cu–SiOx, "
            "absence de changement significatif pendant plus de 14 h pour YSZ/CuO.",
        'conditions': "Cu–SiOx : MEA, 10 à 100 % CO2, 125 à 300 mA cm−2 ; YSZ/CuO : électrolyte acide, 400 mA cm−2.",
        'experimental_results': [], 'evidence_ids': [EV_55H, EV_14H], 'comparable_protocols': False,
        'uncertainties': ["Cellules, électrolytes et densités de courant différents ; aucun classement de durabilité."]}
    return {**row, **overrides}


def compare(state, **arguments):
    args = {'article_ids': THREE, 'research_context': 'Comparer la stabilité dans ses conditions.', 'conclusions': None,
        'relations': None, 'assessment': '', 'claim_offsets': [], **arguments}
    return adapter().execute(state, Decision('compare_articles', args, call_id='cmp'), remaining_seconds=60)


def working(sent):
    item = next((i for i in sent['input'] if isinstance(i.get('content'), str) and i['content'].startswith(workflow.WORKING_PREFIX)), None)
    return json.loads(item['content'][len(workflow.WORKING_PREFIX):]) if item else None


# --- 2. The same matrix is not new knowledge ----------------------------------------

def test_reworded_matrix_is_not_new_and_is_answered_briefly():
    state = request_state()
    first, second = actions()[293], actions()[297]
    assert first['request']['arguments']['research_context'] != second['request']['arguments']['research_context']
    d1 = Decision('compare_articles', first['request']['arguments'])
    assert workflow.novel_delivery(state, d1, first['response']['output'])  # First matrix: new claims.
    deliver(state, 293)
    d2 = Decision('compare_articles', second['request']['arguments'])
    # The real second matrix carries the same claims under another wording.
    assert not workflow.novel_delivery(state, d2, second['response']['output'])
    # Executed again, it is answered by a short notice, not 33k characters.
    stub = compare(state, research_context=second['request']['arguments']['research_context'])
    assert stub['not_executed'] and stub['reason'] == 'matrix_already_delivered', stub
    assert len(json.dumps(stub, ensure_ascii=False)) < 3000 < len(json.dumps(second['response']['output'], ensure_ascii=False))
    assert stub['next_claim_offsets'] == first['response']['output']['packet']['next_claim_offsets']
    assert not workflow.novel_delivery(state, d2, stub)
    # The next window of claims is still new and executed.
    offsets = [{'article_id': a, 'offset': o} for a, o in stub['next_claim_offsets'].items()]
    following = compare(state, claim_offsets=offsets)
    assert not following.get('not_executed') and following.get('packet')
    assert following['save_with'] == workflow.SAVE_RELATIONS


def test_a_saved_card_change_makes_the_matrix_readable_again():
    state = request_state()
    deliver(state, 293)
    args = actions()[297]['request']['arguments']
    assert workflow.comparison_check(state, args)['reason'] == 'matrix_already_delivered'
    state['articles'][CUBES]['analysis']['limitations'].append({'statement': 'Nouvelle limite.', 'evidence_ids': []})
    assert workflow.comparison_check(state, args) is None


# --- 3. Read values stay in the working memory -------------------------------------

def test_14h_stability_read_in_a_passage_stays_in_working_memory():
    state = request_state()
    for revision in (293, 295, 299, 303):  # The real matrix and the real reads.
        deliver(state, revision)
    view = workflow.working_memory(state)
    assert len(json.dumps(view, ensure_ascii=False)) <= workflow.WORKING_MEMORY_CHARS
    row = next(r for r in view['articles'] if r['article_id'] == YSZ)
    shown = [c for c in row['consulted'] if EV_14H in c['evidence_ids']]
    assert any('14 h' in c['statement'] and '400 mA' in c['statement'] for c in shown), row
    # The card's caution against comparing it with longer tests at other currents is kept too.
    assert any('ne démontre pas' in c['statement'] for c in shown)
    # The 55 h test of Cu-SiOx was never transmitted in this request: it is not
    # claimed as consulted. A targeted claim read (not the matrix) brings it,
    # is credited as new, then stays in the working memory.
    cu = next(r for r in view['articles'] if r['article_id'] == CU_SIOX)
    assert not any('55 h' in c['statement'] for c in cu['consulted'])
    args = {'article_id': CU_SIOX, 'fields': ['results'], 'offset': 0}
    claims = adapter().execute(state, Decision('get_analysis_claims', args, call_id='cl'), remaining_seconds=60)
    assert any('55 h' in c['statement'] for c in claims['claims'])
    assert workflow.novel_delivery(state, Decision('get_analysis_claims', args), claims)
    workflow.acknowledge_inputs(state, [{'type': 'function_call', 'call_id': 'cl', 'name': 'get_analysis_claims',
        'arguments': json.dumps(args)}, {'type': 'function_call_output', 'call_id': 'cl', 'output': json.dumps(claims, ensure_ascii=False)}])
    cu = next(r for r in workflow.working_memory(state)['articles'] if r['article_id'] == CU_SIOX)
    assert any('55 h' in c['statement'] and EV_55H in c['evidence_ids'] for c in cu['consulted'])
    # Visible in the next request sent to Sol, within the context budget.
    ad = adapter()
    _, prepared = ad.prepare(state)
    text = next(i['content'] for i in prepared if isinstance(i.get('content'), str) and i['content'].startswith(workflow.WORKING_PREFIX))
    assert '14 h' in text and EV_14H in text
    assert len(json.dumps(prepared, ensure_ascii=False)) + workflow.CONTROL_RESERVE <= ad.config.max_context_chars


# --- 1, 5. Relations saved, validated, honest when protocols differ ---------------

def test_comparison_cannot_be_published_without_saved_relations_or_explicit_assessment():
    state = request_state()
    deliver(state, 293)
    final = actions()[309]['request']['arguments']
    refused = adapter().execute(state, Decision('finalize_response', final, call_id='f'), remaining_seconds=60)
    assert refused['rejection_code'] == 'comparison_not_saved' and not refused['published']
    assert 'relations=[]' in refused['error']
    # "No reliable relation" is accepted only with its reasons.
    assert compare(state, relations=[])['rejection_code'] == 'comparison_assessment_required'
    saved = compare(state, relations=[], assessment="Protocoles et densités de courant trop différents pour une relation fiable.")
    assert saved['saved'] and 'ne pas classer' in saved['guidance']
    assert state['v5_comparison_decisions'][-1]['no_reliable_relation'] and not state.get('scientific_relations')
    assert not workflow.comparison_required(state)


def test_incompatible_superiority_and_unsupported_numbers_are_refused():
    state = request_state()
    deliver(state, 293)
    superior = relation(kind='conditional_superiority', statement="YSZ/CuO est plus stable que Cu–SiOx.")
    out = compare(state, relations=[superior], assessment='Classement de stabilité.')
    assert 'protocoles comparables' in out['error']
    invented = relation(statement="Cu–SiOx reste stable 120 h, YSZ/CuO 14 h.")
    assert 'numérique' in compare(state, relations=[invented], assessment='x')['error']
    assert not state.get('scientific_relations') and workflow.comparison_required(state)


def test_justified_relation_is_saved_and_retrieved_next_request_and_in_section_dossier():
    state = request_state()
    deliver(state, 293)
    out = compare(state, relations=[relation()], conclusions=None,
        assessment="Approches complémentaires ; durabilités rapportées dans des conditions non comparables.")
    assert out['saved'] and len(out['scientific_relations']) == 1, out
    saved = state['scientific_relations'][-1]
    assert saved['kind'] == 'alternative' and saved['comparable_protocols'] is False and not saved['stale']
    assert not workflow.comparison_required(state)
    # A new request: the working memory shows the saved relation before any read.
    state['messages'].append({'id': 'next_request', 'role': 'user', 'content': 'Rédige la section sur la stabilité.'})
    state['v5_request_knowledge'] = {'request_id': 'next_request', 'keys': [], 'failed_sources': [], 'cards': {}}
    view = workflow.working_memory(state)
    assert view['saved_relations'][0]['id'] == saved['id'] and view['saved_relations'][0]['comparable_protocols'] is False
    # A plan section on these works receives it in its dossier, with both proofs.
    state['outline'] = {'title': 'État de l’art', 'status': 'proposed', 'revision': 1, 'sections': [{'id': 'sec_stab',
        'title': 'Stabilité des catalyseurs au cuivre', 'purpose': 'Confronter les durabilités et leurs conditions.',
        'article_ids': [CU_SIOX, YSZ], 'article_roles': [{'article_id': CU_SIOX, 'role': 'evidence'},
            {'article_id': YSZ, 'role': 'evidence'}], 'subsections': []}]}
    toolkit = adapter().toolkit(state)
    packet = workflow.section_packet(state, toolkit, resolve_section(state, 'sec_stab'), 60000)
    relations = packet['scientific_detail']['relations']
    assert [r['id'] for r in relations] == [saved['id']]
    assert {EV_14H, EV_55H} <= {e['evidence_id'] for e in packet['scientific_detail']['evidence']}


# --- Graph replay of Sol's real decisions -----------------------------------------

def save_and_publish(final):
    return [call('compare_articles', {'article_ids': THREE, 'research_context': 'Bilan comparatif', 'conclusions': None,
        'relations': [relation()], 'claim_offsets': [], 'assessment': 'Approches complémentaires ; durabilités non comparables.'},
        'save'), call('finalize_response', final, 'final')]


def replay(app_env, after_real):
    """Sol's real decisions of run fd41608c, then a scripted continuation."""
    make, factory, _ = app_env
    real_steps = [d for d in decisions() if d['tool'] not in {'pause', 'finalize_response'}]
    queue = [[call(d['tool'], d['arguments'], f'd{i}')] for i, d in enumerate(real_steps)]
    seen = []
    def respond(sent):
        seen.append(sent)
        return queue.pop(0) if queue else after_real(sent, len(seen))
    svc, sid, tr = make([respond]*14, real()['before'])
    result = run_sync(svc, sid, real()['run']['request'])
    with factory() as db:
        ops = list(db.scalars(select(Operation).where(Operation.kind == 'action').order_by(Operation.revision)))
    return result, ops, scientific_state(factory, sid), seen, tr


def check_saved_comparison(result, ops, data, tr):
    assert not result.get('fallback') and result['answer'], result
    reads = [o for o in ops if o.request['tool'] == 'compare_articles' and o.request['arguments'].get('relations') is None]
    # The matrix is read once; reworded re-reads get a short notice and no credit.
    assert len([o for o in reads if not o.response['output'].get('not_executed')]) == 1
    repeated = [o for o in reads if o.response['output'].get('reason') == 'matrix_already_delivered']
    assert len(repeated) >= 3 and all(o.response['no_gain'] for o in repeated)
    assert all(len(json.dumps(o.response['output'], ensure_ascii=False)) < 3000 for o in repeated)
    assert [r['kind'] for r in data['scientific_relations']] == ['alternative']
    assert data['v5_comparison_decisions'][-1]['relation_ids'] == [data['scientific_relations'][0]['id']]
    # The analyses and proofs of the three works are untouched; budgets unchanged.
    assert all(data['articles'][a]['analysis'] == real()['before']['articles'][a]['analysis'] for a in THREE)
    assert result['budget_limits'] == real()['run']['budget_limits']


def test_real_fd41_rereads_end_in_a_forced_synthesis_that_saves_relations(app_env):
    final = actions()[309]['request']['arguments']
    repeat = actions()[305]['request']['arguments']
    def after_real(sent, n):
        if sent['tool_choice'] == 'auto':  # Sol keeps re-reading the matrix.
            return call('compare_articles', {**repeat, 'research_context': f'Relire la matrice ({n}).'}, f'again{n}')
        # Forced synthesis: the comparison can be saved before it is presented.
        assert {t['name'] for t in sent['tools']} == {'finalize_response', 'compare_articles'}
        assert sent['tool_choice'] == 'required'
        return save_and_publish(final)
    result, ops, data, seen, tr = replay(app_env, after_real)
    assert result['reason'] == 'stagnation', result
    check_saved_comparison(result, ops, data, tr)
    # Eight real decisions, two scripted re-reads without credit, one forced synthesis.
    assert len(tr.requests) == 11 and seen[-1]['tool_choice'] == 'required'


def test_real_fd41_early_publication_is_refused_until_relations_are_saved(app_env):
    final = actions()[309]['request']['arguments']
    def after_real(sent, n):
        refusal = next((json.loads(i['output']) for i in sent['input'] if i.get('call_id') == 'early'
            and i.get('type') == 'function_call_output'), None)
        if refusal is None:
            return call('finalize_response', final, 'early')  # As the real Sol did, without relations.
        assert refusal['rejection_code'] == 'comparison_not_saved' and not refusal['published']
        return save_and_publish(final)
    result, ops, data, seen, tr = replay(app_env, after_real)
    assert result['reason'] is None, result
    check_saved_comparison(result, ops, data, tr)
