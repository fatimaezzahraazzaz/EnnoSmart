# -*- coding: utf-8 -*-
"""Contrat de données commun du pipeline NLP CIR.

Un passage possède deux dimensions indépendantes :

1. ``semantic_role`` : objectif, méthode, résultat, limite, etc. ;
2. ``lock_candidate`` : le passage mérite-t-il une qualification Frascati ?

Le pipeline ne remplace donc jamais le rôle sémantique par ``verrou``. Un même
``passage_id`` peut être présent dans sa section métier et, en parallèle, dans
``candidats_verrou_nlp``. ``verrous_rnd_locaux`` est réservé aux candidats
qualifiés par FrascatiGuard (compatibilité avec le RAG existant).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping


SEMANTIC_PACK_KEYS = [
    "objectifs_locaux",
    "methodes_locales",
    "resultats_locaux",
    "limites_locales",
    "contributions_locales",
    "etat_art_local",
    "parametres_locaux",
]

# Clé historique : uniquement les candidats qualifiés par FrascatiGuard.
QUALIFIED_LOCK_KEY = "verrous_rnd_locaux"
LOCK_CANDIDATE_KEY = "candidats_verrou_nlp"
LOCK_SUPPORT_KEY = "lock_supporting_evidence"
REJECTED_LOCK_KEY = "candidats_verrou_rejetes"
EVIDENCE_CATALOG_KEY = "evidence_catalog"
TECHNICAL_GROUPS_KEY = "technical_lock_groups"
SECONDARY_TECHNICAL_GROUPS_KEY = "secondary_technical_groups"

ALL_PACK_KEYS = [
    *SEMANTIC_PACK_KEYS,
    QUALIFIED_LOCK_KEY,
    LOCK_CANDIDATE_KEY,
    LOCK_SUPPORT_KEY,
    REJECTED_LOCK_KEY,
    EVIDENCE_CATALOG_KEY,
    TECHNICAL_GROUPS_KEY,
    SECONDARY_TECHNICAL_GROUPS_KEY,
]

ROLE_TO_PACK = {
    "objectif": "objectifs_locaux",
    "methode": "methodes_locales",
    "resultat": "resultats_locaux",
    "limite": "limites_locales",
    "contribution": "contributions_locales",
    "etat_art": "etat_art_local",
    "parametre": "parametres_locaux",
}

NON_LOCK_SEMANTIC_ROLES = tuple(ROLE_TO_PACK)


def safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def empty_pack(*, include_audit: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    keys = ALL_PACK_KEYS if include_audit else [*SEMANTIC_PACK_KEYS, QUALIFIED_LOCK_KEY]
    return {key: [] for key in keys}


def normalize_text_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"\W+", " ", text).strip()


def passage_identity(item: Mapping[str, Any]) -> str:
    explicit = str(item.get("passage_id") or item.get("id") or "").strip()
    if explicit:
        return explicit
    raw = "|".join(
        [
            str(item.get("document") or ""),
            str(item.get("section_title") or ""),
            normalize_text_key(item.get("text"))[:1200],
        ]
    )
    return "passage_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def ensure_passage_id(item: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    item["passage_id"] = passage_identity(item)
    return item


def semantic_role(item: Mapping[str, Any], default: str = "limite") -> str:
    role = str(item.get("semantic_role") or item.get("role") or "").strip().lower()
    return role if role in ROLE_TO_PACK else default


def dedupe_items(items: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Déduplique dans une même liste, jamais entre deux documents.

    Les fenêtres NLP peuvent produire le même texte avec deux ``passage_id``
    différents. L'identité technique reste conservée dans l'élément retenu,
    mais la clé de déduplication est ``source + texte``.
    """
    best: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for raw in items or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        ensure_passage_id(item)
        source_key = str(item.get("source_path") or item.get("document") or "")
        text_key = normalize_text_key(item.get("text"))
        key = f"{source_key}|{text_key}" if text_key else item["passage_id"]
        score = float(
            item.get("rank_score")
            or item.get("frascati_score")
            or item.get("lock_candidate_score")
            or item.get("verrou_score")
            or 0.0
        )
        old = best.get(key)
        if old is None:
            best[key] = item
            order.append(key)
            continue
        old_score = float(
            old.get("rank_score")
            or old.get("frascati_score")
            or old.get("lock_candidate_score")
            or old.get("verrou_score")
            or 0.0
        )
        if score > old_score:
            best[key] = item
    return [best[key] for key in order]


def normalize_pack(pack: Mapping[str, Any] | None) -> Dict[str, List[Dict[str, Any]]]:
    """Normalise un pack moderne ou historique sans perdre de preuve."""
    source = pack if isinstance(pack, Mapping) else {}
    out = empty_pack()
    for key in ALL_PACK_KEYS:
        out[key] = dedupe_items(safe_list(source.get(key)))

    # Alias tolérés pendant la migration.
    for alias in ("lock_candidates", "verrou_candidates", "candidats_verrous_nlp"):
        out[LOCK_CANDIDATE_KEY] = dedupe_items(
            [*out[LOCK_CANDIDATE_KEY], *safe_list(source.get(alias))]
        )

    # Un ancien pack n'avait qu'une liste de verrous. Elle devient une source
    # candidate, mais reste dans la clé historique jusqu'à qualification.
    if not out[LOCK_CANDIDATE_KEY] and out[QUALIFIED_LOCK_KEY]:
        promoted = []
        for raw in out[QUALIFIED_LOCK_KEY]:
            item = dict(raw)
            item.setdefault("lock_candidate", True)
            item.setdefault("lock_candidate_explicit", True)
            item.setdefault("lock_eligible", True)
            item.setdefault("original_model_role", "verrou")
            item.setdefault("lock_candidate_source", "legacy_verrous_rnd_locaux")
            promoted.append(item)
        out[LOCK_CANDIDATE_KEY] = dedupe_items(promoted)

    return out


def count_pack(pack: Mapping[str, Any] | None) -> Dict[str, int]:
    normalized = normalize_pack(pack)
    return {key: len(normalized.get(key, [])) for key in ALL_PACK_KEYS}
