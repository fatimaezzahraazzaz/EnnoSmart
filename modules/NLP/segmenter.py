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

CORRECTIONS v1.1.0 :
  - Détection des passages de type tableau (markdown | séparateurs).
  - Les passages "table" reçoivent source_type="table" dans leur metadata.
  - Cela permet à l'evidence_mapper de router ces passages vers un handler
    dédié pour l'extraction d'entités structurées (personnes, équipements…)
    sans qu'ils soient scorés comme des passages R&D et éliminés.

Ce module ne dépend ni de GLiNER ni d'un LLM.

Version : 1.1.0
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 2800
DEFAULT_OVERLAP_CHARS = 250
MIN_PASSAGE_CHARS = 80   # CORRIGÉ : était 120, abaissé pour capturer les petits tableaux


# ══════════════════════════════════════════════════════════════════════════════
# DÉTECTION DE TABLEAUX
# ══════════════════════════════════════════════════════════════════════════════

# Ligne de séparation markdown  | --- | --- |  ou  |:---|:---:|
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s\-:]+\|", re.UNICODE)

# Ligne de cellules markdown    | Nom | Valeur |
_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|", re.UNICODE)

# Séparateurs tabulaires classiques (TSV, CSV aligné, etc.)
_TAB_SEP_RE = re.compile(r"\t[^\t]+\t", re.UNICODE)


def _is_table_content(text: str) -> bool:
    """
    Retourne True si le passage est majoritairement un tableau.
    Critère : ≥ 3 lignes dont > 55 % ressemblent à des lignes de tableau.
    """
    lines = [l for l in str(text or "").splitlines() if l.strip()]
    if len(lines) < 2:
        return False
    table_hits = sum(
        1
        for l in lines
        if _TABLE_ROW_RE.match(l) or _TABLE_SEP_RE.match(l) or _TAB_SEP_RE.search(l)
    )
    return table_hits / max(len(lines), 1) >= 0.55


# ══════════════════════════════════════════════════════════════════════════════
# INDICES DE RÔLE DE SECTION (OPTIONNELS)
# ══════════════════════════════════════════════════════════════════════════════

SECTION_ROLE_PATTERNS: list[tuple[str, list[str]]] = [
    ("titre_motscles", [
        r"intitul[ée] de l['']op[ée]ration",
        r"nom de l['']op[ée]ration",
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
        r"[ée]tat de l['']art",
        r"analyse de l[''][ée]tat de l['']art",
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
        r"plan d['']essais?",
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
    passage_id: str
    section_title: str = ""
    section_role: str = "unknown"
    source_chunk_index: Optional[int] = None
    part_index: int = 0
    char_start: int = 0
    char_end: int = 0
    # NOUVEAU : "text" | "table" | "visual" | "email_body"
    source_type: str = "text"
    metadata: dict = field(default_factory=dict)

    def context_hint(self) -> str:
        """Petit en-tête d'indice à passer au LLM (non contraignant)."""
        title = self.section_title or "sans titre"
        hint = f"[indice_section: {self.section_role} | titre: {title}"
        if self.source_type == "table":
            hint += " | type: tableau_structuré"
        hint += "]"
        return hint


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

    NOUVEAU :
    - Chaque passage reçoit source_type="table" si son contenu est
      majoritairement un tableau markdown/TSV.
    - keep_small s'applique aussi aux tableaux (ils peuvent être petits).
    """
    passages: list[Passage] = []
    counter = 0

    for chunk_idx, raw in enumerate(chunks or []):
        text = str(raw or "").strip()
        if not text:
            continue

        for body, title, role in _split_by_headings(text, chunk_idx):
            is_table = _is_table_content(body)

            # CORRIGÉ : les tableaux ne sont pas filtrés même s'ils sont courts
            if len(body) < MIN_PASSAGE_CHARS and not keep_small and not is_table:
                continue

            for part_idx, (part, c_start, c_end) in enumerate(
                _split_long_text(body, max_chars, overlap_chars)
            ):
                part_role = role if role != "unknown" else detect_section_role(title, part)

                # Pour les tableaux, on force le rôle section si non détecté
                if is_table and part_role == "unknown":
                    # Essai de détection via le titre parent
                    part_role = detect_section_role(title, "") or "unknown"

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
                        source_type="table" if is_table else "text",
                    )
                )
                counter += 1

    return passages


def summarize_passages(passages: list[Passage]) -> dict:
    """Petit résumé pour le debug."""
    by_role: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for p in passages or []:
        by_role[p.section_role] = by_role.get(p.section_role, 0) + 1
        by_type[p.source_type] = by_type.get(p.source_type, 0) + 1
    sizes = [len(p.text) for p in passages or []]
    return {
        "count": len(passages or []),
        "by_role": by_role,
        "by_type": by_type,
        "max_chars": max(sizes, default=0),
        "avg_chars": round(sum(sizes) / len(sizes), 1) if sizes else 0,
    }


if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.INFO)

    test_table = """Description des ressources humaines
| NOM Prénom | Diplôme | Fonction | Temps (h) |
| --- | --- | --- | --- |
| DUPONT Jean | Master | Ingénieur étude | 210 |
| MARTIN Sophie | BUT | Technicien essais | 230 |
| CHEVALLIER Nicolas | Ingénieur | Chef de projet | 245 |"""

    test_text = """1. Objectifs du projet
L'objectif est de développer un banc d'essai acoustique à basse température.

2. Verrous techniques
Le verrou principal est l'incapacité de maintenir une température stable
de -24°C dans un environnement anéchoïque simultanément."""

    passages = segment_chunks([test_text, test_table], doc_id="test")
    print(json.dumps(summarize_passages(passages), ensure_ascii=False, indent=2))
    print("─" * 60)
    for p in passages:
        print(f"\n[{p.passage_id}] role={p.section_role} | type={p.source_type} | titre={p.section_title!r}")
        print(f"  {p.text[:200]!r}")

# ══════════════════════════════════════════════════════════════════════════════
# PATCH UNIVERSEL v1.2 — rôles de section plus stables
# - distingue annexe / travaux antérieurs pour ne pas mélanger 2021-2023 avec 2024
# - évite de traiter une ligne de table des matières comme titre métier
# ══════════════════════════════════════════════════════════════════════════════

_TOC_HEADING_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?[A-Za-zÀ-ÿ0-9 ,;:'’()/_\-]{3,140}\s+\d{1,3}\s*$",
    re.I | re.U,
)

_TRAVAUX_ANTERIEURS_RE = re.compile(
    r"(?:travaux antérieurs|travaux anterieurs|rappel des travaux antérieurs|"
    r"rappel des travaux anterieurs|annexe.*travaux)",
    re.I | re.U,
)

_ORIGINAL_DETECT_SECTION_ROLE_V11 = detect_section_role
_ORIGINAL_LOOKS_LIKE_HEADING_V11 = _looks_like_heading


def detect_section_role(title: str, nearby_text: str = "") -> str:  # type: ignore[override]
    blob = f"{title}\n{str(nearby_text or '')[:800]}"
    if _TRAVAUX_ANTERIEURS_RE.search(blob):
        return "contexte"
    return _ORIGINAL_DETECT_SECTION_ROLE_V11(title, nearby_text)


def _looks_like_heading(line: str) -> bool:  # type: ignore[override]
    raw = str(line or "").strip()
    # Les lignes de TdM finissant par un numéro de page ne sont pas des titres actifs.
    if _TOC_HEADING_RE.match(raw) and len(raw.split()) <= 16:
        return False
    return _ORIGINAL_LOOKS_LIKE_HEADING_V11(line)


# ══════════════════════════════════════════════════════════════════════════════
# PATCH FINAL UNIVERSEL v1.2
# - Ne considère plus les lignes de table des matières comme titres réels.
# - Évite au maximum les débuts de passage au milieu d'un mot.
# ══════════════════════════════════════════════════════════════════════════════

_TOC_ENTRY_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?[A-Za-zÀ-ÿ0-9 ,;:'’()/_\-]{3,140}\s+\d{1,3}\s*$",
    re.I | re.U,
)

_VERB_SIGNAL_RE = re.compile(
    r"\b(?:est|sont|était|etaient|étaient|avons|avez|ont|sera|serait|permet|"
    r"permettent|nécessite|implique|consiste|vise|visant|porte|réalis|montr|"
    r"révèl|indiq|confirm|démontr|développ|défini|calcul|mesur)\b",
    re.I | re.U,
)


def _looks_like_toc_entry_final(line: str) -> bool:
    raw = re.sub(r"\s+", " ", str(line or "")).strip()
    if not raw:
        return False
    if _TOC_ENTRY_RE.match(raw) and len(raw.split()) <= 18 and not _VERB_SIGNAL_RE.search(raw):
        return True
    return False


_LOOKS_LIKE_HEADING_BASE = _looks_like_heading


def _looks_like_heading(line: str) -> bool:  # type: ignore[override]
    if _looks_like_toc_entry_final(line):
        return False
    return _LOOKS_LIKE_HEADING_BASE(line)


def _advance_to_clean_start(text: str, start: int) -> int:
    """Si l'overlap commence au milieu d'un mot, avance à la prochaine frontière propre."""
    n = len(text)
    if start <= 0 or start >= n:
        return start
    # Si caractère précédent et courant sont alphanumériques, on est dans un mot.
    if text[start - 1].isalnum() and text[start].isalnum():
        candidates = []
        for sep in ["\n\n", ". ", "; ", "\n- ", "\n• ", "\n"]:
            pos = text.find(sep, start, min(n, start + 350))
            if pos != -1:
                candidates.append(pos + len(sep))
        if candidates:
            return min(candidates)
        # sinon au prochain espace
        pos = text.find(" ", start, min(n, start + 120))
        if pos != -1:
            return pos + 1
    return start


def _split_long_text(text: str, max_chars: int, overlap_chars: int) -> list[tuple[str, int, int]]:  # type: ignore[override]
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [(text, 0, len(text))]

    parts: list[tuple[str, int, int]] = []
    start = 0
    n = len(text)
    overlap_chars = max(0, min(overlap_chars, max_chars // 4))

    while start < n:
        start = _advance_to_clean_start(text, start)
        hard_end = min(start + max_chars, n)
        if hard_end >= n:
            end = n
        else:
            window = text[start:hard_end]
            candidates = [
                window.rfind("\n\n"), window.rfind(". "), window.rfind("; "),
                window.rfind("\n- "), window.rfind("\n• "), window.rfind("\n"),
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
        new_start = _advance_to_clean_start(text, new_start)
        if new_start <= start:
            new_start = end
        start = new_start

    return parts


# ══════════════════════════════════════════════════════════════════════════════
# PATCH v1.3 — rôles de sections stricts et universels
# Objectif : corriger les confusions "etat_art→contexte", "résultats→objectifs",
# "RH→objectifs", et identifier les travaux antérieurs sans dépendre du domaine.
# ══════════════════════════════════════════════════════════════════════════════

_SECTION_EXACT_ROLE_RULES = [
    ("administratif", re.compile(r"\b(description\s+des\s+ressources\s+humaines|ressources\s+humaines|indicateurs?\s+de\s+r\s*&?\s*d|d[ée]penses?|budget|co[uû]ts?|etp|personnel)\b", re.I | re.U)),
    ("titre_motscles", re.compile(r"\b(intitul[ée]\s+de\s+l['’]?op[ée]ration|nom\s+de\s+l['’]?op[ée]ration|mots?\s*-?\s*cl[ée]s?|th[ée]saurus|fiche\s+descriptive)\b", re.I | re.U)),
    ("travaux_anterieurs", re.compile(r"\b(rappel\s+des\s+travaux\s+ant[ée]rieurs|description\s+des\s+travaux\s+ant[ée]rieurs|travaux\s+ant[ée]rieurs)\b", re.I | re.U)),
    ("resultats", re.compile(r"\b(r[ée]sultats?\s+de\s+r\s*&?\s*d|analyse\s+des\s+r[ée]sultats?|r[ée]sultats?\s+obtenus?)\b", re.I | re.U)),
    ("conclusion", re.compile(r"\b(conclusion|contribution\s+(?:scientifique|technique|technologique)|perspectives?)\b", re.I | re.U)),
    ("verrous", re.compile(r"\b(verrous?|incertitudes?\s+(?:scientifiques?|techniques?|technologiques?)|difficult[ée]s?|limites?)\b", re.I | re.U)),
    ("objectifs", re.compile(r"\b(objectifs?\s+vis[ée]s?|objectifs?\s+de\s+l['’]?op[ée]ration|buts?|finalit[ée]s?|performances?\s+[àa]\s+atteindre)\b", re.I | re.U)),
    ("etat_art", re.compile(r"\b(analyse\s+de\s+l['’]?[ée]tat\s+de\s+l['’]?art|[ée]tat\s+de\s+l['’]?art|bibliographie|travaux\s+existants|r[ée]f[ée]rences?|state\s+of\s+the\s+art|prior\s+art)\b", re.I | re.U)),
    ("travaux", re.compile(r"\b(description\s+des\s+travaux\s+r[ée]alis[ée]s|travaux\s+r[ée]alis[ée]s|d[ée]marche\s+scientifique|travaux\s+de\s+r\s*&?\s*d\s+r[ée]alis[ée]s|description\s+des\s+travaux)\b", re.I | re.U)),
    ("demarche", re.compile(r"\b(d[ée]marche|m[ée]thodologie|protocole|plan\s+d['’]?essais?|approche\s+(?:retenue|propos[ée]e))\b", re.I | re.U)),
    ("contexte", re.compile(r"\b(contexte|introduction|probl[ée]matique|pr[ée]sentation)\b", re.I | re.U)),
]

_TOC_STRICT_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?[A-Za-zÀ-ÿ0-9 ,;:'’()/_\-]{3,150}\s+\d{1,3}\s*$", re.I | re.U)


def _title_role_strict(title: str) -> str:
    t = re.sub(r"\s+", " ", _normalize_heading(str(title or ""))).strip()
    if not t:
        return "unknown"
    if _TOC_STRICT_RE.match(t) and len(t.split()) <= 18:
        return "unknown"
    for role, pat in _SECTION_EXACT_ROLE_RULES:
        if pat.search(t):
            return role
    return "unknown"


def detect_section_role(title: str, nearby_text: str = "") -> str:  # type: ignore[override]
    """Détection finale : le TITRE prime sur le contenu, puis fallback contenu."""
    role = _title_role_strict(title)
    if role != "unknown":
        return role

    # Fallback uniquement sur le début du passage, en évitant qu'un mot comme
    # "objectif" dans le corps fasse basculer une section résultats/conclusion.
    nearby_head = str(nearby_text or "")[:350]
    for fallback_role in ["resultats", "verrous", "objectifs", "etat_art", "travaux_anterieurs", "travaux", "administratif", "conclusion", "contexte"]:
        for role_name, pat in _SECTION_EXACT_ROLE_RULES:
            if role_name == fallback_role and pat.search(nearby_head):
                return role_name
    return "unknown"


def _looks_like_heading(line: str) -> bool:  # type: ignore[override]
    raw = str(line or "").strip()
    if not raw or len(raw) < 3 or len(raw) > 170:
        return False
    # Les lignes de TdM ne deviennent jamais des titres actifs.
    if _looks_like_toc_entry_final(raw):
        return False
    # Les titres exacts connus sont toujours acceptés, même en minuscules.
    if _title_role_strict(raw) != "unknown":
        return True
    return _LOOKS_LIKE_HEADING_BASE(raw)


# ══════════════════════════════════════════════════════════════════════════════
# PATCH v1.4 — inférence de rôle par CONTENU du passage (documents narratifs)
#
# Problème résolu : quand un document n'a pas de titres structurés
# ("Verrous", "Objectifs"…), tous les passages reçoivent section_role="unknown"
# avec un score bas dans evidence_mapper, et sont évincés du budget LLM.
#
# Solution : après segmentation, si un passage est "unknown", on inspecte son
# contenu pour deviner son rôle fonctionnel CIR.
# UNIVERSEL : aucune règle liée à un domaine métier.
# ══════════════════════════════════════════════════════════════════════════════

_CONTENT_ROLE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("verrous", re.compile(
        r"\b(?:"
        r"verrou|incertitude\s+(?:scientifique|technique)|"
        r"ne\s+permet\s+pas|ne\s+permettent\s+pas|n[''']arrive\s+pas|"
        r"incapacit[ée]|probl[èe]me\s+non\s+r[ée]solu|obstacle\s+(?:technique|majeur)|"
        r"blocage|gap\s+(?:technique|scientifique)|absence\s+de\s+solution|"
        r"aucune\s+solution\s+(?:connue|existante|satisfaisante)|"
        r"aucun\s+proc[ée]d[ée]|n[''']existe\s+pas|reste\s+[àa]\s+d[ée]passer|"
        r"difficult[ée]\s+(?:majeure|principale|fondamentale)|"
        r"ne\s+ma[îi]trise\s+pas|ne\s+contr[ôo]le\s+pas|"
        r"insuffisance\s+de|manque\s+de\s+(?:donn[ée]es|connaissances|m[ée]thode)"
        r")\b",
        re.I | re.U,
    )),
    ("objectifs", re.compile(
        r"\b(?:"
        r"ce\s+projet\s+vise|le\s+pr[ée]sent\s+projet\s+vise|l[''']objectif\s+(?:est|principal)|"
        r"nous\s+visons\s+[àa]|nous\s+cherchons\s+[àa]|l[''']enjeu\s+(?:est|principal)|"
        r"notre\s+(?:objectif|but|ambition)|atteindre\s+(?:un|cet|l[''']objectif)|"
        r"d[ée]velopper\s+une\s+solution|pour\s+r[ée]soudre|pour\s+pallier|"
        r"pour\s+permettre\s+de|a\s+pour\s+objectif|a\s+pour\s+but|"
        r"dans\s+le\s+but\s+de|performances?\s+[àa]\s+atteindre"
        r")\b",
        re.I | re.U,
    )),
    ("resultats", re.compile(
        r"\b(?:"
        r"(?:les\s+)?r[ée]sultats?\s+(?:montrent|indiquent|confirment|obtenus|de\s+nos\s+essais)|"
        r"nous\s+avons\s+obtenu|on\s+observe|la\s+mesure\s+(?:indique|montre)|"
        r"les\s+essais?\s+(?:ont\s+confirm[ée]|montrent|r[ée]v[èe]lent)|"
        r"les\s+tests?\s+(?:ont\s+confirm[ée]|montrent|r[ée]v[èe]lent)|"
        r"corr[ée]lation\s+(?:satisfaisante|bonne|tr[èe]s\s+bonne)|"
        r"pr[ée]cision\s+(?:de|obtenue|mesur[ée])|"
        r"gain\s+de\s+\d|r[ée]duction\s+de\s+\d|am[ée]lioration\s+de\s+\d|"
        r"performances?\s+(?:obtenues?|mesur[ée]es?)|"
        r"validation\s+(?:r[ée]ussie|satisfaisante|positive|du\s+mod[èe]le)"
        r")\b",
        re.I | re.U,
    )),
    ("demarche", re.compile(
        r"\b(?:"
        r"nous\s+avons\s+(?:adopt[ée]|utilis[ée]|mis\s+en\s+place|d[ée]velopp[ée]|r[ée]alis[ée]|choisi)|"
        r"la\s+m[ée]thode\s+(?:utilis[ée]|retenue|choisie|propos[ée]e)|"
        r"l[''']approche\s+(?:retenue|choisie|propos[ée]e)|"
        r"le\s+protocole\s+(?:consiste|utilis[ée]|retenu)|"
        r"nous\s+avons\s+impl[ée]ment[ée]|"
        r"notre\s+(?:d[ée]marche|m[ée]thode|approche|strat[ée]gie)\s+(?:consiste|est|a\s+consist[ée])|"
        r"mise\s+en\s+[œo]uvre\s+de|d[ée]roulement\s+des\s+travaux"
        r")\b",
        re.I | re.U,
    )),
    ("etat_art", re.compile(
        r"\b(?:"
        r"[ée]tat\s+de\s+l[''']art|les\s+travaux\s+existants|les\s+solutions?\s+existantes?|"
        r"dans\s+la\s+litt[ée]rature|les\s+publications?|les\s+auteurs|et\s+al\.|"
        r"bibliographie|les\s+m[ée]thodes?\s+existantes?|"
        r"comparaison\s+(?:avec|aux)\s+(?:l[''']existant|les\s+travaux)|"
        r"solution\s+commerciale|produit\s+(?:du\s+march[ée]|commercial)|"
        r"technologie\s+(?:utilis[ée]e\s+dans\s+l[''']industrie|disponible\s+sur\s+le\s+march[ée])"
        r")\b",
        re.I | re.U,
    )),
    ("essai", re.compile(
        r"\b(?:"
        r"essais?\s+(?:de|r[ée]alis[ée]s?|effectu[ée]s?|conduits?)|"
        r"banc\s+d[''']essai|campagne\s+d[''']essais?|plan\s+d[''']essais?|"
        r"protocole\s+exp[ée]rimental|exp[ée]rimentations?\s+(?:r[ée]alis[ée]es?|men[ée]es?)|"
        r"mesures?\s+(?:effectu[ée]es?|r[ée]alis[ée]es?)|"
        r"tests?\s+(?:de\s+validation|de\s+performance|r[ée]alis[ée]s?|effectu[ée]s?)"
        r")\b",
        re.I | re.U,
    )),
]


def _infer_role_from_content(text: str, current_role: str) -> str:
    """
    Si section_role est 'unknown', tente de deviner le rôle fonctionnel CIR
    depuis le contenu textuel du passage.

    Critère : le pattern le plus fort (nombre de matchs) l'emporte.
    En cas d'égalité, on conserve 'unknown' (pas de faux positif).

    UNIVERSEL : aucune règle domaine-spécifique.
    """
    if current_role != "unknown":
        return current_role

    text = str(text or "")
    if len(text) < 60:
        return "unknown"

    best_role = "unknown"
    best_count = 0

    for role, pattern in _CONTENT_ROLE_PATTERNS:
        matches = len(pattern.findall(text))
        if matches > best_count:
            best_count = matches
            best_role = role

    # Seuil minimal : au moins 1 match fort pour changer le rôle.
    if best_count >= 1:
        logger.debug(
            "_infer_role_from_content : 'unknown' → '%s' (%d signal(s)) | extrait=%r",
            best_role, best_count, text[:80],
        )
        return best_role

    return "unknown"


# Patch segment_chunks pour appeler _infer_role_from_content après segmentation.
_SEGMENT_CHUNKS_V13 = segment_chunks


def segment_chunks(  # type: ignore[override]
    chunks: list[str],
    doc_id: str = "doc",
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    keep_small: bool = True,
) -> list[Passage]:
    """
    v1.4 : ajoute l'inférence de rôle par contenu pour les passages 'unknown'.
    """
    passages = _SEGMENT_CHUNKS_V13(chunks, doc_id, max_chars, overlap_chars, keep_small)

    enriched: list[Passage] = []
    for p in passages:
        if p.section_role == "unknown" and p.source_type != "table":
            inferred = _infer_role_from_content(p.text, p.section_role)
            if inferred != "unknown":
                p.section_role = inferred
        enriched.append(p)

    return enriched