"""Scientific document invariants: real source evidence, dates and human decisions."""
import copy
import json
import time
from unittest.mock import Mock
import pytest
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.toolkit import ScholarTools
from agents.EnnoScholarV4.scientific_scope import literature_coverage, temporal_scope
from tests.ennoscholar_v4.test_tools import tool_inputs


def tools():
    sources = Mock()
    sources.verify.side_effect = lambda article, deadline: article
    sources.metadata.return_value = []
    return ScholarTools({"messages": [], "articles": {}}, sources, ScholarV4Config(api_key="mock"))


def call(t, name, **args):
    return t.execute(name, args, time.monotonic()+30)


def test_sar_cutoff_and_period_change_without_relevance_ranking():
    t = tools()
    t.store.insert([Article(title=title, year=year, verified=True, publication_date=d, url=f"https://example.org/{year}")
        for title, year, d in [("Foundation",2011,"2011-01-01"),("MERLIN",2022,"2022-06-01"),
            ("Recent work",2024,"2024-06-01"),("Later work",2026,"2026-01-01")]])
    t.state["scientific_scope"] = {"topic":"SAR", "cir_year":2025,"start_date":"2025-01-01","work_type":"plan"}
    rows = literature_coverage(t.state,t.registry)["articles"]
    assert [r["temporal_status"] for r in rows] == ["available_by_reference"]*3+["after_reference"]
    t.state["scientific_scope"]["start_date"] = "2026-03-01"
    assert all(r["temporal_status"]=="available_by_reference" for r in literature_coverage(t.state,t.registry)["articles"])
    assert not t.sources.search.called


def test_exploration_without_date_is_not_blocked_and_year_is_not_exact_date():
    assert not temporal_scope({})["clarification_needed"]
    scope = temporal_scope({"scientific_scope":{"cir_year":2025,"work_type":"state_of_art"}})
    assert scope["knowledge_cutoff"] is None and scope["clarification_needed"]
    assert not literature_coverage({"scientific_scope":{"cir_year":2025}}, {})["coverage_sufficient"]


def read_tables(t):
    from agents.EnnoScholarV4.tools.tables import structure, table_text
    ids,_ = t.store.insert([Article(title="GAInS",url="https://example.org/gains",abstract="Earlier work."),
        Article(title="MMR",url="https://example.org/mmr",authors=["Author"],year=2023,verified=True)])
    aid=ids[1]; chunks=[]
    for number,metric,values in [("2","AUROC",["71.0","84.7"]),("3","PRO",["87.8","89.1"])]:
        table=structure(["Method","Mean"],[["PatchCore",values[0]],["MMR",values[1]]],f"Table {number}: {metric} % on AeBAD-S dataset.",number)
        chunks.append({"text":table_text(table),"section":"Results","page":10,"table":table})
    t.registry[aid].fulltext={"url":"https://example.org/mmr","text":"\n\n".join(c["text"] for c in chunks),"chunks":chunks}
    t.state["evaluation"]={"recommended_article_ids":[ids[0]]}
    for number in ["2","3","3"]:
        result=call(t,"analyze_article",article_ids=[aid],action="read",research_context="MMR",passage_query="",passage_offset=0,passages_per_article=8,analysis=None,table_number=number,revisit=True)
        t.delivered([w["window_id"] for w in [*t.store.windows,*t.reading_windows] if w["status"]=="queued"], inputs=tool_inputs(result))
    return ids


def test_gains_mmr_multiread_four_verified_values_survive_final_context():
    from agents.EnnoScholarV4.context import model_context
    t=tools(); ids=read_tables(t)
    memory=model_context(t.state,t,24000,final=True)[0]["content"]
    for value in ["71.0","84.7","87.8","89.1"]:
        assert value in memory
    assert ids[1] in memory
    assert len(memory)<24000
    assert t.state["evaluation"]["recommended_article_ids"]==[ids[0]]


def outline_args(action,**changes):
    return {"action":action,"title":None,"section_id":None,"target_section_id":None,"position":None,"sections":None,"article_ids":None,**changes}


def test_argumented_plan_edits_keep_ids_and_human_approval_is_invalidated():
    from agents.EnnoScholarV4.manuscript import approve_outline
    t=tools(); ids=read_tables(t)
    first=call(t,"manage_outline",**outline_args("create",sections=[{"title":"Experimental comparison","description":"Compare protocols","article_ids":ids,
        "scientific_objective":"Explain numerical results","article_roles":[{"article_id":ids[1],"role":"Evaluate reconstruction"}]}]))["outline"]
    approve_outline(t.state,42)
    assert t.state["outline"]["approval"]["origin"]=="authenticated_human"
    edited=call(t,"manage_outline",**outline_args("dissociate",section_id="1",article_ids=[ids[0]]))["outline"]
    assert edited["sections"][0]["id"]==first["sections"][0]["id"]
    assert edited["status"]=="modified" and "approval" not in edited
    with pytest.raises(Exception):
        call(t,"manage_outline",**outline_args("approve"))


def test_comparative_relation_requires_both_sources_and_comparable_protocols():
    t=tools(); ids=read_tables(t); aids=ids
    reading=call(t,"analyze_article",article_ids=[ids[0]],action="read",research_context="comparison",passage_query="",passage_offset=0,passages_per_article=8,analysis=None)
    t.delivered([w["window_id"] for w in [*t.store.windows,*t.reading_windows] if w["status"]=="queued"], inputs=tool_inputs(reading))
    refs=[next(e for e in t.shown_evidence if t.evidence[e]["article_id"]==aid) for aid in ids]
    relation={"article_ids":ids,"kind":"conditional_superiority","statement":"Better within a shared protocol","origin":"comparative_inference","conditions":"Same dataset and metric","evidence_ids":refs,"comparable_protocols":False}
    with pytest.raises(ValueError):
        call(t,"compare_articles",article_ids=ids,research_context="comparison",conclusions=None,relations=[relation])
    relation.update(kind="alternative",comparable_protocols=None)
    result=call(t,"compare_articles",article_ids=ids,research_context="comparison",conclusions=None,relations=[relation])
    assert result["scientific_relations"][0]["origin"]=="comparative_inference"


def save_analysis(t, aid):
    from agents.EnnoScholarV4.toolkit import Analysis
    fields={k:[] for k in Analysis.model_fields if k not in {"method_family","analysis_limit"}}
    eid=next(e for e in t.shown_evidence if t.evidence[e]["article_id"]==aid)
    fields.update(method=[{"statement":t.evidence[eid]["exact_quote"],"evidence_ids":[eid],"origin":"source_statement"}],method_family="comparison",analysis_limit="Targeted source passages")
    call(t,"analyze_article",article_ids=[aid],action="save",research_context="comparison",passage_query="",passage_offset=0,passages_per_article=8,analysis=fields)


def drafted_document():
    from agents.EnnoScholarV4.manuscript import approve_outline
    t=tools(); ids=read_tables(t)
    for aid in ids:
        t.registry[aid].verified=True; t.registry[aid].authors=["Author"]; t.registry[aid].year=2023
    reading=call(t,"analyze_article",article_ids=[ids[0]],action="read",research_context="comparison",passage_query="",passage_offset=0,passages_per_article=8,analysis=None)
    t.delivered([w["window_id"] for w in [*t.store.windows,*t.reading_windows] if w["status"]=="queued"], inputs=tool_inputs(reading))
    for aid in ids:
        save_analysis(t,aid)
    call(t,"manage_outline",**outline_args("create",sections=[{"title":title,"description":"Scientific argument","article_ids":ids} for title in ["Comparison","Limits"]]))
    approve_outline(t.state,1)
    return t,ids


def write(t,section,text,ids,eids,**extra):
    return call(t,"write_section",section_id=section,action="save",instructions="Scientific draft",text=text,article_ids=ids,evidence_ids=eids,**extra)


def test_multisection_local_revision_preserves_other_paragraphs_and_evidence():
    t,ids=drafted_document(); eids=t.shown_evidence
    protocol_proof=next(e for e in eids if "AeBAD-S" in t.evidence[e].get("table",{}).get("caption",""))
    text=f"The two approaches differ in their assumptions [paper_{ids[0].removeprefix('paper_')}].\n\nMMR results require the AeBAD-S protocol [{ids[1]}] [{protocol_proof}]."
    assert write(t,"1",text,ids,eids)["saved"]
    assert write(t,"2",f"These conclusions remain conditional [{ids[1]}].",ids,eids)["saved"]
    other=copy.deepcopy(t.state["outline"]["sections"][1])
    first=t.state["outline"]["sections"][0]["drafts"][-1]
    assert write(t,"1",f"The methods rely on different assumptions [{ids[0]}].",ids,eids,paragraph_id=first["paragraphs"][0]["id"])["saved"]
    revised=t.state["outline"]["sections"][0]["drafts"][-1]
    assert revised["paragraphs"][1]==first["paragraphs"][1]
    assert t.state["outline"]["sections"][1]==other
    assert set(revised["evidence_ids"])==set(eids)
    assert len(t.state["document_versions"])==4
    t.sources.search.assert_not_called(); t.sources.verify.assert_not_called()


def test_numeric_cells_are_checked_and_scientific_review_is_separate():
    from agents.EnnoScholarV4.scientific_review import validate_number, deterministic_review
    t,ids=drafted_document()
    eid=next(e for e in t.shown_evidence if t.evidence[e].get("table",{}).get("row")==["PatchCore","71.0"])
    fact={"evidence_id":eid,"column":"Mean","value":"71.0","unit":"%","metric":"AUROC"}
    assert validate_number(fact,t)["numeric_value"]==71.0
    with pytest.raises(ValueError):
        validate_number({**fact,"value":"99.0"},t)
    write(t,"1",f"PatchCore reaches 71.0 % AUROC [{ids[1]}] [{eid}].",[ids[1]],[eid],numeric_claims=[fact])
    report=deterministic_review(t.state,t)
    assert report["integrity_passed"] and not report["scientific_assessment_current"]
    assert any(i["code"]=="unwritten_section" for i in report["issues"])
    assert not report["coverage_sufficient"]


def test_calculated_percentage_points_are_distinct_from_relative_percent():
    from agents.EnnoScholarV4.scientific_review import validate_calculation
    t,ids=drafted_document()
    proofs={t.evidence[e].get("table",{}).get("row",[""])[0]:e for e in t.shown_evidence if t.evidence[e].get("table",{}).get("number")=="2"}
    def fact(method,value):return {"evidence_id":proofs[method],"column":"Mean","value":value,"unit":"%","metric":"AUROC"}
    calc={"left":fact("MMR","84.7"),"right":fact("PatchCore","71.0"),"operation":"difference","result":"13.7","rounding_digits":1,"unit":"points de pourcentage"}
    assert validate_calculation(calc,t)["computed_result"]=="13.7"
    with pytest.raises(ValueError):validate_calculation({**calc,"unit":"%"},t)


def test_long_argumented_outline_does_not_displace_all_exact_mmr_proofs():
    from agents.EnnoScholarV4.writing_context import writing_context
    t,ids=drafted_document(); section=t.state["outline"]["sections"][0]
    section["questions"]=["Detailed question "*100]*30
    for aid in ids:
        t.registry[aid].analysis["limitations"]=[{"statement":"Limit "*300,"evidence_ids":[t.shown_evidence[0]],"origin":"inference"}]*8
    packet=writing_context(t.state,t,section,budget=7000)
    assert len(json.dumps(packet,ensure_ascii=False))<7000
    assert packet["evidence"]
    for value in ["71.0","84.7","87.8","89.1"]:assert value in json.dumps(packet)


def test_section_context_keeps_multiple_result_claims_when_room_is_available():
    from agents.EnnoScholarV4.writing_context import writing_context
    t, ids = drafted_document()
    aid = ids[1]
    proofs = [eid for eid in t.shown_evidence if t.evidence[eid]["article_id"] == aid]
    t.registry[aid].analysis["results"] = [
        {"statement": f"Observed table result {index}: {t.evidence[eid]['exact_quote'][:70]}",
            "evidence_ids": [eid], "origin": "source_statement", "basis": "fulltext"}
        for index, eid in enumerate(proofs[:2], 1)]
    packet = writing_context(t.state, t, t.state["outline"]["sections"][0], budget=14000)
    # Both result claims travel with their exact proofs.
    results = [c for c in packet["proof_backed_claims"] if c["article_id"] == aid and c["field"] == "results"]
    assert len(results) == 2
    assert next(s for s in packet["sources"] if s["id"] == aid)["claims_available"]["results"] == 2
    sent = {e["evidence_id"] for e in packet["evidence"]}
    assert packet["evidence"] and all(set(c["evidence_ids"]) <= sent for c in results)


def test_finalization_keeps_numeric_proofs_and_saved_plan_when_historical_memory_is_huge():
    from agents.EnnoScholarV4.context import model_context
    t,ids=drafted_document()
    t.state["research_state"]={"topic":"MMR","consultant_decisions":["Long historic decision "*800]*20}
    memory=model_context(t.state,t,24000,final=True)[0]["content"]
    assert len(memory)<24000
    assert '"outline"' in memory
    for value in ["71.0","84.7","87.8","89.1"]:assert value in memory


def test_read_later_section_handles_unwritten_preceding_sections():
    t,ids=drafted_document()
    packet=call(t,"write_section",section_id="2",action="read",instructions="Write methods",text=None,article_ids=ids,evidence_ids=[])
    # The manuscript map names the unwritten preceding section instead of inventing its content.
    previous = next(row for row in packet["writing_context"]["manuscript_map"]["sections"] if row["number"] == 1)
    assert previous["status"] == "not_written" and "established" not in previous
    write(t,"2",f"Comparison remains conditional [{ids[1]}].",ids,t.shown_evidence)
    review=call(t,"review_document",action="read",section_id="2")
    assert review["sections"][0]["draft_id"] is None


def figures(t,ids):
    proofs=[t.evidence[e] for e in t.shown_evidence if t.evidence[e].get("table",{}).get("number")=="2"]
    points=[{"label":p["table"]["row"][0],"evidence_id":p["evidence_id"],"column":"Mean","value":p["table"]["row"][1],"unit":"%","metric":"AUROC"} for p in proofs]
    chart=call(t,"create_scientific_visual",section_id="1",kind="chart",title="AUROC on AeBAD-S",caption="Mean reported in Table 2",scientific_argument="Compare two methods under the same protocol",points=points)
    diagram=call(t,"create_scientific_visual",section_id="1",kind="diagram",title="Experimental comparison",caption="Source-based comparison workflow",scientific_argument="Distinguish methods and evaluation",nodes=[{"id":"method","label":"PatchCore and MMR","evidence_ids":[proofs[0]["evidence_id"]],"origin":"source_statement"},{"id":"evaluation","label":"AUROC on AeBAD-S","evidence_ids":[proofs[1]["evidence_id"]],"origin":"inference"}],edges=[{"source":"method","target":"evaluation"}])
    return chart,diagram


def test_figures_use_actual_source_cells_and_reject_unverified_originals():
    t,ids=drafted_document()
    chart,diagram=figures(t,ids)
    assert chart["figure"]["data"][0]["numeric_value"] in {71.0,84.7}
    assert diagram["figure"]["origin"]=="agent_synthesis"
    recipe=t.state["scientific_figures"][0]["recipe"]
    bad=copy.deepcopy(recipe); bad["points"][0]["value"]="99.9"
    with pytest.raises(ValueError):
        call(t,"create_scientific_visual",**bad)
    with pytest.raises(ValueError):
        call(t,"create_scientific_visual",section_id="1",kind="original",title="Original",caption="Original figure",scientific_argument="Explain method")


def test_exports_are_saved_version_based_with_figures_tables_and_reference_numbers():
    import io,zipfile,fitz
    from agents.EnnoScholarV4.document_export import export_document
    t,ids=drafted_document()
    proofs={t.evidence[e].get("table",{}).get("row",[""])[0]:e for e in t.shown_evidence if t.evidence[e].get("table",{}).get("number")=="2"}
    write(t,"1",f"Scientific comparison [{ids[1]}].\n\n| Method | AUROC |\n| --- | --- |\n| PatchCore | 71.0 % [{proofs['PatchCore']}] |\n| MMR | 84.7 % [{proofs['MMR']}] |",ids,t.shown_evidence)
    figures(t,ids)
    version=t.state["document"]["version_id"]
    t.state["messages"].append({"role":"assistant","content":"THIS CHAT IS NOT THE DOCUMENT"})
    docx=export_document(t.state,version,"docx"); pdf=export_document(t.state,version,"pdf")
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        xml=archive.read("word/document.xml").decode()
        assert "71.0" in xml and "84.7" in xml
        assert "Section non rédigée" not in xml and "Limits" not in xml
        assert "THIS CHAT" not in xml and "paper_" not in xml and "ev_" not in xml
        assert len([n for n in archive.namelist() if n.startswith("word/media/")])==2
    with fitz.open(stream=pdf,filetype="pdf") as document:
        text="".join(p.get_text() for p in document)
        assert "71.0" in text and "Références" in text and "THIS CHAT" not in text
        assert "Section non rédigée" not in text and "Limits" not in text
    assert t.state["document"]["version_id"]==version


def test_export_refuses_an_outline_without_any_saved_manuscript():
    from agents.EnnoScholarV4.document_export import export_document
    t, _ = drafted_document()
    version = t.state["document_versions"][-1]["id"]
    with pytest.raises(ValueError, match="Aucune section rédigée"):
        export_document(t.state, version, "pdf")


def test_expired_pdf_link_uses_the_verified_article_landing_page():
    from agents.EnnoScholarV4.publication import readable_source_url
    article = Article(title="Verified study", doi="10.1234/verified", verified=True)
    assert readable_source_url(
        "https://publisher.example/study.pdf?error=cookies_not_supported&code=expired", article
    ) == "https://doi.org/10.1234/verified"
