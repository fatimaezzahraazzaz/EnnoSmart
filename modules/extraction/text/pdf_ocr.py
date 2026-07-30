"""
modules/extraction/text/pdf_ocr.py
──────────────────────────────────────────────────────────────────────────────
Extraction OCR de PDF scannés pour dossiers R&D / CIR.

Intégration pipeline :
  pdf_native.py ──(ocr_needed_pages)──► pdf_ocr.py ──► text_chunks BRUTS
  OU
  router.py ──(PDF 100 % scanné)──────► pdf_ocr.py ──► text_chunks BRUTS

Cette version :
  - rend les pages avec PyMuPDF au lieu de pdf2image ;
  - limite strictement les dimensions et le nombre de pixels ;
  - évite Pillow DecompressionBombError sans désactiver la protection ;
  - ferme les PDF et les images explicitement sous Windows ;
  - conserve Surya 0.17.1 en priorité et Tesseract en fallback.
"""

from __future__ import annotations

import gc
import logging
import math
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import torch
except ImportError:
    torch = None

# Surya lit TORCH_DEVICE pendant son import. Ce choix doit donc être fait avant
# l'import de ses prédicteurs. ``auto`` utilise CUDA lorsqu'il est réellement
# disponible, sans empêcher un fallback CPU propre sur une machine sans GPU.
OCR_SURYA_DEVICE_REQUESTED = os.getenv("OCR_SURYA_DEVICE", "auto").strip().lower()
OCR_ENGINE_REQUESTED = os.getenv("OCR_ENGINE", "auto").strip().lower()
if OCR_ENGINE_REQUESTED not in {"auto", "surya", "tesseract"}:
    OCR_ENGINE_REQUESTED = "auto"
CUDA_AVAILABLE = bool(torch is not None and torch.cuda.is_available())
if OCR_SURYA_DEVICE_REQUESTED == "cpu":
    os.environ["TORCH_DEVICE"] = "cpu"
elif CUDA_AVAILABLE:
    os.environ.setdefault("TORCH_DEVICE", "cuda")


# ── Rendu PDF sécurisé avec PyMuPDF ──────────────────────────────────────────

try:
    import fitz  # PyMuPDF

    PYMUPDF_AVAILABLE = True
except ImportError:
    fitz = None
    PYMUPDF_AVAILABLE = False


# ── Surya OCR ─────────────────────────────────────────────────────────────────
# Important :
# Surya peut échouer avec autre chose qu'un ImportError, par exemple lorsque
# torch et torchvision sont incompatibles. Dans ce cas, le module OCR ne doit
# pas planter : on désactive Surya et on utilise Tesseract en fallback.

FoundationPredictor = None
RecognitionPredictor = None
DetectionPredictor = None

SURYA_AVAILABLE = False
SURYA_IMPORT_ERROR: str | None = None

try:
    from surya.foundation import FoundationPredictor
    from surya.recognition import RecognitionPredictor
    from surya.detection import DetectionPredictor

    SURYA_AVAILABLE = True

except Exception as exc:
    SURYA_AVAILABLE = False
    SURYA_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

# ── Pillow ─────────────────────────────────────────────────────────────────────
try:
    from PIL import Image as PILImage
    from PIL import ImageFilter, ImageOps

    PIL_AVAILABLE = True
except ImportError:
    PILImage = None
    ImageFilter = None
    ImageOps = None
    PIL_AVAILABLE = False


# ── Fallback Tesseract ─────────────────────────────────────────────────────────
def _discover_tesseract_cmd() -> str:
    """Résout Tesseract sans chemin lié à un utilisateur particulier."""
    configured = os.getenv("TESSERACT_CMD", "").strip()
    if configured:
        return configured
    executable = shutil.which("tesseract")
    if executable:
        return executable
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        Path.home() / "AppData" / "Local" / "Programs"
        / "Tesseract-OCR" / "tesseract.exe",
    ]
    for candidate in candidates:
        if str(candidate) and candidate.is_file():
            return str(candidate)
    return ""


_DISCOVERED_TESSERACT_CMD = _discover_tesseract_cmd()

try:
    import pytesseract

    if _DISCOVERED_TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = _DISCOVERED_TESSERACT_CMD

    TESSERACT_AVAILABLE = True

except ImportError:
    pytesseract = None
    TESSERACT_AVAILABLE = False

# ── Compatibilité Surya ───────────────────────────────────────────────────────
#
# Ce fichier utilise l'API Surya v1 :
#   FoundationPredictor + RecognitionPredictor + DetectionPredictor
#
# Combinaison validée :
#   surya-ocr==0.17.1
#   transformers==4.57.3
#
# Ne pas mettre Surya à niveau sans adapter cette partie.

EXPECTED_SURYA_MAJOR_MINOR = "0.17"
EXPECTED_TRANSFORMERS_VERSION = "4.57.3"


def _pkg_version(package_name: str) -> str:
    """Retourne la version installée d'un package, ou ``unknown``."""
    try:
        return metadata.version(package_name)
    except Exception:
        return "unknown"


def _surya_env_versions() -> str:
    return (
        f"surya-ocr={_pkg_version('surya-ocr')} | "
        f"transformers={_pkg_version('transformers')}"
    )


def _surya_runtime_device() -> str:
    if torch is None:
        return "torch_absent"
    if torch.cuda.is_available() and os.getenv("TORCH_DEVICE", "").lower().startswith("cuda"):
        try:
            return f"cuda:0 ({torch.cuda.get_device_name(0)})"
        except Exception:
            return "cuda:0"
    return "cpu"


def _is_legacy_surya_env_compatible() -> bool:
    """
    Vérifie que l'environnement correspond au code Surya v1 actuel.

    L'erreur ``SuryaDecoderConfig has no attribute pad_token_id`` apparaît
    généralement avec une version incompatible de Transformers.
    """
    surya_version = _pkg_version("surya-ocr")
    transformers_version = _pkg_version("transformers")

    if (
        surya_version != "unknown"
        and not surya_version.startswith(EXPECTED_SURYA_MAJOR_MINOR)
    ):
        logger.error(
            "Version Surya incompatible avec ce code : %s. "
            "Installe : python -m pip install --force-reinstall "
            "\"surya-ocr==0.17.1\" \"transformers==4.57.3\"",
            _surya_env_versions(),
        )
        return False

    if (
        transformers_version != "unknown"
        and transformers_version != EXPECTED_TRANSFORMERS_VERSION
    ):
        logger.error(
            "Version Transformers non validée pour Surya v1 : %s. "
            "Installe : python -m pip install --force-reinstall "
            "\"surya-ocr==0.17.1\" \"transformers==4.57.3\"",
            _surya_env_versions(),
        )
        return False

    return True


# ── Configuration ─────────────────────────────────────────────────────────────

# 180 DPI est insuffisant pour les petits caractères des articles scientifiques
# à deux colonnes. Le rendu reste borné par les limites ci-dessous.
OCR_DPI = max(150, int(os.getenv("OCR_DPI", "300")))

# Limites de sécurité par page OCR.
OCR_MAX_SIDE = max(1800, int(os.getenv("OCR_MAX_SIDE", "4500")))
OCR_MAX_PIXELS = max(3_000_000, int(os.getenv("OCR_MAX_PIXELS", "14000000")))

# Langues documentaires attendues.
OCR_LANGUAGES = ["fr", "en"]

MIN_OCR_CONFIDENCE = float(os.getenv("MIN_OCR_CONFIDENCE", "0.60"))
MIN_CHARS_OCR_PAGE = int(os.getenv("MIN_CHARS_OCR_PAGE", "30"))

# La confiance brute des moteurs n'est pas comparable d'un moteur à l'autre.
# On calcule donc aussi une qualité textuelle commune et on retente les pages
# faibles avec Tesseract avant de les exposer au RAG.
OCR_RETRY_QUALITY = float(os.getenv("OCR_RETRY_QUALITY", "0.72"))
MIN_OCR_QUALITY = float(os.getenv("MIN_OCR_QUALITY", "0.70"))
OCR_REJECT_LOW_QUALITY = os.getenv("OCR_REJECT_LOW_QUALITY", "1") == "1"
OCR_TESSERACT_LANG = os.getenv("OCR_TESSERACT_LANG", "eng+fra").strip() or "eng+fra"
OCR_TESSERACT_PSMS = tuple(
    psm for psm in (
        int(value.strip())
        for value in os.getenv("OCR_TESSERACT_PSMS", "3,4").split(",")
        if value.strip().isdigit()
    )
    if psm in {1, 3, 4, 6, 11, 12}
) or (3, 4)

# Tesseract peut être configuré dans C:\EnnoSmart\.env :
# TESSERACT_CMD=C:/Users/dell/AppData/Local/Programs/Tesseract-OCR/tesseract.exe
TESSERACT_CMD = os.getenv(
    "TESSERACT_CMD",
    _DISCOVERED_TESSERACT_CMD,
).strip()

if TESSERACT_AVAILABLE and TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


def _bounded_positive_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


# Une RTX 6 Go peut traiter deux pages scientifiques de taille limitée sans
# pression mémoire excessive. En cas d'OOM, le lot est automatiquement rejoué
# page par page : aucune page n'est perdue.
OCR_SURYA_BATCH_SIZE = _bounded_positive_int(
    "OCR_SURYA_BATCH_SIZE", 1, minimum=1, maximum=8
)

# L'OSD Tesseract est CPU et coûteux. La première page suffit presque toujours
# pour un article scientifique dont toutes les pages ont la même orientation.
# Mettre ``all`` pour un PDF hétérogène ou ``none`` pour privilégier la vitesse.
OCR_ORIENTATION_MODE = os.getenv("OCR_ORIENTATION_MODE", "first_page").strip().lower()
if OCR_ORIENTATION_MODE not in {"none", "first_page", "all"}:
    OCR_ORIENTATION_MODE = "first_page"


# ── Enums ─────────────────────────────────────────────────────────────────────

class OCREngine(str, Enum):
    SURYA = "surya"
    TESSERACT = "tesseract"
    MIXED = "mixed"
    NONE = "none"


class PageOrientation(str, Enum):
    NORMAL = "normal"
    ROTATED90 = "rotated_90"
    ROTATED180 = "rotated_180"
    ROTATED270 = "rotated_270"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class OCRPageResult:
    """Résultat OCR pour une page individuelle."""

    page_number: int
    raw_text: str
    confidence: float
    engine_used: OCREngine
    orientation_detected: PageOrientation
    orientation_corrected: bool
    char_count: int
    is_text_poor: bool
    word_count: int
    quality_score: float = 0.0
    selection_reason: str = ""
    extraction_errors: list[str] = field(default_factory=list)
    images_descriptions: list[dict] = field(default_factory=list)


@dataclass
class OCRResult:
    """Résultat complet d'extraction OCR d'un PDF scanné."""

    file_name: str
    source_path: str
    file_type: str = "pdf_ocr"

    # Sortie principale pour le RAG.
    text_chunks: list[str] = field(default_factory=list)

    # Détail page par page.
    pages: list[OCRPageResult] = field(default_factory=list)

    # Moteur réellement utilisé.
    engine_used: OCREngine = OCREngine.NONE

    # Statistiques.
    page_count: int = 0
    pages_processed: list[int] = field(default_factory=list)

    # Qualité et traçabilité.
    confidence_score: float = 0.0
    quality_score: float = 0.0
    tags: list[str] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)


# ── Cache des modèles Surya ───────────────────────────────────────────────────

class _SuryaModelCache:
    """Charge les modèles Surya une seule fois par processus Python."""

    _foundation_predictor = None
    _recognition_predictor = None
    _detection_predictor = None
    _loaded: bool = False

    @classmethod
    def load(cls) -> bool:
        if cls._loaded:
            return True

        if not SURYA_AVAILABLE:
            logger.error(
                "Surya OCR n'est pas importable. %s",
                _surya_env_versions(),
            )
            return False

        if not _is_legacy_surya_env_compatible():
            return False

        try:
            logger.info(
                "Chargement des modèles Surya OCR | %s",
                _surya_env_versions(),
            )

            foundation = FoundationPredictor()
            recognition = RecognitionPredictor(foundation)
            detection = DetectionPredictor()

            cls._foundation_predictor = foundation
            cls._recognition_predictor = recognition
            cls._detection_predictor = detection
            cls._loaded = True

            logger.info(
                "Modèles Surya chargés | device=%s | batch_size=%d | %s",
                _surya_runtime_device(),
                OCR_SURYA_BATCH_SIZE,
                _surya_env_versions(),
            )
            return True

        except AttributeError as exc:
            cls._reset()

            if "pad_token_id" in str(exc):
                logger.error(
                    "Échec chargement Surya : %s. "
                    "Correctif : python -m pip install --force-reinstall "
                    "\"surya-ocr==0.17.1\" \"transformers==4.57.3\"",
                    exc,
                )
            else:
                logger.error("Échec chargement Surya : %s", exc)

            return False

        except Exception as exc:
            cls._reset()
            logger.error(
                "Échec chargement Surya : %s | %s",
                exc,
                _surya_env_versions(),
            )
            return False

    @classmethod
    def _reset(cls) -> None:
        cls._foundation_predictor = None
        cls._recognition_predictor = None
        cls._detection_predictor = None
        cls._loaded = False

    @classmethod
    def get(cls) -> tuple:
        return (
            cls._foundation_predictor,
            cls._recognition_predictor,
            cls._detection_predictor,
        )


# ── Détection moteur OCR ──────────────────────────────────────────────────────

def _is_tesseract_operational() -> bool:
    global TESSERACT_CMD
    if not TESSERACT_AVAILABLE:
        return False

    try:
        runtime_cmd = (
            os.getenv("TESSERACT_CMD", "").strip()
            or TESSERACT_CMD
            or _discover_tesseract_cmd()
        )
        if runtime_cmd:
            TESSERACT_CMD = runtime_cmd
            pytesseract.pytesseract.tesseract_cmd = runtime_cmd
        pytesseract.get_tesseract_version()
        return True
    except Exception as exc:
        logger.warning(
            "Tesseract importé mais inutilisable | cmd=%s | erreur=%s",
            TESSERACT_CMD or "PATH système",
            exc,
        )
        return False


def _detect_available_engine() -> OCREngine:
    """
    Détecte le moteur OCR disponible.

    ``OCR_ENGINE`` permet de choisir explicitement ``tesseract`` ou ``surya``.
    En mode ``auto``, Surya reste prioritaire lorsqu'il est opérationnel.

    Si Surya est installé mais cassé à cause d'une incompatibilité
    torch/torchvision, le pipeline continue automatiquement avec Tesseract.
    """
    strict_surya = os.getenv("OCR_STRICT_SURYA", "0") == "1"

    if OCR_ENGINE_REQUESTED == "tesseract":
        if _is_tesseract_operational():
            logger.info("Moteur OCR imposé par OCR_ENGINE=tesseract.")
            return OCREngine.TESSERACT
        logger.error("OCR_ENGINE=tesseract mais Tesseract est indisponible.")
        return OCREngine.NONE

    if OCR_ENGINE_REQUESTED in {"auto", "surya"} and SURYA_AVAILABLE:
        try:
            if _SuryaModelCache.load():
                return OCREngine.SURYA
        except Exception as exc:
            logger.exception(
                "Surya a échoué pendant son initialisation : %s",
                exc,
            )

    if SURYA_IMPORT_ERROR:
        logger.warning(
            "Surya désactivé à cause d'une erreur d'import : %s",
            SURYA_IMPORT_ERROR,
        )

    if OCR_ENGINE_REQUESTED == "surya" or strict_surya:
        logger.error(
            "Surya demandé mais indisponible. "
            "Fallback Tesseract interdit."
        )
        return OCREngine.NONE

    if _is_tesseract_operational():
        logger.warning("Surya indisponible → fallback Tesseract.")
        return OCREngine.TESSERACT

    logger.error(
        "Aucun moteur OCR utilisable. "
        "Surya disponible=%s | Tesseract disponible=%s",
        SURYA_AVAILABLE,
        TESSERACT_AVAILABLE,
    )

    return OCREngine.NONE

# ── Rendu PDF sécurisé ────────────────────────────────────────────────────────

def _validate_render_dependencies() -> None:
    if not PYMUPDF_AVAILABLE:
        raise RuntimeError(
            "PyMuPDF indisponible. Installer avec : python -m pip install pymupdf"
        )

    if not PIL_AVAILABLE:
        raise RuntimeError(
            "Pillow indisponible. Installer avec : python -m pip install pillow"
        )


def _get_pdf_page_selection(
    pdf_path: Path,
    target_pages: Optional[list[int]],
) -> tuple[int, list[int]]:
    """
    Retourne ``(nombre_total_de_pages, pages_à_traiter)``.

    Les numéros de pages sont 1-indexés.
    """
    _validate_render_dependencies()

    try:
        with fitz.open(str(pdf_path)) as document:
            total_pages = len(document)
    except Exception as exc:
        raise RuntimeError(f"Impossible d'ouvrir le PDF : {exc}") from exc

    if total_pages <= 0:
        return 0, []

    if not target_pages:
        return total_pages, list(range(1, total_pages + 1))

    selected_pages: list[int] = []
    seen: set[int] = set()

    for value in target_pages:
        try:
            page_number = int(value)
        except (TypeError, ValueError):
            logger.warning("Numéro de page OCR ignoré : %r", value)
            continue

        if page_number < 1 or page_number > total_pages:
            logger.warning(
                "Page OCR hors limites ignorée : %d (PDF=%d pages)",
                page_number,
                total_pages,
            )
            continue

        if page_number not in seen:
            seen.add(page_number)
            selected_pages.append(page_number)

    selected_pages.sort()
    return total_pages, selected_pages


def _safe_render_scale(
    page_width_points: float,
    page_height_points: float,
) -> float:
    """
    Calcule un facteur de rendu respectant OCR_MAX_SIDE et OCR_MAX_PIXELS.

    Les dimensions PDF sont exprimées en points, avec 72 points par pouce.
    """
    width = max(float(page_width_points), 1.0)
    height = max(float(page_height_points), 1.0)

    scale = max(float(OCR_DPI) / 72.0, 0.10)

    rendered_width = width * scale
    rendered_height = height * scale

    longest_side = max(rendered_width, rendered_height)
    if longest_side > OCR_MAX_SIDE:
        scale *= OCR_MAX_SIDE / longest_side

    rendered_width = width * scale
    rendered_height = height * scale
    rendered_pixels = rendered_width * rendered_height

    if rendered_pixels > OCR_MAX_PIXELS:
        scale *= math.sqrt(OCR_MAX_PIXELS / rendered_pixels)

    # Évite un facteur nul tout en conservant les limites calculées.
    return max(scale, 0.01)


def _render_pdf_page(
    pdf_path: Path,
    page_number: int,
) -> "PILImage.Image":
    """
    Rend une page PDF en image RGB indépendante.

    Le PDF PyMuPDF est fermé avant le retour, ce qui évite le verrouillage du
    fichier temporaire sous Windows.
    """
    _validate_render_dependencies()

    try:
        with fitz.open(str(pdf_path)) as document:
            if page_number < 1 or page_number > len(document):
                raise ValueError(
                    f"Page {page_number} hors limites pour un PDF de "
                    f"{len(document)} page(s)"
                )

            page = document.load_page(page_number - 1)
            rect = page.rect

            scale = _safe_render_scale(rect.width, rect.height)
            matrix = fitz.Matrix(scale, scale)

            pixmap = page.get_pixmap(
                matrix=matrix,
                colorspace=fitz.csRGB,
                alpha=False,
            )

            width = int(pixmap.width)
            height = int(pixmap.height)
            pixels_count = width * height

            if (
                width <= 0
                or height <= 0
                or max(width, height) > OCR_MAX_SIDE + 2
                or pixels_count > OCR_MAX_PIXELS + max(width, height)
            ):
                raise RuntimeError(
                    f"Page {page_number} trop grande après limitation : "
                    f"{width}x{height}, pixels={pixels_count}"
                )

            # frombytes copie les pixels : l'image reste indépendante après
            # fermeture du document et destruction du Pixmap.
            image = PILImage.frombytes(
                "RGB",
                (width, height),
                pixmap.samples,
            )

            logger.info(
                "Page PDF rendue | page=%d | size=%dx%d | pixels=%d | "
                "scale=%.4f | dpi_cible=%d",
                page_number,
                width,
                height,
                pixels_count,
                scale,
                OCR_DPI,
            )

            del pixmap
            del page
            return image

    except Exception as exc:
        raise RuntimeError(
            f"Échec rendu de la page {page_number} : {exc}"
        ) from exc


# ── Orientation ────────────────────────────────────────────────────────────────

def _detect_orientation(image: "PILImage.Image") -> PageOrientation:
    """Détecte l'orientation d'une page via Tesseract OSD."""
    if not _is_tesseract_operational():
        return PageOrientation.NORMAL

    try:
        osd = pytesseract.image_to_osd(
            image,
            output_type=pytesseract.Output.DICT,
        )
        angle = int(osd.get("rotate", 0) or 0)

        mapping = {
            0: PageOrientation.NORMAL,
            90: PageOrientation.ROTATED90,
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
    """Corrige l'orientation et indique si une nouvelle image a été créée."""
    if orientation == PageOrientation.NORMAL:
        return image, False

    rotation_map = {
        PageOrientation.ROTATED90: -90,
        PageOrientation.ROTATED180: 180,
        PageOrientation.ROTATED270: 90,
    }

    angle = rotation_map.get(orientation, 0)
    corrected = image.rotate(angle, expand=True)

    logger.debug(
        "Orientation corrigée : %s (rotation %d°)",
        orientation.value,
        angle,
    )
    return corrected, True


# ── OCR Surya / Tesseract ─────────────────────────────────────────────────────


def _safe_bbox(value: object) -> Optional[tuple[float, float, float, float]]:
    """Normalise une bbox ou un polygone sans dépendre du schéma du moteur."""
    if value is None:
        return None
    try:
        if isinstance(value, dict):
            if all(key in value for key in ("x0", "y0", "x1", "y1")):
                return tuple(float(value[key]) for key in ("x0", "y0", "x1", "y1"))
            value = value.get("bbox") or value.get("polygon")
        values = list(value)
        if len(values) == 4 and all(isinstance(item, (int, float)) for item in values):
            x0, y0, x1, y1 = (float(item) for item in values)
            return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        points = [list(point) for point in values if isinstance(point, (list, tuple))]
        if points and all(len(point) >= 2 for point in points):
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            return min(xs), min(ys), max(xs), max(ys)
    except (TypeError, ValueError):
        return None
    return None


def _order_line_records(
    records: list[dict],
    page_width: Optional[float],
) -> list[dict]:
    """Restaure l'ordre de lecture, colonne gauche puis colonne droite.

    Les lignes pleine largeur servent de séparateurs de bandes (titre, résumé,
    légende). Si les coordonnées sont absentes, l'ordre du moteur est conservé.
    """
    if not records:
        return []
    valid = [record for record in records if record.get("bbox") is not None]
    if len(valid) < max(3, int(len(records) * 0.60)):
        return sorted(records, key=lambda record: record.get("order", 0))

    inferred_width = max(record["bbox"][2] for record in valid)
    width = max(float(page_width or inferred_width), inferred_width, 1.0)
    middle = width / 2.0

    def center_y(record: dict) -> float:
        box = record["bbox"]
        return (box[1] + box[3]) / 2.0

    def center_x(record: dict) -> float:
        box = record["bbox"]
        return (box[0] + box[2]) / 2.0

    full_width: list[dict] = []
    column_lines: list[dict] = []
    no_bbox: list[dict] = []
    for record in records:
        box = record.get("bbox")
        if box is None:
            no_bbox.append(record)
            continue
        line_width = max(0.0, box[2] - box[0])
        crosses_gutter = box[0] < width * 0.32 and box[2] > width * 0.68
        if line_width >= width * 0.70 or (crosses_gutter and line_width >= width * 0.55):
            full_width.append(record)
        else:
            column_lines.append(record)

    def sort_band(items: list[dict]) -> list[dict]:
        if not items:
            return []
        left = [record for record in items if center_x(record) < middle]
        right = [record for record in items if center_x(record) >= middle]
        # On ne force deux colonnes que si les deux côtés contiennent un vrai
        # paragraphe ; cela évite de casser une page simple avec une note isolée.
        if len(left) >= 2 and len(right) >= 2:
            return (
                sorted(left, key=lambda record: (center_y(record), record["bbox"][0]))
                + sorted(right, key=lambda record: (center_y(record), record["bbox"][0]))
            )
        return sorted(items, key=lambda record: (center_y(record), record["bbox"][0]))

    ordered: list[dict] = []
    remaining = list(column_lines)
    for separator in sorted(full_width, key=lambda record: (center_y(record), record["bbox"][0])):
        separator_y = center_y(separator)
        band = [record for record in remaining if center_y(record) < separator_y]
        ordered.extend(sort_band(band))
        selected_ids = {id(record) for record in band}
        remaining = [record for record in remaining if id(record) not in selected_ids]
        ordered.append(separator)
    ordered.extend(sort_band(remaining))
    ordered.extend(sorted(no_bbox, key=lambda record: record.get("order", 0)))
    return ordered


def _text_quality_score(text: str, confidence: float) -> float:
    """Score commun aux moteurs : confiance + lisibilité du texte produit."""
    clean = (text or "").strip()
    if not clean:
        return 0.0
    words = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", clean, flags=re.UNICODE)
    if not words:
        return 0.0
    alpha_chars = sum(character.isalpha() for character in clean)
    visible_chars = sum(not character.isspace() for character in clean) or 1
    alpha_ratio = alpha_chars / visible_chars
    reasonable_words = sum(2 <= len(word) <= 28 for word in words) / len(words)
    useful_lines = [line.strip() for line in clean.splitlines() if len(line.split()) >= 3]
    line_ratio = min(1.0, len(useful_lines) / max(1.0, len(words) / 12.0))

    overlong_tokens = sum(len(token) > 35 for token in clean.split()) / max(1, len(words))
    symbol_ratio = sum(
        not (character.isalnum() or character.isspace() or character in ".,;:!?%()[]/-+'°λµ×=")
        for character in clean
    ) / max(1, len(clean))
    fused_case = len(re.findall(r"[a-zà-ÿ]{4,}[A-Z]{2,}[a-zA-Z]*", clean))
    broken_fragments = len(re.findall(r"\b[a-z]{5,}\.\s+[a-z]{4,}\b", clean))

    score = (
        0.55 * max(0.0, min(1.0, confidence))
        + 0.20 * min(1.0, alpha_ratio / 0.80)
        + 0.15 * reasonable_words
        + 0.10 * line_ratio
    )
    score -= min(0.20, overlong_tokens * 1.5)
    score -= min(0.15, max(0.0, symbol_ratio - 0.08) * 1.5)
    score -= min(0.15, 0.025 * (fused_case + broken_fragments))
    return round(max(0.0, min(1.0, score)), 3)


def _surya_prediction_to_text(
    prediction: object,
    image_size: Optional[tuple[int, int]] = None,
) -> tuple[str, float]:
    """Normalise Surya et reconstruit l'ordre des colonnes via les bbox."""
    text_lines = getattr(prediction, "text_lines", None) or []
    records: list[dict] = []

    for order, line in enumerate(text_lines):
        line_text = str(getattr(line, "text", "") or "").strip()
        if not line_text:
            continue
        try:
            confidence = float(getattr(line, "confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        bbox = _safe_bbox(getattr(line, "bbox", None))
        if bbox is None:
            bbox = _safe_bbox(getattr(line, "polygon", None))
        records.append({
            "text": line_text,
            "confidence": max(0.0, min(1.0, confidence)),
            "bbox": bbox,
            "order": order,
        })

    ordered = _order_line_records(records, image_size[0] if image_size else None)
    total_weight = sum(max(1, len(record["text"])) for record in ordered)
    average_confidence = (
        sum(record["confidence"] * max(1, len(record["text"])) for record in ordered)
        / total_weight
        if total_weight
        else 0.0
    )

    return "\n".join(record["text"] for record in ordered), round(average_confidence, 3)


def _ocr_pages_surya(
    images: list["PILImage.Image"],
    page_numbers: list[int],
) -> list[tuple[str, float]]:
    """Applique Surya sur un petit lot de pages, avec reprise OOM sûre."""
    _, recognition_predictor, detection_predictor = _SuryaModelCache.get()
    if not images or recognition_predictor is None or detection_predictor is None:
        return [("", 0.0) for _ in images]

    try:
        predictions = recognition_predictor(images, det_predictor=detection_predictor) or []
        results = [
            _surya_prediction_to_text(prediction, image.size)
            for prediction, image in zip(predictions, images)
        ]
        if len(results) < len(images):
            results.extend([("", 0.0)] * (len(images) - len(results)))
        return results[: len(images)]
    except RuntimeError as exc:
        is_oom = "out of memory" in str(exc).lower()
        if is_oom and len(images) > 1:
            logger.warning(
                "Surya CUDA OOM sur le lot %s ; reprise page par page.",
                page_numbers,
            )
            if torch is not None and torch.cuda.is_available():
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
            results: list[tuple[str, float]] = []
            for image, page_number in zip(images, page_numbers):
                results.extend(_ocr_pages_surya([image], [page_number]))
            return results
        logger.warning("Surya OCR pages %s : %s", page_numbers, exc)
    except Exception as exc:
        logger.warning("Surya OCR pages %s : %s", page_numbers, exc)

    return [("", 0.0) for _ in images]


def _ocr_page_surya(
    image: "PILImage.Image",
    page_number: int,
) -> tuple[str, float]:
    """Compatibilité appel unitaire ; le flux principal utilise des lots."""
    return _ocr_pages_surya([image], [page_number])[0]


def _enhance_for_tesseract(image: "PILImage.Image") -> "PILImage.Image":
    """Améliore le contraste des petits caractères sans binarisation brutale."""
    grayscale = ImageOps.grayscale(image)
    enhanced = ImageOps.autocontrast(grayscale, cutoff=1)
    enhanced = enhanced.filter(ImageFilter.SHARPEN)
    return enhanced


def _resolve_tesseract_language() -> str:
    """Ne demande jamais une langue absente, sinon Tesseract échoue en silence."""
    requested = [value.strip() for value in OCR_TESSERACT_LANG.split("+") if value.strip()]
    try:
        installed = set(pytesseract.get_languages(config=""))
    except Exception as exc:
        logger.warning("Impossible de lister les langues Tesseract : %s", exc)
        return OCR_TESSERACT_LANG
    usable = [language for language in requested if language in installed]
    if usable:
        resolved = "+".join(usable)
        if resolved != OCR_TESSERACT_LANG:
            logger.warning(
                "Langues Tesseract ajustées | demandées=%s | utilisées=%s | installées=%s",
                OCR_TESSERACT_LANG,
                resolved,
                sorted(installed),
            )
        return resolved
    for fallback in ("eng", "fra"):
        if fallback in installed:
            logger.warning(
                "Langue Tesseract demandée indisponible ; fallback=%s | installées=%s",
                fallback,
                sorted(installed),
            )
            return fallback
    raise RuntimeError(
        "Aucune langue Tesseract eng/fra disponible. "
        f"Langues installées : {sorted(installed)}"
    )


def _tesseract_data_to_candidate(
    data: dict,
    image_width: int,
) -> tuple[str, float, float]:
    """Reconstruit les lignes et colonnes depuis image_to_data."""
    grouped: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
    texts = data.get("text", []) or []
    for index, value in enumerate(texts):
        word = str(value or "").strip()
        if not word:
            continue
        try:
            confidence = float(data.get("conf", [])[index])
        except (IndexError, TypeError, ValueError):
            confidence = -1.0
        if confidence < 0:
            continue
        try:
            left = int(data.get("left", [])[index])
            top = int(data.get("top", [])[index])
            width = int(data.get("width", [])[index])
            height = int(data.get("height", [])[index])
            block = int(data.get("block_num", [])[index])
            paragraph = int(data.get("par_num", [])[index])
            line = int(data.get("line_num", [])[index])
        except (IndexError, TypeError, ValueError):
            left, top, width, height = 0, index, 1, 1
            block, paragraph, line = 0, 0, index
        grouped[(block, paragraph, line)].append({
            "text": word,
            "confidence": max(0.0, min(1.0, confidence / 100.0)),
            "bbox": (left, top, left + width, top + height),
        })

    records: list[dict] = []
    for order, words in enumerate(grouped.values()):
        words.sort(key=lambda item: item["bbox"][0])
        line_text = " ".join(item["text"] for item in words)
        x0 = min(item["bbox"][0] for item in words)
        y0 = min(item["bbox"][1] for item in words)
        x1 = max(item["bbox"][2] for item in words)
        y1 = max(item["bbox"][3] for item in words)
        weight = sum(max(1, len(item["text"])) for item in words)
        line_confidence = sum(
            item["confidence"] * max(1, len(item["text"])) for item in words
        ) / max(1, weight)
        records.append({
            "text": line_text,
            "confidence": line_confidence,
            "bbox": (x0, y0, x1, y1),
            "order": order,
        })

    ordered = _order_line_records(records, image_width)
    total_weight = sum(max(1, len(record["text"])) for record in ordered)
    confidence = (
        sum(record["confidence"] * max(1, len(record["text"])) for record in ordered)
        / total_weight
        if total_weight
        else 0.0
    )
    text = "\n".join(record["text"] for record in ordered).strip()
    quality = _text_quality_score(text, confidence)
    return text, round(confidence, 3), quality


def _ocr_page_tesseract(
    image: "PILImage.Image",
    page_number: int,
) -> tuple[str, float]:
    """Teste plusieurs segmentations et conserve la sortie la plus lisible."""
    if not _is_tesseract_operational():
        return "", 0.0

    enhanced = None
    try:
        language = _resolve_tesseract_language()
        enhanced = _enhance_for_tesseract(image)
        candidates: list[tuple[float, str, float, str]] = []
        candidate_errors: list[str] = []
        attempts: list[tuple[str, "PILImage.Image", int]] = [("raw", image, 3)]
        attempts.extend(("enhanced", enhanced, psm) for psm in OCR_TESSERACT_PSMS)

        seen_attempts: set[tuple[str, int]] = set()
        for variant, candidate_image, psm in attempts:
            attempt_key = (variant, psm)
            if attempt_key in seen_attempts:
                continue
            seen_attempts.add(attempt_key)
            try:
                data = pytesseract.image_to_data(
                    candidate_image,
                    lang=language,
                    config=f"--oem 1 --psm {psm} -c preserve_interword_spaces=1",
                    output_type=pytesseract.Output.DICT,
                )
                text, confidence, quality = _tesseract_data_to_candidate(
                    data,
                    candidate_image.width,
                )
                if text:
                    candidates.append((quality, text, confidence, f"{variant}:psm{psm}"))
            except Exception as exc:
                candidate_errors.append(
                    f"{variant}:psm{psm}:{type(exc).__name__}:{exc}"
                )
                logger.debug(
                    "Tesseract candidat échoué | page=%d | variante=%s | psm=%d | %s",
                    page_number,
                    variant,
                    psm,
                    exc,
                )

        if not candidates:
            logger.warning(
                "Tesseract n'a produit aucun candidat | page=%d | cmd=%s | "
                "lang=%s | erreurs=%s",
                page_number,
                TESSERACT_CMD or "PATH système",
                language,
                candidate_errors[:3],
            )
            return "", 0.0
        quality, text, confidence, strategy = max(
            candidates,
            key=lambda item: (item[0], item[2], len(item[1])),
        )
        logger.info(
            "Tesseract sélection | page=%d | stratégie=%s | conf=%.3f | qualité=%.3f",
            page_number,
            strategy,
            confidence,
            quality,
        )
        return text, confidence

    except Exception as exc:
        logger.warning(
            "Tesseract page %d : %s",
            page_number,
            exc,
        )
        return "", 0.0
    finally:
        if enhanced is not None:
            try:
                enhanced.close()
            except Exception:
                pass


def _page_result_from_ocr(
    *,
    page_number: int,
    raw_text: str,
    confidence: float,
    engine: OCREngine,
    orientation: PageOrientation,
    was_corrected: bool,
    errors: Optional[list[str]] = None,
    selection_reason: str = "",
) -> OCRPageResult:
    """Construit une sortie page cohérente pour l'OCR unitaire ou par lot."""
    errors = list(errors or [])
    raw_text = raw_text or ""

    character_count = len(raw_text.strip())
    word_count = len(raw_text.split())
    quality_score = _text_quality_score(raw_text, confidence)
    is_text_poor = (
        character_count < MIN_CHARS_OCR_PAGE
        or quality_score < MIN_OCR_QUALITY
    )

    if character_count == 0:
        errors.append(f"Page {page_number} : aucun texte OCR extrait")

    if confidence < MIN_OCR_CONFIDENCE and character_count > 0:
        logger.warning(
            "Page %d : confiance OCR faible %.2f (seuil %.2f)",
            page_number,
            confidence,
            MIN_OCR_CONFIDENCE,
        )
        errors.append(
            f"Page {page_number} : confiance OCR faible "
            f"({confidence:.2f})"
        )
    if quality_score < MIN_OCR_QUALITY and character_count > 0:
        logger.warning(
            "Page %d : qualité OCR faible %.2f (seuil %.2f)",
            page_number,
            quality_score,
            MIN_OCR_QUALITY,
        )
        errors.append(
            f"Page {page_number} : qualité OCR faible "
            f"({quality_score:.2f})"
        )

    return OCRPageResult(
        page_number=page_number,
        raw_text=raw_text,
        confidence=confidence,
        engine_used=engine,
        orientation_detected=orientation,
        orientation_corrected=was_corrected,
        char_count=character_count,
        is_text_poor=is_text_poor,
        word_count=word_count,
        quality_score=quality_score,
        selection_reason=selection_reason or f"{engine.value}_primary",
        extraction_errors=errors,
    )


def _process_single_page(
    image: "PILImage.Image",
    page_number: int,
    engine: OCREngine,
) -> OCRPageResult:
    """Traite une page unitaire (Tesseract ou compatibilité Surya)."""
    errors: list[str] = []
    orientation = _detect_orientation(image)
    corrected_image, was_corrected = _correct_orientation(image, orientation)
    raw_text = ""
    confidence = 0.0

    try:
        if engine == OCREngine.SURYA:
            raw_text, confidence = _ocr_page_surya(corrected_image, page_number)
        elif engine == OCREngine.TESSERACT:
            raw_text, confidence = _ocr_page_tesseract(corrected_image, page_number)
        else:
            errors.append(f"Page {page_number} : aucun moteur OCR disponible")
    finally:
        if was_corrected and corrected_image is not image:
            try:
                corrected_image.close()
            except Exception:
                pass

    return _page_result_from_ocr(
        page_number=page_number,
        raw_text=raw_text,
        confidence=confidence,
        engine=engine,
        orientation=orientation,
        was_corrected=was_corrected,
        errors=errors,
    )


def _orientation_for_surya_page(
    image: "PILImage.Image",
    document_orientation: Optional[PageOrientation],
) -> PageOrientation:
    if OCR_ORIENTATION_MODE == "none":
        return PageOrientation.NORMAL
    if OCR_ORIENTATION_MODE == "first_page" and document_orientation is not None:
        return document_orientation
    return _detect_orientation(image)


def _process_surya_batch(
    rendered_pages: list[tuple[int, "PILImage.Image"]],
    document_orientation: Optional[PageOrientation],
) -> tuple[list[OCRPageResult], Optional[PageOrientation]]:
    """Prépare, infère et libère un lot GPU Surya borné."""
    prepared: list[tuple[int, "PILImage.Image", "PILImage.Image", PageOrientation, bool]] = []
    try:
        for page_number, image in rendered_pages:
            orientation = _orientation_for_surya_page(image, document_orientation)
            if OCR_ORIENTATION_MODE == "first_page" and document_orientation is None:
                document_orientation = orientation
            corrected_image, was_corrected = _correct_orientation(image, orientation)
            prepared.append((page_number, image, corrected_image, orientation, was_corrected))

        texts_and_scores = _ocr_pages_surya(
            [item[2] for item in prepared],
            [item[0] for item in prepared],
        )
        results: list[OCRPageResult] = []
        for item, (raw_text, confidence) in zip(prepared, texts_and_scores):
            page_number, _image, _corrected_image, orientation, was_corrected = item
            chosen_text = raw_text
            chosen_confidence = confidence
            chosen_engine = OCREngine.SURYA
            selection_reason = "surya_primary"
            errors: list[str] = []
            surya_quality = _text_quality_score(raw_text, confidence)

            # Une page faible est rejouée avec plusieurs segmentations
            # Tesseract. On compare une qualité commune, pas les confiances
            # brutes qui ne sont pas calibrées de la même façon.
            if surya_quality < OCR_RETRY_QUALITY and _is_tesseract_operational():
                tess_text, tess_confidence = _ocr_page_tesseract(
                    _corrected_image,
                    page_number,
                )
                tess_quality = _text_quality_score(tess_text, tess_confidence)
                if tess_text and (
                    not raw_text.strip()
                    or tess_quality >= surya_quality + 0.02
                ):
                    chosen_text = tess_text
                    chosen_confidence = tess_confidence
                    chosen_engine = OCREngine.TESSERACT
                    selection_reason = (
                        f"tesseract_rescue:surya_quality={surya_quality:.3f};"
                        f"tesseract_quality={tess_quality:.3f}"
                    )
                    errors.append(
                        f"Page {page_number} : sortie Surya faible remplacée "
                        "par Tesseract"
                    )
                else:
                    selection_reason = (
                        f"surya_kept_after_retry:surya_quality={surya_quality:.3f};"
                        f"tesseract_quality={tess_quality:.3f}"
                    )
            elif surya_quality < OCR_RETRY_QUALITY:
                selection_reason = (
                    f"surya_low_quality_no_tesseract:surya_quality={surya_quality:.3f}"
                )
                errors.append(
                    f"Page {page_number} : Tesseract indisponible pour secourir "
                    f"Surya (cmd={TESSERACT_CMD or 'PATH système'})"
                )
                logger.error(
                    "Secours Tesseract impossible | page=%d | surya_quality=%.3f | cmd=%s",
                    page_number,
                    surya_quality,
                    TESSERACT_CMD or "PATH système",
                )
            results.append(
                _page_result_from_ocr(
                    page_number=page_number,
                    raw_text=chosen_text,
                    confidence=chosen_confidence,
                    engine=chosen_engine,
                    orientation=orientation,
                    was_corrected=was_corrected,
                    errors=errors,
                    selection_reason=selection_reason,
                )
            )
        return results, document_orientation
    finally:
        for _page_number, image, corrected_image, _orientation, was_corrected in prepared:
            if was_corrected and corrected_image is not image:
                try:
                    corrected_image.close()
                except Exception:
                    pass
        for _page_number, image in rendered_pages:
            try:
                image.close()
            except Exception:
                pass


def _record_page_result(result: OCRResult, page_result: OCRPageResult) -> None:
    result.pages.append(page_result)
    result.text_chunks.append(_build_page_chunk(page_result))
    result.extraction_errors.extend(page_result.extraction_errors)
    logger.info(
        "OCR page=%d | chars=%d | words=%d | conf=%.3f | qualité=%.3f | "
        "moteur=%s | sélection=%s | orientation=%s%s",
        page_result.page_number,
        page_result.char_count,
        page_result.word_count,
        page_result.confidence,
        page_result.quality_score,
        page_result.engine_used.value,
        page_result.selection_reason,
        page_result.orientation_detected.value,
        " [corrigée]" if page_result.orientation_corrected else "",
    )


def _record_page_error(result: OCRResult, page_number: int, engine: OCREngine, exc: Exception) -> None:
    message = f"Erreur page {page_number} : {exc}"
    logger.warning(message)
    result.extraction_errors.append(message)
    result.text_chunks.append(
        f"[PAGE {page_number}] [OCR:{engine.value} | conf:0.00]\n\n[ERREUR OCR]"
    )


def _build_page_chunk(page_result: OCRPageResult) -> str:
    """Assemble le chunk RAG d'une page OCR."""
    header = (
        f"[PAGE {page_result.page_number}] "
        f"[OCR:{page_result.engine_used.value} | "
        f"conf:{page_result.confidence:.2f} | "
        f"quality:{page_result.quality_score:.2f}]"
    )

    if (
        OCR_REJECT_LOW_QUALITY
        and page_result.raw_text.strip()
        and page_result.quality_score < MIN_OCR_QUALITY
    ):
        body = (
            "[OCR_REJECTED_LOW_QUALITY] "
            f"quality={page_result.quality_score:.2f} "
            f"threshold={MIN_OCR_QUALITY:.2f}"
        )
    else:
        body = (
            page_result.raw_text.strip()
            if page_result.raw_text.strip()
            else "[PAGE VIDE]"
        )

    return f"{header}\n\n{body}"


# ── Qualité et tags ───────────────────────────────────────────────────────────

def _compute_confidence(pages: list[OCRPageResult]) -> float:
    """Calcule une confiance globale pondérée par le nombre de mots."""
    if not pages:
        return 0.0

    total_weight = sum(page.word_count for page in pages)
    if total_weight <= 0:
        return 0.0

    weighted_sum = sum(
        page.confidence * page.word_count
        for page in pages
    )
    return round(weighted_sum / total_weight, 3)


def _compute_quality(pages: list[OCRPageResult]) -> float:
    """Calcule la qualité textuelle globale pondérée par le nombre de mots."""
    if not pages:
        return 0.0
    total_weight = sum(max(1, page.word_count) for page in pages)
    return round(
        sum(page.quality_score * max(1, page.word_count) for page in pages)
        / max(1, total_weight),
        3,
    )


def _build_tags(result: OCRResult) -> list[str]:
    """Construit les tags de traçabilité."""
    tags = [f"PDF_OCR:{result.engine_used.value.upper()}"]

    if any(page.orientation_corrected for page in result.pages):
        tags.append("ORIENTATION_CORRECTED")

    if any(page.is_text_poor for page in result.pages):
        tags.append("LOW_QUALITY_PAGES")

    if any(
        page.confidence < MIN_OCR_CONFIDENCE
        for page in result.pages
        if page.char_count > 0
    ):
        tags.append("LOW_CONFIDENCE")

    if any(
        page.quality_score < MIN_OCR_QUALITY
        for page in result.pages
        if page.char_count > 0
    ):
        tags.append("LOW_OCR_QUALITY")

    if any(page.char_count == 0 for page in result.pages):
        tags.append("EMPTY_OCR_PAGES")

    if any(page.engine_used == OCREngine.TESSERACT for page in result.pages) and any(
        page.engine_used == OCREngine.SURYA for page in result.pages
    ):
        tags.append("PER_PAGE_ENGINE_SELECTION")

    if result.page_count != len(result.pages_processed):
        tags.append("PARTIAL_OCR")

    if not result.pages:
        tags.append("ERROR:NO_OCR_PAGES")

    return tags


# ── Point d'entrée principal ──────────────────────────────────────────────────

def extract_pdf_ocr(
    file_path: str | Path,
    target_pages: Optional[list[int]] = None,
) -> OCRResult:
    """
    Extrait le texte d'un PDF scanné via OCR.

    Le PDF est ouvert uniquement pendant le rendu de chaque page, puis fermé
    avant l'inférence OCR. Cette stratégie évite les verrous de fichier Windows.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")

    if path.suffix.lower() != ".pdf":
        raise ValueError(
            f"Format non supporté par pdf_ocr : {path.suffix}"
        )

    pdf_title = ""
    try:
        with fitz.open(str(path)) as identity_document:
            pdf_title = str(
                (identity_document.metadata or {}).get("title") or ""
            ).strip()
    except Exception as exc:
        logger.debug("Titre PDF indisponible pour %s : %s", path.name, exc)

    # Niveau WARNING volontaire : cette identité reste visible même lorsque le
    # serveur FastAPI ne montre pas les logs INFO de ce module.
    logger.warning(
        "OCR_ARTICLE_START | file=%s | title=%s | source=%s | target_pages=%s",
        path.name,
        pdf_title or "[titre PDF absent]",
        path.resolve(),
        target_pages or "toutes",
    )

    logger.info(
        "Extraction OCR PDF | file=%s | pages_cibles=%s | "
        "dpi=%d | max_side=%d | max_pixels=%d",
        path.name,
        target_pages or "toutes",
        OCR_DPI,
        OCR_MAX_SIDE,
        OCR_MAX_PIXELS,
    )

    result = OCRResult(
        file_name=path.name,
        source_path=str(path.resolve()),
    )

    try:
        total_pages, selected_pages = _get_pdf_page_selection(
            path,
            target_pages,
        )
    except RuntimeError as exc:
        result.extraction_errors.append(str(exc))
        result.tags = ["PDF_OCR:NONE", "ERROR:PDF_OPEN_FAILED"]
        return result

    result.page_count = total_pages

    if not selected_pages:
        result.extraction_errors.append(
            "Aucune page PDF valide à traiter"
        )
        result.tags = ["PDF_OCR:NONE", "ERROR:NO_PAGES"]
        return result

    engine = _detect_available_engine()
    result.engine_used = engine

    if engine == OCREngine.NONE:
        result.extraction_errors.append(
            "Aucun moteur OCR disponible. Installer/configurer "
            "Surya OCR ou Tesseract."
        )
        result.tags = ["PDF_OCR:NONE", "ERROR:NO_ENGINE"]
        return result

    logger.info(
        "%d page(s) sélectionnée(s) sur %d via %s",
        len(selected_pages),
        total_pages,
        engine.value,
    )

    if engine == OCREngine.SURYA:
        document_orientation: Optional[PageOrientation] = None
        for start in range(0, len(selected_pages), OCR_SURYA_BATCH_SIZE):
            batch_page_numbers = selected_pages[start : start + OCR_SURYA_BATCH_SIZE]
            rendered_pages: list[tuple[int, "PILImage.Image"]] = []
            for page_number in batch_page_numbers:
                try:
                    rendered_pages.append((page_number, _render_pdf_page(path, page_number)))
                    result.pages_processed.append(page_number)
                except Exception as exc:
                    _record_page_error(result, page_number, engine, exc)

            if rendered_pages:
                try:
                    page_results, document_orientation = _process_surya_batch(
                        rendered_pages,
                        document_orientation,
                    )
                    for page_result in page_results:
                        _record_page_result(result, page_result)
                except Exception as exc:
                    # _process_surya_batch ferme déjà les images qu'il reçoit.
                    for page_number, _image in rendered_pages:
                        _record_page_error(result, page_number, engine, exc)

            # Ne pas purger le cache CUDA par page : cela synchronise le GPU et
            # annule l'intérêt du batching. L'option reste disponible après un
            # lot pour les longues séries de documents.
            gc.collect()
            if (
                torch is not None
                and torch.cuda.is_available()
                and (
                    os.getenv("OCR_EMPTY_CUDA_CACHE_PER_BATCH", "0") == "1"
                    or os.getenv("OCR_EMPTY_CUDA_CACHE_PER_PAGE", "0") == "1"
                )
            ):
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
    else:
        for page_number in selected_pages:
            image = None
            try:
                image = _render_pdf_page(path, page_number)
                result.pages_processed.append(page_number)
                _record_page_result(
                    result,
                    _process_single_page(image, page_number, engine),
                )
            except Exception as exc:
                _record_page_error(result, page_number, engine, exc)
            finally:
                if image is not None:
                    try:
                        image.close()
                    except Exception:
                        pass
                gc.collect()

    page_engines = {page.engine_used for page in result.pages}
    if len(page_engines) > 1:
        result.engine_used = OCREngine.MIXED
    elif page_engines:
        result.engine_used = next(iter(page_engines))

    result.confidence_score = _compute_confidence(result.pages)
    result.quality_score = _compute_quality(result.pages)
    result.tags = _build_tags(result)

    for page_result in result.pages:
        if (
            page_result.char_count == 0
            or page_result.quality_score < MIN_OCR_QUALITY
        ):
            logger.error(
                "OCR_ARTICLE_FAILED | file=%s | title=%s | page=%d | "
                "engine=%s | confidence=%.3f | quality=%.3f | chars=%d | "
                "selection=%s",
                path.name,
                pdf_title or "[titre PDF absent]",
                page_result.page_number,
                page_result.engine_used.value,
                page_result.confidence,
                page_result.quality_score,
                page_result.char_count,
                page_result.selection_reason,
            )

    logger.info(
        "OCR terminé | file=%s | pages_ok=%d/%d | "
        "confidence=%.3f | qualité=%.3f | engine=%s | tags=%s",
        path.name,
        len(result.pages),
        len(selected_pages),
        result.confidence_score,
        result.quality_score,
        result.engine_used.value,
        result.tags,
    )

    # Dernière collecte avant que l'appelant ne supprime un PDF temporaire.
    gc.collect()
    return result


# ── Fusion natif + OCR ────────────────────────────────────────────────────────

def merge_native_and_ocr(
    native_chunks: list[str],
    ocr_result: OCRResult,
) -> list[str]:
    """
    Remplace les chunks natifs faibles par leurs versions OCR.

    Les numéros de pages sont 1-indexés.
    """
    ocr_index: dict[int, tuple[str, OCRPageResult]] = {}

    for page_result, chunk in zip(
        ocr_result.pages,
        ocr_result.text_chunks,
    ):
        ocr_index[page_result.page_number] = (chunk, page_result)

    merged: list[str] = []

    for index, chunk in enumerate(native_chunks):
        page_number = index + 1

        if page_number in ocr_index:
            ocr_chunk, page_result = ocr_index[page_number]
            native_quality = _text_quality_score(chunk, 0.65) if chunk.strip() else 0.0
            if (
                page_result.quality_score < MIN_OCR_QUALITY
                and native_quality > page_result.quality_score + 0.03
            ):
                merged.append(chunk)
                logger.warning(
                    "Page %d : OCR rejeté au profit du natif | "
                    "ocr_quality=%.3f | native_quality=%.3f",
                    page_number,
                    page_result.quality_score,
                    native_quality,
                )
            else:
                merged.append(ocr_chunk)
                logger.debug(
                    "Page %d remplacée par le chunk OCR",
                    page_number,
                )
        else:
            merged.append(chunk)

    return merged


# ── Interface debug ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print(
            "Usage : python pdf_ocr.py "
            "<chemin_vers_pdf> [page1,page2,...]"
        )
        sys.exit(1)

    target: Optional[list[int]] = None

    if len(sys.argv) >= 3:
        target = [
            int(page.strip())
            for page in sys.argv[2].split(",")
            if page.strip()
        ]

    response = extract_pdf_ocr(
        sys.argv[1],
        target_pages=target,
    )

    summary = {
        "file": response.file_name,
        "source_path": response.source_path,
        "engine": response.engine_used.value,
        "page_count": response.page_count,
        "pages_processed": response.pages_processed,
        "pages_with_results": len(response.pages),
        "text_chars": sum(
            page.char_count
            for page in response.pages
        ),
        "text_words": sum(
            page.word_count
            for page in response.pages
        ),
        "confidence": response.confidence_score,
        "quality": response.quality_score,
        "tags": response.tags,
        "errors": response.extraction_errors,
        "chunks_preview": [
            chunk[:300] + ("…" if len(chunk) > 300 else "")
            for chunk in response.text_chunks[:3]
        ],
        "pages_quality": [
            {
                "page": page.page_number,
                "engine": page.engine_used.value,
                "confidence": page.confidence,
                "quality": page.quality_score,
                "selection": page.selection_reason,
                "rejected_for_rag": (
                    OCR_REJECT_LOW_QUALITY
                    and page.quality_score < MIN_OCR_QUALITY
                ),
            }
            for page in response.pages
        ],
    }

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )
