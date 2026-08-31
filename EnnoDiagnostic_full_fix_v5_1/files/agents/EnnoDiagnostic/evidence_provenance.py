# -*- coding: utf-8 -*-
# ENNODIAG_FULL_FIX_V5_20260829 — reference-like evidence never becomes project fact
from __future__ import annotations

"""Qualification de provenance et de maturité des preuves EnnoDiagnostic.

Ce module V2 est volontairement indépendant de la logique qui détecte/regroupe les
verrous. Il ajoute une deuxième dimension à une preuve : qui produit le fait ?

Une preuve peut donc rester utile à la détection d'un verrou/à l'état de l'art,
tout en étant interdite comme preuve d'un objectif, d'une démarche ou d'un
résultat réalisé par le projet courant.

Le rôle sémantique produit par le NLP est l'autorité métier. Ce module ne le
reclasse pas avec des listes de mots : il vérifie seulement le corpus, la
provenance et, lorsqu'il existe, le statut d'exécution structuré. Les anciens
signaux textuels ne servent plus qu'à qualifier une provenance sans métadonnées.
"""

import re
import unicodedata
from typing import Any, Dict

PROV_PROJECT_DIRECT = "project_direct"
PROV_EXTERNAL_LITERATURE = "external_literature"
PROV_AMBIGUOUS = "ambiguous_current_dossier"
PROV_HISTORICAL = "historical_project"
PROV_CALCULATION = "calculated_assessment"

ACTOR_PROJECT = "project_team"
ACTOR_EXTERNAL = "external_authors"
ACTOR_UNKNOWN = "unknown"
ACTOR_HISTORICAL = "project_team_previous_year"
ACTOR_BACKEND = "backend_calculation"

EXEC_PLANNED = "planned"
EXEC_PROPOSED = "proposed"
EXEC_IMPLEMENTED = "implemented"
EXEC_EXPERIMENTED = "experimented"
EXEC_OBSERVED = "observed"
EXEC_MEASURED = "measured"
EXEC_ACTIVE_CONSTRAINT = "active_constraint"
EXEC_UNKNOWN = "unknown"

_ALLOWED_EXECUTION_STATUSES = {
    EXEC_PLANNED,
    EXEC_PROPOSED,
    EXEC_IMPLEMENTED,
    EXEC_EXPERIMENTED,
    EXEC_OBSERVED,
    EXEC_MEASURED,
    EXEC_ACTIVE_CONSTRAINT,
    EXEC_UNKNOWN,
}

_ALLOWED_ORIGINS = {
    PROV_PROJECT_DIRECT,
    PROV_EXTERNAL_LITERATURE,
    PROV_AMBIGUOUS,
    PROV_HISTORICAL,
    PROV_CALCULATION,
}

_STATE_OF_ART_SECTION_RE = re.compile(
    r"\b(?:etat de l['’ ]?art|state of the art|related works?|related work|"
    r"literature review|revue de la litterature|revue de litterature|"
    r"travaux connexes|bibliograph(?:ie|y)|references?|background research|"
    r"existing work|prior art)\b",
    re.I,
)

_EXTERNAL_ORIGIN_RE = re.compile(
    r"\b(?:external[_ -]?literature|literature|scientific[_ -]?article|paper|"
    r"state[_ -]?of[_ -]?the[_ -]?art|bibliograph(?:y|ie)|reference)\b",
    re.I,
)

_THIRD_PARTY_ATTRIBUTION_RE = re.compile(
    r"\b(?:les auteurs?|the authors?|according to|selon (?:les auteurs?|l['’]etude|"
    r"l['’]article)|reported by|proposed by|demonstrated by|shown by|"
    r"the paper|previous work|prior work|certaines [eé]tudes (?:sugg[eè]rent|montrent|indiquent)|"
    r"des [eé]tudes (?:r[eé]centes )?(?:sugg[eè]rent|montrent|indiquent)|"
    r"travaux anterieurs|et al\.)\b",
    re.I,
)

# Compatibilité des anciens packs sans ``execution_status``. Ces expressions ne
# choisissent jamais la section ni le rôle : elles distinguent seulement une
# intention d'un fait accompli après la décision sémantique du NLP.
_LEGACY_PLANNED_RE = re.compile(
    r"\b(?:objectif|cible|prévu|prevu|planifié|planifie|reste à|reste a|"
    r"doit|doivent|devons|à tester|a tester|à mesurer|a mesurer|"
    r"expected|planned|target|to be tested|to be measured)\b",
    re.I,
)
_LEGACY_EXPERIMENTED_RE = re.compile(
    r"\b(?:avons|a été|a ete|ont été|ont ete|was|were)\s+"
    r"(?:testé|teste|testés|testes|évalué|evalue|évalués|evalues|"
    r"comparé|compare|comparés|compares|expérimenté|experimente|tested|evaluated|compared)\b",
    re.I,
)
_LEGACY_MEASURED_RE = re.compile(
    r"\b(?:mesuré|mesuree?|mesurés|mesurees|measured|quantifié|quantifie|quantified)\b",
    re.I,
)
_LEGACY_OBSERVED_RE = re.compile(
    r"\b(?:observé|observe|observés|observes|constaté|constate|"
    r"obtenu|obtenue|obtenus|obtenues|observed|obtained)\b",
    re.I,
)
_LEGACY_ACTIVE_CONSTRAINT_RE = re.compile(
    r"\b(?:contrainte|limite|maximum|minimum|seuil|plafond|fenetre|"
    r"fix[eé]e?\s+[aà]|r[eé]gl[eé]e?\s+[aà]|compris entre|"
    r"inf[eé]rieur(?:e)?\s+[aà]|sup[eé]rieur(?:e)?\s+[aà])\b",
    re.I,
)

_PROJECT_SECTION_RE = re.compile(
    r"\b(?:travaux realises?|travaux menes?|demarche experimentale|"
    r"protocole experimental|experimentations?|essais? realises?|"
    r"resultats? obtenus?|resultats? du projet|resultats? et analyse des donnees|"
    r"description des travaux|conclusion et contribution|nos travaux|"
    r"developpements? realises?|mise en oeuvre|implementation du projet|"
    r"compte rendu|reunion technique|synthese des travaux)\b",
    re.I,
)

_STRONG_PROJECT_ATTRIBUTION_RE = re.compile(
    r"\b(?:dans ce projet|dans le cadre (?:de ce|du) projet|l['’]equipe (?:a|a pu|"
    r"a developpe|a teste|a evalue|a mesure|a realise)|nous avons (?:developpe|"
    r"implemente|teste|evalue|mesure|compare|entraine|realise|concu|mis en oeuvre)|"
    r"le projet (?:a|a permis|a developpe|a teste|a evalue|a mesure))\b",
    re.I,
)

# Le simple "we evaluated" n'est volontairement PAS suffisant : dans un article
# copié dans le dossier courant, "we" désigne les auteurs du papier, pas le client.
_WE_ONLY_RE = re.compile(
    r"\bwe (?:developed|implemented|tested|evaluated|measured|compared|trained|"
    r"generated|proposed|conducted|performed)\b",
    re.I,
)

_PROJECT_NARRATIVE_SECTIONS = {
    "synthese_strategique",
    "objectif_global",
    "demarche_detectee",
    "resultats_metriques",
    "parametres_contraintes",
}

_HISTORICAL_ALLOWED_SECTIONS = {
    "demarche_detectee",
    "resultats_metriques",
    "parametres_contraintes",
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _metadata(source: Dict[str, Any]) -> Dict[str, Any]:
    value = source.get("metadata") if isinstance(source, dict) else {}
    return value if isinstance(value, dict) else {}


def _first(source: Dict[str, Any], *keys: str) -> Any:
    meta = _metadata(source)
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
        value = meta.get(key)
        if value not in (None, ""):
            return value
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _norm(value) in {"1", "true", "yes", "oui"}


def _section_text(source: Dict[str, Any]) -> str:
    meta = _metadata(source)
    return _norm(" ".join(
        str(value or "")
        for value in (
            source.get("section_path"),
            source.get("section_title"),
            source.get("section"),
            source.get("heading_path"),
            source.get("source_zone"),
            source.get("document_section_type"),
            meta.get("section_path"),
            meta.get("section_title"),
            meta.get("section"),
            meta.get("heading_path"),
            meta.get("source_zone"),
            meta.get("document_section_type"),
        )
    ))


def _body_text(source: Dict[str, Any]) -> str:
    meta = _metadata(source)
    return _norm(" ".join(
        str(value or "")
        for value in (
            source.get("context_before"),
            source.get("text"),
            source.get("excerpt"),
            source.get("source_text_original"),
            source.get("analysis_text"),
            source.get("context_after"),
            source.get("summary_fr"),
            meta.get("analysis_text"),
        )
    ))


def classify_evidence_provenance(source: Dict[str, Any]) -> Dict[str, Any]:
    """Classe la provenance sans décider de l'existence d'un verrou.

    V3 : une métadonnée ``ambiguous_current_dossier`` ne masque jamais un signal
    plus fort d'état de l'art. C'est essentiel car les packs NLP courants peuvent
    porter une origine ambiguë par défaut avant que le contexte documentaire ne
    soit réévalué.
    """
    item = source if isinstance(source, dict) else {}
    explicit_origin = _norm(_first(item, "evidence_origin", "provenance_origin"))
    explicit_actor = _norm(_first(item, "actor_scope", "provenance_actor"))

    temporal_scope = _norm(_first(item, "temporal_scope"))
    if explicit_origin == PROV_HISTORICAL or temporal_scope in {
        "previous_cir_continuity", "previous year", "historical"
    }:
        return {
            "evidence_origin": PROV_HISTORICAL,
            "actor_scope": ACTOR_HISTORICAL,
            "provenance_reason": "previous_cir_temporal_scope",
            "provenance_confidence": 1.0,
        }

    evidence_id = str(_first(item, "evidence_id") or "").strip()
    role = _norm(_first(item, "role"))
    if explicit_origin == PROV_CALCULATION or evidence_id == "F0" or role == "calculated_assessment":
        return {
            "evidence_origin": PROV_CALCULATION,
            "actor_scope": ACTOR_BACKEND,
            "provenance_reason": "deterministic_backend_assessment",
            "provenance_confidence": 1.0,
        }

    section = _section_text(item)
    body = _body_text(item)
    declared_origin = _norm(_first(
        item,
        "content_origin",
        "source_type",
        "content_type",
        "source_kind",
        "document_type",
        "document_category",
    ))
    semantic_conflicts = _norm(_first(item, "semantic_role_conflicts", "role_conflicts"))
    if _truthy(_first(item, "reference_like")) or "etat_art" in semantic_conflicts or "state_of_art" in semantic_conflicts:
        return {
            "evidence_origin": PROV_EXTERNAL_LITERATURE,
            "actor_scope": ACTOR_EXTERNAL,
            "provenance_reason": "reference_like_or_state_of_art_role_conflict",
            "provenance_confidence": 0.995,
        }

    structured_external = any(
        _truthy(_first(item, key))
        for key in (
            "is_state_of_art",
            "state_of_art",
            "is_external_literature",
            "external_literature",
            "literature_only",
            "bibliographic_source",
            "reference_only",
        )
    )

    if (
        explicit_origin == PROV_EXTERNAL_LITERATURE
        or structured_external
        or _STATE_OF_ART_SECTION_RE.search(section)
    ):
        return {
            "evidence_origin": PROV_EXTERNAL_LITERATURE,
            "actor_scope": ACTOR_EXTERNAL,
            "provenance_reason": "state_of_art_or_explicit_external",
            "provenance_confidence": 0.99,
        }

    if _EXTERNAL_ORIGIN_RE.search(declared_origin) and "project_core" not in declared_origin:
        return {
            "evidence_origin": PROV_EXTERNAL_LITERATURE,
            "actor_scope": ACTOR_EXTERNAL,
            "provenance_reason": "external_content_origin",
            "provenance_confidence": 0.97,
        }

    if _THIRD_PARTY_ATTRIBUTION_RE.search(body):
        return {
            "evidence_origin": PROV_EXTERNAL_LITERATURE,
            "actor_scope": ACTOR_EXTERNAL,
            "provenance_reason": "third_party_attribution",
            "provenance_confidence": 0.9,
        }

    if explicit_origin == PROV_PROJECT_DIRECT:
        return {
            "evidence_origin": PROV_PROJECT_DIRECT,
            "actor_scope": explicit_actor or ACTOR_PROJECT,
            "provenance_reason": "explicit_project_direct",
            "provenance_confidence": 1.0,
        }

    if _PROJECT_SECTION_RE.search(section):
        return {
            "evidence_origin": PROV_PROJECT_DIRECT,
            "actor_scope": ACTOR_PROJECT,
            "provenance_reason": "project_work_section",
            "provenance_confidence": 0.94,
        }

    if _STRONG_PROJECT_ATTRIBUTION_RE.search(body):
        return {
            "evidence_origin": PROV_PROJECT_DIRECT,
            "actor_scope": ACTOR_PROJECT,
            "provenance_reason": "explicit_project_attribution",
            "provenance_confidence": 0.9,
        }

    if _WE_ONLY_RE.search(body):
        reason = "english_we_without_project_anchor"
    elif explicit_origin == PROV_AMBIGUOUS:
        reason = "explicit_ambiguous_after_external_checks"
    else:
        reason = "no_reliable_actor_anchor"
    return {
        "evidence_origin": PROV_AMBIGUOUS,
        "actor_scope": ACTOR_UNKNOWN,
        "provenance_reason": reason,
        "provenance_confidence": 0.35,
    }

def is_external_literature(source: Dict[str, Any]) -> bool:
    return classify_evidence_provenance(source)["evidence_origin"] == PROV_EXTERNAL_LITERATURE


def is_project_anchor(source: Dict[str, Any]) -> bool:
    """Ancrage fort : réservé aux preuves dont l'acteur projet est explicite."""
    return classify_evidence_provenance(source)["evidence_origin"] == PROV_PROJECT_DIRECT


_ROLE_ALIASES = {
    "method": "methode", "methode": "methode", "méthode": "methode",
    "demarche": "methode", "démarche": "methode",
    "result": "resultat", "resultat": "resultat", "résultat": "resultat",
    "parameter": "parametre", "parametre": "parametre", "paramètre": "parametre",
    "constraint": "limite", "limite": "limite", "uncertainty": "limite",
    "lock": "verrou", "verrou": "verrou", "contribution": "contribution",
}

_SECTION_ROLE_REQUIREMENTS = {
    "synthese_strategique": {"objectif", "contribution", "methode", "verrou", "limite"},
    "objectif_global": {"objectif", "contribution"},
    "demarche_detectee": {"methode", "parametre"},
    "resultats_metriques": {"resultat", "contribution"},
    "parametres_contraintes": {"parametre", "limite"},
    "verrou": {"verrou", "limite"},
}


def _role_values(source: Dict[str, Any]) -> set[str]:
    meta = _metadata(source)
    values = []
    for key in (
        "role", "semantic_role", "original_model_role", "final_role",
        "model_role", "candidate_role", "section_role_hint", "section_type",
        "operation_function",
    ):
        values.extend([source.get(key), meta.get(key)])
    output: set[str] = set()
    for raw in values:
        norm = _norm(raw)
        if not norm:
            continue
        output.add(_ROLE_ALIASES.get(norm, norm))
        for alias, canonical in _ROLE_ALIASES.items():
            if alias in norm:
                output.add(canonical)
    return output


def _current_corpus_signal(source: Dict[str, Any]) -> bool:
    meta = _metadata(source)
    if _truthy(_first(source, "current_project_evidence", "declared_raw_document")):
        return True

    selected = source.get("diagnostic_corpus_selected")
    if selected is None:
        selected = meta.get("diagnostic_corpus_selected")
    if selected is True or str(selected or "").strip().lower() in {"1", "true", "yes", "oui"}:
        return True

    declared = _norm(_first(source, "declared_corpus"))
    if "diagnostic" in declared:
        return True

    source_type = _norm(_first(source, "source_type"))
    if source_type in {"nlp_result_current_project", "current_project"}:
        return True

    temporal = _norm(_first(source, "temporal_scope"))
    if temporal in {"current_project", "current_year", "year_n", "current"}:
        return True

    content_origin = _norm(_first(source, "content_origin"))
    if content_origin in {"ambiguous_current_dossier", "project_core", "current_project"}:
        return True

    origin = _norm(_first(source, "evidence_origin", "provenance_origin"))
    has_document = bool(_first(source, "document", "source_path", "document_id"))
    return origin == PROV_AMBIGUOUS and has_document and bool(_role_values(source))


def classify_evidence_execution(source: Dict[str, Any]) -> Dict[str, Any]:
    """Lit la maturité structurée sans reclasser le texte par mots-clés.

    L'ordre d'autorité est : statut explicite, fonction/scope NLP structuré,
    puis rôle sémantique. Ce dernier fallback maintient la compatibilité avec les
    anciens ``nlp_result.json`` qui ne portaient pas encore ``execution_status``.
    """
    item = source if isinstance(source, dict) else {}
    provenance = classify_evidence_provenance(item)
    if provenance.get("evidence_origin") == PROV_EXTERNAL_LITERATURE:
        return {
            "execution_status": EXEC_UNKNOWN,
            "execution_reason": "external_literature_never_promoted_by_nlp_role",
            "execution_confidence": 1.0,
        }
    explicit = _norm(_first(item, "execution_status", "fact_status", "maturity_status"))
    if explicit in _ALLOWED_EXECUTION_STATUSES:
        return {
            "execution_status": explicit,
            "execution_reason": "explicit_metadata",
            "execution_confidence": 1.0,
        }

    operation_function = _norm(_first(item, "operation_function", "activity_function"))
    operation_statuses = {
        "experiment": EXEC_EXPERIMENTED,
        "historical method": EXEC_EXPERIMENTED,
        "result": EXEC_OBSERVED,
        "learning": EXEC_OBSERVED,
        "parameter": EXEC_ACTIVE_CONSTRAINT,
        "constraint": EXEC_ACTIVE_CONSTRAINT,
        "hypothesis": EXEC_PROPOSED,
        "hypothesis component": EXEC_PROPOSED,
    }
    if operation_function in operation_statuses:
        return {
            "execution_status": operation_statuses[operation_function],
            "execution_reason": "structured_operation_function",
            "execution_confidence": 0.96,
        }

    result_scope = _norm(_first(item, "result_scope"))
    if result_scope in {
        "global comparison", "global metric", "observed gain",
        "observed metric", "qualitative observation", "historical result",
    }:
        return {
            "execution_status": EXEC_MEASURED if "metric" in result_scope or "comparison" in result_scope else EXEC_OBSERVED,
            "execution_reason": "structured_result_scope",
            "execution_confidence": 0.94,
        }
    if result_scope in {"target metric", "target context", "planning or question"}:
        return {
            "execution_status": EXEC_PLANNED,
            "execution_reason": "structured_target_scope",
            "execution_confidence": 0.94,
        }

    roles = _role_values(item)
    claim_text = _body_text(item)
    if _LEGACY_PLANNED_RE.search(claim_text):
        return {
            "execution_status": EXEC_PLANNED,
            "execution_reason": "legacy_temporal_fallback_after_nlp_role",
            "execution_confidence": 0.72,
        }
    if "methode" in roles and _LEGACY_EXPERIMENTED_RE.search(claim_text):
        return {
            "execution_status": EXEC_EXPERIMENTED,
            "execution_reason": "legacy_temporal_fallback_after_nlp_role",
            "execution_confidence": 0.72,
        }
    if {"resultat", "contribution"} & roles:
        if _LEGACY_MEASURED_RE.search(claim_text):
            return {
                "execution_status": EXEC_MEASURED,
                "execution_reason": "legacy_temporal_fallback_after_nlp_role",
                "execution_confidence": 0.72,
            }
        if _LEGACY_OBSERVED_RE.search(claim_text):
            return {
                "execution_status": EXEC_OBSERVED,
                "execution_reason": "legacy_temporal_fallback_after_nlp_role",
                "execution_confidence": 0.72,
            }
        document_type = _norm(_first(item, "document_type", "declared_document_type"))
        if document_type in {"resultats_mesures", "résultats_mesures", "measurement_results"}:
            return {
                "execution_status": EXEC_MEASURED,
                "execution_reason": "structured_measurement_document_type",
                "execution_confidence": 0.88,
            }
    if {"parametre", "limite"} & roles and _LEGACY_ACTIVE_CONSTRAINT_RE.search(claim_text):
        return {
            "execution_status": EXEC_ACTIVE_CONSTRAINT,
            "execution_reason": "legacy_active_constraint_after_nlp_role",
            "execution_confidence": 0.78,
        }
    role_statuses = (
        ({"objectif"}, EXEC_PLANNED),
        ({"methode"}, EXEC_IMPLEMENTED),
        ({"resultat", "contribution"}, EXEC_OBSERVED),
    )
    for expected_roles, status in role_statuses:
        if roles & expected_roles:
            return {
                "execution_status": status,
                "execution_reason": "authoritative_nlp_semantic_role",
                "execution_confidence": 0.82,
            }
    return {
        "execution_status": EXEC_UNKNOWN,
        "execution_reason": "no_structured_execution_signal",
        "execution_confidence": 0.0,
    }


def is_trusted_current_project_evidence(source: Dict[str, Any], section_key: str) -> bool:
    """Autorise un rôle NLP courant sans le transformer en ``project_direct``."""
    item = source if isinstance(source, dict) else {}
    report = classify_evidence_provenance(item)
    origin = report.get("evidence_origin")
    if origin == PROV_PROJECT_DIRECT:
        return True
    if origin != PROV_AMBIGUOUS:
        return False
    if report.get("provenance_reason") == "english_we_without_project_anchor":
        return False
    if bool(_first(item, "reference_like")):
        return False
    if _STATE_OF_ART_SECTION_RE.search(_section_text(item)):
        return False
    if _THIRD_PARTY_ATTRIBUTION_RE.search(_body_text(item)):
        return False
    if not _current_corpus_signal(item):
        return False
    required = _SECTION_ROLE_REQUIREMENTS.get(str(section_key or ""), set())
    if not required:
        return False
    return bool(_role_values(item) & required)


def provenance_allows_section(source: Dict[str, Any], section_key: str) -> bool:
    """Autorisation d'usage aval sans refaire la classification du NLP."""
    report = classify_evidence_provenance(source)
    origin = report["evidence_origin"]
    key = str(section_key or "")

    if key in {"synthese_strategique", "objectif_global"}:
        # Les anciens packs ne portaient pas toujours ``project_direct``. Leur
        # rôle NLP explicite reste utilisable après les gardes anti-littérature.
        return origin == PROV_PROJECT_DIRECT or is_trusted_current_project_evidence(
            source, key
        )

    if key in _HISTORICAL_ALLOWED_SECTIONS and origin == PROV_HISTORICAL:
        return True

    if key in {"demarche_detectee", "resultats_metriques", "parametres_contraintes"}:
        # Le rôle NLP du corpus courant assure la compatibilité des anciens
        # packs, mais uniquement après exclusion de l'état de l'art, des
        # références et des attributions à des tiers.
        origin_allowed = (
            origin == PROV_PROJECT_DIRECT
            or is_trusted_current_project_evidence(source, key)
        )
        if not origin_allowed:
            return False
        required = _SECTION_ROLE_REQUIREMENTS[key]
        roles = _role_values(source)
        if roles and not (roles & required):
            return False
        status = classify_evidence_execution(source)["execution_status"]
        allowed_statuses = {
            "demarche_detectee": {
                EXEC_IMPLEMENTED, EXEC_EXPERIMENTED, EXEC_OBSERVED, EXEC_MEASURED,
            },
            "resultats_metriques": {EXEC_OBSERVED, EXEC_MEASURED},
            "parametres_contraintes": {
                EXEC_ACTIVE_CONSTRAINT, EXEC_IMPLEMENTED, EXEC_EXPERIMENTED,
                EXEC_OBSERVED, EXEC_MEASURED,
            },
        }
        return status in allowed_statuses[key]

    if key == "verrou":
        return origin == PROV_PROJECT_DIRECT or is_trusted_current_project_evidence(
            source, "verrou"
        )

    if key == "justification_frascati":
        if origin == PROV_PROJECT_DIRECT:
            return True
        return any(
            is_trusted_current_project_evidence(source, candidate)
            for candidate in (
                "verrou", "demarche_detectee", "resultats_metriques", "parametres_contraintes"
            )
        )

    if origin == PROV_HISTORICAL:
        return False
    return True


def execution_allows_claim(source: Dict[str, Any], claim_kind: str) -> bool:
    """Vérifie la compatibilité entre statut structuré et type d'affirmation."""
    status = classify_evidence_execution(source)["execution_status"]
    kind = _norm(claim_kind).replace(" ", "_")
    if kind in {
        "etapes_experimentales", "experiment", "methodes_outils", "systematicity",
    }:
        return status in {
            EXEC_IMPLEMENTED, EXEC_EXPERIMENTED, EXEC_OBSERVED, EXEC_MEASURED,
        }
    if kind in {
        "resultats", "result", "apprentissage", "learning", "result_facts",
    }:
        return status in {EXEC_OBSERVED, EXEC_MEASURED}
    if kind in {"hypothese", "hypothesis", "hypothesis_component"}:
        return status in {
            EXEC_PLANNED, EXEC_PROPOSED, EXEC_IMPLEMENTED, EXEC_EXPERIMENTED,
        }
    return True


__all__ = [
    "PROV_PROJECT_DIRECT", "PROV_EXTERNAL_LITERATURE", "PROV_AMBIGUOUS",
    "PROV_HISTORICAL", "PROV_CALCULATION", "classify_evidence_provenance",
    "classify_evidence_execution", "is_external_literature", "is_project_anchor",
    "is_trusted_current_project_evidence", "provenance_allows_section",
    "execution_allows_claim", "EXEC_PLANNED", "EXEC_PROPOSED",
    "EXEC_IMPLEMENTED", "EXEC_EXPERIMENTED", "EXEC_OBSERVED", "EXEC_MEASURED",
    "EXEC_ACTIVE_CONSTRAINT", "EXEC_UNKNOWN",
]
