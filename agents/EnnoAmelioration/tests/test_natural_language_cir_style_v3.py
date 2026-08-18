from __future__ import annotations

import json

from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.application.agent import EnnoAmeliorationAgent
from agents.EnnoAmelioration.application.markdown_service import (
    normalize_llm_markdown_output,
)
from agents.EnnoAmelioration.application.semantic_routing_service import (
    SemanticRoutingService,
)
from agents.EnnoAmelioration.application.section_parser import parse_sections, resolve_target
from agents.EnnoAmelioration.application.traceability_service import (
    build_revision_trace,
)
from agents.EnnoAmelioration.application.writer_service import (
    ControlledWriter,
    _compact_json,
    _editorial_semantic_scope_risks,
    _editorial_text_similarity,
    _preserve_leading_heading,
)
from agents.EnnoAmelioration.domain.models import (
    ImprovementIntent,
    ImprovementRequest,
    SectionFunction,
    TargetScope,
)


class RecordingLLM:
    def __init__(self, output: str) -> None:
        self.output = output
        self.prompts: list[str] = []

    def generate(self, prompt: str, **_: object) -> str:
        self.prompts.append(prompt)
        return self.output

    def get_last_generation_meta(self) -> dict[str, str]:
        return {"provider": "recording-test"}


class OneCandidateWriter:
    llm = None

    def __init__(self, candidate: str) -> None:
        self.candidate = candidate
        self.calls = 0

    def rewrite(self, *_: object, **__: object) -> tuple[str, dict[str, str]]:
        self.calls += 1
        return self.candidate, {"provider": "candidate-test"}


def test_natural_formulation_request_is_editorial_writer_only():
    decision = understand_instruction(
        "Peux-tu simplement améliorer la formulation de ce passage ?",
        TargetScope.SECTION,
    )

    assert ImprovementIntent.CLARITY in decision.intents
    assert ImprovementIntent.STYLE in decision.intents
    assert ImprovementIntent.GENERAL_REVISION not in decision.intents
    assert decision.editorial_only is True
    assert decision.needs_diagnostic is False
    assert decision.needs_scholar is False


def test_unaccented_typo_still_detects_style_and_argumentation():
    decision = understand_instruction(
        "ameliore la redaction style cir r&d et ajoute plus de justicifation qui donne de la valeur",
        TargetScope.SECTION,
    )

    assert ImprovementIntent.STYLE in decision.intents
    assert ImprovementIntent.ARGUMENTATION in decision.intents
    assert decision.editorial_only is False
    assert decision.needs_diagnostic is True
    assert decision.needs_scholar is False


def test_rewrite_without_invention_activates_fact_preservation():
    decision = understand_instruction(
        "Réécris ce texte sans rien inventer et sans modifier le fond.",
        TargetScope.SECTION,
    )

    assert decision.editorial_only is True
    assert decision.strict_fact_preservation is True
    assert decision.needs_diagnostic is False
    assert decision.needs_scholar is False


def test_project_only_natural_phrase_never_routes_to_scholar():
    decision = understand_instruction(
        "Ajoute des arguments solides à partir du dossier uniquement.",
        TargetScope.SECTION,
    )

    assert ImprovementIntent.ARGUMENTATION in decision.intents
    assert decision.needs_diagnostic is True
    assert decision.needs_scholar is False
    assert decision.forbids_new_research is True
    assert decision.forbids_scholar is True


def test_explicit_argumentation_routes_uncertainty_section_to_diagnostic(monkeypatch):
    service = SemanticRoutingService()
    monkeypatch.setattr(
        service,
        "_fastjudge",
        lambda rows: {
            "target": {
                "function": SectionFunction.UNCERTAINTY,
                "confidence": 0.99,
                "classifier": "test",
            }
        },
    )
    monkeypatch.setattr(service, "_semantic_functions", lambda rows: {})

    decision = service.route(
        "Renforce l'argumentation R&D à partir du dossier uniquement.",
        TargetScope.SECTION,
        "Le projet rencontre une variabilité documentée entre trois configurations.",
    )

    assert decision.section_function == SectionFunction.UNCERTAINTY
    assert decision.needs_diagnostic is True
    assert decision.needs_scholar is False


def test_writer_injects_selected_cir_guide_separately_from_factual_evidence():
    source = (
        "Le banc comprend trois capteurs. Les mesures sont réalisées toutes les 10 ms. "
        "Deux séries d'essais ont été conduites."
    )
    request = ImprovementRequest(
        instruction="Améliore uniquement la formulation dans un style consultant CIR/R&D.",
        full_text=source,
        target_text=source,
        target_scope=TargetScope.SECTION,
    )
    routing = understand_instruction(request.instruction, request.target_scope)
    evidence = {
        "project_context": {"project_name": "Projet test"},
        "constraints": {"no_hallucination": True},
        "cir_style": {
            "available": True,
            "fact_eligible": False,
            "style_profile": {
                "tone": "TON_MEMOIRE_CIR_TEST",
                "tone_details": ["scientifique", "prudent", "non promotionnel"],
                "style_constraints": {"sentence_style": "phrases structurées"},
                "writing_rules": [
                    "Utiliser la mémoire uniquement pour le ton, la structure et les transitions.",
                    "Ne jamais copier les faits historiques des anciens CIR.",
                ],
            },
            "argumentation_profile": {},
            "fewshot_templates": [],
        },
    }
    llm = RecordingLLM(source)

    _, meta = ControlledWriter(llm=llm).rewrite(request, routing, [], evidence)

    first_prompt = llm.prompts[0]
    assert "GUIDE RÉDACTIONNEL CIR/R&D — NON FACTUEL" in first_prompt
    assert "TON_MEMOIRE_CIR_TEST" in first_prompt
    assert "PREUVES FACTUELLES ENNODIAGNOSTIC" in first_prompt
    assert "PREUVES FACTUELLES ENNOSCHOLAR" in first_prompt
    assert meta["cir_style_guidance_injected"] is True
    assert meta["cir_style_pattern_ids"]
    assert evidence["cir_style"]["guidance_injected"] is True

    trace = build_revision_trace(
        source,
        source.replace("comprend", "comporte"),
        routing,
        evidence,
    )
    assert "CIRStyleMemory" in trace["agents_used"]
    assert trace["cir_memory_used"] is True
    assert trace["cir_style_pattern_ids"] == meta["cir_style_pattern_ids"]


def test_single_loaded_section_is_resolved_without_manual_selection():
    source = """1.4.2.1. Motivation et démarche expérimentale

Le banc comprend trois capteurs. Deux séries d'essais ont été conduites."""
    sections = parse_sections(source)

    target, section = resolve_target(source, sections)

    assert len(sections) == 1
    assert section is not None
    assert section.title == "Motivation et démarche expérimentale"
    assert target == source


def test_editorial_writer_keeps_visible_candidate_for_consultant_review():
    source = (
        "Les travaux sont réalisés progressivement. Une première étape concerne "
        "la génération des données. Une deuxième étape concerne leur intégration."
    )
    candidate = (
        "Les travaux suivent une progression en deux étapes : la génération des "
        "données, puis leur intégration."
    )
    request = ImprovementRequest(
        instruction="Améliore la clarté et la fluidité sans changer les faits.",
        full_text=source,
        target_text=source,
        target_scope=TargetScope.SECTION,
    )
    routing = understand_instruction(request.instruction, request.target_scope)
    llm = RecordingLLM(candidate)

    improved, meta = ControlledWriter(llm=llm).rewrite(
        request,
        routing,
        [],
        {"cir_style": {"available": False}},
    )

    assert improved == candidate
    assert improved != source
    assert len(llm.prompts) == 1
    assert meta["editorial_validation_policy"] == "consultant_reviews_visible_candidate"
    assert meta["strict_editorial_safe_fallback_to_source"] is False


def test_agent_does_not_retry_or_hide_editorial_candidate_with_warnings():
    source = " ".join(
        [
            "Le protocole conserve les paramètres, les réserves et les observations documentées."
        ]
        * 20
    )
    candidate = "Le protocole présente les éléments documentés de façon plus lisible."
    request = ImprovementRequest(
        instruction="Améliore uniquement la formulation sans changer les faits.",
        full_text=source,
        target_text=source,
        target_scope=TargetScope.SECTION,
    )
    routing = understand_instruction(request.instruction, request.target_scope)
    writer = OneCandidateWriter(candidate)

    improved, meta = EnnoAmeliorationAgent(writer=writer)._rewrite_once_with_conservation(
        request,
        routing,
        [],
        {"cir_style": {"available": False}},
    )

    assert improved == candidate
    assert writer.calls == 1
    assert meta["strategy"] == "visible_editorial_candidate"
    assert meta["conservation_validation"] == "consultant_review"
    assert meta["conservation_issues"]


def test_compact_json_remains_valid_when_payload_is_truncated():
    compacted = _compact_json(
        {"diagnostic": [{"text": "preuve " * 2000} for _ in range(30)]},
        900,
    )

    assert len(compacted) <= 900
    assert isinstance(json.loads(compacted), dict)



def test_markdown_output_removes_html_space_entity():
    value = "### 1.2.1. Contexte\n\nPremier paragraphe. &#x20;\n\n\nDeuxième paragraphe."
    cleaned = normalize_llm_markdown_output(value)
    assert "&#x20;" not in cleaned
    assert cleaned == "### 1.2.1. Contexte\n\nPremier paragraphe.\n\nDeuxième paragraphe."


def test_leading_section_heading_is_restored_if_writer_omits_it():
    source = "### 1.2.1. Contexte de l’opération\n\nLe radar est un système actif."
    candidate = "Le radar constitue un système actif."
    restored = _preserve_leading_heading(source, candidate)
    assert restored.startswith("### 1.2.1. Contexte de l’opération\n\n")
    assert restored.endswith(candidate)


def test_new_temporal_qualifier_is_detected():
    source = (
        "Cette technique est aujourd’hui employée dans des systèmes imageurs tels que "
        "RADARSAT, TerraSAR-X ou le futur système Tandem-L."
    )
    candidate = (
        "Cette technique est intégrée dans plusieurs systèmes imageurs actuels, tels que "
        "RADARSAT, TerraSAR-X, ainsi que le futur système Tandem-L."
    )
    risks = _editorial_semantic_scope_risks(source, candidate)
    assert "portee_semantique_nouvelle:actuels" in risks



def test_editorial_similarity_ignores_whitespace_only():
    source = (
        "1.2.1. Contexte de l’opération\n"
        "Le radar est un système actif."
    )

    visually_wrapped = (
        "1.2.1. Contexte de l’opération\n\n"
        "Le radar est un système actif."
    )

    similarity = _editorial_text_similarity(
        source,
        visually_wrapped,
    )

    assert similarity >= 0.995


def test_semantic_review_must_not_collapse_to_original_source():
    source = (
        "1.2.1. Contexte de l’opération\n\n"
        "Les radars sont des systèmes basés sur "
        "l’émission et la réception d’ondes "
        "électromagnétiques. Cette technique est "
        "aujourd’hui employée dans des systèmes "
        "imageurs tels que RADARSAT, TerraSAR-X "
        "ou le futur système Tandem-L."
    )

    first_candidate = (
        "1.2.1. Contexte de l’opération\n\n"
        "Les radars reposent sur l’émission et la "
        "réception d’ondes électromagnétiques. "
        "Cette technique est intégrée dans plusieurs "
        "systèmes imageurs actuels, notamment "
        "RADARSAT, TerraSAR-X, ainsi que le futur "
        "système Tandem-L."
    )

    collapsed_review = source

    first_similarity = (
        _editorial_text_similarity(
            source,
            first_candidate,
        )
    )

    collapsed_similarity = (
        _editorial_text_similarity(
            source,
            collapsed_review,
        )
    )

    assert first_similarity < 0.995
    assert collapsed_similarity >= 0.995

    review_collapsed_to_source = (
        collapsed_similarity >= 0.995
        and first_similarity < 0.995
    )

    assert review_collapsed_to_source is True

from agents.EnnoAmelioration.application.writer_service import (
    _scientific_additive_review_is_safe,
    _scientific_additive_review_prompt,
    validate_conservative_revision,
)



def test_scientific_additive_review_rejects_collapse_to_source():
    source = (
        "La base SAMPLE contient des images mesurées et simulées. "
        "Elle est comparée à MSTAR."
    )
    candidate = (
        "La base SAMPLE contient des images mesurées et simulées. "
        "Elle est comparée à MSTAR. "
        "Des travaux montrent que le transfert synthétique-réel reste "
        "une difficulté de généralisation [A3]."
    )

    safe, reasons = _scientific_additive_review_is_safe(
        source,
        candidate,
        source,
    )

    assert safe is False
    assert "review_collapsed_to_source" in reasons


def test_scientific_additive_review_rejects_lost_validated_citation():
    source = "MSTAR contient des mesures SAR réelles utilisées pour l'entraînement."
    candidate = (
        source
        + " Les performances peuvent varier hors des conditions couvertes "
        "pendant l'apprentissage [A2]."
    )
    reviewed = (
        source
        + " Les performances peuvent varier hors des conditions couvertes "
        "pendant l'apprentissage."
    )

    safe, reasons = _scientific_additive_review_is_safe(
        source,
        candidate,
        reviewed,
    )

    assert safe is False
    assert any(
        reason.startswith("review_lost_validated_citations:A2")
        for reason in reasons
    )


def test_scientific_additive_review_accepts_source_plus_cited_argument():
    source = "La base SAMPLE contient des paires d'images mesurées et simulées."
    candidate = (
        source
        + " Cette structure permet d'étudier le passage entre données "
        "synthétiques et mesurées, un enjeu traité dans la littérature [A1]."
    )

    safe, reasons = _scientific_additive_review_is_safe(
        source,
        candidate,
        candidate,
    )

    assert safe is True
    assert reasons == []


def test_scientific_additive_prompt_requires_source_plus_additions():
    prompt = _scientific_additive_review_prompt(
        "Fait source à conserver.",
        "Fait source à conserver. Argument nouveau [A1].",
        ["A1"],
    )

    assert "SOURCE + AJOUTS" in prompt
    assert "ne doivent pas remplacer" in prompt
    assert "APPORT NOUVEAU" in prompt


def test_enrichment_cannot_be_net_contraction():
    source = " ".join(f"mot{i}" for i in range(130))
    proposal = " ".join(f"mot{i}" for i in range(125))

    issues = validate_conservative_revision(
        source,
        proposal,
        enrichment_requested=True,
        allow_reduction=False,
    )

    assert any(
        issue.startswith("contraction_excessive:")
        for issue in issues
    )
