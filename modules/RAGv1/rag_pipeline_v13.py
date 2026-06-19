# -*- coding: utf-8 -*-
"""
EnnoSmart RAG V13.3.1 — Document-first, sans LLM
Correction TF-IDF : conversion csr_matrix -> ndarray avant float().
"""
from __future__ import annotations

import hashlib
import json
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\ufeff", " ").replace("\xa0", " ")).strip()


def normalize_id(value: Any) -> str:
    s = clean_text(value).lower()
    if not s:
        return "unknown"
    s = re.sub(r"[^a-z0-9_\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:120] or "unknown"


def short_hash(text: str, n: int = 8) -> str:
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:n]


def ensure_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


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


CONSULTANT_FIELDS = {
    "objectifs_locaux": {"label": "Objectif local", "role": "objectif", "category": "objectif", "priority": 1.15},
    "verrous_prioritaires": {"label": "Verrou prioritaire", "role": "verrou", "category": "vrai_verrou_rd", "priority": 1.35},
    "methodes_protocoles": {"label": "Méthode / protocole", "role": "methode", "category": "methode", "priority": 1.0},
    "resultats_importants": {"label": "Résultat important", "role": "resultat", "category": "resultat", "priority": 1.0},
    "parametres_metriques": {"label": "Paramètre / métrique", "role": "parametre", "category": "parametre", "priority": 0.9},
    "contraintes_techniques": {"label": "Contrainte technique", "role": "limite", "category": "contrainte_technique", "priority": 0.95},
    "criteres_validation": {"label": "Critère de validation", "role": "methode", "category": "critere_validation", "priority": 0.95},
    "contraintes_normatives": {"label": "Contrainte normative", "role": "methode", "category": "contrainte_normative", "priority": 0.9},
}


def _project_id(nlp_json: Dict[str, Any]) -> str:
    return normalize_id(nlp_json.get("project_id") or nlp_json.get("project") or "project")


def _chunk_id(project_id: str, document_id: str, field: str, passage_id: str, idx: int) -> str:
    base = f"{project_id}_{document_id}_{field}_{passage_id}_{idx}"
    return normalize_id(base) + "_" + short_hash(base)


def _item_text(info: Dict[str, Any], item: Dict[str, Any]) -> str:
    txt = clean_text(item.get("text") or item.get("verrou_global") or item.get("resume") or item.get("objectif") or item.get("resultat") or item.get("methode"))
    return f"{info['label']} : {txt}" if txt else ""


def add_consultant_view_chunks(nlp_json: Dict[str, Any], chunks: List[RagChunk]) -> List[RagChunk]:
    pid = _project_id(nlp_json)
    pc = nlp_json.get("passages_cles_consultant") or {}
    if not isinstance(pc, dict):
        return chunks
    if isinstance(pc.get("raw"), dict):
        pc = pc["raw"]

    for field_name, info in CONSULTANT_FIELDS.items():
        for idx, item in enumerate(ensure_list(pc.get(field_name))):
            if not isinstance(item, dict):
                continue
            text = _item_text(info, item)
            if not text:
                continue
            doc_id = clean_text(item.get("document_id") or item.get("document") or "unknown_document")
            passage_id = clean_text(item.get("passage_id") or f"{field_name}_{idx}")
            md = {
                "project_id": pid,
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
                "document_id": doc_id,
                "passage_id": passage_id,
                "page_number": item.get("page_number"),
                "source_path": item.get("source_path"),
                "source_section": item.get("source_section"),
                "source_title": item.get("source_title"),
                "source_type": item.get("source_type"),
            }
            chunks.append(RagChunk(_chunk_id(pid, doc_id, field_name, passage_id, idx), text, md))
    return chunks


def add_global_field_chunks(nlp_json: Dict[str, Any], chunks: List[RagChunk]) -> List[RagChunk]:
    pid = _project_id(nlp_json)
    obj = nlp_json.get("objectif_global")
    if isinstance(obj, dict) and clean_text(obj.get("resume")):
        chunks.append(RagChunk(_chunk_id(pid, "global", "objectif_global", "objectif_global", 0),
            f"Objectif global : {clean_text(obj.get('resume'))}",
            {"project_id": pid, "chunk_source_type": "global_card", "field_name": "objectif_global", "field_label": "Objectif global", "role_final": "objectif", "business_category": "objectif", "document_id": "global"}))

    vg = nlp_json.get("verrous_globaux")
    if isinstance(vg, dict):
        for i, item in enumerate(ensure_list(vg.get("verrous"))):
            if not isinstance(item, dict):
                continue
            txt = clean_text(item.get("verrou_global") or item.get("text"))
            if not txt:
                continue
            doc_id = clean_text(item.get("document_id") or item.get("document") or "global")
            chunks.append(RagChunk(_chunk_id(pid, doc_id, "verrous_globaux", item.get("id", i), i),
                f"Verrou global/local : {txt}",
                {"project_id": pid, "chunk_source_type": "global_card", "field_name": "verrous_globaux", "field_label": "Verrous globaux/locaux", "role_final": "verrou", "business_category": item.get("business_category") or "vrai_verrou_rd", "document": item.get("document"), "file_name": item.get("document"), "document_id": doc_id}))
    return chunks


def add_evidence_chunks(nlp_json: Dict[str, Any], chunks: List[RagChunk], max_evidences: int = 800) -> List[RagChunk]:
    pid = _project_id(nlp_json)
    for i, item in enumerate(ensure_list(nlp_json.get("evidences_model_validated"))[:max_evidences]):
        if not isinstance(item, dict):
            continue
        txt = clean_text(item.get("text"))
        if not txt:
            continue
        role = clean_text(item.get("role_final") or item.get("role") or "unknown")
        doc_id = clean_text(item.get("document_id") or item.get("document") or "unknown_document")
        passage_id = clean_text(item.get("passage_id") or f"evidence_{i}")
        chunks.append(RagChunk(_chunk_id(pid, doc_id, "evidence", passage_id, i),
            f"Passage validé [{role}] : {txt}",
            {"project_id": pid, "chunk_source_type": "evidence_card", "field_name": "evidences_model_validated", "field_label": "Passage validé", "role_final": role, "business_category": item.get("business_category"), "document": item.get("document"), "file_name": item.get("document"), "document_id": doc_id, "passage_id": passage_id}))
    return chunks


def build_chunks_from_nlp_json(nlp_json: Dict[str, Any]) -> List[RagChunk]:
    chunks: List[RagChunk] = []
    add_consultant_view_chunks(nlp_json, chunks)
    add_global_field_chunks(nlp_json, chunks)
    add_evidence_chunks(nlp_json, chunks)
    seen, out = set(), []
    for ch in chunks:
        sig = (clean_text(ch.metadata.get("document_id")), clean_text(ch.metadata.get("field_name")), clean_text(ch.text).lower()[:700])
        if sig in seen:
            continue
        seen.add(sig)
        out.append(ch)
    return out


class EmbeddingBackend:
    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL, force_tfidf: bool = False):
        self.model_name = model_name
        self.force_tfidf = force_tfidf
        self.backend_name = ""
        self.model = None
        self.vectorizer = None

    def fit_transform(self, texts: List[str]) -> Any:
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        if not self.force_tfidf:
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer(self.model_name, device="cpu")
                emb = self.model.encode(texts, batch_size=32, show_progress_bar=len(texts) > 200, convert_to_numpy=True, normalize_embeddings=True)
                self.backend_name = "sentence_transformers"
                return emb.astype(np.float32)
            except Exception as exc:
                self.backend_name = f"tfidf_fallback_after_error:{type(exc).__name__}"

        from sklearn.feature_extraction.text import TfidfVectorizer
        self.vectorizer = TfidfVectorizer(lowercase=True, strip_accents="unicode", ngram_range=(1, 2), min_df=1, max_features=70000, norm="l2")
        self.backend_name = self.backend_name or "tfidf"
        return self.vectorizer.fit_transform(texts).astype(np.float32)

    def transform(self, texts: List[str]) -> Any:
        if self.model is not None:
            emb = self.model.encode(texts, batch_size=32, show_progress_bar=False, convert_to_numpy=True, normalize_embeddings=True)
            return emb.astype(np.float32)
        if self.vectorizer is None:
            raise RuntimeError("EmbeddingBackend not fitted.")
        return self.vectorizer.transform(texts).astype(np.float32)


class RagIndex:
    def __init__(self, chunks: List[RagChunk], matrix: Any, backend: EmbeddingBackend, project_id: str = ""):
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


def build_rag_index_from_nlp(nlp_json: Dict[str, Any], embedding_model: str = DEFAULT_EMBEDDING_MODEL, force_tfidf: bool = False) -> RagIndex:
    chunks = build_chunks_from_nlp_json(nlp_json)
    backend = EmbeddingBackend(model_name=embedding_model, force_tfidf=force_tfidf)
    matrix = backend.fit_transform([c.text for c in chunks])
    return RagIndex(chunks, matrix, backend, _project_id(nlp_json))


def load_nlp_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_rag_index_from_nlp_file(nlp_json_path: str | Path, embedding_model: str = DEFAULT_EMBEDDING_MODEL, force_tfidf: bool = False) -> RagIndex:
    return build_rag_index_from_nlp(load_nlp_json(nlp_json_path), embedding_model=embedding_model, force_tfidf=force_tfidf)


def _cosine_scores(matrix: Any, q_vec: Any) -> np.ndarray:
    """
    Fix important : retourne toujours np.ndarray 1D de floats.
    - sentence-transformers : matrix est dense np.ndarray.
    - TF-IDF : matrix @ q_vec.T retourne csr_matrix, donc conversion .toarray().
    """
    if isinstance(matrix, np.ndarray):
        q = q_vec[0] if isinstance(q_vec, np.ndarray) and q_vec.ndim == 2 else q_vec
        return np.asarray(matrix @ q.T, dtype=float).reshape(-1)
    scores = matrix @ q_vec.T
    if hasattr(scores, "toarray"):
        scores = scores.toarray()
    return np.asarray(scores, dtype=float).reshape(-1)


def detect_query_intent(query: str) -> str:
    q = query.lower()

    if any(w in q for w in [
        "contrainte", "contraintes",
        "confidentialité", "confidentialite",
        "données sensibles", "donnees sensibles",
        "ressources", "calcul limité", "calcul limite",
        "local", "api", "cloud",
        "open-source", "open source",
        "token", "fenêtre de contexte", "fenetre de contexte",
    ]):
        return "contraintes"

    if any(w in q for w in ["verrou", "difficult", "incertitude", "limite", "blocage", "problème", "probleme"]):
        return "verrous"

    if any(w in q for w in ["objectif", "objectifs", "but", "vise", "visé", "vises", "finalité", "finalite"]):
        return "objectifs"

    if any(w in q for w in ["méthode", "methode", "protocole", "essai", "test", "travaux", "démarche", "demarche", "expérience", "experience"]):
        return "methodes"

    if any(w in q for w in [
        "résultat", "resultat", "résultats", "resultats",
        "métrique", "metrique", "métriques", "metriques",
        "performance", "coverage", "couverture",
        "compilation", "compilabilité", "compilabilite",
        "smells", "assert"
    ]):
        return "resultats"

    if any(w in q for w in ["critère", "critere", "critères", "criteres", "validation", "acceptation", "conform", "qualité", "qualite", "évaluation", "evaluation"]):
        return "criteres_validation"

    if any(w in q for w in ["norme", "normatif", "référentiel", "referentiel", "standard", "réglement", "reglement"]):
        return "contraintes_normatives"

    return "general"


def rag_business_bonus(query: str, metadata: Dict[str, Any]) -> float:
    intent = detect_query_intent(query)
    field = clean_text(metadata.get("field_name"))
    role = clean_text(metadata.get("role_final"))
    cat = clean_text(metadata.get("business_category") or metadata.get("category"))
    source = clean_text(metadata.get("chunk_source_type"))
    bonus = 0.05 if source == "consultant_card" else 0.0

    if intent == "verrous":
        if field in {"verrous_prioritaires", "verrous_globaux"}: bonus += 0.35
        if cat == "vrai_verrou_rd": bonus += 0.25
        if role in {"verrou", "limite"}: bonus += 0.15
    elif intent == "objectifs":
        if field in {"objectifs_locaux", "objectif_global"}: bonus += 0.35
        if role == "objectif" or cat == "objectif": bonus += 0.25
    elif intent == "methodes":
        if field in {"methodes_protocoles", "demarche_rd_globale"}: bonus += 0.30
        if role == "methode" or cat == "methode": bonus += 0.20
    elif intent == "resultats":
        if field in {"resultats_importants", "resultats_cles_globaux", "parametres_metriques"}: bonus += 0.30
        if role in {"resultat", "parametre"} or cat in {"resultat", "parametre"}: bonus += 0.20
    elif intent == "criteres_validation":
        if field == "criteres_validation": bonus += 0.35
        if cat == "critere_validation": bonus += 0.30
        if field in {"parametres_metriques", "methodes_protocoles"}: bonus += 0.05
    elif intent == "contraintes":
        if field in {"contraintes_techniques", "verrous_prioritaires"}:
            bonus += 0.35
        if cat in {"contrainte_technique", "vrai_verrou_rd"}:
            bonus += 0.20
        if role in {"limite", "verrou"}:
            bonus += 0.15

    elif intent == "contraintes_normatives":
        if field == "contraintes_normatives": bonus += 0.35
        if cat == "contrainte_normative": bonus += 0.30

    imp = safe_float(metadata.get("importance_score") or metadata.get("business_score"), 0.0)
    bonus += min(0.10, imp * 0.08)
    return float(bonus)


def search_rag(index: RagIndex, query: str, top_k: int = 5, project_id: Optional[str] = None, min_score: float = -1.0) -> List[RagSearchResult]:
    if not index or not index.is_ready:
        return []
    query = clean_text(query)
    if not query:
        return []
    q_vec = index.backend.transform([query])
    semantic_scores = np.asarray(_cosine_scores(index.matrix, q_vec), dtype=float).reshape(-1)

    results: List[RagSearchResult] = []
    for i, ch in enumerate(index.chunks):
        if i >= len(semantic_scores):
            continue
        if project_id and clean_text(ch.metadata.get("project_id")) != clean_text(project_id):
            continue
        sem = float(semantic_scores[i])
        bonus = rag_business_bonus(query, ch.metadata)
        final = sem + bonus
        if final < min_score:
            continue
        results.append(RagSearchResult(ch.chunk_id, round(final, 4), round(sem, 4), round(bonus, 4), ch.text, ch.metadata))
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_k]


def answer_rag_extractive(index: RagIndex, query: str, top_k: int = 5, project_id: Optional[str] = None) -> Dict[str, Any]:
    results = search_rag(index=index, query=query, top_k=top_k, project_id=project_id)
    intent = detect_query_intent(query)
    if not results:
        return {"answer": "Aucun passage pertinent trouvé dans l'index RAG.", "query": query, "intent": intent, "sources": [], "chunks_used": 0, "llm_used": False}

    lines = ["Passages les plus pertinents retrouvés par le RAG :"]
    sources = []
    for rank, r in enumerate(results, 1):
        md = r.metadata
        label = md.get("field_label") or md.get("field_name") or "Passage"
        doc = md.get("document") or md.get("file_name") or md.get("document_id") or ""
        role = md.get("role_final") or ""
        cat = md.get("business_category") or md.get("category") or ""
        passage = clean_text(r.text)
        if ":" in passage:
            passage = passage.split(":", 1)[1].strip()
        lines.append(f"\n{rank}. [{label}] score={r.score} | semantic={r.semantic_score} | bonus={r.business_bonus}\nSource : {doc}\nRôle : {role} | catégorie : {cat}\nPassage : {passage}")
        sources.append({"rank": rank, "chunk_id": r.chunk_id, "score": r.score, "semantic_score": r.semantic_score, "business_bonus": r.business_bonus, "text": r.text, "metadata": r.metadata})

    return {"answer": "\n".join(lines), "query": query, "intent": intent, "sources": sources, "chunks_used": len(sources), "llm_used": False}


if __name__ == "__main__":
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
    print(json.dumps(answer_rag_extractive(idx, args.query, args.top_k), ensure_ascii=False, indent=2))
