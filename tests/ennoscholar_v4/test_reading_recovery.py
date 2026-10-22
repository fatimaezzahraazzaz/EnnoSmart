"""Mocked recovery after unavailable full texts and unverified priorities."""
import copy
import json
from dataclasses import replace

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.scientific_reading import recovery_context, reading_tasks
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_progressive_reading import corpus, analysis_for


def blocked_priorities(monkeypatch):
    from agents.EnnoScholarV4 import toolkit as toolkit_module
    state, toolkit, config = corpus(4)
    ids = list(toolkit.registry)
    blocked = set(ids[:2])
    for aid, paper in toolkit.registry.items():
        paper.abstract = f"The authors propose mechanism {aid} for the scientific problem."
        paper.selected = aid in blocked
        if aid in blocked:
            paper.fulltext = {}
            paper.verified = False
            paper.assessment = {"decision": "retain", "justification": "Relevant abstract"}
    toolkit.save()
    state["messages"] = [{"role": "user", "content": "Prépare un état de l'art, compare les méthodes et propose un plan avant rédaction."}]
    def verify(paper, deadline):
        valid = paper.id not in blocked
        return paper.model_copy(update={"verified": valid,
            "metadata_errors": [] if valid else ["DOI registry unavailable; verify original publication page"]})
    toolkit.sources.verify.side_effect = verify
    def fulltext(paper, *args, **kwargs):
        assert paper.id in blocked
        return {"url": paper.url, "text": "", "chunks": [], "access_level": "abstract_only",
            "access_status": "abstract_only", "attempts": [{"url": paper.url, "error": "TimeoutError",
                "reason": "Synthetic download timeout"}, {"url": paper.url, "error": "ValueError",
                "reason": "Synthetic redirect failure"}]}
    monkeypatch.setattr(toolkit_module, "get_fulltext", fulltext)
    return state, toolkit, config, ids


def save_packet(packet, number):
    if packet["basis"] == "fulltext":
        analysis = analysis_for(packet)
    else:
        analysis = analysis_for({"passages": []})
        row = packet["passages"][0]
        analysis["method"] = [{"statement": row["exact_quote"], "evidence_ids": [row["evidence_id"]],
            "origin": "source_statement"}]
        analysis["analysis_limit"] = "Abstract only: experiments, numerical results and limitations not examined."
    return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save",
        "research_context": "scientific comparison", "passage_query": "", "passage_offset": 0,
        "passages_per_article": 6, "analysis": analysis}, number)


def save_blueprint(state, focus, number):
    ids = focus["article_ids"]
    return call("build_argument_blueprint", {"action": "save", "merge": focus["merge"],
        "global_question": "Which mechanisms are supported?", "narrative_logic": "Separate evidence levels.",
        "axes": [{"axis": "methods", "scientific_problem": "What is established?", "section_id": None,
            "steps": [{"kind": "method", "statement": state["articles"][aid]["analysis"]["method"][0]["statement"],
                "article_ids": [aid], "evidence_ids": state["articles"][aid]["analysis"]["method"][0]["evidence_ids"],
                "relation_ids": [], "project_fact_ids": [], "conditions": "Available source only",
                "uncertainty": "Sources have different levels of evidence"} for aid in ids], "transition": ""}],
        "remaining_questions": ["Unavailable full texts remain incomplete"]}, number)


def test_failed_priorities_trigger_local_candidate_review_before_deep_comparison(monkeypatch):
    state, toolkit, config, ids = blocked_priorities(monkeypatch)
    recoveries, comparisons, read = [], [], []
    events = []
    def respond(number, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            if packet["article_id"] not in read:
                assert toolkit.sources.verify.call_count >= len(set(read))+1
            read.append(packet["article_id"])
            return save_packet(packet, number)
        recovery = memory.get("scientific_reading_recovery")
        if recovery:
            assert kw["tool_choice"] == "auto"
            recoveries.append(recovery)
            assert recovery["unassessed_total"] == 2
            assert all(not p["body_extracted"] and not p["bibliography_verified"] for p in recovery["blocked_articles"])
            assert all(p["access_failures"] for p in recovery["blocked_articles"])
            if len(recoveries) == 1:
                return call("search_collected_articles", {"query": "", "limit": 8,
                    "exclude_presented": True, "family": None}, number)
            return call("record_candidate_decisions", {"decisions": [{"article_id": aid, "decision": "retain",
                "justification": "The inspected abstract describes relevant mechanisms"} for aid in ids[2:]],
                "finalist_ids": ids, "covered_families": ["methods"], "remaining_axes": []}, number)
        focus = memory.get("argumentation_focus", {})
        if focus.get("stage") == "comparison":
            assert set(focus["article_ids"]) == set(ids[2:])
            comparisons.append(focus)
            evidence = [p["proofs"][0]["evidence_id"] for p in focus["packet"]["papers"]]
            return call("compare_articles", {"article_ids": ids[2:], "research_context": "scientific conditions",
                "conclusions": None, "relations": [{"article_ids": ids[2:], "kind": "alternative",
                    "statement": "Documented alternatives with distinct protocols", "origin": "comparative_inference",
                    "conditions": "Keep metrics M0/M1/M2 and protocols P0/P1/P2 separate", "experimental_results": [],
                    "evidence_ids": evidence, "comparable_protocols": False, "uncertainties": ["No superiority"]}]}, number)
        if focus.get("stage") == "blueprint":
            return save_blueprint(state, focus, number)
        return call("finalize_response", {"answer": "Méthodes documentées " + " ".join(f"[{aid}]" for aid in ids[2:]),
            "article_ids": ids[2:], "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": ["Two initial sources remain unverified and abstract-only"],
            "analyses": None, "displayed_article_ids": ids[2:]}, number)
    result = ScholarChatAgent(config, client=Client(respond), sources=toolkit.sources).run(state, emit=events.append)
    assert result["publication_accepted"], (result["reason"], result["tool_counts"], result["finalization_refusals"])
    assert len(recoveries) == 2 and comparisons
    assert set(read) == set(ids)
    # Finalists are verified in grouped calls: fewer tools, every work still checked.
    assert result["tool_counts"]["verify_bibliography"] <= 4 and toolkit.sources.verify.call_count >= 4
    first_verify = next(i for i,e in enumerate(events) if e["tool"] == "verify_bibliography" and e["status"] == "completed")
    first_read = next(i for i,e in enumerate(events) if e["tool"] == "get_fulltext")
    assert first_verify < first_read
    tasks = {t["article_id"]: t for t in state["scientific_reading"]["tasks"]}
    for aid in ids[:2]:
        assert tasks[aid]["status"] == "partial" and not tasks[aid]["coverage_sufficient"]
        assert tasks[aid]["field_status"]["limitations"] == "body_not_examined"
        assert not state["articles"][aid]["analysis"]["results"]
        assert not state["articles"][aid]["analysis"]["experimental_setup"]
    for aid in ids[2:]:
        assert len(state["articles"][aid]["analysis"]["method"]) == 3
        assert len(state["articles"][aid]["analysis"]["results"]) == 4
        assert tasks[aid]["coverage_sufficient"]
    assert not result["finalization_refusals"]
    assert result["tool_calls"] <= config.max_tool_calls


def test_recovery_without_decisions_stops_after_two_rounds_without_abstract_comparison(monkeypatch):
    state, toolkit, config, ids = blocked_priorities(monkeypatch)
    recoveries = []
    def respond(number, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            return save_packet(memory["automatic_scientific_reading"], number)
        if memory.get("scientific_reading_recovery"):
            recoveries.append(memory["scientific_reading_recovery"])
            return call("search_collected_articles", {"query": "", "limit": 8,
                "exclude_presented": True, "family": None}, number)
        focus = memory.get("argumentation_focus", {})
        assert focus.get("stage") != "comparison"
        if focus.get("stage") == "blueprint":
            return save_blueprint(state, focus, number)
        return call("finalize_response", {"answer": "Sources insuffisantes ; analyses limitées aux abstracts.",
            "article_ids": [], "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": ["Downloads failed; two candidates still unassessed"],
            "analyses": None, "displayed_article_ids": []}, number)
    result = ScholarChatAgent(config, client=Client(respond), sources=toolkit.sources).run(state)
    assert result["publication_accepted"], (result["reason"], result["tool_counts"])
    assert len(recoveries) == 2
    assert result["tool_counts"].get("compare_articles", 0) == 0
    assert not result["coverage_sufficient"]
    assert all(not state["articles"][aid]["analysis"]["results"] for aid in ids[:2])


def test_recovery_context_does_not_select_sources_or_repeat_the_priority(monkeypatch):
    state, toolkit, _, ids = blocked_priorities(monkeypatch)
    reading_tasks(state, toolkit.registry)
    original = copy.deepcopy(state)
    packet = recovery_context(state, toolkit.registry)
    assert packet["candidate_ids"] == ids[2:]
    assert state == original
    toolkit.registry[ids[2]].selection_override = False
    assert recovery_context(state, toolkit.registry)["candidate_ids"] == ids[3:]


def test_existing_sufficient_body_reading_does_not_reopen_discovery(monkeypatch):
    state, toolkit, _, ids = blocked_priorities(monkeypatch)
    reading_tasks(state, toolkit.registry)
    state["scientific_reading"]["tasks"][0]["coverage_sufficient"] = True
    assert recovery_context(state, toolkit.registry) is None


def test_budget_can_stop_recovery_before_its_two_round_limit(monkeypatch):
    state, toolkit, config, _ = blocked_priorities(monkeypatch)
    config = replace(config, max_tool_rounds=1)
    recoveries = []
    def respond(number, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            return save_packet(memory["automatic_scientific_reading"], number)
        if kw["tool_choice"] != {"type": "function", "name": "finalize_response"}:
            assert memory.get("scientific_reading_recovery")
            recoveries.append(memory["scientific_reading_recovery"])
            return call("search_collected_articles", {"query": "", "limit": 8,
                "exclude_presented": True, "family": None}, number)
        assert memory["scientific_reading_status"]["remaining_article_ids"]
        return call("finalize_response", {"answer": "Budget atteint ; sources insuffisantes.", "article_ids": [],
            "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": ["Unassessed sources"],
            "analyses": None, "displayed_article_ids": []}, number)
    result = ScholarChatAgent(config, client=Client(respond), sources=toolkit.sources).run(state)
    assert result["publication_accepted"], (result["reason"], result["tool_counts"])
    assert len(recoveries) == 1
    assert result["exploration_stop_reason"] == "exploration_round_budget"
    assert result["tool_counts"].get("compare_articles", 0) == 0


def test_legacy_abstract_analysis_is_retained_but_coverage_becomes_partial(monkeypatch):
    from agents.EnnoScholarV4.scientific_reading import completed
    from tests.ennoscholar_v4.test_progressive_reading import prepare
    state, toolkit, _, _ = blocked_priorities(monkeypatch)
    task = reading_tasks(state, toolkit.registry)["tasks"][0]
    packet = prepare(state, toolkit, task)
    arguments = json.loads(save_packet(packet, 1)[0]["arguments"])
    toolkit.execute("analyze_article", arguments, float("inf"))
    completed(state, toolkit.registry[task["article_id"]])
    task["status"] = "analyzed"  # Persisted status produced by the previous implementation.
    before = copy.deepcopy(toolkit.registry[task["article_id"]].analysis)
    reading_tasks(state, toolkit.registry)
    assert task["status"] == "partial" and task["exhausted"]
    assert not task["coverage_sufficient"]
    assert task["field_status"]["limitations"] == "body_not_examined"
    assert toolkit.registry[task["article_id"]].analysis == before


def test_targeted_followup_does_not_reopen_unrelated_discovery(monkeypatch):
    state, toolkit, config, ids = blocked_priorities(monkeypatch)
    state["messages"] = [{"role": "user", "content": "Approfondis Synthetic study 0 et compare ses méthodes."}]
    def respond(number, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        assert not memory.get("scientific_reading_recovery")
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            return save_packet(memory["automatic_scientific_reading"], number)
        focus = memory.get("argumentation_focus", {})
        assert focus.get("stage") != "comparison"
        if focus.get("stage") == "blueprint":
            return save_blueprint(state, focus, number)
        return call("finalize_response", {"answer": "Texte intégral indisponible ; analyse limitée aux abstracts.",
            "article_ids": [], "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": ["Unverified identities"], "analyses": None, "displayed_article_ids": []}, number)
    result = ScholarChatAgent(config, client=Client(respond), sources=toolkit.sources).run(state)
    assert result["publication_accepted"], (result["reason"], result["tool_counts"])
    assert result["tool_counts"].get("search_collected_articles", 0) == 0


def test_untitled_discovered_urls_remain_visible_without_assigning_article_identity(monkeypatch):
    state, toolkit, _, ids = blocked_priorities(monkeypatch)
    reading_tasks(state, toolkit.registry)
    state["web_sources"] = [{"url": f"https://repository.example/abs/{i}", "title": ""} for i in range(38)]
    original = copy.deepcopy(state)
    packet = recovery_context(state, toolkit.registry)
    assert packet["discovered_sources_total"] == 38
    assert packet["discovered_sources_partial"]
    assert len(packet["unverified_discovered_sources"]) == 12
    assert packet["unverified_discovered_sources"][0]["url"] == state["web_sources"][0]["url"]
    assert not any(p.get("arxiv_id") for p in state["articles"].values())
    assert state == original


def test_download_deadline_diagnosis_reproduces_late_alternative_without_network(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from agents.EnnoScholarV4.tools import fulltext
    from agents.EnnoScholarV4.models import Article
    from agents.EnnoScholarV4.config import ScholarV4Config
    clock = [0.0]
    monkeypatch.setattr(fulltext, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    paper = Article(title="Synthetic scientific study", doi="10.1234/download-diagnostic",
        abstract="Abstract only.", pdf_url="https://publisher.example/paper.pdf",
        url="https://doi.org/10.1234/download-diagnostic")
    calls = []
    def fetch(url, deadline, *args, **kwargs):
        calls.append((url, round(deadline-clock[0], 3)))
        if url == paper.pdf_url:
            clock[0] = min(clock[0] + 35, deadline)                  # a slow server, cut at its own deadline
            raise TimeoutError("Délai de téléchargement atteint.")
        if url == paper.url:
            clock[0] += 1.5
            raise ValueError("Trop de redirections.")
        clock[0] = deadline
        raise TimeoutError("Délai de téléchargement atteint.")
    monkeypatch.setattr(fulltext, "fetch_public", fetch)
    sources = Mock()
    def metadata(*args):
        clock[0] += 5
        return [paper.model_copy(update={"arxiv_id": "2601.12345"})]
    sources.metadata.side_effect = metadata
    result = fulltext.get_fulltext(paper, sources, 600, config=ScholarV4Config(api_key="mock"))
    # Diagnosis (earlier policy): the publisher PDF took 35 s and the open arXiv version, found
    # late, kept 3.5 s, its PDF never tried. Now the remaining time is shared: no URL takes more
    # than 25 s while others remain, each alternative keeps at least 5 s, and the article's own
    # deadline (max_download_seconds = 45 s) still bounds the whole.
    assert [url for url, _ in calls] == [paper.pdf_url, paper.url, "https://arxiv.org/html/2601.12345",
        "https://arxiv.org/pdf/2601.12345"]
    assert calls[0][1] <= 25 and all(seconds >= 5 for _, seconds in calls[2:])
    assert sum(a["seconds"] for a in result["attempts"]) + 5 <= ScholarV4Config(api_key="mock").max_download_seconds
    assert result["text"] == "" and result["access_status"] == "abstract_only"
    assert [a["error"] for a in result["attempts"]] == ["TimeoutError", "ValueError", "TimeoutError", "TimeoutError"]


def test_redirect_diagnosis_confirms_five_hop_limit_with_mocked_transport(monkeypatch):
    from contextlib import contextmanager
    import time
    import httpx
    import pytest
    from agents.EnnoScholarV4 import network
    calls = []
    class Transport:
        def __init__(self, **kwargs):
            assert kwargs == {"follow_redirects": False, "trust_env": False}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append(url)
            yield httpx.Response(302, headers={"location": f"/step/{len(calls)}"}, request=httpx.Request(method, url))
    monkeypatch.setattr(network.httpx, "Client", Transport)
    monkeypatch.setattr(network, "resolve_public", lambda url, deadline: (url, "publisher.example"))
    with pytest.raises(ValueError, match="Trop de redirections"):
        network.fetch_public("https://publisher.example/start", time.monotonic()+30)
    assert calls == ["https://publisher.example/start", *[f"https://publisher.example/step/{i}" for i in range(1, 5)]]


def test_resumed_abstract_cards_are_verified_before_recovery_without_repeating_downloads(monkeypatch):
    from agents.EnnoScholarV4.scientific_reading import completed
    from tests.ennoscholar_v4.test_progressive_reading import prepare
    state, toolkit, config, ids = blocked_priorities(monkeypatch)
    for task in reading_tasks(state, toolkit.registry)["tasks"]:
        packet = prepare(state, toolkit, task)
        arguments = json.loads(save_packet(packet, 1)[0]["arguments"])
        toolkit.execute("analyze_article", arguments, float("inf"))
        completed(state, toolkit.registry[task["article_id"]])
        task["status"] = "analyzed"
    toolkit.save()
    previous = {aid: copy.deepcopy(state["articles"][aid]["analysis"]) for aid in ids[:2]}
    toolkit.sources.verify.reset_mock()
    recovery_count = []
    def respond(number, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        assert toolkit.sources.verify.call_count == 2
        if memory.get("scientific_reading_recovery"):
            recovery_count.append(memory["scientific_reading_recovery"])
            assert all(t.get("type") != "web_search" for t in kw["tools"])
            assert all(p["bibliography_errors"] for p in memory["scientific_reading_recovery"]["blocked_articles"])
            return call("search_collected_articles", {"query": "", "limit": 8,
                "exclude_presented": True, "family": None}, number)
        focus = memory.get("argumentation_focus", {})
        assert focus.get("stage") != "comparison"
        if focus.get("stage") == "blueprint":
            return save_blueprint(state, focus, number)
        return call("finalize_response", {"answer": "Les sources restent insuffisantes et les notices non vérifiées.",
            "article_ids": [], "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": ["Unassessed candidates"], "analyses": None, "displayed_article_ids": []}, number)
    result = ScholarChatAgent(replace(config, web_search_enabled=True), client=Client(respond), sources=toolkit.sources).run(state)
    assert result["publication_accepted"], (result["reason"], result["tool_counts"])
    assert len(recovery_count) == 2
    assert result["tool_counts"]["verify_bibliography"] == 1  # Both stale cards in one validation window.
    assert result["tool_counts"].get("get_fulltext", 0) == 0
    assert all(state["articles"][aid]["analysis"] == previous[aid] for aid in ids[:2])
    assert all(t["status"] == "partial" for t in state["scientific_reading"]["tasks"])


def test_successful_alternative_restarts_progressive_reading_in_the_same_run(monkeypatch):
    from agents.EnnoScholarV4 import toolkit as toolkit_module
    state, toolkit, config, ids = blocked_priorities(monkeypatch)
    _, body_toolkit, _ = corpus(1)
    body = copy.deepcopy(body_toolkit.registry[ids[0]].fulltext)
    alternative = "https://repository.example/author-version"
    body.update(url=alternative, access_status="fulltext_extracted", access_level="fulltext", source_kind="html")
    failure = toolkit_module.get_fulltext
    def fulltext(paper, sources, deadline, check, candidate_urls, config, pdf_only=False):
        if candidate_urls == [alternative]:
            return copy.deepcopy(body)
        return failure(paper, sources, deadline, check, candidate_urls, config, pdf_only=pdf_only)
    monkeypatch.setattr(toolkit_module, "get_fulltext", fulltext)
    state["web_sources"] = [{"url": alternative, "title": ""}]
    body_windows = []
    alternatives = []
    def respond(number, kw):
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            if packet["basis"] == "fulltext":
                body_windows.append(packet)
            return save_packet(packet, number)
        if memory.get("scientific_reading_recovery") and not alternatives:
            alternatives.append(alternative)
            return call("get_fulltext", {"article_id": ids[0], "candidate_urls": [alternative],
                "page_start": None, "page_limit": None}, number)
        focus = memory.get("argumentation_focus", {})
        if focus.get("stage") == "blueprint":
            return save_blueprint(state, focus, number)
        return call("finalize_response", {"answer": "Analyse partielle ; identité bibliographique encore incertaine.",
            "article_ids": [], "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": ["One source remains abstract-only"], "analyses": None, "displayed_article_ids": []}, number)
    result = ScholarChatAgent(config, client=Client(respond), sources=toolkit.sources).run(state)
    assert result["publication_accepted"], (result["reason"], result["tool_counts"])
    assert len(body_windows) >= 3
    stored = state["articles"][ids[0]]["analysis"]
    assert len(stored["results"]) == 4 and len(stored["limitations"]) == 3
    assert len(stored["method"]) == 4  # The original abstract claim is also retained.
    task = next(t for t in state["scientific_reading"]["tasks"] if t["article_id"] == ids[0])
    assert task["status"] == "analyzed" and task["coverage_sufficient"]
