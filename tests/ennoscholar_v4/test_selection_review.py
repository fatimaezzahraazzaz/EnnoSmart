"""Selection is reviewed against this message's unseen candidates before long readings."""
import json

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.scientific_reading import next_task, reading_tasks
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_progressive_reading import corpus, analysis_for


def fillers(count=10):
    return [Article(title=f"Filler surface study {i}", abstract="Surface remeshing heuristics for triangle meshes.",
        authors=["F"], year=2023, doi=f"10.1234/filler{i}", verified=True) for i in range(count)]


def scenario():
    state, toolkit, config = corpus(4)
    ids = list(toolkit.registry)
    for aid in ids:
        toolkit.registry[aid].selected = False
        toolkit.registry[aid].abstract = f"Volumetric mesh generation method {aid}."
    toolkit.save()
    # A previous answer recommended another work; it must not be read first.
    state["evaluation"] = {"recommended_article_ids": [ids[3]]}
    state["messages"] = [{"role": "user", "content": "Quels sont les travaux les plus pertinents sur la génération de maillages, leurs méthodes et résultats ?"}]
    toolkit.sources.search.return_value = (fillers() + [toolkit.registry[aid].model_copy(update={"id": ""}) for aid in ids], [])
    return state, toolkit, config, ids


def memory_of(kw):
    return json.loads(kw["input"][0]["content"].split(" : ", 1)[1])


def test_discovery_request_reviews_unseen_candidates_before_reading_new_finalists():
    state, toolkit, config, ids = scenario()
    seen, read, hints = {}, [], {}

    def respond(n, kw):
        hints[n] = kw["instructions"]
        for item in kw["input"]:
            if item.get("type") == "function_call_output":
                out = json.loads(item["output"])
                seen.update({a["id"]: a for a in out.get("articles", [])})
        memory = memory_of(kw)
        if kw["tool_choice"] == {"type": "function", "name": "analyze_article"}:
            packet = memory["automatic_scientific_reading"]
            read.append(packet["article_id"])
            return call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save",
                "research_context": "meshes", "passage_query": "", "passage_offset": 0,
                "passages_per_article": 6, "analysis": analysis_for(packet)}, n)
        if kw["tool_choice"] == {"type": "function", "name": "finalize_response"}:
            return call("finalize_response", {"answer": "Synthèse [" + ids[0] + "]", "article_ids": [ids[0]],
                "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": ["partial"],
                "analyses": None, "displayed_article_ids": None}, n)
        if n == 1:
            assert "automatic_scientific_reading" not in memory
            return call("search_scientific", {"query": "volumetric mesh generation learning", "sources": None,
                "limit_per_source": 20, "offset": 0, "family": "volumetric"}, n)
        if n == 2:
            shown = [aid for aid in seen if aid not in ids]
            return call("record_candidate_decisions", {"decisions": [{"article_id": aid, "decision": "retain",
                "justification": "Surface remeshing only partly related."} for aid in shown[:2]],
                "finalist_ids": shown[:2], "covered_families": [], "remaining_axes": []}, n)
        if n == 3:
            assert "Selection review" in kw["instructions"]
            assert kw["tool_choice"] == "auto" and "automatic_scientific_reading" not in memory
            decisions = json.loads(kw["input"][-1]["output"])
            assert set(ids) <= {row["id"] for row in decisions["unreviewed_turn_candidates"]}
            return call("get_article", {"identifiers": ids[:2]}, n)
        if n == 4:
            assert {ids[0], ids[1]} <= set(seen)
            return call("record_candidate_decisions", {"decisions": [{"article_id": aid, "decision": "retain",
                "justification": "Generates volumetric meshes."} for aid in ids[:2]],
                "finalist_ids": ids[:2], "covered_families": ["volumetric"], "remaining_axes": []}, n)
        return call("finalize_response", {"answer": "Synthèse", "article_ids": [], "evidence_ids": [],
            "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}, n)

    client = Client(respond)
    events = []
    result = ScholarChatAgent(config, client=client, sources=toolkit.sources).run(state, emit=events.append)
    assert result["reason"] != "upstream_error", [e for e in events if e.get("status") == "failed"]
    assert read and set(read) <= {ids[0], ids[1]}
    assert ids[0] in read and ids[1] in read
    assert ids[3] not in read
    # Inspect (round 3) then confirm (round 4); the revised decision ends the review.
    assert [n for n, text in hints.items() if "Selection review" in text] == [3, 4]


def test_older_retained_works_are_not_deepened_for_a_new_selection():
    state, toolkit, config = corpus(3)
    ids = list(toolkit.registry)
    for aid in ids:
        toolkit.registry[aid].selected = False
    toolkit.registry[ids[0]].assessment = {"decision": "retain", "justification": "Earlier topic"}
    state["evaluation"] = {"recommended_article_ids": [ids[1], ids[2]]}
    toolkit.save()
    reading_tasks(state, toolkit.registry)
    assert {t["article_id"] for t in state["scientific_reading"]["tasks"]} == set(ids)
    task = next_task(state, toolkit.registry)
    assert task["article_id"] == ids[1]
    state["evaluation"] = {"recommended_article_ids": [ids[2], ids[1]]}
    reading_tasks(state, toolkit.registry)
    assert next_task(state, toolkit.registry)["article_id"] == ids[2]
    # A named work is still read even when it is not a current finalist.
    assert next_task(state, toolkit.registry, priority_ids=(ids[0],))["article_id"] == ids[0]
