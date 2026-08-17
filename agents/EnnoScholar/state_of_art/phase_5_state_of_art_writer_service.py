# -*- coding: utf-8 -*-
from __future__ import annotations

"""Phase 5 canonique — rédaction globale evidence-first.

Principes :

* un seul état de l'art global ;
* Phase 4.7 reste l'histoire scientifique canonique ;
* le plan consultant approuvé peut fournir les grands titres ;
* chaque verrou confirmé reste présent sous ces grands titres ;
* Article Cards et unités Phase 4.5 sont les seules preuves citables ;
* sans preuve exploitable, la rédaction est bloquée ;
* aucun vocabulaire, chiffre, article ou récit propre à un projet n'est fourni
  par défaut.
"""

import hashlib
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from modules.LLM.llm_client import LLMClient, reload_config

from ..contracts import (
    ContractError,
    assert_same_verrous,
    build_confirmed_contract,
    clean_text as contract_clean_text,
    extract_verrou_items,
    parse_aliases,
    resolve_approved_plan,
)
from ..storage_paths import consultant_plan_path as default_consultant_plan_contract_path
from ..storage_paths import guided_sources_path as default_guided_sources_path
from ..storage_paths import state_of_art_root
from .cir_quality_guard_v3 import (
    apply_cir_postprocessing,
    audit_cir_draft,
    audit_cir_section,
    build_cir_evidence_matrix,
    build_cir_quality_report,
    evidence_matrix_for_prompt,
    filter_visual_placements,
)


PAYLOAD_TYPE = "state_of_art_draft_payload_canonical_global_v1"
FORBIDDEN_FINAL_MARKERS = {
    "phase 4",
    "phase 4.5",
    "phase 4.6",
    "phase 4.7",
    "phase 5",
    "ennoscholar",
    "ennodiagnostic",
    "article card",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: Any, limit: int = 10000) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = " ".join(clean_text(item, limit) for item in value)
    elif isinstance(value, dict):
        for key in (
            "text",
            "value",
            "title",
            "label",
            "name",
            "description",
            "summary",
            "resume",
            "content",
        ):
            candidate = clean_text(value.get(key), limit)
            if candidate:
                value = candidate
                break
        else:
            value = ""
    text = str(value).replace("\u00a0", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit].strip()


def clean_sentence(value: Any, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", clean_text(value, limit)).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, dict):
        return [value]
    return [value]


def unique(values: Iterable[Any]) -> List[str]:
    output: List[str] = []
    seen: Set[str] = set()
    for value in values:
        text = clean_sentence(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def read_json(path: str | Path, default: Any = None) -> Any:
    source = Path(path)
    if not source.is_file():
        return default
    try:
        return json.loads(source.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def normalize_citation_label(value: Any) -> str:
    text = clean_text(value, 80).strip("[] ")
    match = re.search(r"\bA\s*(\d+)\b", text, flags=re.I)
    return f"A{match.group(1)}" if match else ""


def citation_sort(labels: Iterable[Any]) -> List[str]:
    found = {
        label
        for label in (normalize_citation_label(value) for value in labels)
        if label
    }
    return sorted(found, key=lambda label: int(label[1:]))


_CITATION_GROUP_RE = re.compile(
    r"\[([^\[\]]*\bA\s*\d+\b[^\[\]]*)\]",
    flags=re.I,
)


def citations_from_text(text: Any) -> List[str]:
    labels: List[str] = []
    for group in _CITATION_GROUP_RE.finditer(
        clean_text(text, 200000)
    ):
        labels.extend(
            f"A{match.group(1)}"
            for match in re.finditer(
                r"\bA\s*(\d+)\b",
                group.group(1),
                flags=re.I,
            )
        )
    return citation_sort(labels)


def _strip_citation_groups(text: Any) -> str:
    return _CITATION_GROUP_RE.sub(" ", clean_text(text, 300000))


def citations_from_obj(value: Any) -> List[str]:
    output: List[str] = []
    if isinstance(value, Mapping):
        for key in (
            "citation",
            "citation_label",
            "citations",
            "citation_labels",
            "required_citations",
            "allowed_citations",
            "linked_citations",
            "direct_citations",
            "related_citations",
            "methodological_citations",
            "background_citations",
        ):
            output.extend(citations_from_obj(value.get(key)))
        for child in value.values():
            if isinstance(child, (Mapping, list, tuple)):
                output.extend(citations_from_obj(child))
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            output.extend(citations_from_obj(child))
    else:
        label = normalize_citation_label(value)
        if label:
            output.append(label)
    return citation_sort(output)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "oui"}


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _tokens(value: Any) -> Set[str]:
    text = clean_text(value, 10000).lower()
    text = re.sub(r"[^a-z0-9à-öø-ÿ]+", " ", text)
    stop = {
        "avec", "dans", "pour", "sans", "entre", "cette", "des", "les", "une",
        "sur", "par", "que", "qui", "est", "sont", "the", "and", "for", "with",
        "from", "that", "this", "scientifique", "technique", "verrou", "section",
        "état", "art", "projet", "méthode", "method", "données", "data",
    }
    return {
        token
        for token in text.split()
        if len(token) >= 3 and token not in stop
    }


def _similarity(left: Any, right: Any) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


# ---------------------------------------------------------------------------
# Paths — signatures conservées
# ---------------------------------------------------------------------------

def payload_root(organisme: str, project: str, year: str) -> Path:
    return state_of_art_root(organisme, project, str(year))


def default_selection_payload_path(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "selection_payload.json"


def default_article_cards_payload_path(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "article_cards" / "article_cards_payload.json"


def default_phase3_dir(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "phase_3_style_memory"


def default_fewshot_payload_path(organisme: str, project: str, year: str) -> Path:
    return default_phase3_dir(organisme, project, year) / "fewshot_payload.json"


def default_style_profile_payload_path(organisme: str, project: str, year: str) -> Path:
    return default_phase3_dir(organisme, project, year) / "style_profile_payload.json"


def default_argumentation_profile_payload_path(organisme: str, project: str, year: str) -> Path:
    return default_phase3_dir(organisme, project, year) / "argumentation_profile_payload.json"


def default_scientific_reasoning_payload_path(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "phase_4_5_scientific_reasoning" / "scientific_reasoning_payload.json"


def default_phase46_payload_path(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "phase_4_6_project_rd_argumentation" / "project_rd_argumentation_payload.json"


def default_phase47_payload_path(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "phase_4_7_scientific_narrative" / "scientific_narrative_payload.json"


def output_dir(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "phase_5_state_of_art_writer"


def output_payload_path(organisme: str, project: str, year: str) -> Path:
    return output_dir(organisme, project, year) / "state_of_art_draft_payload.json"


def output_markdown_path(organisme: str, project: str, year: str) -> Path:
    return output_dir(organisme, project, year) / "state_of_art_draft.md"


def blueprint_output_path(organisme: str, project: str, year: str) -> Path:
    return output_dir(organisme, project, year) / "unified_writer_blueprint_used.json"


def normalized_evidence_output_path(organisme: str, project: str, year: str) -> Path:
    return output_dir(organisme, project, year) / "normalized_evidence_units.json"


def prompts_dir(organisme: str, project: str, year: str) -> Path:
    return output_dir(organisme, project, year) / "prompts"


def supplemental_sources_path(organisme: str, project: str, year: str) -> Path:
    return default_guided_sources_path(organisme, project, year)


# ---------------------------------------------------------------------------
# Article Cards et preuves
# ---------------------------------------------------------------------------

def _find_card_container(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (
        "article_cards",
        "cards",
        "selected_article_cards",
        "writing_ready_cards",
        "items",
        "articles",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [dict(item) for item in value.values() if isinstance(item, dict)]
    for key in ("data", "result", "payload", "article_cards_payload"):
        nested = _find_card_container(payload.get(key))
        if nested:
            return nested
    return []


def extract_article_cards(payload: Any) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for index, raw in enumerate(_find_card_container(payload), 1):
        if raw.get("excluded_from_writing") or raw.get("writing_excluded"):
            continue
        status = clean_text(raw.get("writing_status") or raw.get("status"), 80).lower()
        if status in {"excluded", "missing_fulltext", "invalid", "rejected"}:
            continue
        label = normalize_citation_label(
            raw.get("citation_label")
            or raw.get("citation")
            or raw.get("label")
            or raw.get("citation_token")
        )
        if not label:
            continue
        if label in seen:
            continue
        seen.add(label)
        card = dict(raw)
        card["citation_label"] = label
        card["title"] = clean_sentence(
            raw.get("title")
            or raw.get("article_title")
            or raw.get("paper_title"),
            600,
        )
        cards.append(card)
    return sorted(cards, key=lambda item: int(item["citation_label"][1:]))


def apply_writing_source_policy(
    cards: List[Dict[str, Any]],
    plan_contract: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Applique la portée de corpus décidée dans la conversation.

    La sélection repose uniquement sur la provenance persistée des cartes et
    sur des identifiants explicites. Aucun titre, domaine ou article n'est codé
    en dur.
    """
    raw_policy = plan_contract.get("writing_source_policy")
    policy = dict(raw_policy) if isinstance(raw_policy, Mapping) else {}
    scope = clean_text(policy.get("scope"), 80) or "all_validated"
    policy_grounded = bool(policy.get("grounded_in_current_message"))
    legacy_policy_recovered = False
    requested_identifiers = {
        clean_text(value, 1000).casefold()
        for value in as_list(policy.get("source_identifiers") or [])
        if clean_text(value, 1000)
    }
    try:
        requested_count = int(policy.get("requested_source_count"))
        if requested_count <= 0:
            requested_count = None
    except (TypeError, ValueError):
        requested_count = None
    explicit_require_all = policy.get("require_all_selected_sources")
    if explicit_require_all is None:
        # Un nombre de sources explicitement demandé décrit un corpus exact.
        # Le writer doit alors rendre compte de chacune d'elles au lieu de
        # considérer le nombre comme un simple filtre d'entrée.
        require_all_selected_sources = requested_count is not None
    else:
        require_all_selected_sources = bool(explicit_require_all)

    def is_guided(card: Mapping[str, Any]) -> bool:
        return card.get("guided_research_source") is True

    def identities(card: Mapping[str, Any]) -> Set[str]:
        values = {
            card.get("citation_label"),
            card.get("article_id"),
            card.get("guided_candidate_id"),
            card.get("doi"),
            card.get("title"),
        }
        return {
            clean_text(value, 1000).casefold()
            for value in values
            if clean_text(value, 1000)
        }

    if scope in {"", "unspecified", "all_validated"}:
        selected = list(cards)
        normalized_scope = "all_validated"
    elif scope == "baseline_verrou_corpus":
        selected = [card for card in cards if not is_guided(card)]
        normalized_scope = scope
        if requested_count is not None and len(selected) != requested_count:
            linked_to_verrous = [
                card
                for card in selected
                if (
                    as_list(card.get("verrou_ids") or [])
                    or as_list(card.get("target_verrous") or [])
                    or as_list(card.get("covered_verrou_ids") or [])
                )
            ]
            if len(linked_to_verrous) == requested_count:
                selected = linked_to_verrous
    elif scope == "guided_research_additions":
        selected = [card for card in cards if is_guided(card)]
        normalized_scope = scope
    elif scope == "explicit_selection":
        selected = [
            card
            for card in cards
            if identities(card) & requested_identifiers
        ]
        normalized_scope = scope
    else:
        raise ContractError(
            "unsupported_writing_source_scope",
            "La portée de sources demandée n'est pas reconnue.",
            {"scope": scope},
        )

    if (
        scope == "explicit_selection"
        and not policy_grounded
        and (
            (requested_count is not None and len(selected) != requested_count)
            or len(selected) != len(requested_identifiers)
        )
    ):
        # Les anciennes conversations pouvaient recopier des références A/C
        # historiques dans le contrat sans qu'elles figurent dans le message
        # courant. Elles représentent alors une contrainte non fiable. Le
        # corpus consultant actuellement validé reste la seule frontière sûre.
        selected = list(cards)
        normalized_scope = "all_validated"
        scope = "all_validated"
        requested_count = None
        requested_identifiers = set()
        require_all_selected_sources = False
        legacy_policy_recovered = True

    if requested_count is not None and len(selected) != requested_count:
        raise ContractError(
            "writing_source_count_mismatch",
            (
                f"Le corpus demandé contient {len(selected)} source(s) "
                f"éligible(s), au lieu des {requested_count} attendues."
            ),
            {
                "scope": normalized_scope,
                "requested_source_count": requested_count,
                "eligible_source_count": len(selected),
                "eligible_citations": [
                    card.get("citation_label") for card in selected
                ],
            },
        )
    if scope == "explicit_selection" and len(selected) != len(
        requested_identifiers
    ):
        matched = set().union(*(identities(card) for card in selected)) if selected else set()
        raise ContractError(
            "writing_source_selection_incomplete",
            "Certaines sources explicitement demandées sont introuvables.",
            {
                "requested_identifiers": sorted(requested_identifiers),
                "unmatched_identifiers": sorted(requested_identifiers - matched),
            },
        )

    report = {
        "scope": normalized_scope,
        "input_source_count": len(cards),
        "eligible_source_count": len(selected),
        "excluded_source_count": len(cards) - len(selected),
        "requested_source_count": requested_count,
        "require_all_selected_sources": require_all_selected_sources,
        "guided_sources_excluded": (
            normalized_scope == "baseline_verrou_corpus"
        ),
        "eligible_citations": [
            card.get("citation_label") for card in selected
        ],
        "policy_grounded_in_current_message": policy_grounded,
        "legacy_policy_recovered": legacy_policy_recovered,
    }
    return selected, report


def extract_supplemental_source_cards(
    payload: Any,
    existing_cards: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convertit les sources guidées validées en cartes complémentaires.

    Une documentation officielle reste limitée aux définitions et procédures.
    Une publication scientifique n'est jamais convertie depuis son seul
    résumé : elle doit passer par PDF direct/MCP puis par une vraie Article Card.
    """
    rows = payload.get("sources") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    next_index = 1 + max(
        [int(card["citation_label"][1:]) for card in existing_cards] or [0]
    )
    output: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for card in existing_cards:
        if not isinstance(card, dict):
            continue
        for value in (card.get("doi"), card.get("url"), card.get("title")):
            identity = clean_text(value, 2000).casefold()
            if identity:
                seen.add(identity)
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        if raw.get("consultant_decision") != "accepted":
            continue
        title = clean_sentence(raw.get("title"), 600)
        url = clean_text(raw.get("url"), 2000)
        identities = {
            clean_text(value, 2000).casefold()
            for value in (raw.get("doi"), url, title)
            if clean_text(value, 2000)
        }
        if not title or not identities or identities & seen:
            continue
        seen.update(identities)
        kind = clean_text(raw.get("candidate_kind"), 80) or "supplemental_source"
        if kind not in {
            "official_documentation",
            "documentation",
            "software_repository",
        }:
            # Les publications guidées doivent être présentes dans les cartes
            # normales, construites uniquement depuis un texte intégral vérifié.
            continue
        abstract = clean_text(raw.get("content_excerpt") or raw.get("abstract"), 12000)
        if len(abstract) < 40:
            continue
        label = f"A{next_index}"
        next_index += 1
        scope = unique(as_list(raw.get("evidence_scope") or []))
        card: Dict[str, Any] = {
            **raw,
            "citation_label": label,
            "title": title,
            "url": url,
            "candidate_kind": kind,
            "source_scope": scope,
            "guided_research_source": True,
            "consultant_validated": True,
            "abstract": abstract,
            "contribution": abstract,
            "verrou_ids": unique(
                as_list(raw.get("target_verrous") or raw.get("verrou_ids") or [])
            ),
        }
        if kind in {
            "official_documentation",
            "documentation",
            "software_repository",
        }:
            card.update(
                {
                    "technical_principle": abstract,
                    "protocol": abstract,
                    "scientific_evidence_eligible": False,
                    "documentation_scope_only": True,
                }
            )
        output.append(card)
    return output


_ATOMIC_CITATION_KEYS = (
    "citation_label",
    "citation",
    "article_citation",
    "source_citation",
)


def _node_citations(node: Mapping[str, Any]) -> List[str]:
    """Retourne uniquement les citations dont *node* est propriétaire.

    ``citations_from_obj`` parcourt récursivement tout un sous-arbre. Utilisé
    ici, il attribuait chaque phrase d'un bloc multi-articles à toutes les
    citations rencontrées plus bas dans ce bloc. Une méthode décrite par A12
    pouvait ainsi devenir artificiellement une preuve A17.

    Les collections telles que ``direct_citations`` ou
    ``required_citations`` sont des contrats de couverture, pas la provenance
    atomique d'une affirmation. Elles sont donc volontairement ignorées.
    """
    citations: List[str] = []
    for key in _ATOMIC_CITATION_KEYS:
        value = node.get(key)
        if isinstance(value, (list, tuple, set)):
            citations.extend(value)
        else:
            citations.append(value)
    # Une liste explicitement nommée ``claim_citations`` est acceptée car elle
    # appartient à l'affirmation courante, contrairement aux listes de
    # couverture d'une section ou d'un verrou.
    citations.extend(as_list(node.get("claim_citations") or []))
    return citation_sort(citations)


EVIDENCE_FIELD_TYPES: Dict[str, str] = {
    "technical_principle_raw": "method",
    "technical_principle": "method",
    "principle": "method",
    "mechanism": "method",
    "method_or_concept": "method",
    "method_name": "method",
    "method": "method",
    "approach": "method",
    "data_context_raw": "data",
    "data_context": "data",
    "dataset": "data",
    "experimental_conditions": "protocol",
    "validation_protocol_raw": "protocol",
    "validation_protocol": "protocol",
    "protocol": "protocol",
    "results_raw": "result",
    "results": "result",
    "result": "result",
    "result_claim": "result",
    "metrics_raw": "result",
    "metrics": "result",
    "limitations_raw": "limitation",
    "limitations": "limitation",
    "limitation": "limitation",
    "limits": "limitation",
    "research_gap": "limitation",
    "gap": "limitation",
    "contribution": "contribution",
    "finding": "contribution",
    "claim": "contribution",
    "claim_text": "contribution",
}


def _sentences(value: Any) -> List[str]:
    if isinstance(value, list):
        output: List[str] = []
        for item in value:
            output.extend(_sentences(item))
        return output
    if isinstance(value, dict):
        output = []
        for child in value.values():
            if not isinstance(child, (dict, list)):
                output.extend(_sentences(child))
        return output
    text = clean_text(value, 5000)
    if not text:
        return []
    return [
        clean_sentence(part, 900)
        for part in re.split(r"(?<=[.!?;])\s+|\n+", text)
        if len(clean_sentence(part, 900)) >= 18
    ]


def _is_secondary_reference_statement(sentence: str) -> bool:
    """Détecte une méthode attribuée par l'article à une autre publication.

    Ces phrases restent utiles pour cartographier la bibliographie, mais elles
    ne prouvent pas que la méthode ou le résultat appartient à l'article hôte.
    Les promouvoir comme preuves de la carte courante crée précisément les
    inversions de citations observées entre articles.
    """
    text = clean_sentence(sentence, 1200)
    return bool(
        re.match(
            r"^(?:"
            r"\[\d+\]"
            r"|[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+"
            r"(?:\s+et\s+al\.)?\s*\[\d+\]"
            r")\s*(?:"
            r"proposed|introduced|developed|presented|reported|"
            r"demonstrated|described|used|applied|investigated"
            r")\b",
            text,
            flags=re.I,
        )
    )


def _collect_units_from_node(
    node: Any,
    *,
    inherited_citations: Optional[List[str]] = None,
    source: str,
    output: List[Dict[str, Any]],
    seen: Set[Tuple[str, str, str]],
    path: str = "root",
    depth: int = 0,
) -> None:
    if depth > 10:
        return
    if isinstance(node, list):
        for index, child in enumerate(node):
            _collect_units_from_node(
                child,
                inherited_citations=inherited_citations,
                source=source,
                output=output,
                seen=seen,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
        return
    if not isinstance(node, dict):
        return
    owned_citations = _node_citations(node)
    inherited = citation_sort(inherited_citations or [])
    citations = owned_citations or inherited
    verrou_ids = [
        clean_text(value, 120)
        for value in as_list(node.get("verrou_ids") or node.get("verrou_id"))
        if clean_text(value, 120)
    ]
    for field, kind in EVIDENCE_FIELD_TYPES.items():
        if field not in node:
            continue
        for sentence in _sentences(node.get(field)):
            if (
                source == "article_card"
                and _is_secondary_reference_statement(sentence)
            ):
                continue
            for citation in citations:
                key = (citation, kind, sentence.casefold())
                if key in seen:
                    continue
                seen.add(key)
                output.append(
                    {
                        "evidence_id": hashlib.sha256(
                            (
                                f"{citation}|{kind}|{sentence.casefold()}|"
                                f"{source}|{path}"
                            ).encode("utf-8")
                        ).hexdigest()[:20],
                        "citation_label": citation,
                        "kind": kind,
                        "text": sentence,
                        "verrou_ids": verrou_ids,
                        "source": source,
                        "source_path": path,
                        "citation_ownership": (
                            "explicit"
                            if citation in owned_citations
                            else "inherited_single_source"
                        ),
                    }
                )
    # Une citation ne se propage vers les enfants que si le nœud courant en
    # possède exactement une. Les blocs de synthèse multi-sources doivent
    # fournir une citation atomique sur chaque enfant exploitable.
    child_inheritance = citations if len(citations) == 1 else []
    for key, child in node.items():
        if isinstance(child, (dict, list)):
            _collect_units_from_node(
                child,
                inherited_citations=child_inheritance,
                source=source,
                output=output,
                seen=seen,
                path=f"{path}.{key}",
                depth=depth + 1,
            )


def extract_evidence_units(
    reasoning_payload: Dict[str, Any],
    phase47_payload: Dict[str, Any],
    cards: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    allowed = {card["citation_label"] for card in cards}
    output: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()
    _collect_units_from_node(
        reasoning_payload,
        source="scientific_reasoning",
        output=output,
        seen=seen,
        path="scientific_reasoning",
    )
    _collect_units_from_node(
        phase47_payload.get("project_specific_method_story_units") or [],
        source="scientific_narrative",
        output=output,
        seen=seen,
        path="scientific_narrative.project_specific_method_story_units",
    )
    for card in cards:
        _collect_units_from_node(
            card,
            inherited_citations=[card["citation_label"]],
            source="article_card",
            output=output,
            seen=seen,
            path=f"article_card.{card['citation_label']}",
        )
        if not any(unit["citation_label"] == card["citation_label"] for unit in output):
            abstract = clean_text(
                card.get("abstract")
                or card.get("summary")
                or card.get("resume")
                or card.get("tldr"),
                1800,
            )
            for sentence in _sentences(abstract)[:3]:
                key = (card["citation_label"], "contribution", sentence.casefold())
                if key not in seen:
                    seen.add(key)
                    output.append(
                        {
                            "evidence_id": hashlib.sha256(
                                (
                                    f"{card['citation_label']}|contribution|"
                                    f"{sentence.casefold()}|article_card|abstract"
                                ).encode("utf-8")
                            ).hexdigest()[:20],
                            "citation_label": card["citation_label"],
                            "kind": "contribution",
                            "text": sentence,
                            "verrou_ids": [],
                            "source": "article_card",
                            "source_path": (
                                f"article_card.{card['citation_label']}.abstract"
                            ),
                            "citation_ownership": "explicit",
                        }
                    )
    card_by_citation = {
        card["citation_label"]: card
        for card in cards
        if card.get("citation_label")
    }
    normalized: List[Dict[str, Any]] = []
    for unit in output:
        if unit["citation_label"] not in allowed or not unit["text"]:
            continue
        card = card_by_citation.get(unit["citation_label"]) or {}
        unit["source_kind"] = (
            card.get("candidate_kind")
            or (
                "scientific_article"
                if not card.get("guided_research_source")
                else "supplemental_source"
            )
        )
        unit["evidence_scope"] = list(card.get("source_scope") or [])
        unit["documentation_scope_only"] = bool(
            card.get("documentation_scope_only")
        )
        unit["article_title"] = clean_sentence(card.get("title"), 700)
        unit["article_method_name"] = clean_sentence(
            card.get("method_name"),
            300,
        )
        normalized.append(unit)
    return normalized


# ---------------------------------------------------------------------------
# Plan global : Phase 4.7 + grands titres consultant
# ---------------------------------------------------------------------------

def _phase47_axes(phase47: Dict[str, Any]) -> List[Dict[str, Any]]:
    axes = phase47.get("project_specific_story_axes")
    if not isinstance(axes, list):
        return []
    output: List[Dict[str, Any]] = []
    for index, axis in enumerate(axes, 1):
        if not isinstance(axis, dict):
            continue
        title = clean_sentence(
            axis.get("visible_title")
            or axis.get("title")
            or axis.get("axis_title")
            or axis.get("label"),
            500,
        )
        if not title:
            continue
        output.append(
            {
                "axis_id": clean_text(axis.get("axis_id") or axis.get("id"), 120) or f"axis_{index}",
                "title": title,
                "objective": clean_sentence(
                    axis.get("goal")
                    or axis.get("objective")
                    or axis.get("narrative_goal")
                    or axis.get("description"),
                    1200,
                ),
                "citations": citations_from_obj(axis),
                "raw": axis,
            }
        )
    return output


def _phase47_verrous(phase47: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = extract_verrou_items(phase47)
    output: List[Dict[str, Any]] = []
    sections = phase47.get("verrou_sections_for_phase5")
    section_by_id = {
        clean_text(item.get("verrou_id"), 120): item
        for item in sections or []
        if isinstance(item, dict) and clean_text(item.get("verrou_id"), 120)
    }
    for item in items:
        verrou_id = clean_text(item.get("verrou_id") or item.get("id"), 120)
        title = clean_sentence(
            item.get("verrou_title")
            or item.get("title")
            or item.get("visible_title_suggestion"),
            700,
        )
        if not verrou_id or not title:
            raise ContractError(
                "invalid_phase47_verrou",
                "La Phase 4.7 contient un verrou sans identifiant ou titre.",
            )
        detail = section_by_id.get(verrou_id, {})
        citation_coverage = (
            detail.get("citation_coverage")
            if isinstance(detail.get("citation_coverage"), Mapping)
            else {}
        )
        direct_citations = citation_sort(
            citation_coverage.get("direct_citations") or []
        )
        related_citations = citation_sort(
            citation_coverage.get("related_citations") or []
        )
        methodological_citations = citation_sort(
            citation_coverage.get("methodological_citations") or []
        )
        background_citations = citation_sort(
            citation_coverage.get("background_citations") or []
        )
        if direct_citations:
            evidence_status = "directly_supported"
        elif (
            related_citations
            or methodological_citations
            or background_citations
        ):
            evidence_status = "insufficient_direct_evidence"
        else:
            evidence_status = "no_scientific_evidence"
        output.append(
            {
                "verrou_id": verrou_id,
                "verrou_title": title,
                # Une citation connexe ne devient jamais obligatoire comme
                # preuve directe. La Phase 5 pourra la mobiliser uniquement
                # pour cadrer la non-transposabilité.
                "required_citations": [],
                "direct_citations": direct_citations,
                "related_citations": related_citations,
                "methodological_citations": methodological_citations,
                "background_citations": background_citations,
                "available_context_citations": citation_sort(
                    [
                        *direct_citations,
                        *related_citations,
                        *methodological_citations,
                        *background_citations,
                    ]
                ),
                "evidence_status": evidence_status,
                "requires_insufficiency_disclosure": (
                    evidence_status != "directly_supported"
                ),
                "context": clean_sentence(
                    detail.get("project_problem")
                    or detail.get("state_of_art_gap")
                    or detail.get("rd_gap"),
                    1600,
                ),
            }
        )
    return output


def _confirmed_contract_from_payload(
    payload: Dict[str, Any],
    *,
    source_name: str,
    source_path: str | Path,
    aliases: Any = None,
) -> Dict[str, Any]:
    """Construit un contrat en indiquant précisément l'artefact fautif.

    Les anciens payloads réels ne partagent pas tous le même nom de clé. Le
    lecteur de contrat gère leurs clés explicites, mais il reste utile de
    signaler la source exacte lorsqu'aucune collection de verrous n'est
    disponible.
    """

    items = extract_verrou_items(payload)
    if not items:
        raise ContractError(
            "no_confirmed_verrous",
            f"Aucun verrou confirmé n'a été trouvé dans {source_name}.",
            {
                "source": source_name,
                "path": str(source_path),
                "top_level_keys": sorted(str(key) for key in payload.keys())[:80],
            },
        )
    return build_confirmed_contract(
        {"verrous": items},
        aliases=aliases,
        source_path=str(source_path),
    )


def _declared_phase47_verrou_ids(value: Any) -> Set[str]:
    """Collecte uniquement les références explicites aux verrous de Phase 4.7."""

    output: Set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "verrou_id":
                verrou_id = clean_text(child, 120)
                if verrou_id:
                    output.add(verrou_id)
                continue
            if key in {
                "target_verrous",
                "verrou_ids",
                "linked_verrou_ids",
                "canonical_verrous",
            }:
                for item in as_list(child):
                    if isinstance(item, Mapping):
                        verrou_id = clean_text(
                            item.get("verrou_id")
                            or item.get("id")
                            or item.get("lock_id"),
                            120,
                        )
                    else:
                        verrou_id = clean_text(item, 120)
                    if verrou_id:
                        output.add(verrou_id)
                continue
            if isinstance(child, (Mapping, list, tuple)):
                output.update(_declared_phase47_verrou_ids(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            output.update(_declared_phase47_verrou_ids(child))
    return output


def _normalize_declared_ids(
    declared_ids: Iterable[str],
    canonical_ids: Set[str],
    aliases: Any = None,
) -> Set[str]:
    alias_map = parse_aliases(aliases)
    output: Set[str] = set()
    for raw_id in declared_ids:
        verrou_id = alias_map.get(clean_text(raw_id, 120), clean_text(raw_id, 120))
        if verrou_id not in canonical_ids and verrou_id.lower().startswith("verrou_"):
            possible = verrou_id[len("verrou_") :]
            verrou_id = alias_map.get(possible, possible)
        output.add(verrou_id)
    return output


def _scientific_plan_from_phase47(
    phase47: Dict[str, Any],
    verrous: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    axes = _phase47_axes(phase47)
    if axes:
        return [
            {
                "section_id": axis["axis_id"],
                "order": index,
                "title": axis["title"],
                "objective": axis["objective"],
                "verrou_ids": [],
                "phase47_axes": [axis],
            }
            for index, axis in enumerate(axes, 1)
        ]
    return [
        {
            "section_id": "synthese_scientifique_globale",
            "order": 1,
            "title": "Synthèse scientifique de la littérature sélectionnée",
            "objective": "Présenter les connaissances, résultats et limites documentés par les sources sélectionnées.",
            "verrou_ids": [item["verrou_id"] for item in verrous],
            "phase47_axes": [],
        }
    ]


def _assign_to_sections(
    sections: List[Dict[str, Any]],
    axes: List[Dict[str, Any]],
    verrous: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    output = [
        {
            **section,
            "phase47_axes": list(section.get("phase47_axes") or []),
            "verrous": [],
            "required_citations": citation_sort(section.get("required_citations") or []),
            "suggested_citations": citation_sort(
                section.get("suggested_citations") or []
            ),
        }
        for section in sections
    ]
    if not output:
        raise ContractError("empty_final_plan", "Le plan final ne contient aucune section.")

    for axis in axes:
        best = max(
            range(len(output)),
            key=lambda index: _similarity(
                f"{axis['title']} {axis['objective']}",
                f"{output[index]['title']} {output[index]['objective']}",
            ),
        )
        if axis not in output[best]["phase47_axes"]:
            output[best]["phase47_axes"].append(axis)
        output[best]["suggested_citations"] = citation_sort(
            output[best]["suggested_citations"] + axis["citations"]
        )

    for verrou in verrous:
        explicitly_linked = [
            index
            for index, section in enumerate(output)
            if verrou["verrou_id"] in set(section.get("verrou_ids") or [])
        ]
        if explicitly_linked:
            best = explicitly_linked[0]
        else:
            best = max(
                range(len(output)),
                key=lambda index: _similarity(
                    f"{verrou['verrou_title']} {verrou['context']}",
                    " ".join(
                        [
                            output[index]["title"],
                            output[index]["objective"],
                            " ".join(axis["title"] for axis in output[index]["phase47_axes"]),
                        ]
                    ),
                ),
            )
        output[best]["verrous"].append(verrou)
        output[best]["suggested_citations"] = citation_sort(
            output[best]["suggested_citations"]
            + verrou.get("available_context_citations", [])
        )
    return output


def build_unified_blueprint(
    *,
    organisme: str,
    project: str,
    year: str,
    reasoning_payload: Dict[str, Any],
    phase46_payload: Dict[str, Any],
    phase47_payload: Dict[str, Any],
    project_context: Dict[str, Any],
    style_memory: Dict[str, Any],
    article_cards: List[Dict[str, Any]],
    evidence_units: List[Dict[str, Any]],
    approved_plan: Optional[List[Dict[str, Any]]] = None,
    aliases: Any = None,
    require_all_selected_sources: bool = False,
) -> Dict[str, Any]:
    del reasoning_payload, phase46_payload
    verrous = _phase47_verrous(phase47_payload)
    contract = build_confirmed_contract(
        {"verrous": verrous},
        aliases=aliases,
        source_path="Phase 4.7",
    )
    axes = _phase47_axes(phase47_payload)
    base_sections = (
        [
            {
                **section,
                "phase47_axes": [],
            }
            for section in approved_plan
        ]
        if approved_plan
        else _scientific_plan_from_phase47(phase47_payload, verrous)
    )
    canonical_ids = {item["verrou_id"] for item in contract["verrous"]}
    declared_ids = {
        clean_text(verrou_id, 120)
        for section in base_sections
        for verrou_id in section.get("verrou_ids") or []
        if clean_text(verrou_id, 120)
    }
    unknown_ids = sorted(declared_ids - canonical_ids)
    if unknown_ids:
        raise ContractError(
            "consultant_plan_unknown_verrou",
            "Le plan consultant référence un verrou qui n'appartient pas au contrat confirmé.",
            {"unknown_verrou_ids": unknown_ids},
        )
    sections = _assign_to_sections(base_sections, axes, verrous)
    allowed = {card["citation_label"] for card in article_cards}
    available = {unit["citation_label"] for unit in evidence_units}
    by_citation = _units_by_citation(evidence_units)
    citable = citation_sort(available & allowed)
    card_by_citation = {
        clean_text(card.get("citation_label"), 40): card
        for card in article_cards
        if clean_text(card.get("citation_label"), 40)
    }
    citation_evidence_text = {
        citation: " ".join(
            clean_text(unit.get("text"), 1400)
            for unit in by_citation.get(citation, [])
        )
        for citation in citable
    }

    # Une même source peut soutenir une définition, une méthode, une
    # comparaison puis une limite. Elle n'est plus partitionnée de manière
    # exclusive entre les sections du plan.
    section_texts = [
        " ".join(
            [
                clean_text(section.get("title"), 700),
                clean_text(section.get("objective"), 1800),
                clean_text(section.get("instructions") or [], 1800),
                clean_text(section.get("required_dimensions") or [], 800),
                " ".join(
                    clean_text(axis.get("title"), 700)
                    for axis in section.get("phase47_axes") or []
                    if isinstance(axis, dict)
                ),
            ]
        )
        for section in sections
    ]
    evidence_roles_by_verrou: Dict[str, Dict[str, Any]] = {}
    for section in sections:
        section["available_citations"] = list(citable)
        section["required_citations"] = citation_sort(
            set(section.get("required_citations") or []) & set(citable)
        )
        for verrou in section.get("verrous") or []:
            if not isinstance(verrou, dict):
                continue
            verrou_id = clean_text(verrou.get("verrou_id"), 120)
            direct = citation_sort(
                set(verrou.get("direct_citations") or []) & set(citable)
            )
            related = citation_sort(
                set(verrou.get("related_citations") or []) & set(citable)
            )
            methodological = citation_sort(
                set(verrou.get("methodological_citations") or [])
                & set(citable)
            )
            background = citation_sort(
                set(verrou.get("background_citations") or []) & set(citable)
            )
            if direct:
                verrou_text = (
                    f"{verrou.get('verrou_title')} {verrou.get('context')}"
                )
                chosen = max(
                    direct,
                    key=lambda citation: (
                        _similarity(
                            verrou_text,
                            citation_evidence_text.get(citation, ""),
                        ),
                        sum(
                            verrou_id
                            in {
                                clean_text(value, 120)
                                for value in as_list(
                                    unit.get("verrou_ids") or []
                                )
                            }
                            for unit in by_citation.get(citation, [])
                        ),
                        citation,
                    ),
                )
                verrou["required_citations"] = [chosen]
                section["required_citations"] = citation_sort(
                    [*section["required_citations"], chosen]
                )
                verrou["evidence_status"] = "directly_supported"
                verrou["requires_insufficiency_disclosure"] = False
            else:
                verrou["required_citations"] = []
                verrou["evidence_status"] = (
                    "insufficient_direct_evidence"
                    if related or methodological or background
                    else "no_scientific_evidence"
                )
                verrou["requires_insufficiency_disclosure"] = True
            verrou["direct_citations"] = direct
            verrou["related_citations"] = related
            verrou["methodological_citations"] = methodological
            verrou["background_citations"] = background
            verrou["available_context_citations"] = citation_sort(
                [*direct, *related, *methodological, *background]
            )
            evidence_roles_by_verrou[verrou_id] = {
                "evidence_status": verrou["evidence_status"],
                "direct_citations": direct,
                "related_citations": related,
                "methodological_citations": methodological,
                "background_citations": background,
                "requires_insufficiency_disclosure": verrou[
                    "requires_insufficiency_disclosure"
                ],
            }

    # Un nombre de sources explicitement demandé constitue un corpus exhaustif.
    # Chaque source est obligatoire dans sa section la plus pertinente, tout en
    # restant disponible dans toutes les autres.
    if require_all_selected_sources and citable:
        required_load = [
            len(section.get("required_citations") or [])
            for section in sections
        ]
        for citation in citable:
            if any(
                citation in set(section.get("required_citations") or [])
                for section in sections
            ):
                continue
            card = card_by_citation.get(citation) or {}
            requested_section_ids = {
                clean_text(value, 120)
                for value in as_list(card.get("section_ids") or [])
                if clean_text(value, 120)
            }
            best_index = max(
                range(len(sections)),
                key=lambda index: (
                    _similarity(
                        citation_evidence_text.get(citation, ""),
                        section_texts[index],
                    )
                    + (
                        2.0
                        if clean_text(
                            sections[index].get("section_id"),
                            120,
                        )
                        in requested_section_ids
                        else 0.0
                    )
                    + (
                        0.05
                        if citation
                        in set(
                            sections[index].get("suggested_citations") or []
                        )
                        else 0.0
                    )
                    - required_load[index] * 0.12,
                    -index,
                ),
            )
            sections[best_index]["required_citations"] = citation_sort(
                [
                    *sections[best_index]["required_citations"],
                    citation,
                ]
            )
            required_load[best_index] += 1

    required_by_verrou: Dict[str, List[str]] = {}
    for section in sections:
        for verrou in section.get("verrous") or []:
            if not isinstance(verrou, dict):
                continue
            verrou_id = clean_text(verrou.get("verrou_id"), 120)
            if verrou_id:
                required_by_verrou[verrou_id] = citation_sort(
                    verrou.get("required_citations") or []
                )
    source_roles: Dict[str, str] = {}
    for citation in citable:
        card = card_by_citation.get(citation) or {}
        explicit_role = clean_text(
            card.get("consultant_evidence_role"),
            120,
        )
        if explicit_role:
            source_roles[citation] = explicit_role
            continue
        if card.get("documentation_scope_only"):
            source_roles[citation] = "supplemental_context"
            continue
        # « direct » ou « connexe » est une relation entre une source et un
        # verrou précis, jamais une propriété globale de l'article. Les rôles
        # fins restent dans evidence_roles_by_verrou et dans chaque
        # sous-section; le rôle global décrit seulement la nature de la source.
        source_roles[citation] = "scientific_source"
    global_required = citation_sort(
        citation
        for section in sections
        for citation in section["required_citations"]
    )
    return {
        "blueprint_type": "canonical_global_evidence_first_v1",
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "project_context": project_context,
        "style_memory": style_memory,
        "plan_source": "consultant_approved" if approved_plan else "phase_4_7",
        "sections": sections,
        "verrous": verrous,
        "verrou_fingerprint": contract["verrou_fingerprint"],
        "allowed_citations": citation_sort(allowed),
        "available_evidence_citations": citation_sort(available),
        "required_citations": global_required,
        "required_citations_by_verrou": required_by_verrou,
        "source_roles": source_roles,
        "evidence_roles_by_verrou": evidence_roles_by_verrou,
        "require_all_selected_sources": bool(
            require_all_selected_sources
        ),
        "rules": {
            "single_global_document": True,
            "consultant_plan_is_canonical_when_approved": bool(approved_plan),
            "phase47_story_is_advisory": True,
            "phase47_never_changes_consultant_headings_order_or_instructions": True,
            "consultant_plan_changes_structure_not_evidence_ownership": True,
            "all_verrous_must_appear": True,
            "article_cards_are_only_citable_sources": True,
            "citations_are_reusable_across_sections": True,
            "related_evidence_never_becomes_direct_by_assignment": True,
            "absence_of_direct_evidence_must_be_disclosed": True,
            "all_selected_sources_required_when_exact_count_requested": bool(
                require_all_selected_sources
            ),
            "no_default_citations": True,
            "no_domain_hardcoding": True,
        },
    }


# ---------------------------------------------------------------------------
# Rédaction déterministe et LLM
# ---------------------------------------------------------------------------

def _units_by_citation(units: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    output: Dict[str, List[Dict[str, Any]]] = {}
    for unit in units:
        output.setdefault(unit["citation_label"], []).append(unit)
    return output


def _evidence_sentence(unit: Dict[str, Any]) -> str:
    text = clean_sentence(unit.get("text"), 700)
    if not text:
        return ""
    text = text.rstrip(".!? ")
    return f"{text} [{unit['citation_label']}]."


def _raw_extraction_fragments(value: Any) -> List[str]:
    """Détecte les en-têtes/pieds de page OCR copiés dans la rédaction.

    Le motif ``x of y`` est indépendant d'un éditeur ou d'un domaine et
    caractérise les compteurs de pages qui contaminent parfois les preuves
    extraites. Ces fragments ne doivent jamais être injectés tels quels dans
    un livrable.
    """
    fragments: List[str] = []
    raw_value = clean_text(value, 500000)
    for paragraph in re.split(r"\n{1,}", raw_value):
        paragraph = clean_sentence(paragraph, 4000)
        if not paragraph:
            continue
        if (
            re.search(
                r"\b(?:page\s*)?\d{1,4}\s+of\s+\d{1,4}\b",
                paragraph,
                flags=re.I,
            )
            or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", paragraph)
        ):
            fragments.append(paragraph)
    return unique(fragments)


def _non_french_raw_fragments(value: Any) -> List[str]:
    """Repère les extraits anglais copiés d'une Article Card.

    Le contrôle s'appuie sur des mots fonctionnels génériques et sur les
    formulations éditoriales typiques d'un article. Les noms de méthodes et le
    vocabulaire scientifique anglais isolé ne suffisent jamais à déclencher le
    signal.
    """
    english_words = {
        "the", "this", "that", "these", "those", "with", "from", "into",
        "where", "which", "while", "through", "using", "used", "only",
        "also", "between", "their", "our", "we", "is", "are", "was",
        "were", "has", "have", "can", "results", "paper", "section",
    }
    french_words = {
        "le", "la", "les", "un", "une", "des", "du", "de", "dans",
        "avec", "pour", "par", "sur", "qui", "que", "dont", "cette",
        "ces", "est", "sont", "nous", "notre", "résultats", "section",
    }
    raw_markers = re.compile(
        r"\b(?:this paper|in this (?:paper|work|part|section)|we (?:propose|"
        r"present|demonstrate|show)|our approach|the proposed (?:method|"
        r"approach)|section\s+\d+(?:\.\d+)*\s+(?:introduces|presents))\b",
        flags=re.I,
    )
    fragments: List[str] = []
    for paragraph in re.split(r"\n{1,}", clean_text(value, 500000)):
        paragraph = clean_sentence(paragraph, 6000)
        if not paragraph:
            continue
        words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ']+", paragraph.casefold())
        english_count = sum(word in english_words for word in words)
        french_count = sum(word in french_words for word in words)
        if raw_markers.search(paragraph) or (
            english_count >= 8
            and english_count >= max(8, french_count * 2)
        ):
            fragments.append(paragraph)
    return unique(fragments)


def _prompt_evidence_text(value: Any, limit: int = 850) -> str:
    """Nettoie uniquement la copie envoyée au LLM, jamais la preuve archivée."""
    text = clean_text(value, max(limit * 2, 2000))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    text = re.sub(r"\b(?:page\s*)?\d{1,4}\s+of\s+\d{1,4}\b", " ", text, flags=re.I)
    return clean_sentence(text, limit)


def build_deterministic_unified_draft(
    blueprint: Dict[str, Any],
    cards: List[Dict[str, Any]],
    evidence_units: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    del cards
    units = evidence_units or blueprint.get("evidence_units") or []
    by_citation = _units_by_citation(units)
    sections_out: List[Dict[str, Any]] = []
    used: Set[str] = set()

    for section in blueprint["sections"]:
        citations = citation_sort(
            section.get("required_citations")
            or section.get("suggested_citations")
            or (section.get("available_citations") or [])[:4]
        )
        section_units = [
            unit
            for citation in citations
            for unit in by_citation.get(citation, [])[:2]
        ]
        content_sentences: List[str] = []
        seen_text: Set[str] = set()
        for unit in section_units:
            sentence = _evidence_sentence(unit)
            key = re.sub(r"\[(A\d+)\]", "", sentence).casefold()
            if not sentence or key in seen_text:
                continue
            seen_text.add(key)
            content_sentences.append(sentence)
            used.add(unit["citation_label"])

        subsection_rows: List[Dict[str, Any]] = []
        for verrou in section.get("verrous") or []:
            verrou_citations = citation_sort(
                verrou.get("required_citations")
                or verrou.get("related_citations")
                or verrou.get("methodological_citations")
                or verrou.get("background_citations")
                or []
            )
            local_units = [
                unit
                for citation in verrou_citations
                for unit in by_citation.get(citation, [])[:2]
                if not unit.get("verrou_ids")
                or verrou["verrou_id"] in unit.get("verrou_ids", [])
            ]
            local_sentences = unique(_evidence_sentence(unit) for unit in local_units)
            used.update(unit["citation_label"] for unit in local_units)
            if verrou.get("requires_insufficiency_disclosure"):
                local_sentences.insert(
                    0,
                    (
                        "Le corpus sélectionné ne fournit aucune preuve "
                        "scientifique directe permettant de conclure sur ce "
                        "verrou; les sources ci-après sont uniquement des "
                        "éléments connexes de cadrage."
                    ),
                )
            elif not local_sentences:
                local_sentences = [
                    "Les unités de preuve disponibles ne permettent pas d’isoler une couverture scientifique propre à ce verrou."
                ]
            subsection_rows.append(
                {
                    "verrou_id": verrou["verrou_id"],
                    "title": verrou["verrou_title"],
                    "content": " ".join(local_sentences),
                }
            )

        sections_out.append(
            {
                "section_id": section["section_id"],
                "title": section["title"],
                "content": " ".join(content_sentences),
                "subsections": subsection_rows,
            }
        )
    return {
        "title": f"État de l’art scientifique — {blueprint['project']}",
        "sections": sections_out,
        "citations_used": citation_sort(used),
        "writer": "deterministic_evidence_only",
    }


def _compact_evidence(
    units: List[Dict[str, Any]],
    *,
    max_units: int = 240,
    max_per_citation: int = 14,
) -> List[Dict[str, Any]]:
    """Compacte les preuves sans favoriser les premières citations du fichier.

    L'ancien découpage ``units[:240]`` pouvait remplir tout le contexte avec
    les premières cartes et rendre invisibles les dernières. La sélection est
    désormais équilibrée par citation puis par nature de preuve.
    """
    by_citation: Dict[str, List[Dict[str, Any]]] = {}
    for unit in units:
        citation = normalize_citation_label(unit.get("citation_label"))
        if not citation:
            continue
        by_citation.setdefault(citation, []).append(unit)

    selected_by_citation: Dict[str, List[Dict[str, Any]]] = {}
    kind_order = (
        "method",
        "protocol",
        "result",
        "limitation",
        "data",
        "contribution",
    )
    for citation in citation_sort(by_citation):
        rows = by_citation[citation]
        local: List[Dict[str, Any]] = []
        seen_text: Set[str] = set()
        for kind in kind_order:
            for unit in rows:
                if clean_text(unit.get("kind"), 80) != kind:
                    continue
                text = _prompt_evidence_text(unit.get("text"), 850)
                key = text.casefold()
                if not text or key in seen_text:
                    continue
                seen_text.add(key)
                local.append(unit)
                if len(local) >= max_per_citation:
                    break
            if len(local) >= max_per_citation:
                break
        if len(local) < max_per_citation:
            for unit in rows:
                text = _prompt_evidence_text(unit.get("text"), 850)
                key = text.casefold()
                if not text or key in seen_text:
                    continue
                seen_text.add(key)
                local.append(unit)
                if len(local) >= max_per_citation:
                    break
        selected_by_citation[citation] = local

    selected: List[Dict[str, Any]] = []
    round_index = 0
    ordered_citations = citation_sort(selected_by_citation)
    while len(selected) < max_units:
        added = False
        for citation in ordered_citations:
            rows = selected_by_citation.get(citation) or []
            if round_index < len(rows):
                selected.append(rows[round_index])
                added = True
                if len(selected) >= max_units:
                    break
        if not added:
            break
        round_index += 1

    return [
        {
            "evidence_id": unit.get("evidence_id"),
            "citation": unit["citation_label"],
            "article_title": unit.get("article_title"),
            "article_method_name": unit.get("article_method_name"),
            "kind": unit["kind"],
            "text": _prompt_evidence_text(unit["text"], 850),
            "verrou_ids": unit.get("verrou_ids") or [],
            "source_kind": unit.get("source_kind") or "scientific_article",
            "evidence_scope": unit.get("evidence_scope") or [],
            "documentation_scope_only": bool(
                unit.get("documentation_scope_only")
            ),
            "citation_ownership": unit.get("citation_ownership"),
        }
        for unit in selected
    ]


_GENERIC_SENTENCE_STARTS = {
    "Ainsi",
    "Enfin",
    "Cependant",
    "Toutefois",
    "Néanmoins",
    "Par",
    "Dans",
    "Cette",
    "Ces",
    "Les",
    "Le",
    "La",
    "Un",
    "Une",
    "Pour",
    "En",
    "D'autre",
    "D’une",
    "D'un",
    "Il",
    "Elle",
    "On",
    "Chaque",
    "Plus",
    "Plusieurs",
    "Notamment",
    "Premièrement",
    "Deuxièmement",
    "Troisièmement",
    "Quatrièmement",
    "Cela",
    "Ceci",
    "D'abord",
    "Parmi",
    "Malgré",
    "Parallèlement",
    "Inversement",
    "Simultanément",
    "Globalement",
    "First",
    "Next",
    "Then",
    "Due",
    "Using",
    "Among",
    "According",
    "These",
    "This",
    "The",
}
_GENERIC_NAMED_TERMS = {
    "Figure",
    "Table",
    "Section",
    "Introduction",
    "Conclusion",
    "État",
    "Art",
    "Projet",
    "Objectif",
}


def _semantic_normalize(value: Any) -> str:
    text = unicodedata.normalize(
        "NFKD",
        # Le contrôle peut agréger plusieurs cartes très riches. Une limite
        # trop courte supprimait silencieusement les dernières citations et
        # créait des faux « méthode absente » selon l'ordre des articles.
        clean_text(value, 2000000),
    )
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _acronym_is_expanded_in_evidence(
    acronym: str,
    normalized_evidence: str,
) -> bool:
    """Reconnaît un sigle à partir de son développement, sans lexique métier."""
    letters = re.sub(r"[^A-Za-z]", "", acronym).casefold()
    if not 2 <= len(letters) <= 8:
        return False
    words = [
        word
        for word in normalized_evidence.split()
        if word and not word.isdigit()
    ]
    size = len(letters)
    return any(
        "".join(word[0] for word in words[index:index + size])
        == letters
        for index in range(0, max(0, len(words) - size + 1))
    )


def _entity_expansion_is_acronym_in_evidence(
    entity: str,
    normalized_evidence: str,
) -> bool:
    """Reconnaît le développement d'un sigle déjà présent dans la preuve.

    Le contrôle doit être symétrique : une preuve peut écrire ``RWG`` alors que
    la rédaction emploie ``Rao-Wilton-Glisson``. La règle reste générique et ne
    contient aucun lexique métier ; elle ne valide que les initiales exactes
    d'une expression composée.
    """
    words = re.findall(
        r"[A-Za-zÀ-ÖØ-öø-ÿ]+",
        clean_text(entity, 200),
    )
    if not 2 <= len(words) <= 8:
        return False
    acronym = "".join(word[0] for word in words).casefold()
    if not 2 <= len(acronym) <= 8:
        return False
    return acronym in normalized_evidence.split()


def _claim_sentences(value: Any) -> List[str]:
    text = clean_text(value, 300000)
    # Certaines sorties placent la citation après le point
    # (« résultat. [A1] Phrase suivante »). La citation soutient alors la
    # phrase précédente : on la replace avant la ponctuation avant découpage.
    text = re.sub(
        r"([.!?])\s*(\[[^\[\]]*\bA\s*\d+\b[^\[\]]*\])",
        r" \2\1",
        text,
        flags=re.I,
    )
    return [
        clean_sentence(part, 4000)
        for part in re.split(r"(?<=[.!?])\s+|\n+", text)
        if len(clean_sentence(part, 4000)) >= 18
    ]


def _salient_entities(sentence: str) -> List[str]:
    without_citations = _strip_citation_groups(sentence)
    # Un développement explicite placé entre parenthèses ne constitue pas une
    # nouvelle méthode à attribuer séparément. Exemple générique :
    # « nom local (Computer-Aided Design, CAD) ». On conserve le sigle pour le
    # contrôle d'entité et on retire seulement les mots de son développement.
    def keep_explicit_acronym(match: re.Match[str]) -> str:
        expansion = match.group(1)
        acronym = match.group(2)
        expansion_words = re.findall(
            r"[A-Za-zÀ-ÖØ-öø-ÿ]+",
            expansion,
        )
        initials = "".join(word[0] for word in expansion_words).casefold()
        letters = re.sub(r"[^A-Za-z]", "", acronym).casefold()
        if initials == letters:
            return f"({acronym})"
        return match.group(0)

    without_citations = re.sub(
        r"\(([^()]{2,100}?)(?:,\s*|\s+)([A-Z]{2,8})\)",
        keep_explicit_acronym,
        without_citations,
    )
    acronym_prefix_terms: set[str] = set()
    for acronym_match in re.finditer(
        r"\(([A-Z]{2,8})\)",
        without_citations,
    ):
        # Les traductions ou développements peuvent précéder le sigle
        # (« nom scientifique développé (ABC) »). Le sigle reste contrôlé,
        # mais ses mots capitalisés ne sont pas interprétés comme autant de
        # méthodes indépendantes.
        prefix = without_citations[
            max(0, acronym_match.start() - 100):acronym_match.start()
        ]
        prefix = re.split(r"[.;:!?]", prefix)[-1]
        acronym_prefix_terms.update(
            re.findall(
                r"\b[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ]{2,}"
                r"(?:-[A-Za-zÀ-ÖØ-öø-ÿ]+)*\b",
                prefix,
            )
        )
    leading_word_match = re.match(
        r"\s*([A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ-]*)\b",
        without_citations,
    )
    leading_word = (
        leading_word_match.group(1)
        if leading_word_match
        else ""
    )
    candidates: List[str] = []
    candidates.extend(
        re.findall(
            r"\b(?:[A-Z]{2,}[A-Z0-9-]*|"
            r"[A-Z][A-Za-z]+(?:[A-Z][A-Za-z0-9-]*)+|"
            r"[A-Za-z]+(?:Net|CNN|GAN|ViT)\d*[A-Za-z0-9-]*|"
            r"[A-Z][a-z]{2,}\d+[A-Za-z0-9-]*)\b",
            without_citations,
        )
    )
    words = re.findall(
        r"\b[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ]{2,}"
        r"(?:-[A-Za-zÀ-ÖØ-öø-ÿ]+)*\b",
        without_citations,
    )
    for index, word in enumerate(words):
        if word in _GENERIC_NAMED_TERMS:
            continue
        if word in acronym_prefix_terms:
            continue
        # Un mot commun mis en capitales pour l'emphase n'est pas une entité
        # scientifique. Les vrais sigles ASCII ont déjà été capturés par
        # l'expression spécialisée ci-dessus.
        if word.isupper():
            continue
        # Un mot simplement capitalisé en tête de phrase est le plus souvent
        # un connecteur ou un verbe, pas une entité scientifique. Les sigles,
        # CamelCase et noms avec chiffres sont déjà capturés par l'expression
        # spécialisée ci-dessus.
        if index == 0 and word == leading_word:
            continue
        # Les noms propres techniques d'un seul mot (Salsa, Adam, Mie, etc.)
        # sont utiles pour détecter une attribution à la mauvaise source.
        candidates.append(word)
    return unique(
        candidate
        for candidate in candidates
        if candidate not in _GENERIC_SENTENCE_STARTS
        and candidate not in _GENERIC_NAMED_TERMS
    )


def _numeric_claim_tokens(sentence: str) -> List[str]:
    text = _strip_citation_groups(sentence)
    # Les indices intégrés aux noms de modèles (VGG-11, ResNet18, etc.) sont
    # contrôlés comme entités et non comme résultats numériques.
    text = re.sub(
        r"\b[A-Za-z][A-Za-z-]*\d+[A-Za-z0-9-]*\b",
        " ",
        text,
    )
    values = re.findall(
        r"(?<![A-Za-z])(?:"
        r"\d+(?:[.,]\d+)?\s*(?:%|°|×|x\d+|"
        r"époques?|epochs?|fold|GHz|MHz|kHz|Hz|GB|MB|"
        r"ms|s|images?|classes?|paramètres?)"
        r"|\d+[.,]\d+"
        r"|\d{2,}"
        r")",
        text,
        flags=re.I,
    )
    return unique(value.strip() for value in values)


def _semantic_claim_audit(
    generated: Mapping[str, Any],
    section: Mapping[str, Any],
    evidence_units: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Contrôle déterministe des attributions avant le juge LLM.

    Ce garde-fou ne cherche pas à résoudre tout l'entailment scientifique. Il
    bloque les erreurs à fort signal : entité/méthode absente des sources
    citées, valeur numérique absente et verrou sans preuve directe présenté
    sans avertissement explicite.
    """
    guided_conversation = bool(section.get("_guided_conversation"))
    evidence_by_citation: Dict[str, str] = {}
    for unit in evidence_units:
        citation = normalize_citation_label(unit.get("citation_label"))
        if not citation:
            continue
        evidence_by_citation[citation] = (
            evidence_by_citation.get(citation, "")
            + " "
            + clean_text(unit.get("article_title"), 1000)
            + " "
            + clean_text(unit.get("article_method_name"), 500)
            + " "
            + clean_text(unit.get("text"), 3000)
        )

    bodies: List[Tuple[str, str]] = [
        ("section", clean_text(generated.get("content"), 200000))
    ]
    generated_subsections = {
        clean_text(item.get("verrou_id"), 120): item
        for item in generated.get("subsections") or []
        if isinstance(item, Mapping)
    }
    for verrou in section.get("verrous") or []:
        if not isinstance(verrou, Mapping):
            continue
        verrou_id = clean_text(verrou.get("verrou_id"), 120)
        subsection = generated_subsections.get(verrou_id) or {}
        bodies.append(
            (
                f"verrou:{verrou_id}",
                clean_text(subsection.get("content"), 200000),
            )
        )

    entity_mismatches: List[Dict[str, Any]] = []
    numeric_mismatches: List[Dict[str, Any]] = []
    uncited_scientific_claims: List[Dict[str, Any]] = []
    scientific_signal = re.compile(
        r"\b(?:"
        r"méthod|modele|modèle|algorith|approche|donnée|data|"
        r"simulation|simulateur|solveur|réseau|apprentissage|"
        r"résultat|performance|précision|exactitude|erreur|"
        r"complexité|validation|généralis|robust|représent|"
        r"rayon|électromagn|classification|augmentation"
        r")",
        flags=re.I,
    )
    disclosure_signal = re.compile(
        r"(?:aucune|absence de|sans)\s+(?:preuve|source|validation)"
        r"(?:\s+scientifique)?\s+directe"
        r"|corpus[^.]{0,120}\b(?:insuffisant|ne\s+(?:permet|suffit|démontre|établit)\s+pas)"
        r"|sources?\s+connexes?"
        r"|(?:ne\s+|n['’])(?:fournit|fournissent|apporte|apportent|"
        r"permet|permettent|démontre|démontrent|établit|établissent)"
        r"\s+(?:donc\s+)?pas(?:\s+de)?(?:\s+preuves?\s+directes?)?"
        r"|rien\s+ne\s+(?:permet|autorise)\s+(?:donc\s+)?"
        r"(?:de|d['’])\b"
        r"|n['’]est\s+(?:donc\s+)?pas\s+possible\s+(?:de|d['’])\b"
        r"|(?:manque|défaut)\s+de\s+preuves?\s+directes?"
        r"|insuffisance(?:\s+\w+){0,4}\s+de\s+preuves?\s+directes?"
        r"|aucune\s+(?:publication|source|article|travail)[^.]{0,140}"
        r"n['’]?(?:établit|démontre|documente|valide|fournit)\b"
        r"|aucune\s+preuve[^.]{0,100}\bn['’](?:établit|démontre|"
        r"documente|valide)\b"
        r"|(?:ne\s+|n['’])(?:apporte|présente|fournit)\s+"
        r"aucune\s+preuve\b"
        r"|(?:ne\s+|n['’])(?:fournit|fournissent|apporte|apportent|"
        r"aborde|abordent)\s+(?:aucun(?:e)?|ni)\b"
        r"|(?:ne\s+|n['’])(?:décrit|décrivent|documente|documentent|"
        r"traite|traitent|étudie|étudient|évalue|évaluent|vise|visent)"
        r"\s+pas(?:\s+explicitement)?\b"
        r"|sans\s+(?:pour\s+autant\s+)?(?:viser|traiter|étudier|évaluer|"
        r"valider|documenter)\s+(?:explicitement\s+)?\b"
        r"|reste(?:nt)?\s+à\s+(?:explorer|établir|démontrer|documenter|"
        r"évaluer|valider)\b"
        r"|n['’]est\s+pas\s+(?:(?:explicitement|formellement)\s+)?"
        r"(?:établi|établie|démontré|démontrée|documenté|documentée|"
        r"évalué|évaluée|validé|validée)\b"
        r"|reste(?:nt)?\s+(?:ouvert|ouverte|ouverts|ouvertes|incertain|"
        r"incertaine|incertains|incertaines|non\s+(?:établi|établie|"
        r"démontré|démontrée|documenté|documentée|validé|validée))\b",
        flags=re.I,
    )
    for location, body in bodies:
        paragraphs = [
            paragraph
            for paragraph in re.split(r"\n{2,}", body)
            if clean_text(paragraph)
        ]
        for paragraph in paragraphs:
            paragraph_citations = citations_from_text(paragraph)
            for sentence in _claim_sentences(paragraph):
                sentence_citations = citations_from_text(sentence)
                # En rédaction académique, une citation placée dans le même
                # paragraphe peut soutenir plusieurs phrases consécutives du
                # même raisonnement. Le vérificateur indépendant contrôle
                # ensuite que ce rattachement de paragraphe est légitime.
                citations = (
                    sentence_citations
                    if sentence_citations
                    else paragraph_citations
                )
                if not citations:
                    lower = sentence.casefold()
                    non_evidentiary_question = sentence.rstrip().endswith("?")
                    project_intent_statement = (
                        "projet" in lower
                        and bool(
                            re.search(
                                r"\b(?:vise|devra|objectif|question|"
                                r"incertitude|enjeu|travaux?\s+à\s+mener)\b",
                                lower,
                            )
                        )
                    )
                    document_navigation_statement = bool(
                        re.search(
                            r"\b(?:suite de cet état de l['’]art|"
                            r"état de l['’]art[^.]{0,160}\bstructur|"
                            r"étude des approches[^.]{0,160}\b"
                            r"familles?\s+méthodologiques?|"
                            r"section suivante|dans la suite|"
                            r"sera présenté|seront présentés)\b",
                            lower,
                        )
                    )
                    project_context_statement = (
                        "projet" in lower
                        and not re.search(
                            r"\b(?:démontre|prouve|amélior|atteint|réduit|"
                            r"surpasse|valide|garantit)\w*",
                            lower,
                        )
                        and not _numeric_claim_tokens(sentence)
                    )
                    guided_lock_framing_statement = (
                        guided_conversation
                        and location == "section"
                        and not _numeric_claim_tokens(sentence)
                        and not _salient_entities(sentence)
                        and not re.search(
                            r"\b(?:d[ée]montre|prouve|atteint|surpasse|"
                            r"valide|garantit|r[ée]duit|am[ée]liore)\w*\b",
                            lower,
                        )
                    )
                    if (
                        scientific_signal.search(sentence)
                        and not disclosure_signal.search(sentence)
                        and not project_context_statement
                        and not project_intent_statement
                        and not non_evidentiary_question
                        and not document_navigation_statement
                        and not guided_lock_framing_statement
                    ):
                        uncited_scientific_claims.append(
                            {
                                "location": location,
                                "claim": sentence,
                            }
                        )
                    continue
                cited_evidence = " ".join(
                    evidence_by_citation.get(citation, "")
                    for citation in citations
                )
                normalized_evidence = _semantic_normalize(cited_evidence)
                for entity in _salient_entities(sentence):
                    # Une phrase d'insuffisance peut nommer le système que la
                    # source connexe ne couvre précisément pas.
                    lower_sentence = sentence.casefold()
                    project_or_verrou_context = (
                        (
                            "projet" in lower_sentence
                            and re.search(
                                r"\b(?:vise|envisage|prévoit|devra|objectif|"
                                r"travaux?|r&d)\b",
                                lower_sentence,
                            )
                        )
                        or (
                            "verrou" in lower_sentence
                            and not re.search(
                                r"\b(?:démontre|prouve|atteint|surpasse|"
                                r"valide|garantit)\w*",
                                lower_sentence,
                            )
                        )
                    )
                    if (
                        disclosure_signal.search(sentence)
                        or project_or_verrou_context
                    ):
                        break
                    normalized_entity = _semantic_normalize(entity)
                    entity_aliases = {
                        # Glossaire bilingue générique, indépendant du projet.
                        "cao": {"cao", "cad"},
                    }.get(normalized_entity, {normalized_entity})
                    if (
                        len(normalized_entity) >= 3
                        and not any(
                            (
                                alias in normalized_evidence
                                if " " in alias
                                else alias in normalized_evidence.split()
                            )
                            for alias in entity_aliases
                        )
                        and not _entity_expansion_is_acronym_in_evidence(
                            entity,
                            normalized_evidence,
                        )
                        and not (
                            entity.isupper()
                            and _acronym_is_expanded_in_evidence(
                            entity,
                            normalized_evidence,
                        )
                    )
                ):
                        entity_mismatches.append(
                            {
                                "location": location,
                                "claim": sentence,
                                "citations": citations,
                                "unsupported_entity": entity,
                            }
                        )
                for numeric_value in _numeric_claim_tokens(sentence):
                    normalized_value = _semantic_normalize(numeric_value)
                    numeric_core_match = re.search(
                        r"\d+(?:[.,]\d+)?",
                        numeric_value,
                    )
                    numeric_core = _semantic_normalize(
                        numeric_core_match.group(0)
                        if numeric_core_match
                        else numeric_value
                    )
                    if (
                        normalized_value
                        and normalized_value not in normalized_evidence
                        and numeric_core not in normalized_evidence.split()
                    ):
                        numeric_mismatches.append(
                            {
                                "location": location,
                                "claim": sentence,
                                "citations": citations,
                                "unsupported_value": numeric_value,
                            }
                        )

    missing_disclosures: List[Dict[str, Any]] = []
    disclosure_pattern = re.compile(
        r"(?:aucune|absence de|sans)\s+(?:preuve|source|validation)"
        r"(?:\s+scientifique)?\s+directe"
        r"|corpus[^.]{0,120}\b(?:insuffisant|ne\s+(?:permet|suffit|démontre|établit)\s+pas)"
        r"|sources?\s+connexes?"
        r"|ne\s+(?:permet|suffit|démontre|prouve|établit)\s+pas"
        r"|(?:ne\s+|n['’])(?:fournit|fournissent|apporte|apportent|"
        r"permet|permettent|démontre|démontrent|établit|établissent)"
        r"\s+(?:donc\s+)?pas(?:\s+de)?(?:\s+preuves?\s+directes?)?"
        r"|rien\s+ne\s+(?:permet|autorise)\s+(?:donc\s+)?"
        r"(?:de|d['’])\b"
        r"|n['’]est\s+(?:donc\s+)?pas\s+possible\s+(?:de|d['’])\b"
        r"|(?:manque|défaut)\s+de\s+preuves?\s+directes?"
        r"|aucune\s+preuve[^.]{0,100}\bn['’](?:établit|démontre|"
        r"documente|valide)\b"
        r"|(?:ne\s+|n['’])(?:apporte|présente|fournit)\s+"
        r"aucune\s+preuve\b"
        r"|(?:ne\s+|n['’])(?:fournit|fournissent|apporte|apportent|"
        r"aborde|abordent)\s+(?:aucun(?:e)?|ni)\b",
        flags=re.I,
    )
    for verrou in section.get("verrous") or []:
        if not isinstance(verrou, Mapping):
            continue
        if not verrou.get("requires_insufficiency_disclosure"):
            continue
        verrou_id = clean_text(verrou.get("verrou_id"), 120)
        subsection = generated_subsections.get(verrou_id) or {}
        content = clean_text(subsection.get("content"), 200000)
        if not disclosure_pattern.search(content):
            missing_disclosures.append(
                {
                    "verrou_id": verrou_id,
                    "evidence_status": verrou.get("evidence_status"),
                    "message": (
                        "Le texte doit déclarer explicitement que le corpus ne "
                        "fournit pas de preuve directe pour ce verrou."
                    ),
                }
            )

    issues = [
        *[
            {"type": "citation_entity_mismatch", **item}
            for item in entity_mismatches
        ],
        *[
            {"type": "unsupported_numeric_value", **item}
            for item in numeric_mismatches
        ],
        *[
            {"type": "uncited_scientific_claim", **item}
            for item in uncited_scientific_claims
        ],
        *[
            {"type": "missing_direct_evidence_disclosure", **item}
            for item in missing_disclosures
        ],
    ]
    return {
        "ok": not issues,
        "issues": issues,
        "entity_mismatches": entity_mismatches,
        "numeric_mismatches": numeric_mismatches,
        "uncited_scientific_claims": uncited_scientific_claims,
        "missing_direct_evidence_disclosures": missing_disclosures,
    }


def _independent_verifier_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["passed", "issues", "summary"],
        "properties": {
            "passed": {"type": "boolean"},
            "summary": {"type": "string"},
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "issue_type",
                        "claim",
                        "citations",
                        "reason",
                        "blocking",
                    ],
                    "properties": {
                        "issue_type": {
                            "type": "string",
                            "enum": [
                                "unsupported_claim",
                                "wrong_source_attribution",
                                "unsupported_numeric_value",
                                "related_evidence_overclaim",
                                "uncited_scientific_claim",
                                "contradiction",
                                "other",
                            ],
                        },
                        "claim": {"type": "string"},
                        "citations": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "reason": {"type": "string"},
                        "blocking": {"type": "boolean"},
                    },
                },
            },
        },
    }


# BEGIN ENNOSCHOLAR_COST_AWARE_VERIFIER_V1
def _section_requires_independent_llm_verifier(
    generated: Mapping[str, Any],
    section: Mapping[str, Any],
    validation: Mapping[str, Any],
    source_roles: Mapping[str, Any],
) -> Dict[str, Any]:
    """Réserve le second LLM aux sections réellement risquées.

    Les contrôles déterministes gratuits ont déjà été exécutés avant.
    """
    if not _env_flag(
        "ENNOSCHOLAR_PHASE5_ENABLE_INDEPENDENT_VERIFIER",
        True,
    ):
        return {
            "required": False,
            "status": "disabled",
            "reasons": [],
        }

    if bool(section.get("_guided_conversation")):
        return {
            "required": False,
            "status": "guided_iterative_publication",
            "reasons": [],
        }

    if not _env_flag(
        "ENNOSCHOLAR_PHASE5_VERIFIER_RISK_ONLY",
        True,
    ):
        return {
            "required": True,
            "status": "forced_all_sections",
            "reasons": ["risk_only_disabled"],
        }

    reasons: List[str] = []
    content = clean_text(
        generated.get("content"), 200000
    )
    content += " " + " ".join(
        clean_text(row.get("content"), 100000)
        for row in generated.get("subsections") or []
        if isinstance(row, Mapping)
    )

    if _numeric_claim_tokens(content):
        reasons.append("numeric_claims")

    if len(citations_from_text(content)) >= 7:
        reasons.append("many_sources_combined")

    for verrou in section.get("verrous") or []:
        if not isinstance(verrou, Mapping):
            continue
        evidence_status = clean_text(
            verrou.get("evidence_status"), 120
        ).casefold()
        if (
            evidence_status
            and evidence_status
            not in {
                "directly_supported",
                "direct",
                "fulltext_ready",
                "supported",
            }
        ):
            reasons.append("non_direct_evidence")
        if verrou.get(
            "requires_insufficiency_disclosure"
        ):
            reasons.append(
                "insufficient_direct_evidence"
            )

    risky_role_markers = (
        "related",
        "connex",
        "methodolog",
        "background",
        "supplemental",
        "context",
        "documentation",
    )
    for role in source_roles.values():
        role_text = clean_text(role, 160).casefold()
        if any(
            marker in role_text
            for marker in risky_role_markers
        ):
            reasons.append(
                "secondary_or_context_source"
            )
            break

    semantic = validation.get(
        "semantic_claim_audit"
    )
    if (
        isinstance(semantic, Mapping)
        and semantic.get("issues")
    ):
        reasons.append("semantic_audit_signal")

    return {
        "required": bool(reasons),
        "status": (
            "risk_detected"
            if reasons
            else "low_risk"
        ),
        "reasons": list(dict.fromkeys(reasons)),
    }
# END ENNOSCHOLAR_COST_AWARE_VERIFIER_V1

def _call_independent_semantic_verifier(
    client: LLMClient,
    *,
    generated: Mapping[str, Any],
    section: Mapping[str, Any],
    evidence_units: Sequence[Mapping[str, Any]],
    source_roles: Mapping[str, Any],
) -> Dict[str, Any]:
    if not _env_flag(
        "ENNOSCHOLAR_PHASE5_ENABLE_INDEPENDENT_VERIFIER",
        True,
    ):
        return {
            "used": False,
            "passed": True,
            "status": "disabled",
            "issues": [],
        }
    verifier_prompt = f"""
Tu es le vérificateur scientifique indépendant d'une section d'état de l'art.
Tu ne réécris pas le texte. Tu contrôles chaque affirmation contre les seules
preuves fournies.

SECTION ATTENDUE
{json.dumps({
    "section_id": section.get("section_id"),
    "title": section.get("title"),
    "objective": section.get("objective"),
    "verrous": [
        {
            "verrou_id": verrou.get("verrou_id"),
            "title": verrou.get("verrou_title"),
            "evidence_status": verrou.get("evidence_status"),
            "direct_citations": verrou.get("direct_citations") or [],
            "related_citations": verrou.get("related_citations") or [],
            "methodological_citations": (
                verrou.get("methodological_citations") or []
            ),
            "background_citations": verrou.get("background_citations") or [],
        }
        for verrou in section.get("verrous") or []
        if isinstance(verrou, Mapping)
    ],
}, ensure_ascii=False)}

TEXTE À CONTRÔLER
{json.dumps(dict(generated), ensure_ascii=False)}

RÔLES GLOBAUX DES SOURCES
{json.dumps(dict(source_roles), ensure_ascii=False)}

PREUVES AUTORISÉES
{json.dumps(
    _compact_evidence(
        list(evidence_units),
        max_units=_env_int(
            "ENNOSCHOLAR_PHASE5_VERIFIER_MAX_EVIDENCE_UNITS",
            48,
            minimum=16,
            maximum=96,
        ),
        max_per_citation=_env_int(
            "ENNOSCHOLAR_PHASE5_VERIFIER_MAX_EVIDENCE_PER_SOURCE",
            4,
            minimum=2,
            maximum=8,
        ),
    ),
    ensure_ascii=False,
)}

RÈGLES DE VERDICT
- Une citation soutient une phrase seulement si sa preuve contient réellement
  la méthode, l'entité, le protocole, le résultat ou la limite affirmée.
- Une méthode mentionnée dans les travaux connexes d'un article n'est pas une
  contribution de cet article.
- Une source connexe ou méthodologique ne devient jamais une preuve directe de
  validation, d'applicabilité ou de transférabilité pour un verrou.
- Une analogie entre deux domaines ou deux familles de solveurs doit être
  explicitement présentée comme une analogie et non comme une démonstration.
- Toute valeur numérique doit être présente dans la preuve citée.
- Toute affirmation scientifique substantielle doit être citée.
- Si evidence_status n'est pas directly_supported, le texte doit reconnaître
  l'absence de preuve directe et limiter strictement sa conclusion.
- Ne sanctionne pas une reformulation fidèle ni une traduction terminologique
  évidente. Signale uniquement les problèmes scientifiques réels.
- passed vaut false dès qu'au moins une issue blocking vaut true.
""".strip()
    try:
        raw = client.generate(
            verifier_prompt,
            temperature=0.0,
            max_output_tokens=2200,
            retries=0,
            json_mode=True,
            response_schema=_independent_verifier_schema(),
            request_name=(
                "ennoscholar:phase5:independent_semantic_verifier"
            ),
        )
        parsed = _extract_json_response(raw)
        issues = [
            dict(issue)
            for issue in parsed.get("issues") or []
            if isinstance(issue, Mapping)
        ]
        blocking = [issue for issue in issues if issue.get("blocking")]
        passed = bool(parsed.get("passed")) and not blocking
        return {
            "used": True,
            "status": "ok",
            "passed": passed,
            "summary": clean_sentence(parsed.get("summary"), 3000),
            "issues": issues,
            "blocking_issues": blocking,
            "llm": client.get_last_generation_meta(),
        }
    except Exception as exc:
        require_verifier = _env_flag(
            "ENNOSCHOLAR_PHASE5_REQUIRE_INDEPENDENT_VERIFIER",
            True,
        )
        return {
            "used": True,
            "status": "error",
            "passed": not require_verifier,
            "issues": [],
            "error": f"{type(exc).__name__}: {exc}",
            "required": require_verifier,
        }


def _build_llm_prompt(
    blueprint: Dict[str, Any],
    evidence_units: List[Dict[str, Any]],
) -> str:
    plan = [
        {
            "section_id": section["section_id"],
            "title": section["title"],
            "objective": section["objective"],
            "parent_id": section.get("parent_id"),
            "level": section.get("level") or 1,
            "target_words": section.get("target_words"),
            "instructions": section.get("instructions") or [],
            "required_dimensions": section.get("required_dimensions") or [],
            "visual_requirements": section.get("visual_requirements") or [],
            "source_preferences": section.get("source_preferences") or [],
            "required_citations": section["required_citations"],
            "available_citations": section["available_citations"],
            "subsections": [
                {
                    "verrou_id": verrou["verrou_id"],
                    "title": verrou["verrou_title"],
                    "required_citations": verrou["required_citations"],
                }
                for verrou in section.get("verrous") or []
            ],
        }
        for section in blueprint["sections"]
    ]
    return f"""
Tu rédiges un unique état de l'art scientifique global en français.

CONTRAT ABSOLU
- Respecte exactement les grands titres, leur ordre, les identifiants de section et les sous-titres de verrous fournis.
- Construis une histoire scientifique transversale, pas une succession de fiches d'articles.
- Utilise uniquement les faits présents dans les unités de preuve.
- N'invente aucun auteur, article, outil, méthode, résultat, chiffre, paramètre, limite ou comparaison.
- Chaque affirmation scientifique doit porter la ou les citations qui la soutiennent.
- N'utilise aucune citation hors de la liste autorisée.
- Ne déplace pas une preuve vers un verrou auquel elle n'est pas liée.
- Les informations de style ne sont jamais des preuves.
- Une documentation officielle peut soutenir une définition, une architecture,
  une procédure ou une configuration, mais jamais à elle seule une performance
  scientifique, une supériorité de méthode ou un résultat expérimental.
- Explique les procédures et mécanismes avant d'argumenter les résultats.
- Distingue clairement résultat observé, interprétation, limite et conséquence
  pour l'incertitude scientifique du projet.
- Respecte les longueurs, dimensions et besoins visuels demandés par le consultant.
- Ne mentionne aucun nom de phase, système interne, payload, blueprint ou pipeline.
- Si une section est peu couverte, indique seulement que les sources sélectionnées ne permettent pas de la documenter davantage.

PROJET
{json.dumps({"nom": blueprint["project"], "contexte": blueprint["project_context"]}, ensure_ascii=False)}

PLAN FINAL
{json.dumps(plan, ensure_ascii=False, indent=2)}

CITATIONS AUTORISÉES
{json.dumps(blueprint["allowed_citations"], ensure_ascii=False)}

UNITÉS DE PREUVE
{json.dumps(_compact_evidence(evidence_units), ensure_ascii=False, indent=2)}

STYLE, TON UNIQUEMENT
{json.dumps(blueprint.get("style_memory") or {}, ensure_ascii=False)}

SORTIE JSON STRICTE
{{
  "title": "État de l’art scientifique — ...",
  "sections": [
    {{
      "section_id": "identifiant exact",
      "title": "titre exact",
      "content": "paragraphes rédigés avec citations [A1]",
      "subsections": [
        {{
          "verrou_id": "identifiant exact",
          "title": "titre exact",
          "content": "texte sourcé"
        }}
      ]
    }}
  ]
}}
""".strip()


def _section_target_words(
    section: Mapping[str, Any],
    total_sections: int,
) -> int:
    try:
        explicit = int(section.get("target_words") or 0)
    except Exception:
        explicit = 0
    if explicit > 0:
        return max(350, min(3500, explicit))
    # Environ dix pages pour un plan court, sans gonfler artificiellement les
    # plans très longs. Chaque phrase reste conditionnée par une preuve.
    return max(650, min(1600, int(4600 / max(1, total_sections))))


def _build_section_llm_prompt(
    blueprint: Dict[str, Any],
    section: Dict[str, Any],
    evidence_units: List[Dict[str, Any]],
    *,
    previous_tail: str,
    repair_feedback: Optional[Dict[str, Any]] = None,
) -> str:
    allowed = set(section.get("available_citations") or [])
    local_units = [
        unit
        for unit in evidence_units
        if unit.get("citation_label") in allowed
    ]
    plan_outline = [
        {
            "section_id": row.get("section_id"),
            "title": row.get("title"),
            "objective": row.get("objective"),
            "level": row.get("level") or 1,
            "parent_id": row.get("parent_id"),
        }
        for row in blueprint.get("sections") or []
    ]
    expected_subsections = [
        {
            "verrou_id": verrou.get("verrou_id"),
            "title": verrou.get("verrou_title"),
            "required_citations": verrou.get("required_citations") or [],
            "evidence_status": verrou.get("evidence_status"),
            "direct_citations": verrou.get("direct_citations") or [],
            "related_citations": verrou.get("related_citations") or [],
            "methodological_citations": (
                verrou.get("methodological_citations") or []
            ),
            "background_citations": verrou.get("background_citations") or [],
            "requires_insufficiency_disclosure": bool(
                verrou.get("requires_insufficiency_disclosure")
            ),
        }
        for verrou in section.get("verrous") or []
    ]
    target_words = _section_target_words(
        section,
        len(blueprint.get("sections") or []),
    )
    return f"""
Tu rédiges UNE section d'un état de l'art scientifique global en français.
Le document doit former une histoire continue et convaincante, pas un catalogue
d'articles. Cette section doit respecter exactement le plan du consultant.

SECTION À RÉDIGER
{json.dumps({
    "section_id": section.get("section_id"),
    "title": section.get("title"),
    "objective": section.get("objective"),
    "parent_id": section.get("parent_id"),
    "level": section.get("level") or 1,
    "target_words": target_words,
    "instructions": section.get("instructions") or [],
    "required_dimensions": section.get("required_dimensions") or [],
    "visual_requirements": section.get("visual_requirements") or [],
    "source_preferences": section.get("source_preferences") or [],
    "required_citations": section.get("required_citations") or [],
    "available_citations": section.get("available_citations") or [],
    "suggested_citations": section.get("suggested_citations") or [],
    "narrative_axes": [
        {
            "title": axis.get("title"),
            "objective": axis.get("objective"),
            "citations": axis.get("citations") or [],
        }
        for axis in section.get("phase47_axes") or []
        if isinstance(axis, dict)
    ],
    "subsections": expected_subsections,
}, ensure_ascii=False, indent=2)}

PLAN GLOBAL, POUR ASSURER LA CONTINUITÉ
{json.dumps(plan_outline, ensure_ascii=False, indent=2)}

FIN DE LA SECTION PRÉCÉDENTE
{clean_text(previous_tail, 1800) or "Première section du document."}

CONTEXTE PROJET
{json.dumps(blueprint.get("project_context") or {}, ensure_ascii=False)}

PREUVES AUTORISÉES POUR CETTE SECTION
{json.dumps(
    _compact_evidence(
        local_units,
        max_units=_env_int(
            "ENNOSCHOLAR_PHASE5_WRITER_MAX_EVIDENCE_UNITS",
            48,
            minimum=16,
            maximum=96,
        ),
        max_per_citation=_env_int(
            "ENNOSCHOLAR_PHASE5_WRITER_MAX_EVIDENCE_PER_SOURCE",
            4,
            minimum=2,
            maximum=8,
        ),
    ),
    ensure_ascii=False,
    indent=2,
)}

RÔLE DES SOURCES
{json.dumps({
    citation: (blueprint.get("source_roles") or {}).get(
        citation,
        "core_scientific_evidence",
    )
    for citation in section.get("available_citations") or []
}, ensure_ascii=False, indent=2)}

CONTRAT CIR V3 — FORCE DE DÉFENSE PAR VERROU
{json.dumps(
    evidence_matrix_for_prompt(
        blueprint.get("cir_evidence_matrix") or {},
        [
            verrou.get("verrou_id")
            for verrou in section.get("verrous") or []
            if isinstance(verrou, dict)
        ],
    ),
    ensure_ascii=False,
    indent=2,
)}

STATUT DES PREUVES PAR VERROU
{json.dumps({
    verrou.get("verrou_id"): {
        "evidence_status": verrou.get("evidence_status"),
        "direct_citations": verrou.get("direct_citations") or [],
        "related_citations": verrou.get("related_citations") or [],
        "methodological_citations": (
            verrou.get("methodological_citations") or []
        ),
        "background_citations": verrou.get("background_citations") or [],
        "requires_insufficiency_disclosure": bool(
            verrou.get("requires_insufficiency_disclosure")
        ),
    }
    for verrou in section.get("verrous") or []
    if isinstance(verrou, dict)
}, ensure_ascii=False, indent=2)}

CONTRAT DE RÉDACTION
- Utilise uniquement les preuves ci-dessus et uniquement leurs citations.
- Les citations obligatoires doivent apparaître; les autres citations autorisées
  ne sont utilisées que lorsqu'elles soutiennent réellement le raisonnement.
- Toute affirmation scientifique, tout résultat et toute limite doivent être cités.
- Place au moins une citation dans CHAQUE phrase qui contient une affirmation
  scientifique. Une citation située dans une autre phrase ou seulement à la
  fin du paragraphe ne couvre pas cette affirmation.
- Les citations groupées sont autorisées sous la forme [A11, A16, A18].
- Le contexte projet décrit le besoin mais ne constitue jamais une preuve
  scientifique. Ne lui attribue aucune citation.
- N'associe un outil, modèle ou méthode nommé à une citation que si son nom ou
  une désignation non ambiguë apparaît explicitement dans la preuve citée.
  Sinon, signale que la preuve manque au lieu d'inventer l'association.
- Ne fusionne jamais deux termes présents dans une preuve pour créer un nouveau
  nom composé, un sigle ou une étiquette de méthode (par exemple « X-Y »).
- Lorsqu'une preuve décrit une méthode hors du domaine cible, sépare toujours
  les deux idées : une phrase citée pour ce que la source établit, puis une
  phrase explicitement négative pour ce qu'elle ne documente pas. Ne mélange
  jamais dans une même phrase un résultat établi et une extrapolation au projet.
- Une source marquée supplemental_context reste un complément technique
  secondaire. Elle ne doit ni ouvrir la problématique, ni porter seule un
  résultat, une limite ou la justification d'un verrou.
- Respecte le rôle consultant associé à chaque source. Un rôle comparatif,
  secondaire ou contextuel autorise une mise en perspective, mais jamais une
  preuve directe d'applicabilité, de validation ou de transférabilité.
- Le rôle d'une source dépend du verrou considéré. Une citation marquée
  related_evidence, methodological_evidence ou background_evidence ne prouve
  jamais directement que le verrou est levé, validé ou applicable.
- Pour une sous-section de verrou, toute citation absente des listes
  direct_citations, related_citations, methodological_citations et
  background_citations de ce verrou est non classée pour ce verrou : elle ne
  peut pas être utilisée pour conclure sur sa validation ou sa levée.
- Pour tout verrou dont evidence_status n'est pas directly_supported, indique
  explicitement que le corpus sélectionné ne fournit pas de preuve directe.
  Présente les sources connexes comme des analogies ou éléments de cadrage,
  puis formule exactement ce qu'elles ne permettent pas de conclure.
- CONTRAT CIR V3 : une source CONNECTED, METHODOLOGICAL ou BACKGROUND ne peut
  jamais être formulée comme une démonstration causale du verrou.
- Pour chaque verrou, construis la logique CIR suivante lorsque les preuves
  existent : connaissances établies -> solutions existantes -> résultats
  réellement rapportés -> limites -> ce que la littérature ne permet pas de
  déterminer -> incertitude scientifique ou technique résiduelle.
- Si le contrat CIR V3 classe un verrou FAIBLE ou INSUFFISANT, ne cherche pas
  à le "défendre" artificiellement. Explique précisément ce que les sources
  connexes apportent et ce qu'elles ne démontrent pas.
- Évite les formulations de plaidoyer telles que « justifie pleinement » ou
  « prouve définitivement ». Décris les connaissances et leurs limites.
- N'attribue jamais à une citation la méthode, l'outil, le jeu de données ou le
  résultat d'une autre citation, même si les deux apparaissent dans la même
  famille scientifique.
- Introduis une définition seulement lorsqu'elle aide le raisonnement, puis
  explique le mécanisme ou la procédure avant les résultats.
- Pour chaque résultat : précise ce qui a été observé, dans quel protocole si
  l'information existe, ce que cela permet de conclure et ce que cela ne permet
  pas de conclure.
- Compare les méthodes selon des critères explicités et termine par les limites,
  les contradictions ou l'incertitude résiduelle utile au projet.
- Chaque paragraphe doit apporter une idée nouvelle. Ne répète ni une définition,
  ni un protocole, ni un résultat déjà exposé dans cette section ou dans la fin
  de la section précédente.
- Les sections d'ouverture et de clôture synthétisent le raisonnement sans
  redévelopper les procédures et résultats détaillés des sections centrales.
- Les sources sont réutilisables entre sections. Construis les familles
  réellement présentes dans les preuves; ne limite jamais une taxonomie au
  seul sous-ensemble de citations obligatoires.
- Une documentation officielle ne soutient que définition, architecture,
  procédure ou configuration. Elle ne prouve jamais seule une performance.
- N'invente aucune valeur, formule, illustration, étape ou propriété absente.
- Si une formule est fournie dans les preuves, utilise LaTeX et explique chaque
  variable. Sinon, n'en invente pas.
- Un tableau Markdown ou un schéma Mermaid n'est produit que si les preuves
  permettent de remplir chaque élément sans invention.
- Reformule les preuves : ne recopie jamais un en-tête ou pied de page, un
  compteur de pages, une légende de figure/tableau ou un fragment OCR brut.
- Évite les formulations « l'article A1 présente ». Fais une synthèse transversale.
- Le titre, l'identifiant, l'ordre et les sous-sections sont immuables.

{((
    "RÉPARATION DEMANDÉE\n"
    "Révise le brouillon rejeté fourni ci-dessous. Conserve sa structure, ses "
    "faits valides et ses citations obligatoires. Modifie uniquement les "
    "affirmations signalées par la validation, sans introduire de nouveau nom "
    "de méthode, outil, jeu de données, sigle ou résultat. Pour chaque "
    "citation_entity_mismatch, n'attribue jamais l'entité absente à la source : "
    "supprime-la de la phrase citée ou sépare le fait établi, avec citation, de "
    "la question propre au projet, sans citation. Une phrase d'insuffisance peut "
    "nommer la cible absente seulement si elle dit explicitement que la source "
    "citée ne la documente pas ou ne permet pas de conclure.\n"
    + json.dumps(repair_feedback, ensure_ascii=False)
) if repair_feedback else "")}

SORTIE JSON STRICTE
{{
  "section_id": "{section.get("section_id")}",
  "title": {json.dumps(section.get("title"), ensure_ascii=False)},
  "content": "paragraphes développés avec citations [A1]",
  "subsections": [
    {{
      "verrou_id": "identifiant exact fourni",
      "title": "titre exact fourni",
      "content": "texte argumenté et sourcé"
    }}
  ]
}}
""".strip()


def _compact_section_validation_feedback(
    validation: Mapping[str, Any],
) -> Dict[str, Any]:
    semantic = validation.get("semantic_claim_audit") or {}
    verifier = validation.get("independent_semantic_verifier") or {}

    def compact_issues(items: Any, limit: int = 12) -> List[Dict[str, Any]]:
        output: List[Dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, Mapping):
                continue
            output.append(
                {
                    key: clean_text(value, 1200)
                    if isinstance(value, str)
                    else value
                    for key, value in item.items()
                    if key
                    in {
                        "type",
                        "issue_type",
                        "location",
                        "claim",
                        "citations",
                        "unsupported_entity",
                        "unsupported_value",
                        "reason",
                        "message",
                        "verrou_id",
                        "blocking",
                    }
                }
            )
            if len(output) >= limit:
                break
        return output

    return {
        "errors": list(validation.get("errors") or []),
        "missing_required_citations": list(
            validation.get("missing_required_citations") or []
        ),
        "missing_verrou_subsection_citations": list(
            validation.get("missing_verrou_subsection_citations") or []
        ),
        "word_count": validation.get("word_count"),
        "minimum_words": validation.get("minimum_words"),
        "maximum_words": validation.get("maximum_words"),
        "semantic_issues": compact_issues(semantic.get("issues")),
        "verifier_issues": compact_issues(
            verifier.get("blocking_issues") or verifier.get("issues")
        ),
        "non_french_fragments": [
            clean_text(item, 700)
            for item in validation.get("non_french_raw_fragments") or []
        ][:6],
        "raw_extraction_fragments": [
            clean_text(item, 700)
            for item in validation.get("raw_extraction_fragments") or []
        ][:6],
    }


def _build_targeted_section_repair_prompt(
    blueprint: Mapping[str, Any],
    section: Mapping[str, Any],
    current_draft: Mapping[str, Any],
    validation: Mapping[str, Any],
    local_evidence: Sequence[Mapping[str, Any]],
) -> str:
    feedback = _compact_section_validation_feedback(validation)
    useful_citations = set(citations_from_obj(current_draft))
    useful_citations.update(section.get("required_citations") or [])
    for issue in [
        *(feedback.get("semantic_issues") or []),
        *(feedback.get("verifier_issues") or []),
    ]:
        useful_citations.update(citations_from_obj(issue))
    if not useful_citations:
        useful_citations.update(section.get("available_citations") or [])
    repair_evidence = [
        dict(unit)
        for unit in local_evidence
        if unit.get("citation_label") in useful_citations
    ]
    expected_subsections = [
        {
            "verrou_id": verrou.get("verrou_id"),
            "title": verrou.get("verrou_title"),
            "evidence_status": verrou.get("evidence_status"),
            "direct_citations": verrou.get("direct_citations") or [],
            "related_citations": verrou.get("related_citations") or [],
            "methodological_citations": (
                verrou.get("methodological_citations") or []
            ),
            "background_citations": verrou.get("background_citations") or [],
            "requires_insufficiency_disclosure": bool(
                verrou.get("requires_insufficiency_disclosure")
            ),
        }
        for verrou in section.get("verrous") or []
        if isinstance(verrou, Mapping)
    ]
    return f"""
Tu corriges en français la NOUVELLE section que tu viens de rédiger. Il ne
s'agit ni de reprendre une ancienne version ni de produire un extrait de
sources. Conserve les paragraphes valides, l'argumentation et les citations
correctes; modifie uniquement les passages signalés ci-dessous.

SECTION IMMUABLE
{json.dumps({
    "section_id": section.get("section_id"),
    "title": section.get("title"),
    "objective": section.get("objective"),
    "target_words": _section_target_words(
        section,
        len(blueprint.get("sections") or []),
    ),
    "available_citations": section.get("available_citations") or [],
    "required_citations": section.get("required_citations") or [],
    "subsections": expected_subsections,
}, ensure_ascii=False, indent=2)}

NOUVELLE RÉDACTION À CORRIGER
{clean_text(json.dumps(dict(current_draft), ensure_ascii=False), 40000)}

PROBLÈMES PRÉCIS À RÉPARER
{json.dumps(feedback, ensure_ascii=False, indent=2)}

PREUVES STRICTEMENT UTILES À LA RÉPARATION
{json.dumps(
    _compact_evidence(
        repair_evidence,
        max_units=32,
        max_per_citation=4,
    ),
    ensure_ascii=False,
    indent=2,
)}

CONTRAT
- Rédige l'intégralité de la section en français naturel de niveau consultant.
- Ne copie aucune phrase anglaise, formule OCR corrompue, en-tête ou légende.
- N'ajoute aucune nouvelle affirmation : retire ou nuance seulement celles que
  les preuves ne soutiennent pas.
- Une absence de preuve dans le corpus est un constat de couverture : formule-la
  explicitement comme telle, sans faire dire à une source ce qu'elle n'étudie pas.
- Préserve les faits et citations non signalés.
- Garde exactement section_id, title, l'ordre et les titres des sous-sections.
- Utilise exclusivement les citations disponibles.

SORTIE JSON STRICTE
{{
  "section_id": {json.dumps(section.get("section_id"), ensure_ascii=False)},
  "title": {json.dumps(section.get("title"), ensure_ascii=False)},
  "content": "section complète corrigée en français",
  "subsections": [
    {{
      "verrou_id": "identifiant exact fourni",
      "title": "titre exact fourni",
      "content": "texte corrigé en français"
    }}
  ]
}}
""".strip()


def _build_language_cleanup_prompt(
    section: Mapping[str, Any],
    current_draft: Mapping[str, Any],
) -> str:
    return f"""
Révise la section JSON ci-dessous en français scientifique naturel. Traduis ou
reformule uniquement les passages anglais et supprime les caractères OCR de
contrôle. Ne change aucun fait, aucune citation, aucun identifiant, aucun titre
et aucune structure. Ne crée aucune formule. Retourne la section JSON complète.

CITATIONS AUTORISÉES
{json.dumps(section.get("available_citations") or [], ensure_ascii=False)}

SECTION
{clean_text(json.dumps(dict(current_draft), ensure_ascii=False), 45000)}

SORTIE JSON STRICTE
{{
  "section_id": {json.dumps(section.get("section_id"), ensure_ascii=False)},
  "title": {json.dumps(section.get("title"), ensure_ascii=False)},
  "content": "texte intégral en français",
  "subsections": [
    {{"verrou_id": "identifiant exact", "title": "titre exact", "content": "texte français"}}
  ]
}}
""".strip()


_SECTION_PUBLICATION_BLOCKERS = {
    "section_id_mismatch",
    "section_title_mismatch",
    "subsections_mismatch",
    "unknown_citations",
    "raw_extraction_fragment",
    "non_french_or_raw_source_fragment",
    # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
    "cir_related_evidence_overclaim",
    "cir_missing_insufficiency_disclosure",
    # END ENNOSCHOLAR_CIR_QUALITY_V3
}

_SECTION_ESCALATION_BLOCKERS = {
    *_SECTION_PUBLICATION_BLOCKERS,
    "missing_required_citations",
    "missing_verrou_subsection_citations",
    "unsupported_or_misattributed_claims",
    "independent_semantic_verifier_rejected",
}


def _section_publication_blockers(
    validation: Mapping[str, Any],
    section: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    if section and bool(section.get("_guided_conversation")):
        return (
            []
            if int(validation.get("word_count") or 0) > 0
            else ["empty_generated_section"]
        )
    return sorted(
        set(validation.get("errors") or []) & _SECTION_PUBLICATION_BLOCKERS
    )


def _section_escalation_blockers(
    validation: Mapping[str, Any],
    section: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Retourne uniquement les défauts justifiant une nouvelle génération.

    La longueur cible reste un indicateur éditorial : elle ne doit jamais, à
    elle seule, acheter une seconde rédaction premium. Les défauts de contrat,
    de langue, de citations et d'attribution scientifique restent bloquants.
    """

    if section and bool(section.get("_guided_conversation")):
        return (
            []
            if int(validation.get("word_count") or 0) > 0
            else ["empty_generated_section"]
        )

    return sorted(
        set(validation.get("errors") or [])
        & _SECTION_ESCALATION_BLOCKERS
    )


def _validate_generated_section(
    generated: Dict[str, Any],
    section: Dict[str, Any],
    *,
    total_sections: int = 1,
    evidence_units: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    if clean_text(generated.get("section_id"), 120) != clean_text(
        section.get("section_id"), 120
    ):
        errors.append("section_id_mismatch")
    if clean_sentence(generated.get("title"), 700) != clean_sentence(
        section.get("title"), 700
    ):
        errors.append("section_title_mismatch")
    expected = [
        (
            clean_text(row.get("verrou_id"), 120),
            clean_sentence(row.get("verrou_title"), 700),
        )
        for row in section.get("verrous") or []
    ]
    observed = [
        (
            clean_text(row.get("verrou_id"), 120),
            clean_sentence(row.get("title"), 700),
        )
        for row in generated.get("subsections") or []
        if isinstance(row, dict)
    ]
    if observed != expected:
        errors.append("subsections_mismatch")
    body = clean_text(generated.get("content"), 200000) + " " + " ".join(
        clean_text(row.get("content"), 200000)
        for row in generated.get("subsections") or []
        if isinstance(row, dict)
    )
    raw_extraction_fragments = _raw_extraction_fragments(body)
    if raw_extraction_fragments:
        errors.append("raw_extraction_fragment")
    non_french_raw_fragments = _non_french_raw_fragments(body)
    if non_french_raw_fragments:
        errors.append("non_french_or_raw_source_fragment")
    used = set(citations_from_text(body))
    allowed = set(section.get("available_citations") or [])
    required = set(section.get("required_citations") or [])
    unknown = sorted(used - allowed)
    missing = citation_sort(required - used)
    if unknown:
        errors.append("unknown_citations")
    if missing:
        errors.append("missing_required_citations")
    generated_subsections = {
        clean_text(row.get("verrou_id"), 120): row
        for row in generated.get("subsections") or []
        if isinstance(row, dict) and clean_text(row.get("verrou_id"), 120)
    }
    missing_by_verrou: List[Dict[str, Any]] = []
    for expected_verrou in section.get("verrous") or []:
        if not isinstance(expected_verrou, dict):
            continue
        verrou_id = clean_text(expected_verrou.get("verrou_id"), 120)
        required_for_verrou = set(
            citation_sort(expected_verrou.get("required_citations") or [])
        )
        generated_verrou = generated_subsections.get(verrou_id) or {}
        detected_for_verrou = set(
            citations_from_text(
                clean_text(generated_verrou.get("content"), 200000)
            )
        )
        missing_for_verrou = citation_sort(
            required_for_verrou - detected_for_verrou
        )
        if missing_for_verrou:
            missing_by_verrou.append(
                {
                    "verrou_id": verrou_id,
                    "missing_citations": missing_for_verrou,
                }
            )
    if missing_by_verrou:
        errors.append("missing_verrou_subsection_citations")
    word_count = len(re.findall(r"\b[\wÀ-ÿ'-]+\b", body))
    target_words = _section_target_words(section, total_sections)
    minimum_words = max(
        250,
        int(target_words * 0.65),
    )
    maximum_words = max(minimum_words + 150, int(target_words * 1.45))
    if word_count < minimum_words:
        errors.append("section_too_short")
    if word_count > maximum_words:
        errors.append("section_too_long")
    semantic_claim_audit = (
        _semantic_claim_audit(
            generated,
            section,
            evidence_units,
        )
        if evidence_units is not None
        else {
            "ok": True,
            "issues": [],
            "skipped": "evidence_units_not_provided",
        }
    )
    if not semantic_claim_audit.get("ok"):
        errors.append("unsupported_or_misattributed_claims")

    # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
    cir_matrix = (
        section.get("_cir_evidence_matrix")
        if isinstance(section.get("_cir_evidence_matrix"), Mapping)
        else {}
    )
    cir_claim_audit = (
        audit_cir_section(generated, section, cir_matrix)
        if cir_matrix
        else {
            "ok": True,
            "issues": [],
            "skipped": "matrix_not_attached",
        }
    )
    for issue in cir_claim_audit.get("issues") or []:
        issue_type = clean_text(issue.get("type"), 120)
        if issue_type and issue_type not in errors:
            errors.append(issue_type)
    # END ENNOSCHOLAR_CIR_QUALITY_V3

    return {
        "ok": not errors,
        "errors": errors,
        "unknown_citations": unknown,
        "missing_required_citations": missing,
        "missing_verrou_subsection_citations": missing_by_verrou,
        "word_count": word_count,
        "minimum_words": minimum_words,
        "maximum_words": maximum_words,
        "target_words": target_words,
        "raw_extraction_fragments": raw_extraction_fragments,
        "non_french_raw_fragments": non_french_raw_fragments,
        "semantic_claim_audit": semantic_claim_audit,
        # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
        "cir_claim_audit": cir_claim_audit,
        # END ENNOSCHOLAR_CIR_QUALITY_V3
    }


def _repair_uncited_taxonomy_claims(
    generated: Mapping[str, Any],
    section: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """Rattache au corpus les seules phrases de taxonomie non citées.

    La réparation est volontairement étroite : elle ne concerne que le corps
    principal, les phrases de structuration d'une typologie, sans valeur
    numérique ni nom de méthode autre que des sigles. Les citations employées
    sont celles que le planificateur a déjà déclarées obligatoires pour la
    section. Toute autre affirmation reste soumise à une nouvelle génération.
    """
    semantic_audit = validation.get("semantic_claim_audit") or {}
    issues = semantic_audit.get("issues") or []
    if not issues or any(
        issue.get("type") != "uncited_scientific_claim"
        or issue.get("location") != "section"
        for issue in issues
        if isinstance(issue, Mapping)
    ):
        return dict(generated), []
    citations = citation_sort(section.get("required_citations") or [])
    if not citations:
        return dict(generated), []

    taxonomy_signal = re.compile(
        r"\b(?:famill\w*|courant\w*|axes?|cat[ée]gor\w*|"
        r"structur\w*|classif\w*|typolog\w*)\b",
        flags=re.I,
    )
    content = clean_text(generated.get("content"), 200000)
    repaired_claims: List[str] = []
    citation_group = "[" + ", ".join(citations) + "]"
    for issue in issues:
        if not isinstance(issue, Mapping):
            continue
        claim = clean_sentence(issue.get("claim"), 4000)
        entities = _salient_entities(claim)
        if (
            not claim
            or claim not in content
            or not taxonomy_signal.search(claim)
            or _numeric_claim_tokens(claim)
            or any(not entity.isupper() for entity in entities)
        ):
            return dict(generated), []
        punctuation = claim[-1] if claim[-1:] in ".!?" else "."
        stem = claim[:-1].rstrip() if claim[-1:] in ".!?" else claim.rstrip()
        replacement = f"{stem} {citation_group}{punctuation}"
        content = content.replace(claim, replacement, 1)
        repaired_claims.append(claim)

    if not repaired_claims:
        return dict(generated), []
    repaired = dict(generated)
    repaired["content"] = content
    return repaired, repaired_claims


def _ensure_guided_insufficiency_disclosures(
    generated: Mapping[str, Any],
    section: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """Ajoute la réserve scientifique exigée au brouillon du chat.

    Cette réparation ne crée aucun fait et ne touche pas au workflow 1. Elle
    matérialise seulement, dans la sous-section du verrou, la conclusion déjà
    calculée par la matrice déterministe : aucune preuve directe confirmée.
    """
    output = dict(generated)
    if not output or not section.get("_guided_conversation"):
        return output, []
    matrix = (
        section.get("_cir_evidence_matrix")
        if isinstance(section.get("_cir_evidence_matrix"), Mapping)
        else {}
    )
    if not matrix:
        return output, []
    audit = audit_cir_section(output, section, matrix)
    missing_ids = {
        clean_text(issue.get("verrou_id"), 120)
        for issue in audit.get("issues") or []
        if isinstance(issue, Mapping)
        and issue.get("type") == "cir_missing_insufficiency_disclosure"
    }
    missing_ids.discard("")
    if not missing_ids:
        return output, []

    changed: List[str] = []
    subsections: List[Dict[str, Any]] = []
    for raw in output.get("subsections") or []:
        if not isinstance(raw, Mapping):
            continue
        subsection = dict(raw)
        verrou_id = clean_text(subsection.get("verrou_id"), 120)
        if verrou_id in missing_ids:
            disclosure = (
                "Le corpus sélectionné ne fournit aucune preuve scientifique "
                "directe permettant de conclure sur ce verrou ; les articles "
                "disponibles apportent uniquement des éléments connexes qui "
                "doivent être interprétés dans leur périmètre propre."
            )
            subsection["content"] = (
                clean_text(subsection.get("content"), 200000)
                + "\n\n"
                + disclosure
            ).strip()
            changed.append(verrou_id)
        subsections.append(subsection)
    output["subsections"] = subsections
    return output, changed


def _build_section_enrichment_prompt(
    blueprint: Dict[str, Any],
    section: Dict[str, Any],
    local_evidence: List[Dict[str, Any]],
    current_content: str,
    *,
    missing_words: int,
) -> str:
    return f"""
Tu enrichis une section scientifique déjà valide sans la réécrire et sans
répéter son contenu. Produis uniquement le complément demandé.

SECTION
{json.dumps({
    "section_id": section.get("section_id"),
    "title": section.get("title"),
    "objective": section.get("objective"),
    "instructions": section.get("instructions") or [],
    "required_dimensions": section.get("required_dimensions") or [],
    "visual_requirements": section.get("visual_requirements") or [],
    "citations_autorisees": section.get("available_citations") or [],
    "mots_supplementaires_vises": max(300, missing_words),
}, ensure_ascii=False, indent=2)}

RÔLE DES SOURCES
{json.dumps({
    citation: (blueprint.get("source_roles") or {}).get(
        citation,
        "core_scientific_evidence",
    )
    for citation in section.get("available_citations") or []
}, ensure_ascii=False, indent=2)}

CONTENU DÉJÀ RÉDIGÉ — NE PAS LE RÉPÉTER
{clean_text(current_content, 40000)}

PREUVES AUTORISÉES
{json.dumps(
    _compact_evidence(
        local_evidence,
        max_units=36,
        max_per_citation=4,
    ),
    ensure_ascii=False,
    indent=2,
)}

CONSIGNES
- Approfondis en priorité les procédures, conditions expérimentales, résultats,
  comparaisons et limites encore insuffisamment expliqués.
- Construis une progression argumentée; ne produis pas un catalogue d'articles.
- Toute affirmation scientifique doit être rattachée à une citation autorisée.
- N'invente aucun chiffre, protocole, formule, résultat, schéma ou propriété.
- supplemental_context reste secondaire et ne justifie jamais seul un verrou.
- N'ajoute aucun titre et ne répète pas l'introduction de la section.

SORTIE JSON STRICTE
{{"addition": "complément scientifique continu avec citations [A1]"}}
""".strip()


def _build_missing_citation_repair_prompt(
    section: Mapping[str, Any],
    current_content: str,
    evidence_units: Sequence[Mapping[str, Any]],
    missing_citations: Sequence[str],
) -> str:
    missing = citation_sort(missing_citations)
    missing_set = set(missing)
    evidence = [
        dict(unit)
        for unit in evidence_units
        if unit.get("citation_label") in missing_set
    ]
    return f"""
Tu complètes une section scientifique en français avec un seul paragraphe
continu de 90 à 180 mots.

OBJECTIF DE LA SECTION
{clean_sentence(section.get("objective"), 1800)}

CITATIONS MANQUANTES OBLIGATOIRES
{json.dumps(missing, ensure_ascii=False)}

PREUVES AUTORISÉES POUR CE COMPLÉMENT
{json.dumps(
    _compact_evidence(
        evidence,
        max_units=max(8, len(missing) * 6),
        max_per_citation=6,
    ),
    ensure_ascii=False,
    indent=2,
)}

CONTENU DÉJÀ RÉDIGÉ — NE PAS LE RÉPÉTER
{clean_text(current_content, 30000)}

CONTRAT
- Utilise chaque citation manquante au moins une fois.
- Chaque phrase scientifique contient sa citation.
- N'utilise aucune autre citation.
- Reformule les preuves; ne copie aucun fragment OCR, en-tête, pied de page,
  légende de figure ou compteur de pages.
- N'invente aucun nom de méthode, outil, résultat, valeur ou protocole.
- Apporte une idée complémentaire utile à l'objectif, sans écrire un catalogue
  d'articles et sans répéter le contenu existant.

SORTIE JSON STRICTE
{{"addition": "paragraphe scientifique en français avec citations"}}
""".strip()


def call_sectional_writer_llm(
    blueprint: Dict[str, Any],
    evidence_units: List[Dict[str, Any]],
    *,
    checkpoint_dir: Optional[str | Path] = None,
    progress_markdown_path: Optional[str | Path] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not _env_flag("ENNOSCHOLAR_PHASE5_ENABLE_LLM", True):
        return {}, {"used": False, "status": "disabled"}
    llm_runtime_config = reload_config()

    # Coût-first :
    # - mini pour le premier draft ;
    # - mini pour une correction ciblée si un vrai blocage subsiste ;
    # - premium seulement après l'échec de ces tentatives économiques.
    configured_model = clean_text(
        os.getenv("ENNOSCHOLAR_PHASE5_DRAFT_MODEL")
        or llm_runtime_config.get(
            "ENNOSCHOLAR_PHASE5_DRAFT_MODEL"
        )
        or "gpt-4.1-mini",
        200,
    )
    escalation_model = clean_text(
        os.getenv(
            "ENNOSCHOLAR_PHASE5_ESCALATION_MODEL"
        )
        or llm_runtime_config.get(
            "ENNOSCHOLAR_PHASE5_ESCALATION_MODEL"
        )
        or os.getenv(
            "ENNOSCHOLAR_PHASE5_WRITER_MODEL"
        )
        or "gpt-4.1",
        200,
    )
    verifier_model = clean_text(
        os.getenv(
            "ENNOSCHOLAR_PHASE5_VERIFIER_MODEL"
        )
        or llm_runtime_config.get(
            "ENNOSCHOLAR_PHASE5_VERIFIER_MODEL"
        )
        or "gpt-4.1-mini",
        200,
    )

    client = LLMClient(
        model=configured_model or None
    )
    escalation_client = LLMClient(
        model=escalation_model or None
    )
    verifier_client = LLMClient(
        model=verifier_model or None
    )
    try:
        section_timeout = max(
            30,
            int(os.getenv("ENNOSCHOLAR_PHASE5_SECTION_TIMEOUT_SECONDS", "240")),
        )
    except Exception:
        section_timeout = 240
    client.read_timeout = min(
        client.read_timeout,
        section_timeout,
    )
    escalation_client.read_timeout = min(
        escalation_client.read_timeout,
        section_timeout,
    )
    verifier_client.read_timeout = min(
        verifier_client.read_timeout,
        section_timeout,
    )
    legacy_attempts = _env_int(
        "ENNOSCHOLAR_PHASE5_SECTION_ATTEMPTS",
        2,
        minimum=1,
        maximum=3,
    )
    mini_attempts = _env_int(
        "ENNOSCHOLAR_PHASE5_MINI_ATTEMPTS",
        legacy_attempts,
        minimum=1,
        maximum=3,
    )
    premium_enabled = (
        _env_flag(
            "ENNOSCHOLAR_PHASE5_ENABLE_PREMIUM_ESCALATION",
            True,
        )
        and bool(escalation_model)
        and clean_text(escalation_model, 200).casefold()
        != clean_text(configured_model, 200).casefold()
    )
    attempt_plan: List[Tuple[str, LLMClient]] = [
        ("mini_draft", client),
        *[
            ("mini_repair", client)
            for _ in range(max(0, mini_attempts - 1))
        ],
    ]
    if premium_enabled:
        attempt_plan.append(("premium_escalation", escalation_client))

    checkpoint_root = Path(checkpoint_dir) if checkpoint_dir else None
    progress_path = Path(progress_markdown_path) if progress_markdown_path else None
    generated_sections: List[Dict[str, Any]] = []
    reports: List[Dict[str, Any]] = []
    advisory_sections_count = 0
    mini_repairs_count = 0
    premium_escalations_count = 0
    previous_tail = ""
    total_sections = len(blueprint.get("sections") or [])
    for index, section in enumerate(blueprint.get("sections") or [], 1):
        guided_iterative_publication = bool(
            section.get("_guided_conversation")
        )
        accepted: Dict[str, Any] = {}
        accepted_mode = ""
        feedback: Optional[Dict[str, Any]] = None
        attempts: List[Dict[str, Any]] = []
        latest_llm_candidate: Dict[str, Any] = {}
        latest_llm_validation: Dict[str, Any] = {}
        latest_publishable_candidate: Dict[str, Any] = {}
        latest_publishable_validation: Dict[str, Any] = {}
        section_id = clean_text(section.get("section_id"), 120) or f"section_{index}"
        safe_section_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", section_id).strip("_")
        local_evidence = [
            unit
            for unit in evidence_units
            if unit.get("citation_label")
            in set(section.get("available_citations") or [])
        ]
        # BEGIN ENNOSCHOLAR_CROSS_MODEL_CHECKPOINT_RESUME_V3_1
        def _section_checkpoint_fingerprint_for_model(
            fingerprint_model: Any,
        ) -> str:
            checkpoint_fingerprint_payload = {
                    "section": section,
                    "evidence": local_evidence,
                    "previous_tail": previous_tail,
                    "project_context": blueprint.get("project_context") or {},
                    "style_memory": blueprint.get("style_memory") or {},
                    "source_roles": {
                        citation: (blueprint.get("source_roles") or {}).get(
                            citation
                        )
                        for citation in section.get("available_citations") or []
                    },
                    "model": clean_text(fingerprint_model, 200),
                    "scientific_validation_contract": (
                        "compact_targeted_llm_repair_no_raw_fallback_v10"
                    ),
                }
            return hashlib.sha256(
                json.dumps(
                    checkpoint_fingerprint_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()

        checkpoint_fingerprint = (
            _section_checkpoint_fingerprint_for_model(
                configured_model
            )
        )
        checkpoint_path = (
            checkpoint_root / f"{index:02d}_{safe_section_id}.json"
            if checkpoint_root
            else None
        )
        if (
            checkpoint_path
            and checkpoint_path.is_file()
            and _env_flag(
                "ENNOSCHOLAR_PHASE5_REUSE_SECTION_CHECKPOINTS",
                False,
            )
        ):
            cached = read_json(checkpoint_path, {}) or {}

            # Compatibilité avec les checkpoints produits avant un
            # changement de modèle de coût. Tous les éléments scientifiques
            # restent identiques ; seul le modèle générateur peut différer.
            checkpoint_fingerprint_candidates = {
                checkpoint_fingerprint
            }
            cached_llm_meta = (
                cached.get("llm")
                if isinstance(cached.get("llm"), dict)
                else {}
            )
            legacy_model_candidates = {
                clean_text(cached_llm_meta.get(key), 200)
                for key in (
                    "model",
                    "model_name",
                    "requested_model",
                    "effective_model",
                )
            }
            legacy_model_candidates.add(
                clean_text(
                    os.getenv(
                        "ENNOSCHOLAR_PHASE5_WRITER_MODEL"
                    )
                    or llm_runtime_config.get(
                        "ENNOSCHOLAR_PHASE5_WRITER_MODEL"
                    ),
                    200,
                )
            )
            legacy_model_candidates.discard("")

            for legacy_model in legacy_model_candidates:
                checkpoint_fingerprint_candidates.add(
                    _section_checkpoint_fingerprint_for_model(
                        legacy_model
                    )
                )

            checkpoint_fingerprint_compatible = (
                cached.get("fingerprint")
                in checkpoint_fingerprint_candidates
            )

            cached_section = cached.get("section")
            cached_validation = (
                _validate_generated_section(
                    cached_section,
                    section,
                    total_sections=total_sections,
                    evidence_units=local_evidence,
                )
                if isinstance(cached_section, dict)
                else {"ok": False}
            )
            if (
                checkpoint_fingerprint_compatible
                and not _section_escalation_blockers(
                    cached_validation,
                    section,
                )
                and (
                    guided_iterative_publication
                    or
                    not _env_flag(
                        "ENNOSCHOLAR_PHASE5_ENABLE_INDEPENDENT_VERIFIER",
                        True,
                    )
                    or bool(
                        (
                            cached.get("independent_semantic_verifier")
                            or {}
                        ).get("passed")
                    )
                )
            ):
                accepted = cached_section
                accepted_mode = "llm_cached"
                attempts.append(
                    {
                        "attempt": 0,
                        "cached": True,
                        "checkpoint_reused_without_llm": True,
                        "checkpoint_generator_model": (
                            cached_llm_meta.get("model")
                            or cached_llm_meta.get("model_name")
                            or cached_llm_meta.get("effective_model")
                            or ""
                        ),
                        "current_draft_model": configured_model,
                        "cross_model_checkpoint_compatibility": (
                            clean_text(
                                cached_llm_meta.get("model")
                                or cached_llm_meta.get("model_name")
                                or cached_llm_meta.get(
                                    "effective_model"
                                ),
                                200,
                            )
                            != clean_text(configured_model, 200)
                        ),
                        "validation": cached_validation,
                        "independent_semantic_verifier": cached.get(
                            "independent_semantic_verifier"
                        )
                        or {},
                        "llm": cached.get("llm") or {},
                    }
                )

        for attempt, (attempt_stage, active_writer_client) in enumerate(
            attempt_plan,
            1,
        ):
            if accepted:
                break
            if attempt == 1 or not feedback:
                prompt = _build_section_llm_prompt(
                    blueprint,
                    section,
                    evidence_units,
                    previous_tail=previous_tail,
                )
            else:
                prompt = _build_targeted_section_repair_prompt(
                    blueprint,
                    section,
                    feedback.get("rejected_draft") or {},
                    feedback.get("validation") or {},
                    local_evidence,
                )
            try:
                if attempt_stage == "mini_repair":
                    mini_repairs_count += 1
                elif attempt_stage == "premium_escalation":
                    premium_escalations_count += 1
                raw = active_writer_client.generate(
                    prompt,
                    temperature=0.08,
                    max_output_tokens=max(
                        min(
                            4200,
                            1800
                            + 400
                            * len(
                                [
                                    row
                                    for row in section.get("verrous") or []
                                    if isinstance(row, dict)
                                ]
                            ),
                        ),
                        min(
                            6200,
                            int(
                                _section_target_words(
                                    section,
                                    len(blueprint.get("sections") or []),
                                )
                                * 1.75
                            ),
                        ),
                    ),
                    retries=0,
                    json_mode=True,
                    request_name=f"ennoscholar:phase5:section:{index}",
                )
                parsed = _extract_json_response(raw)
                if guided_iterative_publication:
                    disclosure_repairs = []
                else:
                    parsed, disclosure_repairs = (
                        _ensure_guided_insufficiency_disclosures(
                            parsed,
                            section,
                        )
                    )
                validation = _validate_generated_section(
                    parsed,
                    section,
                    total_sections=total_sections,
                    evidence_units=local_evidence,
                )
                base_llm_meta = active_writer_client.get_last_generation_meta()
                deterministic_repair: Dict[str, Any] = (
                    {
                        "applied": True,
                        "kind": "guided_insufficiency_disclosure",
                        "verrou_ids": disclosure_repairs,
                    }
                    if disclosure_repairs
                    else {}
                )
                citation_addition_repair: Dict[str, Any] = {
                    "applied": False,
                }
                initial_errors = set(validation.get("errors") or [])
                missing_for_repair = citation_sort(
                    validation.get("missing_required_citations") or []
                )
                if (
                    not guided_iterative_publication
                    and
                    parsed
                    and missing_for_repair
                    and initial_errors
                    <= {
                        "missing_required_citations",
                        "section_too_short",
                    }
                    and _env_flag(
                        "ENNOSCHOLAR_PHASE5_ENABLE_LLM_CITATION_REPAIR",
                        False,
                    )
                ):
                    try:
                        current_content = clean_text(
                            parsed.get("content"),
                            200000,
                        )
                        repair_raw = client.generate(
                            _build_missing_citation_repair_prompt(
                                section,
                                current_content,
                                local_evidence,
                                missing_for_repair,
                            ),
                            temperature=0.05,
                            max_output_tokens=900,
                            retries=0,
                            json_mode=True,
                            request_name=(
                                f"ennoscholar:phase5:section:{index}:"
                                "missing-citations"
                            ),
                        )
                        repair_payload = _extract_json_response(repair_raw)
                        addition = clean_text(
                            repair_payload.get("addition"),
                            12000,
                        )
                        addition_citations = set(
                            citations_from_text(addition)
                        )
                        allowed_for_addition = set(missing_for_repair)
                        if (
                            addition
                            and allowed_for_addition
                            <= addition_citations
                            and not (
                                addition_citations - allowed_for_addition
                            )
                            and not _raw_extraction_fragments(addition)
                        ):
                            repaired = dict(parsed)
                            repaired["content"] = (
                                current_content + "\n\n" + addition
                            ).strip()
                            repaired_validation = (
                                _validate_generated_section(
                                    repaired,
                                    section,
                                    total_sections=total_sections,
                                    evidence_units=local_evidence,
                                )
                            )
                            citation_addition_repair = {
                                "applied": True,
                                "missing_citations": missing_for_repair,
                                "addition": addition,
                                "validation": repaired_validation,
                                "llm": base_llm_meta,
                            }
                            parsed = repaired
                            validation = repaired_validation
                        else:
                            citation_addition_repair = {
                                "applied": False,
                                "status": "invalid_addition",
                                "missing_citations": missing_for_repair,
                                "detected_citations": citation_sort(
                                    addition_citations
                                ),
                                "llm": client.get_last_generation_meta(),
                            }
                    except Exception as repair_exc:
                        citation_addition_repair = {
                            "applied": False,
                            "status": "llm_error",
                            "error": (
                                f"{type(repair_exc).__name__}: "
                                f"{repair_exc}"
                            ),
                        }
                taxonomy_repaired, taxonomy_claims = (
                    _repair_uncited_taxonomy_claims(
                        parsed,
                        section,
                        validation,
                    )
                    if parsed and not guided_iterative_publication
                    else ({}, [])
                )
                if taxonomy_claims:
                    taxonomy_validation = _validate_generated_section(
                        taxonomy_repaired,
                        section,
                        total_sections=total_sections,
                        evidence_units=local_evidence,
                    )
                    deterministic_repair = {
                        "applied": True,
                        "kind": "uncited_taxonomy_claims",
                        "repaired_claims": taxonomy_claims,
                        "validation": taxonomy_validation,
                    }
                    parsed = taxonomy_repaired
                    validation = taxonomy_validation
                validation_errors = set(validation.get("errors") or [])
                if (
                    not guided_iterative_publication
                    and
                    parsed
                    and validation_errors
                    and _env_flag(
                        "ENNOSCHOLAR_PHASE5_DETERMINISTIC_CITATION_REPAIR",
                        False,
                    )
                    and validation_errors
                    <= {
                        "missing_required_citations",
                        "missing_verrou_subsection_citations",
                        "section_too_short",
                    }
                    and (
                        validation.get("missing_required_citations")
                        or validation.get(
                            "missing_verrou_subsection_citations"
                        )
                    )
                ):
                    repaired = dict(parsed)
                    additions: List[str] = []
                    for missing_citation in validation.get(
                        "missing_required_citations"
                    ) or []:
                        unit = next(
                            (
                                row
                                for row in local_evidence
                                if row.get("citation_label") == missing_citation
                                and clean_sentence(row.get("text"), 700)
                            ),
                            None,
                        )
                        if not unit:
                            continue
                        sentence = _evidence_sentence(unit)
                        if sentence:
                            additions.append(sentence)
                    if additions:
                        repaired["content"] = (
                            clean_text(parsed.get("content"), 200000)
                            + "\n\n"
                            + " ".join(additions)
                        ).strip()

                    repaired_subsections = [
                        dict(row)
                        for row in parsed.get("subsections") or []
                        if isinstance(row, dict)
                    ]
                    subsection_repairs: List[Dict[str, Any]] = []
                    for missing_row in validation.get(
                        "missing_verrou_subsection_citations"
                    ) or []:
                        if not isinstance(missing_row, dict):
                            continue
                        verrou_id = clean_text(
                            missing_row.get("verrou_id"),
                            120,
                        )
                        subsection = next(
                            (
                                row
                                for row in repaired_subsections
                                if clean_text(
                                    row.get("verrou_id"),
                                    120,
                                )
                                == verrou_id
                            ),
                            None,
                        )
                        if not subsection:
                            continue
                        sentences: List[str] = []
                        for missing_citation in missing_row.get(
                            "missing_citations"
                        ) or []:
                            candidates = [
                                row
                                for row in local_evidence
                                if row.get("citation_label")
                                == missing_citation
                                and clean_sentence(row.get("text"), 700)
                            ]
                            if not candidates:
                                continue
                            unit = max(
                                candidates,
                                key=lambda row: int(
                                    verrou_id
                                    in {
                                        clean_text(value, 120)
                                        for value in as_list(
                                            row.get("verrou_ids") or []
                                        )
                                    }
                                ),
                            )
                            sentence = _evidence_sentence(unit)
                            if sentence:
                                sentences.append(sentence)
                        if sentences:
                            subsection["content"] = (
                                clean_text(
                                    subsection.get("content"),
                                    200000,
                                )
                                + "\n\n"
                                + " ".join(sentences)
                            ).strip()
                            subsection_repairs.append(
                                {
                                    "verrou_id": verrou_id,
                                    "added_citations": missing_row.get(
                                        "missing_citations"
                                    )
                                    or [],
                                }
                            )
                    repaired["subsections"] = repaired_subsections

                    if additions or subsection_repairs:
                        repaired_validation = _validate_generated_section(
                            repaired,
                            section,
                            total_sections=total_sections,
                            evidence_units=local_evidence,
                        )
                        deterministic_repair = {
                            "applied": True,
                            "kind": "missing_required_citations",
                            "added_citations": validation.get(
                                "missing_required_citations"
                            )
                            or [],
                            "subsection_repairs": subsection_repairs,
                            "validation": repaired_validation,
                        }
                        repaired_errors = set(
                            repaired_validation.get("errors") or []
                        )
                        if not {
                            "missing_required_citations",
                            "missing_verrou_subsection_citations",
                        } & repaired_errors:
                            parsed = repaired
                            validation = repaired_validation

                depth_enrichment: List[Dict[str, Any]] = []
                if (
                    not guided_iterative_publication
                    and
                    parsed
                    and set(validation.get("errors") or [])
                    == {"section_too_short"}
                    and _env_flag(
                        "ENNOSCHOLAR_PHASE5_ENABLE_DEPTH_ENRICHMENT",
                        False,
                    )
                ):
                    for enrichment_round in range(1, 2):
                        missing_words = max(
                            300,
                            int(validation.get("minimum_words") or 0)
                            - int(validation.get("word_count") or 0)
                            + 120,
                        )
                        current_content = (
                            clean_text(parsed.get("content"), 200000)
                            + "\n\n"
                            + "\n\n".join(
                                clean_text(row.get("content"), 100000)
                                for row in parsed.get("subsections") or []
                                if isinstance(row, dict)
                            )
                        ).strip()
                        enrichment_prompt = _build_section_enrichment_prompt(
                            blueprint,
                            section,
                            local_evidence,
                            current_content,
                            missing_words=missing_words,
                        )
                        enrichment_raw = client.generate(
                            enrichment_prompt,
                            temperature=0.08,
                            max_output_tokens=max(
                                1200,
                                min(4000, missing_words * 2),
                            ),
                            retries=0,
                            json_mode=True,
                            request_name=(
                                f"ennoscholar:phase5:section:{index}:"
                                f"enrich:{enrichment_round}"
                            ),
                        )
                        enrichment_payload = _extract_json_response(
                            enrichment_raw
                        )
                        addition = clean_text(
                            enrichment_payload.get("addition"),
                            100000,
                        )
                        addition_citations = set(
                            citations_from_text(addition)
                        )
                        unknown_addition_citations = sorted(
                            addition_citations
                            - set(section.get("available_citations") or [])
                        )
                        if (
                            not addition
                            or unknown_addition_citations
                            or len(
                                re.findall(
                                    r"\b[\wÀ-ÿ'-]+\b",
                                    addition,
                                )
                            )
                            < 120
                        ):
                            depth_enrichment.append(
                                {
                                    "round": enrichment_round,
                                    "accepted": False,
                                    "unknown_citations": (
                                        unknown_addition_citations
                                    ),
                                    "llm": client.get_last_generation_meta(),
                                }
                            )
                            continue
                        enriched = dict(parsed)
                        enriched["content"] = (
                            clean_text(parsed.get("content"), 200000)
                            + "\n\n"
                            + addition
                        ).strip()
                        enriched_validation = _validate_generated_section(
                            enriched,
                            section,
                            total_sections=total_sections,
                            evidence_units=local_evidence,
                        )
                        depth_enrichment.append(
                            {
                                "round": enrichment_round,
                                "accepted": True,
                                "added_words": len(
                                    re.findall(
                                        r"\b[\wÀ-ÿ'-]+\b",
                                        addition,
                                    )
                                ),
                                "validation": enriched_validation,
                                "llm": client.get_last_generation_meta(),
                            }
                        )
                        parsed = enriched
                        validation = enriched_validation
                        if validation.get("ok"):
                            break
                independent_semantic_verifier: Dict[str, Any] = {
                    "used": False,
                    "status": "not_run_due_to_blocking_errors",
                    "passed": False,
                    "issues": [],
                }
                escalation_blockers = _section_escalation_blockers(
                    validation,
                    section,
                )
                if parsed and not escalation_blockers:
                    source_roles = {
                        citation: (
                            blueprint.get("source_roles") or {}
                        ).get(citation)
                        for citation in (
                            section.get(
                                "available_citations"
                            )
                            or []
                        )
                    }
                    verifier_policy = (
                        _section_requires_independent_llm_verifier(
                            parsed,
                            section,
                            validation,
                            source_roles,
                        )
                    )

                    if verifier_policy.get("required"):
                        independent_semantic_verifier = (
                            _call_independent_semantic_verifier(
                                verifier_client,
                                generated=parsed,
                                section=section,
                                evidence_units=local_evidence,
                                source_roles=source_roles,
                            )
                        )
                        independent_semantic_verifier[
                            "policy"
                        ] = verifier_policy
                    else:
                        independent_semantic_verifier = {
                            "used": False,
                            "status": "skipped_low_risk_deterministic_pass",
                            "passed": True,
                            "issues": [],
                            "required": False,
                            "policy": verifier_policy,
                        }

                    validation[
                        "independent_semantic_verifier"
                    ] = independent_semantic_verifier
                    if not independent_semantic_verifier.get(
                        "passed"
                    ):
                        validation["errors"] = list(
                            dict.fromkeys(
                                [
                                    *(
                                        validation.get("errors")
                                        or []
                                    ),
                                    (
                                        "independent_semantic_"
                                        "verifier_rejected"
                                    ),
                                ]
                            )
                        )
                        validation["ok"] = False
                escalation_blockers = _section_escalation_blockers(
                    validation,
                    section,
                )
                if parsed:
                    latest_llm_candidate = parsed
                    latest_llm_validation = validation
                    if not _section_publication_blockers(validation, section):
                        latest_publishable_candidate = parsed
                        latest_publishable_validation = validation
                attempts.append(
                    {
                        "attempt": attempt,
                        "attempt_stage": attempt_stage,
                        "premium_escalation": (
                            attempt_stage == "premium_escalation"
                        ),
                        "escalation_blockers": escalation_blockers,
                        "generated_section": parsed,
                        "validation": validation,
                        "independent_semantic_verifier": (
                            independent_semantic_verifier
                        ),
                        "deterministic_citation_repair": deterministic_repair,
                        "citation_addition_repair": (
                            citation_addition_repair
                        ),
                        "depth_enrichment": depth_enrichment,
                        "llm": base_llm_meta,
                    }
                )
                if parsed and not escalation_blockers:
                    accepted = parsed
                    if guided_iterative_publication:
                        accepted_mode = "llm_guided_new_draft_as_generated"
                        if not validation.get("ok"):
                            advisory_sections_count += 1
                    elif validation.get("ok"):
                        accepted_mode = "llm_verified"
                    else:
                        accepted_mode = (
                            "llm_mini_with_advisories"
                            if attempt_stage != "premium_escalation"
                            else "llm_premium_with_advisories"
                        )
                        advisory_sections_count += 1
                    if checkpoint_path:
                        write_json(
                            checkpoint_path,
                            {
                                "payload_type": "phase5_section_checkpoint_v1",
                                "fingerprint": checkpoint_fingerprint,
                                "generated_at": now_iso(),
                                "section": accepted,
                                "validation": validation,
                                "independent_semantic_verifier": (
                                    independent_semantic_verifier
                                ),
                                "attempt_stage": attempt_stage,
                                "llm": base_llm_meta,
                            },
                        )
                    break
                feedback = {
                    "validation": validation,
                    "escalation_blockers": escalation_blockers,
                    "rejected_draft": parsed,
                }
            except Exception as exc:
                attempts.append(
                    {
                        "attempt": attempt,
                        "attempt_stage": attempt_stage,
                        "premium_escalation": (
                            attempt_stage == "premium_escalation"
                        ),
                        "error": (
                            f"{type(exc).__name__}: {exc}"
                        ),
                    }
                )
                if type(exc).__name__ == "BudgetLimitExceeded":
                    return {}, {
                        "used": bool(
                            generated_sections or attempts
                        ),
                        "status": "cost_budget_reached",
                        "partial": bool(
                            generated_sections
                        ),
                        "completed_sections_count": len(
                            generated_sections
                        ),
                        "total_sections_count": total_sections,
                        "failed_section_index": index,
                        "failed_section_id": section_id,
                        "progress_markdown_path": (
                            str(progress_path)
                            if progress_path
                            else ""
                        ),
                        "sections": reports,
                        "mini_repairs_count": mini_repairs_count,
                        "premium_escalations_count": (
                            premium_escalations_count
                        ),
                        "budget_guard_message": str(exc),
                    }
                feedback = {
                    "errors": ["llm_error"],
                    "detail": str(exc),
                }
        if not accepted and latest_llm_candidate:
            language_errors = {
                "raw_extraction_fragment",
                "non_french_or_raw_source_fragment",
            } & set(latest_llm_validation.get("errors") or [])
            if language_errors:
                try:
                    cleanup_raw = client.generate(
                        _build_language_cleanup_prompt(
                            section,
                            latest_llm_candidate,
                        ),
                        temperature=0.05,
                        max_output_tokens=max(
                            1400,
                            min(
                                4200,
                                int(
                                    _section_target_words(
                                        section,
                                        total_sections,
                                    )
                                    * 1.7
                                ),
                            ),
                        ),
                        retries=0,
                        json_mode=True,
                        request_name=(
                            f"ennoscholar:phase5:section:{index}:"
                            "french-cleanup"
                        ),
                    )
                    cleanup_candidate = _extract_json_response(cleanup_raw)
                    cleanup_validation = _validate_generated_section(
                        cleanup_candidate,
                        section,
                        total_sections=total_sections,
                        evidence_units=local_evidence,
                    )
                    attempts.append(
                        {
                            "attempt": "french_cleanup",
                            "generated_section": cleanup_candidate,
                            "validation": cleanup_validation,
                            "llm": client.get_last_generation_meta(),
                        }
                    )
                    if (
                        cleanup_candidate
                        and not _section_publication_blockers(
                            cleanup_validation,
                            section,
                        )
                    ):
                        latest_publishable_candidate = cleanup_candidate
                        latest_publishable_validation = cleanup_validation
                except Exception as cleanup_exc:
                    attempts.append(
                        {
                            "attempt": "french_cleanup",
                            "error": (
                                f"{type(cleanup_exc).__name__}: "
                                f"{cleanup_exc}"
                            ),
                        }
                    )

            if latest_publishable_candidate:
                accepted = latest_publishable_candidate
                accepted_mode = "llm_repaired_with_advisories"
                advisory_sections_count += 1
                attempts.append(
                    {
                        "attempt": "retain_new_llm_draft",
                        "generated_section": accepted,
                        "validation": latest_publishable_validation,
                        "advisory_only": True,
                        "reason": (
                            "La nouvelle rédaction LLM est conservée; les "
                            "contrôles restants sont consignés comme conseils "
                            "et ne déclenchent aucun remplacement déterministe."
                        ),
                    }
                )

        reports.append(
            {
                "section_id": section.get("section_id"),
                "ok": bool(accepted),
                "writer_mode": accepted_mode or "llm_generation_failed",
                "attempts": attempts,
            }
        )
        if not accepted:
            # Le LLM a réellement été utilisé même si cette section n'a pas
            # passé les validations. Les sections précédentes restent dans
            # state_of_art_draft_in_progress.md et leurs checkpoints JSON.
            had_llm_activity = bool(
                generated_sections
                or attempts
                or any((row.get("attempts") or []) for row in reports)
            )
            return {}, {
                "used": had_llm_activity,
                "status": "section_generation_failed",
                "partial": bool(generated_sections),
                "completed_sections_count": len(generated_sections),
                "total_sections_count": total_sections,
                "failed_section_index": index,
                "failed_section_id": section_id,
                "progress_markdown_path": (
                    str(progress_path) if progress_path else ""
                ),
                "sections": reports,
                "mini_repairs_count": mini_repairs_count,
                "premium_escalations_count": (
                    premium_escalations_count
                ),
            }
        generated_sections.append(accepted)
        if progress_path:
            progress_draft = {
                "title": f"\u00c9tat de l'art scientifique \u2014 {blueprint.get('project')}",
                "sections": generated_sections,
            }
            progress_markdown = draft_to_markdown(
                progress_draft,
                {"ok": False},
            )
            progress_markdown = (
                f"> R\u00e9daction en cours \u2014 section {index}/"
                f"{len(blueprint.get('sections') or [])}\n\n"
                + progress_markdown
            )
            write_text(progress_path, progress_markdown)
        previous_tail = (
            clean_text(accepted.get("content"), 100000)[-1400:]
            + " "
            + " ".join(
                clean_text(row.get("content"), 100000)[-500:]
                for row in accepted.get("subsections") or []
                if isinstance(row, dict)
            )
        )[-1800:]
    return {
        "title": f"État de l'art scientifique — {blueprint.get('project')}",
        "sections": generated_sections,
    }, {
        "used": True,
        "status": "ok",
        "mode": (
            "sectional_llm_with_advisories"
            if advisory_sections_count
            else "sectional_long_form"
        ),
        "deterministic_fallback_sections_count": 0,
        "advisory_sections_count": advisory_sections_count,
        "mini_repairs_count": mini_repairs_count,
        "premium_escalations_count": premium_escalations_count,
        "model_policy": {
            "draft_model": configured_model,
            "mini_attempts_before_premium": mini_attempts,
            "premium_enabled": premium_enabled,
            "premium_model": (
                escalation_model if premium_enabled else ""
            ),
            "length_only_never_escalates": True,
            "escalation_requires_blocking_validation_error": True,
        },
        "all_sections_generated_by_llm": True,
        "sections": reports,
    }


def _extract_json_response(text: str) -> Dict[str, Any]:
    raw = clean_text(text, 300000)
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start:end + 1])
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
    return {}


def _http_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def call_writer_llm(prompt: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not _env_flag("ENNOSCHOLAR_PHASE5_ENABLE_LLM", True):
        return {}, {"used": False, "status": "disabled"}
    provider = clean_text(
        os.getenv("ENNOSCHOLAR_PHASE5_PROVIDER")
        or os.getenv("ENNOSMART_PHASE5_LLM_PROVIDER")
        or "openai",
        40,
    ).lower()
    model = clean_text(
        os.getenv("ENNOSCHOLAR_PHASE5_WRITER_MODEL")
        or os.getenv("ENNOSMART_PHASE5_WRITER_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "",
        200,
    )
    timeout = int(os.getenv("ENNOSCHOLAR_PHASE5_TIMEOUT_SECONDS", "600"))
    max_tokens = int(os.getenv("ENNOSCHOLAR_PHASE5_MAX_OUTPUT_TOKENS", "8000"))
    try:
        temperature = float(os.getenv("ENNOSCHOLAR_PHASE5_TEMPERATURE", "0.06"))
    except Exception:
        temperature = 0.06

    if provider == "none" or not model:
        return {}, {"used": False, "status": "provider_or_model_missing"}

    try:
        if provider == "ollama":
            base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
            response = _http_json(
                f"{base}/api/chat",
                {},
                {
                    "model": model,
                    "stream": False,
                    "format": "json",
                    "messages": [{"role": "user", "content": prompt}],
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                },
                timeout,
            )
            content = ((response.get("message") or {}).get("content") or "")
        else:
            if provider == "openrouter":
                key = os.getenv("OPENROUTER_API_KEY", "")
                base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            else:
                key = os.getenv("OPENAI_API_KEY", "")
                base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            if not key:
                return {}, {"used": False, "status": "api_key_missing", "provider": provider, "model": model}
            request_payload: Dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
            model_name = model.lower().rsplit("/", 1)[-1]
            is_gpt5 = model_name.startswith("gpt-5")
            if is_gpt5 and provider != "openrouter":
                request_payload["max_completion_tokens"] = max_tokens
            else:
                request_payload["max_tokens"] = max_tokens
            # Les modèles GPT-5 n'acceptent pas toujours une température non
            # standard. Elle est donc volontairement omise pour toute la famille.
            if not is_gpt5:
                request_payload["temperature"] = temperature
            response = _http_json(
                f"{base.rstrip('/')}/chat/completions",
                {"Authorization": f"Bearer {key}"},
                request_payload,
                timeout,
            )
            choices = response.get("choices") or []
            content = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        parsed = _extract_json_response(content)
        return parsed, {
            "used": bool(parsed),
            "status": "ok" if parsed else "invalid_json_response",
            "provider": provider,
            "model": model,
            "temperature_sent": not model.lower().rsplit("/", 1)[-1].startswith("gpt-5"),
        }
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return {}, {
            "used": False,
            "status": "llm_error",
            "provider": provider,
            "model": model,
            "error": repr(exc),
        }


# ---------------------------------------------------------------------------
# Validation, références et Markdown
# ---------------------------------------------------------------------------

def validate_draft(
    draft: Dict[str, Any],
    blueprint: Dict[str, Any],
    source_draft: Optional[Dict[str, Any]] = None,
    evidence_units: Optional[Sequence[Mapping[str, Any]]] = None,
    enforce_consultant_language: bool = False,
) -> Dict[str, Any]:
    del source_draft
    errors: List[str] = []
    expected_sections = blueprint.get("sections") or []
    actual_sections = draft.get("sections") if isinstance(draft.get("sections"), list) else []
    expected_ids = [section["section_id"] for section in expected_sections]
    actual_ids = [clean_text(section.get("section_id"), 120) for section in actual_sections if isinstance(section, dict)]
    if actual_ids != expected_ids:
        errors.append("section_order_or_ids_mismatch")

    expected_titles = [
        clean_sentence(section.get("title"), 700)
        for section in expected_sections
    ]
    actual_titles = [clean_sentence(section.get("title"), 700) for section in actual_sections if isinstance(section, dict)]
    if actual_titles != expected_titles:
        errors.append("section_titles_mismatch")

    expected_verrous = [
        (
            clean_text(verrou.get("verrou_id"), 120),
            clean_sentence(verrou.get("verrou_title"), 700),
        )
        for section in expected_sections
        for verrou in section.get("verrous") or []
    ]
    actual_verrous = [
        (
            clean_text(subsection.get("verrou_id"), 120),
            clean_sentence(subsection.get("title"), 700),
        )
        for section in actual_sections
        if isinstance(section, dict)
        for subsection in section.get("subsections") or []
        if isinstance(subsection, dict)
    ]
    if actual_verrous != expected_verrous:
        errors.append("verrou_coverage_or_titles_mismatch")

    body = " ".join(
        [
            clean_text(section.get("content"), 100000)
            + " "
            + " ".join(
                clean_text(subsection.get("content"), 100000)
                for subsection in section.get("subsections") or []
                if isinstance(subsection, dict)
            )
            for section in actual_sections
            if isinstance(section, dict)
        ]
    )
    raw_extraction_fragments = _raw_extraction_fragments(body)
    if raw_extraction_fragments:
        errors.append("raw_extraction_fragment")
    non_french_raw_fragments = (
        _non_french_raw_fragments(body)
        if enforce_consultant_language
        else []
    )
    if non_french_raw_fragments:
        errors.append("non_french_or_raw_source_fragment")
    used = citations_from_text(body)
    allowed = set(blueprint.get("allowed_citations") or [])
    unknown = sorted(set(used) - allowed)
    if unknown:
        errors.append("unknown_citations")

    required_available = set(blueprint.get("required_citations") or []) & set(
        blueprint.get("available_evidence_citations") or []
    )
    missing = citation_sort(required_available - set(used))
    if missing:
        errors.append("missing_required_citations")

    forbidden = sorted(
        marker
        for marker in FORBIDDEN_FINAL_MARKERS
        if marker in body.lower()
    )
    if forbidden:
        errors.append("internal_markers_in_final_text")

    empty_sections = [
        section.get("section_id")
        for section in actual_sections
        if isinstance(section, dict)
        and not clean_text(section.get("content"))
        and not any(clean_text(item.get("content")) for item in section.get("subsections") or [] if isinstance(item, dict))
    ]
    if empty_sections:
        errors.append("empty_sections")

    semantic_section_reports: List[Dict[str, Any]] = []
    if evidence_units is not None:
        for expected_section, actual_section in zip(
            expected_sections,
            actual_sections,
        ):
            if not isinstance(actual_section, Mapping):
                continue
            report = _semantic_claim_audit(
                actual_section,
                expected_section,
                evidence_units,
            )
            semantic_section_reports.append(
                {
                    "section_id": expected_section.get("section_id"),
                    **report,
                }
            )
        if any(not row.get("ok") for row in semantic_section_reports):
            errors.append("unsupported_or_misattributed_claims")

    required_global = citation_sort(required_available)
    detected_global = citation_sort(set(used) & allowed)
    per_verrou_coverage: List[Dict[str, Any]] = []
    actual_verrou_pairs = set(actual_verrous)
    required_by_verrou = (
        blueprint.get("required_citations_by_verrou")
        if isinstance(blueprint.get("required_citations_by_verrou"), dict)
        else {}
    )
    actual_subsections_by_id = {
        clean_text(subsection.get("verrou_id"), 120): subsection
        for section in actual_sections
        if isinstance(section, dict)
        for subsection in section.get("subsections") or []
        if isinstance(subsection, dict)
        and clean_text(subsection.get("verrou_id"), 120)
    }
    for index, (verrou_id, verrou_title) in enumerate(
        expected_verrous,
        1,
    ):
        required_for_verrou = citation_sort(
            set(required_by_verrou.get(verrou_id) or []) & allowed
        )
        verrou_subsection = actual_subsections_by_id.get(verrou_id) or {}
        all_detected_in_verrou = citation_sort(
            citations_from_text(
                clean_text(verrou_subsection.get("content"), 200000)
            )
        )
        detected_for_verrou = citation_sort(
            set(required_for_verrou) & set(all_detected_in_verrou)
        )
        missing_for_verrou = citation_sort(
            set(required_for_verrou) - set(detected_for_verrou)
        )
        per_verrou_coverage.append(
            {
                "verrou_index": index,
                "verrou_id": verrou_id,
                "verrou_title": verrou_title,
                "required_citations": required_for_verrou,
                "detected_citations_in_verrou_section": detected_for_verrou,
                "all_citations_in_verrou_section": all_detected_in_verrou,
                "missing_citations": missing_for_verrou,
                "coverage_ok": (
                    (verrou_id, verrou_title) in actual_verrou_pairs
                    and not missing_for_verrou
                ),
            }
        )

    passed = not errors
    return {
        "ok": passed,
        "passed": passed,
        "errors": errors,
        "unknown_citations": unknown,
        "missing_required_citations": missing,
        "detected_citations": used,
        "forbidden_markers": forbidden,
        "raw_extraction_fragments": raw_extraction_fragments,
        "empty_sections": empty_sections,
        "expected_verrous": expected_verrous,
        "actual_verrous": actual_verrous,
        "coverage_required_citations": required_global,
        "citations_detected": detected_global,
        "coverage_required_count": len(required_global),
        "citations_detected_count": len(detected_global),
        "per_verrou_coverage": per_verrou_coverage,
        "verrou_coverage_ok": bool(per_verrou_coverage)
        and all(row["coverage_ok"] for row in per_verrou_coverage),
        "semantic_section_reports": semantic_section_reports,
        "semantic_claims_ok": not any(
            not row.get("ok") for row in semantic_section_reports
        ),
        "citation_entity_mismatches": [
            issue
            for row in semantic_section_reports
            for issue in row.get("entity_mismatches") or []
        ],
        "unsupported_numeric_values": [
            issue
            for row in semantic_section_reports
            for issue in row.get("numeric_mismatches") or []
        ],
        "uncited_scientific_claims": [
            issue
            for row in semantic_section_reports
            for issue in row.get("uncited_scientific_claims") or []
        ],
        "missing_direct_evidence_disclosures": [
            issue
            for row in semantic_section_reports
            for issue in row.get(
                "missing_direct_evidence_disclosures"
            )
            or []
        ],
        "unused_allowed_citations": citation_sort(
            set(blueprint.get("allowed_citations") or []) - set(used)
        ),
    }


_LLM_PUBLICATION_ADVISORY_ERRORS = {
    # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
    # Une citation obligatoire manquante peut encore être réparée par les
    # mécanismes existants. Une sur-affirmation scientifique reste bloquante.
    "missing_required_citations",
    # Le contrôleur sémantique déterministe repère aussi des formulations de
    # cadrage ou de synthèse. Elles restent visibles pour révision, tandis que
    # le garde CIR dédié continue de bloquer les vraies sur-affirmations.
    "unsupported_or_misattributed_claims",
    # END ENNOSCHOLAR_CIR_QUALITY_V3
}


def _publication_guard_for_new_llm(
    strict_guard: Mapping[str, Any],
    *,
    guided_conversation: bool = False,
) -> Dict[str, Any]:
    """Sépare publication du nouveau texte et diagnostics internes.

    Le workflow standard conserve ses validations de publication. Dans le chat
    guidé, le nouveau brouillon non vide est rendu tel qu'il est et tous les
    constats de qualité restent internes pour les futures demandes de révision.
    """
    strict_errors = list(strict_guard.get("errors") or [])
    if guided_conversation:
        advisory_errors = strict_errors
        blocking_errors: List[str] = []
    else:
        advisory_errors = [
            error
            for error in strict_errors
            if error in _LLM_PUBLICATION_ADVISORY_ERRORS
        ]
        blocking_errors = [
            error
            for error in strict_errors
            if error not in _LLM_PUBLICATION_ADVISORY_ERRORS
        ]
    output = dict(strict_guard)
    output.update(
        {
            "ok": not blocking_errors,
            "passed": not blocking_errors,
            "errors": blocking_errors,
            "strict_ok": bool(strict_guard.get("ok")),
            "strict_errors": strict_errors,
            "advisory_errors": advisory_errors,
            "scientific_review_recommended": bool(advisory_errors),
            "new_llm_draft_preserved": True,
            "publication_policy": (
                "guided_iterative_new_draft_as_generated"
                if guided_conversation
                else "validated_document"
            ),
        }
    )
    return output


def _editorial_quality_report(draft: Mapping[str, Any]) -> Dict[str, Any]:
    sections = [
        section
        for section in draft.get("sections") or []
        if isinstance(section, Mapping)
    ]
    sentence_rows: List[Tuple[str, str]] = []
    for section in sections:
        section_id = clean_text(section.get("section_id"), 120)
        bodies = [clean_text(section.get("content"), 200000)]
        bodies.extend(
            clean_text(item.get("content"), 200000)
            for item in section.get("subsections") or []
            if isinstance(item, Mapping)
        )
        for body in bodies:
            for sentence in _claim_sentences(body):
                normalized = _semantic_normalize(
                    re.sub(r"\[(?:A\d+)\]", " ", sentence)
                )
                if len(normalized.split()) >= 10:
                    sentence_rows.append((section_id, normalized))

    exact_counts: Dict[str, int] = {}
    for _, sentence in sentence_rows:
        exact_counts[sentence] = exact_counts.get(sentence, 0) + 1
    exact_duplicates = [
        {"sentence": sentence, "count": count}
        for sentence, count in exact_counts.items()
        if count > 1
    ]

    near_duplicates: List[Dict[str, Any]] = []
    bounded_rows = sentence_rows[:320]
    for left_index, (left_section, left) in enumerate(bounded_rows):
        for right_section, right in bounded_rows[left_index + 1 :]:
            if left_section == right_section:
                continue
            similarity = _similarity(left, right)
            if similarity >= 0.84:
                near_duplicates.append(
                    {
                        "left_section": left_section,
                        "right_section": right_section,
                        "similarity": round(similarity, 3),
                        "left": left[:500],
                        "right": right[:500],
                    }
                )
                if len(near_duplicates) >= 30:
                    break
        if len(near_duplicates) >= 30:
            break

    body = " ".join(
        clean_text(section.get("content"), 200000)
        + " "
        + " ".join(
            clean_text(item.get("content"), 200000)
            for item in section.get("subsections") or []
            if isinstance(item, Mapping)
        )
        for section in sections
    )
    article_catalogue_markers = len(
        re.findall(
            r"\b(?:l['’]article|les auteurs|cette étude|ce papier)\b",
            body,
            flags=re.I,
        )
    )
    sentence_count = max(1, len(sentence_rows))
    near_duplicate_ratio = len(near_duplicates) / sentence_count
    catalogue_ratio = article_catalogue_markers / sentence_count
    raw_extraction_fragments = _raw_extraction_fragments(body)
    non_french_raw_fragments = _non_french_raw_fragments(body)
    issues: List[str] = []
    if exact_duplicates:
        issues.append("exact_sentence_repetition")
    if near_duplicate_ratio > 0.08:
        issues.append("cross_section_repetition")
    if catalogue_ratio > 0.08:
        issues.append("article_catalogue_style")
    if raw_extraction_fragments:
        issues.append("raw_extraction_fragment")
    if non_french_raw_fragments:
        issues.append("non_french_or_raw_source_fragment")
    return {
        "passed": not issues,
        "issues": issues,
        "sentences_analyzed": len(sentence_rows),
        "exact_duplicates": exact_duplicates[:20],
        "near_duplicates": near_duplicates,
        "near_duplicate_ratio": round(near_duplicate_ratio, 4),
        "article_catalogue_markers": article_catalogue_markers,
        "article_catalogue_ratio": round(catalogue_ratio, 4),
        "raw_extraction_fragments": raw_extraction_fragments,
        "non_french_raw_fragments": non_french_raw_fragments,
        "non_french_raw_fragments": non_french_raw_fragments,
    }


def _authors_text(value: Any) -> str:
    if isinstance(value, list):
        names = []
        for item in value[:12]:
            if isinstance(item, dict):
                name = clean_sentence(item.get("name") or item.get("author"), 120)
            else:
                name = clean_sentence(item, 120)
            if name:
                names.append(name)
        return ", ".join(names)
    return clean_sentence(value, 600)


def build_references_for_citations(
    citations: Sequence[str],
    cards: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_label = {card["citation_label"]: card for card in cards}
    references: List[Dict[str, Any]] = []
    for citation in citation_sort(citations):
        card = by_label.get(citation)
        if not card:
            continue
        references.append(
            {
                "citation_label": citation,
                "authors": _authors_text(card.get("authors")),
                "title": clean_sentence(card.get("title"), 700),
                "year": card.get("year"),
                "venue": clean_sentence(card.get("venue") or card.get("journal"), 300),
                "doi": clean_sentence(card.get("doi"), 200),
                "url": clean_sentence(
                    card.get("url")
                    or card.get("open_access_url")
                    or card.get("pdf_url"),
                    1000,
                ),
            }
        )
    return references


def _visual_tokens(value: Any) -> Set[str]:
    text = unicodedata.normalize("NFKD", clean_text(value, 30000).casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", text)
        if token
        not in {
            "the", "and", "for", "with", "from", "this", "that", "these",
            "une", "des", "dans", "pour", "avec", "sur", "les", "est", "sont",
            "figure", "table", "source", "article", "section",
        }
    }


def _visual_similarity(left: Any, right: Any) -> float:
    left_tokens = _visual_tokens(left)
    right_tokens = _visual_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))


def _draft_section_visual_text(
    section: Mapping[str, Any],
    contract_section: Mapping[str, Any],
) -> str:
    return " ".join(
        [
            clean_text(section.get("title"), 1000),
            clean_text(section.get("content"), 12000),
            " ".join(
                clean_text(item.get("content"), 5000)
                for item in section.get("subsections") or []
                if isinstance(item, Mapping)
            ),
            clean_text(contract_section.get("objective"), 3000),
            clean_text(contract_section.get("instructions") or [], 3000),
        ]
    )


def _multilingual_visual_similarities(
    section_texts: Mapping[str, str],
    candidate_texts: Mapping[str, str],
) -> Dict[Tuple[str, str], float]:
    """Similarité texte multilingue, sans appel LLM ni analyse de l'image."""

    if not _env_flag("ENNOSCHOLAR_VISUAL_MULTILINGUAL_MATCHING", True):
        return {}
    section_ids = list(section_texts)
    visual_ids = list(candidate_texts)
    texts = [section_texts[key][:8000] for key in section_ids] + [
        candidate_texts[key][:5000] for key in visual_ids
    ]
    if not texts:
        return {}
    try:
        from modules.RAG.vector_store import encode_texts

        vectors = encode_texts(texts)
        if len(vectors) != len(texts):
            return {}
        split = len(section_ids)
        output: Dict[Tuple[str, str], float] = {}
        for section_index, section_id in enumerate(section_ids):
            left = vectors[section_index]
            for visual_index, visual_id in enumerate(visual_ids):
                right = vectors[split + visual_index]
                score = sum(float(a) * float(b) for a, b in zip(left, right))
                output[(section_id, visual_id)] = max(0.0, min(1.0, score))
        return output
    except Exception:
        return {}


def _normalize_writer_public_text(value: Any, limit: int = 100000) -> str:
    """Nettoyage déterministe du texte public, sans appel LLM."""
    text = clean_text(value, limit)

    # Corrige les séparateurs littéraux observés dans certains drafts.
    text = text.replace("\\n\\n", "\n\n")

    # Si un bracket contient à la fois des citations A# valides et des IDs
    # techniques hexadécimaux, ne conserver que les citations publiques A#.
    def _clean_bracket(match: Any) -> str:
        inside = str(match.group(1) or "")
        labels = citation_sort(
            re.findall(r"\bA\s*\d+\b", inside, flags=re.I)
        )
        has_internal_hex = bool(
            re.search(r"\b[a-f0-9]{12,}\b", inside, flags=re.I)
        )
        if labels and has_internal_hex:
            return "[" + ", ".join(labels) + "]"
        return match.group(0)

    text = re.sub(r"\[([^\[\]]+)\]", _clean_bracket, text)
    return text.strip()


def _split_visual_paragraphs(value: Any) -> List[str]:
    text = _normalize_writer_public_text(value, 100000)
    if not text:
        return []
    paragraphs = [
        part.strip()
        for part in re.split(r"\n\s*\n", text)
        if part.strip()
    ]
    return paragraphs or [text]


def _paragraph_visual_anchors(
    draft: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    anchors: List[Dict[str, Any]] = []

    for section_index, section in enumerate(
        draft.get("sections") or []
    ):
        if not isinstance(section, Mapping):
            continue

        section_id = clean_text(
            section.get("section_id"), 160
        )

        for paragraph_index, paragraph in enumerate(
            _split_visual_paragraphs(section.get("content"))
        ):
            anchor_key = (
                f"{section_id}|section|0|{paragraph_index}"
            )
            anchors.append(
                {
                    "anchor_key": anchor_key,
                    "section_id": section_id,
                    "section_index": section_index,
                    "content_scope": "section",
                    "subsection_index": None,
                    "paragraph_index": paragraph_index,
                    "paragraph_text": paragraph,
                    "citations": set(
                        citations_from_text(paragraph)
                    ),
                }
            )

        for subsection_index, subsection in enumerate(
            section.get("subsections") or []
        ):
            if not isinstance(subsection, Mapping):
                continue

            for paragraph_index, paragraph in enumerate(
                _split_visual_paragraphs(
                    subsection.get("content")
                )
            ):
                anchor_key = (
                    f"{section_id}|subsection|"
                    f"{subsection_index}|{paragraph_index}"
                )
                anchors.append(
                    {
                        "anchor_key": anchor_key,
                        "section_id": section_id,
                        "section_index": section_index,
                        "content_scope": "subsection",
                        "subsection_index": subsection_index,
                        "paragraph_index": paragraph_index,
                        "paragraph_text": paragraph,
                        "citations": set(
                            citations_from_text(paragraph)
                        ),
                    }
                )

    return anchors


def build_visual_placements(
    draft: Dict[str, Any],
    blueprint: Dict[str, Any],
    cards_payload: Dict[str, Any],
    cards: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Place les figures au niveau du paragraphe qu'elles documentent.

    Règles article:
    - la figure vient d'une Article Card sourcée ;
    - le paragraphe cible doit citer CE MÊME article ;
    - caption/context et paragraphe doivent dépasser le seuil sémantique ;
    - au maximum une figure retenue par section ;
    - aucun appel LLM n'est effectué pour le placement.

    Règles document projet:
    - aucune nouvelle preuve scientifique ;
    - proximité sémantique obligatoire.
    """

    if os.getenv(
        "ENNOSCHOLAR_PHASE5_INCLUDE_ORIGINAL_FIGURES",
        "1",
    ).strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return []

    article_min_similarity = float(
        os.getenv(
            "ENNOSCHOLAR_PHASE5_ARTICLE_FIGURE_MIN_SIMILARITY",
            "0.055",
        )
    )
    project_min_similarity = float(
        os.getenv(
            "ENNOSCHOLAR_PHASE5_PROJECT_FIGURE_MIN_SIMILARITY",
            "0.055",
        )
    )

    card_by_label = {
        normalize_citation_label(
            card.get("citation_label")
        ): card
        for card in cards
        if normalize_citation_label(
            card.get("citation_label")
        )
    }

    candidates: List[Dict[str, Any]] = []
    seen_visual_ids: Set[str] = set()

    for citation, card in card_by_label.items():
        for raw in card.get("visual_evidence") or []:
            if not isinstance(raw, dict):
                continue
            visual_id = clean_text(
                raw.get("visual_id"), 120
            )
            if (
                not visual_id
                or visual_id in seen_visual_ids
            ):
                continue
            item = dict(raw)
            item["citation_label"] = citation
            candidates.append(item)
            seen_visual_ids.add(visual_id)

    for raw in (
        cards_payload.get("project_visual_evidence") or []
    ):
        if not isinstance(raw, dict):
            continue
        visual_id = clean_text(
            raw.get("visual_id"), 120
        )
        if (
            not visual_id
            or visual_id in seen_visual_ids
        ):
            continue
        candidates.append(dict(raw))
        seen_visual_ids.add(visual_id)

    if not candidates:
        return []

    blueprint_sections = {
        clean_text(section.get("section_id"), 160):
            section
        for section in blueprint.get("sections") or []
        if isinstance(section, dict)
    }

    anchors = _paragraph_visual_anchors(draft)
    if not anchors:
        return []

    candidate_semantic_texts = {
        clean_text(candidate.get("visual_id"), 120):
            " ".join(
                [
                    clean_text(
                        candidate.get("figure_label"),
                        100,
                    ),
                    clean_text(
                        candidate.get("caption"),
                        1800,
                    ),
                    clean_text(
                        candidate.get("context"),
                        2600,
                    ),
                    clean_text(
                        candidate.get("source_title"),
                        1000,
                    ),
                ]
            )
        for candidate in candidates
        if clean_text(candidate.get("visual_id"), 120)
    }

    paragraph_semantic_texts = {
        anchor["anchor_key"]:
            anchor["paragraph_text"]
        for anchor in anchors
    }

    multilingual_similarities = (
        _multilingual_visual_similarities(
            paragraph_semantic_texts,
            candidate_semantic_texts,
        )
    )

    scored: List[
        tuple[float, int, Dict[str, Any]]
    ] = []

    for anchor in anchors:
        section_id = anchor["section_id"]
        contract_section = (
            blueprint_sections.get(section_id) or {}
        )

        section_verrous = {
            clean_text(value, 160)
            for value in (
                contract_section.get("verrou_ids")
                or [
                    verrou.get("verrou_id")
                    for verrou in (
                        contract_section.get("verrous")
                        or []
                    )
                    if isinstance(verrou, dict)
                ]
            )
            if clean_text(value, 160)
        }

        paragraph_text = anchor["paragraph_text"]

        for candidate in candidates:
            visual_id = clean_text(
                candidate.get("visual_id"), 120
            )
            if not visual_id:
                continue

            caption_text = " ".join(
                [
                    clean_text(
                        candidate.get("figure_label"),
                        100,
                    ),
                    clean_text(
                        candidate.get("caption"),
                        1800,
                    ),
                    clean_text(
                        candidate.get("context"),
                        2600,
                    ),
                    clean_text(
                        candidate.get("source_title"),
                        1000,
                    ),
                ]
            )

            quality = float(
                candidate.get("ranking_score")
                or candidate.get("quality_score")
                or 0.0
            )

            token_similarity = _visual_similarity(
                caption_text,
                paragraph_text,
            )
            similarity = max(
                token_similarity,
                multilingual_similarities.get(
                    (
                        anchor["anchor_key"],
                        visual_id,
                    ),
                    0.0,
                ),
            )

            citation = normalize_citation_label(
                candidate.get("citation_label")
            )

            target_verrous = {
                clean_text(value, 160)
                for value in (
                    candidate.get("target_verrous")
                    or []
                )
                if clean_text(value, 160)
            }

            if citation:
                # Condition forte demandée:
                # la source doit être citée DANS LE PARAGRAPHE,
                # pas seulement quelque part dans la section.
                if citation not in anchor["citations"]:
                    continue
                if similarity < article_min_similarity:
                    continue

                score = (
                    2.0
                    + quality
                    + similarity * 3.0
                )
                if (
                    section_verrous
                    and target_verrous & section_verrous
                ):
                    score += 0.35
            else:
                if similarity < project_min_similarity:
                    continue
                score = quality + similarity * 3.0
                if score < 0.65:
                    continue

            scored.append(
                (
                    score,
                    int(anchor["section_index"]),
                    {
                        "section_id": section_id,
                        "visual_id": visual_id,
                        "citation_label": citation,
                        "source_kind": clean_text(
                            candidate.get("source_kind"),
                            80,
                        ),
                        "source_title": clean_sentence(
                            candidate.get("source_title"),
                            900,
                        ),
                        "page": candidate.get("page"),
                        "figure_label": clean_sentence(
                            candidate.get("figure_label"),
                            100,
                        ),
                        "caption": clean_sentence(
                            candidate.get("caption"),
                            1800,
                        ),
                        "quality_score": quality,
                        "semantic_similarity": round(
                            similarity, 4
                        ),
                        "selection_score": round(
                            score, 4
                        ),
                        "content_scope":
                            anchor["content_scope"],
                        "subsection_index":
                            anchor["subsection_index"],
                        "paragraph_index":
                            anchor["paragraph_index"],
                        "anchor_key":
                            anchor["anchor_key"],
                        "anchor_excerpt":
                            clean_sentence(
                                paragraph_text,
                                420,
                            ),
                        "same_article_cited_in_paragraph":
                            bool(citation),
                        "original_figure_preserved": True,
                        "placement_policy":
                            "paragraph_citation_plus_semantic_match",
                    },
                )
            )

    # 0 = aucune limite globale.
    # Le plan peut avoir 5, 30, 80, 150 sections.
    configured_max_visuals = _env_int(
        "ENNOSCHOLAR_PHASE5_MAX_ORIGINAL_FIGURES",
        0,
        minimum=0,
        maximum=1000,
    )

    placements: List[Dict[str, Any]] = []
    occupied_sections: Set[str] = set()
    occupied_visuals: Set[str] = set()

    for _, _, placement in sorted(
        scored,
        key=lambda item: (-item[0], item[1]),
    ):
        if (
            configured_max_visuals > 0
            and len(placements)
            >= configured_max_visuals
        ):
            break

        # Évite une rédaction visuellement chargée:
        # une seule figure réellement utile par section.
        if placement["section_id"] in occupied_sections:
            continue
        if placement["visual_id"] in occupied_visuals:
            continue

        occupied_sections.add(
            placement["section_id"]
        )
        occupied_visuals.add(
            placement["visual_id"]
        )
        placements.append(placement)

    return sorted(
        placements,
        key=lambda placement: (
            next(
                (
                    index
                    for index, section
                    in enumerate(
                        draft.get("sections") or []
                    )
                    if isinstance(section, dict)
                    and clean_text(
                        section.get("section_id"), 160
                    )
                    == placement["section_id"]
                ),
                10_000,
            ),
            0
            if placement.get("content_scope")
            == "section"
            else 1,
            int(
                placement.get("subsection_index")
                or 0
            ),
            int(
                placement.get("paragraph_index")
                or 0
            ),
        ),
    )


def _visual_markdown_lines(
    placement: Mapping[str, Any],
) -> List[str]:
    visual_id = clean_text(
        placement.get("visual_id"), 120
    )
    if not visual_id:
        return []

    caption = clean_sentence(
        placement.get("caption"), 1800
    )
    figure_label = clean_sentence(
        placement.get("figure_label"), 100
    )
    alt = " — ".join(
        item
        for item in (figure_label, caption)
        if item
    )
    alt = (
        alt or "Figure scientifique sourcée"
    ).replace("]", "").replace("[", "")

    output = [
        f"![{alt}](ennoscholar-visual://{visual_id})",
        "",
    ]

    provenance: List[str] = []
    citation = normalize_citation_label(
        placement.get("citation_label")
    )

    if citation:
        provenance.append(f"source [{citation}]")
    else:
        source_title = clean_sentence(
            placement.get("source_title"), 700
        )
        if source_title:
            provenance.append(
                f"document projet « {source_title} »"
            )

    if placement.get("page"):
        provenance.append(
            f"page {placement['page']}"
        )

    legend = " — ".join(
        item
        for item in (figure_label, caption)
        if item
    ).rstrip(" .")

    if provenance:
        legend = (
            f"{legend}. {' ; '.join(provenance)}"
        )

    output.extend(
        [
            f"*{legend.strip().rstrip(' .')}.*",
            "",
        ]
    )
    return output


def _render_paragraphs_with_visuals(
    *,
    lines: List[str],
    content: Any,
    section_id: str,
    content_scope: str,
    subsection_index: Optional[int],
    placements_by_anchor:
        Mapping[str, List[Dict[str, Any]]],
) -> None:
    paragraphs = _split_visual_paragraphs(content)

    for paragraph_index, paragraph in enumerate(
        paragraphs
    ):
        lines.extend([paragraph, ""])

        anchor_key = (
            f"{section_id}|{content_scope}|"
            f"{0 if subsection_index is None else subsection_index}|"
            f"{paragraph_index}"
        )

        for placement in placements_by_anchor.get(
            anchor_key,
            [],
        ):
            lines.extend(
                _visual_markdown_lines(placement)
            )


def draft_to_markdown(
    draft: Dict[str, Any],
    guard: Dict[str, Any],
    references: Optional[List[Dict[str, Any]]] = None,
    visual_placements: Optional[
        List[Dict[str, Any]]
    ] = None,
) -> str:
    del guard

    lines = [
        f"# {clean_sentence(draft.get('title'), 1000)}",
        "",
    ]

    placements_by_anchor: Dict[
        str,
        List[Dict[str, Any]],
    ] = {}

    for placement in visual_placements or []:
        if not isinstance(placement, dict):
            continue
        anchor_key = clean_text(
            placement.get("anchor_key"),
            260,
        )
        if not anchor_key:
            continue
        placements_by_anchor.setdefault(
            anchor_key, []
        ).append(placement)

    for section in draft.get("sections") or []:
        if not isinstance(section, dict):
            continue

        section_id = clean_text(
            section.get("section_id"), 160
        )

        lines.extend(
            [
                f"## {clean_sentence(section.get('title'), 700)}",
                "",
            ]
        )

        _render_paragraphs_with_visuals(
            lines=lines,
            content=section.get("content"),
            section_id=section_id,
            content_scope="section",
            subsection_index=None,
            placements_by_anchor=
                placements_by_anchor,
        )

        for subsection_index, subsection in enumerate(
            section.get("subsections") or []
        ):
            if not isinstance(subsection, dict):
                continue

            lines.extend(
                [
                    f"### {clean_sentence(subsection.get('title'), 700)}",
                    "",
                ]
            )

            _render_paragraphs_with_visuals(
                lines=lines,
                content=subsection.get("content"),
                section_id=section_id,
                content_scope="subsection",
                subsection_index=subsection_index,
                placements_by_anchor=
                    placements_by_anchor,
            )

    if references:
        lines.extend(
            ["## Références utilisées", ""]
        )

        for reference in references:
            label = reference["citation_label"]
            elements = [
                reference.get("authors"),
                reference.get("title"),
                str(reference.get("year") or ""),
                reference.get("venue"),
                (
                    f"DOI : {reference['doi']}"
                    if reference.get("doi")
                    else ""
                ),
                reference.get("url"),
            ]
            lines.append(
                f"[{label}] "
                + ". ".join(
                    item
                    for item in elements
                    if item
                )
                + "."
            )

    return "\n".join(lines).strip() + "\n"

def _style_memory(*payloads: Dict[str, Any]) -> Dict[str, Any]:
    allowed_keys = {
        "tone",
        "register",
        "paragraph_length",
        "transition_style",
        "citation_style",
        "language",
        "style_summary",
    }
    output: Dict[str, Any] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in allowed_keys:
            value = payload.get(key)
            if value:
                output[key] = value
        for nested_key in ("style_profile", "writing_style", "profile"):
            nested = payload.get(nested_key)
            if isinstance(nested, dict):
                for key in allowed_keys:
                    if nested.get(key):
                        output[key] = nested[key]
    return output


# ---------------------------------------------------------------------------
# API principale
# ---------------------------------------------------------------------------

def run_phase_5_state_of_art_writer(
    organisme: str,
    project: str,
    year: str,
    selection_payload_path: Optional[str | Path] = None,
    article_cards_payload_path: Optional[str | Path] = None,
    fewshot_payload_path: Optional[str | Path] = None,
    style_profile_payload_path: Optional[str | Path] = None,
    argumentation_profile_payload_path: Optional[str | Path] = None,
    scientific_reasoning_payload_path: Optional[str | Path] = None,
    phase46_project_argumentation_payload_path: Optional[str | Path] = None,
    phase47_scientific_narrative_payload_path: Optional[str | Path] = None,
    project_context_payload_path: Optional[str | Path] = None,
    consultant_plan_contract_path: Optional[str | Path] = None,
    output_path: Optional[str | Path] = None,
    markdown_output_path: Optional[str | Path] = None,
    dry_run: bool = False,
    state_of_art_mode: str = "global",
    verrou_aliases: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    mode = clean_text(kwargs.get("mode") or state_of_art_mode or "global", 40).lower()
    guided_conversation = bool(kwargs.get("guided_conversation"))
    if mode not in {"global", "single", "unified", "unique"}:
        return {
            "ok": False,
            "status": "unsupported_state_of_art_mode",
            "message": "Cette version produit uniquement un état de l'art global.",
            "requested_mode": mode,
        }
    aliases = parse_aliases(verrou_aliases)

    selection_path = Path(selection_payload_path or default_selection_payload_path(organisme, project, year))
    cards_path = Path(article_cards_payload_path or default_article_cards_payload_path(organisme, project, year))
    fewshot_path = Path(fewshot_payload_path or default_fewshot_payload_path(organisme, project, year))
    style_path = Path(style_profile_payload_path or default_style_profile_payload_path(organisme, project, year))
    argumentation_path = Path(argumentation_profile_payload_path or default_argumentation_profile_payload_path(organisme, project, year))
    reasoning_path = Path(scientific_reasoning_payload_path or default_scientific_reasoning_payload_path(organisme, project, year))
    phase46_path = Path(phase46_project_argumentation_payload_path or default_phase46_payload_path(organisme, project, year))
    phase47_path = Path(phase47_scientific_narrative_payload_path or default_phase47_payload_path(organisme, project, year))
    out_path = Path(output_path or output_payload_path(organisme, project, year))
    rejected_payload_path = out_path.with_name(
        f"{out_path.stem}_rejected{out_path.suffix}"
    )
    md_path = Path(markdown_output_path or output_markdown_path(organisme, project, year))
    rejected_md_path = md_path.with_name(
        f"{md_path.stem}_rejected{md_path.suffix}"
    )
    progress_md_path = md_path.with_name(
        f"{md_path.stem}_in_progress{md_path.suffix}"
    )
    # Les artefacts annexes suivent le dossier de sortie explicite afin
    # qu'une exécution ne soit jamais répartie entre le nom projet brut et
    # son chemin canonique.
    writer_output_dir = out_path.parent
    blueprint_path = writer_output_dir / "unified_writer_blueprint_used.json"
    evidence_path = writer_output_dir / "normalized_evidence_units.json"
    # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
    cir_evidence_matrix_path = (
        writer_output_dir / "cir_evidence_matrix_v3.json"
    )
    cir_quality_report_path = (
        writer_output_dir / "cir_quality_report_v3.json"
    )
    # END ENNOSCHOLAR_CIR_QUALITY_V3
    writer_prompts_dir = writer_output_dir / "prompts"
    plan_path = Path(
        consultant_plan_contract_path
        or kwargs.get("consultant_plan_path")
        or default_consultant_plan_contract_path(organisme, project, str(year))
    )
    guided_sources_path = Path(
        kwargs.get("guided_research_sources_path")
        or kwargs.get("supplemental_sources_payload_path")
        or supplemental_sources_path(organisme, project, str(year))
    )

    selection = read_json(selection_path, {}) or {}
    cards_payload = read_json(cards_path, {}) or {}
    fewshot = read_json(fewshot_path, {}) or {}
    style = read_json(style_path, {}) or {}
    argumentation = read_json(argumentation_path, {}) or {}
    reasoning = read_json(reasoning_path, {}) or {}
    phase46 = read_json(phase46_path, {}) or {}
    phase47 = read_json(phase47_path, {}) or {}
    project_context = read_json(project_context_payload_path, {}) if project_context_payload_path else {}
    project_context = project_context if isinstance(project_context, dict) else {}

    input_paths = {
        "selection_payload": str(selection_path),
        "article_cards_payload": str(cards_path),
        "scientific_reasoning_payload": str(reasoning_path),
        "phase46_payload": str(phase46_path),
        "phase47_payload": str(phase47_path),
        "consultant_plan_contract": str(plan_path) if plan_path.is_file() else "",
        "guided_research_sources": str(guided_sources_path),
    }

    try:
        if not phase47:
            raise ContractError(
                "missing_phase47_payload",
                "La Phase 5 exige l'histoire scientifique de la Phase 4.7.",
            )
        if phase47.get("ok") is not True:
            raise ContractError(
                "phase47_not_validated",
                "La Phase 4.7 n'a pas validé la qualité de l'histoire scientifique.",
                {"phase47_status": phase47.get("status")},
            )
        selection_contract = (
            _confirmed_contract_from_payload(
                selection,
                source_name="selection_payload.json",
                source_path=selection_path,
                aliases=aliases,
            )
            if selection
            else None
        )
        phase47_verrous = _phase47_verrous(phase47)
        if phase47_verrous:
            phase47_contract = _confirmed_contract_from_payload(
                {"verrous": phase47_verrous},
                source_name="la Phase 4.7",
                source_path=phase47_path,
                aliases=aliases,
            )
        elif selection_contract:
            canonical_ids = {
                clean_text(item.get("verrou_id"), 120)
                for item in selection_contract["verrous"]
            }
            declared_ids = _normalize_declared_ids(
                _declared_phase47_verrou_ids(phase47),
                canonical_ids,
                aliases,
            )
            if declared_ids != canonical_ids:
                raise ContractError(
                    "verrou_contract_mismatch",
                    "Les références de verrous de la Phase 4.7 ne correspondent pas aux verrous confirmés.",
                    {
                        "source": "la Phase 4.7",
                        "expected_ids": sorted(canonical_ids),
                        "observed_ids": sorted(declared_ids),
                        "path": str(phase47_path),
                    },
                )
            # Adaptation en mémoire uniquement : l'histoire Phase 4.7 et son
            # fichier source ne sont pas modifiés.
            phase47["canonical_verrous"] = [
                {
                    "verrou_id": item["verrou_id"],
                    "verrou_title": item["verrou_title"],
                }
                for item in selection_contract["verrous"]
            ]
            phase47_contract = selection_contract
        else:
            phase47_contract = _confirmed_contract_from_payload(
                phase47,
                source_name="la Phase 4.7",
                source_path=phase47_path,
                aliases=aliases,
            )

        if selection_contract:
            assert_same_verrous(
                selection_contract["verrous"],
                phase47_contract["verrous"],
                observed_name="Phase 4.7",
            )
        if phase46:
            assert_same_verrous(
                phase47_contract["verrous"],
                extract_verrou_items(phase46),
                observed_name="Phase 4.6",
            )
    except ContractError as exc:
        result = {
            **exc.as_dict(),
            "phase": "phase_5_state_of_art_writer",
            "payload_type": PAYLOAD_TYPE,
            "input_paths": input_paths,
            "output_path": str(out_path),
        }
        if not dry_run:
            result["rejected_payload_output_path"] = str(
                rejected_payload_path
            )
            write_json(rejected_payload_path, result)
        return result

    plan_contract: Dict[str, Any] = (
        read_json(plan_path, {}) or {}
        if plan_path.is_file()
        else {}
    )
    cards = extract_article_cards(cards_payload)
    guided_payload = read_json(guided_sources_path, {}) or {}
    supplemental_cards = extract_supplemental_source_cards(
        guided_payload,
        cards,
    )
    cards.extend(supplemental_cards)
    try:
        cards, source_policy_report = apply_writing_source_policy(
            cards,
            plan_contract,
        )
    except ContractError as exc:
        result = {
            **exc.as_dict(),
            "phase": "phase_5_state_of_art_writer",
            "payload_type": PAYLOAD_TYPE,
            "input_paths": input_paths,
            "output_path": str(out_path),
            "markdown_output_path": "",
        }
        if not dry_run:
            result["rejected_payload_output_path"] = str(
                rejected_payload_path
            )
            write_json(rejected_payload_path, result)
        return result
    scientific_article_cards_count = len(
        [
            card
            for card in cards
            if not card.get("documentation_scope_only")
        ]
    )
    if not cards:
        result = {
            "ok": False,
            "status": "insufficient_evidence",
            "message": "Aucune Article Card exploitable : rédaction scientifique bloquée.",
            "phase": "phase_5_state_of_art_writer",
            "payload_type": PAYLOAD_TYPE,
            "input_paths": input_paths,
            "output_path": str(out_path),
            "markdown_output_path": "",
            "stats": {"article_cards_count": 0, "evidence_units_count": 0},
        }
        if not dry_run:
            result["rejected_payload_output_path"] = str(
                rejected_payload_path
            )
            write_json(rejected_payload_path, result)
        return result

    evidence_units = extract_evidence_units(reasoning, phase47, cards)
    if not evidence_units:
        result = {
            "ok": False,
            "status": "insufficient_evidence",
            "message": "Les Article Cards ne contiennent aucune unité de preuve exploitable.",
            "phase": "phase_5_state_of_art_writer",
            "payload_type": PAYLOAD_TYPE,
            "input_paths": input_paths,
            "output_path": str(out_path),
            "markdown_output_path": "",
            "stats": {
                "article_cards_count": len(cards),
                "evidence_units_count": 0,
            },
        }
        if not dry_run:
            result["rejected_payload_output_path"] = str(
                rejected_payload_path
            )
            write_json(rejected_payload_path, result)
        return result

    approved_plan: Optional[List[Dict[str, Any]]] = None
    require_plan = _env_flag("ENNOSCHOLAR_REQUIRE_APPROVED_PLAN", False)
    if plan_path.is_file():
        try:
            approved_plan = resolve_approved_plan(plan_contract)
        except ContractError as exc:
            result = {
                **exc.as_dict(),
                "phase": "phase_5_state_of_art_writer",
                "payload_type": PAYLOAD_TYPE,
                "input_paths": input_paths,
                "output_path": str(out_path),
            }
            if not dry_run:
                result["rejected_payload_output_path"] = str(
                    rejected_payload_path
                )
                write_json(rejected_payload_path, result)
            return result
    elif require_plan:
        result = {
            "ok": False,
            "status": "consultant_plan_required",
            "message": "Le mode chat exige un plan consultant approuvé avant la rédaction.",
            "phase": "phase_5_state_of_art_writer",
            "payload_type": PAYLOAD_TYPE,
            "input_paths": input_paths,
            "output_path": str(out_path),
        }
        if not dry_run:
            result["rejected_payload_output_path"] = str(
                rejected_payload_path
            )
            write_json(rejected_payload_path, result)
        return result
    try:
        blueprint = build_unified_blueprint(
            organisme=organisme,
            project=project,
            year=str(year),
            reasoning_payload=reasoning,
            phase46_payload=phase46,
            phase47_payload=phase47,
            project_context=project_context,
            style_memory=_style_memory(fewshot, style, argumentation),
            article_cards=cards,
            evidence_units=evidence_units,
            approved_plan=approved_plan,
            aliases=aliases,
            require_all_selected_sources=bool(
                source_policy_report.get(
                    "require_all_selected_sources"
                )
            ),
        )
    except ContractError as exc:
        result = {
            **exc.as_dict(),
            "phase": "phase_5_state_of_art_writer",
            "payload_type": PAYLOAD_TYPE,
            "input_paths": input_paths,
            "output_path": str(out_path),
        }
        if not dry_run:
            result["rejected_payload_output_path"] = str(
                rejected_payload_path
            )
            write_json(rejected_payload_path, result)
        return result
    blueprint["evidence_units"] = evidence_units
    blueprint["guided_conversation"] = guided_conversation
    if guided_conversation:
        for section in blueprint.get("sections") or []:
            if not isinstance(section, dict):
                continue
            section["_guided_conversation"] = True
            # Un plan approuvé sans consigne chiffrée reste volontairement
            # concis dans le chat. Une cible explicite du consultant est
            # toujours conservée.
            if not section.get("target_words"):
                section["target_words"] = 650

    # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
    cir_evidence_matrix = build_cir_evidence_matrix(
        blueprint,
        evidence_units,
    )
    if guided_conversation:
        cir_evidence_matrix.setdefault("policy", {}).update(
            {
                "guided_conversation": True,
                "connected_claims_may_describe_documented_subproblem": True,
            }
        )
    blueprint["cir_evidence_matrix"] = cir_evidence_matrix

    matrix_rows_by_id = {
        clean_text(row.get("verrou_id"), 120): row
        for row in cir_evidence_matrix.get("verrous") or []
        if isinstance(row, Mapping)
    }
    for _section in blueprint.get("sections") or []:
        if not isinstance(_section, dict):
            continue
        _ids = [
            clean_text(_verrou.get("verrou_id"), 120)
            for _verrou in _section.get("verrous") or []
            if isinstance(_verrou, Mapping)
        ]
        _section["_cir_evidence_matrix"] = {
            "schema_version": cir_evidence_matrix.get("schema_version"),
            "policy": cir_evidence_matrix.get("policy") or {},
            "verrous": [
                matrix_rows_by_id[_id]
                for _id in _ids
                if _id in matrix_rows_by_id
            ],
        }

    if not dry_run:
        write_json(cir_evidence_matrix_path, cir_evidence_matrix)
    # END ENNOSCHOLAR_CIR_QUALITY_V3

    prompt = _build_llm_prompt(blueprint, evidence_units)
    llm_draft, llm_report = call_sectional_writer_llm(
        blueprint,
        evidence_units,
        checkpoint_dir=writer_output_dir / "section_checkpoints",
        progress_markdown_path=progress_md_path,
    )
    if llm_draft:
        strict_llm_guard = validate_draft(
            llm_draft,
            blueprint,
            evidence_units=evidence_units,
            enforce_consultant_language=True,
        )
    else:
        # Une génération partielle/échouée n'est pas "LLM non utilisé".
        # Le statut réel du writer est propagé au guard et au diagnostic.
        llm_failure_status = clean_text(
            llm_report.get("status") or "llm_generation_failed",
            120,
        )
        llm_was_used = bool(llm_report.get("used"))
        strict_llm_guard = {
            "ok": False,
            "passed": False,
            "errors": [
                llm_failure_status if llm_was_used else "llm_not_used"
            ],
            "llm_used": llm_was_used,
            "partial_draft_available": bool(llm_report.get("partial")),
            "completed_sections_count": int(
                llm_report.get("completed_sections_count") or 0
            ),
            "total_sections_count": int(
                llm_report.get("total_sections_count") or 0
            ),
            "failed_section_index": llm_report.get("failed_section_index"),
            "failed_section_id": llm_report.get("failed_section_id"),
        }
    llm_guard = (
        _publication_guard_for_new_llm(
            strict_llm_guard,
            guided_conversation=guided_conversation,
        )
        if llm_draft
        else strict_llm_guard
    )

    if llm_draft and llm_guard.get("ok"):
        draft = llm_draft
        writer_used = (
            "llm_sectional_with_advisories"
            if llm_report.get("mode")
            == "sectional_llm_with_advisories"
            else "llm_sectional_long_form"
        )
        guard = llm_guard
    elif llm_report.get("status") == "disabled":
        draft = build_deterministic_unified_draft(
            blueprint,
            cards,
            evidence_units,
        )
        writer_used = "deterministic_evidence_only"
        guard = validate_draft(
            draft,
            blueprint,
            evidence_units=evidence_units,
        )
    else:
        # Si le LLM a été demandé mais n'a produit aucune section publiable,
        # aucune preuve brute n'est transformée en faux livrable. Le diagnostic
        # reste interne et la dernière version publiée demeure inchangée.
        draft = llm_draft or {"title": "", "sections": []}
        writer_used = "llm_generation_failed_no_raw_fallback"
        guard = llm_guard

    # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
    cir_postprocess = {
        "changes_count": 0,
        "changes": [],
    }
    if draft and draft.get("sections"):
        if not guided_conversation:
            draft, cir_postprocess = apply_cir_postprocessing(
                draft,
                blueprint,
            )
        postprocess_strict_guard = validate_draft(
            draft,
            blueprint,
            evidence_units=evidence_units,
            enforce_consultant_language=writer_used.startswith("llm_"),
        )
        guard = (
            _publication_guard_for_new_llm(
                postprocess_strict_guard,
                guided_conversation=guided_conversation,
            )
            if writer_used.startswith("llm_")
            else postprocess_strict_guard
        )

    cir_claim_audit = audit_cir_draft(
        draft,
        blueprint,
        cir_evidence_matrix,
    )
    if not cir_claim_audit.get("ok"):
        if guided_conversation:
            guard["advisory_errors"] = list(
                dict.fromkeys(
                    [
                        *(guard.get("advisory_errors") or []),
                        "cir_evidence_strength_violation",
                    ]
                )
            )
            guard["scientific_review_recommended"] = True
        else:
            guard["errors"] = list(
                dict.fromkeys(
                    [
                        *(guard.get("errors") or []),
                        "cir_evidence_strength_violation",
                    ]
                )
            )
            guard["ok"] = False
            guard["passed"] = False
    guard["cir_claim_audit"] = cir_claim_audit
    # END ENNOSCHOLAR_CIR_QUALITY_V3

    used_citations = (
        guard.get("detected_citations")
        or citations_from_obj(draft)
    )
    references = build_references_for_citations(used_citations, cards)

    raw_visual_placements = build_visual_placements(
        draft,
        blueprint,
        cards_payload,
        cards,
    )
    # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
    visual_placements, cir_visual_report = filter_visual_placements(
        raw_visual_placements,
        blueprint,
        cir_evidence_matrix,
    )
    # END ENNOSCHOLAR_CIR_QUALITY_V3

    markdown = draft_to_markdown(
        draft,
        guard,
        references,
        visual_placements=visual_placements,
    )
    ok = bool(guard.get("ok"))

    # Si le writer s'arrête après plusieurs sections, llm_draft peut être vide
    # alors que le fichier progress contient le vrai travail déjà généré.
    rejected_markdown = markdown
    if not ok and not llm_draft and progress_md_path.is_file():
        try:
            progress_text = progress_md_path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            progress_text = ""
        if len(progress_text.strip()) > len(rejected_markdown.strip()):
            rejected_markdown = progress_text

    final_text = markdown or ""
    word_count = len(re.findall(r"\b[\wÀ-ÿ'-]+\b", final_text))
    section_count = len(blueprint["sections"])
    requested_words = sum(
        _section_target_words(section, section_count)
        for section in blueprint.get("sections") or []
    )
    minimum_expected_words = (
        max(750, int(requested_words * 0.60))
        if guided_conversation
        else max(1800, int(requested_words * 0.72))
    )
    maximum_expected_words = max(
        minimum_expected_words + 800,
        int(requested_words * 1.65),
    )
    editorial_quality = _editorial_quality_report(draft)
    consultant_writer_mode = writer_used in {
        "llm_sectional_long_form",
        "llm_sectional_with_advisories",
    }
    quality_issues: List[str] = []
    if word_count < minimum_expected_words:
        quality_issues.append("document_too_short_for_consultant_depth")
    if word_count > maximum_expected_words:
        quality_issues.append("document_too_long_or_repetitive")
    if not consultant_writer_mode:
        quality_issues.append("llm_draft_unavailable")
    if not guard.get("semantic_claims_ok", True):
        quality_issues.append(
            "unsupported_or_misattributed_scientific_claims"
        )
    if guard.get("unused_allowed_citations"):
        quality_issues.append("selected_sources_not_used")
    if not editorial_quality.get("passed"):
        quality_issues.extend(editorial_quality.get("issues") or [])
    consultant_quality_ready = bool(
        ok
        and minimum_expected_words <= word_count <= maximum_expected_words
        and guard.get("semantic_claims_ok", True)
        and not (
            blueprint.get("require_all_selected_sources")
            and guard.get("unused_allowed_citations")
        )
        and editorial_quality.get("passed")
        and consultant_writer_mode
    )
    consultant_quality_score = 100
    if not ok:
        consultant_quality_score -= 35
    if not guard.get("semantic_claims_ok", True):
        consultant_quality_score -= 35
    if blueprint.get("require_all_selected_sources") and guard.get(
        "unused_allowed_citations"
    ):
        consultant_quality_score -= 15
    if not editorial_quality.get("passed"):
        consultant_quality_score -= 15
    if not (minimum_expected_words <= word_count <= maximum_expected_words):
        consultant_quality_score -= 10
    if not consultant_writer_mode:
        consultant_quality_score -= 10
    consultant_quality_score = max(0, consultant_quality_score)

    # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
    cir_quality_report = build_cir_quality_report(
        matrix=cir_evidence_matrix,
        audit=cir_claim_audit,
        postprocess=cir_postprocess,
        visual_report=cir_visual_report,
        final_guard=guard,
    )
    # END ENNOSCHOLAR_CIR_QUALITY_V3

    result = {
        "ok": ok,
        "status": "ok" if ok else "draft_rejected_by_guard",
        "phase": "phase_5_state_of_art_writer",
        "payload_type": PAYLOAD_TYPE,
        "generated_at": now_iso(),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "state_of_art_mode": "global",
        "writing_source_policy": source_policy_report,
        "input_paths": input_paths,
        "output_path": str(out_path),
        "rejected_payload_output_path": (
            str(rejected_payload_path) if not ok else ""
        ),
        "markdown_output_path": str(md_path) if ok else "",
        "rejected_markdown_output_path": (
            str(rejected_md_path) if rejected_markdown.strip() and not ok else ""
        ),
        "progress_markdown_output_path": str(progress_md_path),
        "unified_writer_blueprint_path": str(blueprint_path),
        "normalized_evidence_units_path": str(evidence_path),
        # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
        "cir_evidence_matrix_path": str(cir_evidence_matrix_path),
        "cir_quality_report_path": str(cir_quality_report_path),
        "cir_quality": cir_quality_report,
        # END ENNOSCHOLAR_CIR_QUALITY_V3
        "verrou_fingerprint": blueprint["verrou_fingerprint"],
        "plan_source": blueprint["plan_source"],
        "consultant_plan_approval_hash": plan_contract.get("approval_hash") if plan_contract else phase47.get("consultant_plan_approval_hash"),
        "writer_used": writer_used,
        "llm": llm_report,
        "llm_guard": llm_guard,
        "strict_llm_guard": strict_llm_guard,
        "guard": guard,
        "quality": {
            "consultant_quality_ready": consultant_quality_ready,
            "consultant_quality_score": consultant_quality_score,
            "writer_mode": writer_used,
            "word_count": word_count,
            "minimum_expected_words": minimum_expected_words,
            "maximum_expected_words": maximum_expected_words,
            "issues": quality_issues,
            "scientific_claims_supported": guard.get(
                "semantic_claims_ok",
                True,
            ),
            "unused_selected_sources": guard.get(
                "unused_allowed_citations"
            )
            or [],
            "editorial_quality": editorial_quality,
            "note": (
                "Document structuré, sourcé et suffisamment développé."
                if consultant_quality_ready
                else "Le document est scientifiquement contrôlé mais doit être enrichi avant validation consultant."
            ),
        },
        "draft_json": draft,
        "references": references,
        "visual_placements": visual_placements,
        "stats": {
            "article_cards_count": len(cards),
            "scientific_article_cards_count": scientific_article_cards_count,
            "supplemental_sources_count": len(
                [
                    card
                    for card in cards
                    if card.get("guided_research_source") is True
                ]
            ),
            "evidence_units_count": len(evidence_units),
            "verrous_count": len(blueprint["verrous"]),
            "sections_count": len(blueprint["sections"]),
            "citations_used_count": len(used_citations),
            "original_figures_inserted_count": len(visual_placements),
            # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
            "original_figures_rejected_by_cir_guard_count": int(
                cir_visual_report.get("rejected_count") or 0
            ),
            "cir_weak_verrous_count": len(
                [
                    row
                    for row in cir_evidence_matrix.get("verrous") or []
                    if isinstance(row, Mapping)
                    and row.get("strength")
                    in {"FAIBLE", "INSUFFISANTE"}
                ]
            ),
            # END ENNOSCHOLAR_CIR_QUALITY_V3
        },
        "rules": {
            "single_global_document": True,
            "phase47_story_preserved": True,
            "all_confirmed_verrous_required": True,
            "consultant_plan_requires_approval_when_present": True,
            "article_cards_are_only_citable_sources": True,
            "no_scientific_fallback_without_evidence": True,
            "no_default_citations": True,
            "no_domain_hardcoding": True,
            "atomic_citation_ownership_required": True,
            "independent_semantic_verifier_required": _env_flag(
                "ENNOSCHOLAR_PHASE5_REQUIRE_INDEPENDENT_VERIFIER",
                True,
            ),
            "related_evidence_cannot_validate_a_verrou": True,
            "uncited_scientific_claims_rejected": True,
            "gpt5_temperature_omitted": True,
            "original_figures_only": True,
            "vision_rewrite_disabled_for_figures": True,
            "article_figure_requires_same_section_citation": True,
            "project_document_figure_is_context_not_scientific_proof": True,
            # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
            "cir_evidence_matrix_required": True,
            "direct_claim_requires_confirmed_atomic_evidence": True,
            "weak_verrou_overclaim_is_blocking": True,
            "semantic_claim_errors_are_not_advisory": True,
            "context_or_decorative_visuals_filtered": True,
            "project_visuals_excluded_from_state_of_art_by_default": True,
            "anti_repetition_postprocess_enabled": True,
            "cir_v3_additional_llm_calls": 0,
            # END ENNOSCHOLAR_CIR_QUALITY_V3
        },
    }

    if not dry_run:
        write_json(blueprint_path, {key: value for key, value in blueprint.items() if key != "evidence_units"})
        write_json(
            evidence_path,
            {
                "payload_type": "normalized_evidence_units_generic_v1",
                "items": evidence_units,
            },
        )
        # BEGIN ENNOSCHOLAR_CIR_QUALITY_V3
        write_json(cir_evidence_matrix_path, cir_evidence_matrix)
        write_json(cir_quality_report_path, cir_quality_report)
        # END ENNOSCHOLAR_CIR_QUALITY_V3
        if ok:
            write_json(out_path, result)
            write_text(md_path, markdown)
            # Le snapshot de progression n'est plus nécessaire une fois le
            # livrable final publié.
            progress_md_path.unlink(missing_ok=True)
        else:
            write_json(rejected_payload_path, result)
        if rejected_markdown.strip() and not ok:
            # Un brouillon refusé ou partiel reste consultable. Il ne remplace
            # jamais l'artefact final publié au consultant.
            write_text(rejected_md_path, rejected_markdown)
        if _env_flag("ENNOSCHOLAR_SAVE_PROMPTS", False):
            prompt_path = writer_prompts_dir / "global_writer_prompt.txt"
            write_text(prompt_path, prompt)
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="EnnoScholar Phase 5 — writer global canonique")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--selection-payload-path")
    parser.add_argument("--article-cards-payload-path")
    parser.add_argument("--scientific-reasoning-payload-path")
    parser.add_argument("--phase46-project-argumentation-payload-path")
    parser.add_argument("--phase47-scientific-narrative-payload-path")
    parser.add_argument("--consultant-plan-contract-path")
    parser.add_argument(
        "--verrou-alias",
        action="append",
        default=[],
        help="Alias explicite ancien=canonique ; option répétable.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_phase_5_state_of_art_writer(
        organisme=args.organisme,
        project=args.project,
        year=args.year,
        selection_payload_path=args.selection_payload_path,
        article_cards_payload_path=args.article_cards_payload_path,
        scientific_reasoning_payload_path=args.scientific_reasoning_payload_path,
        phase46_project_argumentation_payload_path=args.phase46_project_argumentation_payload_path,
        phase47_scientific_narrative_payload_path=args.phase47_scientific_narrative_payload_path,
        consultant_plan_contract_path=args.consultant_plan_contract_path,
        verrou_aliases=args.verrou_alias,
        dry_run=args.dry_run,
    )
    print(
        json.dumps(
            {
                "ok": result.get("ok"),
                "status": result.get("status"),
                "state_of_art_mode": result.get("state_of_art_mode"),
                "writer_used": result.get("writer_used"),
                "output_path": result.get("output_path"),
                "markdown_output_path": result.get("markdown_output_path"),
                "stats": result.get("stats"),
                "guard": result.get("guard"),
                "message": result.get("message"),
                "details": result.get("details"),
                "input_paths": result.get("input_paths"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
