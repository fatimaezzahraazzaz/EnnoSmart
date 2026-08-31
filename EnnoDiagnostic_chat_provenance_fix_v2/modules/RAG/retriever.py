# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Retriever fidèle au NLP et renforcé pour le chat documentaire.

Objectifs :
- quand role_filter="verrou", chercher uniquement dans les chunks role="verrou" ;
- conserver les supporting_passages indexés pour la traçabilité ;
- favoriser les chunks principaux NLP dans le classement ;
- l'identité des groupes ``lock_group_id`` vient uniquement du NLP avant
  Frascati et n'est jamais recalculée par le RAG ;
- gérer correctement les questions multi-rôles (méthode + résultat + limite...) ;
- réduire la confusion entre preuves projet, état de l'art et commentaires consultant ;
- permettre un filtrage strict sur un ou plusieurs documents sélectionnés ;
- ne contenir aucun hardcoding propre à un projet, un verrou, une méthode ou un score.
"""

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .config import DEFAULT_TOP_K
from .project_store import ProjectStore
from .vector_store import RAGVectorStore


ROLE_KEYWORDS = {
    "verrou": [
        "verrou", "verrous", "difficulté", "difficultés", "blocage",
        "incertitude", "risque", "problème technique", "problèmes techniques",
        "frascati",
    ],
    "objectif": [
        "objectif", "objectifs", "but", "finalité", "vise", "visé",
        "objectif global",
    ],
    "methode": [
        "méthode", "méthodes", "méthodologie", "démarche", "démarches",
        "travaux", "solution", "solutions", "approche", "approches",
        "expérimentation", "expérimentations", "protocole", "protocoles",
        "testé", "testées", "réalisé", "réalisées", "mené", "menées",
    ],
    "resultat": [
        "résultat", "résultats", "performance", "performances", "test", "tests",
        "essai", "essais", "évaluation", "évaluations", "mesure", "mesures",
        "métrique", "métriques", "compilabilité", "couverture",
    ],
    "etat_art": [
        "état de l'art", "etat de l'art", "article", "articles",
        "bibliographie", "travaux existants", "littérature", "publication",
        "publications", "scientifique", "scientifiques",
    ],
    "parametre": [
        "paramètre", "paramètres", "configuration", "configurations",
        "seuil", "seuils", "valeur", "valeurs", "hyperparamètre",
        "hyperparamètres",
    ],
    "contribution": [
        "contribution", "contributions", "apport", "apports",
        "innovation", "innovations", "avancée", "avancées",
    ],
    "limite": [
        "limite", "limites", "contrainte", "contraintes", "insuffisance",
        "insuffisances", "non conforme", "non résolu", "non résolue",
        "non résolus", "non résolues",
    ],
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


# Marqueurs GENERIQUES de provenance. Aucun nom de projet/méthode/modèle n'est codé ici.
_PROJECT_ORIGIN_TOKENS = (
    "preuve_projet",
    "project_current",
    "current_project",
    "projet_courant",
    "projet_client",
    "project",
    "projet",
    "client",
    "internal",
    "interne",
    "team",
    "equipe",
)

_EXTERNAL_ORIGIN_TOKENS = (
    "etat_art",
    "état_art",
    "etat art",
    "état de l'art",
    "bibliograph",
    "literature",
    "littérature",
    "external",
    "externe",
    "article",
    "publication",
    "scientific",
    "scientifique",
)

_CONSULTANT_ORIGIN_TOKENS = (
    "consultant",
    "consulting",
    "question_consultant",
    "commentaire_consultant",
)

_PROJECT_ACTION_QUESTION_TOKENS = (
    "l'équipe",
    "lequipe",
    "équipe",
    "equipe",
    "réellement réalisé",
    "réellement réalisées",
    "réalisé par",
    "réalisées par",
    "mené par",
    "menées par",
    "travaux réalisés",
    "travaux menés",
    "expérimentations menées",
    "a testé",
    "ont testé",
    "a réalisé",
    "ont réalisé",
    "résultats observés",
)

_STATE_ART_QUESTION_TOKENS = (
    "état de l'art",
    "etat de l'art",
    "littérature",
    "literature",
    "article",
    "articles",
    "publication",
    "publications",
    "travaux existants",
)


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _contains_any(text: str, tokens: Iterable[str]) -> bool:
    value = _norm(text)
    return any(token in value for token in tokens)


def detect_role_filters(question: str) -> List[str]:
    """
    Retourne TOUS les rôles explicitement demandés par la question.

    Ancien comportement :
        une question contenant "expérimentations + résultats + limites"
        était réduite au premier rôle trouvé.

    Nouveau comportement :
        ["methode", "resultat", "limite"]
    """
    q = _norm(question)
    roles: List[str] = []
    for role, keywords in ROLE_KEYWORDS.items():
        if any(keyword in q for keyword in keywords):
            roles.append(role)
    return roles


def detect_role_filter(question: str) -> Optional[str]:
    """
    Compatibilité avec l'ancienne API.

    - 0 rôle -> None
    - 1 rôle -> ce rôle
    - plusieurs rôles -> None pour éviter de sélectionner arbitrairement
      le premier rôle.
    """
    roles = detect_role_filters(question)
    return roles[0] if len(roles) == 1 else None


def _section_from_role(role: Optional[str]) -> str:
    if role in {
        "objectif", "verrou", "methode", "resultat",
        "etat_art", "parametre", "contribution", "limite",
    }:
        return role
    return "chat"


def _question_intent(question: str) -> str:
    """
    Classe uniquement l'intention documentaire de la QUESTION.
    Ceci ne décide jamais qu'un passage est vrai ou faux.
    """
    q = _norm(question)
    asks_project_action = _contains_any(q, _PROJECT_ACTION_QUESTION_TOKENS)
    asks_state_art = _contains_any(q, _STATE_ART_QUESTION_TOKENS)

    if asks_project_action and asks_state_art:
        return "compare_project_vs_external"
    if asks_project_action:
        return "project_action"
    if asks_state_art:
        return "state_art"
    return "general"


def _metadata_origin(meta: Dict[str, Any]) -> str:
    """
    Déduit une origine documentaire à partir des METADONNEES déjà indexées.

    Important :
    - aucun nom de projet, modèle ou méthode n'est testé ;
    - le texte du chunk n'est pas utilisé pour inventer une provenance ;
    - si les métadonnées ne permettent pas de trancher, retourne "unknown".
    """
    dtype = _norm(meta.get("document_type"))

    fields = (
        "source_policy",
        "evidence_scope",
        "evidence_origin",
        "provenance_role",
        "source_role",
        "content_origin",
        "origin",
        "document_scope",
        "document_type",
        "role",
        "final_role",
        "quality_status",
    )
    blob = " | ".join(_norm(meta.get(key)) for key in fields if meta.get(key) is not None)

    # Le type documentaire est un signal fort lorsqu'il est explicite.
    if dtype in CORE_TYPES:
        return "project"
    if dtype == "etat_art_bibliographie":
        return "external"

    if _contains_any(blob, _CONSULTANT_ORIGIN_TOKENS):
        return "consultant"
    if _contains_any(blob, _EXTERNAL_ORIGIN_TOKENS):
        return "external"
    if _contains_any(blob, _PROJECT_ORIGIN_TOKENS):
        return "project"
    return "unknown"


def _enrich_retrieval_metadata(
    item: Dict[str, Any],
    *,
    question: str,
    requested_roles: Sequence[str],
) -> Dict[str, Any]:
    """
    Ajoute des métadonnées de récupération NON destructives.
    Les métadonnées d'origine restent intactes.
    """
    output = dict(item)
    meta = dict(output.get("metadata") or {})
    origin = _metadata_origin(meta)

    meta["retrieval_origin"] = origin
    meta["retrieval_question_intent"] = _question_intent(question)
    if requested_roles:
        meta["retrieval_requested_roles"] = ",".join(requested_roles)

    output["metadata"] = meta
    return output


def _score(
    item: Dict[str, Any],
    section: str,
    *,
    question: str = "",
    requested_roles: Optional[Sequence[str]] = None,
) -> float:
    meta = item.get("metadata", {}) or {}

    try:
        distance = float(item.get("distance") or 0.0)
    except Exception:
        distance = 0.0

    dtype = _norm(meta.get("document_type"))
    role = _norm(meta.get("role"))
    final_role = _norm(meta.get("final_role"))
    qstatus = _norm(meta.get("quality_status"))
    decision = _norm(meta.get("frascati_decision"))
    candidate_level = _norm(meta.get("verrou_candidate_level"))
    verrou_source = _norm(meta.get("verrou_source"))
    explicit = bool(meta.get("explicit_verrou"))
    chunk_level = _norm(meta.get("chunk_level"))

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

    roles = set(requested_roles or [])
    if section != "chat" and role == section:
        score += 1.0
    elif roles and role in roles:
        score += 1.0

    if dtype in CORE_TYPES:
        score += 0.35
    elif dtype in SECONDARY_TYPES:
        score -= 0.15
    elif dtype in CONTEXT_TYPES:
        score -= 0.70

    # Correction générique de provenance pour le CHAT.
    #
    # Une question "qu'a réellement fait l'équipe ?" doit favoriser les
    # preuves projet. Les sources externes ne sont PAS supprimées : elles
    # restent disponibles pour une comparaison explicite avec l'état de l'art.
    intent = _question_intent(question)
    origin = _metadata_origin(meta)

    if intent == "project_action":
        if origin == "project":
            score += 0.80
        elif origin == "external":
            score -= 1.10
        elif origin == "consultant":
            score -= 0.70

    elif intent == "state_art":
        if origin == "external":
            score += 0.80
        elif origin == "project":
            score -= 0.10

    elif intent == "compare_project_vs_external":
        # Les deux familles doivent rester visibles, mais leur provenance
        # reste explicitement marquée dans les métadonnées.
        if origin in {"project", "external"}:
            score += 0.30
        elif origin == "consultant":
            score -= 0.50

    if section == "verrou":
        if (
            explicit
            or candidate_level == "strong_candidate"
            or final_role == "verrou_probable"
            or decision == "verrou_probable"
        ):
            score += 1.10
        elif (
            candidate_level in {"to_validate", "implicit_to_validate"}
            or "verifier" in final_role
            or decision == "verrou_a_verifier"
        ):
            score += 0.70

        if (
            qstatus == "frascati_universal_theme_to_validate"
            or verrou_source == "universal_theme_reconstruction"
        ):
            score += 0.25

        if candidate_level == "non_verrou_context":
            score -= 1.50

    return score


def _document_metadata_filter(
    document_filter: Optional[str | Sequence[str]],
) -> Optional[Dict[str, Any]]:
    """
    Transforme le document sélectionné par l'UI en filtre Chroma.

    Hypothèse compatible avec l'index actuel :
        metadata["document"] contient le nom du document.

    Aucun filtre n'est appliqué lorsque document_filter est None/vide.
    """
    if document_filter is None:
        return None

    if isinstance(document_filter, str):
        docs = [document_filter.strip()] if document_filter.strip() else []
    else:
        docs = [str(doc).strip() for doc in document_filter if str(doc).strip()]

    docs = list(dict.fromkeys(docs))
    if not docs:
        return None
    if len(docs) == 1:
        return {"document": docs[0]}
    return {"document": {"$in": docs}}


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
        role_filter: Optional[str | Sequence[str]] = None,
        document_filter: Optional[str | Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recherche principale.

        Compatibilité :
        - role_filter peut toujours être une chaîne unique ;
        - il accepte désormais aussi plusieurs rôles ;
        - document_filter est optionnel et n'altère pas les anciens appels.
        """
        explicit_roles: List[str] = []

        if isinstance(role_filter, str):
            value = role_filter.strip()
            if value and value.lower() not in {"auto", "none"}:
                if value in ROLE_KEYWORDS:
                    explicit_roles = [value]
        elif role_filter:
            explicit_roles = [
                str(role).strip()
                for role in role_filter
                if str(role).strip() in ROLE_KEYWORDS
            ]

        roles = explicit_roles or detect_role_filters(question)

        if len(roles) == 1:
            return self.search_exact_role(
                role=roles[0],
                query=question,
                top_k=top_k,
                document_filter=document_filter,
            )

        if len(roles) > 1:
            return self.search_multi_role(
                roles=roles,
                query=question,
                top_k=top_k,
                document_filter=document_filter,
            )

        return self.search_chat(
            query=question,
            top_k=top_k,
            document_filter=document_filter,
        )

    def search_exact_role(
        self,
        role: str,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        document_filter: Optional[str | Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        metadata_filter = _document_metadata_filter(document_filter)

        # On demande un pool de candidats plus large au vector store pour
        # permettre au reranking local d'être réellement utile.
        candidate_k = max(int(top_k) * 6, int(top_k), 1)

        res = self.vector_store.search(
            collection_name=self.collection_name,
            query=query,
            top_k=candidate_k,
            role_filter=role,
            document_type_exclude=[],
            metadata_filter=metadata_filter,
            oversample=2,
            return_candidate_pool=True,
        )

        arr = sorted(
            res,
            key=lambda x: _score(
                x,
                role,
                question=query,
                requested_roles=[role],
            ),
            reverse=True,
        )

        return [
            _enrich_retrieval_metadata(
                item,
                question=query,
                requested_roles=[role],
            )
            for item in arr[:top_k]
        ]

    def search_multi_role(
        self,
        roles: Sequence[str],
        query: str,
        top_k: int = DEFAULT_TOP_K,
        document_filter: Optional[str | Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recherche multi-rôles équilibrée.

        Exemple :
            "quelles expérimentations, quels résultats et quelles limites ?"
        récupère méthode + résultat + limite au lieu de choisir seulement
        le premier mot-clé reconnu.
        """
        requested_roles = list(dict.fromkeys(
            role for role in roles if role in ROLE_KEYWORDS
        ))
        if not requested_roles:
            return self.search_chat(
                query=query,
                top_k=top_k,
                document_filter=document_filter,
            )

        metadata_filter = _document_metadata_filter(document_filter)
        candidate_k = max(int(top_k) * 8, len(requested_roles) * 4, int(top_k), 1)

        res = self.vector_store.search(
            collection_name=self.collection_name,
            query=query,
            top_k=candidate_k,
            role_filter=requested_roles,
            document_type_exclude=[],
            metadata_filter=metadata_filter,
            oversample=2,
            return_candidate_pool=True,
        )

        arr = sorted(
            res,
            key=lambda x: _score(
                x,
                "chat",
                question=query,
                requested_roles=requested_roles,
            ),
            reverse=True,
        )

        # Première passe : garantir si possible au moins une preuve par rôle.
        selected: List[Dict[str, Any]] = []
        selected_ids = set()

        for requested_role in requested_roles:
            for item in arr:
                item_role = _norm((item.get("metadata") or {}).get("role"))
                item_id = str(item.get("id") or "")
                if item_id in selected_ids:
                    continue
                if item_role == requested_role:
                    selected.append(item)
                    selected_ids.add(item_id)
                    break
            if len(selected) >= top_k:
                break

        # Deuxième passe : compléter par le meilleur score global.
        for item in arr:
            if len(selected) >= top_k:
                break
            item_id = str(item.get("id") or "")
            if item_id in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(item_id)

        return [
            _enrich_retrieval_metadata(
                item,
                question=query,
                requested_roles=requested_roles,
            )
            for item in selected[:top_k]
        ]

    def search_chat(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        document_filter: Optional[str | Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        metadata_filter = _document_metadata_filter(document_filter)
        candidate_k = max(int(top_k) * 8, int(top_k), 1)

        res = self.vector_store.search(
            collection_name=self.collection_name,
            query=query,
            top_k=candidate_k,
            role_filter=None,
            document_type_exclude=list(CONTEXT_TYPES),
            metadata_filter=metadata_filter,
            oversample=2,
            return_candidate_pool=True,
        )

        arr = sorted(
            res,
            key=lambda x: _score(
                x,
                "chat",
                question=query,
                requested_roles=[],
            ),
            reverse=True,
        )

        out: List[Dict[str, Any]] = []
        per_doc = defaultdict(int)

        for item in arr:
            doc = str((item.get("metadata") or {}).get("document") or "")

            # Si un document précis est sélectionné, ne pas pénaliser ce document
            # avec une règle de diversité inter-documents.
            max_per_doc = top_k if document_filter else 3

            if per_doc[doc] >= max_per_doc:
                continue

            per_doc[doc] += 1
            out.append(
                _enrich_retrieval_metadata(
                    item,
                    question=query,
                    requested_roles=[],
                )
            )
            if len(out) >= top_k:
                break

        return out

    def search_for_section(
        self,
        section: str,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        document_filter: Optional[str | Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        # Compatibilité avec anciens appels.
        role = section if section in ROLE_KEYWORDS else None
        if role:
            return self.search_exact_role(
                role=role,
                query=query,
                top_k=top_k,
                document_filter=document_filter,
            )
        return self.search_chat(
            query=query,
            top_k=top_k,
            document_filter=document_filter,
        )
