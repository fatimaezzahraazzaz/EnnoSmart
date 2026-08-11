from __future__ import annotations

import os
from pathlib import Path
import sys


TEST_DB = Path(__file__).resolve().parents[2] / ".tmp" / "ennoma_auth_admin_test.db"
TEST_DB.parent.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-with-enough-random-characters"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db.database import Base, SessionLocal, engine
from db.models import User
from routers import admin, auth, projects


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(admin.router)
    return app


def _headers(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_auth_profile_password_reset_and_admin_workflow(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    client = TestClient(_app())

    first = client.post(
        "/auth/register",
        json={
            "full_name": "Olivier Admin",
            "email": "olivier@example.com",
            "password": "AdminPass!123",
            "company": "Ennoma",
        },
    )
    assert first.status_code == 201, first.text

    with SessionLocal() as db:
        olivier = db.query(User).filter(User.email == "olivier@example.com").one()
        olivier.role = "admin"
        db.commit()

    admin_headers = _headers(client, "olivier@example.com", "AdminPass!123")
    account = client.patch(
        "/auth/me/profile",
        headers=admin_headers,
        json={"job_title": "Responsable CIR", "phone": "+212600000000"},
    )
    assert account.status_code == 200, account.text
    assert account.json()["profile"]["job_title"] == "Responsable CIR"

    created = client.post(
        "/admin/users",
        headers=admin_headers,
        json={
            "full_name": "Sofia Consultante",
            "email": "sofia@example.com",
            "password": "Consultant!123",
            "role": "consultant",
        },
    )
    assert created.status_code == 201, created.text
    consultant_id = created.json()["id"]

    project = client.post(
        "/projects",
        headers=admin_headers,
        json={"organisme": "Acme", "project_name": "Vision", "year": "2026"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    assigned = client.patch(
        f"/admin/projects/{project_id}/assignment",
        headers=admin_headers,
        json={"consultant_id": consultant_id},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["consultant"]["id"] == consultant_id

    workflow = client.patch(
        f"/admin/projects/{project_id}/workflow",
        headers=admin_headers,
        json={
            "stage": "recherche_scientifique",
            "progress_percent": 58,
            "priority": "haute",
            "notes": "État de l'art à consolider",
        },
    )
    assert workflow.status_code == 200, workflow.text
    assert workflow.json()["workflow"]["stage"] == "recherche_scientifique"

    forbidden = client.get("/admin/overview", headers=_headers(client, "sofia@example.com", "Consultant!123"))
    assert forbidden.status_code == 403

    forgot = client.post("/auth/forgot-password", json={"email": "sofia@example.com"})
    assert forgot.status_code == 200, forgot.text
    token = forgot.json()["preview_token"]
    reset = client.post("/auth/reset-password", json={"token": token, "password": "NewConsultant!456"})
    assert reset.status_code == 200, reset.text
    assert client.post("/auth/login", json={"email": "sofia@example.com", "password": "NewConsultant!456"}).status_code == 200

    with SessionLocal() as db:
        olivier = db.query(User).filter(User.email == "olivier@example.com").one()
        olivier.role = "superadmin"
        db.commit()

    super_headers = _headers(client, "olivier@example.com", "AdminPass!123")
    monkeypatch.setattr(admin, "write_runtime_ai_settings", lambda _: Path("test-runtime.json"))
    ai_settings = client.put(
        "/admin/ai-settings",
        headers=super_headers,
        json={
            "provider": "openai",
            "primary_model": "gpt-test",
            "writer_model": "gpt-writer-test",
            "fallback_models": ["gpt-fallback-test"],
            "allow_cross_provider_fallback": False,
            "default_temperature": 0.15,
            "max_output_tokens_cap": 12000,
            "max_prompt_chars": 30000,
            "writer_max_prompt_chars": 180000,
            "monthly_budget_eur": 500,
            "enabled_agents": {"diagnostic": True, "scholar": True, "improvement": True, "cir_memory": True},
        },
    )
    assert ai_settings.status_code == 200, ai_settings.text
    assert ai_settings.json()["applied"] is True
