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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber
from pdfplumber.page import Page

# ── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# ── Constantes R&D ────────────────────────────────────────────────────────────

# Seuil minimum de caractères pour considérer une page comme "texte natif"
MIN_CHARS_PER_PAGE = 80

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
    detected_sections: list[str] = field(default_factory=list)          # Sections R&D détectées sur cette page

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


def _extract_page(page: Page, page_number: int) -> PageResult:
    """
    Extrait tout le contenu utile d'une page pdfplumber.

    Ordre d'extraction :
      1. Texte brut (layout préservé)
      2. Tableaux → Markdown
      3. Présence d'images
      4. Détection sections R&D
      5. Qualification de la qualité texte
    """
    # ── 1. Texte brut ─────────────────────────────────────────────────────
    raw_text: str = page.extract_text(x_tolerance=2, y_tolerance=2) or ""

    # ── 2. Tableaux ───────────────────────────────────────────────────────
    tables_as_text: list[str] = []
    try:
        tables = page.extract_tables(
            table_settings={
                "vertical_strategy": "lines_strict",
                "horizontal_strategy": "lines_strict",
                "snap_tolerance": 3,
                "join_tolerance": 3,
                "edge_min_length": 10,
            }
        )
        for table in tables:
            if table:
                md = _table_to_markdown(table)
                if md:
                    tables_as_text.append(md)
    except Exception as exc:
        logger.warning("Page %d — échec extraction tableaux : %s", page_number, exc)

    # ── 3. Images ─────────────────────────────────────────────────────────
    has_images = bool(page.images)

    # ── 4. Sections R&D ───────────────────────────────────────────────────
    full_page_text = raw_text + "\n".join(tables_as_text)
    detected_sections = _detect_rd_sections(full_page_text)

    # ── 5. Qualité texte ──────────────────────────────────────────────────
    char_count = len(raw_text.strip())
    is_text_poor = char_count < MIN_CHARS_PER_PAGE

    return PageResult(
        page_number=page_number,
        raw_text=raw_text,
        tables_as_text=tables_as_text,
        has_images=has_images,
        is_text_poor=is_text_poor,
        char_count=char_count,
        detected_sections=detected_sections,
    )


def _build_page_chunk(page_result: PageResult) -> str:
    """
    Assemble le chunk final d'une page pour le RAG.
    Intègre aussi les formules et images détectées (garde le contexte de la page).

    """
    parts: list[str] = [f"[PAGE {page_result.page_number}]"]

    if page_result.raw_text.strip():
        parts.append(page_result.raw_text.strip())

    for i, table_md in enumerate(page_result.tables_as_text, start=1):
        parts.append(f"\n[TABLEAU {i}]\n{table_md}")

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

    if any(p.has_images for p in result.pages):
        tags.append("HAS_IMAGES")

    if result.ocr_needed_pages:
        tags.append("PARTIAL_SCAN")

    if result.detected_rd_sections:
        tags.append("CIR_SECTIONS")

    if result.metadata.has_toc:
        tags.append("HAS_TOC")

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
            all_rd_sections: set[str] = set()

            for page in pdf.pages:
                page_number = page.page_number  # 1-indexé dans pdfplumber

                try:
                    page_result = _extract_page(page, page_number)
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