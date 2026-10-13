# -*- coding: utf-8 -*-
from __future__ import annotations

"""Adaptive scientific vocabulary refinement for EnnoScholar V167.6.

One extra LLM call is allowed only when retrieval recall remains weak after the
normal query portfolio + Wave 2.  The model does not re-plan the scientific
problem.  It only proposes alternate close phrasings grounded in:
- the already validated scientific plan;
- vocabulary actually observed in retrieved titles/abstract snippets.

No client/project/domain vocabulary is hard-coded here.
"""

import json
import re
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

from .fast_retrieval import VERSION, query_is_useful, sanitize_query_text, _similarity


def _clean(value: Any, max_chars: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_chars].strip()


def _extract_plan(intent: Mapping[str, Any]) -> Mapping[str, Any]:
    plan = intent.get("scientific_query_plan")
    if isinstance(plan, Mapping):
        return plan
    return {}


def _term(row: Any) -> str:
    if isinstance(row, Mapping):
        return _clean(row.get("term_en") or row.get("value"), 140)
    return _clean(row, 140)


def _plan_summary(plan: Mapping[str, Any]) -> Dict[str, List[str]]:
    keys = [
        "scientific_object",
        "independent_variables",
        "response_variables",
        "operating_conditions",
        "phenomena",
        "methods",
        "validation_concepts",
        "local_identifiers",
    ]
    out: Dict[str, List[str]] = {}
    for key in keys:
        vals = []
        for row in plan.get(key) or []:
            value = _term(row)
            if value and value not in vals:
                vals.append(value)
        if vals:
            out[key] = vals[:6]
    return out


def _article_text(row: Mapping[str, Any]) -> str:
    return _clean(f"{row.get('title') or ''} {row.get('abstract') or ''}", 3500)


def _plan_tokens(plan: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for vals in _plan_summary(plan).values():
        for value in vals:
            tokens.update(re.findall(r"[a-z0-9][a-z0-9+./%-]*", value.lower()))
    return {t for t in tokens if len(t) >= 3}


def _select_literature_examples(
    papers: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
    max_examples: int = 10,
) -> List[Dict[str, str]]:
    plan_tokens = _plan_tokens(plan)
    scored = []
    for row in papers or []:
        if not isinstance(row, Mapping) or row.get("normalized_error"):
            continue
        title = _clean(row.get("title"), 280)
        if not title:
            continue
        abstract = _clean(row.get("abstract"), 700)
        text_tokens = set(re.findall(r"[a-z0-9][a-z0-9+./%-]*", f"{title} {abstract}".lower()))
        overlap = len(plan_tokens & text_tokens)
        if overlap <= 0:
            continue
        scored.append((overlap, title, abstract))
    scored.sort(key=lambda x: (-x[0], x[1].lower()))
    return [
        {"title": title, "abstract_excerpt": abstract}
        for _, title, abstract in scored[: max(1, int(max_examples or 10))]
    ]


def _parse_json_payload(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def build_adaptive_refinement_queries(
    intent: Mapping[str, Any],
    papers: Sequence[Mapping[str, Any]],
    existing_queries: Sequence[Any],
    llm_call: Callable[..., Mapping[str, Any]],
    *,
    max_queries: int = 2,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return up to two close alternate queries from one bounded LLM call."""
    plan = _extract_plan(intent)
    report: Dict[str, Any] = {
        "version": VERSION,
        "enabled": True,
        "llm_calls": 0,
        "queries_count": 0,
        "reason": "not_started",
    }
    if not plan:
        report["reason"] = "missing_scientific_query_plan"
        return [], report

    examples = _select_literature_examples(papers, plan, max_examples=10)
    if len(examples) < 3:
        report["reason"] = "insufficient_literature_vocabulary"
        return [], report

    existing = []
    for row in existing_queries or []:
        q = row.get("query") if isinstance(row, Mapping) else row
        q = sanitize_query_text(q, max_words=14)
        if q:
            existing.append(q)

    payload = {
        "scientific_plan": _plan_summary(plan),
        "existing_queries": existing[:8],
        "retrieved_literature_examples": examples,
    }
    prompt = (
        "You refine scientific search vocabulary for a literature review.\n"
        "The scientific plan is already validated: DO NOT change the problem and DO NOT add unsupported concepts.\n"
        "Use only concepts from the scientific plan and scientific vocabulary visibly present in the retrieved literature examples.\n"
        "Propose at most 2 ALTERNATIVE CLOSE queries that express the same core scientific relation with different literature wording.\n"
        "Do not make broader domain queries. Do not repeat an existing query. Keep each query 5-12 words.\n"
        "Return JSON only: {\"queries\":[{\"query\":\"...\",\"reason\":\"observed vocabulary ...\"}]}.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    report["llm_calls"] = 1
    try:
        response = llm_call(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            timeout=20,
        )
    except Exception as exc:
        report["reason"] = f"llm_exception:{type(exc).__name__}"
        return [], report

    if not isinstance(response, Mapping) or not response.get("ok"):
        report["reason"] = "llm_failed"
        report["error"] = _clean(response.get("error") if isinstance(response, Mapping) else "", 300)
        return [], report

    data = _parse_json_payload(str(response.get("content") or ""))
    rows = data.get("queries") if isinstance(data, Mapping) else []
    if not isinstance(rows, list):
        rows = []

    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        query = sanitize_query_text(row.get("query"), max_words=12)
        if not query or not query_is_useful(query, plan):
            continue
        if any(_similarity(query, old) >= 0.84 for old in existing):
            continue
        if any(_similarity(query, x["query"]) >= 0.90 for x in out):
            continue
        out.append({
            "query": query,
            "kind": "v167_6_adaptive_refinement",
            "family": "adaptive_refinement",
            "search_level": "proche_refinement",
            "target_category": "Direct",
            "strictness": 0.97,
            "planner_version": VERSION,
            "portfolio_source": "retrieval_vocabulary_llm",
            "reason": _clean(row.get("reason"), 300),
        })
        if len(out) >= max(1, min(int(max_queries or 2), 2)):
            break

    report["queries_count"] = len(out)
    report["queries"] = [x["query"] for x in out]
    report["examples_used"] = len(examples)
    report["reason"] = "queries_generated" if out else "no_safe_novel_refinement_query"
    return out, report
