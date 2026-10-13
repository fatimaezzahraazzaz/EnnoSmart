# -*- coding: utf-8 -*-
from __future__ import annotations

"""Récupère les textes intégraux de la sélection EnnoScholar courante.

Ce script de maintenance exécute exactement la même récupération que l'API,
mais affiche une progression article par article. Il est utile lorsqu'un lot
long doit être repris sans dépendre de la durée de vie du processus frontend.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(ROOT_DIR / ".env", override=True)
load_dotenv(BACKEND_DIR / ".env", override=False)

from db.database import SessionLocal  # noqa: E402
from db.models import Project  # noqa: E402
from services.scholar_legal_recovery_service import (  # noqa: E402
    get_combined_fulltext_status_for_selected_articles,
    recover_legal_fulltext_for_article,
)
from services.scholar_selection_scope import get_current_selected_articles  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--search-all", action="store_true")
    parser.add_argument("--unresolved-only", action="store_true")
    parser.add_argument("--article-id", type=int, action="append", dest="article_ids")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == args.project_id).one()
        articles = get_current_selected_articles(db, project)
        if args.article_ids:
            requested_ids = set(args.article_ids)
            articles = [article for article in articles if int(article.id) in requested_ids]
        initial_status = get_combined_fulltext_status_for_selected_articles(db, project)
        category_by_id = {
            int(row["article_id"]): row.get("final_category")
            for row in initial_status.get("results", [])
            if row.get("article_id") is not None
        }
        print(
            f"RECOVERY_START project={project.id} selected={len(articles)} "
            f"force={args.force_refresh} search_all={args.search_all} "
            f"unresolved_only={args.unresolved_only}",
            flush=True,
        )

        for index, article in enumerate(articles, start=1):
            if (
                args.unresolved_only
                and category_by_id.get(int(article.id)) == "verified_fulltext"
            ):
                print(
                    "RECOVERY_PROGRESS "
                    f"{index}/{len(articles)} id={article.id} "
                    "status=skipped_already_verified elapsed=0s",
                    flush=True,
                )
                continue
            started = time.perf_counter()
            try:
                result = recover_legal_fulltext_for_article(
                    db,
                    project,
                    article.id,
                    force_refresh=args.force_refresh,
                    search_all=args.search_all,
                )
                elapsed = round(time.perf_counter() - started, 2)
                print(
                    "RECOVERY_PROGRESS "
                    f"{index}/{len(articles)} id={article.id} "
                    f"status={result.get('status')} "
                    f"chars={result.get('text_chars') or 0} "
                    f"provider={result.get('legal_provider') or '-'} "
                    f"elapsed={elapsed}s",
                    flush=True,
                )
            except Exception as exc:  # poursuit le lot après un échec isolé
                elapsed = round(time.perf_counter() - started, 2)
                print(
                    "RECOVERY_PROGRESS "
                    f"{index}/{len(articles)} id={article.id} "
                    f"status=internal_error error={type(exc).__name__}:{exc} "
                    f"elapsed={elapsed}s",
                    flush=True,
                )

        status = get_combined_fulltext_status_for_selected_articles(db, project)
        summary = {
            key: status.get(key)
            for key in (
                "selected_articles_count",
                "verified_fulltext_count",
                "direct_verified_count",
                "mcp_verified_count",
                "uploaded_verified_count",
                "needs_legal_recovery_count",
                "consultant_upload_required_count",
                "not_checked_count",
            )
        }
        print(f"RECOVERY_DONE {json.dumps(summary, ensure_ascii=False)}", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
