from __future__ import annotations

from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.application.research_orchestration_service import (
    RESEARCH_LAUNCH_TARGETED,
    detect_research_choice,
    explicitly_forbids_research,
    explicitly_forbids_scholar,
)
from agents.EnnoAmelioration.domain.models import TargetScope

PROMPT = """Améliore uniquement la rédaction et la structure de cette section.
Conserve strictement toutes les informations techniques déjà présentes.
N'ajoute aucun nouvel argument scientifique.
Ne lance aucune recherche scientifique.
N'utilise aucune nouvelle source.
Ne lance pas EnnoScholar.
Propose uniquement une nouvelle version rédactionnelle."""

def test_explicit_prohibitions_are_detected():
    assert explicitly_forbids_research(PROMPT) is True
    assert explicitly_forbids_scholar(PROMPT) is True

def test_negative_research_instruction_is_not_a_research_choice():
    assert detect_research_choice(PROMPT) is None
    assert detect_research_choice('Sans nouvelle recherche, reformule ce passage.') is None
    assert detect_research_choice('Pas de nouvelle recherche.') is None

def test_editorial_prompt_keeps_absolute_forbid_flags():
    d = understand_instruction(PROMPT, TargetScope.SECTION)
    assert d.editorial_only is True
    assert d.needs_scholar is False
    assert d.needs_new_research is False
    assert d.forbids_new_research is True
    assert d.forbids_scholar is True

def test_explicit_action_id_is_still_a_real_user_choice():
    assert detect_research_choice(RESEARCH_LAUNCH_TARGETED) == RESEARCH_LAUNCH_TARGETED
