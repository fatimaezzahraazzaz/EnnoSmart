"""Writing scope regressions: archived state, isolated SQL, scripted Responses only."""
import copy

import pytest
from sqlalchemy import select

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.persistence import Budget, Operation
from agents.EnnoScholarV5.writing_job import wants_full_draft
from agents.EnnoScholarV5.writing_progress import begin_writing, check_write_scope
from .test_lithium_fixes import real, REQUEST
from .test_live_integration import app_env, call, run_sync, scientific_state


def request_state(wording, *, prior=True):
    state = real()
    if not prior:
        state['document'].pop('writing_progress', None)
    state['messages'].append({'id': 'scope_request', 'role': 'user', 'content': wording})
    return state


def draft_goal():
    return {'requested_article_count': None, 'deliverable': 'draft', 'lock_ids': [], 'focus_lock_ids': [],
        'exact_quote': '', 'reading_depth': 'core', 'understanding': 'Finaliser la rédaction demandée.',
        'comparison_requested': False, 'search_needed': False, 'article_ids': [], 'article_scope': 'primary',
        'references_ambiguous': False, 'constraints': None}


@pytest.mark.parametrize('prior', [True, False])
@pytest.mark.parametrize('wording,position', [
    ('Finalise la section 1', 0),
    ('Achève la première section', 0),
    ('Termine la section 1', 0),
    ('Acheve la premiere section', 0),
    ('Finalisez la deuxième section', 1),
    ('Finalise la dernière section', -1),
    ('Finalise uniquement la section 1', 0),
    ('Finalise la section section_3b4380477ac0406484741f8839a674d3', 0),
    ('Révise la section 1', 0),
])
def test_writing_scope_single_section_preserves_saved_drafts(wording, position, prior):
    state = request_state(wording, prior=prior)
    outline = copy.deepcopy(state['outline'])
    target = outline['sections'][position]
    assert not wants_full_draft(state, wording)
    progress = begin_writing(state, wording)
    assert progress['mode'] == 'section' and progress['target_ids'] == [target['id']]
    assert progress['request_message_id'] == 'scope_request'
    assert not progress.get('include_conclusion')
    assert progress['baseline_draft_ids'] == {target['id']: (target['drafts'] or [{}])[-1].get('id')}
    assert workflow.next_writing_section(state)['id'] == target['id']
    check_write_scope(state, target['id'])
    other = next(s for s in outline['sections'] if s['id'] != target['id'])
    with pytest.raises(ValueError, match='périmètre'):
        check_write_scope(state, other['id'])
    with pytest.raises(ValueError, match='plan'):
        check_write_scope(state, plan_change=True)
    assert state['outline'] == outline


@pytest.mark.parametrize('wording', ['Termine la rédaction du manuscrit', 'Finalise la rédaction du manuscrit',
    'Achève la rédaction', 'Rédige les sections restantes', REQUEST,
    'Termine la rédaction du manuscrit en préservant les sections déjà sauvegardées.',
    'Rédige un état de l’art complet, sans réécrire les sections déjà enregistrées.'])
def test_writing_scope_explicit_manuscript_keeps_saved_sections(wording):
    state = request_state(wording)
    outline = copy.deepcopy(state['outline'])
    assert wants_full_draft(state, wording)
    progress = begin_writing(state, wording)
    assert progress['mode'] == 'full'
    assert progress['target_ids'] == [s['id'] for s in outline['sections']]
    assert len(progress['saved_draft_ids']) == 2 and progress['include_conclusion']
    assert workflow.next_writing_section(state)['id'] == outline['sections'][2]['id']
    assert state['outline'] == outline


@pytest.mark.parametrize('prior', [True, False])
@pytest.mark.parametrize('wording', [
    'Ne termine pas la rédaction',
    'Finalise la section',
    'Achève cette section',
    'Finalise les sections',
    'Finalise la section 99',
    'Termine la rédaction de la section 99',
    'Achève la section précédente',
    'Finalise.',
])
def test_writing_scope_unresolved_or_negative_never_opens_global_draft(wording, prior):
    state = request_state(wording, prior=prior)
    before = copy.deepcopy(state)
    assert not wants_full_draft(state, wording)
    assert begin_writing(state, wording) is None
    assert state == before
    # Sol's fallback must not turn the same unresolved scope into all sections.
    state['turn_goal'] = {**draft_goal(), 'references': {'message_id': 'scope_request'}}
    workflow.reconcile_draft_goal(state)
    assert workflow.writing_progress(state) is None
    assert state['outline'] == before['outline']
    assert state['document'] == before['document']


def test_writing_scope_existing_section_range_stays_bounded():
    state = request_state('Finalise les sections 2 à 4')
    outline = copy.deepcopy(state['outline'])
    progress = begin_writing(state, state['messages'][-1]['content'])
    assert progress['target_ids'] == [s['id'] for s in outline['sections'][1:4]]
    assert state['outline'] == outline


@pytest.mark.parametrize('wording', ['Finalise la section 1', 'Achève la première section'])
def test_writing_scope_semantic_goal_uses_same_single_section(wording):
    state = request_state(wording, prior=False)
    state['turn_goal'] = {**draft_goal(), 'references': {'message_id': 'scope_request'}}
    workflow.reconcile_draft_goal(state)
    progress = workflow.writing_progress(state)
    assert progress['mode'] == 'section'
    assert progress['target_ids'] == [state['outline']['sections'][0]['id']]


@pytest.mark.parametrize('wording', ['Finalise la section 1', 'Achève la première section'])
def test_writing_scope_graph_saves_only_target_with_no_manuscript_mission(app_env, monkeypatch, wording):
    from .test_ready_section_drafting import real as prepared
    from .test_section_save_reserve import corrected_write
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD', '3')
    state = prepared()['start']
    # A saved section outside the wording must survive the entire graph path.
    state['outline']['sections'][1]['drafts'] = copy.deepcopy(real()['outline']['sections'][1]['drafts'])
    before = copy.deepcopy(state['outline'])
    target = before['sections'][0]['id']
    make, factory, _ = app_env
    cfg = ScholarV4Config(api_key='offline', web_search_enabled=False, writing_job_executor='thread')
    svc, sid, transport = make([call('set_turn_goal', draft_goal(), 'goal'),
        call('write_section', corrected_write(), 'save')], state, config=cfg)
    result = run_sync(svc, sid, wording)
    data = scientific_state(factory, sid)
    # The target is saved; the inaccessible review still makes the science partial.
    assert result['status'] == 'partial' and result['reason'] is None, result
    assert data['document']['writing_progress']['target_ids'] == [target]
    assert data['document']['writing_progress']['status'] == 'draft_complete'
    assert not data.get('writing_job')
    assert len(data['outline']['sections'][0]['drafts']) == 1
    assert data['outline']['sections'][1:] == before['sections'][1:]
    assert data['articles'] == state['articles']
    with factory() as db:
        ops = list(db.scalars(select(Operation).where(Operation.kind == 'action')))
        budget = db.get(Budget, result['id']).data
    writes = [o for o in ops if o.request['tool'] == 'write_section']
    assert len(writes) == 1 and writes[0].request['arguments']['section_id'] == target
    assert writes[0].response['output']['saved']
    assert budget['limits']['tokens'] == cfg.max_tokens
    assert budget['limits']['tools'] == cfg.max_tool_calls
    assert result['usage']['llm_calls'] == len(transport.requests) == 2


@pytest.mark.parametrize('wording', ['Ne termine pas la rédaction', 'Finalise la section', 'Finalise.'])
def test_writing_scope_graph_draft_goal_cannot_create_unrequested_mission(app_env, monkeypatch, wording):
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD', '3')
    state = real()
    before = copy.deepcopy(state['outline'])
    make, factory, _ = app_env
    svc, sid, _ = make([call('set_turn_goal', draft_goal(), 'goal'),
        call('pause', {'reason': 'consultant_review'}, 'pause')], state)
    run_sync(svc, sid, wording)
    data = scientific_state(factory, sid)
    assert not data.get('writing_job')
    assert workflow.writing_progress(data) is None
    assert data['outline'] == before
