from __future__ import annotations

"""Configuration Celery EnnoSmart / EnnoScholar.

V6.1:
- le .env racine reste chargé pour la configuration globale ;
- le .env backend reste chargé sans écraser globalement le processus ;
- MAIS les clés ENNOSCHOLAR_* du backend deviennent explicitement
  autoritaires pour le worker EnnoScholar.

Cela évite qu'une ancienne valeur présente dans C:\EnnoSmart\.env conserve
par exemple 10 workers MCP ou un ancien timeout alors que la V6 a été
configurée dans backend_api\.env.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv, dotenv_values
from celery import Celery


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent

for path in (BACKEND_DIR, PROJECT_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)


ENV_FILE = BACKEND_DIR / ".env"
PROJECT_ENV_FILE = PROJECT_ROOT / ".env"

# Configuration générale historique.
if PROJECT_ENV_FILE.exists():
    load_dotenv(dotenv_path=PROJECT_ENV_FILE, override=False)

# Complète les variables absentes avec le .env backend.
if ENV_FILE.exists():
    load_dotenv(dotenv_path=ENV_FILE, override=False)

# IMPORTANT V6.1 :
# les paramètres propres à EnnoScholar doivent provenir du backend.
# On n'écrase PAS DATABASE_URL, OPENAI_API_KEY, etc.
if ENV_FILE.exists():
    backend_values = dotenv_values(ENV_FILE)
    for key, value in backend_values.items():
        if (
            isinstance(key, str)
            and key.startswith("ENNOSCHOLAR_")
            and value is not None
        ):
            os.environ[key] = str(value)


BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    "redis://127.0.0.1:6379/0",
)

RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    "redis://127.0.0.1:6379/0",
)


celery_app = Celery(
    "ennosmart",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
)
