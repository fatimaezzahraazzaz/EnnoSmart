from agents.EnnoDiagnostic.diagnostic_static_presenter import demarche_legibility_text
from modules.NLP.demarche_legibility import (
    assess_group_demarche_legibility,
    assess_project_demarche_legibility,
)
from modules.NLP.frascati_assessment import assess_group_frascati, assess_project_frascati


def passage(pid, text, role, *, document="dossier.pdf", sentence_start=None, **extra):
    value = {
        "passage_id": pid,
        "text": text,
        "analysis_text": text,
        "semantic_role": role,
        "original_model_role": role,
        "role": role,
        "direct_lock_candidate": role == "verrou",
        "document": document,
        "section_title": extra.pop("section_title", "Travaux R&D"),
        **extra,
    }
    if sentence_start is not None:
        value["sentence_start"] = sentence_start
    return value


def group(*passages, group_id="G1"):
    return {
        "lock_group_id": group_id,
        "supporting_passages": list(passages),
    }


def defendable_group(group_id="G-RND"):
    return group(
        passage(
            "E1",
            "Le comportement reste impossible à prédire et constitue un verrou technologique.",
            "verrou",
            sentence_start=10,
        ),
        passage(
            "E2",
            "L'état de l'art montre des solutions existantes insuffisantes. Notre hypothèse originale "
            "est que le paradigme de simulation influence la généralisation ; nous le comparons afin de le vérifier.",
            "methode",
            sentence_start=20,
        ),
        passage(
            "E3",
            "Les mesures ont montré un écart reproductible ; les connaissances acquises sont documentées et réutilisables.",
            "resultat",
            sentence_start=30,
        ),
        group_id=group_id,
    )


def classical_group(group_id="G-CLASSIC"):
    return group(
        passage("C1", "La conformité du composant doit être contrôlée.", "verrou", sentence_start=10),
        passage(
            "C2",
            "Une validation unitaire vérifie le bon fonctionnement et la valeur attendue.",
            "methode",
            sentence_start=20,
        ),
        passage("C3", "Le résultat est conforme aux spécifications.", "resultat", sentence_start=30),
        group_id=group_id,
    )


def test_passages_are_reconstructed_as_one_ordered_operation_chain():
    operation = group(
        passage("E4", "Les mesures ont montré un impact mesurable.", "resultat", sentence_start=40),
        passage("E2", "Notre hypothèse est que le paradigme influence le résultat.", "methode", sentence_start=20),
        passage("E1", "Le comportement reste impossible à prédire.", "verrou", sentence_start=10),
        passage("E3", "Un test comparatif est mené dans des conditions contrôlées.", "methode", sentence_start=30),
    )

    report = assess_group_demarche_legibility(operation)

    assert report["analysis_unit"] == "consolidated_lock_operation_not_passage"
    assert report["operation_count"] == 1
    assert report["method_steps_count"] == 1
    assert report["operation_status"] == "rnd_core_defendable"
    assert report["causal_chain"]["complete"] is True
    assert report["causal_chain"]["ordered_evidence_ids"][0] == "E1"
    assert report["causal_chain"]["ordered_evidence_ids"][-1] == "E4"


def test_many_method_passages_are_activities_not_many_rnd_operations():
    operation = defendable_group()
    operation["supporting_passages"].extend(
        [
            passage("E4", "Préparation des données nécessaires au protocole.", "methode", sentence_start=21),
            passage("E5", "Génération des images nécessaires à l'expérience.", "methode", sentence_start=22),
            passage("E6", "Entraînement du modèle pour exécuter la comparaison.", "methode", sentence_start=23),
            passage("E7", "Configuration standard du format d'export.", "methode", sentence_start=24),
        ]
    )

    group_report = assess_group_demarche_legibility(operation)
    project_report = assess_project_demarche_legibility([operation])

    assert group_report["operation_count"] == 1
    assert group_report["activities_count"] >= 5
    assert project_report["operations_count"] == 1
    assert project_report["activities_count"] == group_report["activities_count"]
    assert project_report["research_justified_steps_count"] == 1


def test_routine_validation_inside_a_research_chain_becomes_necessary_support():
    operation = defendable_group()
    operation["supporting_passages"].append(
        passage(
            "E4",
            "Une validation unitaire vérifie le respect du format attendu avant la comparaison.",
            "methode",
            sentence_start=25,
        )
    )

    report = assess_group_demarche_legibility(operation)
    activity = next(item for item in report["activities"] if item["evidence_id"] == "E4")

    assert activity["signals"]["routine_validation_candidate"] is True
    assert activity["activity_status"] == "necessary_rnd_support"
    assert report["necessary_rnd_support_activities_count"] >= 1


def test_generic_performance_validation_is_not_promoted_to_direct_rnd():
    operation = defendable_group()
    operation["supporting_passages"].append(
        passage(
            "E4",
            "La validation mesure la précision du modèle sur le jeu de test.",
            "methode",
            sentence_start=25,
        )
    )

    report = assess_group_demarche_legibility(operation)
    activity = next(item for item in report["activities"] if item["evidence_id"] == "E4")

    assert activity["signals"]["routine_validation_candidate"] is True
    assert activity["signals"]["direct_rnd_protocol"] is False
    assert activity["activity_status"] == "necessary_rnd_support"


def test_comparative_protocol_that_tests_hypothesis_can_remain_direct_rnd():
    report = assess_group_demarche_legibility(defendable_group())
    activity = next(item for item in report["activities"] if item["evidence_id"] == "E2")

    assert activity["signals"]["direct_rnd_protocol"] is True
    assert activity["activity_status"] == "direct_rnd"


def test_classical_engineering_is_blocked_but_systematicity_stays_independent():
    operation = classical_group()
    assessment = assess_group_frascati(operation)
    project = assess_project_frascati([operation])

    assert assessment["demarche_legibility"]["operation_status"] == "classical_engineering"
    assert assessment["criteria"]["systematicity"]["status"] == "documented"
    assert assessment["eligibility_recommendation"] == 0
    assert "classical_engineering_operation" in assessment["blocking_criteria"]
    assert assessment["rnd_defensibility_index"] == 0.0
    assert project["eligibility_recommendation"] == 0
    assert project["rnd_defensibility_index"] == 0.0
    assert project["eligibility_blocking_reason"] == "no_potentially_eligible_operation"


def test_partial_chain_changes_risk_not_frascati_coverage_or_score():
    operation = group(
        passage("E1", "Le comportement reste impossible à prédire et constitue un verrou.", "verrou", sentence_start=10),
        passage("E2", "Un test comparatif des variantes A et B a été exécuté.", "methode", sentence_start=20),
        passage("E3", "Le résultat a montré un écart mesurable.", "resultat", sentence_start=30),
        passage("E4", "Finalement, la solution finale a été retenue.", "methode", sentence_start=40),
    )

    assessment = assess_group_frascati(operation)
    audit = assessment["demarche_legibility"]

    assert audit["operation_status"] == "rnd_core_partial"
    assert audit["direct_final_solution_risk"] is True
    assert audit["llm_review_recommended"] is True
    assert audit["llm_policy"] == "reuse_existing_ennodiagnostic_call_only_no_dedicated_call_by_default"
    assert assessment["eligibility_recommendation"] == 1
    assert assessment["risk_level"] == "moyen"
    assert assessment["rnd_defensibility_index"] == assessment["documentary_coverage"]


def test_project_remains_potentially_eligible_with_rnd_and_classical_operations():
    project = assess_project_frascati([
        defendable_group("G-RND"),
        classical_group("G-CLASSIC"),
    ])

    assert project["eligible_operations_count"] == 1
    assert project["eligibility_recommendation"] == 1
    assert project["risk_level"] == "moyen"
    assert project["demarche_legibility"]["project_status"] == "rnd_core_defendable"
    assert project["demarche_legibility"]["classical_engineering_operations_count"] == 1
    assert project["rnd_defensibility_index"] > 0


def test_project_decision_does_not_merge_fragmented_criteria_into_fake_rnd_operation():
    novelty_only = group(
        passage("N1", "L'état de l'art montre des solutions existantes insuffisantes.", "limite"),
        group_id="G-NOVELTY",
    )
    uncertainty_only = group(
        passage("U1", "Un résultat reste impossible à prédire et constitue un verrou.", "verrou"),
        group_id="G-UNCERTAINTY",
    )
    systematicity_only = group(
        passage("S1", "Un protocole de tests comparatifs est exécuté.", "methode"),
        passage("S2", "Les mesures sont consignées dans un résultat reproductible.", "resultat"),
        group_id="G-SYSTEMATIC",
    )

    project = assess_project_frascati([novelty_only, uncertainty_only, systematicity_only])

    assert all(item["eligibility_recommendation"] == 0 for item in project["group_assessments"])
    assert project["eligibility_recommendation"] == 0
    assert project["rnd_defensibility_index"] == 0.0
    assert project["project_criteria_semantics"].startswith("portfolio_summary_only")


def test_insufficient_evidence_keeps_coverage_but_cannot_alone_qualify_project():
    unclear_operation = group(
        passage("I1", "Une limite technique est mentionnée sans autre explication.", "limite"),
        passage("I2", "Une configuration spécifique est réalisée.", "methode"),
        group_id="G-UNCLEAR",
    )

    operation = assess_group_frascati(unclear_operation)
    project = assess_project_frascati([unclear_operation])

    assert operation["demarche_legibility"]["operation_status"] == "insufficient_evidence"
    assert operation["eligibility_recommendation"] == 1
    assert operation["rnd_defensibility_index"] == operation["documentary_coverage"]
    assert operation["risk_level"] == "moyen"
    assert project["eligible_operations_count"] == 0
    assert project["eligibility_recommendation"] == 0
    assert project["rnd_defensibility_index"] == 0.0


def test_frontend_summary_explains_operations_activities_and_strict_classical_guard():
    text = demarche_legibility_text(
        {
            "demarche_legibility": {
                "project_status": "classical_engineering",
                "operations_count": 2,
                "rnd_core_defendable_operations_count": 0,
                "rnd_core_partial_operations_count": 0,
                "classical_engineering_operations_count": 2,
                "insufficient_evidence_operations_count": 0,
                "activities_count": 7,
                "direct_rnd_activities_count": 0,
                "necessary_rnd_support_activities_count": 0,
                "classical_engineering_activities_count": 6,
                "insufficient_evidence_activities_count": 1,
                "direct_final_solution_risk": True,
                "llm_review_recommended": False,
                "questions_to_ask": [],
            }
        }
    )

    assert "2 opération(s) consolidée(s)" in text
    assert "7 activités internes" in text
    assert "non éligible potentielle" in text
    assert "ramené à 0" in text
