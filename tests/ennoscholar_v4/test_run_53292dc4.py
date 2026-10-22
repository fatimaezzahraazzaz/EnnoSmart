"""Run 53292dc4 (session 53292dc4…, research job job_0aa07bfe…), gpt-6-luna:
« Recherche les 5 articles scientifiques les plus pertinents sur la génération automatique de maillages
volumiques 3D en mécanique … analyse leurs méthodes, résultats et limites ».

Luna extracted and saved the results (TetGAN FID 23,35 / 12,27 and 98,9 % / 98,5 % manifold edges,
DL-Polycube 9 900 meshes in about 10 h + 1 h of training, Non-iterative +22 %, better for 94 % of the test
problems), but the final request carried one « method » claim per work and an empty scientific packet, so the
answer called them unavailable. Two complete analyses (DL-Polycube journal version, NeurFrame) were refused for a
single identifier missing one character in reviewed_evidence_ids, and four DL-Polycube requests re-read passages
already analysed. The counter (recommendations), the reading ledger and the published list (NVMG instead of
DDPM-Polycube) did not use the same works.

Offline: the real session is read in read-only mode and worked in memory; no model, provider or download call.
"""
import copy
import json
import sqlite3

import pytest

from tests.ennoscholar_v4.test_audit_91590987 import DB

SESSION = "53292dc472204046a0b48eb5b3408d11"
JOB = "job_0aa07bfe60db4acab6f2034058478a61"
TETGAN, DLP, DLP_JOURNAL = "paper_f298a95c1297f15e123a", "paper_08fa5de8ce3612b9c130", "paper_942bde322abe27ebd8ff"
NONIT, DDPM, MESHNET = "paper_9fa04cd6c86acf755982", "paper_fab4823c942d9b72e946", "paper_bbbc8737f1e0af3aefd1"
NVMG, NEURFRAME = "paper_4e80e3fbf3a54bbe68ec", "paper_f1fd8a61e0efa7989f98"


@pytest.fixture(autouse=True)
def offline_documents(monkeypatch):
    from agents.EnnoScholarV4.tools import fulltext

    def refuse(*args, **kwargs):
        raise ConnectionError("offline test: no download")
    monkeypatch.setattr(fulltext, "fetch_public", refuse)


_CACHE = {}


def session():
    if "state" not in _CACHE:
        if not DB.is_file():
            pytest.skip("Base locale absente")
        db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
        row = db.execute("select data from scholar_v4_sessions where id = ?", (SESSION,)).fetchone()
        db.close()
        if not row:
            pytest.skip("Session du run 53292dc4 absente")
        _CACHE["state"] = json.loads(row[0])
    return copy.deepcopy(_CACHE["state"])


def before_final():
    """The session as its last segment found it before publishing: the request, no answer."""
    state = session()
    assert state["writing_job"]["id"] == JOB
    state["messages"] = state["messages"][:1]
    return state


def tools(state):
    from unittest.mock import Mock
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.toolkit import ScholarTools
    return ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))


def final_memory(state, budget=24000):
    from agents.EnnoScholarV4.context import model_context
    t = tools(state)
    inputs = model_context(state, t, budget, final=True)
    content = inputs[0]["content"]
    return t, inputs, json.loads(content.split(" : ", 1)[1])


def claims_of(paper):
    return [c for v in paper["analysis"].values() if isinstance(v, list) for c in v]


# --- 1. Saved results reach the final request, with their proofs ----------------------------

def test_the_saved_figures_of_every_selected_work_reach_the_final_request_with_their_proofs():
    state = before_final()
    t, inputs, memory = final_memory(state)
    science = memory["conversation_science"]
    papers = {p["id"]: p for p in science["papers"]}
    expected = {TETGAN: ["23,35", "12,27", "98,9", "98,5"], DLP: ["9 900", "10 heures"], NONIT: ["22 %", "94 %"]}
    for aid, values in expected.items():
        text = json.dumps(claims_of(papers[aid]), ensure_ascii=False)
        assert all(v in text for v in values), (aid, values)
        # Every transmitted claim keeps its exact proof reference (evidence_id, section).
        proofs = {p["evidence_id"]: p for p in papers[aid]["proofs"]}
        for claim in claims_of(papers[aid]):
            assert claim["evidence_ids"] and all(eid in proofs and proofs[eid].get("section") for eid in claim["evidence_ids"])
            assert all(t.evidence[eid]["article_id"] == aid for eid in claim["evidence_ids"])
    report = science["transmission"]
    assert report["complete"] and set(report["works"]) == set(papers)
    for aid, row in report["works"].items():
        assert not row["dimensions_not_sent"] and not row["essential_claims_not_sent"]
        assert {"results", "limitations", "method"} <= set(row["dimensions_sent"])
    # The whole request stays within the unchanged request limit.
    assert len(json.dumps(inputs, ensure_ascii=False)) < t.config.max_context_chars - 6000


def test_before_the_fix_the_same_request_lost_them():
    """The old packing (one bounded packet, method claims first) is what the run sent: the
    figures were absent. Kept as a witness of the loss path."""
    from agents.EnnoScholarV4.argumentation import argument_packet, bounded_argument_packet
    state = before_final()
    t = tools(state)
    packet = bounded_argument_packet(argument_packet(state, t, [NVMG, TETGAN, NONIT, DLP, DDPM, MESHNET]), int(24000*.78))
    text = json.dumps(packet, ensure_ascii=False)
    assert "23,35" not in text and "22 %" not in text


def test_the_card_limit_is_computed_over_every_reading_not_the_last_window():
    state = before_final()
    assert "ni résultats numériques" in " ".join(state["articles"][TETGAN]["analysis"]["analysis_limits"])
    _, _, memory = final_memory(state)
    tetgan = next(p for p in memory["conversation_science"]["papers"] if p["id"] == TETGAN)
    limit = tetgan["analysis"]["analysis_limit"]
    assert "Fiche cumulée" in limit and "résultats" in limit.split("Non trouvé")[0]
    assert "ni résultats numériques" not in json.dumps(tetgan, ensure_ascii=False)
    assert tetgan["window_limits_superseded"] >= 2


# --- 2. A truncated reference is refused exactly and repaired without a new analysis ---------

def real_save(state, prefix):
    row = next(v for k, v in state["task_results"].items() if k.startswith(prefix))
    return row["target"], json.loads(row["content"][0]["arguments"])


@pytest.mark.parametrize("prefix,article,truncated", [
    ("24958aaad8", NEURFRAME, "ev_9e23d7c9c7bfd5e82aa203d"),
    ("d6f4ed93db", DLP_JOURNAL, "ev_6398c93cfb6bfd21b8ed436")])
def test_a_truncated_reference_is_refused_durably_and_repaired_exactly_without_reanalysis(prefix, article, truncated):
    state = before_final()
    t = tools(state)
    target, proposal = real_save(state, prefix)
    assert target == article and truncated in proposal["reviewed_evidence_ids"]
    task = next(x for x in state["scientific_reading"]["tasks"] if x["article_id"] == article)
    state["automatic_reading_focus"] = copy.deepcopy(task["pending"])
    assert not state["articles"][article]["analysis"]
    with pytest.raises(ValueError) as refused:
        t.execute("analyze_article", copy.deepcopy(proposal), float("inf"), settle=True)
    payload = refused.value.payload
    assert payload["rejection_code"] == "invalid_evidence_reference"
    assert payload["invalid_references"] == [{"location": f"reviewed_evidence_ids[{proposal['reviewed_evidence_ids'].index(truncated)}]",
        "value": truncated}]
    record = state["analysis_refusals"][-1]
    assert record["status"] == "open" and record["proposal"]["analysis"] and record["id"] == payload["repair_id"]
    # A replay of the same proposal (restart) keeps one record.
    with pytest.raises(ValueError):
        t.execute("analyze_article", copy.deepcopy(proposal), float("inf"), settle=True)
    assert len([r for r in state["analysis_refusals"] if r["article_id"] == article]) == 1
    repair = {"article_ids": [article], "action": "repair", "repair_id": record["id"], "research_context": "",
        "passage_query": "", "passage_offset": 0, "passages_per_article": 1, "analysis": None}
    # No approximate match: neither the server nor a near identifier completes the truncated one.
    for wrong in (truncated, truncated + "x", truncated[:-1]):
        with pytest.raises(ValueError):
            t.execute("analyze_article", {**repair, "replacements": [{"invalid": truncated, "replacement": wrong}]},
                float("inf"), settle=True)
    with pytest.raises(ValueError):                                          # every refused reference needs its fix
        t.execute("analyze_article", {**repair, "replacements": []}, float("inf"), settle=True)
    assert record["status"] == "open" and not state["articles"][article]["analysis"]
    exact = next(ref for ref, eid in record["valid_refs"].items() if ref.startswith("E") and eid.startswith(truncated))
    result = t.execute("analyze_article", {**repair, "replacements": [{"invalid": truncated, "replacement": exact}]},
        float("inf"), settle=True)
    assert result["saved"] and result["repaired"] == record["id"] and record["status"] == "repaired"
    card = t.registry[article].analysis
    claims = [c for f, v in card.items() if isinstance(v, list) and v and isinstance(v[0], dict) and "statement" in v[0]
        and f not in {"scientific_problem", "research_objective", "proposed_method", "main_results", "contributions",
            "relevance_to_current_topic"} for c in v]
    assert len(claims) >= 15                                                 # the whole proposed analysis, kept
    assert all(t.evidence[eid]["article_id"] == article for c in claims for eid in c["evidence_ids"])
    reviewed = state["scientific_reading_reviews"][-1]["evidence_ids"]
    assert record["valid_refs"][exact] in reviewed and truncated not in reviewed


def test_the_reading_context_offers_the_repair_and_a_refused_task_is_not_replayed():
    from agents.EnnoScholarV4.scientific_reading import reading_context
    from agents.EnnoScholarV4.task_results import TaskResults
    state = before_final()
    t = tools(state)
    _, proposal = real_save(state, "24958aaad8")
    task = next(x for x in state["scientific_reading"]["tasks"] if x["article_id"] == NEURFRAME)
    state["automatic_reading_focus"] = copy.deepcopy(task["pending"])
    with pytest.raises(ValueError):
        t.execute("analyze_article", proposal, float("inf"), settle=True)
    packet = reading_context(state, t)
    assert packet["pending_repair"]["invalid_references"][0]["value"] == "ev_9e23d7c9c7bfd5e82aa203d"
    # A refused receipt is not a reusable proposal (the generated one was replayed before).
    rows = TaskResults(state, t.config, {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0,
        "unreported_reserved_cost_usd": 0}, [])
    row = next(v for k, v in state["task_results"].items() if k.startswith("24958aaad8"))
    assert rows.proposal(row) is not None
    rows.reject(row, row["attempts"][-1], {"error": "refused"})
    assert rows.proposal(row) is None


# --- 3. A saved reading is not executed again ----------------------------------------------------

def test_a_window_saved_before_an_interruption_is_completed_locally():
    """The DL-Polycube window saved by receipt 2b5d74f6 is left pending (crash between the save and
    the ledger update): resuming completes it without a new analysis request."""
    from agents.EnnoScholarV4.scientific_reading import prepare_reading, saved_reading
    state = before_final()
    t = tools(state)
    row = next(v for k, v in state["task_results"].items() if k.startswith("2b5d74f6a7"))
    assert row["status"] == "saved" and row["target"] == DLP
    task = next(x for x in state["scientific_reading"]["tasks"] if x["article_id"] == DLP)
    # The focus this receipt answered (same key as the agent's reading requests).
    focus = None
    for ids in (row["evidence_ids"], row["attempts"][-1]["evidence_ids"]):
        trial = {"article_id": DLP, "evidence_ids": list(ids), "reading_window_ids": [], "basis": "fulltext"}
        if saved_reading(state, trial):
            focus = trial
    if focus is None:
        pytest.skip("Fenêtre de ce reçu non reconstituable")
    task["pending"] = copy.deepcopy(focus)
    task["status"] = "awaiting_analysis"
    calls = []
    result = prepare_reading(state, t, task, lambda *a, **k: calls.append(a) or {"reading": []})
    assert task.get("reused_saved_windows") == 1
    assert result is None or result["evidence_ids"] != focus["evidence_ids"]


def test_the_automatic_reading_never_presents_again_a_passage_already_examined():
    """The four DL-Polycube passage requests of the run (revisit=true), replayed on its ledger:
    previously each returned already analysed passages, each followed by a new analysis."""
    state = before_final()
    t = tools(state)
    task = next(x for x in state["scientific_reading"]["tasks"] if x["article_id"] == DLP)
    examined = set(task["examined_evidence_ids"]) | {e["evidence_id"] for e in t.registry[DLP].analysis["evidence"]}
    reads = [json.loads(r["content"][0]["arguments"]) for k, r in state["task_results"].items()
        if r["kind"] == "reading" and r["target"] == DLP and r["status"] == "generated"]
    assert len(reads) == 4 and all(r["action"] == "read" for r in reads)
    state["automatic_reading_focus"] = {"article_id": DLP, "evidence_ids": [], "reading_window_ids": [], "basis": "fulltext"}
    repeated_before = repeated_now = 0
    for read in reads:
        result = t.execute("analyze_article", copy.deepcopy(read), float("inf"))
        window = result["reading"][0]
        ids = [p["evidence_id"] for p in window["passages"]]
        repeated_now += len(set(ids) & examined)
        repeated_before += len(set(window.get("already_examined_evidence_ids", [])))
    assert repeated_before > 0 and repeated_now == 0


# --- 4/5. One canonical selection; verified versions grouped, proofs never mixed -----------------

def test_the_counter_the_cards_and_the_answer_use_one_selection():
    from agents.EnnoScholarV4 import research
    state = before_final()
    observed = research.status(state)
    assert observed["verified_analyzed_ids"] == [MESHNET, DLP, DDPM, TETGAN, NONIT]
    assert observed["verified_analyzed_count"] == 5 == observed["requested_count"]       # never 6/5
    assert not observed["pending_article_ids"] and observed["target_reached"] and not observed["complete"]
    assert observed["abstract_only_ids"] == [MESHNET]                                     # not a full-text analysis
    assert set(observed["fulltext_documented_ids"]) == {DLP, DDPM, TETGAN, NONIT}
    _, _, memory = final_memory(state)
    assert memory["response_selection"]["article_ids"] == observed["verified_analyzed_ids"]
    assert [p["id"] for p in memory["conversation_science"]["papers"]] == observed["verified_analyzed_ids"]
    assert [a["id"] for a in memory["selected_articles"]] == observed["verified_analyzed_ids"]
    depth = memory["conversation_science"]["transmission"]["works"]
    assert depth[MESHNET]["level"] == "abstract_only" and "abstract seul" in depth[MESHNET]["analysis_depth"]


def publish(state, args):
    t = tools(state)
    t.finalization_only = True
    return t.execute("finalize_response", copy.deepcopy(args), float("inf"))


def real_final(state):
    row = next(v for k, v in state["task_results"].items() if k.startswith("f096b5150e"))
    return json.loads(row["content"][0]["arguments"])


def test_the_published_list_must_be_the_canonical_selection_with_its_figures_and_its_2d_scope():
    state = before_final()
    args = real_final(state)
    assert args["displayed_article_ids"] == [NONIT, DLP, TETGAN, NVMG, MESHNET]          # NVMG instead of DDPM-Polycube
    refused = publish(copy.deepcopy(state), args)
    # One refusal names every problem of the real answer, so one correction is enough.
    assert refused["rejection_code"] == "selection_mismatch"
    assert refused["issues"] == ["selection_mismatch", "study_scope_omitted", "saved_results_omitted"]
    assert refused["unexpected_article_ids"] == [NVMG] and refused["missing_article_ids"] == [DDPM]
    assert refused["flagged_article_ids"] == [NONIT]
    fixed = {**args, "article_ids": [NONIT, DLP, TETGAN, DDPM, MESHNET], "displayed_article_ids": [NONIT, DLP, TETGAN, DDPM, MESHNET],
        "answer": args["answer"].replace(NVMG, DDPM).replace("Neural Volumetric Mesh Generator", "DDPM-Polycube")}
    refused = publish(copy.deepcopy(state), fixed)
    assert refused["issues"] == ["study_scope_omitted", "saved_results_omitted"]
    fixed["answer"] += "\n\nLe travail non itératif est validé sur des problèmes d'élasticité linéaire 2D, pas en volume."
    refused = publish(copy.deepcopy(state), fixed)
    assert refused["rejection_code"] == "saved_results_omitted" and refused["issues"] == ["saved_results_omitted"]
    # The run called these unavailable: TetGAN's FID, Non-iterative's gain, and the figures 3DMeshNet's
    # abstract announces (« jusqu'à 85 % », facteur 4 à 8), stated as an abstract's claims.
    assert set(refused["omitted_values"]) == {TETGAN, NONIT, MESHNET}
    assert "23.35" in refused["omitted_values"][TETGAN] and "85" in refused["omitted_values"][MESHNET]
    fixed["answer"] += ("\n\nTetGAN : FID 23,35 sans discriminateur global et 12,27 sans discriminateur local ; 98,9 % et "
        "98,5 % d'arêtes manifold. Non-iterative : +22 % de qualité, meilleur pour 94 % des problèmes. "
        "DL-Polycube : 9 900 maillages générés. 3DMeshNet (abstract seul) annonce jusqu'à 85 % de temps "
        "d'entraînement en moins.")
    assert publish(copy.deepcopy(state), fixed)["published"]


def test_verified_versions_are_one_work_whose_proofs_stay_separate():
    from agents.EnnoScholarV4 import research
    from agents.EnnoScholarV4.scientific_reading import next_task, reading_tasks
    state = before_final()
    groups = research.version_groups(state)
    assert groups[DLP] == groups[DLP_JOURNAL] and groups[DLP] != groups[TETGAN]
    selection = research.canonical_selection(state)
    assert selection["versions"][DLP] == [DLP_JOURNAL, DLP] and DLP_JOURNAL not in selection["selected_ids"]
    t = tools(state)
    # The journal version (its own text) is not read again in depth once the arXiv version is documented…
    reading_tasks(state, t.registry, (), (DLP_JOURNAL,))
    task = next(x for x in state["scientific_reading"]["tasks"] if x["article_id"] == DLP_JOURNAL)
    task.update(status="queued", pending=None)
    for other in state["scientific_reading"]["tasks"]:
        if other["article_id"] != DLP_JOURNAL:
            other["status"] = "analyzed"
    assert next_task(state, t.registry) is None or next_task(state, t.registry)["article_id"] != DLP_JOURNAL
    # …unless the consultant names it; and a repaired card of it keeps its own proofs.
    assert next_task(state, t.registry, priority_ids=(DLP_JOURNAL,))["article_id"] == DLP_JOURNAL
    proofs = {e["article_id"] for e in t.registry[DLP].analysis["evidence"]}
    assert proofs == {DLP}


# --- 6. A 2D study is not presented as a volumetric demonstration -----------------------------

def test_an_explicit_2d_validation_is_flagged_from_the_stored_abstract_only_where_it_is_said():
    from agents.EnnoScholarV4.context import study_scope_flags
    state = before_final()
    t = tools(state)
    flags = study_scope_flags(t.registry[NONIT])
    assert flags and flags[0]["flag"] == "validation_2d" and "two-dimensional linear-elastic" in flags[0]["quote"]
    for aid in (TETGAN, DLP, DDPM, NVMG):                                  # « 2D images », « 2D CNNs » are not studies
        assert not study_scope_flags(t.registry[aid]), aid
    _, _, memory = final_memory(state)
    assert memory["conversation_science"]["transmission"]["works"][NONIT]["scope_flags"][0]["flag"] == "validation_2d"


# --- 8. Budget and model --------------------------------------------------------------------------

def test_the_final_request_costs_cents_and_every_call_of_the_job_was_luna():
    from agents.EnnoScholarV4 import pricing
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.model_policy import request_policy, MODEL
    state = before_final()
    t, inputs, _ = final_memory(state)
    assert MODEL == "gpt-6-luna" and request_policy({"model": "gpt-5.6-sol"})["model"] == MODEL
    tokens = len(json.dumps(inputs, ensure_ascii=False).encode()) // 3
    price = pricing.resolve(ScholarV4Config(api_key="mock"), MODEL)
    assert tokens * price.input / 1e6 < 0.01                     # the protected dossier: < 1 cent
    job = session()["writing_job"]
    assert job["config_fingerprint"]["model"] == MODEL
    assert job["spent"]["confirmed_usd"] <= job["budget_usd"]
    models = {a.get("model_requested") for r in session()["task_results"].values() for a in r["attempts"] if a.get("model_requested")}
    assert models == {MODEL}


# --- The same execution path, end to end: the job's next segment publishes from the dossier -----

class CarefulModel:
    """Answers the final request only from what it receives: the selection, each work's saved
    figures and scope flags. Any other request is recorded (none is expected)."""
    def __init__(self):
        self.calls = []

    def with_options(self, **kw):
        return self

    @property
    def responses(self):
        return self

    def create(self, **kw):
        from types import SimpleNamespace as NS
        from tests.ennoscholar_v4.test_finalization import Stream, call
        self.calls.append(kw)
        memory = json.loads(next(i for i in kw["input"] if i.get("role") == "developer")["content"].split(" : ", 1)[1])
        assert kw["tool_choice"] == {"type": "function", "name": "finalize_response"}, kw["tool_choice"]
        science = memory["conversation_science"]
        papers = {p["id"]: p for p in science["papers"]}
        ids = memory["response_selection"]["article_ids"]
        lines, evidence = ["## Sélection", ""], []
        for aid in ids:
            paper, depth = papers[aid], science["transmission"]["works"][aid]
            results = [c for f in ("results", "metrics") for c in paper["analysis"].get(f, [])]
            lines.append(f"**{paper['title']}** [{aid}] — {depth['analysis_depth']}.")
            for claim in results[:3]:
                lines.append(claim["statement"] + " " + " ".join(f"[{e}]" for e in claim["evidence_ids"]))
                evidence += claim["evidence_ids"]
            for flag in depth.get("scope_flags", []):
                lines.append("Portée : validation sur des problèmes 2D (« " + flag["quote"][:80] + " »), pas en volume.")
        out = call("finalize_response", {"answer": "\n\n".join(lines), "article_ids": ids, "evidence_ids": list(dict.fromkeys(evidence)),
            "coverage_sufficient": False, "remaining_uncertainties": ["Lecture ciblée, non exhaustive."], "analyses": None,
            "displayed_article_ids": ids}, len(self.calls))
        return Stream(NS(output=out, output_text="", status="completed", usage={"input_tokens": 18000, "output_tokens": 1500}))


def test_the_next_segment_publishes_the_saved_figures_without_any_new_reading():
    from agents.EnnoScholarV4 import research
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    from unittest.mock import Mock
    state = before_final()
    # A deliberate partial stop can publish saved science without re-reading it;
    # merely reaching five cards may no longer bypass scientific review.
    research.save_review(state, research.ResearchReviewArgs(reason="Fiches réutilisées, profondeur encore partielle.",
        gaps=["Une publication seulement sur abstract."], stop_reason="Limite documentaire, résultat partiel explicite."))
    request = state["messages"][0]
    cards = {aid: copy.deepcopy(a["analysis"]) for aid, a in state["articles"].items() if a.get("analysis")}
    state["writing_segment"] = {"job_id": JOB, "kind": "research", "request_message_id": request["id"],
        "progress_id": state["research_progress"]["id"], "goal": copy.deepcopy(state["writing_job"]["goal"]), "blocked": {}}
    model, providers = CarefulModel(), Mock()
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=model, sources=providers).run(state)
    assert result["publication_accepted"], (result.get("reason"), result.get("finalization_refusals"))
    assert len(model.calls) == 1 and not providers.search.called                # one request: the final one
    answer = result["answer"]
    for value in ("23,35", "12,27", "22 %", "85 %"):
        assert value in answer, value
    assert "2D" in answer
    assert result["primary_article_ids"] == research.status(state)["verified_analyzed_ids"]
    assert "4 analysée(s) sur des passages ciblés du texte intégral" in answer and "1 sur l'abstract seul" in answer
    # No card changed, no reading task run, every call priced for gpt-6-luna.
    assert all(state["articles"][aid]["analysis"] == card for aid, card in cards.items())
    assert {kw["model"] for kw in model.calls} == {"gpt-6-luna"}


def test_a_reading_refused_for_one_truncated_identifier_is_repaired_by_one_small_request(monkeypatch):
    """The agent's own reading path: the model saves a complete analysis whose reviewed_evidence_ids
    has one identifier cut by one character (as for NeurFrame); the refusal is recorded, the next
    request carries the repair, the model answers with the replacement only, the card is saved."""
    from tests.ennoscholar_v4.test_performance import corpus, with_refs, memory, run
    from tests.ennoscholar_v4.test_finalization import Stream, call
    from types import SimpleNamespace as NS
    state, sources = corpus(1)
    sent = []

    class Client:
        def with_options(self, **kw): return self
        @property
        def responses(self): return self
        def create(self, **kw):
            if kw.get("tool_choice") == {"type": "function", "name": "set_turn_goal"}:
                return Stream(NS(output=[], output_text="", status="completed", usage={"input_tokens": 10, "output_tokens": 5}))
            sent.append(kw)
            n = len(sent)
            if kw.get("tool_choice") == {"type": "function", "name": "analyze_article"}:
                packet = memory(kw)["automatic_scientific_reading"]
                repair = packet.get("pending_repair")
                if repair:
                    bad = repair["invalid_references"][0]["value"]
                    args = {"article_ids": [packet["article_id"]], "action": "repair", "repair_id": repair["repair_id"],
                        "replacements": [{"invalid": bad, "replacement": "E1"}], "research_context": "", "passage_query": "",
                        "passage_offset": 0, "passages_per_article": 1, "analysis": None}
                else:
                    first = packet["passages"][0]["evidence_id"]
                    args = {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x", "passage_query": "",
                        "passage_offset": 0, "passages_per_article": 6, "analysis": with_refs(packet),
                        "reviewed_evidence_ids": [first[:-1]], "reviewed_fields": ["results"]}
                return Stream(NS(output=call("analyze_article", args, f"r{n}"), output_text="", status="completed",
                    usage={"input_tokens": 9000, "output_tokens": 300 if repair else 2600}))
            ids = list(state["articles"])
            return Stream(NS(output=call("finalize_response", {"answer": "Synthèse " + " ".join(f"[{a}]" for a in ids),
                "article_ids": ids, "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": [],
                "analyses": None, "displayed_article_ids": ids}, f"f{n}"), output_text="", status="completed",
                usage={"input_tokens": 9000, "output_tokens": 500}))

    run(state, sources, Client())
    readings = [kw for kw in sent if kw.get("tool_choice") == {"type": "function", "name": "analyze_article"}]
    assert len(readings) == 2                                               # the save, then its repair only
    record = state["analysis_refusals"][0]
    assert record["status"] == "repaired" and record["invalid"][0]["location"] == "reviewed_evidence_ids[0]"
    assert state["articles"]["paper_s0"]["analysis"]["method"]
    row = next(r for r in state["task_results"].values() if r["kind"] == "reading")
    assert [a.get("outcome") for a in row["attempts"]] == ["analysis_refused", "analysis_repaired"]
    assert row["status"] == "saved"
