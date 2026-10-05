"""Offline tests for Responses transport and targeted Fast Path review."""
import json
import tempfile
import unittest
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from modules.LLM.llm_client import LLMClient
from modules.LLM.usage_budget import compute_cost, normalize_usage
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import (
    GlobalFastPathConfig, GlobalReviewerConfig, PipelineConfig,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_fast_path import GlobalFastExtractor, build_passages
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_project_reviewer import GlobalProjectReviewer, reviewed_fact_view
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.pipeline import EnnoDiagnosticV2
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.runtime_adapters import GlobalFastLLMAdapter, GlobalReasonerLLMAdapter
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.structured_facts import build_project_fact_lists


class BackgroundTransportTests(unittest.TestCase):
    def client(self):
        client = LLMClient.__new__(LLMClient)
        client.openai_base_url = "https://api.openai.com/v1"
        client.openai_api_key = "test-key"
        client.openai_store = False
        client.provider = "openai"
        client._generation_meta_context = ContextVar("test-generation-meta", default={})
        client.connect_timeout, client.read_timeout = 10, 360
        client._post_with_retry = Mock(return_value={"id": "resp_test", "status": "queued"})
        return client

    def completed(self):
        return {
            "id": "resp_test", "model": "gpt-5-test-snapshot", "status": "completed",
            "output": [{"type": "reasoning"}, {"type": "message", "content": [
                {"type": "output_text", "text": '{"facts":[]}'}]}],
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                      "input_tokens_details": {"cached_tokens": 50}},
        }

    def response(self, payload, status=200):
        return SimpleNamespace(status_code=status, json=lambda: payload, text="test-error")

    def run_call(self, client):
        return client._generate_openai_background("DOSSIER BRUT sans format explicite", "gpt-5", 64000, 0, True, None, "medium")

    @patch("modules.LLM.llm_client.time.sleep")
    @patch("modules.LLM.llm_client.requests.get")
    def test_one_generation_polling_exact_model_and_usage(self, get, sleep):
        client = self.client()
        get.side_effect = [self.response({"id": "resp_test", "status": "in_progress"}),
                           self.response(self.completed())]
        text, usage = self.run_call(client)
        self.assertEqual(text, '{"facts":[]}')
        self.assertEqual(usage["prompt_tokens"], 100)
        self.assertEqual(usage["completion_tokens"], 20)
        self.assertEqual(usage["response_model"], "gpt-5-test-snapshot")
        self.assertEqual(client._post_with_retry.call_count, 1)
        payload = client._post_with_retry.call_args.args[1]
        self.assertTrue(payload["background"])
        self.assertFalse(payload["store"])
        self.assertEqual(payload["reasoning"], {"effort": "medium"})
        self.assertEqual(payload["text"]["format"]["type"], "json_object")
        self.assertIn("json", payload["input"].lower())
        self.assertTrue(payload["input"].endswith("DOSSIER BRUT sans format explicite"))

    @patch("modules.LLM.llm_client.time.sleep")
    @patch("modules.LLM.llm_client.requests.get")
    def test_json_requirement_is_in_input_even_when_source_has_no_json(self, get, sleep):
        client = self.client()
        def validate_payload(url, payload, *args):
            if payload.get("text", {}).get("format", {}).get("type") == "json_object":
                self.assertIn("json", payload["input"].lower(), "HTTP 400 equivalent: input must mention JSON")
            return {"id": "resp_test", "status": "queued"}
        client._post_with_retry.side_effect = validate_payload
        get.return_value = self.response(self.completed())
        self.assertEqual(self.run_call(client)[0], '{"facts":[]}')

    def test_gpt5_cost_includes_cached_input_and_all_output_tokens(self):
        usage = normalize_usage({"prompt_tokens": 1000000, "cached_tokens": 200000,
                                 "completion_tokens": 100000, "total_tokens": 1100000})
        for model in ("gpt-5", "gpt-5-2025-08-07"):
            cost = compute_cost(model, "openai", usage)
            self.assertTrue(cost["price_known"])
            self.assertAlmostEqual(cost["cost_usd"], 2.025)
        # Adding GPT-5 must not accidentally price a different model as GPT-5.
        self.assertFalse(compute_cost("gpt-5-mini", "openai", usage)["price_known"])
        self.assertEqual(compute_cost("gpt-4.1-mini", "openai", usage)["canonical_model"], "gpt-4.1-mini")

    @patch("modules.LLM.llm_client.time.sleep")
    @patch("modules.LLM.llm_client.requests.get")
    def test_poll_connection_failure_resumes_same_generation(self, get, sleep):
        client = self.client()
        get.side_effect = [requests.ReadTimeout("temporary"), self.response(self.completed())]
        self.assertEqual(self.run_call(client)[0], '{"facts":[]}')
        self.assertEqual(client._post_with_retry.call_count, 1)
        self.assertEqual(get.call_count, 2)

    @patch("modules.LLM.llm_client.time.sleep")
    @patch("modules.LLM.llm_client.requests.get")
    def test_partial_response_is_never_accepted(self, get, sleep):
        client = self.client()
        get.return_value = self.response({**self.completed(), "status": "incomplete",
                                         "incomplete_details": {"reason": "max_output_tokens"}})
        with self.assertRaisesRegex(RuntimeError, "max_output_tokens"):
            self.run_call(client)

    def test_adapter_configuration_independent_effort_and_transport(self):
        with patch("agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config._global_setting",
                   side_effect=lambda name, default: "high" if name.endswith("REASONER_EFFORT") else default):
            fast = GlobalFastPathConfig()
            reviewer = GlobalReviewerConfig()
        self.assertEqual(fast.reasoning_effort, "medium")
        self.assertEqual(reviewer.reasoning_effort, "high")
        for cls, config in ((GlobalFastLLMAdapter, fast), (GlobalReasonerLLMAdapter, reviewer)):
            adapter = cls.__new__(cls)
            adapter.config, adapter.client = config, Mock()
            adapter.extract_json("Instructions JSON", "Dossier")
            self.assertTrue(adapter.client.generate.call_args.kwargs["background"])
            self.assertEqual(adapter.client.generate.call_args.kwargs["reasoning_effort"], config.reasoning_effort)

    def test_background_routing_preserves_normal_chat_call_path(self):
        for background in (True, False):
            client = self.client()
            client._provider_order = lambda: ["openai"]
            client._models_for_provider = lambda *args: ["gpt-5"]
            client._prepare_prompt = lambda **kwargs: (kwargs["prompt"], {})
            client._generate_openai = Mock(return_value=("normal", {}))
            client._generate_openai_background = Mock(return_value=("background", {}))
            with patch("modules.LLM.llm_client._env", return_value=""), \
                 patch("modules.LLM.llm_client._env_int", return_value=0):
                text = client._generate_without_capacity_limit("JSON dossier", background=background)
            self.assertEqual(text, "background" if background else "normal")
            self.assertEqual(client._generate_openai.call_count, int(not background))
            self.assertEqual(client._generate_openai_background.call_count, int(background))


class TargetedReviewTests(unittest.TestCase):
    def setup_core(self, *, literature=False, admitted=True, fail=False, cache=None):
        doc = DocumentInput("D", "D.docx", "État de l'art\nL'article rapporte 75 % de compilabilité avec PLBART."
                            if literature else "Travaux du projet\nNous avons testé SCoT4UT sur des classes complètes.")
        passages = build_passages([doc])
        statement = "75 % de compilabilité avec PLBART" if literature else "Nous avons testé SCoT4UT"
        row = {"kind": "result" if literature else "demarche", "statement": statement,
               "source_role": "context", "temporal_status": "performed",
               "passage_ids": [passages[0].passage_id], "document_ids": ["D"], "needs_review": True}
        fast = GlobalFastExtractor(None, GlobalFastPathConfig(), cache)
        items = fast._items([row], {p.passage_id: p for p in passages})
        fast.extract = Mock(return_value=(items, {"llm_calls": 1, "fingerprint": "test-corpus"}))
        response = {"decisions": [{
            "item_id": item.item_id, "original_item_ids": [item.item_id],
            "evidence_ids": [item.evidence.evidence_id], "source_role": "project_evidence" if admitted else "context",
            "kind": "result" if literature else "method", "project_admission": admitted,
            "uncertain": not admitted, "duplicate_of": None,
            "related_project_evidence_ids": [], "related_lock_ids": [], "reason": "Exact project evidence",
        } for item in items]}
        llm = Mock()
        llm.extract_json = Mock(side_effect=RuntimeError("review failed") if fail else None,
                                return_value=response)
        reviewer = GlobalProjectReviewer(llm, GlobalReviewerConfig())
        core = EnnoDiagnosticV2(semantic_llm=Mock(), fast_extractor=fast,
                                global_reviewer=reviewer, config=PipelineConfig(run_frascati=False))
        core.extractor.extract = Mock(side_effect=AssertionError("legacy extraction forbidden"))
        core.consolidator.consolidate = lambda items: []
        core.qualifier.qualify = lambda entities, **kwargs: entities
        return core, [doc], llm

    def test_uncertain_fact_reclassified_and_admitted_without_overwriting_raw(self):
        core, docs, llm = self.setup_core()
        result = core.run(docs)
        audit = result.global_review["targeted_reviewer"]
        self.assertEqual(llm.extract_json.call_count, 1)
        self.assertEqual(audit["newly_admitted_count"], 1)
        self.assertEqual(audit["changed_kind_count"], 1)
        self.assertEqual(audit["new_facts_count"], 0)
        self.assertEqual(result.global_review["coverage"], 1.0)
        raw = result.extractions[0]
        self.assertTrue(raw.needs_review)
        self.assertEqual(raw.kind, "demarche")
        view, entities = reviewed_fact_view(result.extractions, result.entities, result.global_review)
        self.assertEqual(view[0].kind, "method")
        self.assertFalse(view[0].needs_review)
        self.assertTrue(view[0].metadata["project_admission"])
        self.assertEqual(view[0].evidence, raw.evidence)
        self.assertEqual(len(entities), 1)
        self.assertEqual(len(build_project_fact_lists(view, entities)["method"]), 1)

    def test_literature_admission_cannot_bypass_fast_validation(self):
        core, docs, _ = self.setup_core(literature=True)
        result = core.run(docs)
        decision = result.global_review["decisions"][0]
        self.assertFalse(decision["project_admission"])
        self.assertTrue(decision["needs_review"])
        self.assertIn("literature_evidence_as_project_fact", decision["reason"])
        view, entities = reviewed_fact_view(result.extractions, result.entities, result.global_review)
        self.assertEqual(view, [])
        self.assertEqual(entities, [])

    def test_reviewer_failure_preserves_fast_coverage_and_excludes_uncertain_fact(self):
        core, docs, _ = self.setup_core(fail=True)
        result = core.run(docs)
        self.assertEqual(result.global_review["status"], "complete")
        self.assertEqual(result.global_review["coverage"], 1.0)
        self.assertEqual(result.global_review["targeted_reviewer"]["status"], "fallback")
        self.assertFalse(result.global_review["decisions"][0]["project_admission"])
        self.assertEqual(len(result.extractions), 1)

    def test_targeted_cache_replay_avoids_llm_and_revalidates_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            core, docs, llm = self.setup_core(cache=Path(directory))
            core.run(docs)
            cached = core.run(docs)
            self.assertEqual(llm.extract_json.call_count, 1)
            self.assertTrue(cached.global_review["targeted_reviewer"]["cache_hit"])
            path = next(Path(directory).glob("targeted_review_*.json"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["decisions"][0]["evidence_ids"] = ["invented"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            repaired = core.run(docs)
            self.assertEqual(llm.extract_json.call_count, 2)
            self.assertFalse(repaired.global_review["targeted_reviewer"]["cache_hit"])

    def test_changed_corpus_invalidates_targeted_review_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            core, docs, llm = self.setup_core(cache=Path(directory))
            core.run(docs)
            items, meta = core.fast_extractor.extract.return_value
            core.fast_extractor.extract.return_value = (items, {**meta, "fingerprint": "changed-corpus"})
            result = core.run(docs)
            self.assertEqual(llm.extract_json.call_count, 2)
            self.assertFalse(result.global_review["targeted_reviewer"]["cache_hit"])

    def test_unverified_evidence_is_never_admitted_by_targeted_reviewer(self):
        core, docs, _ = self.setup_core()
        core.fast_extractor.extract.return_value[0][0].evidence.quote_verified = False
        result = core.run(docs)
        self.assertFalse(result.global_review["decisions"][0]["project_admission"])
        self.assertTrue(result.global_review["decisions"][0]["needs_review"])
        self.assertFalse(result.extractions[0].evidence.quote_verified)


if __name__ == "__main__":
    unittest.main()
