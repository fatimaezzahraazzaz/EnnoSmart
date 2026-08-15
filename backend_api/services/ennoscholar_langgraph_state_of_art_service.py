# -*- coding: utf-8 -*-
from __future__ import annotations

"""
EnnoScholar — orchestration LangGraph du pipeline final d'état de l'art.

Le graphe ne remplace aucun service scientifique existant. Il orchestre les
phases déjà présentes et ajoute :
- checkpoints PostgreSQL ;
- reprise au dernier noeud échoué ;
- état sérialisable par projet/utilisateur/session ;
- diagnostic du workflow sans appel LLM.
"""

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, TypedDict

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from core.config import settings

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ROOT_DIR = _BACKEND_DIR.parent
load_dotenv(_ROOT_DIR / ".env", override=False)
load_dotenv(_BACKEND_DIR / ".env", override=False)

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import InMemorySaver

try:
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
except Exception:
    PostgresSaver = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]
    ConnectionPool = None  # type: ignore[assignment]

from modules.LLM.usage_budget import budgeted_pipeline
from services.article_card_builder import (
    get_article_cards_payload,
    is_article_card_ready_for_writing,
)
from services.ennoscholar_state_of_art_orchestrator import (
    _phase5_consultant_failure,
    _phase_paths,
    _read_json,
    _sha256,
    read_latest_state_of_art,
)


class ScholarWorkflowState(TypedDict, total=False):
    workflow_thread_id: str
    workflow_fingerprint: str
    project_id: int
    user_id: int | None
    guided_session_id: str | None
    organisme: str
    project_name: str
    year: str
    force_phase3: bool
    force_article_cards: bool
    paths: Dict[str, str]
    readonly_fingerprints_before: Dict[str, str]
    writing_eligibility: Dict[str, Any]
    phase_status: Dict[str, Any]
    last_completed_phase: str
    final_result: Dict[str, Any]


class ScholarPhaseExecutionError(RuntimeError):
    def __init__(
        self,
        phase: str,
        message: str,
        payload: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.payload = payload or {}


_CHECKPOINTER: Any = None
_CHECKPOINT_POOL: Any = None
_CHECKPOINT_LOCK = threading.Lock()


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().casefold() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _postgres_dsn() -> str:
    raw = str(
        os.getenv("ENNOSCHOLAR_LANGGRAPH_DATABASE_URL")
        or settings.DATABASE_URL
        or ""
    ).strip()
    if not raw:
        raise RuntimeError("DATABASE_URL absent pour le checkpointer LangGraph.")

    replacements = (
        ("postgresql+psycopg2://", "postgresql://"),
        ("postgresql+psycopg://", "postgresql://"),
        ("postgres+psycopg2://", "postgresql://"),
        ("postgres+psycopg://", "postgresql://"),
        ("postgres://", "postgresql://"),
    )
    for prefix, replacement in replacements:
        if raw.startswith(prefix):
            raw = replacement + raw[len(prefix):]
            break

    if not raw.startswith("postgresql://"):
        raise RuntimeError(
            "ENNOSCHOLAR_LANGGRAPH_CHECKPOINTER=postgres exige PostgreSQL. "
            "Pour un test isolé uniquement, utilise "
            "ENNOSCHOLAR_LANGGRAPH_CHECKPOINTER=memory."
        )
    return raw


def _configure_psycopg_connection(conn: Any) -> None:
    conn.autocommit = True
    try:
        conn.prepare_threshold = 0
    except Exception:
        pass
    if dict_row is not None:
        try:
            conn.row_factory = dict_row
        except Exception:
            pass


def _get_checkpointer() -> Any:
    global _CHECKPOINTER, _CHECKPOINT_POOL

    if _CHECKPOINTER is not None:
        return _CHECKPOINTER

    with _CHECKPOINT_LOCK:
        if _CHECKPOINTER is not None:
            return _CHECKPOINTER

        backend = str(
            os.getenv("ENNOSCHOLAR_LANGGRAPH_CHECKPOINTER") or "postgres"
        ).strip().casefold()

        if backend in {"memory", "inmemory", "test"}:
            _CHECKPOINTER = InMemorySaver()
            return _CHECKPOINTER

        if backend != "postgres":
            raise RuntimeError(
                "ENNOSCHOLAR_LANGGRAPH_CHECKPOINTER doit valoir postgres ou memory."
            )

        if PostgresSaver is None or ConnectionPool is None:
            raise RuntimeError(
                "Dépendances LangGraph/PostgreSQL absentes. "
                "Installe backend_api/requirements.txt."
            )

        min_size = _env_int(
            "ENNOSCHOLAR_LANGGRAPH_POOL_MIN", 1, minimum=1, maximum=8
        )
        max_size = _env_int(
            "ENNOSCHOLAR_LANGGRAPH_POOL_MAX",
            8,
            minimum=min_size,
            maximum=32,
        )

        _CHECKPOINT_POOL = ConnectionPool(
            conninfo=_postgres_dsn(),
            min_size=min_size,
            max_size=max_size,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            configure=_configure_psycopg_connection,
            open=True,
        )
        _CHECKPOINTER = PostgresSaver(_CHECKPOINT_POOL)
        _CHECKPOINTER.setup()
        return _CHECKPOINTER


def langgraph_health() -> Dict[str, Any]:
    try:
        saver = _get_checkpointer()
        return {
            "ok": True,
            "enabled": _env_bool("ENNOSCHOLAR_LANGGRAPH_ENABLED", True),
            "checkpointer": type(saver).__name__,
            "checkpoint_backend": str(
                os.getenv("ENNOSCHOLAR_LANGGRAPH_CHECKPOINTER") or "postgres"
            ),
            "strict_msgpack": _env_bool("LANGGRAPH_STRICT_MSGPACK", True),
            "phase5_section_checkpoint_reuse": _env_bool(
                "ENNOSCHOLAR_PHASE5_REUSE_SECTION_CHECKPOINTS", True
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "enabled": _env_bool("ENNOSCHOLAR_LANGGRAPH_ENABLED", True),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_VOLATILE_FINGERPRINT_KEYS = {
    "generated_at",
    "created_at",
    "updated_at",
    "last_accessed_at",
    "payload_path",
    "runtime_path",
}


def _stable_for_fingerprint(value: Any) -> Any:
    """Retire les métadonnées volatiles avant de calculer le thread_id.

    Une simple lecture des Article Cards ne doit jamais créer un nouveau thread
    uniquement parce qu'un timestamp ou un chemin runtime a changé.
    """
    if isinstance(value, dict):
        output: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            key_cf = key_text.casefold()
            if (
                key_cf in _VOLATILE_FINGERPRINT_KEYS
                or key_cf.endswith("_at")
                or "timestamp" in key_cf
            ):
                continue
            output[key_text] = _stable_for_fingerprint(item)
        return output
    if isinstance(value, list):
        normalized = [_stable_for_fingerprint(item) for item in value]
        # Seules les collections de cartes/articles sont traitées comme un set.
        # L'ordre des listes scientifiques internes (étapes, limites, etc.) est
        # au contraire conservé car il peut modifier la rédaction.
        if normalized and all(isinstance(item, dict) for item in normalized):
            if all(
                any(
                    key in item
                    for key in ("article_id", "citation_id", "citation_label")
                )
                for item in normalized
            ):
                return sorted(
                    normalized,
                    key=lambda item: (
                        str(item.get("article_id") or ""),
                        str(item.get("citation_id") or item.get("citation_label") or ""),
                    ),
                )
        return normalized
    return value


def _safe_fragment(value: str | None) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "direct")).strip("-")
    return (text or "direct")[:48]


def _current_input_identity(
    db: Session,
    project: Any,
    *,
    user_id: int | None,
    guided_session_id: str | None,
) -> Dict[str, str]:
    paths = _phase_paths(project)
    selection_hash = (
        _sha256(paths["selection_payload"])
        if paths["selection_payload"].exists()
        else "missing"
    )
    plan_hash = (
        _sha256(paths["consultant_plan_contract"])
        if paths["consultant_plan_contract"].exists()
        else "no-plan"
    )
    cards_payload = get_article_cards_payload(project, db=db)
    cards_hash = _stable_hash(_stable_for_fingerprint(cards_payload or {}))

    fingerprint = _stable_hash(
        {
            "project_id": int(project.id),
            "user_id": int(user_id) if user_id is not None else None,
            "guided_session_id": guided_session_id,
            "selection_sha256": selection_hash,
            "plan_sha256": plan_hash,
            "article_cards_sha256": cards_hash,
        }
    )
    thread_id = (
        f"ennoscholar-soa-p{int(project.id)}"
        f"-u{int(user_id) if user_id is not None else 0}"
        f"-{_safe_fragment(guided_session_id)}"
        f"-{fingerprint[:16]}"
    )
    return {
        "thread_id": thread_id,
        "fingerprint": fingerprint,
        "selection_sha256": selection_hash,
        "plan_sha256": plan_hash,
        "article_cards_sha256": cards_hash,
    }


def _state_paths(project: Any) -> Dict[str, str]:
    return {key: str(value) for key, value in _phase_paths(project).items()}


def _p(state: ScholarWorkflowState, key: str) -> Path:
    raw = (state.get("paths") or {}).get(key)
    if not raw:
        raise ScholarPhaseExecutionError(
            "configuration", f"Chemin LangGraph absent : {key}."
        )
    return Path(raw)


def _phase_update(
    state: ScholarWorkflowState,
    phase: str,
    *,
    result: Dict[str, Any] | None = None,
    **extra: Any,
) -> Dict[str, Any]:
    statuses = dict(state.get("phase_status") or {})
    row: Dict[str, Any] = {"ok": True, "completed_at": _utcnow()}
    if isinstance(result, dict):
        row["status"] = result.get("status")
        row["output_path"] = result.get("output_path")
    row.update(extra)
    statuses[phase] = row
    return {"phase_status": statuses, "last_completed_phase": phase}


def _node_prepare_inputs(
    state: ScholarWorkflowState,
    *,
    db: Session,
    project: Any,
) -> Dict[str, Any]:
    paths = _phase_paths(project)

    if not paths["selection_payload"].exists():
        raise ScholarPhaseExecutionError(
            "prepare_inputs",
            "selection_payload.json est introuvable. Conserve au moins un article.",
        )

    article_cards_payload = get_article_cards_payload(project, db=db)
    if not isinstance(article_cards_payload, dict):
        raise ScholarPhaseExecutionError(
            "prepare_inputs", "Article Cards invalides ou absentes."
        )
    cards = (
        article_cards_payload.get("cards")
        or article_cards_payload.get("article_cards")
        or []
    )
    if not isinstance(cards, list) or not cards:
        raise ScholarPhaseExecutionError(
            "prepare_inputs",
            "Aucune Article Card exploitable. Conserve au moins un article avec texte intégral.",
        )

    invalid_cards = []
    for card in cards:
        ready, reason = is_article_card_ready_for_writing(card)
        if not ready:
            invalid_cards.append(
                {
                    "article_id": card.get("article_id") if isinstance(card, dict) else None,
                    "citation_id": card.get("citation_id") if isinstance(card, dict) else None,
                    "reason": reason,
                }
            )
    if invalid_cards:
        raise ScholarPhaseExecutionError(
            "prepare_inputs",
            "Certaines Article Cards gardées ne sont pas prêtes pour la rédaction.",
            {"invalid_cards": invalid_cards[:10]},
        )

    runtime_dir = paths["base"] / "_langgraph_runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_cards_path = runtime_dir / "article_cards_payload.json"
    runtime_cards_path.write_text(
        json.dumps(article_cards_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths["article_cards_payload"] = runtime_cards_path

    selection_payload = _read_json(paths["selection_payload"], {})
    if not isinstance(selection_payload, dict) or not selection_payload:
        raise ScholarPhaseExecutionError(
            "prepare_inputs", "selection_payload.json est vide ou invalide."
        )

    readonly = {
        "selection_sha256": _sha256(paths["selection_payload"]),
        "article_cards_sha256": _sha256(runtime_cards_path),
    }

    selected_count = int(
        selection_payload.get("selected_articles_count")
        or selection_payload.get("articles_count")
        or article_cards_payload.get("selected_articles_count")
        or len(cards)
    )
    ready_count = len(cards)
    excluded_count = int(
        article_cards_payload.get("excluded_from_writing_count")
        or max(0, selected_count - ready_count)
    )

    eligibility = {
        "selected_articles_count": selected_count,
        "writing_ready_cards_count": ready_count,
        "excluded_from_writing_count": excluded_count,
        "writing_ready_article_ids": article_cards_payload.get("writing_ready_article_ids") or [],
        "excluded_article_ids": article_cards_payload.get("excluded_article_ids") or [],
        "rule": "consultant_kept_verified_article_cards_only",
        "all_cards_verified": True,
        "phase1_rebuilt": False,
        "phase2_rebuilt": False,
        "external_research_started": False,
        "fulltext_processing_started": False,
        "input_fingerprints": readonly,
    }

    update = _phase_update(
        state,
        "prepare_inputs",
        selected_articles_count=selected_count,
        writing_ready_cards_count=ready_count,
    )
    update.update(
        {
            "paths": {key: str(value) for key, value in paths.items()},
            "readonly_fingerprints_before": readonly,
            "writing_eligibility": eligibility,
        }
    )
    return update


def _node_phase3(state: ScholarWorkflowState, *, project: Any) -> Dict[str, Any]:
    from modules.CIR_STYLE_MEMORY.cir_style_fewshot.phase_3_style_fewshot_service import (
        build_phase_3_style_memory,
    )

    selection_payload = _read_json(_p(state, "selection_payload"), {})
    result = build_phase_3_style_memory(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        phase1_payload=selection_payload,
        phase1_payload_path=_p(state, "selection_payload"),
        force=bool(state.get("force_phase3", True)),
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise ScholarPhaseExecutionError(
            "phase3",
            f"Phase 3 échouée : {result}",
            result if isinstance(result, dict) else {},
        )
    return _phase_update(state, "phase3", result=result)


def _node_phase3b(state: ScholarWorkflowState) -> Dict[str, Any]:
    from agents.EnnoScholar.state_of_art.phase_3_style_signature_service import (
        run_phase_3_style_signature,
    )

    output = _p(state, "phase3_style_signature_payload")
    result = run_phase_3_style_signature(
        fewshot_payload_path=_p(state, "phase3_fewshot_payload"),
        output_path=output,
    )
    if not isinstance(result, dict) or not result.get("ok") or not output.exists():
        raise ScholarPhaseExecutionError(
            "phase3b",
            f"Phase 3B échouée : {result}",
            result if isinstance(result, dict) else {},
        )
    return _phase_update(state, "phase3b", result=result)


def _node_phase4(state: ScholarWorkflowState, *, project: Any) -> Dict[str, Any]:
    from agents.EnnoScholar.state_of_art.phase_4_scientific_gap_service import (
        build_scientific_gap_payload,
    )

    output = _p(state, "phase4_gap_payload")
    result = build_scientific_gap_payload(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        selection_payload_path=_p(state, "selection_payload"),
        article_cards_payload_path=_p(state, "article_cards_payload"),
        fewshot_payload_path=_p(state, "phase3_fewshot_payload"),
        output_path=output,
    )
    if not isinstance(result, dict) or not result.get("ok") or not output.exists():
        raise ScholarPhaseExecutionError(
            "phase4",
            f"Phase 4 échouée : {result}",
            result if isinstance(result, dict) else {},
        )
    return _phase_update(state, "phase4", result=result)


def _node_phase45(state: ScholarWorkflowState, *, project: Any) -> Dict[str, Any]:
    from agents.EnnoScholar.state_of_art.phase_4_5_scientific_reasoning_builder_service import (
        run_phase_4_5_scientific_reasoning,
    )

    output = _p(state, "phase45_scientific_reasoning_payload")
    result = run_phase_4_5_scientific_reasoning(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        gap_payload_path=_p(state, "phase4_gap_payload"),
        article_cards_payload_path=_p(state, "article_cards_payload"),
        argumentation_payload_path=_p(state, "phase3_argumentation_profile_payload"),
        output_path=output,
    )
    if not isinstance(result, dict) or not result.get("ok") or not output.exists():
        raise ScholarPhaseExecutionError(
            "phase45",
            f"Phase 4.5 échouée : {result}",
            result if isinstance(result, dict) else {},
        )
    return _phase_update(state, "phase45", result=result)


def _node_phase46(state: ScholarWorkflowState, *, project: Any) -> Dict[str, Any]:
    from agents.EnnoScholar.state_of_art.phase_4_6_project_rd_argumentation_service import (
        run_phase_4_6_project_rd_argumentation,
    )

    output = _p(state, "phase46_project_argumentation_payload")
    result = run_phase_4_6_project_rd_argumentation(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        selection_payload_path=str(_p(state, "selection_payload")),
        article_cards_payload_path=str(_p(state, "article_cards_payload")),
        fewshot_payload_path=str(_p(state, "phase3_fewshot_payload")),
        scientific_gap_payload_path=str(_p(state, "phase4_gap_payload")),
        scientific_reasoning_payload_path=str(_p(state, "phase45_scientific_reasoning_payload")),
        output_path=str(output),
        markdown_output_path=str(output.with_name("project_rd_argumentation_summary.md")),
        use_llm=False,
        dry_run=False,
    )
    argumentations = [
        item
        for item in ((result.get("argumentations") if isinstance(result, dict) else []) or [])
        if isinstance(item, dict)
    ]
    failed = [item for item in argumentations if not item.get("ok")]
    if (
        not isinstance(result, dict)
        or not result.get("ok")
        or not output.exists()
        or not argumentations
        or failed
    ):
        raise ScholarPhaseExecutionError(
            "phase46",
            "Phase 4.6 bloquée par ses garde-fous.",
            result if isinstance(result, dict) else {},
        )
    return _phase_update(state, "phase46", result=result)


def _node_phase47(state: ScholarWorkflowState, *, project: Any) -> Dict[str, Any]:
    from agents.EnnoScholar.state_of_art.phase_4_7_scientific_narrative_builder import (
        build_scientific_narrative_payload,
    )

    output = _p(state, "phase47_scientific_narrative_payload")
    result = build_scientific_narrative_payload(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        phase_4_5_path=str(_p(state, "phase45_scientific_reasoning_payload")),
        phase_4_6_path=str(_p(state, "phase46_project_argumentation_payload")),
        confirmed_verrous_path=str(_p(state, "selection_payload")),
        consultant_plan_path=str(_p(state, "consultant_plan_contract")),
        output_path=str(output),
        markdown_output_path=str(output.with_name("scientific_narrative_summary.md")),
        dry_run=False,
    )
    has_content = bool(
        isinstance(result, dict)
        and (
            result.get("verrous_count")
            or result.get("verrou_index")
            or result.get("verrou_sections_for_phase5")
            or result.get("project_specific_method_story_units")
            or result.get("global_writer_blueprint")
            or result.get("per_verrou_writer_blueprints")
        )
    )
    if (
        not isinstance(result, dict)
        or not result.get("ok")
        or not output.exists()
        or not has_content
    ):
        raise ScholarPhaseExecutionError(
            "phase47",
            "Phase 4.7 bloquée ou narrative inexploitable.",
            result if isinstance(result, dict) else {},
        )
    return _phase_update(state, "phase47", result=result)


def _node_phase5(state: ScholarWorkflowState, *, project: Any) -> Dict[str, Any]:
    from agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import (
        run_phase_5_state_of_art_writer,
    )

    output = _p(state, "phase5_payload")
    markdown = _p(state, "phase5_markdown")
    result = run_phase_5_state_of_art_writer(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        selection_payload_path=_p(state, "selection_payload"),
        article_cards_payload_path=_p(state, "article_cards_payload"),
        fewshot_payload_path=_p(state, "phase3_fewshot_payload"),
        style_profile_payload_path=_p(state, "phase3_style_signature_payload"),
        argumentation_profile_payload_path=_p(state, "phase3_argumentation_profile_payload"),
        scientific_reasoning_payload_path=_p(state, "phase45_scientific_reasoning_payload"),
        phase46_project_argumentation_payload_path=_p(state, "phase46_project_argumentation_payload"),
        phase47_scientific_narrative_payload_path=_p(state, "phase47_scientific_narrative_payload"),
        consultant_plan_contract_path=_p(state, "consultant_plan_contract"),
        guided_research_sources_path=_p(state, "guided_research_sources"),
        output_path=output,
        markdown_output_path=markdown,
        dry_run=False,
    )
    if not isinstance(result, dict):
        raise ScholarPhaseExecutionError(
            "phase5", f"Phase 5 : réponse invalide {type(result).__name__}."
        )
    if not result.get("ok"):
        raise ScholarPhaseExecutionError(
            "phase5",
            f"Phase 5 non publiée : {result.get('status') or 'draft_rejected'}",
            result,
        )
    if not output.exists() or not markdown.exists():
        raise ScholarPhaseExecutionError(
            "phase5", "Phase 5 annoncée OK mais artefact final absent.", result
        )
    return _phase_update(
        state,
        "phase5",
        result=result,
        writer_used=result.get("writer_used"),
        llm_used=bool((result.get("llm") or {}).get("used")),
    )


def _node_finalize(state: ScholarWorkflowState, *, project: Any) -> Dict[str, Any]:
    before = state.get("readonly_fingerprints_before") or {}
    after = {
        "selection_sha256": _sha256(_p(state, "selection_payload")),
        "article_cards_sha256": _sha256(_p(state, "article_cards_payload")),
    }
    if before != after:
        raise ScholarPhaseExecutionError(
            "finalize",
            "Violation lecture seule : la sélection ou les Article Cards ont changé pendant la rédaction.",
            {"before": before, "after": after},
        )

    latest = read_latest_state_of_art(project)
    if not latest.get("ok"):
        raise ScholarPhaseExecutionError(
            "finalize",
            "La Phase 5 est terminée mais le livrable publié n'est pas lisible.",
            latest,
        )

    statuses = dict(state.get("phase_status") or {})
    statuses["finalize"] = {"ok": True, "completed_at": _utcnow()}
    result = {
        "ok": True,
        "status": {
            "langgraph_enabled": True,
            "workflow_thread_id": state.get("workflow_thread_id"),
            "workflow_fingerprint": state.get("workflow_fingerprint"),
            "last_completed_phase": "finalize",
            "checkpointed": True,
            "phase_status": statuses,
            "phase1_phase2_read_only": True,
            "selected_articles_count": (state.get("writing_eligibility") or {}).get("selected_articles_count"),
            "writing_ready_articles_count": (state.get("writing_eligibility") or {}).get("writing_ready_cards_count"),
        },
        "project": {
            "id": int(project.id),
            "organisme": project.organisme,
            "project_name": project.project_name,
            "year": project.year,
        },
        "writing_eligibility": state.get("writing_eligibility") or {},
        "readonly_fingerprints_before": before,
        "readonly_fingerprints_after": after,
        "markdown": latest.get("markdown") or "",
        "state_of_art_view": latest.get("state_of_art_view") or {},
        "report": latest.get("report") or {},
        "paths": latest.get("paths") or {},
        "langgraph": {
            "thread_id": state.get("workflow_thread_id"),
            "fingerprint": state.get("workflow_fingerprint"),
            "resume_available": False,
            "last_completed_phase": "finalize",
            "phase_status": statuses,
        },
    }
    return {
        "phase_status": statuses,
        "last_completed_phase": "finalize",
        "final_result": result,
    }


def _build_graph(*, db: Session, project: Any) -> Any:
    builder = StateGraph(ScholarWorkflowState)
    builder.add_node(
        "prepare_inputs",
        lambda state: _node_prepare_inputs(state, db=db, project=project),
    )
    builder.add_node("phase3", lambda state: _node_phase3(state, project=project))
    builder.add_node("phase3b", _node_phase3b)
    builder.add_node("phase4", lambda state: _node_phase4(state, project=project))
    builder.add_node("phase45", lambda state: _node_phase45(state, project=project))
    builder.add_node("phase46", lambda state: _node_phase46(state, project=project))
    builder.add_node("phase47", lambda state: _node_phase47(state, project=project))
    builder.add_node("phase5", lambda state: _node_phase5(state, project=project))
    builder.add_node("finalize", lambda state: _node_finalize(state, project=project))

    builder.add_edge(START, "prepare_inputs")
    builder.add_edge("prepare_inputs", "phase3")
    builder.add_edge("phase3", "phase3b")
    builder.add_edge("phase3b", "phase4")
    builder.add_edge("phase4", "phase45")
    builder.add_edge("phase45", "phase46")
    builder.add_edge("phase46", "phase47")
    builder.add_edge("phase47", "phase5")
    builder.add_edge("phase5", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=_get_checkpointer())


def _snapshot_summary(snapshot: Any) -> Dict[str, Any]:
    if snapshot is None:
        return {
            "exists": False,
            "next_nodes": [],
            "last_completed_phase": None,
            "phase_status": {},
            "checkpoint_id": None,
        }
    values = snapshot.values if isinstance(snapshot.values, dict) else {}
    config = snapshot.config if isinstance(snapshot.config, dict) else {}
    configurable = config.get("configurable") if isinstance(config.get("configurable"), dict) else {}
    return {
        "exists": True,
        "next_nodes": list(snapshot.next or ()),
        "last_completed_phase": values.get("last_completed_phase"),
        "phase_status": values.get("phase_status") or {},
        "checkpoint_id": configurable.get("checkpoint_id"),
        "workflow_thread_id": values.get("workflow_thread_id"),
        "workflow_fingerprint": values.get("workflow_fingerprint"),
    }


def get_state_of_art_workflow_status(
    *,
    db: Session,
    project: Any,
    user_id: int | None = None,
    guided_session_id: str | None = None,
) -> Dict[str, Any]:
    identity = _current_input_identity(
        db,
        project,
        user_id=user_id,
        guided_session_id=guided_session_id,
    )
    graph = _build_graph(db=db, project=project)
    config = {"configurable": {"thread_id": identity["thread_id"]}}
    summary = _snapshot_summary(graph.get_state(config))
    return {
        "ok": True,
        "health": langgraph_health(),
        "thread_id": identity["thread_id"],
        "fingerprint": identity["fingerprint"],
        "resume_available": bool(summary["next_nodes"]),
        **summary,
    }


@budgeted_pipeline(run_type="ennoscholar_state_of_art_langgraph")
def run_state_of_art_langgraph(
    *,
    db: Session,
    project: Any,
    force_phase3: bool = True,
    force_article_cards: bool = False,
    enable_polish: bool | None = None,
    guided_session_id: str | None = None,
    user_id: int | None = None,
    **_: Any,
) -> Dict[str, Any]:
    del enable_polish

    identity = _current_input_identity(
        db,
        project,
        user_id=user_id,
        guided_session_id=guided_session_id,
    )
    thread_id = identity["thread_id"]
    graph = _build_graph(db=db, project=project)
    config = {"configurable": {"thread_id": thread_id}}

    initial: ScholarWorkflowState = {
        "workflow_thread_id": thread_id,
        "workflow_fingerprint": identity["fingerprint"],
        "project_id": int(project.id),
        "user_id": int(user_id) if user_id is not None else None,
        "guided_session_id": guided_session_id,
        "organisme": str(project.organisme),
        "project_name": str(project.project_name),
        "year": str(project.year),
        "force_phase3": bool(force_phase3),
        "force_article_cards": bool(force_article_cards),
        "paths": _state_paths(project),
        "phase_status": {},
        "last_completed_phase": "",
    }

    before = graph.get_state(config)
    resume = bool(before and before.next)

    try:
        output = graph.invoke(None if resume else initial, config=config)
        final_result = output.get("final_result") if isinstance(output, dict) else None
        if not isinstance(final_result, dict):
            raise RuntimeError("LangGraph a terminé sans final_result sérialisable.")
        final_result.setdefault("langgraph", {})
        final_result["langgraph"].update(
            {
                "thread_id": thread_id,
                "fingerprint": identity["fingerprint"],
                "resumed_from_checkpoint": resume,
                "resume_available": False,
            }
        )
        return final_result
    except Exception as exc:
        after = graph.get_state(config)
        summary = _snapshot_summary(after)
        failed_node = summary["next_nodes"][0] if summary["next_nodes"] else None
        phase = failed_node or "unknown"
        payload: Dict[str, Any] = {}
        if isinstance(exc, ScholarPhaseExecutionError):
            phase = exc.phase or phase
            payload = exc.payload

        public_status = "workflow_checkpointed_failure"
        assistant_message = (
            "La rédaction s'est arrêtée sur une étape précise. Les étapes déjà "
            "terminées sont sauvegardées ; relancez pour reprendre au dernier "
            "checkpoint sans recommencer le pipeline."
        )
        next_action = "retry_from_checkpoint"

        if phase == "phase5" and payload:
            previous = read_latest_state_of_art(project)
            translated = _phase5_consultant_failure(
                payload,
                previous_available=bool(previous.get("ok")),
            )
            public_status = str(
                translated.get("status")
                or payload.get("status")
                or public_status
            )
            assistant_message = str(
                translated.get("assistant_message") or assistant_message
            )
            next_action = str(translated.get("next_action") or next_action)

        return {
            "ok": False,
            "status": public_status,
            "assistant_message": assistant_message,
            "next_action": next_action,
            "retryable": True,
            "previous_draft_preserved": bool(read_latest_state_of_art(project).get("ok")),
            "project": {
                "id": int(project.id),
                "organisme": project.organisme,
                "project_name": project.project_name,
                "year": project.year,
            },
            "langgraph": {
                "enabled": True,
                "thread_id": thread_id,
                "fingerprint": identity["fingerprint"],
                "resumed_from_checkpoint": resume,
                "resume_available": bool(summary["next_nodes"]),
                "failed_node": failed_node,
                "failed_phase": phase,
                "last_completed_phase": summary["last_completed_phase"],
                "next_nodes": summary["next_nodes"],
                "checkpoint_id": summary["checkpoint_id"],
                "phase_status": summary["phase_status"],
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            "phase_failure": payload,
        }
