"""Selection/grounding protocol tests; all model decisions and providers are simulated."""
from __future__ import annotations

import json

import pytest
import httpx
from openai import APITimeoutError
from types import SimpleNamespace

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle
from agents.EnnoScholarV2.canonical import ArticleStore
from agents.EnnoScholarV2.citations import BibliographyError, FinalReference, render_bibliography, validate_reference
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.context import ActiveSources
from agents.EnnoScholarV2.models import Article, ArticleAnalysis, Evidence, FulltextResult, ScientificMemory
from conftest import call, message, response
from test_context_window import active_notices


def records():
    direct = Article(id="art_direct", title="Learned volumetric element generation", doi="10.9999/direct",
        abstract="A trained model produces three dimensional volume elements for finite element mechanics.", access_level="abstract_only")
    adjacent = Article(id="art_adjacent", title="Learned smoothing of existing surface elements", doi="10.9999/adjacent",
        abstract="A model smooths existing surfaces without generating volume elements.", access_level="fulltext", citation_count=10000)
    return {direct.id: StoredArticle(direct), adjacent.id: StoredArticle(adjacent,
        fulltext=FulltextResult(access_level="fulltext", text="Verified surface smoothing text.", fulltext_url="https://example.org/adjacent.pdf"),
        analysis=ArticleAnalysis(access_level="fulltext", analysis_basis="fulltext", method="Surface smoothing."))}


def selection(ids):
    return response(call("set_article_selection", {"request_kind": "bibliographic_search",
        "finalist_article_ids": ids, "pinned_article_ids": ids,
        "justifications": [{"article_id": key, "reason": "Direct production of the requested volumetric object."} for key in ids]}, "selection"))


def final_text(registry, ids, *, wrong_title=False, extra_evidence=False):
    refs = []
    for key in ids:
        a = registry[key].article
        refs.append({"article_id": key, "title": "Invented incorrect title" if wrong_title else a.title,
            "doi": a.doi, "arxiv_id": a.arxiv_id, "contribution": "DIRECT : production volumique pour la mécanique.",
            "evidence_ids": ["invented"] if extra_evidence else []})
    return json.dumps({"summary": "Comparaison selon la tâche et le contexte demandés.", "references": refs})


def test_bibliographic_search_requires_common_comparison_and_explicit_selection(fake_client, caplog):
    registry = records()
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return response(call("search_collected_articles", {"request_kind": "bibliographic_search",
                "query": "volume elements generation surfaces", "limit": 12, "exclude_article_ids": [], "cursor": None}))
        if len(requests) == 2:
            return response(message("Première synthèse prématurée."))
        if len(requests) == 3:
            assert kwargs["tool_choice"] == {"type": "function", "name": "set_article_selection"}
            rows = active_notices(kwargs)
            assert {row["id"] for row in rows} == set(registry)
            assert all("analysis" not in row and "fulltext" not in row for row in rows)
            return selection(["art_direct"])
        assert kwargs["text"]["format"]["name"] == "scholar_bibliography"
        return response(message(final_text(registry, ["art_direct"])))
    fake_client.responses.create.side_effect = create
    with caplog.at_level("INFO", logger="uvicorn.error"):
        result = agent.run("Trouve les meilleurs travaux de génération volumique apprise en mécanique.", [], registry)
    assert not result.partial and len(requests) == 4
    assert result.scientific_memory.finalist_article_ids == ["art_direct"]
    assert result.scientific_memory.finalist_justifications["art_direct"]
    assert registry["art_direct"].article.title in result.answer
    assert registry["art_adjacent"].article.title not in result.answer
    assert registry["art_adjacent"].analysis is not None
    assert all(c.url.endswith("/direct") for c in result.citations if c.cited)
    for event in ("local_search", "returned_ids=", "visible round=", "inspection", "selection finalists=", "closure reason=completed", "cited="):
        assert event in caplog.text
    assert "Verified surface smoothing text." not in caplog.text


def test_budget_exhaustion_cannot_claim_completed_comparison(fake_client):
    registry = records()
    fake_client.responses.create.return_value = response(call("search_collected_articles", {
        "request_kind": "bibliographic_search", "query": "volume elements", "limit": 12, "exclude_article_ids": [], "cursor": None}))
    agent = ScholarChatAgent(ScholarConfig(model="mock", hard_tool_budget=1), client=fake_client)
    result = agent.run("Recherche bibliographique.", [], registry)
    assert result.partial and result.reason == "tool_budget_reached"
    assert "comparaison" in result.answer and "n'ont pas pu être achevées" in result.answer
    assert fake_client.responses.create.call_count == 1


def test_model_cannot_finish_without_selecting_even_if_it_ignores_forced_tool(fake_client):
    registry = records()
    fake_client.responses.create.return_value = response(message("<!--scholar_request:bibliographic_search-->Synthèse sans comparaison."))
    result = ScholarChatAgent(ScholarConfig(model="mock", max_rounds=3), client=fake_client).run("Recherche.", [], registry)
    assert result.partial and result.reason == "round_budget_reached"
    assert "Synthèse sans comparaison" not in result.answer
    assert fake_client.responses.create.call_count == 3


def test_preexisting_active_article_can_be_selected(fake_client):
    registry = records()
    memory = ScientificMemory(active_article_ids=["art_direct"], pinned_article_ids=["art_direct"])
    fake_client.responses.create.side_effect = [response(message("<!--scholar_request:bibliographic_search-->Brouillon.")),
        selection(["art_direct"]), response(message(final_text(registry, ["art_direct"])))]
    result = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client).run("Les meilleures références.", [], registry, memory=memory)
    assert not result.partial and result.scientific_memory.finalist_article_ids == ["art_direct"]


def test_natural_selection_is_not_accepted_before_common_comparison(fake_client):
    registry = records()
    fake_client.responses.create.side_effect = [selection(["art_direct"]), selection(["art_direct"]),
        response(message(final_text(registry, ["art_direct"])))]
    result = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client).run("Bibliographie.", [], registry)
    assert result.tool_counts["set_article_selection"] == 2 and not result.partial
    assert "COMPARAISON COMMUNE" in fake_client.responses.create.call_args_list[1].kwargs["instructions"]


@pytest.mark.parametrize("field,value", [("title", "Wrong title"), ("doi", "10.1002/nme.70060"),
    ("article_id", "unknown"), ("arxiv_id", "2401.12345")])
def test_title_identifier_record_association_is_strict(field, value):
    registry = records()
    reference = {"article_id": "art_direct", "title": registry["art_direct"].article.title,
        "doi": registry["art_direct"].article.doi, "arxiv_id": "", "contribution": "A method.", "evidence_ids": []}
    reference[field] = value
    with pytest.raises(BibliographyError):
        validate_reference(FinalReference.model_validate(reference), ArticleStore(registry))


def test_wrong_title_is_regenerated_once_without_any_tool(fake_client):
    registry = records()
    fake_client.responses.create.side_effect = [response(message("<!--scholar_request:bibliographic_search-->Brouillon.")),
        selection(["art_direct"]), response(message(final_text(registry, ["art_direct"], wrong_title=True))),
        response(message(final_text(registry, ["art_direct"])))]
    result = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client).run("Bibliographie.", [], registry)
    assert not result.partial and "Invented incorrect title" not in result.answer
    repaired = fake_client.responses.create.call_args_list[-1].kwargs
    assert repaired["tools"] == [] and repaired["tool_choice"] == "none"
    assert result.tool_counts == {"set_article_selection": 1}


def test_two_invalid_final_answers_return_grounded_partial_fallback(fake_client):
    registry = records()
    wrong = response(message(final_text(registry, ["art_direct"], wrong_title=True)))
    fake_client.responses.create.side_effect = [response(message("<!--scholar_request:bibliographic_search-->Brouillon.")),
        selection(["art_direct"]), wrong, wrong]
    result = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client).run("Bibliographie.", [], registry)
    assert result.partial and result.reason == "bibliography_validation_failed"
    assert "Invented incorrect title" not in result.answer and "10.1002/nme.70060" not in result.answer
    assert fake_client.responses.create.call_count == 4


def test_cannot_select_unseen_backend_article(fake_client):
    registry = records()
    for i in range(20):
        registry[f"noise{i}"] = StoredArticle(Article(id=f"noise{i}", title=f"Unrelated record {i}"))
    fake_client.responses.create.return_value = selection(["art_direct"])
    result = ScholarChatAgent(ScholarConfig(model="mock", max_rounds=2), client=fake_client).run("Bibliographie.", [], registry)
    assert result.partial and not result.scientific_memory.finalist_article_ids


def test_targeted_analysis_does_not_require_final_selection(fake_client, monkeypatch):
    registry = records()
    monkeypatch.setattr("agents.EnnoScholarV2.agent.analyze_article", lambda *a, **kw:
        ArticleAnalysis(access_level="abstract_only", analysis_basis="abstract", method="A learned method."))
    fake_client.responses.create.side_effect = [response(call("analyze_articles", {
        "request_kind": "article_analysis", "article_ids": ["art_direct"], "user_context": "Analyse ciblée."})),
        response(message("<!--scholar_request:article_analysis-->Analyse fondée sur l'abstract uniquement."))]
    result = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client).run("Analyse cet article.", [], registry)
    assert not result.partial and result.tool_counts == {"analyze_articles": 1}
    assert fake_client.responses.create.call_count == 2 and "scholar_request" not in result.answer


def test_followup_can_use_local_retrieval_without_new_final_selection(fake_client):
    registry = records()
    fake_client.responses.create.side_effect = [response(call("search_collected_articles", {
        "request_kind": "followup", "query": "volume elements", "limit": 12, "exclude_article_ids": [], "cursor": None})),
        response(message("La génération volumique est plus directement pertinente."))]
    result = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client).run("Parmi ces articles, lequel génère l'objet ?",
        [{"role": "assistant", "content": "Articles déjà trouvés."}], registry)
    assert not result.partial and result.tool_counts == {"search_collected_articles": 1}
    assert fake_client.responses.create.call_count == 2


def test_memory_retains_reasons_and_common_view_does_not_sort_by_fulltext():
    registry = records()
    active = ActiveSources(registry, memory=ScientificMemory(active_article_ids=["art_direct", "art_adjacent"]))
    active.select(["art_direct"], ["art_direct"], [{"article_id": "art_direct", "reason": "Exact requested operation."}])
    restored = ActiveSources(registry, memory=active.snapshot())
    rows = restored.comparison_context()
    assert rows[0]["id"] == "art_direct" and all("analysis" not in row for row in rows)
    assert restored.justifications == {"art_direct": "Exact requested operation."}


def test_finalist_cannot_disappear_from_validated_answer():
    registry = records()
    with pytest.raises(BibliographyError, match="exactement"):
        render_bibliography(final_text(registry, ["art_direct"]), registry, ["art_direct", "art_adjacent"])


def test_unverified_evidence_and_free_prose_doi_are_rejected():
    registry = records()
    with pytest.raises(BibliographyError, match="evidence_id"):
        render_bibliography(final_text(registry, ["art_direct"], extra_evidence=True), registry, ["art_direct"])
    draft = json.loads(final_text(registry, ["art_direct"]))
    draft["summary"] = "Another paper DOI 10.1002/nme.70060"
    with pytest.raises(BibliographyError, match="texte libre"):
        render_bibliography(json.dumps(draft), registry, ["art_direct"])


def test_pages_and_exact_quotes_come_only_from_stored_evidence():
    registry = records()
    registry["art_direct"].analysis = ArticleAnalysis(access_level="abstract_only", analysis_basis="abstract", evidence=[
        Evidence(evidence_id="real", page=4, excerpt="Exact stored excerpt.", source_article_id="art_direct",
            canonical_work_id="art_direct", source_url="https://example.org/verified")])
    draft = json.loads(final_text(registry, ["art_direct"]))
    draft["references"][0]["evidence_ids"] = ["real"]
    answer, _, _ = render_bibliography(json.dumps(draft), registry, ["art_direct"])
    assert "Exact stored excerpt." in answer and "page PDF 4" in answer
    assert "https://example.org/verified" in answer


def test_old_examined_candidate_can_return_from_outside_active_window(fake_client):
    registry = records()
    noise = [f"noise{i}" for i in range(12)]
    for key in noise:
        registry[key] = StoredArticle(Article(id=key, title=f"Unrelated candidate {key}"))
    memory = ScientificMemory(active_article_ids=noise, candidate_window=noise,
        examined_article_ids=["art_direct", *noise])
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            assert "art_direct" not in {row["id"] for row in active_notices(kwargs)}
            return response(message("<!--scholar_request:bibliographic_search-->Draft."))
        if len(requests) == 2:
            assert '"article_id": "art_direct"' in kwargs["instructions"]
            assert "art_direct" not in {row["id"] for row in active_notices(kwargs)}
            return selection(["art_direct"])
        if len(requests) == 3:
            assert active_notices(kwargs)[0]["id"] == "art_direct"
            assert len(active_notices(kwargs)) <= 12
            return selection(["art_direct"])
        return response(message(final_text(registry, ["art_direct"])))
    fake_client.responses.create.side_effect = create
    result = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client).run("Bibliography.", [], registry, memory=memory)
    assert not result.partial and result.scientific_memory.finalist_article_ids == ["art_direct"]
    assert result.tool_counts == {"set_article_selection": 2}


@pytest.mark.parametrize("timeout_count", [1, 2])
def test_selected_bibliography_timeout_keeps_single_retry_and_no_tools(fake_client, timeout_count):
    registry = records()
    timeout = APITimeoutError(request=httpx.Request("POST", "https://example.org/mock"))
    fake_client.responses.create.side_effect = [
        response(message("<!--scholar_request:bibliographic_search-->Draft.")), selection(["art_direct"]),
        timeout, timeout if timeout_count == 2 else response(message(final_text(registry, ["art_direct"])))]
    result = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client).run("Bibliography.", [], registry)
    assert result.partial == (timeout_count == 2)
    assert result.reason == ("synthesis_timeout" if timeout_count == 2 else None)
    assert result.tool_counts == {"set_article_selection": 1}
    assert fake_client.responses.create.call_count == 4
    retry = fake_client.responses.create.call_args_list[-1].kwargs
    assert retry["tools"] == [] and retry["tool_choice"] == "none"
    assert retry["text"]["format"]["name"] == "scholar_bibliography"


def test_analysis_deadline_cannot_disguise_unselected_search_as_completed(fake_client, monkeypatch):
    registry = records()
    clock = SimpleNamespace(value=0)
    monkeypatch.setattr("agents.EnnoScholarV2.agent.time.monotonic", lambda: clock.value)
    def create(**kwargs):
        clock.value = 511
        return response(call("get_articles", {"request_kind": "bibliographic_search", "article_ids": ["art_direct"]}))
    fake_client.responses.create.side_effect = create
    result = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client).run("Bibliography.", [], registry)
    assert result.partial and result.reason == "analysis_time_budget_reached"
    assert not result.scientific_memory.finalist_article_ids
    assert fake_client.responses.create.call_count == 1


def test_common_view_and_render_keep_publication_preprint_identities_separate():
    preprint = Article(id="preprint", title="Initial learned element construction", arxiv_id="2401.00001",
        source="arxiv", related_article_ids=["publication"])
    publication = Article(id="publication", title="Published learned element construction", doi="10.9999/publication",
        abstract="A published abstract shared by associated versions.", related_article_ids=["preprint"])
    registry = {preprint.id: StoredArticle(preprint), publication.id: StoredArticle(publication)}
    ArticleStore(registry).annotate()
    active = ActiveSources(registry, memory=ScientificMemory(active_article_ids=[preprint.id]))
    row = active.comparison_context()[0]
    assert (row["id"], row["title"], row["doi"], row["arxiv_id"]) == (preprint.id, preprint.title, "", preprint.arxiv_id)
    assert row["abstract"] == publication.abstract
    answer, citations, _ = render_bibliography(final_text(registry, [preprint.id]), registry, [preprint.id])
    assert preprint.title in answer and publication.title not in answer
    assert citations[0].url == "https://arxiv.org/abs/" + preprint.arxiv_id


def test_wrong_title_with_stored_nme_doi_is_rejected():
    registry = records()
    registry["art_direct"].article.doi = "10.1002/nme.70060"
    with pytest.raises(BibliographyError, match="incohérente"):
        render_bibliography(final_text(registry, ["art_direct"], wrong_title=True), registry, ["art_direct"])
