"""Offline additive-completeness regressions; no SDK calls or diagnostic runs."""
import copy
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.completeness_auditor import AUDIT_SYSTEM, CompletenessAuditor
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalFastPathConfig, PipelineConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_fast_path import GLOBAL_SYSTEM, GlobalFastExtractor, build_passages
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.pipeline import EnnoDiagnosticV2
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
from global_fast_path_regression import fact
from lock_test_fixtures import CoverageQueueLLM as QueueLLM


class CompletenessTests(unittest.TestCase):
    def setUp(self):
        self.config = GlobalFastPathConfig(enabled=True)
        self.docs = [DocumentInput("DE", "Travaux.docx", "Travaux du projet\n"
                     "Nous testons la transformation du modèle source par des règles vers le modèle cible. "
                     "Nous faisons varier la taille des lots et mesurons la couverture obtenue. "
                     "Les échecs de compilation persistent lors des essais répétés.")]
        self.passages = build_passages(self.docs)
        self.p = self.passages[0]
        self.initial = [fact("demarche", "Nous testons la transformation du modèle source", self.p),
                        fact("lock", "Les échecs de compilation persistent lors des essais répétés", self.p)]
        self.validator = GlobalFastExtractor(None, self.config)._validate

    def audit(self, proposals, initial=None, docs=None, cache=None):
        passages = build_passages(docs or self.docs)
        llm = QueueLLM([{"facts": proposals}] if not isinstance(proposals, Exception) else [proposals])
        auditor = CompletenessAuditor(llm, self.config, cache)
        additions, meta = auditor.audit(passages, self.initial if initial is None else initial, self.validator)
        return additions, meta, llm

    def extract(self, audit_response, cache=None):
        fast_llm = QueueLLM([{"facts": self.initial, "reviewed_passage_ids": [p.passage_id for p in self.passages]}])
        audit_llm = QueueLLM([audit_response])
        extractor = GlobalFastExtractor(fast_llm, self.config, cache,
            completeness_auditor=CompletenessAuditor(audit_llm, self.config, cache))
        items, meta = extractor.extract(self.docs)
        return items, meta, fast_llm, audit_llm

    def test_initial_facts_and_order_are_immutable(self):
        before = copy.deepcopy(self.initial)
        new = fact("method", "Transformation du modèle source par des règles vers le modèle cible", self.p)
        additions, _, _ = self.audit([new])
        self.assertEqual(self.initial, before)
        self.assertEqual([*self.initial, *additions][:len(before)], before)
        self.assertEqual([f["kind"] for f in self.initial], ["demarche", "lock"])

    def test_anax_method_added_from_demarche(self):
        row = fact("method", "Transformation du modèle source par des règles vers le modèle cible", self.p)
        additions, meta, llm = self.audit([row])
        self.assertEqual(additions, [row])
        self.assertEqual(meta["llm_calls"], 1)
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("FAITS INITIAUX", llm.calls[0][1])
        self.assertIn(json.dumps(self.p.text, ensure_ascii=False), llm.calls[0][1])

    def test_road_methods_recovered_without_reclassifying_steps(self):
        doc = DocumentInput("ROAD", "Road.docx", "Travaux du projet\n"
                            "Nous polygonisons avec alpha-shape et un modèle probabiliste Viterbi à transitions. "
                            "Nous entraînons DeepLab et HRNet avec RMSProp et comparons les méthodes de polygonisation.")
        p = build_passages([doc])[0]
        initial = [fact("demarche", "Nous entraînons DeepLab et HRNet avec RMSProp", p)]
        statements = ["Polygonisation avec alpha-shape", "Modèle probabiliste Viterbi à transitions",
                      "Architecture DeepLab", "Architecture HRNet", "Optimisation avec RMSProp",
                      "Méthodes de polygonisation"]
        rows = [fact("method", text, p) for text in statements]
        added, _, _ = self.audit(rows, initial=initial, docs=[doc])
        self.assertEqual(added, rows)
        self.assertEqual(initial[0]["kind"], "demarche")

    def test_metric_and_parameter_can_be_added(self):
        rows = [fact("parameter", "Taille des lots", self.p), fact("metric", "Couverture obtenue", self.p)]
        added, _, _ = self.audit(rows)
        self.assertEqual(added, rows)

    def test_added_lock_still_reaches_existing_qualifier(self):
        # Only the additive stage is injected; the normal qualifier runs offline.
        self.initial = self.initial[:1]
        lock = fact("lock", "Les échecs de compilation persistent lors des essais répétés", self.p)
        fast_llm = QueueLLM([{"facts": self.initial, "reviewed_passage_ids": [self.p.passage_id]}])
        audit_llm = QueueLLM([{"facts": [lock]}])
        fast = GlobalFastExtractor(fast_llm, self.config,
            completeness_auditor=CompletenessAuditor(audit_llm, self.config))
        qualifier_llm = QueueLLM([{"qualifications": []}, {"qualifications": []}])
        core = EnnoDiagnosticV2(semantic_llm=QueueLLM([]), qualification_llm=qualifier_llm,
                               config=PipelineConfig(run_frascati=False), fast_extractor=fast)
        core.extractor.extract = lambda _: self.fail("legacy semantic extraction invoked")
        output = core.run(self.docs)
        locks = [e for e in output.entities if e.kind == "lock"]
        self.assertEqual(len(locks), 1)
        self.assertEqual(len(qualifier_llm.calls), 2)
        self.assertEqual(locks[0].metadata["qualification_status"], "NOT_EVALUATED")

    def test_invalid_passage_document_enum_and_support_rejected_individually(self):
        good = fact("method", "Transformation du modèle source par des règles", self.p)
        bad = [dict(good, passage_ids=["invented"]), dict(good, document_ids=["outside"]),
               dict(good, kind="constraint"), dict(good, source_role="unknown"),
               dict(good, temporal_status="yesterday"), dict(good, needs_review="false"),
               dict(good, statement="Unrelated invented quantum cryptographic mechanism")]
        added, meta, llm = self.audit([*bad, good])
        self.assertEqual(added, [good])
        self.assertEqual(len(meta["rejected_candidates"]), len(bad))
        self.assertEqual(len(llm.calls), 1)  # invalid additions cause no retry

    def test_exact_and_reordered_semantic_duplicate_not_added(self):
        original = fact("method", "Transformation modèle source règles modèle cible", self.p)
        equivalent = fact("method", "Règles transformation modèle source modèle cible", self.p)
        added, meta, _ = self.audit([original, equivalent], initial=[original])
        self.assertEqual(added, [])
        self.assertEqual(meta["duplicate_count"], 2)

    def test_duplicate_additions_not_appended_twice(self):
        row = fact("method", "Transformation du modèle source par des règles", self.p)
        added, meta, _ = self.audit([row, row])
        self.assertEqual(added, [row])
        self.assertEqual(meta["duplicate_count"], 1)

    def test_reverse_transformation_is_not_a_semantic_duplicate(self):
        original = fact("method", "Transformation modèle source règles modèle cible", self.p)
        reverse = fact("method", "Transformation modèle cible règles modèle source", self.p)
        added, meta, _ = self.audit([reverse], initial=[original])
        self.assertEqual(added, [reverse])
        self.assertEqual(meta["duplicate_count"], 0)

    def test_different_categories_values_and_negation_remain_distinct(self):
        doc = DocumentInput("MEASURE", "Mesure.docx", "Nous mesurons la couverture à 20 % et à 30 %. "
                            "La couverture progresse et la couverture ne progresse pas.")
        p = build_passages([doc])[0]
        initial = [fact("result", "Couverture 20 %", p)]
        rows = [fact("metric", "Couverture", p), fact("result", "Couverture 30 %", p),
                fact("result", "La couverture progresse", p), fact("result", "La couverture ne progresse pas", p)]
        added, _, _ = self.audit(rows, initial=initial, docs=[doc])
        self.assertEqual(added, rows)

    def test_no_fact_or_failure_preserves_initial_items_exactly(self):
        baseline = GlobalFastExtractor(QueueLLM([{"facts": self.initial,
            "reviewed_passage_ids": [p.passage_id for p in self.passages]}]), self.config)
        expected, _ = baseline.extract(self.docs)
        for response in ({"facts": []}, RuntimeError("offline audit failure"), {"not_facts": []}):
            with self.subTest(response=response):
                actual, _, _, _ = self.extract(response)
                self.assertEqual([asdict(i) for i in actual], [asdict(i) for i in expected])

    def test_initial_item_and_evidence_ids_stay_identical_when_appending(self):
        baseline, _, _, _ = self.extract({"facts": []})
        row = fact("method", "Transformation du modèle source par des règles", self.p)
        actual, _, _, _ = self.extract({"facts": [row]})
        self.assertEqual([asdict(i) for i in actual[:len(baseline)]], [asdict(i) for i in baseline])
        self.assertEqual(len(actual), len(baseline) + 1)

    def test_added_facts_have_exact_highlight_positions_and_evidence(self):
        row = fact("method", "Transformation du modèle source par des règles", self.p)
        items, _, _, _ = self.extract({"facts": [row]})
        evidence = items[-1].evidence
        self.assertEqual(evidence.passage_id, self.p.passage_id)
        self.assertEqual(evidence.document_id, self.p.document_id)
        self.assertEqual(self.docs[0].text[evidence.char_start:evidence.char_end], evidence.quote)
        self.assertTrue(evidence.quote_verified)

    def test_literature_provenance_preserved_and_not_admitted(self):
        doc = DocumentInput("ART", "Article.docx", "État de l'art\nL'article décrit la transformation du modèle source par des règles vers le modèle cible.")
        p = build_passages([doc])[0]
        rows = [fact("method", "Transformation du modèle source par des règles", p, "literature")]
        added, _, _ = self.audit(rows, initial=[], docs=[doc])
        items = GlobalFastExtractor._items(added, {p.passage_id: p})
        self.assertEqual(items[0].metadata["source_role"], "literature")
        self.assertFalse(items[0].metadata["project_admission"])
        bad, meta, _ = self.audit([dict(rows[0], source_role="project_evidence")], initial=[], docs=[doc])
        self.assertEqual(bad, [])
        self.assertEqual(meta["rejected_candidates"][0]["reason"], "literature_evidence_as_project_fact")

    def test_uncertain_addition_never_automatically_admitted(self):
        row = fact("method", "Transformation du modèle source par des règles", self.p, needs_review=True)
        items, _, _, _ = self.extract({"facts": [row]})
        self.assertTrue(items[-1].needs_review)
        self.assertFalse(items[-1].metadata["project_admission"])

    def test_audit_cache_hit_has_zero_global_or_audit_call(self):
        row = fact("method", "Transformation du modèle source par des règles", self.p)
        with tempfile.TemporaryDirectory() as cache:
            first, _, _, _ = self.extract({"facts": [row]}, cache)
            fast_llm, audit_llm = QueueLLM([]), QueueLLM([])
            extractor = GlobalFastExtractor(fast_llm, self.config, cache,
                completeness_auditor=CompletenessAuditor(audit_llm, self.config, cache))
            second, meta = extractor.extract(self.docs)
            self.assertEqual(second, first)
            self.assertEqual(fast_llm.calls, [])
            self.assertEqual(audit_llm.calls, [])
            self.assertTrue(meta["completeness_audit"]["cache_hit"])
            self.assertEqual(meta["llm_calls"], 0)

    def test_cache_invalidated_by_initial_facts_registry_prompt_model_effort(self):
        auditor = CompletenessAuditor(None, self.config)
        key = auditor._fingerprint(self.passages, self.initial)
        variants = [dict(self.initial[0], statement="Transformation modèle cible"),
                    dict(self.initial[0], kind="method")]
        for variant in variants:
            self.assertNotEqual(key, auditor._fingerprint(self.passages, [variant, self.initial[1]]))
        changed = build_passages([DocumentInput("DE", "Travaux.docx", self.docs[0].text + " Modification.")])
        self.assertNotEqual(key, auditor._fingerprint(changed, self.initial))
        for config in (GlobalFastPathConfig(model="other"), GlobalFastPathConfig(reasoning_effort="high")):
            self.assertNotEqual(key, CompletenessAuditor(None, config)._fingerprint(self.passages, self.initial))
        with patch("agents.EnnoDiagnostic.V2.ennodiagnostic_v2.completeness_auditor.AUDIT_SYSTEM", AUDIT_SYSTEM + " Nouvelle version"):
            self.assertNotEqual(key, auditor._fingerprint(self.passages, self.initial))

    def test_changed_initial_facts_triggers_new_audit(self):
        with tempfile.TemporaryDirectory() as cache:
            self.audit([], cache=cache)
            _, meta, llm = self.audit([], initial=self.initial[:1], cache=cache)
            self.assertFalse(meta["cache_hit"])
            self.assertEqual(len(llm.calls), 1)

    def test_normal_path_adds_exactly_one_call_and_keeps_lock_prompt(self):
        _, meta, fast_llm, audit_llm = self.extract({"facts": []})
        self.assertEqual(len(fast_llm.calls), 1)
        self.assertEqual(len(audit_llm.calls), 1)
        self.assertEqual(meta["llm_calls"], 2)
        self.assertEqual(fast_llm.calls[0][0], GLOBAL_SYSTEM)
        self.assertIn("N'enlève et ne reclassifie aucun fait déjà extrait", GLOBAL_SYSTEM)

    def test_full_runtime_wires_auditor_only_into_fast_path(self):
        from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.full_runtime import EnnoDiagnosticV2FullRuntime
        fake = QueueLLM([])
        runtime = EnnoDiagnosticV2FullRuntime(semantic_llm=fake, consolidation_llm=fake,
            qualification_llm=fake, family_llm=fake, synthesis_llm=fake, rnd_llm=fake,
            frascati_llm=fake, global_reviewer_llm=fake, fast_llm=fake, completeness_llm=fake,
            fast_config=self.config)
        self.assertIsInstance(runtime.core.fast_extractor.completeness_auditor, CompletenessAuditor)
        disabled = EnnoDiagnosticV2FullRuntime(semantic_llm=fake, consolidation_llm=fake,
            qualification_llm=fake, family_llm=fake, synthesis_llm=fake, rnd_llm=fake,
            frascati_llm=fake, global_reviewer_llm=fake, fast_config=GlobalFastPathConfig(enabled=False))
        self.assertIsNone(disabled.core.fast_extractor)

    def test_large_audit_batches_cover_every_passage_without_truncation(self):
        docs = [DocumentInput(str(i), f"D{i}.txt", "Travaux du projet\n" + ("Nous testons transformation modèle source et cible. " * 100)) for i in range(3)]
        passages = build_passages(docs)
        config = GlobalFastPathConfig(max_prompt_chars=len(AUDIT_SYSTEM) + 1600)
        auditor = CompletenessAuditor(QueueLLM([]), config)
        batches = auditor._batches(passages, [])
        self.assertGreater(len(batches), 1)
        self.assertLess(len(batches), len(passages))
        self.assertEqual(sorted(p.passage_id for rows, _, _ in batches for p in rows), sorted(p.passage_id for p in passages))
        self.assertTrue(all(auditor._fits(prompt) for _, _, prompt in batches))
        self.assertGreater(len({p.document_id for p in batches[0][0]}), 1)
        llm = QueueLLM([{"facts": []}] * len(batches))
        auditor.llm = llm
        added, meta = auditor.audit(passages, [], self.validator)
        self.assertEqual(added, [])
        self.assertEqual(meta["status"], "complete")
        self.assertEqual(meta["llm_calls"], len(batches))

    def test_batch_failure_discards_only_audit_not_initial(self):
        passages = build_passages([DocumentInput("DE", "D.txt", self.docs[0].text * 40)])
        config = GlobalFastPathConfig(max_prompt_chars=len(AUDIT_SYSTEM) + 1600)
        llm = QueueLLM([{"facts": []}, RuntimeError("batch failure")])
        before = copy.deepcopy(self.initial)
        added, meta = CompletenessAuditor(llm, config).audit(passages, self.initial, self.validator)
        self.assertEqual(added, [])
        self.assertEqual(meta["status"], "failed")
        self.assertEqual(self.initial, before)

    def test_auditor_initialization_failure_keeps_fast_extractor(self):
        from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.full_runtime import EnnoDiagnosticV2FullRuntime
        fake = QueueLLM([])
        with patch("agents.EnnoDiagnostic.V2.ennodiagnostic_v2.full_runtime.CompletenessAuditor",
                   side_effect=RuntimeError("auditor initialization failed")):
            runtime = EnnoDiagnosticV2FullRuntime(semantic_llm=fake, consolidation_llm=fake,
                qualification_llm=fake, family_llm=fake, synthesis_llm=fake, rnd_llm=fake,
                frascati_llm=fake, global_reviewer_llm=fake, fast_llm=fake, fast_config=self.config)
        self.assertIsNotNone(runtime.core.fast_extractor)
        self.assertIsNone(runtime.core.fast_extractor.completeness_auditor)

    def test_auditor_cannot_mutate_initial_even_before_raising(self):
        class BrokenAuditor:
            def audit(self, passages, rows, validate):
                rows[0]["kind"] = "method"
                rows.clear()
                raise RuntimeError("isolated audit exception")
        fast = GlobalFastExtractor(None, self.config, completeness_auditor=BrokenAuditor())
        before = copy.deepcopy(self.initial)
        result, meta = fast._audit_completeness(self.initial, self.passages)
        self.assertEqual(result, before)
        self.assertEqual(self.initial, before)
        self.assertEqual(meta["status"], "failed")

    def test_invalid_cached_addition_is_revalidated_and_not_used(self):
        row = fact("method", "Transformation du modèle source par des règles", self.p)
        with tempfile.TemporaryDirectory() as cache:
            self.audit([row], cache=cache)
            path = next(Path(cache).glob("completeness_*.json"))
            data = json.loads(path.read_text(encoding="utf-8"))
            data["facts"][0]["passage_ids"] = ["invented"]
            path.write_text(json.dumps(data), encoding="utf-8")
            added, meta, llm = self.audit([], cache=cache)
            self.assertEqual(added, [])
            self.assertFalse(meta["cache_hit"])
            self.assertEqual(len(llm.calls), 1)

    def test_failed_audit_not_cached_initial_extraction_still_cached(self):
        with tempfile.TemporaryDirectory() as cache:
            items, _, _, _ = self.extract(RuntimeError("audit failure"), cache)
            self.assertEqual(list(Path(cache).glob("completeness_*.json")), [])
            fast_llm, audit_llm = QueueLLM([]), QueueLLM([{"facts": []}])
            fast = GlobalFastExtractor(fast_llm, self.config, cache,
                completeness_auditor=CompletenessAuditor(audit_llm, self.config, cache))
            again, meta = fast.extract(self.docs)
            self.assertEqual(again, items)
            self.assertEqual(fast_llm.calls, [])
            self.assertEqual(len(audit_llm.calls), 1)
            self.assertEqual(meta["llm_calls"], 1)

    def test_batch_cannot_cite_passage_not_supplied_to_it(self):
        docs = [DocumentInput("DE", "D.txt", self.docs[0].text * 40)]
        passages = build_passages(docs)
        config = GlobalFastPathConfig(max_prompt_chars=len(AUDIT_SYSTEM) + 1600)
        auditor = CompletenessAuditor(None, config)
        batches = auditor._batches(passages, [])
        row = fact("method", "Transformation du modèle source par des règles", passages[-1])
        auditor.llm = QueueLLM([{"facts": [row]}, *({"facts": []} for _ in batches[1:])])
        added, meta = auditor.audit(passages, [], self.validator)
        self.assertEqual(added, [])
        self.assertEqual(meta["rejected_candidates"][0]["candidate"], row)

    def test_output_cache_invalidated_by_changed_document(self):
        with tempfile.TemporaryDirectory() as cache:
            self.audit([], cache=cache)
            docs = [DocumentInput("DE", "Travaux.docx", self.docs[0].text + " Nouvelle expérience.")]
            _, meta, llm = self.audit([], docs=docs, cache=cache)
            self.assertFalse(meta["cache_hit"])
            self.assertEqual(len(llm.calls), 1)

    def test_prompt_uses_generic_methods_not_project_test_names(self):
        for name in ("ANAX", "alpha-shape", "Viterbi", "DeepLab", "HRNet", "RMSProp", "AI-CODE"):
            self.assertNotIn(name, AUDIT_SYSTEM)

    def test_ai_code_existing_locks_preserved_with_zero_additions(self):
        # Existing offline AI-CODE-style evidence, no extraction API invoked.
        docs = [DocumentInput("AI-CODE", "Experiments.docx", "Travaux du projet\n"
                "Les échecs de compilation persistent lors des essais répétés. "
                "La validation de la qualité des tests reste incertaine. "
                "Les modèles locaux restent limités par les ressources disponibles.")]
        p = build_passages(docs)[0]
        rows = [fact("lock", statement, p) for statement in (
            "Les échecs de compilation persistent lors des essais répétés",
            "La validation de la qualité des tests reste incertaine",
            "Les modèles locaux restent limités par les ressources disponibles")]
        payload = {"facts": rows, "reviewed_passage_ids": [p.passage_id]}
        baseline, _ = GlobalFastExtractor(QueueLLM([payload]), self.config).extract(docs)
        audited, _ = GlobalFastExtractor(QueueLLM([payload]), self.config,
            completeness_auditor=CompletenessAuditor(QueueLLM([{"facts": []}]), self.config)).extract(docs)
        self.assertEqual([asdict(i) for i in audited], [asdict(i) for i in baseline])

    def test_real_ai_code_cached_facts_and_locks_preserved_and_audit_fits_one_call(self):
        year = Path(r"C:\EnnoSmartData\object_storage_v2\runtime\organismes\test\projects\test3\years\2024")
        corpus = year / "documents/processed/fulltext_rag_v1"
        caches = list((year / "analysis_cache/global_fast").glob("*.json"))
        caches = [p for p in caches if not p.name.startswith(("targeted_review_", "completeness_"))]
        if not corpus.is_dir() or not caches:
            self.skipTest("AI-CODE fulltext and validated cache not available")
        docs = []
        for path in sorted(corpus.glob("*.json")):
            if path.name == "manifest.json":
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if any(str(data.get(key) or "").lower().startswith("cir_final") for key in ("content_origin", "document_type")):
                continue
            docs.append(DocumentInput(str(data.get("document_id") or data.get("text_sha256") or path.stem),
                str(data.get("document") or data.get("file_name") or path.stem), str(data.get("text") or "").strip()))
        passages = build_passages(docs)
        registry = {p.passage_id: p for p in passages}
        cache = max(caches, key=lambda p: p.stat().st_mtime)
        rows = json.loads(cache.read_text(encoding="utf-8"))["facts"]
        initial = [self.validator(row, registry) for row in rows]
        before = copy.deepcopy(initial)
        expected = GlobalFastExtractor._items(initial, registry)
        self.assertTrue(any(item.kind == "lock" for item in expected))
        llm = QueueLLM([{"facts": []}])
        added, meta = CompletenessAuditor(llm, self.config).audit(passages, initial, self.validator)
        actual = GlobalFastExtractor._items([*initial, *added], registry)
        self.assertEqual(initial, before)
        self.assertEqual([asdict(i) for i in actual], [asdict(i) for i in expected])
        self.assertEqual(meta["mode"], "global")
        self.assertEqual(meta["llm_calls"], 1)
        self.assertEqual(len(llm.calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
