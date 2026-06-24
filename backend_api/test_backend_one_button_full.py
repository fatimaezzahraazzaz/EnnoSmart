# test_backend_one_button_full.py
# Teste l'ancien mode complet en un bouton :
# POST /diagnostic/run = extraction + NLP + RAG + Score IA + Agent

import json
import sys
from pathlib import Path
import requests

API_BASE = "http://127.0.0.1:8000"
EMAIL = "fatimaezzahra@ennosmart.fr"
PASSWORD = "12345678"
PROJECT_ID = 1


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    print("=" * 100)
    print("TEST BACKEND ONE BUTTON — diagnostic/run complet")
    print("=" * 100)

    r = requests.post(
        f"{API_BASE}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    if r.status_code >= 400:
        print("❌ Login échoué")
        print(r.status_code, r.text[:3000])
        sys.exit(1)

    token = r.json().get("access_token") or r.json().get("token") or r.json().get("jwt")
    headers = {"Authorization": f"Bearer {token}"}
    print("✅ Login OK")

    print("\nPOST /projects/1/diagnostic/run")
    r = requests.post(
        f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/run",
        headers=headers,
        timeout=60 * 45,
    )
    if r.status_code >= 400:
        print("❌ run complet échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    run = r.json()
    save_json("backend_one_button_run.json", run)
    print("✅ Nouveau run:", run.get("id"))

    print("\nGET latest")
    r = requests.get(
        f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/latest",
        headers=headers,
        timeout=120,
    )
    latest = r.json()
    save_json("backend_one_button_latest.json", latest)

    display = latest.get("display") or {}
    report = display.get("report_markdown") or ""
    Path("backend_one_button_report.txt").write_text(report, encoding="utf-8")

    stats = display.get("pipeline_stats") or {}
    print("documents_loaded_count:", stats.get("documents_loaded_count"))
    print("raw_candidates:", stats.get("raw_candidates"))
    print("chunks_indexed:", stats.get("chunks_indexed"))
    print("score IA:", display.get("ai_score"))
    print("niveau IA:", display.get("ai_risk_level"))

    for c in [
        "Lecture Frascati du dossier",
        "Synthèse stratégique du projet",
        "Objectif global reformulé",
        "Démarche expérimentale détectée",
        "Points à valider par le consultant",
    ]:
        print(("✅" if c.lower() in report.lower() else "❌"), c)


if __name__ == "__main__":
    main()
