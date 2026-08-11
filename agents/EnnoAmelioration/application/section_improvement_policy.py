from __future__ import annotations

from typing import Any

from ..domain.models import SectionFunction


_POLICIES: dict[SectionFunction, dict[str, Any]] = {
    SectionFunction.CONTEXT: {
        "role": "Présenter le contexte, le besoin, le périmètre et la raison d'être du projet, sans anticiper les sections de verrous, méthode ou résultats.",
        "improve": [
            "reconstruire une progression causale lisible à partir des seules idées déjà présentes : contexte général → besoin métier/technique → contrainte ou limitation → piste retenue → limite de cette piste → motivation des travaux, uniquement pour les maillons réellement documentés",
            "réécrire au niveau des blocs d'idées et des paragraphes : les phrases sources apportent des faits à préserver, mais ne constituent pas des unités de rédaction à paraphraser une par une",
            "fusionner les phrases adjacentes qui portent la même idée, supprimer les répétitions de formulation et créer des transitions explicites uniquement lorsque la relation logique est déjà présente dans la source",
            "faire ressortir plus nettement le besoin du projet et la chaîne cause → conséquence déjà documentée, sans ajouter de nouvelle causalité",
            "clarifier le besoin initial et son lien avec l'objectif du projet lorsqu'ils sont déjà exprimés",
            "réduire les détours descriptifs tout en conservant chaque information technique utile et son niveau de précision exact",
            "terminer sur la motivation des travaux seulement si cette motivation est déjà présente dans le texte",
        ],
        "do_not_force": [
            "un verrou scientifique ou technique",
            "des résultats qui appartiennent à une section de résultats",
            "une contribution ou une avancée de connaissance non documentée",
            "des références bibliographiques nouvelles",
            "un maillon causal absent du texte source",
            "une qualification plus précise que celle du texte source",
        ],
        "evidence_rule": "Réécriture à faits constants. Conserver les concepts techniques, qualificatifs, dimensions, catégories et relations causales au même niveau de précision que la source. Une preuve projet peut seulement préciser un élément déjà documenté ; elle ne doit pas servir à transformer le rôle de la section.",
        "structure_rule": "Préserver l'ordre logique des concepts, mais pas la correspondance phrase-à-phrase. Pour une section développée, organiser le texte en quelques blocs argumentatifs cohérents, chacun ayant une fonction claire. Une reformulation peut fusionner ou scinder des phrases à l'intérieur d'un même bloc, supprimer une redondance de formulation et renforcer les transitions, mais elle ne doit jamais perdre un fait et ne doit pas permuter des blocs thématiques indépendants, déplacer une conclusion avant ses justifications ni créer une relation causale absente de la source.",
    },
    SectionFunction.SCIENTIFIC_LANDSCAPE: {
        "role": "Présenter les connaissances antérieures, les approches existantes et le positionnement scientifique du projet.",
        "improve": [
            "séparer clairement connaissances établies, limites de l'existant et positionnement du projet",
            "améliorer les transitions entre familles d'approches ou travaux cités",
            "rendre chaque comparaison traçable lorsqu'une source validée est disponible",
            "éviter de transformer une absence de preuve en affirmation de lacune scientifique",
        ],
        "do_not_force": [
            "un verrou projet à la place de l'état de l'art",
            "des performances ou limites absentes des sources",
            "des citations, auteurs ou articles inventés",
            "une conclusion d'éligibilité CIR",
        ],
        "evidence_rule": "Sans preuve Scholar validée, améliorer uniquement la forme et l'organisation des affirmations déjà présentes. Toute information scientifique nouvelle exige une source validée.",
    },
    SectionFunction.UNCERTAINTY: {
        "role": "Rendre explicite et défendable l'incertitude scientifique ou technique réellement documentée : ce qui est observé, ce qui reste non maîtrisé, l'impact possible sur les travaux et la raison pour laquelle une investigation est nécessaire.",
        "improve": [
            "reconstruire une démonstration lisible à partir des seuls éléments étayés : constat documenté → mécanisme ou source de difficulté → conséquence ou risque documenté → point restant non maîtrisé → besoin d'investigation",
            "séparer clairement les observations du projet, les interprétations prudentes et les limites établies par la littérature",
            "faire ressortir l'incertitude elle-même plutôt que d'accumuler des qualificatifs CIR",
            "expliquer pourquoi une résolution n'était pas immédiate uniquement lorsqu'une preuve montre une impossibilité de déterminer les paramètres a priori, un échec/une limite de méthodes testées, une variabilité non maîtrisée ou la nécessité d'itérations expérimentales",
            "relier les preuves EnnoDiagnostic aux faits du projet et les preuves EnnoScholar uniquement aux limites ou connaissances scientifiques qu'elles soutiennent directement",
            "terminer sur le besoin d'investigation ou d'expérimentation seulement si ce besoin découle des éléments documentés ; rester prudent sur la méthode exacte si elle n'est pas décrite",
        ],
        "do_not_force": [
            "les mots 'majeur', 'crucial', 'indispensable', 'non trivial', 'robuste', 'garantir' ou équivalents sans support explicite",
            "l'échec de solutions standards non testées ou non documentées",
            "des méthodes prétendument insuffisantes (augmentation standard, plus de bruit, réglage simple, etc.) si aucune preuve ne les évalue",
            "un protocole expérimental détaillé, des métriques ou des comparaisons qui appartiennent à une autre section et ne sont pas documentés ici",
            "une garantie de généralisation, de robustesse, d'efficacité ou de validité en conditions réelles",
            "une qualification de verrou simplement à partir d'un vocabulaire fort",
            "des causes, conséquences, résultats ou objectifs supposés",
        ],
        "evidence_rule": "Un fait projet nouveau doit être soutenu par le texte source ou une preuve EnnoDiagnostic précise. Une limite scientifique générale nouvelle doit être soutenue par une preuve EnnoScholar validée. Une source Scholar ne peut pas prouver qu'un événement s'est produit dans le projet ; elle peut seulement étayer le caractère connu, ouvert ou limité du problème scientifique.",
        "structure_rule": "Privilégier quelques blocs argumentatifs cohérents. Ne pas terminer automatiquement par un plan de travaux ou une promesse de résultat : la section doit défendre l'incertitude, pas écrire la méthodologie ni la conclusion du projet.",
    },
    SectionFunction.METHOD: {
        "role": "Décrire la démarche, les travaux, essais, protocoles ou choix méthodologiques réalisés.",
        "improve": [
            "rendre la séquence des travaux compréhensible",
            "expliquer le rôle de chaque étape par rapport au problème traité lorsque cela est documenté",
            "mettre en évidence les choix, itérations et adaptations réellement effectués",
            "séparer clairement méthode, paramètres, observations et résultats",
        ],
        "do_not_force": [
            "une incertitude nouvelle",
            "des essais non réalisés",
            "une justification technique absente du dossier",
            "des performances qui relèvent des résultats",
        ],
        "evidence_rule": "Les détails de méthode ajoutés doivent provenir du texte ou de preuves projet traçables.",
    },
    SectionFunction.PARAMETER: {
        "role": "Décrire les variables, conditions, contraintes, réglages et hypothèses qui encadrent les travaux.",
        "improve": [
            "rendre explicite la fonction de chaque paramètre ou contrainte documentée",
            "distinguer paramètres imposés, variables étudiées et hypothèses",
            "clarifier les conditions de validité et de comparaison lorsqu'elles sont présentes",
            "relier les paramètres aux choix méthodologiques ou observations sans extrapoler",
        ],
        "do_not_force": [
            "une sensibilité non mesurée",
            "des seuils ou valeurs absents des documents",
            "une causalité non démontrée",
            "des limites expérimentales supposées",
        ],
        "evidence_rule": "Aucune valeur, plage, seuil ou dépendance nouvelle sans preuve projet explicite.",
    },
    SectionFunction.RESULT: {
        "role": "Présenter les observations, mesures et résultats obtenus, puis leur interprétation dans la limite des preuves disponibles.",
        "improve": [
            "séparer résultat observé et interprétation",
            "mettre en avant les résultats déterminants sans supprimer les réserves",
            "relier un résultat à l'objectif, au protocole ou à la condition correspondante lorsqu'ils sont documentés",
            "faire apparaître les limites de validité déjà observées",
        ],
        "do_not_force": [
            "une amélioration de performance non mesurée",
            "une causalité qui n'a pas été démontrée",
            "une généralisation au-delà du périmètre testé",
            "une contribution R&D déduite automatiquement d'un résultat positif",
        ],
        "evidence_rule": "Tout chiffre, comparaison, performance ou interprétation factuelle nouvelle exige une preuve projet précise.",
    },
    SectionFunction.LIMITATION: {
        "role": "Décrire les limites, échecs, conditions de validité restreinte ou points restant non résolus.",
        "improve": [
            "préciser la nature de la limite et les conditions dans lesquelles elle apparaît",
            "expliciter son impact sur la solution ou la validité des résultats lorsqu'il est documenté",
            "distinguer limite observée, hypothèse et incertitude restante",
            "conserver les réserves et résultats négatifs utiles à la démonstration R&D",
        ],
        "do_not_force": [
            "une cause non démontrée",
            "un échec de méthode non documenté",
            "une insuffisance de l'état de l'art sans source",
            "une incertitude plus large que celle réellement observée",
        ],
        "evidence_rule": "Les causes et conséquences nouvelles d'une limite doivent être étayées par le dossier ou une source scientifique validée selon le cas.",
    },
    SectionFunction.CONTRIBUTION: {
        "role": "Présenter l'apport technique ou la connaissance produite par les travaux, sans surévaluer leur portée.",
        "improve": [
            "formuler précisément ce que les travaux ont permis d'établir ou de produire",
            "relier l'apport au problème initial et aux résultats documentés",
            "distinguer livrable d'ingénierie, résultat expérimental et connaissance acquise",
            "indiquer les limites de portée lorsque le dossier les documente",
        ],
        "do_not_force": [
            "une nouveauté scientifique non démontrée",
            "une avancée de connaissance déduite automatiquement",
            "une portée générale supérieure aux essais réalisés",
            "une éligibilité CIR officielle",
        ],
        "evidence_rule": "Toute contribution nouvelle doit être directement reliée à un résultat ou une preuve projet identifiable.",
    },
    SectionFunction.SYNTHESIS: {
        "role": "Synthétiser les éléments déjà établis dans le document et rendre leur articulation claire.",
        "improve": [
            "rappeler uniquement les éléments démontrés dans les sections précédentes",
            "rendre explicite le fil logique entre objectif, travaux, résultats et limites lorsqu'ils existent",
            "hiérarchiser les conclusions sans introduire de nouveau fait",
            "faire apparaître les points encore ouverts s'ils sont déjà documentés",
        ],
        "do_not_force": [
            "un verrou absent du document",
            "un résultat ou chiffre nouveau",
            "une nouvelle référence scientifique",
            "une conclusion d'éligibilité CIR",
        ],
        "evidence_rule": "La synthèse ne crée pas de preuve : elle réorganise les faits déjà établis.",
    },
    SectionFunction.OTHER: {
        "role": "Améliorer un contenu dont la fonction R&D précise n'est pas suffisamment déterminée.",
        "improve": [
            "améliorer la clarté, la cohérence et la lisibilité",
            "préserver strictement les faits et la portée du texte",
            "éviter toute spécialisation R&D automatique tant que le rôle n'est pas établi",
        ],
        "do_not_force": [
            "un verrou",
            "un état de l'art",
            "une méthode ou un résultat absent",
            "une contribution R&D non documentée",
        ],
        "evidence_rule": "Réécriture éditoriale à faits constants uniquement.",
    },
}


def get_section_improvement_policy(function: SectionFunction) -> dict[str, Any]:
    return dict(_POLICIES.get(function, _POLICIES[SectionFunction.OTHER]))


def render_section_improvement_contract(function: SectionFunction) -> str:
    policy = get_section_improvement_policy(function)
    improve = "\n".join(f"- {item}" for item in policy["improve"])
    forbidden = "\n".join(f"- Ne force jamais {item}." for item in policy["do_not_force"])
    structure_rule = str(policy.get("structure_rule") or "").strip()
    structure_block = (
        f"\n\nRÈGLE DE STRUCTURE\n{structure_rule}" if structure_rule else ""
    )
    return (
        f"RÔLE DE LA SECTION\n{policy['role']}\n\n"
        f"OBJECTIFS D'AMÉLIORATION PROPRES À CE RÔLE\n{improve}\n\n"
        f"GARDE-FOUS SPÉCIFIQUES\n{forbidden}\n\n"
        f"POLITIQUE DE PREUVE\n{policy['evidence_rule']}"
        f"{structure_block}"
    )
