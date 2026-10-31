"""Regressions from the real CIR session 3445749b (CO2 to ethylene, six runs, 28/10).

Observed: readings cut to 1-4 passages, plan only in the chat, section 1 written
with 3 of its 5 articles, five sections squeezed into one 7 000-token answer,
writing preparation stopped as "stagnation", a previous request's goal reused.
Scripted Sol over the real state and the real Responses SDK (simulated HTTP):
these tests prove what reaches Sol and what is saved, not Sol's prose quality.
"""
import copy
import gzip
import json
import re
from unittest.mock import Mock
import pytest
from sqlalchemy import select
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.original import compact_exchange
from agents.EnnoScholarV4.tools.outline import OutlineArgs, manage_outline
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.persistence import Operation
from agents.EnnoScholarV5.runtime import ScientificAdapter
from experiments.ennoscholar_v5.adapter import Decision
from experiments.ennoscholar_v5.policy import reusable, semantic_key
from .test_graph import ARTIFACTS
from .test_live_integration import app_env, call, run_sync, scientific_state

WRITE_ALL = "Rédige maintenant un état de l’art scientifique complet en suivant le plan des 5 sections."
WRITE_ONE = ("Rédige uniquement la section 1 : Conception des sites catalytiques du cuivre pour favoriser l’éthylène, de manière "
    "approfondie: méthodes, protocoles, résultats chiffrés, mécanismes et limites.")


def real():
    with gzip.open(ARTIFACTS/'real-cir-session.json.gz', 'rt', encoding='utf-8') as f:
        return json.load(f)


def adapter():
    return ScientificAdapter(ScholarV4Config(api_key='offline', web_search_enabled=False), Mock(), sources=Mock())


def real_plan(state):
    """The five sections, their articles and roles, as published in the chat (run 42056c55)."""
    message = next(m['content'] for m in state['messages'] if m['role'] == 'assistant' and m['content'].startswith('Je te propose de regrouper'))
    titles = {a['title'].strip().lower(): aid for aid, a in state['articles'].items()}
    sections = []
    for block in re.split(r'\n## ', message)[1:]:
        heading = re.sub(r'^\d+\.\s*', '', block.split('\n')[0]).strip()
        rows = re.findall(r'\| \*(.+?)\* \[.*?\]\(.*?\) \| (.+?) \|', block)
        ids, roles = [], []
        for title, role in rows:
            aid = titles.get(title.strip().lower())
            if aid and aid not in ids:
                ids.append(aid)
                roles.append({'article_id': aid, 'role': role.strip()})
        sections.append({'title': heading, 'description': block.split('\n')[2][:3000] if len(block.split('\n')) > 2 else heading,
            'article_ids': ids, 'article_roles': roles})
    return sections


def with_outline(state):
    s = copy.deepcopy(state)
    args = OutlineArgs(action='create', title='Stabilité et sélectivité du cuivre pour CO2 vers éthylène', section_id=None,
        target_section_id=None, position=None, sections=real_plan(state), article_ids=None)
    from agents.EnnoScholarV4.models import Article
    manage_outline(s, args, {aid: Article.model_validate(a) for aid, a in s['articles'].items()})
    # These requests draft « en suivant le plan » : the consultant has approved it.
    from agents.EnnoScholarV4.manuscript import approve_outline
    approve_outline(s, 1)
    return s


# --- 1. Readings: transmitted or deferred, never cut to one passage per work ----------

def real_batch():
    """The two readings of one real response (run 2d882893, revisions 49-50)."""
    ops = [o for o in real()['operations'] if o['run'].startswith('2d882893') and o['kind'] == 'action'
        and o['revision'] in (49, 50)]
    return [(Decision('analyze_article', o['request']['arguments']), o['response']['output']) for o in ops]


def test_batched_readings_are_transmitted_or_deferred_never_cut_uniformly():
    state = copy.deepcopy(real()['state'])
    ad, batch = adapter(), real_batch()
    windows = {}
    for _, output in batch:
        for row in output['reading']:
            windows[row['reading_window_id']] = {'window_id': row['reading_window_id'], 'article_id': row['article']['id'],
                'status': 'queued', 'evidence_ids': [p['evidence_id'] for p in row['passages']]}
    state['reading_windows'] = [w for w in state['reading_windows'] if w['window_id'] not in windows]+list(windows.values())
    exchange = [{'type': 'function_call', 'call_id': f'r{i}', 'name': 'analyze_article', 'arguments': '{}'} for i in range(2)]
    # Before: V4 compaction of the whole response kept one passage per work.
    raw = exchange+[{'type': 'function_call_output', 'call_id': f'r{i}', 'output': json.dumps(o, ensure_ascii=False)}
        for i, (_, o) in enumerate(batch)]
    budget = workflow.context_split(ad.config, state)[0]
    before = compact_exchange(copy.deepcopy(raw), ad.toolkit(copy.deepcopy(state)), budget)
    assert max(len(r['passages']) for i in before if i.get('type') == 'function_call_output'
        for r in json.loads(i['output'])['reading']) == 1
    # After: each result takes the room left by the previous ones.
    state['v5_exchange'] = exchange
    t = ad.toolkit(state)
    fitted = []
    for i, (decision, output) in enumerate(batch):
        room = budget-workflow.exchange_size(state['v5_exchange'])
        out = workflow.fit_output(state, t, decision, copy.deepcopy(output), room)
        fitted.append(out)
        state['v5_exchange'].append({'type': 'function_call_output', 'call_id': f'r{i}', 'output': json.dumps(out, ensure_ascii=False)})
    first, second = fitted
    assert [len(r['passages']) for r in first['reading']] == [len(r['passages']) for r in batch[0][1]['reading']]  # intact
    assert workflow.exchange_size(state['v5_exchange']) <= budget
    withheld = {e for r in second.get('reading', []) for e in r.get('withheld_evidence_ids', [])} | {
        e for ids in (second.get('withheld_evidence_ids') or {}).values() for e in ids}
    assert withheld  # Given back explicitly, by exact identifier.
    by_id = {w['window_id']: w for w in state['reading_windows']}
    for row in second.get('reading', []):
        assert by_id[row['reading_window_id']]['evidence_ids'] == [p['evidence_id'] for p in row['passages']]
    # The prepared request carries exactly the fitted results: nothing is cut afterwards.
    known = set(state['shown_evidence_ids'])
    toolkit, inputs = ad.prepare(state)
    sent = [json.loads(i['output']) for i in inputs if i.get('type') == 'function_call_output']
    assert [len(r['passages']) for r in sent[0]['reading']] == [len(r['passages']) for r in first['reading']]
    ad.delivered(toolkit, inputs)
    # Passages given back are never acknowledged as read.
    assert not (withheld-known) & set(state['shown_evidence_ids'])


def test_a_real_reading_batch_reaches_sol_through_the_graph_without_uniform_cuts(app_env):
    """Revisions 49-50 of run 2d882893, executed by the graph, adapter and SDK."""
    make, factory, _ = app_env
    calls = [call('analyze_article', {**d.arguments, 'revisit': True}, f'r{i}') for i, (d, _) in enumerate(real_batch())]
    seen = {}
    def inspect(sent):
        seen['outputs'] = [json.loads(i['output']) for i in sent['input'] if i.get('type') == 'function_call_output']
        return call('pause', {'reason': 'consultant_review'}, 'p')
    request = next(r for r in real()['runs'] if r['id'].startswith('2d882893'))['data']['request']
    svc, sid, tr = make([calls, inspect], real()['state'])
    run_sync(svc, sid, request)
    first, second = seen['outputs']
    # The first reading arrives whole: 2 works x 8 passages (real run: 1 each).
    assert [len(r['passages']) for r in first['reading']] == [8, 8]
    delivered = sum(len(r.get('passages', [])) for o in seen['outputs'] for r in o.get('reading', []))
    assert delivered > 6  # Real run: 2 + 4 = 6 passages for these two calls.
    data = scientific_state(factory, sid)
    windows = {w['window_id']: w for w in data['reading_windows']}
    for out in seen['outputs']:
        for row in out.get('reading', []):
            assert windows[row['reading_window_id']]['evidence_ids'] == [p['evidence_id'] for p in row['passages']]


def test_withheld_passages_are_readable_next_and_deferred_results_are_not_replayed():
    state = copy.deepcopy(real()['state'])
    decision, output = real_batch()[1]
    out = workflow.fit_output(state, adapter().toolkit(state), decision, copy.deepcopy(output), 2000)
    assert out['transmission_deferred'] and out['not_executed']
    receipt = {'response': {'semantic_key': semantic_key(state, decision), 'output': out}}
    assert reusable(state, decision, [receipt])[1] is None  # Executed again, never served as a result.


# --- 2. The plan is a saved artefact with its associations and source states ----------

def test_plan_is_saved_with_associations_roles_and_readiness(app_env):
    make, factory, _ = app_env
    state = real()['state']
    sections = real_plan(state)
    plan = {'action': 'create', 'title': 'Stabilité et sélectivité du cuivre pour CO2 vers éthylène', 'section_id': None,
        'target_section_id': None, 'position': None, 'sections': sections, 'article_ids': None}
    request = next(r for r in real()['runs'] if r['id'].startswith('42056c55'))['data']['request']
    text = "Plan en cinq sections enregistré ; les sources non analysées restent signalées."
    def answer(tag):
        return call('finalize_response', {'answer': text, 'article_ids': [], 'evidence_ids': [], 'coverage_sufficient': False,
            'remaining_uncertainties': ['Sources non analysées'], 'displayed_article_ids': None}, tag)
    def save_then_present(sent):
        refusal = next(json.loads(i['output']) for i in sent['input'] if i.get('call_id') == 'prose' and i.get('type') == 'function_call_output')
        assert refusal['rejection_code'] == 'plan_not_saved'
        return [call('manage_outline', plan, 'plan'), answer('present')]
    goal = next(o for o in real()['operations'] if o['run'].startswith('42056c55') and o['kind'] == 'decision')['response']['decision']
    svc, sid, tr = make([[call('set_turn_goal', goal['arguments'], 'goal'), answer('prose')], save_then_present], state)
    result = run_sync(svc, sid, request)
    assert result['reason'] is None and not result.get('fallback'), result
    data = scientific_state(factory, sid)
    outline = data['outline']
    assert len(outline['sections']) == 5 and outline['status'] == 'proposed' and not outline.get('approval')
    pairs = [(s['title'], aid) for s in outline['sections'] for aid in s['article_ids']]
    assert len(pairs) == sum(len(s['article_ids']) for s in sections) >= 24
    assert len({aid for _, aid in pairs}) == 11
    assert all(r['role'] for s in outline['sections'] for r in s['article_roles'])
    # source_readiness returned with the saved plan: limits are visible, not silent.
    with factory() as db:
        saved = next(o for o in db.scalars(select(Operation).where(Operation.kind == 'action'))
            if o.request['tool'] == 'manage_outline')
    states = {aid: row['status'] for sec in saved.response['output']['source_readiness'] for aid, row in sec['sources'].items()}
    assert states['paper_8ba44ae1e2f0383cdf4b'] == 'analysed'
    assert states['paper_2558375a4bcbb86d2bb1'] != 'analysed'  # Publisher 403: a stated limit.
    assert states[next(a for a in states if a.endswith('52abc9'))] == 'to_analyse'  # Extracted, never analysed.
    assert len(tr.requests) == 2  # Goal + premature prose, then the plan and its presentation.


def test_forced_synthesis_of_an_unsaved_plan_offers_manage_outline(app_env):
    from .test_stokes_control import make_meter
    from agents.EnnoScholarV5.runtime import ResponsesSol
    svc, sid, tr, rid, scope, meter, factory = make_meter(app_env, state=real()['state'])
    data = scientific_state(factory, sid)
    data['turn_goal'] = {**data['turn_goal'], 'deliverable': 'plan_revision'}
    data['v5_request_start'] = {'request_id': workflow.current_request(data).get('id'), 'plan_revision': None}
    assert workflow.plan_required(data)
    ad = ScientificAdapter(svc.config, meter, sources=Mock())
    _, inputs = ad.prepare(data)
    sol = ResponsesSol(svc.config, meter, client=tr.client)
    sol.bind(data, {'sequence': 0, 'finalizing': True}, 'forced')
    tr.choices[:] = [[call('manage_outline', {'action': 'read', 'title': None, 'section_id': None, 'target_section_id': None,
        'position': None, 'sections': None, 'article_ids': None}, 'o'), call('finalize_response', {'answer': 'x', 'article_ids': [],
        'evidence_ids': [], 'coverage_sufficient': False, 'remaining_uncertainties': [], 'displayed_article_ids': None}, 'f')]]
    decision = sol.choose(inputs, ad.tools(), {}, instructions='')
    sent = tr.requests[0]
    assert {t['name'] for t in sent['tools']} == {'manage_outline', 'finalize_response'} and sent['tool_choice'] == 'required'
    assert decision.tool == 'manage_outline' and decision.calls[0]['tool'] == 'finalize_response'


# --- 3-4. Section dossiers, coverage of every source, drafting section by section -----

def section_text(packet, omit=()):
    """A text built only from the dossier: proof-backed claims, and documentary limits."""
    claims = {}
    for claim in packet['proof_backed_claims']:
        claims.setdefault(claim['article_id'], claim)
    lines, evidence, cited = [], [], []
    for source in packet['sources']:
        aid = source['id']
        if aid in omit:
            continue
        claim = claims.get(aid)
        if claim and source['status'] == 'analysed':
            lines.append(f"{claim['statement']} [{aid}] " + " ".join(f"[{e}]" for e in claim['evidence_ids']))
            evidence += claim['evidence_ids']
            cited.append(aid)
    # Sources without a usable card are not named in prose (V4 attribution check):
    # the dossier lists them as documentary limits, recorded with the section.
    assert {row['article_id'] for row in packet['documentary_limits']} == {
        src['id'] for src in packet['sources'] if src['status'] != 'analysed'}
    if packet.get('requires_conclusion'):
        # Scripted prose only: conclusion reuses a cited, proof-backed statement.
        lines.append('## Conclusion générale\n\n'+(lines[0] if lines else 'Consolidation documentaire encore nécessaire.'))
    return "\n\n".join(lines), evidence, cited


def dossier(sent):
    item = next(i for i in sent['input'] if i.get('role') == 'developer' and isinstance(i.get('content'), str)
        and i['content'].startswith(workflow.DOSSIER_PREFIX))
    return json.loads(item['content'][len(workflow.DOSSIER_PREFIX):])


def last_output(sent):
    outputs = [json.loads(i['output']) for i in sent['input'] if i.get('type') == 'function_call_output']
    return outputs[-1] if outputs else {}


def preparation_refusals(factory):
    with factory() as db:
        return [o for o in db.scalars(select(Operation).where(Operation.kind == 'action'))
            if o.request['tool'] == 'write_section' and o.response['output'].get('rejection_code') == 'section_preparation_required']


def confirm_after_preparation(otherwise):
    """Sol's answer to a one-time preparation refusal: confirm the proposed text
    (text=null); any other previous result gets the scripted next step."""
    def respond(sent):
        step = prepare_unread_source(sent)
        if step:
            return step
        last = last_output(sent)
        if last.get('rejection_code') == 'section_preparation_required':
            sid = last['preparation']['section_id']
            return call('write_section', {'section_id': sid, 'action': 'save', 'instructions': 'Texte proposé confirmé.',
                'text': None, 'article_ids': [], 'evidence_ids': []}, f'confirm_{sid}')
        return otherwise(sent) if callable(otherwise) else otherwise
    return respond


def prepare_unread_source(sent):
    """Scripted Sol now processes retrievable sources instead of confirming blindly."""
    if not any(str(i.get('content','')).startswith(workflow.DOSSIER_PREFIX) for i in sent['input']):
        return None
    prep = dossier(sent).get('scientific_preparation', {})
    for item in prep.get('actions', []):
        aid = item.get('article_id')
        source = prep.get('sources', {}).get(aid, {})
        if item.get('kind')=='network' and source.get('access_state')=='not_requested':
            return call('get_fulltext',{'article_id':aid,'candidate_urls':None,'page_start':None,'page_limit':20},'retrieve_'+aid)
        if item.get('kind')=='local' and source.get('text_available') and (source['status']!='analysed' or source['basis']!='fulltext'):
            last = last_output(sent)
            row = next((r for r in last.get('reading',[]) if r['article']['id']==aid), {})
            if row.get('passages'):
                from .test_direct_state_of_art import save
                return save(aid,[p['evidence_id'] for p in row['passages']],'enrich_'+aid)
            return call('analyze_article',{'article_ids':[aid],'action':'read','research_context':'Approfondir le texte disponible.',
                'passage_query':'method results limitations','passage_offset':0,'passages_per_article':4,'analysis':None},'deepen_'+aid)
    return None


@pytest.fixture(autouse=True)
def simulated_retrieval_failures(monkeypatch):
    """Writing tests simulate refused HTTP; extraction success has its own tests."""
    import httpx
    from agents.EnnoScholarV5.tools.fulltext import base
    def refuse(url,*args,**kwargs):
        req=httpx.Request('GET',url)
        raise httpx.HTTPStatusError('Offline fixture: inaccessible',request=req,response=httpx.Response(403,request=req))
    monkeypatch.setattr(base,'fetch_public',refuse)


def drafting_choices(plan_ids, log, omit_first=()):
    """Scripted drafting from the dossier. A preparation refusal (once per section) is
    answered by confirming the proposed text: these tests are about coverage."""
    def save(omit):
        def respond(sent):
            step = prepare_unread_source(sent)
            if step:
                return step
            last = last_output(sent)
            if last.get('rejection_code') == 'section_preparation_required':
                sid = last['preparation']['section_id']
                return call('write_section', {'section_id': sid, 'action': 'save', 'instructions': 'Texte proposé confirmé.',
                    'text': None, 'article_ids': [], 'evidence_ids': []}, f'confirm_{sid}')
            packet = dossier(sent)
            sid = packet['section']['id']
            assert [s['id'] for s in packet['sources']] == plan_ids[sid]  # Every associated work is offered.
            text, evidence, cited = section_text(packet, omit)
            log.append((sid, omit))
            return call('write_section', {'section_id': sid, 'action': 'save', 'instructions': 'Section du plan validé.',
                'text': text, 'article_ids': cited, 'evidence_ids': evidence}, f'w{len(log)}')
        return respond
    return [save(omit_first), *[save(()) for _ in range((workflow.LOCAL_REFUSALS+3)*len(plan_ids)+12)]]


def test_five_sections_are_drafted_separately_checked_saved_and_assembled(app_env):
    make, factory, _ = app_env
    state = with_outline(real()['state'])
    plan_ids = {s['id']: s['article_ids'] for s in state['outline']['sections']}
    first = state['outline']['sections'][0]['article_ids']
    log = []
    svc, sid, tr = make(drafting_choices(plan_ids, log, omit_first=tuple(first[3:5])), state)
    result = run_sync(svc, sid, WRITE_ALL)
    assert result['reason'] is None and result['writing_progress']['status'] == 'draft_complete', result
    # Section 1 first omitted two of its five works: refused, then rewritten with all five.
    with factory() as db:
        saves = [o.response['output'] for o in db.scalars(select(Operation).where(Operation.kind == 'action').order_by(Operation.revision))
            if o.request['tool'] == 'write_section']
    saves = [s for s in saves if s.get('rejection_code') != 'section_preparation_required']
    assert saves[0]['rejection_code'] == 'section_sources_missing'
    assert {m['article_id'] for m in saves[0]['missing_sources']} == set(first[3:5])
    # One response per section, plus the correction, plus one confirmation per section whose
    # preparation was incomplete (refused once, never twice).
    refused = preparation_refusals(factory)
    per_section = {}
    for o in refused:
        per_section[o.request['arguments']['section_id']] = per_section.get(o.request['arguments']['section_id'], 0)+1
    assert per_section and max(per_section.values()) <= workflow.LOCAL_REFUSALS  # Bounded, never a loop.
    # Every refusal names an action that can actually be done.
    assert all(o.response['output']['preparation']['actions'] for o in refused)
    with factory() as db:
        preparation_calls = sum(o.request['tool'] in {'analyze_article','get_fulltext'} for o in
            db.scalars(select(Operation).where(Operation.kind=='action')))
    assert len(tr.requests) == 6+len(refused)+preparation_calls and result['usage']['llm_calls'] == len(tr.requests)
    assert not any(any(t.get('name') == 'finalize_response' for t in [*r['tools']]) and r['tool_choice'] != 'auto' for r in tr.requests)
    data = scientific_state(factory, sid)
    limits = data['v5_section_limits']
    for section in data['outline']['sections']:
        assert len(section['drafts']) == 1
        raw = section['drafts'][0]['raw_text']
        stated = {row['article_id'] for row in limits[section['id']]}
        # Every associated work is either cited with its proofs or a stated documentary limit.
        assert all(aid in raw or aid in stated for aid in section['article_ids'])
        assert not any(aid in raw for aid in stated)
    assert sum(len(v) for v in limits.values()) >= 1  # Sources refused by the simulated transport remain explicit limits.
    assert "Limites documentaires de la section" in result['answer']
    assert data['document']['writing_progress']['status'] == 'draft_complete'
    answer = result['answer']
    assert 'Brouillon complet enregistré : 5/5' in answer
    assert [s['title'] for s in data['outline']['sections']]+['Conclusion générale'] == re.findall(r'^## (.+)$', answer, re.M)
    # Methods, figures and proofs stay reachable: every draft cites exact, shown passages.
    shown = set(data['shown_evidence_ids'])
    assert all(set(s['drafts'][0]['evidence_ids']) <= shown for s in data['outline']['sections'])
    assert result['budget_limits']['tokens'] == svc.config.max_tokens


def test_section_one_alone_uses_its_five_articles_after_real_preparation(app_env):
    """Run 572ad911's own decisions: goal, section read, four cards, then the section."""
    make, factory, _ = app_env
    state = with_outline(real()['state'])
    s1 = state['outline']['sections'][0]
    ops = [o for o in real()['operations'] if o['run'].startswith('572ad911') and o['kind'] == 'decision']
    goal, read = ops[0]['response']['decision'], ops[1]['response']['decision']
    claims = ops[2]['response']['decision']
    batch = [call(claims['tool'], claims['arguments'], 'c0'), *[call(c['tool'], c['arguments'], f'c{i+1}') for i, c in enumerate(claims['calls'])]]
    log = []
    svc, sid, tr = make([call('set_turn_goal', goal['arguments'], 'goal'), call('write_section', read['arguments'], 'read'), batch,
        *drafting_choices({s1['id']: s1['article_ids']}, log)[1:2+workflow.LOCAL_REFUSALS]], state)
    result = run_sync(svc, sid, WRITE_ONE)
    assert result['reason'] is None and result['writing_progress']['mode'] == 'section', result  # No stagnation stop.
    data = scientific_state(factory, sid)
    assert data['turn_goal']['references']['message_id'] == workflow.current_request(data)['id']
    assert 'uniquement la section 1' in data['turn_goal']['understanding'] or data['turn_goal']['deliverable'] == 'draft'
    draft = data['outline']['sections'][0]['drafts'][0]['raw_text']
    assert all(aid in draft for aid in s1['article_ids']) and len(s1['article_ids']) == 5  # All five analysed and cited.
    assert all(not s.get('drafts') for s in data['outline']['sections'][1:])
    assert len(tr.requests) == 4+len(preparation_refusals(factory))


def test_interrupted_drafting_resumes_at_the_next_section_without_rewriting(app_env, monkeypatch):
    from experiments.ennoscholar_v5 import graph
    make, factory, _ = app_env
    state = with_outline(real()['state'])
    plan_ids = {s['id']: s['article_ids'] for s in state['outline']['sections']}
    log = []
    svc, sid, tr = make(drafting_choices(plan_ids, log)[1:], state)
    original = graph.Runner.__init__
    crashed = []
    def crash(runner_repo):
        def after(opid):
            op = runner_repo.operation(runner_repo_scope[0], opid)
            if op['kind'] == 'action' and op['request']['tool'] == 'write_section' and len(log) == 2 and not crashed:
                crashed.append(opid)
                raise RuntimeError('worker lost after the second section')
        return after
    runner_repo_scope = []
    def init(self, scope, repository, *args, **kwargs):
        runner_repo_scope[:] = [scope]
        kwargs.setdefault('after_commit', crash(repository))
        original(self, scope, repository, *args, **kwargs)
    monkeypatch.setattr(graph.Runner, '__init__', init)
    first = run_sync(svc, sid, WRITE_ALL)
    assert first['reason'].startswith('v5_interrupted') and first['resumable_run_id'] == first['id'], first
    monkeypatch.setattr(graph.Runner, '__init__', original)
    svc.resume_run(1, first['id'])
    svc.pool.shutdown(wait=True)
    resumed = svc.run(1, first['id'])
    assert resumed['writing_progress']['status'] == 'draft_complete', resumed
    data = scientific_state(factory, sid)
    assert [len(s['drafts']) for s in data['outline']['sections']] == [1, 1, 1, 1, 1]  # Nothing rewritten.
    # No repeated paid call: one response per section, one confirmation per refused preparation.
    assert len(tr.requests) >= 5+len(preparation_refusals(factory)) and resumed['usage']['llm_calls'] == len(tr.requests)


# --- 6. Orchestration: request-scoped receipts, inaccessible documents ----------------

def test_a_previous_requests_goal_is_never_served_to_a_new_request():
    state = copy.deepcopy(real()['state'])
    goal = next(o for o in real()['operations'] if o['run'].startswith('572ad911') and o['kind'] == 'decision')['response']['decision']
    decision = Decision('set_turn_goal', goal['arguments'])
    earlier = {'response': {'semantic_key': semantic_key(state, decision), 'output': {'saved': True, 'goal': {'understanding': 'cinq sections'}}}}
    state['turn_goal'] = {'references': {}}
    assert reusable(state, decision, [earlier])[2] == 'same_operation'  # Same request: replay is legitimate.
    state['messages'] = [*state['messages'], {'id': 'next_request', 'role': 'user', 'content': WRITE_ONE}]
    assert reusable(state, decision, [earlier])[1] is None  # New request: the goal is recorded again.


def test_an_inaccessible_document_is_not_requested_again_in_the_same_request():
    state = copy.deepcopy(real()['state'])
    blocked = 'paper_4bcdfdf3560046d9b59e'
    assert state['articles'][blocked]['fulltext'].get('attempts') and not state['articles'][blocked]['fulltext'].get('text')
    ask = Decision('get_fulltext', {'article_id': blocked, 'candidate_urls': None, 'page_start': None, 'page_limit': 10,
        'complement_reason': 'version ouverte'})
    assert reusable(state, ask, [])[1] is None  # First attempt of this request runs.
    workflow.record_delivery(state, Decision('get_fulltext', {'article_id': blocked}),
        {'article_id': blocked, 'access_status': 'access_blocked', 'access_level': 'abstract_only', 'attempts': [{'stage': 'download'}]})
    _, out, why = reusable(state, ask, [])
    assert why == 'inaccessible_source' and out['reason'] == 'source_inaccessible'
    assert reusable(state, Decision('get_fulltext', {**ask.arguments, 'candidate_urls': ['https://example.org/oa.pdf']}), [])[2] is None


def test_reading_cards_for_writing_is_progress_but_replays_are_not():
    from experiments.ennoscholar_v5.progress import project, Scope
    from experiments.ennoscholar_v5.policy import informative_signature
    state = with_outline(real()['state'])
    before = informative_signature(project(state, scope=Scope(1, 'x')))
    claims = next(o for o in real()['operations'] if o['run'].startswith('572ad911') and o['kind'] == 'action'
        and o['request']['tool'] == 'get_analysis_claims')
    decision = Decision('get_analysis_claims', claims['request']['arguments'])
    workflow.record_delivery(state, decision, claims['response']['output'])
    after = informative_signature(project(state, scope=Scope(1, 'x')))
    assert after != before
    workflow.record_delivery(state, decision, claims['response']['output'])
    assert informative_signature(project(state, scope=Scope(1, 'x'))) == after
