"""Literal table structure; ambiguous layouts never assert column associations."""
import re


def structure(headers, rows, caption="", number=None, *, ambiguous=False, raw_rows=None):
    width = len(headers)
    reliable = bool(rows and width > 1 and len(set(headers)) == width and all(h.strip() for h in headers)
                    and all(len(r) == width and all(c is not None for c in r) for r in rows)
                    and not ambiguous)
    dataset = re.search(r"\bon\s+([\w-]+)\s+dataset\b", caption, re.I)
    return {"number": number, "caption": caption, "headers": headers, "rows": rows,
        "raw_rows": raw_rows if raw_rows is not None else rows,
        "structure_status": "verified_cells" if reliable else "ambiguous",
        "dataset": dataset.group(1) if dataset else None,
        "metric_labels": re.findall(r"\b(?:AUROC|PRO|AUC|F1|Dice|mIoU|AP)\b", caption),
        "unit": "%" if "%" in caption else None,
        "conditions": headers[1:] if reliable else [],
        "uncertainty": "" if reliable else "Cell/column association is not verified; do not assign numerical results."}


def html_table(table):
    from .extraction import scientific_text
    figure = table.find_parent(["figure", "table-wrap"]) or table.find_parent(class_="c-article-table")
    caption_node = (figure.find(["figcaption", "caption"]) or figure.select_one(".c-article-table__caption, .c-article-table__title")) if figure else table.find("caption")
    caption = scientific_text(caption_node) if caption_node else ""
    match = re.search(r"\bTable\s+(\d+|[IVXLCDM]+)\b", caption, re.I)
    headers, rows, raw, ambiguous = [], [], [], bool(table.find("table"))
    grid, header_rows, origins = {}, [], {}
    trs = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    for ri, tr in enumerate(trs[:500]):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        values = [scientific_text(c) for c in cells]
        raw.append(values)
        in_head = bool(tr.find_parent("thead")) or (not rows and not any(
            c.name == "td" for c in cells) and (not header_rows or header_rows[-1] == ri-1))
        if in_head:
            header_rows.append(ri)
        else:
            rows.append(ri)
        ci = 0
        for cell, value in zip(cells, values):
            while (ri, ci) in grid:
                ci += 1
            try:
                height, width = int(cell.get("rowspan", 1)), int(cell.get("colspan", 1))
            except (ValueError, TypeError):
                height = width = 1
                ambiguous = True
            if not (1 <= height <= 500 and 1 <= width <= 128 and ci+width <= 128 and ri+height <= len(trs)):
                ambiguous = True
                height = width = 1
            # Data spanning several numerical columns has no unambiguous metric
            # association. A row label spanning rows is literal and safe to repeat.
            if not in_head and (width != 1 or (height != 1 and ci != 0)):
                ambiguous = True
            for dr in range(height):
                for dc in range(width):
                    key = (ri+dr, ci+dc)
                    if key in grid:
                        ambiguous = True
                    grid[key], origins[key] = value, (ri, ci)
            ci += width
    width = max((c+1 for r, c in grid), default=0)
    for ci in range(width):
        labels, seen = [], set()
        for ri in header_rows:
            if (ri, ci) not in grid:
                ambiguous = True
                continue
            origin = origins[ri, ci]
            if origin not in seen and grid[ri, ci]:
                labels.append(grid[ri, ci])
                seen.add(origin)
        headers.append(" / ".join(labels))
    rows = [[grid.get((ri, ci)) for ci in range(width)] for ri in rows]
    result = structure(headers, rows, caption, match.group(1) if match else None,
        ambiguous=ambiguous or len(trs) > 500, raw_rows=raw)
    result["cell_origins"] = [[list(origins.get((ri, ci), ())) for ci in range(width)] for ri in range(len(trs[:500]))]
    result["source_format"] = "html"
    result["element_id"] = figure.get("id", "") if figure else table.get("id", "")
    return result


def table_text(table):
    lines = [table.get("caption") or "Table (caption unavailable)"]
    if table["headers"]:
        lines.append(" | ".join(table["headers"]))
    if table["structure_status"] == "ambiguous":
        lines.append("UNCERTAIN STRUCTURE: " + table["uncertainty"])
    lines.extend(" | ".join("" if c is None else str(c) for c in row) for row in table["rows"])
    return "\n".join(lines)


def pdf_tables(page):
    if not hasattr(page, "find_tables"):
        return horizontal_rule_tables(page)
    try:
        found = page.find_tables()
    except Exception:
        return horizontal_rule_tables(page)
    result = []
    blocks = page.get_text("blocks")
    for table in found.tables:
        raw = table.extract()
        if not raw:
            continue
        headers = list(table.header.names or [])
        headers = [h or "" for h in headers]
        rows = raw if table.header.external else raw[1:]
        nearby = [b for b in blocks if 0 <= table.bbox[1]-b[3] < 100 and re.search(r"\bTable\s+(?:\d+|[IVXLCDM]+)\b", b[4], re.I)]
        caption = nearby[-1][4].strip() if nearby else ""
        match = re.search(r"\bTable\s+(\d+|[IVXLCDM]+)\b", caption, re.I)
        ambiguous = any(not re.search(r"[A-Za-z]", h) for h in headers)
        item = structure(headers, rows, caption, match.group(1) if match else None, ambiguous=ambiguous, raw_rows=raw)
        item.update(source_format="pdf", bbox=list(table.bbox))
        result.append(item)
    result.extend(horizontal_rule_tables(page, result))
    return result


def horizontal_rule_tables(page, existing=()):
    """Conservative fallback for tables with horizontal rules but no vertical grid.

    Require an explicit caption, a ruled header and equal, non-overlapping cell
    columns on every baseline. Never split a prose line into invented cells.
    """
    blocks = [b for b in page.get_text("dict").get("blocks", []) if b.get("type") == 0]
    captions = []
    for block in blocks:
        text = " ".join("".join(s["text"] for s in line["spans"]) for line in block["lines"])
        match = re.match(r"Table\s+(\d+|[IVXLCDM]+)[.\s:]", text, re.I)
        if match:
            captions.append((block["bbox"], text, match.group(1)))
    captions.sort(key=lambda c: c[0][1])
    rules = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                a, b = item[1:3]
                if abs(a.y-b.y) < 1 and abs(a.x-b.x) > 10:
                    rules.append((min(a.x, b.x), max(a.x, b.x), (a.y+b.y)/2))
    # Some publishers draw one horizontal rule as separate adjoining column segments.
    merged = []
    for left, right, y in sorted(rules, key=lambda r: (round(r[2], 1), r[0])):
        if merged and abs(merged[-1][2]-y) < 1 and left <= merged[-1][1]+2:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]), merged[-1][2])
        else:
            merged.append((left, right, y))
    rules = [r for r in merged if r[1]-r[0] > 80]
    lines = [line for block in blocks for line in block["lines"]]
    result = []
    for index, (caption_box, caption, number) in enumerate(captions):
        next_caption = captions[index+1][0][1] if index+1 < len(captions) else page.rect.height
        candidates = sorted((r for r in rules if
            0 <= r[2]-caption_box[3] < 60 or
            (caption_box[1]-2 <= r[2] <= caption_box[3] and r[0] > caption_box[0]+20)), key=lambda r: r[2])
        if not candidates:
            continue
        left, right, top = candidates[0]
        levels = sorted(set(r[2] for r in rules if abs(r[0]-left) < 2 and abs(r[1]-right) < 2
                            and top <= r[2] < min(next_caption, top+450)))
        if len(levels) < 3:
            continue
        bottom = levels[-1]
        bbox = [left, top, right, bottom]
        if any(e["bbox"][0] <= (left+right)/2 <= e["bbox"][2]
               and e["bbox"][1] <= (top+bottom)/2 <= e["bbox"][3] for e in existing):
            continue
        cells = sorted((line for line in lines if top-1 <= line["bbox"][1] and line["bbox"][3] <= bottom+1
                        and left-1 <= line["bbox"][0] and line["bbox"][2] <= right+1),
                       key=lambda line: ((line["bbox"][1]+line["bbox"][3])/2, line["bbox"][0]))
        baselines = []
        for cell in cells:
            center = (cell["bbox"][1]+cell["bbox"][3])/2
            if not baselines or abs(center-baselines[-1][0]) > 2:
                baselines.append((center, []))
            baselines[-1][1].append(cell)
        heading = [row for y, row in baselines if y < levels[1]]
        body = [row for y, row in baselines if y > levels[1]]
        if not heading or len(body) < 2:
            continue
        grid = [sorted(row, key=lambda cell: cell["bbox"][0]) for row in body]
        width = len(heading[0])
        headers = [""] * width
        anchors = sorted((c["bbox"][0]+c["bbox"][2])/2 for c in heading[0])
        boundaries = [left, *[(a+b)/2 for a, b in zip(anchors, anchors[1:])], right]
        header_ambiguous = False
        for row in heading:
            used = set()
            for cell in row:
                center = (cell["bbox"][0]+cell["bbox"][2])/2
                column = next((j for j in range(width) if boundaries[j] <= center <= boundaries[j+1]), None)
                if column is None or column in used or any(cell["bbox"][0] < a < cell["bbox"][2]
                    for j, a in enumerate(anchors) if j != column):
                    header_ambiguous = True
                    continue
                used.add(column)
                headers[column] = " ".join(filter(None, [headers[column], "".join(s["text"] for s in cell["spans"]).strip()]))
        raw = [["".join(s["text"] for s in cell["spans"]).strip() for cell in row] for row in grid]
        clipped = any(top-1 <= line["bbox"][1] and line["bbox"][3] <= bottom+1
                      and line["bbox"][0] < right and line["bbox"][2] > left
                      and (line["bbox"][0] < left-1 or line["bbox"][2] > right+1) for line in lines)
        ambiguous = header_ambiguous or clipped or width < 2 or any(len(row) != width for row in grid) or any(not re.search(r"[A-Za-z]", h) for h in headers)
        if not ambiguous:
            # A shared gap must separate each pair of columns across ALL rows.
            ambiguous = any(max(row[j]["bbox"][2] for row in grid) >= min(row[j+1]["bbox"][0] for row in grid)
                            for j in range(width-1))
        item = structure(headers, raw, caption, number, ambiguous=ambiguous, raw_rows=[headers, *raw])
        item.update(source_format="pdf", bbox=bbox, geometry_method="horizontal_rules_and_aligned_text_cells")
        result.append(item)
    return result
