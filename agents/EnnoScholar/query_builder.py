# -*- coding: utf-8 -*-
from __future__ import annotations

"""Universal query builder grounded in the current EnnoDiagnostic lock.

Every domain-specific token used in a query must come from the current payload:
- lock title;
- linked evidence/source passages;
- explicit keywords/concepts already attached by EnnoDiagnostic.

The module contains only generic linguistic/scientific relation words.  It does
not contain client, project, discipline, technology, software or acronym maps.
"""

import os
import re
from typing import Any, Dict, Iterable, List, Set

from .utils import clean_text, norm, tokenize


META_TERMS = {
    "cir", "frascati", "ennodiagnostic", "ennoscholar", "consultant", "dossier",
    "qualification", "json", "api", "pdf", "docx", "rag", "nlp", "llm",
    "project", "projet", "document", "documents", "source", "sources",
    "evidence", "preuve", "preuves", "verrou", "lock", "scientific",
    "scientifique", "technical", "technique",
}

# Generic words that should not be sufficient to anchor a scientific query.
WEAK_ANCHORS = {
    "study", "paper", "article", "research", "result", "results", "analysis",
    "etude", "article", "recherche", "resultat", "resultats", "analyse",
    "problem", "probleme", "question", "approach", "approaches", "approche",
}

QUERY_NOISE = {
    "intrinsically", "thus", "therefore", "hence", "herein", "whereas",
    "grounded", "version", "cite", "cited", "citation", "citations",
    "figure", "fig", "table", "section", "appendix", "respectively",
    "namely", "according", "indeed", "overview", "introduction",
    "conclusion", "conclusions", "ie", "eg",
    "incertitude", "uncertainty", "impossibilite", "impossibility",
}

FR_FUNCTION_WORDS = {
    "sur", "avec", "sans", "dans", "pour", "des", "les", "une", "un", "aux",
    "du", "de", "la", "le", "et", "ou", "entre", "vers", "notamment", "ainsi",
    "ce", "cette", "ces", "leur", "leurs", "par", "en", "au",
}
EN_FUNCTION_WORDS = {
    "the", "with", "without", "from", "into", "for", "and", "or", "between",
    "this", "that", "these", "those", "their", "by", "in", "on", "of", "to",
}

GENERIC_RELATIONS = {
    "fr": {
        "validation": ["validation", "experimentale", "mesures"],
        "limits": ["limites", "incertitude"],
        "comparison": ["comparaison", "methodes"],
        "robustness": ["robustesse", "generalisation"],
    },
    "en": {
        "validation": ["validation", "experimental", "measurements"],
        "limits": ["limitations", "uncertainty"],
        "comparison": ["comparison", "methods"],
        "robustness": ["robustness", "generalization"],
    },
}

KIND_PRIORITY = {
    "title_core": 1.92,
    "title_evidence": 1.66,
    "passage_specific": 1.59,
    "keywords_evidence": 1.56,
    "validation": 1.40,
    "limitations": 1.34,
    "comparison": 1.28,
    "robustness": 1.24,
    "literal_evidence": 1.16,
    "backend_enriched_source_query_safe": 1.00,
    "auto": 0.80,
}


def _as_text_list(value: Any, max_chars: int = 180) -> List[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        out: List[str] = []
        for key in ("term", "text", "label", "name", "value", "query"):
            if value.get(key) is not None:
                out.extend(_as_text_list(value.get(key), max_chars=max_chars))
        return out
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            out.extend(_as_text_list(item, max_chars=max_chars))
        return out
    text = clean_text(value, max_chars)
    return [text] if text else []


def _tokens(value: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    for token in tokenize(value):
        token = str(token).strip(".,;:()[]{}\"\'")
        nt = norm(token)
        if not nt or nt in META_TERMS or nt in QUERY_NOISE or len(nt) < 3:
            continue
        if nt not in seen:
            seen.add(nt)
            out.append(token)
    return out


def _detect_language(intent: Dict[str, Any]) -> str:
    explicit = str(intent.get("query_language") or intent.get("source_language") or "").lower()
    if explicit.startswith("fr"):
        return "fr"
    if explicit.startswith("en"):
        return "en"
    title = str(intent.get("verrou_title") or intent.get("original_title") or "").lower()
    raw_words = set(re.findall(r"[a-zA-ZÀ-ÿ]+", title))
    fr = len({norm(x) for x in raw_words} & FR_FUNCTION_WORDS)
    en = len({norm(x) for x in raw_words} & EN_FUNCTION_WORDS)
    return "fr" if fr >= en else "en"


def _intent_anchor_tokens(intent: Dict[str, Any]) -> Set[str]:
    values: List[str] = []
    for key in (
        "title_tokens", "title_key_terms", "explicit_keywords", "passage_key_terms", "evidence_key_terms",
        "strong_anchors", "core_concepts", "technical_object", "phenomenon",
        "methods", "literal_source_acronyms", "literal_source_phrases",
        "literal_source_terms", "key_terms_fr", "key_terms_en",
    ):
        values.extend(_as_text_list(intent.get(key)))
    return {
        norm(token)
        for token in _tokens(" ".join(values))
        if norm(token) not in WEAK_ANCHORS
    }


def _ordered_unique_tokens(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        for token in _tokens(value):
            nt = norm(token)
            if not nt or nt in seen or nt in WEAK_ANCHORS:
                continue
            seen.add(nt)
            out.append(token)
    return out


def _query_bounds() -> tuple[int, int]:
    try:
        minimum = max(3, int(os.getenv("ENNOSCHOLAR_QUERY_MIN_WORDS", "5") or 5))
    except Exception:
        minimum = 5
    try:
        maximum = max(minimum, min(14, int(os.getenv("ENNOSCHOLAR_QUERY_MAX_WORDS", "10") or 10)))
    except Exception:
        maximum = 10
    return minimum, maximum


def _make_query(values: Iterable[Any], *, max_words: int | None = None) -> str:
    minimum, configured_max = _query_bounds()
    limit = min(max_words or configured_max, configured_max)
    tokens = _ordered_unique_tokens(values)
    if len(tokens) < minimum:
        return ""
    return clean_text(" ".join(tokens[:limit]), 220)


def _query_similarity(a: str, b: str) -> float:
    aa = {norm(x) for x in _tokens(a)}
    bb = {norm(x) for x in _tokens(b)}
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, len(aa | bb))


def is_query_safe_for_intent(query: str, intent_or_profile: Dict[str, Any] | str) -> bool:
    query = clean_text(query, 220)
    minimum, maximum = _query_bounds()
    query_tokens = [norm(x) for x in _tokens(query)]
    if len(query_tokens) < minimum or len(query_tokens) > maximum:
        return False
    if not isinstance(intent_or_profile, dict):
        return True
    anchors = _intent_anchor_tokens(intent_or_profile)
    local = {token for token in query_tokens if token not in WEAK_ANCHORS}
    # Two independently observed local terms are required.  Generic relation
    # words cannot make an unrelated query pass this guard.
    return len(local & anchors) >= 2


def detect_scholar_profile(intent: Dict[str, Any]) -> str:
    _ = intent
    return "generic_evidence_grounded"


def build_queries_from_intent(intent: Dict[str, Any], max_queries: int = 14) -> List[Dict[str, Any]]:
    intent = dict(intent or {})
    language = _detect_language(intent)
    relations = GENERIC_RELATIONS[language]

    title_tokens = _as_text_list(intent.get("title_tokens"))
    title_terms = _as_text_list(intent.get("title_key_terms"))
    explicit_keywords = _as_text_list(intent.get("explicit_keywords"))
    passage_terms = _as_text_list(intent.get("passage_key_terms"))
    evidence_terms = _as_text_list(intent.get("evidence_key_terms"))
    acronyms = _as_text_list(intent.get("literal_source_acronyms"))
    literal_phrases = _as_text_list(intent.get("literal_source_phrases"))

    # Ordered local basis.  The title is intentionally dominant because it is
    # the consultant-visible scientific lock; evidence then disambiguates it.
    keyword_core = _ordered_unique_tokens(explicit_keywords)
    # Title-first contract: the consultant-visible lock title is always the
    # primary source of query vocabulary. Evidence and explicit EnnoDiagnostic
    # keywords only disambiguate/complement it; they never displace it.
    title_tail = title_tokens[-4:] if len(title_tokens) > 4 else title_tokens
    title_only_core = _ordered_unique_tokens(acronyms + title_tokens + title_terms)
    title_core = _ordered_unique_tokens(title_only_core + keyword_core)
    passage_core = _ordered_unique_tokens(passage_terms)
    evidence_core = _ordered_unique_tokens(evidence_terms + literal_phrases)

    queries: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def add(values: Iterable[Any], kind: str, basis: List[str], *, max_words: int | None = None) -> None:
        query = _make_query(values, max_words=max_words)
        key = norm(query)
        if not query or not key or key in seen:
            return
        if not is_query_safe_for_intent(query, intent):
            return
        seen.add(key)
        queries.append({
            "query": query,
            "kind": kind,
            "basis": basis,
            "query_language": language,
            "hardcoded_domain_rules": False,
        })

    # 1) Compact title representation.  No relation vocabulary is injected.
    add([title_only_core[:10]], "title_core", ["verrou_title"], max_words=10)

    # 2) Title + passages: this is the main disambiguating query.
    add(
        [title_core[:6], evidence_core[:3], keyword_core[:2]],
        "title_evidence",
        ["verrou_title", "linked_passages", "ennodiagnostic_keywords"],
        max_words=10,
    )

    # 3) Passage-specific query: preserves discriminating wording that may not
    # appear in the title (for example a test condition, phenomenon or method).
    add(
        [acronyms[:2], passage_core[:7], title_tail[:2]],
        "passage_specific",
        ["linked_passages", "verrou_title"],
        max_words=10,
    )

    # 4) Explicit keywords + evidence, useful when the title is verbose.
    add(
        [acronyms[:2], keyword_core[:8], evidence_core[:3]],
        "keywords_evidence",
        ["ennodiagnostic_keywords", "linked_passages"],
        max_words=10,
    )

    # Generic scientific relation queries.  Domain terms still come exclusively
    # from the current lock/evidence; relation terms are domain-neutral.
    core_for_relations = title_core[:4] + keyword_core[:2] + evidence_core[:2]
    add([core_for_relations, relations["validation"]], "validation", ["verrou_title", "linked_passages", "generic_relation"], max_words=10)
    add([core_for_relations, relations["limits"]], "limitations", ["verrou_title", "linked_passages", "generic_relation"], max_words=9)
    add([core_for_relations, relations["comparison"]], "comparison", ["verrou_title", "linked_passages", "generic_relation"], max_words=9)
    add([core_for_relations, relations["robustness"]], "robustness", ["verrou_title", "linked_passages", "generic_relation"], max_words=9)

    # Last-resort literal query from evidence only; no generated ontology.
    if len(queries) < 3:
        add(
            [acronyms[:2], evidence_core[:8]],
            "literal_evidence",
            ["linked_passages"],
            max_words=10,
        )

    return queries[:max_queries]


def select_best_queries_for_intent(
    queries_generated: List[Dict[str, Any]],
    intent: Dict[str, Any],
    max_queries: int = 3,
) -> List[Dict[str, Any]]:
    max_queries = max(1, int(max_queries or 3))
    anchors = _intent_anchor_tokens(intent)
    candidates: List[Dict[str, Any]] = []
    seen = set()

    for raw in queries_generated or []:
        item = dict(raw) if isinstance(raw, dict) else {"query": str(raw), "kind": "auto"}
        query = clean_text(item.get("query"), 220)
        if not is_query_safe_for_intent(query, intent):
            continue
        key = norm(query)
        if not key or key in seen:
            continue
        seen.add(key)
        query_tokens = {norm(x) for x in _tokens(query)}
        local_hits = query_tokens & anchors
        kind = str(item.get("kind") or "auto")
        score = KIND_PRIORITY.get(kind, 0.80) + min(len(local_hits) * 0.28, 1.40)
        # Prefer queries grounded in more than one source type.
        basis = item.get("basis") if isinstance(item.get("basis"), list) else []
        score += min(len(set(map(str, basis))) * 0.08, 0.24)
        item.update({
            "query": query,
            "anchor_count": len(local_hits),
            "selection_score": round(score, 4),
            "hardcoded_domain_rules": False,
        })
        candidates.append(item)

    candidates.sort(key=lambda item: item.get("selection_score", 0.0), reverse=True)
    selected: List[Dict[str, Any]] = []
    for item in candidates:
        if len(selected) >= max_queries:
            break
        if any(_query_similarity(item["query"], other["query"]) >= 0.88 for other in selected):
            continue
        selected.append(item)

    # If similarity filtering was too strict, fill with the best remaining safe
    # query rather than returning no query at all.
    if len(selected) < min(max_queries, len(candidates)):
        selected_keys = {norm(item["query"]) for item in selected}
        for item in candidates:
            if len(selected) >= max_queries:
                break
            if norm(item["query"]) in selected_keys:
                continue
            selected.append(item)
            selected_keys.add(norm(item["query"]))

    return selected[:max_queries]


def attach_queries_to_intent(intent: Dict[str, Any], max_queries: int = 14) -> Dict[str, Any]:
    out = dict(intent or {})
    out["cir_domain_profile"] = {
        "profile_id": "generic_evidence_grounded",
        "label": "Generic evidence grounded",
    }
    out["backend_enrichment_profile"] = "generic_evidence_grounded"
    out["enrichment_profile"] = "generic_evidence_grounded"
    out["search_queries"] = build_queries_from_intent(out, max_queries=max_queries)
    out["query_builder_version"] = "v161_title_passages_verified_keywords_universal"
    out["hardcoded_domain_rules"] = False
    return out

# ENNOSCHOLAR_V166_2_UNIVERSAL_QUERY_PLANNER_BEGIN
# V166.2 is tailored to the current V161 evidence-grounded agent uploaded on
# 2026-08-19.  Legacy functions above remain for rollback/history; the public
# API is overridden below by the evidence-grounded scientific planner.
from .scientific_query_workflow import (
    PLANNER_VERSION as _V166_2_PLANNER_VERSION,
    attach_query_plan as _v166_2_attach_query_plan,
    build_queries as _v166_2_build_queries,
    query_is_safe as _v166_2_query_is_safe,
    select_queries as _v166_2_select_queries,
)


def is_query_safe_for_intent(query: str, intent_or_profile: Dict[str, Any] | str) -> bool:
    if not isinstance(intent_or_profile, dict):
        return False
    enriched = _v166_2_attach_query_plan(intent_or_profile)
    return _v166_2_query_is_safe(query, enriched)


def build_queries_from_intent(intent: Dict[str, Any], max_queries: int = 14) -> List[Dict[str, Any]]:
    return _v166_2_build_queries(intent, max_queries=max_queries)


def select_best_queries_for_intent(
    queries_generated: List[Dict[str, Any]],
    intent: Dict[str, Any],
    max_queries: int = 3,
) -> List[Dict[str, Any]]:
    return _v166_2_select_queries(queries_generated, intent, max_queries=max_queries)


def attach_queries_to_intent(intent: Dict[str, Any], max_queries: int = 14) -> Dict[str, Any]:
    return _v166_2_attach_query_plan(intent, max_queries=max_queries)
# ENNOSCHOLAR_V166_2_UNIVERSAL_QUERY_PLANNER_END

# ENNOSCHOLAR_V167_LANGGRAPH_QUERY_WORKFLOW
