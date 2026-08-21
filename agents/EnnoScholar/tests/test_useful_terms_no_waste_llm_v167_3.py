from __future__ import annotations

import json

from agents.EnnoScholar import scientific_query_workflow as wf
from agents.EnnoScholar.fast_retrieval import (
    VERSION,
    build_query_portfolio,
    is_obvious_noise_token,
    query_is_useful,
    sanitize_query_text,
)


def _rich_plan():
    return {
        "scientific_object": [{"term_en": "compressor TGM100", "source_phrase": "Compresseur TGM100"}],
        "independent_variables": [
            {"term_en": "water flow rate"},
            {"term_en": "type of refrigerant"},
        ],
        "response_variables": [
            {"term_en": "compressor outlet temperature"},
            {"term_en": "air intake temperature"},
        ],
        "operating_conditions": [{"term_en": "severe operating conditions for compressor"}],
        "phenomena": [{"term_en": "temperature variation of compressor outlet"}],
        "methods": [{"term_en": "experimental testing under varied water flow rates"}],
        "validation_concepts": [{"term_en": "complementary tests to validate performance"}],
        "local_identifiers": [{"value": "TGM100"}],
        "ambiguities": [],
    }


def test_useful_project_terms_are_kept_and_noise_is_removed():
    assert is_obvious_noise_token("TGM100") is False
    assert is_obvious_noise_token("300bar") is False
    assert is_obvious_noise_token("R290") is False
    assert is_obvious_noise_token("true") is True
    assert is_obvious_noise_token("550e8400-e29b-41d4-a716-446655440000") is True
    assert is_obvious_noise_token("cr_releve_temperature_10bd6eed0529a4ef") is True
    cleaned = sanitize_query_text(
        "TGM100 compressor 300bar true session_123 cr_releve_temperature_10bd6eed0529a4ef water flow rate"
    )
    assert "tgm100" in cleaned.lower()
    assert "300bar" in cleaned.lower()
    assert "water flow rate" in cleaned.lower()
    assert "true" not in cleaned.lower()
    assert "10bd6eed" not in cleaned.lower()


def test_five_queries_and_context_term_does_not_contaminate_all_queries():
    rows = build_query_portfolio(_rich_plan(), [], target=5)
    assert len(rows) == 5
    assert all(row["planner_version"] == VERSION for row in rows)
    joined = [row["query"].lower() for row in rows]
    assert any("tgm100" in q for q in joined)  # useful context retained
    assert any("tgm100" not in q for q in joined)  # not forced into every query
    assert all("compressor" in q for q in joined)
    assert all(query_is_useful(row["query"], _rich_plan()) for row in rows)


def test_bad_query_is_isolated_not_global_failure():
    bases = [
        {"query": "550e8400-e29b-41d4-a716-446655440000 true null", "family": "bad"},
        {"query": "compressor TGM100 water flow rate outlet temperature", "family": "good"},
    ]
    rows = build_query_portfolio(_rich_plan(), bases, target=5)
    assert len(rows) == 5
    assert any("tgm100" in row["query"].lower() for row in rows)
    assert all("550e8400" not in row["query"].lower() for row in rows)


def _intent():
    return {
        "verrou_id": "668",
        "verrou_title": "Compresseur TGM100 : incertitude sur l’impact du débit d’eau et conditions sévères sur température sortie",
        "source_passages": [
            "Les essais montrent une variation significative de la température d’air en sortie du compresseur selon le débit d’eau et les conditions d’aspiration.",
            "Des essais complémentaires devront être réalisés sous des conditions de fonctionnement plus sévères.",
        ],
        "scientific_problem": "variation de température en fonction du débit d'eau",
    }


def test_complete_first_plan_uses_one_llm_call_even_with_project_term(monkeypatch):
    evidence = wf._legacy._source_evidence(_intent())
    units = wf._v1671_split_evidence(evidence)
    object_id = next(k for k, v in units.items() if "Compresseur TGM100".lower() in v.lower())
    flow_id = next(k for k, v in units.items() if "débit d’eau".lower() in v.lower())
    severe_id = next(k for k, v in units.items() if "sévères" in v.lower())
    calls = []

    payload = {
        "scientific_object": [{"term_en": "compressor TGM100", "evidence_ids": [object_id]}],
        "independent_variables": [{"term_en": "water flow rate", "evidence_ids": [flow_id]}],
        "response_variables": [{"term_en": "compressor outlet temperature", "evidence_ids": [flow_id]}],
        "operating_conditions": [{"term_en": "severe operating conditions", "evidence_ids": [severe_id]}],
        "phenomena": [{"term_en": "compressor outlet temperature variation", "evidence_ids": [flow_id]}],
        "methods": [{"term_en": "experimental testing", "evidence_ids": [severe_id]}],
        "validation_concepts": [],
        "local_identifiers": [{"value": "TGM100", "evidence_ids": [object_id]}],
        "ambiguities": [],
    }

    class FakeClient:
        def generate(self, **kwargs):
            calls.append(kwargs.get("request_name"))
            return json.dumps(payload, ensure_ascii=False)
        def get_last_generation_meta(self):
            return {"provider": "fake", "model": "fake", "total_tokens": 10}

    monkeypatch.setattr(wf._legacy, "_load_llm_client_class", lambda: FakeClient)
    result = wf.run_query_workflow(_intent())
    assert result["workflow"]["status"] == wf.STATUS_READY
    assert result["workflow"]["attempts"] == 1
    assert len(result["workflow"]["llm_attempts"]) == 1
    assert len(calls) == 1
    assert len(result["queries"]) == 5
    assert result["workflow"]["evidence_unit_count"] > 0


def test_only_missing_roles_can_trigger_one_repair(monkeypatch):
    evidence = wf._legacy._source_evidence(_intent())
    units = wf._v1671_split_evidence(evidence)
    object_id = next(k for k, v in units.items() if "Compresseur TGM100".lower() in v.lower())
    flow_id = next(k for k, v in units.items() if "débit d’eau".lower() in v.lower())
    calls = []
    payloads = [
        {
            "scientific_object": [{"term_en": "compressor TGM100", "evidence_ids": [object_id]}],
            "independent_variables": [], "response_variables": [], "operating_conditions": [],
            "phenomena": [], "methods": [], "validation_concepts": [],
            "local_identifiers": [{"value": "TGM100", "evidence_ids": [object_id]}], "ambiguities": [],
        },
        {
            "scientific_object": [],
            "independent_variables": [{"term_en": "water flow rate", "evidence_ids": [flow_id]}],
            "response_variables": [{"term_en": "compressor outlet temperature", "evidence_ids": [flow_id]}],
            "operating_conditions": [], "phenomena": [], "methods": [], "validation_concepts": [],
            "local_identifiers": [], "ambiguities": [],
        },
    ]

    class FakeClient:
        idx = 0
        def generate(self, **kwargs):
            calls.append(kwargs.get("request_name"))
            data = payloads[min(FakeClient.idx, 1)]
            FakeClient.idx += 1
            return json.dumps(data, ensure_ascii=False)
        def get_last_generation_meta(self):
            return {"provider": "fake", "model": "fake", "total_tokens": 10}

    monkeypatch.setattr(wf._legacy, "_load_llm_client_class", lambda: FakeClient)
    result = wf.run_query_workflow(_intent())
    assert result["workflow"]["status"] == wf.STATUS_READY
    assert result["workflow"]["attempts"] == 2
    assert len(calls) == 2
    assert not any("attempt_3" in str(x) for x in calls)
    assert result["queries"]
