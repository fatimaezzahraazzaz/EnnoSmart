# test_backend_document_compare_flow.py
# Teste la comparaison des documents bruts : auto-paires + comparaison A/B.

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
    print("TEST BACKEND — Comparaison documents bruts")
    print("=" * 100)

    headers = login()
    print("✅ Login OK")

    print("\n1) Détection des paires comparables")
    r = requests.post(
        f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/document-compare/auto-pairs",
        headers=headers,
        json={"min_similarity": 0.70, "include_medium": True, "force": True},
        timeout=120,
    )
    if r.status_code >= 400:
        print("❌ auto-pairs échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    index = r.json()
    save_json("backend_document_compare_index.json", index)

    pairs = index.get("pairs") or []
    print("✅ Paires détectées:", len(pairs))
    for i, p in enumerate(pairs[:10]):
        print(f"- [{i}] {p.get('decision')} sim={p.get('similarity')} :: {p.get('name_a')} VS {p.get('name_b')}")

    if not pairs:
        print("⚠ Aucune paire détectée. Le backend est OK mais il n'y a rien à comparer.")
        return

    print("\n2) Comparaison de la première paire")
    r = requests.post(
        f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/document-compare/compare-pair",
        headers=headers,
        json={"pair_index": 0, "force": True},
        timeout=60 * 10,
    )
    if r.status_code >= 400:
        print("❌ compare-pair échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    report = r.json()
    save_json("backend_document_compare_pair_report.json", report)

    summary = report.get("summary") or {}
    print("✅ Comparaison paire OK")
    print("doc_a:", summary.get("doc_a"))
    print("doc_b:", summary.get("doc_b"))
    print("change_rate:", summary.get("change_rate"))
    print("identical_count:", summary.get("identical_count"))
    print("different_count:", summary.get("different_count"))
    print("only_in_a_count:", summary.get("only_in_a_count"))
    print("only_in_b_count:", summary.get("only_in_b_count"))

    print("\n3) Latest expose l'index au frontend")
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
    save_json("backend_document_compare_latest.json", latest)
    display = latest.get("display") or {}

    print("document_compare_ok:", display.get("document_compare_ok"))
    print("document_compare_pairs_count:", display.get("document_compare_pairs_count"))

    print("\nFichiers créés :")
    print("- backend_document_compare_index.json")
    print("- backend_document_compare_pair_report.json")
    print("- backend_document_compare_latest.json")


if __name__ == "__main__":
    main()
