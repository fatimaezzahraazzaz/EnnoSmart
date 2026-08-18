from agents.EnnoAmelioration.application.fresh_research_policy_v312 import (
    MODE_FRESH,
    MODE_NONE,
    MODE_REUSE,
    resolve_fresh_research_policy,
)
from agents.EnnoAmelioration.domain.models import ImprovementIntent


def decide(text, *, choice=None, needs_scholar=True, editorial=False, forbid=False):
    return resolve_fresh_research_policy(
        instruction=text,
        current_choice=choice,
        intents=[ImprovementIntent.SCIENTIFIC_ENRICHMENT] if needs_scholar else [],
        needs_scholar=needs_scholar,
        editorial_only=editorial,
        hard_forbid_research=forbid,
        hard_forbid_scholar=False,
    )


def test_scientific_strengthening_always_opens_fresh_research():
    out = decide("Renforce scientifiquement uniquement cette section.")
    assert out.mode == MODE_FRESH


def test_stale_use_existing_choice_cannot_override_new_strengthening():
    out = decide(
        "Renforce scientifiquement uniquement cette section.",
        choice="use_existing_sources",
    )
    assert out.mode == MODE_FRESH


def test_explicit_reuse_is_the_only_reuse_path_for_strengthening():
    out = decide(
        "Renforce cette section avec les sources que j'ai gardées et ne relance pas de recherche.",
        choice="use_existing_sources",
    )
    assert out.mode == MODE_REUSE


def test_explicit_new_search_wins_even_if_old_sources_exist():
    out = decide(
        "Fais une nouvelle recherche scientifique et trouve de nouvelles sources pour renforcer cette section.",
        choice="use_existing_sources",
    )
    assert out.mode == MODE_FRESH


def test_simple_write_after_validation_does_not_relaunch_by_itself():
    out = resolve_fresh_research_policy(
        instruction="C'est bon, rédige la section.",
        current_choice=None,
        intents=[],
        needs_scholar=True,
        editorial_only=False,
        hard_forbid_research=False,
        hard_forbid_scholar=False,
    )
    assert out.mode == MODE_NONE


def test_explicit_validated_evidence_reuse_is_recognized():
    out = resolve_fresh_research_policy(
        instruction="Rédige maintenant avec les preuves validées, sans nouvelle recherche.",
        current_choice=None,
        intents=[],
        needs_scholar=True,
        editorial_only=False,
        hard_forbid_research=False,
        hard_forbid_scholar=False,
    )
    assert out.mode == MODE_REUSE


def test_editorial_only_never_launches_research():
    out = resolve_fresh_research_policy(
        instruction="Améliore uniquement le style et la clarté de ce paragraphe.",
        current_choice=None,
        intents=[ImprovementIntent.STYLE],
        needs_scholar=False,
        editorial_only=True,
        hard_forbid_research=False,
        hard_forbid_scholar=False,
    )
    assert out.mode == MODE_NONE
