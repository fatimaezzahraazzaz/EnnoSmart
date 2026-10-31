"""Regression from real run 7de2fab6 (session 21ce7ee2, lithium plating, 28/10).

Observed: decision 76 raised ValueError('Local context budget…') before any
provider call. Runner.decide sized the work view with the raw control message,
but the control travels as a JSON string inside the inputs: escaping added 1,418
characters and the context reached 85,102 for a limit of 85,000. A resume would
recompute the same context. Real persisted state and checkpoint values.
"""
import copy
import gzip
import json
from unittest.mock import Mock
import pytest
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.runtime import ScientificAdapter
from experiments.ennoscholar_v5.graph import Runner, request_packet
from experiments.ennoscholar_v5.progress import model_work_view
from .test_graph import ARTIFACTS
from .test_section_save_reserve import corrected_write, SECTION
from .test_cir_workflow import last_output
from .test_direct_state_of_art import scripted
from .test_live_integration import app_env, call, run_sync, scientific_state

CONFIG = ScholarV4Config(api_key='offline', web_search_enabled=False)


def real():
    with gzip.open(ARTIFACTS/'real-7de2-run.json.gz', 'rt', encoding='utf-8') as f:
        return json.load(f)


def runner():
    r = object.__new__(Runner)  # Only the local assembly is exercised: no repository, no model.
    r.adapter = ScientificAdapter(CONFIG, Mock(), sources=Mock())
    return r


def prepared():
    data = real()
    session = copy.deepcopy(data['session'])
    session['v5_finalization_mode'] = False
    _, inputs = runner().adapter.prepare(session, data['last_output'])
    return data, session, inputs


def previous_assembly(state, session, inputs, adapter):
    """The sizing of Runner.decide before the fix (raw control length)."""
    view = adapter.workflow_view(session)
    control = {'v5_workflow': view, 'request': request_packet(session), 'current_goal': session.get('turn_goal', {}),
        'no_gain_streak': state['no_gain_streak'], 'recent_no_gain': state['recent_no_gain'][-4:],
        'control': 'Ne pas reformuler le cadrage ni relire la notice sans nouveau besoin. Choisir librement une tâche utile : '
            'pagination restante, lecture, analyse, recherche complémentaire motivée ou finalisation.'}
    room = CONFIG.max_context_chars-len(json.dumps(inputs, ensure_ascii=False))-len(json.dumps(control, ensure_ascii=False))-120
    control['v5_work_state'] = model_work_view(state['progress'], budget=min(10000, max(500, room)))
    return [*inputs, {'role': 'developer', 'content': json.dumps(control, ensure_ascii=False)}]


def test_real_7de2_decision_now_fits_the_context_as_sent():
    data, session, inputs = prepared()
    assert data['checkpoint_error'] == ["ValueError('Local context budget; scientific sources retained in the repository')"]
    before = previous_assembly(data['graph_state'], session, copy.deepcopy(inputs), runner().adapter)
    # The defect itself: the old sizing measured the control raw, while its escaping
    # as sent exceeds the 120-character margin (1,418 in the real run, 85,102 > 85,000).
    # The exact overflow is not re-asserted: run 152fc86a's fix shortened the workflow view.
    raw = before[-1]['content']
    assert len(json.dumps(raw, ensure_ascii=False))-len(raw) > 120
    sent = runner().assemble(data['graph_state'], session, copy.deepcopy(inputs))
    assert len(json.dumps(sent, ensure_ascii=False)) <= CONFIG.max_context_chars
    # Only the work view yielded: memory, exchange and the section dossier are sent unchanged.
    assert sent[:-1] == inputs
    control = json.loads(sent[-1]['content'])
    assert control['v5_work_state'] and control['v5_workflow'] and control['current_goal']
    dossier = next(i['content'] for i in sent if isinstance(i.get('content'), str) and i['content'].startswith(workflow.DOSSIER_PREFIX))
    packet = json.loads(dossier[len(workflow.DOSSIER_PREFIX):])
    assert packet['section']['id'] == SECTION and packet['scientific_detail']['relations']


def test_a_context_that_cannot_fit_even_with_a_minimal_work_view_is_still_refused():
    data, session, inputs = prepared()
    inputs.append({'role': 'developer', 'content': 'x'*CONFIG.max_context_chars})
    with pytest.raises(ValueError, match='Local context budget|ne tient pas'):  # Refused, nothing dropped.
        runner().assemble(data['graph_state'], session, inputs)


def test_continue_request_from_the_real_state_saves_section_one_within_the_context(app_env):
    """« Continue la rédaction » on the persisted session: the next section is written
    from the dossier (two cards, the relation) and saved; every request fits."""
    make, factory, _ = app_env
    def write(sent):
        return call('write_section', corrected_write(), 'w1')
    def stop(sent):
        assert last_output(sent)['saved']
        return call('pause', {'reason': 'consultant_review'}, 'p')
    responses, errors = scripted([write, stop], [])
    svc, sid, tr = make(responses, real()['session'])
    result = run_sync(svc, sid, 'Continue la rédaction de l’état de l’art en suivant le plan proposé.')
    assert not errors, errors
    assert not str(result['reason']).startswith('v5_interrupted'), result
    assert all(len(json.dumps(r['input'], ensure_ascii=False)) <= CONFIG.max_context_chars for r in tr.requests)
    data = scientific_state(factory, sid)
    assert len(data['outline']['sections'][0]['drafts']) == 1
    assert data['articles'] == real()['session']['articles']  # Cards and proofs untouched.
    assert data['scientific_relations'] == real()['session']['scientific_relations']
    assert result['budget_limits'] == real()['run'].get('budget_limits', result['budget_limits'])
