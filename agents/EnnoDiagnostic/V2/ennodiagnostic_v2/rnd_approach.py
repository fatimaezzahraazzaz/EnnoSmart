from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .schemas import ExtractionItem, Chunk
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
    evidence_ids: List[str] = field(default_factory=list)


RND_APPROACH_SYSTEM = """
Tu analyses la DEMARCHE R&D d?crite dans un dossier technique.

Tu travailles uniquement ? partir des documents du projet.

Ta question n'est PAS :
"Le projet est-il innovant ?"

Ta question est :

"Les travaux d?crits montrent-ils une d?marche structur?e visant
? comprendre ou lever une incertitude scientifique ou technique,
ou principalement une d?marche d'ing?nierie classique ?"

============================================================
RESEARCH_APPROACH
============================================================

Utilise RESEARCH_APPROACH lorsque plusieurs ?l?ments montrent
une d?marche r?elle de recherche ou d'exp?rimentation :

- probl?me ou incertitude technique identifi? ;
- hypoth?ses ou pistes techniques ;
- essais / exp?rimentations / prototypes ;
- variation de param?tres ;
- comparaison de plusieurs approches ;
- r?sultats analys?s ;
- ?checs ou limites observ?s ;
- modifications de l'approche apr?s les r?sultats ;
- acquisition de connaissances techniques nouvelles.

Tous ces ?l?ments ne sont pas obligatoires simultan?ment,
mais il doit exister une cha?ne de raisonnement exp?rimentale cr?dible.

============================================================
MIXED_APPROACH
============================================================

Utilise MIXED_APPROACH lorsque le projet contient ? la fois :

- une vraie activit? d'exp?rimentation ou d'analyse technique ;

ET

- une part importante de d?veloppement, int?gration,
  param?trage, adaptation ou industrialisation classique.

============================================================
ENGINEERING_ONLY
============================================================

Utilise ENGINEERING_ONLY lorsque les documents d?crivent essentiellement :

- d?veloppement d'une fonctionnalit? connue ;
- int?gration de composants ;
- migration ;
- configuration ;
- adaptation ? une API ;
- mise en production ;
- optimisation classique ;
- correction de bugs ;
- application de m?thodes connues ;

sans v?ritable d?marche exp?rimentale destin?e ? lever
une incertitude scientifique ou technique.

La complexit?, le temps pass? ou le nombre de d?veloppements
ne suffisent pas ? constituer une d?marche R&D.

============================================================
INSUFFICIENT_EVIDENCE
============================================================

Utilise INSUFFICIENT_EVIDENCE lorsque le projet peut avoir
une dimension R&D, mais que les documents disponibles ne permettent
pas de comprendre r?ellement la d?marche suivie.

============================================================
DIMENSIONS A ANALYSER
============================================================

Pour chaque dimension, retourne :

present
partial
absent
unknown

1. hypothesis_status
Existe-t-il des hypoth?ses, pistes ou choix techniques ? tester ?

2. experimentation_status
Existe-t-il des essais, prototypes, exp?rimentations ou comparaisons ?

3. parameter_study_status
Des param?tres, configurations ou conditions sont-ils ?tudi?s ?

4. iteration_status
Observe-t-on une boucle :
essai -> r?sultat -> adaptation -> nouvel essai ?

5. failure_learning_status
Des ?checs, limites ou r?sultats insuffisants sont-ils analys?s
et utilis?s pour orienter les travaux ?

6. result_analysis_status
Les r?sultats sont-ils analys?s techniquement,
au-del? de "?a marche / ?a ne marche pas" ?

7. knowledge_gain_status
Les travaux produisent-ils une compr?hension,
une caract?risation ou une connaissance technique nouvelle
pour le projet ?

============================================================
REGLES
============================================================

1. Ne d?duis pas une d?marche R&D uniquement parce que
   le document parle d'un "verrou".

2. Ne consid?re pas un simple test de validation produit
   comme une exp?rimentation R&D.

3. Une succession de t?ches de d?veloppement
   n'est pas une d?marche exp?rimentale.

4. Les essais doivent servir ? comprendre ou r?duire
   une incertitude technique.

5. Les preuves peuvent ?tre r?parties dans plusieurs documents.

6. N'invente jamais une ?tape qui n'est pas d?crite.

7. Si des ?l?ments sont absents du dossier,
   indique-les dans missing_evidence.

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
  "evidence_ids": ["..."]
}
""".strip()


def _build_prompt(
    extractions: Iterable[ExtractionItem],
    chunks: Optional[Iterable[Chunk]] = None,
) -> str:

    items = list(extractions)

    chunk_by_id: Dict[str, Chunk] = {}

    if chunks:
        chunk_by_id = {
            chunk.chunk_id: chunk
            for chunk in chunks
        }

    blocks = []

    # Tous les types sont int?ressants ici :
    # objectif, verrou, m?thode, param?tre, r?sultat.
    for item in items:

        quote = str(
            item.evidence.quote or ""
        ).strip()

        if len(quote) > 1200:
            quote = quote[:1200] + " [...]"

        context = ""

        chunk = chunk_by_id.get(
            item.evidence.chunk_id
        )

        if chunk is not None:
            context = str(
                chunk.text or ""
            ).strip()

            if len(context) > 1800:
                context = context[:1800] + " [...]"

        blocks.append(
            "\n".join(
                [
                    f"ITEM_ID: {item.item_id}",
                    f"TYPE: {item.kind}",
                    f"DOCUMENT: {item.evidence.document_name}",
                    f"STATEMENT: {item.statement}",
                    f"PREUVE: {quote}",
                    f"CONTEXTE: {context}",
                ]
            )
        )

    return (
        "ELEMENTS EXTRAITS DU DOSSIER :\n\n"
        + "\n\n--------------------\n\n".join(blocks)
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

        extractions = list(extractions)

        if not extractions:
            return RnDApproachAssessment(
                status="INSUFFICIENT_EVIDENCE",
                rationale="Aucun ?l?ment exploitable dans le dossier.",
                missing_evidence=[
                    "M?thodes, essais, param?tres et r?sultats du projet."
                ],
            )

        if self.llm is None:
            return RnDApproachAssessment(
                status="INSUFFICIENT_EVIDENCE",
                rationale="Analyse LLM non ex?cut?e.",
            )

        try:
            raw = self.llm.extract_json(
                RND_APPROACH_SYSTEM,
                _build_prompt(
                    extractions,
                    chunks=chunks,
                ),
            )

            data = parse_json_payload(raw)

        except Exception as exc:
            return RnDApproachAssessment(
                status="INSUFFICIENT_EVIDENCE",
                rationale=(
                    "Erreur pendant l'analyse de la d?marche R&D : "
                    f"{type(exc).__name__}"
                ),
            )

        status = str(
            data.get("status") or ""
        ).strip().upper()

        if status not in ALLOWED_STATUSES:
            status = "INSUFFICIENT_EVIDENCE"

        dimensions = data.get("dimensions") or {}

        valid_ids = {
            item.evidence.evidence_id
            for item in extractions
        }

        evidence_ids = [
            str(value)
            for value in (
                data.get("evidence_ids") or []
            )
            if str(value) in valid_ids
        ]

        def dim(name):
            value = str(
                dimensions.get(name) or "unknown"
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
                    data.get("missing_evidence") or []
                )
                if str(x).strip()
            ],

            evidence_ids=evidence_ids,
        )
