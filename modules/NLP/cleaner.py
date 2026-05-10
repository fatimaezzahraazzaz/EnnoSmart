"""
modules/nlp/cleaner.py
──────────────────────────────────────────────────────────────────────────────
Nettoyage du texte brut extrait pour dossiers R&D / CIR multilingues.

Rôle :
  - Nettoyer les chunks issus de l'extraction.
  - Préserver le contenu métier utile.
  - Supprimer les métadonnées structurelles AVANT le NER :
      [SLIDE], [NOTES PRÉSENTATEUR], [FORMULES], LaTeX, Domaine, Confiance...
  - Éviter que GLiNER détecte NOTE, SLIDE, FORMULES, PHYSIQUE, LATEX
    comme technologies ou domaines.

Sources traitées :
  PDF natif
  PDF OCR
  DOCX / PPTX
  Excel
  Email
  Vision Qwen

Auteur  : EnnoSmart
Version : 2.1.0
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════

MIN_LINE_LENGTH = 2
MAX_LINE_REPETITIONS = 2


LIGATURE_MAP: dict[str, str] = {
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬀ": "ff",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
    "œ": "oe",
    "æ": "ae",
    "Œ": "Oe",
    "Æ": "Ae",
}


UNICODE_BULLETS: dict[str, str] = {
    "\u2022": "-",
    "\u2023": "-",
    "\u25cf": "-",
    "\u25e6": "-",
    "\u2043": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u00b7": "-",
    "\u2026": "...",
    "\u00a0": " ",
    "\u200b": "",
    "\u200c": "",
    "\u200d": "",
    "\ufeff": "",
    "\u2019": "'",
    "\u2018": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u00ab": '"',
    "\u00bb": '"',
}


ACRONYM_FIXES: list[tuple[str, str]] = [
    # Français
    (r"\bR\s*&\s*D\b", "R&D"),
    (r"\bC\s*I\s*R\b", "CIR"),
    (r"\bC\s*I\s*I\b", "CII"),
    (r"\bB\s*P\s*I\b", "BPI"),
    (r"\bA\s*N\s*R\b", "ANR"),
    (r"\bA\s*N\s*R\s*T\b", "ANRT"),
    (r"\bE\s*T\s*P\b", "ETP"),
    (r"\bI\s*P\b", "IP"),
    (r"\bT\s*R\s*L\b", "TRL"),
    (r"\bP\s*M\s*E\b", "PME"),
    (r"\bE\s*T\s*I\b", "ETI"),
    (r"\bI\s*N\s*P\s*I\b", "INPI"),
    (r"\bC\s*N\s*R\s*S\b", "CNRS"),
    (r"\bC\s*E\s*A\b", "CEA"),
    (r"\bI\s*N\s*R\s*A\b", "INRA"),
    (r"\bI\s*N\s*R\s*I\s*A\b", "INRIA"),
    (r"\bD\s*G\s*E\b", "DGE"),
    (r"\bA\s*D\s*E\s*M\s*E\b", "ADEME"),
    (r"\bO\s*P\s*C\s*I\b", "OPCI"),

    # Anglais / technique
    (r"\bA\s*I\b", "AI"),
    (r"\bM\s*L\b", "ML"),
    (r"\bN\s*L\s*P\b", "NLP"),
    (r"\bL\s*L\s*M\b", "LLM"),
    (r"\bA\s*P\s*I\b", "API"),
    (r"\bS\s*D\s*K\b", "SDK"),
    (r"\bC\s*P\s*U\b", "CPU"),
    (r"\bG\s*P\s*U\b", "GPU"),
    (r"\bN\s*P\s*U\b", "NPU"),
    (r"\bR\s*O\s*I\b", "ROI"),
    (r"\bK\s*P\s*I\b", "KPI"),
    (r"\bS\s*L\s*A\b", "SLA"),
    (r"\bR\s*A\s*G\b", "RAG"),
]


HEADER_FOOTER_PATTERNS: list[str] = [
    r"^page\s+\d+\s*(?:/|sur|of|von|di)\s*\d+$",
    r"^\d+\s*(?:/|sur|of)\s*\d+$",
    r"^-\s*\d+\s*-$",
    r"^\d+$",
    r"^(?:confidentiel|confidential|draft|brouillon|vertraulich|riservato)$",
    r"^(?:ennosmart|internal use only|usage interne|für den internen gebrauch)$",
    r"^©.{0,80}$",
    r"^(?:tous droits réservés|all rights reserved|alle rechte vorbehalten)$",
    r"^(?:propriété intellectuelle|intellectual property).*$",
    r"^(?:version|rev\.?|révision)\s*:?\s*[\d.]+$",
    r"^(?:date|date de création|created|erstellt)\s*:?\s*[\d/\-\.]+$",
    r"^(?:auteur|author|verfasser)\s*:?\s*.{0,50}$",
    r"^(?:document\s+(?:interne|confidentiel|officiel))$",
]

_HEADER_FOOTER_RE = re.compile(
    "|".join(f"(?:{p})" for p in HEADER_FOOTER_PATTERNS),
    flags=re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════════════
# BLOCS À PROTÉGER
# ══════════════════════════════════════════════════════════════════════════════

_VISION_BLOCK_RE = re.compile(
    r"(\[IMAGE\s*\|[^\]]+\]\n\[QUALIT[ÉE]:[^\]]+\](?:\n\[L[ÉE]GENDE[^\]]*\])?\n\n.*?)(?=\[IMAGE\s*\||$)",
    re.DOTALL | re.IGNORECASE,
)

_MARKDOWN_TABLE_RE = re.compile(
    r"(\|.+\|[ \t]*\n(?:\|[-: ]+\|[ \t]*\n)(?:\|.+\|[ \t]*\n?)+)",
    re.MULTILINE,
)

_IMAGES_BLOCK_RE = re.compile(
    r"\[IMAGES\]\n(?:\s*[-•].*\n?)+",
    re.MULTILINE | re.IGNORECASE,
)


# Important :
# On ne protège plus tout le bloc [FORMULES] avant nettoyage,
# sinon LaTeX/Domaine/Confiance restent visibles pour le NER.
# On garde seulement l'explication utile via _strip_structural_blocks().
_FORMULA_BLOCK_RE = re.compile(
    r"\[FORMULES?[^\]]*\]\n(?:\s*\d+\..*\n?|\s+.*\n?)+",
    re.MULTILINE | re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════════════
# PATTERNS TECHNIQUES / OCR
# ══════════════════════════════════════════════════════════════════════════════

_NUMERIC_VALUE_RE = re.compile(
    r"\d[\d\s]*(?:[.,]\d+)?\s*"
    r"(?:€|\$|£|¥|%|ms|µs|ns|GHz|MHz|kHz|Hz|"
    r"Go|Mo|Ko|TB|GB|MB|KB|"
    r"km|m|cm|mm|µm|nm|"
    r"kg|g|mg|µg|"
    r"kW|MW|W|V|A|Ω|"
    r"°C|°F|K|"
    r"ETP|j/h|h/j|j\.h)"
)

_REF_CODE_RE = re.compile(
    r"(?:ANR|BPI|CIR|CII|H2020|FUI|ADEME|CIFRE)-[\w\d\-]+",
    re.IGNORECASE,
)

_WORD_BREAK_RE = re.compile(r"(\w)-\n(\w)")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REPEATED_CHARS_RE = re.compile(r"(.)\1{4,}")
_SEPARATOR_LINE_RE = re.compile(r"^[\-=_*#~]{3,}$")
_MIME_QP_RE = re.compile(r"=[0-9A-Fa-f]{2}")

_PAGE_NUMBER_INLINE_RE = re.compile(
    r"(?<!\[)(?<!\w)(?:page|p\.?)\s*\d{1,4}(?!\w)(?!\])",
    flags=re.IGNORECASE,
)

_EMAIL_SIGNATURE_RE = re.compile(
    r"(?:^--\s*$.*?(?=\n\n|\Z))|"
    r"(?:cordialement|bien cordialement|sincèrement|best regards|regards)"
    r"[\s\S]{0,300}$",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)


# ══════════════════════════════════════════════════════════════════════════════
# BLOCS STRUCTURELS EXTRACTION → À NETTOYER AVANT NER
# ══════════════════════════════════════════════════════════════════════════════

_SLIDE_HEADER_RE = re.compile(
    r"^\s*\[SLIDE\s+\d+(?:\s*:\s*(.*?))?\]\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_PAGE_HEADER_RE = re.compile(
    r"^\s*\[PAGE\s+\d+(?:\s*:\s*(.*?))?\]\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_SECTION_HEADER_RE = re.compile(
    r"^\s*\[SECTION\s*:\s*(.*?)\]\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_NOTES_BLOCK_RE = re.compile(
    r"\[NOTES?\s+PR[ÉE]SENTATEUR\].*?(?=\n\s*\[(?:SLIDE|PAGE|SECTION|FORMULES?|IMAGES|TABLEAU)|\Z)",
    re.DOTALL | re.IGNORECASE,
)

_FORMULA_HEADER_RE = re.compile(
    r"^\s*\[FORMULES?[^\]]*\]\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_STRUCTURAL_LINE_RE = re.compile(
    r"^\s*\[(?:"
    r"IMAGES?|TABLEAU|DONN[ÉE]ES TABULAIRES|QUALIT[ÉE][^\]]*|"
    r"NOTES?|SLIDE|PAGE"
    r")[^\]]*\]\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_FORMULA_META_RE = re.compile(
    r"^\s*(?:\d+\.\s*)?LaTeX:\s*.*$|"
    r"^\s*Domaine:\s*.*$|"
    r"^\s*Confiance:\s*.*$|"
    r"^\s*Quality:\s*.*$|"
    r"^\s*Backend:\s*.*$|"
    r"^\s*Source:\s*.*$",
    re.IGNORECASE | re.MULTILINE,
)

_FORMULA_EXPLANATION_RE = re.compile(
    r"^\s*Explication:\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)

_MULTI_BLANK_RE = re.compile(r"\n{3,}")


STRUCTURAL_ONLY_LINES = {
    "slide",
    "slides",
    "note",
    "notes",
    "notes présentateur",
    "notes presentateur",
    "formule",
    "formules",
    "formules détectées",
    "formules detectees",
    "formules omml",
    "latex",
    "domaine",
    "confiance",
    "explication",
    "image",
    "images",
    "tableau",
    "tableaux",
    "qualité",
    "qualite",
    "présentation",
    "presentation",
    "soutenance",
    "sommaire",
    "conclusion",
    "objectif",
    "objectifs",
    "méthodologie",
    "methodologie",
    "résultat",
    "resultat",
    "résultats",
    "resultats",
}


def _strip_structural_blocks(text: str, doc_type: str = "unknown") -> tuple[str, bool]:
    """
    Nettoie les marqueurs structurels injectés par extraction/ avant le NER.

    Règles :
      - [SLIDE N : titre]    → titre seul
      - [PAGE N : titre]     → titre seul
      - [SECTION : titre]    → titre seul
      - [NOTES PRÉSENTATEUR] → supprimé pour PPTX
      - [FORMULES]           → header supprimé
      - LaTeX/Domaine/Confiance supprimés
      - Explication conservée comme phrase utile
    """
    original = text

    if not text:
        return text, False

    doc_type = (doc_type or "unknown").lower()

    # 1. Garder seulement le titre des slides.
    text = _SLIDE_HEADER_RE.sub(
        lambda m: (m.group(1) or "").strip(),
        text,
    )

    # 2. Garder seulement le titre des pages si présent.
    text = _PAGE_HEADER_RE.sub(
        lambda m: (m.group(1) or "").strip(),
        text,
    )

    # 3. Garder seulement le titre des sections.
    text = _SECTION_HEADER_RE.sub(
        lambda m: (m.group(1) or "").strip(),
        text,
    )

    # 4. Supprimer les notes présentateur pour les PPTX.
    if doc_type == "pptx":
        text = _NOTES_BLOCK_RE.sub("", text)

    # 5. Supprimer les headers structurels.
    text = _FORMULA_HEADER_RE.sub("", text)
    text = _STRUCTURAL_LINE_RE.sub("", text)

    # 6. Supprimer les métadonnées des formules.
    text = _FORMULA_META_RE.sub("", text)

    # 7. Garder l'explication, mais enlever le mot "Explication".
    text = _FORMULA_EXPLANATION_RE.sub(
        lambda m: f"Description technique : {m.group(1).strip()}",
        text,
    )

    # 8. Supprimer les lignes structurelles seules.
    kept_lines: list[str] = []

    for line in text.splitlines():
        clean = line.strip().lower().strip(":").strip()

        if clean in STRUCTURAL_ONLY_LINES:
            continue

        if clean.startswith("formuladomain."):
            continue

        kept_lines.append(line)

    text = "\n".join(kept_lines)

    # 9. Nettoyage des blancs.
    text = _MULTI_BLANK_RE.sub("\n\n", text).strip()

    return text, text != original


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CleanedChunk:
    original: str
    cleaned: str
    char_count_before: int
    char_count_after: int
    transformations_applied: list[str]
    is_empty: bool
    source_type: str = "text"


@dataclass
class CleanerResult:
    chunks: list[CleanedChunk]
    clean_chunks: list[str]
    total_chars_removed: int
    transformations_summary: dict[str, int]
    empty_chunks_removed: int


# ══════════════════════════════════════════════════════════════════════════════
# PROTECTION BLOCS
# ══════════════════════════════════════════════════════════════════════════════

def _protect_blocks(text: str) -> tuple[str, dict[str, str]]:
    """
    Protège les blocs qui doivent rester lisibles :
      - vision orpheline
      - images intégrées
      - tableaux Markdown

    Les blocs [FORMULES] ne sont pas protégés volontairement,
    car leurs métadonnées doivent être nettoyées avant le NER.
    """
    protected: dict[str, str] = {}
    counter = [0]

    def _replace(match: re.Match, prefix: str) -> str:
        token = f"__PROTECT_{prefix}_{counter[0]}__"
        protected[token] = match.group(0)
        counter[0] += 1
        return token

    text = _IMAGES_BLOCK_RE.sub(lambda m: _replace(m, "IMAGES"), text)
    text = _VISION_BLOCK_RE.sub(lambda m: _replace(m, "VISION"), text)
    text = _MARKDOWN_TABLE_RE.sub(lambda m: _replace(m, "TABLE"), text)

    return text, protected


def _restore_blocks(text: str, protected: dict[str, str]) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


def _detect_chunk_type(text: str) -> str:
    if text.strip().startswith("[IMAGE |"):
        return "vision"
    if re.search(r"^\|.+\|", text, re.MULTILINE):
        return "table"
    return "text"


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPES DE NETTOYAGE
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_unicode(text: str) -> tuple[str, bool]:
    normalized = unicodedata.normalize("NFC", text)

    for ligature, replacement in LIGATURE_MAP.items():
        normalized = normalized.replace(ligature, replacement)

    for char, replacement in UNICODE_BULLETS.items():
        normalized = normalized.replace(char, replacement)

    if _MIME_QP_RE.search(normalized):
        normalized = _MIME_QP_RE.sub(" ", normalized)

    return normalized, normalized != text


def _remove_control_characters(text: str) -> tuple[str, bool]:
    cleaned = _CONTROL_CHARS_RE.sub("", text)
    return cleaned, cleaned != text


def _fix_word_breaks(text: str) -> tuple[str, bool]:
    cleaned = _WORD_BREAK_RE.sub(r"\1\2", text)
    return cleaned, cleaned != text


def _fix_spaced_acronyms(text: str) -> tuple[str, bool]:
    cleaned = text

    for pattern, replacement in ACRONYM_FIXES:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    return cleaned, cleaned != text


def _fix_ocr_artifacts(text: str) -> tuple[str, bool]:
    cleaned = text

    cleaned = re.sub(r"(?<=[A-Z])0(?=[A-Z])", "O", cleaned)
    cleaned = re.sub(r"(?<=[a-z])0(?=[a-z])", "o", cleaned)
    cleaned = re.sub(r"\bl(\d)", r"1\1", cleaned)

    cleaned = re.sub(
        r"(?<!\d)(\d) (\d) (\d)(?: (\d))?(?: (\d))?(?!\d)",
        lambda m: "".join(d for d in m.groups() if d),
        cleaned,
    )

    return cleaned, cleaned != text


def _remove_headers_footers(text: str) -> tuple[str, bool]:
    lines = text.split("\n")
    filtered: list[str] = []
    removed = 0

    for line in lines:
        stripped = line.strip()

        if stripped and _HEADER_FOOTER_RE.match(stripped):
            removed += 1
            continue

        if _SEPARATOR_LINE_RE.match(stripped):
            removed += 1
            continue

        filtered.append(line)

    result = "\n".join(filtered)
    return result, removed > 0


def _remove_email_signatures(text: str) -> tuple[str, bool]:
    cleaned = _EMAIL_SIGNATURE_RE.sub("", text)
    return cleaned, cleaned != text


def _remove_repeated_lines(text: str) -> tuple[str, bool]:
    lines = text.split("\n")
    line_counts: dict[str, int] = {}
    filtered: list[str] = []
    removed = 0

    for line in lines:
        key = line.strip().lower()

        if not key:
            filtered.append(line)
            continue

        line_counts[key] = line_counts.get(key, 0) + 1

        if line_counts[key] <= MAX_LINE_REPETITIONS:
            filtered.append(line)
        else:
            removed += 1

    result = "\n".join(filtered)
    return result, removed > 0


def _remove_short_lines(text: str) -> tuple[str, bool]:
    lines = text.split("\n")
    filtered: list[str] = []
    removed = 0

    for line in lines:
        stripped = line.strip()

        if not stripped or len(stripped) >= MIN_LINE_LENGTH:
            filtered.append(line)
        else:
            removed += 1

    result = "\n".join(filtered)
    return result, removed > 0


def _fix_repeated_chars(text: str) -> tuple[str, bool]:
    def _replace(match: re.Match) -> str:
        char = match.group(1)

        if char in ("-", "|", "=", "_", "*"):
            return match.group(0)

        if char == ".":
            return "..."

        return char * 2

    cleaned = _REPEATED_CHARS_RE.sub(_replace, text)
    return cleaned, cleaned != text


def _remove_page_markers(text: str) -> tuple[str, bool]:
    cleaned = _PAGE_NUMBER_INLINE_RE.sub("", text)
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned, cleaned != text


def _normalize_whitespace(text: str) -> tuple[str, bool]:
    original = text

    text = text.replace("\t", " ")
    text = re.sub(r" {2,}", " ", text)

    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" ([.,])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)

    return text.strip(), text != original


def _preserve_numeric_formatting(text: str) -> tuple[str, bool]:
    cleaned = text

    def _normalize_amount(m: re.Match) -> str:
        num_str = m.group(1).replace(".", " ").replace(",", " ")
        num_str = re.sub(r"\s+", " ", num_str).strip()
        return num_str + m.group(2)

    cleaned = re.sub(
        r"([\d][.\d,]+\d)\s*(€|\$|£|¥|EUR|USD)",
        _normalize_amount,
        cleaned,
    )

    return cleaned, cleaned != text


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINES
# ══════════════════════════════════════════════════════════════════════════════

_TEXT_CLEANING_STEPS = [
    ("unicode", _normalize_unicode),
    ("control_chars", _remove_control_characters),
    ("word_breaks", _fix_word_breaks),
    ("spaced_acronyms", _fix_spaced_acronyms),
    ("ocr_artifacts", _fix_ocr_artifacts),
    ("email_signatures", _remove_email_signatures),
    ("headers_footers", _remove_headers_footers),
    ("repeated_lines", _remove_repeated_lines),
    ("repeated_chars", _fix_repeated_chars),
    ("page_markers", _remove_page_markers),
    ("numeric_formatting", _preserve_numeric_formatting),
    ("short_lines", _remove_short_lines),
    ("whitespace", _normalize_whitespace),
]

_VISION_CLEANING_STEPS = [
    ("unicode", _normalize_unicode),
    ("control_chars", _remove_control_characters),
    ("repeated_chars", _fix_repeated_chars),
    ("whitespace", _normalize_whitespace),
]

_TABLE_CLEANING_STEPS = [
    ("unicode", _normalize_unicode),
    ("control_chars", _remove_control_characters),
    ("whitespace", _normalize_whitespace),
]


# ══════════════════════════════════════════════════════════════════════════════
# POINTS D’ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def clean_chunk(text: str, doc_type: str = "unknown") -> CleanedChunk:
    """
    Nettoie un chunk de texte brut issu du module extraction/.

    doc_type :
      "pptx" | "docx" | "pdf" | "email" | "excel" | "image" | "unknown"

    Important :
      L'étape structural_blocks est appliquée AVANT le NER.
    """
    if not text or not text.strip():
        return CleanedChunk(
            original=text,
            cleaned="",
            char_count_before=len(text or ""),
            char_count_after=0,
            transformations_applied=[],
            is_empty=True,
            source_type="text",
        )

    original_input = text
    applied: list[str] = []

    # Étape 0 : enlever les marqueurs structurels AVANT toute protection.
    text, was_stripped = _strip_structural_blocks(text, doc_type=doc_type)

    if was_stripped:
        applied.append("structural_blocks")

    source_type = _detect_chunk_type(text)

    if source_type == "vision":
        steps = _VISION_CLEANING_STEPS
    elif source_type == "table":
        steps = _TABLE_CLEANING_STEPS
    else:
        steps = _TEXT_CLEANING_STEPS

    current, protected = _protect_blocks(text)

    for step_name, step_fn in steps:
        try:
            result, was_modified = step_fn(current)

            if was_modified:
                applied.append(step_name)
                current = result

        except Exception as exc:
            logger.warning("Étape nettoyage '%s' échouée : %s", step_name, exc)

    current = _restore_blocks(current, protected)
    current = current.strip()

    is_empty = not bool(current)

    return CleanedChunk(
        original=original_input,
        cleaned=current,
        char_count_before=len(original_input),
        char_count_after=len(current),
        transformations_applied=applied,
        is_empty=is_empty,
        source_type=source_type,
    )


def clean_chunks(chunks: list[str], doc_type: str = "unknown") -> CleanerResult:
    """
    Nettoie une liste de chunks issus d'un ExtractionResult.
    """
    transformations_summary: dict[str, int] = {}
    total_chars_removed = 0
    empty_removed = 0
    cleaned_chunks: list[CleanedChunk] = []

    for chunk in chunks:
        cc = clean_chunk(chunk, doc_type=doc_type)
        cleaned_chunks.append(cc)

        total_chars_removed += cc.char_count_before - cc.char_count_after

        for t in cc.transformations_applied:
            transformations_summary[t] = transformations_summary.get(t, 0) + 1

        if cc.is_empty:
            empty_removed += 1

    clean_texts = [cc.cleaned for cc in cleaned_chunks if not cc.is_empty]

    if empty_removed:
        logger.info("%d chunk(s) vide(s) supprimé(s) après nettoyage", empty_removed)

    vision_count = sum(1 for cc in cleaned_chunks if cc.source_type == "vision")
    table_count = sum(1 for cc in cleaned_chunks if cc.source_type == "table")
    text_count = sum(1 for cc in cleaned_chunks if cc.source_type == "text")

    logger.info(
        "Nettoyage : %d→%d chunks [texte=%d vision=%d tableaux=%d] | "
        "%d chars supprimés | transformations=%s",
        len(chunks),
        len(clean_texts),
        text_count,
        vision_count,
        table_count,
        total_chars_removed,
        transformations_summary,
    )

    return CleanerResult(
        chunks=cleaned_chunks,
        clean_chunks=clean_texts,
        total_chars_removed=total_chars_removed,
        transformations_summary=transformations_summary,
        empty_chunks_removed=empty_removed,
    )


# ══════════════════════════════════════════════════════════════════════════════
# DEBUG LOCAL
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)

    test_chunks = [
        "[SLIDE 1 : TECHNOLOGIE THERMIQUE]\n[NOTES PRÉSENTATEUR]\nCeci est une note orale à supprimer.\nLe projet porte sur la transmission thermique.",
        "[FORMULES DÉTECTÉES]\n  1. LaTeX: E\n     Explication: Module d'Young associé à un matériau.\n     Domaine: FormulaDomain.MECANIQUE\n     Confiance: 100.00%",
        "R  &  D et C I R sont au cœur de la straté-\ngie de l'entreprise.",
        "Page 3 / 45\nLes travaux de recherche portent sur l'IA.\nPage 4 / 45",
        "ﬁltrage des données\tpar les algorithmes\x00 de machine learning.",
        "Montant CIR : 150 000 € ( dont 30 000 € de sous-traitance )",
        "Budget total : 1.500.000 EUR sur 3 ans",
        "Le pr0cessus d'apprentissage automatique utilise l50 000 exemples.",
        "[IMAGE | schema_technique | PAGE 3]\n[QUALITÉ: full | GPU-Intel | Qwen2-VL-7B-Instruct]\n\nSchéma montrant un système de mesure.",
        "| Année | Budget R&D | ETP |\n|-------|-----------|-----|\n| 2023  | 500 000 € |  5  |",
        "---",
        "",
        "The A I model was trained using G P U acceleration on 1.5M samples.",
        "Please find the R&D report attached.\n\n--\nJohn Doe\nEnnoSmart\njohn@ennosmart.com",
    ]

    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            test_chunks = [f.read()]

    result = clean_chunks(test_chunks, doc_type="pptx")

    print(f"\n{'═' * 60}")
    print(f"Chunks : {len(test_chunks)} → {len(result.clean_chunks)}")
    print(f"Chars supprimés : {result.total_chars_removed}")
    print(f"Transformations : {result.transformations_summary}")
    print(f"Vides supprimés : {result.empty_chunks_removed}")
    print(f"{'─' * 60}")

    for i, cc in enumerate(result.chunks):
        if cc.is_empty:
            print(f"\n[CHUNK {i + 1}] ({cc.source_type}) → SUPPRIMÉ")
            continue

        print(f"\n[CHUNK {i + 1}] ({cc.source_type})")
        print(f"Transformations : {cc.transformations_applied}")
        print(f"Avant : {cc.original[:200]!r}")
        print(f"Après : {cc.cleaned[:200]!r}")

    print(f"\n{'═' * 60}")