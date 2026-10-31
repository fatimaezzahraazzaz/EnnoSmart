"""V5 documents have an isolated content-addressed directory, unchanged quotas."""
import os
from pathlib import Path
from .reuse import load
base = load("_pdf_store_base", "pdf_store.py")

def directory():
    root = Path(os.getenv("ENNOSCHOLAR_V5_PDF_DIRECTORY", str(Path(__file__).resolve().parents[2] / "backend_api/storage/scholar_v5/pdf")))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()

base.directory = directory
for name in dir(base):
    if not name.startswith("_") and name != "directory":
        globals()[name] = getattr(base, name)

_original_pdf_path = pdf_path

def pdf_path(filename):
    try:
        return _original_pdf_path(filename)
    except FileNotFoundError:
        # Only a filename from the authorised document record reaches here.
        # Hash verification and filename validation remain the original V4 code.
        from agents.EnnoScholarV4 import pdf_store as existing
        root = Path(os.getenv("ENNOSCHOLAR_V4_PDF_DIRECTORY", str(Path(existing.__file__).resolve().parents[2] / "backend_api/storage/scholar_v4/pdf")))
        path = root / filename
        if file_digest(path) != filename[:-4]:
            raise ValueError("ExistingPDFIntegrityMismatch")
        return path

base.pdf_path = pdf_path
