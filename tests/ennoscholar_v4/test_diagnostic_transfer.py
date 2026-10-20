import copy
import json
import time

import pytest

from agents.EnnoScholarV4 import diagnostic_link as dl
from agents.EnnoScholarV4.argumentation import argument_packet, bounded_argument_packet


def state_for(items=(), locks=None, sections=None):
    locks = locks or [{"id": "a", "title": "Stabilité thermique", "decision": "garde", "justification": "Humain", "source": {}},
                      {"id": "b", "title": "Qualité visuelle", "decision": "garde", "justification": "Humain", "source": {}}]
    facts, evidence = dl.normalize_records([], list(items), {}, locks, sections or {}, 91)
    snapshot = {"project": {"id": 1, "name": "Projet anonyme", "year": "2025"}, "diagnostic_id": 91,
        "source_version": "fixed_version", "locks": locks, "facts": facts, "evidence": evidence,
        "synthesis": {"main_objective": "Stabiliser le matériau"}, "context_sections": sections or {},
        "rnd_approach": {}, "temporal_context": {}}
    return {"mode": "diagnostic_linked", "diagnostic_link": dl.make_link(snapshot, ["a"]), "articles": {}}


def proof(**fields):
    return {"evidence_id": "E1", "passage_id": "p1", "document_id": "d1", "excerpt": "Mesure exacte à 37.25 °C.", **fields}


@pytest.mark.parametrize("field", ["quote", "excerpt", "text"])
def test_legacy_quote_alias_preserves_passage_and_never_invents_verification(field):
    row = proof()
    row.pop("excerpt")
    row[field] = "Résultat original : 0.75 mm."
    state = state_for([{"item_id": "old", "kind": "result", "statement": "Résultat rapporté", "evidence": row}])
    normalized = next(iter(state["diagnostic_link"]["snapshot"]["evidence"].values()))
    assert normalized["quote"] == "Résultat original : 0.75 mm."
    assert normalized["passage_id"] == "p1" and normalized["quote_verified"] is None
    assert normalized["verification_status"] == "not_established"
    assert not dl.project_facts_view(state)


def test_colliding_old_ids_do_not_overwrite_or_resolve_ambiguous_fact():
    locks = [{"id": lid, "title": lid, "decision": "garde", "justification": "Humain", "source": {"sources": [row]}}
             for lid, row in (("a", proof()), ("b", proof(passage_id="p2", text="Autre valeur", excerpt="Autre mesure 8.5 kPa.")))]
    state = state_for([{"item_id": "unknown", "kind": "result", "statement": "À rattacher", "evidence_ids": ["E1"]}], locks)
    source = state["diagnostic_link"]["snapshot"]
    assert len(source["evidence"]) == 2 and all(eid != "E1" for eid in source["evidence"])
    assert source["facts"][0]["unresolved_evidence_ids"] == ["E1"] and not source["facts"][0]["evidence_ids"]
    assert len(dl.retrieve(state, "evidence", scope="all", ids=["E1"])["items"]) == 2
    active = dl.retrieve(state, "evidence")["items"]
    assert len(active) == 1 and active[0]["quote"] == "Mesure exacte à 37.25 °C."
    # Reordering the source does not change a proof's scoped identity.
    reversed_state = state_for(locks=list(reversed(locks)))
    assert set(reversed_state["diagnostic_link"]["snapshot"]["evidence"]) == set(source["evidence"])


def test_shared_evidence_family_and_source_items_do_not_attribute_methods():
    locks = [{"id": lid, "title": lid, "decision": "garde", "justification": "Humain",
              "source": {"entity_id": "lock_"+lid, "family_id": "same_family", "evidence_ids": ["E1"],
                         "v2_entity": {"source_item_ids": ["method_b"]}}} for lid in ("a", "b")]
    items = [{"item_id": "method_b", "kind": "method", "statement": "Méthode réservée à B", "evidence": proof(),
              "metadata": {"related_lock_ids": ["lock_b"]}},
             {"item_id": "parameter_global", "kind": "parameter", "statement": "37.25 °C", "evidence": proof()}]
    state = state_for(items, locks)
    assert not dl.active_records(state)[1]
    general = dl.retrieve(state, "facts", scope="general")["items"]
    assert [f["id"] for f in general] == ["parameter_global"] and not general[0]["lock_ids"]
    assert "Méthode réservée à B" not in json.dumps(dl.comparison_project_packet(state), ensure_ascii=False)


def test_explicit_unknown_relation_is_not_promoted_to_general_context():
    state = state_for([{"item_id": "unresolved", "kind": "result", "statement": "Ne pas attribuer",
        "metadata": {"related_lock_ids": ["unknown_lock"]}, "evidence": proof()}])
    assert not dl.retrieve(state, "facts", scope="general")["items"]
    assert dl.retrieve(state, "facts", scope="all")["items"][0]["scope"] == "unresolved"


def test_general_method_parameter_and_result_survive_large_comparison_without_automatic_attribution():
    items = [{"item_id": kind, "kind": kind, "statement": kind+" documenté à 37.25 °C.",
              "evidence": proof(evidence_id="E_"+kind, quote_verified=True),
              "temporal_status": "performed", "metadata": {"source_role": "project_evidence"}}
             for kind in ("method", "parameter", "result")]
    state = state_for(items)
    from agents.EnnoScholarV4.toolkit import ScholarTools
    from agents.EnnoScholarV4.config import ScholarV4Config
    tools = ScholarTools(state, None, ScholarV4Config(api_key="mock"))
    original = argument_packet(state, tools, [])
    for _ in range(200):
        original["project_facts"].extend(copy.deepcopy(original["project_facts"][:3]))
    packet = bounded_argument_packet(original, 14000)
    assert {r["category"] for r in packet["project_context"]["observations"]} >= {"method", "parameter", "result"}
    assert packet["project_facts"]
    assert all(f["scope"] == "project_context_not_linked_to_lock" and not f["lock_ids"] for f in packet["project_facts"])
    assert len(json.dumps(packet, ensure_ascii=False)) <= 14000


def test_general_project_confrontation_requires_explicit_uncertainty():
    from tests.ennoscholar_v4.test_context_memory import rich_corpus
    tools, ids = rich_corpus(1)
    source = state_for([{"item_id": "method", "kind": "method", "statement": "Méthode du projet",
        "evidence": proof(quote_verified=True), "metadata": {"source_role": "project_evidence"}}])
    tools.state.update(mode="diagnostic_linked", diagnostic_link=source["diagnostic_link"])
    fact = dl.project_facts_view(tools.state)[0]
    eid = next(eid for eid in tools.shown_evidence if tools.evidence[eid]["article_id"] == ids[0])
    step = {"kind": "project_confrontation", "statement": "Comparaison à vérifier pour le verrou actif", "article_ids": ids,
        "evidence_ids": [eid], "project_fact_ids": [fact["id"]], "uncertainty": ""}
    args = {"action": "save", "merge": False, "global_question": "Q", "narrative_logic": "Comparaison prudente",
        "axes": [{"axis": "Méthode", "scientific_problem": "Q", "section_id": None, "transition": "Suite", "steps": [step]}],
        "remaining_questions": [], "claim_offsets": []}
    with pytest.raises(ValueError, match="général"):
        tools.execute("build_argument_blueprint", args, time.monotonic()+30)
    step["uncertainty"] = "Le lien entre cette méthode générale et le verrou actif n'est pas établi."
    assert tools.execute("build_argument_blueprint", args, time.monotonic()+30)["saved"]


def test_express_comparison_keeps_existing_project_fact_contract():
    from tests.ennoscholar_v4.test_context_memory import rich_corpus
    tools, ids = rich_corpus(1)
    tools.state["messages"] = [{"id": "human", "role": "user", "content": "Notre essai mesurait 37.25 °C."}]
    tools.state["project_facts"] = [{"id": "project_fact_human", "category": "parameter", "statement": "37.25 °C",
        "exact_quote": "Notre essai mesurait 37.25 °C.", "source_type": "consultant_message", "source_id": "human", "source_version": "message:human"}]
    packet = argument_packet(tools.state, tools, ids)
    assert "project_context" not in packet and packet["project_facts"][0]["source_current"] is True
    assert not dl.comparison_project_packet(tools.state)


def test_linked_comparison_preserves_later_consultant_facts():
    from tests.ennoscholar_v4.test_context_memory import rich_corpus
    tools, ids = rich_corpus(1)
    linked = state_for()
    tools.state.update(mode="diagnostic_linked", diagnostic_link=linked["diagnostic_link"])
    tools.state["messages"] = [{"id": "human", "role": "user", "content": "Notre essai mesurait 37.25 °C."}]
    fact = {"id": "project_fact_human", "category": "parameter", "statement": "37.25 °C",
        "exact_quote": "Notre essai mesurait 37.25 °C.", "source_type": "consultant_message", "source_id": "human", "source_version": "message:human"}
    tools.state["project_facts"] = [fact]
    packet = bounded_argument_packet(argument_packet(tools.state, tools, ids), 14000)
    assert packet["project_facts"][0]["exact_quote"] == fact["exact_quote"]


def test_conflicting_verification_flags_never_promote_a_passage():
    state = state_for([{"item_id": "one", "kind": "method", "statement": "Rapporté", "evidence": proof(quote_verified=True)},
                       {"item_id": "two", "kind": "method", "statement": "À vérifier", "evidence": proof(quote_verified=False)}])
    rows = state["diagnostic_link"]["snapshot"]["evidence"]
    assert len(rows) == 1 and next(iter(rows.values()))["quote_verified"] is False
