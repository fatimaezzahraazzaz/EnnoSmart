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
    counter = Counter()
    for chunk in chunks or []:
        meta = chunk.get("metadata", {}) or {}
        counter[str(meta.get(key) or "unknown")] += 1
    return dict(counter)


def index_nlp_result(
    organisme: str,
    project: str,
    nlp_result: Dict[str, Any],
    reset: bool = True,
    year: Optional[str | int] = None,
    annee: Optional[str | int] = None,
    subproject: Optional[str] = None,
) -> Dict[str, Any]:
    """Indexe fidelement les groupes deja finalises par le NLP.

    Le RAG ne regroupe, ne separe et ne reclasse aucun verrou. L'identifiant
    ``lock_group_id`` produit avant Frascati reste l'unique identite aval.
    """
    project_store = ProjectStore(
        organisme,
        project,
        subproject=subproject,
        year=year,
        annee=annee,
    ).ensure()
    project_store.save_json("nlp/nlp_result.json", nlp_result)

    chunks = nlp_json_to_chunks(
        project_store.project_id,
        nlp_result,
        year=project_store.year,
    )

    # Projection fidèle : aucune consolidation sémantique n'est exécutée ici.
    nlp_lock_group_ids = {
        str((chunk.get("metadata") or {}).get("lock_group_id") or "").strip()
        for chunk in chunks
        if isinstance(chunk, dict)
        and str((chunk.get("metadata") or {}).get("role") or "") == "verrou"
        and str((chunk.get("metadata") or {}).get("chunk_level") or "") == "nlp_main_item"
        and str((chunk.get("metadata") or {}).get("lock_group_id") or "").strip()
    }

    chunks_path = project_store.rag_dir / "chunks.json"
    chunks_path.write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    collection_name = project_store.collection_name
    report = RAGVectorStore(project_store.chroma_dir).add_chunks(
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
        "frascati_interpretations_count": _count_meta(chunks, "frascati_interpretation"),
        "verrou_sources_count": _count_meta(chunks, "verrou_source"),
        "theme_ids_count": _count_meta(chunks, "theme_id"),
        "verrou_candidate_levels_count": _count_meta(chunks, "verrou_candidate_level"),
        "chunk_levels_count": _count_meta(chunks, "chunk_level"),
        "nlp_lock_group_ids_count": len(nlp_lock_group_ids),
    }

    project_store.write_metadata({
        "last_indexed_chunks": report.get("added", 0),
        "collection_name": collection_name,
        "rag_dir": str(project_store.rag_dir),
        "lock_grouping_owner": "nlp_before_frascati",
        "downstream_lock_regrouping_enabled": False,
        "nlp_lock_groups_count": len(nlp_lock_group_ids),
        **stats,
    })

    return {
        "organisme_id": project_store.organisme_id,
        "project_id": project_store.project_id,
        "subproject_id": project_store.subproject_id or None,
        "year": project_store.year,
        "annee": project_store.year,
        "year_id": project_store.year_id,
        "collection_name": collection_name,
        "chunks_prepared": len(chunks),
        "chunks_indexed": report.get("added", 0),
        "chunks_deduplicated": report.get("deduplicated", 0),
        "embedding_model": report.get("embedding_model"),
        "project_dir": str(project_store.project_dir),
        "chunks_path": str(chunks_path),
        "lock_grouping_owner": "nlp_before_frascati",
        "downstream_lock_regrouping_enabled": False,
        "nlp_lock_groups_count": len(nlp_lock_group_ids),
        # Alias historiques : aucun fichier/cluster RAG n'est desormais cree.
        "lock_clusters_path": None,
        "lock_clusters_ok": True,
        "lock_clusters_version": None,
        "lock_clusters_mode": "disabled_nlp_group_passthrough",
        "lock_groups_count": len(nlp_lock_group_ids),
        "lock_display_clusters_count": len(nlp_lock_group_ids),
        "lock_support_only_clusters_count": 0,
        "lock_cluster_error": None,
        **stats,
    }


def index_nlp_result_file(
    organisme: str,
    project: str,
    nlp_json_path: str | Path,
    reset: bool = True,
    year: Optional[str | int] = None,
    annee: Optional[str | int] = None,
    subproject: Optional[str] = None,
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
        subproject=subproject,
    )
