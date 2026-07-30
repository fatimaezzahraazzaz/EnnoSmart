from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from EnnoScholar.contracts import ContractError
from EnnoScholar.scholar_agent import EnnoScholarAgent
from EnnoScholar.scholar_memory_v2 import match_memory_v2_articles
from EnnoScholar.state_of_art.phase_4_7_scientific_narrative_builder import (
    build_scientific_narrative_payload,
)
from EnnoScholar.state_of_art.phase_4_scientific_gap_service import normalize_verrou


def write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


class PipelineGuardTests(unittest.TestCase):
    def test_phase4_refuses_a_verrou_without_id(self) -> None:
        with self.assertRaises(ContractError):
            normalize_verrou({"verrou_title": "Titre sans identifiant"}, 1)

    def test_phase47_requires_ennodiagnostic_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p45 = write(
                root / "p45.json",
                {"verrous_reasoning": [{"verrou_id": "v1", "verrou_title": "Verrou 1"}]},
            )
            p46 = write(
                root / "p46.json",
                {"argumentations": [{"verrou_id": "v1", "verrou_title": "Verrou 1"}]},
            )
            result = build_scientific_narrative_payload(
                organisme="Demo",
                project="Projet",
                year="2026",
                phase_4_5_path=str(p45),
                phase_4_6_path=str(p46),
                confirmed_verrous_path=str(root / "absent.json"),
                dry_run=True,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "confirmed_verrous_missing")

    def test_previous_project_memory_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENNOSCHOLAR_MEMORY_V2_ENABLED", None)
            result = match_memory_v2_articles({"verrou_id": "v1"})
        self.assertFalse(result["enabled"])
        self.assertEqual(result["articles"], [])

    def test_legacy_per_verrou_writer_is_disabled(self) -> None:
        agent = EnnoScholarAgent(
            use_semantic_scholar=False,
            use_openalex=False,
            use_arxiv=False,
            offline_dry_run=True,
        )
        result = agent.run_writer_from_selection(
            {
                "project": "Projet",
                "verrous": [{"verrou_id": "v1", "verrou_title": "Verrou 1"}],
            }
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "legacy_per_verrou_writer_disabled")
        self.assertEqual(result["verrous_written"], 0)


if __name__ == "__main__":
    unittest.main()
