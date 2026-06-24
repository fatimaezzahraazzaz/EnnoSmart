# test_backend_ennoscholar_selected_verrous_flow.py
# Teste le lien EnnoDiagnostic -> EnnoScholar :
# verrous gardés par le consultant -> recherche scientifique -> articles liés.

import json
import os
import sys
from pathlib import Path
import requests

API_BASE = "http://127.0.0.1:8000"
EMAIL = "fatimaezzahra@ennosmart.fr"
PASSWORD = "12345678"
PROJECT_ID = 1

# Mets ENNOSCHOLAR_TEST_OFFLINE=1 si tu veux tester sans appeler les bases web.
OFFLINE = os.getenv("ENNOSCHOLAR_TEST_OFFLINE", "0").strip() == "1"


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
    print("TEST BACKEND — EnnoDiagnostic -> EnnoScholar")
    print("=" * 100)

    headers = login()
    print("✅ Login OK")

    print("\n1) Verrous EnnoDiagnostic")
    r = requests.get(f"{API_BASE}/projects/{PROJECT_ID}/verrous", headers=headers, timeout=60)
    if r.status_code >= 400:
        print("❌ GET verrous échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    verrous = r.json()
    print("Verrous:", len(verrous))

    gardes = [v for v in verrous if v.get("consultant_status") == "garde"]
    if not gardes:
        print("⚠ Aucun verrou gardé. Je garde automatiquement les 2 premiers pour tester.")
        for v in verrous[:2]:
            rr = requests.patch(
                f"{API_BASE}/projects/{PROJECT_ID}/verrous/{v['id']}/decision",
                headers={**headers, "Content-Type": "application/json"},
                json={"consultant_status": "garde"},
                timeout=60,
            )
            if rr.status_code >= 400:
                print("❌ Impossible de garder le verrou", v.get("id"))
                print(rr.status_code, rr.text[:2000])
                sys.exit(1)
        r = requests.get(f"{API_BASE}/projects/{PROJECT_ID}/verrous", headers=headers, timeout=60)
        verrous = r.json()
        gardes = [v for v in verrous if v.get("consultant_status") == "garde"]

    print("✅ Verrous gardés pour EnnoScholar:", len(gardes))
    for v in gardes[:5]:
        print("-", v.get("id"), v.get("title"))

    print("\n2) Payload preview")
    r = requests.get(
        f"{API_BASE}/projects/{PROJECT_ID}/scholar/payload-preview?max_verrous=5",
        headers=headers,
        timeout=60,
    )
    if r.status_code >= 400:
        print("❌ payload-preview échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    payload = r.json()
    save_json("backend_scholar_payload_preview.json", payload)
    print("selected_verrous_count:", payload.get("selected_verrous_count"))

    print("\n3) Lancement EnnoScholar")
    r = requests.post(
        f"{API_BASE}/projects/{PROJECT_ID}/scholar/run-from-selected-verrous"
        f"?max_verrous=3&limit_per_query=2&offline_dry_run={'true' if OFFLINE else 'false'}",
        headers=headers,
        timeout=60 * 10,
    )
    if r.status_code >= 400:
        print("❌ run-from-selected-verrous échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    run = r.json()
    save_json("backend_scholar_run.json", run)
    run_id = run.get("id")
    print("✅ Scholar run créé:", run_id)
    print("status:", run.get("status"))

    print("\n4) Latest EnnoScholar")
    r = requests.get(f"{API_BASE}/projects/{PROJECT_ID}/scholar/latest", headers=headers, timeout=120)
    if r.status_code >= 400:
        print("❌ scholar latest échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    latest = r.json()
    save_json("backend_scholar_latest.json", latest)

    report = (latest.get("bundle") or {}).get("report") or {}
    summary = (latest.get("bundle") or {}).get("summary") or {}

    print("verrous_analyzed:", report.get("verrous_analyzed"))
    print("decision_counts:", report.get("decision_counts"))
    print("summary:", summary)

    print("\n5) Articles synchronisés")
    r = requests.get(f"{API_BASE}/projects/{PROJECT_ID}/articles", headers=headers, timeout=120)
    if r.status_code >= 400:
        print("❌ GET articles échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    articles = r.json()
    save_json("backend_scholar_articles.json", articles)
    print("articles:", len(articles))
    for a in articles[:5]:
        print("-", a.get("tag_article"), a.get("score"), a.get("title"), "| verrou", a.get("verrou_id"))

    print("\nFichiers créés :")
    print("- backend_scholar_payload_preview.json")
    print("- backend_scholar_run.json")
    print("- backend_scholar_latest.json")
    print("- backend_scholar_articles.json")


if __name__ == "__main__":
    main()
