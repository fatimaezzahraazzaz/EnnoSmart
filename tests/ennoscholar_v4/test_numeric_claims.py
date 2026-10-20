"""Numeric claims tied to their source table, and multi-section drafting that survives a hard section.

Reproduces run 23e259a8: section 5 cited AdaptNet Table 2 (metrics as row labels,
units printed in the cells) and was refused three times, the last time with
« Métrique absente de la légende ou de la colonne source. », which stopped the run at 4/7.
No network, no OpenAI call.
"""
import copy
import json
import re

import pytest

from agents.EnnoScholarV4.scientific_review import validate_number
from agents.EnnoScholarV4.tools.tables import structure
from tests.ennoscholar_v4.test_context_memory import (Client, agent, call, memory_of, proofs_of, rich_corpus,
    save_section, seed_argumentation, seven_section_plan)

# AdaptNet Table 2 exactly as extracted in the real session.
ADAPTNET_TABLE = structure(["Model", "Meshnet", "Graphnet"],
    [["Computation", "Engineer time", "50.06 s"], ["Prediction", "0.68 s", "0.79 s"], ["Speed-up", "ND", "63.4"]],
    "Table 2. CPU time comparison for computation and prediction of each phase", "2")
REQUEST = "Continue la rédaction : rédige toutes les sections restantes sans modifier les sections déjà enregistrées."


def table_corpus():
    t, ids = rich_corpus(4, table=ADAPTNET_TABLE)
    rows = {tuple(t.evidence[e]["table"]["row"]): e for e in t.shown_evidence if isinstance(t.evidence[e].get("table"), dict)}
    return t, ids, rows


def fact(eid, value="0.79", unit="s", metric="Prediction", column="Graphnet"):
    return {"evidence_id": eid, "column": column, "value": value, "unit": unit, "metric": metric}


def test_real_refusal_is_reproduced_and_the_source_linked_claim_now_validates():
    t, ids, rows = table_corpus()
    eid = rows[("Prediction", "0.68 s", "0.79 s")]
    assert t.evidence[eid]["table"]["structure_status"] == "verified_cells"
    # The claim the model could not save: metric = row label, unit printed in the cell.
    checked = validate_number(fact(eid), t)
    assert checked["numeric_value"] == 0.79 and checked["table_number"] == "2" and checked["article_id"] == ids[0]
    # The value may also be written exactly as the cell, and the metric taken from the legend.
    assert validate_number(fact(eid, value="0.79 s", metric="CPU time"), t)["numeric_value"] == 0.79
    assert validate_number(fact(eid, value="0.68", column="Meshnet", metric="Meshnet"), t)["numeric_value"] == 0.68
    speed = rows[("Speed-up", "ND", "63.4")]
    assert validate_number(fact(speed, value="63.4", unit="", metric="Speed-up"), t)["numeric_value"] == 63.4


def test_wrong_metric_gets_an_actionable_error_and_is_corrected_from_the_source():
    t, ids, rows = table_corpus()
    eid = rows[("Prediction", "0.68 s", "0.79 s")]
    with pytest.raises(ValueError) as refused:
        validate_number(fact(eid, metric="temps de prédiction"), t)
    message = str(refused.value)
    # Everything needed to find the source and correct the claim.
    for expected in (eid, ids[0], "tableau 2", "CPU time comparison", "['Model', 'Meshnet', 'Graphnet']",
            "['Prediction', '0.68 s', '0.79 s']", "'Prediction'", "reformuler sans valeur non vérifiée"):
        assert expected in message
    accepted = re.search(r"Métriques acceptées, mot pour mot : \[([^\]]*)\]", message).group(1)
    corrected = fact(eid, metric=re.findall(r"'([^']+)'", accepted)[-1])
    assert validate_number(corrected, t)["metric"] == "Prediction"


@pytest.mark.parametrize("bad, error", [
    (dict(metric="minimum scaled Jacobian"), "Métrique « minimum scaled Jacobian » absente"),
    (dict(metric="time"), "absente"),  # too vague: neither a label nor a legend term of this row
    (dict(value="0.8"), "différente de la cellule source « 0.79 s »"),
    (dict(value="0.790"), "différente de la cellule"),
    (dict(unit="ms"), "unités trouvées ['s']"),
    (dict(unit=""), "unités trouvées ['s']"),
    (dict(column="Graph net"), "Colonne « Graph net » absente"),
])
def test_unfound_or_inconsistent_numbers_stay_refused(bad, error):
    t, ids, rows = table_corpus()
    eid = rows[("Prediction", "0.68 s", "0.79 s")]
    if bad.get("metric") == "time":
        bad = dict(metric="time comparison of phases")
    with pytest.raises(ValueError, match=re.escape(error)):
        validate_number(fact(eid, **bad), t)


def test_text_passage_is_not_a_table_cell():
    t, ids, rows = table_corpus()
    prose = next(e for e in proofs_of(t, ids[1]) if not t.evidence[e].get("table"))
    with pytest.raises(ValueError, match="pas une ligne de tableau"):
        validate_number(fact(prose), t)


class Writer:
    """Fake model drafting sections; section 5 carries the AdaptNet number."""

    def __init__(self, ids, rows, metric_attempts, log):
        self.ids, self.rows, self.metric_attempts, self.log = ids, rows, list(metric_attempts), log

    def __call__(self, n, kw):
        memory = memory_of(kw)
        drafting = memory["section_drafting"]
        sid = drafting["focus"]["section_id"]
        title = next(s["title"] for s in self.sections if s["id"] == sid)
        outputs = [item["output"] for item in kw["input"] if item.get("type") == "function_call_output"]
        self.log.append({"section": title, "memory": memory, "last_output": outputs[-1] if outputs else ""})
        proof = drafting["packet"]["evidence"][0]
        args = {"section_id": sid, "action": "save", "instructions": "Rédaction étayée",
            "text": f"{title} : synthèse argumentée [{proof['article_id']}] [{proof['evidence_id']}].",
            "article_ids": [proof["article_id"]], "evidence_ids": [proof["evidence_id"]]}
        if title.startswith("Section 5"):
            eid = self.rows[("Prediction", "0.68 s", "0.79 s")]
            metric = self.metric_attempts.pop(0) if self.metric_attempts else None
            if metric == "from_error":
                # Read the refusal and take the exact label it lists.
                accepted = re.search(r"Métriques acceptées, mot pour mot : \[([^\]]*)\]", self.log[-1]["last_output"])
                metric = re.findall(r"'([^']+)'", accepted.group(1))[-1] if accepted else "?"
            args.update(text=f"Graphnet prédit le champ en 0.79 s [{self.ids[0]}] [{eid}].",
                article_ids=[self.ids[0]], evidence_ids=[eid],
                numeric_claims=[fact(eid, metric=metric or "temps de prédiction")])
        return call("write_section", args, n)


def four_of_seven():
    t, ids, rows = table_corpus()
    seven_section_plan(t, ids)
    seed_argumentation(t, ids)
    for number in range(1, 5):
        assert save_section(t, number, ids)["saved"]
    state = t.state
    state["messages"] = [{"id": "resume", "role": "user", "content": REQUEST}]
    saved = {s["id"]: copy.deepcopy(s["drafts"]) for s in state["outline"]["sections"][:4]}
    return t, ids, rows, state, saved


def run(state, writer):
    writer.sections = state["outline"]["sections"]
    return agent(Client(writer)).run(state)


def test_resume_at_four_of_seven_corrects_the_metric_and_finishes_without_intermediate_approval():
    t, ids, rows, state, saved = four_of_seven()
    log = []
    result = run(state, Writer(ids, rows, ["temps de prédiction", "from_error"], log))
    assert not result["partial"], (result["reason"], result["writing_progress"].get("errors"))
    sections = state["outline"]["sections"]
    # Resumed at section 5, then 6 and 7 in the same run.
    order = [entry["section"] for entry in log]
    assert order[0].startswith("Section 5")
    # Independent sections finish while section 5 is being corrected; their
    # completion order is deliberately not a sequential scheduling contract.
    assert any(title.startswith("Section 6") for title in order)
    assert any(title.startswith("Section 7") for title in order)
    assert {s["id"]: s["drafts"] for s in sections[:4]} == saved
    assert all(len(s["drafts"]) == 1 for s in sections)
    number = sections[4]["drafts"][-1]["numeric_claims"][0]
    assert (number["value"], number["unit"], number["metric"], number["column"], number["table_number"],
        number["article_id"]) == ("0.79", "s", "Prediction", "Graphnet", "2", ids[0])
    # The refusal named the source; the correction came from it.
    correction = next(entry for entry in log[1:] if entry["section"].startswith("Section 5"))
    assert "Métriques acceptées" in correction["last_output"] and rows[("Prediction", "0.68 s", "0.79 s")] in correction["last_output"]
    # Drafts saved without an intermediate approval; final human approval untouched.
    assert "validation humaine" not in result["answer"] and "Plan en attente" not in result["answer"]
    assert "approbation finale" in result["answer"]
    assert result["writing_progress"]["approval_required"] is True
    assert "approval" not in state["outline"] and state["outline"].get("status") != "approved"
    assert not state.get("document", {}).get("approval")


def test_a_section_that_stays_unverifiable_is_set_aside_and_later_sections_are_still_written():
    t, ids, rows, state, saved = four_of_seven()
    log = []
    result = run(state, Writer(ids, rows, ["minimum scaled Jacobian"]*3, log))
    sections = state["outline"]["sections"]
    assert result["partial"] and result["reason"] == "section_blocked"
    assert not sections[4]["drafts"] and sections[5]["drafts"] and sections[6]["drafts"]
    assert {s["id"]: s["drafts"] for s in sections[:4]} == saved
    progress = result["writing_progress"]
    assert progress["section_status"][sections[4]["id"]] == "blocked"
    answer = result["answer"]
    assert "6/7" in answer and "complet" not in answer.split("\n")[0]
    assert "Section 5" in answer and "Métrique « minimum scaled Jacobian » absente" in answer
    # The unverified number was never saved anywhere.
    assert all("0.79" not in json.dumps(s["drafts"], ensure_ascii=False) for s in sections)
    # A new request retries the set-aside section only, then completes.
    state["messages"].append({"id": "again", "role": "user", "content": "Continue la rédaction des sections restantes."})
    log.clear()
    resumed = run(state, Writer(ids, rows, ["temps de prédiction", "from_error"], log))
    assert not resumed["partial"], resumed["reason"]
    assert {entry["section"].split()[1] for entry in log} == {"5"}
    assert all(len(s["drafts"]) == 1 for s in sections)
    assert "Brouillon complet enregistré : 7/7" in resumed["answer"]
