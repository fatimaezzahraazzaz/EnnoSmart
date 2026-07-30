# test_backend_simple_flow_prepare_then_agent.py
# Teste la logique simple :
# 1) prepare-sources = extraction + NLP + Frascati + RAG
# 2) run-agent = EnnoDiagnosticAgent seulement
# 3) sync-verrous
# 4) latest

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


def login():
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
    if not token:
        print("❌ Token introuvable")
        print(r.json())
        sys.exit(1)
    return {"Authorization": f"Bearer {token}"}


def main():
    print("=" * 100)
    print("TEST BACKEND SIMPLE FLOW — prepare-sources puis run-agent")
    print("=" * 100)

    headers = login()
    print("✅ Login OK")

    print("\n1) POST /projects/1/diagnostic/prepare-sources")
    print("⏳ Extraction + NLP + Frascati + RAG/Chroma")
    r = requests.post(
        f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/prepare-sources",
        headers=headers,
        timeout=60 * 45,
    )
    if r.status_code >= 400:
        print("❌ prepare-sources échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    prepare = r.json()
    save_json("backend_prepare_sources.json", prepare)

    print("✅ Sources préparées")
    print("documents_used_count:", prepare.get("documents_used_count"))
    print("documents_loaded_count:", prepare.get("documents_loaded_count"))
    print("nlp_stats:", prepare.get("nlp_stats"))
    print("index_report:", prepare.get("index_report"))

    print("\n2) POST /projects/1/diagnostic/run-agent")
    print("⏳ Score IA + EnnoDiagnosticAgent.generate_diagnostic()")
    r = requests.post(
        f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/run-agent",
        headers=headers,
        timeout=60 * 30,
    )
    if r.status_code >= 400:
        print("❌ run-agent échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    run = r.json()
    run_id = run.get("id")
    save_json("backend_run_agent.json", run)
    print("✅ Run agent créé:", run_id)

    if run_id:
        print(f"\n3) POST /projects/1/diagnostic/{run_id}/sync-verrous")
        r = requests.post(
            f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/{run_id}/sync-verrous",
            headers=headers,
            timeout=120,
        )
        if r.status_code >= 400:
            print("⚠ sync-verrous échoué")
            print(r.status_code, r.text[:3000])
        else:
            verrous = r.json()
            save_json("backend_run_agent_verrous.json", verrous)
            print("✅ Verrous synchronisés:", len(verrous))

    print("\n4) GET /projects/1/diagnostic/latest")
    r = requests.get(
        f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/latest",
        headers=headers,
        timeout=120,
    )
    if r.status_code >= 400:
        print("❌ latest échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    latest = r.json()
    save_json("backend_simple_flow_latest.json", latest)

    display = latest.get("display") or {}
    report = display.get("report_markdown") or ""
    Path("backend_simple_flow_report.txt").write_text(report, encoding="utf-8")

    print("✅ latest sauvegardé : backend_simple_flow_latest.json")
    print("✅ rapport sauvegardé : backend_simple_flow_report.txt")

    print("\n5) Stats latest")
    stats = display.get("pipeline_stats") or {}
    print("Latest run:", (latest.get("latest_run") or {}).get("id"))
    print("documents_loaded_count:", stats.get("documents_loaded_count"))
    print("raw_candidates:", stats.get("raw_candidates"))
    print("raw_kept:", stats.get("raw_kept"))
    print("merged_verrous:", stats.get("merged_verrous"))
    print("chunks_indexed:", stats.get("chunks_indexed"))
    print("validation_verrous:", len(latest.get("validation_verrous") or []))
    print("score IA:", display.get("ai_score"))
    print("niveau IA:", display.get("ai_risk_level"))

    print("\n6) Vérification sections")
    checks = [
        "Lecture Frascati du dossier",
        "Synthèse stratégique du projet",
        "Objectif global reformulé",
        "Verrous R&D / signaux de verrous",
        "Démarche expérimentale détectée",
        "Résultats et métriques disponibles",
        "Paramètres et contraintes techniques",
        "Points à valider par le consultant",
    ]
    for c in checks:
        print(("✅" if c.lower() in report.lower() else "❌"), c)

    if "Développer et valider une architecture de compresseur TGM100".lower() in report.lower():
        print("✅ Objectif TGM100 exact OK")
    else:
        print("⚠ Objectif TGM100 exact non trouvé")


if __name__ == "__main__":
    main()
