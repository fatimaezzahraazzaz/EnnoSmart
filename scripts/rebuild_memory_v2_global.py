from __future__ import annotations

"""Construit la collection Memory V2 globale dans un dossier Chroma neuf."""

import argparse
import json
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env", override=False)
    load_dotenv(ROOT_DIR / "backend_api" / ".env", override=False)
except Exception:
    pass

from scripts import experience_memory_v2_engine as engine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chroma-dir", required=True)
    args = parser.parse_args()

    target = Path(args.chroma_dir).resolve()
    memory_root = engine.V2_ROOT.resolve()
    try:
        target.relative_to(memory_root)
    except ValueError as exc:
        raise ValueError("Le Chroma temporaire doit rester dans la racine Memory V2.") from exc
    if target.exists():
        raise FileExistsError(f"La destination Chroma existe déjà : {target}")

    engine.V2_CHROMA_DIR = target
    report = engine.rebuild_global_graph_and_catalog(reset_chroma=True)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
