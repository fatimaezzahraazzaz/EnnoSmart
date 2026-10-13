from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(REPO_ROOT),
    )

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.chunking import build_chunks
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import ChunkConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.semantic_extractor import SemanticExtractor
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.consolidation import GlobalLockConsolidator
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.qualification import LockQualifier
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.project_synthesis import ProjectSynthesizer
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.rnd_approach import RnDApproachAnalyzer
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.project_frascati import ProjectFrascatiEvaluator
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.cir_scoring import CIRScorer

# La famille est testée si le module actuel est présent.
try:
    from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.lock_family_synthesis import (
        LockFamilySynthesizer,
    )
except ImportError:
    LockFamilySynthesizer = None


DOCUMENT_TEXT = """
CONTEXTE ET OBJECTIF

Le projet vise à déterminer comment générer automatiquement des tests unitaires Java fiables avec des modèles de langage dans des projets industriels complexes.

LIMITES EXISTANTES

Les solutions disponibles produisent encore des tests incorrects ou non compilables lorsqu'elles disposent de peu de contexte sur le projet Java.

VERROU TECHNIQUE

Nos premiers essais zero-shot ont produit des tests incorrects ou non compilables, et la dépendance au contexte du projet reste difficile à maîtriser.

DEMARCHE EXPERIMENTALE

Nous avons comparé plusieurs stratégies de prompting et fait varier la quantité de contexte fournie au modèle.

RESULTATS

La stratégie contextualisée a amélioré la compilabilité des tests générés.
Les essais ont aussi montré que certains cas restent non compilables malgré l'ajout de contexte.

ETAT DE L ART

Une étude externe rapporte également des limites de robustesse des tests générés par des modèles de langage.
""".strip()


def empty_payload():
    return {
        "objectives": [],
        "locks": [],
        "methods": [],
        "parameters": [],
        "results": [],
    }


def obj(statement, quote, role):
    return {
        "statement": statement,
        "evidence_quote": quote,
        "explicitness": "explicit",
        "rationale": "smoke test",
        "technical_object": "",
        "unresolved_question": "",
        "confidence": 1.0,
        "source_role": role,
    }


class FakePipelineLLM:
    """
    Aucun appel API.
    Le faux LLM répond au format attendu par chaque étape.
    Il teste le câblage réel du pipeline local.
    """

    def extract_json(self, system_prompt, user_prompt):
        system = str(system_prompt or "")
        user = str(user_prompt or "")

        if "Tu classes la provenance" in system:
            passages = []
            for passage_id, passage_text in re.findall(
                r"\[(P\d+)\]\s*([^\n]+)", user
            ):
                role = (
                    "literature"
                    if "étude externe" in passage_text.lower()
                    or "solutions disponibles" in passage_text.lower()
                    else "project_evidence"
                )
                passages.append({"id": passage_id, "role": role})
            return {"passages": passages}

        # ----------------------------------------------------
        # SYNTHÈSE PROJET
        # ----------------------------------------------------
        if "SYNTHÈSE GLOBALE" in system or "SYNTHESE GLOBALE" in system:
            evidence_ids = re.findall(
                r"EVIDENCE_ID:\s*([A-Za-z0-9_\-]+)",
                user,
            )

            return json.dumps(
                {
                    "project_summary": (
                        "Le projet étudie la génération automatique de tests unitaires Java "
                        "avec des modèles de langage. Les premiers essais montrent des tests "
                        "incorrects ou non compilables lorsque le contexte projet est insuffisant. "
                        "Plusieurs stratégies de prompting et niveaux de contexte sont comparés. "
                        "Les résultats montrent une amélioration avec la contextualisation, "
                        "mais certains cas restent non maîtrisés."
                    ),
                    "main_objective": (
                        "Déterminer comment générer des tests unitaires Java fiables "
                        "dans des projets industriels complexes."
                    ),
                    "technical_context": (
                        "Génération de tests unitaires Java avec des modèles de langage."
                    ),
                    "technical_ambition": (
                        "Comprendre l'effet du contexte et des stratégies de prompting "
                        "sur la fiabilité des tests générés."
                    ),
                    "existing_solutions_gap": (
                        "Les solutions disponibles produisent encore des tests incorrects "
                        "ou non compilables lorsque le contexte projet est insuffisant."
                    ),
                    "knowledge_gap": (
                        "Les conditions permettant de garantir la compilabilité et la fiabilité "
                        "des tests restent à établir."
                    ),
                    "methods_summary": (
                        "Comparaison de plusieurs stratégies de prompting et variation du contexte."
                    ),
                    "results_summary": (
                        "La contextualisation améliore la compilabilité, mais certains cas "
                        "restent non compilables."
                    ),
                    "constraints_summary": (
                        "Le comportement dépend fortement du contexte du projet Java."
                    ),
                    "main_locks_summary": (
                        "Le verrou principal concerne la génération fiable de tests compilables "
                        "dans des projets Java complexes."
                    ),
                    "state_of_art_context": (
                        "Une étude externe citée rapporte également des limites de robustesse."
                    ),
                    "rnd_relevance_status": "strong",
                    "rnd_relevance_reason": (
                        "Le projet cherche à comprendre expérimentalement les conditions "
                        "permettant de dépasser une limite technique observée."
                    ),
                    "existing_solutions_gap_status": "strong",
                    "existing_solutions_gap_reason": (
                        "Le dossier relie directement les limites des solutions disponibles "
                        "aux échecs observés dans le projet."
                    ),
                    "evidence_ids": evidence_ids[:6],
                    "missing_information": [],
                },
                ensure_ascii=False,
            )

        # ----------------------------------------------------
        # DEMARCHE R&D
        # ----------------------------------------------------
        if "DEMARCHE R&D" in system and "RESEARCH_APPROACH" in system:
            evidence_ids = re.findall(
                r"EVIDENCE_ID:\s*([A-Za-z0-9_\-]+)",
                user,
            )

            return json.dumps(
                {
                    "status": "RESEARCH_APPROACH",
                    "dimensions": {
                        "hypothesis_status": "present",
                        "experimentation_status": "present",
                        "parameter_study_status": "present",
                        "iteration_status": "present",
                        "failure_learning_status": "present",
                        "result_analysis_status": "present",
                        "knowledge_gain_status": "present",
                    },
                    "rationale": (
                        "Le dossier décrit un problème, des essais, une variation de paramètres "
                        "et l'analyse de résultats."
                    ),
                    "strengths": [
                        "Essais comparatifs documentés",
                        "Résultats négatifs et positifs présents",
                    ],
                    "weaknesses": [],
                    "missing_evidence": [],
                    "evidence_ids": evidence_ids[:6],
                },
                ensure_ascii=False,
            )

        # ----------------------------------------------------
        # FRASCATI
        # ----------------------------------------------------
        if "cinq crit" in system.lower() and "frascati" in system.lower():
            evidence_ids = re.findall(
                r"EVIDENCE_ID:\s*([A-Za-z0-9_\-]+)",
                user,
            )

            criteria = []

            for name in [
                "uncertainty",
                "novelty",
                "creativity",
                "systematic",
                "transferability",
            ]:
                criteria.append(
                    {
                        "criterion": name,
                        "status": "supported",
                        "explanation": f"{name} soutenu dans le smoke test.",
                        "evidence_ids": evidence_ids[:2],
                        "missing_evidence": [],
                    }
                )

            return json.dumps(
                {
                    "criteria": criteria,
                    "summary": "Les cinq critères sont soutenus dans le cas synthétique.",
                    "strengths": ["Dossier structuré"],
                    "weaknesses": [],
                },
                ensure_ascii=False,
            )

        # ----------------------------------------------------
        # QUALIFICATION
        # ----------------------------------------------------
        if "qualification des candidats verrous" in system.lower():
            ids = re.findall(
                r"ENTITY_ID:\s*([A-Za-z0-9_\-]+)",
                user,
            )

            qualifications = []

            blocks = re.split(
                r"(?=ENTITY_ID:)",
                user,
            )

            for block in blocks:
                match = re.search(
                    r"ENTITY_ID:\s*([A-Za-z0-9_\-]+)",
                    block,
                )

                if not match:
                    continue

                entity_id = match.group(1)

                role_text = block.lower()

                if "literature" in role_text and "project_evidence" not in role_text:
                    status = "INSUFFICIENT_EVIDENCE"
                    reason = "Signal provenant uniquement de la littérature."
                else:
                    status = "VALID_LOCK"
                    reason = "Limite technique observée directement dans les essais du projet."

                ev_ids = re.findall(
                    r"EVIDENCE_ID:\s*([A-Za-z0-9_\-]+)",
                    block,
                )

                qualifications.append(
                    {
                        "entity_id": entity_id,
                        "status": status,
                        "reason": reason,
                        "suggested_statement": "",
                        "evidence_ids": ev_ids,
                        "missing_evidence": [],
                    }
                )

            return json.dumps(
                {
                    "qualifications": qualifications,
                },
                ensure_ascii=False,
            )

        # ----------------------------------------------------
        # FAMILLES
        # ----------------------------------------------------
        if "famill" in system.lower() and "verrou" in system.lower():
            ids = []

            for value in re.findall(
                r"(?:ENTITY_ID|ID):\s*([A-Za-z0-9_\-]+)",
                user,
            ):
                if value not in ids:
                    ids.append(value)

            if ids:
                return json.dumps(
                    {
                        "families": [
                            {
                                "title": "Fiabilité des tests Java générés",
                                "statement": (
                                    "Difficulté à garantir des tests Java fiables "
                                    "et compilables lorsque le contexte projet est incomplet."
                                ),
                                "member_entity_ids": ids,
                                "rationale": (
                                    "Les candidats décrivent le même problème de fiabilité."
                                ),
                            }
                        ],
                        "unassigned_entity_ids": [],
                    },
                    ensure_ascii=False,
                )

            return json.dumps(
                {
                    "families": [],
                    "unassigned_entity_ids": [],
                }
            )

        # ----------------------------------------------------
        # CONSOLIDATION
        # ----------------------------------------------------
        if "consolidation globale" in system.lower():
            blocks = re.split(
                r"(?=ITEM_ID:)",
                user,
            )

            clusters = []

            for block in blocks:
                id_match = re.search(
                    r"ITEM_ID:\s*([A-Za-z0-9_\-]+)",
                    block,
                )
                statement_match = re.search(
                    r"STATEMENT:\s*(.+)",
                    block,
                )

                if not id_match:
                    continue

                clusters.append(
                    {
                        "canonical_statement": (
                            statement_match.group(1).strip()
                            if statement_match
                            else "Verrou technique"
                        ),
                        "item_ids": [
                            id_match.group(1)
                        ],
                        "reason": "Singleton conservé dans le smoke test.",
                    }
                )

            return json.dumps(
                {
                    "clusters": clusters,
                },
                ensure_ascii=False,
            )

        # ----------------------------------------------------
        # EXTRACTION SEMANTIQUE
        # ----------------------------------------------------
        payload = empty_payload()

        if (
            "Le projet vise à déterminer comment générer automatiquement"
            in user
        ):
            payload["objectives"].append(
                obj(
                    (
                        "Déterminer comment générer automatiquement des tests "
                        "unitaires Java fiables avec des modèles de langage."
                    ),
                    (
                        "Le projet vise à déterminer comment générer automatiquement "
                        "des tests unitaires Java fiables avec des modèles de langage "
                        "dans des projets industriels complexes."
                    ),
                    "project_evidence",
                )
            )

        if (
            "Nos premiers essais zero-shot ont produit des tests incorrects"
            in user
        ):
            payload["locks"].append(
                obj(
                    (
                        "La génération de tests Java reste non fiable lorsque "
                        "le contexte projet est insuffisant."
                    ),
                    (
                        "Nos premiers essais zero-shot ont produit des tests incorrects "
                        "ou non compilables, et la dépendance au contexte du projet "
                        "reste difficile à maîtriser."
                    ),
                    "project_evidence",
                )
            )

        if (
            "Nous avons comparé plusieurs stratégies de prompting"
            in user
        ):
            payload["methods"].append(
                obj(
                    (
                        "Comparer plusieurs stratégies de prompting."
                    ),
                    (
                        "Nous avons comparé plusieurs stratégies de prompting "
                        "et fait varier la quantité de contexte fournie au modèle."
                    ),
                    "project_evidence",
                )
            )

            payload["parameters"].append(
                obj(
                    "Faire varier la quantité de contexte fournie au modèle.",
                    (
                        "Nous avons comparé plusieurs stratégies de prompting "
                        "et fait varier la quantité de contexte fournie au modèle."
                    ),
                    "project_evidence",
                )
            )

        if (
            "La stratégie contextualisée a amélioré la compilabilité"
            in user
        ):
            payload["results"].append(
                obj(
                    "La contextualisation améliore la compilabilité des tests.",
                    (
                        "La stratégie contextualisée a amélioré la compilabilité "
                        "des tests générés."
                    ),
                    "project_evidence",
                )
            )

        if (
            "Les essais ont aussi montré que certains cas restent non compilables"
            in user
        ):
            payload["results"].append(
                obj(
                    (
                        "Certains cas restent non compilables malgré "
                        "l'ajout de contexte."
                    ),
                    (
                        "Les essais ont aussi montré que certains cas restent "
                        "non compilables malgré l'ajout de contexte."
                    ),
                    "project_evidence",
                )
            )

        if (
            "Une étude externe rapporte également des limites de robustesse"
            in user
        ):
            payload["locks"].append(
                obj(
                    (
                        "La littérature rapporte des limites de robustesse "
                        "des tests générés."
                    ),
                    (
                        "Une étude externe rapporte également des limites de robustesse "
                        "des tests générés par des modèles de langage."
                    ),
                    "literature",
                )
            )

        return json.dumps(
            payload,
            ensure_ascii=False,
        )


def main():
    fake_llm = FakePipelineLLM()

    print("=" * 88)
    print("SMOKE TEST ENNODIAGNOSTIC V2 - CHUNK -> DOUBLE SCORE")
    print("=" * 88)

    # --------------------------------------------------------
    # 1. DOCUMENT -> CHUNKS
    # --------------------------------------------------------
    document = DocumentInput(
        document_id="smoke_project",
        name="smoke_project.txt",
        text=DOCUMENT_TEXT,
        declared_mode="raw",
    )

    chunks = build_chunks(
        document,
        ChunkConfig(
            max_chars=700,
            overlap_chars=80,
            context_chars=120,
            min_chunk_chars=40,
        ),
    )

    assert chunks, "Aucun chunk généré."

    print(f"1. CHUNKING               : OK ({len(chunks)} chunk(s))")

    # --------------------------------------------------------
    # 2. EXTRACTION
    # --------------------------------------------------------
    extractions = SemanticExtractor(
        fake_llm
    ).extract(
        chunks
    )

    kinds = {
        item.kind
        for item
        in extractions
    }

    assert {
        "objective",
        "lock",
        "method",
        "parameter",
        "result",
    }.issubset(
        kinds
    ), (
        "Types d'extraction manquants : "
        f"{kinds}"
    )

    assert any(
        item.metadata.get(
            "source_role"
        ) == "project_evidence"
        for item
        in extractions
    ), (
        "source_role=project_evidence n'est pas propagé "
        "par l'extracteur."
    )

    assert any(
        item.metadata.get(
            "source_role"
        ) == "literature"
        for item
        in extractions
    ), (
        "source_role=literature n'est pas propagé "
        "par l'extracteur."
    )

    print(
        f"2. EXTRACTION             : OK ({len(extractions)} élément(s))"
    )

    # --------------------------------------------------------
    # 3. CONSOLIDATION VERROUS
    # --------------------------------------------------------
    entities = (
        GlobalLockConsolidator(
            fake_llm
        ).consolidate(
            extractions
        )
    )

    assert entities, "Aucun verrou consolidé."

    assert any(
        entity.metadata.get(
            "has_project_evidence"
        ) is True
        for entity
        in entities
    ), (
        "La consolidation n'a pas propagé "
        "has_project_evidence=True."
    )

    print(
        f"3. CONSOLIDATION          : OK ({len(entities)} verrou(x))"
    )

    # --------------------------------------------------------
    # 4. QUALIFICATION
    # --------------------------------------------------------
    qualified = LockQualifier(
        fake_llm,
        batch_size=5,
        retry_missing=True,
    ).qualify(
        entities,
        chunks=chunks,
    )

    statuses = {
        entity.metadata.get(
            "qualification_status"
        )
        for entity
        in qualified
    }

    assert "VALID_LOCK" in statuses

    print(
        f"4. QUALIFICATION          : OK ({sorted(statuses)})"
    )

    # --------------------------------------------------------
    # 5. FAMILLES (si module présent)
    # --------------------------------------------------------
    if LockFamilySynthesizer is not None:
        families = LockFamilySynthesizer(
            fake_llm
        ).synthesize(
            qualified
        )

        total_preserved = (
            sum(
                len(
                    family.member_entity_ids
                )
                for family
                in families.families
            )
            + len(
                families.literature_only_entity_ids
            )
            + len(
                families.unassigned_entity_ids
            )
        )

        assert (
            total_preserved
            >= len(qualified)
        ), (
            "La synthèse de familles a perdu au moins "
            "un verrou."
        )

        print(
            f"5. FAMILLES               : OK "
            f"({len(families.families)} famille(s))"
        )

    else:
        print(
            "5. FAMILLES               : NON TESTE "
            "(module lock_family_synthesis absent)"
        )

    # --------------------------------------------------------
    # 6. SYNTHÈSE PROJET
    # --------------------------------------------------------
    synthesis = ProjectSynthesizer(
        fake_llm
    ).synthesize(
        extractions,
        qualified,
    )

    assert synthesis.project_summary
    assert (
        synthesis.rnd_relevance_status
        == "strong"
    )
    assert (
        synthesis.existing_solutions_gap_status
        == "strong"
    )

    print(
        "6. SYNTHESE PROJET        : OK "
        f"(R&D={synthesis.rnd_relevance_status}, "
        f"gap={synthesis.existing_solutions_gap_status})"
    )

    # --------------------------------------------------------
    # 7. DEMARCHE R&D
    # --------------------------------------------------------
    rnd = RnDApproachAnalyzer(
        fake_llm
    ).assess(
        extractions,
        chunks=chunks,
    )

    assert (
        rnd.status
        == "RESEARCH_APPROACH"
    )

    print(
        f"7. DEMARCHE R&D           : OK ({rnd.status})"
    )

    # --------------------------------------------------------
    # 8. FRASCATI
    # --------------------------------------------------------
    frascati = ProjectFrascatiEvaluator(
        fake_llm
    ).assess(
        qualified,
        extractions,
        rnd,
        chunks=chunks,
    )

    assert len(
        frascati.criteria
    ) == 5

    assert all(
        criterion.status
        == "supported"
        for criterion
        in frascati.criteria
    )

    print(
        "8. FRASCATI               : OK (5/5 supported)"
    )

    # --------------------------------------------------------
    # 9. DOUBLE SCORE
    # --------------------------------------------------------
    score = CIRScorer().score(
        entities=qualified,
        extractions=extractions,
        rnd=rnd,
        frascati=frascati,
        synthesis=synthesis,
    )

    payload = score.to_dict()

    assert "eligibility" in payload
    assert "defensibility" in payload

    assert (
        score.eligibility.score
        >= 75
    ), score.eligibility.score

    assert (
        score.defensibility.score
        >= 75
    ), score.defensibility.score

    print(
        f"9. ELIGIBILITE            : OK "
        f"({score.eligibility.score}/100)"
    )

    print(
        f"10. DEFENDABILITE         : OK "
        f"({score.defensibility.score}/100)"
    )

    print()
    print("SYNTHÈSE :")
    print(
        synthesis.project_summary
    )

    print()
    print("ELIGIBILITE :")
    print(
        score.eligibility.explanation
    )

    for component in (
        score.eligibility.components
    ):
        print(
            f"  - {component.name}: "
            f"{component.score}/{component.max_score}"
        )

    print()
    print("DEFENDABILITE :")
    print(
        score.defensibility.explanation
    )

    for component in (
        score.defensibility.components
    ):
        print(
            f"  - {component.name}: "
            f"{component.score}/{component.max_score}"
        )

    print()
    print("=" * 88)
    print(
        "SMOKE TEST PIPELINE COMPLET REUSSI - AUCUN APPEL API"
    )
    print("=" * 88)


if __name__ == "__main__":
    main()
