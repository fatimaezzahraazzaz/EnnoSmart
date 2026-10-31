from __future__ import annotations

import re
import time
from urllib.parse import urlsplit, urljoin
from agents.EnnoScholarV2.models import Article as LegacyArticle
from agents.EnnoScholarV2.tools.article_reader import matches_article
from .extraction import pdf_chunks, html_document, xml_document
from ..network import fetch_document as fetch_public
from ..pdf_store import save_pdf, StagedDocument, archive_file


def merge_attempts(*histories):
    """Merge the existing article journal without duplicating imported attempts."""
    result = []
    for history in histories:
        for attempt in history:
            if attempt not in result:
                result.append(dict(attempt))
    return sorted(result, key=lambda a: a.get("attempted_at", 0))[-100:]


def retry_at(attempt, max_bytes=None, memory_bytes=None, *, requested=False, max_attempts=2):
    """None means this exact URL already failed permanently; zero allows a retry."""
    if attempt.get("reason") == "Publication trop volumineuse.":
        # Legacy refusals used 15 MiB. A newly authorised larger limit may
        # reconsider the URL, without erasing the original refusal.
        previous_limit = attempt.get("max_bytes", 15728640)
        if attempt.get("storage_mode") == "memory" and memory_bytes is not None:
            max_bytes = memory_bytes
        if max_bytes is not None and max_bytes > previous_limit:
            observed = attempt.get("observed_bytes")
            return 0 if observed is None or observed <= max_bytes else None
        return None
    if (attempt.get("http_status") in {404, 410} or attempt.get("reason") in {
            "DocumentIdentityMismatch", "UnsupportedFormat",
            "RequestedPDFUnavailable", "Publication trop volumineuse."}):
        return None
    if attempt.get("http_status") == 403 or attempt.get("reason") in {
            "AccessRestricted", "DocumentIdentityUnconfirmed", "ScientificBodyUnavailable"}:
        if attempt.get("attempt_number", 1) >= max_attempts:
            return None
        # A legacy failure without a date can be reconsidered once only when
        # this URL is explicitly chosen; never replay the entire old history.
        when = attempt.get("attempted_at")
        if not when:
            return 0 if requested else None
        return max(when+300, attempt.get("retry_at", 0))
    return attempt.get("retry_at", 0)


def checked_identity(article, version, title="", authors=(), front="", verified_preprints=()):
    """Verify the retrieved content, never words guessed from its URL."""
    from ..models import normalize_doi
    ids = version.get("identifiers", {})
    doi = normalize_doi(ids.get("doi") or ids.get("related_publication_doi", ""))
    expected = normalize_doi(article.doi)
    arxiv, target = ids.get("arxiv", ""), article.arxiv_id
    if arxiv and any(re.sub(r"v\d+$", "", arxiv) == re.sub(r"v\d+$", "", approved) and (
            not re.search(r"v\d+$", approved) or arxiv == approved) for approved in verified_preprints):
        return "verified_preprint_alternative"
    if arxiv and target:
        if re.sub(r"v\d+$", "", arxiv) != re.sub(r"v\d+$", "", target) or (
                re.search(r"v\d+$", target) and arxiv != target):
            raise ValueError("DocumentIdentityMismatch")
        return "arxiv"
    if doi and expected:
        if doi != expected:
            raise ValueError("DocumentIdentityMismatch")
        return "doi"
    # Without shared identifiers, require the exact title AND a known author
    # in page metadata or PDF front matter. A title in a search URL proves nothing.
    from ..provenance import bibliographic_match
    if bibliographic_match(article, title, authors, front):
        return "exact_title_and_author"
    return ""


def linked_html_tables(data, url, article, deadline, check, config, attempts, allowance):
    """Read only explicit table links of this same article, using the existing fetch limits."""
    import hashlib
    from bs4 import BeautifulSoup
    from .extraction import ScientificChunk
    from .tables import html_table, table_text
    soup = BeautifulSoup(data, "html.parser")
    base = urlsplit(url)
    links = {}
    for a in soup.select("a[href]"):
        target = urlsplit(urljoin(url, a["href"]))
        match = re.fullmatch(re.escape(base.path.rstrip("/"))+r"/tables/(\d+)", target.path)
        if target.scheme == "https" and target.netloc == base.netloc and match:
            links[target._replace(fragment="", query="").geturl()] = match.group(1)
    chunks, sources = [], []
    for target, number in list(links.items())[:min(4, allowance)]:
        if time.monotonic()+1 >= deadline:
            break
        check()
        try:
            raw, content_type, final_url = fetch_public(target, min(deadline, time.monotonic()+10), check,
                max_bytes=min(config.max_pdf_bytes, 1000000), pdf_to_disk=False)
            if "html" not in content_type or urlsplit(final_url).netloc != base.netloc or urlsplit(final_url).path != urlsplit(target).path:
                raise ValueError("LinkedTableIdentityMismatch")
            page = BeautifulSoup(raw, "html.parser")
            meta = page.find("meta", attrs={"name": "citation_doi"})
            from ..models import normalize_doi
            if meta and article.doi and normalize_doi(meta.get("content", "")) != article.doi:
                raise ValueError("LinkedTableIdentityMismatch")
            digest = hashlib.sha256(raw).hexdigest()
            retained = []
            for node in page.select("table"):
                if node.find_parent("table"):
                    continue
                table = html_table(node)
                if table.get("number") not in (None, number):
                    continue
                table.update(number=number, source_url=final_url, source_sha256=digest)
                retained.append(ScientificChunk(section="Table "+number, text=table_text(table), table=table))
            if not retained:
                raise ValueError("LinkedTableUnavailable")
            chunks.extend(retained)
            sources.append({"url": final_url, "sha256": digest, "html": raw.decode("utf-8", errors="replace")})
            attempts.append({"url": target, "stage": "linked_table", "outcome": "success", "tables": len(retained)})
        except Exception as exc:
            check()
            attempts.append({"url": target, "stage": "linked_table", "error": type(exc).__name__,
                "reason": str(exc)[:160] if isinstance(exc, ValueError) else ""})
    return chunks, sources


def representation_kind(article, url):
    """Document format for scheduling only; never bibliographic identification."""
    declared = next((str(c.get('format', '')).lower() for c in article.document_candidates if c.get('url') == url), '')
    path = urlsplit(url).path.lower()
    if declared in {'xml', 'jats'} or path.endswith('.xml') or path.endswith('/fulltextxml'):
        return 'xml'
    if declared == 'pdf' or path.endswith('.pdf') or '/pdf/' in path:
        return 'pdf'
    if declared == 'html' or path.endswith(('.html', '.full')) or '/html/' in path:
        return 'html'
    return 'landing'


def ordered_urls(article, urls, pdf_only=False):
    from ..models import public_link
    urls = list(dict.fromkeys(u for u in urls if u and public_link(u)))
    if pdf_only:
        return [u for u in urls if representation_kind(article, u) == 'pdf'] or urls
    groups = {kind: [u for u in urls if representation_kind(article, u) == kind]
        for kind in ('xml', 'html', 'landing', 'pdf')}
    # One cheap landing can expose HTML/XML links. A direct document must not
    # wait behind an unbounded catalogue of other landing pages.
    return [*groups['xml'], *groups['html'], *groups['landing'][:1],
        *groups['pdf'], *groups['landing'][1:]]


def document_urls(article, candidate_urls=None, pdf_only=False):
    explicit = list(candidate_urls or [])
    derived = []
    if article.arxiv_id:
        derived.extend(["https://arxiv.org/html/" + article.arxiv_id, "https://arxiv.org/pdf/" + article.arxiv_id])
    originals = [article.pdf_url, *article.work_urls,
        *[c["url"] for c in article.document_candidates if c.get("url")], article.url]
    # Locations resolved in a preceding call remain available after a checkpoint.
    originals.extend(u for a in article.fulltext.get("attempts", []) if a.get("stage") == "oa_resolution"
        for u in a.get("urls", []))
    originals.extend(u for a in article.fulltext.get("attempts", []) for u in a.get("discovered_urls", []))
    # Publisher landing pages derived from the collected PDF location, before
    # slow PDF transfer. The same identity and scientific-body checks still run.
    if article.pdf_url and not pdf_only:
        parsed = urlsplit(article.pdf_url)
        if parsed.hostname == "link.springer.com" and article.doi:
            derived.append("https://link.springer.com/article/" + article.doi)
        elif (parsed.hostname or "").endswith("mdpi.com") and parsed.path.endswith("/pdf"):
            derived.append(parsed._replace(path=parsed.path[:-4], query="").geturl())
    for url in [*explicit, *originals]:
        if not url:
            continue
        pmc = re.search(r"(?:pmc/)?articles/(?:PMC)?(\d+)", url, re.I)
        if pmc and (urlsplit(url).hostname or "").endswith("ncbi.nlm.nih.gov"):
            derived.append("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC" + pmc.group(1) + "/fullTextXML")
        bio = re.search(r"(\d{4}\.\d{2}\.\d{2}\.\d+(?:v\d+)?)", url)
        if bio and "biorxiv.org" in (urlsplit(url).hostname or ""):
            derived.append("https://www.biorxiv.org/content/10.1101/" + bio.group(1) + ".full")
    originals.extend(article.fulltext.get('remaining_document_urls', []))
    return ordered_urls(article, [*explicit, *derived, *originals], pdf_only)


def get_fulltext(article, sources, deadline, check=lambda: None, candidate_urls=None, config=None, pdf_only=False,
        web_candidates=None, verified_preprints=()):
    from ..config import ScholarV4Config
    config = config or ScholarV4Config.from_env()
    deadline = min(deadline, time.monotonic() + (config.max_pdf_download_seconds if pdf_only else config.max_download_seconds))
    attempts = merge_attempts(article.fulltext.get("attempts", []))
    history_length = len(attempts)
    seen, cooldowns, skipped = set(), {}, []
    if article.fulltext.get('text'):
        seen.add(article.fulltext.get('url'))
        seen.update(d.get('url') for d in article.documents.values() if d.get('text'))
    from ..models import public_link
    web_required = {u for u in (web_candidates or []) if public_link(u)}
    urls = list(dict.fromkeys([*ordered_urls(article, candidate_urls or [], pdf_only),
        *ordered_urls(article, [*document_urls(article, pdf_only=pdf_only), *(web_candidates or [])], pdf_only)]))
    def retry_due(attempt, url):
        return retry_at(attempt, config.max_pdf_disk_bytes, config.max_pdf_bytes,
            requested=url in (candidate_urls or []), max_attempts=config.max_retry_attempts)
    resolution = next((a for a in reversed(attempts) if a.get("stage") == "oa_resolution"), {})
    resolved, downloads = resolution.get("retry_at", 0) > time.time(), 0
    retries = []
    def eligible(url):
        if not url or url in seen or cooldowns.get(urlsplit(url).hostname, 0) > time.monotonic():
            return False
        last = next((a for a in reversed(attempts) if a.get('url') == url and a.get('stage') in {'download', 'extraction', 'storage'}), None)
        due = retry_due(last, url) if last else 0
        return due is not None and due <= time.time()
    while True:
        for url in urls:
            check()
            if not url or url in seen or downloads >= config.max_download_attempts or time.monotonic() >= deadline:
                continue
            seen.add(url)
            previous = [a for a in attempts if a.get("url") == url and a.get("stage") in {"download", "extraction", "storage"}]
            last = previous[-1] if previous else None
            due = retry_due(last, url) if last else 0
            if due is None or due > time.time():
                skipped.append({"url": url, "reason": "previous_permanent_failure" if due is None else "retry_deferred", "retry_at": due})
                continue
            host = urlsplit(url).hostname
            if cooldowns.get(host, 0) > time.monotonic():
                attempts.append({"url": url, "stage": "download", "outcome": "deferred_host_rate_limit"})
                continue
            started = time.monotonic()
            downloads += 1
            stage = "download"
            discovered = []
            staged = None
            try:
                # Large PDFs can require >18 seconds. Keep the total article deadline,
                # reserve time for alternatives when several candidates remain.
                remaining = deadline-time.monotonic()
                remaining_urls = sum(eligible(u) for u in urls)
                allowance = remaining if not remaining_urls else min(70 if pdf_only else 25,
                    max(5, remaining / min(3, remaining_urls + 1)))
                data, content_type, final_url = fetch_public(url, min(deadline, time.monotonic()+allowance), check,
                    max_bytes=config.max_pdf_bytes, config=config)
                if isinstance(data, StagedDocument):
                    staged, data = data, data.path
                is_pdf = staged is not None or isinstance(data, bytes) and data.startswith(b"%PDF")
                stage = "extraction"
                if pdf_only and not is_pdf:
                    raise ValueError("RequestedPDFUnavailable")
                source_title, source_authors, identity = "", [], ""
                version = None
                progress = {}
                if is_pdf:
                    chunks, truncated = pdf_chunks(data, config, page_limit=min(config.pdf_extraction_batch_pages, config.max_pdf_pages),
                        progress=progress, deadline=deadline, check=check)
                    kind = "pdf"
                elif "html" in content_type:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(data, "html.parser")
                    page_title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
                    if any(token in page_title for token in ("checking your browser", "verify you are human", "just a moment")):
                        raise ValueError("AccessRestricted")
                    row, chunks, truncated = html_document(data, final_url, config, allow_partial=True)
                    source_title, source_authors = row.title, row.authors
                    from ..provenance import document_identity
                    version = document_identity(data, final_url, "html", chunks)
                    identity = checked_identity(article, version, row.title, row.authors, verified_preprints=verified_preprints)
                    if url in web_required and not identity:
                        raise ValueError("DocumentIdentityUnconfirmed")
                    # Follow only explicit article metadata/alternate links of
                    # this identified work, never an arbitrary repository crawl.
                    from ..models import normalize_doi, public_link
                    if (identity or matches_article(LegacyArticle(title=article.title), row.title)) and not (
                            article.doi and row.doi and normalize_doi(row.doi) != normalize_doi(article.doi)):
                        links = [row.pdf_url]
                        links.extend(n.get("content", "") for n in soup.select(
                            'meta[name="citation_pdf_url"], meta[name="citation_fulltext_html_url"]'))
                        links.extend(n.get("href", "") for n in soup.select('link[rel="alternate"]')
                            if n.get("type") in {"application/pdf", "text/html", "application/xml"})
                        discovered = [urljoin(final_url, u) for u in links if u and public_link(urljoin(final_url, u))]
                        if url in web_required:
                            web_required.update(discovered)
                        # A verified landing page's content links precede unrelated
                        # search results, within the same download/time quotas.
                        index = urls.index(url)+1
                        urls[index:] = ordered_urls(article, [*discovered, *urls[index:]], pdf_only)
                    # A genuine article may load a CAPTCHA widget for its comment form.
                    # Reject actual challenge pages, not incidental script references.
                    if not chunks and any(token in soup.get_text(" ", strip=True).casefold() for token in ("verify you are human", "captcha", "just a moment")):
                        raise ValueError("AccessRestricted")
                    source_title, kind = row.title, "html"
                elif "xml" in content_type:
                    row, chunks, truncated = xml_document(data, final_url, config)
                    source_title, source_authors, kind = row.title, row.authors, "jats"
                else:
                    raise ValueError("UnsupportedFormat")
                text = "\n\n".join(chunk.text for chunk in chunks)
                identity_text = source_title + "\n" + (chunks[0].text[:7000] if chunks else "")
                from ..provenance import document_identity
                version = version or document_identity(data, final_url, kind, chunks)
                identity = identity or checked_identity(article, version, source_title, source_authors,
                    chunks[0].text[:8000] if chunks else "", verified_preprints)
                if (url in web_required or kind == 'html' and truncated) and not identity:
                    raise ValueError("DocumentIdentityUnconfirmed")
                if len(text) < 500:
                    raise ValueError("ScientificBodyUnavailable")
                if not identity and not matches_article(LegacyArticle(title=article.title), identity_text):
                    raise ValueError("DocumentIdentityMismatch")
                supplements = []
                if kind == "html":
                    additional, supplements = linked_html_tables(data, final_url, article, deadline, check, config,
                        attempts, config.max_download_attempts-downloads)
                    if additional:
                        from .extraction import bounded
                        chunks, extra_truncated = bounded([*chunks, *additional], config)
                        truncated |= extra_truncated
                        text = "\n\n".join(chunk.text for chunk in chunks)
                check()
                stage = "storage"
                filename = (archive_file(staged) if staged else save_pdf(data, max_bytes=config.max_pdf_disk_bytes)) if kind == "pdf" else ""
                byte_count = staged.size if staged else len(data)
                if identity == "verified_preprint_alternative":
                    from ..provenance import bind_open_version
                    bind_open_version(version, article, verified_preprints)
                elif (identity == 'exact_title_and_author' and article.verified and not article.metadata_errors
                        and (article.doi or article.arxiv_id)):
                    from ..provenance import bind_bibliographic_version
                    bind_bibliographic_version(version, article, title=source_title, authors=source_authors,
                        front=chunks[0].text[:8000] if chunks else '', original_url=url)
                attempts.append({"url": url, "final_url": final_url, "stage": "extraction", "outcome": "success",
                    "bytes": byte_count, "characters": len(text), "seconds": round(time.monotonic()-started, 3),
                    "attempted_at": time.time(), "attempt_number": len(previous)+1})
                return {"access_status": "fulltext_extracted", "access_level": "fulltext", "text": text,
                    "url": final_url, "pdf_file": filename, "source_kind": kind,
                    "document_version": version,
                    "extraction_version": "scientific_structures_v2",
                    "identity_verification": identity or "collected_title_match",
                    "content_scope": "partial_scientific_text" if truncated else "scientific_body",
                    "source_html": data.decode("utf-8", errors="replace") if kind == "html" else "",
                    "linked_table_sources": supplements,
                    "extraction_limits": ["Les figures et courbes ne sont pas interprétées visuellement ; leurs valeurs non présentes dans le texte ou les tableaux restent non vérifiées."],
                    "chunks": [chunk.model_dump() for chunk in chunks], "truncated": truncated,
                    "remaining_document_urls": ordered_urls(article, [u for u in urls if u not in seen], pdf_only),
                    **progress,
                    "attempts": attempts, "reason": ("Texte scientifique partiel extrait ; document incomplet, lecture et analyse restent à effectuer."
                        if truncated else "Corps scientifique extrait ; lecture et analyse restent à effectuer.")}
            except Exception as exc:
                check()
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)
                retry_after = response.headers.get("retry-after") if response is not None else None
                number = max([a.get("attempt_number", 1) for a in previous], default=0)+1
                try:
                    delay = max(2, float(retry_after or 2), 300 if number >= config.max_retry_attempts else 2)
                except ValueError:
                    delay = 300 if number >= config.max_retry_attempts else 2
                if status == 403 or str(exc) in {'AccessRestricted','ScientificBodyUnavailable','DocumentIdentityUnconfirmed'}:
                    delay = max(delay, 300)
                size = {key: getattr(exc, key) for key in ("max_bytes", "observed_bytes", "size_source", "content_type", "storage_mode") if hasattr(exc, key)}
                attempts.append({"url": url, "stage": stage, "http_status": status, "error": type(exc).__name__,
                    "reason": str(exc)[:160] if isinstance(exc, (ValueError, TimeoutError)) else "",
                    "retry_after": retry_after, "seconds": round(time.monotonic()-started, 3),
                    "attempted_at": time.time(), "attempt_number": number, "retry_at": time.time()+delay,
                    **size,
                    **({"discovered_urls": discovered} if discovered else {})})
                if (status in {429, 500, 502, 503, 504} or isinstance(exc, TimeoutError) or
                        type(exc).__name__ in {"ReadTimeout", "ConnectTimeout", "PoolTimeout", "WriteTimeout"}) and number < config.max_retry_attempts:
                    try:
                        delay = max(2, float(retry_after or 2))
                    except ValueError:
                        delay = 2
                    cooldowns[host] = time.monotonic()+delay
                    retries.append(url)
            finally:
                if staged is not None:
                    staged.close()
        if not resolved and article.doi and time.monotonic()+2 < deadline and downloads < config.max_download_attempts:
            resolved = True
            try:
                rows = sources.metadata(article.doi, min(deadline, time.monotonic()+12), check)
                urls = list(dict.fromkeys(u for row in rows if (
                    (not row.doi or row.doi == article.doi) and matches_article(LegacyArticle(title=article.title), row.title))
                    for u in document_urls(row, pdf_only=pdf_only)))
                attempts.append({"stage": "oa_resolution", "outcome": "success", "urls": urls,
                    "attempted_at": time.time(), "retry_at": time.time()+300})
            except Exception as exc:
                check()
                attempts.append({"stage": "oa_resolution", "error": type(exc).__name__,
                    "attempted_at": time.time(), "retry_at": time.time()+300})
                urls = []
            continue
        if retries and downloads < config.max_download_attempts:
            url = retries.pop(0)
            previous = next((a for a in reversed(attempts) if a.get('url') == url), {})
            due = retry_due(previous, url)
            wait = max(0, cooldowns.get(urlsplit(url).hostname, 0)-time.monotonic(), (due or 0)-time.time())
            if time.monotonic()+wait+2 < deadline:
                until = time.monotonic()+wait
                # History uses wall time; host cooldowns use monotonic time.
                # Both must expire before the eligibility check above, including
                # on platforms where the two clocks have different resolutions.
                while time.monotonic() < until or time.time() < (due or 0):
                    check()
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(min(.1, max(0, until-time.monotonic(), (due or 0)-time.time())))
                seen.discard(url)
                urls = [url]
                # At most one delayed retry of a candidate.
                retries = []
                resolved = True
                continue
        break
    current = [a for a in attempts[history_length:] if a.get("url") and a.get("error")]
    relevant = merge_attempts(current, [next(a for a in reversed(attempts) if a.get("url") == s["url"]) for s in skipped])
    oversized = next((a for a in reversed(relevant) if a.get("reason") == "Publication trop volumineuse."), None)
    blocked = any(a.get("http_status") == 403 or a.get("reason") == "AccessRestricted" for a in relevant)
    extraction_failed = any(a.get("stage") == "extraction" for a in relevant)
    reason = "Aucun corps scientifique lisible récupéré ; les notices et preuves disponibles restent conservées."
    if oversized:
        limit = oversized.get("max_bytes", config.max_pdf_bytes)
        observed = oversized.get("observed_bytes")
        reason = (f"Document refusé avant extraction : limite de téléchargement de {limit} octets"
            + (f", taille {'annoncée' if oversized.get('size_source') == 'content_length' else 'observée au moins'} {observed} octets" if observed else "")
            + ". Aucune représentation scientifique alternative n'a pu être récupérée.")
    return {"access_status": "access_blocked" if blocked else "extraction_failed" if extraction_failed else "abstract_only" if article.abstract else "metadata_only",
        "access_level": "abstract_only" if article.abstract else "metadata_only", "text": "", "chunks": [], "truncated": False,
        "attempts": attempts, "skipped_urls": skipped,
        "failure_stage": "download" if oversized else "extraction" if extraction_failed else "download" if downloads else "discovery",
        "failure_code": "download_size_limit" if oversized else "scientific_body_unavailable",
        "content_scope": "abstract_only" if article.abstract else "unavailable",
        "reason": reason}


class FullTextResolver:
    def resolve(self, article, sources, deadline, check=lambda: None, candidate_urls=None, config=None):
        if article.fulltext.get("text"):
            return article.fulltext
        return get_fulltext(article, sources, deadline, check, candidate_urls, config)
