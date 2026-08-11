"""
modules/extraction/text/pdf_native.py
──────────────────────────────────────────────────────────────────────────────
Extraction native de PDF texte pour dossiers R&D / CIR.

Stratégie :
  - Utilise pdfplumber pour extraire texte + tableaux avec positions (BBox)
  - Détecte automatiquement si une page est "vide" (→ signale besoin OCR)
  - Extrait les tableaux et les convertit en texte structuré lisible pour le RAG
  - Extrait les métadonnées du document (auteur, date)
  - Retourne un ExtractionResult standardisé

"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber
from pdfplumber.page import Page

from modules.extraction.text.pdf_reading_order import extract_page_reading_order

# ── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# ── Constantes R&D ────────────────────────────────────────────────────────────

# Seuil minimum de caractères pour considérer une page comme "texte natif"
MIN_CHARS_PER_PAGE = 30

# Types de sections fréquentes dans les CIR / dossiers R&D
RD_SECTION_PATTERNS: list[str] = [
    r"(objectif[s]?\s+(?:du\s+)?projet)",
    r"(état\s+de\s+l['\s]art)",
    r"(verrous?\s+technologique[s]?)",
    r"(travaux\s+(?:de\s+)?r(?:echerche)?(?:\s*&\s*|\s+et\s+)d(?:éveloppement)?)",
    r"(résultats?\s+(?:obtenus?|attendus?))",
    r"(description\s+(?:des\s+)?travaux)",
    r"(dépenses?\s+(?:de\s+)?recherche)",
    r"(personnel\s+(?:de\s+)?recherche)",
    r"(nature\s+(?:de\s+)?l['\s]incertitude)",
    r"(démarche\s+(?:scientifique|expérimentale))",
]

_RD_SECTION_RE = re.compile(
    "|".join(RD_SECTION_PATTERNS),
    flags=re.IGNORECASE | re.UNICODE,
)


# ── Dataclasses résultats ─────────────────────────────────────────────────────

@dataclass
class PageResult:
    """Résultat d'extraction pour une page individuelle."""
    page_number: int                      # Numéro 1-indexé
    raw_text: str                         # Texte brut pdfplumber
    tables_as_text: list[str]             # Tableaux convertis en Markdown
    has_images: bool                      # Page contient des images
    is_text_poor: bool                    # Texte insuffisant (→ candidat OCR)
    char_count: int = 0
    detected_sections: list[str] = field(default_factory=list)
    source_text_raw: str = ""
    layout_mode: str = "legacy"
    column_count: int = 1
    layout_confidence: float = 0.0
    layout_split_x: Optional[float] = None
    word_count: int = 0
    table_words_removed: int = 0
    table_bboxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    figure_captions: list[str] = field(default_factory=list)
    table_captions: list[str] = field(default_factory=list)
    body_bbox: Optional[tuple[float, float, float, float]] = None
    boundary_lines_removed: int = 0
    body_image_count: int = 0

@dataclass
class DocumentMetadata:
    """Métadonnées extraites du document PDF."""
    title: Optional[str] = None
    author: Optional[str] = None
    creator_tool: Optional[str] = None   # Logiciel utilisé (Word, LaTeX…)
    creation_date: Optional[str] = None
    modification_date: Optional[str] = None
    organisme_detected: Optional[str] = None   # Rempli par NER pour le moment c'est NOne
    page_count: int = 0
    has_toc: bool = False                # Table des matières présente


@dataclass
class NativePDFResult:
    """
    Résultat complet de l'extraction native d'un PDF.
    Compatible avec ExtractionResult (base.py).
    """
    file_name: str
    source_path: str
    file_type: str = "pdf_native"

    # ── Sortie principale pour le RAG ──────────────────────────────────────
    text_chunks: list[str] = field(default_factory=list)
    # Chaque chunk = texte d'une page + ses tableaux Markdown

    # ── Métadonnées document ───────────────────────────────────────────────
    metadata: DocumentMetadata = field(default_factory=DocumentMetadata)

    # ── Détail page par page ───────────────────────────────────────────────
    pages: list[PageResult] = field(default_factory=list)

    # ── Pages à renvoyer vers OCR ──────────────────────────────────────────
    ocr_needed_pages: list[int] = field(default_factory=list)

    # ── Sections R&D détectées sur l'ensemble du document ─────────────────
    detected_rd_sections: list[str] = field(default_factory=list)

    # ── Tags de traçabilité ────────────────────────────────────────────────
    tags: list[str] = field(default_factory=list)
    # Ex: ["HAS_TABLES", "PARTIAL_SCAN", "CIR_SECTIONS"]

    # ── Qualité globale ────────────────────────────────────────────────────
    confidence_score: float = 1.0
    extraction_errors: list[str] = field(default_factory=list)


# ── Fonctions utilitaires ─────────────────────────────────────────────────────

def _table_to_markdown(table: list[list[str | None]]) -> str:
    """
    Convertit un tableau pdfplumber en Markdown lisible pour le RAG.
    Les cellules None sont remplacées par des chaînes vides.

    """
    if not table or not table[0]:
        return ""

    # Nettoyage des cellules
    cleaned: list[list[str]] = []
    for row in table:
        cleaned.append([
            str(cell).strip().replace("\n", " ") if cell is not None else ""
            for cell in row
        ])

    if not cleaned:
        return ""

    # Supprime les colonnes de mise en page entièrement vides. Les tableaux
    # Word exportés en PDF contiennent souvent des colonnes fantômes dues aux
    # cellules fusionnées ; elles nuisent fortement à la lecture RAG.
    max_columns = max(len(row) for row in cleaned)
    kept_columns = [
        index
        for index in range(max_columns)
        if any(index < len(row) and row[index].strip() for row in cleaned)
    ]
    cleaned = [
        [row[index] if index < len(row) else "" for index in kept_columns]
        for row in cleaned
    ]
    cleaned = [row for row in cleaned if any(cell.strip() for cell in row)]
    if not cleaned or not cleaned[0]:
        return ""

    # En-tête = première ligne
    header = cleaned[0]
    col_widths = [max(len(r[i]) for r in cleaned if i < len(r)) for i in range(len(header))]
    col_widths = [max(w, 3) for w in col_widths]  # minimum 3 pour le séparateur

    def _fmt_row(row: list[str]) -> str:
        padded = [
            row[i].ljust(col_widths[i]) if i < len(row) else " " * col_widths[i]
            for i in range(len(header))
        ]
        return "| " + " | ".join(padded) + " |"

    separator = "| " + " | ".join("-" * w for w in col_widths) + " |"

    lines = [_fmt_row(header), separator]
    for row in cleaned[1:]:
        lines.append(_fmt_row(row))

    return "\n".join(lines)


def _normalise_boundary_key(text: str) -> str:
    """Signature générique d'une ligne d'en-tête/pied répétée."""
    value = unicodedata.normalize("NFKD", str(text or "").lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"\d+", "#", value)
    value = re.sub(r"[^a-z#]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _positioned_lines(page: Page) -> list[tuple[str, float, float]]:
    """Reconstruit des lignes simples avec leurs coordonnées verticales."""
    try:
        words = page.extract_words(
            x_tolerance=2,
            y_tolerance=2,
            keep_blank_chars=False,
            use_text_flow=False,
        ) or []
    except TypeError:
        words = page.extract_words(
            x_tolerance=2,
            y_tolerance=2,
            keep_blank_chars=False,
        ) or []
    except Exception:
        return []

    rows: list[dict] = []
    for word in sorted(words, key=lambda item: (float(item.get("top", 0)), float(item.get("x0", 0)))):
        top = float(word.get("top", 0))
        bottom = float(word.get("bottom", top + 1))
        center = (top + bottom) / 2.0
        height = max(1.0, bottom - top)
        row = next(
            (
                candidate
                for candidate in reversed(rows[-4:])
                if abs(center - candidate["center"]) <= max(2.2, height * 0.45)
            ),
            None,
        )
        if row is None:
            rows.append({"center": center, "words": [word]})
        else:
            row["words"].append(word)
            row["center"] = sum(
                (float(item.get("top", 0)) + float(item.get("bottom", 0))) / 2.0
                for item in row["words"]
            ) / len(row["words"])

    output: list[tuple[str, float, float]] = []
    for row in rows:
        ordered = sorted(row["words"], key=lambda item: float(item.get("x0", 0)))
        text = " ".join(str(item.get("text") or "").strip() for item in ordered).strip()
        if text:
            output.append(
                (
                    text,
                    min(float(item.get("top", 0)) for item in ordered),
                    max(float(item.get("bottom", 0)) for item in ordered),
                )
            )
    return output


def _infer_body_bboxes(pdf: pdfplumber.PDF) -> tuple[dict[int, tuple[float, float, float, float]], int]:
    """Déduit les bandes utiles à partir des lignes répétées du document.

    Les signatures sont calculées uniquement dans les marges haute/basse. La
    première page est conservée intégralement afin de ne pas rogner une page de
    garde. Aucune chaîne propre à un client ou à un projet n'est utilisée.
    """
    if len(pdf.pages) < 3:
        return {}, 0

    page_lines: dict[int, list[tuple[str, float, float, str]]] = {}
    counts: Counter[str] = Counter()

    for page in pdf.pages[1:]:
        candidates: list[tuple[str, float, float, str]] = []
        height = float(page.height or 0)
        for text, top, bottom in _positioned_lines(page):
            if top <= height * 0.16 or bottom >= height * 0.84:
                key = _normalise_boundary_key(text)
                if len(key) >= 5:
                    candidates.append((key, top, bottom, text))
        page_lines[page.page_number] = candidates
        counts.update({item[0] for item in candidates})

    threshold = max(3, math.ceil((len(pdf.pages) - 1) * 0.35))
    repeated = {key for key, count in counts.items() if count >= threshold}
    if not repeated:
        return {}, 0

    bboxes: dict[int, tuple[float, float, float, float]] = {}
    removed = 0

    for page in pdf.pages[1:]:
        width = float(page.width or 0)
        height = float(page.height or 0)
        matches = [item for item in page_lines.get(page.page_number, []) if item[0] in repeated]
        header = [item for item in matches if item[1] <= height * 0.30]
        footer = [item for item in matches if item[2] >= height * 0.70]
        top = max((item[2] for item in header), default=0.0)
        bottom = min((item[1] for item in footer), default=height)
        if top:
            top = min(height * 0.25, top + 4.0)
        if bottom < height:
            bottom = max(height * 0.70, bottom - 4.0)
        if top < bottom:
            bboxes[page.page_number] = (0.0, top, width, bottom)
            removed += len(header) + len(footer)

    return bboxes, removed


def _table_is_structural(table: list[list[str | None]], bbox: tuple[float, ...], page: Page) -> bool:
    """Écarte cadres/pieds de page tout en gardant les vrais tableaux."""
    if not table or len(bbox) != 4:
        return False
    _, top, _, bottom = bbox
    height = float(page.height or 0)
    if top < height * 0.10 or bottom > height * 0.88:
        return False

    row_count = len(table)
    col_count = max((len(row) for row in table), default=0)
    if col_count < 2:
        return False

    non_empty_per_row = [
        sum(1 for cell in row if cell is not None and str(cell).strip())
        for row in table
    ]
    chars = sum(len(str(cell).strip()) for row in table for cell in row if cell is not None)
    paired_rows = sum(count >= 2 for count in non_empty_per_row)

    if row_count >= 2 and paired_rows >= 2 and chars >= 24:
        return True
    return bool(
        row_count == 1
        and non_empty_per_row
        and non_empty_per_row[0] >= 2
        and (bottom - top) >= 32
        and chars >= 80
    )


def _extract_caption_blocks(text: str) -> tuple[list[str], list[str]]:
    """Extrait les légendes multi-lignes sans interpréter le visuel."""
    figures: list[str] = []
    tables: list[str] = []
    start_re = re.compile(
        r"^\s*(figure|fig\.?|illustration|sch[ée]ma|tableau)\s*\d+\s*[:.]",
        re.I,
    )
    heading_re = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s+")

    for block in re.split(r"\n\s*\n", str(text or "")):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            match = start_re.match(line)
            if not match:
                continue
            caption_lines = [line]
            for continuation in lines[index + 1 : index + 4]:
                if heading_re.match(continuation) or start_re.match(continuation):
                    break
                caption_lines.append(continuation)
                if continuation.endswith((".", ";")):
                    break
            caption = re.sub(r"\s+", " ", " ".join(caption_lines)).strip()
            target = tables if match.group(1).lower().startswith("tableau") else figures
            if caption and caption not in target:
                target.append(caption)
    return figures, tables


def _extract_positioned_captions(page: Page) -> tuple[list[str], list[str]]:
    """Extrait les légendes à partir de leur continuité géométrique.

    Une ligne suivante n'appartient à la légende que si l'espacement vertical
    reste celui d'une ligne normale. Cela évite d'absorber le paragraphe placé
    sous une figure dans le chunk visuel.
    """
    records = _positioned_lines(page)
    figures: list[str] = []
    tables: list[str] = []
    start_re = re.compile(
        r"^\s*(figure|fig\.?|illustration|sch[ée]ma|tableau)\s*\d+\s*[:.]",
        re.I,
    )
    heading_re = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s+")

    for index, (line, _top, bottom) in enumerate(records):
        match = start_re.match(line)
        if not match:
            continue
        caption_lines = [line.strip()]
        previous_bottom = bottom
        for continuation, next_top, next_bottom in records[index + 1 : index + 4]:
            if next_top - previous_bottom > 5.0:
                break
            if heading_re.match(continuation) or start_re.match(continuation):
                break
            caption_lines.append(continuation.strip())
            previous_bottom = next_bottom
        caption = re.sub(r"\s+", " ", " ".join(caption_lines)).strip()
        target = tables if match.group(1).lower().startswith("tableau") else figures
        if caption and caption not in target:
            target.append(caption)

    return figures, tables


def _detect_rd_sections(text: str) -> list[str]:
    """
    Détecte les sections R&D / CIR standard présentes dans le texte.
    Retourne la liste des intitulés de sections trouvés.
    """
    matches = _RD_SECTION_RE.findall(text)
    # findall retourne des tuples de groupes → aplatir et filtrer
    sections = []
    for match in matches:
        if isinstance(match, tuple):
            sections.extend(s.strip() for s in match if s.strip())
        elif match.strip():
            sections.append(match.strip())
    # Déduplique en conservant l'ordre
    seen: set[str] = set()
    unique: list[str] = []
    for s in sections:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def _extract_page(
    page: Page,
    page_number: int,
    *,
    body_bbox: Optional[tuple[float, float, float, float]] = None,
) -> PageResult:
    """
    V188 : extraction native avec reconstruction
    automatique de l'ordre de lecture.
    """

    extraction_page = page.crop(body_bbox, strict=False) if body_bbox else page

    table_settings = {
        "vertical_strategy":
            "lines",

        "horizontal_strategy":
            "lines",

        "snap_tolerance": 3,
        "join_tolerance": 3,
        "edge_min_length": 10,
        "intersection_tolerance": 4,
    }


    tables_as_text = []
    table_bboxes = []


    # ========================================================
    # Tableaux + BBox
    # ========================================================

    try:

        found_tables = (
            extraction_page.find_tables(
                table_settings
            )
            or []
        )


        for found in found_tables:

            try:
                table = found.extract()
            except Exception:
                table = None


            bbox = tuple(float(v) for v in found.bbox)

            if table and _table_is_structural(table, bbox, page):

                md = _table_to_markdown(
                    table
                )

                if md:
                    tables_as_text.append(
                        md
                    )
                    table_bboxes.append(
                        bbox
                    )


    except Exception as exc:

        logger.warning(
            "Page %d ? ?chec find_tables : %s",
            page_number,
            exc,
        )


        # fallback historique
        try:

            tables = (
                extraction_page.extract_tables(
                    table_settings=
                        table_settings
                )
                or []
            )


            for table in tables:

                if not table:
                    continue

                md = _table_to_markdown(
                    table
                )

                if md:
                    tables_as_text.append(
                        md
                    )

        except Exception as table_exc:

            logger.warning(
                "Page %d ? ?chec tableaux fallback : %s",
                page_number,
                table_exc,
            )


    # ========================================================
    # Texte / colonnes
    # ========================================================

    layout = (
        extract_page_reading_order(
            extraction_page,
            page_number,
            table_bboxes=
                table_bboxes,
        )
    )


    raw_text = (
        layout.text
        or ""
    )


    body_images = list(getattr(extraction_page, "images", []) or [])
    has_images = bool(body_images)


    full_page_text = (
        raw_text
        + "\n"
        + "\n".join(
            tables_as_text
        )
    )


    detected_sections = (
        _detect_rd_sections(
            full_page_text
        )
    )


    char_count = len(
        full_page_text.strip()
    )

    figure_captions, table_captions = _extract_positioned_captions(extraction_page)
    if not figure_captions and not table_captions:
        figure_captions, table_captions = _extract_caption_blocks(raw_text)


    is_text_poor = (
        char_count
        < MIN_CHARS_PER_PAGE
    )


    return PageResult(
        page_number=
            page_number,

        raw_text=
            raw_text,

        tables_as_text=
            tables_as_text,

        has_images=
            has_images,

        is_text_poor=
            is_text_poor,

        char_count=
            char_count,

        detected_sections=
            detected_sections,

        source_text_raw=
            layout.source_text_raw,

        layout_mode=
            layout.layout_mode,

        column_count=
            layout.column_count,

        layout_confidence=
            layout.confidence,

        layout_split_x=
            layout.split_x,

        word_count=
            layout.word_count,

        table_words_removed=
            layout.table_words_removed,

        table_bboxes=table_bboxes,

        figure_captions=figure_captions,

        table_captions=table_captions,

        body_bbox=body_bbox,

        boundary_lines_removed=0,

        body_image_count=len(body_images),
    )

def _build_page_chunk(page_result: PageResult) -> str:
    """
    Assemble le chunk final d'une page pour le RAG.
    Intègre aussi les formules et images détectées (garde le contexte de la page).

    """
    parts: list[str] = [f"[PAGE {page_result.page_number}]"]

    body = page_result.raw_text.strip()
    remaining_tables: list[tuple[int, str, bool]] = []

    for index, table_md in enumerate(page_result.tables_as_text, start=1):
        caption = (
            page_result.table_captions[index - 1]
            if index - 1 < len(page_result.table_captions)
            else ""
        )
        label_match = re.match(r"(Tableau\s*\d+\s*[:.])", caption, flags=re.I)
        if body and label_match:
            line_re = re.compile(
                rf"^(\s*{re.escape(label_match.group(1))}[^\n]*)$",
                flags=re.I | re.M,
            )
            if line_re.search(body):
                insertion = f"[TABLEAU STRUCTURÉ {index}]\n{table_md}\n\n\\1"
                body = line_re.sub(insertion, body, count=1)
                continue
        bbox = page_result.table_bboxes[index - 1] if index - 1 < len(page_result.table_bboxes) else None
        body_top = page_result.body_bbox[1] if page_result.body_bbox else 0.0
        begins_page = bool(bbox and float(bbox[1]) <= float(body_top) + 30.0)
        remaining_tables.append((index, table_md, begins_page))

    leading_tables = [
        f"[TABLEAU STRUCTURÉ {index}]\n{table_md}"
        for index, table_md, begins_page in remaining_tables
        if begins_page
    ]
    if leading_tables:
        parts.extend(leading_tables)

    if body:
        parts.append(body)

    for index, table_md, begins_page in remaining_tables:
        if not begins_page:
            parts.append(f"\n[TABLEAU STRUCTURÉ {index}]\n{table_md}")

    chunk = "\n\n".join(parts)
    
    
    return chunk


def _extract_metadata(pdf: pdfplumber.PDF, first_pages_text: str) -> DocumentMetadata:
    """
    Extrait les métadonnées du document.
    Combine les métadonnées PDF intégrées + détection NLP légère sur le texte.
    """
    meta = pdf.metadata or {}

    def _clean(val: object) -> Optional[str]:
        """Nettoie une valeur metadata PDF (peut être bytes ou str)."""
        if val is None:
            return None
        if isinstance(val, bytes):
            try:
                return val.decode("utf-8", errors="replace").strip()
            except Exception:
                return None
        s = str(val).strip()
        return s if s else None

    # Détection table des matières (présence de "table des matières" ou "sommaire")
    has_toc = bool(
        re.search(r"(table\s+des\s+matières|sommaire)", first_pages_text, re.IGNORECASE)
    )

    return DocumentMetadata(
        title=_clean(meta.get("Title")) or _clean(meta.get("title")),
        author=_clean(meta.get("Author")) or _clean(meta.get("author")),
        creator_tool=_clean(meta.get("Creator")) or _clean(meta.get("creator")),
        creation_date=_clean(meta.get("CreationDate")),
        modification_date=_clean(meta.get("ModDate")),
        organisme_detected=None,
        page_count=len(pdf.pages),
        has_toc=has_toc,
    )


def _build_tags(result: NativePDFResult) -> list[str]:
    """Construit la liste de tags de traçabilité du document."""
    tags: list[str] = ["PDF_NATIVE"]

    if any(p.tables_as_text for p in result.pages):
        tags.append("HAS_TABLES")

    if any(p.body_bbox for p in result.pages):
        tags.append("REPEATED_BOUNDARIES_REMOVED")

    if any(p.figure_captions for p in result.pages):
        tags.append("HAS_FIGURE_CAPTIONS")

    if any(p.has_images for p in result.pages):
        tags.append("HAS_IMAGES")

    if result.ocr_needed_pages:
        tags.append("PARTIAL_SCAN")

    if result.detected_rd_sections:
        tags.append("CIR_SECTIONS")

    if result.metadata.has_toc:
        tags.append("HAS_TOC")

    if any(
        getattr(
            page,
            "column_count",
            1,
        ) >= 2
        for page in result.pages
    ):
        tags.append(
            "MULTI_COLUMN_READING_ORDER"
        )

    if any(
        str(
            getattr(
                page,
                "layout_mode",
                "",
            )
        ).startswith("bbox_")
        for page in result.pages
    ):
        tags.append(
            "BBOX_READING_ORDER_V188"
        )

    return tags


def _compute_confidence(result: NativePDFResult) -> float:
    """
    Calcule un score de confiance global [0.0 – 1.0].

    Pénalités :
      - Pages OCR nécessaires  → -0.15 par page (max -0.40)
      - Erreurs d'extraction   → -0.10 par erreur (max -0.30)
      - Aucun texte extrait    → 0.10 (score minimal)
    """
    if not result.pages:
        return 0.0

    total_pages = len(result.pages)
    ocr_ratio = len(result.ocr_needed_pages) / total_pages
    error_penalty = min(len(result.extraction_errors) * 0.10, 0.30)
    ocr_penalty = min(ocr_ratio * 0.50, 0.40)

    score = 1.0 - ocr_penalty - error_penalty

    # Si aucun texte exploitable → score plancher
    if all(p.is_text_poor for p in result.pages):
        return max(score, 0.10)

    return max(round(score, 2), 0.10)


# ── Point d'entrée principal ──────────────────────────────────────────────────

def extract_pdf_native(file_path: str | Path) -> NativePDFResult:
    """
    Extrait le contenu complet d'un PDF texte natif pour le RAG EnnoSmart.

    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Format non supporté par pdf_native : {path.suffix}")

    logger.info("Extraction native PDF → %s", path.name)

    result = NativePDFResult(
        file_name=path.name,
        source_path=str(path.resolve()),
    )

    try:
        with pdfplumber.open(str(path)) as pdf:

            # ── Métadonnées (sur les 3 premières pages) ────────────────────
            first_pages_text = ""
            preview_limit = min(3, len(pdf.pages))
            for i in range(preview_limit):
                first_pages_text += pdf.pages[i].extract_text() or ""

            result.metadata = _extract_metadata(pdf, first_pages_text)
            result.metadata.page_count = len(pdf.pages)

            # ── Extraction page par page ───────────────────────────────────
            body_bboxes, boundary_lines_removed = _infer_body_bboxes(pdf)
            all_rd_sections: set[str] = set()

            for page in pdf.pages:
                page_number = page.page_number  # 1-indexé dans pdfplumber

                try:
                    page_result = _extract_page(
                        page,
                        page_number,
                        body_bbox=body_bboxes.get(page_number),
                    )
                    if page_number in body_bboxes:
                        page_result.boundary_lines_removed = boundary_lines_removed
                    result.pages.append(page_result)

                    # Chunk pour le RAG
                    # Les formules sont maintenant intégrées DANS le chunk (via _build_page_chunk)
                    chunk = _build_page_chunk(page_result)
                    result.text_chunks.append(chunk)

                    # Pages à OCR
                    if page_result.is_text_poor:
                        result.ocr_needed_pages.append(page_number)
                        logger.debug(
                            "Page %d — texte pauvre (%d chars), candidat OCR",
                            page_number, page_result.char_count,
                        )

                    # Sections R&D
                    all_rd_sections.update(page_result.detected_sections)

                except Exception as exc:
                    msg = f"Erreur page {page_number} : {exc}"
                    logger.warning(msg)
                    result.extraction_errors.append(msg)
                    # On crée quand même un chunk vide pour garder l'alignement
                    result.text_chunks.append(f"[PAGE {page_number}]\n[ERREUR EXTRACTION]")

            result.detected_rd_sections = sorted(all_rd_sections)

    except pdfplumber.exceptions.PDFSyntaxError as exc:
        raise ValueError(f"PDF corrompu ou invalide : {path.name}") from exc
    except Exception as exc:
        logger.error("Échec extraction native %s : %s", path.name, exc)
        result.extraction_errors.append(f"Échec global : {exc}")

    # ── Post-traitement ────────────────────────────────────────────────────
    result.tags = _build_tags(result)
    result.confidence_score = _compute_confidence(result)

    logger.info(
        "✓ %s — %d pages | %d chunks | score=%.2f | tags=%s | OCR_needed=%s",
        path.name,
        result.metadata.page_count,
        len(result.text_chunks),
        result.confidence_score,
        result.tags,
        result.ocr_needed_pages or "aucune",
    )

    return result


# ── Interface rapide (debug / tests) ──────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.DEBUG)

    if len(sys.argv) < 2:
        print("Usage : python pdf_native.py <chemin_vers_pdf>")
        sys.exit(1)

    res = extract_pdf_native(sys.argv[1])

    summary = {
        "file": res.file_name,
        "pages": res.metadata.page_count,
        "organisme": res.metadata.organisme_detected,
        "tags": res.tags,
        "confidence": res.confidence_score,
        "rd_sections": res.detected_rd_sections,
        "ocr_needed_pages": res.ocr_needed_pages,
        "errors": res.extraction_errors,
        "chunks_preview": [c[:200] + "…" for c in res.text_chunks[:3]],
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
