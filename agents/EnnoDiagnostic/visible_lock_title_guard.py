# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Safe visible-title repair for EnnoDiagnostic.

This module NEVER changes:
- Frascati score/recommendation,
- current evidence,
- historical matching,
- lock grouping / parent consolidation,
- scientific uncertainty / why_lock.

It may replace ONLY ``title`` for a CURRENT visible lock when the existing
title is demonstrably malformed, truncated, generic, or inherited from a
historical/document section heading.

Fail-safe contract:
- no suspicious title => no LLM call;
- no LLM / API error / invalid JSON / insufficient grounding => original title;
- pure historical memory cards are never rewritten here.
"""

import json
import re
import unicodedata
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

VERSION = "visible_lock_title_guard_safe_v1_20260916"
LOG = "[EnnoDiagnostic][VISIBLE_TITLE_GUARD]"

_STOPWORDS = {
    "avec", "dans", "pour", "sans", "sous", "entre", "vers", "chez", "depuis",
    "des", "les", "une", "aux", "sur", "par", "que", "qui", "dont", "plus",
    "moins", "ainsi", "afin", "leur", "leurs", "cette", "ces", "cela", "comme",
    "etre", "sont", "avait", "avoir", "peut", "doit", "projet", "cir", "annee",
    "technique", "techniques", "scientifique", "scientifiques", "travaux",
    "etude", "analyse", "resultat", "resultats", "methode", "methodes",
    "verrou", "verrous", "signal", "signaux", "incertitude", "incertitudes",
    "recherche", "niveau", "preuve", "preuves", "dossier",
    "the", "and", "with", "without", "from", "into", "that", "this", "these",
    "project", "technical", "scientific", "results", "method", "methods",
}

_TRAILING_FRAGMENT_RE = re.compile(
    r"(?:\b(?:de|du|des|le|la|les|un|une|à|a|au|aux|sur|pour|avec|sans|"
    r"entre|dans|et|ou|par|vers|dont|que|qui|niveau)\b|[:|;/,-])\s*$",
    re.I,
)

_BAD_TITLE_PATTERNS = (
    re.compile(r"\bincertitude\s+sur\s+(?:les?\s+)?incertitudes?\b", re.I),
    re.compile(r"\bincertitudes?\s+de\s+recherche\b", re.I),
    re.compile(r"\bverrou\s*\d+\b", re.I),
    re.compile(r"\b(?:ffl|done)\b", re.I),
    re.compile(r"\|\s*(?:incertitudes?|recherche|verrou|section|questions?)\b", re.I),
)

_GENERIC_RE = re.compile(
    r"^(?:incertitude technique(?: à| a)? préciser(?: avant validation cir)?|"
    r"signal technique(?: à| a)? reformuler(?: avant validation cir)?|"
    r"verrou technique(?: à| a)? préciser|verrou(?: à| a)? préciser|"
    r"incertitude sur (?:le|la|les)?\s*(?:comportement|robustesse|conditions?|"
    r"limites?|incertitudes?)?)$",
    re.I,
)


def _clean(value: Any, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'")
    text = re.sub(r"[^a-z0-9%+./_-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> Set[str]:
    out: Set[str] = set()
    for token in re.findall(r"[a-z0-9][a-z0-9%+./_-]{2,}", _norm(value)):
        if len(token) >= 4 and token not in _STOPWORDS and not token.isdigit():
            out.add(token)
    return out


def _historical_year_in_current_title(
    title: str,
    item: Mapping[str, Any],
    current_year: Any,
) -> bool:
    years = set(re.findall(r"\b20\d{2}\b", title))
    if not years:
        return False
    current = _clean(current_year)
    has_history = bool(
        item.get("historical_continuity")
        or item.get("historical_gap_recovered")
        or item.get("historical_matched_to_current")
    )
    return bool(has_history and any(year != current for year in years))


def suspicious_title_reasons(
    item: Mapping[str, Any],
    current_year: Any = None,
) -> List[str]:
    title = _clean(item.get("title"), 320)
    reasons: List[str] = []

    if not title:
        reasons.append("empty_title")
        return reasons

    if bool(item.get("historical_memory_card")):
        return []

    norm = _norm(title)
    if len(title) < 28:
        reasons.append("too_short")
    if len(title) > 260:
        reasons.append("too_long")
    if "|" in title:
        reasons.append("section_separator")
    if "\n" in title or "\r" in title:
        reasons.append("multiline")
    if _GENERIC_RE.match(norm):
        reasons.append("generic_title")
    if _TRAILING_FRAGMENT_RE.search(title):
        reasons.append("truncated_ending")
    if any(pattern.search(title) for pattern in _BAD_TITLE_PATTERNS):
        reasons.append("document_heading_or_duplicate_uncertainty")
    if _historical_year_in_current_title(title, item, current_year):
        reasons.append("historical_year_in_current_title")

    # Duplicated label-like punctuation is usually a heading copied from a source.
    if title.count(":") >= 2 or title.count("|") >= 1:
        reasons.append("heading_like_title")

    return list(dict.fromkeys(reasons))


def _source_text(source: Mapping[str, Any]) -> str:
    meta = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
    return _clean(
        source.get("analysis_text")
        or source.get("text")
        or source.get("source_text")
        or source.get("excerpt")
        or source.get("content")
        or meta.get("analysis_text")
        or meta.get("text")
        or meta.get("source_text")
        or meta.get("excerpt"),
        1400,
    )


def _current_support_from_history(item: Mapping[str, Any]) -> List[str]:
    continuity = item.get("historical_continuity")
    if not isinstance(continuity, Mapping):
        return []
    support = continuity.get("current_support")
    if not isinstance(support, Mapping):
        return []
    out: List[str] = []
    for values in support.values():
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        for row in values:
            if isinstance(row, Mapping):
                text = _clean(row.get("text"), 1400)
                if text:
                    out.append(text)
    return out[:14]


def _grounding_blob(item: Mapping[str, Any]) -> str:
    parts: List[str] = []

    # Current semantic fields first.
    for key in (
        "scientific_uncertainty",
        "scientific_lock",
        "why_lock",
        "evidence_summary",
        "consultant_explanation",
        "analysis_text",
        "text",
    ):
        value = _clean(item.get(key), 2200)
        if value:
            parts.append(value)

    # Explicit CURRENT proof carried by the historical continuity object.
    parts.extend(_current_support_from_history(item))

    # Current source objects. Historical title/excerpt fields are intentionally
    # excluded so a 2024 heading cannot ground a new 2025 visible title.
    for key in ("sources", "source_evidence", "supporting_passages"):
        values = item.get(key)
        if isinstance(values, Mapping):
            values = [values]
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        for source in list(values)[:16]:
            if isinstance(source, Mapping):
                text = _source_text(source)
                if text:
                    parts.append(text)

    deduped: List[str] = []
    seen = set()
    for part in parts:
        sig = _norm(part)
        if not sig or sig in seen:
            continue
        seen.add(sig)
        deduped.append(part)

    return _clean(" ".join(deduped), 12000)


def _extract_json_object(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _valid_repaired_title(title: Any, grounding: str) -> Tuple[bool, str]:
    candidate = _clean(title, 300)
    if len(candidate) < 35 or len(candidate) > 240:
        return False, "length"
    if not re.match(r"^Incertitude sur\b", candidate, flags=re.I):
        return False, "missing_incertitude_prefix"
    if "|" in candidate or "\n" in candidate or "\r" in candidate:
        return False, "heading_separator"
    if re.search(r"\b20\d{2}\b", candidate):
        return False, "year_in_title"
    if _TRAILING_FRAGMENT_RE.search(candidate):
        return False, "truncated_ending"
    if any(pattern.search(candidate) for pattern in _BAD_TITLE_PATTERNS):
        return False, "bad_pattern"

    title_tokens = _tokens(candidate)
    grounding_tokens = _tokens(grounding)
    shared = title_tokens & grounding_tokens
    # Require at least two specific technical anchors from CURRENT evidence.
    if len(shared) < 2:
        return False, "insufficient_current_grounding"

    return True, "ok"


def repair_suspicious_visible_titles(
    items: Sequence[Mapping[str, Any]],
    *,
    llm: Any,
    current_year: Any = None,
) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = [
        deepcopy(dict(item)) if isinstance(item, Mapping) else {}
        for item in items
    ]

    suspicious: List[Dict[str, Any]] = []
    for index, item in enumerate(output):
        reasons = suspicious_title_reasons(item, current_year=current_year)
        if not reasons:
            continue
        grounding = _grounding_blob(item)
        if not grounding:
            print(
                f"{LOG} SKIP index={index} reason=no_current_grounding "
                f"title={_clean(item.get('title'), 140)}",
                flush=True,
            )
            continue
        suspicious.append({
            "index": index,
            "original_title": _clean(item.get("title"), 280),
            "reasons": reasons,
            "grounding": grounding,
        })

    if not suspicious:
        return output

    if llm is None:
        print(f"{LOG} SKIP count={len(suspicious)} reason=no_llm", flush=True)
        return output

    compact = []
    for row in suspicious[:12]:
        compact.append({
            "index": row["index"],
            "original_title": row["original_title"],
            "quality_issues": row["reasons"],
            "current_evidence": _clean(row["grounding"], 6500),
        })

    prompt = (
        "Tu corriges UNIQUEMENT les titres visibles de verrous R&D candidats "
        "EnnoDiagnostic lorsque le titre actuel est manifestement tronqué, générique "
        "ou hérité d'un titre de section historique.\n"
        "RÈGLES ABSOLUES:\n"
        "1. Ne modifie pas le sens du verrou.\n"
        "2. Utilise uniquement current_evidence ; n'invente aucun fait, technologie, "
        "dataset, résultat ou cause.\n"
        "3. Le titre doit commencer exactement par « Incertitude sur ».\n"
        "4. Décris l'inconnue technique précise, pas la solution ni une tâche.\n"
        "5. 8 à 26 mots environ, une seule phrase, sans année, sans « | », sans "
        "numéro de verrou, sans titre de section.\n"
        "6. N'écris pas « Incertitude sur incertitude(s) ».\n"
        "7. Si les preuves ne permettent pas un titre plus précis, retourne "
        "l'original avec repair=false.\n"
        "JSON uniquement, aucun commentaire.\n\n"
        + json.dumps(compact, ensure_ascii=False, indent=2)
        + '\n\nFormat exact: {"repairs":[{"index":0,"repair":true,'
          '"title":"Incertitude sur ..."}]}'
    )

    try:
        try:
            raw = llm.generate(
                prompt,
                request_name="ennodiagnostic:visible_lock_title_repair",
                temperature=0.0,
                max_output_tokens=1400,
                retries=1,
            )
        except TypeError:
            raw = llm.generate(
                prompt,
                temperature=0.0,
                max_output_tokens=1400,
                retries=1,
            )
        data = _extract_json_object(raw)
    except Exception as exc:
        print(f"{LOG} WARN llm_error={exc}", flush=True)
        return output

    rows = data.get("repairs")
    if not isinstance(rows, list):
        print(f"{LOG} WARN invalid_json_shape", flush=True)
        return output

    suspicious_by_index = {row["index"]: row for row in suspicious}
    repaired_count = 0

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            index = int(row.get("index"))
        except Exception:
            continue
        source = suspicious_by_index.get(index)
        if source is None or index < 0 or index >= len(output):
            continue
        if row.get("repair") is False:
            continue

        candidate = _clean(row.get("title"), 300)
        valid, validation_reason = _valid_repaired_title(
            candidate,
            source["grounding"],
        )
        if not valid:
            print(
                f"{LOG} REJECT index={index} reason={validation_reason} "
                f"candidate={candidate[:160]}",
                flush=True,
            )
            continue

        original = _clean(output[index].get("title"), 300)
        if _norm(candidate) == _norm(original):
            continue

        # ONLY title changes. Everything else remains byte-for-byte equivalent
        # at object-field level, except this explicit audit metadata.
        output[index]["title"] = candidate
        output[index]["visible_title_repair"] = {
            "version": VERSION,
            "repaired": True,
            "original_title": original,
            "repaired_title": candidate,
            "trigger_reasons": list(source["reasons"]),
            "grounding_policy": "current_evidence_only",
            "changed_fields": ["title"],
        }
        repaired_count += 1

        print(
            f"{LOG} REPAIRED index={index} "
            f"from={original[:120]} -> to={candidate[:180]}",
            flush=True,
        )

    print(
        f"{LOG} SUMMARY suspicious={len(suspicious)} repaired={repaired_count}",
        flush=True,
    )
    return output
