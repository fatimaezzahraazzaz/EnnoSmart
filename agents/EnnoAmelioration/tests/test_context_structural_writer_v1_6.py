from agents.EnnoAmelioration.application.section_improvement_policy import (
    render_section_improvement_contract,
)
from agents.EnnoAmelioration.application.writer_service import ControlledWriter
from agents.EnnoAmelioration.domain.models import (
    ImprovementIntent,
    ImprovementRequest,
    RoutingDecision,
    SectionFunction,
    SpecialistRoute,
    TargetScope,
)


class FakeLLM:
    def __init__(self) -> None:
        self.prompt = ""

    def generate(self, prompt, **kwargs):
        self.prompt = prompt
        # Le contenu n'a pas d'importance ici : on teste le contrat envoyé au writer.
        return (
            "Le contexte général est présenté. Le besoin est ensuite explicité. "
            "La limitation documentée conduit enfin à la motivation des travaux."
        )

    def get_last_generation_meta(self):
        return {"provider": "fake"}


def _request(instruction: str) -> ImprovementRequest:
    text = (
        "Le domaine est présenté avec plusieurs éléments descriptifs. "
        "Les méthodes nécessitent des données. Les données disponibles sont limitées. "
        "Une solution de simulation est envisagée. Cette solution présente une limite. "
        "C'est dans ce contexte que les travaux sont réalisés."
    )
    return ImprovementRequest(
        instruction=instruction,
        full_text=text,
        target_text=text,
        target_scope=TargetScope.SECTION,
        target_section_id="1.2.1",
        target_section_title="Contexte de l'opération",
    )


def _routing(*, candidate_revision: bool = False) -> RoutingDecision:
    intents = [ImprovementIntent.CLARITY, ImprovementIntent.ARGUMENTATION]
    if candidate_revision:
        intents.insert(0, ImprovementIntent.CANDIDATE_REVISION)
    return RoutingDecision(
        intents=intents,
        target_scope=TargetScope.SECTION,
        specialist_route=SpecialistRoute.WRITER,
        section_function=SectionFunction.CONTEXT,
        section_confidence=0.99,
        semantic_classifier="test",
        candidate_revision=candidate_revision,
        forbids_new_research=True,
        forbids_scholar=True,
    )


def test_context_policy_demands_block_level_rewriting_not_sentence_paraphrase():
    contract = render_section_improvement_contract(SectionFunction.CONTEXT)
    assert "blocs d'idées" in contract
    assert "ne constituent pas des unités de rédaction à paraphraser une par une" in contract
    assert "cause → conséquence" in contract
    assert "pas la correspondance phrase-à-phrase" in contract


def test_initial_context_rewrite_uses_structural_mode():
    llm = FakeLLM()
    writer = ControlledWriter(llm=llm)
    _, meta = writer.rewrite(
        _request("Améliore cette section pour clarifier son contexte et son argumentation."),
        _routing(),
        [],
        {},
    )
    assert meta["rewrite_mode"] == "context_structural"
    assert "MODE RESTRUCTURATION CONTEXTE À FAITS CONSTANTS" in llm.prompt
    assert "les phrases de la source ne sont pas des unités à conserver ou paraphraser une par une" in llm.prompt
    assert "3 à 6 blocs cohérents" in llm.prompt
    assert "Ne fais pas une simple substitution de synonymes phrase par phrase" in llm.prompt
    assert "Le renforcement vient d'abord de la structure" in llm.prompt


def test_context_structural_mode_forbids_intensification_and_precision_drift():
    llm = FakeLLM()
    writer = ControlledWriter(llm=llm)
    writer.rewrite(_request("Rends le contexte plus clair."), _routing(), [], {})
    assert "N'intensifie pas le texte" in llm.prompt
    assert "ne transforme pas « résolution du système »" in llm.prompt
    assert "« un logiciel » en « plusieurs logiciels »" in llm.prompt


def test_candidate_revision_keeps_differential_mode_instead_of_full_context_restructure():
    llm = FakeLLM()
    writer = ControlledWriter(llm=llm)
    _, meta = writer.rewrite(
        _request("Corrige uniquement les formulations trop fortes et garde le reste."),
        _routing(candidate_revision=True),
        [],
        {},
    )
    assert meta["rewrite_mode"] == "candidate_revision"
    assert "MODE CORRECTION DE LA PROPOSITION COURANTE" in llm.prompt
    assert "MODE RESTRUCTURATION CONTEXTE À FAITS CONSTANTS" not in llm.prompt
