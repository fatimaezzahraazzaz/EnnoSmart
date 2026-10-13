from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

_ADMIN_ENTITIES = {
    "CIR", "R&D", "RD", "NLP", "RAG", "LLM", "API", "PDF", "DOCX",
    "JSON", "SQL", "HTTP", "HTTPS", "UI", "UX",
}

_TERM_STOPWORDS = {
    "afin", "ainsi", "alors", "avec", "avoir", "cette", "comme", "dans", "des",
    "donc", "elle", "elles", "entre", "est", "etre", "être", "faire", "leur",
    "leurs", "mais", "nous", "pour", "plus", "projet", "section", "selon",
    "sont", "sous", "sur", "texte", "tout", "toute", "toutes", "tous", "une",
    "vers", "vous", "from", "into", "that", "the", "their", "this", "with",
    "using", "based", "study", "paper", "article",
}


def _clean(value: Any, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit and len(text) > limit else text


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip()


def extract_named_entities(value: Any, *, max_items: int = 20) -> list[str]:
    raw = str(value or "")
    output: list[str] = []
    seen: set[str] = set()

    def add(item: str) -> None:
        item = _clean(item, 180).strip(" ,;:.")
        key = _norm(item)
        if not item or not key or item.upper() in _ADMIN_ENTITIES or key in seen:
            return
        seen.add(key)
        output.append(item)

    for token in re.findall(
        r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{2,}(?:[-_/][A-Z0-9]+)?)(?![A-Za-z0-9])",
        raw,
    ):
        add(token)
        if len(output) >= max_items:
            return output[:max_items]

    for quoted in re.findall(r'[«"“](.*?)[»"”]', raw):
        if 1 <= len(quoted.strip().split()) <= 8:
            add(quoted)
        if len(output) >= max_items:
            break
    return output[:max_items]


def extract_section_terms(value: Any, *, max_items: int = 16) -> list[str]:
    normalized = re.sub(
        r"[^A-Za-zÀ-ÿ0-9+/_-]+",
        " ",
        str(value or "").casefold(),
    )
    ordered: list[str] = []
    counts: Counter[str] = Counter()

    for token in normalized.split():
        token = token.strip("-_/")
        folded = _norm(token)
        if (
            not folded
            or folded in _TERM_STOPWORDS
            or folded.isdigit()
            or len(folded) < 4
        ):
            continue
        if folded not in counts:
            ordered.append(token)
        counts[folded] += 1

    ranked = sorted(
        ordered,
        key=lambda token: (
            -counts[_norm(token)],
            -len(token),
            ordered.index(token),
        ),
    )
    return ranked[:max_items]


def _dedupe(values: list[str], max_items: int = 12) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = _clean(value, 100)
        key = _norm(value)
        if not value or not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= max_items:
            break
    return out


def _query(parts: list[str], max_words: int = 12) -> str:
    words: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9+/_-]+", str(part or "")):
            key = _norm(token)
            if not key or key in seen or key in _TERM_STOPWORDS:
                continue
            seen.add(key)
            words.append(token)
            if len(words) >= max_words:
                return _clean(" ".join(words), 220)
    return _clean(" ".join(words), 220)


def build_section_queries(
    *,
    section_text: str,
    section_title: str,
    section_entities: list[str],
) -> list[dict[str, Any]]:
    title_terms = extract_section_terms(section_title, max_items=8)
    body_terms = extract_section_terms(section_text, max_items=12)

    strong = [
        value for value in section_entities
        if len(re.sub(r"[^A-Za-z0-9]", "", value)) >= 4
    ]
    short = [value for value in section_entities if value not in strong]
    entities = _dedupe([*strong, *short], 10)

    queries: list[dict[str, Any]] = []
    q1 = _query([*entities[:6], *title_terms[:5]], 12)
    if len(q1.split()) >= 4:
        queries.append({"query": q1, "kind": "section_source_exact_v3_8"})

    q2 = _query([*entities[:5], *body_terms[:7]], 12)
    if len(q2.split()) >= 4 and _norm(q2) not in {_norm(x["query"]) for x in queries}:
        queries.append({"query": q2, "kind": "section_source_semantic_v3_8"})
    return queries[:2]


def _sanitize_context(
    base: dict[str, Any],
    *,
    section_text: str,
    section_title: str,
    section_id: Any,
    section_entities: list[str],
    section_terms: list[str],
    same_target: bool,
) -> dict[str, Any]:
    context = dict(base or {})
    for key in (
        "research_objective",
        "consultant_instruction",
        "consultant_focus_entities",
        "conversation_refinement",
        "local_context",
        "context_text",
        "section_context",
        "local_cir_context",
    ):
        context.pop(key, None)

    context.update({
        "context_contract_version": "v3_8_selected_cir_section_only",
        "scientific_source_policy": "request_target_text_only",
        "section_text": section_text[:20000],
        "section_text_chars": len(section_text),
        "source_section_id": section_id,
        "source_section_title": section_title,
        "section_entities": list(section_entities),
        "section_terms": list(section_terms),
        "consultant_message_role": "routing_only",
        "consultant_message_used_for_scientific_keywords": False,
        "consultant_message_used_for_scientific_context": False,
        "local_cir_neighbourhood_used_for_scientific_context": False,
        "same_section_research_iteration": bool(same_target),
    })
    return context


def enrich_direct_research_context(
    request: Any,
    direct_context: dict[str, Any],
    *,
    conversation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scientific payload = exact selected section only.

    request.instruction is intentionally never read here.
    """

    context = dict(direct_context or {})
    conversation_context = dict(conversation_context or {})

    section_text = str(getattr(request, "target_text", "") or "").strip()
    section_title = str(getattr(request, "target_section_title", "") or "").strip()
    section_id = getattr(request, "target_section_id", None)

    if len(section_text) < 35:
        raise RuntimeError(
            "Le texte exact de la section sélectionnée n'a pas été récupéré. "
            "La recherche scientifique est arrêtée pour éviter une requête "
            "construite uniquement depuis le message ou le titre."
        )

    section_entities = extract_named_entities(
        f"{section_title}\n{section_text}",
        max_items=20,
    )
    section_terms = extract_section_terms(
        f"{section_title}\n{section_text}",
        max_items=18,
    )

    same_target = bool(conversation_context.get("same_target"))
    clean_context = _sanitize_context(
        dict(context.get("research_context") or {}),
        section_text=section_text,
        section_title=section_title,
        section_id=section_id,
        section_entities=section_entities,
        section_terms=section_terms,
        same_target=same_target,
    )
    context["research_context"] = clean_context

    suggested = build_section_queries(
        section_text=section_text,
        section_title=section_title,
        section_entities=section_entities,
    )

    targets: list[dict[str, Any]] = []
    for row in context.get("research_targets") or []:
        if not isinstance(row, dict):
            continue

        target = dict(row)
        raw = dict(target.get("raw_item") or {})

        for key in (
            "consultant_instruction",
            "consultant_focus_entities",
            "research_refinement_context",
            "local_context",
            "context_text",
            "section_context",
        ):
            raw.pop(key, None)

        target["text"] = section_text
        raw["text"] = section_text
        raw["source_text"] = section_text
        raw["section_entities"] = list(section_entities)
        raw["section_terms"] = list(section_terms)
        raw["supporting_passages"] = []
        raw["context_contract"] = {
            "scientific_source": "exact_selected_cir_section",
            "scientific_source_field": "request.target_text",
            "consultant_message": "routing_only",
            "local_document_neighbourhood": "not_scientific_input",
        }

        target["raw_item"] = raw
        target["research_context"] = clean_context
        target["context"] = clean_context
        target["sources"] = []
        if suggested:
            target["suggested_queries"] = [dict(x) for x in suggested]
        else:
            target.pop("suggested_queries", None)
        targets.append(target)

    context["research_targets"] = targets
    context["v3_8_source_section_contract"] = {
        "source_section_id": section_id,
        "source_section_title": section_title,
        "section_text_chars": len(section_text),
        "section_entities": list(section_entities),
        "section_terms": list(section_terms),
        "section_queries": [dict(x) for x in suggested],
        "same_target": same_target,
        "scientific_source_policy": "request_target_text_only",
        "consultant_message_role": "routing_only",
    }

    context["v3_7_complex_context"] = {
        "section_entities": list(section_entities),
        "section_text_chars": len(section_text),
        "scientific_source_policy": "request_target_text_only",
    }
    return context


def build_research_conversation_context(
    session_context: dict[str, Any],
    *,
    previous_target_section_id: Any,
    current_target_section_id: Any,
    current_target_section_title: Any,
    consultant_feedback: str = "",
) -> dict[str, Any]:
    """Operational memory only. consultant_feedback text is ignored."""

    same_target = bool(
        previous_target_section_id
        and current_target_section_id
        and str(previous_target_section_id) == str(current_target_section_id)
    )
    context = dict(session_context or {})
    previous_sources = [
        row for row in (context.get("research_sources") or [])
        if isinstance(row, dict)
    ]
    return {
        "same_target": same_target,
        "source_section_id": current_target_section_id,
        "source_section_title": current_target_section_title,
        "previous_candidate_count": len(previous_sources) if same_target else 0,
        "policy": "conversation_text_never_enters_scientific_search_v3_8",
    }


def _candidate_text(candidate: dict[str, Any]) -> str:
    parts = [
        str(candidate.get("title") or ""),
        str(candidate.get("abstract") or ""),
        str(candidate.get("role_reason") or ""),
    ]
    for row in candidate.get("raw_payloads") or []:
        if isinstance(row, dict):
            parts.extend(
                str(row.get(key) or "")
                for key in ("title", "abstract", "summary", "tldr", "reason")
            )
    return _clean(" ".join(parts), 16000)


def _anchor_present(text: str, anchor: str) -> bool:
    anchor = str(anchor or "").strip()
    if not anchor:
        return False

    if re.fullmatch(r"[A-Z][A-Z0-9]{2,}(?:[-_/][A-Z0-9]+)?", anchor):
        return bool(re.search(
            rf"(?<![A-Za-z0-9]){re.escape(anchor)}(?![A-Za-z0-9])",
            text,
        ))

    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(_norm(anchor))}(?![a-z0-9])",
        _norm(text),
    ))


def _concept_hit(text: str, concept: str, aliases: dict[str, Any]) -> bool:
    values = [concept]
    if isinstance(aliases.get(concept), list):
        values.extend(str(value) for value in aliases.get(concept) or [])
    return any(_anchor_present(text, value) for value in values)


def filter_candidates_against_section_context(
    candidates: list[dict[str, Any]],
    *,
    direct_context: dict[str, Any],
    search_metadata: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Final gate based only on the selected section."""

    rc = dict(direct_context.get("research_context") or {})
    section_entities = [
        str(v) for v in (rc.get("section_entities") or []) if str(v or "").strip()
    ]

    strong = [
        v for v in section_entities
        if len(re.sub(r"[^A-Za-z0-9]", "", v)) >= 4
    ]
    short = [v for v in section_entities if v not in strong]

    intent = dict(search_metadata.get("scientific_intent") or {})
    core = [
        str(v)
        for v in (
            intent.get("primary_core_concepts")
            or intent.get("core_concepts")
            or []
        )
        if str(v or "").strip()
    ]
    aliases = dict(intent.get("concept_aliases") or {})
    acronyms = [
        str(v) for v in (intent.get("literal_source_acronyms") or [])
        if str(v or "").strip()
    ]

    strict = bool(strong or len(core) >= 2 or len(short) >= 2 or len(acronyms) >= 2)
    if not strict:
        return candidates, {
            "enabled": False,
            "reason": "insufficient_distinctive_section_anchors",
            "policy": "selected_section_text_only_v3_8",
            "input_count": len(candidates),
            "kept_count": len(candidates),
            "removed_count": 0,
        }

    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for candidate in candidates:
        text = _candidate_text(candidate)
        strong_hits = [v for v in strong if _anchor_present(text, v)]
        short_hits = [v for v in short if _anchor_present(text, v)]
        core_hits = [v for v in core if _concept_hit(text, v, aliases)]
        acronym_hits = [v for v in acronyms if _anchor_present(text, v)]

        keep = bool(
            len(core_hits) >= 2
            or (strong_hits and (core_hits or short_hits or acronym_hits))
            or (len(short_hits) >= 2 and (core_hits or acronym_hits))
        )

        enriched = {
            **candidate,
            "section_context_gate": {
                "strong_section_entity_hits": strong_hits,
                "short_section_entity_hits": short_hits,
                "core_concept_hits": core_hits,
                "intent_acronym_hits": acronym_hits,
            },
        }
        (kept if keep else removed).append(enriched)

    return kept, {
        "enabled": True,
        "policy": "selected_section_text_only_v3_8",
        "scientific_source": "request.target_text",
        "consultant_message_used": False,
        "strong_section_entities": strong,
        "short_section_entities": short,
        "core_concepts": core,
        "input_count": len(candidates),
        "kept_count": len(kept),
        "removed_count": len(removed),
        "removed_examples": [
            {"title": x.get("title"), "gate": x.get("section_context_gate")}
            for x in removed[:10]
        ],
    }
