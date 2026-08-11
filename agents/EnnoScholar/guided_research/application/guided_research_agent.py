# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import inspect
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy.orm import Session

from modules.LLM.llm_client import LLMClient

from ...contracts import normalize_plan_sections, plan_hash
from ...consultant_plan_service import (
    approve_plan,
    authorize_writing,
    create_contract,
    propose_from_phase47,
    read_json,
    update_edited_plan,
    write_json,
)
from ...storage_paths import (
    consultant_plan_path,
    guided_sources_path,
    slug as storage_slug,
    state_of_art_root,
)
from ..lot1.domain.enums import (
    ConsultantIntent,
    ConversationRole,
    GuidedResearchEntryModule,
    GuidedResearchState,
    GuidedResearchTargetMode,
    NextAction,
    RequestedDepth,
    RequestedEntityType,
)
from ..lot1.domain.models import (
    ConsultantBrief,
    ConversationUnderstanding,
    ConversationResponse,
    GuidedResearchSessionData,
    IntentClassification,
    RequestedSection,
    RequestedTopic,
)
from ..lot1.conversation_understanding_service import (
    ConversationUnderstandingService,
    _ground_writing_source_policy,
)
from ..lot1.session_state_manager import GuidedResearchSessionStateManager
from .session_repository import GuidedResearchSessionRepository
from .web_research_service import WebResearchService


def _clean(value: Any, limit: int = 12000) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[:limit]


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9+#./-]+", " ", text).strip()


_PLAN_ACTION_INTENTS = {
    ConsultantIntent.PROPOSE_PLAN,
    ConsultantIntent.ADD_TOPIC,
    ConsultantIntent.REMOVE_TOPIC,
    ConsultantIntent.CHANGE_PLAN,
}
_SEARCH_ACTION_INTENTS = {
    ConsultantIntent.SEARCH_MORE,
    ConsultantIntent.SEARCH_ALTERNATIVE,
    ConsultantIntent.REPLACE_SOURCE,
    ConsultantIntent.ADD_VERROU_AND_SEARCH,
}
_RESPONSE_ONLY_INTENTS = {
    ConsultantIntent.CONVERSE,
    ConsultantIntent.UNKNOWN,
    ConsultantIntent.EXPLAIN_SOURCE,
}
_SUPPORTED_MESSAGE_INTENTS = (
    _PLAN_ACTION_INTENTS
    | _SEARCH_ACTION_INTENTS
    | _RESPONSE_ONLY_INTENTS
    | {
        ConsultantIntent.DESCRIBE_REQUIREMENTS,
        ConsultantIntent.ACCEPT_PLAN,
        ConsultantIntent.START_WRITING,
        ConsultantIntent.REVISE_DRAFT,
        ConsultantIntent.CANCEL,
    }
)


def _fallback_intent_classification(message: str) -> IntentClassification:
    return IntentClassification(
        intent=ConsultantIntent.UNKNOWN,
        confidence=0.0,
        rationale=(
            "La compréhension structurée n'a pas produit de décision valide ; "
            "aucune action n'est autorisée."
        ),
        requested_actions=[],
        forbidden_actions=[],
        needs_clarification=True,
        corrected_message=_clean(message),
        extracted_text=_clean(message),
        classifier="safe_no_action_fallback",
    )


def _resolve_routed_intent(
    classification: IntentClassification,
) -> ConsultantIntent:
    """Route l'intention normalisée sans rejeter les incohérences secondaires."""
    intent = classification.intent
    if intent not in _SUPPORTED_MESSAGE_INTENTS:
        return ConsultantIntent.UNKNOWN
    if classification.needs_clarification and intent != ConsultantIntent.CONVERSE:
        return ConsultantIntent.UNKNOWN
    if intent in classification.forbidden_actions:
        return ConsultantIntent.UNKNOWN
    return intent


def _resolve_effective_writing_source_identifiers(
    current_identifiers: Iterable[str] | None,
    stored_identifiers: Iterable[str] | None,
    requested_count: int | None,
) -> list[str]:
    """Préserve le corpus validé quand le dernier tour n'en cite qu'un extrait."""

    def normalized(values: Iterable[str] | None) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            cleaned = _clean(value, 500)
            key = cleaned.casefold()
            if not cleaned or key in seen:
                continue
            seen.add(key)
            output.append(cleaned)
        return output

    current = normalized(current_identifiers)
    stored = normalized(stored_identifiers)
    if (
        requested_count is not None
        and requested_count > 0
        and len(stored) == requested_count
        and len(current) != requested_count
    ):
        return stored
    return current or stored


def _resolve_candidate_display_identifiers(
    identifiers: Iterable[str],
    current_candidate_ids: Iterable[str] | None,
) -> list[str]:
    """Convertit C1/C2 du dernier résultat en identifiants persistants."""
    candidates = [
        _clean(value, 500)
        for value in (current_candidate_ids or [])
        if _clean(value, 500)
    ]
    resolved: list[str] = []
    seen: set[str] = set()
    for raw in identifiers:
        value = _clean(raw, 500)
        match = re.fullmatch(r"C\s*(\d{1,4})", value, flags=re.I)
        if match:
            index = int(match.group(1)) - 1
            if 0 <= index < len(candidates):
                value = candidates[index]
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            resolved.append(value)
    return resolved


def _reconcile_contextual_intent(
    classification: IntentClassification,
) -> IntentClassification:
    """Normalise les champs techniques dérivables de l'intention principale."""
    intent = classification.intent
    classification.requested_actions = list(
        dict.fromkeys(classification.requested_actions or [])
    )
    classification.forbidden_actions = [
        action
        for action in dict.fromkeys(classification.forbidden_actions or [])
        if action != intent
    ]

    if intent in {ConsultantIntent.CONVERSE, ConsultantIntent.UNKNOWN}:
        classification.requested_actions = []
    elif intent not in classification.requested_actions:
        classification.requested_actions.insert(0, intent)

    if intent in {
        ConsultantIntent.START_WRITING,
        ConsultantIntent.REVISE_DRAFT,
    }:
        classification.explicit_write_command = True
    if intent == ConsultantIntent.ACCEPT_PLAN:
        classification.explicit_plan_approval = True
    if intent in _SEARCH_ACTION_INTENTS:
        classification.explicit_research_command = True
    if intent == ConsultantIntent.ADD_TOPIC:
        classification.replace_current_plan = False
    if intent == ConsultantIntent.UNKNOWN:
        classification.needs_clarification = True

    return classification

def _approve_for_combined_write(
    contract: Mapping[str, Any],
    message: str,
    *,
    approved_by: str,
    explicit_approval: bool = False,
) -> dict[str, Any]:
    del message  # Compatibilité de signature ; l'autorisation vient du schéma.
    current = dict(contract)
    if current.get("approved_plan"):
        return current
    if not explicit_approval:
        return current
    if not _contract_sections(current):
        return current
    return approve_plan(current, approved_by=approved_by)


def _plan_materially_changed(
    candidate: Iterable[Mapping[str, Any]],
    current: Iterable[Mapping[str, Any]],
) -> bool:
    candidate_plan = normalize_plan_sections(list(candidate))
    current_plan = normalize_plan_sections(list(current))
    return bool(
        candidate_plan
        and (
            not current_plan
            or plan_hash(candidate_plan) != plan_hash(current_plan)
        )
    )


def _merge_additive_plan_update(
    current: Iterable[Mapping[str, Any]],
    candidate: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Préserve les sections existantes lors d'un ajout ou d'une modification.

    Le modèle conversationnel peut ne retourner que la section modifiée et les
    nouvelles sous-sections. Ce résultat partiel ne doit jamais remplacer tout
    le plan existant.
    """
    current_plan = normalize_plan_sections(list(current))
    candidate_plan = normalize_plan_sections(list(candidate))
    if not current_plan:
        return candidate_plan
    if not candidate_plan:
        return current_plan

    candidate_by_title = {
        _norm(section.get("title")): (index, section)
        for index, section in enumerate(candidate_plan)
        if _norm(section.get("title"))
    }
    candidate_by_id = {
        _clean(section.get("section_id"), 200): (index, section)
        for index, section in enumerate(candidate_plan)
        if _clean(section.get("section_id"), 200)
    }
    consumed: set[int] = set()
    merged: list[dict[str, Any]] = []
    for current_section in current_plan:
        current_id = _clean(current_section.get("section_id"), 200)
        current_title = _norm(current_section.get("title"))
        match = candidate_by_title.get(current_title)
        if match is None and current_id and not re.fullmatch(
            r"section_\d+",
            current_id,
            flags=re.I,
        ):
            match = candidate_by_id.get(current_id)
        if match is None:
            merged.append(dict(current_section))
            continue
        candidate_index, candidate_section = match
        consumed.add(candidate_index)
        replacement = dict(candidate_section)
        replacement["section_id"] = current_id or replacement.get("section_id")
        merged.append(replacement)

    used_ids = {
        _clean(section.get("section_id"), 200)
        for section in merged
        if _clean(section.get("section_id"), 200)
    }
    for index, candidate_section in enumerate(candidate_plan):
        if index in consumed:
            continue
        addition = dict(candidate_section)
        section_id = _clean(addition.get("section_id"), 200)
        if not section_id or section_id in used_ids:
            base_id = storage_slug(addition.get("title"))
            section_id = base_id
            suffix = 2
            while section_id in used_ids:
                section_id = f"{base_id}_{suffix}"
                suffix += 1
            addition["section_id"] = section_id
        used_ids.add(section_id)
        merged.append(addition)
    return normalize_plan_sections(merged)


def _plan_candidate_covers_current(
    candidate: Iterable[Mapping[str, Any]],
    current: Iterable[Mapping[str, Any]],
) -> bool:
    """Détecte structurellement un plan final plutôt qu'un simple delta."""
    candidate_plan = normalize_plan_sections(list(candidate))
    current_plan = normalize_plan_sections(list(current))
    if not current_plan:
        return bool(candidate_plan)
    candidate_ids = {
        _clean(section.get("section_id"), 200)
        for section in candidate_plan
        if _clean(section.get("section_id"), 200)
    }
    candidate_titles = {
        _norm(section.get("title"))
        for section in candidate_plan
        if _norm(section.get("title"))
    }
    matched = 0
    for section in current_plan:
        section_id = _clean(section.get("section_id"), 200)
        title = _norm(section.get("title"))
        if (
            (section_id and section_id in candidate_ids)
            or (title and title in candidate_titles)
        ):
            matched += 1
    return matched / max(1, len(current_plan)) >= 0.70


def _append_plan_history(
    history: Iterable[Mapping[str, Any]],
    plan: Iterable[Mapping[str, Any]],
    *,
    version: Any,
    created_at: Any = None,
) -> list[dict[str, Any]]:
    normalized = normalize_plan_sections(list(plan))
    if not normalized:
        return [dict(row) for row in history if isinstance(row, Mapping)]
    fingerprint = plan_hash(normalized)
    output = [
        dict(row)
        for row in history
        if isinstance(row, Mapping) and isinstance(row.get("plan"), list)
    ]
    if any(_clean(row.get("plan_hash"), 200) == fingerprint for row in output):
        return output
    output.append({
        "version": version,
        "plan_hash": fingerprint,
        "created_at": (
            created_at.isoformat()
            if hasattr(created_at, "isoformat")
            else _clean(created_at, 120)
            or datetime.now(timezone.utc).isoformat()
        ),
        "plan": normalized,
    })
    return output if len(output) <= 8 else [output[0], *output[-7:]]


def _plan_history_from_session(
    session: GuidedResearchSessionData,
) -> list[dict[str, Any]]:
    """Agrège les versions persistées et migre les anciens snapshots de messages."""
    history = [
        dict(row)
        for row in (session.context.get("plan_history") or [])
        if isinstance(row, Mapping)
    ]
    for turn in session.messages:
        metadata = turn.metadata if isinstance(turn.metadata, Mapping) else {}
        contract = metadata.get("contract")
        if not isinstance(contract, Mapping):
            continue
        plan = _contract_sections(contract)
        if not plan:
            continue
        history = _append_plan_history(
            history,
            plan,
            version=contract.get("plan_version"),
            created_at=turn.created_at,
        )
    return history


def _format_plan_for_review(plan: Iterable[Mapping[str, Any]]) -> str:
    counters: list[int] = []
    lines: list[str] = []
    for section in normalize_plan_sections(list(plan)):
        level = max(1, int(section.get("level") or 1))
        while len(counters) < level:
            counters.append(0)
        counters = counters[:level]
        counters[-1] += 1
        label = ".".join(str(value) for value in counters)
        indent = "  " * (level - 1)
        lines.append(f"{indent}{label}. {_clean(section.get('title'), 300)}")
        objective = _clean(section.get("objective"), 900)
        if objective:
            lines.append(f"{indent}   Objectif : {objective}")
    return "\n".join(lines)


def _tokens(value: Any) -> set[str]:
    stop = {
        "avec", "dans", "des", "une", "pour", "par", "sur", "les", "the",
        "and", "for", "from", "with", "etat", "art", "section", "article",
        "projet", "scientifique", "scientific",
    }
    return {
        token for token in _norm(value).split()
        if len(token) >= 3 and token not in stop
    }


def _unique(values: Iterable[Any], limit: int = 100) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean(value, 1000)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
            if len(output) >= limit:
                break
    return output


def _extract_json(value: Any) -> dict[str, Any]:
    raw = str(value or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                pass
    return {}


def _contract_sections(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("approved_plan", "consultant_edited_plan", "proposed_plan"):
        rows = contract.get(key)
        if isinstance(rows, list) and rows:
            return [dict(row) for row in rows if isinstance(row, dict)]
    return []


def _entity_type(value: Any) -> RequestedEntityType:
    raw = _norm(value).replace(" ", "_")
    aliases = {
        "method": RequestedEntityType.SCIENTIFIC_METHOD,
        "methode": RequestedEntityType.SCIENTIFIC_METHOD,
        "software": RequestedEntityType.SCIENTIFIC_SOFTWARE,
        "logiciel": RequestedEntityType.SCIENTIFIC_SOFTWARE,
        "library": RequestedEntityType.SOFTWARE_LIBRARY,
        "bibliotheque": RequestedEntityType.SOFTWARE_LIBRARY,
        "documentation": RequestedEntityType.TOOL,
        "outil": RequestedEntityType.TOOL,
    }
    if raw in aliases:
        return aliases[raw]
    try:
        return RequestedEntityType(raw)
    except Exception:
        return RequestedEntityType.OTHER


def _depth(value: Any) -> RequestedDepth:
    raw = _norm(value).replace(" ", "_")
    try:
        return RequestedDepth(raw)
    except Exception:
        return RequestedDepth.DETAILED


def _brief_from_contract(
    contract: Mapping[str, Any],
    *,
    raw_request: str = "",
    topics: Iterable[Mapping[str, Any]] | None = None,
    constraints: Iterable[Any] | None = None,
) -> ConsultantBrief:
    sections: list[RequestedSection] = []
    for index, row in enumerate(_contract_sections(contract), 1):
        title = _clean(row.get("title"), 400)
        if not title:
            continue
        sections.append(
            RequestedSection(
                section_id=_clean(row.get("section_id"), 100) or f"section_{index}",
                title=title,
                order=index,
                parent_id=_clean(row.get("parent_id"), 100) or None,
                level=int(row.get("level") or 1),
                objective=_clean(row.get("objective"), 2000),
                instructions=_unique(row.get("instructions") or []),
                required_dimensions=_unique(row.get("required_dimensions") or []),
                visual_requirements=_unique(row.get("visual_requirements") or []),
                source_preferences=_unique(row.get("source_preferences") or []),
                target_verrous=_unique(row.get("verrou_ids") or []),
                depth=_depth(row.get("depth")),
                target_words=row.get("target_words"),
            )
        )
    requested_topics: list[RequestedTopic] = []
    for index, row in enumerate(topics or [], 1):
        if not isinstance(row, Mapping):
            continue
        name = _clean(row.get("name") or row.get("topic"), 300)
        if not name:
            continue
        requested_topics.append(
            RequestedTopic(
                topic_id=_clean(row.get("topic_id"), 100) or f"topic_{index}",
                name=name,
                entity_type=_entity_type(row.get("entity_type")),
                requested_dimensions=_unique(row.get("requested_dimensions") or []),
                target_sections=_unique(row.get("target_sections") or []),
                target_verrous=_unique(row.get("target_verrous") or []),
                source_preferences=_unique(row.get("source_preferences") or []),
                notes=_clean(row.get("notes"), 1500),
                required=bool(row.get("required", True)),
            )
        )
    return ConsultantBrief(
        raw_request=raw_request,
        requested_sections=sections,
        requested_topics=requested_topics,
        use_selected_articles=True,
        use_previous_cir=False,
        research_new_sources=True,
        output_mode=GuidedResearchTargetMode.GLOBAL,
        language="fr",
        desired_depth=RequestedDepth.VERY_DETAILED,
        general_constraints=_unique(
            [
                "Produire un seul état de l'art global et argumenté.",
                "Respecter exactement le plan et les demandes validés par le consultant.",
                "Distinguer les preuves scientifiques, les documents projet et les documentations officielles.",
                "Expliquer clairement les définitions, procédures, résultats, comparaisons et limites utiles.",
                "Ne jamais inventer de résultat, de chiffre, de source ou de fonctionnement d'outil.",
                *(constraints or []),
            ]
        ),
    )


class EnnoScholarGuidedResearchAgent:
    """Conversation naturelle pilotant plan, couverture, recherche et rédaction."""

    def __init__(
        self,
        *,
        state_manager: GuidedResearchSessionStateManager | None = None,
        repository: GuidedResearchSessionRepository | None = None,
        llm: LLMClient | None = None,
        research: WebResearchService | None = None,
    ) -> None:
        self.state_manager = state_manager or GuidedResearchSessionStateManager()
        self.repository = repository or GuidedResearchSessionRepository()
        self.llm = llm or LLMClient()
        self.research = research or WebResearchService(llm=self.llm)
        self.understanding = ConversationUnderstandingService(self.llm)

    @staticmethod
    def _contract_path(project: Any) -> Path:
        return consultant_plan_path(
            str(project.organisme),
            str(project.project_name),
            str(project.year),
        )

    @staticmethod
    def _phase47_path(project: Any) -> Path:
        return (
            state_of_art_root(
                str(project.organisme),
                str(project.project_name),
                str(project.year),
            )
            / "phase_4_7_scientific_narrative"
            / "scientific_narrative_payload.json"
        )

    @staticmethod
    def _cards_path(project: Any) -> Path:
        return (
            state_of_art_root(
                str(project.organisme),
                str(project.project_name),
                str(project.year),
            )
            / "article_cards"
            / "article_cards_payload.json"
        )

    @staticmethod
    def _sources_path(project: Any) -> Path:
        return guided_sources_path(
            str(project.organisme),
            str(project.project_name),
            str(project.year),
        )

    def _load_or_create_contract(self, project: Any) -> dict[str, Any]:
        path = self._contract_path(project)
        existing = read_json(path)
        if existing.get("payload_type") == "ennoscholar_consultant_plan_contract_v1":
            return existing
        contract = self._load_contract_snapshot(project)
        if contract:
            write_json(path, contract)
        return contract

    def _load_contract_snapshot(self, project: Any) -> dict[str, Any]:
        """Construit une vue en mémoire sans créer d'artefact projet."""
        existing = read_json(self._contract_path(project))
        if existing.get("payload_type") == "ennoscholar_consultant_plan_contract_v1":
            return existing
        phase47 = read_json(self._phase47_path(project))
        proposed = propose_from_phase47(phase47) if phase47 else []
        if not proposed:
            return {}
        return create_contract(proposed)

    def _conversation_project_context(
        self,
        db: Session,
        project: Any,
        session: GuidedResearchSessionData,
    ) -> dict[str, Any]:
        cards_payload = read_json(self._cards_path(project))
        cards: list[dict[str, Any]] = []
        for key in ("cards", "article_cards", "items", "articles"):
            value = cards_payload.get(key)
            if isinstance(value, list):
                cards = [row for row in value if isinstance(row, dict)]
                break
        article_context = [
            {
                "citation_id": _clean(card.get("citation_id"), 80),
                "article_id": card.get("article_id"),
                "title": _clean(card.get("title"), 500),
                "role": _clean(card.get("role") or card.get("tag"), 100),
                "guided_research_source": bool(
                    card.get("guided_research_source")
                ),
                "verrou_ids": list(
                    card.get("verrou_ids")
                    or card.get("target_verrous")
                    or card.get("covered_verrou_ids")
                    or []
                )[:12],
                "abstract": _clean(
                    card.get("abstract")
                    or card.get("probleme_scientifique")
                    or card.get("objectif"),
                    900,
                ),
                "keywords": list(card.get("mots_cles") or card.get("keywords") or [])[:20],
            }
            for card in cards[:30]
        ]

        narrative = read_json(self._phase47_path(project))
        narrative_context = {
            key: narrative.get(key)
            for key in (
                "canonical_verrous",
                "project_specific_storyline",
                "project_specific_story_axes",
                "dominant_project_terms",
                "shared_limitations",
                "scientific_consensus",
                "scientific_contradictions",
                "remaining_unknowns",
                "consultant_storyline",
            )
            if narrative.get(key) not in (None, "", [], {})
        }
        # Ces artefacts peuvent être très riches. La sérialisation bornée conserve
        # le contexte utile sans injecter le document complet dans chaque tour.
        narrative_text = _clean(
            json.dumps(narrative_context, ensure_ascii=False, default=str),
            18000,
        )

        try:
            from services.consultant_verrou_service import (
                list_latest_diagnostic_verrous_for_chat,
            )
            current_verrous = list_latest_diagnostic_verrous_for_chat(
                db, project
            )
        except Exception as exc:
            current_verrous = []
            print(
                "[EnnoScholar][GuidedResearch][WARN] "
                f"lecture verrous chat impossible: {exc}"
            )

        standalone_verrous = [
            dict(row)
            for row in (session.context.get("consultant_verrous") or [])
            if isinstance(row, Mapping) and _clean(row.get("title"), 700)
        ]
        known_ids = {
            _clean(row.get("id"), 120)
            for row in current_verrous
            if _clean(row.get("id"), 120)
        }
        current_verrous.extend(
            row
            for row in standalone_verrous
            if _clean(row.get("id"), 120) not in known_ids
        )

        return {
            "project": {
                "id": int(project.id),
                "organisme": str(project.organisme),
                "name": str(project.project_name),
                "year": str(project.year),
                "domain": _clean(getattr(project, "domain_label", ""), 500),
            },
            "scientific_context": narrative_text,
            "validated_article_cards": article_context,
            "current_verrous": current_verrous,
            "operating_mode": _clean(
                session.context.get("operating_mode"), 80
            ),
            "standalone_project_brief": dict(
                session.context.get("standalone_project_brief") or {}
            ),
            "previous_project_memories": list(
                session.context.get("previous_project_memories") or []
            )[-5:],
            "plan_history": list(
                _plan_history_from_session(session)
            ),
            "writing_source_policy": dict(
                session.context.get("writing_source_policy") or {}
            ),
        }

    def _resolved_conversation_project_context(
        self,
        db: Session,
        project: Any,
        session: GuidedResearchSessionData,
    ) -> dict[str, Any]:
        """Compatibilité avec les adaptateurs historiques à deux arguments."""
        builder = self._conversation_project_context
        try:
            parameter_count = len(inspect.signature(builder).parameters)
        except (TypeError, ValueError):
            parameter_count = 3
        if parameter_count <= 2:
            return builder(project, session)  # type: ignore[call-arg]
        return builder(db, project, session)

    def create_session(
        self,
        db: Session,
        project: Any,
        *,
        created_by_user_id: int | None,
        target_mode: GuidedResearchTargetMode = GuidedResearchTargetMode.GLOBAL,
        entry_module: GuidedResearchEntryModule = GuidedResearchEntryModule.ENNOSCHOLAR,
    ) -> GuidedResearchSessionData:
        try:
            from services.consultant_verrou_service import (
                get_latest_diagnostic_run,
            )
            diagnostic_backed = bool(
                get_latest_diagnostic_run(db, int(project.id))
            )
        except Exception:
            diagnostic_backed = False
        contract = (
            {}
            if entry_module == GuidedResearchEntryModule.ENNOAMEL
            else self._load_or_create_contract(project)
        )
        brief = _brief_from_contract(contract) if contract else None
        previous_memories: list[dict[str, Any]] = []
        if entry_module != GuidedResearchEntryModule.ENNOAMEL:
            for previous in self.state_manager.list_project_sessions(
                db, int(project.id), limit=8
            ):
                memory = previous.context.get("conversation_memory")
                if isinstance(memory, dict) and memory:
                    previous_memories.append(memory)
        latest_memory = previous_memories[0] if previous_memories else {}
        session = self.state_manager.create_session(
            db,
            project_id=int(project.id),
            created_by_user_id=created_by_user_id,
            entry_module=entry_module,
            target_mode=target_mode,
            initial_context={
                "project": {
                    "id": int(project.id),
                    "organisme": str(project.organisme),
                    "project_name": str(project.project_name),
                    "year": str(project.year),
                },
                "contract_path": str(self._contract_path(project)),
                "phase47_path": str(self._phase47_path(project)),
                "guided_sources_path": str(self._sources_path(project)),
                "research_enabled": True,
                "conversation_mode": "natural_llm",
                "operating_mode": (
                    "diagnostic_backed"
                    if diagnostic_backed
                    else "standalone_chat"
                ),
                "chat_only_interface": not diagnostic_backed,
                "consultant_verrous": [],
                "standalone_project_brief": {},
                "conversation_memory": latest_memory,
                "previous_project_memories": previous_memories[:5],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if brief and brief.requested_sections:
            session = self.state_manager.update_brief(db, session.session_id, brief)
        self.repository.update(
            db,
            session.session_id,
            writing_contract=contract,
            state=(
                GuidedResearchState.READY_TO_WRITE
                if contract.get("approved_plan")
                else GuidedResearchState.BRIEF_IN_PROGRESS
            ),
            ready_to_write=bool(contract.get("approved_plan")),
        )
        greeting = (
            (
                "Parlez-moi librement de l'histoire scientifique que vous voulez construire. "
                "Je peux proposer ou modifier le plan, signaler les parties faibles, rechercher "
                "des articles ou des documentations officielles, ajouter un verrou manquant "
                "sur demande explicite, puis rédiger lorsque vous le décidez."
            )
            if diagnostic_backed
            else (
                "Cette conversation peut partir sans diagnostic préalable. Indiquez le "
                "projet, son domaine, son objectif et le ou les verrous à étudier. "
                "Je rechercherai les publications pertinentes avant de rédiger une revue "
                "ciblée pour un verrou ou globale pour plusieurs verrous."
            )
        )
        self.state_manager.append_message(
            db,
            session.session_id,
            role=ConversationRole.ASSISTANT,
            content=greeting,
            metadata={"conversation_mode": "natural_llm"},
        )
        return self.state_manager.get_session(db, session.session_id)

    def get_session(self, db: Session, session_id: str) -> dict[str, Any]:
        session = self.state_manager.get_session(db, session_id)
        return {
            "session": session.model_dump(mode="json"),
            "artifacts": self.repository.snapshot(db, session_id),
        }

    def _respond_only(
        self,
        session: GuidedResearchSessionData,
        *,
        intent: ConsultantIntent,
        interpretation: Mapping[str, Any],
        fallback: Mapping[str, Any] | None = None,
    ) -> ConversationResponse:
        assistant = _clean(interpretation.get("assistant_message"), 6000)
        if not assistant:
            assistant = (
                "Je n'ai pas pu déterminer précisément ce que vous attendez. "
                "Pouvez-vous reformuler ou préciser l'objectif de ce message ?"
            )
        return self._response(
            session.session_id,
            intent,
            session.state,
            assistant,
            NextAction.NONE,
            session.brief,
            session.ready_to_write,
            {
                "conversation_natural": True,
                "no_project_mutation": True,
                **(
                    {"understanding_fallback": dict(fallback)}
                    if fallback
                    else {}
                ),
            },
        )

    def handle_message(
        self,
        db: Session,
        project: Any,
        *,
        session_id: str,
        consultant_message: str,
    ) -> ConversationResponse:
        session = self.state_manager.get_session(db, session_id)
        if int(session.project_id) != int(project.id):
            raise PermissionError("Cette session n'appartient pas au projet demandé.")
        message = str(consultant_message or "").strip()
        if not message:
            raise ValueError("Le message ne peut pas être vide.")

        contract_path = self._contract_path(project)
        contract = self._load_contract_snapshot(project)
        understanding = self.understanding.understand(
            session=session,
            consultant_message=message,
            project_context=self._resolved_conversation_project_context(
                db, project, session
            ),
            current_plan=_contract_sections(contract),
        )
        understanding_failure: dict[str, Any] = {}
        if understanding is None:
            failure_reader = getattr(self.understanding, "get_last_failure", None)
            if callable(failure_reader):
                understanding_failure = dict(failure_reader() or {})
        classification = (
            understanding.classification
            if understanding is not None
            else _fallback_intent_classification(message)
        )
        classification = _reconcile_contextual_intent(classification)
        classification = _ground_writing_source_policy(
            message,
            classification,
        )
        original_intent = classification.intent
        intent = _resolve_routed_intent(classification)
        classification.intent = intent
        route_guard_applied = intent != original_intent
        interpretation: dict[str, Any]
        if understanding is not None:
            interpretation = understanding.model_dump(mode="json")
            interpretation["classification"] = classification.model_dump(mode="json")
            if route_guard_applied and not _clean(
                interpretation.get("assistant_message"), 6000
            ):
                interpretation["assistant_message"] = (
                    "Je préfère vérifier votre intention avant d'agir. "
                    "Pouvez-vous préciser l'action que vous souhaitez ?"
                )
        else:
            fallback_metadata = {
                "active": True,
                "classifier": classification.classifier,
                **understanding_failure,
            }
            interpretation = {
                "assistant_message": "",
                "plan": [],
                "topics": [],
                "constraints": [],
                "verrous": [],
                "project_brief": {},
                "review_scope": "auto",
                "search_requests": [],
                "interpreter": {
                    "fallback": True,
                    **fallback_metadata,
                },
            }

        # La sélection de capacité autorise les payloads. Leur simple présence
        # dans une sortie de modèle ne constitue jamais une autorisation.
        if intent not in _PLAN_ACTION_INTENTS:
            interpretation["plan"] = []
        if intent != ConsultantIntent.DESCRIBE_REQUIREMENTS:
            interpretation["topics"] = []
            interpretation["constraints"] = []
        standalone_context_declaration = bool(
            intent == ConsultantIntent.DESCRIBE_REQUIREMENTS
            and _clean(session.context.get("operating_mode"), 80)
            == "standalone_chat"
        )
        if (
            intent != ConsultantIntent.ADD_VERROU_AND_SEARCH
            and not standalone_context_declaration
        ):
            interpretation["verrous"] = []
            interpretation["project_brief"] = {}
            interpretation["review_scope"] = "auto"
        if (
            intent not in _SEARCH_ACTION_INTENTS
            or not classification.explicit_research_command
        ):
            interpretation["search_requests"] = []
        if route_guard_applied:
            interpretation["plan"] = []
            interpretation["topics"] = []
            interpretation["constraints"] = []
            interpretation["verrous"] = []
            interpretation["project_brief"] = {}
            interpretation["review_scope"] = "auto"
            interpretation["search_requests"] = []

        interpreter_metadata = interpretation.get("interpreter")
        context_updates: dict[str, Any] = {
            "last_intent_classification": classification.model_dump(mode="json"),
            "last_corrected_message": classification.corrected_message,
            "last_understanding_fallback": (
                dict(interpreter_metadata)
                if (
                    isinstance(interpreter_metadata, Mapping)
                    and understanding is None
                )
                else None
            ),
        }
        if classification.writing_source_scope != "unspecified":
            context_updates["writing_source_policy"] = {
                "scope": classification.writing_source_scope,
                "source_identifiers": list(
                    classification.writing_source_identifiers or []
                ),
                "requested_source_count": classification.requested_source_count,
                "exclude_external_research": (
                    classification.writing_source_scope
                    == "baseline_verrou_corpus"
                ),
                "grounded_in_current_message": True,
            }
        if (
            understanding is not None
            and intent not in _RESPONSE_ONLY_INTENTS
            and not route_guard_applied
        ):
            context_updates["conversation_memory"] = (
                understanding.memory.model_dump(mode="json")
            )
        self.repository.update(
            db,
            session_id,
            context_updates=context_updates,
        )
        self.state_manager.append_message(
            db,
            session_id,
            role=ConversationRole.CONSULTANT,
            content=message,
            intent=intent,
            metadata={
                "classification": classification.model_dump(mode="json"),
                "route_guard_applied": route_guard_applied,
                "understanding_fallback": (
                    interpretation.get("interpreter")
                    if understanding is None
                    else None
                ),
            },
        )

        if intent in _RESPONSE_ONLY_INTENTS:
            response = self._respond_only(
                session,
                intent=intent,
                interpretation=interpretation,
                fallback=(
                    interpretation.get("interpreter")
                    if understanding is None
                    else None
                ),
            )
        elif intent == ConsultantIntent.CANCEL:
            response = self._response(
                session_id,
                intent,
                GuidedResearchState.CANCELLED,
                _clean(interpretation.get("assistant_message"), 6000)
                or (
                    "D'accord, j'arrête cette session. Aucun article, verrou "
                    "ou document n'a été supprimé."
                ),
                NextAction.NONE,
                session.brief,
                False,
            )
            self.repository.update(
                db, session_id, state=GuidedResearchState.CANCELLED, ready_to_write=False
            )
        elif intent == ConsultantIntent.ADD_VERROU_AND_SEARCH:
            response = self._add_consultant_verrou_and_search(
                db,
                project,
                session,
                contract,
                contract_path,
                message,
                interpretation=interpretation,
                pending_write_requested=(
                    ConsultantIntent.START_WRITING
                    in classification.requested_actions
                ),
            )
        elif standalone_context_declaration and (
            interpretation.get("verrous")
            or interpretation.get("project_brief")
        ):
            response = self._add_standalone_verrous_and_search(
                db,
                project,
                session,
                message,
                interpretation=interpretation,
                pending_write_requested=False,
                run_research=False,
            )
        elif intent == ConsultantIntent.ACCEPT_PLAN:
            response = self._accept_plan(
                db,
                project,
                session,
                contract,
                contract_path,
                conversation_reply=_clean(
                    interpretation.get("assistant_message"), 6000
                ),
            )
        elif intent in {
            ConsultantIntent.START_WRITING,
            ConsultantIntent.REVISE_DRAFT,
        }:
            response = self._start_writing(
                db,
                project,
                session,
                contract,
                contract_path,
                message,
                explicit_plan_approval=classification.explicit_plan_approval,
                use_current_sources_only=classification.use_current_sources_only,
                writing_source_scope=classification.writing_source_scope,
                writing_source_identifiers=(
                    classification.writing_source_identifiers
                ),
                requested_source_count=classification.requested_source_count,
                action_intent=intent,
                conversation_reply=_clean(
                    interpretation.get("assistant_message"), 6000
                ),
            )
        elif intent in (
            _PLAN_ACTION_INTENTS
            | _SEARCH_ACTION_INTENTS
            | {ConsultantIntent.DESCRIBE_REQUIREMENTS}
        ):
            response = self._continue_conversation(
                db,
                project,
                session,
                contract,
                contract_path,
                message,
                force_search=intent in _SEARCH_ACTION_INTENTS,
                allow_plan_change=intent in _PLAN_ACTION_INTENTS,
                action_intent=intent,
                classification=classification,
                interpretation=interpretation,
            )
        else:
            response = self._respond_only(
                session,
                intent=ConsultantIntent.UNKNOWN,
                interpretation={
                    "assistant_message": (
                        "J'ai compris le type de demande, mais il manque les "
                        "éléments nécessaires pour l'exécuter en sécurité. "
                        "Pouvez-vous préciser la cible concernée ?"
                    )
                },
            )

        deferred_actions = [
            action
            for action in classification.requested_actions
            if action != intent and action not in classification.forbidden_actions
        ]
        if deferred_actions and not route_guard_applied:
            response.metadata["deferred_requested_actions"] = [
                action.value for action in deferred_actions
            ]
            response.assistant_message = (
                response.assistant_message.rstrip()
                + "\n\nJ'ai traité la première action demandée. "
                "Les autres restent explicitement en attente afin de ne pas "
                "enchaîner des opérations sans validation intermédiaire."
            )

        self.state_manager.append_message(
            db,
            session_id,
            role=ConversationRole.ASSISTANT,
            content=response.assistant_message,
            intent=response.intent,
            metadata={"next_action": response.next_action.value, **response.metadata},
        )
        return response

    @staticmethod
    def _invalidate_plan_after_verrou_scope_change(
        contract: Mapping[str, Any],
        *,
        verrou_id: int,
        verrou_title: str,
    ) -> tuple[dict[str, Any], bool]:
        current = dict(contract or {})
        if not current:
            return current, False

        approved = list(current.get("approved_plan") or [])
        authorized = bool(current.get("writing_authorized"))
        if not approved and not authorized:
            return current, False

        if approved and not (
            current.get("consultant_edited_plan")
            or current.get("proposed_plan")
        ):
            current["consultant_edited_plan"] = approved

        current["approved_plan"] = []
        current["approval_hash"] = ""
        current["approved_at"] = ""
        current["approved_by"] = ""
        current["writing_authorized"] = False
        current["plan_version"] = int(current.get("plan_version") or 1) + 1

        history = [
            dict(row)
            for row in (current.get("scope_change_history") or [])
            if isinstance(row, Mapping)
        ]
        history.append({
            "reason": "consultant_added_verrou",
            "verrou_id": int(verrou_id),
            "verrou_title": _clean(verrou_title, 1200),
            "at": datetime.now(timezone.utc).isoformat(),
        })
        current["scope_change_history"] = history[-20:]
        current["scope_revision_required"] = True
        return current, True

    @staticmethod
    def _brief_with_consultant_verrou(
        session: GuidedResearchSessionData,
        contract: Mapping[str, Any],
        *,
        verrou_id: int | str,
        verrou_title: str,
        justification: str,
        supporting_context: str,
        raw_request: str,
        research_requested: bool = True,
    ) -> ConsultantBrief:
        brief = session.brief or _brief_from_contract(
            dict(contract or {}),
            raw_request=raw_request,
        )
        topics = list(brief.requested_topics or [])
        target_id = str(verrou_id)
        already_present = any(
            target_id in (topic.target_verrous or [])
            or _norm(topic.name) == _norm(verrou_title)
            for topic in topics
        )
        if not already_present:
            topics.append(
                RequestedTopic(
                    name=_clean(verrou_title, 1200),
                    entity_type=RequestedEntityType.SCIENTIFIC_CONCEPT,
                    requested_dimensions=[
                        "scientific basis",
                        "methods",
                        "experimental results",
                        "contradictory evidence",
                        "limitations",
                        "validation protocols",
                    ],
                    target_sections=[],
                    target_verrous=[target_id],
                    source_preferences=["articles scientifiques"],
                    notes=_clean(
                        " ".join(
                            value
                            for value in (justification, supporting_context)
                            if value
                        ),
                        5000,
                    ),
                    required=True,
                )
            )

        instructions = {
            str(key): list(values or [])
            for key, values in (brief.verrou_instructions or {}).items()
        }
        instructions[target_id] = [
            value
            for value in (
                _clean(justification, 4000),
                _clean(supporting_context, 8000),
                (
                    "Rechercher des sources qui étayent, nuancent ou contredisent "
                    "le verrou et qui précisent les conditions de validation."
                    if research_requested
                    else ""
                ),
            )
            if value
        ]
        return brief.model_copy(
            update={
                "raw_request": _clean(
                    "\n".join(
                        value
                        for value in (brief.raw_request, raw_request)
                        if value
                    ),
                    12000,
                ),
                "requested_topics": topics,
                "verrou_instructions": instructions,
                "research_new_sources": bool(
                    getattr(brief, "research_new_sources", False)
                    or research_requested
                ),
                "updated_at": datetime.now(timezone.utc),
            }
        )

    @staticmethod
    def _standalone_verrou_id(session_id: str, title: str) -> str:
        fingerprint = hashlib.sha256(
            f"{session_id}|{_norm(title)}".encode("utf-8")
        ).hexdigest()[:12]
        return f"SV-{fingerprint}"

    def _add_standalone_verrous_and_search(
        self,
        db: Session,
        project: Any,
        session: GuidedResearchSessionData,
        message: str,
        *,
        interpretation: Mapping[str, Any],
        pending_write_requested: bool,
        run_research: bool = True,
    ) -> ConversationResponse:
        payloads = [
            dict(row)
            for row in (interpretation.get("verrous") or [])
            if isinstance(row, Mapping) and _clean(row.get("title"), 1200)
        ][:10]
        if not payloads and run_research:
            return self._respond_only(
                session,
                intent=ConsultantIntent.UNKNOWN,
                interpretation={
                    "assistant_message": (
                        "Précisez au moins un verrou scientifique ou technologique "
                        "à étudier, ainsi que le domaine ou l'objectif du projet si "
                        "ces éléments ne sont pas encore connus."
                    )
                },
            )

        existing_verrous = [
            dict(row)
            for row in (session.context.get("consultant_verrous") or [])
            if isinstance(row, Mapping) and _clean(row.get("title"), 1200)
        ]
        requested_verrous: list[dict[str, Any]] = []
        created_verrou_ids: list[str] = []
        try:
            from services.consultant_verrou_service import (
                verrou_title_similarity,
            )
        except Exception:
            verrou_title_similarity = lambda left, right: (  # type: ignore[assignment]
                1.0 if _norm(left) == _norm(right) else 0.0
            )

        for payload in payloads:
            title = _clean(payload.get("title"), 1200)
            duplicate = next(
                (
                    row
                    for row in existing_verrous
                    if verrou_title_similarity(title, row.get("title")) >= 0.86
                ),
                None,
            )
            if duplicate is not None and not bool(
                payload.get("force_create_distinct")
            ):
                target = duplicate
                target["justification"] = _clean(
                    payload.get("justification")
                    or target.get("justification"),
                    4000,
                )
                target["supporting_context"] = _clean(
                    payload.get("supporting_context")
                    or target.get("supporting_context"),
                    8000,
                )
            else:
                target = {
                    "id": self._standalone_verrou_id(
                        session.session_id,
                        title
                        + (
                            f"|{len(existing_verrous) + 1}"
                            if payload.get("force_create_distinct")
                            else ""
                        ),
                    ),
                    "title": title,
                    "justification": _clean(
                        payload.get("justification"), 4000
                    ),
                    "supporting_context": _clean(
                        payload.get("supporting_context"), 8000
                    ),
                    "source_document_ids": list(
                        payload.get("source_document_ids") or []
                    ),
                    "origin": "consultant_standalone_chat",
                    "consultant_status": "garde",
                    "supplementary_verrou": True,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                existing_verrous.append(target)
                created_verrou_ids.append(str(target["id"]))
            if target not in requested_verrous:
                requested_verrous.append(target)

        previous_project_brief = (
            dict(session.context.get("standalone_project_brief") or {})
            if isinstance(
                session.context.get("standalone_project_brief"), Mapping
            )
            else {}
        )
        incoming_project_brief = (
            dict(interpretation.get("project_brief") or {})
            if isinstance(interpretation.get("project_brief"), Mapping)
            else {}
        )
        standalone_project_brief = {
            "project_name": _clean(
                incoming_project_brief.get("project_name")
                or previous_project_brief.get("project_name")
                or getattr(project, "project_name", ""),
                500,
            ),
            "domain": _clean(
                incoming_project_brief.get("domain")
                or previous_project_brief.get("domain")
                or getattr(project, "domain_label", ""),
                1000,
            ),
            "objective": _clean(
                incoming_project_brief.get("objective")
                or previous_project_brief.get("objective"),
                5000,
            ),
            "additional_context": _clean(
                incoming_project_brief.get("additional_context")
                or previous_project_brief.get("additional_context"),
                8000,
            ),
        }

        requested_scope = _clean(
            interpretation.get("review_scope"), 40
        )
        review_scope = (
            "global"
            if requested_scope == "global" or len(requested_verrous) > 1
            else "per_verrou"
        )
        active_verrous = (
            existing_verrous
            if review_scope == "global"
            else requested_verrous[:1]
        )

        brief = session.brief or ConsultantBrief(
            raw_request=message,
            output_mode=(
                GuidedResearchTargetMode.GLOBAL
                if review_scope == "global"
                else GuidedResearchTargetMode.PER_VERROU
            ),
            desired_depth=RequestedDepth.VERY_DETAILED,
        )
        for verrou in active_verrous:
            brief = self._brief_with_consultant_verrou(
                session.model_copy(update={"brief": brief}),
                {},
                verrou_id=str(verrou["id"]),
                verrou_title=_clean(verrou.get("title"), 1200),
                justification=_clean(verrou.get("justification"), 4000),
                supporting_context=_clean(
                    verrou.get("supporting_context"), 8000
                ),
                raw_request=message,
                research_requested=run_research,
            )
        brief = brief.model_copy(
            update={
                "output_mode": (
                    GuidedResearchTargetMode.GLOBAL
                    if review_scope == "global"
                    else GuidedResearchTargetMode.PER_VERROU
                )
            }
        )
        self.state_manager.update_brief(db, session.session_id, brief)
        self.repository.update(
            db,
            session.session_id,
            ready_to_write=False,
            context_updates={
                "operating_mode": "standalone_chat",
                "chat_only_interface": True,
                "consultant_verrous": existing_verrous,
                "standalone_project_brief": standalone_project_brief,
                "review_scope": review_scope,
                "active_verrou_ids": [
                    str(row.get("id")) for row in active_verrous
                ],
                "pending_write_request": pending_write_requested,
                "pipeline_execution_requested": False,
            },
        )

        if not run_research:
            self.repository.update(
                db,
                session.session_id,
                state=GuidedResearchState.BRIEF_PARSED,
                ready_to_write=False,
            )
            project_name = _clean(
                standalone_project_brief.get("project_name"), 500
            )
            domain = _clean(standalone_project_brief.get("domain"), 1000)
            objective = _clean(
                standalone_project_brief.get("objective"), 5000
            )
            verrou_titles = [
                _clean(row.get("title"), 1200)
                for row in active_verrous
                if _clean(row.get("title"), 1200)
            ]
            confirmation = [
                (
                    f"Contexte scientifique enregistré pour « {project_name} »."
                    if project_name
                    else "Contexte scientifique du projet enregistré."
                )
            ]
            if domain:
                confirmation.append(f"Domaine retenu : {domain}.")
            if objective:
                confirmation.append(f"Objectif retenu : {objective}.")
            if verrou_titles:
                label = "Verrou retenu" if len(verrou_titles) == 1 else "Verrous retenus"
                confirmation.append(f"{label} : " + " ; ".join(verrou_titles) + ".")
            confirmation.append(
                "Aucune recherche ni rédaction n'a été lancée."
            )
            return self._response(
                session.session_id,
                ConsultantIntent.DESCRIBE_REQUIREMENTS,
                GuidedResearchState.BRIEF_PARSED,
                "\n\n".join(confirmation),
                NextAction.NONE,
                brief,
                False,
                {
                    "operating_mode": "standalone_chat",
                    "chat_only_interface": True,
                    "context_recorded": True,
                    "research_started": False,
                    "review_scope": review_scope,
                    "active_verrous": active_verrous,
                    "standalone_project_brief": standalone_project_brief,
                    "trigger_state_of_art_generation": False,
                    "conversation_natural": True,
                },
            )

        raw_requests = [
            dict(row)
            for row in (interpretation.get("search_requests") or [])
            if isinstance(row, Mapping) and _clean(row.get("query"), 1200)
        ][:8]
        if not raw_requests:
            for verrou in active_verrous:
                title = _clean(verrou.get("title"), 1200)
                context = " ".join(
                    value
                    for value in (
                        standalone_project_brief.get("domain"),
                        standalone_project_brief.get("objective"),
                    )
                    if value
                )
                raw_requests.extend(
                    [
                        {
                            "query": f"{title} {context} scientific evidence",
                            "query_kind": "scientific_evidence",
                        },
                        {
                            "query": (
                                f"{title} {context} experimental validation "
                                "limitations contradictory results"
                            ),
                            "query_kind": "direct_scientific_evidence",
                            "require_direct_evidence": True,
                        },
                    ]
                )

        search_requests: list[dict[str, Any]] = []
        for raw_request in raw_requests[:8]:
            request = dict(raw_request)
            request_text = " ".join(
                _clean(request.get(key), 1200)
                for key in ("query", "entity_name")
            )
            request_tokens = _tokens(request_text)
            ranked = sorted(
                active_verrous,
                key=lambda row: len(
                    request_tokens & _tokens(row.get("title"))
                ),
                reverse=True,
            )
            best_overlap = (
                len(request_tokens & _tokens(ranked[0].get("title")))
                if ranked
                else 0
            )
            targets = (
                [str(ranked[0]["id"])]
                if ranked and best_overlap > 0
                else [str(row["id"]) for row in active_verrous]
            )
            query_kind = _clean(
                request.get("query_kind"), 80
            ) or "scientific_evidence"
            if query_kind not in {
                "scientific_evidence",
                "direct_scientific_evidence",
                "official_documentation",
            }:
                query_kind = "scientific_evidence"
            request["query_kind"] = query_kind
            request["require_direct_evidence"] = bool(
                query_kind == "direct_scientific_evidence"
                or request.get("require_direct_evidence")
            )
            request["target_verrous"] = targets
            request["entity_type"] = _clean(
                request.get("entity_type"), 120
            ) or "scientific_concept"
            request["requested_dimensions"] = list(
                request.get("requested_dimensions")
                or [
                    "scientific basis",
                    "methods",
                    "experimental results",
                    "contradictory evidence",
                    "limitations",
                    "validation protocols",
                ]
            )
            request["target_context_dimensions"] = list(
                request.get("target_context_dimensions")
                or [
                    value
                    for value in (
                        standalone_project_brief.get("domain"),
                        standalone_project_brief.get("objective"),
                    )
                    if value
                ]
            )
            request["source_preferences"] = list(
                request.get("source_preferences")
                or ["articles scientifiques"]
            )
            search_requests.append(request)

        research = self._run_research(
            db, project, session, search_requests
        )
        candidates = list(research.get("candidates") or [])
        synthesis = self._synthesize_research_response(
            project=project,
            contract={},
            consultant_message=message,
            candidates=candidates,
            research_completeness=research.get("completeness") or {},
        )
        scope_label = (
            "globale"
            if review_scope == "global"
            else f"ciblée sur « {_clean(active_verrous[0].get('title'), 1200)} »"
        )
        assistant_message = (
            (
                "J'ai enregistré le nouveau verrou et lancé la recherche "
                if created_verrou_ids
                else "J'ai lancé la recherche "
            )
            + f"{scope_label}. "
        )
        if candidates:
            assistant_message += (
                f"La recherche propose {len(candidates)} source(s). Sélectionnez les "
                "publications à retenir ; elles seront extraites et transformées en "
                "Article Cards avant la rédaction."
            )
            if pending_write_requested:
                assistant_message += (
                    " Votre demande de rédaction est mémorisée, mais elle attend la "
                    "validation des sources afin de ne pas produire un texte sans preuves."
                )
            if synthesis:
                assistant_message += "\n\n" + synthesis.strip()
        else:
            assistant_message += (
                "Aucune publication exploitable n'a été trouvée. Il faut préciser "
                "le domaine, reformuler le verrou ou élargir la stratégie de recherche."
            )

        return self._response(
            session.session_id,
            ConsultantIntent.ADD_VERROU_AND_SEARCH,
            (
                GuidedResearchState.WAITING_CONSULTANT_FEEDBACK
                if candidates
                else GuidedResearchState.RESEARCH_REFINEMENT
            ),
            assistant_message,
            (
                NextAction.REVIEW_SOURCES
                if candidates
                else NextAction.RUN_RESEARCH
            ),
            brief,
            False,
            {
                "operating_mode": "standalone_chat",
                "chat_only_interface": True,
                "review_scope": review_scope,
                "active_verrous": active_verrous,
                "created_verrou_ids": created_verrou_ids,
                "research": research,
                "candidates": candidates,
                "pending_write_request": pending_write_requested,
                "trigger_state_of_art_generation": False,
                "conversation_natural": True,
            },
        )

    def _add_consultant_verrou_and_search(
        self,
        db: Session,
        project: Any,
        session: GuidedResearchSessionData,
        contract: dict[str, Any],
        contract_path: Path,
        message: str,
        *,
        interpretation: Mapping[str, Any],
        pending_write_requested: bool = False,
    ) -> ConversationResponse:
        verrou_payloads = [
            dict(row)
            for row in (interpretation.get("verrous") or [])
            if isinstance(row, Mapping)
        ]
        try:
            from services.consultant_verrou_service import (
                get_latest_diagnostic_run,
            )
            diagnostic_backed = bool(
                get_latest_diagnostic_run(db, int(project.id))
            )
        except Exception:
            diagnostic_backed = False

        if not diagnostic_backed:
            return self._add_standalone_verrous_and_search(
                db,
                project,
                session,
                message,
                interpretation=interpretation,
                pending_write_requested=pending_write_requested,
            )

        if len(verrou_payloads) != 1:
            return self._respond_only(
                session,
                intent=ConsultantIntent.UNKNOWN,
                interpretation={
                    "assistant_message": (
                        "Indiquez un seul verrou à la fois, avec sa formulation et "
                        "le contexte qui justifie son ajout."
                    )
                },
            )

        verrou_payload = verrou_payloads[0]
        from services.consultant_verrou_service import (
            create_or_reuse_consultant_verrou,
        )

        result = create_or_reuse_consultant_verrou(
            db,
            project,
            title=_clean(verrou_payload.get("title"), 1200),
            justification=_clean(verrou_payload.get("justification"), 4000),
            supporting_context=_clean(
                verrou_payload.get("supporting_context"), 8000
            ),
            source_document_ids=list(
                verrou_payload.get("source_document_ids") or []
            ),
            session_id=session.session_id,
            created_by_user_id=session.created_by_user_id,
            force_create_distinct=bool(
                verrou_payload.get("force_create_distinct")
            ),
        )

        if result.get("status") == "possible_duplicate":
            candidate = result.get("candidate") or {}
            return self._response(
                session.session_id,
                ConsultantIntent.ADD_VERROU_AND_SEARCH,
                session.state,
                (
                    "Un verrou proche existe déjà : « "
                    + _clean(candidate.get("title"), 1200)
                    + " ». Précisez soit « réutilise ce verrou », soit « crée un "
                    "verrou distinct » avant de lancer la recherche."
                ),
                NextAction.CONTINUE_BRIEF,
                session.brief,
                False,
                {
                    "possible_duplicate": result,
                    "trigger_state_of_art_generation": False,
                },
            )

        verrou_id = int(result["verrou_id"])
        verrou_title = _clean(result.get("title"), 1200)

        contract, plan_invalidated = self._invalidate_plan_after_verrou_scope_change(
            contract,
            verrou_id=verrou_id,
            verrou_title=verrou_title,
        )
        if contract:
            write_json(contract_path, contract)

        brief = self._brief_with_consultant_verrou(
            session,
            contract,
            verrou_id=verrou_id,
            verrou_title=verrou_title,
            justification=_clean(verrou_payload.get("justification"), 4000),
            supporting_context=_clean(
                verrou_payload.get("supporting_context"), 8000
            ),
            raw_request=message,
        )
        self.state_manager.update_brief(db, session.session_id, brief)
        self.repository.update(
            db,
            session.session_id,
            writing_contract=contract,
            ready_to_write=False,
            context_updates={
                "last_consultant_added_verrou": result,
                "scope_revision_required": plan_invalidated,
                "writing_authorized": False,
                "pipeline_execution_requested": False,
            },
        )

        raw_requests = [
            dict(row)
            for row in (interpretation.get("search_requests") or [])
            if isinstance(row, Mapping)
        ]
        if not raw_requests:
            raw_requests = [
                {
                    "query": f"{verrou_title} scientific literature",
                    "query_kind": "scientific_evidence",
                },
                {
                    "query": f"{verrou_title} experimental validation limitations",
                    "query_kind": "direct_scientific_evidence",
                    "require_direct_evidence": True,
                },
                {
                    "query": f"{verrou_title} contradictory evidence robustness",
                    "query_kind": "scientific_evidence",
                },
            ]

        search_requests: list[dict[str, Any]] = []
        for raw_request in raw_requests[:5]:
            request = dict(raw_request)
            query_kind = _clean(
                request.get("query_kind"), 80
            ) or "scientific_evidence"
            if query_kind not in {
                "scientific_evidence",
                "direct_scientific_evidence",
            }:
                query_kind = "scientific_evidence"
            request["query_kind"] = query_kind
            request["require_direct_evidence"] = bool(
                query_kind == "direct_scientific_evidence"
                or request.get("require_direct_evidence")
            )
            request["target_verrous"] = [str(verrou_id)]
            request["entity_name"] = _clean(
                request.get("entity_name"), 400
            ) or verrou_title
            request["entity_type"] = _clean(
                request.get("entity_type"), 120
            ) or "scientific_concept"
            request["requested_dimensions"] = list(
                request.get("requested_dimensions")
                or [
                    "scientific basis",
                    "methods",
                    "experimental results",
                    "contradictory evidence",
                    "limitations",
                    "validation protocols",
                ]
            )
            request["source_preferences"] = list(
                request.get("source_preferences")
                or ["articles scientifiques"]
            )
            search_requests.append(request)

        research = self._run_research(
            db,
            project,
            session,
            search_requests,
        )
        candidates = list(research.get("candidates") or [])
        synthesis = self._synthesize_research_response(
            project=project,
            contract=contract,
            consultant_message=message,
            candidates=candidates,
            research_completeness=research.get("completeness") or {},
        )

        action_label = (
            "réutilisé et validé"
            if result.get("status") == "reused_existing"
            else "ajouté au dernier diagnostic et validé par le consultant"
        )
        assistant_message = (
            f"Le verrou « {verrou_title} » a été {action_label}. "
            "Son score CIR et son tag CIR restent indéterminés jusqu'à l'analyse "
            "des preuves. "
        )
        if candidates:
            assistant_message += (
                f"La recherche a trouvé {len(candidates)} source(s) candidate(s). "
                "Sélectionnez celles à intégrer au corpus ; seules les sources "
                "acceptées seront extraites et utilisées."
            )
            if synthesis:
                assistant_message += "\n\n" + synthesis.strip()
        else:
            assistant_message += (
                "Aucune source exploitable n'a été trouvée pour cette première "
                "recherche. Il faut reformuler ou élargir les requêtes."
            )
        if plan_invalidated:
            assistant_message += (
                "\n\nLe plan précédemment approuvé a été invalidé, car le périmètre "
                "scientifique a changé. Il devra être revu puis validé avant la rédaction."
            )

        return self._response(
            session.session_id,
            ConsultantIntent.ADD_VERROU_AND_SEARCH,
            (
                GuidedResearchState.WAITING_CONSULTANT_FEEDBACK
                if candidates
                else GuidedResearchState.RESEARCH_REFINEMENT
            ),
            assistant_message,
            (
                NextAction.REVIEW_SOURCES
                if candidates
                else NextAction.RUN_RESEARCH
            ),
            brief,
            False,
            {
                "consultant_verrou": result,
                "research": research,
                "candidates": candidates,
                "plan_invalidated": plan_invalidated,
                "trigger_state_of_art_generation": False,
                "conversation_natural": True,
            },
        )

    def _continue_conversation(
        self,
        db: Session,
        project: Any,
        session: GuidedResearchSessionData,
        contract: dict[str, Any],
        contract_path: Path,
        message: str,
        *,
        force_search: bool,
        allow_plan_change: bool = False,
        action_intent: ConsultantIntent = ConsultantIntent.DESCRIBE_REQUIREMENTS,
        classification: IntentClassification,
        interpretation: Mapping[str, Any] | None = None,
    ) -> ConversationResponse:
        interpretation = dict(interpretation or {})
        current_plan = normalize_plan_sections(_contract_sections(contract))

        proposed_plan = (
            interpretation.get("plan")
            if (
                allow_plan_change
                and isinstance(interpretation.get("plan"), list)
            )
            else []
        )
        normalized_proposed_plan = normalize_plan_sections(proposed_plan)
        if allow_plan_change and not normalized_proposed_plan:
            return self._respond_only(
                session,
                intent=ConsultantIntent.UNKNOWN,
                interpretation={
                    "assistant_message": (
                        "La structure du plan n'est pas assez précise pour être "
                        "enregistrée. Pouvez-vous préciser les sections attendues ?"
                    )
                },
            )
        if (
            action_intent in {
                ConsultantIntent.ADD_TOPIC,
                ConsultantIntent.CHANGE_PLAN,
            }
            and not classification.replace_current_plan
            and not _plan_candidate_covers_current(
                normalized_proposed_plan,
                current_plan,
            )
        ):
            normalized_proposed_plan = _merge_additive_plan_update(
                current_plan,
                normalized_proposed_plan,
            )
        plan_changed = bool(
            allow_plan_change
            and _plan_materially_changed(
                normalized_proposed_plan,
                current_plan,
            )
        )
        if plan_changed:
            contract = (
                update_edited_plan(contract, normalized_proposed_plan)
                if contract
                else create_contract(normalized_proposed_plan)
            )
            write_json(contract_path, contract)
        plan_history = _plan_history_from_session(session)
        if plan_changed:
            plan_history = _append_plan_history(
                plan_history,
                normalized_proposed_plan,
                version=contract.get("plan_version"),
            )

        topics = (
            interpretation.get("topics")
            if isinstance(interpretation.get("topics"), list)
            else []
        )
        constraints = (
            interpretation.get("constraints")
            if isinstance(interpretation.get("constraints"), list)
            else []
        )
        if (
            action_intent == ConsultantIntent.DESCRIBE_REQUIREMENTS
            and not topics
            and not constraints
        ):
            return self._respond_only(
                session,
                intent=ConsultantIntent.UNKNOWN,
                interpretation={
                    "assistant_message": (
                        "Je n'ai pas identifié d'exigence durable assez précise. "
                        "Pouvez-vous préciser ce qui doit changer dans le livrable ?"
                    )
                },
            )

        search_requests = (
            interpretation.get("search_requests")
            if (
                force_search
                and classification.explicit_research_command
                and isinstance(interpretation.get("search_requests"), list)
            )
            else []
        )
        if force_search and not search_requests:
            return self._respond_only(
                session,
                intent=ConsultantIntent.UNKNOWN,
                interpretation={
                    "assistant_message": (
                        "J'ai compris que vous souhaitez une recherche, mais "
                        "la cible n'est pas assez précise. Quel sujet ou quelle "
                        "source dois-je rechercher ?"
                    )
                },
            )

        brief_changed = bool(plan_changed or topics or constraints)
        brief_needs_update = bool(brief_changed or session.brief is None)
        previous_topics = (
            [topic.model_dump(mode="json") for topic in session.brief.requested_topics]
            if session.brief
            else []
        )
        if brief_needs_update:
            previous_request = (
                session.brief.raw_request if session.brief else ""
            )
            brief = _brief_from_contract(
                contract,
                raw_request=_clean(
                    "\n".join(
                        value
                        for value in (previous_request, message)
                        if value
                    ),
                    12000,
                ),
                topics=[*previous_topics, *topics],
                constraints=[
                    *((session.brief.general_constraints if session.brief else []) or []),
                    *constraints,
                ],
            )
            self.state_manager.update_brief(db, session.session_id, brief)
        else:
            brief = session.brief

        if brief is None:
            return self._respond_only(
                session,
                intent=ConsultantIntent.UNKNOWN,
                interpretation={
                    "assistant_message": (
                        "Je n'ai pas obtenu assez d'éléments structurés pour "
                        "appliquer cette demande. Pouvez-vous la préciser ?"
                    )
                },
            )

        if force_search:
            enriched_requests: list[dict[str, Any]] = []
            for request in search_requests:
                if not isinstance(request, Mapping):
                    continue
                enriched = dict(request)
                if "target_context_dimensions" not in enriched:
                    target_dimensions = (
                        WebResearchService._target_context_dimensions(enriched)
                    )
                    if target_dimensions:
                        enriched["target_context_dimensions"] = target_dimensions
                enriched_requests.append(enriched)
            search_requests = enriched_requests
            research = self._run_research(db, project, session, search_requests)
            candidates = research.get("candidates") or []
            assistant = self._synthesize_research_response(
                project=project,
                contract=contract,
                consultant_message=message,
                candidates=candidates,
                research_completeness=research.get("completeness") or {},
            )
            if not assistant:
                assistant = _clean(interpretation.get("assistant_message"), 6000)
                if candidates:
                    assistant += (
                        "\n\nLa recherche ciblée est terminée. "
                        + self._candidate_summary(candidates)
                        + " Je peux maintenant examiner avec vous les sources à valider."
                    )
                else:
                    assistant += (
                        "\n\nCette recherche n'a pas produit de source exploitable. "
                        "Précisez la cible ou demandez une autre stratégie de recherche."
                    )
            response_state = (
                GuidedResearchState.WAITING_CONSULTANT_FEEDBACK
                if candidates
                else GuidedResearchState.RESEARCH_REFINEMENT
            )
            return self._response(
                session.session_id,
                action_intent,
                response_state,
                assistant.strip(),
                (
                    NextAction.REVIEW_SOURCES
                    if candidates
                    else NextAction.RUN_RESEARCH
                ),
                brief,
                False,
                {
                    "contract": contract,
                    "research": research,
                    "candidates": candidates,
                    "plan_changed": False,
                    "trigger_state_of_art_generation": False,
                    "conversation_natural": True,
                },
            )

        coverage = self._coverage(project, brief)
        target_state = (
            GuidedResearchState.BRIEF_PARSED
            if plan_changed
            else (
                GuidedResearchState.BRIEF_PARSED
                if (
                    session.state == GuidedResearchState.BRIEF_IN_PROGRESS
                    and brief_needs_update
                )
                else session.state
            )
        )
        target_ready = False if plan_changed else session.ready_to_write
        if brief_needs_update:
            self.repository.update(
                db,
                session.session_id,
                writing_contract=contract if plan_changed else None,
                coverage=coverage,
                state=target_state,
                ready_to_write=target_ready,
                context_updates={
                    "plan_approved": (
                        False
                        if plan_changed
                        else bool(contract.get("approved_plan"))
                    ),
                    "writing_authorized": (
                        False
                        if plan_changed
                        else bool(session.context.get("writing_authorized"))
                    ),
                    "last_interpreter": interpretation.get("interpreter"),
                    **(
                        {"plan_history": plan_history}
                        if plan_changed
                        else {}
                    ),
                },
            )

        assistant = _clean(interpretation.get("assistant_message"), 6000)
        if action_intent in _PLAN_ACTION_INTENTS:
            assistant = (
                "Voici le plan détaillé proposé pour validation :\n\n"
                + _format_plan_for_review(_contract_sections(contract))
                + "\n\nConfirmez ce plan ou demandez un nouvel ajustement. "
                "Aucune recherche ni rédaction n'a été lancée."
            )
        elif not assistant:
            assistant = (
                "J'ai intégré cette exigence au brief du document. "
                "Aucune recherche ni rédaction n'a été lancée."
            )
        return self._response(
            session.session_id,
            action_intent,
            target_state,
            assistant,
            NextAction.CONTINUE_BRIEF,
            brief,
            target_ready,
            {
                "contract": contract,
                "coverage": coverage,
                "plan_changed": plan_changed,
                "trigger_state_of_art_generation": False,
                "conversation_natural": True,
            },
        )

    def _accept_plan(
        self,
        db: Session,
        project: Any,
        session: GuidedResearchSessionData,
        contract: dict[str, Any],
        contract_path: Path,
        *,
        conversation_reply: str = "",
    ) -> ConversationResponse:
        if not _contract_sections(contract):
            return self._response(
                session.session_id,
                ConsultantIntent.ACCEPT_PLAN,
                GuidedResearchState.BRIEF_IN_PROGRESS,
                "Nous n'avons pas encore de plan à valider. Décrivez-moi d'abord l'histoire et les parties attendues.",
                NextAction.CONTINUE_BRIEF,
                session.brief,
                False,
            )
        contract = approve_plan(
            contract, approved_by=str(session.created_by_user_id or "consultant")
        )
        write_json(contract_path, contract)
        brief = _brief_from_contract(
            contract,
            raw_request=session.brief.raw_request if session.brief else "",
            topics=(
                [topic.model_dump(mode="json") for topic in session.brief.requested_topics]
                if session.brief
                else []
            ),
            constraints=session.brief.general_constraints if session.brief else [],
        )
        self.state_manager.update_brief(db, session.session_id, brief)
        coverage = self._coverage(project, brief)
        weak = coverage.get("weak_sections") or []
        assistant = conversation_reply or (
            "Le plan est validé et devient la structure obligatoire du document. "
            "La préparation scientifique pourra seulement alimenter ce plan, jamais le remplacer."
        )
        if weak:
            assistant += (
                "\n\nAvant d'écrire, je vois encore "
                f"{len(weak)} partie(s) faiblement couverte(s). "
                "Je lancerai une recherche ciblée si vous me demandez de rédiger, sauf si vous précisez de travailler uniquement avec les sources actuelles."
            )
        else:
            assistant += "\n\nLa couverture est suffisante. Vous pouvez me dire naturellement de commencer la rédaction."
        self.repository.update(
            db,
            session.session_id,
            writing_contract=contract,
            coverage=coverage,
            state=GuidedResearchState.READY_TO_WRITE,
            ready_to_write=True,
            context_updates={"plan_approved": True, "writing_authorized": False},
        )
        return self._response(
            session.session_id,
            ConsultantIntent.ACCEPT_PLAN,
            GuidedResearchState.READY_TO_WRITE,
            assistant,
            NextAction.WAIT_FOR_WRITE_COMMAND,
            brief,
            True,
            {"contract": contract, "coverage": coverage, "pipeline_started": False},
        )

    def _start_writing(
        self,
        db: Session,
        project: Any,
        session: GuidedResearchSessionData,
        contract: dict[str, Any],
        contract_path: Path,
        message: str,
        *,
        explicit_plan_approval: bool = False,
        use_current_sources_only: bool = False,
        writing_source_scope: str = "unspecified",
        writing_source_identifiers: Iterable[str] | None = None,
        requested_source_count: int | None = None,
        action_intent: ConsultantIntent = ConsultantIntent.START_WRITING,
        conversation_reply: str = "",
    ) -> ConversationResponse:
        revision_request = (
            message
            if action_intent == ConsultantIntent.REVISE_DRAFT
            else ""
        )
        if _clean(session.context.get("operating_mode"), 80) == "standalone_chat":
            snapshot = self.repository.snapshot(db, session.session_id)
            context = dict(snapshot.get("context") or {})
            all_verrous = [
                dict(row)
                for row in (context.get("consultant_verrous") or [])
                if isinstance(row, Mapping)
            ]
            review_scope = _clean(context.get("review_scope"), 40) or (
                "global" if len(all_verrous) > 1 else "per_verrou"
            )
            active_ids = {
                _clean(value, 120)
                for value in (context.get("active_verrou_ids") or [])
                if _clean(value, 120)
            }
            active_verrous = (
                all_verrous
                if review_scope == "global" or not active_ids
                else [
                    row
                    for row in all_verrous
                    if _clean(row.get("id"), 120) in active_ids
                ]
            )

            # En mode autonome, une commande combinée « je valide et rédige »
            # doit enregistrer l'approbation avant l'appel du writer. Ainsi un
            # incident de rédaction ne fait pas perdre la décision du consultant
            # et une relance courte peut réellement reprendre le même plan.
            approved_plan: list[dict[str, Any]] = []
            if _contract_sections(contract):
                active_verrou_ids = [
                    _clean(row.get("id"), 120)
                    for row in active_verrous
                    if _clean(row.get("id"), 120)
                ]
                scoped_plan = []
                for row in _contract_sections(contract):
                    scoped = dict(row)
                    if not scoped.get("verrou_ids"):
                        scoped["verrou_ids"] = active_verrou_ids
                    scoped_plan.append(scoped)
                if plan_hash(scoped_plan) != plan_hash(
                    _contract_sections(contract)
                ):
                    was_approved = bool(contract.get("approved_plan"))
                    contract = create_contract(
                        scoped_plan,
                        version=int(contract.get("plan_version") or 1) + 1,
                    )
                    if was_approved or explicit_plan_approval:
                        contract = approve_plan(
                            contract,
                            approved_by=str(
                                session.created_by_user_id or "consultant"
                            ),
                        )
                approved_contract = _approve_for_combined_write(
                    contract,
                    message,
                    approved_by=str(session.created_by_user_id or "consultant"),
                    explicit_approval=explicit_plan_approval,
                )
                try:
                    contract = authorize_writing(approved_contract)
                except Exception as exc:
                    return self._response(
                        session.session_id,
                        action_intent,
                        GuidedResearchState.BRIEF_IN_PROGRESS,
                        f"Je ne lance pas encore la rédaction : {exc}",
                        NextAction.CONTINUE_BRIEF,
                        session.brief,
                        False,
                        {
                            "operating_mode": "standalone_chat",
                            "trigger_state_of_art_generation": False,
                        },
                    )
                approved_plan = [
                    dict(row)
                    for row in (contract.get("approved_plan") or [])
                    if isinstance(row, Mapping)
                ]
                write_json(contract_path, contract)
                self.repository.update(
                    db,
                    session.session_id,
                    writing_contract=contract,
                    state=GuidedResearchState.READY_TO_WRITE,
                    ready_to_write=True,
                    context_updates={
                        "plan_approved": True,
                        "writing_authorized": True,
                    },
                )

            if not approved_plan:
                return self._response(
                    session.session_id,
                    action_intent,
                    GuidedResearchState.BRIEF_IN_PROGRESS,
                    (
                        "Je dois d'abord disposer d'un plan validÃ© avant de "
                        "lancer toutes les phases de rÃ©daction. Proposez ou "
                        "validez le plan, puis relancez."
                    ),
                    NextAction.CONTINUE_BRIEF,
                    session.brief,
                    False,
                    {
                        "operating_mode": "standalone_chat",
                        "trigger_state_of_art_generation": False,
                    },
                )

            # Le mode autonome suit dÃ©sormais exactement le pipeline commun :
            # reconstruction sans LLM des Phases 1/2, puis dÃ©clenchement des
            # Phases 3, 4, 4.5, 4.6, 4.7 et 5 par le frontend.
            try:
                from services.scholar_state_of_art_payload_service import (
                    build_state_of_art_selection_payload,
                )
                from services.article_card_builder import (
                    build_article_cards_for_selected_articles,
                )

                selection_payload = build_state_of_art_selection_payload(
                    db,
                    project,
                )
                cards_payload = build_article_cards_for_selected_articles(
                    db=db,
                    project=project,
                    mode="auto",
                    force=False,
                )
            except Exception as exc:
                print(
                    "[EnnoScholar][STANDALONE][PHASE1_2][ERROR] "
                    f"{type(exc).__name__}: {_clean(exc, 1200)}"
                )
                return self._response(
                    session.session_id,
                    action_intent,
                    GuidedResearchState.READY_TO_WRITE,
                    (
                        "La prÃ©paration scientifique n'est pas encore complÃ¨te. "
                        "Le plan et les sources sont conservÃ©s ; relancez la "
                        "rÃ©daction sans refaire la recherche."
                    ),
                    NextAction.START_WRITING,
                    session.brief,
                    True,
                    {
                        "operating_mode": "standalone_chat",
                        "trigger_state_of_art_generation": False,
                        "retryable": True,
                        "error_type": type(exc).__name__,
                    },
                )

            self.repository.update(
                db,
                session.session_id,
                state=GuidedResearchState.WRITING_IN_PROGRESS,
                ready_to_write=False,
                context_updates={
                    "pending_write_request": False,
                    "pipeline_execution_requested": True,
                    "write_with_current_sources": True,
                    "generation_mode": (
                        "partial_revision"
                        if revision_request
                        else "full_generation"
                    ),
                    "revision_request": _clean(revision_request, 6000),
                    "standalone_full_pipeline": True,
                },
            )
            return self._response(
                session.session_id,
                action_intent,
                GuidedResearchState.WRITING_IN_PROGRESS,
                (
                    "Je lance la rÃ©daction avec le plan validÃ© et toutes les "
                    "publications prÃªtes, y compris les PDF ajoutÃ©s depuis votre "
                    "ordinateur. Le dossier va parcourir les Phases 1 Ã  5 sans "
                    "nouvelle recherche."
                ),
                NextAction.START_WRITING,
                session.brief,
                False,
                {
                    "operating_mode": "standalone_chat",
                    "trigger_state_of_art_generation": True,
                    "standalone_full_pipeline": True,
                    "phase_1": selection_payload.get("selection_summary") or {},
                    "phase_2": {
                        "cards_count": cards_payload.get("cards_count"),
                        "writing_ready_cards_count": cards_payload.get(
                            "writing_ready_cards_count"
                        ),
                    },
                },
            )

            from .standalone_state_of_art_writer_service import (
                run_standalone_state_of_art_writer,
            )

            try:
                draft_result = run_standalone_state_of_art_writer(
                    llm=self.llm,
                    project_brief=dict(
                        context.get("standalone_project_brief") or {}
                    ),
                    verrous=active_verrous,
                    review_scope=review_scope,
                    selected_sources=list(
                        snapshot.get("selected_sources") or []
                    ),
                    cards_payload=read_json(self._cards_path(project)),
                    output_dir=(
                        state_of_art_root(
                            str(project.organisme),
                            str(project.project_name),
                            str(project.year),
                        )
                        / "guided_sessions"
                        / session.session_id
                    ),
                    revision_request=revision_request,
                    current_markdown=_clean(
                        (snapshot.get("draft") or {}).get("markdown"),
                        100000,
                    ),
                    approved_plan=approved_plan,
                )
            except Exception as exc:
                print(
                    "[EnnoScholar][STANDALONE][ERROR] "
                    f"{type(exc).__name__}: {_clean(exc, 1200)}"
                )
                return self._response(
                    session.session_id,
                    action_intent,
                    GuidedResearchState.READY_TO_WRITE,
                    (
                        "Une erreur technique interne a interrompu la rédaction. "
                        "Votre plan approuvé, vos sources validées et votre éventuel "
                        "document précédent sont conservés ; vous pouvez relancer "
                        "sans recommencer la recherche."
                    ),
                    NextAction.START_WRITING,
                    session.brief,
                    True,
                    {
                        "operating_mode": "standalone_chat",
                        "trigger_state_of_art_generation": False,
                        "retryable": True,
                        "error_type": type(exc).__name__,
                    },
                )

            if not draft_result.get("ok"):
                return self._response(
                    session.session_id,
                    action_intent,
                    GuidedResearchState.READY_TO_WRITE,
                    _clean(draft_result.get("message"), 3000)
                    or (
                        "Je n'ai pas publié cette tentative : certaines parties "
                        "ne sont pas encore suffisamment reliées aux publications "
                        "validées. Votre travail précédent est conservé. Vous pouvez "
                        "valider des sources plus complètes ou demander une version "
                        "strictement limitée aux preuves actuellement disponibles."
                    ),
                    NextAction.START_WRITING,
                    session.brief,
                    True,
                    {
                        "operating_mode": "standalone_chat",
                        "standalone_draft": draft_result,
                        "trigger_state_of_art_generation": False,
                    },
                )

            self.repository.update(
                db,
                session.session_id,
                draft=draft_result,
                state=GuidedResearchState.DRAFT_READY,
                ready_to_write=False,
                context_updates={
                    "pending_write_request": False,
                    "pipeline_execution_requested": False,
                    "last_standalone_draft_at": draft_result.get(
                        "generated_at"
                    ),
                },
            )
            quality = dict(draft_result.get("quality") or {})
            assistant_message = (
                "L'état de l'art autonome a été rédigé à partir des seules "
                "Article Cards validées. Il couvre "
                + (
                    "tous les verrous de la conversation."
                    if review_scope == "global"
                    else "le verrou ciblé."
                )
            )
            if not quality.get("consultant_quality_ready"):
                assistant_message += (
                    " Une première version sourcée est disponible ; vous pouvez "
                    "demander un approfondissement ciblé pour augmenter son niveau "
                    "de détail."
                )
            return self._response(
                session.session_id,
                action_intent,
                GuidedResearchState.DRAFT_READY,
                assistant_message,
                NextAction.REVIEW_DRAFT,
                session.brief,
                False,
                {
                    "operating_mode": "standalone_chat",
                    "standalone_draft": draft_result,
                    "standalone_draft_markdown": draft_result.get("markdown"),
                    "trigger_state_of_art_generation": False,
                },
            )

        generation_mode = (
            "partial_revision"
            if revision_request
            else "full_generation"
        )
        stored_source_policy = (
            session.context.get("writing_source_policy")
            if isinstance(
                session.context.get("writing_source_policy"),
                Mapping,
            )
            else {}
        )
        if (
            _clean(writing_source_scope, 80) == "unspecified"
            and not stored_source_policy.get("grounded_in_current_message")
        ):
            # Migration sûre des anciennes sessions : avant l'ancrage au tour
            # courant, une politique pouvait contenir des références reprises
            # de la mémoire. Elle ne doit pas bloquer une nouvelle rédaction.
            stored_source_policy = {}
        effective_source_scope = _clean(writing_source_scope, 80)
        if effective_source_scope == "unspecified":
            effective_source_scope = _clean(
                stored_source_policy.get("scope"),
                80,
            ) or "all_validated"
        effective_source_count = requested_source_count
        if effective_source_count is None:
            try:
                effective_source_count = int(
                    stored_source_policy.get("requested_source_count")
                )
            except (TypeError, ValueError):
                effective_source_count = None
        effective_source_identifiers = _resolve_candidate_display_identifiers(
            _resolve_effective_writing_source_identifiers(
                writing_source_identifiers,
                stored_source_policy.get("source_identifiers") or [],
                effective_source_count,
            ),
            session.context.get("current_candidate_ids") or [],
        )
        source_policy = {
            "scope": effective_source_scope,
            "source_identifiers": effective_source_identifiers,
            "requested_source_count": effective_source_count,
            # Un nombre explicitement demandé définit un corpus exhaustif :
            # la Phase 5 devra utiliser chaque source ou refuser le statut
            # "niveau consultant". Sans nombre, le corpus reste une frontière
            # d'autorisation et les sources non pertinentes peuvent rester
            # inutilisées avec un rapport de couverture.
            "require_all_selected_sources": (
                effective_source_count is not None
            ),
            "exclude_external_research": (
                effective_source_scope == "baseline_verrou_corpus"
            ),
            "grounded_in_current_message": bool(
                _clean(writing_source_scope, 80) != "unspecified"
                or stored_source_policy.get("grounded_in_current_message")
            ),
        }

        approved_contract = _approve_for_combined_write(
            contract,
            message,
            approved_by=str(session.created_by_user_id or "consultant"),
            explicit_approval=explicit_plan_approval,
        )
        try:
            contract = authorize_writing(approved_contract)
        except Exception as exc:
            return self._response(
                session.session_id,
                action_intent,
                GuidedResearchState.BRIEF_IN_PROGRESS,
                f"Je ne lance pas encore la rédaction : {exc}",
                NextAction.CONTINUE_BRIEF,
                session.brief,
                False,
                {"trigger_state_of_art_generation": False},
            )
        contract["writing_source_policy"] = source_policy
        brief = session.brief or _brief_from_contract(contract, raw_request=message)
        coverage = self._coverage(project, brief)
        weak = coverage.get("weak_sections") or []
        bypass_search = bool(
            use_current_sources_only
            or effective_source_scope != "all_validated"
        )
        snapshot = self.repository.snapshot(db, session.session_id)
        already_searched = bool((snapshot.get("context") or {}).get("external_research_started"))
        if weak and not bypass_search and not already_searched:
            write_json(contract_path, approved_contract)
            self.repository.update(
                db,
                session.session_id,
                writing_contract=approved_contract,
                coverage=coverage,
                state=GuidedResearchState.READY_TO_WRITE,
                ready_to_write=True,
                context_updates={
                    "plan_approved": True,
                    "writing_authorized": False,
                    "pipeline_execution_requested": False,
                },
            )
            return self._response(
                session.session_id,
                action_intent,
                GuidedResearchState.READY_TO_WRITE,
                (
                    "Le plan est prêt, mais certaines sections manquent encore "
                    "de preuves. Souhaitez-vous lancer une recherche ciblée, ou "
                    "rédiger avec les sources actuellement validées ?"
                ),
                NextAction.RUN_RESEARCH,
                brief,
                True,
                {
                    "coverage": coverage,
                    "trigger_state_of_art_generation": False,
                    "awaiting_source_policy": True,
                },
            )
        write_json(contract_path, contract)
        self.repository.update(
            db,
            session.session_id,
            writing_contract=contract,
            coverage=coverage,
            state=GuidedResearchState.WRITING_IN_PROGRESS,
            ready_to_write=False,
            context_updates={
                "plan_approved": True,
                "writing_authorized": True,
                "pipeline_execution_requested": True,
                "write_with_current_sources": bypass_search,
                "generation_mode": generation_mode,
                "revision_request": _clean(revision_request, 6000),
                "writing_source_policy": source_policy,
            },
        )
        return self._response(
            session.session_id,
            action_intent,
            GuidedResearchState.WRITING_IN_PROGRESS,
            conversation_reply
            or (
                (
                    "Je lance la révision ciblée du document existant. "
                    "Les sections compatibles seront conservées et les parties concernées, "
                    "avec leurs transitions, seront recalculées à partir des sources validées."
                )
                if generation_mode == "partial_revision"
                else (
                    "Je commence la rédaction selon le plan validé. "
                    "Le texte sera construit section par section, avec définitions claires, "
                    "procédures expliquées, résultats argumentés, comparaisons, limites et transitions."
                )
            ),
            NextAction.START_WRITING,
            brief,
            False,
            {
                "contract": contract,
                "contract_path": str(contract_path),
                "coverage": coverage,
                "trigger_state_of_art_generation": True,
                "generation_mode": generation_mode,
                "revision_request": _clean(revision_request, 6000),
                "reuse_compatible_section_checkpoints": True,
                "pipeline_started": False,
                "writing_source_policy": source_policy,
            },
        )

    def _synthesize_research_response(
        self,
        *,
        project: Any,
        contract: Mapping[str, Any],
        consultant_message: str,
        candidates: Iterable[Mapping[str, Any]],
        research_completeness: Mapping[str, Any] | None = None,
    ) -> str:
        """Produit une réponse de recherche utile sans écrire ni modifier le document."""
        _ = project
        candidate_rows: list[dict[str, Any]] = []
        for index, source in enumerate(candidates, start=1):
            if not isinstance(source, Mapping):
                continue
            candidate_rows.append({
                "source_ref": f"C{index}",
                "candidate_id": _clean(source.get("candidate_id"), 120),
                "title": _clean(source.get("title"), 500),
                "year": source.get("year"),
                "authors": list(source.get("authors") or [])[:8],
                "candidate_kind": _clean(source.get("candidate_kind"), 80),
                "provider": _clean(source.get("provider"), 80),
                "url": _clean(source.get("url"), 1500),
                "abstract_or_excerpt": _clean(
                    source.get("abstract")
                    or source.get("content_excerpt")
                    or source.get("summary"),
                    1400,
                ),
                "evidence_scope": list(source.get("evidence_scope") or []),
                "scientific_evidence_eligible": bool(
                    source.get("scientific_evidence_eligible")
                ),
                "open_access": source.get("open_access"),
                "relevance_score": source.get("relevance_score"),
                "selection_priority_score": source.get(
                    "selection_priority_score"
                ),
                "relevance_role": _clean(
                    source.get("relevance_role"),
                    80,
                ),
                "role_reason": _clean(
                    source.get("role_reason"),
                    500,
                ),
                "role_confidence": source.get("role_confidence"),
            })
            if len(candidate_rows) >= 14:
                break

        current_plan = [
            {
                "section_id": _clean(row.get("section_id"), 120),
                "title": _clean(row.get("title"), 400),
                "objective": _clean(row.get("objective"), 900),
                "parent_id": row.get("parent_id"),
                "level": row.get("level"),
            }
            for row in _contract_sections(contract)
            if isinstance(row, Mapping)
        ]
        allow_plan_suggestions = bool(
            re.search(
                r"\b(?:plan|section|sous-section|chapitre|sommaire|"
                r"integrer|intégrer|ajouter|emplacement)\b",
                _norm(consultant_message),
            )
        )
        prompt = f"""
Tu réponds au consultant juste après une recherche ciblée pour un état de l'art R&D.
Tu analyses uniquement les candidats fournis et retournes une structure qui sera
rendue par le serveur avec des catégories inviolables.

DEMANDE DU CONSULTANT
{consultant_message}

PLAN ACTUEL, fourni seulement si une suggestion de plan est explicitement demandée
{json.dumps(current_plan, ensure_ascii=False)}

SUGGESTION DE PLAN AUTORISÉE
{json.dumps(allow_plan_suggestions)}

CANDIDATS DE RECHERCHE
{json.dumps(candidate_rows, ensure_ascii=False)}

CONTRÔLE AUTOMATIQUE DE COMPLÉTUDE
{json.dumps(dict(research_completeness or {}), ensure_ascii=False)}

Retourne uniquement ce JSON :
{{
  "direct_evidence": [
    {{"source_refs": ["C1"], "analysis": "analyse factuelle courte"}}
  ],
  "official_documentation": [
    {{"source_refs": ["C2"], "analysis": "capacité ou procédure documentée"}}
  ],
  "connected_evidence": [
    {{"source_refs": ["C3"], "analysis": "apport comparatif et limite de transfert"}}
  ],
  "implementation": [
    {{"source_refs": ["C4"], "analysis": "apport technique seulement"}}
  ],
  "transferability_conditions": [
    {{"source_refs": ["C1", "C3"], "analysis": "condition vérifiable"}}
  ],
  "limitations": [
    {{"source_refs": ["C2"], "analysis": "limite vérifiable"}}
  ],
  "plan_suggestions": [
    "suggestion seulement si SUGGESTION DE PLAN AUTORISÉE vaut true"
  ]
}}

CONSIGNES IMPÉRATIVES
- Analyse les métadonnées, résumés ou extraits sans inventer.
- N'invente aucune preuve, aucun résultat numérique et aucune relation entre la technologie étudiée et le projet.
- Place chaque source uniquement dans la catégorie relevance_role fournie par le serveur.
- direct_evidence accepte exclusivement les candidats relevance_role=direct_evidence.
- official_documentation accepte exclusivement relevance_role=official_documentation et décrit
  des capacités ou procédures, jamais une preuve de performance ou d'applicabilité scientifique.
- connected_evidence ne devient jamais une preuve directe.
- implementation reste un artefact technique.
- N'écris aucune référence C1, C2, etc. dans analysis : utilise seulement source_refs.
- Si aucune preuve directe n'existe, laisse direct_evidence vide.
- Laisse plan_suggestions vide lorsque SUGGESTION DE PLAN AUTORISÉE vaut false.
- Ne rédige aucune section de l'état de l'art et n'annonce aucune action.
""".strip()
        try:
            raw = self.llm.generate(
                prompt,
                temperature=0.0,
                max_output_tokens=2600,
                json_mode=True,
                request_name="ennoscholar:guided_research:research_synthesis",
            )
            parsed = _extract_json(raw)
        except Exception:
            parsed = {}

        source_by_ref = {
            str(row.get("source_ref") or ""): row
            for row in candidate_rows
            if row.get("source_ref")
        }

        def render_items(
            payload_key: str,
            allowed_roles: set[str],
            *,
            limit: int,
            fallback_to_sources: bool = True,
        ) -> list[str]:
            rendered: list[str] = []
            payload_rows = (
                parsed.get(payload_key)
                if isinstance(parsed, Mapping)
                else []
            )
            for item in payload_rows if isinstance(payload_rows, list) else []:
                if not isinstance(item, Mapping):
                    continue
                requested_refs = item.get("source_refs") or []
                if isinstance(requested_refs, str):
                    requested_refs = [requested_refs]
                valid_refs = [
                    str(ref)
                    for ref in requested_refs
                    if str(ref) in source_by_ref
                    and str(
                        source_by_ref[str(ref)].get("relevance_role") or ""
                    )
                    in allowed_roles
                ]
                if not valid_refs:
                    continue
                analysis = re.sub(
                    r"\bC\d+\b",
                    "",
                    _clean(item.get("analysis"), 1400),
                    flags=re.I,
                ).strip(" ,;:-")
                if not analysis:
                    continue
                titles = "; ".join(
                    _clean(source_by_ref[ref].get("title"), 300)
                    for ref in valid_refs
                )
                rendered.append(
                    f"- {', '.join(valid_refs)} — {titles} : {analysis}"
                )
                if len(rendered) >= limit:
                    break
            if rendered or not fallback_to_sources:
                return rendered

            # Repli déterministe : les rôles calculés restent inchangés même
            # si le modèle de synthèse retourne un JSON incomplet.
            for source in candidate_rows:
                if str(source.get("relevance_role") or "") not in allowed_roles:
                    continue
                ref = str(source.get("source_ref") or "")
                title = _clean(source.get("title"), 300)
                reason = _clean(
                    source.get("role_reason")
                    or source.get("abstract_or_excerpt"),
                    700,
                )
                rendered.append(
                    f"- {ref} — {title}"
                    + (f" : {reason}" if reason else "")
                )
                if len(rendered) >= limit:
                    break
            return rendered

        direct = render_items(
            "direct_evidence",
            {"direct_evidence"},
            limit=5,
        )
        documentation = render_items(
            "official_documentation",
            {"official_documentation"},
            limit=5,
        )
        connected = render_items(
            "connected_evidence",
            {"connected_evidence"},
            limit=7,
        )
        implementation = render_items(
            "implementation",
            {"implementation"},
            limit=3,
        )

        sections: list[str] = ["Résultat de la recherche ciblée"]
        sections.extend([
            "\nPreuves scientifiques directes",
            *(
                direct
                or [
                    "- Aucune preuve scientifique directe n'a été identifiée "
                    "après les recherches automatiques ciblées."
                ]
            ),
            "\nDocumentation officielle — capacités techniques, pas preuves scientifiques",
            *(
                documentation
                or ["- Aucune documentation officielle vérifiée n'a été trouvée."]
            ),
            "\nSources scientifiques connexes",
            *(
                connected
                or ["- Aucune source scientifique connexe n'a été retenue."]
            ),
        ])
        if implementation:
            sections.extend([
                "\nImplémentations et artefacts techniques",
                *implementation,
            ])

        for payload_key, heading in (
            ("transferability_conditions", "Conditions de transférabilité"),
            ("limitations", "Limites et preuves manquantes"),
        ):
            rows = render_items(
                payload_key,
                {
                    "direct_evidence",
                    "official_documentation",
                    "connected_evidence",
                    "implementation",
                },
                limit=6,
                fallback_to_sources=False,
            )
            if rows:
                sections.extend([f"\n{heading}", *rows])

        if allow_plan_suggestions and isinstance(parsed, Mapping):
            suggestions = [
                _clean(value, 900)
                for value in (parsed.get("plan_suggestions") or [])
                if _clean(value, 900)
            ][:4]
            if suggestions:
                sections.extend([
                    "\nEmplacements possibles dans le plan — aucune modification appliquée",
                    *[f"- {value}" for value in suggestions],
                ])

        missing = set(
            dict(research_completeness or {}).get(
                "missing_source_types"
            )
            or []
        )
        if "direct_scientific_evidence" in missing:
            sections.extend([
                "\nConclusion",
                "La recherche reste insuffisante pour revendiquer une preuve "
                "directe dans le contexte cible. Les sources connexes peuvent "
                "éclairer une étude de transférabilité, sans la valider.",
            ])
        else:
            sections.extend([
                "\nConclusion",
                "La sélection contient les catégories de sources demandées. "
                "Vous pouvez maintenant valider uniquement les candidats utiles.",
            ])
        return "\n".join(sections).strip()[:10000]

    def _coverage(
        self,
        project: Any,
        brief: ConsultantBrief,
    ) -> dict[str, Any]:
        cards_payload = read_json(self._cards_path(project))
        cards = []
        for key in ("cards", "article_cards", "items", "articles"):
            value = cards_payload.get(key)
            if isinstance(value, list):
                cards = [row for row in value if isinstance(row, dict)]
                break
        supplemental_source_kinds = {
            "official_documentation",
            "official_website",
            "software_repository",
            "technical_documentation",
            "documentation",
        }
        supplemental = [
            row
            for row in (read_json(self._sources_path(project)).get("sources") or [])
            if isinstance(row, dict)
            and str(row.get("candidate_kind") or "").strip().lower() in supplemental_source_kinds
        ]
        corpus = [*cards, *supplemental]
        rows: list[dict[str, Any]] = []
        for section in brief.requested_sections:
            query = " ".join(
                [
                    section.title,
                    section.objective,
                    *section.instructions,
                    *section.required_dimensions,
                ]
            )
            wanted = _tokens(query)
            matched: list[dict[str, Any]] = []
            for source in corpus:
                source_text = " ".join(
                    _clean(source.get(key), 5000)
                    for key in (
                        "title",
                        "abstract",
                        "summary",
                        "technical_principle",
                        "method",
                        "results",
                        "limitations",
                        "content_excerpt",
                    )
                )
                found = _tokens(source_text)
                overlap = len(wanted & found) / max(1, len(wanted))
                if overlap >= 0.08 or len(wanted & found) >= 2:
                    matched.append({
                        "citation": source.get("citation_label") or source.get("citation_id"),
                        "title": source.get("title"),
                        "overlap": round(overlap, 3),
                        "source_kind": source.get("candidate_kind") or "scientific_article",
                    })
            scientific_count = sum(
                (row.get("source_kind") or "scientific_article") == "scientific_article"
                for row in matched
            )
            status = (
                "covered"
                if scientific_count >= 3
                else "partial"
                if scientific_count >= 1
                else "absent"
            )
            rows.append({
                "section_id": section.section_id,
                "title": section.title,
                "coverage_status": status,
                "matched_sources_count": len(matched),
                "scientific_sources_count": scientific_count,
                "matched_sources": sorted(
                    matched, key=lambda row: float(row.get("overlap") or 0), reverse=True
                )[:12],
                "required_dimensions": section.required_dimensions,
                "target_verrous": section.target_verrous,
            })
        weak = [row for row in rows if row["coverage_status"] != "covered"]
        return {
            "ok": True,
            "payload_type": "guided_section_coverage_v2",
            "section_coverage": rows,
            "weak_sections": weak,
            "sufficient": not weak,
            "scientific_cards_count": len(cards),
            "accepted_supplemental_sources_count": len(supplemental),
            "policy": {
                "coverage_is_section_specific": True,
                "official_documentation_does_not_replace_scientific_results": True,
                "weak_section_triggers_targeted_research_before_writing": True,
            },
        }

    def _search_requests_from_coverage(
        self,
        coverage: Mapping[str, Any],
        brief: ConsultantBrief,
    ) -> list[dict[str, Any]]:
        by_id = {section.section_id: section for section in brief.requested_sections}
        requests_payload: list[dict[str, Any]] = []
        for row in (coverage.get("weak_sections") or [])[:6]:
            section = by_id.get(str(row.get("section_id") or ""))
            if section is None:
                continue
            requests_payload.append({
                "query": _clean(
                    f"{section.title} {section.objective} "
                    + " ".join(section.required_dimensions),
                    800,
                ),
                "entity_type": "scientific_method",
                "section_ids": [section.section_id],
                "section_titles": [section.title],
                "target_verrous": section.target_verrous,
                "requested_dimensions": section.required_dimensions
                or ["method", "procedure", "results", "limitations"],
                "source_preferences": section.source_preferences
                or ["articles scientifiques"],
            })
        for topic in brief.requested_topics:
            requests_payload.append({
                "query": topic.name,
                "entity_name": topic.name,
                "entity_type": topic.entity_type.value,
                "section_ids": topic.target_sections,
                "section_titles": [],
                "target_verrous": topic.target_verrous,
                "requested_dimensions": topic.requested_dimensions,
                "source_preferences": topic.source_preferences
                or ["documentation officielle", "articles scientifiques"],
            })
        return requests_payload[:8]

    def _run_research(
        self,
        db: Session,
        project: Any,
        session: GuidedResearchSessionData,
        requests_payload: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot = self.repository.snapshot(db, session.session_id)
        existing = list(snapshot.get("selected_sources") or [])
        decided_sources = [
            dict(row)
            for row in existing
            if str(row.get("consultant_decision") or "")
            in {"accepted", "rejected"}
        ]
        excluded = [
            str(row.get("candidate_id"))
            for row in decided_sources
            if row.get("candidate_id")
        ]
        self.repository.update(
            db,
            session.session_id,
            state=GuidedResearchState.RESEARCH_IN_PROGRESS,
            ready_to_write=False,
        )
        result = self.research.search(
            requests_payload,
            excluded_ids=excluded,
            max_candidates=30,
        )
        batch_id = (
            "BATCH-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        )
        candidates = [
            {
                **dict(row),
                "research_batch_id": batch_id,
                "current_research_batch": True,
            }
            for row in (result.get("candidates") or [])
            if isinstance(row, Mapping)
        ]
        result["candidates"] = candidates
        result["research_batch_id"] = batch_id
        self.repository.update(
            db,
            session.session_id,
            research_plan=result,
            selected_sources=[*decided_sources, *candidates],
            state=(
                GuidedResearchState.WAITING_CONSULTANT_FEEDBACK
                if candidates
                else GuidedResearchState.RESEARCH_REFINEMENT
            ),
            ready_to_write=False,
            context_updates={
                "external_research_started": True,
                "last_research_at": datetime.now(timezone.utc).isoformat(),
                "current_research_batch_id": batch_id,
                "current_candidate_ids": [
                    str(row.get("candidate_id") or "")
                    for row in candidates
                    if row.get("candidate_id")
                ],
            },
        )
        return result

    def decide_sources(
        self,
        db: Session,
        project: Any,
        *,
        session_id: str,
        candidate_ids: list[str],
        decision: str,
        reason: str = "",
        prepare_after_acceptance: bool = True,
    ) -> ConversationResponse:
        # La préparation lourde (PDF direct -> MCP -> passages) est orchestrée
        # par le service backend après cette décision. Le paramètre reste ici
        # pour conserver le contrat public et la décision elle-même reste
        # indépendante d'un éventuel échec réseau.
        _ = prepare_after_acceptance
        session = self.state_manager.get_session(db, session_id)
        if int(session.project_id) != int(project.id):
            raise PermissionError("Cette session n'appartient pas au projet demandé.")
        normalized = _norm(decision)
        normalized = {
            "accept": "accepted",
            "accepted": "accepted",
            "garde": "accepted",
            "reject": "rejected",
            "rejected": "rejected",
            "refuse": "rejected",
        }.get(normalized, normalized)
        if normalized not in {"accepted", "rejected"}:
            raise ValueError("Décision attendue : accepted ou rejected.")
        sources = self.repository.upsert_source_decision(
            db,
            session_id,
            candidate_ids=candidate_ids,
            decision=normalized,
            reason=reason,
        )
        if normalized == "accepted":
            accepted = [
                dict(row) for row in sources
                if row.get("consultant_decision") == "accepted"
            ]
            for source in accepted:
                if (
                    source.get("candidate_kind") in {
                        "official_documentation",
                        "documentation",
                        "software_repository",
                        "research_output",
                    }
                    and source.get("url")
                    and not source.get("content_excerpt")
                ):
                    try:
                        fetched = self.research.fetch_public_content(
                            str(source.get("url"))
                        )
                    except Exception as exc:
                        fetched = {"ok": False, "error": str(exc), "text": ""}
                    if fetched.get("ok"):
                        source["content_excerpt"] = fetched.get("text")
                        source["resolved_url"] = fetched.get("url")
                    source["content_fetch"] = {
                        key: value
                        for key, value in fetched.items()
                        if key != "text"
                    }
            payload = {
                "ok": True,
                "payload_type": "guided_accepted_sources_v1",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "sources": accepted,
                "policy": {
                    "consultant_validated": True,
                    "official_documentation_scope_limited": True,
                    "scientific_results_require_scientific_sources": True,
                },
            }
            write_json(self._sources_path(project), payload)
            current_contract = read_json(self._contract_path(project))
            previous_brief = session.brief
            brief = _brief_from_contract(
                current_contract,
                raw_request=(
                    previous_brief.raw_request
                    if previous_brief
                    else ""
                ),
                topics=(
                    [
                        topic.model_dump(mode="json")
                        for topic in previous_brief.requested_topics
                    ]
                    if previous_brief
                    else []
                ),
                constraints=(
                    list(previous_brief.general_constraints)
                    if previous_brief
                    else []
                ),
            )
            self.state_manager.update_brief(db, session_id, brief)
            coverage = self._coverage(project, brief)
            approved = bool(
                read_json(self._contract_path(project)).get("approved_plan")
            )
            self.repository.update(
                db,
                session_id,
                selected_sources=sources,
                coverage=coverage,
                state=(
                    GuidedResearchState.READY_TO_WRITE
                    if approved
                    else GuidedResearchState.BRIEF_PARSED
                ),
                ready_to_write=approved,
                context_updates={"sources_accepted": True},
            )
            message = (
                f"{len(accepted)} source(s) validée(s) sont maintenant attachée(s) au dossier "
                "avec leur provenance et leur périmètre de preuve. La couverture a été recalculée."
            )
            return self._response(
                session_id,
                ConsultantIntent.ACCEPT_SOURCES,
                GuidedResearchState.READY_TO_WRITE if approved else GuidedResearchState.BRIEF_PARSED,
                message,
                NextAction.WAIT_FOR_WRITE_COMMAND if approved else NextAction.CONTINUE_BRIEF,
                brief,
                approved,
                {"selected_sources": sources, "coverage": coverage},
            )
        self.repository.update(
            db,
            session_id,
            selected_sources=sources,
            state=GuidedResearchState.RESEARCH_REFINEMENT,
            ready_to_write=False,
        )
        return self._response(
            session_id,
            ConsultantIntent.ACCEPT_SOURCES,
            GuidedResearchState.RESEARCH_REFINEMENT,
            "Les sources indiquées ont été écartées. Je peux chercher des alternatives plus proches.",
            NextAction.RUN_RESEARCH,
            session.brief,
            False,
            {"selected_sources": sources},
        )

    def submit_structured_prompt(
        self,
        db: Session,
        project: Any,
        *,
        session_id: str,
        raw_request: str,
        sections: list[dict[str, Any]],
        general_constraints: list[str] | None = None,
    ) -> ConversationResponse:
        session = self.state_manager.get_session(db, session_id)
        if int(session.project_id) != int(project.id):
            raise PermissionError("Cette session n'appartient pas au projet demandé.")
        contract_path = self._contract_path(project)
        existing = read_json(contract_path)
        contract = (
            update_edited_plan(existing, sections)
            if existing
            else create_contract(sections)
        )
        write_json(contract_path, contract)
        brief = _brief_from_contract(
            contract,
            raw_request=raw_request,
            constraints=general_constraints or [],
        )
        self.state_manager.update_brief(db, session_id, brief)
        coverage = self._coverage(project, brief)
        self.repository.update(
            db,
            session_id,
            writing_contract=contract,
            coverage=coverage,
            state=GuidedResearchState.BRIEF_PARSED,
            ready_to_write=False,
        )
        return self._response(
            session_id,
            ConsultantIntent.CHANGE_PLAN,
            GuidedResearchState.BRIEF_PARSED,
            "Le plan structuré est enregistré avec sa hiérarchie et ses consignes. Vous pouvez continuer à en discuter naturellement ou le valider.",
            NextAction.CONTINUE_BRIEF,
            brief,
            False,
            {"contract": contract, "coverage": coverage},
        )

    @staticmethod
    def _candidate_summary(candidates: list[dict[str, Any]]) -> str:
        scientific = sum(
            row.get("candidate_kind") == "scientific_article"
            for row in candidates
        )
        documentation = sum(
            row.get("candidate_kind") in {
                "official_documentation",
                "documentation",
                "software_repository",
                "research_output",
            }
            for row in candidates
        )
        return (
            f"J'ai trouvé {len(candidates)} candidat(s) : "
            f"{scientific} article(s) scientifique(s) et "
            f"{documentation} documentation(s) potentielle(s)."
        )

    @staticmethod
    def _response(
        session_id: str,
        intent: ConsultantIntent,
        state: GuidedResearchState,
        message: str,
        next_action: NextAction,
        brief: ConsultantBrief | None,
        ready: bool,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationResponse:
        return ConversationResponse(
            session_id=session_id,
            intent=intent,
            state=state,
            assistant_message=message,
            next_action=next_action,
            brief=brief,
            ready_to_write=ready,
            metadata=metadata or {},
        )
