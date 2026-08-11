from __future__ import annotations

import math
import re
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


STRUCTURE_VERSION = "ennoamelioration_document_structure_v1"

_IMMUTABLE_OPEN = "[BLOC DOCUMENT IMMUTABLE"
_IMMUTABLE_CLOSE = "[/BLOC DOCUMENT IMMUTABLE]"
_CAPTION_RE = re.compile(
    r"^\s*(?:figure|fig\.?|tableau|table)\s*[0-9ivxlcdm]+\b",
    flags=re.I,
)
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+)+)\.?(?:\s+)(?P<title>\S[\s\S]{1,240})$"
)


def _clean_inline(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("\u00ad", "").replace("\u200b", "")
    return re.sub(r"[ \t]+", " ", text).strip()


def _layout_signature(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean_inline(value)).casefold()
    text = re.sub(r"\d+", "#", text)
    return re.sub(r"\s+", " ", text).strip(" -_|•")


def _bbox(value: Any) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(item) for item in value)
    except Exception:
        return (0.0, 0.0, 0.0, 0.0)
    return values if len(values) == 4 else (0.0, 0.0, 0.0, 0.0)


def _overlap_ratio(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    intersection = (x1 - x0) * (y1 - y0)
    area = max(1.0, (left[2] - left[0]) * (left[3] - left[1]))
    return intersection / area


def _block_text(block: dict[str, Any]) -> str:
    rendered: list[str] = []
    current = ""
    previous_bottom: float | None = None
    for line in block.get("lines") or []:
        spans = line.get("spans") or []
        value = _clean_inline("".join(str(span.get("text") or "") for span in spans))
        if not value:
            continue
        line_bbox = _bbox(line.get("bbox"))
        line_height = max(1.0, line_bbox[3] - line_bbox[1])
        starts_item = bool(re.match(r"^(?:[•✓▪◦]|\d+[.)])\s*", value))
        paragraph_gap = bool(
            previous_bottom is not None
            and line_bbox[1] - previous_bottom > line_height * 0.75
        )
        if current and (starts_item or paragraph_gap):
            rendered.append(current)
            current = value
        else:
            current = f"{current} {value}".strip()
        previous_bottom = line_bbox[3]
    if current:
        rendered.append(current)
    text = "\n".join(rendered).strip()
    heading = _NUMBERED_HEADING_RE.match(text.replace("\n", " "))
    if heading:
        return f"{heading.group('number')}. {_clean_inline(heading.group('title'))}"
    return text


def _table_to_markdown(rows: list[list[Any]]) -> str:
    cleaned = [
        [_clean_inline(cell) if cell is not None else "" for cell in row]
        for row in rows
        if isinstance(row, (list, tuple))
    ]
    width = max((len(row) for row in cleaned), default=0)
    if width <= 0:
        return ""
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    header = cleaned[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in cleaned[1:])
    return "\n".join(lines)


def _immutable_block(
    *,
    block_id: str,
    kind: str,
    page: int | None,
    content: str,
) -> str:
    page_label = f" page={page}" if page is not None else ""
    label = "Figure originale conservée" if kind == "figure" else "Tableau original conservé"
    body = _clean_inline(content) if kind == "figure" else str(content or "").strip()
    return (
        f'{_IMMUTABLE_OPEN} id="{block_id}" type="{kind}"{page_label}]\n'
        f"[{label} à l'identique dans le document source]"
        + (f"\n{body}" if body else "")
        + f"\n{_IMMUTABLE_CLOSE}"
    )


def _page_text_records(page: Any) -> list[dict[str, Any]]:
    try:
        payload = page.get_text("dict", flags=11, sort=True)
    except TypeError:
        payload = page.get_text("dict", sort=True)
    records: list[dict[str, Any]] = []
    for block in payload.get("blocks") or []:
        if int(block.get("type") or 0) != 0:
            continue
        text = _block_text(block)
        if not text:
            continue
        spans = [
            span
            for line in (block.get("lines") or [])
            for span in (line.get("spans") or [])
        ]
        records.append(
            {
                "bbox": _bbox(block.get("bbox")),
                "text": text,
                "fonts": sorted({str(span.get("font") or "") for span in spans}),
                "sizes": sorted({round(float(span.get("size") or 0.0), 2) for span in spans}),
            }
        )
    return records


def _recurring_margin_signatures(
    page_records: list[dict[str, Any]],
) -> set[str]:
    counts: Counter[str] = Counter()
    for page in page_records:
        height = float(page.get("height") or 0.0)
        seen: set[str] = set()
        for record in page.get("text") or []:
            box = record["bbox"]
            if not (box[3] <= height * 0.14 or box[1] >= height * 0.86):
                continue
            signature = _layout_signature(record.get("text"))
            if signature and len(signature) <= 500:
                seen.add(signature)
        counts.update(seen)
    threshold = max(3, math.ceil(len(page_records) * 0.25))
    return {signature for signature, count in counts.items() if count >= threshold}


def _pdf_tables(page: Any, page_number: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    try:
        finder = page.find_tables(
            vertical_strategy="lines_strict",
            horizontal_strategy="lines_strict",
        )
        tables = list(getattr(finder, "tables", None) or [])
    except Exception:
        tables = []
    page_height = float(page.rect.height)
    for index, table in enumerate(tables, start=1):
        box = _bbox(getattr(table, "bbox", None))
        if box[3] <= page_height * 0.14 or box[1] >= page_height * 0.86:
            continue
        try:
            markdown = _table_to_markdown(table.extract() or [])
        except Exception:
            markdown = ""
        if not markdown or len(re.sub(r"[\s|:-]", "", markdown)) < 3:
            continue
        block_id = f"table-p{page_number}-{index}"
        output.append(
            {
                "id": block_id,
                "kind": "table",
                "page": page_number,
                "bbox": box,
                "text": _immutable_block(
                    block_id=block_id,
                    kind="table",
                    page=page_number,
                    content=markdown,
                ),
                "rows": markdown.count("\n") - 1,
            }
        )
    return output


def _pdf_image_groups(page: Any, page_number: int) -> list[dict[str, Any]]:
    height = float(page.rect.height)
    width = float(page.rect.width)
    page_area = max(1.0, height * width)
    images: list[tuple[float, float, float, float]] = []
    try:
        info_rows = page.get_image_info(xrefs=True)
    except Exception:
        info_rows = []
    for row in info_rows or []:
        box = _bbox(row.get("bbox"))
        area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
        if area / page_area < 0.002:
            continue
        if box[3] <= height * 0.14 or box[1] >= height * 0.86:
            continue
        if not any(all(abs(a - b) < 0.5 for a, b in zip(box, old)) for old in images):
            images.append(box)
    images.sort(key=lambda box: (box[1], box[0]))

    groups: list[list[tuple[float, float, float, float]]] = []
    for box in images:
        selected: list[tuple[float, float, float, float]] | None = None
        for group in groups:
            group_top = min(item[1] for item in group)
            group_bottom = max(item[3] for item in group)
            vertical_overlap = max(0.0, min(group_bottom, box[3]) - max(group_top, box[1]))
            min_height = max(1.0, min(group_bottom - group_top, box[3] - box[1]))
            if vertical_overlap / min_height >= 0.45 or abs(group_top - box[1]) <= 18.0:
                selected = group
                break
        if selected is None:
            groups.append([box])
        else:
            selected.append(box)

    return [
        {
            "id": f"figure-p{page_number}-{index}",
            "kind": "figure",
            "page": page_number,
            "bbox": (
                min(item[0] for item in group),
                min(item[1] for item in group),
                max(item[2] for item in group),
                max(item[3] for item in group),
            ),
            "image_count": len(group),
        }
        for index, group in enumerate(groups, start=1)
    ]


def _caption_for_group(
    group: dict[str, Any],
    text_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    box = group["bbox"]
    candidates: list[tuple[float, dict[str, Any]]] = []
    for record in text_records:
        record_box = record["bbox"]
        vertical_gap = record_box[1] - box[3]
        horizontal_overlap = max(0.0, min(record_box[2], box[2]) - max(record_box[0], box[0]))
        if 0.0 <= vertical_gap <= 70.0 and horizontal_overlap > 0 and _CAPTION_RE.match(record["text"]):
            candidates.append((vertical_gap, record))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _extract_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        import fitz  # type: ignore
    except Exception as exc:
        raise RuntimeError("PyMuPDF est requis pour l'import PDF structuré.") from exc

    document = fitz.open(str(path))
    try:
        raw_pages = [
            {
                "page": index + 1,
                "width": float(page.rect.width),
                "height": float(page.rect.height),
                "text": _page_text_records(page),
            }
            for index, page in enumerate(document)
        ]
        repeated = _recurring_margin_signatures(raw_pages)
        rendered_pages: list[str] = []
        page_summaries: list[dict[str, Any]] = []
        assets: list[dict[str, Any]] = []
        heading_count = 0

        for page_index, page in enumerate(document):
            page_number = page_index + 1
            record_page = raw_pages[page_index]
            height = record_page["height"]
            text_records: list[dict[str, Any]] = []
            removed_margin_blocks = 0
            for record in record_page["text"]:
                box = record["bbox"]
                in_margin = box[3] <= height * 0.14 or box[1] >= height * 0.86
                signature = _layout_signature(record["text"])
                page_number_only = bool(re.fullmatch(r"(?:page\s*)?\d{1,4}", record["text"], re.I))
                if in_margin and (signature in repeated or page_number_only):
                    removed_margin_blocks += 1
                    continue
                text_records.append(record)

            tables = _pdf_tables(page, page_number)
            figures = _pdf_image_groups(page, page_number)
            caption_ids: set[int] = set()
            for figure in figures:
                caption = _caption_for_group(figure, text_records)
                caption_text = caption["text"] if caption else ""
                if caption is not None:
                    caption_ids.add(id(caption))
                figure["caption"] = caption_text
                figure["text"] = _immutable_block(
                    block_id=figure["id"],
                    kind="figure",
                    page=page_number,
                    content=caption_text,
                )

            events: list[dict[str, Any]] = []
            for record in text_records:
                if id(record) in caption_ids:
                    continue
                if any(_overlap_ratio(record["bbox"], table["bbox"]) >= 0.45 for table in tables):
                    continue
                events.append({"y": record["bbox"][1], "x": record["bbox"][0], "text": record["text"]})
                if _NUMBERED_HEADING_RE.match(record["text"].replace("\n", " ")):
                    heading_count += 1
            for asset in [*tables, *figures]:
                events.append({"y": asset["bbox"][1], "x": asset["bbox"][0], "text": asset["text"]})
                assets.append(
                    {
                        key: value
                        for key, value in asset.items()
                        if key not in {"text", "bbox"}
                    }
                )
            events.sort(key=lambda item: (item["y"], item["x"]))
            page_text = "\n\n".join(item["text"].strip() for item in events if item["text"].strip())
            rendered_pages.append(page_text)
            page_summaries.append(
                {
                    "page": page_number,
                    "text_blocks": len(text_records),
                    "body_chars": len(page_text),
                    "figures": len(figures),
                    "tables": len(tables),
                    "recurring_margin_blocks_removed": removed_margin_blocks,
                }
            )

        text = "\n\n".join(page for page in rendered_pages if page.strip())
        structure = {
            "version": STRUCTURE_VERSION,
            "source_format": "pdf",
            "page_count": len(document),
            "heading_count": heading_count,
            "asset_count": len(assets),
            "figure_count": sum(1 for asset in assets if asset.get("kind") == "figure"),
            "table_count": sum(1 for asset in assets if asset.get("kind") == "table"),
            "assets": assets,
            "pages": page_summaries,
            "preservation": {
                "source_binary_immutable": True,
                "layout_master": "original_document",
                "revision_mode": "text_patches_only",
                "protected_asset_blocks": True,
                "headers_and_footers_rewritten": False,
            },
            "extraction_engine": "pymupdf_layout_blocks",
            "business_keyword_dictionary": False,
        }
        return text, structure
    finally:
        document.close()


_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"


def _docx_paragraph_text(element: ET.Element) -> str:
    return _clean_inline("".join(node.text or "" for node in element.iter(f"{{{_WORD_NS}}}t")))


def _extract_docx(path: Path) -> tuple[str, dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        body = root.find(f"{{{_WORD_NS}}}body")
        if body is None:
            return "", {}
        blocks: list[str] = []
        assets: list[dict[str, Any]] = []
        table_count = 0
        figure_count = 0
        for element in list(body):
            if element.tag == f"{{{_WORD_NS}}}p":
                text = _docx_paragraph_text(element)
                drawings = list(element.iter(f"{{{_DRAWING_NS}}}inline")) + list(
                    element.iter(f"{{{_DRAWING_NS}}}anchor")
                )
                if drawings:
                    figure_count += 1
                    block_id = f"figure-docx-{figure_count}"
                    blocks.append(
                        _immutable_block(
                            block_id=block_id,
                            kind="figure",
                            page=None,
                            content=text,
                        )
                    )
                    assets.append({"id": block_id, "kind": "figure", "caption": text})
                elif text:
                    blocks.append(text)
            elif element.tag == f"{{{_WORD_NS}}}tbl":
                rows: list[list[str]] = []
                for row in element.findall(f"{{{_WORD_NS}}}tr"):
                    rows.append(
                        [
                            _clean_inline(" ".join(_docx_paragraph_text(p) for p in cell.findall(f"{{{_WORD_NS}}}p")))
                            for cell in row.findall(f"{{{_WORD_NS}}}tc")
                        ]
                    )
                markdown = _table_to_markdown(rows)
                if markdown:
                    table_count += 1
                    block_id = f"table-docx-{table_count}"
                    blocks.append(
                        _immutable_block(
                            block_id=block_id,
                            kind="table",
                            page=None,
                            content=markdown,
                        )
                    )
                    assets.append({"id": block_id, "kind": "table", "rows": len(rows)})

        media_count = len([name for name in archive.namelist() if name.startswith("word/media/")])
    text = "\n\n".join(block for block in blocks if block.strip())
    return text, {
        "version": STRUCTURE_VERSION,
        "source_format": "docx",
        "page_count": None,
        "asset_count": len(assets),
        "figure_count": max(figure_count, media_count),
        "table_count": table_count,
        "assets": assets,
        "preservation": {
            "source_binary_immutable": True,
            "layout_master": "original_document",
            "revision_mode": "text_patches_only",
            "protected_asset_blocks": True,
            "headers_and_footers_rewritten": False,
        },
        "extraction_engine": "docx_body_xml_order",
        "business_keyword_dictionary": False,
    }


def extract_layout_preserving_document(path: str | Path) -> tuple[str, dict[str, Any]]:
    source = Path(path)
    suffix = source.suffix.casefold()
    if suffix == ".pdf":
        return _extract_pdf(source)
    if suffix in {".docx", ".docm"}:
        return _extract_docx(source)
    raise RuntimeError(f"Format sans extraction structurée : {suffix or 'inconnu'}")


def immutable_document_blocks(text: str) -> dict[str, str]:
    pattern = re.compile(
        r"(?ms)^\[BLOC DOCUMENT IMMUTABLE\s+id=\"(?P<id>[^\"]+)\"[^\]]*\]\s*$.*?^\[/BLOC DOCUMENT IMMUTABLE\]\s*$"
    )
    return {match.group("id"): match.group(0) for match in pattern.finditer(str(text or ""))}
