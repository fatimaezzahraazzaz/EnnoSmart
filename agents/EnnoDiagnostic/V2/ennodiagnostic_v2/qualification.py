from __future__ import annotations

from dataclasses import replace
from typing import Iterable, List, Optional
import re

from .schemas import CanonicalEntity, Chunk, ExtractionItem
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

REGLE CRITIQUE POUR NEEDS_REFORMULATION :

La reformulation sert uniquement a PRECISER une problematique technique
deja soutenue par les preuves.

Elle ne doit jamais CREER la problematique technique qui manque.

Pour utiliser NEEDS_REFORMULATION, la PREUVE_EXACTE ou le CONTEXTE_LOCAL
doit deja relier explicitement le candidat a au moins un phenomene,
obstacle, comportement, limite ou incertitude technique concrete.

Un contexte general sur les risques connus d'une technologie ne suffit pas.

Par exemple, le fait qu'un passage precedent indique de maniere generale
que l'IA peut etre non deterministe, produire des erreurs ou halluciner
ne permet pas, a lui seul, de transformer un objectif de cadrage tel que
"definir ou utiliser l'IA" en verrou technique.

De meme, les formulations suivantes ne sont pas automatiquement des verrous :
- definir un perimetre ;
- identifier les cas d'usage ;
- choisir ou appliquer une technologie ;
- determiner les etapes ou elle sera utilisee ;
- etudier l'apport d'une technologie.

Si les sources expriment uniquement ce type de cadrage ou d'objectif,
sans decrire le probleme technique sous-jacent qui empeche de le realiser,
utilise INSUFFICIENT_EVIDENCE.

En revanche, si la source relie explicitement cet objectif a une difficulte
technique concrete et non maitrisee, NEEDS_REFORMULATION reste approprie.

La suggested_statement ne doit jamais ajouter :
- un echec non mentionne ;
- une instabilite non rattachee au candidat ;
- une limite de methode non documentee ;
- une incertitude seulement deduite de connaissances generales.

Elle doit reformuler uniquement ce que les preuves permettent deja
d'identifier comme probleme technique.

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
DISTINCTION REQUIREMENT_ONLY / ENGINEERING_DIFFICULTY
============================================================

Cette distinction est importante :

REQUIREMENT_ONLY
= la source dit principalement CE QUE la solution doit respecter,
sans décrire d'obstacle concret de mise en œuvre.

Exemples :
- "le système doit être sûr" ;
- "il faut respecter le temps réel" ;
- "les résultats doivent être validés".

ENGINEERING_DIFFICULTY
= la source décrit un obstacle concret d'intégration, d'adaptation,
d'interopérabilité, de compatibilité ou de déploiement, mais sans
démontrer encore une incertitude R&D.

Exemples :
- hétérogénéité de plusieurs environnements qui complique l'intégration ;
- systèmes propriétaires rendant l'interopérabilité difficile ;
- incompatibilités entre formats, protocoles ou composants ;
- adaptation complexe d'une solution existante à plusieurs plateformes.

IMPORTANT :
Une difficulté d'ingénierie n'a PAS besoin d'être une incertitude
scientifique pour être classée ENGINEERING_DIFFICULTY.

Si la source dit explicitement qu'une propriété du contexte
(hétérogénéité, environnement propriétaire, incompatibilité,
interopérabilité, intégration, migration, adaptation, etc.)
REND la mise en œuvre ou l'intégration techniquement difficile,
préfère ENGINEERING_DIFFICULTY à REQUIREMENT_ONLY.

En revanche, si la source énonce seulement une contrainte à respecter
sans obstacle concret de mise en œuvre, garde REQUIREMENT_ONLY.

Exemple :
"Les environnements industriels sont hétérogènes et propriétaires,
ce qui rend complexe l'intégration de l'IA dans les chaînes existantes."
=> ENGINEERING_DIFFICULTY, sauf si les preuves démontrent en plus
une incertitude technique non maîtrisée justifiant une autre catégorie.

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

11. NEEDS_REFORMULATION ne doit jamais servir a transformer un simple
    objectif, cadrage, perimetre ou cas d'usage en verrou.

12. Le probleme technique sous-jacent doit etre deja identifiable dans
    PREUVE_EXACTE ou CONTEXTE_LOCAL. Le contexte general du domaine ne
    suffit pas a l'inventer.

13. Si le candidat exprime uniquement un cadrage ou un objectif et que
    l'obstacle technique sous-jacent n'est pas identifiable dans les
    preuves, prefere INSUFFICIENT_EVIDENCE.


============================================================
ATTRIBUTION PROJET / LITTERATURE
============================================================

Chaque preuve peut être annotée SOURCE_ROLE :
project_evidence = preuve décrivant réellement le projet analysé.
literature = article, publication, état de l'art ou résultat d'une équipe externe.
context = information générale de contexte.
admin = budget, gouvernance ou procédure administrative.
unknown = attribution incertaine.

REGLES :
- Ne supprime aucun candidat, quel que soit son SOURCE_ROLE.
- Une preuve literature peut montrer qu'un problème existe dans le domaine,
  mais elle ne prouve PAS que le projet analysé l'a réellement rencontré.
- Un candidat fondé uniquement sur literature ne peut pas être VALID_LOCK pour le projet.
  Utilise normalement INSUFFICIENT_EVIDENCE si le problème est crédible mais qu'aucune
  preuve du projet ne le rattache aux travaux analysés.
- Si un candidat possède à la fois project_evidence et literature, qualifie le verrou
  à partir des preuves du projet ; la littérature sert seulement de contexte.
- context, admin et unknown ne doivent pas être transformés en preuve projet.
- Vérifie les candidats dans la vue globale du document/projet, mais ne transfère
  jamais la preuve d'un autre candidat sans lien technique démontré.
- Pour VALID_LOCK ou NEEDS_REFORMULATION, cite dans evidence_ids au moins une
  preuve project_evidence pertinente qui décrit concrètement la difficulté.

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
    *,
    all_locks: Optional[List[CanonicalEntity]] = None,
    project_items: Optional[List[ExtractionItem]] = None,
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
        evidence_source_roles = entity.metadata.get("evidence_source_roles") or {}

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
                        f"SOURCE_ROLE: {evidence_source_roles.get(evidence.evidence_id, 'unknown')}",
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
                    f"SOURCE_ROLES: {entity.metadata.get('source_roles')}",
                    f"HAS_PROJECT_EVIDENCE: {entity.metadata.get('has_project_evidence')}",
                    "SOURCES:",
                    "\n\n".join(evidence_blocks),
                ]
            )
        )

    overview = "\n".join(
        f"- {entity.entity_id}: {entity.statement[:260]} "
        f"(provenance={','.join(entity.metadata.get('source_roles') or ['unknown'])})"
        for entity in (all_locks or entities)
    )
    batch_terms = set(re.findall(
        r"\w{4,}", " ".join(entity.statement for entity in entities).casefold()
    ))
    batch_documents = {doc_id for entity in entities for doc_id in entity.document_ids}
    unique_items = {}
    for item in project_items or []:
        evidence_id = item.evidence.evidence_id
        if evidence_id and evidence_id not in unique_items:
            unique_items[evidence_id] = item
    ranked = sorted(unique_items.values(), key=lambda item: (
        -int(item.evidence.document_id in batch_documents),
        -len(batch_terms & set(re.findall(r"\w{4,}", (
            item.statement + " " + item.evidence.quote
        ).casefold()))),
        item.evidence.evidence_id,
    ))[:60]
    project_evidence = "\n".join(
        f"- EVIDENCE_ID={item.evidence.evidence_id} TYPE={item.kind} "
        f"DOCUMENT={item.evidence.document_name} "
        f"PREUVE_EXACTE={item.evidence.quote[:450]}"
        for item in ranked
    )
    return (
        "VUE GLOBALE DES CANDIDATS DU DOSSIER :\n"
        + (overview or "Aucun")
        + "\n\nPREUVES PROPRES AU PROJET (sélection pertinente du dossier ; "
        "la littérature ne figure pas ici) :\n"
        + (project_evidence or "Aucune preuve projet classée")
        + "\n\nCANDIDATS À QUALIFIER DANS CE LOT :\n\n"
        + "\n\n====================\n\n".join(blocks)
    )


class LockQualifier:
    def __init__(
        self,
        llm: Optional[LLMClientProtocol],
        *,
        batch_size: int = 5,
        retry_missing: bool = True,
    ):
        self.llm = llm
        self.batch_size = max(1, int(batch_size))
        self.retry_missing = bool(retry_missing)

    def _request_batch(self, locks: List[CanonicalEntity], chunks, all_locks, project_items):
        raw = self.llm.extract_json(
            QUALIFICATION_SYSTEM,
            _build_prompt(
                locks, chunks=chunks,
                all_locks=all_locks, project_items=project_items,
            ),
        )
        data = parse_json_payload(raw)
        raw_qualifications = data.get("qualifications") or []
        allowed_ids = {entity.entity_id for entity in locks}
        output = {}

        if isinstance(raw_qualifications, list):
            for item in raw_qualifications:
                if not isinstance(item, dict):
                    continue
                entity_id = str(item.get("entity_id") or "").strip()
                if entity_id in allowed_ids:
                    output[entity_id] = item

        return output

    def qualify(
        self,
        entities: Iterable[CanonicalEntity],
        chunks: Optional[Iterable[Chunk]] = None,
        extractions: Optional[Iterable[ExtractionItem]] = None,
    ) -> List[CanonicalEntity]:
        entities = list(entities)
        locks = [entity for entity in entities if entity.kind == "lock"]
        project_items = [
            item for item in (extractions or [])
            if (item.metadata or {}).get("source_role") == "project_evidence"
            and item.evidence.quote_verified
        ]
        project_by_evidence_id = {
            item.evidence.evidence_id: item for item in project_items
        }

        if not locks or self.llm is None:
            return entities

        chunk_list = list(chunks) if chunks is not None else None
        qualification_by_id = {}
        failure_reason_by_id = {}

        # Qualification par petits lots pour éviter la troncature LLM.
        for start in range(0, len(locks), self.batch_size):
            batch = locks[start:start + self.batch_size]
            try:
                batch_results = self._request_batch(
                    batch, chunk_list, locks, project_items
                )
                qualification_by_id.update(batch_results)
                for entity in batch:
                    if entity.entity_id not in batch_results:
                        failure_reason_by_id[entity.entity_id] = "candidate_missing_from_qualification_batch"
            except Exception as exc:
                for entity in batch:
                    failure_reason_by_id[entity.entity_id] = (
                        "qualification_batch_error:" + type(exc).__name__
                    )

        # Retry individuel uniquement pour les candidats manquants.
        if self.retry_missing:
            missing_locks = [
                entity for entity in locks
                if entity.entity_id not in qualification_by_id
            ]
            for entity in missing_locks:
                try:
                    single_result = self._request_batch(
                        [entity], chunk_list, locks, project_items
                    )
                    qualification = single_result.get(entity.entity_id)
                    if qualification:
                        qualification_by_id[entity.entity_id] = qualification
                        failure_reason_by_id.pop(entity.entity_id, None)
                    else:
                        failure_reason_by_id[entity.entity_id] = "candidate_missing_after_individual_retry"
                except Exception as exc:
                    failure_reason_by_id[entity.entity_id] = (
                        "qualification_retry_error:" + type(exc).__name__
                    )

        output = []

        for entity in entities:
            if entity.kind != "lock":
                output.append(entity)
                continue

            qualification = qualification_by_id.get(entity.entity_id)
            metadata = dict(entity.metadata)

            if not qualification:
                metadata.update({
                    "qualification_status": "NOT_EVALUATED",
                    "qualification_reason": failure_reason_by_id.get(
                        entity.entity_id,
                        "qualification_not_returned",
                    ),
                    "qualification_keeps_candidate": True,
                })
                output.append(replace(entity, metadata=metadata, needs_review=True))
                continue

            status = str(qualification.get("status") or "").strip().upper()
            if status not in ALLOWED_STATUSES:
                status = "INSUFFICIENT_EVIDENCE"

            valid_evidence_ids = (
                {evidence.evidence_id for evidence in entity.evidences}
                | set(project_by_evidence_id)
            )
            selected_evidence_ids = [
                str(value)
                for value in (qualification.get("evidence_ids") or [])
                if str(value) in valid_evidence_ids
            ]
            suggested_statement = str(
                qualification.get("suggested_statement") or ""
            ).strip()
            reason = str(qualification.get("reason") or "").strip()
            missing_evidence = [
                str(value).strip()
                for value in (qualification.get("missing_evidence") or [])
                if str(value).strip()
            ]

            entity_roles = metadata.get("evidence_source_roles") or {}
            verified_entity_ids = {
                evidence.evidence_id for evidence in entity.evidences
                if evidence.quote_verified
            }
            selected_project_ids = [
                evidence_id for evidence_id in selected_evidence_ids
                if evidence_id in project_by_evidence_id
                or (
                    evidence_id in verified_entity_ids
                    and entity_roles.get(evidence_id) == "project_evidence"
                )
            ]
            if status in {"VALID_LOCK", "NEEDS_REFORMULATION"} and not selected_project_ids:
                status = "INSUFFICIENT_EVIDENCE"
                reason = (
                    "Aucune preuve propre au projet n'a été citée pour soutenir "
                    "cette difficulté. " + reason
                ).strip()
                missing_evidence.append(
                    "Essai, observation ou difficulté technique concrète du projet liée à ce candidat."
                )

            linked_items = [
                project_by_evidence_id[evidence_id]
                for evidence_id in selected_project_ids
                if evidence_id in project_by_evidence_id
                and evidence_id not in {e.evidence_id for e in entity.evidences}
            ]
            evidence_roles = dict(entity_roles)
            for item in linked_items:
                evidence_roles[item.evidence.evidence_id] = "project_evidence"
            if linked_items:
                metadata["evidence_source_roles"] = evidence_roles
                metadata["source_roles"] = sorted(
                    set(metadata.get("source_roles") or []) | {"project_evidence"}
                )
                metadata["has_project_evidence"] = True
                metadata["qualification_linked_evidence_ids"] = [
                    item.evidence.evidence_id for item in linked_items
                ]

            metadata.update({
                "qualification_status": status,
                "qualification_reason": reason,
                "suggested_statement": suggested_statement,
                "qualification_evidence_ids": selected_evidence_ids,
                "missing_evidence": missing_evidence,
                "qualification_keeps_candidate": True,
            })

            output.append(
                replace(
                    entity,
                    metadata=metadata,
                    evidences=entity.evidences + [item.evidence for item in linked_items],
                    source_item_ids=entity.source_item_ids + [
                        item.item_id for item in linked_items
                    ],
                    document_ids=sorted(
                        set(entity.document_ids)
                        | {item.evidence.document_id for item in linked_items}
                    ),
                    needs_review=(entity.needs_review or status != "VALID_LOCK"),
                )
            )

        return output
