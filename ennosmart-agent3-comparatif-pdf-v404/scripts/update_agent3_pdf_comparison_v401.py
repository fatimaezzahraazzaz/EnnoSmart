from __future__ import annotations

import shutil
from pathlib import Path
import py_compile

REPO = Path(r"C:\EnnoSmart")
PACK = Path(__file__).resolve().parents[1]

FILES = [
    (
        PACK / "backend_api" / "routers" / "source_highlight.py",
        REPO / "backend_api" / "routers" / "source_highlight.py",
    ),
    (
        PACK / "frontend" / "components" / "ennosmart" / "improvement-pdf-comparator.tsx",
        REPO / "frontend" / "components" / "ennosmart" / "improvement-pdf-comparator.tsx",
    ),
]

for source, target in FILES:
    if not source.exists():
        raise SystemExit(f"[ERREUR] Source pack introuvable: {source}")
    if target.exists():
        backup = target.with_name(target.name + ".before-agent3-v401")
        shutil.copy2(target, backup)
        print(f"[BACKUP] {backup}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"[OK] {target}")

py_compile.compile(
    str(REPO / "backend_api" / "routers" / "source_highlight.py"),
    doraise=True,
)
print("[PYTHON OK] source_highlight.py")
print("")
print("V4.01 INSTALLEE.")
print("Redemarre FastAPI puis actualise le frontend.")
print("Pas besoin de relancer la proposition Agent 3.")
