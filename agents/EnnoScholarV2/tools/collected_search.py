"""Small offline BM25 windows. Retrieval scores are navigation, never scientific ranks."""
from __future__ import annotations

import base64
import hashlib
import json
import math
from collections import Counter

from ..canonical import ArticleStore, normalized_title, notice
from .compact_results import compact_candidate


def terms(value):
    return normalized_title(value).split()


def search_collected_articles(registry, query, limit=10, exclude_article_ids=None, cursor=None):
    if not isinstance(query, str) or not query.strip() or len(query) > 1500:
        raise ValueError("Une query locale de 1 à 1500 caractères est nécessaire.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 12:
        raise ValueError("La fenêtre locale accepte de un à douze travaux.")
    excluded = exclude_article_ids or []
    if not isinstance(excluded, list) or len(excluded) > 10000 or not all(isinstance(key, str) for key in excluded):
        raise ValueError("Les exclusions doivent être des identifiants de cette session.")
    store = ArticleStore(registry)
    excluded_works = {store.work_id(key) for key in excluded}
    query_terms = set(terms(query))
    if not query_terms:
        raise ValueError("La query locale doit contenir des termes recherchables.")
    documents, frequencies = [], Counter()
    weights = (("title", 3.0), ("abstract", 1.0), ("authors", .7), ("identifiers", 2.0), ("queries", .3))
    for work in store.works:
        article = work.article
        origins = [(version.id, row) for version in work.versions for row in [notice(version), *version.provider_notices]]
        abstracts = list(dict.fromkeys(row.get("abstract", "") for _, row in origins if row.get("abstract")))
        fields = {"title": " ".join(dict.fromkeys(row.get("title", "") for _, row in origins)), "abstract": " ".join(abstracts),
            "authors": " ".join(dict.fromkeys(name for _, row in origins for name in row.get("authors", []))),
            "identifiers": " ".join(value for values in work.identifiers.values() for value in values),
            "queries": " ".join(article.matched_queries)}
        counts = {name: Counter(terms(value)) for name, value in fields.items()}
        frequencies.update({word for counter in counts.values() for word in counter})
        documents.append((work, fields, counts, origins))
    averages = {name: sum(sum(counts[name].values()) for _, _, counts, _ in documents) / max(1, len(documents)) for name, _ in weights}
    results = []
    for work, fields, counts, origins in documents:
        if work.canonical_id in excluded_works:
            continue
        score = 0.0
        for name, weight in weights:
            length = sum(counts[name].values())
            for word in query_terms:
                frequency = counts[name][word]
                if frequency:
                    idf = math.log(1 + (len(documents) - frequencies[word] + .5) / (frequencies[word] + .5))
                    score += weight * idf * frequency * 2.2 / (frequency + 1.2 * (.25 + .75 * length / max(1, averages[name])))
        if score:
            results.append((score, work, origins))
    results.sort(key=lambda row: (-row[0], row[1].canonical_id))
    # Bind the offset to this query, exclusions and searchable corpus contents.
    fingerprint = hashlib.sha256(json.dumps([query, sorted(excluded_works - {None}),
        [(work.canonical_id, fields) for work, fields, _, _ in documents]], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    offset = 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 300:
                raise ValueError
            data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            offset = data["offset"]
            if data["fingerprint"] != fingerprint or type(offset) is not int or not 0 <= offset <= len(results):
                raise ValueError
        except Exception as exc:
            raise ValueError("Cursor invalide ou corpus/query modifiés ; recommencez sans cursor.") from exc
    articles = []
    for _, work, origins in results[offset:offset + limit]:
        row = compact_candidate(work.article)
        row["article_id"] = work.representative_id
        row["canonical_id"] = work.canonical_id
        # Expose the matched abstract region rather than an unrelated opening.
        matched = next(((identifier, origin.get("abstract", "")) for identifier, origin in origins
            if query_terms & set(terms(origin.get("abstract", "")))), (work.representative_id, work.article.abstract))
        source_id, abstract = matched
        row["abstract_source_article_id"] = source_id
        position = next((index for index in range(0, len(abstract), 200)
            if query_terms & set(terms(abstract[index:index + 800]))), 0)
        row["abstract_excerpt"] = abstract[position:position + 800]
        row["abstract_truncated"] = len(abstract) > 800
        articles.append(row)
    next_offset = offset + len(articles)
    next_cursor = base64.urlsafe_b64encode(json.dumps({"fingerprint": fingerprint, "offset": next_offset}).encode()).decode() if next_offset < len(results) else None
    return {"query": query, "articles": articles, "matched_work_count": len(results), "next_cursor": next_cursor,
        "canonical_work_count": len(store.works), "retrieval_only": True,
        "selection": "Local BM25 retrieval; one canonical work per candidate; scientific judgment belongs to ScholarChatAgent."}
