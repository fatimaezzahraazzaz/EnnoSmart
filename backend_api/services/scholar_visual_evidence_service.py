# -*- coding: utf-8 -*-
from __future__ import annotations

"""Extraction de figures originales pour les livrables EnnoScholar.

Le service ne demande pas au modèle vision de réinterpréter les figures. Il
conserve l'image originale, sa légende, sa page et sa provenance, afin que la
Phase 5 puisse l'insérer après un passage déjà rédigé qui cite la même source.
"""

import hashlib
import io
import json
import os
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from db.models import Article, Document, Project


VISUAL_SCHEMA_VERSION = "scholar_visual_evidence_v1_2_metric_ranked"
MAX_FIGURES_PER_SOURCE = max(
    1,
    int(os.getenv("ENNOSCHOLAR_VISUAL_MAX_FIGURES_PER_SOURCE", "8")),
)
MAX_PROJECT_DOCUMENTS = max(
    1,
    int(os.getenv("ENNOSCHOLAR_VISUAL_MAX_PROJECT_DOCUMENTS", "20")),
)
REMOTE_FETCH_ENABLED = os.getenv(
    "ENNOSCHOLAR_VISUAL_REMOTE_FETCH",
    "1",
).strip().lower() in {"1", "true", "yes", "on"}
MAX_REMOTE_BYTES = int(
    os.getenv(
        "ENNOSCHOLAR_REMOTE_CONTENT_MAX_BYTES",
        str(100 * 1024 * 1024),
    )
)

_CAPTION_RE = re.compile(
    r"^\s*(?P<label>fig(?:ure)?\.?|table(?:au)?|graphique|sch[ée]ma)"
    r"\s*(?P<number>[A-Z]?\d+(?:[.\-]\d+)*(?:[A-Z])?)?"
    r"\s*(?:[:.\-–—]\s*|\s+)(?P<caption>.{8,})$",
    flags=re.I | re.S,
)
_RESULT_CAPTION_RE = re.compile(
    r"\b(?:result|performance|compar|experiment|validation|evaluation|"
    r"accuracy|loss|error|precision|recall|score|metric|measurement|"
    r"coverage|mae|tradeoff|curve|heatmap|diagnostic|robustness|ablation|"
    r"résultat|précision|erreur|mesure|essai|validation|comparaison|courbe)\b",
    flags=re.I,
)


def _safe_text(value: Any, limit: int = 0) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit].strip() if limit and len(text) > limit else text


def _slug(value: Any, limit: int = 90) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return (re.sub(r"_+", "_", text).strip("_") or "source")[:limit]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _project_ennoscholar_dir(project: Project) -> Path:
    root = Path(os.getenv("ENNOSMART_STORAGE_ROOT", "C:/EnnoSmart/storage"))
    return (
        root
        / "organismes"
        / _slug(getattr(project, "organisme", "") or "organisme")
        / "projects"
        / _slug(getattr(project, "project_name", "") or "project")
        / "years"
        / _slug(getattr(project, "year", "") or "year")
        / "ennoscholar"
    )


def visual_assets_dir(project: Project) -> Path:
    return (
        _project_ennoscholar_dir(project)
        / "state_of_art_payload"
        / "visual_evidence"
        / "assets"
    )


def resolve_visual_asset(project: Project, visual_id: str) -> Optional[Path]:
    """Résout un identifiant opaque sans accepter de chemin fourni par le client."""

    clean_id = re.sub(r"[^A-Za-z0-9_-]", "", str(visual_id or ""))[:120]
    if not clean_id or clean_id != str(visual_id or ""):
        return None
    root = visual_assets_dir(project).resolve()
    for extension in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = (root / f"{clean_id}{extension}").resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _pdf_sha256(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def _image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def _is_useful_image(image_bytes: bytes) -> bool:
    width, height = _image_dimensions(image_bytes)
    if width < 280 or height < 150 or width * height < 70_000:
        return False
    try:
        from PIL import Image, ImageStat

        with Image.open(io.BytesIO(image_bytes)).convert("L") as image:
            stat = ImageStat.Stat(image)
            return float(stat.stddev[0] or 0.0) >= 4.0
    except Exception:
        return True


def _caption_blocks(page: Any) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for raw in page.get_text("blocks") or []:
        if len(raw) < 5:
            continue
        text = _safe_text(raw[4], 1800).replace("\n", " ")
        match = _CAPTION_RE.match(text)
        if not match:
            continue
        label = _safe_text(match.group("label"), 30)
        number = _safe_text(match.group("number"), 30)
        caption = _safe_text(match.group("caption"), 1400)
        blocks.append(
            {
                "rect": (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])),
                "figure_label": " ".join(item for item in (label, number) if item),
                "caption": caption,
                "raw_caption": text,
            }
        )
    return blocks


def _text_context(page: Any, caption_rect: tuple[float, float, float, float]) -> str:
    x0, y0, x1, y1 = caption_rect
    rows: List[tuple[float, str]] = []
    for raw in page.get_text("blocks") or []:
        if len(raw) < 5:
            continue
        bx0, by0, bx1, by1 = map(float, raw[:4])
        text = _safe_text(raw[4], 1200).replace("\n", " ")
        if not text or _CAPTION_RE.match(text):
            continue
        horizontal_overlap = max(0.0, min(x1, bx1) - max(x0, bx0))
        if horizontal_overlap <= 0 and not (bx0 <= x0 <= bx1 or x0 <= bx0 <= x1):
            continue
        distance = min(abs(y0 - by1), abs(by0 - y1))
        if distance <= float(page.rect.height) * 0.24:
            rows.append((distance, text))
    return " ".join(text for _, text in sorted(rows)[:3])[:2400]


def _candidate_crop_rect(
    page: Any,
    caption_rect: tuple[float, float, float, float],
) -> Any:
    import fitz

    page_rect = page.rect
    cx0, cy0, cx1, cy1 = caption_rect
    image_rects: List[Any] = []
    for image in page.get_images(full=True) or []:
        try:
            image_rects.extend(page.get_image_rects(image[0]) or [])
        except Exception:
            continue
    above = [
        rect
        for rect in image_rects
        if rect.y1 <= cy1 + 8
        and rect.y1 >= cy0 - page_rect.height * 0.62
        and max(0.0, min(cx1, rect.x1) - max(cx0, rect.x0))
        >= min(rect.width, max(1.0, cx1 - cx0)) * 0.18
    ]
    if above:
        target = min(above, key=lambda rect: abs(cy0 - rect.y1))
        return fitz.Rect(
            max(page_rect.x0, target.x0 - 8),
            max(page_rect.y0, target.y0 - 8),
            min(page_rect.x1, target.x1 + 8),
            min(page_rect.y1, target.y1 + 8),
        )

    # Les courbes et schémas sont souvent dessinés en vecteurs. Dans ce cas,
    # on rend la zone au-dessus de la légende, bornée par le dernier paragraphe
    # long afin d'éviter de capturer toute la page.
    top = max(page_rect.y0 + 6, cy0 - page_rect.height * 0.48)
    for raw in page.get_text("blocks") or []:
        if len(raw) < 5:
            continue
        text = _safe_text(raw[4], 2000).replace("\n", " ")
        by1 = float(raw[3])
        if len(text) >= 180 and by1 < cy0 - 16 and by1 > top:
            top = by1 + 5
    left = max(page_rect.x0 + 8, min(cx0, page_rect.x0 + page_rect.width * 0.08))
    right = min(page_rect.x1 - 8, max(cx1, page_rect.x1 - page_rect.width * 0.08))
    return fitz.Rect(left, top, right, max(top + 10, cy0 - 4))


def _render_crop(page: Any, rect: Any) -> bytes:
    if rect.width < 120 or rect.height < 80:
        return b""
    import fitz

    pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=rect, alpha=False)
    try:
        return pixmap.tobytes("png")
    finally:
        del pixmap


def _extract_pdf_figures(
    *,
    project: Project,
    pdf_bytes: bytes,
    source_kind: str,
    source_id: str,
    source_title: str,
    citation_label: str = "",
    article_id: Optional[int] = None,
    document_id: Optional[int] = None,
    target_verrous: Optional[Iterable[str]] = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    import fitz

    source_hash = _pdf_sha256(pdf_bytes)
    manifest_dir = visual_assets_dir(project).parent / "manifests"
    manifest_path = manifest_dir / f"{_slug(source_kind)}_{_slug(source_id)}.json"
    cached = _read_json(manifest_path)
    cached_items = cached.get("items") if isinstance(cached.get("items"), list) else []
    if (
        cached.get("schema_version") == VISUAL_SCHEMA_VERSION
        and cached.get("source_sha256") == source_hash
        and cached_items
        and all(resolve_visual_asset(project, str(item.get("visual_id") or "")) for item in cached_items)
    ):
        output = []
        for item in cached_items:
            refreshed = dict(item)
            refreshed["citation_label"] = citation_label or refreshed.get("citation_label") or ""
            refreshed["target_verrous"] = list(target_verrous or refreshed.get("target_verrous") or [])
            output.append(refreshed)
        return output

    assets = visual_assets_dir(project)
    assets.mkdir(parents=True, exist_ok=True)
    candidates: List[Dict[str, Any]] = []
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        ranked_captions: List[tuple[float, int, int, Dict[str, Any]]] = []
        for page_index in range(int(document.page_count or 0)):
            page = document.load_page(page_index)
            for caption_index, caption_block in enumerate(_caption_blocks(page), 1):
                caption = caption_block["caption"]
                # Un paragraphe narratif commençant par « Figure 2 montre… »
                # n'est pas la légende imprimée sous la figure.
                if len(caption) > 520:
                    continue
                rank = 0.58
                if len(caption) >= 30:
                    rank += 0.12
                if _RESULT_CAPTION_RE.search(caption):
                    rank += 0.26
                if re.search(
                    r"\b(?:accuracy|loss|error|precision|recall|coverage|"
                    r"mae|rmse|mse|auc|f1|score|metric|gain|risk|"
                    r"précision|erreur|couverture|métrique|risque)\b",
                    caption,
                    re.I,
                ):
                    rank += 0.16
                if str(caption_block.get("figure_label") or "").casefold().startswith(
                    ("fig", "graph", "sch")
                ):
                    rank += 0.10
                if re.search(r"\b\d+(?:[.,]\d+)?\s*(?:%|mae|rmse|auc|f1)?\b", caption, re.I):
                    rank += 0.06
                ranked_captions.append(
                    (rank, page_index, caption_index, caption_block)
                )

        # On parcourt tout le document avant de limiter : les courbes de
        # résultats sont généralement après les figures de protocole.
        for rank, page_index, caption_index, caption_block in sorted(
            ranked_captions,
            key=lambda item: (-item[0], item[1], item[2]),
        ):
                page = document.load_page(page_index)
                rect = _candidate_crop_rect(page, caption_block["rect"])
                image_bytes = _render_crop(page, rect)
                if not image_bytes or not _is_useful_image(image_bytes):
                    continue
                identity_seed = (
                    f"{source_hash}:{page_index + 1}:{caption_index}:"
                    f"{caption_block['raw_caption']}"
                )
                visual_id = "V" + hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()[:20]
                asset_path = assets / f"{visual_id}.png"
                asset_path.write_bytes(image_bytes)
                caption = caption_block["caption"]
                candidates.append(
                    {
                        "visual_id": visual_id,
                        "schema_version": VISUAL_SCHEMA_VERSION,
                        "source_kind": source_kind,
                        "source_id": str(source_id),
                        "article_id": article_id,
                        "document_id": document_id,
                        "citation_label": citation_label,
                        "source_title": _safe_text(source_title, 900),
                        "page": page_index + 1,
                        "figure_label": caption_block["figure_label"],
                        "caption": caption,
                        "context": _text_context(page, caption_block["rect"]),
                        "asset_path": str(asset_path),
                        "asset_mime_type": "image/png",
                        "asset_sha256": hashlib.sha256(image_bytes).hexdigest(),
                        "width": _image_dimensions(image_bytes)[0],
                        "height": _image_dimensions(image_bytes)[1],
                        "quality_score": round(min(1.0, rank), 3),
                        "ranking_score": round(rank, 3),
                        "target_verrous": list(target_verrous or []),
                        "provenance": dict(provenance or {}),
                        "selection_policy": "caption_and_source_relevance_no_vision_rewrite",
                    }
                )
                if len(candidates) >= MAX_FIGURES_PER_SOURCE:
                    break
    finally:
        document.close()

    _write_json(
        manifest_path,
        {
            "ok": True,
            "schema_version": VISUAL_SCHEMA_VERSION,
            "source_sha256": source_hash,
            "source_kind": source_kind,
            "source_id": str(source_id),
            "generated_at": _utc_now(),
            "items": candidates,
        },
    )
    return candidates


def _fulltext_payload(fulltext_info: Dict[str, Any]) -> Dict[str, Any]:
    path = _safe_text(fulltext_info.get("path"), 4000)
    return _read_json(Path(path)) if path and Path(path).is_file() else {}


def _existing_article_pdf(
    article: Article,
    fulltext_info: Dict[str, Any],
) -> Optional[Path]:
    source_json = article.source_json if isinstance(article.source_json, dict) else {}
    payload = _fulltext_payload(fulltext_info)
    keys = (
        "uploaded_pdf_path",
        "local_pdf_path",
        "saved_pdf_path",
        "pdf_path",
        "downloaded_pdf_path",
    )
    for container in (source_json, payload, fulltext_info):
        for key in keys:
            candidate = _safe_text(container.get(key), 4000)
            if candidate and Path(candidate).is_file():
                return Path(candidate)
    return None


def _materialize_verified_remote_pdf(
    project: Project,
    article: Article,
    fulltext_info: Dict[str, Any],
) -> Optional[Path]:
    if not REMOTE_FETCH_ENABLED:
        return None
    payload = _fulltext_payload(fulltext_info)
    if payload.get("content_source_kind") != "pdf":
        return None
    if payload.get("verified_pdf") is not True or payload.get("same_article") is not True:
        return None
    url = _safe_text(
        payload.get("fulltext_final_url") or payload.get("fulltext_source_url"),
        4000,
    )
    if not url:
        return None
    try:
        from services.http_client import GLOBAL_FETCHER

        ok, info, content = GLOBAL_FETCHER.fetch_bytes(
            url=url,
            headers={
                "Accept": "application/pdf,application/octet-stream;q=0.8,*/*;q=0.2",
                "User-Agent": os.getenv(
                    "ENNOSCHOLAR_BROWSER_USER_AGENT",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EnnoScholar/1.0",
                ),
            },
            max_bytes=MAX_REMOTE_BYTES,
        )
        if not ok or not content or not content.startswith(b"%PDF-"):
            return None
        expected_sha = _safe_text(payload.get("remote_sha256"), 128).casefold()
        observed_sha = hashlib.sha256(content).hexdigest()
        if expected_sha and expected_sha != observed_sha:
            return None
        target = (
            _project_ennoscholar_dir(project)
            / "fulltext"
            / "legal_pdf"
            / f"article_{int(article.id)}_{_slug(article.title, 60)}.pdf"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        payload.update(
            {
                "saved_pdf": True,
                "local_pdf_path": str(target),
                "visual_source_materialized_at": _utc_now(),
                "visual_source_final_url": info.get("final_url") or url,
            }
        )
        fulltext_path = _safe_text(fulltext_info.get("path"), 4000)
        if fulltext_path:
            _write_json(Path(fulltext_path), payload)
        source_json = dict(article.source_json) if isinstance(article.source_json, dict) else {}
        source_json["legal_pdf_path"] = str(target)
        article.source_json = source_json
        return target
    except Exception:
        return None


def extract_article_visual_evidence(
    *,
    project: Project,
    article: Article,
    citation_label: str,
    fulltext_info: Dict[str, Any],
) -> List[Dict[str, Any]]:
    pdf_path = _existing_article_pdf(article, fulltext_info)
    if pdf_path is None:
        pdf_path = _materialize_verified_remote_pdf(project, article, fulltext_info)
    if pdf_path is None:
        return []
    try:
        pdf_bytes = pdf_path.read_bytes()
    except Exception:
        return []
    source_json = article.source_json if isinstance(article.source_json, dict) else {}
    return _extract_pdf_figures(
        project=project,
        pdf_bytes=pdf_bytes,
        source_kind="scientific_article",
        source_id=str(article.id),
        source_title=article.title or pdf_path.name,
        citation_label=citation_label,
        article_id=int(article.id),
        target_verrous=(
            source_json.get("target_verrous")
            or source_json.get("covered_verrou_ids")
            or []
        ),
        provenance={
            "source_url": article.url,
            "doi": article.doi,
            "pdf_path": str(pdf_path),
            "fulltext_source_kind": fulltext_info.get("source_kind"),
            "original_figure_preserved": True,
        },
    )


def _store_standalone_image(
    project: Project,
    *,
    image_bytes: bytes,
    source_kind: str,
    source_id: str,
    source_title: str,
    caption: str,
    document_id: Optional[int],
    context: str = "",
) -> Optional[Dict[str, Any]]:
    if not _is_useful_image(image_bytes):
        return None
    digest = hashlib.sha256(image_bytes).hexdigest()
    visual_id = "V" + digest[:20]
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            normalized = io.BytesIO()
            image.convert("RGB").save(normalized, format="PNG")
            output_bytes = normalized.getvalue()
    except Exception:
        return None
    target = visual_assets_dir(project) / f"{visual_id}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(output_bytes)
    width, height = _image_dimensions(output_bytes)
    return {
        "visual_id": visual_id,
        "schema_version": VISUAL_SCHEMA_VERSION,
        "source_kind": source_kind,
        "source_id": source_id,
        "document_id": document_id,
        "citation_label": "",
        "source_title": _safe_text(source_title, 900),
        "page": None,
        "figure_label": "",
        "caption": _safe_text(caption, 1400),
        "context": _safe_text(context, 2400),
        "asset_path": str(target),
        "asset_mime_type": "image/png",
        "asset_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "width": width,
        "height": height,
        "quality_score": 0.42,
        "target_verrous": [],
        "provenance": {
            "project_document": True,
            "original_figure_preserved": True,
        },
        "selection_policy": "caption_and_source_relevance_no_vision_rewrite",
    }


def _office_visuals(
    project: Project,
    document: Document,
    file_bytes: bytes,
) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            names = archive.namelist()
            xml_context_parts: List[str] = []
            for name in names:
                if name.endswith(".xml") and (
                    name.startswith("word/") or name.startswith("ppt/slides/")
                ):
                    try:
                        raw_xml = archive.read(name).decode("utf-8", errors="ignore")
                        xml_context_parts.extend(re.findall(r">([^<>]{3,})<", raw_xml))
                    except Exception:
                        continue
            context = _safe_text(" ".join(xml_context_parts), 2400)
            media_names = [
                name
                for name in names
                if name.startswith(("word/media/", "ppt/media/"))
                and Path(name).suffix.casefold()
                in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
            ]
            for media_name in media_names[:MAX_FIGURES_PER_SOURCE]:
                candidate = _store_standalone_image(
                    project,
                    image_bytes=archive.read(media_name),
                    source_kind="project_document",
                    source_id=str(document.id),
                    source_title=document.filename,
                    caption=f"Illustration originale extraite de {document.filename}",
                    document_id=int(document.id),
                    context=context,
                )
                if candidate:
                    candidate["provenance"]["archive_member"] = media_name
                    output.append(candidate)
    except Exception:
        return []
    return output


def extract_project_document_visuals(
    db: Session,
    project: Project,
) -> List[Dict[str, Any]]:
    """Extrait les figures des documents projet sans les transformer en preuves scientifiques."""

    documents = (
        db.query(Document)
        .filter(Document.project_id == project.id)
        .order_by(Document.created_at.desc(), Document.id.desc())
        .limit(MAX_PROJECT_DOCUMENTS)
        .all()
    )
    output: List[Dict[str, Any]] = []
    for document in documents:
        file_bytes = bytes(document.file_data or b"")
        if not file_bytes:
            path = _safe_text(document.file_path, 4000)
            if path and not path.startswith("db://") and Path(path).is_file():
                try:
                    file_bytes = Path(path).read_bytes()
                except Exception:
                    file_bytes = b""
        if not file_bytes:
            continue
        suffix = Path(document.filename or "").suffix.casefold()
        if suffix == ".pdf" and file_bytes.startswith(b"%PDF-"):
            output.extend(
                _extract_pdf_figures(
                    project=project,
                    pdf_bytes=file_bytes,
                    source_kind="project_document",
                    source_id=str(document.id),
                    source_title=document.filename,
                    document_id=int(document.id),
                    provenance={
                        "project_document": True,
                        "filename": document.filename,
                        "original_figure_preserved": True,
                    },
                )
            )
        elif suffix in {".docx", ".pptx"}:
            output.extend(_office_visuals(project, document, file_bytes))
        elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
            candidate = _store_standalone_image(
                project,
                image_bytes=file_bytes,
                source_kind="project_document",
                source_id=str(document.id),
                source_title=document.filename,
                caption=f"Illustration originale du document {document.filename}",
                document_id=int(document.id),
            )
            if candidate:
                output.append(candidate)

    # Une image identique peut apparaître dans plusieurs archives Office.
    unique: Dict[str, Dict[str, Any]] = {}
    for item in output:
        unique.setdefault(str(item.get("visual_id") or ""), item)
    return list(unique.values())
