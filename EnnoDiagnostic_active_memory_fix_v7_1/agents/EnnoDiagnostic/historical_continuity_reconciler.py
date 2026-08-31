# -*- coding: utf-8 -*-
from __future__ import annotations

"""
EnnoDiagnostic V400 - Active Historical Memory Reconciler.

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
- recover a missing candidate when several current-year clues jointly support the
  same historical scientific uncertainty, while keeping N-1 non-factual for N.

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


VERSION = "historical_continuity_reconciler_v410_active_memory_fast_current_sections"

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

    # V400 — mémoire active et conservative : un CIR précédent peut contenir
    # certaines incertitudes sous le rôle ``verrou`` et d'autres sous ``limite``.
    # L'ancien fallback exclusif supprimait toutes les limites dès qu'un seul
    # verrou explicite existait. On conserve désormais les deux familles de
    # graines ; le contrôle de qualité V300/V400 en aval décide ensuite lesquelles
    # sont suffisamment scientifiques. Aucun contenu historique n'est pour autant
    # considéré comme preuve de l'année courante.
    explicit_locks = [item for item in items if _role(item) == "verrou"]
    limit_seeds = [item for item in items if _role(item) == "limite"]
    lock_items = [*explicit_locks, *limit_seeds]

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
        "objectif": ("objectifs",),
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
                "raw_source": dict(source),
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



def _current_section_gap_candidates(
    family: Mapping[str, Any],
    current_sections: Mapping[str, Sequence[Mapping[str, Any]]],
    max_items: int = 18,
) -> List[Dict[str, Any]]:
    """Construit un gap probe depuis les sections NLP N déjà chargées.

    La mémoire historique sert à savoir quoi chercher ; les preuves conservées ici
    proviennent exclusivement de l'année courante.
    """
    family_title = _clean(family.get("title"))
    family_text = _clean(
        family.get("canonical_historical_uncertainty")
        or family.get("text")
    )
    support = family.get("support") if isinstance(family.get("support"), Mapping) else {}
    section_map = {
        "objectif": ("objectifs",),
        "methode": ("methodes",),
        "resultat": ("resultats",),
        "limite": ("limites", "verrou_support_context"),
        "parametre": ("parametres",),
        "contribution": ("contributions",),
    }

    historical_support_by_role: Dict[str, List[str]] = {}
    for role, rows in support.items():
        if not isinstance(rows, list):
            continue
        historical_support_by_role[str(role)] = [
            _clean(row.get("text"))
            for row in rows
            if isinstance(row, Mapping) and _clean(row.get("text"))
        ][:5]

    scored: List[Tuple[float, Dict[str, Any]]] = []
    seen: Set[str] = set()

    for current_role, section_keys in section_map.items():
        for section_key in section_keys:
            for raw in current_sections.get(section_key) or []:
                if not isinstance(raw, Mapping):
                    continue
                current_text = _source_text(raw)
                if not current_text:
                    continue
                sid = _source_identity(raw, prefix="N")
                signature = sid or (
                    _source_document(raw) + "|" + _norm(current_text)[:500]
                )
                if not signature or signature in seen:
                    continue
                seen.add(signature)

                lock_sim = _similarity(
                    current_text,
                    family_text,
                    current_title=_source_title(raw),
                    historical_title=family_title,
                )
                best_support_score = 0.0
                best_support_role = ""
                best_support_similarity: Optional[Similarity] = None

                ordered_roles = [current_role] + [
                    role for role in historical_support_by_role
                    if role != current_role
                ]
                for hist_role in ordered_roles:
                    for hist_text in historical_support_by_role.get(hist_role, []):
                        sim = _similarity(
                            current_text,
                            hist_text,
                            current_title=_source_title(raw),
                            historical_title=family_title,
                        )
                        if sim.score > best_support_score:
                            best_support_score = sim.score
                            best_support_role = hist_role
                            best_support_similarity = sim

                continuity_score = max(lock_sim.score, best_support_score)
                if best_support_role == current_role and continuity_score > 0:
                    continuity_score = min(1.0, continuity_score + 0.04)
                if continuity_score < 0.10:
                    continue

                meta = _source_meta(raw)
                scored.append((continuity_score, {
                    "evidence_id": sid,
                    "role": current_role,
                    "document": _source_document(raw),
                    "source_path": _source_path(raw),
                    "section_title": _source_title(raw),
                    "text": _truncate(current_text, 1000),
                    "similarity": lock_sim.as_dict(),
                    "continuity_score": round(continuity_score, 4),
                    "best_historical_support_role": best_support_role,
                    "best_historical_support_similarity": (
                        best_support_similarity.as_dict()
                        if best_support_similarity else {}
                    ),
                    "lock_group_id": _clean(
                        raw.get("lock_group_id")
                        or meta.get("lock_group_id")
                    ),
                    "raw_source": dict(raw),
                    "evidence_origin": "current_sections_nlp",
                }))

    ranked = [
        item for _, item in sorted(
            scored,
            key=lambda pair: pair[0],
            reverse=True,
        )
    ]

    selected: List[Dict[str, Any]] = []
    selected_ids: Set[str] = set()
    for role in ("limite", "methode", "resultat", "parametre", "objectif", "contribution"):
        candidate = next(
            (
                item for item in ranked
                if _norm(item.get("role")) == role
                and _clean(item.get("evidence_id")) not in selected_ids
            ),
            None,
        )
        if candidate is not None:
            selected.append(candidate)
            selected_ids.add(_clean(candidate.get("evidence_id")))
            if len(selected) >= max_items:
                return selected

    for item in ranked:
        sid = _clean(item.get("evidence_id"))
        if sid in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(sid)
        if len(selected) >= max_items:
            break
    return selected


def _current_section_probe_is_sufficient(
    evidence: Sequence[Mapping[str, Any]],
) -> bool:
    """Évite le RAG si les indices N déjà chargés sont suffisants."""
    rows = [row for row in evidence if isinstance(row, Mapping)]
    if not rows:
        return False

    def score(row: Mapping[str, Any]) -> float:
        return max(
            _float(row.get("continuity_score")),
            _float((row.get("similarity") or {}).get("score")),
            _float((row.get("best_historical_support_similarity") or {}).get("score")),
        )

    strong = [row for row in rows if score(row) >= 0.22]
    moderate = [row for row in rows if score(row) >= 0.14]
    roles = {
        _norm(row.get("role"))
        for row in moderate
        if _norm(row.get("role"))
    }
    has_uncertainty = bool(roles & {"limite", "verrou", "incertitude"})
    has_other_scientific_role = bool(
        roles & {"methode", "resultat", "parametre", "contribution", "objectif"}
    )
    return (
        len(strong) >= 2
        or (
            len(moderate) >= 3
            and len(roles) >= 2
            and (has_uncertainty or has_other_scientific_role)
        )
    )


def _gap_probe(
    family: Mapping[str, Any],
    search_current: Optional[Callable[..., List[Dict[str, Any]]]],
    current_sections: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    top_k: int = 12,
) -> Dict[str, Any]:
    """Recherche une continuité N-1 dans plusieurs facettes des preuves N.

    V400 : un verrou transversal n'est souvent pas formulé mot pour mot dans un
    seul passage N. La recherche combine donc le verrou historique avec ses
    contextes méthode/résultat/limite/paramètre/objectif et conserve la meilleure
    correspondance par preuve courante. Le CIR précédent ne fait que construire
    les requêtes : toutes les preuves retournées proviennent du projet courant.
    """
    family_id = family.get("family_id")
    family_title = _clean(family.get("title"))
    family_text = _clean(
        family.get("canonical_historical_uncertainty")
        or family.get("text")
    )
    support = family.get("support") if isinstance(family.get("support"), Mapping) else {}

    current_section_evidence = _current_section_gap_candidates(
        family,
        current_sections or {},
        max_items=max(10, top_k),
    )
    if _current_section_probe_is_sufficient(current_section_evidence):
        return {
            "family_id": family_id,
            "family_title": family_title,
            "historical_uncertainty": _truncate(family_text, 1200),
            "historical_support": deepcopy(support),
            "query": "current_sections_nlp_scan",
            "queries": [{"origin": "current_sections_nlp", "role": None}],
            "evidence": current_section_evidence[:top_k],
            "search_available": True,
            "search_mode": "current_sections_first_no_rag_needed",
            "query_errors": [],
            "seed_is_explicit_verrou": bool(family.get("seed_is_explicit_verrou")),
            "seed_quality_v300": _float(family.get("seed_quality_v300")),
        }

    if search_current is None:
        return {
            "family_id": family_id,
            "family_title": family_title,
            "historical_uncertainty": _truncate(family_text, 1200),
            "historical_support": deepcopy(support),
            "query": "current_sections_nlp_scan",
            "queries": [{"origin": "current_sections_nlp", "role": None}],
            "evidence": current_section_evidence[:top_k],
            "search_available": bool(current_section_evidence),
            "search_mode": "current_sections_only",
            "seed_is_explicit_verrou": bool(family.get("seed_is_explicit_verrou")),
            "seed_quality_v300": _float(family.get("seed_quality_v300")),
        }

    query_rows: List[Dict[str, Any]] = []
    primary_query = _truncate(" ".join([family_title, family_text]), 1000)
    if primary_query:
        query_rows.append({"role": None, "query": primary_query, "origin": "historical_lock"})

    # Recherche multi-facettes. Les rôles ne sont que des préférences de recherche ;
    # un fallback sans filtre est exécuté si un retriever ne supporte pas le rôle.
    max_role_queries = max(1, int(os.getenv("ENNOSMART_HISTORICAL_GAP_ROLE_QUERIES", "1")))
    role_count = 0
    for role in ("limite", "methode", "resultat", "parametre", "objectif", "contribution"):
        rows = [row for row in (support.get(role) or []) if isinstance(row, Mapping)]
        if not rows or role_count >= max_role_queries:
            continue
        best = rows[0]
        support_text = _clean(best.get("text"))
        if not support_text:
            continue
        query_rows.append({
            "role": role if role in {"limite", "methode", "resultat", "parametre"} else None,
            "query": _truncate(" ".join([family_title, support_text]), 900),
            "origin": f"historical_{role}",
            "historical_support_text": support_text,
        })
        role_count += 1

    # Déduplique les requêtes quasi identiques.
    deduped_queries: List[Dict[str, Any]] = []
    seen_queries: Set[str] = set()
    for row in query_rows:
        signature = _norm(row.get("query"))[:700]
        if not signature or signature in seen_queries:
            continue
        seen_queries.add(signature)
        deduped_queries.append(row)

    raw_hits: Dict[str, Dict[str, Any]] = {}
    for row in current_section_evidence:
        sid = _clean(row.get("evidence_id"))
        raw_source = row.get("raw_source")
        if sid and isinstance(raw_source, Mapping):
            raw_hits[sid] = {
                "source": dict(raw_source),
                "query_origins": ["current_sections_nlp"],
            }

    query_errors: List[str] = []
    per_query_top_k = max(4, min(top_k, int(os.getenv("ENNOSMART_HISTORICAL_GAP_PER_QUERY_TOP_K", "5"))))
    for qrow in deduped_queries:
        role = qrow.get("role")
        query = _clean(qrow.get("query"))
        try:
            try:
                found = search_current(role=role, query=query, top_k=per_query_top_k)
            except TypeError:
                found = search_current(role, query, per_query_top_k)
        except Exception as exc:
            # Certains retrievers n'acceptent pas tous les rôles. Repli sans filtre.
            if role:
                try:
                    try:
                        found = search_current(role=None, query=query, top_k=per_query_top_k)
                    except TypeError:
                        found = search_current(None, query, per_query_top_k)
                except Exception as fallback_exc:
                    query_errors.append(f"{role}:{fallback_exc}")
                    continue
            else:
                query_errors.append(str(exc))
                continue

        for source in found or []:
            if not isinstance(source, Mapping):
                continue
            sid = _source_identity(source, prefix="G")
            if not sid:
                continue
            holder = raw_hits.setdefault(sid, {"source": dict(source), "query_origins": []})
            holder["query_origins"].append(qrow.get("origin"))

    support_texts_by_role: Dict[str, List[str]] = {}
    for role, rows in support.items():
        if not isinstance(rows, list):
            continue
        support_texts_by_role[str(role)] = [
            _clean(row.get("text"))
            for row in rows if isinstance(row, Mapping) and _clean(row.get("text"))
        ][:5]

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for sid, holder in raw_hits.items():
        source = holder["source"]
        current_text = _source_text(source)
        if not current_text:
            continue
        meta = _source_meta(source)
        current_role = _norm(meta.get("role") or meta.get("final_role") or source.get("role"))
        lock_sim = _similarity(
            current_text,
            family_text,
            current_title=_source_title(source),
            historical_title=family_title,
            support_texts=_family_support_texts(family),
        )

        best_support_score = 0.0
        best_support_role = ""
        best_support_similarity: Optional[Similarity] = None
        # Compare la preuve N aux facettes N-1 ; cela permet à une méthode ou un
        # résultat courant de confirmer une continuité même si le titre du verrou
        # est plus abstrait.
        for hist_role, hist_texts in support_texts_by_role.items():
            for hist_text in hist_texts:
                sim = _similarity(
                    current_text,
                    hist_text,
                    current_title=_source_title(source),
                    historical_title=family_title,
                )
                if sim.score > best_support_score:
                    best_support_score = sim.score
                    best_support_role = hist_role
                    best_support_similarity = sim

        continuity_score = max(lock_sim.score, best_support_score)
        # Petit bonus uniquement lorsque le rôle courant rejoint la facette N-1.
        if current_role and best_support_role and best_support_role in current_role:
            continuity_score = min(1.0, continuity_score + 0.04)

        item = {
            "evidence_id": sid,
            "role": current_role,
            "document": _source_document(source),
            "source_path": _source_path(source),
            "section_title": _source_title(source),
            "text": _truncate(current_text, 1000),
            "similarity": lock_sim.as_dict(),
            "continuity_score": round(continuity_score, 4),
            "best_historical_support_role": best_support_role,
            "best_historical_support_similarity": (
                best_support_similarity.as_dict() if best_support_similarity else {}
            ),
            "query_origins": list(dict.fromkeys(holder.get("query_origins") or [])),
            "frascati_score": meta.get("frascati_score") or meta.get("verrou_score"),
            "lock_group_id": _clean(meta.get("lock_group_id") or source.get("lock_group_id")),
            "raw_source": dict(source),
        }
        scored.append((continuity_score, item))

    evidence = [item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:top_k]]
    return {
        "family_id": family_id,
        "family_title": family_title,
        "historical_uncertainty": _truncate(family_text, 1200),
        "historical_support": {
            role: [
                {
                    "section_title": _clean(row.get("section_title")),
                    "text": _truncate(row.get("text"), 500),
                }
                for row in rows[:3] if isinstance(row, Mapping)
            ]
            for role, rows in support.items() if isinstance(rows, list) and rows
        },
        "query": primary_query,
        "queries": deduped_queries,
        "evidence": evidence,
        "search_available": True,
        "query_errors": query_errors,
        "composite_probe": True,
        "seed_is_explicit_verrou": bool(family.get("seed_is_explicit_verrou")),
        "seed_quality_v300": _float(family.get("seed_quality_v300")),
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
    """Valide les décisions LLM et ajoute un fallback déterministe très strict.

    Le fallback ne s'active que lorsqu'une famille historique crédible possède
    plusieurs preuves N complémentaires et actuelles. Il n'utilise jamais le
    texte N-1 comme preuve de l'année N.
    """
    valid_ids = {str(gap.get("family_id")) for gap in gap_probes}
    evidence_by_family = {
        str(gap.get("family_id")): {
            str(e.get("evidence_id")) for e in gap.get("evidence") or []
            if isinstance(e, Mapping)
        }
        for gap in gap_probes
    }
    evidence_text_by_family = {
        str(gap.get("family_id")): " ".join(
            _clean(e.get("text"))
            for e in gap.get("evidence") or []
            if isinstance(e, Mapping)
        )
        for gap in gap_probes
    }
    output: Dict[str, Dict[str, Any]] = {}

    if llm_report.get("ok"):
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
            recover = bool(raw.get("recover")) and bool(ids)
            title = _truncate(raw.get("current_lock_title"), 260)
            uncertainty = _truncate(raw.get("current_uncertainty"), 1100)
            selected_text = " ".join(
                _clean(e.get("text"))
                for gap in gap_probes if str(gap.get("family_id")) == family_id
                for e in gap.get("evidence") or []
                if isinstance(e, Mapping)
                and _clean(e.get("evidence_id")) in set(ids)
            ) or evidence_text_by_family.get(family_id, "")

            def grounded(value: str, ratio: float) -> bool:
                toks = _tokens(value)
                if not toks:
                    return False
                support_toks = _tokens(selected_text)
                return len(toks & support_toks) / max(1, len(toks)) >= ratio

            if title and not grounded(title, 0.32):
                title = ""
            if uncertainty and not grounded(uncertainty, 0.22):
                uncertainty = ""
            output[family_id] = {
                "recover": recover,
                "confidence": max(0.0, min(1.0, _float(raw.get("confidence")))),
                "current_evidence_ids": ids,
                "current_lock_title": title,
                "current_uncertainty": uncertainty,
                "reason": _truncate(raw.get("reason"), 500),
                "decision_source": "llm",
            }

    # Fallback déterministe V410 : seulement pour une vraie famille historique
    # et une continuité N forte, distribuée sur plusieurs rôles/preuves.
    for gap in gap_probes:
        family_id = str(gap.get("family_id"))
        existing = output.get(family_id)
        if existing and existing.get("recover"):
            continue

        evidence = [
            dict(e) for e in gap.get("evidence") or []
            if isinstance(e, Mapping)
        ]
        if not evidence:
            continue

        def score(e: Mapping[str, Any]) -> float:
            return max(
                _float(e.get("continuity_score")),
                _float((e.get("similarity") or {}).get("score")),
                _float((e.get("best_historical_support_similarity") or {}).get("score")),
            )

        ranked = sorted(evidence, key=score, reverse=True)
        useful = [e for e in ranked if score(e) >= 0.18]
        if len(useful) < 4:
            continue

        roles = {_norm(e.get("role")) for e in useful if _norm(e.get("role"))}
        has_uncertainty = bool(roles & {"limite", "verrou", "incertitude"})
        has_method_or_result = bool(roles & {"methode", "resultat", "contribution"})
        has_param_or_objective = bool(roles & {"parametre", "objectif"})
        max_score = max((score(e) for e in useful), default=0.0)
        top_mean = sum(score(e) for e in useful[:3]) / max(1, min(3, len(useful)))
        explicit_seed = bool(gap.get("seed_is_explicit_verrou"))
        seed_quality = _float(gap.get("seed_quality_v300"))

        # Une limite historique peut aussi être récupérée, mais seulement si sa
        # qualité est forte et que les indices N couvrent plusieurs facettes.
        credible_family = explicit_seed or seed_quality >= 0.55
        composite_roles = (
            has_uncertainty and has_method_or_result
            and (has_param_or_objective or len(roles) >= 3)
        )
        if not (
            credible_family
            and composite_roles
            and max_score >= 0.28
            and top_mean >= 0.22
        ):
            continue

        selected = useful[:6]
        output[family_id] = {
            "recover": True,
            "confidence": max(
                0.78,
                min(0.90, 0.70 + 0.20 * max_score),
            ),
            "current_evidence_ids": [
                _clean(e.get("evidence_id")) for e in selected
                if _clean(e.get("evidence_id"))
            ],
            "current_lock_title": "",
            "current_uncertainty": "",
            "reason": (
                "Continuité confirmée par plusieurs preuves N complémentaires "
                "(incertitude + démarche/résultat + autre facette courante)."
            ),
            "decision_source": "deterministic_composite_current_evidence",
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
    """Valide une récupération uniquement à partir d'indices N convergents.

    La voie historique n'abaisse pas le niveau de preuve : elle autorise seulement
    une preuve *composite* (méthode + résultat + limite/paramètre, par exemple)
    lorsque plusieurs passages N décrivent ensemble la continuité scientifique.
    """
    if not decision or not decision.get("recover"):
        return False, [], "llm_did_not_confirm_recovery"
    if _float(decision.get("confidence")) < _float(
        os.getenv("ENNOSMART_HISTORICAL_GAP_MIN_CONFIDENCE", "0.76"), 0.76
    ):
        return False, [], "gap_confidence_below_threshold"

    evidence = [e for e in gap.get("evidence") or [] if isinstance(e, Mapping)]
    requested = {_clean(value) for value in decision.get("current_evidence_ids") or [] if _clean(value)}
    if requested:
        evidence = [e for e in evidence if _clean(e.get("evidence_id")) in requested]
    if not evidence:
        return False, [], "no_current_year_evidence_selected"

    def score(e: Mapping[str, Any]) -> float:
        return max(
            _float(e.get("continuity_score")),
            _float((e.get("similarity") or {}).get("score")),
            _float((e.get("best_historical_support_similarity") or {}).get("score")),
        )

    ranked = sorted((dict(e) for e in evidence), key=score, reverse=True)
    strong = [e for e in ranked if score(e) >= 0.28]
    very_strong = [e for e in ranked if score(e) >= 0.42]
    role_strong = [
        e for e in ranked
        if score(e) >= 0.30
        and any(marker in _norm(e.get("role")) for marker in ("verrou", "limite", "incertitude"))
    ]
    if len(strong) >= 2 or very_strong or role_strong:
        # Conserve les preuves les plus fortes MAIS aussi les facettes
        # complémentaires déjà sélectionnées par le probe. Cela permet à la
        # mémoire active d'orienter ensuite démarche/résultats/paramètres sans
        # perdre les indices N moins lexicaux.
        pool = [e for e in ranked if score(e) >= 0.16]
        selected: List[Dict[str, Any]] = []
        seen_roles: Set[str] = set()
        for e in pool:
            role_key = _norm(e.get("role")) or "general"
            if role_key not in seen_roles:
                selected.append(e)
                seen_roles.add(role_key)
            if len(selected) >= 6:
                break
        for e in pool:
            if e not in selected and len(selected) < 6:
                selected.append(e)
        return True, selected[:6], "strict_current_evidence_gate_passed"

    # V400 — continuité multi-preuves. Trois indices modestes mais complémentaires
    # sont plus probants qu'un seul passage lexicalement proche d'un titre N-1.
    composite = [e for e in ranked if score(e) >= 0.16]
    role_families: Set[str] = set()
    for e in composite:
        role = _norm(e.get("role"))
        historical_role = _norm(e.get("best_historical_support_role"))
        blob = f"{role} {historical_role}"
        if any(x in blob for x in ("verrou", "limite", "incertitude")):
            role_families.add("incertitude")
        if any(x in blob for x in ("methode", "demarche", "method")):
            role_families.add("methode")
        if any(x in blob for x in ("resultat", "result", "contribution")):
            role_families.add("resultat")
        if any(x in blob for x in ("parametre", "parameter", "constraint")):
            role_families.add("parametre")
        if "objectif" in blob:
            role_families.add("objectif")

    max_score = max((score(e) for e in composite), default=0.0)
    documents = {_clean(e.get("document")) for e in composite if _clean(e.get("document"))}
    groups = {_clean(e.get("lock_group_id")) for e in composite if _clean(e.get("lock_group_id"))}
    complementary_context = len(role_families) >= 2
    distributed_support = len(documents) >= 2 or len(groups) >= 2 or len(composite) >= 4
    if len(composite) >= 3 and max_score >= 0.22 and complementary_context and distributed_support:
        selected: List[Dict[str, Any]] = []
        seen_roles: Set[str] = set()
        # Priorité à la diversité sémantique puis au score.
        for e in composite:
            role_key = _norm(e.get("best_historical_support_role") or e.get("role")) or "general"
            if role_key not in seen_roles:
                selected.append(e)
                seen_roles.add(role_key)
            if len(selected) >= 6:
                break
        for e in composite:
            if e not in selected and len(selected) < 6:
                selected.append(e)
        return True, selected, "composite_current_evidence_gate_passed"

    return False, [], "insufficient_current_year_evidence"

def _recovered_gap_candidate(
    family: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    decision: Mapping[str, Any],
) -> Dict[str, Any]:
    raw_sources = [
        dict(e.get("raw_source") or {})
        for e in evidence
        if isinstance(e.get("raw_source"), Mapping)
    ]
    current_evidence_text = " ".join(_clean(e.get("text")) for e in evidence)

    # Le LLM peut proposer une formulation courante, mais uniquement si ses mots
    # techniques restent ancrés dans les preuves N sélectionnées. Sinon on utilise
    # une formulation prudente de continuité, jamais le texte N-1 comme fait N.
    proposed_title = _clean(decision.get("current_lock_title"))
    proposed_uncertainty = _clean(decision.get("current_uncertainty"))

    def grounded(value: str, min_ratio: float) -> bool:
        toks = _tokens(value)
        if not toks:
            return False
        evidence_tokens = _tokens(current_evidence_text)
        return len(toks & evidence_tokens) / max(1, len(toks)) >= min_ratio

    if proposed_title and len(proposed_title) >= 14 and grounded(proposed_title, 0.40):
        title = _truncate(proposed_title, 240)
        title_source = "llm_current_evidence_grounded"
    else:
        # Le titre historique sert seulement à nommer la famille recherchée ; le
        # préfixe explicite que sa persistance reste à valider.
        title = "Continuité à confirmer — " + _truncate(family.get("title"), 180)
        title_source = "historical_family_label_with_current_evidence_gate"

    if proposed_uncertainty and len(proposed_uncertainty) >= 24 and grounded(proposed_uncertainty, 0.30):
        scientific_uncertainty = _truncate(proposed_uncertainty, 1100)
        uncertainty_source = "llm_current_evidence_grounded"
    else:
        scientific_uncertainty = (
            "Les preuves de l'année courante montrent des indices convergents d'une "
            "incertitude déjà suivie lors de l'exercice précédent ; sa persistance et "
            "son périmètre exact doivent être validés par le consultant."
        )
        uncertainty_source = "guarded_continuity_statement"

    group_ids = _dedupe_scalar_list(e.get("lock_group_id") for e in evidence if e.get("lock_group_id"))
    documents = _dedupe_scalar_list(e.get("document") for e in evidence if e.get("document"))
    source_summary = " ".join(_truncate(e.get("text"), 500) for e in evidence[:5])

    return {
        "title": title,
        "scientific_uncertainty": scientific_uncertainty,
        "scientific_lock": scientific_uncertainty,
        "technical_axis": title,
        "why_lock": (
            "Candidat retrouvé par la mémoire scientifique longitudinale. Le CIR antérieur "
            "a orienté la recherche ; la carte visible est maintenue uniquement parce que "
            "des indices du projet courant ont franchi le contrôle de continuité."
        ),
        "evidence_summary": _truncate(source_summary, 1800),
        "source_evidence": raw_sources,
        "supporting_passages": raw_sources,
        "sources": raw_sources,
        "member_group_ids": group_ids,
        "original_nlp_group_ids": group_ids,
        "document": "; ".join(str(value) for value in documents),
        "display_as_lock": True,
        "display_as_main_lock": True,
        "operation_status": "rnd_core_partial",
        "frascati_score": None,
        "score": None,
        "consultant_status": "historical_gap_recovered_to_validate",
        "candidate_origin": "historical_gap_probe_current_year_composite_evidence",
        "historical_gap_recovered": True,
        "historical_recovery_title_source": title_source,
        "historical_recovery_uncertainty_source": uncertainty_source,
        "historical_recovery_gate_reason": decision.get("recovery_gate_reason"),
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
            "current_support": {
                role: [
                    {
                        "evidence_id": e.get("evidence_id"),
                        "role": role,
                        "document": e.get("document"),
                        "source_path": e.get("source_path"),
                        "section_title": e.get("section_title"),
                        "text": e.get("text"),
                        "raw_source": dict(e.get("raw_source") or {}),
                    }
                    for e in evidence
                    if _norm(e.get("role")) == role
                ]
                for role in {"methode", "resultat", "limite", "parametre", "objectif"}
                if any(_norm(e.get("role")) == role for e in evidence)
            },
            "current_support_is_current_proof": True,
            "history_is_current_proof": False,
            "current_evidence_ids": [e.get("evidence_id") for e in evidence],
            "usage": "active_memory_search_trigger_current_composite_evidence_required",
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
    score = 0.45 if role == "verrou" else (0.28 if role == "limite" else 0.0)
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
        min_quality = 0.38 if _role(family) == "limite" else 0.42
        if quality >= min_quality:
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
    if not base or llm is None or not _bool_env("ENNOSMART_HISTORICAL_FAMILY_RECONSTRUCTION_USE_LLM", False):
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
        historical_support = gap.get("historical_support") if isinstance(gap.get("historical_support"), Mapping) else {}
        gaps.append({
            "previous_family_id": gap.get("family_id"),
            "historical_family_title": gap.get("family_title"),
            "historical_uncertainty": _truncate(gap.get("historical_uncertainty"), 900),
            "historical_support": {
                role: [
                    {"section_title": row.get("section_title"), "text": _truncate(row.get("text"), 320)}
                    for row in (rows or [])[:2]
                    if isinstance(row, Mapping)
                ]
                for role, rows in historical_support.items()
                if isinstance(rows, list)
            },
            "current_evidence": [
                {
                    "evidence_id": e.get("evidence_id"),
                    "role": e.get("role"),
                    "document": e.get("document"),
                    "section_title": e.get("section_title"),
                    "text": _truncate(e.get("text"), 620),
                    "lock_similarity": (e.get("similarity") or {}).get("score"),
                    "continuity_score": e.get("continuity_score"),
                    "best_historical_support_role": e.get("best_historical_support_role"),
                }
                for e in (gap.get("evidence") or [])[:10]
            ],
        })

    prompt = f"""
Réconciliation longitudinale EnnoDiagnostic V400.

Le diagnostic N a d'abord été produit indépendamment. Le CIR N-1 est la mémoire
scientifique du même projet : il sert à rappeler ce qu'il faut rechercher, à
maintenir le bon niveau d'abstraction et à détecter une continuité oubliée.
Il n'est JAMAIS une preuve factuelle de N.

REGLES COURANTES
- Pour current_decisions, décide si chaque verrou N poursuit, raffine, étend ou
  remplace une famille N-1.
- Plusieurs sous-problèmes N peuvent appartenir au même verrou historique, mais
  ne fusionne pas des mécanismes distincts.
- Une conformité à une norme seule n'est pas un verrou R&D.

RECUPERATION D'UN VERROU OUBLIE
- Pour un GAP, recover=true uniquement si les preuves COURANTES N montrent
  réellement des indices de continuité.
- La continuité peut être COMPOSITE : aucun passage N ne doit nécessairement
  reprendre mot pour mot le titre N-1. Une combinaison cohérente méthode +
  résultat + limite/paramètre peut démontrer la persistance du même verrou.
- N'utilise jamais le texte N-1 seul pour recover=true.
- current_evidence_ids doit contenir uniquement les preuves N réellement nécessaires.
- Si recover=true, propose current_lock_title et current_uncertainty à partir des
  preuves N sélectionnées. Ces formulations doivent décrire le mécanisme
  scientifique courant, sans recopier un fait historique non confirmé.

CANDIDATS COURANTS
{json.dumps(list(candidate_rows), ensure_ascii=False, indent=2)}

FAMILLES HISTORIQUES A CONTROLER ET INDICES N
{json.dumps(gaps, ensure_ascii=False, indent=2)}

Réponds UNIQUEMENT avec ce JSON :
{{
  "current_decisions": [
    {{
      "current_id": "C1",
      "status": "continued|refined|sub_lock|partially_lifted|extended_scope|new|uncertain",
      "previous_family_id": "HF... ou null",
      "confidence": 0.0,
      "reason": "..."
    }}
  ],
  "family_summaries": [
    {{
      "previous_family_id": "HF...",
      "current_ids": ["C1", "C2"],
      "canonical_current_title": "...",
      "confidence": 0.0,
      "reason": "..."
    }}
  ],
  "gap_decisions": [
    {{
      "previous_family_id": "HF...",
      "recover": false,
      "confidence": 0.0,
      "current_evidence_ids": [],
      "current_lock_title": "",
      "current_uncertainty": "",
      "reason": "..."
    }}
  ]
}}
""".strip()

    try:
        try:
            raw = llm.generate(
                prompt,
                request_name="ennodiagnostic:historical_continuity_v400",
                temperature=0.01,
                max_output_tokens=3400,
                retries=1,
            )
        except TypeError:
            raw = llm.generate(prompt, temperature=0.01, max_output_tokens=3400, retries=1)
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
    # V400 — un score lexical moyen ne suffit plus à déclarer une famille
    # "couverte". Les familles sans correspondance déterministe très forte sont
    # contrôlées dans le RAG courant ; l'arbitrage LLM décidera ensuite si le gap
    # est réel. Cela évite qu'un faux rapprochement à 0.50 bloque toute recherche.
    strong_trigger = _float(os.getenv("ENNOSMART_HISTORICAL_GAP_TRIGGER_SCORE", "0.72"), 0.72)
    unmatched = [
        family
        for family in families
        if mapped_family_scores.get(str(family.get("family_id")), 0.0) < strong_trigger
    ]
    unmatched = sorted(
        unmatched,
        key=lambda family: mapped_family_scores.get(str(family.get("family_id")), 0.0),
    )
    max_gap_families = max(0, int(os.getenv("ENNOSMART_HISTORICAL_GAP_MAX_FAMILIES", "8")))
    gap_probes = [
        _gap_probe(
            family,
            search_current,
            current_sections=current_sections,
        )
        for family in unmatched[:max_gap_families]
    ]

    llm_report = _llm_adjudicate(llm, candidate_rows, gap_probes)
    decisions = _validated_current_decisions(candidate_rows, llm_report)
    family_summaries = _validated_family_summaries_v300(candidate_rows, decisions, llm_report)
    gap_decisions = _validated_gap_decisions(gap_probes, llm_report)

    rows_by_id = {str(row.get("current_id")): row for row in candidate_rows}
    decisions_by_id = {str(decision.get("current_id")): decision for decision in decisions}
    families_by_id = _family_by_id(families)
    current_by_id = {str(item.get("continuity_current_id")): item for item in current}
    matched_family_ids = {
        _clean(decision.get("previous_family_id"))
        for decision in decisions
        if decision.get("status") in CONTINUITY_STATUSES
        and _clean(decision.get("previous_family_id"))
    }

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
            merged_history = merged.get("historical_continuity") if isinstance(merged.get("historical_continuity"), dict) else {}
            merged_current_support: Dict[str, List[Dict[str, Any]]] = {}
            for _cid in cids:
                for _role, _rows in (current_support.get(_cid) or {}).items():
                    merged_current_support.setdefault(_role, []).extend(deepcopy(list(_rows or [])))
            merged_history["current_support"] = {
                _role: _dedupe_scalar_list(_rows)[:8]
                for _role, _rows in merged_current_support.items()
            }
            merged_history["current_support_is_current_proof"] = True
            merged["historical_continuity"] = merged_history
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
                annotated = _annotate_single(item, decision, family, similarity)
                annotated_history = annotated.get("historical_continuity") if isinstance(annotated.get("historical_continuity"), dict) else {}
                annotated_history["current_support"] = deepcopy(current_support.get(cid) or {})
                annotated_history["current_support_is_current_proof"] = True
                annotated["historical_continuity"] = annotated_history
                reconciled.append(annotated)
                consumed.add(cid)

    for cid in standalone_ids:
        if cid in consumed:
            continue
        item = current_by_id[cid]
        decision = decisions_by_id.get(cid) or _deterministic_decision(rows_by_id[cid])
        fid = _clean(decision.get("previous_family_id"))
        family = families_by_id.get(fid)
        similarity = _candidate_similarity_for_family(rows_by_id[cid], fid) if fid else 0.0
        annotated = _annotate_single(item, decision, family, similarity)
        annotated_history = annotated.get("historical_continuity") if isinstance(annotated.get("historical_continuity"), dict) else {}
        annotated_history["current_support"] = deepcopy(current_support.get(cid) or {})
        annotated_history["current_support_is_current_proof"] = True
        annotated["historical_continuity"] = annotated_history
        reconciled.append(annotated)
        consumed.add(cid)

    # Strict historical gap recovery. It is still only a candidate in
    # EnnoDiagnostic, never a validated CIR lock.
    recovered: List[Dict[str, Any]] = []
    gap_results: List[Dict[str, Any]] = []
    auto_recover = _bool_env("ENNOSMART_HISTORICAL_GAP_RECOVERY", True)
    for gap in gap_probes:
        fid = str(gap.get("family_id"))
        decision = gap_decisions.get(fid)
        if fid in matched_family_ids:
            # La recherche de gap est préparée avant l'arbitrage LLM. Une famille
            # ensuite reconnue comme continuité ne doit jamais être récupérée une
            # seconde fois sous forme de doublon.
            passed, evidence, gate_reason = False, [], "already_mapped_to_current_candidate"
        else:
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
            decision_for_candidate = dict(decision or {})
            decision_for_candidate["recovery_gate_reason"] = gate_reason
            candidate = _recovered_gap_candidate(families_by_id[fid], evidence, decision_for_candidate)
            recovered.append(candidate)
            reconciled.append(candidate)
            result["recovered"] = True
        gap_results.append(result)

    gap_results_by_family = {
        str(item.get("previous_family_id")): item for item in gap_results
    }
    current_ids_by_family: Dict[str, List[str]] = {}
    for decision in decisions:
        fid = _clean(decision.get("previous_family_id"))
        if decision.get("status") in CONTINUITY_STATUSES and fid:
            current_ids_by_family.setdefault(fid, []).append(_clean(decision.get("current_id")))

    historical_family_coverage: List[Dict[str, Any]] = []
    recovered_family_ids = {
        _clean((item.get("historical_continuity") or {}).get("previous_family_id"))
        for item in recovered
        if isinstance(item.get("historical_continuity"), Mapping)
    }
    for family in families:
        fid = _clean(family.get("family_id"))
        gap_result = gap_results_by_family.get(fid) or {}
        if fid in current_ids_by_family:
            coverage_status = "matched_current_candidate"
            requires_review = False
        elif fid in recovered_family_ids:
            coverage_status = "recovered_with_current_year_evidence"
            requires_review = True
        elif gap_result:
            coverage_status = "not_found_in_current_year_evidence"
            requires_review = True
        else:
            coverage_status = "similarity_detected_but_continuity_not_validated"
            requires_review = True
        historical_family_coverage.append({
            "previous_family_id": fid,
            "previous_year": family.get("previous_year"),
            "previous_family_title": family.get("title"),
            "coverage_status": coverage_status,
            "current_ids": current_ids_by_family.get(fid, []),
            "top_current_similarity": gap_result.get("top_current_similarity"),
            "recovery_gate_reason": gap_result.get("recovery_gate_reason"),
            "requires_consultant_review": requires_review,
            "history_is_current_proof": False,
        })

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
            "Pass 1 = current year only. Historical CIR = active same-project memory for continuity, "
            "gap search and abstraction orientation; never factual proof of N. Every visible current "
            "candidate requires current-year evidence, including composite evidence when appropriate."
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
        "historical_family_coverage": historical_family_coverage,
        "historical_family_coverage_counts": {
            status: sum(
                1 for row in historical_family_coverage
                if row.get("coverage_status") == status
            )
            for status in {
                row.get("coverage_status") for row in historical_family_coverage
                if row.get("coverage_status")
            }
        },
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
