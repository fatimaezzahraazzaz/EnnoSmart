from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from .schemas import Chunk, ExtractionItem, RnDStep
from .semantic_extractor import LLMClientProtocol, parse_json_payload


ALLOWED_STATUSES = {
    "RESEARCH_APPROACH",
    "MIXED_APPROACH",
    "ENGINEERING_ONLY",
    "INSUFFICIENT_EVIDENCE",
}


@dataclass
class RnDApproachAssessment:
    status: str

    hypothesis_status: str = "unknown"
    experimentation_status: str = "unknown"
    parameter_study_status: str = "unknown"
    iteration_status: str = "unknown"
    failure_learning_status: str = "unknown"
    result_analysis_status: str = "unknown"
    knowledge_gain_status: str = "unknown"

    rationale: str = ""
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)

    # Preuves utilisées pour l'évaluation R&D.
    # Elles doivent toujours correspondre à des extractions du projet.
    evidence_ids: List[str] = field(default_factory=list)

    # Compatibilité avec l'ancien frontend / anciens exports.
    # Depuis cette architecture, rnd_approach.py N'EXTRAIT PLUS la démarche.
    # Les nouvelles étapes sont exposées dans project_synthesis.demarches.
    steps: List[RnDStep] = field(default_factory=list)
    step_evidences: List[dict] = field(default_factory=list)


RND_APPROACH_SYSTEM = """
Tu évalues uniquement la FORCE DE LA DEMARCHE R&D réellement démontrée
dans un dossier technique.

Tu N'EXTRAIS PAS les étapes de démarche projet.
Les étapes sont déjà extraites en amont par l'extracteur sémantique
sous TYPE=demarche.

Ta tâche est uniquement de répondre à la question :

"Les travaux REALISES montrent-ils une démarche structurée visant
à comprendre ou lever une incertitude scientifique ou technique,
ou principalement une démarche d'ingénierie classique ?"

============================================================
REGLE FONDAMENTALE DE TEMPORALITE
============================================================

Les extractions peuvent contenir :

TEMPORAL_STATUS: planned
TEMPORAL_STATUS: in_progress
TEMPORAL_STATUS: performed
TEMPORAL_STATUS: unclear

Une démarche planned peut décrire un bon plan de travail,
mais elle ne prouve PAS que l'expérimentation R&D a été réalisée.

Une démarche in_progress ne doit soutenir une dimension R&D que pour
ce qui est explicitement déjà effectué dans la preuve.

Une démarche performed peut soutenir l'évaluation si la preuve montre
réellement l'activité correspondante.

Ne transforme jamais une intention, un objectif, un verrou ou une étape
future en travail expérimental réalisé.

============================================================
STATUT GLOBAL
============================================================

RESEARCH_APPROACH

Utilise RESEARCH_APPROACH lorsque plusieurs preuves montrent
une démarche R&D réellement exécutée, par exemple :

- hypothèse technique réellement mise à l'épreuve ;
- essai, expérimentation ou prototype ;
- variation de paramètres ;
- comparaison expérimentale ;
- résultat analysé ;
- échec ou limite observé ;
- adaptation après résultat ;
- acquisition de connaissance technique.

Tous ces éléments ne sont pas obligatoires,
mais une chaîne expérimentale réelle doit être documentée.

------------------------------------------------------------

MIXED_APPROACH

Utilise MIXED_APPROACH lorsque le projet contient à la fois :

- une activité R&D / expérimentale réellement démontrée ;

ET

- une part importante de développement, intégration,
  configuration ou industrialisation classique.

------------------------------------------------------------

ENGINEERING_ONLY

Utilise ENGINEERING_ONLY lorsque les travaux réellement décrits
relèvent essentiellement de l'ingénierie classique :

- développement connu ;
- intégration ;
- migration ;
- configuration ;
- adaptation à une API ;
- mise en production ;
- optimisation routinière ;
- correction de bugs ;
- application directe d'une méthode connue ;

sans démarche expérimentale visant à lever une incertitude.

------------------------------------------------------------

INSUFFICIENT_EVIDENCE

Utilise INSUFFICIENT_EVIDENCE lorsque :

- le projet peut avoir une dimension R&D ;
- des travaux futurs sont prévus ;
- des verrous ou objectifs existent ;

mais les sources ne démontrent pas suffisamment
ce qui a réellement été expérimenté / analysé.

============================================================
DIMENSIONS R&D
============================================================

Pour chaque dimension :
present | partial | absent | unknown

CRITIQUE :
"present" signifie qu'il existe une preuve de travaux REALISES.
Une intention future ne suffit jamais.

1. hypothesis_status

Existe-t-il une hypothèse TECHNIQUE TESTABLE réellement formulée
et mise à l'épreuve ?

Un verrou, un objectif, une question, une intention
ou "étudier l'IA" n'est PAS une hypothèse.

2. experimentation_status

Existe-t-il des essais, prototypes, expérimentations
ou comparaisons réellement exécutés ?

3. parameter_study_status

Des paramètres ou configurations ont-ils réellement été étudiés ?

4. iteration_status

Existe-t-il une boucle réelle :
essai -> résultat -> adaptation -> nouvel essai ?

5. failure_learning_status

Des échecs / limites observés ont-ils réellement servi
à réorienter les travaux ?

6. result_analysis_status

Les résultats obtenus sont-ils réellement analysés techniquement ?

7. knowledge_gain_status

Les travaux réalisés produisent-ils une compréhension
ou connaissance technique nouvelle pour le projet ?

============================================================
ROLE DES EXTRACTIONS TYPE=demarche
============================================================

TYPE=demarche décrit les étapes du projet.

Utilise ces éléments pour comprendre l'organisation des travaux,
mais vérifie TOUJOURS TEMPORAL_STATUS.

Exemples :

TYPE=demarche
TEMPORAL_STATUS=planned
STATEMENT=Tester et valider la solution

=> cela NE démontre PAS une expérimentation réalisée.

TYPE=demarche
TEMPORAL_STATUS=performed
STATEMENT=Comparer trois modèles sur le même jeu de validation
PREUVE=Nous avons comparé ...

=> peut soutenir experimentation_status si la preuve l'établit réellement.

============================================================
ATTRIBUTION PROJET / LITTERATURE
============================================================

SOURCE_ROLE peut valoir :
project_evidence | literature | contextual | unknown.

- project_evidence peut soutenir ce qui a été fait par le projet,
  sous réserve de la temporalité.
- literature contextualise, mais ne prouve jamais une réalisation du projet.
- contextual ne prouve pas une réalisation R&D.
- unknown reste neutre.

Pour evidence_ids :
utilise uniquement les EVIDENCE_ID fournis dans les EXTRACTIONS
avec SOURCE_ROLE=project_evidence.

============================================================
SORTIE JSON STRICTE
============================================================

Retourne uniquement :

{
  "status": "RESEARCH_APPROACH|MIXED_APPROACH|ENGINEERING_ONLY|INSUFFICIENT_EVIDENCE",

  "dimensions": {
    "hypothesis_status": "present|partial|absent|unknown",
    "experimentation_status": "present|partial|absent|unknown",
    "parameter_study_status": "present|partial|absent|unknown",
    "iteration_status": "present|partial|absent|unknown",
    "failure_learning_status": "present|partial|absent|unknown",
    "result_analysis_status": "present|partial|absent|unknown",
    "knowledge_gain_status": "present|partial|absent|unknown"
  },

  "rationale": "...",
  "strengths": ["..."],
  "weaknesses": ["..."],
  "missing_evidence": ["..."],
  "evidence_ids": ["ev_..."]
}
""".strip()


def _build_prompt(
    extractions: Iterable[ExtractionItem],
) -> str:
    items = list(extractions)

    blocks = []

    for item in items:
        quote = str(
            item.evidence.quote or ""
        ).strip()

        if len(quote) > 1200:
            quote = quote[:1200] + " [...]"

        temporal_status = str(
            getattr(item, "temporal_status", "")
            or (item.metadata or {}).get(
                "temporal_status",
                "unclear",
            )
            or "unclear"
        ).strip().lower()

        sequence_position = getattr(
            item,
            "sequence_position",
            None,
        )

        lines = [
            f"ITEM_ID: {item.item_id}",
            f"EVIDENCE_ID: {item.evidence.evidence_id}",
            f"TYPE: {item.kind}",
            f"SOURCE_ROLE: {(item.metadata or {}).get('source_role', 'unknown')}",
            f"TEMPORAL_STATUS: {temporal_status}",
        ]

        if sequence_position is not None:
            lines.append(
                f"SEQUENCE_POSITION: {sequence_position}"
            )

        lines.extend(
            [
                f"DOCUMENT: {item.evidence.document_name}",
                f"SECTION: {item.evidence.section_title}",
                f"STATEMENT: {item.statement}",
                f"PREUVE: {quote}",
            ]
        )

        blocks.append(
            "\n".join(lines)
        )

    return (
        "============================================================\n"
        "EXTRACTIONS DU PROJET - EVALUATION R&D UNIQUEMENT\n"
        "============================================================\n\n"
        + (
            "\n\n--------------------\n\n".join(blocks)
            if blocks
            else "Aucun élément extrait."
        )
        + "\n\nIMPORTANT : n'extrais aucune étape supplémentaire. "
        "Les TYPE=demarche sont déjà les étapes extraites en amont."
    )


class RnDApproachAnalyzer:
    def __init__(
        self,
        llm: Optional[LLMClientProtocol],
    ):
        self.llm = llm

    def assess(
        self,
        extractions: Iterable[ExtractionItem],
        chunks: Optional[Iterable[Chunk]] = None,
    ) -> RnDApproachAssessment:
        """
        `chunks` est conservé dans la signature pour compatibilité avec
        full_runtime.py, mais n'est plus utilisé pour extraire des étapes.
        """
        del chunks

        extractions = list(extractions)

        if not extractions:
            return RnDApproachAssessment(
                status="INSUFFICIENT_EVIDENCE",
                rationale=(
                    "Aucun élément exploitable du projet n'est disponible."
                ),
                missing_evidence=[
                    "Preuves de travaux R&D réellement exécutés."
                ],
            )

        if self.llm is None:
            return RnDApproachAssessment(
                status="INSUFFICIENT_EVIDENCE",
                rationale="Analyse LLM non exécutée.",
            )

        try:
            raw = self.llm.extract_json(
                RND_APPROACH_SYSTEM,
                _build_prompt(extractions),
            )
            data = parse_json_payload(raw)

        except Exception as exc:
            return RnDApproachAssessment(
                status="INSUFFICIENT_EVIDENCE",
                rationale=(
                    "Erreur pendant l'analyse de la démarche R&D : "
                    f"{type(exc).__name__}"
                ),
            )

        status = str(
            data.get("status") or ""
        ).strip().upper()

        if status not in ALLOWED_STATUSES:
            status = "INSUFFICIENT_EVIDENCE"

        dimensions = data.get(
            "dimensions"
        ) or {}

        provenance_available = any(
            str(
                (item.metadata or {}).get(
                    "source_role"
                ) or ""
            ).strip()
            for item in extractions
        )

        if provenance_available:
            valid_project_ids = {
                item.evidence.evidence_id
                for item in extractions
                if item.evidence.evidence_id
                and str(
                    (item.metadata or {}).get(
                        "source_role"
                    ) or ""
                ).strip().lower()
                == "project_evidence"
            }
        else:
            # Compatibilité avec les anciens snapshots sans source_role.
            valid_project_ids = {
                item.evidence.evidence_id
                for item in extractions
                if item.evidence.evidence_id
            }

        evidence_ids = []
        seen_global_ids = set()

        for value in (
            data.get("evidence_ids") or []
        ):
            evidence_id = str(
                value or ""
            ).strip()

            if (
                evidence_id
                and evidence_id in valid_project_ids
                and evidence_id not in seen_global_ids
            ):
                seen_global_ids.add(
                    evidence_id
                )
                evidence_ids.append(
                    evidence_id
                )

        def dim(name: str) -> str:
            value = str(
                dimensions.get(name)
                or "unknown"
            ).strip().lower()

            if value not in {
                "present",
                "partial",
                "absent",
                "unknown",
            }:
                return "unknown"

            return value

        return RnDApproachAssessment(
            status=status,

            hypothesis_status=dim(
                "hypothesis_status"
            ),
            experimentation_status=dim(
                "experimentation_status"
            ),
            parameter_study_status=dim(
                "parameter_study_status"
            ),
            iteration_status=dim(
                "iteration_status"
            ),
            failure_learning_status=dim(
                "failure_learning_status"
            ),
            result_analysis_status=dim(
                "result_analysis_status"
            ),
            knowledge_gain_status=dim(
                "knowledge_gain_status"
            ),

            rationale=str(
                data.get("rationale") or ""
            ).strip(),

            strengths=[
                str(x).strip()
                for x in (
                    data.get("strengths") or []
                )
                if str(x).strip()
            ],

            weaknesses=[
                str(x).strip()
                for x in (
                    data.get("weaknesses") or []
                )
                if str(x).strip()
            ],

            missing_evidence=[
                str(x).strip()
                for x in (
                    data.get("missing_evidence")
                    or []
                )
                if str(x).strip()
            ],

            evidence_ids=evidence_ids,

            # Compatibilité uniquement : plus aucune extraction ici.
            steps=[],
            step_evidences=[],
        )
