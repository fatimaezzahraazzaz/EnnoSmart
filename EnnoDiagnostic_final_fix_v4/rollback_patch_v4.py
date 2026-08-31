from __future__ import annotations
import argparse, shutil
from pathlib import Path

FILES = [
    Path("agents/EnnoDiagnostic/consultant_verrou_synthesizer.py"),
    Path("agents/EnnoDiagnostic/scientific_axis_synthesizer.py"),
    Path("agents/EnnoDiagnostic/diagnostic_static_presenter.py"),
    Path("agents/EnnoDiagnostic/ennodiagnostic_agent.py"),
    Path("agents/EnnoDiagnostic/structured_eligibility_writer.py"),
    Path("modules/NLP/frascati_assessment.py"),
]

p=argparse.ArgumentParser()
p.add_argument("--repo", default=".")
p.add_argument("--backup", required=True, help="Chemin du backup affiché par apply_patch_v4.py")
a=p.parse_args()
root=Path(a.repo).resolve(); backup=Path(a.backup).resolve()
for rel in FILES:
    src=backup/rel; dst=root/rel
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src,dst)
        print("[RESTORE]", rel)
print("[OK] rollback terminé")
