# -*- coding: utf-8 -*-
"""
filter.py — V23 contexte documentaire

Ce fichier remplace les filtres rigides par un scoring contextualisé :
- score modèle
- score source
- cohérence entre rôle prédit et rôle de section
- type de document

On ne cherche pas la perfection au passage. On garde du rappel, mais on diminue
le poids des passages hors contexte projet pour la synthèse.
"""
from __future__ import annotations

from typing import Dict, Any, List
import re

STRICT = {
    "objectif": 0.72,
    "verrou": 0.66,
    "methode": 0.70,
    "parametre": 0.70,
    "resultat": 0.70,
    "limite": 0.70,
    "contribution": 0.70,
}
RECALL = {
    "objectif": 0.58,
    "verrou": 0.54,
    "methode": 0.58,
    "parametre": 0.58,
    "resultat": 0.58,
    "limite": 0.58,
    "contribution": 0.58,
}
STRICT_VERROU_DETECTOR = 0.52
RECALL_VERROU_DETECTOR = 0.40

BAD_SYNTH = [
    "tapez ici", "nom de la présentation", "document security", "charte graphique",
    "diffusion", "nombre d exemplaire", "erreur nom de propriete"
]

DOC_TYPE_WEIGHT = {
    "project_note": 1.15,
    "project_presentation": 1.08,
    "project_methodology": 1.02,
    "project_document": 1.00,
    "unknown_document": 0.90,
    "benchmark_or_state_of_art": 0.72,
    "scientific_article": 0.65,
    "metadata_summary": 0.25,
}

SECTION_ROLE_MATCH = {
    "objectif": {"objectif"},
    "verrou": {"verrou", "limite"},
    "limite": {"verrou", "limite"},
    "methode": {"methode"},
    "resultat": {"resultat"},
    "parametre": {"parametre", "methode"},
    "contribution": {"contribution", "resultat"},
}


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return default


def _norm(s: str) -> str:
    s = str(s or "").lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def context_alignment(item: Dict[str, Any]) -> float:
    """Bonus/malus doux, jamais un filtre dur."""
    role = item.get("role")
    hint = item.get("section_role_hint") or "unknown"
    if hint == "unknown" or role == "bruit":
        return 0.0
    if hint in SECTION_ROLE_MATCH.get(role, set()):
        return 0.12
    # Si le modèle dit verrou mais la section est méthode/résultat, on garde mais on baisse.
    if role == "verrou" and hint in {"methode", "resultat", "parametre", "etat_art"}:
        return -0.18
    # Si le modèle dit objectif dans une section verrou, c'est souvent une question technique formulée comme objectif.
    if role == "objectif" and hint == "verrou":
        return -0.05
    return -0.04


def rank_score(item: Dict[str, Any]) -> float:
    conf = _safe_float(item.get("confidence") or item.get("model_confidence"))
    verrou = _safe_float(item.get("verrou_score"))
    weight = _safe_float(item.get("source_weight"), 0.75)
    role = item.get("role")
    doc_type = item.get("document_type") or "unknown_document"

    base = conf * weight
    base *= DOC_TYPE_WEIGHT.get(doc_type, 0.90)

    if role == "verrou":
        base += 0.30 * verrou
    elif role == "limite":
        base += 0.18 * verrou

    base += context_alignment(item)

    # Origine : le contenu projet reste plus fiable pour objectif/verrou.
    origin = item.get("content_origin")
    if origin == "project_core":
        base *= 1.08
    elif origin == "state_of_art":
        base *= 0.78
    elif origin == "metadata":
        base *= 0.35
    elif origin == "unknown":
        base *= 0.92

    return round(max(base, 0.0), 4)


def safe_for_synthesis(item: Dict[str, Any]) -> bool:
    text = _norm(item.get("text", ""))
    if item.get("quality_status") not in {"strict", "recall"}:
        return False
    if len(text) < 45:
        return False
    if any(x in text for x in BAD_SYNTH):
        return False
    if item.get("content_origin") == "metadata":
        return False
    if item.get("content_origin") == "cir_final":
        return False
    if "€" in str(item.get("text", "")) or "cout" in text or "coût" in str(item.get("text", "")).lower():
        return False

    # Les articles externes peuvent être gardés comme état de l'art/méthode/résultat,
    # mais pas comme preuve principale de verrou projet.
    if item.get("document_type") in {"scientific_article", "benchmark_or_state_of_art"} and item.get("role") == "verrou":
        if item.get("section_role_hint") not in {"verrou", "limite"}:
            return False

    return True


def apply_quality_filter(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    kept, rejected = [], []

    for it in items:
        role = it.get("role", "bruit")
        conf = _safe_float(it.get("confidence"))
        vs = _safe_float(it.get("verrou_score"))
        hint = it.get("section_role_hint") or "unknown"

        ok = False
        status = "rejected"

        if role in STRICT and conf >= STRICT[role]:
            ok = True
            status = "strict"
        elif role in RECALL and conf >= RECALL[role]:
            ok = True
            status = "recall"

        # VerrouDetector peut sauver une limite/méthode/résultat seulement si le contexte n'est pas clairement état de l'art.
        if role in {"limite", "parametre", "resultat", "methode"} and vs >= STRICT_VERROU_DETECTOR:
            ok = True
            status = "strict"
        elif role in {"limite", "parametre", "resultat", "methode"} and vs >= RECALL_VERROU_DETECTOR:
            ok = True
            status = "recall"

        # Le contexte de section peut sauver un passage limite/verrou avec confiance moyenne.
        if hint in {"verrou", "limite"} and role in {"objectif", "limite", "verrou", "methode"} and conf >= 0.50:
            ok = True
            if status == "rejected":
                status = "recall"

        if role == "bruit":
            ok = False
            status = "rejected"

        it["quality_status"] = status
        it["context_alignment"] = context_alignment(it)
        it["rank_score"] = rank_score(it)
        it["accepted_for_synthesis"] = safe_for_synthesis(it) if ok else False

        (kept if ok else rejected).append(it)

    return {
        "kept": kept,
        "rejected": rejected,
        "stats": {
            "input": len(items),
            "kept": len(kept),
            "rejected": len(rejected),
            "strict": sum(1 for x in kept if x.get("quality_status") == "strict"),
            "recall_only": sum(1 for x in kept if x.get("quality_status") == "recall"),
        },
    }


def thresholds() -> Dict[str, float]:
    return {
        **{f"strict_{k}": v for k, v in STRICT.items()},
        **{f"recall_{k}": v for k, v in RECALL.items()},
        "strict_verrou_detector": STRICT_VERROU_DETECTOR,
        "recall_verrou_detector": RECALL_VERROU_DETECTOR,
        "bruit": 0.99,
    }
