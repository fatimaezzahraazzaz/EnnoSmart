# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, List, Optional
import re

TITLE_PATTERNS = [
    re.compile(r'^(#{1,4})\s+(.+)$', re.MULTILINE),
    re.compile(r'^(\d+(?:\.\d+)*)\s+(.+)$', re.MULTILINE),
    re.compile(r'^([IVXLCDM]+\.?)\s+(.+)$', re.MULTILINE),
    re.compile(r'^([A-Z][A-Z\s]{2,})\s*$', re.MULTILINE),
    re.compile(r'^(?:Section|Chapitre|Partie)\s+\d+\s*:?\s*(.+)$', re.MULTILINE | re.IGNORECASE),
    re.compile(r'^\[(.*?)\]$', re.MULTILINE),
]

SECTION_ROLE_KEYWORDS = {
    "objectif": ["objectif", "but", "finalité", "goal", "aim", "contexte", "problématique"],
    "verrou": ["verrou", "challenge", "difficulté", "bloquant", "frein", "obstacle", "limitation", "incertitude"],
    "contrainte": ["norme", "réglementation", "reglementation", "certification", "exigence", "standard"],
    "methode": ["méthode", "méthodologie", "approach", "protocole", "processus", "solution", "essai", "test"],
    "resultat": ["résultat", "performance", "metrics", "évaluation", "comparaison", "observation", "mesure"],
    "limite": ["limite", "défaut", "inconvénient"],
    "contribution": ["contribution", "apport", "innovation", "originalité"],
    "parametre": ["paramètre", "réglage", "configuration", "hyperparamètre", "setting"],
}


def is_likely_title(text: str) -> bool:
    text = str(text or "").strip()
    if re.match(r'^(Figure|Tableau|Annexe|Page|Fig\.|Table|Image)\s+\d+', text, re.I):
        return False
    if len(text) < 5 or len(text) > 140:
        return False
    if text.count('|') >= 2:
        return False
    return True


def guess_section_role(title: str) -> Optional[str]:
    low = str(title or "").lower()
    for role, keywords in SECTION_ROLE_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            return role
    return None


def extract_sections(text: str) -> List[Dict[str, Any]]:
    lines = str(text or "").splitlines()
    sections = []
    current_title = None
    current_level = None
    current_role = None
    current_content = []

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        is_title = False
        title_text = None
        level = None

        for pattern in TITLE_PATTERNS:
            m = pattern.match(line_stripped)
            if m:
                is_title = True
                if len(m.groups()) == 2:
                    title_text = m.group(2).strip()
                    level = len(m.group(1)) if pattern == TITLE_PATTERNS[0] else 2
                else:
                    title_text = m.group(1).strip()
                    level = 2
                break

        if is_title and title_text and is_likely_title(title_text):
            if current_title is not None and current_content:
                sections.append({
                    "title": current_title,
                    "level": current_level,
                    "role_hint": current_role,
                    "content": "\n".join(current_content).strip(),
                })
            current_title = title_text
            current_level = level
            current_role = guess_section_role(title_text)
            current_content = []
        else:
            if current_title is None:
                current_title = "INTRODUCTION"
                current_level = 1
                current_role = None
            current_content.append(line)

    if current_title is not None and current_content:
        sections.append({
            "title": current_title,
            "level": current_level,
            "role_hint": current_role,
            "content": "\n".join(current_content).strip(),
        })

    return [s for s in sections if len(s.get("content", "")) > 50]


def get_structure_type(sections: List[Dict[str, Any]]) -> str:
    if not sections:
        return "unknown_structure"
    avg_len = sum(len(s.get("content", "")) for s in sections) / max(1, len(sections))
    if avg_len < 300:
        return "presentation_structure"
    return "report_structure"


def analyze_structure(doc_text: str) -> Dict[str, Any]:
    sections = extract_sections(doc_text)
    return {
        "structure_type": get_structure_type(sections),
        "sections": sections,
        "num_sections": len(sections),
    }
