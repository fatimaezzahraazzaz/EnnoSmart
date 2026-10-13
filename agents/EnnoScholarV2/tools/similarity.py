from __future__ import annotations

from ..models import Article
from ..canonical import canonical_works
from .scientific_search import ScientificSearch, deduplicate


def find_similar_articles(article_or_topic: str, search: ScientificSearch, *, article: Article | None = None,
    queries: list[str] | None = None, limit: int = 10) -> dict:
    """Use queries chosen by the conversational model; no forced analysis pipeline."""
    effective = [q.strip()[:1500] for q in (queries or [article.title if article else article_or_topic]) if q.strip()][:4]
    if not effective:
        raise ValueError("Indiquez un article ou un sujet scientifique.")
    candidates, warnings = [], []
    for query in effective:
        result = search.search_scientific(query, limit)
        candidates.extend(Article.model_validate(row) for row in result["articles"])
        warnings.extend(result["warnings"])
    candidates = deduplicate(candidates, 80)
    works = canonical_works([*candidates, *([article] if article else [])])
    recommended = [work for work in works if not article or article.id not in work.article.related_article_ids][:max(1, min(limit, 20))]
    return {"queries": effective, "articles": [work.article.model_dump() for work in recommended],
        "all_articles": [version.model_dump() for work in recommended for version in work.versions],
        "warnings": warnings, "ranking_note": "Candidats à comparer selon le contexte, sans bonus automatique pour l'open access."}
