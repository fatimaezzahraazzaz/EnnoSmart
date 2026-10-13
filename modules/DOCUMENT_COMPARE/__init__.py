# -*- coding: utf-8 -*-
from __future__ import annotations

from .document_compare import (
    auto_compare_fulltext_pairs,
    compare_documents,
    compare_pair_to_report,
    discover_comparable_file_pairs,
    discover_comparable_fulltext_pairs,
    load_text_from_file,
    load_text_from_fulltext_file,
    auto_compare_project_pairs,
)

__all__ = [
    "auto_compare_fulltext_pairs",
    "auto_compare_project_pairs",
    "compare_documents",
    "compare_pair_to_report",
    "discover_comparable_file_pairs",
    "discover_comparable_fulltext_pairs",
    "load_text_from_file",
    "load_text_from_fulltext_file",
]
