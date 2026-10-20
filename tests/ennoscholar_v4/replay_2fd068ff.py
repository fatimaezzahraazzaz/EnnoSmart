"""Offline replay of real run 2fd068ff (session 23236f69, 18 October 2026): the draft
of the state of the art from the saved 12-section plan.

The real run spent 11 rounds on section 1 (16 passage reads, 11 refused project-context
calls, no save attempt) and ended with an upstream error: 0/12 sections in 146 s.

The session is read from the local database (read-only) and rebuilt as it was before
the run. The fake model answers with the durations measured in the real traces and a
scaled hybrid clock (model calls x100, local processing at real speed). No network,
no OpenAI call, no write.
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock

from tests.ennoscholar_v4.replay_e7bae458 import DB, Clock, CLOCK, Stream, call, memory, forced

SESSION = "23236f69"
REQUEST = ("Rédige maintenant l'état de l'art de mon projet CIR à partir du plan proposé et des quatre verrous identifiés. "
    "Appuie-toi sur les publications scientifiques pertinentes, compare leurs méthodes, résultats et limites, et explique "
    "ce qui reste non résolu par rapport à mon projet. Cite précisément tes sources.")
# Measured (s, output tokens): understanding 5.6 s; full-text analysis 145 s (2 600 tokens),
# abstract analysis 88 s; retrieval rounds 4-12 s. A saved section of ~600 words is about
# 1 500 output tokens: 60 s at the measured 25-30 tokens/s.
DURATION = {"understanding": (5.6, 133), "analysis_fulltext": (145.0, 2600), "analysis_abstract": (88.0, 1500),
    "retrieval": (8.0, 500), "section": (60.0, 1500)}


def archived_outline(state, before):
    """The plan and drafts as saved before a run started (latest document version created
    before it): later runs' drafts are not part of the replayed state."""
    import copy as _copy
    versions = [v for v in state.get("document_versions", []) if v["created_at"] < before]
    if not versions:
        return
    state["outline"] = _copy.deepcopy(versions[-1]["outline"])
    state["document_versions"] = [v for v in state["document_versions"] if v["created_at"] < before]
    for key in ("manuscript_memory", "comparison_reading"):
        state.pop(key, None)


def available():
    return DB.is_file()


def load():
    db = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    data = json.loads(db.execute("select data from scholar_v4_sessions where id like ?", (SESSION+"%",)).fetchone()[0])
    plan = db.execute("select data from scholar_v4_outlines where session_id like ? and version=1", (SESSION+"%",)).fetchone()
    prior_run = db.execute("select data from scholar_v4_runs where id='d71d17b007d149738df84896dfe62510'").fetchone()
    db.close()
    state = copy.deepcopy(data)
    if plan and prior_run:
        state["outline"] = json.loads(plan[0])
        state["evaluation"]["recommended_article_ids"] = json.loads(prior_run[0])["recommended_article_ids"]
    for key in ("task_results", "plan_generation", "section_read_rounds", "section_received_claims"):
        state.pop(key, None)
    index = next(i for i, m in enumerate(state["messages"]) if m.get("role") == "user" and m["content"].startswith("Rédige maintenant"))
    state["messages"] = state["messages"][:index+1]
    for key in ("argumentation_focus", "writing_focus", "writing_planning", "turn_goal", "automatic_reading_focus", "selection_focus"):
        state.pop(key, None)
    state.get("document", {}).pop("writing_progress", None)
    state.get("document", {}).pop("active_section_id", None)
    # The plan of that run (12 sections, no draft) as archived before it started.
    archived_outline(state, "2026-10-18T18:21:51")
    state.pop("task_results", None)
    return state


def analysis(packet):
    """A card written only from the passages of the window (short references)."""
    rows = packet["passages"]
    claim = lambda row: {"statement": row["exact_quote"][:300], "evidence_ids": [row["ref"]], "origin": "source_statement"}
    numeric = [r for r in rows if re.search(r"\d", r["exact_quote"])] or rows[:1]
    empty = {k: [] for k in ("problem", "objective", "contribution", "hypotheses", "generalization", "open_questions",
        "relevance", "equations", "parameters", "validation_protocol", "datasets_materials", "tables_figures", "comparisons",
        "conditions_of_validity", "technical_mechanism", "experimental_setup")}
    return {**empty, "method": [claim(rows[0])], "results": [claim(numeric[0])], "limitations": [claim(rows[-1])],
        "method_family": "replay", "analysis_limit": "Passages ciblés seulement."}


class DraftModel:
    """Writes like a careful model: reads the project context and the cards, then saves.
    stubborn=True reproduces run 2fd068ff: it only reads and never saves on its own."""
    def __init__(self, state, stubborn=False):
        self.state, self.stubborn = state, stubborn
        self.lock, self.calls, self.log, self.rounds = threading.Lock(), [], [], {}

    def with_options(self, **kw): return self

    @property
    def responses(self): return self

    def create(self, **kw):
        with self.lock:
            self.calls.append(kw)
            n = len(self.calls)
        output, (seconds, tokens), kind = self.respond(n, kw)
        with self.lock:
            self.log.append((kind, seconds))
        CLOCK[0].wait(seconds)
        return Stream(NS(output=output, output_text="", status="completed",
            usage={"input_tokens": len(json.dumps(kw["input"], ensure_ascii=False))//4, "output_tokens": tokens}))

    def respond(self, n, kw):
        name, mem = forced(kw), memory(kw)
        if name == "set_turn_goal":
            return [call("set_turn_goal", {"deliverable": "draft", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "",
                "reading_depth": "deep", "understanding": "Rédiger l'état de l'art à partir du plan.", "search_needed": False}, n)], \
                DURATION["understanding"], "understanding"
        if name == "analyze_article":
            packet = mem["automatic_scientific_reading"]
            kind = "analysis_fulltext" if packet.get("basis") == "fulltext" else "analysis_abstract"
            return [call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "état de l'art",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis(packet)}, n)], DURATION[kind], "analysis"
        section = (mem.get("section_drafting") or {}).get("focus") or {}
        sid = section.get("section_id")
        if sid:
            self.rounds[sid] = self.rounds.get(sid, 0)+1
            tools = {t.get("name") for t in kw["tools"]}
            if tools == {"write_section"} or (not self.stubborn and self.rounds[sid] >= 2):
                return [call("write_section", self.section_save(sid, mem), n)], DURATION["section"], "section"
            if self.rounds[sid] == 1:
                # The project facts of the section's locks, then the claims of one card.
                return [call("get_project_context", {"kind": "facts", "offset": 0, "limit": 5, "ids": None, "scope": "active"}, f"{n}p"),
                    call("write_section", {"section_id": sid, "action": "read", "instructions": "", "text": None,
                        "article_ids": [], "evidence_ids": []}, f"{n}r")], DURATION["retrieval"], "retrieval"
            article = next(iter(mem["section_drafting"]["packet"].get("articles", [])), None)
            return [call("analyze_article", {"article_ids": [article["id"]] if article else [], "action": "read",
                "research_context": "x", "passage_query": "results limitations", "passage_offset": 0, "passages_per_article": 3,
                "analysis": None}, n)], DURATION["retrieval"], "retrieval"
        return [{"type": "message", "content": [{"type": "output_text", "text": "Rédaction en cours.", "annotations": []}]}], (5.0, 50), "conversation"

    def section_save(self, sid, mem):
        """One sentence per source from a proof-backed claim, and one comparison with a
        project fact of the section's locks (both origins cited)."""
        packet = mem["section_drafting"]["packet"]
        cited, evidence, sentences = [], [], []
        for claim in packet.get("proof_backed_claims", []):
            if claim["article_id"] in cited:
                continue
            eid = claim["evidence_ids"][0]
            cited.append(claim["article_id"]); evidence.append(eid)
            sentences.append(f"La publication [{claim['article_id']}] documente un élément de méthode ou de résultat étayé par [{eid}].")
        project = [f for row in (mem.get("diagnostic_context") or {}).get("locks", []) for unit in row.get("work_units", [])
            for facts in unit.get("roles", {}).values() for f in facts if f.get("evidence_ids")]
        project_ids = []
        if project:
            pid = project[0]["evidence_ids"][0]
            project_ids.append(pid)
            if cited:
                sentences.append(f"Le projet a observé une difficulté comparable [projet:{pid}], que cette publication [{cited[0]}] "
                    f"ne lève que dans ses propres conditions [{evidence[0]}].")
            else:
                sentences.append(f"Le projet documente cette difficulté [projet:{pid}] ; les publications restent à analyser.")
        if not cited and not project_ids:
            sentences = ["Cette section reste à documenter : aucune fiche enregistrée pour ses sources."]
        return {"section_id": sid, "action": "save", "instructions": "", "text": " ".join(sentences),
            "article_ids": cited, "evidence_ids": evidence, "project_evidence_ids": project_ids}


def sources():
    mock = Mock()
    mock.last_provider_stats = None
    mock.search.return_value = ([], [])
    mock.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    mock.metadata.return_value = []
    return mock


def run(stubborn=False, timeout=600):
    from agents.EnnoScholarV4 import agent as module
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    state = load()
    # No network in the replay: works without a stored text are read from their abstract.
    for row in state["articles"].values():
        if not (row.get("fulltext") or {}).get("text"):
            row.setdefault("fulltext", {})["attempts"] = [{"replay": "no_network"}]
    clock = Clock()
    CLOCK[0] = clock
    previous = module.time
    module.time = NS(monotonic=clock)
    events = []
    try:
        model = DraftModel(state, stubborn)
        config = ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False, input_price=4.0, output_price=20.0, timeout_seconds=timeout)
        # get_fulltext would fetch: in the replay, the stored texts are the only ones available.
        from agents.EnnoScholarV4 import scientific_reading
        original = scientific_reading.prepare_reading
        def prepare(state_, toolkit, task, run_tool):
            task["access_attempted"] = True
            return original(state_, toolkit, task, run_tool)
        scientific_reading.prepare_reading = prepare
        try:
            result = ScholarChatAgent(config, client=model, sources=sources()).run(state, emit=events.append)
        finally:
            scientific_reading.prepare_reading = original
    finally:
        module.time = previous
    by_kind = {}
    for kind, seconds in model.log:
        by_kind[kind] = round(by_kind.get(kind, 0)+seconds, 1)
    return {"result": result, "state": state, "events": events, "model_seconds_by_kind": by_kind, "calls": len(model.calls)}


if __name__ == "__main__":
    for stubborn in (False, True):
        out = run(stubborn)
        r = out["result"]
        progress = out["state"]["document"].get("writing_progress", {})
        refused = sum(e.get("tool") == "get_project_context" and e.get("status") == "failed" for e in out["events"])
        print(json.dumps({"stubborn": stubborn, "reason": r["reason"], "wall": r["timings"]["wall_seconds"],
            "stages": r["timings"].get("stages"), "calls": out["calls"], "model_seconds_by_kind": out["model_seconds_by_kind"],
            "sections_saved": len(progress.get("saved_draft_ids", {})), "sections_total": len(progress.get("target_ids", [])),
            "project_context_refused": refused, "analyses_saved": r["tool_counts"].get("analyze_article"),
            "parallel": r["timings"].get("max_concurrent_analyses"), "cost": round(r["usage"]["estimated_cost_usd"], 3),
            "answer_head": r["answer"][:200]}, ensure_ascii=False, indent=1))
