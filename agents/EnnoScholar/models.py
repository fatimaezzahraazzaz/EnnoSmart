# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class ScientificIntent:
    verrou_id: str
    verrou_title: str
    scientific_problem: str
    technical_object: str = ""
    phenomenon: str = ""
    constraints: List[str] = field(default_factory=list)
    methods: List[str] = field(default_factory=list)
    key_terms_fr: List[str] = field(default_factory=list)
    key_terms_en: List[str] = field(default_factory=list)
    search_queries: List[Dict[str, Any]] = field(default_factory=list)
    source_basis: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Paper:
    source: str
    paper_id: str
    title: str
    abstract: str = ""
    year: Optional[int] = None
    venue: str = ""
    url: str = ""
    doi: str = ""
    authors: List[str] = field(default_factory=list)
    citation_count: int = 0
    query: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
