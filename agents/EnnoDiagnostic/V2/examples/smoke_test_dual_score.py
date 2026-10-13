from pathlib import Path
import sys
from types import SimpleNamespace

# Permet d'exécuter directement ce script depuis agents/.../examples
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.cir_scoring import CIRScorer


def ns(**kwargs):
    return SimpleNamespace(**kwargs)


# ------------------------------------------------------------
# Verrous
# ------------------------------------------------------------

entities = [
    ns(
        kind="lock",
        metadata={
            "qualification_status": "VALID_LOCK",
            "has_project_evidence": True,
            "source_roles": ["project_evidence"],
        },
    ),
    ns(
        kind="lock",
        metadata={
            "qualification_status": "REQUIREMENT_ONLY",
            "has_project_evidence": True,
            "source_roles": ["project_evidence"],
        },
    ),
    # Littérature : doit rester hors score d'éligibilité.
    ns(
        kind="lock",
        metadata={
            "qualification_status": "VALID_LOCK",
            "has_project_evidence": False,
            "source_roles": ["literature"],
        },
    ),
]


def extraction(kind, evidence_id, role="project_evidence"):
    return ns(
        kind=kind,
        metadata={"source_role": role},
        evidence=ns(
            quote_verified=True,
            evidence_id=evidence_id,
        ),
    )


# 5 types couverts + 2 résultats.
# Une extraction littérature vérifie qu'elle ne gonfle pas le score.
extractions = [
    extraction("objective", "ev_1"),
    extraction("lock", "ev_2"),
    extraction("method", "ev_3"),
    extraction("parameter", "ev_4"),
    extraction("result", "ev_5"),
    extraction("result", "ev_6"),
    extraction("result", "ev_lit", role="literature"),
]


rnd = ns(
    status="RESEARCH_APPROACH",
    hypothesis_status="present",
    experimentation_status="present",
    parameter_study_status="present",
    iteration_status="present",
    failure_learning_status="present",
    result_analysis_status="present",
    knowledge_gain_status="present",
)


frascati = ns(
    criteria=[
        ns(criterion="uncertainty", status="supported"),
        ns(criterion="novelty", status="supported"),
        ns(criterion="creativity", status="supported"),
        ns(criterion="systematic", status="supported"),
        ns(criterion="transferability", status="supported"),
    ]
)


result = CIRScorer().score(
    entities=entities,
    extractions=extractions,
    rnd=rnd,
    frascati=frascati,
)


print("=" * 80)
print("SMOKE TEST DOUBLE SCORE")
print("=" * 80)

print(
    f"ELIGIBILITE   : {result.eligibility.score}/100 "
    f"- {result.eligibility.level}"
)
print("EXPLICATION   :", result.eligibility.explanation)

print()

print(
    f"DEFENDABILITE : {result.defensibility.score}/100 "
    f"- {result.defensibility.level}"
)
print("EXPLICATION   :", result.defensibility.explanation)

print()

print("RISQUE        :", result.risk_level)
print("POTENTIEL     :", result.potential_level)

print()

payload = result.to_dict()

assert "eligibility" in payload
assert "defensibility" in payload
assert payload["eligibility"]["score"] == 87.5, payload["eligibility"]["score"]
assert payload["defensibility"]["score"] == 100.0, payload["defensibility"]["score"]

# Compatibilité temporaire
assert result.score == result.eligibility.score
assert payload["score"] == payload["eligibility"]["score"]

print("JSON          : eligibility + defensibility présents")
print("COMPATIBILITE : ancien champ score conservé")
print()
print("SMOKE TEST DOUBLE SCORE REUSSI")
