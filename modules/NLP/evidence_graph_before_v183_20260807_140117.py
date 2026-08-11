# -*- coding: utf-8 -*-
from __future__ import annotations

"""Regroupement interdocuments V177, piloté par des graines de verrou.

Principes :
- une preuve de support ne peut jamais créer seule un groupe ;
- seules les graines exprimant une incertitude technique réelle sont clusterisées ;
- les preuves complémentaires sont rattachées ensuite au meilleur groupe ;
- les fenêtres fortement chevauchantes sont dédupliquées ;
- complete-linkage évite les fusions transitives.
"""

from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
import hashlib
import math
import re
import unicodedata

from .candidates import enrich_candidates
from .cleaner import is_noise_line

VERSION = "lock_evidence_grouping_v178_domain_neutral_support_attachment"

ROLE_PAIRS = {
    frozenset(("objectif", "methode")), frozenset(("objectif", "resultat")),
    frozenset(("methode", "resultat")), frozenset(("parametre", "limite")),
    frozenset(("parametre", "resultat")), frozenset(("resultat", "limite")),
    frozenset(("methode", "limite")), frozenset(("contribution", "resultat")),
}
STOPWORDS = {
    "avec", "dans", "pour", "sans", "sous", "entre", "ainsi", "cela", "cette",
    "comme", "plus", "moins", "apres", "avant", "etre", "avoir", "document",
    "rapport", "section", "page", "essai", "essais", "resultat", "resultats",
    "aussi", "fait", "date", "modification", "written", "redige", "realise",
    "realises", "realis", "technical", "dependency", "open_validation", "rev",
    "ci-dessous", "compose", "composant", "reference", "references", "droite",
    "gauche", "surface", "conditions", "eventuelles", "relativement", "votre",
    "semble", "donnees", "generale", "creation", "propriete", "ajout",
    "probleme", "problemes", "fonctionnement", "systeme", "technique", "techniques",
    "determiner", "valider", "verification", "influence", "impact", "performance",
}
CAUSE_WORDS = {
    "cause", "provoque", "entraine", "induit", "depend", "influence", "impact",
    "origine", "mecanisme", "incertitude", "inconnue", "compromis",
}
# Grandeur écrite sous la forme « valeur + unité ». Le motif reste volontairement
# neutre pour ne pas privilégier un secteur industriel ou une unité donnée.
CONDITION_PATTERN = re.compile(
    r"(?<![a-z0-9])[-+]?\d+(?:[.,]\d+)?\s*(?:%|°\s*[cfk]|[a-zµ]{1,6}(?:/[a-z0-9µ]{1,6})?)\b",
    re.I,
)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%+./_-]+", " ", text)).strip()


def _tokens(value: Any) -> Set[str]:
    return {
        token for token in _norm(value).split()
        if len(token) >= 4 and token not in STOPWORDS and not any(c.isdigit() for c in token)
    }


def _role(item: Mapping[str, Any]) -> str:
    return _norm(item.get("semantic_role") or item.get("original_model_role") or item.get("role"))


def _text(item: Mapping[str, Any]) -> str:
    return str(item.get("analysis_text") or item.get("text") or "").strip()


STRUCTURING_TEXT_PATTERN = re.compile(
    r"\b(?:incertitud|verrou|non[ -](?:realisable|maitris|resolu|determin|valid|garanti)|"
    r"impossib|reste(?:nt)? a|a (?:valider|confirmer|verifier|determiner|caracteriser)|"
    r"essais? complementaires?|ne (?:peut|peuvent) (?:pas |etre )?)\b",
    re.I,
)


def _representative_quality(item: Mapping[str, Any]) -> Tuple[float, float, float, int]:
    """Privilégie la phrase qui formule le problème, pas une ligne de mesures."""
    features = item.get("lock_candidate_features") or {}
    source_text = str(item.get("text") or "").strip()
    analysis_text = _text(item)
    feature_score = (
        5.0 * bool(features.get("knowledge_gap"))
        + 4.0 * bool(features.get("tradeoff"))
        + 3.0 * bool(features.get("uncertainty"))
        + 2.0 * bool(features.get("open_validation"))
        + 1.0 * bool(features.get("causal_gap"))
        + 2.0 * bool(item.get("lock_candidate_explicit"))
    )
    # Un signal présent dans le passage cité est préférable à un signal situé
    # uniquement dans son contexte voisin.
    feature_score += 2.0 * bool(STRUCTURING_TEXT_PATTERN.search(_norm(source_text)))
    alpha_count = len(re.findall(r"[A-Za-zÀ-ÿ]{3,}", source_text))
    digit_count = len(re.findall(r"\d", source_text))
    readability = min(alpha_count, 80) / 80.0 - min(digit_count, 60) / 120.0
    return (
        feature_score,
        readability,
        float(item.get("lock_evidence_score") or 0.0),
        len(analysis_text),
    )


def _representative_excerpt(item: Mapping[str, Any], limit: int = 1200) -> str:
    """Assemble uniquement des fragments sources utiles et traçables."""
    source_text = str(item.get("text") or "").strip()
    section_title = str(item.get("section_title") or "").strip()
    context_before = str(item.get("context_before") or "").strip()
    parts: List[str] = []
    for value in (section_title, context_before, source_text):
        value = re.sub(r"\s+", " ", value).strip(" -|.;")
        if len(value) < 20 or is_noise_line(value):
            continue
        normalized = _norm(value)
        if any(normalized in _norm(existing) or _norm(existing) in normalized for existing in parts):
            if len(value) > max((len(existing) for existing in parts), default=0):
                parts = [existing for existing in parts if _norm(existing) not in normalized]
            else:
                continue
        parts.append(value)
    excerpt = " ".join(parts) or source_text or _text(item)
    return excerpt[:limit].rsplit(" ", 1)[0] if len(excerpt) > limit else excerpt


def _document_key(item: Mapping[str, Any]) -> str:
    return _norm(item.get("source_path") or item.get("document") or "")


def _span(item: Mapping[str, Any]) -> Tuple[int, int]:
    start = int(item.get("sentence_start") or 0)
    size = max(1, int(item.get("window_size") or 1))
    return start, start + size


def _overlap_ratio(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    if _document_key(left) != _document_key(right):
        return 0.0
    l0, l1 = _span(left)
    r0, r1 = _span(right)
    overlap = max(0, min(l1, r1) - max(l0, r0))
    base = max(1, min(l1 - l0, r1 - r0))
    return overlap / base


def _near_duplicate(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if _document_key(left) != _document_key(right):
        return False
    if _overlap_ratio(left, right) >= 0.67:
        return True
    lt, rt = _tokens(_text(left)), _tokens(_text(right))
    union = lt | rt
    return bool(union and len(lt & rt) / len(union) >= 0.82)


def _deduplicate_windows(items: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    ordered = sorted(
        (dict(item) for item in items),
        key=lambda item: (
            int(bool(item.get("direct_lock_candidate"))),
            float(item.get("lock_evidence_score") or 0.0),
            float(item.get("rank_score") or 0.0),
            len(_text(item)),
        ),
        reverse=True,
    )
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    for item in ordered:
        duplicate_of = next((other for other in kept if _near_duplicate(item, other)), None)
        if duplicate_of is None:
            kept.append(item)
        else:
            removed.append({
                "passage_id": item.get("passage_id"),
                "duplicate_of": duplicate_of.get("passage_id"),
                "document": item.get("document"),
            })
    return kept, removed


def build_evidence_signature(item: Mapping[str, Any]) -> str:
    profile = item.get("concept_profile") or {}
    extras: List[str] = []
    if isinstance(profile, dict):
        for key in ("top_terms", "top_phrases", "technical_entities"):
            value = profile.get(key) or []
            if isinstance(value, list):
                extras.extend(str(v) for v in value[:20])
    fields = [
        item.get("section_title"),
        item.get("analysis_text") or item.get("text"),
        " ".join(extras),
    ]
    return "\n".join(str(value).strip() for value in fields if value)


def _conditions(item: Mapping[str, Any]) -> Set[str]:
    return {_norm(match.group(0)) for match in CONDITION_PATTERN.finditer(build_evidence_signature(item))}


def _anchors(item: Mapping[str, Any]) -> Set[str]:
    return _tokens(build_evidence_signature(item))


def _jaccard(left: Set[str], right: Set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    nl = math.sqrt(sum(float(a) ** 2 for a in left))
    nr = math.sqrt(sum(float(b) ** 2 for b in right))
    return dot / (nl * nr) if nl and nr else 0.0


def pair_score(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    semantic_similarity: float,
    *,
    for_support_attachment: bool = False,
) -> Tuple[float, Dict[str, Any]]:
    left_anchors, right_anchors = _anchors(left), _anchors(right)
    shared_anchors = sorted(left_anchors & right_anchors)
    anchor_overlap = _jaccard(left_anchors, right_anchors)
    condition_overlap = _jaccard(_conditions(left), _conditions(right))
    role_pair = frozenset((_role(left), _role(right)))
    complementary = role_pair in ROLE_PAIRS or _role(left) == _role(right)
    joined = _norm(build_evidence_signature(left) + " " + build_evidence_signature(right))
    causal = any(word in joined for word in CAUSE_WORDS) and bool(shared_anchors)
    same_doc = _document_key(left) == _document_key(right)

    score = 0.60 * max(0.0, semantic_similarity)
    score += min(0.18, anchor_overlap * 0.50)
    score += 0.07 if complementary else 0.0
    score += min(0.08, condition_overlap * 0.25)
    score += 0.08 if causal else 0.0
    score += 0.02 if same_doc else 0.0

    dimensions = sum([
        semantic_similarity >= (0.48 if for_support_attachment else 0.50),
        anchor_overlap >= 0.06,
        len(shared_anchors) >= 2,
        condition_overlap > 0,
        causal,
    ])
    left_type = _norm(left.get("document_type"))
    right_type = _norm(right.get("document_type"))
    design_support = bool(
        for_support_attachment
        and (left_type in {"plan_schema", "conception_technique", "unknown_document"}
             or right_type in {"plan_schema", "conception_technique", "unknown_document"})
    )
    threshold = 0.50 if for_support_attachment else 0.56
    minimum_semantic = 0.54 if design_support else (0.56 if for_support_attachment else 0.52)
    minimum_anchors = 2
    topic_gate = bool(
        len(shared_anchors) >= minimum_anchors
        or anchor_overlap >= 0.08
        or semantic_similarity >= (0.64 if for_support_attachment else 0.62)
        or (condition_overlap > 0 and len(shared_anchors) >= 1)
    )
    allowed = bool(
        score >= threshold
        and dimensions >= 2
        and topic_gate
        and (
            semantic_similarity >= minimum_semantic
            or len(shared_anchors) >= minimum_anchors
            or condition_overlap > 0
        )
        and not (design_support and not shared_anchors and condition_overlap == 0)
    )
    return round(min(score, 1.0), 4), {
        "allowed": allowed,
        "dimensions": dimensions,
        "semantic_similarity": round(float(semantic_similarity), 4),
        "anchor_overlap": round(anchor_overlap, 4),
        "condition_overlap": round(condition_overlap, 4),
        "roles_complementary": complementary,
        "causal_relation": causal,
        "same_document": same_doc,
        "shared_anchors": shared_anchors[:12],
    }


def _group_signature(items: Sequence[Mapping[str, Any]]) -> str:
    return "\n---\n".join(build_evidence_signature(item) for item in items)


def _merge_group(items: Sequence[Mapping[str, Any]], order: int) -> Dict[str, Any]:
    passages = [dict(item) for item in items]
    seeds = [item for item in passages if item.get("direct_lock_candidate")]
    representative_pool = seeds or passages
    representative = max(representative_pool, key=_representative_quality)
    representative_text = _representative_excerpt(representative)
    documents = sorted({str(item.get("document") or "").strip() for item in passages if item.get("document")})
    roles = sorted({_role(item) for item in passages if _role(item)})
    digest_source = "|".join(sorted(str(item.get("passage_id") or "") for item in seeds or passages))
    digest = hashlib.sha1(digest_source.encode("utf-8", errors="ignore")).hexdigest()[:16]
    evidence_scores = [float(item.get("lock_evidence_score") or 0.0) for item in passages]
    explicit_seed_count = sum(1 for item in seeds if item.get("lock_candidate_explicit") or _role(item) == "verrou")
    structuring_seed_count = sum(
        1
        for item in seeds
        if any(
            bool((item.get("lock_candidate_features") or {}).get(name))
            for name in ("open_validation", "tradeoff", "knowledge_gap")
        )
        or bool(item.get("lock_candidate_explicit"))
    )
    if structuring_seed_count and len(documents) >= 2 and len(seeds) >= 1:
        technical_scope = "project_structuring_lock"
    elif structuring_seed_count or explicit_seed_count:
        technical_scope = "lock_to_validate"
    else:
        technical_scope = "local_technical_subproblem"
    return {
        "passage_id": f"technical_group_{digest}",
        "lock_group_id": f"lock_group_{order:03d}_{digest}",
        "technical_group_candidate": True,
        "lock_group_candidate": True,
        "technical_scope": technical_scope,
        "display_as_main_lock": technical_scope in {"project_structuring_lock", "lock_to_validate"},
        "derived_view": "seeded_technical_lock_group_before_frascati_assessment",
        "grouping_version": VERSION,
        "text": representative_text,
        "source_text": representative.get("text") or "",
        "analysis_text": _group_signature(passages),
        "document": representative.get("document") or "",
        "source_path": representative.get("source_path") or "",
        "section_title": representative.get("section_title") or "",
        "source_semantic_roles": roles,
        "supporting_passages": passages,
        "supporting_documents": [
            {"document": doc, "passage_count": sum(1 for p in passages if str(p.get("document") or "").strip() == doc)}
            for doc in documents
        ],
        "evidence_count": len(passages),
        "direct_candidate_count": len(seeds),
        "structuring_seed_count": structuring_seed_count,
        "supporting_evidence_count": len(passages) - len(seeds),
        "seed_passage_ids": [str(item.get("passage_id") or "") for item in seeds],
        "lock_candidate_score_mean": round(sum(evidence_scores) / len(evidence_scores), 4) if evidence_scores else 0.0,
        "concept_profile": {
            "top_terms": [
                term for term, _ in Counter(
                    token for item in passages for token in _tokens(build_evidence_signature(item))
                ).most_common(24)
            ],
            "semantic_roles": roles,
            "documents_count": len(documents),
            "passages_count": len(passages),
        },
    }


def build_technical_lock_groups(
    candidates: Iterable[Mapping[str, Any]],
    *,
    encode_texts: Optional[Callable[[List[str]], Sequence[Sequence[float]]]] = None,
    minimum_complete_link_score: float = 0.56,
    support_attachment_minimum: float = 0.50,
    support_attachment_margin: float = 0.04,
    max_support_per_group: int = 24,
) -> Dict[str, Any]:
    enriched = [
        item for item in enrich_candidates(candidates)
        if item.get("direct_lock_candidate") or item.get("supporting_lock_evidence")
    ]
    deduped, duplicate_audit = _deduplicate_windows(enriched)
    seeds = [item for item in deduped if item.get("direct_lock_candidate")]
    supports = [item for item in deduped if not item.get("direct_lock_candidate") and item.get("supporting_lock_evidence")]

    if not seeds:
        return {
            "version": VERSION,
            "method": "seeded_cross_document_complete_linkage",
            "groups": [],
            "pairwise_audit": [],
            "support_attachment_audit": [],
            "candidates_count": len(deduped),
            "seed_count": 0,
            "support_count": len(supports),
            "duplicates_removed": duplicate_audit,
            "candidate_passages": deduped,
            "warning": "no_direct_seed_candidate",
        }

    all_items = seeds + supports
    signatures = [build_evidence_signature(item) for item in all_items]
    vectors: Sequence[Sequence[float]] = []
    if encode_texts is not None:
        try:
            vectors = encode_texts(signatures)
        except Exception:
            vectors = []

    def semantic(i: int, j: int) -> float:
        if vectors:
            return _cosine(vectors[i], vectors[j])
        return _jaccard(_tokens(signatures[i]), _tokens(signatures[j]))

    pair_cache: Dict[Tuple[int, int], Tuple[float, Dict[str, Any]]] = {}
    pairwise_audit: List[Dict[str, Any]] = []
    for i in range(len(seeds)):
        for j in range(i + 1, len(seeds)):
            score, details = pair_score(seeds[i], seeds[j], semantic(i, j))
            pair_cache[(i, j)] = (score, details)
            pairwise_audit.append({
                "left_passage_id": seeds[i].get("passage_id"),
                "right_passage_id": seeds[j].get("passage_id"),
                "score": score,
                **details,
            })

    clusters: List[List[int]] = [[index] for index in range(len(seeds))]
    while True:
        best: Optional[Tuple[int, int, float]] = None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                values: List[float] = []
                allowed = True
                for i in clusters[a]:
                    for j in clusters[b]:
                        key = (min(i, j), max(i, j))
                        score, details = pair_cache.get(key, (0.0, {"allowed": False}))
                        values.append(score)
                        if not details.get("allowed"):
                            allowed = False
                complete_score = min(values) if values else 0.0
                if allowed and complete_score >= minimum_complete_link_score:
                    if best is None or complete_score > best[2]:
                        best = (a, b, complete_score)
        if best is None:
            break
        a, b, _ = best
        clusters[a].extend(clusters[b])
        del clusters[b]

    cluster_items: List[List[Dict[str, Any]]] = [[dict(seeds[index]) for index in cluster] for cluster in clusters]
    support_audit: List[Dict[str, Any]] = []
    unassigned_supports: List[str] = []

    for support_offset, support in enumerate(supports, start=len(seeds)):
        candidates_for_attachment: List[Tuple[int, float, Dict[str, Any]]] = []
        for cluster_index, seed_indexes in enumerate(clusters):
            values: List[float] = []
            details_list: List[Dict[str, Any]] = []
            for seed_index in seed_indexes:
                score, details = pair_score(
                    support,
                    seeds[seed_index],
                    semantic(support_offset, seed_index),
                    for_support_attachment=True,
                )
                values.append(score)
                details_list.append(details)
            cluster_score = max(values) if values else 0.0
            best_details = details_list[values.index(cluster_score)] if values else {"allowed": False}
            if best_details.get("allowed") and cluster_score >= support_attachment_minimum:
                candidates_for_attachment.append((cluster_index, cluster_score, best_details))

        candidates_for_attachment.sort(key=lambda value: value[1], reverse=True)
        attached = False
        if candidates_for_attachment:
            best_cluster, best_score, best_details = candidates_for_attachment[0]
            second_score = candidates_for_attachment[1][1] if len(candidates_for_attachment) > 1 else 0.0
            margin_ok = best_score - second_score >= support_attachment_margin or best_score >= 0.67
            capacity_ok = len(cluster_items[best_cluster]) < len(clusters[best_cluster]) + max_support_per_group
            if margin_ok and capacity_ok:
                cluster_items[best_cluster].append(dict(support))
                attached = True
            support_audit.append({
                "support_passage_id": support.get("passage_id"),
                "attached": attached,
                "cluster_index": best_cluster if attached else None,
                "best_score": round(best_score, 4),
                "second_score": round(second_score, 4),
                "margin_ok": margin_ok,
                "capacity_ok": capacity_ok,
                **best_details,
            })
        if not attached:
            unassigned_supports.append(str(support.get("passage_id") or ""))

    groups = [_merge_group(items, order + 1) for order, items in enumerate(cluster_items)]
    covered_ids = {
        str(item.get("passage_id") or "")
        for group in groups
        for item in group.get("supporting_passages") or []
    }
    seed_ids = {str(item.get("passage_id") or "") for item in seeds}

    return {
        "version": VERSION,
        "method": "seeded_cross_document_complete_linkage_then_support_attachment",
        "candidates_count": len(deduped),
        "seed_count": len(seeds),
        "support_count": len(supports),
        "groups_count": len(groups),
        "groups": groups,
        "pairwise_audit": pairwise_audit,
        "support_attachment_audit": support_audit,
        "duplicates_removed": duplicate_audit,
        "candidate_passages": deduped,
        "unassigned_support_passage_ids": unassigned_supports,
        "coverage": {
            "seed_count": len(seed_ids),
            "covered_seed_count": len(seed_ids & covered_ids),
            "seed_coverage_rate": round(len(seed_ids & covered_ids) / len(seed_ids), 4) if seed_ids else 1.0,
            "support_count": len(supports),
            "attached_support_count": len(covered_ids - seed_ids),
            "unassigned_support_count": len(unassigned_supports),
        },
        "invariants": {
            "every_group_has_seed": all(int(group.get("direct_candidate_count") or 0) >= 1 for group in groups),
            "support_cannot_create_group": True,
            "complete_linkage": True,
        },
    }
