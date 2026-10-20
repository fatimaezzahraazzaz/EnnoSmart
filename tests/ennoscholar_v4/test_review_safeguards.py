"""Offline adversarial checks of Claude's P0/P1 safeguards; no real session data."""
import copy
import json
from types import SimpleNamespace

import pytest

from agents.EnnoScholarV4.attribution import check_attribution
from agents.EnnoScholarV4.scientific_review import deterministic_review
from tests.ennoscholar_v4.test_attribution import execute, save, two_papers
from tests.ennoscholar_v4.test_context_memory import (
    REQUEST_A, Client, Model, agent, call, rich_corpus, save_section,
    seed_argumentation, seven_section_plan,
)


def test_absent_claims_refuse_save_without_changing_existing_draft_or_cards():
    t, ids, proofs = two_papers()
    eid = proofs[ids[0]]["Our"]
    text = f"Qian et al. produisent un maillage quadrangulaire [{ids[0]}] [{eid}]."
    assert save(t, ids, text, [eid])["saved"]
    before = copy.deepcopy(t.state["outline"])
    cards = {aid: copy.deepcopy(t.registry[aid].analysis) for aid in ids}
    claims = [{"statement": f"Information importante absente {i} : " + "détail " * 40,
        "evidence_ids": [eid], "origin": "inference"} for i in range(7)]
    result = execute(t, "write_section", section_id=before["sections"][0]["id"],
        action="save", instructions="x", text=text, article_ids=ids, evidence_ids=[eid], claims=claims)
    assert result.get("saved") is False
    assert result["claims_not_in_text"] == [c["statement"] for c in claims]
    # Returned exactly as declared (with their typed provenance defaults).
    assert [{k: c[k] for k in ("statement", "evidence_ids", "origin")} for c in result["rejected_claims"]] == claims
    assert all(c["kind"] == "scientific" and c["project_evidence_ids"] == [] for c in result["rejected_claims"])
    assert t.state["outline"] == before
    assert all(t.registry[aid].analysis == cards[aid] for aid in ids)


@pytest.mark.parametrize("name, aid_index, proof_key", [
    ("MeshGraphNets", 1, "Graphnet"), ("Kong et al.", 0, "Our"),
])
def test_a_name_in_another_passage_does_not_support_this_sentence(name, aid_index, proof_key):
    t, ids, proofs = two_papers()
    aid = ids[aid_index]
    text = f"La méthode {name} produit le résultat étudié [{aid}] [{proofs[aid][proof_key]}]."
    assert check_attribution(text, ids, t)
    with pytest.raises(ValueError, match="Attribution non vérifiable"):
        save(t, ids, text, [proofs[aid][proof_key]])


def test_abstract_baseline_name_is_not_the_publication_identity():
    t, ids, proofs = two_papers()
    t.registry[ids[0]].abstract += " We compare to MeshGraphNets."
    text = f"MeshGraphNets produit des quadrangles [{ids[0]}] [{proofs[ids[0]]['Our']}]."
    assert check_attribution(text, ids, t)


def test_comparison_naming_two_works_needs_both_publications_proofs():
    t, ids, proofs = two_papers()
    t.registry[ids[1]].title = "AdaptNet: graph neural networks for adaptation"
    text = f"TemplateNet et AdaptNet utilisent ce mécanisme [{ids[0]}] [{proofs[ids[0]]['Our']}]."
    assert check_attribution(text, ids, t)
    own, metric = proofs[ids[0]]["Our"], proofs[ids[1]]["Graphnet"]
    corrected = f"TemplateNet et AdaptNet diffèrent [{ids[0]}] [{own}] [{ids[1]}] [{metric}]."
    assert save(t, ids, corrected, [own, metric])["saved"]
    base = proofs[ids[1]]["Both"]
    assert save(t, ids, f"AdaptNet utilise l'architecture MeshGraphNets [{ids[1]}] [{base}].", [base])["saved"]


@pytest.mark.parametrize("author, prior, method, component", [
    ("Qian", "Kong", "TemplateNet", "MeshGraphNets"),
    ("Durand", "Martin", "SurfaceFlow", "CellGraphNet"),
])
def test_attribution_rules_apply_to_arbitrary_publication_names(author, prior, method, component):
    article = SimpleNamespace(id="paper_a", title=f"{method}: meshing", abstract=f"We compare to {component}.",
        authors=[f"A. {author}"], year=2025, analysis={})
    toolkit = SimpleNamespace(registry={article.id: article}, shown_evidence=["ev_own", "ev_prior"], evidence={
        "ev_own": {"article_id": article.id, "exact_quote": "Our method produces surface meshes."},
        "ev_prior": {"article_id": article.id, "section": "Related works",
            "exact_quote": f"{prior} et al. use {component} for volume meshes."}})
    for name in (f"{prior} et al.", component):
        assert check_attribution(f"La méthode {name} produit des surfaces [paper_a] [ev_own].", [article.id], toolkit)
    assert not check_attribution(
        f"{author} et al. citent {component} de {prior} et al. [paper_a] [ev_prior].", [article.id], toolkit)


def test_declared_claim_cannot_use_evidence_outside_the_draft():
    t, ids, proofs = two_papers()
    own, prior = proofs[ids[0]]["Our"], proofs[ids[0]]["Kong"]
    text = f"Résultat de surface [{ids[0]}] [{own}]."
    with pytest.raises(ValueError, match="preuves déclarées"):
        execute(t, "write_section", section_id="1", action="save", instructions="x", text=text,
            article_ids=ids, evidence_ids=[own], claims=[
                {"statement": text, "evidence_ids": [prior], "origin": "source_statement"}])
    assert not t.state["outline"]["sections"][0]["drafts"]


@pytest.mark.parametrize("correct_identity", [True, False])
def test_shared_author_is_disambiguated_by_the_cited_publication(correct_identity):
    t, ids, proofs = two_papers()
    t.registry[ids[1]].authors = ["Other Qian"]
    aid = ids[0] if correct_identity else ids[1]
    text = f"Qian et al. produisent des quadrangles [{aid}] [{proofs[ids[0]]['Our']}]."
    assert bool(check_attribution(text, ids, t)) is not correct_identity


def test_legacy_attribution_errors_fail_the_integrity_review_without_rewriting():
    t, ids, proofs = two_papers()
    eid = proofs[ids[0]]["Our"]
    save(t, ids, f"Résultat documenté [{ids[0]}] [{eid}].", [eid])
    draft = t.state["outline"]["sections"][0]["drafts"][-1]
    draft["raw_text"] += " Kong et al. développent le travail étudié."
    before = copy.deepcopy(draft)
    report = deterministic_review(t.state, t)
    assert any(i["code"] == "unsupported_attribution" for i in report["issues"])
    assert not report["integrity_passed"]
    assert draft == before


@pytest.mark.parametrize("mode", ["pages", "articles", "fields"])
def test_more_than_four_distinct_claim_reads_remain_available(mode):
    t, ids = rich_corpus(5 if mode == "articles" else 1)
    seven_section_plan(t, ids)
    for number in range(1, 7):
        save_section(t, number, ids)
    if mode == "pages":
        # Five real pages, with whole claims and source locators in each page.
        rows = t.registry[ids[0]].analysis["results"]
        t.registry[ids[0]].analysis["results"] = [
            {**copy.deepcopy(rows[i % len(rows)]), "statement": f"Claim {i}: " + "detail " * 300}
            for i in range(30)]
        t.save()
    seed_argumentation(t, ids)
    t.state["messages"] = [{"id": "m", "role": "user", "content": REQUEST_A}]
    offset, received, offered = 0, [], []
    fields = ["method", "results", "limitations", "technical_mechanism", "experimental_setup"]

    def model(n, kw):
        nonlocal offset
        offered.append({tool["name"] for tool in kw["tools"]})
        outputs = [json.loads(item["output"]) for item in kw["input"]
            if item.get("type") == "function_call_output"]
        for item in kw["input"]:
            if item.get("role") == "developer" and item.get("content", "").startswith('{"tool_results"'):
                outputs.extend(json.loads(item["content"])["tool_results"])
        pages = [o for o in outputs if "next_offset" in o]
        if pages:
            received.append(pages[-1])
            offset = pages[-1]["next_offset"]
        if n <= 5:
            return call("get_analysis_claims", {"article_id": ids[n-1] if mode == "articles" else ids[0],
                "fields": [fields[n-1]] if mode == "fields" else ["results"],
                "offset": offset if mode == "pages" else 0}, n)
        return Model(ids, [])(n, kw)

    result = agent(Client(model)).run(t.state)
    assert not result["partial"], result["reason"]
    if mode == "fields":
        # Audit 128698bb: once every claim of the card has reached this section (packet and
        # earlier pages), get_analysis_claims is no longer offered; each read before that was served.
        from agents.EnnoScholarV4.writing_context import claim_key
        from agents.EnnoScholarV4.context import analysis_projection
        delivered = {k for keys in t.state["section_received_claims"].values() for k in keys}
        view = analysis_projection(t.registry[ids[0]].analysis, evidence=t.evidence)
        offered_rounds = sum("get_analysis_claims" in tools for tools in offered[:5])
        assert offered_rounds == 5 or all(claim_key(c) in delivered for v in view.values()
            if isinstance(v, list) and v and isinstance(v[0], dict) for c in v)
        assert len(received) >= min(offered_rounds, 5)-1
        return
    assert "get_analysis_claims" in offered[4]
    assert len(received) == 5
    if mode == "pages":
        assert all(page["next_offset"] > page["offset"] for page in received)
        assert len({p["offset"] for p in received}) == 5
