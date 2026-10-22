"""Discovery/ranking regression: real archived decisions, fake model/providers, no IO.

The optional archive is opened mode=ro/query_only. Every mutation below is to a
deserialized copy. Portable scenarios cover the same contracts without that DB.
"""
import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4 import research
from agents.EnnoScholarV4.context import model_context, SelectionContextError
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.toolkit import ScholarTools


def tools(count=8, domain="mechanics"):
    state = {"messages": [{"id": "user-test", "role": "user", "content": f"Find five scientific papers in {domain}."}],
        "articles": {}, "turn_goal": {"deliverable": "literature_search", "search_needed": True,
            "understanding": domain, "requested_article_count": 5, "lock_ids": [], "reading_depth": "core"},
        "research_state": {"topic": domain, "search_axes": [f"direct methods in {domain}", f"assisted methods in {domain}"]},
        "research_progress": {"id": "research_test", "requested_count": 5, "reviews": []}}
    provider = Mock()
    provider.metadata.side_effect = AssertionError("No external lookup is needed")
    provider.search.side_effect = AssertionError("No provider call is needed")
    tk = ScholarTools(state, provider, ScholarV4Config(api_key="mock", web_search_enabled=False))
    ids, _ = tk.store.insert([Article(title=f"Contribution {i} in {domain}", abstract=(
        f"The study solves a problem in {domain}. Its method produces a specified output. "
        "Experiments report a conditional improvement, with an explicitly limited validation domain. ")*(i+1),
        doi=f"10.1234/rank{i}", authors=["Researcher"], year=2024, sources=["openalex"]) for i in range(count)])
    return tk, ids


def deliver(tk, ids, budget=36000, evaluation_ids=None):
    tk.save()
    tk.state["selection_focus"] = {"candidate_ids": research.selection_ids(tk.state, ids), "review": True,
        "evaluation_ids": list(ids) if evaluation_ids is None else evaluation_ids}
    inputs = model_context(tk.state, tk, budget)
    memory = json.loads(inputs[0]["content"].split(" : ", 1)[1])
    tk.delivered([w["window_id"] for w in tk.store.windows if w["status"] == "queued"], inputs=inputs)
    return memory


def decide(tk, ids, finalists, replacements=()):
    return tk.execute("record_candidate_decisions", {"decisions": [{"article_id": aid, "decision": "retain",
        "justification": "The documented method addresses this scientific need, within its stated conditions.",
        "contribution": "direct"} for aid in ids], "finalist_ids": finalists,
        "covered_families": [], "remaining_axes": [], "replacements": list(replacements)}, float("inf"))


def replacement(inc, out):
    return {"incoming_id": inc, "outgoing_id": out,
        "justification": "The entrant produces the requested object; the outgoing method only uses it as input.",
        "information_used": ["The received abstracts describe each method's actual output and validation conditions."],
        "uncertainties": ["No common experimental protocol establishes a numerical superiority."]}


def test_omission_does_not_replace_but_received_explicit_comparison_does():
    tk, ids = tools()
    deliver(tk, ids)
    decide(tk, ids, ids[:5])
    first = tk.state["evaluation"]["recommended_article_ids"][:]
    deliver(tk, ids[5:])
    result = decide(tk, ids[5:], ids[5:])
    assert tk.state["evaluation"]["recommended_article_ids"] == first
    assert all(r["reason"] == "explicit_comparison_required" for r in result["ranking_ignored"])
    result = decide(tk, [ids[5]], [ids[5], *ids[:4]], [replacement(ids[5], ids[4])])
    assert ids[5] in tk.state["evaluation"]["recommended_article_ids"]
    assert ids[4] not in tk.state["evaluation"]["recommended_article_ids"]
    event = result["ranking_changes"][0]
    assert set(event["dependencies"]) == {ids[4], ids[5]}
    assert event["information_used"] and event["uncertainties"] and event["provenance"]
    assert research.triage_pool(tk.state) == []


def test_title_only_or_old_dossier_cannot_authorize_replacement_and_pins_prevail():
    tk, ids = tools()
    deliver(tk, ids)
    decide(tk, ids, ids[:5])
    tk.state["selection_dossier_receipts"] = {}
    result = decide(tk, ids[5:], [*ids[5:], *ids[:2]], [replacement(ids[5], ids[4])])
    assert ids[4] in tk.state["evaluation"]["recommended_article_ids"]
    assert result["ranking_ignored"][0]["reason"] == "comparison_dossier_not_received"
    tk.registry[ids[4]].selection_override = True
    deliver(tk, ids[5:])
    result = decide(tk, [ids[5]], [ids[5]], [replacement(ids[5], ids[4])])
    assert result["ranking_ignored"][0]["reason"] == "consultant_pinned"
    assert tk.state["evaluation"]["recommended_article_ids"][0] == ids[4]


def test_balanced_context_keeps_incumbents_and_resumable_full_challenger_abstracts():
    tk, ids = tools(30)
    deliver(tk, ids[:5])
    decide(tk, ids[:5], ids[:5])
    memory = deliver(tk, ids[5:], budget=19000)
    packet = memory["selection_comparison"]
    assert [p["id"] for p in packet["incumbents"]] == ids[:5]
    assert packet["challengers"]
    assert all(set(p) == set(packet["incumbents"][0]) for p in packet["challengers"])
    shown = {p["id"]: p for p in memory["selection_candidates"]}
    for row in packet["challengers"]:
        assert shown[row["id"]]["abstract"] == tk.registry[row["id"]].abstract
        assert not shown[row["id"]].get("abstract_truncated")
    assert set(ids[:5]) <= {p["id"] for p in packet["incumbents"]}
    assert len(shown) < len(ids)
    with pytest.raises(SelectionContextError):
        deliver(tk, ids[5:], budget=1500)


@pytest.mark.parametrize("verdict", ["retain", "pending", "reject"])
def test_partial_notice_is_revisited_locally_and_ack_survives_interrupted_decision(verdict):
    tk, ids = tools(1)
    aid = ids[0]
    tk.registry[aid].assessment = {"decision": verdict, "justification": "Only a truncated abstract was received."}
    tk.save()
    assert research.triage_pool(tk.state) == [aid]
    deliver(tk, [aid])
    # A completed model response followed by an interruption before tool application
    # does not count as a completed scientific evaluation.
    resumed = json.loads(json.dumps(tk.state))
    assert research.triage_pool(resumed) == [aid]
    decide(tk, [aid], [aid])
    assert not research.triage_pool(tk.state)
    tk.sources.metadata.assert_not_called()
    tk.sources.search.assert_not_called()


def test_one_changed_article_preserves_unaffected_ranking_and_comparisons():
    tk, ids = tools()
    deliver(tk, ids)
    decide(tk, ids, ids[:5])
    assert research.ranking_current(tk.state)
    before = copy.deepcopy(tk.state["research_progress"]["ranking_records"])
    tk.registry[ids[0]].fulltext = {"text": "Downloaded but not yet read; no new scientific judgment."}
    tk.save()
    assert research.ranking_current(tk.state)
    tk.registry[ids[0]].analysis = {"analysis_limit": "New verified information is now available."}
    tk.save()
    assert research.stale_ranking_candidates(tk.state) == [ids[0]]
    print(json.dumps({"scenario": "incremental_ranking", "affected": 1, "unaffected_reused": len(ids)-1}))
    assert all(before[aid] == tk.state["research_progress"]["ranking_records"][aid] for aid in ids[1:])
    assert research.triage_pool(tk.state) == [ids[0]]
    deliver(tk, [ids[0]])
    decide(tk, [ids[0]], ids[:5])
    assert research.ranking_current(tk.state)


def test_discovery_gap_runs_before_reading_with_durable_deduplicated_actions():
    tk, ids = tools(5, domain="catalysis")
    deliver(tk, ids)
    decide(tk, ids, ids)
    assert research.transition(tk.state)["stage"] == "discovery_review"
    args = {"ready_for_reading": False,
        "families": [{"family": "Direct synthesis of the requested catalyst", "status": "missing",
            "reason": "Available candidates describe characterization, not synthesis."}],
        "gaps": ["Direct synthesis routes"], "reason": "One targeted search is needed before expensive reading.",
        "actions": [{"kind": "search", "query": "learned catalyst synthesis experimental selectivity",
            "sources": ["openalex"], "family": "direct synthesis", "reason": "Resolve this specific missing contribution."}]}
    tk.execute("review_discovery", args, float("inf"))
    resumed = json.loads(json.dumps(tk.state))
    assert research.transition(resumed)["stage"] == "action"
    tk.execute("review_discovery", args, float("inf"))
    assert len(research.pending_actions(tk.state)) == 1
    tk.state["research_progress"]["actions"][0]["status"] = "done"
    # Identical work cannot be admitted again on an unchanged dossier.
    tk.execute("review_discovery", args, float("inf"))
    assert not research.pending_actions(tk.state)
    assert research.transition(tk.state)["stage"] == "partial"


def test_ready_discovery_review_authorizes_reading_but_not_final_completeness():
    tk, ids = tools(5)
    deliver(tk, ids)
    decide(tk, ids, ids)
    tk.execute("review_discovery", {"ready_for_reading": True,
        "families": [{"family": "Direct methods", "status": "searched", "reason": "Notices examined", "article_ids": ids}],
        "reason": "Provisional candidates compared; expensive reading may now start."}, float("inf"))
    assert research.transition(tk.state)["stage"] == "read"
    assert not research.status(tk.state)["complete"]
    tk.state["articles"][ids[0]]["analysis"] = {"analysis_limit": "Updated reading"}
    assert research.discovery_current(tk.state)


def test_bibliographic_pointer_is_not_an_article_or_a_proof():
    tk, ids = tools(1)
    text = "Introduction\nA contribution.\nReferences\n[23] Zhang et al. MeshingNet3D: Efficient generation of adapted tetrahedral meshes for computational mechanics. 2021. doi: 10.1016/j.advengsoft.2021.103021 .\n"
    tk.registry[ids[0]].fulltext = {"text": text}
    tk.save()
    leads = research.collect_reference_leads(tk.state)
    lead = next(iter(leads.values()))
    assert lead["identifier"] == "10.1016/j.advengsoft.2021.103021"
    assert not lead["identity_verified"] and lead["status"] == "unverified"
    assert text[lead["start"]:lead["end"]].rstrip(".,;)]") == lead["identifier"]
    assert len(tk.registry) == 1 and not tk.evidence
    assert research.collect_reference_leads(tk.state) == leads


def test_partial_page_is_drained_locally_with_original_or_subset_source_and_no_requery():
    tk, ids = tools(0)
    rows = [Article(title=f"Stored result {i}", abstract="Scientific abstract", doi=f"10.1234/page{i}",
        sources=["openalex" if i % 2 else "arxiv"]).durable() for i in range(6)]
    key = ["targeted family", ["arxiv", "openalex"], 0, 12]
    tk.state["research_pending_pages"] = [{"key": key, "rows": rows}]
    tk.state["recent_searches"] = [{"key": key, "query": key[0], "limit_dropped": 6,
        "providers": {"arxiv": {"returned": 12}, "openalex": {"returned": 12}}}]
    args = {"query": key[0], "sources": ["arxiv"], "offset": 0, "limit_per_source": 12, "family": "target"}
    result = tk.execute("search_scientific", args, float("inf"))
    assert result["reused_local"] and result["new_candidates"] == 3
    assert len(tk.state["research_pending_pages"][0]["rows"]) == 3
    result = tk.execute("search_scientific", {**args, "sources": ["openalex"]}, float("inf"))
    assert result["reused_local"] and result["new_candidates"] == 3
    assert research.source_plan(tk.state, key[0], ["arxiv", "openalex"], 0, 12) == []
    tk.sources.search.assert_not_called()


def test_distinct_work_titles_and_years_do_not_merge_and_provider_order_is_stable():
    from agents.EnnoScholarV4.store import interleave_by_provider
    tk, ids = tools(0)
    rows = [Article(title="MeshingNet", year=2020, doi="10.1234/mesh2020", sources=["arxiv"]),
        Article(title="MeshingNet3D", year=2021, doi="10.1234/mesh2021", sources=["openalex"])]
    ids, _ = tk.store.insert(rows)
    assert len(ids) == 2 and len(set(research.version_groups(tk.state).values())) == 2
    assert [a.title for a in interleave_by_provider(rows)] == [a.title for a in interleave_by_provider(rows[::-1])]


def test_changed_comparison_dependency_invalidates_only_affected_decisions():
    tk, ids = tools()
    deliver(tk, ids)
    decide(tk, ids, ids[:5])
    deliver(tk, ids[5:])
    decide(tk, [ids[5]], [ids[5], *ids[:4]], [replacement(ids[5], ids[4])])
    tk.registry[ids[4]].abstract += " A new condition resolves the earlier uncertainty."
    tk.save()
    assert set(research.stale_ranking_candidates(tk.state)) == {ids[4], ids[5]}
    deliver(tk, [ids[6]])
    tk.registry[ids[6]].abstract += " New information after context delivery."
    tk.save()
    result = decide(tk, [ids[6]], [ids[6], *ids[:4]], [replacement(ids[6], ids[5])])
    assert ids[5] in result["evaluation"]["recommended_article_ids"]
    assert not result["ranking_changes"]


def test_no_finalist_list_cannot_bypass_explicit_withdrawal_contract():
    tk, ids = tools()
    deliver(tk, ids)
    decide(tk, ids, ids[:5])
    old = copy.deepcopy(tk.registry[ids[0]].assessment)
    result = tk.execute("record_candidate_decisions", {"decisions": [{"article_id": ids[0], "decision": "reject",
        "justification": "A rejection is a withdrawal and also needs the explicit comparison record."}],
        "finalist_ids": None, "covered_families": [], "remaining_axes": []}, float("inf"))
    assert tk.registry[ids[0]].assessment == old
    assert result["ignored"][0]["reason"] == "explicit_withdrawal_required"


def test_discovery_network_allowance_is_durable_and_local_actions_come_first():
    tk, ids = tools(1)
    actions = [research.ResearchAction(kind="search", query=f"specific scientific family {i}", reason="Distinct gap") for i in range(5)]
    for action in actions:
        research.enqueue_actions(tk.state, [action], maximum_network=3)
        tk.state = json.loads(json.dumps(tk.state))
        for row in research.pending_actions(tk.state):
            row["status"] = "done"
    assert len(tk.state["research_progress"]["actions"]) == 3
    assert not research.enqueue_actions(tk.state, [actions[-1]], maximum_network=3)
    local = research.ResearchAction(kind="inspect", article_ids=ids, reason="Use an already stored notice")
    assert research.enqueue_actions(tk.state, [local], maximum_network=3)[0]["local"]


def test_effective_arxiv_parameters_are_traced_without_credentials(monkeypatch):
    from agents.EnnoScholarV4.sources import V4ScientificSearch
    from agents.EnnoScholarV2.tools.scientific_search import ScientificSearch
    from agents.EnnoScholarV2.config import ScholarConfig
    client = V4ScientificSearch(ScholarConfig())
    observed = []
    monkeypatch.setattr(ScientificSearch, "_get", lambda *a, **kw: observed.append(kw) or object())
    client._paging.requests = []
    client.search_offset = 12
    client._get_paced("https://export.arxiv.org/api/query", params={
        "search_query": "all:(deep learning volumetric scientific method)", "max_results": 12, "api_key": "must-not-be-recorded"},
        headers={"Authorization": "must-not-be-recorded"})
    trace = client._paging.requests[0]
    assert "ti:deep" in trace["params"]["search_query"] and trace["params"]["start"] == 12
    assert "must-not-be-recorded" not in json.dumps(trace)
    assert trace["params"]["search_query"] == observed[0]["params"]["search_query"]
    assert trace["params"]["max_results"] == 12


def test_verified_versions_share_a_slot_but_keep_their_scientific_sources_separate():
    from agents.EnnoScholarV4.context import scientific_dossier
    from tests.ennoscholar_v4.test_decision_policy import corpus
    tk, ids = corpus(7)
    # An already verified version correspondence, not a fuzzy title merge.
    tk.state["version_correspondences"] = {ids[0]: [{"article_id": ids[1], "relation": "verified_registry_version"}]}
    tk.registry[ids[0]].analysis = {}
    tk.registry[ids[0]].analysis_basis = None
    tk.registry[ids[0]].fulltext = {}
    tk.save()
    dossier = scientific_dossier(tk.state, ids[0])
    assert dossier["provenance"]["science_source_article_id"] == ids[1]
    assert all(c["article_id"] == ids[1] for rows in dossier["saved_claims"].values() for c in rows)
    before = research.candidate_signature(tk.state, ids[0])
    tk.registry[ids[1]].analysis["analysis_limit"] = "New qualification in the verified version."
    tk.save()
    assert research.candidate_signature(tk.state, ids[0]) != before
    tk.state["evaluation"]["recommended_article_ids"] = ids[:5]
    current, events, ignored = research.cumulative_ranking(tk.state, ids[5:], [])
    assert len(current) == 5 and ids[5] in current  # two versions did not consume two work slots
    assert len({research.version_groups(tk.state)[aid] for aid in current}) == 5


def test_known_identity_is_read_locally_and_discovery_cannot_start_long_reads():
    tk, ids = tools(1)
    row = tk.registry[ids[0]]
    for identifier in [ids[0], row.title, row.doi]:
        result = tk.execute("get_article", {"identifiers": [identifier]}, float("inf"))
        assert result["articles"][0]["id"] == ids[0]
    tk.sources.metadata.assert_not_called()
    with pytest.raises(ValueError):
        research.DiscoveryReviewArgs.model_validate({"ready_for_reading": False, "reason": "Discovery only",
            "families": [{"family": "Relevant methods", "status": "searched", "reason": "Initial notices"}],
            "actions": [{"kind": "read", "article_ids": ids, "query": "results", "reason": "Too early"}]})


def test_real_agent_catalysis_explores_a_gap_before_any_long_reading():
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from tests.ennoscholar_v4.test_finalization import Client, call
    tk, ids = tools(5, domain="catalysis")
    state = copy.deepcopy(tk.state)
    state["articles"] = {aid: state["articles"][aid] for aid in ids[:3]}
    state["evaluation"] = {"recommended_article_ids": ids[:3]}
    state["research_progress"].pop("discovery_review", None)
    state["research_state"] = {"topic": "AI for selective catalyst synthesis", "search_axes": ["synthesis routes", "selectivity validation"]}
    state["messages"] = [{"id": "user-test", "role": "user", "content": "Research five papers on AI for selective catalyst synthesis."}]
    goal = {**state["turn_goal"], "requested_article_count": 5, "lock_ids": [], "focus_lock_ids": [],
        "understanding": "Find methods for catalyst synthesis and selectivity validation.",
        "article_ids": None, "article_scope": "open", "references_ambiguous": False, "constraints": []}
    state["writing_segment"] = {"kind": "research", "goal": goal, "request_message_id": "user-test"}
    waves = [tk.registry[aid].model_copy(deep=True) for aid in ids[3:]]
    for row in waves:
        row.analysis, row.assessment = {}, {}
    provider = Mock()
    provider.search.return_value = waves, []
    provider.last_provider_stats = {"openalex": {"returned": 2}}
    provider.verify.side_effect = lambda a, deadline: a
    phases = []
    def respond(n, kw):
        name = kw["tool_choice"].get("name") if isinstance(kw["tool_choice"], dict) else None
        phases.append(name)
        memory = json.loads(kw["input"][0]["content"].split(" : ", 1)[1])
        if name == "record_candidate_decisions":
            shown = [p["id"] for p in memory["selection_candidates"]]
            return call(name, {"decisions": [{"article_id": aid, "decision": "retain", "contribution": "direct",
                "justification": "Candidate scientific method for catalyst synthesis; validation must be checked."} for aid in shown],
                "finalist_ids": list(dict.fromkeys([*memory["selection_comparison"]["incumbent_ids"], *shown]))[:5],
                "covered_families": [], "remaining_axes": ["Selectivity validation"]}, n)
        if name == "review_discovery":
            first = phases.count(name) == 1
            return call(name, {"ready_for_reading": False, "families": [{"family": "Direct catalyst synthesis",
                "status": "missing" if first else "searched", "reason": "Targeted complementary methods need evaluation."}],
                "reason": "Resolve the missing family before long reading.", "actions": [{"kind": "search",
                    "query": "learned catalyst synthesis experimental selectivity", "sources": ["openalex"],
                    "reason": "Missing synthesis family"}] if first else [],
                "stop_reason": None if first else "Offline scenario ends after discovery; no depth success claimed."}, n)
        if name == "finalize_response":
            return call(name, {"answer": "Provisional scientific candidates; validation remains partial.", "article_ids": [],
                "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": ["Validation"],
                "analyses": None, "displayed_article_ids": None}, n)
        raise AssertionError(f"Unexpected paid decision stage {name}")
    model = Client(respond)
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False, max_tool_rounds=12),
        client=model, sources=provider).run(state)
    assert result.get("reason") != "internal_error", result.get("diagnostic")
    assert phases.count("review_discovery") == 2 and provider.search.call_count == 1
    assert "analyze_article" not in phases and "get_fulltext" not in phases
    assert len(model.calls) < 10 and {kw["model"] for kw in model.calls} == {"gpt-6-luna"}
    assert len(state["articles"]) == 5
    print(json.dumps({"scenario": "catalysis_agent", "llm_calls_simulated": len(model.calls),
        "targeted_provider_searches": provider.search.call_count, "long_readings_before_review": 0}))


@pytest.fixture(scope="module")
def archive():
    db = Path(__file__).resolve().parents[2]/"ennoscholar_v4_test.db"
    if not db.is_file():
        pytest.skip("Optional local real-run archive unavailable")
    with sqlite3.connect(db.resolve().as_uri()+"?mode=ro", uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        row = con.execute("select data from scholar_v4_sessions where id=?", ("04382dc6125d438a83510a7a5b36bf23",)).fetchone()
    if row is None:
        pytest.skip("Real-run session unavailable")
    return json.loads(row[0])


def test_real_six_batches_cannot_silently_lose_3dmeshnet_adaptnet_or_neurframe(archive):
    tk, _ = tools(0)
    # Original scientific notices, not current analyses fed back into an earlier decision.
    state = tk.state
    state["articles"] = {aid: {**copy.deepcopy(a), "analysis": {}, "fulltext": {}, "assessment": {},
        "analysis_basis": None} for aid, a in archive["articles"].items()}
    # The archive's exposure ledger also includes abbreviated search notices.
    # Exposure is not a full-abstract receipt or a comparative authorization.
    state["exposed_article_ids"] = list(archive["exposed_article_ids"])
    tk = ScholarTools(state, tk.sources, tk.config)
    ranks = archive["research_progress"]["ranking_history"][:6]
    windows = [w for w in archive["candidate_windows"] if w["purpose"] == "selection_checkpoint"][:6]
    first = ranks[0]["article_ids"][:5]
    reused = 0
    for i, (rank, window) in enumerate(zip(ranks, windows)):
        # Establish the actual first top with complete, balanced dossiers. Later
        # model outputs are the original ungrounded replacement proposals.
        needed = set(research.triage_pool(tk.state, maximum=100)) | set(research.stale_ranking_candidates(tk.state))
        delivered = first if i == 0 else window["article_ids"]
        deliver(tk, delivered, evaluation_ids=[aid for aid in delivered if aid in needed])
        result = tk.execute("record_candidate_decisions", {"decisions": rank["decisions"],
            "finalist_ids": rank["article_ids"], "covered_families": [], "remaining_axes": []}, float("inf"))
        assert set(first) <= set(result["evaluation"]["recommended_article_ids"])
        reused += len(result["reused_decisions"])
    assert "paper_2118d065d84bbc333259" in first
    assert not tk.state["research_progress"].get("ranking_changes")
    assert len(tk.state["research_progress"]["ranking_history"]) == 6
    assert reused > 0 and len(tk.state["candidate_decisions"]) < sum(len(r["decisions"]) for r in ranks)
    tk.sources.search.assert_not_called()
    print(json.dumps({"scenario": "real_six_batches", "original_decisions": sum(len(r["decisions"]) for r in ranks),
        "new_assessments_applied": len(tk.state["candidate_decisions"]), "unchanged_assessments_reused": reused,
        "first_finalists_preserved": len(first), "unjustified_withdrawals": 0, "provider_calls": 0}))


def test_real_pending_abstracts_and_amber_bibliography_remain_actionable(archive):
    state = copy.deepcopy(archive)
    state["research_progress"].pop("candidate_progress", None)
    ids = {"paper_f298a95c1297f15e123a", "paper_4e80e3fbf3a54bbe68ec"}
    assert ids <= set(research.triage_pool(state, maximum=100))
    leads = research.collect_reference_leads(state)
    assert any(lead["identifier"] == "10.1016/j.advengsoft.2021.103021" and not lead["identity_verified"] for lead in leads.values())

