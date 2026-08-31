# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


PATCH_FILES = (
    "agents/EnnoDiagnostic/scientific_axis_synthesizer.py",
    "agents/EnnoDiagnostic/ennodiagnostic_agent.py",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="Racine du dépôt EnnoSmart")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    patch_root = Path(__file__).resolve().parent

    if not repo.exists():
        raise SystemExit(f"[FAIL] dépôt introuvable: {repo}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = repo.parent / "EnnoSmart_patch_backups" / f"transversal_lock_v6_0_{stamp}"

    for rel in PATCH_FILES:
        src = patch_root / rel
        dst = repo / rel
        if not src.exists():
            raise SystemExit(f"[FAIL] fichier patch introuvable: {src}")
        if dst.exists():
            backup = backup_root / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup)
            print(f"[BACKUP] {dst} -> {backup}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"[OK] installé: {rel}")

    print("")
    print("[OK] Installation terminée.")
    print("[INFO] consultant_verrou_synthesizer.py et semantic_lock_finalizer.py sont fournis")
    print("       dans le package pour référence, mais ne sont PAS remplacés par l'installateur.")
    print(f"[INFO] Sauvegardes: {backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
