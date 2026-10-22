"""Discovery and ranking metrics against independent graded judgments (0-3).

Pure functions: no EnnoScholar import, no model, no network. A work's key is its DOI,
else its arXiv identifier, else its normalised title (the judgments file uses the same).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

JUDGMENTS = Path(__file__).with_name("discovery_judgments.json")
RELEVANT = 2          # grade >= 2: a work the need is really about (3) or a strong, specific contribution (2)


def work_key(article):
    if article.get("doi"):
        return "doi:" + article["doi"].lower()
    if article.get("arxiv_id"):
        return "arxiv:" + re.sub(r"v\d+$", "", article["arxiv_id"].lower())
    return "title:" + re.sub(r"[^a-z0-9]+", " ", (article.get("title") or "").lower()).strip()


def load(domain):
    return json.loads(JUDGMENTS.read_text(encoding="utf-8"))["domains"][domain]


def grades_of(articles, judgments):
    """grade per article id; a notice absent from the judgments counts as not assessed (None)."""
    return {aid: (judgments.get(work_key(a)) or {}).get("grade") for aid, a in articles.items()}


def dedupe(ids, articles):
    seen, out = set(), []
    for aid in ids:
        key = work_key(articles[aid])
        if key not in seen:
            seen.add(key)
            out.append(aid)
    return out


def precision_at(ids, grade, k=5):
    top = ids[:k]
    return sum((grade.get(aid) or 0) >= RELEVANT for aid in top) / k if top else 0.0


def ndcg_at(ids, grade, pool_grades, k=5):
    dcg = sum(((2 ** (grade.get(aid) or 0)) - 1) / math.log2(i + 2) for i, aid in enumerate(ids[:k]))
    ideal = sorted((g or 0 for g in pool_grades), reverse=True)[:k]
    idcg = sum(((2 ** g) - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def recall_at(ids, grade, relevant_total, k=20):
    found = sum((grade.get(aid) or 0) >= RELEVANT for aid in ids[:k])
    return found / relevant_total if relevant_total else 0.0
