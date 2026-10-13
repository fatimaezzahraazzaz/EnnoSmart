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
    rows = [
        dict(row)
        for row in (context.get("research_targets") or [])
        if isinstance(row, dict)
    ]
    if not rows:
        raise RuntimeError(
            "Aucune cible scientifique sectionnelle n'a ete construite pour la recherche."
        )

    shared_context = dict(context.get("research_context") or {})
    for key in (
        "consultant_instruction",
        "consultant_focus_entities",
        "conversation_refinement",
        "local_context",
        "context_text",
        "section_context",
        "local_cir_context",
        "section_text",
        "section_terms",
        "section_entities",
        "section_secondary_entities",
        "section_entity_definitions",
    ):
        shared_context.pop(key, None)

    targets: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    request_text = str(getattr(request, "target_text", "") or "").strip()
    request_title = str(
        getattr(request, "target_section_title", "") or ""
    ).strip()
    request_id = getattr(request, "target_section_id", None)

    for index, row in enumerate(rows):
        target = dict(row)
        raw = dict(target.get("raw_item") or {})
        target_id = (
            target.get("research_target_id")
            or target.get("target_id")
            or request_id
            or f"research-target-{index + 1}"
        )
        target_title = str(
            target.get("research_target_title")
            or target.get("title")
            or raw.get("source_section_title")
            or request_title
            or target_id
        ).strip()
        raw_active_section = str(
            target.get("text")
            or raw.get("source_text")
            or raw.get("text")
            or (request_text if len(rows) == 1 else "")
        ).strip()

        if len(raw_active_section) < 35:
            raise RuntimeError(
                f"La cible scientifique {target_id} n'a pas ete resolue dans la version active. "
                "Aucun retour au PDF original ou au message consultant n'est autorise."
            )

        normalized_section, normalization_report = normalize_research_section_text(
            raw_active_section
        )
        if len(normalized_section) < 35:
            raise RuntimeError(
                f"Le nettoyage de la cible {target_id} a retire trop de contenu. "
                "La recherche est arretee pour preserver la fiabilite."
            )

        source_target_context = dict(
            target.get("research_context") or target.get("context") or {}
        )
        raw_parent_section = str(
            target.get("parent_section_text")
            or raw.get("parent_section_text")
            or source_target_context.get("parent_section_text")
            or normalized_section
        ).strip()
        normalized_parent_section, parent_normalization_report = (
            normalize_research_section_text(raw_parent_section)
        )
        if len(normalized_parent_section) < 35:
            normalized_parent_section = normalized_section
            parent_normalization_report = dict(normalization_report)
        research_objective = str(
            source_target_context.get("research_objective")
            or raw.get("research_objective")
            or shared_context.get("research_objective")
            or ""
        ).strip()

        primary_entities, secondary_entities, definitions = extract_research_entities(
            normalized_section,
            target_title,
            normalization_report,
        )
        section_terms = extract_section_terms(
            f"{target_title}\n{normalized_section}"
        )
        queries = build_section_queries(
            normalized_section,
            target_title,
            primary_entities,
            definitions,
        )

        target_context = dict(source_target_context)
        for key in (
            "consultant_instruction",
            "consultant_focus_entities",
            "conversation_refinement",
            "local_context",
            "context_text",
            "section_context",
            "local_cir_context",
        ):
            target_context.pop(key, None)
        target_context.update({
            **shared_context,
            "context_contract_version": "v3_11_target_scoped_research_normalized",
            "scientific_source_policy": "normalized_resolved_active_version_target",
            "source_section_id": target_id,
            "source_section_title": target_title,
            "section_text": normalized_section[:20000],
            "section_text_chars": len(normalized_section),
            "parent_section_text": normalized_parent_section[:20000],
            "parent_section_text_chars": len(normalized_parent_section),
            "research_objective": research_objective[:3500],
            "research_objective_role": "semantic_constraint_not_query_vocabulary",
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
            "active_section_raw_sha256": normalization_report.get("raw_sha256"),
            "active_section_normalized_sha256": normalization_report.get("normalized_sha256"),
            "parent_section_normalized_sha256": parent_normalization_report.get("normalized_sha256"),
        })

        for key in (
            "consultant_instruction",
            "consultant_focus_entities",
            "research_refinement_context",
            "local_context",
            "context_text",
            "section_context",
        ):
            raw.pop(key, None)
        raw.update({
            "text": normalized_section,
            "source_text": normalized_section,
            "parent_section_text": normalized_parent_section[:20000],
            "research_objective": research_objective[:3500],
            "section_entities": list(primary_entities),
            "section_secondary_entities": list(secondary_entities),
            "section_entity_definitions": definitions,
            "section_terms": list(section_terms),
            "supporting_passages": [],
            "research_normalization": normalization_report,
            "parent_section_normalization": parent_normalization_report,
            "version_source_contract": {
                "source": "active_version_resolved_target",
                "scientific_view": "normalized_copy",
                "active_version_modified": False,
                "pdf_original": "history_only",
                "consultant_message": "routing_only",
            },
        })

        target.update({
            "research_target_id": target_id,
            "title": target_title,
            "text": normalized_section,
            "raw_item": raw,
            "context": target_context,
            "research_context": target_context,
            "sources": [],
            "suggested_queries": [dict(item) for item in queries],
        })
        targets.append(target)
        contracts.append({
            "source_section_id": target_id,
            "source_section_title": target_title,
            "primary_entities": list(primary_entities),
            "secondary_entities": list(secondary_entities),
            "definitions": definitions,
            "section_terms": list(section_terms),
            "queries": [dict(item) for item in queries],
            "normalization": normalization_report,
            "parent_section_normalization": parent_normalization_report,
            "parent_section_chars": len(normalized_parent_section),
            "research_objective": research_objective[:3500],
            "active_version_modified": False,
        })

        print(
            "[EnnoAmel][ResearchNormalize]"
            f" target={target_id}"
            f" raw_chars={len(raw_active_section)}"
            f" normalized_chars={len(normalized_section)}"
            f" refs_removed={normalization_report.get('removed_reference_blocks', 0)}"
            f" primary_entities={primary_entities}"
            f" queries={[item.get('query') for item in queries]}"
        )

    context["research_targets"] = targets
    if len(targets) == 1:
        context["research_context"] = dict(targets[0].get("research_context") or {})
    else:
        context["research_context"] = {
            **shared_context,
            "context_contract_version": "v3_11_multi_target_research_normalized",
            "context_kind": "target_scoped_research_collection",
            "source_section_ids": [row.get("research_target_id") for row in targets],
            "target_count": len(targets),
            "consultant_message_role": "routing_only",
            "original_uploaded_document_used": False,
        }

    first = contracts[0]
    context["v3_10_research_normalization_contract"] = {
        **first,
        "contract_version": "v3_11_target_scoped_research_normalized",
        "base_version_id": conversation_context.get("base_version_id"),
        "base_version_number": conversation_context.get("base_version_number"),
        "base_version_status": conversation_context.get("base_version_status"),
        "base_version_is_active": conversation_context.get("base_version_is_active"),
        "target_count": len(contracts),
        "targets": contracts,
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


def _candidate_score_details(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for row in candidate.get("raw_payloads") or []:
        if not isinstance(row, dict):
            continue
        value = row.get("score_details")
        if isinstance(value, dict):
            details.append(dict(value))
    return details


def _ranker_role_gate(candidate: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Reutilise le contrat objet + axe produit pour la cible courante."""

    try:
        relevance = float(candidate.get("relevance_score") or 0.0)
    except (TypeError, ValueError):
        relevance = 0.0
    tag = str(candidate.get("tag") or "")
    decisions: list[dict[str, Any]] = []

    for details in _candidate_score_details(candidate):
        object_hit = bool(details.get("object_role_hit"))
        relation_hit = bool(
            details.get("relation_evidence") or details.get("problem_evidence")
        )
        support_count = int(details.get("support_role_count") or 0)
        contradiction = bool(details.get("domain_contradiction"))
        core_count = int(details.get("core_concept_hit_count") or 0)
        role_hits = details.get("role_hits") if isinstance(details.get("role_hits"), dict) else {}
        axis_hit = bool(
            support_count >= 1
            or role_hits.get("phenomena")
            or role_hits.get("methods")
            or role_hits.get("validation")
        )
        keep = False
        policy = "role_contract_rejected"
        if not contradiction and tag == "Direct":
            keep = bool(
                object_hit and relation_hit and support_count >= 2 and relevance >= 0.45
            )
            policy = "direct_object_relation_two_axes"
        elif not contradiction and tag == "Connexe":
            keep = bool(object_hit and axis_hit and relevance >= 0.32)
            policy = "connected_same_object_one_axis"
        elif not contradiction and tag == "Fondamental":
            keep = bool(core_count >= 1 and axis_hit and relevance >= 0.25)
            policy = "fundamental_core_and_scientific_axis"
        decisions.append({
            "keep": keep,
            "policy": policy,
            "tag": tag,
            "relevance_score": relevance,
            "object_hit": object_hit,
            "relation_hit": relation_hit,
            "support_role_count": support_count,
            "domain_contradiction": contradiction,
        })
        if keep:
            return True, decisions[-1]
    return False, (decisions[0] if decisions else {
        "keep": False,
        "policy": "missing_scientific_role_contract",
        "tag": tag,
        "relevance_score": relevance,
    })


def filter_candidates_against_section_context(
    candidates: list[dict[str, Any]],
    *,
    direct_context: dict[str, Any],
    search_metadata: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Garde-fou final cible par cible, fonde sur objet + axe scientifique."""

    target_by_id = {
        str(row.get("research_target_id") or ""): dict(row)
        for row in (direct_context.get("research_targets") or [])
        if isinstance(row, dict) and str(row.get("research_target_id") or "").strip()
    }
    intents_by_target = {
        str(key): dict(value)
        for key, value in (
            search_metadata.get("scientific_intents_by_target") or {}
        ).items()
        if isinstance(value, dict)
    }

    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for candidate in candidates:
        text = _candidate_text(candidate)
        candidate_target_ids = [
            str(value)
            for value in (candidate.get("research_target_ids") or [])
            if str(value or "").strip()
        ]
        if not candidate_target_ids and len(target_by_id) == 1:
            candidate_target_ids = list(target_by_id)
        role_keep, role_decision = _ranker_role_gate(candidate)
        bindings: list[dict[str, Any]] = []
        lexical_fallback = False

        for target_id in candidate_target_ids:
            target = target_by_id.get(target_id) or {}
            target_context = dict(
                target.get("research_context") or target.get("context") or {}
            )
            intent = intents_by_target.get(target_id) or {}
            primary_entities = [
                str(value)
                for value in (target_context.get("section_entities") or [])
                if str(value or "").strip()
            ]
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
            entity_hits = [
                value for value in primary_entities if _anchor_present(text, value)
            ]
            core_hits = [
                value for value in core if _concept_hit(text, value, aliases)
            ]
            lexical_match = bool(
                len(core_hits) >= 2
                or len(entity_hits) >= 2
                or (entity_hits and core_hits)
            )
            lexical_fallback = lexical_fallback or lexical_match
            bindings.append({
                "research_target_id": target_id,
                "research_target_title": target.get("title"),
                "parent_section_id": target.get("parent_section_id") or target_id,
                "parent_section_title": target.get("parent_section_title") or target.get("title"),
                "source_passage_index": target.get("source_passage_index"),
                "primary_entity_hits": entity_hits,
                "core_concept_hits": core_hits,
                "match_policy": (
                    role_decision.get("policy") if role_keep else "lexical_fallback"
                ),
            })

        keep = bool(role_keep or lexical_fallback)
        parent_section_ids = list(dict.fromkeys(
            str(row.get("parent_section_id") or "")
            for row in bindings
            if str(row.get("parent_section_id") or "").strip()
        ))

        enriched = {
            **candidate,
            "section_ids": list(dict.fromkeys([
                *[str(value) for value in (candidate.get("section_ids") or []) if str(value or "").strip()],
                *parent_section_ids,
            ])),
            "target_bindings": bindings,
            "section_context_gate": {
                "accepted": keep,
                "role_contract": role_decision,
                "lexical_fallback": lexical_fallback,
                "target_bindings": bindings,
            },
        }
        (kept if keep else removed).append(enriched)

    return kept, {
        "enabled": True,
        "policy": "target_scoped_object_plus_scientific_axis_v3_11",
        "scientific_source": "normalized_active_version_target",
        "consultant_message_used": False,
        "original_pdf_used": False,
        "target_count": len(target_by_id),
        "target_ids": list(target_by_id),
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
