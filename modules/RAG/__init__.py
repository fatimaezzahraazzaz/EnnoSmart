"""
modules/RAG/__init__.py
RAG v2 structured — exports propres
"""

from modules.RAG.rag_pipeline import (
    RAGPipeline,
    build_rag_chunks_from_nlp_json,
    slugify_org,
    force_organisme_metadata,
    build_organisme_filter,
)

__all__ = [
    "RAGPipeline",
    "build_rag_chunks_from_nlp_json",
    "slugify_org",
    "force_organisme_metadata",
    "build_organisme_filter",
]
