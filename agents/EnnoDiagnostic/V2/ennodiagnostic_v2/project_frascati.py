from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from .schemas import CanonicalEntity, ExtractionItem, Chunk
from .rnd_approach import RnDApproachAssessment
from .semantic_extractor import LLMClientProtocol, parse_json_payload


CRITERIA = {
    "uncertainty",
    "novelty",
    "creativity",
    "systematic",
    "transferability",
}

STATUSES = {
    "supported",
    "partial",
    "insufficient",
}


@dataclass
class ProjectFrascatiCriterion:
    criterion: str
    status: str
    explanation: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)


@dataclass
class ProjectFrascatiAssessment:
    criteria: List[ProjectFrascatiCriterion] = field(default_factory=list)
    summary: str = ""
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)


FRASCATI_PROJECT_SYSTEM = """
Tu analyses les cinq crit?res Frascati ? l'?chelle d'un PROJET R&D.

Tu travailles uniquement avec les informations pr?sentes
dans le dossier fourni.

Tu ne dois PAS d?cider directement de l'?ligibilit? CIR.
Tu ne dois PAS produire de pourcentage ni de score global.

Pour chacun des cinq crit?res, retourne :

supported
partial
insufficient

============================================================
1. UNCERTAINTY
============================================================

Le projet doit chercher ? lever une incertitude scientifique
ou technique r?ellement non ma?tris?e.

SUPPORTED :
les preuves montrent un probl?me technique non r?solu,
des r?sultats impr?visibles, des limites de m?thodes,
des essais infructueux ou une propri?t? non ma?tris?e.

PARTIAL :
une probl?matique technique cr?dible est identifiable,
mais elle est encore trop g?n?rale ou insuffisamment d?montr?e.

INSUFFICIENT :
le dossier montre seulement des exigences,
de la complexit? ou de l'ing?nierie classique.

Prends en compte les qualifications des verrous fournies.

============================================================
2. NOVELTY
============================================================

Ici, la nouveaut? signifie :
le projet cherche ? produire une connaissance,
une compr?hension ou une capacit? technique
qui n'?tait pas d?j? ma?tris?e dans le contexte d?crit.

Ne confonds pas avec :
- nouveau produit ;
- nouvelle fonctionnalit? commerciale ;
- nouvelle version ;
- changement de technologie.

SUPPORTED :
le dossier d?crit clairement :
- une situation technique de d?part ;
- une limite existante ;
- ce que les travaux cherchent ? d?passer ;
- et une connaissance/capacit? technique nouvelle obtenue
  ou recherch?e.

PARTIAL :
le d?passement technique est plausible mais
la situation initiale, les limites ou le gain de connaissance
sont incompl?tement document?s.

INSUFFICIENT :
la nouveaut? est seulement fonctionnelle,
commerciale ou d?clarative.

============================================================
3. CREATIVITY
============================================================

Le projet doit mobiliser une d?marche de conception,
des hypoth?ses ou des approches techniques qui ne sont pas
une simple application routini?re de m?thodes connues.

SUPPORTED :
plusieurs hypoth?ses, pistes ou combinaisons originales
sont ?tudi?es et justifi?es.

PARTIAL :
une adaptation ou une conception non triviale appara?t,
mais la cr?ativit? technique est peu explicit?e.

INSUFFICIENT :
simple int?gration, configuration ou application directe
d'une solution existante.

============================================================
4. SYSTEMATIC
============================================================

Le projet doit suivre une d?marche organis?e et structur?e.

Utilise fortement l'analyse de d?marche R&D fournie.

SUPPORTED :
on trouve une cha?ne coh?rente :
probl?me -> hypoth?se/piste -> essai -> r?sultat ->
analyse -> adaptation/it?ration.

PARTIAL :
plusieurs ?l?ments existent mais la cha?ne est incompl?te.

INSUFFICIENT :
suite de t?ches de d?veloppement sans d?marche exp?rimentale claire.

============================================================
5. TRANSFERABILITY
============================================================

Le projet doit produire des r?sultats ou connaissances
qui peuvent ?tre document?s, r?utilis?s ou reproduits.

SUPPORTED :
m?thodes, param?tres, protocoles, r?sultats,
conditions et enseignements sont suffisamment document?s.

PARTIAL :
certaines connaissances ou r?sultats sont d?crits,
mais la reproductibilit? ou r?utilisation reste incompl?te.

INSUFFICIENT :
les documents indiquent seulement que la solution fonctionne
sans d?crire comment les r?sultats ont ?t? obtenus
ni ce qui a ?t? appris.

============================================================
REGLES
============================================================

1. Utilise uniquement les preuves fournies.
2. Ne consid?re jamais le mot "verrou" comme une preuve suffisante.
3. Une affirmation sans r?sultat ou preuve doit rester partial
   ou insufficient.
4. Ne cr?e aucune connaissance absente du dossier.
5. Ne donne aucun score num?rique.
6. Pour chaque crit?re, explique bri?vement pourquoi.
7. Indique les informations manquantes pour renforcer le dossier.

Retourne uniquement :

{
  "criteria": [
    {
      "criterion": "uncertainty",
      "status": "supported|partial|insufficient",
      "explanation": "...",
      "evidence_ids": [],
      "missing_evidence": []
    },
    {
      "criterion": "novelty",
      "status": "supported|partial|insufficient",
      "explanation": "...",
      "evidence_ids": [],
      "missing_evidence": []
    },
    {
      "criterion": "creativity",
      "status": "supported|partial|insufficient",
      "explanation": "...",
      "evidence_ids": [],
      "missing_evidence": []
    },
    {
      "criterion": "systematic",
      "status": "supported|partial|insufficient",
      "explanation": "...",
      "evidence_ids": [],
      "missing_evidence": []
    },
    {
      "criterion": "transferability",
      "status": "supported|partial|insufficient",
      "explanation": "...",
      "evidence_ids": [],
      "missing_evidence": []
    }
  ],
  "summary": "...",
  "strengths": [],
  "weaknesses": []
}
""".strip()


def _build_prompt(
    entities: Iterable[CanonicalEntity],
    extractions: Iterable[ExtractionItem],
    rnd: RnDApproachAssessment,
    chunks: Optional[Iterable[Chunk]] = None,
):

    entities = list(entities)
    extractions = list(extractions)

    parts = []

    parts.append("=== VERROUS QUALIFIES ===")

    for entity in entities:
        if entity.kind != "lock":
            continue

        parts.append(
            "\n".join([
                f"ENTITY_ID: {entity.entity_id}",
                f"VERROU: {entity.statement}",
                f"QUALIFICATION: {entity.metadata.get('qualification_status')}",
                f"RAISON: {entity.metadata.get('qualification_reason')}",
                f"REFORMULATION: {entity.metadata.get('suggested_statement')}",
            ])
        )

    parts.append("\n=== DEMARCHE R&D ===")

    parts.append(
        "\n".join([
            f"STATUS: {rnd.status}",
            f"HYPOTHESES: {rnd.hypothesis_status}",
            f"EXPERIMENTATION: {rnd.experimentation_status}",
            f"PARAMETRES: {rnd.parameter_study_status}",
            f"ITERATIONS: {rnd.iteration_status}",
            f"ECHECS_APPRENTISSAGE: {rnd.failure_learning_status}",
            f"ANALYSE_RESULTATS: {rnd.result_analysis_status}",
            f"GAIN_CONNAISSANCE: {rnd.knowledge_gain_status}",
            f"RAISON: {rnd.rationale}",
        ])
    )

    parts.append("\n=== EXTRACTIONS DU DOSSIER ===")

    for item in extractions:
        quote = str(item.evidence.quote or "").strip()

        if len(quote) > 1200:
            quote = quote[:1200] + " [...]"

        parts.append(
            "\n".join([
                f"EVIDENCE_ID: {item.evidence.evidence_id}",
                f"TYPE: {item.kind}",
                f"STATEMENT: {item.statement}",
                f"PREUVE: {quote}",
            ])
        )

    return "\n\n--------------------\n\n".join(parts)


class ProjectFrascatiEvaluator:
    def __init__(
        self,
        llm: Optional[LLMClientProtocol],
    ):
        self.llm = llm

    def assess(
        self,
        entities: Iterable[CanonicalEntity],
        extractions: Iterable[ExtractionItem],
        rnd: RnDApproachAssessment,
        chunks: Optional[Iterable[Chunk]] = None,
    ) -> ProjectFrascatiAssessment:

        if self.llm is None:
            return ProjectFrascatiAssessment(
                summary="Analyse Frascati non ex?cut?e."
            )

        try:
            raw = self.llm.extract_json(
                FRASCATI_PROJECT_SYSTEM,
                _build_prompt(
                    entities,
                    extractions,
                    rnd,
                    chunks=chunks,
                ),
            )

            data = parse_json_payload(raw)

        except Exception as exc:
            return ProjectFrascatiAssessment(
                summary=(
                    "Erreur pendant l'analyse Frascati : "
                    f"{type(exc).__name__}"
                )
            )

        valid_evidence_ids = {
            item.evidence.evidence_id
            for item in extractions
        }

        results = []
        seen = set()

        for raw_criterion in data.get("criteria") or []:

            criterion = str(
                raw_criterion.get("criterion") or ""
            ).strip().lower()

            if criterion not in CRITERIA:
                continue

            if criterion in seen:
                continue

            seen.add(criterion)

            status = str(
                raw_criterion.get("status") or ""
            ).strip().lower()

            if status not in STATUSES:
                status = "insufficient"

            evidence_ids = [
                str(value)
                for value in (
                    raw_criterion.get("evidence_ids") or []
                )
                if str(value) in valid_evidence_ids
            ]

            missing = [
                str(value).strip()
                for value in (
                    raw_criterion.get("missing_evidence") or []
                )
                if str(value).strip()
            ]

            results.append(
                ProjectFrascatiCriterion(
                    criterion=criterion,
                    status=status,
                    explanation=str(
                        raw_criterion.get("explanation") or ""
                    ).strip(),
                    evidence_ids=evidence_ids,
                    missing_evidence=missing,
                )
            )

        # Si le LLM oublie un crit?re, on ne l'invente pas :
        # on le marque insuffisant.
        for criterion in CRITERIA:
            if criterion not in seen:
                results.append(
                    ProjectFrascatiCriterion(
                        criterion=criterion,
                        status="insufficient",
                        explanation="Crit?re absent de la r?ponse du mod?le.",
                    )
                )

        return ProjectFrascatiAssessment(
            criteria=results,
            summary=str(
                data.get("summary") or ""
            ).strip(),
            strengths=[
                str(x).strip()
                for x in data.get("strengths") or []
                if str(x).strip()
            ],
            weaknesses=[
                str(x).strip()
                for x in data.get("weaknesses") or []
                if str(x).strip()
            ],
        )
