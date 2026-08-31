# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


PATCH_FILES = (
    "agents/EnnoDiagnostic/historical_continuity_reconciler.py",
    "agents/EnnoDiagnostic/diagnostic_static_presenter.py",
    "agents/EnnoDiagnostic/ennodiagnostic_agent.py",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Installe EnnoDiagnostic Final Memory Fix V7.1."
    )
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    root = Path(__file__).resolve().parent
    if not repo.exists():
        raise SystemExit(f"[FAIL] dépôt introuvable: {repo}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = repo.parent / "EnnoSmart_patch_backups" / f"final_memory_v7_1_{stamp}"

    for rel in PATCH_FILES:
        src = root / rel
        dst = repo / rel
        if not src.exists():
            raise SystemExit(f"[FAIL] patch incomplet: {src}")
        if dst.exists():
            bak = backup / rel
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, bak)
            print(f"[BACKUP] {rel} -> {bak}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"[OK] installé: {rel}")

    print("")
    print("[OK] V7.1 installée.")
    print("[INFO] scientific_axis_synthesizer.py est fourni complet mais non remplacé : la V6 existante est conservée.")
    print("[INFO] Aucun fichier Frascati, score, RAG/chat, frontend ou NLP n'est modifié.")
    print(f"[INFO] Sauvegarde: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
