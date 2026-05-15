"""
modules/RAG/embedder.py — EnnoSmart RAG v1.1
──────────────────────────────────────────────────────────────────────────────
Génère des embeddings pour les chunks NLP enrichis.

Intégration :
  La sortie du router NLP (NLPResult / to_json) produit des chunks avec :
    - content
    - metadata
    - entities
    - champs R&D structurés

Ce module prend ces chunks et construit un texte enrichi à embedder.

Objectif :
  Ne pas embedder uniquement le texte brut.
  Ajouter aussi les métadonnées NLP utiles afin que le RAG retrouve mieux
  les chunks par mots-clés, entités, méthodes, outils, verrous, métriques, etc.

Modèles recommandés :
  - "BAAI/bge-m3" : recommandé pour EnnoSmart/CIR, FR+EN, multi-domaines.
  - "paraphrase-multilingual-MiniLM-L12-v2" : fallback rapide.
  - "distiluse-base-multilingual-cased-v2" : bon FR/EN, plus léger.
  - "all-MiniLM-L6-v2" : très rapide, mais moins recommandé pour CIR FR.

Usage :
  from modules.RAG.embedder import Embedder

  emb = Embedder()
  vectors = emb.embed_chunks(chunks)
  query_vector = emb.embed_query("Quels sont les verrous techniques ?")
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Recommandé pour EnnoSmart : documents CIR français/anglais multi-domaines.
DEFAULT_MODEL = "BAAI/bge-m3"

# Fallbacks possibles :
# DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
# DEFAULT_MODEL = "distiluse-base-multilingual-cased-v2"
# DEFAULT_MODEL = "all-MiniLM-L6-v2"

CACHE_DIR = Path("modules/RAG/.cache")
BATCH_SIZE = 16


def _safe_str(value: Any) -> str:
    """Convertit proprement une valeur en string."""
    if value is None:
        return ""
    return str(value).strip()


def _flatten_value(value: Any) -> list[str]:
    """
    Convertit une valeur metadata en liste de textes.

    Gère :
      - str
      - list[str]
      - list[dict]
      - dict
      - valeurs simples
    """
    if value is None:
        return []

    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []

    if isinstance(value, (int, float, bool)):
        return [str(value)]

    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_flatten_value(item))
        return out

    if isinstance(value, dict):
        out: list[str] = []

        # Cas fréquent : entity = {"text": "...", "type": "...", "confidence": ...}
        if "text" in value:
            text = _safe_str(value.get("text"))
            entity_type = _safe_str(value.get("type"))
            status = _safe_str(value.get("status"))
            if text:
                if entity_type:
                    out.append(f"{entity_type}: {text}")
                else:
                    out.append(text)
            if status:
                out.append(f"status: {status}")
            return out

        # Cas général : dictionnaire metadata
        for k, v in value.items():
            vals = _flatten_value(v)
            for val in vals:
                if val:
                    out.append(f"{k}: {val}")
        return out

    text = str(value).strip()
    return [text] if text else []


def _unique_keep_order(values: list[str]) -> list[str]:
    """Déduplique en gardant l'ordre."""
    seen = set()
    out = []
    for value in values:
        clean = " ".join(str(value or "").split()).strip()
        if not clean:
            continue
        key = clean.lower()
        if key not in seen:
            seen.add(key)
            out.append(clean)
    return out


def _chunk_to_text(chunk: dict) -> str:
    """
    Construit le texte à embedder depuis un chunk NLP enrichi.

    On combine :
      - content : texte principal du chunk
      - metadata : champs sémantiques clés
      - entities : entités locales typées

    Pourquoi :
      Un chunk peut être important parce que ses métadonnées contiennent
      "JUnit", "LTSpice", "verrou technique", "méthode R&D", etc.,
      même si le texte brut seul n'est pas suffisant pour la recherche.
    """
    parts: list[str] = []

    content = _safe_str(chunk.get("content", ""))
    if content:
        parts.append(content)

    meta = chunk.get("metadata", {}) or {}

    semantic_fields = [
        "mots_cles_high_confidence",
        "mots_cles_candidates",
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
        "composants_techniques",
        "materiaux",
        "equipements",
        "technologies",
        "entities",
    ]

    semantic_parts: list[str] = []

    for field in semantic_fields:
        values = _flatten_value(meta.get(field))
        if values:
            semantic_parts.append(f"{field}: " + ", ".join(_unique_keep_order(values)))

    if semantic_parts:
        parts.append("\n[MÉTADONNÉES NLP]\n" + "\n".join(semantic_parts))

    # Champs documentaires utiles pour recherche/filtres.
    context_fields = [
        "file_name",
        "domaine_principal",
        "source_type",
        "chunk_source_type",
    ]

    context_parts = []
    for field in context_fields:
        value = _safe_str(meta.get(field))
        if value:
            context_parts.append(f"{field}: {value}")

    if context_parts:
        parts.append("\n[CONTEXTE DOCUMENT]\n" + "\n".join(context_parts))

    return "\n\n".join(parts).strip()


def _cache_key(text: str, model_name: str) -> str:
    """Clé stable pour cache embedding."""
    h = hashlib.md5(f"{model_name}::{text}".encode("utf-8")).hexdigest()
    return h


class Embedder:
    """
    Wrapper sentence-transformers avec cache JSON disque.

    Cache :
      - évite de recalculer les mêmes embeddings entre deux runs ;
      - utile pour POC Streamlit ;
      - pour une grande base, ChromaDB gérera ensuite la persistance.

    Paramètres :
      model_name : modèle sentence-transformers.
      device     : "cpu" ou "cuda".
      normalize_embeddings : recommandé True pour cosine similarity.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "cpu",
        cache: bool = True,
        cache_dir: Optional[Path] = None,
        normalize_embeddings: bool = True,
        batch_size: int = BATCH_SIZE,
    ):
        self.model_name = model_name
        self.device = device
        self.use_cache = cache
        self.cache_dir = Path(cache_dir or CACHE_DIR)
        self.normalize_embeddings = normalize_embeddings
        self.batch_size = batch_size

        self._model = None
        self._cache: dict[str, list[float]] = {}

        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._load_cache()

    def _cache_file(self) -> Path:
        safe_name = self.model_name.replace("/", "_").replace("\\", "_")
        norm_flag = "norm" if self.normalize_embeddings else "raw"
        return self.cache_dir / f"{safe_name}_{self.device}_{norm_flag}.json"

    def _load_cache(self) -> None:
        cache_file = self._cache_file()
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
                logger.debug("Cache embeddings chargé : %d entrées", len(self._cache))
            except Exception as exc:
                logger.warning("Cache embeddings illisible : %s", exc)
                self._cache = {}

    def _save_cache(self) -> None:
        if not self.use_cache:
            return

        cache_file = self._cache_file()
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f)
        except Exception as exc:
            logger.warning("Impossible de sauvegarder le cache embeddings : %s", exc)

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers non installé. "
                "Exécute : pip install sentence-transformers"
            ) from exc

        logger.info(
            "Chargement modèle embedding : %s | device=%s",
            self.model_name,
            self.device,
        )

        self._model = SentenceTransformer(self.model_name, device=self.device)

        logger.info(
            "Modèle embedding prêt : %s | dims=%s",
            self.model_name,
            self._model.get_sentence_embedding_dimension(),
        )

        return self._model

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """
        Embeds une liste de textes.

        Retourne :
          np.ndarray shape = (n_texts, dims)
        """
        if not texts:
            return np.array([], dtype=np.float32)

        model = self._load_model()

        results: list[Optional[list[float]]] = []
        to_compute: list[tuple[int, str]] = []

        for i, text in enumerate(texts):
            clean_text = _safe_str(text)
            key = _cache_key(clean_text, self.model_name)

            if self.use_cache and key in self._cache:
                results.append(self._cache[key])
            else:
                results.append(None)
                to_compute.append((i, clean_text))

        if to_compute:
            indices, batch_texts = zip(*to_compute)

            t0 = time.time()

            vectors = model.encode(
                list(batch_texts),
                batch_size=self.batch_size,
                show_progress_bar=len(batch_texts) > 100,
                convert_to_numpy=True,
                normalize_embeddings=self.normalize_embeddings,
            )

            elapsed = time.time() - t0

            logger.debug(
                "Embedding %d textes en %.2fs (%.1f textes/s)",
                len(batch_texts),
                elapsed,
                len(batch_texts) / max(elapsed, 0.001),
            )

            for idx, vec in zip(indices, vectors):
                vec_list = vec.astype(np.float32).tolist()
                results[idx] = vec_list

                if self.use_cache:
                    key = _cache_key(texts[idx], self.model_name)
                    self._cache[key] = vec_list

            self._save_cache()

        # Sécurité : normalement tous les None sont remplacés.
        final = [r for r in results if r is not None]

        if len(final) != len(texts):
            raise RuntimeError(
                f"Erreur embeddings : {len(final)} vecteurs produits pour {len(texts)} textes."
            )

        return np.array(final, dtype=np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        """
        Embeds une seule requête utilisateur.

        Retourne :
          np.ndarray shape = (dims,)
        """
        return self.embed_texts([query])[0]

    def embed_chunks(self, chunks: list[dict]) -> tuple[np.ndarray, list[dict]]:
        """
        Prend les chunks JSON issus de NLPResult.to_json()["chunks"].

        Retourne :
          matrix, valid_chunks

        matrix :
          np.ndarray shape = (n_chunks_valides, dims)

        valid_chunks :
          chunks conservés après filtrage des chunks vides.
        """
        valid_chunks: list[dict] = []
        texts: list[str] = []

        for chunk in chunks or []:
            text = _chunk_to_text(chunk)
            if not text.strip():
                continue

            valid_chunks.append(chunk)
            texts.append(text)

        if not texts:
            logger.warning("Aucun chunk valide à embedder.")
            return np.array([], dtype=np.float32), []

        matrix = self.embed_texts(texts)

        logger.info(
            "Embedded %d chunks → shape %s | modèle=%s | device=%s",
            len(valid_chunks),
            matrix.shape,
            self.model_name,
            self.device,
        )

        return matrix, valid_chunks

    @property
    def dims(self) -> int:
        """Retourne la dimension des embeddings du modèle."""
        model = self._load_model()
        return int(model.get_sentence_embedding_dimension())

    def build_text_for_chunk(self, chunk: dict) -> str:
        """
        Méthode publique utile pour debug :
        voir exactement quel texte est envoyé à l'embedder.
        """
        return _chunk_to_text(chunk)