# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Dict, Any

from .document_type_classifier import classify_document_type


def infer_origin(file_name: str, text: str = "") -> Dict[str, Any]:
    """Origine documentaire générique, basée sur le type du document."""
    typed = classify_document_type({"document": Path(file_name).name, "text": text})
    dtype = typed.get("document_type")
    weight = float(typed.get("document_weight") or 0.70)

    if dtype == "cir_final":
        return {"content_origin": "cir_final", "source_weight": 0.0, "reason": "document_type_cir_final", **typed}

    if dtype in {"concept_projet", "brevet", "preuve_depot_brevet", "rapport_test", "note_projet", "presentation_projet", "methodologie_protocole", "document_projet"}:
        return {"content_origin": "project_core", "source_weight": weight, "reason": f"document_type_{dtype}", **typed}

    if dtype == "etat_art_bibliographie":
        return {"content_origin": "state_of_art", "source_weight": weight, "reason": "document_type_state_of_art", **typed}

    if dtype in {"norme_reglementation", "plan_schema", "notice_memoire_technique", "administratif", "template_formulaire"}:
        return {"content_origin": dtype, "source_weight": weight, "reason": f"document_type_{dtype}", **typed}

    return {"content_origin": "unknown", "source_weight": weight, "reason": "unknown", **typed}
