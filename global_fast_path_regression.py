"""Contrôles hors réseau du Global Fast Path."""

import json
import tempfile
import unittest
from pathlib import Path

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalFastPathConfig, PipelineConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_fast_path import GlobalFastExtractor, build_passages
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_project_reviewer import reviewed_fact_view
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.pipeline import EnnoDiagnosticV2
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.structured_facts import build_project_fact_lists


def fact(kind, statement, passage, role="project_evidence", **extra):
    return {
        "kind": kind, "statement": statement, "source_role": role,
        "temporal_status": "performed", "passage_ids": [passage.passage_id],
        "document_ids": [passage.document_id], "needs_review": False,
        **extra,
    }


class QueueLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def extract_json(self, system, prompt):
        self.calls.append((system, prompt))
        if not self.responses:
            raise AssertionError("unexpected_llm_call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class GlobalFastPathTests(unittest.TestCase):
    def setUp(self):
        self.config = GlobalFastPathConfig(enabled=True)
        self.docs = [
            DocumentInput("DE", "Travaux.docx", "Travaux du projet\nNous avons testé SCoT4UT sur des classes complètes."),
            DocumentInput("ART", "Article.docx", "État de l'art\nL'article rapporte 75 % de compilabilité avec PLBART."),
        ]
        self.passages = build_passages(self.docs)

    def extractor(self, llm, cache=None):
        return GlobalFastExtractor(llm, self.config, cache)

    def test_ai_code_real_corpus_fits_global(self):
        root = Path(r"C:\EnnoSmartData\object_storage_v2\runtime\organismes\test\projects\test3\years\2024\documents\processed\fulltext_rag_v1")
        if not root.is_dir():
            self.skipTest("local AI-CODE corpus unavailable")
        docs = []
        for path in sorted(root.glob("*.json")):
            if path.name == "manifest.json":
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("content_origin") or "").lower().startswith("cir_final"):
                continue
            docs.append(DocumentInput(
                str(data.get("document_id") or data.get("text_sha256") or path.stem),
                str(data.get("document") or data.get("file_name") or path.stem),
                str(data.get("text") or "").strip(),
            ))
        preflight = self.extractor(None).preflight(docs)
        self.assertEqual(preflight["mode"], "global")
        self.assertEqual(preflight["documents"], 7)
        self.assertEqual(preflight["source_chars"], 90170)

    def test_passage_registry_is_exact_and_complete(self):
        for doc in self.docs:
            parts = [p for p in self.passages if p.document_id == doc.document_id]
            self.assertEqual("".join(p.text for p in parts), doc.text)
            self.assertTrue(all(doc.text[p.char_start:p.char_end] == p.text for p in parts))

    def test_one_call_no_semantic_chunk_extract(self):
        p = self.passages[0]
        llm = QueueLLM([{"reviewed_passage_ids": [x.passage_id for x in self.passages],
                         "facts": [fact("method", "Nous avons testé SCoT4UT", p)]}])
        core = EnnoDiagnosticV2(
            semantic_llm=llm, config=PipelineConfig(run_frascati=False),
            fast_extractor=self.extractor(llm),
        )
        core.extractor.extract = lambda chunks: self.fail("semantic_extract called")
        core.consolidator.consolidate = lambda items: []
        core.qualifier.qualify = lambda entities, **kwargs: entities
        output = core.run(self.docs)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(output.global_review["status"], "complete")
        self.assertEqual(output.global_review["fast_path"]["llm_calls"], 1)
        viewed_items, viewed_entities = reviewed_fact_view(
            output.extractions, output.entities, output.global_review,
        )
        self.assertEqual(len(viewed_items), 1)
        self.assertEqual(len(viewed_entities), 1)

    def test_literature_is_auditable_but_not_project_entity(self):
        project, article = self.passages
        llm = QueueLLM([{"reviewed_passage_ids": [p.passage_id for p in self.passages],
                         "facts": [fact("method", "Nous avons testé SCoT4UT", project),
                                   fact("result", "75 % de compilabilité avec PLBART", article, "literature")]}])
        items, _ = self.extractor(llm).extract(self.docs)
        self.assertEqual([i.metadata["source_role"] for i in items], ["project_evidence", "literature"])
        self.assertFalse(items[1].metadata["project_admission"])

    def test_literature_result_does_not_enter_project_synthesis_lists(self):
        project, article = self.passages
        llm = QueueLLM([{"reviewed_passage_ids": [p.passage_id for p in self.passages],
                         "facts": [fact("method", "Nous avons testé SCoT4UT", project),
                                   fact("result", "75 % de compilabilité avec PLBART", article, "literature")]}])
        core = EnnoDiagnosticV2(
            semantic_llm=llm, config=PipelineConfig(run_frascati=False),
            fast_extractor=self.extractor(llm),
        )
        core.extractor.extract = lambda chunks: self.fail("semantic_extract called")
        core.consolidator.consolidate = lambda items: []
        core.qualifier.qualify = lambda entities, **kwargs: entities
        output = core.run(self.docs)
        self.assertEqual(len(output.extractions), 2)
        self.assertEqual([entity.kind for entity in output.entities], ["method"])
        view = reviewed_fact_view(output.extractions, output.entities, output.global_review)
        lists = build_project_fact_lists(*view)
        self.assertEqual(len(lists["method"]), 1)
        self.assertEqual(lists["result"], [])

    def test_literature_result_cannot_be_admitted_as_project(self):
        article = self.passages[1]
        extractor = self.extractor(None)
        with self.assertRaisesRegex(ValueError, "literature_evidence_as_project_fact"):
            extractor._validate(fact("result", "75 % de compilabilité avec PLBART", article),
                                {p.passage_id: p for p in self.passages})

    def test_invalid_passage_or_document_is_rejected(self):
        extractor = self.extractor(None)
        lookup = {p.passage_id: p for p in self.passages}
        bad = fact("method", "Nous avons testé SCoT4UT", self.passages[0])
        bad["passage_ids"] = ["p999"]
        with self.assertRaisesRegex(ValueError, "invalid_passage_id"):
            extractor._validate(bad, lookup)
        bad = fact("method", "Nous avons testé SCoT4UT", self.passages[0])
        bad["document_ids"] = ["OUTSIDE"]
        with self.assertRaisesRegex(ValueError, "document_id_mismatch"):
            extractor._validate(bad, lookup)

    def test_partial_passage_coverage_uses_targeted_second_call(self):
        p1, p2 = self.passages
        llm = QueueLLM([
            {"reviewed_passage_ids": [p1.passage_id], "facts": [fact("method", "Nous avons testé SCoT4UT", p1)]},
            {"reviewed_passage_ids": [p2.passage_id], "facts": [fact("result", "75 % de compilabilité avec PLBART", p2, "literature")]},
        ])
        items, meta = self.extractor(llm).extract(self.docs)
        self.assertEqual(meta["llm_calls"], 2)
        self.assertEqual(len(items), 2)
        self.assertIn(p2.passage_id, llm.calls[1][1])
        self.assertNotIn(p1.text, llm.calls[1][1])

    def test_invalid_fact_targeted_retry(self):
        p = self.passages[0]
        bad = fact("method", "Nous avons testé SCoT4UT", p)
        bad["document_ids"] = ["OUTSIDE"]
        llm = QueueLLM([
            {"reviewed_passage_ids": [x.passage_id for x in self.passages], "facts": [bad]},
            {"facts": [fact("method", "Nous avons testé SCoT4UT", p)]},
        ])
        items, meta = self.extractor(llm).extract(self.docs)
        self.assertEqual(meta["targeted_retries"], 1)
        self.assertTrue(items[0].metadata["project_admission"])

    def test_two_invalid_facts_share_one_targeted_retry(self):
        project, article = self.passages
        bad_project = fact("method", "Nous avons testé SCoT4UT", project)
        bad_article = fact("result", "75 % de compilabilité avec PLBART", article, "literature")
        bad_project["document_ids"] = ["OUTSIDE"]
        bad_article["document_ids"] = ["OUTSIDE"]
        fixed_project = {"candidate_id": "c0", **fact("method", "Nous avons testé SCoT4UT", project)}
        fixed_article = {"candidate_id": "c1", **fact("result", "75 % de compilabilité avec PLBART", article, "literature")}
        llm = QueueLLM([
            {"reviewed_passage_ids": [p.passage_id for p in self.passages],
             "facts": [bad_project, bad_article]},
            {"facts": [fixed_project, fixed_article]},
        ])
        items, meta = self.extractor(llm).extract(self.docs)
        self.assertEqual(meta["targeted_retries"], 1)
        self.assertEqual(meta["llm_calls"], 2)
        self.assertEqual(len(items), 2)

    def test_failed_retry_is_needs_review_and_not_admitted(self):
        p = self.passages[0]
        bad = fact("method", "Nous avons testé SCoT4UT", p)
        bad["document_ids"] = ["OUTSIDE"]
        llm = QueueLLM([
            {"reviewed_passage_ids": [x.passage_id for x in self.passages], "facts": [bad]},
            {"facts": []},
        ])
        items, _ = self.extractor(llm).extract(self.docs)
        self.assertTrue(items[0].needs_review)
        self.assertEqual(items[0].metadata["source_role"], "context")
        self.assertFalse(items[0].metadata["project_admission"])

    def test_cache_hit_and_document_change(self):
        p = self.passages[0]
        response = {"reviewed_passage_ids": [x.passage_id for x in self.passages],
                    "facts": [fact("method", "Nous avons testé SCoT4UT", p)]}
        llm = QueueLLM([response, response])
        with tempfile.TemporaryDirectory() as directory:
            extractor = self.extractor(llm, Path(directory))
            extractor.extract(self.docs)
            _, cached = extractor.extract(self.docs)
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(cached["llm_calls"], 0)
            self.assertEqual(len(llm.calls), 1)
            changed = [self.docs[0], DocumentInput("ART", "Article.docx", self.docs[1].text + "\nSuite.")]
            self.assertNotEqual(extractor._fingerprint(changed), extractor._fingerprint(self.docs))
            _, changed_meta = extractor.extract(changed)
            self.assertFalse(changed_meta["cache_hit"])
            self.assertEqual(len(llm.calls), 2)

    def test_large_dossier_uses_parallel_lots_and_final_reconciliation(self):
        from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_fast_path import GLOBAL_SYSTEM, _registry_prompt

        docs = [
            DocumentInput("A", "A.docx", "\n".join("Nous avons testé Alpha méthode technique. " * 9 for _ in range(12))),
            DocumentInput("B", "B.docx", "\n".join("Nous avons testé Beta méthode technique. " * 9 for _ in range(12))),
        ]
        # Reserve space for the current contract while keeping the fixture too
        # large for a single global call. This does not change runtime budgets.
        cfg = GlobalFastPathConfig(enabled=True, max_prompt_chars=len(GLOBAL_SYSTEM) + 4500)
        passages = build_passages(docs)
        extractor = GlobalFastExtractor(None, cfg)
        self.assertEqual(extractor.preflight(docs)["mode"], "multi_lot")
        lots = extractor._lots(passages)
        self.assertGreater(len(lots), 1)
        responses = []
        provisional = []
        for lot in lots:
            first = lot[0]
            row = fact("method", "Nous avons testé Alpha méthode technique" if first.document_id == "A" else "Nous avons testé Beta méthode technique", first)
            provisional.append(row)
            responses.append({"reviewed_passage_ids": [p.passage_id for p in lot], "facts": [row]})
        responses.append({"facts": [{"fact_id": f"f{i+1}", **row} for i, row in enumerate(provisional)]})
        llm = QueueLLM(responses)
        extractor.llm = llm
        items, meta = extractor.extract(docs)
        self.assertEqual(meta["mode"], "multi_lot")
        self.assertEqual(meta["llm_calls"], len(lots) + 1)
        self.assertEqual(len(items), len(lots))
        self.assertTrue(all(item.evidence.quote_verified for item in items))

    def test_fast_failure_uses_legacy(self):
        class FailingFast:
            def extract(self, documents):
                raise RuntimeError("test_failure")

        core = EnnoDiagnosticV2(
            semantic_llm=QueueLLM([]), config=PipelineConfig(run_frascati=False),
            fast_extractor=FailingFast(),
        )
        called = []
        core.extractor.extract = lambda chunks: called.append(True) or []
        core.consolidator.consolidate = lambda items: []
        core.qualifier.qualify = lambda entities, **kwargs: entities
        result = core.run(self.docs)
        self.assertEqual(called, [True])
        self.assertEqual(result.global_review["fast_path"]["status"], "fallback")


if __name__ == "__main__":
    unittest.main()
