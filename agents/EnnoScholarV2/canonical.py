"""Identity and canonical views only. No scientific score, network or record deletion."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from .models import Article


def normalize_doi(value: str) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).lower().rstrip(".,;")


def normalized_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[\W_]+", " ", value).strip()


def arxiv_key(value: str) -> str:
    value = re.sub(r"^(?:https?://arxiv\.org/(?:abs|pdf|html)/|arxiv:\s*)", "", value, flags=re.I)
    return re.sub(r"v\d+$", "", value.removesuffix(".pdf")).casefold()


def strong_keys(article: Article) -> set[tuple[str, str]]:
    doi = normalize_doi(article.doi)
    arxiv = arxiv_key(article.arxiv_id)
    if doi.startswith("10.48550/arxiv."):
        arxiv = arxiv or arxiv_key(doi.split("arxiv.", 1)[1])
    keys = {(kind, value) for kind, value in (("doi", doi), ("arxiv", arxiv)) if value}
    keys.update((kind, value.casefold()) for kind, value in article.external_ids.items() if value)
    # Alternative provider IDs remain usable after their notices are merged.
    for row in article.provider_notices:
        keys.update((kind, value.casefold()) for kind, value in row.get("external_ids", {}).items() if value)
    return keys


def identity_keys(article: Article) -> list[tuple[str, str]]:
    return sorted(strong_keys(article)) + ([("title", normalized_title(article.title))] if normalized_title(article.title) else [])


def article_id(article: Article) -> str:
    identity = normalize_doi(article.doi) or arxiv_key(article.arxiv_id)
    if not identity:
        identity = normalized_title(article.title)
        # Weak identities must not overwrite different works with the same title.
        if article.authors or article.year:
            identity += "|" + "|".join(sorted(normalized_title(name) for name in article.authors)) + "|" + str(article.year or "")
    return "art_" + hashlib.sha256(identity.encode()).hexdigest()[:20]


def version_kind(article: Article) -> str:
    if normalize_doi(article.doi).startswith("10.48550/arxiv.") or (article.source == "arxiv" and not article.doi):
        return "preprint"
    if article.doi:
        return "publication"
    return "preprint" if article.arxiv_id else "provider_notice"


def authors_compatible(left: Article, right: Article) -> bool:
    if not left.authors or not right.authors:
        return True
    return bool({normalized_title(name) for name in left.authors} & {normalized_title(name) for name in right.authors})


def title_compatible(left: Article, right: Article) -> bool:
    # Exact strong identity/known associations are handled separately. Titles alone
    # must never override contradictory publication or preprint identifiers.
    ld, rd = normalize_doi(left.doi), normalize_doi(right.doi)
    if ld and rd and ld != rd and not (ld.startswith("10.48550/arxiv.") ^ rd.startswith("10.48550/arxiv.")):
        return False
    la = {value for kind, value in strong_keys(left) if kind == "arxiv"}
    ra = {value for kind, value in strong_keys(right) if kind == "arxiv"}
    if la and ra and not la & ra:
        return False
    return authors_compatible(left, right) and (not left.year or not right.year or abs(left.year - right.year) <= 2)


def same_record(left: Article, right: Article) -> bool:
    """Merge provider notices of one version; keep different version DOIs."""
    ld, rd = normalize_doi(left.doi), normalize_doi(right.doi)
    if ld and rd:
        return ld == rd
    if version_kind(left) != version_kind(right) and {version_kind(left), version_kind(right)} == {"publication", "preprint"}:
        return False
    if strong_keys(left) & strong_keys(right):
        return True
    return normalized_title(left.title) == normalized_title(right.title) and title_compatible(left, right)


def notice(article: Article) -> dict:
    return article.model_dump(include={"title", "authors", "year", "doi", "arxiv_id", "url", "pdf_url",
        "source", "sources", "abstract", "matched_queries", "external_ids"})


def preserve_notices(article: Article) -> None:
    if not article.provider_notices:
        article.provider_notices = [notice(article)]


def merge_metadata(target: Article, incoming: Article) -> Article:
    """Keep origin notices, alternative URLs and identifiers before merging fields."""
    preserve_notices(target)
    preserve_notices(incoming)
    for row in incoming.provider_notices:
        if row not in target.provider_notices:
            target.provider_notices.append(row)
    target.work_urls = list(dict.fromkeys([*target.work_urls, *incoming.work_urls,
        *[url for url in (target.url, target.pdf_url, incoming.url, incoming.pdf_url) if url]]))
    for field in ("authors", "year", "url", "doi", "arxiv_id", "pdf_url"):
        if not getattr(target, field):
            setattr(target, field, getattr(incoming, field))
    target.external_ids = {**incoming.external_ids, **target.external_ids}
    target.sources = list(dict.fromkeys([*target.sources, *incoming.sources]))
    target.matched_queries = list(dict.fromkeys([*target.matched_queries, *incoming.matched_queries]))
    target.citation_count = max(target.citation_count, incoming.citation_count)
    if len(incoming.abstract) > len(target.abstract):
        target.abstract = incoming.abstract
    target.access_level = "abstract_only" if target.abstract else "metadata_only"
    return target


def work_title(value: str) -> str:
    if ":" in value and len(value.split(":", 1)[0].split()) <= 2:
        value = value.split(":", 1)[1]
    return normalized_title(value)


def grouped_articles(articles: list[Article]) -> list[list[Article]]:
    """Union all strong/known links before conservative title associations."""
    parents = list(range(len(articles)))
    members = {index: [index] for index in parents}
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    def union(left, right):
        left, right = root(left), root(right)
        if left != right:
            left, right = min(left, right), max(left, right)
            parents[right] = left
            members[left].extend(members.pop(right))
    exact, identifiers, associations = {}, {}, {}
    for index, article in enumerate(articles):
        if article.id:
            if article.id in identifiers:
                union(index, identifiers[article.id])
            identifiers.setdefault(article.id, index)
        for key in strong_keys(article):
            if key in exact:
                union(index, exact[key])
            exact[key] = index
        if article.canonical_article_id:
            if article.canonical_article_id in associations:
                union(index, associations[article.canonical_article_id])
            associations[article.canonical_article_id] = index
    for index, article in enumerate(articles):
        for key in [article.canonical_article_id, *article.related_article_ids]:
            if key in identifiers:
                union(index, identifiers[key])
    title_index, words_index = {}, {}
    for index, article in enumerate(articles):
        title = work_title(article.title)
        words = set(title.split())
        candidates = list(title_index.get(title, []))
        if len(words) >= 8:
            for word in sorted(words, key=lambda value: (-len(value), value))[:2]:
                candidates.extend(words_index.get(word, []))
        for other_index in dict.fromkeys(candidates):
            left, right = root(index), root(other_index)
            if left == right:
                continue
            other = articles[other_index]
            if not title_compatible(article, other):
                continue
            known = work_title(other.title)
            if title != known:
                known_words = set(known.split())
                if len(words & known_words) / max(1, len(words | known_words)) < .8:
                    continue
                if {word for word in words if any(char.isdigit() for char in word)} != {word for word in known_words if any(char.isdigit() for char in word)}:
                    continue
                similarity = SequenceMatcher(None, title, known).ratio()
                common_author = bool(set(article.authors) & set(other.authors))
                if similarity < (.90 if common_author else .94):
                    continue
            if all(title_compatible(articles[a], articles[b]) for a in members[left] for b in members[right]):
                union(left, right)
        title_index.setdefault(title, []).append(index)
        for word in words:
            words_index.setdefault(word, []).append(index)
    return [[articles[index] for index in sorted(indices)] for indices in members.values()]


@dataclass
class CanonicalWork:
    canonical_id: str
    representative_id: str
    article: Article
    versions: list[Article]
    identifiers: dict[str, list[str]]


def canonical_works(articles: list[Article]) -> list[CanonicalWork]:
    works = []
    for group in grouped_articles(articles):
        ids = [article.id for article in group]
        representative = min(group, key=lambda article: (version_kind(article) != "publication", ids.index(article.id)))
        established = [article.canonical_article_id for article in group if article.canonical_article_id in ids]
        canonical = established[0] if established else representative.id
        view = representative.model_copy(deep=True)
        view.id = canonical
        view.canonical_article_id = canonical
        view.related_article_ids = ids
        view.sources = list(dict.fromkeys(source for article in group for source in article.sources or [article.source] if source))
        view.matched_queries = list(dict.fromkeys(query for article in group for query in article.matched_queries))
        view.work_urls = list(dict.fromkeys(url for article in group for url in [*article.work_urls, article.url,
            article.pdf_url, "https://doi.org/" + article.doi if article.doi else "",
            "https://arxiv.org/abs/" + article.arxiv_id if article.arxiv_id else ""] if url))
        view.abstract = max((article.abstract for article in group), key=len)
        view.access_level = max((article.access_level for article in group), key={"metadata_only": 0, "abstract_only": 1, "fulltext": 2}.get)
        view.citation_count = max(article.citation_count for article in group)
        view.provider_notices = []  # Complete version records stay backend, never in the view.
        keys = {}
        for article in group:
            for kind, value in strong_keys(article):
                keys.setdefault(kind, []).append(value)
        works.append(CanonicalWork(canonical, representative.id, view, group,
            {kind: list(dict.fromkeys(values)) for kind, values in keys.items()}))
    return works


class ArticleStore:
    """RAM projection above StoredArticle; preserves every original stored payload."""
    def __init__(self, registry):
        self.registry = registry
        self.works = canonical_works([record.article for record in registry.values()])
        self.by_id = {identifier: work for work in self.works for identifier in [work.canonical_id, *work.article.related_article_ids]}
        for identifier, record in registry.items():
            if record.article.id in self.by_id:
                self.by_id[identifier] = self.by_id[record.article.id]

    def annotate(self):
        for work in self.works:
            for article in work.versions:
                article.canonical_article_id = work.canonical_id
                article.related_article_ids = work.article.related_article_ids
                article.work_urls = work.article.work_urls

    def work_id(self, identifier):
        work = self.by_id.get(identifier)
        return work.canonical_id if work else None

    def version_record_count(self):
        return len(self.registry)

    def version_count(self):
        return sum(max(1, len(record.article.provider_notices)) for record in self.registry.values())

    def payloads(self, canonical_id):
        """Version-specific fulltexts and analyses, accessible only on the backend."""
        work = self.by_id[canonical_id]
        return {article.id: self.registry[article.id] for article in work.versions}
