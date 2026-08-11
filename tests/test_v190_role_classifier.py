from modules.NLP.semantic_lock_finalizer import (
    GROUP_ROLE_HYPOTHESES,
    LIMITATION_ROLE_HYPOTHESES,
    SemanticLockFinalizer,
    VERSION,
)


LOCK_RAW = {
    "LOCK_CORE": 0.46,
    "SUPPORT_EVIDENCE": 0.15,
    "METHOD_CONTEXT": 0.27,
    "NOISE": 0.12,
}

METHOD_RAW = {
    "LOCK_CORE": 0.24,
    "SUPPORT_EVIDENCE": 0.18,
    "METHOD_CONTEXT": 0.48,
    "NOISE": 0.10,
}


def _finalizer(primary_scores, limitation_scores=None):
    finalizer = object.__new__(SemanticLockFinalizer)

    def score(_profile, hypotheses):
        if hypotheses is GROUP_ROLE_HYPOTHESES:
            return dict(primary_scores)
        if hypotheses is LIMITATION_ROLE_HYPOTHESES:
            return dict(
                limitation_scores
                or {
                    "ATTRIBUTED_METHOD_LIMITATION": 0.75,
                    "EXPLICIT_UNRESOLVED_PROBLEM": 0.25,
                }
            )
        raise AssertionError("unexpected hypothesis family")

    finalizer._score_hypotheses = score
    return finalizer


def test_v190_changes_only_role_classifier_version():
    assert VERSION == (
        "semantic_lock_finalizer_v190_"
        "role_classifier_only_complete_linkage_unchanged"
    )


def test_relative_which_clause_and_residual_difference_are_evidence():
    report = _finalizer(LOCK_RAW).classify_group(
        "Despite their overall similarity, noticeable differences remain in "
        "the signatures, which are expected to be partly compensated.",
        {"project_lock_seed_count": 1},
    )

    assert report["semantic_signals"]["lock_intent"] is False
    assert report["semantic_signals"]["result_only"] is True
    assert report["role"] == "SUPPORT_EVIDENCE"
    assert report["decision_reason"] == "explicit_observation_or_result_without_lock"


def test_low_nli_support_score_does_not_override_explicit_observation():
    report = _finalizer({
        "LOCK_CORE": 0.6163,
        "SUPPORT_EVIDENCE": 0.1499,
        "METHOD_CONTEXT": 0.2167,
        "NOISE": 0.0171,
    }).classify_group(
        "The score includes angles where classification might be very "
        "difficult or even impossible.",
        {"project_lock_seed_count": 1},
    )

    assert report["role"] == "SUPPORT_EVIDENCE"
    assert report["raw_top_role"] == "LOCK_CORE"


def test_existing_method_limitation_requires_surface_and_nli_agreement():
    report = _finalizer(LOCK_RAW).classify_group(
        "Existing refinement approaches require real observations that may be "
        "impossible to obtain.",
        {"project_lock_seed_count": 1},
    )

    assert report["semantic_signals"]["attributed_method_limitation"] is True
    assert report["limitation_role_scores"] == {
        "ATTRIBUTED_METHOD_LIMITATION": 0.75,
        "EXPLICIT_UNRESOLVED_PROBLEM": 0.25,
    }
    assert report["role"] == "SUPPORT_EVIDENCE"
    assert report["decision_reason"] == "nli_confirmed_limitation_of_existing_method"


def test_unconfirmed_method_limitation_is_not_forced_to_support():
    report = _finalizer(
        LOCK_RAW,
        {
            "ATTRIBUTED_METHOD_LIMITATION": 0.54,
            "EXPLICIT_UNRESOLVED_PROBLEM": 0.46,
        },
    ).classify_group(
        "Existing approaches require additional validation.",
        {"project_lock_seed_count": 1},
    )

    assert report["role"] == "LOCK_CORE"
    assert report["decision_reason"] == "nli_lock_core"


def test_explicit_unresolved_problem_stays_core_even_with_method_language():
    report = _finalizer(LOCK_RAW).classify_group(
        "Synthetic datasets are not as representative as real measurements; "
        "models cannot generalize well to real conditions.",
        {"project_lock_seed_count": 1},
    )

    assert report["semantic_signals"]["lock_intent"] is True
    assert report["role"] == "LOCK_CORE"
    assert report["limitation_role_scores"] == {}


def test_explicit_question_and_tradeoff_stay_core():
    finalizer = _finalizer(LOCK_RAW)
    question = finalizer.classify_group(
        "What simplifying assumptions are necessary and sufficient?",
        {"project_lock_seed_count": 1},
    )
    tradeoff = finalizer.classify_group(
        "The unresolved trade-off between accuracy and computing speed must "
        "still be understood.",
        {"project_lock_seed_count": 1},
    )

    assert question["role"] == "LOCK_CORE"
    assert tradeoff["role"] == "LOCK_CORE"


def test_metric_and_procedure_stay_method_context():
    finalizer = _finalizer(METHOD_RAW)
    metric = finalizer.classify_group(
        "This score gives a metric to compare two datasets.",
        {"project_lock_seed_count": 1},
    )
    procedure = finalizer.classify_group(
        "The method uses a numerical procedure and an experimental setup.",
        {"project_lock_seed_count": 1},
    )

    assert metric["role"] == "METHOD_CONTEXT"
    assert procedure["role"] == "METHOD_CONTEXT"


def test_comparative_inventory_of_methods_stays_method_context():
    report = _finalizer(
        LOCK_RAW,
        {
            "ATTRIBUTED_METHOD_LIMITATION": 0.56,
            "EXPLICIT_UNRESOLVED_PROBLEM": 0.44,
        },
    ).classify_group(
        "Le logiciel propose differentes methodes de calcul, comme la methode "
        "des moments et les elements finis. Ces methodes necessitent des "
        "ressources importantes, tandis que d'autres sont plus rapides.",
        {"project_lock_seed_count": 1},
    )

    assert report["semantic_signals"]["method_inventory"] is True
    assert report["role"] == "METHOD_CONTEXT"
