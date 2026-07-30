# -*- coding: utf-8 -*-
from __future__ import annotations

"""
scripts/ai_experience_memory_rag_patch.py

Patch qualité pour la base RAG mémoire CIR :
1) Pour un CIR final validé, le rôle RAG principal reste le rôle structurel CIR.
2) Frascati reste en métadonnée secondaire.
3) On évite de polluer la mémoire avec des faux "verrous".
"""

from typing import Any, Dict, List
import re


SECTION_TYPE_TO_ROLE = {
    "contexte": "objectif",
    "objectifs": "objectif",
    "etat_art": "etat_art",
    "limites_etat_art": "limite",
    "verrous": "verrou",
    "methodes_travaux": "methode",
    "resultats": "resultat",
    "contribution": "contribution",
    "administratif": "administratif",
    "annexe": "annexe",
    "project_title": "objectif",
}


def _clean(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def _role_from_raw_item(raw_item: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    section_type = _clean(raw_item.get("section_type") or metadata.get("section_type")).lower()

    if section_type in SECTION_TYPE_TO_ROLE:
        return SECTION_TYPE_TO_ROLE[section_type]

    source_category = _clean(raw_item.get("_source_category") or metadata.get("pack_key")).lower()

    if source_category == "objectifs_locaux":
        return "objectif"
    if source_category == "verrous_rnd_locaux":
        return "verrou"
    if source_category == "methodes_locales":
        return "methode"
    if source_category == "resultats_locaux":
        return "resultat"
    if source_category == "etat_art_local":
        return "etat_art"
    if source_category == "limites_locales":
        return "limite"
    if source_category == "contributions_locales":
        return "contribution"
    if source_category == "parametres_locaux":
        return "parametre"

    role = _clean(raw_item.get("role") or metadata.get("role")).lower()
    if role in {"objectif", "verrou", "methode", "resultat", "etat_art", "limite", "contribution", "parametre", "style"}:
        return role

    return "autre"


def normalize_cir_final_chunk_roles(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    À appeler juste après modules.RAG.json_to_chunks.nlp_json_to_chunks().

    Le problème corrigé :
    Le pipeline Frascati peut reclasser un état de l'art / objectif / méthode en
    "verrou_probable". Pour une base mémoire validée, ce n'est pas le rôle principal
    qu'on veut indexer dans Chroma. On veut garder le rôle structurel CIR.
    """
    out: List[Dict[str, Any]] = []

    for ch in chunks or []:
        if not isinstance(ch, dict):
            continue

        meta = ch.get("metadata")
        if not isinstance(meta, dict):
            meta = {}
            ch["metadata"] = meta

        raw_item = ch.get("raw_item")
        if not isinstance(raw_item, dict):
            raw_item = {}

        document_type = _clean(
            meta.get("document_type")
            or raw_item.get("document_type")
            or meta.get("source_kind")
            or raw_item.get("source_kind")
        )

        content_origin = _clean(meta.get("content_origin") or raw_item.get("content_origin"))
        memory_status = _clean(meta.get("memory_status") or raw_item.get("memory_status"))

        is_cir_final = (
            document_type == "cir_final_consultant"
            or content_origin == "cir_final_consultant"
            or raw_item.get("source_kind") == "cir_final_consultant"
        )

        if is_cir_final and memory_status == "validated":
            original_role = _clean(meta.get("role"))
            structural_role = _role_from_raw_item(raw_item, meta)

            # Ne touche pas les chunks créés spécifiquement pour le style.
            if original_role != "style" and meta.get("chunk_level") != "style_section":
                meta["role_before_memory_normalization"] = original_role
                meta["role"] = structural_role
                meta["memory_role_normalized"] = True
                meta["frascati_role"] = raw_item.get("final_role") or meta.get("final_role") or ""
                meta["frascati_decision"] = (
                    meta.get("frascati_decision")
                    or ((raw_item.get("frascati") or {}).get("decision") if isinstance(raw_item.get("frascati"), dict) else "")
                )
                meta["can_use_as_fact"] = True
                meta["can_use_as_style"] = structural_role in {
                    "objectif", "etat_art", "limite", "verrou", "methode", "resultat", "contribution"
                }

                # Recrée un id plus sain si nécessaire.
                old_id = _clean(ch.get("id"))
                if old_id and "_verrou_" in old_id and structural_role != "verrou":
                    new_id = old_id.replace("_verrou_", f"_{structural_role}_", 1)
                    ch["id_before_memory_normalization"] = old_id
                    ch["id"] = new_id
                    meta["rag_chunk_id"] = new_id

        out.append(ch)

    return out
