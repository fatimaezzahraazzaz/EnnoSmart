from __future__ import annotations

from bs4 import FeatureNotFound

from mcp_servers.legal_fulltext_mcp.services import (
    generic_publisher_discovery as discovery,
)


def test_html_parser_falls_back_when_lxml_is_unavailable(monkeypatch) -> None:
    real_beautiful_soup = discovery.BeautifulSoup

    def parser(markup, features):
        if features == "lxml":
            raise FeatureNotFound("lxml unavailable")
        return real_beautiful_soup(markup, features)

    monkeypatch.setattr(discovery, "BeautifulSoup", parser)

    soup = discovery._parse_html(
        "<html><head><title>Article</title></head><body>Full text</body></html>"
    )

    assert soup.title.get_text(strip=True) == "Article"
    assert "Full text" in soup.get_text(" ", strip=True)
