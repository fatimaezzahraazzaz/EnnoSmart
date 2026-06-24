# test_backend_document_compare_manual_upload.py
# Teste le mode Streamlit-like : uploader manuellement 2 fichiers puis comparer A/B.

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
    print("TEST BACKEND — Upload manuel 2 documents + comparaison A/B")
    print("=" * 100)

    headers = login()
    print("✅ Login OK")

    # On récupère d'abord une paire existante pour tester l'upload manuel avec de vrais fichiers raw.
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
    pairs = index.get("pairs") or []
    if not pairs:
        print("⚠ Aucune paire auto détectée, impossible de tester l'upload manuel automatiquement.")
        return

    pair = pairs[0]
    file_a = Path(pair["file_a"])
    file_b = Path(pair["file_b"])

    if not file_a.exists() or not file_b.exists():
        print("❌ Les fichiers de la paire n'existent pas localement")
        print(file_a, file_a.exists())
        print(file_b, file_b.exists())
        sys.exit(1)

    print("Fichier A:", file_a.name)
    print("Fichier B:", file_b.name)

    print("\n1) Upload manuel et comparaison")
    with file_a.open("rb") as fa, file_b.open("rb") as fb:
        r = requests.post(
            f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/document-compare/upload-pair",
            headers=headers,
            files={
                "file_a": (file_a.name, fa),
                "file_b": (file_b.name, fb),
            },
            timeout=60 * 10,
        )

    if r.status_code >= 400:
        print("❌ upload-pair échoué")
        print(r.status_code, r.text[:5000])
        sys.exit(1)

    report = r.json()
    save_json("backend_document_compare_manual_upload_report.json", report)

    summary = report.get("summary") or {}
    manual = report.get("manual_upload") or {}

    print("✅ Upload manuel + comparaison OK")
    print("manual_upload:", manual.get("ok"))
    print("doc_a:", summary.get("doc_a"))
    print("doc_b:", summary.get("doc_b"))
    print("change_rate:", summary.get("change_rate"))
    print("identical_count:", summary.get("identical_count"))
    print("different_count:", summary.get("different_count"))
    print("only_in_a_count:", summary.get("only_in_a_count"))
    print("only_in_b_count:", summary.get("only_in_b_count"))

    print("\nFichier créé :")
    print("- backend_document_compare_manual_upload_report.json")


if __name__ == "__main__":
    main()
