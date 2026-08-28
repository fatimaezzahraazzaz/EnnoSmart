# -*- coding: utf-8 -*-
from __future__ import annotations

"""Qualification de provenance et de maturité des preuves EnnoDiagnostic.

Ce module V2 est volontairement indépendant de la logique qui détecte/regroupe les
verrous. Il ajoute une deuxième dimension à une preuve : qui produit le fait ?

Une preuve peut donc rester utile à la détection d'un verrou/à l'état de l'art,
tout en étant interdite comme preuve d'un objectif, d'une démarche ou d'un
résultat réalisé par le projet courant.

V3 ajoute une seconde dimension indépendante : le statut d'exécution du fait.
Cela évite de supprimer une vraie preuve projet sans attribution littérale et,
à l'inverse, de transformer une intention en travail réalisé ou résultat mesuré.
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
    r"the paper|previous work|prior work|"
    r"travaux anterieurs|et al\.)\b",
    re.I,
)

_EXTERNAL_STUDY_CONTENT_RE = re.compile(
    r"\b(?:dans (?:cette|l['’ ]?)etude|the study|this study|les travaux de|"
    r"dans (?:le|ce) papier|l['’ ]article (?:presente|propose|evalue|compare)|"
    r"auteurs? (?:proposent|"
    r"presentent|evaluent|comparent|utilisent)|les auteurs? ont (?:utilise|"
    r"entraine|effectue|evalue|compare|propose|obtenu)|ils ont (?:utilise|"
    r"entraine|effectue|evalue|compare|propose|obtenu|choisi)|certaines etudes (?:suggerent|"
    r"montrent|indiquent)|des etudes recentes (?:ont )?(?:compare|montre|evalue)|"
    r"les deux approches (?:demontrent|montrent)|les frameworks? soulignent|"
    r"les modeles de pointe|les modeles tels que|ont attire l[' ]attention|"
    r"specifiquement ils (?:generent|configurent|utilisent|evaluent|comparent)|"
    r"approches? existantes?|dans l[' ]etat de l[' ]art|"
    r"baseline|state of the art|"
    r"related work|doi\b|arxiv\b|proceedings\b|et al\b)\b",
    re.I,
)

_BIBLIOGRAPHIC_CITATION_RE = re.compile(r"(?:\[\s*\d{1,3}\s*\]|\(\s*\d{4}\s*\))")

_PROJECT_SECTION_RE = re.compile(
    r"\b(?:travaux realises?|travaux menes?|demarche experimentale|"
    r"protocole experimental|experimentations?|essais? realises?|"
    r"resultats? obtenus?|resultats? du projet|nos travaux|"
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

_PLANNED_RE = re.compile(
    r"\b(?:objectif|but|vise(?:nt)? a|chercher a|souhaite(?:nt)?|dev(?:ons|ra|ront|rait)|"
    r"a faire|reste a faire|prevu(?:e|es|s)?|planifie(?:e|es|s)?|envisage(?:e|es|s)?|"
    r"une etude possible|il faudra|doit etre|doivent etre|permettra|permettront|"
    r"sera(?:ient)?|seront|a evaluer|a tester|a valider|a mesurer)\b",
    re.I,
)

_PROPOSED_RE = re.compile(
    r"\b(?:propos(?:e|ons|ent|ee|ees)|hypothese|piste envisagee|approche candidate|"
    r"scenario propose|protocole propose)\b",
    re.I,
)

_MEASURED_RE = re.compile(
    r"\b(?:mesur(?:e|ee|es|ons)|valeur(?:s)? obtenue(?:s)?|score(?:s)? obtenus?|"
    r"taux mesure|metrique(?:s)? obtenue(?:s)?|couverture (?:atteinte|mesuree)|"
    r"precision (?:atteinte|mesuree)|temps d[' ]execution mesure|benchmark (?:obtenu|realise))\b",
    re.I,
)

_OBSERVED_RE = re.compile(
    r"\b(?:resultats? (?:obtenus? )?(?:montrent|indiquent|confirment|font apparaitre)|"
    r"avons observe|avons constate|on observe|on constate|il ressort|"
    r"a montre|ont montre|a confirme|ont confirme|observation(?:s)? obtenue(?:s)?)\b",
    re.I,
)

_EXPERIMENTED_RE = re.compile(
    r"\b(?:avons (?:teste|evalue|compare|experimente|execute|injecte|entraine)|"
    r"on a (?:teste|evalue|compare|experimente|execute|injecte|entraine|fait)|"
    r"l[' ]equipe a (?:teste|evalue|compare|experimente|execute|entraine)|"
    r"l[' ]experience (?:a|comprend|comporte) (?:deux|trois|quatre|cinq|\d+) etapes|"
    r"essais? (?:realises?|menes?|effectues?)|experimentations? (?:realisees?|menees?))\b",
    re.I,
)

_IMPLEMENTED_RE = re.compile(
    r"\b(?:avons (?:developpe|implemente|cree|concu|utilise|adapte|modifie|"
    r"identifie|extrait|configure|mis en oeuvre)|"
    r"on a (?:developpe|implemente|cree|concu|utilise|adapte|modifie|"
    r"identifie|extrait|configure|mis en oeuvre)|"
    r"nous[ ,]+on a (?:developpe|implemente|cree|concu|utilise|adapte|modifie|"
    r"identifie|extrait|configure|mis en oeuvre)|"
    r"l[' ]equipe a (?:developpe|implemente|cree|concu|utilise|adapte|modifie|"
    r"identifie|extrait|configure|mis en oeuvre)|"
    r"l[' ]analyse s[' ]est faite|l[' ]analyse a ete realisee|"
    r"a ete (?:developpe|implemente|cree|adapte|modifie|configure|mis en oeuvre|deploye)|"
    r"ont ete (?:developpes|implementes|crees|adaptes|modifies|configures|mis en oeuvre|deployes)|"
    r"(?:strategie|methode|procedure|configuration)s? (?:est|sont) (?:utilisee?s?|appliquee?s?|executee?s?))\b",
    re.I,
)

_PROCEDURE_IN_USE_RE = re.compile(
    r"\b(?:la (?:methode|demarche|procedure|chaine|pipeline) (?:consiste|utilise|applique|execute)|"
    r"le (?:protocole|modele|systeme|demonstrateur) (?:utilise|applique|execute|genere)|"
    r"les etapes (?:sont|comprennent)|configuration utilisee)\b",
    re.I,
)

_CONSTRAINT_RE = re.compile(
    r"\b(?:parametre|configuration|contrainte|limite|seuil|fenetre de contexte|"
    r"jeu de donnees|temperature|taille|nombre de|maximum|minimum|compris entre|"
    r"fixe(?:e)? a|regle(?:e)? a)\b",
    re.I,
)

_QUANTITATIVE_RE = re.compile(
    r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?\s*(?:%|ms|s|db|go|mo|ko|tokens?|images?|cas|tests?)?",
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
    text = str(value or "").lower().replace("’", "'").replace("`", "'").replace("´", "'")
    # Certains anciens exports OCR ont remplacé accents ET apostrophes par �.
    # On restaure les élisions fréquentes, puis on retire le marqueur restant
    # pour que « exp�rience » redevienne « experience ».
    text = re.sub(r"\b(l|d|s|n|c|qu)\ufffd(?=[a-z])", r"\1 ", text)
    text = text.replace("\ufffd", "")
    text = unicodedata.normalize("NFKD", text)
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


def _section_text(source: Dict[str, Any]) -> str:
    meta = _metadata(source)
    return _norm(" ".join(
        str(value or "")
        for value in (
            source.get("section_path"),
            source.get("section_title"),
            source.get("section"),
            meta.get("section_path"),
            meta.get("section_title"),
            meta.get("section"),
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


def _claim_text(source: Dict[str, Any]) -> str:
    """Texte du fait lui-même, sans le voisinage qui peut changer son temps."""
    meta = _metadata(source)
    for value in (
        source.get("text"),
        source.get("excerpt"),
        source.get("source_text_original"),
        source.get("analysis_text"),
        meta.get("analysis_text"),
    ):
        normalized = _norm(value)
        if normalized:
            return normalized
    return ""


def _document_text(source: Dict[str, Any]) -> str:
    return _norm(" ".join(
        str(value or "")
        for value in (
            _first(source, "document", "document_name", "file_name", "filename"),
            _first(source, "document_type", "declared_document_type", "structure_type"),
            _first(source, "declared_corpus", "source_policy"),
        )
    ))


def _role_text(source: Dict[str, Any]) -> str:
    return _norm(_first(
        source,
        "semantic_role",
        "role",
        "final_role",
        "section_role_hint",
        "pack_key",
    ))


def _is_measurement_document(source: Dict[str, Any]) -> bool:
    document = re.sub(r"[_/\\-]+", " ", _document_text(source))
    return any(marker in document for marker in (
        "resultats mesures", "resultat mesure", "mesures resultats",
    ))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _norm(value) in {"1", "true", "yes", "oui"}


def classify_evidence_provenance(source: Dict[str, Any]) -> Dict[str, Any]:
    """Classe la provenance sans décider de l'existence d'un verrou.

    Priorité volontaire :
    1) provenance explicitement déjà qualifiée ;
    2) N-1 / calcul backend ;
    3) section d'état de l'art (prioritaire sur les pronoms "we/nous") ;
    4) attribution explicite à des auteurs tiers ;
    5) section/action explicitement rattachée au projet ;
    6) ambigu : on conserve la preuve, mais on ne la certifie pas comme fait projet.
    """
    item = source if isinstance(source, dict) else {}
    explicit_origin = _norm(_first(item, "evidence_origin", "provenance_origin"))
    explicit_actor = _norm(_first(item, "actor_scope", "provenance_actor"))

    if explicit_origin in _ALLOWED_ORIGINS:
        actor = explicit_actor or {
            PROV_PROJECT_DIRECT: ACTOR_PROJECT,
            PROV_EXTERNAL_LITERATURE: ACTOR_EXTERNAL,
            PROV_HISTORICAL: ACTOR_HISTORICAL,
            PROV_CALCULATION: ACTOR_BACKEND,
        }.get(explicit_origin, ACTOR_UNKNOWN)
        return {
            "evidence_origin": explicit_origin,
            "actor_scope": actor,
            "provenance_reason": "explicit_metadata",
            "provenance_confidence": 1.0,
        }

    temporal_scope = _norm(_first(item, "temporal_scope"))
    if temporal_scope in {"previous_cir_continuity", "previous year", "historical"}:
        return {
            "evidence_origin": PROV_HISTORICAL,
            "actor_scope": ACTOR_HISTORICAL,
            "provenance_reason": "previous_cir_temporal_scope",
            "provenance_confidence": 1.0,
        }

    evidence_id = str(_first(item, "evidence_id") or "").strip()
    role = _norm(_first(item, "role"))
    if evidence_id == "F0" or role == "calculated_assessment":
        return {
            "evidence_origin": PROV_CALCULATION,
            "actor_scope": ACTOR_BACKEND,
            "provenance_reason": "deterministic_backend_assessment",
            "provenance_confidence": 1.0,
        }

    section = _section_text(item)
    body = _body_text(item)
    section_and_body = f"{section} {body}".strip()
    document = _document_text(item)
    declared_origin = _norm(_first(item, "content_origin", "source_type", "content_type"))

    # Le titre/contexte documentaire gagne toujours sur "we/nous" dans le texte.
    if _STATE_OF_ART_SECTION_RE.search(section):
        return {
            "evidence_origin": PROV_EXTERNAL_LITERATURE,
            "actor_scope": ACTOR_EXTERNAL,
            "provenance_reason": "state_of_art_section",
            "provenance_confidence": 0.99,
        }

    if _EXTERNAL_ORIGIN_RE.search(declared_origin) and "project_core" not in declared_origin:
        return {
            "evidence_origin": PROV_EXTERNAL_LITERATURE,
            "actor_scope": ACTOR_EXTERNAL,
            "provenance_reason": "external_content_origin",
            "provenance_confidence": 0.97,
        }

    if (
        _THIRD_PARTY_ATTRIBUTION_RE.search(section_and_body)
        or _EXTERNAL_STUDY_CONTENT_RE.search(section_and_body)
        or _BIBLIOGRAPHIC_CITATION_RE.search(section_and_body)
    ):
        return {
            "evidence_origin": PROV_EXTERNAL_LITERATURE,
            "actor_scope": ACTOR_EXTERNAL,
            "provenance_reason": "third_party_attribution_or_study_context",
            "provenance_confidence": 0.9,
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

    # Le NLP sait déjà qu'un passage vient du corpus diagnostic courant. V2
    # perdait cette information pendant la conversion et classait presque tout
    # en ambigu. On restaure cet ancrage uniquement APRES les gardes état de
    # l'art/auteurs tiers ci-dessus. Les « rapports de test » restent prudents :
    # ils peuvent contenir une synthèse bibliographique copiée dans le dossier.
    current_project_evidence = _truthy(_first(item, "current_project_evidence"))
    declared_raw = _truthy(_first(item, "declared_raw_document"))
    current_source = "nlp_result_current_project" in declared_origin
    normalized_document_kind = re.sub(r"[_/\\-]+", " ", document)
    trusted_current_project_document = any(
        marker in document
        for marker in (
            "concept projet", "methodologie protocole", "resultats mesures",
            "cir final", "dossier rd", "dossier r d", "compte rendu",
        )
    ) or any(
        marker in normalized_document_kind
        for marker in (
            "concept projet", "methodologie protocole", "resultats mesures",
            "cir final", "dossier rd", "dossier r d", "compte rendu",
        )
    )
    if (
        (current_project_evidence or declared_raw or current_source)
        and trusted_current_project_document
    ):
        return {
            "evidence_origin": PROV_PROJECT_DIRECT,
            "actor_scope": ACTOR_PROJECT,
            "provenance_reason": "trusted_current_project_document_role",
            "provenance_confidence": 0.84,
        }

    # Un "we evaluated" isolé reste ambigu, précisément pour ne pas confondre
    # les auteurs d'un article et l'équipe du projet.
    reason = "english_we_without_project_anchor" if _WE_ONLY_RE.search(body) else "no_reliable_actor_anchor"
    return {
        "evidence_origin": PROV_AMBIGUOUS,
        "actor_scope": ACTOR_UNKNOWN,
        "provenance_reason": reason,
        "provenance_confidence": 0.35,
    }


def classify_evidence_execution(source: Dict[str, Any]) -> Dict[str, Any]:
    """Qualifie ce que la preuve permet d'affirmer sur l'avancement réel.

    Cette fonction ne participe jamais à la détection ni au regroupement des
    verrous. Elle sert uniquement aux sections narratives du diagnostic.
    """
    item = source if isinstance(source, dict) else {}
    explicit = _norm(_first(item, "execution_status", "fact_status", "maturity_status"))
    if explicit in _ALLOWED_EXECUTION_STATUSES:
        return {
            "execution_status": explicit,
            "execution_reason": "explicit_metadata",
            "execution_confidence": 1.0,
        }

    # Le contexte avant/après sert à la provenance, mais pas au temps du fait :
    # un mot « objectif » dans la phrase voisine ne doit pas transformer un essai
    # effectivement décrit en simple intention.
    text = _claim_text(item) or _body_text(item)
    role = _role_text(item)
    provenance = classify_evidence_provenance(item)
    is_direct_project_fact = provenance.get("evidence_origin") == PROV_PROJECT_DIRECT

    # Les marqueurs d'accomplissement portent sur le fait lui-même. Comme le
    # voisinage a été retiré, ils peuvent gagner sur une cible secondaire citée
    # plus loin dans le même passage (« analyse réalisée ; seuil à confirmer »).
    if _MEASURED_RE.search(text) or (
        ("resultat" in role or "parametre" in role)
        and _QUANTITATIVE_RE.search(text)
        and _is_measurement_document(item)
        and any(marker in text for marker in (
            "obtenu", "mesure", "score", "taux", "couverture", "precision",
        ))
    ):
        return {
            "execution_status": EXEC_MEASURED,
            "execution_reason": "measured_result_language",
            "execution_confidence": 0.92,
        }
    if _OBSERVED_RE.search(text):
        return {
            "execution_status": EXEC_OBSERVED,
            "execution_reason": "observed_result_language",
            "execution_confidence": 0.9,
        }
    if _EXPERIMENTED_RE.search(text):
        return {
            "execution_status": EXEC_EXPERIMENTED,
            "execution_reason": "completed_experiment_language",
            "execution_confidence": 0.9,
        }
    if _IMPLEMENTED_RE.search(text):
        return {
            "execution_status": EXEC_IMPLEMENTED,
            "execution_reason": "completed_implementation_language",
            "execution_confidence": 0.88,
        }
    # En l'absence d'un marqueur accompli, une formulation planifiée/proposée
    # reste impérativement une intention, jamais une démarche ou un résultat.
    if _PLANNED_RE.search(text):
        return {
            "execution_status": EXEC_PLANNED,
            "execution_reason": "planning_or_target_language",
            "execution_confidence": 0.9,
        }
    if _PROPOSED_RE.search(text):
        return {
            "execution_status": EXEC_PROPOSED,
            "execution_reason": "proposal_or_hypothesis_language",
            "execution_confidence": 0.86,
        }
    if "objectif" in role:
        return {
            "execution_status": EXEC_PLANNED,
            "execution_reason": "objective_role_is_a_target_not_a_result",
            "execution_confidence": 0.76,
        }
    if (
        is_direct_project_fact
        and "resultat" in role
        and _QUANTITATIVE_RE.search(text)
        and _is_measurement_document(item)
    ):
        return {
            "execution_status": EXEC_MEASURED,
            "execution_reason": "quantitative_result_role",
            "execution_confidence": 0.72,
        }
    if (
        is_direct_project_fact
        and "parametre" in role
        and _CONSTRAINT_RE.search(text)
    ):
        return {
            "execution_status": EXEC_ACTIVE_CONSTRAINT,
            "execution_reason": "parameter_or_constraint_in_force",
            "execution_confidence": 0.78,
        }
    if is_direct_project_fact and "methode" in role and _PROCEDURE_IN_USE_RE.search(text):
        return {
            "execution_status": EXEC_IMPLEMENTED,
            "execution_reason": "procedure_described_in_use",
            "execution_confidence": 0.72,
        }
    return {
        "execution_status": EXEC_UNKNOWN,
        "execution_reason": "no_reliable_execution_anchor",
        "execution_confidence": 0.35,
    }


def is_external_literature(source: Dict[str, Any]) -> bool:
    return classify_evidence_provenance(source)["evidence_origin"] == PROV_EXTERNAL_LITERATURE


def is_project_anchor(source: Dict[str, Any]) -> bool:
    """V2 stricte : ambigu n'est jamais synonyme de preuve projet."""
    return classify_evidence_provenance(source)["evidence_origin"] == PROV_PROJECT_DIRECT


def provenance_allows_section(source: Dict[str, Any], section_key: str) -> bool:
    """Autorisation d'usage aval, sans toucher à la détection des verrous.

    Pour toute section qui affirme un fait du projet courant, seule une preuve
    `project_direct` est autorisée. La littérature et les passages ambigus restent
    disponibles ailleurs (notamment pour uncertainty / novelty / verrous).
    Les preuves N-1 gardent leur chemin historique dédié dans les trois sections
    qui savent explicitement les étiqueter comme historiques.
    """
    report = classify_evidence_provenance(source)
    origin = report["evidence_origin"]
    key = str(section_key or "")

    strict_project_fact_sections = set(_PROJECT_NARRATIVE_SECTIONS) | {"justification_frascati"}
    if key in strict_project_fact_sections:
        if origin == PROV_HISTORICAL and key in _HISTORICAL_ALLOWED_SECTIONS:
            return True
        if origin != PROV_PROJECT_DIRECT:
            # Pour résumer le sujet et formuler son objectif, une preuve du
            # dossier courant sans acteur explicite reste utilisable comme
            # cadrage. Elle ne pourra pas prouver une action ou un résultat.
            current_dossier = (
                _truthy(_first(source, "current_project_evidence"))
                or _truthy(_first(source, "declared_raw_document"))
                or "nlp_result_current_project" in _norm(
                    _first(source, "source_type", "content_origin")
                )
            )
            if (
                origin == PROV_AMBIGUOUS
                and key in {"synthese_strategique", "objectif_global"}
                and current_dossier
            ):
                return True
            return False

        status = classify_evidence_execution(source)["execution_status"]
        if key == "demarche_detectee":
            return status in {
                EXEC_IMPLEMENTED, EXEC_EXPERIMENTED, EXEC_OBSERVED, EXEC_MEASURED,
            }
        if key == "resultats_metriques":
            return status in {EXEC_OBSERVED, EXEC_MEASURED}
        if key == "parametres_contraintes":
            return status in {
                EXEC_ACTIVE_CONSTRAINT, EXEC_IMPLEMENTED, EXEC_EXPERIMENTED,
                EXEC_OBSERVED, EXEC_MEASURED,
            }
        # Synthèse et objectif peuvent décrire une cible. Le rédacteur doit
        # cependant conserver le statut planned/proposed dans sa formulation.
        return True

    if origin == PROV_HISTORICAL and key not in _HISTORICAL_ALLOWED_SECTIONS:
        return False
    return True


def execution_allows_claim(source: Dict[str, Any], claim_kind: str) -> bool:
    """Compatibilité minimale entre une preuve et un claim Frascati."""
    status = classify_evidence_execution(source)["execution_status"]
    kind = _norm(claim_kind)
    if kind in {
        "etapes experimentales", "etapes_experimentales", "experiment",
        "methodes outils", "methodes_outils", "systematicity",
    }:
        return status in {
            EXEC_IMPLEMENTED, EXEC_EXPERIMENTED, EXEC_OBSERVED, EXEC_MEASURED,
        }
    if kind in {
        "resultats", "result", "apprentissage", "learning",
        "result facts", "result_facts",
    }:
        return status in {EXEC_OBSERVED, EXEC_MEASURED}
    if kind in {
        "hypothese", "hypothesis", "hypothesis component", "hypothesis_component",
    }:
        return status in {
            EXEC_PLANNED, EXEC_PROPOSED, EXEC_IMPLEMENTED, EXEC_EXPERIMENTED,
        }
    return True


__all__ = [
    "PROV_PROJECT_DIRECT", "PROV_EXTERNAL_LITERATURE", "PROV_AMBIGUOUS",
    "PROV_HISTORICAL", "PROV_CALCULATION", "classify_evidence_provenance",
    "classify_evidence_execution", "is_external_literature", "is_project_anchor",
    "provenance_allows_section", "execution_allows_claim",
    "EXEC_PLANNED", "EXEC_PROPOSED", "EXEC_IMPLEMENTED", "EXEC_EXPERIMENTED",
    "EXEC_OBSERVED", "EXEC_MEASURED", "EXEC_ACTIVE_CONSTRAINT", "EXEC_UNKNOWN",
]
