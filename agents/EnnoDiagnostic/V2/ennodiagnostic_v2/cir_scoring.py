from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional, Tuple

from .schemas import CanonicalEntity, ExtractionItem
from .rnd_approach import RnDApproachAssessment
from .project_frascati import ProjectFrascatiAssessment
from .project_synthesis import ProjectSynthesis


# ============================================================
# MODELES
# ============================================================


@dataclass
class ScoreComponent:
    name: str
    score: float
    max_score: float
    explanation: str = ""


@dataclass
class AxisDiagnosticScore:
    score: float
    raw_score: float
    level: str
    explanation: str = ""
    components: List[ScoreComponent] = field(
        default_factory=list
    )
    caps_applied: List[str] = field(
        default_factory=list
    )


@dataclass
class CIRDiagnosticScore:
    eligibility: AxisDiagnosticScore
    defensibility: AxisDiagnosticScore

    # Compatibilité avec l'ancienne interface.
    risk_level: str
    potential_level: str

    strengths: List[str] = field(
        default_factory=list
    )
    weaknesses: List[str] = field(
        default_factory=list
    )

    summary: str = ""

    @property
    def score(self) -> float:
        # Compatibilité temporaire :
        # l'ancien score = score d'éligibilité.
        return self.eligibility.score

    @property
    def raw_score(self) -> float:
        return self.eligibility.raw_score

    @property
    def components(self) -> List[ScoreComponent]:
        return (
            list(
                self.eligibility.components
            )
            + list(
                self.defensibility.components
            )
        )

    @property
    def caps_applied(self) -> List[str]:
        return (
            list(
                self.eligibility.caps_applied
            )
            + list(
                self.defensibility.caps_applied
            )
        )

    def to_dict(self):
        data = asdict(self)

        # Anciennes clés conservées pendant
        # la migration backend/frontend.
        data["score"] = self.score
        data["raw_score"] = (
            self.raw_score
        )
        data["components"] = [
            asdict(component)
            for component
            in self.components
        ]
        data["caps_applied"] = list(
            self.caps_applied
        )

        return data


# ============================================================
# CONSTANTES
# ============================================================


LOCK_FACTORS = {
    "VALID_LOCK": 1.00,
    "NEEDS_REFORMULATION": 0.70,
    "INSUFFICIENT_EVIDENCE": 0.35,
    "ENGINEERING_DIFFICULTY": 0.10,
    "REQUIREMENT_ONLY": 0.00,
    "NOT_EVALUATED": 0.20,
}


SUPPORT_FACTORS = {
    "strong": 1.00,
    "partial": 0.60,
    "weak": 0.20,
    "unknown": 0.00,
}


DIMENSION_FACTORS = {
    "present": 1.00,
    "partial": 0.50,
    "absent": 0.00,
    "unknown": 0.00,
}


FRASCATI_POINTS = {
    "supported": 5.0,
    "partial": 2.5,
    "insufficient": 0.0,
}


CREDIBLE_LOCK_STATUSES = {
    "VALID_LOCK",
    "NEEDS_REFORMULATION",
}


# ============================================================
# HELPERS
# ============================================================


def _round(
    value: float,
) -> float:
    return round(
        float(value),
        1,
    )


def _risk_level(
    score: float,
) -> str:
    if score >= 75:
        return "Faible"
    if score >= 60:
        return "Modéré"
    if score >= 40:
        return "Élevé"
    return "Très élevé"


def _potential_level(
    score: float,
) -> str:
    if score >= 75:
        return "Fort potentiel CIR"
    if score >= 60:
        return "Potentiel CIR à consolider"
    if score >= 40:
        return "Éligibilité incertaine"
    return "Potentiel CIR faible"


def _defensibility_level(
    score: float,
) -> str:
    if score >= 75:
        return "Dossier bien défendable"
    if score >= 60:
        return "Dossier défendable à consolider"
    if score >= 40:
        return "Défendabilité fragile"
    return "Défendabilité faible"


def _metadata_dict(
    obj,
) -> dict:
    metadata = getattr(
        obj,
        "metadata",
        None,
    )

    return (
        metadata
        if isinstance(
            metadata,
            dict,
        )
        else {}
    )


def _project_locks(
    entities: Iterable[CanonicalEntity],
) -> Tuple[
    List[CanonicalEntity],
    int,
]:
    all_locks = [
        entity
        for entity in entities
        if entity.kind == "lock"
    ]

    if not all_locks:
        return [], 0

    provenance_available = any(
        (
            "has_project_evidence"
            in _metadata_dict(lock)
            or "source_roles"
            in _metadata_dict(lock)
        )
        for lock in all_locks
    )

    if not provenance_available:
        return all_locks, 0

    project_locks = [
        lock
        for lock in all_locks
        if _metadata_dict(lock).get(
            "has_project_evidence"
        ) is True
    ]

    return (
        project_locks,
        len(all_locks)
        - len(project_locks),
    )


def _project_extractions(
    extractions: Iterable[ExtractionItem],
) -> Tuple[
    List[ExtractionItem],
    int,
]:
    items = list(
        extractions
    )

    if not items:
        return [], 0

    provenance_available = any(
        str(
            _metadata_dict(item).get(
                "source_role"
            ) or ""
        ).strip()
        for item in items
    )

    if not provenance_available:
        return items, 0

    project_items = [
        item
        for item in items
        if str(
            _metadata_dict(item).get(
                "source_role"
            ) or ""
        ).strip().lower()
        == "project_evidence"
    ]

    return (
        project_items,
        len(items)
        - len(project_items),
    )


# ============================================================
# ELIGIBILITE : 100 POINTS
#
# 1. Contexte / pertinence R&D : 30
# 2. Verrous / incertitudes : 50
# 3. Ecart aux solutions existantes : 20
# ============================================================


def _score_rnd_context(
    synthesis: Optional[
        ProjectSynthesis
    ],
) -> ScoreComponent:

    if synthesis is None:
        return ScoreComponent(
            name="Contexte / pertinence R&D",
            score=0.0,
            max_score=30.0,
            explanation=(
                "Synthèse globale du projet indisponible."
            ),
        )

    status = str(
        synthesis.rnd_relevance_status
        or "unknown"
    ).strip().lower()

    factor = SUPPORT_FACTORS.get(
        status,
        0.0,
    )

    score = 30.0 * factor

    reason = str(
        synthesis.rnd_relevance_reason
        or ""
    ).strip()

    if not reason:
        reason = {
            "strong":
                "Le contexte décrit une ambition technique de R&D clairement identifiable.",
            "partial":
                "La dimension R&D est crédible mais reste partiellement explicitée.",
            "weak":
                "Le contexte relève surtout d'un objectif fonctionnel ou d'ingénierie.",
            "unknown":
                "Les informations disponibles ne permettent pas de caractériser le contexte R&D.",
        }.get(
            status,
            "Contexte R&D non caractérisé.",
        )

    return ScoreComponent(
        name="Contexte / pertinence R&D",
        score=_round(score),
        max_score=30.0,
        explanation=reason,
    )


def _score_locks(
    entities: Iterable[
        CanonicalEntity
    ],
) -> Tuple[
    ScoreComponent,
    bool,
    bool,
]:

    (
        locks,
        excluded_non_project,
    ) = _project_locks(
        entities
    )

    if not locks:
        extra = (
            f" {excluded_non_project} candidat(s) hors preuve projet sont conservés hors calcul."
            if excluded_non_project
            else ""
        )

        return (
            ScoreComponent(
                name="Verrous / incertitudes",
                score=0.0,
                max_score=50.0,
                explanation=(
                    "Aucun verrou technique directement rattaché au projet n'a été identifié."
                    + extra
                ),
            ),
            False,
            False,
        )

    factors = []
    statuses = []

    for lock in locks:
        status = str(
            _metadata_dict(lock).get(
                "qualification_status",
                "NOT_EVALUATED",
            )
        ).strip().upper()

        statuses.append(
            status
        )

        factors.append(
            LOCK_FACTORS.get(
                status,
                0.20,
            )
        )

    best = max(
        factors
    )

    average = (
        sum(factors)
        / len(factors)
    )

    # Même philosophie que l'ancien score :
    # un projet peut reposer sur un verrou central fort.
    factor = (
        0.75 * best
        + 0.25 * average
    )

    score = (
        50.0 * factor
    )

    has_valid = (
        "VALID_LOCK"
        in statuses
    )

    has_credible = any(
        status
        in CREDIBLE_LOCK_STATUSES
        for status
        in statuses
    )

    count_valid = statuses.count(
        "VALID_LOCK"
    )

    count_reformulation = (
        statuses.count(
            "NEEDS_REFORMULATION"
        )
    )

    explanation = (
        f"{len(locks)} candidat(s) projet analysé(s) : "
        f"{count_valid} verrou(x) validé(s), "
        f"{count_reformulation} à reformuler."
    )

    if excluded_non_project:
        explanation += (
            f" {excluded_non_project} candidat(s) littérature/autres sont conservés hors calcul."
        )

    return (
        ScoreComponent(
            name="Verrous / incertitudes",
            score=_round(score),
            max_score=50.0,
            explanation=explanation,
        ),
        has_valid,
        has_credible,
    )


def _score_existing_gap(
    synthesis: Optional[
        ProjectSynthesis
    ],
) -> ScoreComponent:

    if synthesis is None:
        return ScoreComponent(
            name="Écart aux solutions existantes",
            score=0.0,
            max_score=20.0,
            explanation=(
                "Synthèse globale du projet indisponible."
            ),
        )

    status = str(
        synthesis.existing_solutions_gap_status
        or "unknown"
    ).strip().lower()

    factor = SUPPORT_FACTORS.get(
        status,
        0.0,
    )

    score = (
        20.0 * factor
    )

    reason = str(
        synthesis.existing_solutions_gap_reason
        or ""
    ).strip()

    if not reason:
        reason = {
            "strong":
                "Le dossier décrit une limite concrète des solutions disponibles dans le contexte du projet.",
            "partial":
                "Des limites sont identifiées, mais leur lien avec le projet reste incomplet.",
            "weak":
                "Le dossier n'établit pas de limite technique claire des solutions existantes.",
            "unknown":
                "Les informations disponibles ne permettent pas de caractériser l'écart à l'existant.",
        }.get(
            status,
            "Écart à l'existant non caractérisé.",
        )

    return ScoreComponent(
        name="Écart aux solutions existantes",
        score=_round(score),
        max_score=20.0,
        explanation=reason,
    )


# ============================================================
# DEFENDABILITE : 70 POINTS INTERNES NORMALISES /100
#
# 1. Démarche R&D : 30
# 2. Frascati : 25
# 3. Qualité des preuves : 15
# ============================================================


def _score_rnd(
    rnd: RnDApproachAssessment,
) -> ScoreComponent:

    dimensions = [
        rnd.hypothesis_status,
        rnd.experimentation_status,
        rnd.parameter_study_status,
        rnd.iteration_status,
        rnd.failure_learning_status,
        rnd.result_analysis_status,
        rnd.knowledge_gain_status,
    ]

    factor = (
        sum(
            DIMENSION_FACTORS.get(
                str(value).lower(),
                0.0,
            )
            for value
            in dimensions
        )
        / len(dimensions)
    )

    score = (
        30.0 * factor
    )

    status_caps = {
        "RESEARCH_APPROACH": 30.0,
        "MIXED_APPROACH": 22.0,
        "ENGINEERING_ONLY": 6.0,
        "INSUFFICIENT_EVIDENCE": 12.0,
    }

    score = min(
        score,
        status_caps.get(
            str(
                rnd.status
            ).upper(),
            12.0,
        ),
    )

    explanation_map = {
        "RESEARCH_APPROACH":
            "La démarche présente les caractéristiques d'une activité expérimentale de R&D.",
        "MIXED_APPROACH":
            "Le projet combine une démarche expérimentale et des travaux d'ingénierie classique.",
        "ENGINEERING_ONLY":
            "Les travaux décrits relèvent principalement de l'ingénierie classique.",
        "INSUFFICIENT_EVIDENCE":
            "Les documents ne permettent pas de démontrer suffisamment la démarche R&D.",
    }

    return ScoreComponent(
        name="Démarche R&D",
        score=_round(score),
        max_score=30.0,
        explanation=(
            explanation_map.get(
                str(
                    rnd.status
                ).upper(),
                "La démarche R&D reste insuffisamment caractérisée.",
            )
        ),
    )


def _score_frascati(
    frascati: ProjectFrascatiAssessment,
) -> Tuple[
    ScoreComponent,
    dict,
]:

    statuses = {}

    for criterion in (
        frascati.criteria
    ):
        name = str(
            criterion.criterion
        ).strip().lower()

        status = str(
            criterion.status
        ).strip().lower()

        statuses[
            name
        ] = status

    expected = [
        "uncertainty",
        "novelty",
        "creativity",
        "systematic",
        "transferability",
    ]

    score = 0.0

    for criterion in expected:
        score += (
            FRASCATI_POINTS.get(
                statuses.get(
                    criterion,
                    "insufficient",
                ),
                0.0,
            )
        )

    supported = sum(
        1
        for name
        in expected
        if statuses.get(
            name
        ) == "supported"
    )

    partial = sum(
        1
        for name
        in expected
        if statuses.get(
            name
        ) == "partial"
    )

    return (
        ScoreComponent(
            name="Critères Frascati",
            score=_round(score),
            max_score=25.0,
            explanation=(
                f"{supported}/5 critère(s) fortement soutenu(s), "
                f"{partial}/5 partiellement soutenu(s)."
            ),
        ),
        statuses,
    )


def _score_evidence(
    extractions: Iterable[
        ExtractionItem
    ],
) -> ScoreComponent:

    (
        items,
        excluded_non_project,
    ) = _project_extractions(
        extractions
    )

    if not items:
        extra = (
            f" {excluded_non_project} élément(s) littérature/contexte sont conservés hors calcul."
            if excluded_non_project
            else ""
        )

        return ScoreComponent(
            name="Qualité des preuves",
            score=0.0,
            max_score=15.0,
            explanation=(
                "Aucune preuve projet exploitable n'est disponible."
                + extra
            ),
        )

    verified = sum(
        1
        for item
        in items
        if item.evidence.quote_verified
    )

    verified_points = (
        5.0
        * (
            verified
            / len(items)
        )
    )

    kinds = {
        item.kind
        for item
        in items
    }

    relevant_kinds = {
        "objective",
        "lock",
        "method",
        "parameter",
        "result",
    }

    coverage_points = float(
        len(
            kinds.intersection(
                relevant_kinds
            )
        )
    )

    if (
        "method" in kinds
        and "result" in kinds
    ):
        chain_points = 2.0

    elif (
        "method" in kinds
        or "result" in kinds
    ):
        chain_points = 1.0

    else:
        chain_points = 0.0

    parameter_points = (
        1.0
        if "parameter" in kinds
        else 0.0
    )

    result_count = sum(
        1
        for item
        in items
        if item.kind
        == "result"
    )

    multiple_results_points = (
        1.0
        if result_count >= 2
        else 0.0
    )

    evidence_ids = {
        item.evidence.evidence_id
        for item
        in items
        if item.evidence.evidence_id
    }

    traceability_points = (
        1.0
        if len(
            evidence_ids
        ) >= 3
        else 0.0
    )

    score = min(
        (
            verified_points
            + coverage_points
            + chain_points
            + parameter_points
            + multiple_results_points
            + traceability_points
        ),
        15.0,
    )

    explanation = (
        f"{verified}/{len(items)} preuve(s) projet avec citation vérifiée ; "
        f"{len(kinds.intersection(relevant_kinds))}/5 type(s) "
        f"d'information technique couvert(s)."
    )

    if excluded_non_project:
        explanation += (
            f" {excluded_non_project} élément(s) littérature/contexte sont conservés hors calcul."
        )

    return ScoreComponent(
        name="Qualité des preuves",
        score=_round(score),
        max_score=15.0,
        explanation=explanation,
    )


# ============================================================
# EXPLICATIONS COURTES FRONTEND
# ============================================================


def _eligibility_explanation(
    score: float,
    components: List[
        ScoreComponent
    ],
) -> str:

    if score >= 75:
        lead = (
            "Le projet présente une problématique technique de R&D crédible, "
            "des verrous significatifs et un écart identifiable aux solutions disponibles."
        )
    elif score >= 60:
        lead = (
            "Le projet présente un potentiel CIR réel, mais certains éléments "
            "du contexte, des verrous ou de l'écart à l'existant restent à consolider."
        )
    elif score >= 40:
        lead = (
            "Une problématique technique est identifiable, mais son caractère R&D "
            "ou l'insuffisance des solutions existantes reste encore partiellement démontré."
        )
    else:
        lead = (
            "Les éléments disponibles ne montrent pas encore suffisamment "
            "une problématique R&D et des verrous techniques crédibles."
        )

    weakest_ratio, weakest = min(
        [
            (
                (
                    component.score
                    / component.max_score
                )
                if component.max_score
                else 0.0,
                component,
            )
            for component
            in components
        ],
        key=lambda value: value[0],
    )

    if weakest_ratio < 0.60:
        return (
            f"{lead} Point principal à renforcer : "
            f"{weakest.name.lower()}."
        )

    return lead


def _defensibility_explanation(
    score: float,
    components: List[
        ScoreComponent
    ],
) -> str:

    if score >= 75:
        lead = (
            "Le dossier est bien étayé : la démarche R&D, les critères "
            "Frascati et les preuves documentaires sont globalement solides."
        )
    elif score >= 60:
        lead = (
            "Le dossier est défendable, mais certains éléments doivent "
            "encore être consolidés."
        )
    elif score >= 40:
        lead = (
            "La défendabilité reste fragile : plusieurs éléments de démarche, "
            "de preuve ou de justification Frascati sont incomplets."
        )
    else:
        lead = (
            "Le dossier est peu défendable en l'état : la démarche R&D "
            "et les preuves doivent être davantage documentées."
        )

    weakest_ratio, weakest = min(
        [
            (
                (
                    component.score
                    / component.max_score
                )
                if component.max_score
                else 0.0,
                component,
            )
            for component
            in components
        ],
        key=lambda value: value[0],
    )

    if weakest_ratio < 0.70:
        return (
            f"{lead} Point principal à renforcer : "
            f"{weakest.name.lower()}."
        )

    return lead


# ============================================================
# DOUBLE SCORE
# ============================================================


class CIRScorer:

    def score(
        self,
        *,
        entities: Iterable[
            CanonicalEntity
        ],
        extractions: Iterable[
            ExtractionItem
        ],
        rnd: RnDApproachAssessment,
        frascati: ProjectFrascatiAssessment,
        synthesis: Optional[
            ProjectSynthesis
        ] = None,
    ) -> CIRDiagnosticScore:

        entities = list(
            entities
        )
        extractions = list(
            extractions
        )

        # ----------------------------------------------------
        # A. ELIGIBILITE
        # ----------------------------------------------------

        context_component = (
            _score_rnd_context(
                synthesis
            )
        )

        (
            locks_component,
            has_valid_lock,
            has_credible_lock,
        ) = _score_locks(
            entities
        )

        gap_component = (
            _score_existing_gap(
                synthesis
            )
        )

        eligibility_components = [
            context_component,
            locks_component,
            gap_component,
        ]

        eligibility_raw = sum(
            component.score
            for component
            in eligibility_components
        )

        eligibility_caps = []
        cap_candidates = []

        project_locks, _ = (
            _project_locks(
                entities
            )
        )

        if synthesis is None:
            cap_candidates.append(
                60.0
            )
            eligibility_caps.append(
                "Éligibilité plafonnée à 60 : synthèse globale du projet indisponible."
            )

        if not project_locks:
            cap_candidates.append(
                50.0
            )
            eligibility_caps.append(
                "Éligibilité plafonnée à 50 : aucun verrou directement rattaché au projet."
            )

        elif not has_credible_lock:
            cap_candidates.append(
                60.0
            )
            eligibility_caps.append(
                "Éligibilité plafonnée à 60 : aucun verrou validé ou à reformuler."
            )

        if (
            synthesis is not None
            and str(
                synthesis.rnd_relevance_status
            ).strip().lower()
            == "weak"
        ):
            cap_candidates.append(
                55.0
            )
            eligibility_caps.append(
                "Éligibilité plafonnée à 55 : le contexte décrit principalement une activité d'ingénierie ou un objectif fonctionnel."
            )

        eligibility_score = (
            eligibility_raw
        )

        if cap_candidates:
            eligibility_score = min(
                eligibility_score,
                min(
                    cap_candidates
                ),
            )

        eligibility_score = _round(
            max(
                0.0,
                min(
                    eligibility_score,
                    100.0,
                ),
            )
        )

        eligibility_raw = _round(
            eligibility_raw
        )

        eligibility = (
            AxisDiagnosticScore(
                score=eligibility_score,
                raw_score=eligibility_raw,
                level=_potential_level(
                    eligibility_score
                ),
                explanation=(
                    _eligibility_explanation(
                        eligibility_score,
                        eligibility_components,
                    )
                ),
                components=(
                    eligibility_components
                ),
                caps_applied=(
                    eligibility_caps
                ),
            )
        )

        # ----------------------------------------------------
        # B. DEFENDABILITE
        # ----------------------------------------------------

        rnd_component = (
            _score_rnd(
                rnd
            )
        )

        (
            frascati_component,
            frascati_statuses,
        ) = _score_frascati(
            frascati
        )

        evidence_component = (
            _score_evidence(
                extractions
            )
        )

        defensibility_components = [
            rnd_component,
            frascati_component,
            evidence_component,
        ]

        defensibility_raw_points = sum(
            component.score
            for component
            in defensibility_components
        )

        defensibility_raw = (
            defensibility_raw_points
            / 70.0
            * 100.0
        )

        defensibility_caps = []
        cap_candidates = []

        rnd_status = str(
            rnd.status
        ).upper()

        if (
            rnd_status
            == "ENGINEERING_ONLY"
        ):
            cap_candidates.append(
                40.0
            )
            defensibility_caps.append(
                "Défendabilité plafonnée à 40 : la démarche relève principalement de l'ingénierie classique."
            )

        elif (
            rnd_status
            == "INSUFFICIENT_EVIDENCE"
        ):
            cap_candidates.append(
                60.0
            )
            defensibility_caps.append(
                "Défendabilité plafonnée à 60 : la démarche R&D n'est pas suffisamment démontrée."
            )

        uncertainty = (
            frascati_statuses.get(
                "uncertainty",
                "insufficient",
            )
        )

        systematic = (
            frascati_statuses.get(
                "systematic",
                "insufficient",
            )
        )

        if (
            uncertainty
            == "insufficient"
        ):
            cap_candidates.append(
                55.0
            )
            defensibility_caps.append(
                "Défendabilité plafonnée à 55 : l'incertitude technique n'est pas suffisamment démontrée."
            )

        if (
            uncertainty
            == "insufficient"
            and systematic
            == "insufficient"
        ):
            cap_candidates.append(
                40.0
            )
            defensibility_caps.append(
                "Défendabilité plafonnée à 40 : ni l'incertitude ni la démarche systématique ne sont démontrées."
            )

        insufficient_count = sum(
            1
            for status
            in frascati_statuses.values()
            if status
            == "insufficient"
        )

        if (
            insufficient_count
            >= 4
        ):
            cap_candidates.append(
                45.0
            )
            defensibility_caps.append(
                "Défendabilité plafonnée à 45 : au moins quatre critères Frascati sont insuffisamment démontrés."
            )

        defensibility_score = (
            defensibility_raw
        )

        if cap_candidates:
            defensibility_score = min(
                defensibility_score,
                min(
                    cap_candidates
                ),
            )

        defensibility_score = _round(
            max(
                0.0,
                min(
                    defensibility_score,
                    100.0,
                ),
            )
        )

        defensibility_raw = _round(
            defensibility_raw
        )

        defensibility = (
            AxisDiagnosticScore(
                score=defensibility_score,
                raw_score=defensibility_raw,
                level=_defensibility_level(
                    defensibility_score
                ),
                explanation=(
                    _defensibility_explanation(
                        defensibility_score,
                        defensibility_components,
                    )
                ),
                components=(
                    defensibility_components
                ),
                caps_applied=(
                    defensibility_caps
                ),
            )
        )

        # ----------------------------------------------------
        # FORCES / FAIBLESSES
        # ----------------------------------------------------

        all_components = (
            eligibility_components
            + defensibility_components
        )

        strengths = []
        weaknesses = []

        for component in (
            all_components
        ):
            ratio = (
                (
                    component.score
                    / component.max_score
                )
                if component.max_score
                else 0.0
            )

            if ratio >= 0.70:
                strengths.append(
                    f"{component.name} : "
                    f"{component.score}/{component.max_score}"
                )

            elif ratio < 0.50:
                weaknesses.append(
                    f"{component.name} : "
                    f"{component.score}/{component.max_score}"
                )

        risk = _risk_level(
            defensibility_score
        )

        potential = (
            eligibility.level
        )

        summary = (
            f"Éligibilité : "
            f"{eligibility.score}/100 — "
            f"{eligibility.level}. "
            f"Défendabilité : "
            f"{defensibility.score}/100 — "
            f"{defensibility.level}."
        )

        return CIRDiagnosticScore(
            eligibility=eligibility,
            defensibility=defensibility,
            risk_level=risk,
            potential_level=potential,
            strengths=strengths,
            weaknesses=weaknesses,
            summary=summary,
        )
