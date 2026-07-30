# -*- coding: utf-8 -*-
"""Reconstruction déterministe du système technique avant les verrous.

Cette étape ne décide ni qu'un axe est un verrou, ni qu'il est éligible au CIR.
Elle transforme les preuves NLP en graphe traçable : documents, preuves,
objets, fonctions, phénomènes, paramètres, références et familles provisoires.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence, Set, Tuple

from .technical_system_models import ConceptNode, EvidenceNode, ProvisionalSubsystem, RelationEdge
from .technical_system_normalizer import (
    EVIDENCE_WORDS,
    GENERIC_WORDS,
    TECHNICAL_HEADS,
    canonical_text,
    clean_document_name,
    compact_unique,
    extract_references,
    has_technical_head,
    normalize_label,
    normalize_space,
    score_filename_phrase,
    tokenize,
)

VERSION = "technical_system_graph_v1_0_evidence_first_no_lock_decision"

FUNCTION_PATTERNS = [
    re.compile(r"\b(?:afin de|permet(?:tre)? de|destin[ée]e? à|sert à|vise à|objectif(?: est)? de|but(?: est)? de|consiste à)\s+([^.;:\n]{8,180})", re.I),
    re.compile(r"\b(?:in order to|designed to|used to|aims? to|purpose is to|allows? to)\s+([^.;:\n]{8,180})", re.I),
]

PHENOMENON_TERMS = {
    "acoustique", "adhesion", "bruit", "cavitation", "corrosion", "deformation",
    "diffusion", "dilatation", "dispersion", "echauffement", "ecoulement",
    "erosion", "fatigue", "fissuration", "frottement", "humidite", "hygrometrie",
    "instabilite", "interference", "latence", "perte", "pression", "resonance",
    "saturation", "separation", "temperature", "turbulence", "usure", "vibration",
    "vitesse", "debit", "condensation", "evaporation", "compression", "traction",
    "torsion", "flambement", "conductivite", "viscosite", "precision", "derive",
    "overfitting", "drift", "bias", "noise", "corrosion", "wear", "vibration",
    "temperature", "pressure", "flow", "latency", "instability", "deformation",
}

PARAMETER_TERMS = {
    "temperature", "pression", "debit", "vitesse", "puissance", "couple", "masse",
    "poids", "frequence", "amplitude", "humidite", "hygrometrie", "viscosite",
    "densite", "concentration", "tension", "courant", "resistance", "conductivite",
    "rendement", "precision", "latence", "temps", "distance", "diametre", "epaisseur",
    "rugosite", "pente", "seuil", "score", "taux", "volume", "capacite", "energie",
    "temperature", "pressure", "flow", "speed", "power", "frequency", "humidity",
    "accuracy", "latency", "threshold", "voltage", "current", "mass", "weight",
}

UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)?\s*(?:°?c|k|bar|pa|kpa|mpa|rpm|tr/min|hz|khz|mhz|ghz|v|mv|a|ma|w|kw|mw|j|kj|wh|kwh|m3/h|l/min|kg|g|mg|mm|cm|m|µm|um|nm|%|ms|s|min|h)\b",
    re.I,
)

OBJECT_PATTERN = re.compile(
    r"\b(?:syst[eè]me|circuit|m[ée]canisme|module|ensemble|composant|dispositif|"
    r"algorithme|architecture|filtre|pompe|moteur|capteur|mat[ée]riau|proc[ée]d[ée]|"
    r"r[ée]seau|interface|logiciel|r[ée]acteur|convertisseur|contr[oô]leur|vanne|"
    r"system|mechanism|module|component|device|algorithm|architecture|filter|pump|"
    r"motor|sensor|material|process|network|interface|software|reactor)"
    r"(?:\s+(?:d['’]|de|du|des|à|a|pour|of|for))?\s+([A-Za-zÀ-ÿ0-9_-]+(?:\s+[A-Za-zÀ-ÿ0-9_-]+){0,4})",
    re.I,
)


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _evidence_type(item: Mapping[str, Any]) -> str:
    dtype = canonical_text(item.get("document_type") or item.get("structure_type") or "")
    role = canonical_text(item.get("semantic_role") or item.get("role") or "")
    text = canonical_text(" ".join([str(item.get("section_title") or ""), str(item.get("text") or "")]))
    if dtype in {"plan_schema", "conception_technique"}:
        return "design"
    if dtype == "resultats_mesures" or role == "resultat":
        return "measurement"
    if dtype == "rapport_test" or set(tokenize(text, keep_generic=True)) & {"essai", "test", "validation"}:
        return "test"
    if dtype == "etude_technique":
        return "analysis"
    if role in {"methode", "parametre"}:
        return "method_or_parameter"
    if role == "limite":
        return "limitation"
    return "context"


def _passage_identity(item: Mapping[str, Any], index: int = 0) -> str:
    return str(
        item.get("passage_id")
        or item.get("evidence_id")
        or _stable_id(
            "passage",
            item.get("source_path") or item.get("document"),
            item.get("sentence_start"),
            item.get("text"),
            index,
        )
    )


def _collect_from_pack(pack: Mapping[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(pack.get("evidence_catalog"), list):
        return [dict(item) for item in pack.get("evidence_catalog") or [] if isinstance(item, Mapping)]
    keys = (
        "objectifs_locaux", "methodes_locales", "resultats_locaux", "limites_locales",
        "contributions_locales", "etat_art_local", "parametres_locaux",
        "candidats_verrou_nlp",
    )
    out: List[Dict[str, Any]] = []
    for key in keys:
        for item in pack.get(key) or []:
            if isinstance(item, Mapping):
                out.append(dict(item))
    return out


def collect_evidence_items(source: Any) -> List[Dict[str, Any]]:
    """Accepte un résultat NLP complet, un evidence pack ou une liste de preuves."""
    if isinstance(source, list):
        raw_items = [dict(item) for item in source if isinstance(item, Mapping)]
    elif isinstance(source, Mapping):
        if isinstance(source.get("multi_document_evidence_pack_for_ennodiagnostic"), Mapping):
            raw_items = _collect_from_pack(source["multi_document_evidence_pack_for_ennodiagnostic"])
        elif isinstance(source.get("evidence_pack_before_frascati"), Mapping):
            raw_items = _collect_from_pack(source["evidence_pack_before_frascati"])
        else:
            raw_items = _collect_from_pack(source)
    else:
        raw_items = []

    deduped: Dict[str, Dict[str, Any]] = {}
    for index, item in enumerate(raw_items):
        key = _passage_identity(item, index)
        if key not in deduped:
            deduped[key] = dict(item)
    return list(deduped.values())


def _routing_by_document(source: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(source, Mapping):
        return out
    for row in source.get("routing") or []:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("document") or "")
        if name:
            out[name] = dict(row)
    return out


# ---------------------------------------------------------------------------
# Extraction de concepts
# ---------------------------------------------------------------------------

def _filename_phrases(document: str) -> List[Tuple[str, float]]:
    clean = clean_document_name(document)
    if not clean:
        return []
    # Sépare les marqueurs de type analyse/essais de l'objet réellement décrit.
    raw_tokens = tokenize(clean, keep_generic=True)
    filtered = [
        token for token in raw_tokens
        if token not in GENERIC_WORDS
        and token not in EVIDENCE_WORDS
        and not re.fullmatch(r"[a-z]{2,8}\d{2,}[a-z0-9_-]*", token)
    ]
    phrases: List[str] = []
    if filtered:
        phrases.append(" ".join(filtered[:6]))
    # Les n-grammes de 1 à 3 mots préservent les composants partagés entre noms.
    for n in (3, 2, 1):
        for index in range(0, max(0, len(filtered) - n + 1)):
            gram = " ".join(filtered[index:index + n])
            if gram and gram not in phrases:
                phrases.append(gram)
    return [(phrase, score_filename_phrase(phrase)) for phrase in phrases[:12]]


def _field_phrases(item: Mapping[str, Any]) -> List[Tuple[str, float, str]]:
    out: List[Tuple[str, float, str]] = []
    for field, score in (("technical_entities", 0.98), ("top_phrases", 0.88), ("top_terms", 0.72)):
        value = item.get(field)
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            for candidate in value:
                label = normalize_space(str(candidate or ""))
                if normalize_label(label):
                    out.append((label, score, field))
    profile = item.get("concept_profile")
    if isinstance(profile, Mapping):
        for field in ("top_phrases", "top_terms", "technical_entities"):
            value = profile.get(field)
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                for candidate in value:
                    label = normalize_space(str(candidate or ""))
                    if normalize_label(label):
                        out.append((label, 0.76, f"concept_profile.{field}"))
    return out


def _object_phrases(item: Mapping[str, Any]) -> List[Tuple[str, float, str]]:
    document = str(item.get("document") or "")
    text = " ".join(
        str(value or "")
        for value in (item.get("section_title"), item.get("context_before"), item.get("text"), item.get("context_after"))
    )
    candidates: List[Tuple[str, float, str]] = []
    candidates.extend((label, score, "filename") for label, score in _filename_phrases(document))
    candidates.extend(_field_phrases(item))
    for match in OBJECT_PATTERN.finditer(text):
        whole = normalize_space(match.group(0))
        if whole:
            candidates.append((whole, 0.82, "technical_head_pattern"))
    # Les titres de section courts peuvent nommer directement le composant.
    section = normalize_space(str(item.get("section_title") or ""))
    section_tokens = tokenize(section)
    if 1 <= len(section_tokens) <= 6 and (has_technical_head(section) or any(ch.isdigit() for ch in section)):
        candidates.append((section, 0.72, "section_title"))

    cleaned: List[Tuple[str, float, str]] = []
    seen: Set[str] = set()
    for label, score, source_kind in candidates:
        low = canonical_text(label)
        if any(marker in low for marker in ("telephone", "telefax", "e mail", "email", "website", "www", "http")):
            continue
        canonical = normalize_label(label)
        tokens = canonical.split()
        if not canonical or canonical in seen:
            continue
        if len(tokens) > 7:
            continue
        if len(tokens) == 1 and tokens[0] in TECHNICAL_HEADS:
            continue
        if len(tokens) == 1 and tokens[0] in (PHENOMENON_TERMS | PARAMETER_TERMS | GENERIC_WORDS):
            continue
        if all(token in GENERIC_WORDS for token in tokens):
            continue
        if all(any(ch.isdigit() for ch in token) for token in tokens):
            continue
        seen.add(canonical)
        cleaned.append((label, score, source_kind))
    return cleaned


def _functions(text: str) -> List[str]:
    values: List[str] = []
    for pattern in FUNCTION_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            phrase = normalize_space(match.group(1))
            phrase = re.sub(r"\s+", " ", phrase).strip(" -,:;")
            if 8 <= len(phrase) <= 180:
                values.append(phrase)
    return compact_unique(values, limit=12)


def _phenomena(text: str) -> List[str]:
    tokens = tokenize(text, keep_generic=True)
    found = [token for token in tokens if token in PHENOMENON_TERMS]
    return sorted(set(found))


def _parameters(text: str) -> List[str]:
    canonical_tokens = tokenize(text, keep_generic=True)
    labels = {token for token in canonical_tokens if token in PARAMETER_TERMS}
    # Les unités sont conservées comme métadonnées de paramètre, sans devenir
    # des objets techniques.
    labels.update(normalize_space(match.group(0)) for match in UNIT_RE.finditer(str(text or "")))
    return sorted(labels)


# ---------------------------------------------------------------------------
# Agrégation et graphe
# ---------------------------------------------------------------------------

def _upsert_concept(
    store: MutableMapping[str, Dict[str, Any]],
    *,
    kind: str,
    label: str,
    evidence_id: str,
    document_id: str,
    source_kind: str,
    score: float,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    canonical = normalize_label(label) if kind != "reference" else re.sub(r"\W+", "", str(label).upper())
    if not canonical:
        return ""
    concept_id = _stable_id(kind, canonical)
    row = store.setdefault(
        concept_id,
        {
            "concept_id": concept_id,
            "kind": kind,
            "label": normalize_space(label),
            "canonical_label": canonical,
            "aliases": set(),
            "evidence_ids": set(),
            "document_ids": set(),
            "source_kinds": set(),
            "score_sum": 0.0,
            "mention_count": 0,
            "metadata": defaultdict(set),
        },
    )
    row["aliases"].add(normalize_space(label))
    row["evidence_ids"].add(evidence_id)
    row["document_ids"].add(document_id)
    row["source_kinds"].add(source_kind)
    row["score_sum"] += float(score)
    row["mention_count"] += 1
    for key, value in (metadata or {}).items():
        if isinstance(value, (list, tuple, set)):
            row["metadata"][key].update(str(v) for v in value if v not in (None, ""))
        elif value not in (None, ""):
            row["metadata"][key].add(str(value))
    return concept_id


def _finalize_concepts(store: Mapping[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in store.values():
        mentions = max(1, int(row["mention_count"]))
        average = row["score_sum"] / mentions
        diversity_bonus = min(0.18, 0.05 * max(0, len(row["document_ids"]) - 1))
        score = min(1.0, average + diversity_bonus)
        node = ConceptNode(
            concept_id=row["concept_id"],
            kind=row["kind"],
            label=sorted(row["aliases"], key=lambda value: (-len(value), value))[0],
            canonical_label=row["canonical_label"],
            aliases=sorted(row["aliases"]),
            evidence_ids=sorted(row["evidence_ids"]),
            document_ids=sorted(row["document_ids"]),
            source_kinds=sorted(row["source_kinds"]),
            score=round(score, 4),
            metadata={key: sorted(values) for key, values in row["metadata"].items()},
        )
        data = asdict(node)
        data["mention_count"] = mentions
        out.append(data)
    out.sort(key=lambda item: (-len(item["document_ids"]), -item["score"], item["canonical_label"]))
    return out


def _build_provisional_subsystems(
    concepts: Sequence[Mapping[str, Any]],
    object_reference_links: Mapping[str, Set[str]],
    reference_doc_frequency: Mapping[str, int],
    total_documents: int,
) -> List[Dict[str, Any]]:
    object_rows = {str(row["concept_id"]): row for row in concepts if row.get("kind") == "technical_object"}
    reference_rows = {str(row["concept_id"]): row for row in concepts if row.get("kind") == "reference"}
    if not object_rows:
        return []

    parent = {object_id: object_id for object_id in object_rows}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        lroot, rroot = find(left), find(right)
        if lroot != rroot:
            parent[rroot] = lroot

    # Deux objets ne sont reliés que par une référence rare. Une référence qui
    # apparaît dans une grande partie du dossier est probablement le nom global
    # du projet et ne doit pas créer un méga-groupe.
    ref_to_objects: Dict[str, List[str]] = defaultdict(list)
    for object_id, refs in object_reference_links.items():
        for ref_id in refs:
            ref_to_objects[ref_id].append(object_id)
    max_common = max(4, math.ceil(max(total_documents, 1) * 0.15))
    accepted_refs: Set[str] = set()
    for ref_id, object_ids in ref_to_objects.items():
        ref_row = reference_rows.get(ref_id) or {}
        canonical = str(ref_row.get("canonical_label") or "")
        doc_freq = int(reference_doc_frequency.get(canonical, len(ref_row.get("document_ids") or [])))
        unique_objects = sorted(set(object_ids))
        # Une référence doit relier au moins deux documents, mais ne doit pas
        # être si fréquente qu'elle représente vraisemblablement le produit ou
        # le projet entier plutôt qu'un sous-système.
        if len(unique_objects) < 2 or doc_freq < 2 or doc_freq >= max_common:
            continue
        accepted_refs.add(ref_id)
        for index in range(1, len(unique_objects)):
            union(unique_objects[0], unique_objects[index])

    components: Dict[str, List[str]] = defaultdict(list)
    for object_id in object_rows:
        components[find(object_id)].append(object_id)

    subsystems: List[Dict[str, Any]] = []
    for object_ids in components.values():
        documents = sorted({doc for object_id in object_ids for doc in object_rows[object_id].get("document_ids") or []})
        if len(documents) < 2:
            continue
        refs = sorted({ref_id for object_id in object_ids for ref_id in object_reference_links.get(object_id, set()) if ref_id in accepted_refs})
        # Une famille provisoire exige au moins deux objets distincts. Un seul
        # libellé répété dans plusieurs documents reste un objet multi-source,
        # mais ne devient pas encore un sous-système.
        if len(object_ids) < 2:
            continue
        labels = [str(object_rows[object_id].get("label") or "") for object_id in object_ids]
        labels.sort(key=lambda value: (-len(value), value))
        label = labels[0] if labels else "Sous-système technique"
        evidence_ids = sorted({eid for object_id in object_ids for eid in object_rows[object_id].get("evidence_ids") or []})
        confidence = min(0.95, 0.45 + 0.08 * len(documents) + 0.06 * len(refs))
        subsystem = ProvisionalSubsystem(
            subsystem_id=_stable_id("subsystem", *sorted(object_ids)),
            label=label,
            object_ids=sorted(object_ids),
            reference_ids=refs,
            evidence_ids=evidence_ids,
            document_ids=documents,
            confidence=round(confidence, 4),
            reason="Objets techniques reliés par une référence distinctive présente dans plusieurs documents.",
        )
        subsystems.append(asdict(subsystem))
    subsystems.sort(key=lambda item: (-len(item["document_ids"]), -item["confidence"], item["label"]))
    return subsystems


def build_technical_system_graph(source: Any) -> Dict[str, Any]:
    evidence_items = collect_evidence_items(source)
    routing = _routing_by_document(source)

    evidence_nodes: List[Dict[str, Any]] = []
    document_nodes: Dict[str, Dict[str, Any]] = {}
    concept_store: Dict[str, Dict[str, Any]] = {}
    relations: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    concepts_by_evidence: Dict[str, Set[str]] = defaultdict(set)
    object_reference_links: Dict[str, Set[str]] = defaultdict(set)
    reference_documents: Dict[str, Set[str]] = defaultdict(set)

    def add_relation(source_id: str, target_id: str, relation: str, evidence_id: str = "", weight: float = 1.0, **metadata: Any) -> None:
        if not source_id or not target_id:
            return
        key = (source_id, target_id, relation)
        row = relations.setdefault(
            key,
            {
                "edge_id": _stable_id("edge", source_id, target_id, relation),
                "source_id": source_id,
                "target_id": target_id,
                "relation": relation,
                "weight": 0.0,
                "evidence_ids": set(),
                "metadata": defaultdict(set),
            },
        )
        row["weight"] += float(weight)
        if evidence_id:
            row["evidence_ids"].add(evidence_id)
        for meta_key, value in metadata.items():
            if isinstance(value, (list, tuple, set)):
                row["metadata"][meta_key].update(str(v) for v in value if v not in (None, ""))
            elif value not in (None, ""):
                row["metadata"][meta_key].add(str(value))

    for index, item in enumerate(evidence_items):
        document = str(item.get("document") or item.get("file_name") or "document_inconnu")
        route_row = routing.get(document) or {}
        source_path = str(item.get("source_path") or "")
        document_id = _stable_id("document", source_path or document)
        document_type = str(item.get("document_type") or route_row.get("document_type") or "unknown_document")
        document_nodes.setdefault(
            document_id,
            {
                "document_id": document_id,
                "document": document,
                "source_path": source_path,
                "document_type": document_type,
                "content_origin": item.get("content_origin") or route_row.get("content_origin") or "unknown",
                "source_policy": item.get("source_policy") or route_row.get("source_policy") or "secondary",
                "evidence_ids": set(),
                "reference_codes": set(),
            },
        )

        passage_id = _passage_identity(item, index)
        evidence_id = _stable_id("evidence", passage_id)
        role = str(item.get("semantic_role") or item.get("role") or item.get("original_model_role") or "unknown")
        confidence = _safe_float(item.get("semantic_role_confidence") or item.get("confidence") or item.get("model_confidence"))
        text = normalize_space(str(item.get("text") or ""))
        analysis_text = normalize_space(str(item.get("analysis_text") or text))
        node = EvidenceNode(
            evidence_id=evidence_id,
            passage_id=passage_id,
            document_id=document_id,
            document=document,
            source_path=source_path,
            document_type=document_type,
            semantic_role=role,
            evidence_type=_evidence_type(item),
            text=text,
            section_title=normalize_space(str(item.get("section_title") or "")),
            confidence=round(confidence, 4),
        )
        evidence_nodes.append(asdict(node))
        document_nodes[document_id]["evidence_ids"].add(evidence_id)
        add_relation(document_id, evidence_id, "document_contains_evidence", evidence_id=evidence_id)

        object_ids: Set[str] = set()
        filename_object_ids: Set[str] = set()
        for label, score, source_kind in _object_phrases(item):
            object_id = _upsert_concept(
                concept_store,
                kind="technical_object",
                label=label,
                evidence_id=evidence_id,
                document_id=document_id,
                source_kind=source_kind,
                score=score,
                metadata={"document_type": document_type},
            )
            if object_id:
                object_ids.add(object_id)
                if source_kind == "filename":
                    filename_object_ids.add(object_id)
                concepts_by_evidence[evidence_id].add(object_id)
                add_relation(evidence_id, object_id, "evidence_mentions_object", evidence_id=evidence_id, weight=score)

        full_text = " ".join([clean_document_name(document), str(item.get("section_title") or ""), analysis_text])
        filename_references = set(extract_references(clean_document_name(document)))
        text_references = set(extract_references(str(item.get("section_title") or ""), analysis_text))
        reference_ids: Set[str] = set()
        filename_reference_ids: Set[str] = set()
        for reference in sorted(filename_references | text_references):
            source_kind = "filename_reference" if reference in filename_references else "text_reference"
            ref_id = _upsert_concept(
                concept_store,
                kind="reference",
                label=reference,
                evidence_id=evidence_id,
                document_id=document_id,
                source_kind=source_kind,
                score=0.94 if source_kind == "filename_reference" else 0.82,
            )
            if ref_id:
                reference_ids.add(ref_id)
                if source_kind == "filename_reference":
                    filename_reference_ids.add(ref_id)
                    reference_documents[reference].add(document_id)
                    document_nodes[document_id]["reference_codes"].add(reference)
                concepts_by_evidence[evidence_id].add(ref_id)
                add_relation(evidence_id, ref_id, "evidence_mentions_reference", evidence_id=evidence_id, weight=0.92)

        # Les familles provisoires utilisent seulement les objets et références
        # présents dans les noms de fichiers. Les références trouvées au milieu
        # des tableaux ou des plans restent visibles, mais ne peuvent pas créer
        # de chaîne géante entre des sous-systèmes différents.
        for object_id in filename_object_ids:
            for ref_id in filename_reference_ids:
                object_reference_links[object_id].add(ref_id)
                add_relation(object_id, ref_id, "object_has_filename_reference", evidence_id=evidence_id, weight=1.0)

        for function in _functions(full_text):
            concept_id = _upsert_concept(
                concept_store,
                kind="function",
                label=function,
                evidence_id=evidence_id,
                document_id=document_id,
                source_kind="functional_phrase",
                score=0.82,
            )
            if concept_id:
                concepts_by_evidence[evidence_id].add(concept_id)
                add_relation(evidence_id, concept_id, "evidence_describes_function", evidence_id=evidence_id, weight=0.82)

        for phenomenon in _phenomena(full_text):
            concept_id = _upsert_concept(
                concept_store,
                kind="phenomenon",
                label=phenomenon,
                evidence_id=evidence_id,
                document_id=document_id,
                source_kind="phenomenon_term",
                score=0.74,
            )
            if concept_id:
                concepts_by_evidence[evidence_id].add(concept_id)
                add_relation(evidence_id, concept_id, "evidence_observes_phenomenon", evidence_id=evidence_id, weight=0.74)

        for parameter in _parameters(full_text):
            concept_id = _upsert_concept(
                concept_store,
                kind="parameter",
                label=parameter,
                evidence_id=evidence_id,
                document_id=document_id,
                source_kind="parameter_or_unit",
                score=0.68,
            )
            if concept_id:
                concepts_by_evidence[evidence_id].add(concept_id)
                add_relation(evidence_id, concept_id, "evidence_uses_parameter", evidence_id=evidence_id, weight=0.68)

    concepts = _finalize_concepts(concept_store)
    concept_index = {str(row["concept_id"]): row for row in concepts}

    # Relations de cooccurrence : elles décrivent le système, sans conclure à un verrou.
    cooccurrence: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    for evidence_id, ids in concepts_by_evidence.items():
        technical_ids = sorted(
            concept_id for concept_id in ids
            if concept_index.get(concept_id, {}).get("kind") in {"technical_object", "function", "phenomenon"}
        )
        for left_index in range(len(technical_ids)):
            for right_index in range(left_index + 1, len(technical_ids)):
                cooccurrence[(technical_ids[left_index], technical_ids[right_index])].add(evidence_id)
    for (left_id, right_id), evidence_ids in cooccurrence.items():
        if len(evidence_ids) >= 2:
            add_relation(left_id, right_id, "concepts_cooccur", weight=float(len(evidence_ids)), cooccurrence_count=len(evidence_ids))
            relations[(left_id, right_id, "concepts_cooccur")]["evidence_ids"].update(evidence_ids)

    finalized_relations: List[Dict[str, Any]] = []
    for row in relations.values():
        edge = RelationEdge(
            edge_id=row["edge_id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            relation=row["relation"],
            weight=round(row["weight"], 4),
            evidence_ids=sorted(row["evidence_ids"]),
            metadata={key: sorted(values) for key, values in row["metadata"].items()},
        )
        finalized_relations.append(asdict(edge))
    finalized_relations.sort(key=lambda edge: (edge["relation"], edge["source_id"], edge["target_id"]))

    # Conserver aussi les documents routés qui n'ont fourni aucune preuve.
    existing_document_names = {str(row.get("document") or "") for row in document_nodes.values()}
    for document_name, route_row in routing.items():
        if document_name in existing_document_names:
            continue
        document_id = _stable_id("document", document_name)
        document_nodes[document_id] = {
            "document_id": document_id,
            "document": document_name,
            "source_path": "",
            "document_type": route_row.get("document_type") or "unknown_document",
            "content_origin": route_row.get("content_origin") or "unknown",
            "source_policy": route_row.get("source_policy") or "secondary",
            "evidence_ids": set(),
            "reference_codes": set(),
        }

    finalized_documents: List[Dict[str, Any]] = []
    for row in document_nodes.values():
        finalized_documents.append(
            {
                **{key: value for key, value in row.items() if key not in {"evidence_ids", "reference_codes"}},
                "evidence_ids": sorted(row["evidence_ids"]),
                "reference_codes": sorted(row["reference_codes"]),
            }
        )
    finalized_documents.sort(key=lambda row: row["document"])

    reference_frequency = {reference: len(document_ids) for reference, document_ids in reference_documents.items()}
    provisional_subsystems = _build_provisional_subsystems(
        concepts,
        object_reference_links,
        reference_frequency,
        total_documents=len(finalized_documents),
    )

    counts = Counter(row["kind"] for row in concepts)
    return {
        "version": VERSION,
        "contract": "describe_technical_system_before_lock_reasoning",
        "decision_policy": {
            "creates_lock": False,
            "applies_frascati": False,
            "filters_evidence": False,
            "human_validation_required": True,
        },
        "stats": {
            "documents_count": len(finalized_documents),
            "evidence_count": len(evidence_nodes),
            "technical_objects_count": counts.get("technical_object", 0),
            "functions_count": counts.get("function", 0),
            "phenomena_count": counts.get("phenomenon", 0),
            "parameters_count": counts.get("parameter", 0),
            "references_count": counts.get("reference", 0),
            "relations_count": len(finalized_relations),
            "provisional_subsystems_count": len(provisional_subsystems),
        },
        "documents": finalized_documents,
        "evidence": evidence_nodes,
        "concepts": concepts,
        "provisional_subsystems": provisional_subsystems,
        "relations": finalized_relations,
        "audit": {
            "reference_document_frequency": dict(sorted(reference_frequency.items())),
            "notes": [
                "Les familles provisoires ne sont pas des verrous.",
                "Une référence trop fréquente est traitée comme référence globale et ne fusionne pas le dossier.",
                "Les passages sources restent inchangés et chaque concept conserve ses preuves.",
            ],
        },
    }
