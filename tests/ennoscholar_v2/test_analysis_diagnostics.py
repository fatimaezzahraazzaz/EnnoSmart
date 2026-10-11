from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.models import Article, ChatRequest, FulltextResult, TextChunk
from agents.EnnoScholarV2.service import ScholarChatService
from agents.EnnoScholarV2.tools.article_analysis import AnalysisDraft, AnalysisIncompleteError, analyze_article
from conftest import call, evidence_reference, message, response
from scientific_fixture import CENTRAL_TITLES
from test_batching import client_for_service


def analysis_response(text, *, status="completed", details=None, dict_usage=False):
    usage = {"input_tokens": 4200, "output_tokens": 3500}
    return SimpleNamespace(id="resp_test", status=status, incomplete_details=details,
        usage=usage if dict_usage else SimpleNamespace(**usage), output_text=text)


def diagnostic_records(caplog, event):
    prefix = f"EnnoScholar V2 {event} "
    return [json.loads(record.getMessage()[len(prefix):]) for record in caplog.records
        if record.getMessage().startswith(prefix)]


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress", "queued", None])
def test_non_completed_never_validates_json_and_retries_only_token_exhaustion(fake_client, monkeypatch, caplog, status):
    article = Article(id="a", title="Article", abstract="PRIVATE_SOURCE_MATERIAL")
    details = SimpleNamespace(model_dump=lambda **kw: {"reason": "max_output_tokens"})
    fake_client.responses.create.return_value = analysis_response("BROKEN_JSON_PRIVATE", status=status, details=details)
    validator = Mock(side_effect=AssertionError("Must not parse unfinished response"))
    monkeypatch.setattr(AnalysisDraft, "model_validate_json", validator)
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    with pytest.raises(AnalysisIncompleteError, match="analysis_incomplete"):
        analyze_article(article, None, fake_client, ScholarConfig(model="mock"))
    validator.assert_not_called()
    attempts = 2 if status == "incomplete" else 1
    assert fake_client.responses.create.call_count == attempts
    assert [c.kwargs["max_output_tokens"] for c in fake_client.responses.create.call_args_list] == [3500, 6000][:attempts]
    entries = diagnostic_records(caplog, "analysis_incomplete")
    assert len(entries) == attempts
    entry = entries[0]
    assert entry == {"article_id": "a", "title": "Article", "analysis_basis": "abstract",
        "chunk_count": 1, "material_char_count": len(article.abstract), "response_id": "resp_test",
        "response_status": status, "incomplete_details": {"reason": "max_output_tokens"},
        "input_tokens": 4200, "output_tokens": 3500, "output_text_length": 19}
    assert article.abstract not in caplog.text and "BROKEN_JSON_PRIVATE" not in caplog.text


@pytest.mark.parametrize("text,kind,location", [
    ('{"method":', "json_invalid", []),
    ('{"access_level":"fulltext","analysis_basis":"fulltext","method":123}', "string_type", ["method"]),
    ('{"analysis_basis":"abstract"}', "missing", ["access_level"]),
    ('{"access_level":"pdf","analysis_basis":"abstract"}', "literal_error", ["access_level"]),
])
def test_completed_validation_error_has_field_and_usage_without_full_output(fake_client, caplog, text, kind, location):
    fake_client.responses.create.return_value = analysis_response(text, dict_usage=True)
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    article = Article(id="a", title="Article", abstract="PRIVATE_SOURCE_MATERIAL")
    with pytest.raises(ValidationError):
        analyze_article(article, None, fake_client, ScholarConfig(model="mock"))
    [entry] = diagnostic_records(caplog, "analysis_validation_error")
    assert any(error["type"] == kind and error["loc"] == location for error in entry["validation_errors"])
    assert all("input" not in error for error in entry["validation_errors"])
    assert entry["response_status"] == "completed" and entry["incomplete_details"] is None
    assert entry["input_tokens"] == 4200 and entry["output_tokens"] == 3500
    assert entry["article_id"] == "a" and entry["analysis_basis"] == "abstract"
    assert entry["output_text_length"] == len(text)
    assert entry["output_text_start"] + entry["output_text_end"] != text
    assert article.abstract not in caplog.text
    fake_client.responses.create.assert_called_once()


def test_long_output_samples_are_bounded_and_middle_and_sources_not_logged(fake_client, caplog):
    text = "{" + "A" * 1000 + "PRIVATE_MIDDLE_SENTINEL" + "Z" * 1000 + "}"
    fulltext = FulltextResult(access_level="fulltext", text="PRIVATE_FULLTEXT_SENTINEL",
        chunks=[TextChunk(page=3, text="PRIVATE_FULLTEXT_SENTINEL")])
    fake_client.responses.create.return_value = analysis_response(text)
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    with pytest.raises(ValidationError):
        analyze_article(Article(id="a", title="Article"), fulltext, fake_client, ScholarConfig(model="mock"))
    [entry] = diagnostic_records(caplog, "analysis_validation_error")
    assert len(entry["output_text_start"]) == len(entry["output_text_end"]) == 400
    assert entry["analysis_basis"] == "fulltext" and entry["chunk_count"] == 1
    assert entry["material_char_count"] == len(fulltext.chunks[0].text)
    assert "PRIVATE_MIDDLE_SENTINEL" not in caplog.text and fulltext.text not in caplog.text


def test_unknown_evidence_id_keeps_existing_safety_and_exact_quote(fake_client):
    quote = "The trained model produces the requested object."
    article = Article(id="a", title="Article", abstract=quote)
    def create(**kwargs):
        payload = json.loads(kwargs["input"])
        return analysis_response(json.dumps({"method": "Verified method", "results": "Unsupported result",
            "access_level": "abstract_only", "analysis_basis": "abstract", "evidence": [
                evidence_reference(payload, quote, ["method"]), {"evidence_id": "does_not_exist", "supports": ["results"]}]}))
    fake_client.responses.create.side_effect = create
    result = analyze_article(article, None, fake_client, ScholarConfig(model="mock"))
    assert result.method and not result.results and len(result.evidence) == 1
    assert result.evidence[0].excerpt == quote


def test_two_benchmark_failures_preserve_other_batch_analyses_and_http200(fake_client, monkeypatch, caplog):
    # Synthetic failure causes: this does not assert the cause of the previous real run.
    identifiers = ["art_942bde322abe27ebd8ff", "art_7343e4f0cf5a62497734", "working"]
    titles = [CENTRAL_TITLES[1], CENTRAL_TITLES[2], "AMBER"]
    quote = "A trained model produces the requested object."
    fulltext = FulltextResult(access_level="fulltext", text=quote, chunks=[TextChunk(page=4, section="Methods", text=quote)])
    registry = {identifier: StoredArticle(Article(id=identifier, title=title, access_level="fulltext"), fulltext=fulltext)
        for identifier, title in zip(identifiers, titles)}
    chat_calls = []
    def create(**kwargs):
        if "text" not in kwargs:
            chat_calls.append(kwargs)
            if len(chat_calls) == 1:
                return response(call("analyze_articles", {"article_ids": identifiers, "user_context": "Scientific request"}))
            return response(message("Deux PDF récupérés mais passages non analysés. Une analyse réussie conservée."))
        payload = json.loads(kwargs["input"])
        identifier = payload["article"]["id"]
        if identifier == identifiers[0]:
            return analysis_response('{"method":', status="incomplete", details={"reason": "max_output_tokens"})
        if identifier == identifiers[1]:
            return analysis_response('{"method":123,"access_level":"fulltext","analysis_basis":"fulltext"}')
        return analysis_response(json.dumps({"method": "Verified method", "access_level": "fulltext", "analysis_basis": "fulltext",
            "evidence": [evidence_reference(payload, quote, ["method"])]}))
    fake_client.responses.create.side_effect = create
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    service = ScholarChatService(agent.config, agent=agent)
    sid, session = service._acquire_session(1, ChatRequest(message="Start"))
    session.articles = registry
    session.lock.release()
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    http = client_for_service(monkeypatch, service).post("/scholar-v2/chat", json={"session_id": sid, "message": "Analyse ces sources"})
    assert http.status_code == 200 and not http.json()["partial"]
    assert http.json()["tool_counts"] == {"analyze_articles": 1}
    stored = service.sessions[sid].articles
    assert stored[identifiers[0]].analysis is None and stored[identifiers[1]].analysis is None
    assert all(stored[identifier].fulltext.text == quote for identifier in identifiers)
    assert stored["working"].analysis.method and stored["working"].analysis.evidence[0].excerpt == quote
    incomplete_entries = diagnostic_records(caplog, "analysis_incomplete")
    assert len(incomplete_entries) == 2
    incomplete = incomplete_entries[0]
    [invalid] = diagnostic_records(caplog, "analysis_validation_error")
    assert incomplete["article_id"] == identifiers[0] and invalid["article_id"] == identifiers[1]
    failures = [r.getMessage() for r in caplog.records if "batch article failure" in r.getMessage()]
    assert len(failures) == 2 and all("basis=fulltext" in line for line in failures)
    assert any("reason=analysis_incomplete" in line for line in failures)
    assert any("error_type=ValidationError reason=analysis_validation_error" in line for line in failures)
    assert fake_client.responses.create.call_count == 6  # Two chat responses, three analyses, one token-limit retry.
