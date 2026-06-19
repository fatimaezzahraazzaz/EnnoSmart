# -*- coding: utf-8 -*-
"""
EnnoSmart RAG V13.3.1 — Document-first, sans LLM

Objectif :
- Indexer le JSON NLP V13.3.1.
- Prioriser les passages locaux :
  passages_cles_consultant.objectifs_locaux
  passages_cles_consultant.verrous_prioritaires
  passages_cles_consultant.methodes_protocoles
  passages_cles_consultant.resultats_importants
  passages_cles_consultant.parametres_metriques
  passages_cles_consultant.contraintes_techniques
  passages_cles_consultant.criteres_validation
  passages_cles_consultant.contraintes_normatives
- Garder la logique document-first : on ne classe pas par organisme.
- Retourner des passages, pas une réponse LLM.

Dépendances :
    pip install sentence-transformers scikit-learn numpy joblib

Fallback :
- Si sentence-transformers n'est pas disponible, TF-IDF sklearn est utilisé.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


# ---------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------

def clean_text(value: Any) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or "").replace("\ufeff", " ").replace("\xa0", " ")
    ).strip()


def safe_get(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def short_hash(text: str, n: int = 10) -> str:
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:n]


def normalize_id(value: Any) -> str:
    s = clean_text(value)
    if not s:
        return "unknown"
    s = s.lower()
    s = re.sub(r"[^a-z0-9A-Z_\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:120] or "unknown"


def ensure_list(x: Any) -> List[Any]:
    if isinstance(x, list):
        return x
    return []


# ---------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------

@dataclass
class RagChunk:
    chunk_id: str
    text: str
    metadata: Dict[str, Any]


@dataclass
class RagSearchResult:
    chunk_id: str
    score: float
    semantic_score: float
    business_bonus: float
    text: str
    metadata: Dict[str, Any]


# ---------------------------------------------------------------------
# Chunk builder
# ---------------------------------------------------------------------

CONSULTANT_FIELDS = {
    "objectifs_locaux": {
        "label": "Objectif local",
        "role": "objectif",
        "category": "objectif",
        "priority": 1.15,
    },
    "verrous_prioritaires": {
        "label": "Verrou prioritaire",
        "role": "verrou",
        "category": "vrai_verrou_rd",
        "priority": 1.35,
    },
    "methodes_protocoles": {
        "label": "Méthode / protocole",
        "role": "methode",
        "category": "methode",
        "priority": 1.00,
    },
    "resultats_importants": {
        "label": "Résultat important",
        "role": "resultat",
        "category": "resultat",
        "priority": 1.00,
    },
    "parametres_metriques": {
        "label": "Paramètre / métrique",
        "role": "parametre",
        "category": "parametre",
        "priority": 0.90,
    },
    "contraintes_techniques": {
        "label": "Contrainte technique",
        "role": "limite",
        "category": "contrainte_technique",
        "priority": 0.95,
    },
    "criteres_validation": {
        "label": "Critère de validation",
        "role": "methode",
        "category": "critere_validation",
        "priority": 0.95,
    },
    "contraintes_normatives": {
        "label": "Contrainte normative",
        "role": "methode",
        "category": "contrainte_normative",
        "priority": 0.90,
    },
}


def _make_chunk_id(project_id: str, document_id: str, field_name: str, passage_id: str, idx: int) -> str:
    base = f"{project_id}_{document_id}_{field_name}_{passage_id}_{idx}"
    return normalize_id(base) + "_" + short_hash(base, 8)


def _extract_project_id(nlp_json: Dict[str, Any]) -> str:
    return normalize_id(nlp_json.get("project_id") or nlp_json.get("project") or "project")


def _field_item_text(field_name: str, info: Dict[str, Any], item: Dict[str, Any]) -> str:
    text = clean_text(item.get("text") or item.get("verrou_global") or item.get("resume") or item.get("objectif"))
    if not text:
        return ""
    return f"{info['label']} : {text}"


def add_consultant_view_chunks(nlp_json: Dict[str, Any], chunks: List[RagChunk]) -> List[RagChunk]:
    """
    Indexe la nouvelle sortie NLP V13.3.1.
    C'est la partie la plus importante pour les documents bruts.
    """
    project_id = _extract_project_id(nlp_json)

    pc = nlp_json.get("passages_cles_consultant") or {}
    if not isinstance(pc, dict):
        return chunks

    # Cas hybride raw + CIR :
    # passages_cles_consultant peut être {"raw": {...}, "cir": {...}}
    if "raw" in pc and isinstance(pc.get("raw"), dict):
        pc = pc["raw"]

    for field_name, info in CONSULTANT_FIELDS.items():
        items = ensure_list(pc.get(field_name))
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue

            text = _field_item_text(field_name, info, item)
            if not text:
                continue

            document_id = clean_text(item.get("document_id") or item.get("document") or "unknown_document")
            passage_id = clean_text(item.get("passage_id") or f"{field_name}_{idx}")

            metadata = {
                "project_id": project_id,
                "chunk_source_type": "consultant_card",
                "field_name": field_name,
                "field_label": info["label"],
                "field_priority": info["priority"],
                "role_final": item.get("role_final") or info["role"],
                "business_category": item.get("business_category") or info["category"],
                "category": item.get("category") or info["category"],
                "importance_score": item.get("importance_score"),
                "importance_level": item.get("importance_level"),
                "business_score": item.get("business_score"),
                "fastjudge_confidence": item.get("fastjudge_confidence"),
                "verrou_score": item.get("verrou_score"),
                "verrou_level": item.get("verrou_level"),
                "semantic_label": item.get("semantic_label"),
                "semantic_score": item.get("semantic_score"),
                "document": item.get("document"),
                "file_name": item.get("document"),
                "document_id": document_id,
                "passage_id": passage_id,
                "page_number": item.get("page_number"),
                "source_path": item.get("source_path"),
                "source_section": item.get("source_section"),
                "source_title": item.get("source_title"),
                "source_type": item.get("source_type"),
            }

            chunks.append(
                RagChunk(
                    chunk_id=_make_chunk_id(project_id, document_id, field_name, passage_id, idx),
                    text=text,
                    metadata=metadata,
                )
            )

    return chunks


def add_global_field_chunks(nlp_json: Dict[str, Any], chunks: List[RagChunk]) -> List[RagChunk]:
    """
    Compatibilité avec l'ancien JSON :
    - objectif_global
    - verrous_globaux
    - demarche_rd_globale
    - resultats_cles_globaux
    """
    project_id = _extract_project_id(nlp_json)

    # objectif_global
    obj = nlp_json.get("objectif_global")
    if isinstance(obj, dict):
        text = clean_text(obj.get("resume"))
        if text:
            chunks.append(
                RagChunk(
                    chunk_id=_make_chunk_id(project_id, "global", "objectif_global", "objectif_global", 0),
                    text=f"Objectif global : {text}",
                    metadata={
                        "project_id": project_id,
                        "chunk_source_type": "global_card",
                        "field_name": "objectif_global",
                        "field_label": "Objectif global",
                        "role_final": "objectif",
                        "business_category": "objectif",
                        "importance_score": obj.get("niveau_confiance"),
                        "document_id": "global",
                    },
                )
            )

    # verrous_globaux
    vg = nlp_json.get("verrous_globaux")
    if isinstance(vg, dict):
        for i, item in enumerate(ensure_list(vg.get("verrous"))):
            if not isinstance(item, dict):
                continue
            text = clean_text(item.get("verrou_global") or item.get("text"))
            if not text:
                continue
            document_id = clean_text(item.get("document_id") or item.get("document") or "global")
            chunks.append(
                RagChunk(
                    chunk_id=_make_chunk_id(project_id, document_id, "verrous_globaux", item.get("id", i), i),
                    text=f"Verrou global/local : {text}",
                    metadata={
                        "project_id": project_id,
                        "chunk_source_type": "global_card",
                        "field_name": "verrous_globaux",
                        "field_label": "Verrous globaux/locaux",
                        "role_final": "verrou",
                        "business_category": item.get("business_category") or "vrai_verrou_rd",
                        "importance_score": item.get("niveau_confiance"),
                        "document": item.get("document"),
                        "file_name": item.get("document"),
                        "document_id": document_id,
                        "source_section": item.get("source_section"),
                        "source_title": item.get("source_title"),
                    },
                )
            )

    # blocs avec items
    block_mapping = {
        "demarche_rd_globale": ("Démarche / travaux R&D", "methode", "methode"),
        "resultats_cles_globaux": ("Résultat clé", "resultat", "resultat"),
        "limites_incertitudes_globales": ("Limite / incertitude", "limite", "vrai_verrou_rd"),
        "contributions_globales": ("Contribution", "contribution", "resultat"),
    }

    for field_name, (label, role, category) in block_mapping.items():
        block = nlp_json.get(field_name)
        if not isinstance(block, dict):
            continue
        for i, item in enumerate(ensure_list(block.get("items"))):
            if not isinstance(item, dict):
                continue
            text = clean_text(item.get("text") or item.get("methode") or item.get("resultat") or item.get("limite") or item.get("contribution"))
            if not text:
                continue
            document_id = clean_text(item.get("document_id") or item.get("document") or "global")
            chunks.append(
                RagChunk(
                    chunk_id=_make_chunk_id(project_id, document_id, field_name, i, i),
                    text=f"{label} : {text}",
                    metadata={
                        "project_id": project_id,
                        "chunk_source_type": "global_card",
                        "field_name": field_name,
                        "field_label": label,
                        "role_final": role,
                        "business_category": category,
                        "importance_score": item.get("importance_score"),
                        "importance_level": item.get("importance_level"),
                        "document": item.get("document"),
                        "file_name": item.get("document"),
                        "document_id": document_id,
                    },
                )
            )

    return chunks


def add_evidence_chunks(nlp_json: Dict[str, Any], chunks: List[RagChunk], max_evidences: int = 800) -> List[RagChunk]:
    """
    Fallback : indexe les évidences modèle validées si besoin.
    Utile si passages_cles_consultant est vide.
    """
    project_id = _extract_project_id(nlp_json)
    evidences = ensure_list(nlp_json.get("evidences_model_validated"))

    for i, item in enumerate(evidences[:max_evidences]):
        if not isinstance(item, dict):
            continue
        text = clean_text(item.get("text"))
        if not text:
            continue

        role = clean_text(item.get("role_final") or item.get("role") or "unknown")
        document_id = clean_text(item.get("document_id") or item.get("document") or "unknown_document")
        passage_id = clean_text(item.get("passage_id") or f"evidence_{i}")

        chunks.append(
            RagChunk(
                chunk_id=_make_chunk_id(project_id, document_id, "evidence", passage_id, i),
                text=f"Passage validé [{role}] : {text}",
                metadata={
                    "project_id": project_id,
                    "chunk_source_type": "evidence_card",
                    "field_name": "evidences_model_validated",
                    "field_label": "Passage validé",
                    "role_final": role,
                    "business_category": item.get("business_category"),
                    "importance_score": item.get("importance_score"),
                    "importance_level": item.get("importance_level"),
                    "business_score": item.get("business_score"),
                    "fastjudge_confidence": item.get("fastjudge_confidence"),
                    "verrou_score": item.get("verrou_score"),
                    "verrou_level": item.get("verrou_level"),
                    "semantic_label": item.get("semantic_label"),
                    "semantic_score": item.get("semantic_score"),
                    "document": item.get("document"),
                    "file_name": item.get("document"),
                    "document_id": document_id,
                    "passage_id": passage_id,
                    "page_number": item.get("page_number"),
                    "source_path": item.get("source_path"),
                    "source_section": item.get("source_section"),
                    "source_title": item.get("source_title"),
                    "source_type": item.get("source_type"),
                },
            )
        )

    return chunks


def build_chunks_from_nlp_json(nlp_json: Dict[str, Any]) -> List[RagChunk]:
    chunks: List[RagChunk] = []

    # Priorité V13.3.1
    chunks = add_consultant_view_chunks(nlp_json, chunks)

    # Compatibilité / fallback
    chunks = add_global_field_chunks(nlp_json, chunks)

    # Evidences en secours
    chunks = add_evidence_chunks(nlp_json, chunks)

    # Dédup par texte + document + field
    seen = set()
    out: List[RagChunk] = []
    for ch in chunks:
        sig = (
            clean_text(ch.metadata.get("document_id")),
            clean_text(ch.metadata.get("field_name")),
            clean_text(ch.text).lower()[:700],
        )
        if sig in seen:
            continue
        seen.add(sig)
        out.append(ch)

    return out


# ---------------------------------------------------------------------
# Embedding backend
# ---------------------------------------------------------------------

class EmbeddingBackend:
    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL, force_tfidf: bool = False):
        self.model_name = model_name
        self.force_tfidf = force_tfidf
        self.backend_name = ""
        self.model = None
        self.vectorizer = None

    def fit_transform(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)

        if not self.force_tfidf:
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer(self.model_name, device="cpu")
                emb = self.model.encode(
                    texts,
                    batch_size=32,
                    show_progress_bar=len(texts) > 200,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                )
                self.backend_name = "sentence_transformers"
                return emb.astype(np.float32)
            except Exception as exc:
                self.backend_name = f"tfidf_fallback_after_error:{type(exc).__name__}"

        from sklearn.feature_extraction.text import TfidfVectorizer
        self.vectorizer = TfidfVectorizer(
            lowercase=True,
            strip_accents="unicode",
            ngram_range=(1, 2),
            min_df=1,
            max_features=70000,
        )
        X = self.vectorizer.fit_transform(texts).astype(np.float32)
        self.backend_name = self.backend_name or "tfidf"
        return X

    def transform(self, texts: List[str]):
        if self.model is not None:
            emb = self.model.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            return emb.astype(np.float32)

        if self.vectorizer is None:
            raise RuntimeError("EmbeddingBackend not fitted.")

        return self.vectorizer.transform(texts).astype(np.float32)


# ---------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------

class RagIndex:
    def __init__(
        self,
        chunks: List[RagChunk],
        matrix: Any,
        backend: EmbeddingBackend,
        project_id: str = "",
    ):
        self.chunks = chunks
        self.matrix = matrix
        self.backend = backend
        self.project_id = project_id

    @property
    def is_ready(self) -> bool:
        return bool(self.chunks) and self.matrix is not None

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path: str | Path) -> "RagIndex":
        with Path(path).open("rb") as f:
            return pickle.load(f)


def build_rag_index_from_nlp(
    nlp_json: Dict[str, Any],
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    force_tfidf: bool = False,
) -> RagIndex:
    chunks = build_chunks_from_nlp_json(nlp_json)
    texts = [c.text for c in chunks]
    backend = EmbeddingBackend(model_name=embedding_model, force_tfidf=force_tfidf)
    matrix = backend.fit_transform(texts)
    return RagIndex(chunks=chunks, matrix=matrix, backend=backend, project_id=_extract_project_id(nlp_json))


def load_nlp_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_rag_index_from_nlp_file(
    nlp_json_path: str | Path,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    force_tfidf: bool = False,
) -> RagIndex:
    return build_rag_index_from_nlp(
        load_nlp_json(nlp_json_path),
        embedding_model=embedding_model,
        force_tfidf=force_tfidf,
    )


# ---------------------------------------------------------------------
# Search / scoring
# ---------------------------------------------------------------------

def _cosine_scores(matrix: Any, q_vec: Any) -> np.ndarray:
    # sentence-transformers: dense normalized embeddings
    if isinstance(matrix, np.ndarray):
        q = q_vec[0] if isinstance(q_vec, np.ndarray) and q_vec.ndim == 2 else q_vec
        return np.asarray(matrix @ q.T).reshape(-1)

    # sparse TF-IDF
    return np.asarray(matrix @ q_vec.T).reshape(-1)


def detect_query_intent(query: str) -> str:
    q = query.lower()
    if any(w in q for w in ["verrou", "difficult", "incertitude", "limite", "blocage", "problème", "probleme"]):
        return "verrous"
    if any(w in q for w in ["objectif", "but", "vise", "finalité", "finalite"]):
        return "objectifs"
    if any(w in q for w in ["méthode", "methode", "protocole", "essai", "test", "travaux", "démarche", "demarche"]):
        return "methodes"
    if any(w in q for w in ["résultat", "resultat", "conclusion", "obtenu", "mesure", "performance"]):
        return "resultats"
    if any(w in q for w in ["critère", "critere", "validation", "acceptation", "conform"]):
        return "criteres_validation"
    if any(w in q for w in ["norme", "normatif", "référentiel", "referentiel", "standard", "réglement", "reglement"]):
        return "contraintes_normatives"
    return "general"


def rag_business_bonus(query: str, metadata: Dict[str, Any]) -> float:
    intent = detect_query_intent(query)
    field = clean_text(metadata.get("field_name"))
    role = clean_text(metadata.get("role_final"))
    category = clean_text(metadata.get("business_category") or metadata.get("category"))
    source_type = clean_text(metadata.get("chunk_source_type"))

    bonus = 0.0

    # Favoriser les cartes consultant V13.
    if source_type == "consultant_card":
        bonus += 0.05

    if intent == "verrous":
        if field in {"verrous_prioritaires", "verrous_globaux"}:
            bonus += 0.35
        if category == "vrai_verrou_rd":
            bonus += 0.25
        if role in {"verrou", "limite"}:
            bonus += 0.15

    elif intent == "objectifs":
        if field in {"objectifs_locaux", "objectif_global"}:
            bonus += 0.35
        if role == "objectif" or category == "objectif":
            bonus += 0.25

    elif intent == "methodes":
        if field in {"methodes_protocoles", "demarche_rd_globale"}:
            bonus += 0.30
        if role == "methode" or category == "methode":
            bonus += 0.20

    elif intent == "resultats":
        if field in {"resultats_importants", "resultats_cles_globaux"}:
            bonus += 0.30
        if role == "resultat" or category == "resultat":
            bonus += 0.20

    elif intent == "criteres_validation":
        if field == "criteres_validation":
            bonus += 0.35
        if category == "critere_validation":
            bonus += 0.30

    elif intent == "contraintes_normatives":
        if field == "contraintes_normatives":
            bonus += 0.35
        if category == "contrainte_normative":
            bonus += 0.30

    # Bonus importance
    try:
        imp = float(metadata.get("importance_score") or metadata.get("business_score") or 0)
        bonus += min(0.10, imp * 0.08)
    except Exception:
        pass

    return float(bonus)


def search_rag(
    index: RagIndex,
    query: str,
    top_k: int = 5,
    project_id: Optional[str] = None,
    min_score: float = -1.0,
) -> List[RagSearchResult]:
    if not index or not index.is_ready:
        return []

    query = clean_text(query)
    if not query:
        return []

    q_vec = index.backend.transform([query])
    semantic_scores = _cosine_scores(index.matrix, q_vec)

    results: List[RagSearchResult] = []
    for i, ch in enumerate(index.chunks):
        if project_id and clean_text(ch.metadata.get("project_id")) != clean_text(project_id):
            continue

        sem = float(semantic_scores[i])
        bonus = rag_business_bonus(query, ch.metadata)
        final = sem + bonus

        if final < min_score:
            continue

        results.append(
            RagSearchResult(
                chunk_id=ch.chunk_id,
                score=round(final, 4),
                semantic_score=round(sem, 4),
                business_bonus=round(bonus, 4),
                text=ch.text,
                metadata=ch.metadata,
            )
        )

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_k]


def answer_rag_extractive(
    index: RagIndex,
    query: str,
    top_k: int = 5,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    results = search_rag(index=index, query=query, top_k=top_k, project_id=project_id)
    intent = detect_query_intent(query)

    if not results:
        return {
            "answer": "Aucun passage pertinent trouvé dans l'index RAG.",
            "query": query,
            "intent": intent,
            "sources": [],
            "chunks_used": 0,
            "llm_used": False,
        }

    lines = ["Passages les plus pertinents retrouvés par le RAG :"]
    sources = []

    for i, r in enumerate(results, start=1):
        md = r.metadata
        label = md.get("field_label") or md.get("field_name") or "Passage"
        doc = md.get("document") or md.get("file_name") or md.get("document_id") or ""
        role = md.get("role_final") or ""
        cat = md.get("business_category") or md.get("category") or ""

        passage = clean_text(r.text)
        if ":" in passage:
            passage = passage.split(":", 1)[1].strip()

        lines.append(
            f"\n{i}. [{label}] score={r.score} | semantic={r.semantic_score} | bonus={r.business_bonus}\n"
            f"Source : {doc}\n"
            f"Rôle : {role} | catégorie : {cat}\n"
            f"Passage : {passage}"
        )

        sources.append({
            "rank": i,
            "chunk_id": r.chunk_id,
            "score": r.score,
            "semantic_score": r.semantic_score,
            "business_bonus": r.business_bonus,
            "text": r.text,
            "metadata": r.metadata,
        })

    return {
        "answer": "\n".join(lines),
        "query": query,
        "intent": intent,
        "sources": sources,
        "chunks_used": len(sources),
        "llm_used": False,
    }


# ---------------------------------------------------------------------
# Debug CLI
# ---------------------------------------------------------------------

def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--nlp-json", required=True)
    ap.add_argument("--query", required=True)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--tfidf", action="store_true")
    args = ap.parse_args()

    idx = build_rag_index_from_nlp_file(args.nlp_json, force_tfidf=args.tfidf)
    print("Chunks:", len(idx.chunks))
    print("Backend:", idx.backend.backend_name)

    out = answer_rag_extractive(idx, args.query, top_k=args.top_k)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
