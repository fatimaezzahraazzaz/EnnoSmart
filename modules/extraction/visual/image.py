"""
modules/extraction/visual/image.py
──────────────────────────────────────────────────────────────────────────────
Prétraitement d'images pour dossiers R&D / CIR.

Rôle dans le pipeline :
  Ce module est la PREMIÈRE étape du pipeline visuel. Il reçoit une image
  brute (extraite d'un PDF, d'un PPTX, ou uploadée directement) et la
  prépare pour vision.py (Llama-3.2-Vision).

  Il ne décrit PAS l'image — c'est le rôle de vision.py.
  Il PRÉPARE l'image pour être décrite de façon optimale.

Sources d'entrée :
  ┌─────────────────────────────────────────────────────┐
  │  pdf_native.py  →  pages avec has_images=True       │
  │  pdf_ocr.py     →  pages images extraites           │
  │  office.py      →  visual_candidates (slides PPTX)  │
  │  Upload direct  →  .png / .jpg / .tiff / .bmp / .svg│
  └─────────────────────────────────────────────────────┘
                          │
                          ▼
                    image.py (ce module)
                    ─ détection type visuel
                    ─ normalisation résolution
                    ─ correction orientation
                    ─ amélioration contraste si besoin
                    ─ conversion format unifié
                          │
                          ▼
                    vision.py (Llama-3.2-Vision)

Types visuels R&D détectés :
  - SCHEMA_TECHNIQUE  : diagramme, flowchart, architecture système
  - GRAPHIQUE         : courbe, histogramme, nuage de points
  - TABLEAU_IMAGE     : tableau photographié ou scanné
  - PHOTO             : photo de matériel, prototype, laboratoire
  - CAPTURE_ECRAN     : screenshot d'interface, log, terminal
  - EQUATION          : formule mathématique ou chimique
  - PLAN              : plan mécanique, électronique, bâtiment
  - INCONNU           : type non déterminable sans vision LLM

Auteur  : EnnoSmart
Version : 1.0.0
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Union

# ── Pillow ────────────────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    from PIL.Image import Image as PILImage
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

logger = logging.getLogger(__name__)


# ── Constantes ────────────────────────────────────────────────────────────────

# Résolution cible pour Llama-3.2-Vision (optimal qualité/performance)
TARGET_WIDTH  = 448   # Patch size Qwen2-VL — évite nan/inf
TARGET_HEIGHT = 448

# Résolution minimale pour tenter une analyse vision
# Note : _safe_normalize_image dans router.py filtre déjà < 50px
MIN_WIDTH  = 50
MIN_HEIGHT = 50

# Résolution maximale acceptée en entrée avant redimensionnement
MAX_INPUT_WIDTH  = 4096
MAX_INPUT_HEIGHT = 4096

# Format de sortie unifié vers vision.py
OUTPUT_FORMAT = "PNG"
OUTPUT_MODE   = "RGB"

# Seuil de ratio blanc/noir pour détecter une image quasi-vide
BLANK_PAGE_THRESHOLD = 0.97   # 97% pixels clairs → page blanche

# Seuil de contraste minimal (écart-type des pixels)
MIN_CONTRAST_STD = 15.0

# Extensions d'images supportées
SUPPORTED_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tiff", ".tif",
    ".bmp", ".gif", ".webp", ".svg",
}


# ── Enums ─────────────────────────────────────────────────────────────────────

class VisualType(str, Enum):
    """Type de contenu visuel — détecté par heuristiques avant vision LLM."""
    SCHEMA_TECHNIQUE = "schema_technique"
    GRAPHIQUE        = "graphique"
    TABLEAU_IMAGE    = "tableau_image"
    PHOTO            = "photo"
    CAPTURE_ECRAN    = "capture_ecran"
    EQUATION         = "equation"
    PLAN             = "plan"
    INCONNU          = "inconnu"


class ImageOrientation(str, Enum):
    NORMAL     = "normal"
    ROTATED_90 = "rotated_90"
    ROTATED_180= "rotated_180"
    ROTATED_270= "rotated_270"


class ImageQuality(str, Enum):
    GOOD     = "good"       # Prête pour vision.py
    LOW_RES  = "low_res"    # Résolution insuffisante
    BLANK    = "blank"      # Page blanche ou quasi-vide
    CORRUPT  = "corrupt"    # Image illisible
    ENHANCED = "enhanced"   # Améliorée par le module


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class ImageInfo:
    """Informations techniques sur l'image originale."""
    width: int
    height: int
    mode: str                    # RGB, RGBA, L, P…
    format: Optional[str]        # PNG, JPEG, TIFF…
    file_size_bytes: int
    dpi: Optional[tuple[float, float]]
    has_alpha: bool
    is_grayscale: bool


@dataclass
class ProcessedImage:
    """
    Résultat du prétraitement d'une image.
    Contient l'image normalisée prête pour vision.py + toutes ses métadonnées.
    """
    # ── Identité ──────────────────────────────────────────────────────────
    source_path: Optional[str]          # None si image extraite de PDF/PPTX
    source_type: str                    # "file" | "pdf_page" | "pptx_slide" | "bytes"
    image_hash: str                     # MD5 du contenu → déduplication

    # ── Image normalisée → prête pour vision.py ───────────────────────────
    image_bytes: bytes                  # PNG bytes normalisés
    width: int
    height: int

    # ── Informations originales ───────────────────────────────────────────
    original_info: ImageInfo

    # ── Analyse heuristique ───────────────────────────────────────────────
    visual_type: VisualType             # Type détecté sans LLM
    visual_type_confidence: float       # Confiance [0.0–1.0]
    orientation: ImageOrientation
    orientation_corrected: bool
    quality: ImageQuality
    contrast_enhanced: bool

    # ── Contexte document ─────────────────────────────────────────────────
    page_number: Optional[int]          # Si extrait d'un PDF
    slide_number: Optional[int]         # Si extrait d'un PPTX
    caption: Optional[str]             # Légende détectée dans le document

    # ── Traçabilité ───────────────────────────────────────────────────────
    tags: list[str] = field(default_factory=list)
    skip_vision: bool = False           # True si image vide/corrompue
    skip_reason: Optional[str] = None
    processing_errors: list[str] = field(default_factory=list)


# ── Détection du type visuel ──────────────────────────────────────────────────

def _detect_visual_type(
    img: "PILImage",
    filename: Optional[str] = None,
) -> tuple[VisualType, float]:
    """
    Détecte le type de contenu visuel par heuristiques sur l'image.

    Approche multi-signaux :
      1. Analyse des couleurs dominantes
      2. Détection de lignes/bords (ratio bords = graphique ou schéma)
      3. Rapport largeur/hauteur
      4. Nom du fichier si disponible (hint du document source)

    Retourne (VisualType, confidence)
    """
    if not PILLOW_AVAILABLE:
        return VisualType.INCONNU, 0.0

    try:
        # ── Signal 1 : nom de fichier ──────────────────────────────────────
        if filename:
            name_lower = filename.lower()
            file_hints: list[tuple[str, VisualType]] = [
                ("schema", VisualType.SCHEMA_TECHNIQUE),
                ("diag",   VisualType.SCHEMA_TECHNIQUE),
                ("arch",   VisualType.SCHEMA_TECHNIQUE),
                ("flow",   VisualType.SCHEMA_TECHNIQUE),
                ("graph",  VisualType.GRAPHIQUE),
                ("chart",  VisualType.GRAPHIQUE),
                ("plot",   VisualType.GRAPHIQUE),
                ("fig",    VisualType.GRAPHIQUE),
                ("table",  VisualType.TABLEAU_IMAGE),
                ("tab",    VisualType.TABLEAU_IMAGE),
                ("photo",  VisualType.PHOTO),
                ("img",    VisualType.PHOTO),
                ("screen", VisualType.CAPTURE_ECRAN),
                ("capture",VisualType.CAPTURE_ECRAN),
                ("eq",     VisualType.EQUATION),
                ("plan",   VisualType.PLAN),
                ("cad",    VisualType.PLAN),
            ]
            for hint, vtype in file_hints:
                if hint in name_lower:
                    return vtype, 0.75

        # ── Signal 2 : analyse couleurs ───────────────────────────────────
        img_rgb = img.convert("RGB")
        img_small = img_rgb.resize((64, 64), Image.LANCZOS)  # Rapide

        pixels = list(img_small.getdata())
        total = len(pixels)

        # Compte des couleurs distinctes
        unique_colors = len(set(pixels))
        color_ratio = unique_colors / total

        # Pixels "presque blancs" (fond de schéma/graphique)
        white_pixels = sum(
            1 for r, g, b in pixels
            if r > 230 and g > 230 and b > 230
        )
        white_ratio = white_pixels / total

        # Pixels très saturés (graphiques colorés)
        def _saturation(r: int, g: int, b: int) -> float:
            mx, mn = max(r, g, b) / 255, min(r, g, b) / 255
            return (mx - mn) / mx if mx > 0 else 0.0

        saturated = sum(
            1 for r, g, b in pixels
            if _saturation(r, g, b) > 0.5
        )
        saturation_ratio = saturated / total

        # ── Signal 3 : détection bords (proxy pour structure) ─────────────
        img_gray = img.convert("L").resize((128, 128), Image.LANCZOS)
        edges = img_gray.filter(ImageFilter.FIND_EDGES)
        edge_pixels = list(edges.getdata())
        edge_ratio = sum(1 for p in edge_pixels if p > 30) / len(edge_pixels)

        # ── Signal 4 : ratio largeur/hauteur ──────────────────────────────
        w, h = img.size
        aspect = w / h if h > 0 else 1.0

        # ── Décision heuristique ───────────────────────────────────────────
        #
        # SCHEMA_TECHNIQUE  : fond blanc + peu de couleurs + beaucoup de bords
        # GRAPHIQUE         : couleurs saturées + fond blanc + bords modérés
        # TABLEAU_IMAGE     : aspect proche de 1.5–3.0 + bords forts + peu saturé
        # PHOTO             : beaucoup de couleurs + peu de blanc + peu de bords
        # CAPTURE_ECRAN     : fond très blanc + couleurs douces + bords marqués
        # EQUATION          : très blanc + très peu de couleurs + quelques bords

        scores: dict[VisualType, float] = {
            VisualType.SCHEMA_TECHNIQUE: 0.0,
            VisualType.GRAPHIQUE:        0.0,
            VisualType.TABLEAU_IMAGE:    0.0,
            VisualType.PHOTO:            0.0,
            VisualType.CAPTURE_ECRAN:    0.0,
            VisualType.EQUATION:         0.0,
        }

        # Schéma technique
        if white_ratio > 0.55 and edge_ratio > 0.10 and color_ratio < 0.35:
            scores[VisualType.SCHEMA_TECHNIQUE] += 0.6
        if white_ratio > 0.70 and edge_ratio > 0.08:
            scores[VisualType.SCHEMA_TECHNIQUE] += 0.2

        # Graphique
        if saturation_ratio > 0.15 and white_ratio > 0.40:
            scores[VisualType.GRAPHIQUE] += 0.5
        if saturation_ratio > 0.25:
            scores[VisualType.GRAPHIQUE] += 0.2

        # Tableau image
        if 1.3 < aspect < 3.5 and edge_ratio > 0.12 and saturation_ratio < 0.10:
            scores[VisualType.TABLEAU_IMAGE] += 0.5
        if white_ratio > 0.60 and edge_ratio > 0.15:
            scores[VisualType.TABLEAU_IMAGE] += 0.2

        # Photo
        if color_ratio > 0.50 and white_ratio < 0.30:
            scores[VisualType.PHOTO] += 0.5
        if saturation_ratio > 0.30 and white_ratio < 0.20:
            scores[VisualType.PHOTO] += 0.3

        # Capture écran
        if white_ratio > 0.50 and color_ratio > 0.20 and edge_ratio > 0.08:
            scores[VisualType.CAPTURE_ECRAN] += 0.4
        if aspect > 1.5 and white_ratio > 0.45:
            scores[VisualType.CAPTURE_ECRAN] += 0.2

        # Équation
        if white_ratio > 0.80 and color_ratio < 0.15 and edge_ratio < 0.10:
            scores[VisualType.EQUATION] += 0.6

        # Meilleur score
        best_type = max(scores, key=lambda k: scores[k])
        best_score = scores[best_type]

        if best_score < 0.30:
            return VisualType.INCONNU, best_score

        return best_type, min(round(best_score, 2), 0.90)

    except Exception as exc:
        logger.debug("Échec détection type visuel : %s", exc)
        return VisualType.INCONNU, 0.0


# ── Analyse qualité ───────────────────────────────────────────────────────────

def _analyze_quality(img: "PILImage") -> tuple[ImageQuality, Optional[str]]:
    """
    Évalue la qualité de l'image avant traitement.

    Retourne (ImageQuality, raison_si_skip)
    """
    w, h = img.size

    # Résolution insuffisante
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return ImageQuality.LOW_RES, f"Résolution trop faible : {w}×{h}px"

    # Bannière / séparateur (ratio très allongé — non-informatif)
    aspect = w / h if h > 0 else 1.0
    if aspect > 5.0 or aspect < 0.2:
        return ImageQuality.BLANK, f"Bannière ou séparateur (ratio={aspect:.1f})"

    # Bannière répétitive connue (ex: logo header 1135×289)
    if w == 1135 and h == 289:
        return ImageQuality.BLANK, "Bannière répétitive filtrée"

    # Image quasi-blanche (page vide ou header sans contenu)
    try:
        img_gray = img.convert("L")
        pixels = list(img_gray.getdata())
        total = len(pixels)
        light_pixels = sum(1 for p in pixels if p > 240)
        if light_pixels / total > BLANK_PAGE_THRESHOLD:
            return ImageQuality.BLANK, "Image quasi-blanche (page vide)"
    except Exception:
        pass

    # Contraste insuffisant
    try:
        import statistics
        img_gray = img.convert("L")
        pixels = list(img_gray.getdata())
        if len(pixels) > 100:
            std = statistics.stdev(pixels[:min(len(pixels), 10000)])
            if std < MIN_CONTRAST_STD:
                return ImageQuality.LOW_RES, f"Contraste insuffisant (σ={std:.1f})"
    except Exception:
        pass

    return ImageQuality.GOOD, None


# ── Correction d'orientation ──────────────────────────────────────────────────

def _correct_orientation(img: "PILImage") -> tuple["PILImage", ImageOrientation, bool]:
    """
    Corrige l'orientation via les métadonnées EXIF si disponibles.
    Retourne (image_corrigée, orientation_détectée, a_été_corrigée).
    """
    orientation = ImageOrientation.NORMAL
    corrected = False

    try:
        # Lire les données EXIF
        exif_data = img._getexif() if hasattr(img, "_getexif") else None
        if exif_data:
            # Tag EXIF 274 = Orientation
            exif_orientation = exif_data.get(274)
            rotation_map = {
                3: (180,                ImageOrientation.ROTATED_180),
                6: (-90,               ImageOrientation.ROTATED_90),
                8: (90,                ImageOrientation.ROTATED_270),
            }
            if exif_orientation in rotation_map:
                angle, orient = rotation_map[exif_orientation]
                img = img.rotate(angle, expand=True)
                orientation = orient
                corrected = True
    except Exception:
        pass

    return img, orientation, corrected


# ── Amélioration contraste ────────────────────────────────────────────────────

def _enhance_if_needed(img: "PILImage") -> tuple["PILImage", bool]:
    """
    Améliore le contraste si l'image est terne (schémas scannés, fax).
    N'améliore PAS les photos (risque de sur-saturation).
    Retourne (image, a_été_améliorée).
    """
    try:
        import statistics
        img_gray = img.convert("L")
        pixels = list(img_gray.getdata())
        std = statistics.stdev(pixels[:min(len(pixels), 10000)])

        # Seulement si contraste faible et image non-photographique
        if MIN_CONTRAST_STD <= std < 40.0:
            # Normalisation des niveaux
            img_enhanced = ImageOps.autocontrast(img, cutoff=2)
            # Légère augmentation du contraste
            enhancer = ImageEnhance.Contrast(img_enhanced)
            img_enhanced = enhancer.enhance(1.3)
            return img_enhanced, True

    except Exception as exc:
        logger.debug("Échec amélioration contraste : %s", exc)

    return img, False


# ── Normalisation résolution ──────────────────────────────────────────────────

def _normalize_size(img: "PILImage") -> "PILImage":
    """
    Redimensionne l'image à la résolution cible pour Llama-3.2-Vision.

    Règles :
      - Image trop grande (> MAX_INPUT) → réduction proportionnelle
      - Image dans la plage cible        → pas de modification
      - Préserve le ratio largeur/hauteur
      - Utilise LANCZOS pour la qualité
    """
    w, h = img.size

    # Déjà dans la plage acceptable
    if w <= TARGET_WIDTH and h <= TARGET_HEIGHT:
        return img

    # Calculer le facteur de réduction
    scale = min(TARGET_WIDTH / w, TARGET_HEIGHT / h)
    new_w = int(w * scale)
    new_h = int(h * scale)

    return img.resize((new_w, new_h), Image.LANCZOS)


# ── Conversion SVG ────────────────────────────────────────────────────────────

def _svg_to_png_bytes(svg_path: Path) -> Optional[bytes]:
    """
    Convertit un SVG en PNG via cairosvg si disponible.
    Les SVG sont fréquents dans les rapports R&D (schémas vectoriels).
    """
    try:
        import cairosvg
        png_bytes = cairosvg.svg2png(
            url=str(svg_path),
            output_width=TARGET_WIDTH,
            output_height=TARGET_HEIGHT,
        )
        return png_bytes
    except ImportError:
        logger.debug("cairosvg non installé — SVG ignoré (pip install cairosvg)")
        return None
    except Exception as exc:
        logger.warning("Conversion SVG→PNG échouée : %s", exc)
        return None


# ── Construction des tags ─────────────────────────────────────────────────────

def _build_tags(result: ProcessedImage) -> list[str]:
    tags: list[str] = [f"VISUAL:{result.visual_type.value.upper()}"]

    if result.orientation_corrected:
        tags.append("ORIENTATION_CORRECTED")
    if result.contrast_enhanced:
        tags.append("CONTRAST_ENHANCED")
    if result.quality == ImageQuality.ENHANCED:
        tags.append("IMAGE_ENHANCED")
    if result.skip_vision:
        tags.append("SKIP_VISION")
    if result.page_number is not None:
        tags.append(f"FROM_PDF_PAGE:{result.page_number}")
    if result.slide_number is not None:
        tags.append(f"FROM_PPTX_SLIDE:{result.slide_number}")
    if result.visual_type_confidence >= 0.70:
        tags.append("HIGH_CONFIDENCE_TYPE")

    return tags


# ── Pipeline de traitement ────────────────────────────────────────────────────

def _process_pil_image(
    img: "PILImage",
    source_path: Optional[str],
    source_type: str,
    filename: Optional[str],
    page_number: Optional[int],
    slide_number: Optional[int],
    caption: Optional[str],
) -> ProcessedImage:
    """
    Pipeline complet de traitement d'une image PIL.
    Utilisé par toutes les fonctions publiques.
    """
    errors: list[str] = []

    # ── Info originale ─────────────────────────────────────────────────────
    original_info = ImageInfo(
        width=img.width,
        height=img.height,
        mode=img.mode,
        format=getattr(img, "format", None),
        file_size_bytes=0,           # Mis à jour si fichier
        dpi=img.info.get("dpi"),
        has_alpha=img.mode in ("RGBA", "LA", "PA"),
        is_grayscale=img.mode in ("L", "LA"),
    )

    # ── Analyse qualité ────────────────────────────────────────────────────
    quality, skip_reason = _analyze_quality(img)
    skip_vision = quality in (ImageQuality.BLANK, ImageQuality.CORRUPT)

    if skip_vision:
        # Image inutilisable — on retourne un résultat minimal sans traitement
        return ProcessedImage(
            source_path=source_path,
            source_type=source_type,
            image_hash=hashlib.md5(img.tobytes()).hexdigest()[:12],
            image_bytes=b"",
            width=img.width,
            height=img.height,
            original_info=original_info,
            visual_type=VisualType.INCONNU,
            visual_type_confidence=0.0,
            orientation=ImageOrientation.NORMAL,
            orientation_corrected=False,
            quality=quality,
            contrast_enhanced=False,
            page_number=page_number,
            slide_number=slide_number,
            caption=caption,
            tags=["SKIP_VISION", f"REASON:{quality.value.upper()}"],
            skip_vision=True,
            skip_reason=skip_reason,
        )

    # ── Correction orientation EXIF ────────────────────────────────────────
    img, orientation, orientation_corrected = _correct_orientation(img)

    # ── Conversion en RGB (unifié) ─────────────────────────────────────────
    if img.mode != OUTPUT_MODE:
        try:
            img = img.convert(OUTPUT_MODE)
        except Exception as exc:
            errors.append(f"Conversion RGB échouée : {exc}")
            # Fallback
            img = img.convert("RGB")

    # ── Amélioration contraste si nécessaire ──────────────────────────────
    img, contrast_enhanced = _enhance_if_needed(img)
    if contrast_enhanced:
        quality = ImageQuality.ENHANCED

    # ── Normalisation taille ───────────────────────────────────────────────
    img = _normalize_size(img)

    # ── Détection type visuel ──────────────────────────────────────────────
    visual_type, type_confidence = _detect_visual_type(img, filename)

    # ── Sérialisation PNG ──────────────────────────────────────────────────
    buffer = io.BytesIO()
    img.save(buffer, format=OUTPUT_FORMAT, optimize=True)
    image_bytes = buffer.getvalue()

    # ── Hash pour déduplication ────────────────────────────────────────────
    image_hash = hashlib.md5(image_bytes).hexdigest()[:12]

    result = ProcessedImage(
        source_path=source_path,
        source_type=source_type,
        image_hash=image_hash,
        image_bytes=image_bytes,
        width=img.width,
        height=img.height,
        original_info=original_info,
        visual_type=visual_type,
        visual_type_confidence=type_confidence,
        orientation=orientation,
        orientation_corrected=orientation_corrected,
        quality=quality,
        contrast_enhanced=contrast_enhanced,
        page_number=page_number,
        slide_number=slide_number,
        caption=caption,
        processing_errors=errors,
    )

    result.tags = _build_tags(result)

    logger.debug(
        "Image traitée [%s] %dx%d → %dx%d | type=%s (%.0f%%) | qualité=%s",
        source_type,
        original_info.width, original_info.height,
        img.width, img.height,
        visual_type.value, type_confidence * 100,
        quality.value,
    )

    return result


# ── Points d'entrée publics ───────────────────────────────────────────────────

def process_image_file(
    file_path: str | Path,
    page_number: Optional[int] = None,
    slide_number: Optional[int] = None,
    caption: Optional[str] = None,
) -> ProcessedImage:
    """
    Traite un fichier image depuis le disque.

    Paramètres
    ----------
    file_path    : chemin vers l'image (.png, .jpg, .tiff, .bmp, .svg)
    page_number  : numéro de page source si extrait d'un PDF
    slide_number : numéro de slide source si extrait d'un PPTX
    caption      : légende détectée dans le document source

    Retourne
    --------
    ProcessedImage
        image_bytes  : PNG normalisé prêt pour vision.py
        visual_type  : type détecté par heuristiques
        quality      : GOOD | LOW_RES | BLANK | ENHANCED
        skip_vision  : True si l'image ne mérite pas d'être analysée
        tags         : traçabilité

    Raises
    ------
    FileNotFoundError : fichier introuvable
    ValueError        : format non supporté
    RuntimeError      : Pillow non installé
    """
    if not PILLOW_AVAILABLE:
        raise RuntimeError("Pillow non installé : pip install Pillow")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Image introuvable : {path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError(
            f"Format non supporté : {ext}\n"
            f"Formats acceptés : {', '.join(sorted(SUPPORTED_IMAGE_EXTENSIONS))}"
        )

    logger.info("Traitement image fichier → %s", path.name)

    # ── Cas SVG → conversion préalable ────────────────────────────────────
    if ext == ".svg":
        png_bytes = _svg_to_png_bytes(path)
        if not png_bytes:
            # SVG non convertible — retourner un résultat skip
            return ProcessedImage(
                source_path=str(path.resolve()),
                source_type="file",
                image_hash="",
                image_bytes=b"",
                width=0, height=0,
                original_info=ImageInfo(0, 0, "", "SVG", 0, None, False, False),
                visual_type=VisualType.SCHEMA_TECHNIQUE,  # SVG = souvent schéma
                visual_type_confidence=0.60,
                orientation=ImageOrientation.NORMAL,
                orientation_corrected=False,
                quality=ImageQuality.CORRUPT,
                contrast_enhanced=False,
                page_number=page_number,
                slide_number=slide_number,
                caption=caption,
                tags=["VISUAL:SCHEMA_TECHNIQUE", "SKIP_VISION", "SVG_CONVERSION_FAILED"],
                skip_vision=True,
                skip_reason="Conversion SVG→PNG échouée (cairosvg requis)",
            )
        img = Image.open(io.BytesIO(png_bytes))
    else:
        try:
            img = Image.open(str(path))
            img.load()   # Force le chargement complet (détecte les fichiers corrompus)
        except Exception as exc:
            raise ValueError(f"Image corrompue ou illisible : {path.name}") from exc

    result = _process_pil_image(
        img=img,
        source_path=str(path.resolve()),
        source_type="file",
        filename=path.name,
        page_number=page_number,
        slide_number=slide_number,
        caption=caption,
    )

    # Taille fichier original
    result.original_info.file_size_bytes = path.stat().st_size

    return result


def process_image_bytes(
    data: bytes,
    source_type: str = "bytes",
    filename: Optional[str] = None,
    page_number: Optional[int] = None,
    slide_number: Optional[int] = None,
    caption: Optional[str] = None,
) -> ProcessedImage:
    """
    Traite une image depuis ses bytes bruts.
    Utilisé par pdf_native.py et office.py qui extraient des images
    directement depuis leurs fichiers sans les écrire sur disque.

    Paramètres
    ----------
    data         : bytes bruts de l'image
    source_type  : "pdf_page" | "pptx_slide" | "email_attachment" | "bytes"
    filename     : nom original si connu (hint pour la détection de type)
    page_number  : numéro de page source
    slide_number : numéro de slide source
    caption      : légende du document source

    Retourne
    --------
    ProcessedImage — même structure que process_image_file()
    """
    if not PILLOW_AVAILABLE:
        raise RuntimeError("Pillow non installé : pip install Pillow")

    if not data:
        raise ValueError("Données image vides")

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise ValueError(f"Impossible de décoder l'image depuis les bytes : {exc}") from exc

    result = _process_pil_image(
        img=img,
        source_path=None,
        source_type=source_type,
        filename=filename,
        page_number=page_number,
        slide_number=slide_number,
        caption=caption,
    )

    result.original_info.file_size_bytes = len(data)
    return result


def process_image_batch(
    items: list[dict],
) -> list[ProcessedImage]:
    """
    Traite un lot d'images en séquence.
    Utilisé par router.py pour traiter toutes les images d'un document.

    Paramètres
    ----------
    items : liste de dict avec les clés :
        - "path"         (str | Path) ou "data" (bytes)     [obligatoire]
        - "source_type"  (str)                               [optionnel]
        - "filename"     (str)                               [optionnel]
        - "page_number"  (int)                               [optionnel]
        - "slide_number" (int)                               [optionnel]
        - "caption"      (str)                               [optionnel]

    Retourne
    --------
    list[ProcessedImage]
        Résultats dans le même ordre que items.
        Les erreurs individuelles n'arrêtent pas le traitement du lot.
    """
    results: list[ProcessedImage] = []

    for i, item in enumerate(items):
        try:
            if "path" in item:
                result = process_image_file(
                    file_path=item["path"],
                    page_number=item.get("page_number"),
                    slide_number=item.get("slide_number"),
                    caption=item.get("caption"),
                )
            elif "data" in item:
                result = process_image_bytes(
                    data=item["data"],
                    source_type=item.get("source_type", "bytes"),
                    filename=item.get("filename"),
                    page_number=item.get("page_number"),
                    slide_number=item.get("slide_number"),
                    caption=item.get("caption"),
                )
            else:
                logger.warning("Item %d ignoré — ni 'path' ni 'data'", i)
                continue

            results.append(result)

        except Exception as exc:
            logger.warning("Erreur item %d : %s", i, exc)
            # On ajoute un résultat d'erreur pour garder l'alignement
            results.append(ProcessedImage(
                source_path=str(item.get("path", "")),
                source_type=item.get("source_type", "unknown"),
                image_hash="",
                image_bytes=b"",
                width=0, height=0,
                original_info=ImageInfo(0, 0, "", None, 0, None, False, False),
                visual_type=VisualType.INCONNU,
                visual_type_confidence=0.0,
                orientation=ImageOrientation.NORMAL,
                orientation_corrected=False,
                quality=ImageQuality.CORRUPT,
                contrast_enhanced=False,
                page_number=item.get("page_number"),
                slide_number=item.get("slide_number"),
                caption=item.get("caption"),
                tags=["SKIP_VISION", "ERROR"],
                skip_vision=True,
                skip_reason=str(exc),
                processing_errors=[str(exc)],
            ))

    valid = sum(1 for r in results if not r.skip_vision)
    logger.info(
        "Batch images : %d/%d valides pour vision.py",
        valid, len(results),
    )

    return results


# ── Interface rapide (debug / tests) ──────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.DEBUG)

    if len(sys.argv) < 2:
        print("Usage : python image.py <chemin_vers_image>")
        sys.exit(1)

    res = process_image_file(sys.argv[1])

    summary = {
        "source":         res.source_path,
        "original_size":  f"{res.original_info.width}×{res.original_info.height}",
        "processed_size": f"{res.width}×{res.height}",
        "visual_type":    res.visual_type.value,
        "type_confidence":f"{res.visual_type_confidence:.0%}",
        "quality":        res.quality.value,
        "orientation":    res.orientation.value,
        "corrected":      res.orientation_corrected,
        "enhanced":       res.contrast_enhanced,
        "skip_vision":    res.skip_vision,
        "skip_reason":    res.skip_reason,
        "image_hash":     res.image_hash,
        "output_bytes":   len(res.image_bytes),
        "tags":           res.tags,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))