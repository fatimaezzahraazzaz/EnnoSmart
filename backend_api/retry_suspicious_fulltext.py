import json

from db.database import SessionLocal
from db.models import Project
from services.scholar_fulltext_fetcher import (
    fetch_fulltext_pdf_for_article,
)
from services.scholar_pdf_direct_extractor import (
    extract_direct_fulltext_for_article,
)


PROJECT_ID = 1

ARTICLE_IDS = [
    12612,  # UCAV FEKO
    12643,  # MDPI Hybrid FVTD
    12595,  # TUBITAK RCS
    12623,  # TUBITAK pRediCS
    12809,  # Semantic Scholar landing
]


db = SessionLocal()

try:
    project = (
        db.query(Project)
        .filter(Project.id == PROJECT_ID)
        .first()
    )

    if project is None:
        raise RuntimeError(
            f"Projet {PROJECT_ID} introuvable"
        )

    for article_id in ARTICLE_IDS:
        print("\n" + "=" * 100)
        print("ARTICLE_ID=", article_id)

        phase_2a = fetch_fulltext_pdf_for_article(
            db=db,
            project=project,
            article_id=article_id,
            force=True,
        )

        print("\nPHASE 2A")
        print(
            json.dumps(
                {
                    "ok": phase_2a.get("ok"),
                    "status": phase_2a.get("status"),
                    "full_text_status": phase_2a.get(
                        "full_text_status"
                    ),
                    "resolver": phase_2a.get("resolver"),
                    "pdf_source_url": phase_2a.get(
                        "pdf_source_url"
                    ),
                    "pdf_final_url": phase_2a.get(
                        "pdf_final_url"
                    ),
                    "retrieved_via_mcp": phase_2a.get(
                        "retrieved_via_mcp"
                    ),
                    "mcp_status": phase_2a.get(
                        "mcp_status"
                    ),
                    "mcp_cache_hit": phase_2a.get(
                        "mcp_cache_hit"
                    ),
                    "needs_consultant_upload": phase_2a.get(
                        "needs_consultant_upload"
                    ),
                    "attempts": phase_2a.get(
                        "attempts",
                        [],
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        phase_2b = extract_direct_fulltext_for_article(
            db=db,
            project=project,
            article_id=article_id,
            force=True,
        )

        print("\nPHASE 2B")
        print(
            json.dumps(
                {
                    "ok": phase_2b.get("ok"),
                    "status": phase_2b.get("status"),
                    "full_text_status": phase_2b.get(
                        "full_text_status"
                    ),
                    "pdf_source_url": phase_2b.get(
                        "pdf_source_url"
                    ),
                    "pdf_final_url": phase_2b.get(
                        "pdf_final_url"
                    ),
                    "retrieved_via_mcp": phase_2b.get(
                        "retrieved_via_mcp"
                    ),
                    "legal_provider": phase_2b.get(
                        "legal_provider"
                    ),
                    "pages_count": phase_2b.get(
                        "pages_count"
                    ),
                    "text_chars": phase_2b.get(
                        "text_chars"
                    ),
                    "ocr_engine": phase_2b.get(
                        "ocr_engine"
                    ),
                    "needs_consultant_upload": phase_2b.get(
                        "needs_consultant_upload"
                    ),
                    "attempts": phase_2b.get(
                        "attempts",
                        [],
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

finally:
    db.close()
