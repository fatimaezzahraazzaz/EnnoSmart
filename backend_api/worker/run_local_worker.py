from __future__ import annotations

"""Lance le worker Celery local avec un pool compatible Windows.

Le runtime de maintenance peut être distinct du virtualenv principal. Dans ce
cas, on réutilise ses paquets Python sans modifier la configuration du projet.
"""

import sys
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]
LEGACY_SITE_PACKAGES = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    import celery  # noqa: F401
except ModuleNotFoundError:
    if LEGACY_SITE_PACKAGES.exists():
        sys.path.append(str(LEGACY_SITE_PACKAGES))

from worker.celery_app import celery_app


if __name__ == "__main__":
    hostname = sys.argv[1] if len(sys.argv) > 1 else "ennoscholar@%h"
    queue = sys.argv[2] if len(sys.argv) > 2 else "celery"
    pool = os.getenv("ENNOSCHOLAR_CELERY_POOL", "threads")
    concurrency = os.getenv("ENNOSCHOLAR_CELERY_CONCURRENCY", "2")
    celery_app.worker_main(
        [
            "worker",
            "--loglevel=info",
            f"--pool={pool}",
            f"--concurrency={concurrency}",
            f"--hostname={hostname}",
            f"--queues={queue}",
        ]
    )
