# -*- coding: utf-8 -*-
"""Chargement et inférence du classifieur FastJudge unique.

FastJudge classe chaque passage dans les huit rôles sémantiques du module NLP.
La prédiction ``verrou`` sert directement de graine de verrou potentiel. Les
champs historiques ``verrou_score`` / ``lock_candidate_score`` sont conservés
pour ne pas casser le pipeline aval, mais ils proviennent désormais du même
FastJudge et non d'un second modèle.
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path
from functools import lru_cache
from typing import Any, Dict, List, Tuple

try:
    import joblib
except Exception:  # pragma: no cover
    joblib = None

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


_APP_ROOT = Path(os.getenv("ENNOSMART_ROOT", Path(__file__).resolve().parents[2]))

DEFAULT_FASTJUDGE_PATHS = [
    str(_APP_ROOT / "models" / "fastjudge" / "fastjudge_linearsvc_C025.joblib"),
]

ROLE_LABELS = [
    "objectif", "verrou", "methode", "parametre", "resultat", "limite", "contribution", "bruit"
]

INCLUDE_MODEL_PATHS_IN_ITEMS = os.getenv("ENNOSMART_NLP_INCLUDE_MODEL_PATHS", "0").strip().lower() in {"1", "true", "yes", "on"}


# ============================================================
# Utils
# ============================================================

def _exists(path: str | Path | None) -> bool:
    return bool(path) and Path(str(path)).exists()


def _first_existing(paths: List[str], env_name: str) -> Path:
    env_path = os.environ.get(env_name)
    if _exists(env_path):
        return Path(env_path)
    for p in paths:
        if _exists(p):
            return Path(p)
    raise FileNotFoundError(
        f"Modèle introuvable pour {env_name}. Chemins testés : "
        + ", ".join([env_path or ""] + paths)
    )


def _load_file(path: str | Path) -> Any:
    path = Path(path)
    if joblib is not None:
        try:
            return joblib.load(path)
        except Exception:
            pass
    with open(path, "rb") as f:
        return pickle.load(f)


def _is_array_like(x: Any) -> bool:
    if x is None:
        return False
    if np is not None and isinstance(x, np.ndarray):
        return True
    return isinstance(x, (list, tuple))


def _safe_get(bundle: Any, key: str, default: Any = None) -> Any:
    if isinstance(bundle, dict) and key in bundle:
        return bundle[key]
    return default


def _as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if np is not None and isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


def _unwrap_model(bundle: Any) -> Tuple[Any, Any, Any, Dict[str, Any]]:
    """
    Retourne (estimator, vectorizer, label_encoder, raw_dict)
    Compatible :
    - sklearn Pipeline direct
    - dict avec model/clf/classifier/estimator/pipeline
    - dict avec vectorizer/tfidf et label_encoder/classes
    - tuple/list (model, vectorizer, label_encoder)
    """
    raw = bundle if isinstance(bundle, dict) else {}

    if isinstance(bundle, dict):
        est = None
        for k in ["pipeline", "model", "clf", "classifier", "estimator", "logreg", "svm"]:
            if k in bundle and bundle[k] is not None:
                est = bundle[k]
                break
        if est is None:
            # Cas où le dict lui-même expose predict rarement
            est = bundle if hasattr(bundle, "predict") else None

        vec = None
        for k in ["vectorizer", "tfidf", "tfidf_vectorizer", "vect", "encoder"]:
            if k in bundle and bundle[k] is not None:
                vec = bundle[k]
                break

        le = None
        for k in ["label_encoder", "le", "encoder_labels", "target_encoder"]:
            if k in bundle and bundle[k] is not None:
                le = bundle[k]
                break

        return est, vec, le, raw

    if isinstance(bundle, (list, tuple)):
        est = bundle[0] if len(bundle) > 0 else None
        vec = bundle[1] if len(bundle) > 1 else None
        le = bundle[2] if len(bundle) > 2 else None
        return est, vec, le, {}

    return bundle, None, None, {}


def _transform_if_needed(est: Any, vec: Any, texts: List[str]) -> Any:
    # Si estimator est un Pipeline sklearn, il accepte directement texts.
    if hasattr(est, "steps") or hasattr(est, "named_steps"):
        return texts
    if vec is not None and hasattr(vec, "transform"):
        return vec.transform(texts)
    return texts


def _classes_from(est: Any, label_encoder: Any, raw: Dict[str, Any]) -> List[Any]:
    """
    Correction principale : ne jamais faire `array or autre`.
    Avec numpy array, `or` provoque ValueError.
    """
    classes = getattr(est, "classes_", None)

    if classes is None and label_encoder is not None:
        classes = getattr(label_encoder, "classes_", None)

    if classes is None and isinstance(raw, dict):
        for k in ["classes", "labels", "target_names", "class_names"]:
            if k in raw and raw[k] is not None:
                classes = raw[k]
                break

    if classes is None:
        return []

    return _as_list(classes)


def _decode_labels(preds: Any, label_encoder: Any, classes: List[Any]) -> List[str]:
    preds_list = _as_list(preds)

    # Si prédictions numériques et label_encoder disponible
    if label_encoder is not None and hasattr(label_encoder, "inverse_transform"):
        try:
            decoded = label_encoder.inverse_transform(preds_list)
            return [str(x) for x in _as_list(decoded)]
        except Exception:
            pass

    # Si prédictions numériques et classes disponibles
    out = []
    for p in preds_list:
        if isinstance(p, (int,)) or (np is not None and isinstance(p, np.integer)):
            idx = int(p)
            if 0 <= idx < len(classes):
                out.append(str(classes[idx]))
            else:
                out.append(str(p))
        else:
            out.append(str(p))
    return out


def _sigmoid(value: float) -> float:
    # Stable enough for sklearn decision margins. This is a relative score,
    # not a calibrated probability.
    if np is not None:
        value = float(np.clip(value, -40.0, 40.0))
    else:
        value = max(-40.0, min(40.0, float(value)))
    return 1.0 / (1.0 + __import__("math").exp(-value))


def _decision_to_score_matrix(decision: Any, classes: List[Any]) -> Any:
    """Convertit les marges LinearSVC en scores 0..1 pour compatibilité UI.

    Ces scores servent au classement/affichage uniquement. Ils ne sont pas
    interprétés comme des probabilités calibrées et ne servent plus à décider
    si un passage est un verrou : la classe prédite ``verrou`` fait foi.
    """
    if decision is None:
        return None
    try:
        arr = np.asarray(decision, dtype=float) if np is not None else decision
        if np is not None:
            if arr.ndim == 1:
                if len(classes) == 2:
                    arr = np.column_stack([-arr, arr])
                else:
                    arr = arr.reshape(-1, 1)
            clipped = np.clip(arr, -40.0, 40.0)
            return 1.0 / (1.0 + np.exp(-clipped))
        # Fallback minimal sans numpy
        rows = decision if isinstance(decision, (list, tuple)) else [decision]
        out = []
        for row in rows:
            vals = list(row) if isinstance(row, (list, tuple)) else [row]
            out.append([_sigmoid(float(v)) for v in vals])
        return out
    except Exception:
        return None


def _predict_bundle(bundle: Any, texts: List[str]) -> Tuple[List[str], Any, List[Any], str]:
    est, vec, le, raw = _unwrap_model(bundle)

    if est is None or not hasattr(est, "predict"):
        raise AttributeError(
            "Le modèle chargé n'a pas de méthode predict(). "
            "Vérifie les clés du .joblib avec debug_model_files()."
        )

    x = _transform_if_needed(est, vec, texts)
    preds_raw = est.predict(x)
    classes = _classes_from(est, le, raw)
    preds = _decode_labels(preds_raw, le, classes)

    score_matrix = None
    score_source = "none"
    if hasattr(est, "predict_proba"):
        try:
            score_matrix = est.predict_proba(x)
            score_source = "predict_proba"
        except Exception:
            score_matrix = None

    if score_matrix is None and hasattr(est, "decision_function"):
        try:
            decision = est.decision_function(x)
            score_matrix = _decision_to_score_matrix(decision, classes)
            score_source = "decision_function_sigmoid_uncalibrated"
        except Exception:
            score_matrix = None
            score_source = "none"

    return preds, score_matrix, classes, score_source

def _score_from_proba(proba: Any, i: int, label: str, classes: List[Any]) -> Tuple[float, Dict[str, float]]:
    if proba is None:
        return 0.0, {}

    try:
        row = proba[i]
    except Exception:
        return 0.0, {}

    row_list = _as_list(row)
    class_list = [str(c) for c in classes]

    scores: Dict[str, float] = {}
    for c, s in zip(class_list, row_list):
        try:
            scores[str(c)] = float(s)
        except Exception:
            pass

    if label in scores:
        return float(scores[label]), scores

    # fallback max proba
    if row_list:
        try:
            return float(max(row_list)), scores
        except Exception:
            return 0.0, scores

    return 0.0, scores


# ============================================================
# Loaders
# ============================================================

@lru_cache(maxsize=1)
def _load_fastjudge() -> Tuple[Any, Path]:
    path = _first_existing(DEFAULT_FASTJUDGE_PATHS, "ENNOSMART_FASTJUDGE_MODEL_PATH")
    return _load_file(path), path


# ============================================================
# Public API
# ============================================================

def judge_passages_batch(texts: List[str]) -> List[Dict[str, Any]]:
    if not texts:
        return []

    model, path = _load_fastjudge()
    preds, proba, classes, score_source = _predict_bundle(model, texts)

    out: List[Dict[str, Any]] = []
    for i, label in enumerate(preds):
        label = str(label).strip()
        conf, scores = _score_from_proba(proba, i, label, classes)
        if not conf and scores:
            conf = max(scores.values())
        out.append({
            "label": label,
            "role": label,
            "score": float(conf or 0.0),
            "confidence": float(conf or 0.0),
            "scores": scores,
            "score_source": score_source,
            "fastjudge_model_path": str(path),
        })
    return out


def run_fastjudge(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    texts = [str(c.get("model_input") or c.get("text", "")) for c in candidates]
    preds = judge_passages_batch(texts)

    out: List[Dict[str, Any]] = []
    for c, p in zip(candidates, preds):
        item = dict(c)
        item.pop("model_input", None)

        role = str(p.get("label") or p.get("role") or "bruit").strip().lower()
        role_scores = dict(p.get("scores", {}) or {})
        model_score = float(p.get("score") or p.get("confidence") or 0.0)
        verrou_score = float(role_scores.get("verrou", model_score if role == "verrou" else 0.0) or 0.0)

        # Rôle multiclasses : source canonique unique.
        item["original_model_role"] = role
        item["role"] = role
        item["model_confidence"] = model_score
        item["confidence"] = model_score
        item["role_scores"] = role_scores
        item["scores"] = dict(role_scores)
        item["model_score_source"] = p.get("score_source") or "unknown"

        # Aliases historiques conservés pour les modules aval. Ils ne viennent
        # plus d'un VerrouDetector séparé.
        item["verrou_score"] = verrou_score
        item["lock_candidate_score"] = verrou_score
        item["lock_model_label"] = "verrou" if role == "verrou" else "not_verrou"
        item["lock_model_scores"] = {"verrou": verrou_score}
        item["lock_score_source"] = "fastjudge_single_model"

        if INCLUDE_MODEL_PATHS_IN_ITEMS:
            item["fastjudge_model_path"] = p.get("fastjudge_model_path")

        out.append(item)
    return out


def apply_models(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Applique uniquement FastJudge ; aucun second classifieur de verrou."""
    if not candidates:
        return []
    return run_fastjudge(candidates)


def debug_model_files() -> Dict[str, Any]:
    """À afficher dans Streamlit si besoin."""
    info: Dict[str, Any] = {}
    for name, loader in [("fastjudge", _load_fastjudge)]:
        try:
            obj, path = loader()
            est, vec, le, raw = _unwrap_model(obj)
            info[name] = {
                "path": str(path),
                "raw_type": type(obj).__name__,
                "raw_keys": list(obj.keys()) if isinstance(obj, dict) else None,
                "estimator_type": type(est).__name__ if est is not None else None,
                "has_predict": bool(hasattr(est, "predict")),
                "has_predict_proba": bool(hasattr(est, "predict_proba")),
                "has_decision_function": bool(hasattr(est, "decision_function")),
                "vectorizer_type": type(vec).__name__ if vec is not None else None,
                "label_encoder_type": type(le).__name__ if le is not None else None,
                "classes": [str(x) for x in _classes_from(est, le, raw)],
            }
        except Exception as e:
            info[name] = {"error": str(e)}
    return info
