# -*- coding: utf-8 -*-
"""
verrou_builder.py — V23

Ne fabrique pas de verrou global.
Il convertit le pack local en verrous locaux mieux contextualisés :
- importance du titre/section
- importance du type de document
- regroupement en thèmes de preuves
"""
from __future__ import annotations

import re
from typing import Dict, Any, List

from .evidence_graph import build_verrou_evidence_graph


def norm(text: str) -> str:
    text = str(text or "").lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    text = text.translate(tr)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return default


def _looks_like_noise(text: str) -> bool:
    low = norm(text)
    if not low or len(low) < 35:
        return True
    chars = len(low)
    digits = sum(c.isdigit() for c in low)
    if chars and digits / chars > 0.48:
        return True
    if low.count("|") >= 4:
        return True
    return False


def local_verrou_score(item: Dict[str, Any]) -> float:
    role = item.get("role")
    hint = item.get("section_role_hint") or "unknown"
    confidence = _safe_float(item.get("confidence") or item.get("model_confidence"))
    verrou_score = _safe_float(item.get("verrou_score"))
    rank_score = _safe_float(item.get("rank_score"))

    score = 0.0
    if role == "verrou":
        score += 0.45
    elif role == "limite":
        score += 0.30
    elif role in {"methode", "resultat"}:
        score += 0.10

    if hint in {"verrou", "limite"}:
        score += 0.35
    elif role == "verrou" and hint in {"methode", "resultat", "etat_art", "parametre"}:
        score -= 0.18

    score += min(confidence, 1.0) * 0.15
    score += min(verrou_score, 1.0) * 0.30
    score += min(rank_score, 1.5) * 0.15

    if item.get("document_type") in {"project_note", "project_presentation", "project_methodology"}:
        score += 0.10
    if item.get("document_type") in {"scientific_article", "benchmark_or_state_of_art"} and role == "verrou":
        score -= 0.25

    return round(max(0.0, min(score, 1.5)), 4)


def _dedupe_key(item: Dict[str, Any]) -> str:
    return f"{norm(item.get('document',''))}|{norm(item.get('section_title',''))}|{norm(item.get('text',''))[:180]}"


def collect_candidate_sources(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    for key in ["verrous_rnd_locaux", "limites_locales", "methodes_locales", "resultats_locaux", "objectifs_locaux"]:
        for item in pack.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            x = dict(item)
            x["_source_category"] = key
            x["local_verrou_score"] = local_verrou_score(x)
            if x["local_verrou_score"] > 0:
                sources.append(x)
    return sources


def build_local_verrous(pack: Dict[str, Any], max_verrous: int = 12) -> List[Dict[str, Any]]:
    sources = collect_candidate_sources(pack)
    sources = [x for x in sources if not _looks_like_noise(x.get("text", ""))]

    # On privilégie les groupes evidence plutôt que les phrases isolées.
    graph = build_verrou_evidence_graph(pack, max_groups=max_verrous)
    selected: List[Dict[str, Any]] = []
    seen = set()

    for group in graph.get("verrou_evidence_groups", []) or []:
        main = dict(group.get("main_passage") or {})
        if not main:
            continue
        key = _dedupe_key(main)
        if key in seen:
            continue
        seen.add(key)
        main["role"] = "verrou"
        main["verrou_source"] = "contextual_evidence_group"
        main["verrou_theme"] = group.get("theme")
        main["local_verrou_score"] = group.get("score")
        main["needs_human_validation"] = True
        main["accepted_for_synthesis"] = True
        main["supporting_passages"] = group.get("preuves", [])
        selected.append(main)
        if len(selected) >= max_verrous:
            break

    # Fallback si aucun groupe : passages directs avec score fort.
    if not selected:
        for item in sorted(sources, key=lambda x: x.get("local_verrou_score", 0), reverse=True):
            if item.get("local_verrou_score", 0) < 0.55:
                continue
            key = _dedupe_key(item)
            if key in seen:
                continue
            seen.add(key)
            item["role"] = "verrou"
            item["verrou_source"] = "direct_contextual_candidate"
            item["needs_human_validation"] = True
            item["accepted_for_synthesis"] = True
            selected.append(item)
            if len(selected) >= max_verrous:
                break

    for i, item in enumerate(selected, start=1):
        item.setdefault("cluster_id", f"verrou_local_{i:03d}")
    return selected


def enrich_evidence_pack_with_verrous(pack: Dict[str, Any]) -> Dict[str, Any]:
    pack = dict(pack or {})
    graph = build_verrou_evidence_graph(pack, max_groups=12)
    pack["verrous_rnd_locaux"] = build_local_verrous(pack, max_verrous=12)
    pack["verrou_evidence_graph"] = graph
    return pack
