from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from EnnoScholar.contracts import (
    ContractError,
    assert_same_verrous,
    build_confirmed_contract,
    build_plan_contract,
    resolve_approved_plan,
)
from EnnoScholar.consultant_plan_service import approve_plan, authorize_writing
from EnnoScholar.verrou_selector import select_scholar_verrous_from_nlp


class ContractTests(unittest.TestCase):
    def test_extracts_canonical_verrous_from_phase47_schema(self) -> None:
        payload = {
            "canonical_verrous": [
                {
                    "verrou_id": "v-a",
                    "verrou_title": "Incertitude A",
                }
            ]
        }
        contract = build_confirmed_contract(payload)
        self.assertEqual(contract["verrous_count"], 1)
        self.assertEqual(contract["verrous"][0]["verrou_id"], "v-a")

    def test_extracts_nested_global_writer_verrou_index(self) -> None:
        payload = {
            "global_writer_blueprint": {
                "verrou_index": [
                    {
                        "verrou_id": "v-b",
                        "verrou_title": "Incertitude B",
                    }
                ]
            }
        }
        contract = build_confirmed_contract(payload)
        self.assertEqual(contract["verrous_count"], 1)
        self.assertEqual(contract["verrous"][0]["verrou_id"], "v-b")

    def test_confirmed_verrous_keep_ids_and_titles(self) -> None:
        payload = {
            "verrous": [
                {"verrou_id": "v-a", "verrou_title": "Incertitude A"},
                {"verrou_id": "v-b", "verrou_title": "Incertitude B"},
            ]
        }
        contract = build_confirmed_contract(payload)
        self.assertEqual(
            [(v["verrou_id"], v["verrou_title"]) for v in contract["verrous"]],
            [("v-a", "Incertitude A"), ("v-b", "Incertitude B")],
        )
        self.assertEqual(contract["verrous_count"], 2)

    def test_raw_nlp_cannot_create_verrous(self) -> None:
        with self.assertRaises(ContractError):
            select_scholar_verrous_from_nlp(
                {
                    "frascati_guard": {
                        "qualified_pack_for_ennodiagnostic": {
                            "limites_locales": [{"text": "Une limite quelconque"}]
                        }
                    }
                }
            )

    def test_alias_merges_lists_without_hardcoded_alias(self) -> None:
        contract = build_confirmed_contract(
            {
                "verrous": [
                    {
                        "verrou_id": "old",
                        "verrou_title": "Ancien titre",
                        "selected_articles": [{"citation_label": "A1"}],
                    },
                    {
                        "verrou_id": "canonical",
                        "verrou_title": "Titre canonique",
                        "selected_articles": [{"citation_label": "A2"}],
                    },
                ]
            },
            aliases={"old": "canonical"},
        )
        self.assertEqual(len(contract["verrous"]), 1)
        self.assertEqual(contract["verrous"][0]["verrou_title"], "Titre canonique")
        labels = {
            item["citation_label"]
            for item in contract["verrous"][0]["selected_articles"]
        }
        self.assertEqual(labels, {"A1", "A2"})

    def test_environment_alias_cannot_contaminate_another_project(self) -> None:
        with patch.dict(
            os.environ,
            {"ENNOSCHOLAR_VERROU_ALIASES": "ancien=canonique"},
            clear=False,
        ):
            contract = build_confirmed_contract(
                {
                    "verrous": [
                        {
                            "verrou_id": "projet-b",
                            "verrou_title": "Verrou confirmé du projet B",
                        }
                    ]
                }
            )
        self.assertEqual(contract["verrous_count"], 1)
        self.assertEqual(contract["verrous"][0]["verrou_id"], "projet-b")

    def test_phase_mismatch_is_blocking(self) -> None:
        expected = [{"verrou_id": "1", "verrou_title": "Titre exact"}]
        with self.assertRaises(ContractError):
            assert_same_verrous(
                expected,
                [{"verrou_id": "1", "verrou_title": "Titre modifié"}],
                observed_name="test",
            )

    def test_plan_must_be_approved_and_authorized(self) -> None:
        draft = build_plan_contract(
            proposed_plan=[{"title": "Section A"}],
            approve=False,
        )
        with self.assertRaises(ContractError):
            resolve_approved_plan(draft)
        approved = approve_plan(draft, "consultant")
        with self.assertRaises(ContractError):
            resolve_approved_plan(approved)
        authorized = authorize_writing(approved)
        plan = resolve_approved_plan(authorized)
        self.assertEqual(plan[0]["title"], "Section A")


if __name__ == "__main__":
    unittest.main()
