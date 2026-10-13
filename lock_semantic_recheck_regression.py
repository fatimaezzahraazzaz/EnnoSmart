"""Offline LOCK recall contract and integration regressions.

QueueLLM returns authored fixtures, not predictions. These tests verify prompts,
traceability, additive processing and call counts; they do not measure GPT recall
or certify live AI-CODE/ANAX/Road outputs. No OpenAI calls or diagnostic runs.
"""
import copy
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2 import global_fast_path as fast
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2 import completeness_auditor as audit
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalFastPathConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.consolidation import GlobalLockConsolidator
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.qualification import LockQualifier
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
from global_fast_path_regression import fact
from lock_test_fixtures import CoverageQueueLLM as QueueLLM


class LockSemanticRecheckTests(unittest.TestCase):
    def setUp(self):
        self.config = GlobalFastPathConfig(enabled=True)

    def run_fixture(self, docs, initial, additions):
        passages = fast.build_passages(docs)
        initial_llm = QueueLLM([{"facts": initial,
                                "reviewed_passage_ids": [p.passage_id for p in passages]}])
        audit_llm = QueueLLM([{"facts": additions}])
        extractor = fast.GlobalFastExtractor(initial_llm, self.config,
            completeness_auditor=audit.CompletenessAuditor(audit_llm, self.config))
        items, meta = extractor.extract(docs)
        self.assertEqual(len(initial_llm.calls), 1)
        self.assertEqual(len(audit_llm.calls), 1)
        self.assertEqual(meta["llm_calls"], 2)
        self.assertEqual(meta["targeted_retries"], 0)
        self.assertEqual(initial_llm.calls[0][0], fast.GLOBAL_SYSTEM)
        self.assertEqual(audit_llm.calls[0][0], audit.AUDIT_SYSTEM)
        return items, meta, audit_llm

    def test_both_prompts_require_content_check_for_section_signal(self):
        for prompt in (fast.GLOBAL_SYSTEM, audit.AUDIT_SYSTEM):
            with self.subTest(prompt=prompt[:40]):
                for instruction in ("texte exact", "difficulté technique",
                                    "incertitude scientifique", "problème à résoudre",
                                    "seulement un SIGNAL", "objectif/exigence"):
                    self.assertIn(instruction, prompt)
        self.assertIn("Ne crée jamais un LOCK sur le seul titre", fast.GLOBAL_SYSTEM)
        self.assertIn("section « verrou » seule", audit.AUDIT_SYSTEM)

    def test_both_prompts_require_technical_content_and_no_invented_uncertainty(self):
        for prompt in (fast.GLOBAL_SYSTEM, audit.AUDIT_SYSTEM):
            with self.subTest(prompt=prompt[:40]):
                for instruction in ("limite persistante", "absence de méthode",
                                    "critère technique", "difficulté business",
                                    "organisationnelle seule", "métrique seule",
                                    "souhait de reproductibilité"):
                    self.assertIn(instruction, prompt)
        self.assertIn("contrainte seule", fast.GLOBAL_SYSTEM)
        self.assertIn("contrainte seule", audit.AUDIT_SYSTEM)
        self.assertIn("littérature seule", audit.AUDIT_SYSTEM)

    def test_recheck_is_additive_and_in_existing_calls(self):
        self.assertIn("Pour chaque fait important déjà classé", fast.GLOBAL_SYSTEM)
        self.assertIn("garde le fait initial intact", fast.GLOBAL_SYSTEM)
        self.assertIn("Cette relecture se fait dans le MÊME appel", fast.GLOBAL_SYSTEM)
        self.assertIn("Contrôle LOCK dans ce MÊME appel, uniquement additif", audit.AUDIT_SYSTEM)
        self.assertIn("OBJECTIVE/METRIC et LOCK peuvent", audit.AUDIT_SYSTEM)
        self.assertIn("Ne supprime, modifie ou reclasse aucun fact", audit.AUDIT_SYSTEM)
        self.assertIn("LOCK existant couvre déjà cette incertitude précise", audit.AUDIT_SYSTEM)

    def test_prompt_rules_are_generic_and_do_not_assign_valid_lock(self):
        for prompt in (fast.GLOBAL_SYSTEM, audit.AUDIT_SYSTEM):
            for name in ("AI-CODE", "ANAX", "Road Segmentation", "p14", "p15", "p16"):
                self.assertNotIn(name, prompt)
        self.assertIn("Ne qualifie jamais toi-même", fast.GLOBAL_SYSTEM)
        self.assertIn("LockQualifier reste seul responsable", audit.AUDIT_SYSTEM)

    def test_fast_objective_and_lock_can_share_exact_proof(self):
        doc = DocumentInput("D", "Critères.docx", "Verrous scientifiques ou techniques\n"
            "Nous visons un protocole d'évaluation. Aucun critère reproductible satisfaisant "
            "n'est établi ; cette difficulté empêche une évaluation fiable de la qualité.")
        p = fast.build_passages([doc])[0]
        rows = [fact("objective", "Nous visons un protocole d'évaluation", p),
                fact("lock", "Aucun critère reproductible satisfaisant n'est établi", p)]
        items, _, _ = self.run_fixture([doc], rows, [])
        self.assertEqual([i.kind for i in items], ["objective", "lock"])
        self.assertEqual(items[0].statement, rows[0]["statement"])
        self.assertEqual(asdict(items[0].evidence) | {"evidence_id": ""},
                         asdict(items[1].evidence) | {"evidence_id": ""})
        self.assertNotIn("qualification_status", items[1].metadata)

    def test_audit_metric_and_lock_coexist_without_reclassifying_metric(self):
        doc = DocumentInput("D", "Qualité.docx", "Travaux du projet\n"
            "Nous mesurons la couverture. La couverture seule ne caractérise pas la qualité ; "
            "aucun critère reproductible satisfaisant n'est établi.")
        p = fast.build_passages([doc])[0]
        rows = [fact("metric", "Nous mesurons la couverture", p)]
        before = copy.deepcopy(rows)
        added = fact("lock", "Aucun critère reproductible satisfaisant n'est établi", p)
        items, _, llm = self.run_fixture([doc], rows, [added])
        self.assertEqual(rows, before)
        self.assertEqual([i.kind for i in items], ["metric", "lock"])
        self.assertTrue(all(i.metadata["project_admission"] for i in items))
        self.assertIn(rows[0]["statement"], llm.calls[0][1])
        self.assertIn(json.dumps(p.text, ensure_ascii=False), llm.calls[0][1])

    def test_section_and_simple_objective_fixture_do_not_trigger_python_lock_creation(self):
        doc = DocumentInput("D", "Cadrage.docx", "Verrous scientifiques ou techniques\n"
                            "Définir un protocole d'évaluation et livrer une documentation.")
        p = fast.build_passages([doc])[0]
        row = fact("objective", "Définir un protocole d'évaluation", p)
        items, _, _ = self.run_fixture([doc], [row], [])
        self.assertEqual([i.kind for i in items], ["objective"])

    def test_section_and_real_technical_difficulty_fixture_accept_candidate(self):
        doc = DocumentInput("D", "Essais.docx", "Difficulté technique\n"
            "Les essais restent non reproductibles malgré plusieurs corrections du protocole.")
        p = fast.build_passages([doc])[0]
        lock = fact("lock", "Essais non reproductibles malgré plusieurs corrections du protocole", p)
        items, _, _ = self.run_fixture([doc], [lock], [])
        self.assertEqual(items[0].kind, "lock")
        self.assertTrue(items[0].metadata["project_admission"])
        self.assertEqual(items[0].evidence.quote, p.text)

    def test_literature_candidate_is_auditable_but_not_project(self):
        doc = DocumentInput("ART", "Article.docx", "État de l'art\n"
                            "L'article décrit des essais non reproductibles.")
        p = fast.build_passages([doc])[0]
        candidate = fact("lock", "Essais non reproductibles", p, "literature")
        items, _, _ = self.run_fixture([doc], [candidate], [])
        self.assertEqual(items[0].metadata["source_role"], "literature")
        self.assertFalse(items[0].metadata["project_admission"])
        additions, meta = audit.CompletenessAuditor(QueueLLM([
            {"facts": [dict(candidate, source_role="project_evidence")]}]), self.config).audit(
                [p], [], fast.GlobalFastExtractor(None, self.config)._validate)
        self.assertEqual(additions, [])
        self.assertEqual(meta["rejected_candidates"][0]["reason"], "literature_evidence_as_project_fact")

    def test_existing_lock_not_duplicated_by_audit(self):
        doc = DocumentInput("D", "Essais.docx", "Les essais restent non reproductibles.")
        p = fast.build_passages([doc])[0]
        row = fact("lock", "Les essais restent non reproductibles", p)
        items, meta, _ = self.run_fixture([doc], [row], [row])
        self.assertEqual(len(items), 1)
        self.assertEqual(meta["completeness_audit"]["duplicate_count"], 1)

    def test_new_lock_does_not_overwrite_any_initial_category_or_evidence_id(self):
        doc = DocumentInput("D", "Travaux.docx", "Nous visons un protocole fiable. "
            "Nous utilisons une analyse statique. Nous réalisons une campagne d'essais. "
            "Nous choisissons trois modèles. Nous mesurons la couverture. "
            "La couverture atteint 80 %. Les essais restent non reproductibles.")
        p = fast.build_passages([doc])[0]
        rows = [fact(kind, statement, p) for kind, statement in (
            ("objective", "Nous visons un protocole fiable"),
            ("method", "Nous utilisons une analyse statique"),
            ("demarche", "Nous réalisons une campagne d'essais"),
            ("parameter", "Nous choisissons trois modèles"),
            ("metric", "Nous mesurons la couverture"),
            ("result", "La couverture atteint 80 %"))]
        before, _, _ = self.run_fixture([doc], rows, [])
        after, _, _ = self.run_fixture([doc], rows,
            [fact("lock", "Les essais restent non reproductibles", p)])
        self.assertEqual([asdict(i) for i in before], [asdict(i) for i in after[:-1]])
        self.assertEqual(after[-1].kind, "lock")

    def test_anax_requirement_fixtures_remain_subject_to_unchanged_qualifier(self):
        doc = DocumentInput("ANAX", "Exigences.docx", "Verrous scientifiques ou techniques\n"
            "Définir le périmètre d'utilisation de l'IA. Garantir sûreté, sécurité et temps réel.")
        p = fast.build_passages([doc])[0]
        rows = [fact("objective", "Définir le périmètre d'utilisation de l'IA", p),
                fact("objective", "Garantir sûreté, sécurité et temps réel", p)]
        items, _, _ = self.run_fixture([doc], rows, [])
        self.assertFalse(any(i.kind == "lock" for i in items))
        # A model can still emit requirement candidates. Python must not grant
        # VALID_LOCK, and the existing qualifier must keep its own decision.
        candidates = fast.GlobalFastExtractor._items(
            [dict(row, kind="lock") for row in rows], {p.passage_id: p})
        entities = GlobalLockConsolidator(None).consolidate(candidates)
        answer = {"qualifications": [
            {"entity_id": e.entity_id, "status": "REQUIREMENT_ONLY",
             "reason": "Exigence seule dans cette fixture", "evidence_ids": [],
             "suggested_statement": "", "missing_evidence": []} for e in entities]}
        qualified = LockQualifier(QueueLLM([answer])).qualify(entities, extractions=candidates)
        self.assertTrue(qualified)
        self.assertTrue(all(e.metadata["qualification_status"] == "REQUIREMENT_ONLY" for e in qualified))

    def test_road_fixture_keeps_three_difficulties_without_dataset_or_metric_locks(self):
        doc = DocumentInput("ROAD", "Segmentation.docx", "Travaux du projet\n"
            "Nous visons une segmentation fiable. Le dataset contient 100 images. "
            "Nous mesurons IoU. La segmentation reste instable sur des labels décalés. "
            "La polygonisation produit des auto-intersections. "
            "Le lissage produit un biais de parallélisme.")
        p = fast.build_passages([doc])[0]
        rows = [fact("objective", "Nous visons une segmentation fiable", p),
                fact("metric", "Nous mesurons IoU", p),
                fact("parameter", "Le dataset contient 100 images", p)]
        locks = [fact("lock", s, p) for s in (
            "La segmentation reste instable sur des labels décalés",
            "La polygonisation produit des auto-intersections",
            "Le lissage produit un biais de parallélisme")]
        before, _, _ = self.run_fixture([doc], [*rows, *locks], [])
        after, _, _ = self.run_fixture([doc], [*rows, *locks], locks)
        self.assertEqual([asdict(i) for i in before], [asdict(i) for i in after])
        self.assertEqual([i.statement for i in after if i.kind == "lock"],
                         [r["statement"] for r in locks])

    def test_prompt_change_invalidates_both_caches_without_new_stage(self):
        doc = DocumentInput("D", "Travaux.docx", "Nous visons un protocole fiable.")
        p = fast.build_passages([doc])[0]
        row = fact("objective", "Nous visons un protocole fiable", p)
        old_fast = fast.GLOBAL_SYSTEM.split("Contrôle sémantique supplémentaire :")[0]
        old_audit = audit.AUDIT_SYSTEM.split("Contrôle LOCK dans ce MÊME appel")[0]
        with tempfile.TemporaryDirectory() as directory:
            initial = QueueLLM([{"facts": [row], "reviewed_passage_ids": [p.passage_id]}] * 2)
            completion = QueueLLM([{"facts": []}] * 2)
            extractor = fast.GlobalFastExtractor(initial, self.config, Path(directory),
                completeness_auditor=audit.CompletenessAuditor(completion, self.config, Path(directory)))
            with patch.object(fast, "GLOBAL_SYSTEM", old_fast), patch.object(audit, "AUDIT_SYSTEM", old_audit):
                _, old_meta = extractor.extract([doc])
            expected, changed = extractor.extract([doc])
            cached, hit = extractor.extract([doc])
            self.assertNotEqual(old_meta["fingerprint"], changed["fingerprint"])
            self.assertNotEqual(old_meta["completeness_audit"]["fingerprint"],
                                changed["completeness_audit"]["fingerprint"])
            self.assertEqual(changed["llm_calls"], 2)
            self.assertEqual(hit["llm_calls"], 0)
            self.assertEqual(cached, expected)
            self.assertEqual(len(initial.calls), 2)
            self.assertEqual(len(completion.calls), 2)

    def test_real_ai_code_passages_allow_additional_lock_fixture_without_losing_initial_facts(self):
        year = Path(r"C:\EnnoSmartData\object_storage_v2\runtime\organismes\test\projects\test3\years\2024")
        corpus = year / "documents/processed/fulltext_rag_v1"
        cache = year / "analysis_cache/global_fast/9402829c918837b808f726aa5027a45ed6d6b26be67894fdd23506532aef8325.json"
        if not corpus.is_dir() or not cache.is_file():
            self.skipTest("Recorded run 25 fulltext/cache unavailable")
        docs = []
        for path in sorted(corpus.glob("*.json")):
            if path.name == "manifest.json":
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            docs.append(DocumentInput(str(data["document_id"]),
                str(data.get("document") or data.get("file_name") or path.stem), data["text"].strip()))
        passages = fast.build_passages(docs)
        registry = {p.passage_id: p for p in passages}
        self.assertEqual(len(docs), 7)
        self.assertEqual(registry["p14"].document_id, "5")
        self.assertIn("Verrous scientifiques ou techniques", registry["p14"].text)
        self.assertIn("reproductible", registry["p15"].text)
        self.assertIn("comparer différents LLM", registry["p16"].text)
        rows = json.loads(cache.read_text(encoding="utf-8"))["facts"]
        self.assertEqual(len(rows), 69)
        candidate = fact("lock",
            "Difficulté à concevoir des critères fiables et reproductibles pour évaluer la qualité "
            "des tests unitaires générés et comparer les LLM", registry["p14"],
            passage_ids=["p14", "p15", "p16"], document_ids=["5"], temporal_status="planned")
        expected = fast.GlobalFastExtractor._items(rows, registry)
        # These are already validated cache rows, including an uncertain fact.
        # Replaying them as raw GPT output would exercise a pre-existing retry.
        # Replay the actual cache -> audit boundary without fabricating that
        # retry response or changing any recorded needs_review flag.
        llm = QueueLLM([{"facts": [candidate]}])
        unused_fast_llm = QueueLLM([])
        with tempfile.TemporaryDirectory() as directory:
            extractor = fast.GlobalFastExtractor(unused_fast_llm, self.config, Path(directory),
                completeness_auditor=audit.CompletenessAuditor(llm, self.config))
            extractor._write_cache(extractor._fingerprint(docs), rows)
            items, meta = extractor.extract(docs)
        self.assertEqual(unused_fast_llm.calls, [])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(meta["llm_calls"], 1)
        self.assertTrue(meta["cache_hit"])
        self.assertEqual([asdict(i) for i in items[:len(expected)]], [asdict(i) for i in expected])
        self.assertEqual([i.evidence.passage_id for i in items[len(expected):]], ["p14", "p15", "p16"])
        self.assertTrue(all(i.kind == "lock" and i.metadata["project_admission"]
                            for i in items[len(expected):]))
        for item in items[len(expected):]:
            self.assertEqual(item.evidence.quote, registry[item.evidence.passage_id].text)
            self.assertTrue(item.evidence.quote_verified)
        self.assertEqual(meta["mode"], "global")
        self.assertEqual(meta["completeness_audit"]["mode"], "global")
        self.assertEqual(len(audit.CompletenessAuditor(None, self.config)._batches(passages, rows)), 1)
        self.assertIn(rows[7]["statement"], llm.calls[0][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
