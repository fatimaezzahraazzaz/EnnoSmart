"""Immutable PDF bytes and paginated extraction, isolated from older versions."""
import hashlib
import os
import re
from pathlib import Path


def directory():
    root = Path(os.getenv("ENNOSCHOLAR_V4_PDF_DIRECTORY", str(Path(__file__).resolve().parents[2]/"backend_api"/"storage"/"scholar_v4"/"pdf")))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def save_pdf(data):
    from .config import ScholarV4Config
    if not data.startswith(b"%PDF") or len(data) > ScholarV4Config.from_env().max_pdf_bytes:
        raise ValueError("PDF requis, taille maximale 15 Mo.")
    filename = hashlib.sha256(data).hexdigest()+".pdf"
    path = directory()/filename
    # Identical content is immutable and can be shared; authorization remains session-scoped.
    if not path.exists():
        try:
            with path.open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            pass  # Another worker already stored the same immutable bytes.
    return filename


def pdf_path(filename):
    if not re.fullmatch(r"[a-f0-9]{64}\.pdf", filename):
        raise ValueError("Identifiant PDF invalide.")
    return directory()/filename


def extract_pages(filename, page_start, page_limit):
    import fitz
    from dataclasses import replace
    from .config import ScholarV4Config
    from .tools.extraction import pdf_chunks
    data = pdf_path(filename).read_bytes()
    with fitz.open(stream=data, filetype="pdf") as document:
        count = len(document)
    chunks, _ = pdf_chunks(data, replace(ScholarV4Config.from_env(), max_extracted_chars=100000), page_start, page_limit)
    return [c.model_dump() for c in chunks], count
