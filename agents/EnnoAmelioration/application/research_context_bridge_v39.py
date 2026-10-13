from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

_ADMIN_ENTITIES = {
    "CIR", "R&D", "RD", "NLP", "RAG", "LLM", "API", "PDF", "DOCX",
    "JSON", "SQL", "HTTP", "HTTPS", "UI", "UX",
}
_STOP = {
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


def extract_section_entities(value: Any, max_items: int = 20) -> list[str]:
    raw = str(value or "")
    output: list[str] = []
    seen: set[str] = set()

    def add(item: str) -> None:
        item = _clean(item, 160).strip(" ,;:.")
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
    return output[:max_items]


def extract_section_terms(value: Any, max_items: int = 18) -> list[str]:
    normalized = re.sub(r"[^A-Za-zÀ-ÿ0-9+/_-]+", " ", str(value or "").casefold())
    order: list[str] = []
    counts: Counter[str] = Counter()
    for token in normalized.split():
        token = token.strip("-_/")
        folded = _norm(token)
        if not folded or folded in _STOP or folded.isdigit() or len(folded) < 4:
            continue
        if folded not in counts:
            order.append(token)
        counts[folded] += 1
    return sorted(
        order,
        key=lambda token: (-counts[_norm(token)], -len(token), order.index(token)),
    )[:max_items]


def _query(parts: list[str], max_words: int = 12) -> str:
    words: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9+/_-]+", str(part or "")):
            key = _norm(token)
            if not key or key in seen or key in _STOP:
                continue
            seen.add(key)
            words.append(token)
            if len(words) >= max_words:
                return _clean(" ".join(words), 220)
    return _clean(" ".join(words), 220)


def build_section_queries(section_text: str, section_title: str, entities: list[str]) -> list[dict[str, Any]]:
    long_entities = [
        value for value in entities
        if len(re.sub(r"[^A-Za-z0-9]", "", value)) >= 4
    ]
    short_entities = [value for value in entities if value not in long_entities]
    title_terms = extract_section_terms(section_title, 8)
    body_terms = extract_section_terms(section_text, 12)

    output: list[dict[str, Any]] = []
    q1 = _query([*long_entities[:6], *short_entities[:4], *title_terms[:5]], 12)
    if len(q1.split()) >= 4:
        output.append({"query": q1, "kind": "active_section_exact_v3_9"})

    q2 = _query([*long_entities[:5], *short_entities[:3], *body_terms[:7]], 12)
    if len(q2.split()) >= 4 and _norm(q2) not in {_norm(x["query"]) for x in output}:
        output.append({"query": q2, "kind": "active_section_semantic_v3_9"})
    return output[:2]


def enrich_direct_research_context(
    request: Any,
    direct_context: dict[str, Any],
    *,
    conversation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use only the section already resolved from the current working version."""
    context = dict(direct_context or {})
    conversation_context = dict(conversation_context or {})

    section_text = str(getattr(request, "target_text", "") or "").strip()
    section_title = str(getattr(request, "target_section_title", "") or "").strip()
    section_id = getattr(request, "target_section_id", None)

    if len(section_text) < 35:
        raise RuntimeError(
            "La section n'a pas été résolue dans la version active. "
            "Aucun retour au PDF original ni au message consultant n'est autorisé."
        )

    entities = extract_section_entities(f"{section_title}\n{section_text}")
    terms = extract_section_terms(f"{section_title}\n{section_text}")
    queries = build_section_queries(section_text, section_title, entities)

    rc = dict(context.get("research_context") or {})
    for key in (
        "research_objective", "consultant_instruction", "consultant_focus_entities",
        "conversation_refinement", "local_context", "context_text",
        "section_context", "local_cir_context",
    ):
        rc.pop(key, None)

    rc.update({
        "context_contract_version": "v3_9_active_version_section_only",
        "scientific_source_policy": "resolved_working_version_target_text_only",
        "source_section_id": section_id,
        "source_section_title": section_title,
        "section_text": section_text[:20000],
        "section_text_chars": len(section_text),
        "section_entities": list(entities),
        "section_terms": list(terms),
        "base_version_id": conversation_context.get("base_version_id"),
        "base_version_number": conversation_context.get("base_version_number"),
        "base_version_status": conversation_context.get("base_version_status"),
        "base_version_is_active": conversation_context.get("base_version_is_active"),
        "consultant_message_role": "routing_only",
        "original_uploaded_document_used": False,
    })
    context["research_context"] = rc

    targets = []
    for row in context.get("research_targets") or []:
        if not isinstance(row, dict):
            continue
        target = dict(row)
        raw = dict(target.get("raw_item") or {})
        for key in (
            "consultant_instruction", "consultant_focus_entities",
            "research_refinement_context", "local_context", "context_text",
            "section_context",
        ):
            raw.pop(key, None)

        target["text"] = section_text
        raw["text"] = section_text
        raw["source_text"] = section_text
        raw["section_entities"] = list(entities)
        raw["section_terms"] = list(terms)
        raw["supporting_passages"] = []
        raw["version_source_contract"] = {
            "source": "resolved_working_version",
            "source_field": "request.target_text",
            "pdf_original": "history_only",
            "consultant_message": "routing_only",
        }
        target["raw_item"] = raw
        target["context"] = rc
        target["research_context"] = rc
        target["sources"] = []
        if queries:
            target["suggested_queries"] = [dict(item) for item in queries]
        targets.append(target)

    context["research_targets"] = targets
    context["v3_9_active_version_contract"] = {
        "source_section_id": section_id,
        "source_section_title": section_title,
        "section_text_chars": len(section_text),
        "section_entities": list(entities),
        "section_queries": [dict(item) for item in queries],
        "base_version_id": conversation_context.get("base_version_id"),
        "base_version_number": conversation_context.get("base_version_number"),
        "base_version_status": conversation_context.get("base_version_status"),
        "base_version_is_active": conversation_context.get("base_version_is_active"),
        "scientific_source_policy": "resolved_working_version_target_text_only",
    }
    return context


def build_research_conversation_context(
    session_context: dict[str, Any],
    *,
    previous_target_section_id: Any,
    current_target_section_id: Any,
    current_target_section_title: Any,
    consultant_feedback: str = "",
    base_version_id: Any = None,
    base_version_number: Any = None,
    base_version_status: Any = None,
    base_version_is_active: bool = True,
) -> dict[str, Any]:
    return {
        "same_target": bool(
            previous_target_section_id
            and current_target_section_id
            and str(previous_target_section_id) == str(current_target_section_id)
        ),
        "source_section_id": current_target_section_id,
        "source_section_title": current_target_section_title,
        "base_version_id": base_version_id,
        "base_version_number": base_version_number,
        "base_version_status": base_version_status,
        "base_version_is_active": bool(base_version_is_active),
        "policy": "active_version_is_source_of_truth_v3_9",
    }


def _candidate_text(candidate: dict[str, Any]) -> str:
    return _clean(" ".join([
        str(candidate.get("title") or ""),
        str(candidate.get("abstract") or ""),
        str(candidate.get("role_reason") or ""),
    ]), 16000)


def _anchor_present(text: str, anchor: str) -> bool:
    anchor = str(anchor or "").strip()
    if not anchor:
        return False
    if re.fullmatch(r"[A-Z][A-Z0-9]{2,}(?:[-_/][A-Z0-9]+)?", anchor):
        return bool(re.search(
            rf"(?<![A-Za-z0-9]){re.escape(anchor)}(?![A-Za-z0-9])",
            text,
        ))
    return _norm(anchor) in _norm(text)


def _concept_hit(text: str, concept: str, aliases: dict[str, Any]) -> bool:
    values = [concept]
    if isinstance(aliases.get(concept), list):
        values.extend(str(x) for x in aliases.get(concept) or [])
    return any(_anchor_present(text, value) for value in values)


def filter_candidates_against_section_context(
    candidates: list[dict[str, Any]],
    *,
    direct_context: dict[str, Any],
    search_metadata: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rc = dict(direct_context.get("research_context") or {})
    entities = [str(x) for x in (rc.get("section_entities") or []) if str(x or "").strip()]
    long_entities = [x for x in entities if len(re.sub(r"[^A-Za-z0-9]", "", x)) >= 4]
    short_entities = [x for x in entities if x not in long_entities]

    intent = dict(search_metadata.get("scientific_intent") or {})
    core = [
        str(x)
        for x in (intent.get("primary_core_concepts") or intent.get("core_concepts") or [])
        if str(x or "").strip()
    ]
    aliases = dict(intent.get("concept_aliases") or {})
    acronyms = [str(x) for x in (intent.get("literal_source_acronyms") or []) if str(x or "").strip()]

    if not (long_entities or len(short_entities) >= 2 or len(core) >= 2 or len(acronyms) >= 2):
        return candidates, {
            "enabled": False,
            "policy": "active_version_section_only_v3_9",
            "input_count": len(candidates),
            "kept_count": len(candidates),
            "removed_count": 0,
        }

    kept, removed = [], []
    for candidate in candidates:
        text = _candidate_text(candidate)
        long_hits = [x for x in long_entities if _anchor_present(text, x)]
        short_hits = [x for x in short_entities if _anchor_present(text, x)]
        core_hits = [x for x in core if _concept_hit(text, x, aliases)]
        acronym_hits = [x for x in acronyms if _anchor_present(text, x)]

        keep = bool(
            len(core_hits) >= 2
            or (long_hits and (core_hits or short_hits or acronym_hits))
            or (len(short_hits) >= 2 and (core_hits or acronym_hits))
        )
        enriched = {
            **candidate,
            "section_context_gate": {
                "long_hits": long_hits,
                "short_hits": short_hits,
                "core_hits": core_hits,
                "acronym_hits": acronym_hits,
            },
        }
        (kept if keep else removed).append(enriched)

    return kept, {
        "enabled": True,
        "policy": "active_version_section_only_v3_9",
        "scientific_source": "resolved_working_version_request.target_text",
        "consultant_message_used": False,
        "original_pdf_used": False,
        "input_count": len(candidates),
        "kept_count": len(kept),
        "removed_count": len(removed),
    }
