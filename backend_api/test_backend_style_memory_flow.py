# test_backend_style_memory_flow.py
# Teste prepare-sources + run-agent avec CIR_STYLE_MEMORY branchée dans EnnoDiagnostic.

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
    print("TEST BACKEND — EnnoDiagnostic + CIR_STYLE_MEMORY")
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
    save_json("backend_style_prepare_sources.json", prepare)
    print("✅ Sources préparées")
    print("documents_loaded_count:", prepare.get("documents_loaded_count"))
    print("chunks_indexed:", (prepare.get("index_report") or {}).get("chunks_indexed"))

    print("\n2) run-agent avec mémoire rédactionnelle")
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
    save_json("backend_style_run_agent.json", run)
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

    print("\n3) latest")
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
    save_json("backend_style_latest.json", latest)

    display = latest.get("display") or {}
    report = display.get("report_markdown") or ""
    Path("backend_style_report.txt").write_text(report, encoding="utf-8")

    style = display.get("style_memory") or {}
    print("\nMémoire style CIR")
    print("ok:", display.get("style_memory_ok") or style.get("ok"))
    print("examples_count:", display.get("style_memory_examples_count") or style.get("examples_count"))
    print("roles:", display.get("style_memory_roles") or style.get("examples_by_role_count"))
    print("stats:", display.get("style_memory_stats") or style.get("stats"))

    print("\nSections rapport")
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

    print("\nQualité verrous")
    generic_terms = [
        "Performance insuffisante sous contrainte",
        "Cause racine inconnue",
        "Compromis entre contraintes contradictoires",
        "Qualité de sortie non conforme",
        "Non-transférabilité des solutions existantes",
    ]

    found_generic = [x for x in generic_terms if x.lower() in report.lower()]
    if found_generic:
        print("⚠ Termes encore génériques trouvés:", found_generic)
    else:
        print("✅ Pas de titres génériques exacts détectés")

    technical_terms = [
        "vibro-acoustique",
        "thermique",
        "réfrigérant",
        "soufflage carter",
        "segments",
        "contrepoids",
        "équilibrage",
    ]
    found_technical = [x for x in technical_terms if x.lower() in report.lower()]
    print("Termes techniques présents:", found_technical)

    print("\nFichiers créés :")
    print("- backend_style_latest.json")
    print("- backend_style_report.txt")


if __name__ == "__main__":
    main()
