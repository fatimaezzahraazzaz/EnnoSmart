# -*- coding: utf-8 -*-
from __future__ import annotations

"""Reconstruit la sélection et les Article Cards sans relancer la recherche."""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend_api"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(ROOT / ".env", override=False)
load_dotenv(BACKEND / ".env", override=False)

from db.database import SessionLocal
from db.models import Project
from services.article_card_builder import build_article_cards_for_selected_articles
from services.scholar_state_of_art_payload_service import (
    build_state_of_art_selection_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True, type=int)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == args.project_id).one()
        selection = build_state_of_art_selection_payload(db, project)
        cards = build_article_cards_for_selected_articles(
            db,
            project,
            mode="auto",
            force=False,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "selection_payload_path": selection.get("payload_path"),
                    "selected_articles_count": cards.get("selected_articles_count"),
                    "writing_ready_cards_count": cards.get(
                        "writing_ready_cards_count"
                    ),
                    "excluded_from_writing_count": cards.get(
                        "excluded_from_writing_count"
                    ),
                    "article_cards_payload_path": cards.get("payload_path"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
