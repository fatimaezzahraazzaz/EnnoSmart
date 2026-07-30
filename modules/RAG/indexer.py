# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

from .json_to_chunks import nlp_json_to_chunks
from .lock_semantic_consolidator import save_lock_clusters
from .project_store import ProjectStore
from .vector_store import RAGVectorStore


def _count_meta(chunks, key: str) -> Dict[str, int]:
    counter = Counter()
    for chunk in chunks or []:
        meta = chunk.get("metadata", {}) or {}
        counter[str(meta.get(key) or "unknown")] += 1
    return dict(counter)


def _apply_cluster_metadata(chunks, cluster_report: Dict[str, Any]) -> None:
    """Ajoute l'identifiant de cluster aux chunks principaux sans modifier la preuve."""
    membership: Dict[str, Dict[str, Any]] = {}
    for cluster in cluster_report.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        for group_id in cluster.get("member_group_ids") or []:
            membership[str(group_id)] = cluster

    for chunk in chunks or []:
        if not isinstance(chunk, dict):
            continue
        meta = chunk.get("metadata") or {}
        if not isinstance(meta, dict):
            continue
        group_id = str(meta.get("lock_group_id") or "").strip()
        cluster = membership.get(group_id)
        if not cluster:
            continue
        meta.update({
            "semantic_lock_cluster_id": str(cluster.get("cluster_id") or ""),
            "semantic_lock_cluster_scope": str(cluster.get("lock_scope") or ""),
            "semantic_lock_cluster_technical_scope": str(cluster.get("technical_scope") or ""),
            "semantic_lock_cluster_role": str(cluster.get("cluster_role") or ""),
            "semantic_lock_cluster_display": bool(cluster.get("display_as_lock", True)),
            "semantic_lock_cluster_size": int(cluster.get("group_count") or 1),
            "semantic_lock_cluster_version": str(cluster_report.get("version") or ""),
        })
        chunk["metadata"] = meta


def index_nlp_result(
    organisme: str,
    project: str,
    nlp_result: Dict[str, Any],
    reset: bool = True,
    year: Optional[str | int] = None,
    annee: Optional[str | int] = None,
) -> Dict[str, Any]:
    """Indexe le NLP et consolide les groupes de verrous dans le RAG, sans LLM."""
    project_store = ProjectStore(organisme, project, year=year, annee=annee).ensure()
    project_store.save_json("nlp/nlp_result.json", nlp_result)

    chunks = nlp_json_to_chunks(
        project_store.project_id,
        nlp_result,
        year=project_store.year,
    )

    # La consolidation sémantique est calculée une fois lors de l'indexation.
    # EnnoDiagnostic lira ensuite ce fichier et ne décidera plus des fusions.
    lock_clusters_path = project_store.rag_dir / "lock_clusters.json"
    cluster_report = save_lock_clusters(
        nlp_result=nlp_result,
        output_path=lock_clusters_path,
    )
    _apply_cluster_metadata(chunks, cluster_report)

    chunks_path = project_store.rag_dir / "chunks.json"
    chunks_path.write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    collection_name = (
        f"ennosmart_{project_store.organisme_id}_{project_store.project_id}_{project_store.year_id}"
    )
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
        "semantic_lock_cluster_ids_count": _count_meta(chunks, "semantic_lock_cluster_id"),
    }

    project_store.write_metadata({
        "last_indexed_chunks": report.get("added", 0),
        "collection_name": collection_name,
        "rag_dir": str(project_store.rag_dir),
        "lock_clusters_path": str(lock_clusters_path),
        "lock_clusters_version": cluster_report.get("version"),
        "lock_clusters_mode": cluster_report.get("mode"),
        "lock_clusters_count": cluster_report.get("display_clusters_count", 0),
        **stats,
    })

    return {
        "organisme_id": project_store.organisme_id,
        "project_id": project_store.project_id,
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
        "lock_clusters_path": str(lock_clusters_path),
        "lock_clusters_ok": bool(cluster_report.get("ok")),
        "lock_clusters_version": cluster_report.get("version"),
        "lock_clusters_mode": cluster_report.get("mode"),
        "lock_groups_count": cluster_report.get("groups_count", 0),
        "lock_display_clusters_count": cluster_report.get("display_clusters_count", 0),
        "lock_support_only_clusters_count": cluster_report.get("support_only_clusters_count", 0),
        "lock_cluster_error": cluster_report.get("error"),
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
