# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import math
import re
from typing import Dict, Any, List, Optional, Tuple

from .normalizer import normalize_text
from .cleaner import is_noise_line

SENT_SPLIT = re.compile(r'(?<=[.!?;:])\s+|\n+')


def _slug(s: str) -> str:
    s = re.sub(r'[^a-zA-Z0-9]+', '_', str(s or '').lower()).strip('_')
    return s[:55] or 'doc'


def split_sentences(text: str) -> List[str]:
    text = normalize_text(text)
    raw = [x.strip(' -|') for x in SENT_SPLIT.split(text) if x and x.strip()]
    out = []
    for s in raw:
        if len(s) < 25:
            continue
        if is_noise_line(s):
            continue
        out.append(s)
    return out


def is_good_candidate(text: str) -> bool:
    if not text or len(text) < 45 or len(text) > 1500:
        return False
    low = text.lower()
    if any(x in low for x in ['tapez ici', 'nom de la présentation', 'document security', 'charte graphique']):
        return False
    words = re.findall(r'[A-Za-zÀ-ÿ]{3,}', text)
    if len(words) < 6:
        return False
    if len(set(w.lower() for w in words)) / max(len(words), 1) < 0.30:
        return False
    return True


def _candidate_key(text: str) -> str:
    return hashlib.md5(re.sub(r'\W+', '', text.lower()).encode('utf-8')).hexdigest()


def _find_section(text: str, doc_sections: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    for sec in doc_sections or []:
        content = str(sec.get("content") or "")
        if text in content:
            return sec.get("title"), sec.get("role_hint")
    return None, None


def _windows_for_doc(sents_count: int, windows) -> List[int]:
    if sents_count < 12:
        return [w for w in (1, 2, 3) if w <= max(sents_count, 1)]
    return list(windows)


def make_candidates(
    documents: List[Dict[str, Any]],
    max_candidates: int = 600,
    windows=(2, 3, 4),
    section_info: Optional[List[Dict[str, Any]]] = None,
    min_candidates_per_doc: int = 8,
    max_candidates_per_doc: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Génère les candidats de manière équilibrée multi-documents.

    Ancien comportement : un gros document en début de liste pouvait consommer tout max_candidates.
    Nouveau comportement : chaque document reçoit un quota, puis les places restantes sont redistribuées.
    """
    documents = documents or []
    if not documents:
        return []

    n_docs = len(documents)
    base_quota = max(min_candidates_per_doc, int(math.ceil(max_candidates / max(n_docs, 1))))
    if max_candidates_per_doc is not None:
        base_quota = min(base_quota, int(max_candidates_per_doc))

    all_by_doc: List[List[Dict[str, Any]]] = []
    global_seen = set()

    for idx_doc, doc in enumerate(documents):
        sents = split_sentences(doc.get('text', ''))
        did = _slug(doc.get('document', 'doc'))
        doc_sections = []
        structure_type = None

        if section_info and idx_doc < len(section_info):
            structure_type = section_info[idx_doc].get("structure_type") or section_info[idx_doc].get("document_type")
            doc_sections = section_info[idx_doc].get("sections", [])

        # IMPORTANT : le vrai document_type vient du document enrichi, pas du structure_analyzer.
        doc_type = doc.get("document_type") or "unknown_document"
        source_policy = doc.get("source_policy") or "secondary"
        document_weight = float(doc.get("document_weight") or doc.get("source_weight") or 0.55)

        local_candidates: List[Dict[str, Any]] = []
        local_seen = set()
        idx = 0

        for w in _windows_for_doc(len(sents), windows):
            for i in range(0, max(0, len(sents) - w + 1)):
                text = ' '.join(sents[i:i + w]).strip()
                if not is_good_candidate(text):
                    continue

                key = _candidate_key(text)
                if key in local_seen:
                    continue
                local_seen.add(key)

                section_title, section_role_hint = _find_section(text, doc_sections)

                local_candidates.append({
                    'passage_id': f'{did}_{idx}',
                    'document': doc.get('document'),
                    'source_path': doc.get('source_path'),
                    'source_type': 'raw',
                    'content_origin': doc.get('content_origin', 'unknown'),
                    'source_weight': float(doc.get('source_weight', document_weight)),
                    'text': text,
                    'window_size': w,
                    'document_type': doc_type,
                    'source_policy': source_policy,
                    'document_weight': document_weight,
                    'document_type_confidence': doc.get('document_type_confidence'),
                    'structure_type': structure_type,
                    'section_title': section_title,
                    'section_role_hint': section_role_hint,
                })
                idx += 1

        # D'abord quota par document pour garantir la couverture.
        selected = []
        for c in local_candidates:
            key = _candidate_key(c.get("text", ""))
            if key in global_seen:
                continue
            selected.append(c)
            global_seen.add(key)
            if len(selected) >= base_quota:
                break

        all_by_doc.append(selected)

    # Aplatir les quotas garantis.
    candidates: List[Dict[str, Any]] = [c for bucket in all_by_doc for c in bucket]

    # Si on dépasse max_candidates, garder au moins une couverture documentaire.
    if len(candidates) > max_candidates:
        candidates = candidates[:max_candidates]

    return candidates
