from __future__ import annotations

from dataclasses import replace
from typing import Iterable, List, Optional

from .schemas import CanonicalEntity, Chunk
from .semantic_extractor import LLMClientProtocol, parse_json_payload


ALLOWED_STATUSES = {
    "VALID_LOCK",
    "NEEDS_REFORMULATION",
    "REQUIREMENT_ONLY",
    "ENGINEERING_DIFFICULTY",
    "INSUFFICIENT_EVIDENCE",
}


QUALIFICATION_SYSTEM = """
Tu es l'?tape de qualification des candidats verrous d'EnnoDiagnostic.

Cette ?tape est ind?pendante du domaine :
logiciel, IA, m?canique, ?lectronique, mat?riaux, chimie,
proc?d?s industriels, ?nergie, etc.

Les candidats ont d?j? ?t? extraits et consolid?s.

Tu ne dois :
- ni supprimer un candidat ;
- ni fusionner des candidats ;
- ni inventer une difficult? absente des sources ;
- ni d?cider de l'?ligibilit? CIR finale.

Tu dois uniquement qualifier ce que les sources permettent d'affirmer.

============================================================
PRINCIPE CENTRAL
============================================================

Ne confonds jamais :

A. ce que le syst?me DOIT faire ;
B. ce qui est difficile ? impl?menter ;
C. ce qui constitue r?ellement une incertitude technique ;
D. une vraie probl?matique technique qui est encore mal formul?e.

Le fait que le document utilise les mots :
"verrou", "probl?me", "enjeu", "complexe", "critique",
"innovation", "difficult?"
n'est jamais une preuve suffisante.

============================================================
VALID_LOCK
============================================================

Utilise VALID_LOCK lorsque les sources montrent concr?tement
une incertitude scientifique ou technique r?ellement rencontr?e.

Il doit exister des indices concrets tels que :

- comportement instable ou non reproductible observ? ;
- ph?nom?nes non ma?tris?s ;
- r?sultats incompatibles ou impr?visibles ;
- essais infructueux ;
- r?glages test?s sans r?soudre le probl?me ;
- limites d?montr?es d'une m?thode existante ;
- propri?t? technique qu'on ne parvient pas ? garantir ;
- cas restant techniquement non r?solus ;
- absence de m?thode connue satisfaisant simultan?ment
  les contraintes recherch?es.

VALID_LOCK doit ?tre fond? sur les preuves du projet,
pas uniquement sur la connaissance g?n?rale du domaine.

============================================================
NEEDS_REFORMULATION
============================================================

Utilise NEEDS_REFORMULATION lorsqu'une probl?matique technique
sp?cifique et cr?dible est r?ellement identifiable dans les sources,
mais qu'elle est formul?e trop g?n?ralement ou insuffisamment reli?e
au contexte concret du projet.

Exemples g?n?riques :

- un ph?nom?ne technique probl?matique est identifi?,
  mais ses conditions d'apparition ne sont pas d?taill?es ;

- un comportement difficile ? ma?triser est clairement mentionn?,
  mais les essais ou limites des approches existantes ne sont pas d?crits ;

- le candidat est formul? comme un objectif
  ("ma?triser...", "garantir...", "r?duire...")
  alors que la source r?v?le derri?re cet objectif
  une vraie probl?matique technique ;

- une difficult? de v?rification ou validation para?t techniquement
  non triviale, mais le dossier ne pr?cise pas encore suffisamment
  ce qui emp?che les m?thodes existantes de la r?soudre.

NEEDS_REFORMULATION signifie :

"Le probl?me technique est pertinent et identifiable,
mais le dossier doit mieux expliquer ce qui reste non ma?tris?."

Dans ce cas :
- suggested_statement doit proposer une formulation plus pr?cise ;
- missing_evidence doit indiquer ce qu'il faut demander au consultant.

============================================================
REQUIREMENT_ONLY
============================================================

Utilise REQUIREMENT_ONLY si la source exprime seulement
une propri?t? attendue ou une contrainte.

Exemples :

- respecter un temps de r?ponse ;
- assurer la s?curit? ;
- ?tre robuste ;
- garantir une pr?cision ;
- valider les r?sultats ;
- assurer une tra?abilit? ;
- r?sister ? une temp?rature ou ? une charge donn?e.

Une exigence importante, difficile ou critique
n'est pas automatiquement un verrou.

Pour sortir de REQUIREMENT_ONLY, il faut identifier
une difficult? technique sous-jacente concr?te.

============================================================
ENGINEERING_DIFFICULTY
============================================================

Utilise ENGINEERING_DIFFICULTY lorsque la source d?crit principalement :

- int?gration ;
- migration ;
- configuration ;
- d?veloppement ;
- adaptation ;
- interop?rabilit? ;
- compatibilit? ;
- environnement propri?taire ;
- changement de format ou protocole ;

sans montrer que les m?thodes d'ing?nierie disponibles
sont insuffisantes ou qu'une incertitude technique subsiste.

Une t?che peut ?tre complexe, co?teuse et longue
sans constituer une incertitude R&D.

============================================================
INSUFFICIENT_EVIDENCE
============================================================

Utilise INSUFFICIENT_EVIDENCE lorsqu'il existe un signal possible,
mais qu'on ne peut m?me pas d?terminer clairement si une v?ritable
probl?matique technique non ma?tris?e existe.

Diff?rence fondamentale :

NEEDS_REFORMULATION
= le probl?me technique est identifiable,
  mais mal formul? ou insuffisamment d?montr?.

INSUFFICIENT_EVIDENCE
= les informations disponibles ne permettent pas encore
  de savoir s'il existe vraiment un probl?me technique non ma?tris?.

============================================================
CONTEXTE LOCAL
============================================================

Pour chaque preuve tu peux recevoir :

PREUVE_EXACTE
= citation qui a d?clench? le candidat.

CONTEXTE_LOCAL
= texte environnant provenant du m?me passage du document.

La PREUVE_EXACTE reste la preuve principale.

Le CONTEXTE_LOCAL sert seulement ? comprendre correctement
le probl?me et ?viter de qualifier une phrase isol?e.

Tu ne dois pas inventer une information qui n'existe
ni dans la preuve ni dans le contexte.

============================================================
REFORMULATION
============================================================

Une suggested_statement doit :

- d?crire le probl?me technique ;
- ?viter de simplement r?p?ter l'objectif ;
- rester strictement soutenue par les sources ;
- ne jamais inventer un ?chec ou une incertitude.

Exemple :

Trop objectif :
"Garantir la stabilit? du proc?d?."

Meilleure formulation si les preuves le permettent :
"Variabilit? du comportement du proc?d? dans des conditions
identiques emp?chant d'obtenir des r?sultats reproductibles."

============================================================
MISSING_EVIDENCE
============================================================

Pour VALID_LOCK :
missing_evidence peut ?tre vide.

Pour NEEDS_REFORMULATION :
indique ce qui permettrait de mieux d?montrer le probl?me :
- r?sultats d'essais ;
- conditions d'apparition ;
- limites des m?thodes existantes ;
- approches d?j? test?es ;
- impacts observ?s ;
- param?tres influents.

Pour REQUIREMENT_ONLY :
indique ce qu'il faudrait d?montrer pour transformer
la contrainte en v?ritable probl?matique technique.

Pour ENGINEERING_DIFFICULTY :
indique ce qui montrerait que le probl?me d?passe
une difficult? d'ing?nierie classique.

Pour INSUFFICIENT_EVIDENCE :
indique pr?cis?ment les informations permettant de d?cider.

============================================================
R?GLES FINALES
============================================================

1. Ne supprime aucun candidat.
2. Ne cr?e aucun candidat suppl?mentaire.
3. Ne te fie jamais uniquement au mot "verrou".
4. VALID_LOCK exige des preuves concr?tes.
5. NEEDS_REFORMULATION doit ?tre utilis? lorsqu'un probl?me technique
   r?el est identifiable mais encore trop g?n?ral ou mal d?montr?.
6. REQUIREMENT_ONLY correspond ? une propri?t? attendue sans
   difficult? technique sous-jacente d?montr?e.
7. ENGINEERING_DIFFICULTY correspond ? une complexit? classique.
8. INSUFFICIENT_EVIDENCE correspond ? une ambigu?t? r?elle.
9. En cas de doute entre VALID_LOCK et NEEDS_REFORMULATION,
   pr?f?re NEEDS_REFORMULATION.
10. En cas de doute entre NEEDS_REFORMULATION et REQUIREMENT_ONLY,
    v?rifie s'il existe r?ellement un ph?nom?ne ou obstacle technique
    sp?cifique dans les sources.

Retourne uniquement :

{
  "qualifications": [
    {
      "entity_id": "...",
      "status": "VALID_LOCK|NEEDS_REFORMULATION|REQUIREMENT_ONLY|ENGINEERING_DIFFICULTY|INSUFFICIENT_EVIDENCE",
      "reason": "...",
      "suggested_statement": "...",
      "evidence_ids": [],
      "missing_evidence": []
    }
  ]
}
""".strip()


def _build_prompt(
    entities: List[CanonicalEntity],
    chunks: Optional[Iterable[Chunk]] = None,
) -> str:

    chunk_by_id = {}

    if chunks:
        chunk_by_id = {
            chunk.chunk_id: chunk
            for chunk in chunks
        }

    blocks = []

    for entity in entities:
        evidence_blocks = []

        for evidence in entity.evidences:
            quote = str(evidence.quote or "").strip()

            if len(quote) > 1000:
                quote = quote[:1000] + " [...]"

            context = ""

            chunk = chunk_by_id.get(evidence.chunk_id)

            if chunk is not None:
                context = str(chunk.text or "").strip()

                if len(context) > 1800:
                    context = context[:1800] + " [...]"

            evidence_blocks.append(
                "\n".join(
                    [
                        f"EVIDENCE_ID: {evidence.evidence_id}",
                        f"DOCUMENT: {evidence.document_name}",
                        f"PREUVE_EXACTE: {quote}",
                        f"CONTEXTE_LOCAL: {context}",
                    ]
                )
            )

        blocks.append(
            "\n".join(
                [
                    f"ENTITY_ID: {entity.entity_id}",
                    f"CANDIDAT: {entity.statement}",
                    "SOURCES:",
                    "\n\n".join(evidence_blocks),
                ]
            )
        )

    return (
        "CANDIDATS ? QUALIFIER :\n\n"
        + "\n\n====================\n\n".join(blocks)
    )


class LockQualifier:
    def __init__(
        self,
        llm: Optional[LLMClientProtocol],
    ):
        self.llm = llm

    def qualify(
        self,
        entities: Iterable[CanonicalEntity],
        chunks: Optional[Iterable[Chunk]] = None,
    ) -> List[CanonicalEntity]:

        entities = list(entities)

        locks = [
            entity
            for entity in entities
            if entity.kind == "lock"
        ]

        if not locks or self.llm is None:
            return entities

        try:
            raw = self.llm.extract_json(
                QUALIFICATION_SYSTEM,
                _build_prompt(
                    locks,
                    chunks=chunks,
                ),
            )

            data = parse_json_payload(raw)

        except Exception as exc:
            output = []

            for entity in entities:
                if entity.kind != "lock":
                    output.append(entity)
                    continue

                metadata = dict(entity.metadata)

                metadata.update({
                    "qualification_status": "NOT_EVALUATED",
                    "qualification_reason":
                        f"qualification_llm_error:{type(exc).__name__}",
                    "qualification_keeps_candidate": True,
                })

                output.append(
                    replace(
                        entity,
                        metadata=metadata,
                        needs_review=True,
                    )
                )

            return output

        raw_qualifications = (
            data.get("qualifications") or []
        )

        qualification_by_id = {}

        if isinstance(raw_qualifications, list):
            for item in raw_qualifications:

                if not isinstance(item, dict):
                    continue

                entity_id = str(
                    item.get("entity_id") or ""
                ).strip()

                if entity_id:
                    qualification_by_id[
                        entity_id
                    ] = item

        output = []

        for entity in entities:

            if entity.kind != "lock":
                output.append(entity)
                continue

            qualification = qualification_by_id.get(
                entity.entity_id
            )

            metadata = dict(entity.metadata)

            if not qualification:
                metadata.update({
                    "qualification_status":
                        "NOT_EVALUATED",
                    "qualification_reason":
                        "candidate_missing_from_qualification_response",
                    "qualification_keeps_candidate": True,
                })

                output.append(
                    replace(
                        entity,
                        metadata=metadata,
                        needs_review=True,
                    )
                )

                continue

            status = str(
                qualification.get("status") or ""
            ).strip().upper()

            if status not in ALLOWED_STATUSES:
                status = "INSUFFICIENT_EVIDENCE"

            valid_evidence_ids = {
                evidence.evidence_id
                for evidence in entity.evidences
            }

            selected_evidence_ids = [
                str(value)
                for value in (
                    qualification.get(
                        "evidence_ids"
                    ) or []
                )
                if str(value)
                in valid_evidence_ids
            ]

            suggested_statement = str(
                qualification.get(
                    "suggested_statement"
                ) or ""
            ).strip()

            reason = str(
                qualification.get("reason") or ""
            ).strip()

            missing_evidence = [
                str(value).strip()
                for value in (
                    qualification.get(
                        "missing_evidence"
                    ) or []
                )
                if str(value).strip()
            ]

            metadata.update({
                "qualification_status":
                    status,
                "qualification_reason":
                    reason,
                "suggested_statement":
                    suggested_statement,
                "qualification_evidence_ids":
                    selected_evidence_ids,
                "missing_evidence":
                    missing_evidence,
                "qualification_keeps_candidate":
                    True,
            })

            output.append(
                replace(
                    entity,
                    metadata=metadata,
                    needs_review=(
                        entity.needs_review
                        or status != "VALID_LOCK"
                    ),
                )
            )

        return output
