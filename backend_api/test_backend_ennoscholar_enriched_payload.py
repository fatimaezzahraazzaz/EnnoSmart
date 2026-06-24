# test_backend_ennoscholar_enriched_payload.py
# Vérifie que le pont EnnoDiagnostic -> EnnoScholar envoie un payload enrichi,
# pas les titres génériques Frascati.

import json
import os
import sys
from pathlib import Path
import requests

API_BASE = "http://127.0.0.1:8000"
EMAIL = "fatimaezzahra@ennosmart.fr"
PASSWORD = "12345678"
PROJECT_ID = 1
RUN_SCHOLAR = os.getenv("RUN_SCHOLAR", "0").strip() == "1"


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
    print("TEST BACKEND — EnnoScholar payload enrichi")
    print("=" * 100)

    headers = login()
    print("✅ Login OK")

    r = requests.get(
        f"{API_BASE}/projects/{PROJECT_ID}/scholar/payload-preview?max_verrous=8",
        headers=headers,
        timeout=60,
    )
    if r.status_code >= 400:
        print("❌ payload-preview échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    payload = r.json()
    save_json("backend_scholar_enriched_payload_preview.json", payload)

    verrous = payload.get("verrous") or []
    print("selected_verrous_count:", payload.get("selected_verrous_count"))

    if not verrous:
        print("❌ Aucun verrou gardé. Garde d'abord 1 ou 2 verrous dans le frontend.")
        sys.exit(1)

    ok = True
    for v in verrous:
        enrichment = (v.get("source_json") or {}).get("scholar_enrichment", {})
        original = v.get("original_title")
        enriched = v.get("title")
        suggested = v.get("suggested_queries") or []

        print("\n---")
        print("original_title:", original)
        print("enriched_title:", enriched)
        print("profile:", enrichment.get("profile"))
        print("suggested_queries:")
        for q in suggested[:5]:
            print("-", q)

        if not original:
            ok = False
            print("❌ original_title absent : le backend utilise probablement l'ancien scholar_service.py")

        if not enrichment.get("profile"):
            ok = False
            print("❌ profile absent : enrichissement non appliqué")

        if enriched == original:
            ok = False
            print("❌ enriched_title identique au titre original")

        if not suggested:
            ok = False
            print("❌ suggested_queries absent")

    if ok:
        print("\n✅ Les verrous sont enrichis avant EnnoScholar.")
    else:
        print("\n❌ Payload non enrichi. Recopie backend_api/services/scholar_service.py depuis le patch V31.1 puis redémarre uvicorn.")
        sys.exit(1)

    if RUN_SCHOLAR:
        print("\nLancement EnnoScholar réel...")
        r = requests.post(
            f"{API_BASE}/projects/{PROJECT_ID}/scholar/run-from-selected-verrous?max_verrous=3&limit_per_query=3&offline_dry_run=false",
            headers=headers,
            timeout=60 * 10,
        )
        if r.status_code >= 400:
            print("❌ run-from-selected-verrous échoué")
            print(r.status_code, r.text[:5000])
            sys.exit(1)

        run = r.json()
        save_json("backend_scholar_enriched_run.json", run)
        print("✅ Scholar run:", run.get("id"), run.get("status"))

        r = requests.get(f"{API_BASE}/projects/{PROJECT_ID}/scholar/latest", headers=headers, timeout=60)
        latest = r.json()
        save_json("backend_scholar_enriched_latest.json", latest)
        report = (latest.get("bundle") or {}).get("report") or {}
        print("decision_counts:", report.get("decision_counts"))


if __name__ == "__main__":
    main()
