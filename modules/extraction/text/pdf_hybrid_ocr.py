# -*- coding: utf-8 -*-
"""OCR hybride PDF pour EnnoSmart.

Principe :
1. extraction native de chaque page ;
2. OCR des pages pauvres OU suspectes ;
3. fusion native + OCR sans perdre le texte natif ;
4. rapport JSON page par page pour contrôle.

À copier dans : modules/extraction/text/pdf_hybrid_ocr.py
"""

from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from modules.extraction.text.pdf_native import extract_pdf_native
from modules.extraction.text.pdf_ocr import extract_pdf_ocr


# Une page qui contient quelques mots natifs peut quand même être un scan.
MIN_NATIVE_CHARS = 180
MAX_CHARS_FOR_IMAGE_PAGE = 450
MIN_ALPHA_RATIO = 0.45


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9àâçéèêëîïôùûüÿœæ ]+", " ", (text or "").lower())


def _looks_suspicious(text: str, has_images: bool) -> bool:
    value = _compact(text)
    if len(value) < MIN_NATIVE_CHARS:
        return True
    if has_images and len(value) < MAX_CHARS_FOR_IMAGE_PAGE:
        return True

    letters = sum(ch.isalpha() for ch in value)
    alnum = sum(ch.isalnum() for ch in value)
    if alnum and letters / alnum < MIN_ALPHA_RATIO:
        return True
    if "�" in value or value.count("\\") > 8:
        return True
    return False


def select_ocr_pages(native: Any) -> list[int]:
    """Retourne les pages nécessitant un OCR complémentaire."""
    selected: set[int] = set(getattr(native, "ocr_needed_pages", []) or [])
    for page in getattr(native, "pages", []) or []:
        page_no = int(getattr(page, "page_number"))
        text = getattr(page, "raw_text", "") or ""
        has_images = bool(getattr(page, "has_images", False))
        if _looks_suspicious(text, has_images):
            selected.add(page_no)
    return sorted(selected)


def _merge_text(native_text: str, ocr_text: str) -> str:
    """Fusionne les deux sources en privilégiant le rappel des phrases."""
    native_text = native_text or ""
    ocr_text = ocr_text or ""

    if not native_text.strip() or "[ERREUR EXTRACTION]" in native_text:
        return ocr_text.strip()
    if not ocr_text.strip() or "[ERREUR OCR]" in ocr_text:
        return native_text.strip()

    n = _normalized(native_text)
    o = _normalized(ocr_text)
    if not n or not o:
        return native_text.strip() or ocr_text.strip()

    # Les deux résultats sont presque identiques : éviter les doublons.
    similarity = SequenceMatcher(None, n, o).ratio()
    if similarity >= 0.78:
        return (ocr_text if len(ocr_text) >= len(native_text) else native_text).strip()

    # Un résultat contient déjà l'autre.
    if n in o:
        return ocr_text.strip()
    if o in n:
        return native_text.strip()

    # Les deux sources sont complémentaires : on conserve les deux.
    return (
        native_text.strip()
        + "\n\n[OCR_COMPLEMENTAIRE]\n"
        + ocr_text.strip()
    )


def extract_pdf_hybrid(path: str | Path) -> tuple[list[str], dict[str, Any]]:
    """Extrait un PDF et retourne (chunks, audit)."""
    pdf_path = Path(path)
    native = extract_pdf_native(str(pdf_path))
    target_pages = select_ocr_pages(native)

    ocr = None
    if target_pages:
        ocr = extract_pdf_ocr(str(pdf_path), target_pages=target_pages)

    ocr_by_page: dict[int, str] = {}
    ocr_confidence: dict[int, float] = {}
    if ocr is not None:
        for page, chunk in zip(ocr.pages, ocr.text_chunks):
            page_no = int(page.page_number)
            ocr_by_page[page_no] = chunk
            ocr_confidence[page_no] = float(getattr(page, "confidence", 0.0))

    chunks: list[str] = []
    page_audit: list[dict[str, Any]] = []
    native_pages = {int(p.page_number): p for p in getattr(native, "pages", [])}

    for index, native_chunk in enumerate(native.text_chunks, start=1):
        page_no = int(native_pages.get(index, None).page_number) if index in native_pages else index
        final_chunk = _merge_text(native_chunk, ocr_by_page.get(page_no, ""))
        chunks.append(final_chunk)
        page = native_pages.get(page_no)
        page_audit.append(
            {
                "page": page_no,
                "native_chars": len(getattr(page, "raw_text", "") or "") if page else 0,
                "has_images": bool(getattr(page, "has_images", False)) if page else False,
                "ocr_requested": page_no in target_pages,
                "ocr_chars": len(ocr_by_page.get(page_no, "")),
                "ocr_confidence": ocr_confidence.get(page_no),
                "final_chars": len(final_chunk),
            }
        )

    audit = {
        "file": str(pdf_path),
        "pages": len(chunks),
        "ocr_pages": target_pages,
        "ocr_pages_count": len(target_pages),
        "native_confidence": getattr(native, "confidence_score", None),
        "ocr_confidence": getattr(ocr, "confidence_score", None) if ocr else None,
        "errors": list(getattr(native, "extraction_errors", []) or [])
        + (list(getattr(ocr, "extraction_errors", []) or []) if ocr else []),
        "page_audit": page_audit,
        "text": "\n\n".join(chunks),
    }
    return chunks, audit


def _find_inputs(input_path: Path, patterns: list[str]) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    found: list[Path] = []
    for pattern in patterns:
        found.extend(input_path.rglob(pattern))
    return sorted(set(found))


def main() -> int:
    parser = argparse.ArgumentParser(description="Test OCR hybride EnnoSmart")
    parser.add_argument("--input", required=True, help="PDF ou dossier raw")
    parser.add_argument(
        "--pattern",
        action="append",
        default=[],
        help="Motif, répétable. Exemple: *Eclateur*.pdf",
    )
    parser.add_argument("--output", default="ocr_hybrid_audit.json")
    args = parser.parse_args()

    inputs = _find_inputs(Path(args.input), args.pattern or ["*.pdf"])
    if not inputs:
        print("Aucun PDF trouvé")
        return 2

    reports = []
    for pdf in inputs:
        print(f"OCR hybride : {pdf.name}")
        _, report = extract_pdf_hybrid(pdf)
        reports.append(report)
        print(f"  pages OCR : {report['ocr_pages']}")
        print(f"  caractères finaux : {len(report['text'])}")

    Path(args.output).write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Rapport écrit : {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())