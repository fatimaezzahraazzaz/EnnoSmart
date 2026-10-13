"""Small provider-order views; scientific judgment belongs to the chat model."""
from __future__ import annotations

from ..models import Article
from ..canonical import canonical_works, identity_keys, same_record
from .scientific_selection import work_groups


def compact_candidate(article: Article) -> dict:
    return {"article_id": article.id, "canonical_id": article.canonical_article_id or article.id, "title": article.title[:500], "year": article.year,
        "authors": article.authors[:5], "sources": article.sources or [article.source],
        "doi": article.doi, "arxiv_id": article.arxiv_id, "citation_count": article.citation_count,
        "matched_queries": [q[:250] for q in article.matched_queries[:8]],
        "access_level": article.access_level, "abstract_excerpt": article.abstract[:800]}


def compact_results(articles: list[Article], limit: int = 8, *, buckets: list[list[Article]] | None = None) -> list[dict]:
    """Round robin in native provider order, with a canonical view per work.

    Callers supply buckets interleaved by query/source. No lexical or scientific
    score, no LLM call, no selection over the accumulated session registry.
    """
    limit = max(1, min(limit, 12))
    works = canonical_works(articles)
    views = [work.article for work in works]
    by_id = {identifier: work.article for work in works for identifier in work.article.related_article_ids}
    index = {}
    for work in works:
        for version in work.versions:
            for key in identity_keys(version):
                index.setdefault(key, []).append((work.article, version))
    def find_view(article):
        if article.id in by_id:
            return by_id[article.id]
        return next((view for key in identity_keys(article) for view, version in index.get(key, []) if same_record(article, version)), None)
    ordered_buckets = []
    for bucket in buckets or [articles]:
        ordered, seen = [], set()
        for article in bucket:
            view = find_view(article)
            if view and view.id not in seen:
                ordered.append(view); seen.add(view.id)
        if ordered:
            ordered_buckets.append(iter(ordered))
    selected, seen = [], set()
    while ordered_buckets and len(selected) < limit:
        remaining = []
        for bucket in ordered_buckets:
            for view in bucket:
                if view.id not in seen:
                    selected.append(view); seen.add(view.id)
                    remaining.append(bucket)
                    break
            if len(selected) == limit:
                break
        ordered_buckets = remaining
    return [compact_candidate(view) for view in selected]
