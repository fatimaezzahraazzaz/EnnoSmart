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
    printed_page: str | None = None
    pdf_page_label: str | None = None
    page_verification: str = "not_verified"
    table: dict | None = None


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


def pdf_chunks(data, config=None, page_start=1, page_limit=None):
    import fitz
    config = config or ScholarV4Config.from_env()
    chunks, used, section = [], 0, ""
    with fitz.open(stream=data, filetype="pdf") as document:
        if document.needs_pass:
            raise ValueError("PDF protégé par un mot de passe.")
        if not 1 <= page_start <= len(document):
            raise ValueError("Page PDF hors du document.")
        end = min(len(document), page_start-1+(page_limit or config.max_pdf_pages))
        for index in range(end):
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
            buffer = []
            def flush():
                nonlocal used, buffer
                if not buffer:
                    return
                raw = "\n".join(buffer).strip()
                remaining = config.max_extracted_chars-used
                if remaining > 0:
                    retained = raw[:remaining]
                    chunks.append(ScientificChunk(page=index+1, section=section, text=retained,
                        printed_page=printed, pdf_page_label=label, page_verification=verification))
                    used += len(retained)
                buffer = []
            for line in text.splitlines():
                heading = re.sub(r"^(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.\s]+", "", line.strip()).strip()
                is_heading = bool(re.fullmatch(r"Abstract|Introduction|Methods|Methodology|Materials and methods|Experiments?(?: and results)?|Experimental results|Results(?: and discussion)?|Discussion|Conclusions?|Limitations?(?: and future work)?|References|Acknowledg(?:e)?ments?|Reporting summary|Extended Data Fig\. \d+\s*\|.*", heading, re.I))
                if is_heading:
                    boxes = positions.get(" ".join(line.split()), [])
                    box = boxes.pop(0) if boxes else None
                    if box and any(t["bbox"][0]-1 <= box[0] and box[2] <= t["bbox"][2]+1
                                   and t["bbox"][1]-1 <= box[1] and box[3] <= t["bbox"][3]+1 for t in tables):
                        is_heading = False  # A table column named Methods is not a section.
                if is_heading:
                    if index >= page_start-1:
                        flush()
                    section = line.strip()
                if index >= page_start-1:
                    buffer.append(line)
            if index >= page_start-1:
                flush()
                for table in tables:
                    raw = table_text(table)
                    remaining = config.max_extracted_chars-used
                    if remaining < len(raw):
                        return chunks, True
                    # Table position is known; do not infer a section from a different column.
                    chunks.append(ScientificChunk(page=index+1, section="", text=raw, table=table,
                        printed_page=printed, pdf_page_label=label, page_verification=verification))
                    used += len(raw)
                if used >= config.max_extracted_chars:
                    return chunks, True
        return chunks, page_start != 1 or end < len(document)


def html_document(data, url, config=None):
    config = config or ScholarV4Config.from_env()
    article, _, _ = metadata_document(data, url)
    soup = BeautifulSoup(data, "html.parser")
    for node in soup.select("script, style, nav, footer, header, form"):
        node.decompose()
    body = soup.select_one(BODY)
    if body is None:
        return article, [], False
    chunks, buffer, section, headings = [], [], "", {}
    for node in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table"]):
        if node.find_parent("table"):
            continue
        if node.name == "table":
            from .tables import html_table, table_text
            if buffer:
                chunks.append(TextChunk(section=section, text="\n".join(buffer)))
                buffer = []
            table = html_table(node)
            chunks.append(ScientificChunk(section=section, text=table_text(table), table=table))
            continue
        text = node.get_text(" ", strip=True)
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
    if sum(bool(SECTION.match(c.section)) for c in chunks) < 2 or sum(len(c.text) for c in chunks) < 1000:
        return article, [], False
    retained, truncated = bounded(chunks, config)
    return article, retained, truncated


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
