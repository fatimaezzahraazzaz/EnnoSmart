# -*- coding: utf-8 -*-
r"""
modules/RAG/embedder.py — EnnoSmart RAG No-LLM V4

Embedding uniquement.
Pas de LLM.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-m3"
CACHE_DIR = Path("modules/RAG/.cache")
BATCH_SIZE = 16


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _flatten_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        t = value.strip()
        return [t] if t else []
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        out = []
        for x in value:
            out.extend(_flatten_value(x))
        return out
    if isinstance(value, dict):
        out = []
        for key in (
            "text", "phrase", "verrou", "verrou_global", "objectif",
            "methode", "resultat", "contribution", "etat_art",
            "title", "theme", "label", "name",
        ):
            if value.get(key):
                out.extend(_flatten_value(value.get(key)))
        if out:
            return out
        for k, v in value.items():
            vals = _flatten_value(v)
            for val in vals:
                out.append(f"{k}: {val}")
        return out
    return [str(value).strip()] if str(value).strip() else []


def _uniq(values: list[str]) -> list[str]:
    seen, out = set(), []
    for v in values:
        x = " ".join(str(v or "").split()).strip()
        if not x:
            continue
        key = x.lower()
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def chunk_to_embedding_text(chunk: dict) -> str:
    content = _safe_str(chunk.get("content"))
    meta = chunk.get("metadata", {}) or {}

    parts = []
    if content:
        parts.append(content)

    context_fields = [
        "file_name", "document_id", "project_id",
        "chunk_source_type", "field_name", "field_label",
        "role_final", "category",
        "section_family", "section_zone",
        "source_section", "source_title",
        "domaine_principal", "domaine_code",
    ]
    ctx = []
    for f in context_fields:
        if meta.get(f):
            ctx.append(f"{f}: {meta.get(f)}")
    if ctx:
        parts.append("[CONTEXTE]\n" + "\n".join(ctx))

    semantic_fields = [
        "objectif_global", "verrous_globaux", "verrous_prioritaires",
        "objectifs_locaux", "methodes_protocoles", "resultats_importants",
        "parametres_metriques", "etat_art", "demarche_rd_globale",
        "resultats_cles_globaux", "contributions_globales",
        "technical_entities", "materiaux_composants", "equipements",
        "standards_normes", "technologies", "entities",
    ]

    sem = []
    for f in semantic_fields:
        vals = _uniq(_flatten_value(meta.get(f)))
        if vals:
            sem.append(f"{f}: " + ", ".join(vals[:30]))
    if sem:
        parts.append("[MÉTADONNÉES NLP]\n" + "\n".join(sem))

    return "\n\n".join(parts).strip()


def _cache_key(text: str, model_name: str) -> str:
    return hashlib.md5(f"{model_name}::{text}".encode("utf-8")).hexdigest()


class Embedder:
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
        safe = self.model_name.replace("/", "_").replace("\\", "_")
        flag = "norm" if self.normalize_embeddings else "raw"
        return self.cache_dir / f"{safe}_{self.device}_{flag}.json"

    def _load_cache(self):
        path = self._cache_file()
        if path.exists():
            try:
                self._cache = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}

    def _save_cache(self):
        if self.use_cache:
            try:
                self._cache_file().write_text(json.dumps(self._cache), encoding="utf-8")
            except Exception as exc:
                logger.warning("Cache embedding non sauvegardé : %s", exc)

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError("Installe sentence-transformers : pip install sentence-transformers") from exc

        self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.array([], dtype=np.float32)

        model = self._load_model()
        results: list[Optional[list[float]]] = []
        to_compute: list[tuple[int, str]] = []

        for i, t in enumerate(texts):
            t = _safe_str(t)
            key = _cache_key(t, self.model_name)
            if self.use_cache and key in self._cache:
                results.append(self._cache[key])
            else:
                results.append(None)
                to_compute.append((i, t))

        if to_compute:
            indices, batch_texts = zip(*to_compute)
            vectors = model.encode(
                list(batch_texts),
                batch_size=self.batch_size,
                show_progress_bar=len(batch_texts) > 50,
                convert_to_numpy=True,
                normalize_embeddings=self.normalize_embeddings,
            )
            for idx, vec in zip(indices, vectors):
                v = vec.astype(np.float32).tolist()
                results[idx] = v
                if self.use_cache:
                    self._cache[_cache_key(texts[idx], self.model_name)] = v
            self._save_cache()

        final = [r for r in results if r is not None]
        return np.array(final, dtype=np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        return self.embed_texts([query])[0]

    def embed_chunks(self, chunks: list[dict]) -> tuple[np.ndarray, list[dict]]:
        valid, texts = [], []
        for c in chunks or []:
            text = chunk_to_embedding_text(c)
            if text:
                valid.append(c)
                texts.append(text)
        if not valid:
            return np.array([], dtype=np.float32), []
        return self.embed_texts(texts), valid

    @property
    def dims(self) -> int:
        return int(self._load_model().get_sentence_embedding_dimension())
