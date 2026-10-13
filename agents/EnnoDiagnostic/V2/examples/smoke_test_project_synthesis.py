from pathlib import Path
import json
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.project_synthesis import (
    ProjectSynthesizer,
    build_project_synthesis_prompt,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import (
    CanonicalEntity,
    EvidenceRef,
    ExtractionItem,
)


def ev(eid, quote, section):
    return EvidenceRef(
        evidence_id=eid,
        document_id="doc_test",
        document_name="test.docx",
        chunk_id="chunk_test",
        quote=quote,
        section_title=section,
        quote_verified=True,
    )


project_objective = ExtractionItem(
    item_id="it_obj",
    kind="objective",
    statement="Produire automatiquement des tests unitaires Java fiables.",
    evidence=ev(
        "ev_obj",
        "L'objectif est de produire automatiquement des tests unitaires Java fiables.",
        "Objectifs",
    ),
    metadata={"source_role": "project_evidence"},
)

project_result = ExtractionItem(
    item_id="it_res",
    kind="result",
    statement="Les essais zero-shot produisent de nombreux tests non compilables.",
    evidence=ev(
        "ev_res",
        "Les essais zero-shot ont produit de nombreux tests non compilables.",
        "Résultats",
    ),
    metadata={"source_role": "project_evidence"},
)

literature = ExtractionItem(
    item_id="it_lit",
    kind="result",
    statement="Une étude externe rapporte des limites de robustesse.",
    evidence=ev(
        "ev_lit",
        "Les auteurs rapportent des limites de robustesse.",
        "Etat de l'art",
    ),
    metadata={"source_role": "literature"},
)

lock = CanonicalEntity(
    entity_id="lock_1",
    kind="lock",
    statement="Difficulté à produire des tests Java compilables et fiables.",
    source_item_ids=["it_lock"],
    evidences=[
        ev(
            "ev_lock",
            "Les tests générés restent souvent incorrects ou non compilables.",
            "Verrous",
        )
    ],
    document_ids=["doc_test"],
    metadata={
        "qualification_status": "VALID_LOCK",
        "qualification_reason": "Résultat négatif observé dans le projet.",
        "suggested_statement": "",
        "source_roles": ["project_evidence"],
        "evidence_source_roles": {
            "ev_lock": "project_evidence",
        },
        "has_project_evidence": True,
    },
)


class FakeLLM:
    def extract_json(self, system_prompt, user_prompt):
        # Vérifie que la provenance est bien séparée dans le prompt.
        assert "ELEMENTS DU PROJET" in user_prompt
        assert "CONTEXTE LITTERATURE" in user_prompt
        assert "ev_obj" in user_prompt
        assert "ev_res" in user_prompt
        assert "ev_lit" in user_prompt
        assert "ev_lock" in user_prompt

        return json.dumps(
            {
                "project_summary": (
                    "Le projet vise à générer automatiquement des tests unitaires Java fiables. "
                    "Les premiers essais montrent encore des tests non compilables, ce qui motive "
                    "des travaux d'adaptation et d'évaluation."
                ),
                "main_objective": "Générer automatiquement des tests unitaires Java fiables.",
                "technical_context": "Génération de tests unitaires Java avec des modèles de langage.",
                "technical_ambition": "Améliorer la fiabilité et la compilabilité des tests générés.",
                "existing_solutions_gap": (
                    "Le dossier indique que les approches actuelles restent insuffisantes "
                    "pour garantir des tests fiables."
                ),
                "knowledge_gap": (
                    "Les conditions permettant d'obtenir des tests fiables restent à établir."
                ),
                "methods_summary": "",
                "results_summary": "Les essais zero-shot produisent encore des tests non compilables.",
                "constraints_summary": "",
                "main_locks_summary": (
                    "Le principal verrou porte sur la génération de tests Java compilables et fiables."
                ),
                "state_of_art_context": (
                    "Une étude externe citée dans le dossier rapporte également des limites de robustesse."
                ),
                "evidence_ids": [
                    "ev_obj",
                    "ev_res",
                    "ev_lock",
                    "id_invente",
                ],
                "missing_information": [],
            },
            ensure_ascii=False,
        )


items = [
    project_objective,
    project_result,
    literature,
]

prompt, stats = build_project_synthesis_prompt(
    items,
    [lock],
)

assert stats["project_evidence_count"] == 2
assert stats["literature_evidence_count"] == 1
assert stats["section_count"] == 2
assert stats["project_lock_count"] == 1

result = ProjectSynthesizer(
    FakeLLM()
).synthesize(
    items,
    [lock],
)

assert result.project_summary
assert result.main_objective
assert result.evidence_ids == [
    "ev_obj",
    "ev_res",
    "ev_lock",
]
assert "id_invente" not in result.evidence_ids

print("=" * 80)
print("SMOKE TEST PROJECT SYNTHESIS")
print("=" * 80)
print("Résumé :", result.project_summary)
print("Objectif :", result.main_objective)
print("Verrous :", result.main_locks_summary)
print("Etat de l'art :", result.state_of_art_context)
print("Preuves projet :", result.project_evidence_count)
print("Preuves littérature :", result.literature_evidence_count)
print("Sections projet :", result.section_count)
print("Evidence IDs validés :", result.evidence_ids)
print()
print("SMOKE TEST PROJECT SYNTHESIS REUSSI")
