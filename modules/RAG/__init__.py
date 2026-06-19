# -*- coding: utf-8 -*-
"""
Module RAG EnnoSmart.

Important :
- Ce module ne contient PAS de LLM.
- Il sert uniquement à indexer, stocker et retrouver les sources.
- La génération de texte est dans modules/LLM.
"""

from .retriever import EnnoRetriever
from .indexer import index_nlp_result, index_nlp_result_file
