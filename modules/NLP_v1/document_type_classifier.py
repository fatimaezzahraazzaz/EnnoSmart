# -*- coding: utf-8 -*-
"""
document_type_classifier.py — V23

But : donner un rôle documentaire global au fichier.
Ce n'est pas une règle métier CIR, c'est un contexte documentaire générique.
Le pipeline utilise ce contexte pour éviter de juger une phrase isolée sans savoir
si elle vient d'une note projet, d'une présentation, d'un benchmark ou d'un article.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict


def _norm(s: str) -> str:
    s = str(s or "").lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def classify_document_type(doc: Dict[str, Any]) -> Dict[str, Any]:
    name = _norm(Path(str(doc.get("document") or doc.get("file_name") or "")).name)
    text = _norm(str(doc.get("text") or "")[:8000])
    joined = f"{name} {text}"
    origin = doc.get("content_origin") or "unknown"

    # Ordre volontaire : les documents projet sont prioritaires si leur nom est explicite.
    if re.search(r"\b(note de cadrage|cadrage|brief|expression besoin|fiche projet)\b", joined):
        return {"document_type": "project_note", "document_type_confidence": 0.95}

    if re.search(r"\b(presentation|présentation|avancement|travaux|slides?|pptx?)\b", joined):
        return {"document_type": "project_presentation", "document_type_confidence": 0.92}

    if re.search(r"\b(methodologie|méthodologie|protocole|demarche|démarche|plan d essais|plan de test)\b", joined):
        return {"document_type": "project_methodology", "document_type_confidence": 0.88}

    if re.search(r"\b(benchmark|comparatif|baseline|etat de l art|état de l art|state of art|survey|review)\b", joined):
        return {"document_type": "benchmark_or_state_of_art", "document_type_confidence": 0.86}

    if re.search(r"\b(abstract|keywords|doi|journal|conference|ieee|arxiv|frontiers|trials|references)\b", joined):
        return {"document_type": "scientific_article", "document_type_confidence": 0.82}

    if re.search(r"\b(resume de la documentation|résumé de la documentation|liste des documents|documentation)\b", name):
        return {"document_type": "metadata_summary", "document_type_confidence": 0.90}

    if origin == "project_core":
        return {"document_type": "project_document", "document_type_confidence": 0.70}
    if origin == "state_of_art":
        return {"document_type": "scientific_article", "document_type_confidence": 0.70}
    if origin == "metadata":
        return {"document_type": "metadata_summary", "document_type_confidence": 0.70}

    return {"document_type": "unknown_document", "document_type_confidence": 0.50}


def enrich_document_type(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(doc or {})
    out.update(classify_document_type(out))
    return out
