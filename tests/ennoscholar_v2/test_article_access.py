from __future__ import annotations

import json
from types import SimpleNamespace

import fitz
import pytest

from conftest import evidence_reference
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.models import Article, FulltextResult, TextChunk
from agents.EnnoScholarV2.tools import article_reader
from agents.EnnoScholarV2.tools.article_analysis import analyze_article
from agents.EnnoScholarV2.tools.fulltext import get_fulltext
from agents.EnnoScholarV2.tools.scientific_search import ScientificSearch


def test_no_fulltext_keeps_abstract_only_and_paid_article(monkeypatch):
    search = ScientificSearch(ScholarConfig())
    monkeypatch.setattr(search, "lookup_doi", lambda doi: [])
    monkeypatch.setattr("agents.EnnoScholarV2.tools.fulltext.read_fulltext", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("Paywall")))
    article = Article(title="Relevant paid paper", abstract="We propose an approach.", doi="10.1234/paid", url="https://publisher.example/paper")
    result = get_fulltext(article, search)
    assert result.access_level == "abstract_only" and result.text == article.abstract
    assert "abstract" in result.reason and article.title == "Relevant paid paper"


def test_no_abstract_remains_metadata_only(monkeypatch):
    search = ScientificSearch(ScholarConfig())
    monkeypatch.setattr(search, "lookup_doi", lambda doi: [])
    result = get_fulltext(Article(title="Found paper"), search)
    assert result.access_level == "metadata_only" and result.text == ""


def test_public_pdf_really_extracted_with_page_and_sections(monkeypatch):
    with fitz.open() as document:
        page = document.new_page()
        text = "Neural mesh generation\nIntroduction\n" + ("We construct meshes with a learned model and evaluate geometric quality.\n" * 16)
        page.insert_textbox(fitz.Rect(30, 30, 560, 800), text, fontsize=10)
        data = document.tobytes()
    monkeypatch.setattr(article_reader, "fetch_url", lambda *args, **kwargs: (data, "application/pdf", "https://arxiv.org/pdf/2401.12345"))
    article = Article(title="Neural mesh generation", pdf_url="https://arxiv.org/pdf/2401.12345")
    result = get_fulltext(article, ScientificSearch(ScholarConfig()))
    assert result.access_level == "fulltext" and result.chunks[0].page == 1
    assert "Introduction" in result.text and result.fulltext_url == article.pdf_url


def test_html_landing_abstract_does_not_become_fulltext():
    data = b'<html><head><meta name="citation_title" content="Neural mesh generation"></head><body><article><h2>Abstract</h2><p>A proposed method.</p></article></body></html>'
    article, chunks, _ = article_reader.html_document(data, "https://example.org/paper")
    assert article.title == "Neural mesh generation" and chunks == []


def test_html_fulltext_preserves_sections(monkeypatch):
    data = ('<html><head><meta name="citation_title" content="Neural mesh generation"></head><body><article><h1>Neural mesh generation</h1><h2>Introduction</h2><p>' + "Neural mesh generation constructs meshes. " * 30 + '</p><h2>Limitations</h2><p>Generalization is uncertain.</p></article></body></html>').encode()
    monkeypatch.setattr(article_reader, "fetch_url", lambda *args, **kwargs: (data, "text/html", "https://example.org/paper"))
    result = article_reader.read_fulltext("https://example.org/paper", Article(title="Neural mesh generation"))
    assert result.access_level == "fulltext" and any(c.section == "Limitations" for c in result.chunks)


def test_mismatched_document_cannot_claim_fulltext(monkeypatch):
    data = ('<article><h2>Introduction</h2><p>' + 'A completely unrelated astronomical paper. ' * 40 + '</p><h2>Results</h2><p>Stars.</p></article>').encode()
    monkeypatch.setattr(article_reader, "fetch_url", lambda *args, **kwargs: (data, "text/html", "https://example.org/wrong"))
    with pytest.raises(ValueError, match="identité"):
        article_reader.read_fulltext("https://example.org/wrong", Article(title="Neural mesh generation"))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://localhost", "http://127.0.0.1", "http://169.254.169.254/latest", "http://example.org:8000", "https://user:secret@example.org"])
def test_reader_rejects_private_or_non_public_urls(monkeypatch, url):
    monkeypatch.setattr(article_reader.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(ValueError):
        article_reader.validate_public_url(url)


def test_abstract_analysis_removes_invented_results_and_fulltext_claim(fake_client):
    article = Article(title="Meshing method", abstract="We propose a neural meshing method.")
    fake_client.responses.create.side_effect = lambda **kwargs: SimpleNamespace(status="completed", output_text=json.dumps({
        "problem": "Construire des maillages", "method": "Méthode neuronale", "results": "99% accuracy", "limitations": ["Never fails"],
        "access_level": "fulltext", "analysis_basis": "fulltext", "evidence": [
            evidence_reference(json.loads(kwargs["input"]), "We propose a neural meshing method.", ["method"]),
            {"evidence_id": "unknown", "supports": ["results", "limitations"]}]}))
    result = analyze_article(article, None, fake_client, ScholarConfig(model="mock-model"))
    assert result.access_level == "abstract_only" and result.analysis_basis == "abstract"
    assert result.results == "" and result.problem == "" and result.limitations == []
    assert result.method and len(result.evidence) == 1 and "abstract uniquement" in result.analysis_limit


def test_fulltext_analysis_requires_actual_page_evidence(fake_client):
    fake_client.responses.create.side_effect = lambda **kwargs: SimpleNamespace(status="completed", output_text=json.dumps({"results": "Meshes were valid", "access_level": "fulltext", "analysis_basis": "fulltext",
        "evidence": [evidence_reference(json.loads(kwargs["input"]), "All generated meshes were valid.", ["results"])]}))
    full = FulltextResult(access_level="fulltext", text="All generated meshes were valid.", chunks=[TextChunk(page=4, section="Results", text="All generated meshes were valid.")])
    result = analyze_article(Article(title="Meshing"), full, fake_client, ScholarConfig(model="mock-model"))
    assert result.results and result.evidence[0].page == 4 and result.analysis_basis == "fulltext"


def test_metadata_analysis_makes_no_llm_call(fake_client):
    result = analyze_article(Article(title="Paper"), None, fake_client, ScholarConfig())
    assert result.analysis_basis == "metadata" and not result.results
    fake_client.responses.create.assert_not_called()


def test_selected_chunks_bound_budget_and_include_limitations():
    chunks = [TextChunk(page=i, section="Method", text="x" * 5000) for i in range(1, 50)]
    chunks.append(TextChunk(page=50, section="Limitations", text="Known failures. " * 500))
    selected = article_reader.select_chunks(chunks)
    assert sum(len(c.text) for c in selected) <= 24000
    assert any(c.section == "Limitations" for c in selected)


def test_arxiv_landing_url_is_resolved_to_pdf_without_v1_client(monkeypatch):
    attempted = []
    def read(url, article, **kwargs):
        attempted.append(url)
        return FulltextResult(access_level="fulltext", text="Retrieved full text", fulltext_url=url)
    monkeypatch.setattr("agents.EnnoScholarV2.tools.fulltext.read_fulltext", read)
    result = get_fulltext(Article(title="MeshingNet", url="https://arxiv.org/abs/2004.07016"), ScientificSearch(ScholarConfig()))
    assert result.access_level == "fulltext" and attempted == ["https://arxiv.org/pdf/2004.07016"]
