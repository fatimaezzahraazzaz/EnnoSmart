# -*- coding: utf-8 -*-
from __future__ import annotations

"""Persistance des verrous ajoutés explicitement depuis le chat EnnoScholar.

Règles métier :
- aucune création automatique silencieuse ;
- le consultant doit demander explicitement l'ajout ;
- le verrou est rattaché au dernier DiagnosticRun officiel du projet ;
- score CIR et tag CIR restent indéterminés ;
- la provenance humaine est conservée dans source_json ;
- les doublons certains sont réutilisés ;
- les doublons possibles bloquent l'action jusqu'à confirmation explicite.
"""

from datetime import datetime, timezone
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any, Iterable, Mapping

from sqlalchemy.orm import Session

from db.models import DiagnosticRun, Verrou


_EXACT_OR_NEAR_DUPLICATE_THRESHOLD = 0.92
_POSSIBLE_DUPLICATE_THRESHOLD = 0.74

_STOPWORDS = {
    "a", "au", "aux", "avec", "ce", "ces", "cet", "cette", "dans", "de",
    "des", "du", "en", "et", "la", "le", "les", "ou", "par", "pour",
    "sur", "un", "une", "d", "l", "the", "of", "and", "to", "for", "in",
    "verrou", "verrous", "incertitude", "scientifique", "technologique",
    "technique", "projet", "probleme", "problème",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean(value: Any, limit: int = 12000) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in _norm(value).split()
        if len(token) >= 3 and token not in _STOPWORDS
    }


def verrou_title_similarity(left: Any, right: Any) -> float:
    """Score générique de proximité entre deux formulations de verrou."""
    left_norm = _norm(left)
    right_norm = _norm(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0

    left_tokens = _tokens(left_norm)
    right_tokens = _tokens(right_norm)
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()

    # Les formulations de verrous changent souvent d'ordre. Le recouvrement des
    # concepts est donc légèrement prioritaire par rapport à la séquence brute.
    return round(max(jaccard, (0.58 * jaccard) + (0.42 * sequence)), 4)


def get_latest_diagnostic_run(db: Session, project_id: int) -> DiagnosticRun | None:
    return (
        db.query(DiagnosticRun)
        .filter(DiagnosticRun.project_id == int(project_id))
        .order_by(DiagnosticRun.created_at.desc(), DiagnosticRun.id.desc())
        .first()
    )


def get_latest_diagnostic_verrous(db: Session, project_id: int) -> list[Verrou]:
    run = get_latest_diagnostic_run(db, project_id)
    if run is None:
        return []
    return (
        db.query(Verrou)
        .filter(Verrou.diagnostic_run_id == int(run.id))
        .order_by(Verrou.created_at.asc(), Verrou.id.asc())
        .all()
    )


def list_latest_diagnostic_verrous_for_chat(
    db: Session,
    project: Any,
) -> list[dict[str, Any]]:
    """Vue compacte transmise au contrôleur conversationnel."""
    rows = get_latest_diagnostic_verrous(db, int(project.id))
    output: list[dict[str, Any]] = []
    for verrou in rows:
        source_json = verrou.source_json if isinstance(verrou.source_json, dict) else {}
        output.append(
            {
                "id": int(verrou.id),
                "title": _clean(verrou.title, 700),
                "consultant_status": _clean(verrou.consultant_status, 80),
                "score": verrou.score,
                "tag_cir": verrou.tag_cir,
                "justification": _clean(verrou.justification, 1200),
                "origin": _clean(source_json.get("origin"), 120),
                "supplementary_verrou": bool(
                    source_json.get("supplementary_verrou")
                ),
            }
        )
    return output


def _best_duplicate(
    title: str,
    existing: Iterable[Verrou],
) -> tuple[Verrou | None, float]:
    best: Verrou | None = None
    best_score = 0.0
    for verrou in existing:
        score = verrou_title_similarity(title, verrou.title)
        if score > best_score:
            best = verrou
            best_score = score
    return best, best_score


def _merge_consultant_event(
    verrou: Verrou,
    *,
    session_id: str,
    created_by_user_id: int | None,
    justification: str,
    supporting_context: str,
    source_document_ids: Iterable[int],
    reused: bool,
) -> None:
    source_json = dict(verrou.source_json) if isinstance(verrou.source_json, dict) else {}
    events = [
        dict(row)
        for row in (source_json.get("consultant_chat_events") or [])
        if isinstance(row, Mapping)
    ]
    events.append(
        {
            "event": "reuse_existing_verrou" if reused else "create_verrou",
            "at": _utc_now(),
            "session_id": _clean(session_id, 200),
            "created_by_user_id": created_by_user_id,
            "justification": _clean(justification, 4000),
            "supporting_context": _clean(supporting_context, 8000),
            "source_document_ids": sorted(
                {
                    int(value)
                    for value in source_document_ids
                    if str(value).strip().isdigit()
                }
            ),
        }
    )
    source_json["consultant_chat_events"] = events[-20:]
    source_json.setdefault("human_validated", True)
    source_json.setdefault("automatic_verrou_creation", False)
    verrou.source_json = source_json


def create_or_reuse_consultant_verrou(
    db: Session,
    project: Any,
    *,
    title: str,
    justification: str = "",
    supporting_context: str = "",
    source_document_ids: Iterable[int] = (),
    session_id: str = "",
    created_by_user_id: int | None = None,
    force_create_distinct: bool = False,
) -> dict[str, Any]:
    """Crée ou réutilise un verrou explicitement demandé par le consultant."""
    clean_title = _clean(title, 1200)
    if len(clean_title) < 5:
        raise ValueError("Le verrou doit contenir une formulation exploitable.")

    latest_run = get_latest_diagnostic_run(db, int(project.id))
    if latest_run is None:
        raise RuntimeError(
            "Aucun DiagnosticRun officiel n'existe pour ce projet. "
            "Lance d'abord EnnoDiagnostic avant d'ajouter un verrou depuis EnnoScholar."
        )

    existing = get_latest_diagnostic_verrous(db, int(project.id))
    duplicate, similarity = _best_duplicate(clean_title, existing)

    if duplicate is not None and similarity >= _EXACT_OR_NEAR_DUPLICATE_THRESHOLD:
        duplicate.consultant_status = "garde"
        _merge_consultant_event(
            duplicate,
            session_id=session_id,
            created_by_user_id=created_by_user_id,
            justification=justification,
            supporting_context=supporting_context,
            source_document_ids=source_document_ids,
            reused=True,
        )
        db.add(duplicate)
        db.commit()
        db.refresh(duplicate)
        return {
            "ok": True,
            "status": "reused_existing",
            "created": False,
            "verrou_id": int(duplicate.id),
            "title": duplicate.title,
            "similarity": similarity,
            "consultant_status": duplicate.consultant_status,
            "latest_diagnostic_run_id": int(latest_run.id),
        }

    if (
        duplicate is not None
        and similarity >= _POSSIBLE_DUPLICATE_THRESHOLD
        and not force_create_distinct
    ):
        return {
            "ok": False,
            "status": "possible_duplicate",
            "created": False,
            "requested_title": clean_title,
            "similarity": similarity,
            "candidate": {
                "verrou_id": int(duplicate.id),
                "title": duplicate.title,
                "consultant_status": duplicate.consultant_status,
            },
            "message": (
                "Un verrou proche existe déjà. Demande explicitement de créer "
                "un verrou distinct ou réutilise le verrou proposé."
            ),
        }

    source_ids = sorted(
        {
            int(value)
            for value in source_document_ids
            if str(value).strip().isdigit()
        }
    )
    now = _utc_now()
    source_json = {
        "origin": "consultant_chat",
        "origin_type": "human_declared_missing_verrou",
        "added_via": "ennoscholar_guided_research",
        "supplementary_verrou": True,
        "human_validated": True,
        "automatic_verrou_creation": False,
        "session_id": _clean(session_id, 200),
        "created_by_user_id": created_by_user_id,
        "created_at": now,
        "supporting_context": _clean(supporting_context, 8000),
        "source_document_ids": source_ids,
        "document_support_status": (
            "explicit_document_links_supplied"
            if source_ids
            else "declared_by_consultant_not_document_verified"
        ),
        "scientific_support_status": "pending_research",
        "research_policy": {
            "seek_supporting_evidence": True,
            "seek_contradictory_evidence": True,
            "seek_limitations_and_validation_conditions": True,
            "consultant_selection_required": True,
        },
        "consultant_chat_events": [
            {
                "event": "create_verrou",
                "at": now,
                "session_id": _clean(session_id, 200),
                "created_by_user_id": created_by_user_id,
                "justification": _clean(justification, 4000),
                "supporting_context": _clean(supporting_context, 8000),
                "source_document_ids": source_ids,
            }
        ],
    }

    verrou = Verrou(
        diagnostic_run_id=int(latest_run.id),
        title=clean_title,
        tag_cir=None,
        score=None,
        consultant_status="garde",
        justification=_clean(justification, 4000) or None,
        source_json=source_json,
    )
    db.add(verrou)
    db.commit()
    db.refresh(verrou)

    return {
        "ok": True,
        "status": "created",
        "created": True,
        "verrou_id": int(verrou.id),
        "title": verrou.title,
        "similarity": similarity,
        "possible_related_verrou_id": int(duplicate.id) if duplicate else None,
        "consultant_status": verrou.consultant_status,
        "latest_diagnostic_run_id": int(latest_run.id),
    }
