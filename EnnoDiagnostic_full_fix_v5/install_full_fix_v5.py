# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, shutil, sys
from datetime import datetime
from pathlib import Path
import py_compile

FILES = [
    "agents/EnnoDiagnostic/ennodiagnostic_agent.py",
    "agents/EnnoDiagnostic/consultant_verrou_synthesizer.py",
    "agents/EnnoDiagnostic/evidence_provenance.py",
    "agents/EnnoDiagnostic/scientific_axis_synthesizer.py",
    "agents/EnnoDiagnostic/diagnostic_static_presenter.py",
    "agents/EnnoDiagnostic/structured_eligibility_writer.py",
    "modules/NLP/demarche_legibility.py",
    "modules/NLP/frascati_assessment.py",
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    here = Path(__file__).resolve().parent
    payload = here / "files"
    backup = repo / ".ennosmart_patch_backups" / ("full_fix_v5_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    backup.mkdir(parents=True, exist_ok=False)

    missing = [rel for rel in FILES if not (payload / rel).exists()]
    if missing:
        raise RuntimeError("Fichiers payload manquants: " + ", ".join(missing))

    copied = []
    try:
        for rel in FILES:
            dst = repo / rel
            src = payload / rel
            if not dst.exists():
                raise RuntimeError(f"Fichier local attendu absent: {dst}")
            b = backup / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, b)
        print(f"[OK] Backup créé : {backup}")

        for rel in FILES:
            dst = repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(payload / rel, dst)
            copied.append(dst)
            print(f"[OK] Remplacé : {rel}")

        for path in copied:
            py_compile.compile(str(path), doraise=True)
        print("[OK] Compilation Python réussie.")
        print("[OK] Full Fix V5 installé.")
        print(f"[INFO] Rollback : recopier les fichiers depuis {backup}")
    except Exception:
        for rel in FILES:
            b = backup / rel
            dst = repo / rel
            if b.exists():
                shutil.copy2(b, dst)
        print("[ROLLBACK] Les fichiers d'origine ont été restaurés.", file=sys.stderr)
        raise

if __name__ == "__main__":
    main()
