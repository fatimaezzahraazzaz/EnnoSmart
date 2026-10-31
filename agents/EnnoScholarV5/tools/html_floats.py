"""Generic HTML floats: tables and figures with their label, caption and literal media references.

Only reusable structures are recognised: <caption>/<figcaption>, elements whose class names
contain "caption" or "legend", and headings or label/title elements whose own text starts with
"Table N" / "Figure N". The search climbs at most a few levels and stops at any section boundary
(another heading, another table, media, or several ordinary paragraphs). Nothing is invented:
an absent caption, label or reference stays absent.
"""
import re

TABLE_LABEL = re.compile(r"\s*((?:(?:Supplementary|Supporting|Extended\s+Data)\s+)?Tab(?:le|\.)\s*"
    r"((?-i:[A-Z]?\s?\d+[A-Za-z]?|[IVXLCDM]+))\b)\s*[.:|—–-]?\s*", re.I)
FIGURE_LABEL = re.compile(r"\s*((?:(?:Supplementary|Supporting|Extended\s+Data)\s+)?Fig(?:ure|\.)?\s*"
    r"((?-i:[A-Z]?\s?\d+[A-Za-z]?))\b)\s*[.:|—–-]?\s*", re.I)
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
MEDIA = ["img", "picture", "svg", "object", "embed", "video"]
STOP = {"body", "html", "article", "main", "[document]"}
IMAGE_LINK = re.compile(r"\.(?:png|jpe?g|gif|tiff?|webp|svg|bmp|eps)(?:[?#].*)?$", re.I)
MAX_DEPTH = 4


def _text(node):
    from .extraction import scientific_text
    return scientific_text(node)


def _classes(node):
    value = node.get("class") or []
    return " ".join(value if isinstance(value, list) else [value]).lower()


def _below(node, ancestor):
    """Identity test (bs4 '==' compares markup, not elements)."""
    return any(p is ancestor for p in node.parents)


def _within(node, owners):
    return any(node is o or _below(node, o) for o in owners)


def is_equation_table(table):
    return any(str(c).startswith("ltx_equation") for c in table.get("class", []))


def _is_caption(node):
    return node.name in {"caption", "figcaption"} or bool(re.search(r"caption|legend", _classes(node)))


def _label_candidate(node):
    return node.name in HEADINGS or bool(re.search(r"label|title|head", _classes(node)))


def _note(node):
    return bool(re.search(r"foot|note", _classes(node)))


def _chain(node, container):
    """node and its ancestors strictly below container."""
    out = [node]
    for p in node.parents:
        if p is container:
            break
        out.append(p)
    return out


def _own(node, exclude):
    """Descendants of node, excluding the float's own table."""
    for d in node.find_all(True):
        if exclude is not None and (d is exclude or _below(d, exclude)):
            continue
        yield d


def _boundary(node, exclude, pattern):
    """True when node is wider than one float (section, other table, other media...)."""
    paragraphs = 0
    for d in _own(node, exclude):
        if d.name == "table" and not is_equation_table(d) and d.find_parent("table") is None:
            return True
        if exclude is not None and d.name in MEDIA:
            return True
        if d.name in HEADINGS and not pattern.match(_text(d)):
            return True
        if d.name in {"p", "li"} and _text(d) and not any(_is_caption(p) or _note(p) for p in _chain(d, node)):
            paragraphs += 1
            if paragraphs > 2:
                return True
    return False


def describe(container, pattern, exclude=None):
    """Literal label, caption, sub-captions and number of one float container."""
    label = caption = ""
    label_node = None
    captions = []
    for d in _own(container, exclude):
        inside_caption = any(_is_caption(p) for p in _chain(d, container)[1:])
        if _is_caption(d) and not inside_caption:
            captions.append(d)
        elif label_node is None and not inside_caption and _label_candidate(d):
            value = _text(d)
            match = pattern.match(value)
            if match and len(value) <= 600:
                label_node, label = d, match.group(1).strip()
                rest = value[match.end():].strip()
                if rest:
                    caption = rest
    # A caption naming another kind of float ("Figure 8..." beside a table) is not this one's.
    foreign = FIGURE_LABEL if pattern is TABLE_LABEL else TABLE_LABEL
    kept = [(c, _text(c)) for c in captions]
    kept = [(c, t) for c, t in kept if t and not (foreign.match(t) and not pattern.match(t))]
    captions = [c for c, _ in kept]
    texts = [t for _, t in kept]
    if texts and not caption:
        # The labelled caption ("Figure 4. ...") is the float's; panel captions stay as sub-captions.
        main = next((i for i, t in enumerate(texts) if pattern.match(t)), 0)
        caption, texts = texts[main], texts[:main]+texts[main+1:]
    if not label and caption:
        match = pattern.match(caption)
        label = match.group(1).strip() if match else ""
    match = pattern.match(label) if label else None
    number = re.sub(r"\s+", "", match.group(2)) if match else None
    return {"label": label, "caption": caption, "subcaptions": texts, "number": number,
        "id": container.get("id") or "", "label_node": label_node, "caption_nodes": captions}


def _climb(start, pattern, exclude):
    node = start
    for _ in range(MAX_DEPTH):
        node = node.parent
        if node is None or node.name in STOP or _boundary(node, exclude, pattern):
            return None
        found = describe(node, pattern, exclude)
        if found["label"] or found["caption"]:
            return node
    return None


def table_container(table):
    """Nearest wrapper carrying this table's label/caption; JATS markup keeps its own parser."""
    if table.find_parent("table-wrap") is not None or is_equation_table(table):
        return None
    return _climb(table, TABLE_LABEL, table)


def base_caption_found(table):
    """Same lookup as V4 html_table: when it finds a caption, V4's output is kept unchanged."""
    figure = table.find_parent(["figure", "table-wrap"]) or table.find_parent(class_="c-article-table")
    node = (figure.find(["figcaption", "caption"]) or figure.select_one(".c-article-table__caption, .c-article-table__title")) if figure else table.find("caption")
    return bool(node and _text(node))


def graphic_refs(container):
    refs = []
    for node in container.find_all(["img", "source", "object", "embed", "image", "video", "a"]):
        for attr in ("src", "data-src", "data-original", "srcset", "data", "href", "xlink:href", "poster"):
            value = (node.get(attr) or "").strip()
            if not value:
                continue
            if attr == "srcset":
                value = value.split(",")[0].split()[0]
            if node.name == "a" and not IMAGE_LINK.search(value):
                continue
            if not value.startswith("data:"):
                refs.append(value)
    return list(dict.fromkeys(refs))


def containers(body):
    """Float containers in document order: tables whose caption V4 misses, then figures."""
    found = []
    for table in body.find_all("table"):
        if table.find_parent("table") is not None or is_equation_table(table) or base_caption_found(table):
            continue
        node = table_container(table)
        if node is not None and not any(node is f["node"] for f in found):
            found.append({"kind": "table", "node": node, "table": table})
    owners = [f["node"] for f in found]
    candidates = list(body.find_all("figure"))
    # Wrappers holding media, or a caption that starts with a figure label (figures given as links).
    starts = [*body.find_all(MEDIA), *(n for n in body.find_all(_is_caption) if FIGURE_LABEL.match(_text(n)))]
    for start in starts:
        if start.find_parent("table") is not None or start.find_parent("figure") is not None or _within(start, owners):
            continue
        node = _climb(start, FIGURE_LABEL, None)
        if node is not None:
            candidates.append(node)
    figures = []
    for node in candidates:
        if _within(node, owners) or any(node is f for f in figures) or any(_below(o, node) for o in owners):
            continue
        if any(not is_equation_table(t) for t in node.find_all("table")):
            continue  # a table wrapped in <figure> keeps the table path
        info = describe(node, FIGURE_LABEL)
        if info["label"] or info["caption"]:
            figures.append(node)
    figures = [n for n in figures if not any(_below(n, o) for o in figures)]  # outermost only
    found.extend({"kind": "figure", "node": n} for n in figures)
    order = {id(n): i for i, n in enumerate(body.find_all(True))}
    return sorted(found, key=lambda f: order.get(id(f["node"]), 0))


def leftovers(container, info, exclude=None):
    """Other literal text blocks of a float (footnotes, notes); never dropped."""
    skip = [n for n in [info.get("label_node"), *info.get("caption_nodes", [])] if n is not None]
    blocks = []
    for d in _own(container, exclude):
        if d.name in {"p", "li"} and d.find_parent(["p", "li"]) is None and not _within(d, skip):
            value = _text(d)
            if value:
                blocks.append(value)
    return blocks
