# -*- coding: utf-8 -*-
r"""
modules/RAG/retriever.py — EnnoSmart RAG No-LLM V4

Recherche sémantique + reranking léger par métadonnées.
Pas de LLM.
"""

from __future__ import annotations

import re
from typing import Any, Optional

DEFAULT_TOP_K = 5
DEFAULT_MIN_SCORE = -1.0


def _norm(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x or "").lower()).strip()


def detect_intent(query: str) -> str:
    q = _norm(query)
    if any(w in q for w in ["verrou", "incertitude", "difficulté", "difficulte", "risque", "limite"]):
        return "eligibility"
    if any(w in q for w in ["objectif", "but", "vise", "visé"]):
        return "summary"
    if any(w in q for w in ["méthode", "methode", "protocole", "démarche", "demarche", "travaux"]):
        return "methods"
    if any(w in q for w in ["résultat", "resultat", "performance", "métrique", "metrique"]):
        return "metrics"
    if any(w in q for w in ["état de l'art", "etat de l'art", "article", "publication"]):
        return "scholar"
    return "qa"


def _terms(query: str) -> list[str]:
    stop = {
        "quel", "quels", "quelle", "quelles", "sont", "est", "les", "des",
        "une", "dans", "pour", "avec", "projet", "document", "donne", "moi",
        "principal", "principaux", "technique", "techniques",
    }
    out, seen = [], set()
    for t in re.findall(r"[\wÀ-ÿ\-]+", _norm(query)):
        if len(t) >= 3 and t not in stop and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _meta_text(meta: dict) -> str:
    vals = []
    for k, v in (meta or {}).items():
        vals.append(f"{k}: {v}")
    return _norm(" ".join(vals))


class Retriever:
    def __init__(
        self,
        store,
        embedder,
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
        fetch_multiplier: int = 8,
    ):
        self.store = store
        self.embedder = embedder
        self.top_k = top_k
        self.min_score = min_score
        self.fetch_multiplier = fetch_multiplier

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
        filter_meta: Optional[dict] = None,
        filter_mode: str = "contains",
        intent: Optional[str] = None,
        deduplicate: bool = True,
    ) -> list[dict]:
        if not query or not query.strip():
            return []
        if self.store.is_empty:
            return []

        k = top_k or self.top_k
        threshold = self.min_score if min_score is None else min_score
        intent = intent or detect_intent(query)

        qvec = self.embedder.embed_query(query)
        raw = self.store.search(
            query_vector=qvec,
            top_k=max(k, 12),
            filter_meta=filter_meta,
            filter_mode=filter_mode,
            fetch_multiplier=self.fetch_multiplier,
        )

        scored = []
        for r in raw:
            meta = r.get("metadata") or {}
            content = r.get("content") or ""
            bonus = self._bonus(query, content, meta, intent)
            final = float(r.get("score", 0.0)) + bonus
            if final < threshold:
                continue
            rr = dict(r)
            rr["metadata_bonus"] = round(bonus, 4)
            rr["final_score"] = round(final, 4)
            rr["intent"] = intent
            scored.append(rr)

        scored.sort(key=lambda x: x.get("final_score", x.get("score", 0)), reverse=True)
        if deduplicate:
            scored = self._dedup(scored)
        for i, r in enumerate(scored[:k]):
            r["rank"] = i + 1
        return scored[:k]

    def search_multi(self, queries: list[str], top_k: Optional[int] = None, filter_meta: Optional[dict] = None, intent: Optional[str] = None) -> list[dict]:
        merged = {}
        for q in queries:
            for r in self.search(q, top_k=top_k or self.top_k, filter_meta=filter_meta, intent=intent, deduplicate=False):
                cid = r.get("chunk_id")
                if cid not in merged or r.get("final_score", 0) > merged[cid].get("final_score", 0):
                    merged[cid] = r
        out = list(merged.values())
        out.sort(key=lambda x: x.get("final_score", x.get("score", 0)), reverse=True)
        return out[: (top_k or self.top_k)]

    def _bonus(self, query: str, content: str, meta: dict, intent: str) -> float:
        mt = _meta_text(meta)
        ct = _norm(content)
        bonus = 0.0

        for t in _terms(query):
            if t in mt:
                bonus += 0.06
            if t in ct:
                bonus += 0.025

        field = str(meta.get("field_name") or "")
        role = str(meta.get("role_final") or "")
        ctype = str(meta.get("chunk_source_type") or "")

        if intent == "eligibility":
            if field in {"verrous_prioritaires", "verrous_globaux"}:
                bonus += 0.20
            if role in {"verrou", "limite"}:
                bonus += 0.12
        elif intent == "summary":
            if field in {"objectif_global", "objectifs_locaux"}:
                bonus += 0.18
            if role == "objectif":
                bonus += 0.08
        elif intent == "methods":
            if field in {"demarche_rd_globale", "methodes_protocoles"}:
                bonus += 0.18
            if role == "methode":
                bonus += 0.08
        elif intent == "metrics":
            if field in {"resultats_cles_globaux", "resultats_importants", "parametres_metriques"}:
                bonus += 0.18
            if role in {"resultat", "parametre"}:
                bonus += 0.08
        elif intent == "scholar":
            if field == "etat_art":
                bonus += 0.18

        if ctype in {"consultant_card", "field_card", "section_cir", "evidence_card"}:
            bonus += 0.04

        return min(bonus, 0.50)

    @staticmethod
    def _dedup(results: list[dict]) -> list[dict]:
        kept, seen = [], []
        for r in results:
            text = _norm(r.get("content", ""))[:450]
            tokens = set(text.split())
            duplicate = False
            for old in seen:
                old_tokens = set(old.split())
                if tokens and old_tokens:
                    sim = len(tokens & old_tokens) / max(1, len(tokens | old_tokens))
                    if sim > 0.88:
                        duplicate = True
                        break
            if not duplicate:
                kept.append(r)
                seen.append(text)
        return kept
