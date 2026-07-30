from mcp_servers.legal_fulltext_mcp.infrastructure.http import _ScholarlyHtmlParser
from mcp_servers.legal_fulltext_mcp.infrastructure.landing_extractors import platform_pdf_candidates


def parse(base: str, body: str):
    parser = _ScholarlyHtmlParser(base)
    parser.feed(body)
    parser.finalize()
    return parser.pdf_urls


def test_standard_meta_and_embed_are_discovered():
    urls = parse(
        "https://journal.example/article/42",
        '<meta name="citation_pdf_url" content="/files/paper.pdf">'
        '<iframe src="/viewer/fulltext" type="application/pdf"></iframe>',
    )
    assert "https://journal.example/files/paper.pdf" in urls
    assert "https://journal.example/viewer/fulltext" in urls


def test_json_script_pdf_url_is_discovered():
    urls = parse(
        "https://journal.example/article/42",
        '<script type="application/ld+json">'
        '{"@type":"ScholarlyArticle","contentUrl":"/download/article.pdf"}'
        '</script>',
    )
    assert "https://journal.example/download/article.pdf" in urls


def test_mdpi_rule_is_platform_generic():
    urls = platform_pdf_candidates("https://www.mdpi.com/2079-9292/8/12/1388", [])
    assert "https://www.mdpi.com/2079-9292/8/12/1388/pdf" in urls


def test_spie_rule_is_platform_generic():
    urls = platform_pdf_candidates(
        "https://www.spiedigitallibrary.org/conference-proceedings-of-spie/11393/113930N/title/10.1117/12.2558258",
        [],
    )
    assert urls[0].endswith(".pdf")


def test_ieee_uses_document_number_from_current_url_only():
    urls = platform_pdf_candidates("https://ieeexplore.ieee.org/document/10246308", [])
    assert urls == ["https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=10246308"]
