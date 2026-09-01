from __future__ import annotations

"""Centralise les chemins de code et de donnees d'execution EnnoSmart.

Le depot applicatif doit rester immuable en production. Les documents client,
les JSON NLP, Chroma, les rapports et les caches persistants vivent donc sous
``ENNOSMART_DATA_ROOT`` (ou sous les racines specialisees explicites).
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Dict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _file_configuration() -> Dict[str, str]:
    """Lit les .env sans modifier globalement ``os.environ``.

    Le fichier racine est l'autorite principale ; ``backend_api/.env`` reste
    un fallback pour les options historiques propres a l'API.
    """

    try:
        from dotenv import dotenv_values
    except Exception:
        return {}

    merged: Dict[str, str] = {}
    for path in (PROJECT_ROOT / "backend_api" / ".env", PROJECT_ROOT / ".env"):
        if not path.is_file():
            continue
        try:
            for key, value in (dotenv_values(path) or {}).items():
                if value is not None:
                    merged[str(key)] = str(value)
        except Exception:
            continue
    return merged


def _configured(name: str) -> str:
    return str(os.getenv(name) or _file_configuration().get(name) or "").strip()


def code_root() -> Path:
    """Racine du code, jamais utilisee comme stockage d'execution."""

    configured = _configured("ENNOSMART_ROOT")
    return Path(configured).expanduser() if configured else PROJECT_ROOT


def default_data_root() -> Path:
    """Racine externe sure lorsque aucune configuration n'est fournie."""

    if os.name == "nt":
        return PROJECT_ROOT.parent / "EnnoSmartData"
    xdg_data_home = str(os.getenv("XDG_DATA_HOME") or "").strip()
    if xdg_data_home:
        return Path(xdg_data_home).expanduser() / "ennosmart"
    return Path.home() / ".local" / "share" / "ennosmart"


def data_root() -> Path:
    configured = _configured("ENNOSMART_DATA_ROOT")
    return Path(configured).expanduser() if configured else default_data_root()


def storage_root() -> Path:
    configured = _configured("ENNOSMART_STORAGE_ROOT")
    return Path(configured).expanduser() if configured else data_root() / "storage"


def outputs_root() -> Path:
    configured = _configured("AI_OUTPUT_ROOT")
    return (
        Path(configured).expanduser()
        if configured
        else data_root() / "outputs" / "safe_rag_upload"
    )


def uploads_root() -> Path:
    configured = _configured("UPLOAD_ROOT")
    return Path(configured).expanduser() if configured else storage_root() / "uploads"


def experience_memory_root() -> Path:
    configured = _configured("ENNOSMART_EXPERIENCE_MEMORY_V2_DIR")
    return (
        Path(configured).expanduser()
        if configured
        else storage_root() / "experience_memory_v2"
    )


def organism_memory_root() -> Path:
    configured = _configured("ENNOSMART_MEMORY_V2_ROOT")
    return (
        Path(configured).expanduser()
        if configured
        else storage_root() / "organismes"
    )


def audit_root() -> Path:
    configured = _configured("POWER_AUTOMATE_AUDIT_ROOT")
    return (
        Path(configured).expanduser()
        if configured
        else storage_root() / "power_automate_import"
    )


def logs_root() -> Path:
    configured = _configured("ENNOSMART_LOG_ROOT")
    return Path(configured).expanduser() if configured else data_root() / "logs"


def cache_root() -> Path:
    configured = _configured("ENNOSMART_CACHE_ROOT")
    return Path(configured).expanduser() if configured else data_root() / "cache"


def resolve_persisted_path(value: object) -> Path | None:
    """Résout un ancien chemin Windows après migration vers Linux/OVH.

    Les métadonnées JSON/Chroma restent lisibles sans réécrire les index : un
    chemin contenant ``storage`` ou ``safe_rag_upload`` est rebasé sur les
    racines persistantes actuelles.
    """

    raw = str(value or "").strip().strip('"').strip("'")
    if not raw:
        return None

    direct = Path(raw).expanduser()
    parts = [part for part in raw.replace("\\", "/").split("/") if part]
    lowered = [part.lower() for part in parts]
    candidates: list[Path] = []

    if "safe_rag_upload" in lowered:
        index = lowered.index("safe_rag_upload")
        candidates.append(outputs_root().joinpath(*parts[index + 1 :]))
    if "storage" in lowered:
        index = lowered.index("storage")
        candidates.append(storage_root().joinpath(*parts[index + 1 :]))

    if not direct.is_absolute():
        candidates.extend((storage_root() / direct, outputs_root() / direct))

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.exists():
                return resolved
        except Exception:
            continue
    if direct.exists():
        return direct.resolve()
    return None
