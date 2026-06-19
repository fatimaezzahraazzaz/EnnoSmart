# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from sentence_transformers import SentenceTransformer

from .config import EMBEDDING_MODEL_NAME, EMBEDDING_OFFLINE


_MODEL_CACHE = None


def get_embedding_model() -> SentenceTransformer:
    """
    Charge le modèle embedding une seule fois.
    Ce n'est pas un LLM : c'est uniquement un modèle d'embedding pour la recherche vectorielle.
    """
    global _MODEL_CACHE

    if _MODEL_CACHE is not None:
        return _MODEL_CACHE

    kwargs = {}
    if EMBEDDING_OFFLINE:
        kwargs["local_files_only"] = True

    _MODEL_CACHE = SentenceTransformer(EMBEDDING_MODEL_NAME, **kwargs)
    return _MODEL_CACHE


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

    def add_chunks(self, collection_name: str, chunks: List[Dict[str, Any]], reset: bool = True) -> Dict[str, Any]:
        col = self.reset_collection(collection_name) if reset else self.collection(collection_name)

        if not chunks:
            return {"added": 0, "deduplicated": 0}

        safe_chunks = self._dedupe_chunk_ids(chunks)

        ids = [str(c["id"]) for c in safe_chunks]
        texts = [str(c["text"]) for c in safe_chunks]
        metadatas = [self._clean_metadata(c.get("metadata", {})) for c in safe_chunks]
        embeddings = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()

        col.add(ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings)

        return {
            "added": len(safe_chunks),
            "deduplicated": len(chunks) - len(safe_chunks),
        }

    def search(
        self,
        collection_name: str,
        query: str,
        top_k: int = 8,
        role_filter: Optional[str] = None,
        document_type_include: Optional[List[str]] = None,
        document_type_exclude: Optional[List[str]] = None,
        source_policy_exclude: Optional[List[str]] = None,
        oversample: int = 4,
    ) -> List[Dict[str, Any]]:
        col = self.collection(collection_name)

        query_embedding = self.model.encode(
            [query],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()[0]

        where = {"role": role_filter} if role_filter else None
        n_results = max(top_k, top_k * max(1, oversample))

        result = col.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        ids = result.get("ids", [[]])[0]

        out = []
        include = set(document_type_include or [])
        exclude = set(document_type_exclude or [])
        policy_ex = set(source_policy_exclude or [])

        for i in range(len(docs)):
            meta = metas[i] or {}
            dtype = str(meta.get("document_type") or "")
            policy = str(meta.get("source_policy") or "")

            if include and dtype not in include:
                continue
            if exclude and dtype in exclude:
                continue
            if policy_ex and policy in policy_ex:
                continue

            out.append({
                "id": ids[i],
                "text": docs[i],
                "metadata": meta,
                "distance": distances[i],
            })

            if len(out) >= top_k:
                break

        return out

    @staticmethod
    def _clean_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
        clean = {}

        for k, v in (meta or {}).items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                clean[k] = v
            else:
                clean[k] = str(v)

        return clean

    @staticmethod
    def _dedupe_chunk_ids(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        import hashlib

        seen = set()
        out = []

        for idx, c in enumerate(chunks or []):
            item = dict(c)
            base_id = str(item.get("id") or f"chunk_{idx}")
            text = str(item.get("text") or "")

            cid = base_id
            if cid in seen:
                suffix = hashlib.md5(f"{base_id}|{idx}|{text}".encode("utf-8")).hexdigest()[:8]
                cid = f"{base_id[:200]}_dup_{suffix}"
                counter = 1

                while cid in seen:
                    counter += 1
                    cid = f"{base_id[:195]}_dup_{suffix}_{counter}"

            seen.add(cid)
            item["id"] = cid

            meta = dict(item.get("metadata") or {})
            meta["rag_chunk_id"] = cid
            item["metadata"] = meta

            out.append(item)

        return out
