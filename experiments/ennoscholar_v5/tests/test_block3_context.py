"""Context and output envelopes follow the task; scientific tools stay available."""
import json
from unittest.mock import Mock
import pytest
from agents.EnnoScholarV5.call_context import profile
from agents.EnnoScholarV5.config import ScholarV5Config
from agents.EnnoScholarV5.runtime import ScientificAdapter
from agents.EnnoScholarV5.token_estimation import estimate, local_tokenizer
from .test_live_integration import app_env, call, run_sync
from .test_plan_edit_scope import lithium, outline, ask


def test_block3_rename_sends_plan_without_full_science_or_manuscripts(app_env):
    make, _, _ = app_env;state = lithium()
    svc,sid,tr = make([call('manage_outline',outline('rename',state['outline']['sections'][2]['id'],'Limites'))],state)
    run_sync(svc,sid,'Renomme la section 3 en Limites.')
    request = tr.requests[0]
    wire = json.dumps(request,ensure_ascii=False)
    assert len(wire.encode()) < 35000 and request['max_output_tokens'] <= 900
    assert {t.get('name') for t in request['tools']} == {'manage_outline','set_turn_goal','finalize_response'}
    assert not any(t.get('type')=='web_search' for t in request['tools'])
    assert 'Mémoire scientifique' not in wire and 'Dossier scientifique' not in wire
    assert not any(d.get('raw_text','') and d['raw_text'] in wire for s in state['outline']['sections'] for d in s.get('drafts',[]))


@pytest.mark.parametrize('request_text', ['Analyse scientifiquement l’article 4.', 'Compare les protocoles des articles.',
    'Rédige la section 3.', 'Finalise la section 1.', 'Renomme la section 3 après avoir vérifié les preuves.',
    'Réorganise le plan en comparant les méthodes.'])
def test_block3_scientific_or_ambiguous_tasks_keep_scientific_guide_and_all_tools(request_text):
    state = ask(lithium(),request_text)
    adapter = ScientificAdapter(ScholarV5Config(api_key='offline',web_search_enabled=False),Mock(),sources=Mock())
    assert profile(state)['kind']=='science'
    from agents.EnnoScholarV5.toolkit import definitions
    assert adapter.tools() == definitions(True) and len(adapter.instructions)>20000
    assert {'analyze_article','get_fulltext','compare_articles','write_section'} <= {t['name'] for t in adapter.tools()}


def test_block3_greeting_does_not_load_historical_corpus_or_force_validation(app_env):
    make,_,_ = app_env
    response={'type':'message','id':'g','status':'completed','role':'assistant','content':[
        {'type':'output_text','text':'Bonjour, que souhaitez-vous étudier ?','annotations':[]}]}
    svc,sid,tr=make([response],lithium())
    result=run_sync(svc,sid,'Bonjour !')
    assert result['status']=='completed' and len(tr.requests)==1
    assert len(json.dumps(tr.requests[0])) < 3000 and tr.requests[0]['max_output_tokens']==256


@pytest.mark.parametrize('text', ['Texte français : des méthodes et leurs résultats.',
    '∇·u = 0 ; CO₂ + H₂O → H₂CO₃ ; σ = 2.3 ± 0.4 mV',
    json.dumps({'array':[{'doi':'10.1234/x','value':2.3,'unit':'mV'}]*50})])
def test_block3_estimates_report_point_and_safe_envelope_without_network(text):
    metrics=estimate({'input':[{'role':'user','content':text}], 'tools':[], 'max_output_tokens':7000})
    assert metrics['point_input_tokens']>0
    assert metrics['admission_input_tokens']>=metrics['point_input_tokens']
    assert not metrics['billing_guarantee'] and not metrics['model_tokenizer_attested']
    assert metrics['block_bytes']['input']>=len(text.encode())


def test_block3_missing_or_corrupt_tokenizer_cache_never_downloads(tmp_path,monkeypatch):
    monkeypatch.setenv('TIKTOKEN_CACHE_DIR',str(tmp_path));local_tokenizer.cache_clear()
    assert local_tokenizer() is None
    local_tokenizer.cache_clear()
