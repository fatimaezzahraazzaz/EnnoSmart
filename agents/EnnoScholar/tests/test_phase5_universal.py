from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from EnnoScholar.consultant_plan_service import (
    approve_plan,
    authorize_writing,
    create_contract,
)
from EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import (
    call_writer_llm,
    run_phase_5_state_of_art_writer,
)


def write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class Phase5UniversalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.selection = write(
            self.root / "selection.json",
            {
                "verrous": [
                    {
                        "verrou_id": "bio-1",
                        "verrou_title": "Incertitude sur la durabilité hygrique",
                    }
                ]
            },
        )
        self.cards = write(
            self.root / "cards.json",
            {
                "article_cards": [
                    {
                        "citation_label": "A1",
                        "title": "Ageing of bio-based composites",
                        "abstract": "The study evaluates moisture ageing of bio-based composite specimens.",
                        "limitations": [
                            "Long-term outdoor exposure is not covered by the protocol."
                        ],
                        "year": 2025,
                    }
                ]
            },
        )
        self.reasoning = write(
            self.root / "reasoning.json",
            {
                "verrous_reasoning": [
                    {
                        "verrou_id": "bio-1",
                        "verrou_title": "Incertitude sur la durabilité hygrique",
                        "technical_methods": [
                            {
                                "citation_label": "A1",
                                "method": "Moisture ageing is evaluated on composite specimens.",
                                "result": "The protocol reports changes after humid exposure.",
                                "limitation": "Long-term outdoor exposure is not covered.",
                            }
                        ],
                    }
                ]
            },
        )
        self.phase46 = write(
            self.root / "phase46.json",
            {
                "argumentations": [
                    {
                        "verrou_id": "bio-1",
                        "verrou_title": "Incertitude sur la durabilité hygrique",
                    }
                ]
            },
        )
        self.phase47 = write(
            self.root / "phase47.json",
            {
                "ok": True,
                "verrou_index": [
                    {
                        "verrou_id": "bio-1",
                        "verrou_title": "Incertitude sur la durabilité hygrique",
                    }
                ],
                "verrou_sections_for_phase5": [
                    {
                        "verrou_id": "bio-1",
                        "verrou_title": "Incertitude sur la durabilité hygrique",
                        "citation_coverage": {"required_citations": ["A1"]},
                    }
                ],
                "project_specific_story_axes": [
                    {
                        "axis_id": "durabilite",
                        "title": "Durabilité et conditions d’exposition",
                        "objective": "Analyser les protocoles et leurs limites.",
                        "citations": ["A1"],
                    }
                ],
                "project_specific_method_story_units": [
                    {
                        "citation_label": "A1",
                        "method": "Moisture ageing is evaluated on composite specimens.",
                        "limitation": "Long-term outdoor exposure is not covered.",
                        "verrou_id": "bio-1",
                    }
                ],
            },
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_writer(self, **extra):
        output = self.root / "out.json"
        markdown = self.root / "out.md"
        selection_path = extra.pop(
            "selection_payload_path",
            self.selection,
        )
        phase47_path = extra.pop(
            "phase47_scientific_narrative_payload_path",
            self.phase47,
        )
        with patch.dict(
            os.environ,
            {"ENNOSCHOLAR_PHASE5_ENABLE_LLM": "0"},
            clear=False,
        ):
            result = run_phase_5_state_of_art_writer(
                organisme="Demo",
                project="Batteries biosourcées",
                year="2026",
                selection_payload_path=selection_path,
                article_cards_payload_path=self.cards,
                scientific_reasoning_payload_path=self.reasoning,
                phase46_project_argumentation_payload_path=self.phase46,
                phase47_scientific_narrative_payload_path=phase47_path,
                output_path=output,
                markdown_output_path=markdown,
                **extra,
            )
        return result, output, markdown

    def test_non_radar_project_has_no_cross_project_leak(self) -> None:
        result, _, markdown = self.run_writer()
        self.assertTrue(result["ok"], result)
        text = markdown.read_text(encoding="utf-8").lower()
        for forbidden in (
            "ai-radar",
            "mocem",
            "mstar",
            "adasca",
            "salsa",
            "mobilenetv3",
            "pix2pix",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("[a1]", text)
        self.assertEqual(result["state_of_art_mode"], "global")

    def test_approved_consultant_titles_are_exact(self) -> None:
        contract = create_contract(
            [
                {
                    "section_id": "grand_titre",
                    "title": "Titre choisi par le consultant",
                    "objective": "Organiser les preuves sans modifier les verrous.",
                }
            ]
        )
        contract = authorize_writing(approve_plan(contract, "consultant"))
        plan_path = write(self.root / "plan.json", contract)
        result, _, _ = self.run_writer(consultant_plan_contract_path=plan_path)
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            result["draft_json"]["sections"][0]["title"],
            "Titre choisi par le consultant",
        )
        self.assertEqual(
            result["draft_json"]["sections"][0]["subsections"][0]["title"],
            "Incertitude sur la durabilité hygrique",
        )

    def test_no_cards_blocks_writing(self) -> None:
        empty = write(self.root / "empty_cards.json", {"article_cards": []})
        with patch.dict(os.environ, {"ENNOSCHOLAR_PHASE5_ENABLE_LLM": "0"}, clear=False):
            result = run_phase_5_state_of_art_writer(
                organisme="Demo",
                project="No evidence",
                year="2026",
                selection_payload_path=self.selection,
                article_cards_payload_path=empty,
                scientific_reasoning_payload_path=self.reasoning,
                phase46_project_argumentation_payload_path=self.phase46,
                phase47_scientific_narrative_payload_path=self.phase47,
                output_path=self.root / "blocked.json",
                markdown_output_path=self.root / "blocked.md",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertFalse((self.root / "blocked.md").exists())
        self.assertFalse((self.root / "blocked.json").exists())
        self.assertTrue((self.root / "blocked_rejected.json").exists())

    def test_legacy_phase47_target_verrous_are_accepted_without_rewriting_story(self) -> None:
        legacy_phase47 = write(
            self.root / "legacy-phase47.json",
            {
                "ok": True,
                "project_specific_story_axes": [
                    {
                        "axis_id": "durabilite",
                        "title": "Durabilité et conditions d’exposition",
                        "objective": "Analyser les protocoles et leurs limites.",
                        "target_verrous": ["verrou_bio-1"],
                        "citations": ["A1"],
                    }
                ],
                "project_specific_method_story_units": [
                    {
                        "citation_label": "A1",
                        "method": "Moisture ageing is evaluated on composite specimens.",
                        "limitation": "Long-term outdoor exposure is not covered.",
                        "verrou_id": "bio-1",
                    }
                ],
            },
        )
        result, _, _ = self.run_writer(
            phase47_scientific_narrative_payload_path=legacy_phase47
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stats"]["verrous_count"], 1)

    def test_project_alias_is_explicit_and_not_global(self) -> None:
        legacy_selection = write(
            self.root / "legacy-selection.json",
            {
                "verrous": [
                    {
                        "verrou_id": "legacy-bio",
                        "verrou_title": "Incertitude sur la durabilité hygrique",
                    }
                ]
            },
        )
        with patch.dict(
            os.environ,
            {"ENNOSCHOLAR_VERROU_ALIASES": "wrong=missing"},
            clear=False,
        ):
            result, _, _ = self.run_writer(
                selection_payload_path=legacy_selection,
                verrou_aliases={"legacy-bio": "bio-1"},
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stats"]["verrous_count"], 1)

    def test_consultant_plan_cannot_create_a_verrou(self) -> None:
        contract = create_contract(
            [
                {
                    "section_id": "grand_titre",
                    "title": "Titre consultant",
                    "verrou_ids": ["verrou-invente"],
                }
            ]
        )
        contract = authorize_writing(approve_plan(contract, "consultant"))
        plan_path = write(self.root / "invalid-plan.json", contract)
        result, _, _ = self.run_writer(consultant_plan_contract_path=plan_path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "consultant_plan_unknown_verrou")

    def test_gpt5_request_omits_temperature(self) -> None:
        captured = {}

        def fake_http(url, headers, payload, timeout):
            captured.update(payload)
            return {
                "choices": [
                    {"message": {"content": '{"title":"x","sections":[]}'}}]
            }

        env = {
            "ENNOSCHOLAR_PHASE5_ENABLE_LLM": "1",
            "ENNOSCHOLAR_PHASE5_PROVIDER": "openai",
            "ENNOSCHOLAR_PHASE5_WRITER_MODEL": "gpt-5.6-terra",
            "OPENAI_API_KEY": "test-only",
            "ENNOSCHOLAR_PHASE5_TEMPERATURE": "0.06",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "EnnoScholar.state_of_art.phase_5_state_of_art_writer_service._http_json",
            side_effect=fake_http,
        ):
            _, report = call_writer_llm("test")
        self.assertNotIn("temperature", captured)
        self.assertIn("max_completion_tokens", captured)
        self.assertNotIn("max_tokens", captured)
        self.assertFalse(report["temperature_sent"])


if __name__ == "__main__":
    unittest.main()
