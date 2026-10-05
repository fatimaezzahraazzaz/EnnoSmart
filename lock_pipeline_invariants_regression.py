"""Generic Python contracts with authored responses, no network calls."""
import copy
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.global_fast_path import GlobalFastExtractor, build_passages
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.completeness_auditor import CompletenessAuditor
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.consolidation import GlobalLockConsolidator
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.diagnostic_debug import DiagnosticDebug
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.lock_controls import lock_signals, validate_lock_coverage
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import GlobalFastPathConfig, PipelineConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.pipeline import EnnoDiagnosticV2
from global_fast_path_regression import QueueLLM, fact


class InvariantTests(unittest.TestCase):
    def setUp(self):
        self.docs = [DocumentInput('A', 'Travaux', '[SECTION : Verrous scientifiques]\n'
            'Nous voulons concevoir un critère fiable. Les essais restent instables.'),
            DocumentInput('B', 'Essais', 'Travaux du projet\n'
            'Nous observons des mesures instables malgré plusieurs corrections.'),
            DocumentInput('C', 'Article', "État de l’art\nLes auteurs décrivent une solution externe instable.")]
        self.passages = build_passages(self.docs)
        self.registry = {p.passage_id: p for p in self.passages}
        self.config = GlobalFastPathConfig(enabled=True)
        self.validator = GlobalFastExtractor(None, self.config)._validate
        self.objective = fact('objective', 'Nous voulons concevoir un critère fiable', self.passages[0], temporal_status='planned')

    def decision(self, signal, code='objective_only'):
        return {**signal, 'decision':'not_a_lock', 'reason':'Pas de difficulté technique démontrée par cet élément seul',
                'reason_code':code, 'existing_lock_ids':[], 'new_lock_ids':[]}

    def test_planned_project_remains_project(self):
        row = self.validator(self.objective, self.registry)
        self.assertEqual((row['source_role'], row['temporal_status']), ('project_evidence','planned'))

    def test_performed_project_remains_project(self):
        row = self.validator(dict(self.objective, temporal_status='performed'), self.registry)
        self.assertEqual(row['source_role'], 'project_evidence')

    def test_planned_context_requires_targeted_repair(self):
        wrong = dict(self.objective, source_role='context')
        with self.assertRaisesRegex(ValueError, 'suspect_project_context'):
            self.validator(wrong, self.registry)
        repaired = dict(self.objective, candidate_id='c0')
        llm = QueueLLM([{'facts':[wrong], 'reviewed_passage_ids':list(self.registry)}, {'facts':[repaired]}])
        items, meta = GlobalFastExtractor(llm,self.config).extract(self.docs)
        self.assertEqual(meta['targeted_retries'], 1)
        self.assertTrue(items[0].metadata['project_admission'])
        self.assertIn('project_evidence ou context', llm.calls[1][0])

    def test_failed_provenance_repair_preserves_unadmitted_fact(self):
        wrong = dict(self.objective, source_role='context')
        llm = QueueLLM([{'facts':[wrong], 'reviewed_passage_ids':list(self.registry)}, RuntimeError('failed')])
        items, _ = GlobalFastExtractor(llm,self.config).extract(self.docs)
        self.assertTrue(items[0].needs_review)
        self.assertFalse(items[0].metadata['project_admission'])
        self.assertEqual(items[0].metadata['validation_error'], 'suspect_project_context')

    def test_external_existing_method_stays_context(self):
        row = fact('method', 'Les auteurs décrivent une solution externe instable', self.passages[2], 'context')
        self.assertEqual(self.validator(row,self.registry)['source_role'], 'context')

    def test_literature_stays_literature(self):
        row = fact('result', 'Les auteurs décrivent une solution externe instable', self.passages[2], 'literature')
        self.assertEqual(self.validator(row,self.registry)['source_role'],'literature')

    def test_literature_cannot_cover_project_lock(self):
        row = fact('lock', 'Les auteurs décrivent une solution externe instable', self.passages[2], 'literature')
        signals=lock_signals(self.passages,[row]); s=signals[-1]
        decisions=[{**s,'decision':'covered_by_existing_lock','existing_lock_ids':['f1'], 'reason':'Même difficulté'}]
        _, errors=validate_lock_coverage(decisions,[s],[row],{},self.registry)
        self.assertTrue(errors)

    def test_objective_does_not_cover_lock(self):
        signals=lock_signals(self.passages,[self.objective]); s=signals[0]
        rows=[{**s,'decision':'covered_by_existing_lock','existing_lock_ids':['f1'], 'reason':'Objectif'}]
        _,errors=validate_lock_coverage(rows,[s],[self.objective],{},self.registry)
        self.assertTrue(errors)

    def test_objective_and_multievidence_lock_coexist(self):
        lock=fact('lock','Mesures instables malgré plusieurs corrections',self.passages[0],
                  passage_ids=['p1','p2'],document_ids=['A','B'])
        normalized=self.validator(lock,self.registry)
        signals=lock_signals(self.passages,[self.objective]); s=signals[0]
        d={**s,'passage_ids':['p1','p2'],'decision':'new_lock_added','new_lock_ids':['a1'],'reason':'Incertitude soutenue par les essais'}
        valid,errors=validate_lock_coverage([d],[s],[self.objective],{'a1':normalized},self.registry)
        self.assertFalse(errors); self.assertEqual(len(valid),1)
        items=GlobalFastExtractor._items([self.objective,normalized],self.registry)
        self.assertEqual(set(i.kind for i in items),{'objective','lock'})

    def test_parameter_with_consequence_can_share_proof(self):
        parameter=fact('parameter','Plusieurs corrections',self.passages[1])
        lock=fact('lock','Mesures instables malgré plusieurs corrections',self.passages[1])
        items=GlobalFastExtractor._items([self.validator(parameter,self.registry), self.validator(lock,self.registry)],self.registry)
        self.assertEqual([i.kind for i in items],['parameter','lock'])
        self.assertEqual(items[0].evidence.passage_id,items[1].evidence.passage_id)
        self.assertEqual(items[0].evidence.quote,items[1].evidence.quote)

    def test_signal_never_creates_fact_for_simple_objective_requirement_metric(self):
        for kind, code in [('objective','objective_only'),('parameter','requirement_only'),('metric','metric_only')]:
            initial=[dict(self.objective,kind=kind)]
            signals=lock_signals(self.passages,initial)
            rows=[self.decision(s,code) for s in signals]
            _, errors=validate_lock_coverage(rows,signals,initial,{},self.registry)
            self.assertFalse(errors)
            self.assertFalse(any(f['kind']=='lock' for f in initial))

    def test_all_signals_must_receive_explicit_decision(self):
        signals=lock_signals(self.passages,[self.objective])
        _,errors=validate_lock_coverage([],signals,[self.objective],{},self.registry)
        self.assertEqual(set(errors),{s['signal_id'] for s in signals})

    def test_embedded_section_signal_is_case_and_accent_insensitive(self):
        docs=[DocumentInput('X','X','Background\n[SECTION : DIFFICULTÉS TECHNIQUES]\nDéfinir un objectif.')]
        ps=build_passages(docs)
        self.assertTrue(lock_signals(ps,[]))

    def test_planned_objective_requests_decision_without_becoming_lock(self):
        p=build_passages([DocumentInput('X','X','Travaux du projet\nConcevoir un critère.')])[0]
        obj=fact('objective','Concevoir un critère',p,temporal_status='planned')
        signal=lock_signals([p],[obj])
        self.assertEqual(len(signal),1)
        self.assertIn('planned_project_objective_to_examine',signal[0]['signals'])
        self.assertEqual(obj['kind'],'objective')

    def test_reference_resolves_exact_proofs_without_model_recopied_chain(self):
        lock=fact('lock','Mesures instables malgré plusieurs corrections',self.passages[0],passage_ids=['p1','p2'],document_ids=['A','B'])
        s=lock_signals(self.passages,[lock])[0]
        d={**s,'decision':'covered_by_existing_lock','existing_lock_ids':['f1'],'reason':'Même phénomène'}
        valid,errors=validate_lock_coverage([d],[s],[lock],{},self.registry)
        self.assertFalse(errors)
        self.assertEqual(valid[0]['resolved_lock_passage_ids'],['p1','p2'])

    def test_one_fast_fact_with_multiple_passages_serializes_once_losslessly(self):
        row=fact('lock','Mesures instables malgré plusieurs corrections',self.passages[0],passage_ids=['p1','p2'],document_ids=['A','B'])
        items=GlobalFastExtractor._items([row],self.registry)
        compact,expand=GlobalLockConsolidator._compact_exact_fast_facts(items)
        self.assertEqual(len(compact),1);self.assertEqual(expand[compact[0].item_id],items)
        llm=QueueLLM([])
        groups=GlobalLockConsolidator(llm,require_intergroup=True).consolidate(items)
        self.assertEqual(len(llm.calls),0);self.assertEqual(len(groups),1)
        self.assertEqual(set(groups[0].source_item_ids),{i.item_id for i in items})
        self.assertEqual({e.evidence_id for e in groups[0].evidences},{i.evidence.evidence_id for i in items})

    def test_compact_relation_aliases_remain_exact_and_checked(self):
        items,groups=self.groups()
        _,aliases=GlobalLockConsolidator._repair_registry(groups)
        row={'pair_id':'pair1','relation':'SUPPORTING_MANIFESTATION','principal_group':'g1',
             'same_technical_object':True,'same_phenomenon':True,'strong_support':True,
             'evidence_refs':['e1','e2'],'reason':'Même phénomène'}
        resolved=GlobalLockConsolidator._resolve_relation_aliases([row],aliases)
        self.assertEqual(len(GlobalLockConsolidator._intergroup(groups,resolved,items)),1)
        with self.assertRaises(ValueError):
            GlobalLockConsolidator._resolve_relation_aliases([dict(row,evidence_refs=['e999'])],aliases)

    def test_missing_decision_retries_existing_callback_only(self):
        llm=QueueLLM([{'facts':[]}]); auditor=CompletenessAuditor(llm,self.config)
        signals=lock_signals(self.passages,[self.objective])
        retry=QueueLLM([{'facts':[],'lock_coverage_decisions':[self.decision(s) for s in signals]}])
        auditor.repair_callback=retry.extract_json
        _,meta=auditor.audit(self.passages,[self.objective],self.validator)
        self.assertTrue(meta['lock_coverage_complete']); self.assertEqual(meta['targeted_retries'],1)
        self.assertEqual(len(retry.calls),1)

    def test_incomplete_retry_never_claims_complete(self):
        auditor=CompletenessAuditor(QueueLLM([{'facts':[]}]),self.config)
        auditor.repair_callback=QueueLLM([{'facts':[]}]).extract_json
        _,meta=auditor.audit(self.passages,[self.objective],self.validator)
        self.assertFalse(meta['lock_coverage_complete']); self.assertEqual(meta['status'],'incomplete')

    def test_invalid_coverage_enum_reference_and_proof_rejected(self):
        s=lock_signals(self.passages,[self.objective])[0]
        for bad in [dict(self.decision(s),decision='silently_accept'),dict(self.decision(s),passage_ids=['outside']),
                    dict(self.decision(s),existing_lock_ids=['invented']),dict(self.decision(s),reason_code='planned_only')]:
            _,errors=validate_lock_coverage([bad],[s],[self.objective],{},self.registry)
            self.assertTrue(errors)

    def test_declared_section_metadata_preserves_exact_intervals(self):
        text='Introduction\n'+('Background words. '*18)+'\n[SECTION : Protocole expérimental]\n'+('Conception des critères. '*40)
        ps=build_passages([DocumentInput('X','X',text)],180)
        self.assertEqual(''.join(p.text for p in ps),text)
        self.assertTrue(all('Protocole' in p.section_title for p in ps if p.char_start>text.index('[SECTION')))

    def groups(self):
        rows=[fact('lock','Les essais restent instables',self.passages[0]),
              fact('lock','Mesures instables malgré plusieurs corrections',self.passages[1])]
        items=GlobalFastExtractor._items(rows,self.registry)
        groups=GlobalLockConsolidator(QueueLLM([{'clusters':[]}])).consolidate(items)
        return items,groups

    def relation(self,groups,relation):
        return {'left_anchor':groups[0].source_item_ids[0],'right_anchor':groups[1].source_item_ids[0],
                'relation':relation,'principal_anchor':groups[0].source_item_ids[0],
                'reason':'Même objet et même phénomène observés' if relation!='DISTINCT_LOCK' else 'Phénomènes distincts',
                'same_technical_object':relation!='DISTINCT_LOCK','same_phenomenon':relation!='DISTINCT_LOCK',
                'strong_support':relation!='DISTINCT_LOCK',
                'evidence_ids':[e.evidence_id for g in groups for e in g.evidences]}

    def test_supporting_manifestation_merges_without_loss(self):
        items,groups=self.groups(); rows=[self.relation(groups,'SUPPORTING_MANIFESTATION')]
        merged=GlobalLockConsolidator._intergroup(groups,rows,items)
        self.assertEqual(len(merged),1); self.assertEqual(merged[0].statement,groups[0].statement)
        self.assertEqual(set(merged[0].source_item_ids),{i.item_id for i in items})
        self.assertEqual({e.evidence_id for e in merged[0].evidences},{i.evidence.evidence_id for i in items})

    def test_same_uncertainty_merges(self):
        items,groups=self.groups()
        self.assertEqual(len(GlobalLockConsolidator._intergroup(groups,[self.relation(groups,'SAME_UNCERTAINTY')],items)),1)

    def test_distinct_locks_stay_separate(self):
        items,groups=self.groups()
        self.assertEqual(len(GlobalLockConsolidator._intergroup(groups,[self.relation(groups,'DISTINCT_LOCK')],items)),2)

    def test_missing_intergroup_pair_and_invalid_proof_fail(self):
        items,groups=self.groups()
        for rows in [[],[dict(self.relation(groups,'SAME_UNCERTAINTY'),evidence_ids=['invented'])],
                     [dict(self.relation(groups,'SAME_UNCERTAINTY'),strong_support=False)]]:
            with self.assertRaises(ValueError): GlobalLockConsolidator._intergroup(groups,rows,items)

    def test_failed_matrix_retry_preserves_every_group(self):
        items,groups=self.groups()
        llm=QueueLLM([{'clusters':[]},RuntimeError('failure')])
        output=GlobalLockConsolidator(llm,require_intergroup=True).consolidate(items)
        self.assertEqual(len(output),len(groups)); self.assertTrue(all(e.needs_review for e in output))
        self.assertTrue(all(not e.metadata['intergroup_validated'] for e in output))

    def test_exact_qualifier_input_debug_not_ui_payload(self):
        lock=fact('lock','Les essais restent instables',self.passages[0])
        llm=QueueLLM([{'facts':[lock],'reviewed_passage_ids':list(self.registry)}])
        with tempfile.TemporaryDirectory() as tmp:
            core=EnnoDiagnosticV2(semantic_llm=QueueLLM([]),fast_extractor=GlobalFastExtractor(llm,self.config),
                                 config=PipelineConfig(run_frascati=False),debug_directory=tmp)
            result=core.run(self.docs)
            trace=[json.loads(l) for l in Path(core.last_debug_reference).read_text(encoding='utf-8').splitlines()]
            self.assertTrue(any(r['stage']=='fast.raw_response' for r in trace))
            sent=next(r['data'] for r in trace if r['stage']=='qualification.input_candidates')
            self.assertEqual(len(sent),1); self.assertEqual(sent[0]['statement'],lock['statement'])
            self.assertNotIn('raw_response',json.dumps(result.to_dict(),default=str))


if __name__=='__main__': unittest.main()
