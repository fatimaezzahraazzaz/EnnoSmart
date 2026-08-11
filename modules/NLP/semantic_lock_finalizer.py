
# -*- coding: utf-8 -*-
"""
EnnoSmart V189
Final Conceptual Lock Consolidation

But :
transformer les groupes techniques d?j? propres issus
de V185 en VERROUS PRINCIPAUX conceptuellement coh?rents.

Le module distingue :

LOCK_CORE
    = v?ritable incertitude / probl?me scientifique ou technique

SUPPORT_EVIDENCE
    = r?sultat, mesure, preuve ou sous-probl?me documentant un verrou

METHOD_CONTEXT
    = m?thode / impl?mentation / param?trage

NOISE
    = m?tadonn?e / bruit ?ditorial

Puis les LOCK_CORE sont compar?s selon :

SAME_PARENT_LOCK
SUPPORTS_LOCK
DISTINCT_LOCK

Important :
- aucun nom de projet en dur ;
- aucun nombre de verrous impos? ;
- FastJudge n'est pas modifi? ;
- aucune preuve n'est supprim?e ;
- le mod?le NLI est local Hugging Face.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata

from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .semantic_lock_adjudicator import (
    SemanticLockAdjudicator,
)


VERSION = (
    "semantic_lock_finalizer_v190_"
    "role_classifier_only_complete_linkage_unchanged"
)


# ============================================================
# HYPOTHESES NLI
# ============================================================

GROUP_ROLE_HYPOTHESES = {

    "LOCK_CORE":
        (
            "This text itself states an unresolved scientific "
            "or technical uncertainty, knowledge gap, validation "
            "problem, or trade-off that requires further "
            "investigation to understand or solve. It is not "
            "merely an experimental result, a measured degradation, "
            "a method, or an implementation detail."
        ),

    "SUPPORT_EVIDENCE":
        (
            "This text mainly reports a concrete experimental "
            "result, measurement, observed degradation, empirical "
            "fact, consequence, or supporting observation. It can "
            "support a broader unresolved problem, but does not by "
            "itself formulate that broader scientific or technical "
            "uncertainty."
        ),

    "METHOD_CONTEXT":
        (
            "This text mainly describes a method, implementation, "
            "configuration, parameter, technical procedure, known "
            "mechanism, or experimental setup rather than an "
            "unresolved scientific or technical problem."
        ),

    "NOISE":
        (
            "This text is mainly editorial metadata, citation, "
            "bibliography, acknowledgement, publication information, "
            "or irrelevant noise."
        ),
}


# Seconde lecture NLI, volontairement limitee au cas ambigu « limite d'une
# methode existante » vs « verrou explicitement non resolu ». Elle ne contient
# aucun vocabulaire de domaine ou de projet et ne participe jamais au
# regroupement des groupes.
LIMITATION_ROLE_HYPOTHESES = {
    "ATTRIBUTED_METHOD_LIMITATION":
        (
            "This text reports a concrete limitation, failure, prerequisite, "
            "restricted scope, or inability attributed to an existing method, "
            "model, classifier, algorithm, system, or technical approach."
        ),
    "EXPLICIT_UNRESOLVED_PROBLEM":
        (
            "This text explicitly formulates the broader unresolved scientific "
            "or technical question, unknown, knowledge gap, or trade-off that "
            "still needs to be understood or solved."
        ),
}

PAIR_HYPOTHESES = {

    "SAME_PARENT_LOCK":
        (
            "These two groups formulate the same underlying "
            "unresolved scientific or technical question. "
            "Resolving the core uncertainty expressed by one would "
            "substantially resolve the core uncertainty expressed by "
            "the other. They are not merely related by domain, method, "
            "cause, consequence, or shared evidence."
        ),

    "SUPPORTS_LOCK":
        (
            "The two groups are related, but one mainly provides "
            "evidence, an experimental result, a method, a consequence, "
            "or a narrower manifestation that supports the unresolved "
            "problem expressed by the other. They should not be merged "
            "as two equivalent main locks."
        ),

    "DISTINCT_LOCK":
        (
            "These two groups express different underlying unresolved "
            "scientific or technical questions. They may concern the "
            "same system or domain and may interact, but resolving one "
            "would not by itself resolve the other. They should remain "
            "separate main technical locks."
        ),
}


# ============================================================
# CLEANING
# ============================================================

EDITORIAL = re.compile(
    r"\b(?:"
    r"to cite this version|"
    r"archive for the deposit|"
    r"this work has been funded|"
    r"funded by|"
    r"acknowledg(?:e)?ments?|"
    r"copyright|"
    r"all rights reserved|"
    r"corresponding author|"
    r"author affiliation"
    r")\b",
    flags=re.I,
)


EDITORIAL_PREFIX = re.compile(
    r"^\s*(?:(?:"
    r"to cite this version|"
    r"please cite (?:this|the) version|"
    r"citation(?: information)?|"
    r"suggested citation|"
    r"how to cite"
    r")\s*(?::|[-\u2013\u2014])?\s*)+",
    flags=re.I,
)


LOCK_INTENT_PATTERNS = (
    # Une proposition relative telle que « signatures, which are expected »
    # n'est pas une question. La forme interrogative complete et son point
    # d'interrogation sont donc requis.
    r"(?:^|[.!?]\s+)(?:(?:what|which|how|why)\s+(?:is|are|does|do|can|could|should|would)|(?:is|are|does|do|can|could|should|would)\s+[^?]{1,80})\b[^?]{0,240}\?",
    r"\b(?:quelle?s?|quels?|comment|pourquoi)\b.{0,100}\?",
    r"\b(?:open|unresolved)\s+(?:question|problem|issue|challenge)s?\b",
    r"\b(?:question|probleme|incertitude)\s+(?:ouverte?|non resolu[e]?|scientifique|technique)\b",
    r"\b(?:remains?|remain)\s+(?:unknown|unclear|unresolved|undetermined|unvalidated)\b",
    r"\breste(?:nt)?\s+(?:inconnu[e]?|incertain[e]?|a (?:determiner|comprendre|valider|verifier))\b",
    r"\b(?:knowledge|scientific|technical)\s+gap\b",
    r"\b(?:necessary|sufficient)\b.{0,80}\b(?:necessary|sufficient)\b",
    r"\bnecessaire?s?\b.{0,80}\bsuffisante?s?\b",
    r"\b(?:not|insufficiently)\s+(?:representative|understood|validated|demonstrated|controlled|predictable)\b",
    r"\b(?:non|insuffisamment)\s+(?:representatif|representative|compris|comprise|valide|validee|demontre|demontree|maitrise|maitrisee|predictible)\b",
    r"\bcannot\s+(?:generalize|generalise|guarantee|predict|determine|validate|explain)\b",
    r"\b(?:ne peut|impossible de)\s+(?:generaliser|garantir|predire|determiner|valider|expliquer)\b",
    r"\bgenerali[sz]ation\s+(?:gap|issue|issues|problem|problems)\b",
    r"\b(?:ecart|probleme)s?\s+de\s+generalisation\b",
    r"\b(?:trade[- ]?off|compromise|compromis)\b",
)


METHOD_CONTEXT_PATTERNS = (
    r"\b(?:we|the authors?)\s+(?:use|used|apply|applied|propose|proposed|evaluate|evaluated|measure|measured|compare|compared)\b",
    r"\b(?:nous|on)\s+(?:utilisons|utilise|appliquons|applique|proposons|propose|evaluons|evalue|mesurons|mesure|comparons|compare)\b",
    r"\b(?:method|methodology|approach|procedure|protocol|implementation|configuration|experimental setup)s?\b",
    r"\b(?:methode|methodologie|approche|procedure|protocole|implementation|configuration|montage experimental)e?s?\b",
    r"\b(?:metric|indicator|criterion)\s+(?:to|for|used to)\s+(?:measure|compare|evaluate|assess|quantify)\b",
    r"\b(?:metrique|indicateur|critere)\s+(?:pour|permettant de)\s+(?:mesurer|comparer|evaluer|quantifier)\b",
)


METHOD_INVENTORY_PATTERNS = (
    r"\b(?:different|several|multiple|various)\s+(?:methods?|approach(?:es)?|techniques?|procedures?)\b",
    r"\b(?:methods?|approach(?:es)?|techniques?|procedures?)\b.{0,120}\b(?:such as|including|for example)\b",
    r"\b(?:differentes?|plusieurs|multiples|diverses?)\s+(?:methodes?|approches?|techniques?|procedures?)\b",
    r"\b(?:methodes?|approches?|techniques?|procedures?)\b.{0,120}\b(?:comme|telles? que|notamment|par exemple)\b",
)


MEASUREMENT_METHOD_PATTERNS = (
    r"\b(?:provides?|gives?|serves? as|is used as)\s+(?:a|the)?\s*(?:metric|indicator|criterion)\b",
    r"\b(?:metric|indicator|criterion)\s+(?:to|for|used to)\s+(?:measure|compare|evaluate|assess|quantify)\b",
    r"\b(?:fournit|donne|sert de|est utilise comme)\s+(?:une?|la)?\s*(?:metrique|indicateur|critere)\b",
    r"\b(?:metrique|indicateur|critere)\s+(?:pour|permettant de)\s+(?:mesurer|comparer|evaluer|quantifier)\b",
)


RESULT_EVIDENCE_PATTERNS = (
    r"\b(?:results?|experiments?|measurements?)\s+(?:show|showed|indicate|indicated|demonstrate|demonstrated|confirm|confirmed)\b",
    r"\b(?:resultats?|essais?|mesures?)\s+(?:montrent|indiquent|demontrent|confirment)\b",
    r"\b(?:we|the authors?)\s+(?:found|observed|measured|obtained|achieved)\b",
    r"\b(?:nous|on)\s+(?:avons constate|constatons|observe|mesure|obtient|obtenons)\b",
    r"\b(?:accuracy|precision|score|error rate|performance)\b.{0,80}\b(?:reached|was|were|is|are|improved|decreased|increased|gap)\b",
    r"\b(?:precision|score|taux d erreur|performance)\b.{0,80}\b(?:atteint|etait|etaient|est|sont|ameliore|diminue|augmente|ecart)\b",
    r"\b(?:classification|prediction)\b.{0,80}\b(?:difficult|impossible|failed|fails)\b",
    r"\b(?:increments?|improvements?|gains?|degradations?|differences?|gaps?)\s+(?:remains?|persist(?:s|ed)?|is|are|was|were|led)\b",
    r"\b(?:gains?|ameliorations?|degradations?|differences?|ecarts?)\s+(?:reste(?:nt)?|persiste(?:nt)?|est|sont|etait|etaient|conduit|conduisent)\b",
)


# Marque uniquement la structure rhetorique « objet technique existant +
# limitation attribuee ». Le vocabulaire est transversal et la decision finale
# exige aussi une confirmation NLI ; ce n'est pas une liste de cas projet.
ATTRIBUTED_METHOD_LIMITATION_PATTERNS = (
    r"\b(?:methods?|methodologies|approach(?:es)?|models?|classifiers?|algorithms?|systems?|techniques?|frameworks?|pipelines?|simulators?|procedures?|protocols?)\b.{0,160}\b(?:only\s+)?(?:achieves?|requires?|depends?|cannot|fails?|failed|is\s+unable|are\s+unable|is\s+limited|are\s+limited|is\s+restricted|are\s+restricted)\b",
    r"\b(?:methodes?|methodologies|approches?|modeles?|classifieurs?|algorithmes?|systemes?|techniques?|simulateurs?|procedures?|protocoles?)\b.{0,160}\b(?:atteint|atteignent|necessite|necessitent|depend|dependent|ne\s+peut|ne\s+peuvent|echoue|echouent|est\s+limite|sont\s+limites|est\s+restreint|sont\s+restreints)\b",
)


def _clean(
    value: Any,
) -> str:

    text = str(
        value or ""
    )

    text = text.replace(
        "\r",
        " "
    ).replace(
        "\n",
        " "
    )

    # r?f?rences [12], [8], etc.
    text = re.sub(
        r"\[\s*\d{1,3}\s*\]",
        " ",
        text,
    )

    # coupures de mots PDF :
    # simu- lated -> simulated
    text = re.sub(
        r"(?<=\w)-\s+(?=\w)",
        "",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip(" -|")

    return text


def _strip_editorial_prefix(
    value: Any,
) -> str:
    """Retire un en-tete de citation sans supprimer le contenu scientifique.

    Le nettoyage est volontairement ancre au debut du passage. Un mot comme
    ``citation`` rencontre plus loin dans une phrase scientifique ne peut donc
    pas provoquer de coupe du texte utile.
    """

    text = _clean(value)

    for _ in range(3):
        cleaned = EDITORIAL_PREFIX.sub("", text, count=1).strip(" -|:;")
        if cleaned == text:
            break
        text = cleaned

    return text


def _signal_text(
    value: Any,
) -> str:
    text = unicodedata.normalize(
        "NFKD",
        _strip_editorial_prefix(value).lower(),
    )
    text = "".join(
        char
        for char in text
        if not unicodedata.combining(char)
    )
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _matched_patterns(
    text: str,
    patterns: Sequence[str],
) -> List[str]:
    return [
        pattern
        for pattern in patterns
        if re.search(pattern, text, flags=re.I)
    ]


def _semantic_role_signals(
    value: Any,
) -> Dict[str, Any]:
    """Extrait des indices generiques qui stabilisent la decision NLI.

    Ces indices ne contiennent aucun vocabulaire de projet. Ils ne remplacent
    pas le NLI : ils distinguent surtout les cas linguistiquement explicites
    (question ouverte, metrique d'evaluation, resultat observe). Les cas
    ambigus restent decides par le modele.
    """

    text = _signal_text(value)
    lock_matches = _matched_patterns(text, LOCK_INTENT_PATTERNS)
    method_matches = _matched_patterns(text, METHOD_CONTEXT_PATTERNS)
    method_inventory_matches = _matched_patterns(text, METHOD_INVENTORY_PATTERNS)
    measurement_matches = _matched_patterns(text, MEASUREMENT_METHOD_PATTERNS)
    result_matches = _matched_patterns(text, RESULT_EVIDENCE_PATTERNS)
    limitation_matches = _matched_patterns(
        text,
        ATTRIBUTED_METHOD_LIMITATION_PATTERNS,
    )

    has_lock_intent = bool(lock_matches)
    measurement_method = bool(measurement_matches)
    method_only = bool(
        not has_lock_intent
        and (
            measurement_method
            or (len(method_matches) >= 1 and not result_matches)
        )
    )
    result_only = bool(
        not has_lock_intent
        and bool(result_matches)
        and not measurement_method
    )

    return {
        "lock_intent": has_lock_intent,
        "method_only": method_only,
        "result_only": result_only,
        "measurement_method": measurement_method,
        "method_inventory": bool(method_inventory_matches),
        "attributed_method_limitation": bool(limitation_matches),
        "lock_signal_count": len(lock_matches),
        "method_signal_count": len(method_matches),
        "method_inventory_signal_count": len(method_inventory_matches),
        "result_signal_count": len(result_matches),
        "method_limitation_signal_count": len(limitation_matches),
    }


def _clean_seed_text(
    item: Mapping[str, Any],
) -> str:

    passage = _strip_editorial_prefix(
        item.get("text")
    )

    # On n'utilise PAS section_title par d?faut.
    # C'?tait une source importante de pollution
    # du type "To cite this version".
    if not passage:
        return ""

    # Si le passage a r?ellement besoin du contexte,
    # V185 l'a explicitement marqu?.
    if item.get(
        "lock_contextual_bridge"
    ):

        before = _strip_editorial_prefix(
            item.get(
                "context_before"
            )
        )

        after = _strip_editorial_prefix(
            item.get(
                "context_after"
            )
        )

        fragments = []

        for value in (
            before,
            passage,
            after,
        ):

            if not value:
                continue

            if (
                EDITORIAL.search(value)
                and value != passage
            ):
                continue

            fragments.append(
                value
            )

        passage = " ".join(
            fragments
        )

    return passage[:900]


# ============================================================
# PROFIL CONCEPTUEL DU GROUPE
# ============================================================

def build_problem_profile(
    group: Mapping[str, Any],
    *,
    max_seeds: int = 3,
) -> str:

    passages = [
        item
        for item in (
            group.get(
                "supporting_passages"
            )
            or []
        )
        if isinstance(
            item,
            Mapping,
        )
    ]

    seeds = [
        item
        for item in passages
        if item.get(
            "project_lock_seed"
        )
    ]

    # Important :
    # on ne profile plus le groupe avec toutes
    # ses m?thodes/r?sultats/supports.
    pool = (
        seeds
        if seeds
        else passages
    )

    def quality(
        item: Mapping[str, Any],
    ) -> float:

        score = 0.0

        if item.get(
            "project_lock_seed"
        ):
            score += 10.0

        if item.get(
            "lock_candidate_explicit"
        ):
            score += 1.0

        try:
            score += float(
                item.get(
                    "lock_candidate_score"
                )
                or item.get(
                    "verrou_score"
                )
                or 0.0
            )
        except Exception:
            pass

        return score


    pool = sorted(
        pool,
        key=quality,
        reverse=True,
    )

    parts: List[str] = []

    normalized_seen = set()

    for item in pool:

        text = _clean_seed_text(
            item
        )

        if len(text) < 35:
            continue

        low = re.sub(
            r"\W+",
            "",
            text.lower(),
        )

        if not low:
            continue

        duplicate = False

        for known in normalized_seen:

            if (
                low in known
                or known in low
            ):
                duplicate = True
                break

        if duplicate:
            continue

        normalized_seen.add(
            low
        )

        parts.append(
            text
        )

        if len(parts) >= max_seeds:
            break


    if not parts:

        fallback = _strip_editorial_prefix(
            group.get("text")
        )

        if fallback:
            parts.append(
                fallback[:900]
            )


    return "\n".join(
        parts
    )[:2200]


# ============================================================
# HELPERS
# ============================================================

def _normalise_scores(
    scores: Dict[str, float],
) -> Dict[str, float]:

    total = sum(
        max(
            0.0,
            float(value),
        )
        for value in scores.values()
    )

    if total <= 0.0:

        n = max(
            1,
            len(scores),
        )

        return {
            key:
                1.0 / n
            for key in scores
        }

    return {
        key:
            max(
                0.0,
                float(value),
            )
            / total
        for key, value
        in scores.items()
    }


def _cosine(
    left: Sequence[float],
    right: Sequence[float],
) -> float:

    if (
        left is None
        or right is None
    ):
        return 0.0

    if (
        len(left) == 0
        or len(right) == 0
        or len(left) != len(right)
    ):
        return 0.0

    dot = sum(
        float(a) * float(b)
        for a, b in zip(
            left,
            right,
        )
    )

    nl = math.sqrt(
        sum(
            float(x) * float(x)
            for x in left
        )
    )

    nr = math.sqrt(
        sum(
            float(x) * float(x)
            for x in right
        )
    )

    if nl <= 0.0 or nr <= 0.0:
        return 0.0

    return float(
        dot / (nl * nr)
    )


def _seed_count(
    group: Mapping[str, Any],
) -> int:

    return int(
        group.get(
            "project_lock_seed_count"
        )
        or group.get(
            "direct_candidate_count"
        )
        or 0
    )


def _passage_key(
    item: Mapping[str, Any],
) -> str:

    passage_id = str(
        item.get(
            "passage_id"
        )
        or ""
    ).strip()

    if passage_id:
        return passage_id

    return hashlib.sha1(
        _clean(
            item.get("text")
        ).encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()


# ============================================================
# FINALIZER
# ============================================================

class SemanticLockFinalizer:

    def __init__(
        self,
    ) -> None:

        # Réutilise exclusivement le snapshot NLI local déjà téléchargé.
        self.judge = (
            SemanticLockAdjudicator()
        )


    # --------------------------------------------------------
    # NLI hypotheses
    # --------------------------------------------------------

    def _score_hypotheses(
        self,
        premise: str,
        hypotheses: Mapping[
            str,
            str,
        ],
    ) -> Dict[str, float]:

        labels = list(
            hypotheses.keys()
        )

        texts = [
            hypotheses[label]
            for label in labels
        ]

        raw = (
            self.judge
            ._predict_direction(
                [premise]
                * len(texts),
                texts,
                batch_size=len(texts),
            )
        )

        scores = {
            label:
                float(
                    result.entailment
                )
            for label, result
            in zip(
                labels,
                raw,
            )
        }

        return _normalise_scores(
            scores
        )


    # --------------------------------------------------------
    # r?le du groupe
    # --------------------------------------------------------

    def classify_group(
        self,
        profile: str,
        group: Mapping[str, Any],
    ) -> Dict[str, Any]:

        scores = (
            self._score_hypotheses(
                profile,
                GROUP_ROLE_HYPOTHESES,
            )
        )

        ordered = sorted(
            scores.items(),
            key=lambda pair:
                pair[1],
            reverse=True,
        )

        top_role = ordered[0][0]
        top_score = ordered[0][1]

        second_score = (
            ordered[1][1]
            if len(ordered) > 1
            else 0.0
        )

        seeds = _seed_count(
            group
        )

        lock_score = float(
            scores.get(
                "LOCK_CORE",
                0.0,
            )
        )

        support_score = float(
            scores.get(
                "SUPPORT_EVIDENCE",
                0.0,
            )
        )

        method_score = float(
            scores.get(
                "METHOD_CONTEXT",
                0.0,
            )
        )

        noise_score = float(
            scores.get(
                "NOISE",
                0.0,
            )
        )

        semantic_signals = _semantic_role_signals(
            profile
        )

        limitation_role_scores: Dict[str, float] = {}
        limitation_confirmed = False

        # Le second passage NLI n'est lance que si la syntaxe attribue deja
        # une limitation a un objet technique. Cette double condition evite
        # qu'un simple mot comme « method » ou « cannot » decide seul du role.
        if (
            semantic_signals["attributed_method_limitation"]
            and not semantic_signals["lock_intent"]
        ):
            limitation_role_scores = self._score_hypotheses(
                profile,
                LIMITATION_ROLE_HYPOTHESES,
            )
            attributed_score = float(
                limitation_role_scores.get(
                    "ATTRIBUTED_METHOD_LIMITATION",
                    0.0,
                )
            )
            unresolved_score = float(
                limitation_role_scores.get(
                    "EXPLICIT_UNRESOLVED_PROBLEM",
                    0.0,
                )
            )
            limitation_confirmed = bool(
                attributed_score >= 0.60
                and attributed_score >= unresolved_score + 0.20
            )

        decision_reason = "nli_ambiguous_review"


        # --------------------------------------------
        # Decision hybride generique.
        #
        # Les cas linguistiquement explicites corrigent les confusions les
        # plus frequentes du NLI. Les seuils restent souples : un signal de
        # verrou explicite suffit avec un NLI seulement compatible, tandis
        # qu'une methode ou un resultat ne sont retrogrades que lorsqu'aucune
        # inconnue scientifique n'est formulee.
        # --------------------------------------------

        if (
            top_role == "NOISE"
            and noise_score >= 0.40
            and not semantic_signals["lock_intent"]
            and not semantic_signals["method_only"]
            and not semantic_signals["result_only"]
            and not limitation_confirmed
        ):

            final_role = "NOISE"
            decision_reason = "nli_editorial_noise"


        elif (
            semantic_signals["method_only"]
            and (
                semantic_signals["measurement_method"]
                or semantic_signals["method_inventory"]
                or (
                    not semantic_signals["attributed_method_limitation"]
                    and method_score >= 0.16
                )
                or (
                    top_role == "METHOD_CONTEXT"
                    and method_score >= 0.30
                )
            )
        ):

            final_role = "METHOD_CONTEXT"
            decision_reason = "explicit_method_or_measurement_procedure_without_lock"


        elif (
            semantic_signals["result_only"]
        ):

            final_role = "SUPPORT_EVIDENCE"
            decision_reason = "explicit_observation_or_result_without_lock"


        elif (
            semantic_signals["lock_intent"]
            and lock_score >= 0.20
            and noise_score < 0.52
        ):

            final_role = "LOCK_CORE"
            decision_reason = "explicit_unresolved_question_gap_or_tradeoff"


        elif limitation_confirmed:

            final_role = "SUPPORT_EVIDENCE"
            decision_reason = "nli_confirmed_limitation_of_existing_method"


        elif (
            top_role == "METHOD_CONTEXT"
            and method_score >= 0.40
            and lock_score < 0.32
        ):

            final_role = (
                "METHOD_CONTEXT"
            )
            decision_reason = "nli_method_context"


        elif (
            top_role == "SUPPORT_EVIDENCE"
            and support_score >= 0.38
            and lock_score < 0.35
        ):

            final_role = (
                "SUPPORT_EVIDENCE"
            )
            decision_reason = "nli_support_evidence"


        elif (
            top_role == "LOCK_CORE"
            and lock_score >= 0.31
        ):

            final_role = "LOCK_CORE"
            decision_reason = "nli_lock_core"


        # Plusieurs seeds ind?pendants emp?chent
        # qu'un vrai probl?me soit perdu uniquement
        # ? cause d'une NLI h?sitante.
        elif (
            seeds >= 2
            and lock_score >= 0.25
            and noise_score < 0.45
            and method_score < 0.50
        ):

            final_role = "LOCK_CORE"
            decision_reason = "multi_seed_lock_preservation"


        else:

            final_role = "REVIEW"


        return {
            "role":
                final_role,

            "raw_top_role":
                top_role,

            "confidence":
                round(
                    float(top_score),
                    4,
                ),

            "margin":
                round(
                    float(
                        top_score
                        - second_score
                    ),
                    4,
                ),

            "scores": {
                key:
                    round(
                        float(value),
                        4,
                    )
                for key, value
                in scores.items()
            },

            "project_lock_seed_count":
                seeds,

            "decision_reason":
                decision_reason,

            "semantic_signals":
                semantic_signals,

            "limitation_role_scores": {
                key: round(float(value), 4)
                for key, value in limitation_role_scores.items()
            },
        }


    # --------------------------------------------------------
    # relation entre deux groupes
    # --------------------------------------------------------

    def compare_groups(
        self,
        left_profile: str,
        right_profile: str,
        cosine: float,
    ) -> Dict[str, Any]:

        premise = (
            "GROUP A:\n"
            + left_profile
            + "\n\nGROUP B:\n"
            + right_profile
        )

        relation_scores = (
            self._score_hypotheses(
                premise,
                PAIR_HYPOTHESES,
            )
        )

        directional = (
            self.judge
            ._predict_direction(
                [
                    left_profile,
                    right_profile,
                ],
                [
                    right_profile,
                    left_profile,
                ],
                batch_size=2,
            )
        )

        ab = directional[0]
        ba = directional[1]

        max_entail = max(
            ab.entailment,
            ba.entailment,
        )

        min_entail = min(
            ab.entailment,
            ba.entailment,
        )

        max_contradiction = max(
            ab.contradiction,
            ba.contradiction,
        )

        same_nli = float(
            relation_scores.get(
                "SAME_PARENT_LOCK",
                0.0,
            )
        )

        support_nli = float(
            relation_scores.get(
                "SUPPORTS_LOCK",
                0.0,
            )
        )

        distinct_nli = float(
            relation_scores.get(
                "DISTINCT_LOCK",
                0.0,
            )
        )

        ordered = sorted(
            relation_scores.items(),
            key=lambda item:
                item[1],
            reverse=True,
        )

        top_relation = ordered[0][0]

        left_signals = _semantic_role_signals(
            left_profile
        )

        right_signals = _semantic_role_signals(
            right_profile
        )

        # Zone de rattrapage limitee pour les formulations differentes d'une
        # meme question parent. Le cosinus ne suffit jamais : les deux textes
        # doivent exprimer une inconnue, le NLI SAME doit rester plausible et
        # DISTINCT ne doit avoir ni score ni marge forts.
        balanced_similarity_bridge = bool(
            left_signals["lock_intent"]
            and right_signals["lock_intent"]
            and cosine >= 0.62
            and same_nli >= 0.27
            and support_nli <= same_nli + 0.08
            and distinct_nli < 0.48
            and distinct_nli <= same_nli + 0.16
            and max_contradiction <= 0.40
        )


        # ====================================================
        # DISTINCT = VETO EXPLICITE
        #
        # Principe conservateur :
        # en cas de doute, on garde deux verrous séparés.
        # Le cosinus ne décide jamais d'une fusion.
        # ====================================================

        strong_distinct = bool(

            (
                top_relation
                == "DISTINCT_LOCK"

                and distinct_nli >= 0.42

                and distinct_nli
                    >= same_nli + 0.06

                and not balanced_similarity_bridge
            )

            or

            (
                distinct_nli >= 0.50

                and distinct_nli
                    >= same_nli + 0.12
            )

            or

            (
                cosine < 0.16
                and max_entail < 0.35
            )
        )


        # ====================================================
        # SAME PARENT
        #
        # Une forte implication dans UNE seule direction
        # n'est plus considérée comme "même verrou".
        # C'est généralement une relation de support,
        # conséquence ou sous-problème.
        #
        # Pour fusionner, le modèle doit explicitement préférer
        # SAME_PARENT_LOCK et cette décision ne doit pas être
        # concurrencée par SUPPORT/DISTINCT.
        # ====================================================

        strict_nli_same_parent = bool(
            top_relation
            == "SAME_PARENT_LOCK"

            and same_nli >= 0.48

            and same_nli
                >= support_nli + 0.06

            and same_nli
                >= distinct_nli + 0.08

            and max_contradiction <= 0.40

            and (
                cosine >= 0.28

                or min_entail >= 0.45

                or (
                    max_entail >= 0.72
                    and min_entail >= 0.30
                )
            )
        )

        same_parent = bool(
            not strong_distinct
            and (
                strict_nli_same_parent
                or balanced_similarity_bridge
            )
        )


        # ====================================================
        # SUPPORT
        #
        # SUPPORTS_LOCK ne provoque JAMAIS une fusion.
        # L'asymétrie d'implication est traitée ici,
        # et non plus comme SAME_PARENT_LOCK.
        # ====================================================

        asymmetric_support = bool(

            not same_parent

            and max_entail >= 0.72
            and min_entail <= 0.45
            and cosine >= 0.25
            and max_contradiction <= 0.40
        )

        support_relation = bool(

            not same_parent

            and not strong_distinct

            and (
                (
                    top_relation
                    == "SUPPORTS_LOCK"

                    and support_nli >= 0.38

                    and support_nli
                        >= distinct_nli
                )

                or asymmetric_support
            )
        )


        same_score = (

            0.55
            * same_nli

            + 0.20
            * float(
                min_entail
            )

            + 0.15
            * float(
                max_entail
            )

            + 0.10
            * max(
                0.0,
                float(cosine),
            )
        )


        support_score = (

            0.55
            * support_nli

            + 0.30
            * float(
                max_entail
            )

            + 0.15
            * max(
                0.0,
                float(cosine),
            )
        )


        return {

            "top_relation":
                top_relation,

            "relation_scores": {
                key:
                    round(
                        float(value),
                        4,
                    )
                for key, value
                in relation_scores.items()
            },

            "cosine":
                round(
                    float(cosine),
                    4,
                ),

            "entailment_ab":
                round(
                    float(
                        ab.entailment
                    ),
                    4,
                ),

            "entailment_ba":
                round(
                    float(
                        ba.entailment
                    ),
                    4,
                ),

            "max_entailment":
                round(
                    float(
                        max_entail
                    ),
                    4,
                ),

            "min_entailment":
                round(
                    float(
                        min_entail
                    ),
                    4,
                ),

            "max_contradiction":
                round(
                    float(
                        max_contradiction
                    ),
                    4,
                ),

            "same_parent":
                same_parent,

            "support_relation":
                support_relation,

            "asymmetric_support":
                asymmetric_support,

            "strict_nli_same_parent":
                strict_nli_same_parent,

            "balanced_similarity_bridge":
                balanced_similarity_bridge,

            "strong_distinct":
                strong_distinct,

            "merge_eligible":
                bool(
                    same_parent
                    and not strong_distinct
                ),

            "same_score":
                round(
                    float(
                        same_score
                    ),
                    4,
                ),

            "support_score":
                round(
                    float(
                        support_score
                    ),
                    4,
                ),
        }


# ============================================================
# MERGE
# ============================================================

def _merge_final_group(
    source_groups: Sequence[
        Mapping[str, Any]
    ],
    source_indexes: Sequence[int],
    profiles: Sequence[str],
    role_reports: Sequence[
        Mapping[str, Any]
    ],
    *,
    order: int,
    attached_supports: Optional[
        Sequence[
            Mapping[str, Any]
        ]
    ] = None,
) -> Dict[str, Any]:

    source_groups = [
        dict(group)
        for group in source_groups
    ]

    attached_supports = [
        dict(group)
        for group in (
            attached_supports
            or []
        )
    ]


    # groupe le plus clairement LOCK_CORE
    representative_position = max(
        range(
            len(source_groups)
        ),
        key=lambda local_index: (
            float(
                role_reports[
                    source_indexes[
                        local_index
                    ]
                ]
                .get(
                    "scores",
                    {}
                )
                .get(
                    "LOCK_CORE",
                    0.0,
                )
            ),
            _seed_count(
                source_groups[
                    local_index
                ]
            ),
        ),
    )

    representative = dict(
        source_groups[
            representative_position
        ]
    )

    representative_global_index = (
        source_indexes[
            representative_position
        ]
    )


    # --------------------------------------------
    # passages d?dupliqu?s
    # --------------------------------------------

    all_input_groups = (
        list(source_groups)
        + list(attached_supports)
    )

    passage_map: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for group in all_input_groups:

        for passage in (
            group.get(
                "supporting_passages"
            )
            or []
        ):

            if not isinstance(
                passage,
                Mapping,
            ):
                continue

            passage_map[
                _passage_key(
                    passage
                )
            ] = dict(
                passage
            )


    passages = list(
        passage_map.values()
    )


    # --------------------------------------------
    # documents
    # --------------------------------------------

    document_counts: Dict[
        str,
        int
    ] = {}

    for passage in passages:

        document = str(
            passage.get(
                "document"
            )
            or ""
        ).strip()

        if not document:
            continue

        document_counts[
            document
        ] = (
            document_counts.get(
                document,
                0,
            )
            + 1
        )


    source_ids = [
        str(
            group.get(
                "lock_group_id"
            )
            or group.get(
                "passage_id"
            )
            or ""
        )
        for group in source_groups
    ]


    support_ids = [
        str(
            group.get(
                "lock_group_id"
            )
            or group.get(
                "passage_id"
            )
            or ""
        )
        for group in attached_supports
    ]


    digest = hashlib.sha1(
        "|".join(
            sorted(
                source_ids
            )
        ).encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()[:16]


    project_seed_count = sum(
        _seed_count(
            group
        )
        for group in source_groups
    )


    attached_support_seed_count = sum(
        _seed_count(
            group
        )
        for group in attached_supports
    )


    item = dict(
        representative
    )

    item.update(
        {
            "passage_id":
                f"final_lock_{digest}",

            "lock_group_id":
                f"final_lock_{order:03d}_{digest}",

            "technical_scope":
                "project_structuring_lock",

            "display_as_main_lock":
                True,

            "derived_view":
                "v190_role_classifier_only_complete_linkage_unchanged",

            "semantic_finalizer_version":
                VERSION,

            "text":
                profiles[
                    representative_global_index
                ],

            "analysis_text":
                "\n".join(
                    profiles[index]
                    for index
                    in source_indexes
                )[:5000],

            "supporting_passages":
                passages,

            "supporting_documents": [
                {
                    "document":
                        document,

                    "passage_count":
                        count,
                }
                for document, count
                in sorted(
                    document_counts.items()
                )
            ],

            "evidence_count":
                len(passages),

            "project_lock_seed_count":
                project_seed_count,

            "direct_candidate_count":
                project_seed_count,

            "structuring_seed_count":
                project_seed_count,

            "attached_support_seed_count":
                attached_support_seed_count,

            "source_group_ids":
                source_ids,

            "attached_support_group_ids":
                support_ids,

            "conceptual_group_count":
                len(
                    source_groups
                ),

            "needs_human_validation":
                True,
        }
    )

    return item


# ============================================================
# PUBLIC API
# ============================================================

def finalize_lock_groups(
    groups: Sequence[
        Mapping[str, Any]
    ],
    *,
    encode_texts: Optional[
        Callable[
            [List[str]],
            Sequence[
                Sequence[float]
            ],
        ]
    ] = None,
) -> Dict[str, Any]:

    groups = [
        dict(group)
        for group in groups
        if isinstance(
            group,
            Mapping,
        )
    ]

    if not groups:

        return {
            "version":
                VERSION,

            "groups":
                [],

            "main_groups":
                [],

            "secondary_groups":
                [],

            "audit":
                {
                    "input_groups_count": 0,
                    "final_main_groups_count": 0,
                },
        }


    finalizer = (
        SemanticLockFinalizer()
    )


    # ========================================================
    # 1. PROFILES
    # ========================================================

    profiles = [
        build_problem_profile(
            group
        )
        for group in groups
    ]


    # ========================================================
    # 2. EMBEDDINGS
    # ========================================================

    vectors = []

    if encode_texts is not None:

        try:

            vectors = list(
                encode_texts(
                    profiles
                )
            )

        except Exception:

            vectors = []


    # ========================================================
    # 3. ROLE OF EACH GROUP
    # ========================================================

    role_reports = [
        finalizer.classify_group(
            profile,
            group,
        )
        for profile, group
        in zip(
            profiles,
            groups,
        )
    ]


    # ========================================================
    # 4. PAIR MATRIX
    # ========================================================

    pair_reports: Dict[
        Tuple[int, int],
        Dict[str, Any]
    ] = {}


    for left in range(
        len(groups)
    ):

        for right in range(
            left + 1,
            len(groups),
        ):

            cosine = 0.0

            if (
                vectors
                and left < len(vectors)
                and right < len(vectors)
            ):

                cosine = _cosine(
                    vectors[left],
                    vectors[right],
                )


            pair_reports[
                (
                    left,
                    right,
                )
            ] = (
                finalizer.compare_groups(
                    profiles[left],
                    profiles[right],
                    cosine,
                )
            )


    def pair(
        left: int,
        right: int,
    ) -> Dict[str, Any]:

        if left == right:

            return {
                "same_parent":
                    True,

                "support_relation":
                    False,

                "strong_distinct":
                    False,

                "same_score":
                    1.0,

                "support_score":
                    0.0,
            }

        key = (
            min(left, right),
            max(left, right),
        )

        return pair_reports[
            key
        ]


    # ========================================================
    # 5. CORE CANDIDATES
    # ========================================================

    core_indexes = [
        index
        for index, report
        in enumerate(
            role_reports
        )
        if report.get(
            "role"
        )
        == "LOCK_CORE"
    ]


    # ========================================================
    # 6. SINGLETON PRESERVATION
    #
    # Un seul seed n'est PAS une raison suffisante pour
    # rétrograder un LOCK_CORE en SUPPORT_EVIDENCE.
    #
    # La rareté documentaire doit être exposée dans l'audit,
    # mais le rôle sémantique reste indépendant du volume
    # de preuves. Cela évite de perdre un verrou rare mais
    # explicitement formulé.
    # ========================================================

    demoted_singletons = set()

    for index in core_indexes:

        if _seed_count(
            groups[index]
        ) == 1:

            role_reports[index][
                "singleton_core"
            ] = True

            role_reports[index][
                "documentary_support"
            ] = "limited"

    # ========================================================
    # 7. UNION FIND FOR SAME PARENT LOCKS
    #
    # Avec garde anti-cha?nage :
    # jamais de fusion si une relation DISTINCT forte
    # existe entre les deux composantes.
    # ========================================================

    parent = {
        index: index
        for index in core_indexes
    }


    def find(
        value: int,
    ) -> int:

        while (
            parent[value]
            != value
        ):

            parent[value] = parent[
                parent[value]
            ]

            value = parent[value]

        return value


    def members(
        root: int,
    ) -> List[int]:

        return [
            index
            for index in core_indexes
            if find(index) == root
        ]


    def can_union(
        left: int,
        right: int,
    ) -> bool:

        left_root = find(
            left
        )

        right_root = find(
            right
        )

        if left_root == right_root:
            return False

        left_members = members(
            left_root
        )

        right_members = members(
            right_root
        )


        # ====================================================
        # COMPLETE LINKAGE CONSERVATEUR
        #
        # Pour fusionner deux composantes, CHAQUE paire
        # croisée doit être SAME_PARENT_LOCK.
        #
        # Donc :
        # A SAME B et B SAME C ne suffit plus pour fusionner
        # A+B+C si A et C ne sont pas eux-mêmes SAME.
        #
        # Toute relation DISTINCT bloque immédiatement.
        # SUPPORTS_LOCK ne permet jamais la fusion.
        # ====================================================

        for a in left_members:

            for b in right_members:

                relation = pair(
                    a,
                    b,
                )

                if relation.get(
                    "strong_distinct"
                ):

                    return False

                if not relation.get(
                    "same_parent"
                ):

                    return False

        return True


    same_edges = []

    for position, left in enumerate(
        core_indexes
    ):

        for right in core_indexes[
            position + 1:
        ]:

            relation = pair(
                left,
                right,
            )

            if relation.get(
                "same_parent"
            ):

                same_edges.append(
                    (
                        float(
                            relation.get(
                                "same_score",
                                0.0,
                            )
                        ),
                        left,
                        right,
                    )
                )


    same_edges.sort(
        reverse=True
    )


    for _, left, right in same_edges:

        if not can_union(
            left,
            right,
        ):
            continue

        root_left = find(
            left
        )

        root_right = find(
            right
        )

        if root_left != root_right:

            parent[
                root_right
            ] = root_left


    # ========================================================
    # 8. COMPONENTS
    # ========================================================

    components: Dict[
        int,
        List[int]
    ] = {}

    for index in core_indexes:

        root = find(
            index
        )

        components.setdefault(
            root,
            []
        ).append(
            index
        )


    component_list = list(
        components.values()
    )


    # ========================================================
    # 9. ATTACH SUPPORT GROUPS
    # ========================================================

    non_core_indexes = [
        index
        for index, report
        in enumerate(
            role_reports
        )
        if report.get(
            "role"
        )
        != "LOCK_CORE"
    ]


    support_assignment: Dict[
        int,
        List[int]
    ] = {
        component_index: []
        for component_index
        in range(
            len(component_list)
        )
    }

    unassigned = []


    for index in non_core_indexes:

        role = role_reports[
            index
        ].get(
            "role"
        )

        if role == "NOISE":
            unassigned.append(
                index
            )
            continue

        best_component = None
        best_score = 0.0


        for component_index, component in enumerate(
            component_list
        ):

            component_score = 0.0
            related = False

            for core_index in component:

                relation = pair(
                    index,
                    core_index,
                )

                if (
                    relation.get(
                        "support_relation"
                    )
                    or relation.get(
                        "same_parent"
                    )
                ):

                    related = True

                    component_score = max(
                        component_score,
                        float(
                            relation.get(
                                "support_score",
                                0.0,
                            )
                        ),
                        float(
                            relation.get(
                                "same_score",
                                0.0,
                            )
                        ),
                    )


            if (
                related
                and component_score
                    > best_score
            ):

                best_score = (
                    component_score
                )

                best_component = (
                    component_index
                )


        if best_component is None:

            unassigned.append(
                index
            )

        else:

            support_assignment[
                best_component
            ].append(
                index
            )

            role_reports[index][
                "attached_to_component"
            ] = best_component

            role_reports[index][
                "attachment_score"
            ] = round(
                best_score,
                4,
            )


    # ========================================================
    # 10. FINAL MAIN LOCKS
    # ========================================================

    final_main_groups = []


    for order, component in enumerate(
        component_list,
        1,
    ):

        support_indexes = (
            support_assignment.get(
                order - 1,
                [],
            )
        )

        final_group = (
            _merge_final_group(
                [
                    groups[index]
                    for index
                    in component
                ],
                component,
                profiles,
                role_reports,
                order=order,
                attached_supports=[
                    groups[index]
                    for index
                    in support_indexes
                ],
            )
        )

        final_group[
            "semantic_component_members"
        ] = component

        final_group[
            "semantic_support_members"
        ] = support_indexes

        final_main_groups.append(
            final_group
        )


    # ========================================================
    # 11. UNASSIGNED SECONDARY / REVIEW
    # ========================================================

    secondary_groups = []

    for index in unassigned:

        item = dict(
            groups[index]
        )

        role = role_reports[
            index
        ].get(
            "role"
        )

        item[
            "display_as_main_lock"
        ] = False

        item[
            "semantic_finalizer_role"
        ] = role

        item[
            "semantic_finalizer_version"
        ] = VERSION

        if role == "REVIEW":

            item[
                "technical_scope"
            ] = "lock_to_validate"

        else:

            item[
                "technical_scope"
            ] = "local_technical_subproblem"

        secondary_groups.append(
            item
        )


    # ========================================================
    # 12. AUDIT
    # ========================================================

    audit_pairs = []

    for (
        left,
        right,
    ), report in sorted(
        pair_reports.items()
    ):

        audit_pairs.append(
            {
                "left_index":
                    left,

                "right_index":
                    right,

                **report,
            }
        )


    return {

        "version":
            VERSION,

        "groups":
            (
                final_main_groups
                + secondary_groups
            ),

        "main_groups":
            final_main_groups,

        "secondary_groups":
            secondary_groups,

        "audit": {

            "input_groups_count":
                len(groups),

            "profiles":
                profiles,

            "role_reports":
                role_reports,

            "pair_reports":
                audit_pairs,

            "initial_core_count":
                sum(
                    1
                    for report
                    in role_reports
                    if report.get(
                        "raw_top_role"
                    )
                    == "LOCK_CORE"
                ),

            "demoted_singletons":
                sorted(
                    demoted_singletons
                ),

            "core_groups_after_role_analysis":
                len(
                    core_indexes
                ),

            "conceptual_components":
                component_list,

            "final_main_groups_count":
                len(
                    final_main_groups
                ),

            "secondary_groups_count":
                len(
                    secondary_groups
                ),

            "singleton_demotion_enabled":
                False,

            "merge_policy":
                "complete_linkage_all_pairs_same_parent_with_balanced_similarity_bridge",

            "cosine_can_merge_alone":
                False,

            "support_relation_can_merge":
                False,

            "distinct_relation_is_merge_veto":
                True,

            "hardcoded_project_profiles":
                False,

            "hardcoded_target_group_count":
                False,

            "nli_model_source":
                getattr(
                    finalizer.judge,
                    "model_source",
                    None,
                ),

            "nli_local_files_only":
                bool(
                    getattr(
                        finalizer.judge,
                        "local_files_only",
                        False,
                    )
                ),
        },
    }
