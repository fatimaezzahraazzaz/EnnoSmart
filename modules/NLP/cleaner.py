"""
modules/NLP/cleaner.py — VERSION EVIDENCE-FIRST (universelle)
──────────────────────────────────────────────────────────────────────────────
Nettoyage du texte brut extrait pour dossiers R&D / CIR multi-domaines.

Nouvelle architecture :
  extraction → cleaner.py → normalizer.py → segmenter.py
  → evidence_mapper.py → aggregator.py → domain_classifier.py → synthesizer.py

Rôle du cleaner :
  - Nettoyer les chunks issus de l'extraction.
  - Préserver au maximum les phrases métiers utiles.
  - Supprimer uniquement les artefacts techniques d'extraction :
      [SLIDE], [PAGE], [SECTION], [IMAGES], [FORMULES], LaTeX, Domaine,
      Confiance, Qualité, etc.
  - Ne PAS faire de classification métier.
  - Ne PAS normaliser de vocabulaire technique spécifique à un domaine.
  - Ne PAS supprimer les titres courts utiles : Objectifs, Verrous, Résultats...

Version : 3.0.0
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MIN_LINE_LENGTH = 2
MAX_LINE_REPETITIONS = 3

LIGATURE_MAP: dict[str, str] = {
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
    "œ": "oe", "æ": "ae", "Œ": "Oe", "Æ": "Ae",
}

UNICODE_REPLACEMENTS: dict[str, str] = {
    "\u2022": "-", "\u2023": "-", "\u25cf": "-", "\u25e6": "-",
    "\u2043": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u00b7": "-", "\u2026": "...",
    "\u00a0": " ", "\u202f": " ", "\u2009": " ",
    "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "",
    "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
    "\u00ab": '"', "\u00bb": '"',
}

MOJIBAKE_FIXES: dict[str, str] = {
    "Ã©": "é", "Ã¨": "è", "Ãª": "ê", "Ã«": "ë",
    "Ã ": "à", "Ã¢": "â", "Ã¤": "ä",
    "Ã®": "î", "Ã¯": "ï", "Ã´": "ô", "Ã¶": "ö",
    "Ã¹": "ù", "Ã»": "û", "Ã¼": "ü",
    "Ã§": "ç", "Å“": "œ", "Å’": "Œ",
    "â€™": "'", "â€˜": "'", "â€œ": '"', "â€": '"',
    "â€“": "-", "â€”": "-", "â€¦": "...",
    "Â°": "°", "Âµ": "µ", "Â": "",
}

# Acronymes universels seulement. Pas de vocabulaire métier spécifique.
ACRONYM_FIXES: list[tuple[str, str]] = [
    (r"\bR\s*&\s*D\b", "R&D"),
    (r"\bR\s*et\s*D\b", "R&D"),
    (r"\bR\s*-\s*D\b", "R&D"),
    (r"\bC\s*I\s*R\b", "CIR"),
    (r"\bC\s*I\s*I\b", "CII"),
    (r"\bE\s*T\s*P\b", "ETP"),
    (r"\bT\s*R\s*L\b", "TRL"),
    (r"\bA\s*N\s*R\b", "ANR"),
    (r"\bA\s*N\s*R\s*T\b", "ANRT"),
    (r"\bB\s*P\s*I\b", "BPI"),
    (r"\bI\s*N\s*P\s*I\b", "INPI"),
    (r"\bC\s*N\s*R\s*S\b", "CNRS"),
    (r"\bC\s*E\s*A\b", "CEA"),
    (r"\bI\s*N\s*R\s*I\s*A\b", "INRIA"),
    (r"\bP\s*M\s*E\b", "PME"),
    (r"\bE\s*T\s*I\b", "ETI"),
    (r"\bA\s*I\b", "AI"),
    (r"\bI\s*A\b", "IA"),
    (r"\bM\s*L\b", "ML"),
    (r"\bN\s*L\s*P\b", "NLP"),
    (r"\bL\s*L\s*M\b", "LLM"),
    (r"\bA\s*P\s*I\b", "API"),
    (r"\bS\s*D\s*K\b", "SDK"),
    (r"\bC\s*P\s*U\b", "CPU"),
    (r"\bG\s*P\s*U\b", "GPU"),
]

HEADER_FOOTER_PATTERNS: list[str] = [
    r"^page\s+\d+\s*(?:/|sur|of)\s*\d+$",
    r"^\d+\s*(?:/|sur|of)\s*\d+$",
    r"^-\s*\d+\s*-$",
    r"^\d+$",
    r"^(?:confidentiel|confidential|draft|brouillon|vertraulich|riservato)$",
    r"^(?:internal use only|usage interne)$",
    r"^©.{0,100}$",
    r"^(?:tous droits réservés|all rights reserved)$",
    r"^(?:version|rev\.?|révision)\s*:?\s*[\d.]+$",
    r"^(?:document\s+(?:interne|confidentiel|officiel))$",
]
_HEADER_FOOTER_RE = re.compile("|".join(f"(?:{p})" for p in HEADER_FOOTER_PATTERNS), re.I | re.U)

_MARKDOWN_TABLE_RE = re.compile(
    r"(\|.+\|[ \t]*\n(?:\|[-: ]+\|[ \t]*\n)(?:\|.+\|[ \t]*\n?)+)",
    re.MULTILINE,
)

_VISION_BLOCK_HEADER_RE = re.compile(
    r"^\s*\[IMAGE\s*\|[^\]]+\]\s*$|"
    r"^\s*\[QUALIT[ÉE]:[^\]]+\]\s*$|"
    r"^\s*\[L[ÉE]GENDE[^\]]*\]\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_IMAGES_HEADER_RE = re.compile(r"^\s*\[IMAGES?\]\s*$", re.MULTILINE | re.I)

_FORMULA_HEADER_RE = re.compile(r"^\s*\[FORMULES?[^\]]*\]\s*$", re.MULTILINE | re.I)

_FORMULA_META_RE = re.compile(
    r"^\s*(?:\d+\.\s*)?LaTeX:\s*.*$|"
    r"^\s*(?:Domaine|Confiance|Quality|Backend|Source|QUALIT[ÉE]|Note):\s*.*$",
    re.IGNORECASE | re.MULTILINE,
)

_FORMULA_EXPLANATION_RE = re.compile(
    r"^\s*(?:Explication|Description technique)\s*:\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)

_SLIDE_HEADER_RE = re.compile(r"^\s*\[SLIDE\s+\d+(?:\s*:\s*(.*?))?\]\s*$", re.M | re.I)
_PAGE_HEADER_RE = re.compile(r"^\s*\[PAGE\s+\d+(?:\s*:\s*(.*?))?\]\s*$", re.M | re.I)
_SECTION_HEADER_RE = re.compile(r"^\s*\[SECTION\s*:\s*(.*?)\]\s*$", re.M | re.I)

_NOTES_BLOCK_RE = re.compile(
    r"\[NOTES?\s+PR[ÉE]SENTATEUR\].*?(?=\n\s*\[(?:SLIDE|PAGE|SECTION|FORMULES?|IMAGES?|TABLEAU)|\Z)",
    re.DOTALL | re.IGNORECASE,
)

_STRUCTURAL_LINE_RE = re.compile(
    r"^\s*\[(?:TABLEAU|DONN[ÉE]ES TABULAIRES|QUALIT[ÉE][^\]]*|NOTES?|SLIDE|PAGE)[^\]]*\]\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_MULTI_BLANK_RE = re.compile(r"\n{3,}")

USEFUL_SECTION_TITLE_RE = re.compile(
    r"^\s*(?:"
    r"objectifs?|objectifs? vis[ée]s?|performances? [àa] atteindre|"
    r"contexte|probl[ée]matique|"
    r"[ée]tat de l['’ ]art|bibliographie|"
    r"verrous?(?: technologiques?| techniques?)?|incertitudes?|limitations?|"
    r"m[ée]thodologie|d[ée]marche(?: scientifique| exp[ée]rimentale)?|"
    r"travaux(?: r&d| de r&d)?|essais?|tests?|validation|"
    r"r[ée]sultats?|conclusion|perspectives?|annexes?"
    r")\s*:?[ \t]*$",
    re.IGNORECASE | re.UNICODE,
)

STRUCTURAL_ONLY_LINES = {
    "slide", "slides", "note", "notes", "notes présentateur", "notes presentateur",
    "formule", "formules", "formules détectées", "formules detectees",
    "formules omml", "latex", "domaine", "confiance", "explication",
    "image", "images", "tableau", "tableaux", "qualité", "qualite",
    "présentation", "presentation", "soutenance",
}

_WORD_BREAK_RE = re.compile(r"(\w)-\n(\w)")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REPEATED_CHARS_RE = re.compile(r"(.)\1{4,}")
_SEPARATOR_LINE_RE = re.compile(r"^[\-=_*#~]{3,}$")
_MIME_QP_RE = re.compile(r"=[0-9A-Fa-f]{2}")

_PAGE_NUMBER_INLINE_RE = re.compile(r"(?<!\[)(?<!\w)(?:page|p\.?)\s*\d{1,4}(?!\w)(?!\])", re.I)

_EMAIL_SIGNATURE_RE = re.compile(
    r"(?:\n--\s*\n[\s\S]{0,400}$)|"
    r"(?:\n(?:cordialement|bien cordialement|sincèrement|best regards|regards)\s*,?\s*\n[\s\S]{0,300}$)",
    re.IGNORECASE | re.DOTALL,
)


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


def _strip_structural_blocks(text: str, doc_type: str = "unknown") -> tuple[str, bool]:
    original = text
    if not text:
        return text, False

    doc_type = (doc_type or "unknown").lower()

    text = _SLIDE_HEADER_RE.sub(lambda m: (m.group(1) or "").strip(), text)
    text = _PAGE_HEADER_RE.sub(lambda m: (m.group(1) or "").strip(), text)
    text = _SECTION_HEADER_RE.sub(lambda m: (m.group(1) or "").strip(), text)

    if doc_type == "pptx":
        text = _NOTES_BLOCK_RE.sub("", text)

    text = _VISION_BLOCK_HEADER_RE.sub("", text)
    text = _IMAGES_HEADER_RE.sub("", text)
    text = _FORMULA_HEADER_RE.sub("", text)
    text = _FORMULA_META_RE.sub("", text)
    text = _FORMULA_EXPLANATION_RE.sub(lambda m: m.group(1).strip(), text)
    text = _STRUCTURAL_LINE_RE.sub("", text)

    kept_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        clean = stripped.lower().strip(":").strip()

        if not stripped:
            kept_lines.append(line)
            continue

        if clean in STRUCTURAL_ONLY_LINES and not USEFUL_SECTION_TITLE_RE.match(line):
            continue

        if clean.startswith("formuladomain."):
            continue

        kept_lines.append(line)

    text = "\n".join(kept_lines)
    text = _MULTI_BLANK_RE.sub("\n\n", text).strip()
    return text, text != original


def _protect_blocks(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}
    counter = 0

    def replace_table(match: re.Match) -> str:
        nonlocal counter
        token = f"__PROTECT_TABLE_{counter}__"
        protected[token] = match.group(0)
        counter += 1
        return token

    text = _MARKDOWN_TABLE_RE.sub(replace_table, text)
    return text, protected


def _restore_blocks(text: str, protected: dict[str, str]) -> str:
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


def _detect_chunk_type(text: str) -> str:
    return "table" if re.search(r"^\|.+\|", text, re.MULTILINE) else "text"


def _normalize_unicode(text: str) -> tuple[str, bool]:
    normalized = unicodedata.normalize("NFC", text)

    for ligature, replacement in LIGATURE_MAP.items():
        normalized = normalized.replace(ligature, replacement)

    for char, replacement in UNICODE_REPLACEMENTS.items():
        normalized = normalized.replace(char, replacement)

    for bad, good in MOJIBAKE_FIXES.items():
        normalized = normalized.replace(bad, good)

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
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE | re.UNICODE)
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

        if USEFUL_SECTION_TITLE_RE.match(stripped):
            filtered.append(line)
            continue

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

        if USEFUL_SECTION_TITLE_RE.match(line):
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

        if not stripped:
            filtered.append(line)
            continue

        if USEFUL_SECTION_TITLE_RE.match(stripped):
            filtered.append(line)
            continue

        if len(stripped) >= MIN_LINE_LENGTH:
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


def _preserve_numeric_formatting(text: str) -> tuple[str, bool]:
    cleaned = text

    def _normalize_amount(m: re.Match) -> str:
        num_str = m.group(1).replace(".", " ").replace(",", " ")
        num_str = re.sub(r"\s+", " ", num_str).strip()
        return num_str + " " + m.group(2)

    cleaned = re.sub(r"([\d][.\d,]+\d)\s*(€|\$|£|¥|EUR|USD)", _normalize_amount, cleaned)
    return cleaned, cleaned != text


def _normalize_whitespace(text: str) -> tuple[str, bool]:
    original = text
    text = text.replace("\t", " ")
    text = re.sub(r" {2,}", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" ([.,;:!?])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return text.strip(), text != original


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

_TABLE_CLEANING_STEPS = [
    ("unicode", _normalize_unicode),
    ("control_chars", _remove_control_characters),
    ("whitespace", _normalize_whitespace),
]


def clean_chunk(text: str, doc_type: str = "unknown") -> CleanedChunk:
    """
    Nettoie un chunk de texte brut issu du module extraction/.

    doc_type :
      "pptx" | "docx" | "pdf" | "email" | "excel" | "image" | "unknown"
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

    text, was_stripped = _strip_structural_blocks(text, doc_type=doc_type)
    if was_stripped:
        applied.append("structural_blocks")

    source_type = _detect_chunk_type(text)
    steps = _TABLE_CLEANING_STEPS if source_type == "table" else _TEXT_CLEANING_STEPS

    current, protected = _protect_blocks(text)

    for step_name, step_fn in steps:
        try:
            result, was_modified = step_fn(current)
            if was_modified:
                applied.append(step_name)
                current = result
        except Exception as exc:
            logger.warning("Étape nettoyage '%s' échouée : %s", step_name, exc)

    current = _restore_blocks(current, protected).strip()
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
    transformations_summary: dict[str, int] = {}
    total_chars_removed = 0
    empty_removed = 0
    cleaned_chunks: list[CleanedChunk] = []

    for chunk in chunks or []:
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

    table_count = sum(1 for cc in cleaned_chunks if cc.source_type == "table")
    text_count = sum(1 for cc in cleaned_chunks if cc.source_type == "text")

    logger.info(
        "Nettoyage : %d→%d chunks [texte=%d tableaux=%d] | %d chars supprimés | transformations=%s",
        len(chunks or []),
        len(clean_texts),
        text_count,
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


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)

    test_chunks = [
        "[SLIDE 1 : TECHNOLOGIE THERMIQUE]\n[NOTES PRÉSENTATEUR]\nCeci est une note orale à supprimer.\nLe projet porte sur la transmission thermique.",
        "[FORMULES DÉTECTÉES]\n  1. LaTeX: E\n     Explication: Module d'Young associé à un matériau.\n     Domaine: FormulaDomain.MECANIQUE\n     Confiance: 100.00%",
        "R  &  D et C I R sont au cœur de la straté-\ngie de l'entreprise.",
        "Page 3 / 45\nLes travaux de recherche portent sur l'emballage médical.\nPage 4 / 45",
        "Objectifs\nDévelopper un système d'emballage recyclable.",
        "Verrous\nOn n'arrive pas à garantir simultanément les chocs et la recyclabilité.",
        "| Solution | Chocs | Abrasion |\n|----------|-------|----------|\n| TPU      | ++    | ++       |",
        "",
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
