"""Targeted local reading and bounded retention, with no provider/network calls."""
import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agents.EnnoScholarV5 import consulted
from agents.EnnoScholarV5.evidence import exact_source_valid
from agents.EnnoScholarV5.original import evidence_projection
from . import test_html_currency as currency
from .test_html_floats import PMC_FIGURE, SECTION_WRAPPED, STANDARD_FIGURE, ROWS, page
from .test_orchestration_reliability import prepared, rows, reading, receive, goal, final, sources
from .test_live_integration import app_env, call, run_sync, scientific_state


@pytest.fixture
def document(monkeypatch):
    # A paragraph before the figure also mentions Figure 1. Fuzzy matching alone
    # ranks it before the actual caption, just as in the audited run.
    extra = ''.join(f'<tr><td>Sample {i}</td><td>{500+i}</td><td>0.01{i}</td></tr>' for i in range(4))
    table = SECTION_WRAPPED.replace(ROWS, ROWS.replace('</tbody>', extra+'</tbody>'))
    monkeypatch.setattr(currency, 'HTML', page(PMC_FIGURE, table, STANDARD_FIGURE,
        before='Figure 1 is discussed in the general structural interpretation. '*20, doi=currency.DOI))
    return prepared()


def read_filter(section, **extra):
    return {**reading([]), 'evidence_ids': None, 'section_filter': section,
        'passage_query': 'Figure 1', 'passages_per_article': 1, **extra}


def test_read_honours_literal_section_before_fuzzy_query(document):
    t = currency.tools(document)
    result = t.execute('analyze_article', read_filter('Figure 1'), time.monotonic()+30)
    actual = result['reading'][0]['passages']
    assert len(actual) == 1 and 'fig-a.webp' in actual[0]['exact_quote']
    assert actual[0]['section'].endswith('/ Figure 1')
    assert exact_source_valid(t.evidence[actual[0]['evidence_id']], t.snapshots)


def test_missing_section_never_falls_back_to_an_unrelated_passage(document):
    result = currency.tools(document).execute('analyze_article', read_filter('Figure 99'), time.monotonic()+30)
    assert result['reading'][0]['passages'] == []
    assert result['reading'][0]['passages_total'] == 0


def test_filtered_totals_and_cursors_follow_the_same_passages(document):
    t = currency.tools(document)
    args = read_filter('Table 1', passages_per_article=2)
    first = t.execute('analyze_article', args, time.monotonic()+30)['reading'][0]
    second = t.execute('analyze_article', {**args, 'passage_offset': first['next_offset']}, time.monotonic()+30)['reading'][0]
    expected = [r for r in rows(document) if 'Table 1' in r['section']]
    assert first['passages_total'] == second['passages_total'] == len(expected)
    assert len(first['passages']) == len(second['passages']) == 2
    assert first['next_offset'] == 2 and second['next_offset'] == 4
    assert not {r['evidence_id'] for r in first['passages']} & {r['evidence_id'] for r in second['passages']}


def test_explicit_ids_keep_precedence_over_section_and_table_filters(document):
    target = next(r for r in rows(document) if 'fig-a.webp' in r['exact_quote'])
    t = currency.tools(document)
    result = t.execute('analyze_article', {**read_filter('Figure 99'),
        'evidence_ids': [target['evidence_id']], 'table_number': '99'}, time.monotonic()+30)
    assert [r['evidence_id'] for r in result['reading'][0]['passages']] == [target['evidence_id']]


def test_unfiltered_read_and_explicit_revisit_remain_available(document):
    t = currency.tools(document)
    first = t.execute('analyze_article', read_filter('Figure 1'), time.monotonic()+30)
    eid = first['reading'][0]['passages'][0]['evidence_id']
    t.shown_evidence.append(eid)
    again = t.execute('analyze_article', read_filter('Figure 1', revisit=True), time.monotonic()+30)
    assert [p['evidence_id'] for p in again['reading'][0]['passages']] == [eid]
    # No filter: preserve the ordinary query, including its general paragraph.
    plain = t.execute('analyze_article', read_filter(None), time.monotonic()+30)
    assert 'general structural interpretation' in plain['reading'][0]['passages'][0]['exact_quote']


def test_bounded_memory_keeps_first_ranked_hit_before_later_incidental_rows(document):
    table = [r for r in rows(document) if r.get('table') and r['table'].get('row')]
    target = next(r for r in table if '0.02124' in r['exact_quote'])
    ordered = [target, *[r for r in table if r != target]]
    receive(document, [r['evidence_id'] for r in ordered])
    before = copy.deepcopy(document)
    budget = max(len(json.dumps(evidence_projection(r), ensure_ascii=False)) for r in ordered)+700
    packet = consulted.packet(document, budget)
    assert packet['omitted_total'] > 0  # room for only part of the received table
    assert packet['evidence'][0]['evidence_id'] == target['evidence_id']
    assert len(json.dumps(packet, ensure_ascii=False)) <= budget
    assert document == before  # only a view; no rewriting of source, proof or receipt


def test_newer_read_has_priority_without_reversing_its_passages(document):
    all_rows = rows(document)
    table = next(r for r in all_rows if r.get('table'))
    figures = [r for r in all_rows if '/ Figure ' in r['section']]
    assert len(figures) >= 2
    receive(document, [table['evidence_id']])
    receive(document, [r['evidence_id'] for r in figures[:2]])
    packet = consulted.packet(document, 12000)
    assert [r['evidence_id'] for r in packet['evidence'][:2]] == [r['evidence_id'] for r in figures[:2]]


def test_read_filter_is_isolated_between_concurrent_calls_and_from_v4(document, monkeypatch):
    import agents.EnnoScholarV4.toolkit as v4
    original_v4 = v4.passages
    # Synchronise the selector invocations, so both filters are active together.
    import agents.EnnoScholarV5.toolkit as v5
    original_passages = v5._passages
    barrier = threading.Barrier(2)
    def concurrent(article):
        barrier.wait(timeout=10)
        return original_passages(article)
    monkeypatch.setattr(v5, '_passages', concurrent)
    def run(section):
        t = currency.tools(copy.deepcopy(document))
        return t.execute('analyze_article', read_filter(section), time.monotonic()+30)['reading'][0]['passages']
    with ThreadPoolExecutor(max_workers=2) as pool:
        figure, table = list(pool.map(run, ['Figure 1', 'Table 1']))
    assert figure and all(p['section'].endswith('/ Figure 1') for p in figure)
    assert table and all(p.get('table') for p in table)
    assert v4.passages is original_v4
    monkeypatch.setattr(v5, '_passages', original_passages)
    # A subsequent unfiltered call cannot inherit either context.
    plain = currency.tools(document).execute('analyze_article', read_filter(None), time.monotonic()+30)
    assert 'general structural interpretation' in plain['reading'][0]['passages'][0]['exact_quote']


def test_sdk_graph_reuses_table_and_reads_figure_without_index_or_revisit(app_env, document):
    make, factory, _ = app_env
    seen = {}
    table_args = {**reading([]), 'evidence_ids': None, 'passage_query': 'Table 1', 'passages_per_article': 8}
    def figure(sent):
        output = next(json.loads(i['output']) for i in sent['input']
            if i.get('type') == 'function_call_output' and i.get('call_id') == 'table')
        seen['table_ids'] = [p['evidence_id'] for p in output['reading'][0]['passages']]
        seen['target'] = next(p['evidence_id'] for p in output['reading'][0]['passages'] if '0.02124' in p['exact_quote'])
        return call('analyze_article', read_filter('Figure 1'), 'figure')
    def finish(sent):
        output = next(json.loads(i['output']) for i in sent['input']
            if i.get('type') == 'function_call_output' and i.get('call_id') == 'figure')
        caption = output['reading'][0]['passages'][0]
        assert 'fig-a.webp' in caption['exact_quote']
        memory = next(json.loads(i['content']) for i in sent['input']
            if i.get('role') == 'developer' and i.get('content', '').startswith('{')
            and json.loads(i['content']).get('kind') == 'current_request_consulted_material')
        target = next(p for p in memory['evidence'] if p['evidence_id'] == seen['target'])
        return final([target['evidence_id'], caption['evidence_id']],
            f"[{currency.AID}]\n{target['exact_quote']} [{target['evidence_id']}]\n"
            f"{caption['exact_quote']} [{caption['evidence_id']}]")
    svc, sid, transport = make([[call('set_turn_goal', goal(), 'goal'),
        call('analyze_article', table_args, 'table')], figure, finish], document, sources=sources())
    result = run_sync(svc, sid, 'Donne le tableau 1, les valeurs du matériau A et la légende de la figure 1, depuis le texte enregistré.')
    assert '0.02124' in result['answer'] and 'fig-a.webp' in result['answer']
    assert len(transport.requests) == 3  # scenario measure, not a production turn cap
    state = scientific_state(factory, sid)
    read_windows = [w for w in state['reading_windows'] if w.get('article_id') == currency.AID
        and w['window_id'].startswith('reading_')
        and not w['window_id'].startswith('reading_context_')]
    assert len(read_windows) == 2
    assert not set(read_windows[0]['evidence_ids']) & set(read_windows[1]['evidence_ids'])
    assert not state['articles'][currency.AID].get('analysis')
