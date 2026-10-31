"""Depth regressions from real V5 run e5cdb635 (Stokes, second live test).

Diagnosed causes, each reproduced on the persisted run data:
1. An 8-passage reading (~25 000 chars) was cut to 4 passages by a fixed
   22 000-char exchange budget, while V4 gives the exchange the remaining room.
2. V4's list card shows one claim for five fields: the saved mechanism, mouse
   protocols and resistance claims never reached Sol (0 of 8 proofs presented).
3. The graph projection only checked method/results/limitations, all filled by
   the old card: no signal that results pages 8-10 or methods 20-29 were unread.
4. get_fulltext on the archived document (4 calls) returned no content.
Scripted Sol: these tests check data access and accounting, not Sol's reasoning.
"""
import copy
import gzip
import json
from unittest.mock import Mock
from sqlalchemy import select
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.original import compact_exchange
from agents.EnnoScholarV5.persistence import Operation
from agents.EnnoScholarV5.runtime import ScientificAdapter, MEMORY_PREFIX
from experiments.ennoscholar_v5.adapter import Decision
from experiments.ennoscholar_v5.policy import reusable
from experiments.ennoscholar_v5.progress import Scope, project, model_work_view
from .test_graph import ARTIFACTS, STOKES, case
from .test_live_integration import app_env, call, run_sync, scientific_state
from .test_stokes_control import observed, control


def run2():
    with gzip.open(ARTIFACTS/'real-stokes-v5-run2.json.gz', 'rt', encoding='utf-8') as f:
        return json.load(f)


def start_of_run2():
    """Real run-2 data before its own readings: the saved card only."""
    s = copy.deepcopy(run2()['state'])
    while s['messages'][-1]['role'] == 'assistant':
        s['messages'].pop()
    s.pop('last_response', None)
    s['shown_evidence_ids'], s['v5_presented_context_evidence'] = [], []
    return s


def adapter():
    return ScientificAdapter(ScholarV4Config(api_key='offline', web_search_enabled=False), Mock(), sources=Mock())


def memory(inputs):
    item = next(i for i in inputs if isinstance(i.get('content'), str) and i['content'].startswith(MEMORY_PREFIX))
    return json.loads(item['content'][len(MEMORY_PREFIX):])


def card(m):
    return next(c for c in [*m['selected_articles'], *m['consulted_articles']] if c['id'] == STOKES)


def statements(c):
    return ' '.join(claim['statement'] for claims in c['analysis'].values() for claim in claims)


def real_read_outputs():
    return [o for o in run2()['operations'] if o['kind'] == 'action' and o['request']['tool'] == 'analyze_article'
        and o['response']['output'].get('reading')]


def test_real_run_lost_half_of_each_reading_and_now_receives_all_passages():
    s = start_of_run2()
    out = real_read_outputs()[0]['response']['output']
    assert len(out['reading'][0]['passages']) == 8
    wid = out['reading'][0]['reading_window_id']
    # The tool's window as created before transmission (the persisted one was cut).
    s['reading_windows'] = [w for w in s['reading_windows'] if w['window_id'] != wid]+[{'window_id': wid,
        'article_id': STOKES, 'status': 'queued', 'evidence_ids': [p['evidence_id'] for p in out['reading'][0]['passages']]}]
    s['v5_exchange'] = [{'type': 'function_call', 'call_id': 'r', 'name': 'analyze_article', 'arguments': '{}'},
        {'type': 'function_call_output', 'call_id': 'r', 'output': json.dumps(out, ensure_ascii=False)}]
    ad = adapter()
    # Before: the fixed 22 000-char exchange budget transmitted 4 of 8 passages.
    old = compact_exchange(copy.deepcopy(s['v5_exchange']), ad.toolkit(copy.deepcopy(s)), 22000)
    assert len(json.loads(old[-1]['output'])['reading'][0]['passages']) == 4
    _, inputs = ad.prepare(s)
    sent = next(json.loads(i['output']) for i in inputs if i.get('type') == 'function_call_output')
    assert len(sent['reading'][0]['passages']) == 8
    assert len(next(w for w in s['reading_windows'] if w['window_id'] == wid)['evidence_ids']) == 8
    # Room is still left for the graph's control packet.
    assert len(json.dumps(inputs, ensure_ascii=False)) + 14000 <= ad.config.max_context_chars


def test_saved_mechanism_protocol_and_animal_claims_reach_a_deep_reading():
    s = start_of_run2()
    ad = adapter()
    _, before_fix = ad.prepare(copy.deepcopy({**s, 'turn_goal': {**s['turn_goal'], 'deliverable': 'literature_review'}}))
    compact = card(memory(before_fix))
    # A list keeps V4's compact card, but now counts every omitted stored field.
    assert '15 mg/kg' not in statements(compact)
    assert {'technical_mechanism', 'experimental_setup', 'hypotheses'} <= set(compact['analysis_omitted_claims'])
    for final in (False, True):
        state = copy.deepcopy(s)
        state['v5_finalization_mode'] = final
        _, inputs = ad.prepare(state)
        c, text = card(memory(inputs)), statements(card(memory(inputs)))
        assert 'DiSC3(5)' in text and '15 mg/kg' in text and 'mutant' in text
        assert set(c['analysis']) >= {'technical_mechanism', 'experimental_setup', 'results', 'limitations'}
        if not final:
            # Before synthesis every saved claim fits; passages stay readable by ID.
            assert 'hypotheses' in c['analysis'] and 'fer' in text and not c.get('analysis_omitted_claims')
        else:
            # Synthesis keeps citable passages first and declares what it left out.
            assert sum(c['analysis_omitted_claims'].values()) >= 1
        # Every saved claim's proof is either sent or addressable by ID.
        m = memory(inputs)
        ids = {eid for claims in c['analysis'].values() for claim in claims for eid in claim['evidence_ids']}
        assert ids <= {e['evidence_id'] for e in m['evidence']} | set(m['proofs_available_by_id'])
        assert len(inputs[0]['content']) + len(inputs[1]['content']) <= ad.config.finalization_context_chars + 200


def test_progress_shows_unexamined_results_and_methods_pages_without_imposing_them():
    s = start_of_run2()
    p = project(s, scope=Scope(1, 'x'))
    row = next(r for r in model_work_view(p)['articles'] if r['article_id'] == STOKES)
    assert set(range(20, 30)) <= set(row['unexamined_pages'])
    assert {'tables_figures', 'comparisons'} <= set(row['empty_card_fields'])
    methods = [g for g in row['sections'] if g[0] == 'METHODS DETAILS' and g[1] == 24]
    assert methods and methods[0][3] == 0 and methods[0][1] == 24
    # Historical proofs stay saved. A changed segmentation is not implicitly
    # examined just because its first 80 characters match an older quotation.
    assert row['states']['saved'] == len(p['articles'][STOKES]['saved_evidence_ids'])
    task = next(t for t in p['tasks'].values() if t['kind'] == 'review_depth')
    assert task['unexamined_pages'] == row['unexamined_pages']
    assert p['counts']['target'] is None  # A named-paper reading is not a quota.
    # A multi-article list stays compact: no section map per article.
    chem = model_work_view(project(case('chemistry')['state'], scope=Scope(1, 'c')))
    assert not any('reading_map' in r for r in chem['articles'])


def test_archived_pages_return_fresh_unexamined_locators_without_reextraction():
    s = copy.deepcopy(run2()['state'])
    asked = Decision('get_fulltext', {'article_id': STOKES, 'page_start': 17, 'page_limit': 13, 'candidate_urls': None,
        'complement_reason': 'Lire les méthodes STAR détaillées'})
    key, out, why = reusable(s, asked, [])
    assert why == 'archived_document' and out['reused_local']
    index = out['unexamined_passages']
    assert index['total_unexamined'] == 41 and all(17 <= r['page'] <= 29 for r in index['passages'])
    first = index['passages'][0]['evidence_id']
    s['shown_evidence_ids'].append(first)
    replay = {'response': {'semantic_key': key, 'output': out}}
    _, again, why = reusable(s, asked, [replay])
    assert why == 'archived_document' and first in {r['evidence_id'] for r in again['unexamined_passages']['passages']}
    assert again['unexamined_passages']['total_unexamined']==41  # Presented is still unexamined.
    # Incomplete pages still go to the extractor (real continuation, run 18a2a6a).
    _, cont, _ = reusable(observed()['state'], Decision('get_fulltext', {'article_id': STOKES, 'page_start': 21,
        'page_limit': 9, 'candidate_urls': None, 'complement_reason': 'pages restantes'}), [])
    assert cont is None


def test_offline_replay_of_run2_reaches_methods_and_cites_animal_evidence(app_env, monkeypatch):
    """Real first decisions of run e5cdb635, then a scripted use of the new locators."""
    make, factory, _ = app_env
    monkeypatch.setenv('ENNOSCHOLAR_V5_PDF_DIRECTORY', str(ARTIFACTS/'pdf'))
    real = [o['response']['decision'] for o in run2()['operations'] if o['kind'] == 'decision']
    first = [call(d['tool'], d['arguments'], 'real'+str(i)) for i, d in enumerate(real[:5])]
    assert [d['tool'] for d in real[:6]] == ['set_turn_goal', 'get_article', 'set_turn_goal', 'get_fulltext',
        'analyze_article', 'get_fulltext']
    seen = {}

    def output(sent):
        return next(json.loads(i['output']) for i in reversed(sent['input']) if i.get('type') == 'function_call_output')

    def archived(sent):
        # Real 6th decision: pages 17-29 of the now complete document.
        seen['mixed_read'] = output(sent)['reading'][0]['passages']
        return call('get_fulltext', real[5]['arguments'], 'real5')

    def read_methods(sent):
        index = output(sent)['unexamined_passages']['passages']
        seen['index'] = index
        ids = [r['evidence_id'] for r in index if r['page'] >= 20][:8]
        return call('analyze_article', {'article_ids': [STOKES], 'action': 'read', 'research_context': 'Méthodes détaillées',
            'passage_query': '', 'passage_offset': 0, 'passages_per_article': 8, 'analysis': None, 'evidence_ids': ids}, 'methods')

    def recover_animal_card(sent):
        seen['methods'] = [p['evidence_id'] for p in output(sent)['reading'][0]['passages']]
        c = card(memory(sent['input']))
        assert c.get('saved_card_ref')  # The previous complete card is not resent automatically.
        return call('get_analysis_claims',{'article_id':STOKES,'fields':['experimental_setup'],'offset':0},'animal_card')

    def reread_animal(sent):
        ids = list(dict.fromkeys(e for claim in output(sent)['claims'] for e in claim['evidence_ids']))[:8]
        seen['animal'] = ids
        return call('analyze_article', {'article_ids': [STOKES], 'action': 'read', 'research_context': 'Modèles murins',
            'passage_query': '', 'passage_offset': 0, 'passages_per_article': 8, 'analysis': None, 'evidence_ids': ids}, 'animal')

    def publish(sent):
        eid, mid = seen['animal'][0], seen['methods'][0]
        return call('finalize_response', {'answer': '[A Deep Learning Approach to Antibiotic Discovery](https://doi.org/10.1016/j.cell.2020.01.021) : '
            f'modèles murins décrits dans la fiche et relus [{eid}] ; méthodes détaillées examinées [{mid}]. '
            'Figures non interprétées visuellement ; lecture ciblée.', 'article_ids': [STOKES], 'evidence_ids': [eid, mid],
            'coverage_sufficient': False, 'remaining_uncertainties': ['Figures supplémentaires'], 'displayed_article_ids': [STOKES]}, 'final')

    svc, sid, tr = make([*first, archived, read_methods, recover_animal_card, reread_animal, publish], observed()['state'])
    result = run_sync(svc, sid, run2()['run']['request'])
    assert result['reason'] is None and not result.get('fallback'), result
    # 10 scripted calls instead of 15 real ones; one explicitly recovers the needed card field.
    assert len(tr.requests) == result['usage']['llm_calls'] == 10
    assert len(seen['mixed_read']) == 8  # Full mixed-query reading reached Sol.
    data = scientific_state(factory, sid)
    shown = set(data['shown_evidence_ids'])
    assert set(seen['animal']) <= shown and set(seen['methods']) <= shown
    assert all(data['evidence'][e]['page'] >= 20 for e in seen['methods'])
    ft = data['articles'][STOKES]['fulltext']
    assert ft['next_page'] is None and len(ft['page_extraction']) == 29
    # The saved card and proofs are untouched; no second extraction of pages 17-29.
    assert data['articles'][STOKES]['analysis'] == observed()['state']['articles'][STOKES]['analysis']
    assert all(data['evidence'][k] == v for k, v in observed()['state']['evidence'].items())
    with factory() as db:
        ops = [o for o in db.scalars(select(Operation).where(Operation.kind == 'action'))]
        fulltext = [o for o in ops if o.request['tool'] == 'get_fulltext']
        assert [o.response.get('reuse_reason') for o in fulltext] == [None, 'archived_document']
    assert '15' in result['answer'] or 'murins' in result['answer']
