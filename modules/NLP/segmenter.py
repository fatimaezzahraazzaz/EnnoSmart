"""
modules/NLP/segmenter.py
──────────────────────────────────────────────────────────────────────────────
Découpage en passages pour l'approche evidence-first.

  normalizer.py ──► segmenter.py ──► evidence_mapper.py

RÔLE :
  - Découper les chunks normalisés en PASSAGES de taille raisonnable
    (un passage = un appel LLM dans evidence_mapper.py).
  - Deviner un section_role à partir des titres SI ils existent.
  - NE PAS dépendre des titres : un email, un PPT brut, un brouillon
    client n'ont pas de structure « Objectifs / Verrous / Résultats ».
    Dans ce cas section_role = "unknown" et l'evidence_mapper fera
    quand même son travail (il classe la FONCTION des phrases, pas
    la section).

DIFFÉRENCE avec l'ancien semantic_chunker.py :
  - Plus simple, moins de patterns de titres.
  - section_role n'est qu'un INDICE optionnel passé au LLM, jamais
    une condition. On ne filtre RIEN sur la base du rôle de section.
  - Pas de logique de sélection/priorité (c'était dans llm_extractor_smart,
    supprimé). Ici on segmente, point.

Ce module ne dépend ni de GLiNER ni d'un LLM.

Version : 1.0.0
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Taille cible d'un passage envoyé au LLM. Assez petit pour que le LLM
# fasse une micro-tâche fiable, assez grand pour garder le contexte.
DEFAULT_MAX_CHARS = 2800
DEFAULT_OVERLAP_CHARS = 250
MIN_PASSAGE_CHARS = 120


# ══════════════════════════════════════════════════════════════════════════════
# INDICES DE RÔLE DE SECTION (OPTIONNELS)
# ══════════════════════════════════════════════════════════════════════════════
# Ces patterns servent UNIQUEMENT à enrichir le prompt de l'evidence_mapper
# avec un indice. Si aucun ne matche, section_role = "unknown" et tout
# fonctionne quand même. AUCUN filtrage n'est fait sur cette base.

SECTION_ROLE_PATTERNS: list[tuple[str, list[str]]] = [
    ("titre_motscles", [
        r"intitul[ée] de l['’]op[ée]ration",
        r"nom de l['’]op[ée]ration",
        r"mots?[\s-]cl[ée]s?",
        r"fiche descriptive",
        r"titre du projet",
    ]),
    ("contexte", [
        r"contexte",
        r"pr[ée]sentation (?:du projet|globale|g[ée]n[ée]rale)",
        r"probl[ée]matique",
        r"background",
        r"introduction",
    ]),
    ("objectifs", [
        r"objectifs?",
        r"objectifs? vis[ée]s?",
        r"performances? [àa] atteindre",
        r"buts?",
        r"finalit[ée]s?",
    ]),
    ("etat_art", [
        r"[ée]tat de l['’]art",
        r"analyse de l['’][ée]tat de l['’]art",
        r"bibliographie",
        r"r[ée]f[ée]rences",
        r"travaux existants",
        r"prior art",
        r"state of the art",
    ]),
    ("verrous", [
        r"verrous?",
        r"incertitudes?",
        r"limitations? (?:scientifiques?|techniques?)",
        r"limites?",
        r"risques? (?:techniques?|scientifiques?)",
        r"difficult[ée]s",
        r"technical (?:barriers?|challenges?)",
    ]),
    ("demarche", [
        r"d[ée]marche (?:scientifique|exp[ée]rimentale|r&d)",
        r"m[ée]thodologie",
        r"protocole",
        r"plan d['’]essais?",
        r"approche (?:retenue|propos[ée]e)",
    ]),
    ("travaux", [
        r"travaux",
        r"description des travaux",
        r"mise en [œo]uvre",
        r"d[ée]veloppement",
        r"r[ée]alisation",
        r"exp[ée]rimentation",
        r"mod[ée]lisation",
        r"simulation",
    ]),
    ("resultats", [
        r"r[ée]sultats?",
        r"analyse des (?:donn[ée]es|r[ée]sultats)",
        r"performances? (?:obtenues?|mesur[ée]es?)",
        r"validation",
        r"tests? et validation",
        r"essais?",
    ]),
    ("conclusion", [
        r"conclusion",
        r"contribution (?:scientifique|technique|technologique)",
        r"perspectives?",
        r"travaux futurs",
        r"synth[èe]se",
    ]),
    ("administratif", [
        r"d[ée]penses?",
        r"personnel",
        r"budget",
        r"ressources? humaines?",
        r"co[uû]ts?",
        r"\betp\b",
        r"cerfa",
        r"planning",
        r"jalons?",
    ]),
    ("annexe", [
        r"annexes?",
        r"appendix",
    ]),
]


def _compile_role_patterns() -> list[tuple[str, re.Pattern]]:
    compiled: list[tuple[str, re.Pattern]] = []
    for role, patterns in SECTION_ROLE_PATTERNS:
        safe = []
        for p in patterns:
            try:
                re.compile(p, re.IGNORECASE | re.UNICODE)
                safe.append(p)
            except re.error as exc:
                logger.warning("Pattern section invalide ignoré | role=%s | %r | %s", role, p, exc)
        if safe:
            compiled.append((role, re.compile("|".join(f"(?:{p})" for p in safe), re.IGNORECASE | re.UNICODE)))
    return compiled


_SECTION_ROLE_RE = _compile_role_patterns()


# ══════════════════════════════════════════════════════════════════════════════
# DÉTECTION DE TITRES
# ══════════════════════════════════════════════════════════════════════════════

_NUMBERED_HEADING_RE = re.compile(r"^\s*\d{1,2}(?:\.\d{1,3}){0,5}\.?\s+(?P<title>[^\n]{3,160})\s*$", re.UNICODE)
_ROMAN_HEADING_RE = re.compile(r"^\s*[IVXLC]+\.\s+(?P<title>[^\n]{3,160})\s*$", re.IGNORECASE | re.UNICODE)
_LETTER_HEADING_RE = re.compile(r"^\s*[A-Z]\.\s+(?P<title>[^\n]{3,160})\s*$", re.UNICODE)
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(?P<title>[^\n]{3,160})\s*$", re.UNICODE)
_SECTION_MARKER_RE = re.compile(r"^\s*SECTION\s*:\s*(?P<title>[^\n]{3,160})\s*$", re.IGNORECASE | re.UNICODE)

_BAD_HEADING_RE = re.compile(
    r"^(?:page\s+\d+|\d+\s*/\s*\d+|table\s+des\s+mati[èe]res|figure\s+\d+|tableau\s+\d+)$",
    re.IGNORECASE | re.UNICODE,
)

_SHORT_USEFUL_HEADINGS = {
    "objectifs", "objectif", "résultats", "resultats", "résultat", "resultat",
    "verrous", "verrou", "méthodologie", "methodologie", "conclusion",
    "annexes", "annexe", "contexte", "validation", "performances",
    "travaux", "bibliographie", "références", "references", "introduction",
    "démarche", "demarche", "synthèse", "synthese",
}


def _normalize_heading(line: str) -> str:
    text = re.sub(r"\s+", " ", str(line or "")).strip(" -–—\t")
    text = re.sub(r"^\d{1,2}(?:\.\d{1,3}){0,5}\.?\s+", "", text)
    text = re.sub(r"^[IVXLC]+\.\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[A-Z]\.\s+", "", text)
    text = re.sub(r"^#{1,6}\s+", "", text)
    text = re.sub(r"^SECTION\s*:\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


def detect_section_role(title: str, nearby_text: str = "") -> str:
    """Indice de rôle. 'unknown' si rien ne matche — ce n'est PAS bloquant."""
    blob = f"{title}\n{str(nearby_text or '')[:600]}"
    for role, pattern in _SECTION_ROLE_RE:
        if pattern.search(blob):
            return role
    return "unknown"


def _extract_heading_title(raw: str) -> Optional[str]:
    for pattern in (_NUMBERED_HEADING_RE, _ROMAN_HEADING_RE, _LETTER_HEADING_RE,
                    _MARKDOWN_HEADING_RE, _SECTION_MARKER_RE):
        m = pattern.match(raw)
        if m:
            return _normalize_heading(m.group("title"))
    return None


def _looks_like_heading(line: str) -> bool:
    raw = str(line or "").strip()
    if not raw or len(raw) < 3 or len(raw) > 170:
        return False
    if _BAD_HEADING_RE.match(raw):
        return False

    low = _normalize_heading(raw).lower()
    if low in _SHORT_USEFUL_HEADINGS:
        return True

    title = _extract_heading_title(raw)
    if not title:
        # Titre court non numéroté, en majuscules ou reconnu par rôle.
        if len(raw.split()) <= 10 and not raw.endswith((".", ";", ",")):
            if raw.isupper() or detect_section_role(raw) != "unknown":
                return True
        return False

    words = title.split()
    if len(title) < 3 or len(words) > 18:
        return False
    if title.endswith((".", ";", ",")):
        return False
    if title == title.lower() and len(words) > 3:
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Passage:
    """Un passage prêt à être envoyé à l'evidence_mapper."""
    text: str
    passage_id: str                       # ex: "doc_p0007"
    section_title: str = ""               # "" si document non structuré
    section_role: str = "unknown"         # indice optionnel, jamais bloquant
    source_chunk_index: Optional[int] = None
    part_index: int = 0
    char_start: int = 0
    char_end: int = 0
    metadata: dict = field(default_factory=dict)

    def context_hint(self) -> str:
        """Petit en-tête d'indice à passer au LLM (non contraignant)."""
        title = self.section_title or "sans titre"
        return f"[indice_section: {self.section_role} | titre: {title}]"


# ══════════════════════════════════════════════════════════════════════════════
# DÉCOUPAGE
# ══════════════════════════════════════════════════════════════════════════════

def _split_by_headings(text: str, source_chunk_index: int | None) -> list[tuple[str, str, str]]:
    """
    Découpe un chunk en sections selon les titres détectés.
    Retourne [(body, title, role), ...].
    Si aucun titre : une seule section (body, "", "unknown").
    """
    lines = str(text or "").splitlines()
    sections: list[tuple[str, str, str]] = []

    current_title = ""
    current_lines: list[str] = []

    def flush():
        body = "\n".join(current_lines).strip()
        if body:
            role = detect_section_role(current_title, body)
            sections.append((body, current_title, role))

    for line in lines:
        if _looks_like_heading(line):
            if current_lines:
                flush()
            current_title = _normalize_heading(line)
            current_lines = [current_title]
        else:
            current_lines.append(line)
    flush()

    if not sections:
        body = str(text or "").strip()
        if body:
            sections = [(body, "", "unknown")]
    return sections


def _split_long_text(text: str, max_chars: int, overlap_chars: int) -> list[tuple[str, int, int]]:
    """Découpe un texte long en parties, en coupant sur des frontières propres."""
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [(text, 0, len(text))]

    parts: list[tuple[str, int, int]] = []
    start = 0
    n = len(text)
    overlap_chars = max(0, min(overlap_chars, max_chars // 3))

    while start < n:
        hard_end = min(start + max_chars, n)
        if hard_end >= n:
            end = n
        else:
            window = text[start:hard_end]
            candidates = [
                window.rfind("\n\n"),
                window.rfind(". "),
                window.rfind("; "),
                window.rfind("\n- "),
                window.rfind("\n• "),
                window.rfind("\n"),
            ]
            cut = max(candidates)
            if cut < int(max_chars * 0.55):
                cut = max_chars
            end = start + cut

        part = text[start:end].strip()
        if part:
            parts.append((part, start, end))

        if end >= n:
            break
        new_start = max(0, end - overlap_chars)
        if new_start <= start:
            new_start = end
        start = new_start

    return parts


def segment_chunks(
    chunks: list[str],
    doc_id: str = "doc",
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    keep_small: bool = True,
) -> list[Passage]:
    """
    Découpe une liste de chunks normalisés en passages.

    Paramètres
    ----------
    chunks       : chunks normalisés (depuis normalizer.py)
    doc_id       : identifiant document, sert à nommer les passages
    max_chars    : taille max d'un passage
    overlap_chars: chevauchement entre parties d'une même section longue
    keep_small   : si True, garde même les petits passages (utile pour
                   emails courts, slides peu denses)

    Retourne
    --------
    list[Passage]
    """
    passages: list[Passage] = []
    counter = 0

    for chunk_idx, raw in enumerate(chunks or []):
        text = str(raw or "").strip()
        if not text:
            continue

        for body, title, role in _split_by_headings(text, chunk_idx):
            if len(body) < MIN_PASSAGE_CHARS and not keep_small:
                continue

            for part_idx, (part, c_start, c_end) in enumerate(
                _split_long_text(body, max_chars, overlap_chars)
            ):
                # Ré-évalue le rôle sur le contenu réel de la partie.
                part_role = role if role != "unknown" else detect_section_role(title, part)

                # Préfixe le titre s'il n'est pas déjà au début de la partie.
                if title and title.lower() not in part[:200].lower():
                    part_text = f"{title}\n{part}"
                else:
                    part_text = part

                passages.append(
                    Passage(
                        text=part_text,
                        passage_id=f"{doc_id}_p{counter:04d}",
                        section_title=title,
                        section_role=part_role,
                        source_chunk_index=chunk_idx,
                        part_index=part_idx,
                        char_start=c_start,
                        char_end=c_end,
                    )
                )
                counter += 1

    return passages


def summarize_passages(passages: list[Passage]) -> dict:
    """Petit résumé pour le debug."""
    by_role: dict[str, int] = {}
    for p in passages or []:
        by_role[p.section_role] = by_role.get(p.section_role, 0) + 1
    sizes = [len(p.text) for p in passages or []]
    return {
        "count": len(passages or []),
        "by_role": by_role,
        "max_chars": max(sizes, default=0),
        "avg_chars": round(sum(sizes) / len(sizes), 1) if sizes else 0,
    }


# ── Interface rapide (debug / tests) ──────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            chunks = [f.read()]
    else:
        # Test : un doc structuré + un email brut non structuré.
        chunks = [
            "1. Objectifs du projet\n"
            "L'objectif est de développer un emballage médical recyclable "
            "résistant aux chocs et à l'abrasion.\n\n"
            "2. Verrous techniques\n"
            "Le verrou principal est l'incapacité de résoudre simultanément "
            "la tenue aux chocs, la résistance à l'abrasion et la recyclabilité.",

            "Bonjour,\n"
            "Après plusieurs essais, la solution en mousse amortit bien les chocs "
            "mais génère des particules au contact des surfaces agressives. "
            "La solution suspendue réduit ce problème. On n'arrive toujours pas "
            "à garantir la stérilité après ouverture.\n"
            "Cordialement",
        ]

    passages = segment_chunks(chunks, doc_id="test")
    print(json.dumps(summarize_passages(passages), ensure_ascii=False, indent=2))
    print("─" * 60)
    for p in passages:
        print(f"\n[{p.passage_id}] role={p.section_role} | titre={p.section_title!r}")
        print(f"  {p.text[:200]!r}")