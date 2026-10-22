"""Discovery and ranking: independent bench and adversarial scenarios.

MEASURED tests score real recorded outputs with the independent judgments of
bench/discovery_judgments.json (sessions read in mode=ro). SIMULATED tests replace the
model's judgments by fixed ones and only check what the code does with them. No model,
provider or network call. No scientific title is expected anywhere in production code.
"""
import copy
import json

import pytest

from agents.EnnoScholarV4 import research
from tests.ennoscholar_v4.bench import metrics, run as bench


@pytest.fixture(autouse=True)
def offline_documents(monkeypatch):
    from agents.EnnoScholarV4.tools import fulltext

    def refuse(*args, **kwargs):
        raise ConnectionError("offline test: no download")
    monkeypatch.setattr(fulltext, "fetch_public", refuse)


def recorded():
    if not bench.DB.is_file():
        pytest.skip("Base locale absente")
    return [(d, p) for d, prefixes in bench.SESSIONS.items() for p in prefixes]


# --- The bench itself -------------------------------------------------------------------------

def test_the_judgments_cover_every_notice_of_the_bench_pools_and_document_their_criteria():
    data = json.loads(metrics.JUDGMENTS.read_text(encoding="utf-8"))
    assert {"assessor", "protocol"} <= set(data)
    for domain, prefixes in bench.SESSIONS.items():
        rubric = data["domains"][domain]
        assert rubric["need"] and set(rubric["grades"]) == {"0", "1", "2", "3"}
        assert sum(j["grade"] >= metrics.RELEVANT for j in rubric["judgments"].values()) >= 5
        for prefix in prefixes:
            if not bench.DB.is_file():
                continue
            state = bench.load_session(prefix)
            missing = [aid for aid, a in state["articles"].items() if metrics.work_key(a) not in rubric["judgments"]]
            assert not missing, (domain, prefix, missing[:3])


def test_metrics_follow_their_definitions():
    grade = {"a": 3, "b": 0, "c": 2, "d": 1, "e": 3}
    assert metrics.precision_at(["a", "b", "c", "d", "e"], grade) == .6
    assert metrics.recall_at(["b", "a"], grade, 3, k=2) == pytest.approx(1/3)
    assert metrics.ndcg_at(["a", "e", "c", "d", "b"], grade, grade.values()) == pytest.approx(1.0)
    assert metrics.ndcg_at(["b", "d", "c", "e", "a"], grade, grade.values()) < .7


# --- MEASURED on recorded runs -----------------------------------------------------------------

def test_measured_figures_of_the_recorded_runs_are_reported_from_independent_judgments(capsys):
    rows = [bench.measure(d, p) for d, p in recorded()]
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
        assert 0 <= row["recorded_finalists"]["P@5"] <= 1 and row["relevant"] >= 1
    # Recorded weakness of the meshing runs this change targets (not a target for the model):
    weak = {r["session"]: r["recorded_finalists"]["P@5"] for r in rows if r["domain"] == "mesh"}
    assert weak["04382dc6"] <= .2 and weak["1e5e467e"] <= .4


def test_the_tier_rule_never_lowers_the_measured_top_five_of_recorded_decisions():
    """Applied to the model's real recorded decisions (contribution, profiles), the rule keeps or
    improves P@5 and NDCG@5 against independent judgments in every recorded run."""
    gains = 0
    for domain, prefix in recorded():
        row = bench.measure(domain, prefix)
        before, after = row["recorded_finalists"], row["tier_rule_on_recorded_decisions"]
        assert after["P@5"] >= before["P@5"] and after["NDCG@5"] >= before["NDCG@5"] - 1e-9, row
        gains += after["NDCG@5"] > before["NDCG@5"]
    assert gains >= 2


def test_the_triage_presentation_order_keeps_its_measured_recall():
    """Guard: a lexical reordering by the need's words was tried and lowered Recall@20 from 0.47 to
    0.32-0.34 on these pools; the round-robin order stays."""
    values = [bench.measure(d, p)["code_triage_order"] for d, p in recorded()]
    assert sum(values) / len(values) >= .45


# --- SIMULATED: what the rule does with given criteria judgments --------------------------------

def test_simulated_exact_criteria_judgments_give_a_relevant_top_five_wherever_one_exists():
    for domain, prefix in recorded():
        result = bench.simulate(domain, prefix)
        state = bench.load_session(prefix)
        _, grade, pool, relevant = bench._context(domain, state)
        assert result["after"]["P@5"] >= min(1.0, relevant / 5) - 1e-9, (domain, prefix, result)
        assert result["after"]["P@5"] >= result["before"]["P@5"]


def test_simulated_noisy_criteria_judgments_still_keep_the_precision_of_the_recorded_start():
    worse = [(d, p) for d, p in recorded() if bench.simulate(d, p, .25, 7)["after"]["P@5"] < bench.simulate(d, p)["before"]["P@5"]]
    assert not worse


# --- Adversarial scenarios, any field -----------------------------------------------------------

NEED = {"object": "catalyseur hétérogène", "problem": "réduire le CO2 en méthanol à basse pression",
    "expected_output": "un catalyseur testé produisant du méthanol depuis CO2", "acceptable_methods": [],
    "application_context": "procédé industriel", "constraints": [],
    "criteria": [{"id": "c1", "statement": "Le travail produit et teste un catalyseur de conversion CO2 → méthanol.", "essential": True},
        {"id": "c2", "statement": "Démonstration en conditions de basse pression (≤ 30 bar).", "essential": True},
        {"id": "c3", "statement": "Stabilité rapportée sur plus de 100 h.", "essential": False}],
    "direct": "catalyseur testé CO2→méthanol basse pression", "related": "autre réaction ou haute pression",
    "foundational": "mécanismes de référence",
    "families": [{"family": "Cu/ZnO", "query": "Cu ZnO catalyst CO2 methanol low pressure", "why": "industriel"},
        {"family": "In2O3", "query": "In2O3 catalyst CO2 hydrogenation methanol", "why": "sélectivité"}],
    "uncertainties": []}


def corpus(n=8):
    """A pool of notices; titles carry no meaning for the code under test."""
    state = {"articles": {}, "turn_goal": {"deliverable": "literature_search", "search_needed": True, "understanding": "x"},
        "research_need": copy.deepcopy(NEED), "research_progress": {"requested_count": 5}, "evaluation": {}}
    for i in range(n):
        state["articles"][f"paper_{i}"] = {"id": f"paper_{i}", "title": f"Work {i}", "abstract": f"Abstract {i}.",
            "doi": f"10.1/w{i}", "verified": True, "selection_override": None, "assessment": {}, "analysis": {}}
    return state


def judge(state, aid, c1, c2, contribution="direct", decision="retain"):
    state["articles"][aid]["assessment"] = {"decision": decision, "contribution": contribution, "relevance": {"criteria": [
        {"criterion_id": "c1", "status": c1, "basis": "abstract", "note": "n"},
        {"criterion_id": "c2", "status": c2, "basis": "abstract", "note": "n"}]},
        "need_signature": research.need_signature(state), "source_signature": research.candidate_signature(state, aid)}


def test_a_lookalike_failing_an_essential_criterion_yields_its_place_with_the_compared_criteria():
    """The 2D-mesh case in any field: a work very close in wording but failing an essential condition."""
    state = corpus()
    for i in range(4):
        judge(state, f"paper_{i}", "met", "met")
    judge(state, "paper_4", "met", "not_met")                                   # lookalike: wrong pressure/dimension
    state["evaluation"]["recommended_article_ids"] = [f"paper_{i}" for i in range(5)]
    judge(state, "paper_7", "met", "met")                                        # late direct work
    events = research.rebalance_ranking(state)
    assert [e["outgoing_id"] for e in events] == ["paper_4"] and events[0]["incoming_id"] == "paper_7"
    assert any("c2" in line and "sortant not_met" in line for line in events[0]["information_used"])
    assert state["research_progress"]["ranking_changes"][-1]["need_signature"] == research.need_signature(state)
    assert "paper_4" not in state["evaluation"]["recommended_article_ids"]


def test_a_related_work_rich_in_keywords_never_outranks_a_direct_one():
    state = corpus()
    state["articles"]["paper_1"]["title"] = "Methanol CO2 catalyst low pressure methanol CO2 catalyst methanol"
    judge(state, "paper_0", "met", "met")
    judge(state, "paper_1", "partial", "unknown", contribution="related")
    state["evaluation"]["recommended_article_ids"] = ["paper_1", "paper_0"]
    research.rebalance_ranking(state)
    assert state["evaluation"]["recommended_article_ids"] == ["paper_0", "paper_1"]


def test_equal_tiers_never_churn_and_a_second_pass_changes_nothing():
    state = corpus()
    for i in range(8):
        judge(state, f"paper_{i}", "met", "met")
    state["evaluation"]["recommended_article_ids"] = [f"paper_{i}" for i in range(5)]
    assert research.rebalance_ranking(state) == []
    assert state["evaluation"]["recommended_article_ids"] == [f"paper_{i}" for i in range(5)]
    judge(state, "paper_2", "met", "not_met")
    assert len(research.rebalance_ranking(state)) == 1
    snapshot = copy.deepcopy(state["evaluation"])
    assert research.rebalance_ranking(state) == [] and state["evaluation"] == snapshot


def test_an_incomplete_notice_cannot_displace_a_finalist_meeting_every_essential_criterion():
    state = corpus()
    for i in range(5):
        judge(state, f"paper_{i}", "met", "met")
    state["evaluation"]["recommended_article_ids"] = [f"paper_{i}" for i in range(5)]
    judge(state, "paper_6", "met", "unknown")                                   # abstract truncated: c2 not established
    assert research.rebalance_ranking(state) == []
    judge(state, "paper_3", "not_met", "met")                                   # …but it displaces a failing finalist
    events = research.rebalance_ranking(state)
    assert [(e["incoming_id"], e["outgoing_id"]) for e in events] == [("paper_6", "paper_3")]
    assert events[0]["uncertainties"] == ["c2 non établi pour l'entrant"]


def test_the_consultant_and_stale_judgments_are_never_overridden():
    state = corpus()
    for i in range(5):
        judge(state, f"paper_{i}", "met", "not_met")
    state["articles"]["paper_0"]["selection_override"] = True                  # consultant accepted
    state["consultant_order"] = ["paper_1"]
    state["evaluation"]["recommended_article_ids"] = [f"paper_{i}" for i in range(5)]
    judge(state, "paper_6", "met", "met")
    judge(state, "paper_7", "met", "met")
    state["articles"]["paper_7"]["assessment"]["need_signature"] = "older need"  # judged for another need
    events = research.rebalance_ranking(state)
    assert {e["incoming_id"] for e in events} == {"paper_6"} and "paper_0" not in {e["outgoing_id"] for e in events}
    assert state["evaluation"]["recommended_article_ids"][0] == "paper_1"
    assert "paper_0" in state["evaluation"]["recommended_article_ids"]


def test_distinct_works_with_close_titles_stay_distinct_and_versions_stay_one_work():
    state = corpus()
    state["articles"]["paper_0"]["title"] = "NetGen: neural generation"
    state["articles"]["paper_1"]["title"] = "NetGen3D: neural generation in three dimensions"
    groups = research.version_groups(state)
    assert groups["paper_0"] != groups["paper_1"]
    state["version_correspondences"] = {"paper_2": [{"article_id": "paper_3", "relation": "verified_metadata_preprint_alternative"}]}
    groups = research.version_groups(state)
    assert groups["paper_2"] == groups["paper_3"]
    for i in range(6):
        judge(state, f"paper_{i}", "met", "met")
    state["evaluation"]["recommended_article_ids"] = ["paper_0", "paper_1", "paper_2"]
    research.rebalance_ranking(state)                                          # vacancies: never two versions
    ranked = state["evaluation"]["recommended_article_ids"]
    assert {"paper_0", "paper_1"} <= set(ranked) and not {"paper_2", "paper_3"} <= set(ranked)


def test_a_reference_in_a_bibliography_is_a_lead_to_verify_not_a_candidate():
    state = corpus()
    state["articles"]["paper_0"]["fulltext"] = {"text": "Body.\n\nReferences\n[1] A. B. Catalysts. J. Cat. 2020. "
        "https://doi.org/10.1016/j.jcat.2020.000001\n[2] C. D. 2019. 10.1021/acscatal.9b00001\n[3] E. F. 2018."}
    leads = research.collect_reference_leads(state)
    assert {lead["identifier"] for lead in leads.values()} >= {"10.1016/j.jcat.2020.000001", "10.1021/acscatal.9b00001"}
    assert all(lead["status"] == "unverified" and not lead["identity_verified"] for lead in leads.values())
    assert len(state["articles"]) == 8                                         # nothing becomes a publication


def test_a_planned_family_never_searched_blocks_reading_until_explored_or_justified():
    state = corpus()
    for article in state["articles"].values():
        article["abstract"] = ""                                                # nothing left to triage
    for i in range(5):
        judge(state, f"paper_{i}", "met", "met")
    state["evaluation"]["recommended_article_ids"] = [f"paper_{i}" for i in range(5)]
    for article in state["articles"].values():
        article["assessment"] = article["assessment"] or {"decision": "reject"}
    state["recent_searches"] = [{"query": "Cu ZnO catalyst CO2 methanol low pressure", "family": "Cu/ZnO"}]
    state["research_progress"]["ranking_records"] = {aid: {"dependencies": {aid: research.candidate_signature(state, aid)},
        "need_signature": research.need_signature(state)} for aid in state["articles"]}
    options = research.exploration_options(state)
    assert [f["searched"] for f in options["planned_families"]] == [True, False]
    review = lambda families: research.DiscoveryReviewArgs(ready_for_reading=True, families=families, reason="r")
    covered = research.DiscoveryFamily(family="Cu/ZnO", status="covered", reason="r", article_ids=["paper_0"])
    with pytest.raises(ValueError, match="jamais interrogées : In2O3"):
        research.save_discovery_review(state, review([covered]))
    absent = research.DiscoveryFamily(family="In2O3", status="not_relevant", reason="hors pression visée")
    assert research.save_discovery_review(state, review([covered, absent]))["saved"]


def test_the_need_is_validated_persisted_apart_from_the_goal_and_invalidates_old_judgments():
    from agents.EnnoScholarV4.turn import TurnGoalArgs, record_goal
    goal = {"requested_article_count": 5, "deliverable": "literature_search", "lock_ids": [], "focus_lock_ids": [],
        "exact_quote": "", "reading_depth": "core", "understanding": "u", "comparison_requested": False, "search_needed": True,
        "article_ids": None, "article_scope": "open", "references_ambiguous": False, "constraints": None, "research_need": NEED}
    state = {"messages": [{"id": "m1", "role": "user", "content": "Trouve 5 articles."}], "articles": {}}
    before = research.need_signature(state)
    record_goal(state, TurnGoalArgs.model_validate(goal), "Trouve 5 articles.", {})
    assert state["research_need"]["criteria"][0]["id"] == "c1" and "research_need" not in state["turn_goal"]
    assert research.need_signature(state) != before
    bad = copy.deepcopy(NEED)
    bad["criteria"] = [{**c, "essential": False} for c in bad["criteria"]]
    with pytest.raises(ValueError):
        TurnGoalArgs.model_validate({**goal, "research_need": bad})
    bad = copy.deepcopy(NEED)
    bad["families"].append(dict(bad["families"][0]))
    with pytest.raises(ValueError):
        TurnGoalArgs.model_validate({**goal, "research_need": bad})


def test_the_need_reaches_selection_and_review_requests_but_not_a_reading_window():
    from agents.EnnoScholarV4.context import TASK_KEYS, COMMON_KEYS
    assert "research_need" in COMMON_KEYS and "research_need" in TASK_KEYS["selection"]
    assert "research_need" not in TASK_KEYS["reading"]
    state = corpus()
    judge(state, "paper_0", "met", "met")
    from agents.EnnoScholarV4.context import scientific_dossier
    dossier = scientific_dossier(state, "paper_0")
    assert dossier["qualification"]["tier"] == 0 and dossier["qualification"]["current"]


def test_rebalancing_runs_inside_the_decision_tool_without_any_model_call():
    """The tool path: decisions recorded with criteria, the list updated locally and explained."""
    from unittest.mock import Mock
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from agents.EnnoScholarV4.models import Article
    state = {"messages": [{"role": "user", "content": "Trouve 2 articles."}], "articles": {},
        "turn_goal": {"deliverable": "literature_search", "search_needed": True, "understanding": "u"},
        "research_need": copy.deepcopy(NEED), "research_progress": {"requested_count": 2}}
    t = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    ids, _ = t.store.insert([Article(title=title, abstract="A catalyst study, abstract " + str(i), doi=f"10.1234/cat{i}",
        verified=True, authors=["A"], year=2024) for i, title in enumerate(["Copper zinc catalyst for methanol",
        "Indium oxide catalyst hydrogenation", "Palladium catalysts for formate"])])
    assert len(ids) == 3
    t.store.exposed.extend(ids)
    import hashlib
    for aid in ids:
        state.setdefault("full_abstract_receipts", {})[aid] = hashlib.sha256(t.registry[aid].abstract.encode()).hexdigest()
    profile = lambda c1, c2: {"problem": "p", "output": "o", "method": "m", "object_scope": "s", "ai_role": "", "conditions": "",
        "results_validation": "", "fit": "f", "uncertainties": [], "missing_information": [], "criteria": [
            {"criterion_id": "c1", "status": c1, "basis": "abstract", "note": "n"},
            {"criterion_id": "c2", "status": c2, "basis": "abstract", "note": "n"}]}
    decision = lambda aid, c1, c2, contribution: {"article_id": aid, "decision": "retain", "justification": "j", "lock_ids": [],
        "contribution": contribution, "relevance": profile(c1, c2)}
    t.execute("record_candidate_decisions", {"decisions": [decision(ids[0], "met", "not_met", "direct"),
        decision(ids[1], "partial", "unknown", "related")], "finalist_ids": ids[:2], "covered_families": [], "remaining_axes": []}, float("inf"))
    assert state["evaluation"]["recommended_article_ids"] == [ids[1], ids[0]]   # tier 2 before tier 3
    result = t.execute("record_candidate_decisions", {"decisions": [decision(ids[2], "met", "met", "direct")],
        "finalist_ids": None, "covered_families": [], "remaining_axes": []}, float("inf"))
    assert state["evaluation"]["recommended_article_ids"][0] == ids[2] and ids[0] not in state["evaluation"]["recommended_article_ids"]
    change = next(c for c in result["ranking_changes"] if c.get("kind") == "criteria_dominance")
    assert change["outgoing_id"] == ids[0] and change["information_used"]
