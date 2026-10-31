"""N01–N04: real V5 extractors/read tools, isolated data, no live transport."""
import copy
import hashlib
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.tools import extraction as v4
from agents.EnnoScholarV5.tools import extraction as v5
from agents.EnnoScholarV5.evidence import passages, snapshot, exact_source_valid
from agents.EnnoScholarV5.models import Article
from .test_graph import case
from .test_live_integration import app_env, call, run_sync, scientific_state


URL = 'https://fixture.invalid/article.xml'
DOI = '10.9999/extraction-step1'
TITLE = 'Isolated extraction study'
PROSE = 'Scientific methods and experimental observations under documented conditions. ' * 24


def table(identifier='T1', label='Table 1', value='14'):
    return (f'<table-wrap id="{identifier}"><label>{label}</label><caption><p>Stability of catalysts.</p></caption>'
        '<table><thead><tr><th>Catalyst</th><th>Duration (h)</th></tr></thead>'
        f'<tbody><tr><td>A</td><td>{value}</td></tr></tbody></table></table-wrap>')


def figure(identifier='F2', label='Figure 2', caption='Experimental curves; values require reading the source.', href='fig2.png'):
    return ('<fig'+(f' id="{identifier}"' if identifier else '')+'>'+
        (f'<label>{label}</label>' if label else '')+
        (f'<caption><p>{caption}</p></caption>' if caption else '')+
        (f'<graphic xlink:href="{href}"/>' if href else '')+'</fig>')


def jats(inside='', outside='', prose=PROSE):
    return ('<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>'
        f'<article-id pub-id-type="doi">{DOI}</article-id><title-group><article-title>{TITLE}</article-title></title-group>'
        '<contrib-group><contrib><name><surname>Author</surname><given-names>Ada</given-names></name></contrib></contrib-group>'
        '</article-meta></front><body><sec><title>Methods</title><p>'+prose+'</p>'+inside+
        '</sec></body>'+outside+'</article>').encode()


@pytest.mark.parametrize('placement', ['body', 'floats-group', 'back'])
def test_jats_tables_inside_and_outside_body(placement):
    data = jats(inside=table()) if placement == 'body' else jats(outside=f'<{placement}>{table()}</{placement}>')
    article, chunks, truncated = v5.xml_document(data, URL)
    tables = [c.table for c in chunks if getattr(c, 'table', None)]
    assert not truncated and article.doi == DOI
    assert len(tables) == 1
    assert tables[0]['headers'] == ['Catalyst', 'Duration (h)']
    assert tables[0]['rows'] == [['A', '14']] and tables[0]['structure_status'] == 'verified_cells'
    assert tables[0]['element_id'] == 'T1' and tables[0]['label'] == 'Table 1'
    assert tables[0]['number'] == '1' and tables[0]['source_url'] == URL


@pytest.mark.parametrize('label,number', [('Table 1', '1'), ('Table S1', 'S1'), ('Supplementary Table S2', 'S2'), ('Table IV', 'IV')])
def test_separate_table_label_and_caption(label, number):
    _, chunks, _ = v5.xml_document(jats(table(label=label)), URL)
    t = next(c.table for c in chunks if getattr(c, 'table', None))
    assert t['label'] == label and t['number'] == number
    assert t['caption'] == 'Stability of catalysts.'
    assert t['cell_origins'] and t['rows'][0][1] == '14'


def test_floating_copies_are_not_duplicated_or_distinct_tables_merged():
    first = table()
    _, chunks, _ = v5.xml_document(jats(first, '<floats-group>'+first+table('T2', 'Table 2', '55')+'</floats-group>'), URL)
    tables = [c.table for c in chunks if getattr(c, 'table', None)]
    assert [t['element_id'] for t in tables] == ['T1', 'T2']
    assert [t['rows'][0][1] for t in tables] == ['14', '55']


@pytest.mark.parametrize('outside', [False, True])
def test_figure_references_are_literal_and_attached_to_source(outside):
    f = figure()
    data = jats(outside='<floats-group>'+f+'</floats-group>') if outside else jats(inside=f)
    _, chunks, _ = v5.xml_document(data, URL)
    figures = [c for c in chunks if getattr(c, 'figure', None)]
    assert len(figures) == 1
    record = figures[0].figure
    assert record == {'id': 'F2', 'label': 'Figure 2', 'caption': 'Experimental curves; values require reading the source.',
        'graphic_refs': ['fig2.png'], 'source_url': URL, 'publication_doi': DOI}
    assert 'F2' in figures[0].text and 'fig2.png' in figures[0].text
    assert 'numeric_values' not in record and 'image_analysis' not in record


@pytest.mark.parametrize('changes', [dict(identifier=None), dict(label=None), dict(caption=None), dict(href=None)])
def test_incomplete_figure_metadata_is_not_invented(changes):
    _, chunks, _ = v5.xml_document(jats(figure(**changes)), URL)
    record = next(c.figure for c in chunks if getattr(c, 'figure', None))
    key = {'identifier': 'id', 'label': 'label', 'caption': 'caption', 'href': 'graphic_refs'}[next(iter(changes))]
    assert not record[key]


def test_duplicate_figures_and_empty_figures():
    f = figure()
    _, chunks, _ = v5.xml_document(jats(f+'<fig/>', '<floats-group>'+f+'</floats-group>'), URL)
    assert len([c for c in chunks if getattr(c, 'figure', None)]) == 1
    assert sum(c.text.count('Experimental curves') for c in chunks) == 1


def test_namespaced_jats_and_distinct_table_contents_are_preserved():
    data = jats(table(), '<floats-group>'+table(value='55')+'</floats-group>')
    data = data.replace(b'<article ', b'<article xmlns="urn:fixture:jats" ', 1)
    _, chunks, _ = v5.xml_document(data, URL)
    tables = [c.table for c in chunks if getattr(c, 'table', None)]
    assert [t['rows'][0][1] for t in tables] == ['14', '55']
    assert all(t['label'] == 'Table 1' for t in tables)


@pytest.mark.parametrize('value', ['14 h', '1.45 mA cm−2', '1,45 mol L−1', '1.4e-6 A', '1.4 × 10⁻⁶ mol·L⁻¹', '14 ± 2 h', '25–30 %', '37 °C', '14 m. s−1', '¹⁴ h'])
def test_no_numeric_prefix_or_unit_fragment_at_any_boundary(value):
    lead = 'Scientific context. Duration '
    text = lead+value+' under the experimental protocol.'
    for cut in range(len(lead)+1, len(lead+value)+1):
        chunks, truncated = v5.bounded([v5.TextChunk(section='Results', text=text)],
            replace(ScholarV4Config(api_key='offline'), max_extracted_chars=cut))
        retained = ''.join(c.text for c in chunks)
        assert truncated and len(retained) <= cut
        assert retained == 'Scientific context. Duration', (value, cut, retained)


@pytest.mark.parametrize('format', ['xml', 'html'])
def test_numeric_boundary_is_used_by_real_document_extractors(format):
    lead = PROSE+'Duration '
    limit = len(lead+'1')
    config = replace(ScholarV4Config(api_key='offline'), max_extracted_chars=limit)
    if format == 'xml':
        _, chunks, truncated = v5.xml_document(jats(prose=lead+'14 h.'), URL, config)
    else:
        html = f'<html><head><title>{TITLE}</title></head><body><article><h2>Methods</h2><p>{lead}14 h.</p><h2>Results</h2><p>{PROSE}</p></article></body></html>'
        _, chunks, truncated = v5.html_document(html.encode(), URL, config)
    assert truncated and sum(len(c.text) for c in chunks) <= limit
    assert not chunks[-1].text.endswith('Duration 1') and '14' not in chunks[-1].text[-20:]


def test_whole_records_share_one_allowance_and_report_deferred_table():
    _, chunks, _ = v5.xml_document(jats(table()+figure()), URL)
    table_index = next(i for i, c in enumerate(chunks) if getattr(c, 'table', None))
    before = sum(len(c.text) for c in chunks[:table_index])
    limit = before+len(chunks[table_index].text)-1
    retained, truncated = v5.bounded(chunks, replace(ScholarV4Config(api_key='offline'), max_extracted_chars=limit))
    assert truncated and sum(len(c.text) for c in retained) <= limit
    assert not any(getattr(c, 'table', None) or getattr(c, 'figure', None) for c in retained)
    assert all(a.model_dump() == b.model_dump() for a, b in zip(retained, chunks[:table_index]))


def test_figure_at_character_limit_is_whole_or_explicitly_deferred():
    _, chunks, _ = v5.xml_document(jats(figure()), URL)
    fig = next(c for c in chunks if getattr(c, 'figure', None))
    limit = sum(len(c.text) for c in chunks)-1
    retained, truncated = v5.bounded(chunks, replace(ScholarV4Config(api_key='offline'), max_extracted_chars=limit))
    assert truncated and not any(getattr(c, 'figure', None) for c in retained)
    full, truncated = v5.bounded(chunks, replace(ScholarV4Config(api_key='offline'), max_extracted_chars=limit+1))
    assert not truncated and full[-1].model_dump() == fig.model_dump()


def test_zero_room_and_complete_numeric_paragraphs():
    config = replace(ScholarV4Config(api_key='offline'), max_extracted_chars=0)
    assert v5.bounded([v5.TextChunk(text='14 h')], config) == ([], True)
    text = 'Complete result: 14 h.\nNext duration 55 mg under conditions.'
    chunks, truncated = v5.bounded([v5.TextChunk(text=text)], replace(config, max_extracted_chars=text.index('55')+1))
    assert truncated and chunks[0].text == 'Complete result: 14 h.\nNext duration'


def test_complete_numeric_paragraph_before_an_unfitting_first_word():
    text = 'Complete result: 14 h.\n12345 kg under conditions.'
    cap = len('Complete result: 14 h.\n12')
    retained, truncated = v5.bounded([v5.TextChunk(text=text)],
        replace(ScholarV4Config(api_key='offline'), max_extracted_chars=cap))
    assert truncated and retained[0].text == 'Complete result: 14 h.'


def test_complete_chunks_and_existing_html_table_remain_unchanged():
    html = f'<article><h2>Methods</h2><p>{PROSE}</p><figure id="T"><figcaption>Table 3. Duration (h)</figcaption><table><tr><th>Sample</th><th>h</th></tr><tr><td>A</td><td>14</td></tr></table></figure><h2>Results</h2><p>{PROSE}</p></article>'
    old = v4.html_document(html.encode(), URL)
    new = v5.html_document(html.encode(), URL)
    assert [c.model_dump() for c in old[1]] == [c.model_dump() for c in new[1]]
    assert old[2] == new[2] is False
    assert v4.bounded is not v5.bounded and v4.xml_document is not v5.xml_document


def test_pdf_extraction_and_pagination_keep_existing_contract():
    import fitz
    with fitz.open() as doc:
        doc.new_page().insert_text((50, 50), 'Methods\nObserved duration 14 h.')
        doc.new_page().insert_text((50, 50), 'Results\nMeasured duration 55 h.')
        data = doc.tobytes()
    config = ScholarV4Config(api_key='offline')
    old_progress, new_progress = {}, {}
    old = v5._pdf_chunks(data, config, page_start=2, page_limit=1, progress=old_progress)
    new = v5.pdf_chunks(data, config, page_start=2, page_limit=1, progress=new_progress)
    assert [c.model_dump() for c in old[0]] == [c.model_dump() for c in new[0]]
    assert old_progress == new_progress and old[1] == new[1]
    assert all(c.page == 2 and not hasattr(c, 'figure') for c in new[0])


def test_existing_pdf_table_cells_and_coordinates_are_preserved():
    import fitz
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 50), 'Methods')
        for x in (50, 180, 320):
            page.draw_line((x, 100), (x, 150))
        for y in (100, 125, 150):
            page.draw_line((50, y), (320, y))
        for x, y, value in [(55, 117, 'Sample'), (185, 117, 'Duration (h)'), (55, 142, 'A'), (185, 142, '14')]:
            page.insert_text((x, y), value, fontsize=10)
        data = doc.tobytes()
    old, _ = v5._pdf_chunks(data)
    new, _ = v5.pdf_chunks(data)
    assert [c.model_dump() for c in old] == [c.model_dump() for c in new]
    t = next(c for c in new if c.table)
    assert t.page == 1 and t.table['rows'] == [['A', '14']]
    assert t.table['headers'] == ['Sample', 'Duration (h)'] and t.table['bbox']


def test_existing_saved_passages_are_not_rewritten_or_migrated():
    state = case('stokes')['state']
    before = copy.deepcopy(state)
    a = Article.model_validate(state['articles'][next(a for a in state['articles'] if state['articles'][a].get('fulltext', {}).get('text'))])
    assert passages(a) == v4_passages(a)
    v5.xml_document(jats(table(), '<floats-group>'+figure()+'</floats-group>'), URL)
    assert state == before  # No migration or source/analysis side effect.


def v4_passages(article):
    from agents.EnnoScholarV4.evidence import passages as original
    return original(article)


def test_known_N05_identity_effect_is_neither_fixed_nor_amplified():
    from .test_graph import STOKES
    state = case('stokes')['state']
    original = Article.model_validate(state['articles'][STOKES])
    before = copy.deepcopy(original.durable())
    extended = original.model_copy(deep=True)
    extended.fulltext['chunks'].append({'page': 29, 'section': 'METHODS DETAILS', 'text': 'Additional archived methods.'})
    extended.fulltext['text'] = '\n\n'.join(c['text'] for c in extended.fulltext['chunks'])
    assert passages(original) == v4_passages(original)
    assert passages(extended) == v4_passages(extended)
    changed_v4 = {p['evidence_id'] for p in v4_passages(original)} - {p['evidence_id'] for p in v4_passages(extended)}
    changed_v5 = {p['evidence_id'] for p in passages(original)} - {p['evidence_id'] for p in passages(extended)}
    assert changed_v5 == changed_v4 and changed_v5  # Known N05 remains outside this change.
    assert original.durable() == before


def test_truncated_acquisition_reports_partial_content(monkeypatch):
    import time
    from agents.EnnoScholarV5.tools import fulltext
    body = jats(prose=PROSE+'Duration 14 h.')
    monkeypatch.setattr(fulltext.base, 'fetch_public', lambda url, *args, **kwargs: (body, 'application/xml', URL))
    article = Article(id='paper_partial', title=TITLE, doi=DOI, authors=['Ada Author'], year=2024,
        url=URL, verified=True, verification_sources=[URL])
    config = replace(ScholarV4Config(api_key='offline'), max_extracted_chars=len(PROSE+'Duration 1'))
    result = fulltext.get_fulltext(article, Mock(), time.monotonic()+60, config=config)
    assert result['truncated'] and result['content_scope'] == 'partial_scientific_text'
    assert 'incomplet' in result['reason'] and result['document_version']['document_sha256'] == hashlib.sha256(body).hexdigest()
    assert sum(len(c['text']) for c in result['chunks']) <= config.max_extracted_chars
    assert not result['chunks'][-1]['text'].endswith('Duration 1')


def test_acquisition_to_versioned_table_and_figure_reading_through_langgraph(app_env, monkeypatch):
    from agents.EnnoScholarV5.tools import fulltext
    make, factory, _ = app_env
    state = copy.deepcopy(case('chemistry')['state'])
    aid = 'paper_extraction_step1'
    state['articles'][aid] = Article(id=aid, canonical_work_id=aid, title=TITLE, doi=DOI,
        authors=['Ada Author'], year=2024, url=URL, verified=True, verification_sources=[URL]).durable()
    state['turn_goal'] = {'deliverable': 'deep_reading', 'article_ids': [aid],
        'references': {'article_ids': [aid], 'scope': 'exclusive'}}
    body = jats(table('TS1', 'Table S1'), '<floats-group>'+figure()+'</floats-group>')
    fetched = []
    def fetch(url, *args, **kwargs):
        fetched.append(url)
        return body, 'application/xml', URL
    monkeypatch.setattr(fulltext.base, 'fetch_public', fetch)
    sources = Mock()
    sources.verify.side_effect = lambda article, *args, **kwargs: article
    acquired = {'article_id': aid, 'candidate_urls': None, 'page_start': None, 'page_limit': None}
    reading = {'article_ids': [aid], 'action': 'read', 'research_context': 'Tables and figure references only.',
        'passage_query': '', 'passage_offset': 0, 'passages_per_article': 2, 'analysis': None}
    def read_figure(sent):
        saved = scientific_state(factory, sid)
        rows = passages(Article.model_validate(saved['articles'][aid]))
        eids = [e['evidence_id'] for e in rows if 'fig2.png' in e['exact_quote']]
        assert eids
        return call('analyze_article', {**reading, 'evidence_ids': eids[:1]}, 'figure')
    def finish(sent):
        saved = scientific_state(factory, sid)
        ids = [eid for eid in saved['shown_evidence_ids'] if saved['evidence'][eid]['article_id'] == aid]
        return call('finalize_response', {'answer': 'Tableau et légende consultés ; aucune valeur visuelle inférée. '
            f'[{aid}] '+ ' '.join(f'[{e}]' for e in ids), 'article_ids': [aid], 'evidence_ids': ids,
            'coverage_sufficient': False, 'remaining_uncertainties': ['Image non analysée.'], 'analyses': None,
            'displayed_article_ids': None}, 'final')
    svc, sid, transport = make([call('get_fulltext', acquired, 'acquire'),
        call('analyze_article', {**reading, 'table_number': 'S1'}, 'table'), read_figure,
        call('get_fulltext', acquired, 'reuse'), finish], state, sources=sources)
    result = run_sync(svc, sid, 'Consulte le tableau S1 et la légende de Figure 2 de cet article.')
    saved = scientific_state(factory, sid)
    article = Article.model_validate(saved['articles'][aid])
    assert fetched == [URL] and len(transport.requests) == 5
    assert not article.analysis and result['coverage_sufficient'] is False
    assert article.fulltext['document_version']['document_sha256'] == hashlib.sha256(body).hexdigest()
    snap = snapshot(article)
    figure_chunk = next(c for c in snap['chunks'] if c.get('figure'))
    assert figure_chunk['figure']['publication_doi'] == DOI and figure_chunk['figure']['id'] == 'F2'
    proofs = [saved['evidence'][eid] for eid in saved['shown_evidence_ids'] if saved['evidence'][eid]['article_id'] == aid]
    table_proof = next(e for e in proofs if e.get('table'))
    assert table_proof['table']['number'] == 'S1' and table_proof['table']['label'] == 'Table S1'
    assert table_proof['table']['element_id'] == 'TS1' and table_proof['table']['row'] == ['A', '14']
    assert all(exact_source_valid(e, saved['source_snapshots']) for e in proofs)
    assert any('fig2.png' in e['exact_quote'] for e in proofs)
    assert all(e['page_number'] is None and e['document_version_id'] for e in proofs)
