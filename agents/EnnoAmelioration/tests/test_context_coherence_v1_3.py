from agents.EnnoAmelioration.application.section_improvement_policy import (
    render_section_improvement_contract,
)
from agents.EnnoAmelioration.application.traceability_service import _replacement_pairs
from agents.EnnoAmelioration.domain.models import SectionFunction


def test_context_contract_requires_causal_progression_and_order():
    contract = render_section_improvement_contract(SectionFunction.CONTEXT)
    assert "progression causale" in contract
    assert "Préserver l'ordre logique des concepts" in contract
    assert "ne doit pas permuter" in contract
    assert "maillon causal absent" in contract


def test_trace_alignment_does_not_zip_equal_length_reordered_sentences():
    before = [
        "Les radars permettent d'observer la zone étudiée.",
        "Les modèles nécessitent de grands volumes de données.",
        "Le Dataset Shift limite la généralisation aux données réelles.",
    ]
    after = [
        "Le Dataset Shift limite la généralisation vers des données réelles.",
        "Les radars permettent l'observation de la zone étudiée.",
        "Les modèles d'apprentissage nécessitent un grand volume de données.",
    ]
    ops = _replacement_pairs(before, after)
    replacements = [(b, a) for op, b, a in ops if op == "replace"]
    # Un alignement monotone peut choisir soit la première idée déplacée, soit
    # les deux idées restées dans le même ordre, mais il ne doit jamais créer
    # les faux couples positionnels de l'ancien zip.
    assert not any(
        "radars" in b.lower() and "dataset shift" in a.lower()
        for b, a in replacements
    )
    assert not any(
        "volumes de données" in b.lower() and "radars" in a.lower()
        for b, a in replacements
    )


def test_trace_alignment_pairs_true_local_paraphrases():
    before = [
        "Les radars sont des systèmes actifs disposant de leur propre source d'émission.",
        "Les données publiques disponibles sont limitées.",
    ]
    after = [
        "Les radars sont des systèmes actifs qui possèdent leur propre source d'émission.",
        "Les données disponibles publiquement restent limitées.",
    ]
    ops = _replacement_pairs(before, after)
    replacements = [(b, a) for op, b, a in ops if op == "replace"]
    assert len(replacements) == 2
    assert "radars" in replacements[0][0].lower() and "radars" in replacements[0][1].lower()
    assert "données" in replacements[1][0].lower() and "données" in replacements[1][1].lower()
