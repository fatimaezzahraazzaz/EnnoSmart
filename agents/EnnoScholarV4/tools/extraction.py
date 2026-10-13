"""Extract every section within explicit limits; the agent chooses what to read."""
from bs4 import BeautifulSoup
from agents.EnnoScholarV2.models import TextChunk
from agents.EnnoScholarV2.tools.article_reader import SECTION, html_document as metadata_document
from ..config import ScholarV4Config


def pdf_chunks(data, config=None):
    import fitz
    config = config or ScholarV4Config.from_env()
    chunks, used = [], 0
    with fitz.open(stream=data, filetype="pdf") as document:
        if document.needs_pass:
            raise ValueError("PDF protégé par un mot de passe.")
        for index in range(min(len(document), config.max_pdf_pages)):
            remaining = config.max_extracted_chars-used
            if remaining <= 0:
                break
            text = document[index].get_text()
            retained = text[:remaining]
            section = next((line.strip() for line in retained.splitlines() if SECTION.match(line.strip())), "")
            chunks.append(TextChunk(page=index+1, section=section, text=retained.strip()))
            used += len(retained)
            if len(retained) < len(text):
                return chunks, True
        return chunks, len(chunks) < len(document)


def html_document(data, url, config=None):
    config = config or ScholarV4Config.from_env()
    article, _, _ = metadata_document(data, url)
    soup = BeautifulSoup(data, "html.parser")
    for node in soup.select("script, style, nav, footer, header, form"):
        node.decompose()
    body = soup.select_one("article, .fulltext, #fulltext, .ltx_document, [role='doc-body']")
    if body is None:
        return article, [], False
    chunks, buffer, section = [], [], ""
    for node in body.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        text = node.get_text(" ", strip=True)
        if node.name.startswith("h"):
            if buffer:
                chunks.append(TextChunk(section=section, text="\n".join(buffer)))
                buffer = []
            section = text
        elif text:
            buffer.append(text)
    if buffer:
        chunks.append(TextChunk(section=section, text="\n".join(buffer)))
    if sum(bool(SECTION.match(c.section)) for c in chunks) < 2 or sum(len(c.text) for c in chunks) < 1000:
        return article, [], False
    retained, used, truncated = [], 0, False
    for chunk in chunks:
        remaining = config.max_extracted_chars-used
        if remaining <= 0:
            truncated = True
            break
        retained.append(chunk.model_copy(update={"text": chunk.text[:remaining]}))
        used += len(retained[-1].text)
        truncated |= len(chunk.text) > remaining
    return article, retained, truncated
