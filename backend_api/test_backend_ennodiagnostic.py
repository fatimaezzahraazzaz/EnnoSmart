# test_backend_ennodiagnostic.py
# Teste le backend seul, sans frontend, et vérifie si /diagnostic/latest renvoie le rapport type Streamlit.

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
    print("=" * 80)
    print("TEST BACKEND SEUL — EnnoDiagnostic")
    print("=" * 80)

    print("1) Login...")
    login_payload = {"email": EMAIL, "password": PASSWORD}

    try:
        r = requests.post(f"{API_BASE}/auth/login", json=login_payload, timeout=30)
    except Exception as e:
        print("❌ Backend inaccessible :", e)
        print("➡ Vérifie que uvicorn tourne sur http://127.0.0.1:8000")
        sys.exit(1)

    if r.status_code >= 400:
        print("❌ Login échoué")
        print("Status:", r.status_code)
        print(r.text[:2000])
        sys.exit(1)

    login = r.json()
    token = login.get("access_token") or login.get("token") or login.get("jwt")

    if not token:
        print("❌ Token introuvable dans la réponse login")
        print(login)
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}"}
    print("✅ Login OK")

    print("\n2) GET /projects/1/diagnostic/latest ...")
    r = requests.get(
        f"{API_BASE}/projects/{PROJECT_ID}/diagnostic/latest",
        headers=headers,
        timeout=60,
    )

    if r.status_code >= 400:
        print("❌ diagnostic/latest échoué")
        print("Status:", r.status_code)
        print(r.text[:4000])
        sys.exit(1)

    data = r.json()
    save_json("backend_diagnostic_latest.json", data)
    print("✅ JSON sauvegardé : backend_diagnostic_latest.json")

    display = data.get("display") or {}
    report = display.get("report_markdown") or ""
    objective = display.get("objective") or ""
    pipeline_stats = display.get("pipeline_stats") or {}

    Path("backend_report_markdown.txt").write_text(report, encoding="utf-8")
    print("✅ Rapport sauvegardé : backend_report_markdown.txt")

    print("\n3) Infos backend")
    print("Project:", data.get("project", {}))
    print("Latest run:", (data.get("latest_run") or {}).get("id"))
    print("Display source:", display.get("source"))
    print("Pipeline stats:", pipeline_stats)
    print("Validation verrous:", len(data.get("validation_verrous") or []))

    expected = [
        "Lecture Frascati du dossier",
        "Synthèse stratégique du projet",
        "Objectif global reformulé",
        "Développer et valider une architecture de compresseur TGM100",
        "aspiration acoustique déportée",
        "réfrigérant",
        "contrepoids sans plomb",
        "soufflage carter",
        "Démarche expérimentale détectée",
        "Résultats et métriques disponibles",
        "Paramètres et contraintes techniques",
        "Points à valider par le consultant",
    ]

    print("\n4) Vérification contenu type Streamlit")
    all_ok = True
    for text in expected:
        ok = text.lower() in report.lower()
        all_ok = all_ok and ok
        print(("✅" if ok else "❌"), text)

    print("\n5) Aperçu objectif")
    print("-" * 80)
    print(objective[:1500] if objective else "Aucun objectif dans display.objective")
    print("-" * 80)

    print("\n6) Aperçu rapport")
    print("-" * 80)
    print(report[:2500] if report else "Aucun report_markdown")
    print("-" * 80)

    if all_ok:
        print("\n🎉 OK : le backend seul renvoie bien la sortie type Streamlit.")
    else:
        print("\n⚠ PAS ENCORE OK : le backend ne renvoie pas encore exactement la sortie type Streamlit.")
        print("➡ Ouvre backend_diagnostic_latest.json et regarde display.report_markdown.")
        print("➡ Si report_markdown est vide/générique, problème backend/display.")
        print("➡ Si report_markdown est correct mais React faux, problème frontend.")


if __name__ == "__main__":
    main()
