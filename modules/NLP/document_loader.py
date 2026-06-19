# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cleaner import clean_text
from .origin_detector import infer_origin
from .document_type_classifier import enrich_document_type

SUPPORTED_EXTENSIONS = {'.pdf','.docx','.doc','.pptx','.ppt','.xlsx','.xls','.csv','.txt','.md','.json','.msg','.eml'}


def normalize_chunks(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    out: List[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                for k in ['text','content','chunk','page_text','rag_chunk']:
                    if isinstance(item.get(k), str):
                        out.append(item[k]); break
            elif hasattr(item, 'text') and isinstance(getattr(item, 'text'), str):
                out.append(getattr(item, 'text'))
            elif hasattr(item, 'content') and isinstance(getattr(item, 'content'), str):
                out.append(getattr(item, 'content'))
    elif isinstance(value, dict):
        for k in ['text','content','chunk','page_text','rag_chunk']:
            if isinstance(value.get(k), str):
                out.append(value[k])
    return out


def extract_text_from_result(result: Any) -> str:
    if result is None:
        return ''
    chunks: List[str] = []
    if isinstance(result, dict):
        for k in ['text_chunks','chunks','pages','texts','content']:
            chunks.extend(normalize_chunks(result.get(k)))
        if isinstance(result.get('text'), str):
            chunks.append(result['text'])
    else:
        for attr in ['text_chunks','chunks','pages','texts','content']:
            if hasattr(result, attr):
                chunks.extend(normalize_chunks(getattr(result, attr)))
        if hasattr(result, 'text') and isinstance(getattr(result, 'text'), str):
            chunks.append(getattr(result, 'text'))
    if isinstance(result, str):
        chunks.append(result)
    return '\n\n'.join(c.strip() for c in chunks if isinstance(c, str) and c.strip())


def extract_with_ennosmart_router(path: str) -> Optional[str]:
    try:
        from modules.extraction.router import extract
    except Exception:
        return None
    try:
        result = extract(path)
    except Exception:
        return None
    text = extract_text_from_result(result)
    text = clean_text(text)
    return text if text.strip() else None


def fallback_extract(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in {'.txt','.md','.csv'}: return read_text_file(path)
    if ext == '.json': return read_json_file(path)
    if ext == '.docx': return read_docx(path)
    if ext == '.pptx': return read_pptx(path)
    if ext in {'.xlsx','.xls'}: return read_excel(path)
    if ext == '.pdf': return read_pdf(path)
    if ext == '.eml': return read_eml(path)
    if ext == '.msg': return read_msg(path)
    return ''


def read_text_file(path: str) -> str:
    for enc in ['utf-8','utf-8-sig','cp1252','latin-1']:
        try:
            return Path(path).read_text(encoding=enc, errors='ignore')
        except Exception:
            pass
    return ''


def read_json_file(path: str) -> str:
    raw = read_text_file(path)
    try:
        data = json.loads(raw)
    except Exception:
        return raw
    parts: List[str] = []
    def walk(x: Any):
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).lower() in {'source_path','file_category','visual_chunks','structured_data','attachments_paths','extraction_errors'}:
                    continue
                walk(v)
        elif isinstance(x, list):
            for i in x: walk(i)
        elif isinstance(x, str) and len(x.strip()) > 25:
            parts.append(x.strip())
    walk(data)
    return '\n\n'.join(parts)


def read_docx(path: str) -> str:
    try:
        from docx import Document
        doc = Document(path)
        parts: List[str] = []
        for p in doc.paragraphs:
            t = p.text.strip()
            if t: parts.append(t)
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells: parts.append(' | '.join(cells))
        return '\n\n'.join(parts)
    except Exception:
        return ''


def read_pptx(path: str) -> str:
    try:
        from pptx import Presentation
        prs = Presentation(path)
        parts: List[str] = []
        for i, slide in enumerate(prs.slides, 1):
            slide_parts = []
            for shape in slide.shapes:
                if hasattr(shape, 'text'):
                    t = shape.text.strip()
                    if t: slide_parts.append(t)
            if slide_parts: parts.append(f'[SLIDE {i}]\n' + '\n'.join(slide_parts))
        return '\n\n'.join(parts)
    except Exception:
        return ''


def read_excel(path: str) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        parts: List[str] = []
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                vals = [str(v).strip() for v in row if v is not None and str(v).strip()]
                if vals: rows.append(' | '.join(vals))
            if rows: parts.append(f'[FEUILLE : {ws.title}]\n' + '\n'.join(rows[:350]))
        return '\n\n'.join(parts)
    except Exception:
        return ''


def read_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        parts = []
        for i, p in enumerate(reader.pages, 1):
            t = (p.extract_text() or '').strip()
            if t: parts.append(f'[PAGE {i}]\n{t}')
        return '\n\n'.join(parts)
    except Exception:
        return ''


def read_eml(path: str) -> str:
    try:
        from email import policy
        from email.parser import BytesParser
        with open(path, 'rb') as f:
            msg = BytesParser(policy=policy.default).parse(f)
        parts = []
        for label, key in [('Objet','subject'),('De','from'),('Date','date')]:
            if msg.get(key): parts.append(f'{label} : {msg.get(key)}')
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain': parts.append(part.get_content())
        elif msg.get_content_type() == 'text/plain':
            parts.append(msg.get_content())
        return '\n\n'.join(parts)
    except Exception:
        return ''


def read_msg(path: str) -> str:
    try:
        import extract_msg
        msg = extract_msg.Message(path)
        parts = []
        if msg.subject: parts.append(f'Objet : {msg.subject}')
        if msg.sender: parts.append(f'De : {msg.sender}')
        if msg.date: parts.append(f'Date : {msg.date}')
        if msg.body: parts.append(msg.body)
        return '\n\n'.join(parts)
    except Exception:
        return ''


def load_document(path: str, use_ennosmart_extraction: bool=True) -> Dict[str, Any]:
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return {'document': p.name, 'source_path': str(p), 'extension': ext, 'text': '', 'loader': 'unsupported', 'error': f'Extension non supportée : {ext}'}
    text = extract_with_ennosmart_router(str(p)) if use_ennosmart_extraction else None
    loader = 'modules.extraction.router' if text else 'fallback'
    if not text:
        text = fallback_extract(str(p))
    text = clean_text(text)
    origin = infer_origin(p.name, text)
    doc = {'document': p.name, 'source_path': str(p), 'extension': ext, 'text': text, 'loader': loader, 'chars': len(text or ''), 'error': None if text else 'Aucun texte extrait', **origin}
    doc = enrich_document_type(doc)
    return doc


def load_documents(paths: List[str], use_ennosmart_extraction: bool=True, include_cir_final: bool=False) -> List[Dict[str, Any]]:
    docs = []
    for path in paths:
        d = load_document(path, use_ennosmart_extraction=use_ennosmart_extraction)
        if not d.get('text','').strip():
            continue
        if d.get('content_origin') == 'cir_final' and not include_cir_final:
            d['skipped_reason'] = 'cir_final_excluded'
            continue
        docs.append(d)
    return docs
