# -*- coding: utf-8 -*-
"""Regroupement des preuves par rôle sémantique, sans décider les verrous."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .evidence_contract import (
    EVIDENCE_CATALOG_KEY,
    LOCK_CANDIDATE_KEY,
    QUALIFIED_LOCK_KEY,
    ROLE_TO_PACK,
    dedupe_items,
    empty_pack,
    semantic_role,
)


def tokens(text: str) -> set[str]:
    stop = {"dans", "avec", "pour", "sans", "une", "des", "les", "que", "qui", "est", "sur", "par", "plus", "moins"}
    return {word for word in re.findall(r"[a-zà-ÿ0-9]+", str(text or "").lower()) if len(word) > 3 and word not in stop}


def sim(first: str, second: str) -> float:
    left, right = tokens(first), tokens(second)
    return len(left & right) / max(1, len(left | right))


def theme_text(text: str, max_words: int = 12) -> str:
    words = re.findall(r"[A-Za-zÀ-ÿ0-9%/._-]+", str(text or ""))
    return " ".join(words[:max_words])


def group_items(items: List[Dict[str, Any]], threshold: float = 0.28) -> List[Dict[str, Any]]:
    """Regroupe uniquement dans une même section sémantique.

    Le regroupement sert à limiter les répétitions d'affichage. La liste
    parallèle des candidats verrous est construite à partir des passages bruts,
    pas à partir de ces représentants : aucune preuve de verrou n'est perdue.
    """
    accepted = [dict(item) for item in items or [] if item.get("accepted_for_semantic_section", item.get("accepted_for_synthesis"))]
    accepted.sort(key=lambda item: float(item.get("rank_score") or 0.0), reverse=True)
    clusters: List[List[Dict[str, Any]]] = []

    for item in accepted:
        role = semantic_role(item)
        item["semantic_role"] = role
        item["role"] = role
        for cluster in clusters:
            representative = cluster[0]
            if semantic_role(representative) == role and sim(item.get("text", ""), representative.get("text", "")) >= threshold:
                cluster.append(item)
                break
        else:
            clusters.append([item])

    groups: List[Dict[str, Any]] = []
    for index, cluster in enumerate(clusters):
        cluster.sort(key=lambda item: float(item.get("rank_score") or 0.0), reverse=True)
        representative = dict(cluster[0])
        representative["group_id"] = f"semantic_group_{index + 1}"
        representative["cluster_size"] = len(cluster)
        representative["theme_label"] = theme_text(representative.get("text", ""))
        representative["supporting_passages"] = [
            {
                key: item.get(key)
                for key in (
                    "passage_id",
                    "document",
                    "source_path",
                    "section_title",
                    "text",
                    "semantic_role",
                    "rank_score",
                    "lock_candidate",
                    "lock_candidate_score",
                )
            }
            for item in cluster
        ]
        groups.append(representative)
    return groups


def origin_priority(item: Dict[str, Any]) -> int:
    origin = str(item.get("content_origin") or "unknown")
    if origin in {"project_core", "cir_structured", "client_pre_cir"}:
        return 4
    if origin == "unknown":
        return 3
    if origin == "state_of_art":
        return 2
    return 1


def build_evidence_pack(
    groups: List[Dict[str, Any]],
    top_k: Optional[Dict[str, int]] = None,
    *,
    items: Optional[List[Dict[str, Any]]] = None,
    lock_candidates: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Construit le pack canonique V177.

    ``top_k`` ne s'applique qu'aux vues sémantiques. Il ne limite jamais
    ``candidats_verrou_nlp`` ni ``evidence_catalog``.
    """
    pack = empty_pack()
    for group in groups or []:
        key = ROLE_TO_PACK.get(semantic_role(group))
        if key:
            pack[key].append(dict(group))

    for key in ROLE_TO_PACK.values():
        ordered = sorted(
            pack[key],
            key=lambda item: (
                origin_priority(item),
                float(item.get("rank_score") or 0.0),
                int(item.get("cluster_size") or 1),
            ),
            reverse=True,
        )
        limit = int((top_k or {}).get(key, 0)) if isinstance(top_k, dict) else 0
        pack[key] = ordered[:limit] if limit > 0 else ordered

    catalog = dedupe_items(items or groups or [])
    candidates = lock_candidates
    if candidates is None:
        candidates = [item for item in catalog if item.get("lock_candidate")]

    pack[EVIDENCE_CATALOG_KEY] = catalog
    pack[LOCK_CANDIDATE_KEY] = dedupe_items(candidates or [])
    pack[QUALIFIED_LOCK_KEY] = []  # rempli uniquement par FrascatiGuard
    pack["_contract_version"] = "nlp_evidence_v177_before_lock_grouping"
    return pack
