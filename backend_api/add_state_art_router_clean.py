from pathlib import Path
from datetime import datetime
import shutil

p = Path("main.py")
backup = Path(f"main.py.bak_add_state_art_route_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
shutil.copy2(p, backup)

text = p.read_text(encoding="utf-8", errors="replace")

if "scholar_state_of_art_direct" not in text:
    lines = text.splitlines()

    # ajouter import après les autres imports routers si possible
    inserted_import = False
    for i, line in enumerate(lines):
        if line.startswith("from routers import "):
            lines[i] = line.rstrip() + ", scholar_state_of_art_direct"
            inserted_import = True
            break

    if not inserted_import:
        # après les imports standards
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                insert_at = i + 1
        lines.insert(insert_at, "from routers import scholar_state_of_art_direct")

    text = "\n".join(lines) + "\n"

if "scholar_state_of_art_direct.router" not in text:
    lines = text.splitlines()

    inserted = False

    # Cas app factory : ajouter juste avant return app avec la même indentation
    for i, line in enumerate(lines):
        if line.strip() == "return app":
            indent = line[:len(line) - len(line.lstrip())]
            lines.insert(i, indent + "app.include_router(scholar_state_of_art_direct.router)")
            inserted = True
            break

    # Cas app global : ajouter après les autres include_router
    if not inserted:
        last = -1
        for i, line in enumerate(lines):
            if "app.include_router(" in line:
                last = i
        if last >= 0:
            lines.insert(last + 1, "app.include_router(scholar_state_of_art_direct.router)")
            inserted = True

    if not inserted:
        lines.append("app.include_router(scholar_state_of_art_direct.router)")

    text = "\n".join(lines) + "\n"

p.write_text(text, encoding="utf-8")
print("Backup:", backup)
print("main.py corrigé proprement.")
