from __future__ import annotations
import os
import sys

ROOT = os.path.abspath(os.path.dirname(__file__))
BACKEND = os.path.join(ROOT, "backend_api")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents.EnnoScholar.opencitations_client import OpenCitationsClient

def main():
    print("=== EnnoScholar V2 runtime check ===")
    oc = OpenCitationsClient()
    rows = oc.neighbours("10.1038/nature12373", references=1, citations=1)
    print("OpenCitations:", "OK" if rows else "NO_RESULT", len(rows))

    try:
        from services.research_runtime import runtime_status
        print("Runtime:", runtime_status())
    except Exception as exc:
        print("Runtime: ERROR", type(exc).__name__, exc)

    try:
        from services.grobid_client import GrobidClient
        grobid = GrobidClient()
        print("GROBID:", "OK" if grobid.alive() else "OFFLINE")
    except Exception as exc:
        print("GROBID: ERROR", type(exc).__name__, exc)

    try:
        from worker.celery_app import celery_app
        print("Celery import: OK", celery_app.main)
    except Exception as exc:
        print("Celery import: ERROR", type(exc).__name__, exc)

if __name__ == "__main__":
    main()
