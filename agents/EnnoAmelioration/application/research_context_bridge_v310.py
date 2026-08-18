from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

from .research_text_normalizer import normalize_research_section_text


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


def _all_upper_entities(value: str) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()

    for token in re.findall(
        r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{2,}(?:[-_/][A-Z0-9]+)?)(?![A-Za-z0-9])",
        str(value or ""),
    ):
        key = _norm(token)
        if (
            not key
            or token.upper() in _ADMIN_ENTITIES
            or key in seen
        ):
            continue
        seen.add(key)
        output.append(token)
    return output


def _defined_entities(value: str) -> list[tuple[str, str]]:
    """Entités nommées suivies d'une expansion : ABC (Long Scientific Name)."""

    output: list[tuple[str, str]] = []
    seen: set[str] = set()

    pattern = re.compile(
        r"(?<![A-Za-z0-9])"
        r"(?P<name>[A-Z][A-Z0-9]{2,}(?:[-_/][A-Z0-9]+)?)"
        r"\s*\((?P<expansion>[^)\n]{8,220})\)"
    )

    for match in pattern.finditer(str(value or "")):
        name = match.group("name")
        expansion = " ".join(match.group("expansion").split())
        words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", expansion)
        if len(words) < 3:
            continue
        key = _norm(name)
        if name.upper() in _ADMIN_ENTITIES or key in seen:
            continue
        seen.add(key)
        output.append((name, expansion))

    return output


def _entity_count(text: str, entity: str) -> int:
    return len(
        re.findall(
            rf"(?<![A-Za-z0-9]){re.escape(entity)}(?![A-Za-z0-9])",
            text,
            flags=re.I,
        )
    )


def extract_research_entities(
    normalized_text: str,
    section_title: str,
    normalization_report: dict[str, Any],
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    """Sépare concepts principaux et instances secondaires.

    Règles structurelles :
    - entité définie par une expansion : prioritaire ;
    - entité du titre : prioritaire ;
    - si aucune définition/titre n'existe, entités récurrentes : fallback ;
    - codes d'une longue énumération : jamais prioritaires.
    """

    all_entities = _all_upper_entities(normalized_text)
    protected_codes = {
        str(value)
        for value in (normalization_report.get("protected_enumerated_codes") or [])
    }

    definitions = _defined_entities(normalized_text)
    defined_names = [name for name, _ in definitions]
    title_entities = _all_upper_entities(section_title)

    primary: list[str] = []
    seen: set[str] = set()

    def add_primary(value: str) -> None:
        key = _norm(value)
        if (
            not value
            or value in protected_codes
            or key in seen
        ):
            return
        seen.add(key)
        primary.append(value)

    for value in defined_names:
        add_primary(value)
    for value in title_entities:
        add_primary(value)

    # Fallback générique seulement si la structure ne définit rien.
    if not primary:
        for value in all_entities:
            if (
                value not in protected_codes
                and _entity_count(normalized_text, value) >= 2
            ):
                add_primary(value)

    secondary = [
        value
        for value in all_entities
        if _norm(value) not in {_norm(x) for x in primary}
        and value not in protected_codes
    ]

    definition_payload = [
        {"name": name, "expansion": expansion}
        for name, expansion in definitions
    ]
    return primary[:12], secondary[:24], definition_payload[:12]


def extract_section_terms(value: Any, max_items: int = 18) -> list[str]:
    normalized = re.sub(
        r"[^A-Za-zÀ-ÿ0-9+/_-]+",
        " ",
        str(value or "").casefold(),
    )
    order: list[str] = []
    counts: Counter[str] = Counter()

    for token in normalized.split():
        token = token.strip("-_/")
        folded = _norm(token)
        if (
            not folded
            or folded in _STOP
            or folded.isdigit()
            or len(folded) < 4
        ):
            continue
        if folded not in counts:
            order.append(token)
        counts[folded] += 1

    return sorted(
        order,
        key=lambda token: (
            -counts[_norm(token)],
            -len(token),
            order.index(token),
        ),
    )[:max_items]


def _query(parts: list[str], max_words: int = 12) -> str:
    words: list[str] = []
    seen: set[str] = set()

    for part in parts:
        for token in re.findall(
            r"[A-Za-zÀ-ÿ0-9+/_-]+",
            str(part or ""),
        ):
            key = _norm(token)
            if (
                not key
                or key in seen
                or key in _STOP
            ):
                continue
            seen.add(key)
            words.append(token)
            if len(words) >= max_words:
                return _clean(" ".join(words), 220)

    return _clean(" ".join(words), 220)


def build_section_queries(
    normalized_text: str,
    section_title: str,
    primary_entities: list[str],
    definitions: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Construit au plus 3 requêtes à fort rappel depuis les objets principaux."""

    output: list[dict[str, Any]] = []
    title_terms = extract_section_terms(section_title, 6)
    body_terms = extract_section_terms(normalized_text, 12)

    definition_by_name = {
        row["name"]: row["expansion"]
        for row in definitions
        if row.get("name") and row.get("expansion")
    }

    # Une requête par entité réellement définie, maximum deux.
    for entity in primary_entities:
        if len(output) >= 2:
            break
        expansion = definition_by_name.get(entity, "")
        query = _query(
            [entity, expansion, *title_terms[:4]],
            max_words=12,
        )
        if len(query.split()) < 3:
            continue
        output.append({
            "query": query,
            "kind": "active_section_exact_v3_9",
            "research_normalization": "v3_10",
        })

    # Axe conjoint : objets principaux + termes fréquents du passage.
    joint = _query(
        [*primary_entities[:5], *body_terms[:8]],
        max_words=12,
    )
    if (
        len(joint.split()) >= 4
        and _norm(joint) not in {_norm(row["query"]) for row in output}
    ):
        output.append({
            "query": joint,
            "kind": "active_section_semantic_v3_9",
            "research_normalization": "v3_10",
        })

    # Si aucune définition n'était présente, conserver un fallback sectionnel.
    if not output:
        fallback = _query(
            [*primary_entities[:5], *title_terms[:5], *body_terms[:6]],
            max_words=12,
        )
        if len(fallback.split()) >= 4:
            output.append({
                "query": fallback,
                "kind": "active_section_semantic_v3_9",
                "research_normalization": "v3_10",
            })

    return output[:3]


def enrich_direct_research_context(
    request: Any,
    direct_context: dict[str, Any],
    *,
    conversation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = dict(direct_context or {})
    conversation_context = dict(conversation_context or {})

    raw_active_section = str(
        getattr(request, "target_text", "") or ""
    ).strip()
    section_title = str(
        getattr(request, "target_section_title", "") or ""
    ).strip()
    section_id = getattr(request, "target_section_id", None)

    if len(raw_active_section) < 35:
        raise RuntimeError(
            "La section n'a pas été résolue dans la version active. "
            "Aucun retour au PDF original ou au message consultant n'est autorisé."
        )

    normalized_section, normalization_report = (
        normalize_research_section_text(raw_active_section)
    )

    if len(normalized_section) < 35:
        raise RuntimeError(
            "Le nettoyage de recherche a retiré trop de contenu. "
            "La recherche est arrêtée pour préserver la fiabilité."
        )

    primary_entities, secondary_entities, definitions = (
        extract_research_entities(
            normalized_section,
            section_title,
            normalization_report,
        )
    )
    section_terms = extract_section_terms(
        f"{section_title}\n{normalized_section}"
    )
    queries = build_section_queries(
        normalized_section,
        section_title,
        primary_entities,
        definitions,
    )

    print(
        "[EnnoAmel][ResearchNormalize]"
        f" raw_chars={len(raw_active_section)}"
        f" normalized_chars={len(normalized_section)}"
        f" refs_removed={normalization_report.get('removed_reference_blocks', 0)}"
        f" repairs={normalization_report.get('inline_marker_repairs', [])}"
        f" primary_entities={primary_entities}"
        f" queries={[row.get('query') for row in queries]}"
    )

    rc = dict(context.get("research_context") or {})
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
        rc.pop(key, None)

    rc.update({
        "context_contract_version": "v3_10_active_version_research_normalized",
        "scientific_source_policy": "normalized_resolved_active_version_section",
        "source_section_id": section_id,
        "source_section_title": section_title,
        "section_text": normalized_section[:20000],
        "section_text_chars": len(normalized_section),
        "section_entities": list(primary_entities),
        "section_secondary_entities": list(secondary_entities),
        "section_entity_definitions": definitions,
        "section_terms": list(section_terms),
        "research_normalization": normalization_report,
        "base_version_id": conversation_context.get("base_version_id"),
        "base_version_number": conversation_context.get("base_version_number"),
        "base_version_status": conversation_context.get("base_version_status"),
        "base_version_is_active": conversation_context.get("base_version_is_active"),
        "consultant_message_role": "routing_only",
        "original_uploaded_document_used": False,
        # Traçabilité sans réinjecter le texte brut dans le moteur scientifique.
        "active_section_raw_sha256": normalization_report.get("raw_sha256"),
        "active_section_normalized_sha256": normalization_report.get("normalized_sha256"),
    })
    context["research_context"] = rc

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

        # IMPORTANT : EnnoScholar reçoit la copie normalisée, pas le texte
        # actif brut. La version active en base n'est jamais modifiée.
        target["text"] = normalized_section
        raw["text"] = normalized_section
        raw["source_text"] = normalized_section
        raw["section_entities"] = list(primary_entities)
        raw["section_secondary_entities"] = list(secondary_entities)
        raw["section_entity_definitions"] = definitions
        raw["section_terms"] = list(section_terms)
        raw["supporting_passages"] = []
        raw["research_normalization"] = normalization_report
        raw["version_source_contract"] = {
            "source": "active_version_resolved_section",
            "scientific_view": "normalized_copy",
            "active_version_modified": False,
            "pdf_original": "history_only",
            "consultant_message": "routing_only",
        }

        target["raw_item"] = raw
        target["context"] = rc
        target["research_context"] = rc
        target["sources"] = []

        if queries:
            target["suggested_queries"] = [
                dict(item)
                for item in queries
            ]

        targets.append(target)

    context["research_targets"] = targets
    context["v3_10_research_normalization_contract"] = {
        "source_section_id": section_id,
        "source_section_title": section_title,
        "base_version_id": conversation_context.get("base_version_id"),
        "base_version_number": conversation_context.get("base_version_number"),
        "base_version_status": conversation_context.get("base_version_status"),
        "base_version_is_active": conversation_context.get("base_version_is_active"),
        "primary_entities": list(primary_entities),
        "secondary_entities": list(secondary_entities),
        "definitions": definitions,
        "queries": [dict(item) for item in queries],
        "normalization": normalization_report,
        "active_version_modified": False,
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
            and str(previous_target_section_id)
            == str(current_target_section_id)
        ),
        "source_section_id": current_target_section_id,
        "source_section_title": current_target_section_title,
        "base_version_id": base_version_id,
        "base_version_number": base_version_number,
        "base_version_status": base_version_status,
        "base_version_is_active": bool(base_version_is_active),
        "policy": "active_version_normalized_research_view_v3_10",
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
                for key in (
                    "title",
                    "abstract",
                    "summary",
                    "tldr",
                    "reason",
                )
            )
    return _clean(" ".join(parts), 16000)


def _anchor_present(text: str, anchor: str) -> bool:
    anchor = str(anchor or "").strip()
    if not anchor:
        return False

    if re.fullmatch(
        r"[A-Z][A-Z0-9]{2,}(?:[-_/][A-Z0-9]+)?",
        anchor,
    ):
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9]){re.escape(anchor)}(?![A-Za-z0-9])",
                text,
            )
        )

    return _norm(anchor) in _norm(text)


def _concept_hit(
    text: str,
    concept: str,
    aliases: dict[str, Any],
) -> bool:
    values = [concept]
    if isinstance(aliases.get(concept), list):
        values.extend(
            str(item)
            for item in aliases.get(concept) or []
        )
    return any(
        _anchor_present(text, value)
        for value in values
    )


def filter_candidates_against_section_context(
    candidates: list[dict[str, Any]],
    *,
    direct_context: dict[str, Any],
    search_metadata: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Garde-fou final : concepts principaux uniquement, pas les codes d'instances."""

    rc = dict(
        direct_context.get("research_context") or {}
    )
    primary_entities = [
        str(value)
        for value in (rc.get("section_entities") or [])
        if str(value or "").strip()
    ]

    intent = dict(
        search_metadata.get("scientific_intent") or {}
    )
    core = [
        str(value)
        for value in (
            intent.get("primary_core_concepts")
            or intent.get("core_concepts")
            or []
        )
        if str(value or "").strip()
    ]
    aliases = dict(intent.get("concept_aliases") or {})

    if not primary_entities and len(core) < 2:
        return candidates, {
            "enabled": False,
            "reason": "insufficient_primary_section_context",
            "policy": "normalized_active_section_v3_10",
            "input_count": len(candidates),
            "kept_count": len(candidates),
            "removed_count": 0,
        }

    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for candidate in candidates:
        text = _candidate_text(candidate)

        entity_hits = [
            value
            for value in primary_entities
            if _anchor_present(text, value)
        ]
        core_hits = [
            value
            for value in core
            if _concept_hit(text, value, aliases)
        ]

        # On veut éviter "SAR uniquement" tout en conservant les travaux qui
        # couvrent réellement deux axes scientifiques même s'ils ne citent pas
        # le nom exact d'une base.
        keep = bool(
            len(core_hits) >= 2
            or len(entity_hits) >= 2
            or (
                entity_hits
                and core_hits
            )
        )

        enriched = {
            **candidate,
            "section_context_gate": {
                "primary_entity_hits": entity_hits,
                "core_concept_hits": core_hits,
            },
        }
        (kept if keep else removed).append(enriched)

    return kept, {
        "enabled": True,
        "policy": "normalized_active_section_v3_10",
        "scientific_source": "normalized_active_version_section",
        "consultant_message_used": False,
        "original_pdf_used": False,
        "primary_entities": primary_entities,
        "core_concepts": core,
        "input_count": len(candidates),
        "kept_count": len(kept),
        "removed_count": len(removed),
        "removed_examples": [
            {
                "title": row.get("title"),
                "gate": row.get("section_context_gate"),
            }
            for row in removed[:10]
        ],
    }
