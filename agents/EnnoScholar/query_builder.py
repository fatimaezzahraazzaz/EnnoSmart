# -*- coding: utf-8 -*-
from __future__ import annotations

"""
query_builder.py — EnnoScholar V145 strict local queries

Construction et sélection de requêtes scientifiques multi-domaines.

Correction V138 :
- ne plus laisser les requêtes de profil CIR génériques dominer la sélection ;
- extraire des termes-ancrages depuis le verrou + EnnoDiagnostic + preuves sources ;
- construire des queries orientées projet/verrou avant les queries domaine ;
- pénaliser dynamiquement les queries composées surtout de termes de profil domaine ;
- rester générique : aucun client/projet n'est codé en dur.
"""

import re
from typing import Any, Dict, List, Set

from .utils import clean_text, dedupe_keep_order, flatten_text, norm, tokenize
from .cir_domain_query_catalog import build_cir_domain_queries, get_cir_domain_profile


# Bruit rédactionnel / Frascati seulement.
# IMPORTANT : ne pas mettre ici des mots métier comme classification, detection,
# architecture, system, model, validation, performance, etc. Ces mots peuvent être
# essentiels selon le domaine du projet.
NOISE_QUERY_TOKENS = {
    "question", "qualification", "permet", "permet-elle", "implicite",
    "probable", "possible", "projet", "travaux", "documents", "sources",
    "source", "groupe", "agent", "diagnostic", "courant", "courante",
    "dossier", "client", "consultant", "frascati", "ennodiagnostic",
    "ennoscholar", "verrou", "verrous", "rnd", "rd", "r", "d",
    "peut", "peuvent", "doit", "doivent", "permettre", "vise", "visent",
    "montrer", "montre", "montrent", "confirmer", "confirme", "confirment",
    "demontrer", "démontre", "demontrent", "démontrent", "utilisation",
    "efficace", "cruciale", "bon", "bonne", "cette", "cet", "ces",
    "dans", "toutes", "conditions", "informatique", "domaine", "detecte",
    "détecté", "scientifique", "investiguer", "partir", "preuves",
    "cir", "nlp", "rag", "llm", "api", "json", "pdf", "docx",
    "sont", "est", "etre", "être", "qui", "que", "dont", "mais", "plus", "moins",
    "avec", "sans", "entre", "vers", "comparaison", "comparison",
}


KIND_PRIORITY = {
    # Requêtes ancrées projet/verrou : prioritaires.
    "local_object_anchors": 1.28,
    "local_phenomenon_anchors": 1.24,
    "local_methods_anchors": 1.20,
    "local_constraint_anchors": 1.16,
    "project_anchor_terms": 0.90,
    "validation_anchor_terms": 1.12,
    "simulation_validation_terms": 1.08,
    "object_phenomenon_clean": 1.00,
    "scientific_problem_clean": 0.96,
    "key_terms_clean": 0.92,
    "methods_clean": 0.88,
    "constraints_clean": 0.86,
    "diagnostic_context_terms": 0.84,

    # Requêtes proposées par le backend à partir des preuves sources.
    "backend_enriched_source_query_safe": 0.90,
    "backend_suggested_query": 0.88,

    # Requêtes domaine : utiles, mais seulement en complément/fallback.
    "verrou_terms_domain_context": 0.76,
    "domain_terms_verrou_context": 0.70,
    "french_key_terms": 0.74,
    "cir_domain_profile_query": 0.46,
    "fallback_domain_terms": 0.44,
}


GENERIC_VALIDATION_TERMS = [
    # Ce sont des termes méthodologiques transverses, pas des mots d'un domaine métier.
    "validation",
    "experimental",
    "measured data",
    "simulation",
    "comparison",
    "benchmark",
    "representativeness",
    "generalization",
]

ADMIN_QUERY_TERMS = {
    "cir", "frascati", "nlp", "rag", "llm", "ennodiagnostic", "ennoscholar",
    "consultant", "dossier", "qualification", "json", "api", "pdf", "docx",
}

WEAK_ANCHOR_TERMS = {
    "comparison", "comparaison", "validation", "evaluation", "performance",
    "method", "methods", "methode", "méthode", "model", "models", "modele", "modèle",
    "system", "systems", "systeme", "système", "software", "logiciel", "data", "données",
    "image", "signal", "result", "results", "résultat", "study", "article", "paper",
}



def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v or "").strip()]
    return [str(value)] if str(value or "").strip() else []


def _intent_text(intent: Dict[str, Any]) -> str:
    return " ".join([
        str(intent.get("backend_enrichment_profile") or ""),
        str(intent.get("enrichment_profile") or ""),
        str(intent.get("profile") or ""),
        str(intent.get("verrou_title") or ""),
        str(intent.get("original_title") or ""),
        str(intent.get("verrou_text") or ""),
        str(intent.get("scientific_problem") or ""),
        str(intent.get("technical_object") or ""),
        str(intent.get("phenomenon") or ""),
        " ".join(map(str, intent.get("constraints") or [])),
        " ".join(map(str, intent.get("methods") or [])),
        " ".join(map(str, intent.get("key_terms_fr") or [])),
        " ".join(map(str, intent.get("key_terms_en") or [])),
    ])


def _domain_detection_from_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    for k in ["domain_detection", "cir_domain_detection"]:
        if isinstance(intent.get(k), dict):
            return intent.get(k) or {}
    return {}


def _title_priority_text(intent: Dict[str, Any]) -> str:
    """Texte court prioritaire pour choisir le profil sans pollution du contexte global."""
    return " ".join([
        str(intent.get("verrou_title") or ""),
        str(intent.get("original_title") or ""),
        str(intent.get("title") or ""),
    ])


def _profile_for_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    """
    Profil CIR choisi titre-first, puis domaine/contexte en fallback.
    Cette logique reste générique et ne cible aucun client/projet précis.
    """
    title_profile = get_cir_domain_profile({}, _title_priority_text(intent))
    title_profile_id = str(title_profile.get("profile_id") or "")
    if title_profile_id not in {"generic", "mechanical_civil_engineering", "sociology_geography_urbanism"}:
        return title_profile
    return get_cir_domain_profile(_domain_detection_from_intent(intent), _intent_text(intent))


def detect_scholar_profile(intent: Dict[str, Any]) -> str:
    return _profile_for_intent(intent).get("profile_id", "generic")


def _filtered_words(text: Any) -> List[str]:
    """
    Retire seulement le bruit rédactionnel.
    Les mots métier restent autorisés car EnnoScholar est multi-domaines.
    """
    words: List[str] = []
    for t in tokenize(text):
        nt = norm(t)
        if not nt or nt in NOISE_QUERY_TOKENS:
            continue
        if len(nt) < 3:
            continue
        if t not in words:
            words.append(t)
    return words


def _query(parts: List[Any], max_words: int = 10) -> str:
    words: List[str] = []
    seen = set()
    for p in parts:
        for w in _filtered_words(p):
            nw = norm(w)
            if nw and nw not in seen:
                seen.add(nw)
                words.append(w)
            if len(words) >= max_words:
                return clean_text(" ".join(words), 220)
    return clean_text(" ".join(words), 220)


def _domain_labels_text(intent: Dict[str, Any]) -> str:
    d = _domain_detection_from_intent(intent)
    parts = []
    for key in [
        "main_domain_label", "sub_domain_label", "broad_domain_label",
        "domain_label_niv1", "domain_label_niv2", "domain_label_niv3",
        "display_label",
    ]:
        if d.get(key):
            parts.append(str(d.get(key)))
    profile = intent.get("cir_domain_profile") or {}
    if isinstance(profile, dict):
        for key in ["label", "profile_id"]:
            if profile.get(key):
                parts.append(str(profile.get(key)))
    return " ".join(parts)


def _diagnostic_context_text(intent: Dict[str, Any]) -> str:
    """Récupère le contexte EnnoDiagnostic sans forcer un format unique."""
    if intent.get("diagnostic_context_text"):
        return clean_text(intent.get("diagnostic_context_text"), 3500)
    ctx = intent.get("diagnostic_context")
    if isinstance(ctx, dict):
        return flatten_text(ctx, max_chars=3500)
    return ""


def _source_basis_text(intent: Dict[str, Any]) -> str:
    sb = intent.get("source_basis") or {}
    if not isinstance(sb, dict):
        return ""
    return " ".join([
        str(sb.get("title") or ""),
        str(sb.get("source_text_excerpt") or ""),
        str(sb.get("context_relevant_excerpt") or ""),
        " ".join(map(str, sb.get("domain_terms") or [])),
    ])


def _query_tokens(text: Any) -> Set[str]:
    return set(_filtered_words(text))


def _extract_acronyms_and_names(*texts: Any) -> List[str]:
    """
    Extrait des ancres fortes sans domaine codé en dur : acronymes, sigles,
    noms techniques/projets. Exemples : SAR, ATR, REI, MOCEM, Salsa, ADN, GPU.
    """
    raw = " ".join(str(t or "") for t in texts)
    out: List[str] = []
    seen = set()

    for m in re.findall(r"\b[A-Z][A-Z0-9]{1,}(?:[-_/][A-Z0-9]+)?\b", raw):
        key = norm(m)
        if key and key not in seen:
            seen.add(key)
            out.append(m)

    for m in re.findall(r"\b[A-Z][a-zA-Z0-9]{3,}\b", raw):
        # On retire quelques mots de structure, pas des mots métier.
        if m.lower() in {"source", "projet", "validation", "domaine", "signal", "lecture"}:
            continue
        key = norm(m)
        if key and key not in seen:
            seen.add(key)
            out.append(m)

    return out[:14]


def _profile_terms_for_intent(intent: Dict[str, Any]) -> Set[str]:
    """
    Termes de profil domaine dynamiques : ils viennent du profil CIR détecté,
    pas d'une liste fixe codée pour un domaine.
    """
    profile = intent.get("cir_domain_profile") or _profile_for_intent(intent)
    parts: List[str] = []
    if isinstance(profile, dict):
        for key in ["positive_terms", "domain_terms", "query_seeds", "label", "profile_id"]:
            value = profile.get(key)
            if isinstance(value, list):
                parts.extend(map(str, value))
            elif value:
                parts.append(str(value))
    parts.append(_domain_labels_text(intent))
    return _query_tokens(" ".join(parts))



def _extract_anchor_terms(intent: Dict[str, Any]) -> List[str]:
    """Ancres locales au verrou, sans contexte global ni termes administratifs."""
    source_basis = intent.get("source_basis") or {}
    if not isinstance(source_basis, dict):
        source_basis = {}

    local_texts = [
        str(intent.get("verrou_title") or intent.get("original_title") or ""),
        str(intent.get("technical_object") or ""),
        str(intent.get("phenomenon") or ""),
        str(source_basis.get("title") or ""),
        str(source_basis.get("source_text_excerpt") or ""),
    ]

    anchors: List[str] = []
    anchors.extend(_as_list(intent.get("strong_anchors")))
    anchors.extend(_extract_acronyms_and_names(*local_texts))

    for key in ["key_terms_fr", "key_terms_en", "methods"]:
        for value in _as_list(intent.get(key)):
            nv = norm(value)
            toks = _query_tokens(value)
            if not nv or any(t in ADMIN_QUERY_TERMS for t in toks):
                continue
            if nv in WEAK_ANCHOR_TERMS:
                continue
            if len(toks) >= 2 or (len(toks) == 1 and len(next(iter(toks), "")) >= 5):
                anchors.append(value)

    # Expressions compactes du titre/objet uniquement.
    for text in local_texts[:3]:
        toks = [t for t in _filtered_words(text) if norm(t) not in WEAK_ANCHOR_TERMS]
        for size in [3, 2]:
            for i in range(max(0, len(toks) - size + 1)):
                expr = " ".join(toks[i:i + size])
                if len(expr) >= 8:
                    anchors.append(expr)

    out: List[str] = []
    seen = set()
    for value in anchors:
        value = clean_text(value, 90)
        nv = norm(value)
        toks = _query_tokens(value)
        if not nv or nv in seen or not toks:
            continue
        if any(t in ADMIN_QUERY_TERMS for t in toks):
            continue
        if all(t in WEAK_ANCHOR_TERMS for t in toks):
            continue
        seen.add(nv)
        out.append(value)
    return out[:24]


def _anchor_overlap_count(query: str, anchors: List[str]) -> int:
    q_tokens = _query_tokens(query)
    qn = " " + norm(query) + " "
    count = 0
    for anchor in anchors:
        an = norm(anchor)
        atoks = _query_tokens(anchor)
        if not an or not atoks:
            continue
        if len(atoks) == 1:
            token = next(iter(atoks))
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", qn):
                count += 1
        elif atoks.issubset(q_tokens) or re.search(
            rf"(?<![a-z0-9]){re.escape(an).replace('\\ ', r'\\s+')}(?![a-z0-9])",
            qn,
        ):
            count += 1
    return count

def _profile_term_count(query: str, intent: Dict[str, Any]) -> int:
    q_tokens = _query_tokens(query)
    profile_terms = _profile_terms_for_intent(intent)
    return len(q_tokens & profile_terms)


def _profile_dominance(query: str, intent: Dict[str, Any], anchors: List[str]) -> float:
    q_tokens = _query_tokens(query)
    if not q_tokens:
        return 0.0
    profile_terms = _profile_terms_for_intent(intent)
    anchor_tokens = _query_tokens(" ".join(anchors))
    # termes profil qui ne sont pas aussi des ancres projet.
    profile_only = (q_tokens & profile_terms) - anchor_tokens
    return len(profile_only) / max(1, len(q_tokens))


def _refine_query_with_anchors(query: str, intent: Dict[str, Any], max_words: int = 10) -> str:
    anchors = _extract_anchor_terms(intent)
    words: List[str] = []
    seen = set()

    # priorité aux ancres fortes puis à la query originale.
    for part in anchors + [query]:
        for w in _filtered_words(part):
            nw = norm(w)
            if not nw or nw in seen or nw in NOISE_QUERY_TOKENS:
                continue
            seen.add(nw)
            words.append(w)
            if len(words) >= max_words:
                return clean_text(" ".join(words), 220)

    return clean_text(" ".join(words), 220)



def _build_anchor_queries(intent: Dict[str, Any]) -> List[Dict[str, Any]]:
    anchors = _extract_anchor_terms(intent)
    if not anchors:
        return []

    obj = clean_text(intent.get("technical_object"), 180)
    phen = clean_text(intent.get("phenomenon"), 160)
    methods = _as_list(intent.get("methods"))
    constraints = _as_list(intent.get("constraints"))

    out: List[Dict[str, Any]] = []
    seen = set()

    def add(parts: List[Any], kind: str, max_words: int = 9) -> None:
        query = _query(parts, max_words=max_words)
        if not query or not is_query_safe_for_intent(query, intent):
            return
        key = norm(query)
        if key in seen:
            return
        seen.add(key)
        out.append({"query": query, "kind": kind})

    # Une requête par axe scientifique, sans mélanger tous les acronymes du projet.
    add([obj, anchors[:4]], "local_object_anchors", max_words=9)
    if phen:
        add([phen, anchors[:3], methods[:2]], "local_phenomenon_anchors", max_words=9)
    if methods:
        add([methods[:3], obj, anchors[:2]], "local_methods_anchors", max_words=9)
    if constraints:
        add([obj, constraints[:1], anchors[:3]], "local_constraint_anchors", max_words=9)

    return out[:4]


def is_query_safe_for_intent(query: str, intent_or_profile: Dict[str, Any] | str) -> bool:
    q = norm(query)
    if len(q) < 10:
        return False

    words = [w for w in q.split() if len(w) >= 3]
    if len(words) < 3:
        return False
    if any(word in ADMIN_QUERY_TERMS for word in words):
        return False

    useful = [w for w in words if w not in NOISE_QUERY_TOKENS and w not in WEAK_ANCHOR_TERMS]
    if len(useful) < 2:
        return False

    if isinstance(intent_or_profile, dict):
        anchors = _extract_anchor_terms(intent_or_profile)
        # Les requêtes profil pur ne sont plus autorisées : au moins une ancre locale.
        if anchors and _anchor_overlap_count(query, anchors) < 1:
            return False
    return True


def build_queries_from_intent(intent: Dict[str, Any], max_queries: int = 14) -> List[Dict[str, Any]]:
    """Génère des requêtes courtes et indépendantes pour le verrou courant."""
    intent = dict(intent or {})
    profile = _profile_for_intent(intent)
    intent["cir_domain_profile"] = profile

    queries: List[Dict[str, Any]] = []
    seen = set()

    def add(query: str, kind: str) -> None:
        query = clean_text(query, 220)
        if not is_query_safe_for_intent(query, intent):
            return
        key = norm(query)
        if not key or key in seen:
            return
        seen.add(key)
        queries.append({"query": query, "kind": kind})

    obj = clean_text(intent.get("technical_object"), 180)
    phen = clean_text(intent.get("phenomenon"), 180)
    prob = clean_text(intent.get("scientific_problem"), 260)
    title = clean_text(intent.get("verrou_title") or intent.get("original_title"), 220)
    methods = intent.get("methods") or []
    constraints = intent.get("constraints") or []
    anchors = _extract_anchor_terms(intent)

    for item in _build_anchor_queries(intent):
        add(item.get("query", ""), item.get("kind", "local_anchor_query"))

    add(_query([obj, phen, anchors[:3]], max_words=9), "object_phenomenon_clean")
    add(_query([prob, anchors[:3]], max_words=9), "scientific_problem_clean")
    if methods:
        add(_query([methods[:3], obj, anchors[:2]], max_words=9), "methods_clean")
    if constraints:
        add(_query([obj, constraints[:1], anchors[:3]], max_words=9), "constraints_clean")

    # Un seul complément domaine, obligatoirement croisé avec des ancres locales.
    if anchors:
        profile_terms = profile.get("positive_terms") or profile.get("domain_terms") or []
        if profile_terms:
            add(_query([anchors[:3], profile_terms[:2]], max_words=8), "local_domain_context")

    if not queries:
        add(_query([title, obj, anchors[:4]], max_words=9), "fallback_local_terms")

    return queries[:max_queries]


def _context_terms_for_selection(intent: Dict[str, Any]) -> Set[str]:
    """Scoring des requêtes à partir du verrou local seulement."""
    source_basis = intent.get("source_basis") or {}
    if not isinstance(source_basis, dict):
        source_basis = {}
    parts: List[Any] = [
        intent.get("verrou_title"),
        intent.get("original_title"),
        intent.get("scientific_problem"),
        intent.get("technical_object"),
        intent.get("phenomenon"),
        " ".join(map(str, intent.get("methods") or [])),
        " ".join(map(str, intent.get("key_terms_en") or [])),
        " ".join(map(str, intent.get("key_terms_fr") or [])),
        source_basis.get("source_text_excerpt"),
    ]
    return _query_tokens(" ".join(str(x or "") for x in parts))

def _query_similarity(q1: str, q2: str) -> float:
    t1 = _query_tokens(q1)
    t2 = _query_tokens(q2)
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / max(1, len(t1 | t2))


def _score_query_candidate(item: Dict[str, Any], intent: Dict[str, Any], context_terms: Set[str]) -> float:
    query = item.get("query", "")
    kind = item.get("kind", "")
    q_tokens = _query_tokens(query)
    if not q_tokens:
        return -999.0

    anchors = _extract_anchor_terms(intent)
    anchor_count = _anchor_overlap_count(query, anchors)
    dominance = _profile_dominance(query, intent, anchors)

    base = KIND_PRIORITY.get(kind, 0.60)
    overlap = q_tokens & context_terms
    overlap_ratio = len(overlap) / max(1, len(q_tokens))

    score = base
    score += overlap_ratio * 0.85

    # Bonus fort : la query reprend les ancres réelles du verrou/projet.
    score += min(anchor_count * 0.32, 1.10)

    # Bonus si plusieurs mots de la query viennent vraiment du verrou/contexte.
    if len(overlap) >= 3:
        score += 0.16
    if len(overlap) >= 5:
        score += 0.16

    # Bonus léger si la query contient des termes du titre ou de l'objet technique.
    title_terms = _query_tokens(" ".join([
        str(intent.get("verrou_title") or ""),
        str(intent.get("original_title") or ""),
        str(intent.get("technical_object") or ""),
    ]))
    if q_tokens & title_terms:
        score += 0.18

    # Malus si la query n'est pas liée au contexte.
    if len(overlap) == 0:
        score -= 0.80
    elif len(overlap) == 1:
        score -= 0.25

    # Malus si elle est dominée par le profil domaine mais sans ancres projet.
    if dominance >= 0.45 and anchor_count < 2:
        score -= 0.85
    elif dominance >= 0.35 and anchor_count < 2:
        score -= 0.45

    # Les queries domaine restent utiles, mais seulement si elles croisent le verrou.
    if kind == "cir_domain_profile_query":
        score -= 0.20
        if anchor_count == 0:
            score -= 0.60
        if len(overlap) <= 1:
            score -= 0.35

    # Malus pour requêtes trop courtes ou trop longues.
    if len(q_tokens) < 4:
        score -= 0.20
    if len(q_tokens) > 14:
        score -= 0.18

    return round(score, 4)



def select_best_queries_for_intent(
    queries_generated: List[Dict[str, Any]],
    intent: Dict[str, Any],
    max_queries: int = 3,
) -> List[Dict[str, Any]]:
    if not queries_generated:
        return []

    max_queries = max(1, int(max_queries or 3))
    context_terms = _context_terms_for_selection(intent)
    anchors = _extract_anchor_terms(intent)

    scored: List[Dict[str, Any]] = []
    seen = set()
    for raw in queries_generated:
        query = raw.get("query") if isinstance(raw, dict) else str(raw)
        query = clean_text(query, 220)
        if not is_query_safe_for_intent(query, intent):
            continue
        key = norm(query)
        if not key or key in seen:
            continue
        seen.add(key)

        item = dict(raw) if isinstance(raw, dict) else {"query": query, "kind": "auto"}
        item["query"] = query
        item.setdefault("kind", "auto")
        item["anchor_count"] = _anchor_overlap_count(query, anchors)
        item["profile_term_count"] = _profile_term_count(query, intent)
        item["profile_dominance"] = round(_profile_dominance(query, intent, anchors), 4)
        item["selection_score"] = _score_query_candidate(item, intent, context_terms)
        if item["anchor_count"] >= 1:
            scored.append(item)

    scored.sort(key=lambda x: x.get("selection_score", 0.0), reverse=True)
    selected: List[Dict[str, Any]] = []
    kinds = set()
    for item in scored:
        if len(selected) >= max_queries:
            break
        query = item.get("query", "")
        if any(_query_similarity(query, other.get("query", "")) >= 0.68 for other in selected):
            continue
        kind = str(item.get("kind") or "")
        # Favorise des axes différents plutôt que trois variantes quasi identiques.
        family = kind.split("_")[1] if "_" in kind else kind
        if family in kinds and len(selected) < max_queries - 1:
            continue
        kinds.add(family)
        selected.append(item)

    return selected[:max_queries]

def attach_queries_to_intent(intent: Dict[str, Any], max_queries: int = 14) -> Dict[str, Any]:
    out = dict(intent or {})
    out["cir_domain_profile"] = _profile_for_intent(out)
    out["search_queries"] = build_queries_from_intent(out, max_queries=max_queries)
    out["query_builder_version"] = "v145_strict_local_anchor_queries"
    return out


# =============================================================================
# V146 — requêtes fondées sur les concepts scientifiques centraux
# =============================================================================
_BUILD_QUERIES_V145 = build_queries_from_intent
_SELECT_QUERIES_V145 = select_best_queries_for_intent

KIND_PRIORITY.update({
    "v146_core_methods": 1.45,
    "v146_core_phenomenon": 1.40,
    "v146_core_constraints": 1.34,
    "v146_core_project_tools": 1.30,
    "v146_core_alternative_methods": 1.26,
    "v148_primary_problem_evidence": 1.72,
    "v148_primary_evidence_phenomenon": 1.68,
    "v149_secondary_problem_axis": 1.66,
    "v148_core_baseline": 1.05,
    "dynamic_local_source_problem": 1.82,
    "dynamic_local_source_relation": 1.86,
    "dynamic_local_source_roles": 1.76,
    "dynamic_local_source_terms": 1.68,
})

_V146_QUERY_GENERIC = {
    "technical", "uncertainty", "technical uncertainty", "incertitude", "validation",
    "performance", "comparison", "comparaison", "method", "methods", "model", "system",
    "cpu", "gpu", "cuda", "software", "logiciel",
}


def _v146_list(intent: Dict[str, Any], key: str) -> List[str]:
    return [clean_text(x, 120) for x in (intent.get(key) or []) if clean_text(x, 120)]


def _v146_query_has_concept(query: str, intent: Dict[str, Any]) -> int:
    qn = " " + norm(query) + " "
    aliases = intent.get("concept_aliases") if isinstance(intent.get("concept_aliases"), dict) else {}
    ambiguous = {str(x).upper() for x in intent.get("ambiguous_acronyms") or []}
    hits = 0
    for concept in _v146_list(intent, "core_concepts"):
        candidates = aliases.get(concept) or [concept]
        concept_hit = False
        for alias in candidates:
            alias = str(alias)
            # Un acronyme ambigu seul ne valide pas une requête ; l'expansion canonique doit apparaître.
            if alias.upper() in ambiguous and len(alias.split()) == 1:
                continue
            an = norm(alias)
            if an and re.search(rf"(?<![a-z0-9]){re.escape(an).replace(r'\ ', r'\s+')}(?![a-z0-9])", qn):
                concept_hit = True
                break
        if concept_hit:
            hits += 1
    return hits


def is_query_safe_for_intent(query: str, intent_or_profile: Dict[str, Any] | str) -> bool:
    q = clean_text(query, 220)
    qn = norm(q)
    words = [w for w in qn.split() if len(w) >= 3]
    if len(words) < 4 or len(words) > 14:
        return False
    if any(w in ADMIN_QUERY_TERMS for w in words):
        return False
    useful = [w for w in words if w not in NOISE_QUERY_TOKENS and w not in WEAK_ANCHOR_TERMS]
    if len(useful) < 3:
        return False
    if isinstance(intent_or_profile, dict):
        intent = intent_or_profile
        core = _v146_list(intent, "core_concepts")
        core_concept_hits = _v146_query_has_concept(q, intent) if core else 0
        literal_acronyms = [
            str(value)
            for value in (intent.get("literal_source_acronyms") or [])
            if str(value or "").strip()
        ]
        # Un acronyme ambigu n'est pas obligatoire lorsque la requête contient
        # déjà son concept scientifique développé. Par exemple, une preuve qui
        # contient « SAR » doit pouvoir produire « synthetic aperture radar ... ».
        # Exiger aussi le sigle supprimait entièrement les requêtes de certains
        # verrous, avant même le premier appel aux sources bibliographiques.
        if literal_acronyms and not any(
            re.search(
                rf"(?<![a-z0-9]){re.escape(norm(value))}(?![a-z0-9])",
                qn,
            )
            for value in literal_acronyms
        ) and core_concept_hits < 1:
            return False
        if core and core_concept_hits < 1:
            return False
        # Une requête dominée par uncertainty/CPU/GPU n'est jamais envoyée.
        generic_count = sum(1 for x in _V146_QUERY_GENERIC if norm(x) in qn)
        if generic_count >= max(2, len(useful) // 2):
            return False
    return True


def _v146_make_query(parts: List[Any], max_words: int = 11) -> str:
    words: List[str] = []
    seen = set()
    for part in parts:
        values = part if isinstance(part, list) else [part]
        for value in values:
            for token in str(value or "").replace("-", " ").split():
                nt = norm(token)
                if not nt or nt in seen or nt in NOISE_QUERY_TOKENS:
                    continue
                if nt in {"technical", "uncertainty", "cpu", "gpu", "software"}:
                    continue
                seen.add(nt)
                words.append(token)
                if len(words) >= max_words:
                    return clean_text(" ".join(words), 220)
    return clean_text(" ".join(words), 220)


def _v146_relation_parts(intent: Dict[str, Any], concepts: List[str]) -> List[str]:
    """Preserve a relation between extracted concepts within the word budget.

    The canonical concept is kept for the first axis. For subsequent axes, the
    first alternative name already produced by the intent extractor replaces
    the longer canonical label so the whole relation survives the word budget.
    This is entirely data-driven: no project, acronym or domain vocabulary is
    introduced by the query builder.
    """
    aliases = intent.get("concept_aliases")
    aliases = aliases if isinstance(aliases, dict) else {}
    parts: List[str] = []
    for index, concept in enumerate(concepts):
        concept = clean_text(concept, 120)
        if not concept:
            continue
        if index == 0:
            parts.append(concept)
            continue
        canonical = norm(concept)
        selected = concept
        for candidate in aliases.get(concept) or []:
            candidate = clean_text(candidate, 120)
            if candidate and norm(candidate) != canonical:
                selected = candidate
                break
        parts.append(selected)
    return parts


def build_queries_from_intent(intent: Dict[str, Any], max_queries: int = 14) -> List[Dict[str, Any]]:
    intent = dict(intent or {})
    core = _v146_list(intent, "core_concepts")
    primary = _v146_list(intent, "primary_core_concepts") or core[:2]
    primary_norm = {norm(x) for x in primary}
    secondary = [x for x in core if norm(x) not in primary_norm]
    methods = _v146_list(intent, "method_anchors") or _v146_list(intent, "methods")
    phenomena = _v146_list(intent, "phenomenon_anchors")
    tools = _v146_list(intent, "project_tool_terms")
    constraints = [clean_text(x, 120) for x in (intent.get("constraints") or []) if clean_text(x, 120)]

    queries: List[Dict[str, Any]] = []
    seen = set()

    def add(parts: List[Any], kind: str, max_words: int = 11) -> None:
        q = _v146_make_query(parts, max_words=max_words)
        if not q or not is_query_safe_for_intent(q, intent):
            return
        k = norm(q)
        if k in seen:
            return
        seen.add(k)
        queries.append({"query": q, "kind": kind})

    if core:
        # Chercher le problème du verrou, pas seulement son domaine général.
        if secondary:
            add([primary[:2], secondary[:2]], "v148_primary_problem_evidence", max_words=12)
            if len(secondary) >= 3:
                add(
                    [primary[:2], secondary[1:3]],
                    "v149_secondary_problem_axis",
                    max_words=12,
                )
            if phenomena:
                add(
                    [primary[:2], secondary[:1], phenomena[:1]],
                    "v148_primary_evidence_phenomenon",
                    max_words=12,
                )
        if methods:
            add([primary[:2], methods[:2]], "v146_core_methods")
        else:
            add([primary[:2]], "v148_core_baseline")
        add([primary[:2], phenomena[:2]], "v146_core_phenomenon")
        if len(methods) > 2:
            add([primary[:2], methods[2:5], phenomena[:1]], "v146_core_alternative_methods")
        elif constraints:
            add([primary[:2], constraints[:1], phenomena[:1]], "v146_core_constraints")
        # Les noms d'outils internes (par exemple un logiciel projet) ne sont
        # pas utilisés comme requête bibliographique : ils réduisent le rappel
        # et ne constituent pas une preuve scientifique du verrou.
    else:
        # Fallback générique, sans réintroduire le contexte global.
        fallback = _BUILD_QUERIES_V145(intent, max_queries=max_queries)
        for item in fallback:
            if is_query_safe_for_intent(item.get("query", ""), intent):
                queries.append(item)

    # Les requetes prioritaires sont derivees du passage courant. Aucun
    # vocabulaire metier ou domaine client n'est ajoute ici.
    literal_acronyms = _v146_list(intent, "literal_source_acronyms")
    literal_phrases = _v146_list(intent, "literal_source_phrases")
    literal_terms = _v146_list(intent, "literal_source_terms")
    if literal_acronyms and len(secondary) >= 2:
        add(
            [literal_acronyms[:1], _v146_relation_parts(intent, secondary[:3])],
            "dynamic_local_source_relation",
            max_words=12,
        )
    if literal_acronyms or literal_phrases:
        add(
            [literal_acronyms[:2], primary[:1], literal_phrases[:2], phenomena[:1]],
            "dynamic_local_source_problem",
            max_words=12,
        )
        add(
            [literal_acronyms[:2], primary[:2], secondary[:2], literal_phrases[2:5], phenomena[:2]],
            "dynamic_local_source_roles",
            max_words=12,
        )
        add(
            [literal_acronyms[:2], primary[:1], literal_terms[:6]],
            "dynamic_local_source_terms",
            max_words=12,
        )

    return queries[:max_queries]


def select_best_queries_for_intent(
    queries_generated: List[Dict[str, Any]],
    intent: Dict[str, Any],
    max_queries: int = 3,
) -> List[Dict[str, Any]]:
    max_queries = max(1, int(max_queries or 3))
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for raw in queries_generated or []:
        item = dict(raw) if isinstance(raw, dict) else {"query": str(raw), "kind": "auto"}
        q = clean_text(item.get("query"), 220)
        if not is_query_safe_for_intent(q, intent):
            continue
        k = norm(q)
        if not k or k in seen:
            continue
        seen.add(k)
        core_hits = _v146_query_has_concept(q, intent)
        kind = str(item.get("kind") or "auto")
        role_bonus = KIND_PRIORITY.get(kind, 0.55)
        method_hits = sum(1 for m in _v146_list(intent, "method_anchors") if norm(m) in norm(q))
        phenomenon_hits = sum(1 for p in _v146_list(intent, "phenomenon_anchors") if norm(p) in norm(q))
        item.update({
            "query": q,
            "core_concept_count": core_hits,
            "method_role_count": method_hits,
            "phenomenon_role_count": phenomenon_hits,
            "selection_score": round(role_bonus + 0.55 * core_hits + 0.16 * method_hits + 0.16 * phenomenon_hits, 4),
        })
        candidates.append(item)

    candidates.sort(key=lambda x: x.get("selection_score", 0.0), reverse=True)
    selected: List[Dict[str, Any]] = []
    families = set()
    problem_query_kinds = {
        "v148_primary_problem_evidence",
        "v148_primary_evidence_phenomenon",
        "v149_secondary_problem_axis",
        "v146_core_phenomenon",
    }
    for item in candidates:
        if len(selected) >= max_queries:
            break
        q = item["query"]
        kind = str(item.get("kind") or "")
        too_similar = any(
            _query_similarity(q, other["query"]) >= 0.72
            and not (
                kind in problem_query_kinds
                and str(other.get("kind") or "") in problem_query_kinds
                and kind != str(other.get("kind") or "")
            )
            for other in selected
        )
        if too_similar:
            continue
        family = kind.replace("v146_", "").replace("v148_", "").replace("v149_", "")
        if family in families:
            continue
        families.add(family)
        selected.append(item)
    return selected[:max_queries]


def attach_queries_to_intent(intent: Dict[str, Any], max_queries: int = 14) -> Dict[str, Any]:
    out = dict(intent or {})
    out["cir_domain_profile"] = _profile_for_intent(out)
    out["search_queries"] = build_queries_from_intent(out, max_queries=max_queries)
    out["query_builder_version"] = "v155_section_aware_lock_queries"
    return out
