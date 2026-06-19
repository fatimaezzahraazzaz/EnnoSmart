# -*- coding: utf-8 -*-
"""
candidates.py — V23 contextualisé

Avant : candidats = fenêtres de phrases sans contexte.
Maintenant : candidats = fenêtres de phrases rattachées à une section documentaire.
Le modèle reçoit un champ `model_input` enrichi par le titre parent et le rôle de section,
mais le champ `text` reste le passage source original.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, Any, List, Tuple

from .normalizer import normalize_text
from .cleaner import is_noise_line
from .document_structure_mapper import map_document_structure, context_prefix

SENT_SPLIT = re.compile(r"(?<=[.!?;:])\s+|\n+")


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(s or "").lower()).strip("_")
    return s[:55] or "doc"


def split_sentences(text: str) -> List[str]:
    text = normalize_text(text)
    raw = [x.strip(" -|") for x in SENT_SPLIT.split(text) if x and x.strip()]
    out: List[str] = []
    for s in raw:
        if len(s) < 25:
            continue
        if is_noise_line(s):
            continue
        out.append(s)
    return out


def _section_blocks_to_sentences(blocks: List[str]) -> List[str]:
    sents: List[str] = []
    for b in blocks or []:
        sents.extend(split_sentences(b))
    return sents


def is_good_candidate(text: str) -> bool:
    if not text or len(text) < 45 or len(text) > 1400:
        return False
    low = text.lower()
    if any(x in low for x in ["tapez ici", "nom de la présentation", "document security", "charte graphique"]):
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ]{3,}", text)
    if len(words) < 7:
        return False
    if len(set(w.lower() for w in words)) / max(len(words), 1) < 0.32:
        return False
    return True


def _candidate_key(text: str, section_title: str = "") -> str:
    raw = f"{section_title}\n{text}"
    return hashlib.md5(re.sub(r"\W+", "", raw.lower()).encode("utf-8")).hexdigest()


def _make_one_candidate(doc: Dict[str, Any], sec: Dict[str, Any], text: str, idx: int, w: int) -> Dict[str, Any]:
    item = {
        "passage_id": f"{_slug(doc.get('document','doc'))}_{idx}",
        "document": doc.get("document"),
        "source_path": doc.get("source_path"),
        "source_type": "raw",
        "content_origin": doc.get("content_origin", "unknown"),
        "source_weight": float(doc.get("source_weight", 0.75)),
        "document_type": doc.get("document_type", "unknown_document"),
        "document_type_confidence": doc.get("document_type_confidence", 0.5),
        "section_id": sec.get("section_id"),
        "section_title": sec.get("section_title"),
        "section_path": sec.get("section_path") or [],
        "section_level": sec.get("section_level"),
        "section_role_hint": sec.get("section_role_hint", "unknown"),
        "text": text,
        "model_input": "",
        "window_size": w,
    }
    prefix = context_prefix(item)
    item["model_input"] = f"{prefix}\nPassage: {text}" if prefix else text
    return item


def make_candidates(
    documents: List[Dict[str, Any]],
    max_candidates: int = 700,
    windows: Tuple[int, ...] = (1, 2, 3),
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen = set()

    for raw_doc in documents or []:
        doc = raw_doc if raw_doc.get("sections") else map_document_structure(raw_doc)
        idx = 0
        sections = doc.get("sections") or []
        if not sections:
            sections = [{
                "section_id": "sec_000",
                "section_title": "Document",
                "section_path": ["Document"],
                "section_level": 0,
                "section_role_hint": "unknown",
                "blocks": [doc.get("text", "")],
            }]

        for sec in sections:
            sents = _section_blocks_to_sentences(sec.get("blocks") or [])
            if not sents:
                continue

            # Si une section a un titre très informatif mais peu de contenu, rattacher le titre au premier bloc.
            for w in windows:
                if len(sents) < w:
                    continue
                for i in range(0, len(sents) - w + 1):
                    text = " ".join(sents[i:i + w]).strip()
                    if not is_good_candidate(text):
                        continue
                    key = _candidate_key(text, sec.get("section_title", ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(_make_one_candidate(doc, sec, text, idx, w))
                    idx += 1
                    if len(candidates) >= max_candidates:
                        return candidates

    return candidates
