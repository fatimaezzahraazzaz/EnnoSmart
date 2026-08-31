# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

FILES = (
    Path("modules/RAG/diagnostic_chat_service.py"),
    Path("modules/RAG/retriever.py"),
    Path("modules/RAG/vector_store.py"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Installe le correctif Chat RAG provenance V2.")
    parser.add_argument("--repo", required=True, help="Racine du repo EnnoSmart")
    args = parser.parse_args()

    package_root = Path(__file__).resolve().parent
    repo = Path(args.repo).resolve()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = repo / f"_backup_chat_rag_provenance_v2_{stamp}"

    for relative in FILES:
        src = package_root / relative
        dst = repo / relative
        if not src.exists():
            raise FileNotFoundError(src)
        if dst.exists():
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"[OK] {relative}")

    print(f"Backup : {backup_root}")
    print("Correctif installé. Redémarrez uniquement le backend puis retestez le chat.")
    print("Pas besoin de relancer EnnoDiagnostic. L'index brut compagnon sera reconstruit automatiquement au premier message.")


if __name__ == "__main__":
    main()
