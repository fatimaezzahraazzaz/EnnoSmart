from __future__ import annotations

from agents.EnnoScholar import scientific_query_planner as p


def _intent():
    passage = (
        "Les essais actuels montrent une variation significative de la température d’air en sortie "
        "du compresseur selon le débit d’eau et les conditions d’aspiration, mais les conditions "
        "de fonctionnement plus sévères n’ont pas encore été explorées ni validées."
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


def _good_payload():
    return {
        "scientific_object": [
            {"term_en": "compressor", "source_phrase": "compresseur"},
        ],
        "phenomena": [
            {"term_en": "discharge air temperature variation", "source_phrase": "variation significative de la température d’air en sortie du compresseur"},
        ],
        "independent_variables": [
            {"term_en": "cooling water flow rate", "source_phrase": "débit d’eau"},
        ],
        "response_variables": [
            {"term_en": "compressor discharge air temperature", "source_phrase": "température d’air en sortie du compresseur"},
        ],
        "operating_conditions": [
            {"term_en": "compressor suction conditions", "source_phrase": "conditions d’aspiration"},
        ],
        "methods": [
            {"term_en": "experimental tests", "source_phrase": "essais actuels"},
        ],
        "validation_concepts": [
            {"term_en": "validation under severe operating conditions", "source_phrase": "conditions de fonctionnement plus sévères n’ont pas encore été explorées ni validées"},
        ],
        "local_identifiers": [
            {"value": "TGM100", "source_phrase": "TGM100"},
        ],
        "ambiguities": [
            {"source_term": "débit", "resolved_en": "flow rate", "source_phrase": "débit d’eau"},
        ],
    }


def test_v166_3_uses_current_v161_passages_as_evidence():
    evidence = p._source_evidence(_intent())
    assert "débit d’eau" in evidence
    assert "température d’air en sortie" in evidence
    assert "conditions d’aspiration" in evidence


def test_v166_3_removes_metadata_noise():
    intent = _intent()
    intent["source_passages"].append("true false null session_ABCDEF123 550e8400-e29b-41d4-a716-446655440000")
    evidence = p._source_evidence(intent).lower()
    assert " true " not in f" {evidence} "
    assert " false " not in f" {evidence} "
    assert "550e8400" not in evidence


def test_v166_3_keeps_local_identifier_out_of_main_queries(monkeypatch):
    monkeypatch.setattr(p, "_call_llm_planner", lambda evidence, ids: (_good_payload(), {"used": True, "ok": True}))
    enriched = p.attach_query_plan(_intent())
    queries = [row["query"].lower() for row in enriched["search_queries"]]
    assert queries
    assert all("tgm100" not in q for q in queries)
    assert "TGM100" in enriched["local_names"]


def test_v166_3_direct_query_is_object_independent_response(monkeypatch):
    monkeypatch.setattr(p, "_call_llm_planner", lambda evidence, ids: (_good_payload(), {"used": True, "ok": True}))
    enriched = p.attach_query_plan(_intent())
    direct = next(row["query"].lower() for row in enriched["search_queries"] if row["family"] == "direct")
    assert "compressor" in direct
    assert "water" in direct and "flow" in direct and "rate" in direct
    assert "discharge" in direct and "temperature" in direct
    assert "impact" not in direct
    assert "severe" not in direct
    assert " and " not in f" {direct} "
    assert " of " not in f" {direct} "


def test_v166_3_bad_relational_object_is_rejected():
    evidence = p._source_evidence(_intent())
    bad = _good_payload()
    bad["scientific_object"] = [
        {"term_en": "impact of and severe conditions on compressor output", "source_phrase": "impact du débit d’eau et conditions sévères sur température sortie"}
    ]
    validated = p._validate_llm_payload(bad, evidence)
    assert validated["scientific_object"] == []


def test_v166_3_legacy_fields_remain_for_existing_ranker(monkeypatch):
    monkeypatch.setattr(p, "_call_llm_planner", lambda evidence, ids: (_good_payload(), {"used": True, "ok": True}))
    enriched = p.attach_query_plan(_intent())
    plan = enriched["scientific_query_plan"]
    assert any(row["term_en"] == "cooling water flow rate" for row in plan["variables"])
    assert any(row["term_en"] == "compressor discharge air temperature" for row in plan["variables"])
    assert any(row["term_en"] == "compressor suction conditions" for row in plan["constraints"])
    assert "cooling water flow rate" in enriched["core_concepts"]


def test_v166_3_provider_adapter_preserves_scientific_core(monkeypatch):
    monkeypatch.setattr(p, "_call_llm_planner", lambda evidence, ids: (_good_payload(), {"used": True, "ok": True}))
    enriched = p.attach_query_plan(_intent())
    direct = next(row["query"] for row in enriched["search_queries"] if row["family"] == "direct")
    crossref = p.adapt_query_for_provider(direct, "crossref", intent=enriched).lower()
    assert crossref
    assert len(crossref.split()) <= 8
    assert "compressor" in crossref
    assert "water" in crossref and "flow" in crossref
    assert "temperature" in crossref


def test_v166_3_query_guard_blocks_ambiguous_source_word(monkeypatch):
    monkeypatch.setattr(p, "_call_llm_planner", lambda evidence, ids: (_good_payload(), {"used": True, "ok": True}))
    enriched = p.attach_query_plan(_intent())
    plan = enriched["scientific_query_plan"]
    assert not p.query_is_safe("compressor debit discharge temperature", plan)
    assert p.query_is_safe("compressor cooling water flow rate discharge air temperature", plan)


def test_v166_3_topic_drift_still_detects_unrelated_corpus(monkeypatch):
    monkeypatch.setattr(p, "_call_llm_planner", lambda evidence, ids: (_good_payload(), {"used": True, "ok": True}))
    papers = [
        {"title": "Impact of Debit and Credit Cards on Currency in Circulation"},
        {"title": "Air-Sea Interactions on Titan"},
        {"title": "Thermal limits of a marine teleost"},
        {"title": "Economic recovery after COVID-19"},
        {"title": "Carbon geopolymer sandwich panels"},
        {"title": "Ozone chemistry and polar vortex events"},
    ]
    report = p.assess_topic_drift(_intent(), papers)
    assert report["checked"] is True
    assert report["triggered"] is True


def test_v166_3_query_families_have_distinct_roles(monkeypatch):
    monkeypatch.setattr(p, "_call_llm_planner", lambda evidence, ids: (_good_payload(), {"used": True, "ok": True}))
    enriched = p.attach_query_plan(_intent())
    families = {row["family"] for row in enriched["search_queries"]}
    assert "direct" in families
    assert "operating_conditions" in families
    assert "experimental" in families
