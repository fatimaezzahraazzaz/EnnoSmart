"""
modules/RAG/vector_store.py — EnnoSmart RAG v2.0 structured POC

ChromaDB local persistant avec filtres exacts par organisme/document.
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
DEFAULT_COLLECTION = "ennosmart_chunks_v2"


def _get_chunk_id(chunk: dict, fallback_index: Optional[int] = None) -> str:
    if not isinstance(chunk, dict):
        return f"chunk_{fallback_index or 0:04d}"
    if chunk.get("chunk_id"):
        return str(chunk["chunk_id"])
    meta = chunk.get("metadata", {}) or {}
    if meta.get("chunk_id"):
        return str(meta["chunk_id"])
    file_name = meta.get("file_name") or chunk.get("file_name") or "doc"
    idx = chunk.get("index", meta.get("chunk_index", fallback_index or 0))
    return f"{file_name}_chunk_{idx}"


def _get_chunk_content(chunk: dict) -> str:
    if not isinstance(chunk, dict):
        return ""
    return str(chunk.get("content", "") or "").strip()


def _metadata_value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.lower().strip()
    if isinstance(value, (int, float, bool)):
        return str(value).lower().strip()
    if isinstance(value, list):
        return " ".join(_metadata_value_to_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(f"{k} {_metadata_value_to_text(v)}" for k, v in value.items())
    return str(value).lower().strip()


def _metadata_match(meta: dict, filter_meta: dict, mode: str = "contains") -> bool:
    if not filter_meta:
        return True
    meta = meta or {}
    for key, expected in filter_meta.items():
        actual = _metadata_value_to_text(meta.get(key))
        exp = _metadata_value_to_text(expected)
        if not exp:
            continue
        if mode == "exact":
            if actual != exp:
                return False
        else:
            if exp not in actual:
                return False
    return True


def _build_chroma_where(filter_meta: Optional[dict]) -> Optional[dict]:
    if not filter_meta:
        return None
    exact_fields = ["organisme_id", "file_hash", "document_id", "chunk_id", "chunk_source_type", "field_name"]
    conditions = []
    for field in exact_fields:
        value = filter_meta.get(field)
        if value is None:
            continue
        value = str(value).strip()
        if value:
            conditions.append({field: value})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _safe_metadata_value(value: Any) -> str | int | float | bool | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    return {str(k): _safe_metadata_value(v) for k, v in (metadata or {}).items()}


def _restore_metadata_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if not ((text.startswith("[") and text.endswith("]")) or (text.startswith("{") and text.endswith("}"))):
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def _restore_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {k: _restore_metadata_value(v) for k, v in (metadata or {}).items()}


class VectorStore:
    def __init__(self, path: Optional[Path] = None, collection_name: str = DEFAULT_COLLECTION):
        self.path = Path(path or DEFAULT_INDEX_DIR)
        self.collection_name = collection_name
        self.path.mkdir(parents=True, exist_ok=True)
        self._client = None
        self._collection = None
        self._dims: Optional[int] = None

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
            logger.warning("VectorStore.add(): aucun vecteur/chunk.")
            return 0
        if vectors.shape[0] != len(chunks):
            raise ValueError(f"Incompatible vectors/chunks : {vectors.shape[0]} vs {len(chunks)}")
        self._dims = int(vectors.shape[1])
        _, collection = self._load_client()

        ids, documents, embeddings, metadatas = [], [], [], []
        for i, chunk in enumerate(chunks):
            content = _get_chunk_content(chunk)
            if not content:
                continue
            chunk_id = _get_chunk_id(chunk, i)
            c = dict(chunk)
            meta = dict(c.get("metadata", {}) or {})
            meta["chunk_id"] = chunk_id
            meta["chunk_index"] = int(c.get("index", meta.get("chunk_index", i)))
            meta["source"] = c.get("source", meta.get("source", "rag"))
            meta["file_name"] = meta.get("file_name", c.get("file_name", "unknown"))
            meta["organisme_name"] = str(meta.get("organisme_name") or c.get("organisme_name") or "Organisme inconnu").strip()
            meta["organisme_id"] = str(meta.get("organisme_id") or c.get("organisme_id") or "organisme_inconnu").strip()
            meta["document_id"] = str(meta.get("document_id") or c.get("document_id") or "").strip()
            meta["file_hash"] = str(meta.get("file_hash") or c.get("file_hash") or "").strip()
            meta["chunk_source_type"] = str(meta.get("chunk_source_type") or c.get("chunk_source_type") or "raw_chunk")

            ids.append(chunk_id)
            documents.append(content)
            embeddings.append(vectors[i].astype(np.float32).tolist())
            metadatas.append(_safe_metadata(meta))

        if not ids:
            return 0
        collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
        logger.info("ChromaDB: %d chunks upsert | total=%d", len(ids), self.total_chunks)
        return len(ids)

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        filter_meta: Optional[dict] = None,
        filter_mode: str = "contains",
        fetch_multiplier: int = 8,
    ) -> list[dict]:
        _, collection = self._load_client()
        if collection.count() == 0:
            return []
        if query_vector is None or query_vector.size == 0:
            return []
        query_vec = query_vector.astype(np.float32).reshape(1, -1)
        where = _build_chroma_where(filter_meta)
        n_results = min(max(top_k * fetch_multiplier, top_k), collection.count())
        kwargs: dict[str, Any] = {
            "query_embeddings": query_vec.tolist(),
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        result = collection.query(**kwargs)
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        out = []
        for i, chunk_id in enumerate(ids):
            meta = _restore_metadata(metas[i] if i < len(metas) else {})
            if filter_meta and not _metadata_match(meta, filter_meta, mode=filter_mode):
                continue
            content = docs[i] if i < len(docs) else ""
            distance = float(distances[i] if i < len(distances) else 1.0)
            score = 1.0 - distance
            chunk = {
                "chunk_id": chunk_id,
                "content": content,
                "metadata": meta,
                "source": meta.get("source", "rag"),
                "index": meta.get("chunk_index"),
            }
            out.append({
                "chunk": chunk,
                "score": score,
                "rank": len(out) + 1,
                "chunk_id": chunk_id,
                "metadata": meta,
                "content": content,
                "distance": distance,
            })
            if len(out) >= top_k:
                break
        return out

    def save(self) -> None:
        logger.info("ChromaDB persiste automatiquement dans %s", self.path)

    def load(self) -> bool:
        try:
            self._load_client()
            logger.info("ChromaDB chargé : %d chunks", self.total_chunks)
            return True
        except Exception as exc:
            logger.error("Erreur chargement ChromaDB : %s", exc)
            return False

    def clear(self, delete_files: bool = False) -> None:
        try:
            if self._client is not None:
                try:
                    self._client.delete_collection(self.collection_name)
                except Exception:
                    pass
            self._client = None
            self._collection = None
            self._dims = None
            if delete_files and self.path.exists():
                shutil.rmtree(self.path)
                self.path.mkdir(parents=True, exist_ok=True)
            self._load_client()
        except Exception as exc:
            logger.warning("Erreur clear ChromaDB : %s", exc)

    def delete_by_filter(self, filter_meta: dict[str, Any]) -> int:
        _, collection = self._load_client()
        where = _build_chroma_where(filter_meta)
        if not where:
            raise ValueError("delete_by_filter exige au moins organisme_id/document_id/file_hash/chunk_id.")
        before = collection.count()
        collection.delete(where=where)
        after = collection.count()
        return max(0, before - after)

    def list_documents(self, organisme_id: Optional[str] = None) -> list[dict[str, Any]]:
        """
        Liste les documents indexés dans ChromaDB.

        Retour attendu par l'interface Streamlit :
        [
            {
                "organisme_id": "...",
                "organisme_name": "...",
                "file_name": "...",
                "document_id": "...",
                "file_hash": "...",
                "chunks_count": 67,
                "domaine_principal": "..."
            }
        ]

        Important :
        - Fonction compatible avec les chunks structurés v2 :
          section / field_card / entity_card / raw_chunk.
        - On reconstruit les documents uniquement depuis les métadonnées Chroma.
        """
        _, collection = self._load_client()

        if collection.count() == 0:
            return []

        where = None
        if organisme_id:
            org_id = str(organisme_id or "").strip()
            if org_id:
                where = {"organisme_id": org_id}

        try:
            if where:
                data = collection.get(where=where, include=["metadatas"])
            else:
                data = collection.get(include=["metadatas"])
        except Exception as exc:
            logger.warning("list_documents(): erreur lecture ChromaDB : %s", exc)
            return []

        metadatas = data.get("metadatas", []) or []

        grouped: dict[str, dict[str, Any]] = {}

        for raw_meta in metadatas:
            if not isinstance(raw_meta, dict):
                continue

            meta = _restore_metadata(raw_meta)

            org_id = str(meta.get("organisme_id") or "").strip()
            org_name = str(meta.get("organisme_name") or "Organisme inconnu").strip()

            file_name = str(meta.get("file_name") or "Document sans nom").strip()
            document_id = str(meta.get("document_id") or "").strip()
            file_hash = str(meta.get("file_hash") or "").strip()

            domaine = str(meta.get("domaine_principal") or "").strip()
            domaine_applicatif = str(meta.get("domaine_applicatif") or "").strip()
            domaine_detaille = str(meta.get("domaine_scientifique_detaille") or "").strip()

            source_tag = str(meta.get("source_tag") or "").strip()
            file_category = str(meta.get("file_category") or "").strip()
            title = str(meta.get("title") or "").strip()

            if not org_id:
                # Sans organisme_id, l'interface ne peut pas classer le document.
                continue

            # Clé stable document :
            # document_id > file_hash > couple organisme/fichier
            doc_key = document_id or file_hash or f"{org_id}::{file_name}"

            if doc_key not in grouped:
                grouped[doc_key] = {
                    "organisme_id": org_id,
                    "organisme_name": org_name,
                    "file_name": file_name,
                    "document_id": document_id,
                    "file_hash": file_hash,
                    "title": title,
                    "file_category": file_category,
                    "source_tag": source_tag,
                    "domaine_principal": domaine,
                    "domaine_applicatif": domaine_applicatif,
                    "domaine_scientifique_detaille": domaine_detaille,
                    "chunks_count": 0,
                    "section_chunks_count": 0,
                    "field_card_chunks_count": 0,
                    "entity_card_chunks_count": 0,
                    "raw_chunks_count": 0,
                }

            grouped[doc_key]["chunks_count"] += 1

            chunk_source_type = str(
                meta.get("chunk_source_type")
                or meta.get("source_type")
                or meta.get("source")
                or ""
            ).strip()

            if chunk_source_type == "section":
                grouped[doc_key]["section_chunks_count"] += 1
            elif chunk_source_type == "field_card":
                grouped[doc_key]["field_card_chunks_count"] += 1
            elif chunk_source_type == "entity_card":
                grouped[doc_key]["entity_card_chunks_count"] += 1
            elif chunk_source_type == "raw_chunk":
                grouped[doc_key]["raw_chunks_count"] += 1

            # Compléter les champs vides si une autre metadata les contient.
            if not grouped[doc_key].get("domaine_principal") and domaine:
                grouped[doc_key]["domaine_principal"] = domaine
            if not grouped[doc_key].get("domaine_applicatif") and domaine_applicatif:
                grouped[doc_key]["domaine_applicatif"] = domaine_applicatif
            if not grouped[doc_key].get("domaine_scientifique_detaille") and domaine_detaille:
                grouped[doc_key]["domaine_scientifique_detaille"] = domaine_detaille
            if not grouped[doc_key].get("title") and title:
                grouped[doc_key]["title"] = title

        docs = list(grouped.values())

        docs.sort(
            key=lambda d: (
                str(d.get("organisme_name") or "").lower(),
                str(d.get("file_name") or "").lower(),
            )
        )

        return docs

    def document_exists(
        self,
        *,
        organisme_id: str,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> bool:
        """
        Vérifie si un document existe déjà dans ChromaDB.
        """
        docs = self.list_documents(organisme_id=organisme_id)

        for doc in docs:
            if file_hash and str(doc.get("file_hash") or "") == str(file_hash):
                return True
            if document_id and str(doc.get("document_id") or "") == str(document_id):
                return True

        return False

    def count_document_chunks(
        self,
        *,
        organisme_id: str,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> int:
        """
        Compte les chunks d'un document.
        """
        docs = self.list_documents(organisme_id=organisme_id)

        for doc in docs:
            if file_hash and str(doc.get("file_hash") or "") == str(file_hash):
                return int(doc.get("chunks_count") or 0)
            if document_id and str(doc.get("document_id") or "") == str(document_id):
                return int(doc.get("chunks_count") or 0)

        return 0

    def delete_document(
        self,
        file_name: Optional[str] = None,
        *,
        organisme_id: Optional[str] = None,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> int:
        """
        Supprime un document de ChromaDB.

        Compatibilité :
        - ancien appel : delete_document(file_name)
        - nouvel appel recommandé : delete_document(document_id=..., file_hash=..., organisme_id=...)
        """
        filter_meta: dict[str, Any] = {}

        if document_id:
            filter_meta["document_id"] = str(document_id).strip()
        elif file_hash:
            filter_meta["file_hash"] = str(file_hash).strip()
        elif file_name:
            # Le filtre file_name n'est pas dans _build_chroma_where().
            # On récupère les ids puis on supprime à partir des ids.
            _, collection = self._load_client()
            try:
                data = collection.get(include=["metadatas"])
            except Exception as exc:
                logger.warning("delete_document(file_name): erreur ChromaDB : %s", exc)
                return 0

            ids_to_delete: list[str] = []
            ids = data.get("ids", []) or []
            metas = data.get("metadatas", []) or []

            target = str(file_name).strip()

            for idx, raw_meta in enumerate(metas):
                meta = _restore_metadata(raw_meta if isinstance(raw_meta, dict) else {})
                if organisme_id and str(meta.get("organisme_id") or "").strip() != str(organisme_id).strip():
                    continue
                if str(meta.get("file_name") or "").strip() == target:
                    if idx < len(ids):
                        ids_to_delete.append(str(ids[idx]))

            if not ids_to_delete:
                return 0

            before = collection.count()
            collection.delete(ids=ids_to_delete)
            after = collection.count()
            return max(0, before - after)
        else:
            raise ValueError("delete_document exige file_name, file_hash ou document_id.")

        if organisme_id:
            filter_meta["organisme_id"] = str(organisme_id).strip()

        return self.delete_by_filter(filter_meta)

    def delete_organisme(self, organisme_id: str) -> int:
        return self.delete_by_filter({"organisme_id": organisme_id})
