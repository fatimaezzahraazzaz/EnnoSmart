from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from ..domain.models import ImprovementIntent, RoutingDecision, TargetScope


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def _fold(text: str) -> str:
    """Normalise les accents sans modifier le texte transmis aux autres services."""

    value = unicodedata.normalize("NFKD", str(text or "").casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return value.replace("’", "'")


def _has_near_word(text: str, *expected: str, cutoff: float = 0.84) -> bool:
    """Tolère une petite faute sur des marqueurs d'intention longs et ciblés.

    Le rapprochement n'est volontairement appliqué qu'aux mots fournis par
    l'appelant. Il ne sert jamais à reconnaître un nom de technologie, de
    section ou de projet.
    """

    words = re.findall(r"[a-z0-9&-]{5,}", _fold(text))
    candidates = [_fold(word) for word in expected]
    return any(
        SequenceMatcher(None, word, candidate).ratio() >= cutoff
        for word in words
        for candidate in candidates
    )


def _requests_editorial_reformulation(text: str) -> bool:
    """Reconnaît les formulations naturelles d'une amélioration de forme."""

    folded = _fold(text)
    return bool(
        _has(
            folded,
            r"\b(?:formul|redig|reecri|reformul|tournur|syntaxe|gramma|orthograph)\w*\b",
            r"\bprofessionnalis\w*\b",
            r"\b(?:ameliore?|ameliorer|corrige?|corriger|revois|reprends?)\w*\b\s+"
            r"(?:(?:simplement|uniquement|seulement|juste|mieux|la|le|ce|cette|du|de)\s+){0,4}"
            r"(?:texte|passage|paragraphe|formulation|redaction)\b",
            r"\b(?:mieux|plus clairement)\b[^.!?;]{0,35}\b(?:ecrit|redige|formule)\b",
        )
        or _has_near_word(
            folded,
            "formulation",
            "redaction",
            "reformuler",
            "reecrire",
            "professionnaliser",
        )
    )


def _research_is_forbidden(clause: str) -> bool:
    """Détecte une interdiction locale sans transformer toute la phrase en règle métier."""

    return _has(
        clause,
        r"\bsans\b[^.!?;]{0,70}\b(?:recherche|rechercher|chercher|source|publication)s?\b",
        r"\bpas\s+de\b[^.!?;]{0,50}\b(?:recherche|source|publication)s?\b",
        r"\baucune?\b[^.!?;]{0,40}\b(?:nouvelle?\s+)?(?:recherche|source|publication)s?\b",
        r"\b(?:ne|n['’])\s*[^.!?;]{0,80}\b(?:pas|plus|aucune?)\b[^.!?;]{0,50}"
        r"\b(?:recherche|rechercher|chercher|source|publication)s?\b",
        r"\b(?:ne|n['’])\s*[^.!?;]{0,50}\b(?:recherche|rechercher|chercher)\b"
        r"[^.!?;]{0,50}\b(?:pas|plus|aucune?)\b",
    )


def _requests_new_research(text: str) -> bool:
    """Reconnaît une action de recherche, en respectant la négation par proposition."""

    clauses = re.split(
        r"[.!?;\n]+|\bmais\b|\bcependant\b|\ben revanche\b",
        str(text or ""),
        flags=re.I,
    )
    # ``trouver`` et ``chercher`` sont ambigus en français. Par exemple,
    # « je trouve le texte trop descriptif » exprime un jugement éditorial et
    # ne doit surtout pas être interprété comme « trouve des sources ».
    # On exige donc un objet explicitement lié à la recherche documentaire
    # pour ces verbes génériques. Le verbe ``rechercher`` et les formulations
    # « lancer/effectuer une recherche » restent, eux, des demandes explicites.
    research_objects = (
        r"(?:articles?|publications?|sources?|références?|citations?|"
        r"travaux(?:\s+scientifiques?)?|littérature|bibliographie|"
        r"preuves?\s+externes?)"
    )
    research_objects_folded = (
        r"(?:articles?|publications?|sources?|references?|citations?|"
        r"travaux(?:\s+scientifiques?)?|litterature|bibliographie|"
        r"preuves?\s+externes?)"
    )
    action_patterns = (
        r"\b(?:recherch(?:e|er|ez|ons|ant))\b",
        rf"\b(?:cherch(?:e|er|ez|ons|ant)|trouv(?:e|er|ez|ons|ant))\b"
        rf"[^.!?;]{{0,90}}\b{research_objects_folded}\b",
        r"\b(?:lance|lancer|lancez|effectue|effectuer|effectuez|fais|faire|faites|"
        r"commence|commencer|relance|relancer)\b[^.!?;]{0,45}\brecherche\b",
    )
    return any(
        _has(_fold(clause), *action_patterns) and not _research_is_forbidden(clause)
        for clause in clauses
        if clause.strip()
    )


def _clauses(text: str) -> list[str]:
    return [
        clause.strip()
        for clause in re.split(
            r"[.!?;\n]+|\bmais\b|\bcependant\b|\ben revanche\b",
            str(text or ""),
            flags=re.I,
        )
        if clause.strip()
    ]


def _scholar_is_forbidden(text: str) -> bool:
    return _has(
        text,
        r"\bsans\b[^.!?;]{0,50}\bennoscholar\b",
        r"\b(?:ne|n['’])\s*[^.!?;]{0,60}\butilis(?:e|er|ez|ons)\b[^.!?;]{0,40}\b(?:pas|plus|jamais)\b[^.!?;]{0,30}\bennoscholar\b",
        r"\b(?:ne|n['’])\s*[^.!?;]{0,40}\bennoscholar\b[^.!?;]{0,40}\b(?:pas|plus|jamais)\b",
        r"\b(?:pas|aucun|aucune)\b[^.!?;]{0,40}\bennoscholar\b",
    )


def _argumentation_is_negated(clause: str) -> bool:
    """Ne confond jamais une interdiction d'ajout avec une demande d'argumentation.

    Exemple critique : « N'ajoute aucun nouvel argument scientifique » contient
    le mot ``argument`` mais signifie exactement l'inverse d'un enrichissement.
    """

    return _has(
        clause,
        r"\b(?:aucun|aucune|pas de|sans)\b[^.!?;]{0,55}\b(?:nouvel(?:le)?s?\s+)?arguments?\b",
        r"\b(?:ne|n['’])\s*[^.!?;]{0,45}\bajout\w*\b[^.!?;]{0,45}\barguments?\b",
        r"\b(?:ne|n['’])\s*[^.!?;]{0,45}\brenforc\w*\b[^.!?;]{0,45}\barguments?\b",
    )


def _requests_argumentation(text: str) -> bool:
    """Détecte seulement une demande POSITIVE d'argumentation/R&D."""

    for clause in _clauses(text):
        if _argumentation_is_negated(clause):
            continue
        folded = _fold(clause)
        if _has(
            clause,
            r"\b(argument|démontr|justifi|défend|convainc|verrou|problémati|difficulté|obstacle)",
            r"\b(non[ -]?trivial|travaux\s+(?:r&d|réalisés)|apport\s+r&d)",
        ) or _has(
            folded,
            r"\b(?:explique?|expliciter|montre?|montrer)\w*\b[^.!?;]{0,70}"
            r"\b(?:pourquoi|necessair|besoin|travaux|demarche)\w*\b",
            r"\b(?:rend|rendre|donne|donner|apporte|apporter|renforce|renforcer)\w*\b"
            r"[^.!?;]{0,70}\b(?:solide|defendable|convaincant|valeur)\w*\b"
            r"[^.!?;]{0,70}\b(?:cir|r&d|rd)\b",
            r"\b(?:cir|r&d|rd)\b[^.!?;]{0,70}"
            r"\b(?:solide|defendable|convaincant|valeur)\w*\b",
        ):
            return True
        if _has_near_word(
            clause,
            "argumentation",
            "argumenter",
            "justification",
            "justifier",
        ):
            return True
    return False


def _forbids_new_factual_content(text: str) -> bool:
    """Repère un contrat explicite de réécriture à faits constants."""

    folded = _fold(text)
    return _has(
        text,
        r"\bà\s+faits?\s+constants?\b",
        r"\b(?:ne|n['’])\s*[^.!?;]{0,50}\bajout\w*\b[^.!?;]{0,70}\b(?:fait|information|argument|méthode|résultat|expérimentation|source)s?\b",
        r"\b(?:aucun|aucune)\b[^.!?;]{0,45}\b(?:nouvel(?:le)?s?\s+)?(?:fait|information|argument|méthode|résultat|expérimentation|source)s?\b",
        r"\bsans\b[^.!?;]{0,55}\b(?:ajouter|introduire)\b[^.!?;]{0,55}\b(?:fait|information|argument|méthode|résultat|expérimentation|source)s?\b",
        r"\bconserv\w*\s+strictement\b[^.!?;]{0,80}\b(?:information|fait|contenu|donnée|élément)s?\b",
        r"\buniquement\b[^.!?;]{0,60}\b(?:rédaction|style|structure|reformulation|version rédactionnelle)\b",
    ) or _has(
        folded,
        r"\b(?:sans|ne)\b[^.!?;]{0,35}\b(?:rien\s+)?invent\w*\b",
        r"\bsans\b[^.!?;]{0,45}\b(?:changer|modifier|alterer|toucher)\w*\b"
        r"[^.!?;]{0,45}\b(?:fond|contenu|faits?|informations?)\b",
        r"\b(?:ne|n')\s*[^.!?;]{0,35}\b(?:change|modifie|altere)\w*\b"
        r"[^.!?;]{0,45}\b(?:pas\s+)?(?:fond|contenu|faits?|informations?)\b",
        r"\b(?:garde|gardez|conserve|conservez)\w*\b[^.!?;]{0,55}"
        r"\b(?:memes?\s+)?(?:faits?|informations?|contenu|fond)\b",
        r"\b(?:contenu|fond|faits?)\s+(?:strictement\s+)?constants?\b",
    )


def _requires_project_evidence_only(text: str) -> bool:
    """Reconnaît une contrainte métier locale sans exiger le nom d'un agent.

    Ces formulations signifient que le corpus autorisé est déjà dans le
    dossier. Elles interdisent donc une recherche ou une source externe, même
    si le consultant ne connaît pas l'architecture interne d'EnnoSmart.
    """

    return _has(
        text,
        r"\b(?:uniquement|seulement|exclusivement)\b[^.!?;]{0,100}"
        r"\b(?:projet|dossier|documents? (?:du|de ce) projet)\b",
        r"\b(?:à partir|sur la base)\b[^.!?;]{0,80}"
        r"\b(?:preuves?|informations?|éléments?|sources?)\b[^.!?;]{0,80}"
        r"\b(?:déjà |réellement )?(?:disponibles?|présents?|validés?)\b[^.!?;]{0,50}"
        r"\b(?:projet|dossier)\b",
        r"\b(?:preuves?|informations?|éléments?|sources?)\b[^.!?;]{0,70}"
        r"\b(?:déjà |réellement )?(?:disponibles?|présents?|validés?)\b[^.!?;]{0,70}"
        r"\b(?:dans|du|de ce) (?:projet|dossier)\b",
        r"\b(?:utilis|appui|base|fond)\w*\b[^.!?;]{0,60}"
        r"\b(?:preuves?|informations?|éléments?|sources?|documents?)\b[^.!?;]{0,60}"
        r"\b(?:dans|du|de ce) (?:projet|dossier)\b",
        r"\b(?:corpus|preuves?|sources?)\b[^.!?;]{0,50}\b(?:interne|du projet|du dossier)\b"
        r"[^.!?;]{0,50}\b(?:uniquement|seulement|exclusivement)\b",
        r"\b(?:uniquement|seulement|exclusivement)\b[^.!?;]{0,80}"
        r"\b(?:à partir|sur la base)\b[^.!?;]{0,45}\b(?:du|de ce)\s+(?:projet|dossier)\b",
        r"\b(?:à partir|sur la base)\b[^.!?;]{0,100}\b(?:du|de ce)\s+(?:projet|dossier)\b"
        r"[^.!?;]{0,45}\b(?:uniquement|seulement|exclusivement)\b",
        r"\b(?:à partir|sur la base)\s+(?:du|de ce)\s+(?:projet|dossier)\b",
    )




def _is_candidate_revision(text: str) -> bool:
    """Détecte une correction de la proposition courante, pas une première amélioration.

    On exige un référent de version/proposition ou une consigne de conservation
    très explicite afin de ne pas confondre « corrige cette section » avec un
    suivi de candidate.
    """

    value = str(text or "")
    has_version_referent = _has(
        value,
        r"\b(?:la|ta|cette)\s+proposition\b",
        r"\b(?:version|sortie|réponse|candidate)\s+(?:actuelle|courante|précédente|proposée|générée)\b",
        r"\b(?:sortie|réponse)\s+précédente\b",
    )
    has_edit_action = _has(
        value,
        r"\b(?:corrig|reformul|révis|modifi|ajust|supprim|retir|enlèv|conserv|gard)\w*\b",
    )
    strong_followup = _has(
        value,
        r"\b(?:sans|ne)\s+repartir\s+de\s+zéro\b",
        r"\b(?:garde|gardez|conserve|conservez)\b[^.!?;]{0,45}\ble\s+reste\b",
        r"\bne\s+(?:change|modifie)\w*\s+que\b",
    )
    return bool((has_version_referent and has_edit_action) or strong_followup)


def _revision_requests_evidence_enrichment(text: str) -> bool:
    """Autorise des preuves dans une révision seulement sur demande positive nette.

    Des phrases comme « n'ajoute rien absent du texte source » ou
    « les informations ajoutées ne sont pas justifiées par la source » ne sont
    jamais interprétées comme une demande d'EnnoScholar.
    """

    for clause in _clauses(text):
        if _research_is_forbidden(clause) or _scholar_is_forbidden(clause):
            continue
        if _has(
            clause,
            r"\b(?:utilis|intégr|ajout|appui|étay|enrich|complèt)\w*\b[^.!?;]{0,70}"
            r"\b(?:articles?|publications?|références?|citations?|preuves?\s+(?:scientifiques?|du\s+projet)|sources?\s+(?:scientifiques?|validées?|du\s+projet))\b",
            r"\b(?:avec|à partir de|sur la base de)\b[^.!?;]{0,55}"
            r"\b(?:articles?|publications?|références?|citations?|preuves?\s+(?:scientifiques?|du\s+projet)|sources?\s+(?:scientifiques?|validées?|du\s+projet))\b",
            r"\b(?:ennoscholar|ennodiagnostic)\b",
        ):
            return True
    return False




def _revision_requests_project_evidence(text: str) -> bool:
    """Détecte une demande positive d'utiliser les preuves internes du projet."""

    for clause in _clauses(text):
        if _research_is_forbidden(clause):
            # « sans nouvelle recherche, utilise les preuves du projet » reste
            # une demande positive de preuves internes ; la négation ne vise
            # que la recherche externe.
            pass
        if _has(
            clause,
            r"\b(?:utilis|appui|étay|base|fond|avec)\w*\b[^.!?;]{0,80}"
            r"\b(?:preuves?|informations?|éléments?|sources?|documents?)\b[^.!?;]{0,60}"
            r"\b(?:du|dans|de ce)\s+(?:projet|dossier)\b",
            r"\b(?:preuves?|informations?|éléments?|sources?|documents?)\b[^.!?;]{0,60}"
            r"\b(?:du|dans|de ce)\s+(?:projet|dossier)\b[^.!?;]{0,50}"
            r"\b(?:utilis|appui|étay)\w*\b",
        ):
            return True
    return False


def _requests_scientific_enrichment(text: str, *, section_kept_sources: bool = False) -> bool:
    """Détecte une utilisation Scholar demandée, sans réagir aux contraintes.

    Le simple mot « source » dans « n'invente rien absent des sources » n'est
    pas une demande d'article. Une action positive ou une fonction de revue
    scientifique doit être présente dans la même proposition.
    """

    for clause in _clauses(text):
        if _scholar_is_forbidden(clause) or _requires_project_evidence_only(clause):
            continue
        forbidden = _research_is_forbidden(clause)
        uses_existing = _has(
            clause,
            r"\b(?:avec|depuis|à partir de|uniquement)\b[^.!?;]{0,70}\b(?:articles?|publications?|sources?|citations?|article cards?)\b[^.!?;]{0,40}\b(?:valid|sélection|disponib|existant)",
            r"\b(?:utilis|intégr|appui|étay)\w*\b[^.!?;]{0,70}\b(?:articles?|publications?|sources?|citations?)\b",
        )
        if section_kept_sources:
            uses_existing = uses_existing or _has(
                clause,
                r"\b(?:avec|depuis|à partir de|uniquement)\b[^.!?;]{0,70}\b(?:articles?|publications?|sources?|citations?)\b[^.!?;]{0,40}\b(?:gard|reten|accept)",
            )
        if forbidden and not uses_existing:
            continue
        folded = _fold(clause)
        if uses_existing or _has(
            clause,
            r"\b(?:ajout|intégr|utilis|cherch|trouv|enrich|appui|étay)\w*\b[^.!?;]{0,70}\b(?:articles?|publications?|sources?|citations?|références?|travaux proches)\b",
            r"\b(?:état de l.art|revue (?:de la )?littérature|revue bibliograph|bibliographie)\b",
            r"\b(?:comparaison|positionnement)\b[^.!?;]{0,60}\b(?:existant|antérieur|littérature)\b",
            r"\b(?:limites?|travaux)\b[^.!?;]{0,50}\b(?:scientifiques?|proches?)\b",
            r"\b(?:ajout|renforc|complèt|développ|approfond)\w*\b[^.!?;]{0,70}"
            r"\b(?:arguments?|justifications?|explications?)\b[^.!?;]{0,60}"
            r"\b(?:scientifiques?|techniques?|documentés?|étayés?)\b",
            r"\b(?:manque|insuffisan|pas assez)\w*\b[^.!?;]{0,50}"
            r"\b(?:arguments?|justifications?|explications?)\b[^.!?;]{0,60}"
            r"\b(?:scientifiques?|techniques?|documentés?|étayés?)\b",
            r"\b(?:ajout|apport|propos|trouv|développ|renforc|complèt)\w*\b"
            r"[^.!?;]{0,55}\b(?:plus\s+d['’e]?|de\s+nouveaux?|d'autres?|des?)\s+arguments?\b",
            r"\b(?:manque|insuffisan|pas assez)\w*\b[^.!?;]{0,45}\barguments?\b",
        ) or _has(
            folded,
            r"\b(?:rend|rendre|renforce|renforcer|etaye|etayer|appuie|appuyer)\w*\b"
            r"[^.!?;]{0,100}\b(?:articles?|publications?|sources?|references?)\b",
            r"\b(?:avec|via|selon|sur la base de|a partir de)\b[^.!?;]{0,80}"
            r"\b(?:articles?|publications?|sources?|references?)\b"
            r"[^.!?;]{0,50}\b(?:scientifiques?|techniques?|validees?|existantes?)\b",
        ):
            return True
    return False


def understand_instruction(instruction: str, default_scope: TargetScope) -> RoutingDecision:
    """Compréhension générique de la demande et routage minimal nécessaire.

    Les règles portent sur l'action rédactionnelle, jamais sur des titres de
    sections, des domaines techniques ou un plan CIR prédéfini.
    """

    text = " ".join(str(instruction or "").split())
    normalized = text.casefold()
    intents: list[ImprovementIntent] = []
    rationale: list[str] = []
    editorial_reformulation = _requests_editorial_reformulation(normalized)
    candidate_revision = _is_candidate_revision(normalized)
    revision_requests_project_evidence = bool(
        candidate_revision and _revision_requests_project_evidence(normalized)
    )
    revision_allows_evidence_enrichment = bool(
        candidate_revision
        and (
            _revision_requests_evidence_enrichment(normalized)
            or revision_requests_project_evidence
        )
    )
    if candidate_revision:
        intents.append(ImprovementIntent.CANDIDATE_REVISION)
        rationale.append(
            "Le consultant corrige la proposition courante : le périmètre de la révision doit rester strictement ciblé."
        )

    if _has(normalized, r"^(bonjour|salut|hello|hi|bonsoir)\b", r"comment (ça|ca) va") and len(normalized.split()) < 12:
        return RoutingDecision(intents=[ImprovementIntent.SMALL_TALK], target_scope=default_scope)
    if editorial_reformulation or _has(
        normalized, r"\b(clair|clarif|lisib|compréhens|reformul)"
    ):
        intents.append(ImprovementIntent.CLARITY)
    if editorial_reformulation or _has(
        normalized, r"\b(style|fluid|professionnel|consultant|ton|rédaction)"
    ):
        intents.append(ImprovementIntent.STYLE)
    if editorial_reformulation:
        rationale.append(
            "La demande porte sur la formulation : appliquer le style CIR/R&D à faits constants."
        )
    if _has(normalized, r"\b(structur|organis|enchaînement|plan|transition)"):
        intents.append(ImprovementIntent.STRUCTURE)
    # Ne pas confondre une donnée/approche « synthétique » avec une demande
    # de synthèse rédactionnelle (ex. « données SAR synthétiques »).
    if _has(
        normalized,
        r"\b(?:raccourc\w*|concis\w*|all[eè]g\w*|résum\w*|synthétis\w*)\b",
        r"\b(?:plus|davantage)\s+synthétique\b",
        r"\brends?\b[^.!?;]{0,45}\b(?:texte|passage|section|contenu)\b"
        r"[^.!?;]{0,35}\bsynthétique\b",
    ):
        intents.append(ImprovementIntent.CONCISION)
    positive_argumentation = _requests_argumentation(normalized)
    if positive_argumentation:
        intents.append(ImprovementIntent.ARGUMENTATION)
    if _has(
        normalized,
        r"\b(?:analys|évalu|vérifi|qualifi|démontr|justifi|défend|renforc)\w*\b"
        r"[^.!?;]{0,80}\b(?:cir|frascati|éligib|incertitude scientifique|"
        r"avance(?:e|é)e de connaissance)\b",
        r"\b(?:est-ce|s'agit-il)\b[^.!?;]{0,80}\b(?:éligible|verrou|incertitude)\b",
    ):
        intents.append(ImprovementIntent.CIR_ELIGIBILITY)
        rationale.append("La demande porte sur la solidité du raisonnement R&D/CIR.")
    project_evidence_only = _requires_project_evidence_only(normalized)
    asks_new_research = _requests_new_research(normalized)
    if candidate_revision and asks_new_research:
        revision_allows_evidence_enrichment = True
    explicit_scientific_enrichment = _requests_scientific_enrichment(
        normalized, section_kept_sources=default_scope == TargetScope.SECTION,
    )
    strict_fact_preservation = _forbids_new_factual_content(normalized)

    # Une correction de candidate est fermée par défaut : EnnoAmel corrige ce
    # qui existe déjà au lieu d'enrichir spontanément avec Diagnostic/Scholar.
    # Seule une demande positive explicite du consultant rouvre ce périmètre.
    if candidate_revision and not revision_allows_evidence_enrichment and not asks_new_research:
        explicit_scientific_enrichment = False

    forbids_scholar = bool(
        _scholar_is_forbidden(normalized)
        or project_evidence_only
        or (
            candidate_revision
            and not revision_allows_evidence_enrichment
            and not asks_new_research
        )
    )
    if explicit_scientific_enrichment and not forbids_scholar:
        intents.append(ImprovementIntent.SCIENTIFIC_ENRICHMENT)
        rationale.append("La demande nécessite de relier la rédaction aux preuves scientifiques disponibles.")

    forbids_new_research = bool(
        not asks_new_research
        and (
            project_evidence_only
            or candidate_revision
            or any(_research_is_forbidden(clause) for clause in _clauses(normalized))
        )
    )
    if asks_new_research:
        intents.append(ImprovementIntent.RESEARCH)
        rationale.append("Le consultant demande explicitement une nouvelle recherche.")
    elif forbids_new_research:
        rationale.append(
            "Correction de candidate : aucune nouvelle recherche n'est lancée."
            if candidate_revision
            else "Le consultant interdit une nouvelle recherche ; aucune recherche externe ne sera lancée."
        )
    if project_evidence_only:
        rationale.append(
            "Le consultant limite le corpus aux preuves déjà présentes dans le projet."
        )
    elif forbids_scholar:
        rationale.append("Le consultant exclut toute mobilisation de références externes.")

    scope = default_scope
    if not candidate_revision:
        if _has(normalized, r"\b(document complet|dossier complet|cir complet|tout le document)"):
            scope = TargetScope.FULL_DOCUMENT
        elif _has(normalized, r"\b(plusieurs sections|ces sections)"):
            scope = TargetScope.MULTI_SECTION
        elif _has(normalized, r"\b(paragraphe|passage|sélection|texte sélectionné)"):
            scope = TargetScope.SELECTION
        elif _has(normalized, r"\b(section|chapitre|partie)"):
            scope = TargetScope.SECTION

    if not intents:
        intents = [ImprovementIntent.GENERAL_REVISION]

    # « Qualité de l'argumentation » peut viser la présentation des faits
    # existants. Dans une réécriture locale à faits constants, ce mot seul ne
    # justifie pas de diagnostic. Les demandes explicites de preuves restent
    # dans leur parcours scientifique, comme le traitement du CIR complet.
    if (
        default_scope in {TargetScope.SECTION, TargetScope.SELECTION, TargetScope.PARAGRAPH}
        and editorial_reformulation
        and strict_fact_preservation
        and not project_evidence_only
        and not _revision_requests_project_evidence(normalized)
        and not explicit_scientific_enrichment
        and not asks_new_research
        and ImprovementIntent.CIR_ELIGIBILITY not in intents
    ):
        intents = [intent for intent in intents if intent != ImprovementIntent.ARGUMENTATION]

    intent_set = set(intents)
    editorial_only = bool(
        intent_set
        & {
            ImprovementIntent.CLARITY,
            ImprovementIntent.STYLE,
            ImprovementIntent.STRUCTURE,
            ImprovementIntent.CONCISION,
        }
    ) and not bool(
        intent_set
        & {
            ImprovementIntent.ARGUMENTATION,
            ImprovementIntent.CIR_ELIGIBILITY,
            ImprovementIntent.SCIENTIFIC_ENRICHMENT,
            ImprovementIntent.RESEARCH,
        }
    )

    # Une demande d'argumentation R&D doit être ancrée dans les preuves du
    # projet. Il ne s'agit pas d'un cas propre à une section ou à un client.
    needs_diagnostic = any(
        intent in intents
        for intent in (ImprovementIntent.ARGUMENTATION, ImprovementIntent.CIR_ELIGIBILITY)
    )
    needs_scholar = any(
        intent in intents
        for intent in (ImprovementIntent.SCIENTIFIC_ENRICHMENT, ImprovementIntent.RESEARCH)
    )
    if editorial_only:
        # V2.7 : garde-fou métier prioritaire. Une demande purement éditoriale
        # reste Writer-only, même si des mots R&D apparaissent dans des
        # contraintes négatives comme « aucun nouvel argument scientifique ».
        needs_diagnostic = False
        needs_scholar = False
        rationale.append(
            "Mode éditorial strict : réécriture à partir du texte cible, sans EnnoDiagnostic ni EnnoScholar."
        )
    if candidate_revision and revision_requests_project_evidence:
        needs_diagnostic = True
    if candidate_revision and not revision_allows_evidence_enrichment and not asks_new_research:
        needs_diagnostic = False
        needs_scholar = False
        rationale.append(
            "Mode correction de candidate : aucun enrichissement Diagnostic/Scholar n'est ajouté sans demande explicite."
        )
    return RoutingDecision(
        intents=list(dict.fromkeys(intents)),
        target_scope=scope,
        needs_diagnostic=needs_diagnostic,
        needs_scholar=needs_scholar,
        needs_new_research=ImprovementIntent.RESEARCH in intents,
        forbids_new_research=forbids_new_research,
        forbids_scholar=forbids_scholar,
        needs_project_evidence=bool(needs_diagnostic),
        rationale=rationale,
        candidate_revision=candidate_revision,
        revision_allows_evidence_enrichment=revision_allows_evidence_enrichment,
        editorial_only=editorial_only,
        strict_fact_preservation=strict_fact_preservation,
    )
