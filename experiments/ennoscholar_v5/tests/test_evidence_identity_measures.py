"""N05 passage identity, N10 measurement binding, F01 cross-article attribution. Offline fixtures."""
import copy
import hashlib
import json
import time
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4 import evidence as v4_evidence
from agents.EnnoScholarV5.evidence import (SEGMENTATION, exact_source_valid, passages, snapshot,
    reusable_science, set_proof_status, proof_status)
from agents.EnnoScholarV5.models import Article
from agents.EnnoScholarV5.toolkit import ScholarTools

CONFIG = ScholarV4Config(api_key='offline', web_search_enabled=False)
PAGES = ['Methods\nThe membrane was cast from a PAN solution and dried at 60 °C for 12 h.',
    'Results\nThe stability duration is 14 h. The catalyst loading is 55 mg.',
    'Discussion\nA later page that was not extracted in the first batch.']


def tools(state):
    sources = Mock()
    sources.verify.side_effect = lambda article, *a, **k: article
    return ScholarTools(state, sources, CONFIG)


def read(t, aid, **extra):
    args = {'article_ids': [aid], 'action': 'read', 'research_context': 'Lecture.', 'passage_query': '',
        'passage_offset': 0, 'passages_per_article': 8, 'analysis': None, **extra}
    return t.execute('analyze_article', args, time.monotonic()+60)['reading'][0]


# --- N05: identity of unchanged passages when the same PDF's extraction grows ---------------

@pytest.fixture
def pdf_article(tmp_path, monkeypatch):
    """A real 3-page PDF of which pages 1-2 are extracted, as get_fulltext stores it."""
    import fitz
    from agents.EnnoScholarV5.pdf_store import extract_pages
    from agents.EnnoScholarV5.provenance import document_identity
    monkeypatch.setenv('ENNOSCHOLAR_V5_PDF_DIRECTORY', str(tmp_path))
    with fitz.open() as doc:
        for text in PAGES:
            doc.new_page().insert_text((50, 60), text)
        data = doc.tobytes()
    sha = hashlib.sha256(data).hexdigest()
    (tmp_path / f'{sha}.pdf').write_bytes(data)
    chunks, count, truncated, progress = extract_pages(f'{sha}.pdf', 1, 2, config=CONFIG)
    from agents.EnnoScholarV5.tools.extraction import ScientificChunk
    version = document_identity(tmp_path / f'{sha}.pdf', 'https://fixture.invalid/n05.pdf', 'pdf',
        [ScientificChunk.model_validate(c) for c in chunks])
    version['identifiers'] = {'doi': '10.9999/n05'}
    fulltext = {'access_status': 'fulltext_extracted', 'access_level': 'fulltext', 'url': 'https://fixture.invalid/n05.pdf',
        'pdf_file': f'{sha}.pdf', 'source_kind': 'pdf', 'document_version': version, 'chunks': chunks,
        'text': '\n\n'.join(c['text'] for c in chunks), 'page_count': count, 'truncated': True,
        'page_extraction': progress['page_extraction'], 'segmentation': SEGMENTATION, 'attempts': []}
    aid = 'paper_n05_fixture'
    article = Article(id=aid, canonical_work_id=aid, title='N05 fixture', doi='10.9999/n05', url=fulltext['url'],
        verified=True, fulltext=fulltext, access_status='fulltext_extracted', access_level='fulltext', fulltext_available=True)
    article.documents = {version['version_id']: copy.deepcopy(fulltext)}
    state = {'articles': {aid: article.durable()}, 'evidence': {}, 'source_snapshots': {}, 'shown_evidence_ids': []}
    return state, aid, tmp_path, sha


def extend(t, aid):
    return t.execute('get_fulltext', {'article_id': aid, 'page_start': 3, 'page_limit': 1, 'candidate_urls': None},
        time.monotonic()+60)


def test_n05_real_page_extension_keeps_unchanged_passage_ids(pdf_article):
    state, aid, _, _ = pdf_article
    t = tools(state)
    first = read(t, aid)['passages']
    before = {p['evidence_id']: p['exact_quote'] for p in first}
    old_rows = {e: copy.deepcopy(state['evidence'][e]) for e in before}
    extend(t, aid)
    article = Article.model_validate(state['articles'][aid])
    assert any(c.get('page') == 3 for c in article.fulltext['chunks'])
    now ={p['exact_quote']: p['evidence_id'] for p in passages(article)}
    assert any('later page' in q for q in now)                     # the extension is really there
    for eid, quote in before.items():
        assert now[quote] == eid                                    # unchanged passage, same ID
        entry = state['evidence'][eid]
        assert entry['source_fingerprint'] == snapshot(article)['fingerprint']
        assert exact_source_valid(entry, state['source_snapshots'])
        assert state['evidence_versions'][eid] == [old_rows[eid]]   # previous entry archived, unchanged
        assert exact_source_valid(old_rows[eid], state['source_snapshots'])  # old snapshot still resolvable
    # The new reading offers only the new page, not the passages already read.
    again = read(tools(state), aid)
    assert {p['evidence_id'] for p in again['passages']} == {now[q] for q in now if 'later page' in q}


def test_n05_ids_survive_save_round_trip_resume_and_reuse(pdf_article):
    state, aid, _, _ = pdf_article
    t = tools(state)
    ids = {p['evidence_id'] for p in read(t, aid)['passages']}
    extend(t, aid)
    resumed = json.loads(json.dumps(state))                         # checkpoint / database round trip
    article = Article.model_validate(resumed['articles'][aid])
    assert ids <= {p['evidence_id'] for p in passages(article)}
    clean = reusable_science(article, resumed['source_snapshots'], resumed['evidence'])
    assert clean and clean[0]['fulltext']['evidence_aliases']       # another session receives the identities
    assert ids <= set(clean[2]) and all(exact_source_valid(clean[2][e], clean[1]) for e in ids)


def test_n05_a_modified_document_or_passage_gets_new_ids(pdf_article):
    state, aid, _, _ = pdf_article
    t = tools(state)
    ids = {p['evidence_id'] for p in read(t, aid)['passages']}
    article = t.registry[aid]
    # Same document, one passage really changed (a new extraction produces new chunk
    # records): only that passage gets a new ID.
    chunks = [dict(c) for c in article.fulltext['chunks']]
    chunks[1]['text'] = chunks[1]['text'].replace('14 h', '15 h')
    article.fulltext['chunks'] = chunks
    article.fulltext['text'] ='\n\n'.join(c['text'] for c in article.fulltext['chunks'])
    t.save()
    rows = {p['exact_quote']: p['evidence_id'] for p in passages(Article.model_validate(state['articles'][aid]))}
    changed = [q for q in rows if '15 h' in q]
    assert changed and all(rows[q] not in ids for q in changed)
    assert any(rows[q] in ids for q in rows if '15 h' not in q)


def test_n05_another_document_has_no_equivalence(pdf_article):
    state, aid, _, _ = pdf_article
    t = tools(state)
    ids = {p['evidence_id'] for p in read(t, aid)['passages']}
    article = t.registry[aid]
    article.fulltext['document_version'] = {**article.fulltext['document_version'], 'document_sha256': '0'*64}
    article.fulltext['chunks'] = [*article.fulltext['chunks'], {'text': 'Extra page.', 'section': 'Discussion', 'page': 3}]
    article.fulltext['text'] += '\n\nExtra page.'
    t.save()
    saved = state['articles'][aid]['fulltext']
    assert 'evidence_aliases' not in saved and not ids & {p['evidence_id'] for p in passages(Article.model_validate(state['articles'][aid]))}
    assert 'evidence_versions' not in state


def test_n05_never_extended_documents_keep_v4_ids_and_withdrawn_proofs_stay_withdrawn(pdf_article):
    state, aid, _, _ = pdf_article
    article = Article.model_validate(state['articles'][aid])
    assert [p['evidence_id'] for p in passages(article)] == [p['evidence_id'] for p in v4_evidence.passages(article)]
    t = tools(state)
    shown = [p['evidence_id'] for p in read(t, aid)['passages']]
    set_proof_status(state, shown[0], 'withdrawn', reason='test')
    withdrawn = copy.deepcopy(state['evidence'][shown[0]])
    extend(t, aid)
    assert state['evidence'][shown[0]] == withdrawn and proof_status(state, shown[0]) == 'withdrawn'
    again = read(tools(state), aid, revisit=True)
    assert shown[0] not in {p['evidence_id'] for p in again['passages']}   # not silently reactivated


# --- N10: a value is bound to its unit, its sentence and its proof ---------------------------

from agents.EnnoScholarV5.science import Measurement, validate_measurements, check_comparative_measurements


def text_case(*texts):
    chunks = [{'text': t, 'section': 'Results', 'page': i+1} for i, t in enumerate(texts)]
    article = Article(id='binding', title='Fixture', doi='10.9999/binding', url='https://fixture.invalid/binding',
        fulltext={'text': '\n\n'.join(texts), 'url': 'https://fixture.invalid/binding', 'chunks': chunks})
    rows, source = passages(article), snapshot(article)
    state = {'evidence': {r['evidence_id']: r for r in rows}, 'source_snapshots': {source['fingerprint']: source},
        'shown_evidence_ids': [r['evidence_id'] for r in rows]}
    return state, article, [r['evidence_id'] for r in rows]


def measure(ids, value, unit, conditions='Reported source conditions', **extra):
    return Measurement(metric='Reported quantity', value_raw=value, unit=unit, experimental_conditions=conditions,
        measurement_method='Reported method', evidence_ids=ids, origin='observed_result', **extra)


TWO = 'The stability duration is 14 h. The catalyst loading is 55 mg.'


@pytest.mark.parametrize('text,value,unit,ok,issues', [
    (TWO, '14', 'h', True, []), (TWO, '55', 'mg', True, []),
    (TWO, '55', 'h', False, None), (TWO, '14', 'mg', False, None), (TWO, '55', 'g', False, None),   # SI prefix kept
    ('The current density was 1.45 mA cm−2 at 25 °C.', '1.45', 'mA', False, None),             # truncated unit
    ('The current density was 1.45 mA cm−2 at 25 °C.', '1.45', 'mA cm−2', True, []),
    ('The current density was 1.45 mA cm−2 at 25 °C.', '1.45', 'mA cm-2', True, []),            # typographic minus
    ('A flux of 3.2 mol L−1 h−1 was reached.', '3.2', 'mol L−1', False, None),
    ('Over the course of continuous 55-h operation.', '55', 'h', True, []),
    ('Stable for 14 h at 25 °C and 55 h at 60 °C.', '55', 'h', True, ['several_values_with_this_unit_in_sentence']),
    ('Lifetimes ranged over 14–55 h.', '14', 'h', True, ['range_bound']),
    ('Lifetimes ranged over 14–55 h.', '55', 'h', True, ['range_bound']),
])
def test_n10_text_value_unit_and_sentence_binding(text, value, unit, ok, issues):
    state, article, ids = text_case(text)
    m = measure(ids, value, unit)
    if not ok:
        with pytest.raises(ValueError):
            validate_measurements(state, article, [m])
        return
    [saved] = validate_measurements(state, article, [m]).values()
    assert saved['binding_issues'] == issues and saved['needs_verification'] is bool(issues)
    assert value in saved['source_sentence']


def test_n10_value_and_unit_from_two_different_proofs_are_not_a_pair():
    state, article, ids = text_case('The stability duration is 14 h.', 'The catalyst loading is 55 mg.')
    with pytest.raises(ValueError):
        validate_measurements(state, article, [measure(ids, '55', 'h')])
    assert validate_measurements(state, article, [measure(ids, '55', 'mg')])


def test_n10_condition_numbers_absent_from_the_source_need_verification():
    state, article, ids = text_case('Stable for 55 h at 25 °C in 1 M KOH.')
    [ok] = validate_measurements(state, article, [measure(ids, '55', 'h', conditions='25 °C, 1 M KOH')]).values()
    [unsure] = validate_measurements(state, article, [measure(ids, '55', 'h', conditions='80 °C, 1 M KOH')]).values()
    assert not ok['needs_verification'] and unsure['binding_issues'] == ['condition_values_not_in_source']


def test_n10_known_measurement_identity_is_unchanged():
    from agents.EnnoScholarV5.task_results import fingerprint
    state, article, ids = text_case(TWO)
    m = measure(ids, '14', 'h')
    [key] = validate_measurements(state, article, [m])
    legacy = fingerprint({**m.model_dump(), 'source_fingerprints': [state['evidence'][ids[0]]['source_fingerprint']],
        'validation': 'literal_value_and_unit', 'semantic_support_status': 'interpreted_by_sol; source_and_literal_value_checked'})
    assert key == legacy and list(validate_measurements(state, article, [m])) == [key]


def table_case():
    from bs4 import BeautifulSoup
    from agents.EnnoScholarV5.tools.tables import html_table, table_text
    html = ('<div class="table-wrap"><h4>Table 1.</h4><div class="caption">Measured transport parameters.</div><table>'
        '<thead><tr><th>Sample</th><th>I0/mA</th><th>R0/Ω</th><th>Duration [h]</th></tr></thead><tbody>'
        '<tr><td>A</td><td>0.02124</td><td>396.937</td><td>14</td></tr><tr><td>B</td><td>0.0126</td><td>699.096</td><td>55</td></tr>'
        '</tbody></table></div>')
    table = html_table(BeautifulSoup(html, 'html.parser').find('table'))
    chunk = {'text': table_text(table), 'section': 'Results / Table 1', 'page': None, 'table': table}
    article = Article(id='tabular', title='Fixture', doi='10.9999/tabular', url='https://fixture.invalid/tabular',
        fulltext={'text': chunk['text'], 'url': 'https://fixture.invalid/tabular', 'chunks': [chunk]})
    rows, source = passages(article), snapshot(article)
    state = {'evidence': {r['evidence_id']: r for r in rows}, 'source_snapshots': {source['fingerprint']: source},
        'shown_evidence_ids': [r['evidence_id'] for r in rows]}
    by_sample = {r['table']['row'][0]: r['evidence_id'] for r in rows if r.get('table')}
    return state, article, by_sample


@pytest.mark.parametrize('sample,column,value,unit,ok', [
    ('A', 'R0/Ω', '396.937', 'Ω', True), ('A', 'I0/mA', '0.02124', 'mA', True), ('B', 'Duration [h]', '55', 'h', True),
    ('A', 'R0/Ω', '396.937', 'mA', False),          # unit of another column
    ('A', 'R0/Ω', '0.02124', 'Ω', False),           # value of another column
    ('B', 'Duration [h]', '14', 'h', False),        # value of another row (sample A)
])
def test_n10_table_cell_column_and_header_unit(sample, column, value, unit, ok):
    state, article, by_sample = table_case()
    m = measure([by_sample[sample]], value, unit, source_kind='table', column=column).model_copy(update={'metric': column})
    if ok:
        [saved] = validate_measurements(state, article, [m]).values()
        assert saved['unit_source'] == 'column_header' and not saved['needs_verification']
    else:
        with pytest.raises(ValueError):
            validate_measurements(state, article, [m])


def test_n10_a_measurement_to_verify_cannot_support_a_superiority():
    from types import SimpleNamespace
    unsure = {'m1': {'evidence_ids': ['e1'], 'needs_verification': True, 'binding_issues': ['range_bound'],
        'metric': 'Duration', 'unit': 'h', 'experimental_conditions': 'x', 'measurement_method': 'y'}}
    sure = {'m2': {'evidence_ids': ['e2'], 'needs_verification': False, 'binding_issues': [],
        'metric': 'Duration', 'unit': 'h', 'experimental_conditions': 'x', 'measurement_method': 'y'}}
    registry = {'a': SimpleNamespace(analysis={'structured_measurements': unsure}),
        'b': SimpleNamespace(analysis={'structured_measurements': sure})}
    relation = SimpleNamespace(kind='conditional_superiority', article_ids=['a', 'b'], evidence_ids=['e1', 'e2'])
    with pytest.raises(ValueError, match='vérifier'):
        check_comparative_measurements(SimpleNamespace(registry=registry), SimpleNamespace(relations=[relation]))
    registry['a'].analysis['structured_measurements']['m1']['needs_verification'] = False
    check_comparative_measurements(SimpleNamespace(registry=registry), SimpleNamespace(relations=[relation]))


# --- F01: values used in a comparison belong to the work they are stated for ---------------

from types import SimpleNamespace
from agents.EnnoScholarV5.science import check_relation_attribution

QUOTES = {'a': 'The MatA catalyst loading is 55 mg and it stays stable for 14 h at 25 °C.',
    'b': 'The MatB electrode is stable for 55 h at 60 °C.'}


def attribution(statement, conditions='', results=()):
    toolkit = SimpleNamespace(evidence={f'e{k}': {'article_id': k, 'exact_quote': q} for k, q in QUOTES.items()},
        registry={'a': SimpleNamespace(title='Supported catalyst study'), 'b': SimpleNamespace(title='Electrode durability study')})
    relation = SimpleNamespace(statement=statement, conditions=conditions, evidence_ids=['ea', 'eb'],
        experimental_results=[SimpleNamespace(statement=s, evidence_ids=['ea', 'eb']) for s in results])
    check_relation_attribution(toolkit, SimpleNamespace(relations=[relation]))


@pytest.mark.parametrize('statement', [
    'MatA tient 14 h, MatB tient 55 h.',
    'MatB reste stable 55 h contre 14 h pour MatA.',
    'MatB dure plus longtemps (55 vs 14 h).',                    # no single work named per part
    'Les deux travaux rapportent 14 h et 55 h dans des conditions différentes.',   # ambiguous: left to the union check
    'MatA utilise 55 mg de catalyseur ; MatB tient 55 h.',
])
def test_f01_correct_or_ambiguous_attributions_are_kept(statement):
    attribution(statement)


@pytest.mark.parametrize('statement', [
    'MatA tient 55 h, MatB tient 14 h.',                          # inverted between works
    'MatA reste stable 55 h.',                                    # same number, unit of the other work (MatA: 55 mg)
    'MatB utilise 55 mg de catalyseur.',
    'MatA tient 14 h ; MatB tient 14 h.',
])
def test_f01_inverted_attributions_are_refused(statement):
    with pytest.raises(ValueError, match='Attribution inversée'):
        attribution(statement)


def test_f01_conditions_and_experimental_results_are_checked():
    attribution('Stabilités non comparables.', conditions='MatA : 25 °C ; MatB : 60 °C')
    with pytest.raises(ValueError, match='Attribution inversée'):
        attribution('Stabilités non comparables.', conditions='MatA : 60 °C ; MatB : 25 °C')
    with pytest.raises(ValueError, match='absente des preuves'):
        attribution('Stabilités non comparables.', conditions='MatA : 80 °C')
    with pytest.raises(ValueError, match='Attribution inversée'):
        attribution('Stabilités non comparables.', results=['MatB reste stable 14 h.'])


def test_f01_real_comparison_keeps_the_correct_relation_and_refuses_the_swap():
    from . import test_comparison_memory as cm
    state = cm.request_state()
    for revision in [293, 295, 299, 303]:
        cm.deliver(state, revision)
    assert cm.compare(state, relations=[cm.relation()], assessment='Stability comparison').get('saved')
    swapped = cm.request_state()
    for revision in [293, 295, 299, 303]:
        cm.deliver(swapped, revision)
    result = cm.compare(swapped, relations=[cm.relation(statement='Cu–SiOx reste stable pendant 14 h, YSZ/CuO pendant 55 h.')],
        assessment='Stability comparison')
    assert not result.get('saved') and not swapped.get('scientific_relations')
