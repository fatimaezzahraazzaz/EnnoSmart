from __future__ import annotations

import time
from agents.EnnoScholarV2.models import Article as LegacyArticle
from agents.EnnoScholarV2.tools.article_reader import pdf_chunks, html_document, matches_article
from .network import fetch_public
from .pdf_store import save_pdf


def get_fulltext(article, sources, deadline, check=lambda: None, candidate_urls=None):
    deadline = min(deadline, time.monotonic() + 45)
    attempts = []
    urls = [article.pdf_url]
    if article.arxiv_id:
        urls.append("https://arxiv.org/pdf/" + article.arxiv_id)
        urls.append("https://arxiv.org/html/" + article.arxiv_id)
    urls.extend([*(candidate_urls or []), *article.work_urls, article.url])
    resolved = False
    seen = set()
    while True:
        for url in urls:
            check()
            if not url or url in seen or len(seen) >= 8 or time.monotonic() >= deadline:
                continue
            seen.add(url)
            try:
                data, content_type, final_url = fetch_public(url, min(deadline, time.monotonic() + 18), check)
                html_title = ""
                if data.startswith(b"%PDF"):
                    chunks, truncated = pdf_chunks(data)
                elif "html" in content_type:
                    html = data[:200000].decode("utf-8", errors="ignore").lower()
                    if any(token in html for token in ("cf-chl-", "verify you are human", "captcha", "just a moment...")):
                        raise ValueError("AccessRestricted")
                    row, chunks, truncated = html_document(data, final_url)
                    html_title = row.title
                else:
                    raise ValueError("UnsupportedFormat")
                text = "\n\n".join(chunk.text for chunk in chunks)
                # Identity comes from the title/front matter, not a matching bibliography entry.
                identity_text = html_title + "\n" + (chunks[0].text[:7000] if chunks else "")
                if len(text) < 500 or not matches_article(LegacyArticle(title=article.title), identity_text):
                    raise ValueError("IdentityOrTextUnavailable")
                filename = save_pdf(data) if data.startswith(b"%PDF") else ""
                return {"access_level": "fulltext", "text": text, "url": final_url, "pdf_file": filename,
                    "chunks": [chunk.model_dump() for chunk in chunks], "truncated": truncated,
                    "attempts": attempts, "reason": "Passages sélectionnés du texte accessible." if truncated else "Texte accessible extrait."}
            except Exception as exc:
                check()  # Never swallow cancellation or the turn deadline.
                attempts.append({"url": url, "error": type(exc).__name__,
                    "reason": str(exc)[:160] if isinstance(exc, ValueError) else ""})
        if resolved or not article.doi or time.monotonic() >= deadline or len(seen) >= 8:
            break
        resolved = True
        try:
            rows = sources.metadata(article.doi, min(deadline, time.monotonic() + 12), check)
            urls = [url for row in rows for url in (row.pdf_url, *row.work_urls, row.url)]
        except Exception as exc:
            check()
            attempts.append({"stage": "oa_resolution", "error": type(exc).__name__})
            urls = []
    return {"access_level": "abstract_only" if article.abstract else "metadata_only", "text": "",
        "chunks": [], "truncated": False, "attempts": attempts,
        "reason": "Aucun texte intégral lisible récupéré ; abstract disponible." if article.abstract else "Métadonnées uniquement."}
