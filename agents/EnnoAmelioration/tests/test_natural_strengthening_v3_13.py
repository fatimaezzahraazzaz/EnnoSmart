from agents.EnnoAmelioration.application.fresh_research_policy_v313 import (
    MODE_FRESH,
    MODE_NONE,
    MODE_REUSE,
    resolve_fresh_research_policy,
)
from agents.EnnoAmelioration.domain.models import ImprovementIntent


def decide(text, *, intents=None, needs_scholar=True, editorial=False, forbid=False):
    return resolve_fresh_research_policy(
        instruction=text,
        current_choice=None,
        intents=intents or [],
        needs_scholar=needs_scholar,
        editorial_only=editorial,
        hard_forbid_research=forbid,
        hard_forbid_scholar=False,
    )


def test_user_exact_failed_phrase_now_opens_research():
    out = decide(
        "renforce uniquement la section Analyse de l’état de l’art par plus d'argument",
        intents=[ImprovementIntent.ARGUMENTATION],
        needs_scholar=True,
    )
    assert out.mode == MODE_FRESH


def test_natural_reinforce_without_word_scientific():
    out = decide(
        "Renforce cette partie avec plus d'arguments.",
        intents=[ImprovementIntent.ARGUMENTATION],
        needs_scholar=True,
    )
    assert out.mode == MODE_FRESH


def test_develop_more_arguments():
    out = decide(
        "Développe davantage l'argumentation de cette section.",
        intents=[ImprovementIntent.ARGUMENTATION],
        needs_scholar=True,
    )
    assert out.mode == MODE_FRESH


def test_make_section_more_solid():
    out = decide(
        "Rends cette partie plus solide et mieux étayée.",
        intents=[ImprovementIntent.ARGUMENTATION],
        needs_scholar=True,
    )
    assert out.mode == MODE_FRESH


def test_scientific_strengthening_still_opens_research():
    out = decide(
        "Renforce scientifiquement cette section.",
        intents=[ImprovementIntent.SCIENTIFIC_ENRICHMENT],
        needs_scholar=True,
    )
    assert out.mode == MODE_FRESH


def test_explicit_search_always_fresh():
    out = decide(
        "Fais une recherche ciblée et trouve des articles.",
        intents=[ImprovementIntent.RESEARCH],
        needs_scholar=True,
    )
    assert out.mode == MODE_FRESH


def test_style_only_never_researches():
    out = decide(
        "Améliore seulement le style, rends le texte plus clair.",
        intents=[ImprovementIntent.STYLE],
        needs_scholar=True,
        editorial=True,
    )
    assert out.mode == MODE_NONE


def test_reuse_only_when_explicit():
    out = decide(
        "Utilise les sources que j'ai gardées et ne relance pas de recherche.",
        intents=[ImprovementIntent.SCIENTIFIC_ENRICHMENT],
        needs_scholar=True,
    )
    assert out.mode == MODE_REUSE


def test_write_after_validation_does_not_relaunch():
    out = decide(
        "C'est bon, rédige maintenant la section.",
        intents=[ImprovementIntent.GENERAL_REVISION],
        needs_scholar=True,
    )
    assert out.mode == MODE_NONE


def test_old_use_existing_choice_is_ignored_without_current_request():
    out = resolve_fresh_research_policy(
        instruction="C'est bon, rédige.",
        current_choice="use_existing_sources",
        intents=[ImprovementIntent.GENERAL_REVISION],
        needs_scholar=True,
        editorial_only=False,
        hard_forbid_research=False,
        hard_forbid_scholar=False,
    )
    assert out.mode == MODE_NONE


def test_argumentation_intent_from_semantic_router_is_enough_for_scholar_target():
    out = decide(
        "Je veux que cette partie soit beaucoup mieux défendue.",
        intents=[ImprovementIntent.ARGUMENTATION],
        needs_scholar=True,
    )
    assert out.mode == MODE_FRESH


def test_argumentation_on_non_scholar_target_does_not_force_external_research():
    out = decide(
        "Renforce cette partie avec plus d'arguments.",
        intents=[ImprovementIntent.ARGUMENTATION],
        needs_scholar=False,
    )
    assert out.mode == MODE_NONE
