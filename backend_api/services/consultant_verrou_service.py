# -*- coding: utf-8 -*-
from __future__ import annotations

"""Persistance des verrous ajoutés explicitement par le consultant.

Règles métier :
- aucune création automatique silencieuse ;
- le consultant doit demander explicitement l'ajout ;
- le verrou est rattaché au dernier DiagnosticRun du projet ;
- un run manuel minimal est créé si EnnoDiagnostic n'a pas encore été lancé ;
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


def is_manual_consultant_verrou(verrou: Verrou) -> bool:
    source_json = verrou.source_json if isinstance(verrou.source_json, dict) else {}
    return bool(
        source_json.get("manual_verrou")
        or source_json.get("supplementary_verrou")
        or (
            source_json.get("human_validated") is True
            and source_json.get("automatic_verrou_creation") is False
        )
    )


def get_project_manual_verrous(db: Session, project_id: int) -> list[Verrou]:
    """Retourne les ajouts humains de tout l'historique, sans doublon de ligne."""
    rows = (
        db.query(Verrou)
        .join(DiagnosticRun, Verrou.diagnostic_run_id == DiagnosticRun.id)
        .filter(DiagnosticRun.project_id == int(project_id))
        .order_by(Verrou.created_at.asc(), Verrou.id.asc())
        .all()
    )
    return [row for row in rows if is_manual_consultant_verrou(row)]


def merge_current_and_manual_verrous(
    current: Iterable[Verrou],
    manual_history: Iterable[Verrou],
) -> list[Verrou]:
    """Ajoute les verrous humains historiques au run courant sans dupliquer les ids."""
    output: list[Verrou] = []
    seen_ids: set[int] = set()
    for verrou in [*(current or []), *(manual_history or [])]:
        verrou_id = int(getattr(verrou, "id", 0) or 0)
        if verrou_id <= 0 or verrou_id in seen_ids:
            continue
        seen_ids.add(verrou_id)
        output.append(verrou)
    return output


def get_latest_diagnostic_verrous(db: Session, project_id: int) -> list[Verrou]:
    run = get_latest_diagnostic_run(db, project_id)
    current = (
        db.query(Verrou)
        .filter(Verrou.diagnostic_run_id == int(run.id))
        .order_by(Verrou.created_at.asc(), Verrou.id.asc())
        .all()
        if run is not None
        else []
    )
    return merge_current_and_manual_verrous(
        current,
        get_project_manual_verrous(db, project_id),
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


def _clean_keywords(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        keyword = _clean(value, 100)
        normalized = _norm(keyword)
        if not keyword or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(keyword)
        if len(output) >= 20:
            break
    return output


def _apply_manual_metadata(
    verrou: Verrou,
    *,
    description: str,
    keywords: Iterable[str],
    added_via: str,
) -> None:
    clean_description = _clean(description, 4000)
    clean_keywords = _clean_keywords(keywords)
    source_json = dict(verrou.source_json) if isinstance(verrou.source_json, dict) else {}
    source_json.update({
        "origin": "consultant_manual",
        "origin_type": "human_declared_verrou",
        "added_via": _clean(added_via, 120) or "consultant_manual",
        "manual_verrou": True,
        "supplementary_verrou": True,
        "human_validated": True,
        "automatic_verrou_creation": False,
        "manual_description": clean_description,
        "keywords": clean_keywords,
        "manual_scholar_text": " ".join(
            part
            for part in [
                _clean(verrou.title, 500),
                clean_description,
                f"Mots-clés : {', '.join(clean_keywords)}" if clean_keywords else "",
            ]
            if part
        ),
        "scientific_support_status": "pending_research",
    })
    verrou.source_json = source_json
    if clean_description and not _clean(verrou.justification, 4000):
        verrou.justification = clean_description


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
    keywords: Iterable[str] = (),
    added_via: str = "ennoscholar_guided_research",
) -> dict[str, Any]:
    """Crée ou réutilise un verrou explicitement demandé par le consultant."""
    clean_title = _clean(title, 1200)
    if len(clean_title) < 5:
        raise ValueError("Le verrou doit contenir une formulation exploitable.")

    latest_run = get_latest_diagnostic_run(db, int(project.id))
    if latest_run is None:
        latest_run = DiagnosticRun(
            project_id=int(project.id),
            status="manual_consultant_only",
            raw_result_json={
                "status": "manual_consultant_only",
                "created_at": _utc_now(),
                "source": "consultant_manual_verrou",
            },
            completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(latest_run)
        db.flush()

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
        _apply_manual_metadata(
            duplicate,
            description=justification,
            keywords=keywords,
            added_via=added_via,
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
        "origin": "consultant_manual",
        "origin_type": "human_declared_verrou",
        "added_via": _clean(added_via, 120) or "consultant_manual",
        "manual_verrou": True,
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
    _apply_manual_metadata(
        verrou,
        description=justification,
        keywords=keywords,
        added_via=added_via,
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
