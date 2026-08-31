# -*- coding: utf-8 -*-
from __future__ import annotations

"""Gate générique des faits projet EnnoDiagnostic.

Ce module ne détecte ni ne regroupe les verrous. Il ne modifie jamais la liste
des verrous. Son unique responsabilité est d'empêcher qu'une preuve de
littérature, une question de réunion, une ligne de tableau non contextualisée,
une consigne ou une valeur de transcription non corroborée soit publiée comme
objectif, démarche, résultat ou paramètre du projet courant.

Principes :
- le rôle NLP décrit *ce que le passage ressemble à être* ;
- la provenance / l'acteur dit *qui porte le fait* ;
- le statut d'exécution dit *si le fait a réellement eu lieu* ;
- un rôle seul n'autorise jamais la publication d'un fait projet.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


PROJECT_FACT_SECTIONS = {
    "objectif_global",
    "demarche_detectee",
    "resultats_metriques",
    "parametres_contraintes",
    "synthese_strategique",
}

_ROLE_ALIASES = {
    "objective": "objectif",
    "objectif": "objectif",
    "goal": "objectif",
    "method": "methode",
    "methode": "methode",
    "méthode": "methode",
    "demarche": "methode",
    "démarche": "methode",
    "result": "resultat",
    "resultat": "resultat",
    "résultat": "resultat",
    "contribution": "contribution",
    "parameter": "parametre",
    "parametre": "parametre",
    "paramètre": "parametre",
    "constraint": "limite",
    "limite": "limite",
    "uncertainty": "limite",
    "lock": "verrou",
    "verrou": "verrou",
}

_STATE_OF_ART_RE = re.compile(
    r"\b(?:etat de l['’ ]?art|state of the art|related works?|travaux connexes|"
    r"revue (?:de la )?litterature|literature review|bibliograph(?:ie|y)|"
    r"systematic review|survey|references?)\b",
    re.I,
)

_EXTERNAL_ATTRIBUTION_RE = re.compile(
    r"\b(?:les auteurs?|the authors?|dans (?:le|l['’]?) papier|the paper|"
    r"selon (?:les auteurs?|l['’]?etude|l['’]?article)|according to|"
    r"une etude empirique|l['’]?etude a (?:inclus|porte|teste|evalue|compare)|"
    r"analyse de \d+ (?:papers?|articles?|publications?)|"
    r"certaines etudes (?:montrent|suggerent|indiquent)|"
    r"des etudes (?:montrent|suggerent|indiquent)|"
    r"ils ont (?:utilise|cree|entraine|propose|compare|evalue|configure|genere|choisi|effectue|analyse|obtenu|mesure)|"
    r"elles ont (?:utilise|cree|entraine|propose|compare|evalue|configure|genere)|"
    r"reported by|proposed by|demonstrated by|previous work|prior work|et al\.)\b",
    re.I,
)

_GENERIC_LITERATURE_RESULT_RE = re.compile(
    r"\b(?:les resultats|the results|les travaux|la litterature|les publications?)\s+"
    r"(?:montrent|indiquent|suggerent|show|indicate|suggest)\b.{0,260}"
    r"\b(?:couramment|frequemment|generally|commonly|often|dans les etudes)\b",
    re.I,
)

_PROJECT_ACTOR_RE = re.compile(
    r"\b(?:dans ce projet|dans le cadre (?:de ce|du) projet|notre projet|nos travaux|"
    r"l['’]?equipe (?:a|avait|a pu)|nous avons|nous avions|nous on a|"
    r"on a (?:teste|evalue|mesure|genere|developpe|compare|utilise|implemente|"
    r"configure|adapte|entraine|fait|choisi|extrait|reinjecte|identifie|modifie|travaille)|"
    r"nous,? on (?:a|l['’]?a|les a|est|voulait)|on (?:l['’]?a|les a)|nous (?:on )?voulions|nous cherchions|"
    r"le projet (?:a|avait|vise|cherche))\b",
    re.I,
)

_PROJECT_WORK_SECTION_RE = re.compile(
    r"\b(?:travaux realises?|travaux menes?|demarche experimentale|"
    r"protocole experimental|description des experimentations?|"
    r"resultats? des experimentations?|resultats? obtenus?|"
    r"resultats? et analyse|description des travaux|mise en oeuvre|"
    r"developpements? realises?|compte rendu|reunion technique)\b",
    re.I,
)

_OBJECTIVE_RE = re.compile(
    r"\b(?:objectif(?:s)?(?: du projet)?|but(?: du projet)?|finalite|"
    r"le projet vise|vise a|cherche a|nous voulions|nous cherchions|"
    r"l['’]?objectif est|l['’]?objectif consiste|goal|aim|purpose)\b",
    re.I,
)

_EXECUTED_ACTION_RE = re.compile(
    r"\b(?:nous avons|l['’]?equipe a)\s+"
    r"(?:test\w*|evalu\w*|mesur\w*|compar\w*|gener\w*|developp\w*|implement\w*|"
    r"configur\w*|adapt\w*|entrain\w*|extrai\w*|reinje\w*|utilis\w*|fait|"
    r"chois\w*|appliqu\w*|constru\w*|execut\w*|lanc\w*|calcul\w*|analys\w*|"
    r"identifi\w*|modifi\w*|travaill\w*|inspir\w*)\b|"
    r"\b(?:nous,?\s*on|on)\s+(?:l['’]?\s*|les\s+)?a\s+"
    r"(?:test\w*|evalu\w*|mesur\w*|compar\w*|gener\w*|developp\w*|implement\w*|"
    r"configur\w*|adapt\w*|entrain\w*|extrai\w*|reinje\w*|utilis\w*|fait|"
    r"chois\w*|appliqu\w*|constru\w*|execut\w*|lanc\w*|calcul\w*|analys\w*|"
    r"identifi\w*|modifi\w*|travaill\w*|inspir\w*)\b|"
    r"\b(?:a ete|ont ete)\s+(?:test\w*|evalu\w*|mesur\w*|compar\w*|"
    r"implement\w*|configur\w*|appliqu\w*|execut\w*|analys\w*)\b|"
    r"\b(?:tested|evaluated|measured|compared|generated|implemented|trained|performed)\b",
    re.I,
)

_OBSERVED_OUTCOME_RE = re.compile(
    r"\b(?:nous avons|nous on a|on a)\s+(?:observe|mesure|obtenu|constate|atteint)\b|"
    r"\bon obtient\b|"
    r"\b(?:resultat|performance|score|taux|coverage|couverture|compilabilite)\b"
    r".{0,100}\b(?:acceptable|faible|eleve|meilleur|inferieur|superieur|stable|instable)\b|"
    r"\bn['’]?a pas (?:trop )?(?:monte|augmente|ameliore|progresse)\b|"
    r"\bn['’]?ont pas (?:trop )?(?:monte|augmente|ameliore|progresse)\b|"
    r"\b(?:a donne|ont donne|a montre|ont montre)\b",
    re.I,
)

_RESULT_RE = re.compile(
    r"\b(?:resultat(?:s)?(?: obtenu(?:s|es)?)?|observation(?:s)?|"
    r"nous avons (?:observe|mesure|obtenu|constate)|on a (?:observe|mesure|obtenu|constate)|"
    r"a (?:montre|donne|atteint)|ont (?:montre|donne|atteint)|"
    r"n['’]?a pas (?:ameliore|augmente|monte)|n['’]?ont pas (?:ameliore|augmente)|"
    r"pas trop (?:monte|augmente|ameliore)|"
    r"measured|observed|obtained|achieved|failed|improved|decreased|increased|"
    r"compilabilite|coverage|couverture|taux|score|performance)\b",
    re.I,
)

_METHOD_ONLY_RE = re.compile(
    r"^\s*(?:approche|methode|technique|strategie|processus|procedure)\b",
    re.I,
)

_PARAMETER_RE = re.compile(
    r"\b(?:parametre|hyperparametre|configuration|fenetre de contexte|"
    r"seuil|limite|maximum|minimum|taille|nombre de|temperature|pression|"
    r"debit|dimension|pas|taux|token|epoch|batch|learning rate|"
    r"contrainte technique|ressource|memoire|gpu|cpu)\b",
    re.I,
)

_NON_NUMERIC_CONSTRAINT_RE = re.compile(
    r"\b(?:contrainte\w*|limite\w*|ressource\w*|capacite\w*|maximum|minimum|seuil\w*|"
    r"compatibilite\w*|confidentialite\w*|souverainete\w*|disponibilite\w*|memoire\w*|"
    r"gpu|cpu|latence\w*|enveloppe technique|restriction\w*|obligation\w*)\b",
    re.I,
)


_QUESTION_OR_INTERVIEW_RE = re.compile(
    r"\b(?:est[- ]?ce que|avez[- ]?vous|pouvez[- ]?vous|"
    r"d['’]?apres ce que j['’]?ai compris|si j['’]?ai bien compris|"
    r"comment vous|pourquoi vous|qu['’]?est[- ]?ce que|"
    r"nous avons besoin de|on a besoin de|il faudrait (?:nous )?(?:envoyer|fournir)|"
    r"je peux vous envoyer|pouvez vous envoyer)\b",
    re.I,
)

_ADMIN_OR_TRACE_RE = re.compile(
    r"\b(?:traces? ecrites?|structurer le dossier technique|"
    r"presenter ici|envoyer les graphiques|document supplementaire|"
    r"piece jointe|voir annexe|a completer|a documenter|"
    r"consigne|commentaire de relecture|masque des diapositives|menu affichage|"
    r"indiquez le niveau de confidentialite|niveau de diffusion)\b",
    re.I,
)

_DOCUMENT_LIST_RE = re.compile(
    r"(?:\.(?:pdf|docx?|msg|pptx?|xlsx?|txt)\b.*){2,}",
    re.I | re.S,
)

_SECTION_MARKER_RE = re.compile(
    r"^\s*\[(?:SECTION|TABLEAU|TABLE|SLIDE)\s*[:|][^\]]+\]\s*$",
    re.I,
)

_TRANSCRIPTION_RE = re.compile(
    r"\b(?:transcription|enregistrement(?: de la reunion)?|meeting recording)\b",
    re.I,
)

_NUMERIC_RE = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?(?:\s*[%A-Za-z°/]+)?", re.I)

_RESULT_DOCUMENT_TYPE_RE = re.compile(
    r"\b(?:resultats?_mesures?|resultats?|mesures?|benchmark_result|"
    r"experimental_results?|test_results?)\b",
    re.I,
)

_PROJECT_DOCUMENT_TYPE_RE = re.compile(
    r"\b(?:concept_projet|methodologie_protocole|resultats?_mesures?|"
    r"rapport_essai|compte_rendu|specification_projet|project_core)\b",
    re.I,
)


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str
    confidence: float = 0.0


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _metadata(source: Mapping[str, Any]) -> Mapping[str, Any]:
    value = source.get("metadata") if isinstance(source, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def _first(source: Mapping[str, Any], *keys: str) -> Any:
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


def _roles(source: Mapping[str, Any]) -> Set[str]:
    output: Set[str] = set()
    meta = _metadata(source)
    for key in (
        "role", "semantic_role", "original_model_role", "final_role",
        "model_role", "candidate_role", "section_role_hint", "section_type",
        "operation_function", "proof_kind",
    ):
        for raw in (source.get(key), meta.get(key)):
            value = _norm(raw).replace(" ", "_")
            if not value:
                continue
            output.add(_ROLE_ALIASES.get(value, value))
            for alias, canonical in _ROLE_ALIASES.items():
                if alias in value:
                    output.add(canonical)
    return output


def _section_text(source: Mapping[str, Any]) -> str:
    return _norm(" ".join(str(_first(source, key) or "") for key in (
        "section_title", "section_path", "heading_path", "source_zone",
        "document_section_type",
    )))


def _body_text(source: Mapping[str, Any]) -> str:
    return _norm(" ".join(str(_first(source, key) or "") for key in (
        "context_before", "text", "excerpt", "analysis_text",
        "source_text_original", "context_after", "summary_fr",
    )))


def _document_text(source: Mapping[str, Any]) -> str:
    return _norm(" ".join(str(_first(source, key) or "") for key in (
        "document", "document_name", "source_path", "document_type",
        "document_category", "source_type", "content_origin",
    )))


def is_transcription_source(source: Mapping[str, Any]) -> bool:
    if _truthy(_first(source, "transcription_like", "is_transcription")):
        return True
    return bool(_TRANSCRIPTION_RE.search(_document_text(source)))


def is_external_or_reference(source: Mapping[str, Any]) -> bool:
    if _truthy(_first(
        source,
        "reference_like", "is_state_of_art", "state_of_art",
        "is_external_literature", "external_literature",
        "literature_only", "bibliographic_source", "reference_only",
    )):
        return True
    origin = _norm(_first(source, "evidence_origin", "provenance_origin"))
    if origin in {"external_literature", "literature", "scientific_article", "state_of_art"}:
        return True
    conflicts = _norm(_first(source, "semantic_role_conflicts", "role_conflicts"))
    if "etat_art" in conflicts or "state_of_art" in conflicts:
        return True
    section = _section_text(source)
    body = _body_text(source)
    if _STATE_OF_ART_RE.search(section):
        return True
    if _EXTERNAL_ATTRIBUTION_RE.search(f"{section} {body}"):
        return True
    if _GENERIC_LITERATURE_RESULT_RE.search(body) and not _PROJECT_ACTOR_RE.search(body):
        return True
    return False


def is_noise_or_interview(source: Mapping[str, Any]) -> bool:
    body = _body_text(source)
    raw = str(_first(source, "excerpt", "text", "analysis_text", "source_text_original") or "").strip()
    if not body:
        return True
    if _SECTION_MARKER_RE.match(raw):
        return True
    if _DOCUMENT_LIST_RE.search(raw):
        return True
    if _ADMIN_OR_TRACE_RE.search(body):
        return True

    # Une question de réunion ne devient jamais un fait. On tolère un passage
    # mixte si une réponse projet explicite et une action/résultat suivent.
    question_signal = bool(_QUESTION_OR_INTERVIEW_RE.search(body))
    answer_signal = bool(
        _PROJECT_ACTOR_RE.search(body)
        and (_EXECUTED_ACTION_RE.search(body) or _RESULT_RE.search(body) or _OBJECTIVE_RE.search(body))
    )
    if question_signal and not answer_signal:
        return True

    # Les lignes de navigation/table des matières ne sont pas des faits.
    if len(body) < 28 and (
        body.startswith("section ")
        or body.startswith("tableau ")
        or body.startswith("slide ")
    ):
        return True
    return False


def _project_direct_signal(source: Mapping[str, Any]) -> bool:
    origin = _norm(_first(source, "evidence_origin", "provenance_origin"))
    actor = _norm(_first(source, "actor_scope", "provenance_actor"))
    if origin == "project_direct" or actor == "project_team":
        return True
    body = _body_text(source)
    section = _section_text(source)
    if _PROJECT_ACTOR_RE.search(body):
        return True
    if _PROJECT_WORK_SECTION_RE.search(section):
        return True
    return False


def _structured_execution(source: Mapping[str, Any]) -> str:
    value = _norm(_first(
        source, "execution_status", "fact_status", "maturity_status",
    )).replace(" ", "_")
    return value


def _result_document_signal(source: Mapping[str, Any]) -> bool:
    doc_type = _norm(_first(source, "document_type", "document_category"))
    return bool(_RESULT_DOCUMENT_TYPE_RE.search(doc_type))


def _project_document_signal(source: Mapping[str, Any]) -> bool:
    doc_type = _norm(_first(source, "document_type", "document_category", "content_origin"))
    return bool(_PROJECT_DOCUMENT_TYPE_RE.search(doc_type))


def has_unverified_transcription_numeric(source: Mapping[str, Any]) -> bool:
    if _truthy(_first(source, "unverified_transcription_numeric")):
        return True
    if not is_transcription_source(source):
        return False
    body = _body_text(source)
    if not _NUMERIC_RE.search(body):
        return False
    # Une étape amont peut explicitement marquer la corroboration.
    if _truthy(_first(source, "numeric_corroborated", "corroborated_numeric")):
        return False
    return True


def gate_project_fact(source: Mapping[str, Any], section_key: str) -> GateDecision:
    """Décide si une preuve peut être publiée dans une section projet.

    La fonction est volontairement conservatrice : une preuve rejetée reste
    disponible pour l'audit et l'état de l'art, mais n'est pas transformée en
    fait du projet courant.
    """
    if not isinstance(source, Mapping):
        return GateDecision(False, "not_mapping", 1.0)

    key = str(section_key or "")
    if key not in PROJECT_FACT_SECTIONS:
        return GateDecision(True, "section_not_gated", 1.0)

    if is_external_or_reference(source):
        return GateDecision(False, "external_or_state_of_art", 1.0)
    if is_noise_or_interview(source):
        return GateDecision(False, "question_admin_or_document_noise", 0.98)

    roles = _roles(source)
    body = _body_text(source)
    section = _section_text(source)
    project_direct = _project_direct_signal(source)
    execution = _structured_execution(source)
    project_doc = _project_document_signal(source)

    if key == "objectif_global":
        if roles and "objectif" not in roles and "contribution" not in roles:
            return GateDecision(False, "wrong_semantic_role_for_objective", 0.95)
        if not _OBJECTIVE_RE.search(body):
            return GateDecision(False, "no_explicit_objective_signal", 0.97)
        # Un objectif doit décrire une finalité, pas un résultat déjà observé.
        if _RESULT_RE.search(body) and not _OBJECTIVE_RE.search(body):
            return GateDecision(False, "observed_result_not_objective", 0.9)
        if not project_direct:
            return GateDecision(False, "objective_actor_not_project_anchored", 0.98)
        return GateDecision(True, "project_objective", 0.95)

    if key == "demarche_detectee":
        if roles and not (roles & {"methode", "parametre"}):
            return GateDecision(False, "wrong_semantic_role_for_method", 0.95)
        # Un operation_function NLP ne suffit pas : il faut aussi un acteur
        # projet, une section de travaux projet ou une provenance project_direct.
        executed = execution in {
            "implemented", "experimented", "observed", "measured", "active_constraint",
        }
        if not (project_direct or (_PROJECT_WORK_SECTION_RE.search(section) and project_doc)):
            return GateDecision(False, "method_not_attributed_to_project_team", 0.98)
        if not (_EXECUTED_ACTION_RE.search(body) or executed):
            return GateDecision(False, "method_not_executed", 0.98)
        if _METHOD_ONLY_RE.search(body) and not (
            _PROJECT_ACTOR_RE.search(body) or _PROJECT_WORK_SECTION_RE.search(section)
        ):
            return GateDecision(False, "generic_method_description", 0.95)
        return GateDecision(True, "executed_project_method", 0.95)

    if key == "resultats_metriques":
        if roles and not (roles & {"resultat", "contribution"}):
            return GateDecision(False, "wrong_semantic_role_for_result", 0.95)
        # Les tableaux de résultats/mesures du projet sont acceptés sans pronom
        # d'acteur, mais les tableaux de littérature ne le sont jamais (testés
        # plus haut). Une transcription doit porter une action + observation.
        explicit_observation = bool(_OBSERVED_OUTCOME_RE.search(body))
        result_doc = _result_document_signal(source)
        if is_transcription_source(source):
            if not (project_direct and explicit_observation):
                return GateDecision(False, "transcript_not_a_confirmed_project_result", 0.98)
        elif not (
            (project_direct and explicit_observation)
            or (result_doc and (explicit_observation or bool(_NUMERIC_RE.search(body))))
        ):
            return GateDecision(False, "result_not_observed_or_not_project_owned", 0.97)
        if _METHOD_ONLY_RE.search(body) and not explicit_observation:
            return GateDecision(False, "method_described_as_result", 0.95)
        return GateDecision(True, "observed_project_result", 0.95)

    if key == "parametres_contraintes":
        if roles and not (roles & {"parametre", "limite"}):
            return GateDecision(False, "wrong_semantic_role_for_parameter", 0.95)
        if has_unverified_transcription_numeric(source):
            return GateDecision(False, "unverified_numeric_from_transcription", 1.0)
        if not (_PARAMETER_RE.search(body) or roles & {"parametre", "limite"}):
            return GateDecision(False, "no_parameter_or_constraint_signal", 0.9)
        has_numeric = bool(_NUMERIC_RE.search(body))
        if has_numeric:
            if not (
                project_direct
                or (_PROJECT_WORK_SECTION_RE.search(section) and project_doc)
            ):
                return GateDecision(False, "numeric_parameter_not_project_anchored", 0.98)
        else:
            if not _NON_NUMERIC_CONSTRAINT_RE.search(body):
                return GateDecision(False, "non_numeric_method_or_metric_not_parameter", 0.97)
            if not (project_direct or project_doc):
                return GateDecision(False, "parameter_not_project_anchored", 0.97)
        return GateDecision(True, "project_parameter_or_constraint", 0.93)

    # Synthèse : on n'autorise que des preuves qui seraient publiables comme
    # objectif, démarche, résultat, paramètre, ou une preuve de verrou/limite.
    if key == "synthese_strategique":
        if roles & {"verrou", "limite"} and (project_direct or project_doc):
            return GateDecision(True, "project_lock_context", 0.9)
        for child in (
            "objectif_global", "demarche_detectee",
            "resultats_metriques", "parametres_contraintes",
        ):
            decision = gate_project_fact(source, child)
            if decision.allowed:
                return GateDecision(True, f"synthesis_via_{child}", decision.confidence)
        return GateDecision(False, "not_a_publishable_project_fact", 0.95)

    return GateDecision(True, "allowed", 1.0)


def filter_project_facts(
    sources: Iterable[Mapping[str, Any]],
    section_key: str,
    *,
    max_items: Optional[int] = None,
) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()
    for raw in sources or []:
        if not isinstance(raw, Mapping):
            continue
        decision = gate_project_fact(raw, section_key)
        if not decision.allowed:
            continue
        item = dict(raw)
        item["project_fact_gate"] = {
            "allowed": True,
            "reason": decision.reason,
            "confidence": decision.confidence,
        }
        signature = (
            _norm(_first(item, "document", "document_name", "source_path")),
            _body_text(item)[:800],
        )
        if signature in seen:
            continue
        seen.add(signature)
        output.append(item)
        if max_items is not None and len(output) >= int(max_items):
            break
    return output


def explain_rejection(source: Mapping[str, Any], section_key: str) -> str:
    return gate_project_fact(source, section_key).reason
