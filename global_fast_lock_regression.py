"""Offline lock contract, multi-label, consolidation and retry regressions.

The queued responses exercise Python integration, not live model quality.
Prompt assertions protect the instructions used by the same global call.
"""
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2 import global_fast_path as fast
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalFastPathConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.consolidation import (
    CONSOLIDATION_SYSTEM, GlobalLockConsolidator,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
from global_fast_path_regression import QueueLLM, fact


BASE_GLOBAL_SYSTEM = """
Analyse ENSEMBLE tous les documents bruts du dossier R&D. Ils ne sont pas un CIR final.
Chaque ligne P du registre est un passage exact identifié par passage_id, document_id
et positions. Réponds uniquement en JSON compact :
{"reviewed_passage_ids":["p1",...],"facts":[{"kind":"objective|lock|method|demarche|parameter|metric|result",
"statement":"fait précis, sans invention","source_role":"project_evidence|literature|context|admin",
"temporal_status":"planned|in_progress|performed|unclear",
"passage_ids":["p1"],"document_ids":["document_id"],"needs_review":false}]}.
Référence les passage_id ; ne recopie pas leurs textes. Un fait doit avoir une preuve
directe et son énoncé doit être compatible avec cette preuve. Ne retourne pas de fait
sans preuve. Les documents peuvent mélanger littérature et travaux du projet.
Une méthode publiée n'est pas une méthode utilisée par le projet ; un résultat
publié n'est pas un résultat expérimental du projet ; une limite de l'état de
l'art n'est pas un verrou effectivement rencontré. Une démarche est une étape
des travaux ; une méthode est un dispositif/protocole technique ; un paramètre
est une variable choisie ou modifiée ; une métrique est ce qu'on mesure ; un
résultat est la valeur ou l'observation effectivement obtenue. Les étapes
explicites d'une liste de travaux doivent être individualisées sans quota.
En cas d'attribution douteuse, needs_review=true et ne revendique pas une preuve
projet certaine. Conserve les faits de littérature pour audit. Réduis les
doublons uniquement lorsqu'il s'agit du même fait ; garde les résultats distincts.
Ne crée aucun passage_id, document_id, mesure, essai ou lien absent des sources.
reviewed_passage_ids doit contenir TOUS les passage_id lus, même ceux sans fait.
""".strip()


class FastLockTests(unittest.TestCase):
    def setUp(self):
        self.docs = [
            DocumentInput("MESURE", "Mesures.docx", "Travaux du projet\n"
                          "Nous mesurons 24 % de compilabilité et 6 % de couverture."),
            DocumentInput("ESSAIS", "Essais.docx", "Travaux du projet\n"
                          "Les échecs de compilation persistent lors des essais répétés. "
                          "Nous comparons plusieurs stratégies de génération."),
            DocumentInput("CORRECTION", "Correction.docx", "Travaux du projet\n"
                          "Nous réinjectons les erreurs de compilation pour correction. "
                          "La correction reste insuffisante et la performance progresse peu."),
        ]
        self.passages = fast.build_passages(self.docs)

    def extract(self, rows, docs=None, responses=None, cache=None):
        docs = docs or self.docs
        passages = fast.build_passages(docs)
        payload = {"reviewed_passage_ids": [p.passage_id for p in passages], "facts": rows}
        llm = QueueLLM([payload, *(responses or [])])
        extractor = fast.GlobalFastExtractor(llm, GlobalFastPathConfig(enabled=True), cache)
        items, meta = extractor.extract(docs)
        return items, meta, llm

    def lock(self):
        return fact("lock", "Échecs persistants de compilation malgré une correction insuffisante.",
                    self.passages[0], passage_ids=[p.passage_id for p in self.passages],
                    document_ids=[p.document_id for p in self.passages])

    def test_existing_category_and_provenance_contract_is_unchanged(self):
        self.assertTrue(fast.GLOBAL_SYSTEM.startswith(BASE_GLOBAL_SYSTEM))
        self.assertIn("provenance et la temporalité sont INDEPENDANTES", fast.GLOBAL_SYSTEM)

    def test_implicit_lock_contract_requires_correlated_observations_not_cir_approval(self):
        for instruction in ("candidat de difficulté, limite ou incertitude technique",
                            "Ne qualifie jamais toi-même un candidat", "en VALID_LOCK",
                            "explicitement nommés", "plusieurs faits concordants",
                            "exige des preuves projet concordantes", "non maîtrisé",
                            "un mauvais résultat isolé", "À la fin du MÊME appel",
                            "OBJECTIVE, RESULT, METHOD, DEMARCHE, PARAMETER et METRIC"):
            self.assertIn(instruction, fast.GLOBAL_SYSTEM)

    def test_weak_result_alone_does_not_automatically_create_lock(self):
        items, meta, llm = self.extract([
            fact("result", "24 % de compilabilité et 6 % de couverture", self.passages[0])])
        self.assertEqual([x.kind for x in items], ["result"])
        self.assertEqual(meta["llm_calls"], 1)
        self.assertEqual(len(llm.calls), 1)

    def test_repeated_failure_and_incomplete_correction_candidate_keeps_exact_evidence(self):
        items, meta, _ = self.extract([self.lock()])
        self.assertEqual(len(items), 3)
        self.assertTrue(all(x.kind == "lock" for x in items))
        self.assertEqual({x.evidence.document_id for x in items}, {d.document_id for d in self.docs})
        self.assertTrue(all(x.evidence.quote_verified for x in items))
        self.assertTrue(all("qualification_status" not in x.metadata for x in items))
        for item in items:
            doc = next(d for d in self.docs if d.document_id == item.evidence.document_id)
            self.assertEqual(doc.text[item.evidence.char_start:item.evidence.char_end], item.evidence.quote)
        self.assertEqual(meta["llm_calls"], 1)

    def test_same_evidence_retains_result_and_additional_lock_in_one_call(self):
        result = fact("result", "24 % de compilabilité et 6 % de couverture", self.passages[0])
        rows = [result,
                fact("method", "Nous comparons plusieurs stratégies de génération", self.passages[1]),
                fact("demarche", "Nous réinjectons les erreurs de compilation pour correction", self.passages[2]),
                self.lock()]
        items, meta, llm = self.extract(rows)
        measured = next(x for x in items if x.kind == "result")
        candidate = next(x for x in items if x.kind == "lock" and x.evidence.passage_id == measured.evidence.passage_id)
        self.assertEqual(measured.statement, result["statement"])
        self.assertEqual(measured.evidence.quote, candidate.evidence.quote)
        self.assertIn("ajoute le LOCK EN PLUS", llm.calls[0][0])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(meta["llm_calls"], 1)
        self.assertEqual(meta["targeted_retries"], 0)

    def test_literature_lock_remains_auditable_without_project_promotion(self):
        docs = [DocumentInput("ARTICLE", "Article.docx", "État de l'art\n"
                              "L'article rapporte des échecs persistants de compilation.")]
        p = fast.build_passages(docs)[0]
        row = fact("lock", "Échecs persistants de compilation", p, "literature")
        items, _, _ = self.extract([row], docs)
        self.assertEqual(items[0].metadata["source_role"], "literature")
        self.assertFalse(items[0].metadata["project_admission"])
        with self.assertRaisesRegex(ValueError, "literature_evidence_as_project_fact"):
            fast.GlobalFastExtractor(None, GlobalFastPathConfig())._validate(
                {**row, "source_role": "project_evidence"}, {p.passage_id: p})

    def test_all_other_category_items_identical_with_previous_prompt_on_fixture(self):
        docs = [DocumentInput("D", "D.docx", "Travaux du projet\n"
                              "Nous visons une génération fiable. Nous utilisons une analyse statique. "
                              "Nous réalisons une campagne de validation. Nous choisissons trois modèles. "
                              "Nous mesurons la couverture ligne. Nous obtenons une couverture ligne de 6 %. "
                              "Les échecs de compilation persistent malgré la correction.")]
        p = fast.build_passages(docs)[0]
        rows = [fact(kind, statement, p) for kind, statement in (
            ("objective", "Nous visons une génération fiable"),
            ("method", "Nous utilisons une analyse statique"),
            ("demarche", "Nous réalisons une campagne de validation"),
            ("parameter", "Nous choisissons trois modèles"),
            ("metric", "Nous mesurons la couverture ligne"),
            ("result", "Nous obtenons une couverture ligne de 6 %"),
        )]
        with patch.object(fast, "GLOBAL_SYSTEM", BASE_GLOBAL_SYSTEM):
            before, before_meta, _ = self.extract(rows, docs)
        after, after_meta, _ = self.extract(
            [*rows, fact("lock", "Échecs de compilation persistants malgré la correction", p)], docs)
        self.assertEqual([asdict(x) for x in before], [asdict(x) for x in after if x.kind != "lock"])
        self.assertEqual(before_meta["llm_calls"], after_meta["llm_calls"])

    def test_new_lock_contract_invalidates_previous_prompt_cache(self):
        rows = [fact("result", "24 % de compilabilité et 6 % de couverture", self.passages[0])]
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with patch.object(fast, "GLOBAL_SYSTEM", BASE_GLOBAL_SYSTEM):
                _, old_meta, _ = self.extract(rows, cache=cache)
            _, new_meta, llm = self.extract([*rows, self.lock()], cache=cache)
            self.assertNotEqual(old_meta["fingerprint"], new_meta["fingerprint"])
            self.assertFalse(new_meta["cache_hit"])
            extractor = fast.GlobalFastExtractor(llm, GlobalFastPathConfig(enabled=True), cache)
            cached, hit = extractor.extract(self.docs)
            self.assertTrue(hit["cache_hit"])
            self.assertEqual(hit["llm_calls"], 0)
            self.assertEqual(len(llm.calls), 1)
            self.assertTrue(any(x.kind == "lock" for x in cached))

    def test_flat_and_wrapped_retry_use_same_validation_and_call_count(self):
        p = self.passages[0]
        original = fact("result", "24 % de compilabilité et 6 % de couverture", p)
        invalid = {**original, "document_ids": ["OUTSIDE"]}
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                repair = ({"candidate_id": "c0", "fact": original} if wrapped
                          else {"candidate_id": "c0", **original})
                items, meta, llm = self.extract([invalid], responses=[{"facts": [repair]}])
                self.assertEqual(items[0].statement, original["statement"])
                self.assertFalse(items[0].needs_review)
                self.assertTrue(items[0].metadata["project_admission"])
                self.assertEqual(meta["rejected_candidates"], [])
                self.assertEqual(meta["llm_calls"], 2)
                self.assertEqual(len(llm.calls), 2)

    def test_run17_performance_retry_shape_preserves_repaired_result(self):
        docs = [DocumentInput("D", "Entretien.docx", "Ça a pas trop monté la performance.")]
        p = fast.build_passages(docs)[0]
        bad = fact("result", "La réinjection des erreurs apporte peu d'amélioration mesurée", p)
        fixed = fact("result", "Ça a pas trop monté la performance", p)
        items, meta, _ = self.extract([bad], docs, [{"facts": [{"candidate_id": "c0", "fact": fixed}]}])
        self.assertEqual(items[0].statement, fixed["statement"])
        self.assertEqual(items[0].kind, "result")
        self.assertEqual(meta["rejected_candidates"], [])

    def test_wrapped_retry_cannot_bypass_enums_or_traceability(self):
        p = self.passages[0]
        original = fact("result", "24 % de compilabilité et 6 % de couverture", p)
        invalid = {**original, "document_ids": ["OUTSIDE"]}
        changes = [{"kind": "INVALID"}, {"source_role": "INVALID"},
                   {"temporal_status": "INVALID"}, {"needs_review": "false"},
                   {"passage_ids": ["p999"]}, {"document_ids": ["OUTSIDE"]},
                   {"statement": "Une découverte entièrement inventée"}]
        for change in changes:
            with self.subTest(change=change):
                repair = {"candidate_id": "c0", "fact": {**original, **change}}
                items, _, _ = self.extract([invalid], responses=[{"facts": [repair]}])
                self.assertTrue(items[0].needs_review)
                self.assertFalse(items[0].metadata["project_admission"])
                self.assertEqual(items[0].evidence.passage_id, p.passage_id)

    def test_wrapped_retry_cannot_switch_to_another_valid_passage(self):
        bad = fact("result", "24 % de compilabilité et 6 % de couverture", self.passages[0],
                   document_ids=["OUTSIDE"])
        other = fact("result", "Les échecs de compilation persistent lors des essais répétés", self.passages[1])
        items, _, _ = self.extract([bad], responses=[{"facts": [{"candidate_id": "c0", "fact": other}]}])
        self.assertTrue(items[0].needs_review)
        self.assertFalse(items[0].metadata["project_admission"])
        self.assertEqual(items[0].evidence.passage_id, self.passages[0].passage_id)

    def test_conflicting_retry_candidate_ids_are_not_accepted(self):
        original = fact("result", "24 % de compilabilité et 6 % de couverture", self.passages[0])
        repair = {"candidate_id": "c0", "fact": {"candidate_id": "c1", **original}}
        items, _, _ = self.extract([{**original, "document_ids": ["OUTSIDE"]}],
                                   responses=[{"facts": [repair]}])
        self.assertTrue(items[0].needs_review)
        self.assertFalse(items[0].metadata["project_admission"])


class LockRecallPromptContractTests(unittest.TestCase):
    def setUp(self):
        self.block = fast.GLOBAL_SYSTEM.split("\n\nLOCK seulement =", 1)[1]
        self.guard = self.block.split("Ne transforme pas automatiquement :", 1)[1].split("en verrou projet.", 1)[0]

    def test_objective_with_technical_evidence_can_also_support_lock(self):
        self.assertIn("Une information peut rester OBJECTIVE", self.block)
        self.assertIn("servir EN PLUS de preuve à un candidat LOCK distinct", self.block)
        self.assertIn("ainsi que leurs preuves projet", self.block)

    def test_metric_with_technical_evidence_can_also_support_lock(self):
        self.assertIn("ou METRIC et servir EN PLUS", self.block)
        self.assertIn("définir, mesurer ou valider de manière fiable la qualité", self.block)
        self.assertIn("elle-même une incertitude technique", self.block)

    def test_parameter_or_constraint_requires_real_technical_difficulty(self):
        self.assertIn("METHOD, DEMARCHE, PARAMETER", self.block)
        self.assertIn("contrainte opérationnelle qui impose une limitation technique réelle", self.block)
        self.assertIn("rechercher une solution non évidente", self.block)
        self.assertIn("preuves projet concordantes", self.block)

    def test_result_and_lock_remain_nonexclusive_without_reclassification(self):
        self.assertIn("les catégories ne sont pas exclusives", self.block)
        self.assertIn("rester OBJECTIVE, RESULT", self.block)
        self.assertIn("N'enlève et ne reclassifie aucun fait déjà extrait", self.block)
        self.assertIn("ajoute le LOCK EN PLUS", self.block)

    def test_simple_objective_is_not_automatically_lock(self):
        self.assertIn("un simple objectif", self.guard)
        self.assertIn("une simple exigence", self.guard)

    def test_simple_constraint_is_not_automatically_lock(self):
        self.assertIn("une simple contrainte métier", self.guard)
        self.assertIn("un mauvais résultat isolé", self.guard)

    def test_literature_alone_is_not_project_lock(self):
        self.assertIn("une limite uniquement issue de la littérature", self.guard)
        self.assertIn("Une limite de littérature reste littérature tant qu'aucune preuve projet", self.block)

    def test_distinct_difficulties_stay_distinct_without_project_specific_quota(self):
        self.assertIn("Garde séparées les difficultés techniquement différentes", self.block)
        self.assertIn("ajoute un fait LOCK distinct", self.block)
        for forbidden in ("trouve 3 verrous", "confidentialité", "critères d'évaluation",
                          "compilabilité", "AI-CODE"):
            self.assertNotIn(forbidden.casefold(), fast.GLOBAL_SYSTEM.casefold())

    def test_all_category_recheck_uses_same_global_call(self):
        self.assertIn("Reconsidère TOUS les faits", self.block)
        self.assertIn("OBJECTIVE, RESULT, METHOD, DEMARCHE, PARAMETER et METRIC", self.block)
        self.assertIn("Cette relecture se fait dans le MÊME appel", self.block)
        self.assertIn("Aucun nouvel appel LLM ne doit être ajouté", self.block)
        doc = DocumentInput("D", "D.docx", "Nous voulons mesurer la qualité des résultats techniques.")
        p = fast.build_passages([doc])[0]
        llm = QueueLLM([{"reviewed_passage_ids": [p.passage_id],
                         "facts": [fact("objective", "Mesurer la qualité des résultats techniques", p)]}])
        items, meta = fast.GlobalFastExtractor(llm, GlobalFastPathConfig()).extract([doc])
        self.assertEqual([i.kind for i in items], ["objective"])
        self.assertEqual(meta["llm_calls"], 1)
        self.assertEqual(len(llm.calls), 1)


class LockConsolidationTests(unittest.TestCase):
    def items(self, statements, same_passage=False):
        docs = ([DocumentInput("D", "D.docx", "Travaux du projet\n" + ". ".join(statements))]
                if same_passage else [DocumentInput(str(i), f"D{i}.docx", "Travaux du projet\n" + s)
                                      for i, s in enumerate(statements)])
        passages = fast.build_passages(docs)
        rows = [fact("lock", s, next(p for p in passages
                                    if p.document_id == ("D" if same_passage else str(i))
                                    and s in p.text))
                for i, s in enumerate(statements)]
        extractor = fast.GlobalFastExtractor(None, GlobalFastPathConfig())
        return extractor._items([extractor._validate(r, {p.passage_id: p for p in passages}) for r in rows],
                                {p.passage_id: p for p in passages})

    def test_distinct_difficulties_same_domain_stay_separate(self):
        statements = ["Erreurs persistantes de compilation des tests générés",
                      "Difficulté de définition des critères d'évaluation des tests",
                      "Incohérence des noms et signatures des tests générés"]
        items = self.items(statements)
        llm = QueueLLM([{"clusters": [{"canonical_statement": item.statement,
                                      "item_ids": [item.item_id], "reason": "Difficulté distincte"}
                                     for item in items]}])
        entities = GlobalLockConsolidator(llm).consolidate(items)
        self.assertEqual(len(entities), 3)
        self.assertEqual([e.source_item_ids for e in entities], [[x.item_id] for x in items])
        self.assertIn("objet technique précis", llm.calls[0][0])
        self.assertIn("Ne construis pas un cluster en juxtaposant", llm.calls[0][0])
        self.assertEqual(len(llm.calls), 1)

    def test_criteria_and_process_from_same_passage_stay_separate(self):
        items = self.items(["Concevoir les critères d'évaluation des modèles",
                            "Définir le processus d'utilisation des modèles"], same_passage=True)
        llm = QueueLLM([{"clusters": [{"canonical_statement": x.statement,
                                      "item_ids": [x.item_id], "reason": "Objet distinct"} for x in items]}])
        entities = GlobalLockConsolidator(llm).consolidate(items)
        self.assertEqual(len(entities), 2)
        self.assertIn("un même passage source ne démontrent pas cette identité", llm.calls[0][0])
        self.assertIn("deux objets distincts", CONSOLIDATION_SYSTEM)

    def test_two_formulations_of_same_difficulty_can_merge_across_documents(self):
        items = self.items(["Erreurs de compilation persistantes malgré les corrections",
                            "Les corrections ne résolvent pas les échecs de compilation"])
        llm = QueueLLM([{"clusters": [{"canonical_statement": "Échecs persistants de compilation",
                                      "item_ids": [x.item_id for x in items],
                                      "reason": "Même difficulté de compilation après correction"}]}])
        entities = GlobalLockConsolidator(llm).consolidate(items)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].source_item_ids, [x.item_id for x in items])
        self.assertEqual(len(entities[0].evidences), 2)
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("Deux formulations différentes de la MÊME difficulté", CONSOLIDATION_SYSTEM)

    def test_non_lock_facts_do_not_enter_lock_consolidation(self):
        items = self.items(["Erreurs persistantes de compilation des tests"])
        items[0].kind = "result"
        llm = QueueLLM([])
        self.assertEqual(GlobalLockConsolidator(llm).consolidate(items), [])
        self.assertEqual(llm.calls, [])


if __name__ == "__main__":
    unittest.main()
