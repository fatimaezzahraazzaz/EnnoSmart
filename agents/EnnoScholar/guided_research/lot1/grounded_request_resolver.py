# -*- coding: utf-8 -*-
from __future__ import annotations

# ENNOSCHOLAR_V170_2_PLAN_EDITING_READBACK_FIX

# ENNOSCHOLAR_V170_1_CONVERSATION_ROUTING_FIX

"""Réparation déterministe et grounded des demandes EnnoScholar.

V2 ajoute à la V1 :
- exécution naturelle « garde/valide ce plan et rédige » dans un seul tour ;
- reconnaissance du corpus déjà disponible (« articles existants », « déjà trouvés », etc.) ;
- maintien des garde-fous : aucune validation de nouvelles sources, aucun nouveau verrou
  et aucune recherche ne sont déclenchés implicitement.
"""

import re
import unicodedata
from typing import Any, Iterable, Mapping

from .domain.enums import ConsultantIntent
from .domain.models import IntentClassification

_NUMBER_WORDS = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4,
    "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
}

_ACTIONABLE_GLOBAL_INTENTS = {
    ConsultantIntent.PROPOSE_PLAN,
    ConsultantIntent.START_WRITING,
    ConsultantIntent.REVISE_DRAFT,
    ConsultantIntent.DESCRIBE_REQUIREMENTS,
}


def _clean(value: Any, limit: int = 12000) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[:limit]


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _known_verrous(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        title = _clean(row.get("title"), 700)
        identifier = _clean(row.get("id"), 120)
        if title and identifier:
            output.append(dict(row))
    return output


def _asks_for_all_verrous(normalized: str, known_count: int) -> bool:
    if not normalized or known_count <= 0:
        return False
    direct_patterns = (
        r"\b(?:tous|tout)\s+les\s+verrous?\b",
        r"\b(?:tous|tout)\s+(?:mes|ces|nos|vos)\s+verrous?\b",
        r"\bl\s+ensemble\s+(?:de|des)\s+verrous?\b",
        r"\bensemble\s+(?:de|des)\s+verrous?\b",
        r"\bla\s+totalite\s+des\s+verrous?\b",
        r"\bglobalement\s+(?:sur|pour)\s+(?:les|tous les)\s+verrous?\b",
    )
    if any(re.search(pattern, normalized) for pattern in direct_patterns):
        return True
    numeric = re.search(r"\b(?:les|ces|nos|mes|vos)\s+(\d{1,2})\s+verrous?\b", normalized)
    if numeric and int(numeric.group(1)) == known_count:
        return True
    words = "|".join(_NUMBER_WORDS)
    word_match = re.search(rf"\b(?:les|ces|nos|mes|vos)\s+({words})\s+verrous?\b", normalized)
    return bool(word_match and _NUMBER_WORDS.get(word_match.group(1)) == known_count)


def _asks_for_state_of_art(normalized: str) -> bool:
    return any(marker in normalized for marker in (
        "etat de l art", "etat de lart", "revue de litterature",
        "revue bibliographique", "state of the art",
    ))


def _asks_to_write(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(?:redig\w*|ecri\w*|produi\w*|gener\w*|"
            r"commenc\w*|lanc\w*)\b",
            normalized,
        )
        or re.search(
            r"\b(?:fais|faire)\b.{0,50}\b(?:redaction|texte|document|"
            r"etat\s+de\s+l\s+art)\b",
            normalized,
        )
    )


def _asks_to_keep_or_approve_plan(normalized: str) -> bool:
    patterns = (
        r"\b(?:garde|gardons|conserve|conservons)\s+"
        r"(?:(?:ce|le)\s+)?(?:meme\s+)?plan\b",
        r"\bon\s+(?:peut\s+)?garde\s+(?:ce|le|meme|même)?\s*plan\b",
        r"\b(?:je\s+)?valid\w*\s+(?:ce|le)\s+plan\b",
        r"\b(?:ce|le)\s+plan\s+(?:me\s+convient|est\s+bon|est\s+valide|est\s+validé)\b",
        r"\b(?:ok|d accord|accord)\s+(?:pour|avec)\s+(?:ce|le)\s+plan\b",
        r"\bon\s+peut\s+(?:partir|continuer)\s+avec\s+(?:ce|le)\s+plan\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _asks_existing_validated_sources(normalized: str) -> bool:
    if not re.search(r"\b(?:articles?|sources?|publications?|corpus)\b", normalized):
        return False
    markers = (
        "articles existants", "sources existantes", "publications existantes",
        "articles deja existants", "sources deja existantes",
        "articles deja trouves", "sources deja trouvees", "publications deja trouvees",
        "articles deja selectionnes", "sources deja selectionnees",
        "articles qu on a", "sources qu on a", "publications qu on a",
        "articles qu on a deja", "sources qu on a deja",
        "articles gardes", "sources gardees", "articles conserves", "sources conservees",
        "articles retenus", "sources retenues", "articles valides", "sources validees",
        "articles actuels", "sources actuelles", "corpus actuel", "corpus valide",
    )
    return any(marker in normalized for marker in markers)


def _asks_plan_readback(normalized: str) -> bool:
    """Lecture seule du plan courant demandée explicitement par le consultant."""
    if not normalized:
        return False
    # Une approbation du consultant n'est jamais une simple lecture.
    if _asks_to_keep_or_approve_plan(normalized):
        return False
    patterns = (
        r"\b(?:affich\w*|montr\w*|donne\w*|rappel\w*)\s+(?:moi\s+)?(?:maintenant\s+)?(?:le\s+)?plan\b",
        r"\bplan\s+courant\s+(?:complet|actuel)\b",
        r"\b(?:affich\w*|montr\w*|donne\w*|rappel\w*)\s+(?:moi\s+)?(?:les\s+)?(?:titres|sections|parties)\b",
        r"\bconfirme\s+moi\s+(?:simplement\s+)?(?:les\s+)?(?:titres|sections|parties)\b",
        r"\btitres\s+que\s+tu\s+vas\s+utilis\w*\b",
        r"\bquels?\s+(?:sont\s+)?(?:les\s+)?titres\b",
        r"\bliste\s+(?:moi\s+)?(?:les\s+)?(?:titres|sections|parties)\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _plan_insert_after_target_id(
    normalized: str,
    current_plan: Iterable[Mapping[str, Any]],
) -> str:
    """Résout une insertion de section à partir d'une position explicite.

    Exemples : « ajoute après la partie 3 ... » ou, avec trois sections
    existantes, « ajoute la partie 4 ... ». Le contenu de la nouvelle section
    reste produit par le payload structuré ; cette fonction ne fabrique aucun
    titre scientifique.
    """
    plan = [dict(row) for row in current_plan or [] if isinstance(row, Mapping)]
    if not normalized or not plan:
        return ""
    if not re.search(r"\b(?:ajout\w*|inser\w*)\b", normalized):
        return ""

    ordinal = None
    match = re.search(
        r"\bapres\s+(?:la\s+|le\s+)?(?:partie|section|chapitre)\s+(\d{1,2})\b",
        normalized,
    )
    if match:
        ordinal = int(match.group(1))
    else:
        # « ajoute la partie 4 ... » avec un plan courant de 3 sections = append.
        match = re.search(
            r"\b(?:ajout\w*|inser\w*)\s+(?:la\s+|le\s+)?(?:partie|section|chapitre)\s+(\d{1,2})\b",
            normalized,
        )
        if match and int(match.group(1)) == len(plan) + 1:
            ordinal = len(plan)

    if ordinal is None or not (1 <= ordinal <= len(plan)):
        return ""
    target = plan[ordinal - 1]
    return _clean(target.get("section_id"), 200)


def _asks_full_plan_replacement(normalized: str) -> bool:
    """Détecte une substitution explicite du plan courant, sans inférer le domaine.

    Ce garde-fou ne construit jamais le plan : il empêche seulement qu'une
    instruction de structure explicite soit routée vers la recherche. Le vrai
    contenu du plan reste extrait ensuite du message consultant par le payload
    structuré.
    """
    if not normalized or "plan" not in normalized:
        return False
    patterns = (
        r"\butilis\w*\s+(?:exactement\s+)?(?:ce|le)\s+plan\b",
        r"\bprend\w*\s+(?:exactement\s+)?(?:ce|le)\s+plan\b",
        r"\bgard\w*\s+(?:exactement\s+)?(?:ce|le)\s+plan\s+(?:a\s+la\s+place|plutot)\b",
        r"\bremplac\w*\s+(?:(?:ce|le|l\s+ancien)\s+)?plan\b",
        r"\b(?:ce|le)\s+plan\s+(?:a\s+la\s+place|plutot)\b",
        r"\bexactement\s+(?:ce|le)\s+plan\b",
        r"\bplan\s+(?:suivant|ci\s+dessous)\b",
        r"\buse\s+exactly\s+this\s+plan\b",
        r"\breplace\s+(?:the|this|current)\s+plan\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _asks_for_research(normalized: str) -> bool:
    """Reconnaît une recherche explicite, y compris avec fautes courantes."""
    return bool(
        re.search(
            r"\b(?:rech\w*|cherch\w*|identifi\w*|localis\w*)\b"
            r".{0,90}\b(?:articles?|sources?|publications?|papers?)\b|"
            r"\b(?:articles?|sources?|publications?|papers?)\b"
            r".{0,90}\b(?:rech\w*|cherch\w*|identifi\w*)\b|"
            r"(?:^|\b(?:fais|fait|veux|veut|souhaite|pour|afin\s+de|"
            r"merci\s+de)\s+)trouv\w*\b.{0,90}"
            r"\b(?:articles?|sources?|publications?|papers?)\b",
            normalized,
        )
        or bool(re.search(r"\b(?:fais|fait|lance)\s+(?:des?\s+)?rech\w*\b", normalized))
    )


def _explicitly_declares_new_verrou(normalized: str) -> bool:
    """Exige que l'adjectif/action porte réellement sur le mot verrou."""
    if re.search(
        r"\b(?:ce|ca|cela)\s+n\s+est\s+pas\s+(?:un\s+)?verrou\b|"
        r"\b(?:pas|sans)\s+(?:creer|ajouter|enregistrer)\s+"
        r"(?:un\s+)?(?:nouveau\s+)?verrou\b|"
        r"\bpas\s+(?:un|de)\s+verrou\b",
        normalized,
    ):
        return False
    patterns = (
        r"\b(?:ajout\w*|cre\w*|enregistr\w*)\s+(?:un\s+)?"
        r"(?:nouveau\s+)?verrou\b",
        r"\b(?:nouveau|nouvelle|autre|second|deuxieme|manquant)\s+verrou\b",
        r"\bverrou\s+(?:nouveau|distinct|supplementaire|manquant|a\s+ajouter)\b",
        r"\bil\s+(?:manque|faut)\s+(?:un\s+)?(?:nouveau\s+)?verrou\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _supplements_existing_scientific_scope(normalized: str) -> bool:
    """Distingue un complément rédactionnel d'un nouveau verrou."""
    structural_target = bool(
        re.search(
            r"\b(?:section|sous\s+section|paragraphe|passage|partie|chapitre|"
            r"plan|texte|redaction|etat\s+de\s+l\s+art)\b",
            normalized,
        )
    )
    additive_language = bool(
        re.search(
            r"\b(?:ajout\w*|inser\w*|integr\w*|complet\w*|enrich\w*|"
            r"develop\w*|parler|traiter|inclure)\b",
            normalized,
        )
    )
    supports_existing_lock = bool(
        re.search(
            r"\b(?:argument\w*|renforc\w*|etay\w*|appuy\w*|document\w*|"
            r"illustr\w*|complet\w*)\b.{0,80}\bverrou\b|"
            r"\bverrou\b.{0,80}\b(?:initial|existant|actuel|principal|"
            r"depart|argument\w*|renforc\w*|etay\w*)\b",
            normalized,
        )
    )
    explicit_non_verrou = bool(
        re.search(
            r"\b(?:ce|ca|cela)\s+n\s+est\s+pas\s+(?:un\s+)?verrou\b|"
            r"\b(?:pas\s+un\s+verrou|juste\s+un\s+plus|"
            r"simple\s+complement)\b",
            normalized,
        )
    )
    return bool(
        (structural_target and additive_language)
        or supports_existing_lock
        or explicit_non_verrou
    )


def _grounded_existing_verrou_targets(
    classification: IntentClassification,
    rows: list[dict[str, Any]],
    context: Mapping[str, Any],
) -> list[str]:
    known = {
        _clean(row.get("id"), 120).casefold(): _clean(row.get("id"), 120)
        for row in rows
        if _clean(row.get("id"), 120)
    }

    def valid(values: Iterable[Any]) -> list[str]:
        output: list[str] = []
        for value in values or []:
            key = _clean(value, 120).casefold()
            if key in known and known[key] not in output:
                output.append(known[key])
        return output

    active = valid(context.get("active_verrou_ids") or [])
    if active:
        return active
    classified = valid(classification.target_verrou_ids or [])
    if classified:
        return classified
    if len(rows) == 1:
        return [_clean(rows[0].get("id"), 120)]
    return []


def _append_classifier_marker(classification: IntentClassification, marker: str) -> None:
    current = _clean(classification.classifier, 300) or "llm_contextual"
    if marker not in current:
        classification.classifier = f"{current}+{marker}"


def _ensure_actions(classification: IntentClassification, *actions: ConsultantIntent) -> None:
    existing = [
        action for action in (classification.requested_actions or [])
        if action not in {ConsultantIntent.UNKNOWN, ConsultantIntent.CONVERSE}
    ]
    merged: list[ConsultantIntent] = []
    for action in [*actions, *existing]:
        if action not in merged and action not in classification.forbidden_actions:
            merged.append(action)
    classification.requested_actions = merged


def repair_contextual_classification(
    classification: IntentClassification,
    *,
    consultant_message: str,
    current_verrous: Iterable[Mapping[str, Any]],
    current_plan: Iterable[Mapping[str, Any]] | None = None,
    session_context: Mapping[str, Any] | None = None,
) -> IntentClassification:
    """Répare uniquement des cas prouvables par l'état courant de la session."""
    rows = _known_verrous(current_verrous)
    plan = [dict(row) for row in (current_plan or []) if isinstance(row, Mapping)]
    context = dict(session_context or {})
    normalized = _norm(consultant_message)
    asks_write = _asks_to_write(normalized)
    asks_state_of_art = _asks_for_state_of_art(normalized)
    asks_all = _asks_for_all_verrous(normalized, len(rows)) if rows else False
    asks_approval = _asks_to_keep_or_approve_plan(normalized)
    asks_existing_sources = _asks_existing_validated_sources(normalized)
    asks_full_plan_replacement = _asks_full_plan_replacement(normalized)
    asks_plan_readback = _asks_plan_readback(normalized)
    plan_insert_after_target_id = _plan_insert_after_target_id(normalized, plan)
    active_scope = _clean(context.get("review_scope"), 40).casefold()
    actions = set(classification.requested_actions or [])
    compound_from_schema = (
        ConsultantIntent.ACCEPT_PLAN in actions
        and ConsultantIntent.START_WRITING in actions
    )
    repaired = False

    # V170.2 — lecture du plan = opération strictement READ-ONLY. Même si le
    # message contient « avant de rédiger », il ne doit ni autoriser l'écriture,
    # ni calculer une politique de recherche, ni modifier l'approbation.
    if asks_plan_readback:
        classification.intent = ConsultantIntent.CONVERSE
        classification.requested_actions = []
        classification.explicit_research_command = False
        classification.explicit_write_command = False
        classification.explicit_plan_approval = False
        classification.replace_current_plan = False
        classification.plan_edit_scope = "none"
        classification.plan_edit_operation = "none"
        classification.target_section_ids = []
        classification.needs_clarification = False
        classification.forbidden_actions = list(dict.fromkeys([
            *(classification.forbidden_actions or []),
            ConsultantIntent.START_WRITING,
            ConsultantIntent.REVISE_DRAFT,
            ConsultantIntent.ACCEPT_PLAN,
            ConsultantIntent.PROPOSE_PLAN,
            ConsultantIntent.ADD_TOPIC,
            ConsultantIntent.REMOVE_TOPIC,
            ConsultantIntent.CHANGE_PLAN,
            ConsultantIntent.SEARCH_MORE,
            ConsultantIntent.SEARCH_ALTERNATIVE,
            ConsultantIntent.REPLACE_SOURCE,
            ConsultantIntent.ADD_VERROU_AND_SEARCH,
        ]))
        _append_classifier_marker(classification, "v170_2_plan_readback")
        repaired = True

    # V170.2 — insertion positionnelle d'une nouvelle section. La cible est
    # résolue contre le plan COURANT de cette conversation, jamais l'historique.
    elif plan_insert_after_target_id:
        classification.intent = ConsultantIntent.ADD_TOPIC
        classification.requested_actions = [ConsultantIntent.ADD_TOPIC]
        classification.explicit_research_command = False
        classification.explicit_write_command = False
        classification.explicit_plan_approval = False
        classification.replace_current_plan = False
        classification.plan_edit_scope = "local_section"
        classification.plan_edit_operation = "add"
        classification.target_section_ids = [plan_insert_after_target_id]
        classification.content_target = "existing_plan"
        classification.needs_clarification = False
        classification.forbidden_actions = list(dict.fromkeys([
            *(classification.forbidden_actions or []),
            ConsultantIntent.SEARCH_MORE,
            ConsultantIntent.SEARCH_ALTERNATIVE,
            ConsultantIntent.REPLACE_SOURCE,
            ConsultantIntent.ADD_VERROU_AND_SEARCH,
            ConsultantIntent.START_WRITING,
            ConsultantIntent.REVISE_DRAFT,
        ]))
        _append_classifier_marker(classification, "v170_2_insert_after_section")
        repaired = True

    # V170.1 — priorité absolue à une instruction explicite de remplacement
    # du plan. Le classifieur LLM peut parfois confondre les mots du contenu
    # scientifique du plan avec une demande de recherche. Ici la décision est
    # grounded uniquement dans le message courant : aucune recherche n'est
    # autorisée tant que le consultant demande de remplacer la structure.
    if asks_full_plan_replacement:
        classification.intent = ConsultantIntent.CHANGE_PLAN
        classification.requested_actions = [ConsultantIntent.CHANGE_PLAN]
        classification.explicit_research_command = False
        classification.explicit_write_command = False
        classification.explicit_plan_approval = False
        classification.replace_current_plan = True
        classification.plan_edit_scope = "full_plan"
        classification.plan_edit_operation = "modify"
        classification.target_section_ids = []
        classification.content_target = "existing_plan"
        classification.needs_clarification = False
        classification.writing_source_scope = "unspecified"
        classification.writing_source_identifiers = []
        classification.requested_source_count = None
        # Une action de recherche éventuellement halluciné par le premier LLM
        # est explicitement neutralisée pour ce tour.
        classification.forbidden_actions = list(dict.fromkeys([
            *(classification.forbidden_actions or []),
            ConsultantIntent.SEARCH_MORE,
            ConsultantIntent.SEARCH_ALTERNATIVE,
            ConsultantIntent.REPLACE_SOURCE,
            ConsultantIntent.ADD_VERROU_AND_SEARCH,
        ]))
        _append_classifier_marker(
            classification,
            "grounded_project_context_repair_v1",
        )
        _append_classifier_marker(
            classification,
            "v170_1_explicit_full_plan_replacement",
        )
        repaired = True

    # V3 — recherche destinée à enrichir une section, un paragraphe ou
    # l'argumentation du verrou actif. « Nouveau paragraphe » ne signifie
    # jamais « nouveau verrou ». La création reste réservée à une déclaration
    # où l'action ou l'adjectif porte explicitement sur le mot verrou.
    semantic_support = bool(
        classification.scientific_scope_relation
        == "supports_existing_verrou"
        or classification.content_target
        in {
            "existing_section",
            "existing_paragraph",
            "existing_draft",
            "existing_verrou",
        }
    )
    semantic_research = bool(
        not asks_full_plan_replacement
        and not asks_plan_readback
        and not plan_insert_after_target_id
        and (
            classification.explicit_research_command
        or classification.intent
        in {
            ConsultantIntent.SEARCH_MORE,
            ConsultantIntent.SEARCH_ALTERNATIVE,
            ConsultantIntent.REPLACE_SOURCE,
            ConsultantIntent.ADD_VERROU_AND_SEARCH,
        }
        or any(
            action
            in {
                ConsultantIntent.SEARCH_MORE,
                ConsultantIntent.SEARCH_ALTERNATIVE,
                ConsultantIntent.REPLACE_SOURCE,
                ConsultantIntent.ADD_VERROU_AND_SEARCH,
            }
            for action in (classification.requested_actions or [])
        )
        )
    )
    textual_safety_net = bool(
        not asks_full_plan_replacement
        and not asks_plan_readback
        and not plan_insert_after_target_id
        and _asks_for_research(normalized)
        and _supplements_existing_scientific_scope(normalized)
    )
    semantic_supplement = bool(
        semantic_research
        and semantic_support
        and classification.scientific_scope_relation
        != "declares_new_verrou"
        and classification.content_target != "new_verrou"
    )
    textual_supplement = bool(
        textual_safety_net
        and not _explicitly_declares_new_verrou(normalized)
    )
    supplemental_research = bool(
        rows and (semantic_supplement or textual_supplement)
    )
    if supplemental_research:
        targets = _grounded_existing_verrou_targets(
            classification,
            rows,
            context,
        )
        classification.intent = ConsultantIntent.SEARCH_MORE
        classification.requested_actions = [ConsultantIntent.SEARCH_MORE]
        classification.forbidden_actions = list(
            dict.fromkeys(
                [
                    *(
                        action
                        for action in (classification.forbidden_actions or [])
                        if action != ConsultantIntent.SEARCH_MORE
                    ),
                    ConsultantIntent.ADD_VERROU_AND_SEARCH,
                ]
            )
        )
        classification.explicit_research_command = True
        classification.explicit_new_verrou_declaration = False
        classification.scientific_scope_relation = (
            "supports_existing_verrou"
        )
        if classification.content_target in {"none", "new_verrou"}:
            classification.content_target = (
                "existing_section"
                if re.search(
                    r"\b(?:section|sous\s+section|paragraphe|passage|partie)\b",
                    normalized,
                )
                else "existing_verrou"
            )
        classification.explicit_write_command = False
        classification.explicit_plan_approval = False
        classification.replace_current_plan = False
        classification.needs_clarification = False
        if targets:
            classification.verrou_scope = "per_verrou"
            classification.target_verrou_ids = targets
        else:
            classification.verrou_scope = "unchanged"
            classification.target_verrou_ids = []
        _append_classifier_marker(
            classification,
            "existing_scope_research_repair_v3",
        )
        repaired = True

    # V2 — plan déjà présent + approbation et ordre d'écriture dans le même tour.
    # On route directement vers START_WRITING : _start_writing sait approuver le
    # contrat de manière atomique avant authorize_writing().
    if (
        not repaired
        and plan
        and asks_write
        and (asks_approval or compound_from_schema)
        and classification.intent
        not in {
            ConsultantIntent.PROPOSE_PLAN,
            ConsultantIntent.ADD_TOPIC,
            ConsultantIntent.REMOVE_TOPIC,
            ConsultantIntent.CHANGE_PLAN,
        }
        and not classification.replace_current_plan
    ):
        classification.intent = ConsultantIntent.START_WRITING
        classification.explicit_write_command = True
        classification.explicit_plan_approval = True
        classification.needs_clarification = False
        classification.replace_current_plan = False
        classification.forbidden_actions = [
            action for action in (classification.forbidden_actions or [])
            if action not in {ConsultantIntent.ACCEPT_PLAN, ConsultantIntent.START_WRITING}
        ]
        _ensure_actions(
            classification,
            ConsultantIntent.ACCEPT_PLAN,
            ConsultantIntent.START_WRITING,
        )
        if asks_all:
            classification.verrou_scope = "global"
            classification.target_verrou_ids = []
        if asks_existing_sources:
            classification.use_current_sources_only = True
            classification.writing_source_scope = "all_validated"
            classification.writing_source_identifiers = []
            classification.requested_source_count = None
        _append_classifier_marker(classification, "compound_action_repair_v2")
        repaired = True

    # V1 — rédiger l'état de l'art pour tous les verrous connus.
    elif not repaired and rows and asks_write and (asks_state_of_art or plan) and asks_all:
        classification.verrou_scope = "global"
        classification.target_verrou_ids = []
        classification.explicit_write_command = True
        classification.needs_clarification = False
        classification.forbidden_actions = [
            action for action in (classification.forbidden_actions or [])
            if action not in {ConsultantIntent.PROPOSE_PLAN, ConsultantIntent.START_WRITING}
        ]
        if plan:
            classification.intent = ConsultantIntent.START_WRITING
            classification.replace_current_plan = False
            _ensure_actions(classification, ConsultantIntent.START_WRITING)
        else:
            classification.intent = ConsultantIntent.PROPOSE_PLAN
            classification.replace_current_plan = True
            _ensure_actions(classification, ConsultantIntent.PROPOSE_PLAN, ConsultantIntent.START_WRITING)
        if asks_existing_sources:
            classification.use_current_sources_only = True
            classification.writing_source_scope = "all_validated"
        repaired = True

    # V1 — « rédige maintenant » lorsque le scope et le plan sont déjà établis.
    elif not repaired and rows and asks_write and plan and active_scope in {"global", "per_verrou"}:
        classification.intent = ConsultantIntent.START_WRITING
        classification.explicit_write_command = True
        classification.needs_clarification = False
        classification.replace_current_plan = False
        if active_scope == "global":
            classification.verrou_scope = "global"
            classification.target_verrou_ids = []
        if asks_existing_sources:
            classification.use_current_sources_only = True
            classification.writing_source_scope = "all_validated"
        _ensure_actions(classification, ConsultantIntent.START_WRITING)
        repaired = True

    # V1 — scope global déjà parfaitement résolu.
    elif not repaired and rows and (
        classification.needs_clarification
        and classification.verrou_scope == "global"
        and classification.intent in _ACTIONABLE_GLOBAL_INTENTS
    ):
        classification.needs_clarification = False
        classification.target_verrou_ids = []
        repaired = True

    # V2 — même sans réparation d'intention, une formulation explicite du corpus
    # courant doit empêcher une recherche complémentaire automatique.
    if asks_write and asks_existing_sources:
        classification.use_current_sources_only = True
        if classification.writing_source_scope == "unspecified":
            classification.writing_source_scope = "all_validated"
            classification.writing_source_identifiers = []
            classification.requested_source_count = None
        _append_classifier_marker(classification, "existing_sources_scope_v2")

    if repaired:
        # Conserver le marqueur V1 car les adaptateurs V1 l'utilisent déjà pour
        # produire une réponse naturelle après réparation.
        _append_classifier_marker(classification, "grounded_project_context_repair_v1")
        suffix = (
            " Périmètre résolu à partir de l'état réellement présent dans la "
            "session ; aucune cible, source ou approbation n'a été inventée."
        )
        rationale = _clean(classification.rationale, 2400)
        if suffix.strip() not in rationale:
            classification.rationale = (rationale + suffix).strip()
        classification.corrected_message = (
            _clean(classification.corrected_message, 12000)
            or _clean(consultant_message, 12000)
        )
        classification.extracted_text = (
            _clean(classification.extracted_text, 12000)
            or _clean(consultant_message, 12000)
        )
    return classification


__all__ = ["repair_contextual_classification"]
