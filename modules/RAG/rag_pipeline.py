"""
modules/RAG/rag_pipeline.py — EnnoSmart RAG v1.1 ChromaDB + EnnoAmel POC
──────────────────────────────────────────────────────────────────────────────
Pipeline RAG complet :
  - ingestion de JSON NLP enrichis ;
  - stockage ChromaDB ;
  - recherche metadata-aware ;
  - réponse via QueryEngine ;
  - compatible avec EnnoAmel orchestrateur.

Architecture :
  Extraction → NLP → RAG → EnnoAmel

Entrée :
  NLPResult.to_json() produit par modules.NLP.router.to_json()

Sortie :
  RAGResponse avec :
    - answer
    - sources
    - intent
    - recommended_agent
    - chunks_used

Modèles recommandés :
  Embedding : BAAI/bge-m3
  LLM       : ollama:mistral:7b-instruct
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ChromaDB local
DEFAULT_INDEX_DIR = Path("modules/RAG/.chroma")

# Cache embeddings
DEFAULT_CACHE_DIR = Path("modules/RAG/.cache")

# Recommandé pour EnnoSmart/CIR : FR + EN + technique + multi-domaines
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"

# LLM local recommandé pour le POC
DEFAULT_LLM_MODEL = "ollama:mistral:7b-instruct"


class RAGPipeline:
    """
    Pipeline RAG complet pour EnnoSmart.

    Rôle :
      - recevoir le JSON NLP enrichi ;
      - embedder les chunks ;
      - les stocker dans ChromaDB ;
      - rechercher les sources pertinentes ;
      - générer une réponse sourcée via QueryEngine.

    Ce pipeline ne remplace pas les agents.
    Il sert de mémoire documentaire à EnnoAmel.
    """

    def __init__(
        self,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        llm_model: str = DEFAULT_LLM_MODEL,
        index_dir: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        top_k: int = 5,
        min_score: float = -1.0,
        embedding_device: str = "cpu",
        auto_load: bool = True,
    ):
        """
        Paramètres :
          embedding_model  : modèle sentence-transformers.
          llm_model        : "ollama:mistral:7b-instruct".
          index_dir        : dossier ChromaDB local.
          cache_dir        : cache embeddings.
          top_k            : nombre de chunks récupérés par défaut.
          min_score        : score minimum après reranking.
          embedding_device : "cpu" ou "cuda".
          auto_load        : charger/init la base ChromaDB au démarrage.
        """
        from modules.RAG.embedder import Embedder
        from modules.RAG.vector_store import VectorStore
        from modules.RAG.retriever import Retriever
        from modules.RAG.query_engine import QueryEngine

        self.embedder = Embedder(
            model_name=embedding_model,
            device=embedding_device,
            cache=True,
            cache_dir=cache_dir or DEFAULT_CACHE_DIR,
            normalize_embeddings=True,
        )

        self.store = VectorStore(
            path=index_dir or DEFAULT_INDEX_DIR,
        )

        self.retriever = Retriever(
            store=self.store,
            embedder=self.embedder,
            top_k=top_k,
            min_score=min_score,
        )

        self.engine = QueryEngine(
            retriever=self.retriever,
            model=llm_model,
            top_k=top_k,
            min_score=min_score,
        )

        self.embedding_model = embedding_model
        self.embedding_device = embedding_device
        self.llm_model = llm_model
        self.top_k = top_k
        self.min_score = min_score

        if auto_load:
            self.store.load()

        logger.info(
            "RAGPipeline prêt | vector_store=ChromaDB | embedding=%s | device=%s | llm=%s | chunks_indexés=%d",
            embedding_model,
            embedding_device,
            llm_model,
            self.store.total_chunks,
        )

    # ──────────────────────────────────────────────────────────────────────
    # INGESTION
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _merge_document_metadata_into_chunks(nlp_json: dict[str, Any]) -> list[dict]:
        """
        Fusionne document_metadata dans chaque chunk.

        Pourquoi :
          ChromaDB stocke/recherche chunk par chunk.
          Les chunks doivent donc garder les infos globales du document :
            - domaine_principal
            - objet_recherche
            - verrous_techniques
            - methodes_rd
            - outils_technologies
            etc.

        Les métadonnées chunk gardent la priorité si elles existent déjà.
        """
        doc_meta = nlp_json.get("document_metadata", {}) or {}
        chunks = nlp_json.get("chunks", []) or []

        organisme_name = str(doc_meta.get("organisme_name") or "Organisme inconnu").strip()
        organisme_id = str(doc_meta.get("organisme_id") or "organisme_inconnu").strip()
        file_hash = str(doc_meta.get("file_hash") or "").strip()
        document_id = str(doc_meta.get("document_id") or "").strip()

        if not organisme_name:
            organisme_name = "Organisme inconnu"
        if not organisme_id:
            organisme_id = "organisme_inconnu"
        if not document_id and file_hash:
            document_id = f"{organisme_id}_{file_hash[:16]}"

        useful_doc_fields = [
            "file_name",
            "file_category",
            "source_tag",
            "organisme_name",
            "organisme_id",
            "file_hash",
            "document_id",
            "title",
            "author",
            "domaine_principal",
            "domaines_scores",
            "mots_cles_projet",
            "objet_recherche",
            "sous_domaines",
            "verrous_techniques",
            "objectifs_rd",
            "hypotheses_rd",
            "methodes_rd",
            "protocoles_experimentaux",
            "outils_technologies",
            "modeles_algorithmes",
            "architectures_systeme",
            "jeux_donnees_benchmarks",
            "metriques_evaluation",
            "parametres_variables",
            "normes_techniques",
            "materiaux_composants",
            "limitations_perspectives",
            "resultats_rd",
            "livrables",
        ]

        enriched_chunks: list[dict] = []

        for i, chunk in enumerate(chunks):
            content = str(chunk.get("content", "") or "").strip()
            if not content:
                continue

            c = dict(chunk)
            chunk_meta = dict(c.get("metadata", {}) or {})

            # Ajouter metadata document si absent du chunk.
            for field in useful_doc_fields:
                if field not in chunk_meta and field in doc_meta:
                    chunk_meta[field] = doc_meta.get(field)

            # Normalisation champs pratiques.
            chunk_id = (
                c.get("chunk_id")
                or chunk_meta.get("chunk_id")
                or f"{doc_meta.get('file_name', 'doc')}_chunk_{i:04d}"
            )

            c["chunk_id"] = chunk_id
            c["index"] = int(c.get("index", i))
            c["source"] = c.get("source", chunk_meta.get("source", "text"))

            chunk_meta["chunk_id"] = chunk_id
            chunk_meta["chunk_index"] = c["index"]
            chunk_meta["source"] = c["source"]
            chunk_meta["file_name"] = chunk_meta.get("file_name") or doc_meta.get("file_name", "unknown")
            chunk_meta["organisme_name"] = str(chunk_meta.get("organisme_name") or organisme_name)
            chunk_meta["organisme_id"] = str(chunk_meta.get("organisme_id") or organisme_id)
            chunk_meta["file_hash"] = str(chunk_meta.get("file_hash") or file_hash)
            chunk_meta["document_id"] = str(chunk_meta.get("document_id") or document_id)
            c["file_hash"] = chunk_meta["file_hash"]
            c["document_id"] = chunk_meta["document_id"]
            c["organisme_name"] = chunk_meta["organisme_name"]
            c["organisme_id"] = chunk_meta["organisme_id"]
            chunk_meta["domaine_principal"] = chunk_meta.get("domaine_principal") or doc_meta.get(
                "domaine_principal",
                "non_classifié",
            )

            # Simplifier mots_cles_projet pour les filtres/recherche.
            mots_cles = chunk_meta.get("mots_cles_projet")
            if isinstance(mots_cles, dict):
                high = mots_cles.get("high_confidence", []) or []
                cand = mots_cles.get("candidates", []) or []
                chunk_meta.setdefault("mots_cles_high_confidence", high)
                chunk_meta.setdefault("mots_cles_candidates", cand)

            c["metadata"] = chunk_meta
            enriched_chunks.append(c)

        return enriched_chunks

    def ingest(self, nlp_json: dict, save: bool = True) -> int:
        """
        Indexe les chunks d'un document NLP.

        Paramètre :
          nlp_json : sortie de router.to_json(nlp_result)

        Retour :
          nombre de chunks indexés.
        """
        if not isinstance(nlp_json, dict):
            raise TypeError("ingest() attend un dict JSON NLP.")

        chunks = self._merge_document_metadata_into_chunks(nlp_json)

        if not chunks:
            logger.warning("ingest() : aucun chunk valide dans le JSON NLP.")
            return 0

        vectors, embedded_chunks = self.embedder.embed_chunks(chunks)

        if vectors is None or vectors.shape[0] == 0:
            logger.warning("ingest() : embedding a produit 0 vecteurs.")
            return 0

        n = self.store.add(vectors, embedded_chunks)

        # Avec ChromaDB, save() est surtout compatibilité.
        if save:
            self.store.save()

        doc_meta = nlp_json.get("document_metadata", {}) or {}
        file_name = doc_meta.get("file_name", "inconnu")
        organisme_id = doc_meta.get("organisme_id", "organisme_inconnu")
        file_hash = doc_meta.get("file_hash", "")

        logger.info(
            "ingest() : '%s' | organisme=%s | hash=%s → %d chunks indexés | total store=%d",
            file_name,
            organisme_id,
            str(file_hash)[:12] if file_hash else "",
            n,
            self.store.total_chunks,
        )

        return n

    def ingest_file(self, json_path: str | Path, save: bool = True) -> int:
        """
        Charge et indexe un fichier .nlp.json.
        """
        path = Path(json_path)

        if not path.exists():
            raise FileNotFoundError(f"Fichier JSON introuvable : {path}")

        with open(path, "r", encoding="utf-8") as f:
            nlp_json = json.load(f)

        return self.ingest(nlp_json, save=save)

    def ingest_batch(self, nlp_jsons: list[dict], save: bool = True) -> dict[str, int]:
        """
        Indexe plusieurs documents NLP d'un coup.

        Retour :
          {file_name: n_chunks_indexés}
        """
        results: dict[str, int] = {}

        for nlp_json in nlp_jsons:
            doc_meta = nlp_json.get("document_metadata", {}) or {}
            file_name = doc_meta.get("file_name", "inconnu")
            n = self.ingest(nlp_json, save=False)
            results[file_name] = n

        if save:
            self.store.save()

        logger.info(
            "ingest_batch() : %d documents indexés | total store=%d",
            len(nlp_jsons),
            self.store.total_chunks,
        )

        return results

    def ingest_folder(
        self,
        folder: str | Path,
        pattern: str = "**/*.nlp.json",
        save: bool = True,
    ) -> dict[str, int]:
        """
        Indexe tous les fichiers .nlp.json d'un dossier.

        Exemple :
          rag.ingest_folder("projects/")
        """
        folder_path = Path(folder)

        if not folder_path.exists():
            raise FileNotFoundError(f"Dossier introuvable : {folder_path}")

        results: dict[str, int] = {}

        for json_file in folder_path.glob(pattern):
            try:
                n = self.ingest_file(json_file, save=False)
                results[str(json_file)] = n
            except Exception as exc:
                logger.warning("Erreur ingestion %s : %s", json_file, exc)
                results[str(json_file)] = 0

        if save:
            self.store.save()

        return results

    # ──────────────────────────────────────────────────────────────────────
    # RECHERCHE / QUESTION
    # ──────────────────────────────────────────────────────────────────────

    def ask(
        self,
        question: str,
        filter_meta: Optional[dict] = None,
        top_k: Optional[int] = None,
        intent: str = "qa",
        recommended_agent: Optional[str] = "EnnoAmel",
    ):
        """
        Répond à une question en langage naturel.

        Paramètres :
          question          : question utilisateur.
          filter_meta       : filtre metadata optionnel.
          top_k             : nombre de chunks à récupérer.
          intent            : intention détectée par EnnoAmel.
          recommended_agent : agent recommandé.

        Exemple :
          rag.ask(
              "Est-ce que ce projet est éligible CIR ?",
              intent="eligibility",
              recommended_agent="EnnoDiagnostic"
          )
        """
        return self.engine.ask(
            question=question,
            filter_meta=filter_meta,
            top_k=top_k,
            intent=intent,
            recommended_agent=recommended_agent,
        )

    def search(
        self,
        question: str,
        top_k: Optional[int] = None,
        filter_meta: Optional[dict] = None,
        intent: str = "qa",
    ) -> list[dict]:
        """
        Recherche les chunks pertinents sans appeler le LLM.

        Utile pour debug :
          results = rag.search("Quels sont les verrous techniques ?")
        """
        return self.retriever.search(
            query=question,
            top_k=top_k or self.top_k,
            min_score=self.min_score,
            filter_meta=filter_meta,
            intent=intent,
        )

    def search_multi(
        self,
        queries: list[str],
        top_k: Optional[int] = None,
        filter_meta: Optional[dict] = None,
        intent: str = "qa",
    ) -> list[dict]:
        """
        Recherche multi-requêtes.

        Utile pour l'orchestrateur :
          question originale + reformulation.
        """
        return self.retriever.search_multi(
            queries=queries,
            top_k=top_k or self.top_k,
            min_score=self.min_score,
            filter_meta=filter_meta,
            intent=intent,
        )

    # ──────────────────────────────────────────────────────────────────────
    # GESTION INDEX
    # ──────────────────────────────────────────────────────────────────────

    def document_exists(
        self,
        *,
        organisme_id: str,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> bool:
        """Vérifie dans ChromaDB si un document est déjà indexé."""
        if not hasattr(self.store, "document_exists"):
            return False
        return self.store.document_exists(
            organisme_id=organisme_id,
            file_hash=file_hash,
            document_id=document_id,
        )

    def count_document_chunks(
        self,
        *,
        organisme_id: str,
        file_hash: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> int:
        """Nombre de chunks déjà indexés pour un document."""
        if not hasattr(self.store, "count_document_chunks"):
            return 0
        return self.store.count_document_chunks(
            organisme_id=organisme_id,
            file_hash=file_hash,
            document_id=document_id,
        )


    def list_documents(self, organisme_id: Optional[str] = None) -> list[dict[str, Any]]:
        """
        Liste les documents uniques réellement indexés dans ChromaDB.

        Utilisé par Streamlit pour afficher les dossiers déjà disponibles
        sans dépendre du dossier uploads/ et sans créer de doublons.
        """
        if not hasattr(self.store, "list_documents"):
            return []
        return self.store.list_documents(organisme_id=organisme_id)

    def delete_document(self, file_name: str) -> int:
        """
        Supprime tous les chunks d'un document de l'index.
        """
        n = self.store.delete_document(file_name)

        if n > 0:
            self.store.save()

        return n

    def save(self) -> None:
        """
        Compatibilité.
        Avec ChromaDB, la persistance est automatique.
        """
        self.store.save()

    def load(self) -> bool:
        """
        Charge/init la base ChromaDB.
        """
        return self.store.load()

    def clear(self, delete_files: bool = False) -> None:
        """
        Vide le store.

        delete_files=True supprime aussi les fichiers Chroma locaux.
        """
        self.store.clear(delete_files=delete_files)

    # ──────────────────────────────────────────────────────────────────────
    # STATS
    # ──────────────────────────────────────────────────────────────────────

    @property
    def total_chunks(self) -> int:
        return self.store.total_chunks

    @property
    def is_ready(self) -> bool:
        return not self.store.is_empty

    def stats(self) -> dict:
        return {
            "vector_store": "ChromaDB",
            "total_chunks": self.store.total_chunks,
            "embedding_model": self.embedder.model_name,
            "embedding_device": getattr(self.embedder, "device", "unknown"),
            "embedding_dims": self.embedder.dims if self.store.total_chunks > 0 else None,
            "llm_model": self.llm_model,
            "top_k": self.top_k,
            "min_score": self.min_score,
            "index_ready": self.is_ready,
        }