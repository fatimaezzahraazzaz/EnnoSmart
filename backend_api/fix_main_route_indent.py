from pathlib import Path
from datetime import datetime
import shutil

p = Path("main.py")
backup = Path(f"main.py.bak_route_indent_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
shutil.copy2(p, backup)

lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
fixed = []

previous_was_app_decorator = False

for line in lines:
    stripped = line.lstrip()

    # Les décorateurs FastAPI doivent être au niveau 0
    if stripped.startswith("@app."):
        fixed.append(stripped)
        previous_was_app_decorator = True
        continue

    # La fonction juste après @app.get/@app.post doit aussi être au niveau 0
    if previous_was_app_decorator and (
        stripped.startswith("def ") or stripped.startswith("async def ")
    ):
        fixed.append(stripped)
        previous_was_app_decorator = False
        continue

    # Les include_router doivent aussi être au niveau 0
    if stripped.startswith("app.include_router("):
        fixed.append(stripped)
        previous_was_app_decorator = False
        continue

    # Les imports doivent être au niveau 0
    if stripped.startswith("from routers import "):
        fixed.append(stripped)
        previous_was_app_decorator = False
        continue

    fixed.append(line)
    if stripped and not stripped.startswith("#"):
        previous_was_app_decorator = False

p.write_text("\n".join(fixed) + "\n", encoding="utf-8")
print("Backup:", backup)
print("main.py corrigé.")
