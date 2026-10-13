# -*- coding: utf-8 -*-
"""Détection générique de l'origine documentaire.

Ce module ne classe pas le contenu technique et ne cherche aucun verrou. Il
fournit seulement une origine prudente à ``document_loader``. La classification
fine reste assurée ensuite par ``document_type_classifier``.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _contains(pattern: str, value: str) -> bool:
    return re.search(pattern, value, flags=re.IGNORECASE) is not None


def infer_origin(filename: str, text: str = "") -> Dict[str, Any]:
    """Retourne une origine stable sans règle propre à un projet.

    Valeurs possibles de ``content_origin`` :
    ``project_core``, ``client_pre_cir``, ``cir_final``, ``state_of_art`` ou
    ``metadata``.
    """
    name = _normalize(Path(str(filename or "")).stem)
    raw_text = str(text or "")
    if len(raw_text) > 24000:
        raw_text = raw_text[:18000] + " " + raw_text[-6000:]
    body = _normalize(raw_text)
    joined = f"{name} {body}"

    cir_context = _contains(
        r"\b(?:cir|credit impot recherche|dossier cir|fiche cir|declaration cir)\b",
        joined,
    )
    pre_cir = cir_context and _contains(
        r"\b(?:pre cir|preparation cir|brouillon cir|draft cir|note cir|"
        r"trame cir|elements cir|projet de dossier cir|cir provisoire|"
        r"version provisoire)\b",
        joined,
    )
    final_cir = cir_context and _contains(
        r"\b(?:cir final|dossier cir final|version finale|cir valide|"
        r"dossier valide|declaration cir|exercice precedent|annee precedente|"
        r"cir n 1|cir n-1)\b",
        joined,
    )

    if pre_cir:
        return {
            "content_origin": "client_pre_cir",
            "source_type": "pre_cir_client",
            "origin_confidence": 0.95,
            "origin_reason": "pré-CIR ou document CIR préparatoire explicite",
        }

    if final_cir:
        return {
            "content_origin": "cir_final",
            "source_type": "cir_final",
            "origin_confidence": 0.96,
            "origin_reason": "CIR final, validé ou exercice précédent explicite",
        }

    state_of_art_name = _contains(
        r"\b(?:etat de l art|state of the art|revue de litterature|"
        r"literature review|bibliographie|related work|survey)\b",
        name,
    )
    bibliography_signals = sum(
        _contains(pattern, body)
        for pattern in (
            r"\b(?:doi|arxiv|isbn|issn)\b",
            r"\b(?:references bibliographiques|bibliographie)\b",
            r"\b(?:journal|conference|proceedings|et al)\b",
            r"\b(?:literature review|related work|state of the art)\b",
        )
    )
    if state_of_art_name or bibliography_signals >= 2:
        return {
            "content_origin": "state_of_art",
            "source_type": "external_context",
            "origin_confidence": 0.90 if state_of_art_name else 0.78,
            "origin_reason": "état de l'art ou source bibliographique",
        }

    metadata_name = _contains(
        r"\b(?:facture|devis|budget|contrat|administratif|cerfa|"
        r"planning|calendrier|honoraires|annexe financiere|formulaire)\b",
        name,
    )
    if metadata_name:
        return {
            "content_origin": "metadata",
            "source_type": "administrative_context",
            "origin_confidence": 0.82,
            "origin_reason": "document administratif ou financier",
        }

    return {
        "content_origin": "project_core",
        "source_type": "raw_client_document",
        "origin_confidence": 0.60,
        "origin_reason": "document projet par défaut",
    }


__all__ = ["infer_origin"]
