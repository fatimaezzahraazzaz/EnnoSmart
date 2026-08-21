from __future__ import annotations

from agents.EnnoScholar import scientific_query_planner as p


def _tgm_intent():
    passage = (
        "Les essais montrent une variation significative de la température d’air en sortie "
        "du compresseur selon le débit d’eau et les conditions d’aspiration."
    )
    return {
        "verrou_title": "Compresseur TGM100 : incertitude sur l’impact du débit d’eau et conditions sévères sur température sortie",
        "source_passages": [passage],
        "source_basis": {
            "verrou_title": "Compresseur TGM100",
            "linked_passages_excerpt": [passage],
            "relevant_diagnostic_context": "Des essais complémentaires permettront de comparer les conditions de fonctionnement.",
        },
        "scientific_problem": "Compresseur TGM100 débit eau température sortie",
        "query_language": "fr",
    }


def _fake_plan_payload():
    return {
        "scientific_object": [
            {"term_en": "compressor", "source_phrase": "compresseur"},
        ],
        "phenomena": [
            {"term_en": "discharge temperature variation", "source_phrase": "variation significative de la température d’air en sortie du compresseur"},
        ],
        "variables": [
            {"term_en": "cooling water flow rate", "source_phrase": "débit d’eau"},
            {"term_en": "suction conditions", "source_phrase": "conditions d’aspiration"},
        ],
        "methods": [],
        "constraints": [],
        "validation_concepts": [
            {"term_en": "experimental measurements", "source_phrase": "essais"},
        ],
        "local_identifiers": [
            {"value": "TGM100", "source_phrase": "TGM100"},
        ],
        "ambiguities": [
            {"source_term": "débit", "resolved_en": "flow rate", "source_phrase": "débit d’eau"},
        ],
    }


def test_v161_linked_passages_are_used_as_evidence():
    evidence = p._source_evidence(_tgm_intent())
    assert "variation significative" in evidence
    assert "débit d’eau" in evidence
    assert "conditions d’aspiration" in evidence


def test_structural_metadata_noise_is_removed():
    intent = _tgm_intent()
    intent["source_passages"].append(
        "true false null session_ABCDEF123 550e8400-e29b-41d4-a716-446655440000"
    )
    e = p._source_evidence(intent).lower()
    assert " true " not in f" {e} "
    assert " false " not in f" {e} "
    assert "550e8400" not in e


def test_mixed_local_machine_id_is_detected_but_scientific_acronyms_are_not():
    local = p._explicit_local_identifiers(_tgm_intent(), p._source_evidence(_tgm_intent()))
    assert "TGM100" in local
    other = {
        "verrou_title": "SAR ATR uncertainty under measured data",
        "source_passages": ["SAR ATR validation with measured data"],
        "local_names": ["SAR", "ATR"],
    }
    local2 = p._explicit_local_identifiers(other, p._source_evidence(other))
    assert "SAR" not in local2
    assert "ATR" not in local2


def test_tgm100_query_plan_is_scientific_english_and_no_debit_card(monkeypatch):
    monkeypatch.setattr(
        p,
        "_call_llm_planner",
        lambda evidence, ids: (_fake_plan_payload(), {"used": True, "ok": True, "provider": "test"}),
    )
    enriched = p.attach_query_plan(_tgm_intent())
    queries = [x["query"].lower() for x in enriched["search_queries"]]
    assert queries
    assert all("tgm100" not in q for q in queries)
    assert all(" debit " not in f" {q} " for q in queries)
    assert any("compressor" in q for q in queries)
    assert any("water" in q and "flow" in q for q in queries)
    assert "TGM100" in enriched["local_names"]


def test_query_guard_rejects_local_identifier_and_ambiguous_source_term(monkeypatch):
    monkeypatch.setattr(
        p,
        "_call_llm_planner",
        lambda evidence, ids: (_fake_plan_payload(), {"used": True, "ok": True}),
    )
    enriched = p.attach_query_plan(_tgm_intent())
    plan = enriched["scientific_query_plan"]
    assert not p.query_is_safe("TGM100 compressor cooling water flow rate", plan)
    assert not p.query_is_safe("compressor debit temperature measurements", plan)
    assert p.query_is_safe("compressor cooling water flow rate discharge temperature variation", plan)


def test_provider_adapter_keeps_meaning_and_shortens_crossref(monkeypatch):
    monkeypatch.setattr(
        p,
        "_call_llm_planner",
        lambda evidence, ids: (_fake_plan_payload(), {"used": True, "ok": True}),
    )
    enriched = p.attach_query_plan(_tgm_intent())
    q = enriched["search_queries"][0]["query"]
    crossref = p.adapt_query_for_provider(q, "crossref", intent=enriched)
    assert crossref
    assert len(crossref.split()) <= 8
    assert "TGM100" not in crossref


def test_topic_drift_detects_unrelated_corpus(monkeypatch):
    monkeypatch.setattr(
        p,
        "_call_llm_planner",
        lambda evidence, ids: (_fake_plan_payload(), {"used": True, "ok": True}),
    )
    papers = [
        {"title": "Impact of Debit and Credit Cards on Currency in Circulation"},
        {"title": "Air-Sea Interactions on Titan"},
        {"title": "Thermal limits of a marine teleost"},
        {"title": "Economic recovery after COVID-19"},
        {"title": "Carbon geopolymer sandwich panels"},
        {"title": "Ozone chemistry and polar vortex events"},
    ]
    report = p.assess_topic_drift(_tgm_intent(), papers)
    assert report["checked"] is True
    assert report["triggered"] is True
    assert report["alignment_ratio"] < report["threshold"]
