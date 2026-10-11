"""Opt-in real research, then independent comparison. Never send gold papers to agents."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]


def identity(row):
    from agents.EnnoScholarV3.models import normalize_doi
    import re
    return {value for value in ("doi:" + normalize_doi(row.get("doi", "")) if row.get("doi") else "",
        "arxiv:" + re.sub(r"v\d+$", "", row.get("arxiv_id", "")) if row.get("arxiv_id") else "") if value}


def answer_order(rows, answer):
    import re
    text = answer.casefold()
    positions = []
    for row in rows:
        identifiers = [row.get("doi", ""), re.sub(r"v\d+$", "", row.get("arxiv_id", ""))]
        if not any(identifiers):
            identifiers = [row.get("title", "")]
        hits = [text.find(value.casefold()) for value in identifiers if value]
        hits = [position for position in hits if position >= 0]
        if hits:
            positions.append((min(hits), row))
    return [row for _, row in sorted(positions, key=lambda item: item[0])]


def measure(payload, reference, annotations=None):
    annotations = annotations or {}
    papers = payload["ranked_articles"]
    gold = [row for row in reference["references"] if row.get("bibliographic_status") == "verified"]
    metrics = {"gold_bibliographically_verified": len(gold), "human_relevance_validation_complete": all(row.get("human_relevance") is not None for row in gold)}
    for k in (5, 10):
        matches = sum(any(identity(p) & identity(g) for p in papers[:k]) for g in gold)
        metrics[f"Recall@{k}"] = matches / len(gold) if gold else None
    judged = [annotations.get(next(iter(sorted(identity(p))), p["id"])) for p in papers[:10]]
    # Do not silently treat unannotated papers as false positives or self-grade relevance.
    metrics["Precision@10"] = sum(value in {"DIRECT", "CONNEXE", "FONDAMENTAL"} for value in judged) / 10 if len(judged) == 10 and all(judged) else None
    metrics["precision_annotations_needed"] = 10 - len(judged) + sum(value is None for value in judged)
    metrics["benchmark_discovered"] = sum(any(identity(p) & identity(g) for p in payload["all_articles"]) for g in gold)
    metrics["benchmark_verified_in_registry"] = sum(any(identity(p) & identity(g) and p.get("verified") is True for p in payload["all_articles"]) for g in gold)
    metrics["metadata_errors"] = sum(bool(p.get("metadata_errors")) for p in papers)
    metrics["unverified_ranked_references"] = sum(not p.get("verified", False) for p in papers)
    metrics["direct_papers_model_tag"] = sum(p.get("relevance_tag") == "DIRECT" for p in papers)
    metrics["method_families_model_tag"] = sorted({p.get("analysis", {}).get("method_family", "") for p in papers} - {""})
    metrics["analysed_papers"] = sum(bool(p.get("analysis")) for p in payload["all_articles"])
    metrics["fulltexts_downloaded"] = sum(p.get("fulltext_available") is True for p in payload["all_articles"])
    metrics["fulltexts_analysed"] = sum(p.get("analysis_basis") == "fulltext" for p in payload["all_articles"])
    metrics["access_status_errors"] = sum(p.get("analysis_basis") == "fulltext" and not p.get("fulltext_source") for p in papers)
    metrics["web_searches"] = payload.get("result", {}).get("web_searches", payload.get("result", {}).get("tool_counts", {}).get("web_search", 0))
    metrics["native_web_calls"] = payload.get("result", {}).get("tool_counts", {}).get("web_search", 0)
    metrics["duration_seconds"] = payload["duration_seconds"]
    metrics["estimated_cost_usd"] = payload.get("result", {}).get("usage", {}).get("estimated_cost_usd")
    metrics["missing"] = []
    for g in gold:
        rank = next((i + 1 for i, p in enumerate(papers) if identity(g) & identity(p)), None)
        discovered = next((p for p in payload["all_articles"] if identity(g) & identity(p)), None)
        if rank is None or rank > 10:
            causes = []
            if discovered:
                if discovered.get("verified") is False:
                    causes.append("bibliographic_resolution_error")
                elif discovered.get("relevance_tag") == "HORS_SUJET":
                    causes.append("retrieved_then_rejected")
                else:
                    causes.append("ranking_or_not_selected")
            elif payload.get("result", {}).get("partial"):
                causes.append("research_limit_reached")
            else:
                causes.append("not_discovered_query_or_source_gap")
            metrics["missing"].append({"reference": g["label"], "doi": g.get("doi"), "arxiv_id": g.get("arxiv_id"),
                "rank": rank, "discovered": bool(discovered), "observed_cause": causes,
                "cause_status": "hypothesis_except_observed_retrieval_or_limit"})
    return metrics


def live_v3(query, config, output):
    from agents.EnnoScholarV3.agent import ScholarV3ChatAgent
    state = {"messages": [{"role": "user", "content": query}], "articles": {}, "research_state": {}, "summary": ""}
    facts = []
    def emit(event):
        facts.append(event)
        # Persist factual operations progressively, never API reasoning outputs.
        (output / "v3_activity.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
        print(event["tool"], event["status"], event.get("query", ""), event.get("found", ""), flush=True)
    started = time.monotonic()
    result = ScholarV3ChatAgent(config).run(state, emit=emit)
    articles = list(state["articles"].values())
    ids = result["recommended_article_ids"]
    ranked = [state["articles"][aid] for aid in ids]
    # Unranked retrieved articles remain visible for diagnosis, not fabricated into a top ten.
    payload = {"engine": "V3", "query": query, "result": result, "ranked_articles": ranked,
        "all_articles": articles, "activity": facts, "duration_seconds": time.monotonic() - started, "state": state}
    (output / "v3_live.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def live_v2(query, config, output):
    from agents.EnnoScholarV2.agent import ScholarChatAgent
    from agents.EnnoScholarV2.config import ScholarConfig
    from openai import OpenAI
    from dataclasses import replace
    # Private evaluator instance; do not touch V2 service, env, prompts or persistent data.
    options = replace(ScholarConfig.from_env(), model=config.model, api_key=config.api_key)
    registry = {}
    started = time.monotonic()
    result = ScholarChatAgent(options, client=OpenAI(api_key=config.api_key, timeout=45, max_retries=0)).run(query, [], registry)
    rows = []
    for item in registry.values():
        row = item.article.model_dump()
        row["analysis"] = item.analysis.model_dump() if item.analysis else {}
        row["verified"] = None  # V2 doesn't provide V3's registry validation contract.
        row["fulltext_source"] = item.fulltext.fulltext_url if item.fulltext else ""
        rows.append(row)
    ids = result.active_article_ids
    payload = {"engine": "V2", "query": query, "result": {"answer": result.answer, "partial": result.partial,
        "reason": result.reason, "tool_counts": result.tool_counts, "rounds": result.rounds},
        "ranked_articles": answer_order(rows, result.answer), "active_article_ids": ids, "all_articles": rows,
        "activity": [a.model_dump() for a in result.activity], "duration_seconds": time.monotonic() - started}
    (output / "v2_live.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Explicitly enable billed external API calls")
    parser.add_argument("--engine", choices=["v3", "v2", "both"], default="v3")
    parser.add_argument("--reference", type=Path, default=Path(__file__).with_name("validated_reference.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--compare-only", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    annotations = json.loads(args.annotations.read_text(encoding="utf-8")) if args.annotations else {}
    payloads = []
    if args.compare_only:
        payloads = [json.loads(path.read_text(encoding="utf-8")) for path in args.output.glob("*_live.json")]
    else:
        if not args.live:
            parser.error("Live research requires --live and appropriate OPENAI_API_KEY/credits.")
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        from agents.EnnoScholarV3.config import ScholarV3Config
        config = ScholarV3Config.from_env()
        config.validate()
        # Only this single string crosses the production agent boundary. No gold metadata.
        if args.engine in {"v3", "both"}:
            payloads.append(live_v3(reference["query"], config, args.output))
        if args.engine in {"v2", "both"}:
            payloads.append(live_v2(reference["query"], config, args.output))
    results = {p["engine"]: measure(p, reference, annotations) for p in payloads}
    (args.output / "metrics.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
