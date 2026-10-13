# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .cleaner import is_noise_line
from .document_structure_mapper import context_prefix
from .normalizer import normalize_text


SENT_SPLIT = re.compile(r"(?<=[.!?;:])\s+|\n+")


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").lower()).strip("_")
    return value[:55] or "doc"


def split_sentences(text: str) -> List[str]:
    # Conserver les sauts de ligne : ils séparent souvent un titre de section
    # de son premier paragraphe. ``normalize_text`` appliqué au document entier
    # les supprimait et pouvait fusionner « Related works » avec une méthode,
    # ce qui faisait perdre le rôle ``etat_art``.
    raw_text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(normalize_text(line) for line in raw_text.split("\n"))
    out: List[str] = []
    for raw in SENT_SPLIT.split(text):
        sentence = str(raw or "").strip(" -|")
        if len(sentence) < 25 or is_noise_line(sentence):
            continue
        out.append(sentence)
    return out


def is_good_candidate(text: str) -> bool:
    if not text or len(text) < 45 or len(text) > 1500:
        return False
    low = text.lower()
    if any(marker in low for marker in ("tapez ici", "nom de la présentation", "document security", "charte graphique")):
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ]{3,}", text)
    if len(words) < 6:
        return False
    return len({word.lower() for word in words}) / max(len(words), 1) >= 0.30


def _candidate_key(text: str) -> str:
    return hashlib.sha1(re.sub(r"\W+", "", text.lower()).encode("utf-8")).hexdigest()


def _passage_id(doc: Dict[str, Any], text: str) -> str:
    doc_key = str(doc.get("source_path") or doc.get("document") or "doc")
    digest = hashlib.sha1(f"{doc_key}|{text}".encode("utf-8")).hexdigest()[:16]
    return f"{_slug(doc.get('document', 'doc'))}_{digest}"


def _section_content(section: Dict[str, Any]) -> str:
    content = section.get("content")
    if content:
        return normalize_text(str(content))
    return normalize_text(" ".join(str(x) for x in section.get("blocks", []) if x))


def _find_section(text: str, doc_sections: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str], List[str]]:
    normalized = normalize_text(text)
    best: Optional[Dict[str, Any]] = None
    best_overlap = 0
    text_tokens = set(re.findall(r"[a-zà-ÿ0-9]+", normalized.lower()))

    for section in doc_sections or []:
        content = _section_content(section)
        if not content:
            continue
        if normalized in content or content in normalized:
            best = section
            break
        section_tokens = set(re.findall(r"[a-zà-ÿ0-9]+", content.lower()))
        overlap = len(text_tokens & section_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best = section

    if not best or best_overlap < 4 and normalized not in _section_content(best):
        return None, None, []
    return (
        best.get("section_title") or best.get("title"),
        best.get("section_role_hint") or best.get("role_hint"),
        list(best.get("section_path") or []),
    )


def _windows_for_doc(sentence_count: int, windows) -> List[int]:
    if sentence_count < 12:
        return [w for w in (1, 2, 3) if w <= max(sentence_count, 1)]
    return [int(w) for w in windows if int(w) > 0]


def _build_doc_candidates(
    doc: Dict[str, Any],
    structure: Dict[str, Any],
    windows,
) -> List[Dict[str, Any]]:
    sentences = split_sentences(doc.get("text", ""))
    sections = structure.get("sections", []) if isinstance(structure, dict) else []
    sentence_locations = [_find_section(sentence, sections) for sentence in sentences]
    structure_type = structure.get("structure_type") or structure.get("document_type") if isinstance(structure, dict) else None
    document_weight = float(doc.get("document_weight") or doc.get("source_weight") or 0.55)
    seen = set()
    out: List[Dict[str, Any]] = []

    for window in _windows_for_doc(len(sentences), windows):
        for index in range(0, max(0, len(sentences) - window + 1)):
            locations = sentence_locations[index:index + window]
            location_titles = {location[0] for location in locations if location[0]}

            # Un passage ne doit pas mélanger deux sections adjacentes. Sans
            # cette garde, la dernière phrase de « Related works » pouvait être
            # concaténée à la première phrase de « Methodology » et devenir un
            # faux verrou du projet.
            if len(location_titles) > 1:
                continue

            text = " ".join(sentences[index:index + window]).strip()
            if not is_good_candidate(text):
                continue
            key = _candidate_key(text)
            if key in seen:
                continue
            seen.add(key)
            if len(location_titles) == 1:
                title = next(iter(location_titles))
                location = next(location for location in locations if location[0] == title)
                _, hint, path = location
            else:
                title, hint, path = _find_section(text, sections)
            item = {
                "passage_id": _passage_id(doc, text),
                "document": doc.get("document"),
                "source_path": doc.get("source_path"),
                "source_type": doc.get("source_type") or "raw",
                "content_origin": doc.get("content_origin", "unknown"),
                "source_weight": float(doc.get("source_weight") or document_weight),
                "text": text,
                "window_size": window,
                "sentence_start": index,
                "document_type": doc.get("document_type") or "unknown_document",
                "source_policy": doc.get("source_policy") or "secondary",
                "document_weight": document_weight,
                "document_type_confidence": doc.get("document_type_confidence"),
                "declared_document_type": doc.get("declared_document_type"),
                "declared_corpus": doc.get("declared_corpus"),
                "declared_mode": doc.get("declared_mode"),
                "current_project_evidence": bool(doc.get("current_project_evidence")),
                "declared_raw_document": bool(doc.get("declared_raw_document")),
                "structure_type": structure_type,
                "section_title": title,
                "section_path": path,
                "section_role_hint": hint or "unknown",
                # Contexte local conservé séparément : le passage source reste
                # inchangé, mais le filtre et Frascati peuvent comprendre une
                # phrase dont l'incertitude est formulée juste avant/après.
                "context_before": sentences[index - 1] if index > 0 else "",
                "context_after": sentences[index + window] if index + window < len(sentences) else "",
            }
            prefix = context_prefix(item)
            local_context = " ".join(
                part for part in (item.get("context_before"), text, item.get("context_after")) if part
            )
            item["analysis_text"] = local_context
            item["model_input"] = f"{prefix}\nContexte local: {local_context}\nPassage: {text}".strip()
            out.append(item)
    return out


def _coverage_order(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ordre dont chaque préfixe couvre le début, le milieu et la fin.

    L'ancienne sélection prenait les premiers candidats d'un document. Quand
    ``max_candidates`` était atteint, les sections finales pouvaient ne jamais
    être classifiées. Cet ordre divise récursivement les intervalles ; une
    limite de coût ne devient donc plus une coupure du document.
    """
    ordered = sorted(
        items,
        key=lambda item: (
            int(item.get("sentence_start") or 0),
            int(item.get("window_size") or 0),
        ),
    )
    size = len(ordered)
    if size <= 2:
        return ordered

    indices = [0, size - 1]
    intervals = [(0, size - 1)]
    seen = set(indices)
    while intervals:
        left, right = intervals.pop(0)
        middle = (left + right) // 2
        if middle not in seen:
            indices.append(middle)
            seen.add(middle)
        if middle - left > 1:
            intervals.append((left, middle))
        if right - middle > 1:
            intervals.append((middle, right))

    # Sécurité pour les petits intervalles et arrondis.
    indices.extend(index for index in range(size) if index not in seen)
    return [ordered[index] for index in indices]


def make_candidates(
    documents: List[Dict[str, Any]],
    max_candidates: int = 700,
    windows=(2, 3, 4),
    section_info: Optional[List[Dict[str, Any]]] = None,
    min_candidates_per_doc: int = 8,
    max_candidates_per_doc: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Construit une liste équilibrée sans dédupliquer entre deux documents.

    La déduplication inter-document de l'ancienne version supprimait parfois la
    deuxième preuve d'un même fait. Le parcours round-robin garantit la
    couverture documentaire ; ``max_candidates`` reste uniquement une limite
    de coût du classifieur, jamais une limite du nombre final de verrous.
    """
    documents = documents or []
    if not documents or max_candidates <= 0:
        return []

    buckets: List[List[Dict[str, Any]]] = []
    for index, doc in enumerate(documents):
        structure = section_info[index] if section_info and index < len(section_info) else {}
        bucket = _coverage_order(_build_doc_candidates(doc, structure, windows))
        if max_candidates_per_doc is not None:
            bucket = bucket[: max(0, int(max_candidates_per_doc))]
        buckets.append(bucket)

    selected: List[Dict[str, Any]] = []
    cursors = [0 for _ in buckets]

    # Premier passage : couverture minimale par document.
    for _ in range(max(0, int(min_candidates_per_doc))):
        for bucket_index, bucket in enumerate(buckets):
            cursor = cursors[bucket_index]
            if cursor < len(bucket) and len(selected) < max_candidates:
                selected.append(bucket[cursor])
                cursors[bucket_index] += 1

    # Redistribution équitable de toutes les places restantes.
    progressed = True
    while len(selected) < max_candidates and progressed:
        progressed = False
        for bucket_index, bucket in enumerate(buckets):
            cursor = cursors[bucket_index]
            if cursor >= len(bucket):
                continue
            selected.append(bucket[cursor])
            cursors[bucket_index] += 1
            progressed = True
            if len(selected) >= max_candidates:
                break

    return selected


# ============================================================================
# Extension V172 : candidat direct + preuve de support
# L'API historique make_candidates reste intégralement disponible.
# ============================================================================

VERSION = "lock_candidates_v185_fastjudge_signal_project_seed_gate"

SUPPORTING_ROLES = {
    "objectif", "methode", "parametre", "resultat", "limite",
    "contribution", "mixed", "verrou",
}
STRONG_DIRECT_FEATURES = ("uncertainty", "causal_gap", "tradeoff", "knowledge_gap")
STRUCTURING_FEATURES = ("open_validation", "tradeoff", "knowledge_gap")
SEED_FRIENDLY_ROLES = {"verrou", "limite", "objectif", "contribution", "mixed"}
METHOD_PARAMETER_ROLES = {"parametre", "methode"}
DESIGN_SUPPORT_TYPES = {"plan_schema", "conception_technique", "unknown_document"}
SUPPORT_FEATURES = ("measurement_limit", "dependency", "technical", "open_validation")
UNRESOLVED_PATTERNS = (
    r"\b(?:reste|restent)\s+(?:a|à)\s+(?:determiner|déterminer|comprendre|valider|verifier|vérifier|etablir|établir)\b",
    r"\b(?:doit|doivent|devra|devront)\s+(?:etre|être\s+)?(?:determine|déterminé|determines|déterminés|valide|validé|verifie|vérifié|etudie|étudié)\b",
    r"\b(?:essais?|analyses?|investigations?)\s+compl[eé]mentaires?\b",
    r"\b(?:essais?|analyses?|investigations?)\s+compl[eé]mentaires?.{0,120}\b(?:determiner|déterminer|valider|verifier|vérifier|comprendre|etablir|établir)\b",
    r"\b(?:cause|origine|mecanisme|mécanisme)\s+(?:reste|demeure)\s+(?:inconnue|inconnu|indeterminee|indéterminée)\b",
    r"\b(?:impossible|non\s+possible)\s+(?:a|à)\s+(?:predire|prédire|determiner|déterminer)\b",
    r"\b(?:non|pas)\s+r[ée]alisable\b",
    r"\b(?:ne peut|ne peuvent)\s+(?:pas\s+)?(?:etre|être\s+)?(?:garanti|garantie|pr[ée]dit|pr[ée]dite|d[ée]termin[ée])\b",
)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value if value not in (None, "") else default)
        return default if math.isnan(number) or math.isinf(number) else number
    except Exception:
        return default


def _text(item: Mapping[str, Any]) -> str:
    # Les noms de fichiers sont des métadonnées, jamais des preuves. Les
    # inclure ici créait de faux verrous à partir de plans et de schémas.
    return " ".join(str(item.get(key) or "").strip() for key in (
        "section_title", "analysis_text", "text",
        "context_before", "context_after"
    ) if item.get(key)).strip()


def _role(item: Mapping[str, Any]) -> str:
    return str(
        item.get("semantic_role")
        or item.get("original_model_role")
        or item.get("role")
        or ""
    ).strip().lower()


def _has_explicit_unresolved_language(text: str) -> bool:
    normalized = str(text or "").lower()
    return any(re.search(pattern, normalized, flags=re.I) for pattern in UNRESOLVED_PATTERNS)


@dataclass(frozen=True)
class CandidateDecision:
    direct_lock_candidate: bool
    supporting_lock_evidence: bool
    direct_score: float
    evidence_score: float
    semantic_role: str
    active_features: List[str]
    reason: str
    seed_reason: str
    version: str = VERSION


def classify_candidate(
    item: Mapping[str, Any],
    *,
    direct_threshold: float = 0.60,
    evidence_threshold: float = 0.38,
) -> CandidateDecision:
    """Sépare une graine de verrou des preuves de support.

    Toute preuve qui porte ``project_lock_seed`` peut créer une graine, quel que
    soit son rôle sémantique. Ce marqueur est posé en amont uniquement quand le
    passage exprime un problème technique encore ouvert ; FastJudge reste
    traçable, mais n'est plus l'unique porte d'entrée.

    ``direct_threshold`` est conservé dans la signature pour compatibilité API,
    mais il n'est plus utilisé pour filtrer une prédiction FastJudge ``verrou``.
    """
    features = item.get("lock_candidate_features") or {}
    if not isinstance(features, dict):
        features = {}

    role = _role(item)
    original_role = str(item.get("original_model_role") or item.get("role") or "").strip().lower()
    text = _text(item)
    model_score = _float(
        item.get("lock_candidate_score")
        or item.get("verrou_score")
        or (item.get("lock_model_scores") or {}).get("verrou")
        or (item.get("lock_model_scores") or {}).get("1")
    )

    active = sorted(str(name) for name, value in features.items() if bool(value))
    explicit_unresolved = _has_explicit_unresolved_language(text)
    has_content = len(text) >= 30

    # FastJudge reste tracé séparément pour l'audit.
    fastjudge_verrou = bool(
        original_role == "verrou"
        and item.get("fastjudge_verrou_signal", item.get("lock_candidate", False))
    )
    direct = bool(
        has_content
        and item.get("project_lock_seed", False)
    )

    # Score descriptif/ranking uniquement, pas une probabilité calibrée.
    direct_score = model_score
    if bool(features.get("technical")):
        direct_score += 0.03
    if explicit_unresolved:
        direct_score += 0.04

    # Les autres rôles peuvent servir de preuves pour documenter le verrou.
    role_supports = role in SUPPORTING_ROLES
    evidence_score = model_score
    if role_supports:
        evidence_score += 0.07
    if any(bool(features.get(name)) for name in SUPPORT_FEATURES):
        evidence_score += 0.08
    if bool(features.get("measurement_limit")):
        evidence_score += 0.07
    if bool(features.get("technical")):
        evidence_score += 0.05
    if explicit_unresolved:
        evidence_score += 0.04

    document_type = str(item.get("document_type") or "").lower()
    source_policy = str(item.get("source_policy") or "").lower()
    if document_type in DESIGN_SUPPORT_TYPES:
        evidence_score += 0.06
    if source_policy == "context_only":
        evidence_score -= 0.03

    supporting = bool(
        has_content
        and not direct
        and role_supports
        and (bool(features.get("technical")) or document_type in DESIGN_SUPPORT_TYPES)
        and evidence_score >= evidence_threshold
    )

    if direct:
        if fastjudge_verrou:
            seed_reason = "fastjudge_verrou_seed"
            reason = "seed_candidate_from_fastjudge"
        else:
            seed_reason = "explicit_technical_problem_seed"
            reason = "seed_candidate_from_current_evidence"
    elif supporting:
        seed_reason = "not_a_seed"
        reason = "supporting_evidence_only"
    else:
        seed_reason = "not_a_seed"
        reason = "not_relevant_for_lock_construction"

    return CandidateDecision(
        direct_lock_candidate=direct,
        supporting_lock_evidence=supporting,
        direct_score=round(min(max(direct_score, 0.0), 1.0), 4),
        evidence_score=round(min(max(evidence_score, 0.0), 1.0), 4),
        semantic_role=role,
        active_features=active,
        reason=reason,
        seed_reason=seed_reason,
    )

def enrich_candidate(item: Mapping[str, Any]) -> Dict[str, Any]:
    output = dict(item)
    decision = classify_candidate(item)
    output["lock_candidate_v173"] = asdict(decision)
    output["lock_candidate_v172"] = asdict(decision)  # compatibilité temporaire
    output["direct_lock_candidate"] = decision.direct_lock_candidate
    output["supporting_lock_evidence"] = decision.supporting_lock_evidence
    output["lock_evidence_score"] = decision.evidence_score
    output["lock_seed_reason"] = decision.seed_reason
    return output


def enrich_candidates(items: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [enrich_candidate(item) for item in items if isinstance(item, Mapping)]
