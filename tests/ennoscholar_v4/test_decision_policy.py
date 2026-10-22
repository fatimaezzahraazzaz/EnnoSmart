"""Decision transitions exercised through real tools and agent, with no external IO.

Synthetic model decisions are scripted scientific judgments, not a Python relevance
ranker. The archive replays below use mode=ro and modify only deserialized objects.
"""
import copy
import hashlib
import json
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup

from agents.EnnoScholarV4 import research
from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.scientific_reading import reading_tasks, next_task, prepare_reading, completed
from agents.EnnoScholarV4.toolkit import ScholarTools, SearchArgs
from agents.EnnoScholarV4.tools.tables import html_table
from tests.ennoscholar_v4.test_context_memory import rich_corpus
from tests.ennoscholar_v4.test_finalization import Stream, call
from tests.ennoscholar_v4.test_tools import tool_inputs
from tests.ennoscholar_v4.replay_2fd068ff import analysis


def corpus(n=5, deep=True, study_scopes=None):
    t, ids = rich_corpus(n, study_scopes=study_scopes)
    t.state["turn_goal"] = {"deliverable": "literature_search", "search_needed": True,
        "reading_depth": "deep" if deep else "core", "comparison_requested": True}
    t.state["research_progress"] = {"id": "research_test", "requested_count": min(n, 5), "reviews": []}
    for aid in ids:
        t.registry[aid].assessment = {"decision": "retain", "justification": "Méthode candidate à confronter au besoin."}
    t.save()
    reading_tasks(t.state, t.registry)
    # These unit scenarios start after the cheap discovery checkpoint. The new
    # checkpoint itself is exercised through the agent and in test_discovery_ranking.
    t.state["research_progress"]["discovery_review"] = {"signature": research.discovery_signature(t.state),
        "ready_for_reading": True}
    return t, ids


def comparison(t, ids):
    return t.execute("compare_articles", {"article_ids": ids, "research_context": "Volumetric meshing for mechanics",
        "relations": [], "conclusions": [], "claim_offsets": [],
        "assessment": "Méthodes alternatives ; protocoles distincts : aucune supériorité numérique établie. "
            "Confronter méthodes, validation, résultats, limites et portée pour la mécanique."}, float("inf"))


def test_five_abstracts_never_establish_deep_completeness():
    t, ids = corpus()
    # Real proofs are re-created from each abstract, never re-labelled fulltext proofs.
    for aid in ids:
        t.registry[aid].analysis = {}
        t.registry[aid].fulltext = {}
        result = t.execute("analyze_article", {"article_ids": [aid], "action": "read", "research_context": "x",
            "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": None}, float("inf"))
        t.delivered([w["window_id"] for w in t.reading_windows if w["status"] == "queued"], inputs=tool_inputs(result))
        packet = {"passages": [{**p, "ref": p["evidence_id"]} for p in result["reading"][0]["passages"]]}
        t.execute("analyze_article", {"article_ids": [aid], "action": "save", "research_context": "x",
            "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis(packet)}, float("inf"))
    t.save()
    observed = research.status(t.state)
    assert observed["target_reached"] and len(observed["abstract_only_ids"]) == 5
    assert not observed["complete"] and not observed["analysis_sufficient"]
    with pytest.raises(ValueError, match="Couverture insuffisante"):
        research.save_review(t.state, research.ResearchReviewArgs(sufficient=True, analysis_sufficient=True,
            scientific_selection_justified=True, reason="Le compteur seul ne prouve rien."))


def test_rank_preserves_unread_late_challenger_and_never_rewards_pdf_access():
    t, ids = corpus(6)
    preferred = ids[-1]
    t.registry[preferred].analysis = {}
    t.registry[preferred].fulltext = {}
    t.state["evaluation"]["recommended_article_ids"] = [preferred, *ids[:-1]]
    t.save()
    result = research.canonical_selection(t.state)
    assert result["ranked_ids"][0] == preferred and result["pending_ids"] == [preferred]
    assert ids[-2] not in result["selected_ids"]  # Not silently back-filled with an easy PDF.
    assert not research.status(t.state)["target_reached"]


def test_consultant_pinned_work_cannot_be_cut_off_by_a_model_top_five():
    t, ids = corpus(6)
    t.registry[ids[-1]].selection_override = True
    result = t.execute("record_candidate_decisions", {"decisions": [{"article_id": aid, "decision": "retain",
        "justification": "Scientific order proposed by model", "lock_ids": []} for aid in ids],
        "finalist_ids": ids[:5], "covered_families": [], "remaining_axes": []}, float("inf"))
    assert result["saved"] and ids[-1] in research.canonical_selection(t.state)["selected_ids"]


def test_review_gap_schedules_targeted_read_and_survives_resume_without_repeating_passages():
    t, ids = corpus(1)
    aid = ids[0]
    old = set(t.state["scientific_reading"]["tasks"][0]["examined_evidence_ids"])
    research.save_review(t.state, research.ResearchReviewArgs(gaps=["Méthodes connues, protocole et ablations incomplets"],
        actions=[research.ResearchAction(kind="read", article_ids=[aid], query="validation results ablation table",
            reason="Passages accessibles pas encore analysés.")], reason="Approfondir plutôt que compter les fiches."))
    resumed = json.loads(json.dumps(t.state))
    assert research.transition(resumed)["stage"] == "action"
    tk = ScholarTools(resumed, Mock(), t.config, lambda: None, lambda *a, **kw: None)
    task = resumed["scientific_reading"]["tasks"][0]
    task["research_query"] = research.pending_actions(resumed)[0]["query"]
    task["research_action_key"] = research.pending_actions(resumed)[0]["key"]
    focus = prepare_reading(resumed, tk, task, lambda n, a: tk.execute(n, a, float("inf")))
    assert focus and not set(focus["evidence_ids"]) & old
    assert not task["core_covered"]  # A field being populated is not a depth review.


def test_semantic_unread_dimension_becomes_an_action_even_without_a_redundant_action_array():
    t, ids = corpus(1)
    research.save_review(t.state, research.ResearchReviewArgs(reason="Résultats principaux encore à lire.",
        gaps=["Ablations et protocole"], article_reviews=[research.ArticleDepthReview(article_id=ids[0],
            article_type="experimental", sufficient=False, dimensions=[research.DimensionReview(dimension="results",
                status="unread", reason="Read experimental results ablation protocol Table 2.")])]))
    action = research.transition(t.state)["action"]
    assert action["kind"] == "read" and action["article_ids"] == ids and "ablation" in action["query"]
    assert not research.status(t.state)["partial_stop"]


def test_comparison_is_durable_even_for_incomparable_protocols_and_invalidates_on_card_change():
    t, ids = corpus(2)
    assert not research.comparison_current(t.state)
    assert comparison(t, ids)["saved"]
    assert research.comparison_current(t.state)
    t.state["articles"][ids[0]]["analysis"]["analysis_limit"] = "New experimental information"
    assert not research.comparison_current(t.state)


def test_after_reading_ranking_is_reviewed_before_coverage():
    t, ids = corpus(2)
    comparison(t, ids)
    assert research.transition(t.state)["stage"] == "rerank"
    t.state["research_progress"]["ranking_review"] = {"card_signature": research.card_signature(t.state),
        "corpus_ids": sorted(t.state["articles"]), "reviewed_ids": ids}
    assert research.transition(t.state)["stage"] == "review"


def test_a_fitted_ranking_window_cannot_hide_unreviewed_challengers():
    t, ids = corpus(7)
    comparison(t, ids)
    research.record_ranking(t.state, [{"article_id": aid, "decision": "retain"} for aid in ids[:2]])
    assert not research.ranking_current(t.state)
    transition = research.transition(t.state)
    assert transition["stage"] == "rerank" and set(ids[2:]) <= set(transition["article_ids"])
    research.record_ranking(t.state, [{"article_id": aid, "decision": "retain"} for aid in ids[2:]])
    assert research.ranking_current(t.state)


def test_scientific_actions_are_progress_and_resume_with_the_original_global_budget():
    from agents.EnnoScholarV4 import writing_job
    t, ids = corpus(1)
    config = ScholarV4Config(research_job_budget_usd=1.0, research_job_max_segments=4)
    job = writing_job.new_job(config, "m1", "r1", {}, kind="research")
    job["corpus_signature"] = research.progress_signature(t.state)
    job["no_progress"] = 1
    research.save_review(t.state, research.ResearchReviewArgs(reason="Protocole incomplet", gaps=["validation"],
        actions=[research.ResearchAction(kind="read", article_ids=ids, query="validation protocol", reason="Passages accessibles")]))
    research.job_evaluate(job, t.state, "r1", "partial", {"ledger": [{"status": "settled", "cost_usd": .2}]})
    assert job["status"] == "waiting" and job["no_progress"] == 0
    job["current_run_id"] = "r2"
    research.job_evaluate(job, t.state, "r2", "partial", {"ledger": [{"status": "settled", "cost_usd": .1}]})
    assert job["budget_usd"] == 1.0 and job["spent"]["confirmed_usd"] == pytest.approx(.3)
    assert len(job["segments"]) == 2 and research.pending_actions(t.state)


def test_depth_judgment_cannot_borrow_another_articles_proof():
    t, ids = corpus(2)
    eid = t.state["articles"][ids[1]]["analysis"]["evidence"][0]["evidence_id"]
    with pytest.raises(ValueError, match="attribuée"):
        research.save_review(t.state, research.ResearchReviewArgs(reason="test", article_reviews=[research.ArticleDepthReview(
            article_id=ids[0], article_type="experimental", sufficient=False,
            dimensions=[research.DimensionReview(dimension="results", status="verified", evidence_ids=[eid], reason="Wrong work")])]))


def test_examined_passage_without_a_claim_is_traceable_but_does_not_prove_article_wide_absence():
    t, ids = corpus(1)
    aid = ids[0]
    before = copy.deepcopy(t.registry[aid].analysis)
    result = t.execute("analyze_article", {"article_ids": ids, "action": "read", "research_context": "ablation",
        "passage_query": "", "passage_offset": 40, "passages_per_article": 1, "analysis": None, "revisit": True}, float("inf"))
    t.delivered([w["window_id"] for w in t.reading_windows if w["status"] == "queued"], inputs=tool_inputs(result))
    eid = result["reading"][0]["passages"][0]["evidence_id"]
    assert eid not in {e["evidence_id"] for e in before["evidence"]}
    local = research.ArticleDepthReview(article_id=aid, article_type="experimental", sufficient=False,
        dimensions=[research.DimensionReview(dimension="comparisons", status="absent", scope="examined_passages",
            evidence_ids=[eid], reason="Ablation non trouvée dans ce passage ; ce constat n'est pas étendu à l'article.")])
    research.save_review(t.state, research.ResearchReviewArgs(reason="Absence locale conservée sans fabriquer un claim.", article_reviews=[local]))
    assert t.registry[aid].analysis == before and not research.status(t.state)["analysis_sufficient"]
    local.dimensions[0].scope = "available_document"
    with pytest.raises(ValueError, match="Absence dans le document"):
        research.save_review(t.state, research.ResearchReviewArgs(reason="Interdit : résultat absent de tout l'article.", article_reviews=[local]))


def test_deep_success_requires_current_semantic_dimensions_ranking_and_comparison():
    t, ids = corpus(2)
    comparison(t, ids)
    t.state["research_progress"]["ranking_review"] = {"card_signature": research.card_signature(t.state),
        "corpus_ids": sorted(t.state["articles"]), "reviewed_ids": ids}
    reviews = []
    for aid in ids:
        card = t.state["articles"][aid]["analysis"]
        fields = {"methods": "method", "experimental_protocol": "experimental_setup", "results": "results", "limits": "limitations"}
        dimensions = [research.DimensionReview(dimension=d, status="verified", evidence_ids=card[f][0]["evidence_ids"],
            reason="Synthetic fixture: exact saved claim examined for this dimension.") for d, f in fields.items()]
        dimensions.append(research.DimensionReview(dimension="comparisons", status="not_applicable",
            reason="Fixture has no ablation study; not inferred from the benchmark figures."))
        reviews.append(research.ArticleDepthReview(article_id=aid, article_type="experimental", sufficient=True, dimensions=dimensions))
    research.save_review(t.state, research.ResearchReviewArgs(sufficient=True, analysis_sufficient=True,
        scientific_selection_justified=True, article_reviews=reviews, reason="Bounded synthetic scientific review completed."))
    assert research.status(t.state)["complete"] and research.transition(t.state)["stage"] == "complete"
    t.state["articles"][ids[0]]["analysis"]["results"].append(copy.deepcopy(t.state["articles"][ids[0]]["analysis"]["results"][0]))
    assert not research.status(t.state)["complete"]  # New science invalidates all three verdicts.


def test_review_actions_are_bounded_even_when_the_model_changes_its_narrative():
    t, ids = corpus(1)
    for i in range(4):
        research.save_review(t.state, research.ResearchReviewArgs(reason=f"Review {i}", gaps=["remaining validation"],
            actions=[research.ResearchAction(kind="search", query="neural tetrahedral validation", reason=f"Reason {i}")]))
        actions = research.pending_actions(t.state)
        for action in actions:
            action["status"] = "done"
    assert len(t.state["research_progress"]["actions"]) == 1
    assert research.status(t.state)["partial_stop"]
    assert research.transition(t.state)["stage"] == "partial"


def test_all_candidates_remain_eligible_in_diverse_progressive_batches():
    t, ids = corpus(1)
    added, _ = t.store.insert([Article(title=f"Candidate {i}", abstract="Ambiguous, requires expert triage.",
        url=f"https://example.org/new/{i}", scientific_families=[f"family-{i % 3}"]) for i in range(55)])
    seen = []
    for _ in range(8):
        batch = research.triage_pool(t.state, 9)
        if not batch:
            break
        seen.extend(batch)
        for aid in batch:
            # A completed abstract evaluation, not just an abbreviated title exposure.
            t.state.setdefault("full_abstract_receipts", {})[aid] = hashlib.sha256(
                t.state["articles"][aid]["abstract"].encode()).hexdigest()
            t.state["articles"][aid]["assessment"] = {"decision": "pending", "justification": "Revisitable"}
    assert not research.triage_pool(t.state)
    assert set(seen) == set(added) and len(seen) == 55
    assert research.triage_pool(t.state, revisit=[added[-1]]) == [added[-1]]


def test_partially_stored_provider_page_resumes_locally_including_after_restart():
    providers = Mock()
    rows = [Article(title=f"Distinct method {i}", abstract="Scientific abstract.",
        doi=f"10.1234/resume-{i}", sources=["openalex"]) for i in range(16)]
    providers.search.return_value = rows, []
    providers.last_provider_stats = {"openalex": {"returned": 16}}
    config = ScholarV4Config(api_key="mock", max_search_results=20)
    t = ScholarTools({}, providers, config, lambda: None, lambda *a, **k: None)
    args = SearchArgs(query="learned mesh generation", sources=["openalex"], limit_per_source=16, offset=0, family="mesh")
    assert t.search(args, float("inf"))["capacity_dropped"] == 6
    resumed = ScholarTools(json.loads(json.dumps(t.state)), providers, config, lambda: None, lambda *a, **k: None)
    assert resumed.search(args, float("inf"))["collected"] == 6
    assert providers.search.call_count == 1 and len(resumed.registry) == 16
    assert not research.source_plan(resumed.state, args.query, args.sources, 0, 16)
    assert not any(p["rows"] for p in resumed.state["research_pending_pages"])


@pytest.mark.parametrize("markup,status,headers,rows", [
    ('<tr><th rowspan="2">Method</th><th colspan="2">Quality</th></tr><tr><th>Mean</th><th>Minimum</th></tr>'
     '<tr><td rowspan="2">A</td><td>0.9</td><td>0.3</td></tr><tr><td>0.8</td><td>0.2</td></tr>',
     "verified_cells", ["Method", "Quality / Mean", "Quality / Minimum"], [["A", "0.9", "0.3"], ["A", "0.8", "0.2"]]),
    ('<tr><th>Method</th><th>A</th><th>B</th></tr><tr><td>X</td><td colspan="2">0.5</td></tr>',
     "ambiguous", ["Method", "A", "B"], [["X", "0.5", "0.5"]]),
])
def test_literal_spans_and_multilevel_headers_preserve_associations_or_report_ambiguity(markup,status,headers,rows):
    result = html_table(BeautifulSoup("<table>"+markup+"</table>", "html.parser").table)
    assert (result["structure_status"], result["headers"], result["rows"]) == (status, headers, rows)
    assert result["cell_origins"]


class TwoWaveModel:
    """Scripted, content-varied judgments: direct tetrahedral mechanics beats related
    frame-field/2D/geometry works. No title substring determines the final selection."""
    def __init__(self, initial, preferred):
        self.initial, self.preferred, self.calls, self.review_count = initial, preferred, [], 0

    def with_options(self, **kw): return self
    @property
    def responses(self): return self

    def create(self, **kw):
        self.calls.append(kw)
        name = (kw.get("tool_choice") or {}).get("name") if isinstance(kw.get("tool_choice"), dict) else None
        memory = json.loads(next(i["content"].split(" : ",1)[1] for i in kw["input"]
            if i.get("role") == "developer" and i.get("content", "").startswith("Mémoire")))
        n = len(self.calls)
        if name == "record_candidate_decisions":
            ids = [r["id"] for r in memory["selection_candidates"]]
            known = set(ids) | set(memory.get("research_objective_progress", {}).get("verified_analyzed_ids", []))
            ranked = [aid for aid in [*self.preferred, *self.initial] if aid in known][:5]
            incumbents = memory.get("selection_comparison", {}).get("incumbent_ids", [])
            incoming = [aid for aid in ranked if aid not in incumbents]
            outgoing = [aid for aid in incumbents if aid not in ranked]
            replacements = [{"incoming_id": inc, "outgoing_id": old,
                "justification": "The new method directly produces the requested object and validates it for the consultant's application.",
                "information_used": ["Received incumbent and challenger scientific dossiers and abstracts."],
                "uncertainties": ["Experimental protocols differ; no numerical superiority inferred."]}
                for inc, old in zip(incoming, outgoing)]
            out = call(name, {"decisions": [{"article_id": aid, "decision": "retain", "lock_ids": [],
                "contribution": "direct" if aid in self.preferred else "related",
                "justification": "Reconstruction volumique et validation mécanique directement pertinentes." if aid in self.preferred
                    else "Travail connexe ; sa portée doit être distinguée d'une validation mécanique directe."} for aid in ids],
                "finalist_ids": ranked, "covered_families": ["learning-based volume reconstruction"], "remaining_axes": [],
                "replacements": replacements}, n)
        elif name == "review_discovery":
            out = call(name, {"ready_for_reading": True, "families": [{"family": "learning-based volume reconstruction",
                "status": "searched", "reason": "Provisional candidates compared in received dossiers."}],
                "reason": "Useful candidates identified; detailed validation now requires reading."}, n)
        elif name == "analyze_article":
            packet = memory["automatic_scientific_reading"]
            out = call(name, {"article_ids": [packet["article_id"]], "action": "save", "research_context": "mechanics",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis(packet),
                "reviewed_evidence_ids": [p["ref"] for p in packet["passages"]],
                "reviewed_fields": ["method", "results", "limitations"]}, n)
        elif name == "compare_articles":
            out = call(name, {"article_ids": memory["argumentation_focus"]["article_ids"], "relations": [],
                "conclusions": [], "claim_offsets": [], "research_context": "3D mechanics",
                "assessment": "Méthodes et validations comparées. Les candidats de seconde vague répondent plus directement "
                    "à la mécanique volumique ; protocoles distincts, aucune supériorité numérique inférée."}, n)
        elif name == "review_research_coverage":
            self.review_count += 1
            out = call(name, {"reason": "Rechercher les concurrents directs avant de conclure." if self.review_count == 1
                else "Classement réévalué ; preuves expérimentales encore partielles, arrêt explicite du scénario borné.",
                "sufficient": False, "gaps": ["Validation mécanique directe"],
                "actions": [{"kind": "search", "query": "differentiable tetrahedral solid mechanics validation",
                    "sources": ["openalex"], "reason": "Concurrents directs insuffisamment représentés."}] if self.review_count == 1 else [],
                "stop_reason": None if self.review_count == 1 else "Couverture expérimentale partielle, accès documentaire limité."}, n)
        elif name == "finalize_response":
            ids = memory["research_objective_progress"]["verified_analyzed_ids"]
            out = call(name, {"answer": "Sélection provisoire comparée ; la profondeur expérimentale reste partielle.",
                "article_ids": [], "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": ["Résultats"],
                "analyses": None, "displayed_article_ids": None}, n)
        else:
            raise AssertionError(f"Unexpected paid decision stage: {name}")
        return Stream(NS(output=out, output_text="", status="completed", usage={"input_tokens": 800, "output_tokens": 250}))


def test_real_agent_two_waves_executes_comparison_review_and_late_replacement_without_early_success():
    # Varied scientific scope, not five copies chosen by a title prefix.
    scopes = ["2D adaptive size field", "hexahedral frame-field", "geometry reconstruction", "tetrahedral generator",
        "fundamental parametrization", "differentiable solid-mechanics mesher", "learned volumetric optimization"]
    t, ids = corpus(7, study_scopes=scopes)
    initial, later = ids[:5], ids[5:]
    for aid in ids:
        t.registry[aid].selected = aid in initial
    wave = [t.registry[aid].model_copy(deep=True) for aid in later]
    for article in wave:
        article.analysis = {}
        article.assessment = {}
    state = copy.deepcopy(t.state)
    state["articles"] = {aid: t.registry[aid].durable() for aid in initial}
    state["evaluation"]["recommended_article_ids"] = initial
    state["scientific_reading"]["tasks"] = [task for task in state["scientific_reading"]["tasks"] if task["article_id"] in initial]
    goal = {**state["turn_goal"], "requested_article_count": 5, "lock_ids": [], "focus_lock_ids": [],
        "understanding": "Comparer les meilleurs travaux pertinents pour le maillage volumique en mécanique.",
        "article_ids": None, "article_scope": "open", "references_ambiguous": False, "constraints": []}
    state["messages"] = [{"id": "request-test", "role": "user", "content": "Recherche les 5 meilleurs articles pour le maillage volumique 3D en mécanique par IA."}]
    state["research_progress"]["request_message_id"] = "request-test"
    state["writing_segment"] = {"kind": "research", "goal": goal, "request_message_id": "request-test",
        "progress_id": state["research_progress"]["id"]}
    provider = Mock()
    provider.search.return_value = wave, []
    provider.last_provider_stats = {"openalex": {"returned": 2}}
    provider.verify.side_effect = lambda a, deadline: a
    provider.metadata.return_value = []
    # A valid blueprint from an earlier task must not disable literature comparison.
    from tests.ennoscholar_v4.test_context_memory import seed_argumentation
    from agents.EnnoScholarV4.argumentation import blueprint_status
    prior = ScholarTools(state, provider, ScholarV4Config(api_key="mock"), lambda: None, lambda *a, **k: None)
    seed_argumentation(prior, initial)
    prior.save()
    assert blueprint_status(state, prior.registry)["complete"] and not blueprint_status(state, prior.registry)["stale"]
    model = TwoWaveModel(initial, later)
    events = []
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False, max_tool_rounds=18,
        max_tool_calls=70), client=model, sources=provider).run(state, emit=events.append)
    stages = [kw.get("tool_choice", {}).get("name") for kw in model.calls if isinstance(kw.get("tool_choice"), dict)]
    assert result.get("reason") != "internal_error", {"diagnostic": result.get("diagnostic"), "stages": stages}
    assert model.review_count == 2, (stages, result.get("reason"))
    assert "compare_articles" in stages and stages.count("record_candidate_decisions") >= 2
    assert "analyze_article" in stages and all(state["articles"][aid]["analysis"] for aid in later), json.dumps({
        "stages": stages, "refusals": state.get("analysis_refusals"),
        "events": [(r.get("tool"), r.get("status"), r.get("error")) for r in events if r.get("status") == "failed"],
        "tasks": [(x["article_id"], x["status"], x.get("attempts")) for x in state["scientific_reading"]["tasks"]]}, ensure_ascii=False)
    assert state["evaluation"]["recommended_article_ids"][:2] == later, {
        "stages": stages, "ranking": state["research_progress"].get("ranking_history"),
        "ids": list(state["articles"]), "later": later}
    assert len(state["research_progress"]["ranking_history"]) >= 2
    assert research.status(state)["partial_stop"] and not research.status(state)["complete"]
    assert provider.search.call_count == 1 and len(model.calls) < 30
    assert {kw["model"] for kw in model.calls} == {"gpt-6-luna"}


@pytest.mark.parametrize("run_or_session", ["94599ea8d32040f986ba9c771f045837", "53292dc472204046a0b48eb5b3408d11", "bfc10a57e8f940728fe535037f586146"])
def test_real_archives_do_not_complete_from_a_counter_and_preserve_scientific_artifacts(run_or_session):
    from tests.ennoscholar_v4.test_audit_91590987 import DB
    if not DB.is_file():
        pytest.skip("Local archive unavailable; portable decision scenarios remain mandatory.")
    with sqlite3.connect(DB.resolve().as_uri()+"?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        if run_or_session.startswith("532"):
            row = db.execute("select data from scholar_v4_sessions where id=?", (run_or_session,)).fetchone()
            state = json.loads(row[0]) if row else None
        else:
            row = db.execute("select r.data,s.data from scholar_v4_runs r join scholar_v4_sessions s on s.id=r.session_id where r.id=?", (run_or_session,)).fetchone()
            state = json.loads(row[1]) if row else None
            if row:
                run = json.loads(row[0])
                state["evaluation"]["recommended_article_ids"] = run["recommended_article_ids"]
                state["scientific_reading"] = run["scientific_reading"]
    assert state and state.get("articles"), "Exact archived state must be available"
    before = copy.deepcopy({k: state.get(k) for k in ("articles", "outline", "source_snapshots")})
    observed = research.status(state)
    assert not observed["complete"] and not observed["scientific_selection_justified"]
    assert all(state.get(k) == v for k, v in before.items())
    if run_or_session.startswith("bfc"):
        assert observed["target_reached"] and observed["unexamined_count"] > 50
        assert research.transition(state)["stage"] == "triage"
        dl = next(aid for aid, a in state["articles"].items() if "fcc710" in aid)
        assert not state["articles"][dl]["analysis"]["metrics"]
        assert dl in observed["depth_review"]["missing_article_ids"]
        # Replay the actual under-read DL-Polycube document, without fetching it.
        t = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"), lambda: None, lambda *a, **k: None)
        reading_tasks(state, t.registry)
        task = next(t for t in state["scientific_reading"]["tasks"] if t["article_id"] == dl)
        prior = set(task.get("examined_evidence_ids", []))
        task["research_query"] = "Results experiments metrics quantitative Table 2 scaled Jacobian validation"
        focus = prepare_reading(state, t, task, lambda name, args: t.execute(name, args, float("inf")))
        assert focus and not set(focus["evidence_ids"]) & prior
        assert all(t.evidence[eid]["article_id"] == dl for eid in focus["evidence_ids"])
        assert not task["core_covered"]  # Six old passages + an empty metric field never prove sufficient depth.
