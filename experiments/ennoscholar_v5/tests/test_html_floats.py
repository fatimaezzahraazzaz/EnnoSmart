"""Generic HTML tables/figures and exact passage reading: synthetic fixtures, no live transport.

Each fixture is a reusable publishing structure (NCBI-style section wrappers, <figure>/<figcaption>,
<table><caption>, div wrappers with caption/label classes, <picture>/srcset, sub-figures, captionless
tables, decorative images, articles without floats). No real identifier is used.
"""
import copy
import json
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.tools import extraction as v4
from agents.EnnoScholarV5.tools import extraction as v5
from agents.EnnoScholarV5.tools import tables as v5_tables
from agents.EnnoScholarV5.evidence import passages, exact_source_valid
from agents.EnnoScholarV5.models import Article
from .test_graph import case
from .test_live_integration import app_env, call, run_sync, scientific_state

URL = 'https://fixture.invalid/article.html'
PROSE = 'Scientific methods and experimental observations under documented conditions. ' * 16
ROWS = ('<table><thead><tr><th>Sample</th><th>R0/Ω</th><th>I0/mA</th></tr></thead>'
        '<tbody><tr><td>A</td><td>396.937</td><td>0.02124</td></tr><tr><td>B</td><td>699.096</td><td>0.0126</td></tr></tbody></table>')


def page(*floats, before=PROSE, after=PROSE, doi=''):
    meta = f'<meta name="citation_doi" content="{doi}">' if doi else ''
    return ('<html><head><title>Fixture</title>'+meta+'</head><body><article><h2>1. Methods</h2><p>'+before+'</p>'
        +''.join(floats)+'<h2>2. Results</h2><p>'+after+'</p></article></body></html>').encode()


def extract(html, module=v5):
    _, chunks, truncated = module.html_document(html, URL)
    return chunks, truncated


def dumps(chunks):
    return [c.model_dump() for c in chunks]


def tables_of(chunks):
    return [c for c in chunks if getattr(c, 'table', None)]


def figures_of(chunks):
    return [c for c in chunks if getattr(c, 'figure', None)]


# --- Tables -----------------------------------------------------------------------------------

SECTION_WRAPPED = ('<section class="tw" id="tbl-a"><h4 class="obj_head">Table 1.</h4>'
    '<div class="caption p"><p>Measured transport parameters at room temperature.</p></div>'
    '<div class="tbl-box">'+ROWS+'</div><div class="tw-foot"><p>Values are means of three cells.</p></div></section>')
DIV_WRAPPED = ('<div class="table-wrapper" id="tbl-b"><div class="table-label">Table 4</div>'
    '<div class="table-caption">Impedance summary.</div>'+ROWS+'</div>')
LABEL_AND_CAPTION_IN_HEADING = '<div class="tableContainer"><h3>Table S2: Supplementary impedance values.</h3>'+ROWS+'</div>'
ROMAN = '<div class="table"><span class="label">Table IV</span><p class="caption">Roman numbered table.</p>'+ROWS+'</div>'


@pytest.mark.parametrize('markup,label,number,caption,element_id', [
    (SECTION_WRAPPED, 'Table 1', '1', 'Measured transport parameters at room temperature.', 'tbl-a'),
    (DIV_WRAPPED, 'Table 4', '4', 'Impedance summary.', 'tbl-b'),
    (LABEL_AND_CAPTION_IN_HEADING, 'Table S2', 'S2', 'Supplementary impedance values.', ''),
    (ROMAN, 'Table IV', 'IV', 'Roman numbered table.', ''),
])
def test_wrapped_html_tables_keep_number_label_caption_and_cells(markup, label, number, caption, element_id):
    chunks, _ = extract(page(markup))
    [chunk] = tables_of(chunks)
    t = chunk.table
    assert (t['label'], t['number'], t['caption'], t['element_id']) == (label, number, caption, element_id)
    assert t['structure_status'] == 'verified_cells' and t['rows'][0] == ['A', '396.937', '0.02124']
    assert t['headers'] == ['Sample', 'R0/Ω', 'I0/mA']
    assert chunk.text.startswith(label+'\n'+caption)
    assert chunk.section == '1. Methods / '+label
    # The label never opens a pseudo-section and the caption is not a detached paragraph.
    assert sum(caption in c.text for c in chunks) == 1
    assert not any(label in c.section and not getattr(c, 'table', None) for c in chunks
        if c.text != 'Values are means of three cells.')
    assert chunks[-1].section == '2. Results'


def test_table_notes_are_kept_next_to_their_table():
    chunks, _ = extract(page(SECTION_WRAPPED))
    i = next(i for i, c in enumerate(chunks) if getattr(c, 'table', None))
    assert chunks[i+1].text == 'Values are means of three cells.' and chunks[i+1].section == '1. Methods / Table 1'


@pytest.mark.parametrize('markup', [
    '<figure id="T"><figcaption>Table 3. Duration (h)</figcaption>'+ROWS+'</figure>',      # standard <figure>
    '<table><caption>Table 2. Native caption.</caption>'+ROWS[7:],                          # <table><caption>
    '<div class="c-article-table"><div class="c-article-table__caption">Table 5 Publisher caption</div>'+ROWS+'</div>',
    ROWS,                                                                                    # no caption at all
    '<div class="box">'+ROWS+'</div>',                                                       # wrapper without caption
])
def test_tables_v4_already_reads_or_without_caption_are_unchanged(markup):
    html = page(markup)
    assert dumps(extract(html)[0]) == dumps(extract(html, v4)[0])


@pytest.mark.parametrize('wrapper', [
    '<div class="content"><p>Context one.</p><div class="figure-caption">Figure 8. Unrelated figure caption.</div>{}</div>',
    '<section><p>One.</p><p>Two.</p><p>Three.</p><div class="caption">Table 9. Caption of another block.</div>{}</section>',
    '<section><h3>2.1 Subsection</h3><p class="caption">Table 9. Caption under a real heading.</p>{}</section>',
])
def test_captionless_table_never_takes_a_neighbouring_caption(wrapper):
    html = page(wrapper.format(ROWS))
    t = tables_of(extract(html)[0])[0].table
    assert t['caption'] == '' and t['number'] is None and 'label' not in t
    assert t == tables_of(extract(html, v4)[0])[0].table


def test_one_wrapper_never_merges_two_tables():
    html = page('<div class="tables"><h4>Table 6</h4>'+ROWS+ROWS+'</div>')
    assert [c.table.get('label') for c in tables_of(extract(html)[0])] == [None, None]


# --- Figures ----------------------------------------------------------------------------------

PMC_FIGURE = ('<figure class="fig" id="fig-a"><h4 class="obj_head">Figure 1.</h4><p class="img-box">'
    '<img class="graphic" src="https://cdn.fixture.invalid/blobs/fig-a.webp" alt="Figure 1"></p>'
    '<div class="p"><a href="/figure/fig-a/">Open in a new tab</a></div>'
    '<figcaption><p>(a) Diffraction patterns; (b) spectra.</p></figcaption></figure>')
STANDARD_FIGURE = ('<figure id="F2"><img src="images/fig2.png" srcset="images/fig2@2x.png 2x">'
    '<figcaption>Figure 2: Cycling performance at 0.5 C.</figcaption></figure>')
DIV_FIGURE = ('<div class="figure" id="F3"><picture><source srcset="fig3.avif 1x, fig3@2x.avif 2x">'
    '<img data-src="fig3.jpg" src="data:image/gif;base64,AAAA"></picture>'
    '<div class="figure-caption"><span class="label">Fig. 3</span> Morphology of the membrane.</div></div>')
SUBFIGURES = ('<figure id="F4"><figure><img src="f4a.png"><figcaption>(a) First panel.</figcaption></figure>'
    '<figure><img src="f4b.png"><figcaption>(b) Second panel.</figcaption></figure>'
    '<figcaption>Figure 4. Two panels.</figcaption></figure>')
NO_IMAGE = '<figure id="F5"><figcaption>Figure S5. Caption only, no graphic element.</figcaption></figure>'
LINKED = '<div class="fig" id="F6"><a href="full/fig6.tif"><span>Download</span></a><div class="caption">Figure 6. Linked high resolution file.</div></div>'


@pytest.mark.parametrize('markup,label,number,caption,refs', [
    (PMC_FIGURE, 'Figure 1', '1', '(a) Diffraction patterns; (b) spectra.', ['https://cdn.fixture.invalid/blobs/fig-a.webp']),
    (STANDARD_FIGURE, 'Figure 2', '2', 'Figure 2: Cycling performance at 0.5 C.', ['images/fig2.png', 'images/fig2@2x.png']),
    (DIV_FIGURE, 'Fig. 3', '3', 'Fig. 3 Morphology of the membrane.', ['fig3.avif', 'fig3.jpg']),
    (NO_IMAGE, 'Figure S5', 'S5', 'Figure S5. Caption only, no graphic element.', []),
    (LINKED, 'Figure 6', '6', 'Figure 6. Linked high resolution file.', ['full/fig6.tif']),
])
def test_figures_keep_literal_label_caption_and_references(markup, label, number, caption, refs):
    chunks, _ = extract(page(markup))
    [chunk] = figures_of(chunks)
    f = chunk.figure
    assert (f['label'], f['number'], f['caption'], f['graphic_refs']) == (label, number, caption, refs)
    assert f['source_url'] == URL and caption in chunk.text and all(r in chunk.text for r in refs)
    assert chunk.section == '1. Methods / '+label
    assert sum(caption in c.text for c in chunks) == 1  # no detached duplicate caption
    assert 'Open in a new tab' not in ' '.join(c.text for c in chunks)
    assert chunks[-1].section == '2. Results'


def test_subfigures_are_one_figure_with_all_panels_and_references():
    [chunk] = figures_of(extract(page(SUBFIGURES))[0])
    f = chunk.figure
    assert f['label'] == 'Figure 4' and f['caption'] == 'Figure 4. Two panels.'
    assert f['graphic_refs'] == ['f4a.png', 'f4b.png']
    assert '(a) First panel.' in chunk.text and '(b) Second panel.' in chunk.text


@pytest.mark.parametrize('markup', [
    '<p>Text with an inline icon <img src="icon.svg"> inside a sentence.</p>',
    '<figure><img src="decorative.png"></figure>',
    '<div class="banner"><img src="logo.png"></div>',
])
def test_media_without_label_or_caption_create_no_figure(markup):
    html = page(markup)
    assert dumps(extract(html)[0]) == dumps(extract(html, v4)[0])


def test_article_without_tables_or_figures_is_identical_to_v4():
    html = page('<h3>1.1 Details</h3><ul><li>First item 14 h.</li><li>Second item.</li></ul>'
        '<p><math alttext="x^2"><mi>x</mi></math> inline.</p><math alttext="E=mc^2"><mi>E</mi></math>')
    assert dumps(extract(html)[0]) == dumps(extract(html, v4)[0])
    assert extract(html)[1] == extract(html, v4)[1]


def test_equation_tables_keep_the_equation_path():
    html = page('<table class="ltx_equation"><tr><td>a = b + c</td></tr></table>')
    assert dumps(extract(html)[0]) == dumps(extract(html, v4)[0])


def test_mixed_page_keeps_all_v4_text_and_document_order():
    html = page(PMC_FIGURE, '<p>Between the floats.</p>', SECTION_WRAPPED, STANDARD_FIGURE)
    new, old = extract(html)[0], extract(html, v4)[0]
    text = '\n'.join(c.text for c in new)
    # Every V4 line survives, except V4's placeholder now replaced by the literal caption.
    assert all(line in text for c in old for line in c.text.split('\n')
        if line.strip() and line != 'Table (caption unavailable)')
    # V4 filed the next paragraph under the float label; V5 keeps it in its real section.
    assert next(c for c in new if c.text == 'Between the floats.').section == '1. Methods'
    kinds = [('figure' if getattr(c, 'figure', None) else 'table' if getattr(c, 'table', None) else 'text') for c in new]
    assert kinds.index('figure') < kinds.index('table') < len(kinds)-1-kinds[::-1].index('figure')


def test_jats_and_pdf_table_text_unchanged_by_label_prefix():
    plain = {'caption': 'Caption.', 'headers': ['A', 'B'], 'rows': [['1', '2']], 'structure_status': 'verified_cells', 'uncertainty': ''}
    assert v5_tables.table_text(plain) == v5_tables.base.table_text(plain)
    labelled = {**plain, 'label': 'Table 2'}
    assert v5_tables.table_text(labelled) == 'Table 2\n'+v5_tables.base.table_text(plain)
    assert v5_tables.table_text({**plain, 'caption': 'Table 2. Caption.', 'label': 'Table 2'}).startswith('Table 2. Caption.')


# --- Exact reading through the real tool path -------------------------------------------------

def test_read_by_ids_ignores_table_filter_and_unknown_number_is_explained(app_env, monkeypatch):
    from agents.EnnoScholarV5.tools import fulltext
    make, factory, _ = app_env
    state = copy.deepcopy(case('chemistry')['state'])
    aid = 'paper_html_floats'
    state['articles'][aid] = Article(id=aid, canonical_work_id=aid, title='Fixture', doi='10.9999/html-floats',
        authors=['Ada Author'], year=2024, url=URL, verified=True, verification_sources=[URL]).durable()
    state['turn_goal'] = {'deliverable': 'deep_reading', 'article_ids': [aid],
        'references': {'article_ids': [aid], 'scope': 'exclusive'}}
    body = page(PMC_FIGURE, SECTION_WRAPPED, doi='10.9999/html-floats')
    monkeypatch.setattr(fulltext.base, 'fetch_public', lambda url, *a, **k: (body, 'text/html; charset=utf-8', URL))
    sources = Mock()
    sources.verify.side_effect = lambda article, *args, **kwargs: article
    reading = {'article_ids': [aid], 'action': 'read', 'research_context': 'Légendes et tableau.',
        'passage_query': '', 'passage_offset': 0, 'passages_per_article': 4, 'analysis': None}
    seen = {'out': {}}

    def collect(sent):
        seen['out'].update({i['call_id']: json.loads(i['output']) for i in sent['input']
            if i.get('type') == 'function_call_output'})

    def step(tool, args, identifier):
        def respond(sent):
            collect(sent)
            return call(tool, args, identifier)
        return respond

    def by_ids(sent):
        collect(sent)
        rows = passages(Article.model_validate(scientific_state(factory, sid)['articles'][aid]))
        seen['figure'] = [r['evidence_id'] for r in rows if 'fig-a.webp' in r['exact_quote']]
        seen['note'] = [r['evidence_id'] for r in rows if r['exact_quote'] == 'Values are means of three cells.']
        assert seen['figure'] and seen['note']
        # Same shape as the failing real call: exact IDs of non-table passages plus a table number.
        return call('analyze_article', {**reading, 'table_number': '1', 'evidence_ids': seen['figure']+seen['note']}, 'ids')

    def finish(sent):
        collect(sent)
        outputs = seen['out']
        seen['ids'], seen['unknown'], seen['table'] = outputs['ids'], outputs['unknown'], outputs['table']
        saved = scientific_state(factory, sid)
        ids = [e for e in saved['shown_evidence_ids'] if saved['evidence'][e]['article_id'] == aid]
        return call('finalize_response', {'answer': f'Consulté. [{aid}] '+' '.join(f'[{e}]' for e in ids),
            'article_ids': [aid], 'evidence_ids': ids, 'coverage_sufficient': False,
            'remaining_uncertainties': ['Image non analysée.'], 'analyses': None, 'displayed_article_ids': None}, 'final')

    acquired = {'article_id': aid, 'candidate_urls': None, 'page_start': None, 'page_limit': None}
    svc, sid, transport = make([call('get_fulltext', acquired, 'acquire'), by_ids,
        step('analyze_article', {**reading, 'table_number': '9'}, 'unknown'),
        step('analyze_article', {**reading, 'table_number': '1'}, 'table'), finish], state, sources=sources)
    result = run_sync(svc, sid, 'Donne la légende du tableau 1 et la référence graphique de la figure 1.')
    assert 'ids' in seen, (len(transport.requests), sorted(seen['out']), result.get('answer', '')[:300],
        {k: str(v)[:300] for k, v in seen['out'].items()})
    shown = seen['ids']['output']['reading'][0]['passages'] if 'output' in seen['ids'] else seen['ids']['reading'][0]['passages']
    assert [p['evidence_id'] for p in shown] == seen['figure']+seen['note']
    unknown = (seen['unknown'].get('output') or seen['unknown'])['reading'][0]
    assert unknown['passages'] == [] and unknown['available_table_numbers'] == ['1']
    assert 'Aucun tableau numéroté 9' in unknown['guidance']
    table = (seen['table'].get('output') or seen['table'])['reading'][0]['passages']
    assert table and all(p['table']['number'] == '1' and p['table']['caption'].startswith('Measured transport') for p in table)
    saved = scientific_state(factory, sid)
    assert all(exact_source_valid(saved['evidence'][e], saved['source_snapshots']) for e in saved['shown_evidence_ids']
        if saved['evidence'][e]['article_id'] == aid)
