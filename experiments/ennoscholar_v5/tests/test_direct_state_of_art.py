"""A direct « search, propose a plan and write a complete state of the art » request.

Session of run fd41608c (real articles and full texts) with every card removed:
nothing was read or deepened before. Scripted Sol over the real tools and the
Responses SDK (simulated HTTP): these tests prove which prerequisites the
workflow exposes and enforces before each section, not how the real Sol chooses.
"""
import copy
import json
from sqlalchemy import select
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.persistence import Operation
from .test_comparison_memory import real, CU_SIOX, CUBES, YSZ
from .test_cir_workflow import dossier, last_output, section_text
from .test_live_integration import app_env, call, run_sync, scientific_state

UNREACHABLE = 'paper_4fe3a3bba2e7c98f2b1a'  # Metadata only: no abstract, no text.
REQUEST = ("Recherche les articles pertinents, propose un plan et rédige un état de l'art scientifique complet sur les "
    "catalyseurs au cuivre pour la réduction du CO2 en éthylène.")
RESUME = "Continue la rédaction de l'état de l'art."


def bare_session():
    """Same session, no card, relation, reading ledger or presented proof."""
    state = copy.deepcopy(real()['before'])
    for article in state['articles'].values():
        article['analysis'], article['analysis_basis'] = {}, None
    for key in ('scientific_relations', 'comparisons', 'research_comparisons', 'v5_comparison_decisions'):
        state.pop(key, None)
    state['scientific_reading'] = {'tasks': []}
    state['shown_evidence_ids'] = []
    state.pop('outline', None)
    return state


def goal():
    return call('set_turn_goal', {'requested_article_count': None, 'deliverable': 'draft', 'lock_ids': [], 'focus_lock_ids': [],
        'exact_quote': '', 'reading_depth': 'deep', 'understanding': "Rechercher, proposer un plan puis rédiger l'état de l'art.",
        'comparison_requested': None, 'search_needed': False, 'article_ids': [CU_SIOX, YSZ, CUBES, UNREACHABLE],
        'article_scope': 'exclusive', 'references_ambiguous': False, 'constraints': None, 'research_need': None}, 'goal')


def search():
    return call('search_collected_articles', {'query': 'cuivre éthylène interface cationique reconstruction', 'limit': 8,
        'exclude_presented': False, 'family': None}, 'search')


PLAN = [{'title': 'Interfaces et sites cationiques du cuivre', 'description': 'Stabiliser les intermédiaires du couplage C–C.',
        'article_ids': [CU_SIOX, YSZ, UNREACHABLE], 'article_roles': [{'article_id': CU_SIOX, 'role': 'interface silice'},
            {'article_id': YSZ, 'role': 'cuivre cationique en milieu acide'}, {'article_id': UNREACHABLE, 'role': 'oxydes alcalino-terreux'}]},
    {'title': 'Reconstruction dynamique des catalyseurs', 'description': 'Évolution des cubes de cuivre en fonctionnement.',
        'article_ids': [CUBES], 'article_roles': [{'article_id': CUBES, 'role': 'EC-TEM des cubes'}]}]


def outline():
    return call('manage_outline', {'action': 'create', 'title': "État de l'art : cuivre et éthylène", 'section_id': None,
        'target_section_id': None, 'position': None, 'sections': PLAN, 'article_ids': None}, 'plan')


def read(ids, tag):
    return call('analyze_article', {'article_ids': ids, 'action': 'read', 'research_context': 'Préparer la section du plan.',
        'passage_query': 'method results stability limitations', 'passage_offset': 0, 'passages_per_article': 4,
        'analysis': None}, tag)


def passages_of(sent, tag):
    out = next(json.loads(i['output']) for i in sent['input'] if i.get('call_id') == tag and i.get('type') == 'function_call_output')
    return {row['article']['id']: [p['evidence_id'] for p in row['passages']] for row in out['reading']}


def card(eids):
    """Methods, results and limits on the exact passages transmitted (no invented value)."""
    claim = lambda text, eid: [{'statement': text, 'evidence_ids': [eid], 'origin': 'source_statement'}]
    fields = ('problem', 'objective', 'contribution', 'hypotheses', 'generalization', 'open_questions', 'relevance',
        'technical_mechanism', 'experimental_setup')
    return {**{k: [] for k in fields}, 'method': claim('Approche expérimentale décrite dans ce passage.', eids[0]),
        'results': claim('Observation rapportée par les auteurs dans ce passage.', eids[1]),
        'limitations': claim('Limite ou condition de validité indiquée dans ce passage.', eids[2 % len(eids)]),
        'method_family': 'Électrocatalyse au cuivre', 'analysis_limit': 'Lecture ciblée de quatre passages.'}


def save(aid, eids, tag):
    return call('analyze_article', {'article_ids': [aid], 'action': 'save', 'research_context': 'Fiche de la section.',
        'passage_query': '', 'passage_offset': 0, 'passages_per_article': 4, 'analysis': card(eids)}, tag)


def write(packet, tag):
    text, evidence, cited = section_text(packet)
    return call('write_section', {'section_id': packet['section']['id'], 'action': 'save', 'instructions': 'Section du plan.',
        'text': text, 'article_ids': cited, 'evidence_ids': evidence}, tag)


def scripted(choices, seen=None):
    """Responses produced by Sol's scripted steps; a failed expectation is reported, not hidden
    behind the transport error it causes."""
    errors = []
    def wrap(choice):
        def respond(sent):
            if seen is not None:
                seen.append(sent)
            try:
                return choice(sent) if callable(choice) else choice
            except Exception as exc:
                errors.append(f"step {len(seen or [])}: {exc!r}")
                raise
        return respond
    return [wrap(c) for c in choices], errors


def test_direct_request_runs_the_scientific_chain_before_each_section(app_env):
    make, factory, _ = app_env
    seen, read_ids = [], {}
    def plan_after_refusal(sent):
        refusal = last_output(sent)
        assert refusal['rejection_code'] == 'plan_not_saved'  # No publication without the requested plan.
        return outline()
    def read_section_one(sent):
        readiness = last_output(sent)['source_readiness']
        actions = {a.get('article_id') for s in readiness for a in s['preparation']['actions']}
        assert {CU_SIOX, YSZ, CUBES} <= actions and UNREACHABLE not in actions  # Readable works only.
        prep = dossier(sent)['scientific_preparation']
        assert prep['sources'][UNREACHABLE]['state'] == 'unavailable' and not prep['ready']
        return read([CU_SIOX, YSZ], 'r1')
    def save_cards(sent):
        read_ids.update(passages_of(sent, 'r1'))
        return [save(CU_SIOX, read_ids[CU_SIOX], 's1'), save(YSZ, read_ids[YSZ], 's2')]
    def compare(sent):
        prep = dossier(sent)['scientific_preparation']
        assert prep['sources'][CU_SIOX]['state'] == prep['sources'][YSZ]['state'] == 'ready'  # Cards now reused.
        assert [a['action'] for a in prep['actions']] == ['compare_articles (relations ou relations=[] avec assessment)']
        relation = {'article_ids': [CU_SIOX, YSZ], 'kind': 'alternative', 'origin': 'comparative_inference',
            'statement': "Deux stratégies d'interface distinctes, évaluées dans des cellules et électrolytes différents.",
            'conditions': 'Protocoles non comparables.', 'experimental_results': [],
            'evidence_ids': [read_ids[CU_SIOX][1], read_ids[YSZ][1]], 'comparable_protocols': False,
            'uncertainties': ['Aucun classement entre les deux catalyseurs.']}
        return call('compare_articles', {'article_ids': [CU_SIOX, YSZ], 'research_context': 'Section 1', 'conclusions': None,
            'relations': [relation], 'claim_offsets': [], 'assessment': 'Approches alternatives non classées.'}, 'cmp')
    def write_one(sent):
        packet = dossier(sent)
        assert packet['scientific_preparation']['ready'] and packet['scientific_detail']['relations']
        return write(packet, 'w1')
    def report(sent):
        assert last_output(sent)['rejection_code'] == 'plan_not_approved'  # Prepared, not drafted.
        return call('finalize_response', {'answer': 'Plan proposé et sources de la section 1 préparées.', 'article_ids': [],
            'evidence_ids': [], 'coverage_sufficient': False, 'remaining_uncertainties': ['Plan à valider'],
            'displayed_article_ids': None}, 'report')
    def write_one_approved(sent):
        packet = dossier(sent)
        assert packet['section']['id'] == data_before['outline']['sections'][0]['id']
        return write(packet, 'w1b')
    def write_two_too_early(sent):
        packet = dossier(sent)
        assert packet['section']['title'].startswith('Reconstruction')
        return write(packet, 'w2')  # Without reading its source first.
    def read_section_two(sent):
        refusal = last_output(sent)
        assert refusal['rejection_code'] == 'section_preparation_required'
        assert [a['article_id'] for a in refusal['preparation']['actions']] == [CUBES]
        return read([CUBES], 'r2')
    def save_cubes(sent):
        read_ids.update(passages_of(sent, 'r2'))
        return save(CUBES, read_ids[CUBES], 's3')
    def write_two(sent):
        return write(dossier(sent), 'w3')
    first_request = [[goal(), search()], call('finalize_response', {'answer': 'Plan et état de l’art.', 'article_ids': [],
        'evidence_ids': [], 'coverage_sufficient': False, 'remaining_uncertainties': [], 'displayed_article_ids': None}, 'early'),
        plan_after_refusal, read_section_one, save_cards, compare, write_one]
    second_request = [write_one_approved, write_two_too_early, read_section_two, save_cubes, write_two]
    responses, errors = scripted([*first_request, *second_request], seen)
    svc, sid, tr = make(responses, bare_session())
    planned = run_sync(svc, sid, REQUEST)
    assert not errors, errors
    # First request: plan proposed and section 1 prepared, nothing drafted before the approval.
    data_before = scientific_state(factory, sid)
    assert len(tr.requests) == len(first_request) and not any(s.get('drafts') for s in data_before['outline']['sections'])
    assert data_before['scientific_relations'] and 'Plan à valider' in planned['answer']
    from agents.EnnoScholarV4.manuscript import DocumentAction
    svc.document_action(1, sid, DocumentAction(action='approve_outline', expected_version=data_before['outline'].get('version', 0)))
    result = run_sync(svc, sid, RESUME)
    assert not errors, errors
    assert result['reason'] is None and result['writing_progress']['status'] == 'draft_complete', result
    assert len(tr.requests) == len(first_request)+len(second_request)
    data = scientific_state(factory, sid)
    # Cards built from the full texts, with methods, results and limits on exact passages.
    for aid in (CU_SIOX, YSZ, CUBES):
        assert data['articles'][aid]['analysis_basis'] == 'fulltext'
        assert all(data['articles'][aid]['analysis'][f] for f in ('method', 'results', 'limitations'))
    assert [r['kind'] for r in data['scientific_relations']] == ['alternative']
    # The unreachable work is stated with its section, never given a result.
    s1, s2 = data['outline']['sections']
    assert [row['article_id'] for row in data['v5_section_limits'][s1['id']]] == [UNREACHABLE]
    assert UNREACHABLE not in s1['drafts'][-1]['raw_text'] and CU_SIOX in s1['drafts'][-1]['raw_text']
    assert not any(data['v5_section_gaps'].values())
    # Preparation was asked once (section 2), never twice; the plan awaits the consultant.
    with factory() as db:
        ops = list(db.scalars(select(Operation).where(Operation.kind == 'action').order_by(Operation.revision)))
    refused = [o for o in ops if o.response['output'].get('rejection_code') == 'section_preparation_required']
    assert [o.request['arguments']['section_id'] for o in refused] == [s2['id']]
    assert data['outline'].get('approval') and not data['document']['writing_progress']['approval_required']
    # The metadata-only work is stated as it is: no full text obtained, nothing attributed.
    assert 'notice seule, texte intégral non obtenu ; aucun résultat attribué' in result['answer']
    assert 'texte disponible' not in result['answer']
    assert result['budget_limits'] == real()['run']['budget_limits']


def test_a_large_corpus_is_prepared_over_resumed_requests_within_its_budget(app_env):
    """A tool budget ends the first request after section 1; the next request resumes
    section 2 and reuses the cards saved before (no re-reading, nothing lost)."""
    make, factory, _ = app_env
    read_ids = {}
    def save_cards(sent):
        read_ids.update(passages_of(sent, 'r1'))
        return [save(CU_SIOX, read_ids[CU_SIOX], 's1'), save(YSZ, read_ids[YSZ], 's2')]
    def no_relation(sent):
        return call('compare_articles', {'article_ids': [CU_SIOX, YSZ], 'research_context': 'Section 1', 'conclusions': None,
            'relations': [], 'claim_offsets': [], 'assessment': 'Protocoles trop différents pour une relation fiable.'}, 'cmp')
    def write_one(sent):
        return write(dossier(sent), 'w1')
    def resumed(sent):
        prep = dossier(sent)['scientific_preparation']
        assert prep['section_id'] == second and [a['article_id'] for a in prep['actions']] == [CUBES]
        return read([CUBES], 'r2')
    def save_cubes(sent):
        read_ids.update(passages_of(sent, 'r2'))
        return save(CUBES, read_ids[CUBES], 's3')
    def write_two(sent):
        return write(dossier(sent), 'w2')
    config = ScholarV4Config(api_key='offline', web_search_enabled=False, max_tool_calls=7)
    # First request: plan and preparation; the save waits for the approval and the tool budget ends.
    responses, errors = scripted([[goal(), outline()], lambda s: read([CU_SIOX, YSZ], 'r1'), save_cards, no_relation,
        write_one, write_one, resumed, save_cubes, write_two], [])
    svc, sid, tr = make(responses, bare_session(), config=config)
    first = run_sync(svc, sid, REQUEST)
    assert not errors, errors
    data = scientific_state(factory, sid)
    second = data['outline']['sections'][1]['id']
    assert first['partial'] and first['writing_progress']['status'] != 'draft_complete', first
    assert not any(s.get('drafts') for s in data['outline']['sections'])  # Nothing before the approval.
    before = copy.deepcopy({aid: data['articles'][aid]['analysis'] for aid in (CU_SIOX, YSZ)})
    from agents.EnnoScholarV4.manuscript import DocumentAction
    svc.document_action(1, sid, DocumentAction(action='approve_outline', expected_version=data['outline'].get('version', 0)))
    result = run_sync(svc, sid, RESUME)
    assert not errors, errors
    assert result['writing_progress']['status'] == 'draft_complete', result
    data = scientific_state(factory, sid)
    assert {aid: data['articles'][aid]['analysis'] for aid in (CU_SIOX, YSZ)} == before  # Reused, not re-read.
    assert [len(s['drafts']) for s in data['outline']['sections']] == [1, 1]
    assert data['v5_comparison_decisions'][0]['no_reliable_relation']
    with factory() as db:
        reads = [o.request['arguments']['article_ids'] for o in db.scalars(select(Operation).where(Operation.kind == 'action'))
            if o.request['tool'] == 'analyze_article' and o.request['arguments'].get('action') == 'read']
    assert reads == [[CU_SIOX, YSZ], [CUBES]]
    assert first['budget_limits']['tools'] == result['budget_limits']['tools'] == 7  # Budget per request, not raised.
