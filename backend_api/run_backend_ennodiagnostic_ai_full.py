# run_backend_ennodiagnostic_ai_full.py
# Lance vraiment EnnoDiagnostic + score IA, sans déplacer le détecteur hors agent.

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
    print("=" * 95)
    print("RUN BACKEND COMPLET — EnnoDiagnostic + Score IA dans agent")
    print("=" * 95)

    r = requests.post(f"{API_BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    if r.status_code >= 400:
        print("❌ Login échoué", r.status_code, r.text[:1000])
        sys.exit(1)

    token = r.json().get("access_token") or r.json().get("token") or r.json().get("jwt")
    headers = {"Authorization": f"Bearer {token}"}
    print("✅ Login OK")

    print("\nPOST /projects/1/diagnostic/run ...")
    r = requests.post(f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/run", headers=headers, timeout=60*45)
    if r.status_code >= 400:
        print("❌ Diagnostic run échoué", r.status_code, r.text[:3000])
        sys.exit(1)

    run = r.json()
    run_id = run.get("id")
    print("✅ Nouveau run créé :", run_id)
    save_json("backend_ai_new_run.json", run)

    if run_id:
        r = requests.post(f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/{run_id}/sync-verrous", headers=headers, timeout=120)
        if r.status_code < 400:
            print("✅ Verrous synchronisés :", len(r.json()))
        else:
            print("⚠ Sync verrous échouée", r.status_code)

    r = requests.get(f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/latest", headers=headers, timeout=120)
    latest = r.json()
    save_json("backend_ai_latest_after_run.json", latest)

    display = latest.get("display") or {}
    stats = display.get("pipeline_stats") or {}
    ai = display.get("ai_detection") or {}
    report = display.get("report_markdown") or ""

    save_json("backend_ai_detection_after_run.json", ai)
    Path("backend_ai_report_after_run.txt").write_text(report, encoding="utf-8")

    print("\nStats pipeline")
    print("Latest run:", (latest.get("latest_run") or {}).get("id"))
    print("documents_loaded_count:", stats.get("documents_loaded_count"))
    print("raw_candidates:", stats.get("raw_candidates"))
    print("raw_kept:", stats.get("raw_kept"))
    print("merged_verrous:", stats.get("merged_verrous"))
    print("chunks_indexed:", stats.get("chunks_indexed"))
    print("validation_verrous:", len(latest.get("validation_verrous") or []))

    print("\nScore IA")
    if isinstance(ai, dict) and ai.get("ok"):
        s = ai.get("summary") or {}
        print("✅ Score IA moyen :", s.get("average_ai_percentage"), "%")
        print("✅ Niveau IA :", s.get("risk_level"))
        print("✅ Passages analysés :", s.get("passages_count"))
        print("✅ Passages suspects :", s.get("suspected_passages_count"))
        print("✅ Risque élevé :", s.get("high_count"))
        print("✅ Risque moyen :", s.get("medium_count"))
        top = ai.get("top_passages") or (ai.get("ai_detection") or {}).get("top_passages") or []
        print("\nTop passages suspects")
        for i, p in enumerate(top[:5], 1):
            print("-" * 80)
            print(f"{i}) score={p.get('ai_score')} | niveau={p.get('risk_level')} | doc={p.get('document')}")
            print((p.get("text_excerpt") or p.get("text") or "")[:450])
    else:
        print("❌ Score IA absent ou non OK")
        print(ai)

    print("\nVérification rapport")
    for key in ["Contrôle IA documentaire", "Lecture Frascati du dossier", "Objectif global reformulé"]:
        print(("✅" if key.lower() in report.lower() else "❌"), key)

if __name__ == "__main__":
    main()
