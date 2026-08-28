# -*- coding: utf-8 -*-
from __future__ import annotations

"""
EnnoDiagnostic V200 - Historical Continuity Reconciler.

Goal
----
Keep the first diagnostic pass strictly based on year N, then use the exact CIR
of the same project in N-1 (fallback N-2/N-3) as a *longitudinal control*.

This module can:
- map current lock candidates to historical lock families;
- distinguish continuity / refinement / sub-lock / partial lift / scope extension;
- merge several current technical sub-problems when they are demonstrably the
  continuation of one historical scientific lock family;
- run a targeted gap probe in the CURRENT project's RAG when an N-1 lock seems
  to have disappeared;
- recover a missing candidate only when current-year evidence exists and the
  strict recovery gate is satisfied.

Non-hallucination contract
--------------------------
Historical CIR content is NEVER a factual proof for year N. It may only:
1) suggest a continuity hypothesis;
2) drive a targeted search in current-year sources;
3) help group current-year candidates into one longitudinal family.

Every visible current candidate keeps current-year evidence only. Historical
text is stored under ``historical_continuity`` and is explicitly marked as
non-current evidence.
"""

import hashlib
import json
import math
import os
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


VERSION = "historical_continuity_reconciler_v300"

CURRENT_STATUSES = {
    "continued",
    "refined",
    "sub_lock",
    "partially_lifted",
    "extended_scope",
    "new",
    "uncertain",
}

CONTINUITY_STATUSES = {
    "continued",
    "refined",
    "sub_lock",
    "partially_lifted",
    "extended_scope",
}

STOPWORDS = {
    # FR
    "avec", "dans", "pour", "sans", "sous", "entre", "vers", "chez", "depuis",
    "des", "les", "une", "aux", "sur", "par", "que", "qui", "dont", "plus",
    "moins", "ainsi", "afin", "leur", "leurs", "cette", "ces", "cela", "comme",
    "etre", "sont", "avait", "avoir", "peut", "doit", "projet", "cir", "annee",
    "technique", "scientifique", "travaux", "etude", "analyse", "resultat",
    "resultats", "methode", "methodes", "verrou", "verrous", "incertitude",
    "incertitudes", "difficulte", "difficultes", "systeme", "systemes",
    # EN
    "with", "without", "from", "into", "that", "this", "these", "those", "their",
    "they", "them", "have", "has", "were", "been", "project", "technical",
    "scientific", "result", "results", "method", "methods", "system", "systems",
}

GENERIC_TITLES = {
    "verrou", "verrous", "verrou technique", "verrou scientifique",
    "incertitude", "incertitudes", "resultat", "resultats", "methode", "methodes",
    "contexte", "objectif", "objectifs", "section", "travaux", "analyse",
}

ROLE_ALIASES = {
    "verrou": "verrou",
    "verrou_rnd": "verrou",
    "verrou_scientifique": "verrou",
    "verrou_technologique": "verrou",
    "incertitude": "limite",
    "limite": "limite",
    "methode": "methode",
    "method": "methode",
    "demarche": "methode",
    "resultat": "resultat",
    "result": "resultat",
    "contribution": "contribution",
    "parametre": "parametre",
    "objectif": "objectif",
    "etat_art": "etat_art",
}


@dataclass
class Similarity:
    score: float
    token_jaccard: float
    token_containment: float
    title_jaccard: float
    title_containment: float
    sequence: float
    ngram_overlap: float
    number_overlap: float
    support_bonus: float
    shared_terms: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "token_jaccard": round(self.token_jaccard, 4),
            "token_containment": round(self.token_containment, 4),
            "title_jaccard": round(self.title_jaccard, 4),
            "title_containment": round(self.title_containment, 4),
            "sequence": round(self.sequence, 4),
            "ngram_overlap": round(self.ngram_overlap, 4),
            "number_overlap": round(self.number_overlap, 4),
            "support_bonus": round(self.support_bonus, 4),
            "shared_terms": self.shared_terms[:16],
        }


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'")
    text = re.sub(r"[^a-z0-9%+./_-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _truncate(value: Any, max_chars: int = 900) -> str:
    text = _clean(value)
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    pos = max(cut.rfind("."), cut.rfind(";"), cut.rfind(":"))
    if pos < max_chars // 2:
        pos = max_chars
    return cut[:pos].rstrip() + "..."


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "oui", "on"}


def _stem_token(token: str) -> str:
    token = token.strip().lower()
    # Very small language-agnostic/French-friendly normalization. This is not a
    # linguistic stemmer; it only prevents plural morphology from hiding strong
    # technical anchors such as palier/paliers or raideur/raideurs.
    if len(token) > 6 and token.endswith("es"):
        token = token[:-2]
    elif len(token) > 5 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token


def _tokens(value: Any) -> Set[str]:
    output: Set[str] = set()
    for raw in re.findall(r"[a-z0-9][a-z0-9%+./_-]{2,}", _norm(value)):
        tok = _stem_token(raw)
        if len(tok) >= 4 and tok not in STOPWORDS and not tok.isdigit():
            output.add(tok)
    return output


def _numbers(value: Any) -> Set[str]:
    return set(re.findall(r"\b\d+(?:[.,]\d+)?(?:\s*(?:%|hz|khz|mhz|mm|cm|m|kg|n|nm|bar|pa|kpa|mpa|°c|c|rpm))?\b", _norm(value)))


def _ngrams(value: Any, n: int = 2) -> Set[str]:
    toks = [_stem_token(tok) for tok in _norm(value).split()]
    toks = [tok for tok in toks if len(tok) >= 4 and tok not in STOPWORDS]
    if len(toks) < n:
        return set()
    return {" ".join(toks[i : i + n]) for i in range(len(toks) - n + 1)}


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _containment(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    # Important for N/N-1: a current sub-problem is often a narrow subset of a
    # broad historical lock. Jaccard alone penalizes that valid relationship.
    return len(a & b) / max(1, min(len(a), len(b)))


def _role(value: Mapping[str, Any]) -> str:
    raw = _norm(
        value.get("role")
        or value.get("section_type")
        or value.get("section_key")
        or value.get("pack_key")
    )
    if raw in ROLE_ALIASES:
        return ROLE_ALIASES[raw]
    for key, canonical in ROLE_ALIASES.items():
        if key in raw:
            return canonical
    return raw or "general"


def _source_meta(value: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = value.get("metadata")
    return meta if isinstance(meta, Mapping) else {}


def _source_text(value: Mapping[str, Any]) -> str:
    meta = _source_meta(value)
    return _clean(
        value.get("analysis_text")
        or value.get("text")
        or value.get("source_text")
        or value.get("excerpt")
        or value.get("content")
        or meta.get("analysis_text")
        or meta.get("text")
        or meta.get("source_text")
        or meta.get("excerpt")
    )


def _source_title(value: Mapping[str, Any]) -> str:
    meta = _source_meta(value)
    return _clean(
        value.get("section_title")
        or value.get("title")
        or value.get("label")
        or meta.get("section_title")
        or meta.get("title")
    )


def _source_document(value: Mapping[str, Any]) -> str:
    meta = _source_meta(value)
    return _clean(
        value.get("document")
        or value.get("filename")
        or value.get("document_name")
        or meta.get("document")
        or meta.get("filename")
        or meta.get("document_name")
    )


def _source_path(value: Mapping[str, Any]) -> str:
    meta = _source_meta(value)
    return _clean(
        value.get("source_path")
        or value.get("path")
        or value.get("file_path")
        or meta.get("source_path")
        or meta.get("path")
    )


def _source_identity(value: Mapping[str, Any], prefix: str = "S") -> str:
    meta = _source_meta(value)
    raw = _clean(
        value.get("evidence_id")
        or value.get("passage_id")
        or value.get("rag_chunk_id")
        or value.get("id")
        or meta.get("passage_id")
        or meta.get("rag_chunk_id")
    )
    if raw:
        return raw
    basis = "|".join((_source_document(value), _source_path(value), _source_text(value)[:900]))
    return prefix + hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _historical_title(item: Mapping[str, Any]) -> str:
    title = _clean(item.get("section_title") or item.get("title") or item.get("section_label"))
    if title and _norm(title) not in GENERIC_TITLES and len(title) >= 10:
        return _truncate(title, 220)
    text = _clean(item.get("text") or item.get("source_text"))
    first = re.split(r"(?<=[.!?;])\s+|\n+", text, maxsplit=1)[0]
    first = re.sub(r"^(?:verrou|incertitude|difficulte)\s*\d*\s*[:\-–—]?\s*", "", first, flags=re.I)
    return _truncate(first, 220) if len(_clean(first)) >= 10 else "Verrou historique"


def _current_title(item: Mapping[str, Any]) -> str:
    return _clean(
        item.get("title")
        or item.get("verrou_title")
        or item.get("label")
        or item.get("section_title")
        or "Signal R&D candidat"
    )


def _current_lock_text(item: Mapping[str, Any]) -> str:
    parts: List[str] = []
    for key in (
        "title", "verrou_title", "scientific_uncertainty", "scientific_lock",
        "why_lock", "why_agent_found_verrou", "consultant_explanation",
        "causal_chain", "evidence_summary", "justification", "text",
    ):
        text = _clean(item.get(key))
        if text:
            parts.append(text)
    for source in _collect_current_sources(item)[:10]:
        text = _source_text(source)
        if text:
            parts.append(text)
    return _truncate("\n".join(parts), 4200)


def _walk_sources(value: Any, output: List[Dict[str, Any]], depth: int = 0) -> None:
    if value is None or depth > 5 or len(output) >= 80:
        return
    if isinstance(value, list):
        for item in value:
            _walk_sources(item, output, depth + 1)
        return
    if not isinstance(value, Mapping):
        return

    looks_like_source = bool(
        value.get("document")
        or value.get("source_path")
        or value.get("passage_id")
        or value.get("rag_chunk_id")
        or value.get("excerpt")
    )
    if looks_like_source and _source_text(value):
        output.append(dict(value))

    for key in (
        "source_evidence", "primary_evidence", "supporting_passages", "sources",
        "evidence", "evidences", "evidence_sources", "proofs", "preuves",
    ):
        if key in value:
            _walk_sources(value.get(key), output, depth + 1)


def _collect_current_sources(item: Mapping[str, Any]) -> List[Dict[str, Any]]:
    values: List[Dict[str, Any]] = []
    _walk_sources(item, values)
    seen: Set[str] = set()
    output: List[Dict[str, Any]] = []
    for source in values:
        sid = _source_identity(source)
        signature = sid or (_source_document(source) + "|" + _norm(_source_text(source))[:260])
        if not signature or signature in seen:
            continue
        seen.add(signature)
        output.append(source)
    return output


def _similarity(
    current_text: str,
    historical_text: str,
    *,
    current_title: str = "",
    historical_title: str = "",
    support_texts: Optional[Sequence[str]] = None,
) -> Similarity:
    c_norm, h_norm = _norm(current_text), _norm(historical_text)
    c_tokens, h_tokens = _tokens(c_norm), _tokens(h_norm)
    shared = sorted(c_tokens & h_tokens, key=lambda x: (-len(x), x))
    token_j = _jaccard(c_tokens, h_tokens)
    token_containment = _containment(c_tokens, h_tokens)
    current_title_tokens = _tokens(current_title)
    historical_title_tokens = _tokens(historical_title)
    title_j = _jaccard(current_title_tokens, historical_title_tokens)
    title_containment = _containment(current_title_tokens, historical_title_tokens)
    seq = SequenceMatcher(None, c_norm[:3200], h_norm[:3200]).ratio() if c_norm and h_norm else 0.0
    bigram = _jaccard(_ngrams(c_norm, 2), _ngrams(h_norm, 2))
    number = _jaccard(_numbers(c_norm), _numbers(h_norm))

    support_bonus = 0.0
    for support in support_texts or []:
        s_tokens = _tokens(support)
        if not s_tokens:
            continue
        support_bonus = max(support_bonus, _jaccard(c_tokens, s_tokens))

    score = (
        0.20 * token_j
        + 0.28 * token_containment
        + 0.14 * seq
        + 0.10 * title_j
        + 0.12 * title_containment
        + 0.06 * bigram
        + 0.02 * number
        + 0.08 * support_bonus
    )

    # Direct lexical anchors are useful for technical names / acronyms.
    if len(shared) >= 5:
        score += 0.05
    elif len(shared) >= 3:
        score += 0.025

    score = max(0.0, min(1.0, score))
    return Similarity(
        score=score,
        token_jaccard=token_j,
        token_containment=token_containment,
        title_jaccard=title_j,
        title_containment=title_containment,
        sequence=seq,
        ngram_overlap=bigram,
        number_overlap=number,
        support_bonus=support_bonus,
        shared_terms=shared,
    )


def _dedupe_historical_items(items: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[Tuple[str, str, str]] = set()
    output: List[Dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        text = _clean(item.get("text") or item.get("source_text"))
        if len(text) < 25:
            continue
        signature = (
            _role(item),
            _source_document(item).lower(),
            _norm(text)[:420],
        )
        if signature in seen:
            continue
        seen.add(signature)
        output.append(item)
    return output


def _historical_family_id(year: str, item: Mapping[str, Any]) -> str:
    basis = "|".join((
        str(year),
        _clean(item.get("id") or item.get("passage_id")),
        _historical_title(item),
        _clean(item.get("text"))[:1200],
    ))
    return "HF-" + hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _build_historical_families_v200(
    previous_years: Sequence[str],
    previous_items: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    items = _dedupe_historical_items(previous_items)
    lock_items = [item for item in items if _role(item) == "verrou"]
    if not lock_items:
        # Conservative fallback: a prior "limite" can seed a family, but is
        # explicitly marked as such and receives a lower matching priority.
        lock_items = [item for item in items if _role(item) == "limite"]

    year = str(previous_years[0]) if previous_years else "N-1"
    supports_by_role: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        supports_by_role.setdefault(_role(item), []).append(item)

    families: List[Dict[str, Any]] = []
    for lock in lock_items:
        lock_text = _clean(lock.get("text") or lock.get("source_text"))
        title = _historical_title(lock)
        document = _source_document(lock)
        support_payload: Dict[str, List[Dict[str, Any]]] = {}
        for role in ("methode", "resultat", "limite", "contribution", "parametre", "objectif"):
            scored: List[Tuple[float, Dict[str, Any]]] = []
            for candidate in supports_by_role.get(role, []):
                if candidate is lock:
                    continue
                candidate_text = _clean(candidate.get("text") or candidate.get("source_text"))
                sim = _similarity(
                    lock_text,
                    candidate_text,
                    current_title=title,
                    historical_title=_historical_title(candidate),
                ).score
                if document and _source_document(candidate) == document:
                    sim += 0.06
                if sim >= 0.10:
                    scored.append((sim, candidate))
            selected = []
            for score, candidate in sorted(scored, key=lambda pair: pair[0], reverse=True)[:3]:
                selected.append({
                    "role": role,
                    "score_to_lock": round(min(1.0, score), 4),
                    "section_title": _source_title(candidate),
                    "document": _source_document(candidate),
                    "source_path": _source_path(candidate),
                    "text": _truncate(candidate.get("text") or candidate.get("source_text"), 900),
                    "previous_year": _clean(candidate.get("previous_year") or candidate.get("year") or year),
                })
            support_payload[role] = selected

        families.append({
            "family_id": _historical_family_id(year, lock),
            "previous_year": _clean(lock.get("previous_year") or lock.get("year") or year),
            "title": title,
            "text": _truncate(lock_text, 1800),
            "document": document,
            "source_path": _source_path(lock),
            "role": _role(lock),
            "seed_is_explicit_verrou": _role(lock) == "verrou",
            "support": support_payload,
            "history_is_current_proof": False,
        })

    # Deduplicate near-identical historical lock segments while preserving all
    # support. This is not current-year regrouping; it only prevents a long CIR
    # section split into overlapping windows from becoming several families.
    deduped: List[Dict[str, Any]] = []
    for family in families:
        duplicate_index: Optional[int] = None
        for idx, existing in enumerate(deduped):
            sim = _similarity(
                family["text"],
                existing["text"],
                current_title=family["title"],
                historical_title=existing["title"],
            )
            if sim.score >= 0.78 or (
                sim.title_jaccard >= 0.66 and sim.token_jaccard >= 0.48
            ):
                duplicate_index = idx
                break
        if duplicate_index is None:
            deduped.append(family)
            continue
        target = deduped[duplicate_index]
        target.setdefault("merged_historical_family_ids", []).append(family["family_id"])
        for role, values in (family.get("support") or {}).items():
            merged = list((target.get("support") or {}).get(role) or []) + list(values or [])
            seen_support: Set[str] = set()
            unique_support: List[Dict[str, Any]] = []
            for value in merged:
                sig = _norm(value.get("text"))[:500]
                if not sig or sig in seen_support:
                    continue
                seen_support.add(sig)
                unique_support.append(value)
            target.setdefault("support", {})[role] = unique_support[:5]
    return deduped


def _family_support_texts(family: Mapping[str, Any]) -> List[str]:
    texts: List[str] = []
    for values in (family.get("support") or {}).values():
        for value in values or []:
            if isinstance(value, Mapping) and _clean(value.get("text")):
                texts.append(_clean(value.get("text")))
    return texts


def _current_support_for_lock(
    lock: Mapping[str, Any],
    current_sections: Mapping[str, Sequence[Mapping[str, Any]]],
    max_per_role: int = 2,
) -> Dict[str, List[Dict[str, Any]]]:
    lock_text = _current_lock_text(lock)
    mapping = {
        "methode": ("methodes",),
        "resultat": ("resultats",),
        "limite": ("limites", "verrou_support_context"),
        "parametre": ("parametres",),
        "contribution": ("contributions",),
    }
    output: Dict[str, List[Dict[str, Any]]] = {}
    for role, section_keys in mapping.items():
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for section_key in section_keys:
            for raw in current_sections.get(section_key) or []:
                if not isinstance(raw, Mapping):
                    continue
                text = _source_text(raw)
                if not text:
                    continue
                sim = _similarity(lock_text, text, current_title=_current_title(lock), historical_title=_source_title(raw))
                if sim.score >= 0.08:
                    scored.append((sim.score, dict(raw)))
        selected: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for score, source in sorted(scored, key=lambda pair: pair[0], reverse=True):
            sid = _source_identity(source)
            if sid in seen:
                continue
            seen.add(sid)
            selected.append({
                "evidence_id": sid,
                "role": role,
                "score_to_current_lock": round(score, 4),
                "document": _source_document(source),
                "source_path": _source_path(source),
                "section_title": _source_title(source),
                "text": _truncate(_source_text(source), 700),
            })
            if len(selected) >= max_per_role:
                break
        output[role] = selected
    return output


def _candidate_matrix(
    current_verrous: Sequence[Mapping[str, Any]],
    families: Sequence[Mapping[str, Any]],
    current_sections: Mapping[str, Sequence[Mapping[str, Any]]],
    top_k: int = 3,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, List[Dict[str, Any]]]]]:
    rows: List[Dict[str, Any]] = []
    current_support: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for index, lock in enumerate(current_verrous, start=1):
        cid = _clean(lock.get("continuity_current_id")) or f"C{index}"
        lock_text = _current_lock_text(lock)
        support = _current_support_for_lock(lock, current_sections)
        current_support[cid] = support
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for family in families:
            sim = _similarity(
                lock_text,
                _clean(family.get("text")),
                current_title=_current_title(lock),
                historical_title=_clean(family.get("title")),
                support_texts=_family_support_texts(family),
            )
            scored.append((sim.score, {
                "family_id": family.get("family_id"),
                "previous_year": family.get("previous_year"),
                "title": family.get("title"),
                "document": family.get("document"),
                "historical_excerpt": _truncate(family.get("text"), 850),
                "similarity": sim.as_dict(),
            }))
        candidates = [item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:top_k]]
        rows.append({
            "current_id": cid,
            "current_index": index - 1,
            "title": _current_title(lock),
            "current_text": _truncate(lock_text, 1400),
            "current_support": support,
            "candidates": candidates,
        })
    return rows, current_support


def _gap_probe(
    family: Mapping[str, Any],
    search_current: Optional[Callable[..., List[Dict[str, Any]]]],
    top_k: int = 10,
) -> Dict[str, Any]:
    if search_current is None:
        return {
            "family_id": family.get("family_id"),
            "query": "",
            "evidence": [],
            "search_available": False,
        }

    query = _truncate(" ".join([
        _clean(family.get("title")),
        _clean(family.get("text")),
    ]), 1000)
    try:
        try:
            raw_sources = search_current(role=None, query=query, top_k=top_k)
        except TypeError:
            raw_sources = search_current(None, query, top_k)
    except Exception as exc:
        return {
            "family_id": family.get("family_id"),
            "query": query,
            "evidence": [],
            "search_available": False,
            "error": str(exc),
        }

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for source in raw_sources or []:
        if not isinstance(source, Mapping):
            continue
        text = _source_text(source)
        if not text:
            continue
        sim = _similarity(
            text,
            _clean(family.get("text")),
            current_title=_source_title(source),
            historical_title=_clean(family.get("title")),
            support_texts=_family_support_texts(family),
        )
        meta = _source_meta(source)
        role = _norm(meta.get("role") or source.get("role"))
        scored.append((sim.score, {
            "evidence_id": _source_identity(source, prefix="G"),
            "role": role,
            "document": _source_document(source),
            "source_path": _source_path(source),
            "section_title": _source_title(source),
            "text": _truncate(text, 900),
            "similarity": sim.as_dict(),
            "frascati_score": meta.get("frascati_score") or meta.get("verrou_score"),
            "lock_group_id": _clean(meta.get("lock_group_id") or source.get("lock_group_id")),
            "raw_source": dict(source),
        }))

    evidence = [item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:top_k]]
    return {
        "family_id": family.get("family_id"),
        "query": query,
        "evidence": evidence,
        "search_available": True,
    }


def _extract_json_object(text: Any) -> Optional[Dict[str, Any]]:
    raw = _clean(text)
    if not raw:
        return None
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except Exception:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(raw[start : end + 1])
            return value if isinstance(value, dict) else None
        except Exception:
            return None
    return None


def _llm_adjudicate_v200(
    llm: Any,
    candidate_rows: Sequence[Mapping[str, Any]],
    gap_probes: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    if llm is None or not _bool_env("ENNOSMART_HISTORICAL_RECONCILIATION_USE_LLM", True):
        return {"ok": False, "used": False, "reason": "llm_disabled"}

    compact_gaps = []
    for gap in gap_probes:
        compact_gaps.append({
            "previous_family_id": gap.get("family_id"),
            "evidence": [
                {
                    "evidence_id": e.get("evidence_id"),
                    "role": e.get("role"),
                    "document": e.get("document"),
                    "section_title": e.get("section_title"),
                    "text": _truncate(e.get("text"), 600),
                    "similarity_score": (e.get("similarity") or {}).get("score"),
                }
                for e in (gap.get("evidence") or [])[:5]
            ],
        })

    prompt = f"""
Tu es le module de réconciliation longitudinale d'EnnoDiagnostic.

OBJECTIF
Le diagnostic de l'année N a déjà été réalisé SANS utiliser le CIR précédent.
Tu dois maintenant contrôler la continuité scientifique avec N-1 sans contaminer
les faits courants.

REGLE ABSOLUE DE PREUVE
- Les textes historiques N-1 servent uniquement de contexte de continuité.
- Ils ne sont JAMAIS une preuve factuelle de l'année N.
- Toute affirmation sur N doit être supportée par current_text/current_support ou
  par une evidence du gap probe, toutes issues du projet courant.
- Ne crée aucun objet, méthode, résultat, chiffre ou conclusion absent des preuves N.

STATUTS AUTORISES pour chaque candidat courant
- continued: même verrou scientifique de fond, toujours présent.
- refined: même verrou, mais caractérisé plus finement en N.
- sub_lock: sous-problème technique N d'un verrou principal historique.
- partially_lifted: certaines incertitudes sont levées par des résultats N mais une
  partie du verrou persiste.
- extended_scope: même verrou étendu à une nouvelle configuration / technologie /
  domaine de validation.
- new: verrou réellement nouveau par rapport aux familles historiques fournies.
- uncertain: correspondance insuffisante.

IMPORTANT POUR LE REGROUPEMENT
Si plusieurs candidats N sont seulement des sous-problèmes / variantes / nouvelles
configurations du MEME verrou historique principal, donne le même
previous_family_id. Ne fusionne pas deux mécanismes scientifiques distincts juste
parce qu'ils appartiennent au même projet.

GAP PROBE
Pour une famille historique apparemment absente, recover=true uniquement si les
preuves COURANTES fournies montrent réellement une persistance ou une investigation
liée. Le texte N-1 seul ne suffit jamais.

CANDIDATS COURANTS ET TOP FAMILLES HISTORIQUES
{json.dumps(list(candidate_rows), ensure_ascii=False, indent=2)}

GAP PROBES DANS LES SOURCES COURANTES
{json.dumps(compact_gaps, ensure_ascii=False, indent=2)}

Réponds UNIQUEMENT avec ce JSON :
{{
  "current_decisions": [
    {{
      "current_id": "C1",
      "status": "continued|refined|sub_lock|partially_lifted|extended_scope|new|uncertain",
      "previous_family_id": "HF-... ou null",
      "confidence": 0.0,
      "reason": "raison courte fondee uniquement sur les donnees fournies"
    }}
  ],
  "gap_decisions": [
    {{
      "previous_family_id": "HF-...",
      "recover": false,
      "confidence": 0.0,
      "current_evidence_ids": ["..."],
      "reason": "raison courte"
    }}
  ]
}}
""".strip()

    try:
        kwargs = {
            "temperature": float(os.getenv("ENNOSMART_HISTORICAL_RECONCILIATION_TEMPERATURE", "0.02")),
            "max_output_tokens": int(os.getenv("ENNOSMART_HISTORICAL_RECONCILIATION_MAX_TOKENS", "2200")),
            "retries": int(os.getenv("ENNOSMART_HISTORICAL_RECONCILIATION_RETRIES", "1")),
        }
        try:
            raw = llm.generate(prompt, request_name="ennodiagnostic:historical_continuity", **kwargs)
        except TypeError:
            raw = llm.generate(prompt, **kwargs)
        parsed = _extract_json_object(raw)
        if not parsed:
            return {
                "ok": False,
                "used": True,
                "error": "invalid_json",
                "raw_preview": _truncate(raw, 700),
                "prompt_chars": len(prompt),
            }
        return {
            "ok": True,
            "used": True,
            "data": parsed,
            "prompt_chars": len(prompt),
        }
    except Exception as exc:
        return {"ok": False, "used": True, "error": str(exc), "prompt_chars": len(prompt)}


def _deterministic_decision(row: Mapping[str, Any]) -> Dict[str, Any]:
    candidates = list(row.get("candidates") or [])
    best = candidates[0] if candidates else {}
    score = _float((best.get("similarity") or {}).get("score"))
    if score >= 0.62:
        status = "continued"
        confidence = min(0.92, 0.58 + score * 0.45)
    elif score >= 0.48:
        status = "refined"
        confidence = min(0.84, 0.48 + score * 0.45)
    elif score >= 0.33:
        status = "uncertain"
        confidence = min(0.68, 0.38 + score * 0.40)
    else:
        status = "new"
        confidence = min(0.80, 0.55 + (0.33 - score) * 0.25)
    return {
        "current_id": row.get("current_id"),
        "status": status,
        "previous_family_id": best.get("family_id") if status in CONTINUITY_STATUSES | {"uncertain"} else None,
        "confidence": round(confidence, 4),
        "reason": "deterministic_similarity_fallback",
        "decision_source": "deterministic",
    }


def _validated_current_decisions(
    candidate_rows: Sequence[Mapping[str, Any]],
    llm_report: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    by_id = {str(row.get("current_id")): row for row in candidate_rows}
    llm_values: Dict[str, Dict[str, Any]] = {}
    if llm_report.get("ok"):
        data = llm_report.get("data") if isinstance(llm_report.get("data"), Mapping) else {}
        for raw in data.get("current_decisions") or []:
            if not isinstance(raw, Mapping):
                continue
            cid = _clean(raw.get("current_id"))
            if cid in by_id:
                llm_values[cid] = dict(raw)

    output: List[Dict[str, Any]] = []
    for row in candidate_rows:
        cid = str(row.get("current_id"))
        fallback = _deterministic_decision(row)
        raw = llm_values.get(cid)
        if not raw:
            output.append(fallback)
            continue

        status = _clean(raw.get("status")).lower()
        if status not in CURRENT_STATUSES:
            output.append(fallback)
            continue
        confidence = max(0.0, min(1.0, _float(raw.get("confidence"))))
        family_id = _clean(raw.get("previous_family_id")) or None
        valid_families = {str(c.get("family_id")) for c in row.get("candidates") or []}
        if status in CONTINUITY_STATUSES and family_id not in valid_families:
            output.append(fallback)
            continue
        if status in {"new", "uncertain"} and family_id and family_id not in valid_families:
            family_id = None
        output.append({
            "current_id": cid,
            "status": status,
            "previous_family_id": family_id if status != "new" else None,
            "confidence": round(confidence, 4),
            "reason": _truncate(raw.get("reason"), 500),
            "decision_source": "llm",
        })
    return output


def _validated_gap_decisions(
    gap_probes: Sequence[Mapping[str, Any]],
    llm_report: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    valid_ids = {str(gap.get("family_id")) for gap in gap_probes}
    evidence_by_family = {
        str(gap.get("family_id")): {str(e.get("evidence_id")) for e in gap.get("evidence") or []}
        for gap in gap_probes
    }
    output: Dict[str, Dict[str, Any]] = {}
    if not llm_report.get("ok"):
        return output
    data = llm_report.get("data") if isinstance(llm_report.get("data"), Mapping) else {}
    for raw in data.get("gap_decisions") or []:
        if not isinstance(raw, Mapping):
            continue
        family_id = _clean(raw.get("previous_family_id"))
        if family_id not in valid_ids:
            continue
        ids = [
            _clean(value)
            for value in (raw.get("current_evidence_ids") or [])
            if _clean(value) in evidence_by_family.get(family_id, set())
        ]
        output[family_id] = {
            "recover": bool(raw.get("recover")),
            "confidence": max(0.0, min(1.0, _float(raw.get("confidence")))),
            "current_evidence_ids": ids,
            "reason": _truncate(raw.get("reason"), 500),
            "decision_source": "llm",
        }
    return output


def _family_by_id(families: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(family.get("family_id")): dict(family) for family in families}


def _candidate_similarity_for_family(row: Mapping[str, Any], family_id: str) -> float:
    for candidate in row.get("candidates") or []:
        if str(candidate.get("family_id")) == str(family_id):
            return _float((candidate.get("similarity") or {}).get("score"))
    return 0.0


def _dedupe_scalar_list(values: Iterable[Any]) -> List[Any]:
    output: List[Any] = []
    seen: Set[str] = set()
    for value in values:
        if value in (None, "", []):
            continue
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _merge_sources(items: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in items:
        for source in _collect_current_sources(item):
            sid = _source_identity(source)
            if sid in seen:
                continue
            seen.add(sid)
            sources.append(source)
    return sources


def _choose_representative(items: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    def score(item: Mapping[str, Any]) -> float:
        evidence_count = len(_collect_current_sources(item))
        frascati = _float(item.get("frascati_score") or item.get("score"))
        title_quality = min(len(_current_title(item)), 180) / 180.0
        body = len(_current_lock_text(item))
        return evidence_count * 3.0 + frascati * 2.0 + title_quality + min(body, 2500) / 2500.0

    best = max(items, key=score)
    return deepcopy(dict(best))


def _aggregate_status(statuses: Sequence[str]) -> str:
    priority = ["partially_lifted", "extended_scope", "refined", "sub_lock", "continued"]
    for status in priority:
        if status in statuses:
            return status
    return statuses[0] if statuses else "continued"


def _annotate_single(
    item: Mapping[str, Any],
    decision: Mapping[str, Any],
    family: Optional[Mapping[str, Any]],
    similarity_score: float,
) -> Dict[str, Any]:
    out = deepcopy(dict(item))
    if family and decision.get("status") in CONTINUITY_STATUSES:
        out["historical_continuity"] = {
            "version": VERSION,
            "status": decision.get("status"),
            "confidence": decision.get("confidence"),
            "decision_source": decision.get("decision_source"),
            "reason": decision.get("reason"),
            "similarity_score": round(similarity_score, 4),
            "previous_year": family.get("previous_year"),
            "previous_family_id": family.get("family_id"),
            "historical_family_title": family.get("title"),
            "historical_excerpt": family.get("text"),
            "historical_document": family.get("document"),
            "historical_story": family.get("support"),
            "history_is_current_proof": False,
            "usage": "continuity_control_only_current_evidence_required",
        }
    elif decision.get("status") in {"new", "uncertain"}:
        out["historical_continuity"] = {
            "version": VERSION,
            "status": decision.get("status"),
            "confidence": decision.get("confidence"),
            "decision_source": decision.get("decision_source"),
            "reason": decision.get("reason"),
            "similarity_score": round(similarity_score, 4),
            "previous_family_id": family.get("family_id") if family else None,
            "historical_family_title": family.get("title") if family else None,
            "history_is_current_proof": False,
            "usage": "continuity_control_only_current_evidence_required",
        }
    return out


def _merge_family_group_v200(
    items: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    family: Mapping[str, Any],
    similarities: Sequence[float],
) -> Dict[str, Any]:
    representative = _choose_representative(items)
    titles = _dedupe_scalar_list(_current_title(item) for item in items)
    scores = [
        _float(item.get("frascati_score") or item.get("score"))
        for item in items
        if item.get("frascati_score") not in (None, "") or item.get("score") not in (None, "")
    ]
    statuses = [str(decision.get("status")) for decision in decisions]
    avg_conf = sum(_float(d.get("confidence")) for d in decisions) / max(1, len(decisions))
    avg_similarity = sum(similarities) / max(1, len(similarities))

    all_group_ids: List[Any] = []
    all_original_ids: List[Any] = []
    all_assessments: List[Any] = []
    for item in items:
        for key in ("member_group_ids", "original_nlp_group_ids"):
            value = item.get(key)
            if isinstance(value, list):
                all_group_ids.extend(value)
            elif value not in (None, ""):
                all_group_ids.append(value)
        value = item.get("original_nlp_group_ids")
        if isinstance(value, list):
            all_original_ids.extend(value)
        assessments = item.get("frascati_group_assessments")
        if isinstance(assessments, list):
            all_assessments.extend(assessments)

    merged_sources = _merge_sources(items)
    representative["member_group_ids"] = _dedupe_scalar_list(all_group_ids)
    representative["original_nlp_group_ids"] = _dedupe_scalar_list(all_original_ids or all_group_ids)
    if all_assessments:
        representative["frascati_group_assessments"] = _dedupe_scalar_list(all_assessments)
    if scores:
        representative["frascati_component_scores"] = scores
        representative["score"] = round(sum(scores) / len(scores), 4)
        representative["frascati_score"] = representative["score"]
        representative["frascati_score_source"] = "mean_of_historical_family_member_groups"

    representative["source_evidence"] = merged_sources
    representative["supporting_passages"] = merged_sources
    representative["subproblems_current"] = titles
    representative["historical_reconciliation"] = "merged_current_candidates_by_same_previous_lock_family"
    representative["historical_continuity"] = {
        "version": VERSION,
        "status": _aggregate_status(statuses),
        "confidence": round(avg_conf, 4),
        "similarity_score": round(avg_similarity, 4),
        "decision_source": "mixed" if len({d.get('decision_source') for d in decisions}) > 1 else decisions[0].get("decision_source"),
        "previous_year": family.get("previous_year"),
        "previous_family_id": family.get("family_id"),
        "historical_family_title": family.get("title"),
        "historical_excerpt": family.get("text"),
        "historical_document": family.get("document"),
        "historical_story": family.get("support"),
        "current_member_titles": titles,
        "history_is_current_proof": False,
        "usage": "continuity_control_only_current_evidence_required",
    }
    return representative


def _can_merge_group_v200(
    rows: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    current_items: Sequence[Mapping[str, Any]],
    family_id: str,
) -> bool:
    if len(decisions) < 2:
        return False
    min_conf = min(_float(d.get("confidence")) for d in decisions)
    if min_conf < _float(os.getenv("ENNOSMART_HISTORICAL_MERGE_MIN_CONFIDENCE", "0.66"), 0.66):
        return False
    if any(str(d.get("status")) not in CONTINUITY_STATUSES for d in decisions):
        return False

    scores = [_candidate_similarity_for_family(row, family_id) for row in rows]
    if all(score >= 0.56 for score in scores):
        return True

    # LLM may correctly identify several different technical objects as sub-locks
    # of one broad historical scientific family. A narrow sub-lock can have a low
    # global similarity score even when it shares highly discriminating anchors
    # (e.g. the exact component and stiffness concept). Therefore the safety gate
    # accepts a high-confidence LLM mapping only when EACH current item still has
    # at least two direct lexical anchors with that exact historical family.
    llm_decided = all(d.get("decision_source") == "llm" for d in decisions)
    direct_anchor_ok = True
    for row in rows:
        candidate = next(
            (c for c in (row.get("candidates") or []) if str(c.get("family_id")) == str(family_id)),
            None,
        )
        shared_terms = ((candidate or {}).get("similarity") or {}).get("shared_terms") or []
        similarity = _float(((candidate or {}).get("similarity") or {}).get("score"))
        if len(shared_terms) < 2 or similarity < 0.05:
            direct_anchor_ok = False
            break
    return llm_decided and direct_anchor_ok


def _strict_gap_recovery_gate(
    gap: Mapping[str, Any],
    decision: Optional[Mapping[str, Any]],
) -> Tuple[bool, List[Dict[str, Any]], str]:
    if not decision or not decision.get("recover"):
        return False, [], "llm_did_not_confirm_recovery"
    if _float(decision.get("confidence")) < _float(os.getenv("ENNOSMART_HISTORICAL_GAP_MIN_CONFIDENCE", "0.76"), 0.76):
        return False, [], "gap_confidence_below_threshold"

    evidence = [e for e in gap.get("evidence") or [] if isinstance(e, Mapping)]
    requested = set(decision.get("current_evidence_ids") or [])
    if requested:
        evidence = [e for e in evidence if e.get("evidence_id") in requested]
    strong = [e for e in evidence if _float((e.get("similarity") or {}).get("score")) >= 0.28]
    very_strong = [e for e in evidence if _float((e.get("similarity") or {}).get("score")) >= 0.42]
    role_strong = [
        e for e in evidence
        if _float((e.get("similarity") or {}).get("score")) >= 0.32
        and any(marker in _norm(e.get("role")) for marker in ("verrou", "limite", "incertitude"))
    ]
    if len(strong) >= 2 or very_strong or role_strong:
        selected = (very_strong or role_strong or strong)[:5]
        return True, [dict(e) for e in selected], "strict_current_evidence_gate_passed"
    return False, [], "insufficient_current_year_evidence"


def _recovered_gap_candidate(
    family: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    decision: Mapping[str, Any],
) -> Dict[str, Any]:
    raw_sources = [dict(e.get("raw_source") or {}) for e in evidence if isinstance(e.get("raw_source"), Mapping)]
    current_section_titles = [
        _clean(e.get("section_title"))
        for e in evidence
        if _clean(e.get("section_title")) and _norm(e.get("section_title")) not in GENERIC_TITLES
    ]
    if current_section_titles:
        title = _truncate(current_section_titles[0], 220)
    else:
        title = "Persistance à vérifier : " + _truncate(family.get("title"), 180)

    group_ids = _dedupe_scalar_list(e.get("lock_group_id") for e in evidence if e.get("lock_group_id"))
    documents = _dedupe_scalar_list(e.get("document") for e in evidence if e.get("document"))
    source_summary = " ".join(_truncate(e.get("text"), 500) for e in evidence[:3])

    return {
        "title": title,
        "scientific_uncertainty": (
            "Persistance potentielle d'une incertitude documentée en N-1, retrouvée par contrôle "
            "historique dans des preuves de l'année courante. Validation consultant requise."
        ),
        "why_lock": (
            "Candidat récupéré par le gap probe historique. Le CIR antérieur a seulement déclenché "
            "la recherche ; les preuves factuelles attachées à ce candidat proviennent de l'année courante."
        ),
        "evidence_summary": _truncate(source_summary, 1500),
        "source_evidence": raw_sources,
        "supporting_passages": raw_sources,
        "member_group_ids": group_ids,
        "original_nlp_group_ids": group_ids,
        "document": "; ".join(str(value) for value in documents),
        "display_as_lock": True,
        "consultant_status": "historical_gap_recovered_to_validate",
        "candidate_origin": "historical_gap_probe_current_year_evidence",
        "historical_gap_recovered": True,
        "historical_continuity": {
            "version": VERSION,
            "status": "continued_to_confirm",
            "confidence": round(_float(decision.get("confidence")), 4),
            "decision_source": decision.get("decision_source"),
            "reason": decision.get("reason"),
            "previous_year": family.get("previous_year"),
            "previous_family_id": family.get("family_id"),
            "historical_family_title": family.get("title"),
            "historical_excerpt": family.get("text"),
            "historical_story": family.get("support"),
            "history_is_current_proof": False,
            "usage": "gap_search_trigger_only_current_evidence_required",
        },
    }


def _write_report(output_dir: Optional[str | Path], report: Mapping[str, Any]) -> Optional[str]:
    if output_dir in (None, ""):
        return None
    try:
        path = Path(output_dir) / "historical_continuity_report_v300.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        serializable = json.loads(json.dumps(report, ensure_ascii=False, default=str))
        path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    except Exception:
        return None


def _historical_seed_quality_v300(item: Mapping[str, Any]) -> Tuple[float, List[str]]:
    role = _role(item)
    title = _norm(_historical_title(item))
    text = _norm(item.get("text") or item.get("source_text"))
    combined = f"{title} {text}"
    score = 0.45 if role == "verrou" else (0.22 if role == "limite" else 0.0)
    reasons = [f"role={role}"]
    uncertainty = (
        "incert", "non maitr", "diffic", "impossib", "insuffis", "non conclu",
        "reste a", "recalage", "representativ", "variabil", "fiabil", "generalisation",
    )
    descriptive = (
        "caracterisation", "description", "presentation", "figure", "sommaire",
        "contexte", "materiel",
    )
    method = ("essai", "analyse modale", "methodologie", "protocole", "campagne", "mesure")
    hits = sum(1 for x in uncertainty if x in combined)
    score += min(0.42, 0.10 * hits)
    if hits:
        reasons.append(f"uncertainty={hits}")
    dh = sum(1 for x in descriptive if x in title)
    score -= min(0.35, 0.15 * dh)
    if dh:
        reasons.append(f"descriptive={dh}")
    mh = sum(1 for x in method if x in title)
    if mh and hits == 0:
        score -= 0.28
        reasons.append("method_only_title")
    return max(0.0, min(1.0, score)), reasons


def _build_historical_families(previous_years, previous_items):
    base = _build_historical_families_v200(previous_years, previous_items)
    kept = []
    for family in base:
        quality, reasons = _historical_seed_quality_v300(family)
        family = deepcopy(dict(family))
        family["seed_quality_v300"] = round(quality, 4)
        family["seed_quality_reasons_v300"] = reasons
        if quality >= 0.42:
            kept.append(family)
    if kept:
        return kept
    if base:
        best = max(base, key=lambda f: _historical_seed_quality_v300(f)[0])
        best = deepcopy(dict(best))
        best["seed_quality_v300"] = _historical_seed_quality_v300(best)[0]
        best["historical_seed_fallback"] = True
        return [best]
    return []


def _reconstruct_historical_families_with_llm_v300(llm, families):
    base = [deepcopy(dict(f)) for f in families]
    if not base or llm is None or not _bool_env("ENNOSMART_HISTORICAL_FAMILY_RECONSTRUCTION_USE_LLM", True):
        return base, {"used": False, "reason": "disabled_or_empty"}

    compact = []
    for family in base:
        support = []
        for role, values in (family.get("support") or {}).items():
            for value in (values or [])[:3]:
                support.append({
                    "role": role,
                    "section_title": value.get("section_title"),
                    "text": _truncate(value.get("text"), 450),
                })
        compact.append({
            "family_id": family.get("family_id"),
            "seed_title": family.get("title"),
            "seed_text": _truncate(family.get("text"), 850),
            "support": support[:12],
        })

    prompt = (
        "Tu reconstruis uniquement les familles scientifiques du CIR N-1. "
        "Un titre descriptif (caractérisation, essais, matériel) n'est pas le nom d'un verrou. "
        "Formule le verrou de fond à partir des incertitudes, limites, démarches et résultats fournis. "
        "Aucun fait nouveau. Tu peux fusionner des family_id si c'est clairement le même verrou.\n\n"
        + json.dumps(compact, ensure_ascii=False, indent=2)
        + "\n\nJSON uniquement: "
        + '{"families":[{"member_family_ids":["HF-..."],"canonical_title":"...",'
          '"historical_uncertainty":"...","remaining_uncertainties":["..."],"confidence":0.0}]}'
    )

    try:
        try:
            raw = llm.generate(
                prompt,
                request_name="ennodiagnostic:historical_family_reconstruction_v300",
                temperature=0.01,
                max_output_tokens=1800,
                retries=1,
            )
        except TypeError:
            raw = llm.generate(prompt, temperature=0.01, max_output_tokens=1800, retries=1)
        data = _extract_json_object(raw)
    except Exception as exc:
        return base, {"used": True, "ok": False, "error": str(exc)}

    if not data:
        return base, {"used": True, "ok": False, "reason": "invalid_json"}

    by_id = {str(f.get("family_id")): f for f in base}
    consumed = set()
    rebuilt = []
    for row in data.get("families") or []:
        if not isinstance(row, Mapping):
            continue
        ids = [str(x) for x in row.get("member_family_ids") or [] if str(x) in by_id]
        ids = list(dict.fromkeys(ids))
        title = _clean(row.get("canonical_title"))
        confidence = max(0.0, min(1.0, _float(row.get("confidence"))))
        if not ids or len(title) < 12 or confidence < 0.60:
            continue

        anchor = " ".join(
            str(by_id[fid].get("title") or "") + " " + str(by_id[fid].get("text") or "")
            for fid in ids
        )
        title_tokens = _tokens(title)
        anchor_tokens = _tokens(anchor)
        if title_tokens and len(title_tokens & anchor_tokens) / max(1, len(title_tokens)) < 0.45:
            continue

        representative = deepcopy(max(
            (by_id[fid] for fid in ids),
            key=lambda f: _float(f.get("seed_quality_v300")),
        ))
        representative["family_id"] = "HF3-" + hashlib.sha1(
            "|".join(sorted(ids)).encode("utf-8")
        ).hexdigest()[:12]
        representative["canonical_member_family_ids"] = ids
        representative["original_seed_title"] = representative.get("title")
        representative["title"] = _truncate(title, 240)
        representative["canonical_historical_uncertainty"] = _truncate(
            row.get("historical_uncertainty"), 1000
        )
        representative["remaining_uncertainties"] = [
            _truncate(x, 350)
            for x in row.get("remaining_uncertainties") or []
            if _clean(x)
        ][:8]
        representative["family_reconstruction_confidence"] = round(confidence, 4)
        representative["family_reconstruction_source"] = "llm_n_minus_1_only"

        merged_support = {}
        for fid in ids:
            for role, values in (by_id[fid].get("support") or {}).items():
                merged_support.setdefault(role, []).extend(deepcopy(list(values or [])))
        representative["support"] = {
            role: values[:8] for role, values in merged_support.items()
        }
        rebuilt.append(representative)
        consumed.update(ids)

    for fid, family in by_id.items():
        if fid not in consumed:
            rebuilt.append(deepcopy(family))

    return rebuilt, {
        "used": True,
        "ok": True,
        "input_count": len(base),
        "output_count": len(rebuilt),
        "reconstructed_count": len([
            f for f in rebuilt if f.get("family_reconstruction_source")
        ]),
    }


def _llm_adjudicate(llm, candidate_rows, gap_probes):
    if llm is None or not _bool_env("ENNOSMART_HISTORICAL_RECONCILIATION_USE_LLM", True):
        return {"ok": False, "used": False, "reason": "llm_disabled"}

    gaps = []
    for gap in gap_probes:
        gaps.append({
            "previous_family_id": gap.get("family_id"),
            "evidence": [
                {
                    "evidence_id": e.get("evidence_id"),
                    "role": e.get("role"),
                    "text": _truncate(e.get("text"), 500),
                    "similarity_score": (e.get("similarity") or {}).get("score"),
                }
                for e in (gap.get("evidence") or [])[:5]
            ],
        })

    prompt = (
        "Réconciliation longitudinale EnnoDiagnostic V300. Le diagnostic N est déjà indépendant. "
        "N-1 sert uniquement à comprendre la continuité, jamais comme preuve N.\n\n"
        "Si plusieurs candidats N (paliers, huile, raideurs, pions, vis...) sont des sous-problèmes "
        "du même verrou scientifique N-1, donne le même previous_family_id et status sub_lock/refined/continued. "
        "Ne fusionne pas des mécanismes distincts (ex. CEM vs vibratoire) juste parce qu'ils sont dans le même projet.\n"
        "Une conformité à une norme seule n'est pas un verrou R&D.\n"
        "Pour chaque famille avec >=2 candidats, fournis family_summaries avec un canonical_current_title "
        "fondé UNIQUEMENT sur les candidats N.\n\nCANDIDATS:\n"
        + json.dumps(list(candidate_rows), ensure_ascii=False, indent=2)
        + "\n\nGAPS:\n"
        + json.dumps(gaps, ensure_ascii=False, indent=2)
        + "\n\nJSON uniquement: "
        + '{"current_decisions":[{"current_id":"C1","status":"continued|refined|sub_lock|partially_lifted|extended_scope|new|uncertain",'
          '"previous_family_id":"HF... ou null","confidence":0.0,"reason":"..."}],'
          '"family_summaries":[{"previous_family_id":"HF...","current_ids":["C1","C2"],'
          '"canonical_current_title":"...","confidence":0.0,"reason":"..."}],'
          '"gap_decisions":[{"previous_family_id":"HF...","recover":false,"confidence":0.0,'
          '"current_evidence_ids":[],"reason":"..."}]}'
    )

    try:
        try:
            raw = llm.generate(
                prompt,
                request_name="ennodiagnostic:historical_continuity_v300",
                temperature=0.01,
                max_output_tokens=2800,
                retries=1,
            )
        except TypeError:
            raw = llm.generate(prompt, temperature=0.01, max_output_tokens=2800, retries=1)
        data = _extract_json_object(raw)
        if not data:
            return {"ok": False, "used": True, "error": "invalid_json", "raw_preview": _truncate(raw, 600)}
        return {"ok": True, "used": True, "data": data, "prompt_chars": len(prompt)}
    except Exception as exc:
        return {"ok": False, "used": True, "error": str(exc)}


def _validated_family_summaries_v300(candidate_rows, decisions, llm_report):
    if not llm_report.get("ok"):
        return {}
    data = llm_report.get("data") if isinstance(llm_report.get("data"), Mapping) else {}
    rows = {str(r.get("current_id")): r for r in candidate_rows}
    decisions_by_id = {str(d.get("current_id")): d for d in decisions}
    output = {}
    for item in data.get("family_summaries") or []:
        if not isinstance(item, Mapping):
            continue
        family_id = _clean(item.get("previous_family_id"))
        current_ids = [str(x) for x in item.get("current_ids") or [] if str(x) in rows]
        current_ids = list(dict.fromkeys(current_ids))
        title = _clean(item.get("canonical_current_title"))
        confidence = _float(item.get("confidence"))
        if not family_id or len(current_ids) < 2 or len(title) < 14 or confidence < 0.68:
            continue
        if any(
            _clean((decisions_by_id.get(cid) or {}).get("previous_family_id")) != family_id
            for cid in current_ids
        ):
            continue
        current_union = " ".join(str(rows[cid].get("current_text") or "") for cid in current_ids)
        title_tokens = _tokens(title)
        current_tokens = _tokens(current_union)
        if title_tokens and len(title_tokens & current_tokens) / max(1, len(title_tokens)) < 0.50:
            continue
        output[family_id] = {
            "canonical_current_title": _truncate(title, 240),
            "confidence": round(confidence, 4),
            "source": "llm_current_evidence_guarded",
        }
    return output


def _can_merge_group(rows, decisions, current_items, family_id):
    if len(decisions) < 2:
        return False
    if any(str(d.get("status")) not in CONTINUITY_STATUSES for d in decisions):
        return False
    min_conf = min(_float(d.get("confidence")) for d in decisions)
    if min_conf < _float(os.getenv("ENNOSMART_HISTORICAL_MERGE_MIN_CONFIDENCE", "0.62"), 0.62):
        return False

    scores = [_candidate_similarity_for_family(row, family_id) for row in rows]
    if all(score >= 0.42 for score in scores):
        return True

    if not all(d.get("decision_source") == "llm" for d in decisions) or min_conf < 0.74:
        return False

    for row in rows:
        candidate = next(
            (c for c in (row.get("candidates") or []) if str(c.get("family_id")) == str(family_id)),
            None,
        )
        similarity = (candidate or {}).get("similarity") or {}
        shared = similarity.get("shared_terms") or []
        if (
            _float(similarity.get("score")) < 0.18
            and max(_float(similarity.get("token_containment")), _float(similarity.get("title_containment"))) < 0.34
            and len(shared) < 2
        ):
            return False

    for i in range(len(current_items)):
        for j in range(i + 1, len(current_items)):
            similarity = _similarity(
                _current_lock_text(current_items[i]),
                _current_lock_text(current_items[j]),
                current_title=_current_title(current_items[i]),
                historical_title=_current_title(current_items[j]),
            )
            if similarity.score >= 0.18 or similarity.token_containment >= 0.30 or len(similarity.shared_terms) >= 2:
                return True
    return False


def _canonical_title_fallback_v300(items, family):
    scored = []
    for item in items:
        title = _current_title(item)
        centrality = 0.0
        for other in items:
            if other is not item:
                centrality += _similarity(
                    _current_lock_text(item),
                    _current_lock_text(other),
                    current_title=title,
                    historical_title=_current_title(other),
                ).score
        historical = _similarity(
            _current_lock_text(item),
            _clean(family.get("text")),
            current_title=title,
            historical_title=_clean(family.get("title")),
            support_texts=_family_support_texts(family),
        ).score
        breadth_penalty = max(0.0, (len(_tokens(title)) - 16) * 0.015)
        scored.append((centrality + 0.65 * historical - breadth_penalty, title))
    return max(scored, key=lambda row: row[0])[1] if scored else "Signal R&D candidat"


def _merge_family_group(items, decisions, family, similarities, canonical_summary=None):
    output = _merge_family_group_v200(items, decisions, family, similarities)
    original_title = _current_title(output)
    canonical_title = _clean((canonical_summary or {}).get("canonical_current_title")) or _canonical_title_fallback_v300(items, family)
    output["pre_reconciliation_title"] = original_title
    output["title"] = canonical_title
    output["historical_reconciliation"] = "v300_merged_current_subproblems_by_scientific_family"
    history = output.get("historical_continuity") if isinstance(output.get("historical_continuity"), dict) else {}
    history["version"] = VERSION
    history["canonical_current_title_source"] = (canonical_summary or {}).get("source") or "deterministic_current_centrality"
    history["remaining_uncertainties_n_minus_1"] = family.get("remaining_uncertainties") or []
    output["historical_continuity"] = history
    return output


def _normative_only_candidate_v300(item):
    title = _norm(_current_title(item))
    text = _norm(_current_lock_text(item))
    if not any(x in title for x in ("conformite", "conformity", "norme", "standard", "reglement", "regulatory")):
        return False, "not_normative"
    mechanisms = (
        "modelisation", "simulation", "prediction", "mecanisme", "comportement",
        "non maitr", "couplage", "emission", "immunite", "phenomene", "interaction",
        "variabil", "reproductibil",
    )
    if sum(1 for x in mechanisms if x in text) >= 2:
        return False, "normative_context_with_mechanism"
    return True, "compliance_only_without_scientific_mechanism"


def reconcile_historical_continuity(
    *,
    organisme: str,
    project: str,
    year: str,
    current_verrous: Sequence[Mapping[str, Any]],
    current_sections: Mapping[str, Sequence[Mapping[str, Any]]],
    subproject: str = "",
    search_current: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    llm: Any = None,
    output_dir: Optional[str | Path] = None,
    max_previous_years: Optional[int] = None,
) -> Dict[str, Any]:
    """Reconcile current EnnoDiagnostic candidates with the exact prior CIR.

    The function is intentionally executed *after* the independent year-N lock
    synthesis. It never modifies the NLP result or Frascati decisions upstream.
    """
    current = [deepcopy(dict(item)) for item in current_verrous if isinstance(item, Mapping)]
    original_current_count = len(current)
    for index, item in enumerate(current, start=1):
        item.setdefault("continuity_current_id", f"C{index}")

    normative_suppressed = []
    current_kept = []
    for item in current:
        suppress, reason = _normative_only_candidate_v300(item)
        if suppress and _bool_env("ENNOSMART_HISTORICAL_NORMATIVE_GATE", True):
            hidden = deepcopy(item)
            hidden["display_as_lock"] = False
            hidden["consultant_status"] = "contextual_normative_constraint_not_rnd_lock"
            hidden["normative_gate_reason"] = reason
            normative_suppressed.append(hidden)
        else:
            current_kept.append(item)
    current = current_kept

    max_years = max_previous_years or int(os.getenv("ENNOSMART_CIR_MEMORY_MAX_PREVIOUS_YEARS", "3"))
    try:
        from modules.CIR_MEMORY.cir_memory import load_previous_cir_memory_items

        previous_years, previous_items = load_previous_cir_memory_items(
            organisme=organisme,
            project=project,
            current_year=str(year),
            max_previous_years=max(1, int(max_years)),
            subproject=subproject,
        )
    except Exception as exc:
        report = {
            "ok": False,
            "version": VERSION,
            "error": str(exc),
            "has_previous_cir": False,
            "current_before_count": original_current_count,
            "reconciled_count": len(current),
            "reconciled_verrous": current,
            "policy": "history never current proof; reconciliation skipped on loader error",
        }
        path = _write_report(output_dir, report)
        if path:
            report["output_path"] = path
        return report

    families = _build_historical_families(previous_years, previous_items)
    families, family_reconstruction_report = _reconstruct_historical_families_with_llm_v300(llm, families)
    if not previous_years or not families:
        report = {
            "ok": True,
            "version": VERSION,
            "has_previous_cir": bool(previous_years),
            "previous_years": list(previous_years or []),
            "previous_families_count": len(families),
            "current_before_count": original_current_count,
            "reconciled_count": len(current),
            "reconciled_verrous": current,
            "merged_groups_count": 0,
            "recovered_gap_candidates_count": 0,
            "normative_suppressed_count": len(normative_suppressed),
            "normative_suppressed_candidates": normative_suppressed,
            "policy": "no usable prior lock family; current diagnostic preserved unchanged",
        }
        path = _write_report(output_dir, report)
        if path:
            report["output_path"] = path
        return report

    candidate_rows, current_support = _candidate_matrix(current, families, current_sections)

    # Families with no solid deterministic current match get a targeted search
    # in the current project. This is the omission-detection pass.
    mapped_family_scores: Dict[str, float] = {}
    for row in candidate_rows:
        for candidate in row.get("candidates") or []:
            fid = str(candidate.get("family_id"))
            score = _float((candidate.get("similarity") or {}).get("score"))
            mapped_family_scores[fid] = max(mapped_family_scores.get(fid, 0.0), score)
    unmatched = [
        family
        for family in families
        if mapped_family_scores.get(str(family.get("family_id")), 0.0)
        < _float(os.getenv("ENNOSMART_HISTORICAL_GAP_TRIGGER_SCORE", "0.48"), 0.48)
    ]
    max_gap_families = max(0, int(os.getenv("ENNOSMART_HISTORICAL_GAP_MAX_FAMILIES", "10")))
    gap_probes = [_gap_probe(family, search_current) for family in unmatched[:max_gap_families]]

    llm_report = _llm_adjudicate(llm, candidate_rows, gap_probes)
    decisions = _validated_current_decisions(candidate_rows, llm_report)
    family_summaries = _validated_family_summaries_v300(candidate_rows, decisions, llm_report)
    gap_decisions = _validated_gap_decisions(gap_probes, llm_report)

    rows_by_id = {str(row.get("current_id")): row for row in candidate_rows}
    decisions_by_id = {str(decision.get("current_id")): decision for decision in decisions}
    families_by_id = _family_by_id(families)
    current_by_id = {str(item.get("continuity_current_id")): item for item in current}

    # Group current candidates by a validated historical family.
    family_groups: Dict[str, List[str]] = {}
    standalone_ids: List[str] = []
    for cid, item in current_by_id.items():
        decision = decisions_by_id.get(cid) or _deterministic_decision(rows_by_id[cid])
        fid = _clean(decision.get("previous_family_id"))
        if decision.get("status") in CONTINUITY_STATUSES and fid in families_by_id:
            family_groups.setdefault(fid, []).append(cid)
        else:
            standalone_ids.append(cid)

    reconciled: List[Dict[str, Any]] = []
    merged_groups: List[Dict[str, Any]] = []
    consumed: Set[str] = set()

    for fid, cids in family_groups.items():
        family = families_by_id[fid]
        group_items = [current_by_id[cid] for cid in cids]
        group_rows = [rows_by_id[cid] for cid in cids]
        group_decisions = [decisions_by_id[cid] for cid in cids]
        similarities = [_candidate_similarity_for_family(rows_by_id[cid], fid) for cid in cids]

        if _can_merge_group(group_rows, group_decisions, group_items, fid):
            merged = _merge_family_group(group_items, group_decisions, family, similarities, canonical_summary=family_summaries.get(fid))
            reconciled.append(merged)
            consumed.update(cids)
            merged_groups.append({
                "previous_family_id": fid,
                "previous_year": family.get("previous_year"),
                "historical_family_title": family.get("title"),
                "current_ids": cids,
                "current_titles": [_current_title(item) for item in group_items],
                "merged_into_title": _current_title(merged),
                "reason": "same historical family + validated continuity + merge safety gate",
            })
        else:
            for cid, item, decision, similarity in zip(cids, group_items, group_decisions, similarities):
                reconciled.append(_annotate_single(item, decision, family, similarity))
                consumed.add(cid)

    for cid in standalone_ids:
        if cid in consumed:
            continue
        item = current_by_id[cid]
        decision = decisions_by_id.get(cid) or _deterministic_decision(rows_by_id[cid])
        fid = _clean(decision.get("previous_family_id"))
        family = families_by_id.get(fid)
        similarity = _candidate_similarity_for_family(rows_by_id[cid], fid) if fid else 0.0
        reconciled.append(_annotate_single(item, decision, family, similarity))
        consumed.add(cid)

    # Strict historical gap recovery. It is still only a candidate in
    # EnnoDiagnostic, never a validated CIR lock.
    recovered: List[Dict[str, Any]] = []
    gap_results: List[Dict[str, Any]] = []
    auto_recover = _bool_env("ENNOSMART_HISTORICAL_GAP_RECOVERY", True)
    for gap in gap_probes:
        fid = str(gap.get("family_id"))
        decision = gap_decisions.get(fid)
        passed, evidence, gate_reason = _strict_gap_recovery_gate(gap, decision)
        result = {
            "previous_family_id": fid,
            "previous_family_title": families_by_id.get(fid, {}).get("title"),
            "search_available": gap.get("search_available"),
            "top_current_similarity": _float(((gap.get("evidence") or [{}])[0].get("similarity") or {}).get("score")) if gap.get("evidence") else 0.0,
            "decision": decision,
            "recovery_gate_passed": passed,
            "recovery_gate_reason": gate_reason,
            "recovered": False,
            "current_evidence_ids": [e.get("evidence_id") for e in evidence],
        }
        if auto_recover and passed and fid in families_by_id:
            candidate = _recovered_gap_candidate(families_by_id[fid], evidence, decision or {})
            recovered.append(candidate)
            reconciled.append(candidate)
            result["recovered"] = True
        gap_results.append(result)

    # Stable order: existing current candidates remain first according to their
    # earliest original position; recovered gaps are appended as explicit review
    # candidates. Merged groups carry the representative's original order.
    original_position = {
        str(item.get("continuity_current_id")): index
        for index, item in enumerate(current)
    }

    def reconciled_order(item: Mapping[str, Any]) -> Tuple[int, int]:
        if item.get("historical_gap_recovered"):
            return (10_000, len(reconciled))
        cid = _clean(item.get("continuity_current_id"))
        if cid in original_position:
            return (original_position[cid], 0)
        # merged items may have retained representative id
        return (5_000, 0)

    reconciled.sort(key=reconciled_order)

    report: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "has_previous_cir": True,
        "organisme": organisme,
        "project": project,
        "subproject": subproject or None,
        "current_year": str(year),
        "previous_years": list(previous_years or []),
        "policy": (
            "Pass 1 = current year only. Historical CIR = continuity/gap-search context only. "
            "Every current candidate requires current-year evidence."
        ),
        "current_before_count": original_current_count,
        "reconciled_count": len(reconciled),
        "previous_families_count": len(families),
        "historical_family_reconstruction": family_reconstruction_report,
        "family_summaries": family_summaries,
        "normative_suppressed_count": len(normative_suppressed),
        "normative_suppressed_candidates": normative_suppressed,
        "merged_groups_count": len(merged_groups),
        "recovered_gap_candidates_count": len(recovered),
        "historical_families": families,
        "candidate_matrix": candidate_rows,
        "current_support_by_candidate": current_support,
        "current_decisions": decisions,
        "merged_groups": merged_groups,
        "gap_results": gap_results,
        "llm_adjudication": {
            key: value
            for key, value in llm_report.items()
            if key not in {"data"}
        },
        "reconciled_verrous": reconciled,
        "history_is_current_proof": False,
        "upstream_nlp_groups_modified": False,
        "upstream_frascati_modified": False,
    }

    path = _write_report(output_dir, report)
    if path:
        report["output_path"] = path
    return report
