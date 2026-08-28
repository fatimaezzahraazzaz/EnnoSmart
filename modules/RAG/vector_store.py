# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import chromadb
from sentence_transformers import SentenceTransformer

from .config import EMBEDDING_MODEL_NAME, EMBEDDING_OFFLINE


_MODEL_CACHE: Optional[SentenceTransformer] = None


def get_embedding_model() -> SentenceTransformer:
    """Retourne l'unique instance du modèle sémantique pour tout le processus."""
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    kwargs = {"local_files_only": True} if EMBEDDING_OFFLINE else {}
    model_source = EMBEDDING_MODEL_NAME
    if EMBEDDING_OFFLINE:
        from huggingface_hub import snapshot_download

        model_source = snapshot_download(
            repo_id=EMBEDDING_MODEL_NAME,
            local_files_only=True,
        )
    _MODEL_CACHE = SentenceTransformer(model_source, **kwargs)
    return _MODEL_CACHE


def encode_texts(texts: Sequence[str]) -> List[List[float]]:
    """Encode des textes normalisés afin que le produit scalaire = cosinus."""
    clean = [str(text or "").strip() for text in texts]
    if not clean:
        return []
    return get_embedding_model().encode(
        clean,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).tolist()


class RAGVectorStore:
    def __init__(self, persist_dir: str | Path):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.persist_dir))
        self.model = get_embedding_model()

    def collection(self, collection_name: str):
        return self.client.get_or_create_collection(name=collection_name)

    def reset_collection(self, collection_name: str):
        try:
            self.client.delete_collection(collection_name)
        except Exception:
            pass
        return self.collection(collection_name)

    def add_chunks(
        self,
        collection_name: str,
        chunks: List[Dict[str, Any]],
        reset: bool = True,
    ) -> Dict[str, Any]:
        collection = self.reset_collection(collection_name) if reset else self.collection(collection_name)
        if not chunks:
            return {"added": 0, "deduplicated": 0}

        safe_chunks = self._dedupe_chunk_ids(chunks)
        ids = [str(chunk["id"]) for chunk in safe_chunks]

        # Le document stocké reste la preuve lisible exacte. Le texte utilisé
        # pour l'embedding peut être enrichi pour les groupes de verrous.
        documents = [str(chunk.get("text") or "") for chunk in safe_chunks]
        embedding_texts = [
            str(chunk.get("embedding_text") or chunk.get("text") or "")
            for chunk in safe_chunks
        ]
        metadatas = [self._clean_metadata(chunk.get("metadata", {})) for chunk in safe_chunks]
        # Chroma impose une taille maximale par op?ration add().
        # On travaille volontairement par lots de 1000 afin de rester
        # largement sous la limite du backend et de garder une consommation
        # m?moire stable lorsque Memory V2 devient volumineuse.
        batch_size = 1000

        for start in range(0, len(safe_chunks), batch_size):
            end = min(start + batch_size, len(safe_chunks))

            batch_embedding_texts = embedding_texts[start:end]

            batch_embeddings = self.model.encode(
                batch_embedding_texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist()

            collection.add(
                ids=ids[start:end],
                documents=documents[start:end],
                metadatas=metadatas[start:end],
                embeddings=batch_embeddings,
            )
        return {
            "added": len(safe_chunks),
            "deduplicated": len(chunks) - len(safe_chunks),
            "embedding_model": EMBEDDING_MODEL_NAME,
            "enriched_embedding_texts": sum(
                1 for chunk in safe_chunks if str(chunk.get("embedding_text") or "").strip()
            ),
        }

    def search(
        self,
        collection_name: str,
        query: str,
        top_k: int = 8,
        role_filter: Optional[str | Sequence[str]] = None,
        document_type_include: Optional[List[str]] = None,
        document_type_exclude: Optional[List[str]] = None,
        source_policy_exclude: Optional[List[str]] = None,
        chunk_level_include: Optional[List[str]] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
        oversample: int = 4,
    ) -> List[Dict[str, Any]]:
        collection = self.collection(collection_name)
        count = int(collection.count())
        if count <= 0 or not str(query or "").strip():
            return []

        query_embedding = self.model.encode(
            [str(query)],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()[0]

        where = dict(metadata_filter or {}) or None
        role_where = None
        if isinstance(role_filter, str) and role_filter.strip():
            role_where = {"role": role_filter.strip()}
        elif role_filter:
            roles = [str(role).strip() for role in role_filter if str(role).strip()]
            if roles:
                role_where = {"role": {"$in": roles}}
        if where and role_where:
            where = {"$and": [where, role_where]}
        elif role_where:
            where = role_where

        n_results = min(count, max(top_k, top_k * max(1, oversample)))
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        ids = result.get("ids", [[]])[0]
        include_types = set(document_type_include or [])
        exclude_types = set(document_type_exclude or [])
        excluded_policies = set(source_policy_exclude or [])
        included_levels = set(chunk_level_include or [])

        output: List[Dict[str, Any]] = []
        seen = set()
        for index, document in enumerate(docs):
            meta = metas[index] or {}
            document_type = str(meta.get("document_type") or "")
            source_policy = str(meta.get("source_policy") or "")
            chunk_level = str(meta.get("chunk_level") or "")
            if include_types and document_type not in include_types:
                continue
            if exclude_types and document_type in exclude_types:
                continue
            if excluded_policies and source_policy in excluded_policies:
                continue
            if included_levels and chunk_level not in included_levels:
                continue

            chunk_id = str(ids[index])
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            output.append({
                "id": chunk_id,
                "text": document,
                "metadata": meta,
                "distance": distances[index],
            })
            if len(output) >= top_k:
                break
        return output

    @staticmethod
    def _clean_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
        clean: Dict[str, Any] = {}
        for key, value in (meta or {}).items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                clean[key] = value
            elif isinstance(value, (dict, list, tuple)):
                clean[key] = json.dumps(value, ensure_ascii=False)
            else:
                clean[key] = str(value)
        return clean

    @staticmethod
    def _dedupe_chunk_ids(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        output = []
        for index, chunk in enumerate(chunks or []):
            item = dict(chunk)
            base_id = str(item.get("id") or f"chunk_{index}")
            text = str(item.get("text") or "")
            chunk_id = base_id
            if chunk_id in seen:
                suffix = hashlib.md5(f"{base_id}|{index}|{text}".encode("utf-8")).hexdigest()[:8]
                chunk_id = f"{base_id[:200]}_dup_{suffix}"
                counter = 1
                while chunk_id in seen:
                    counter += 1
                    chunk_id = f"{base_id[:195]}_dup_{suffix}_{counter}"
            seen.add(chunk_id)
            item["id"] = chunk_id
            metadata = dict(item.get("metadata") or {})
            metadata["rag_chunk_id"] = chunk_id
            item["metadata"] = metadata
            output.append(item)
        return output
