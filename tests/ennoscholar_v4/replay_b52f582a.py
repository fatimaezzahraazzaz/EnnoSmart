"""Offline replay of real run b52f582a (session 23236f69, 18 October 2026): « Continue
la rédaction de mon état de l'art CIR à partir des sections déjà enregistrées ».

Real run: 6 parallel proposals, 5 refusals, 1 section saved; one sequential repair
call of 299 s; timeout at 602 s for 0.52 USD (+0.22 reserved).

The session is read from the local database (read-only) and rebuilt as it was before
the run (the draft it saved and the task receipts it created are removed). The six
sections the real model wrote receive its REAL first proposal (text, claims, evidence,
as stored in task_results). Repairs and the other sections are SIMULATED: a careful
writer applying the exact refusal, using only the packet it receives. Durations are
those measured in the real trace. No network, no OpenAI call, no write.
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
RUN_DRAFT = "270463e718ec4200bd7381395ee96094"
REQUEST_PREFIX = "Continue la rédaction de mon état de l'art CIR"
# Measured in b52f582a: understanding 21 s; section proposals 24-62 s (call_seconds).
UNDERSTANDING = (21.0, 128)
SECTION = (35.0, 1900)


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
    db.close()
    state = copy.deepcopy(data)
    proposals = {}
    # Only the tasks of this run (its trace names them); later runs' proposals are excluded.
    db = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    trace = json.loads(db.execute("select data from scholar_v4_runs where id like 'b52f582a%'").fetchone()[0])
    db.close()
    run_tasks = {e.get("task_id") for e in trace.get("activity", []) if e.get("task_id")}
    for task in state.get("task_results", {}).values():
        if task["kind"] != "section" or not task["attempts"] or task["id"] not in run_tasks:
            continue
        first = task["attempts"][0]
        args = next((json.loads(item["arguments"]) for item in first.get("content", [])
            if item.get("type") == "function_call" and item.get("name") == "write_section"), None)
        if args:
            proposals[task["target"]] = args
    index = max(i for i, m in enumerate(state["messages"]) if m.get("role") == "user" and m["content"].startswith(REQUEST_PREFIX))
    state["messages"] = state["messages"][:index+1]
    for key in ("argumentation_focus", "writing_focus", "writing_planning", "turn_goal", "automatic_reading_focus",
            "selection_focus", "task_results", "delivered_project_evidence_ids"):
        state.pop(key, None)
    # Plan and drafts as archived just before the run (later runs saved 11 more sections).
    archived_outline(state, "2026-10-18T22:28:07")
    for section in state["outline"]["sections"]:
        section["drafts"] = [d for d in section.get("drafts", []) if d["id"] != RUN_DRAFT]
    state.get("document", {}).pop("writing_progress", None)
    return state, proposals


def project_facts(mem):
    rows = []
    for lock in (mem.get("diagnostic_context") or {}).get("locks", []):
        for unit in lock.get("work_units", []):
            for facts in unit.get("roles", {}).values():
                rows.extend(f for f in facts if f.get("evidence_ids"))
        rows.extend({"statement": p["quote"], "evidence_ids": [p["evidence_id"]]} for p in lock.get("evidence_passages", []))
    return rows


def confrontation(mem, article_id, eid):
    """Simulated target behaviour: for each lock of the section, what the project tried
    (approach or method) and what it obtained (result) in the same work unit, each with
    its own proof, related to one publication finding (both origins in the comparison)."""
    sentences, project = [], []
    for lock in (mem.get("diagnostic_context") or {}).get("locks", []):
        for unit in lock.get("work_units", [])[:1]:
            roles = unit.get("roles", {})
            tried = next((f for r in ("approaches", "methods") for f in roles.get(r, []) if f.get("evidence_ids")), None)
            obtained = next((f for f in roles.get("results", []) if f.get("evidence_ids")), None)
            if not (tried and obtained):
                continue
            a, r = tried["evidence_ids"][0], obtained["evidence_ids"][0]
            project += [a, r]
            sentences.append(f"Pour ce verrou, le projet a mis en oeuvre une démarche documentée [projet:{a}].")
            sentences.append(f"Le résultat obtenu dans cette même unité de travail reste insuffisant [projet:{r}].")
            if article_id:
                sentences.append(f"La publication [{article_id}] traite cette difficulté dans ses propres conditions expérimentales "
                    f"[{eid}], qui diffèrent de celles du projet [projet:{r}].")
    return sentences, list(dict.fromkeys(project))


class Writer:
    """First attempt of the six real sections: the real proposal. Everything else:
    a writer that follows the packet and the exact refusal (simulation)."""
    def __init__(self, state, proposals):
        self.state, self.proposals = state, proposals
        self.lock, self.calls, self.log = threading.Lock(), [], []
        self.attempts = {}

    def with_options(self, **kw): return self

    @property
    def responses(self): return self

    def create(self, **kw):
        with self.lock:
            self.calls.append(kw)
            n = len(self.calls)
        output, (seconds, tokens), kind, detail = self.respond(n, kw)
        with self.lock:
            self.log.append({"kind": kind, "seconds": seconds, **detail})
        CLOCK[0].wait(seconds)
        return Stream(NS(output=output, output_text="", status="completed",
            usage={"input_tokens": len(json.dumps(kw["input"], ensure_ascii=False))//4, "output_tokens": tokens}))

    def respond(self, n, kw):
        name, mem = forced(kw), memory(kw)
        if name == "set_turn_goal":
            return [call("set_turn_goal", {"deliverable": "draft", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "",
                "reading_depth": "core", "understanding": "Poursuivre la rédaction.", "search_needed": False}, n)], UNDERSTANDING, "understanding", {}
        if name == "analyze_article":
            from tests.ennoscholar_v4.replay_2fd068ff import analysis
            packet = mem["automatic_scientific_reading"]
            return [call("analyze_article", {"article_ids": [packet["article_id"]], "action": "save", "research_context": "x",
                "passage_query": "", "passage_offset": 0, "passages_per_article": 6, "analysis": analysis(packet)}, n)], (88.0, 1500), "analysis", {}
        drafting = mem.get("section_drafting") or {}
        sid = (drafting.get("focus") or {}).get("section_id")
        if not sid:
            return [{"type": "message", "content": [{"type": "output_text", "text": "Rédaction.", "annotations": []}]}], (5.0, 50), "conversation", {}
        refusal = next((json.loads(item["output"]) for item in kw["input"] if item.get("type") == "function_call_output"), None)
        previous = next((json.loads(item["arguments"]) for item in kw["input"] if item.get("type") == "function_call"
            and item.get("name") == "write_section"), None)
        with self.lock:
            self.attempts[sid] = self.attempts.get(sid, 0)+1
            attempt = self.attempts[sid]
        packet = drafting["packet"]
        detail = {"section_id": sid, "attempt": attempt, "real": False, "repair": bool(refusal),
            "proofs_sent": len(packet.get("evidence", [])), "claims_sent": len(packet.get("proof_backed_claims", [])),
            "project_facts_sent": len(project_facts(mem))}
        if attempt == 1 and sid in self.proposals and not refusal:
            args = copy.deepcopy(self.proposals[sid])
            detail["real"] = True
        elif refusal and previous:
            args = self.repair(previous, refusal, mem)
        else:
            args = self.fresh(sid, mem)
        args.update(section_id=sid, action="save")
        return [call("write_section", args, n)], SECTION, "section", detail

    @staticmethod
    def fresh(sid, mem):
        packet = mem["section_drafting"]["packet"]
        cited, evidence, sentences = [], [], []
        for claim in packet.get("proof_backed_claims", []):
            if claim["article_id"] in cited:
                continue
            eid = claim["evidence_ids"][0]
            cited.append(claim["article_id"]); evidence.append(eid)
            sentences.append(f"La publication [{claim['article_id']}] établit un élément de méthode ou de résultat [{eid}].")
        extra, project = confrontation(mem, cited[0] if cited else None, evidence[0] if evidence else None)
        sentences += extra
        if not project:
            facts = project_facts(mem)
            project = [facts[0]["evidence_ids"][0]] if facts else []
            if project:
                sentences.append(f"Le projet documente une difficulté correspondante [projet:{project[0]}].")
        return {"instructions": "", "text": " ".join(sentences), "article_ids": cited, "evidence_ids": evidence,
            "project_evidence_ids": project, "claims": []}

    @staticmethod
    def repair(previous, refusal, mem):
        """Apply the exact refusal, using only the received packet (simulated model)."""
        args = copy.deepcopy(previous)
        error = refusal.get("error", "")
        if refusal.get("claims_not_in_text") or "absentes du texte" in error:
            # Declared claims were paraphrases: rely on the per-sentence citations instead.
            args["claims"] = []
        if "Attribution non vérifiable" in error or "Provenance" in error:
            facts = project_facts(mem)
            for sentence in re.findall(r"« (.+?) »", error):
                names = re.findall(r"[A-Z][A-Za-z]+(?:/[A-Z][A-Za-z]+)?", sentence)
                proof = next((f for f in facts if any(n.split("/")[0] in f["statement"] for n in names)), None)
                start = args["text"].find(sentence[:60])
                end = args["text"].find(".", start)
                if proof and start >= 0 and end > start:
                    pid = proof["evidence_ids"][0]
                    args["text"] = args["text"][:end] + f" [projet:{pid}]" + args["text"][end:]
                    args.setdefault("project_evidence_ids", []).append(pid)
                elif start >= 0 and end > start:
                    args["text"] = args["text"][:start] + args["text"][end+1:]
        if "exige du texte" in error or not (args.get("article_ids") or args.get("project_evidence_ids")):
            facts = project_facts(mem)
            if facts:
                pid = facts[0]["evidence_ids"][0]
                args["text"] = (args.get("text") or "") + f" Le projet documente cette difficulté [projet:{pid}]."
                args["project_evidence_ids"] = [*args.get("project_evidence_ids", []), pid]
        return args


def sources():
    mock = Mock()
    mock.last_provider_stats = None
    mock.search.return_value = ([], [])
    mock.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    mock.metadata.return_value = []
    return mock


def run(timeout=600, state=None, proposals=None):
    """One run of the request; pass the state of a previous run to resume it."""
    from agents.EnnoScholarV4 import agent as module
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4 import scientific_reading
    if state is None:
        state, proposals = load()
    for row in state["articles"].values():
        if not (row.get("fulltext") or {}).get("text"):
            row.setdefault("fulltext", {})["attempts"] = [{"replay": "no_network"}]
    clock = Clock()
    CLOCK[0] = clock
    previous = module.time
    module.time = NS(monotonic=clock)
    original = scientific_reading.prepare_reading
    def prepare(state_, toolkit, task, run_tool):
        task["access_attempted"] = True
        return original(state_, toolkit, task, run_tool)
    scientific_reading.prepare_reading = prepare
    events = []
    try:
        model = Writer(state, proposals)
        config = ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False, input_price=4.0, output_price=20.0, timeout_seconds=timeout)
        result = ScholarChatAgent(config, client=model, sources=sources()).run(state, emit=events.append)
    finally:
        module.time = previous
        scientific_reading.prepare_reading = original
    return {"result": result, "state": state, "events": events, "log": model.log, "calls": len(model.calls), "proposals": proposals}


def summary(out):
    r, state = out["result"], out["state"]
    progress = state["document"].get("writing_progress", {})
    sections = [entry for entry in out["log"] if entry["kind"] == "section"]
    refusals = [e for e in out["events"] if e.get("tool") == "writing_progress" and e.get("status") == "failed"]
    by_section = {}
    for entry in sections:
        by_section[entry["section_id"]] = by_section.get(entry["section_id"], 0)+1
    return {"reason": r["reason"], "wall": r["timings"]["wall_seconds"], "stages": r["timings"].get("stages"),
        "llm_calls": out["calls"], "section_calls": len(sections), "real_proposals_used": sum(e["real"] for e in sections),
        "repairs": sum(e["repair"] for e in sections), "max_calls_one_section": max(by_section.values(), default=0),
        "sections_saved_this_run": len(progress.get("saved_draft_ids", {})) - sum(1 for s in state["outline"]["sections"]
            if any(d["id"] != RUN_DRAFT for d in s.get("drafts", [])[:-1])),
        "sections_saved_total": len(progress.get("saved_draft_ids", {})), "sections_total": len(progress.get("target_ids", [])),
        "blocked": list(progress.get("blocked", {}).values())[:3],
        "min_proofs_sent": min((e["proofs_sent"] for e in sections), default=0),
        "refusal_reasons": sorted({e.get("error", "")[:70] for e in refusals}),
        "cost": round(r["usage"]["estimated_cost_usd"], 3),
        "confrontation": confrontation_summary(state)}


def confrontation_summary(state):
    drafts = [s["drafts"][-1] for s in state["outline"]["sections"] if s.get("drafts") and s["drafts"][-1].get("confrontation")]
    return {"sections_recorded": len(drafts),
        "sections_citing_their_lock_units": sum(d["confrontation"]["units_cited"] > 0 for d in drafts),
        "sections_comparing_publication_and_project": sum(d["confrontation"]["compared_units"] > 0 for d in drafts),
        "roles_cited": sorted({r for d in drafts for r in d["confrontation"]["roles_cited"]}),
        "proofs_outside_section_locks": sum(len(d["confrontation"]["outside_section_locks"]) for d in drafts)}


if __name__ == "__main__":
    print(json.dumps(summary(run()), ensure_ascii=False, indent=1))
