"""Offline integration: buried candidates, canonical versions and scientific memory."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle, TOOLS
from agents.EnnoScholarV2.article_reference import resolve_article_reference
from agents.EnnoScholarV2.canonical import ArticleStore, canonical_works
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.context import ActiveSources, compact_analysis
from agents.EnnoScholarV2.evidence_provenance import analysis_material
from agents.EnnoScholarV2.models import Article, ChatRequest, FulltextResult, ScientificMemory, TextChunk
from agents.EnnoScholarV2.service import ScholarChatService
from agents.EnnoScholarV2.tools.article_analysis import analyze_article
from agents.EnnoScholarV2.tools.collected_search import search_collected_articles
from agents.EnnoScholarV2.tools.scientific_search import deduplicate
from agents.EnnoScholarV2.tools.similarity import find_similar_articles
from conftest import call, evidence_reference, message, response
from scientific_fixture import CENTRAL_TITLES
from test_context_window import active_notices


def corpus(size=1100):
    return {f"article{i}": StoredArticle(Article(id=f"article{i}", title=f"Unrelated study {i}",
        doi=f"10.9999/study-{i}", abstract="Another scientific application.", sources=["openalex"])) for i in range(size)}


def versions():
    title = "CellBridge: Deep learning enhanced generalized spatial mapping for high quality cellular graph construction"
    pub = Article(id="pub", title=title, doi="10.9999/published", year=2025, authors=["Alice"], sources=["openalex"])
    pre = Article(id="pre", title=title.replace("generalized ", ""), doi="10.48550/arxiv.2401.12345",
        arxiv_id="2401.12345", year=2024, authors=["Alice"], source="arxiv", sources=["arxiv"],
        url="https://arxiv.org/abs/2401.12345", abstract="A learned mapping method.")
    return {pub.id: StoredArticle(pub), pre.id: StoredArticle(pre)}


def forbid_discovery(monkeypatch, agent):
    def fail(*args, **kwargs):
        pytest.fail("Offline navigation must never call a scientific provider")
    for name in ("search_batch", "search_scientific", "lookup_doi", "semantic", "openalex", "arxiv"):
        monkeypatch.setattr(agent.search, name, fail)


def test_buried_candidate_first_local_window_is_visible_to_real_agent_loop(fake_client, monkeypatch):
    registry = corpus()
    target = registry["article960"].article
    target.title = "Differential neural construction of volumetric discretizations"
    target.abstract = "Introduction boilerplate. " * 60 + "Learned hexahedral mesh generation for finite element mechanics."
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    forbid_discovery(monkeypatch, agent)
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return response(call("search_collected_articles", {"query": "learned hexahedral mesh generation finite element mechanics",
                "limit": 8, "exclude_article_ids": [], "cursor": None}))
        notices = active_notices(kwargs)
        found = next(row for row in notices if row["id"] == target.id)
        assert "hexahedral" in found["abstract"].lower()
        assert len(notices) <= 12 and "Qualification" not in found
        return response(message("Le candidat enfoui est maintenant disponible pour comparaison scientifique."))
    fake_client.responses.create.side_effect = create
    result = agent.run("Explore les méthodes apprises dans le corpus existant, sans nouvelle recherche.", [], registry)
    assert result.tool_counts == {"search_collected_articles": 1}
    assert target.id in result.active_article_ids and len(registry) == 1100
    assert all(tool.get("name") != "search_scientific" for tool in calls[0]["tools"])


def test_local_search_accesses_provider_position_beyond_first(fake_client):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    def provider(query, limit, **kwargs):
        return [Article(title=f"Provider candidate {i}", doi=f"10.9999/native-{i}",
            abstract="Learned molecular conformation synthesis." if i == 20 else "Unrelated study.") for i in range(limit)]
    agent.search.semantic = provider
    agent.search.openalex = lambda *a, **kw: []
    agent.search.arxiv = lambda *a, **kw: []
    registry = {}
    first = agent.execute_tool("search_scientific", {"queries": ["broad task"], "top_k": 8}, registry, fake_client)
    target = next(key for key, record in registry.items() if record.article.title == "Provider candidate 20")
    assert target not in [row["article_id"] for row in first["articles"]]
    local = search_collected_articles(registry, "molecular conformation synthesis", 8)
    assert local["articles"][0]["article_id"] == target


def test_query_exclusion_and_cursor_return_different_work_windows():
    registry = corpus(30)
    for record in registry.values():
        record.article.abstract = "Protein folding energy prediction."
    one = search_collected_articles(registry, "protein folding", 8)
    ids = [row["article_id"] for row in one["articles"]]
    two = search_collected_articles(registry, "protein folding", 8, ids)
    page = search_collected_articles(registry, "protein folding", 8, cursor=one["next_cursor"])
    assert set(ids).isdisjoint(row["article_id"] for row in two["articles"])
    assert set(ids).isdisjoint(row["article_id"] for row in page["articles"])
    assert one["retrieval_only"] and all("relevance_score" not in row for row in one["articles"])
    with pytest.raises(ValueError):
        search_collected_articles(registry, "other query", cursor=one["next_cursor"])


def test_local_query_can_use_author_and_identifiers_without_access_or_citation_bonus():
    registry = corpus(25)
    registry["article20"].article.authors = ["Researcher UniqueSurname"]
    registry["article21"].article.citation_count = 1000000
    registry["article21"].article.access_level = "fulltext"
    assert search_collected_articles(registry, "UniqueSurname")["articles"][0]["article_id"] == "article20"
    assert search_collected_articles(registry, "10.9999/study-20")["articles"][0]["article_id"] == "article20"


@pytest.mark.parametrize("count", [1, 4, 12])
def test_new_candidates_visible_without_deleting_pins_or_finalists(count):
    registry = corpus(40)
    active = ActiveSources(registry)
    selected = [f"article{i}" for i in range(count)]
    active.select(selected, selected)
    candidates = [{"article_id": f"article{i}"} for i in range(20, 28)]
    active.update(candidates)
    assert active.finalists == selected and active.pinned == selected
    assert set(f"article{i}" for i in range(20, 28)) <= set(active.identifiers)
    assert len(active.identifiers) <= 12
    restored = ActiveSources(registry, memory=active.snapshot())
    assert restored.identifiers[:count] == selected


def test_agent_selected_finalists_persist_next_service_turn(fake_client, monkeypatch):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    service = ScholarChatService(agent.config, agent=agent)
    fake_client.responses.create.return_value = response(message("Bonjour."))
    first = service.chat(7, ChatRequest(message="Bonjour"))
    session = service.sessions[first.session_id]
    session.articles = corpus()
    fake_client.responses.create.side_effect = [response(call("get_articles", {"article_ids": ["article960"]})),
        response(call("set_article_selection", {"finalist_article_ids": ["article960"], "pinned_article_ids": ["article960"]})),
        response(message("Finaliste choisi par le chat.")), response(message("Comparaison depuis la mémoire."))]
    forbid_discovery(monkeypatch, agent)
    service.chat(7, ChatRequest(session_id=first.session_id, message="Examine la notice article960 sans nouvelle recherche."))
    state = session.scientific_memory
    assert state.finalist_article_ids == ["article960"] and "article960" in state.inspected_article_ids
    last = service.chat(7, ChatRequest(session_id=first.session_id, message="Compare les finalistes précédents."))
    assert "article960" in last.active_article_ids
    assert active_notices(fake_client.responses.create.call_args.kwargs)[0]["id"] == "article960"


def test_canonical_publication_preprint_retains_every_stored_payload():
    registry = versions()
    full = FulltextResult(access_level="fulltext", text="Original PDF text.", chunks=[TextChunk(page=3, section="Results", text="Original PDF text.")])
    registry["pre"].fulltext = full
    store = ArticleStore(registry)
    store.annotate()
    assert len(store.works) == 1 and len(store.works[0].versions) == 2
    assert store.works[0].identifiers["arxiv"] == ["2401.12345"]
    assert registry["pre"].fulltext is full and registry["pre"].fulltext.chunks[0].page == 3
    assert store.payloads(store.works[0].canonical_id)["pre"].fulltext is full


@pytest.mark.parametrize("reference,version", [("10.9999/published", "pub"), ("10.48550/arxiv.2401.12345", "pre"),
    ("arXiv:2401.12345v2", "pre"), ("CellBridge", "pub")])
def test_resolver_identity_selects_correct_version_of_one_work(reference, version):
    registry = versions()
    ArticleStore(registry).annotate()
    resolved = resolve_article_reference(reference, registry)
    assert resolved["status"] == "resolved" and resolved["article_id"] == version
    assert resolved["canonical_id"] == registry["pub"].article.canonical_article_id


def test_same_doi_and_transitive_links_never_create_false_ambiguity(fake_client):
    registry = {key: StoredArticle(Article(id=key, title=f"Different version {key}", doi="10.9999/same")) for key in ("a", "b")}
    assert resolve_article_reference("10.9999/same", registry)["status"] == "resolved"
    registry = {"a": StoredArticle(Article(id="a", title="Original version", doi="10.9999/bridge")),
        "b": StoredArticle(Article(id="b", title="Variant version")),
        "c": StoredArticle(Article(id="c", title="Final variant", related_article_ids=["b"]))}
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    agent.register([Article(title="Variant version", doi="10.9999/bridge").model_dump()], registry)
    assert len(ArticleStore(registry).works) == 1 and len(registry) == 3


@pytest.mark.parametrize("similar", [False, True])
def test_conflicting_publication_identifiers_never_merge_by_title(similar):
    title = "Learned construction of scientific numerical computational structures and elements"
    rows = [Article(id="a", title=title, doi="10.9999/a", authors=["Alice"], year=2025),
        Article(id="b", title=title.replace("elements", "element") if similar else title,
            doi="10.9999/b", authors=["Bob"], year=2019)]
    assert len(deduplicate(copy.deepcopy(rows), 20)) == 2
    assert len(canonical_works(rows)) == 2


@pytest.mark.parametrize("reply,index", [("le premier", 0), ("le deuxième", 1), ("2", 1)])
def test_clarification_state_resolves_ordinal_in_next_turn(fake_client, monkeypatch, reply, index):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    service = ScholarChatService(agent.config, agent=agent)
    fake_client.responses.create.return_value = response(message("Bonjour."))
    first = service.chat(7, ChatRequest(message="Bonjour"))
    registry = versions()
    registry.pop("pre")
    other = registry["pub"].article.model_copy(deep=True, update={"id": "other", "title": registry["pub"].article.title.replace("generalized", "adaptive"), "doi": "10.9999/other"})
    registry[other.id] = StoredArticle(other)
    service.sessions[first.session_id].articles = registry
    forbid_discovery(monkeypatch, agent)
    fake_client.responses.create.return_value = response(call("resolve_article_reference", {"reference": "CellBridge"}))
    clarification = service.chat(7, ChatRequest(session_id=first.session_id, message="Analyse la référence discutée sans nouvelle recherche."))
    pending = service.sessions[first.session_id].scientific_memory.pending_clarification
    assert "1." in clarification.answer and "2." in clarification.answer and pending
    chosen = pending["candidates"][index]
    def create(**kwargs):
        assert any(row["id"] == chosen["article_id"] for row in active_notices(kwargs))
        assert '"matched_by": "clarification"' in kwargs["instructions"]
        return response(message("Référence identifiée, sans nouvel outil."))
    fake_client.responses.create.side_effect = create
    last = service.chat(7, ChatRequest(session_id=first.session_id, message=reply))
    assert chosen["article_id"] in last.active_article_ids
    assert service.sessions[first.session_id].scientific_memory.pending_clarification is None


@pytest.mark.parametrize("material,page", [("pdf", 4), ("html", None), ("abstract", None)])
def test_exact_evidence_and_pages_with_provenance(fake_client, material, page):
    quote = "Exact source punctuation and  spacing."
    article = Article(id="paper", title="Known source", doi="10.9999/source", canonical_article_id="canonical", abstract=quote)
    full = None if material == "abstract" else FulltextResult(access_level="fulltext", text=quote,
        chunks=[TextChunk(page=page, section="Results", text=quote)], fulltext_url="https://public.example/source", source_article_id="source-version", source_version="preprint")
    def create(**kwargs):
        payload = json.loads(kwargs["input"])
        return SimpleNamespace(status="completed", output_text=json.dumps({"results": "Supported result", "access_level": "fulltext",
            "analysis_basis": "fulltext", "evidence": [evidence_reference(payload, quote, ["results"])]}))
    fake_client.responses.create.side_effect = create
    result = analyze_article(article, full, fake_client, ScholarConfig(model="mock"))
    evidence = result.evidence[0]
    assert evidence.exact_quote == quote and evidence.page_number == page
    assert evidence.canonical_work_id == "canonical"
    assert evidence.source_article_id == ("paper" if material == "abstract" else "source-version")
    assert evidence.source_version == ("publication" if material == "abstract" else "preprint")
    assert compact_analysis(result)["evidence"][0]["exact_quote"] == quote


def test_reused_preprint_and_flattened_copy_recover_only_original_pages():
    registry = versions()
    full = FulltextResult(access_level="fulltext", text="Actual preprint result.", fulltext_url="https://arxiv.org/pdf/2401.12345",
        chunks=[TextChunk(page=7, section="Results", text="Actual preprint result.")])
    registry["pre"].fulltext = full
    registry["pub"].fulltext = full.model_copy(update={"chunks": []})
    registry["pub"].fulltext_article_id = "pre"
    ArticleStore(registry).annotate()
    material = analysis_material("pub", registry)
    assert material.source_article_id == "pre" and material.source_version == "preprint"
    assert material.chunks[0].page == 7 and not registry["pub"].fulltext.chunks
    registry["pub"].fulltext = registry["pub"].fulltext.model_copy(update={"text": "Different document."})
    assert not analysis_material("pub", registry).chunks


def test_similar_recommendations_canonical_and_seed_excluded(fake_client):
    registry = versions()
    seed = Article(id="seed", title="Different reference", doi="10.9999/seed")
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    agent.search.search_scientific = lambda *a, **kw: {"articles": [record.article.model_dump() for record in registry.values()], "warnings": []}
    result = find_similar_articles("Related sources", agent.search, article=seed)
    assert len(result["articles"]) == 1
    result = find_similar_articles("Related sources", agent.search, article=registry["pre"].article)
    assert not result["articles"]


def test_canonical_api_count_and_versions_preserved(fake_client):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    service = ScholarChatService(agent.config, agent=agent)
    fake_client.responses.create.return_value = response(message("Bonjour."))
    first = service.chat(7, ChatRequest(message="Bonjour"))
    service.sessions[first.session_id].articles = versions()
    followup = service.chat(7, ChatRequest(session_id=first.session_id, message="Compare les notices existantes."))
    assert followup.articles_total == followup.canonical_work_count == 1
    assert followup.record_count == followup.version_count == 2
    page = service.list_articles(7, first.session_id)
    assert page.total == 1 and len(page.articles) == 1 and len(page.articles[0].related_article_ids) == 2
    assert len(service.sessions[first.session_id].articles) == 2


@pytest.mark.parametrize("title", CENTRAL_TITLES[:6] + ["AMBER: Adaptive meshing with learned refinement", "NeurFrame: Neural frame fields for volumetric meshing"])
def test_historical_benchmark_can_be_found_below_position_900(title):
    registry = corpus(1001)
    registry["article960"].article.title = title
    found = search_collected_articles(registry, title, 8)
    assert found["articles"][0]["article_id"] == "article960"
    assert len(found["articles"]) <= 12


def test_new_tools_do_not_include_any_scientific_reranker():
    names = {tool.get("name") for tool in TOOLS}
    assert {"search_collected_articles", "set_article_selection", "find_similar_articles"} <= names
    assert "rank_scientific_articles" not in names


def test_generic_scientific_query_exposes_buried_benchmark_family_without_forced_titles():
    registry = corpus(1100)
    titles = CENTRAL_TITLES[:6] + ["AMBER: Adaptive meshing with learned refinement", "NeurFrame: Neural frame fields for volumetric meshing"]
    targets = set()
    for index, title in enumerate(titles, 960):
        article = registry[f"article{index}"].article
        article.title = title
        article.abstract = "A learned model generates or adapts volumetric meshes for finite element mechanics."
        targets.add(article.id)
    found = search_collected_articles(registry, "learned volumetric meshes finite element mechanics", 8)
    assert {row["article_id"] for row in found["articles"]} == targets
    assert all("relevance_score" not in row for row in found["articles"])


def test_real_benchmark_publication_doi_is_not_mistaken_for_associated_preprint_doi():
    title = CENTRAL_TITLES[1]
    pub = Article(id="publication", title=title, doi="10.1007/s00366-025-02161-9", year=2025)
    pre = Article(id="preprint", title=title.replace("generalized ", ""), doi="10.48550/arxiv.2410.18852", arxiv_id="2410.18852", year=2024)
    registry = {article.id: StoredArticle(article) for article in (pub, pre)}
    ArticleStore(registry).annotate()
    assert len(ArticleStore(registry).works) == 1
    for article in (pub, pre):
        result = resolve_article_reference(article.doi, registry)
        assert result["status"] == "resolved" and result["article_id"] == article.id
        assert result["canonical_id"] == pub.canonical_article_id


def test_excluding_any_version_excludes_the_whole_work_and_cursor_detects_changed_corpus():
    registry = versions()
    first = search_collected_articles(registry, "learned mapping", 1)
    assert len(first["articles"]) == 1
    assert not search_collected_articles(registry, "learned mapping", 1, ["pre"])["articles"]
    registry.update(corpus(3))
    for record in registry.values():
        record.article.abstract += " Learned mapping."
    first = search_collected_articles(registry, "learned mapping", 1)
    registry["article0"].article.abstract += " Changed material."
    with pytest.raises(ValueError, match="corpus/query"):
        search_collected_articles(registry, "learned mapping", 1, cursor=first["next_cursor"])


def test_provider_ids_and_origin_notices_survive_version_merge(fake_client):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    rows = [Article(id="provider-a", title="One work", doi="10.9999/work", source="openalex", sources=["openalex"],
        external_ids={"openalex": "https://openalex.org/W123"}),
        Article(id="provider-b", title="One work", doi="10.9999/work", source="semantic_scholar", sources=["semantic_scholar"],
        external_ids={"semantic_scholar": "paper123"})]
    registry = {}
    agent.register([row.model_dump() for row in rows], registry)
    store = ArticleStore(registry)
    assert len(registry) == len(store.works) == 1
    assert next(iter(registry)).startswith("art_")
    work = store.works[0]
    assert work.identifiers["semantic_scholar"] == ["paper123"]
    assert work.identifiers["openalex"] == ["https://openalex.org/w123"]
    assert len(work.versions[0].provider_notices) == 2


def test_truly_unpaginated_text_never_invents_a_page(fake_client):
    registry = versions()
    registry["pub"].fulltext = FulltextResult(access_level="fulltext", text="Verified sentence.", fulltext_url="https://public.example/plain")
    material = analysis_material("pub", registry)
    assert material.source_format == "unpaginated_text" and not material.chunks
    def create(**kwargs):
        data = json.loads(kwargs["input"])
        assert "provider_notices" not in data["article"]
        assert all(row["page"] is None for row in data["passages"])
        return SimpleNamespace(status="completed", output_text=json.dumps({"results": "Supported result", "access_level": "fulltext",
            "analysis_basis": "fulltext", "evidence": [evidence_reference(data, "Verified sentence.", ["results"])]}))
    fake_client.responses.create.side_effect = create
    result = analyze_article(registry["pub"].article, material, fake_client, ScholarConfig(model="mock"))
    assert result.evidence[0].page_number is None


def test_local_retrieval_preserves_shorter_version_and_provider_abstracts(fake_client):
    registry = versions()
    registry["pub"].article.abstract = "Piezoelectric discretization."
    registry["pre"].article.abstract = "Completely different terminology. " * 50
    assert search_collected_articles(registry, "piezoelectric")["articles"][0]["abstract_source_article_id"] == "pub"
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    rows = [Article(title="Independent study", doi="10.9999/independent", abstract="A longer general abstract. " * 30,
        external_ids={"openalex": "https://openalex.org/W100"}),
        Article(title="Independent study", doi="10.9999/independent", abstract="Magnetostrictive elements.",
        external_ids={"openalex": "https://openalex.org/W200"})]
    agent.register([article.model_dump() for article in rows], registry)
    result = search_collected_articles(registry, "magnetostrictive")
    assert len(result["articles"]) == 1 and "Magnetostrictive" in result["articles"][0]["abstract_excerpt"]
    work = ArticleStore(registry).by_id[result["articles"][0]["article_id"]]
    assert set(work.identifiers["openalex"]) == {"https://openalex.org/w100", "https://openalex.org/w200"}


def test_first_compact_window_cannot_resolve_a_conflicting_doi_by_title(fake_client):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    rows = [Article(id="provider-a", title="Identical title", doi="10.9999/a", authors=["Alice"], year=2025),
        Article(id="provider-b", title="Identical title", doi="10.9999/b", authors=["Bob"], year=2019)]
    agent.search.search_batch = lambda *a, **kw: {"all_articles": [row.model_dump() for row in rows],
        "articles": [{"article_id": "provider-b", "title": rows[1].title, "doi": rows[1].doi}], "total_found": 2}
    registry = {}
    result = agent.execute_tool("search_scientific", {"queries": ["general subject"], "top_k": 8}, registry, fake_client)
    assert len(ArticleStore(registry).works) == 2
    assert registry[result["articles"][0]["article_id"]].article.doi == "10.9999/b"


def test_similar_tool_keeps_versions_backend_but_only_one_visible_recommendation(fake_client):
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    original = versions()
    agent.search.search_scientific = lambda *a, **kw: {"articles": [record.article.model_dump() for record in original.values()], "warnings": []}
    registry = {}
    result = agent.execute_tool("find_similar_articles", {"article_or_topic": "Related sources", "limit": 8}, registry, fake_client)
    assert len(registry) == 2 and len(ArticleStore(registry).works) == 1
    assert len(result["articles"]) == 1 and "all_articles" not in result
