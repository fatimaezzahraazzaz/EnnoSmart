# -*- coding: utf-8 -*-
from agents.EnnoScholar.state_of_art.cir_quality_guard_v3 import (
    apply_cir_postprocessing,
    audit_cir_section,
    build_cir_evidence_matrix,
    classify_visual_role,
    filter_visual_placements,
)


def _blueprint():
    return {
        "verrous": [
            {
                "verrou_id": "V1",
                "verrou_title": "Généralisation synthétique réel",
            },
            {
                "verrou_id": "V2",
                "verrou_title": "Calage spéculaire",
            },
        ],
        "evidence_roles_by_verrou": {
            "V1": {
                "direct_citations": ["A2"],
                "related_citations": ["A3"],
                "methodological_citations": [],
                "background_citations": [],
            },
            "V2": {
                "direct_citations": ["A6"],
                "related_citations": ["A7"],
                "methodological_citations": ["A8"],
                "background_citations": [],
            },
        },
        "sections": [
            {
                "section_id": "s1",
                "title": "Généralisation synthétique réel",
                "verrous": [
                    {
                        "verrou_id": "V1",
                        "verrou_title": "Généralisation synthétique réel",
                    }
                ],
                "visual_requirements": [],
            },
            {
                "section_id": "s2",
                "title": "Calage spéculaire",
                "verrous": [
                    {
                        "verrou_id": "V2",
                        "verrou_title": "Calage spéculaire",
                    }
                ],
                "visual_requirements": [],
            },
        ],
    }


def _evidence():
    return [
        {
            "citation_label": "A2",
            "kind": "result",
            "verrou_ids": ["V1"],
            "text": (
                "La performance mesurée diminue lors du transfert "
                "synthétique vers réel."
            ),
        },
        {
            "citation_label": "A3",
            "kind": "method",
            "verrou_ids": ["V1"],
            "text": "Une augmentation SBR GAN est étudiée.",
        },
        # A6 est déclaré direct en Phase 4.7, mais ne contient ici
        # qu'une description de méthode: V3 le rétrograde.
        {
            "citation_label": "A6",
            "kind": "method",
            "verrou_ids": ["V2"],
            "text": "Le simulateur utilise GO et PO.",
        },
        {
            "citation_label": "A7",
            "kind": "method",
            "verrou_ids": [],
            "text": (
                "Une accélération matérielle du lancer de rayons "
                "est proposée."
            ),
        },
    ]


def test_matrix_confirms_direct_and_downgrades_method_only():
    matrix = build_cir_evidence_matrix(
        _blueprint(),
        _evidence(),
    )
    rows = {
        row["verrou_id"]: row
        for row in matrix["verrous"]
    }
    assert rows["V1"]["direct_confirmed_citations"] == ["A2"]
    assert rows["V1"]["strength"] == "MOYENNE"
    assert rows["V2"]["direct_confirmed_citations"] == []
    assert rows["V2"]["direct_declared_but_unconfirmed"] == ["A6"]
    assert rows["V2"]["strength"] == "FAIBLE"


def test_weak_verrou_overclaim_is_blocking():
    matrix = build_cir_evidence_matrix(
        _blueprint(),
        _evidence(),
    )
    section = _blueprint()["sections"][1]
    generated = {
        "section_id": "s2",
        "title": "Calage spéculaire",
        "content": "",
        "subsections": [
            {
                "verrou_id": "V2",
                "title": "Calage spéculaire",
                "content": (
                    "L'étude démontre que les directions spéculaires "
                    "provoquent une instabilité de calage [A6]."
                ),
            }
        ],
    }
    audit = audit_cir_section(
        generated,
        section,
        matrix,
    )
    assert audit["ok"] is False
    assert (
        audit["blocking_issues"][0]["type"]
        == "cir_related_evidence_overclaim"
    )


def test_weak_verrou_cautious_wording_passes():
    matrix = build_cir_evidence_matrix(
        _blueprint(),
        _evidence(),
    )
    section = _blueprint()["sections"][1]
    generated = {
        "section_id": "s2",
        "title": "Calage spéculaire",
        "content": "",
        "subsections": [
            {
                "verrou_id": "V2",
                "title": "Calage spéculaire",
                "content": (
                    "A6 décrit une méthode GO-PO [A6]. Le corpus "
                    "sélectionné ne fournit toutefois aucune preuve "
                    "directe permettant d'établir la stabilité du "
                    "calage dans les directions spéculaires."
                ),
            }
        ],
    }
    audit = audit_cir_section(
        generated,
        section,
        matrix,
    )
    assert audit["ok"] is True


def test_guided_mode_allows_documented_subproblem_but_not_full_lock_claim():
    matrix = build_cir_evidence_matrix(
        _blueprint(),
        _evidence(),
    )
    matrix["policy"]["guided_conversation"] = True
    section = _blueprint()["sections"][1]
    documented_subproblem = {
        "section_id": "s2",
        "title": "Calage spéculaire",
        "content": "",
        "subsections": [
            {
                "verrou_id": "V2",
                "title": "Calage spéculaire",
                "content": (
                    "Cette approche démontre la faisabilité technique du "
                    "lancer de rayons [A7]. Le corpus sélectionné ne fournit "
                    "aucune preuve scientifique directe permettant de "
                    "conclure sur ce verrou."
                ),
            }
        ],
    }
    assert audit_cir_section(
        documented_subproblem,
        section,
        matrix,
    )["ok"] is True

    full_lock_claim = {
        **documented_subproblem,
        "subsections": [
            {
                "verrou_id": "V2",
                "title": "Calage spéculaire",
                "content": "Cette méthode résout ce verrou [A7].",
            }
        ],
    }
    report = audit_cir_section(full_lock_claim, section, matrix)
    assert any(
        issue["type"] == "cir_related_evidence_overclaim"
        for issue in report["issues"]
    )


def test_parent_duplicate_removed_subsection_kept():
    blueprint = _blueprint()
    paragraph = (
        "La littérature montre une difficulté de transfert entre "
        "données synthétiques et mesurées [A2]."
    )
    draft = {
        "title": "Test",
        "sections": [
            {
                "section_id": "s1",
                "title": "Généralisation synthétique réel",
                "content": paragraph,
                "subsections": [
                    {
                        "verrou_id": "V1",
                        "title": "Généralisation synthétique réel",
                        "content": paragraph,
                    }
                ],
            }
        ],
    }
    fixed, report = apply_cir_postprocessing(
        draft,
        blueprint,
    )
    assert fixed["sections"][0]["subsections"][0]["content"]
    assert fixed["sections"][0]["content"] == ""
    assert report["changes_count"] >= 1


def test_project_visual_filtered_by_default():
    blueprint = _blueprint()
    matrix = build_cir_evidence_matrix(
        blueprint,
        _evidence(),
    )
    placements = [
        {
            "section_id": "s1",
            "visual_id": "VPROJECT",
            "citation_label": "",
            "caption": "Résultat interne du projet",
            "semantic_similarity": 0.9,
        }
    ]
    kept, report = filter_visual_placements(
        placements,
        blueprint,
        matrix,
    )
    assert kept == []
    assert report["rejected"][0]["role"] == "PROJECT_RESULT"


def test_cad_visual_is_method_not_evidence():
    assert (
        classify_visual_role(
            {
                "citation_label": "A6",
                "caption": "Modèle CAO utilisé pour la simulation",
            }
        )
        == "METHOD"
    )
