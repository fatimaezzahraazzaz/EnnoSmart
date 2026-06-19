# -*- coding: utf-8 -*-
r"""
modules/RAG/vector_store.py — EnnoSmart RAG No-LLM V4

ChromaDB document-first.
Pas de LLM.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_INDEX_DIR = Path("modules/RAG/.chroma")
DEFAULT_COLLECTION = "ennosmart_rag_no_llm_v4"


def _safe_meta_value(v: Any):
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _safe_meta(meta: dict) -> dict:
    out = {}
    for k, v in (meta or {}).items():
        val = _safe_meta_value(v)
        if val is not None:
            out[str(k)] = val
    return out


def _restore(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return v
    if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
        try:
            return json.loads(s)
        except Exception:
            return v
    return v


def _restore_meta(meta: dict) -> dict:
    return {k: _restore(v) for k, v in (meta or {}).items()}


def _text(v: Any) -> str:
    v = _restore(v)
    if v is None:
        return ""
    if isinstance(v, str):
        return v.lower().strip()
    if isinstance(v, (int, float, bool)):
        return str(v).lower().strip()
    if isinstance(v, list):
        return " ".join(_text(x) for x in v)
    if isinstance(v, dict):
        return " ".join(f"{k} {_text(x)}" for k, x in v.items())
    return str(v).lower().strip()


def _meta_match(meta: dict, filt: Optional[dict], mode: str = "contains") -> bool:
    if not filt:
        return True
    exact = {
        "document_id", "file_hash", "project_id", "chunk_source_type",
        "field_name", "role_final", "section_zone", "chunk_id"
    }
    for k, expected in filt.items():
        exp = _text(expected)
        if not exp:
            continue
        actual = _text((meta or {}).get(k))
        if mode == "exact" or k in exact:
            if actual != exp:
                return False
        else:
            if exp not in actual:
                return False
    return True


def _where(filt: Optional[dict]) -> Optional[dict]:
    if not filt:
        return None
    exact_fields = [
        "document_id", "file_hash", "project_id", "chunk_id",
        "chunk_source_type", "field_name", "role_final", "section_zone",
    ]
    cond = []
    for f in exact_fields:
        if filt.get(f):
            cond.append({f: str(filt[f]).strip()})
    if not cond:
        return None
    if len(cond) == 1:
        return cond[0]
    return {"$and": cond}


class VectorStore:
    def __init__(self, path: Optional[Path] = None, collection_name: str = DEFAULT_COLLECTION):
        self.path = Path(path or DEFAULT_INDEX_DIR)
        self.collection_name = collection_name
        self.path.mkdir(parents=True, exist_ok=True)
        self._client = None
        self._collection = None

    def _load_client(self):
        if self._client is not None and self._collection is not None:
            return self._client, self._collection
        try:
            import chromadb
        except ImportError as exc:
            raise ImportError("Installe chromadb : pip install chromadb") from exc

        self._client = chromadb.PersistentClient(path=str(self.path))
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        return self._client, self._collection

    @property
    def total_chunks(self) -> int:
        try:
            _, col = self._load_client()
            return int(col.count())
        except Exception:
            return 0

    @property
    def is_empty(self) -> bool:
        return self.total_chunks == 0

    def add(self, vectors: np.ndarray, chunks: list[dict]) -> int:
        if vectors is None or vectors.size == 0 or not chunks:
            return 0
        if vectors.shape[0] != len(chunks):
            raise ValueError(f"vectors/chunks incompatibles : {vectors.shape[0]} vs {len(chunks)}")

        _, col = self._load_client()
        ids, docs, embs, metas = [], [], [], []

        for i, c in enumerate(chunks):
            content = str(c.get("content", "") or "").strip()
            if not content:
                continue
            meta = dict(c.get("metadata", {}) or {})
            chunk_id = str(c.get("chunk_id") or meta.get("chunk_id") or f"chunk_{i:06d}")

            meta["chunk_id"] = chunk_id
            meta["chunk_index"] = int(c.get("index", i))
            meta["file_name"] = str(meta.get("file_name") or c.get("file_name") or "document").strip()
            meta["document_id"] = str(meta.get("document_id") or c.get("document_id") or "").strip()
            meta["file_hash"] = str(meta.get("file_hash") or c.get("file_hash") or "").strip()
            meta["project_id"] = str(meta.get("project_id") or c.get("project_id") or "").strip()
            meta["chunk_source_type"] = str(meta.get("chunk_source_type") or c.get("source") or "rag_chunk").strip()

            ids.append(chunk_id)
            docs.append(content)
            embs.append(vectors[i].astype(np.float32).tolist())
            metas.append(_safe_meta(meta))

        if not ids:
            return 0
        col.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=metas)
        return len(ids)

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        filter_meta: Optional[dict] = None,
        filter_mode: str = "contains",
        fetch_multiplier: int = 8,
    ) -> list[dict]:
        _, col = self._load_client()
        if col.count() == 0:
            return []
        if query_vector is None or query_vector.size == 0:
            return []

        n_results = min(max(top_k * fetch_multiplier, top_k), col.count())
        kwargs = {
            "query_embeddings": query_vector.astype(np.float32).reshape(1, -1).tolist(),
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        where = _where(filter_meta)
        if where:
            kwargs["where"] = where

        res = col.query(**kwargs)
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]

        out = []
        for i, cid in enumerate(ids):
            meta = _restore_meta(metas[i] if i < len(metas) else {})
            if filter_meta and not _meta_match(meta, filter_meta, filter_mode):
                continue
            content = docs[i] if i < len(docs) else ""
            dist = float(dists[i] if i < len(dists) else 1.0)
            score = 1.0 - dist
            out.append({
                "chunk_id": cid,
                "content": content,
                "metadata": meta,
                "score": score,
                "distance": dist,
                "rank": len(out) + 1,
                "chunk": {"chunk_id": cid, "content": content, "metadata": meta},
            })
            if len(out) >= top_k:
                break
        return out

    def save(self) -> None:
        # Chroma persiste automatiquement.
        pass

    def load(self) -> bool:
        self._load_client()
        return True

    def clear(self, delete_files: bool = False):
        if self._client is not None:
            try:
                self._client.delete_collection(self.collection_name)
            except Exception:
                pass
        self._client = None
        self._collection = None
        if delete_files and self.path.exists():
            shutil.rmtree(self.path)
            self.path.mkdir(parents=True, exist_ok=True)
        self._load_client()

    def list_documents(self, project_id: Optional[str] = None) -> list[dict]:
        _, col = self._load_client()
        if col.count() == 0:
            return []

        where = {"project_id": str(project_id).strip()} if project_id else None
        data = col.get(where=where, include=["metadatas"]) if where else col.get(include=["metadatas"])
        metas = data.get("metadatas", []) or []

        grouped = {}
        for m in metas:
            meta = _restore_meta(m if isinstance(m, dict) else {})
            key = str(meta.get("document_id") or meta.get("file_hash") or meta.get("file_name") or "document")
            if key not in grouped:
                grouped[key] = {
                    "document_id": meta.get("document_id", ""),
                    "file_name": meta.get("file_name", ""),
                    "file_hash": meta.get("file_hash", ""),
                    "project_id": meta.get("project_id", ""),
                    "domaine_principal": meta.get("domaine_principal", ""),
                    "domaine_code": meta.get("domaine_code", ""),
                    "chunks_count": 0,
                    "section_chunks_count": 0,
                    "field_card_chunks_count": 0,
                    "consultant_card_chunks_count": 0,
                    "evidence_card_chunks_count": 0,
                    "entity_card_chunks_count": 0,
                    "raw_chunks_count": 0,
                }
            grouped[key]["chunks_count"] += 1
            ctype = str(meta.get("chunk_source_type") or "")
            if ctype == "section_cir":
                grouped[key]["section_chunks_count"] += 1
            elif ctype == "field_card":
                grouped[key]["field_card_chunks_count"] += 1
            elif ctype == "consultant_card":
                grouped[key]["consultant_card_chunks_count"] += 1
            elif ctype == "evidence_card":
                grouped[key]["evidence_card_chunks_count"] += 1
            elif ctype == "entity_card":
                grouped[key]["entity_card_chunks_count"] += 1
            elif ctype == "raw_chunk":
                grouped[key]["raw_chunks_count"] += 1

        return sorted(grouped.values(), key=lambda x: str(x.get("file_name", "")).lower())

    def delete_document(self, document_id: Optional[str] = None, file_hash: Optional[str] = None, file_name: Optional[str] = None) -> int:
        filt = {}
        if document_id:
            filt["document_id"] = document_id
        elif file_hash:
            filt["file_hash"] = file_hash
        elif file_name:
            filt["file_name"] = file_name
        else:
            return 0
        _, col = self._load_client()
        before = col.count()
        where = _where(filt)
        if where:
            col.delete(where=where)
        after = col.count()
        return max(0, before - after)
