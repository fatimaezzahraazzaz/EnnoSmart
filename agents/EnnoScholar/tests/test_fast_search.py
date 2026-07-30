from __future__ import annotations

import os
import threading
import unittest
from unittest.mock import patch

from EnnoScholar.doaj_client import DoajClient
from EnnoScholar.scholar_agent import EnnoScholarAgent
from EnnoScholar.source_router import build_source_plan


class FastSearchTests(unittest.TestCase):
    def test_fast_source_plan_uses_doaj_and_defers_slow_sources(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENNOSCHOLAR_FAST_MODE": "1",
                "ENNOSCHOLAR_FAST_INCLUDE_SECONDARY_SOURCES": "0",
                "ENNOSCHOLAR_FAST_SEARCH_ARTIFACTS": "0",
                "ENNOSCHOLAR_USE_DOAJ": "1",
            },
            clear=False,
        ):
            plan = build_source_plan(
                {
                    "backend_enrichment_profile": "signal_image_vision",
                    "verrou_title": "synthetic aperture radar target recognition",
                }
            )

        self.assertIn("doaj", plan["scientific_sources"])
        self.assertNotIn("hal", plan["scientific_sources"])
        self.assertNotIn("zenodo", plan["scientific_sources"])
        self.assertIn("hal", plan["fallback_scientific_sources"])
        self.assertIn("zenodo", plan["fallback_scientific_sources"])
        self.assertEqual(plan["artifact_sources"], [])
        self.assertTrue(plan["fast_mode"])

    def test_doaj_normalization_keeps_open_access_metadata(self) -> None:
        article = DoajClient.normalize(
            {
                "id": "doaj-1",
                "bibjson": {
                    "title": "Fast open article",
                    "abstract": "<p>A useful abstract.</p>",
                    "year": "2025",
                    "identifier": [{"type": "doi", "id": "10.1234/example"}],
                    "journal": {"title": "Example Journal"},
                    "author": [{"name": "Ada Lovelace"}],
                    "keywords": ["radar"],
                    "link": [
                        {
                            "type": "fulltext",
                            "content_type": "application/pdf",
                            "url": "https://example.org/article.pdf",
                        }
                    ],
                },
            },
            "radar",
        )

        self.assertEqual(article["source"], "doaj")
        self.assertEqual(article["doi"], "10.1234/example")
        self.assertEqual(article["pdf_url"], "https://example.org/article.pdf")
        self.assertTrue(article["is_open_access"])
        self.assertTrue(article["free_fulltext_available"])

    def test_verrous_are_searched_in_parallel_and_keep_input_order(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENNOSCHOLAR_FAST_MODE": "1",
                "ENNOSCHOLAR_VERROU_WORKERS": "2",
                "ENNOSCHOLAR_RUN_CACHE_ENABLED": "0",
                "ENNOSCHOLAR_ENABLE_BGE_RERANKER": "0",
            },
            clear=False,
        ):
            agent = EnnoScholarAgent(offline_dry_run=True)
            barrier = threading.Barrier(2)

            def fake_search(verrou, domain_detection, diagnostic_context):
                del domain_detection, diagnostic_context
                barrier.wait(timeout=2)
                return {
                    "verrou_id": verrou["verrou_id"],
                    "verrou_title": verrou["verrou_id"],
                    "articles": [],
                    "decision": "aucun_article_trouve",
                }

            with patch.object(agent, "search_for_verrou", side_effect=fake_search):
                report = agent.run_search(
                    {
                        "verrous": [
                            {"verrou_id": "first"},
                            {"verrou_id": "second"},
                        ]
                    }
                )

        self.assertEqual(
            [item["verrou_id"] for item in report["results"]],
            ["first", "second"],
        )
        self.assertEqual(report["verrou_workers"], 2)


if __name__ == "__main__":
    unittest.main()
