"""Regressions from real run bcf1d7ad (session 21ce7ee2, lithium plating, 28/10).

Observed: 2/5 sections saved. Section 2 rested on three abstracts (IOP and IEEE
full texts refused or unreadable), yet its preparation was "ready" and the
manuscript did not say so; section 1 called an inaccessible review (three HTTP
403) « texte disponible, non analysé »; titles were doubled. A "ready" section
meant "nothing more can be done", not "scientifically documented".

Real persisted state (sections 1 and 2 as saved) and real CO2 full texts
(run fd41608c) for the enrichment case; scripted calls to the real tools. These
tests prove how sections are qualified, refused, saved and enriched, not how the
real Sol will write.
"""
import copy
import gzip
import json
from agents.EnnoScholarV5 import workflow
from .test_graph import ARTIFACTS
from .test_comparison_memory import adapter, real as co2, CU_SIOX, YSZ
from .test_section_save_reserve import corrected_write, run_with_used, SECTION, REVIEW
from .test_direct_state_of_art import scripted
from .test_live_integration import app_env, call, scientific_state
from experiments.ennoscholar_v5.adapter import Decision

TRIGGER, REVERSIBILITY = 'paper_a39c5c7db2f6111f9728', 'paper_c80ccacd3c3ab0ea59dd'
SECTION_2 = 'section_8ea525c40bc9417394ca34b6ee630a53'
ABSTRACTS = ['paper_b82e5821d5dc7af21d95', 'paper_2e6768211cfff02dee58', 'paper_e67897c8d44d144bf4f8']


def lithium():
    with gzip.open(ARTIFACTS/'real-bcf1-run.json.gz', 'rt', encoding='utf-8') as f:
        return json.load(f)['session']


def section(state, sid):
    return next(s for s in state['outline']['sections'] if s['id'] == sid)


def execute(state, tool, args, tag='c'):
    return adapter().execute(state, Decision(tool, args, call_id=tag), remaining_seconds=60)


# --- Qualification of the real saved sections -----------------------------------------

def test_section_on_three_abstracts_is_provisional_and_never_shown_as_full_text():
    state = lithium()
    quality = workflow.section_quality(state, section(state, SECTION_2))
    assert quality['writable'] and quality['basis'] == 'abstract_only' and quality['scientific_state'] == 'limited'
    assert set(quality['sources'].values()) == {'analyse fondée sur le résumé ; texte intégral non obtenu'}
    document = workflow.assembled_document(state)
    part = document.split('## 2. ')[1]
    assert 'Section provisoire : couverture scientifique insuffisante (3 source(s) sur 3' in part
    assert 'Analyse fondée sur le résumé (méthodes, paramètres, résultats détaillés et limites non vérifiés)' in part
    assert 'Faits documentés par le texte intégral' not in part.split('## 3.')[0]
    result = workflow.writing_result(copy.deepcopy(state))
    assert not result['coverage_sufficient'] and SECTION_2 in {r['section_id'] for r in result['provisional_sections']}
    # No full text was ever obtained: no download is proposed again (no loop).
    prep = workflow.section_preparation(state, workflow.readiness_toolkit(state), section(state, SECTION_2))
    assert not any(a.get('kind') == 'network' for a in prep['actions'])


def test_two_full_texts_and_an_inaccessible_review_are_stated_as_they_are():
    state = lithium()
    quality = workflow.section_quality(state, section(state, SECTION))
    assert quality['basis'] == 'fulltext' and quality['scientific_state'] == 'limited' and quality['writable']
    assert quality['sources'][REVIEW] == 'texte intégral non obtenu ; aucun résultat attribué'
    assert quality['sources'][TRIGGER] == quality['sources'][REVERSIBILITY] == 'texte intégral analysé'
    document = workflow.assembled_document(state)
    part = document.split('## 2. ')[0]
    assert 'texte disponible' not in document and 'texte intégral non obtenu' in part
    assert 'Faits documentés par le texte intégral' in part and 'Interprétations : 1 relation(s)' in part
    assert document.count('## 1. Comprendre') == 1  # The title Sol repeated in its text is not doubled.


def test_saved_texts_proofs_and_comparisons_are_reused_untouched():
    state = lithium()
    before = copy.deepcopy(state)
    workflow.assembled_document(state)
    workflow.writing_result(state)
    for sid in (SECTION, SECTION_2):
        workflow.section_quality(state, section(state, sid))
    assert state['outline'] == before['outline'] and state['articles'] == before['articles']
    assert state['scientific_relations'] == before['scientific_relations'] and state['evidence'] == before['evidence']
    toolkit = adapter().toolkit(copy.deepcopy(state))
    for sid in (SECTION, SECTION_2):
        packet = workflow.section_packet(state, toolkit, section(state, sid), 60000)
        relations = packet['scientific_detail']['relations']
        assert len(relations) == 1
        shown = {e['evidence_id'] for e in packet['scientific_detail']['evidence']}
        assert set(relations[0]['evidence_ids']) <= shown


# --- A local text not yet analysed --------------------------------------------------------

def test_an_unread_local_text_must_be_analysed_before_another_draft_is_saved():
    state = lithium()
    state['v5_finalization_mode'] = False  # Reset by the graph at every decision (the real run ended in a forced save).
    state['articles'][REVERSIBILITY]['analysis'], state['articles'][REVERSIBILITY]['analysis_basis'] = {}, None
    assert state['articles'][REVERSIBILITY]['fulltext'].get('text')  # Extracted, readable, not analysed.
    claim = state['articles'][TRIGGER]['analysis']['results'][0]
    args = {'section_id': SECTION, 'action': 'save', 'instructions': 'Section 1.', 'article_ids': [TRIGGER],
        'evidence_ids': claim['evidence_ids'], 'text': claim['statement']+f" [{TRIGGER}] "+" ".join(f"[{e}]" for e in claim['evidence_ids'])}
    first = execute(state, 'write_section', args)
    assert first['rejection_code'] == 'section_preparation_required'
    action = next(a for a in first['preparation']['actions'] if a.get('article_id') == REVERSIBILITY)
    assert action['kind'] == 'local' and action['reason'] == 'texte intégral disponible, non analysé'
    confirm = {**args, 'text': None, 'article_ids': [], 'evidence_ids': []}
    assert execute(state, 'write_section', confirm)['rejection_code'] == 'section_preparation_required'
    saved = execute(state, 'write_section', confirm)
    assert saved['rejection_code']=='section_preparation_required'
    quality = workflow.section_quality(state, section(state, SECTION))
    assert quality['scientific_state'] == 'limited' and quality['sources'][REVERSIBILITY] == 'texte intégral disponible, non analysé'
    assert len(section(state, SECTION)['drafts']) == 1  # No premature replacement of the saved version.


# --- Budget reserve -----------------------------------------------------------------------

def test_a_save_forced_by_the_budget_reserve_is_not_declared_complete(app_env):
    make, factory, _ = app_env
    from .test_ready_section_drafting import real as start
    def save(sent):
        assert sent['tool_choice'] == {'type': 'function', 'name': 'write_section'}
        return call('write_section', corrected_write(), 'save')
    responses, errors = scripted([save], [])
    svc, sid, tr = make(responses, start()['start'])
    result = run_with_used(svc, factory, sid, 'Continue la rédaction.', 330000)
    assert not errors, errors
    assert result['reason'] == 'section_save_reserve' and len(tr.requests) == 1, result
    assert 'Sections provisoires, couverture scientifique insuffisante' in result['answer']
    assert '1 source(s) sur 3 sans analyse complète du texte intégral' in result['answer']
    data = scientific_state(factory, sid)
    assert data['v5_section_quality'][SECTION][-1]['scientific_state'] == 'limited'
    assert result['budget_limits']['tokens'] == 400000


# --- Enrichment once a full text is obtained ------------------------------------------------

def card_from(eids, label):
    claim = lambda text, eid: [{'statement': text, 'evidence_ids': [eid], 'origin': 'source_statement'}]
    fields = ('problem', 'objective', 'contribution', 'hypotheses', 'generalization', 'open_questions', 'relevance',
        'technical_mechanism', 'experimental_setup')
    return {**{k: [] for k in fields}, 'method': claim(f'Méthode décrite ({label}).', eids[0]),
        'results': claim(f'Résultat rapporté ({label}).', eids[1 % len(eids)]),
        'limitations': claim(f'Limite indiquée ({label}).', eids[2 % len(eids)]),
        'method_family': 'Électrocatalyse au cuivre', 'analysis_limit': f'Lecture ciblée ({label}).'}


def read_ids(state, aid, query):
    """Read, then acknowledge the passages as the next Responses decision does (only
    passages actually transmitted to Sol can be saved, V4's rule)."""
    args = {'article_ids': [aid], 'action': 'read', 'research_context': 'Section.', 'passage_query': query,
        'passage_offset': 0, 'passages_per_article': 4, 'analysis': None}
    out = execute(state, 'analyze_article', args)
    ad = adapter()
    ad.delivered(ad.toolkit(state), [{'type': 'function_call', 'call_id': 'r', 'name': 'analyze_article',
        'arguments': json.dumps(args)}, {'type': 'function_call_output', 'call_id': 'r', 'output': json.dumps(out, ensure_ascii=False)}])
    return [p['evidence_id'] for row in out['reading'] for p in row['passages']]


def save_card(state, aid, eids, label):
    return execute(state, 'analyze_article', {'article_ids': [aid], 'action': 'save', 'research_context': 'Fiche.',
        'passage_query': '', 'passage_offset': 0, 'passages_per_article': 4, 'analysis': card_from(eids, label)})


def relate(state, ev_a, ev_b):
    relation = {'article_ids': [CU_SIOX, YSZ], 'kind': 'complementarity', 'origin': 'comparative_inference',
        'statement': "Deux stratégies d'interface distinctes, évaluées dans des cellules différentes.",
        'conditions': 'Protocoles non comparables.', 'experimental_results': [], 'evidence_ids': [ev_a, ev_b],
        'comparable_protocols': False, 'uncertainties': ['Aucun classement.']}
    return execute(state, 'compare_articles', {'article_ids': [CU_SIOX, YSZ], 'research_context': 'Section 1',
        'conclusions': None, 'relations': [relation], 'claim_offsets': [], 'assessment': 'Approches complémentaires.'})


def write(state, eids):
    text = " ".join(f"Apport documenté [{aid}] [{e}]." for aid, e in eids)
    return execute(state, 'write_section', {'section_id': 'sec_interfaces', 'action': 'save', 'instructions': 'Section.',
        'text': text, 'article_ids': [CU_SIOX, YSZ], 'evidence_ids': [e for _, e in eids]})


def test_a_provisional_section_is_enriched_when_a_full_text_pdf_is_added():
    state = copy.deepcopy(co2()['before'])
    state['messages'].append({'id': 'enrich_request', 'role': 'user', 'content': 'Approfondis la section 1.'})
    pdf = copy.deepcopy(state['articles'][YSZ]['fulltext'])
    ysz = state['articles'][YSZ]
    ysz.update(analysis={}, analysis_basis=None, access_level='abstract_only',
        fulltext={'attempts': [{'stage': 'download', 'url': 'https://example.org/paywall', 'http_status': 403, 'error': 'HTTPStatusError'}]})
    state['v5_finalization_mode'] = False
    # The consultant approved this plan: sections may be saved.
    state['outline'] = {'title': 'État de l’art', 'status': 'approved', 'version': 1, 'plan_revision': 1,
        'approval': {'plan_revision': 1, 'owner_id': 1, 'origin': 'authenticated_human'}, 'sections': [{'id': 'sec_interfaces',
        'title': '1. Interfaces oxyde–cuivre', 'description': 'Interfaces.', 'article_ids': [CU_SIOX, YSZ],
        'article_roles': [{'article_id': CU_SIOX, 'role': 'silice'}, {'article_id': YSZ, 'role': 'YSZ'}]}]}
    sec = lambda: state['outline']['sections'][0]
    # Version 1: the YSZ card can only rest on its abstract.
    abstract = read_ids(state, YSZ, 'abstract')
    assert save_card(state, YSZ, abstract, 'résumé')['analysis_basis'] == 'abstract'
    cu = state['articles'][CU_SIOX]['analysis']['results'][0]['evidence_ids'][0]
    assert relate(state, cu, abstract[0])['saved']
    assert write(state, [(CU_SIOX, cu), (YSZ, abstract[0])])['saved']
    v1 = workflow.section_quality(state, sec())
    assert v1['basis'] == 'mixed' and v1['scientific_state'] == 'limited'
    assert v1['sources'][YSZ] == 'analyse fondée sur le résumé ; texte intégral non obtenu'
    # The consultant adds the PDF: deepening becomes a possible local action.
    state['articles'][YSZ].update(fulltext=pdf, access_level='fulltext')  # Saves replace the article's dict.
    prep = workflow.section_preparation(state, workflow.readiness_toolkit(state), sec())
    assert [a['article_id'] for a in prep['actions'] if a.get('kind') == 'local' and a.get('article_id')] == [YSZ]
    assert workflow.section_quality(state, sec())['sources'][YSZ].endswith('texte intégral disponible, non encore analysé')
    refused = write(state, [(CU_SIOX, cu), (YSZ, abstract[0])])
    assert refused['rejection_code'] == 'section_preparation_required'
    # Version 2: the full text is read, the card enriched, the comparison renewed.
    full = read_ids(state, YSZ, 'stability ethylene Faradaic efficiency current density limitations')
    # V4 keeps « abstract » on a card holding any abstract proof; V5 sees each core
    # dimension now backed by the full text.
    assert save_card(state, YSZ, full, 'texte intégral')['analysis_basis'] == 'abstract'
    assert relate(state, cu, full[0])['saved']
    assert write(state, [(CU_SIOX, cu), (YSZ, full[0])])['saved']
    v2 = workflow.section_quality(state, sec())
    assert v2['basis'] == 'fulltext' and v2['sources'][YSZ].startswith('texte intégral analysé')
    drafts = sec()['drafts']
    assert len(drafts) == 2 and abstract[0] in drafts[0]['evidence_ids'] and full[0] in drafts[1]['evidence_ids']
    assert [q['basis'] for q in state['v5_section_quality']['sec_interfaces']] == ['mixed', 'fulltext']
    assert set(abstract) <= {e['evidence_id'] for e in state['articles'][YSZ]['analysis']['evidence']}  # Abstract proofs kept.


# --- No section before the plan's approval ------------------------------------------------

def test_sections_are_never_saved_before_the_plan_approval_but_preparation_continues():
    from agents.EnnoScholarV4.manuscript import approve_outline
    state = lithium()
    state['outline'].pop('approval', None)
    state['outline']['status'] = 'proposed'
    claims = [(aid, state['articles'][aid]['analysis']['results'][0]) for aid in (TRIGGER, REVERSIBILITY)]
    args = {'section_id': SECTION, 'action': 'save', 'instructions': 'Section 1.', 'article_ids': [TRIGGER, REVERSIBILITY],
        'evidence_ids': [e for _, c in claims for e in c['evidence_ids']],
        'text': " ".join(c['statement']+f" [{aid}] "+" ".join(f"[{e}]" for e in c['evidence_ids']) for aid, c in claims)}
    drafts = copy.deepcopy(section(state, SECTION)['drafts'])
    for finalization in (False, True):  # Ordinary decision, then a save forced by the budget reserve.
        state['v5_finalization_mode'] = finalization
        refused = execute(state, 'write_section', dict(args))
        assert refused['rejection_code'] == 'plan_not_approved' and not refused['saved']
        assert section(state, SECTION)['drafts'] == drafts
    assert not workflow.save_ready(state)
    # Preparation stays allowed: a comparison decision is recorded.
    out = execute(state, 'compare_articles', {'article_ids': [TRIGGER, REVERSIBILITY], 'research_context': 'Section 1',
        'conclusions': None, 'relations': [], 'claim_offsets': [], 'assessment': 'Bilan en attente du plan.'})
    assert out['saved']
    approve_outline(state, 1)
    state['v5_finalization_mode'] = False
    assert execute(state, 'write_section', dict(args))['saved']  # The same text, once the plan is approved.
