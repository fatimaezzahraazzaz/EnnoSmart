# -*- coding: utf-8 -*-
"""grouping.py — V23 groupement par rôle + contexte de section."""
from __future__ import annotations
from typing import Dict, Any, List
import re

STOP = set("le la les des du de un une et ou en dans pour par avec sur au aux ce ces cette qui que quoi dont est sont ont plus moins très tres afin lors comme document section type role passage".split())
ROLE_KEYS = {
    "objectif": "objectifs_locaux",
    "verrou": "verrous_rnd_locaux",
    "methode": "methodes_locales",
    "parametre": "parametres_locaux",
    "resultat": "resultats_locaux",
    "limite": "limites_locales",
    "contribution": "contributions_locales",
}


def tokens(text: str) -> set:
    return {w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ0-9]{3,}", str(text or "")) if w.lower() not in STOP}


def sim(a: str, b: str) -> float:
    A, B = tokens(a), tokens(b)
    if not A or not B:
        return 0.0
    return len(A & B) / max(1, min(len(A), len(B)))


def theme_text(x: Dict[str, Any]) -> str:
    return " ".join([
        str(x.get("section_title") or ""),
        " ".join(str(p) for p in (x.get("section_path") or [])[-2:]),
        str(x.get("text") or ""),
    ])


def group_items(items: List[Dict[str, Any]], threshold: float = 0.52) -> List[Dict[str, Any]]:
    items = sorted([x for x in items if x.get("accepted_for_synthesis")], key=lambda x: x.get("rank_score", 0), reverse=True)
    groups: List[List[Dict[str, Any]]] = []

    for it in items:
        placed = False
        for g in groups:
            same_role = it.get("role") == g[0].get("role")
            same_section = it.get("section_title") and it.get("section_title") == g[0].get("section_title")
            related_text = sim(theme_text(it), theme_text(g[0])) >= threshold
            if same_role and (same_section or related_text):
                g.append(it)
                placed = True
                break
        if not placed:
            groups.append([it])

    reps = []
    counters: Dict[str, int] = {}
    for g in groups:
        g = sorted(g, key=lambda x: x.get("rank_score", 0), reverse=True)
        rep = dict(g[0])
        role = rep.get("role", "item")
        counters[role] = counters.get(role, 0) + 1
        rep["cluster_id"] = f"{role}_{counters[role]:03d}"
        rep["cluster_size"] = len(g)
        rep["theme_hint"] = rep.get("section_title") or rep.get("cluster_id")
        rep["supporting_passages"] = [
            {
                "text": x.get("text"),
                "document": x.get("document"),
                "content_origin": x.get("content_origin"),
                "document_type": x.get("document_type"),
                "section_title": x.get("section_title"),
                "section_role_hint": x.get("section_role_hint"),
                "passage_id": x.get("passage_id"),
                "confidence": x.get("confidence"),
                "verrou_score": x.get("verrou_score"),
                "quality_status": x.get("quality_status"),
                "rank_score": x.get("rank_score"),
            }
            for x in g[:8]
        ]
        reps.append(rep)
    return reps


def origin_priority(x: Dict[str, Any]) -> float:
    o = x.get("content_origin")
    dt = x.get("document_type")
    base = {"project_core": 3.0, "unknown": 2.0, "state_of_art": 1.0, "metadata": 0.2}.get(o, 1.5)

    if dt in {"concept_projet", "brevet", "preuve_depot_brevet", "rapport_test", "note_projet"}:
        base += 0.90
    elif dt in {"presentation_projet", "methodologie_protocole", "project_note", "project_presentation", "project_methodology"}:
        base += 0.45
    elif dt in {"etat_art_bibliographie", "scientific_article", "benchmark_or_state_of_art"}:
        base -= 0.30
    elif dt in {"notice_memoire_technique"}:
        base -= 0.60
    elif dt in {"norme_reglementation", "plan_schema", "administratif", "template_formulaire"}:
        base -= 1.60
    return base


def build_evidence_pack(groups: List[Dict[str, Any]], top_k=None) -> Dict[str, Any]:
    top_k = top_k or {
        "objectifs_locaux": 8,
        "verrous_rnd_locaux": 14,
        "methodes_locales": 12,
        "parametres_locaux": 10,
        "resultats_locaux": 10,
        "limites_locales": 10,
        "contributions_locales": 6,
    }
    pack = {v: [] for v in ROLE_KEYS.values()}
    for role, key in ROLE_KEYS.items():
        arr = [g for g in groups if g.get("role") == role]
        arr = sorted(arr, key=lambda x: (origin_priority(x), x.get("rank_score", 0), x.get("cluster_size", 1)), reverse=True)
        pack[key] = arr[:top_k.get(key, 8)]
    pack.setdefault("etat_art_local", [])
    return pack