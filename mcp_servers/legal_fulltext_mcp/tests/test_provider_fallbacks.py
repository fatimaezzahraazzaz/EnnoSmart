from pathlib import Path

from mcp_servers.legal_fulltext_mcp.domain.models import ArticleIdentity
from mcp_servers.legal_fulltext_mcp.infrastructure.cache import SQLiteTTLCache
from mcp_servers.legal_fulltext_mcp.providers.arxiv import ArxivProvider
from mcp_servers.legal_fulltext_mcp.providers.crossref import _mdpi_static_pdf_urls
from mcp_servers.legal_fulltext_mcp.providers.unpaywall import _pdf_variants


def test_arxiv_queries_include_title_fallback() -> None:
    article = ArticleIdentity(
        doi="10.1234/example.2024",
        title="A Generic Scientific Paper About Document Retrieval",
        authors=["Alice Smith"],
    )
    queries = ArxivProvider._queries(article)
    assert queries[0] == "doi:10.1234/example.2024"
    assert any(query.startswith('ti:"A Generic Scientific Paper') for query in queries)


def test_publisher_variants_remove_temporary_query() -> None:
    variants = _pdf_variants(
        "https://www.mdpi.com/1234-5678/1/2/3/pdf?version=4",
        "https://www.mdpi.com/1234-5678/1/2/3",
    )
    assert "https://www.mdpi.com/1234-5678/1/2/3/pdf" in variants
    assert "https://www.mdpi.com/1234-5678/1/2/3/pdf-vor" in variants


def test_mdpi_static_cdn_candidates_are_derived_from_crossref_metadata() -> None:
    urls = _mdpi_static_pdf_urls(
        {
            "DOI": "10.3390/rs16234427",
            "container-title": ["Remote Sensing"],
            "volume": "16",
            "article-number": "4427",
        }
    )
    assert (
        "https://mdpi-res.com/d_attachment/remotesensing/"
        "remotesensing-16-04427/article_deploy/remotesensing-16-04427.pdf"
    ) in urls


def test_cache_delete(tmp_path: Path) -> None:
    cache = SQLiteTTLCache(str(tmp_path / "cache.sqlite3"), ttl_seconds=3600)
    cache.set("x", {"found": False})
    assert cache.get("x") == {"found": False}
    cache.delete("x")
    assert cache.get("x") is None
