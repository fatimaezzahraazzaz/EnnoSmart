# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import EMBEDDING_MODEL_NAME, FULLTEXT_CHAT_COLLECTION_SUFFIX
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
    persist_json_files: bool = True,
    artifact_sink: Optional[Callable[[str, Any], None]] = None,
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
    if artifact_sink is not None:
        artifact_sink("nlp/nlp_result.json", nlp_result)
    if persist_json_files:
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

    if artifact_sink is not None:
        artifact_sink("rag/chunks.json", chunks)
    chunks_path = project_store.rag_dir / "chunks.json"
    if persist_json_files:
        chunks_path.write_text(
            json.dumps(chunks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    collection_name = project_store.collection_name
    report = RAGVectorStore(
        project_store.chroma_dir,
        scope_metadata=project_store.chroma_scope_metadata,
        collection_namespace=collection_name,
    ).add_chunks(
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
        "chunks_path": str(chunks_path) if persist_json_files else None,
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


FULLTEXT_INDEX_VERSION = "fulltext_rag_index_v2"
FULLTEXT_INDEX_MANIFEST = "fulltext_index_manifest.json"


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _fulltext_files(corpus_dir: Path) -> List[Path]:
    if not corpus_dir.is_dir():
        return []
    return [
        path
        for path in sorted(corpus_dir.glob("*.json"), key=lambda item: item.name.lower())
        if path.is_file() and path.name.lower() not in {"manifest.json", "nlp_result.json"}
    ]


def fulltext_corpus_fingerprint(corpus_dir: str | Path) -> str:
    """Empreinte stable du contenu fulltext, indépendante des mtimes Windows."""
    folder = Path(corpus_dir)
    rows: List[str] = []
    for path in _fulltext_files(folder):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        text = _clean_text(payload.get("text"))
        if not text:
            continue
        text_sha = str(payload.get("text_sha256") or "").strip()
        if not text_sha:
            text_sha = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        rows.append(
            "|".join(
                [
                    str(payload.get("document_id") or ""),
                    str(payload.get("document") or path.stem),
                    text_sha,
                    str(len(text)),
                ]
            )
        )
    raw = "\n".join(rows)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def _split_with_offsets(
    text: str,
    *,
    max_chars: int = 1700,
    overlap: int = 220,
) -> List[Tuple[str, int, int]]:
    source = _clean_text(text)
    if not source:
        return []
    max_chars = max(400, int(max_chars or 1700))
    overlap = max(0, min(int(overlap or 0), max_chars // 2))
    out: List[Tuple[str, int, int]] = []
    start = 0
    while start < len(source):
        end = min(len(source), start + max_chars)
        if end < len(source):
            floor = start + int(max_chars * 0.55)
            boundary = max(
                source.rfind("\n\n", floor, end),
                source.rfind("\n", floor, end),
                source.rfind(". ", floor, end),
                source.rfind("; ", floor, end),
            )
            if boundary >= floor:
                end = boundary + (2 if source[boundary:boundary + 2] in {". ", "; "} else 1)
        raw = source[start:end]
        leading = len(raw) - len(raw.lstrip())
        clean = raw.strip()
        chunk_start = start + leading
        chunk_end = chunk_start + len(clean)
        if clean:
            out.append((clean, chunk_start, chunk_end))
        if end >= len(source):
            break
        next_start = max(end - overlap, start + 1)
        start = next_start
    return out


def fulltext_corpus_to_chunks(
    project_store: ProjectStore,
    *,
    max_chars: int = 1700,
    overlap: int = 220,
) -> List[Dict[str, Any]]:
    """Convertit directement fulltext_rag_v1 en chunks Chroma traçables."""
    corpus_dir = Path(project_store.documents_processed_dir) / "fulltext_rag_v1"
    chunks: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for path in _fulltext_files(corpus_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        document = str(payload.get("document") or payload.get("file_name") or path.stem).strip()
        document_id = str(payload.get("document_id") or payload.get("text_sha256") or path.stem).strip()
        source_path = str(payload.get("source_path") or "").strip()
        base_meta = {
            "project_id": project_store.project_id,
            "year": project_store.year,
            "annee": project_store.year,
            "document": Path(document or path.stem).name,
            "document_id": document_id,
            "source_path": source_path,
            "role": "document_brut",
            "final_role": "document_brut",
            "document_type": str(payload.get("document_type") or "client_raw_document"),
            "source_policy": str(payload.get("source_policy") or "current_project_document_scope_only"),
            "content_origin": str(payload.get("content_origin") or ""),
            "source_kind": "client_raw",
            "chunk_level": "fulltext_document_chunk",
            "is_supporting_passage": False,
            "fulltext_index_version": FULLTEXT_INDEX_VERSION,
            "fulltext_corpus_path": str(path),
        }

        # Toujours chunker le texte fulltext complet. Les sections servent
        # uniquement à enrichir la provenance ; elles ne doivent jamais devenir
        # un filtre qui ferait disparaître une introduction ou un passage situé
        # hors des marqueurs [SECTION : ...].
        sections = [
            section
            for section in (payload.get("sections") or [])
            if isinstance(section, dict)
        ]

        def section_title_for_offset(offset: int) -> str:
            for section in sections:
                try:
                    section_start = int(section.get("char_start") or 0)
                    section_end = int(section.get("char_end") or 0)
                except Exception:
                    continue
                if section_start <= offset < section_end:
                    return str(section.get("section_title") or "").strip()
            return ""

        sources: List[Tuple[str, str, int, int]] = []
        body = _clean_text(payload.get("text"))
        for part, local_start, local_end in _split_with_offsets(
            body,
            max_chars=max_chars,
            overlap=overlap,
        ):
            sources.append((
                part,
                section_title_for_offset(local_start),
                local_start,
                local_end,
            ))

        for index, (part, section_title, char_start, char_end) in enumerate(sources):
            digest = hashlib.sha256(
                f"{document_id}|{char_start}|{char_end}|{part[:500]}".encode("utf-8", errors="ignore")
            ).hexdigest()[:24]
            chunk_id = f"fulltext_{digest}"
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            chunks.append(
                {
                    "id": chunk_id,
                    "text": part,
                    "embedding_text": part,
                    "metadata": {
                        **base_meta,
                        "section_title": section_title,
                        "char_start": char_start,
                        "char_end": char_end,
                        "paragraph_index": index,
                        "json_path": "fulltext.text",
                    },
                }
            )

    return chunks


def index_fulltext_corpus(
    organisme: str,
    project: str,
    *,
    reset: bool = True,
    year: Optional[str | int] = None,
    annee: Optional[str | int] = None,
    subproject: Optional[str] = None,
) -> Dict[str, Any]:
    """Indexe fulltext_rag_v1 sans lire nlp_result.json.

    La collection dédiée au chat documentaire reste séparée de la collection
    historique NLP afin de ne pas casser les modules legacy pendant la migration.
    """
    project_store = ProjectStore(
        organisme,
        project,
        subproject=subproject,
        year=year,
        annee=annee,
    ).ensure()
    corpus_dir = Path(project_store.documents_processed_dir) / "fulltext_rag_v1"
    fingerprint = fulltext_corpus_fingerprint(corpus_dir)
    if not fingerprint or fingerprint == hashlib.sha256(b"").hexdigest():
        raise RuntimeError(f"Corpus fulltext vide ou introuvable : {corpus_dir}")

    collection_name = project_store.collection_name + FULLTEXT_CHAT_COLLECTION_SUFFIX
    manifest_path = Path(project_store.rag_dir) / FULLTEXT_INDEX_MANIFEST
    chat_manifest_path = Path(project_store.rag_dir) / "diagnostic_chat_raw_manifest.json"
    previous: Dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}

    store = RAGVectorStore(
        project_store.chroma_dir,
        scope_metadata=project_store.chroma_scope_metadata,
        collection_namespace=project_store.collection_name,
    )
    current_count = int(store.collection(collection_name).count())
    if (
        previous.get("version") == FULLTEXT_INDEX_VERSION
        and previous.get("fingerprint") == fingerprint
        and previous.get("embedding_model") == EMBEDDING_MODEL_NAME
        and current_count > 0
    ):
        return {
            "ok": True,
            "reused": True,
            "source": "fulltext_rag_v1",
            "nlp_result_used": False,
            "collection_name": collection_name,
            "chunks_indexed": current_count,
            "fingerprint": fingerprint,
        }

    chunks = fulltext_corpus_to_chunks(project_store)
    if not chunks:
        raise RuntimeError(f"Aucun chunk fulltext exploitable : {corpus_dir}")

    report = store.add_chunks(
        collection_name=collection_name,
        chunks=chunks,
        reset=reset,
    )
    manifest = {
        "version": FULLTEXT_INDEX_VERSION,
        "fingerprint": fingerprint,
        "source": "fulltext_rag_v1",
        "nlp_result_used": False,
        "collection_name": collection_name,
        "chunks_indexed": int(report.get("added") or 0),
        "embedding_model": report.get("embedding_model"),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    chat_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    project_store.write_metadata({
        "rag_index_source": "fulltext_rag_v1",
        "rag_nlp_result_used": False,
        "fulltext_collection_name": collection_name,
        "fulltext_chunks_indexed": manifest["chunks_indexed"],
        "fulltext_index_fingerprint": fingerprint,
    })

    return {
        "ok": True,
        "reused": False,
        "source": "fulltext_rag_v1",
        "nlp_result_used": False,
        "collection_name": collection_name,
        "chunks_prepared": len(chunks),
        "chunks_indexed": manifest["chunks_indexed"],
        "embedding_model": report.get("embedding_model"),
        "fingerprint": fingerprint,
        "manifest_path": str(manifest_path),
    }
