# -*- coding: utf-8 -*-
from __future__ import annotations

"""Handoff explicite et figé entre EnnoDiagnostic et EnnoScholar.

Le handoff est un snapshot de contexte, pas une duplication des documents. Il
référence les runs/verrous/articles PostgreSQL qui constituent l'état de départ
de la conversation EnnoScholar et empêche le chat de dériver vers un run plus
récent créé par une autre conversation ou un autre consultant.
"""

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from db.models import Article, DiagnosticRun, ScholarRun, Verrou


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split()).strip()[:limit]


def _as_int_set(values: Any) -> set[int]:
    result: set[int] = set()
    if not isinstance(values, (list, tuple, set)):
        return result
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            result.add(number)
    return result


def _serialize_verrou(verrou: Verrou) -> dict[str, Any]:
    source_json = verrou.source_json if isinstance(verrou.source_json, dict) else {}
    return {
        "id": int(verrou.id),
        "title": _clean(verrou.title, 700),
        "consultant_status": _clean(verrou.consultant_status, 80),
        "score": verrou.score,
        "tag_cir": verrou.tag_cir,
        "justification": _clean(verrou.justification, 1200),
        "origin": _clean(source_json.get("origin"), 120),
        "supplementary_verrou": bool(source_json.get("supplementary_verrou")),
    }


def _latest_diagnostic_run(db: Session, project_id: int) -> DiagnosticRun | None:
    return (
        db.query(DiagnosticRun)
        .filter(DiagnosticRun.project_id == int(project_id))
        .order_by(DiagnosticRun.created_at.desc(), DiagnosticRun.id.desc())
        .first()
    )


def _latest_scholar_run(db: Session, project_id: int) -> ScholarRun | None:
    return (
        db.query(ScholarRun)
        .filter(ScholarRun.project_id == int(project_id))
        .order_by(ScholarRun.created_at.desc(), ScholarRun.id.desc())
        .first()
    )


def _resolve_diagnostic_run(
    db: Session,
    project_id: int,
    requested: Mapping[str, Any],
) -> DiagnosticRun | None:
    requested_id = requested.get("diagnostic_run_id")
    source_agent = _clean(requested.get("source_agent"), 80).casefold()

    # Un handoff explicitement manuel permet de démarrer EnnoScholar sans figer
    # un diagnostic existant. Sans handoff explicite, on photographie l'état
    # courant afin de préserver la compatibilité avec le frontend V5 actuel.
    if requested and source_agent in {"manual", "standalone"} and not requested_id:
        return None

    if requested_id is not None:
        run = db.get(DiagnosticRun, int(requested_id))
        if run is None or int(run.project_id) != int(project_id):
            raise ValueError("Le DiagnosticRun demandé n'appartient pas à ce projet.")
        return run
    return _latest_diagnostic_run(db, project_id)


def _resolve_scholar_run(
    db: Session,
    project_id: int,
    requested: Mapping[str, Any],
) -> ScholarRun | None:
    requested_id = requested.get("scholar_run_id")
    if requested_id is not None:
        run = db.get(ScholarRun, int(requested_id))
        if run is None or int(run.project_id) != int(project_id):
            raise ValueError("Le ScholarRun demandé n'appartient pas à ce projet.")
        return run
    return _latest_scholar_run(db, project_id)


def build_guided_research_handoff_context(
    db: Session,
    project: Any,
    *,
    requested_handoff: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Construit un snapshot de handoff validé contre PostgreSQL.

    Si ``requested_handoff`` est absent, le service photographie automatiquement
    le dernier état diagnostic/scholar disponible. Cela rend le correctif utile
    immédiatement avec le frontend V5 existant, tout en permettant au futur
    frontend d'envoyer des IDs explicites.
    """

    requested = dict(requested_handoff or {})
    project_id = int(project.id)
    diagnostic_run = _resolve_diagnostic_run(db, project_id, requested)

    all_verrous: list[Verrou] = []
    if diagnostic_run is not None:
        all_verrous = (
            db.query(Verrou)
            .filter(Verrou.diagnostic_run_id == int(diagnostic_run.id))
            .order_by(Verrou.created_at.asc(), Verrou.id.asc())
            .all()
        )

    explicit_verrou_ids = _as_int_set(
        requested.get("verrou_ids") or requested.get("selected_verrou_ids")
    )
    by_id = {int(verrou.id): verrou for verrou in all_verrous}
    if explicit_verrou_ids:
        missing = sorted(explicit_verrou_ids - set(by_id))
        if missing:
            raise ValueError(
                "Certains verrous du handoff n'appartiennent pas au DiagnosticRun : "
                + ", ".join(str(value) for value in missing)
            )
        selected_verrous = [
            verrou for verrou in all_verrous if int(verrou.id) in explicit_verrou_ids
        ]
    else:
        consultant_selected = [
            verrou
            for verrou in all_verrous
            if _clean(verrou.consultant_status, 80).casefold() == "garde"
        ]
        selected_verrous = consultant_selected or all_verrous

    scholar_run = _resolve_scholar_run(db, project_id, requested)
    explicit_article_ids = _as_int_set(requested.get("selected_article_ids"))
    selected_articles: list[Article] = []
    if scholar_run is not None:
        run_articles = (
            db.query(Article)
            .filter(Article.scholar_run_id == int(scholar_run.id))
            .order_by(Article.id.asc())
            .all()
        )
        article_by_id = {int(article.id): article for article in run_articles}
        if explicit_article_ids:
            missing_articles = sorted(explicit_article_ids - set(article_by_id))
            if missing_articles:
                raise ValueError(
                    "Certains articles du handoff n'appartiennent pas au ScholarRun : "
                    + ", ".join(str(value) for value in missing_articles)
                )
            selected_articles = [
                article
                for article in run_articles
                if int(article.id) in explicit_article_ids
            ]
        else:
            selected_articles = [
                article
                for article in run_articles
                if _clean(article.consultant_status, 80).casefold() == "garde"
            ]
    elif explicit_article_ids:
        raise ValueError(
            "selected_article_ids exige un ScholarRun appartenant au projet."
        )

    requested_scope = _clean(requested.get("review_scope"), 40).casefold()
    if requested_scope not in {"global", "per_verrou"}:
        requested_scope = (
            "per_verrou" if len(selected_verrous) == 1 else "global"
        )

    serialized_verrous = [_serialize_verrou(verrou) for verrou in selected_verrous]
    selected_verrou_ids = [int(verrou.id) for verrou in selected_verrous]
    selected_article_ids = [int(article.id) for article in selected_articles]

    if not diagnostic_run and not scholar_run and not requested:
        # Projet réellement autonome : ne force aucune métadonnée inutile.
        return {}

    source_agent = _clean(requested.get("source_agent"), 80).casefold()
    if not source_agent:
        source_agent = "ennodiagnostic" if diagnostic_run is not None else "ennoscholar"

    handoff = {
        "version": "ennoscholar_handoff_v1",
        "frozen": True,
        "source_agent": source_agent,
        "target_agent": "ennoscholar",
        "project_id": project_id,
        "diagnostic_run_id": (
            int(diagnostic_run.id) if diagnostic_run is not None else None
        ),
        "scholar_run_id": int(scholar_run.id) if scholar_run is not None else None,
        "selected_verrou_ids": selected_verrou_ids,
        "selected_article_ids": selected_article_ids,
        "review_scope": requested_scope,
        "frozen_at": _now(),
        "automatic_snapshot": not bool(requested),
    }

    context: dict[str, Any] = {
        "handoff": handoff,
        "handoff_selected_article_ids": selected_article_ids,
        "review_scope": requested_scope,
        "project_verrous": serialized_verrous,
        "active_verrous": serialized_verrous,
        # Une liste vide signifie « tout le catalogue figé » dans la convention
        # actuelle d'EnnoScholar.
        "active_verrou_ids": (
            [] if requested_scope == "global" else selected_verrou_ids[:1]
        ),
    }
    return context


__all__ = ["build_guided_research_handoff_context"]
