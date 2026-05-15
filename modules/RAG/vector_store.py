"""
modules/RAG/vector_store.py — EnnoSmart RAG v1.1 ChromaDB
──────────────────────────────────────────────────────────────────────────────
Vector store local ChromaDB pour stocker et rechercher les chunks NLP enrichis.

Pourquoi ChromaDB pour EnnoSmart :
  - persistance locale simple ;
  - pas de serveur obligatoire ;
  - gestion directe des documents, embeddings et métadonnées ;
  - plus pratique que FAISS pour filtrer par organisme_id, file_name, domaine, source, etc. ;
  - adapté au POC Streamlit + EnnoAmel.

Compatible avec :
  - modules/RAG/embedder.py
  - NLPResult.to_json()["chunks"]

Usage :
  from modules.RAG.vector_store import VectorStore

  store = VectorStore(path="modules/RAG/.chroma")
  store.add(vectors, chunks)
  results = store.search(query_vec, top_k=5)
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
DEFAULT_COLLECTION = "ennosmart_chunks"


def _get_chunk_id(chunk: dict, fallback_index: Optional[int] = None) -> str:
    """
    Récupère un identifiant stable pour un chunk.

    Priorité :
      1. chunk["chunk_id"]
      2. chunk["metadata"]["chunk_id"]
      3. file_name + index
      4. fallback générique
    """
    if not isinstance(chunk, dict):
        return f"chunk_{fallback_index or 0:04d}"

    if chunk.get("chunk_id"):
        return str(chunk["chunk_id"])

    meta = chunk.get("metadata", {}) or {}

    if meta.get("chunk_id"):
        return str(meta["chunk_id"])

    file_name = (
        meta.get("file_name")
        or chunk.get("file_name")
        or "doc"
    )

    index = chunk.get("index", meta.get("chunk_index", fallback_index))

    if index is not None:
        try:
            return f"{file_name}_chunk_{int(index):04d}"
        except Exception:
            return f"{file_name}_chunk_{index}"

    return f"{file_name}_chunk_{fallback_index or 0:04d}"


def _get_chunk_content(chunk: dict) -> str:
    """Retourne le texte principal du chunk."""
    if not isinstance(chunk, dict):
        return ""
    return str(chunk.get("content", "") or "").strip()


def _metadata_value_to_text(value: Any) -> str:
    """
    Convertit une valeur metadata en texte pour comparaison souple.

    Sert au filtre local contains/exact après recherche.
    """
    if value is None:
        return ""

    if isinstance(value, str):
        return value.lower().strip()

    if isinstance(value, (int, float, bool)):
        return str(value).lower().strip()

    if isinstance(value, list):
        return " ".join(_metadata_value_to_text(v) for v in value)

    if isinstance(value, dict):
        return " ".join(
            f"{k} {_metadata_value_to_text(v)}"
            for k, v in value.items()
        )

    return str(value).lower().strip()


def _metadata_match(meta: dict, filter_meta: dict, mode: str = "contains") -> bool:
    """
    Filtre metadata souple.

    mode="contains" :
      filter_meta={"domaine_principal": "intelligence artificielle"}
      matche "intelligence artificielle et génie logiciel"

    mode="exact" :
      impose égalité stricte.
    """
    if not filter_meta:
        return True

    meta = meta or {}

    for key, expected in filter_meta.items():
        actual_text = _metadata_value_to_text(meta.get(key))
        expected_text = _metadata_value_to_text(expected)

        if not expected_text:
            continue

        if mode == "exact":
            if actual_text != expected_text:
                return False
        else:
            if expected_text not in actual_text:
                return False

    return True




def _build_chroma_where(filter_meta: Optional[dict]) -> Optional[dict]:
    """
    Construit un filtre ChromaDB valide pour les champs exacts/stables.

    Règle ChromaDB importante :
      - un seul champ exact  → {"organisme_id": "scalian"}
      - plusieurs champs     → {"$and": [{"organisme_id": "scalian"}, {"file_hash": "..."}]}

    On ne met ici que les champs vraiment exacts :
      - organisme_id : isolation client
      - file_hash    : identité binaire du document
      - document_id  : identité logique du document
      - chunk_id     : identité du chunk

    Les autres filtres restent vérifiés localement par _metadata_match(),
    car ils peuvent être longs, partiels ou stockés comme JSON string.
    """
    if not filter_meta:
        return None

    exact_fields = [
        "organisme_id",
        "file_hash",
        "document_id",
        "chunk_id",
    ]

    conditions: list[dict[str, Any]] = []

    for field in exact_fields:
        value = filter_meta.get(field)
        if value is None:
            continue

        value = str(value).strip()
        if not value:
            continue

        conditions.append({field: value})

    if not conditions:
        return None

    if len(conditions) == 1:
        return conditions[0]

    return {"$and": conditions}

def _safe_metadata_value(value: Any) -> str | int | float | bool | None:
    """
    ChromaDB accepte uniquement :
      str, int, float, bool, None

    Les listes/dicts sont convertis en JSON string.
    """
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)

    return str(value)


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    """Convertit toutes les métadonnées en types acceptés par Chroma."""
    safe: dict[str, str | int | float | bool | None] = {}

    for key, value in (metadata or {}).items():
        key = str(key)
        safe[key] = _safe_metadata_value(value)

    return safe


def _restore_metadata_value(value: Any) -> Any:
    """
    Essaie de restaurer les listes/dicts stockés en JSON string.
    Si ce n'est pas du JSON, retourne la valeur brute.
    """
    if not isinstance(value, str):
        return value

    text = value.strip()

    if not text:
        return value

    if not (
        (text.startswith("[") and text.endswith("]"))
        or (text.startswith("{") and text.endswith("}"))
    ):
        return value

    try:
        return json.loads(text)
    except Exception:
        return value


def _restore_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _restore_metadata_value(value)
        for key, value in (metadata or {}).items()
    }


class VectorStore:
    """
    Store ChromaDB local.

    Stocke :
      - embeddings
      - documents/chunks
      - métadonnées

    Méthodes principales :
      - add(vectors, chunks)
      - search(query_vector)
      - delete_document(file_name)
      - clear()
      - total_chunks
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        collection_name: str = DEFAULT_COLLECTION,
    ):
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
            raise ImportError(
                "chromadb non installé. Exécute : pip install chromadb"
            ) from exc

        self._client = chromadb.PersistentClient(path=str(self.path))

        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        logger.debug(
            "ChromaDB collection prête : %s → %s",
            self.collection_name,
            self.path,
        )

        return self._client, self._collection

    def add(self, vectors: np.ndarray, chunks: list[dict]) -> int:
        """
        Ajoute ou remplace des vecteurs + chunks dans ChromaDB.

        Paramètres :
          vectors : np.ndarray shape = (n_chunks, dims)
          chunks  : liste des chunks NLP JSON

        Retourne :
          nombre de chunks ajoutés/remplacés.
        """
        if vectors is None or vectors.size == 0:
            logger.warning("Aucun vecteur à ajouter.")
            return 0

        if not chunks:
            logger.warning("Aucun chunk à ajouter.")
            return 0

        if vectors.shape[0] != len(chunks):
            raise ValueError(
                f"Nombre vecteurs/chunks incompatible : "
                f"{vectors.shape[0]} vecteurs pour {len(chunks)} chunks."
            )

        dims = int(vectors.shape[1])
        self._dims = dims

        _, collection = self._load_client()

        ids: list[str] = []
        documents: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict] = []

        for i, chunk in enumerate(chunks):
            chunk_id = _get_chunk_id(chunk, fallback_index=i)
            content = _get_chunk_content(chunk)

            if not content:
                continue

            c = dict(chunk)
            c["chunk_id"] = chunk_id

            meta = c.get("metadata", {}) or {}
            meta = dict(meta)
            meta["chunk_id"] = chunk_id
            meta["chunk_index"] = int(c.get("index", meta.get("chunk_index", i)))
            meta["source"] = c.get("source", meta.get("source", "text"))
            meta["file_name"] = meta.get("file_name", c.get("file_name", "unknown"))

            # Clés métier multi-organismes : indispensables pour isoler
            # les recherches RAG et l'historique par client.
            meta["organisme_name"] = str(
                meta.get("organisme_name")
                or c.get("organisme_name")
                or "Organisme inconnu"
            ).strip()
            meta["organisme_id"] = str(
                meta.get("organisme_id")
                or c.get("organisme_id")
                or "organisme_inconnu"
            ).strip()

            meta["file_hash"] = str(
                meta.get("file_hash")
                or c.get("file_hash")
                or ""
            ).strip()
            meta["document_id"] = str(
                meta.get("document_id")
                or c.get("document_id")
                or (f"{meta['organisme_id']}_{meta['file_hash'][:16]}" if meta.get("file_hash") else "")
            ).strip()

            ids.append(chunk_id)
            documents.append(content)
            embeddings.append(vectors[i].astype(np.float32).tolist())
            metadatas.append(_safe_metadata(meta))

        if not ids:
            logger.warning("Aucun chunk avec contenu utile à ajouter.")
            return 0

        # upsert = remplacement automatique si chunk_id existe déjà.
        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        logger.info(
            "VectorStore ChromaDB : +%d chunks | total=%d",
            len(ids),
            self.total_chunks,
        )

        return len(ids)

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        filter_meta: Optional[dict] = None,
        filter_mode: str = "contains",
        fetch_multiplier: int = 8,
    ) -> list[dict]:
        """
        Recherche les top_k chunks les plus proches du vecteur requête.

        filter_meta :
          filtre optionnel sur métadonnées.

        Important :
          - organisme_id est filtré directement par ChromaDB en exact.
          - les autres filtres restent vérifiés localement en contains/exact
            pour garder une recherche souple sur domaine, fichier, source, etc.

        Retourne :
          [
            {
              "chunk": chunk,
              "score": float,
              "rank": int,
              "chunk_id": str,
              "metadata": dict,
              "content": str
            }
          ]
        """
        _, collection = self._load_client()

        if collection.count() == 0:
            logger.warning("VectorStore vide.")
            return []

        if query_vector is None or query_vector.size == 0:
            logger.warning("Query vector vide.")
            return []

        query_vec = query_vector.astype(np.float32).reshape(1, -1)

        where = _build_chroma_where(filter_meta)

        # Si filtre local, on récupère plus large.
        # Même avec where={organisme_id: ...}, on garde un fetch plus large si
        # d'autres filtres doivent être vérifiés localement.
        n_results = min(
            max(top_k * fetch_multiplier, top_k) if filter_meta else top_k,
            collection.count(),
        )

        query_kwargs = {
            "query_embeddings": query_vec.tolist(),
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            query_kwargs["where"] = where

        result = collection.query(**query_kwargs)

        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        results: list[dict] = []

        for idx, chunk_id in enumerate(ids):
            content = docs[idx] if idx < len(docs) else ""
            meta = metas[idx] if idx < len(metas) else {}
            distance = float(distances[idx]) if idx < len(distances) else 1.0

            restored_meta = _restore_metadata(meta)

            if filter_meta and not _metadata_match(
                restored_meta,
                filter_meta,
                mode=filter_mode,
            ):
                continue

            # Chroma cosine distance : plus petit = mieux.
            # Score simple : 1 - distance.
            score = 1.0 - distance

            chunk = {
                "chunk_id": chunk_id,
                "content": content,
                "metadata": restored_meta,
                "source": restored_meta.get("source", "text"),
                "index": restored_meta.get("chunk_index"),
            }

            results.append(
                {
                    "chunk": chunk,
                    "score": float(score),
                    "rank": len(results) + 1,
                    "chunk_id": chunk_id,
                    "metadata": restored_meta,
                    "content": content,
                    "distance": distance,
                }
            )

            if len(results) >= top_k:
                break

        return results

    def save(self) -> None:
        """
        ChromaDB persiste automatiquement les données avec PersistentClient.
        Cette méthode existe pour compatibilité avec l'ancien store FAISS.
        """
        logger.info("ChromaDB persiste automatiquement dans : %s", self.path)

    def load(self) -> bool:
        """
        Charge ou initialise la collection ChromaDB.

        Retourne True si la collection existe/est prête.
        """
        try:
            self._load_client()
            logger.info(
                "VectorStore ChromaDB chargé : %d chunks",
                self.total_chunks,
            )
            return True
        except Exception as exc:
            logger.error("Erreur chargement ChromaDB : %s", exc)
            return False

    def clear(self, delete_files: bool = False) -> None:
        """
        Vide la collection.

        Si delete_files=True :
          supprime aussi le dossier Chroma local.
        """
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

            # recréer collection vide
            self._load_client()

            logger.info("VectorStore ChromaDB vidé.")

        except Exception as exc:
            logger.warning("Erreur clear ChromaDB : %s", exc)

    def delete_document(self, file_name: str) -> int:
        """
        Supprime tous les chunks d'un document donné.

        Match souple :
          - metadata.file_name exact/contains
          - chunk_id contains file_name
        """
        _, collection = self._load_client()

        target = str(file_name or "").lower().strip()

        if not target:
            return 0

        if collection.count() == 0:
            return 0

        # Récupérer tous les ids + metadata.
        data = collection.get(include=["metadatas"])

        ids = data.get("ids", []) or []
        metadatas = data.get("metadatas", []) or []

        ids_to_delete: list[str] = []

        for chunk_id, meta in zip(ids, metadatas):
            restored_meta = _restore_metadata(meta or {})
            meta_file = str(restored_meta.get("file_name", "")).lower()
            cid = str(chunk_id).lower()

            remove = (
                meta_file == target
                or target in meta_file
                or target in cid
            )

            if remove:
                ids_to_delete.append(chunk_id)

        if not ids_to_delete:
            return 0

        collection.delete(ids=ids_to_delete)

        logger.info(
            "Document '%s' supprimé de ChromaDB : %d chunks retirés",
            file_name,
            len(ids_to_delete),
        )

        return len(ids_to_delete)

    def delete_organisme(self, organisme_id: str) -> int:
        """
        Supprime tous les chunks appartenant à un organisme.

        Utile si on veut réinitialiser l'espace documentaire d'un client
        sans toucher aux autres organismes.
        """
        _, collection = self._load_client()

        target = str(organisme_id or "").lower().strip()

        if not target:
            return 0

        if collection.count() == 0:
            return 0

        data = collection.get(include=["metadatas"])

        ids = data.get("ids", []) or []
        metadatas = data.get("metadatas", []) or []

        ids_to_delete: list[str] = []

        for chunk_id, meta in zip(ids, metadatas):
            restored_meta = _restore_metadata(meta or {})
            meta_organisme_id = str(restored_meta.get("organisme_id", "")).lower().strip()

            if meta_organisme_id == target:
                ids_to_delete.append(chunk_id)

        if not ids_to_delete:
            return 0

        collection.delete(ids=ids_to_delete)

        logger.info(
            "Organisme '%s' supprimé de ChromaDB : %d chunks retirés",
            organisme_id,
            len(ids_to_delete),
        )

        return len(ids_to_delete)


    def count_by_filter(self, filter_meta: dict[str, Any]) -> int:
        """
        Compte les chunks qui correspondent à un filtre metadata.

        Utilisé par EnnoAmel pour vérifier si un document est déjà indexé
        sans refaire Extraction → NLP → RAG.
        """
        _, collection = self._load_client()

        if collection.count() == 0:
            return 0

        where = _build_chroma_where(filter_meta)

        try:
            if where:
                data = collection.get(where=where, include=["metadatas"])
            else:
                data = collection.get(include=["metadatas"])
        except (TypeError, ValueError) as exc:
            # Compatibilité / sécurité : si une ancienne version Chroma refuse
            # le filtre, on récupère les métadonnées puis on filtre localement.
            logger.warning("Filtre Chroma ignoré dans count_by_filter %s : %s", where, exc)
            data = collection.get(include=["metadatas"])

        ids = data.get("ids", []) or []
        metadatas = data.get("metadatas", []) or []

        if not filter_meta:
            return len(ids)

        count = 0
        for meta in metadatas:
            restored_meta = _restore_metadata(meta or {})
            if _metadata_match(restored_meta, filter_meta, mode="exact"):
                count += 1

        return count

    def document_exists(
        self,
        *,
        organisme_id: str,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> bool:
        """
        Vérifie si un document existe déjà dans ChromaDB.

        Priorité : organisme_id + file_hash. Si file_hash absent, utilise document_id.
        """
        filter_meta: dict[str, Any] = {"organisme_id": str(organisme_id or "").strip()}
        if file_hash:
            filter_meta["file_hash"] = str(file_hash).strip()
        elif document_id:
            filter_meta["document_id"] = str(document_id).strip()
        else:
            return False

        return self.count_by_filter(filter_meta) > 0

    def count_document_chunks(
        self,
        *,
        organisme_id: str,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> int:
        """Retourne le nombre de chunks indexés pour un document."""
        filter_meta: dict[str, Any] = {"organisme_id": str(organisme_id or "").strip()}
        if file_hash:
            filter_meta["file_hash"] = str(file_hash).strip()
        elif document_id:
            filter_meta["document_id"] = str(document_id).strip()
        else:
            return 0
        return self.count_by_filter(filter_meta)



    def list_documents(self, organisme_id: Optional[str] = None) -> list[dict[str, Any]]:
        """
        Liste les documents réellement présents dans ChromaDB, sans doublons.

        Déduplication :
          1. file_hash si disponible ;
          2. document_id ;
          3. file_name.

        Retour : une entrée par document, même si plusieurs chunks existent.
        """
        _, collection = self._load_client()

        if collection.count() == 0:
            return []

        target_org = str(organisme_id or "").strip()
        where = _build_chroma_where({"organisme_id": target_org}) if target_org else None

        try:
            if where:
                data = collection.get(where=where, include=["metadatas"])
            else:
                data = collection.get(include=["metadatas"])
        except (TypeError, ValueError) as exc:
            logger.warning("Filtre Chroma ignoré dans list_documents %s : %s", where, exc)
            data = collection.get(include=["metadatas"])

        ids = data.get("ids", []) or []
        metadatas = data.get("metadatas", []) or []

        grouped: dict[str, dict[str, Any]] = {}

        for chunk_id, meta in zip(ids, metadatas):
            restored = _restore_metadata(meta or {})

            org_id = str(restored.get("organisme_id") or "").strip()
            if target_org and org_id != target_org:
                continue

            file_hash = str(restored.get("file_hash") or "").strip()
            document_id = str(restored.get("document_id") or "").strip()
            file_name = str(restored.get("file_name") or "Document sans nom").strip()
            organisme_name = str(restored.get("organisme_name") or "Organisme inconnu").strip()
            domaine = str(restored.get("domaine_principal") or "").strip()
            source = str(restored.get("source") or "").strip()

            # La clé file_hash évite les doublons du même document uploadé plusieurs fois.
            key = file_hash or document_id or f"{org_id}:{file_name}"

            if key not in grouped:
                grouped[key] = {
                    "file_name": file_name,
                    "file_hash": file_hash,
                    "document_id": document_id,
                    "organisme_id": org_id,
                    "organisme_name": organisme_name,
                    "domaine_principal": domaine,
                    "chunks_count": 0,
                    "chunk_ids": [],
                    "sources": set(),
                }

            item = grouped[key]
            item["chunks_count"] += 1
            item["chunk_ids"].append(str(chunk_id))
            if source:
                item["sources"].add(source)
            if not item.get("domaine_principal") and domaine:
                item["domaine_principal"] = domaine
            if not item.get("document_id") and document_id:
                item["document_id"] = document_id
            if not item.get("file_hash") and file_hash:
                item["file_hash"] = file_hash

        docs = []
        for item in grouped.values():
            sources = sorted(item.pop("sources", set()))
            chunk_ids = item.get("chunk_ids", [])
            item["sources"] = sources
            item["first_chunk_id"] = chunk_ids[0] if chunk_ids else ""
            item["last_chunk_id"] = chunk_ids[-1] if chunk_ids else ""
            docs.append(item)

        docs.sort(key=lambda d: (str(d.get("file_name") or "").lower(), str(d.get("document_id") or "")))
        return docs

    @property
    def total_chunks(self) -> int:
        try:
            _, collection = self._load_client()
            return int(collection.count())
        except Exception:
            return 0

    @property
    def is_empty(self) -> bool:
        return self.total_chunks == 0

    @property
    def dims(self) -> Optional[int]:
        return self._dims