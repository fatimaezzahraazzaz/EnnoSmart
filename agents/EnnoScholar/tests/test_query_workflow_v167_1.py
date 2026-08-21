from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.EnnoScholar import scientific_query_workflow as wf


def _intent():
    return {
        "verrou_id": "668",
        "verrou_title": "Compresseur TGM100 : incertitude sur l’impact du débit d’eau et conditions sévères sur température sortie",
        "source_passages": [
            "Les essais montrent une variation significative de la température d’air en sortie du compresseur selon le débit d’eau et les conditions d’aspiration.",
            "Les conditions de fonctionnement plus sévères n’ont pas encore été explorées ni validées à 300bar.",
            "cr_des_relev_s_en_temp_rature_en_fonction_de_la_variati_10bd6eed0529a4ef",
        ],
        "scientific_problem": "variation de température en fonction du débit d'eau",
        # Deliberately polluted legacy fields: V167.1 must not use these as fallback.
        "technical_object": "TGM100 compresseur tgm100 impact debit debit eau",
        "phenomenon": "tgm100 incertitude incertitude impact incertitude",
        "key_terms_en": ["tgm100", "impact", "debit", "temperature"],
    }


def _units(intent=None):
    intent = intent or _intent()
    evidence = wf._legacy._source_evidence(intent)
    return evidence, wf._v1671_split_evidence(evidence)


def _find_id(units, needle):
    needle = needle.lower()
    for key, value in units.items():
        if needle in value.lower():
            return key
    raise AssertionError((needle, units))


def test_local_identifier_detection_is_title_scoped():
    intent = _intent()
    evidence, _ = _units(intent)
    ids = wf._v1671_safe_explicit_local_identifiers(intent, evidence)
    assert "TGM100" in ids
    assert "300bar" not in ids
    assert not any("10bd6eed" in x for x in ids)


def test_partial_validation_keeps_good_fields_when_one_row_is_bad():
    evidence, units = _units()
    object_id = _find_id(units, "compresseur")
    passage_id = _find_id(units, "débit d’eau")
    payload = {
        "scientific_object": [{"term_en": "compressor", "evidence_ids": [object_id]}],
        "independent_variables": [{"term_en": "water flow rate", "evidence_ids": [passage_id]}],
        "response_variables": [{"term_en": "discharge air temperature", "evidence_ids": ["E999"]}],
        "operating_conditions": [], "phenomena": [], "methods": [], "validation_concepts": [],
        "local_identifiers": [], "ambiguities": [],
    }
    plan, warnings = wf._v1671_resolve_partial(payload, units)
    assert plan["scientific_object"][0]["term_en"] == "compressor"
    assert plan["independent_variables"][0]["term_en"] == "water flow rate"
    assert plan["response_variables"] == []
    assert any("invalid_evidence_ref" in x for x in warnings)


def test_progressive_repair_merges_roles_and_generates_queries(monkeypatch):
    intent = _intent()
    evidence, units = _units(intent)
    object_id = _find_id(units, "compresseur")
    passage_id = _find_id(units, "débit d’eau")

    responses = [
        {
            "scientific_object": [{"term_en": "compressor", "evidence_ids": [object_id]}],
            "independent_variables": [],
            "response_variables": [], "operating_conditions": [], "phenomena": [], "methods": [],
            "validation_concepts": [],
            "local_identifiers": [{"value": "TGM100", "evidence_ids": [object_id]}],
            "ambiguities": [{"source_term": "débit", "resolved_en": "water flow rate", "evidence_ids": [passage_id]}],
        },
        {
            "scientific_object": [], "independent_variables": [{"term_en": "water flow rate", "evidence_ids": [passage_id]}],
            "response_variables": [{"term_en": "compressor discharge air temperature", "evidence_ids": [passage_id]}],
            "operating_conditions": [{"term_en": "compressor suction conditions", "evidence_ids": [passage_id]}],
            "phenomena": [{"term_en": "discharge air temperature variation", "evidence_ids": [passage_id]}],
            "methods": [], "validation_concepts": [], "local_identifiers": [], "ambiguities": [],
        },
    ]

    class FakeClient:
        idx = 0
        def generate(self, **kwargs):
            data = responses[min(FakeClient.idx, len(responses)-1)]
            FakeClient.idx += 1
            return json.dumps(data, ensure_ascii=False)
        def get_last_generation_meta(self):
            return {"provider": "fake", "model": "fake", "total_tokens": 10}

    monkeypatch.setattr(wf._legacy, "_load_llm_client_class", lambda: FakeClient)
    result = wf.run_query_workflow(intent)
    assert result["workflow"]["status"] == wf.STATUS_READY
    assert result["workflow"]["search_allowed"] is True
    assert result["workflow"]["attempts"] <= 2
    plan = result["plan"]
    assert plan["scientific_object"]
    assert plan["independent_variables"]
    assert plan["response_variables"]
    assert result["queries"]
    joined = " ".join(q["query"].lower() for q in result["queries"])
    assert "tgm100" not in joined
    assert "debit" not in joined
    assert "water flow rate" in joined
    assert "compressor" in joined


def test_bad_legacy_intent_is_never_used_as_fallback(monkeypatch):
    class EmptyClient:
        def generate(self, **kwargs):
            return json.dumps({
                "scientific_object": [], "independent_variables": [], "response_variables": [],
                "operating_conditions": [], "phenomena": [], "methods": [], "validation_concepts": [],
                "local_identifiers": [], "ambiguities": [],
            })
        def get_last_generation_meta(self):
            return {"provider": "fake", "model": "fake", "total_tokens": 1}

    monkeypatch.setattr(wf._legacy, "_load_llm_client_class", lambda: EmptyClient)
    result = wf.run_query_workflow(_intent())
    assert result["workflow"]["status"] == wf.STATUS_FAILED
    assert result["workflow"]["search_allowed"] is False
    assert result["queries"] == []
    # The polluted legacy field must not reappear in the grounded plan.
    assert result["plan"].get("scientific_object") == []


def test_evidence_refs_restore_exact_source_text():
    evidence, units = _units()
    passage_id = _find_id(units, "débit d’eau")
    payload = {
        "scientific_object": [], "independent_variables": [
            {"term_en": "cooling water flow rate", "evidence_ids": [passage_id]}
        ],
        "response_variables": [], "operating_conditions": [], "phenomena": [], "methods": [],
        "validation_concepts": [], "local_identifiers": [], "ambiguities": [],
    }
    plan, _ = wf._v1671_resolve_partial(payload, units)
    assert plan["independent_variables"][0]["source_phrase"] == units[passage_id][:220]
