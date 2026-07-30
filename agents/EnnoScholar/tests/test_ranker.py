from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from EnnoScholar.paper_ranker import rank_papers_for_intent
from EnnoScholar.scholar_agent import (
    _filter_articles_after_project_year,
    _filter_free_fulltext_articles,
)


class RankerTests(unittest.TestCase):
    @staticmethod
    def _sar_intent() -> dict:
        return {
            "core_concepts": [
                "synthetic aperture radar",
                "automatic target recognition",
                "synthetic training data",
                "real radar measurements",
                "sim-to-real generalization",
            ],
            "primary_core_concepts": [
                "synthetic aperture radar",
                "automatic target recognition",
            ],
            "phenomenon_anchors": ["limited sim-to-real generalization"],
            "concept_aliases": {
                "synthetic aperture radar": ["synthetic aperture radar", "SAR"],
                "automatic target recognition": ["automatic target recognition", "ATR"],
                "synthetic training data": ["synthetic training data", "synthetic data"],
                "real radar measurements": ["measured SAR data", "real radar measurements"],
                "sim-to-real generalization": ["sim-to-real", "domain adaptation"],
            },
        }

    def test_generic_sar_atr_article_is_connexe_not_direct(self) -> None:
        ranked = rank_papers_for_intent(
            [{
                "title": "Synthetic aperture radar automatic target recognition",
                "abstract": "A classifier is evaluated on the MSTAR benchmark.",
                "year": 2025,
            }],
            self._sar_intent(),
            top_n=5,
        )

        self.assertEqual(ranked[0]["tag"], "Connexe")
        self.assertFalse(ranked[0]["score_details"]["problem_evidence"])

    def test_sar_article_covering_synthetic_and_measured_data_is_direct(self) -> None:
        ranked = rank_papers_for_intent(
            [{
                "title": "SAR automatic target recognition from synthetic training data",
                "abstract": (
                    "The model is trained with synthetic data and evaluated on "
                    "measured SAR data to quantify generalization."
                ),
                "year": 2025,
            }],
            self._sar_intent(),
            top_n=5,
        )

        self.assertEqual(ranked[0]["tag"], "Direct")
        self.assertTrue(ranked[0]["score_details"]["direct_eligible"])
        self.assertGreaterEqual(
            ranked[0]["score_details"]["secondary_core_hit_count"],
            2,
        )

    def test_em_reference_method_without_ray_tracing_is_not_direct(self) -> None:
        intent = {
            "core_concepts": [
                "electromagnetic scattering",
                "electromagnetic ray tracing",
            ],
            "primary_core_concepts": [
                "electromagnetic scattering",
                "electromagnetic ray tracing",
            ],
            "method_anchors": [
                "method of moments",
                "electromagnetic ray tracing",
            ],
            "phenomenon_anchors": [
                "validation against reference methods or measurements",
            ],
            "concept_aliases": {
                "electromagnetic scattering": ["electromagnetic scattering"],
                "electromagnetic ray tracing": ["ray tracing"],
            },
        }
        ranked = rank_papers_for_intent(
            [{
                "title": "Method of moments for electromagnetic scattering",
                "abstract": "An efficient integral-equation solver is presented.",
            }],
            intent,
            top_n=5,
        )

        self.assertEqual(ranked[0]["tag"], "Connexe")
        self.assertFalse(ranked[0]["score_details"]["direct_eligible"])

    def test_em_ray_tracing_validated_against_reference_is_direct(self) -> None:
        intent = {
            "core_concepts": [
                "electromagnetic scattering",
                "electromagnetic ray tracing",
            ],
            "primary_core_concepts": [
                "electromagnetic scattering",
                "electromagnetic ray tracing",
            ],
            "method_anchors": [
                "method of moments",
                "electromagnetic ray tracing",
            ],
            "phenomenon_anchors": [
                "validation against reference methods or measurements",
            ],
            "concept_aliases": {
                "electromagnetic scattering": ["electromagnetic scattering"],
                "electromagnetic ray tracing": ["ray tracing"],
            },
        }
        ranked = rank_papers_for_intent(
            [{
                "title": "Ray tracing for electromagnetic scattering",
                "abstract": (
                    "The predictions are validated against a method of moments "
                    "reference solution."
                ),
            }],
            intent,
            top_n=5,
        )

        self.assertEqual(ranked[0]["tag"], "Direct")
        self.assertIn(
            "method of moments",
            ranked[0]["score_details"]["independent_method_hits"],
        )

    def test_ranker_uses_current_intent_only(self) -> None:
        intent = {
            "verrou_title": "Durabilité des composites biosourcés humides",
            "technical_object": "composites biosourcés",
            "phenomenon": "vieillissement hygrique",
            "strong_anchors": [
                "composites biosourcés",
                "vieillissement hygrique",
            ],
        }
        papers = [
            {
                "title": "Hygrothermal ageing of bio-based composites",
                "abstract": "Bio-based composites are evaluated under humid ageing conditions.",
                "year": 2025,
            },
            {
                "title": "Unrelated remote sensing classification",
                "abstract": "A signal classification benchmark for remote observations.",
                "year": 2025,
            },
        ]
        ranked = rank_papers_for_intent(papers, intent, top_n=2)
        self.assertIn("bio-based composites", ranked[0]["title"])
        self.assertGreater(
            ranked[0]["relevance_score"],
            ranked[1]["relevance_score"],
        )
        self.assertEqual(
            ranked[0]["score_details"]["domain_specific_ontology_used"],
            False,
        )

    def test_restored_v146_core_gate_rejects_acronym_contradiction(self) -> None:
        intent = {
            "core_concepts": [
                "synthetic aperture radar",
                "automatic target recognition",
            ],
            "primary_core_concepts": [
                "synthetic aperture radar",
                "automatic target recognition",
            ],
            "phenomenon_anchors": ["limited sim-to-real generalization"],
            "concept_aliases": {
                "synthetic aperture radar": ["synthetic aperture radar", "SAR"],
                "automatic target recognition": [
                    "automatic target recognition",
                    "ATR",
                ],
            },
        }
        papers = [
            {
                "title": "Synthetic aperture radar automatic target recognition",
                "abstract": "SAR ATR with limited sim-to-real generalization.",
                "year": 2025,
            },
            {
                "title": "Specific absorption rate in human tissue",
                "abstract": "Medical exposure study measured in W/kg.",
                "year": 2025,
            },
        ]

        ranked = rank_papers_for_intent(papers, intent, top_n=2)

        self.assertEqual(ranked[0]["tag"], "Direct")
        self.assertEqual(ranked[1]["tag"], "Hors sujet")
        self.assertTrue(
            ranked[0]["score_details"]["domain_specific_ontology_used"]
        )

    def test_paid_articles_are_kept_for_legal_mcp_recovery(self) -> None:
        paywalled = {
            "title": "Relevant paper without an open-access URL",
            "doi": "10.1000/example",
            "is_open_access": False,
        }
        clean_env = dict(os.environ)
        clean_env.pop("ENNOSCHOLAR_REQUIRE_FREE_FULLTEXT", None)

        with patch.dict(os.environ, clean_env, clear=True):
            kept, report = _filter_free_fulltext_articles([paywalled])

        self.assertEqual(kept, [paywalled])
        self.assertFalse(report["enabled"])
        self.assertEqual(report["removed_count"], 0)

    def test_articles_published_after_project_year_are_removed(self) -> None:
        articles = [
            {"title": "Available in project year", "year": 2025},
            {"title": "Published too late", "year": 2026},
            {"title": "Unknown publication year", "year": None},
        ]
        with patch.dict(
            os.environ,
            {"ENNOSCHOLAR_ENFORCE_PROJECT_YEAR_CUTOFF": "1"},
            clear=False,
        ):
            kept, report = _filter_articles_after_project_year(articles, "2025")

        self.assertEqual(
            [item["title"] for item in kept],
            ["Available in project year", "Unknown publication year"],
        )
        self.assertEqual(report["removed_count"], 1)
        self.assertEqual(report["cutoff_year"], 2025)


if __name__ == "__main__":
    unittest.main()
