# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .cir_section_mapper import map_section_type


CIR_STRONG_SIGNALS = [
    "credit impot recherche",
    "crédit impôt recherche",
    "verrous et incertitudes",
    "demarche experimentale",
    "démarche expérimentale",
    "etat de l art",
    "état de l’art",
    "objectifs du projet",
    "objectifs vises",
    "conclusion et contribution",
    "travaux r&d",
    "travaux rd",
    "fiche descriptive",
]

SECTION_MARKER_RE = re.compile(r"^\s*\[SECTION\s*:\s*(.+?)\]\s*$", re.I)
NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,6})\.?\s+(.{3,180}?)\s*$")
PLAIN_HEADING_RE = re.compile(
    r"^\s*(FICHE DESCRIPTIVE DU PROJET|TH[ÉE]SAURUS|MOTS[- ]CL[ÉE]S?)\s*$",
    re.I,
)


def _norm(s: str) -> str:
    s = str(s or "").lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _is_toc_start(line: str) -> bool:
    low = str(line or "").strip().lower()
    return low in {"table des matières", "table des matieres", "sommaire"} or low.startswith("table des mat")


def _is_toc_line(line: str) -> bool:
    """
    Détecte une ligne de table des matières Word :
    - PAGEREF / _Toc / TOC \o
    - 1.3. Etat de l'art 5
    """
    raw = str(line or "").strip()
    low = raw.lower()

    if not raw:
        return False

    if _is_toc_start(raw):
        return True

    if "pageref" in low or "_toc" in low or low.startswith("toc \\o"):
        return True

    if re.match(r"^\s*\d+(?:\.\d+)*\.?\s+.{3,180}\s+\d{1,3}\s*$", raw):
        words = re.findall(r"[A-Za-zÀ-ÿ]{3,}", raw)
        if len(words) >= 2:
            return True

    return False


def _drop_toc_lines(lines: List[str]) -> List[str]:
    """Supprime le bloc table des matières avant détection des sections."""
    cleaned: List[str] = []
    in_toc = False

    for line in lines:
        raw = str(line or "").strip()

        if re.match(r"^-?\d{8,}$", raw):
            continue

        if _is_toc_start(raw):
            in_toc = True
            continue

        is_toc = _is_toc_line(raw)

        if in_toc and is_toc:
            continue

        if in_toc and raw and not is_toc:
            in_toc = False

        if is_toc:
            continue

        cleaned.append(line)

    return cleaned


def _looks_like_noise_heading(line: str) -> bool:
    l = str(line or "").strip()
    low = _norm(l)

    if not l:
        return True

    if _is_toc_line(l):
        return True

    if len(l) > 220:
        return True

    if "|" in l and len(l) > 25:
        return True

    if re.search(
        r"\b(tableau|figure|fig\.|rev\.|modification|date|nom / name|echelle|scale|planche|sheet)\b",
        low,
    ):
        return True

    if re.search(r"\b(chef s de projet|ressources humaines|prenom|nom prenom)\b", low):
        return True

    # Bloque les listes simples 1. / 2. qui ne sont pas des titres hiérarchiques CIR.
    if re.match(r"^\s*\d+\.\s+", l) and not re.match(r"^\s*\d+\.\d+", l):
        return True

    # Lignes très techniques de plans uniquement.
    if len(re.findall(r"[A-Za-zÀ-ÿ]{3,}", l)) < 3 and len(l) > 30:
        return True

    return False


def _heading_level(section_id: Optional[str], marker: bool = False) -> int:
    if marker or not section_id:
        return 1
    return min(section_id.count(".") + 1, 6)


def _is_heading(line: str) -> Optional[Dict[str, Any]]:
    raw = str(line or "").strip()

    if _looks_like_noise_heading(raw):
        return None

    m = SECTION_MARKER_RE.match(raw)
    if m:
        title = m.group(1).strip()
        if _looks_like_noise_heading(title):
            return None
        return {
            "section_id": None,
            "title": title,
            "level": 1,
            "raw_title": raw,
            "is_marker": True,
        }

    m = NUMBERED_HEADING_RE.match(raw)
    if m:
        sid = m.group(1).strip(".")
        title = m.group(2).strip(" .\t")

        if _looks_like_noise_heading(title):
            return None

        # Pour éviter les faux titres de données : il faut au moins 1.1
        # ou un titre CIR fort pour niveau 1.
        if "." not in sid and not any(k in _norm(title) for k in ["projet", "fiche", "annexe"]):
            return None

        return {
            "section_id": sid,
            "title": title,
            "level": _heading_level(sid),
            "raw_title": raw,
            "is_marker": False,
        }

    m = PLAIN_HEADING_RE.match(raw)
    if m:
        return {
            "section_id": None,
            "title": m.group(1).strip(),
            "level": 1,
            "raw_title": raw,
            "is_marker": False,
        }

    return None


def _content_is_only_toc(text: str) -> bool:
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    if not lines:
        return False

    toc_like = sum(1 for line in lines[:120] if _is_toc_line(line))
    return toc_like >= max(3, len(lines[:120]) // 3)


def detect_cir_structure(text: str, document: str = "") -> Dict[str, Any]:
    text = str(text or "").replace("\r", "\n")
    lines = text.splitlines()
    lines = _drop_toc_lines(lines)

    sections: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    buf: List[str] = []

    def flush() -> None:
        nonlocal current, buf
        if current is not None:
            content = "\n".join(buf).strip()
            if _content_is_only_toc(content):
                content = ""
            current["text"] = content
            sections.append(current)
        current = None
        buf = []

    for line in lines:
        h = _is_heading(line)
        if h:
            flush()
            current = h
            buf = []
        else:
            if current is not None:
                buf.append(line)

    flush()

    # Fallback si les paragraphes Word sont écrits sous forme [SECTION : titre].
    if not sections and "[SECTION" in text:
        clean_text = "\n".join(_drop_toc_lines(text.splitlines()))
        parts = re.split(r"(\[SECTION\s*:\s*.+?\])", clean_text, flags=re.I)
        cur = None

        for part in parts:
            m = SECTION_MARKER_RE.match(part.strip())
            if m:
                if cur:
                    sections.append(cur)
                cur = {
                    "section_id": None,
                    "title": m.group(1).strip(),
                    "level": 1,
                    "raw_title": part.strip(),
                    "text": "",
                }
            elif cur:
                cur["text"] += "\n" + part

        if cur:
            sections.append(cur)

    # Mapping avec héritage.
    stack: List[Dict[str, Any]] = []
    mapped: List[Dict[str, Any]] = []

    for s in sections:
        level = int(s.get("level") or 1)

        while stack and int(stack[-1].get("level") or 1) >= level:
            stack.pop()

        parent_type = stack[-1].get("section_type") if stack else None
        stype = map_section_type(s.get("title", ""), parent_type=parent_type)

        s = dict(s)
        s["section_type"] = stype
        s["parent_section_type"] = parent_type

        mapped.append(s)
        stack.append(s)

    low = _norm("\n".join(lines)[:80000])
    keyword_hits = sum(1 for k in CIR_STRONG_SIGNALS if _norm(k) in low)

    important_heading_hits = sum(
        1
        for s in mapped
        if s.get("section_type") in {"objectifs", "etat_art", "verrous", "methodes_travaux", "contribution"}
    )

    numbered_heading_count = sum(1 for s in mapped if s.get("section_id"))

    is_cir = (
        keyword_hits >= 3
        or important_heading_hits >= 4
        or (numbered_heading_count >= 8 and keyword_hits >= 2)
    )

    confidence = min(1.0, 0.25 + 0.10 * keyword_hits + 0.05 * important_heading_hits)

    return {
        "document": document,
        "is_cir_structured": bool(is_cir),
        "confidence": round(confidence, 3),
        "keyword_hits": keyword_hits,
        "heading_count": len(mapped),
        "numbered_heading_count": numbered_heading_count,
        "important_heading_hits": important_heading_hits,
        "sections": mapped,
    }
