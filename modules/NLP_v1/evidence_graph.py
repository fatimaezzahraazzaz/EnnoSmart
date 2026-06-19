# -*- coding: utf-8 -*-
"""
evidence_graph.py — V23

Construit une vision "groupe de preuves" au-dessus des passages.
Un verrou n'est plus seulement une phrase : c'est un thème local avec preuves.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List


def _norm(s: str) -> str:
    s = str(s or "").lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’", "aaaeeeeiioouuuc'")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return default


def _theme_key(item: Dict[str, Any]) -> str:
    sec = _norm(item.get("section_title") or item.get("theme_hint") or "")
    if sec and sec != "document":
        return sec[:120]
    text = _norm(item.get("text") or "")
    toks = [t for t in re.findall(r"[a-z0-9]{4,}", text) if t not in {"avec", "dans", "pour", "cette", "sont", "etre", "avoir"}]
    return " ".join(toks[:8])[:120]


def _verrou_strength(item: Dict[str, Any]) -> float:
    role = item.get("role")
    hint = item.get("section_role_hint") or "unknown"
    score = 0.0
    if role == "verrou":
        score += 0.45
    elif role == "limite":
        score += 0.30
    elif role in {"methode", "resultat"}:
        score += 0.10

    if hint in {"verrou", "limite"}:
        score += 0.35
    elif hint in {"methode", "resultat", "parametre", "etat_art"} and role == "verrou":
        score -= 0.20

    score += min(_safe_float(item.get("verrou_score")), 1.0) * 0.30
    score += min(_safe_float(item.get("rank_score")), 1.2) * 0.20

    if item.get("document_type") in {"project_note", "project_presentation", "project_methodology", "project_document"}:
        score += 0.10
    if item.get("document_type") in {"scientific_article", "benchmark_or_state_of_art"} and role == "verrou":
        score -= 0.20

    return round(max(0.0, min(score, 1.5)), 4)


def build_verrou_evidence_graph(pack: Dict[str, Any], max_groups: int = 10) -> Dict[str, Any]:
    sources: List[Dict[str, Any]] = []
    for key in ["verrous_rnd_locaux", "limites_locales", "methodes_locales", "resultats_locaux", "objectifs_locaux"]:
        for x in pack.get(key, []) or []:
            if isinstance(x, dict):
                item = dict(x)
                item["_source_category"] = key
                item["verrou_strength"] = _verrou_strength(item)
                if item["verrou_strength"] > 0:
                    sources.append(item)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in sources:
        key = _theme_key(item)
        grouped.setdefault(key, []).append(item)

    groups: List[Dict[str, Any]] = []
    for key, arr in grouped.items():
        arr = sorted(arr, key=lambda x: x.get("verrou_strength", 0), reverse=True)
        best = arr[0]
        avg = sum(_safe_float(x.get("verrou_strength")) for x in arr) / max(len(arr), 1)
        groups.append({
            "theme": best.get("section_title") or best.get("theme_hint") or key,
            "section_role_hint": best.get("section_role_hint"),
            "document_type": best.get("document_type"),
            "document": best.get("document"),
            "score": round(avg + min(len(arr), 5) * 0.05, 4),
            "main_passage": best,
            "preuves": arr[:8],
        })

    groups = sorted(groups, key=lambda g: g.get("score", 0), reverse=True)[:max_groups]
    return {"verrou_evidence_groups": groups, "count": len(groups)}
