"""Corrections 3 to 11 of audit 128698bb (financial and technical audit of the section-7 run).

Offline: the real session is read from the local base in read-only mode and replayed with
a simulated model (no provider call, no network, no write to the base).
"""
import copy
import json
from types import SimpleNamespace as NS

import pytest

from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.scientific_reading import next_task, reading_identity, reading_tasks, reading_twins
from tests.ennoscholar_v4.test_audit_91590987 import real_state

LDPOLY = ("paper_9812a1f783142ceb8bf4", "paper_414c65597612f559177b")
HOLITRACER = "paper_e41c0d6864c27137d9f5"


def before_section_7(state):
    """The real manuscript as the run found it: 13 drafts, no card for LDPoly (both
    records) and HoliTracer, their extracted texts kept."""
    state = copy.deepcopy(state)
    for key in ("argumentation_focus", "writing_focus", "writing_planning", "turn_goal", "automatic_reading_focus", "selection_focus"):
        state.pop(key, None)
    state.get("document", {}).pop("writing_progress", None)
    state["outline"]["sections"][6]["drafts"] = []
    for aid in (*LDPOLY, HOLITRACER):
        state["articles"][aid].update(analysis={}, analysis_basis=None, access_status="fulltext_extracted")
    state["scientific_reading"]["tasks"] = [t for t in state["scientific_reading"]["tasks"]
        if t["article_id"] not in (*LDPOLY, HOLITRACER)]
    state["task_results"] = {}
    state["section_received_claims"] = {}
    return state


# --- Correction 3: one reading per document actually read -----------------------------

def test_ldpoly_records_share_one_reading_identity_and_holitracer_has_its_own():
    state = real_state()
    a, b = (Article.model_validate(state["articles"][aid]) for aid in LDPOLY)
    assert reading_identity(a) == reading_identity(b)                    # same work, same document version
    assert reading_identity(Article.model_validate(state["articles"][HOLITRACER])) != reading_identity(a)


def test_the_same_doi_with_another_text_version_or_a_private_document_is_not_merged():
    base = Article(id="paper_a", title="T", doi="10.1/x", canonical_work_id="paper_a",
        fulltext={"text": "v1 text", "chunks": [{"text": "v1 text"}], "document_version": {"document_sha256": "aaa"}})
    other_version = base.model_copy(update={"id": "paper_b", "fulltext": {**base.fulltext, "document_version": {"document_sha256": "bbb"}}})
    assert reading_identity(base) != reading_identity(other_version)
    uploaded = base.model_copy(update={"id": "paper_u", "canonical_work_id": "paper_u"})   # a private upload stays its own work
    assert reading_identity(uploaded) != reading_identity(base)
    changed = base.model_copy(update={"fulltext": {**base.fulltext, "document_version": {"document_sha256": "ccc"}}})
    assert reading_identity(changed) != reading_identity(base)            # a real change of text is read again


def scheduling_state():
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from agents.EnnoScholarV4.config import ScholarV4Config
    from unittest.mock import Mock
    state = before_section_7(real_state())
    state.setdefault("evaluation", {})["recommended_article_ids"] = [*LDPOLY, HOLITRACER]
    toolkit = ScholarTools(state, Mock(), ScholarV4Config(api_key="mock"))
    reading_tasks(state, toolkit.registry, priority_ids=(*LDPOLY, HOLITRACER))
    return state, toolkit


def test_an_alias_is_not_read_again_once_its_twin_has_read_the_document():
    state, toolkit = scheduling_state()
    assert set(next(iter(reading_twins(state, toolkit.registry).values()))) == set(LDPOLY)
    # Before any reading, one record reads for the pair (the canonical one).
    first = next_task(state, toolkit.registry, (*LDPOLY,))
    assert first["article_id"] in LDPOLY
    # The historical record read the first window (as in the real run): the canonical record
    # is never scheduled for the same document; the next window, if any, goes to the reader.
    tasks = {t["article_id"]: t for t in state["scientific_reading"]["tasks"]}
    reader, alias = LDPOLY
    tasks[reader].update(windows_read=1, status="partial", examined_evidence_ids=["e1"], cursor=6)
    for _ in range(3):
        chosen = next_task(state, toolkit.registry, (*LDPOLY,))
        assert chosen is None or chosen["article_id"] == reader
    assert tasks[alias]["reused_from"] == reader


def test_holitracer_keeps_its_complementary_windows():
    state, toolkit = scheduling_state()
    task = next(t for t in state["scientific_reading"]["tasks"] if t["article_id"] == HOLITRACER)
    task.update(windows_read=1, status="partial", examined_evidence_ids=["w1"], cursor=6, core_covered=False)
    again = next_task(state, toolkit.registry, (HOLITRACER,))
    assert again and again["article_id"] == HOLITRACER                       # a second, different window stays possible


def test_a_twin_without_card_is_written_from_the_card_of_the_record_that_read_it():
    from agents.EnnoScholarV4.writing_context import section_article_ids
    state, toolkit = scheduling_state()
    reader, alias = LDPOLY[1], LDPOLY[0]
    toolkit.registry[reader].analysis = {"method": [{"statement": "m", "evidence_ids": ["e"]}]}
    section = {"id": "s7", "article_ids": [alias, HOLITRACER]}
    assert section_article_ids(state, toolkit.registry, section) == [reader, HOLITRACER]
    assert state["reading_aliases"][alias] == reader                      # the plan keeps its id; substitution traced


# --- Corrections 4 to 6 on the real section-7 resumption ----------------------------------

def resumed_run():
    from tests.ennoscholar_v4 import replay_b52f582a as replay
    if not replay.available():
        pytest.skip("Base locale absente")
    state = before_section_7(real_state())
    sections = state["outline"]["sections"]
    before = {s["id"]: [d["id"] for d in s.get("drafts", [])] for s in sections}
    state["messages"].append({"id": "resume_7", "role": "user", "content":
        "Complète uniquement la section 7 de mon état de l'art CIR, sans modifier les autres sections."})
    out = replay.run(state=state, proposals={})
    return state, before, out


def test_resuming_section_7_reads_ldpoly_once_and_never_falls_back_to_a_general_round():
    state, before, out = resumed_run()
    sections = state["outline"]["sections"]
    assert sections[6]["id"] in state["document"]["writing_progress"]["saved_draft_ids"]
    assert all([d["id"] for d in s.get("drafts", [])] == before[s["id"]] for s in sections[:6] + sections[7:])
    readings = [t for t in state["task_results"].values() if t["kind"] == "reading"]
    read_ldpoly = {t["target"] for t in readings if t["target"] in LDPOLY}
    assert len(read_ldpoly) == 1                                             # one record reads for the document
    windows = [(t["target"], tuple(t["attempts"][-1]["evidence_ids"])) for t in readings]
    assert len(windows) == len(set(windows))                                 # no window read twice
    # No general (conversation) round after the goal is understood: readings in flight are awaited.
    assert not [e for e in out["log"] if e["kind"] == "conversation"]


def test_section_packet_lists_real_gaps_and_tools_follow_them():
    from tests.ennoscholar_v4.test_context_memory import (Client, Model, REQUEST_A, agent, rich_corpus,
        seed_argumentation, seven_section_plan)
    from agents.EnnoScholarV4.writing_context import section_packet
    t, ids = rich_corpus(2)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    t.state["messages"] = [{"id": "m", "role": "user", "content": REQUEST_A}]
    seen = []

    def model(n, kw):
        packet = section_packet(kw["input"])
        if packet is not None:
            seen.append((packet["retrieval_gaps"], {tool.get("name") for tool in kw["tools"]}))
        return Model(ids, [])(n, kw)
    agent(Client(model)).run(t.state)
    sequential = [(gaps, tools) for gaps, tools in seen if len(tools) > 1]   # parallel saves offer write_section only
    assert seen and sequential
    for gaps, tools in sequential:
        assert ("get_analysis_claims" in tools) == bool(gaps["claims_not_transmitted"])
        if not (gaps["sources_without_card"] or gaps["dimensions_not_examined"] or gaps["abstract_only"]):
            assert "analyze_article" not in tools and "get_fulltext" not in tools


def test_a_card_page_already_delivered_gets_a_local_no_new_evidence_answer():
    from tests.ennoscholar_v4.test_context_memory import (Client, Model, REQUEST_A, agent, call, rich_corpus,
        save_section, seed_argumentation, seven_section_plan)
    t, ids = rich_corpus(2)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    for number in range(1, 7):
        save_section(t, number, ids)
    t.state["messages"] = [{"id": "m", "role": "user", "content": REQUEST_A}]
    outputs = []

    def model(n, kw):
        outputs.extend(json.loads(item["output"]) for item in kw["input"] if item.get("type") == "function_call_output")
        if n <= 2 and any(tool.get("name") == "get_analysis_claims" for tool in kw["tools"]):
            return call("get_analysis_claims", {"article_id": ids[0], "fields": None, "offset": 0}, n)
        return Model(ids, [])(n, kw)
    agent(Client(model)).run(t.state)
    answers = [o for o in outputs if "no_new_evidence" in o or "claims" in o]
    # Either the tool was not offered (everything already in the packet) or a repeated page answered locally.
    assert all(o.get("no_new_evidence") or o.get("claims") for o in answers)
    assert not any(o.get("claims") == [] and not o.get("no_new_evidence") for o in answers)


# --- Correction 6: no general round, goal kept --------------------------------------------

def test_set_turn_goal_is_not_offered_again_once_the_goal_is_declared():
    from tests.ennoscholar_v4.test_turn_goal import linked_state
    from tests.ennoscholar_v4.test_finalization import Client, call
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    from agents.EnnoScholarV4.config import ScholarV4Config
    state = {"articles": {}, "messages": [{"id": "m", "role": "user", "content": "Bonjour, que sais-tu faire ?"}]}
    offered = []

    def respond(n, kw):
        offered.append({t.get("name") for t in kw["tools"]})
        if n == 1:
            return call("set_turn_goal", {"deliverable": "answer", "lock_ids": [], "focus_lock_ids": [], "exact_quote": "",
                "reading_depth": "core", "understanding": "Question générale.", "search_needed": False}, n)
        return call("finalize_response", {"answer": "Je peux rechercher et analyser des publications.", "article_ids": [],
            "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": [], "displayed_article_ids": None}, n)
    ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=Client(respond)).run(state)
    assert "set_turn_goal" in offered[0]
    assert all("set_turn_goal" not in tools for tools in offered[1:])


# --- Correction 7: comparison phase -------------------------------------------------------

def test_the_comparison_phase_offers_only_its_save_form():
    from tests.ennoscholar_v4.test_context_memory import (Client, Model, rich_corpus, seed_argumentation,
        seven_section_plan, agent)
    t, ids = rich_corpus(3)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    t.state["messages"] = [{"id": "m", "role": "user", "content": "Rédige et compare les méthodes de mon état de l'art."}]
    schemas = []

    def model(n, kw):
        for tool in kw["tools"]:
            if tool.get("name") == "compare_articles":
                schemas.append(tool["parameters"]["properties"]["relations"])
        return Model(ids, [])(n, kw)

    class Goal(Client):
        def create(self, **kw):
            if kw.get("tool_choice") == {"type": "function", "name": "set_turn_goal"}:
                from tests.ennoscholar_v4.test_finalization import Stream, call
                return Stream(NS(output=call("set_turn_goal", {"deliverable": "draft", "lock_ids": [], "focus_lock_ids": [],
                    "exact_quote": "", "reading_depth": "core", "understanding": "Rédiger et comparer.", "search_needed": False,
                    "comparison_requested": True}, 0), output_text="", status="completed", usage={"input_tokens": 10, "output_tokens": 5}))
            return super().create(**kw)
    agent(Goal(model)).run(t.state)
    # Wherever compare_articles is offered during a comparison phase, relations is required and never null.
    assert all(schema.get("type") == "array" for schema in schemas)


def test_works_without_a_shared_documented_dimension_are_not_sent_to_the_model_for_comparison():
    from agents.EnnoScholarV4.argumentation import comparable_dimensions
    from tests.ennoscholar_v4.test_context_memory import rich_corpus
    t, ids = rich_corpus(2)
    shared = comparable_dimensions(t, ids)
    assert shared and all(set(members) == set(ids) for members in shared.values())
    # The second card keeps only its problem statement: no method, protocol, result or limit to compare.
    t.registry[ids[1]].analysis = {k: v for k, v in t.registry[ids[1]].analysis.items()
        if k in {"problem", "evidence", "analysis_basis"}}
    assert comparable_dimensions(t, ids) == {}


# --- Correction 8: local repair -----------------------------------------------------------

def repair_toolkit():
    from tests.ennoscholar_v4.test_context_memory import rich_corpus
    t, ids = rich_corpus(2)
    return t, ids


def test_attribution_accepts_names_supported_by_evidence_and_still_refuses_unsupported_ones():
    from agents.EnnoScholarV4.attribution import check_attribution
    t, ids = repair_toolkit()
    ev = next(e for e in t.evidence.values() if e["article_id"] == ids[0])
    quote = ev["exact_quote"]
    # A project proof naming the architecture tested by the project (SegArchA).
    t.state["mode"] = "diagnostic_linked"
    t.state["diagnostic_link"] = {"snapshot": {"evidence": [{"id": "ev_fast_1", "quote": "Le passage à SegArchA/SegArchB sur LiDAR n'a pas stabilisé le pipeline."}]}}
    from agents.EnnoScholarV4 import diagnostic_link
    original = diagnostic_link.diagnostic_evidence
    diagnostic_link.diagnostic_evidence = lambda state: {"ev_fast_1": {"quote": "Le passage à SegArchA/SegArchB sur LiDAR n'a pas stabilisé le pipeline."}}
    try:
        assert not check_attribution("Le projet a testé SegArchA sans stabiliser le pipeline [projet:ev_fast_1].", ids, t)
        # A project fact keeps its project proof: LiDAR is accepted when the paragraph cites the project
        # passage that names it, refused without it (no vocabulary list).
        assert not check_attribution("Le transfert à des données LiDAR n'est pas démontré. Le projet l'a constaté "
            "[projet:ev_fast_1].", ids, t)
        assert check_attribution("Le transfert à des données LiDAR n'est pas démontré.", ids, t)
        # A name introduced with its passage can be reused; an unsupported one is still refused.
        name = "Douglas-Peucker"
        ev["exact_quote"] = quote + " A modified Douglas-Peucker simplification reduces vertices."
        t.evidence[ev["evidence_id"]] = ev
        text = (f"La publication [{ids[0]}] simplifie par Douglas-Peucker [{ev['evidence_id']}].\n\n"
            "Au lieu d'une simplification de type Douglas-Peucker, d'autres travaux génèrent directement les contours.")
        issues = check_attribution(text, ids, t)
        assert not [i for i in issues if name in i["names"]]
        refused = check_attribution("Le jeu couvre des paysages aux Pays-Bas.", ids, t)
        assert refused and refused[0]["names"] == ["Pays-Bas"]               # no passage names it: still refused
        refused = check_attribution("La comparaison oppose LDPoly à HiSup.", ids, t)
        assert refused                                                       # HiSup without any proof
    finally:
        diagnostic_link.diagnostic_evidence = original


def test_a_refused_proposal_is_repaired_paragraph_by_paragraph_and_revalidated_whole():
    from agents.EnnoScholarV4.local_repair import diagnose, merge, split
    t, ids = repair_toolkit()
    ev = next(e for e in t.evidence.values() if e["article_id"] == ids[0])
    good = [f"La publication [{ids[0]}] décrit sa méthode [{ev['evidence_id']}].", "Le projet reste à confronter à cette méthode.",
        f"Un troisième paragraphe valide reprend [{ids[0]}] et sa preuve [{ev['evidence_id']}]."]
    bad = "La comparaison oppose cette méthode à HiSup sur les Pays-Bas."
    proposal = {"text": "\n\n".join([good[0], bad, good[1], good[2]]), "article_ids": [ids[0]], "evidence_ids": [ev["evidence_id"]],
        "project_evidence_ids": [], "claims": [{"statement": good[0], "evidence_ids": [ev["evidence_id"]]}],
        "numeric_claims": [], "calculations": []}
    diagnosis = diagnose(t, proposal, {"error": "Attribution non vérifiable"})
    assert [p["index"] for p in diagnosis["paragraphs"]] == [1] and diagnosis["kept_paragraphs"] == [0, 2, 3]
    patch = {"paragraph_patches": [{"index": 1, "text": f"La comparaison reste à établir [{ids[0]}] [{ev['evidence_id']}]."}],
        "claims": [], "evidence_ids": [ev["evidence_id"]]}
    merged, receipt = merge(proposal, patch)
    after = split(merged["text"])
    assert [after[0], after[2], after[3]] == [good[0], good[1], good[2]]      # untouched paragraphs, byte for byte
    assert receipt == {"changed_paragraphs": [1], "kept_paragraphs": 3, "paragraphs_total": 4}
    assert merged["claims"] == proposal["claims"]                              # valid claims of kept paragraphs remain
    from agents.EnnoScholarV4.attribution import check_attribution
    assert not check_attribution(merged["text"], merged["article_ids"], t)   # the whole section is re-checked
    with pytest.raises(ValueError):
        merge(proposal, {"paragraph_patches": [{"index": 9, "text": "x"}]})


def test_a_patch_without_a_refused_proposal_is_refused_by_write_section():
    from tests.ennoscholar_v4.test_context_memory import rich_corpus, seven_section_plan
    t, ids = rich_corpus(2)
    seven_section_plan(t, ids)
    sid = t.state["outline"]["sections"][0]["id"]
    with pytest.raises(ValueError, match="paragraph_patches"):
        t.execute("write_section", {"section_id": sid, "action": "save", "instructions": "", "text": None, "article_ids": [],
            "evidence_ids": [], "paragraph_patches": [{"index": 0, "text": "x"}]}, float("inf"))


# --- Corrections 9 and 10: cache instrumentation and model per task -----------------------

def test_prefix_fingerprints_measure_what_a_cache_could_reuse_without_retaining_text():
    from agents.EnnoScholarV4.finance import prefix_probe
    first = {"prompt_cache_key": "ennoscholar-v4-section", "instructions": "règles", "tools": [{"name": "write_section"}],
        "input": [{"role": "developer", "content": "mémoire A"}, {"role": "developer", "content": "consigne 1"}]}
    probe1 = prefix_probe(first, None)
    second = {**first, "input": [first["input"][0], {"role": "developer", "content": "consigne 2"}]}
    probe2 = prefix_probe(second, probe1)
    assert probe2["compared_with_previous"]["shared_items"] == 1 and probe2["compared_with_previous"]["first_changed_item"] == 1
    assert "mémoire" not in json.dumps(probe2, ensure_ascii=False)          # hashes and sizes only
    third = {**second, "tools": [{"name": "get_analysis_claims"}]}
    assert prefix_probe(third, probe2)["compared_with_previous"]["shared_prefix_bytes"] == 0


def test_model_routing_is_neutral_by_default_and_validated_when_configured():
    from dataclasses import replace
    from agents.EnnoScholarV4.config import ConfigurationError, ScholarV4Config
    from agents.EnnoScholarV4.routing import model_for, STAGES
    config = ScholarV4Config(api_key="mock", model="gpt-5.6-sol")
    assert {model_for(config, kind) for stage in STAGES.values() for kind in stage} == {"gpt-5.6-sol"}
    routed = replace(config, task_models=json.dumps({"understanding": "gpt-5.6-luna"}))
    routed.validate()
    assert model_for(routed, "understanding") == "gpt-5.6-luna" and model_for(routed, "reading") == "gpt-5.6-sol"
    with pytest.raises(ConfigurationError):
        replace(config, task_models=json.dumps({"lecture": "gpt-5.6-luna"})).validate()
    with pytest.raises(ConfigurationError):
        replace(config, task_models=json.dumps({"reading": "modele-sans-tarif"})).validate()


def test_each_receipt_records_the_model_requested_its_price_and_the_prefix_probe():
    from tests.ennoscholar_v4.test_context_memory import Client, Model, REQUEST_A, agent, rich_corpus, seven_section_plan
    t, ids = rich_corpus(2)
    seven_section_plan(t, ids)
    t.state["messages"] = [{"id": "m", "role": "user", "content": REQUEST_A}]
    agent(Client(Model(ids, []))).run(t.state)
    attempts = [a for task in t.state["task_results"].values() for a in task["attempts"] if a.get("cost")]
    assert attempts and all(a["model_requested"] and a["price_version"] and a.get("cache_probe") for a in attempts)


# --- Correction 11: shared public library (design, not activated) ------------------------

def test_only_public_documents_could_be_shared_and_private_ones_never():
    from agents.EnnoScholarV4.public_library import library_key, shareable
    state = real_state()
    ldpoly = Article.model_validate(state["articles"][LDPOLY[0]])          # arXiv HTML, public
    assert shareable(ldpoly) and library_key(ldpoly)["identity"] == reading_identity(ldpoly)
    uploaded = ldpoly.model_copy(update={"fulltext": {**ldpoly.fulltext, "source_kind": "user_uploaded_document"}})
    assert not shareable(uploaded) and library_key(uploaded) is None
    anonymous = ldpoly.model_copy(update={"doi": "", "arxiv_id": ""})
    assert library_key(anonymous) is None
