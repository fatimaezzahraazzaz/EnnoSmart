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
import time
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


VERSION = "historical_continuity_reconciler_v422_deduped_llm_memory_title_continuity_metric"

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


def _is_generic_historical_heading(value: Any) -> bool:
    norm = _norm(value)
    if not norm:
        return True
    if norm in GENERIC_TITLES:
        return True
    generic_fragments = (
        "verrous et incertitudes scientifiques",
        "verrous et incertitudes techniques",
        "etat de l art et verrous",
        "etat de l art",
        "demarche experimentale",
        "travaux r d realises",
        "objectifs de l operation",
        "description des travaux",
    )
    return any(fragment in norm for fragment in generic_fragments)


def _historical_title(item: Mapping[str, Any]) -> str:
    # V410 — préserver d'abord le vrai titre de la carte mémoire si l'ingestion
    # en fournit un. Un titre de section générique (ex. « Verrous et incertitudes… »)
    # ne doit plus masquer un titre scientifique plus précis stocké dans `title`.
    candidates = [
        item.get("title"),
        item.get("label"),
        item.get("verrou_title"),
        item.get("section_title"),
        item.get("section_label"),
    ]
    for candidate in candidates:
        title = _clean(candidate)
        if title and not _is_generic_historical_heading(title) and len(title) >= 10:
            return _truncate(title, 240)

    text = _clean(item.get("text") or item.get("source_text"))
    # Si le CIR n'a pas de sous-titre individuel, il n'existe littéralement
    # aucun « titre exact » à préserver : on garde alors le début exact du
    # paragraphe de verrou, sans invention de vocabulaire.
    first = re.split(r"(?<=[.!?;])\s+|\n+", text, maxsplit=1)[0]
    first = re.sub(
        r"^(?:verrou|incertitude|difficulte)\s*\d*\s*[:\-–—]?\s*",
        "",
        first,
        flags=re.I,
    )
    return _truncate(first, 240) if len(_clean(first)) >= 10 else "Verrou historique"


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


@lru_cache(maxsize=4096)
def _similarity_text_features(text: str) -> Tuple[Any, ...]:
    """Immutable text features; bounded cache, no candidate or proof is omitted."""
    normalized = _norm(text)
    return (
        normalized, frozenset(_tokens(normalized)),
        frozenset(_ngrams(normalized, 2)), frozenset(_numbers(normalized)),
    )


@lru_cache(maxsize=65536)
def _sequence_similarity(current: str, previous: str) -> float:
    # Keep references to the shared normalized strings in the cache keys; slice
    # only inside a miss. A small cache thrashed between overlapping families.
    return SequenceMatcher(None, current[:3200], previous[:3200]).ratio() if current and previous else 0.0


def _similarity(
    current_text: str,
    historical_text: str,
    *,
    current_title: str = "",
    historical_title: str = "",
    support_texts: Optional[Sequence[str]] = None,
    _sequence_override: Optional[float] = None,
) -> Similarity:
    c_norm, c_tokens, c_ngrams, c_numbers = _similarity_text_features(current_text)
    h_norm, h_tokens, h_ngrams, h_numbers = _similarity_text_features(historical_text)
    shared = sorted(c_tokens & h_tokens, key=lambda x: (-len(x), x))
    token_j = _jaccard(c_tokens, h_tokens)
    token_containment = _containment(c_tokens, h_tokens)
    current_title_tokens = _similarity_text_features(current_title)[1]
    historical_title_tokens = _similarity_text_features(historical_title)[1]
    title_j = _jaccard(current_title_tokens, historical_title_tokens)
    title_containment = _containment(current_title_tokens, historical_title_tokens)
    seq = _sequence_override if _sequence_override is not None else (
        _sequence_similarity(c_norm, h_norm)
    )
    bigram = _jaccard(c_ngrams, h_ngrams)
    number = _jaccard(c_numbers, h_numbers)

    support_bonus = 0.0
    for support in support_texts or []:
        s_tokens = _similarity_text_features(support)[1]
        if not s_tokens:
            continue
        support_bonus = max(support_bonus, _jaccard(c_tokens, s_tokens))

    similarity = Similarity(
        score=0.0,
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
    similarity.score = _score_similarity_features(similarity, seq)
    return similarity


def _score_similarity_features(features: Similarity, sequence: float) -> float:
    # Same arithmetic/order as the original score. Reuse invariant lexical
    # features for the upper bound, quick bound and exact sequence comparison.
    score = (
        0.20 * features.token_jaccard + 0.28 * features.token_containment
        + 0.14 * sequence + 0.10 * features.title_jaccard
        + 0.12 * features.title_containment + 0.06 * features.ngram_overlap
        + 0.02 * features.number_overlap + 0.08 * features.support_bonus
    )
    if len(features.shared_terms) >= 5:
        score += 0.05
    elif len(features.shared_terms) >= 3:
        score += 0.025
    return max(0.0, min(1.0, score))


def _top_historical_supports(
    lock_text, title, document, candidates, limit=3, *, prepared=None, matchers=None,
):
    """Exact top-k with a safe upper bound on the costly sequence comparison.

    All candidates retain the same lexical score and document bonus. A full
    comparison is skipped only when even sequence=1 cannot reach the current
    top-k. Original order breaks ties, as in the exhaustive stable sort.
    """
    bounded = []
    if prepared is None:
        prepared = [(candidate, _clean(candidate.get("text") or candidate.get("source_text")),
                     _historical_title(candidate), _source_document(candidate)) for candidate in candidates]
    if matchers is None:
        matchers = {}
    for index, (candidate, text, candidate_title, candidate_document) in enumerate(prepared):
        bonus = 0.06 if document and candidate_document == document else 0.0
        features = _similarity(
            lock_text, text, current_title=title, historical_title=candidate_title,
            _sequence_override=1.0,
        )
        upper = features.score + bonus
        bounded.append((upper, index, candidate, text, features, bonus))
    bounded.sort(key=lambda row: (-row[0], row[1]))
    best = []
    c_norm = _similarity_text_features(lock_text)[0][:3200]
    for upper, index, candidate, text, features, bonus in bounded:
        floor = best[-1][0] if len(best) >= limit else 0.10
        if upper + 1e-12 < floor:
            break
        h_norm = _similarity_text_features(text)[0][:3200]
        matcher = matchers.get(h_norm)
        if matcher is None:
            matcher = SequenceMatcher(None, c_norm, h_norm)
            matchers[h_norm] = matcher
        else:
            matcher.set_seq1(c_norm)
        sequence_bound = matcher.quick_ratio() if c_norm and h_norm else 0.0
        tighter_upper = _score_similarity_features(features, sequence_bound) + bonus
        if tighter_upper + 1e-12 < floor:
            continue
        score = _score_similarity_features(features, matcher.ratio() if c_norm and h_norm else 0.0) + bonus
        if score >= 0.10:
            best.append((score, index, candidate))
            best.sort(key=lambda row: (-row[0], row[1]))
            del best[limit:]
    return [(score, candidate) for score, _, candidate in best]


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


def _same_historical_family(family: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
    """Exact legacy decision with a cheap upper bound before sequence matching.

    Sequence similarity contributes at most 0.14. If even sequence=1 cannot
    reach the original threshold, the expensive comparison cannot change the
    decision. The independent title/token condition is checked unchanged.
    """
    kwargs = {"current_title": family["title"], "historical_title": existing["title"]}
    upper = _similarity(family["text"], existing["text"], _sequence_override=1.0, **kwargs)
    if upper.title_jaccard >= 0.66 and upper.token_jaccard >= 0.48:
        return True
    if upper.score + 1e-12 < 0.78:
        return False
    return _similarity(family["text"], existing["text"], **kwargs).score >= 0.78


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
    prepared_by_role = {
        role: [(item, _clean(item.get("text") or item.get("source_text")),
                _historical_title(item), _source_document(item)) for item in values]
        for role, values in supports_by_role.items()
    }
    # Local to this build, never shared across requests/threads. set_seq1 keeps
    # the immutable historical index and character counts instead of rebuilding
    # them for every current seed. All candidate comparisons remain available.
    matchers: Dict[str, SequenceMatcher] = {}

    families: List[Dict[str, Any]] = []
    progress_started = time.perf_counter()
    for lock_index, lock in enumerate(lock_items, start=1):
        lock_text = _clean(lock.get("text") or lock.get("source_text"))
        title = _historical_title(lock)
        document = _source_document(lock)
        support_payload: Dict[str, List[Dict[str, Any]]] = {}
        for role in ("methode", "resultat", "limite", "contribution", "parametre", "objectif"):
            scored = _top_historical_supports(
                lock_text, title, document,
                [candidate for candidate in supports_by_role.get(role, []) if candidate is not lock],
                prepared=[row for row in prepared_by_role.get(role, []) if row[0] is not lock],
                matchers=matchers,
            )
            selected = []
            for score, candidate in scored:
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
            "historical_exact_title": title,
            "historical_exact_analysis": _truncate(lock_text, 1800),
            "document": document,
            "source_path": _source_path(lock),
            "role": _role(lock),
            "seed_is_explicit_verrou": _role(lock) == "verrou",
            "support": support_payload,
            "history_is_current_proof": False,
        })
        if time.perf_counter() - progress_started >= 10:
            print(f"[EnnoDiagnostic][HISTORY_FAMILIES] supports={lock_index}/{len(lock_items)}", flush=True)
            progress_started = time.perf_counter()

    # Deduplicate near-identical historical lock segments while preserving all
    # support. This is not current-year regrouping; it only prevents a long CIR
    # section split into overlapping windows from becoming several families.
    deduped: List[Dict[str, Any]] = []
    for family in families:
        duplicate_index: Optional[int] = None
        for idx, existing in enumerate(deduped):
            if _same_historical_family(family, existing):
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
    top_k: int = 5,
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



def _score_current_source_against_family(
    source: Mapping[str, Any],
    family: Mapping[str, Any],
    *,
    forced_role: str = "",
) -> Optional[Dict[str, Any]]:
    current_text = _source_text(source)
    if not current_text:
        return None
    meta = _source_meta(source)
    current_role = _norm(
        forced_role
        or meta.get("role")
        or meta.get("final_role")
        or source.get("role")
    )
    family_title = _clean(family.get("title"))
    family_text = _clean(
        family.get("canonical_historical_uncertainty")
        or family.get("text")
    )
    lock_sim = _similarity(
        current_text,
        family_text,
        current_title=_source_title(source),
        historical_title=family_title,
        support_texts=_family_support_texts(family),
    )

    support = family.get("support") if isinstance(family.get("support"), Mapping) else {}
    best_support_score = 0.0
    best_support_role = ""
    best_support_similarity: Optional[Similarity] = None
    support_candidates = []
    for hist_role, rows in support.items():
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            hist_text = _clean(row.get("text"))
            if not hist_text:
                continue
            upper = _similarity(
                current_text,
                hist_text,
                current_title=_source_title(source),
                historical_title=family_title,
                _sequence_override=1.0,
            ).score
            support_candidates.append((upper, len(support_candidates), str(hist_role), hist_text))
    best_index = len(support_candidates)
    for upper, index, hist_role, hist_text in sorted(support_candidates, key=lambda row: (-row[0], row[1])):
        if upper + 1e-12 < best_support_score:
            break
        sim = _similarity(current_text, hist_text, current_title=_source_title(source), historical_title=family_title)
        if sim.score > best_support_score or (sim.score > 0 and sim.score == best_support_score and index < best_index):
            best_support_score = sim.score
            best_support_role = hist_role
            best_support_similarity = sim
            best_index = index

    continuity_score = max(lock_sim.score, best_support_score)
    if current_role and best_support_role and best_support_role in current_role:
        continuity_score = min(1.0, continuity_score + 0.04)

    return {
        "evidence_id": _source_identity(source, prefix="L"),
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
        "query_origins": ["current_nlp_sections"],
        "frascati_score": meta.get("frascati_score") or meta.get("verrou_score"),
        "lock_group_id": _clean(meta.get("lock_group_id") or source.get("lock_group_id")),
        "raw_source": dict(source),
    }


def _gap_probe_from_current_sections(
    family: Mapping[str, Any],
    current_sections: Mapping[str, Sequence[Mapping[str, Any]]],
    top_k: int = 14,
) -> Dict[str, Any]:
    """Contrôle d'abord N-1 contre le NLP courant, sans Chroma ni appel réseau.

    Le NLP courant est l'autorité documentaire déjà préparée par EnnoDiagnostic.
    Cette passe évite des dizaines de recherches Chroma lorsque les indices N sont
    déjà présents dans objectifs/méthodes/résultats/paramètres/limites.
    """
    role_map = {
        "objectifs": "objectif",
        "methodes": "methode",
        "resultats": "resultat",
        "parametres": "parametre",
        "limites": "limite",
        "contributions": "contribution",
        "verrou_support_context": "limite",
    }
    scored: List[Tuple[float, Dict[str, Any]]] = []
    seen: Set[str] = set()
    for section_key, role in role_map.items():
        for raw in current_sections.get(section_key) or []:
            if not isinstance(raw, Mapping):
                continue
            signature = _source_identity(raw, prefix="L")
            if signature in seen:
                continue
            item = _score_current_source_against_family(raw, family, forced_role=role)
            if not item:
                continue
            signature = _clean(item.get("evidence_id"))
            if not signature or signature in seen:
                continue
            seen.add(signature)
            scored.append((_float(item.get("continuity_score")), item))

    evidence = [item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:top_k]]
    return {
        "family_id": family.get("family_id"),
        "family_title": family.get("title"),
        "historical_uncertainty": _truncate(
            family.get("canonical_historical_uncertainty") or family.get("text"),
            1200,
        ),
        "historical_support": family.get("support") or {},
        "query": "",
        "queries": [],
        "evidence": evidence,
        "search_available": False,
        "local_nlp_probe": True,
        "composite_probe": True,
    }


def _probe_has_enough_local_signal(probe: Mapping[str, Any]) -> bool:
    evidence = [e for e in probe.get("evidence") or [] if isinstance(e, Mapping)]
    scores = sorted((_float(e.get("continuity_score")) for e in evidence), reverse=True)
    if not scores:
        return False
    if scores[0] >= 0.42:
        return True
    if len([s for s in scores if s >= 0.28]) >= 2:
        return True
    if len([s for s in scores if s >= 0.16]) >= 3 and scores[0] >= 0.22:
        return True
    return False


def _merge_gap_probes(local: Mapping[str, Any], remote: Mapping[str, Any], top_k: int = 14) -> Dict[str, Any]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for source in list(local.get("evidence") or []) + list(remote.get("evidence") or []):
        if not isinstance(source, Mapping):
            continue
        sid = _clean(source.get("evidence_id"))
        if not sid:
            continue
        existing = by_id.get(sid)
        if existing is None or _float(source.get("continuity_score")) > _float(existing.get("continuity_score")):
            by_id[sid] = dict(source)
    evidence = sorted(
        by_id.values(),
        key=lambda e: _float(e.get("continuity_score")),
        reverse=True,
    )[:top_k]
    merged = dict(local)
    merged["evidence"] = evidence
    merged["search_available"] = bool(remote.get("search_available"))
    merged["queries"] = remote.get("queries") or []
    merged["query"] = remote.get("query") or ""
    merged["query_errors"] = remote.get("query_errors") or []
    merged["local_nlp_probe"] = True
    merged["chroma_fallback_used"] = True
    return merged


def _gap_probe(
    family: Mapping[str, Any],
    search_current: Optional[Callable[..., List[Dict[str, Any]]]],
    top_k: int = 14,
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

    if search_current is None:
        return {
            "family_id": family_id,
            "family_title": family_title,
            "historical_uncertainty": _truncate(family_text, 1200),
            "query": "",
            "queries": [],
            "evidence": [],
            "search_available": False,
        }

    query_rows: List[Dict[str, Any]] = []
    primary_query = _truncate(" ".join([family_title, family_text]), 1000)
    if primary_query:
        query_rows.append({"role": None, "query": primary_query, "origin": "historical_lock"})

    # Recherche multi-facettes. Les rôles ne sont que des préférences de recherche ;
    # un fallback sans filtre est exécuté si un retriever ne supporte pas le rôle.
    max_role_queries = max(1, int(os.getenv("ENNOSMART_HISTORICAL_GAP_ROLE_QUERIES", "2")))
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
    evidence_text_by_family = {
        str(gap.get("family_id")): " ".join(
            _clean(e.get("text")) for e in gap.get("evidence") or [] if isinstance(e, Mapping)
        )
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
        # Une récupération affirmative sans aucune preuve N explicitement citée
        # est toujours refusée en aval.
        recover = bool(raw.get("recover")) and bool(ids)
        title = _truncate(raw.get("current_lock_title"), 260)
        uncertainty = _truncate(raw.get("current_uncertainty"), 1100)
        selected_text = " ".join(
            _clean(e.get("text"))
            for gap in gap_probes if str(gap.get("family_id")) == family_id
            for e in gap.get("evidence") or []
            if isinstance(e, Mapping) and _clean(e.get("evidence_id")) in set(ids)
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
        selected = (very_strong or role_strong or strong)[:6]
        return True, selected, "strict_current_evidence_gate_passed"

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
    """Ajoute un verrou N-1 comme carte N seulement si N confirme sa continuité.

    Quand le recovery gate est franchi, la carte visible utilise le titre
    canonique reformulé par le LLM depuis N-1 lorsqu'il existe, et conserve en
    audit le titre source exact ainsi que l'analyse historique complète. Les
    preuves cliquables restent exclusivement celles de N.
    """
    raw_sources = [
        dict(e.get("raw_source") or {})
        for e in evidence
        if isinstance(e.get("raw_source"), Mapping)
    ]
    exact_memory_title = _clean(family.get("historical_exact_title"))
    canonical_memory_title = _clean(family.get("title"))
    llm_memory_title = _clean(family.get("canonical_title_v300"))
    historical_analysis = _clean(
        family.get("historical_exact_analysis")
        or family.get("canonical_historical_uncertainty")
        or family.get("text")
    )

    if llm_memory_title and family.get("family_reconstruction_source") == "llm_n_minus_1_only":
        # Titre visible reformulé par le LLM à partir du CIR N-1 uniquement.
        # Le titre source exact reste conservé ci-dessous pour l'audit.
        historical_title = llm_memory_title
        visible_title_source = "llm_n_minus_1_family_reconstruction"
    elif exact_memory_title and not _is_generic_historical_heading(exact_memory_title):
        historical_title = exact_memory_title
        visible_title_source = "same_project_previous_cir_exact_title"
    elif canonical_memory_title and not _is_generic_historical_heading(canonical_memory_title):
        # Si le CIR n'avait pas de sous-titre individuel, la reconstruction N-1
        # peut avoir produit un titre canonique à partir du paragraphe historique.
        # Il reste entièrement issu de la mémoire N-1 et n'est pas réécrit par N.
        historical_title = canonical_memory_title
        visible_title_source = "historical_family_canonical_title"
    else:
        # Il n'existe pas toujours de sous-titre individuel dans le CIR. Dans ce
        # cas, le libellé est le début exact du paragraphe historique, pas une
        # reformulation projet-spécifique inventée.
        first = re.split(r"(?<=[.!?;])\s+", historical_analysis, maxsplit=1)[0]
        historical_title = _truncate(first, 240) or "Verrou historique confirmé par les preuves courantes"
        visible_title_source = "historical_analysis_first_sentence_fallback"

    scientific_uncertainty = _truncate(historical_analysis, 1800)
    group_ids = _dedupe_scalar_list(
        e.get("lock_group_id") for e in evidence if e.get("lock_group_id")
    )
    documents = _dedupe_scalar_list(
        e.get("document") for e in evidence if e.get("document")
    )
    source_summary = " ".join(_truncate(e.get("text"), 500) for e in evidence[:6])
    continuity_confidence = max(0.0, min(1.0, _float(decision.get("confidence"))))
    continuity_percentage = int(round(continuity_confidence * 100))

    return {
        "title": historical_title,
        "scientific_uncertainty": scientific_uncertainty,
        "scientific_lock": scientific_uncertainty,
        "technical_axis": historical_title,
        "why_lock": scientific_uncertainty,
        "consultant_explanation": scientific_uncertainty,
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
        "display_score": False,
        "display_metric_kind": "historical_continuity",
        "continuity_percentage": continuity_percentage,
        "consultant_status": "historical_continuity_confirmed_by_current_evidence",
        "candidate_origin": "historical_memory_exact_lock_confirmed_by_current_year_evidence",
        "visible_title_source": visible_title_source,
        "historical_gap_recovered": True,
        "historical_memory_exact_title": historical_title,
        "historical_memory_source_exact_title": exact_memory_title,
        "historical_memory_llm_title": llm_memory_title,
        "historical_memory_exact_analysis": scientific_uncertainty,
        "historical_recovery_gate_reason": decision.get("recovery_gate_reason"),
        "historical_continuity": {
            "version": VERSION,
            "status": "continued",
            "confidence": round(continuity_confidence, 4),
            "continuity_percentage": continuity_percentage,
            "decision_source": decision.get("decision_source"),
            "reason": decision.get("reason"),
            "previous_year": family.get("previous_year"),
            "previous_family_id": family.get("family_id"),
            "historical_family_title": historical_title,
            "historical_source_exact_title": exact_memory_title,
            "historical_llm_reformulated_title": llm_memory_title,
            "visible_title_source": visible_title_source,
            "historical_excerpt": scientific_uncertainty,
            "historical_story": family.get("support"),
            "visible_title_and_analysis_origin": "same_project_previous_cir_memory",
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
                for role in {"methode", "resultat", "limite", "parametre", "objectif", "contribution"}
                if any(_norm(e.get("role")) == role for e in evidence)
            },
            "current_support_is_current_proof": True,
            "history_is_current_proof": False,
            "current_evidence_ids": [e.get("evidence_id") for e in evidence],
            "usage": "exact_memory_lock_visible_only_after_current_evidence_confirmation",
        },
    }


def _matched_historical_memory_candidate(
    family: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    current_ids: Sequence[str],
) -> Dict[str, Any]:
    """Rend visible le verrou N-1 confirmé sans remplacer les verrous N.

    L'ancienne réconciliation annotait uniquement les cartes courantes. Le titre
    et l'analyse du verrou historique existaient donc dans le JSON, mais aucune
    carte séparée ne les exposait au consultant. Cette carte mémoire réutilise
    exactement le même garde de provenance que la récupération de gap : son
    contenu vient de N-1 et ses preuves cliquables viennent exclusivement de N.
    """
    statuses = [_clean(row.get("status")) for row in decisions if _clean(row.get("status"))]
    confidences = [_float(row.get("confidence")) for row in decisions]
    reason = " | ".join(_dedupe_scalar_list(
        _clean(row.get("reason")) for row in decisions if _clean(row.get("reason"))
    ))
    decision = {
        "status": _aggregate_status(statuses),
        "confidence": (
            sum(confidences) / len(confidences)
            if confidences else 0.0
        ),
        "decision_source": "+".join(_dedupe_scalar_list(
            _clean(row.get("decision_source"))
            for row in decisions
            if _clean(row.get("decision_source"))
        )),
        "reason": reason,
        "recovery_gate_reason": "validated_current_candidate_continuity",
    }
    candidate = _recovered_gap_candidate(family, evidence, decision)
    candidate["historical_gap_recovered"] = False
    candidate["historical_memory_card"] = True
    candidate["historical_matched_to_current"] = True
    candidate["candidate_origin"] = "historical_memory_lock_visible_after_validated_current_mapping"
    candidate["historical_recovery_gate_reason"] = "validated_current_candidate_continuity"
    continuity = candidate.get("historical_continuity")
    continuity = continuity if isinstance(continuity, dict) else {}
    continuity["status"] = decision["status"]
    continuity["matched_current_ids"] = list(current_ids)
    continuity["usage"] = "historical_memory_lock_visible_after_validated_current_mapping"
    candidate["historical_continuity"] = continuity
    return candidate


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
    if not base or llm is None or not _bool_env("ENNOSMART_HISTORICAL_FAMILY_RECONSTRUCTION_USE_LLM", True):
        return base, {"used": False, "reason": "disabled_or_empty"}

    # Une section CIR peut être découpée en plusieurs dizaines de fenêtres de
    # phrases qui se chevauchent. Le précédent payload répétait jusqu'à douze
    # supports longs par fenêtre : sur VECAME il dépassait largement la taille
    # utile du contexte et la reconstruction retournait zéro famille acceptée.
    # On conserve toutes les graines, mais avec un contexte court et diversifié.
    compact = []
    for family in base:
        support = []
        for role, values in (family.get("support") or {}).items():
            for value in (values or [])[:1]:
                support.append({
                    "role": role,
                    "section_title": value.get("section_title"),
                    "text": _truncate(value.get("text"), 160),
                })
        compact.append({
            "family_id": family.get("family_id"),
            "role": family.get("role"),
            "seed_quality": family.get("seed_quality_v300"),
            "seed_title": family.get("title"),
            "seed_text": _truncate(family.get("text"), 520),
            "support": support[:3],
        })

    prompt = (
        "Tu reconstruis uniquement les familles scientifiques du CIR N-1. "
        "Un titre descriptif (caractérisation, essais, matériel) n'est pas le nom d'un verrou. "
        "Retrouve TOUS les verrous distincts : ne supprime pas un verrou scientifique sous prétexte "
        "qu'il est partiellement proche d'un autre. Regroupe seulement les fenêtres qui décrivent "
        "manifestement le même verrou. Formule le titre canonique à partir du CIR N-1. "
        "historical_uncertainty doit être un extrait exact (une ou plusieurs phrases copiées) "
        "de l'analyse N-1 fournie, sans paraphrase et sans fait nouveau. "
        "Chaque fragment scientifique utile doit apparaître dans un member_family_ids.\n\n"
        + json.dumps(compact, ensure_ascii=False, indent=2)
        + "\n\nJSON uniquement: "
        + '{"families":[{"member_family_ids":["HF-..."],"canonical_title":"...",'
          '"historical_uncertainty":"...","remaining_uncertainties":["..."],"confidence":0.85}]}'
    )

    try:
        try:
            raw = llm.generate(
                prompt,
                request_name="ennodiagnostic:historical_family_reconstruction_v300",
                temperature=0.01,
                max_output_tokens=3600,
                retries=1,
            )
        except TypeError:
            raw = llm.generate(prompt, temperature=0.01, max_output_tokens=3600, retries=1)
        data = _extract_json_object(raw)
    except Exception as exc:
        return base, {"used": True, "ok": False, "error": str(exc)}

    if not data:
        return base, {"used": True, "ok": False, "reason": "invalid_json"}

    by_id = {str(f.get("family_id")): f for f in base}
    consumed = set()
    rebuilt = []
    rejected_reasons: Dict[str, int] = {}

    def reject(reason: str) -> None:
        rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1

    for row in data.get("families") or []:
        if not isinstance(row, Mapping):
            reject("row_not_mapping")
            continue
        ids = [str(x) for x in row.get("member_family_ids") or [] if str(x) in by_id]
        ids = list(dict.fromkeys(ids))
        title = _clean(row.get("canonical_title"))
        historical_uncertainty = _clean(row.get("historical_uncertainty"))
        confidence = max(0.0, min(1.0, _float(row.get("confidence"))))
        if not ids:
            reject("missing_or_unknown_member_family_ids")
            continue
        if len(title) < 12:
            reject("canonical_title_too_short")
            continue

        anchor = " ".join(
            str(by_id[fid].get("title") or "") + " " + str(by_id[fid].get("text") or "")
            for fid in ids
        )
        title_tokens = _tokens(title)
        anchor_tokens = _tokens(anchor)
        uncertainty_tokens = _tokens(historical_uncertainty)
        title_grounding = len(title_tokens & anchor_tokens) / max(1, len(title_tokens))
        uncertainty_grounding = len(uncertainty_tokens & anchor_tokens) / max(1, len(uncertainty_tokens))
        # Le titre est une synthèse, donc un seuil lexical de 45 % rejetait des
        # regroupements pourtant fidèles. L'analyse, plus longue, reste fortement
        # ancrée dans le texte N-1 et constitue le garde-fou principal.
        if title_tokens and title_grounding < 0.22:
            reject("canonical_title_not_grounded")
            continue
        if historical_uncertainty and uncertainty_grounding < 0.62:
            reject("historical_analysis_not_grounded")
            continue

        # La valeur 0.0 figurait auparavant dans l'exemple JSON. Certains
        # modèles l'ont recopiée comme placeholder alors que le regroupement
        # était correctement ancré dans N-1 ; les cinq propositions VECAME ont
        # ainsi été jetées avant même le contrôle lexical. Le grounding
        # déterministe reste l'autorité : après ses deux contrôles, une confiance
        # absente/faible reçoit un plancher auditable au lieu de supprimer le
        # verrou historique.
        confidence_source = "llm"
        if confidence < 0.60:
            confidence = 0.72
            confidence_source = "deterministic_n_minus_1_grounding_floor"

        representative = deepcopy(max(
            (by_id[fid] for fid in ids),
            key=lambda f: _float(f.get("seed_quality_v300")),
        ))
        representative["family_id"] = "HF3-" + hashlib.sha1(
            "|".join(sorted(ids)).encode("utf-8")
        ).hexdigest()[:12]
        representative["canonical_member_family_ids"] = ids
        representative["original_seed_title"] = representative.get("title")
        representative["canonical_title_v300"] = _truncate(title, 240)
        # Après reconstruction, le titre canonique désigne la famille scientifique
        # entière. Le titre/fragment source reste conservé séparément pour audit.
        representative["title"] = _truncate(title, 240)
        representative["canonical_historical_uncertainty"] = _truncate(
            historical_uncertainty or representative.get("text"), 1400
        )
        source_exact_title = _clean(
            representative.get("historical_exact_title")
            or representative.get("original_seed_title")
        )
        representative["historical_source_title"] = source_exact_title
        # Si le CIR porte un vrai titre individuel, le conserver mot pour mot.
        # Le titre canonique ne sert de remplacement que lorsque le document
        # utilise un intertitre générique (« Verrous », « Limites », etc.).
        representative["historical_exact_title"] = (
            source_exact_title
            if source_exact_title and not _is_generic_historical_heading(source_exact_title)
            else representative["title"]
        )
        representative["historical_exact_analysis"] = representative["canonical_historical_uncertainty"]
        representative["historical_analysis_grounding"] = round(uncertainty_grounding, 4)
        representative["remaining_uncertainties"] = [
            _truncate(x, 350)
            for x in row.get("remaining_uncertainties") or []
            if _clean(x)
        ][:8]
        representative["family_reconstruction_confidence"] = round(confidence, 4)
        representative["family_reconstruction_confidence_source"] = confidence_source
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

    # Le LLM peut reconstruire correctement une famille principale tout en
    # oubliant l'identifiant d'une fenêtre qui reprend mot pour mot une phrase
    # de son analyse. Cette fenêtre restait alors une famille autonome et
    # produisait deux cartes mémoire pour un seul verrou. On l'absorbe seulement
    # lorsque la relation est déterministe : même document, même année et titre
    # brut suffisamment long inclus textuellement dans l'analyse reconstruite.
    canonical_rows = [
        family for family in rebuilt
        if family.get("family_reconstruction_source") == "llm_n_minus_1_only"
    ]
    absorbed_overlap_ids: Set[str] = set()
    absorbed_overlaps: List[Dict[str, Any]] = []
    for raw_family in rebuilt:
        raw_id = _clean(raw_family.get("family_id"))
        if not raw_id or raw_family in canonical_rows:
            continue
        raw_title_norm = _norm(
            raw_family.get("historical_exact_title")
            or raw_family.get("title")
        )
        if len(raw_title_norm) < 55:
            continue
        for canonical in canonical_rows:
            if _clean(raw_family.get("previous_year")) != _clean(canonical.get("previous_year")):
                continue
            if _norm(raw_family.get("document")) != _norm(canonical.get("document")):
                continue
            canonical_analysis_norm = _norm(
                canonical.get("canonical_historical_uncertainty")
                or canonical.get("historical_exact_analysis")
                or canonical.get("text")
            )
            if raw_title_norm not in canonical_analysis_norm:
                continue

            canonical["canonical_member_family_ids"] = _dedupe_scalar_list([
                *(canonical.get("canonical_member_family_ids") or []),
                raw_id,
            ])
            canonical_analysis = _clean(
                canonical.get("historical_exact_analysis")
                or canonical.get("canonical_historical_uncertainty")
                or canonical.get("text")
            )
            raw_analysis = _clean(
                raw_family.get("historical_exact_analysis")
                or raw_family.get("text")
            )
            analysis_sentences: List[str] = []
            seen_sentences: Set[str] = set()
            for sentence in re.split(
                r"(?<=[.!?;])\s+",
                " ".join(value for value in (canonical_analysis, raw_analysis) if value),
            ):
                sentence = _clean(sentence)
                signature = _norm(sentence)
                if not signature or signature in seen_sentences:
                    continue
                seen_sentences.add(signature)
                analysis_sentences.append(sentence)
            merged_exact_analysis = _truncate(" ".join(analysis_sentences), 2400)
            if merged_exact_analysis:
                canonical["historical_exact_analysis"] = merged_exact_analysis
                canonical["canonical_historical_uncertainty"] = merged_exact_analysis
            canonical["absorbed_historical_fragments"] = [
                *(canonical.get("absorbed_historical_fragments") or []),
                {
                    "family_id": raw_id,
                    "exact_title": raw_family.get("historical_exact_title") or raw_family.get("title"),
                    "exact_analysis": raw_analysis,
                    "document": raw_family.get("document"),
                },
            ]
            for role, values in (raw_family.get("support") or {}).items():
                canonical.setdefault("support", {}).setdefault(role, []).extend(
                    deepcopy(list(values or []))
                )
                canonical["support"][role] = _dedupe_scalar_list(
                    canonical["support"][role]
                )[:8]
            absorbed_overlap_ids.add(raw_id)
            absorbed_overlaps.append({
                "absorbed_family_id": raw_id,
                "canonical_family_id": canonical.get("family_id"),
                "reason": "same_document_year_and_raw_title_exactly_present_in_canonical_analysis",
            })
            break

    if absorbed_overlap_ids:
        consumed.update(absorbed_overlap_ids)
        rebuilt = [
            family for family in rebuilt
            if _clean(family.get("family_id")) not in absorbed_overlap_ids
        ]

    return rebuilt, {
        "used": True,
        "ok": True,
        "input_count": len(base),
        "output_count": len(rebuilt),
        "reconstructed_count": len([
            f for f in rebuilt if f.get("family_reconstruction_source")
        ]),
        "consumed_seed_count": len(consumed),
        "unconsumed_seed_count": len(base) - len(consumed),
        "rejected_reasons": rejected_reasons,
        "absorbed_overlap_count": len(absorbed_overlap_ids),
        "absorbed_overlaps": absorbed_overlaps,
        "prompt_chars": len(prompt),
    }


def _llm_adjudicate(llm, candidate_rows, gap_probes):
    if llm is None or not _bool_env("ENNOSMART_HISTORICAL_RECONCILIATION_USE_LLM", True):
        return {"ok": False, "used": False, "reason": "llm_disabled"}

    # Ne jamais envoyer raw_source et toutes ses métadonnées au LLM. Deux verrous
    # VECAME produisaient auparavant un prompt d'environ 307 000 caractères, ce
    # qui faisait tomber l'arbitrage sur le fallback lexical. Ce payload compact
    # conserve uniquement les informations nécessaires à la décision.
    compact_candidates = []
    for row in candidate_rows:
        compact_support: Dict[str, List[Dict[str, Any]]] = {}
        for role, values in (row.get("current_support") or {}).items():
            compact_support[str(role)] = [
                {
                    "evidence_id": value.get("evidence_id"),
                    "role": value.get("role") or role,
                    "document": value.get("document"),
                    "section_title": value.get("section_title"),
                    "text": _truncate(value.get("text"), 360),
                    "score_to_current_lock": value.get("score_to_current_lock"),
                }
                for value in (values or [])[:2]
                if isinstance(value, Mapping)
            ]
        compact_candidates.append({
            "current_id": row.get("current_id"),
            "title": row.get("title"),
            "current_text": _truncate(row.get("current_text"), 900),
            "current_support": compact_support,
            "historical_candidates": [
                {
                    "family_id": candidate.get("family_id"),
                    "title": candidate.get("title"),
                    "historical_excerpt": _truncate(candidate.get("historical_excerpt"), 520),
                    "similarity": candidate.get("similarity"),
                }
                for candidate in (row.get("candidates") or [])[:5]
                if isinstance(candidate, Mapping)
            ],
        })

    gaps = []
    for gap in gap_probes:
        historical_support = gap.get("historical_support") if isinstance(gap.get("historical_support"), Mapping) else {}
        gaps.append({
            "previous_family_id": gap.get("family_id"),
            "historical_family_title": gap.get("family_title"),
            "historical_uncertainty": _truncate(gap.get("historical_uncertainty"), 900),
            "historical_support": {
                role: [
                    {"section_title": row.get("section_title"), "text": _truncate(row.get("text"), 220)}
                    for row in (rows or [])[:1]
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
                    "text": _truncate(e.get("text"), 420),
                    "lock_similarity": (e.get("similarity") or {}).get("score"),
                    "continuity_score": e.get("continuity_score"),
                    "best_historical_support_role": e.get("best_historical_support_role"),
                }
                for e in (gap.get("evidence") or [])[:6]
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
{json.dumps(compact_candidates, ensure_ascii=False, indent=2)}

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
    previous_memory: Optional[Tuple[List[str], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Reconcile current EnnoDiagnostic candidates with the exact prior CIR.

    The function is intentionally executed *after* the independent year-N lock
    synthesis. It never modifies the NLP result or Frascati decisions upstream.
    """
    timings = {}
    stage_started = time.perf_counter()

    def finish_stage(name):
        nonlocal stage_started
        now = time.perf_counter()
        timings[name] = round(now - stage_started, 3)
        stage_started = now
        print(f"[EnnoDiagnostic][HISTORY_PERF] {name}={timings[name]}s", flush=True)

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

        previous_years, previous_items = previous_memory if previous_memory is not None else load_previous_cir_memory_items(
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

    finish_stage('load_previous_memory')
    print(f"[EnnoDiagnostic][HISTORY_STAGE] build_families=start items={len(previous_items)}", flush=True)
    families = _build_historical_families(previous_years, previous_items)
    finish_stage('build_families')
    families, family_reconstruction_report = _reconstruct_historical_families_with_llm_v300(llm, families)
    finish_stage('reconstruct_families')
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
    finish_stage('candidate_matching')

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
    # Les anciens fragments étaient triés uniquement par similarité croissante :
    # les phrases introductives arrivaient en premier et consommaient tout le
    # quota, tandis que les vrais verrous placés plus loin dans la section CIR
    # n'étaient jamais contrôlés. On donne la priorité aux familles reconstruites,
    # puis aux graines portant le signal scientifique le plus fort.
    unmatched = sorted(
        unmatched,
        key=lambda family: (
            0 if family.get("family_reconstruction_source") else 1,
            -_float(family.get("seed_quality_v300")),
            mapped_family_scores.get(str(family.get("family_id")), 0.0),
        ),
    )
    max_gap_families = max(0, int(os.getenv("ENNOSMART_HISTORICAL_GAP_MAX_FAMILIES", "24")))
    reconstructed_unmatched = [
        family for family in unmatched if family.get("family_reconstruction_source")
    ]
    # Tous les vrais verrous reconstruits sont contrôlés. Le plafond ne limite
    # que les fragments bruts de secours lorsque la reconstruction est partielle.
    selected_gap_families = list(reconstructed_unmatched)
    selected_ids = {str(family.get("family_id")) for family in selected_gap_families}
    remaining_capacity = max(0, max_gap_families - len(selected_gap_families))
    selected_gap_families.extend([
        family
        for family in unmatched
        if str(family.get("family_id")) not in selected_ids
    ][:remaining_capacity])
    chroma_fallback_enabled = _bool_env("ENNOSMART_HISTORICAL_GAP_USE_CHROMA_FALLBACK", True)
    max_chroma_families = max(
        0,
        int(os.getenv("ENNOSMART_HISTORICAL_GAP_MAX_CHROMA_FAMILIES", "3")),
    )
    gap_probes: List[Dict[str, Any]] = []
    chroma_used = 0
    for family in selected_gap_families:
        local_probe = _gap_probe_from_current_sections(
            family,
            current_sections,
            top_k=14,
        )
        if (
            _probe_has_enough_local_signal(local_probe)
            or not chroma_fallback_enabled
            or search_current is None
            or chroma_used >= max_chroma_families
        ):
            gap_probes.append(local_probe)
            continue

        remote_probe = _gap_probe(family, search_current, top_k=10)
        chroma_used += 1
        gap_probes.append(_merge_gap_probes(local_probe, remote_probe, top_k=14))

    finish_stage('current_evidence_probes')
    llm_report = _llm_adjudicate(llm, candidate_rows, gap_probes)
    finish_stage('continuity_adjudication')
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

        # La mémoire N-1 ne doit jamais réduire le nombre de verrous déjà détectés
        # dans N. Une fusion reste disponible sur opt-in pour les anciens usages,
        # mais le comportement sûr par défaut annote chaque verrou séparément.
        allow_current_lock_merge = _bool_env(
            "ENNOSMART_HISTORICAL_ALLOW_CURRENT_LOCK_MERGE",
            False,
        )
        if allow_current_lock_merge and _can_merge_group(group_rows, group_decisions, group_items, fid):
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

    # Une correspondance validée ne doit plus rester une simple métadonnée
    # repliée dans la carte N. On ajoute aussi la carte mémoire du verrou N-1,
    # avec son titre/analyse historiques, tout en conservant intégralement les
    # cartes courantes ci-dessus. Aucune carte n'est créée sans preuves N.
    matched_history_cards: List[Dict[str, Any]] = []
    matched_history_signatures: Set[str] = set()
    for fid, cids in family_groups.items():
        family = families_by_id.get(fid)
        if not family:
            continue
        evidence: List[Dict[str, Any]] = []
        seen_evidence_ids: Set[str] = set()
        for cid in cids:
            for role, rows in (current_support.get(cid) or {}).items():
                for raw in rows or []:
                    if not isinstance(raw, Mapping):
                        continue
                    item = deepcopy(dict(raw))
                    item["role"] = _clean(item.get("role")) or _clean(role) or "limite"
                    evidence_id = _clean(item.get("evidence_id"))
                    if not evidence_id:
                        raw_source = item.get("raw_source") if isinstance(item.get("raw_source"), Mapping) else {}
                        evidence_id = _source_identity(raw_source)
                    if not evidence_id or evidence_id in seen_evidence_ids:
                        continue
                    seen_evidence_ids.add(evidence_id)
                    item["evidence_id"] = evidence_id
                    evidence.append(item)

        # Le support rapproché est normalement toujours présent. Ce fallback
        # utilise les sources de la carte N elle-même et évite qu'une variation
        # de structure amont rende la continuité invisible.
        if not evidence:
            for cid in cids:
                for raw_source in _collect_current_sources(current_by_id[cid]):
                    evidence_id = _source_identity(raw_source)
                    if not evidence_id or evidence_id in seen_evidence_ids:
                        continue
                    seen_evidence_ids.add(evidence_id)
                    meta = _source_meta(raw_source)
                    evidence.append({
                        "evidence_id": evidence_id,
                        "role": _clean(meta.get("role") or meta.get("final_role")) or "limite",
                        "document": _source_document(raw_source),
                        "source_path": _source_path(raw_source),
                        "section_title": _source_title(raw_source),
                        "text": _truncate(_source_text(raw_source), 700),
                        "raw_source": dict(raw_source),
                    })

        if not evidence:
            continue
        card = _matched_historical_memory_candidate(
            family,
            evidence[:12],
            [decisions_by_id[cid] for cid in cids],
            cids,
        )
        signature = _norm(
            str(card.get("historical_memory_exact_title") or "")
            + " "
            + str(card.get("historical_memory_exact_analysis") or "")
        )[:1800]
        if signature and signature not in matched_history_signatures:
            matched_history_signatures.add(signature)
            matched_history_cards.append(card)
            reconciled.append(card)

    # Strict historical gap recovery. It is still only a candidate in
    # EnnoDiagnostic, never a validated CIR lock.
    recovered: List[Dict[str, Any]] = []
    recovered_signatures: Set[str] = set()
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
            candidate = _recovered_gap_candidate(
                families_by_id[fid],
                evidence,
                decision_for_candidate,
            )
            signature = _norm(
                str(candidate.get("historical_memory_exact_title") or "")
                + " "
                + str(candidate.get("historical_memory_exact_analysis") or "")
            )[:1800]
            if signature and signature not in recovered_signatures:
                recovered_signatures.add(signature)
                recovered.append(candidate)
                reconciled.append(candidate)
                result["recovered"] = True
            else:
                result["recovery_gate_reason"] = "duplicate_historical_memory_lock_suppressed"
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
        if item.get("historical_memory_card"):
            return (9_000, len(reconciled))
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
        "matched_historical_cards_count": len(matched_history_cards),
        "historical_memory_cards_count": len(matched_history_cards) + len(recovered),
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
        "stage_timings": timings,
    }

    path = _write_report(output_dir, report)
    if path:
        report["output_path"] = path
    return report
