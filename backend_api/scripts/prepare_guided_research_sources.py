# -*- coding: utf-8 -*-
from __future__ import annotations

"""Prépare ou répare des sources déjà acceptées dans la recherche guidée.

Exemple :
    python backend_api/scripts/prepare_guided_research_sources.py \
        --project-id 1 --session-id <uuid> \
        --candidate-id SRC-... --candidate-id SRC-...

Le script réutilise exactement le service de décision public. Il est
idempotent : une source déjà importée ou extraite est réutilisée.
"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend_api"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

load_dotenv(ROOT / ".env", override=False)
load_dotenv(BACKEND / ".env", override=False)

from db.database import SessionLocal
from db.models import Project
from services.guided_research_service import decide_guided_research_sources


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PDF direct puis MCP pour des sources guidées acceptées."
    )
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument("--session-id", required=True)
    parser.add_argument(
        "--candidate-id",
        action="append",
        dest="candidate_ids",
        required=True,
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == args.project_id).one()
        response = decide_guided_research_sources(
            db,
            project,
            session_id=args.session_id,
            candidate_ids=args.candidate_ids,
            decision="accepted",
            reason="Préparation ou reprise explicite du texte intégral.",
            prepare_after_acceptance=True,
        )
        print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
        preparation = (response.get("metadata") or {}).get("source_preparation") or {}
        return 0 if preparation.get("ok") else 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
