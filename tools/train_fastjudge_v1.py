# -*- coding: utf-8 -*-
"""
train_fastjudge_v1.py
------------------------------------------------------------
ÉTAPE 4 FastJudge V1 : entraîner un classifieur rapide sans LLM.

Entrée :
    C:\EnnoSmart\data\training\role_classification_dataset.jsonl

Sorties :
    C:\EnnoSmart\models\fastjudge\fastjudge_role_classifier.pkl
    C:\EnnoSmart\models\fastjudge\fastjudge_role_classifier_report.json
    C:\EnnoSmart\models\fastjudge\fastjudge_role_classifier_predictions.csv

Modèle :
    TF-IDF mots + caractères
    + LogisticRegression class_weight='balanced'

Pourquoi ce choix ?
- rapide
- local
- pas de téléchargement HuggingFace
- adapté pour une V1
- robuste sur petit dataset pseudo-annoté

Usage :
cd C:\EnnoSmart
python tools\train_fastjudge_v1.py
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import FunctionTransformer


VALID_ROLES = [
    "objectif",
    "verrou",
    "methode",
    "parametre",
    "variable",
    "resultat",
    "limite",
    "contribution",
    "hypothese",
    "bruit",
]


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def normalize_text(x: Any) -> str:
    s = str(x or "")
    s = s.replace("\u00a0", " ").replace("\ufeff", " ")
    s = " ".join(s.split())
    return s.strip()


def build_input_text(row: Dict[str, Any]) -> str:
    """
    On donne au modèle :
    - texte candidat
    - petit contexte
    - source type
    - rôle candidat initial
    """
    text = normalize_text(row.get("text"))
    before = normalize_text(row.get("context_before"))
    after = normalize_text(row.get("context_after"))
    candidate = normalize_text(row.get("candidate_role"))
    source = normalize_text(row.get("source_type"))
    project_type = normalize_text(row.get("project_type"))

    # Le texte est volontairement répété pour donner plus de poids à la phrase.
    return (
        f"{text}\n"
        f"{text}\n"
        f"CONTEXT_BEFORE: {before}\n"
        f"CONTEXT_AFTER: {after}\n"
        f"CANDIDATE_ROLE: {candidate}\n"
        f"SOURCE_TYPE: {source}\n"
        f"PROJECT_TYPE: {project_type}"
    )


def prepare_dataset(rows: List[Dict[str, Any]], min_text_len: int = 20) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    X, y, kept = [], [], []

    for row in rows:
        role = str(row.get("role_gold") or "").strip().lower()
        text = normalize_text(row.get("text"))

        if role not in VALID_ROLES:
            continue
        if len(text) < min_text_len:
            continue

        X.append(build_input_text(row))
        y.append(role)
        kept.append(row)

    return X, y, kept


def build_model() -> Pipeline:
    """
    Modèle rapide :
    - word ngrams : compréhension générale
    - char ngrams : tolère OCR/fautes/domaines
    """
    word_tfidf = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 3),
        min_df=2,
        max_df=0.95,
        sublinear_tf=True,
        strip_accents="unicode",
        lowercase=True,
    )

    char_tfidf = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(3, 6),
        min_df=2,
        max_df=0.98,
        sublinear_tf=True,
        strip_accents="unicode",
        lowercase=True,
    )

    features = FeatureUnion([
        ("word_tfidf", word_tfidf),
        ("char_tfidf", char_tfidf),
    ])

    clf = LogisticRegression(
        max_iter=2500,
        class_weight="balanced",
        solver="liblinear",
        C=2.0,
        random_state=42,
    )

    return Pipeline([
        ("features", features),
        ("clf", clf),
    ])


def safe_train_test_split(X, y, rows, test_size=0.2, seed=42):
    """
    Stratify si possible, sinon split simple.
    """
    counts = Counter(y)
    can_stratify = all(v >= 2 for v in counts.values())

    if can_stratify:
        return train_test_split(
            X, y, rows,
            test_size=test_size,
            random_state=seed,
            stratify=y,
        )

    return train_test_split(
        X, y, rows,
        test_size=test_size,
        random_state=seed,
        shuffle=True,
    )


def save_predictions_csv(path: Path, rows_test: List[Dict[str, Any]], y_true: List[str], y_pred: List[str], proba: np.ndarray | None, labels: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "project_id",
        "source_type",
        "source_doc",
        "candidate_role",
        "role_gold",
        "role_pred",
        "pred_confidence",
        "is_correct",
        "text",
        "context_before",
        "context_after",
    ]

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for i, row in enumerate(rows_test):
            conf = ""
            if proba is not None:
                conf = round(float(np.max(proba[i])), 4)

            writer.writerow({
                "project_id": row.get("project_id"),
                "source_type": row.get("source_type"),
                "source_doc": row.get("source_doc"),
                "candidate_role": row.get("candidate_role"),
                "role_gold": y_true[i],
                "role_pred": y_pred[i],
                "pred_confidence": conf,
                "is_correct": str(y_true[i] == y_pred[i]).lower(),
                "text": row.get("text"),
                "context_before": row.get("context_before"),
                "context_after": row.get("context_after"),
            })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-jsonl", default=r"C:\EnnoSmart\data\training\role_classification_dataset.jsonl")
    parser.add_argument("--model-dir", default=r"C:\EnnoSmart\models\fastjudge")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    input_path = Path(args.input_jsonl)
    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        raise FileNotFoundError(f"Dataset introuvable : {input_path}")

    rows = read_jsonl(input_path)
    X, y, kept_rows = prepare_dataset(rows)

    print(f"Lignes dataset : {len(rows)}")
    print(f"Lignes utilisées : {len(X)}")
    print("Distribution :", dict(Counter(y)))

    if len(X) < 100:
        raise RuntimeError("Dataset trop petit pour entraîner FastJudge.")

    X_train, X_test, y_train, y_test, rows_train, rows_test = safe_train_test_split(
        X, y, kept_rows,
        test_size=float(args.test_size),
        seed=int(args.seed),
    )

    model = build_model()
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)

    proba = None
    labels = list(model.named_steps["clf"].classes_)
    if hasattr(model, "predict_proba"):
        try:
            proba = model.predict_proba(X_test)
        except Exception:
            proba = None

    acc = accuracy_score(y_test, y_pred)
    macro_f1 = f1_score(y_test, y_pred, average="macro")
    weighted_f1 = f1_score(y_test, y_pred, average="weighted")

    report_text = classification_report(y_test, y_pred, labels=VALID_ROLES, zero_division=0)
    report_dict = classification_report(y_test, y_pred, labels=VALID_ROLES, output_dict=True, zero_division=0)
    cm = confusion_matrix(y_test, y_pred, labels=VALID_ROLES)

    model_path = model_dir / "fastjudge_role_classifier.pkl"
    report_path = model_dir / "fastjudge_role_classifier_report.json"
    predictions_path = model_dir / "fastjudge_role_classifier_predictions.csv"

    bundle = {
        "model": model,
        "labels": labels,
        "valid_roles": VALID_ROLES,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model_version": "fastjudge_v1_tfidf_logreg",
        "input_schema": {
            "required": ["text"],
            "optional": ["context_before", "context_after", "candidate_role", "source_type", "project_type"],
        },
        "build_input_text_function": "modules.NLP.fast_judge.fast_role_classifier.build_input_text",
    }

    joblib.dump(bundle, model_path)

    save_predictions_csv(predictions_path, rows_test, y_test, list(y_pred), proba, labels)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model_version": "fastjudge_v1_tfidf_logreg",
        "input_jsonl": str(input_path),
        "model_path": str(model_path),
        "predictions_path": str(predictions_path),
        "n_total": len(rows),
        "n_used": len(X),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "distribution_total": dict(Counter(y)),
        "distribution_train": dict(Counter(y_train)),
        "distribution_test": dict(Counter(y_test)),
        "accuracy": round(float(acc), 4),
        "macro_f1": round(float(macro_f1), 4),
        "weighted_f1": round(float(weighted_f1), 4),
        "classification_report": report_dict,
        "classification_report_text": report_text,
        "confusion_matrix_labels": VALID_ROLES,
        "confusion_matrix": cm.tolist(),
        "warning": (
            "Dataset issu de pseudo-annotations automatiques. "
            "Le modèle V1 est utilisable pour trier/prioriser, mais ses prédictions ne doivent pas être considérées comme vérité absolue."
        ),
    }

    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nENTRAÎNEMENT TERMINÉ")
    print(f"Model      : {model_path}")
    print(f"Report     : {report_path}")
    print(f"Predictions: {predictions_path}")
    print(f"Accuracy   : {acc:.4f}")
    print(f"Macro F1   : {macro_f1:.4f}")
    print(f"Weighted F1: {weighted_f1:.4f}")
    print("\nRapport :")
    print(report_text)


if __name__ == "__main__":
    main()
