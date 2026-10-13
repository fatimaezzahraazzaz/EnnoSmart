"""Retours consultants : rubriques facultatives et captures envoyées par SMTP."""
from __future__ import annotations

from datetime import datetime
from email.message import EmailMessage
import json
import smtplib
import ssl
from typing import Any

from core.config import settings

RECIPIENT = "fatima-ezzahra.azzaz@ennodev.com"
SECTIONS = {
    "eligibility": "Score d’éligibilité CIR",
    "defensibility": "Score de défendabilité",
    "synthesis": "Synthèse",
    "objective": "Diagnostic — Objectifs",
    "lock": "Diagnostic — Verrous",
    "method": "Diagnostic — Méthodes",
    "demarche": "Diagnostic — Démarches",
    "result": "Diagnostic — Résultats",
    "metric": "Diagnostic — Métriques",
    "parameter": "Diagnostic — Paramètres",
    "ai_detection": "Détection IA",
    "previous_cir": "CIR précédent",
    "comparison": "Comparaison des documents",
    "document_chat": "Chat documentaire",
    "other": "Autre",
}
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024
MAX_FILES = 15


def parse_feedback(payload: str, attachment_sections: str) -> tuple[dict[str, str], list[str]]:
    if len(payload) > 80000 or len(attachment_sections) > 2000:
        raise ValueError("Le retour est trop volumineux.")
    try:
        comments = json.loads(payload)
        sections = json.loads(attachment_sections)
    except (ValueError, TypeError) as exc:
        raise ValueError("Le formulaire de retour est invalide.") from exc
    if not isinstance(comments, dict) or not isinstance(sections, list):
        raise ValueError("Le formulaire de retour est invalide.")
    if any(key not in SECTIONS for key in comments) or any(
        not isinstance(key, str) or key not in SECTIONS for key in sections
    ):
        raise ValueError("Une rubrique du retour est inconnue.")
    if any(not isinstance(value, str) or len(value) > 4000 for value in comments.values()):
        raise ValueError("Chaque commentaire est limité à 4 000 caractères.")
    if len(sections) > MAX_FILES:
        raise ValueError("Vous pouvez joindre au maximum 15 captures.")
    return {key: value.strip() for key, value in comments.items() if value.strip()}, sections


def screenshot_type(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg", "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "webp"
    raise ValueError("Les captures doivent être des images PNG, JPEG ou WebP.")


def build_feedback_email(
    project: Any, user: Any, run_id: int | None, comments: dict[str, str],
    screenshots: list[tuple[str, bytes]],
) -> EmailMessage:
    message = EmailMessage()
    project_name = " ".join(str(project.project_name).splitlines())
    message["Subject"] = f"[EnnoDiagnostic — Retour] {project_name} · {project.year}"
    message["From"] = settings.SMTP_FROM
    message["To"] = RECIPIENT
    message["Reply-To"] = user.email
    lines = [
        "Retour consultant — EnnoDiagnostic", "",
        f"Projet : {project.organisme} / {project.project_name} / {project.year}",
        f"Identifiant projet : {project.id}",
        f"Diagnostic : {run_id if run_id is not None else 'Pas encore exécuté'}",
        f"Envoyé par : {user.full_name} <{user.email}>",
        f"Date : {datetime.now().astimezone().isoformat(timespec='seconds')}", "",
    ]
    for key, label in SECTIONS.items():
        count = sum(section == key for section, _ in screenshots)
        if key not in comments and not count:
            continue
        lines.extend([label, "-" * 40, comments.get(key, "Capture jointe, sans commentaire.")])
        if count:
            lines.append(f"Captures jointes : {count}")
        lines.append("")
    message.set_content("\n".join(lines))
    for index, (section, data) in enumerate(screenshots, 1):
        subtype, extension = screenshot_type(data)
        message.add_attachment(data, maintype="image", subtype=subtype,
                               filename=f"{section}_{index}.{extension}")
    return message


def send_feedback_email(message: EmailMessage) -> None:
    if not settings.SMTP_HOST:
        raise RuntimeError("Le service d’envoi des retours n’est pas configuré.")
    client_class = smtplib.SMTP_SSL if settings.SMTP_PORT == 465 else smtplib.SMTP
    with client_class(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as client:
        if settings.SMTP_USE_TLS and settings.SMTP_PORT != 465:
            client.starttls(context=ssl.create_default_context())
        if settings.SMTP_USER:
            client.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")
        if client.send_message(message):
            raise RuntimeError("Le destinataire a refusé le message.")
