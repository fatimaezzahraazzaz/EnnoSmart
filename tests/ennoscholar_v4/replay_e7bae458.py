"""Offline replay of real run e7bae458 (session 23236f69, 18 October 2026).

The session state is read from the local database (read-only) and rebuilt as it
was before the run: the run's own searches, their new notices and the project
facts it recorded are removed. Each search returns the notices the real run
received for that query. The fake model answers like the real one, with the
durations measured in the trace. A scaled clock (1 real second = 100 simulated
seconds) lets concurrent calls overlap. No network, no OpenAI call, no write.
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
import time as real_time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

DB = Path(__file__).resolve().parents[2] / "ennoscholar_v4_test.db"
SESSION = "23236f69"
REQUEST_ID = "0c731db104b2420f8d6b82eb0eb4c39c"
SCALE = 100.0
# Durations (s) and output tokens measured in e7bae458 (and d8606428 for a long plan).
DURATION = {"project_facts": (27.5, 394), "comparison": (17.7, 450), "blueprint": [(139.7, 4055), (121.4, 3176)],
    "conversation": [(9.4, 676), (7.2, 566), (10.8, 617)], "understanding": (5.0, 250), "selection": (39.0, 853),
    "plan": (160.0, 4500), "plan_part": (45.0, 1200), "final": (152.0, 5100)}
# Searches of the run, in order: query, lock, measured duration.
ROUND_9 = [("road segmentation noisy labels domain shift", "1", 8), ("road mask polygonization topology vectorization self intersections", "2", 15),
    ("SegArchA SegArchB road segmentation training stability", "3", 2), ("road boundary programmation dynamique dynamic programming coupled contours", "4", 6)]
ROUND_10 = [("SegArchA SegArchB semantic segmentation architecture benchmark", "3", 2), ("road boundary extraction dynamic programming aerial imagery", "4", 3),
    ("coupled road boundaries active contour parallel constraint", "4", 4)]
ROUND_11 = ["SegArchA+ encoder decoder atrous separable convolution semantic segmentation", "SegArchB high resolution representations semantic segmentation",
    "polygon road vectorization topology raster vector remote sensing", "road boundary dynamic programming active contour parallel constraint"]


def available():
    return DB.is_file()


def load():
    """The session before e7bae458, and the notices each of its searches returned."""
    db = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    data = json.loads(db.execute("select data from scholar_v4_sessions where id like ?", (SESSION+"%",)).fetchone()[0])
    db.close()
    state = copy.deepcopy(data)
    index = next(i for i, m in enumerate(state["messages"]) if m.get("id") == REQUEST_ID)
    state["messages"] = state["messages"][:index+1]
    run_searches = state["recent_searches"][6:]
    state["recent_searches"] = state["recent_searches"][:6]
    before = {aid for s in state["recent_searches"] for aid in s["article_ids"]} | set(state["evaluation"].get("recommended_article_ids", []))
    before |= {d["article_id"] for d in state.get("candidate_decisions", [])}
    results = {s["query"]: [copy.deepcopy(data["articles"][aid]) for aid in s["article_ids"] if aid in data["articles"]] for s in run_searches}
    state["articles"] = {aid: row for aid, row in state["articles"].items() if aid in before}
    state["exposed_article_ids"] = [aid for aid in state.get("exposed_article_ids", []) if aid in before]
    state["examined_article_ids"] = [aid for aid in state.get("examined_article_ids", []) if aid in before]
    state["project_facts"] = []
    for key in ("comparison_reading", "comparisons"):
        state[key] = {}
    for key in ("turn_goal", "last_response"):
        state.pop(key, None)
    # This run predates the first plan in this session. Later live runs may have
    # saved a manuscript; those artifacts must not leak into this historical replay.
    for key in ("outline", "outline_versions", "document", "document_versions", "task_results",
                "plan_generation", "section_read_rounds"):
        state.pop(key, None)
    return state, results


class Clock:
    """Simulated time: model calls and provider searches count exactly their stated
    duration (overlapping calls counted once, played SCALE times faster); local
    processing counts the CPU time this process really spends, as in production.

    Wall time is not used for either part: a machine put to sleep, or slowed down by
    other programs, used to turn a few real seconds of oversleep into minutes of
    simulated time and stop the replay on its time budget (b52f582a in the full suite)."""
    def __init__(self):
        self.start = real_time.process_time()
        self.lock = threading.Lock()
        self.extra, self.active, self.since, self.window = 0.0, 0, 0.0, 0.0

    def progress(self):
        # Simulated time elapsed in the current window of model calls, never beyond
        # the stated durations (oversleep, standby and load are not charged).
        return min((real_time.perf_counter()-self.since)*SCALE, self.window) if self.active else 0.0

    def __call__(self):
        with self.lock:
            return real_time.process_time()-self.start+self.extra+self.progress()

    def wait(self, seconds):
        with self.lock:
            if not self.active:
                self.since, self.window = real_time.perf_counter(), 0.0
            # This call ends `seconds` after the point the window has reached.
            self.window = max(self.window, self.progress()+seconds)
            self.active += 1
        real_time.sleep(seconds/SCALE)
        with self.lock:
            self.active -= 1
            if not self.active:
                self.extra += self.window


CLOCK = [None]


def call(name, args, number):
    return {"type": "function_call", "name": name, "call_id": f"call_{name}_{number}", "arguments": json.dumps(args)}


class Stream:
    def __init__(self, response): self.response = response
    def __iter__(self): return iter([NS(type="response.completed", response=self.response)])
    def close(self): pass


def memory(kw):
    for item in kw["input"]:
        if item.get("role") == "developer" and item.get("content", "").startswith("Mémoire"):
            return json.loads(item["content"].split(" : ", 1)[1])
    return {}


def forced(kw):
    choice = kw.get("tool_choice")
    return choice.get("name") if isinstance(choice, dict) else None


class ReplayModel:
    """Answers like the real model of e7bae458, with its measured durations."""
    def __init__(self, state):
        self.state, self.lock, self.calls, self.log = state, threading.Lock(), [], []
        self.conversations = self.blueprints = 0

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

    def goal(self):
        return {"deliverable": "plan", "lock_ids": ["1", "2", "3", "4"], "focus_lock_ids": [], "exact_quote": "",
            "reading_depth": "core", "understanding": "Plan CIR détaillé par verrou, avec les publications pertinentes ; sans rédaction."}

    def respond(self, n, kw):
        name, mem = forced(kw), memory(kw)
        task = mem.get("context_task", "conversation")
        focus = mem.get("argumentation_focus") or {}
        if name == "set_turn_goal":
            return [call("set_turn_goal", self.goal(), n)], DURATION["understanding"], "understanding"
        if name == "record_project_facts" or focus.get("stage") == "project_facts":
            return [call("record_project_facts", {"facts": [{"category": "constraint", "statement": "Plan sans rédaction",
                "exact_quote": self.quote(focus.get("source_id")), "source_type": "consultant_message", "source_id": focus.get("source_id") or REQUEST_ID,
                "value": None, "unit": None}]}, n)], DURATION["project_facts"], "argumentation"
        if name == "compare_articles" or focus.get("stage") == "comparison":
            return [call("compare_articles", {"article_ids": focus["article_ids"], "research_context": "comparaison",
                "conclusions": None, "relations": [], "claim_offsets": []}, n)], DURATION["comparison"], "argumentation"
        if name == "build_argument_blueprint" or focus.get("stage") == "blueprint":
            # The real blueprints cited a plan section that did not exist: refused twice.
            duration = DURATION["blueprint"][min(self.blueprints, 1)]
            self.blueprints += 1
            return [call("build_argument_blueprint", {"action": "save", "merge": False, "global_question": "Q", "narrative_logic": "L",
                "axes": [{"axis": "a", "scientific_problem": "p", "section_id": "section_absent", "transition": "",
                    "steps": [{"kind": "problem", "statement": "s", "article_ids": focus["article_ids"][:1], "evidence_ids": [],
                        "relation_ids": [], "project_fact_ids": [], "conditions": "", "uncertainty": ""}]}],
                "remaining_questions": [], "claim_offsets": []}, n)], duration, "argumentation"
        if name == "record_candidate_decisions":
            return [call("record_candidate_decisions", self.decisions(mem), n)], DURATION["selection"], "selection"
        if name == "propose_plan_sections":
            return [call("propose_plan_sections", self.part(mem), n)], DURATION["plan_part"], "plan"
        if name == "manage_outline" or task == "planning":
            return [call("manage_outline", self.outline(mem), n)], DURATION["plan"], "plan"
        if name == "finalize_response":
            return [call("finalize_response", {"answer": "Synthèse.", "article_ids": [], "evidence_ids": [],
                "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}, n)], DURATION["final"], "final"
        # Free conversation rounds, as in rounds 9-11 of the real run.
        index = self.conversations
        self.conversations += 1
        duration = DURATION["conversation"][min(index, 2)]
        if index == 0:
            items = [] if mem.get("turn_goal") else [call("set_turn_goal", self.goal(), f"{n}g")]
            items += [call("search_scientific", {"query": q, "sources": None, "limit_per_source": 10, "offset": 0,
                "family": f"verrou {lock}", "lock_ids": [lock]}, f"{n}s{i}") for i, (q, lock, _) in enumerate(ROUND_9)]
            return items, duration, "search"
        if index == 1:
            return [call("search_scientific", {"query": q, "sources": None, "limit_per_source": 10, "offset": 0,
                "family": f"verrou {lock}", "lock_ids": [lock]}, f"{n}s{i}") for i, (q, lock, _) in enumerate(ROUND_10)], duration, "search"
        if index == 2:
            return [call("search_collected_articles", {"query": q, "limit": 12, "exclude_presented": True, "family": None},
                f"{n}c{i}") for i, q in enumerate(ROUND_11)], duration, "search"
        return [call("finalize_response", {"answer": "Synthèse.", "article_ids": [], "evidence_ids": [], "coverage_sufficient": False,
            "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None}, n)], DURATION["final"], "final"

    def quote(self, message_id):
        text = next((m["content"] for m in self.state["messages"] if m.get("id") == message_id), "")
        return text.split(".")[0][:200] or "plan"

    def decisions(self, mem):
        """A careful reviewer: retains road works matching a lock, rejects the rest."""
        terms = {"1": r"label|noisy|domain|adaptation", "2": r"polygon|vector|topolog", "3": r"hrnet|deeplab|architecture",
            "4": r"boundar|curb|dynamic programming|contour|edge"}
        rows, finalists = [], []
        for row in mem.get("selection_candidates", []):
            text = (row.get("title", "") + " " + row.get("abstract", "")).casefold()
            locks = [lid for lid, pattern in terms.items() if re.search(pattern, text)] if "road" in text else []
            rows.append({"article_id": row["id"], "decision": "retain" if locks else "reject",
                "justification": "Contribution routière liée au verrou." if locks else "Hors du périmètre routier.", "lock_ids": locks})
            if locks and len(finalists) < 8:
                finalists.append(row["id"])
        return {"decisions": rows[:20], "finalist_ids": [f for f in finalists if f in {r["article_id"] for r in rows[:20]}],
            "covered_families": [], "remaining_axes": []}

    def sections(self, mem, locks):
        retained = {}
        for row in mem.get("plan_sources", []):
            for lid in row.get("lock_ids") or []:
                retained.setdefault(lid, []).append(row["id"])
        return [{"title": f"Verrou {lid} : état des méthodes", "description": "Argument.", "article_ids": retained.get(lid, [])[:3],
            "scientific_objective": f"Quelles méthodes répondent au verrou {lid} ?", "questions": [], "article_roles": [],
            "relation_ids": [], "experimental_results": [], "limitations": [], "evidence_ids": [],
            "missing_information": [] if retained.get(lid) else ["Aucune publication retenue"], "project_connection": "À confronter au projet.",
            "figure_suggestions": [], "lock_ids": [lid]} for lid in locks]

    def outline(self, mem):
        action = "create" if not (self.state.get("outline") or {}).get("sections") else "replace"
        return {"action": action, "title": "État de l'art", "section_id": None, "target_section_id": None, "position": None,
            "sections": self.sections(mem, ["1", "2", "3", "4"]), "article_ids": None, "offset": 0, "limit": 8,
            "consultant_message": "Plan construit à partir des publications retenues."}

    def part(self, mem):
        part = mem.get("plan_part") or {}
        if part.get("kind") == "framing":
            intro = {**self.sections(mem, [])[0:1][0], "title": "Introduction : de la segmentation au polygone exploitable", "lock_ids": []}                 if self.sections(mem, []) else None
            frame = lambda title: {"title": title, "description": "Cadre.", "article_ids": [], "scientific_objective": "", "questions": [],
                "article_roles": [], "relation_ids": [], "experimental_results": [], "limitations": [], "evidence_ids": [],
                "missing_information": [], "project_connection": "", "figure_suggestions": [], "lock_ids": []}
            return {"sections": [frame("Introduction : de la segmentation au polygone exploitable")],
                "closing_sections": [frame("Synthèse et perspectives")],
                "consultant_note": "Plan construit à partir des publications retenues pour chaque verrou."}
        return {"sections": self.sections(mem, part.get("lock_ids") or ["1"]), "closing_sections": [], "consultant_note": ""}


def sources(results):
    delays = {q: d for q, _, d in ROUND_9+ROUND_10}
    mock = Mock()
    mock.last_provider_stats = None
    mock.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    def search(query, *args, **kwargs):
        from agents.EnnoScholarV4.models import Article
        CLOCK[0].wait(delays.get(query, 5))
        return [Article.model_validate(row) for row in results.get(query, [])], []
    mock.search.side_effect = search
    return mock


def run():
    """Replay with the current code; returns the measures and the final state."""
    from agents.EnnoScholarV4 import agent as module
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    state, results = load()
    clock = Clock()
    CLOCK[0] = clock
    previous = module.time
    module.time = NS(monotonic=clock)
    try:
        model = ReplayModel(state)
        config = ScholarV4Config(api_key="mock", model="gpt-5.6-sol", web_search_enabled=False, input_price=4.0, output_price=20.0)
        events = []
        result = ScholarChatAgent(config, client=model, sources=sources(results)).run(state, emit=events.append)
    finally:
        module.time = previous
    by_kind = {}
    for kind, seconds in model.log:
        by_kind[kind] = round(by_kind.get(kind, 0)+seconds, 1)
    return {"result": result, "state": state, "events": events, "model_seconds_by_kind": by_kind, "calls": len(model.calls)}


if __name__ == "__main__":
    out = run()
    r = out["result"]
    print(json.dumps({"reason": r["reason"], "fallback": r["fallback"], "wall": r["timings"]["wall_seconds"],
        "stages": r["timings"].get("stages"), "calls": out["calls"], "model_seconds_by_kind": out["model_seconds_by_kind"],
        "plan_saved": bool((out["state"].get("outline") or {}).get("sections")),
        "answer_head": r["answer"][:160]}, ensure_ascii=False, indent=1))
