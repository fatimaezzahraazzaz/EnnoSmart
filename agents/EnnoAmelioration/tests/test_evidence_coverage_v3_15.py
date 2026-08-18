from agents.EnnoAmelioration.application.evidence_coverage_v315 import build_coverage_report, build_mandatory_scholar_payload, render_mandatory_evidence_contract, required_citation_ids

def evidence(*ids):
    return {"scholar":{"available":True,"evidence":[{"citation_id":cid,"title":f"Paper {cid}","abstract":f"Evidence {cid}"} for cid in ids]}}

def test_all_required(): assert required_citation_ids(evidence("A2","A3","A4"))==["A2","A3","A4"]
def test_missing_detected(): assert build_coverage_report(evidence("A2","A3","A4"),"x [A2] y [A4]")["missing_required_ids"]==["A3"]
def test_complete(): assert build_coverage_report(evidence("A2","A3","A4"),"[A2][A3][A4]")["complete"] is True
def test_no_required(): assert build_coverage_report({"scholar":{"evidence":[]}},"x")["complete"] is True
def test_dedup(): assert required_citation_ids({"scholar":{"evidence":[{"citation_id":"A2"},{"citation_id":"A2"},{"citation_id":"A3"}]}})==["A2","A3"]
def test_contract_lists_all(): assert "A2, A3, A4" in render_mandatory_evidence_contract(evidence("A2","A3","A4"))
def test_payload_15_ids():
    p=build_mandatory_scholar_payload(evidence(*[f"A{i}" for i in range(1,16)]),12000)
    assert all(f'"citation_id":"A{i}"' in p for i in range(1,16))
def test_payload_large_abstracts():
    ev={"scholar":{"evidence":[{"citation_id":f"A{i}","title":"x","abstract":"z"*10000} for i in range(1,11)]}}
    p=build_mandatory_scholar_payload(ev,10000)
    assert all(f'"citation_id":"A{i}"' in p for i in range(1,11))
def test_scope_agnostic(): assert build_coverage_report(evidence("A4"),"paragraph [A4]")["complete"] is True
