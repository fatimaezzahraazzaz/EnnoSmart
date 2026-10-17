"""Persistent scientific memory, task-scoped context and multi-section drafting (tests A-F).

Every corpus here is built through the real tools (reading windows, exact
evidence, cumulative analysis saves); the model is a deterministic fake client.
No network, no OpenAI call.
"""
import copy
import json
import re
import time
from dataclasses import replace
from unittest.mock import Mock

from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.context import context_task, model_context
from agents.EnnoScholarV4.instructions import capture_instructions
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.scientific_reading import reading_tasks
from agents.EnnoScholarV4.writing_progress import begin_writing
from tests.ennoscholar_v4.test_finalization import Client, call
from tests.ennoscholar_v4.test_tools import tool_inputs
from tests.ennoscholar_v4.test_writing import outline_args, tools
from agents.EnnoScholarV4.tools.tables import table_text

CONFIG = replace(ScholarV4Config(api_key="mock", web_search_enabled=False))
REQUEST_A = ("Rédige l'état de l'art complet à partir du plan existant et des articles validés. "
    "Continue les sections non encore enregistrées sans modifier celles qui sont terminées.")
LAYOUT = {"technical_mechanism": 8, "results": 12, "limitations": 5, "tables_figures": 3, "method": 2, "experimental_setup": 2}


def execute(t, name, **args):
    return t.execute(name, args, time.monotonic()+60)


def passage(i, j, pad=0):
    return (f"In work {i}, passage {j}: the mesher reaches a minimum scaled Jacobian of 0.{j:02d}{i % 10} on benchmark B{j} "
        f"with {1000+j} hexahedra under protocol P{j}, compared with {900+j} for the baseline."
        + " The surrounding discussion details the frame field, the singularity graph and the parametrization." * pad)


def rich_corpus(n_articles=4, passages=60, read_windows=4, candidates=0, pad=0, table=None):
    """Selected works with detailed cards (8 mechanisms, 12 results, 5 limitations,
    3 tables, 32 exact proofs each) and sources only partly traversed."""
    t = tools()
    works = [Article(title=f"Work{i}: learned hexahedral meshing variant {i}", url=f"https://example.org/w{i}",
        doi=f"10.1234/w{i}", authors=["Author"], year=2024, verified=True, abstract=f"Volumetric meshing study {i}.")
        for i in range(n_articles)]
    ids, _ = t.store.insert(works)
    for i, aid in enumerate(ids):
        chunks = [{"text": passage(i, j, pad), "section": "Results" if j % 2 else "Method", "page": j+1} for j in range(passages)]
        if table and i == 0:
            # A structured results table of the first work, read before its card is saved.
            chunks.append({"text": table_text(table), "section": "Results", "page": passages+1, "table": table})
        t.registry[aid].fulltext = {"url": f"https://example.org/w{i}", "text": "\n\n".join(c["text"] for c in chunks), "chunks": chunks}
        if table and i == 0:
            result = execute(t, "analyze_article", article_ids=[aid], action="read", research_context="table", passage_query="",
                passage_offset=0, passages_per_article=8, analysis=None, table_number=table["number"], revisit=True)
            t.delivered([w["window_id"] for w in [*t.store.windows, *t.reading_windows] if w["status"] == "queued"],
                inputs=tool_inputs(result))
        for offset in range(0, read_windows*8, 8):
            result = execute(t, "analyze_article", article_ids=[aid], action="read", research_context="meshing",
                passage_query="", passage_offset=offset, passages_per_article=8, analysis=None, revisit=True)
            t.delivered([w["window_id"] for w in [*t.store.windows, *t.reading_windows] if w["status"] == "queued"],
                inputs=tool_inputs(result))
        shown = [e for e in t.shown_evidence if t.evidence[e]["article_id"] == aid]
        claims, cursor = {}, 0
        for field, count in LAYOUT.items():
            # The card states the finding (first sentence of its exact proof).
            claims[field] = [{"statement": t.evidence[e]["exact_quote"].split(" The surrounding")[0], "evidence_ids": [e], "origin": "source_statement"}
                for e in shown[cursor:cursor+count]]
            cursor += count
        # Proofs read beyond the layout (table rows) are incorporated too.
        claims["tables_figures"] += [{"statement": t.evidence[e]["exact_quote"].split(" The surrounding")[0],
            "evidence_ids": [e], "origin": "source_statement"} for e in shown[cursor:]]
        for part in (0, 1):  # cards are enriched by successive saves (8 claims per field per save)
            analysis = {field: [] for field in ("problem", "objective", "contribution", "hypotheses", "generalization",
                "open_questions", "relevance")}
            analysis.update({field: rows[part*8:(part+1)*8] for field, rows in claims.items()})
            analysis.update(method_family=f"learned meshing family {part}", analysis_limit=f"Targeted passages, window set {part}")
            execute(t, "analyze_article", article_ids=[aid], action="save", research_context="meshing", passage_query="",
                passage_offset=0, passages_per_article=8, analysis=analysis)
        t.registry[aid].selected = True
    if candidates:
        t.store.insert([Article(title=f"Unrelated candidate {k}", url=f"https://example.org/c{k}", abstract="Not evaluated.")
            for k in range(candidates)])
    t.state["evaluation"] = {"recommended_article_ids": ids}
    t.state["research_state"] = {"topic": "Génération automatique de maillages volumiques 3D par IA"}
    # Ledger as left by the earlier automatic readings: windows examined and
    # incorporated, core dimensions covered, the rest of each source unread.
    ledger = reading_tasks(t.state, t.registry)
    for task in ledger["tasks"]:
        shown = proofs_of(t, task["article_id"])
        task.update(cursor=len(shown), examined_evidence_ids=shown, windows_read=read_windows)
    reading_tasks(t.state, t.registry)
    t.save()
    return t, ids


def all_claims(t, aid, fields=None):
    """Page through get_analysis_claims until complete; returns claims and proofs."""
    claims, proofs, offset, pages = [], {}, 0, 0
    while True:
        page = execute(t, "get_analysis_claims", article_id=aid, fields=fields, offset=offset)
        assert len(json.dumps(page, ensure_ascii=False)) <= 12000
        claims.extend(page["claims"])
        proofs.update({e["evidence_id"]: e for e in page["evidence"]})
        offset, pages = page["next_offset"], pages+1
        if page["complete"]:
            return claims, proofs, pages


def proofs_of(t, aid):
    return [e for e in t.shown_evidence if t.evidence[e]["article_id"] == aid]


def seed_argumentation(t, ids):
    """A saved comparison and blueprint, as left by the earlier turns."""
    from agents.EnnoScholarV4.argumentation import comparison_batches, comparison_source_signature
    t.state["comparison_reading"] = {"|".join(batch): {"complete": True, "claim_offsets": {},
        "source_signature": comparison_source_signature(t.registry, batch)} for batch in comparison_batches(t.state, t.registry)}
    steps = [{"kind": "method", "statement": "Alternatives documentées", "article_ids": ids[k:k+8],
        "evidence_ids": [proofs_of(t, aid)[0] for aid in ids[k:k+8]], "relation_ids": [], "project_fact_ids": [],
        "conditions": "Protocoles différents", "uncertainty": ""} for k in range(0, len(ids), 8)]
    execute(t, "build_argument_blueprint", action="save", global_question="Méthodes de maillage volumique apprises ?",
        narrative_logic="Des mécanismes aux résultats et aux limites.",
        axes=[{"axis": "méthodes", "scientific_problem": "Comparer les mécanismes", "section_id": None,
            "transition": "Évaluer les preuves expérimentales.", "steps": steps}], remaining_questions=[], claim_offsets=[])


def seven_section_plan(t, ids, per_section=None):
    groups = per_section or [ids]*7
    execute(t, "manage_outline", **outline_args("create", sections=[{"title": f"Section {n+1} maillage hexaédrique",
        "description": "Argument scientifique", "article_ids": groups[n % len(groups)]} for n in range(7)]))


def save_section(t, number, ids):
    section = t.state["outline"]["sections"][number-1]
    eid = proofs_of(t, section["article_ids"][0])[0]
    return execute(t, "write_section", section_id=section["id"], action="save", instructions="Rédaction",
        text=f"Section rédigée par le consultant [{section['article_ids'][0]}] [{eid}].",
        article_ids=section["article_ids"], evidence_ids=[eid])


def agent(client, config=CONFIG):
    sources = Mock()
    sources.search.side_effect = AssertionError("No bibliographic search")
    sources.verify.side_effect = lambda article, deadline: article
    return ScholarChatAgent(config, client=client, sources=sources)


def memory_of(kw):
    return json.loads(kw["input"][0]["content"].split(" : ", 1)[1])


def forced(kw):
    choice = kw.get("tool_choice")
    return choice.get("name") if isinstance(choice, dict) else None


class Model:
    """Deterministic stand-in for the conversation model, by forced task."""

    def __init__(self, t_ids, log):
        self.ids, self.log = t_ids, log

    def __call__(self, n, kw):
        memory = memory_of(kw)
        size = len(json.dumps(kw["input"], ensure_ascii=False))
        self.log.append({"task": memory.get("context_task", "conversation"), "forced": forced(kw), "size": size,
            "memory": memory, "tool_outputs": [item["output"] for item in kw["input"] if item.get("type") == "function_call_output"]})
        name = forced(kw)
        if name == "analyze_article":
            raise AssertionError("Stored analyses must not be re-read before drafting")
        if name == "compare_articles":
            focus = memory["argumentation_focus"]
            return call("compare_articles", {"article_ids": focus["article_ids"], "research_context": "comparaison",
                "conclusions": None, "relations": [], "claim_offsets": []}, n)
        if name == "build_argument_blueprint":
            focus = memory["argumentation_focus"]
            papers = focus["packet"]["papers"]
            eids = [paper["proofs"][0]["evidence_id"] for paper in papers if paper["proofs"]]
            return call("build_argument_blueprint", {"action": "save", "merge": focus["merge"], "global_question": "Q",
                "narrative_logic": "Logique", "axes": [{"axis": "méthodes", "scientific_problem": "Comparer", "section_id": None,
                    "transition": "Suite", "steps": [{"kind": "method", "statement": "Alternatives",
                        "article_ids": [p["id"] for p in papers], "evidence_ids": eids, "relation_ids": [],
                        "project_fact_ids": [], "conditions": "Protocoles différents", "uncertainty": ""}]}],
                "remaining_questions": [], "claim_offsets": []}, n)
        if name == "manage_outline":
            return call("manage_outline", outline_args("create", sections=[{"title": f"Axe {k} des méthodes de maillage",
                "description": "Argument", "article_ids": self.ids} for k in range(1, 4)]), n)
        drafting = memory["section_drafting"]
        packet = drafting["packet"]
        sid = drafting["focus"]["section_id"]
        proof = packet["evidence"][0]
        return call("write_section", {"section_id": sid, "action": "save", "instructions": "Rédaction étayée",
            "text": f"Synthèse argumentée de la section [{proof['article_id']}] [{proof['evidence_id']}].",
            "article_ids": [proof["article_id"]], "evidence_ids": [proof["evidence_id"]]}, n)


def large_history(state):
    long_answer = "## Sélection\n\n" + ("Analyse détaillée des méthodes et des résultats. " * 190)
    state["messages"] = [m for pair in [({"id": f"u{k}", "role": "user", "content": "Analyse ces publications en détail."},
        {"id": f"a{k}", "role": "assistant", "content": long_answer}) for k in range(5)] for m in pair]


def test_a_real_bug_resume_after_two_sections_fits_the_context_budget():
    t, ids = rich_corpus(4, candidates=71)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    for number in (1, 2):
        assert save_section(t, number, ids)["saved"]
    state = t.state
    large_history(state)
    # Left by the interrupted real run: an old comparison focus and the last section touched.
    state["argumentation_focus"] = {"stage": "comparison", "article_ids": ids, "claim_offsets": {}}
    tasks = state["scientific_reading"]["tasks"]
    assert all(task["status"] == "partial" and not task["exhausted"] and task["core_covered"] for task in tasks)
    before = {s["id"]: copy.deepcopy(s["drafts"]) for s in state["outline"]["sections"][:2]}
    cards = {aid: copy.deepcopy(t.registry[aid].analysis) for aid in ids}
    state["messages"].append({"id": "request_a", "role": "user", "content": REQUEST_A})
    log = []
    result = agent(Client(Model(ids, log))).run(state)
    assert not result["partial"], (result["reason"], result["writing_progress"])
    assert result["writing_progress"]["status"] == "draft_complete"
    sections = state["outline"]["sections"]
    assert {s["id"]: s["drafts"] for s in sections[:2]} == before
    assert all(len(s["drafts"]) == 1 for s in sections)
    # Only drafting requests: no stale comparison, no re-reading of the four works.
    assert [entry["task"] for entry in log] == ["section"]*5
    for entry in log:
        assert entry["size"] <= CONFIG.max_context_chars
        memory = entry["memory"]
        assert len(json.dumps(memory, ensure_ascii=False)) <= 18000
        assert not {"argumentation_focus", "candidate_index", "writing_context", "user_message_sources"} & set(memory)
        assert memory["section_drafting"]["packet"]["articles"] and memory["section_drafting"]["packet"]["evidence"]
        assert "sans modifier celles qui sont terminées" in json.dumps(memory["consultant_instructions"], ensure_ascii=False)
    request = log[0]["memory"]
    # Durable cards are untouched by the bounded transmission.
    from agents.EnnoScholarV4.store import ArticleStore
    stored = ArticleStore(state, CONFIG.max_articles).registry
    assert {aid: stored[aid].analysis for aid in ids} == cards
    assert request["section_drafting"]["packet"]["articles"][0]["analysis_available"]["results"] == 12


def test_a_reproduces_the_old_failure_when_budgets_are_enforced_strictly():
    """The context of each task stays far below the request limit even with the
    real-run ingredients that previously produced 37-40k characters."""
    t, ids = rich_corpus(4, candidates=71)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    save_section(t, 1, ids); save_section(t, 2, ids)
    large_history(t.state)
    t.state["messages"].append({"id": "request_a", "role": "user", "content": REQUEST_A})
    t.state["document"]["active_section_id"] = t.state["outline"]["sections"][1]["id"]
    begin_writing(t.state, REQUEST_A)
    section = t.state["outline"]["sections"][2]
    t.state["writing_focus"] = {"section_id": section["id"], "article_ids": ids}
    t.state["argumentation_focus"] = {"stage": "comparison", "article_ids": ids, "claim_offsets": {}}
    # The orchestrator never leaves both foci; even then the section task wins
    # its own context instead of stacking a stale comparison packet on it.
    t.state.pop("argumentation_focus")
    for budget in (18000, 12000, 8000):
        inputs = model_context(t.state, t, budget)
        assert len(json.dumps(inputs, ensure_ascii=False)) <= budget
        assert memory_of({"input": inputs})["section_drafting"]["packet"]["evidence"]


def test_b_plan_then_full_draft_in_one_request_stays_unapproved():
    t, ids = rich_corpus(3, passages=24, read_windows=3)
    state = t.state
    state["messages"] = [{"id": "request_b", "role": "user",
        "content": "Utilise les publications retenues, propose un plan puis rédige l'état de l'art complet."}]
    log = []
    result = agent(Client(Model(ids, log))).run(state)
    assert not result["partial"], (result["reason"], result["writing_progress"])
    tasks = [entry["task"] for entry in log]
    # Comparison, blueprint, plan and every section, chained without the consultant.
    assert tasks.index("planning") < tasks.index("section")
    assert "argumentation" in tasks and tasks.count("section") == 3
    assert result["writing_progress"]["status"] == "draft_complete"
    assert result["writing_progress"]["approval_required"] is True
    assert "approval" not in state["outline"] and state["outline"]["status"] != "approved"
    assert "validation humaine" not in result["answer"] and "approbation finale" in result["answer"]
    assert all(len(section["drafts"]) == 1 for section in state["outline"]["sections"])
    assert all(entry["size"] <= CONFIG.max_context_chars for entry in log)


def test_c_bounded_contexts_never_reduce_permanent_cards_and_details_stay_retrievable():
    t, ids = rich_corpus(4, candidates=20)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    before_cards = {aid: copy.deepcopy(t.registry[aid].analysis) for aid in ids}
    before_evidence = copy.deepcopy(t.evidence)
    section = t.state["outline"]["sections"][3]
    t.state["writing_focus"] = {"section_id": section["id"], "article_ids": ids}
    inputs = model_context(t.state, t, 9000)
    packet = memory_of({"input": inputs})["section_drafting"]["packet"]
    assert len(json.dumps(inputs, ensure_ascii=False)) <= 9000
    # The partial view announces what exists.
    sent = json.dumps(packet, ensure_ascii=False)
    first = next(a for a in packet["articles"])
    assert first["analysis_available"]["technical_mechanism"] == 8 and first["analysis_available"]["results"] == 12
    assert first["analysis_available"]["limitations"] == 5 and first["analysis_available"]["tables_figures"] == 3
    # Whole claims only: every transmitted statement is a complete stored statement.
    stored = {c["statement"] for aid in ids for v in t.registry[aid].analysis.values() if isinstance(v, list)
        for c in v if isinstance(c, dict) and "statement" in c}
    for article in packet["articles"]:
        for value in article["analysis"].values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                assert all(claim["statement"] in stored for claim in value)
    # Lossless, deterministic retrieval by identifiers: every claim, number and proof.
    for aid in ids:
        page, offset, claims, proofs = None, 0, [], {}
        while page is None or not page["complete"]:
            page = execute(t, "get_analysis_claims", article_id=aid, fields=None, offset=offset)
            claims.extend(page["claims"])
            proofs.update({e["evidence_id"]: e for e in page["evidence"]})
            offset = page["next_offset"]
        card = t.registry[aid].analysis
        assert len(claims) == sum(len(card[f]) for f in LAYOUT) == 32
        assert {c["statement"] for c in claims} == {c["statement"] for f in LAYOUT for c in card[f]}
        # Exact proof locators; the quotation itself stays stored and is re-read by id.
        assert all(proofs[e]["evidence_id"] == e and proofs[e]["exact_quote_stored"] for c in claims for e in c["evidence_ids"])
    results, offset = [], 0
    while True:
        page = execute(t, "get_analysis_claims", article_id=ids[2], fields=["results"], offset=offset)
        results.extend(page["claims"]); offset = page["next_offset"]
        if page["complete"]:
            break
    assert [c["statement"] for c in results] == [c["statement"] for c in t.registry[ids[2]].analysis["results"]]
    assert len(results) == 12 and all(re.search(r"0\.\d{3}", c["statement"]) for c in results)
    # Known evidence re-read exactly; an unread passage found by targeted search.
    eid = proofs_of(t, ids[1])[-1]
    exact = execute(t, "analyze_article", article_ids=[ids[1]], action="read", research_context="détail", passage_query="",
        passage_offset=0, passages_per_article=1, analysis=None, evidence_ids=[eid])
    assert eid in json.dumps(exact, ensure_ascii=False)
    found = execute(t, "analyze_article", article_ids=[ids[1]], action="read", research_context="détail",
        passage_query="benchmark B57 protocol P57 hexahedra", passage_offset=0, passages_per_article=2, analysis=None)
    assert "benchmark B57" in json.dumps(found, ensure_ascii=False)
    for task_state in ({"argumentation_focus": {"stage": "comparison", "article_ids": ids, "claim_offsets": {}}},
            {"writing_planning": True}, {}):
        t.state.pop("writing_focus", None)
        t.state.update(task_state)
        model_context(t.state, t, 6000)
        model_context(t.state, t, 6000, final=True)
        for key in task_state:
            t.state.pop(key)
    assert {aid: t.registry[aid].analysis for aid in ids} == before_cards
    assert all(t.evidence[e] == before_evidence[e] for e in before_evidence)
    assert sent  # the bounded view was really produced


def test_d_consultant_instructions_scope_status_and_separation_from_facts():
    t, ids = rich_corpus(2, passages=24, read_windows=3)
    seven_section_plan(t, ids)
    state = t.state
    facts_before = copy.deepcopy({aid: t.registry[aid].analysis for aid in ids})
    messages = [
        ("m1", "Concentre-toi uniquement sur les maillages volumiques."),
        ("m2", "Ne modifie pas les deux premières sections."),
        ("m3", "Dans la section 3, insiste davantage sur les résultats numériques."),
        ("m4", "Finalement, pour la section 3, mets plutôt l'accent sur les limites expérimentales."),
    ]
    for mid, text in messages:
        state.setdefault("messages", []).append({"id": mid, "role": "user", "content": text})
        capture_instructions(state, state["messages"][-1], t.registry)
    register = {row["source_message_id"]: row for row in state["consultant_instructions"]}
    assert register["m1"]["scope"] == "project" and register["m1"]["kind"] == "decision"
    assert register["m2"]["scope"] == "section" and register["m2"]["target_ids"] == [s["id"] for s in state["outline"]["sections"][:2]]
    assert register["m3"]["status"] == "superseded" and register["m3"]["superseded_by"] == register["m4"]["id"]
    assert register["m4"]["status"] == "active" and register["m4"]["text"] == messages[3][1]
    assert [register[m]["order"] for m in ("m1", "m2", "m3", "m4")] == [1, 2, 3, 4]

    def instructions_for(number):
        section = state["outline"]["sections"][number-1]
        state["writing_focus"] = {"section_id": section["id"], "article_ids": ids}
        memory = memory_of({"input": model_context(state, t, 18000)})
        state.pop("writing_focus")
        return [row["text"] for row in memory.get("consultant_instructions", {}).get("items", [])], memory

    third, memory = instructions_for(3)
    assert third == [messages[0][1], messages[3][1]]
    assert "pas des faits" in memory["consultant_instructions"]["nature"]
    fourth, _ = instructions_for(4)
    assert fourth == [messages[0][1]]
    first, _ = instructions_for(1)
    assert messages[1][1] in first
    # Instructions never enter the scientific records.
    assert {aid: t.registry[aid].analysis for aid in ids} == facts_before
    text = json.dumps([state.get("scientific_relations", []), state.get("project_facts", []), state["evidence"]], ensure_ascii=False)
    assert "résultats numériques" not in text and "maillages volumiques." not in text
    # A repeated capture of the same message adds nothing.
    assert capture_instructions(state, state["messages"][-1], t.registry) == []


def test_e_interrupted_at_three_resumes_at_four_without_touching_saved_sections():
    t, ids = rich_corpus(4)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    state = t.state
    state["messages"] = [{"id": "first", "role": "user", "content": "Rédige tout l'état de l'art complet à partir du plan."}]
    log, events = [], []
    first = agent(Client(Model(ids, log))).run(state, emit=events.append,
        cancelled=lambda: sum(e["tool"] == "writing_progress" and e["status"] == "completed" and bool(e.get("section_id"))
            for e in events) >= 3)
    assert first["partial"] and first["reason"] == "cancelled"
    assert "3/7" in first["answer"] and "interruption" in first["answer"].lower()
    sections = state["outline"]["sections"]
    saved = {s["id"]: copy.deepcopy(s["drafts"]) for s in sections[:3]}
    assert all(saved.values()) and not any(s["drafts"] for s in sections[3:])
    evidence = copy.deepcopy(state["evidence"])
    state["messages"].append({"id": "resume", "role": "user", "content": "Reprends la rédaction des sections restantes."})
    log.clear()
    resumed = agent(Client(Model(ids, log))).run(state)
    assert not resumed["partial"], resumed["reason"]
    assert log[0]["memory"]["section_drafting"]["focus"]["section_id"] == sections[3]["id"]
    assert [entry["memory"]["section_drafting"]["focus"]["section_id"] for entry in log] == [s["id"] for s in sections[3:]]
    assert {s["id"]: s["drafts"] for s in sections[:3]} == saved
    assert all(state["evidence"][e] == evidence[e] for e in evidence)
    assert all(len(s["drafts"]) == 1 for s in sections)


def test_f_large_corpus_keeps_every_record_while_each_task_context_stays_bounded():
    t, ids = rich_corpus(24, passages=32, read_windows=4, candidates=300)
    groups = [ids[k:k+4] for k in range(0, 24, 4)] + [ids[:4]]
    seven_section_plan(t, ids, groups)
    seed_argumentation(t, ids)
    state = t.state
    large_history(state)
    state["messages"].append({"id": "request_f", "role": "user", "content": REQUEST_A})
    assert len(t.registry) == 324
    contexts = {}
    section = state["outline"]["sections"][4]
    for task_state in ({"writing_focus": {"section_id": section["id"], "article_ids": section["article_ids"]}},
            {"argumentation_focus": {"stage": "comparison", "article_ids": ids[:4], "claim_offsets": {}}},
            {"writing_planning": True}, {"selection_focus": {"candidate_ids": list(t.registry)[24:48], "presented_total": 24}}, {}):
        state.update(task_state)
        task = context_task(state)
        inputs = model_context(state, t, 18000)
        contexts[task] = memory_of({"input": inputs})
        assert len(json.dumps(inputs, ensure_ascii=False)) <= 18000, task
        for key in task_state:
            state.pop(key)
    assert set(contexts) == {"section", "argumentation", "planning", "selection", "conversation"}
    packet = contexts["section"]["section_drafting"]["packet"]
    assert {a["id"] for a in packet["articles"]} <= set(section["article_ids"])
    assert "candidate_index" not in contexts["section"]
    # A whole drafting run on the large corpus: each request bounded, every section saved.
    log = []
    result = agent(Client(Model(ids, log))).run(state)
    assert not result["partial"], (result["reason"], result["writing_progress"])
    assert all(entry["size"] <= CONFIG.max_context_chars for entry in log)
    assert max(len(json.dumps(entry["memory"], ensure_ascii=False)) for entry in log) <= 18000
    # Nothing permanent was dropped: all notices, cards and proofs remain retrievable.
    assert len(t.registry) == 324 and len(state["evidence"]) >= 24*32
    for aid in (ids[0], ids[-1]):
        claims, proofs, pages = all_claims(t, aid, ["results", "limitations"])
        assert len(claims) == 17 and pages >= 1 and all(e in proofs for c in claims for e in c["evidence_ids"])


def test_g_comparison_of_detailed_works_takes_bounded_windows_with_exact_proof_references():
    from agents.EnnoScholarV4.agent import ARGUMENT_WINDOWS
    # Real cards: long exact quotations (about 1 200 characters) behind every claim.
    t, ids = rich_corpus(4, passages=60, read_windows=4, pad=11)
    lengths = sorted(len(t.evidence[e]["exact_quote"]) for e in t.shown_evidence)
    assert lengths[len(lengths)//2] >= 1000
    seven_section_plan(t, ids)
    state = t.state
    state["messages"] = [{"id": "g", "role": "user", "content": REQUEST_A}]
    cards = {aid: copy.deepcopy(t.registry[aid].analysis) for aid in ids}
    log = []
    result = agent(Client(Model(ids, log))).run(state)
    assert not result["partial"], (result["reason"], result["writing_progress"])
    comparisons = [e for e in log if e["forced"] == "compare_articles"]
    assert 1 <= len(comparisons) <= ARGUMENT_WINDOWS
    for entry in comparisons:
        packet = entry["memory"]["argumentation_focus"]["packet"]
        # Several works per window (an omitted one comes first in the next window),
        # with whole claims and exact proof locators.
        assert len(packet["papers"]) >= 3 and set(packet["omitted_article_ids"]) | {p["id"] for p in packet["papers"]} == set(ids)
        # Several whole claims per work and per window (was about one per work with full quotations).
        assert all(sum(len(v) for v in p["analysis"].values() if isinstance(v, list)) >= 4 for p in packet["papers"])
        proofs = [proof for p in packet["papers"] for proof in p["proofs"]]
        assert proofs and all(proof["evidence_id"] in t.evidence and "exact_quote" not in proof for proof in proofs)
    assert {p["id"] for e in comparisons for p in e["memory"]["argumentation_focus"]["packet"]["papers"]} == set(ids)
    receipt = next(iter(state["comparison_reading"].values()))
    assert receipt["complete"] or receipt["coverage"] == "prioritized_windows"
    # The saved comparison is not repeated in the next request.
    later = log[log.index(comparisons[-1])+1]
    assert later["tool_outputs"] and all('"packet"' not in output and len(output) < 4000 for output in later["tool_outputs"])
    assert all(entry["size"] <= 30000 for entry in log)
    assert result["writing_progress"]["status"] == "draft_complete"
    from agents.EnnoScholarV4.store import ArticleStore
    assert {aid: ArticleStore(state, CONFIG.max_articles).registry[aid].analysis for aid in ids} == cards
