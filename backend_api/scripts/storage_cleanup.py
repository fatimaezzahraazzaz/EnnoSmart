# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
for path in (ROOT_DIR, BACKEND_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services.storage_cleanup_service import run_storage_cleanup


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Nettoyage sécurisé du storage EnnoSmart (dry-run par défaut)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Applique réellement les suppressions. Sans ce flag: dry-run uniquement.",
    )
    parser.add_argument(
        "--category",
        action="append",
        choices=["all", "staging", "previews", "cache", "ennoscholar_cache", "mcp_results", "terminal_tests"],
        help="Catégorie à nettoyer. Répétable. Par défaut: all.",
    )
    parser.add_argument(
        "--max-delete-gb",
        type=float,
        default=50.0,
        help="Garde-fou: volume maximal supprimable en mode --apply (défaut: 50 Go).",
    )
    parser.add_argument(
        "--show-files",
        action="store_true",
        help="Affiche chaque candidat en plus du résumé.",
    )
    args = parser.parse_args()

    result = run_storage_cleanup(
        apply=bool(args.apply),
        categories=args.category or ["all"],
        max_delete_gb=float(args.max_delete_gb),
    )

    eligible = [c for c in (result.get("candidates") or []) if c.get("eligible")]
    blocked = [c for c in (result.get("candidates") or []) if not c.get("eligible")]
    print("=" * 72)
    print(f"EnnoSmart storage cleanup - {result.get('mode')}")
    print(f"Storage : {result.get('storage_root')}")
    print(f"Éligibles : {len(eligible)} fichier(s) / {result.get('eligible_bytes', 0) / (1024**3):.2f} Go")
    print(f"Protégés/ignorés : {len(blocked)} fichier(s)")
    print(f"Supprimés : {result.get('deleted_files', 0)} fichier(s) / {result.get('deleted_bytes', 0) / (1024**3):.2f} Go")
    if result.get("errors"):
        print("Erreurs/garde-fous:")
        for error in result["errors"]:
            print(f"  - {error}")
    if args.show_files:
        print("\nCandidats:")
        for item in result.get("candidates") or []:
            size_mb = float(item.get("bytes") or 0) / (1024**2)
            print(f"  [{item.get('action')}] {item.get('category')} {size_mb:.2f} Mo - {item.get('path')}")
            if item.get("detail"):
                print(f"      {item.get('detail')}")
    print("=" * 72)

    return 1 if result.get("errors") and args.apply else 0


if __name__ == "__main__":
    raise SystemExit(main())
