from agents.EnnoDiagnostic.diagnostic_static_presenter import demarche_legibility_text
from modules.NLP.demarche_legibility import assess_group_demarche_legibility
from modules.NLP.frascati_assessment import assess_group_frascati, assess_project_frascati


def passage(pid, text, role):
    return {
        "passage_id": pid,
        "text": text,
        "analysis_text": text,
        "semantic_role": role,
        "original_model_role": role,
        "role": role,
        "direct_lock_candidate": role == "verrou",
    }


def group(*passages):
    return {
        "lock_group_id": "G1",
        "supporting_passages": list(passages),
    }


def test_each_research_step_needs_a_local_causal_link():
    report = assess_group_demarche_legibility(
        group(
            passage("E1", "Le comportement reste impossible à prédire et constitue un verrou.", "verrou"),
            passage("E2", "Un test comparatif des variantes A et B a été exécuté.", "methode"),
            passage("E3", "Le résultat a montré un écart mesurable entre les variantes.", "resultat"),
        )
    )

    assert report["research_justified_steps_count"] == 0
    assert report["unexplained_steps_count"] == 1
    assert report["steps"][0]["classification"] == "needs_explanation"
    assert report["llm_review_recommended"] is True


def test_documented_research_trajectory_is_kept_and_needs_no_extra_llm_review():
    report = assess_group_demarche_legibility(
        group(
            passage("E1", "Le comportement reste impossible à prédire et constitue un verrou.", "verrou"),
            passage(
                "E2",
                "Face aux limites de l'état de l'art et aux solutions existantes insuffisantes, "
                "notre hypothèse originale a été testée par comparaison afin de vérifier le phénomène.",
                "methode",
            ),
            passage("E3", "Les mesures ont montré un résultat reproductible et réutilisable.", "resultat"),
            passage(
                "E4",
                "Suite aux essais, un second test a été mené pour lever l'incertitude ; "
                "l'approche retenue a permis de documenter l'apprentissage obtenu.",
                "methode",
            ),
        )
    )

    assert report["label"] == "clear_research_trajectory"
    assert report["research_justified_steps_count"] == 2
    assert report["direct_final_solution_risk"] is False
    assert report["llm_review_recommended"] is False


def test_routine_engineering_is_directly_non_eligible_with_zero_score():
    routine_group = group(
        passage("E1", "Une incertitude de performance était mentionnée au démarrage.", "verrou"),
        passage("E2", "Installation et configuration standard selon la documentation.", "methode"),
        passage("E3", "Déploiement puis recette fonctionnelle selon le mode opératoire.", "methode"),
        passage("E4", "La solution finale est une solution standard directement applicable.", "methode"),
    )

    group_assessment = assess_group_frascati(routine_group)
    project_assessment = assess_project_frascati([routine_group])

    assert group_assessment["demarche_legibility"]["label"] == "routine_engineering_dominant"
    assert group_assessment["criteria"]["systematicity"]["status"] == "contradictory"
    assert group_assessment["eligibility_recommendation"] == 0
    assert group_assessment["eligibility_assessment_score"] == 0.0
    assert project_assessment["eligibility_recommendation"] == 0
    assert project_assessment["eligibility_assessment_score"] == 0.0
    assert project_assessment["eligibility_blocking_reason"] == "routine_engineering_without_justified_rnd_step"


def test_ambiguous_shortcut_penalizes_score_and_reuses_existing_llm_call():
    ambiguous_group = group(
        passage("E1", "Le comportement reste impossible à prédire et constitue un verrou.", "verrou"),
        passage("E2", "Un test de la variante A a été exécuté.", "methode"),
        passage("E3", "Le résultat de la campagne a été enregistré.", "resultat"),
        passage("E4", "Finalement, la solution finale a été retenue.", "methode"),
    )

    assessment = assess_group_frascati(ambiguous_group)
    audit = assessment["demarche_legibility"]

    assert audit["label"] == "mixed_or_partially_justified_trajectory"
    assert audit["direct_final_solution_risk"] is True
    assert audit["llm_review_recommended"] is True
    assert audit["llm_policy"] == "reuse_existing_ennodiagnostic_call_only_no_dedicated_call_by_default"
    assert 0.0 < assessment["eligibility_assessment_score"] < assessment["documentary_coverage"]


def test_frontend_summary_explains_the_strict_engineering_decision():
    text = demarche_legibility_text(
        {
            "demarche_legibility": {
                "label": "routine_engineering_dominant",
                "method_steps_count": 3,
                "research_justified_steps_count": 0,
                "routine_engineering_steps_count": 3,
                "unexplained_steps_count": 0,
                "redundant_steps_count": 0,
                "direct_final_solution_risk": True,
                "llm_review_recommended": False,
                "questions_to_ask": [],
            }
        }
    )

    assert "ingénierie classique dominante" in text
    assert "non éligible potentiel" in text
    assert "ramené à 0" in text
