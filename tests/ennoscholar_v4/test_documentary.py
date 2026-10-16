import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.sources import ScientificSources, V4ScientificSearch
from agents.EnnoScholarV4.store import ArticleStore
from agents.EnnoScholarV4.toolkit import ScholarTools
from agents.EnnoScholarV4.tools import fulltext
from agents.EnnoScholarV4.tools.extraction import html_document, xml_document
from tests.ennoscholar_v4.test_tools import tool_inputs


TITLE = "Verified scientific study of cellular segmentation"


def scientific_html(container="article", roman=False):
    sections = [("I Introduction", "II Methodology", "III Results", "IV Discussion") if roman else
                ("Introduction", "Methods", "Results", "Discussion")][0]
    return (f'<html><head><meta name="citation_title" content="{TITLE}"></head><body><{container}>' +
            "".join(f'<h2>{s}</h2><p>{s}: '+"Verified scientific content. "*45+"</p>" for s in sections) +
            f'</{container.split()[0]}></body></html>').encode()


@pytest.mark.parametrize("container,roman", [("article", True), ('div class="article fulltext-view"', False)])
def test_real_publisher_markup_keeps_methods_and_discussion(container, roman):
    _, chunks, truncated = html_document(scientific_html(container, roman), "https://example.org/paper")
    assert len(chunks) == 4 and not truncated
    assert any("Method" in c.section for c in chunks)
    assert any("Discussion" in c.section for c in chunks)
    assert all(c.page is None for c in chunks)


def test_notice_without_body_never_becomes_fulltext():
    _, chunks, _ = html_document(b'<html><h1>Paper</h1><div class="abstract">Abstract only.</div></html>', "https://example.org")
    assert chunks == []


def test_jats_body_and_identity_are_extracted_without_external_entities():
    data = ('<article><front><article-meta><title-group><article-title>'+TITLE+
            '</article-title></title-group><article-id pub-id-type="doi">10.1234/study</article-id></article-meta></front><body>'+
            '<sec><title>Materials and methods</title><p>'+"Verified methods. "*100+'</p></sec>'+
            '<sec><title>Results</title><p>'+"Verified results. "*100+'</p></sec></body></article>').encode()
    row, chunks, _ = xml_document(data, "https://example.org/article.xml")
    assert row.doi == "10.1234/study" and len(chunks) == 2
    assert all(c.page is None for c in chunks)


def test_openalex_alternatives_survive_normalization_and_storage():
    sources = ScientificSources()
    raw = {"id": "https://openalex.org/W1", "title": TITLE, "doi": "https://doi.org/10.1234/study",
        "locations": [{"is_oa": True, "version": "publishedVersion", "landing_page_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/", "pdf_url": "https://repository.example/paper.pdf"}]}
    article = sources.normalize(sources.client._openalex(raw))
    store = ArticleStore({}); ids, _ = store.insert([article])
    assert len(store.article(ids[0]).document_candidates) == 2
    assert "https://repository.example/paper.pdf" in store.article(ids[0]).work_urls
    assert any("fullTextXML" in u for u in fulltext.document_urls(article))


def test_accessible_document_is_durable_and_readable_not_automatically_analyzed(monkeypatch):
    sources = Mock(); sources.metadata.return_value = []
    toolkit = ScholarTools({"articles": {}, "messages": []}, sources, ScholarV4Config(api_key="mock"))
    ids, _ = toolkit.store.insert([Article(title=TITLE, doi="10.1234/study", url="https://example.org/paper", abstract="An abstract")])
    monkeypatch.setattr(fulltext, "fetch_public", lambda *a, **k: (scientific_html(roman=True), "text/html", "https://example.org/paper"))
    result = toolkit.execute("get_fulltext", {"article_id": ids[0], "candidate_urls": None, "page_start": None, "page_limit": None}, time.monotonic()+10)
    assert result["access_status"] == "fulltext_extracted" and toolkit.registry[ids[0]].analysis_basis is None
    restored = ScholarTools(toolkit.state, sources, toolkit.config)
    reading = restored.execute("analyze_article", {"article_ids": ids, "action": "read", "research_context": "segmentation", "passage_query": "Discussion", "passage_offset": 0, "passages_per_article": 8, "analysis": None}, time.monotonic()+10)
    assert any(p["basis"] == "fulltext" and "Discussion" in p["section"] for p in reading["reading"][0]["passages"])


def test_captcha_is_rejected_and_alternative_is_tried(monkeypatch):
    article = Article(title=TITLE, abstract="Abstract", url="https://example.org/paper")
    def fetch(url, *args, **kwargs):
        if "blocked" in url:
            return b'<html><article>captcha</article></html>', "text/html", url
        return scientific_html(), "text/html", url
    monkeypatch.setattr(fulltext, "fetch_public", fetch)
    result = fulltext.get_fulltext(article, Mock(), time.monotonic()+10, candidate_urls=["https://example.org/blocked", "https://repository.example/paper"])
    assert result["text"] and result["url"] == "https://repository.example/paper"
    assert result["attempts"][0]["reason"] == "AccessRestricted"


def test_transient_download_retry_is_bounded(monkeypatch):
    article = Article(title=TITLE, abstract="Abstract", url="https://example.org/paper")
    calls = []
    def fail(url, *a, **kw):
        calls.append(url)
        r = httpx.Response(429, headers={"Retry-After": "0"}, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("rate limited", request=r.request, response=r)
    sources = Mock(); sources.metadata.return_value = []
    monkeypatch.setattr(fulltext, "fetch_public", fail)
    result = fulltext.get_fulltext(article, sources, time.monotonic()+5)
    assert len(calls) == 2 and result["text"] == ""
    assert result["failure_stage"] == "download"


def test_article_comment_captcha_widget_does_not_hide_scientific_body(monkeypatch):
    article = Article(title=TITLE, url="https://example.org/paper")
    data = scientific_html().replace(b'</head>', b'<script src="https://example.org/recaptcha.js"></script></head>')
    monkeypatch.setattr(fulltext, "fetch_public", lambda *a, **kw: (data, "text/html", article.url))
    result = fulltext.get_fulltext(article, Mock(), time.monotonic()+5)
    assert result["text"] and result["access_status"] == "fulltext_extracted"


def test_actual_preprint_version_not_inherited_from_publisher_doi():
    from agents.EnnoScholarV4.provenance import document_identity
    from agents.EnnoScholarV4.evidence import snapshot, passages, exact_source_valid
    from agents.EnnoScholarV4.context import evidence_projection
    data = scientific_html().replace(b'</head>', b'<meta name="citation_doi" content="10.1109/published"></head>')
    _, chunks, _ = html_document(data, "https://arxiv.org/html/2409.13988v1")
    version = document_identity(data, "https://arxiv.org/html/2409.13988v1", "html", chunks)
    assert version["label"] == "arxiv:2409.13988v1"
    assert version["identifiers"]["related_publication_doi"] == "10.1109/published"
    a = Article(id="paper_test", title=TITLE, doi="10.1109/published", source_version="10.1109/published",
        fulltext={"url": version["url"], "text": "\n\n".join(c.text for c in chunks), "chunks": [c.model_dump() for c in chunks], "document_version": version})
    s = snapshot(a); e = passages(a)[0]
    assert exact_source_valid(e, {s["fingerprint"]: s})
    for change in [{"source_version": "10.1109/published"}, {"document_sha256": "wrong"},
                   {"source_identifiers": {"arxiv": "2409.13988v2"}}, {"source_article_id": "wrong"},
                   {"exact_quote": "invented quotation"}]:
        assert not exact_source_valid({**e, **change}, {s["fingerprint"]: s})
    assert evidence_projection(e)["source_version"] == version["label"]


def test_biorxiv_identity_separates_related_journal_doi_and_rejects_lookalike_host():
    from agents.EnnoScholarV4.provenance import document_identity
    data = scientific_html().replace(b'</head>', b'<meta name="citation_doi" content="10.1234/published"></head>')
    url = 'https://www.biorxiv.org/content/10.1101/2020.02.02.931238v1.full'
    _, chunks, _ = html_document(data, url)
    version = document_identity(data, url, 'html', chunks)
    assert version['identifiers']['doi'] == '10.1101/2020.02.02.931238'
    assert version['identifiers']['related_publication_doi'] == '10.1234/published'
    plain = scientific_html()
    _, chunks, _ = html_document(plain, url)
    spoof = document_identity(plain, url.replace('www.biorxiv.org', 'notbiorxiv.org'), 'html', chunks)
    assert 'biorxiv' not in spoof['identifiers'] and spoof['version_status'] == 'identifier_unverified'


def test_legacy_evidence_stays_readable_with_version_explicitly_unverified():
    import hashlib
    from agents.EnnoScholarV4.evidence import exact_source_valid
    from agents.EnnoScholarV4.context import evidence_projection
    text = "A legacy exact scientific passage."
    source = {"article_id": "paper_old", "url": "https://example.org/old", "text": text,
        "chunks": [{"text": text, "section": "Discussion", "page": None}]}
    digest = hashlib.sha256((source["url"]+"\n"+text).encode()).hexdigest()
    e = {"source_fingerprint": digest, "source_article_id": "paper_old", "source_url": source["url"],
        "chunk_index": 0, "char_offset": 0, "exact_quote": text, "page_number": None,
        "section": "Discussion", "source_version": "10.1234/old-notice"}
    assert exact_source_valid(e, {digest: source})
    assert evidence_projection(e)["version_status"] == "legacy_unverified"
    assert evidence_projection(e)["source_version"] == "legacy_unverified"


def test_document_versions_and_snapshots_are_independent_after_reload():
    import copy
    from agents.EnnoScholarV4.evidence import snapshot
    a = Article(id="paper_test", title=TITLE, fulltext={"url": "https://example.org/v1", "text": "old",
        "chunks": [{"text": "old", "section": "Methods", "page": None}]})
    old = snapshot(a)
    a.fulltext["chunks"][0]["text"] = "new"
    assert old["chunks"][0]["text"] == "old"
    a.documents = {"v1": copy.deepcopy(old), "v2": {"text": "new", "url": "https://example.org/v2"}}
    restored = Article.model_validate(a.durable())
    assert restored.documents["v1"]["text"] == "old" and restored.documents["v2"]["text"] == "new"


def scientific_pdf(printed=True):
    import fitz
    with fitz.open() as doc:
        for i in range(2):
            p = doc.new_page()
            p.insert_text((40, 80), TITLE+"\nDiscussion")
            p.insert_textbox(fitz.Rect(40, 125, 520, 700), "A verified scientific passage about overlap. "*35, fontsize=10)
            if printed:
                p.insert_text((280, p.rect.height-25), str(1446+i))
        doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 10}])
        return doc.tobytes()


def test_pdf_position_label_and_printed_page_are_distinct():
    from agents.EnnoScholarV4.tools.extraction import pdf_chunks
    chunks, _ = pdf_chunks(scientific_pdf())
    first = next(c for c in chunks if c.page == 1)
    assert first.printed_page == "1446" and first.pdf_page_label == "10"
    assert first.page_verification == "margin_text"
    chunks, _ = pdf_chunks(scientific_pdf(False))
    assert all(c.printed_page is None and c.page_verification == "not_verified" for c in chunks)


def test_pdf_upgrade_keeps_html_version_and_its_unpaginated_evidence(monkeypatch, tmp_path):
    from agents.EnnoScholarV4.evidence import exact_source_valid
    monkeypatch.setenv("ENNOSCHOLAR_V4_PDF_DIRECTORY", str(tmp_path))
    sources = Mock(); sources.metadata.return_value = []
    t = ScholarTools({"articles": {}, "messages": []}, sources, ScholarV4Config())
    ids, _ = t.store.insert([Article(title=TITLE, url="https://example.org/paper", pdf_url="https://example.org/paper.pdf")])
    html = scientific_html()
    pdf = scientific_pdf()
    monkeypatch.setattr(fulltext, "fetch_public", lambda url, *a, **kw: (pdf, "application/pdf", url) if url.endswith('.pdf') else (html, "text/html", url))
    t.execute("get_fulltext", {"article_id": ids[0], "candidate_urls": ["https://example.org/paper"], "page_start": None, "page_limit": None}, time.monotonic()+10)
    t.execute("analyze_article", {"article_ids": ids, "action": "read", "research_context": "overlap", "passage_query": "Discussion", "passage_offset": 0, "passages_per_article": 8, "analysis": None}, time.monotonic()+10)
    old = next(iter(t.evidence.values()))
    t.execute("get_fulltext", {"article_id": ids[0], "candidate_urls": None, "page_start": 1, "page_limit": 1}, time.monotonic()+10)
    assert len(t.registry[ids[0]].documents) == 2
    assert old["page_number"] is None and old["printed_page"] is None
    assert exact_source_valid(old, t.snapshots)
    reading = t.execute("analyze_article", {"article_ids": ids, "action": "read", "research_context": "overlap", "passage_query": "Discussion", "passage_offset": 0, "passages_per_article": 8, "analysis": None}, time.monotonic()+10)
    proof = next(p for p in reading['reading'][0]['passages'] if p['page_number']==1)
    assert proof['printed_page'] == "1446"
    raw = t.evidence[proof['evidence_id']]
    assert not exact_source_valid({**raw, "printed_page": "1447"}, t.snapshots)


def test_initial_page_request_keeps_accessible_html_and_document_identity(monkeypatch):
    sources = Mock(); sources.metadata.return_value = []
    toolkit = ScholarTools({'articles': {}, 'messages': []}, sources, ScholarV4Config())
    ids, _ = toolkit.store.insert([Article(title=TITLE, url='https://example.org/paper')])
    html = scientific_html()
    calls = []
    def fetch(url, *a, **kw):
        calls.append(url)
        return html, 'text/html', url
    monkeypatch.setattr(fulltext, 'fetch_public', fetch)
    result = toolkit.execute('get_fulltext', {'article_id': ids[0], 'candidate_urls': ['https://example.org/paper'],
        'page_start': 1, 'page_limit': 1}, time.monotonic()+10)
    assert result['source_kind']=='html' and result['access_status']=='fulltext_extracted'
    assert result['document_version']['document_sha256'] and 'pagination' in result['warning']
    restored = ScholarTools(toolkit.state, sources, toolkit.config)
    reading = restored.execute('analyze_article', {'article_ids': ids, 'action': 'read', 'research_context': 'limits',
        'passage_query': 'Discussion', 'passage_offset': 0, 'passages_per_article': 8, 'analysis': None}, time.monotonic()+10)
    assert reading['reading'][0]['passages'] and all(e['page_number'] is None for e in reading['reading'][0]['passages'])
    assert calls == ['https://example.org/paper']


def test_structured_html_table_rows_are_citable_with_metric_dataset_and_method():
    from agents.EnnoScholarV4.evidence import passages, snapshot, exact_source_valid
    tables = '<figure id="table2"><figcaption>Table 2: AUROC % on AeBAD-S dataset.</figcaption><table><tr><th>Method</th><th>Mean</th></tr><tr><td>PatchCore</td><td>71.0</td></tr><tr><td>MMR</td><td>84.7</td></tr></table></figure>'
    data = scientific_html().replace(b'</article>', tables.encode()+b'</article>')
    _, chunks, _ = html_document(data, "https://arxiv.org/html/2304.02216v2")
    table_chunk = next(c for c in chunks if getattr(c, 'table', None))
    assert table_chunk.table['rows'] == [['PatchCore', '71.0'], ['MMR', '84.7']]
    a = Article(id="paper_table", title=TITLE, fulltext={"url": "https://example.org/paper", "text": '\n\n'.join(c.text for c in chunks), "chunks": [c.model_dump() for c in chunks]})
    s = snapshot(a)
    proofs = [e for e in passages(a) if e.get('table')]
    assert len(proofs) == 2 and proofs[0]['table']['dataset'] == 'AeBAD-S'
    assert proofs[0]['table']['metric_labels'] == ['AUROC'] and proofs[0]['table']['unit'] == '%'
    assert all(exact_source_valid(e, {s['fingerprint']: s}) for e in proofs)
    wrong = {**proofs[0], 'table': {**proofs[0]['table'], 'row': ['MMR', '71.0']}}
    assert not exact_source_valid(wrong, {s['fingerprint']: s})


def test_merged_html_table_keeps_uncertainty_instead_of_column_associations():
    from bs4 import BeautifulSoup
    from agents.EnnoScholarV4.tools.tables import html_table
    table = BeautifulSoup('<table><tr><th>Method</th><th colspan="2">Score</th></tr><tr><td>MMR</td><td>84.7</td><td>89.1</td></tr></table>', 'html.parser').table
    result = html_table(table)
    assert result['structure_status'] == 'ambiguous' and result['conditions'] == []
    assert result['raw_rows'][1] == ['MMR', '84.7', '89.1']


def test_pdf_table_geometry_retains_literal_rows():
    import fitz
    from agents.EnnoScholarV4.tools.extraction import pdf_chunks
    with fitz.open() as doc:
        p = doc.new_page()
        p.insert_text((50, 80), TITLE+'\nResults\nTable 2: AUROC % on AeBAD-S dataset.')
        for x in [50, 260, 400]:
            p.draw_line((x, 150), (x, 240))
        for y in [150, 180, 210, 240]:
            p.draw_line((50, y), (400, y))
        for i, row in enumerate([['Method', 'Mean'], ['PatchCore', '71.0'], ['MMR', '84.7']]):
            for j, cell in enumerate(row):
                p.insert_text((60 if j == 0 else 270, 170+i*30), cell)
        data = doc.tobytes()
    chunks, _ = pdf_chunks(data)
    table = next(c.table for c in chunks if getattr(c, 'table', None))
    assert table['structure_status'] == 'verified_cells'
    assert table['rows'] == [['PatchCore', '71.0'], ['MMR', '84.7']]


def test_pdf_table_methods_header_does_not_replace_roman_scientific_section():
    import fitz
    from agents.EnnoScholarV4.tools.extraction import pdf_chunks
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'III. EXPERIMENTS AND RESULTS')
        page.insert_text((50, 110), 'Extended Data Fig. 9b). Notably, the results remain experimental.')
        for x in [50, 260, 400]:
            page.draw_line((x, 150), (x, 240))
        for y in [150, 180, 210, 240]:
            page.draw_line((50, y), (400, y))
        for i, row in enumerate([['Methods','Mean'], ['PatchCore','71.0'], ['MMR','84.7']]):
            for j, cell in enumerate(row):
                page.insert_text((60+j*210, 170+i*30), cell)
        page.insert_text((50, 280), 'The experimental results require appropriate dataset settings.')
        chunks, _ = pdf_chunks(doc.tobytes())
    prose = next(c for c in chunks if 'experimental results require' in c.text)
    assert prose.section == 'III. EXPERIMENTS AND RESULTS'


@pytest.mark.parametrize('missing_cell', [False, True])
def test_horizontal_rule_pdf_tables_keep_last_row_and_reject_missing_cells(missing_cell):
    import fitz
    from agents.EnnoScholarV4.tools.extraction import pdf_chunks
    from agents.EnnoScholarV4.evidence import passages, snapshot, exact_source_valid
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 70), TITLE)
        for number, metric, values, top in [('2', 'AUROC', ['71.0', '84.7'], 130), ('3', 'PRO', ['87.8', '89.1'], 280)]:
            page.insert_text((40, top-25), f'Table {number}. {metric} % on AeBAD-S dataset. Five trials.')
            for y in [top, top+25, top+75]:
                page.draw_line((40, y), (400, y))
            rows = [['Method', 'Same', 'Mean'], ['PatchCore', '75.2', values[0]], ['MMR', '85.6', values[1]]]
            for i, row in enumerate(rows):
                for j, cell in enumerate(row):
                    if missing_cell and number == '3' and i == 2 and j == 1:
                        continue
                    page.insert_text((50+j*120, top+18+i*25), cell, fontsize=9)
        data = doc.tobytes()
    chunks, _ = pdf_chunks(data)
    tables = {c.table['number']: c.table for c in chunks if getattr(c, 'table', None)}
    assert tables['2']['structure_status'] == 'verified_cells'
    assert tables['2']['rows'][-1] == ['MMR', '85.6', '84.7']
    assert tables['3']['structure_status'] == ('ambiguous' if missing_cell else 'verified_cells')
    article = Article(id='paper_table', title=TITLE, fulltext={'url': 'https://example.org/paper.pdf',
        'text': '\n\n'.join(c.text for c in chunks), 'chunks': [c.model_dump() for c in chunks]})
    source = snapshot(article)
    proofs = [e for e in passages(article) if e.get('table')]
    assert all(exact_source_valid(e, {source['fingerprint']: source}) for e in proofs)
    if not missing_cell:
        assert any(e['table']['number']=='3' and e['table']['row']==['MMR','85.6','89.1'] for e in proofs)
    else:
        assert not any(e['table']['number']=='3' for e in proofs)


def test_table_number_selects_only_requested_document_rows():
    from agents.EnnoScholarV4.tools.tables import structure, table_text
    t = ScholarTools({"articles": {}, "messages": []}, Mock(), ScholarV4Config())
    ids, _ = t.store.insert([Article(title=TITLE, url="https://example.org/paper")])
    chunks = []
    for number, metric, values in [('2', 'AUROC', ['71.0','84.7']), ('3','PRO',['87.8','89.1'])]:
        table = structure(['Method','Mean'], [['PatchCore', values[0]], ['MMR',values[1]]], f'Table {number}: {metric} % on AeBAD-S dataset.', number)
        chunks.append({'text': table_text(table), 'section': 'Results', 'page': None, 'table': table})
    t.registry[ids[0]].fulltext = {'url': 'https://example.org/paper', 'chunks': chunks, 'text': '\n\n'.join(c['text'] for c in chunks)}
    result = t.execute('analyze_article', {'article_ids': ids, 'action':'read','research_context':'comparison','passage_query':'MMR PatchCore', 'passage_offset':0,'passages_per_article':8,'analysis':None,'table_number':'3'}, time.monotonic()+10)
    proofs = result['reading'][0]['passages']
    assert len(proofs)==2 and all(p['table']['number']=='3' for p in proofs)
    assert {p['table']['row'][-1] for p in proofs} == {'87.8','89.1'}


def test_publication_admits_actual_document_doi_only_with_verified_shown_evidence(monkeypatch):
    sources = Mock(); sources.verify.side_effect = lambda a,*args:a.model_copy(update={"verified":True})
    t = ScholarTools({'articles':{},'messages':[]}, sources, ScholarV4Config())
    ids,_=t.store.insert([Article(title=TITLE,doi='10.1234/published',url='https://example.org/paper',verified=True,authors=['Author'],year=2025)])
    data=scientific_html().replace(b'</head>',b'<meta name="citation_doi" content="10.1234/preprint"></head>')
    monkeypatch.setattr(fulltext,'fetch_public',lambda *a,**k:(data,'text/html','https://example.org/preprint'))
    t.execute('get_fulltext',{'article_id':ids[0],'candidate_urls':None,'page_start':None,'page_limit':None},time.monotonic()+10)
    reading=t.execute('analyze_article',{'article_ids':ids,'action':'read','research_context':'scientific limit','passage_query':'Discussion','passage_offset':0,'passages_per_article':8,'analysis':None},time.monotonic()+10)
    t.delivered([*[w['window_id'] for w in t.store.windows],*[w['window_id'] for w in t.reading_windows]], inputs=tool_inputs(reading))
    eid=next(iter(t.evidence))
    args={'answer':f'Version consultÃ©e DOI 10.1234/preprint [{ids[0]}] [{eid}]','article_ids':ids,'evidence_ids':[eid],'coverage_sufficient':False,'remaining_uncertainties':[]}
    assert t.execute('finalize_response',args,time.monotonic()+10)['published']
    args['answer']=f'DOI inventÃ© 10.1234/invented [{ids[0]}] [{eid}]'
    assert t.execute('finalize_response',args,time.monotonic()+10)['rejection_code']=='unknown_doi'
