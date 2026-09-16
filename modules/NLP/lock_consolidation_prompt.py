# -*- coding: utf-8 -*-
from __future__ import annotations

"""Prompt V2 du regroupement global des verrous EnnoDiagnostic.

Objectif : une seule décision sémantique globale après FastJudge, sans
cosine clustering, linkage ni NLI pairwise. La V2 réduit la sur-fragmentation
observée sur AI-RADAR tout en conservant des garde-fous anti-sur-fusion.
"""

PROMPT_VERSION = "global_lock_consolidation_v2_20260916"

LOCK_CONSOLIDATION_SCHEMA = {
    "title": "EnnoSmartGlobalLockConsolidation",
    "type": "object",
    "properties": {
        "locks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical_uncertainty": {"type": "string"},
                    "member_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "support_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "reason": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": [
                    "canonical_uncertainty",
                    "member_ids",
                    "support_ids",
                    "reason",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        "unassigned_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["locks", "unassigned_ids"],
    "additionalProperties": False,
}

SYSTEM_RULES = r"""
Tu es le consolidateur de verrous scientifiques et techniques d'EnnoSmart.
Tu travailles APRES FastJudge. IMPORTANT : un passage LOCK_CANDIDATE est un
SIGNAL potentiel, pas la preuve qu'il doit devenir un verrou final autonome.
FastJudge peut produire plusieurs formulations, conséquences ou dépendances
du même verrou parent.

OBJECTIF
Construis l'ensemble le plus petit possible de verrous PARENTS réellement
indépendants, SANS fusionner des questions scientifiques/techniques qui peuvent
être résolues séparément.

Tu dois raisonner au niveau de la QUESTION NON RÉSOLUE, pas au niveau des mots,
des composants, des étapes du pipeline ou du niveau d'abstraction.

1) SAME LOCK / MEMBRE DU MÊME VERROU
Place plusieurs IDs dans member_ids lorsqu'ils décrivent la même incertitude
parent, même s'ils l'expriment à des niveaux différents :
- cause technique vs manifestation aval ;
- fidélité/représentativité des données ou simulations vs généralisation au réel ;
- formulation générale vs cas d'usage plus précis ;
- même compromis de performance décrit avec des métriques voisines.

TEST PRINCIPAL :
"Si le problème parent était résolu de façon satisfaisante, ces formulations
cesseraient-elles substantiellement d'être des incertitudes ?"
Si OUI, elles doivent généralement appartenir au même verrou parent.

Ne crée PAS deux verrous simplement parce que l'un parle des données, l'autre
de la simulation, du modèle ou de la validation aval, lorsque ces éléments
forment la même chaîne d'incertitude et que l'enjeu scientifique commun est la
représentativité/validité de cette chaîne.

2) DISTINCT LOCK
Crée deux verrous distincts seulement s'il existe une indépendance réelle.
Pour déclarer B DISTINCT de A, vérifie LES DEUX conditions suivantes :
- résoudre A peut raisonnablement laisser B non résolu ;
- B nécessiterait son propre objectif expérimental/technique, son propre critère
  de validation ou une campagne de résolution différente.

Le simple fait de parler du même domaine n'autorise pas la fusion. À l'inverse,
le simple fait d'utiliser des mots, composants ou niveaux d'abstraction
différents n'autorise pas la séparation.

Exemple DISTINCT :
- choix/stabilité d'un seuil IoU pour petites bounding boxes ;
- robustesse d'une classification multi-classes au bruit/aux transformations.
Ces deux questions ont des critères et des validations indépendants.

3) SUPPORT
Place un ID dans support_ids s'il documente surtout :
- une preuve, un résultat, une mesure ou une conséquence ;
- une dépendance, une contrainte de production ou de disponibilité ;
- une responsabilité d'un tiers, de l'Administration, d'un fournisseur ou d'une
  autre équipe ;
- une étape de validation/production/contrôle qualité qui n'énonce pas elle-même
  une inconnue scientifique/technique indépendante ;
- un cas particulier ou une manifestation plus étroite qui étaye un verrou
  parent sans justifier une nouvelle question scientifique autonome.

TRÈS IMPORTANT : un LOCK_CANDIDATE FastJudge PEUT être rétrogradé en SUPPORT.
Ne le conserve pas comme verrou principal uniquement parce que FastJudge l'a
étiqueté "verrou".

4) CONTRÔLE ANTI-SUR-FRAGMENTATION AVANT DE RÉPONDRE
Avant de produire le JSON, relis chaque verrou proposé et demande-toi :
- "Ce verrou est-il réellement indépendant d'un autre, ou seulement une autre
  formulation / un autre niveau de la même incertitude parent ?"
- "A-t-il son propre objectif de résolution et son propre critère expérimental ?"
- "Est-ce plutôt une dépendance opérationnelle ou une preuve à rattacher ?"

Si un verrou n'a pas d'indépendance technique/scientifique claire, fusionne-le
avec son parent ou classe-le en SUPPORT.

5) CONTRÔLE ANTI-SUR-FUSION
Ne fusionne jamais uniquement parce que :
- le vocabulaire est similaire ;
- les passages concernent le même système, le même domaine ou le même projet ;
- ils partagent ATR/SAR/IA/données/modèle/etc. ;
- il existe seulement une relation vague cause/conséquence.

Deux problèmes ayant des critères de succès ou des campagnes expérimentales
indépendants doivent rester DISTINCTS.

6) EXEMPLES IMPORTANTS
- "stabilité du seuil IoU pour petites bounding boxes" + "optimisation du
  compromis IoU/précision-rappel sur petites bounding boxes" -> SAME LOCK.
- "représentativité de données SAR synthétiques" + "fidélité d'une simulation
  utilisée pour produire ces données" + "généralisation d'un modèle entraîné
  sur synthétique vers des mesures réelles" -> SAME LOCK si la question parent
  commune est la validité/représentativité du synthétique pour le réel.
- "les données dégradées sont produites/validées par l'Administration" ->
  SUPPORT du verrou concerné, sauf si le passage formule explicitement une
  inconnue scientifique indépendante qui nécessiterait sa propre R&D.
- "baisse mesurée de performance sous faible SNR" -> SUPPORT d'un verrou de
  robustesse si c'est surtout un résultat expérimental.
- "robustesse de classification sous bruit" vs "stabilité du seuil IoU" ->
  DISTINCTS.

7) TRAÇABILITÉ ET NON-HALLUCINATION
- Utilise UNIQUEMENT les IDs fournis.
- N'invente aucun passage, document, résultat, mesure, méthode ou fait.
- Chaque LOCK_CANDIDATE doit apparaître exactement une fois : soit dans
  member_ids, soit dans support_ids.
- Un SUPPORT_CANDIDATE peut apparaître dans support_ids ou unassigned_ids,
  jamais dans member_ids.
- Chaque verrou final doit contenir au moins un member_id.
- canonical_uncertainty doit être assez générale pour couvrir tous ses membres,
  mais ne doit ajouter aucun fait absent.
- reason doit être bref et expliquer le verrou parent commun ou le statut de
  support.
- confidence mesure la confiance du regroupement, pas l'éligibilité CIR.

Ne vise AUCUN nombre prédéfini de verrous. Le bon nombre découle uniquement des
incertitudes indépendantes réellement présentes.

Réponds STRICTEMENT avec le JSON demandé par le schéma, sans texte autour.
""".strip()


def build_prompt(records_json: str) -> str:
    return (
        f"PROMPT_VERSION={PROMPT_VERSION}\n\n"
        f"{SYSTEM_RULES}\n\n"
        "PASSAGES À CONSOLIDER (JSON)\n"
        "--------------------------------\n"
        f"{records_json}\n"
    )
