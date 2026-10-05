"""Contrat HTTP/e-mail des retours ; aucun e-mail réel ni appel LLM."""
import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path[:0] = [str(Path(__file__).parent / "backend_api"), str(Path(__file__).parent)]
from core.deps import get_current_user, get_db
from routers import diagnostic_feedback as route
from services import diagnostic_feedback_service as service

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7XYAAAAASUVORK5CYII=")
USER = SimpleNamespace(id=1, full_name="Consultant test", email="test@example.org")
PROJECT = SimpleNamespace(id=12, organisme="Entreprise", project_name="Projet test", year="2025")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(route.router)
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = SimpleNamespace(id=29)
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_db] = lambda: db
    with patch.object(route, "get_project_for_user", return_value=PROJECT), patch.object(route, "send_feedback_email") as send:
        yield TestClient(app), send, app


def submit(client, comments=None, sections=None, files=None):
    return client.post("/projects/12/diagnostic-feedback", data={
        "payload": json.dumps(comments or {}),
        "attachment_sections": json.dumps(sections or []),
    }, files=files)


def test_one_optional_section_sent_with_context_and_fixed_recipient(client):
    http, send, _ = client
    response = submit(http, {"eligibility": "Le score mérite une explication."})
    assert response.status_code == 200
    email = send.call_args.args[0]
    assert email["To"] == "fatima-ezzahra.azzaz@ennodev.com"
    assert email["Reply-To"] == USER.email
    body = email.get_content()
    assert "Score d’éligibilité CIR" in body and "Diagnostic : 29" in body
    assert "Projet test" in body and USER.full_name in body
    assert "Score de défendabilité" not in body


def test_screenshot_only_and_section_association(client):
    http, send, _ = client
    response = submit(http, sections=["lock"], files=[("files", ("capture.png", PNG, "image/png"))])
    assert response.status_code == 200
    email = send.call_args.args[0]
    assert "Diagnostic — Verrous" in email.get_body().get_content()
    attachment, = email.iter_attachments()
    assert attachment.get_filename() == "lock_1.png"
    assert attachment.get_payload(decode=True) == PNG


def test_all_sections_optional_and_grouped_in_email(client):
    http, send, _ = client
    response = submit(http, {key: f"Remarque {key}" for key in service.SECTIONS})
    assert response.status_code == 200
    body = send.call_args.args[0].get_content()
    for label in service.SECTIONS.values():
        assert label in body


@pytest.mark.parametrize("comments,sections,files", [
    ({}, [], None),
    ({"lock": "  "}, [], None),
    ({"unknown": "Texte"}, [], None),
    ({"method": 123}, [], None),
    ({"result": "a" * 4001}, [], None),
    ({"lock": "Texte"}, ["lock"], None),
    ({}, ["unknown"], [("files", ("capture.png", PNG, "image/png"))]),
    ({}, ["lock"], [("files", ("capture.png", b"not an image", "image/png"))]),
])
def test_invalid_return_never_sent(client, comments, sections, files):
    http, send, _ = client
    assert submit(http, comments, sections, files).status_code == 422
    send.assert_not_called()


def test_oversized_capture_not_sent(client):
    http, send, _ = client
    response = submit(http, sections=["other"], files=[("files", ("big.png", PNG + b"x" * service.MAX_FILE_BYTES, "image/png"))])
    assert response.status_code == 422
    send.assert_not_called()


def test_smtp_failure_is_not_success(client):
    http, send, _ = client
    send.side_effect = RuntimeError("SMTP unavailable")
    response = submit(http, {"document_chat": "Réponse incomplète."})
    assert response.status_code == 503
    assert "restent dans le formulaire" in response.json()["detail"]


def test_project_access_required_before_sending(client):
    http, send, _ = client
    with patch.object(route, "get_project_for_user", side_effect=HTTPException(403, "Accès interdit")):
        assert submit(http, {"other": "Texte"}).status_code == 403
    send.assert_not_called()


def test_authentication_required(client):
    http, send, app = client
    app.dependency_overrides.pop(get_current_user)
    assert submit(http, {"other": "Texte"}).status_code in (401, 403)
    send.assert_not_called()


def test_smtp_settings_used_and_recipient_refusal_raised():
    message = service.build_feedback_email(PROJECT, USER, None, {"other": "Texte"}, [])
    with patch.object(service, "settings", SimpleNamespace(SMTP_HOST="smtp.test", SMTP_PORT=587, SMTP_USE_TLS=True, SMTP_USER="user", SMTP_PASSWORD="secret")), patch.object(service.smtplib, "SMTP") as smtp:
        smtp.return_value.__enter__.return_value.send_message.return_value = {}
        service.send_feedback_email(message)
        smtp.assert_called_once_with("smtp.test", 587, timeout=20)
        smtp.return_value.__enter__.return_value.starttls.assert_called_once()
        smtp.return_value.__enter__.return_value.login.assert_called_once_with("user", "secret")
        smtp.return_value.__enter__.return_value.send_message.return_value = {service.RECIPIENT: (550, b"refused")}
        with pytest.raises(RuntimeError):
            service.send_feedback_email(message)


def test_missing_smtp_configuration_never_succeeds():
    message = service.build_feedback_email(PROJECT, USER, None, {}, [])
    with patch.object(service, "settings", SimpleNamespace(SMTP_HOST=None)):
        with pytest.raises(RuntimeError):
            service.send_feedback_email(message)
