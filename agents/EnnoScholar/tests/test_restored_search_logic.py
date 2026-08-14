from __future__ import annotations

import unittest

from EnnoScholar.query_builder import attach_queries_to_intent
from EnnoScholar.scientific_intent_builder import build_scientific_intent


class RestoredSearchLogicTests(unittest.TestCase):
    def test_sar_queries_include_problem_evidence_not_only_sar_atr(self) -> None:
        verrou = {
            "verrou_id": "690",
            "verrou_title": (
                "Représentativité des données synthétiques SAR pour la "
                "généralisation en ATR"
            ),
            "raw_item": {
                "source_text": (
                    "Les modèles ATR sont entraînés sur des données synthétiques "
                    "et doivent généraliser vers des mesures réelles SAR."
                )
            },
        }

        intent = build_scientific_intent(verrou)
        enriched = attach_queries_to_intent(intent, max_queries=8)
        queries = [
            item["query"].lower()
            for item in enriched.get("search_queries") or []
        ]

        self.assertTrue(
            any(
                "training data" in query
                and "real measurements" in query
                for query in queries
            )
        )
        self.assertFalse(any("analyser" in query for query in queries))

    def test_radar_lock_builds_canonical_queries_from_local_evidence(self) -> None:
        verrou = {
            "verrou_id": "689",
            "verrou_title": (
                "Incertitude sur la représentativité des méthodes de lancer "
                "de rayons pour la validation"
            ),
            "raw_item": {
                "source_text": (
                    "La méthode MLFMM améliore MoM pour les grands systèmes. "
                    "Les méthodes exactes modélisent la diffraction des arêtes, "
                    "contrairement au lancer de rayons. La validation compare "
                    "les résultats sur des cibles radar canoniques."
                ),
            },
        }

        intent = build_scientific_intent(verrou)
        enriched = attach_queries_to_intent(intent, max_queries=8)
        queries = [
            item["query"]
            for item in enriched.get("search_queries") or []
        ]

        self.assertIn("electromagnetic scattering", intent["core_concepts"])
        self.assertIn("electromagnetic ray tracing", intent["core_concepts"])
        self.assertIn(
            "multilevel fast multipole method",
            intent["method_anchors"],
        )
        self.assertTrue(
            any("electromagnetic scattering" in query for query in queries)
        )
        self.assertFalse(any("quant elle" in query for query in queries))

    def test_bistatic_radar_lock_is_not_dropped_when_sar_is_ambiguous(self) -> None:
        intent = {
            "verrou_id": "805",
            "verrou_title": (
                "Incertitude sur l'impact du controle de densite des rayons "
                "sur la precision et la performance calculatoire des "
                "simulations radar bistatiques"
            ),
            "core_concepts": ["synthetic aperture radar"],
            "primary_core_concepts": ["synthetic aperture radar"],
            "concept_aliases": {
                "synthetic aperture radar": ["synthetic aperture radar", "SAR"],
            },
            "ambiguous_acronyms": ["SAR"],
            "literal_source_acronyms": ["SAR"],
            "literal_source_phrases": [
                "simulations radar bistatiques",
                "radar bistatic configurations",
            ],
            "literal_source_terms": [
                "radar",
                "simulations",
                "precision",
                "densite",
                "rayons",
            ],
            "phenomenon_anchors": [
                "validation against reference methods or measurements",
            ],
        }

        enriched = attach_queries_to_intent(intent, max_queries=8)
        queries = [
            item["query"].lower()
            for item in enriched.get("search_queries") or []
        ]

        self.assertGreater(len(queries), 0)
        self.assertTrue(any("synthetic aperture radar" in query for query in queries))
        self.assertTrue(
            any("bistatic" in query or "ray" in query for query in queries)
        )

    def test_section_title_preserves_bistatic_ray_density_problem(self) -> None:
        verrou = {
            "verrou_id": "805",
            "verrou_title": (
                "Incertitude sur l'impact du controle de densite des rayons "
                "sur la precision et la performance calculatoire des "
                "simulations radar bistatiques"
            ),
            "raw_item": {
                "source_text": (
                    "plays a critical role in controlling the trade-off "
                    "between accuracy and computing speed."
                ),
            },
            "sources": [
                {
                    "section_title": (
                        "and bistatic radar configurations. The ray-launch density"
                    ),
                    "excerpt": (
                        "plays a critical role in controlling the trade-off "
                        "between accuracy and computing speed. SAR image formation."
                    ),
                },
            ],
        }

        intent = build_scientific_intent(verrou)
        enriched = attach_queries_to_intent(intent, max_queries=8)
        queries = [
            item["query"].lower()
            for item in enriched.get("search_queries") or []
        ]

        self.assertIn("electromagnetic ray tracing", intent["core_concepts"])
        self.assertIn("electromagnetic ray tracing", intent["method_anchors"])
        self.assertIn("ray-launch density", intent["core_concepts"])
        self.assertIn("bistatic radar simulation", intent["core_concepts"])
        self.assertIn(
            "accuracy-computational cost trade-off",
            intent["phenomenon_anchors"],
        )
        self.assertTrue(any("ray tracing" in query for query in queries))


if __name__ == "__main__":
    unittest.main()
