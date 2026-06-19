"""
modules/extraction/base.py
──────────────────────────────────────────────────────────────────────────────
Contrat universel de sortie du module extraction/.

Tous les extracteurs (pdf_native, pdf_ocr, office, email_parser,
excel_struct, vision, audio_transcriber) produisent leurs propres dataclasses
riches.

Ce module définit ExtractionResult — la forme normalisée et unifiée que
router.py assemble depuis ces résultats avant de les passer au pipeline
NLP → RAG.

Pourquoi ce niveau d'abstraction ?
  Les modules NLP (cleaner, ner, classifier) et RAG (chunker, embedder, store)
  ne doivent pas connaître la différence entre un PDF, un email, une image
  ou un fichier audio/vidéo.

  Ils reçoivent toujours un ExtractionResult identique.

  pdf_native.py        ┐
  pdf_ocr.py           │
  office.py            │
  email_parser.py      ├──► router.py ──► ExtractionResult ──► nlp/ ──► rag_core/
  excel_struct.py      │
  vision.py            │
  audio_transcriber.py ┘

Auteur  : EnnoSmart
Version : 1.1.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────

class SourceTag(str, Enum):
    """Origine du document dans le contexte R&D / CIR."""

    DE_DOC = "DE_DOC"       # Document projet en cours
    NOTES = "NOTES"         # Notes consultant
    ARCHIVE = "ARCHIVE"     # Dossier historique : brut / CIR final / ancien dossier


class FileCategory(str, Enum):
    """Catégorie technique du fichier source."""

    PDF_NATIVE = "pdf_native"
    PDF_OCR = "pdf_ocr"

    DOCX = "docx"
    PPTX = "pptx"

    EMAIL = "email"
    EXCEL = "excel"
    IMAGE = "image"

    # Nouveau : transcription audio / vidéo
    AUDIO_VIDEO = "audio_video"

    UNKNOWN = "unknown"


# ──────────────────────────────────────────────────────────────────────────────
# Dataclass principale
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    """
    Résultat d'extraction normalisé — contrat universel EnnoSmart.

    Produit par router.py à partir des résultats bruts des extracteurs.
    Consommé ensuite par modules/nlp/ puis modules/rag_core/.

    Champs obligatoires
    -------------------
    file_name, source_path, file_category

    Sortie principale
    -----------------
    text_chunks :
        Blocs texte exploitables par le NLP et le RAG.
        Exemples :
        - "[PAGE 1] ..."
        - "[SLIDE 3] ..."
        - "[EMAIL] ..."
        - "[AUDIO:reunion.mp3] [SEGMENT 1] ..."

    visual_chunks :
        Descriptions d'images, schémas, graphiques ou captures.
        Ces chunks peuvent aussi être indexés dans le RAG.

    structured_data :
        Données tabulaires, surtout pour Excel / EnnoValor.

    attachments_paths :
        Chemins temporaires des pièces jointes extraites des emails.

    Champs transcription
    --------------------
    media_duration_seconds :
        Durée audio/vidéo si connue.

    transcription_language :
        Langue détectée ou imposée, ex: "fr", "en".

    transcription_model :
        Modèle utilisé, ex: "small", "medium".

    transcription_engine :
        Moteur utilisé, ex: "faster_whisper".
    """

    # ── Identité fichier ──────────────────────────────────────────────────
    file_name: str
    source_path: str
    file_category: FileCategory

    # ── Sortie texte → NLP + RAG ──────────────────────────────────────────
    text_chunks: list[str] = field(default_factory=list)

    # ── Sortie visuelle → RAG enrichi ─────────────────────────────────────
    visual_chunks: list[str] = field(default_factory=list)

    # ── Sortie structurée → EnnoValor / Excel ─────────────────────────────
    structured_data: Optional[dict[str, Any]] = None

    # ── Pièces jointes à router, surtout emails ───────────────────────────
    attachments_paths: list[str] = field(default_factory=list)

    # ── Métadonnées document ───────────────────────────────────────────────
    title: Optional[str] = None
    author: Optional[str] = None
    organisme: Optional[str] = None
    creation_date: Optional[str] = None
    page_count: int = 0
    source_tag: SourceTag = SourceTag.DE_DOC

    # ── Métadonnées transcription audio/vidéo ─────────────────────────────
    media_duration_seconds: Optional[float] = None
    transcription_language: Optional[str] = None
    transcription_model: Optional[str] = None
    transcription_engine: Optional[str] = None

    # ── Sections R&D détectées ─────────────────────────────────────────────
    detected_rd_sections: list[str] = field(default_factory=list)

    # ── Traçabilité & qualité ──────────────────────────────────────────────
    tags: list[str] = field(default_factory=list)
    confidence_score: float = 1.0
    extraction_errors: list[str] = field(default_factory=list)

    # ── Pages nécessitant OCR, surtout PDF mixtes ──────────────────────────
    ocr_needed_pages: list[int] = field(default_factory=list)

    # ──────────────────────────────────────────────────────────────────────
    # Propriétés calculées
    # ──────────────────────────────────────────────────────────────────────

    @property
    def all_chunks(self) -> list[str]:
        """
        Tous les chunks à indexer dans le RAG :
        texte + descriptions visuelles.
        """
        return self.text_chunks + self.visual_chunks

    @property
    def has_structured_data(self) -> bool:
        """True si le document contient des données structurées."""
        return self.structured_data is not None

    @property
    def has_visuals(self) -> bool:
        """True si des descriptions visuelles ont été générées."""
        return bool(self.visual_chunks)

    @property
    def has_attachments(self) -> bool:
        """True si des pièces jointes ont été extraites."""
        return bool(self.attachments_paths)

    @property
    def is_audio_video(self) -> bool:
        """True si le fichier est un média audio/vidéo."""
        return self.file_category == FileCategory.AUDIO_VIDEO

    @property
    def has_transcription(self) -> bool:
        """True si une transcription exploitable existe."""
        return self.is_audio_video and bool(self.text_chunks)

    @property
    def has_ocr(self) -> bool:
        """True si le résultat provient d'un PDF OCR ou mixte."""
        return self.file_category == FileCategory.PDF_OCR or bool(self.ocr_needed_pages)

    @property
    def total_chunks(self) -> int:
        """Nombre total de chunks RAG : texte + visuels."""
        return len(self.text_chunks) + len(self.visual_chunks)

    @property
    def is_valid(self) -> bool:
        """
        True si l'extraction a produit au moins un chunk exploitable.
        Un résultat invalide ne doit pas être envoyé au RAG.
        """
        return bool(self.text_chunks or self.visual_chunks)

    @property
    def has_errors(self) -> bool:
        """True si l'extraction a généré des erreurs ou alertes."""
        return bool(self.extraction_errors)

    # ──────────────────────────────────────────────────────────────────────
    # Méthodes utilitaires
    # ──────────────────────────────────────────────────────────────────────

    def add_tag(self, tag: str) -> None:
        """Ajoute un tag sans doublon."""
        clean = str(tag or "").strip()
        if clean and clean not in self.tags:
            self.tags.append(clean)

    def add_error(self, error: str) -> None:
        """Ajoute une erreur sans doublon."""
        clean = str(error or "").strip()
        if clean and clean not in self.extraction_errors:
            self.extraction_errors.append(clean)

    def extend_text_chunks(self, chunks: list[str]) -> None:
        """Ajoute des chunks texte non vides."""
        for chunk in chunks or []:
            if chunk and str(chunk).strip():
                self.text_chunks.append(str(chunk))

    def extend_visual_chunks(self, chunks: list[str]) -> None:
        """Ajoute des chunks visuels non vides."""
        for chunk in chunks or []:
            if chunk and str(chunk).strip():
                self.visual_chunks.append(str(chunk))

    def summary(self) -> dict[str, Any]:
        """
        Résumé compact pour logs, debug Streamlit et tests pipeline.
        """
        return {
            "file": self.file_name,
            "source_path": self.source_path,
            "category": self.file_category.value,
            "source_tag": self.source_tag.value,

            "chunks": self.total_chunks,
            "text_chunks": len(self.text_chunks),
            "visual_chunks": len(self.visual_chunks),

            "has_structured": self.has_structured_data,
            "has_attachments": self.has_attachments,
            "has_visuals": self.has_visuals,
            "has_ocr": self.has_ocr,
            "is_audio_video": self.is_audio_video,
            "has_transcription": self.has_transcription,

            "title": self.title,
            "author": self.author,
            "organisme": self.organisme,
            "creation_date": self.creation_date,
            "page_count": self.page_count,

            "media_duration_seconds": self.media_duration_seconds,
            "transcription_language": self.transcription_language,
            "transcription_model": self.transcription_model,
            "transcription_engine": self.transcription_engine,

            "rd_sections": len(self.detected_rd_sections),
            "detected_rd_sections": self.detected_rd_sections,

            "confidence": self.confidence_score,
            "tags": self.tags,
            "errors": self.extraction_errors,
            "ocr_needed": self.ocr_needed_pages,
            "is_valid": self.is_valid,
        }