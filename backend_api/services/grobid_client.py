# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

try:
    import httpx
except Exception:
    httpx = None


class GrobidClient:
    """Optional local GROBID fallback for scientific PDFs."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.getenv("GROBID_URL", "http://127.0.0.1:8070")).rstrip("/")
        self.timeout = float(os.getenv("GROBID_TIMEOUT", "45"))
        self.enabled = (
            str(os.getenv("ENNOSCHOLAR_GROBID_ENABLED", "1")).lower()
            in {"1", "true", "yes", "on"} and httpx is not None
        )

    def alive(self) -> bool:
        if not self.enabled:
            return False
        try:
            with httpx.Client(timeout=2.0) as client:
                r = client.get(f"{self.base_url}/api/isalive")
            return r.status_code == 200 and "true" in r.text.lower()
        except Exception:
            return False

    def process_pdf(self, content: bytes) -> Dict[str, Any]:
        if not self.enabled or not content.startswith(b"%PDF-"):
            return {"ok": False, "status": "grobid_disabled_or_not_pdf"}
        try:
            files = {"input": ("article.pdf", content, "application/pdf")}
            data = {"consolidateHeader": "0", "consolidateCitations": "0", "includeRawCitations": "1"}
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(f"{self.base_url}/api/processFulltextDocument", files=files, data=data)
            if r.status_code != 200 or not r.content:
                return {"ok": False, "status": "grobid_http_error", "http_status": r.status_code}
            return self._tei_to_payload(r.content)
        except Exception as exc:
            return {"ok": False, "status": "grobid_error", "error": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _text(node: ET.Element | None) -> str:
        if node is None:
            return ""
        return re.sub(r"\s+", " ", " ".join(node.itertext())).strip()

    def _tei_to_payload(self, xml_bytes: bytes) -> Dict[str, Any]:
        try:
            root = ET.fromstring(xml_bytes)
        except Exception as exc:
            return {"ok": False, "status": "grobid_invalid_tei", "error": str(exc)}

        ns = {"tei": "http://www.tei-c.org/ns/1.0"}
        title = self._text(root.find(".//tei:titleStmt/tei:title", ns))
        abstract = self._text(root.find(".//tei:profileDesc//tei:abstract", ns))

        sections: List[Dict[str, Any]] = []
        for div in root.findall(".//tei:text/tei:body//tei:div", ns):
            heading = self._text(div.find("./tei:head", ns)) or "Section"
            paragraphs = [self._text(p) for p in div.findall("./tei:p", ns)]
            text = "\n".join(p for p in paragraphs if p)
            if text:
                sections.append({"heading": heading, "text": text})

        if not sections:
            body_text = self._text(root.find(".//tei:text/tei:body", ns))
            if body_text:
                sections = [{"heading": "Body", "text": body_text}]

        full_parts = (["Abstract\n" + abstract] if abstract else []) + [
            f"{s['heading']}\n{s['text']}" for s in sections
        ]
        full_text = re.sub(r"\n{3,}", "\n\n", "\n\n".join(full_parts)).strip()
        min_chars = int(os.getenv("ENNOSCHOLAR_MIN_USEFUL_FULLTEXT_CHARS", "1000"))
        ok = len(full_text) >= min_chars

        pages = [
            {"page": i + 1, "section": s["heading"], "text": f"{s['heading']}\n{s['text']}",
             "chars": len(s["text"]), "has_text": True, "extraction_method": "grobid_tei"}
            for i, s in enumerate(sections)
        ]
        return {
            "ok": ok,
            "status": "grobid_fulltext_extracted" if ok else "grobid_text_insufficient",
            "document_type": "pdf_scientific_article",
            "extraction_method": "grobid_tei",
            "title_extracted": title,
            "abstract_extracted": abstract,
            "sections": sections,
            "pages": pages,
            "pages_count": len(pages),
            "pages_with_text": len(pages),
            "full_text": full_text,
            "clean_text": full_text,
            "full_text_preview": full_text[:3000],
            "text_chars": len(full_text),
            "text_words": len(re.findall(r"\w+", full_text)),
            "quality": {"is_text_extractable": ok, "needs_ocr": False, "empty_pages_count": 0},
            "grobid_used": True,
        }


GROBID = GrobidClient()
