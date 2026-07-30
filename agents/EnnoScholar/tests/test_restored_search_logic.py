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


if __name__ == "__main__":
    unittest.main()
