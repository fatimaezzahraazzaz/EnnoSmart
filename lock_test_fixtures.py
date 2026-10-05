"""Authored coverage fixture adapter for older additive tests.

This supplies protocol fields to historical mock responses. It is not a
semantic oracle; missing-decision invariants use the original QueueLLM.
"""
import copy
import json
from global_fast_path_regression import QueueLLM as BaseQueueLLM


class CoverageQueueLLM(BaseQueueLLM):
    def extract_json(self, system, prompt):
        response = super().extract_json(system, prompt)
        if (not isinstance(response, dict) or "LOCK_SIGNALS:\n" not in prompt
                or "lock_coverage_decisions" in response):
            return response
        response = copy.deepcopy(response)
        initial = json.JSONDecoder().raw_decode(prompt.split("VALIDÉS (ne pas les retourner):\n", 1)[1])[0]
        signals = json.JSONDecoder().raw_decode(prompt.split("LOCK_SIGNALS:\n", 1)[1])[0]
        proposed = response.get("facts", [])
        rows = []
        for signal in signals:
            existing = [f for f in initial if f.get("kind") == "lock"
                        and f.get("source_role") == "project_evidence"
                        and set(signal["passage_ids"]) <= set(f["passage_ids"])]
            new = [f for f in proposed if isinstance(f, dict) and f.get("kind") == "lock"
                   and f.get("source_role") == "project_evidence"
                   and set(signal["passage_ids"]) <= set(f.get("passage_ids", []))]
            decision = {**signal, "existing_lock_ids": [], "new_lock_ids": [],
                        "reason": "Authored fixture coverage, not a prediction"}
            if existing or new:
                selected = (existing or new)[0]
                decision.update(decision="covered_by_existing_lock" if existing else "new_lock_added",
                                passage_ids=sorted(set(signal["passage_ids"] + selected["passage_ids"])))
                decision["existing_lock_ids" if existing else "new_lock_ids"] = [
                    selected['fact_id'] if existing else selected.get('fact_id', f"a{proposed.index(selected)+1}")]
            else:
                decision.update(decision="not_a_lock", reason_code="no_technical_uncertainty")
            rows.append(decision)
        response["lock_coverage_decisions"] = rows
        return response
