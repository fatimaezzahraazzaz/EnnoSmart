from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.database import SessionLocal
from db.models import Project
from services.scholar_pdf_direct_extractor import extract_direct_fulltext_for_article


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Teste la résolution PDF + extraction native/OCR EnnoScholar."
    )
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--article-id", type=int, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == args.project_id).first()
        if not project:
            raise RuntimeError(f"Projet {args.project_id} introuvable")

        result = extract_direct_fulltext_for_article(
            db=db,
            project=project,
            article_id=args.article_id,
            force=args.force,
        )

        summary = {
            "ok": result.get("ok"),
            "status": result.get("status"),
            "full_text_status": result.get("full_text_status"),
            "extraction_method": result.get("extraction_method"),
            "pages_count": result.get("pages_count"),
            "pages_with_text": result.get("pages_with_text"),
            "text_chars": result.get("text_chars"),
            "text_words": result.get("text_words"),
            "ocr_attempted": result.get("ocr_attempted"),
            "ocr_engine": result.get("ocr_engine"),
            "ocr_confidence": result.get("ocr_confidence"),
            "ocr_pages_processed": result.get("ocr_pages_processed"),
            "ocr_errors": result.get("ocr_errors"),
            "temporary_pdf_deleted": result.get("temporary_pdf_deleted"),
            "retrieved_via_mcp": result.get("retrieved_via_mcp"),
            "legal_provider": result.get("legal_provider"),
            "output_path": result.get("output_path"),
            "needs_consultant_upload": result.get("needs_consultant_upload"),
        }

        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

        return 0 if bool(result.get("ok")) and int(result.get("text_chars") or 0) >= 1000 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
