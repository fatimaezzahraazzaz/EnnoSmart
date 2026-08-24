# -*- coding: utf-8 -*-
from __future__ import annotations

# ENNOSCHOLAR_V170_2_PLAN_EDITING_READBACK_FIX

# ENNOSCHOLAR_V170_1_CONVERSATION_ROUTING_FIX

import json
import logging
import re
import unicodedata
from contextvars import ContextVar
from typing import Any, Literal, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from modules.LLM.llm_client import LLMClient

from .domain.enums import ConsultantIntent
from .domain.models import (
    ConversationMemory,
    ConversationUnderstanding,
    GuidedResearchSessionData,
    IntentClassification,
)
logger = logging.getLogger(__name__)

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)

_PLAN_PAYLOAD_INTENTS = {
    ConsultantIntent.DESCRIBE_REQUIREMENTS,
    ConsultantIntent.PROPOSE_PLAN,
    ConsultantIntent.ADD_TOPIC,
    ConsultantIntent.REMOVE_TOPIC,
    ConsultantIntent.CHANGE_PLAN,
}
_SEARCH_PAYLOAD_INTENTS = {
    ConsultantIntent.SEARCH_MORE,
    ConsultantIntent.SEARCH_ALTERNATIVE,
    ConsultantIntent.REPLACE_SOURCE,
    ConsultantIntent.ADD_VERROU_AND_SEARCH,
}
_WRITING_INTENTS = {
    ConsultantIntent.START_WRITING,
    ConsultantIntent.REVISE_DRAFT,
}
_SUPPORTED_TURN_INTENTS = {
    ConsultantIntent.CONVERSE,
    ConsultantIntent.DESCRIBE_REQUIREMENTS,
    ConsultantIntent.PROPOSE_PLAN,
    ConsultantIntent.ADD_TOPIC,
    ConsultantIntent.REMOVE_TOPIC,
    ConsultantIntent.CHANGE_PLAN,
    ConsultantIntent.ADD_VERROU_AND_SEARCH,
    ConsultantIntent.SEARCH_MORE,
    ConsultantIntent.SEARCH_ALTERNATIVE,
    ConsultantIntent.REPLACE_SOURCE,
    ConsultantIntent.EXPLAIN_SOURCE,
    ConsultantIntent.ACCEPT_PLAN,
    ConsultantIntent.START_WRITING,
    ConsultantIntent.REVISE_DRAFT,
    ConsultantIntent.CANCEL,
    ConsultantIntent.UNKNOWN,
}


class _TurnDecision(BaseModel):
    """Décision conversationnelle, indépendante de tout payload métier."""

    model_config = ConfigDict(extra="ignore", use_enum_values=False)

    classification: IntentClassification
    plan_reference: Literal[
        "none",
        "current",
        "previous",
        "first",
        "specific",
    ] = "none"
    referenced_plan_version: str = ""
    plan_generation_mode: Literal[
        "none",
        "initial",
        "alternative",
    ] = "none"
    plan_document_scope: Literal[
        "none",
        "state_of_art",
        "full_project_document",
        "other",
        "unspecified",
    ] = "none"
    # Le texte conversationnel est facultatif : les services métier savent
    # générer une réponse fidèle à l'action réellement exécutée. Son omission par
    # le LLM ne doit jamais annuler un plan, une recherche ou une rédaction déjà
    # correctement structurés.
    assistant_message: str = Field(default="", max_length=6000)
    # La mémoire est un delta facultatif. Son absence signifie simplement que le
    # tour n'ajoute aucun fait durable ; elle ne doit jamais invalider une action
    # correctement comprise ni déclencher un appel de réparation supplémentaire.
    memory: ConversationMemory = Field(default_factory=ConversationMemory)

    @field_validator("referenced_plan_version", mode="before")
    @classmethod
    def normalize_referenced_plan_version(cls, value: Any) -> str:
        return "" if value is None else str(value)


class _SearchRequestPayload(BaseModel):
    """Contrat machine transmis au moteur de recherche, sans réinterprétation."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1, max_length=1200)
    query_kind: Literal[
        "scientific_evidence",
        "direct_scientific_evidence",
        "official_documentation",
    ] = "scientific_evidence"
    entity_name: str = Field(default="", max_length=400)
    entity_names: list[str] = Field(default_factory=list)
    entity_type: str = Field(default="other", max_length=120)
    required_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    section_titles: list[str] = Field(default_factory=list)
    target_verrous: list[str] = Field(default_factory=list)
    requested_dimensions: list[str] = Field(default_factory=list)
    target_context_dimensions: list[str] = Field(default_factory=list)
    require_direct_evidence: bool = False
    source_preferences: list[str] = Field(default_factory=list)


class _ConsultantVerrouPayload(BaseModel):
    """Verrou manquant déclaré explicitement par le consultant."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=5, max_length=1200)
    justification: str = Field(default="", max_length=4000)
    supporting_context: str = Field(default="", max_length=8000)
    source_document_ids: list[int] = Field(default_factory=list)
    force_create_distinct: bool = False


class _StandaloneProjectBriefPayload(BaseModel):
    """Contexte déclaré par le consultant lorsqu'Agent 1 n'a pas été exécuté."""

    model_config = ConfigDict(extra="ignore")

    project_name: str = Field(default="", max_length=500)
    domain: str = Field(default="", max_length=1000)
    objective: str = Field(default="", max_length=5000)
    additional_context: str = Field(default="", max_length=8000)

class _ActionPayload(BaseModel):
    """Arguments d'action produits seulement après sélection d'une capacité."""

    model_config = ConfigDict(extra="ignore")

    plan: list[dict[str, Any]] = Field(default_factory=list)
    topics: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    verrous: list[_ConsultantVerrouPayload] = Field(default_factory=list)
    project_brief: _StandaloneProjectBriefPayload | None = None
    review_scope: Literal["auto", "per_verrou", "global"] = "auto"
    search_requests: list[_SearchRequestPayload] = Field(default_factory=list)

    @field_validator("review_scope", mode="before")
    @classmethod
    def normalize_review_scope(cls, value: Any) -> str:
        """Convertit les variantes neutres du LLM sans reclasser l'intention."""
        normalized = _clean(value, 80).casefold().replace("-", "_").replace(" ", "_")
        if normalized in {"per_verrou", "global"}:
            return normalized
        # « unchanged/current/same » décrit une absence de changement. Dans le
        # contrat d'action, la valeur neutre équivalente est ``auto``.
        return "auto"


class _TurnResolution(BaseModel):
    """Compréhension et matérialisation produites par un seul appel LLM.

    La séparation précédente ``decision -> action`` demandait au second appel de
    reconstruire une partie du sens du premier. Une demande composée pouvait donc
    être réduite à une seule action (par exemple approuver/rédiger) et perdre le
    plan fourni dans le même tour. Le modèle est désormais l'unique interprète :
    il renvoie simultanément la décision et ses arguments métier.
    """

    model_config = ConfigDict(extra="ignore")

    decision: _TurnDecision
    action: _ActionPayload = Field(default_factory=_ActionPayload)

    @model_validator(mode="before")
    @classmethod
    def normalize_llm_layout(cls, value: Any) -> Any:
        """Tolère un placement imparfait des champs par le fournisseur LLM.

        Le modèle comprend parfois correctement le tour mais place les champs de
        référence au plan dans ``decision.classification`` au lieu de
        ``decision``. Cette adaptation ne déduit aucune intention : elle déplace
        uniquement les champs connus vers leur emplacement contractuel, puis
        ignore les métadonnées étrangères à la classification.
        """
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        raw_decision = payload.get("decision")
        if not isinstance(raw_decision, Mapping):
            return payload
        decision = dict(raw_decision)
        raw_classification = decision.get("classification")
        if isinstance(raw_classification, Mapping):
            classification = dict(raw_classification)
            for field_name in (
                "plan_reference",
                "referenced_plan_version",
                "plan_generation_mode",
                "plan_document_scope",
            ):
                misplaced = classification.pop(field_name, None)
                if field_name not in decision and misplaced is not None:
                    decision[field_name] = misplaced
            allowed_classification_fields = set(IntentClassification.model_fields)
            decision["classification"] = {
                key: child
                for key, child in classification.items()
                if key in allowed_classification_fields
            }
        payload["decision"] = decision
        return payload


def _scope_action_payload(
    intent: ConsultantIntent,
    payload: _ActionPayload,
    *,
    allow_standalone_context: bool = False,
) -> _ActionPayload:
    """Conserve uniquement les arguments autorisés par l'intention retenue.

    Le schéma d'action est commun à toutes les capacités. Certains fournisseurs
    remplissent malgré tout des champs hors intention, surtout lorsqu'une phrase
    combine un plan et une rédaction future. L'intention structurée reste la
    source d'autorité : ces champs sont ignorés au lieu de faire échouer le tour,
    sans jamais pouvoir devenir exécutables.
    """

    normalized_search_requests = [
        (
            request.model_copy(update={"require_direct_evidence": True})
            if request.query_kind == "direct_scientific_evidence"
            and not request.require_direct_evidence
            else request
        )
        for request in (payload.search_requests or [])
    ]

    if intent in {
        ConsultantIntent.PROPOSE_PLAN,
        ConsultantIntent.ADD_TOPIC,
        ConsultantIntent.REMOVE_TOPIC,
        ConsultantIntent.CHANGE_PLAN,
    }:
        return _ActionPayload(plan=list(payload.plan or []))

    if intent == ConsultantIntent.DESCRIBE_REQUIREMENTS:
        return _ActionPayload(
            topics=list(payload.topics or []),
            constraints=list(payload.constraints or []),
            verrous=(
                list(payload.verrous or [])
                if allow_standalone_context
                else []
            ),
            project_brief=(
                payload.project_brief
                if allow_standalone_context
                else None
            ),
            review_scope=(
                payload.review_scope
                if allow_standalone_context
                else "auto"
            ),
        )

    if intent == ConsultantIntent.ADD_VERROU_AND_SEARCH:
        return _ActionPayload(
            verrous=list(payload.verrous or []),
            project_brief=payload.project_brief,
            review_scope=payload.review_scope,
            search_requests=normalized_search_requests,
        )

    if intent in {
        ConsultantIntent.SEARCH_MORE,
        ConsultantIntent.SEARCH_ALTERNATIVE,
        ConsultantIntent.REPLACE_SOURCE,
    }:
        return _ActionPayload(
            search_requests=normalized_search_requests,
        )

    return _ActionPayload()


def _clean(value: Any, limit: int = 16000) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[:limit]


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
                return {}
    return {}


def _compact_project_context(value: Mapping[str, Any]) -> dict[str, Any]:
    project = value.get("project")
    project_row = dict(project) if isinstance(project, Mapping) else {}

    cards: list[dict[str, Any]] = []
    for raw_card in value.get("validated_article_cards") or []:
        if not isinstance(raw_card, Mapping):
            continue
        cards.append({
            "citation_id": _clean(raw_card.get("citation_id"), 80),
            "article_id": raw_card.get("article_id"),
            "title": _clean(raw_card.get("title"), 360),
            "role": _clean(raw_card.get("role"), 100),
            "guided_research_source": bool(
                raw_card.get("guided_research_source")
            ),
            "verrou_ids": [
                _clean(value, 120)
                for value in (raw_card.get("verrou_ids") or [])[:12]
                if _clean(value, 120)
            ],
            "abstract": _clean(raw_card.get("abstract"), 360),
            "keywords": [
                _clean(keyword, 80)
                for keyword in (raw_card.get("keywords") or [])[:8]
                if _clean(keyword, 80)
            ],
        })
        if len(cards) >= 24:
            break

    previous_memories = [
        dict(memory)
        for memory in (value.get("previous_project_memories") or [])[-2:]
        if isinstance(memory, Mapping)
    ]
    raw_plan_history = [
        history
        for history in (value.get("plan_history") or [])
        if isinstance(history, Mapping)
    ]
    selected_history = (
        raw_plan_history
        if len(raw_plan_history) <= 4
        else [raw_plan_history[0], *raw_plan_history[-3:]]
    )
    plan_history = [
        {
            "version": history.get("version"),
            "created_at": history.get("created_at"),
            "plan": _compact_plan_snapshot(history.get("plan")),
        }
        for history in selected_history
        if _compact_plan_snapshot(history.get("plan"))
    ]

    current_verrous: list[dict[str, Any]] = []
    for raw_verrou in value.get("current_verrous") or []:
        if not isinstance(raw_verrou, Mapping):
            continue
        title = _clean(raw_verrou.get("title"), 700)
        if not title:
            continue
        current_verrous.append({
            "id": raw_verrou.get("id"),
            "title": title,
            "consultant_status": _clean(
                raw_verrou.get("consultant_status"), 80
            ),
            "score": raw_verrou.get("score"),
            "tag_cir": raw_verrou.get("tag_cir"),
            "origin": _clean(raw_verrou.get("origin"), 120),
            "supplementary_verrou": bool(
                raw_verrou.get("supplementary_verrou")
            ),
        })
        if len(current_verrous) >= 30:
            break

    return {
        "project": project_row,
        "scientific_context": _clean(value.get("scientific_context"), 4200),
        "validated_article_cards": cards,
        "current_verrous": current_verrous,
        "active_verrou_ids": [
            _clean(value, 120)
            for value in (value.get("active_verrou_ids") or [])
            if _clean(value, 120)
        ],
        "review_scope": _clean(value.get("review_scope"), 40),
        "operating_mode": _clean(value.get("operating_mode"), 80),
        "standalone_project_brief": (
            dict(value.get("standalone_project_brief") or {})
            if isinstance(value.get("standalone_project_brief"), Mapping)
            else {}
        ),
        "previous_project_memories": previous_memories,
        "plan_history": plan_history,
        # BEGIN ENNOSCHOLAR_HANDOFF_CONTEXT_V1
        "handoff": (
            dict(value.get("handoff") or {})
            if isinstance(value.get("handoff"), Mapping)
            else {}
        ),
        "selected_article_ids": [
            int(article_id)
            for article_id in (value.get("selected_article_ids") or [])
            if str(article_id).strip().isdigit()
        ][:100],
        # END ENNOSCHOLAR_HANDOFF_CONTEXT_V1
        "writing_source_policy": (
            dict(value.get("writing_source_policy") or {})
            if isinstance(value.get("writing_source_policy"), Mapping)
            else {}
        ),
    }


def _compact_plan(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw_section in value[:40]:
        if not isinstance(raw_section, Mapping):
            continue
        output.append({
            "section_id": _clean(raw_section.get("section_id"), 120),
            "title": _clean(raw_section.get("title"), 320),
            "objective": _clean(raw_section.get("objective"), 420),
            "parent_id": _clean(raw_section.get("parent_id"), 120) or None,
            "level": raw_section.get("level"),
        })
    return output


def _compact_plan_snapshot(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for raw_section in value[:28]:
        if not isinstance(raw_section, Mapping):
            continue
        title = _clean(raw_section.get("title"), 240)
        if not title:
            continue
        output.append({
            "section_id": _clean(raw_section.get("section_id"), 120),
            "title": title,
            "objective": _clean(raw_section.get("objective"), 180),
            "parent_id": _clean(raw_section.get("parent_id"), 120) or None,
            "level": raw_section.get("level"),
        })
    return output



# -----------------------------------------------------------------------------
# V2 — Grounding conversationnel des références au plan
# -----------------------------------------------------------------------------

_PLAN_ORDINALS: dict[str, int] = {
    "premier": 1, "premiere": 1, "1er": 1, "1ere": 1,
    "deuxieme": 2, "second": 2, "seconde": 2, "2eme": 2,
    "troisieme": 3, "3eme": 3,
    "quatrieme": 4, "4eme": 4,
    "cinquieme": 5, "5eme": 5,
    "sixieme": 6, "6eme": 6,
    "septieme": 7, "7eme": 7,
    "huitieme": 8, "8eme": 8,
    "neuvieme": 9, "9eme": 9,
    "dixieme": 10, "10eme": 10,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
}


def _indexed_plan_rows(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ajoute un numéro d'affichage stable (1, 2, 3, 3.1...) au plan plat."""
    rows: list[dict[str, Any]] = []
    counters: list[int] = []
    for raw in _compact_plan_snapshot(plan):
        row = dict(raw)
        level = max(1, int(row.get("level") or 1))
        while len(counters) < level:
            counters.append(0)
        counters = counters[:level]
        counters[-1] += 1
        label = ".".join(str(value) for value in counters)
        row["display_label"] = label
        rows.append(row)
    return rows


def _indexed_plan_text(plan: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in _indexed_plan_rows(plan):
        indent = "  " * (max(1, int(row.get("level") or 1)) - 1)
        lines.append(
            f"{indent}{row['display_label']} | section_id={_clean(row.get('section_id'), 120)} "
            f"| title={_clean(row.get('title'), 320)}"
        )
    return "\n".join(lines)


def _message_plan_labels(message: str) -> list[str]:
    """Extrait uniquement les références structurelles explicitement formulées."""
    normalized = unicodedata.normalize("NFKD", _clean(message, 12000).casefold())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    labels: list[str] = []

    # section 3, partie 3.2, point 4, axe 2, chapitre 5...
    for match in re.finditer(
        r"\b(?:partie|section|sous[- ]?section|point|axe|chapitre|titre)\s+"
        r"(\d{1,2}(?:\.\d{1,2})*)\b",
        normalized,
    ):
        labels.append(match.group(1))

    # la troisième partie / la deuxième section...
    for word, number in _PLAN_ORDINALS.items():
        if re.search(
            rf"\b(?:partie|section|point|axe|chapitre)\s+{re.escape(word)}\b|"
            rf"\b{re.escape(word)}\s+(?:partie|section|point|axe|chapitre)\b",
            normalized,
        ):
            labels.append(str(number))

    # Déduplication en conservant l'ordre du message.
    return list(dict.fromkeys(labels))


def _resolve_explicit_plan_targets(
    message: str,
    plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_label = {
        str(row.get("display_label") or ""): row
        for row in _indexed_plan_rows(plan)
    }
    return [by_label[label] for label in _message_plan_labels(message) if label in by_label]


def _looks_like_structural_plan_edit(message: str) -> bool:
    normalized = unicodedata.normalize("NFKD", _clean(message, 12000).casefold())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    markers = (
        "modif", "change", "transform", "detail", "develop", "decompos", "decoup",
        "reorganis", "structure", "sous section", "sous-section", "ajout", "ajoute",
        "retire", "supprim", "enleve", "remplace", "deplace", "fusion",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_previous_edit_correction(message: str) -> bool:
    normalized = unicodedata.normalize("NFKD", _clean(message, 12000).casefold())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    correction_markers = (
        "non ", "pas la", "pas le", "au lieu", "je voulais", "j ai demande",
        "je t ai demande", "tu as change", "tu as modifie", "corrige",
    )
    return any(marker in normalized for marker in correction_markers)


def _ground_explicit_plan_edit_decision(
    decision: _TurnDecision,
    *,
    consultant_message: str,
    current_plan: list[dict[str, Any]],
    history: list[dict[str, Any]],
    project_context: Mapping[str, Any],
) -> _TurnDecision:
    """Sécurise les références explicites sans remplacer la compréhension LLM.

    Le LLM reste responsable du sens général. Ce garde-fou n'intervient que lorsque
    le consultant nomme sans ambiguïté une partie/section existante et formule une
    modification structurelle. Il évite qu'une requête comme « transforme la partie 3
    en sous-sections, pas la 5 » soit appliquée à une autre branche du plan.
    """
    targets = _resolve_explicit_plan_targets(consultant_message, current_plan)
    if not targets or not _looks_like_structural_plan_edit(consultant_message):
        return decision

    classification = decision.classification
    resolved_target_ids = [
        _clean(target.get("section_id"), 120)
        for target in targets
        if _clean(target.get("section_id"), 120)
    ]
    if resolved_target_ids:
        classification.target_section_ids = resolved_target_ids
        classification.plan_edit_scope = "local_section"
        if classification.plan_edit_operation == "none":
            classification.plan_edit_operation = {
                ConsultantIntent.ADD_TOPIC: "add",
                ConsultantIntent.REMOVE_TOPIC: "remove",
            }.get(classification.intent, "modify")
        if classification.content_target not in {
            "existing_section",
            "existing_paragraph",
        }:
            classification.content_target = "existing_section"
    if classification.intent in {ConsultantIntent.UNKNOWN, ConsultantIntent.CONVERSE}:
        classification.intent = ConsultantIntent.CHANGE_PLAN
        classification.requested_actions = [ConsultantIntent.CHANGE_PLAN]
        classification.forbidden_actions = [
            action for action in (classification.forbidden_actions or [])
            if action != ConsultantIntent.CHANGE_PLAN
        ]
        classification.needs_clarification = False
        classification.corrected_message = _clean(consultant_message)
        classification.extracted_text = _clean(consultant_message)
        classification.classifier = (
            f"{_clean(classification.classifier, 120) or 'llm'}+explicit_plan_grounding"
        )

    # Une correction explicite du DERNIER mauvais changement doit repartir du plan
    # précédent afin de ne pas conserver les sous-sections créées au mauvais endroit.
    if _looks_like_previous_edit_correction(consultant_message):
        candidates = _plan_reference_candidates(
            history=history,
            project_context=project_context,
        )
        has_previous = len(candidates.get("recent") or []) > 1 or len(candidates.get("stored") or []) > 1
        if has_previous:
            decision.plan_reference = "previous"
            classification.replace_current_plan = True
        else:
            decision.plan_reference = "current"
            classification.replace_current_plan = False
    elif decision.plan_reference == "none":
        decision.plan_reference = "current"
        classification.replace_current_plan = False

    decision.plan_generation_mode = "none"
    if decision.plan_document_scope == "none":
        decision.plan_document_scope = "state_of_art"
    return decision


def _plan_target_consistency_error(
    *,
    consultant_message: str,
    candidate_plan: list[dict[str, Any]],
    grounding_plan: list[dict[str, Any]],
    replace_current_plan: bool,
) -> str:
    """Refuse une réponse LLM rattachée à la mauvaise section explicite."""
    targets = _resolve_explicit_plan_targets(consultant_message, grounding_plan)
    if len(targets) != 1:
        return ""

    target = targets[0]
    target_id = _clean(target.get("section_id"), 120)
    target_title = _clean(target.get("title"), 320).casefold()
    target_level = max(1, int(target.get("level") or 1))
    normalized_message = unicodedata.normalize(
        "NFKD", _clean(consultant_message, 12000).casefold()
    )
    normalized_message = "".join(
        ch for ch in normalized_message if not unicodedata.combining(ch)
    )
    asks_children = bool(
        re.search(r"\bsous[- ]?sections?\b", normalized_message)
        or any(marker in normalized_message for marker in ("decompos", "decoup", "detaille en", "developpe en"))
    )
    if not asks_children:
        return ""

    rows = _compact_plan_snapshot(candidate_plan)
    if not rows:
        return "La modification demandée doit produire une structure de plan non vide."

    # En remplacement complet, la section cible doit exister et avoir au moins un enfant.
    # En delta local, un enfant rattaché au bon parent suffit.
    children = [
        row for row in rows
        if _clean(row.get("parent_id"), 120) == target_id
        and max(1, int(row.get("level") or 1)) == target_level + 1
    ]
    if not children:
        return (
            f"La demande vise explicitement la section {target.get('display_label')} "
            f"« {target.get('title')} ». Les nouvelles sous-sections doivent avoir "
            f"parent_id={target_id!r} et level={target_level + 1}. Ne modifie pas une autre partie."
        )

    # Si le modèle renvoie le parent lui-même, il doit bien s'agir de la cible.
    parent_like = [
        row for row in rows
        if max(1, int(row.get("level") or 1)) == target_level
        and not _clean(row.get("parent_id"), 120)
    ]
    if parent_like and not replace_current_plan:
        wrong = [
            row for row in parent_like
            if _clean(row.get("section_id"), 120) not in {"", target_id}
            and _clean(row.get("title"), 320).casefold() != target_title
        ]
        if wrong:
            return "La modification locale ne doit pas créer ou réécrire une autre section principale."
    return ""


def _plan_snapshot_from_turn(turn: Any) -> dict[str, Any] | None:
    metadata = turn.metadata if isinstance(turn.metadata, Mapping) else {}
    contract = metadata.get("contract")
    if not isinstance(contract, Mapping):
        return None
    for key in ("approved_plan", "consultant_edited_plan", "proposed_plan"):
        plan = _compact_plan_snapshot(contract.get(key))
        if plan:
            return {
                "plan_version": contract.get("plan_version"),
                "plan": plan,
            }
    return None


def _recent_history(session: GuidedResearchSessionData) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    snapshot_indexes: list[int] = []
    for turn in session.messages[-16:]:
        row: dict[str, Any] = {
            "role": str(turn.role),
            "content": _clean(turn.content, 900),
            "intent": str(turn.intent) if turn.intent else None,
        }
        snapshot = _plan_snapshot_from_turn(turn)
        if snapshot:
            row["plan_snapshot"] = snapshot
            snapshot_indexes.append(len(rows))
        rows.append(row)

    # Suffisant pour résoudre « le premier plan » et « le plan précédent »
    # sans recopier chaque version intermédiaire dans le prompt.
    # Conserver le premier plan et les trois versions les plus récentes.
    # Cela permet de comprendre naturellement « le plan précédent »,
    # « non, je voulais la partie 3, pas la 5 », etc.
    keep_indexes = set(snapshot_indexes[:1] + snapshot_indexes[-3:])
    for index in snapshot_indexes:
        if index not in keep_indexes:
            rows[index].pop("plan_snapshot", None)
    return rows


def _plan_reference_candidates(
    *,
    history: list[dict[str, Any]],
    project_context: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    recent: list[dict[str, Any]] = []
    stored: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append_candidate(
        target: list[dict[str, Any]],
        version: Any,
        plan: Any,
    ) -> None:
        compact = _compact_plan_snapshot(plan)
        if not compact:
            return
        fingerprint = json.dumps(compact, ensure_ascii=False, sort_keys=True)
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        target.append({
            "version": version,
            "plan": compact,
        })

    for row in history:
        if not isinstance(row, Mapping):
            continue
        snapshot = row.get("plan_snapshot")
        if isinstance(snapshot, Mapping):
            append_candidate(
                recent,
                snapshot.get("plan_version"),
                snapshot.get("plan"),
            )
    for row in project_context.get("plan_history") or []:
        if isinstance(row, Mapping):
            append_candidate(stored, row.get("version"), row.get("plan"))
    return {"recent": recent, "stored": stored}


def _resolve_plan_reference(
    *,
    decision: _TurnDecision,
    history: list[dict[str, Any]],
    project_context: Mapping[str, Any],
    current_plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reference = decision.plan_reference
    if reference == "current":
        return _compact_plan_snapshot(current_plan)
    if reference == "none":
        return []

    candidate_groups = _plan_reference_candidates(
        history=history,
        project_context=project_context,
    )
    recent = candidate_groups["recent"]
    stored = candidate_groups["stored"]
    if not recent and not stored:
        return []
    if reference == "first":
        # « Le premier plan » est d'abord résolu dans l'épisode conversationnel
        # visible. L'historique durable ne sert de repli que si un seul snapshot
        # récent (le plan courant) est disponible.
        candidates = recent if len(recent) > 1 else stored or recent
        return list(candidates[0]["plan"])
    if reference == "previous":
        candidates = recent if len(recent) > 1 else stored or recent
        return list(candidates[-2 if len(candidates) > 1 else -1]["plan"])

    requested_version = _clean(decision.referenced_plan_version, 120)
    for candidate in [*recent, *stored]:
        if _clean(candidate.get("version"), 120) == requested_version:
            return list(candidate["plan"])
    return []


def _existing_memory(session: GuidedResearchSessionData) -> ConversationMemory:
    raw_memory = (
        session.context.get("conversation_memory")
        or session.context.get("project_memory")
        or {}
    )
    if not isinstance(raw_memory, Mapping):
        raw_memory = {}
    try:
        return ConversationMemory.model_validate(raw_memory)
    except Exception:
        return ConversationMemory()


def _merge_memory_delta(
    existing: ConversationMemory,
    delta: ConversationMemory,
) -> ConversationMemory:
    """Ajoute les faits du tour sans pouvoir effacer la mémoire existante."""
    existing_data = existing.model_dump(mode="python")
    delta_data = delta.model_dump(mode="python")
    merged: dict[str, Any] = {}
    for field_name in (
        "project_facts",
        "consultant_preferences",
        "validated_decisions",
        "rejected_options",
        "open_questions",
    ):
        merged[field_name] = [
            *list(existing_data.get(field_name) or []),
            *list(delta_data.get(field_name) or []),
        ]
    for field_name in ("current_focus", "last_consultant_goal"):
        merged[field_name] = (
            _clean(delta_data.get(field_name), 2000)
            or _clean(existing_data.get(field_name), 2000)
        )
    return ConversationMemory.model_validate(merged)


def _normalize_turn_decision(decision: _TurnDecision) -> _TurnDecision:
    """Réconcilie les champs redondants sans réinterpréter le message."""
    classification = decision.classification
    intent = classification.intent

    classification.requested_actions = list(
        dict.fromkeys(classification.requested_actions or [])
    )
    classification.forbidden_actions = list(
        dict.fromkeys(classification.forbidden_actions or [])
    )

    if intent in {ConsultantIntent.CONVERSE, ConsultantIntent.UNKNOWN}:
        classification.requested_actions = []
    elif intent not in classification.requested_actions:
        classification.requested_actions.insert(0, intent)

    classification.forbidden_actions = [
        action for action in classification.forbidden_actions if action != intent
    ]

    if intent == ConsultantIntent.CONVERSE:
        classification.explicit_write_command = False
        classification.explicit_plan_approval = False
        classification.explicit_research_command = False
        classification.replace_current_plan = False
        classification.use_current_sources_only = False
        classification.writing_source_scope = "unspecified"
        classification.writing_source_identifiers = []
        classification.requested_source_count = None
        classification.needs_clarification = False
        decision.plan_reference = "none"
        decision.plan_generation_mode = "none"
        decision.plan_document_scope = "none"
    elif intent == ConsultantIntent.UNKNOWN:
        classification.explicit_write_command = False
        classification.explicit_plan_approval = False
        classification.explicit_research_command = False
        classification.replace_current_plan = False
        classification.use_current_sources_only = False
        classification.writing_source_scope = "unspecified"
        classification.writing_source_identifiers = []
        classification.requested_source_count = None
        classification.needs_clarification = True
        decision.plan_reference = "none"
        decision.plan_generation_mode = "none"
        decision.plan_document_scope = "none"
    elif intent == ConsultantIntent.ADD_TOPIC:
        classification.replace_current_plan = False
        decision.plan_reference = "current"
        decision.plan_generation_mode = "none"
        if decision.plan_document_scope == "none":
            decision.plan_document_scope = "state_of_art"
    elif intent in {ConsultantIntent.CHANGE_PLAN, ConsultantIntent.REMOVE_TOPIC}:
        if decision.plan_reference == "none":
            decision.plan_reference = "current"
        decision.plan_generation_mode = "none"
        if decision.plan_document_scope == "none":
            decision.plan_document_scope = "state_of_art"
        if decision.plan_reference in {"previous", "first", "specific"}:
            classification.replace_current_plan = True
    elif intent == ConsultantIntent.PROPOSE_PLAN:
        decision.plan_reference = "none"
        classification.replace_current_plan = True
        if decision.plan_generation_mode == "none":
            decision.plan_generation_mode = "alternative"
        if decision.plan_document_scope == "none":
            decision.plan_document_scope = "state_of_art"
    else:
        decision.plan_reference = "none"
        decision.referenced_plan_version = ""
        decision.plan_generation_mode = "none"
        decision.plan_document_scope = "none"

    plan_intents = {
        ConsultantIntent.PROPOSE_PLAN,
        ConsultantIntent.ADD_TOPIC,
        ConsultantIntent.REMOVE_TOPIC,
        ConsultantIntent.CHANGE_PLAN,
    }
    if intent not in plan_intents:
        classification.plan_edit_scope = "none"
        classification.plan_edit_operation = "none"
        classification.target_section_ids = []
    elif intent == ConsultantIntent.PROPOSE_PLAN:
        classification.plan_edit_scope = "full_plan"
        classification.plan_edit_operation = "none"
        classification.target_section_ids = []
    elif classification.plan_edit_scope == "local_section":
        classification.target_section_ids = list(
            dict.fromkeys(
                _clean(value, 200)
                for value in (classification.target_section_ids or [])
                if _clean(value, 200)
            )
        )
    elif classification.replace_current_plan:
        classification.plan_edit_scope = "full_plan"
        classification.target_section_ids = []

    if intent in _WRITING_INTENTS:
        classification.explicit_write_command = True
        if classification.writing_source_scope != "unspecified":
            classification.use_current_sources_only = True
    if intent == ConsultantIntent.ACCEPT_PLAN:
        classification.explicit_plan_approval = True
    if intent in _SEARCH_PAYLOAD_INTENTS:
        classification.explicit_research_command = True

    if classification.verrou_scope == "global":
        classification.target_verrou_ids = []
    elif classification.verrou_scope == "per_verrou":
        classification.target_verrou_ids = list(
            dict.fromkeys(
                _clean(value, 300)
                for value in (classification.target_verrou_ids or [])
                if _clean(value, 300)
            )
        )
    else:
        classification.verrou_scope = "unchanged"
        classification.target_verrou_ids = []

    return decision


def _honor_llm_action_order(decision: _TurnDecision) -> _TurnDecision:
    """Réconcilie uniquement les deux représentations produites par le même LLM.

    ``requested_actions`` est explicitement ordonné dans le contrat. Lorsqu'un
    modèle décrit correctement une demande composée mais place une autre action
    dans ``intent``, l'exécution doit commencer par la première action demandée,
    sans qu'un classifieur secondaire réinterprète le message.

    Un paragraphe dans une section existante relève du contenu rédactionnel, pas
    de la structure du plan. Si une recherche est demandée d'abord, la demande
    complète est mémorisée comme directive rédactionnelle par le moteur de
    recherche ; aucune fausse action ADD_TOPIC n'est laissée en attente.
    """

    classification = decision.classification
    actions = list(dict.fromkeys(classification.requested_actions or []))
    executable_actions = [
        action for action in actions if action in _SUPPORTED_TURN_INTENTS
    ]
    if executable_actions:
        classification.intent = executable_actions[0]
        classification.requested_actions = executable_actions

    if (
        classification.content_target == "existing_paragraph"
        and classification.intent in _SEARCH_PAYLOAD_INTENTS
    ):
        classification.requested_actions = [
            action
            for action in classification.requested_actions
            if action not in {
                ConsultantIntent.PROPOSE_PLAN,
                ConsultantIntent.ADD_TOPIC,
                ConsultantIntent.REMOVE_TOPIC,
                ConsultantIntent.CHANGE_PLAN,
            }
        ]
        if classification.intent not in classification.requested_actions:
            classification.requested_actions.insert(0, classification.intent)
        classification.plan_edit_scope = "none"
        classification.plan_edit_operation = "none"
        classification.replace_current_plan = False

    return decision


def _ground_writing_source_policy(
    consultant_message: str,
    classification: IntentClassification,
) -> IntentClassification:
    """Ancre la portée du corpus dans le seul message consultant courant.

    La mémoire contient volontairement les décisions historiques, mais elle ne
    doit jamais transformer d'anciennes références A/C en sélection exacte pour
    une nouvelle rédaction. Un nombre n'est contraignant que s'il est écrit à
    côté de « source », « article » ou « publication » dans le tour courant.
    """

    normalized = unicodedata.normalize(
        "NFKD", _clean(consultant_message, 12000).casefold()
    )
    normalized = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()

    literal_identifiers: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"(?<![a-z0-9-])(?:[ac]\s*\d{1,4}|(?:src|web)-[a-z0-9]+)(?![a-z0-9-])",
        normalized,
        flags=re.I,
    ):
        value = re.sub(r"\s+", "", match.group(0)).upper()
        if value not in seen:
            seen.add(value)
            literal_identifiers.append(value)

    count_match = re.search(
        r"(?<!\d)(\d{1,3})\s+(?:articles?|sources?|publications?)\b",
        normalized,
    )
    explicit_count = int(count_match.group(1)) if count_match else None

    source_language = bool(
        re.search(r"\b(?:articles?|sources?|publications?|corpus)\b", normalized)
    )
    baseline_requested = source_language and any(
        marker in normalized
        for marker in (
            "articles initiaux",
            "sources initiales",
            "corpus initial",
            "corpus de base",
            "sans les articles de recherche",
            "sans les sources de recherche",
            "hors recherche complementaire",
        )
    )
    additions_requested = source_language and any(
        marker in normalized
        for marker in (
            "articles ajoutes par la recherche",
            "sources ajoutees par la recherche",
            "recherche complementaire uniquement",
            "nouveaux articles uniquement",
            "nouvelles sources uniquement",
        )
    )
    validated_corpus_requested = source_language and any(
        marker in normalized
        for marker in (
            "articles selectionnes",
            "sources selectionnees",
            "articles valides",
            "sources validees",
            "articles retenus",
            "sources retenues",
            "corpus valide",
            "corpus actuel",
            "sources actuelles",
            "articles actuels",
            # BEGIN ENNOSCHOLAR_EXISTING_SOURCES_SYNONYMS_V2
            "articles existants",
            "sources existantes",
            "publications existantes",
            "articles deja existants",
            "sources deja existantes",
            "articles deja trouves",
            "sources deja trouvees",
            "publications deja trouvees",
            "articles deja selectionnes",
            "sources deja selectionnees",
            "articles qu on a",
            "sources qu on a",
            "articles qu on a deja",
            "sources qu on a deja",
            "articles gardes",
            "sources gardees",
            "articles conserves",
            "sources conservees",
            # END ENNOSCHOLAR_EXISTING_SOURCES_SYNONYMS_V2
        )
    )

    if literal_identifiers:
        classification.writing_source_scope = "explicit_selection"
        classification.writing_source_identifiers = literal_identifiers
        classification.requested_source_count = explicit_count
    elif baseline_requested:
        classification.writing_source_scope = "baseline_verrou_corpus"
        classification.writing_source_identifiers = []
        classification.requested_source_count = explicit_count
    elif additions_requested:
        classification.writing_source_scope = "guided_research_additions"
        classification.writing_source_identifiers = []
        classification.requested_source_count = explicit_count
    elif validated_corpus_requested:
        classification.writing_source_scope = "all_validated"
        classification.writing_source_identifiers = []
        classification.requested_source_count = explicit_count
    elif (
        classification.writing_source_scope != "unspecified"
        and not classification.explicit_write_command
    ):
        # Hors demande de rédaction, une portée de sources ne doit jamais être
        # héritée du contexte. Pendant une rédaction explicite, la décision
        # structurée du LLM reste en revanche l'autorité sémantique : elle doit
        # comprendre le langage naturel au lieu de dépendre d'une liste fermée
        # de formulations reconnues localement.
        classification.writing_source_scope = "unspecified"
        classification.writing_source_identifiers = []
        classification.requested_source_count = None

    if classification.writing_source_scope != "unspecified":
        classification.use_current_sources_only = True
    return classification


def _decision_consistency_error(decision: _TurnDecision) -> str:
    """Ne bloque que les contradictions réellement dangereuses."""
    decision = _normalize_turn_decision(decision)
    if decision.classification.intent not in _SUPPORTED_TURN_INTENTS:
        return "Cette intention n'est pas exécutable dans ce canal conversationnel."
    if (
        decision.plan_reference == "specific"
        and not _clean(decision.referenced_plan_version, 120)
    ):
        return "La version précise du plan doit être indiquée."
    classification = decision.classification
    requested_actions = set(classification.requested_actions or [])
    if (
        classification.explicit_write_command
        and classification.intent in _PLAN_PAYLOAD_INTENTS
        and ConsultantIntent.START_WRITING not in requested_actions
    ):
        return (
            "Une modification de plan accompagnée d'un ordre de rédaction doit "
            "conserver START_WRITING dans requested_actions ; elle ne doit pas "
            "transformer le contenu scientifique du plan en recherche."
        )
    if (
        classification.intent
        in {
            ConsultantIntent.ADD_TOPIC,
            ConsultantIntent.REMOVE_TOPIC,
            ConsultantIntent.CHANGE_PLAN,
        }
        and classification.content_target
        in {"existing_section", "existing_paragraph"}
        and classification.plan_edit_scope != "local_section"
    ):
        return (
            "Une action visant une section ou un paragraphe existant doit être "
            "déclarée comme plan_edit_scope=local_section et résoudre ses "
            "target_section_ids depuis le plan indexé."
        )
    if classification.plan_edit_scope == "local_section":
        if not classification.target_section_ids:
            return (
                "Une modification locale doit identifier la ou les sections "
                "cibles par leur section_id exact du plan indexé."
            )
        if classification.plan_edit_operation == "none":
            return (
                "Une modification locale doit préciser si elle ajoute, modifie "
                "ou supprime dans la section ciblée."
            )
    if (
        classification.intent == ConsultantIntent.ADD_VERROU_AND_SEARCH
        and not classification.explicit_new_verrou_declaration
    ):
        return (
            "ADD_VERROU_AND_SEARCH exige que le tour actuel déclare "
            "explicitement un verrou nouveau ou manquant. La sélection d'un "
            "ou de plusieurs verrous déjà présents dans current_verrous ne "
            "crée aucun verrou."
        )
    if classification.intent == ConsultantIntent.ADD_VERROU_AND_SEARCH and (
        classification.scientific_scope_relation != "declares_new_verrou"
        or classification.content_target != "new_verrou"
    ):
        return (
            "ADD_VERROU_AND_SEARCH exige trois décisions sémantiques "
            "cohérentes : explicit_new_verrou_declaration=true, "
            "scientific_scope_relation=declares_new_verrou et "
            "content_target=new_verrou. Si le sujet sert une section, un "
            "paragraphe ou un verrou existant, choisis SEARCH_MORE."
        )
    if (
        classification.scientific_scope_relation
        == "supports_existing_verrou"
        and classification.intent == ConsultantIntent.ADD_VERROU_AND_SEARCH
    ):
        return (
            "Un complément destiné à soutenir un verrou existant ne peut pas "
            "être matérialisé comme un nouveau verrou."
        )
    if (
        classification.writing_source_scope == "explicit_selection"
        and not classification.writing_source_identifiers
    ):
        return "La sélection explicite doit nommer au moins une source."
    if (
        classification.requested_source_count is not None
        and classification.writing_source_scope == "unspecified"
    ):
        return "Le nombre demandé doit être associé à une portée de sources."
    return ""

def _payload_consistency_error(
    intent: ConsultantIntent,
    payload: _ActionPayload,
    *,
    allow_standalone_context: bool = False,
    reference_plan: list[dict[str, Any]] | None = None,
    require_reference_coverage: bool = False,
    require_distinct_from_current: bool = False,
    current_plan: list[dict[str, Any]] | None = None,
) -> str:
    is_add_verrou = intent == ConsultantIntent.ADD_VERROU_AND_SEARCH

    if intent in _PLAN_PAYLOAD_INTENTS - {
        ConsultantIntent.DESCRIBE_REQUIREMENTS
    } and (
        payload.topics
        or payload.constraints
        or payload.verrous
        or payload.project_brief
        or payload.search_requests
    ):
        return "Une action de plan ne peut contenir aucun autre payload métier."

    standalone_context_declaration = bool(
        intent == ConsultantIntent.DESCRIBE_REQUIREMENTS
        and allow_standalone_context
        and (payload.verrous or payload.project_brief)
    )
    if (
        intent == ConsultantIntent.DESCRIBE_REQUIREMENTS
        and not payload.topics
        and not payload.constraints
        and not standalone_context_declaration
    ):
        return (
            "DESCRIBE_REQUIREMENTS exige au moins un sujet ou une contrainte "
            "explicitement formulée, ou un contexte projet autonome explicite."
        )
    if (
        intent == ConsultantIntent.DESCRIBE_REQUIREMENTS
        and (
            payload.plan
            or payload.search_requests
            or (
                not allow_standalone_context
                and (payload.verrous or payload.project_brief)
            )
        )
    ):
        return (
            "Une exigence ne peut contenir ni plan ni recherche. Le contexte "
            "projet et les verrous sans recherche sont réservés au mode autonome."
        )

    if intent in {
        ConsultantIntent.PROPOSE_PLAN,
        ConsultantIntent.ADD_TOPIC,
        ConsultantIntent.REMOVE_TOPIC,
        ConsultantIntent.CHANGE_PLAN,
    } and not payload.plan:
        return "L'action de plan sélectionnée exige un tableau plan non vide."

    if payload.plan and any(
        not _clean(row.get("title"), 400)
        for row in payload.plan
        if isinstance(row, Mapping)
    ):
        return "Chaque section du plan exige un titre non vide."

    if (
        require_reference_coverage
        and reference_plan
        and not _plan_covers_reference(payload.plan, reference_plan)
    ):
        return (
            "Le plan final doit conserver la structure de la version référencée "
            "et lui appliquer uniquement la modification demandée."
        )

    if (
        require_distinct_from_current
        and current_plan
        and _plans_are_effectively_identical(payload.plan, current_plan)
    ):
        return (
            "Le consultant demande une autre proposition : l'organisation, "
            "la hiérarchie ou les axes doivent réellement changer."
        )

    if payload.topics and any(
        not _clean(row.get("name") or row.get("topic"), 400)
        for row in payload.topics
        if isinstance(row, Mapping)
    ):
        return "Chaque sujet exige un nom non vide."

    if is_add_verrou:
        if not 1 <= len(payload.verrous) <= 10:
            return (
                "ADD_VERROU_AND_SEARCH exige entre un et dix verrous explicites."
            )
        if any(not _clean(verrou.title, 1200) for verrou in payload.verrous):
            return "Le verrou ajouté exige un titre non vide."
        if payload.plan or payload.topics or payload.constraints:
            return (
                "L'ajout d'un verrou ne peut pas modifier silencieusement le plan "
                "ou les exigences dans la même action."
            )
        if not payload.search_requests:
            return (
                "ADD_VERROU_AND_SEARCH exige des requêtes scientifiques pour le "
                "verrou déclaré."
            )
    elif payload.verrous and not standalone_context_declaration:
        return (
            "Le payload verrous est réservé à ADD_VERROU_AND_SEARCH. "
            "ADD_TOPIC ne crée qu'une section du plan."
        )

    if (
        payload.project_brief
        and intent != ConsultantIntent.ADD_VERROU_AND_SEARCH
        and not standalone_context_declaration
    ):
        return (
            "Le contexte projet autonome est réservé à la déclaration de verrous "
            "quand la recherche doit être initialisée depuis le chat."
        )

    if intent in _SEARCH_PAYLOAD_INTENTS and not payload.search_requests:
        return "L'action de recherche sélectionnée exige search_requests non vide."

    if intent in _SEARCH_PAYLOAD_INTENTS and payload.search_requests:
        overloaded_queries = [
            request.query
            for request in payload.search_requests
            if len(re.findall(r"[A-Za-z0-9+#./-]+", request.query)) > 12
        ]
        if overloaded_queries:
            return (
                "Chaque requête scientifique doit rester concise (douze mots "
                "significatifs au maximum). Répartis les concepts entre "
                "plusieurs requêtes complémentaires."
            )
        multidimensional = any(
            len(request.target_context_dimensions) >= 2
            or len(request.requested_dimensions) >= 3
            for request in payload.search_requests
        )
        if multidimensional and len(payload.search_requests) < 3:
            return (
                "Une recherche scientifique multidimensionnelle exige au moins "
                "trois requêtes complémentaires et concises, au lieu d'une "
                "requête unique qui concatène tous les concepts."
            )

    if intent in _SEARCH_PAYLOAD_INTENTS and (
        payload.plan or payload.topics or payload.constraints
    ):
        return "Une recherche ne peut contenir aucun autre payload métier."

    if payload.search_requests and any(
        not _clean(row.query, 1000)
        for row in payload.search_requests
    ):
        return "Chaque recherche exige une requête non vide."

    if any(
        row.query_kind == "direct_scientific_evidence"
        and not row.require_direct_evidence
        for row in payload.search_requests
    ):
        return (
            "direct_scientific_evidence exige "
            "require_direct_evidence=true."
        )
    return ""

def _plan_covers_reference(
    candidate: list[dict[str, Any]],
    reference: list[dict[str, Any]],
) -> bool:
    reference_rows = _compact_plan_snapshot(reference)
    if not reference_rows:
        return True
    candidate_rows = _compact_plan_snapshot(candidate)
    candidate_ids = {
        _clean(row.get("section_id"), 120)
        for row in candidate_rows
        if _clean(row.get("section_id"), 120)
    }
    candidate_titles = {
        _clean(row.get("title"), 320).casefold()
        for row in candidate_rows
        if _clean(row.get("title"), 320)
    }
    matched = sum(
        1
        for row in reference_rows
        if (
            _clean(row.get("section_id"), 120) in candidate_ids
            or _clean(row.get("title"), 320).casefold() in candidate_titles
        )
    )
    return matched / len(reference_rows) >= 0.70


def _plans_are_effectively_identical(
    candidate: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> bool:
    """Rejette uniquement une copie quasi exacte."""
    left = _compact_plan_snapshot(candidate)
    right = _compact_plan_snapshot(current)
    if not left or not right:
        return False

    def signature(rows: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
        return [
            (
                _clean(row.get("title"), 320).casefold(),
                int(row.get("level") or 1),
                _clean(row.get("parent_id"), 120).casefold(),
            )
            for row in rows
        ]

    left_sig = signature(left)
    right_sig = signature(right)
    if left_sig == right_sig:
        return True

    left_titles = {row[0] for row in left_sig if row[0]}
    right_titles = {row[0] for row in right_sig if row[0]}
    overlap = len(left_titles & right_titles) / max(1, len(left_titles | right_titles))
    same_size = abs(len(left_sig) - len(right_sig)) <= 1
    same_levels = [row[1] for row in left_sig] == [row[1] for row in right_sig]
    return overlap >= 0.92 and same_size and same_levels


class ConversationUnderstandingService:
    """Contrôleur conversationnel structuré, puis matérialiseur d'action."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()
        self._last_failure_context: ContextVar[dict[str, str] | None] = ContextVar(
            f"ennosmart_conversation_failure_{id(self)}",
            default=None,
        )

    def get_last_failure(self) -> dict[str, str]:
        return dict(self._last_failure_context.get() or {})

    def _generation_meta(self) -> dict[str, Any]:
        reader = getattr(self.llm, "get_last_generation_meta", None)
        if not callable(reader):
            return {}
        value = reader()
        return dict(value) if isinstance(value, Mapping) else {}

    def _call_structured(
        self,
        *,
        prompt: str,
        model_type: type[StructuredModel],
        request_name: str,
        max_output_tokens: int,
        consistency_check: Any = None,
    ) -> tuple[StructuredModel, list[dict[str, Any]]]:
        schema = model_type.model_json_schema()
        attempts: list[dict[str, Any]] = []
        previous_output = ""
        previous_error = ""

        for attempt in range(2):
            current_prompt = prompt
            if attempt:
                current_prompt = f"""
Répare la sortie JSON précédente pour qu'elle respecte exactement le schéma fourni
par l'API. Ne change pas l'objectif du dernier tour et n'invente aucune action.

ERREUR DE VALIDATION
{_clean(previous_error, 3000)}

SORTIE PRÉCÉDENTE
{_clean(previous_output, 7000)}

DEMANDE ORIGINALE
{prompt}
""".strip()

            raw = self.llm.generate(
                current_prompt,
                temperature=0.05,
                max_output_tokens=max_output_tokens,
                json_mode=True,
                response_schema=schema,
                request_name=(
                    request_name
                    if attempt == 0
                    else f"{request_name}:schema_repair"
                ),
            )
            meta = self._generation_meta()
            parsed = _extract_json(raw)
            try:
                if not parsed:
                    raise ValueError(
                        "La réponse ne contient pas d'objet JSON exploitable."
                    )
                # Les schémas restent stricts pour les objets métier persistés.
                # À la frontière LLM, une métadonnée explicative supplémentaire
                # ne doit toutefois pas annuler une intention valide ni provoquer
                # un second appel coûteux. Les champs connus restent entièrement
                # validés (types, littéraux, bornes) ; seuls les extras sont ignorés.
                result = model_type.model_validate(parsed, extra="ignore")
                if consistency_check is not None:
                    consistency_error = _clean(consistency_check(result), 2000)
                    if consistency_error:
                        raise ValueError(consistency_error)
                attempts.append({
                    "attempt": attempt + 1,
                    "valid": True,
                    **meta,
                })
                return result, attempts
            except Exception as exc:
                previous_output = raw
                previous_error = str(exc)
                attempts.append({
                    "attempt": attempt + 1,
                    "valid": False,
                    "validation_error": _clean(exc, 2000),
                    **meta,
                })

        raise ValueError(previous_error or "Sortie structurée invalide.")

    @staticmethod
    def _decision_prompt(
        *,
        consultant_message: str,
        history: list[dict[str, Any]],
        memory: ConversationMemory,
        project_context: Mapping[str, Any],
        current_plan: list[dict[str, Any]],
    ) -> str:
        intent_values = [
            intent.value
            for intent in ConsultantIntent
            if intent in _SUPPORTED_TURN_INTENTS
        ]
        return f"""
TOUR ACTUEL DU CONSULTANT — SOURCE D'AUTORITÉ
<current_turn>{consultant_message}</current_turn>

Tu es le contrôleur conversationnel d'un agent scientifique R&D. Comprends le sens
du tour actuel en français libre, fautif, elliptique ou familier. L'historique sert
uniquement à résoudre les références du tour actuel ; il ne t'autorise jamais à
continuer spontanément une ancienne tâche.

TOLÉRANCE AU LANGAGE HUMAIN
- Corrige mentalement les fautes d'orthographe, accords, accents manquants, mots
  tronqués et erreurs de frappe avant d'interpréter la demande.
- Une formulation courte ou télégraphique reste exécutable lorsque son sens le plus
  probable est établi par le tour actuel et l'historique récent. Par exemple, après
  l'affichage d'un plan, une approbation brève suivie d'un verbe de rédaction porte
  naturellement sur ce plan.
- N'utilise UNKNOWN que si au moins deux actions incompatibles restent réellement
  plausibles et que choisir l'une pourrait modifier le mauvais objet. Une confiance
  imparfaite sur la grammaire ou l'orthographe ne justifie jamais UNKNOWN à elle seule.
- corrected_message peut reformuler proprement le tour, mais conserve tous les noms,
  contraintes, négations et références fournis par le consultant.
- « Ajouter/enrichir/insérer un paragraphe ou un passage » dans une section existante
  modifie le contenu rédactionnel, jamais la structure du plan. N'utilise ADD_TOPIC
  que pour ajouter une section ou sous-section au plan. Sans recherche, une demande
  de modification du texte existant relève de REVISE_DRAFT.
- Si le consultant demande d'abord une recherche puis l'ajout d'un paragraphe dans
  une section, choisis SEARCH_MORE comme première et unique action immédiatement
  exécutable, conserve existing_paragraph et les target_section_ids exacts. La demande
  complète sera mémorisée comme directive de rédaction après validation des sources ;
  ne crée aucune section artificielle et ne demande aucun nouveau plan.

CAPACITÉS AUTORISÉES
{json.dumps(intent_values, ensure_ascii=False)}

SÉMANTIQUE DE DÉCISION
- CONVERSE : échange conversationnel, question générale, acte social ou réponse sans
  demande d'action projet. requested_actions doit être vide et aucun effet projet
  ne doit être annoncé.
- DESCRIBE_REQUIREMENTS : le consultant établit une exigence durable du livrable.
  En operating_mode=standalone_chat, cette capacité enregistre aussi le nom du
  projet, son domaine, son objectif et les verrous explicitement déclarés lorsque
  le consultant demande de conserver ce contexte sans lancer de recherche ni de
  rédaction. Ce tour n'est alors ni UNKNOWN ni ADD_VERROU_AND_SEARCH.
- PROPOSE_PLAN : demande d'un premier plan complet ou d'une nouvelle proposition
  alternative. Le plan est recréé depuis le corpus et l'histoire scientifique.
- ADD_TOPIC : ajout explicite d'une ou plusieurs sections/sous-sections sans
  supprimer la structure existante.
- REMOVE_TOPIC : retrait explicite d'une partie du plan.
- CHANGE_PLAN : réécriture, réorganisation ou modification d'une partie existante.
- SEARCH_MORE, SEARCH_ALTERNATIVE, REPLACE_SOURCE : recherche réelle de sources.
  SEARCH_ALTERNATIVE concerne uniquement les sources, jamais un autre plan.
- ADD_VERROU_AND_SEARCH : le consultant affirme explicitement qu'un verrou
  scientifique ou technologique manque au projet, demande de l'ajouter et souhaite
  rechercher des publications liées. C'est une action atomique : ne la sépare jamais
  en ADD_TOPIC puis SEARCH_MORE. Dans operating_mode=standalone_chat, utilise aussi
  cette capacité lorsqu'il demande de rechercher ou rédiger un état de l'art sur un
  ou plusieurs verrous qu'il déclare lui-même : le serveur les conservera dans la
  conversation sans créer de faux diagnostic. Une simple demande de section reste
  ADD_TOPIC.
- « nouveau » qualifie uniquement le nom qui le suit : « nouveau paragraphe »,
  « nouvelle section », « nouveau passage » ou « nouveaux articles » ne déclarent
  jamais un nouveau verrou. Si le consultant demande une recherche pour enrichir
  ce paragraphe/section, choisis SEARCH_MORE et rattache les requêtes au verrou
  actif. ADD_VERROU_AND_SEARCH exige une formulation explicite telle que
  « ajoute un verrou », « nouveau verrou » ou « verrou manquant ».
- Une méthode, un modèle ou un thème demandé « pour argumenter/renforcer/étayer le
  verrou initial/existant » est un complément de preuve : choisis SEARCH_MORE,
  explicit_new_verrou_declaration=false et conserve le verrou actif. Ne transforme
  jamais le nom de cette méthode ou de ce thème en titre de verrou.
- En operating_mode=diagnostic_backed, current_verrous est le catalogue des verrous
  scientifiques déjà retenus pour le projet. Une demande portant sur un verrou de ce
  catalogue, sur plusieurs d'entre eux ou sur leur totalité réutilise ce catalogue :
  elle ne crée aucun verrou et ne lance aucune recherche si le consultant ne la demande
  pas. Le nombre de verrous mentionné décrit la portée attendue, pas un ajout.
- Si le consultant demande de préparer ou rédiger un état de l'art sur les verrous
  existants alors que PLAN COURANT est vide, sélectionne PROPOSE_PLAN comme première
  action sûre. Ajoute START_WRITING dans requested_actions seulement si la rédaction
  est explicitement demandée ; elle restera différée jusqu'à la validation du plan.
  Si un plan exploitable existe déjà et que la rédaction est demandée, sélectionne
  START_WRITING.
- En standalone_chat, lorsqu'un verrou figure déjà dans current_verrous et que le
  consultant demande de rechercher « sur ce verrou », « sur le verrou enregistré »
  ou sur son identifiant/titre, choisis SEARCH_MORE. Ne recrée pas le verrou et ne
  réannonce pas l'enregistrement du contexte. ADD_VERROU_AND_SEARCH reste réservé à
  un verrou nouveau explicitement déclaré dans le tour actuel.
- EXPLAIN_SOURCE : explication sans nouvelle recherche.
- ACCEPT_PLAN, START_WRITING, REVISE_DRAFT et CANCEL : actions portant exactement
  leur nom. L'acceptation de sources et de brouillons exige leurs contrôles dédiés
  avec des identifiants explicites ; ne la simule pas dans ce canal.
- START_WRITING couvre aussi une formulation naturelle qui constate que le plan
  et les sources sont prêts puis demande de commencer, poursuivre ou produire
  l'état de l'art. Ce n'est pas une simple conversation.
- Lorsqu'un brouillon existe déjà et que le consultant demande de « reprendre »,
  « refaire », « réécrire », « enrichir » ou mettre à jour uniquement une partie,
  une section ou un paragraphe, choisis REVISE_DRAFT, content_target=existing_section
  ou existing_paragraph et résous ses target_section_ids. L'ajout d'articles déjà
  sélectionnés à cette partie reste une révision locale : ne choisis jamais
  START_WRITING pour refaire tout le document et ne modifie aucune autre section.
- « garde le plan », « on peut garder ce plan », « ce plan me convient », « valide ce
  plan » ou « conserve le même plan » constituent une approbation explicite lorsqu'elles
  sont formulées dans le tour courant. Si la même phrase ordonne aussi de rédiger,
  utilise la séquence atomique ACCEPT_PLAN -> START_WRITING décrite ci-dessus.
- « articles existants », « articles déjà trouvés/sélectionnés/gardés », « sources
  existantes/actuelles » ou formulations équivalentes désignent le corpus validé déjà
  disponible : writing_source_scope=all_validated et use_current_sources_only=true ;
  n'annonce aucune recherche complémentaire.
- UNKNOWN : le tour ne peut pas être résolu de façon fiable ; pose une question courte.

RÈGLES
- Une correction ou un rejet dans le tour actuel remplace l'objectif antérieur concerné.
- Quand le consultant référence « partie 3 », « section 3 », « 3.2 », « la troisième partie »
  ou le titre exact d'une section, résous cette référence contre PLAN COURANT INDEXÉ et
  conserve exactement son section_id comme cible. Ne choisis jamais une autre section
  parce qu'elle contient des mots sémantiquement proches.
- Les pronoms « cette partie », « cette section », « celle-ci », « la précédente » se
  résolvent d'abord avec le dernier échange pertinent et les snapshots de plan récents.
- Si le consultant corrige explicitement le dernier changement (« non », « pas la 5 mais
  la 3 », « tu as changé la mauvaise section », « je voulais la partie X »), il s'agit
  d'une correction du dernier edit : utilise plan_reference=previous et
  replace_current_plan=true afin de repartir de la version antérieure puis appliquer
  uniquement la correction demandée.
- Une ancienne réponse assistant, un article ou une lacune du projet n'est jamais une
  demande actuelle. N'en parle pas spontanément.
- requested_actions contient seulement les capacités effectivement demandées maintenant,
  dans leur ordre. Pour des actions COMPATIBLES déjà autorisées par le même tour, ne force
  pas artificiellement un aller-retour supplémentaire. En particulier, si un PLAN COURANT
  existe et que le consultant dit naturellement « garde/conserve/valide ce plan et rédige »,
  requested_actions=[ACCEPT_PLAN, START_WRITING], explicit_plan_approval=true,
  explicit_write_command=true et intent=START_WRITING : le serveur approuvera le plan puis
  lancera la rédaction de façon atomique. Une modification explicite de plan suivie de
  « valide et rédige » garde l'action de plan comme intent et conserve ACCEPT_PLAN puis
  START_WRITING dans requested_actions ; le serveur chaînera seulement après matérialisation
  réussie du nouveau plan. Les actions nécessitant une vraie validation humaine (nouveaux
  articles, nouveau verrou, sélection de sources) restent différées.
  Une mention, une hypothèse, une négation ou une action future n'en fait pas partie ;
  place les actions interdites dans forbidden_actions.
- Distingue toujours le CONTENU demandé pour le futur document de l'ACTION demandée
  maintenant. Un plan détaillé peut contenir des thèmes comme littérature, méthodes,
  résultats, limites, comparaison ou validation expérimentale sans demander aucune
  nouvelle recherche. Si le consultant fournit ou corrige une structure puis ordonne
  de rédiger avec le corpus déjà retenu, route la modification du plan suivie de
  ACCEPT_PLAN et START_WRITING ; ne choisis une action de recherche que si le tour
  demande réellement d'obtenir de nouvelles sources.
- Analyse la phrase de manière compositionnelle avant de choisir l'intention :
  (1) quel objet le consultant veut-il ajouter/modifier (plan, section, paragraphe,
  source, verrou), (2) quel rôle joue le thème nommé par rapport au verrou
  (complément de preuve, nouveau verrou, aucun lien), (3) quelle action est demandée
  maintenant (chercher, modifier, rédiger, expliquer). Ne transfère jamais l'adjectif
  « nouveau » d'un nom à un autre et ne transforme pas automatiquement le thème de
  recherche en verrou.
- En mode standalone_chat, une demande « écris/rédige l'état de l'art du verrou X »
  nécessite d'abord ADD_VERROU_AND_SEARCH si aucune source validée ni aucun verrou de
  session n'existe. Si la rédaction est explicitement demandée, ajoute START_WRITING
  après ADD_VERROU_AND_SEARCH dans requested_actions ; elle restera différée jusqu'à
  la validation des sources. Pour plusieurs verrous ou « tous les verrous », la portée
  de revue est globale. Pour un seul verrou nommé, elle est per_verrou.
- explicit_write_command, explicit_plan_approval et explicit_research_command ne valent
  true que si le tour actuel autorise réellement l'action correspondante.
- explicit_new_verrou_declaration vaut true uniquement lorsque le tour actuel affirme
  explicitement qu'un verrou nouveau ou manquant doit être ajouté. Il reste false pour
  une demande concernant un, plusieurs ou tous les verrous déjà présents dans
  current_verrous. ADD_VERROU_AND_SEARCH est invalide lorsque ce booléen vaut false.
- scientific_scope_relation décrit le rôle sémantique du sujet demandé :
  supports_existing_verrou si ce sujet sert à enrichir, comparer, nuancer, étayer,
  argumenter ou documenter un verrou existant ; declares_new_verrou seulement si le
  consultant demande explicitement d'en faire un verrou nouveau ; unrelated_to_verrou
  si le tour n'a aucun rapport avec les verrous ; unspecified si rien ne permet de
  trancher. Une nouvelle méthode/source/section est normalement un complément, pas un
  verrou.
- content_target indique l'objet réellement visé : existing_plan, existing_section,
  existing_paragraph, existing_draft, existing_verrou ou new_verrou. Par exemple,
  « ajoute un paragraphe sur X et cherche des articles » donne
  content_target=existing_paragraph et scientific_scope_relation=supports_existing_verrou ;
  « ajoute un verrou X et cherche » donne content_target=new_verrou et
  scientific_scope_relation=declares_new_verrou.
- plan_edit_scope décrit la portée de toute action de plan : local_section quand
  l'ajout, la modification ou la suppression vise une partie précise ; full_plan
  seulement lorsque le consultant demande réellement une refonte ou un remplacement
  global ; none hors action de plan. Cette décision est sémantique et ne dépend pas
  de mots-clés particuliers ni de la langue employée.
- Pour plan_edit_scope=local_section, target_section_ids contient exclusivement les
  section_id exacts résolus depuis PLAN COURANT INDEXÉ, y compris lorsque la cible est
  exprimée par un titre, une paraphrase, un numéro, un pronom ou une correction dans
  n'importe quelle langue. plan_edit_operation vaut add, modify ou remove selon
  l'effet demandé. Toutes les sections hors cible doivent rester strictement figées.
- replace_current_plan indique que le plan demandé remplace la structure courante.
  Il reste false pour un ajout ou une modification locale, même si l'intention
  principale est CHANGE_PLAN.
- plan_reference résout la version visée sans interprétation lexicale dans le code :
  none pour un nouveau plan indépendant, current pour le plan actif, previous pour
  la version immédiatement antérieure, first pour la première version proposée,
  specific pour une version explicitement nommée. Une ancienne version modifiée
  devient le plan final : replace_current_plan vaut donc true. Un simple ajout au
  plan actif utilise ADD_TOPIC, current et replace_current_plan=false.
- referenced_plan_version n'est rempli que pour plan_reference=specific.
- plan_generation_mode vaut initial pour une première proposition complète,
  alternative quand le consultant demande un autre plan complet, et none pour les
  modifications d'une version. Une alternative doit réellement changer
  l'organisation et la hiérarchie, pas seulement ajouter des sections au plan actif.
- plan_document_scope vaut state_of_art pour une revue de littérature, même si elle
  appartient à un projet plus large ; full_project_document seulement si le tour
  demande explicitement le plan du document projet complet.
- use_current_sources_only indique qu'une rédaction doit utiliser le corpus déjà
  disponible sans recherche supplémentaire.
- writing_source_scope précise ce corpus :
  all_validated = toutes les sources validées disponibles ;
  baseline_verrou_corpus = corpus scientifique initial rattaché aux verrous, en
  excluant les publications ajoutées par la recherche conversationnelle ;
  guided_research_additions = uniquement les publications ajoutées par cette recherche ;
  explicit_selection = uniquement les identifiants ou titres fournis dans
  writing_source_identifiers ; unspecified = aucune restriction exprimée.
- requested_source_count contient seulement un nombre explicitement demandé.
  Par exemple, « les 11 articles qu'on avait pour les verrous, sans les articles
  de recherche » signifie baseline_verrou_corpus, requested_source_count=11 et
  use_current_sources_only=true.
- verrou_scope décrit uniquement la portée explicitement demandée dans le tour
  actuel : per_verrou pour un ou plusieurs verrous nommés comme seule cible,
  global pour « tous les verrous », unchanged si le tour ne change pas la portée.
- Pour per_verrou, target_verrou_ids contient les ID exacts lus dans current_verrous.
  « verrou 1 » désigne le premier élément de cette liste, « verrou 2 » le deuxième,
  etc. Un titre cité est résolu vers l'ID du titre correspondant. N'invente jamais
  d'ID et ne confonds pas le numéro d'affichage avec l'ID technique.
- extracted_text cite le fragment du tour actuel qui fonde l'intention.
- assistant_message répond naturellement et proportionnellement au tour actuel. Ne récite
  pas l'état du projet et n'annonce aucune action non sélectionnée.
- memory est un delta : elle contient uniquement les nouveaux faits, préférences et
  décisions établis dans le tour actuel. Laisse ses champs vides en l'absence de
  nouveauté ; ne répète pas l'ancienne mémoire et n'inclus jamais les propositions
  de l'assistant ni une déduction provisoire.

CONTEXTE RÉCENT
{json.dumps(history, ensure_ascii=False)}

MÉMOIRE VALIDÉE
{memory.model_dump_json()}

PLAN COURANT INDEXÉ — SOURCE DE VÉRITÉ POUR « PARTIE/SECTION N »
{_indexed_plan_text(current_plan)}

PLAN COURANT JSON
{json.dumps(current_plan, ensure_ascii=False)}

CONTEXTE PROJET COMPACT
{json.dumps(dict(project_context), ensure_ascii=False)}

RAPPEL — INTERPRÈTE UNIQUEMENT CE TOUR
<current_turn>{consultant_message}</current_turn>
""".strip()

    @staticmethod
    def _resolution_prompt(
        *,
        consultant_message: str,
        history: list[dict[str, Any]],
        memory: ConversationMemory,
        project_context: Mapping[str, Any],
        current_plan: list[dict[str, Any]],
    ) -> str:
        """Demande au LLM une compréhension exécutable en un seul passage."""

        decision_prompt = ConversationUnderstandingService._decision_prompt(
            consultant_message=consultant_message,
            history=history,
            memory=memory,
            project_context=project_context,
            current_plan=current_plan,
        )
        return f"""
{decision_prompt}

SORTIE UNIQUE — COMPRÉHENSION ET ACTION
Tu es l'unique interprète sémantique de ce tour. Retourne dans le même objet JSON :
- decision : l'intention complète, toutes les actions compatibles demandées et la
  réponse conversationnelle ;
- action : les arguments métier exacts nécessaires pour exécuter cette décision.

Il n'existe aucun second classifieur qui complétera ou corrigera ton interprétation.
Tu dois donc préserver toutes les composantes du tour actuel. Une demande peut, dans
le même tour, fournir/remplacer un plan, l'approuver et ordonner la rédaction. Dans ce
cas l'intention principale reste l'action de plan, requested_actions contient dans
l'ordre l'action de plan, ACCEPT_PLAN et START_WRITING, les indicateurs d'approbation
et de rédaction valent true, et action.plan contient le nouveau plan complet. Ne
réduis jamais une telle demande à START_WRITING et ne valide jamais le plan courant
si le consultant vient d'en fournir un autre.

MATÉRIALISATION
- Les champs plan_reference, referenced_plan_version, plan_generation_mode et
  plan_document_scope appartiennent directement à decision, jamais à
  decision.classification.
- action.review_scope accepte uniquement auto, per_verrou ou global. Pour
  conserver la portée actuelle sans en imposer une nouvelle, utilise auto.
- action.plan est non vide uniquement pour PROPOSE_PLAN, ADD_TOPIC, REMOVE_TOPIC ou
  CHANGE_PLAN. Pour un remplacement complet, restitue toute la structure finale.
  Pour un ajout ou une modification locale, restitue seulement le patch demandé.
- Si le tour contient une hiérarchie de titres destinée au livrable, transforme-la
  fidèlement en objets section_id, title, objective, parent_id et level. Les titres
  fournis par le consultant font autorité ; n'y substitue pas les axes du plan courant.
- Pour plan_edit_scope=local_section, recopie les section_id exacts du plan indexé et
  ne modifie aucune section extérieure à la cible.
- Pour PROPOSE_PLAN, crée une structure propre au projet et au corpus, sans gabarit
  fixe. Une alternative doit réellement différer du plan courant.
- Pour plan_document_scope=state_of_art, organise une analyse de la littérature, de
  ses méthodes, résultats, validations et limites ; ne transforme pas automatiquement
  chaque verrou en mini-état de l'art si le consultant demande une narration globale.
- action.topics et action.constraints sont réservés à DESCRIBE_REQUIREMENTS.
- action.verrous et action.project_brief ne sont renseignés que lorsque le consultant
  déclare réellement un nouveau verrou ou un contexte autonome correspondant.
- Pour SEARCH_MORE, SEARCH_ALTERNATIVE ou REPLACE_SOURCE, action.search_requests
  contient deux à cinq requêtes complémentaires, courtes et distinctes. Une recherche
  multidimensionnelle en contient au moins trois. Le contenu souhaité dans le futur
  document n'est pas une commande de recherche.
- Pour ADD_VERROU_AND_SEARCH, action.verrous contient les verrous explicitement
  déclarés et action.search_requests couvre preuves, limites et validation.
- Pour ACCEPT_PLAN, START_WRITING, REVISE_DRAFT, EXPLAIN_SOURCE, CONVERSE, CANCEL ou
  UNKNOWN, action reste vide, sauf lorsqu'une action de plan est l'intention principale
  du même tour composé comme décrit ci-dessus.
- Tous les champs sans rapport avec les actions réellement demandées restent vides.

CONTRÔLE AVANT DE RÉPONDRE
Vérifie que decision.classification.rationale décrit toutes les composantes du tour,
que requested_actions les conserve dans leur ordre, et que action contient chaque
objet explicitement fourni par le consultant. La structure JSON sert à exécuter ta
compréhension ; elle ne doit jamais l'appauvrir.
""".strip()

    @staticmethod
    def _action_prompt(
        *,
        consultant_message: str,
        decision: _TurnDecision,
        history: list[dict[str, Any]],
        project_context: Mapping[str, Any],
        current_plan: list[dict[str, Any]],
        reference_plan: list[dict[str, Any]],
    ) -> str:
        return f"""
MATÉRIALISATION D'UNE CAPACITÉ DÉJÀ SÉLECTIONNÉE

TOUR ACTUEL
<current_turn>{consultant_message}</current_turn>

INTENTION VALIDÉE
{decision.classification.model_dump_json()}

Produis uniquement les arguments nécessaires à cette intention :
- Pour DESCRIBE_REQUIREMENTS, topics et/ou constraints matérialisent précisément
  les exigences durables du tour actuel ; plan reste vide. En
  operating_mode=standalone_chat, si le consultant demande d'enregistrer son
  contexte sans recherche, project_brief reprend uniquement le nom, le domaine,
  l'objectif et le contexte explicitement fournis, et verrous reprend le ou les
  verrous déclarés. Dans ce cas topics et constraints peuvent rester vides,
  search_requests reste impérativement vide et aucune recherche n'est annoncée.
- Pour PROPOSE_PLAN, crée une structure scientifique adaptée à ce projet précis :
  déduis librement le nombre de sections, leur hiérarchie et leurs objectifs des
  articles validés, de l'histoire scientifique et de la demande. Ne copie pas le
  plan courant et n'utilise aucun gabarit fixe, sauf si le tour référence
  explicitement une version antérieure.
- La politique writing_source_scope de l'INTENTION VALIDÉE s'applique aussi au
  plan proposé. Pour baseline_verrou_corpus, fonde le plan uniquement sur les
  cartes où guided_research_source=false ; pour guided_research_additions,
  uniquement sur celles où il vaut true ; pour explicit_selection, uniquement
  sur les identifiants demandés. N'utilise pas une carte hors portée pour créer
  un axe ou une sous-section.
- Pour plan_generation_mode=alternative, propose une organisation complète
  réellement différente : change les axes de synthèse et/ou la hiérarchie selon
  ce que justifient les articles et l'histoire scientifique. Ne complète pas
  simplement le plan courant.
- PLAN DE RÉFÉRENCE RÉSOLU est la base exacte à utiliser lorsque plan_reference
  vaut current, previous, first ou specific. Si une ancienne version est visée,
  restitue sa structure complète puis applique seulement la modification demandée ;
  ne pars jamais du PLAN COURANT.
- Pour ADD_TOPIC, renvoie uniquement les ajouts. Pour CHANGE_PLAN avec
  replace_current_plan=false, renvoie uniquement les sections ajoutées ou modifiées.
  Pour CHANGE_PLAN avec replace_current_plan=true et pour REMOVE_TOPIC, renvoie
  l'intégralité de la structure finale.
- Pour plan_edit_scope=local_section, ne renvoie qu'un patch local : la section cible,
  ses descendants réellement modifiés et les nouveaux descendants demandés. Recopie
  exactement les section_id de target_section_ids. Ne réécris, ne renomme, ne déplace
  et ne supprime aucune section extérieure à cette portée ; le serveur les figera.
- IMPORTANT — références structurelles : lorsqu'un consultant nomme une partie/section
  par son numéro ou son titre, PLAN COURANT INDEXÉ / PLAN DE RÉFÉRENCE INDEXÉ donne
  l'identité exacte de la cible. Ne déplace jamais la demande vers une autre partie.
- Si la demande transforme une section existante en sous-sections, chaque nouvelle
  sous-section doit avoir parent_id égal EXACTEMENT au section_id de la section cible
  et level égal au level du parent + 1. Ne crée pas les sous-sections sous une autre
  section même si son titre contient des concepts proches.
- Une correction « pas X, mais Y » annule l'édition précédente incorrecte : quand
  plan_reference=previous, repars du PLAN DE RÉFÉRENCE RÉSOLU complet et applique la
  modification à Y seulement.
- Pour plan_document_scope=state_of_art, chaque section de fond doit analyser la
  littérature existante : familles de méthodes, données, résultats comparés,
  limites, contradictions, insuffisances et verrous. Exclue les sections consacrées
  aux propositions propres du projet, à sa méthodologie future ou à ses résultats
  attendus ; elles relèvent d'un document projet complet, pas de l'état de l'art.
- Chaque objet de plan contient section_id, title, objective, parent_id et level.
- topics et constraints sont réservés à DESCRIBE_REQUIREMENTS. Pour une action
  de plan ou de recherche, ces tableaux restent vides.
- Pour ADD_VERROU_AND_SEARCH, verrous contient un à dix objets avec title,
  justification, supporting_context, source_document_ids et force_create_distinct.
  source_document_ids contient uniquement des identifiants explicitement donnés.
  force_create_distinct vaut true seulement si le consultant demande clairement de
  créer un verrou distinct malgré un verrou proche. En operating_mode=standalone_chat,
  project_brief contient les seuls éléments explicitement donnés : project_name,
  domain, objective et additional_context. N'invente pas les champs absents et ne
  perds jamais un objectif explicitement formulé dans le même message que le verrou.
  review_scope vaut per_verrou pour un seul verrou ciblé, global pour plusieurs
  verrous ou « tous les verrous », sinon auto. search_requests contient 2 à 8
  recherches scientifiques couvrant les preuves favorables, les résultats contraires,
  les limites et les conditions de validation. target_verrous reste vide pour un
  nouveau verrou : le serveur injectera son identifiant DB ou de session après sa
  création. plan, topics et constraints restent vides.
- Pour une recherche, search_requests contient 2 à 5 objets avec query anglaise
  concise, entity_name, query_kind (scientific_evidence,
  direct_scientific_evidence ou official_documentation), required_terms,
  excluded_terms, section_ids,
  section_titles, target_verrous, requested_dimensions,
  target_context_dimensions, require_direct_evidence et source_preferences.
- La demande du consultant est la cible scientifique primaire. Le verrou, la
  section ou le paragraphe cité sert uniquement à rattacher et contextualiser
  cette cible : ne remplace jamais « FEKO pour RCS bistatique » par une recherche
  générale sur tout le verrou SAR.
- Distingue un outil ou protocole externe publiquement documenté d'un nom interne
  de projet, client ou simulateur. Pour un outil externe, émets une requête directe
  exigeant son nom et une requête official_documentation distincte. Pour un nom
  interne, conserve au plus une requête directe sur ce nom, puis décompose le besoin
  en requêtes transférables portant sur les méthodes scientifiques sous-jacentes,
  le domaine d'application, les protocoles de validation et les limites. Dans ces
  requêtes transférables, le nom interne ne figure ni dans entity_name ni dans
  required_terms. Seuls les articles constituent une preuve scientifique de résultats.
- Pour une méthode recherchée dans un domaine ou une application précise,
  target_context_dimensions contient obligatoirement ce contexte cible et
  required_terms sépare les ancrages indispensables (méthode, domaine, tâche).
  Une publication qui emploie la méthode dans un domaine sans rapport ne doit
  pas être rendue pertinente par la seule présence du nom de la méthode.
- Lorsque la demande comporte plusieurs conditions, mécanismes, types de preuve
  ou dimensions d'évaluation, produis 3 à 5 requêtes complémentaires courtes :
  une sur les preuves directes dans le domaine, une sur les méthodes de
  généralisation ou d'adaptation, et une sur les protocoles, échecs ou limites.
  Ne concatène jamais tous les axes dans une seule requête surchargée.
- Tous les tableaux sans rapport avec l'intention restent vides.

PLAN COURANT INDEXÉ
{_indexed_plan_text(current_plan)}

PLAN COURANT JSON
{json.dumps(current_plan, ensure_ascii=False)}

PLAN DE RÉFÉRENCE INDEXÉ
{_indexed_plan_text(reference_plan)}

PLAN DE RÉFÉRENCE RÉSOLU JSON
{json.dumps(reference_plan, ensure_ascii=False)}

HISTORIQUE RÉCENT, AVEC SNAPSHOTS DE PLANS SI DISPONIBLES
{json.dumps(history, ensure_ascii=False)}

CONTEXTE PROJET COMPACT
{json.dumps(dict(project_context), ensure_ascii=False)}

Ne réponds pas à une ancienne demande et n'ajoute aucune action non sélectionnée.
""".strip()

    def understand(
        self,
        *,
        session: GuidedResearchSessionData,
        consultant_message: str,
        project_context: Mapping[str, Any],
        current_plan: list[dict[str, Any]],
    ) -> ConversationUnderstanding | None:
        self._last_failure_context.set(None)
        memory = _existing_memory(session)
        history = _recent_history(session)
        compact_context = _compact_project_context(project_context)
        compact_plan = _compact_plan(current_plan)

        try:
            resolution_prompt = self._resolution_prompt(
                consultant_message=consultant_message,
                history=history,
                memory=memory,
                project_context=compact_context,
                current_plan=compact_plan,
            )
            allow_standalone_context = (
                _clean(compact_context.get("operating_mode"), 80)
                == "standalone_chat"
            )

            resolution, resolution_attempts = self._call_structured(
                prompt=resolution_prompt,
                model_type=_TurnResolution,
                request_name=(
                    "ennoscholar:guided_research:conversation_resolution"
                ),
                max_output_tokens=7000,
            )
            decision = _honor_llm_action_order(resolution.decision)
            intent = decision.classification.intent
            action = _scope_action_payload(
                intent,
                resolution.action,
                allow_standalone_context=allow_standalone_context,
            )
            reference_plan = _resolve_plan_reference(
                decision=decision,
                history=history,
                project_context=compact_context,
                current_plan=compact_plan,
            )

            # La mémoire reste un simple cumul de faits explicitement compris par
            # le même LLM ; aucune autre couche ne requalifie l'intention.
            if intent == ConsultantIntent.UNKNOWN:
                decision.memory = memory
            else:
                decision.memory = _merge_memory_delta(memory, decision.memory)

            return ConversationUnderstanding(
                classification=decision.classification,
                assistant_message=decision.assistant_message,
                plan=action.plan,
                topics=action.topics,
                constraints=action.constraints,
                verrous=[
                    verrou.model_dump(mode="json")
                    for verrou in action.verrous
                ],
                project_brief=(
                    action.project_brief.model_dump(mode="json")
                    if action.project_brief is not None
                    else {}
                ),
                review_scope=action.review_scope,
                search_requests=[
                    request.model_dump(mode="json")
                    for request in action.search_requests
                ],
                memory=decision.memory,
                interpreter={
                    "architecture": "single_llm_resolution_v3",
                    "explicit_plan_labels": [],
                    "resolved_plan_targets": [
                        {
                            "display_label": row.get("display_label"),
                            "section_id": row.get("section_id"),
                            "title": row.get("title"),
                        }
                        for row in _indexed_plan_rows(reference_plan or compact_plan)
                        if _clean(row.get("section_id"), 160)
                        in set(decision.classification.target_section_ids or [])
                    ],
                    "resolution_attempts": resolution_attempts,
                    "prompt_chars": len(resolution_prompt),
                    "prompt_truncated": any(
                        bool(row.get("prompt_truncated"))
                        for row in resolution_attempts
                    ),
                },
            )
        except Exception as exc:
            self._last_failure_context.set({
                "stage": "structured_conversation",
                "error_type": type(exc).__name__,
                "message": _clean(exc, 1800),
            })
            logger.exception(
                "Échec du contrôleur conversationnel structuré; aucune action autorisée."
            )
            return None


__all__ = [
    "ConversationUnderstandingService",
    "_ground_writing_source_policy",
]
