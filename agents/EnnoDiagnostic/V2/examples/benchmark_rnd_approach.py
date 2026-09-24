from ennodiagnostic_v2.rnd_approach import RnDApproachAnalyzer
from ennodiagnostic_v2.runtime_adapters import EnnoSmartLLMAdapter
from ennodiagnostic_v2.schemas import ExtractionItem, EvidenceRef


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


CASES = {

    "research_materials": {
        "expected": "RESEARCH_APPROACH",
        "items": [
            item(
                "m1",
                "lock",
                "? conditions identiques, la porosit? varie fortement entre les s?ries."
            ),
            item(
                "m2",
                "parameter",
                "Plusieurs temp?ratures et pressions de polym?risation ont ?t? ?tudi?es."
            ),
            item(
                "m3",
                "method",
                "Trois campagnes d'essais ont ?t? men?es en faisant varier temp?rature et pression."
            ),
            item(
                "m4",
                "result",
                "Les premiers r?glages n'ont pas stabilis? la porosit? ; une seconde s?rie a ?t? r?alis?e avec une plage de pression diff?rente."
            ),
            item(
                "m5",
                "result",
                "Les essais ont mis en ?vidence une interaction entre temp?rature et pression influen?ant fortement la porosit?."
            ),
        ],
    },

    "classic_software": {
        "expected": "ENGINEERING_ONLY",
        "items": [
            item(
                "s1",
                "objective",
                "Ajouter une fonctionnalit? d'export PDF dans l'application."
            ),
            item(
                "s2",
                "method",
                "Une biblioth?que existante a ?t? int?gr?e puis configur?e."
            ),
            item(
                "s3",
                "result",
                "L'export PDF fonctionne d?sormais dans l'application."
            ),
        ],
    },

    "mixed_industrial": {
        "expected": "MIXED_APPROACH",
        "items": [
            item(
                "i1",
                "lock",
                "Le comportement du capteur devient instable dans certaines conditions vibratoires."
            ),
            item(
                "i2",
                "method",
                "Plusieurs fr?quences de filtrage ont ?t? test?es sur banc."
            ),
            item(
                "i3",
                "result",
                "Les essais ont montr? qu'un r?glage r?duit le bruit mais d?grade le temps de r?ponse."
            ),
            item(
                "i4",
                "method",
                "En parall?le, l'?quipe a d?velopp? les connecteurs n?cessaires ? l'int?gration dans le syst?me industriel."
            ),
        ],
    },

    "insufficient": {
        "expected": "INSUFFICIENT_EVIDENCE",
        "items": [
            item(
                "u1",
                "lock",
                "Le projet pr?sente plusieurs difficult?s techniques importantes."
            ),
            item(
                "u2",
                "objective",
                "L'objectif est d'am?liorer les performances du syst?me."
            ),
        ],
    },
}


llm = EnnoSmartLLMAdapter(
    request_name="ennodiagnostic:v2:rnd_approach_benchmark",
    temperature=0.0,
    max_output_tokens=2200,
)

analyzer = RnDApproachAnalyzer(llm)

score = 0

print()
print("=" * 70)
print("BENCHMARK DEMARCHE R&D")
print("=" * 70)

for name, case in CASES.items():

    result = analyzer.assess(
        case["items"]
    )

    expected = case["expected"]
    actual = result.status

    ok = actual == expected

    if ok:
        score += 1

    print()
    print("CAS :", name)
    print("ATTENDU :", expected)
    print("OBTENU  :", actual)
    print("RESULTAT :", "OK" if ok else "A REVOIR")

    print("Hypoth?se :", result.hypothesis_status)
    print("Exp?rimentation :", result.experimentation_status)
    print("Param?tres :", result.parameter_study_status)
    print("It?rations :", result.iteration_status)
    print("?checs/apprentissage :", result.failure_learning_status)
    print("Analyse r?sultats :", result.result_analysis_status)
    print("Gain connaissance :", result.knowledge_gain_status)

    print("RAISON :", result.rationale)

    if result.strengths:
        print("POINTS FORTS :", result.strengths)

    if result.weaknesses:
        print("FAIBLESSES :", result.weaknesses)

    if result.missing_evidence:
        print(
            "PREUVES MANQUANTES :",
            result.missing_evidence,
        )


print()
print("=" * 70)
print(
    f"SCORE BENCHMARK : {score}/{len(CASES)}"
)
print("=" * 70)
