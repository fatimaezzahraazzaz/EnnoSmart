"""
modules/extraction/base.py
──────────────────────────────────────────────────────────────────────────────
Contrat universel de sortie du module extraction/.

Tous les extracteurs (pdf_native, pdf_ocr, office, email_parser,
excel_struct, vision) produisent leurs propres dataclasses riches.
Ce module définit ExtractionResult — la forme normalisée et unifiée
que router.py assemble depuis ces résultats avant de les passer
au pipeline NLP → RAG.

Pourquoi ce niveau d'abstraction ?
  Les modules NLP (cleaner, ner) et RAG (chunker, embedder, store)
  ne doivent pas connaître la différence entre un PDF et un email.
  Ils reçoivent toujours un ExtractionResult identique.

  pdf_native.py  ┐
  pdf_ocr.py     │
  office.py      ├──► router.py ──► ExtractionResult ──► nlp/ ──► rag_core/
  email_parser.py│
  excel_struct.py│
  vision.py      ┘

Auteur  : EnnoSmart
Version : 1.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ── Enums ─────────────────────────────────────────────────────────────────────

class SourceTag(str, Enum):
    """Origine du document dans le contexte R&D."""
    DE_DOC    = "DE_DOC"     # Document projet en cours
    NOTES     = "NOTES"      # Notes consultant
    ARCHIVE   = "ARCHIVE"    # Dossier historique (pairs Brut/Final CIR)


class FileCategory(str, Enum):
    """Catégorie technique du fichier source."""
    PDF_NATIVE = "pdf_native"
    PDF_OCR    = "pdf_ocr"
    DOCX       = "docx"
    PPTX       = "pptx"
    EMAIL      = "email"
    EXCEL      = "excel"
    IMAGE      = "image"
    UNKNOWN    = "unknown"


# ── Dataclass principale ──────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    """
    Résultat d'extraction normalisé — contrat universel EnnoSmart.

    Produit par router.py à partir des résultats bruts des extracteurs.
    Consommé par modules/nlp/ puis modules/rag_core/.

    Champs obligatoires
    -------------------
    file_name, source_path, file_category, text_chunks

    Champs optionnels remplis selon le type de fichier
    ---------------------------------------------------
    structured_data   → Excel uniquement (Enno Valo)
    visual_chunks     → Images/schémas décrits par Llama Vision
    attachments_paths → Emails avec pièces jointes
    organisme         → Rempli par nlp/ner.py (GLiNER), pas ici
    """

    # ── Identité ──────────────────────────────────────────────────────────
    file_name: str
    source_path: str
    file_category: FileCategory

    # ── Sortie texte → RAG (tous agents) ──────────────────────────────────
    text_chunks: list[str] = field(default_factory=list)
    # Chaque chunk est un bloc texte brut prêt pour nlp/cleaner.py
    # Format unifié : "[PAGE N]...", "[SECTION : ...]...", "[SLIDE N]..."

    # ── Sortie visuelle → RAG enrichi ─────────────────────────────────────
    visual_chunks: list[str] = field(default_factory=list)
    # Descriptions Llama Vision — injectées dans le RAG comme text_chunks
    # Format : "[IMAGE | type | PAGE N]\n<description structurée>"

    # ── Sortie structurée → Enno Valo uniquement ──────────────────────────
    structured_data: Optional[dict[str, Any]] = None
    # Contenu : {"sheets": [...], "named_ranges": [...], "all_tables": [...]}
    # None pour tous les types sauf Excel

    # ── Pièces jointes à router (emails) ──────────────────────────────────
    attachments_paths: list[str] = field(default_factory=list)
    # Chemins temporaires des PJ extraites → router.py les traitera
    # comme des fichiers indépendants

    # ── Métadonnées document ───────────────────────────────────────────────
    title: Optional[str]            = None
    author: Optional[str]           = None
    organisme: Optional[str]        = None   # ← rempli par nlp/ner.py
    creation_date: Optional[str]    = None
    page_count: int                 = 0
    source_tag: SourceTag           = SourceTag.DE_DOC

    # ── Sections R&D détectées ─────────────────────────────────────────────
    detected_rd_sections: list[str] = field(default_factory=list)
    # Ex: ["état de l'art", "verrous technologiques", "démarche expérimentale"]

    # ── Traçabilité & Qualité ──────────────────────────────────────────────
    tags: list[str]                 = field(default_factory=list)
    confidence_score: float         = 1.0    # [0.0 – 1.0]
    extraction_errors: list[str]    = field(default_factory=list)

    # ── Pages nécessitant OCR (PDF mixtes) ────────────────────────────────
    ocr_needed_pages: list[int]     = field(default_factory=list)

    # ── Propriétés calculées ──────────────────────────────────────────────

    @property
    def all_chunks(self) -> list[str]:
        """
        Tous les chunks à indexer dans le RAG :
        texte + descriptions visuelles.
        Ordre : text_chunks en premier, visual_chunks ensuite.
        """
        return self.text_chunks + self.visual_chunks

    @property
    def has_structured_data(self) -> bool:
        """True si le document contient des données structurées pour Valo."""
        return self.structured_data is not None

    @property
    def has_visuals(self) -> bool:
        """True si des descriptions visuelles ont été générées."""
        return bool(self.visual_chunks)

    @property
    def has_attachments(self) -> bool:
        """True si des pièces jointes ont été extraites (emails)."""
        return bool(self.attachments_paths)

    @property
    def total_chunks(self) -> int:
        """Nombre total de chunks RAG (texte + visuels)."""
        return len(self.text_chunks) + len(self.visual_chunks)

    @property
    def is_valid(self) -> bool:
        """
        True si l'extraction a produit au moins un chunk exploitable.
        Un résultat invalide ne sera pas envoyé au RAG.
        """
        return bool(self.text_chunks or self.visual_chunks)

    def summary(self) -> dict:
        """
        Résumé compact pour les logs et le debug.
        """
        return {
            "file":            self.file_name,
            "category":        self.file_category.value,
            "chunks":          self.total_chunks,
            "text_chunks":     len(self.text_chunks),
            "visual_chunks":   len(self.visual_chunks),
            "has_structured":  self.has_structured_data,
            "has_attachments": self.has_attachments,
            "rd_sections":     len(self.detected_rd_sections),
            "confidence":      self.confidence_score,
            "tags":            self.tags,
            "errors":          self.extraction_errors,
            "ocr_needed":      self.ocr_needed_pages,
        }