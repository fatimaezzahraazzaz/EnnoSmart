# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


PATCH_FILES = (
    "agents/EnnoDiagnostic/historical_continuity_reconciler.py",
    "agents/EnnoDiagnostic/scientific_axis_synthesizer.py",
    "agents/EnnoDiagnostic/diagnostic_static_presenter.py",
    "agents/EnnoDiagnostic/ennodiagnostic_agent.py",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Installe le correctif EnnoDiagnostic Active Memory V7.0."
    )
    parser.add_argument("--repo", required=True, help="Racine du dépôt EnnoSmart")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    patch_root = Path(__file__).resolve().parent

    if not repo.exists():
        raise SystemExit(f"[FAIL] dépôt introuvable: {repo}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = repo.parent / "EnnoSmart_patch_backups" / f"active_memory_v7_0_{stamp}"

    print("[INFO] Correctif additif : 4 fichiers seulement seront remplacés.")
    for rel in PATCH_FILES:
        src = patch_root / rel
        dst = repo / rel

        if not src.exists():
            raise SystemExit(f"[FAIL] fichier du patch introuvable: {src}")

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
    print("[INFO] modules/NLP/evidence_graph.py est fourni pour référence mais n'est PAS remplacé.")
    print("[INFO] Aucun fichier Frascati, RAG/chat, score ou frontend n'est modifié.")
    print(f"[INFO] Sauvegardes: {backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
