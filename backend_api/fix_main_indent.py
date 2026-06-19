from pathlib import Path
from datetime import datetime
import shutil

p = Path("main.py")
backup = Path(f"main.py.bak_indent_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
shutil.copy2(p, backup)

lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
fixed = []

for line in lines:
    stripped = line.lstrip()

    if stripped.startswith("@app."):
        fixed.append(stripped)
    elif stripped.startswith("app.include_router("):
        fixed.append(stripped)
    elif stripped.startswith("from routers import "):
        fixed.append(stripped)
    elif stripped.startswith("from fastapi"):
        fixed.append(stripped)
    else:
        fixed.append(line)

p.write_text("\n".join(fixed) + "\n", encoding="utf-8")
print("Backup:", backup)
print("main.py indentation corrigée.")
