"""Intent-driven metadata triage. No subject vocabulary or benchmark identities."""
from __future__ import annotations

import math
import re
import unicodedata
from functools import lru_cache

from ..models import Article, SearchIntent


@lru_cache(maxsize=1024)
def tokens(value: str) -> tuple[str, ...]:
    value = unicodedata.normalize("NFKD", value).casefold()
    words = re.findall(r"[a-z0-9]+", "".join(char for char in value if not unicodedata.combining(char)))
    result = []
    for word in words:
        for suffix in ("ions", "ion", "ing", "ed", "es", "s"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                word = word[:-len(suffix)]
                break
        if word.endswith("e") and len(word) > 5:
            word = word[:-1]
        result.append(word)
    return tuple(result)


def matches(value: str, terms: list[str]) -> float:
    words = set(tokens(value))
    return sum(bool(parts := tokens(term)) and set(parts) <= words for term in terms) / max(1, len(terms))


def relation(value: str, targets: list[str], operations: list[str]) -> bool:
    # Require the object and operation in the same short clause, not independent
    # words anywhere in a long abstract. Negated comparisons are not the objective.
    target_sets = [set(tokens(term)) for term in targets]
    operation_sets = [set(tokens(term)) for term in operations]
    if not target_sets or not operation_sets:
        return False
    for clause in re.split(r"[.;!?\n]", value):
        if re.search(r"\b(?:not|without|unlike|neither|sans|contrairement)\b", clause, re.I):
            continue
        words = tokens(clause)
        for start in range(max(1, len(words))):
            window = set(words[start:start + 18])
            if any(parts and parts <= window for parts in target_sets) and any(parts and parts <= window for parts in operation_sets):
                return True
    return False


def rank_articles(articles: list[Article], queries: list[str], intent: SearchIntent | dict | None = None) -> list[Article]:
    """Re-score the whole corpus for the current request; availability is ignored."""
    intent = SearchIntent.model_validate(intent) if intent is not None else None
    query_words = set(tokens(" ".join(queries)))
    for article in articles:
        title_words, abstract_words = set(tokens(article.title)), set(tokens(article.abstract))
        lexical = (2 * len(query_words & title_words) + len(query_words & abstract_words)) / max(1, len(query_words))
        coverage = len(set(article.matched_queries) & set(queries)) / max(1, len(queries))
        priority, tag, explanation = 0.0, "CONNEXE", "Recouvrement lexical ; intention détaillée absente."
        if intent:
            title = article.title
            content = title + ". " + article.abstract
            method = not intent.method_terms or bool(matches(content, intent.method_terms))
            primary = relation(title, intent.target_terms, intent.primary_operations)
            secondary = relation(title, intent.target_terms, intent.secondary_operations)
            adjacent = bool(matches(title, intent.adjacent_operations))
            if primary and method:
                priority, tag = 3.0, "DIRECT"
                explanation = "Objet et objectif principal liés dans le titre ; méthode demandée repérée dans les métadonnées."
            elif secondary and method:
                priority, tag = 2.0, "DIRECT"
                explanation = "Objet et objectif secondaire liés dans le titre ; méthode demandée repérée."
            elif not adjacent and method and relation(article.abstract, intent.target_terms, intent.primary_operations):
                priority, tag = 2.5, "DIRECT"
                explanation = "Objet et objectif principal liés dans une clause de l'abstract."
            elif not adjacent and method and relation(article.abstract, intent.target_terms, intent.secondary_operations):
                priority, tag = 1.5, "DIRECT"
                explanation = "Objectif secondaire lié à l'objet dans l'abstract."
            else:
                explanation = "Objet ou objectif différent, relation non démontrée par la notice, ou méthode demandée non repérée."
            if priority and re.search(r"\b(?:survey|review|overview|revue|synthese)\b", title, re.I):
                tag = "FONDAMENTAL"
            lexical += 2 * matches(title, intent.target_terms) + matches(content, intent.method_terms)
            lexical += 2 * matches(content, intent.context_terms)
        article.relevance_score = round(100 * priority + lexical + 0.05 * coverage + 0.005 * math.log1p(article.citation_count), 6)
        article.retrieval_score = article.relevance_score
        article.relevance_tag, article.ranking_reason = tag, explanation
    return sorted(articles, key=lambda article: (-article.relevance_score, article.title.casefold(), article.id))
