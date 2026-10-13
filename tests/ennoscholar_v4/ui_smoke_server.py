"""Local-only UI smoke fixture. No production accounts, providers or LLM calls."""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/"backend_api")]
import os
os.environ["DATABASE_URL"] = "sqlite:///"+str(ROOT/".codex_tmp"/"v4-ui-smoke.sqlite")
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--live", action="store_true", help="Use the real V4 agent and LLM, in an isolated test database.")
parser.add_argument("--env-file", type=Path)
parser.add_argument("--seed-state", type=Path)
parser.add_argument("--port", type=int, default=8014)
args = parser.parse_args() if __name__ == "__main__" else parser.parse_args([])
if args.live:
    if not args.env_file:
        parser.error("--live requires --env-file")
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=False)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.database import Base
from db.models import User
from core.deps import get_current_user
from routers import scholar_v4
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.service import ScholarV4Service


class FixtureAgent:
    def run(self, state, emit, cancelled):
        import time
        emit({"tool": "search_scientific", "status": "running"})
        for _ in range(25):
            if cancelled():
                return {"answer": "Recherche simulée arrêtée.", "reason": "cancelled", "partial": True}
            time.sleep(.1)
        row = Article(id="paper_ui_test", title="Publication de démonstration — test interface", abstract="Source synthétique pour valider uniquement l'interface.",
            authors=["Auteur de test"], year=2025, url="https://example.invalid/publication", verified=True, sources=["ui_fixture"], access_level="abstract_only")
        state["articles"][row.id] = row.durable()
        state["evaluation"] = {"recommended_article_ids": [row.id]}
        emit({"tool": "search_scientific", "status": "completed", "found": 1})
        return {"answer": """## Résultat du test interface

Test simulé uniquement.

| Article | Consultation |
|---|---|
| [Source de test](https://example.invalid/publication) | Abstract seulement |

Aucun fournisseur ou modèle réel n'a été appelé.""", "partial": False, "recommended_article_ids": [row.id]}


engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
factory = sessionmaker(bind=engine)
with factory() as db:
    if db.get(User, 1) is None:
        db.add(User(id=1, full_name="Consultant test V4", email="v4-ui@example.invalid", hashed_password="fixture", is_active=True)); db.commit()
service = ScholarV4Service(factory, ScholarV4Config.from_env() if args.live else ScholarV4Config(api_key="mock"), None if args.live else FixtureAgent)
if args.live and args.seed_state:
    import json
    from agents.EnnoScholarV4.session import ScholarV4Session, sync_records
    seed = json.loads(args.seed_state.read_text(encoding="utf-8"))
    sid = service.create(1, "Validation backend et LLM V4")["id"]
    with factory() as db:
        row = db.get(ScholarV4Session, sid)
        row.data = seed
        sync_records(db, row)
        db.commit()
scholar_v4.get_service = lambda: service
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3014", "http://127.0.0.1:3014"], allow_methods=["*"], allow_headers=["*"])
app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
app.include_router(scholar_v4.router)


@app.post("/auth/login")
def login():
    return {"access_token": "ui-fixture-only", "refresh_token": "ui-fixture-only", "token_type": "bearer"}


@app.get("/auth/me")
def me():
    return {"id": 1, "full_name": "Consultant test V4", "email": "v4-ui@example.invalid", "role": "consultant", "is_active": True, "created_at": "2026-10-12T00:00:00Z"}


@app.get("/admin/agent-availability")
def availability():
    return {"diagnostic": True, "scholar": True, "improvement": True}


@app.get("/projects")
def projects(): return []


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port)
