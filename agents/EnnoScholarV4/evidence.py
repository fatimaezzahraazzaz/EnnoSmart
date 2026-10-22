"""Exact source passages: immutable version identity, pages and quotations."""
import hashlib
import re
import copy
import json


def snapshot(article):
    fulltext = article.fulltext
    chunks = copy.deepcopy(fulltext.get("chunks", [])) if fulltext.get("text") else (
        [{"text": article.abstract, "page": None, "section": "Abstract"}] if article.abstract else [])
    url = fulltext.get("url") or article.url
    text = "\n\n".join(c["text"] for c in chunks)
    version = fulltext.get("document_version") or {"label": "legacy_unverified", "version_status": "legacy_unverified"}
    source = {"article_id": article.id, "url": url, "chunks": chunks, "text": text,
        "basis": "fulltext" if fulltext.get("text") else "abstract", "schema_version": 2,
        "document_version": copy.deepcopy(version), "source_file": fulltext.get("pdf_file", ""),
        "source_type": fulltext.get("source_kind") or ("pdf" if fulltext.get("pdf_file") else "html" if fulltext.get("text") else "abstract")}
    source["fingerprint"] = source_fingerprint(source)
    return source


def source_fingerprint(source):
    if source.get("schema_version") == 2:
        payload = {k: v for k, v in source.items() if k != "fingerprint"}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return hashlib.sha256((source["url"]+"\n"+source["text"]).encode()).hexdigest()


# Snapshots are content-addressed and never edited in place; hashing a whole document
# for each of its passages made a section context take up to 15 s (session 23236f69).
_FINGERPRINTS = {}


def _fingerprint(source):
    key = (id(source), len(source.get("text", "")), len(source.get("chunks", [])), source.get("url"))
    cached = _FINGERPRINTS.get(key)
    if cached is None or cached[0] is not source:
        if len(_FINGERPRINTS) > 4096:
            _FINGERPRINTS.clear()
        cached = _FINGERPRINTS[key] = (source, source_fingerprint(source))
    return cached[1]


def exact_source_valid(evidence, snapshots):
    source = snapshots.get(evidence.get("source_fingerprint"))
    if not source or source["article_id"] != evidence["source_article_id"] or source["url"] != evidence["source_url"]:
        return False
    if _fingerprint(source) != evidence["source_fingerprint"]:
        return False
    if source.get("schema_version") == 2:
        version = source["document_version"]
        if (evidence.get("source_version") != version["label"] or
                evidence.get("document_version_id") != version.get("version_id") or
                evidence.get("source_identifiers") != version.get("identifiers", {}) or
                evidence.get("version_status") != version["version_status"] or
                evidence.get("source_type") != source.get("source_type") or
                evidence.get("document_sha256") != version.get("document_sha256") or
                evidence.get("source_file", "") != source["source_file"]):
            return False
    try:
        chunk = source["chunks"][evidence["chunk_index"]]
        start = evidence["char_offset"]
        if evidence.get("table") or evidence.get("table_row_index") is not None:
            table = chunk.get("table")
            row_index = evidence.get("table_row_index")
            if not table or row_index is None or not 0 <= row_index < len(table["rows"]):
                return False
            expected = {**{k: v for k, v in table.items() if k not in {"rows", "raw_rows"}}, "row": table["rows"][row_index]}
            if evidence.get("table") != expected:
                return False
        return (chunk["text"][start:start+len(evidence["exact_quote"])] == evidence["exact_quote"] and
            chunk.get("page") == evidence["page_number"] and chunk.get("section", "") == evidence["section"] and
            (source.get("schema_version") != 2 or (
                chunk.get("printed_page") == evidence.get("printed_page") and
                chunk.get("pdf_page_label") == evidence.get("pdf_page_label") and
                chunk.get("page_verification", "not_verified") == evidence.get("page_verification", "not_verified"))))
    except (IndexError, KeyError, TypeError):
        return False


# Documents stored from now on are cut on scientific boundaries (paragraphs, sentences, whole
# tables); documents already read keep their windows so their proofs, cursors and receipts
# stay valid (a new cut would give new identifiers and repeat their readings).
SEGMENTATION = "paragraphs_v2"
SEGMENT_CHARS, PARAGRAPH_CHARS, TABLE_CHARS = 1600, 2000, 3200
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9(\[«\"])")
_NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")


def _table_like(block):
    """Rows of short lines carrying several values or cell separators (citations removed):
    a paragraph of prose with bracketed references is not a table."""
    lines = [re.sub(r"\[[\d\s,–-]+\]|\\\(.*?\\\)", "", line) for line in block.splitlines() if line.strip()]
    if len(lines) < 3 or sorted(len(line) for line in lines)[len(lines)//2] > 90:
        return False
    return sum(len(_NUMBERS.findall(line)) >= 2 or "|" in line or "\t" in line for line in lines) >= len(lines)*.6


def _units(text):
    """(start, end, header) units: paragraphs; a long paragraph cut at sentence ends; a table
    kept whole up to TABLE_CHARS, else cut between rows with its first line as header."""
    units = []
    for match in re.finditer(r"\S(?:.*?\S)??(?=\s*\n\s*\n|\s*$)", text, re.S):
        start, end = match.start(), match.end()
        block = text[start:end]
        if _table_like(block):
            if len(block) <= TABLE_CHARS:
                units.append((start, end, None))
                continue
            header = block.splitlines()[0].strip()[:300]
            piece = start
            for line in re.finditer(r"[^\n]*\n?", block):
                if not line.group(0):
                    continue
                if start+line.end()-piece > SEGMENT_CHARS and start+line.start() > piece:
                    units.append((piece, start+line.start(), header if piece > start else None))
                    piece = start+line.start()
            units.append((piece, end, header if piece > start else None))
            continue
        if len(block) <= PARAGRAPH_CHARS:
            units.append((start, end, None))
            continue
        piece, last = start, start
        cuts = [start+m.start() for m in _SENTENCE_END.finditer(block)] + [end]
        for cut in cuts:
            if cut-piece > SEGMENT_CHARS and last > piece:
                units.append((piece, last, None))
                piece = last
            while cut-piece > PARAGRAPH_CHARS:
                # A sentence longer than a paragraph: cut at a word boundary.
                space = text.rfind(" ", piece, piece+SEGMENT_CHARS)
                space = space if space > piece else piece+SEGMENT_CHARS
                units.append((piece, space, None))
                piece = space
            last = cut
        if end > piece:
            units.append((piece, end, None))
    return units


def segments(text):
    """Packed units of one chunk: (offset, exact quote, table header or None)."""
    rows, current = [], None
    for start, end, header in _units(text):
        if header is not None or (current and (current[2] is not None or end-current[0] > SEGMENT_CHARS)):
            if current:
                rows.append(current)
            current = (start, end, header)
            if header is not None:
                rows.append(current)
                current = None
            continue
        current = (current[0], end, None) if current else (start, end, None)
    if current:
        rows.append(current)
    result = []
    for start, end, header in rows:
        while start < end and text[start].isspace():
            start += 1
        if text[start:end].strip():
            result.append((start, text[start:end], header))
    return result


SECTION_PRIORITY = re.compile(r"method|approach|model|architecture|experiment|evaluation|result|discussion|limitation|"
    r"conclusion|ablation|validation|benchmark|performance|méthode|expérience|résultat|limite", re.I)
SECTION_LAST = re.compile(r"reference|bibliograph|acknowledg|remerciement|funding|author contribution|conflict of interest", re.I)


def section_rank(row):
    """Reading priority of a passage by its section heading (methods, experiments, results,
    discussion and limits first; reference lists and acknowledgements last)."""
    section = row.get("section") or ""
    return 2 if SECTION_LAST.search(section) else 0 if SECTION_PRIORITY.search(section) else 1


def passages(article):
    fulltext = article.fulltext
    chunks = fulltext.get("chunks", []) if fulltext.get("text") else (
        [{"text": article.abstract, "page": None, "section": "Abstract"}] if article.abstract else [])
    url = fulltext.get("url") or article.url
    document_version = fulltext.get("document_version") or {"label": "legacy_unverified", "version_status": "legacy_unverified"}
    version = document_version["label"]
    fingerprint = snapshot(article)["fingerprint"]
    rows = []
    for index, chunk in enumerate(chunks):
        text = chunk["text"]
        table = chunk.get("table")
        selections = []
        headers = {}
        if table and table.get("rows") and table["structure_status"] == "verified_cells":
            cursor = 0
            for row_index, row in enumerate(table["rows"]):
                quote = " | ".join("" if c is None else str(c) for c in row)
                try:
                    offset = text.index(quote, cursor)
                except ValueError:
                    continue
                cursor = offset+len(quote)
                selections.append((offset, quote, row_index))
        elif fulltext.get("text") and fulltext.get("segmentation") == SEGMENTATION:
            headers = {}
            for offset, quote, header in segments(text):
                selections.append((offset, quote, None))
                if header:
                    headers[offset] = header
        else:
            selections = [(offset, text[offset:offset+1200], None) for offset in range(0, len(text), 1000)]
        for offset, quote, row_index in selections:
            if not quote.strip():
                continue
            eid = "ev_" + hashlib.sha256(f"{article.id}|{fingerprint}|{index}|{offset}|{quote}".encode()).hexdigest()[:24]
            row = {"evidence_id": eid, "article_id": article.id,
                "canonical_work_id": article.canonical_work_id or article.id, "source_article_id": article.id,
                "source_url": url, "source_type": fulltext.get("source_kind") or ("pdf" if fulltext.get("pdf_file") else "html" if fulltext.get("text") else "abstract"),
                "source_file": fulltext.get("pdf_file", ""), "source_version": version, "source_fingerprint": fingerprint,
                "document_version_id": document_version.get("version_id"), "document_sha256": document_version.get("document_sha256"),
                "source_identifiers": document_version.get("identifiers", {}), "version_status": document_version["version_status"],
                "page_number": chunk.get("page"), "page": chunk.get("page"), "section": chunk.get("section", ""),
                "printed_page": chunk.get("printed_page"), "pdf_page_label": chunk.get("pdf_page_label"),
                "page_verification": chunk.get("page_verification", "not_verified"),
                "exact_quote": quote, "text": quote, "basis": "fulltext" if fulltext.get("text") else "abstract",
                "chunk_index": index, "char_offset": offset}
            if table and row_index is not None:
                row["table"] = {**{k: v for k, v in table.items() if k not in {"rows", "raw_rows"}}, "row": table["rows"][row_index]}
                row["table_row_index"] = row_index
            elif table:
                row["table_structure_status"] = "ambiguous"
            if fulltext.get("segmentation") == SEGMENTATION and not row_index and headers.get(offset):
                # The first line of a table cut between rows travels with each part (metadata,
                # not quotation); its columns stay unattributed unless the structure is verified.
                row["table_header"] = headers[offset]
                row["table_structure_status"] = row.get("table_structure_status") or "ambiguous"
            rows.append(row)
    return rows


def choose_passages(rows, query, offset=0, limit=8):
    # Stable source order and stable offsets, independently of delivery receipts.
    rows = list({row["evidence_id"]: row for row in rows}.values())
    if query:
        terms = set(re.findall(r"\w+", query.casefold()))
        def score(row):
            return sum(term in (row["section"] + " " + row["text"] + " " + json.dumps(row.get("table", {}))).casefold() for term in terms)
        matched = [row for row in rows if score(row)]
        rows = sorted(matched or rows, key=lambda r: (-score(r), r["chunk_index"], r["char_offset"], r["evidence_id"]))
    return rows[offset:offset + limit]


def validate_claims(analysis, article, evidence, shown):
    validated, used = {}, set()
    for category, claims in analysis.items():
        if not isinstance(claims, list):
            validated[category] = claims
            continue
        validated[category] = []
        for claim in claims:
            ids = claim["evidence_ids"]
            if not ids or any(eid not in evidence or eid not in shown or evidence[eid]["article_id"] != article.id for eid in ids):
                raise ValueError("Chaque affirmation doit citer des passages de cet article réellement présentés au GPT.")
            validated[category].append({**claim, "basis": "fulltext" if all(evidence[eid]["basis"] == "fulltext" for eid in ids) else "abstract"})
            used.update(ids)
    if not used:
        raise ValueError("Aucune affirmation scientifique étayée n'a été fournie.")
    validated["evidence"] = [evidence[eid] for eid in sorted(used)]
    validated["analysis_basis"] = "fulltext" if all(evidence[eid]["basis"] == "fulltext" for eid in used) else "abstract"
    validated["fulltext_truncated"] = article.fulltext.get("truncated", False)
    validated["semantic_support_status"] = "interpreted_by_conversation_model; exact_source_checked_by_python"
    return validated
