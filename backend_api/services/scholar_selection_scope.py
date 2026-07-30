# -*- coding: utf-8 -*-
from __future__ import annotations

"""Périmètre canonique de la sélection consultant EnnoScholar.

L'interface affiche les articles du dernier ScholarRun. Les phases aval
(fulltext, MCP, Article Cards et rédaction) doivent utiliser exactement le
même périmètre, sans réintroduire les décisions gardées de runs historiques.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from db.models import Article, Project, ScholarRun


def get_current_scholar_run(
    db: Session,
    project: Project,
) -> Optional[ScholarRun]:
    return (
        db.query(ScholarRun)
        .filter(ScholarRun.project_id == project.id)
        .order_by(ScholarRun.created_at.desc(), ScholarRun.id.desc())
        .first()
    )


def get_current_selected_articles(
    db: Session,
    project: Project,
) -> List[Article]:
    """Retourne uniquement les articles gardés dans le dernier ScholarRun."""
    current_run = get_current_scholar_run(db, project)
    if current_run is None:
        return []

    return (
        db.query(Article)
        .filter(Article.scholar_run_id == current_run.id)
        .filter(Article.consultant_status == "garde")
        .order_by(Article.score.desc(), Article.year.desc(), Article.created_at.asc())
        .all()
    )
