"""
modules/extraction/router.py — EnnoSmart extraction router optimisé

Nouveautés :
- formula_mode = "off" | "fast" | "explain"
  Par défaut : "off" pendant la refonte NLP evidence-first.
- vision_mode = "text_only" | "auto" | "fast" | "full"
  Par défaut : "text_only" pour éviter le coût vision pendant les tests NLP.
- Warmup vision seulement après filtrage réel des images utiles.
- Limite images par document pour éviter des traitements trop longs.
- Compatible avec l'ancien paramètre enable_formulas=True/False.
"""

from __future__ import annotations

import logging
import mimetypes
import re
import tempfile
from pathlib import Path
from typing import Optional

from modules.extraction.base import ExtractionResult, FileCategory, SourceTag

logger = logging.getLogger(__name__)

EXTENSION_MAP: dict[str, FileCategory] = {
    ".pdf": FileCategory.PDF_NATIVE,
    ".docx": FileCategory.DOCX, ".doc": FileCategory.DOCX,
    ".pptx": FileCategory.PPTX, ".ppt": FileCategory.PPTX,
    ".eml": FileCategory.EMAIL, ".msg": FileCategory.EMAIL,
    ".xlsx": FileCategory.EXCEL, ".xlsm": FileCategory.EXCEL,
    ".xls": FileCategory.EXCEL, ".csv": FileCategory.EXCEL,
    ".png": FileCategory.IMAGE, ".jpg": FileCategory.IMAGE,
    ".jpeg": FileCategory.IMAGE, ".tiff": FileCategory.IMAGE,
    ".tif": FileCategory.IMAGE, ".bmp": FileCategory.IMAGE,
    ".gif": FileCategory.IMAGE, ".webp": FileCategory.IMAGE,
    ".svg": FileCategory.IMAGE,
}

MAGIC_BYTES = [
    (b"%PDF", FileCategory.PDF_NATIVE),
    (b"PK\x03\x04", FileCategory.DOCX),
    (b"\xd0\xcf\x11\xe0", FileCategory.DOCX),
]

MAX_IMAGES_PER_SLIDE = 3
MAX_IMAGES_PER_DOCUMENT_AUTO = 2
MAX_IMAGES_PER_DOCUMENT_FAST = 3
MAX_IMAGES_PER_DOCUMENT_FULL = 3


# ──────────────────────────────────────────────────────────────────────────────
# Détection type fichier
# ──────────────────────────────────────────────────────────────────────────────

def _detect_category(path: Path) -> FileCategory:
    ext = path.suffix.lower()
    if ext in EXTENSION_MAP and ext not in (".docx", ".pptx", ".xlsx", ".xlsm"):
        return EXTENSION_MAP[ext]

    try:
        with open(path, "rb") as f:
            header = f.read(8)
        for magic, cat in MAGIC_BYTES:
            if header.startswith(magic):
                if magic == b"PK\x03\x04":
                    return _detect_zip_subtype(path, ext)
                if magic == b"\xd0\xcf\x11\xe0":
                    return FileCategory.EMAIL if ext == ".msg" else FileCategory.DOCX
                return cat
    except Exception:
        pass

    if ext in EXTENSION_MAP:
        return EXTENSION_MAP[ext]

    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        for k, v in [
            ("pdf", FileCategory.PDF_NATIVE),
            ("word", FileCategory.DOCX),
            ("presentation", FileCategory.PPTX),
            ("spreadsheet", FileCategory.EXCEL),
            ("image", FileCategory.IMAGE),
            ("message", FileCategory.EMAIL),
        ]:
            if k in mime:
                return v

    return FileCategory.UNKNOWN


def _detect_zip_subtype(path: Path, ext: str) -> FileCategory:
    try:
        import zipfile
        with zipfile.ZipFile(str(path)) as zf:
            names = zf.namelist()
            if any("word/" in n for n in names):
                return FileCategory.DOCX
            if any("ppt/" in n for n in names):
                return FileCategory.PPTX
            if any("xl/" in n for n in names):
                return FileCategory.EXCEL
    except Exception:
        pass
    return EXTENSION_MAP.get(ext, FileCategory.UNKNOWN)


def _normalize_formula_mode(formula_mode: str | None = None, enable_formulas: Optional[bool] = None) -> str:
    if enable_formulas is False:
        return "off"
    mode = (formula_mode or "off").lower().strip()
    if mode in {"false", "none", "disabled", "no", "0"}:
        return "off"
    if mode not in {"off", "fast", "explain"}:
        logger.warning("formula_mode inconnu %r → off", formula_mode)
        return "off"
    return mode


def _normalize_vision_mode(mode: str | None) -> str:
    m = (mode or "text_only").lower().strip()
    if m in {"none", "off", "no_vision", "disabled"}:
        return "text_only"
    if m not in {"text_only", "auto", "fast", "full"}:
        logger.warning("vision_mode inconnu %r → text_only", mode)
        return "text_only"
    return m


# ──────────────────────────────────────────────────────────────────────────────
# Utilitaires chunks
# ──────────────────────────────────────────────────────────────────────────────

def _slide_num(header: str) -> Optional[int]:
    m = re.search(r"(?:SLIDE|PAGE)\s+(\d+)", str(header or ""), re.IGNORECASE)
    return int(m.group(1)) if m else None


def _build_chunk_index(chunks: list[str]) -> dict[int, int]:
    idx: dict[int, int] = {}
    for i, c in enumerate(chunks or []):
        n = _slide_num(c.split("\n")[0] if c else "")
        if n is not None:
            idx[n] = i
    return idx


def _safe_normalize_image(img_bytes: bytes) -> Optional[bytes]:
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.load()
        w, h = img.size
        if w < 50 or h < 50:
            return None
        aspect = w / h if h > 0 else 1
        if aspect > 5 or aspect < 0.2:
            return None
        if w == 1135 and h == 289:
            return None

        canvas = Image.new("RGB", (448, 448), (255, 255, 255))
        img.thumbnail((448, 448), Image.LANCZOS)
        canvas.paste(img, ((448 - img.width) // 2, (448 - img.height) // 2))
        buf = io.BytesIO()
        canvas.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Formules
# ──────────────────────────────────────────────────────────────────────────────

def _inject_formulas(
    chunks: list[str],
    context_file: str = "",
    formula_mode: str = "off",
) -> list[str]:
    mode = _normalize_formula_mode(formula_mode)
    if mode == "off":
        return chunks

    try:
        from modules.extraction.formula.formula import extract_formulas, _detect
    except Exception as exc:
        logger.debug("Module formule indisponible : %s", exc)
        return chunks

    result: list[str] = []

    for chunk in chunks or []:
        num = _slide_num(chunk.split("\n")[0] if chunk else "")
        ctx = (f"Slide/Page {num} — " if num else "") + context_file

        has, _ = _detect(chunk, ctx)
        if not has:
            result.append(chunk)
            continue

        fmls = extract_formulas(text=chunk, context=ctx, formula_mode=mode)
        if not fmls:
            result.append(chunk)
            continue

        seen: set[str] = set()
        section = "\n[FORMULES DÉTECTÉES]\n"
        for i, f in enumerate(fmls, 1):
            k = re.sub(r"\s+", "", f.latex.strip().lower())
            if not k or k in seen:
                continue
            seen.add(k)
            # Sortie courte : on n'injecte plus d'explication longue.
            section += (
                f"  {i}. LaTeX: {f.latex}\n"
                f"     Source: {f.source.value} | Domaine: {f.domain.value} | Confiance: {f.confidence:.2f}\n"
            )

        result.append(chunk.rstrip() + section if seen else chunk)

    return result


def _run_docx_omml(path: Path, chunks: list[str], formula_mode: str = "off") -> list[str]:
    if _normalize_formula_mode(formula_mode) == "off":
        return chunks

    try:
        import zipfile
        import xml.etree.ElementTree as ET
        from modules.extraction.formula.formula import extract_formulas

        ns = "http://schemas.openxmlformats.org/officeDocument/2006/math"
        with zipfile.ZipFile(str(path)) as zf:
            if "word/document.xml" not in zf.namelist():
                return chunks
            root = ET.fromstring(zf.read("word/document.xml"))
            omaths = list(root.iter("{" + ns + "}oMath"))

        if not omaths:
            return chunks

        enriched = list(chunks)
        seen: set[str] = set()
        section = "\n[FORMULES OMML]\n"

        for omath in omaths:
            for r in extract_formulas(
                omml_xml=ET.tostring(omath, encoding="unicode"),
                context=path.name,
                formula_mode=formula_mode,
            ):
                k = re.sub(r"\s+", "", r.latex.strip().lower())
                if k and k not in seen:
                    seen.add(k)
                    section += f"  • {r.latex} — {r.source.value} | {r.confidence:.2f}\n"

        if enriched and seen:
            enriched[-1] = enriched[-1].rstrip() + section

        return enriched

    except Exception as exc:
        logger.debug("OMML DOCX : %s", exc)
        return chunks


def _run_pptx_omml(path: Path, chunks: list[str], formula_mode: str = "off") -> list[str]:
    if _normalize_formula_mode(formula_mode) == "off":
        return chunks

    try:
        import zipfile
        import xml.etree.ElementTree as ET
        from modules.extraction.formula.formula import extract_formulas

        ns = "http://schemas.openxmlformats.org/officeDocument/2006/math"
        cidx = _build_chunk_index(chunks)
        enriched = list(chunks)

        with zipfile.ZipFile(str(path)) as zf:
            for sf in sorted(f for f in zf.namelist() if f.startswith("ppt/slides/slide") and f.endswith(".xml")):
                try:
                    tree = ET.fromstring(zf.read(sf))
                    omaths = list(tree.iter("{" + ns + "}oMath"))
                    if not omaths:
                        continue

                    m = re.search(r"slide(\d+)\.xml", sf)
                    snum = int(m.group(1)) if m else 0
                    ctx = enriched[cidx[snum]][:400] if snum in cidx else f"Slide {snum} R&D"

                    seen: set[str] = set()
                    section = ""
                    for omath in omaths:
                        for r in extract_formulas(
                            omml_xml=ET.tostring(omath, encoding="unicode"),
                            context=ctx,
                            formula_mode=formula_mode,
                        ):
                            k = re.sub(r"\s+", "", r.latex.strip().lower())
                            if k and k not in seen:
                                seen.add(k)
                                section += f"  • {r.latex} — {r.source.value} | {r.confidence:.2f}\n"

                    if section and snum in cidx:
                        enriched[cidx[snum]] = enriched[cidx[snum]].rstrip() + "\n[FORMULES OMML]\n" + section

                except Exception as exc:
                    logger.debug("OMML slide %s : %s", sf, exc)

        return enriched

    except Exception as exc:
        logger.debug("OMML PPTX : %s", exc)
        return chunks


# ──────────────────────────────────────────────────────────────────────────────
# Images / Vision
# ──────────────────────────────────────────────────────────────────────────────

def _limit_images_per_slide(items: list[dict]) -> list[dict]:
    from collections import defaultdict
    import io

    groups: dict = defaultdict(list)
    for item in items or []:
        groups[item.get("slide") or item.get("page") or 0].append(item)

    result = []
    for group in groups.values():
        if len(group) <= MAX_IMAGES_PER_SLIDE:
            result.extend(group)
            continue

        def _sz(it):
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(it["bytes"]))
                return img.width * img.height
            except Exception:
                return 0

        result.extend(sorted(group, key=_sz, reverse=True)[:MAX_IMAGES_PER_SLIDE])

    return result


def _filter_processed_images(processed_list: list[tuple], vision_mode: str) -> list[tuple]:
    """
    processed_list = [(ProcessedImage, normalized_bytes, original_item), ...]
    """
    from modules.extraction.visual.image import VisualType

    mode = _normalize_vision_mode(vision_mode)
    if mode == "text_only":
        return []

    priority = {
        VisualType.GRAPHIQUE: 100,
        VisualType.SCHEMA_TECHNIQUE: 95,
        VisualType.EQUATION: 90,
        VisualType.TABLEAU_IMAGE: 75,
        VisualType.CAPTURE_ECRAN: 55,
        VisualType.PLAN: 55,
        VisualType.PHOTO: 30,
        VisualType.INCONNU: 10,
    }

    kept = []
    for p, norm, item in processed_list:
        if p.skip_vision:
            continue

        vtype = p.visual_type
        conf = float(p.visual_type_confidence or 0.0)

        if mode == "auto":
            # Mode recommandé : très strict.
            if vtype not in {VisualType.GRAPHIQUE, VisualType.SCHEMA_TECHNIQUE, VisualType.EQUATION, VisualType.TABLEAU_IMAGE}:
                continue
            if vtype != VisualType.EQUATION and conf < 0.35:
                continue
        elif mode == "fast":
            if vtype not in {VisualType.EQUATION, VisualType.SCHEMA_TECHNIQUE, VisualType.GRAPHIQUE, VisualType.TABLEAU_IMAGE}:
                continue
        elif mode == "full":
            pass

        kept.append((p, norm, item))

    if mode == "auto":
        limit = MAX_IMAGES_PER_DOCUMENT_AUTO
    elif mode == "fast":
        limit = MAX_IMAGES_PER_DOCUMENT_FAST
    else:
        limit = MAX_IMAGES_PER_DOCUMENT_FULL

    kept = sorted(
        kept,
        key=lambda x: (
            priority.get(x[0].visual_type, 0),
            float(x[0].visual_type_confidence or 0.0),
            x[0].width * x[0].height,
        ),
        reverse=True,
    )[:limit]

    return kept


def _inject_images(
    chunks: list[str],
    items: list[dict],
    source_type: str,
    vision_mode: str,
    formula_mode: str = "off",
) -> tuple[list[str], list[str]]:
    from modules.extraction.visual.image import process_image_bytes, VisualType
    from modules.extraction.visual.vision import describe_image_batch

    mode = _normalize_vision_mode(vision_mode)
    if not items or mode == "text_only":
        return chunks, []

    processed_list = []
    for item in items:
        norm = _safe_normalize_image(item["bytes"])
        if norm is None:
            continue
        try:
            p = process_image_bytes(
                norm,
                source_type,
                page_number=item.get("page"),
                slide_number=item.get("slide"),
                filename=item.get("filename"),
                caption=item.get("caption"),
            )
            processed_list.append((p, norm, item))
        except Exception as exc:
            logger.debug("Image ignorée : %s", exc)

    processed_list = _filter_processed_images(processed_list, mode)
    if not processed_list:
        return chunks, []

    # Warmup uniquement si des images seront vraiment traitées.
    try:
        from modules.extraction.visual.vision import warmup_vision_backend
        warmup_vision_backend()
    except Exception as exc:
        logger.debug("Warmup vision ignoré : %s", exc)

    # Hints de contexte par image : on passe les premiers caractères du chunk associé.
    cidx = _build_chunk_index(chunks)
    context_hints: dict[int, str] = {}
    for p, _, item in processed_list:
        num = item.get("slide") or item.get("page")
        tidx = cidx.get(num) if num else None
        if tidx is not None and 0 <= tidx < len(chunks):
            context_hints[id(p)] = chunks[tidx][:600]

    results = describe_image_batch(
        [p for p, _, _ in processed_list],
        context_hints=context_hints,
    )

    enriched = list(chunks)
    orphans: list[str] = []

    for (p, norm, item), vr in zip(processed_list, results):
        if not vr.description:
            continue

        num = item.get("slide") or item.get("page")
        tidx = cidx.get(num) if num else None

        fml_section = ""
        if _normalize_formula_mode(formula_mode) != "off" and p.visual_type == VisualType.EQUATION:
            try:
                from modules.extraction.formula.formula import extract_formulas
                fmls = extract_formulas(
                    image_bytes=norm,
                    page_number=item.get("page"),
                    formula_mode=formula_mode,
                )
                if fmls:
                    fml_section = "\n[FORMULES DÉTECTÉES]\n" + "".join(
                        f"  {i}. LaTeX: {f.latex}\n"
                        for i, f in enumerate(fmls, 1)
                    )
            except Exception as exc:
                logger.debug("Formule image ignorée : %s", exc)

        addition = f"\n[IMAGES]\n  • {vr.description}" + fml_section

        if tidx is not None:
            enriched[tidx] = enriched[tidx].rstrip() + addition
        else:
            orphans.append(vr.description)

    return enriched, orphans


# ──────────────────────────────────────────────────────────────────────────────
# Pipelines par type fichier
# ──────────────────────────────────────────────────────────────────────────────

def _run_pdf(path: Path, vision_mode: str, formula_mode: str = "off") -> ExtractionResult:
    from modules.extraction.text.pdf_native import extract_pdf_native
    from modules.extraction.text.pdf_ocr import extract_pdf_ocr, merge_native_and_ocr

    native = extract_pdf_native(str(path))
    final_chunks = native.text_chunks
    confidence = native.confidence_score

    if native.ocr_needed_pages:
        ocr = extract_pdf_ocr(str(path), target_pages=native.ocr_needed_pages)
        final_chunks = merge_native_and_ocr(native.text_chunks, ocr)
        native.extraction_errors.extend(ocr.extraction_errors)
        confidence = native.confidence_score * 0.7 + ocr.confidence_score * 0.3

    enriched = _inject_formulas(final_chunks, path.name, formula_mode=formula_mode)

    image_items = []
    if _normalize_vision_mode(vision_mode) != "text_only":
        try:
            import fitz
            doc = fitz.open(str(path))
            for pr in [p for p in native.pages if getattr(p, "has_images", False)]:
                for img_info in doc[pr.page_number - 1].get_images(full=True):
                    image_items.append({
                        "bytes": doc.extract_image(img_info[0])["image"],
                        "page": pr.page_number,
                        "slide": None,
                    })
            doc.close()
        except Exception as exc:
            logger.warning("Images PDF : %s", exc)

    image_items = _limit_images_per_slide(image_items)
    enriched, orphans = _inject_images(enriched, image_items, "pdf_page", vision_mode, formula_mode=formula_mode)

    tags = list(set(native.tags))
    if _normalize_formula_mode(formula_mode) == "off":
        tags.append("FORMULAS_DISABLED")
    else:
        tags.append(f"FORMULA_MODE:{formula_mode.upper()}")
    if _normalize_vision_mode(vision_mode) == "text_only":
        tags.append("VISUALS_DISABLED")
    else:
        tags.append(f"VISION_MODE:{_normalize_vision_mode(vision_mode).upper()}")
    if native.ocr_needed_pages:
        tags.append("MIXED_NATIVE_OCR")
    if orphans or any("[IMAGES]" in c for c in enriched):
        tags.append("HAS_VISUAL_DESCRIPTIONS")
    if any("[FORMULES" in c for c in enriched):
        tags.append("HAS_FORMULAS")

    return ExtractionResult(
        file_name=path.name,
        source_path=str(path.resolve()),
        file_category=FileCategory.PDF_NATIVE if not native.ocr_needed_pages else FileCategory.PDF_OCR,
        text_chunks=enriched,
        visual_chunks=orphans,
        title=native.metadata.title,
        author=native.metadata.author,
        creation_date=native.metadata.creation_date,
        page_count=native.metadata.page_count,
        detected_rd_sections=native.detected_rd_sections,
        tags=tags,
        confidence_score=round(confidence, 2),
        extraction_errors=native.extraction_errors,
        ocr_needed_pages=native.ocr_needed_pages,
    )


def _run_office(path: Path, vision_mode: str, formula_mode: str = "off") -> ExtractionResult:
    from modules.extraction.text.office import extract_office

    office = extract_office(str(path))

    enriched = _inject_formulas(office.text_chunks, path.name, formula_mode=formula_mode)
    if _normalize_formula_mode(formula_mode) != "off" and office.file_type == "docx":
        enriched = _run_docx_omml(path, enriched, formula_mode=formula_mode)
    elif _normalize_formula_mode(formula_mode) != "off" and office.file_type == "pptx":
        enriched = _run_pptx_omml(path, enriched, formula_mode=formula_mode)

    image_items = []
    if _normalize_vision_mode(vision_mode) != "text_only":
        try:
            if office.file_type == "docx":
                import zipfile
                with zipfile.ZipFile(str(path)) as zf:
                    for mf in [f for f in zf.namelist() if f.startswith("word/media/")]:
                        image_items.append({
                            "bytes": zf.read(mf),
                            "page": None,
                            "slide": None,
                            "filename": Path(mf).name,
                        })
            elif office.visual_candidates:
                from pptx import Presentation
                prs = Presentation(str(path))
                for snum in office.visual_candidates:
                    for shape in prs.slides[snum - 1].shapes:
                        if hasattr(shape, "image"):
                            image_items.append({
                                "bytes": shape.image.blob,
                                "slide": snum,
                                "page": None,
                            })
        except Exception as exc:
            logger.warning("Collecte images office : %s", exc)

    image_items = _limit_images_per_slide(image_items)
    enriched, orphans = _inject_images(
        enriched,
        image_items,
        "docx_document" if office.file_type == "docx" else "pptx_slide",
        vision_mode,
        formula_mode=formula_mode,
    )

    tags = list(set(office.tags))
    if _normalize_formula_mode(formula_mode) == "off":
        tags.append("FORMULAS_DISABLED")
    else:
        tags.append(f"FORMULA_MODE:{formula_mode.upper()}")
    if _normalize_vision_mode(vision_mode) == "text_only":
        tags.append("VISUALS_DISABLED")
    else:
        tags.append(f"VISION_MODE:{_normalize_vision_mode(vision_mode).upper()}")
    if orphans or any("[IMAGES]" in c for c in enriched):
        tags.append("HAS_VISUAL_DESCRIPTIONS")
    if any("[FORMULES" in c for c in enriched):
        tags.append("HAS_FORMULAS")

    return ExtractionResult(
        file_name=path.name,
        source_path=str(path.resolve()),
        file_category=FileCategory.DOCX if office.file_type == "docx" else FileCategory.PPTX,
        text_chunks=enriched,
        visual_chunks=orphans,
        title=office.metadata.title,
        author=office.metadata.author,
        page_count=office.metadata.slide_count or 0,
        detected_rd_sections=office.detected_rd_sections,
        tags=tags,
        confidence_score=office.confidence_score,
        extraction_errors=office.extraction_errors,
    )


def _run_email(path: Path, formula_mode: str = "off") -> ExtractionResult:
    from modules.extraction.text.email_parser import extract_email

    email_result = extract_email(str(path))
    attachment_paths = []

    for att in [a for a in email_result.attachments if a.is_rd_relevant]:
        if att.content:
            try:
                tmp = Path(tempfile.mkdtemp(prefix="ennosmart_att_")) / att.filename
                tmp.write_bytes(att.content)
                attachment_paths.append(str(tmp))
            except Exception:
                pass

    enriched = _inject_formulas(email_result.text_chunks, path.name, formula_mode=formula_mode)
    tags = list(set(email_result.tags))
    if _normalize_formula_mode(formula_mode) == "off":
        tags.append("FORMULAS_DISABLED")
    else:
        tags.append(f"FORMULA_MODE:{formula_mode.upper()}")
    if any("[FORMULES" in c for c in enriched):
        tags.append("HAS_FORMULAS")

    return ExtractionResult(
        file_name=path.name,
        source_path=str(path.resolve()),
        file_category=FileCategory.EMAIL,
        text_chunks=enriched,
        attachments_paths=attachment_paths,
        detected_rd_sections=email_result.detected_rd_sections,
        tags=tags,
        confidence_score=email_result.confidence_score,
        extraction_errors=email_result.extraction_errors,
    )


def _run_excel(path: Path, formula_mode: str = "off") -> ExtractionResult:
    from modules.extraction.structured.excel_struct import extract_excel

    excel = extract_excel(str(path))
    enriched = list(excel.text_chunks)

    if _normalize_formula_mode(formula_mode) != "off":
        try:
            from modules.extraction.formula.formula import extract_formulas

            for sheet in excel.sheets:
                sidx = next((i for i, c in enumerate(enriched) if sheet.name in c), len(enriched) - 1)
                seen: set[str] = set()
                section = ""

                for addr, cell in sheet.raw_cells.items():
                    value = str(cell.value or "")
                    if value.startswith("="):
                        for r in extract_formulas(excel_formula=value, formula_mode=formula_mode):
                            k = re.sub(r"\s+", "", r.latex.strip().lower())
                            if k and k not in seen:
                                seen.add(k)
                                section += f"  • {addr}: {r.latex}\n"

                if section and 0 <= sidx < len(enriched):
                    enriched[sidx] = enriched[sidx].rstrip() + "\n[FORMULES DÉTECTÉES]\n" + section

        except Exception as exc:
            logger.debug("Formules Excel ignorées : %s", exc)

    tags = list(set(excel.tags))
    if _normalize_formula_mode(formula_mode) == "off":
        tags.append("FORMULAS_DISABLED")
    else:
        tags.append(f"FORMULA_MODE:{formula_mode.upper()}")
    if any("[FORMULES" in c for c in enriched):
        tags.append("HAS_FORMULAS")

    return ExtractionResult(
        file_name=path.name,
        source_path=str(path.resolve()),
        file_category=FileCategory.EXCEL,
        text_chunks=enriched,
        structured_data=excel.structured_data,
        detected_rd_sections=excel.detected_rd_sections,
        tags=tags,
        confidence_score=excel.confidence_score,
        extraction_errors=excel.extraction_errors,
    )


def _run_image(path: Path, vision_mode: str, formula_mode: str = "off") -> ExtractionResult:
    from modules.extraction.visual.image import process_image_file, VisualType
    from modules.extraction.visual.vision import describe_image

    processed = process_image_file(str(path))

    if _normalize_vision_mode(vision_mode) == "text_only":
        return ExtractionResult(
            file_name=path.name,
            source_path=str(path.resolve()),
            file_category=FileCategory.IMAGE,
            text_chunks=[],
            visual_chunks=[],
            tags=list(set(processed.tags + ["VISUAL_DISABLED"])),
            confidence_score=0.0,
            extraction_errors=processed.processing_errors,
        )

    vr = describe_image(processed)
    chunk = vr.description or ""

    if _normalize_formula_mode(formula_mode) != "off" and processed.visual_type == VisualType.EQUATION and chunk:
        try:
            from modules.extraction.formula.formula import extract_formulas
            fmls = extract_formulas(image_bytes=processed.image_bytes, formula_mode=formula_mode)
            if fmls:
                chunk = chunk.rstrip() + "\n[FORMULES DÉTECTÉES]\n" + "".join(
                    f"  {i}. LaTeX: {f.latex}\n"
                    for i, f in enumerate(fmls, 1)
                )
        except Exception as exc:
            logger.debug("Formule image ignorée : %s", exc)

    tags = list(set(processed.tags + vr.tags))
    if _normalize_formula_mode(formula_mode) == "off":
        tags.append("FORMULAS_DISABLED")
    else:
        tags.append(f"FORMULA_MODE:{formula_mode.upper()}")
    if "[FORMULES" in chunk:
        tags.append("HAS_FORMULAS")

    return ExtractionResult(
        file_name=path.name,
        source_path=str(path.resolve()),
        file_category=FileCategory.IMAGE,
        text_chunks=[],
        visual_chunks=[chunk] if chunk else [],
        tags=tags,
        confidence_score=1.0 if not processed.skip_vision else 0.0,
        extraction_errors=processed.processing_errors + ([vr.error] if vr.error else []),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Point d'entrée
# ──────────────────────────────────────────────────────────────────────────────

def extract(
    file_path,
    source_tag: SourceTag = SourceTag.DE_DOC,
    vision_mode: str = "text_only",
    formula_mode: str = "off",
    enable_formulas: Optional[bool] = None,
    mode: Optional[str] = None,
) -> ExtractionResult:
    """
    Paramètres recommandés :
      vision_mode  : text_only | auto | fast | full
      formula_mode : off | fast | explain

    Défaut evidence-first / tests NLP :
      vision_mode="text_only"
      formula_mode="off"

    Pour production enrichie :
      vision_mode="full"
      formula_mode="fast"

    Compatibilité :
      - enable_formulas=False équivaut à formula_mode="off"
      - mode="text" ou "text_only" désactive vision
      - mode="fast" garde vision fast si vision_mode non fourni
      - mode="full" garde vision full si demandé explicitement par CLI
    """
    path = Path(file_path)

    # Compatibilité avec anciens scripts : --mode text/fast/full
    if mode:
        m = str(mode).lower().strip()
        if m in {"text", "text_only"}:
            vision_mode = "text_only"
        elif m == "fast" and vision_mode in {"auto", "", None}:
            vision_mode = "fast"
        elif m == "full" and vision_mode in {"", None}:
            vision_mode = "full"

    vision_mode = _normalize_vision_mode(vision_mode)
    formula_mode = _normalize_formula_mode(formula_mode, enable_formulas=enable_formulas)

    if not path.exists():
        return ExtractionResult(
            file_name=path.name,
            source_path=str(path),
            file_category=FileCategory.UNKNOWN,
            extraction_errors=[f"Fichier introuvable : {path}"],
        )

    category = _detect_category(path)
    logger.info(
        "Extraction : %s [%s] vision=%s formula=%s",
        path.name, category.value, vision_mode, formula_mode,
    )

    try:
        if category in (FileCategory.PDF_NATIVE, FileCategory.PDF_OCR):
            result = _run_pdf(path, vision_mode, formula_mode=formula_mode)
        elif category in (FileCategory.DOCX, FileCategory.PPTX):
            result = _run_office(path, vision_mode, formula_mode=formula_mode)
        elif category == FileCategory.EMAIL:
            result = _run_email(path, formula_mode=formula_mode)
        elif category == FileCategory.EXCEL:
            result = _run_excel(path, formula_mode=formula_mode)
        elif category == FileCategory.IMAGE:
            result = _run_image(path, vision_mode, formula_mode=formula_mode)
        else:
            return ExtractionResult(
                file_name=path.name,
                source_path=str(path),
                file_category=FileCategory.UNKNOWN,
                extraction_errors=[f"Type non supporté : {path.suffix}"],
            )

        result.source_tag = source_tag

        # Pièces jointes email
        if result.attachments_paths:
            for att_path in result.attachments_paths:
                try:
                    ar = extract(
                        att_path,
                        source_tag=source_tag,
                        vision_mode=vision_mode,
                        formula_mode=formula_mode,
                    )
                    result.text_chunks.extend(ar.text_chunks)
                    result.visual_chunks.extend(ar.visual_chunks)
                except Exception as exc:
                    logger.warning("PJ %s : %s", att_path, exc)

        logger.info(
            "Résultat : %d chunks texte, %d visuels",
            len(result.text_chunks),
            len(result.visual_chunks),
        )
        return result

    except Exception as exc:
        logger.error("Erreur extraction %s : %s", path.name, exc, exc_info=True)
        return ExtractionResult(
            file_name=path.name,
            source_path=str(path),
            file_category=category,
            extraction_errors=[str(exc)],
        )


# Alias compatibilité
extract_file = extract
extract_document = extract
process_file = extract
process_document = extract
run_extraction = extract
route_file = extract
extract_any = extract
