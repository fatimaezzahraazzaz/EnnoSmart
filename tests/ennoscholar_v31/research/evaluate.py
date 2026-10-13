"""Opt-in scientific evaluation. Gold references never enter production inputs."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT/"backend_api")]


def identities(row):
    from agents.EnnoScholarV31.models import normalize_doi
    import re
    doi = normalize_doi(row.get("doi", ""))
    arxiv = re.sub(r"v\d+$", "", row.get("arxiv_id", ""))
    return {v for v in (("doi:"+doi) if doi else "", ("arxiv:"+arxiv) if arxiv else "") if v}


def measure(payload, case, annotations=None):
    from agents.EnnoScholarV31.evidence import exact_source_valid
    state, result = payload["state"], payload["result"]
    records = state.get("articles", {})
    exposed = set(state.get("exposed_article_ids", []))
    examined = set(state.get("examined_article_ids", []))
    selected = state.get("evaluation", {}).get("recommended_article_ids", [])
    returned = set(result.get("cited_article_ids", []))
    gold = [r for r in case["references"] if r.get("bibliographic_status") == "verified"]
    audit = []
    for reference in gold:
        matched = {aid for aid, row in records.items() if identities(row) & identities(reference)}
        matched_work = {records[aid].get("canonical_work_id") or aid for aid in matched}
        canonical = {aid for aid, row in records.items() if (row.get("canonical_work_id") or aid) in matched_work}
        hit = bool(matched)
        seen, retained, answered = bool(matched & exposed), bool(matched & set(selected)), bool(matched & returned)
        stage = "absent_from_corpus" if not hit else "present_not_exposed" if not seen else "exposed_not_selected" if not retained else "selected_not_returned" if not answered else "returned"
        audit.append({"reference": reference["label"], "doi": reference.get("doi"), "arxiv_id": reference.get("arxiv_id"),
            "corpus_ids": sorted(matched), "presented": seen, "examined": bool(matched & examined),
            "selected": retained, "returned": answered, "failure_stage": stage,
            "canonical_selected": bool(canonical & set(selected)), "canonical_returned": bool(canonical & returned),
            "rank": next((i+1 for i, aid in enumerate(selected) if aid in matched), None)})
    count = len(gold)
    ratio = lambda n: n/count if count else None
    annotations = annotations or {}
    judged = [annotations.get(aid) for aid in selected]
    precision = sum(bool(a["relevant"]) for a in judged)/len(judged) if judged and all(a is not None for a in judged) else None
    families = sorted({f for a in judged if a for f in a.get("families", [])})
    evidence_ids = result.get("cited_evidence_ids", [])
    grounded = sum(eid in state.get("evidence", {}) and exact_source_valid(state["evidence"][eid], state.get("source_snapshots", {})) for eid in evidence_ids)
    bibliography = (state.get("last_response", {}).get("bibliography") or {}).get("articles", [])
    return {"case": case["case"], "evaluation_type": "live" if payload.get("live") else "simulation",
        "gold_count": count, "recall_corpus": ratio(sum(bool(a["corpus_ids"]) for a in audit)),
        "exposure": ratio(sum(a["presented"] for a in audit)), "selection": ratio(sum(a["selected"] for a in audit)),
        "returned": ratio(sum(a["returned"] for a in audit)), "canonical_selection": ratio(sum(a["canonical_selected"] for a in audit)),
        "recall_at_10": ratio(sum(a["rank"] is not None and a["rank"] <= 10 for a in audit)),
        "precision_independent": precision, "precision_annotations_missing": sum(a is None for a in judged),
        "families_independently_annotated": families, "coverage_independent": len(set(families)&set(case.get("expected_families", [])))/len(case["expected_families"]) if all(judged) and judged and case.get("expected_families") else None,
        "bibliographic_validity": sum(a["valid"] for a in bibliography)/len(bibliography) if bibliography else None,
        "quoted_evidence_integrity": grounded/len(evidence_ids) if evidence_ids else None,
        "semantic_grounding_independent": None, "corpus_total": len(records), "presented_total": len(exposed),
        "examined_total": len(examined), "selected_total": len(selected), "returned_total": len(returned),
        "latency_seconds": payload.get("duration_seconds"), "usage": result.get("usage"),
        "partial": result.get("partial"), "stop_reason": result.get("reason"), "reference_audit": audit}


def run_live(query, config, output):
    # Only the user's query crosses this boundary; no references, labels or families.
    from agents.EnnoScholarV31.agent import ScholarV31ChatAgent
    state = {"messages": [{"role": "user", "content": query}], "articles": {}}
    started, events = time.monotonic(), []
    def emit(event):
        events.append(event)
        (output/"activity.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
        (output/"checkpoint.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        print(event["tool"], event["status"], flush=True)
    result = ScholarV31ChatAgent(config).run(state, emit=emit)
    payload = {"live": True, "query": query, "state": state, "result": result, "duration_seconds": time.monotonic()-started}
    (output/"run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approved-budget-usd", type=float)
    parser.add_argument("--case", choices=["all", "mechanical_mesh", "protein_structure", "quantum_tomography"], default="all")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare-only", action="store_true")
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    suite = json.loads(Path(__file__).with_name("references.json").read_text(encoding="utf-8"))
    cases = [c for c in suite["cases"] if args.case in {"all", c["case"]}]
    args.output.mkdir(parents=True, exist_ok=True)
    annotations = json.loads(args.annotations.read_text(encoding="utf-8")) if args.annotations else {}
    metrics = {}
    if not args.compare_only:
        if not args.live or args.approved_budget_usd is None or args.approved_budget_usd <= 0:
            parser.error("Appels payants interdits sans --live ET --approved-budget-usd après validation humaine du budget.")
        from dotenv import load_dotenv
        from dataclasses import replace
        from agents.EnnoScholarV31.config import ScholarV31Config
        profile = args.env_file or (ROOT/".env.native" if (ROOT/".env.native").exists() else ROOT/".env")
        load_dotenv(profile, override=False)
        remaining = args.approved_budget_usd
        base_config = ScholarV31Config.from_env()
    for case in cases:
        directory = args.output/case["case"]
        directory.mkdir(exist_ok=True)
        if args.compare_only:
            path = directory/"run.json"
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            cap = min(base_config.max_cost_usd, remaining)
            if cap <= 0:
                break
            config = replace(base_config, paid_calls_authorized=True, max_cost_usd=cap)
            config.validate()
            payload = run_live(case["query"], config, directory)
            usage = payload["result"]["usage"]
            remaining -= usage["estimated_cost_usd"]+usage.get("unreported_reserved_cost_usd", 0)
        metrics[case["case"]] = measure(payload, case, annotations.get(case["case"]))
    (args.output/"metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
