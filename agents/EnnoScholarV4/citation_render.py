"""One rendering of the manuscript's citations for the workspace and the exports.

Drafts keep their raw identifiers ([paper_…], [ev_…], [projet:…]) in raw_text; this
module derives a readable text from them: numbered references [n], exact passages Pn,
project passages Dn (one per canonical passage, aliases kept for audit). A group of
adjacent markers becomes one bracket, without repetition. Section bodies lose a
leading heading that only repeats the plan title (the document carries the heading).
Metadata are shown when known, never invented; missing fields leave no empty
punctuation. Deterministic, no LLM call.
"""
from __future__ import annotations

import re
import unicodedata

MARKERS = re.compile(r"\[(paper_[\w-]+|ev_[\w-]+|projet:[\w-]+)\](?:\s*\[(?:paper_[\w-]+|ev_[\w-]+|projet:[\w-]+)\])*")
ONE = re.compile(r"\[(paper_[\w-]+|ev_[\w-]+|projet:[\w-]+)\]")
NUMBERING = re.compile(r"^\s*(?:(?:section|partie)\s+)?(?:\d+(?:\.\d+)*|[IVX]+)\s*[.):—–-]\s+", re.I)


def _norm(text):
    text = unicodedata.normalize("NFKD", NUMBERING.sub("", text or "")).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", "".join(c for c in text if not unicodedata.combining(c))).split())


def section_body(section, draft):
    """The draft's raw text without a first heading that only repeats the section title."""
    raw = draft.get("raw_text") or draft.get("text") or ""
    lines = raw.lstrip("\n").split("\n")
    heading = re.match(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$", lines[0]) if lines else None
    if heading and _norm(heading.group(1)) == _norm(section.get("title", "")):
        return "\n".join(lines[1:]).lstrip("\n")
    return raw


def format_reference(meta):
    """« Auteurs (année). Titre. Revue. DOI/URL » with only the known fields."""
    authors = [a for a in meta.get("authors") or [] if a]
    names = (", ".join(authors[:6]) + (" et al." if len(authors) > 6 else "")) if authors else ""
    head = " ".join(p for p in (names, f"({meta['year']})" if meta.get("year") else "") if p)
    url = ("https://doi.org/" + meta["doi"]) if meta.get("doi") else ("https://arxiv.org/abs/" + meta["arxiv_id"]) if meta.get("arxiv_id") else meta.get("url") or ""
    parts = [head, (meta.get("title") or "").strip().rstrip("."), (meta.get("journal") or "").strip().rstrip("."), url]
    return ". ".join(p for p in parts if p) + ("" if url else ".")


def proof_locator(row, snapshots=None):
    """A display-only correction from a literal heading in the archived passage.

    Never mutate source evidence: its section is part of the source fingerprint.
    No page number, bibliographic identity or passage text is inferred.
    """
    locator = {"section": row.get("section"), "page_number": row.get("page_number", row.get("page"))}
    if (row.get("section") or "").strip().casefold() == "references":
        from .tools.extraction import scientific_heading
        def leading_heading(text):
            line = next((line for line in text.splitlines()
                if line.strip() and not re.fullmatch(r"\s*\d+\s*", line)), "")
            return scientific_heading(line)
        # Continuation pages may have no heading of their own. Reuse only a
        # literal preceding heading in this exact, verified source snapshot.
        from .evidence import exact_source_valid
        if snapshots and leading_heading(row.get("exact_quote", "")) is None and exact_source_valid(row, snapshots):
            source = snapshots[row["source_fingerprint"]]
            previous = None
            for chunk in source.get("chunks", [])[:row.get("chunk_index", -1)+1]:
                found = leading_heading(chunk.get("text", ""))
                # A table/figure caption identifies its own passage, not every
                # later page. Only section headings carry across chunks.
                if found and not re.search(r"\b(?:Table|Fig(?:ure)?\.?)\s*[A-Z]?\d", found, re.I):
                    previous = (found, chunk.get("page"))
            if previous and previous[0].casefold() != "references":
                locator.update(section=previous[0], display_correction={"stored_section": row["section"],
                    "basis": "literal_preceding_heading_in_exact_source_snapshot", "evidence_id": row.get("evidence_id"),
                    "source_fingerprint": row.get("source_fingerprint"), "heading_page_number": previous[1]})
        for line in row.get("exact_quote", "").splitlines():
            if not line.strip() or re.fullmatch(r"\s*\d+\s*", line):
                continue
            heading = scientific_heading(line)
            if heading and heading.casefold() != "references":
                locator.update(section=heading, display_correction={"stored_section": row["section"],
                    "basis": "literal_heading_in_exact_quote", "evidence_id": row.get("evidence_id"),
                    "source_fingerprint": row.get("source_fingerprint")})
            break  # Only an explicit leading heading; no title inference from body prose.
    return locator


def render_manuscript(state, sections, article_metadata=None, evidence=None):
    """Readable bodies and numbered apparatus for the drafted sections, in plan order."""
    from .diagnostic_link import diagnostic_evidence, passage_index
    articles = article_metadata or state.get("articles", {})
    evidence = evidence if evidence is not None else state.get("evidence", {})
    project = diagnostic_evidence(state)
    index = passage_index(state) if project else {"canonical": {}, "aliases": {}, "locators": {}}
    references, proofs, sources = {}, {}, {}
    bodies = {}

    def number(table, key):
        return table.setdefault(key, len(table)+1)

    def group(match):
        ids = list(dict.fromkeys(ONE.findall(match.group(0))))
        refs, passages, project_refs = [], [], []
        for marker in ids:
            if marker.startswith("paper_") and marker in articles:
                refs.append(number(references, marker))
            elif marker.startswith("ev_") and marker in evidence:
                refs.append(number(references, evidence[marker]["article_id"]))
                passages.append(number(proofs, marker))
            elif marker.startswith("ev_") and marker in project:
                project_refs.append(number(sources, index["canonical"].get(marker, marker)))
            elif marker.startswith("projet:"):
                pid = marker.split(":", 1)[1]
                project_refs.append(number(sources, index["canonical"].get(pid, pid)))
            else:
                return match.group(0)  # unknown marker: left visible for review
        parts = [", ".join(str(n) for n in dict.fromkeys(refs)), ", ".join(f"P{n}" for n in dict.fromkeys(passages)),
            ", ".join(f"D{n}" for n in dict.fromkeys(project_refs))]
        return "[" + " ; ".join(p for p in parts if p) + "]"

    for section in sections:
        draft = (section.get("drafts") or [None])[-1]
        if draft:
            bodies[section["id"]] = MARKERS.sub(group, section_body(section, draft))
    reference_rows = [{"number": n, "article_id": aid, "text": format_reference(articles.get(aid, {})),
        **{k: articles.get(aid, {}).get(k) for k in ("title", "authors", "year", "journal", "doi", "arxiv_id", "url")
           if articles.get(aid, {}).get(k)}} for aid, n in references.items()]
    proof_rows = []
    for eid, n in proofs.items():
        row = evidence[eid]
        proof_rows.append({"number": f"P{n}", "evidence_id": eid, "reference": references.get(row["article_id"]),
            **proof_locator(row, state.get("source_snapshots")), "quote": row.get("exact_quote", "")[:500]})
    source_rows = []
    for cid, n in sources.items():
        row = project.get(cid, {})
        locator = index["locators"].get(cid, {})
        source_rows.append({"number": f"D{n}", "canonical_evidence_id": cid, "aliases": index["aliases"].get(cid, [cid]),
            "document_name": row.get("document_name"), "section_title": locator.get("effective_section_title") or row.get("section_title"),
            **({"stored_section_title": locator["stored_section_title"]} if locator.get("corrected") else {}),
            **({"spans": locator["spans"]} if locator.get("spans") else {}),
            "page": row.get("page"), "quote": (row.get("quote") or "")[:500]})
    return {"sections": bodies, "references": reference_rows, "proofs": proof_rows, "project_sources": source_rows}
