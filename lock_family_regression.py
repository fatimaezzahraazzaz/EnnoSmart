from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.lock_family_synthesis import (
    LockFamilySynthesizer,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.semantic_extractor import (
    CallableLLMAdapter,
)


def _record(entity_id, status, quote):
    return {
        "entity_id": entity_id,
        "kind": "lock",
        "statement": quote,
        "metadata": {
            "qualification_status": status,
            "source_roles": ["project_evidence"],
        },
        "evidences": [{
            "evidence_id": f"evidence_{entity_id}",
            "document_id": "document_1",
            "quote": quote,
        }],
    }


def test_explicit_source_locks_do_not_create_families():
    records = [
        _record("v1", "REQUIREMENT_ONLY", "Le premier verrou porte sur le cadrage."),
        _record("v2", "VALID_LOCK", "Le deuxième verrou concerne la fiabilité."),
        _record("v3", "REQUIREMENT_ONLY", "Le troisième verrou décrit une exigence."),
        _record("v4", "ENGINEERING_DIFFICULTY", "Le quatrième verrou décrit une intégration."),
        _record("v5", "NEEDS_REFORMULATION", "Le dernier verrou concerne la validation."),
    ]

    def unexpected_llm(_system_prompt, _user_prompt):
        raise AssertionError("La source explicite ne requiert pas de regroupement.")

    result = LockFamilySynthesizer(
        CallableLLMAdapter(unexpected_llm)
    ).synthesize_records(records)
    assert result.families == []
    assert set(result.unassigned_entity_ids) == {"v1", "v2", "v3", "v4", "v5"}
    assert result.project_entity_count == 5


def test_genuine_multi_member_family_is_preserved():
    records = [
        _record("v1", "VALID_LOCK", "Les tests générés ne compilent pas."),
        _record("v2", "NEEDS_REFORMULATION", "Les tests générés échouent à l'exécution."),
    ]

    def grouping_llm(_system_prompt, _user_prompt):
        return {"families": [{
            "title": "Fiabilité des tests générés",
            "statement": "Les tests ne sont pas exploitables.",
            "member_entity_ids": ["v1", "v2"],
            "rationale": "Même problème de fiabilité.",
        }]}

    result = LockFamilySynthesizer(
        CallableLLMAdapter(grouping_llm)
    ).synthesize_records(records)
    assert len(result.families) == 1
    assert result.families[0].member_entity_ids == ["v1", "v2"]


def test_single_candidate_is_not_a_family():
    record = _record("v1", "VALID_LOCK", "Une incertitude technique autonome.")

    def singleton_llm(_system_prompt, _user_prompt):
        return {"families": [{
            "title": "Famille artificielle",
            "member_entity_ids": ["v1"],
        }]}

    result = LockFamilySynthesizer(
        CallableLLMAdapter(singleton_llm)
    ).synthesize_records([record])
    assert result.families == []
    assert result.unassigned_entity_ids == ["v1"]
