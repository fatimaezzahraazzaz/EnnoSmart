# verify_scholar_service_v31.py
# Vérifie que le fichier scholar_service.py copié dans backend_api est bien la version V31.

from pathlib import Path
import sys

p = Path(__file__).resolve().parent / "services" / "scholar_service.py"
txt = p.read_text(encoding="utf-8", errors="ignore")

checks = {
    "_build_enriched_scientific_profile": "_build_enriched_scientific_profile" in txt,
    "suggested_queries": "suggested_queries" in txt,
    "original_title": "original_title" in txt,
    "agents.EnnoScholar": "agents.EnnoScholar" in txt,
    "blow-by query": "reciprocating compressor piston rings blow-by leakage crankcase pressure" in txt,
    "thermal query": "high pressure reciprocating compressor intercooler water cooling temperature" in txt,
}

print("Fichier vérifié :", p)
for k, v in checks.items():
    print(("✅" if v else "❌"), k)

if not all(checks.values()):
    print("\n❌ scholar_service.py n'est pas la version V31 enrichie.")
    print("Recopie backend_api/services/scholar_service.py depuis le patch V31.1 puis redémarre uvicorn.")
    sys.exit(1)

print("\n✅ scholar_service.py est bien la version V31 enrichie.")
