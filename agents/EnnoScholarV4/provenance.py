"""Identity of the retrieved document, separate from the bibliographic work."""
import hashlib
import re
import json
from urllib.parse import urlsplit, unquote
from bs4 import BeautifulSoup
from .models import normalize_doi


def bibliographic_match(article, title="", authors=(), front=""):
    """The admission check: exact normalised title AND a known full author."""
    from .models import public_link
    from .versions import tokens
    wanted = " ".join(tokens(article.title))
    actual = " ".join(tokens(title)) if title and not public_link(title) else ""
    title_matches = bool(wanted and (actual == wanted or not actual and wanted in " ".join(tokens(front[:1200]))))
    known = {tuple(tokens(a)) for a in article.authors if len(tokens(a)) >= 2}
    found = {tuple(tokens(a)) for a in authors if len(tokens(a)) >= 2}
    words = " ".join(tokens(front))
    return bool(title_matches and known and (known & found or any(" ".join(a) in words for a in known)))


def bind_bibliographic_version(version, article, *, title="", authors=(), front="", original_url=""):
    """Persist an observed bibliographic match, never invent a document identifier."""
    if not article.verified or article.metadata_errors or not (article.doi or article.arxiv_id):
        raise ValueError('BibliographicCorrespondenceUnverified')
    if not bibliographic_match(article, title, authors, front):
        raise ValueError('BibliographicCorrespondenceUnverified')
    receipt = {'work_id': article.id, 'publication_doi': normalize_doi(article.doi),
        'publication_arxiv': article.arxiv_id, 'publication_title': article.title,
        'publication_authors': list(article.authors), 'observed_title': title,
        'observed_authors': list(authors), 'observed_front': front[:8000],
        'document_sha256': version['document_sha256'], 'document_version_id': version['version_id'],
        'source_url': version['url'], 'original_url': original_url or version['url'],
        'source_kind': version['source_kind'], 'document_label': version['label'],
        'method': 'exact_title_and_author'}
    receipt['fingerprint'] = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    version['work_correspondence'] = receipt


def restore_bibliographic_correspondence(article):
    """Repair only this requested work's legacy admission, on an in-memory copy.

    The archived PDF is hash checked and its first page revalidates title/authors.
    Old snapshots/proof identifiers remain immutable. No database or network write.
    """
    import os
    from pathlib import Path
    from .pdf_store import file_digest
    key = normalize_doi(article.doi) or article.arxiv_id
    expected = 'paper_'+hashlib.sha256(key.encode()).hexdigest()[:20] if key else ''
    if not article.verified or article.metadata_errors or article.id != expected:
        return False
    changed = False
    documents = [article.fulltext, *article.documents.values()]
    checked = {}
    for document in documents:
        version = document.get('document_version', {})
        if (version.get('work_correspondence') or document.get('identity_verification') != 'exact_title_and_author'
                or document.get('source_kind') != 'pdf' or not document.get('text')):
            continue
        digest = version.get('document_sha256', '')
        if (not re.fullmatch('[a-f0-9]{64}', digest) or version.get('version_id') != 'document_'+digest
                or document.get('pdf_file') != digest+'.pdf' or document.get('url') != version.get('url')):
            continue
        document_key = (digest, version.get('url'))
        if document_key not in checked:
            try:
                directory = Path(os.getenv('ENNOSCHOLAR_V4_PDF_DIRECTORY',
                    str(Path(__file__).resolve().parents[2]/'backend_api'/'storage'/'scholar_v4'/'pdf')))
                path = directory/(digest+'.pdf')
                if file_digest(path) != digest:
                    continue
                import fitz
                with fitz.open(path) as pdf:
                    front = pdf[0].get_text()[:8000]
                # Re-run the original identity check, including conflicting identifiers.
                from .tools.fulltext import checked_identity
                if checked_identity(article, version, front=front) != 'exact_title_and_author':
                    continue
                successful = next((a for a in reversed(document.get('attempts', []))
                    if a.get('stage') == 'extraction' and a.get('outcome') == 'success'
                    and (a.get('final_url') or a.get('url')) == version['url']
                    and a.get('bytes') == path.stat().st_size), None)
                if not successful:
                    continue
                checked[document_key] = (front, successful.get('url') or version['url'])
            except (OSError, ValueError, RuntimeError, IndexError):
                continue
        front, original_url = checked[document_key]
        bind_bibliographic_version(version, article, front=front, original_url=original_url)
        changed = True
    return changed


def bind_open_version(version, article, verified_preprints):
    """Receipt made only after checked_identity accepted independently checked metadata."""
    arxiv = version.get('identifiers', {}).get('arxiv', '')
    approved = next((v for v in verified_preprints if re.sub(r'v\d+$', '', v) == re.sub(r'v\d+$', '', arxiv)
        and (not re.search(r'v\d+$', v) or v == arxiv)), None)
    if not approved or not article.verified or article.metadata_errors:
        raise ValueError('OpenVersionCorrespondenceUnverified')
    receipt = {'work_id': article.id, 'publication_doi': normalize_doi(article.doi),
        'arxiv_id': arxiv, 'verified_arxiv_id': approved,
        'document_sha256': version['document_sha256'], 'document_version_id': version['version_id'],
        'source_url': version['url'], 'method': 'independently_verified_metadata_preprint_alternative'}
    receipt['fingerprint'] = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    version['work_correspondence'] = receipt


def open_version_matches(version, article):
    receipt = version.get('work_correspondence') or {}
    if not receipt:
        # A legacy repair attaches the receipt to the document archive, not to
        # immutable snapshots: their fingerprints and all old proofs stay valid.
        keys = ('version_id', 'label', 'document_sha256', 'url', 'source_kind', 'identifiers', 'version_status')
        for document in [article.fulltext, *article.documents.values()]:
            stored = document.get('document_version', {})
            if stored.get('work_correspondence') and all(stored.get(k) == version.get(k) for k in keys):
                return open_version_matches(stored, article)
        return False
    payload = {k: v for k, v in receipt.items() if k != 'fingerprint'}
    ids = version.get('identifiers', {})
    if receipt.get('method') == 'exact_title_and_author':
        doi = normalize_doi(ids.get('doi') or ids.get('related_publication_doi', ''))
        return bool(article.verified and not article.metadata_errors
            and receipt.get('fingerprint') == hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            and receipt.get('work_id') == article.id
            and receipt.get('publication_doi') == normalize_doi(article.doi)
            and receipt.get('publication_arxiv') == article.arxiv_id
            and receipt.get('publication_title') == article.title and receipt.get('publication_authors') == article.authors
            and receipt.get('document_sha256') == version.get('document_sha256')
            and receipt.get('document_version_id') == version.get('version_id')
            and receipt.get('source_url') == version.get('url')
            and receipt.get('source_kind') == version.get('source_kind')
            and receipt.get('document_label') == version.get('label')
            and (not doi or doi == normalize_doi(article.doi))
            and (not ids.get('arxiv') or ids['arxiv'] == article.arxiv_id)
            and bibliographic_match(article, receipt.get('observed_title', ''),
                receipt.get('observed_authors', []), receipt.get('observed_front', '')))
    return bool(receipt and receipt.get('fingerprint') == hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        and receipt.get('method') == 'independently_verified_metadata_preprint_alternative'
        and receipt.get('work_id') == article.id
        and receipt.get('publication_doi') == normalize_doi(article.doi)
        and receipt.get('document_sha256') == version.get('document_sha256')
        and receipt.get('document_version_id') == version.get('version_id')
        and receipt.get('source_url') == version.get('url')
        and receipt.get('arxiv_id') == ids.get('arxiv')
        and re.sub(r'v\d+$', '', receipt.get('verified_arxiv_id', '')) == re.sub(r'v\d+$', '', ids.get('arxiv', ''))
        and (not re.search(r'v\d+$', receipt.get('verified_arxiv_id', '')) or receipt.get('verified_arxiv_id') == ids.get('arxiv')))


def document_identity(data, url, kind, chunks):
    from pathlib import Path
    if isinstance(data, (str, Path)):
        from .pdf_store import file_digest
        digest = file_digest(data)
    else:
        digest = hashlib.sha256(data).hexdigest()
    identifiers = {}
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    def on_domain(domain):
        return host == domain or host.endswith("."+domain)
    path = unquote(parsed.path)
    front = chunks[0].text[:8000] if chunks else ""
    if kind == "html":
        soup = BeautifulSoup(data, "html.parser")
        front = soup.get_text(" ", strip=True)[:5000]
        for name in ("citation_doi", "dc.identifier"):
            node = soup.find("meta", attrs={"name": name})
            doi = normalize_doi(str(node.get("content", ""))) if node else ""
            if doi:
                identifiers["doi"] = doi
                break
    elif kind == "jats":
        # Extraction has already parsed JATS with external entities disabled.
        from lxml import etree
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
        for node in root.xpath("//*[local-name()='article-meta']/*[local-name()='article-id']"):
            name = node.get("pub-id-type")
            if name in {"doi", "pmcid", "pmid"}:
                identifiers[name] = "".join(node.itertext()).strip()
    else:
        # Only explicit front matter; a DOI in the references cannot identify a PDF.
        doi = re.search(r"(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)(10\.\d{4,9}/[^\s<>]+)", front, re.I)
        if doi:
            identifiers["doi"] = normalize_doi(doi.group(1).rstrip(".,;)"))
    arxiv = re.search(r"arXiv\s*:\s*(\d{4}\.\d{4,5}v\d+)", front, re.I)
    if on_domain("arxiv.org"):
        arxiv = arxiv or re.search(r"/(?:abs|pdf|html)/(\d{4}\.\d{4,5}(?:v\d+)?)", path)
    if arxiv:
        identifiers["arxiv"] = arxiv.group(1)
        if identifiers.get("doi") and not identifiers["doi"].startswith("10.48550/arxiv."):
            identifiers["related_publication_doi"] = identifiers.pop("doi")
    bio = re.search(r"(\d{4}\.\d{2}\.\d{2}\.\d+(?:v\d+)?)", path) if on_domain("biorxiv.org") else None
    if bio:
        identifiers["biorxiv"] = bio.group(1)
        preprint_doi = "10.1101/"+re.sub(r"v\d+$", "", bio.group(1))
        if identifiers.get("doi") and identifiers["doi"] != preprint_doi:
            identifiers["related_publication_doi"] = identifiers["doi"]
        identifiers["doi"] = preprint_doi
    pmc = re.search(r"(PMC\d+)", path, re.I) if on_domain("ncbi.nlm.nih.gov") or on_domain("ebi.ac.uk") else None
    if pmc:
        identifiers.setdefault("pmcid", pmc.group(1).upper())
    label = next((name+":"+identifiers[name] for name in ("arxiv", "biorxiv", "pmcid", "doi") if identifiers.get(name)), "document_sha256:"+digest)
    return {"version_id": "document_"+digest, "label": label, "identifiers": identifiers,
        "document_sha256": digest, "url": url, "source_kind": kind,
        "version_status": "document_identified" if identifiers else "identifier_unverified"}
