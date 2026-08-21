# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import urllib.error
from typing import Any, Dict, List
from .external_source_base import cache_path, encode_params, get_json, normalized_error, read_cache, safe, write_cache, merge_fresh_with_cache, fallback_from_cache

GITHUB_SEARCH = "https://api.github.com/search/repositories"


class GitHubClient:
    def __init__(self, token: str | None = None, timeout: int = 10, max_retries: int = 1, cache_ttl_days: int = 14):
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self.timeout = timeout; self.max_retries = max_retries; self.cache_ttl_days = cache_ttl_days

    def search_repositories(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        query = safe(query, 250)
        if not query: return []
        limit = max(1, min(int(limit or 10), 30))
        path = cache_path("github", query, limit)
        cached = read_cache(path, self.cache_ttl_days)
        url = encode_params(GITHUB_SEARCH, {"q": query, "per_page": limit, "sort": "stars", "order": "desc"})
        headers = {"Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28", "User-Agent":"EnnoSmart-EnnoScholar/3.2"}
        if self.token: headers["Authorization"] = f"Bearer {self.token}"
        try:
            try:
                data = get_json(url, headers=headers, timeout=self.timeout, retries=self.max_retries)
            except Exception as first_exc:
                # Ne retirer le token que si GitHub refuse réellement l'authentification.
                # Pour 502/503/504, get_json a déjà effectué les retries/backoff :
                # on conserve donc l'authentification et on laisse le cache prendre le relais.
                status = int(getattr(first_exc, "code", 0) or 0)
                auth_error = isinstance(first_exc, urllib.error.HTTPError) and status in {401, 403}
                if not self.token or not auth_error:
                    raise
                anonymous_headers = {
                    key: value for key, value in headers.items()
                    if key != "Authorization"
                }
                data = get_json(
                    url,
                    headers=anonymous_headers,
                    timeout=self.timeout,
                    retries=self.max_retries,
                )
            rows = data.get("items") or []
            out = [self.normalize(x, query) for x in rows if isinstance(x,dict)]
            combined = merge_fresh_with_cache(out, cached, limit, "github")
            write_cache(path, combined)
            return combined
        except Exception as exc:
            stale = read_cache(path,3650)
            fallback = fallback_from_cache(cached or stale, "github", exc)
            return fallback if fallback else [normalized_error("github", query, exc)]

    @staticmethod
    def normalize(item: Dict[str, Any], query: str) -> Dict[str, Any]:
        topics = item.get("topics") or []
        return {
            "source":"github", "source_type":"github_repository", "evidence_level":"technical_support",
            "artifact_type":"software_repository", "query":query,
            "artifact_id":safe(item.get("full_name") or item.get("id"),260), "paper_id":"github:"+safe(item.get("full_name") or item.get("id"),260),
            "title":safe(item.get("full_name") or item.get("name"),500), "abstract":safe(item.get("description"),4000),
            "year":int(str(item.get("created_at") or "")[:4]) if str(item.get("created_at") or "")[:4].isdigit() else None,
            "venue":"GitHub", "url":safe(item.get("html_url"),1000), "doi":"", "authors":[safe((item.get("owner") or {}).get("login"),180)] if isinstance(item.get("owner"),dict) else [],
            "documentation_url":safe(item.get("homepage"),1000),
            "repository_full_name":safe(item.get("full_name"),260),
            "citation_count":0, "fields_of_study":topics, "stars":int(item.get("stargazers_count") or 0), "forks":int(item.get("forks_count") or 0),
            "language":safe(item.get("language"),80), "license":safe((item.get("license") or {}).get("spdx_id"),80) if isinstance(item.get("license"),dict) else "",
            "tag":"Artefact technique", "reason":"Repository proposé comme preuve d’implémentation ou de reproductibilité ; il ne valide pas seul l’état de l’art scientifique.",
        }
