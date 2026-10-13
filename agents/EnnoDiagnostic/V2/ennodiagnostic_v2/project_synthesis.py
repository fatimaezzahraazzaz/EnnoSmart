from __future__ import annotations



import re

from dataclasses import asdict, dataclass, field

from typing import Dict, Iterable, List, Optional, Tuple



from .schemas import CanonicalEntity, ExtractionItem, TraceablePoint
from .structured_facts import build_project_fact_lists

from .semantic_extractor import LLMClientProtocol, parse_json_payload





SUPPORT_LEVELS = {

    "strong",

    "partial",

    "weak",

    "unknown",

}





@dataclass

class ProjectSynthesis:

    """

    Synthèse descriptive du projet + deux signaux structurés servant ensuite

    au calcul déterministe de l'éligibilité.



    IMPORTANT :

    - aucun score n'est produit ici ;

    - aucune décision CIR n'est prise ici ;

    - la littérature ne devient jamais une preuve projet.

    """



    project_summary: str = ""

    main_objective: str = ""

    objective_points: List[str] = field(default_factory=list)

    technical_context: str = ""

    technical_ambition: str = ""

    existing_solutions_gap: str = ""

    knowledge_gap: str = ""

    methods_summary: str = ""

    results_summary: str = ""



    # Version courte, consolid?e et tra?able destin?e au frontend.

    methods: List[TraceablePoint] = field(default_factory=list)

    # Démarches extraites pendant la phase sémantique, puis consolidées
    # ici sans dépendre de rnd_approach.py.
    demarches: List[TraceablePoint] = field(default_factory=list)

    parameters: List[TraceablePoint] = field(default_factory=list)
    metrics: List[TraceablePoint] = field(default_factory=list)

    results: List[TraceablePoint] = field(default_factory=list)



    constraints_summary: str = ""

    main_locks_summary: str = ""

    state_of_art_context: str = ""



    # Signaux qualitatifs pour le score d'éligibilité.

    # Le LLM classe ; Python calcule ensuite les points.

    rnd_relevance_status: str = "unknown"

    rnd_relevance_reason: str = ""

    existing_solutions_gap_status: str = "unknown"

    existing_solutions_gap_reason: str = ""



    evidence_ids: List[str] = field(default_factory=list)

    missing_information: List[str] = field(default_factory=list)



    project_evidence_count: int = 0

    literature_evidence_count: int = 0

    section_count: int = 0



    needs_review: bool = False

    generation_error: str = ""



    def to_dict(self):

        return asdict(self)





PROJECT_SYNTHESIS_SYSTEM = r"""

Tu rédiges la SYNTHÈSE GLOBALE d'un projet technique analysé par EnnoDiagnostic.



OBJECTIF

Produire une vue courte, claire, fidèle et exploitable du projet à partir

des éléments déjà extraits des documents.



IMPORTANT

Tu ne dois PAS :

- décider si le projet est éligible au CIR ;

- attribuer un score ou un pourcentage ;

- affirmer qu'un sujet est scientifiquement nouveau dans le monde ;

- inventer une difficulté, un résultat, une méthode ou une connaissance ;

- transformer une information de littérature en résultat du projet.



============================================================

SOURCES

============================================================



Les informations sont séparées en deux catégories :



1. ELEMENTS DU PROJET

Informations directement rattachées aux travaux analysés :

objectifs, verrous, méthodes, démarches, paramètres, résultats et contraintes.



2. CONTEXTE LITTERATURE

Informations issues d'articles, publications ou état de l'art.

Elles servent uniquement à expliquer ce que le dossier présente comme

approches existantes ou limites connues.



Le CONTEXTE LITTERATURE ne doit jamais être présenté comme une expérience,

un résultat ou une preuve obtenue par le projet.



============================================================

REGLES DE FIDELITE FACTUELLE

============================================================



La synthese doit decrire le PROJET et non les conclusions produites par

EnnoDiagnostic.



Ne presente jamais comme resultat du projet :

- une qualification de verrou ;

- un statut de qualification ;

- un regroupement ou une reformulation de verrou ;

- un score ou un niveau de risque ;

- une conclusion generee par le diagnostic.



Un resultat du projet doit etre soutenu par un element de type result

provenant des ELEMENTS DU PROJET.



L'inventaire factuel fourni dans l'entree est contraignant :

- si OBJECTIFS > 0, ne dis pas que les objectifs sont absents ;

- si METHODES > 0, ne dis pas que les methodes sont absentes ;

- si DEMARCHES > 0, ne dis pas que la démarche projet est absente ;

- si PARAMETRES > 0, ne dis pas que les parametres sont absents ;

- si RESULTATS > 0, ne dis pas que les resultats sont totalement absents.



Si RESULTATS = 0 :

- n'invente aucun resultat ;

- ne transforme pas un verrou, une methode ou une conclusion du diagnostic

  en resultat ;

- indique clairement qu'aucun resultat experimental ou constat technique

  issu des travaux n'est documente.



Une information peut etre presente mais insuffisamment detaillee.

Dans ce cas, indique qu'elle est incomplete plutot que de dire qu'elle

est absente.



============================================================

CONTENU DESCRIPTIF ATTENDU

============================================================



project_summary

- 4 a 6 phrases maximum.

- Decris uniquement le projet : sujet, objectif, difficultes techniques,

  demarche et resultats reellement documentes.

- Ne presente jamais une qualification, un regroupement ou une reformulation

  de verrou realisee par EnnoDiagnostic comme un resultat du projet.

- Si aucun resultat projet n'est extrait, ne fabrique pas de resultat pour

  completer le resume.

- Doit pouvoir etre affiche directement dans le frontend.



main_objective

- Produis UN SEUL objectif global du projet.

- Consolide les diff?rents ?l?ments de type objective pr?sents dans les ?l?ments du projet.

- L'objectif global doit exprimer la finalit? technique principale commune.

- Ne recopie pas simplement le titre du projet si une formulation plus pr?cise existe.

- Ne transforme jamais un verrou, une contrainte, une m?thode ou un r?sultat en objectif.

- N'invente aucune finalit? absente des preuves.

- 1 ? 2 phrases maximum.



objective_points

- Liste les sous-objectifs techniques qui pr?cisent directement l'objectif global.

- Utilise uniquement des objectifs r?ellement soutenus par les preuves du projet.

- Fusionne les formulations redondantes.

- Chaque point doit apporter une information distincte.

- Ne mets pas ici des verrous, contraintes, m?thodes ou r?sultats.

- Ne r?p?te pas simplement main_objective.

- 2 ? 6 points maximum.

- Si aucun sous-objectif distinct n'est suffisamment soutenu, retourne [].



technical_context

- Contexte technique dans lequel le projet s'inscrit.

- 1 à 2 phrases.



technical_ambition

- Ce que le projet cherche techniquement à rendre possible,

  comprendre, améliorer ou maîtriser.

- Ne parle pas de "nouveauté mondiale".

- 1 à 2 phrases.



existing_solutions_gap

- Ce que le DOSSIER indique comme insuffisant, limité ou inadapté

  dans les approches/solutions disponibles.

- Si cela provient uniquement de la littérature, formule-le comme tel.

- 1 à 3 phrases.



knowledge_gap

- Question technique ou connaissance qui reste à établir dans le projet.

- Si le dossier ne permet pas de l'identifier clairement, dis-le.

- 1 à 2 phrases.



methods_summary

- Résume uniquement les METHODES scientifiques ou techniques du projet.

- Une METHOD peut être retenue si la preuve décrit :
  1. une méthode/approche technique identifiable explicitement liée au projet ;
  OU
  2. un mécanisme technique concret expliquant le principe de fonctionnement.

- N'exige PAS que tous les paramètres, étapes internes ou détails
  d'implémentation soient présents.

- Exemple à conserver :
  "IDM : génération d'un modèle cible à partir d'un modèle source
   via des règles de transformation."
  Cette description est suffisamment technique pour constituer une méthode.

- Autres exemples à conserver :
  fine-tuning, LoRA, RAG avec recherche vectorielle, PCA, SVM, XGBoost,
  Isolation Forest, validation croisée, Optuna, règles de transformation,
  protocole expérimental ou méthode de mesure définie.

- En revanche, une simple étape de DEMARCHE n'est pas une METHOD :
  état de l'art, étude d'approches, collecte, nettoyage, entraînement,
  réglage, développement, implémentation, expérimentation, comparaison,
  test ou validation, lorsqu'aucune approche ou mécanisme technique n'est donné.

- Une technologie ou un domaine seul (IA, Python, cloud, machine learning...)
  n'est pas une méthode.

- TYPE=method est un candidat : revérifie la preuve, mais ne rejette pas une
  vraie méthode simplement parce qu'elle n'est pas décrite jusque dans tous
  ses détails.

- Préserve la temporalité : prévu != réalisé.

- Si aucune méthode réelle ne subsiste, indique-le sobrement.

- 1 à 3 phrases.

demarches

- Ce champ est une LISTE destinée au frontend.
- Il constitue un SECOND CONTROLE SEMANTIQUE des ExtractionItem TYPE=demarche.
- Tu ne dois utiliser QUE des éléments TYPE=demarche provenant des ELEMENTS DU PROJET.
- Chaque point doit contenir :
  * "text" : l'étape technique/scientifique, formulée clairement ;
  * "evidence_ids" : une ou plusieurs preuves TYPE=demarche correspondantes.

TEST OBLIGATOIRE :

Conserve une démarche seulement si elle satisfait AU MOINS UNE condition :

1. l'action agit directement sur l'objet technique/scientifique, les données,
   le modèle, l'algorithme, le prototype, l'architecture, l'expérience ou
   la solution étudiée ;

OU

2. l'action produit directement une connaissance technique, une comparaison,
   une observation expérimentale ou une validation technique.

EXCLURE même si TYPE=demarche dans l'extraction initiale :
- procédure d'identification/sélection/validation des projets R&D ;
- suivi CIR ou suivi d'éligibilité des travaux/documents ;
- définition ou validation administrative des sujets ;
- appels à projets ;
- comité R&D / comité d'éligibilité / décision collégiale ;
- recrutement, stage, RH, affectation de ressources ;
- budget, gouvernance, reporting, pilotage ;
- rédaction ou validation documentaire ;
- titre du projet, nom d'un outil, objectif ou finalité ;
- toute étape organisationnelle qui ne réalise pas le travail technique.

IMPORTANT :
TYPE=demarche est un CANDIDAT issu de l'extraction initiale.
Tu dois le revérifier sémantiquement avant de le retourner.

Exemples :
- "État de l'art technique sur les techniques IA applicables au projet"
  => garder.
- "Étude des différentes approches IA"
  => garder si cette étude appartient aux travaux techniques du projet.
- "Proposition d'une solution IA"
  => garder.
- "Implémentation de la solution"
  => garder.
- "Test et validation technique de la solution"
  => garder.
- "Procédure d'identification et de validation des projets R&D"
  => exclure.
- "Suivi continu de l'éligibilité des travaux"
  => exclure.
- "Ouverture des appels à projets"
  => exclure.
- "Recrutement d'un stagiaire"
  => exclure.

ORDRE :
- conserve l'ordre de la séquence technique lorsque le dossier l'explicite ;
- ne réordonne pas arbitrairement les étapes ;
- ne fusionne que les doublons évidents ;
- garde séparées les étapes techniquement distinctes.

TEMPORALITE :
- ne change jamais la temporalité portée par les extractions sources ;
- planned != in_progress != performed ;
- une démarche prévue ne devient jamais réalisée.

N'invente jamais une démarche ou un EVIDENCE_ID.
Si aucune démarche technique/scientifique ne subsiste, retourne [].

results_summary

- Utilise uniquement les elements de type result provenant des ELEMENTS DU PROJET.

- Resume uniquement les resultats ou constats reellement documentes.

- Preserve les valeurs quantitatives disponibles.

- Ne transforme jamais un objectif, une methode, une contrainte, un verrou

  ou une qualification EnnoDiagnostic en resultat.

- Si RESULTATS = 0 dans l'inventaire factuel, indique explicitement

  qu'aucun resultat experimental ou constat technique issu des travaux

  n'est documente dans les sources projet fournies.

- 1 a 3 phrases.





methods / demarches / parameters / metrics / results



Ces cinq champs sont des LISTES destin?es ? l'affichage frontend.



Pour chaque ?l?ment :

- "text" explique l'?l?ment clairement en 1 ? 2 phrases maximum ;

- "evidence_ids" contient un ou plusieurs EVIDENCE_ID des ELEMENTS DU PROJET.



REGLES :



- Ne recopie pas m?caniquement les extractions brutes.

- Consolide et explique leur sens.

- Fusionne uniquement les formulations r?ellement redondantes.

- Garde s?par?s les ?l?ments techniquement distincts.



- Un point de "methods" doit être soutenu par au moins une preuve TYPE=method.

- ATTENTION : TYPE=method est un candidat, pas une validation automatique.

- Garde le point si la preuve décrit au moins l'un des deux cas suivants :
  1. une méthode, méthodologie, technique, modèle, algorithme ou protocole
     identifiable explicitement utilisé/étudié/prévu dans le projet ;
  2. un mécanisme technique concret permettant de comprendre comment
     l'approche fonctionne.

- Une méthode n'a pas besoin d'être décrite avec tous ses paramètres ou tous
  ses détails d'implémentation.

- Exemple important :
  "Ingénierie dirigée par les modèles (IDM) : génération d'un modèle cible
   à partir d'un modèle source via des règles de transformation"
  => GARDER comme METHOD.

- Exemples à garder :
  * fine-tuning d'un LLM ;
  * fine-tuning avec LoRA ;
  * PCA ;
  * SVM / Random Forest / XGBoost utilisés pour une tâche du projet ;
  * RAG avec recherche vectorielle ;
  * Isolation Forest ;
  * Optuna / Random Search / Grid Search ;
  * validation croisée stratifiée ;
  * règles de transformation ;
  * protocole expérimental ou de mesure défini.

- Exclure une simple étape de DEMARCHE lorsqu'elle reste générique :
  * état de l'art ;
  * étude d'approches ;
  * recherche / collecte de données ;
  * nettoyage / préparation ;
  * entraînement ;
  * réglage d'hyperparamètres ;
  * développement ;
  * implémentation ;
  * expérimentation ;
  * comparaison ;
  * test ;
  * validation ;
  * analyse des résultats.

- Exclure également :
  * simple domaine ou technologie générique ;
  * objectif ;
  * verrou ;
  * contrainte ;
  * résultat ;
  * activité administrative ;
  * suivi CIR / éligibilité ;
  * pilotage / reporting ;
  * rédaction documentaire ;
  * validation administrative.

- N'invente jamais les détails absents pour rendre une méthode plus précise.

- Préserve la temporalité : une méthode prévue ne devient jamais réalisée.

- Si aucune méthode réelle ne subsiste, retourne "methods": [].

- Un point de "parameters" doit ?tre soutenu par au moins une preuve TYPE=parameter.

- Un point de "metrics" doit être soutenu par au moins une preuve TYPE=metric.
  Une métrique est ce qui est mesuré ; elle ne devient un résultat que si une
  valeur ou un constat effectivement obtenu est documenté.

- Un point de "results" doit ?tre soutenu par au moins une preuve TYPE=result.



- Pr?serve les valeurs num?riques et unit?s importantes dans les r?sultats.

- N'invente jamais un ?l?ment ou un EVIDENCE_ID.

- Un ?l?ment sans preuve valide ne doit pas ?tre retourn?.

- Si aucun ?l?ment du type demand? n'existe, retourne [].



constraints_summary

- Contraintes importantes du projet : architecture, ressources,

  confidentialité, performances, données, matériel, etc.

- 1 à 3 phrases.



main_locks_summary

- Résume les principaux verrous qualifiés du projet sans les remplacer

  ni supprimer leurs formulations détaillées.

- 1 à 3 phrases.



state_of_art_context

- Résume brièvement les éléments de littérature utiles pour contextualiser

  le projet.

- Si aucune littérature exploitable n'est fournie, retourne une chaîne vide.

- 1 à 2 phrases.



============================================================

DEUX SIGNAUX QUALITATIFS POUR L'ELIGIBILITE

============================================================



Tu ne calcules PAS le score final.

Tu classes uniquement deux dimensions avec :

strong | partial | weak | unknown



1. rnd_relevance_status

Question :

"Le contexte et l'objectif du projet décrivent-ils une vraie ambition

technique de compréhension, exploration ou dépassement d'une limite,

plutôt qu'une simple implémentation fonctionnelle ?"



STRONG :

- objectif technique clair ;

- recherche d'une compréhension/capacité non maîtrisée dans le contexte ;

- exploration, comparaison ou dépassement technique clairement décrit.



PARTIAL :

- dimension technique crédible mais encore générale ou peu explicitée.



WEAK :

- objectif principalement fonctionnel, intégration, migration,

  configuration ou développement routinier.



UNKNOWN :

- informations insuffisantes pour décider.



rnd_relevance_reason

- 1 phrase factuelle expliquant le classement.



2. existing_solutions_gap_status

Question :

"Le dossier explique-t-il concrètement pourquoi les solutions,

méthodes ou connaissances disponibles ne suffisent pas dans le contexte

du projet ?"



STRONG :

- au moins une limite concrète et reliée au projet est expliquée ;

- le dossier montre ce qui reste insuffisant ou non maîtrisé.



PARTIAL :

- des limites sont mentionnées mais leur lien avec le projet

  ou leur impact technique reste incomplet.



WEAK :

- aucune insuffisance technique réelle n'est décrite ;

  le dossier présente surtout une préférence ou un choix de solution.



UNKNOWN :

- informations insuffisantes.



existing_solutions_gap_reason

- 1 phrase factuelle expliquant le classement.



IMPORTANT :

La littérature peut aider à contextualiser une limite existante,

mais le classement STRONG doit rester cohérent avec les besoins,

contraintes ou observations du projet lui-même.

Ne conclus jamais "nouveau dans le monde".



============================================================

TRACABILITE

============================================================



evidence_ids

- Retourne uniquement des EVIDENCE_ID fournis dans l'entrée.

- Sélectionne les preuves les plus structurantes de la synthèse.

- N'invente jamais un identifiant.



missing_information

- Liste courte des informations importantes absentes ou trop faibles

  pour produire une synthèse complètement solide.

- Ne demande pas des informations déjà présentes.



============================================================

SORTIE JSON STRICTE

============================================================



{

  "project_summary": "...",

  "main_objective": "...",

  "objective_points": ["..."],

  "technical_context": "...",

  "technical_ambition": "...",

  "existing_solutions_gap": "...",

  "knowledge_gap": "...",

  "methods_summary": "...",

  "results_summary": "...",



  "methods": [

    {

      "text": "M?thode expliqu?e en 1 ? 2 phrases.",

      "evidence_ids": ["ev_..."]

    }

  ],



  "demarches": [

    {

      "text": "Etape technique/scientifique du projet.",

      "evidence_ids": ["ev_..."]

    }

  ],



  "parameters": [

    {

      "text": "Param?tre expliqu? en 1 ? 2 phrases.",

      "evidence_ids": ["ev_..."]

    }

  ],

  "metrics": [
    {
      "text": "Métrique mesurée ou évaluée.",
      "evidence_ids": ["ev_..."]
    }
  ],



  "results": [

    {

      "text": "R?sultat expliqu? en 1 ? 2 phrases.",

      "evidence_ids": ["ev_..."]

    }

  ],



  "constraints_summary": "...",

  "main_locks_summary": "...",

  "state_of_art_context": "...",



  "rnd_relevance_status": "strong|partial|weak|unknown",

  "rnd_relevance_reason": "...",

  "existing_solutions_gap_status": "strong|partial|weak|unknown",

  "existing_solutions_gap_reason": "...",



  "evidence_ids": ["..."],

  "missing_information": ["..."]

}

""".strip()





def _norm(text: str) -> str:

    return re.sub(r"\s+", " ", str(text or "")).strip().lower()





def _metadata(obj) -> dict:

    value = getattr(obj, "metadata", None)

    return value if isinstance(value, dict) else {}





def _source_role(item: ExtractionItem) -> str:

    role = str(

        _metadata(item).get("source_role") or "unknown"

    ).strip().lower()



    if role not in {

        "project_evidence",

        "literature",

        "contextual",
        "context",
        "admin",

        "unknown",

    }:

        return "unknown"



    return role





def _split_extractions(

    extractions: Iterable[ExtractionItem],

) -> Tuple[List[ExtractionItem], List[ExtractionItem], List[ExtractionItem]]:

    """

    Aucun élément n'est supprimé du pipeline.

    La séparation sert uniquement à empêcher une confusion projet/littérature

    dans la synthèse.

    """

    items = list(extractions)



    provenance_available = any(

        str(

            _metadata(item).get("source_role") or ""

        ).strip()

        for item in items

    )



    if not provenance_available:

        # Compatibilité ancien pipeline.

        return items, [], []



    project_items = []

    literature_items = []

    other_items = []



    for item in items:

        role = _source_role(item)



        if role == "project_evidence":

            project_items.append(item)

        elif role == "literature":

            literature_items.append(item)

        else:

            other_items.append(item)



    return project_items, literature_items, other_items





def _dedupe_items(

    items: Iterable[ExtractionItem],

) -> List[ExtractionItem]:

    """

    Déduplication de présentation uniquement.

    Les extractions sources restent intactes.

    """

    out = []

    seen = set()



    for item in items:

        key = (

            item.kind,

            _norm(item.statement),

            _norm(item.evidence.section_title),

        )



        if key in seen:

            continue



        seen.add(key)

        out.append(item)



    return out





def _project_lock_entities(

    entities: Iterable[CanonicalEntity],

) -> List[CanonicalEntity]:

    locks = [

        entity

        for entity in entities

        if entity.kind == "lock"

    ]



    if not locks:

        return []



    provenance_available = any(

        (

            "has_project_evidence" in _metadata(entity)

            or "source_roles" in _metadata(entity)

        )

        for entity in locks

    )



    if not provenance_available:

        return locks



    return [

        entity

        for entity in locks

        if _metadata(entity).get(

            "has_project_evidence"

        ) is True

    ]





def _item_block(

    item: ExtractionItem,

) -> str:

    statement = str(

        item.statement or ""

    ).strip()



    if len(statement) > 700:

        statement = statement[:700] + " [...]"



    quote = str(

        item.evidence.quote or ""

    ).strip()



    if len(quote) > 320:

        quote = quote[:320] + " [...]"



    section = str(

        item.evidence.section_title

        or "Sans section"

    ).strip()



    return "\n".join(

        [

            f"EVIDENCE_ID: {item.evidence.evidence_id}",

            f"TYPE: {item.kind}",

            f"SECTION: {section}",

            f"STATEMENT: {statement}",

            f"PREUVE: {quote}",

        ]

    )





def _lock_block(

    entity: CanonicalEntity,

) -> str:

    metadata = _metadata(entity)



    status = str(

        metadata.get(

            "qualification_status"

        ) or "NOT_EVALUATED"

    ).strip()



    suggested = str(

        metadata.get(

            "suggested_statement"

        ) or ""

    ).strip()



    reason = str(

        metadata.get(

            "qualification_reason"

        ) or ""

    ).strip()



    evidence_source_roles = (

        metadata.get("evidence_source_roles")

        if isinstance(

            metadata.get("evidence_source_roles"),

            dict,

        )

        else {}

    )



    evidence_lines = []



    for evidence in entity.evidences:

        role = str(

            evidence_source_roles.get(

                evidence.evidence_id

            ) or ""

        ).strip().lower()



        if (

            evidence_source_roles

            and role != "project_evidence"

        ):

            continue



        quote = str(

            evidence.quote or ""

        ).strip()



        if len(quote) > 320:

            quote = quote[:320] + " [...]"



        evidence_lines.append(

            f"- {evidence.evidence_id}: {quote}"

        )



    lines = [

        f"ENTITY_ID: {entity.entity_id}",

        f"STATUT: {status}",

        f"VERROU: {entity.statement}",

    ]



    if suggested:

        lines.append(

            f"REFORMULATION_PROPOSEE: {suggested}"

        )



    if reason:

        lines.append(

            f"RAISON_QUALIFICATION: {reason}"

        )



    if evidence_lines:

        lines.append("PREUVES_PROJET:")

        lines.extend(evidence_lines)



    return "\n".join(lines)





def build_project_synthesis_prompt(

    extractions: Iterable[ExtractionItem],

    entities: Iterable[CanonicalEntity],

) -> Tuple[str, dict]:

    all_items = list(extractions)

    all_entities = list(entities)



    (

        project_items,

        literature_items,

        other_items,

    ) = _split_extractions(

        all_items

    )



    project_items = _dedupe_items(

        project_items

    )

    literature_items = _dedupe_items(

        literature_items

    )

    other_items = _dedupe_items(

        other_items

    )



    project_kind_counts = {}



    for item in project_items:

        kind = str(

            getattr(item, "kind", "") or ""

        ).strip().lower()



        if not kind:

            continue



        project_kind_counts[kind] = (

            project_kind_counts.get(kind, 0) + 1

        )



    project_locks = _project_lock_entities(

        all_entities

    )



    project_sections: Dict[

        str,

        List[ExtractionItem],

    ] = {}



    for item in project_items:

        section = str(

            item.evidence.section_title

            or "Sans section"

        ).strip()



        project_sections.setdefault(

            section,

            [],

        ).append(item)



    project_blocks = []



    for section, items in project_sections.items():

        project_blocks.append(

            (

                f"### SECTION: {section}\n\n"

                + "\n\n".join(

                    _item_block(item)

                    for item in items

                )

            )

        )



    literature_blocks = [

        _item_block(item)

        for item in literature_items

    ]



    other_blocks = [

        _item_block(item)

        for item in other_items

    ]



    lock_blocks = [

        _lock_block(entity)

        for entity in project_locks

    ]



    prompt_parts = [

        "============================================================",

        "INVENTAIRE FACTUEL DES ELEMENTS PROJET",

        "============================================================",

        f"OBJECTIFS: {project_kind_counts.get('objective', 0)}",

        f"METHODES: {project_kind_counts.get('method', 0)}",

        f"DEMARCHES: {project_kind_counts.get('demarche', 0)}",

        f"PARAMETRES: {project_kind_counts.get('parameter', 0)}",

        f"RESULTATS: {project_kind_counts.get('result', 0)}",

        "",

        "Ces nombres correspondent uniquement aux elements projet extraits.",

        "Les conclusions du diagnostic ne doivent pas les remplacer.",

        "",

        "============================================================",

        "ELEMENTS DU PROJET, ORGANISES PAR SECTION",

        "============================================================",

        (

            "\n\n".join(project_blocks)

            if project_blocks

            else "Aucun élément projet explicite."

        ),

        "",

        "============================================================",

        "VERROUS QUALIFIES DU PROJET",

        "============================================================",

        (

            "\n\n--------------------\n\n".join(lock_blocks)

            if lock_blocks

            else "Aucun verrou projet qualifié."

        ),

        "",

        "============================================================",

        "CONTEXTE LITTERATURE - NE PAS PRESENTER COMME RESULTAT DU PROJET",

        "============================================================",

        (

            "\n\n".join(literature_blocks)

            if literature_blocks

            else "Aucun élément littérature."

        ),

        "",

        "============================================================",

        "ELEMENTS CONTEXTUELS / ATTRIBUTION INCERTAINE",

        "============================================================",

        (

            "\n\n".join(other_blocks)

            if other_blocks

            else "Aucun élément contextuel ou incertain."

        ),

    ]



    section_names = {

        str(

            item.evidence.section_title

            or "Sans section"

        ).strip()

        for item in project_items

    }



    stats = {

        "project_evidence_count": len(

            project_items

        ),

        "literature_evidence_count": len(

            literature_items

        ),

        "other_evidence_count": len(

            other_items

        ),

        "section_count": len(

            section_names

        ),

        "project_objective_count": project_kind_counts.get(

            "objective", 0

        ),

        "project_method_count": project_kind_counts.get(

            "method", 0

        ),

        "project_demarche_count": project_kind_counts.get(

            "demarche", 0

        ),

        "project_parameter_count": project_kind_counts.get(

            "parameter", 0

        ),

        "project_result_count": project_kind_counts.get(

            "result", 0

        ),

        "project_lock_count": len(

            project_locks

        ),

    }



    return "\n".join(

        prompt_parts

    ), stats





def _support_status(

    value,

) -> str:

    value = str(

        value or "unknown"

    ).strip().lower()



    if value not in SUPPORT_LEVELS:

        return "unknown"



    return value





class ProjectSynthesizer:

    def __init__(

        self,

        llm: Optional[LLMClientProtocol],
        reviewed_llm: Optional[LLMClientProtocol] = None,

    ):

        self.llm = llm
        self.reviewed_llm = reviewed_llm or llm



    def synthesize(

        self,

        extractions: Iterable[ExtractionItem],

        entities: Iterable[CanonicalEntity],
        *,
        globally_reviewed: bool = False,

    ) -> ProjectSynthesis:



        extractions = list(extractions)

        entities = list(entities)



        if not extractions and not entities:

            return ProjectSynthesis(

                missing_information=[

                    "Aucun élément exploitable du projet n'est disponible."

                ],

                needs_review=True,

            )



        prompt, stats = (

            build_project_synthesis_prompt(

                extractions,

                entities,

            )

        )

        # Les cinq listes sont établies à partir des entités consolidées et de
        # leurs preuves projet avant la rédaction de la synthèse stratégique.
        # Le modèle de synthèse ne peut plus les vider en omettant une clé JSON.
        structured = build_project_fact_lists(
            extractions, entities, None if globally_reviewed else self.llm
        )
        fact_lines = []
        for kind in ("method", "demarche", "parameter", "metric", "result"):
            fact_lines.append(f"\n{kind.upper()} ({len(structured[kind])})")
            fact_lines.extend(
                f"- {point.text} [preuves: {', '.join(point.evidence_ids)}; "
                f"temporalite: {point.temporal_status}]"
                for point in structured[kind]
            )
        prompt += (
            "\n\nINVENTAIRE STRUCTURE PROJET A UTILISER POUR LES RESUMES "
            "STRATEGIQUES (ne pas attribuer la litterature au projet) :\n"
            + "\n".join(fact_lines)
        )



        if self.llm is None:

            return ProjectSynthesis(

                project_evidence_count=stats[

                    "project_evidence_count"

                ],

                literature_evidence_count=stats[

                    "literature_evidence_count"

                ],

                section_count=stats[

                    "section_count"

                ],
                methods=structured["method"],
                demarches=structured["demarche"],
                parameters=structured["parameter"],
                metrics=structured["metric"],
                results=structured["result"],

                missing_information=[

                    "Synthèse LLM non exécutée."

                ],

                needs_review=True,

            )



        try:

            synthesis_llm = self.reviewed_llm if globally_reviewed else self.llm
            raw = synthesis_llm.extract_json(

                PROJECT_SYNTHESIS_SYSTEM + (
                    "\nLes listes structurees method/demarche/parameter/metric/result "
                    "sont deja etablies dans le prompt. Redige les champs de "
                    "synthese strategique A PARTIR de ces listes et des vrais "
                    "verrous projet. Pour les cinq cles de listes dans ta "
                    "reponse JSON, retourne [] : elles seront fournies par "
                    "l'inventaire tracable, sans limite arbitraire."
                ),

                prompt,

            )



            data = parse_json_payload(

                raw

            )



        except Exception as exc:

            return ProjectSynthesis(

                project_evidence_count=stats[

                    "project_evidence_count"

                ],

                literature_evidence_count=stats[

                    "literature_evidence_count"

                ],

                section_count=stats[

                    "section_count"

                ],
                methods=structured["method"],
                demarches=structured["demarche"],
                parameters=structured["parameter"],
                metrics=structured["metric"],
                results=structured["result"],

                needs_review=True,

                generation_error=type(

                    exc

                ).__name__,

                missing_information=[

                    "La synthèse automatique n'a pas pu être générée."

                ],

            )



        valid_evidence_ids = {

            item.evidence.evidence_id

            for item in extractions

            if item.evidence.evidence_id

        }



        for entity in entities:

            for evidence in entity.evidences:

                if evidence.evidence_id:

                    valid_evidence_ids.add(

                        evidence.evidence_id

                    )



        evidence_ids = [

            str(value).strip()

            for value in (

                data.get(

                    "evidence_ids"

                ) or []

            )

            if str(value).strip()

            in valid_evidence_ids

        ]



        def text(

            name: str,

        ) -> str:

            return str(

                data.get(name) or ""

            ).strip()



        objective_points = []

        seen_objective_points = set()



        for value in (data.get("objective_points") or []):

            point = str(value or "").strip()



            if not point:

                continue



            key = _norm(point)



            if not key or key in seen_objective_points:

                continue



            seen_objective_points.add(key)

            objective_points.append(point)



            if len(objective_points) >= 6:

                break



        # =====================================================

        # LISTES COURTES ET TRACABLES POUR LE FRONTEND

        # =====================================================



        project_items_for_traceability, _, _ = (

            _split_extractions(

                extractions

            )

        )



        valid_ids_by_kind = {

            "method": set(),

            "demarche": set(),

            "parameter": set(),
            "metric": set(),

            "result": set(),

        }



        for item in project_items_for_traceability:

            kind = str(

                getattr(

                    item,

                    "kind",

                    "",

                ) or ""

            ).strip().lower()



            evidence_id = str(

                getattr(

                    item.evidence,

                    "evidence_id",

                    "",

                ) or ""

            ).strip()



            if (

                kind in valid_ids_by_kind

                and evidence_id

            ):

                valid_ids_by_kind[

                    kind

                ].add(

                    evidence_id

                )



        def traceable_points(

            name: str,

            kind: str,

        ) -> List[TraceablePoint]:



            out: List[

                TraceablePoint

            ] = []



            seen = set()



            allowed_ids = (

                valid_ids_by_kind.get(

                    kind,

                    set(),

                )

            )



            for raw_point in (

                data.get(name) or []

            ):

                if not isinstance(

                    raw_point,

                    dict,

                ):

                    continue



                point_text = str(

                    raw_point.get(

                        "text"

                    ) or ""

                ).strip()



                if not point_text:

                    continue



                point_ids = []

                seen_ids = set()



                for value in (

                    raw_point.get(

                        "evidence_ids"

                    ) or []

                ):

                    evidence_id = str(

                        value or ""

                    ).strip()



                    if (

                        not evidence_id

                        or evidence_id not in allowed_ids

                        or evidence_id in seen_ids

                    ):

                        continue



                    seen_ids.add(

                        evidence_id

                    )



                    point_ids.append(

                        evidence_id

                    )



                # Un ?l?ment affich? doit toujours poss?der

                # au moins une vraie preuve du bon type.

                if not point_ids:

                    continue



                key = (

                    _norm(

                        point_text

                    ),

                    tuple(

                        point_ids

                    ),

                )



                if key in seen:

                    continue



                seen.add(key)



                out.append(

                    TraceablePoint(

                        text=point_text,

                        evidence_ids=point_ids,

                    )

                )



            return out



        def demarche_points_from_llm() -> List[TraceablePoint]:
            """
            Second contrôle sémantique des démarches.

            L'extraction sémantique propose des candidats TYPE=demarche.
            Le même appel LLM de synthèse sélectionne uniquement les vraies
            étapes techniques/scientifiques et retourne leurs evidence_ids.

            Python n'invente aucun filtre métier : il vérifie seulement que
            les preuves retournées existent bien et sont de TYPE=demarche,
            puis restaure la temporalité issue de l'extraction source.
            """
            temporal_labels = {
                "planned": "Prévu",
                "in_progress": "En cours",
                "performed": "Réalisé",
                "unclear": "Temporalité non précisée",
            }

            allowed_ids = valid_ids_by_kind.get("demarche", set())
            if not allowed_ids:
                return []

            source_by_evidence_id = {}
            source_order = {}
            chunk_first_index = {}

            for source_index, item in enumerate(project_items_for_traceability):
                if str(getattr(item, "kind", "") or "").strip().lower() != "demarche":
                    continue

                evidence_id = str(
                    getattr(item.evidence, "evidence_id", "") or ""
                ).strip()

                if not evidence_id or evidence_id not in allowed_ids:
                    continue

                source_by_evidence_id[evidence_id] = item

                chunk_id = str(
                    getattr(item.evidence, "chunk_id", "") or ""
                ).strip()

                chunk_first_index.setdefault(chunk_id, source_index)

                sequence_position = getattr(item, "sequence_position", None)
                try:
                    sequence_position = (
                        int(sequence_position)
                        if sequence_position is not None
                        else None
                    )
                except (TypeError, ValueError):
                    sequence_position = None

                source_order[evidence_id] = (
                    chunk_first_index.get(chunk_id, source_index),
                    sequence_position is None,
                    sequence_position if sequence_position is not None else source_index,
                    source_index,
                )

            prepared = []
            seen = set()

            for llm_index, raw_point in enumerate(data.get("demarches") or []):
                if not isinstance(raw_point, dict):
                    continue

                point_text = str(raw_point.get("text") or "").strip()
                if not point_text:
                    continue

                point_ids = []
                seen_ids = set()

                for value in raw_point.get("evidence_ids") or []:
                    evidence_id = str(value or "").strip()

                    if (
                        not evidence_id
                        or evidence_id not in allowed_ids
                        or evidence_id not in source_by_evidence_id
                        or evidence_id in seen_ids
                    ):
                        continue

                    seen_ids.add(evidence_id)
                    point_ids.append(evidence_id)

                if not point_ids:
                    continue

                # Une même carte ne mélange jamais prévu / en cours / réalisé.
                first_item = source_by_evidence_id[point_ids[0]]
                temporal_status = str(
                    getattr(first_item, "temporal_status", "")
                    or (getattr(first_item, "metadata", {}) or {}).get(
                        "temporal_status", "unclear"
                    )
                    or "unclear"
                ).strip().lower()

                if temporal_status not in temporal_labels:
                    temporal_status = "unclear"

                same_temporal_ids = []
                for evidence_id in point_ids:
                    item = source_by_evidence_id[evidence_id]
                    item_status = str(
                        getattr(item, "temporal_status", "")
                        or (getattr(item, "metadata", {}) or {}).get(
                            "temporal_status", "unclear"
                        )
                        or "unclear"
                    ).strip().lower()

                    if item_status not in temporal_labels:
                        item_status = "unclear"

                    if item_status == temporal_status:
                        same_temporal_ids.append(evidence_id)

                point_ids = same_temporal_ids
                if not point_ids:
                    continue

                prefix = temporal_labels[temporal_status]
                clean_statement = point_text

                lowered = _norm(clean_statement)
                if not (
                    lowered.startswith("prévu")
                    or lowered.startswith("en cours")
                    or lowered.startswith("réalisé")
                    or lowered.startswith("temporalité")
                ):
                    clean_statement = f"{prefix} — {clean_statement}"

                key = (
                    _norm(clean_statement),
                    tuple(point_ids),
                )

                if key in seen:
                    continue

                seen.add(key)

                # L'ordre documentaire reste prioritaire ; l'ordre LLM ne sert
                # qu'en dernier recours si plusieurs points partagent la même source.
                order_key = min(
                    (source_order.get(evidence_id, (10**9, True, 10**9, 10**9))
                     for evidence_id in point_ids),
                    default=(10**9, True, 10**9, 10**9),
                )

                prepared.append(
                    (
                        order_key,
                        llm_index,
                        TraceablePoint(
                            text=clean_statement,
                            evidence_ids=point_ids,
                        ),
                    )
                )

            prepared.sort(key=lambda value: (value[0], value[1]))
            return [point for _, _, point in prepared]



        methods = structured["method"]
        demarches = structured["demarche"]
        parameters = structured["parameter"]
        metrics = structured["metric"]
        results = structured["result"]



        missing_information = [

            str(value).strip()

            for value in (

                data.get(

                    "missing_information"

                ) or []

            )

            if str(value).strip()

        ]



        rnd_status = _support_status(

            data.get(

                "rnd_relevance_status"

            )

        )



        gap_status = _support_status(

            data.get(

                "existing_solutions_gap_status"

            )

        )





        # NO_PROJECT_RESULTS_FALLBACK

        # Une absence de resultats projet est imposee par les extractions,

        # pas decidee par le LLM.

        results_summary = text("results_summary")



        if stats.get("project_result_count", 0) == 0:

            results_summary = (

                "Aucun résultat expérimental ou constat technique "

                "issu des travaux n'est documenté dans les sources "

                "projet fournies."

            )



        return ProjectSynthesis(

            project_summary=text(

                "project_summary"

            ),

            main_objective=text(

                "main_objective"

            ),

            objective_points=objective_points,

            technical_context=text(

                "technical_context"

            ),

            technical_ambition=text(

                "technical_ambition"

            ),

            existing_solutions_gap=text(

                "existing_solutions_gap"

            ),

            knowledge_gap=text(

                "knowledge_gap"

            ),

            methods_summary=text(

                "methods_summary"

            ),

            results_summary=results_summary,



            methods=methods,

            demarches=demarches,

            parameters=parameters,
            metrics=metrics,

            results=results,



            constraints_summary=text(

                "constraints_summary"

            ),

            main_locks_summary=text(

                "main_locks_summary"

            ),

            state_of_art_context=text(

                "state_of_art_context"

            ),

            rnd_relevance_status=rnd_status,

            rnd_relevance_reason=text(

                "rnd_relevance_reason"

            ),

            existing_solutions_gap_status=gap_status,

            existing_solutions_gap_reason=text(

                "existing_solutions_gap_reason"

            ),

            evidence_ids=evidence_ids,

            missing_information=missing_information,

            project_evidence_count=stats[

                "project_evidence_count"

            ],

            literature_evidence_count=stats[

                "literature_evidence_count"

            ],

            section_count=stats[

                "section_count"

            ],

            needs_review=(

                bool(

                    missing_information

                )

                or rnd_status == "unknown"

                or gap_status == "unknown"

            ),

        )
