# -*- coding: utf-8 -*-
"""Agrégation multi-documents du contrat NLP V177."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List

from .evidence_contract import (
    LOCK_CANDIDATE_KEY,
    QUALIFIED_LOCK_KEY,
    SECONDARY_TECHNICAL_GROUPS_KEY,
    SEMANTIC_PACK_KEYS,
    TECHNICAL_GROUPS_KEY,
    normalize_pack,
    passage_identity,
    semantic_role,
)

# Compatibilité avec les anciens consommateurs.
PACK_KEYS = [*SEMANTIC_PACK_KEYS, QUALIFIED_LOCK_KEY]
ROLE_TO_PACK = {
    "objectif": "objectifs_locaux",
    "verrou": QUALIFIED_LOCK_KEY,
    "methode": "methodes_locales",
    "resultat": "resultats_locaux",
    "limite": "limites_locales",
    "contribution": "contributions_locales",
    "etat_art": "etat_art_local",
    "parametre": "parametres_locaux",
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _doc_name(item: Dict[str, Any]) -> str:
    return str(item.get("document") or item.get("file_name") or "document_inconnu")


def _score_item(item: Dict[str, Any]) -> float:
    try:
        rank = float(item.get("rank_score") or 0.0)
        semantic_conf = float(item.get("semantic_role_confidence") or item.get("model_confidence") or 0.0)
        lock_score = float(item.get("frascati_score") or item.get("lock_candidate_score") or 0.0)
        weight = float(item.get("document_weight") or item.get("source_weight") or 0.55)
    except (TypeError, ValueError):
        return 0.0
    return round(rank or semantic_conf * weight + 0.15 * lock_score, 4)


def _short_text(text: Any, limit: int = 700) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit].rsplit(" ", 1)[0] + "…"


def _semantic_items(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen = set()
    for key in SEMANTIC_PACK_KEYS:
        for raw in _safe_list(pack.get(key)):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            pid = passage_identity(item)
            if pid in seen:
                continue
            seen.add(pid)
            item["_pack_key"] = key
            item["semantic_role"] = semantic_role(item)
            items.append(item)
            for support in _safe_list(item.get("supporting_passages")):
                if not isinstance(support, dict):
                    continue
                child = dict(support)
                child.setdefault("document", item.get("document"))
                child.setdefault("semantic_role", item.get("semantic_role"))
                child.setdefault("role", item.get("semantic_role"))
                child["_pack_key"] = key
                child_pid = passage_identity(child)
                if child_pid not in seen:
                    seen.add(child_pid)
                    items.append(child)
    return items


def _qualified_counts_by_document(groups: List[Dict[str, Any]]) -> tuple[Counter, Counter]:
    """Compte les groupes et les passages sources pour chaque document.

    Un groupe de verrou peut réunir des preuves de plusieurs documents. Le
    compteur historique, basé seulement sur le document représentatif, faisait
    disparaître cette information dans le résumé consultant.
    """
    passage_counts: Counter = Counter()
    group_counts: Counter = Counter()
    for group in groups or []:
        supports = [item for item in _safe_list(group.get("supporting_passages")) if isinstance(item, dict)]
        if not supports:
            name = _doc_name(group)
            passage_counts[name] += 1
            group_counts[name] += 1
            continue
        seen_docs = set()
        for support in supports:
            name = _doc_name(support)
            passage_counts[name] += 1
            seen_docs.add(name)
        for name in seen_docs:
            group_counts[name] += 1
    return passage_counts, group_counts


def build_document_evidence_summaries(
    documents: List[Dict[str, Any]],
    pack: Dict[str, Any],
    max_items_per_role: int = 4,
) -> List[Dict[str, Any]]:
    normalized = normalize_pack(pack)
    semantic_items = _semantic_items(normalized)
    candidates = normalized.get(LOCK_CANDIDATE_KEY, [])
    qualified_locks = normalized.get(QUALIFIED_LOCK_KEY, [])

    by_doc: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in semantic_items:
        by_doc[_doc_name(item)].append(item)
    candidate_counts = Counter(_doc_name(item) for item in candidates)
    qualified_counts, qualified_group_counts = _qualified_counts_by_document(qualified_locks)

    summaries = []
    for document in documents or []:
        name = _doc_name(document)
        roles: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in by_doc.get(name, []):
            roles[semantic_role(item)].append(item)

        evidence_by_role: Dict[str, List[Dict[str, Any]]] = {}
        for role, values in roles.items():
            selected = []
            seen = set()
            for item in sorted(values, key=_score_item, reverse=True):
                text = _short_text(item.get("text") or item.get("source_text"), 500)
                key = _norm(text)[:180]
                if not text or key in seen:
                    continue
                seen.add(key)
                selected.append(
                    {
                        "passage_id": passage_identity(item),
                        "text": text,
                        "semantic_role": role,
                        "pack_key": item.get("_pack_key"),
                        "rank_score": item.get("rank_score"),
                        "lock_candidate": item.get("lock_candidate", False),
                        "lock_candidate_score": item.get("lock_candidate_score"),
                        "frascati_decision": item.get("frascati_decision"),
                        "section_title": item.get("section_title"),
                    }
                )
                if len(selected) >= max_items_per_role:
                    break
            if selected:
                evidence_by_role[role] = selected

        summaries.append(
            {
                "document": name,
                "source_path": document.get("source_path"),
                "extension": document.get("extension"),
                "chars": len(str(document.get("text") or "")),
                "content_origin": document.get("content_origin", "unknown"),
                "document_type": document.get("document_type", "unknown_document"),
                "source_policy": document.get("source_policy", "secondary"),
                "document_weight": document.get("document_weight", document.get("source_weight")),
                "document_type_confidence": document.get("document_type_confidence"),
                "counts_by_semantic_role": {role: len(values) for role, values in roles.items()},
                "lock_candidate_count": candidate_counts[name],
                "qualified_lock_passage_count": qualified_counts[name],
                "qualified_lock_group_count": qualified_group_counts[name],
                "has_evidence": bool(evidence_by_role),
                "evidence_by_role": evidence_by_role,
            }
        )
    return summaries


def _select_balanced_items(source: List[Dict[str, Any]], max_total: int, max_per_doc: int) -> List[Dict[str, Any]]:
    """Vue de prompt seulement ; le pack canonique reste complet."""
    ordered = sorted([dict(item) for item in source if isinstance(item, dict)], key=_score_item, reverse=True)
    selected: List[Dict[str, Any]] = []
    per_doc: Counter = Counter()
    seen = set()
    for item in ordered:
        pid = passage_identity(item)
        doc = _doc_name(item)
        if pid in seen or per_doc[doc] >= max_per_doc:
            continue
        selected.append(item)
        seen.add(pid)
        per_doc[doc] += 1
        if len(selected) >= max_total:
            return selected
    for item in ordered:
        pid = passage_identity(item)
        if pid in seen:
            continue
        selected.append(item)
        seen.add(pid)
        if len(selected) >= max_total:
            break
    return selected


def build_multi_document_pack_for_ennodiagnostic(
    pack: Dict[str, Any],
    document_summaries: List[Dict[str, Any]],
    max_total_per_category: Dict[str, int] | None = None,
    max_per_doc: int = 3,
) -> Dict[str, Any]:
    """Retourne le pack complet et une vue équilibrée optionnelle de prompt."""
    normalized = normalize_pack(pack)
    technical_groups_total = len(normalized.get(TECHNICAL_GROUPS_KEY, []))
    # Le convertisseur RAG historique donne la priorité à
    # ``technical_lock_groups`` et les étiquette tous comme verrous. Cette vue
    # complète reste dans le rapport Frascati, mais ne doit jamais entrer dans
    # le pack destiné à json_to_chunks.
    out: Dict[str, Any] = {
        key: list(value)
        for key, value in normalized.items()
        if key != TECHNICAL_GROUPS_KEY
    }

    prompt_limits = max_total_per_category or {
        "objectifs_locaux": 12,
        "methodes_locales": 16,
        "resultats_locaux": 12,
        "limites_locales": 16,
        "contributions_locales": 10,
        "etat_art_local": 10,
        "parametres_locaux": 10,
        # Les verrous sont déjà regroupés en axes de preuves. Cette valeur est
        # ignorée ci-dessous : aucune coupe n'est appliquée à ces axes.
        "verrous_rnd_locaux": 0,
    }
    prompt_view = {
        key: _select_balanced_items(out.get(key, []), prompt_limits.get(key, 12), max_per_doc)
        for key in SEMANTIC_PACK_KEYS
    }
    # Un axe de verrou ne doit jamais être masqué par une limite de prompt.
    # Si le contexte devient trop long, EnnoDiagnostic doit le traiter en
    # batch, sans changer le nombre de groupes finaux.
    prompt_view[QUALIFIED_LOCK_KEY] = [dict(item) for item in out.get(QUALIFIED_LOCK_KEY, [])]

    docs_with_evidence = [item for item in document_summaries if item.get("has_evidence")]
    type_counts = Counter(item.get("document_type", "unknown_document") for item in document_summaries)
    policy_counts = Counter(item.get("source_policy", "secondary") for item in document_summaries)
    used_docs = Counter(_doc_name(item) for key in SEMANTIC_PACK_KEYS for item in out.get(key, []))
    total_used = sum(used_docs.values())
    dominant_doc = used_docs.most_common(1)[0][0] if used_docs else None
    dominant_ratio = used_docs[dominant_doc] / total_used if dominant_doc and total_used else 0.0

    out["document_evidence_summaries"] = document_summaries
    out["prompt_balanced_view"] = prompt_view
    out["coverage_report"] = {
        "documents_total": len(document_summaries),
        "documents_with_evidence": len(docs_with_evidence),
        "documents_without_evidence": [item.get("document") for item in document_summaries if not item.get("has_evidence")],
        "document_types_count": dict(type_counts),
        "source_policies_count": dict(policy_counts),
        "used_sources_by_document": dict(used_docs),
        "dominant_document": dominant_doc,
        "dominant_ratio": round(dominant_ratio, 4),
        "semantic_evidence_count": sum(len(out.get(key, [])) for key in SEMANTIC_PACK_KEYS),
        "lock_candidates_total": len(out.get(LOCK_CANDIDATE_KEY, [])),
        "qualified_lock_groups_total": len(out.get(QUALIFIED_LOCK_KEY, [])),
        "technical_groups_total": technical_groups_total,
        "secondary_technical_groups_total": len(out.get(SECONDARY_TECHNICAL_GROUPS_KEY, [])),
        "qualified_lock_passages_total": sum(
            len(_safe_list(group.get("supporting_passages"))) or 1
            for group in out.get(QUALIFIED_LOCK_KEY, [])
            if isinstance(group, dict)
        ),
        "note": (
            "Le pack canonique n'est pas tronqué. Seuls les groupes principaux sont exposés comme verrous ; "
            "les sous-problèmes restent dans secondary_technical_groups."
        ),
    }
    out["_contract_version"] = "nlp_evidence_v189_single_grouping_before_frascati"
    return out


def build_ennodiagnostic_nlp_context(documents: List[Dict[str, Any]], pack: Dict[str, Any]) -> Dict[str, Any]:
    summaries = build_document_evidence_summaries(documents=documents, pack=pack)
    multi = build_multi_document_pack_for_ennodiagnostic(pack=pack, document_summaries=summaries)
    return {
        "document_evidence_summaries": summaries,
        "multi_document_evidence_pack_for_ennodiagnostic": multi,
        "coverage_report": multi.get("coverage_report", {}),
    }
