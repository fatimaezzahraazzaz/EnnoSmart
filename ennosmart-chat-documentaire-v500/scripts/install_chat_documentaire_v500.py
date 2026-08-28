from __future__ import annotations

import shutil
from pathlib import Path

REPO = Path(r"C:\EnnoSmart")
PACK = Path(__file__).resolve().parents[1]

SOURCE = (
    PACK
    / "frontend"
    / "components"
    / "ennosmart"
    / "diagnostic-rag-chat.tsx"
)
TARGET = (
    REPO
    / "frontend"
    / "components"
    / "ennosmart"
    / "diagnostic-rag-chat.tsx"
)

print("=== EnnoSmart - Chat documentaire V5.00 ===")

if not SOURCE.exists():
    raise SystemExit(f"[ERREUR] Fichier pack introuvable : {SOURCE}")

if not TARGET.exists():
    raise SystemExit(f"[ERREUR] Composant local introuvable : {TARGET}")

backup = TARGET.with_name(TARGET.name + ".before-chat-v500")
shutil.copy2(TARGET, backup)
print(f"[BACKUP] {backup}")

shutil.copy2(SOURCE, TARGET)
print(f"[OK] {TARGET}")

content = TARGET.read_text(encoding="utf-8")

checks = {
    "Passages au-dessus": "Passages et preuves" in content,
    "Document viewer": "source-highlight/preview" in content,
    "Chat droite": "Assistant EnnoDiagnostic" in content,
    "Scope documents": "Tous les documents du projet" in content,
    "Plein ecran": "setExpanded" in content,
}

for label, ok in checks.items():
    print(f"[{'OK' if ok else 'ERREUR'}] {label}")
    if not ok:
        raise SystemExit(2)

print("")
print("CHAT DOCUMENTAIRE V5.00 INSTALLE.")
print("Aucun changement backend.")
print("Actualise le frontend.")
