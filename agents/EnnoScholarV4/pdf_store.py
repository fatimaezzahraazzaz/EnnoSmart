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
    chunks, used = [], 0
    with fitz.open(pdf_path(filename)) as document:
        if document.needs_pass:
            raise ValueError("PDF protégé par mot de passe.")
        if not 1 <= page_start <= len(document):
            raise ValueError("Page PDF hors du document.")
        for index in range(page_start-1, min(len(document), page_start-1+page_limit)):
            text = document[index].get_text()[:100000-used]
            from agents.EnnoScholarV2.tools.article_reader import SECTION
            section = next((line.strip() for line in text.splitlines() if SECTION.match(line.strip())), "")
            if text.strip():
                chunks.append({"page": index+1, "section": section, "text": text.strip()})
            used += len(text)
            if used >= 100000:
                break
        return chunks, len(document)
