# -*- coding: utf-8 -*-
from __future__ import annotations

"""
query_builder.py — EnnoScholar V2.1

Queries depuis ScientificIntent source-evidence-first.
Évite les termes génériques type question/qualification.
"""

from typing import Any, Dict, List

from .utils import clean_text, dedupe_keep_order, norm, tokenize


BAD_QUERY_TOKENS = {
    "question", "qualification", "permet", "permet-elle", "maitrise", "maîtrise",
    "systeme", "système", "solution", "possible", "implicite", "probable",
    "non", "sous", "contrainte", "contraintes",
}


def _filtered_words(text: Any) -> List[str]:
    words = []
    for t in tokenize(text):
        if norm(t) in BAD_QUERY_TOKENS:
            continue
        if t not in words:
            words.append(t)
    return words


def _query(parts: List[str], max_words: int = 10) -> str:
    words = []
    seen = set()
    for p in parts:
        for w in _filtered_words(p):
            nw = norm(w)
            if nw and nw not in seen:
                seen.add(nw)
                words.append(w)
            if len(words) >= max_words:
                return clean_text(" ".join(words), 220)
    return clean_text(" ".join(words), 220)


def build_queries_from_intent(intent: Dict[str, Any], max_queries: int = 6) -> List[Dict[str, Any]]:
    obj = clean_text(intent.get("technical_object"), 160)
    phen = clean_text(intent.get("phenomenon"), 160)
    prob = clean_text(intent.get("scientific_problem"), 220)
    constraints = intent.get("constraints") or []
    methods = intent.get("methods") or []
    key_terms = intent.get("key_terms_en") or intent.get("key_terms_fr") or []

    q = []

    def add(query: str, kind: str):
        query = clean_text(query, 220)
        if len(query) < 6:
            return
        if query.lower() in {x["query"].lower() for x in q}:
            return
        q.append({"query": query, "kind": kind})

    add(_query([obj, phen], max_words=10), "object_phenomenon")
    add(_query([prob], max_words=10), "scientific_problem")

    # Ajouter uncertainty seulement si query assez technique.
    if len(_filtered_words(obj + " " + phen)) >= 4:
        add(_query([obj, phen, "technical uncertainty"], max_words=11), "technical_uncertainty")

    if constraints:
        add(_query([obj, phen, " ".join(constraints[:2])], max_words=10), "constraints")

    if methods:
        add(_query([obj, phen, " ".join(methods[:3])], max_words=10), "methods")

    add(_query([" ".join(key_terms[:10])], max_words=10), "key_terms")

    if len(_filtered_words(obj + " " + phen)) >= 4:
        add(_query([obj, phen, "state of the art"], max_words=11), "state_of_art")

    return q[:max_queries]


def attach_queries_to_intent(intent: Dict[str, Any], max_queries: int = 6) -> Dict[str, Any]:
    out = dict(intent or {})
    out["search_queries"] = build_queries_from_intent(out, max_queries=max_queries)
    return out
