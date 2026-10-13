from __future__ import annotations

import re
import unicodedata
from typing import Any


_ADMIN_ENTITIES = {
    "CIR", "R&D", "RD", "NLP", "RAG", "LLM", "API", "PDF", "DOCX",
    "JSON", "SQL", "HTTP", "HTTPS", "UI", "UX",
}


def _clean(value: Any, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit and len(text) > limit else text


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip()


def extract_named_entities(value: Any, *, max_items: int = 16) -> list[str]:
    """Extrait dynamiquement les sigles/identifiants réellement présents.

    Aucun terme métier n'est codé en dur. Cela fonctionne donc pour MSTAR/SAMPLE,
    mais aussi pour n'importe quel autre dossier ou technologie.
    """

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


def build_focus_queries(
    *,
    focus_entities: list[str],
    section_entities: list[str],
    section_title: str,
) -> list[dict[str, Any]]:
    """Construit seulement des hints sûrs; EnnoScholar garde le contrôle final.

    Ces requêtes ne viennent jamais d'un catalogue métier : elles sont dérivées
    du message du consultant et du texte de la section courante.
    """

    if not focus_entities:
        return []

    anchors: list[str] = []
    for value in [*focus_entities, *section_entities]:
        value = _clean(value, 80)
        if value and _norm(value) not in {_norm(row) for row in anchors}:
            anchors.append(value)

    queries: list[dict[str, Any]] = []
    q1 = _clean(" ".join(anchors[:6]), 220)
    if q1:
        queries.append({
            "query": q1,
            "kind": "consultant_focus_exact_v3_7",
        })

    title = _clean(section_title, 120)
    q2 = _clean(" ".join([*focus_entities[:4], title]), 220)
    if q2 and _norm(q2) != _norm(q1):
        queries.append({
            "query": q2,
            "kind": "consultant_focus_section_v3_7",
        })
    return queries[:2]


def enrich_direct_research_context(
    request: Any,
    direct_context: dict[str, Any],
    *,
    conversation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ajoute le contexte complet de la section avant EnnoScholar."""

    context = dict(direct_context or {})
    conversation_context = dict(conversation_context or {})
    section_text = str(getattr(request, "target_text", "") or "").strip()
    section_title = str(getattr(request, "target_section_title", "") or "").strip()
    instruction = str(getattr(request, "instruction", "") or "").strip()

    focus_entities = extract_named_entities(instruction, max_items=12)
    section_entities = extract_named_entities(
        f"{section_title}\n{section_text}",
        max_items=18,
    )

    research_context = dict(context.get("research_context") or {})
    local_context = str(
        research_context.get("local_context")
        or research_context.get("context_text")
        or ""
    ).strip()
    research_context.update({
        "context_contract_version": "v3_7_exact_selected_section",
        "section_text": section_text[:18000],
        "section_text_chars": len(section_text),
        "section_context_priority": "exact_selected_section_is_primary",
        "local_cir_context": local_context[:12000],
        "local_cir_context_priority": "secondary_only",
        "consultant_instruction": instruction[:3500],
        "consultant_focus_entities": focus_entities,
        "section_entities": section_entities,
        "conversation_refinement": conversation_context,
    })
    context["research_context"] = research_context

    targets: list[dict[str, Any]] = []
    for row in context.get("research_targets") or []:
        if not isinstance(row, dict):
            continue
        target = dict(row)
        raw_item = dict(target.get("raw_item") or {})

        # Le texte de section reste la preuve locale principale. La précision
        # consultant est ajoutée comme instruction de recherche, pas comme fait.
        raw_item["text"] = section_text
        raw_item["source_text"] = section_text
        raw_item["consultant_instruction"] = instruction[:3500]
        raw_item["consultant_focus_entities"] = focus_entities
        raw_item["section_entities"] = section_entities
        raw_item["research_refinement_context"] = conversation_context
        raw_item["context_contract"] = {
            "primary": "exact_selected_section_text",
            "secondary": "local_cir_context",
            "focus": "current_consultant_instruction",
        }

        supporting = [
            dict(item)
            for item in (raw_item.get("supporting_passages") or [])
            if isinstance(item, dict)
        ]
        if local_context:
            supporting.append({
                "text": local_context[:7000],
                "role": "local_cir_neighbourhood_secondary",
            })
        if focus_entities:
            # Ce bloc sert uniquement à l'intention de recherche. Le moteur
            # scientifique le lira comme des ancres, jamais comme publication.
            supporting.append({
                "text": "Consultant research focus: " + " ; ".join(focus_entities),
                "role": "consultant_focus_not_evidence",
            })
        raw_item["supporting_passages"] = supporting[:8]
        target["raw_item"] = raw_item
        target["research_context"] = research_context
        target["context"] = research_context

        hints = build_focus_queries(
            focus_entities=focus_entities,
            section_entities=section_entities,
            section_title=section_title,
        )
        if hints:
            target["suggested_queries"] = hints
        targets.append(target)

    context["research_targets"] = targets
    context["v3_7_complex_context"] = {
        "focus_entities": focus_entities,
        "section_entities": section_entities,
        "section_text_chars": len(section_text),
        "conversation_refinement_used": bool(conversation_context.get("same_target")),
    }
    return context


def build_research_conversation_context(
    session_context: dict[str, Any],
    *,
    previous_target_section_id: Any,
    current_target_section_id: Any,
    current_target_section_title: Any,
    consultant_feedback: str,
) -> dict[str, Any]:
    """Réutilise la recherche précédente uniquement si la section est la même."""

    same_target = bool(
        previous_target_section_id
        and current_target_section_id
        and str(previous_target_section_id) == str(current_target_section_id)
    )
    if not same_target:
        return {
            "same_target": False,
            "consultant_feedback": consultant_feedback,
        }

    context = dict(session_context or {})
    handoff = context.get("scholar_handoff")
    handoff = dict(handoff) if isinstance(handoff, dict) else {}
    internal = handoff.get("internal_response")
    internal = dict(internal) if isinstance(internal, dict) else {}
    previous_sources = [
        dict(row)
        for row in (context.get("research_sources") or [])
        if isinstance(row, dict)
    ]

    return {
        "same_target": True,
        "source_section_id": current_target_section_id,
        "source_section_title": current_target_section_title,
        "previous_queries": list(internal.get("queries") or [])[:12],
        "previous_research_context": dict(internal.get("research_context") or {}),
        "previous_candidate_titles": [
            str(row.get("title") or "")
            for row in previous_sources[:12]
            if str(row.get("title") or "").strip()
        ],
        "consultant_feedback": consultant_feedback,
        "policy": "refine_previous_research_without_losing_selected_section",
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
    return _clean(" ".join(parts), 14000)


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
    """Élimine les cartes manifestement hors du contexte exact de la section."""

    research_context = dict(direct_context.get("research_context") or {})
    focus = [
        str(value) for value in research_context.get("consultant_focus_entities") or []
        if str(value or "").strip()
    ]
    intent = dict(search_metadata.get("scientific_intent") or {})
    core = [
        str(value) for value in (
            intent.get("primary_core_concepts")
            or intent.get("core_concepts")
            or []
        ) if str(value or "").strip()
    ]
    aliases = dict(intent.get("concept_aliases") or {})
    acronyms = [
        str(value) for value in intent.get("literal_source_acronyms") or []
        if str(value or "").strip()
    ]

    strict = bool(focus or len(core) >= 2 or len(acronyms) >= 2)
    if not strict:
        return candidates, {
            "enabled": False,
            "reason": "insufficient_distinctive_context_anchors",
            "input_count": len(candidates),
            "kept_count": len(candidates),
            "removed_count": 0,
        }

    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for candidate in candidates:
        text = _candidate_text(candidate)
        focus_hits = [value for value in focus if _anchor_present(text, value)]
        core_hits = [value for value in core if _concept_hit(text, value, aliases)]
        acronym_hits = [value for value in acronyms if _anchor_present(text, value)]

        if focus:
            keep = bool(focus_hits and (core_hits or acronym_hits))
        else:
            keep = bool(len(core_hits) >= 2 or len(acronym_hits) >= 2)

        enriched = {
            **candidate,
            "section_context_gate": {
                "focus_hits": focus_hits,
                "core_hits": core_hits,
                "acronym_hits": acronym_hits,
            },
        }
        (kept if keep else removed).append(enriched)

    return kept, {
        "enabled": True,
        "policy": "selected_section_context_plus_consultant_focus_v3_7",
        "explicit_focus": focus,
        "core_concepts": core,
        "literal_source_acronyms": acronyms,
        "input_count": len(candidates),
        "kept_count": len(kept),
        "removed_count": len(removed),
        "removed_examples": [
            {"title": row.get("title"), "gate": row.get("section_context_gate")}
            for row in removed[:8]
        ],
    }
