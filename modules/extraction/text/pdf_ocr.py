"""
modules/extraction/text/pdf_ocr.py
──────────────────────────────────────────────────────────────────────────────
Extraction OCR de PDF scannés pour dossiers R&D / CIR.

Intégration pipeline :
  pdf_native.py ──(ocr_needed_pages)──► pdf_ocr.py ──► text_chunks BRUTS
  OU
  router.py ──(PDF 100% scanné)──────► pdf_ocr.py ──► text_chunks BRUTS

"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

try:
    import torch
except ImportError:
    torch = None

if torch is not None and torch.cuda.is_available():
    os.environ.setdefault("TORCH_DEVICE", "cuda")

# Import pdf2image
try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

# Import Surya OCR (0.17+)
try:
    from surya.foundation import FoundationPredictor
    from surya.recognition import RecognitionPredictor
    from surya.detection import DetectionPredictor
    SURYA_AVAILABLE = True
except ImportError:
    SURYA_AVAILABLE = False

    
# ── Fallback pytesseract ──────────────────────────────────────────────────────
try:
    import pytesseract
    from PIL import Image as PILImage
    
    # AJOUTE CETTE LIGNE ICI :
    pytesseract.pytesseract.tesseract_cmd = r'C:\Users\dell\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'
    
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

logger = logging.getLogger(__name__)
# ── Constantes ────────────────────────────────────────────────────────────────

# Résolution DPI pour la conversion PDF → image
# 300 DPI = standard qualité OCR sur documents techniques
OCR_DPI = 300

# Langues supportées par Surya et Tesseract pour les CIR
# Français prioritaire, Anglais pour brevets/publications
OCR_LANGUAGES = ["fr", "en"]

# Seuil de confiance OCR minimum acceptable (Surya fournit un score)
MIN_OCR_CONFIDENCE = 0.60

# Seuil texte pauvre — en dessous → on tente quand même mais on flag
MIN_CHARS_OCR_PAGE = 30


# ── Enums ─────────────────────────────────────────────────────────────────────

class OCREngine(str, Enum):
    SURYA      = "surya"
    TESSERACT  = "tesseract"
    NONE       = "none"    


class PageOrientation(str, Enum):
    NORMAL    = "normal"
    ROTATED90 = "rotated_90"
    ROTATED180= "rotated_180"
    ROTATED270= "rotated_270"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class OCRPageResult:
    """Résultat OCR pour une page individuelle."""
    page_number: int
    raw_text: str                        # Texte OCR brut
    confidence: float                    # Score confiance moyen [0.0–1.0]
    engine_used: OCREngine
    orientation_detected: PageOrientation
    orientation_corrected: bool
    char_count: int
    is_text_poor: bool                   # Résultat OCR encore insuffisant
    word_count: int
    extraction_errors: list[str] = field(default_factory=list)
    images_descriptions: list[dict] = field(default_factory=list)  # [{"index": 1, "description": "..."}]


@dataclass
class OCRResult:
    """
    Résultat complet d'extraction OCR d'un PDF scanné.
    Compatible avec NativePDFResult / ExtractionResult (base.py).
    """
    file_name: str
    source_path: str
    file_type: str = "pdf_ocr"

    # ── Sortie principale pour le RAG ──────────────────────────────────────
    text_chunks: list[str] = field(default_factory=list)
    # Format identique à pdf_native : "[PAGE N]\n<texte OCR>"

    # ── Détail page par page ───────────────────────────────────────────────
    pages: list[OCRPageResult] = field(default_factory=list)

    # ── Moteur utilisé ─────────────────────────────────────────────────────
    engine_used: OCREngine = OCREngine.NONE

    # ── Statistiques ───────────────────────────────────────────────────────
    page_count: int = 0
    pages_processed: list[int] = field(default_factory=list)
    # Si appelé depuis pdf_native : seulement les pages OCR nécessaires

    # ── Qualité globale ────────────────────────────────────────────────────
    confidence_score: float = 0.0
    tags: list[str] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)


# ── Gestion modèles Surya (chargement unique) ─────────────────────────────────

class _SuryaModelCache:
    """
    Singleton de cache des modeles Surya.
    Les modeles sont lourds (~1-2 GB), on les charge une seule fois.
    """

    _foundation_predictor = None
    _recognition_predictor = None
    _detection_predictor = None
    _loaded: bool = False

    @classmethod
    def load(cls) -> bool:
        """Charge les modeles Surya. Retourne True si succes."""
        if cls._loaded:
            return True
        if not SURYA_AVAILABLE:
            return False
        try:
            logger.info("Chargement modeles Surya OCR (premiere utilisation)...")
            cls._foundation_predictor = FoundationPredictor()
            cls._recognition_predictor = RecognitionPredictor(
                cls._foundation_predictor
            )
            cls._detection_predictor = DetectionPredictor()
            cls._loaded = True
            logger.info(
                "Modeles Surya charges | torch_device=%s",
                os.getenv("TORCH_DEVICE", "auto"),
            )
            return True
        except Exception as exc:
            logger.error("Echec chargement Surya : %s", exc)
            return False

    @classmethod
    def get(cls) -> tuple:
        """Retourne le tuple (foundation_predictor, recognition_predictor, detection_predictor)."""
        return (
            cls._foundation_predictor,
            cls._recognition_predictor,
            cls._detection_predictor,
        )


# ── Fonctions utilitaires ─────────────────────────────────────────────────────

def _detect_available_engine() -> OCREngine:
    """
    Détecte le moteur OCR disponible dans l'environnement.
    Priorité : Surya > Tesseract > None
    """
    if SURYA_AVAILABLE and _SuryaModelCache.load():
        return OCREngine.SURYA
    if TESSERACT_AVAILABLE:
        try:
            pytesseract.get_tesseract_version()
            logger.warning("Surya indisponible → fallback Tesseract")
            return OCREngine.TESSERACT
        except Exception:
            pass
    logger.error("Aucun moteur OCR disponible (Surya ni Tesseract)")
    return OCREngine.NONE


def _detect_orientation(image: "PILImage.Image") -> PageOrientation:
    """
    Détecte l'orientation d'une page via pytesseract OSD.
    """
    if not TESSERACT_AVAILABLE:
        return PageOrientation.NORMAL
    try:
        osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
        angle = osd.get("rotate", 0)
        mapping = {
            0:   PageOrientation.NORMAL,
            90:  PageOrientation.ROTATED90,
            180: PageOrientation.ROTATED180,
            270: PageOrientation.ROTATED270,
        }
        return mapping.get(angle, PageOrientation.NORMAL)
    except Exception:
        return PageOrientation.NORMAL


def _correct_orientation(
    image: "PILImage.Image",
    orientation: PageOrientation,
) -> tuple["PILImage.Image", bool]:
    """
    Corrige l'orientation d'une image si nécessaire.
    Retourne (image_corrigée, a_été_corrigée).
    """
    if orientation == PageOrientation.NORMAL:
        return image, False

    rotation_map = {
        PageOrientation.ROTATED90:  -90,
        PageOrientation.ROTATED180: 180,
        PageOrientation.ROTATED270:  90,
    }
    angle = rotation_map.get(orientation, 0)
    corrected = image.rotate(angle, expand=True)
    logger.debug("Orientation corrigée : %s (rotation %d°)", orientation.value, angle)
    return corrected, True


def _ocr_page_surya(
    image: "PILImage.Image",
    page_number: int,
) -> tuple[str, float]:
    """
    Applique Surya OCR sur une image de page.

    Retourne:
    tuple[str, float]
        (texte_extrait, score_confiance_moyen)
    """
    _, recognition_predictor, detection_predictor = _SuryaModelCache.get()

    try:
        predictions = recognition_predictor(
            [image],
            det_predictor=detection_predictor,
        )

        if not predictions or not predictions[0].text_lines:
            return "", 0.0

        lines = predictions[0].text_lines
        texts: list[str] = []
        confidences: list[float] = []

        for line in lines:
            if line.text and line.text.strip():
                texts.append(line.text.strip())
                confidences.append(getattr(line, "confidence", 1.0))

        full_text = "\n".join(texts)
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return full_text, round(avg_confidence, 3)

    except Exception as exc:
        logger.warning("Surya OCR page %d - erreur : %s", page_number, exc)
        return "", 0.0


def _ocr_page_tesseract(
    image: "PILImage.Image",
    page_number: int,
) -> tuple[str, float]:
    """
    Applique Tesseract OCR comme fallback.

    Retourne
    --------
    tuple[str, float]
        (texte_extrait, score_confiance_moyen)
    """
    try:
        lang_str = "+".join(["fra", "eng"])  # codes Tesseract

        # Extraction texte
        text = pytesseract.image_to_string(
            image,
            lang=lang_str,
            config="--psm 3",   # Mode auto — adapté aux documents mixtes
        )

        # Score confiance via data détaillée
        data = pytesseract.image_to_data(
            image,
            lang=lang_str,
            output_type=pytesseract.Output.DICT,
        )
        confidences = [
            c for c in data.get("conf", [])
            if isinstance(c, (int, float)) and c >= 0
        ]
        avg_conf = sum(confidences) / len(confidences) / 100.0 if confidences else 0.5

        return text.strip(), round(avg_conf, 3)

    except Exception as exc:
        logger.warning("Tesseract page %d — erreur : %s", page_number, exc)
        return "", 0.0


def _process_single_page(
    image: "PILImage.Image",
    page_number: int,
    engine: OCREngine,
) -> OCRPageResult:
    """
    Pipeline complet pour une page :
      1. Détection orientation
      2. Correction orientation
      3. OCR (Surya ou Tesseract)
      4. Construction OCRPageResult
    """
    errors: list[str] = []

    # ── 1 & 2. Orientation ────────────────────────────────────────────────
    orientation = _detect_orientation(image)
    image_corrected, was_corrected = _correct_orientation(image, orientation)

    # ── 3. OCR ───────────────────────────────────────────────────────────
    raw_text = ""
    confidence = 0.0

    if engine == OCREngine.SURYA:
        raw_text, confidence = _ocr_page_surya(image_corrected, page_number)
    elif engine == OCREngine.TESSERACT:
        raw_text, confidence = _ocr_page_tesseract(image_corrected, page_number)
    else:
        errors.append(f"Page {page_number} : aucun moteur OCR disponible")

    # ── 4. Métriques ─────────────────────────────────────────────────────
    char_count = len(raw_text.strip())
    word_count = len(raw_text.split())
    is_text_poor = char_count < MIN_CHARS_OCR_PAGE

    if confidence < MIN_OCR_CONFIDENCE and char_count > 0:
        logger.warning(
            "Page %d — confiance OCR faible : %.2f (seuil=%.2f)",
            page_number, confidence, MIN_OCR_CONFIDENCE,
        )
        errors.append(f"Page {page_number} : confiance OCR faible ({confidence:.2f})")

    return OCRPageResult(
        page_number=page_number,
        raw_text=raw_text,
        confidence=confidence,
        engine_used=engine,
        orientation_detected=orientation,
        orientation_corrected=was_corrected,
        char_count=char_count,
        is_text_poor=is_text_poor,
        word_count=word_count,
        extraction_errors=errors,
    )


def _build_page_chunk(page_result: OCRPageResult) -> str:
    """
    Assemble le chunk RAG d'une page OCR avec intégration des images et formules.
    Format identique à pdf_native pour cohérence downstream.
    """
    header = (
        f"[PAGE {page_result.page_number}] "
        f"[OCR:{page_result.engine_used.value} | "
        f"conf:{page_result.confidence:.2f}]"
    )
    body = page_result.raw_text.strip() if page_result.raw_text.strip() else "[PAGE VIDE]"
    chunk = f"{header}\n\n{body}"
    
    # Formules et images intégrées par router.py
    
    return chunk


def _pdf_to_images(
    pdf_path: Path,
    target_pages: Optional[list[int]],
) -> list[tuple[int, "PILImage.Image"]]:
    """
    Convertit les pages PDF en images PIL à 300 DPI.
    """
    if not PDF2IMAGE_AVAILABLE:
        raise RuntimeError(
            "pdf2image non installé. "
            "Installer avec : pip install pdf2image"
        )

    kwargs: dict = {"dpi": OCR_DPI, "fmt": "PNG", "thread_count": 2}

    if target_pages:
        # Conversion sélective — plus rapide si peu de pages à traiter
        results: list[tuple[int, "PILImage.Image"]] = []
        for page_num in sorted(target_pages):
            try:
                images = convert_from_path(
                    str(pdf_path),
                    first_page=page_num,
                    last_page=page_num,
                    **kwargs,
                )
                if images:
                    results.append((page_num, images[0]))
            except Exception as exc:
                logger.warning("Conversion page %d échouée : %s", page_num, exc)
        return results
    else:
        # Conversion complète — toutes les pages
        try:
            all_images = convert_from_path(str(pdf_path), **kwargs)
            return [(i + 1, img) for i, img in enumerate(all_images)]
        except Exception as exc:
            raise RuntimeError(f"Échec conversion PDF → images : {exc}") from exc


def _compute_confidence(pages: list[OCRPageResult]) -> float:
    """
    Calcule le score de confiance global de l'OCR.

    Basé sur la moyenne des scores de confiance par page,
    pondérée par le nombre de mots (pages plus denses ont plus de poids).
    """
    if not pages:
        return 0.0

    total_weight = sum(p.word_count for p in pages)
    if total_weight == 0:
        return 0.0

    weighted_sum = sum(p.confidence * p.word_count for p in pages)
    return round(weighted_sum / total_weight, 3)


def _build_tags(result: OCRResult) -> list[str]:
    """Construit les tags de traçabilité pour le résultat OCR."""
    tags: list[str] = [f"PDF_OCR:{result.engine_used.value.upper()}"]

    if any(p.orientation_corrected for p in result.pages):
        tags.append("ORIENTATION_CORRECTED")

    if any(p.is_text_poor for p in result.pages):
        tags.append("LOW_QUALITY_PAGES")

    if any(p.confidence < MIN_OCR_CONFIDENCE for p in result.pages if p.char_count > 0):
        tags.append("LOW_CONFIDENCE")

    # Pages partielles (appelé depuis pdf_native)
    if result.page_count != len(result.pages_processed):
        tags.append("PARTIAL_OCR")

    return tags


# ── Point d'entrée principal ──────────────────────────────────────────────────

def extract_pdf_ocr(
    file_path: str | Path,
    target_pages: Optional[list[int]] = None,
) -> OCRResult:
    """
    Extrait le texte d'un PDF scanné via OCR pour le RAG EnnoSmart.

    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Format non supporté par pdf_ocr : {path.suffix}")

    logger.info(
        "Extraction OCR PDF → %s | pages_cibles=%s",
        path.name,
        target_pages or "toutes",
    )

    result = OCRResult(
        file_name=path.name,
        source_path=str(path.resolve()),
    )

    # ── Détection moteur OCR ──────────────────────────────────────────────
    engine = _detect_available_engine()
    result.engine_used = engine

    if engine == OCREngine.NONE:
        result.extraction_errors.append(
            "Aucun moteur OCR disponible. "
            "Installer surya-ocr : pip install surya-ocr"
        )
        result.confidence_score = 0.0
        result.tags = ["PDF_OCR:NONE", "ERROR:NO_ENGINE"]
        return result

    # ── Conversion PDF → Images ───────────────────────────────────────────
    try:
        page_images = _pdf_to_images(path, target_pages)
    except RuntimeError as exc:
        result.extraction_errors.append(str(exc))
        result.tags = [f"PDF_OCR:{engine.value.upper()}", "ERROR:CONVERSION_FAILED"]
        return result

    if not page_images:
        result.extraction_errors.append("Aucune image extraite du PDF")
        result.tags = [f"PDF_OCR:{engine.value.upper()}", "ERROR:NO_IMAGES"]
        return result

    result.page_count = len(page_images)
    result.pages_processed = [num for num, _ in page_images]

    logger.info(
        "%d page(s) à traiter via %s",
        len(page_images), engine.value,
    )

    # ── OCR page par page ─────────────────────────────────────────────────
    for page_number, image in page_images:
        try:
            page_result = _process_single_page(image, page_number, engine)
            result.pages.append(page_result)
            # Les formules sont maintenant intégrées DANS le chunk (via _build_page_chunk)
            result.text_chunks.append(_build_page_chunk(page_result))

            # Propagation des erreurs non-bloquantes
            result.extraction_errors.extend(page_result.extraction_errors)

            logger.debug(
                "Page %d — %d chars | conf=%.2f | orientation=%s%s",
                page_number,
                page_result.char_count,
                page_result.confidence,
                page_result.orientation_detected.value,
                " [corrigée]" if page_result.orientation_corrected else "",
            )

        except Exception as exc:
            msg = f"Erreur page {page_number} : {exc}"
            logger.warning(msg)
            result.extraction_errors.append(msg)
            result.text_chunks.append(
                f"[PAGE {page_number}] [OCR:{engine.value} | conf:0.00]\n[ERREUR OCR]"
            )

    # ── Post-traitement ───────────────────────────────────────────────────
    result.confidence_score = _compute_confidence(result.pages)
    result.tags = _build_tags(result)

    logger.info(
        "✓ %s — %d pages OCR | score=%.2f | moteur=%s | tags=%s",
        path.name,
        len(result.pages),
        result.confidence_score,
        engine.value,
        result.tags,
    )

    return result


# ── Fusion avec résultat natif (appelée par router.py) ────────────────────────

def merge_native_and_ocr(
    native_chunks: list[str],
    ocr_result: OCRResult,
) -> list[str]:
    """
    Fusionne les chunks natifs et OCR pour les PDFs mixtes.

    Un PDF mixte = certaines pages texte natif + d'autres scannées.
    pdf_native.py a laissé des placeholders "[ERREUR EXTRACTION]"
    sur les pages faibles → on les remplace par le résultat OCR.

    Paramètres:
    
    native_chunks : liste complète de chunks de pdf_native (1 par page)
    ocr_result    : résultat de extract_pdf_ocr sur les pages ciblées

    Retourne:
    
    list[str]
        
    """
    # Index OCR : page_number → chunk OCR
    ocr_index: dict[int, str] = {}
    for page_result, chunk in zip(ocr_result.pages, ocr_result.text_chunks):
        ocr_index[page_result.page_number] = chunk

    merged: list[str] = []
    for i, chunk in enumerate(native_chunks):
        page_number = i + 1  # Chunks sont 1-indexés
        if page_number in ocr_index:
            merged.append(ocr_index[page_number])
            logger.debug("Page %d — remplacée par chunk OCR", page_number)
        else:
            merged.append(chunk)

    return merged


# ── Interface rapide (debug / tests) ──────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.DEBUG)

    if len(sys.argv) < 2:
        print("Usage : python pdf_ocr.py <chemin_vers_pdf> [page1,page2,...]")
        sys.exit(1)

    target: Optional[list[int]] = None
    if len(sys.argv) >= 3:
        target = [int(p) for p in sys.argv[2].split(",")]

    res = extract_pdf_ocr(sys.argv[1], target_pages=target)

    summary = {
        "file":            res.file_name,
        "engine":          res.engine_used.value,
        "pages_processed": res.pages_processed,
        "confidence":      res.confidence_score,
        "tags":            res.tags,
        "errors":          res.extraction_errors,
        "chunks_preview":  [c[:300] + "…" for c in res.text_chunks[:3]],
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))