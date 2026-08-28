from __future__ import annotations

import shutil
import py_compile
from pathlib import Path

REPO = Path(r"C:\EnnoSmart")
PACK = Path(__file__).resolve().parents[1]

FILES = [
    (
        PACK / "backend_api" / "services" / "improvement_comparison_service.py",
        REPO / "backend_api" / "services" / "improvement_comparison_service.py",
    ),
    (
        PACK / "frontend" / "components" / "ennosmart" / "improvement-pdf-comparator.tsx",
        REPO / "frontend" / "components" / "ennosmart" / "improvement-pdf-comparator.tsx",
    ),
]

print("=== EnnoSmart Agent 3 - Comparatif V4.02 ===")

for source, target in FILES:
    if not source.exists():
        raise SystemExit(f"[ERREUR] Source pack introuvable : {source}")

    if target.exists():
        backup = target.with_name(target.name + ".before-agent3-v402")
        shutil.copy2(target, backup)
        print(f"[BACKUP] {backup}")

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"[OK] {target}")

py_compile.compile(
    str(REPO / "backend_api" / "services" / "improvement_comparison_service.py"),
    doraise=True,
)

print("[PYTHON OK] improvement_comparison_service.py")
print("")
print("V4.02 INSTALLEE.")
print("source_document_id est maintenant la source unique du comparatif.")
print("PDF : aucune conversion Office.")
print("Word Windows : Microsoft Word prioritaire, LibreOffice en fallback.")
print("Redemarre FastAPI puis actualise le frontend.")
