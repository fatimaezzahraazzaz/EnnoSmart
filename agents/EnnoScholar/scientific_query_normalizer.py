# -*- coding: utf-8 -*-
from __future__ import annotations

"""Generic compatibility normalizer for EnnoScholar.

V159 rule: no domain, client, project, product, acronym or technology catalog is
allowed in the scientific-search path.  Concepts are derived only from the
current lock/evidence and from fields already present in the intent.
"""

from collections import Counter
import re
from typing import Any, Dict, Iterable, List

_GENERIC = {
    "the", "and", "with", "from", "into", "for", "that", "this", "these", "those",
    "dans", "avec", "pour", "sans", "des", "les", "une", "sur", "par", "aux", "est",
    "verrou", "project", "projet", "scientific", "scientifique", "technical", "technique",
    "study", "etude", "étude", "result", "results", "résultat", "résultats",
}


def _norm(text: Any) -> str:
    value = str(text or "").casefold().replace("œ", "oe")
    value = re.sub(r"[^a-z0-9à-ÿ%°µ/._+\- ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _unique(values: Iterable[str], limit: int) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        cleaned = _norm(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _tokens(text: str) -> List[str]:
    return [
        token for token in re.findall(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9%°µ/._+\-]{2,}", str(text or ""))
        if _norm(token) not in _GENERIC
    ]


def _local_names(text: str) -> List[str]:
    # Literal names/acronyms are detected from the evidence itself.  No known-name list.
    values = re.findall(r"\b[A-Z][A-Z0-9_-]{1,}\b|\b[A-Z][a-zA-Z0-9_-]{3,}\b", str(text or ""))
    return _unique(values, 16)


def normalize_scientific_intent(intent: Dict[str, Any], evidence_text: str = "") -> Dict[str, Any]:
    out = dict(intent or {})
    source_basis = out.get("source_basis") if isinstance(out.get("source_basis"), dict) else {}
    evidence = " ".join([
        str(out.get("verrou_title") or out.get("title") or ""),
        str(out.get("scientific_problem") or ""),
        str(evidence_text or source_basis.get("source_text_excerpt") or ""),
        " ".join(map(str, out.get("key_terms_en") or [])),
        " ".join(map(str, out.get("key_terms_fr") or [])),
    ]).strip()

    local_names = _local_names(evidence)
    local_norms = {_norm(name) for name in local_names}
    tokens = [token for token in _tokens(evidence) if _norm(token) not in local_norms]
    counts = Counter(_norm(token) for token in tokens if _norm(token))

    phrases: List[str] = []
    normalized_tokens = [_norm(token) for token in tokens if _norm(token)]
    for width in (3, 2):
        for index in range(max(0, len(normalized_tokens) - width + 1)):
            gram = normalized_tokens[index:index + width]
            if any(token in _GENERIC for token in gram) or len(set(gram)) != width:
                continue
            phrases.append(" ".join(gram))
    phrase_counts = Counter(phrases)

    existing_core = [str(x) for x in out.get("core_concepts") or []]
    inferred = [p for p, _ in phrase_counts.most_common(10)]
    if len(inferred) < 6:
        inferred.extend([token for token, _ in counts.most_common(12)])

    core = _unique(existing_core + inferred, 12)
    out["core_concepts"] = core
    out["primary_core_concepts"] = _unique(out.get("primary_core_concepts") or core[:3], 5)
    out["project_tool_terms"] = local_names
    out["local_names"] = local_names
    out["normalized_research_text_en"] = evidence[:2400]
    out["normalization_report"] = {
        "version": "v159_generic_evidence_only",
        "project_specific_rules": False,
        "domain_dictionary_used": False,
        "local_names_detected_from_evidence": local_names,
        "core_concepts": core,
    }
    return out
