from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

import fitz
import requests
from tqdm import tqdm

from common import clean_text, dedupe_rows, ensure_dirs, jsonl_write, split_passages

HAL_API = "https://api.hal.science/search/"

def first(value: Any) -> str:
    if isinstance(value, list):
        return clean_text(value[0]) if value else ""
    return clean_text(value)

def fetch_hal(max_docs: int, rows: int = 200) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    start = 0

    while len(docs) < max_docs:
        size = min(rows, max_docs - len(docs))
        params = [
            ("q", "*:*"),
            ("fq", "docType_s:(THESE OR HDR)"),
            ("fq", "submitType_s:file"),
            ("fq", "language_s:fr"),
            ("rows", str(size)),
            ("start", str(start)),
            ("wt", "json"),
            ("fl", "docid,title_s,abstract_s,keyword_s,fileMain_s,uri_s,producedDateY_i,docType_s,language_s"),
            ("sort", "docid asc"),
        ]
        r = requests.get(HAL_API, params=params, timeout=90)
        r.raise_for_status()
        payload = r.json()
        batch = payload.get("response", {}).get("docs", [])
        if not batch:
            break
        docs.extend(batch)
        start += len(batch)
        print(f"[HAL] {len(docs)}/{max_docs}")
        if len(batch) < size:
            break
        time.sleep(0.15)

    return docs[:max_docs]

def extract_pdf_passages(url: str, output_pdf: Path, max_pages: int = 80) -> List[str]:
    try:
        with requests.get(url, stream=True, timeout=(20, 120)) as r:
            r.raise_for_status()
            ctype = str(r.headers.get("content-type") or "").lower()
            content = r.content
            if "pdf" not in ctype and not content.startswith(b"%PDF"):
                return []
            output_pdf.write_bytes(content)

        doc = fitz.open(output_pdf)
        page_texts = []
        for page in doc[: min(len(doc), max_pages)]:
            text = clean_text(page.get_text("text"))
            if text:
                page_texts.append(text)
        doc.close()

        text = "\n\n".join(page_texts)
        return split_passages(text, min_chars=100, max_chars=1500)
    except Exception:
        return []

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    parser.add_argument("--max-docs", type=int, default=1500)
    parser.add_argument("--download-pdfs", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=80)
    args = parser.parse_args()

    root = Path(args.root)
    dirs = ensure_dirs(root)

    docs = fetch_hal(args.max_docs)
    rows: List[Dict[str, Any]] = []
    pdf_success = 0

    for idx, doc in enumerate(tqdm(docs, desc="HAL passages"), start=1):
        project_id = f"HAL_{doc.get('docid', idx)}"
        title = first(doc.get("title_s"))
        abstract = first(doc.get("abstract_s"))
        file_url = first(doc.get("fileMain_s"))
        uri = first(doc.get("uri_s"))
        year = doc.get("producedDateY_i")
        doc_type = first(doc.get("docType_s"))

        # Résumé : source rapide et peu coûteuse.
        if abstract:
            for pno, passage in enumerate(split_passages(abstract), start=1):
                rows.append({
                    "source": "HAL",
                    "project_id": project_id,
                    "document_id": project_id,
                    "title": title,
                    "section_title": "abstract",
                    "text": passage,
                    "language": "fr",
                    "year": year,
                    "doc_type": doc_type,
                    "uri": uri,
                    "file_url": file_url,
                    "passage_no": pno,
                    "annotation_origin": "public_scientific_corpus",
                })

        # Texte complet sur un sous-ensemble contrôlé.
        if file_url and pdf_success < args.download_pdfs:
            pdf_path = dirs["hal_pdf"] / f"{project_id}.pdf"
            passages = extract_pdf_passages(file_url, pdf_path, max_pages=args.max_pages)
            if passages:
                pdf_success += 1
                for pno, passage in enumerate(passages, start=1):
                    rows.append({
                        "source": "HAL",
                        "project_id": project_id,
                        "document_id": project_id,
                        "title": title,
                        "section_title": "fulltext",
                        "text": passage,
                        "language": "fr",
                        "year": year,
                        "doc_type": doc_type,
                        "uri": uri,
                        "file_url": file_url,
                        "passage_no": pno,
                        "annotation_origin": "public_scientific_fulltext",
                    })

    rows = dedupe_rows(rows)
    out = dirs["hal"] / "hal_passages.jsonl"
    count = jsonl_write(out, rows)

    report = {
        "source": "HAL",
        "hal_api": HAL_API,
        "docs_requested": args.max_docs,
        "docs_received": len(docs),
        "pdfs_extracted": pdf_success,
        "passages_written": count,
        "output": str(out),
    }
    report_path = dirs["reports"] / "hal_collection_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[OK] HAL")
    print("Documents :", len(docs))
    print("PDF OK    :", pdf_success)
    print("Passages  :", count)
    print("Sortie    :", out)

if __name__ == "__main__":
    main()
