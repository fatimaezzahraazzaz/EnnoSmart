from agents.EnnoAmelioration.application.fresh_research_policy_v314 import (
    MODE_FRESH,
    MODE_NONE,
    MODE_REUSE,
    resolve_fresh_research_policy,
)
from agents.EnnoAmelioration.domain.models import ImprovementIntent


def decide(text, intents=None, needs_scholar=False, editorial=False, forbid_research=False, forbid_scholar=False):
    return resolve_fresh_research_policy(
        instruction=text,
        current_choice=None,
        intents=intents or [],
        needs_scholar=needs_scholar,
        editorial_only=editorial,
        hard_forbid_research=forbid_research,
        hard_forbid_scholar=forbid_scholar,
    )


def test_exact_runtime_failure():
    out = decide(
        "la section Approches existantes de détection et reconnaissance automatique des cibles "
        "je la trouve faible et a besoin plus de justification et argument "
        "et je veut que tu l'improuve avec plus d'argument",
        intents=[ImprovementIntent.ARGUMENTATION],
        needs_scholar=False,
    )
    assert out.mode == MODE_FRESH


def test_plus_arguments_without_prior_scholar():
    assert decide("renforce cette section par plus d'argument").mode == MODE_FRESH


def test_plus_justification_without_prior_scholar():
    assert decide("je trouve ce passage faible, il a besoin de plus de justification").mode == MODE_FRESH


def test_improuve_plus_arguments():
    assert decide("improuve cette partie avec plus d'arguments").mode == MODE_FRESH


def test_semantic_argumentation_alone_is_enough():
    out = decide(
        "je veux que ce raisonnement soit beaucoup mieux défendu",
        intents=[ImprovementIntent.ARGUMENTATION],
        needs_scholar=False,
    )
    assert out.mode == MODE_FRESH


def test_scientific_enrichment_alone_is_enough():
    out = decide(
        "complète le fond",
        intents=[ImprovementIntent.SCIENTIFIC_ENRICHMENT],
        needs_scholar=False,
    )
    assert out.mode == MODE_FRESH


def test_research_intent_alone_is_enough():
    out = decide(
        "cherche ce qu'il faut",
        intents=[ImprovementIntent.RESEARCH],
        needs_scholar=False,
    )
    assert out.mode == MODE_FRESH


def test_explicit_reuse():
    out = decide(
        "utilise les sources que j'ai gardées et ne relance pas de recherche",
        intents=[ImprovementIntent.SCIENTIFIC_ENRICHMENT],
    )
    assert out.mode == MODE_REUSE


def test_style_only():
    out = decide(
        "améliore uniquement le style",
        intents=[ImprovementIntent.STYLE],
        editorial=True,
    )
    assert out.mode == MODE_NONE


def test_write_after_validation():
    out = decide(
        "c'est bon, rédige maintenant la section",
        intents=[ImprovementIntent.GENERAL_REVISION],
    )
    assert out.mode == MODE_NONE


def test_old_frontend_choice_not_reused_silently():
    out = resolve_fresh_research_policy(
        instruction="continue",
        current_choice="use_existing_sources",
        intents=[ImprovementIntent.GENERAL_REVISION],
        needs_scholar=False,
        editorial_only=False,
        hard_forbid_research=False,
        hard_forbid_scholar=False,
    )
    assert out.mode == MODE_NONE


def test_forbid_scholar_wins():
    out = decide(
        "renforce cette partie avec plus d'arguments",
        intents=[ImprovementIntent.ARGUMENTATION],
        forbid_scholar=True,
    )
    assert out.mode == MODE_NONE


def test_forbid_research_wins_over_strengthening():
    out = decide(
        "renforce les arguments sans nouvelle recherche",
        intents=[ImprovementIntent.ARGUMENTATION],
        forbid_research=True,
    )
    assert out.mode in {MODE_NONE, MODE_REUSE}
