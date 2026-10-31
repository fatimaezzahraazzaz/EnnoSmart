"""One consultant request, the whole state of the art: the V5 drafting mission.

Real lithium-plating session after run bcf1d7ad (sections 1-2 saved, plan
approved, sections 3-5 not prepared). Their nine sources only have abstracts:
their full texts are marked as refused by the publishers, as for section 2 in the
real run. Scripted Sol over the real tools, the Responses SDK (simulated HTTP),
the application database and V4's job driver (thread executor). These tests prove
the supervision (segments, budgets, recovery, quality), not how the real Sol writes.
"""
import copy
import json
import re
import pytest
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.service import ScholarV5Service
from .test_section_quality import lithium
from .test_cir_workflow import dossier, last_output, section_text
from .test_live_integration import app_env, call, run_sync, scientific_state

REQUEST = "Rédige l'état de l'art complet en suivant le plan validé."


@pytest.fixture(autouse=True)
def authorised_budget(monkeypatch):
    """The consultant's explicit authorisation of the mission's global cap (10 $ here)."""
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD', '10')
SAVED = ('section_3b4380477ac0406484741f8839a674d3', 'section_8ea525c40bc9417394ca34b6ee630a53')


def mission_state(*, approved=True):
    state = lithium()
    for section in state['outline']['sections'][2:]:
        for aid in section['article_ids']:
            article = state['articles'][aid]
            if not article.get('analysis') and not (article.get('fulltext') or {}).get('text'):
                article.setdefault('fulltext', {})['attempts'] = [{'stage': 'download', 'url': 'https://doi.org/x',
                    'http_status': 403, 'error': 'HTTPStatusError'}]
            # verify_bibliography needs the network: its successful outcome is assumed here.
            article['verified'], article['metadata_errors'] = True, []
    if not approved:
        state['outline'].pop('approval', None)
        state['outline']['status'] = 'proposed'
    return state


def config(**overrides):
    from agents.EnnoScholarV5.config import ScholarV5Config
    # Separate, explicit test limits: the existing ceiling of 48 for the whole
    # mission, and a lowered quantum of 9 per segment to exercise continuation.
    return ScholarV5Config(api_key='offline', web_search_enabled=False, writing_job_executor='thread',
        mission_max_tool_calls=48, max_tool_calls=overrides.pop('max_tool_calls', 9), **overrides)


class Sol:
    """Prepares the dossier's section from what is available (abstracts here), compares
    or records that no relation is reliable, then writes from the dossier only."""
    def __init__(self, idle=False):
        self.reads, self.saved, self.idle, self.calls, self.waited = {}, set(), idle, 0, 0

    def __call__(self, sent):
        self.calls += 1
        packet = dossier(sent) if any(isinstance(i.get('content'), str) and i['content'].startswith(workflow.DOSSIER_PREFIX)
            for i in sent['input']) else None
        if packet is None:
            return call('pause', {'reason': 'consultant_review'}, f'p{self.calls}')
        sid, tag = packet['section']['id'], f'c{self.calls}'
        if self.idle:  # Re-reads the dossier only: no scientific progress.
            return call('write_section', {'section_id': sid, 'action': 'read', 'instructions': 'Relire.', 'text': None,
                'article_ids': [], 'evidence_ids': []}, tag)
        for item in sent['input']:
            if item.get('type') == 'function_call_output' and item.get('call_id', '').startswith('read_'):
                out = json.loads(item['output'])
                for row in out.get('reading', []):
                    if row.get('passages'):
                        self.reads[row['article']['id']] = [p['evidence_id'] for p in row['passages']]
        last = last_output(sent)
        if last.get('rejection_code') == 'plan_not_approved':
            self.waited += 1  # Prepared; the plan awaits the consultant.
            return call('pause', {'reason': 'consultant_review'}, f'wait{self.calls}')
        if last.get('error') and 'présentés' in last['error']:
            self.reads.clear()  # Passages read in a previous segment were never delivered: read again.
        forced = sent['tool_choice'] == {'type': 'function', 'name': 'write_section'}
        need = [s['id'] for s in packet['sources'] if s['status'] != 'analysed']
        if not forced:
            unread = [aid for aid in need if aid not in self.reads]
            if unread:
                return call('analyze_article', {'article_ids': unread, 'action': 'read', 'research_context': 'Section.',
                    'passage_query': 'abstract', 'passage_offset': 0, 'passages_per_article': 3, 'analysis': None}, 'read_'+tag)
            for aid in need:
                eids = self.reads[aid]
                claim = lambda text, eid: [{'statement': text, 'evidence_ids': [eid], 'origin': 'source_statement'}]
                card = {**{k: [] for k in ('problem', 'objective', 'contribution', 'hypotheses', 'generalization', 'open_questions',
                    'relevance', 'technical_mechanism', 'experimental_setup')},
                    'method': claim('Approche annoncée dans le résumé.', eids[0]),
                    'results': claim('Résultat annoncé dans le résumé.', eids[1 % len(eids)]),
                    'limitations': claim('Portée indiquée dans le résumé.', eids[2 % len(eids)]),
                    'method_family': 'Batteries lithium-ion', 'analysis_limit': 'Résumé seul ; texte intégral refusé.'}
                return call('analyze_article', {'article_ids': [aid], 'action': 'save', 'research_context': 'Fiche.',
                    'passage_query': '', 'passage_offset': 0, 'passages_per_article': 3, 'analysis': card}, tag)
            if any(a['action'].startswith('compare_articles') for a in packet['scientific_preparation']['actions']):
                ids = packet['scientific_preparation']['comparison']['article_ids']
                return call('compare_articles', {'article_ids': ids, 'research_context': 'Section.', 'conclusions': None,
                    'relations': [], 'claim_offsets': [], 'assessment': 'Résumés seuls : aucune relation fiable entre ces travaux.'}, tag)
        if last.get('rejection_code') == 'section_preparation_required':
            return call('write_section', {'section_id': sid, 'action': 'save', 'instructions': 'Confirmé.', 'text': None,
                'article_ids': [], 'evidence_ids': []}, tag)
        text, evidence, cited = section_text(packet)
        return call('write_section', {'section_id': sid, 'action': 'save', 'instructions': 'Section du plan.',
            'text': text or 'Section provisoire : aucune source analysée.', 'article_ids': cited, 'evidence_ids': evidence}, tag)


def metered(sol):
    """Responses with realistic usage: input tokens from the size of what Sol actually
    receives (about 4 characters per token), so the dollar and token caps apply as in a
    real run (the offline transport otherwise reports 150 tokens per call)."""
    import httpx
    def respond(sent):
        out = sol(sent)
        output = out if isinstance(out, list) else [out]
        # The provider counts instructions and tool definitions as input, as the real ledger shows.
        tokens = len(json.dumps({k: sent.get(k) for k in ('instructions', 'tools', 'input')}, ensure_ascii=False))//4
        response = {"id": "resp_m", "object": "response", "created_at": 1, "model": "gpt-6.1-sol", "service_tier": "default",
            "status": "completed", "output": output, "usage": {"input_tokens": tokens, "output_tokens": 600,
                "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 200}}}
        event = {"type": "response.completed", "sequence_number": 1, "response": response}
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
            text="event: response.completed\ndata: "+json.dumps(event)+"\n\ndata: [DONE]\n\n")
    return respond


def start(app_env, sol, state, cfg, *, settle=True):
    make, factory, checkpoints = app_env
    svc, sid, tr = make([sol]*200, state, config=cfg)
    first = run_sync(svc, sid, REQUEST)
    if settle:
        svc.pool.shutdown(wait=True)  # The job driver's thread runs the remaining segments.
    return svc, sid, tr, first, factory


def job_of(factory, sid):
    return scientific_state(factory, sid).get('writing_job')


def runs(factory, sid):
    from agents.EnnoScholarV5.session import ScholarV4Run
    from sqlalchemy import select
    with factory() as db:
        return [copy.deepcopy(r.data) | {'status': r.status, 'id': r.id} for r in
            db.scalars(select(ScholarV4Run).where(ScholarV4Run.session_id == sid).order_by(ScholarV4Run.created_at))]


# --- 1, 2, 3, 7, 8: one request, several segments, the whole manuscript ---------------

def test_one_request_drafts_the_remaining_sections_over_several_segments(app_env):
    before = mission_state()
    sol = Sol()
    svc, sid, tr, first, factory = start(app_env, sol, before, config())
    data = scientific_state(factory, sid)
    job = data['writing_job']
    assert job['status'] == 'completed', job
    assert len(job['segments']) >= 2  # The first request and automatic continuation(s).
    assert [s['status'] for s in runs(factory, sid)][-1] in {'completed', 'partial'}
    # Sections 1-2 reused as saved; 3-5 drafted once each, never duplicated.
    sections = data['outline']['sections']
    assert [len(s.get('drafts', [])) for s in sections] == [1, 1, 1, 1, 1]
    for sid_saved in SAVED:
        old = next(s for s in before['outline']['sections'] if s['id'] == sid_saved)['drafts']
        assert next(s for s in sections if s['id'] == sid_saved)['drafts'] == old
    assert data['scientific_relations'][:2] == before['scientific_relations'][:2]
    assert all(data['articles'][a]['analysis'] == before['articles'][a]['analysis'] for a in before['articles']
        if before['articles'][a].get('analysis'))
    # Inaccessible full texts: the manuscript is complete with documentary limits, never "validated".
    view = svc.writing_job(1, sid)['job']['mission_progress']
    assert view['state'] == 'termine_avec_limites' and view['sections_done'] == view['sections_total'] == 5
    assert set(view['provisional_section_ids']) >= {s['id'] for s in sections[1:]}
    final = [m for m in data['messages'] if m['role'] == 'assistant'][-1]['content']
    assert 'Brouillon complet enregistré : 5/5' in final and 'Sections provisoires' in final
    assert 'résumés seulement' in final and 'texte intégral non obtenu' in final
    # Never asked to relaunch: no « continue la rédaction » after the request.
    later = data['messages'][next(i for i, m in enumerate(data['messages']) if m.get('content') == REQUEST):]
    assert [m for m in later if m['role'] == 'user'] == [later[0]]
    assert not any(workflow.RELAUNCH_REQUEST.strip() in (m.get('content') or '') for m in later)
    # Every segment within its own cap, and the mission within its budget.
    assert all(r['budget_limits']['tokens'] <= 400000 for r in runs(factory, sid) if r.get('budget_limits'))
    assert job['spent']['confirmed_usd'] <= job['budget_usd']


# --- 4: global caps ----------------------------------------------------------------------

def test_the_mission_dollar_budget_stops_it_and_asks_for_an_authorisation(app_env, monkeypatch):
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD', '0.6')
    svc, sid, tr, first, factory = start(app_env, metered(Sol()), mission_state(), config())
    job = job_of(factory, sid)
    assert job['status'] == 'budget_exhausted', job
    # Every call was admitted against the mission budget: never beyond it.
    assert job['spent']['confirmed_usd'] + job['spent']['uncertain_usd'] <= job['budget_usd']
    view = svc.writing_job(1, sid)['job']['mission_progress']
    assert view['state'] == 'suspendu_par_budget' and 'autorisation supplémentaire' in view['error']
    assert view['sections_done'] < 5  # Nothing beyond the authorised budget.
    data = scientific_state(factory, sid)
    assert data['messages'][-1]['content'] == job['error']


def test_the_mission_token_cap_stops_it_before_another_segment(app_env, monkeypatch):
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_MAX_TOKENS', '180000')
    svc, sid, tr, first, factory = start(app_env, metered(Sol()), mission_state(), config())
    job = job_of(factory, sid)
    assert job['status'] == 'budget_exhausted' and job['stop_reason'] in {'mission_tokens', 'reservation_insufficient'}, job
    assert job['mission']['tokens'] <= 180000  # Each segment was capped by what remained of the mission.
    assert ('plafond de la mission atteint' in job['error'] or 'insuffisantes pour réserver' in job['error']) and 'autorisation supplémentaire' in job['error']
    view = svc.writing_job(1, sid)['job']['mission_progress']
    assert view['state'] == 'suspendu_par_budget' and view['sections_done'] < 5


# --- 5: no loop without progress ------------------------------------------------------------

def test_segments_without_scientific_progress_stop_the_mission(app_env):
    svc, sid, tr, first, factory = start(app_env, Sol(idle=True), mission_state(), config())
    job = job_of(factory, sid)
    assert job['status'] == 'stalled' and len(job['segments']) == job['stalled_segments'], job
    assert svc.writing_job(1, sid)['job']['mission_progress']['state'] == 'erreur'
    data = scientific_state(factory, sid)
    assert [len(s.get('drafts', [])) for s in data['outline']['sections']] == [1, 1, 0, 0, 0]  # Nothing duplicated.


# --- 6: browser closed, backend restarted ---------------------------------------------------

def test_a_waiting_mission_is_resumed_by_a_restarted_backend(app_env, monkeypatch):
    make, factory, checkpoints = app_env
    sol = Sol()
    svc, sid, tr = make([sol]*200, mission_state(), config=config())
    monkeypatch.setattr(svc, 'dispatch_job', lambda *a, **k: None)  # The process stops before its driver starts.
    run_sync(svc, sid, REQUEST)
    svc.pool.shutdown(wait=True)
    job = job_of(factory, sid)
    assert job['status'] == 'waiting' and len(job['segments']) == 1, job
    # A new process (same database and checkpoints), no browser: start-up recovery only.
    restarted = ScholarV5Service(factory, config(), client=tr.client, sources=svc.sources, checkpointer_factory=checkpoints)
    restarted.recover_all_jobs()
    restarted.pool.shutdown(wait=True)
    job = job_of(factory, sid)
    assert job['status'] == 'completed' and len({s['run_id'] for s in job['segments']}) == len(job['segments']), job
    data = scientific_state(factory, sid)
    assert [len(s.get('drafts', [])) for s in data['outline']['sections']] == [1, 1, 1, 1, 1]


# --- Plan approval ----------------------------------------------------------------------------

def test_nothing_is_drafted_before_the_plan_approval_then_the_mission_completes(app_env):
    """Unapproved plan: the first segment prepares (cards, comparison) and saves no
    section; the mission waits; the approval resumes it until 5/5, over several segments."""
    from agents.EnnoScholarV4.manuscript import DocumentAction
    sol = Sol()
    # Six tools per segment: the remaining sections need several segments after the approval.
    svc, sid, tr, first, factory = start(app_env, sol, mission_state(approved=False), config(max_tool_calls=6), settle=False)
    job = job_of(factory, sid)
    assert job['status'] == 'paused' and job['paused_reason'] == 'plan_approval', job
    data = scientific_state(factory, sid)
    assert [len(s.get('drafts', [])) for s in data['outline']['sections']] == [1, 1, 0, 0, 0]  # Nothing new drafted.
    third = data['outline']['sections'][2]['article_ids']
    assert all(data['articles'][a].get('analysis') for a in third)  # Prepared meanwhile, nothing drafted.
    assert 'Plan à valider' in [m for m in data['messages'] if m['role'] == 'assistant'][-1]['content']
    view = svc.writing_job(1, sid)['job']['mission_progress']
    assert view['state'] == 'en_attente_de_validation'
    svc.document_action(1, sid, DocumentAction(action='approve_outline', expected_version=data['outline'].get('version', 0)))
    svc.pool.shutdown(wait=True)
    job = job_of(factory, sid)
    assert job['status'] == 'completed', job
    assert len(job['segments']) - 1 >= 2  # Several autonomous segments after the approval.
    data = scientific_state(factory, sid)
    assert [len(s.get('drafts', [])) for s in data['outline']['sections']] == [1, 1, 1, 1, 1]


def test_no_mission_without_an_explicitly_authorised_global_budget(app_env, monkeypatch):
    monkeypatch.delenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD')
    svc, sid, tr, first, factory = start(app_env, Sol(), mission_state(), config())
    data = scientific_state(factory, sid)
    assert not data.get('writing_job') and not svc.config.writing_job_enabled  # V4's 10 $ default is never assumed.
    assert len(runs(factory, sid)) == 1
    assert 'Rédaction automatique non activée' in first['answer'] and 'ENNOSCHOLAR_V5_MISSION_BUDGET_USD' in first['answer']


def test_an_invalid_mission_budget_is_refused_at_configuration(monkeypatch):
    from agents.EnnoScholarV5.config import runtime_config, ConfigurationError
    for value in ('0', '-5', 'dix'):
        monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD', value)
        with pytest.raises(ConfigurationError):
            runtime_config(config())
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_BUDGET_USD', '7.5')
    assert runtime_config(config()).writing_job_budget_usd == 7.5


# --- Duration and number of sub-runs ------------------------------------------------------------

def test_duration_and_segment_limits_stop_a_mission_before_any_new_call(monkeypatch):
    from datetime import datetime, timedelta
    from agents.EnnoScholarV5 import writing_job
    monkeypatch.setenv('ENNOSCHOLAR_V5_MISSION_MAX_SECONDS', '60')
    job = writing_job.new_job(config(), 'm1', 'r1', {})
    job['progress'] = {'id': 'writing_x', 'target_ids': ['s1']}
    assert writing_job.can_start_segment(job) == (True, None)
    job['mission']['started_at'] = (datetime.utcnow() - timedelta(seconds=61)).isoformat() + 'Z'
    assert writing_job.can_start_segment(job) == (False, 'mission_duration')
    stopped = writing_job.stop(job, 'mission_duration')
    assert stopped['status'] == 'budget_exhausted' and 'durée maximale' in stopped['error']
    assert 'autorisation supplémentaire' in stopped['error']
    job = writing_job.new_job(config(), 'm2', 'r2', {})
    job['progress'] = {'id': 'writing_y', 'target_ids': ['s1']}
    job['segments'] = [{'run_id': f'r{i}', 'spent': {}} for i in range(job['max_segments'])]
    assert writing_job.can_start_segment(job) == (False, 'segment_limit')
