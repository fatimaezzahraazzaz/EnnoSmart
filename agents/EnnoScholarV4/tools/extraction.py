"""Extract every section within explicit limits; the agent chooses what to read."""
import re
from bs4 import BeautifulSoup
from agents.EnnoScholarV2.models import Article
from agents.EnnoScholarV2.models import TextChunk
from agents.EnnoScholarV2.tools.article_reader import html_document as metadata_document
from ..config import ScholarV4Config

# Documentary heading recognition belongs to V4; older search/readers stay intact.
SECTION = re.compile(r"^(?:(?:\d+(?:\.\d+)*|[IVXLCDM]+)(?:[-.][A-Z])?[.\s]+)?(?:abstract|introduction|related work|background|materials and methods|methods?|methodology|experiments?|results?|discussion|limitations?|conclusions?|future work)\b", re.I)
BODY = "article, .article.fulltext-view, .highwire-markup .fulltext-view, .fulltext, #fulltext, .ltx_document, [role='doc-body']"


def scientific_heading(line):
    """Recognize literal scientific headings, including supplementary captions."""
    heading = re.sub(r"^(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.\s]+", "", line.strip()).strip()
    ordinary = r"Abstract|Introduction|Methods|Methodology|Materials and methods|Experiments?(?: and results)?|Experimental results|Results(?: and discussion)?|Discussion|Conclusions?|Limitations?(?: and future work)?|References|Acknowledg(?:e)?ments?|Reporting summary"
    supplementary = (r"(?:Supplementary|Supporting)\s+(?:Text|Information|Materials?|Methods|Results|Data)(?:\s*[:.—–-].*)?|"
        r"(?:Appendix|Appendices|Annexe?s?)(?:\s+[A-Z0-9]+)?(?:\s*[:.—–-].*)?|"
        r"(?:(?:Extended\s+Data|Supplementary|Supporting)\s+(?:Table|Fig(?:ure)?\.?)\s*[A-Z]?\d+[A-Za-z]?\b.*|"
        r"(?:Table|Fig(?:ure)?\.?)\s*S\s*\d+[A-Za-z]?\b.*)")
    return line.strip() if re.fullmatch(f"(?:{ordinary}|{supplementary})", heading, re.I) else None


def bounded(chunks, config):
    retained, used, truncated = [], 0, False
    for chunk in chunks:
        remaining = config.max_extracted_chars-used
        if remaining <= 0:
            truncated = True
            break
        retained.append(chunk.model_copy(update={"text": chunk.text[:remaining]}))
        used += len(retained[-1].text)
        truncated |= len(chunk.text) > remaining
    return retained, truncated


class ScientificChunk(TextChunk):
    page_char_offset: int = 0
    printed_page: str | None = None
    pdf_page_label: str | None = None
    page_verification: str = "not_verified"
    table: dict | None = None
    equation: str | None = None


def scientific_text(node):
    """Keep one literal math representation, preferably the source's TeX annotation."""
    node = BeautifulSoup(str(node), "html.parser")
    for math in node.find_all("math"):
        annotation = math.find("annotation", attrs={"encoding": re.compile(r"(?:x-tex|latex)", re.I)})
        text = annotation.get_text() if annotation else math.get("alttext") or math.get_text(" ", strip=True)
        math.replace_with(text)
    return node.get_text(" ", strip=True)


def page_coordinates(page):
    """Read page labels and explicit margin text; never infer publication offsets."""
    labels = page.parent.get_page_labels()
    label = page.get_label() if labels else None
    numbers = set()
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            y0, y1 = line["bbox"][1], line["bbox"][3]
            if y1 > 60 and y0 < page.rect.height-60:
                continue
            text = " ".join(span["text"] for span in line.get("spans", [])).strip()
            if re.fullmatch(r"\d{1,6}|[ivxlcdm]{1,8}", text, re.I):
                numbers.add(text)
    printed = next(iter(numbers)) if len(numbers) == 1 else None
    return printed, label, "margin_text" if printed else "not_verified"


def pdf_chunks(data, config=None, page_start=1, page_limit=None, *, progress=None,
        page_offsets=None, deadline=None, check=lambda: None):
    import fitz
    import time
    from pathlib import Path
    config = config or ScholarV4Config.from_env()
    progress = progress if progress is not None else {}
    page_offsets = page_offsets or {}
    chunks, used, section = [], 0, ""
    opened = fitz.open(str(data)) if isinstance(data, (str, Path)) else fitz.open(stream=data, filetype="pdf")
    with opened as document:
        if document.needs_pass:
            raise ValueError("PDF protégé par un mot de passe.")
        if not 1 <= page_start <= len(document):
            raise ValueError("Page PDF hors du document.")
        end = min(len(document), page_start-1+(page_limit or config.max_pdf_pages))
        progress.update(page_count=len(document), page_extraction={})
        for index in range(page_start-1, end):
            if deadline is not None and time.monotonic() >= deadline:
                break
            check()
            page = document[index]
            text = page.get_text()
            from .tables import pdf_tables, table_text
            tables = pdf_tables(page)
            positions = {}
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    value = " ".join("".join(span["text"] for span in line["spans"]).split())
                    positions.setdefault(value, []).append(line["bbox"])
            printed, label, verification = page_coordinates(page)
            buffer, raw_chunks = [], []
            def flush():
                nonlocal buffer
                raw = "\n".join(buffer).strip()
                if raw:
                    raw_chunks.append(ScientificChunk(page=index+1, section=section, text=raw,
                        printed_page=printed, pdf_page_label=label, page_verification=verification))
                buffer = []
            for line in text.splitlines():
                is_heading = bool(scientific_heading(line))
                if is_heading:
                    boxes = positions.get(" ".join(line.split()), [])
                    box = boxes.pop(0) if boxes else None
                    if box and any(t["bbox"][0]-1 <= box[0] and box[2] <= t["bbox"][2]+1
                                   and t["bbox"][1]-1 <= box[1] and box[3] <= t["bbox"][3]+1 for t in tables):
                        is_heading = False
                if is_heading:
                    flush()
                    section = line.strip()
                buffer.append(line)
            flush()
            for table in tables:
                raw_chunks.append(ScientificChunk(page=index+1, section="", text=table_text(table), table=table,
                    printed_page=printed, pdf_page_label=label, page_verification=verification))
            offset = int(page_offsets.get(str(index+1), 0))
            cursor, consumed, reason = 0, offset, ""
            for chunk in raw_chunks:
                start, cursor = cursor, cursor+len(chunk.text)
                if cursor <= offset:
                    continue
                skip = max(0, offset-start)
                remaining = config.max_extracted_chars-used
                if remaining <= 0 or chunk.table and (skip or len(chunk.text) > remaining):
                    reason = "table_exceeds_batch" if chunk.table and len(chunk.text) > config.max_extracted_chars else "character_budget"
                    break
                value = chunk.text[skip:skip+remaining]
                if skip+len(value) < len(chunk.text) and value and not value[-1].isspace() and not chunk.text[skip+len(value)].isspace():
                    # Never turn the prefix of a number/unit into a standalone
                    # scientific result at an extraction boundary.
                    boundary = max((m.end() for m in re.finditer(r'\s+', value)), default=0)
                    value = value[:boundary]
                    if not value:
                        reason = 'token_exceeds_batch'
                        break
                chunks.append(chunk.model_copy(update={"text": value, "page_char_offset": start+skip}))
                used += len(value)
                consumed = start+skip+len(value)
                if skip+len(value) < len(chunk.text):
                    reason = "character_budget"
                    break
            status = "partial" if reason else "complete" if raw_chunks else "unavailable"
            progress["page_extraction"][str(index+1)] = {"status": status, "next_offset": consumed,
                **({"reason": reason or "no_extractable_text"} if status != "complete" else {})}
            if reason or used >= config.max_extracted_chars:
                break
        complete = {int(p) for p, row in progress["page_extraction"].items() if row["status"] == "complete"}
        progress["extraction_complete"] = len(complete) == len(document)
        progress["next_page"] = next((p for p in range(page_start, len(document)+1) if p not in complete), None)
        return chunks, not progress["extraction_complete"]


def html_document(data, url, config=None, *, allow_partial=False):
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
    chunks, buffer, section, headings = [], [], "", {}
    for node in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "math", "figcaption"]):
        if node.find_parent("table") or node.find_parent("figcaption") or (node.name == "math" and node.find_parent(["p", "li"])):
            continue
        if (node.name == "table" and any(str(c).startswith("ltx_equation") for c in node.get("class", []))) or node.name == "math":
            if buffer:
                chunks.append(TextChunk(section=section, text="\n".join(buffer)))
                buffer = []
            text = scientific_text(node)
            if text:
                chunks.append(ScientificChunk(section=section, text=text, equation=text))
            continue
        if node.name == "table":
            from .tables import html_table, table_text
            if buffer:
                chunks.append(TextChunk(section=section, text="\n".join(buffer)))
                buffer = []
            table = html_table(node)
            chunks.append(ScientificChunk(section=section, text=table_text(table), table=table))
            continue
        text = scientific_text(node)
        if node.name.startswith("h"):
            if buffer:
                chunks.append(TextChunk(section=section, text="\n".join(buffer)))
                buffer = []
            level = int(node.name[1])
            headings = {n: value for n, value in headings.items() if n < level}
            if level > 1:
                headings[level] = text
            section = " / ".join(headings.values()) or text
        elif text:
            buffer.append(text)
    if buffer:
        chunks.append(TextChunk(section=section, text="\n".join(buffer)))
    chunks = [c for c in chunks if not re.match(r'abstract\b|résumé\b', c.section, re.I)]
    if sum(len(c.text) for c in chunks) < 1000:
        return article, [], False
    partial = len({c.section for c in chunks if SECTION.match(c.section)}) < 2
    core = any(re.search(r'\b(?:methods?|methodology|materials and methods|results?|experiments?|discussion|limitations?|conclusions?)\b', c.section, re.I) for c in chunks)
    if partial and not (allow_partial and core):
        return article, [], False
    retained, truncated = bounded(chunks, config)
    return article, retained, truncated or partial


def xml_document(data, url, config=None):
    """Read an actual JATS article body, never resolve external XML entities."""
    from lxml import etree
    config = config or ScholarV4Config.from_env()
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    for node in root.iter():
        if isinstance(node.tag, str):
            node.tag = etree.QName(node).localname
    def text(node):
        return " ".join("".join(node.itertext()).split()) if node is not None else ""
    title = text(root.find(".//article-meta/title-group/article-title"))
    doi = text(root.find(".//article-meta/article-id[@pub-id-type='doi']"))
    article = Article(title=title or url, doi=doi, url=url, source="article_url", sources=["article_url"],
        abstract=text(root.find(".//article-meta/abstract")),
        authors=[text(n) for n in root.findall(".//article-meta/contrib-group/contrib/name")])
    body = root.find(".//body")
    if root.tag != "article" or not title or body is None:
        return article, [], False
    chunks, buffer, section = [], [], "Main"
    for node in body.iter():
        if any(n.tag == "table-wrap" for n in node.iterancestors()):
            continue
        if node.tag == "table-wrap":
            from .tables import html_table, table_text
            if buffer:
                chunks.append(TextChunk(section=section, text="\n".join(buffer)))
                buffer = []
            markup = BeautifulSoup(etree.tostring(node, encoding="unicode"), "xml")
            table_node = markup.find("table")
            if table_node is not None:
                table = html_table(table_node)
                chunks.append(ScientificChunk(section=section, text=table_text(table), table=table))
            continue
        if node.tag == "title" and node.getparent().tag == "sec":
            if buffer:
                chunks.append(TextChunk(section=section, text="\n".join(buffer)))
                buffer = []
            ancestors = list(node.getparent().iterancestors("sec"))[::-1]
            section = " / ".join(filter(None, [*[text(n.find("title")) for n in ancestors], text(node)])) or "Main"
        elif node.tag == "p" and text(node):
            buffer.append(text(node))
    if buffer:
        chunks.append(TextChunk(section=section, text="\n".join(buffer)))
    if sum(len(c.text) for c in chunks) < 1000:
        return article, [], False
    retained, truncated = bounded(chunks, config)
    return article, retained, truncated
