# -*- coding: utf-8 -*-
from __future__ import annotations

# ENNOSCHOLAR_V169_1_PROJECT_PERSISTENT_CORPUS

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


def _clean(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[:limit]


def _slug(value: Any, limit: int = 120) -> str:
    text = _clean(value, 500).casefold()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")[:limit] or "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_base(project: Any) -> Path:
    from agents.EnnoScholar.storage_paths import state_of_art_root
    return state_of_art_root(
        str(project.organisme),
        str(project.project_name),
        str(project.year),
    )


def safe_session_id(session_id: str) -> str:
    value = str(session_id or "").strip()
    if not value:
        raise ValueError("guided_session_id vide.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,120}", value):
        raise ValueError("guided_session_id invalide.")
    return value


def conversation_root(project: Any, session_id: str) -> Path:
    return _project_base(project) / "conversations" / safe_session_id(session_id)


def conversation_work_root(project: Any, session_id: str) -> Path:
    return conversation_root(project, session_id) / "work"


def conversation_versions_root(project: Any, session_id: str) -> Path:
    return conversation_root(project, session_id) / "versions"


def assert_conversation_result_is_isolated(
    project: Any,
    session_id: str,
    result: Mapping[str, Any],
) -> None:
    """Refuse de publier dans une conversation un résultat produit ailleurs."""

    expected_session_id = safe_session_id(session_id)
    actual_session_id = str(result.get("guided_session_id") or "").strip()
    if actual_session_id != expected_session_id:
        raise RuntimeError(
            "conversation_scope_violation: le résultat ne porte pas "
            "l'identifiant de la conversation active."
        )

    result_paths = result.get("paths")
    result_paths = result_paths if isinstance(result_paths, Mapping) else {}
    markdown_path = str(
        result_paths.get("state_of_art_markdown")
        or result_paths.get("phase5_markdown")
        or ""
    ).strip()
    if not markdown_path:
        raise RuntimeError(
            "conversation_scope_violation: chemin du document final absent."
        )

    expected_root = conversation_root(project, expected_session_id).resolve()
    actual_path = Path(markdown_path).resolve()
    if not actual_path.is_relative_to(expected_root):
        raise RuntimeError(
            "conversation_scope_violation: le document final se trouve hors "
            "du dossier de la conversation active."
        )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _read_json(path: Path, default: Any = None) -> Any:
    default = {} if default is None else default
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _normalized_verrou_identifiers(value: Any) -> set[str]:
    output: set[str] = set()

    def add(raw: Any) -> None:
        text = _clean(raw, 300)
        if not text:
            return
        output.add(text.casefold())
        output.add(_slug(text))

    if isinstance(value, Mapping):
        for key in ("id", "verrou_id", "verrouId", "title", "verrou_title", "verrouTitle", "name"):
            if key in value:
                add(value.get(key))
    else:
        add(value)
    return {item for item in output if item}


def _collect_session_scope(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    context = dict(snapshot.get("context") or {})
    brief = dict(snapshot.get("brief") or {})
    active_raw: list[Any] = []

    for key in ("active_verrou_ids", "requested_verrou_ids", "selected_verrou_ids", "target_verrou_ids"):
        value = context.get(key)
        if isinstance(value, (list, tuple, set)):
            active_raw.extend(value)
        elif value not in (None, ""):
            active_raw.append(value)

    for key in ("verrou_ids", "requested_verrou_ids", "target_verrou_ids"):
        value = brief.get(key)
        if isinstance(value, (list, tuple, set)):
            active_raw.extend(value)
        elif value not in (None, ""):
            active_raw.append(value)

    consultant_verrous = [
        dict(row)
        for row in [
            *(context.get("consultant_verrous") or []),
            *(context.get("active_verrous") or []),
        ]
        if isinstance(row, Mapping)
    ]

    scope_identifiers: set[str] = set()
    for raw in active_raw:
        scope_identifiers |= _normalized_verrou_identifiers(raw)

    if scope_identifiers:
        for row in consultant_verrous:
            row_ids = _normalized_verrou_identifiers(row)
            if row_ids & scope_identifiers:
                scope_identifiers |= row_ids

    return {
        "mode": "scoped" if scope_identifiers else "global",
        "identifiers": sorted(scope_identifiers),
        "raw_active_verrous": [_clean(v, 300) for v in active_raw if _clean(v, 300)],
        "consultant_verrous": consultant_verrous,
    }


def _contract_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    contract = snapshot.get("writing_contract")
    if isinstance(contract, Mapping) and contract:
        return dict(contract)

    context = dict(snapshot.get("context") or {})
    for key in ("contract", "consultant_plan_contract", "writing_contract"):
        value = context.get(key)
        if isinstance(value, Mapping) and value:
            return dict(value)

    history = [
        row
        for row in (context.get("plan_history") or [])
        if isinstance(row, Mapping) and isinstance(row.get("plan"), list)
    ]
    if history:
        latest = dict(history[-1])
        plan = list(latest.get("plan") or [])
        return {
            "ok": True,
            "payload_type": "consultant_plan_contract_session_materialized_v1",
            "approved_plan": plan,
            "edited_plan": plan,
            "plan": plan,
            "approval_hash": latest.get("plan_hash"),
            "plan_version": latest.get("version"),
            "materialized_from_plan_history": True,
        }
    return {}


def _sources_from_snapshot(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    accepted = {"accepted", "accept", "garde", "gardé", "garder"}
    return [
        dict(row)
        for row in (snapshot.get("selected_sources") or [])
        if isinstance(row, Mapping)
        and str(row.get("consultant_decision") or "").casefold() in accepted
    ]


def prepare_conversation_run(db: Any, project: Any, session_id: str) -> dict[str, Any]:
    session_id = safe_session_id(session_id)

    from services.guided_research_service import get_guided_research_agent

    agent = get_guided_research_agent()
    snapshot = agent.repository.snapshot(db, session_id)
    if int(snapshot.get("project_id") or 0) != int(project.id):
        raise PermissionError("Cette conversation appartient à un autre projet.")

    root = conversation_root(project, session_id)
    work = conversation_work_root(project, session_id)
    inputs = root / "inputs"
    runtime = work / "runtime"
    for p in (root, work, inputs, runtime):
        p.mkdir(parents=True, exist_ok=True)

    scope = _collect_session_scope(snapshot)
    contract = _contract_from_snapshot(snapshot)
    # V169.1 : la conversation garde ses candidats locaux mais son corpus accepte
    # est reconstruit depuis toutes les publications gardees du projet.
    from services.ennoscholar_project_corpus_service import get_effective_guided_sources

    snapshot_context = dict(snapshot.get("context") or {})
    active_verrou_ids = (
        list(snapshot_context.get("active_verrou_ids") or [])
        if str(snapshot_context.get("review_scope") or "") == "per_verrou"
        else []
    )
    effective_sources = get_effective_guided_sources(
        db,
        project,
        session_sources=list(snapshot.get("selected_sources") or []),
        active_verrou_ids=active_verrou_ids,
    )
    accepted_decisions = {"accepted", "accept", "garde", "gardé", "garder"}
    sources = [
        dict(row)
        for row in effective_sources
        if isinstance(row, Mapping)
        and str(row.get("consultant_decision") or "").casefold()
        in accepted_decisions
    ]
    from services.guided_research_service import _guided_corpus_run

    _, corpus_run, _ = _guided_corpus_run(
        db,
        project,
        session_id=session_id,
        create=False,
    )
    snapshot_context = dict(snapshot.get("context") or {})
    corpus_scope_id = str(
        snapshot_context.get("corpus_scope_id") or session_id
    ).strip()

    contract_path = inputs / "consultant_plan_contract.json"
    sources_path = inputs / "guided_research_sources.json"
    scope_path = inputs / "session_scope.json"

    _write_json(
        contract_path,
        {
            **contract,
            "_conversation": {
                "session_id": session_id,
                "project_id": int(project.id),
                "materialized_at": _now(),
            },
        },
    )
    _write_json(
        sources_path,
        {
            "ok": True,
            "payload_type": "guided_accepted_sources_project_persistent_v169_1",
            "session_id": session_id,
            "updated_at": _now(),
            "sources": sources,
        },
    )
    _write_json(
        scope_path,
        {
            "ok": True,
            "payload_type": "ennoscholar_conversation_scope_v1",
            "session_id": session_id,
            "project_id": int(project.id),
            "scope": scope,
            "created_at": _now(),
        },
    )

    phase4 = work / "phase_4_scientific_gap"
    phase45 = work / "phase_4_5_scientific_reasoning"
    phase46 = work / "phase_4_6_project_rd_argumentation"
    phase47 = work / "phase_4_7_scientific_narrative"
    phase5 = work / "phase_5_state_of_art_writer"

    path_overrides = {
        "consultant_plan_contract": contract_path,
        "guided_research_sources": sources_path,
        "phase4_gap_payload": phase4 / "gap_scientific_payload.json",
        "phase45_scientific_reasoning_payload": phase45 / "scientific_reasoning_payload.json",
        "phase46_project_argumentation_payload": phase46 / "project_rd_argumentation_payload.json",
        "phase47_scientific_narrative_payload": phase47 / "scientific_narrative_payload.json",
        "phase5_dir": phase5,
        "phase5_payload": phase5 / "state_of_art_draft_payload.json",
        "phase5_markdown": phase5 / "state_of_art_draft.md",
        "phase5_deterministic_markdown": (
            phase5 / "state_of_art_draft_deterministic.md"
        ),
        "phase5_llm_raw_markdown": phase5 / "state_of_art_draft_llm_raw.md",
        "phase5_hybrid_markdown": phase5 / "state_of_art_draft_hybrid_llm.md",
        "phase5_prompts_dir": phase5 / "prompts",
        "phase5_llm_outputs_dir": phase5 / "llm_outputs",
        "conversation_root": root,
        "conversation_runtime": runtime,
        "conversation_scope_manifest": scope_path,
    }

    for path in path_overrides.values():
        if isinstance(path, Path):
            (path if not path.suffix else path.parent).mkdir(parents=True, exist_ok=True)

    return {
        "session_id": session_id,
        # Le scope de stockage reste conversationnel pour la tracabilite ; le
        # corpus scientifique effectif est commun au projet.
        "corpus_scope_id": corpus_scope_id,
        "effective_corpus_scope_id": f"project:{project.id}",
        "project_persistent_corpus": True,
        "scholar_run_id": (
            int(corpus_run.id) if corpus_run is not None else None
        ),
        "snapshot": snapshot,
        "scope": scope,
        "contract": contract,
        "sources": sources,
        "path_overrides": path_overrides,
    }


def _row_scope_identifiers(row: Mapping[str, Any]) -> set[str]:
    identifiers: set[str] = set()

    for key in ("target_verrous", "verrou_ids", "verrous", "target_verrou_ids"):
        value = row.get(key)
        if isinstance(value, (list, tuple, set)):
            for item in value:
                identifiers |= _normalized_verrou_identifiers(item)
        elif isinstance(value, Mapping):
            identifiers |= _normalized_verrou_identifiers(value)

    for key in ("verrou_id", "verrou_title", "target_verrou_id", "target_verrou_title"):
        if row.get(key) not in (None, ""):
            identifiers |= _normalized_verrou_identifiers(row.get(key))

    for nested_key in ("scientific_intent", "verrou_scientific_validation", "source_json", "evidence"):
        nested = row.get(nested_key)
        if isinstance(nested, Mapping):
            identifiers |= _row_scope_identifiers(nested)

    return identifiers


def _article_identity(row: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for key in (
        "article_id",
        "id",
        "citation_id",
        "citation_label",
        "candidate_id",
        "guided_candidate_id",
        "doi",
        "url",
        "title",
    ):
        value = _clean(row.get(key), 900)
        if value:
            result.add(f"{key}:{value.casefold()}")
            if key in {"candidate_id", "guided_candidate_id"}:
                result.add(f"candidate:{value.casefold()}")
            if key in {"citation_id", "citation_label"}:
                result.add(f"citation:{value.casefold()}")
    return result


def _article_identities_match(left: set[str], right: set[str]) -> bool:
    """Compare deux articles sans confondre des labels de citation recyclés."""

    priority_groups = (
        ("article_id:",),
        ("doi:",),
        ("candidate:", "candidate_id:", "guided_candidate_id:"),
        ("url:",),
        ("title:",),
        ("citation:", "citation_id:", "citation_label:"),
    )
    for prefixes in priority_groups:
        left_values = {
            value for value in left if value.startswith(prefixes)
        }
        right_values = {
            value for value in right if value.startswith(prefixes)
        }
        if left_values and right_values:
            return bool(left_values & right_values)
    return False


def _looks_like_article_row(value: Mapping[str, Any]) -> bool:
    return any(
        value.get(key) not in (None, "")
        for key in (
            "article_id",
            "citation_id",
            "citation_label",
            "candidate_id",
            "guided_candidate_id",
            "doi",
            "url",
        )
    )


def _session_source_scope_index(
    sources: Iterable[Mapping[str, Any]],
) -> list[tuple[set[str], set[str]]]:
    return [
        (_article_identity(source), _row_scope_identifiers(source))
        for source in sources
    ]


def _selection_article_scope_index(
    selection_payload: Mapping[str, Any],
) -> list[tuple[set[str], set[str]]]:
    """Déduit le rattachement article→verrou depuis la sélection canonique.

    Les Article Cards historiques ne recopient pas toujours ``verrou_ids``.
    La sélection reste alors la source d'autorité : chaque article imbriqué
    sous un verrou hérite du périmètre de ce verrou.
    """

    output: list[tuple[set[str], set[str]]] = []

    def visit(value: Any, inherited_scope: set[str]) -> None:
        if isinstance(value, Mapping):
            own_scope = _row_scope_identifiers(value)
            effective_scope = own_scope or inherited_scope
            if _looks_like_article_row(value) and effective_scope:
                identity = _article_identity(value)
                if identity:
                    output.append((identity, set(effective_scope)))
            for child in value.values():
                visit(child, effective_scope)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, inherited_scope)

    verrous = selection_payload.get("verrous")
    if isinstance(verrous, list):
        for verrou in verrous:
            if not isinstance(verrou, Mapping):
                continue
            verrou_scope = _normalized_verrou_identifiers(verrou)
            visit(verrou, verrou_scope)
    return output


def _matches_scope(
    row: Mapping[str, Any],
    allowed_scope: set[str],
    source_index: list[tuple[set[str], set[str]]],
) -> bool:
    if not allowed_scope:
        return True

    direct = _row_scope_identifiers(row)
    if direct:
        return bool(direct & allowed_scope)

    identity = _article_identity(row)
    if identity:
        for source_identity, source_scope in source_index:
            if _article_identities_match(identity, source_identity):
                return bool(source_scope & allowed_scope)

    return False


def _verrou_item_matches(row: Mapping[str, Any], allowed_scope: set[str]) -> bool:
    return bool(_normalized_verrou_identifiers(row) & allowed_scope)


def build_conversation_phase1_payload(
    *,
    project: Any,
    conversation_context: Mapping[str, Any],
    article_cards_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Matérialise le handoff Phase 1 propre à une conversation autonome.

    Le chat autonome ne passe volontairement pas par la sélection canonique du
    workflow 1. Son verrou confirmé et son corpus sont déjà enregistrés dans la
    session et dans le ScholarRun privé. Ce payload adapte ces deux sources au
    contrat attendu par les phases 3 à 5, sans écrire ni modifier le
    ``selection_payload.json`` partagé du projet.
    """

    snapshot = (
        dict(conversation_context.get("snapshot") or {})
        if isinstance(conversation_context.get("snapshot"), Mapping)
        else {}
    )
    snapshot_context = (
        dict(snapshot.get("context") or {})
        if isinstance(snapshot.get("context"), Mapping)
        else {}
    )
    scope = (
        dict(conversation_context.get("scope") or {})
        if isinstance(conversation_context.get("scope"), Mapping)
        else {}
    )
    project_brief = (
        dict(snapshot_context.get("standalone_project_brief") or {})
        if isinstance(snapshot_context.get("standalone_project_brief"), Mapping)
        else {}
    )

    raw_cards = (
        article_cards_payload.get("cards")
        if isinstance(article_cards_payload.get("cards"), list)
        else article_cards_payload.get("article_cards")
    )
    cards = [
        dict(row)
        for row in (raw_cards or [])
        if isinstance(row, Mapping)
    ]

    # La sélection ne duplique pas le contenu scientifique des Article Cards :
    # elle conserve seulement leurs identités et leur statut de sélection. Le
    # texte probant reste lu depuis le payload Phase 2 privé de la conversation.
    article_refs: list[dict[str, Any]] = []
    for index, card in enumerate(cards, 1):
        citation_id = _clean(
            card.get("citation_id") or card.get("citation_label") or f"A{index}",
            80,
        )
        article_refs.append({
            "article_id": card.get("article_id") or card.get("id"),
            "citation_id": citation_id,
            "citation_label": citation_id,
            "title": _clean(card.get("title") or card.get("article_title"), 1200),
            "doi": _clean(card.get("doi"), 300),
            "url": _clean(card.get("url"), 2000),
            "tag": _clean(card.get("tag") or card.get("role") or "Connexe", 120),
            "consultant_status": "garde",
            "consultant_selected": True,
            "selected": True,
        })

    verrous: list[dict[str, Any]] = []
    seen_verrou_ids: set[str] = set()
    for index, raw_verrou in enumerate(scope.get("consultant_verrous") or [], 1):
        if not isinstance(raw_verrou, Mapping):
            continue
        row = dict(raw_verrou)
        verrou_id = _clean(
            row.get("verrou_id") or row.get("id") or row.get("lock_id"),
            160,
        )
        verrou_title = _clean(
            row.get("verrou_title")
            or row.get("title")
            or row.get("name")
            or row.get("label"),
            1200,
        )
        if (
            not verrou_id
            or not verrou_title
            or verrou_id.casefold() in seen_verrou_ids
        ):
            continue
        seen_verrou_ids.add(verrou_id.casefold())
        objectif = _clean(
            row.get("objectif_rd")
            or row.get("justification")
            or row.get("objectif")
            or project_brief.get("objective"),
            5000,
        )
        contexte = _clean(
            row.get("contexte_projet")
            or row.get("supporting_context")
            or row.get("description")
            or project_brief.get("additional_context")
            or project_brief.get("domain"),
            8000,
        )
        row.update({
            "verrou_index": index,
            "verrou_id": verrou_id,
            "verrou_key": verrou_id,
            "verrou_title": verrou_title,
            "objectif_r&d": objectif,
            "objectif_rd": objectif,
            "contexte_projet": contexte,
            # Liaison éphémère de runtime uniquement. Aucun Article.verrou_id
            # ni artefact de sélection du workflow 1 n'est modifié.
            "selected_articles": [dict(article) for article in article_refs],
            "conversation_confirmed": True,
            "contract_origin": "guided_conversation_consultant_verrou",
        })
        verrous.append(row)

    project_name = _clean(
        project_brief.get("project_name")
        or getattr(project, "project_name", ""),
        500,
    )
    domain = _clean(
        project_brief.get("domain")
        or getattr(project, "domain_label", ""),
        1000,
    )
    objective = _clean(project_brief.get("objective"), 5000)
    additional_context = _clean(
        project_brief.get("additional_context"),
        8000,
    )
    session_id = _clean(conversation_context.get("session_id"), 160)
    scholar_run_id = conversation_context.get("scholar_run_id")
    scope_id = _clean(conversation_context.get("corpus_scope_id") or session_id, 160)
    excluded_count = int(
        article_cards_payload.get("excluded_from_writing_count") or 0
    )

    return {
        "ok": True,
        "agent": "EnnoScholar",
        "phase": "phase_1_selection_payload",
        "payload_type": "state_of_art_selection_payload_v1",
        "payload_version": "conversation_runtime_handoff_v1",
        "generated_at": _now(),
        "project_id": int(project.id),
        "project": project_name,
        "project_name": project_name,
        "organisme": _clean(getattr(project, "organisme", ""), 500),
        "year": _clean(getattr(project, "year", ""), 40),
        "domain_label": domain,
        "guided_session_id": session_id,
        "scope_id": scope_id,
        "scholar_run_id": int(scholar_run_id) if scholar_run_id is not None else None,
        "materialized_from_guided_session": True,
        "standalone_project_brief": project_brief,
        "project_context_structured": {
            "available": bool(objective or additional_context or domain),
            "source_priority": ["guided_consultant_conversation"],
            "report_path": "",
            "besoin_projet": objective,
            "objectif_technique": objective,
            "contexte_technique": additional_context or domain,
            "donnees_et_environnement": [],
            "contraintes_projet": [],
            "criteres_validation": [],
            "incertitude_rd": " ; ".join(
                _clean(row.get("verrou_title"), 1200) for row in verrous
            ),
            "points_de_preuve_projet": [
                *(
                    [{"role": "objectif", "text": objective, "source": "guided_conversation"}]
                    if objective
                    else []
                ),
                *[
                    {
                        "role": "verrou",
                        "text": row["verrou_title"],
                        "source": "guided_conversation",
                    }
                    for row in verrous
                ],
            ],
            "trace": {
                "from_guided_consultant_conversation": True,
                "guided_session_id": session_id,
                "standalone_without_diagnostic": True,
            },
        },
        "selection_summary": {
            "kept_articles_total": len(article_refs),
            "usable_articles_total": len(article_refs),
            "verrous_count": len(verrous),
            "excluded_articles_count": excluded_count,
            "excluded_by_limit_total": 0,
            "can_write_without_force": bool(verrous and article_refs),
        },
        "verrous": verrous,
        "verrous_count": len(verrous),
        "selected_articles": article_refs,
        "articles": article_refs,
        "selected_articles_count": len(article_refs),
        "articles_count": len(article_refs),
        "policy": "guided_conversation_validated_article_cards_without_limit",
    }


def apply_verrou_scope_lock(
    *,
    selection_payload: Mapping[str, Any],
    article_cards_payload: Mapping[str, Any],
    session_sources: Iterable[Mapping[str, Any]],
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    allowed_scope = {
        str(value).casefold()
        for value in (scope.get("identifiers") or [])
        if str(value).strip()
    }
    source_index = [
        *_session_source_scope_index(session_sources),
        *_selection_article_scope_index(selection_payload),
    ]

    cards_key = "cards" if isinstance(article_cards_payload.get("cards"), list) else "article_cards"
    all_cards = [
        dict(row)
        for row in (article_cards_payload.get(cards_key) or [])
        if isinstance(row, Mapping)
    ]
    kept_cards = [
        row for row in all_cards
        if _matches_scope(row, allowed_scope, source_index)
    ]

    kept_identities = [_article_identity(card) for card in kept_cards]

    def article_is_kept(row: Mapping[str, Any]) -> bool:
        identity = _article_identity(row)
        return bool(identity) and any(
            _article_identities_match(identity, kept_identity)
            for kept_identity in kept_identities
        )

    filtered_cards = dict(article_cards_payload)
    filtered_cards["cards"] = kept_cards
    filtered_cards["article_cards"] = kept_cards
    filtered_cards["selected_articles_count"] = len(kept_cards)
    filtered_cards["articles_count"] = len(kept_cards)
    filtered_cards["writing_ready_cards_count"] = len(kept_cards)
    filtered_cards["scope_lock"] = {
        "active": bool(allowed_scope),
        "allowed_verrou_identifiers": sorted(allowed_scope),
        "input_cards_count": len(all_cards),
        "kept_cards_count": len(kept_cards),
        "excluded_cards_count": len(all_cards) - len(kept_cards),
        "policy": "requested_verrou_only_before_reasoning_and_writing",
    }

    selection = dict(selection_payload)
    verrous = selection.get("verrous")
    if isinstance(verrous, list) and allowed_scope:
        kept_verrous = [
            dict(row)
            for row in verrous
            if isinstance(row, Mapping) and _verrou_item_matches(row, allowed_scope)
        ]

        # Une conversation autonome peut porter un verrou créé et validé dans
        # le chat sans verrou DB/canonique dans la sélection du workflow 1.
        # Il s'agit ici de matérialiser ce verrou consultant explicite, jamais
        # de le reconstruire par NLP. Les phases 4 à 5 reçoivent ainsi le même
        # identifiant, le même titre et le même contexte que la conversation.
        if not kept_verrous:
            seen_verrou_ids: set[str] = set()
            for raw_verrou in scope.get("consultant_verrous") or []:
                if not isinstance(raw_verrou, Mapping):
                    continue
                if allowed_scope and not _verrou_item_matches(
                    raw_verrou, allowed_scope
                ):
                    continue
                row = dict(raw_verrou)
                verrou_id = _clean(
                    row.get("verrou_id") or row.get("id") or row.get("lock_id"),
                    160,
                )
                verrou_title = _clean(
                    row.get("verrou_title")
                    or row.get("title")
                    or row.get("name")
                    or row.get("label"),
                    900,
                )
                if (
                    not verrou_id
                    or not verrou_title
                    or verrou_id.casefold() in seen_verrou_ids
                ):
                    continue
                seen_verrou_ids.add(verrou_id.casefold())
                row.update({
                    "verrou_id": verrou_id,
                    "verrou_title": verrou_title,
                    "objectif_rd": _clean(
                        row.get("objectif_rd")
                        or row.get("justification")
                        or row.get("objectif"),
                        4000,
                    ),
                    "contexte_projet": _clean(
                        row.get("contexte_projet")
                        or row.get("supporting_context")
                        or row.get("description"),
                        6000,
                    ),
                    "conversation_confirmed": True,
                    "contract_origin": "guided_conversation_consultant_verrou",
                })
                kept_verrous.append(row)

        def filter_nested_article_collections(value: Any) -> Any:
            if isinstance(value, Mapping):
                if _looks_like_article_row(value):
                    return dict(value)
                return {
                    key: filter_nested_article_collections(child)
                    for key, child in value.items()
                }
            if isinstance(value, list):
                contains_articles = any(
                    isinstance(child, Mapping)
                    and _looks_like_article_row(child)
                    for child in value
                )
                children = (
                    [
                        child
                        for child in value
                        if not isinstance(child, Mapping)
                        or not _looks_like_article_row(child)
                        or article_is_kept(child)
                    ]
                    if contains_articles
                    else value
                )
                return [
                    filter_nested_article_collections(child)
                    for child in children
                ]
            return value

        kept_verrous = [
            filter_nested_article_collections(row)
            for row in kept_verrous
        ]
        selection["verrous"] = kept_verrous
        selection["verrous_count"] = len(kept_verrous)

    def filter_article_list(value: Any) -> Any:
        if not isinstance(value, list):
            return value
        output = []
        for row in value:
            if not isinstance(row, Mapping):
                output.append(row)
                continue
            identity = _article_identity(row)
            if not allowed_scope:
                output.append(dict(row))
            elif article_is_kept(row):
                output.append(dict(row))
        return output

    for key in ("selected_articles", "articles", "selected_sources", "article_cards", "cards"):
        if key in selection:
            selection[key] = filter_article_list(selection.get(key))

    selection["selected_articles_count"] = len(kept_cards)
    selection["articles_count"] = len(kept_cards)
    selection["scope_lock"] = {
        "active": bool(allowed_scope),
        "allowed_verrou_identifiers": sorted(allowed_scope),
        "kept_article_cards_count": len(kept_cards),
    }

    scoped_sources = [
        dict(row)
        for row in session_sources
        if not allowed_scope or _matches_scope(row, allowed_scope, source_index)
    ]

    return {
        "selection_payload": selection,
        "article_cards_payload": filtered_cards,
        "guided_sources": scoped_sources,
        "scope_manifest": {
            "active": bool(allowed_scope),
            "allowed_verrou_identifiers": sorted(allowed_scope),
            "input_cards_count": len(all_cards),
            "kept_cards_count": len(kept_cards),
            "excluded_cards_count": len(all_cards) - len(kept_cards),
            "kept_citations": sorted({
                _clean(card.get("citation_label") or card.get("citation_id"), 80)
                for card in kept_cards
                if _clean(card.get("citation_label") or card.get("citation_id"), 80)
            }),
        },
    }


def materialize_scoped_runtime(
    *,
    conversation_context: Mapping[str, Any],
    selection_payload: Mapping[str, Any],
    article_cards_payload: Mapping[str, Any],
) -> dict[str, Any]:
    paths = dict(conversation_context.get("path_overrides") or {})
    runtime = Path(paths["conversation_runtime"])

    scoped = apply_verrou_scope_lock(
        selection_payload=selection_payload,
        article_cards_payload=article_cards_payload,
        session_sources=conversation_context.get("sources") or [],
        scope=conversation_context.get("scope") or {},
    )

    selection_path = runtime / "selection_payload_scoped.json"
    cards_path = runtime / "article_cards_payload_scoped.json"
    sources_path = Path(paths["guided_research_sources"])
    manifest_path = runtime / "scope_lock_manifest.json"

    _write_json(selection_path, scoped["selection_payload"])
    _write_json(cards_path, scoped["article_cards_payload"])
    _write_json(
        sources_path,
        {
            "ok": True,
            "payload_type": "guided_sources_scoped_for_current_conversation_v1",
            "sources": scoped["guided_sources"],
            "scope_lock": scoped["scope_manifest"],
            "updated_at": _now(),
        },
    )
    _write_json(manifest_path, scoped["scope_manifest"])

    return {
        **scoped,
        "selection_path": selection_path,
        "article_cards_path": cards_path,
        "sources_path": sources_path,
        "manifest_path": manifest_path,
    }


def _version_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def archive_conversation_state_of_art(
    *,
    project: Any,
    session_id: str,
    markdown: str,
    payload: Mapping[str, Any],
    editorial_report: Mapping[str, Any] | None = None,
    scope_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    session_id = safe_session_id(session_id)
    versions_root = conversation_versions_root(project, session_id)
    versions_root.mkdir(parents=True, exist_ok=True)

    version_id = _version_id()
    version_root = versions_root / version_id
    version_root.mkdir(parents=True, exist_ok=False)

    md_path = version_root / "state_of_art.md"
    payload_path = version_root / "state_of_art_payload.json"
    report_path = version_root / "editorial_report.json"
    scope_path = version_root / "scope_manifest.json"

    md_path.write_text(str(markdown or ""), encoding="utf-8")
    _write_json(payload_path, dict(payload))
    _write_json(report_path, dict(editorial_report or {}))
    _write_json(scope_path, dict(scope_manifest or {}))

    sha = hashlib.sha256(md_path.read_bytes()).hexdigest()
    metadata = {
        "version_id": version_id,
        "session_id": session_id,
        "project_id": int(project.id),
        "created_at": _now(),
        "markdown_path": str(md_path),
        "payload_path": str(payload_path),
        "editorial_report_path": str(report_path),
        "scope_manifest_path": str(scope_path),
        "markdown_sha256": sha,
        "word_count": len(re.findall(r"\b[\wÀ-ÿ'-]+\b", markdown or "")),
        "status": payload.get("status"),
        "ok": bool(payload.get("ok")),
    }

    index_path = conversation_root(project, session_id) / "versions_index.json"
    index = _read_json(index_path, {"versions": []})
    versions = [
        dict(row)
        for row in (index.get("versions") or [])
        if isinstance(row, Mapping)
    ]
    versions.append(metadata)
    _write_json(
        index_path,
        {
            "session_id": session_id,
            "project_id": int(project.id),
            "updated_at": _now(),
            "versions": versions,
        },
    )
    _write_json(conversation_root(project, session_id) / "latest_version.json", metadata)
    return metadata


def list_conversation_versions(project: Any, session_id: str) -> list[dict[str, Any]]:
    index_path = conversation_root(project, session_id) / "versions_index.json"
    index = _read_json(index_path, {"versions": []})
    return [
        dict(row)
        for row in (index.get("versions") or [])
        if isinstance(row, Mapping)
    ]


def get_conversation_version(
    project: Any,
    session_id: str,
    version_id: str,
) -> dict[str, Any]:
    version_id = _slug(version_id, 160)
    root = conversation_versions_root(project, session_id) / version_id
    if not root.is_dir():
        raise FileNotFoundError(version_id)

    return {
        "version_id": version_id,
        "markdown": (
            (root / "state_of_art.md").read_text(encoding="utf-8")
            if (root / "state_of_art.md").exists()
            else ""
        ),
        "payload": _read_json(root / "state_of_art_payload.json", {}),
        "editorial_report": _read_json(root / "editorial_report.json", {}),
        "scope_manifest": _read_json(root / "scope_manifest.json", {}),
    }


def filter_article_orm_rows_for_session(
    db: Any,
    project: Any,
    session_id: str,
    articles: Iterable[Any],
) -> list[Any]:
    """Applique le même scope verrou au garde-fou backend avant rédaction."""
    from services.guided_research_service import get_guided_research_agent

    snapshot = get_guided_research_agent().repository.snapshot(
        db,
        safe_session_id(session_id),
    )
    if int(snapshot.get("project_id") or 0) != int(project.id):
        raise PermissionError("Cette conversation appartient à un autre projet.")

    scope = _collect_session_scope(snapshot)
    allowed_scope = {
        str(value).casefold()
        for value in (scope.get("identifiers") or [])
        if str(value).strip()
    }
    if not allowed_scope:
        return list(articles)

    sources = _sources_from_snapshot(snapshot)
    source_index = _session_source_scope_index(sources)

    output = []
    for article in articles:
        row = {
            "article_id": getattr(article, "id", None),
            "id": getattr(article, "id", None),
            "title": getattr(article, "title", ""),
            "doi": getattr(article, "doi", ""),
            "url": getattr(article, "url", ""),
            "verrou_id": getattr(article, "verrou_id", None),
            "source_json": (
                getattr(article, "source_json", {})
                if isinstance(getattr(article, "source_json", {}), Mapping)
                else {}
            ),
        }
        if _matches_scope(row, allowed_scope, source_index):
            output.append(article)
    return output


def _extract_plan_sections(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("approved_plan", "edited_plan", "plan", "sections"):
        value = contract.get(key)
        if isinstance(value, list) and value:
            return [dict(row) for row in value if isinstance(row, Mapping)]
    return []


def estimate_conversation_cost(
    db: Any,
    project: Any,
    session_id: str,
) -> dict[str, Any]:
    """Estimation locale/sans LLM pour la conversation et son verrou actif."""
    context = prepare_conversation_run(db, project, session_id)

    shared_selection = _read_json(
        _project_base(project) / "selection_payload.json",
        {},
    )
    from services.article_card_builder import get_article_cards_payload
    cards_payload = get_article_cards_payload(project, db=db)

    scoped = apply_verrou_scope_lock(
        selection_payload=shared_selection,
        article_cards_payload=cards_payload,
        session_sources=context.get("sources") or [],
        scope=context.get("scope") or {},
    )

    plan_sections = _extract_plan_sections(context.get("contract") or {})
    section_count = len(plan_sections)
    cards_count = len(
        (scoped.get("article_cards_payload") or {}).get("cards") or []
    )

    from modules.LLM.usage_budget import compute_cost

    def usage(inp: int, out: int) -> dict[str, int]:
        return {
            "input_tokens": inp,
            "uncached_input_tokens": inp,
            "cached_input_tokens": 0,
            "output_tokens": out,
            "total_tokens": inp + out,
        }

    draft_model = str(
        __import__("os").getenv(
            "ENNOSCHOLAR_PHASE5_DRAFT_MODEL",
            "gpt-4.1-mini",
        )
    )
    verifier_model = str(
        __import__("os").getenv(
            "ENNOSCHOLAR_PHASE5_VERIFIER_MODEL",
            "gpt-4.1-mini",
        )
    )
    escalation_model = str(
        __import__("os").getenv(
            "ENNOSCHOLAR_PHASE5_ESCALATION_MODEL",
            "gpt-4.1",
        )
    )

    writer_input = int(
        __import__("os").getenv(
            "ENNOSCHOLAR_COST_EST_WRITER_INPUT_TOKENS",
            "7000",
        )
    )
    writer_output = int(
        __import__("os").getenv(
            "ENNOSCHOLAR_COST_EST_WRITER_OUTPUT_TOKENS",
            "1200",
        )
    )
    verifier_input = int(
        __import__("os").getenv(
            "ENNOSCHOLAR_COST_EST_VERIFIER_INPUT_TOKENS",
            "5000",
        )
    )
    verifier_output = int(
        __import__("os").getenv(
            "ENNOSCHOLAR_COST_EST_VERIFIER_OUTPUT_TOKENS",
            "450",
        )
    )
    risk_ratio = float(
        __import__("os").getenv(
            "ENNOSCHOLAR_COST_EST_RISK_VERIFIER_RATIO",
            "0.20",
        )
    )
    escalation_ratio = float(
        __import__("os").getenv(
            "ENNOSCHOLAR_COST_EST_ESCALATION_RATIO",
            "0.10",
        )
    )

    draft_unit = float(
        compute_cost(
            draft_model,
            "openai",
            usage(writer_input, writer_output),
        ).get("cost_usd")
        or 0.0
    )
    verifier_unit = float(
        compute_cost(
            verifier_model,
            "openai",
            usage(verifier_input, verifier_output),
        ).get("cost_usd")
        or 0.0
    )
    escalation_unit = float(
        compute_cost(
            escalation_model,
            "openai",
            usage(writer_input, writer_output),
        ).get("cost_usd")
        or 0.0
    )

    expected = (
        section_count * draft_unit
        + section_count * risk_ratio * verifier_unit
        + section_count * escalation_ratio * escalation_unit
    )
    high = (
        section_count * draft_unit
        + section_count * max(risk_ratio, 0.35) * verifier_unit
        + section_count * max(escalation_ratio, 0.20) * escalation_unit
    )

    hard_limit = float(
        __import__("os").getenv(
            "ENNOSMART_BUDGET_HARD_LIMIT_USD",
            "0.35",
        )
    )
    expected_slices = (
        max(1, int(__import__("math").ceil(expected / hard_limit)))
        if hard_limit > 0 and expected > 0
        else 1
    )

    return {
        "ok": True,
        "provider_call_made": False,
        "cost_estimate_only": True,
        "project_id": int(project.id),
        "session_id": safe_session_id(session_id),
        "scope": scoped.get("scope_manifest") or {},
        "plan_sections_count": section_count,
        "article_cards_count": cards_count,
        "estimated_cost_usd": {
            "expected": round(expected, 4),
            "high": round(high, 4),
        },
        "budget": {
            "hard_limit_per_slice_usd": hard_limit,
            "expected_slices": expected_slices,
            "resume_checkpoints_between_slices": True,
        },
        "models": {
            "draft": draft_model,
            "verifier": verifier_model,
            "escalation": escalation_model,
        },
        "note": (
            "Estimation conversationnelle après filtrage du verrou actif. "
            "Aucun appel LLM n'a été effectué."
        ),
    }
