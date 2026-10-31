"""One saved administrative edit or a human gate costs no further Responses call."""
import copy
import pytest
from sqlalchemy import select
from agents.EnnoScholarV5.persistence import Operation
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.runtime import ScientificAdapter
from experiments.ennoscholar_v5.adapter import Decision
from .test_live_integration import app_env, call, run_sync, scientific_state
from .test_plan_edit_scope import lithium, outline, ask, adapter
from .test_planning_block import new_section


@pytest.mark.parametrize('action,request_text', [('rename', 'Renomme uniquement la section 3 en « Limites scientifiques ».'),
    ('delete', 'Supprime la section 4 du plan.')])
def test_block3_saved_admin_edit_ends_without_llm_confirmation(app_env, action, request_text):
    make, factory, _ = app_env
    state = lithium(); section = state['outline']['sections'][2 if action == 'rename' else 3]
    args = outline(action, section['id'], title='Limites scientifiques' if action == 'rename' else None)
    svc, sid, tr = make([call('manage_outline', args)], state)
    result = run_sync(svc, sid, request_text)
    after = scientific_state(factory, sid)
    assert len(tr.requests) == 1 and result['outcome'] == 'operation_saved'
    assert result['status'] == 'completed' and not result['partial']
    assert result['citations'] == [] and 'notices' not in result['answer'].lower()
    assert all(s.get('drafts') == next(t for t in after['outline']['sections'] if t['id']==s['id']).get('drafts')
        for s in state['outline']['sections'] if s['id'] != section['id'])


def test_block3_add_then_write_stops_before_next_call_and_keeps_intention(app_env):
    make, factory, _ = app_env;state = lithium()
    svc, sid, tr = make([call('manage_outline', outline('add', sections=[new_section('Perspectives futures')]))], state)
    result = run_sync(svc, sid, "Ajoute une section Perspectives futures puis rédige-la.")
    data = scientific_state(factory, sid)
    assert len(tr.requests) == 1 and result['outcome'] == 'human_validation_required'
    assert data['v5_pending_draft']['clause'] == 'rédige-la.'
    assert not workflow.plan_approved(data) and not data['outline']['sections'][-1].get('drafts')
    assert result['partial'] and not data.get('writing_job')


def test_block3_hard_refusal_preserves_proposed_text_and_ends_batch(app_env):
    make, factory, _ = app_env;state = lithium(False)
    section = state['outline']['sections'][2]
    args = {'section_id':section['id'], 'action':'save', 'instructions':'Révision', 'text':'Proposition conservée.',
        'article_ids': [], 'evidence_ids': []}
    svc, sid, tr = make([[call('write_section', args, 'save'), call('manage_outline', outline('read'), 'extra')]], state)
    result = run_sync(svc, sid, 'Réécris la section 3.')
    data = scientific_state(factory, sid)
    assert len(tr.requests) == 1 and result['outcome'] == 'human_validation_required'
    assert data['v5_pending_sections'][section['id']]['arguments']['text'] == args['text']
    assert [s.get('drafts') for s in data['outline']['sections']] == [s.get('drafts') for s in state['outline']['sections']]
    with factory() as db:
        tools = [o.request['tool'] for o in db.scalars(select(Operation).where(Operation.kind=='action', Operation.status=='complete'))]
    assert tools == ['write_section']


def test_block3_repairable_scientific_refusal_remains_repairable():
    state = ask(lithium(), 'Compare les travaux scientifiques de la section 3.')
    tools = adapter();t = tools.toolkit(state)
    result = tools.workflow_checks(state, t, Decision('compare_articles', {'article_ids':state['outline']['sections'][2]['article_ids'],
        'relations': [], 'conclusions': None, 'assessment': ''}))
    assert result['rejection_code'] == 'comparison_assessment_required'
    assert not result.get('published') and not result.get('outcome')
