# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List

from .opencitations_client import OpenCitationsClient


def _clean_doi(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", "", text, flags=re.I)
    return text.strip().rstrip("./")


def _score(article: Dict[str, Any]) -> float:
    for key in ("bge_reranker_score", "relevance_score", "score"):
        try:
            if article.get(key) is not None:
                return float(article.get(key))
        except Exception:
            pass
    return 0.0


class DeepDiscoveryService:
    """Citation snowballing with a strict network budget."""

    def __init__(self, client: OpenCitationsClient | None = None):
        self.client = client or OpenCitationsClient()
        self.enabled = (
            str(os.getenv("ENNOSCHOLAR_DEEP_DISCOVERY_ENABLED", "1")).lower()
            in {"1", "true", "yes", "on"} and self.client.available
        )
        self.max_seeds = max(1, min(int(os.getenv("ENNOSCHOLAR_DISCOVERY_SEEDS", "3")), 5))
        self.refs_per_seed = max(0, min(int(os.getenv("ENNOSCHOLAR_DISCOVERY_REFS_PER_SEED", "4")), 10))
        self.cites_per_seed = max(0, min(int(os.getenv("ENNOSCHOLAR_DISCOVERY_CITES_PER_SEED", "4")), 10))
        self.max_new_candidates = max(1, min(int(os.getenv("ENNOSCHOLAR_DISCOVERY_MAX_NEW", "18")), 40))
        self.workers = max(1, min(int(os.getenv("ENNOSCHOLAR_DISCOVERY_WORKERS", "6")), 10))

    @staticmethod
    def _exact_match(rows: List[Dict[str, Any]], doi: str) -> Dict[str, Any] | None:
        doi = _clean_doi(doi)
        for row in rows or []:
            if isinstance(row, dict) and not row.get("normalized_error") and _clean_doi(row.get("doi")) == doi:
                return dict(row)
        return None

    def _resolve_metadata(
        self,
        doi: str,
        openalex_search: Callable[[str, int], List[Dict[str, Any]]],
        crossref_search: Callable[[str, int], List[Dict[str, Any]]],
    ) -> Dict[str, Any] | None:
        for resolver_name, resolver in (("openalex", openalex_search), ("crossref", crossref_search)):
            try:
                exact = self._exact_match(resolver(doi, 5), doi)
                if exact:
                    exact["discovered_via"] = "opencitations"
                    exact["metadata_resolver"] = resolver_name
                    return exact
            except Exception:
                continue
        return None

    def discover(
        self,
        ranked_articles: List[Dict[str, Any]],
        *,
        openalex_search: Callable[[str, int], List[Dict[str, Any]]],
        crossref_search: Callable[[str, int], List[Dict[str, Any]]],
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        report = {"enabled": self.enabled, "seed_count": 0, "citation_links_found": 0, "new_candidates_resolved": 0}
        if not self.enabled:
            return [], report

        seeds = sorted(
            [a for a in ranked_articles or [] if isinstance(a, dict) and _clean_doi(a.get("doi"))],
            key=_score, reverse=True,
        )[: self.max_seeds]
        report["seed_count"] = len(seeds)
        if not seeds:
            report["reason"] = "no_seed_with_doi"
            return [], report

        neighbours = []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(seeds))) as executor:
            futures = {
                executor.submit(
                    self.client.neighbours,
                    _clean_doi(seed.get("doi")),
                    references=self.refs_per_seed,
                    citations=self.cites_per_seed,
                ): seed
                for seed in seeds
            }
            for future in as_completed(futures):
                try:
                    neighbours.extend(future.result() or [])
                except Exception:
                    pass

        report["citation_links_found"] = len(neighbours)
        known = {_clean_doi(a.get("doi")) for a in ranked_articles or [] if isinstance(a, dict)}
        unique, seen, relation_by_doi = [], set(), {}
        for row in neighbours:
            doi = _clean_doi(row.get("doi"))
            if not doi or doi in known or doi in seen:
                continue
            seen.add(doi)
            unique.append(doi)
            relation_by_doi[doi] = row
            if len(unique) >= self.max_new_candidates:
                break

        resolved = []
        if unique:
            with ThreadPoolExecutor(max_workers=min(self.workers, len(unique))) as executor:
                futures = {
                    executor.submit(self._resolve_metadata, doi, openalex_search, crossref_search): doi
                    for doi in unique
                }
                for future in as_completed(futures):
                    doi = futures[future]
                    try:
                        article = future.result()
                    except Exception:
                        article = None
                    if not article or not article.get("title"):
                        continue
                    relation = relation_by_doi.get(doi, {})
                    article["citation_relation"] = relation.get("relation")
                    article["citation_seed_doi"] = relation.get("seed_doi")
                    article["deep_discovery_candidate"] = True
                    resolved.append(article)

        report["new_candidates_resolved"] = len(resolved)
        return resolved, report
