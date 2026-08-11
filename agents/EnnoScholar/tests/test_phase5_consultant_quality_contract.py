from __future__ import annotations

import json

from agents.EnnoScholar.state_of_art import (
    phase_5_state_of_art_writer_service as phase5,
)
from agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import (
    _build_section_llm_prompt,
    _build_targeted_section_repair_prompt,
    _evidence_sentence,
    _build_missing_citation_repair_prompt,
    _non_french_raw_fragments,
    _publication_guard_for_new_llm,
    _repair_uncited_taxonomy_claims,
    _semantic_claim_audit,
    _validate_generated_section,
    build_unified_blueprint,
    citations_from_text,
    extract_evidence_units,
    validate_draft,
)


def _card(citation: str, title: str, method: str) -> dict:
    return {
        "citation_label": citation,
        "title": title,
        "method_name": method,
        "method": method,
        "technical_principle": f"{method} is the method studied by this paper.",
        "results": f"The experiments evaluate {method}.",
    }


def _unit(citation: str, text: str, kind: str = "method") -> dict:
    return {
        "evidence_id": f"{citation}-{kind}",
        "citation_label": citation,
        "kind": kind,
        "text": text,
        "verrou_ids": [],
        "source": "article_card",
        "source_path": f"article_card.{citation}",
        "citation_ownership": "explicit",
        "source_kind": "scientific_article",
        "article_title": text,
        "article_method_name": text.split()[0],
    }


def test_atomic_evidence_does_not_propagate_aggregate_citations() -> None:
    cards = [
        _card(
            "A12",
            "Soft Segmented Randomization for domain generalization",
            "Soft Segmented Randomization (SSR)",
        ),
        {
            **_card(
                "A17",
                "Hierarchical self-supervised learning",
                "Hierarchical Self-Supervised Learning (SSL)",
            ),
            "article_evidence_bank": {
                "paragraph_buckets": {
                    "related_work": [
                        {
                            "text": (
                                "[7] proposed Soft Segmented Randomization "
                                "(SSR) for domain generalization."
                            )
                        }
                    ]
                }
            },
        },
    ]
    reasoning = {
        "reasoning_sections": {
            "reasoning": "SSR is explained in a multi-source synthesis.",
            "citation_strategy": "Use [A12] and [A17].",
            "required_citations": ["A12", "A17"],
        }
    }

    units = extract_evidence_units(reasoning, {}, cards)

    a12_text = " ".join(
        row["text"] for row in units if row["citation_label"] == "A12"
    )
    a17_text = " ".join(
        row["text"] for row in units if row["citation_label"] == "A17"
    )
    assert "Soft Segmented Randomization" in a12_text
    assert "Soft Segmented Randomization" not in a17_text
    assert "Self-Supervised Learning" in a17_text


def test_blueprint_never_promotes_related_source_to_direct_proof() -> None:
    phase47 = {
        "canonical_verrous": [
            {
                "verrou_id": "V1",
                "verrou_title": "Validation du solveur cible",
            }
        ],
        "verrou_sections_for_phase5": [
            {
                "verrou_id": "V1",
                "verrou_title": "Validation du solveur cible",
                "state_of_art_gap": "La validation directe reste à établir.",
                "citation_coverage": {
                    "direct_citations": [],
                    "related_citations": ["A2"],
                    "methodological_citations": [],
                    "background_citations": [],
                },
            }
        ],
    }
    cards = [
        _card("A1", "Directly unrelated method", "Method Alpha"),
        _card("A2", "Related numerical solver", "Method Beta"),
    ]
    evidence = [
        _unit("A1", "Method Alpha is evaluated for another task."),
        _unit("A2", "Method Beta is a related numerical solver."),
    ]
    approved_plan = [
        {
            "section_id": "gap",
            "title": "Gap scientifique",
            "objective": "Identifier les limites des preuves disponibles.",
            "order": 1,
            "level": 1,
            "parent_id": None,
            "verrou_ids": ["V1"],
        }
    ]

    blueprint = build_unified_blueprint(
        organisme="org",
        project="project",
        year="2026",
        reasoning_payload={},
        phase46_payload={},
        phase47_payload=phase47,
        project_context={},
        style_memory={},
        article_cards=cards,
        evidence_units=evidence,
        approved_plan=approved_plan,
    )

    verrou = blueprint["sections"][0]["verrous"][0]
    assert verrou["evidence_status"] == "insufficient_direct_evidence"
    assert verrou["required_citations"] == []
    assert verrou["related_citations"] == ["A2"]
    assert verrou["requires_insufficiency_disclosure"] is True
    assert blueprint["source_roles"]["A2"] == "scientific_source"
    assert blueprint["evidence_roles_by_verrou"]["V1"][
        "related_citations"
    ] == ["A2"]


def test_sources_are_reusable_and_exact_corpus_is_exhaustive() -> None:
    phase47 = {
        "canonical_verrous": [
            {"verrou_id": "V1", "verrou_title": "Robustesse"}
        ],
        "verrou_sections_for_phase5": [
            {
                "verrou_id": "V1",
                "verrou_title": "Robustesse",
                "citation_coverage": {
                    "direct_citations": ["A1"],
                    "related_citations": ["A2"],
                },
            }
        ],
    }
    cards = [
        _card("A1", "Method Alpha", "Method Alpha"),
        _card("A2", "Survey Beta", "Survey Beta"),
    ]
    evidence = [
        _unit("A1", "Method Alpha improves robustness."),
        _unit("A2", "Survey Beta defines the evaluation landscape."),
    ]
    approved_plan = [
        {
            "section_id": "methods",
            "title": "Méthodes existantes",
            "objective": "Expliquer les méthodes.",
            "order": 1,
            "level": 1,
            "parent_id": None,
            "verrou_ids": [],
        },
        {
            "section_id": "limits",
            "title": "Limites et gap",
            "objective": "Comparer les limites.",
            "order": 2,
            "level": 1,
            "parent_id": None,
            "verrou_ids": ["V1"],
        },
    ]

    blueprint = build_unified_blueprint(
        organisme="org",
        project="project",
        year="2026",
        reasoning_payload={},
        phase46_payload={},
        phase47_payload=phase47,
        project_context={},
        style_memory={},
        article_cards=cards,
        evidence_units=evidence,
        approved_plan=approved_plan,
        require_all_selected_sources=True,
    )

    assert all(
        section["available_citations"] == ["A1", "A2"]
        for section in blueprint["sections"]
    )
    assert blueprint["required_citations"] == ["A1", "A2"]


def test_semantic_audit_rejects_wrong_method_source() -> None:
    section = {"section_id": "methods", "verrous": []}
    evidence = [
        _unit(
            "A17",
            "Hierarchical Self-Supervised Learning (SSL) learns representations.",
        )
    ]
    generated = {
        "section_id": "methods",
        "content": (
            "Soft Segmented Randomization (SSR) applies a Gaussian mixture "
            "model (GMM) [A17]."
        ),
        "subsections": [],
    }

    report = _semantic_claim_audit(generated, section, evidence)

    assert report["ok"] is False
    unsupported = {
        row["unsupported_entity"] for row in report["entity_mismatches"]
    }
    assert {"SSR", "GMM"} <= unsupported


def test_grouped_citations_are_parsed_and_stay_with_the_claim() -> None:
    assert citations_from_text(
        "Le résultat converge [A11, A16, A18]."
    ) == ["A11", "A16", "A18"]
    assert (
        _evidence_sentence(
            _unit("A11", "The protocol converges.")
        )
        == "The protocol converges [A11]."
    )

    section = {"section_id": "methods", "verrous": []}
    evidence = [
        _unit("A11", "Method Alpha improves robustness."),
        _unit("A16", "Method Alpha is evaluated on measured data."),
    ]
    generated = {
        "section_id": "methods",
        "content": (
            "Method Alpha improves robustness and is evaluated on measured "
            "data [A11, A16]."
        ),
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_acronym_is_supported_by_its_explicit_expansion() -> None:
    section = {"section_id": "methods", "verrous": []}
    evidence = [
        _unit(
            "A23",
            "Automatic Target Recognition identifies target classes.",
        )
    ]
    generated = {
        "section_id": "methods",
        "content": "L’ATR identifie automatiquement les cibles [A23].",
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_expanded_entity_is_supported_by_acronym_in_evidence() -> None:
    section = {"section_id": "methods", "verrous": []}
    evidence = [
        _unit(
            "A35",
            "The solver uses RWG basis functions.",
        )
    ]
    generated = {
        "section_id": "methods",
        "content": (
            "Le solveur utilise des fonctions de base "
            "Rao-Wilton-Glisson [A35]."
        ),
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_composite_label_is_not_supported_by_separate_acronyms() -> None:
    section = {"section_id": "methods", "verrous": []}
    evidence = [
        _unit(
            "A24",
            "The method combines SBR simulation and a GAN.",
        )
    ]
    generated = {
        "section_id": "methods",
        "content": "La méthode SBR-GAN augmente les données [A24].",
        "subsections": [],
    }

    report = _semantic_claim_audit(generated, section, evidence)

    assert report["ok"] is False
    assert {
        row["unsupported_entity"] for row in report["entity_mismatches"]
    } == {"SBR-GAN"}


def test_generated_section_rejects_raw_page_counter_fragment() -> None:
    section = {
        "section_id": "methods",
        "title": "Familles de méthodes",
        "available_citations": ["A24"],
        "required_citations": ["A24"],
        "target_words": 350,
        "verrous": [],
    }
    generated = {
        "section_id": "methods",
        "title": "Familles de méthodes",
        "content": (
            "Cette méthode décrit une procédure documentée [A24]. "
            + "Phrase scientifique étayée [A24]. " * 55
            + "2024, 16, 4427 9 of 31 Figure 4 [A24]."
        ),
        "subsections": [],
    }

    report = _validate_generated_section(
        generated,
        section,
        evidence_units=[
            _unit(
                "A24",
                "This method describes a documented scientific procedure.",
            )
        ],
    )

    assert report["ok"] is False
    assert "raw_extraction_fragment" in report["errors"]
    assert report["raw_extraction_fragments"]


def test_raw_english_article_excerpt_is_detected_but_french_is_not() -> None:
    english = (
        "In this part, we demonstrate the application of the proposed method "
        "and this paper presents the results obtained with our approach."
    )
    french = (
        "Cette section analyse la méthode proposée et présente les résultats "
        "obtenus dans les conditions expérimentales documentées."
    )

    assert _non_french_raw_fragments(english)
    assert _non_french_raw_fragments(french) == []


def test_publication_guard_keeps_new_llm_semantic_advice_without_hiding_it() -> None:
    report = _publication_guard_for_new_llm(
        {
            "ok": False,
            "passed": False,
            "errors": ["unsupported_or_misattributed_claims"],
            "semantic_claims_ok": False,
        }
    )

    assert report["ok"] is True
    assert report["errors"] == []
    assert report["strict_ok"] is False
    assert report["advisory_errors"] == [
        "unsupported_or_misattributed_claims"
    ]
    assert report["new_llm_draft_preserved"] is True


def test_publication_guard_still_blocks_structural_or_unknown_citation_errors() -> None:
    report = _publication_guard_for_new_llm(
        {
            "ok": False,
            "passed": False,
            "errors": [
                "unknown_citations",
                "unsupported_or_misattributed_claims",
            ],
        }
    )

    assert report["ok"] is False
    assert report["errors"] == ["unknown_citations"]
    assert report["advisory_errors"] == [
        "unsupported_or_misattributed_claims"
    ]


def test_targeted_repair_prompt_is_smaller_than_full_writer_prompt() -> None:
    section = {
        "section_id": "limits",
        "title": "Limites et gap scientifique",
        "objective": "Nuancer les limites des méthodes.",
        "available_citations": [f"A{i}" for i in range(1, 13)],
        "required_citations": ["A1"],
        "target_words": 650,
        "verrous": [],
    }
    blueprint = {
        "project": "Projet de test",
        "sections": [section],
        "project_context": {"objective": "Évaluer une méthode."},
        "source_roles": {},
    }
    evidence = [
        _unit(
            f"A{citation}",
            f"Method {citation} reports a documented protocol and result {row}.",
            kind=("method", "protocol", "result", "limitation")[row % 4],
        )
        for citation in range(1, 13)
        for row in range(10)
    ]
    draft = {
        "section_id": "limits",
        "title": "Limites et gap scientifique",
        "content": "La méthode 1 est décrite dans le corpus [A1].",
        "subsections": [],
    }
    validation = {
        "errors": ["unsupported_or_misattributed_claims"],
        "semantic_claim_audit": {
            "issues": [
                {
                    "type": "citation_entity_mismatch",
                    "location": "section",
                    "claim": "La méthode inconnue est validée [A1].",
                    "citations": ["A1"],
                    "unsupported_entity": "Inconnue",
                }
            ]
        },
    }

    full_prompt = _build_section_llm_prompt(
        blueprint,
        section,
        evidence,
        previous_tail="",
    )
    repair_prompt = _build_targeted_section_repair_prompt(
        blueprint,
        section,
        draft,
        validation,
        evidence,
    )

    assert "NOUVELLE RÉDACTION À CORRIGER" in repair_prompt
    assert len(repair_prompt) < len(full_prompt) * 0.65


def test_sectional_writer_keeps_latest_new_llm_text_never_raw_fallback(
    monkeypatch,
) -> None:
    class FakeClient:
        responses: list[str] = []
        prompts: list[str] = []

        def __init__(self, model=None) -> None:
            self.model = model
            self.read_timeout = 300
            self._meta = {}

        def generate(self, prompt: str, **kwargs) -> str:
            self.prompts.append(prompt)
            self._meta = {
                "model": self.model or "test-model",
                "prompt_tokens": len(prompt) // 4,
                "completion_tokens": 500,
                "total_tokens": len(prompt) // 4 + 500,
            }
            return self.responses.pop(0)

        def get_last_generation_meta(self) -> dict:
            return dict(self._meta)

    repeated = " ".join(
        "La méthode Alpha décrit un protocole scientifique documenté [A1]."
        for _ in range(42)
    )
    first = {
        "section_id": "methods",
        "title": "Méthodes analysées",
        "content": repeated + " La méthode Beta est validée [A1].",
        "subsections": [],
    }
    second = {
        "section_id": "methods",
        "title": "Méthodes analysées",
        "content": repeated + " La méthode Gamma est validée [A1].",
        "subsections": [],
    }
    FakeClient.responses = [
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ]
    FakeClient.prompts = []
    monkeypatch.setattr(phase5, "LLMClient", FakeClient)
    monkeypatch.setattr(phase5, "reload_config", lambda: {})
    monkeypatch.setenv("ENNOSCHOLAR_PHASE5_ENABLE_LLM", "1")
    monkeypatch.setenv("ENNOSCHOLAR_PHASE5_SECTION_ATTEMPTS", "2")
    monkeypatch.setenv(
        "ENNOSCHOLAR_PHASE5_ENABLE_INDEPENDENT_VERIFIER",
        "0",
    )
    monkeypatch.setenv("ENNOSCHOLAR_PHASE5_REUSE_SECTION_CHECKPOINTS", "0")
    blueprint = {
        "project": "Projet de test",
        "sections": [
            {
                "section_id": "methods",
                "title": "Méthodes analysées",
                "objective": "Analyser les méthodes.",
                "target_words": 350,
                "available_citations": ["A1"],
                "required_citations": ["A1"],
                "verrous": [],
            }
        ],
        "project_context": {},
        "style_memory": {},
        "source_roles": {"A1": "scientific_source"},
    }
    evidence = [
        _unit(
            "A1",
            "Method Alpha describes a documented scientific protocol.",
        )
    ]

    draft, report = phase5.call_sectional_writer_llm(
        blueprint,
        evidence,
    )

    assert len(FakeClient.prompts) == 2
    assert "NOUVELLE RÉDACTION À CORRIGER" in FakeClient.prompts[1]
    assert "Gamma" in draft["sections"][0]["content"]
    assert "Beta" not in draft["sections"][0]["content"]
    assert report["mode"] == "sectional_llm_with_advisories"
    assert report["deterministic_fallback_sections_count"] == 0
    assert report["all_sections_generated_by_llm"] is True
    assert all(
        attempt.get("attempt") != "deterministic_fallback"
        for attempt in report["sections"][0]["attempts"]
    )


def test_only_taxonomy_claims_receive_planner_required_citations() -> None:
    section = {
        "section_id": "methods",
        "title": "Familles de méthodes",
        "available_citations": ["A14", "A24", "A35"],
        "required_citations": ["A14", "A24", "A35"],
        "verrous": [],
    }
    generated = {
        "section_id": "methods",
        "title": "Familles de méthodes",
        "content": (
            "Les travaux se structurent en trois familles méthodologiques. "
            "La première famille mobilise des approches de simulation."
        ),
        "subsections": [],
    }
    validation = {
        "semantic_claim_audit": {
            "issues": [
                {
                    "type": "uncited_scientific_claim",
                    "location": "section",
                    "claim": (
                        "Les travaux se structurent en trois familles "
                        "méthodologiques."
                    ),
                }
            ]
        }
    }

    repaired, claims = _repair_uncited_taxonomy_claims(
        generated,
        section,
        validation,
    )

    assert claims
    assert (
        "trois familles méthodologiques [A14, A24, A35]."
        in repaired["content"]
    )
    assert (
        "La première famille mobilise des approches de simulation."
        in repaired["content"]
    )


def test_specific_uncited_method_claim_is_not_auto_cited() -> None:
    section = {
        "required_citations": ["A24"],
    }
    generated = {
        "content": "La méthode NewMethod atteint 99 % de précision.",
    }
    validation = {
        "semantic_claim_audit": {
            "issues": [
                {
                    "type": "uncited_scientific_claim",
                    "location": "section",
                    "claim": "La méthode NewMethod atteint 99 % de précision.",
                }
            ]
        }
    }

    repaired, claims = _repair_uncited_taxonomy_claims(
        generated,
        section,
        validation,
    )

    assert claims == []
    assert repaired == generated


def test_missing_citation_repair_prompt_is_evidence_bounded() -> None:
    prompt = _build_missing_citation_repair_prompt(
        {
            "objective": "Comparer les familles de méthodes.",
        },
        "Contenu existant [A14].",
        [
            _unit("A14", "Evidence that must stay outside the repair."),
            _unit("A24", "SBR simulation and GAN augment SAR images."),
        ],
        ["A24"],
    )

    assert '"A24"' in prompt
    assert "SBR simulation and GAN augment SAR images." in prompt
    assert "Evidence that must stay outside the repair." not in prompt
    assert "ne copie aucun fragment OCR" in prompt


def test_explicit_bilingual_acronym_expansion_is_not_a_new_entity() -> None:
    section = {"section_id": "methods", "verrous": []}
    evidence = [
        _unit(
            "A15",
            "CAD target models support synthetic data generation.",
        )
    ]
    generated = {
        "section_id": "methods",
        "content": (
            "Les modèles CAO (Computer-Aided Design, CAD) décrivent les "
            "cibles synthétiques [A15]."
        ),
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_localized_expansion_before_acronym_is_not_split_into_entities() -> None:
    section = {"section_id": "methods", "verrous": []}
    evidence = [
        _unit(
            "A20",
            "Synthetic Aperture Radar (SAR) supports target recognition.",
        )
    ]
    generated = {
        "section_id": "methods",
        "content": (
            "Les images Radar à Synthèse d’Ouverture (SAR) sont utilisées "
            "pour la reconnaissance de cibles [A20]."
        ),
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_semantic_audit_does_not_drop_late_cited_evidence() -> None:
    section = {"section_id": "methods", "verrous": []}
    evidence = [
        _unit("A1", "generic background " * 30000),
        _unit("A2", "LateMethod controls the final solver."),
    ]
    generated = {
        "section_id": "methods",
        "content": "LateMethod controls the final solver [A1, A2].",
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_semantic_audit_requires_direct_evidence_disclosure() -> None:
    section = {
        "section_id": "gap",
        "verrous": [
            {
                "verrou_id": "V1",
                "verrou_title": "Validation du système cible",
                "evidence_status": "insufficient_direct_evidence",
                "requires_insufficiency_disclosure": True,
                "direct_citations": [],
                "related_citations": ["A2"],
            }
        ],
    }
    evidence = [_unit("A2", "Method Beta is validated on a different system.")]
    invalid = {
        "section_id": "gap",
        "content": "",
        "subsections": [
            {
                "verrou_id": "V1",
                "title": "Validation du système cible",
                "content": "Method Beta validates the target system [A2].",
            }
        ],
    }
    valid = {
        **invalid,
        "subsections": [
            {
                "verrou_id": "V1",
                "title": "Validation du système cible",
                "content": (
                    "Le corpus ne fournit pas de preuve directe pour "
                    "TargetSolver. Method Beta est seulement un cadrage "
                    "connexe sur un autre système [A2]."
                ),
            }
        ],
    }

    invalid_report = _semantic_claim_audit(invalid, section, evidence)
    valid_report = _semantic_claim_audit(valid, section, evidence)

    assert invalid_report["missing_direct_evidence_disclosures"]
    assert not valid_report["missing_direct_evidence_disclosures"]


def test_negated_scope_statement_may_name_an_absent_target() -> None:
    section = {"section_id": "limits", "verrous": []}
    evidence = [
        _unit(
            "A35",
            "MLFMA models electromagnetic scattering by large structures.",
        )
    ]
    generated = {
        "section_id": "limits",
        "content": (
            "La source ne décrit pas explicitement l’intégration de cette "
            "méthode dans une chaîne ATR [A35]."
        ),
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_open_research_gap_may_name_an_absent_target() -> None:
    section = {"section_id": "limits", "verrous": []}
    evidence = [
        _unit(
            "A35",
            "MLFMA models electromagnetic scattering by large structures.",
        )
    ]
    generated = {
        "section_id": "limits",
        "content": (
            "Le lien de cette méthode avec la généralisation ATR reste à "
            "explorer [A35]."
        ),
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_impossibility_scope_may_name_an_absent_target() -> None:
    section = {"section_id": "limits", "verrous": []}
    evidence = [
        _unit(
            "A35",
            "MLFMA models electromagnetic scattering by large structures.",
        )
    ]
    generated = {
        "section_id": "limits",
        "content": (
            "Il n’est donc pas possible d’étendre ces résultats aux "
            "performances ATR [A35]."
        ),
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_nothing_allows_conclusion_may_name_an_absent_target() -> None:
    section = {"section_id": "limits", "verrous": []}
    evidence = [
        _unit(
            "A35",
            "MLFMA models electromagnetic scattering by large structures.",
        )
    ]
    generated = {
        "section_id": "limits",
        "content": (
            "Rien ne permet donc de conclure à une amélioration ATR "
            "[A35]."
        ),
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_no_evidence_establishes_scope_may_name_an_absent_target() -> None:
    section = {"section_id": "limits", "verrous": []}
    evidence = [
        _unit(
            "A35",
            "MLFMA models electromagnetic scattering by large structures.",
        )
    ]
    generated = {
        "section_id": "limits",
        "content": (
            "Aucune preuve apportée n’établit explicitement l’utilisation "
            "de cette simulation dans une chaîne ATR [A35]."
        ),
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_source_presents_no_evidence_may_name_an_absent_target() -> None:
    section = {"section_id": "limits", "verrous": []}
    evidence = [
        _unit(
            "A35",
            "MLFMA models electromagnetic scattering by large structures.",
        )
    ]
    generated = {
        "section_id": "limits",
        "content": (
            "La source ne présente aucune preuve explicite d’une validation "
            "ATR [A35]."
        ),
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_passive_unestablished_scope_may_name_an_absent_target() -> None:
    section = {"section_id": "limits", "verrous": []}
    evidence = [
        _unit(
            "A35",
            "MLFMA models electromagnetic scattering by large structures.",
        )
    ]
    generated = {
        "section_id": "limits",
        "content": (
            "L’adéquation à l’apprentissage ATR n’est pas formellement "
            "documentée et reste ouverte [A35]."
        ),
        "subsections": [],
    }

    assert _semantic_claim_audit(generated, section, evidence)["ok"] is True


def test_global_guard_compares_normalized_consultant_titles() -> None:
    blueprint = {
        "sections": [
            {
                "section_id": "problem",
                "title": "Problématique : Validité du modèle",
                "verrous": [],
            }
        ],
        "allowed_citations": [],
        "required_citations": [],
        "available_evidence_citations": [],
        "required_citations_by_verrou": {},
    }
    draft = {
        "sections": [
            {
                "section_id": "problem",
                "title": "Problématique : Validité du modèle",
                "content": "Le contexte du projet définit la question à examiner.",
                "subsections": [],
            }
        ]
    }

    guard = validate_draft(draft, blueprint)

    assert guard["ok"] is True
    assert "section_titles_mismatch" not in guard["errors"]
