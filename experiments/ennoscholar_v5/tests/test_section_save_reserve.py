"""Regressions from real run b748605d (session 21ce7ee2, lithium plating, 28/10).

Observed: section 1 was prepared (two full-text cards, one relation), then
write_section declared and cited [paper_386c…], an inaccessible review without a
card; V4 refused the whole section. The next call needed 65,322 tokens with
61,481 left: admission_refused, 0/5 sections. Scripted Sol over the real state
and Responses SDK (simulated HTTP): these tests prove what is refused, saved,
reserved and resumed, not how the real Sol will write.
"""
import copy
import gzip
import json
import re
from sqlalchemy import select
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.persistence import Budget, Operation, RunMeter
from experiments.ennoscholar_v5.progress import Scope
from .test_graph import ARTIFACTS
from .test_comparison_memory import adapter
from .test_cir_workflow import dossier, last_output
from .test_direct_state_of_art import scripted
from .test_live_integration import app_env, call, run_sync, scientific_state
from experiments.ennoscholar_v5.adapter import Decision

TRIGGER, REVERSIBILITY = 'paper_a39c5c7db2f6111f9728', 'paper_c80ccacd3c3ab0ea59dd'
REVIEW = 'paper_386c11c8a870988ec5e3'  # Inaccessible review (two HTTP 403), abstract only, no card.
SECTION = 'section_3b4380477ac0406484741f8839a674d3'
RESUME = 'Continue la rédaction.'


def real():
    """Run b748605d's states, with the plan approval the consultant gave before the next
    request (session 21ce7ee2): sections are only saved on an approved plan."""
    from agents.EnnoScholarV4.manuscript import approve_outline
    with gzip.open(ARTIFACTS/'real-b748-run.json.gz', 'rt', encoding='utf-8') as f:
        data = json.load(f)
    for key in ('before', 'after'):
        approve_outline(data[key], 1)
    return data


def op(revision):
    return next(o for o in real()['operations'] if o['revision'] == revision)


def real_write():
    """Sol's real section 1 (revision 61): it cites the review in a limit paragraph."""
    return copy.deepcopy(op(62)['request']['arguments'])


def corrected_write():
    """The same section without the [paper_386c…] limit paragraph; article_ids unchanged."""
    args = real_write()
    args['text'] = "\n\n".join(p for p in re.split(r"\n\s*\n", args['text']) if REVIEW not in p)
    return args


def execute(state, args):
    return adapter().execute(state, Decision('write_section', args, call_id='w'), remaining_seconds=60)


def run_with_used(svc, factory, sid, message, used):
    """A request whose budget already holds `used` tokens (as run b748605d at its 10th call)."""
    rid = svc.open_run(1, sid, message)
    RunMeter(svc, Scope(1, sid), rid, svc.config)  # Creates the run's budget row.
    with factory() as db:
        row = db.get(Budget, rid)
        data = copy.deepcopy(row.data)
        data['entries'] = [{'id': 'earlier_calls', 'status': 'settled', 'cost_usd': .33, 'input_tokens': used,
            'output_tokens': 0, 'reserved_usd': .33, 'reserved_tokens': used}]
        row.data = data
        db.commit()
    svc.slots.acquire()
    svc.execute(1, sid, rid)
    return svc.run(1, rid)


def write_ops(factory):
    with factory() as db:
        return [o for o in db.scalars(select(Operation).where(Operation.kind == 'action').order_by(Operation.revision))
            if o.request['tool'] == 'write_section']


# --- 1. Publications without a card ------------------------------------------------

def test_citing_the_inaccessible_review_as_analysed_is_refused_precisely():
    state = copy.deepcopy(real()['after'])  # Two analysed works, the review without a card.
    out = execute(state, real_write())
    assert out['rejection_code'] == 'section_source_not_analysed' and not out['saved'], out
    assert [s['article_id'] for s in out['sources']] == [REVIEW]
    assert f'retirer le marqueur [{REVIEW}]' in out['error'] and 'limite documentaire' in out['error']
    section = next(s for s in state['outline']['sections'] if s['id'] == SECTION)
    assert not section.get('drafts')  # Nothing published, nothing silently removed.
    pending = state['v5_pending_sections'][SECTION]
    assert pending['arguments']['text'] == real_write()['text'] and pending['refusal'] == 'section_source_not_analysed'


def test_section_is_saved_with_the_review_as_a_stated_limit_not_a_source():
    state = copy.deepcopy(real()['after'])
    args = corrected_write()
    assert REVIEW in args['article_ids']  # Still declared, but no longer cited.
    out = execute(state, args)
    assert out['saved'], out
    assert [s['article_id'] for s in out['not_declared_as_analysed']] == [REVIEW]
    assert [row['article_id'] for row in out['documentary_limits']] == [REVIEW]
    section = next(s for s in state['outline']['sections'] if s['id'] == SECTION)
    draft = section['drafts'][-1]
    # V4 validated it: only the two analysed works and their exact, presented proofs.
    assert draft['article_ids'] == [TRIGGER, REVERSIBILITY] and REVIEW not in draft['raw_text']
    assert set(draft['evidence_ids']) <= set(state['shown_evidence_ids'])
    assert REVIEW in section['article_ids']  # The plan keeps it.
    document = workflow.assembled_document(state)
    assert 'Limites documentaires de la section' in document and 'texte' in document
    assert SECTION not in state.get('v5_pending_sections', {})


# --- 2. Budget kept for the save -------------------------------------------------------

def test_the_last_call_of_the_budget_is_kept_for_saving_the_prepared_section(app_env):
    make, factory, _ = app_env
    seen = []
    def save(sent):
        assert sent['tool_choice'] == {'type': 'function', 'name': 'write_section'}
        assert [t['name'] for t in sent['tools']] == ['write_section']
        assert dossier(sent)['section']['id'] == SECTION and dossier(sent)['scientific_preparation']['ready']
        return call('write_section', corrected_write(), 'save')
    responses, errors = scripted([save], seen)
    svc, sid, tr = make(responses, real()['after'])
    result = run_with_used(svc, factory, sid, RESUME, 330000)
    assert not errors, errors
    # The first call would have left less than one call for the save: it became the save.
    assert len(tr.requests) == 1 and result['reason'] == 'section_save_reserve', result
    assert 'Brouillon partiel enregistré : 1/5' in result['answer'] and 'non encore validé' not in result['answer']  # Plan approved.
    data = scientific_state(factory, sid)
    assert data['outline']['sections'][0]['drafts'] and data['outline'].get('approval')
    assert result['budget_limits'] == real()['run']['budget_limits']  # 400,000 tokens: unchanged.
    with factory() as db:
        budget = db.get(Budget, result['id']).data
    used = sum(e.get('input_tokens', 0)+e.get('output_tokens', 0) for e in budget['entries'] if e['status'] == 'settled')
    assert used <= budget['limits']['tokens']


def test_a_refused_last_save_is_kept_provisionally_and_resumed_without_rework(app_env):
    make, factory, _ = app_env
    before = real()['after']
    def save_real(sent):
        return call('write_section', real_write(), 'save')  # Cites the review: refused.
    def repair(sent):
        packet = dossier(sent)
        draft = packet['provisional_draft']
        assert draft['refusal'] == 'section_source_not_analysed' and draft['text_chars'] == len(real_write()['text'])
        return call('write_section', corrected_write(), 'repair')
    def stop(sent):
        assert last_output(sent)['saved']
        return call('pause', {'reason': 'consultant_review'}, 'p')
    responses, errors = scripted([save_real, repair, stop], [])
    svc, sid, tr = make(responses, before)
    first = run_with_used(svc, factory, sid, RESUME, 330000)
    assert not errors, errors
    assert first['reason'] == 'section_save_reserve' and 'Brouillon partiel enregistré : 0/5' in first['answer'], first
    data = scientific_state(factory, sid)
    assert not data['outline']['sections'][0].get('drafts')  # Never published as a validated section.
    assert data['v5_pending_sections'][SECTION]['arguments']['text'] == real_write()['text']
    second = run_sync(svc, sid, RESUME)  # Next request, fresh budget of the same size.
    assert not errors, errors
    data = scientific_state(factory, sid)
    assert len(data['outline']['sections'][0]['drafts']) == 1 and SECTION not in data['v5_pending_sections']
    # Cards, proofs and comparison of the prepared section are untouched and current.
    for aid in (TRIGGER, REVERSIBILITY):
        assert data['articles'][aid]['analysis'] == before['articles'][aid]['analysis']
    assert [r['id'] for r in data['scientific_relations']] == [r['id'] for r in before['scientific_relations']]
    assert not any(r.get('stale') for r in data['scientific_relations'])
    reads = [o for o in write_ops(factory)]
    assert [o.response['output'].get('rejection_code') for o in reads] == ['section_source_not_analysed', None]
    assert second['budget_limits'] == first['budget_limits'] == real()['run']['budget_limits']


# --- 3. Sol's real decisions from the start of the run ---------------------------------

def test_real_b748_chain_prepares_compares_then_saves_section_one(app_env):
    """Revisions 39-61 replayed: fulltexts (archived), reads, cards, comparison, then the
    real section, refused for its [paper_386c…] citation, repaired and saved."""
    make, factory, _ = app_env
    steps = [o['response']['decision'] for o in real()['operations'] if o['kind'] == 'decision'
        and o['response']['decision']['tool'] != 'pause']
    def as_call(d, i):
        return [call(x['tool'], x['arguments'], f'd{i}_{j}') for j, x in enumerate([d, *d.get('calls', [])])]
    def repair(sent):
        refusal = last_output(sent)
        assert refusal['rejection_code'] == 'section_source_not_analysed', refusal
        return call('write_section', corrected_write(), 'repair')
    def stop(sent):
        assert last_output(sent)['saved'] and dossier(sent)['section']['id'] != SECTION
        return call('pause', {'reason': 'consultant_review'}, 'p')
    responses, errors = scripted([*[as_call(d, i) for i, d in enumerate(steps)], repair, stop], [])
    svc, sid, tr = make(responses, real()['before'])
    result = run_sync(svc, sid, real()['run']['request'])
    assert not errors, errors
    data = scientific_state(factory, sid)
    assert result['writing_progress']['saved_draft_ids'].keys() == {SECTION}, result
    for aid in (TRIGGER, REVERSIBILITY):
        assert data['articles'][aid]['analysis_basis'] == 'fulltext'
        assert all(data['articles'][aid]['analysis'].get(f) for f in ('method', 'results', 'limitations'))
    assert len([r for r in data['scientific_relations'] if not r.get('stale')]) == 1
    assert [row['article_id'] for row in data['v5_section_limits'][SECTION]] == [REVIEW]
    draft = data['outline']['sections'][0]['drafts'][0]
    assert REVIEW not in draft['raw_text'] and set(draft['article_ids']) == {TRIGGER, REVERSIBILITY}
    assert data['outline'].get('approval') and not data['document']['writing_progress']['approval_required']
    assert result['budget_limits'] == real()['run']['budget_limits']
