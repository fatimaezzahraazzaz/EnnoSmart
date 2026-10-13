"""Exact source passages: immutable version identity, pages and quotations."""
import hashlib
import re


def snapshot(article):
    fulltext = article.fulltext
    chunks = fulltext.get("chunks", []) if fulltext.get("text") else (
        [{"text": article.abstract, "page": None, "section": "Abstract"}] if article.abstract else [])
    url = fulltext.get("url") or article.url
    text = "\n\n".join(c["text"] for c in chunks)
    fingerprint = hashlib.sha256((url+"\n"+text).encode()).hexdigest()
    return {"article_id": article.id, "url": url, "chunks": chunks, "text": text,
        "fingerprint": fingerprint, "basis": "fulltext" if fulltext.get("text") else "abstract"}


def exact_source_valid(evidence, snapshots):
    source = snapshots.get(evidence.get("source_fingerprint"))
    if not source or source["article_id"] != evidence["source_article_id"] or source["url"] != evidence["source_url"]:
        return False
    if hashlib.sha256((source["url"]+"\n"+source["text"]).encode()).hexdigest() != evidence["source_fingerprint"]:
        return False
    try:
        chunk = source["chunks"][evidence["chunk_index"]]
        start = evidence["char_offset"]
        return (chunk["text"][start:start+len(evidence["exact_quote"])] == evidence["exact_quote"] and
            chunk.get("page") == evidence["page_number"] and chunk.get("section", "") == evidence["section"])
    except (IndexError, KeyError, TypeError):
        return False


def passages(article):
    fulltext = article.fulltext
    chunks = fulltext.get("chunks", []) if fulltext.get("text") else (
        [{"text": article.abstract, "page": None, "section": "Abstract"}] if article.abstract else [])
    url = fulltext.get("url") or article.url
    version = article.source_version or article.doi or article.arxiv_id or url
    fingerprint = snapshot(article)["fingerprint"]
    rows = []
    for index, chunk in enumerate(chunks):
        text = chunk["text"]
        for offset in range(0, len(text), 1000):
            quote = text[offset:offset + 1200]
            if not quote.strip():
                continue
            eid = "ev_" + hashlib.sha256(f"{article.id}|{fingerprint}|{index}|{offset}|{quote}".encode()).hexdigest()[:24]
            rows.append({"evidence_id": eid, "article_id": article.id,
                "canonical_work_id": article.canonical_work_id or article.id, "source_article_id": article.id,
                "source_url": url, "source_version": version, "source_fingerprint": fingerprint,
                "page_number": chunk.get("page"), "page": chunk.get("page"), "section": chunk.get("section", ""),
                "exact_quote": quote, "text": quote, "basis": "fulltext" if fulltext.get("text") else "abstract",
                "chunk_index": index, "char_offset": offset})
    return rows


def choose_passages(rows, query, offset=0, limit=8):
    if query:
        terms = set(re.findall(r"\w+", query.casefold()))
        rows = sorted(rows, key=lambda r: (-sum(term in (r["section"] + " " + r["text"]).casefold() for term in terms), r["chunk_index"], r["char_offset"]))
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
