"""Offline regressions for local contradictory intergroup relations."""
import copy
import itertools
import random
import unittest
from dataclasses import asdict

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.consolidation import GlobalLockConsolidator
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import (
    CanonicalEntity, EvidenceRef, ExtractionItem,
)
from global_fast_path_regression import QueueLLM


class IntergroupConflictTests(unittest.TestCase):
    def setUp(self):
        self.items = []
        self.groups = []
        for letter in "ABCDEF":
            proof = EvidenceRef(
                evidence_id=f"ev_{letter}", document_id=letter,
                document_name=f"{letter}.txt", chunk_id=f"p{letter}",
                passage_id=f"p{letter}", quote=f"Exact technical evidence {letter}",
                quote_verified=True, char_start=0, char_end=26,
            )
            item = ExtractionItem(
                item_id=letter, kind="lock", statement=f"Technical difficulty {letter}",
                evidence=proof, metadata={"source_role": "project_evidence"},
            )
            self.items.append(item)
            self.groups.append(CanonicalEntity(
                entity_id=f"group_{letter}", kind="lock", statement=item.statement,
                source_item_ids=[letter], evidences=[proof], document_ids=[letter],
            ))
        self.rows = []
        for a, b in itertools.combinations("ABCDEF", 2):
            supporting = a + b in {"AB", "AC", "AD", "BC", "BD", "CD"}
            specific = a + b in {"AD", "BD"}
            self.rows.append({
                "left_anchor": a, "right_anchor": b, "principal_anchor": a,
                "relation": "SUPPORTING_MANIFESTATION" if supporting else "DISTINCT_LOCK",
                "same_technical_object": supporting, "same_phenomenon": supporting,
                "strong_support": True, "same_technical_mechanism": supporting,
                "introduces_method_specific_uncertainty": specific or not supporting,
                "would_remain_if_parent_resolved": not supporting,
                "is_experimental_attempt": supporting, "targets_parent_problem": supporting,
                "shows_parent_persistence": supporting,
                "has_independent_uncertainty": not supporting,
                "evidence_ids": [f"ev_{a}", f"ev_{b}"],
                "reason": "Exact evidence supports the declared technical relation",
            })

    def apply(self, rows=None, groups=None):
        return GlobalLockConsolidator._intergroup(
            groups or self.groups, rows if rows is not None else copy.deepcopy(self.rows),
            self.items, strict_experimental_contract=True,
        )

    def assert_components(self, result):
        self.assertEqual(
            {frozenset(e.source_item_ids) for e in result},
            {frozenset("ABC"), frozenset("D"), frozenset("E"), frozenset("F")},
        )
        ids = [i for e in result for i in e.source_item_ids]
        self.assertEqual(sorted(ids), sorted(i.item_id for i in self.items))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            {e.evidence_id: asdict(e) for g in result for e in g.evidences},
            {i.evidence.evidence_id: asdict(i.evidence) for i in self.items},
        )
        self.assertTrue(all(e.metadata["intergroup_validated"] for e in result))

    def test_coherent_component_survives_local_conflict_with_exact_evidence(self):
        before = copy.deepcopy(self.items)
        result = self.apply()
        self.assert_components(result)
        self.assertEqual(self.items, before)
        merged = next(e for e in result if "A" in e.source_item_ids)
        self.assertEqual(merged.statement, self.groups[0].statement)
        self.assertEqual(merged.document_ids, ["A", "B", "C"])

    def test_response_and_group_order_cannot_choose_the_conflicting_branch(self):
        rng = random.Random(42)
        for _ in range(25):
            rows, groups = copy.deepcopy(self.rows), list(self.groups)
            rng.shuffle(rows)
            rng.shuffle(groups)
            for row in rows:
                if rng.choice([True, False]):
                    row["left_anchor"], row["right_anchor"] = row["right_anchor"], row["left_anchor"]
            with self.subTest(order=[g.entity_id for g in groups]):
                self.assert_components(self.apply(rows, groups))

    def test_conflict_is_audited_and_only_affected_components_need_review(self):
        rows = copy.deepcopy(self.rows)
        result = self.apply(rows)
        rejected = next(r for r in rows if {r["left_anchor"], r["right_anchor"]} == {"C", "D"})
        self.assertEqual(rejected["relation"], "DISTINCT_LOCK")
        self.assertEqual(rejected["proposed_relation"], "SUPPORTING_MANIFESTATION")
        self.assertEqual(rejected["relation_validation"], "contradictory_intergroup_relation_kept_distinct")
        self.assertEqual(rejected["conflicting_distinct_pairs"], [["A", "D"], ["B", "D"]])
        for entity in result:
            affected = bool(set(entity.source_item_ids) & {"C", "D"})
            self.assertEqual(entity.needs_review, affected)
            self.assertEqual(bool(entity.metadata.get("intergroup_conflicts")), affected)

    def test_invalid_traceability_and_incomplete_coverage_still_fail(self):
        invalid = copy.deepcopy(self.rows)
        invalid[0]["evidence_ids"] = ["invented"]
        for rows in (invalid, copy.deepcopy(self.rows[:-1])):
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                self.apply(rows)

    def test_legacy_direct_call_retains_its_previous_conflict_failure(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            if {row["left_anchor"], row["right_anchor"]} in ({"A", "D"}, {"B", "D"}):
                row["relation"] = "DISTINCT_LOCK"
        with self.assertRaisesRegex(ValueError, "contradictory_intergroup_relations"):
            GlobalLockConsolidator._intergroup(self.groups, rows, self.items)

    def test_normal_consolidator_handles_conflict_without_an_extra_model_call(self):
        llm = QueueLLM([{
            "clusters": [{"item_ids": [i.item_id], "canonical_statement": i.statement,
                          "reason": "Preserve the original candidate"} for i in self.items],
            "inter_group_relations": copy.deepcopy(self.rows),
        }])
        result = GlobalLockConsolidator(llm, require_intergroup=True).consolidate(self.items)
        self.assert_components(result)
        self.assertEqual(len(llm.calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
