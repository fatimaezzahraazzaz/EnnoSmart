"""Régressions hors réseau de la phase 1 du reviewer global."""

import re
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalReviewerConfig, PipelineConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.full_runtime import EnnoDiagnosticV2FullRuntime
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_project_reviewer import (
    GlobalProjectReviewer, reviewed_fact_view,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.pipeline import EnnoDiagnosticV2
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.project_synthesis import ProjectSynthesizer
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import (
    Chunk, DocumentInput, EvidenceRef, ExtractionItem, PipelineResult,
)
from modules.LLM.llm_client import LLMClient
from backend_api.services.ennodiagnostic_v2_service import load_v2_document_inputs


def item(number, kind, role, statement, document, *, verified=True):
    return ExtractionItem(
        item_id=f"it_{number}", kind=kind, statement=statement,
        evidence=EvidenceRef(
            evidence_id=f"ev_{number}", document_id=document,
            document_name=f"{document}.docx", chunk_id=f"ch_{number}",
            quote=statement, section_title="Travaux" if document == "DE" else "État de l'art",
            quote_verified=verified,
        ),
        metadata={"source_role": role},
    )


class ReviewerLLM:
    def __init__(self, omit=None):
        self.omit = omit
        self.calls = 0
        self.requests = []

    def extract_json(self, system_prompt, user_prompt):
        self.calls += 1
        if "Organise les faits candidats" in system_prompt:
            self.requests.append(("theme", [], user_prompt))
            return {"themes": []}  # teste le regroupement déterministe de secours
        match = re.search(
            r"^(?:ITEM_ID CIBLES \(une décision par identifiant\)|"
            r"DECISIONS FINALES DEMANDEES UNIQUEMENT POUR) : (.+)$",
            user_prompt, re.M,
        )
        ids = [value.strip() for value in match.group(1).split(",")] if match else []
        self.requests.append(("final" if "PASSE FINALE OBLIGATOIRE" in system_prompt else "initial", ids, user_prompt))
        decisions = []
        for item_id in ids:
            if item_id == self.omit:
                continue
            number = int(item_id.split("_")[1])
            role = "literature" if number in {1, 4, 5} else "project_evidence"
            kind = {1: "method", 2: "demarche", 3: "metric", 4: "result", 5: "lock", 6: "metric", 7: "method"}[number]
            uncertain = number == 7
            decisions.append({
                "item_id": item_id,
                "original_item_ids": [item_id], "evidence_ids": [f"ev_{number}"],
                "source_role": role, "kind": kind,
                "project_admission": role == "project_evidence" and not uncertain,
                "uncertain": uncertain,
                "duplicate_of": "it_3" if number == 6 else None,
                "related_project_evidence_ids": ["ev_2"] if number == 5 else [],
                "related_lock_ids": [], "reason": "Vérification du dossier complet",
            })
        return {"decisions": decisions}


class ScenarioLLM:
    def __init__(self, rules, *, initial=None, final=None, omit=None,
                 bad_evidence_final=None, bad_original_final=None,
                 fail_final=False, semantic_themes=None, targeted=None):
        self.rules = rules
        self.initial = initial or {}
        self.final = final or {}
        self.omit = omit
        self.bad_evidence_final = bad_evidence_final
        self.bad_original_final = bad_original_final
        self.fail_final = fail_final
        self.semantic_themes = semantic_themes
        self.targeted = targeted or {}
        self.requests = []

    def extract_json(self, system_prompt, user_prompt):
        if "Organise les faits candidats" in system_prompt:
            self.requests.append(("theme", [], user_prompt))
            if self.semantic_themes is None:
                return {"themes": []}
            return {"themes": [
                {"theme_id": f"T{index}", "item_ids": ids}
                for index, ids in enumerate(self.semantic_themes, 1)
            ]}
        final_stage = "PASSE FINALE OBLIGATOIRE" in system_prompt
        targeted_stage = "REPRISE CIBLEE" in user_prompt
        if final_stage and self.fail_final:
            raise RuntimeError("reconciliation_unavailable")
        match = re.search(
            r"^(?:ITEM_ID CIBLES \(une décision par identifiant\)|"
            r"DECISIONS FINALES DEMANDEES UNIQUEMENT POUR) : (.+)$",
            user_prompt, re.M,
        )
        ids = [value.strip() for value in match.group(1).split(",")] if match else []
        self.requests.append(("final" if final_stage else "initial", ids, user_prompt))
        rows = []
        for item_id in ids:
            if item_id == self.omit:
                continue
            number = item_id.split("_")[1]
            settings = dict(self.rules[item_id])
            settings.update((self.final if final_stage else self.initial).get(item_id, {}))
            if targeted_stage:
                settings.update(self.targeted.get(item_id, {}))
            role = settings.get("source_role", "project_evidence")
            row = {
                "item_id": item_id, "original_item_ids": [item_id],
                "evidence_ids": [f"ev_{number}"],
                "source_role": role, "kind": settings.get("kind", "method"),
                "project_admission": settings.get("project_admission", role == "project_evidence"),
                "uncertain": settings.get("uncertain", False),
                "duplicate_of": settings.get("duplicate_of"),
                "related_project_evidence_ids": [], "related_lock_ids": [],
                "reason": "Décision vérifiée sur les passages exacts",
            }
            if final_stage and item_id == self.bad_evidence_final:
                row["evidence_ids"] = ["ev_invented"]
            if final_stage and item_id == self.bad_original_final:
                row["original_item_ids"] = ["it_invented"]
            rows.append(row)
        return {"decisions": rows}


class GlobalReviewerRegression(unittest.TestCase):
    def setUp(self):
        self.items = [
            item(1, "method", "project_evidence", "A3Test est étudié dans un article", "ARTICLE"),
            item(2, "demarche", "project_evidence", "Comparer plusieurs contextes de génération", "DE"),
            item(3, "parameter", "project_evidence", "Line coverage mesurée", "DE"),
            item(4, "result", "project_evidence", "A3Test atteint 70 % dans une publication", "ARTICLE"),
            item(5, "lock", "literature", "Limite de la méthode publiée", "ARTICLE"),
            item(6, "metric", "project_evidence", "Couverture des lignes mesurée", "DE"),
            item(7, "method", "project_evidence", "Méthode d'attribution incertaine", "DE"),
        ]
        self.chunks = [
            Chunk(chunk_id="ch_article", document_id="ARTICLE", document_name="ARTICLE.docx",
                  text="Article externe", section_title="État de l'art"),
            Chunk(chunk_id="ch_de", document_id="DE", document_name="DE.docx",
                  text="Travaux projet", section_title="Travaux"),
        ]
        self.config = GlobalReviewerConfig(max_prompt_chars=20000, batch_size=3)

    def test_complete_review_preserves_sources_and_reclassifies_only_view(self):
        llm = ReviewerLLM()
        review = GlobalProjectReviewer(llm, self.config).review(
            self.chunks, self.items, []
        )
        self.assertEqual(review["status"], "complete")
        self.assertEqual(review["mode"], "global")
        self.assertEqual(llm.calls, 1)
        self.assertEqual(review["coverage"], 1.0)
        self.assertEqual(review["reviewed_count"], len(self.items))
        self.assertEqual(review["lock_observations"][0]["item_id"], "it_5")
        self.assertEqual(review["decisions"][0]["quote"], self.items[0].evidence.quote)
        uncertain = next(row for row in review["decisions"] if row["item_id"] == "it_7")
        self.assertTrue(uncertain["needs_review"])
        self.assertFalse(uncertain["project_admission"])
        view_items, view_entities = reviewed_fact_view(self.items, [], review)
        synthesis = ProjectSynthesizer(None).synthesize(
            view_items, view_entities, globally_reviewed=True
        )
        self.assertEqual(self.items[2].kind, "parameter")  # fait brut intact
        self.assertEqual([point.text for point in synthesis.demarches], [self.items[1].statement])
        self.assertEqual(len(synthesis.metrics), 1)
        self.assertEqual(set(synthesis.metrics[0].evidence_ids), {"ev_3", "ev_6"})
        self.assertEqual(set(synthesis.metrics[0].original_item_ids), {"it_3", "it_6"})
        self.assertEqual(synthesis.methods, [])
        self.assertEqual(synthesis.results, [])
        self.assertEqual(synthesis.parameters, [])

    def test_incomplete_review_keeps_coverage_with_needs_review(self):
        review = GlobalProjectReviewer(ReviewerLLM(omit="it_2"), self.config).review(
            self.chunks, self.items, []
        )
        self.assertEqual(review["status"], "complete")
        self.assertEqual(review["coverage"], 1.0)
        missing = next(row for row in review["decisions"] if row["item_id"] == "it_2")
        self.assertTrue(missing["needs_review"])
        self.assertFalse(missing["project_admission"])
        self.assertEqual(missing["evidence_ids"], ["ev_2"])
        view_items, _ = reviewed_fact_view(self.items, [], review)
        self.assertNotIn("it_2", {fact.item_id for fact in view_items})

    def test_dedicated_client_refuses_silent_truncation(self):
        client = LLMClient(model="gpt-5", provider="openai")
        with self.assertRaises(ValueError):
            client._prepare_prompt(
                "x" * 1500, "openai", "ennodiagnostic:v2:global_project_review",
                None, 1000, prompt_char_limit=1000, fail_on_truncation=True,
            )

    def test_reviewer_runs_before_unchanged_qualifier_inputs(self):
        events = []

        class Observer:
            def review(self, chunks, extractions, entities):
                events.append("review")
                self.seen_items = extractions
                return {"status": "complete", "coverage": 1.0, "decisions": []}

        observer = Observer()
        pipeline = EnnoDiagnosticV2(
            semantic_llm=ReviewerLLM(), global_reviewer=observer,
            config=PipelineConfig(run_frascati=False),
        )
        pipeline.extractor.extract = lambda chunks: self.items
        pipeline.consolidator.consolidate = lambda extractions: []

        def qualify(entities, chunks, extractions):
            events.append("qualify")
            self.assertIs(extractions, self.items)
            self.assertEqual(extractions[2].kind, "parameter")
            return entities

        pipeline.qualifier.qualify = qualify
        pipeline.frascati.assess = lambda entities: []
        result = pipeline.run([DocumentInput("DE", "DE.docx", "Travaux\nTexte technique documenté.")])
        self.assertEqual(events, ["review", "qualify"])
        self.assertIs(observer.seen_items, self.items)
        self.assertEqual(result.global_review["status"], "complete")

    def test_full_runtime_uses_legacy_synthesis_on_review_fallback(self):
        llm = ReviewerLLM()
        runtime = EnnoDiagnosticV2FullRuntime(
            semantic_llm=llm, consolidation_llm=llm, qualification_llm=llm,
            family_llm=llm, synthesis_llm=llm, rnd_llm=llm, frascati_llm=llm,
            global_reviewer_llm=llm,
        )
        core = PipelineResult(
            version="test", documents=[], chunks=self.chunks,
            extractions=self.items, entities=[], frascati=[], review_queue=[],
            audit={}, global_review={"status": "fallback", "coverage": 0.0},
        )
        runtime.core.run = lambda documents: core
        runtime.families.synthesize = lambda entities: None
        runtime.rnd.assess = lambda extractions, chunks: None
        runtime.frascati.assess = lambda *args, **kwargs: None
        runtime.scorer.score = lambda **kwargs: None
        calls = []

        def synthesize(extractions, entities, *, globally_reviewed=False):
            calls.append((extractions, entities, globally_reviewed))
            return None

        runtime.synthesis.synthesize = synthesize
        runtime.run([])
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], self.items)
        self.assertIs(calls[0][1], core.entities)
        self.assertFalse(calls[0][2])

    def test_full_runtime_activates_reviewed_synthesis_without_changing_score_inputs(self):
        llm = ReviewerLLM()
        runtime = EnnoDiagnosticV2FullRuntime(
            semantic_llm=llm, consolidation_llm=llm, qualification_llm=llm,
            family_llm=llm, synthesis_llm=llm, rnd_llm=llm, frascati_llm=llm,
            global_reviewer_llm=llm,
        )
        review = GlobalProjectReviewer(llm, self.config).review(self.chunks, self.items, [])
        core = PipelineResult(
            version="test", documents=[], chunks=self.chunks,
            extractions=self.items, entities=[], frascati=[], review_queue=[],
            audit={}, global_review=review,
        )
        runtime.core.run = lambda documents: core
        runtime.families.synthesize = lambda entities: None
        runtime.rnd.assess = lambda extractions, chunks: None
        runtime.frascati.assess = lambda *args, **kwargs: None
        calls = []
        def synthesize(extractions, entities, *, globally_reviewed=False):
            calls.append((extractions, entities, globally_reviewed))
            return {"globally_reviewed": globally_reviewed}

        runtime.synthesis.synthesize = synthesize
        score_inputs = []
        runtime.scorer.score = lambda **kwargs: score_inputs.append(kwargs) or None
        result = runtime.run([])
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0][0], self.items)
        self.assertFalse(calls[0][2])
        self.assertTrue(calls[1][2])
        self.assertIsNot(calls[1][0], self.items)
        self.assertIs(score_inputs[0]["extractions"], self.items)
        self.assertIs(score_inputs[0]["entities"], core.entities)
        self.assertEqual(score_inputs[0]["synthesis"], {"globally_reviewed": False})
        self.assertEqual(result.project_synthesis, {"globally_reviewed": True})

    def test_final_cir_is_not_loaded_as_raw_diagnostic_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "fulltext_rag_v1"
            corpus.mkdir()
            (corpus / "de.json").write_text(json.dumps({
                "document_id": "DE", "document": "de.docx", "text": "Travaux bruts",
                "content_origin": "project_core",
            }), encoding="utf-8")
            (corpus / "cir.json").write_text(json.dumps({
                "document_id": "CIR", "document": "cir_final.docx", "text": "Vérité humaine",
                "content_origin": "cir_final_validated",
            }), encoding="utf-8")
            documents = load_v2_document_inputs(
                SimpleNamespace(documents_processed_dir=temporary)
            )
            self.assertEqual([document.document_id for document in documents], ["DE"])

    def test_large_dossier_uses_multiple_lots_and_final_reconciliation(self):
        llm = ReviewerLLM()
        config = GlobalReviewerConfig(max_prompt_chars=20000, max_output_tokens=1400)
        review = GlobalProjectReviewer(llm, config).review(self.chunks, self.items, [])
        self.assertEqual(review["status"], "complete")
        self.assertEqual(review["mode"], "multi_lot")
        self.assertGreater(review["batch_count"], 1)
        self.assertGreater(review["reconciliation_calls"], 0)
        self.assertEqual({row["item_id"] for row in review["decisions"]},
                         {item.item_id for item in self.items})
        self.assertTrue(any(stage == "final" for stage, _, _ in llm.requests))
        self.assertTrue(all("[CONTEXTE REDUIT AUTOMATIQUEMENT]" not in prompt
                            for _, _, prompt in llm.requests))

    def test_oversized_global_index_does_not_force_fallback(self):
        facts = [
            item(100 + index, "method", "project_evidence",
                 f"Protocole spécifique numéro {index} appliqué au sous-ensemble {index}",
                 "DE" if index % 2 else "ANNEXE")
            for index in range(30)
        ]
        llm = ScenarioLLM({fact.item_id: {} for fact in facts})
        config = GlobalReviewerConfig(max_prompt_chars=6500, max_output_tokens=1400)
        review = GlobalProjectReviewer(llm, config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete", review["error"])
        self.assertEqual(review["mode"], "multi_lot")
        self.assertEqual(review["reviewed_count"], len(facts))
        self.assertGreater(review["reconciliation_calls"], 0)

    def test_related_facts_from_different_documents_share_thematic_lot(self):
        facts = [
            item(8, "method", "literature",
                 "Tests générés avec erreurs de compilation causées par les imports absents", "ARTICLE"),
            item(9, "method", "project_evidence",
                 "Les imports absents provoquent des erreurs de compilation des tests générés", "DE"),
            item(10, "result", "project_evidence", "Le temps de traitement atteint trente secondes", "DE"),
        ]
        llm = ScenarioLLM({
            "it_8": {"kind": "method", "source_role": "literature"},
            "it_9": {"kind": "method", "source_role": "project_evidence"},
            "it_10": {"kind": "result", "source_role": "project_evidence"},
        })
        config = GlobalReviewerConfig(max_prompt_chars=20000, max_output_tokens=1400)
        review = GlobalProjectReviewer(llm, config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete")
        initial_lots = [set(ids) for stage, ids, _ in llm.requests if stage == "initial"]
        self.assertIn({"it_8", "it_9"}, initial_lots)

    def test_model_theme_plan_links_paraphrases_across_documents(self):
        facts = [
            item(23, "method", "literature", "Context enrichment for Java unit generation", "ARTICLE"),
            item(24, "method", "project_evidence", "Ajout de la classe complète aux essais de tests", "DE"),
            item(25, "metric", "project_evidence", "Durée moyenne mesurée en secondes", "DE"),
        ]
        llm = ScenarioLLM(
            {"it_23": {"source_role": "literature"}, "it_24": {},
             "it_25": {"kind": "metric"}},
            semantic_themes=[["it_23", "it_24"], ["it_25"]],
        )
        config = GlobalReviewerConfig(max_prompt_chars=20000, max_output_tokens=1400)
        review = GlobalProjectReviewer(llm, config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete")
        self.assertEqual(review["theme_plan_source"], "model")
        initial_lots = [set(ids) for stage, ids, _ in llm.requests if stage == "initial"]
        self.assertIn({"it_23", "it_24"}, initial_lots)
        llm_split = ScenarioLLM(
            {"it_23": {"source_role": "literature"}, "it_24": {},
             "it_25": {"kind": "metric"}},
            semantic_themes=[["it_23", "it_24"], ["it_25"]],
        )
        split_config = GlobalReviewerConfig(max_prompt_chars=20000, max_output_tokens=1150)
        split_review = GlobalProjectReviewer(llm_split, split_config).review(self.chunks, facts, [])
        self.assertEqual(split_review["status"], "complete")
        final_prompts = [prompt for stage, _, prompt in llm_split.requests if stage == "final"]
        self.assertTrue(any(facts[0].evidence.quote in prompt and facts[1].evidence.quote in prompt
                            for prompt in final_prompts))

    def test_interlot_duplicate_is_reconciled_with_both_exact_quotes(self):
        facts = [
            item(11, "method", "project_evidence", "Protocole de génération de tests par contexte Java", "DE"),
            item(12, "method", "project_evidence", "Génération des tests Java selon le contexte du projet", "ANNEXE"),
        ]
        llm = ScenarioLLM(
            {"it_11": {"kind": "method"}, "it_12": {"kind": "method"}},
            final={"it_12": {"duplicate_of": "it_11"}},
        )
        config = GlobalReviewerConfig(max_prompt_chars=20000, max_output_tokens=1150)
        review = GlobalProjectReviewer(llm, config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete")
        self.assertEqual(review["mode"], "multi_lot")
        self.assertEqual(review["decisions"][1]["duplicate_of"], "it_11")
        final_prompts = [prompt for stage, _, prompt in llm.requests if stage == "final"]
        self.assertTrue(any(facts[0].evidence.quote in prompt and facts[1].evidence.quote in prompt
                            for prompt in final_prompts))
        view_items, view_entities = reviewed_fact_view(facts, [], review)
        synthesis = ProjectSynthesizer(None).synthesize(view_items, view_entities, globally_reviewed=True)
        self.assertEqual(len(synthesis.methods), 1)
        self.assertEqual(set(synthesis.methods[0].evidence_ids), {"ev_11", "ev_12"})

    def test_conflicting_provenance_gets_one_final_decision_per_fact(self):
        facts = [
            item(13, "method", "project_evidence", "Protocole publié de réparation des tests", "ARTICLE"),
            item(14, "method", "literature", "Réparation des tests par le protocole publié", "DE"),
        ]
        llm = ScenarioLLM(
            {"it_13": {"kind": "method"}, "it_14": {"kind": "method"}},
            initial={"it_14": {"source_role": "literature"}},
            final={
                "it_13": {"source_role": "literature"},
                "it_14": {"source_role": "literature"},
            },
        )
        config = GlobalReviewerConfig(max_prompt_chars=20000, max_output_tokens=1150)
        review = GlobalProjectReviewer(llm, config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete")
        self.assertEqual([row["source_role"] for row in review["decisions"]],
                         ["literature", "literature"])
        self.assertTrue(all(not row["project_admission"] for row in review["decisions"]))
        self.assertEqual(len({row["item_id"] for row in review["decisions"]}), 2)

    def test_conflicting_category_is_resolved_in_final_decisions(self):
        facts = [
            item(15, "method", "project_evidence", "Architecture de génération des tests Java", "DE"),
            item(16, "demarche", "project_evidence", "Génération des tests Java par cette architecture", "ANNEXE"),
        ]
        llm = ScenarioLLM(
            {"it_15": {"kind": "method"}, "it_16": {"kind": "demarche"}},
            final={"it_16": {"kind": "method"}},
        )
        config = GlobalReviewerConfig(max_prompt_chars=20000, max_output_tokens=1150)
        review = GlobalProjectReviewer(llm, config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete")
        self.assertEqual([row["reviewed_kind"] for row in review["decisions"]],
                         ["method", "method"])

    def test_missing_item_id_becomes_needs_review_without_loss(self):
        facts = [item(17, "method", "project_evidence", "Comparer deux architectures", "DE"),
                 item(18, "method", "project_evidence", "Valider les architectures", "ANNEXE")]
        llm = ScenarioLLM({"it_17": {}, "it_18": {}}, omit="it_18")
        review = GlobalProjectReviewer(llm, self.config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete")
        self.assertEqual(review["coverage"], 1.0)
        self.assertEqual({row["item_id"] for row in review["decisions"]}, {"it_17", "it_18"})
        self.assertTrue(review["decisions"][1]["needs_review"])
        self.assertFalse(review["decisions"][1]["project_admission"])

    def test_invalid_evidence_id_is_not_accepted_after_retry(self):
        facts = [item(19, "method", "project_evidence", "Comparer deux architectures", "DE"),
                 item(20, "method", "project_evidence", "Valider les architectures", "ANNEXE")]
        llm = ScenarioLLM({"it_19": {}, "it_20": {}}, bad_evidence_final="it_20")
        config = GlobalReviewerConfig(max_prompt_chars=20000, max_output_tokens=1150)
        review = GlobalProjectReviewer(llm, config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete")
        self.assertEqual(review["coverage"], 1.0)
        bad = review["decisions"][1]
        self.assertEqual(bad["evidence_ids"], ["ev_20"])
        self.assertTrue(bad["needs_review"])
        self.assertFalse(bad["project_admission"])
        self.assertIn("invalid_evidence_ids", bad["reason"])

    def test_invalid_original_item_id_is_not_accepted_after_retry(self):
        facts = [item(26, "method", "project_evidence", "Comparer deux architectures", "DE"),
                 item(27, "method", "project_evidence", "Valider les architectures", "ANNEXE")]
        llm = ScenarioLLM({"it_26": {}, "it_27": {}}, bad_original_final="it_27")
        config = GlobalReviewerConfig(max_prompt_chars=20000, max_output_tokens=1150)
        review = GlobalProjectReviewer(llm, config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete")
        bad = review["decisions"][1]
        self.assertEqual(bad["original_item_ids"], ["it_27"])
        self.assertTrue(bad["needs_review"])
        self.assertFalse(bad["project_admission"])
        self.assertIn("invalid_original_item_ids", bad["reason"])

    def test_one_invalid_item_gets_targeted_retry_without_fallback(self):
        facts = [
            item(28, "method", "project_evidence", "Méthode A du projet", "DE"),
            item(29, "method", "project_evidence", "Méthode B du projet", "DE"),
        ]
        llm = ScenarioLLM(
            {"it_28": {}, "it_29": {}},
            initial={"it_29": {"kind": "outside_enum"}},
            targeted={"it_29": {"kind": "method"}},
        )
        review = GlobalProjectReviewer(llm, self.config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete", review["error"])
        self.assertEqual(review["coverage"], 1.0)
        self.assertEqual(review["reviewed_count"], 2)
        retries = [entry for entry in review["audit"]["responses"]
                   if entry["stage"] == "targeted_retry"]
        self.assertEqual([entry["target_item_ids"] for entry in retries], [["it_29"]])
        self.assertIn(facts[1].evidence.quote, llm.requests[-1][2])
        self.assertTrue(review["decisions"][1]["project_admission"])

    def test_failed_retry_keeps_unverified_quote_out_of_project(self):
        facts = [
            item(30, "method", "project_evidence", "Méthode vérifiée", "DE"),
            item(31, "method", "project_evidence", "Citation incertaine", "DE", verified=False),
        ]
        llm = ScenarioLLM({"it_30": {}, "it_31": {}})
        review = GlobalProjectReviewer(llm, self.config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete", review["error"])
        self.assertEqual(review["coverage"], 1.0)
        self.assertEqual(len(review["decisions"]), 2)
        bad = review["decisions"][1]
        self.assertEqual(bad["evidence_ids"], ["ev_31"])
        self.assertEqual(bad["quote"], "Citation incertaine")
        self.assertTrue(bad["needs_review"])
        self.assertFalse(bad["project_admission"])
        self.assertIn("unsupported_project_admission", bad["reason"])
        view_items, _ = reviewed_fact_view(facts, [], review)
        self.assertEqual([fact.item_id for fact in view_items], ["it_30"])

    def test_failed_retry_is_not_overridden_by_reconciliation(self):
        facts = [
            item(32, "method", "project_evidence", "Méthode vérifiée A", "DE"),
            item(33, "method", "project_evidence", "Méthode incertaine B", "ANNEXE"),
        ]
        llm = ScenarioLLM(
            {"it_32": {}, "it_33": {}},
            initial={"it_33": {"kind": "outside_enum"}},
        )
        config = GlobalReviewerConfig(max_prompt_chars=20000, max_output_tokens=1150)
        review = GlobalProjectReviewer(llm, config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "complete", review["error"])
        self.assertEqual(review["mode"], "multi_lot")
        bad = next(row for row in review["decisions"] if row["item_id"] == "it_33")
        self.assertTrue(bad["needs_review"])
        self.assertFalse(bad["project_admission"])
        self.assertEqual(bad["review_recovery"], "targeted_retry_failed")
        self.assertEqual(review["coverage"], 1.0)

    def test_reconciliation_failure_returns_complete_legacy_fallback(self):
        facts = [item(21, "method", "project_evidence", "Comparer deux architectures", "DE"),
                 item(22, "method", "project_evidence", "Valider les architectures", "ANNEXE")]
        llm = ScenarioLLM({"it_21": {}, "it_22": {}}, fail_final=True)
        config = GlobalReviewerConfig(max_prompt_chars=20000, max_output_tokens=1150)
        review = GlobalProjectReviewer(llm, config).review(self.chunks, facts, [])
        self.assertEqual(review["status"], "fallback")
        self.assertEqual(review["coverage"], 0.0)
        self.assertEqual(review["decisions"], [])
        self.assertGreater(len(review["audit"]["responses"]), 0)
        self.assertTrue(any(entry["response"] for entry in review["audit"]["responses"]))


if __name__ == "__main__":
    unittest.main()
