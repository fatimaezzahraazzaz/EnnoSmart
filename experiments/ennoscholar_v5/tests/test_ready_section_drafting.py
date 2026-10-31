"""Regressions from real run 152fc86a (session 21ce7ee2, lithium plating, 28/10).

Observed: section 1 was prepared (two full-text cards, one relation, plan
approved, preparation ready) and Sol never called write_section(save). In five
decisions it re-read: three write_section(read) returned the same 16,073
characters next to the injected 40,000-character dossier, with a V4 guidance
asking for more readings; they filled the exchange (20,488 characters), so the
claims it asked for were deferred and asked again. The control message proposed
"lecture, analyse, recherche… ou finalisation", the workflow view opened on the
missing analyses of the four later sections, and stagnation then stopped the
request with 0/5 sections, whereas a stalled synthesis is forced elsewhere.

These tests replay Sol's real decisions and its real section text (run
b748605d) over the real state. They prove what Sol now receives and what the
graph does when Sol stalls on a ready section, not how the real Sol will choose.
"""
import gzip
import json
from sqlalchemy import select
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.persistence import Operation
from .test_graph import ARTIFACTS
from .test_section_save_reserve import corrected_write, SECTION
from .test_cir_workflow import dossier, last_output
from .test_direct_state_of_art import scripted
from .test_live_integration import app_env, call, run_sync, scientific_state

CARDS = ('paper_a39c5c7db2f6111f9728', 'paper_c80ccacd3c3ab0ea59dd')


def real():
    with gzip.open(ARTIFACTS/'real-152f-run.json.gz', 'rt', encoding='utf-8') as f:
        return json.load(f)


def real_decisions():
    return [o['response']['decision'] for o in real()['operations'] if o['kind'] == 'decision' and o['response'].get('decision')]


def as_call(d, tag):
    return [call(x['tool'], x['arguments'], f'{tag}_{j}') for j, x in enumerate([d, *d.get('calls', [])])]


def control(sent):
    return next(json.loads(i['content']) for i in reversed(sent['input']) if i.get('role') == 'developer'
        and isinstance(i.get('content'), str) and i['content'].startswith('{"v5_workflow"'))


def actions(factory):
    with factory() as db:
        return list(db.scalars(select(Operation).where(Operation.kind == 'action').order_by(Operation.revision)))


def test_the_real_run_reread_the_dossier_and_deferred_its_own_requests():
    """What happened in reality (persisted operations), as the baseline of the replay."""
    outputs = [(o['request'].get('tool'), (o['request'].get('arguments') or {}).get('action'), o['response'].get('output') or {})
        for o in real()['operations'] if o['kind'] == 'action']
    reads = [out for tool, action, out in outputs if tool == 'write_section' and action == 'read']
    assert len(reads) == 3 and all(len(json.dumps(out, ensure_ascii=False)) > 16000 for out in reads)
    assert sum(out.get('reason') == 'deferred_transmission_budget' for _, _, out in outputs) == 3
    assert not any(tool == 'write_section' and action == 'save' for tool, action, _ in outputs)
    assert real()['run']['reason'] == 'stagnation' and 'Brouillon partiel enregistré : 0/5' in real()['run']['answer']


def test_real_152f_decisions_now_lead_to_the_save_of_the_prepared_section(app_env):
    make, factory, _ = app_env
    steps = [d for d in real_decisions() if d['tool'] != 'pause']
    queue = [as_call(d, f'd{i}') for i, d in enumerate(steps)]
    seen = []
    def respond(sent):
        if queue:
            current = control(sent)['v5_workflow']['current_section']
            assert current['section_id'] == SECTION and current['ready'] and 'write_section' in current['next_action']
            return queue.pop(0)
        if sent['tool_choice'] == {'type': 'function', 'name': 'write_section'}:
            assert dossier(sent)['section']['id'] == SECTION
            return call('write_section', corrected_write(), 'save')  # Sol's own section 1 text (run b748605d).
        if last_output(sent).get('saved'):
            assert dossier(sent)['section']['id'] != SECTION  # Drafting moved on to section 2.
            return call('pause', {'reason': 'consultant_review'}, 'p')
        return as_call(steps[-1], f'again{len(seen)}')  # Sol keeps re-reading, as it did.
    responses, errors = scripted([respond]*12, seen)
    svc, sid, tr = make(responses, real()['start'])
    result = run_sync(svc, sid, real()['run']['request'])
    assert not errors, errors
    ops = actions(factory)
    # The dossier is no longer sent twice: reads of the current section are short notices…
    reads = [o for o in ops if o.request['tool'] == 'write_section' and o.request['arguments'].get('action') == 'read']
    assert reads and all(o.response['output'].get('reason') == 'dossier_already_in_context' for o in reads)
    assert all(len(json.dumps(o.response['output'], ensure_ascii=False)) < 3000 for o in reads)
    # …so the claims Sol asked for in the same responses are delivered, not deferred.
    claims = [o for o in ops if o.request['tool'] == 'get_analysis_claims']
    assert claims and not any(o.response['output'].get('transmission_deferred') for o in claims)
    # Stalling on a ready section now leads to its save, then drafting continues.
    saves = [o for o in ops if o.request['tool'] == 'write_section' and o.request['arguments'].get('action') == 'save']
    assert [bool(o.response['output'].get('saved')) for o in saves] == [True]
    data = scientific_state(factory, sid)
    assert len(data['outline']['sections'][0]['drafts']) == 1 and data['outline'].get('approval')
    assert data['document']['writing_progress']['saved_draft_ids'].keys() == {SECTION}
    for aid in CARDS:  # Cards, proofs and the relation are reused untouched.
        assert data['articles'][aid]['analysis'] == real()['start']['articles'][aid]['analysis']
    assert data['scientific_relations'] == real()['start']['scientific_relations']
    assert result['budget_limits'] == real()['run']['budget_limits']
    assert len(tr.requests) <= 8 and result['usage']['llm_calls'] == len(tr.requests)


def test_resumed_session_with_a_prepared_section_and_approved_plan_writes_it_first(app_env):
    """« Continue la rédaction » on the real state (plan approved, 0/5 saved): Sol's
    real first response, then it follows what its context now says."""
    make, factory, _ = app_env
    first = real_decisions()[0]
    def opening(sent):
        assert control(sent)['control'].startswith('Rédaction en cours')
        return as_call(first, 'd0')
    def follow(sent):
        current = control(sent)['v5_workflow']['current_section']
        notice = next(json.loads(i['output']) for i in sent['input'] if i.get('call_id') == 'd0_1'
            and i.get('type') == 'function_call_output')
        assert notice['reason'] == 'dossier_already_in_context' and notice['preparation_ready']
        assert current['ready'] and SECTION in current['next_action']
        return call('write_section', corrected_write(), 'save')
    def stop(sent):
        assert last_output(sent)['saved']
        return call('pause', {'reason': 'consultant_review'}, 'p')
    responses, errors = scripted([opening, follow, stop], [])
    svc, sid, tr = make(responses, real()['start'])
    result = run_sync(svc, sid, 'Continue la rédaction de l’état de l’art en suivant le plan validé.')
    assert not errors, errors
    assert result['reason'] != 'stagnation' and len(tr.requests) == 3, result
    data = scientific_state(factory, sid)
    assert len(data['outline']['sections'][0]['drafts']) == 1
    assert [row['article_id'] for row in data['v5_section_limits'][SECTION]] == ['paper_386c11c8a870988ec5e3']
