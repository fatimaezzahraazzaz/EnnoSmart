# -*- coding: utf-8 -*-
from __future__ import annotations

"""Consolidation sémantique des groupes de verrous qualifiés.

Cette étape appartient au RAG, pas au LLM :
- les groupes NLP/Frascati sont encodés avec le modèle d'embeddings déjà chargé ;
- les fusions sont validées par similarité sémantique + ancres discriminantes ;
- l'algorithme utilise un complete-linkage strict, sans pont transitif ;
- tout groupe non fusionné est conservé séparément ;
- une limite de mesure peut être rattachée comme support, mais jamais supprimée.

Le module ne contient aucun identifiant ni règle spécifique à un projet, un domaine, un document ou un cluster.
"""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

from .config import (
    EMBEDDING_MODEL_NAME,
    LOCK_CLUSTER_COMPLETE_LINK_MIN,
    LOCK_CLUSTER_ENABLED,
    LOCK_CLUSTER_MAX_EVIDENCE_PASSAGES,
    LOCK_CLUSTER_MEASUREMENT_ATTACH_MIN,
    LOCK_CLUSTER_MEASUREMENT_MARGIN,
    LOCK_CLUSTER_MIN_SIMILARITY,
    LOCK_CLUSTER_STRONG_SIMILARITY,
    LOCK_CLUSTER_ROLE_CLASSIFICATION_ENABLED,
    LOCK_CLUSTER_RELATED_MIN_SIMILARITY,
)
from .json_to_chunks import build_lock_semantic_text, get_pack


VERSION = "lock_semantic_consolidator_v182_scope_and_frascati_bridge"

POSITIVE_DECISIONS = {
    "verrou_probable",
    "verrou_a_verifier",
    "verrou_à_vérifier",
}

GENERIC_TERMS = {
    "projet", "dossier", "document", "source", "passage", "groupe",
    "verrou", "signal", "technique", "scientifique", "analyse", "etude",
    "essai", "essais", "resultat", "resultats", "objectif", "methode",
    "parametre", "parametres", "systeme", "ensemble", "equipement",
    "machine", "dispositif", "solution", "application", "fonctionnement",
    "performance", "amelioration", "validation", "valeur", "valeurs",
    "mesure", "mesures", "releve", "releves", "test", "tests",
    "prefix", "pdf", "docx", "xlsx", "txt", "concl", "conclus",
    "conclusion", "creation", "creat", "donne", "donnees",
    "project", "document", "source", "group", "technical", "system",
    "equipment", "test", "result", "measurement", "parameter",
}

STOPWORDS = {
    "avec", "sans", "dans", "pour", "par", "sur", "sous", "entre",
    "des", "les", "une", "un", "aux", "est", "sont", "etre", "avoir",
    "qui", "que", "quoi", "dont", "cette", "ces", "leur", "leurs",
    "plus", "moins", "ainsi", "afin", "donc", "mais", "tout", "tous",
    "the", "and", "with", "from", "that", "this", "using", "used",
    "of", "to", "in", "on", "for", "a", "an",
}


GENERIC_PHRASES = {
    "objectif technique", "resultat essai", "resultats essais",
    "essai mesure", "mesure parametre",
    "projet dossier", "analyse technique", "validation projet",
}

MEASUREMENT_MARKERS = (
    "debitmetre", "sonometre", "accelerometre", "capteur", "instrument",
    "etalonnage", "calibrage", "calibration", "precision", "biais",
    "mesure faussee", "fausse mesure", "incertitude de mesure",
    "limite de mesure", "lecture faussee", "measurement", "sensor",
    "meter", "instrumentation", "accuracy", "bias",
)

PHENOMENON_MARKERS = (
    "acoust", "bruit", "vibr", "usure", "fuite", "soufflage",
    "refroid", "refriger", "therm", "temperature", "pression",
    "performance", "rendement", "gain", "reduction", "impact",
    "cause", "origine", "mecanisme", "comportement", "stabilite",
)

STRONG_INSTRUMENT_MARKERS = (
    "debitmetre", "sonometre", "accelerometre", "capteur", "instrument",
    "etalonnage", "calibrage", "calibration", "faussee", "fausse mesure",
    "precision", "biais", "polluer le capteur", "measurement bias",
)

UNCERTAINTY_MARKERS = (
    "incert", "inconnu", "non maitr", "non-maitr", "ne permet pas",
    "ne peut pas", "a verifier", "à vérifier", "a confirmer", "à confirmer",
    "a caracteriser", "à caractériser", "a quantifier", "à quantifier",
    "cause", "origine", "mecanisme", "hypothese", "hypothèse", "limite",
    "variabil", "generalisation", "généralisation", "comparabil",
)

# Termes trop faibles pour justifier le rattachement d'une limite de mesure.
# Cette liste est générique : elle élimine les mots de liaison, de fichier ou
# de contexte qui peuvent apparaître dans plusieurs axes sans relation causale.
WEAK_RELATION_TERMS = {
    "inter", "interne", "peut", "etant", "apres", "avant", "ainsi",
    "important", "normale", "normal", "maximum", "minimum", "valeur",
    "utilisation", "releve", "releves", "rev", "rev1", "fichier",
    "section", "rapport", "page", "annexe", "docx", "pdf", "xlsx",
    "nous", "gain", "effet", "impact", "variation", "niveau",
}

WEAK_RELATION_PHRASES = {
    "rev1 docx", "rev docx", "rapport pdf", "section rapport",
}


# Indices génériques de nature méthodologique. Ils ne décrivent aucun métier :
# ils caractérisent la fonction linguistique d'un passage (hypothèse, choix de
# calcul, simplification ou condition de modèle).
METHODOLOGICAL_HYPOTHESIS_MARKERS = (
    "hypothese", "suppose", "supposee", "considere", "consideree",
    "approximation", "approxime", "assimile", "neglige", "negligee",
    "pris a", "prise a", "fixe a", "fixee a", "condition limite",
    "modele", "modelisation", "simulation", "isotherme", "stationnaire",
    "par defaut", "choix de calcul", "parametre de calcul",
)

RESULT_ONLY_MARKERS = (
    "on constate", "on observe", "a montre", "a permis", "gain mesure",
    "resultat obtenu", "mesure realisee", "valeur relevee",
)

OPEN_PROBLEM_MARKERS = (
    "reste a", "reste à", "doit etre confirme", "doit être confirmé",
    "a determiner", "à déterminer", "a comprendre", "à comprendre",
    "a expliquer", "à expliquer", "cause non identifiee", "origine inconnue",
    "mecanisme non etabli", "non reproductible", "non generalisable",
)

LOCK_CLUSTER_ROLES = {"verrou_scientifique", "verrou_a_verifier"}
NON_LOCK_CLUSTER_ROLES = {
    "hypothese_methodologique", "limite_de_mesure",
    "resultat_technique", "contexte_technique",
}


@dataclass
class LockGroup:
    group_id: str
    raw_item: Dict[str, Any]
    semantic_text: str
    anchor_terms: Set[str]
    anchor_phrases: Set[str]
    documents: Set[str]
    frascati_score: float
    frascati_decision: str
    is_measurement_support: bool


@dataclass
class PairDecision:
    left_group_id: str
    right_group_id: str
    similarity: float
    shared_terms: List[str]
    shared_phrases: List[str]
    same_document: bool
    merge_allowed: bool
    measurement_relation: bool
    reason: str


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\b[a-f0-9]{10,}\b", " ", text)
    text = re.sub(r"[^a-z0-9%+./_-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value if value not in (None, "") else default)
        return default if math.isnan(number) or math.isinf(number) else number
    except Exception:
        return default


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _norm(value) in {"1", "true", "yes", "oui"}


def _unique_texts(values: Iterable[Any], max_items: Optional[int] = None) -> List[str]:
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


def _profile(item: Mapping[str, Any]) -> Dict[str, Any]:
    profile = item.get("concept_profile") or {}
    return profile if isinstance(profile, dict) else {}


def _decision(item: Mapping[str, Any]) -> str:
    frascati = item.get("frascati") or {}
    if not isinstance(frascati, dict):
        frascati = {}
    return _clean(frascati.get("decision") or item.get("frascati_decision") or item.get("final_role"))


def _score(item: Mapping[str, Any]) -> float:
    frascati = item.get("frascati") or {}
    if not isinstance(frascati, dict):
        frascati = {}
    return _safe_float(
        frascati.get("frascati_score")
        or item.get("frascati_score")
        or item.get("lock_candidate_score")
        or item.get("verrou_score")
    )


def _official_assessments_by_group(nlp_result: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Indexe les scores Frascati officiels sans les recalculer.

    Les groupes techniques du pack RAG et les évaluations Frascati sont deux
    vues séparées du même résultat NLP. Cette passerelle les rattache par
    ``group_id`` afin que le score survive à la consolidation sémantique.
    """
    guard = nlp_result.get("frascati_guard") or {}
    if not isinstance(guard, dict):
        return {}
    assessment = guard.get("frascati_assessment") or {}
    if not isinstance(assessment, dict):
        return {}
    values = assessment.get("group_assessments") or []
    if not isinstance(values, list):
        return {}
    output: Dict[str, Dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        group_id = _clean(value.get("group_id"))
        score = _safe_float(value.get("eligibility_score"))
        if group_id and score > 0:
            output[group_id] = {
                "eligibility_score": round(score, 4),
                "risk_level": _clean(value.get("risk_level")),
                "interpretation": _clean(value.get("interpretation")),
            }
    return output


def _group_id(item: Mapping[str, Any], index: int) -> str:
    value = _clean(item.get("lock_group_id") or item.get("passage_id") or item.get("id"))
    if value:
        return value
    digest = hashlib.sha1(
        f"{item.get('document')}|{item.get('text')}|{index}".encode("utf-8", errors="ignore")
    ).hexdigest()[:16]
    return f"lock_group_{digest}"


def _is_qualified_lock_group(item: Mapping[str, Any]) -> bool:
    """V172 : Frascati n'est plus autorisé à supprimer un groupe technique.

    Un groupe est transmis au RAG s'il a été construit par le pipeline technique
    et contient des preuves. Les anciens champs restent acceptés pour migration.
    """
    if not isinstance(item, dict):
        return False
    candidate = bool(
        _safe_bool(item.get("technical_group_candidate"))
        or _safe_bool(item.get("lock_group_candidate"))
        or item.get("lock_group_id")
    )
    evidence = item.get("supporting_passages") or []
    has_evidence = bool(evidence or item.get("analysis_text") or item.get("text"))
    return bool(candidate and has_evidence)


def _documents(item: Mapping[str, Any]) -> Set[str]:
    docs: Set[str] = set()
    direct = _clean(item.get("source_path") or item.get("document"))
    if direct:
        docs.add(direct.lower())
    supporting = item.get("supporting_documents") or []
    if isinstance(supporting, list):
        for entry in supporting:
            if isinstance(entry, dict):
                value = _clean(entry.get("source_path") or entry.get("document"))
            else:
                value = _clean(entry)
            if value:
                docs.add(value.lower())
    return docs


def _tokenize(value: Any) -> List[str]:
    return [
        token for token in _norm(value).split()
        if len(token) >= 3 and token not in STOPWORDS
    ]


def _extract_anchor_terms(item: Mapping[str, Any], semantic_text: str) -> Set[str]:
    profile = _profile(item)
    candidates: List[str] = []
    for key in ("top_terms", "technical_entities"):
        values = profile.get(key) or []
        if isinstance(values, list):
            candidates.extend(_clean(value) for value in values)

    # Les termes du profil NLP ont priorité. Les termes du texte complètent sans
    # introduire les mots de contexte les plus fréquents.
    candidates.extend(_tokenize(" ".join([
        _clean(item.get("candidate_group_label")),
        _clean(item.get("section_title")),
        _clean(item.get("text")),
        _clean(item.get("analysis_text")),
    ])))

    output: Set[str] = set()
    for candidate in candidates:
        normalized = _norm(candidate)
        if not normalized:
            continue
        for token in normalized.split():
            if len(token) < 4 or token in STOPWORDS or token in GENERIC_TERMS:
                continue
            # Les identifiants de projet, dates, hashes, extensions et valeurs
            # numériques ne doivent pas créer de faux rapprochements.
            if any(char.isdigit() for char in token):
                continue
            if token.startswith("prefix"):
                continue
            if len(token) >= 4:
                output.add(token)
    return output


def _extract_anchor_phrases(item: Mapping[str, Any], semantic_text: str) -> Set[str]:
    profile = _profile(item)
    phrases: List[str] = []
    values = profile.get("top_phrases") or []
    if isinstance(values, list):
        phrases.extend(_clean(value) for value in values)

    tokens = _tokenize(" ".join([
        _clean(item.get("candidate_group_label")),
        _clean(item.get("section_title")),
        _clean(item.get("text")),
        _clean(item.get("analysis_text")),
    ]))
    for size in (2, 3):
        for index in range(0, max(0, len(tokens) - size + 1)):
            phrase_tokens = tokens[index:index + size]
            # Un composé de grandeurs génériques peut rester discriminant :
            # « débit eau », « température sortie », etc. On écarte seulement
            # quelques expressions réellement génériques.
            phrase = " ".join(phrase_tokens)
            normalized_phrase = _norm(phrase)
            if normalized_phrase in GENERIC_PHRASES:
                continue
            if any(
                token in {"prefix", "pdf", "docx", "xlsx", "txt"}
                or any(char.isdigit() for char in token)
                for token in phrase_tokens
            ):
                continue
            phrases.append(phrase)

    output: Set[str] = set()
    for phrase in phrases:
        normalized = _norm(phrase)
        if len(normalized) < 6:
            continue
        output.add(normalized)
    return output


def _is_measurement_support(item: Mapping[str, Any], semantic_text: str) -> bool:
    label = _norm(" ".join([
        _clean(item.get("candidate_group_label")),
        _clean(item.get("section_title")),
    ]))
    representative = _norm(" ".join([
        _clean(item.get("candidate_group_label")),
        _clean(item.get("section_title")),
        _clean(item.get("text")),
        _clean(item.get("analysis_text")),
    ]))
    all_text = _norm(representative + " " + semantic_text[:2200])

    label_instrument_hits = sum(1 for marker in STRONG_INSTRUMENT_MARKERS if _norm(marker) in label)
    representative_instrument_hits = sum(
        1 for marker in STRONG_INSTRUMENT_MARKERS if _norm(marker) in representative
    )
    all_measurement_hits = sum(1 for marker in MEASUREMENT_MARKERS if _norm(marker) in all_text)
    label_phenomenon_hits = sum(1 for marker in PHENOMENON_MARKERS if _norm(marker) in label)

    features = item.get("lock_candidate_features") or {}
    if not isinstance(features, dict):
        features = {}
    measurement_flag = _safe_bool(features.get("measurement_limit"))

    # Une étude acoustique, thermique ou vibratoire peut utiliser plusieurs
    # instruments sans devenir une simple limite de mesure. Si le label porte
    # clairement sur le phénomène et non sur l'instrument, le groupe reste un
    # verrou affichable.
    phenomenon_led_label = label_phenomenon_hits >= 1 and label_instrument_hits == 0
    if phenomenon_led_label:
        return False

    # Une limite instrumentale est considérée comme support lorsque la fiabilité
    # de la mesure est réellement centrale dans le passage représentatif. Le
    # flag NLP seul ne suffit jamais.
    return bool(
        label_instrument_hits >= 1
        or representative_instrument_hits >= 2
        or (measurement_flag and representative_instrument_hits >= 1 and all_measurement_hits >= 2)
    )


def extract_lock_groups(nlp_result: Dict[str, Any]) -> List[LockGroup]:
    pack = get_pack(nlp_result)
    items = (
        pack.get("technical_lock_groups")
        or nlp_result.get("technical_lock_groups")
        or pack.get("verrous_rnd_locaux")
        or []
    )
    if not isinstance(items, list):
        return []

    official_assessments = _official_assessments_by_group(nlp_result)
    output: List[LockGroup] = []
    seen: Set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not _is_qualified_lock_group(item):
            continue
        group_id = _group_id(item, index)
        if group_id in seen:
            continue
        seen.add(group_id)
        assessment = official_assessments.get(group_id) or {}
        raw_item = dict(item)
        if assessment and _score(raw_item) <= 0:
            raw_item["frascati_score"] = assessment["eligibility_score"]
            raw_item["frascati_score_source"] = "nlp_group_assessment"
            raw_item["frascati_risk_level"] = assessment.get("risk_level")
            raw_item["frascati_interpretation"] = assessment.get("interpretation")
        semantic_text = build_lock_semantic_text(
            raw_item,
            max_supporting_passages=LOCK_CLUSTER_MAX_EVIDENCE_PASSAGES,
        ) or _clean(raw_item.get("analysis_text") or raw_item.get("text"))
        output.append(
            LockGroup(
                group_id=group_id,
                raw_item=raw_item,
                semantic_text=semantic_text,
                anchor_terms=_extract_anchor_terms(raw_item, semantic_text),
                anchor_phrases=_extract_anchor_phrases(raw_item, semantic_text),
                documents=_documents(raw_item),
                frascati_score=_score(raw_item),
                frascati_decision=_decision(raw_item) or "technical_group_unfiltered",
                is_measurement_support=_is_measurement_support(raw_item, semantic_text),
            )
        )
    return output


def _cosine_matrix(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    if not vectors:
        return np.zeros((0, 0), dtype=float)
    array = np.asarray(vectors, dtype=float)
    # encode_texts normalise déjà, mais on sécurise le contrat.
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    array = array / norms
    return np.clip(array @ array.T, -1.0, 1.0)


def _overlap(left: Set[str], right: Set[str]) -> Tuple[List[str], float]:
    if not left or not right:
        return [], 0.0
    shared = sorted(left & right)
    containment = len(shared) / max(1, min(len(left), len(right)))
    return shared, containment


def _pair_decision(left: LockGroup, right: LockGroup, similarity: float) -> PairDecision:
    shared_terms, term_containment = _overlap(left.anchor_terms, right.anchor_terms)
    shared_phrases, phrase_containment = _overlap(left.anchor_phrases, right.anchor_phrases)
    same_document = bool(left.documents & right.documents)
    measurement_relation = left.is_measurement_support != right.is_measurement_support

    strong_semantic = similarity >= LOCK_CLUSTER_STRONG_SIMILARITY
    normal_semantic = similarity >= LOCK_CLUSTER_MIN_SIMILARITY
    enough_anchors = bool(
        len(shared_phrases) >= 1
        or len(shared_terms) >= 2
        or term_containment >= 0.22
        or phrase_containment >= 0.18
    )
    very_strong_with_guard = strong_semantic and (
        enough_anchors or same_document or similarity >= min(0.90, LOCK_CLUSTER_STRONG_SIMILARITY + 0.12)
    )
    regular_merge = normal_semantic and enough_anchors

    # Deux limites instrumentales ne sont pas automatiquement fusionnées : elles
    # peuvent concerner des phénomènes distincts sur le même équipement.
    both_measurement = left.is_measurement_support and right.is_measurement_support
    merge_allowed = bool((very_strong_with_guard or regular_merge) and not both_measurement)

    if merge_allowed:
        reason = "semantic_and_discriminative_anchor_agreement"
    elif both_measurement:
        reason = "two_measurement_limits_kept_separate_until_attachment"
    elif not normal_semantic:
        reason = "semantic_similarity_below_threshold"
    else:
        reason = "same_equipment_or_generic_context_without_shared_anchor"

    return PairDecision(
        left_group_id=left.group_id,
        right_group_id=right.group_id,
        similarity=round(float(similarity), 6),
        shared_terms=shared_terms[:20],
        shared_phrases=shared_phrases[:20],
        same_document=same_document,
        merge_allowed=merge_allowed,
        measurement_relation=measurement_relation,
        reason=reason,
    )


def _pair_key(left_index: int, right_index: int) -> Tuple[int, int]:
    return (left_index, right_index) if left_index < right_index else (right_index, left_index)


def _cluster_pair_metrics(
    left_cluster: Sequence[int],
    right_cluster: Sequence[int],
    decisions: Mapping[Tuple[int, int], PairDecision],
) -> Tuple[bool, float, float, List[PairDecision]]:
    pairs: List[PairDecision] = []
    for left_index in left_cluster:
        for right_index in right_cluster:
            decision = decisions[_pair_key(left_index, right_index)]
            pairs.append(decision)
    if not pairs:
        return False, 0.0, 0.0, []

    minimum = min(pair.similarity for pair in pairs)
    mean = sum(pair.similarity for pair in pairs) / len(pairs)
    # Complete-linkage : chaque paire croisée doit être compatible. Cette règle
    # interdit A-B-C lorsque A et C parlent de phénomènes différents.
    allowed = all(pair.merge_allowed for pair in pairs) and minimum >= LOCK_CLUSTER_COMPLETE_LINK_MIN
    return allowed, minimum, mean, pairs


def _complete_linkage_clusters(
    core_indices: Sequence[int],
    decisions: Mapping[Tuple[int, int], PairDecision],
) -> List[List[int]]:
    clusters: List[List[int]] = [[index] for index in core_indices]
    while True:
        best: Optional[Tuple[float, float, int, int]] = None
        for left_pos in range(len(clusters)):
            for right_pos in range(left_pos + 1, len(clusters)):
                allowed, minimum, mean, _ = _cluster_pair_metrics(
                    clusters[left_pos], clusters[right_pos], decisions
                )
                if not allowed:
                    continue
                candidate = (minimum, mean, left_pos, right_pos)
                if best is None or candidate[:2] > best[:2]:
                    best = candidate
        if best is None:
            break
        _, _, left_pos, right_pos = best
        merged = sorted(set(clusters[left_pos]) | set(clusters[right_pos]))
        clusters[left_pos] = merged
        del clusters[right_pos]
    return clusters


def _meaningful_relation_terms(pair: PairDecision) -> List[str]:
    """Retourne uniquement les ancres réellement discriminantes d'une paire."""
    output: List[str] = []
    for value in pair.shared_terms:
        normalized = _norm(value)
        if not normalized:
            continue
        if normalized in WEAK_RELATION_TERMS or normalized in GENERIC_TERMS or normalized in STOPWORDS:
            continue
        if any(char.isdigit() for char in normalized):
            continue
        if len(normalized) < 4:
            continue
        output.append(normalized)
    return sorted(set(output))


def _meaningful_relation_phrases(pair: PairDecision) -> List[str]:
    """Écarte les phrases de fichier ou de contexte non causal."""
    output: List[str] = []
    for value in pair.shared_phrases:
        normalized = _norm(value)
        if not normalized or normalized in WEAK_RELATION_PHRASES:
            continue
        tokens = [token for token in normalized.split() if token]
        if not tokens:
            continue
        if any(any(char.isdigit() for char in token) for token in tokens):
            continue
        meaningful = [
            token for token in tokens
            if token not in WEAK_RELATION_TERMS
            and token not in GENERIC_TERMS
            and token not in STOPWORDS
        ]
        if not meaningful:
            continue
        output.append(normalized)
    return sorted(set(output))


def _measurement_pair_evidence(pair: PairDecision) -> Dict[str, Any]:
    """Évalue la relation entre une limite de mesure et un groupe cœur.

    La similarité vectorielle ne suffit jamais. Il faut une relation de mesure
    et au moins une ancre discriminante :
    - une phrase technique commune ;
    - deux termes techniques communs ;
    - ou le même document avec au moins un terme technique commun.
    """
    terms = _meaningful_relation_terms(pair)
    phrases = _meaningful_relation_phrases(pair)
    anchor_supported = bool(
        phrases
        or len(terms) >= 2
        or (pair.same_document and len(terms) >= 1)
    )
    similarity_supported = pair.similarity >= LOCK_CLUSTER_MEASUREMENT_ATTACH_MIN
    eligible = bool(
        pair.measurement_relation
        and similarity_supported
        and anchor_supported
    )

    # Le score de classement privilégie le lien causal/documentaire. Une forte
    # similarité sans ancre ne devient jamais candidate.
    relation_score = float(pair.similarity)
    if pair.same_document:
        relation_score += 0.10
    relation_score += min(len(terms), 3) * 0.03
    relation_score += min(len(phrases), 2) * 0.05

    return {
        "eligible": eligible,
        "relation_score": round(relation_score, 6),
        "similarity": pair.similarity,
        "same_document": pair.same_document,
        "meaningful_shared_terms": terms,
        "meaningful_shared_phrases": phrases,
        "reason": (
            "measurement_relation_with_discriminative_anchor"
            if eligible
            else (
                "not_a_measurement_relation"
                if not pair.measurement_relation
                else (
                    "measurement_similarity_below_threshold"
                    if not similarity_supported
                    else "measurement_relation_without_discriminative_anchor"
                )
            )
        ),
    }


def _cluster_measurement_candidate(
    measurement_index: int,
    cluster: Sequence[int],
    decisions: Mapping[Tuple[int, int], PairDecision],
) -> Optional[Dict[str, Any]]:
    pairs = [decisions[_pair_key(measurement_index, member)] for member in cluster]
    evidence = [_measurement_pair_evidence(pair) for pair in pairs]
    eligible_evidence = [item for item in evidence if item["eligible"]]
    if not eligible_evidence:
        return None

    best = max(
        eligible_evidence,
        key=lambda item: (item["relation_score"], item["similarity"]),
    )
    # Plusieurs relations cohérentes dans le même cluster renforcent le choix,
    # sans imposer qu'une limite de mesure soit similaire à tous les membres.
    cluster_score = float(best["relation_score"]) + 0.015 * max(0, len(eligible_evidence) - 1)
    return {
        "cluster_score": round(cluster_score, 6),
        "best_similarity": float(best["similarity"]),
        "eligible_pair_count": len(eligible_evidence),
        "best_evidence": best,
        "pair_evidence": evidence,
        "pair_group_ids": [
            [pair.left_group_id, pair.right_group_id] for pair in pairs
        ],
    }


def _attach_measurement_groups(
    clusters: List[List[int]],
    measurement_indices: Sequence[int],
    decisions: Mapping[Tuple[int, int], PairDecision],
) -> Tuple[List[List[int]], Dict[int, List[int]], List[List[int]], List[Dict[str, Any]]]:
    """Rattache prudemment les limites de mesure comme supports.

    Les indices de mesure ne sont plus ajoutés aux membres principaux du
    cluster. Ils sont conservés dans ``support_group_ids`` afin de ne jamais
    transformer une limite instrumentale en verrou autonome ou en thème central.
    """
    attached_supports: Dict[int, List[int]] = {}
    support_only: List[List[int]] = []
    attachment_audit: List[Dict[str, Any]] = []

    for measurement_index in measurement_indices:
        ranked: List[Tuple[float, int, Dict[str, Any]]] = []
        rejected_candidates: List[Dict[str, Any]] = []

        for cluster_pos, cluster in enumerate(clusters):
            candidate = _cluster_measurement_candidate(
                measurement_index, cluster, decisions
            )
            if candidate is None:
                pairs = [decisions[_pair_key(measurement_index, member)] for member in cluster]
                rejected_candidates.append({
                    "cluster_position": cluster_pos,
                    "pair_group_ids": [
                        [pair.left_group_id, pair.right_group_id] for pair in pairs
                    ],
                    "pair_evidence": [_measurement_pair_evidence(pair) for pair in pairs],
                    "reason": "no_eligible_measurement_relation",
                })
                continue
            ranked.append((candidate["cluster_score"], cluster_pos, candidate))

        ranked.sort(key=lambda value: value[0], reverse=True)
        best = ranked[0] if ranked else None
        second_score = ranked[1][0] if len(ranked) > 1 else -1.0

        attached = False
        target_cluster_position: Optional[int] = None
        audit_entry: Dict[str, Any] = {
            "measurement_index": measurement_index,
            "attached": False,
            "attachment_role": "supporting_measurement",
            "rejected_candidates": rejected_candidates,
        }

        if best is not None:
            best_score, cluster_pos, candidate = best
            margin = best_score - second_score if second_score >= 0 else best_score
            attached = bool(
                candidate["best_similarity"] >= LOCK_CLUSTER_MEASUREMENT_ATTACH_MIN
                and margin >= LOCK_CLUSTER_MEASUREMENT_MARGIN
            )
            target_cluster_position = cluster_pos
            audit_entry.update({
                "target_cluster_position": cluster_pos,
                "cluster_score": round(best_score, 6),
                "second_cluster_score": round(second_score, 6) if second_score >= 0 else None,
                "margin": round(margin, 6),
                "best_similarity": round(candidate["best_similarity"], 6),
                "eligible_pair_count": candidate["eligible_pair_count"],
                "best_evidence": candidate["best_evidence"],
                "pair_evidence": candidate["pair_evidence"],
                "pair_group_ids": candidate["pair_group_ids"],
                "attached": attached,
                "reason": (
                    "attached_as_measurement_support"
                    if attached
                    else "ambiguous_measurement_target_margin_too_small"
                ),
            })

        if attached and target_cluster_position is not None:
            attached_supports.setdefault(target_cluster_position, []).append(measurement_index)
        else:
            support_only.append([measurement_index])
            audit_entry.setdefault("reason", "no_safe_measurement_target")

        attachment_audit.append(audit_entry)

    return clusters, attached_supports, support_only, attachment_audit

def _supporting_passages(item: Mapping[str, Any]) -> List[Dict[str, Any]]:
    values = item.get("supporting_passages") or []
    output: List[Dict[str, Any]] = []
    if isinstance(values, list):
        for value in values:
            if isinstance(value, dict):
                output.append(dict(value))
            elif _clean(value):
                output.append({"text": _clean(value)})
    if not output and _clean(item.get("text")):
        output.append({
            "passage_id": _clean(item.get("passage_id")),
            "document": _clean(item.get("document")),
            "source_path": _clean(item.get("source_path")),
            "section_title": _clean(item.get("section_title")),
            "text": _clean(item.get("text")),
            "analysis_text": _clean(item.get("analysis_text")),
        })
    return output


def _supporting_documents(item: Mapping[str, Any]) -> List[Dict[str, Any]]:
    values = item.get("supporting_documents") or []
    output: List[Dict[str, Any]] = []
    if isinstance(values, list):
        for value in values:
            if isinstance(value, dict):
                output.append(dict(value))
            elif _clean(value):
                output.append({"document": _clean(value)})
    direct = _clean(item.get("document"))
    if direct and not any(_clean(entry.get("document")) == direct for entry in output):
        output.append({
            "document": direct,
            "source_path": _clean(item.get("source_path")),
            "passage_count": max(1, len(_supporting_passages(item))),
        })
    return output


def _dedupe_dicts(values: Iterable[Dict[str, Any]], keys: Sequence[str]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, ...]] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        signature = tuple(_clean(value.get(key)) for key in keys)
        if not any(signature):
            signature = (_norm(json.dumps(value, ensure_ascii=False, sort_keys=True))[:500],)
        if signature in seen:
            continue
        seen.add(signature)
        output.append(value)
    return output


def _merge_profiles(groups: Sequence[LockGroup]) -> Dict[str, Any]:
    profile: Dict[str, Any] = {}
    for key, limit in (("top_terms", 30), ("top_phrases", 24), ("technical_entities", 20), ("semantic_roles", 12)):
        values: List[Any] = []
        for group in groups:
            raw_values = _profile(group.raw_item).get(key) or []
            if isinstance(raw_values, list):
                values.extend(raw_values)
        profile[key] = _unique_texts(values, max_items=limit)
    profile["documents_count"] = len(set().union(*(group.documents for group in groups))) if groups else 0
    profile["groups_count"] = len(groups)
    return profile



def _iter_group_roles(group: LockGroup) -> List[str]:
    values: List[Any] = []
    profile = _profile(group.raw_item)
    values.extend(profile.get("semantic_roles") or [])
    values.extend(group.raw_item.get("source_semantic_roles") or [])
    values.append(group.raw_item.get("semantic_role"))
    values.append(group.raw_item.get("original_model_role"))
    return [_norm(value) for value in values if _norm(value)]


def _group_feature_counts(group: LockGroup) -> Dict[str, int]:
    raw = group.raw_item
    candidates = [
        raw.get("frascati_features"),
        raw.get("frascati_group_features"),
        raw.get("lock_candidate_features"),
    ]
    output: Dict[str, int] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key, value in candidate.items():
            if isinstance(value, bool):
                output[key] = output.get(key, 0) + int(value)
            elif isinstance(value, (int, float)):
                output[key] = output.get(key, 0) + int(max(0, value))
    return output


def _cluster_role_decision(
    members: Sequence[LockGroup],
    supports: Sequence[LockGroup],
) -> Dict[str, Any]:
    """Classe le rôle du cluster à partir de sa fonction probante.

    Cette classification ne dépend d'aucun nom de projet, de domaine ou de
    cluster. Elle utilise uniquement le texte source, les rôles NLP et les
    caractéristiques Frascati déjà calculées.
    """
    if (not members and supports) or (members and all(group.is_measurement_support for group in members)):
        return {
            "cluster_role": "limite_de_mesure",
            "display_as_lock": False,
            "lock_scope": "supporting_measurement",
            "confidence": 1.0,
            "reasons": ["cluster composé uniquement de limites de mesure"],
        }

    technical_scopes = {
        _clean(group.raw_item.get("technical_scope"))
        for group in members
        if _clean(group.raw_item.get("technical_scope"))
    }
    if technical_scopes and technical_scopes.issubset({"local_technical_subproblem"}):
        return {
            "cluster_role": "sous_probleme_technique_local",
            "display_as_lock": False,
            "lock_scope": "local_technical_subproblem",
            "confidence": 0.92,
            "reasons": ["les preuves décrivent une anomalie locale sans portée interdocument démontrée"],
            "signals": {"technical_scopes": sorted(technical_scopes)},
        }

    if not LOCK_CLUSTER_ROLE_CLASSIFICATION_ENABLED:
        return {
            "cluster_role": "verrou_a_verifier",
            "display_as_lock": True,
            "lock_scope": "principal" if len(members) >= 2 else "secondary",
            "confidence": 0.5,
            "reasons": ["classification de rôle désactivée"],
        }

    source_parts: List[str] = []
    roles: List[str] = []
    feature_totals: Counter[str] = Counter()
    for group in members:
        source_parts.extend([
            group.raw_item.get("candidate_group_label"),
            group.raw_item.get("section_title"),
            group.raw_item.get("analysis_text"),
            group.raw_item.get("text"),
            group.semantic_text,
        ])
        roles.extend(_iter_group_roles(group))
        feature_totals.update(_group_feature_counts(group))

    text = _norm(" ".join(_clean(part) for part in source_parts if _clean(part)))
    role_set = set(roles)
    hypothesis_hits = sorted({m for m in METHODOLOGICAL_HYPOTHESIS_MARKERS if _norm(m) in text})
    result_hits = sorted({m for m in RESULT_ONLY_MARKERS if _norm(m) in text})
    open_text_hits = sorted({m for m in OPEN_PROBLEM_MARKERS if _norm(m) in text})

    causal_count = (
        feature_totals.get("causal_gap_count", 0)
        + feature_totals.get("causal_gap", 0)
        + feature_totals.get("tradeoff_count", 0)
        + feature_totals.get("tradeoff", 0)
        + feature_totals.get("dependency_count", 0)
        + feature_totals.get("dependency", 0)
        + feature_totals.get("open_validation_count", 0)
        + feature_totals.get("open_validation", 0)
    )
    explicit_count = (
        feature_totals.get("explicit_count", 0)
        + feature_totals.get("explicit_lock_signal", 0)
        + feature_totals.get("explicit_section_count", 0)
        + feature_totals.get("explicit_section_lock_signal", 0)
    )
    uncertainty_count = (
        feature_totals.get("uncertainty_count", 0)
        + feature_totals.get("uncertainty_signal_count", 0)
        + feature_totals.get("uncertainty", 0)
    )
    evidence_count = sum(
        max(1, int(_safe_float(group.raw_item.get("evidence_count"), 1)))
        for group in members
    )
    score = sum(group.frascati_score for group in members) / max(1, len(members))
    method_parameter_only = bool(role_set) and role_set.issubset({"methode", "parametre", "mixed"})

    # Une hypothèse de calcul n'est pas affichée comme verrou si elle ne porte
    # pas simultanément un problème causal ou une validation ouverte forte.
    if (
        len(hypothesis_hits) >= 2
        and method_parameter_only
        and causal_count == 0
        and explicit_count == 0
        and not open_text_hits
    ):
        return {
            "cluster_role": "hypothese_methodologique",
            "display_as_lock": False,
            "lock_scope": "methodological_hypothesis",
            "confidence": round(min(0.98, 0.72 + 0.05 * len(hypothesis_hits)), 4),
            "reasons": [
                "le contenu décrit principalement une hypothèse ou simplification de modèle",
                "aucun gap causal ou besoin de validation ouverte n'est démontré",
            ],
            "signals": {"hypothesis_markers": hypothesis_hits, "semantic_roles": sorted(role_set)},
        }

    # Un résultat seul, sans inconnue ni validation ouverte, reste un résultat.
    if (
        result_hits
        and causal_count == 0
        and explicit_count == 0
        and uncertainty_count == 0
        and not open_text_hits
        and role_set.issubset({"resultat", "parametre", "contribution", "mixed"})
    ):
        return {
            "cluster_role": "resultat_technique",
            "display_as_lock": False,
            "lock_scope": "technical_result",
            "confidence": 0.78,
            "reasons": ["le cluster rapporte un résultat sans inconnue résiduelle démontrée"],
            "signals": {"result_markers": result_hits, "semantic_roles": sorted(role_set)},
        }

    strong_lock = bool(
        explicit_count > 0
        or causal_count >= 2
        or open_text_hits
        or (len(members) >= 2 and evidence_count >= 2 and score >= 0.50)
    )
    role = "verrou_scientifique" if strong_lock else "verrou_a_verifier"
    confidence = 0.82 if strong_lock else 0.64
    reasons = [
        "présence d'une inconnue, d'un lien causal ouvert ou d'un besoin de validation"
        if strong_lock
        else "signal technique qualifié en amont mais encore insuffisant pour une conclusion définitive"
    ]
    return {
        "cluster_role": role,
        "display_as_lock": True,
        "lock_scope": "principal" if len(members) >= 2 else "secondary",
        "confidence": confidence,
        "reasons": reasons,
        "signals": {
            "semantic_roles": sorted(role_set),
            "causal_or_open_count": causal_count,
            "uncertainty_count": uncertainty_count,
            "explicit_count": explicit_count,
            "evidence_count": evidence_count,
            "frascati_score_mean": round(score, 4),
        },
    }


def _annotate_related_clusters(
    payload_clusters: List[Dict[str, Any]],
    groups: Sequence[LockGroup],
    decisions: Mapping[Tuple[int, int], PairDecision],
) -> List[Dict[str, Any]]:
    """Relie des axes proches sans modifier leur composition.

    Une relation n'est jamais une fusion. Elle sert uniquement à indiquer au
    consultant que deux axes partagent des preuves ou un mécanisme potentiel.
    """
    group_index = {group.group_id: index for index, group in enumerate(groups)}
    for cluster in payload_clusters:
        cluster.setdefault("related_cluster_ids", [])
        cluster.setdefault("related_cluster_evidence", [])

    for left_pos in range(len(payload_clusters)):
        left = payload_clusters[left_pos]
        if not left.get("display_as_lock"):
            continue
        for right_pos in range(left_pos + 1, len(payload_clusters)):
            right = payload_clusters[right_pos]
            if not right.get("display_as_lock"):
                continue
            pair_candidates: List[Tuple[float, PairDecision, List[str], List[str]]] = []
            for left_id in left.get("member_group_ids") or []:
                for right_id in right.get("member_group_ids") or []:
                    if left_id not in group_index or right_id not in group_index:
                        continue
                    pair = decisions.get(_pair_key(group_index[left_id], group_index[right_id]))
                    if pair is None:
                        continue
                    terms = _meaningful_relation_terms(pair)
                    phrases = _meaningful_relation_phrases(pair)
                    if pair.similarity < LOCK_CLUSTER_RELATED_MIN_SIMILARITY:
                        continue
                    if not phrases and len(terms) < 2:
                        continue
                    pair_candidates.append((pair.similarity, pair, terms, phrases))
            if not pair_candidates:
                continue
            similarity, pair, terms, phrases = max(pair_candidates, key=lambda item: item[0])
            evidence = {
                "left_cluster_id": left.get("cluster_id"),
                "right_cluster_id": right.get("cluster_id"),
                "similarity": round(float(similarity), 6),
                "shared_terms": terms[:12],
                "shared_phrases": phrases[:8],
                "relation_type": "related_without_merge",
            }
            left["related_cluster_ids"].append(right.get("cluster_id"))
            right["related_cluster_ids"].append(left.get("cluster_id"))
            left["related_cluster_evidence"].append(evidence)
            right["related_cluster_evidence"].append(evidence)

    for cluster in payload_clusters:
        cluster["related_cluster_ids"] = sorted(set(cluster.get("related_cluster_ids") or []))
    return payload_clusters


def _cluster_payload(
    cluster_id: str,
    member_indices: Sequence[int],
    groups: Sequence[LockGroup],
    matrix: np.ndarray,
    display_as_lock: bool,
    lock_scope: str,
    support_indices: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    support_indices = list(support_indices or [])
    members = [groups[index] for index in member_indices]
    supports = [groups[index] for index in support_indices]
    evidence_groups = members + supports

    group_ids = [member.group_id for member in members]
    support_group_ids = [support.group_id for support in supports]
    passages = _dedupe_dicts(
        (
            passage
            for group in evidence_groups
            for passage in _supporting_passages(group.raw_item)
        ),
        keys=("source_path", "document", "passage_id", "text"),
    )
    documents = _dedupe_dicts(
        (
            document
            for group in evidence_groups
            for document in _supporting_documents(group.raw_item)
        ),
        keys=("source_path", "document"),
    )

    pair_similarities: List[float] = []
    for left_pos in range(len(member_indices)):
        for right_pos in range(left_pos + 1, len(member_indices)):
            pair_similarities.append(
                float(matrix[member_indices[left_pos], member_indices[right_pos]])
            )

    # Le score et le représentant restent fondés sur les groupes cœur. Une
    # limite de mesure associée ne doit pas devenir le sujet principal.
    scores = [member.frascati_score for member in members if member.frascati_score > 0]
    labels = _unique_texts(
        member.raw_item.get("candidate_group_label")
        or member.raw_item.get("section_title")
        or member.raw_item.get("text")
        for member in members
    )
    representative = max(
        members,
        key=lambda member: (member.frascati_score, len(member.semantic_text)),
    )
    semantic_texts = _unique_texts(member.semantic_text for member in members)
    support_texts = _unique_texts(support.semantic_text for support in supports)
    role_decision = _cluster_role_decision(members, supports)
    final_display = bool(display_as_lock and role_decision.get("display_as_lock", True))
    final_scope = _clean(role_decision.get("lock_scope") or lock_scope)
    if final_display and role_decision.get("cluster_role") in LOCK_CLUSTER_ROLES:
        declared_scopes = {
            _clean(member.raw_item.get("technical_scope") or member.raw_item.get("lock_scope"))
            for member in members
            if _clean(member.raw_item.get("technical_scope") or member.raw_item.get("lock_scope"))
        }
        declared_main = any(
            _safe_bool(member.raw_item.get("display_as_main_lock"))
            for member in members
        ) or bool(declared_scopes & {"project_structuring_lock", "principal", "main_lock"})
        declared_local = bool(
            declared_scopes
            & {"local_technical_subproblem", "secondary", "supporting_measurement"}
        )
        if declared_main:
            final_scope = "project_structuring_lock"
        elif declared_local:
            final_scope = "local_technical_subproblem"

    return {
        "cluster_id": cluster_id,
        "member_group_ids": group_ids,
        "support_group_ids": support_group_ids,
        "display_as_lock": final_display,
        "lock_scope": final_scope,
        "cluster_role": role_decision.get("cluster_role", "verrou_a_verifier"),
        "cluster_role_confidence": role_decision.get("confidence", 0.5),
        "cluster_role_reasons": role_decision.get("reasons", []),
        "cluster_role_signals": role_decision.get("signals", {}),
        "related_cluster_ids": [],
        "related_cluster_evidence": [],
        "group_count": len(members),
        "support_group_count": len(supports),
        "total_group_count": len(evidence_groups),
        "representative_group_id": representative.group_id,
        "representative_label": labels[0] if labels else representative.group_id,
        "representative_text": _clean(
            representative.raw_item.get("analysis_text")
            or representative.raw_item.get("text")
        ),
        # Le texte principal ne contient que les groupes cœur. Les preuves des
        # supports restent néanmoins transmises séparément au reformulateur.
        "semantic_text": "\n\n".join(semantic_texts),
        "measurement_support_text": "\n\n".join(support_texts),
        "concept_profile": _merge_profiles(members),
        "support_concept_profile": _merge_profiles(supports),
        "frascati_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "frascati_decisions": _unique_texts(
            member.frascati_decision for member in members
        ),
        "semantic_similarity_min": (
            round(min(pair_similarities), 6) if pair_similarities else 1.0
        ),
        "semantic_similarity_mean": (
            round(sum(pair_similarities) / len(pair_similarities), 6)
            if pair_similarities else 1.0
        ),
        "supporting_documents": documents,
        "supporting_passages": passages,
        "member_groups": [member.raw_item for member in members],
        "support_member_groups": [support.raw_item for support in supports],
        "needs_human_validation": True,
        "not_final_cir": True,
    }

def _singleton_report(groups: Sequence[LockGroup], error: Optional[str] = None) -> Dict[str, Any]:
    clusters = []
    for index, group in enumerate(groups, start=1):
        clusters.append(_cluster_payload(
            cluster_id=f"VC{index:03d}",
            member_indices=[index - 1],
            groups=groups,
            matrix=np.eye(len(groups), dtype=float),
            display_as_lock=not group.is_measurement_support,
            lock_scope="supporting_measurement" if group.is_measurement_support else "secondary",
        ))
    return {
        "ok": error is None,
        "version": VERSION,
        "mode": "singleton_safe_fallback" if error else "singleton_no_merge",
        "embedding_model": EMBEDDING_MODEL_NAME,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "groups_count": len(groups),
        "display_clusters_count": sum(1 for cluster in clusters if cluster["display_as_lock"]),
        "support_only_clusters_count": sum(1 for cluster in clusters if not cluster["display_as_lock"]),
        "clusters": clusters,
        "pairwise_decisions": [],
        "cluster_role_counts": dict(Counter(cluster.get("cluster_role") for cluster in clusters)),
        "coverage": {
            "input_group_ids": [group.group_id for group in groups],
            "covered_group_ids": [group.group_id for group in groups],
            "uncovered_group_ids": [],
            "coverage_rate": 1.0,
        },
        "error": error,
    }


def consolidate_lock_groups(
    nlp_result: Dict[str, Any],
    embedding_vectors: Optional[Sequence[Sequence[float]]] = None,
) -> Dict[str, Any]:
    groups = extract_lock_groups(nlp_result)
    if not groups:
        return {
            "ok": True,
            "version": VERSION,
            "mode": "no_qualified_lock_group",
            "embedding_model": EMBEDDING_MODEL_NAME,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "groups_count": 0,
            "display_clusters_count": 0,
            "support_only_clusters_count": 0,
            "clusters": [],
            "pairwise_decisions": [],
            "coverage": {"input_group_ids": [], "covered_group_ids": [], "uncovered_group_ids": []},
        }

    if not LOCK_CLUSTER_ENABLED:
        return _singleton_report(groups)

    try:
        if embedding_vectors is not None:
            vectors = list(embedding_vectors)
        else:
            # Import tardif : le module reste testable même si Chroma n'est pas
            # chargé dans un environnement d'analyse. En production, le RAG
            # possède déjà cette dépendance.
            from .vector_store import encode_texts
            vectors = encode_texts([group.semantic_text for group in groups])
        if len(vectors) != len(groups):
            raise ValueError(
                f"Nombre de vecteurs incohérent: {len(vectors)} pour {len(groups)} groupes."
            )
        matrix = _cosine_matrix(vectors)
    except Exception as exc:
        return _singleton_report(groups, error=f"embedding_error: {exc}")

    decisions: Dict[Tuple[int, int], PairDecision] = {}
    pairwise_payload: List[Dict[str, Any]] = []
    for left_index in range(len(groups)):
        for right_index in range(left_index + 1, len(groups)):
            decision = _pair_decision(
                groups[left_index],
                groups[right_index],
                float(matrix[left_index, right_index]),
            )
            decisions[(left_index, right_index)] = decision
            pairwise_payload.append({
                "left_group_id": decision.left_group_id,
                "right_group_id": decision.right_group_id,
                "similarity": decision.similarity,
                "shared_terms": decision.shared_terms,
                "shared_phrases": decision.shared_phrases,
                "same_document": decision.same_document,
                "merge_allowed": decision.merge_allowed,
                "measurement_relation": decision.measurement_relation,
                "reason": decision.reason,
            })

    core_indices = [index for index, group in enumerate(groups) if not group.is_measurement_support]
    measurement_indices = [index for index, group in enumerate(groups) if group.is_measurement_support]

    clusters = _complete_linkage_clusters(core_indices, decisions) if core_indices else []
    clusters, attached_supports, support_only, attachment_audit = _attach_measurement_groups(
        clusters,
        measurement_indices,
        decisions,
    )

    payload_clusters: List[Dict[str, Any]] = []
    counter = 1
    for cluster_pos, cluster in enumerate(clusters):
        payload_clusters.append(_cluster_payload(
            cluster_id=f"VC{counter:03d}",
            member_indices=cluster,
            support_indices=attached_supports.get(cluster_pos, []),
            groups=groups,
            matrix=matrix,
            display_as_lock=True,
            lock_scope="principal" if len(cluster) >= 2 else "secondary",
        ))
        counter += 1

    for cluster in support_only:
        payload_clusters.append(_cluster_payload(
            cluster_id=f"VC{counter:03d}",
            member_indices=cluster,
            groups=groups,
            matrix=matrix,
            display_as_lock=False,
            lock_scope="supporting_measurement",
        ))
        counter += 1

    payload_clusters = _annotate_related_clusters(payload_clusters, groups, decisions)

    covered = [
        group_id
        for cluster in payload_clusters
        for group_id in (
            list(cluster.get("member_group_ids", []))
            + list(cluster.get("support_group_ids", []))
        )
    ]
    input_ids = [group.group_id for group in groups]
    uncovered = [group_id for group_id in input_ids if group_id not in set(covered)]

    # Invariant de sécurité absolu : toute couverture incomplète retombe sur des
    # singletons, jamais sur une fusion partielle destructrice.
    if uncovered or len(covered) != len(set(covered)):
        return _singleton_report(
            groups,
            error=(
                "coverage_invariant_failed: "
                f"uncovered={uncovered}, covered={len(covered)}, unique={len(set(covered))}"
            ),
        )

    return {
        "ok": True,
        "version": VERSION,
        "mode": "semantic_complete_linkage_with_generic_cluster_roles",
        "embedding_model": EMBEDDING_MODEL_NAME,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "thresholds": {
            "minimum_similarity": LOCK_CLUSTER_MIN_SIMILARITY,
            "strong_similarity": LOCK_CLUSTER_STRONG_SIMILARITY,
            "complete_link_minimum": LOCK_CLUSTER_COMPLETE_LINK_MIN,
            "measurement_attach_minimum": LOCK_CLUSTER_MEASUREMENT_ATTACH_MIN,
            "measurement_margin": LOCK_CLUSTER_MEASUREMENT_MARGIN,
            "related_minimum_similarity": LOCK_CLUSTER_RELATED_MIN_SIMILARITY,
        },
        "groups_count": len(groups),
        "core_groups_count": len(core_indices),
        "measurement_groups_count": len(measurement_indices),
        "display_clusters_count": sum(1 for cluster in payload_clusters if cluster["display_as_lock"]),
        "support_only_clusters_count": sum(1 for cluster in payload_clusters if not cluster["display_as_lock"]),
        "clusters": payload_clusters,
        "pairwise_decisions": pairwise_payload,
        "measurement_attachment_audit": attachment_audit,
        "cluster_role_counts": dict(Counter(cluster.get("cluster_role") for cluster in payload_clusters)),
        "coverage": {
            "input_group_ids": input_ids,
            "covered_group_ids": covered,
            "uncovered_group_ids": [],
            "coverage_rate": 1.0,
        },
        "principle": (
            "Les embeddings proposent des proximités ; la fusion exige aussi des ancres "
            "discriminantes et un accord complete-linkage entre chaque paire. Une limite "
            "de mesure est rattachée uniquement comme support lorsqu'une relation "
            "documentaire ou technique discriminante est démontrée. Le rôle visible "
            "du cluster est classé génériquement à partir de la fonction des preuves."
        ),
    }


def save_lock_clusters(
    nlp_result: Dict[str, Any],
    output_path: str | Path,
    embedding_vectors: Optional[Sequence[Sequence[float]]] = None,
) -> Dict[str, Any]:
    report = consolidate_lock_groups(nlp_result, embedding_vectors=embedding_vectors)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_path"] = str(path)
    return report
