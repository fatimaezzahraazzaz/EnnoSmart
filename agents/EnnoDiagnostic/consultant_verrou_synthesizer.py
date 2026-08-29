# -*- coding: utf-8 -*-
from __future__ import annotations

"""Reformulation consultant des groupes de verrous produits par le NLP.

V172 sépare strictement les responsabilités :
- le NLP construit les groupes techniques ;
- Frascati évalue l’éligibilité sans filtrer les groupes ;
- le regroupement sémantique unique est exécuté dans le NLP avant Frascati ;
- le RAG indexe chaque groupe sans le consolider ;
- EnnoDiagnostic reformule uniquement chaque groupe NLP déjà déterminé.

Aucun nombre de verrous n'est imposé. Toute preuve et tout identifiant de groupe
restent traçables. Une sortie LLM invalide déclenche une relance ciblée puis une
reformulation déterministe sûre, sans exposer de message interne au consultant.
"""

import hashlib
import json
import math
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


VERSION = "v190_nlp_group_passthrough_cached_reformulation"

VISIBLE_FIELDS = (
    "title",
    "scientific_uncertainty",
    "causal_chain",
    "why_lock",
    "why_not_simple_engineering",
    "evidence_summary",
    "consultant_check",
)


LOCK_ROLES = {"verrou_scientifique", "verrou_a_verifier"}
CONTEXT_ROLES = {"hypothese_methodologique", "resultat_technique", "contexte_technique"}
SUPPORT_ROLES = {"limite_de_mesure"}

_DECLARED_LOCK_SIGNAL_RE = re.compile(
    r"\b(?:verrou(?:s)? (?:important|majeur|technique|scientifique)?(?:\s+\w+){0,4}\s+"
    r"(?:tient|reside|concerne|provient|est lie)|forte contrainte|contrainte non negociable|"
    r"exigence non negociable|difficulte majeure|impossibilite de|non maitrise|"
    r"reste a demontrer|aucune solution (?:connue|satisfaisante)|limitation structurelle)\b",
    re.I,
)
_EXTERNAL_SECTION_RE = re.compile(
    r"\b(?:etat de l art|revue (?:de la litterature|bibliographique|des usages)|"
    r"travaux connexes|related work|state of the art|references?)\b",
    re.I,
)

INTERNAL_MARKERS = (
    "llm", "nlp", "parser", "fallback", "erreur", "sortie invalide",
    "contrat json", "cluster absent", "frascati en amont",
)

TITLE_STOPWORDS = {
    "avec", "dans", "pour", "sans", "sous", "entre", "vers", "chez",
    "des", "les", "une", "aux", "sur", "par", "que", "qui", "dont",
    "cette", "ces", "leur", "leurs", "plus", "moins", "ainsi", "afin",
    "technique", "scientifique", "projet", "systeme", "dispositif",
    "conditions", "optimales", "fonctionnement", "performance", "performances",
    "determination", "optimisation", "amelioration", "etude", "analyse",
}

GENERIC_TITLE_PATTERNS = (
    r"^determination\s+(?:des?|du|de la|d )?conditions?\s+optimales?",
    r"^determination\s+(?:(?:de la|de l|des?|du|d)\s+)?impact",
    r"^optimisation\s+(?:des?|du|de la|d )?(?:conditions?|fonctionnement|performances?)",
    r"^amelioration\s+(?:des?|du|de la|d )?(?:fonctionnement|performances?)",
    r"^(?:etude|analyse)\s+(?:des?|du|de la|d )?(?:fonctionnement|performances?)",
    r"^performance\s+insuffisante",
    r"^incertitude\s+technique\s+(?:du|de la|autour)",
)

TASK_TITLE_PATTERNS = (
    r"^(?:des?\s+)?essais?\s+(?:complementaires?\s+)?(?:doivent|devront|sont|seront)",
    r"^(?:determiner|evaluer|mesurer|analyser|tester|verifier|comparer)\b",
    r"^(?:nous|on|il|elle)\s+(?:cherchions|cherchons|cherche|devons|allons).*?\b(?:determiner|evaluer|mesurer|analyser|tester|verifier)\b",
    r"^(?:mise|mettre)\s+en\s+(?:place|essai)",
)

INCOMPLETE_TITLE_ENDINGS = {
    "dans", "de", "du", "des", "a", "au", "aux", "pour", "afin", "avec",
    "sans", "sous", "sur", "entre", "vers", "par", "en", "le", "la", "les",
}

# Un titre de verrou CIR doit être immédiatement compréhensible par un consultant :
# objet technique + comportement non maîtrisé + contrainte utile.
CONSULTANT_UNCERTAINTY_MARKERS = (
    "incertitude", "maitrise incertaine", "non maitrise", "non maitrisable", "non realisable",
    "impossibilite", "difficulte a garantir", "difficulte de garantir",
    "instabilite", "variabilite non expliquee", "comportement non predictible",
    "robustesse insuffisante", "absence de maitrise", "sensibilite non maitrisee",
    "equilibrage non maitrise", "equilibrage non realisable",
    "non garantie", "non garanti", "garantie insuffisante",
    "representativite incertaine", "validite incertaine",
    "fiabilite incertaine", "adequation incertaine",
    "representativite non garantie", "validite non garantie",
)

RAW_LABEL_PATTERNS = (
    r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    r"^(?:courbe|tableau|figure|annexe|essai|test|releve|mesure)\b",
    r"^nouveau\s+.+\s+par\s+rapport\s+(?:a\s+)?(?:ancien|ancienne)\b",
)

CONSULTANT_TITLE_MAX_WORDS = 22
CONSULTANT_TITLE_MIN_WORDS = 5


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9%+./_-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value if value not in (None, "") else default)
        return default if math.isnan(number) or math.isinf(number) else number
    except Exception:
        return default


def _safe_int(value: Any, default: int = -1) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = _norm(value)
    if normalized in {"1", "true", "yes", "oui"}:
        return True
    if normalized in {"0", "false", "no", "non"}:
        return False
    return default


def _truncate(value: Any, max_chars: int) -> str:
    text = _clean(value)
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    position = max(cut.rfind("."), cut.rfind(";"), cut.rfind(":"))
    if position < max_chars // 2:
        position = max_chars
    return cut[:position].rstrip() + "…"


def _contains_internal_marker(value: Any) -> bool:
    normalized = _norm(value)
    return any(_norm(marker) in normalized for marker in INTERNAL_MARKERS)


def _safe_visible(value: Any) -> str:
    text = _clean(value)
    if not text or _contains_internal_marker(text):
        return ""
    return text


def _dedupe_texts(values: Iterable[Any], max_items: Optional[int] = None) -> List[str]:
    output: List[str] = []
    seen: Set[str] = set()
    for value in values:
        text = _clean(value)
        signature = _norm(text)
        if not text or not signature or signature in seen:
            continue
        seen.add(signature)
        output.append(text)
        if max_items is not None and len(output) >= max_items:
            break
    return output


def _dedupe_dicts(values: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        signature = (
            _clean(value.get("source_path") or value.get("document")),
            _clean(value.get("passage_id") or value.get("rag_chunk_id") or value.get("id")),
            _norm(value.get("excerpt") or value.get("text") or value.get("analysis_text"))[:260],
        )
        if signature in seen:
            continue
        seen.add(signature)
        output.append(value)
    return output


def _source_identity(source: Mapping[str, Any]) -> str:
    for key in ("rag_chunk_id", "passage_id", "id", "original_passage_id"):
        value = source.get(key)
        if value:
            return str(value)
    raw = "|".join([
        _clean(source.get("source_path") or source.get("document")),
        _clean(source.get("excerpt") or source.get("text"))[:1200],
    ])
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:20]


def _passage_to_source(passage: Mapping[str, Any], evidence_id: str) -> Dict[str, Any]:
    text = _clean(
        passage.get("text")
        or passage.get("source_text")
        or passage.get("excerpt")
        or passage.get("analysis_text")
    )
    return {
        "evidence_id": evidence_id,
        "rag_chunk_id": _clean(passage.get("rag_chunk_id")),
        "passage_id": _clean(passage.get("passage_id") or passage.get("id")),
        "document_id": _clean(passage.get("document_id") or passage.get("doc_id")),
        "document": _clean(passage.get("document") or passage.get("file_name")),
        "source_path": _clean(passage.get("source_path") or passage.get("path")),
        "section_title": _clean(passage.get("section_title") or passage.get("title")),
        "page_number": passage.get("page_number") if passage.get("page_number") is not None else passage.get("page"),
        "paragraph_index": passage.get("paragraph_index") if passage.get("paragraph_index") is not None else passage.get("paragraph"),
        "char_start": passage.get("char_start"),
        "char_end": passage.get("char_end"),
        "excerpt": _truncate(text, 1200),
        "text": text,
        "role": "verrou",
    }


def _cluster_sources(cluster: Mapping[str, Any]) -> List[Dict[str, Any]]:
    passages = cluster.get("supporting_passages") or []
    sources: List[Dict[str, Any]] = []
    if isinstance(passages, list):
        for index, passage in enumerate(passages, start=1):
            if isinstance(passage, dict):
                sources.append(_passage_to_source(passage, f"E{index}"))
            elif _clean(passage):
                sources.append(_passage_to_source({"text": _clean(passage)}, f"E{index}"))

    if not sources:
        members = cluster.get("member_groups") or []
        if isinstance(members, list):
            for member in members:
                if not isinstance(member, dict):
                    continue
                nested = member.get("supporting_passages") or []
                if isinstance(nested, list) and nested:
                    for passage in nested:
                        if isinstance(passage, dict):
                            sources.append(_passage_to_source(passage, f"E{len(sources) + 1}"))
                elif _clean(member.get("text")):
                    sources.append(_passage_to_source(member, f"E{len(sources) + 1}"))

    deduped = _dedupe_dicts(sources)
    for index, source in enumerate(deduped, start=1):
        source["evidence_id"] = f"E{index}"
    return deduped


def _load_json_file(path: str | Path | None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if path in (None, ""):
        return None, None
    file_path = Path(path)
    if not file_path.exists():
        return None, f"fichier absent: {file_path}"
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None, "contenu JSON non objet"
        return value, None
    except Exception as exc:
        return None, str(exc)


def _metadata(source: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = source.get("metadata")
    return meta if isinstance(meta, dict) else source


def _source_text(source: Mapping[str, Any]) -> str:
    meta = _metadata(source)
    return _clean(
        source.get("text")
        or source.get("source_text")
        or source.get("excerpt")
        or meta.get("text")
        or meta.get("excerpt")
    )


def _singleton_clusters_from_sections(sections: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback de couverture : un groupe RAG absent devient un singleton, jamais une fusion."""
    raw_sources: List[Dict[str, Any]] = []
    for key, value in (sections or {}).items():
        if not isinstance(value, list):
            continue
        normalized_key = _norm(key)
        for source in value:
            if not isinstance(source, dict):
                continue
            meta = _metadata(source)
            role = _norm(meta.get("role") or source.get("role") or normalized_key)
            if role == "verrou" or "verrou" in normalized_key:
                raw_sources.append(source)

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for source in raw_sources:
        meta = _metadata(source)
        group_id = _clean(
            meta.get("lock_group_id")
            or source.get("lock_group_id")
            or meta.get("passage_id")
            or source.get("id")
        )
        if not group_id:
            group_id = "G_" + hashlib.sha1(
                f"{meta.get('document')}|{_source_text(source)}".encode("utf-8", errors="ignore")
            ).hexdigest()[:12]
        groups.setdefault(group_id, []).append(source)

    clusters: List[Dict[str, Any]] = []
    for index, (group_id, sources) in enumerate(groups.items(), start=1):
        passages = []
        labels = []
        scores = []
        for source in sources:
            meta = _metadata(source)
            labels.append(meta.get("candidate_group_label") or meta.get("section_title"))
            scores.append(_safe_float(meta.get("frascati_score") or meta.get("verrou_score")))
            passages.append({
                "passage_id": _clean(meta.get("passage_id") or source.get("id")),
                "document": _clean(meta.get("document")),
                "source_path": _clean(meta.get("source_path")),
                "section_title": _clean(meta.get("section_title")),
                "text": _source_text(source),
            })
        clusters.append({
            "cluster_id": f"VC{index:03d}",
            "member_group_ids": [group_id],
            "support_group_ids": [],
            "display_as_lock": True,
            "display_as_main_lock": True,
            "lock_scope": "project_structuring_lock",
            "cluster_role": "verrou_a_verifier",
            "cluster_role_confidence": 0.5,
            "cluster_role_reasons": ["fallback singleton sans reclassification"],
            "related_cluster_ids": [],
            "group_count": 1,
            "representative_label": next((str(value) for value in labels if _clean(value)), group_id),
            "representative_text": _source_text(sources[0]) if sources else "",
            "semantic_text": "\n".join(_source_text(source) for source in sources),
            "concept_profile": {},
            "frascati_score": round(sum(score for score in scores if score > 0) / max(1, sum(1 for score in scores if score > 0)), 4) if any(score > 0 for score in scores) else 0.0,
            "frascati_decisions": ["verrou_a_verifier"],
            "supporting_documents": [],
            "supporting_passages": passages,
            "member_groups": [],
            "needs_human_validation": True,
            "not_final_cir": True,
        })

    return {
        "ok": True,
        "version": "agent_singleton_cluster_fallback_v171",
        "mode": "singleton_from_sections_no_merge",
        "groups_count": len(groups),
        "display_clusters_count": len(clusters),
        "support_only_clusters_count": 0,
        "clusters": clusters,
        "coverage": {
            "input_group_ids": list(groups.keys()),
            "covered_group_ids": list(groups.keys()),
            "uncovered_group_ids": [],
        },
    }


def _nlp_group_report_from_sections(
    sections: Dict[str, Any],
) -> Dict[str, Any]:
    """Projette chaque ``lock_group_id`` NLP en une fiche, sans regroupement.

    Plusieurs chunks peuvent representer le meme groupe (chunk principal et
    passages de preuve). Ils sont reunis uniquement par leur identifiant NLP
    deja existant. Deux identifiants NLP distincts ne sont jamais fusionnes.
    """

    preferred = sections.get("_nlp_verrou_candidates")
    if not isinstance(preferred, list) or not preferred:
        preferred = sections.get("verrous")
    raw_sources = [
        source
        for source in (preferred or [])
        if isinstance(source, dict)
    ]
    # Certains documents expriment un verrou dans le corps d'un paragraphe
    # (contrainte forte, exigence non négociable) sans que le premier passage ait
    # reçu le rôle ``verrou``. On récupère ces déclarations dans toutes les
    # sections courantes, sans vocabulaire propre à un projet.
    seen_sources = {
        (
            _clean((_metadata(source).get("passage_id") or source.get("id"))),
            _norm(_source_text(source))[:360],
        )
        for source in raw_sources
    }
    for section_values in sections.values():
        if not isinstance(section_values, list):
            continue
        for source in section_values:
            if not isinstance(source, dict):
                continue
            meta = _metadata(source)
            role = _norm(
                meta.get("role") or meta.get("semantic_role")
                or source.get("role") or source.get("semantic_role")
            )
            section_title = _norm(meta.get("section_title") or source.get("section_title"))
            text_value = _source_text(source)
            declared = role in {
                "verrou", "verrou scientifique", "verrou a verifier", "limite",
                "incertitude", "constraint", "lock",
            } or bool(_DECLARED_LOCK_SIGNAL_RE.search(_norm(text_value)))
            if not declared or _EXTERNAL_SECTION_RE.search(section_title):
                continue
            signature = (
                _clean(meta.get("passage_id") or source.get("id")),
                _norm(text_value)[:360],
            )
            if signature in seen_sources:
                continue
            seen_sources.add(signature)
            raw_sources.append(source)

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for source in raw_sources:
        meta = _metadata(source)
        role = _norm(
            meta.get("role") or meta.get("semantic_role")
            or source.get("role") or source.get("semantic_role")
        )
        explicitly_declared = bool(
            _DECLARED_LOCK_SIGNAL_RE.search(_norm(_source_text(source)))
        )
        if role not in {
            "verrou", "verrou scientifique", "verrou a verifier", "limite",
            "incertitude", "constraint", "lock",
        } and not explicitly_declared:
            continue
        if meta.get("display_as_main_lock") is False:
            continue
        scope = _norm(meta.get("technical_scope") or meta.get("lock_scope"))
        if scope in {"local_technical_subproblem", "secondary", "supporting_measurement"}:
            continue

        group_id = _clean(
            meta.get("lock_group_id")
            or source.get("lock_group_id")
            or meta.get("passage_id")
            or source.get("id")
        )
        if not group_id:
            group_id = "nlp_group_" + hashlib.sha1(
                f"{meta.get('document')}|{_source_text(source)}".encode(
                    "utf-8",
                    errors="ignore",
                )
            ).hexdigest()[:16]
        groups.setdefault(group_id, []).append(source)

    projected: List[Dict[str, Any]] = []
    for group_id, sources in groups.items():
        passages: List[Dict[str, Any]] = []
        labels: List[str] = []
        semantic_parts: List[str] = []
        scores: List[float] = []
        recommendations: List[int] = []

        for source in sources:
            meta = _metadata(source)
            labels.append(_clean(meta.get("candidate_group_label") or meta.get("section_title")))
            semantic_parts.append(_source_text(source))

            score = _safe_float(meta.get("frascati_score"), 0.0)
            assessment = meta.get("frascati_assessment") or {}
            if score <= 0 and isinstance(assessment, Mapping):
                score = _safe_float(
                    assessment.get("eligibility_score")
                    or assessment.get("documentary_coverage"),
                    0.0,
                )
            if score > 0:
                scores.append(score)

            recommendation = meta.get("frascati_recommendation")
            if recommendation is None:
                recommendation = meta.get("frascati_decision")
            try:
                recommendations.append(int(recommendation))
            except Exception:
                pass

            nested = meta.get("supporting_passages") or source.get("supporting_passages") or []
            if isinstance(nested, list):
                for passage in nested:
                    if isinstance(passage, Mapping):
                        passages.append(dict(passage))

        if not passages:
            for source in sources:
                meta = _metadata(source)
                passages.append({
                    "passage_id": _clean(meta.get("passage_id") or source.get("id")),
                    "document": _clean(meta.get("document") or source.get("document")),
                    "source_path": _clean(meta.get("source_path") or source.get("source_path")),
                    "section_title": _clean(meta.get("section_title")),
                    "role": role,
                    "semantic_role": _clean(meta.get("semantic_role") or source.get("semantic_role")),
                    "content_origin": _clean(meta.get("content_origin") or source.get("content_origin")),
                    "source_type": _clean(meta.get("source_type") or source.get("source_type")),
                    "declared_corpus": _clean(meta.get("declared_corpus") or source.get("declared_corpus")),
                    "diagnostic_corpus_selected": bool(meta.get("diagnostic_corpus_selected") or source.get("diagnostic_corpus_selected")),
                    "text": _source_text(source),
                })

        deduped_passages: List[Dict[str, Any]] = []
        seen_passages: Set[Tuple[str, str]] = set()
        for passage in passages:
            key = (
                _clean(passage.get("passage_id") or passage.get("id")),
                _norm(passage.get("text") or passage.get("excerpt"))[:360],
            )
            if key in seen_passages:
                continue
            seen_passages.add(key)
            deduped_passages.append(passage)

        representative_text = next(
            (_source_text(source) for source in sources if _source_text(source)),
            "",
        )
        projected.append({
            "cluster_id": group_id,
            "member_group_ids": [group_id],
            "support_group_ids": [],
            "display_as_lock": True,
            "display_as_main_lock": True,
            "lock_scope": "project_structuring_lock",
            "cluster_role": "verrou_a_verifier",
            "cluster_role_confidence": 1.0,
            "cluster_role_reasons": ["groupe principal finalise par le NLP avant Frascati"],
            "related_cluster_ids": [],
            "group_count": 1,
            "representative_label": next((value for value in labels if value), group_id),
            "representative_text": representative_text,
            "semantic_text": "\n".join(_dedupe_texts(semantic_parts))[:5000],
            "concept_profile": {},
            "frascati_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
            "frascati_recommendations": recommendations,
            "frascati_decisions": ["verrou_a_verifier"],
            "supporting_documents": [],
            "supporting_passages": deduped_passages,
            "member_groups": [],
            "needs_human_validation": True,
            "not_final_cir": True,
            "grouping_owner": "nlp_before_frascati",
            "downstream_regrouping_applied": False,
        })

    group_ids = list(groups.keys())
    return {
        "ok": True,
        "version": "nlp_group_passthrough_v189",
        "mode": "nlp_group_passthrough_no_regrouping",
        "groups_count": len(projected),
        "display_clusters_count": len(projected),
        "support_only_clusters_count": 0,
        "clusters": projected,
        "coverage": {
            "input_group_ids": group_ids,
            "covered_group_ids": group_ids,
            "uncovered_group_ids": [],
        },
        "downstream_regrouping_applied": False,
    }


def _resolve_cluster_report(
    sections: Dict[str, Any],
    lock_clusters: Optional[Dict[str, Any]],
    lock_clusters_path: str | Path | None,
) -> Tuple[Dict[str, Any], str, Optional[str]]:
    # Les deux arguments historiques sont volontairement ignores. Un ancien
    # ``lock_clusters.json`` peut encore exister sur disque, mais il ne doit
    # plus modifier la composition produite par le NLP.
    report = _nlp_group_report_from_sections(sections)
    legacy_ignored = bool(lock_clusters is not None or lock_clusters_path not in (None, ""))
    report["legacy_cluster_input_ignored"] = legacy_ignored
    return report, "nlp_groups_from_sections", None



def _memory_examples(previous_cir_context: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(previous_cir_context, Mapping):
        return []
    values = previous_cir_context.get("examples") or []
    return [dict(value) for value in values if isinstance(value, Mapping)]


def _semantic_terms(value: Any) -> Set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _norm(value))
        if len(token) >= 4 and token not in TITLE_STOPWORDS and not token.isdigit()
    }


def _rank_previous_examples(
    cluster: Mapping[str, Any],
    previous_cir_context: Optional[Mapping[str, Any]],
    max_items: int = 3,
) -> List[Dict[str, Any]]:
    """Sélectionne les exemples antérieurs les plus proches du cluster courant.

    Le classement sert uniquement à réduire le contexte envoyé au LLM. Les exemples
    ne sont jamais ajoutés au corpus de grounding et ne peuvent donc pas valider un
    fait, un chiffre ou un objet absent des preuves courantes.
    """
    examples = _memory_examples(previous_cir_context)
    if not examples:
        return []

    current_text = " ".join([
        _clean(cluster.get("representative_label")),
        _clean(cluster.get("representative_text")),
        _clean(cluster.get("semantic_text")),
        _clean(cluster.get("measurement_support_text")),
        " ".join(
            _clean(source.get("excerpt") or source.get("text"))
            for source in _cluster_sources(cluster)
        ),
    ])
    current_terms = _semantic_terms(current_text)

    ranked: List[Tuple[float, Dict[str, Any]]] = []
    for raw in examples:
        example = dict(raw)
        example_text = f"{_clean(example.get('title'))} {_clean(example.get('text'))}"
        example_terms = _semantic_terms(example_text)
        overlap = current_terms & example_terms
        union = current_terms | example_terms
        jaccard = len(overlap) / max(1, len(union))
        coverage = len(overlap) / max(1, min(len(current_terms), 24))
        score = (0.65 * coverage) + (0.35 * jaccard)
        if _norm(example.get("role")) == "verrou":
            score += 0.05
        example["similarity_score_for_prompt"] = round(score, 4)
        ranked.append((score, example))

    ranked.sort(key=lambda pair: (pair[0], len(_clean(pair[1].get("text")))), reverse=True)
    selected = [example for score, example in ranked if score > 0][:max_items]
    if not selected:
        selected = [example for _, example in ranked[: min(2, max_items)]]

    output: List[Dict[str, Any]] = []
    for example in selected:
        output.append({
            "example_id": _clean(example.get("example_id")),
            "year": _clean(example.get("year")),
            "title": _truncate(example.get("title"), 220),
            "text": _truncate(example.get("text"), 650),
            "similarity_score_for_prompt": example.get("similarity_score_for_prompt"),
            "usage": "style_and_conceptual_framing_only",
            "is_current_evidence": False,
        })
    return output


def _previous_context_summary(previous_cir_context: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(previous_cir_context, Mapping):
        return {"available": False, "previous_years": [], "examples_count": 0}
    examples = _memory_examples(previous_cir_context)
    return {
        "available": bool(examples),
        "previous_years": list(previous_cir_context.get("previous_years") or []),
        "examples_count": len(examples),
        "usage": "style_and_conceptual_framing_only",
        "factual_use_allowed": False,
    }


def _compact_cluster(
    cluster: Mapping[str, Any],
    previous_cir_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    sources = _cluster_sources(cluster)
    evidence = [
        {
            "evidence_id": source.get("evidence_id"),
            "document": source.get("document"),
            "section_title": source.get("section_title"),
            "text": _truncate(source.get("excerpt"), 520),
        }
        # La fiche finale conserve toutes les sources. Le prompt de
        # reformulation n'envoie qu'un échantillon représentatif afin de ne pas
        # multiplier les lots/appels LLM pour répéter des passages proches.
        for source in sources[:8]
    ]
    return {
        "cluster_id": _clean(cluster.get("cluster_id")),
        "member_group_ids": list(cluster.get("member_group_ids") or []),
        "support_group_ids": list(cluster.get("support_group_ids") or []),
        "lock_scope": _clean(cluster.get("lock_scope") or "project_structuring_lock"),
        "display_as_main_lock": True,
        "cluster_role": _clean(cluster.get("cluster_role") or "verrou_a_verifier"),
        "cluster_role_reasons": list(cluster.get("cluster_role_reasons") or []),
        "related_cluster_ids": list(cluster.get("related_cluster_ids") or []),
        "representative_label": _truncate(cluster.get("representative_label"), 260),
        "representative_text": _truncate(cluster.get("representative_text"), 760),
        "semantic_text": _truncate(cluster.get("semantic_text"), 1600),
        "measurement_support_text": _truncate(cluster.get("measurement_support_text"), 650),
        "concept_profile": cluster.get("concept_profile") or {},
        "frascati_score": cluster.get("frascati_score"),
        "evidence": evidence,
        "previous_cir_style_examples": _rank_previous_examples(
            cluster,
            previous_cir_context,
            max_items=int(os.getenv("ENNOSMART_DIAG_VERROU_PREVIOUS_EXAMPLES_PER_CLUSTER", "3")),
        ),
    }

def _build_prompt(
    clusters: Sequence[Mapping[str, Any]],
    style_block: str = "",
    previous_cir_context: Optional[Mapping[str, Any]] = None,
) -> str:
    compact = [_compact_cluster(cluster, previous_cir_context) for cluster in clusters]
    style = _truncate(style_block, 1400)
    previous_summary = _previous_context_summary(previous_cir_context)
    return f"""
Tu es EnnoDiagnostic, consultant CIR senior.

Les regroupements ont déjà été calculés une seule fois par le NLP, avant
l'évaluation Frascati. Chaque cluster reçu correspond exactement à un groupe NLP.
Tu ne dois ni fusionner, ni séparer, ni supprimer, ni reclasser ces groupes.
Ton unique tâche est de REFORMULER chaque groupe comme un verrou candidat CIR.

SÉPARATION ABSOLUE DES SOURCES
- Le tableau "evidence" de chaque cluster contient les seules preuves factuelles
  autorisées pour le projet courant.
- "previous_cir_style_examples" contient des formulations issues d'années
  antérieures. Elles servent uniquement à comprendre le niveau de rédaction,
  le vocabulaire CIR et la manière de remonter d'un passage brut vers une
  incertitude scientifique ou technique.
- Ne jamais transférer depuis un exemple antérieur un objet, une méthode, une
  valeur, un résultat, une cause ou une conclusion absent des preuves courantes.
- La validation factuelle est réalisée uniquement contre "evidence".

RÈGLES DE REFORMULATION
- Retourner exactement une entrée par cluster_id fourni.
- Conserver exactement le cluster_id reçu.
- Ne créer aucun nouveau cluster_id.
- Ne jamais utiliser les preuves d’un autre cluster.
- Rédiger comme un consultant CIR expérimenté : le verrou doit être clair,
  précis et compréhensible sans avoir participé au projet.
- Le titre doit être une formulation CIR courte, autonome et professionnelle.
  Il doit contenir : 1) l'objet technique concret, 2) le comportement ou
  phénomène non maîtrisé, 3) la contrainte discriminante seulement si utile.
- Le titre doit exprimer explicitement une incertitude résiduelle, une
  impossibilité à garantir, une instabilité, une non-maîtrise, une robustesse
  insuffisante ou un comportement non prédictible.
- Le titre ne doit jamais être une liste de mots-clés, une date, une légende,
  une section, un nom de fichier, une tâche d'essai ou une simple description.
- Viser 8 à 18 mots, avec un maximum de 22 mots.
- Reprendre des termes techniques réellement présents dans les preuves courantes.
- Distinguer un résultat obtenu de l'incertitude restant à lever.
- Ne jamais mentionner RAG, NLP, Frascati, LLM, parser, fallback ou erreur dans
  les champs visibles.
- Le résultat reste un signal candidat soumis à validation humaine.

QUALITÉ DES CHAMPS
- scientific_uncertainty : dire précisément ce qui reste inconnu ou non maîtrisé.
- causal_chain : relier objectif, paramètres/phénomènes, incertitude et impact.
- why_lock : expliquer pourquoi les preuves révèlent une difficulté non résolue.
- why_not_simple_engineering : rester prudent et indiquer ce qui doit être
  confirmé par l'état de l'art ou des essais.
- evidence_summary : synthèse factuelle des preuves du cluster courant.
- consultant_check : contrôles concrets à effectuer avant validation.

JSON STRICT
{{
  "clusters": [
    {{
      "cluster_id": "VC001",
      "title": "titre de verrou CIR clair",
      "scientific_uncertainty": "inconnue scientifique ou technique résiduelle",
      "causal_chain": "objectif -> paramètres/phénomènes -> incertitude -> impact",
      "why_lock": "raisonnement fondé sur les preuves courantes",
      "why_not_simple_engineering": "raison prudente à confirmer",
      "evidence_summary": "synthèse factuelle des preuves courantes",
      "consultant_check": "points à vérifier par le consultant"
    }}
  ]
}}

Style global facultatif, sans valeur de preuve :
{style or "Aucun style externe."}

Contexte CIR antérieur disponible :
{json.dumps(previous_summary, ensure_ascii=False, indent=2)}

Clusters courants :
{json.dumps(compact, ensure_ascii=False, indent=2)}

Répondre uniquement avec le JSON.
""".strip()

def _build_retry_prompt(
    clusters: Sequence[Mapping[str, Any]],
    errors: Mapping[str, Sequence[str]],
    drafts: Optional[Mapping[str, Mapping[str, Any]]] = None,
    previous_cir_context: Optional[Mapping[str, Any]] = None,
) -> str:
    compact = [_compact_cluster(cluster, previous_cir_context) for cluster in clusters]
    draft_payload = {
        cluster_id: {
            field: _safe_visible(value.get(field))
            for field in VISIBLE_FIELDS
        }
        for cluster_id, value in (drafts or {}).items()
        if isinstance(value, Mapping)
    }
    return f"""
Tu corriges des fiches de verrous CIR. Retourne un JSON strict avec la clé
"clusters" et une entrée exacte par cluster_id.

IMPORTANT
- Les brouillons contiennent parfois des champs déjà corrects : conserve-les.
- Corrige seulement les champs signalés, puis retourne néanmoins la fiche complète.
- Les faits autorisés viennent exclusivement de "evidence" dans les clusters courants.
- Les exemples CIR antérieurs sont uniquement des modèles de style et de niveau
  d'abstraction. Ne copie aucun de leurs faits dans le projet courant.
- Ne remplace jamais un titre par une liste de mots-clés ou par concept_profile.top_terms.

Le titre doit être clair, autonome, professionnel et formulé comme un verrou CIR :
objet technique + comportement non maîtrisé + contrainte utile prouvée.
Il doit exprimer une incertitude, une non-maîtrise, une impossibilité à garantir,
une instabilité, une robustesse insuffisante ou un comportement non prédictible.

Chaque entrée doit contenir des chaînes non vides pour :
{json.dumps(list(VISIBLE_FIELDS), ensure_ascii=False)}

Brouillons à préserver autant que possible :
{json.dumps(draft_payload, ensure_ascii=False, indent=2)}

Erreurs détectées :
{json.dumps(errors, ensure_ascii=False, indent=2)}

Clusters courants et exemples de style antérieurs :
{json.dumps(compact, ensure_ascii=False, indent=2)}

Répondre uniquement avec :
{{"clusters": [{{"cluster_id": "VC001", "title": "...", "scientific_uncertainty": "...", "causal_chain": "...", "why_lock": "...", "why_not_simple_engineering": "...", "evidence_summary": "...", "consultant_check": "..."}}]}}
""".strip()


def _build_final_repair_prompt(
    clusters: Sequence[Mapping[str, Any]],
    errors: Mapping[str, Sequence[str]],
    drafts: Mapping[str, Mapping[str, Any]],
    previous_cir_context: Optional[Mapping[str, Any]] = None,
) -> str:
    compact = [_compact_cluster(cluster, previous_cir_context) for cluster in clusters]
    return f"""
Dernière correction de fiches de verrous CIR.

Pour chaque cluster :
1. conserve tous les champs corrects du brouillon ;
2. corrige uniquement les erreurs listées ;
3. vérifie que le titre est une phrase française claire de consultant CIR et
   non une liste de tokens ;
4. n'utilise que les preuves courantes pour les faits ;
5. utilise le CIR antérieur uniquement pour le style de formulation.

Brouillons :
{json.dumps(drafts, ensure_ascii=False, indent=2)}

Erreurs restantes :
{json.dumps(errors, ensure_ascii=False, indent=2)}

Clusters :
{json.dumps(compact, ensure_ascii=False, indent=2)}

Retourne uniquement un JSON strict sous la forme {{"clusters": [...]}} avec tous
les champs {json.dumps(list(VISIBLE_FIELDS), ensure_ascii=False)}.
""".strip()


def _build_title_repair_prompt(
    cluster: Mapping[str, Any],
    draft: Mapping[str, Any],
    errors: Sequence[str],
    previous_cir_context: Optional[Mapping[str, Any]] = None,
    previous_title: str = "",
) -> str:
    """Construit une relance extrêmement ciblée qui ne réécrit que le titre.

    Cette relance est utilisée uniquement lorsque tous les champs techniques de
    la fiche sont valides mais que le titre reste descriptif, trop générique ou
    insuffisamment ancré. Elle évite de jeter le raisonnement déjà produit par
    le LLM et évite aussi le placeholder visible dans l'interface.
    """
    compact = _compact_cluster(cluster, previous_cir_context)
    stable_context = {
        "cluster_id": _clean(cluster.get("cluster_id")),
        "scientific_uncertainty": _safe_visible(draft.get("scientific_uncertainty")),
        "causal_chain": _safe_visible(draft.get("causal_chain")),
        "why_lock": _safe_visible(draft.get("why_lock")),
        "evidence_summary": _safe_visible(draft.get("evidence_summary")),
    }
    return f"""
Tu es consultant CIR senior. Tu dois réparer UNIQUEMENT le titre d'un verrou.

CONTRAINTE ABSOLUE
- Ne modifie aucun autre champ.
- N'utilise comme faits que les preuves courantes du cluster.
- Les exemples CIR antérieurs servent seulement au style.
- Ne crée aucun phénomène, méthode, valeur ou résultat absent des preuves.
- Ne retourne jamais un placeholder, une liste de mots-clés, un nom de section,
  une tâche, une question ou une simple description du sujet.

FORME OBLIGATOIRE DU TITRE
- Une phrase nominale française claire et autonome.
- 7 à 18 mots, maximum 22 mots.
- Contenir l'objet technique concret présent dans les preuves.
- Exprimer explicitement une incertitude résiduelle ou une non-maîtrise.
- Utiliser naturellement une forme comme :
  * « Incertitude sur ... »
  * « Maîtrise incertaine de ... »
  * « Représentativité non garantie de ... »
  * « Validité non garantie de ... »
  * « Robustesse insuffisamment démontrée de ... »
  Ces formes sont des modèles syntaxiques, pas des contenus à copier.

CHAMPS TECHNIQUES DÉJÀ VALIDÉS
{json.dumps(stable_context, ensure_ascii=False, indent=2)}

ANCIEN TITRE REFUSÉ
{json.dumps(previous_title or _safe_visible(draft.get("title")), ensure_ascii=False)}

ERREURS À CORRIGER
{json.dumps(list(errors or []), ensure_ascii=False, indent=2)}

CLUSTER COURANT ET PREUVES
{json.dumps(compact, ensure_ascii=False, indent=2)}

Répondre uniquement avec ce JSON strict :
{{"cluster_id": "{_clean(cluster.get('cluster_id'))}", "title": "titre CIR corrigé"}}
""".strip()


def _extract_title_repair(raw: Any, cluster_id: str) -> str:
    parsed = _extract_json(raw)
    if isinstance(parsed, Mapping):
        if isinstance(parsed.get("clusters"), list) and parsed.get("clusters"):
            parsed = parsed["clusters"][0]
        if isinstance(parsed, Mapping):
            returned_id = _clean(parsed.get("cluster_id"))
            if returned_id and returned_id != cluster_id:
                raise ValueError(
                    f"cluster_id attendu={cluster_id}, reçu={returned_id}"
                )
            return _safe_visible(parsed.get("title"))
    raise ValueError("titre absent de la réponse de réparation ciblée")


def _generation_text(raw: Any) -> Any:
    if raw is None or isinstance(raw, (str, dict, list)):
        return raw
    for key in ("text", "content", "response", "output", "generated_text"):
        value = getattr(raw, key, None)
        if value:
            return value
    return raw


def _generate(llm: Any, prompt: str, *, request_name: str, max_tokens: int) -> Any:
    if llm is None:
        return None
    raw = llm.generate(
        prompt,
        temperature=0.02,
        max_output_tokens=max_tokens,
        max_input_tokens=int(os.getenv("ENNOSMART_DIAG_VERROU_MAX_INPUT_TOKENS", "5200")),
        retries=int(os.getenv("ENNOSMART_DIAG_VERROU_LLM_RETRIES", "1")),
        json_mode=True,
        request_name=request_name,
    )
    return _generation_text(raw)


def _extract_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    text = _clean(raw)
    if not text:
        raise ValueError("réponse vide")
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    starts = [position for position in (text.find("{"), text.find("[")) if position >= 0]
    if not starts:
        raise ValueError("aucun JSON détecté")
    start = min(starts)
    for end in range(len(text), start, -1):
        candidate = text[start:end].strip()
        if not candidate or candidate[-1] not in "}]":
            continue
        try:
            return json.loads(candidate)
        except Exception:
            continue
    raise ValueError("JSON invalide")



def _cluster_grounding_text(cluster: Mapping[str, Any]) -> str:
    parts = [
        cluster.get("representative_label"),
        cluster.get("representative_text"),
        cluster.get("semantic_text"),
        cluster.get("measurement_support_text"),
    ]
    for source in _cluster_sources(cluster):
        parts.append(source.get("excerpt") or source.get("text"))
        parts.append(source.get("document"))
    return _norm(" ".join(_clean(part) for part in parts if _clean(part)))


def _numeric_tokens(value: Any) -> Set[str]:
    """Normalise les valeurs numériques indépendamment de l'espacement des unités.

    Ainsi, ``107db``, ``107 dB`` et ``107 dB(A)`` produisent tous la valeur
    canonique ``107``. Le contrôle reste strict sur les nombres réellement absents,
    sans rejeter une reformulation uniquement à cause de la typographie de l'unité.
    """
    text = unicodedata.normalize("NFKD", _clean(value).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    values: Set[str] = set()
    for raw in re.findall(r"(?<![a-z0-9])\d+(?:[.,]\d+)?", text):
        normalized = raw.replace(",", ".")
        try:
            number = float(normalized)
            normalized = str(int(number)) if number.is_integer() else (
                f"{number:.8f}".rstrip("0").rstrip(".")
            )
        except Exception:
            pass
        values.add(normalized)
    return values


def _grounding_errors(value: Mapping[str, Any], cluster: Mapping[str, Any]) -> List[str]:
    source_text = _cluster_grounding_text(cluster)
    source_numbers = _numeric_tokens(source_text)
    errors: List[str] = []
    for field in VISIBLE_FIELDS:
        text = _safe_visible(value.get(field))
        unknown = sorted(token for token in _numeric_tokens(text) if token not in source_numbers)
        if unknown:
            errors.append(f"champ {field}: valeurs absentes des preuves {unknown[:8]}")
    return errors


def _title_content_tokens(value: Any) -> Set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _norm(value))
        if len(token) >= 4 and token not in TITLE_STOPWORDS and not token.isdigit()
    }


def _title_quality_errors(title: Any, cluster: Mapping[str, Any]) -> List[str]:
    """Valide un vrai titre de verrou CIR, clair, autonome et ancré."""
    raw_title = _clean(title)
    normalized = _norm(raw_title).replace("'", " ")
    errors: List[str] = []

    if any(re.search(pattern, normalized) for pattern in GENERIC_TITLE_PATTERNS):
        errors.append(
            "titre trop générique : formuler l'incertitude technologique centrale"
        )
    if any(re.search(pattern, normalized) for pattern in TASK_TITLE_PATTERNS):
        errors.append(
            "titre formulé comme une tâche : exprimer le comportement non maîtrisé"
        )
    if any(re.search(pattern, normalized) for pattern in RAW_LABEL_PATTERNS):
        errors.append(
            "titre ressemblant à une date, une légende ou un libellé brut de document"
        )

    words = normalized.split()
    if not words or words[-1] in INCOMPLETE_TITLE_ENDINGS or raw_title.rstrip().endswith((":", "-", "—", "–")):
        errors.append("titre incomplet ou phrase tronquée")
    if len(words) < CONSULTANT_TITLE_MIN_WORDS:
        errors.append("titre trop court pour être compris sans le contexte du dossier")
    if len(words) > CONSULTANT_TITLE_MAX_WORDS:
        errors.append("titre trop long : viser une formulation CIR concise")

    if not any(marker in normalized for marker in CONSULTANT_UNCERTAINTY_MARKERS):
        errors.append(
            "titre seulement descriptif : expliciter l'incertitude, la non-maîtrise, "
            "l'instabilité ou l'impossibilité à garantir"
        )

    title_tokens = _title_content_tokens(raw_title)
    source_tokens = _title_content_tokens(_cluster_grounding_text(cluster))
    exact = title_tokens & source_tokens
    stem_matches = {
        title_token
        for title_token in title_tokens - exact
        if any(
            len(title_token) >= 5
            and len(source_token) >= 5
            and title_token[:5] == source_token[:5]
            for source_token in source_tokens
        )
    }
    anchor_score = len(exact) + (0.65 * len(stem_matches))
    if anchor_score < 1.5:
        errors.append(
            "titre insuffisamment ancré : reprendre des objets ou phénomènes techniques présents dans les preuves"
        )
    return errors


def _validate_analysis(
    value: Any,
    cluster_id: str,
    cluster: Optional[Mapping[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Normalise toujours les champs récupérables et retourne les erreurs à part.

    Contrairement aux versions précédentes, une erreur sur ``causal_chain`` ne
    détruit plus un bon titre LLM. Le brouillon partiel peut être fusionné avec
    la relance ciblée puis complété prudemment.
    """
    if not isinstance(value, Mapping):
        return None, ["entrée absente ou non objet"]

    errors: List[str] = []
    returned_id = _clean(value.get("cluster_id"))
    if returned_id and returned_id != cluster_id:
        errors.append(f"cluster_id attendu={cluster_id}, reçu={returned_id}")

    normalized: Dict[str, Any] = {"cluster_id": cluster_id}
    for field in VISIBLE_FIELDS:
        text = _safe_visible(value.get(field))
        if len(text) < 12:
            errors.append(f"champ {field} absent, trop court ou interne")
        normalized[field] = text

    if cluster is not None:
        errors.extend(_grounding_errors(normalized, cluster))
        errors.extend(_title_quality_errors(normalized.get("title"), cluster))
    return normalized, _dedupe_texts(errors)


def _merge_analysis(
    base: Optional[Mapping[str, Any]],
    update: Optional[Mapping[str, Any]],
    cluster_id: str,
) -> Dict[str, Any]:
    merged: Dict[str, Any] = {"cluster_id": cluster_id}
    for field in VISIBLE_FIELDS:
        newer = _safe_visible((update or {}).get(field))
        older = _safe_visible((base or {}).get(field))
        merged[field] = newer or older
    return merged


def _error_fields(errors: Sequence[str]) -> Set[str]:
    fields: Set[str] = set()
    for error in errors or []:
        normalized = _norm(error)
        for field in VISIBLE_FIELDS:
            if normalized.startswith(_norm(f"champ {field}")):
                fields.add(field)
        if "titre" in normalized:
            fields.add("title")
        if "cluster_id" in normalized:
            fields.add("cluster_id")
    return fields


def _title_is_valid(title: Any, cluster: Mapping[str, Any]) -> bool:
    if not _safe_visible(title):
        return False
    candidate = {field: "texte valide sans valeur" for field in VISIBLE_FIELDS}
    candidate["title"] = _safe_visible(title)
    title_errors = _title_quality_errors(candidate["title"], cluster)
    numeric_errors = [
        error for error in _grounding_errors(candidate, cluster)
        if _norm(error).startswith("champ title")
    ]
    return not title_errors and not numeric_errors


def _title_from_uncertainty(uncertainty: Any, cluster: Mapping[str, Any]) -> str:
    """Transforme dynamiquement une incertitude validée en titre de secours.

    Cette fonction n'intègre aucun vocabulaire propre à un projet. Elle réduit
    simplement une phrase longue de type « X reste incertain » en une phrase
    nominale CIR, puis la soumet au même validateur que les titres LLM.
    """
    text = _safe_visible(uncertainty)
    if not text:
        return ""

    # Retirer les incises explicatives sans supprimer l'objet technique central.
    compact = re.sub(
        r"(?i),\s*notamment(?!\s+en\s+raison)\s+[^,]{1,120},\s*",
        " ",
        text,
    )
    compact = re.split(
        r"(?i),\s*(?:ce qui|notamment en raison de|en raison de|sans que|tandis que|alors que|de sorte que)\b",
        compact,
        maxsplit=1,
    )[0]
    compact = compact.strip(" .:;–—-")

    transformations = (
        (
            r"(?i)^(?:l['’]?)?ad[eé]quation\s+(.+?)\s+reste\s+(?:incertaine|incertain|[àa] confirmer|non garantie)$",
            r"Adéquation incertaine \1",
        ),
        (
            r"(?i)^(?:la\s+)?repr[eé]sentativit[eé]\s+(.+?)\s+reste\s+(?:incertaine|incertain|[àa] confirmer|non garantie)$",
            r"Représentativité non garantie \1",
        ),
        (
            r"(?i)^(?:la\s+)?validit[eé]\s+(.+?)\s+reste\s+(?:incertaine|incertain|[àa] confirmer|non garantie)$",
            r"Validité non garantie \1",
        ),
        (
            r"(?i)^(?:la\s+)?fiabilit[eé]\s+(.+?)\s+reste\s+(?:incertaine|incertain|[àa] confirmer|non garantie)$",
            r"Fiabilité incertaine \1",
        ),
        (
            r"(?i)^(.+?)\s+reste(?:nt)?\s+(?:incertaine|incertain|non ma[iî]tris[eé]e?|[àa] confirmer|non garantie)$",
            r"Maîtrise incertaine de \1",
        ),
    )

    candidate = compact
    for pattern, replacement in transformations:
        transformed = re.sub(pattern, replacement, compact).strip(" .:;–—-")
        if transformed != compact:
            candidate = transformed
            break

    if candidate == compact:
        candidate = re.sub(
            r"(?i)^(?:l['’]?)?(?:incertitude|difficult[eé])\s+(?:scientifique|technique|technologique)?\s*(?:porte|r[eé]side)?\s*(?:sur|dans)?\s*",
            "",
            candidate,
        ).strip(" .:;–—-")
        if not any(marker in _norm(candidate) for marker in CONSULTANT_UNCERTAINTY_MARKERS):
            candidate = "Maîtrise incertaine de " + candidate[:1].lower() + candidate[1:]

    candidate = re.sub(r"\s+", " ", candidate).strip()
    candidate = _truncate(candidate, 220)
    return candidate if _title_is_valid(candidate, cluster) else ""

def _safe_evidence_summary(cluster: Mapping[str, Any]) -> str:
    excerpts = _dedupe_texts(
        (
            source.get("excerpt") or source.get("text")
            for source in _cluster_sources(cluster)
        ),
        max_items=4,
    )
    return _truncate(" ".join(excerpts), 1100)


def _complete_analysis_preserving_llm(
    draft: Optional[Mapping[str, Any]],
    cluster: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Complète les champs défaillants sans remplacer les bons champs LLM."""
    cluster_id = _clean(cluster.get("cluster_id"))
    result = _merge_analysis(draft, None, cluster_id)
    _, initial_errors = _validate_analysis(result, cluster_id, cluster)
    invalid_fields = _error_fields(initial_errors)
    deterministic_fields: List[str] = []

    if "title" in invalid_fields or not _title_is_valid(result.get("title"), cluster):
        title = _title_from_uncertainty(result.get("scientific_uncertainty"), cluster)
        if not title:
            title = _fallback_title(cluster)
        result["title"] = title
        deterministic_fields.append("title")

    if "scientific_uncertainty" in invalid_fields or len(_safe_visible(result.get("scientific_uncertainty"))) < 12:
        result["scientific_uncertainty"] = (
            "Les preuves courantes montrent que le comportement technique, ses conditions "
            "de validité ou sa robustesse ne sont pas encore suffisamment maîtrisés."
        )
        deterministic_fields.append("scientific_uncertainty")

    if "evidence_summary" in invalid_fields or len(_safe_visible(result.get("evidence_summary"))) < 12:
        result["evidence_summary"] = _safe_evidence_summary(cluster) or (
            "Les passages du cluster décrivent des essais, comparaisons ou limites qui "
            "doivent être relus conjointement pour qualifier l'incertitude centrale."
        )
        deterministic_fields.append("evidence_summary")

    if "causal_chain" in invalid_fields or len(_safe_visible(result.get("causal_chain"))) < 12:
        result["causal_chain"] = (
            "objectif technique -> paramètres et phénomènes observés -> incertitude "
            "résiduelle -> impact sur la validité ou la robustesse à qualifier"
        )
        deterministic_fields.append("causal_chain")

    if "why_lock" in invalid_fields or len(_safe_visible(result.get("why_lock"))) < 12:
        result["why_lock"] = (
            "Les preuves décrivent une dépendance ou une limite technique dont la cause, "
            "la portée ou la généralisation ne sont pas encore établies."
        )
        deterministic_fields.append("why_lock")

    if "why_not_simple_engineering" in invalid_fields or len(_safe_visible(result.get("why_not_simple_engineering"))) < 12:
        result["why_not_simple_engineering"] = (
            "Le caractère non routinier doit être confirmé par l'état de l'art, la "
            "traçabilité des hypothèses et des essais comparatifs."
        )
        deterministic_fields.append("why_not_simple_engineering")

    if "consultant_check" in invalid_fields or len(_safe_visible(result.get("consultant_check"))) < 12:
        result["consultant_check"] = (
            "Vérifier l'objet technique exact, la cause encore ouverte, les conditions "
            "comparables et l'effet sur la performance recherchée."
        )
        deterministic_fields.append("consultant_check")

    normalized, final_errors = _validate_analysis(result, cluster_id, cluster)
    return normalized or result, final_errors, sorted(set(deterministic_fields))


def _usable_title_candidate(value: Any) -> bool:
    text = _safe_visible(value)
    if len(text) < 18:
        return False
    normalized = _norm(text)
    words = normalized.split()
    if not words or words[-1] in INCOMPLETE_TITLE_ENDINGS:
        return False
    if len(words) < CONSULTANT_TITLE_MIN_WORDS or len(words) > CONSULTANT_TITLE_MAX_WORDS:
        return False
    if any(re.search(pattern, normalized) for pattern in TASK_TITLE_PATTERNS):
        return False
    if any(re.search(pattern, normalized) for pattern in GENERIC_TITLE_PATTERNS):
        return False
    if any(re.search(pattern, normalized) for pattern in RAW_LABEL_PATTERNS):
        return False
    if not any(marker in normalized for marker in CONSULTANT_UNCERTAINTY_MARKERS):
        return False
    return True


def _title_from_evidence_sentence(cluster: Mapping[str, Any]) -> str:
    corpus_parts = [
        cluster.get("representative_text"),
        cluster.get("semantic_text"),
        cluster.get("measurement_support_text"),
    ]
    corpus_parts.extend(source.get("excerpt") or source.get("text") for source in _cluster_sources(cluster))
    corpus = " ".join(_clean(part) for part in corpus_parts if _clean(part))
    sentences = [
        _clean(sentence)
        for sentence in re.split(r"(?<=[.!?;])\s+|\n+", corpus)
        if len(_clean(sentence)) >= 25
    ]
    markers = (
        "non realisable", "impossible", "incertitude", "non maitrise", "reste a",
        "impact de", "influence de", "variation", "depend", "ecart", "instabilite",
        "usure", "temperature", "pression", "debit", "equilibrage", "vibration",
    )
    sentences.sort(
        key=lambda sentence: (
            sum(1 for marker in markers if marker in _norm(sentence)),
            min(len(sentence), 260),
        ),
        reverse=True,
    )
    for sentence in sentences:
        candidate = sentence
        candidate = re.sub(
            r"(?i)^.*?\b(?:chercher|cherchions|cherchons|cherche|objectif)\s+(?:a|à)\s+d[eé]terminer\s+l['’]?impact\s+de\s+",
            "Impact non maîtrisé de ",
            candidate,
        )
        candidate = re.sub(
            r"(?i)^.*?\b(?:d[eé]terminer|[eé]valuer|mesurer|analyser)\s+l['’]?impact\s+de\s+",
            "Impact non maîtrisé de ",
            candidate,
        )
        candidate = re.sub(r"(?i)^des?\s+essais?\s+.*?afin\s+de\s+", "Incertitude sur ", candidate)
        candidate = re.sub(r"(?i)^(?:nous\s+)?(?:avons|allons|devons)\s+", "", candidate)
        candidate = candidate.strip(" .,:;–—-")
        if len(candidate) > 220:
            candidate = _truncate(candidate, 220)
        if _usable_title_candidate(candidate):
            return candidate[0].upper() + candidate[1:]
    return ""


def _fallback_title(cluster: Mapping[str, Any]) -> str:
    """Fallback sans concaténation de ``concept_profile.top_terms``.

    Une liste de tokens n'est jamais un verrou CIR. Si aucune phrase technique
    exploitable n'est disponible, le système affiche explicitement qu'une
    reformulation humaine est nécessaire au lieu de fabriquer un faux titre.
    """
    evidence_title = _title_from_evidence_sentence(cluster)
    if evidence_title:
        return evidence_title

    label = _safe_visible(cluster.get("representative_label"))
    if _usable_title_candidate(label) and _title_is_valid(label, cluster):
        return _truncate(label, 220)

    return "Signal technique à reformuler avant validation CIR"

def _fallback_analysis(cluster: Mapping[str, Any]) -> Dict[str, Any]:
    sources = _cluster_sources(cluster)
    excerpts = _dedupe_texts(
        (source.get("excerpt") for source in sources if source.get("excerpt")),
        max_items=6,
    )
    evidence_summary = _truncate(" ".join(excerpts), 1200)
    uncertainty = (
        "Les preuves décrivent un comportement technique dont le mécanisme, les "
        "conditions de validité ou la robustesse restent à confirmer dans le projet."
    )
    why = (
        "Les observations et essais rassemblés montrent une relation entre plusieurs "
        "paramètres et le comportement recherché, sans permettre encore de conclure "
        "sur la cause centrale ni sur la généralisation des résultats."
    )
    return {
        "cluster_id": _clean(cluster.get("cluster_id")),
        "title": _fallback_title(cluster),
        "scientific_uncertainty": uncertainty,
        "causal_chain": "objectif technique -> paramètres et phénomènes observés -> incertitude résiduelle -> impact à confirmer",
        "why_lock": why,
        "why_not_simple_engineering": (
            "La distinction entre optimisation courante et incertitude de R&D doit être "
            "confirmée par l'état de l'art et par les essais complémentaires."
        ),
        "evidence_summary": evidence_summary or (
            "Les preuves disponibles doivent être relues conjointement afin de confirmer "
            "le phénomène central et ses conditions de validité."
        ),
        "consultant_check": (
            "Confirmer le phénomène central, les causes encore ouvertes, les conditions "
            "d'essai comparables et l'effet sur la performance recherchée."
        ),
        "generation_source": "deterministic_source_based_reformulation",
    }


def _batch_clusters(
    clusters: Sequence[Mapping[str, Any]],
    style_block: str,
    previous_cir_context: Optional[Mapping[str, Any]] = None,
) -> List[List[Mapping[str, Any]]]:
    configured = int(os.getenv("ENNOSMART_LLM_MAX_PROMPT_CHARS", "30000"))
    budget = max(9000, int(configured * 0.88))
    batches: List[List[Mapping[str, Any]]] = []
    current: List[Mapping[str, Any]] = []
    for cluster in clusters:
        trial = current + [cluster]
        if current and len(_build_prompt(trial, style_block, previous_cir_context)) > budget:
            batches.append(current)
            current = [cluster]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches

def _candidate_output(cluster: Mapping[str, Any], analysis: Mapping[str, Any], llm_generated: bool) -> Dict[str, Any]:
    sources = _cluster_sources(cluster)
    source_ids = [_source_identity(source) for source in sources]
    documents = _dedupe_texts(source.get("document") for source in sources if source.get("document"))
    member_group_ids = [str(value) for value in (cluster.get("member_group_ids") or []) if value]
    frascati_decisions = [str(value) for value in (cluster.get("frascati_decisions") or []) if value]
    upstream_decision = "verrou_probable" if "verrou_probable" in frascati_decisions else "verrou_a_verifier"

    title = _safe_visible(analysis.get("title")) or _fallback_title(cluster)
    uncertainty = _safe_visible(analysis.get("scientific_uncertainty"))
    causal_chain = _safe_visible(analysis.get("causal_chain"))
    why = _safe_visible(analysis.get("why_lock"))
    not_simple = _safe_visible(analysis.get("why_not_simple_engineering"))
    evidence_summary = _safe_visible(analysis.get("evidence_summary"))
    consultant_check = _safe_visible(analysis.get("consultant_check"))

    output = {
        "group_id": _clean(cluster.get("cluster_id")),
        "cluster_id": _clean(cluster.get("cluster_id")),
        "member_group_ids": member_group_ids,
        "original_nlp_group_ids": member_group_ids,
        "support_group_ids": list(cluster.get("support_group_ids") or []),
        "lock_scope": _clean(cluster.get("lock_scope") or "project_structuring_lock"),
        "display_as_main_lock": True,
        "cluster_role": _clean(cluster.get("cluster_role") or "verrou_a_verifier"),
        "cluster_role_confidence": _safe_float(cluster.get("cluster_role_confidence"), 0.5),
        "cluster_role_reasons": list(cluster.get("cluster_role_reasons") or []),
        "related_cluster_ids": list(cluster.get("related_cluster_ids") or []),
        "display_as_lock": True,
        "title": title,
        "tag_cir": "Signal R&D candidat — à confirmer",
        "score": round(_safe_float(cluster.get("frascati_score")), 4),
        "frascati_decision": upstream_decision,
        "consultant_status": "en_attente",
        "candidate_status": "candidate_to_validate",
        "not_final_cir": True,
        "document": " ; ".join(documents),
        "justification": why,
        "text": why,
        "scientific_lock": uncertainty,
        "causal_chain": causal_chain,
        "technical_axis": title,
        "project_reasoning": why,
        "why_not_simple_engineering": not_simple,
        "evidence_summary": evidence_summary,
        "consultant_explanation": why,
        "agent_reasoning": why,
        "why_agent_found_verrou": why,
        "consultant_check": consultant_check,
        "risk_level": "à évaluer",
        "source_ids": source_ids,
        "sources": sources,
        "source": "nlp_semantic_lock_group",
        "needs_human_validation": True,
        "llm_generated": bool(llm_generated),
        "llm_generated_fields": list(analysis.get("_llm_generated_fields") or []),
        "deterministic_completion_fields": list(analysis.get("_deterministic_completion_fields") or []),
        "reformulation_stage": _clean(analysis.get("_reformulation_stage") or ("llm" if llm_generated else "fallback")),
        "llm_is_cir_lock": _clean(cluster.get("cluster_role")) in LOCK_ROLES,
        "llm_classification": upstream_decision,
        "classification_contract_valid": True,
        "qualification_source": "nlp_single_grouping_then_frascati",
        "grouping_policy": "nlp_only_before_frascati_no_downstream_regrouping",
        "concept_profile": cluster.get("concept_profile") or {},
        "upstream_frascati_score": round(_safe_float(cluster.get("frascati_score")), 4),
        "semantic_similarity_min": cluster.get("semantic_similarity_min"),
        "semantic_similarity_mean": cluster.get("semantic_similarity_mean"),
    }
    output["source_json"] = {
        "source": output["source"],
        "cluster_id": output["cluster_id"],
        "member_group_ids": member_group_ids,
        "sources": sources,
        "candidate_source_ids": source_ids,
        "semantic_cluster": {
            "lock_scope": output["lock_scope"],
            "semantic_similarity_min": cluster.get("semantic_similarity_min"),
            "semantic_similarity_mean": cluster.get("semantic_similarity_mean"),
        },
        "principle": (
            "La composition du groupe est calculée par le NLP avant Frascati. "
            "Le LLM reformule uniquement les preuves sans modifier la couverture."
        ),
    }
    return output


def _support_output(cluster: Mapping[str, Any]) -> Dict[str, Any]:
    sources = _cluster_sources(cluster)
    return {
        "group_id": _clean(cluster.get("cluster_id")),
        "cluster_id": _clean(cluster.get("cluster_id")),
        "member_group_ids": list(cluster.get("member_group_ids") or []),
        "lock_scope": "supporting_measurement",
        "display_as_lock": False,
        "title": _fallback_title(cluster),
        "score": round(_safe_float(cluster.get("frascati_score")), 4),
        "candidate_status": "supporting_measurement",
        "not_final_cir": True,
        "sources": sources,
        "source_ids": [_source_identity(source) for source in sources],
        "source": "nlp_semantic_lock_group_support",
        "needs_human_validation": True,
    }



def _context_output(cluster: Mapping[str, Any]) -> Dict[str, Any]:
    sources = _cluster_sources(cluster)
    role = _clean(cluster.get("cluster_role") or "contexte_technique")
    labels = {
        "hypothese_methodologique": "Hypothèse méthodologique à valider",
        "resultat_technique": "Résultat technique",
        "contexte_technique": "Contexte technique",
    }
    return {
        "group_id": _clean(cluster.get("cluster_id")),
        "cluster_id": _clean(cluster.get("cluster_id")),
        "member_group_ids": list(cluster.get("member_group_ids") or []),
        "support_group_ids": list(cluster.get("support_group_ids") or []),
        "cluster_role": role,
        "lock_scope": _clean(cluster.get("lock_scope")),
        "display_as_lock": False,
        "title": labels.get(role, "Point technique à examiner"),
        "description": _truncate(
            cluster.get("representative_text") or cluster.get("semantic_text"),
            1000,
        ),
        "classification_reasons": list(cluster.get("cluster_role_reasons") or []),
        "score": round(_safe_float(cluster.get("frascati_score")), 4),
        "sources": sources,
        "source_ids": [_source_identity(source) for source in sources],
        "source": "nlp_semantic_lock_group_context",
        "needs_human_validation": True,
        "not_final_cir": True,
    }


def synthesize_consultant_verrous(
    sections: Dict[str, Any],
    frascati_summary: Optional[Dict[str, Any]] = None,
    llm: Any = None,
    style_block: str = "",
    memory_v2_report: Optional[Dict[str, Any]] = None,
    previous_cir_context: Optional[Dict[str, Any]] = None,
    lock_clusters: Optional[Dict[str, Any]] = None,
    lock_clusters_path: str | Path | None = None,
    cache_path: str | Path | None = None,
    **legacy_arguments: Any,
) -> Dict[str, Any]:
    """Reformule les groupes NLP avec mémoire antérieure de style uniquement."""
    sections = sections if isinstance(sections, dict) else {}
    report, cluster_source, cluster_load_error = _resolve_cluster_report(
        sections=sections,
        lock_clusters=lock_clusters,
        lock_clusters_path=lock_clusters_path,
    )
    clusters = [cluster for cluster in (report.get("clusters") or []) if isinstance(cluster, dict)]
    display_clusters = [
        cluster for cluster in clusters
        if _safe_bool(cluster.get("display_as_lock"), True)
        and _clean(cluster.get("cluster_role") or "verrou_a_verifier") in LOCK_ROLES
    ]
    context_clusters = [
        cluster for cluster in clusters
        if _clean(cluster.get("cluster_role")) in CONTEXT_ROLES
    ]
    support_clusters = [
        cluster for cluster in clusters
        if _clean(cluster.get("cluster_role")) in SUPPORT_ROLES
        and not _safe_bool(cluster.get("display_as_lock"), False)
    ]

    previous_summary = _previous_context_summary(previous_cir_context)

    # Les groupes NLP sont immuables entre deux relances « Diagnostic » tant
    # que les sources ne sont pas préparées à nouveau. Réutiliser leur
    # reformulation évite jusqu'à 7 appels LLM identiques sans modifier un seul
    # candidat de l'agent 1.
    cache_key_payload = {
        "version": VERSION,
        "clusters": display_clusters,
        "style_block": style_block,
        "previous_summary": previous_summary,
    }
    cache_key = hashlib.sha256(
        json.dumps(
            cache_key_payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8", errors="ignore")
    ).hexdigest()
    resolved_cache_path = Path(cache_path) if cache_path else None
    if resolved_cache_path is not None:
        try:
            cached = json.loads(resolved_cache_path.read_text(encoding="utf-8"))
            cached_report = cached.get("report") if isinstance(cached, dict) else None
            if cached.get("cache_key") == cache_key and isinstance(cached_report, dict):
                output = dict(cached_report)
                output["cached"] = True
                output["cache_key"] = cache_key
                output["llm_calls_reused"] = int(output.get("llm_calls") or 0)
                output["llm_calls"] = 0
                output["qualification_llm_calls"] = 0
                output["qualification_retry_llm_calls"] = 0
                output["qualification_final_repair_llm_calls"] = 0
                output["qualification_title_repair_llm_calls"] = 0
                output["qualification_title_repair_retry_llm_calls"] = 0
                return output
        except Exception:
            pass

    if not display_clusters:
        return {
            "ok": True,
            "version": VERSION,
            "mode": "no_displayable_nlp_group",
            "principle": "Aucun verrou n'est forcé.",
            "cluster_source": cluster_source,
            "cluster_load_error": cluster_load_error,
            "groups_count": int(report.get("groups_count") or 0),
            "pre_consolidation_count": int(report.get("groups_count") or 0),
            "final_count": 0,
            "supporting_only_count": len(support_clusters),
            "context_only_count": len(context_clusters),
            "qualification_llm_calls": 0,
            "qualification_retry_llm_calls": 0,
            "qualification_final_repair_llm_calls": 0,
            "qualification_title_repair_llm_calls": 0,
            "qualification_title_repair_retry_llm_calls": 0,
            "llm_calls": 0,
            "previous_cir_context": previous_summary,
            "llm_reformulated_verrous": [],
            "consolidation_support_groups": [_support_output(cluster) for cluster in support_clusters],
            "context_only_groups": [_context_output(cluster) for cluster in context_clusters],
        }

    batches = _batch_clusters(display_clusters, style_block, previous_cir_context)
    by_cluster: Dict[str, Dict[str, Any]] = {}
    partial_by_cluster: Dict[str, Dict[str, Any]] = {}
    validation_errors: Dict[str, List[str]] = {}
    parse_errors: List[str] = []
    llm_calls = 0
    retry_calls = 0
    final_repair_calls = 0
    title_repair_calls = 0
    title_repair_retry_calls = 0
    prompt_sizes: List[int] = []

    if llm is not None:
        for batch_index, batch in enumerate(batches, start=1):
            prompt = _build_prompt(batch, style_block, previous_cir_context)
            prompt_sizes.append(len(prompt))
            expected = {_clean(cluster.get("cluster_id")): cluster for cluster in batch}
            invalid: Dict[str, List[str]] = {}
            try:
                raw = _generate(
                    llm,
                    prompt,
                    request_name="ennodiagnostic:verrou_reformulation",
                    max_tokens=int(os.getenv("ENNOSMART_DIAG_VERROU_MAX_OUTPUT_TOKENS", "3000")),
                )
                llm_calls += 1
                parsed = _extract_json(raw)
                values = parsed.get("clusters") if isinstance(parsed, dict) else parsed
                if not isinstance(values, list):
                    raise ValueError("tableau clusters absent")
                returned = {
                    _clean(value.get("cluster_id")): value
                    for value in values
                    if isinstance(value, dict) and _clean(value.get("cluster_id"))
                }
                for cluster_id, cluster in expected.items():
                    normalized, errors = _validate_analysis(returned.get(cluster_id), cluster_id, cluster)
                    if normalized is not None:
                        partial_by_cluster[cluster_id] = normalized
                    if normalized is not None and not errors:
                        normalized["_llm_generated_fields"] = list(VISIBLE_FIELDS)
                        normalized["_deterministic_completion_fields"] = []
                        normalized["_reformulation_stage"] = "initial_llm"
                        by_cluster[cluster_id] = normalized
                    else:
                        invalid[cluster_id] = errors or ["réponse incomplète"]
            except Exception as exc:
                parse_errors.append(f"batch {batch_index}: {exc}")
                invalid = {cluster_id: [str(exc)] for cluster_id in expected}

            if invalid:
                retry_batch = [expected[cluster_id] for cluster_id in invalid]
                retry_prompt = _build_retry_prompt(
                    retry_batch,
                    invalid,
                    drafts={cluster_id: partial_by_cluster.get(cluster_id, {}) for cluster_id in invalid},
                    previous_cir_context=previous_cir_context,
                )
                prompt_sizes.append(len(retry_prompt))
                try:
                    raw = _generate(
                        llm,
                        retry_prompt,
                        request_name="ennodiagnostic:verrou_reformulation_retry",
                        max_tokens=int(os.getenv("ENNOSMART_DIAG_VERROU_RETRY_MAX_OUTPUT_TOKENS", "2400")),
                    )
                    llm_calls += 1
                    retry_calls += 1
                    parsed = _extract_json(raw)
                    values = parsed.get("clusters") if isinstance(parsed, dict) else parsed
                    if not isinstance(values, list):
                        raise ValueError("tableau clusters absent dans la relance")
                    returned = {
                        _clean(value.get("cluster_id")): value
                        for value in values
                        if isinstance(value, dict) and _clean(value.get("cluster_id"))
                    }
                    for cluster_id in invalid:
                        retry_normalized, _ = _validate_analysis(
                            returned.get(cluster_id), cluster_id, expected.get(cluster_id)
                        )
                        merged = _merge_analysis(
                            partial_by_cluster.get(cluster_id),
                            retry_normalized,
                            cluster_id,
                        )
                        partial_by_cluster[cluster_id] = merged
                        normalized, errors = _validate_analysis(merged, cluster_id, expected.get(cluster_id))
                        if normalized is not None and not errors:
                            normalized["_llm_generated_fields"] = [
                                field for field in VISIBLE_FIELDS if _safe_visible(normalized.get(field))
                            ]
                            normalized["_deterministic_completion_fields"] = []
                            normalized["_reformulation_stage"] = "targeted_retry"
                            by_cluster[cluster_id] = normalized
                        else:
                            validation_errors[cluster_id] = errors or ["réponse encore incomplète"]
                except Exception as exc:
                    parse_errors.append(f"batch {batch_index} retry: {exc}")
                    for cluster_id, errors in invalid.items():
                        validation_errors[cluster_id] = [*errors, str(exc)]
    else:
        for batch in batches:
            prompt_sizes.append(len(_build_prompt(batch, style_block, previous_cir_context)))

    # Une dernière relance groupée répare uniquement les clusters restant invalides.
    remaining_ids = [
        _clean(cluster.get("cluster_id"))
        for cluster in display_clusters
        if _clean(cluster.get("cluster_id")) not in by_cluster
    ]
    if llm is not None and remaining_ids:
        expected_all = {_clean(cluster.get("cluster_id")): cluster for cluster in display_clusters}
        repair_clusters = [expected_all[cluster_id] for cluster_id in remaining_ids]
        remaining_errors = {
            cluster_id: validation_errors.get(cluster_id) or ["fiche non validée après relance"]
            for cluster_id in remaining_ids
        }
        repair_prompt = _build_final_repair_prompt(
            repair_clusters,
            remaining_errors,
            drafts={cluster_id: partial_by_cluster.get(cluster_id, {}) for cluster_id in remaining_ids},
            previous_cir_context=previous_cir_context,
        )
        prompt_sizes.append(len(repair_prompt))
        try:
            raw = _generate(
                llm,
                repair_prompt,
                request_name="ennodiagnostic:verrou_reformulation_final_repair",
                max_tokens=int(os.getenv("ENNOSMART_DIAG_VERROU_FINAL_REPAIR_MAX_OUTPUT_TOKENS", "2600")),
            )
            llm_calls += 1
            final_repair_calls += 1
            parsed = _extract_json(raw)
            values = parsed.get("clusters") if isinstance(parsed, dict) else parsed
            if not isinstance(values, list):
                raise ValueError("tableau clusters absent dans la réparation finale")
            returned = {
                _clean(value.get("cluster_id")): value
                for value in values
                if isinstance(value, dict) and _clean(value.get("cluster_id"))
            }
            for cluster_id in remaining_ids:
                candidate, _ = _validate_analysis(
                    returned.get(cluster_id), cluster_id, expected_all.get(cluster_id)
                )
                merged = _merge_analysis(partial_by_cluster.get(cluster_id), candidate, cluster_id)
                partial_by_cluster[cluster_id] = merged
                normalized, errors = _validate_analysis(merged, cluster_id, expected_all.get(cluster_id))
                if normalized is not None and not errors:
                    normalized["_llm_generated_fields"] = [
                        field for field in VISIBLE_FIELDS if _safe_visible(normalized.get(field))
                    ]
                    normalized["_deterministic_completion_fields"] = []
                    normalized["_reformulation_stage"] = "final_llm_repair"
                    by_cluster[cluster_id] = normalized
                    validation_errors.pop(cluster_id, None)
                else:
                    validation_errors[cluster_id] = errors or ["réparation finale incomplète"]
        except Exception as exc:
            parse_errors.append(f"final repair: {exc}")

    # Réparation dédiée du titre : si tous les autres champs sont valides mais
    # que le titre seul échoue, on ne revient jamais au placeholder. On demande
    # au LLM une phrase nominale CIR courte, puis on la valide avec les mêmes
    # règles de grounding.
    title_repair_ids: List[str] = []
    expected_all = {_clean(cluster.get("cluster_id")): cluster for cluster in display_clusters}
    for cluster_id, cluster in expected_all.items():
        if cluster_id in by_cluster:
            continue
        draft = partial_by_cluster.get(cluster_id) or {}
        _, current_errors = _validate_analysis(draft, cluster_id, cluster)
        current_fields = _error_fields(current_errors)
        non_title_fields = {
            field for field in current_fields
            if field not in {"title", "cluster_id"}
        }
        if draft and not non_title_fields and "title" in current_fields:
            title_repair_ids.append(cluster_id)

    if llm is not None:
        for cluster_id in title_repair_ids:
            cluster = expected_all[cluster_id]
            draft = partial_by_cluster.get(cluster_id) or {}
            _, current_errors = _validate_analysis(draft, cluster_id, cluster)
            previous_title = _safe_visible(draft.get("title"))
            repaired = False
            for attempt in range(2):
                title_prompt = _build_title_repair_prompt(
                    cluster,
                    draft,
                    current_errors,
                    previous_cir_context=previous_cir_context,
                    previous_title=previous_title,
                )
                prompt_sizes.append(len(title_prompt))
                request_name = (
                    "ennodiagnostic:verrou_title_repair"
                    if attempt == 0
                    else "ennodiagnostic:verrou_title_repair_retry"
                )
                try:
                    raw = _generate(
                        llm,
                        title_prompt,
                        request_name=request_name,
                        max_tokens=int(
                            os.getenv(
                                "ENNOSMART_DIAG_VERROU_TITLE_REPAIR_MAX_OUTPUT_TOKENS",
                                "320",
                            )
                        ),
                    )
                    llm_calls += 1
                    title_repair_calls += 1
                    if attempt > 0:
                        title_repair_retry_calls += 1
                    title = _extract_title_repair(raw, cluster_id)
                    title_errors = _title_quality_errors(title, cluster)
                    title_errors.extend(
                        error
                        for error in _grounding_errors({"title": title}, cluster)
                        if _norm(error).startswith("champ title")
                    )
                    title_errors = _dedupe_texts(title_errors)
                    if title_errors:
                        previous_title = title
                        current_errors = title_errors
                        continue

                    merged = _merge_analysis(
                        draft,
                        {"cluster_id": cluster_id, "title": title},
                        cluster_id,
                    )
                    normalized, errors = _validate_analysis(merged, cluster_id, cluster)
                    if normalized is not None and not errors:
                        normalized["_llm_generated_fields"] = [
                            field for field in VISIBLE_FIELDS
                            if _safe_visible(normalized.get(field))
                        ]
                        normalized["_deterministic_completion_fields"] = []
                        normalized["_reformulation_stage"] = (
                            "title_only_llm_repair"
                            if attempt == 0
                            else "title_only_llm_repair_retry"
                        )
                        by_cluster[cluster_id] = normalized
                        partial_by_cluster[cluster_id] = normalized
                        validation_errors.pop(cluster_id, None)
                        repaired = True
                        break
                    current_errors = errors or ["titre réparé mais fiche encore invalide"]
                except Exception as exc:
                    parse_errors.append(f"title repair {cluster_id} attempt {attempt + 1}: {exc}")
                    current_errors = [str(exc)]

            if not repaired:
                # Dernier filet de sécurité générique : dériver le titre depuis
                # l'incertitude scientifique déjà produite, jamais depuis top_terms.
                derived_title = _title_from_uncertainty(
                    draft.get("scientific_uncertainty"),
                    cluster,
                )
                if derived_title:
                    merged = _merge_analysis(
                        draft,
                        {"cluster_id": cluster_id, "title": derived_title},
                        cluster_id,
                    )
                    normalized, errors = _validate_analysis(merged, cluster_id, cluster)
                    if normalized is not None and not errors:
                        normalized["_llm_generated_fields"] = [
                            field for field in VISIBLE_FIELDS
                            if field != "title" and _safe_visible(normalized.get(field))
                        ]
                        normalized["_deterministic_completion_fields"] = ["title"]
                        normalized["_reformulation_stage"] = (
                            "title_derived_from_validated_scientific_uncertainty"
                        )
                        by_cluster[cluster_id] = normalized
                        partial_by_cluster[cluster_id] = normalized
                        validation_errors.pop(cluster_id, None)
                    else:
                        validation_errors[cluster_id] = errors or current_errors
                else:
                    validation_errors[cluster_id] = current_errors

    final_items: List[Dict[str, Any]] = []
    covered_cluster_ids: List[str] = []
    llm_generated_count = 0
    partial_preserved_count = 0
    deterministic_completion_count = 0

    for cluster in display_clusters:
        cluster_id = _clean(cluster.get("cluster_id"))
        analysis = by_cluster.get(cluster_id)
        generated = bool(
            isinstance(analysis, dict)
            and "title" not in set(analysis.get("_deterministic_completion_fields") or [])
        )
        if not generated:
            partial = partial_by_cluster.get(cluster_id)
            completed, remaining_errors, deterministic_fields = _complete_analysis_preserving_llm(
                partial,
                cluster,
            )
            title_from_llm = bool(
                isinstance(partial, Mapping)
                and _title_is_valid(partial.get("title"), cluster)
                and completed.get("title") == partial.get("title")
            )
            llm_fields = [
                field for field in VISIBLE_FIELDS
                if isinstance(partial, Mapping)
                and _safe_visible(partial.get(field))
                and field not in set(deterministic_fields)
            ]
            completed["_llm_generated_fields"] = llm_fields
            completed["_deterministic_completion_fields"] = deterministic_fields
            completed["_reformulation_stage"] = (
                "partial_llm_preserved_with_safe_completion"
                if llm_fields else "safe_fallback_without_llm_output"
            )
            analysis = completed
            generated = title_from_llm
            if llm_fields:
                partial_preserved_count += 1
            if deterministic_fields:
                deterministic_completion_count += 1
            if remaining_errors:
                validation_errors[cluster_id] = remaining_errors
        if generated:
            llm_generated_count += 1
        final_items.append(_candidate_output(cluster, analysis, llm_generated=generated))
        covered_cluster_ids.append(cluster_id)

    expected_cluster_ids = [_clean(cluster.get("cluster_id")) for cluster in display_clusters]
    uncovered_clusters = [cluster_id for cluster_id in expected_cluster_ids if cluster_id not in set(covered_cluster_ids)]

    input_group_ids = [str(value) for value in ((report.get("coverage") or {}).get("input_group_ids") or [])]
    covered_group_ids = [
        str(group_id)
        for cluster in clusters
        for group_id in (
            list(cluster.get("member_group_ids") or [])
            + list(cluster.get("support_group_ids") or [])
        )
    ]

    output = {
        "ok": True,
        "version": VERSION,
        "mode": "nlp_groups_cir_reformulation_with_title_only_repair",
        "principle": (
            "Le NLP groupe les verrous avant Frascati ; le RAG les transmet sans "
            "consolidation ; EnnoDiagnostic charge avant reformulation des exemples CIR antérieurs "
            "utilisés uniquement pour le style, puis préserve les champs LLM valides. "
            "Si le titre seul échoue, une relance LLM dédiée au titre est exécutée "
            "avant toute complétion sûre, sans utiliser concept_profile.top_terms."
        ),
        "cluster_source": cluster_source,
        "cluster_report_version": report.get("version"),
        "cluster_report_mode": report.get("mode"),
        "cluster_report_path": "",
        "cluster_load_error": cluster_load_error,
        "sources_count": sum(len(_cluster_sources(cluster)) for cluster in clusters),
        "groups_count": int(report.get("groups_count") or len(input_group_ids)),
        "pre_consolidation_count": int(report.get("groups_count") or len(input_group_ids)),
        "semantic_clusters_count": len(clusters),
        "batch_count": len(batches),
        "qualification_llm_calls": llm_calls,
        "qualification_retry_llm_calls": retry_calls,
        "qualification_final_repair_llm_calls": final_repair_calls,
        "qualification_title_repair_llm_calls": title_repair_calls,
        "qualification_title_repair_retry_llm_calls": title_repair_retry_calls,
        "llm_calls": llm_calls,
        "llm_generated_count": llm_generated_count,
        "partial_llm_preserved_count": partial_preserved_count,
        "deterministic_completion_count": deterministic_completion_count,
        "prompt_chars_total": sum(prompt_sizes),
        "prompt_chars_max_batch": max(prompt_sizes) if prompt_sizes else 0,
        "final_count": len(final_items),
        "supporting_only_count": len(support_clusters),
        "context_only_count": len(context_clusters),
        "parse_error": " | ".join(parse_errors) or None,
        "classification_validation_errors": validation_errors,
        "consolidation_error": None,
        "consolidation_mode": "disabled_nlp_group_passthrough",
        "downstream_regrouping_applied": False,
        "legacy_limit_arguments_ignored": sorted(legacy_arguments.keys()),
        "memory_v2_used_for_context": bool(isinstance(memory_v2_report, dict) and memory_v2_report.get("ok")),
        "previous_cir_context": previous_summary,
        "previous_cir_examples_in_prompt": bool(previous_summary.get("available")),
        "previous_cir_factual_use_allowed": False,
        "coverage": {
            "input_group_ids": input_group_ids,
            "covered_group_ids": covered_group_ids,
            "uncovered_group_ids": [group_id for group_id in input_group_ids if group_id not in set(covered_group_ids)],
            "display_cluster_ids": expected_cluster_ids,
            "covered_display_cluster_ids": covered_cluster_ids,
            "uncovered_display_cluster_ids": uncovered_clusters,
            "coverage_rate": round(len(set(covered_group_ids)) / max(1, len(set(input_group_ids))), 4) if input_group_ids else 1.0,
        },
        "context_only_groups": [_context_output(cluster) for cluster in context_clusters],
        "pre_consolidation_verrous": [],
        "consolidation_support_groups": [_support_output(cluster) for cluster in support_clusters],
        "llm_reformulated_verrous": final_items,
    }
    output["cache_key"] = cache_key
    if resolved_cache_path is not None:
        try:
            resolved_cache_path.parent.mkdir(parents=True, exist_ok=True)
            resolved_cache_path.write_text(
                json.dumps(
                    {"cache_key": cache_key, "version": VERSION, "report": output},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            output["cache_write_error"] = str(exc)
    return output

