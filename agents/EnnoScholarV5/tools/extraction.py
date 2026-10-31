"""V5 extraction corrections; reuse V4's table parser and PDF pagination."""
import re
from ..reuse import load
base = load("tools._extraction_base", "tools/extraction.py")
_original_heading = base.scientific_heading

def scientific_heading(line):
    value = re.sub(r"^(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.\s]+", "", line.strip()).strip()
    if re.fullmatch(r"STAR\s*\+\s*METHODS|METHODS\s+DETAILS", value, re.I):
        return line.strip()
    # Captions may start a caption, but a sentence mentioning Table S1 is not a section.
    if re.match(r"(?:(?:Extended Data|Supplementary|Supporting)\s+)?(?:Table|Fig(?:ure)?\.?)\s*[A-Z]?\d+", value, re.I):
        return None
    return _original_heading(line)

base.scientific_heading = scientific_heading
base.SECTION = re.compile(base.SECTION.pattern + r"|^(?:STAR\s*\+\s*METHODS|METHODS\s+DETAILS)\s*$", re.I)
for name in dir(base):
    if not name.startswith("_"):
        globals()[name] = getattr(base, name)

# A continuation batch inherits its last observed scientific heading from the
# already archived chunks. No old quote, chunk, page or proof is rewritten.
from contextvars import ContextVar
previous_chunks = ContextVar("v5_previous_chunks", default=())
_pdf_chunks = pdf_chunks

def pdf_chunks(data, config=None, page_start=1, page_limit=None, **kwargs):
    inherited = ""
    for chunk in previous_chunks.get():
        if chunk.get("page") is not None and chunk["page"] >= page_start:
            continue
        for line in chunk.get("text", "").splitlines():
            if scientific_heading(line):
                inherited = line.strip()
    result = _pdf_chunks(data, config, page_start, page_limit, **kwargs)
    # V4 returns (chunks, truncated); the page coordinate objects are kept intact.
    chunks, truncated = result
    for chunk in chunks:
        if chunk.section:
            inherited = chunk.section
        elif inherited and not chunk.table:
            chunk.section = inherited
    return chunks, truncated


def _safe_prefix(text, limit):
    """Keep whole words; defer numbers in the unfinished scientific paragraph.

    Stopping after a complete number may still remove its unit/exponent. Rather
    than guess which words form a unit, retain the preceding prose only. Complete
    paragraphs before the boundary remain untouched. In HTML/JATS these
    paragraph boundaries are produced by the extractor, not visual line wraps.
    """
    cut = limit
    if cut and not text[cut-1].isspace() and not text[cut].isspace():
        cut = max((m.end() for m in re.finditer(r"\s+", text[:cut])), default=0)
    raw_prefix = text[:cut]
    boundary = raw_prefix.rfind('\n')+1
    prefix = raw_prefix.rstrip()
    numeric = re.search(r"\S*[\d⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉]", prefix[boundary:])
    if numeric:
        prefix = prefix[:boundary+numeric.start()].rstrip()
    return prefix


def bounded(chunks, config):
    """One unchanged character allowance; structured records stay indivisible."""
    retained, used = [], 0
    for chunk in chunks:
        remaining = config.max_extracted_chars-used
        if len(chunk.text) <= remaining:
            retained.append(chunk)
            used += len(chunk.text)
            continue
        if remaining > 0 and not (getattr(chunk, 'table', None) or
                getattr(chunk, 'equation', None) or getattr(chunk, 'figure', None)):
            prefix = _safe_prefix(chunk.text, remaining)
            if prefix:
                retained.append(chunk.model_copy(update={'text': prefix}))
        # The caller's existing truncated/content_scope receipt reports the
        # remaining content. Never manufacture a fragment of a table or figure.
        return retained, True
    return retained, False


class FigureChunk(ScientificChunk):
    # New JATS figures only: no extra fields are added to existing PDF chunks.
    figure: dict


def xml_document(data, url, config=None):
    """V4's JATS text walk, including the article's floating tables/figures."""
    import json
    from itertools import chain
    from lxml import etree
    from .tables import html_table, table_text

    config = config or ScholarV4Config.from_env()
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    for node in root.iter():
        if isinstance(node.tag, str):
            node.tag = etree.QName(node).localname

    def text(node):
        return ' '.join(''.join(node.itertext()).split()) if node is not None else ''

    title = text(root.find('.//article-meta/title-group/article-title'))
    doi = text(root.find(".//article-meta/article-id[@pub-id-type='doi']"))
    article = Article(title=title or url, doi=doi, url=url, source='article_url', sources=['article_url'],
        abstract=text(root.find('.//article-meta/abstract')),
        authors=[text(n) for n in root.findall('.//article-meta/contrib-group/contrib/name')])
    body = root.find('.//body')
    if root.tag != 'article' or not title or body is None:
        return article, [], False

    chunks, buffer, section, seen = [], [], 'Main', set()

    def flush():
        if buffer:
            chunks.append(TextChunk(section=section, text='\n'.join(buffer)))
            buffer.clear()

    outside = (n for n in root.iter() if n.tag in {'table-wrap', 'fig'} and body not in n.iterancestors())
    for node in chain(body.iter(), outside):
        if any(n.tag in {'table-wrap', 'fig', 'sub-article', 'ref-list'} for n in node.iterancestors()):
            continue
        if node.tag in {'table-wrap', 'fig'}:
            flush()
            if body not in node.iterancestors():
                sections = list(node.iterancestors('sec'))[::-1]
                section = ' / '.join(filter(None, (text(n.find('title')) for n in sections))) or 'Floats'
            label = text(node.find('label'))
            if node.tag == 'table-wrap':
                markup = BeautifulSoup(etree.tostring(node, encoding='unicode'), 'xml')
                table_node = markup.find('table')
                if table_node is None:
                    continue
                table = html_table(table_node)
                table.update(label=label, element_id=node.get('id', ''), source_url=url)
                number = re.fullmatch(r'(?:(?:Supplementary|Supporting|Extended Data)\s+)?'
                    r'(?:Table(?:au)?\s*)?([A-Z]?\s*\d+[A-Za-z]?|[IVXLCDM]+)\s*[.:]?', label, re.I)
                if number:
                    table['number'] = re.sub(r'\s+', '', number[1])
                value = table_text(table)
                if label and not value.startswith(label):
                    value = label+'\n'+value
                record = table
                chunk = ScientificChunk(section=section, text=value, table=table)
            else:
                refs = list(dict.fromkeys(n.get('href') or n.get('{http://www.w3.org/1999/xlink}href')
                    for n in node.iter() if n.tag in {'graphic', 'inline-graphic'}))
                refs = [ref for ref in refs if ref]
                record = {'id': node.get('id'), 'label': label, 'caption': text(node.find('caption')),
                    'graphic_refs': refs, 'source_url': url, 'publication_doi': doi}
                # Literal source fields make the references available to existing
                # passage/read tools too. No image is fetched or interpreted.
                value = '\n'.join(dict.fromkeys(v for v in
                    [label, record['caption'], record['id'], *refs] if v))
                if not value:
                    continue
                chunk = FigureChunk(section=label or section, text=value, figure=record)
            # Same observed identity and content: skip a repeated floating copy.
            # Distinct IDs/content are never merged just because values look alike.
            key = (node.tag, node.get('id') or label or section, json.dumps(record, sort_keys=True))
            if key not in seen:
                chunks.append(chunk)
                seen.add(key)
        elif node.tag == 'title' and node.getparent().tag == 'sec':
            flush()
            sections = list(node.getparent().iterancestors('sec'))[::-1]
            section = ' / '.join(filter(None, [*[text(n.find('title')) for n in sections], text(node)])) or 'Main'
        elif node.tag == 'p' and text(node):
            buffer.append(text(node))
    flush()
    if sum(len(c.text) for c in chunks) < 1000:
        return article, [], False
    retained, truncated = bounded(chunks, config)
    return article, retained, truncated


HTML_NODES = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "math", "figcaption"}


def html_document(data, url, config=None, *, allow_partial=False):
    """V4's HTML walk. A generic table/figure wrapper (see html_floats) becomes one
    structured record: its label heading no longer opens a section and its caption is
    no longer a detached paragraph. Pages without such wrappers are read exactly as in V4."""
    from .html_floats import FIGURE_LABEL, TABLE_LABEL, containers, describe, graphic_refs, leftovers
    from .tables import html_table, table_text

    config = config or ScholarV4Config.from_env()
    article, _, _ = metadata_document(data, url)
    soup = BeautifulSoup(data, "html.parser")
    for node in soup.select("script, style, nav, footer, header, form"):
        node.decompose()
    body = soup.select_one(BODY)
    if body is None:
        return article, [], False
    if re.search(r'abstract|resume|résumé', str(body.get('id', ''))+' '+ ' '.join(body.get('class', [])), re.I):
        return article, [], False
    for node in body.select('.abstract, .ltx_abstract, #abstract, [role="doc-abstract"], section.abstract'):
        node.decompose()
    floats = {id(f["node"]): f for f in containers(body)}
    chunks, buffer, section, headings = [], [], "", {}

    def flush():
        if buffer:
            chunks.append(TextChunk(section=section, text="\n".join(buffer)))
            buffer.clear()

    def owner(node):
        for p in (node, *node.parents):
            if id(p) in floats:
                return floats[id(p)]
        return None

    def place(label):
        return f"{section} / {label}" if section and label else (label or section)

    def emit(found):
        node = found["node"]
        if found["kind"] == "table":
            table = html_table(found["table"])
            chunks.append(ScientificChunk(section=place(table.get("label", "")), text=table_text(table), table=table))
            info = describe(node, TABLE_LABEL, found["table"])
            extra = leftovers(node, info, found["table"])
        else:
            info = describe(node, FIGURE_LABEL)
            refs = graphic_refs(node)
            record = {"id": info["id"] or None, "label": info["label"], "caption": info["caption"],
                "number": info["number"], "graphic_refs": refs, "source_url": url,
                "publication_doi": getattr(article, "doi", "") or ""}
            label = [info["label"]] if info["label"] and not info["caption"].startswith(info["label"]) else []
            value = "\n".join(dict.fromkeys(v for v in [*label, info["caption"], *info["subcaptions"], info["id"], *refs] if v))
            chunks.append(FigureChunk(section=place(info["label"]), text=value, figure=record))
            extra = leftovers(node, info)
        if extra:
            chunks.append(TextChunk(section=place(info["label"]), text="\n".join(extra)))

    for node in body.find_all(lambda n: n.name in HTML_NODES or id(n) in floats):
        found = owner(node)
        if found is not None:
            if node is found["node"]:
                flush()
                emit(found)
            continue
        if node.find_parent("table") or node.find_parent("figcaption") or (node.name == "math" and node.find_parent(["p", "li"])):
            continue
        if (node.name == "table" and any(str(c).startswith("ltx_equation") for c in node.get("class", []))) or node.name == "math":
            flush()
            value = scientific_text(node)
            if value:
                chunks.append(ScientificChunk(section=section, text=value, equation=value))
            continue
        if node.name == "table":
            flush()
            table = html_table(node)
            chunks.append(ScientificChunk(section=section, text=table_text(table), table=table))
            continue
        value = scientific_text(node)
        if node.name.startswith("h"):
            flush()
            level = int(node.name[1])
            headings = {n: v for n, v in headings.items() if n < level}
            if level > 1:
                headings[level] = value
            section = " / ".join(headings.values()) or value
        elif value:
            buffer.append(value)
    flush()
    chunks = [c for c in chunks if not re.match(r'abstract\b|résumé\b', c.section, re.I)]
    if sum(len(c.text) for c in chunks) < 1000:
        return article, [], False
    partial = len({c.section for c in chunks if SECTION.match(c.section)}) < 2
    core = any(re.search(r'\b(?:methods?|methodology|materials and methods|results?|experiments?|discussion|limitations?|conclusions?)\b', c.section, re.I) for c in chunks)
    if partial and not (allow_partial and core):
        return article, [], False
    retained, truncated = bounded(chunks, config)
    return article, retained, truncated or partial


# These globals belong to the private module loaded under EnnoScholarV5.
# Existing HTML extraction uses this bound; PDF extraction does not call it.
base.bounded = bounded
base.xml_document = xml_document
base.html_document = html_document
