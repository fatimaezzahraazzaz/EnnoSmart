"""Identity of the retrieved document, separate from the bibliographic work."""
import hashlib
import re
from urllib.parse import urlsplit, unquote
from bs4 import BeautifulSoup
from .models import normalize_doi


def document_identity(data, url, kind, chunks):
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
