# -*- coding: utf-8 -*-
"""
modules.RAG — EnnoSmart RAG V13.3.1

Compatibilité avec le nouveau RAG sans LLM.
"""

try:
    
    from .rag_pipeline_v13 import (build_rag_index_from_nlp, build_rag_index_from_nlp_file, build_chunks_from_nlp_json, search_rag, answer_rag_extractive, RagIndex, RagChunk, RagSearchResult)

except Exception:
    # Évite de casser l'import du package si dépendance manquante.
    pass