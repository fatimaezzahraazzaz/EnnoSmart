"""Conservative metadata correspondence for open preprint alternatives.

No canonical merge: every downloaded document retains its own version, URL and
hash. A different author set, a short / merely similar title, two publisher DOIs
or unverified identities cannot authorize an alternate source.
"""
import re
import unicodedata


def tokens(text):
    text = unicodedata.normalize("NFKD", str(text)).casefold()
    return re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c)))


def author_keys(article):
    return {tuple(sorted(tokens(a))) for a in article.authors if len(tokens(a)) >= 2}


def corresponding_preprint(article, candidate):
    if not article.verified or not candidate.verified or article.metadata_errors or candidate.metadata_errors:
        return False
    # An independently verified preprint is an alternative version, not another
    # publisher's similarly named contribution. Never infer journal equivalence.
    if not candidate.arxiv_id or article.arxiv_id or not article.doi or article.doi.startswith("10.48550/"):
        return False
    authors, other = author_keys(article), author_keys(candidate)
    if len(authors) < 2 or authors != other:
        return False
    if not article.year or not candidate.year or not 0 <= article.year - candidate.year <= 2:
        return False
    left, right = set(tokens(article.title)), set(tokens(candidate.title))
    return len(left & right) >= 10 and len(left & right) / len(left | right) >= .9


def alternatives(article, registry, sources, deadline):
    urls, traces = [], []
    from .tools.fulltext import document_urls
    for candidate in registry.values():
        if candidate.id == article.id or not candidate.arxiv_id:
            continue
        # Cheap candidate check before any registry request. Author metadata is
        # necessary but not sufficient: the candidate is verified independently.
        if len(author_keys(article)) < 2 or author_keys(article) != author_keys(candidate):
            continue
        left, right = set(tokens(article.title)), set(tokens(candidate.title))
        if len(left & right) < 10 or len(left & right) / len(left | right) < .9:
            continue
        checked = candidate.model_copy(deep=True)
        if not checked.verified:
            checked = sources.verify(checked, deadline)
        if corresponding_preprint(article, checked):
            urls.extend(document_urls(checked))
            traces.append({"article_id": candidate.id, "arxiv_id": checked.arxiv_id,
                "relation": "verified_metadata_preprint_alternative", "canonical_merge": False,
                "verification_sources": checked.verification_sources})
    return list(dict.fromkeys(urls)), traces
