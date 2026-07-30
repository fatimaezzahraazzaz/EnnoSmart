from __future__ import annotations

import gc
import sys
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.database import SessionLocal
from db.models import Article, Project
from modules.extraction.text.pdf_ocr import extract_pdf_ocr
from services.scholar_pdf_direct_extractor import _fetch_pdf_bytes_from_url


ARTICLE_ID = 12592
PROJECT_ID = 1


def delete_temporary_file(
    path: Path | None,
    retries: int = 15,
    delay_seconds: float = 0.4,
) -> bool:
    if path is None:
        return True

    for attempt in range(1, retries + 1):
        try:
            gc.collect()

            if not path.exists():
                return True

            path.unlink()
            return True

        except PermissionError:
            if attempt == retries:
                return False
            time.sleep(delay_seconds)

        except FileNotFoundError:
            return True

    return False


def main() -> None:
    db = SessionLocal()
    temporary_pdf: Path | None = None

    try:
        article = (
            db.query(Article)
            .filter(Article.id == ARTICLE_ID)
            .first()
        )
        project = (
            db.query(Project)
            .filter(Project.id == PROJECT_ID)
            .first()
        )

        if article is None:
            raise RuntimeError(
                f"Article {ARTICLE_ID} introuvable"
            )

        if project is None:
            raise RuntimeError(
                f"Projet {PROJECT_ID} introuvable"
            )

        source_json = article.source_json or {}

        pdf_url = (
            source_json.get("pdf_url")
            or source_json.get("primary_pdf_url")
        )

        if not pdf_url:
            raise RuntimeError(
                "Aucune URL PDF dans source_json"
            )

        print("PDF_URL=", pdf_url)

        ok, download_info, pdf_bytes = _fetch_pdf_bytes_from_url(
            url=pdf_url,
            project=project,
            article=article,
        )

        print("DOWNLOAD_OK=", ok)
        print(
            "DOWNLOAD_STATUS=",
            download_info.get("status"),
        )
        print("PDF_BYTES=", len(pdf_bytes or b""))

        if not ok or not pdf_bytes:
            raise RuntimeError(download_info)

        with NamedTemporaryFile(
            suffix=".pdf",
            delete=False,
            prefix=f"ennoscholar_article_{ARTICLE_ID}_",
        ) as temporary_file:
            temporary_file.write(pdf_bytes)
            temporary_pdf = Path(temporary_file.name)

        print("TEMP_PDF=", temporary_pdf)

        ocr_result = extract_pdf_ocr(temporary_pdf)

        full_text = "\n\n".join(
            page.raw_text.strip()
            for page in ocr_result.pages
            if page.raw_text.strip()
        )

        print("OCR_ENGINE=", ocr_result.engine_used.value)
        print("OCR_PAGE_COUNT=", ocr_result.page_count)
        print(
            "OCR_PROCESSED=",
            ocr_result.pages_processed,
        )
        print(
            "OCR_CONFIDENCE=",
            ocr_result.confidence_score,
        )
        print("OCR_CHARS=", len(full_text))
        print("OCR_WORDS=", len(full_text.split()))
        print("OCR_TAGS=", ocr_result.tags)
        print(
            "OCR_ERRORS=",
            ocr_result.extraction_errors,
        )

        print("\n--- PREVIEW ---")
        print(full_text[:2500])

    finally:
        db.close()
        gc.collect()

        deleted = delete_temporary_file(
            temporary_pdf,
        )

        print(
            "\nTEMP_PDF_DELETED=",
            deleted,
        )

        if not deleted and temporary_pdf:
            print(
                "TEMP_PDF_STILL_LOCKED=",
                temporary_pdf,
            )


if __name__ == "__main__":
    main()
