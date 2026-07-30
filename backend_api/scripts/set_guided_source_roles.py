# -*- coding: utf-8 -*-
from __future__ import annotations

"""Associe des rôles de preuve consultant à des sources guidées.

Le rôle est une donnée de session, pas une règle applicative codée en dur.
Format : ``--role CANDIDATE_ID=role_libre``.
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend_api"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

load_dotenv(ROOT / ".env", override=False)
load_dotenv(BACKEND / ".env", override=False)

from agents.EnnoScholar.consultant_plan_service import read_json, write_json
from db.database import SessionLocal
from db.models import Article, Project
from services.guided_research_service import get_guided_research_agent
from services.scholar_selection_scope import get_current_scholar_run


def _parse_roles(values: list[str]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for value in values:
        candidate_id, separator, role = value.partition("=")
        candidate_id = candidate_id.strip()
        role = role.strip()
        if not separator or not candidate_id or not role:
            raise ValueError(
                f"Rôle invalide {value!r}; format attendu CANDIDATE_ID=role."
            )
        roles[candidate_id] = role
    return roles


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--role", action="append", required=True)
    args = parser.parse_args()
    roles = _parse_roles(args.role)

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == args.project_id).one()
        agent = get_guided_research_agent()
        snapshot = agent.repository.snapshot(db, args.session_id)
        sources = list(snapshot.get("selected_sources") or [])
        changed_sources = 0
        for source in sources:
            candidate_id = str(source.get("candidate_id") or "")
            if candidate_id in roles:
                source["consultant_evidence_role"] = roles[candidate_id]
                changed_sources += 1
        agent.repository.update(
            db,
            args.session_id,
            selected_sources=sources,
            context_updates={"consultant_source_roles": roles},
        )

        current_run = get_current_scholar_run(db, project)
        changed_articles = 0
        if current_run is not None:
            articles = (
                db.query(Article)
                .filter(Article.scholar_run_id == current_run.id)
                .all()
            )
            for article in articles:
                source_json = (
                    dict(article.source_json)
                    if isinstance(article.source_json, dict)
                    else {}
                )
                candidate_id = str(source_json.get("guided_candidate_id") or "")
                if candidate_id not in roles:
                    continue
                source_json["consultant_evidence_role"] = roles[candidate_id]
                article.source_json = source_json
                db.add(article)
                changed_articles += 1
            db.commit()

        source_path = agent._sources_path(project)
        payload = read_json(source_path)
        artifact_sources = payload.get("sources")
        if isinstance(artifact_sources, list):
            for source in artifact_sources:
                if not isinstance(source, dict):
                    continue
                candidate_id = str(source.get("candidate_id") or "")
                if candidate_id in roles:
                    source["consultant_evidence_role"] = roles[candidate_id]
        payload["sources"] = artifact_sources or [
            row
            for row in sources
            if row.get("consultant_decision") == "accepted"
        ]
        write_json(source_path, payload)
        print(
            {
                "ok": True,
                "changed_sources": changed_sources,
                "changed_articles": changed_articles,
                "roles": roles,
            }
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
