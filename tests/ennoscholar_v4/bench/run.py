"""Offline discovery and ranking bench of EnnoScholar V4.

    python -m tests.ennoscholar_v4.bench.run

Recorded sessions are read with SQLite mode=ro (query_only) and worked in memory; no model,
provider or network call. Two kinds of figures, never mixed:

* MEASURED — real outputs of past runs (finalists chosen by gpt-6-luna, triage order of the
  code on the real candidate pools, the new tier rule applied to the model's recorded
  decisions), scored with the independent judgments of discovery_judgments.json.
* SIMULATED — the model's per-criterion judgments replaced by an oracle derived from the same
  judgments (exact, then with seeded noise). It shows what the ranking rule does with good or
  imperfect criteria judgments; it is NOT a measure of the model's quality.
"""
from __future__ import annotations

import copy
import json
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "ennoscholar_v4_test.db"
SESSIONS = {"mesh": ["53292dc4", "04382dc6", "1e5e467e", "00a2e6bb"], "sar": ["6728b41d", "ee57ed9a"],
    "battery": ["98fddbeb"], "anomaly": ["e34be359"], "road": ["ace44bf8"]}


def load_session(prefix):
    db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    row = db.execute("select data from scholar_v4_sessions where id like ?", (prefix + "%",)).fetchone()
    db.close()
    return json.loads(row[0]) if row else None


def _context(domain, state):
    from .metrics import load, grades_of, dedupe, RELEVANT
    judgments = load(domain)["judgments"]
    articles = state["articles"]
    grade = grades_of(articles, judgments)
    pool = dedupe(list(articles), articles)
    relevant = sum((grade[a] or 0) >= RELEVANT for a in pool)
    return articles, grade, pool, relevant


def _scores(ids, articles, grade, pool, relevant):
    from .metrics import precision_at, ndcg_at, recall_at, dedupe
    ids = dedupe([a for a in ids if a in articles], articles)
    return {"P@5": round(precision_at(ids, grade), 3), "NDCG@5": round(ndcg_at(ids, grade, [grade[a] for a in pool]), 3),
        "R@20": round(recall_at(ids, grade, relevant), 3)}


def current_judgments(state):
    """Recorded judgments were made for this request: mark them current for its need."""
    from agents.EnnoScholarV4 import research
    for aid, article in state["articles"].items():
        if article.get("assessment"):
            article["assessment"].update(need_signature=research.need_signature(state),
                source_signature=research.candidate_signature(state, aid))


def measure(domain, prefix):
    """MEASURED figures of one recorded session."""
    from agents.EnnoScholarV4 import research
    state = load_session(prefix)
    articles, grade, pool, relevant = _context(domain, state)
    finalists = state.get("evaluation", {}).get("recommended_article_ids", [])
    triaged = list(dict.fromkeys(d.get("article_id") for d in state.get("candidate_decisions", []) if d.get("article_id") in articles))
    fresh = copy.deepcopy(state)
    for article in fresh["articles"].values():
        article["assessment"] = {}
    fresh["research_progress"] = {}
    order = research.triage_pool(fresh, maximum=10**6)
    rebalanced = copy.deepcopy(state)
    rebalanced.setdefault("research_progress", {})["requested_count"] = (state.get("research_progress") or {}).get("requested_count") or 5
    current_judgments(rebalanced)
    events = research.rebalance_ranking(rebalanced)
    return {"domain": domain, "session": prefix, "pool": len(pool), "relevant": relevant,
        "recorded_finalists": _scores(finalists, articles, grade, pool, relevant),
        "recorded_triage_order": _scores(triaged, articles, grade, pool, relevant)["R@20"],
        "code_triage_order": _scores(order, articles, grade, pool, relevant)["R@20"],
        "tier_rule_on_recorded_decisions": {**_scores(rebalanced["evaluation"]["recommended_article_ids"], articles, grade, pool, relevant),
            "changes": len(events)},
        "efficiency": efficiency(state)}


def efficiency(state):
    """Recorded cost and repetition counters of a run (MEASURED, as stored)."""
    decisions = [d.get("article_id") for d in state.get("candidate_decisions", [])]
    attempts = [a for t in state.get("task_results", {}).values() for a in t.get("attempts", [])]
    return {"llm_calls": len(attempts), "decisions": len(decisions), "repeated_decisions": len(decisions) - len(set(decisions)),
        "searches": len(state.get("recent_searches", [])),
        "repeated_queries": len(state.get("recent_searches", [])) - len({" ".join(str(s.get("query", "")).casefold().split())
            for s in state.get("recent_searches", [])}),
        "estimated_cost_usd": round(sum(a.get("estimated_cost_usd") or 0 for a in attempts), 4)}


NEED = {"object": "objet du besoin", "problem": "problème", "expected_output": "résultat technique recherché",
    "acceptable_methods": [], "application_context": "contexte", "constraints": [],
    "criteria": [{"id": "c1", "statement": "Produit le résultat technique recherché lui-même.", "essential": True},
        {"id": "c2", "statement": "Démontré sur l'objet, la dimension et l'échelle demandés.", "essential": True},
        {"id": "c3", "statement": "Validation expérimentale rapportée.", "essential": False}],
    "direct": "produit le résultat recherché", "related": "problème voisin", "foundational": "référence",
    "families": [{"family": "famille", "query": "query", "why": "—"}], "uncertainties": []}


def oracle(grade, rng=None, noise=0.0):
    """Simulated per-criterion judgments from an independent grade: 3 meets every essential
    criterion; 2 meets c1, c2 not established; 1 fails c2; 0 is rejected. With noise, each
    status is replaced by a random one with that probability (seeded)."""
    statuses = {3: ("met", "met"), 2: ("met", "unknown"), 1: ("met", "not_met")}.get(grade)
    if statuses is None:
        return None
    statuses = list(statuses)
    for i in range(2):
        if rng is not None and rng.random() < noise:
            statuses[i] = rng.choice(["met", "partial", "not_met", "unknown"])
    contribution = "direct" if statuses[0] == "met" and grade >= 2 else "related"
    if rng is not None and rng.random() < noise:
        contribution = rng.choice(["direct", "related", "foundational"])
    return {"decision": "retain", "contribution": contribution, "relevance": {"criteria": [
        {"criterion_id": "c1", "status": statuses[0], "basis": "abstract", "note": "oracle"},
        {"criterion_id": "c2", "status": statuses[1], "basis": "abstract", "note": "oracle"}]}}


def simulate(domain, prefix, noise=0.0, seed=0):
    """SIMULATED: the recorded finalists (first-come cumulative ranking) as starting point, every
    candidate of the pool judged by the oracle, then the tier rule."""
    from agents.EnnoScholarV4 import research
    state = load_session(prefix)
    articles, grade, pool, relevant = _context(domain, state)
    state["research_need"] = copy.deepcopy(NEED)
    state.setdefault("research_progress", {})["requested_count"] = 5
    state.pop("consultant_order", None)
    rng = random.Random(seed) if noise else None
    for aid in articles:
        articles[aid]["selection_override"] = None if articles[aid].get("selection_override") is not False else False
        judged = oracle(grade[aid] if grade[aid] is not None else 0, rng, noise)
        articles[aid]["assessment"] = judged or {"decision": "reject"}
    current_judgments(state)
    before = list(state.get("evaluation", {}).get("recommended_article_ids", []))[:5]
    research.rebalance_ranking(state)
    return {"before": _scores(before, articles, grade, pool, relevant),
        "after": _scores(state["evaluation"]["recommended_article_ids"], articles, grade, pool, relevant)}


def report():
    rows = [measure(domain, prefix) for domain, prefixes in SESSIONS.items() for prefix in prefixes]
    simulations = {f"{d}/{p}": {"exact": simulate(d, p)["after"], "noise_25%": simulate(d, p, .25, 7)["after"],
        "recorded_start": simulate(d, p)["before"]} for d, prefixes in SESSIONS.items() for p in prefixes}
    return {"measured": rows, "simulated": simulations}


def main():
    if not DB.is_file():
        print("Base locale absente : banc non exécutable.")
        return 1
    sys.path.insert(0, str(ROOT))
    data = report()
    print("MEASURED (independent judgments, recorded runs)")
    print(f"{'session':18} {'pool':>4} {'rel':>3} | finalists P@5 NDCG@5 | tier rule P@5 NDCG@5 chg | R@20 recorded code | calls cost$")
    for r in data["measured"]:
        f, t = r["recorded_finalists"], r["tier_rule_on_recorded_decisions"]
        print(f"{r['domain'] + '/' + r['session']:18} {r['pool']:>4} {r['relevant']:>3} | {f['P@5']:>13} {f['NDCG@5']:>6} | "
            f"{t['P@5']:>13} {t['NDCG@5']:>6} {t['changes']:>3} | {r['recorded_triage_order']:>13} {r['code_triage_order']:>4} | "
            f"{r['efficiency']['llm_calls']:>5} {r['efficiency']['estimated_cost_usd']:>5}")
    print("\nSIMULATED (oracle criteria judgments; not a measure of model quality)")
    for key, s in data["simulated"].items():
        print(f"{key:18} start P@5={s['recorded_start']['P@5']} | exact P@5={s['exact']['P@5']} NDCG@5={s['exact']['NDCG@5']} "
            f"| noise 25% P@5={s['noise_25%']['P@5']} NDCG@5={s['noise_25%']['NDCG@5']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
