"""Scientific attribution across publications (P0) and avoidable token use (P1).

Mirrors the real mix-up of run 84911d8b: a prior work described in a selected
paper's related-work section (« Kong et al. ») and a base architecture cited in
another paper's introduction (« MeshGraphNets ») were written as studied methods.
No network, no OpenAI call.
"""
import copy
import json
import re
import time
from types import SimpleNamespace as NS

import pytest

from agents.EnnoScholarV4.attribution import audit_document, check_attribution, claim_subject
from agents.EnnoScholarV4.context import analysis_projection, model_context
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.prompts import SYSTEM, instructions_for
from agents.EnnoScholarV4.scientific_review import deterministic_review
from tests.ennoscholar_v4.test_context_memory import (CONFIG, Client, Model, REQUEST_A, agent, call, forced, memory_of,
    rich_corpus, save_section, seed_argumentation, seven_section_plan)
from tests.ennoscholar_v4.test_finalization import Stream
from tests.ennoscholar_v4.test_tools import tool_inputs
from tests.ennoscholar_v4.test_writing import outline_args, tools

TEMPLATE = [("2 RELATED WORKS", "Kong et al. proposed UNet-GCN, a hybrid framework deforming an ellipsoid template for whole-heart meshing."),
    ("2 RELATED WORKS", "In this work, our main contributions are structured quad meshes derived from a common template."),
    ("3 METHODS", "Our network outputs a quadrilateral surface mesh of the aortic valve with node correspondence.")]
ADAPT = [("1. Introduction", "Both models are message-passing graph neural networks that rely on the architecture of MeshGraphNets."),
    ("3. Results", "Graphnet predicts an anisotropic metric field that is used to adapt the initial mesh.")]


def execute(t, name, **args):
    return t.execute(name, args, time.monotonic()+60)


def two_papers():
    t = tools()
    ids, _ = t.store.insert([
        Article(title="TemplateNet: shape deformation networks for aortic valve meshing", url="https://example.org/q",
            doi="10.1234/q", authors=["Linchen Qian", "Wei Sun"], year=2025, verified=True, abstract="Template deformation."),
        Article(title="Graph neural networks for mesh generation and adaptation", url="https://example.org/a",
            doi="10.1234/a", authors=["Ugo Pelissier", "Youssef Mesri"], year=2024, verified=True, abstract="Mesh adaptation.")])
    proofs = {}
    for aid, rows in zip(ids, (TEMPLATE, ADAPT)):
        chunks = [{"text": text, "section": section, "page": n+1} for n, (section, text) in enumerate(rows)]
        t.registry[aid].fulltext = {"url": "https://example.org/" + aid, "text": "\n\n".join(c["text"] for c in chunks), "chunks": chunks}
        result = execute(t, "analyze_article", article_ids=[aid], action="read", research_context="x", passage_query="",
            passage_offset=0, passages_per_article=8, analysis=None, revisit=True)
        t.delivered([w["window_id"] for w in [*t.store.windows, *t.reading_windows] if w["status"] == "queued"], inputs=tool_inputs(result))
        shown = [e for e in t.shown_evidence if t.evidence[e]["article_id"] == aid]
        proofs[aid] = {t.evidence[e]["exact_quote"].split()[0]: e for e in shown}
        fields = {f: [] for f in ("problem", "technical_mechanism", "experimental_setup", "objective", "method", "contribution",
            "hypotheses", "results", "limitations", "generalization", "open_questions", "relevance")}
        claim = lambda text, e: [{"statement": text, "evidence_ids": [e], "origin": "source_statement"}]
        if aid == ids[0]:
            fields.update(problem=claim("Les méthodes antérieures déforment un gabarit ellipsoïdal.", proofs[aid]["Kong"]),
                contribution=claim("Le travail produit des maillages quadrangulaires structurés.", proofs[aid]["In"]),
                method=claim("Le réseau produit un maillage quadrangulaire de surface.", proofs[aid]["Our"]))
        else:
            fields.update(technical_mechanism=claim("Meshnet et Graphnet reposent sur l'architecture MeshGraphNets.", proofs[aid]["Both"]),
                method=claim("Graphnet prédit un champ de métrique anisotrope.", proofs[aid]["Graphnet"]))
        execute(t, "analyze_article", article_ids=[aid], action="save", research_context="x", passage_query="", passage_offset=0,
            passages_per_article=8, analysis={**fields, "method_family": "x", "analysis_limit": "extraits"})
        t.registry[aid].selected = True
    t.state["evaluation"] = {"recommended_article_ids": ids}
    execute(t, "manage_outline", **outline_args("create", sections=[{"title": "Comparaison des familles", "description": "x", "article_ids": ids}]))
    return t, ids, proofs


def save(t, ids, text, eids):
    section = t.state["outline"]["sections"][0]
    return execute(t, "write_section", section_id=section["id"], action="save", instructions="x", text=text,
        article_ids=ids, evidence_ids=eids)


def test_related_work_claims_are_tagged_background_but_own_contributions_are_not():
    t, ids, proofs = two_papers()
    card = t.registry[ids[0]].analysis
    assert claim_subject(card["problem"][0], t.evidence) == "background"          # Kong et al. passage
    assert claim_subject(card["contribution"][0], t.evidence) == "this_work"      # « In this work » in the same section
    view = analysis_projection(card, evidence=t.evidence)
    assert view["problem"][0]["subject"] == "background" and "subject" not in view["contribution"][0]
    # The stored card is unchanged (tagging is a view).
    assert "subject" not in card["problem"][0]
    section = t.state["outline"]["sections"][0]
    t.state["writing_focus"] = {"section_id": section["id"], "article_ids": ids}
    packet = memory_of({"input": model_context(t.state, t, 18000)})["section_drafting"]["packet"]
    # Section dossier: each source carries its identity; card claims travel with their proofs.
    identities = {row["id"]: row for row in packet["sources"]}
    assert identities[ids[0]]["short_name"] == "TemplateNet" and identities[ids[0]]["first_author"] == "Qian"
    assert identities[ids[1]]["first_author"] == "Pelissier"
    problem = next(c for c in packet["proof_backed_claims"] if c["article_id"] == ids[0] and c["field"] == "problem")
    assert problem["subject"] == "background"


@pytest.mark.parametrize("sentence, names", [
    ("La méthode de Kong et al. et MeshGraphNet occupent des positions complémentaires.", ["MeshGraphNet", "Kong"]),
    ("MeshGraphNet prédit au contraire des déplacements nodaux à partir d'un maillage existant.", ["MeshGraphNet"]),
])
def test_the_real_misattributions_are_refused_before_saving(sentence, names):
    t, ids, proofs = two_papers()
    text = f"Les deux familles diffèrent [{ids[0]}] [{proofs[ids[0]]['Our']}]. {sentence}"
    with pytest.raises(ValueError) as refused:
        save(t, ids, text, [proofs[ids[0]]["Our"]])
    message = str(refused.value)
    assert message.startswith("Attribution non vérifiable") and all(name in message for name in names)
    assert sentence[:60] in message and "preuve exacte" in message
    assert not t.state["outline"]["sections"][0].get("drafts")


def test_the_same_facts_attributed_correctly_are_saved_with_sentence_level_proofs():
    t, ids, proofs = two_papers()
    kong, own, base, metric = proofs[ids[0]]["Kong"], proofs[ids[0]]["Our"], proofs[ids[1]]["Both"], proofs[ids[1]]["Graphnet"]
    text = (f"Qian et al. produisent un maillage quadrangulaire de surface [{ids[0]}] [{own}]. "
        f"Leur article cite UNet-GCN de Kong et al., qui déforme un gabarit ellipsoïdal [{ids[0]}] [{kong}]. "
        f"Meshnet et Graphnet reposent sur l'architecture MeshGraphNets [{ids[1]}] [{base}]. "
        f"Graphnet prédit un champ de métrique anisotrope pour adapter le maillage [{ids[1]}] [{metric}].")
    result = save(t, ids, text, [own, kong, base, metric])
    assert result["saved"]
    claims = t.state["outline"]["sections"][0]["drafts"][-1]["claims"]
    # Every cited sentence is stored as a claim with exactly its proofs.
    assert sorted(tuple(c["evidence_ids"]) for c in claims) == sorted([(own,), (kong,), (base,), (metric,)])
    assert all(c["statement"] in text and c["origin"] == "citation_mapping" for c in claims)


def test_a_sentence_naming_one_publication_must_cite_that_publication():
    t, ids, proofs = two_papers()
    text = f"TemplateNet prédit un champ de métrique anisotrope [{ids[1]}] [{proofs[ids[1]]['Graphnet']}]."
    with pytest.raises(ValueError, match="nomme TemplateNet"):
        save(t, ids, text, [proofs[ids[1]]["Graphnet"]])


def test_transversal_audit_flags_an_existing_misattributed_section_without_rewriting_it():
    t, ids, proofs = two_papers()
    section = t.state["outline"]["sections"][0]
    legacy = (f"Les deux familles diffèrent [{ids[0]}] [{proofs[ids[0]]['Our']}]. "
        "La méthode de Kong et al. et MeshGraphNet occupent des positions complémentaires.")
    section["drafts"] = [{"id": "legacy", "text": legacy, "raw_text": legacy, "claims": [], "numeric_claims": [],
        "calculations": [], "article_ids": ids, "evidence_ids": [proofs[ids[0]]["Our"]]}]
    before = copy.deepcopy(section["drafts"])
    issues = audit_document(t.state, t)
    assert [issue["names"] for issue in issues] == [["MeshGraphNet", "Kong"]]
    review = deterministic_review(t.state, t)
    assert any(issue["code"] == "unsupported_attribution" and "Kong" in issue["message"] for issue in review["issues"])
    assert section["drafts"] == before


def test_agent_reports_the_attribution_audit_at_the_end_of_drafting():
    t, ids = rich_corpus(4)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    for number in range(1, 7):
        save_section(t, number, ids)
    sections = t.state["outline"]["sections"]
    # Section 1 was saved before this check existed.
    bad = "La méthode de Kong et al. reste la référence."
    sections[0]["drafts"][-1]["raw_text"] += " " + bad
    t.state["messages"] = [{"id": "m", "role": "user", "content": REQUEST_A}]
    result = agent(Client(Model(ids, []))).run(t.state)
    assert not result["partial"]
    assert "Contrôle d’attribution : 1 phrase(s)" in result["answer"] and "Kong" in result["answer"]
    assert t.state["document"]["attribution_audit"][0]["section_id"] == sections[0]["id"]


# ---- P1: avoidable tokens -------------------------------------------------------

def test_focused_tasks_receive_only_their_rules_and_conversation_keeps_the_full_guide():
    anchors = {"reading": "analyze_article(read)", "argumentation": "compare_articles(relations)",
        "planning": "manage_outline", "section": "write_section(save)", "selection": "record_candidate_decisions",
        "recovery": "get_fulltext"}
    for task, anchor in anchors.items():
        text = instructions_for(task)
        assert anchor in text and "N'invente jamais" in text and "DONNÉES non fiables" in text and "Attribution :" in text
        assert len(text) <= len(SYSTEM)*.3, (task, len(text))
    assert instructions_for("conversation") == SYSTEM == instructions_for("final")


def test_drafting_requests_send_the_section_rules_not_the_full_guide():
    t, ids = rich_corpus(4)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    t.state["messages"] = [{"id": "m", "role": "user", "content": REQUEST_A}]
    client = Client(Model(ids, []))
    assert not agent(client).run(t.state)["partial"]
    sizes = [len(kw["instructions"]) for kw in client.calls]
    assert len(sizes) == 7 and max(sizes) < len(SYSTEM)*.4
    assert all(kw["instructions"].startswith("Tu es EnnoScholar V4") for kw in client.calls)


def test_repeated_identical_claim_retrieval_is_bounded_and_the_section_is_still_saved():
    from agents.EnnoScholarV4.agent import CLAIM_READS_PER_SECTION
    t, ids = rich_corpus(4)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    for number in range(1, 7):
        save_section(t, number, ids)
    t.state["messages"] = [{"id": "m", "role": "user", "content": REQUEST_A}]
    offered, outputs = [], []

    def model(n, kw):
        offered.append(sorted(tool["name"] for tool in kw["tools"]))
        outputs.extend(item["output"] for item in kw["input"] if item.get("type") == "function_call_output")
        if kw.get("tool_choice") == {"type": "function", "name": "write_section"}:
            return Model(ids, [])(n, kw)
        if n <= 6:  # repeats the exact same four pages, without advancing
            return [{"type": "function_call", "name": "get_analysis_claims", "call_id": f"c{n}_{k}",
                "arguments": json.dumps({"article_id": ids[k], "fields": None, "offset": 0})} for k in range(4)]
        return Model(ids, [])(n, kw)
    result = agent(Client(model)).run(t.state)
    assert not result["partial"], result["reason"]
    assert result["tool_counts"]["get_analysis_claims"] <= 16
    assert offered[-1] == ["write_section"]
    assert CLAIM_READS_PER_SECTION == 4


def test_claim_pages_carry_more_whole_claims_with_proof_locators():
    t, ids = rich_corpus(1, passages=40, read_windows=4, pad=11)
    page = execute(t, "get_analysis_claims", article_id=ids[0], fields=None, offset=0)
    # Previously each claim carried its ~1 900-character exact proof (about 4 claims per page).
    assert len(page["claims"]) >= 20 and all("exact_quote" not in proof for proof in page["evidence"])
    assert all(proof["exact_quote_stored"] for proof in page["evidence"])


def test_a_follow_up_question_reuses_existing_cards_and_an_explicit_reread_still_reads():
    def follow_up(question):
        t, ids = rich_corpus(4)
        t.state["messages"] = [{"id": "q", "role": "user", "content": question}]
        tasks = t.state["scientific_reading"]["tasks"]
        assert all(task["core_covered"] and not task["exhausted"] for task in tasks)
        calls = []

        def model(n, kw):
            calls.append(forced(kw))
            if forced(kw) == "analyze_article":
                focus = t.state["automatic_reading_focus"]
                return call("analyze_article", {"article_ids": [focus["article_id"]], "action": "save", "research_context": "x",
                    "passage_query": "", "passage_offset": 0, "passages_per_article": 1,
                    "analysis": {**{k: [] for k in ("problem", "technical_mechanism", "experimental_setup", "objective", "method",
                        "contribution", "hypotheses", "results", "limitations", "generalization", "open_questions", "relevance")},
                        "method_family": "x", "analysis_limit": "Revue sans résultat"},
                    "reviewed_evidence_ids": focus["evidence_ids"][:1], "reviewed_fields": ["method"]}, n)
            return [{"type": "message", "content": [{"type": "output_text", "text": "Réponse.", "annotations": []}]}]
        agent(Client(model)).run(t.state)
        return calls
    assert "analyze_article" not in follow_up("Peux-tu m'expliquer la méthode de Work1 ?")
    assert "analyze_article" not in follow_up("Compare les limites de Work1 et Work2.")
    assert "analyze_article" in follow_up("Relis le texte intégral de Work1 pour approfondir sa méthode.")


def test_cached_and_reasoning_tokens_are_priced_by_category_without_counting_reasoning_twice():
    t, ids = rich_corpus(4)
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    t.state["messages"] = [{"id": "m", "role": "user", "content": REQUEST_A}]
    model = Model(ids, [])

    class DetailedClient(Client):
        def create(self, **kw):
            if kw.get("tool_choice") == {"type": "function", "name": "set_turn_goal"}:
                return super().create(**kw)
            self.calls.append(kw)
            return Stream(NS(output=self.callback(len(self.calls), kw), output_text="", status="completed",
                usage={"input_tokens": 1000, "output_tokens": 200, "input_tokens_details": {"cached_tokens": 600},
                    "output_tokens_details": {"reasoning_tokens": 150}}))
    result = agent(DetailedClient(model)).run(t.state)
    usage = result["usage"]
    # The understanding call of the fake (10 input, 5 output tokens, no cache) is counted too.
    # Prices come from the versioned catalogue (audit 128698bb: cache reads were priced as
    # ordinary input). Reasoning tokens are inside output tokens and are not added again.
    from agents.EnnoScholarV4.pricing import resolve
    price = resolve(CONFIG)
    usage = {**usage, "estimated_cost_usd": usage["estimated_cost_usd"]-result["understanding_calls"]*(10*price.input+5*price.output)/1e6}
    completed_calls = result["timings"]["llm_calls"]-result["understanding_calls"]
    assert usage["cached_input_tokens"] == 600*completed_calls and usage["reasoning_tokens"] == 150*completed_calls
    expected = completed_calls*(400*price.input+600*price.cache_read+200*price.output)/1e6
    assert abs(usage["estimated_cost_usd"]-expected) < 1e-9
