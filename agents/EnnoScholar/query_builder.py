# -*- coding: utf-8 -*-
from __future__ import annotations

"""
query_builder.py — EnnoScholar V131

Construction de requêtes scientifiques multi-domaines à partir :
- du verrou reformulé ;
- du domaine CIR détecté ;
- de la nomenclature CIR complète ;
- d'un catalogue de profils génériques.

But :
- éviter les requêtes génériques ("technical uncertainty", "service", "performance") ;
- ne pas spécialiser uniquement au bâtiment biosourcé ;
- couvrir tous les domaines CIR de la nomenclature.
"""

from typing import Any, Dict, List

from .utils import clean_text, dedupe_keep_order, norm, tokenize
from .cir_domain_query_catalog import build_cir_domain_queries, get_cir_domain_profile


BAD_QUERY_TOKENS = {
    "question", "qualification", "permet", "permet-elle", "maitrise", "maîtrise",
    "systeme", "système", "solution", "solutions", "possible", "implicite",
    "probable", "non", "sous", "contrainte", "contraintes", "projet", "travaux",
    "documents", "sources", "source", "groupe", "agent", "diagnostic",
    "technical", "uncertainty", "technical uncertainty", "performance", "service",
    "architecture", "engineering", "method", "system", "study",
}

DANGEROUS_SINGLE_WORDS = {
    "vent", "service", "isolation", "diffusion", "rei", "architecture",
    "performance", "system", "technical", "engineering", "service",
}


def _intent_text(intent: Dict[str, Any]) -> str:
    return " ".join([
        str(intent.get("backend_enrichment_profile") or ""),
        str(intent.get("enrichment_profile") or ""),
        str(intent.get("profile") or ""),
        str(intent.get("verrou_title") or ""),
        str(intent.get("original_title") or ""),
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
    """Texte court prioritaire pour choisir le profil : évite que le contexte global pollue le verrou."""
    return " ".join([
        str(intent.get("verrou_title") or ""),
        str(intent.get("original_title") or ""),
        str(intent.get("title") or ""),
    ])


def _profile_for_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    """
    V131 : le profil est choisi d'abord à partir du titre du verrou.
    Si le titre porte clairement sur inertie/hygro/feu/connecteurs, on ignore le domaine global
    parfois trop large ou pollué par les autres verrous du dossier.
    """
    title_profile = get_cir_domain_profile({}, _title_priority_text(intent))
    title_profile_id = str(title_profile.get("profile_id") or "")
    if title_profile_id not in {"generic", "mechanical_civil_engineering", "sociology_geography_urbanism"}:
        return title_profile
    return get_cir_domain_profile(_domain_detection_from_intent(intent), _intent_text(intent))



def detect_scholar_profile(intent: Dict[str, Any]) -> str:
    return _profile_for_intent(intent).get("profile_id", "generic")


def _filtered_words(text: Any) -> List[str]:
    words = []
    for t in tokenize(text):
        nt = norm(t)
        if not nt or nt in BAD_QUERY_TOKENS:
            continue
        if nt in DANGEROUS_SINGLE_WORDS:
            continue
        if len(nt) < 3:
            continue
        if t not in words:
            words.append(t)
    return words


def _query(parts: List[str], max_words: int = 10) -> str:
    words = []
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


def is_query_safe_for_intent(query: str, intent_or_profile: Dict[str, Any] | str) -> bool:
    q = norm(query)
    if len(q) < 10:
        return False
    if any(x in q for x in ["technical uncertainty", "question qualification", "simple engineering"]):
        return False

    # Les requêtes avec seulement 1 ou 2 mots sont trop instables.
    words = [w for w in q.split() if len(w) >= 4]
    if len(words) < 3:
        return False

    if len(words) <= 4 and any(w in DANGEROUS_SINGLE_WORDS for w in words):
        return False

    return True


def build_queries_from_intent(intent: Dict[str, Any], max_queries: int = 8) -> List[Dict[str, Any]]:
    intent = dict(intent or {})

    # 1) Profil nomenclature CIR prioritaire, recalculé titre-first en V131.
    profile = _profile_for_intent(intent)
    intent["cir_domain_profile"] = profile

    q: List[Dict[str, Any]] = []
    seen = set()

    def add(query: str, kind: str):
        query = clean_text(query, 220)
        if not is_query_safe_for_intent(query, intent):
            return
        k = norm(query)
        if not k or k in seen:
            return
        seen.add(k)
        q.append({"query": query, "kind": kind})

    # 2) Requêtes contrôlées par domaine.
    for item in build_cir_domain_queries(intent, max_queries=max_queries):
        add(item.get("query", ""), item.get("kind", "cir_domain_profile_query"))

    # 3) Requêtes construites depuis objet + phénomène, mais nettoyées.
    obj = clean_text(intent.get("technical_object"), 160)
    phen = clean_text(intent.get("phenomenon"), 160)
    prob = clean_text(intent.get("scientific_problem"), 220)
    constraints = intent.get("constraints") or []
    methods = intent.get("methods") or []
    key_terms = intent.get("key_terms_en") or intent.get("key_terms_fr") or []

    add(_query([obj, phen], max_words=9), "object_phenomenon_clean")
    add(_query([prob], max_words=9), "scientific_problem_clean")

    if constraints:
        add(_query([obj, phen, " ".join(map(str, constraints[:2]))], max_words=9), "constraints_clean")

    if methods:
        add(_query([obj, phen, " ".join(map(str, methods[:3]))], max_words=9), "methods_clean")

    if key_terms:
        add(_query([" ".join(map(str, key_terms[:10]))], max_words=9), "key_terms_clean")

    # 4) Fallback si vraiment rien : domaine + mots utiles.
    if not q:
        terms = dedupe_keep_order(_filtered_words(_intent_text(intent)), 8)
        domain_terms = profile.get("positive_terms") or profile.get("domain_terms") or []
        add(" ".join(list(map(str, domain_terms[:5])) + terms[:5]), "fallback_domain_terms")

    return q[:max_queries]


def attach_queries_to_intent(intent: Dict[str, Any], max_queries: int = 8) -> Dict[str, Any]:
    out = dict(intent or {})
    out["cir_domain_profile"] = _profile_for_intent(out)
    out["search_queries"] = build_queries_from_intent(out, max_queries=max_queries)
    out["query_builder_version"] = "v131_profile_priority_no_context_leak"
    return out
