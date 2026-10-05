from ennodiagnostic_v2.project_frascati import ProjectFrascatiEvaluator
from ennodiagnostic_v2.rnd_approach import RnDApproachAssessment
from ennodiagnostic_v2.runtime_adapters import EnnoSmartLLMAdapter
from ennodiagnostic_v2.schemas import (
    CanonicalEntity,
    ExtractionItem,
    EvidenceRef,
)


def extraction(i, kind, text):
    ev = EvidenceRef(
        evidence_id=f"ev_{i}",
        document_id=f"doc_{i}",
        document_name=f"doc_{i}.docx",
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


def lock_entity(
    entity_id,
    statement,
    extraction_item,
    qualification,
    reason,
):
    return CanonicalEntity(
        entity_id=entity_id,
        kind="lock",
        statement=statement,
        source_item_ids=[extraction_item.item_id],
        evidences=[extraction_item.evidence],
        document_ids=[extraction_item.evidence.document_id],
        metadata={
            "qualification_status": qualification,
            "qualification_reason": reason,
            "suggested_statement": "",
        },
    )


# ============================================================
# CAS 1 : PROJET R&D SOLIDE
# ============================================================

strong_lock = extraction(
    "strong_lock",
    "lock",
    (
        "? conditions identiques, le proc?d? pr?sente une variation "
        "de porosit? de 3 % ? 11 %, sans m?canisme explicatif ma?tris?."
    ),
)

strong_items = [
    strong_lock,

    extraction(
        "strong_baseline",
        "objective",
        (
            "La m?thode initiale permettait de ma?triser la porosit? "
            "uniquement ? l'?chelle laboratoire, mais devenait instable "
            "lors du passage au pilote."
        ),
    ),

    extraction(
        "strong_method1",
        "method",
        (
            "Une premi?re hypoth?se attribuait la variabilit? "
            "? la temp?rature de polym?risation."
        ),
    ),

    extraction(
        "strong_method2",
        "method",
        (
            "Une seconde hypoth?se portait sur l'interaction "
            "entre temp?rature et pression."
        ),
    ),

    extraction(
        "strong_param",
        "parameter",
        (
            "Trois niveaux de temp?rature et quatre niveaux de pression "
            "ont ?t? test?s selon un protocole exp?rimental document?."
        ),
    ),

    extraction(
        "strong_result1",
        "result",
        (
            "Les premiers essais n'ont pas permis de stabiliser "
            "la porosit? sous 5 %, ce qui a conduit ? modifier "
            "la plage de pression ?tudi?e."
        ),
    ),

    extraction(
        "strong_result2",
        "result",
        (
            "Les essais suivants ont mis en ?vidence une interaction "
            "entre temp?rature et pression qui n'?tait pas identifi?e "
            "dans la m?thode initiale."
        ),
    ),

    extraction(
        "strong_transfer",
        "result",
        (
            "Le protocole, les param?tres, les conditions d'essais "
            "et les r?sultats ont ?t? consign?s afin de reproduire "
            "les essais sur de nouvelles s?ries."
        ),
    ),
]

strong_entity = lock_entity(
    "lock_strong",
    strong_lock.statement,
    strong_lock,
    "VALID_LOCK",
    (
        "Variabilit? non ma?tris?e d?montr?e par plusieurs essais "
        "et r?glages infructueux."
    ),
)

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


# ============================================================
# CAS 2 : INGENIERIE CLASSIQUE
# ============================================================

eng_lock = extraction(
    "eng_lock",
    "lock",
    (
        "L'application doit permettre l'export des rapports "
        "au format PDF."
    ),
)

engineering_items = [
    eng_lock,

    extraction(
        "eng_method",
        "method",
        (
            "Une biblioth?que PDF existante a ?t? int?gr?e "
            "et configur?e dans l'application."
        ),
    ),

    extraction(
        "eng_result",
        "result",
        (
            "Apr?s configuration de la biblioth?que, "
            "les rapports sont correctement export?s en PDF."
        ),
    ),
]

engineering_entity = lock_entity(
    "lock_engineering",
    eng_lock.statement,
    eng_lock,
    "REQUIREMENT_ONLY",
    (
        "Le passage d?crit une fonctionnalit? attendue "
        "sans incertitude technique."
    ),
)

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


# ============================================================
# CAS 3 : DOSSIER R&D PARTIEL
# ============================================================

partial_lock = extraction(
    "partial_lock",
    "lock",
    (
        "Le capteur pr?sente une instabilit? dans certaines "
        "conditions vibratoires qui affecte la mesure."
    ),
)

partial_items = [
    partial_lock,

    extraction(
        "partial_method",
        "method",
        (
            "Plusieurs fr?quences de filtrage ont ?t? compar?es "
            "sur un banc d'essai."
        ),
    ),

    extraction(
        "partial_param",
        "parameter",
        (
            "Les fr?quences de coupure de 20, 40 et 60 Hz "
            "ont ?t? test?es."
        ),
    ),

    extraction(
        "partial_result",
        "result",
        (
            "Le filtrage ? 20 Hz r?duit le bruit mais augmente "
            "le temps de r?ponse du syst?me."
        ),
    ),

    extraction(
        "partial_design",
        "method",
        (
            "Une adaptation sp?cifique du filtrage est envisag?e "
            "pour rechercher un compromis entre bruit et temps de r?ponse."
        ),
    ),
]

partial_entity = lock_entity(
    "lock_partial",
    partial_lock.statement,
    partial_lock,
    "NEEDS_REFORMULATION",
    (
        "La probl?matique technique est identifiable, "
        "mais les limites et essais restent partiellement document?s."
    ),
)

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


CASES = {
    "strong_research": {
        "entities": [strong_entity],
        "extractions": strong_items,
        "rnd": strong_rnd,
        "expected": {
            "uncertainty": "supported",
            "novelty": "supported",
            "creativity": "supported",
            "systematic": "supported",
            "transferability": "supported",
        },
    },

    "classic_engineering": {
        "entities": [engineering_entity],
        "extractions": engineering_items,
        "rnd": engineering_rnd,
        "expected": {
            "uncertainty": "insufficient",
            "novelty": "insufficient",
            "creativity": "insufficient",
            "systematic": "insufficient",
            "transferability": "insufficient",
        },
    },

    "partial_research": {
        "entities": [partial_entity],
        "extractions": partial_items,
        "rnd": partial_rnd,
        "expected": {
            "uncertainty": "partial",
            "novelty": "partial",
            "creativity": "partial",
            "systematic": "partial",
            "transferability": "partial",
        },
    },
}


llm = EnnoSmartLLMAdapter(
    request_name="ennodiagnostic:v2:project_frascati_benchmark",
    temperature=0.0,
    max_output_tokens=2800,
)

evaluator = ProjectFrascatiEvaluator(llm)

total_ok = 0
total_checks = 0

print()
print("=" * 78)
print("BENCHMARK FRASCATI PROJET")
print("=" * 78)

for name, case in CASES.items():

    result = evaluator.assess(
        entities=case["entities"],
        extractions=case["extractions"],
        rnd=case["rnd"],
    )

    actual = {
        criterion.criterion: criterion
        for criterion in result.criteria
    }

    print()
    print("-" * 78)
    print("PROJET :", name)
    print("-" * 78)

    for criterion_name, expected in case["expected"].items():

        criterion = actual.get(criterion_name)

        obtained = (
            criterion.status
            if criterion is not None
            else "MISSING"
        )

        ok = obtained == expected

        total_checks += 1

        if ok:
            total_ok += 1

        print()
        print("CRITERE :", criterion_name)
        print("ATTENDU :", expected)
        print("OBTENU  :", obtained)
        print("RESULTAT :", "OK" if ok else "A REVOIR")

        if criterion is not None:
            print(
                "EXPLICATION :",
                criterion.explanation,
            )

            if criterion.missing_evidence:
                print(
                    "PREUVES MANQUANTES :",
                    criterion.missing_evidence,
                )

    print()
    print("RESUME :", result.summary)

    if result.strengths:
        print("POINTS FORTS :", result.strengths)

    if result.weaknesses:
        print("FAIBLESSES :", result.weaknesses)


print()
print("=" * 78)
print(
    f"SCORE BENCHMARK : {total_ok}/{total_checks}"
)
print("=" * 78)
