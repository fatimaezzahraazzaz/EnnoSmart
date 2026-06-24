# test_backend_cir_memory_previous_flow.py
# Teste EnnoDiagnostic avec comparaison CIR précédent intégrée.

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
    print("TEST BACKEND — EnnoDiagnostic + CIR précédent")
    print("=" * 100)

    headers = login()
    print("✅ Login OK")

    print("\n1) prepare-sources")
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
    save_json("backend_cir_prepare_sources.json", prepare)
    print("✅ Sources préparées")
    print("documents_loaded_count:", prepare.get("documents_loaded_count"))
    print("chunks_indexed:", (prepare.get("index_report") or {}).get("chunks_indexed"))

    print("\n2) comparaison CIR mémoire seule")
    r = requests.post(
        f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/cir-memory/compare",
        headers=headers,
        timeout=120,
    )
    if r.status_code >= 400:
        print("⚠ Comparaison seule échouée")
        print(r.status_code, r.text[:5000])
    else:
        cmp_report = r.json()
        save_json("backend_cir_memory_compare_only.json", cmp_report)
        print("✅ Comparaison seule OK")
        print("has_previous_cir:", cmp_report.get("has_previous_cir"))
        print("previous_years:", cmp_report.get("previous_cir_years_used"))
        print("summary:", cmp_report.get("summary"))

    print("\n3) run-agent")
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
    save_json("backend_cir_run_agent.json", run)
    run_id = run.get("id")
    print("✅ Run agent créé:", run_id)

    if run_id:
        r = requests.post(
            f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/{run_id}/sync-verrous",
            headers=headers,
            timeout=120,
        )
        if r.status_code >= 400:
            print("⚠ sync-verrous échoué")
            print(r.status_code, r.text[:3000])
        else:
            print("✅ Verrous synchronisés:", len(r.json()))

    print("\n4) latest")
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
    save_json("backend_cir_latest.json", latest)

    display = latest.get("display") or {}
    report = display.get("report_markdown") or ""
    Path("backend_cir_report.txt").write_text(report, encoding="utf-8")

    cir = display.get("cir_memory") or {}
    summary = display.get("cir_memory_summary") or {}

    print("\nCIR précédent")
    print("ok:", display.get("cir_memory_ok") or cir.get("ok"))
    print("has_previous:", display.get("cir_memory_has_previous") or cir.get("has_previous_cir"))
    print("previous_years:", display.get("cir_memory_previous_years") or cir.get("previous_cir_years_used"))
    print("project_novelty_score:", display.get("cir_memory_project_novelty_score") or summary.get("project_novelty_score"))
    print("signal:", display.get("cir_memory_signal") or summary.get("frascati_context_signal"))
    print("new_verrou_count:", summary.get("new_verrou_count"))
    print("evolution_verrou_count:", summary.get("evolution_verrou_count"))
    print("continuity_verrou_count:", summary.get("continuity_verrou_count"))

    if not (display.get("cir_memory_ok") or cir.get("ok")):
        print("⚠ Détail cir_memory:", cir)

    print("\nSections rapport")
    checks = [
        "Lecture Frascati du dossier",
        "Synthèse stratégique du projet",
        "Objectif global reformulé",
        "Verrous R&D / signaux de verrous",
        "Démarche expérimentale détectée",
        "Résultats et métriques disponibles",
        "Paramètres et contraintes techniques",
        "Comparaison avec le CIR précédent",
        "Points à valider par le consultant",
    ]
    for c in checks:
        print(("✅" if c.lower() in report.lower() else "❌"), c)

    print("\nFichiers créés :")
    print("- backend_cir_latest.json")
    print("- backend_cir_report.txt")
    print("- backend_cir_memory_compare_only.json si comparaison seule OK")


if __name__ == "__main__":
    main()
