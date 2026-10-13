from ennodiagnostic_v2.cir_scoring import CIRScorer
from ennodiagnostic_v2.schemas import (
    CanonicalEntity,
    EvidenceRef,
    ExtractionItem,
)
from ennodiagnostic_v2.rnd_approach import RnDApproachAssessment
from ennodiagnostic_v2.project_frascati import (
    ProjectFrascatiAssessment,
    ProjectFrascatiCriterion,
)


def item(i, kind, text):
    ev = EvidenceRef(
        evidence_id=f"ev_{i}",
        document_id="benchmark",
        document_name="benchmark.docx",
        chunk_id=f"chunk_{i}",
        quote=text,
        quote_verified=True,
    )

    return ExtractionItem(
        item_id=f"item_{i}",
        kind=kind,
        statement=text,
        evidence=ev,
    )


def lock(i, status):
    source = item(
        i,
        "lock",
        f"Probl?matique technique {i}",
    )

    entity = CanonicalEntity(
        entity_id=f"lock_{i}",
        kind="lock",
        statement=source.statement,
        source_item_ids=[source.item_id],
        evidences=[source.evidence],
        document_ids=["benchmark"],
        metadata={
            "qualification_status": status,
        },
    )

    return source, entity


def frascati(status):
    return ProjectFrascatiAssessment(
        criteria=[
            ProjectFrascatiCriterion(
                criterion=name,
                status=status,
            )
            for name in [
                "uncertainty",
                "novelty",
                "creativity",
                "systematic",
                "transferability",
            ]
        ]
    )


scorer = CIRScorer()


# ============================================================
# CAS 1 : R&D FORTE
# ============================================================

strong_lock_item, strong_lock = lock(
    "strong",
    "VALID_LOCK",
)

strong_extractions = [
    strong_lock_item,
    item("obj", "objective", "Comprendre et ma?triser le ph?nom?ne."),
    item("method", "method", "Plusieurs hypoth?ses ont ?t? test?es."),
    item("param", "parameter", "Temp?rature et pression ont ?t? vari?es."),
    item("result1", "result", "Le premier essai a ?chou?."),
    item("result2", "result", "Une interaction nouvelle a ?t? identifi?e."),
]

strong_rnd = RnDApproachAssessment(
    status="RESEARCH_APPROACH",
    hypothesis_status="present",
    experimentation_status="present",
    parameter_study_status="present",
    iteration_status="present",
    failure_learning_status="present",
    result_analysis_status="present",
    knowledge_gain_status="present",
)

strong_result = scorer.score(
    entities=[strong_lock],
    extractions=strong_extractions,
    rnd=strong_rnd,
    frascati=frascati("supported"),
)


# ============================================================
# CAS 2 : R&D PARTIELLE
# ============================================================

partial_lock_item, partial_lock = lock(
    "partial",
    "NEEDS_REFORMULATION",
)

partial_extractions = [
    partial_lock_item,
    item("pmethod", "method", "Plusieurs filtrages ont ?t? test?s."),
    item("pparam", "parameter", "20, 40 et 60 Hz ont ?t? compar?s."),
    item("presult", "result", "Un compromis bruit / temps de r?ponse appara?t."),
]

partial_rnd = RnDApproachAssessment(
    status="MIXED_APPROACH",
    hypothesis_status="partial",
    experimentation_status="present",
    parameter_study_status="present",
    iteration_status="partial",
    failure_learning_status="partial",
    result_analysis_status="present",
    knowledge_gain_status="partial",
)

partial_result = scorer.score(
    entities=[partial_lock],
    extractions=partial_extractions,
    rnd=partial_rnd,
    frascati=frascati("partial"),
)


# ============================================================
# CAS 3 : INGENIERIE CLASSIQUE
# ============================================================

engineering_lock_item, engineering_lock = lock(
    "engineering",
    "REQUIREMENT_ONLY",
)

engineering_extractions = [
    engineering_lock_item,
    item("emethod", "method", "Une biblioth?que existante est int?gr?e."),
    item("eresult", "result", "La fonctionnalit? fonctionne."),
]

engineering_rnd = RnDApproachAssessment(
    status="ENGINEERING_ONLY",
    hypothesis_status="absent",
    experimentation_status="absent",
    parameter_study_status="absent",
    iteration_status="absent",
    failure_learning_status="absent",
    result_analysis_status="absent",
    knowledge_gain_status="absent",
)

engineering_result = scorer.score(
    entities=[engineering_lock],
    extractions=engineering_extractions,
    rnd=engineering_rnd,
    frascati=frascati("insufficient"),
)


# ============================================================
# CAS 4 : TEST DU GARDE-FOU
#
# M?me si les autres modules sont artificiellement tr?s positifs,
# un projet sans vrai verrou ne doit pas avoir un score ?lev?.
# ============================================================

guard_lock_item, guard_lock = lock(
    "guard",
    "REQUIREMENT_ONLY",
)

guard_extractions = [
    guard_lock_item,
    item("gobj", "objective", "Objectif technique."),
    item("gmethod", "method", "M?thode document?e."),
    item("gparam", "parameter", "Param?tres document?s."),
    item("gres1", "result", "R?sultat document?."),
    item("gres2", "result", "Deuxi?me r?sultat document?."),
]

guard_rnd = RnDApproachAssessment(
    status="RESEARCH_APPROACH",
    hypothesis_status="present",
    experimentation_status="present",
    parameter_study_status="present",
    iteration_status="present",
    failure_learning_status="present",
    result_analysis_status="present",
    knowledge_gain_status="present",
)

guard_result = scorer.score(
    entities=[guard_lock],
    extractions=guard_extractions,
    rnd=guard_rnd,
    frascati=frascati("supported"),
)


CASES = [
    (
        "strong_research",
        strong_result,
        90,
        100,
    ),
    (
        "partial_research",
        partial_result,
        60,
        74.9,
    ),
    (
        "classic_engineering",
        engineering_result,
        0,
        39.9,
    ),
    (
        "guard_no_credible_lock",
        guard_result,
        0,
        55,
    ),
]


print()
print("=" * 78)
print("BENCHMARK SCORE DIAGNOSTIC CIR")
print("=" * 78)

ok_count = 0

for name, result, minimum, maximum in CASES:

    ok = (
        minimum
        <= result.score
        <= maximum
    )

    if ok:
        ok_count += 1

    print()
    print("-" * 78)
    print("CAS :", name)
    print("-" * 78)

    print("SCORE BRUT :", result.raw_score)
    print("SCORE FINAL :", result.score)
    print("ATTENDU :", f"{minimum} - {maximum}")
    print("RESULTAT :", "OK" if ok else "A REVOIR")

    print("POTENTIEL :", result.potential_level)
    print("RISQUE :", result.risk_level)

    print()
    print("DETAIL :")

    for component in result.components:
        print(
            f"  {component.name}: "
            f"{component.score}/{component.max_score}"
        )

    if result.caps_applied:
        print()
        print("PLAFONDS :")
        for cap in result.caps_applied:
            print(" -", cap)

    print()
    print("EXPLICATION :", result.summary)


print()
print("=" * 78)
print(
    f"SCORE BENCHMARK : {ok_count}/{len(CASES)}"
)
print("=" * 78)
