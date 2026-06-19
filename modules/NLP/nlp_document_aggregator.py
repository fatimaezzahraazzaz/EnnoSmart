# -*- coding: utf-8 -*-
"""
nlp_document_aggregator.py — V1

Construit un pack multi-documents pour EnnoDiagnostic.
Objectif : EnnoDiagnostic ne doit plus dépendre seulement de 8 sources RAG.
Il reçoit une synthèse structurée de TOUS les documents traités.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

PACK_KEYS = [
    "objectifs_locaux", "verrous_rnd_locaux", "methodes_locales", "resultats_locaux",
    "limites_locales", "contributions_locales", "etat_art_local", "parametres_locaux",
]

ROLE_TO_PACK = {
    "objectif": "objectifs_locaux",
    "verrou": "verrous_rnd_locaux",
    "methode": "methodes_locales",
    "resultat": "resultats_locaux",
    "limite": "limites_locales",
    "contribution": "contributions_locales",
    "parametre": "parametres_locaux",
}


def _norm(s: str) -> str:
    s = str(s or "").lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _safe_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _doc_name(x: Dict[str, Any]) -> str:
    return str(x.get("document") or x.get("file_name") or "document_inconnu")


def _score_item(item: Dict[str, Any]) -> float:
    rank = float(item.get("rank_score") or 0.0)
    conf = float(item.get("confidence") or item.get("model_confidence") or 0.0)
    vs = float(item.get("verrou_score") or 0.0)
    weight = float(item.get("document_weight") or item.get("source_weight") or 0.55)
    role = item.get("role")
    base = rank or (conf * weight)
    if role in {"verrou", "limite"}:
        base += 0.25 * vs
    return round(base, 4)


def _short_text(text: str, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _iter_pack_items(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = []
    for key in PACK_KEYS:
        for item in _safe_list(pack.get(key)):
            if not isinstance(item, dict):
                continue
            x = dict(item)
            x["_pack_key"] = key
            items.append(x)
            for sp in _safe_list(item.get("supporting_passages")):
                if isinstance(sp, dict):
                    y = dict(sp)
                    y.setdefault("document", item.get("document"))
                    y.setdefault("role", item.get("role"))
                    y.setdefault("document_type", item.get("document_type"))
                    y.setdefault("source_policy", item.get("source_policy"))
                    y.setdefault("document_weight", item.get("document_weight"))
                    y["_pack_key"] = key
                    y["_supporting_from"] = item.get("passage_id") or item.get("cluster_id")
                    items.append(y)
    return items


def build_document_evidence_summaries(
    documents: List[Dict[str, Any]],
    pack: Dict[str, Any],
    max_items_per_role: int = 4,
) -> List[Dict[str, Any]]:
    """Résumé court par document, avec objectifs/verrous/méthodes/résultats trouvés."""
    items = _iter_pack_items(pack or {})
    by_doc: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for it in items:
        by_doc[_doc_name(it)].append(it)

    summaries = []
    for d in documents or []:
        name = _doc_name(d)
        doc_items = by_doc.get(name, [])
        roles = defaultdict(list)
        for it in doc_items:
            role = str(it.get("role") or "unknown")
            roles[role].append(it)

        role_blocks: Dict[str, List[Dict[str, Any]]] = {}
        for role, arr in roles.items():
            arr = sorted(arr, key=_score_item, reverse=True)
            selected = []
            seen = set()
            for it in arr:
                txt = _short_text(it.get("text") or it.get("source_text") or "", 500)
                key = _norm(txt)[:180]
                if not txt or key in seen:
                    continue
                seen.add(key)
                selected.append({
                    "text": txt,
                    "role": role,
                    "pack_key": it.get("_pack_key") or ROLE_TO_PACK.get(role),
                    "confidence": it.get("confidence"),
                    "verrou_score": it.get("verrou_score"),
                    "rank_score": it.get("rank_score"),
                    "quality_status": it.get("quality_status"),
                    "document_type": it.get("document_type"),
                    "source_policy": it.get("source_policy"),
                    "passage_id": it.get("passage_id") or it.get("original_passage_id"),
                })
                if len(selected) >= max_items_per_role:
                    break
            if selected:
                role_blocks[role] = selected

        summaries.append({
            "document": name,
            "source_path": d.get("source_path"),
            "extension": d.get("extension"),
            "chars": len(str(d.get("text") or "")),
            "content_origin": d.get("content_origin", "unknown"),
            "document_type": d.get("document_type", "unknown_document"),
            "source_policy": d.get("source_policy", "secondary"),
            "document_weight": d.get("document_weight", d.get("source_weight")),
            "document_type_confidence": d.get("document_type_confidence"),
            "counts_by_role": {r: len(v) for r, v in roles.items()},
            "has_evidence": bool(role_blocks),
            "evidence_by_role": role_blocks,
        })
    return summaries


def _select_balanced_items(
    pack: Dict[str, Any],
    key: str,
    max_total: int,
    max_per_doc: int,
    include_supporting: bool = False,
) -> List[Dict[str, Any]]:
    source = _safe_list((pack or {}).get(key))
    if include_supporting:
        expanded = []
        for it in source:
            if isinstance(it, dict):
                expanded.append(it)
                for sp in _safe_list(it.get("supporting_passages")):
                    if isinstance(sp, dict):
                        y = dict(sp)
                        y.setdefault("document", it.get("document"))
                        y.setdefault("role", it.get("role"))
                        y.setdefault("document_type", it.get("document_type"))
                        y.setdefault("source_policy", it.get("source_policy"))
                        y.setdefault("document_weight", it.get("document_weight"))
                        expanded.append(y)
        source = expanded

    arr = [x for x in source if isinstance(x, dict)]
    arr = sorted(arr, key=_score_item, reverse=True)

    selected = []
    per_doc = Counter()
    seen_text = set()

    # Premier passage : diversité documentaire.
    for it in arr:
        doc = _doc_name(it)
        txt = _short_text(it.get("text") or it.get("source_text") or "", 700)
        key_txt = _norm(txt)[:220]
        if not txt or key_txt in seen_text:
            continue
        if per_doc[doc] >= max_per_doc:
            continue
        x = dict(it)
        x["text"] = txt
        selected.append(x)
        seen_text.add(key_txt)
        per_doc[doc] += 1
        if len(selected) >= max_total:
            return selected

    # Si pas assez, compléter sans contrainte max_per_doc.
    for it in arr:
        txt = _short_text(it.get("text") or it.get("source_text") or "", 700)
        key_txt = _norm(txt)[:220]
        if not txt or key_txt in seen_text:
            continue
        x = dict(it)
        x["text"] = txt
        selected.append(x)
        seen_text.add(key_txt)
        if len(selected) >= max_total:
            break

    return selected


def build_multi_document_pack_for_ennodiagnostic(
    pack: Dict[str, Any],
    document_summaries: List[Dict[str, Any]],
    max_total_per_category: Dict[str, int] | None = None,
    max_per_doc: int = 3,
) -> Dict[str, Any]:
    """
    Pack destiné à EnnoDiagnostic :
    - garde les catégories classiques pour compatibilité,
    - ajoute document_summaries pour représenter tous les documents,
    - équilibre les preuves pour éviter qu'un seul document décide tout.
    """
    max_total_per_category = max_total_per_category or {
        "objectifs_locaux": 20,
        "verrous_rnd_locaux": 24,
        "methodes_locales": 20,
        "resultats_locaux": 16,
        "limites_locales": 18,
        "contributions_locales": 12,
        "parametres_locaux": 12,
        "etat_art_local": 10,
    }

    out = {k: [] for k in PACK_KEYS}
    for key in PACK_KEYS:
        out[key] = _select_balanced_items(
            pack=pack,
            key=key,
            max_total=max_total_per_category.get(key, 12),
            max_per_doc=max_per_doc,
            include_supporting=True,
        )

    # Statistiques de couverture.
    docs_with_evidence = [d for d in document_summaries if d.get("has_evidence")]
    type_counts = Counter(d.get("document_type", "unknown_document") for d in document_summaries)
    policy_counts = Counter(d.get("source_policy", "secondary") for d in document_summaries)

    used_docs = Counter()
    for key in PACK_KEYS:
        for item in out.get(key, []) or []:
            used_docs[_doc_name(item)] += 1

    total_used = sum(used_docs.values())
    dominant_doc = used_docs.most_common(1)[0][0] if used_docs else None
    dominant_ratio = (used_docs[dominant_doc] / total_used) if dominant_doc and total_used else 0.0

    out["document_evidence_summaries"] = document_summaries
    out["coverage_report"] = {
        "documents_total": len(document_summaries),
        "documents_with_evidence": len(docs_with_evidence),
        "documents_without_evidence": [d.get("document") for d in document_summaries if not d.get("has_evidence")],
        "document_types_count": dict(type_counts),
        "source_policies_count": dict(policy_counts),
        "used_sources_by_document": dict(used_docs),
        "dominant_document": dominant_doc,
        "dominant_ratio": round(dominant_ratio, 4),
        "note": "Ce pack représente tous les documents via document_evidence_summaries. Les catégories classiques sont équilibrées pour éviter une domination par un seul document.",
    }
    return out


def build_ennodiagnostic_nlp_context(
    documents: List[Dict[str, Any]],
    pack: Dict[str, Any],
) -> Dict[str, Any]:
    summaries = build_document_evidence_summaries(documents=documents, pack=pack)
    balanced = build_multi_document_pack_for_ennodiagnostic(pack=pack, document_summaries=summaries)
    return {
        "document_evidence_summaries": summaries,
        "multi_document_evidence_pack_for_ennodiagnostic": balanced,
        "coverage_report": balanced.get("coverage_report", {}),
    }
