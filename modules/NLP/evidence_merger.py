# -*- coding: utf-8 -*-
"""Fusion de packs NLP en conservant le contrat à deux dimensions."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .evidence_contract import (
    ALL_PACK_KEYS,
    LOCK_CANDIDATE_KEY,
    QUALIFIED_LOCK_KEY,
    SEMANTIC_PACK_KEYS,
    dedupe_items,
    normalize_pack,
    passage_identity,
)

# Compatibilité avec les imports des anciennes versions.
PACK_KEYS = [*SEMANTIC_PACK_KEYS, QUALIFIED_LOCK_KEY]


def _restore_semantic_location(pack: Dict[str, List[Dict[str, Any]]]) -> None:
    """Restaure uniquement les verrous provenant d'un ancien contrat.

    V34 parcourait aussi ``candidats_verrou_nlp``. Comme le pack sémantique
    contient des représentants de groupes et le catalogue contient les
    passages complets, presque tous les candidats semblaient « absents » et
    étaient ajoutés à tort dans ``limites_locales`` (40 -> 471 dans le dossier
    de contrôle). Un candidat V34 possède déjà ``semantic_role`` : il ne doit
    jamais être restauré une seconde fois.
    """
    semantic_ids = {
        passage_identity(item)
        for key in SEMANTIC_PACK_KEYS
        for item in pack.get(key, [])
    }
    additions = []
    legacy_items = [
        item
        for item in [*pack.get(LOCK_CANDIDATE_KEY, []), *pack.get(QUALIFIED_LOCK_KEY, [])]
        if item.get("lock_candidate_source") == "legacy_verrous_rnd_locaux"
        or (
            not item.get("semantic_role")
            and str(item.get("role") or "").lower() == "verrou"
        )
    ]
    for raw in legacy_items:
        if passage_identity(raw) in semantic_ids:
            continue
        item = dict(raw)
        item.setdefault("original_model_role", item.get("role") or "verrou")
        item["semantic_role"] = "limite"
        item["role"] = "limite"
        item.setdefault("semantic_role_source", "legacy_lock_fallback_to_limit")
        additions.append(item)
        semantic_ids.add(passage_identity(item))
    pack["limites_locales"] = dedupe_items([*pack.get("limites_locales", []), *additions])


def merge_evidence_packs(
    raw_pack: Mapping[str, Any] | None,
    cir_pack: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    left = normalize_pack(raw_pack)
    right = normalize_pack(cir_pack)
    merged: Dict[str, Any] = {}
    for key in ALL_PACK_KEYS:
        merged[key] = dedupe_items([*left.get(key, []), *right.get(key, [])])

    _restore_semantic_location(merged)
    merged["_contract_version"] = "nlp_evidence_v177_merged_before_lock_grouping"
    return merged
