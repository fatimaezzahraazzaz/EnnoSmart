# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Any, Dict, List
from .external_source_base import cache_path, encode_params, get_json, normalized_error, read_cache, safe, write_cache, merge_fresh_with_cache, fallback_from_cache

HF_MODELS = "https://huggingface.co/api/models"
HF_DATASETS = "https://huggingface.co/api/datasets"


class HuggingFaceClient:
    def __init__(self, token: str | None = None, timeout: int = 10, max_retries: int = 1, cache_ttl_days: int = 14):
        self.token = token or os.getenv("HF_TOKEN", "")
        self.timeout=timeout; self.max_retries=max_retries; self.cache_ttl_days=cache_ttl_days

    def search_artifacts(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        query=safe(query,250)
        if not query:return []
        limit=max(1,min(int(limit or 10),30))
        path=cache_path("huggingface",query,limit)
        cached=read_cache(path,self.cache_ttl_days)
        headers={"Accept":"application/json","User-Agent":"EnnoSmart-EnnoScholar/3.2"}
        if self.token: headers["Authorization"]=f"Bearer {self.token}"
        out=[]
        try:
            for endpoint,kind in [(HF_MODELS,"model"),(HF_DATASETS,"dataset")]:
                data=get_json(encode_params(endpoint,{"search":query,"limit":max(1,limit//2)}),headers=headers,timeout=self.timeout,retries=self.max_retries)
                if isinstance(data,list):
                    out.extend(self.normalize(x,query,kind) for x in data if isinstance(x,dict))
            combined = merge_fresh_with_cache(out[:limit], cached, limit, "huggingface")
            write_cache(path, combined)
            return combined
        except Exception as exc:
            stale=read_cache(path,3650)
            fallback = fallback_from_cache(cached or stale, "huggingface", exc)
            return fallback if fallback else [normalized_error("huggingface", query, exc)]

    @staticmethod
    def normalize(item:Dict[str,Any],query:str,kind:str)->Dict[str,Any]:
        ident=safe(item.get("modelId") or item.get("id"),300)
        tags=item.get("tags") or []
        return {
            "source":"huggingface", "source_type":"model_or_dataset", "evidence_level":"technical_or_experimental_support",
            "artifact_type":kind, "query":query, "artifact_id":ident, "paper_id":f"huggingface:{kind}:{ident}",
            "title":ident, "abstract":safe(item.get("description") or " ".join(map(str,tags[:20])),4000),
            "year":None, "venue":"Hugging Face Hub", "url":f"https://huggingface.co/{'datasets/' if kind=='dataset' else ''}{ident}",
            "doi":"", "authors":[ident.split('/')[0]] if '/' in ident else [], "citation_count":0, "fields_of_study":tags,
            "downloads":int(item.get("downloads") or 0), "likes":int(item.get("likes") or 0), "tag":"Artefact technique",
            "reason":"Modèle ou dataset proposé comme support expérimental ; il ne constitue pas seul une preuve scientifique.",
        }
