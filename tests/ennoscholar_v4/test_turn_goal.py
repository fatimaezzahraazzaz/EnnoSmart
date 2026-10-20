"""Offline reproduction of run 5768bedd (session ed686234) and its corrections.

The real run: a linked project with four kept locks, the message « donne moi le plan
pour rediger mon etat de l'art avec les verrou et pour chaque paragraphe cherche les
artcile pertinaat ». Searches mostly covered two locks, two full-text analyses of
~145 s each consumed the time budget and the final synthesis was cut by the 600 s
deadline: a fallback summary instead of the plan. No network, no OpenAI call.
"""
import copy
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4 import agent as module
from agents.EnnoScholarV4.agent import ScholarChatAgent
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.context import model_context
from agents.EnnoScholarV4.diagnostic_link import make_link, active_packet, LINKED_GUIDANCE
from agents.EnnoScholarV4.toolkit import ScholarTools
from agents.EnnoScholarV4.turn import lock_coverage
from tests.ennoscholar_v4.test_finalization import Stream, call
from tests.ennoscholar_v4.test_context_memory import memory_of
from tests.ennoscholar_v4.test_scientific_reading import article, analysis_from_packet

REAL_REQUEST = ("donne moi le plan pour rediger mon etat de l'art avec les verrou et pour chaque paragraphe "
    "cherche les artcile pertinaat")
LOCKS = {
    "1": "Les modèles appris de segmentation restent sensibles à l'hétérogénéité des données et au bruit des annotations.",
    "2": "La conversion des masques en polygones donne une topologie fragile, sensible aux seuils et au mode d'agrégation.",
    "3": "Le changement d'architecture vers SegArchA/SegArchB n'a pas permis d'obtenir un entraînement suffisamment stable.",
    "4": "La méthode géométrique avec lissage par programmation dynamique présente un biais systématique des contours dans les zones ambiguës.",
}
# As in the real diagnostic: the project's technical facts are not linked to a lock.
GENERAL = [
    ("method", "Lissage par programmation dynamique des contours détectés, avec contrainte de cohérence entre bords."),
    ("result", "Biais systématique des contours observé dans les zones ambiguës."),
    ("parameter", "Seuil de binarisation 0,5 et agrégation des masques par union avant polygonisation."),
    ("result", "Instabilité de l'entraînement SegArchB : pertes de validation oscillantes après adaptation de SegArchA."),
    ("demarche", "Constitution d'une base annotée interne (120 km, 15 000 images) pour réduire l'hétérogénéité des données."),
    ("result", "Polygones avec auto-intersections nécessitant un nettoyage topologique fréquent."),
]


def linked_state(message=REAL_REQUEST, active=None):
    facts = [{"id": f"lock_entity_{lid}", "kind": "lock", "statement": title, "evidence_ids": [], "source_path": "entities",
        "source": {}} for lid, title in LOCKS.items()]
    facts += [{"id": f"ent_{i}", "kind": kind, "statement": text, "evidence_ids": [], "source_path": f"entities[{i}]",
        "source": {"needs_review": False, "metadata": {}}} for i, (kind, text) in enumerate(GENERAL)]
    snapshot = {"project": {"id": 2, "name": "Chaussées", "organisme": "Client", "year": "2025", "subproject_name": None},
        "diagnostic_id": 1, "diagnostic_status": "completed", "diagnostic_date": "2026-10-17T00:00:00",
        "locks": [{"id": lid, "title": title, "decision": "garde", "justification": "Décision consultant.", "source": {}}
            for lid, title in LOCKS.items()],
        "synthesis": {"main_objective": "Vectoriser de façon robuste les éléments routiers depuis LiDAR et orthophotographies."},
        "facts": facts, "evidence": {}, "context_sections": {}, "families": [], "rnd_approach": {}, "temporal_context": {},
        "source_version": "v1", "normalization_version": 2}
    link = make_link(snapshot, list(LOCKS))
    if active:
        link["active_lock_ids"] = active
    return {"mode": "diagnostic_linked", "project_id": 2, "diagnostic_link": link, "articles": {},
        "scientific_scope": {"topic": "Segmentation et vectorisation des chaussées", "work_type": "state_of_art"},
        "messages": [{"id": "msg_plan", "role": "user", "content": message}]}


PAPERS = {lid: article(title, f"10.1234/lock-{lid}", "roads", method) for lid, (title, method) in {
    "1": ("Domain adaptive road segmentation with noisy labels", "Noise-robust domain adaptation"),
    "2": ("Topology-preserving polygonization of road masks", "Topological polygon regularization"),
    "3": ("Stable training of high-resolution segmentation networks", "SegArchB training stabilization"),
    "4": ("Hidden Markov map matching of road boundaries", "HMM boundary smoothing"),
}.items()}
for lid, paper in PAPERS.items():
    paper.id = f"paper_lock_{lid}"


def sources():
    mock = Mock()
    mock.last_provider_stats = None
    mock.verify.side_effect = lambda a, deadline: a.model_copy(update={"verified": True})
    def search(query, *args, **kwargs):
        lid = query.split()[-1]
        return ([PAPERS[lid].model_copy(deep=True)] if lid in PAPERS else [], [])
    mock.search.side_effect = search
    return mock


class TimedClient:
    """Fake Responses client whose calls take the measured real durations."""
    def __init__(self, callback, clock, goal=None):
        self.callback, self.clock, self.calls, self.goal, self.understanding = callback, clock, [], goal, []
    def with_options(self, **kw): return self
    @property
    def responses(self): return self
    def create(self, **kw):
        if kw.get("tool_choice") == {"type": "function", "name": "set_turn_goal"}:
            # The short understanding call (5 s measured scale), answered with this test's goal.
            self.understanding.append(kw)
            self.clock[0] += 5
            output = call("set_turn_goal", self.goal, "goal") if self.goal else []
            return Stream(NS(output=output, output_text="", status="completed", usage={"input_tokens": 3000, "output_tokens": 200}))
        self.calls.append(copy.deepcopy(kw))
        output, seconds, out_tokens = self.callback(len(self.calls), kw)
        self.clock[0] += seconds
        return Stream(NS(output=output, output_text="", status="completed", usage={"input_tokens": 100, "output_tokens": out_tokens}))


def calls(*items):
    return [row for i, (name, args) in enumerate(items) for row in call(name, args, f"{i}")]


def search(lid):
    return ("search_scientific", {"query": f"road boundary method lock {lid}", "sources": None, "limit_per_source": 10,
        "offset": 0, "family": f"verrou {lid}", "lock_ids": [lid]})


def goal(deliverable="plan", depth="core", **extra):
    return ("set_turn_goal", {"deliverable": deliverable, "lock_ids": [], "focus_lock_ids": [], "exact_quote": "",
        "reading_depth": depth, "understanding": "Plan d'état de l'art par verrou, avec les publications de chaque paragraphe.", **extra})


def stored(state, lid):
    """Identifier given by the store to the work found for this lock."""
    return next((aid for aid, row in state["articles"].items() if row.get("doi") == f"10.1234/lock-{lid}"), f"paper_lock_{lid}")


def plan_sections(lock_ids=("1", "2", "3", "4"), state=None):
    ref = (lambda lid: stored(state, lid)) if state else (lambda lid: f"paper_lock_{lid}")
    return [{"title": f"Verrou {lid} : état des méthodes", "description": "Argument de la section.", "article_ids":
        [ref(lid)] if lid != "4" else [], "scientific_objective": f"Quelle méthode répond au verrou {lid} ?",
        "questions": [], "article_roles": [{"article_id": ref(lid), "role": "Méthode de référence"}] if lid != "4" else [],
        "relation_ids": [], "experimental_results": ["Métrique M = 84.7 sur le jeu Q (à confirmer)."], "limitations": [],
        "evidence_ids": [], "missing_information": ["Aucune publication pertinente retenue"] if lid == "4" else [],
        "project_connection": "Comparer aux essais du projet (contexte général, non rattaché au verrou).", "figure_suggestions": [],
        "lock_ids": [lid]} for lid in lock_ids]


def outline(action="create", sections=None, message="Plan provisoire : le verrou 4 reste sans publication pertinente.", state=None):
    return ("manage_outline", {"action": action, "title": "État de l'art — chaussées", "section_id": None, "target_section_id": None,
        "position": None, "sections": sections or plan_sections(state=state), "article_ids": None, "offset": 0, "limit": 8,
        "consultant_message": message})


def part_payload(kw, state, note="Plan provisoire : le verrou 4 reste sans publication pertinente.", n="p"):
    """A plan part, as the model writes it for its lock (or the framing part)."""
    part = memory_of(kw)["plan_part"]
    if part["kind"] == "framing":
        frame = lambda title: {**plan_sections(("1",), state)[0], "title": title, "article_ids": [], "article_roles": [],
            "lock_ids": [], "missing_information": [], "scientific_objective": ""}
        return call("propose_plan_sections", {"sections": [frame("Introduction : chaîne de vectorisation des chaussées")],
            "closing_sections": [frame("Synthèse et perspectives")], "consultant_note": note}, n)
    return call("propose_plan_sections", {"sections": plan_sections(tuple(part["lock_ids"]), state), "closing_sections": [],
        "consultant_note": ""}, n)


def test_real_plan_request_produces_the_plan_with_every_lock_within_the_deadline(monkeypatch):
    clock = [0.]
    monkeypatch.setattr(module, "time", NS(monotonic=lambda: clock[0]))
    state = linked_state()
    trace, conversations = [], []
    def respond(n, kw):
        memory = memory_of(kw)
        names = [t.get("name") for t in kw["tools"]]
        choice = kw["tool_choice"]
        if choice == {"type": "function", "name": "analyze_article"}:
            raise AssertionError("A plan at reading_depth=core reads no full text")
        if choice == {"type": "function", "name": "propose_plan_sections"}:
            # The plan is generated by part (framing + one per lock), not diverted to a project re-read.
            assert names == ["propose_plan_sections"] and memory["context_task"] == "planning"
            # A lock's part receives its own sources (none for lock 4) and every other one as a reference.
            assert "plan_sources" in memory and memory["lock_coverage"]
            if memory["plan_part"]["kind"] == "lock":
                assert memory["plan_sources"] or memory["other_plan_sources"]
            trace.append(("plan", memory["plan_part"]["kind"], clock[0]))
            return part_payload(kw, state, n=n), 30, 1200
        if choice == {"type": "function", "name": "record_candidate_decisions"}:
            trace.append(("selection", n, clock[0]))
            decisions = [{"article_id": stored(state, lid), "decision": "retain" if lid != "4" else "reject",
                "justification": "Contribution pertinente" if lid != "4" else "Hors sujet", "lock_ids": [lid]} for lid in LOCKS]
            return calls(("record_candidate_decisions", {"decisions": decisions, "finalist_ids": [stored(state, lid) for lid in "123"],
                "covered_families": [], "remaining_axes": []})), 15, 600
        conversations.append(n)
        if len(conversations) == 1:
            # The model sees the project's concrete difficulties and no unusable tool.
            assert "set_turn_goal" in names and "write_section" not in names and "compare_articles" not in names
            technical = json.dumps(memory["diagnostic_context"].get("project_technical_context"), ensure_ascii=False)
            assert "programmation dynamique" in technical and "SegArchB" in technical
            assert memory["diagnostic_context"]["technical_context_note"]
            trace.append(("conversation", n, clock[0]))
            return calls(goal(), search("1"), search("2")), 15, 400
        if len(conversations) == 2:
            # The orchestrator names the locks not searched yet; no selection before they are.
            assert "['3', '4']" in kw["input"][-1]["content"]
            assert [r["status"] for r in memory["lock_coverage"]["locks"]][2:] == ["not_searched", "not_searched"]
            return calls(search("3"), search("4")), 15, 400
        # One gap round for the lock still without a retained work, then the plan.
        assert "Gap round" in kw["input"][-1]["content"] and "['4']" in kw["input"][-1]["content"]
        trace.append(("gap", n, clock[0]))
        return calls(("search_scientific", {**search("4")[1], "query": "road boundary hidden Markov smoothing lock 4"})), 15, 300
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=TimedClient(respond, clock),
        sources=sources()).run(state)
    assert not result["partial"] and not result["fallback"], result["reason"]
    assert result["tool_counts"].get("finalize_response") is None and not result["tool_counts"].get("analyze_article")
    kinds = [row[0] for row in trace]
    # The gap search brings nothing new: no second selection, the plan follows.
    assert kinds[:4] == ["conversation", "selection", "gap", "plan"] and kinds.count("selection") == 1
    assert sorted(row[1] for row in trace if row[0] == "plan") == ["framing", "lock", "lock", "lock", "lock"]
    assert clock[0] < ScholarV4Config().timeout_seconds - 100
    answer = result["answer"]
    assert "plan proposé" in answer and "Couverture des verrous" in answer and "Verrou 4" in answer
    assert "aucune publication pertinente retenue" in answer and "Plan provisoire" in answer
    assert state["outline"]["status"] == "proposed"
    assert [s["lock_ids"] for s in state["outline"]["sections"]] == [[], ["1"], ["2"], ["3"], ["4"], []]
    assert not state.get("document", {}).get("writing_progress")
    from agents.EnnoScholarV4.models import Article
    registry = {aid: Article.model_construct(**row) for aid, row in state["articles"].items()}
    coverage = {row["lock_id"]: row["status"] for row in lock_coverage(state, registry)["locks"]}
    assert coverage == {"1": "candidates_retained", "2": "candidates_retained", "3": "candidates_retained",
        "4": "searched_no_relevant_result"}


def test_natural_scope_change_is_understood_by_the_model_and_quoted():
    state = linked_state("Je préfère commencer par le deuxième.")
    def respond(n, kw):
        if n == 1:
            return call("set_turn_goal", {**goal("literature_search", "abstracts")[1], "focus_lock_ids": ["2"],
                "exact_quote": "Je préfère commencer par le deuxième"}, n)
        assert memory_of(kw)["diagnostic_context"]["active_lock_ids"] == ["2"]
        return call("finalize_response", {"answer": "Nous travaillons maintenant sur le verrou de polygonisation.", "article_ids": [],
            "evidence_ids": [], "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None,
            "displayed_article_ids": None}, n)
    from tests.ennoscholar_v4.test_finalization import Client
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=Client(respond), sources=sources()).run(state)
    assert result["publication_accepted"]
    link = state["diagnostic_link"]
    assert link["active_lock_ids"] == ["2"] and link["history"][-1]["origin"] == "consultant_message"
    assert link["history"][-1]["exact_quote"] == "Je préfère commencer par le deuxième"


def test_scope_change_requires_the_consultant_words_and_selected_locks():
    state = linked_state("Et les autres verrous ?")
    tools = ScholarTools(state, None, ScholarV4Config(api_key="mock"))
    with pytest.raises(ValueError, match="mot pour mot"):
        tools.execute("set_turn_goal", {**goal()[1], "focus_lock_ids": ["3"], "exact_quote": "passe au verrou 3"}, 1e12)
    with pytest.raises(ValueError, match="hors de la sélection"):
        tools.execute("set_turn_goal", {**goal()[1], "focus_lock_ids": ["9"], "exact_quote": "Et les autres verrous"}, 1e12)
    assert state["diagnostic_link"]["active_lock_ids"] == list(LOCKS)
    express = {"articles": {}, "messages": [{"role": "user", "content": "Trouve des articles"}]}
    with pytest.raises(ValueError, match="Express"):
        ScholarTools(express, None, ScholarV4Config(api_key="mock")).execute("set_turn_goal", {**goal()[1], "lock_ids": ["1"]}, 1e12)


def test_plan_revision_reads_the_message_before_any_leftover_reading():
    state = linked_state("Ce plan ne me convainc pas, améliore-le.")
    paper = PAPERS["1"].model_copy(deep=True)
    paper.selected = True
    state["articles"] = {paper.id: paper.durable(), PAPERS["2"].id: PAPERS["2"].durable()}
    tools = ScholarTools(state, None, ScholarV4Config(api_key="mock"))
    from agents.EnnoScholarV4.tools.outline import OutlineArgs, manage_outline
    manage_outline(state, OutlineArgs.model_validate(dict(outline(sections=plan_sections(("1",)))[1])), tools.registry)
    seen = []
    def respond(n, kw):
        seen.append(kw["tool_choice"])
        assert n == 1, "no reading, comparison or synthesis round is needed"
        # The unread retained work (stored state_of_art scope) does not preempt the request.
        assert "automatic_scientific_reading" not in memory_of(kw)
        return calls(goal("plan_revision", "none"), outline("replace", plan_sections(("2", "1")), "J'ai inversé l'ordre pour partir de la topologie.")), 1, 1500
    from tests.ennoscholar_v4.test_finalization import Client
    clock = [0.]
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=TimedClient(respond, clock),
        sources=sources()).run(state)
    assert seen == ["auto"] and not result["partial"]
    assert result["answer"].startswith("J'ai inversé l'ordre") and "### 1. Verrou 2" in result["answer"]
    assert len(state["outline_versions"]) == 2 and state["outline"]["status"] == "proposed"
    assert not result["tool_counts"].get("analyze_article")


def test_replace_keeps_drafted_plans_intact():
    state = linked_state()
    tools = ScholarTools(state, None, ScholarV4Config(api_key="mock"))
    from agents.EnnoScholarV4.tools.outline import OutlineArgs, manage_outline
    state["articles"] = {p.id: p.durable() for p in PAPERS.values()}
    tools = ScholarTools(state, None, ScholarV4Config(api_key="mock"))
    manage_outline(state, OutlineArgs.model_validate(dict(outline()[1])), tools.registry)
    state["outline"]["sections"][0]["drafts"] = [{"id": "draft_1", "text": "Texte"}]
    with pytest.raises(ValueError, match="brouillons"):
        manage_outline(state, OutlineArgs.model_validate(dict(outline("replace")[1])), tools.registry)
    with pytest.raises(ValueError, match="Verrous inconnus"):
        manage_outline(state, OutlineArgs.model_validate({**outline("add")[1], "sections": plan_sections(("7",))}), tools.registry)


def test_plan_request_never_opens_a_draft_and_defers_a_synthesis_to_the_plan():
    state = linked_state()
    state["articles"] = {p.id: p.durable() for p in PAPERS.values()}
    order = []
    def respond(n, kw):
        if n == 1:
            order.append("goal+finalize")
            return calls(goal("plan", "abstracts"), ("finalize_response", {"answer": "Plan : 1. ...", "article_ids": [], "evidence_ids": [],
                "coverage_sufficient": False, "remaining_uncertainties": [], "analyses": None, "displayed_article_ids": None})), 1, 300
        order.append(kw["tool_choice"])
        return part_payload(kw, state, n=n), 1, 1500
    clock = [0.]
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=TimedClient(respond, clock),
        sources=sources()).run(state)
    # The deferred synthesis is replaced by the plan, generated by part (framing + 4 locks).
    assert order == ["goal+finalize", *[{"type": "function", "name": "propose_plan_sections"}]*5] and not result["partial"]
    assert "Couverture des verrous" in result["answer"] and not state.get("document", {}).get("writing_progress")
    assert result["tool_counts"]["finalize_response"] == 1 and not result["publication_accepted"]


def test_project_retrieval_for_the_model_omits_raw_diagnostic_payloads():
    state = linked_state()
    state["diagnostic_link"]["snapshot"]["facts"][5]["source"]["metadata"] = {"temporal_status": "performed", "raw": "x"*5000}
    state["diagnostic_link"]["snapshot"]["locks"][0]["source"] = {"full_persisted_verrou": {"text": "y"*20000}}
    tools = ScholarTools(state, None, ScholarV4Config(api_key="mock"))
    facts = tools.execute("get_project_context", {"kind": "facts", "scope": "general", "offset": 0, "limit": 10, "ids": None}, 1e12)
    locks = tools.execute("get_project_context", {"kind": "locks", "scope": "active", "offset": 0, "limit": 10, "ids": None}, 1e12)
    assert all("source" not in row for row in facts["items"]) and len(json.dumps(locks, ensure_ascii=False)) < 3000
    assert any(row.get("temporal_status") == "performed" for row in facts["items"])
    # The stored snapshot is unchanged.
    assert "full_persisted_verrou" in state["diagnostic_link"]["snapshot"]["locks"][0]["source"]


def test_express_conversation_offers_the_goal_tool_but_no_tool_without_its_input():
    state = {"articles": {}, "messages": [{"role": "user", "content": "Trouve-moi des articles sur la segmentation de routes."}]}
    seen = {}
    def respond(n, kw):
        seen["names"] = [t.get("name") for t in kw["tools"]]
        return [{"type": "message", "content": [{"type": "output_text", "text": "Bien sûr.", "annotations": []}]}]
    from tests.ennoscholar_v4.test_finalization import Client
    class TextClient(Client):
        def create(self, **kw):
            self.calls.append(kw)
            output = self.callback(len(self.calls), kw)
            return Stream(NS(output=output, output_text="Bien sûr.", status="completed", usage={"input_tokens": 1, "output_tokens": 1}))
    result = ScholarChatAgent(ScholarV4Config(api_key="mock", web_search_enabled=False), client=TextClient(respond), sources=sources()).run(state)
    assert result["answer"] == "Bien sûr."
    assert "set_turn_goal" in seen["names"] and "get_project_context" not in seen["names"]
    assert not {"write_section", "review_document", "create_scientific_visual", "compare_articles", "get_analysis_claims"} & set(seen["names"])


REAL_DB = Path(__file__).resolve().parents[2] / "ennoscholar_v4_test.db"


@pytest.mark.skipif(not REAL_DB.is_file(), reason="local test database absent")
def test_real_session_ed686234_context_now_carries_the_project_difficulties():
    db = sqlite3.connect(f"file:{REAL_DB.as_posix()}?mode=ro", uri=True)
    row = db.execute("select data from scholar_v4_sessions where id like 'ed686234%'").fetchone()
    db.close()
    if not row:
        pytest.skip("session ed686234 absent")
    state = json.loads(row[0])
    before = copy.deepcopy(state["diagnostic_link"])
    tools = ScholarTools(state, None, ScholarV4Config(api_key="mock"))
    # The 3 000-character packet of the real run carried no technical fact (0/256);
    # the same budget now carries the project's methods, parameters and results.
    packet = active_packet(state, min(4200, 18000//4))
    assert len(json.dumps(packet, ensure_ascii=False)) <= 4200
    kinds = {r["kind"] for r in packet["project_technical_context"]}
    assert {"method", "result", "parameter"} <= kinds and len(packet["project_technical_context"]) >= 8
    # A method named in lock 4's own title (read from the base) reaches the technical context.
    lock4 = next(v["title"] for v in state["diagnostic_link"]["snapshot"]["locks"] if v["id"] == "4")
    assert set(re.findall(r"[A-Z][a-z]{4,}", lock4)) & set(re.findall(r"[A-Z][a-z]{4,}",
        json.dumps(packet["project_technical_context"], ensure_ascii=False)))
    assert len(packet["locks"]) == 4 and all(row.get("title") for row in packet["locks"])
    assert all(not row.get("lock_ids") for row in packet["project_technical_context"])
    coverage = lock_coverage(state, tools.registry)
    assert [r["lock_id"] for r in coverage["locks"]] == ["1", "2", "3", "4"]
    assert coverage["searches_without_lock_ids"] == 6
    context = model_context(state, tools, 18000)
    assert len(json.dumps(context, ensure_ascii=False)) <= ScholarV4Config().max_context_chars
    assert state["diagnostic_link"] == before
