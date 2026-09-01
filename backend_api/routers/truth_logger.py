# -*- coding: utf-8 -*-
from __future__ import annotations

"""
EnnoSmart V76 — Truth Logger backend

But : écrire un log simple et honnête de ce qui est réellement appelé par chaque bouton.
Fichier de log : <racine-projet>/logs/truth_actions.log
Endpoint : GET /debug/truth-log/latest
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware
from modules.common.runtime_paths import logs_root

LOG_DIR = logs_root()
LOG_FILE = LOG_DIR / "truth_actions.log"

router = APIRouter(prefix="/debug", tags=["debug-truth-log"])

ACTION_MAP = {
    "/diagnostic/prepare-sources": {
        "button": "Préparer les sources",
        "expected": "Extraction documents + OCR si besoin + NLP local + Frascati + chunks + RAG/Chroma",
        "should_not": "LLM de rédaction finale / EnnoScholar / comparaison CIR précédente lourde",
    },
    "/diagnostic/run-agent": {
        "button": "Lancer EnnoDiagnostic",
        "expected": "Agent LLM de reformulation depuis les sources déjà préparées / RAG",
        "should_not": "OCR complet / NLP complet / réindexation Chroma / recalcul score IA documentaire si désactivé",
    },
    "/diagnostic/run-nlp-only": {
        "button": "Diagnostic NLP only",
        "expected": "Diagnostic rapide sans LLM",
        "should_not": "LLM / RAG lourd / comparaison CIR",
    },
    "/cir-previous/upload-final": {
        "button": "Ajouter CIR final précédent",
        "expected": "Extraction mémoire CIR N-1 et stockage comme mémoire, pas comme document brut courant",
        "should_not": "Diagnostic complet du projet courant",
    },
    "/cir-source-view/open-passage": {
        "button": "Ouvrir passage source",
        "expected": "Chercher source exacte + excerpt + générer preview document avec passage encadré",
        "should_not": "Relancer diagnostic / LLM / NLP complet",
    },
    "/diagnostic/document-compare/auto-pairs": {
        "button": "Détecter les paires documentaires",
        "expected": "Comparer noms/contenus des documents pour proposer des paires A/B",
        "should_not": "Diagnostic CIR complet",
    },
    "/diagnostic/document-compare/compare-pair": {
        "button": "Comparer une paire documentaire",
        "expected": "Comparer seulement la paire A/B sélectionnée",
        "should_not": "Relancer EnnoDiagnostic",
    },
    "/diagnostic/document-compare/upload-pair": {
        "button": "Comparer manuellement deux documents",
        "expected": "Comparer seulement les deux fichiers uploadés",
        "should_not": "Modifier le dossier courant / relancer le diagnostic",
    },
    "/scholar/run-from-selected-verrous": {
        "button": "Lancer EnnoScholar",
        "expected": "Recherche scientifique depuis les verrous retenus par le consultant",
        "should_not": "Recréer les verrous NLP / diagnostic complet",
    },
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _safe_json(value: Any, max_chars: int = 4000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) > max_chars:
        return text[:max_chars] + "...<truncated>"
    return text


def _path_action(path: str) -> Dict[str, str]:
    for suffix, info in ACTION_MAP.items():
        if path.endswith(suffix) or suffix in path:
            return info
    return {
        "button": "Route API appelée directement ou chargement automatique",
        "expected": "Voir le chemin exact de la route",
        "should_not": "—",
    }


def _extract_project_id(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    for i, part in enumerate(parts):
        if part == "projects" and i + 1 < len(parts):
            return parts[i + 1]
    return "—"


def write_truth_event(event: Dict[str, Any]) -> None:
    _ensure_log_dir()
    line = _safe_json(event, max_chars=12000)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


class TruthLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        watch = any(key in path for key in [
            "/diagnostic/",
            "/cir-previous/",
            "/cir-source-view/",
            "/source-highlight/",
            "/scholar/",
        ])

        if not watch:
            return await call_next(request)

        started = time.time()
        body_preview: Any = None

        if request.method in {"POST", "PUT", "PATCH"}:
            try:
                raw = await request.body()
                if raw:
                    content_type = request.headers.get("content-type", "")
                    if "application/json" in content_type:
                        try:
                            body_preview = json.loads(raw.decode("utf-8", errors="ignore"))
                        except Exception:
                            body_preview = raw.decode("utf-8", errors="ignore")[:2000]
                    else:
                        body_preview = f"<{content_type or 'body'} len={len(raw)}>"
            except Exception as exc:
                body_preview = f"<body unreadable: {exc}>"

        info = _path_action(path)
        start_event = {
            "time": _now(),
            "phase": "START",
            "method": request.method,
            "path": path,
            "project_id": _extract_project_id(path),
            "button_header": request.headers.get("x-enno-button") or request.headers.get("x-ennosmart-button"),
            "button_detected": info["button"],
            "expected_pipeline": request.headers.get("x-enno-expected-pipeline") or info["expected"],
            "should_not_launch": info["should_not"],
            "query": str(request.url.query or ""),
            "body_preview": body_preview,
        }
        write_truth_event(start_event)
        print(f"[TRUTH][START] {request.method} {path} | button={start_event['button_header'] or info['button']} | expected={start_event['expected_pipeline']}")

        try:
            response = await call_next(request)
            duration_ms = round((time.time() - started) * 1000, 2)
            end_event = {
                "time": _now(),
                "phase": "END",
                "method": request.method,
                "path": path,
                "project_id": _extract_project_id(path),
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            }
            write_truth_event(end_event)
            print(f"[TRUTH][END] {request.method} {path} | status={response.status_code} | duration_ms={duration_ms}")
            return response
        except Exception as exc:
            duration_ms = round((time.time() - started) * 1000, 2)
            err_event = {
                "time": _now(),
                "phase": "ERROR",
                "method": request.method,
                "path": path,
                "project_id": _extract_project_id(path),
                "duration_ms": duration_ms,
                "error": repr(exc),
            }
            write_truth_event(err_event)
            print(f"[TRUTH][ERROR] {request.method} {path} | duration_ms={duration_ms} | error={exc}")
            raise


def install_truth_logger(app) -> None:
    app.add_middleware(TruthLoggerMiddleware)


@router.get("/truth-log/latest")
def latest_truth_log(lines: int = 80):
    _ensure_log_dir()
    if not LOG_FILE.exists():
        return JSONResponse({"ok": True, "log_file": str(LOG_FILE), "items": []})

    raw_lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-max(1, min(lines, 500)):]
    items = []
    for line in raw_lines:
        try:
            items.append(json.loads(line))
        except Exception:
            items.append({"raw": line})
    return {"ok": True, "log_file": str(LOG_FILE), "items": items}


@router.get("/truth-log/text", response_class=PlainTextResponse)
def latest_truth_log_text(lines: int = 120):
    _ensure_log_dir()
    if not LOG_FILE.exists():
        return f"Aucun log pour le moment. Fichier attendu : {LOG_FILE}"
    raw_lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-max(1, min(lines, 500)):]
    return "\n".join(raw_lines)
