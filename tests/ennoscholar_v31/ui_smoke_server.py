"""Local-only UI smoke fixture. No production accounts, providers or LLM calls."""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/"backend_api")]
import os
os.environ["DATABASE_URL"] = "sqlite:///"+str(ROOT/".codex_tmp"/"v31-ui-smoke.sqlite")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.database import Base
from db.models import User
from core.deps import get_current_user
from routers import scholar_v31
from agents.EnnoScholarV31.config import ScholarV31Config
from agents.EnnoScholarV31.models import Article
from agents.EnnoScholarV31.service import ScholarV31Service


class FixtureAgent:
    def run(self, state, emit, cancelled):
        row = Article(id="paper_ui_test", title="Publication de démonstration — test interface", abstract="Source synthétique pour valider uniquement l'interface.",
            authors=["Auteur de test"], year=2025, url="https://example.invalid/publication", verified=True, sources=["ui_fixture"], access_level="abstract_only")
        state["articles"][row.id] = row.durable()
        state["evaluation"] = {"recommended_article_ids": [row.id]}
        emit({"tool": "search_scientific", "status": "completed", "found": 1})
        return {"answer": "Test simulé de l'interface terminé. Aucun fournisseur ou modèle réel n'a été appelé.", "partial": False, "recommended_article_ids": [row.id]}


engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
factory = sessionmaker(bind=engine)
with factory() as db:
    if db.get(User, 1) is None:
        db.add(User(id=1, full_name="Consultant test V3.1", email="v31-ui@example.invalid", hashed_password="fixture", is_active=True)); db.commit()
service = ScholarV31Service(factory, ScholarV31Config(api_key="mock", paid_calls_authorized=True), FixtureAgent)
scholar_v31.get_service = lambda: service
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3002", "http://127.0.0.1:3002"], allow_methods=["*"], allow_headers=["*"])
app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
app.include_router(scholar_v31.router)


@app.post("/auth/login")
def login():
    return {"access_token": "ui-fixture-only", "refresh_token": "ui-fixture-only", "token_type": "bearer"}


@app.get("/auth/me")
def me():
    return {"id": 1, "full_name": "Consultant test V3.1", "email": "v31-ui@example.invalid", "role": "consultant", "is_active": True, "created_at": "2026-10-12T00:00:00Z"}


@app.get("/admin/agent-availability")
def availability():
    return {"diagnostic": True, "scholar": True, "improvement": True}


@app.get("/projects")
def projects(): return []


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8004)
