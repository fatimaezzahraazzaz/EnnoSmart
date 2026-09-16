"""Contrôle de construction : dépendances, code courant et FastJudge, sans réseau."""
from __future__ import annotations

import argparse
import importlib
from importlib.metadata import version
from pathlib import Path
import shutil
import sys
import warnings

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]

MODULES = (
    "openai", "psycopg", "psycopg2", "celery", "redis", "chromadb",
    "sentence_transformers", "transformers", "mcp", "pydantic_ai",
    "modules.NLP.frascati_guard",
    "modules.NLP.llm_parent_lock_consolidator",
    "modules.NLP.history_parent_support",
    "agents.EnnoDiagnostic.final_parent_consolidation",
    "agents.EnnoDiagnostic.historical_continuity_cache",
    "agents.EnnoDiagnostic.visible_lock_title_guard",
    "agents.EnnoDiagnostic.ennodiagnostic_agent",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-system-tools", action="store_true", help="Pour vérifier les imports hors Docker")
    args = parser.parse_args()
    for name in MODULES:
        importlib.import_module(name)
        print(f"IMPORT_OK {name}", flush=True)
    import joblib
    from sklearn.exceptions import InconsistentVersionWarning
    model = ROOT / "models/fastjudge/fastjudge_linearsvc_C025.joblib"
    with warnings.catch_warnings():
        warnings.simplefilter("error", InconsistentVersionWarning)
        loaded = joblib.load(model)
        estimator = loaded["model"] if isinstance(loaded, dict) else loaded
        estimator.predict(["Cette étude compare des protocoles pour comprendre une incertitude technique."])
    print(f"FASTJUDGE_OK sklearn={version('scikit-learn')}")
    if not args.skip_system_tools:
        for name in ("tesseract", "pdftotext", "pdftoppm", "libreoffice", "ffmpeg", "java"):
            if not shutil.which(name):
                raise RuntimeError(f"Outil système manquant : {name}")
        import torch
        if torch.version.cuda is not None:
            raise RuntimeError("L'image OVH CPU a installé une distribution CUDA de PyTorch")
    print("BACKEND_IMAGE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
