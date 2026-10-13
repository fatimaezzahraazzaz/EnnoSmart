# -*- coding: utf-8 -*-
from __future__ import annotations

"""Legacy compatibility API, deliberately domain-agnostic since V159.

The previous file contained a static CIR/domain query catalog.  Search V159
must never inject domain vocabulary that is absent from the current evidence.
These functions therefore keep old imports compatible while deriving only
literal terms from the supplied intent/text.
"""

from collections import Counter
import re
from typing import Any, Dict, List

_STOP = {
    "the", "and", "with", "from", "into", "for", "that", "this", "dans", "avec",
    "pour", "sans", "des", "les", "une", "sur", "par", "aux", "project", "projet",
    "technical", "technique", "scientific", "scientifique", "verrou", "recherche",
}


def _norm(text: Any) -> str:
    value = str(text or "").casefold().replace("œ", "oe")
    value = re.sub(r"[^a-z0-9à-ÿ%°µ/._+\- ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _words(text: Any) -> List[str]:
    return [w for w in re.findall(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9%°µ/._+\-]{2,}", str(text or "")) if _norm(w) not in _STOP]


def _intent_text(intent: Dict[str, Any]) -> str:
    return " ".join([
        str(intent.get("verrou_title") or intent.get("title") or ""),
        str(intent.get("scientific_problem") or ""),
        str(intent.get("technical_object") or ""),
        str(intent.get("phenomenon") or ""),
        " ".join(map(str, intent.get("constraints") or [])),
        " ".join(map(str, intent.get("methods") or [])),
        " ".join(map(str, intent.get("key_terms_fr") or [])),
        " ".join(map(str, intent.get("key_terms_en") or [])),
    ])


def _literal_terms(text: Any, limit: int = 18) -> List[str]:
    counts = Counter(_norm(w) for w in _words(text) if _norm(w))
    return [term for term, _ in counts.most_common(max(1, int(limit)))]


def get_cir_domain_profile(domain_detection: Dict[str, Any] | None = None, text: Any = "") -> Dict[str, Any]:
    # Domain metadata stays traceable, but it never injects query vocabulary.
    domain_detection = domain_detection or {}
    return {
        "profile_id": "generic_evidence_driven",
        "base_profile_id": "generic_evidence_driven",
        "label": "Generic evidence-driven retrieval",
        "code1": str(domain_detection.get("code1") or ""),
        "code2": str(domain_detection.get("code2") or ""),
        "code3": str(domain_detection.get("code3") or ""),
        "code4": str(domain_detection.get("code4") or ""),
        "query_seeds": [],
        "positive_terms": _literal_terms(text, 12),
        "negative_terms": [],
        "source_profiles": [],
        "domain_terms": [],
        "nomenclature_coverage": {
            "rows_total": 0,
            "matched_rows": 0,
            "source": "disabled_static_catalog_v159",
        },
        "hardcoded_domain_rules": False,
    }


def build_cir_domain_queries(intent: Dict[str, Any], max_queries: int = 8) -> List[Dict[str, str]]:
    # Compatibility only. Main V159 query generation lives in query_builder.py.
    terms = _literal_terms(_intent_text(intent or {}), 16)
    if not terms:
        return []
    chunks: List[Dict[str, str]] = []
    seen = set()
    for offset in (0, 2, 4):
        query = " ".join(terms[offset:offset + 8]).strip()
        if len(query.split()) < 3 or query in seen:
            continue
        seen.add(query)
        chunks.append({"query": query, "kind": "generic_evidence_terms"})
        if len(chunks) >= max(1, int(max_queries)):
            break
    return chunks


def score_text_against_profile(text: Any, profile: Dict[str, Any]) -> Dict[str, Any]:
    haystack = set(_literal_terms(text, 200))
    positives = [str(x) for x in profile.get("positive_terms") or []]
    matched = [term for term in positives if _norm(term) in haystack]
    denom = max(1, len(positives))
    return {
        "domain_profile_score": round(min(1.0, len(matched) / denom), 4),
        "matched_positive_terms": matched[:8],
        "matched_negative_terms": [],
        "hardcoded_domain_rules": False,
    }
