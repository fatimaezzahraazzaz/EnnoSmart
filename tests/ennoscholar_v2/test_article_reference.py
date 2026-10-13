from __future__ import annotations

import json
import logging

import pytest

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle, TOOLS
from agents.EnnoScholarV2.article_reference import (ABSENT_MESSAGE, DISCOVERY_TOOLS,
    explicitly_no_new_search, resolve_article_reference, resolve_message_reference)
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.context import ActiveSources
from agents.EnnoScholarV2.models import Article, ChatRequest, FulltextResult, TextChunk
from agents.EnnoScholarV2.service import ScholarChatService
from conftest import call, evidence_reference, message, response
from test_analysis_diagnostics import analysis_response
from test_batching import client_for_service
from test_context_window import active_notices


TITLE = "CellBridge: Deep Learning Enhanced Generalized Spatial Mapping for High-Quality Cellular Graph Generation and Tissue Structure Reconstruction"
ABBREVIATED = "CellBridge: Deep learning enhanced spatial mapping for high-quality cellular graph generation and tissue structure reconstruction."
SOURCE = "The method preserves verified cellular connectivity."


def known_registry(count=40):
    registry = {"target": StoredArticle(Article(id="target", title=TITLE, doi="10.9999/cellbridge",
        url="https://public.example/record/cellbridge", pdf_url="https://public.example/cellbridge.pdf",
        arxiv_id="2401.12345", abstract=SOURCE, access_level="abstract_only"))}
    registry.update({f"extra{i}": StoredArticle(Article(id=f"extra{i}", title=f"Unrelated scientific record {i}")) for i in range(count)})
    return registry


@pytest.mark.parametrize("reference,method", [
    ("target", "article_id"), (TITLE, "title"), (TITLE.swapcase(), "title"),
    (TITLE.replace("-", " ").replace(":", " — "), "title"), (ABBREVIATED, "title_fuzzy"),
    ("CellBridge", "alias"), ("10.9999/CELLBRIDGE", "doi"),
    ("https://doi.org/10.9999/cellbridge", "doi"), ("DOI:10.9999/cellbridge.", "doi"),
    ("(https://doi.org/10.9999/cellbridge)", "doi"),
    ("https://public.example/record/cellbridge", "url"), ("https://public.example/cellbridge.pdf", "url"),
    ("arXiv:2401.12345v2", "arxiv"), ("https://arxiv.org/pdf/2401.12345v1.pdf", "arxiv"),
])
def test_resolution_identity_precedence_case_punctuation_abbreviation_and_alias(reference, method):
    registry = known_registry()
    result = resolve_article_reference(reference, registry)
    assert result["status"] == "resolved" and result["article_id"] == "target"
    assert result["matched_by"] == method and result["article"]["doi"] == "10.9999/cellbridge"
    assert len(registry) == 41 and registry["target"].article.title == TITLE


def test_resolver_scans_whole_registry_not_only_active_and_returns_one_compact_notice():
    registry = known_registry(1600)
    assert "target" not in ActiveSources(registry).identifiers
    result = resolve_article_reference(ABBREVIATED, registry)
    assert result["article_id"] == "target" and result["article"]["title"] == TITLE
    wire = json.dumps(result)
    assert "extra0" not in wire and "extra1599" not in wire and len(wire) < 3000
    assert len(registry) == 1601


@pytest.mark.parametrize("reference", ["spatial mapping", "CellBridge: Graph classification using other scientific methods",
    "OtherBridge: Deep Learning Enhanced Generalized Spatial Mapping for High-Quality Cellular Graph Generation and Tissue Structure Reconstruction",
    TITLE.replace("Generation", "Optimization"), TITLE.replace("CellBridge", "CellBridge2")])
def test_resolver_never_uses_loose_topic_or_conflicting_name_to_choose_an_article(reference):
    assert resolve_article_reference(reference, known_registry())["status"] == "not_found"


def test_small_typo_after_stable_name_can_match_without_replacing_scientific_terms():
    assert resolve_article_reference(TITLE.replace("Enhanced", "Enhancd"), known_registry())["article_id"] == "target"


def test_ambiguous_abbreviation_returns_candidates_without_silent_id():
    registry = known_registry(0)
    registry["alternative"] = StoredArticle(Article(id="alternative", title=TITLE.replace("Generalized", "Adaptive"), doi="10.9999/alternative"))
    result = resolve_article_reference(ABBREVIATED, registry)
    assert result["status"] == "ambiguous" and "article_id" not in result
    assert {row["article_id"] for row in result["candidates"]} == {"target", "alternative"}


@pytest.mark.parametrize("instruction", ["Ne fais aucune nouvelle recherche.", "Ne lance pas de nouvelle recherche.",
    "Sans nouvelle recherche.", "N'effectue aucune recherche supplémentaire.", "Do not perform any new search.", "No new search."])
def test_explicit_search_restriction_is_recognized(instruction):
    assert explicitly_no_new_search(instruction)


@pytest.mark.parametrize("instruction", ["Cherche d'autres articles.", "Analyse ces articles.", "Fais une nouvelle recherche."])
def test_unrestricted_turn_does_not_disable_discovery(instruction):
    assert not explicitly_no_new_search(instruction)


@pytest.mark.parametrize("cached", [False, True])
def test_previous_turn_article_title_resolution_fulltext_analysis_and_http200(fake_client, monkeypatch, caplog, cached):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    service = ScholarChatService(agent.config, agent=agent)
    client = client_for_service(monkeypatch, service)
    fake_client.responses.create.return_value = response(message("Trois références, dont " + TITLE))
    first = client.post("/scholar-v2/chat", json={"message": "Donne trois références"}).json()
    # Committed previous-turn provider record: no network required to prepare the test.
    registry = known_registry()
    text = FulltextResult(access_level="fulltext", text=SOURCE,
        fulltext_url="https://public.example/cellbridge.pdf", chunks=[TextChunk(page=4, text=SOURCE)])
    if cached:
        registry["target"].fulltext = text
        registry["target"].article.access_level = "fulltext"
    service.sessions[first["session_id"]].articles = registry
    network = []
    def fulltext(*args, **kwargs): network.append(1); return text
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", fulltext)
    for name in ("search_batch", "search_scientific", "lookup_doi", "arxiv"):
        monkeypatch.setattr(agent.search, name, lambda *a, **kw: pytest.fail("Scientific retrieval forbidden"))
    chat = []
    def create(**kwargs):
        if "text" in kwargs:
            payload = json.loads(kwargs["input"])
            assert payload["article"]["id"] == "target" and payload["analysis_basis"] == "fulltext"
            return analysis_response(json.dumps({"access_level": "fulltext", "analysis_basis": "fulltext", "method": "Verified method",
                "evidence": [evidence_reference(payload, SOURCE, ["method"])]}))
        chat.append(kwargs)
        assert not DISCOVERY_TOOLS & {t.get("name") or t.get("type") for t in kwargs["tools"]}
        assert "target" in {row["id"] for row in active_notices(kwargs)}
        assert len(active_notices(kwargs)) <= 12 and "no_new_search=true" in kwargs["instructions"]
        if len(chat) == 1:
            return response(call("get_articles", {"article_ids": ["target"]}, "details"))
        if len(chat) == 2:
            return response(call("get_fulltext", {"article_ids": ["target"], "candidate_urls": []}, "fulltext"))
        if len(chat) == 3:
            return response(call("analyze_articles", {"article_ids": ["target"], "user_context": "Only this scientific source"}, "analysis"))
        return response(message("Article identifié dans la session ; passages du texte récupéré analysés."))
    fake_client.responses.create.side_effect = create
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    answer = client.post("/scholar-v2/chat", json={"session_id": first["session_id"],
        "message": "Analyse uniquement cet article :\n" + ABBREVIATED + "\nUtilise le texte complet s'il est disponible. Ne fais aucune nouvelle recherche."})
    assert answer.status_code == 200 and not answer.json()["partial"]
    assert answer.json()["tool_counts"] == {"get_articles": 1, "get_fulltext": 1, "analyze_articles": 1}
    assert network == ([] if cached else [1])
    assert service.sessions[first["session_id"]].articles["target"].analysis.evidence[0].excerpt == SOURCE
    assert len(service.sessions[first["session_id"]].articles) == 41
    if cached: assert "cache_hit=true" in caplog.text


def test_local_tool_pins_only_resolved_article_without_exposing_whole_registry(fake_client):
    registry = known_registry(1600)
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    assert any(t.get("name") == "resolve_article_reference" for t in TOOLS)
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return response(call("resolve_article_reference", {"reference": TITLE}, "resolve"))
        assert "target" in {row["id"] for row in active_notices(kwargs)}
        out = json.loads(next(i["output"] for i in kwargs["input"] if i.get("type") == "function_call_output"))
        assert out["status"] == "resolved" and out["article_id"] == "target" and "article" not in out
        assert "extra0" not in kwargs["instructions"] and "extra0" not in out
        return response(message("Notice identifiée, sans recherche."))
    fake_client.responses.create.side_effect = create
    result = agent.run("Retrouve la référence discutée, sans nouvelle recherche.", [], registry)
    assert result.tool_counts == {"resolve_article_reference": 1} and not result.partial
    assert len(result.active_article_ids) <= 12 and "target" in result.active_article_ids


def test_forbidden_custom_calls_are_skipped_even_if_model_proposes_them(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock", soft_tool_budget=1), client=fake_client)
    monkeypatch.setattr(agent, "execute_tool", lambda *a, **kw: pytest.fail("Forbidden discovery must not dispatch"))
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        assert not DISCOVERY_TOOLS & {t.get("name") or t.get("type") for t in kwargs["tools"]}
        if len(requests) == 1:
            return response(call("search_scientific", {"queries": ["forbidden"], "top_k": 8}, "s"),
                call("find_similar_articles", {}, "f"), call("web_search", {}, "w"))
        return response(message("Réponse à partir du registre conservé."))
    fake_client.responses.create.side_effect = create
    result = agent.run("Compare les sources existantes. Ne fais aucune nouvelle recherche.", [], known_registry())
    assert result.tool_counts == {} and len(result.activity) == 3
    assert all(row.status == "skipped" for row in result.activity)


def test_search_ban_is_not_restored_by_soft_finishing_catalog(fake_client):
    agent = ScholarChatAgent(ScholarConfig(model="mock", soft_tool_budget=1), client=fake_client)
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        assert not DISCOVERY_TOOLS & {t.get("name") or t.get("type") for t in kwargs["tools"]}
        if len(requests) == 1:
            return response(call("get_articles", {"article_ids": ["target"]}))
        return response(message("Notices conservées."))
    fake_client.responses.create.side_effect = create
    result = agent.run("Relis les notices sans nouvelle recherche.", [], known_registry())
    assert result.tool_counts == {"get_articles": 1} and not result.partial


def test_no_search_read_article_uses_local_doi_not_external_lookup(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    monkeypatch.setattr(agent, "read_article", lambda *a, **kw: pytest.fail("No external reader bypass"))
    fake_client.responses.create.side_effect = [response(call("read_article", {"url_or_doi": "10.9999/cellbridge"})), response(message("Notice existante identifiée."))]
    result = agent.run("Retrouve la notice discutée. Ne fais aucune nouvelle recherche.", [], known_registry())
    assert not result.partial and "target" in result.active_article_ids


@pytest.mark.parametrize("via_tool", [False, True])
def test_ambiguity_clarifies_without_any_download_or_analysis(fake_client, monkeypatch, via_tool):
    registry = known_registry(0)
    registry["alternative"] = StoredArticle(Article(id="alternative", title=TITLE.replace("Generalized", "Adaptive"), doi="10.9999/alternative"))
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", lambda *a, **kw: pytest.fail("No PDF before clarification"))
    fake_client.responses.create.return_value = response(call("resolve_article_reference", {"reference": ABBREVIATED}))
    request = "Analyse uniquement cet article :\n" + ABBREVIATED if not via_tool else "Analyse la référence discutée."
    result = agent.run(request + "\nNe fais aucune nouvelle recherche.", [], registry)
    assert "Lequel souhaitez-vous analyser" in result.answer
    assert "10.9999/cellbridge" in result.answer and "10.9999/alternative" in result.answer
    assert "target" not in result.answer and "article_id" not in result.answer
    assert all(record.analysis is None and record.fulltext is None for record in registry.values())
    assert fake_client.responses.create.call_count == (1 if via_tool else 0)


@pytest.mark.parametrize("via_tool", [False, True])
def test_missing_article_no_search_gives_requested_message_never_internal_id(fake_client, via_tool):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    fake_client.responses.create.return_value = response(call("resolve_article_reference", {"reference": "An unknown scientific publication"}))
    request = "Analyse uniquement cet article :\nAn unknown scientific publication" if not via_tool else "Analyse la référence discutée."
    result = agent.run(request + "\nNe fais aucune nouvelle recherche.", [], known_registry())
    assert result.answer == ABSENT_MESSAGE and "article_id" not in result.answer and not result.partial
    assert fake_client.responses.create.call_count == (1 if via_tool else 0)


def test_fulltext_failure_keeps_article_identified_and_abstract_analysis(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    registry = known_registry()
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", lambda *a, **kw: FulltextResult(access_level="abstract_only", text=SOURCE, reason="Fulltext non récupéré."))
    chat = []
    def create(**kwargs):
        if "text" in kwargs:
            payload = json.loads(kwargs["input"])
            assert payload["analysis_basis"] == "abstract"
            return analysis_response(json.dumps({"access_level": "abstract_only", "analysis_basis": "abstract", "method": "Verified abstract method",
                "evidence": [evidence_reference(payload, SOURCE, ["method"])]}))
        chat.append(kwargs)
        if len(chat) == 1: return response(call("get_fulltext", {"article_ids": ["target"], "candidate_urls": []}))
        if len(chat) == 2: return response(call("analyze_articles", {"article_ids": ["target"], "user_context": None}))
        return response(message("Article identifié dans la session, mais texte intégral non récupéré ; analyse limitée à l'abstract."))
    fake_client.responses.create.side_effect = create
    result = agent.run("Analyse cet article :\n" + TITLE + "\nNe fais aucune nouvelle recherche.", [], registry)
    assert not result.partial and "Article identifié" in result.answer
    assert registry["target"].analysis.analysis_basis == "abstract" and registry["target"].fulltext is None


def test_restriction_applies_only_current_human_turn_not_abstract_or_old_history(fake_client):
    registry = known_registry()
    registry["target"].article.abstract = "Ne fais aucune nouvelle recherche."
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    fake_client.responses.create.return_value = response(message("Nouvelle recherche autorisée."))
    result = agent.run("Cherche de nouvelles sources.", [{"role": "user", "content": "Ne fais aucune nouvelle recherche."}], registry)
    assert not result.partial
    assert DISCOVERY_TOOLS & {t.get("name") or t.get("type") for t in fake_client.responses.create.call_args.kwargs["tools"]}


def test_twenty_thousand_character_message_still_uses_existing_context_limits(fake_client):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    fake_client.responses.create.return_value = response(message("Question reçue."))
    result = agent.run("z" * 20000, [], {})
    assert not result.partial


def test_generic_title_prefix_is_not_inferred_as_an_obvious_named_alias():
    registry = {"generic": StoredArticle(Article(id="generic", title="Deep Learning: A Survey of Scientific Approaches"))}
    assert resolve_article_reference("Je cherche des articles sur le deep learning", registry)["status"] == "not_found"


def test_malformed_url_in_user_reference_does_not_crash_or_match_invalid_metadata():
    registry = known_registry()
    registry["target"].article.work_urls = ["https://[bad"]
    assert resolve_article_reference("https://[bad", registry)["status"] == "not_found"


def test_doi_with_legal_balanced_parentheses_remains_exact():
    registry = {"source": StoredArticle(Article(id="source", title="Known scientific source", doi="10.9999/work(2023)"))}
    assert resolve_article_reference("(https://doi.org/10.9999/work(2023))", registry)["article_id"] == "source"


@pytest.mark.parametrize("tail", ["Utilise le texte complet si disponible. Ne fais aucune nouvelle recherche.",
    "Ne fais aucune nouvelle recherche."])
def test_labelled_abbreviated_reference_with_same_line_instructions(tail):
    result = resolve_message_reference("Analyse cet article : " + ABBREVIATED + " " + tail, known_registry())
    assert result["status"] == "resolved" and result["article_id"] == "target"


@pytest.mark.parametrize("reference", [TITLE, ABBREVIATED])
def test_real_agent_previous_search_registers_generated_id_then_followup_resolves_locally(fake_client, monkeypatch, reference):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    service = ScholarChatService(agent.config, agent=agent)
    provider_calls = []
    rows = [record.article.model_dump() for record in known_registry().values()]
    def search(*args, **kwargs):
        provider_calls.append(1)
        return {"all_articles": rows, "articles": [], "total_found": len(rows)}
    monkeypatch.setattr(agent.search, "search_batch", search)
    fake_client.responses.create.side_effect = [response(call("search_scientific", {"queries": ["cellular connectivity"], "top_k": 8})),
        response(message("Trois références utiles, dont " + TITLE))]
    first = service.chat(7, ChatRequest(message="Cherche trois références scientifiques."))
    registry = service.sessions[first.session_id].articles
    identifier = next(key for key, record in registry.items() if record.article.title == TITLE)
    assert identifier.startswith("art_") and identifier != "target"
    assert len(registry) == 41 and identifier not in ActiveSources(registry).identifiers
    text = FulltextResult(access_level="fulltext", text=SOURCE, chunks=[TextChunk(page=4, text=SOURCE)],
        fulltext_url="https://public.example/cellbridge.pdf")
    monkeypatch.setattr("agents.EnnoScholarV2.agent.get_fulltext", lambda *a, **kw: text)
    chat = []
    def create(**kwargs):
        if "text" in kwargs:
            payload = json.loads(kwargs["input"])
            assert payload["article"]["id"] == identifier
            return analysis_response(json.dumps({"access_level": "fulltext", "analysis_basis": "fulltext", "method": "Verified method",
                "evidence": [evidence_reference(payload, SOURCE, ["method"])]}))
        chat.append(kwargs)
        assert identifier in {row["id"] for row in active_notices(kwargs)}
        assert not DISCOVERY_TOOLS & {tool.get("name") or tool.get("type") for tool in kwargs["tools"]}
        if len(chat) == 1: return response(call("get_articles", {"article_ids": [identifier]}))
        if len(chat) == 2: return response(call("get_fulltext", {"article_ids": [identifier], "candidate_urls": []}))
        if len(chat) == 3: return response(call("analyze_articles", {"article_ids": [identifier], "user_context": "Only this article"}))
        return response(message("Article déjà trouvé, texte récupéré et passages analysés."))
    fake_client.responses.create.side_effect = create
    final = service.chat(7, ChatRequest(session_id=first.session_id,
        message="Analyse uniquement cet article :\n" + reference + "\nNe fais aucune nouvelle recherche."))
    assert not final.partial and final.articles_total == 41
    assert final.tool_counts == {"get_articles": 1, "get_fulltext": 1, "analyze_articles": 1}
    assert provider_calls == [1]  # Only the preceding turn searched.
    assert service.sessions[first.session_id].articles[identifier].analysis.evidence[0].excerpt == SOURCE


def test_known_pdf_url_containing_doi_path_is_still_an_exact_url_reference():
    registry = known_registry()
    registry["target"].article.pdf_url = "https://public.example/10.9999/cellbridge.pdf"
    result = resolve_article_reference(registry["target"].article.pdf_url, registry)
    assert result["status"] == "resolved" and result["matched_by"] == "url" and result["article_id"] == "target"


def test_doi_present_only_in_known_metadata_url_can_resolve_without_network():
    registry = known_registry()
    registry["target"].article.doi = ""
    registry["target"].article.url = "https://doi.org/10.9999/cellbridge"
    assert resolve_article_reference("10.9999/CELLBRIDGE", registry)["article_id"] == "target"
    assert registry["target"].article.doi == ""  # Identity lookup does not rewrite metadata.


def test_unknown_doi_is_not_masked_by_an_existing_title_in_same_reference():
    assert resolve_article_reference(TITLE + " DOI:10.9999/different", known_registry())["status"] == "not_found"
