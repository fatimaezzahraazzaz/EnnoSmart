# -*- coding: utf-8 -*-
from __future__ import annotations

"""
paper_reranker_model.py — EnnoScholar V145 semantic tag correction

Reranking local optionnel avec un cross-encoder scientifique/multilingue.

Objectif :
- ne PAS remplacer le ranker déterministe EnnoScholar ;
- réordonner les articles déjà récupérés/classés en comparant :
    verrou + contexte EnnoDiagnostic
    VS
    titre + résumé de l'article ;
- fonctionner en mode dégradé si transformers/torch/modèle indisponibles.

Variables .env utiles :
    ENNOSCHOLAR_ENABLE_BGE_RERANKER=true
    ENNOSCHOLAR_RERANKER_MODEL=BAAI/bge-reranker-v2-m3
    ENNOSCHOLAR_RERANKER_DEVICE=auto          # auto | cuda | cpu
    ENNOSCHOLAR_RERANKER_TOP_K_INPUT=60       # nombre d'articles envoyés au reranker
    ENNOSCHOLAR_RERANKER_BATCH_SIZE=8
    ENNOSCHOLAR_RERANKER_MAX_LENGTH=512
    ENNOSCHOLAR_RERANKER_WEIGHT=0.65          # poids du score modèle dans le score final

Note : le premier lancement peut télécharger le modèle Hugging Face.
"""

import math
import os
import time
from typing import Any, Dict, List, Tuple


def _env_bool(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name, "") or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on", "oui"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def _clean(text: Any, max_chars: int = 2000) -> str:
    import re
    return re.sub(r"\s+", " ", str(text or "")).strip()[:max_chars].strip()


def _sigmoid(x: float) -> float:
    try:
        if x >= 0:
            z = math.exp(-x)
            return 1.0 / (1.0 + z)
        z = math.exp(x)
        return z / (1.0 + z)
    except Exception:
        return 0.0


def is_bge_reranker_enabled() -> bool:
    # Default true: if dependencies/model are missing, the module falls back cleanly.
    return _env_bool("ENNOSCHOLAR_ENABLE_BGE_RERANKER", True)



def _intent_query_text(intent: Dict[str, Any]) -> str:
    """Requête BGE compacte, limitée au verrou courant et à ses preuves."""
    parts: List[str] = []
    for key in ["verrou_title", "original_title", "scientific_problem", "technical_object", "phenomenon"]:
        if intent.get(key):
            parts.append(str(intent.get(key)))
    for key in ["methods", "key_terms_fr", "key_terms_en", "strong_anchors"]:
        value = intent.get(key) or []
        if isinstance(value, list):
            parts.append(" ".join(map(str, value[:12])))
        elif value:
            parts.append(str(value))
    source_basis = intent.get("source_basis") or {}
    if isinstance(source_basis, dict) and source_basis.get("source_text_excerpt"):
        parts.append(str(source_basis.get("source_text_excerpt"))[:1000])
    return _clean(" | ".join(parts), 2200)

def _paper_text(article: Dict[str, Any]) -> str:
    fields = article.get("fields_of_study") or []
    if isinstance(fields, list):
        fields_txt = " ".join(map(str, fields))
    else:
        fields_txt = str(fields or "")

    return _clean(
        "\n".join([
            "Title: " + str(article.get("title") or ""),
            "Abstract: " + str(article.get("abstract") or article.get("tldr") or ""),
            "Venue: " + str(article.get("venue") or ""),
            "Fields: " + fields_txt,
        ]),
        3200,
    )


class _CrossEncoderReranker:
    _model_name: str | None = None
    _tokenizer: Any = None
    _model: Any = None
    _device: Any = None
    _load_error: str = ""

    @classmethod
    def load(cls) -> Tuple[bool, str]:
        model_name = os.getenv("ENNOSCHOLAR_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
        if cls._model is not None and cls._model_name == model_name:
            return True, ""
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            device_pref = str(os.getenv("ENNOSCHOLAR_RERANKER_DEVICE", "auto") or "auto").lower()
            if device_pref == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            else:
                device = device_pref

            trust_remote_code = _env_bool("ENNOSCHOLAR_RERANKER_TRUST_REMOTE_CODE", False)
            cls._tokenizer = AutoTokenizer.from_pretrained(
                model_name,
                trust_remote_code=trust_remote_code,
            )
            cls._model = AutoModelForSequenceClassification.from_pretrained(
                model_name,
                trust_remote_code=trust_remote_code,
            )
            cls._model.to(device)
            cls._model.eval()
            cls._device = device
            cls._model_name = model_name
            cls._load_error = ""
            return True, ""
        except Exception as exc:
            cls._model = None
            cls._tokenizer = None
            cls._device = None
            cls._model_name = model_name
            cls._load_error = repr(exc)
            return False, cls._load_error

    @classmethod
    def predict(cls, query: str, docs: List[str]) -> List[float]:
        ok, err = cls.load()
        if not ok:
            raise RuntimeError(err)

        import torch

        batch_size = max(1, min(_env_int("ENNOSCHOLAR_RERANKER_BATCH_SIZE", 8), 32))
        max_length = max(128, min(_env_int("ENNOSCHOLAR_RERANKER_MAX_LENGTH", 512), 4096))
        scores: List[float] = []

        with torch.no_grad():
            for start in range(0, len(docs), batch_size):
                batch_docs = docs[start:start + batch_size]
                inputs = cls._tokenizer(
                    [query] * len(batch_docs),
                    batch_docs,
                    padding=True,
                    truncation=True,
                    max_length=max_length,
                    return_tensors="pt",
                )
                inputs = {k: v.to(cls._device) for k, v in inputs.items()}
                outputs = cls._model(**inputs)
                logits = outputs.logits
                if logits.ndim == 2 and logits.shape[-1] == 1:
                    raw = logits[:, 0]
                elif logits.ndim == 2:
                    # Pour les modèles 2 classes, on prend la classe pertinente.
                    raw = logits[:, -1]
                else:
                    raw = logits.reshape(-1)
                scores.extend([float(x) for x in raw.detach().cpu().tolist()])
        return scores



def _normalize_scores(raw_scores: List[float]) -> List[float]:
    """Score absolu sigmoid. Aucun min-max relatif qui ferait artificiellement un gagnant à 1."""
    return [_sigmoid(float(x)) for x in (raw_scores or [])]


def rerank_papers_with_bge(
    articles: List[Dict[str, Any]],
    intent: Dict[str, Any],
    top_n: int | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Reranking BGE avec rétrogradation possible des faux Direct."""
    started = time.perf_counter()
    articles = [a for a in (articles or []) if isinstance(a, dict)]
    top_n = int(top_n or len(articles) or 0)

    report: Dict[str, Any] = {
        "enabled": is_bge_reranker_enabled(),
        "used": False,
        "model": os.getenv("ENNOSCHOLAR_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
        "input_count": len(articles),
        "reranked_count": 0,
        "requalified_count": 0,
        "top_k_input": 0,
        "elapsed_seconds": 0.0,
        "error": "",
        "policy": "v145_absolute_bge_can_downgrade_false_direct",
    }

    if not articles:
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return articles, report
    if not report["enabled"]:
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return articles[:top_n], report

    top_k_input = max(1, min(_env_int("ENNOSCHOLAR_RERANKER_TOP_K_INPUT", 60), len(articles)))
    report["top_k_input"] = top_k_input
    head = [dict(a) for a in articles[:top_k_input]]
    tail = [dict(a) for a in articles[top_k_input:]]
    query = _intent_query_text(intent)
    docs = [_paper_text(a) for a in head]

    try:
        raw_scores = _CrossEncoderReranker.predict(query, docs)
        absolute_scores = _normalize_scores(raw_scores)
        weight = max(0.0, min(_env_float("ENNOSCHOLAR_RERANKER_WEIGHT", 0.45), 1.0))
        requalified = 0

        for article, raw, bge_score in zip(head, raw_scores, absolute_scores):
            previous = float(article.get("relevance_score") or 0.0)
            combined = (weight * float(bge_score)) + ((1.0 - weight) * previous)
            details = article.get("score_details") if isinstance(article.get("score_details"), dict) else {}
            details = dict(details)
            old_tag = str(article.get("tag") or "Fondamental")
            direct_eligible = bool(details.get("direct_eligible"))
            specific_count = int(details.get("specific_anchor_count") or 0)
            title_specific_count = len(details.get("specific_title_anchors") or [])
            object_overlap = int(details.get("object_overlap_count") or 0)

            new_tag = old_tag
            # Un Direct déterministe sans critères forts est systématiquement rétrogradé.
            if old_tag == "Direct" and not direct_eligible:
                new_tag = "Connexe" if specific_count and object_overlap else "Fondamental"
            # Très faible accord sémantique absolu : pas de Direct sans plusieurs preuves fortes.
            elif old_tag == "Direct" and float(raw) < -5.0 and float(bge_score) < 0.01:
                if title_specific_count < 1 or specific_count < 2:
                    new_tag = "Connexe"
            # Aucun ancrage précis et score BGE très faible : hors sujet.
            elif specific_count == 0 and object_overlap == 0 and float(bge_score) < 0.02:
                new_tag = "Hors sujet"
            # Promotion prudente, jamais vers Direct sans direct_eligible.
            elif old_tag in {"Hors sujet", "Fondamental"} and direct_eligible and float(bge_score) >= 0.35:
                new_tag = "Connexe"

            if new_tag != old_tag:
                requalified += 1
                article["tag_before_bge"] = old_tag
                article["tag"] = new_tag
                article["reason"] = (
                    f"Tag réévalué par le garde sémantique V145 : {old_tag} → {new_tag}. "
                    "La décision combine ancres exactes, compatibilité de l’objet et score BGE absolu."
                )

            article["relevance_score_before_rerank"] = round(previous, 4)
            article["bge_reranker_score"] = round(float(bge_score), 6)
            article["bge_reranker_raw_score"] = round(float(raw), 4)
            article["relevance_score"] = round(max(0.0, min(combined, 1.0)), 4)
            details.update({
                "bge_reranker_used": True,
                "bge_reranker_score_absolute": article["bge_reranker_score"],
                "bge_reranker_raw_score": article["bge_reranker_raw_score"],
                "relevance_score_before_rerank": article["relevance_score_before_rerank"],
                "tag_before_bge": old_tag,
                "tag_after_bge": article.get("tag"),
            })
            article["score_details"] = details

        tag_order = {"Direct": 4, "Connexe": 3, "Fondamental": 2, "Technique": 1, "Hors sujet": 0}

        def safe_int(value: Any) -> int:
            try:
                return int(value or 0)
            except Exception:
                return 0

        head.sort(
            key=lambda x: (
                tag_order.get(str(x.get("tag") or ""), 0),
                float(x.get("relevance_score") or 0.0),
                float(x.get("bge_reranker_score") or 0.0),
                safe_int(x.get("citation_count") or x.get("citationCount")),
                safe_int(x.get("year")),
            ),
            reverse=True,
        )
        report.update({
            "used": True,
            "reranked_count": len(head),
            "requalified_count": requalified,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        })
        return (head + tail)[:top_n], report

    except Exception as exc:
        report.update({
            "used": False,
            "error": repr(exc),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        })
        return articles[:top_n], report



# =============================================================================
# V146 — BGE ne remplace jamais la preuve conceptuelle déterministe
# =============================================================================
def _intent_query_text(intent: Dict[str, Any]) -> str:
    parts: List[str] = []
    plan = intent.get("scientific_query_plan") if isinstance(intent.get("scientific_query_plan"), dict) else {}
    if plan:
        for key in [
            "scientific_object", "independent_variables", "response_variables",
            "operating_conditions", "phenomena", "methods", "validation_concepts",
        ]:
            rows = plan.get(key) or []
            if not isinstance(rows, list):
                rows = [rows]
            for row in rows[:8]:
                if isinstance(row, dict):
                    value = row.get("term_en") or row.get("term") or row.get("value")
                else:
                    value = row
                if value:
                    parts.append(str(value))
    for key in ["core_concepts", "method_anchors", "phenomenon_anchors"]:
        value = intent.get(key) or []
        if isinstance(value, list):
            parts.extend(map(str, value[:8]))
    if not parts:
        for key in ["technical_object", "phenomenon", "scientific_problem", "verrou_title"]:
            if intent.get(key):
                parts.append(str(intent.get(key)))
    # Preserve order while removing duplicate concepts.
    seen = set()
    unique = []
    for item in parts:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return _clean(" | ".join(unique), 3000)


def rerank_papers_with_bge(
    articles: List[Dict[str, Any]],
    intent: Dict[str, Any],
    top_n: int | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """V168: BGE orders relevance inside deterministic categories only.

    The scientific category is decided by paper_ranker V168. Semantic similarity
    cannot promote an article from Fundamental/Hors sujet to Direct/Connexe and
    cannot turn a technical implementation into scientific evidence.
    """
    started = time.perf_counter()
    articles = [a for a in (articles or []) if isinstance(a, dict)]
    top_n = int(top_n or len(articles) or 0)
    report: Dict[str, Any] = {
        "enabled": is_bge_reranker_enabled(), "used": False,
        "model": os.getenv("ENNOSCHOLAR_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
        "input_count": len(articles), "reranked_count": 0, "requalified_count": 0,
        "top_k_input": 0, "elapsed_seconds": 0.0, "error": "",
        "policy": "v168_bge_order_only_category_locked",
    }
    if not articles or not report["enabled"]:
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return articles[:top_n], report

    top_k_input = max(1, min(_env_int("ENNOSCHOLAR_RERANKER_TOP_K_INPUT", 80), len(articles)))
    report["top_k_input"] = top_k_input
    head, tail = [dict(a) for a in articles[:top_k_input]], [dict(a) for a in articles[top_k_input:]]
    try:
        raw_scores = _CrossEncoderReranker.predict(_intent_query_text(intent), [_paper_text(a) for a in head])
        scores = _normalize_scores(raw_scores)
        weight = max(0.0, min(_env_float("ENNOSCHOLAR_RERANKER_WEIGHT", 0.30), 0.45))
        memory_threshold = _env_float("ENNOSCHOLAR_MEMORY_V2_BGE_MIN_SCORE", 0.30)

        for article, raw, bge in zip(head, raw_scores, scores):
            old_tag = str(article.get("tag") or "Hors sujet")
            details = dict(article.get("score_details") or {})
            previous = float(article.get("relevance_score") or 0.0)

            # Memory V2 remains conservative, but BGE does not modify the tag.
            if article.get("memory_v2_prior"):
                object_hit = bool(details.get("object_role_hit") or int(details.get("primary_core_hit_count") or 0) >= 1)
                relation = bool(details.get("relation_evidence") or details.get("problem_evidence"))
                accepted = bool(
                    old_tag in {"Direct", "Connexe"}
                    and object_hit
                    and (relation or int(details.get("support_role_count") or 0) >= 1)
                    and float(bge) >= memory_threshold
                )
                article["memory_v2_accepted_after_bge"] = accepted
                article["memory_v2_rejection_reason"] = "" if accepted else "memory_requires_role_support_and_bge_threshold"

            combined = weight * float(bge) + (1.0 - weight) * previous
            article["relevance_score_before_rerank"] = round(previous, 4)
            article["bge_reranker_score"] = round(float(bge), 6)
            article["bge_reranker_raw_score"] = round(float(raw), 4)
            article["relevance_score"] = round(max(0.0, min(combined, 1.0)), 4)
            details.update({
                "bge_reranker_used": True,
                "bge_reranker_policy": "order_only_category_locked",
                "bge_reranker_score_absolute": article["bge_reranker_score"],
                "bge_reranker_raw_score": article["bge_reranker_raw_score"],
                "tag_before_bge": old_tag,
                "tag_after_bge": old_tag,
                "memory_v2_accepted_after_bge": article.get("memory_v2_accepted_after_bge"),
            })
            article["score_details"] = details

        order = {"Direct": 4, "Connexe": 3, "Fondamental": 2, "Technique": 1, "Hors sujet": 0}
        head.sort(key=lambda x: (
            order.get(str(x.get("tag") or ""), 0),
            float(x.get("relevance_score") or 0.0),
            float(x.get("bge_reranker_score") or 0.0),
            int(x.get("citation_count") or 0),
        ), reverse=True)
        report.update({
            "used": True,
            "reranked_count": len(head),
            "requalified_count": 0,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        })
        return (head + tail)[:top_n], report
    except Exception as exc:
        report.update({"used": False, "error": repr(exc), "elapsed_seconds": round(time.perf_counter() - started, 3)})
        return articles[:top_n], report
