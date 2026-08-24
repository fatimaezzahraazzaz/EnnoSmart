# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Retriever fidèle au NLP.

Objectif :
- quand role_filter="verrou", chercher uniquement dans les chunks role="verrou" ;
- conserver les supporting_passages indexés pour la traçabilité ;
- favoriser les chunks principaux NLP dans le classement ;
- l'identité des groupes ``lock_group_id`` vient uniquement du NLP avant
  Frascati et n'est jamais recalculée par le RAG.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional

from .config import DEFAULT_TOP_K
from .project_store import ProjectStore
from .vector_store import RAGVectorStore


ROLE_KEYWORDS = {
    "verrou": [
        "verrou", "verrous", "difficulté", "difficultés", "blocage",
        "incertitude", "risque", "problème technique", "frascati",
    ],
    "objectif": ["objectif", "objectifs", "but", "finalité", "vise", "visé", "objectif global"],
    "methode": ["méthode", "méthodologie", "démarche", "travaux", "solution", "approche", "expérimentation", "protocole"],
    "resultat": ["résultat", "résultats", "performance", "test", "essai", "évaluation", "mesure", "métrique"],
    "etat_art": ["état de l'art", "etat de l'art", "article", "bibliographie", "travaux existants", "littérature"],
    "parametre": ["paramètre", "paramètres", "configuration", "seuil", "valeur"],
    "contribution": ["contribution", "apport", "innovation", "avancée"],
    "limite": ["limite", "contrainte", "insuffisance", "non conforme"],
}


CORE_TYPES = {
    "concept_projet",
    "brevet",
    "preuve_depot_brevet",
    "rapport_test",
    "note_projet",
    "presentation_projet",
    "methodologie_protocole",
    "document_projet",
    "resultats_mesures",
}

CONTEXT_TYPES = {
    "norme_reglementation",
    "plan_schema",
    "administratif",
    "template_formulaire",
}

SECONDARY_TYPES = {
    "notice_memoire_technique",
    "etat_art_bibliographie",
}


def detect_role_filter(question: str) -> Optional[str]:
    q = str(question or "").lower()
    for role, kws in ROLE_KEYWORDS.items():
        if any(k in q for k in kws):
            return role
    return None


def _section_from_role(role: Optional[str]) -> str:
    if role in {"objectif", "verrou", "methode", "resultat", "etat_art", "parametre", "contribution", "limite"}:
        return role
    return "chat"


def _score(item: Dict[str, Any], section: str) -> float:
    meta = item.get("metadata", {}) or {}

    try:
        distance = float(item.get("distance") or 0.0)
    except Exception:
        distance = 0.0

    dtype = str(meta.get("document_type") or "unknown_document")
    role = str(meta.get("role") or "")
    final_role = str(meta.get("final_role") or "")
    qstatus = str(meta.get("quality_status") or "")
    decision = str(meta.get("frascati_decision") or "")
    candidate_level = str(meta.get("verrou_candidate_level") or "")
    verrou_source = str(meta.get("verrou_source") or "")
    explicit = bool(meta.get("explicit_verrou"))
    chunk_level = str(meta.get("chunk_level") or "")

    try:
        rank = float(meta.get("rank_score") or 0.0)
    except Exception:
        rank = 0.0

    try:
        fr_score = float(meta.get("frascati_score") or 0.0)
    except Exception:
        fr_score = 0.0

    score = (1.0 / (1.0 + distance)) + 0.25 * rank + 0.20 * fr_score

    # Favorise les chunks principaux NLP exacts.
    if chunk_level == "nlp_main_item":
        score += 1.0

    # Favorise le rôle demandé.
    if role == section:
        score += 1.0

    if dtype in CORE_TYPES:
        score += 0.35
    elif dtype in SECONDARY_TYPES:
        score -= 0.15
    elif dtype in CONTEXT_TYPES:
        score -= 0.70

    if section == "verrou":
        if explicit or candidate_level == "strong_candidate" or final_role == "verrou_probable" or decision == "verrou_probable":
            score += 1.10
        elif candidate_level in {"to_validate", "implicit_to_validate"} or "verifier" in final_role or decision == "verrou_a_verifier":
            score += 0.70

        if qstatus == "frascati_universal_theme_to_validate" or verrou_source == "universal_theme_reconstruction":
            score += 0.25

        if candidate_level == "non_verrou_context":
            score -= 1.50

    return score


class EnnoRetriever:
    def __init__(
        self,
        organisme: str,
        project: str,
        year: Optional[str | int] = None,
        annee: Optional[str | int] = None,
        subproject: Optional[str] = None,
    ):
        self.store = ProjectStore(
            organisme,
            project,
            subproject=subproject,
            year=year,
            annee=annee,
        ).ensure()
        self.collection_name = self.store.collection_name
        self.vector_store = RAGVectorStore(self.store.chroma_dir)

    def search(
        self,
        question: str,
        top_k: int = DEFAULT_TOP_K,
        role_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        role = role_filter if role_filter is not None else detect_role_filter(question)

        if role in {"", "auto", "none", "None"}:
            role = detect_role_filter(question)

        section = _section_from_role(role)

        # Si l'utilisateur choisit un rôle, on cherche uniquement ce rôle.
        # C'est la correction principale.
        if role in ROLE_KEYWORDS:
            return self.search_exact_role(role=role, query=question, top_k=top_k)

        return self.search_chat(query=question, top_k=top_k)

    def search_exact_role(self, role: str, query: str, top_k: int = DEFAULT_TOP_K) -> List[Dict[str, Any]]:
        res = self.vector_store.search(
            collection_name=self.collection_name,
            query=query,
            top_k=max(int(top_k), 1),
            role_filter=role,
            document_type_exclude=[],
            oversample=6,
        )

        arr = sorted(res, key=lambda x: _score(x, role), reverse=True)
        return arr[:top_k]

    def search_chat(self, query: str, top_k: int = DEFAULT_TOP_K) -> List[Dict[str, Any]]:
        res = self.vector_store.search(
            collection_name=self.collection_name,
            query=query,
            top_k=max(int(top_k), 1),
            role_filter=None,
            document_type_exclude=list(CONTEXT_TYPES),
            oversample=6,
        )

        arr = sorted(res, key=lambda x: _score(x, "chat"), reverse=True)

        out = []
        per_doc = defaultdict(int)
        for item in arr:
            doc = str((item.get("metadata") or {}).get("document") or "")
            if per_doc[doc] >= 3:
                continue
            per_doc[doc] += 1
            out.append(item)
            if len(out) >= top_k:
                break

        return out

    def search_for_section(self, section: str, query: str, top_k: int = DEFAULT_TOP_K) -> List[Dict[str, Any]]:
        # Compatibilité avec anciens appels.
        role = section if section in ROLE_KEYWORDS else None
        if role:
            return self.search_exact_role(role=role, query=query, top_k=top_k)
        return self.search_chat(query=query, top_k=top_k)
