"""Offline relation-based LOCK and additive PARAMETER prompt regressions."""
import copy
import unittest
from dataclasses import asdict

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.completeness_auditor import AUDIT_SYSTEM, CompletenessAuditor
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalFastPathConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.consolidation import CONSOLIDATION_SYSTEM, GlobalLockConsolidator
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_fast_path import GlobalFastExtractor, build_passages
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
from global_fast_path_regression import fact
from lock_test_fixtures import CoverageQueueLLM as QueueLLM


def cluster(items, relations, anchor=None, statement=None):
    return {"item_ids": [i.item_id for i in items],
            "anchor_item_id": anchor or items[0].item_id,
            "canonical_statement": statement or items[0].statement,
            "relations": [{"item_id": item.item_id, "relation": relation,
                           "reason": "Même objet et phénomène documentés ; manifestation sans difficulté indépendante"
                           if relation != "DISTINCT_LOCK" else "Mécanisme différent nécessitant une résolution distincte"}
                          for item, relation in relations],
            "reason": "Identité ou différence des objets et mécanismes vérifiée dans les preuves"}


class LockRelationsTests(unittest.TestCase):
    def setUp(self):
        self.docs = [DocumentInput("MAIN", "Travaux.docx", "Travaux du projet\n"
                     "La segmentation reste instable sur des données hétérogènes et des labels décalés."),
                     DocumentInput("EXPERIMENT", "Essais.docx", "Travaux du projet\n"
                     "Ces mêmes hétérogénéités imposent des seuils de confiance élevés ; "
                     "les gains pixel ne stabilisent pas les frontières de segmentation."),
                     DocumentInput("TOPO", "Polygonisation.docx", "Travaux du projet\n"
                     "La polygonisation produit des auto-intersections et une fragilité topologique."),
                     DocumentInput("BIAS", "Lissage.docx", "Travaux du projet\n"
                     "Le lissage produit un biais de parallélisme et altère la fidélité géométrique locale.")]
        self.passages = build_passages(self.docs)
        rows = [fact("lock", text, self.passages[p]) for text, p in (
            ("Segmentation instable sur données hétérogènes et labels décalés", 0),
            ("Seuils élevés dus aux mêmes hétérogénéités de segmentation", 1),
            ("Les gains pixel ne stabilisent pas les frontières de segmentation", 1),
            ("Polygonisation topologiquement fragile avec auto-intersections", 2),
            ("Biais de parallélisme du lissage et perte de fidélité géométrique locale", 3))]
        self.items = GlobalFastExtractor._items(rows, {p.passage_id: p for p in self.passages})

    def consolidate(self, clusters, items=None):
        llm = QueueLLM([{"clusters": clusters}])
        output = GlobalLockConsolidator(llm).consolidate(items or self.items)
        self.assertEqual(len(llm.calls), 1)
        return output, llm

    def assert_coverage(self, entities, items):
        ids = [i for e in entities for i in e.source_item_ids]
        self.assertEqual(sorted(ids), sorted(i.item_id for i in items))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({e.evidence_id for entity in entities for e in entity.evidences},
                         {i.evidence.evidence_id for i in items})
        self.assertEqual({e.passage_id for entity in entities for e in entity.evidences},
                         {i.evidence.passage_id for i in items})

    def test_same_uncertainty_different_wording_merges_once(self):
        items = self.items[:2]
        entities, _ = self.consolidate([cluster(items, [(items[1], "SAME_UNCERTAINTY")])], items)
        self.assertEqual(len(entities), 1)
        self.assert_coverage(entities, items)

    def test_manifestations_keep_every_exact_evidence_and_source_document(self):
        items = self.items[:3]
        before = [asdict(i) for i in items]
        entities, llm = self.consolidate([cluster(items, [(i, "SUPPORTING_MANIFESTATION") for i in items[1:]])], items)
        self.assertEqual(len(entities), 1)
        self.assertEqual(len(entities[0].evidences), 3)
        self.assertEqual(set(entities[0].document_ids), {"MAIN", "EXPERIMENT"})
        for item in items:
            proof = next(e for e in entities[0].evidences if e.evidence_id == item.evidence.evidence_id)
            self.assertEqual(asdict(proof), asdict(item.evidence))
            self.assertIn(item.evidence.evidence_id, llm.calls[0][1])
            self.assertIn(item.evidence.passage_id, llm.calls[0][1])
        self.assertEqual([asdict(i) for i in items], before)
        self.assert_coverage(entities, items)

    def test_two_different_mechanisms_remain_separate_even_in_same_cluster(self):
        items = [self.items[0], self.items[3]]
        entities, _ = self.consolidate([cluster(items, [(items[1], "DISTINCT_LOCK")],
                statement="Une synthèse invalide juxtaposant deux problèmes")], items)
        self.assertEqual(len(entities), 2)
        self.assertEqual({e.statement for e in entities}, {i.statement for i in items})
        self.assert_coverage(entities, items)

    def test_road_three_locks_not_one_family_and_no_secondary_decision_duplicates(self):
        relations = [(i, "SUPPORTING_MANIFESTATION") for i in self.items[1:3]]
        relations += [(i, "DISTINCT_LOCK") for i in self.items[3:]]
        entities, _ = self.consolidate([cluster(self.items, relations)])
        self.assertEqual(len(entities), 3)
        self.assertEqual([len(e.source_item_ids) for e in entities], [3, 1, 1])
        self.assertEqual([e.statement for e in entities],
                         [self.items[0].statement, self.items[3].statement, self.items[4].statement])
        self.assert_coverage(entities, self.items)

    def test_fast_and_audit_locks_receive_identical_relation_treatment(self):
        config = GlobalFastPathConfig()
        initial = fact("lock", self.items[0].statement, self.passages[0])
        addition = fact("lock", self.items[1].statement, self.passages[1])
        fast_llm = QueueLLM([{"reviewed_passage_ids": [p.passage_id for p in self.passages],
                             "facts": [initial]}])
        audit_llm = QueueLLM([{"facts": [addition]}])
        extractor = GlobalFastExtractor(fast_llm, config,
            completeness_auditor=CompletenessAuditor(audit_llm, config))
        items, meta = extractor.extract(self.docs)
        entities, _ = self.consolidate([cluster(items, [(items[1], "SUPPORTING_MANIFESTATION")])], items)
        self.assertEqual(len(entities), 1)
        self.assert_coverage(entities, items)
        self.assertEqual(len(fast_llm.calls), 1)
        self.assertEqual(len(audit_llm.calls), 1)
        self.assertEqual(meta["llm_calls"], 2)

    def test_invalid_unknown_or_missing_relation_keeps_candidate_without_extra_call(self):
        items = self.items[:2]
        for relation in ("UNSUPPORTED", None, ["SAME_UNCERTAINTY"]):
            with self.subTest(relation=relation):
                group = cluster(items, [(items[1], relation)])
                entities, _ = self.consolidate([group], items)
                self.assertEqual(len(entities), 2)
                self.assert_coverage(entities, items)
                self.assertTrue(entities[1].needs_review)
        entities, _ = self.consolidate([cluster(items, [])], items)
        self.assertEqual(len(entities), 2)
        self.assert_coverage(entities, items)

    def test_invalid_reference_or_anchor_never_loses_candidates(self):
        items = self.items[:2]
        group = cluster(items, [(items[1], "SAME_UNCERTAINTY")])
        variants = [dict(group, anchor_item_id="outside"), dict(group, relations=None),
                    dict(group, relations=[{"item_id": "outside", "relation": "SAME_UNCERTAINTY"}]),
                    dict(group, relations=[{}, *group["relations"]])]
        for variant in variants:
            with self.subTest(variant=variant):
                entities, _ = self.consolidate([variant], items)
                self.assertEqual(len(entities), 2)
                self.assert_coverage(entities, items)

    def test_duplicate_relation_and_empty_reason_are_not_accepted(self):
        items = self.items[:2]
        group = cluster(items, [(items[1], "SAME_UNCERTAINTY")])
        for rows in ([*group["relations"], *group["relations"]],
                     [dict(group["relations"][0], reason="")]):
            entities, _ = self.consolidate([dict(group, relations=rows)], items)
            self.assertEqual(len(entities), 2)
            self.assert_coverage(entities, items)

    def test_missing_llm_candidate_still_preserved(self):
        group = cluster(self.items[:2], [(self.items[1], "SAME_UNCERTAINTY")])
        entities, _ = self.consolidate([group])
        self.assert_coverage(entities, self.items)

    def test_old_cluster_format_still_supported(self):
        items = self.items[:2]
        entities, _ = self.consolidate([{"item_ids": [i.item_id for i in items],
            "canonical_statement": items[0].statement, "reason": "Même difficulté"}], items)
        self.assertEqual(len(entities), 1)
        self.assert_coverage(entities, items)

    def test_anax_explicit_numbered_locks_never_absorbed(self):
        docs = [DocumentInput("ANAX", "ANAX.docx", "Le premier verrou concerne la fiabilité des résultats."),
                DocumentInput("ANAX", "ANAX.docx", "Le deuxième verrou concerne les méthodes de validation.")]
        # One document has separate exact passages with separate source ordinals.
        from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import EvidenceRef, ExtractionItem
        items = [ExtractionItem(item_id=f"anax{i}", kind="lock", statement=doc.text,
            evidence=EvidenceRef(evidence_id=f"ev_anax{i}", document_id="ANAX", document_name=doc.name,
                chunk_id=f"p{i}", passage_id=f"p{i}", quote=doc.text, quote_verified=True),
            metadata={"source_role": "project_evidence"}) for i, doc in enumerate(docs)]
        entities, _ = self.consolidate([cluster(items, [(items[1], "SAME_UNCERTAINTY")])], items)
        self.assertEqual(len(entities), 2)
        self.assert_coverage(entities, items)

    def test_ai_code_compilation_and_evaluation_uncertainties_stay_distinct(self):
        docs = [DocumentInput("AI-CODE", "Essais.docx", "Les échecs de compilation persistent. "
                             "Les critères d'évaluation de la qualité restent difficiles à définir.")]
        p = build_passages(docs)[0]
        rows = [fact("lock", statement, p) for statement in (
            "Échecs persistants de compilation", "Difficulté de définition des critères d'évaluation de qualité")]
        items = GlobalFastExtractor._items(rows, {p.passage_id: p})
        entities, _ = self.consolidate([cluster(items, [(items[1], "DISTINCT_LOCK")])], items)
        self.assertEqual(len(entities), 2)
        self.assert_coverage(entities, items)

    def test_zero_and_singleton_locks_still_use_zero_llm_calls(self):
        llm = QueueLLM([])
        self.assertEqual(GlobalLockConsolidator(llm).consolidate([]), [])
        entities = GlobalLockConsolidator(llm).consolidate(self.items[:1])
        self.assertEqual(len(entities), 1)
        self.assertEqual(llm.calls, [])

    def test_consolidation_prompt_demands_causal_identity_not_theme_or_family(self):
        for text in ("SAME_UNCERTAINTY", "SUPPORTING_MANIFESTATION", "DISTINCT_LOCK",
                     "chaîne causale", "même objet technique", "sans décider de leur qualification",
                     "mêmes documents", "une même famille", "en cas de doute, DISTINCT_LOCK"):
            self.assertIn(text, CONSOLIDATION_SYSTEM)


class ParameterDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.config = GlobalFastPathConfig()

    def test_definition_covers_settings_conditions_and_technical_constraints(self):
        for text in ("condition expérimentale", "contrainte technique", "reproductibilité",
                     "seuils de confiance", "pondérations de classes", "politiques d'augmentation",
                     "résolution", "jeux de paramètres", "labels partiels ou décalés", "validité topologique"):
            self.assertIn(text, AUDIT_SYSTEM)

    def test_descriptions_excluded_with_experimental_exception(self):
        for text in ("EXCLURE PAR DÉFAUT", "nombre total d'images", "kilomètres",
                     "liste exhaustive de classes/IDs", "pourcentages descriptifs de répartition",
                     "EXCEPTION", "variable/condition expérimentale", "sous-ensemble/jeu complet",
                     "sans recopier tous les", "Ne duplique pas automatiquement un optimiseur"):
            self.assertIn(text, AUDIT_SYSTEM)

    def test_road_supported_parameters_reach_items_and_initial_dataset_fact_is_immutable(self):
        doc = DocumentInput("ROAD", "Expériences.docx", "Travaux du projet\n"
            "Le dataset Offroad3D contient 27 000 images, 70 km et 73 % d'autoroute. "
            "Nous comparons les jeux de paramètres tight et loose et les seuils de confiance élevés. "
            "Les labels partiels et décalés sont une contrainte expérimentale. "
            "La validité topologique est imposée à la sortie.")
        passages = build_passages([doc])
        p = passages[0]
        initial = [fact("parameter", "Dataset Offroad3D : 27 000 images", p)]
        before = copy.deepcopy(initial)
        additions = [fact("parameter", text, p) for text in (
            "Jeux de paramètres tight et loose", "Seuils de confiance élevés",
            "Labels partiels et décalés comme contrainte expérimentale", "Validité topologique imposée à la sortie")]
        llm = QueueLLM([{"facts": additions}])
        validator = GlobalFastExtractor(None, self.config)._validate
        added, meta = CompletenessAuditor(llm, self.config).audit(passages, initial, validator)
        self.assertEqual(added, additions)
        self.assertEqual(initial, before)
        self.assertEqual(meta["llm_calls"], 1)
        # Exclusion is a prompt rule, not a new destructive Python admission gate.
        self.assertIn("EXCLURE PAR DÉFAUT", llm.calls[0][0])
        self.assertTrue(all("27 000" not in row["statement"] and "70 km" not in row["statement"] for row in added))

    def test_dataset_quantity_can_be_experimental_parameter_when_compared(self):
        doc = DocumentInput("D", "D.docx", "Nous comparons l'entraînement sur un sous-ensemble et "
                            "le jeu complet pour mesurer l'effet de la quantité de données.")
        passages = build_passages([doc])
        row = fact("parameter", "Quantité de données : sous-ensemble ou jeu complet", passages[0])
        added, _ = CompletenessAuditor(QueueLLM([{"facts": [row]}]), self.config).audit(
            passages, [], GlobalFastExtractor(None, self.config)._validate)
        self.assertEqual(added, [row])

    def test_optimizer_method_not_automatically_duplicated_as_parameter(self):
        doc = DocumentInput("D", "D.docx", "Nous entraînons avec l'optimiseur RMSProp.")
        passages = build_passages([doc])
        initial = [fact("method", "Optimiseur RMSProp", passages[0])]
        before = copy.deepcopy(initial)
        llm = QueueLLM([{"facts": []}])
        added, _ = CompletenessAuditor(llm, self.config).audit(
            passages, initial, GlobalFastExtractor(None, self.config)._validate)
        self.assertEqual(added, [])
        self.assertEqual(initial, before)
        self.assertIn("choix de l'optimiseur", llm.calls[0][0])

    def test_anax_idm_method_remains_in_initial_inventory(self):
        doc = DocumentInput("ANAX", "ANAX.docx", "Nous utilisons l'ingénierie dirigée par les modèles : "
                            "modèle source, règles de transformation, modèle cible.")
        passages = build_passages([doc])
        initial = [fact("method", "Ingénierie dirigée par les modèles et règles de transformation", passages[0])]
        before = copy.deepcopy(initial)
        added, _ = CompletenessAuditor(QueueLLM([{"facts": []}]), self.config).audit(
            passages, initial, GlobalFastExtractor(None, self.config)._validate)
        self.assertEqual(initial, before)
        self.assertEqual(added, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
