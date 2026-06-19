# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

from .json_to_chunks import nlp_json_to_chunks
from .project_store import ProjectStore
from .vector_store import RAGVectorStore


def _count_meta(chunks, key: str) -> Dict[str, int]:
    c = Counter()

    for ch in chunks or []:
        meta = ch.get("metadata", {}) or {}
        c[str(meta.get(key) or "unknown")] += 1

    return dict(c)


def index_nlp_result(
    organisme: str,
    project: str,
    nlp_result: Dict[str, Any],
    reset: bool = True,
    year: Optional[str | int] = None,
    annee: Optional[str | int] = None,
) -> Dict[str, Any]:
    """
    Indexe le résultat NLP dans Chroma.
    Aucun LLM ici.
    """
    ps = ProjectStore(organisme, project, year=year, annee=annee).ensure()
    ps.save_json("nlp/nlp_result.json", nlp_result)

    chunks = nlp_json_to_chunks(ps.project_id, nlp_result, year=ps.year)

    chunks_path = ps.rag_dir / "chunks.json"
    chunks_path.write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    collection_name = f"ennosmart_{ps.organisme_id}_{ps.project_id}_{ps.year_id}"

    vector_store = RAGVectorStore(ps.chroma_dir)
    rep = vector_store.add_chunks(
        collection_name=collection_name,
        chunks=chunks,
        reset=reset,
    )

    stats = {
        "document_types_count": _count_meta(chunks, "document_type"),
        "source_policies_count": _count_meta(chunks, "source_policy"),
        "roles_count": _count_meta(chunks, "role"),
        "final_roles_count": _count_meta(chunks, "final_role"),
        "frascati_decisions_count": _count_meta(chunks, "frascati_decision"),
        "verrou_sources_count": _count_meta(chunks, "verrou_source"),
        "theme_ids_count": _count_meta(chunks, "theme_id"),
        "verrou_candidate_levels_count": _count_meta(chunks, "verrou_candidate_level"),
    }

    ps.write_metadata({
        "last_indexed_chunks": rep.get("added", 0),
        "collection_name": collection_name,
        "rag_dir": str(ps.rag_dir),
        **stats,
    })

    return {
        "organisme_id": ps.organisme_id,
        "project_id": ps.project_id,
        "year": ps.year,
        "annee": ps.year,
        "year_id": ps.year_id,
        "collection_name": collection_name,
        "chunks_prepared": len(chunks),
        "chunks_indexed": rep.get("added", 0),
        "chunks_deduplicated": rep.get("deduplicated", 0),
        "project_dir": str(ps.project_dir),
        "chunks_path": str(chunks_path),
        **stats,
    }


def index_nlp_result_file(
    organisme: str,
    project: str,
    nlp_json_path: str | Path,
    reset: bool = True,
    year: Optional[str | int] = None,
    annee: Optional[str | int] = None,
) -> Dict[str, Any]:
    path = Path(nlp_json_path)
    data = json.loads(path.read_text(encoding="utf-8"))

    return index_nlp_result(
        organisme=organisme,
        project=project,
        nlp_result=data,
        reset=reset,
        year=year,
        annee=annee,
    )
