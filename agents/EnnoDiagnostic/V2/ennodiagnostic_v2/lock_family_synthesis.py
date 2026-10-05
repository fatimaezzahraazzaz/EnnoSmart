from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .schemas import CanonicalEntity
from .semantic_extractor import LLMClientProtocol, parse_json_payload


ALLOWED_STATUSES = {
    "VALID_LOCK",
    "NEEDS_REFORMULATION",
    "REQUIREMENT_ONLY",
    "ENGINEERING_DIFFICULTY",
    "INSUFFICIENT_EVIDENCE",
    "NOT_EVALUATED",
}

STATUS_PRIORITY = {
    "VALID_LOCK": 6,
    "NEEDS_REFORMULATION": 5,
    "ENGINEERING_DIFFICULTY": 4,
    "REQUIREMENT_ONLY": 3,
    "INSUFFICIENT_EVIDENCE": 2,
    "NOT_EVALUATED": 1,
}

# Seuls ces statuts peuvent contribuer aux VERROUS PRINCIPAUX affichés.
# Les autres candidats restent conservés dans les entités du diagnostic,
# mais ne doivent pas créer artificiellement des familles de verrous.
MAIN_FAMILY_STATUSES = {
    "VALID_LOCK",
    "NEEDS_REFORMULATION",
}


@dataclass
class LockFamily:
    family_id: str
    title: str
    statement: str
    status: str
    member_entity_ids: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    rationale: str = ""
    needs_review: bool = False


@dataclass
class LockFamilySynthesis:
    families: List[LockFamily] = field(default_factory=list)
    literature_only_entity_ids: List[str] = field(default_factory=list)
    unassigned_entity_ids: List[str] = field(default_factory=list)
    source_entity_count: int = 0
    project_entity_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


LOCK_FAMILY_SYSTEM = r"""
Tu réalises la SYNTHÈSE FINALE des verrous techniques d'un dossier R&D/CIR.

Tu reçois des sous-verrous déjà :
- extraits ;
- consolidés pour supprimer les doublons exacts ;
- qualifiés ;
- reliés à leurs preuves.

Ta tâche n'est PAS de refaire l'extraction ni la qualification.
Ta tâche est de regrouper en GRANDES FAMILLES les candidats du PROJET
déjà retenus en amont comme :
- VALID_LOCK ;
- NEEDS_REFORMULATION.

Les exigences simples, difficultés d'ingénierie et preuves insuffisantes
sont conservées ailleurs dans le diagnostic et ne doivent pas créer
de familles de verrous principaux.

Exemple :
- couverture insuffisante
- erreurs de compilation
- échecs d'exécution
- tests peu pertinents
peuvent appartenir à une famille plus globale :
"Fiabilité et performance des tests générés".

RÈGLES IMPÉRATIVES

1. Ne supprime aucun ENTITY_ID fourni.
2. Chaque ENTITY_ID doit apparaître exactement une fois :
   soit dans une famille, soit dans unassigned_entity_ids.
3. Ne change jamais la qualification d'un sous-verrou.
4. Ne crée aucun problème technique absent des sous-verrous fournis.
5. Tu peux regrouper plusieurs manifestations différentes lorsqu'elles
   correspondent clairement au même problème technique global.
6. Ne fusionne pas des problèmes seulement parce qu'ils appartiennent
   au même projet ou utilisent les mêmes technologies.
7. Ne crée pas une famille supplémentaire uniquement pour réduire ou augmenter
   artificiellement le nombre de familles.
8. Il n'existe AUCUN nombre cible de familles. Deux sous-verrous doivent être
   regroupés seulement s'ils décrivent réellement le même problème technique
   global. Des problèmes distincts doivent rester distincts.
9. Les candidats purement issus de la littérature ne sont pas fournis ici :
   ils restent conservés ailleurs comme contexte.
10. La synthèse sert uniquement à l'affichage. Les sous-verrous originaux
    restent conservés et traçables.
11. STRUCTURE EXPLICITE DE LA SOURCE : si un même document distingue
    explicitement plusieurs verrous ("Premier verrou", "Deuxième verrou",
    "Troisième verrou", "Verrou 1", "Verrou 2", etc.), ces verrous
    représentent des problématiques distinctes et NE DOIVENT PAS être
    fusionnés dans une même famille.
12. Deux verrous peuvent être liés, complémentaires ou appartenir au même
    thème sans constituer le même problème technique global. Par exemple,
    "générer des tests fiables" et "évaluer la qualité des tests" restent
    distincts si la source les présente comme deux verrous différents.
13. Le champ STRUCTURE_SOURCE_EXPLICITE fourni pour chaque ENTITY_ID est une
    contrainte forte : deux marqueurs différents du même document ne doivent
    jamais être placés dans la même famille.
14. Une famille contient au moins deux verrous. Si la source distingue déjà
    explicitement des verrous autonomes, laisse-les non assignés à une famille.

Réponds uniquement en JSON :

{
  "families": [
    {
      "title": "Titre court de la famille",
      "statement": "Synthèse fidèle du problème technique global",
      "member_entity_ids": ["lock_x", "lock_y"],
      "rationale": "Pourquoi ces sous-verrous appartiennent à la même famille"
    }
  ],
  "unassigned_entity_ids": []
}
""".strip()


def _family_id(member_ids: Iterable[str]) -> str:
    key = "|".join(sorted(str(x) for x in member_ids))
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"family_{digest}"


def _normalize_status(value: Any) -> str:
    status = str(value or "NOT_EVALUATED").strip().upper()
    return status if status in ALLOWED_STATUSES else "NOT_EVALUATED"


def _family_status(records: List[Dict[str, Any]]) -> str:
    statuses = [
        _normalize_status(record.get("qualification_status"))
        for record in records
    ]
    if not statuses:
        return "NOT_EVALUATED"
    return max(statuses, key=lambda s: STATUS_PRIORITY.get(s, 0))


_EXPLICIT_LOCK_MARKERS = [
    (r"\bpremier\s+verrou\b|\bverrou\s*1\b|\b1(?:er)?\s+verrou\b", "1"),
    (r"\bdeuxi[eè]me\s+verrou\b|\bverrou\s*2\b|\b2(?:e|ème)?\s+verrou\b", "2"),
    (r"\btroisi[eè]me\s+verrou\b|\bverrou\s*3\b|\b3(?:e|ème)?\s+verrou\b", "3"),
    (r"\bquatri[eè]me\s+verrou\b|\bverrou\s*4\b|\b4(?:e|ème)?\s+verrou\b", "4"),
    (r"\bcinqui[eè]me\s+verrou\b|\bverrou\s*5\b|\b5(?:e|ème)?\s+verrou\b", "5"),
    (r"\bsixi[eè]me\s+verrou\b|\bverrou\s*6\b|\b6(?:e|ème)?\s+verrou\b", "6"),
    (r"\bdernier\s+verrou\b", "last"),
]


def _explicit_lock_marker(text: str) -> Optional[str]:
    value = str(text or "").lower()
    if "verrou" not in value:
        return None

    for pattern, marker in _EXPLICIT_LOCK_MARKERS:
        if re.search(pattern, value, flags=re.IGNORECASE):
            return marker
    return None


def _evidence_field(evidence: Any, name: str) -> Any:
    if isinstance(evidence, dict):
        return evidence.get(name)
    return getattr(evidence, name, None)


def _explicit_source_locks(
    statement: str,
    evidences: Iterable[Any],
) -> List[Dict[str, str]]:
    found = set()

    for evidence in evidences or []:
        quote = str(_evidence_field(evidence, "quote") or "")
        section = str(_evidence_field(evidence, "section_title") or "")
        document_id = str(_evidence_field(evidence, "document_id") or "")

        marker = _explicit_lock_marker(
            "\n".join([str(statement or ""), quote, section])
        )

        if marker is not None:
            found.add((document_id, marker))

    return [
        {"document_id": document_id, "marker": marker}
        for document_id, marker in sorted(found)
    ]


def _entity_to_record(entity: CanonicalEntity) -> Dict[str, Any]:
    metadata = dict(entity.metadata or {})
    return {
        "entity_id": entity.entity_id,
        "statement": entity.statement,
        "qualification_status": metadata.get("qualification_status"),
        "qualification_reason": metadata.get("qualification_reason"),
        "suggested_statement": metadata.get("suggested_statement"),
        "source_roles": metadata.get("source_roles") or [],
        "has_project_evidence": metadata.get("has_project_evidence"),
        "evidence_ids": [
            evidence.evidence_id
            for evidence in entity.evidences
        ],
        "explicit_source_locks": _explicit_source_locks(
            entity.statement,
            entity.evidences,
        ),
        "needs_review": bool(entity.needs_review),
    }


def _dict_to_record(entity: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(entity.get("metadata") or {})
    evidences = entity.get("evidences") or []
    statement = str(entity.get("statement") or "").strip()

    return {
        "entity_id": str(entity.get("entity_id") or "").strip(),
        "statement": statement,
        "qualification_status": metadata.get("qualification_status"),
        "qualification_reason": metadata.get("qualification_reason"),
        "suggested_statement": metadata.get("suggested_statement"),
        "source_roles": metadata.get("source_roles") or [],
        "has_project_evidence": metadata.get("has_project_evidence"),
        "evidence_ids": [
            str(e.get("evidence_id"))
            for e in evidences
            if isinstance(e, dict) and e.get("evidence_id")
        ],
        "explicit_source_locks": _explicit_source_locks(
            statement,
            evidences,
        ),
        "needs_review": bool(entity.get("needs_review", False)),
    }



def _provenance_state(record: Dict[str, Any]) -> str:
    """
    Retourne :
    - project : preuve projet explicite ;
    - literature : littérature explicite uniquement ;
    - ambiguous : provenance absente, contextual ou unknown.

    IMPORTANT :
    l'absence de métadonnée ne doit jamais être interprétée comme littérature.
    """
    roles = {
        str(role or "").strip().lower()
        for role in (record.get("source_roles") or [])
        if str(role or "").strip()
    }

    has_project = record.get("has_project_evidence")

    if has_project is True or "project_evidence" in roles:
        return "project"

    if roles == {"literature"}:
        return "literature"

    return "ambiguous"


def _build_prompt(records: List[Dict[str, Any]]) -> str:
    blocks = []

    for record in records:
        reason = str(
            record.get("qualification_reason") or ""
        ).strip()
        suggested = str(
            record.get("suggested_statement") or ""
        ).strip()

        if len(reason) > 500:
            reason = reason[:500] + " [...]"

        if len(suggested) > 500:
            suggested = suggested[:500] + " [...]"

        explicit_refs = record.get("explicit_source_locks") or []
        explicit_structure = ", ".join(
            f"{ref.get('document_id') or '?'}:verrou_{ref.get('marker')}"
            for ref in explicit_refs
            if isinstance(ref, dict) and ref.get("marker")
        )

        blocks.append(
            "\n".join([
                f"ENTITY_ID: {record['entity_id']}",
                f"STATEMENT: {record['statement']}",
                f"QUALIFICATION: {_normalize_status(record.get('qualification_status'))}",
                f"REFORMULATION: {suggested}",
                f"RAISON: {reason}",
                "STRUCTURE_SOURCE_EXPLICITE: "
                + (explicit_structure or "aucune"),
            ])
        )

    return (
        "SOUS-VERROUS DU PROJET À SYNTHÉTISER :\n\n"
        + "\n\n---\n\n".join(blocks)
    )


def _marker_sort_key(marker: str) -> tuple:
    if marker == "last":
        return (1, 999)

    try:
        return (0, int(marker))
    except Exception:
        return (1, 998)


def _split_on_explicit_source_structure(
    member_ids: List[str],
    by_id: Dict[str, Dict[str, Any]],
) -> List[List[str]]:
    records = [by_id[entity_id] for entity_id in member_ids]

    markers_by_document: Dict[str, set] = {}

    for record in records:
        for ref in record.get("explicit_source_locks") or []:
            if not isinstance(ref, dict):
                continue

            document_id = str(ref.get("document_id") or "")
            marker = str(ref.get("marker") or "")

            if not marker:
                continue

            markers_by_document.setdefault(document_id, set()).add(marker)

    conflicting_documents = [
        document_id
        for document_id, markers in markers_by_document.items()
        if len(markers) > 1
    ]

    if not conflicting_documents:
        return [member_ids]

    document_id = sorted(conflicting_documents)[0]

    groups: Dict[str, List[str]] = {}
    unmarked: List[str] = []
    ambiguous: List[List[str]] = []

    for entity_id in member_ids:
        record = by_id[entity_id]
        markers = sorted({
            str(ref.get("marker"))
            for ref in (record.get("explicit_source_locks") or [])
            if isinstance(ref, dict)
            and str(ref.get("document_id") or "") == document_id
            and ref.get("marker")
        }, key=_marker_sort_key)

        if len(markers) == 1:
            groups.setdefault(markers[0], []).append(entity_id)
        elif len(markers) > 1:
            ambiguous.append([entity_id])
        else:
            unmarked.append(entity_id)

    split_groups = [
        groups[marker]
        for marker in sorted(groups, key=_marker_sort_key)
        if groups[marker]
    ]

    split_groups.extend(ambiguous)

    if unmarked:
        split_groups.append(unmarked)

    return split_groups


def _build_guarded_family(
    member_ids: List[str],
    by_id: Dict[str, Dict[str, Any]],
    *,
    rationale: str,
) -> LockFamily:
    member_records = [by_id[entity_id] for entity_id in member_ids]

    evidence_ids = sorted({
        evidence_id
        for record in member_records
        for evidence_id in (record.get("evidence_ids") or [])
    })

    representative = max(
        member_records,
        key=lambda record: len(
            str(
                record.get("suggested_statement")
                or record.get("statement")
                or ""
            )
        ),
    )

    statement = str(
        representative.get("suggested_statement")
        or representative.get("statement")
        or "Verrou technique"
    ).strip()

    title = str(
        representative.get("statement")
        or statement
        or "Verrou technique"
    ).strip()

    if len(title) > 180:
        title = title[:177].rstrip() + "..."

    return LockFamily(
        family_id=_family_id(member_ids),
        title=title,
        statement=statement,
        status=_family_status(member_records),
        member_entity_ids=member_ids,
        evidence_ids=evidence_ids,
        rationale=rationale,
        needs_review=any(
            bool(record.get("needs_review"))
            for record in member_records
        ),
    )


class LockFamilySynthesizer:
    def __init__(self, llm: Optional[LLMClientProtocol]):
        self.llm = llm

    def synthesize(
        self,
        entities: Iterable[CanonicalEntity],
    ) -> LockFamilySynthesis:
        records = [
            _entity_to_record(entity)
            for entity in entities
            if entity.kind == "lock"
        ]
        return self.synthesize_records(records)

    def synthesize_records(
        self,
        entities: Iterable[Dict[str, Any]],
    ) -> LockFamilySynthesis:
        raw_entities = list(entities)

        records = []
        for entity in raw_entities:
            if "metadata" in entity or "evidences" in entity:
                record = _dict_to_record(entity)
            else:
                record = dict(entity)

            if not record.get("entity_id"):
                continue

            records.append(record)

        literature_only = [
            record["entity_id"]
            for record in records
            if _provenance_state(record) == "literature"
        ]

        project_records = [
            record
            for record in records
            if _provenance_state(record) == "project"
        ]

        ambiguous_ids = [
            record["entity_id"]
            for record in records
            if _provenance_state(record) == "ambiguous"
        ]

        if not project_records:
            return LockFamilySynthesis(
                literature_only_entity_ids=literature_only,
                unassigned_entity_ids=ambiguous_ids,
                source_entity_count=len(records),
                project_entity_count=0,
            )

        # IMPORTANT :
        # On conserve TOUS les candidats projet, mais seuls VALID_LOCK et
        # NEEDS_REFORMULATION participent à la synthèse des verrous principaux.
        # Cela évite qu'une exigence, une difficulté d'ingénierie ou une preuve
        # insuffisante soit affichée comme un verrou principal.
        family_records = [
            record
            for record in project_records
            if _normalize_status(record.get("qualification_status"))
            in MAIN_FAMILY_STATUSES
        ]

        # Quand le document numérote explicitement plusieurs verrous, chacun
        # reste un verrou précis. Une famille singleton n'apporte aucune
        # synthèse et ferait croire à un niveau décisionnel supplémentaire.
        markers_by_document: Dict[str, set] = {}
        for record in family_records:
            for ref in record.get("explicit_source_locks") or []:
                if isinstance(ref, dict) and ref.get("marker"):
                    markers_by_document.setdefault(
                        str(ref.get("document_id") or ""), set()
                    ).add(str(ref["marker"]))
        explicitly_structured_docs = {
            document_id for document_id, markers in markers_by_document.items()
            if len(markers) >= 2
        }
        explicit_project_ids = [
            record["entity_id"] for record in family_records
            if any(
                isinstance(ref, dict)
                and str(ref.get("document_id") or "") in explicitly_structured_docs
                for ref in (record.get("explicit_source_locks") or [])
            )
        ]
        explicit_project_id_set = set(explicit_project_ids)
        family_records = [
            record for record in family_records
            if record["entity_id"] not in explicit_project_id_set
        ]

        secondary_project_ids = [
            record["entity_id"]
            for record in project_records
            if _normalize_status(record.get("qualification_status"))
            not in MAIN_FAMILY_STATUSES
        ]

        if not family_records:
            return LockFamilySynthesis(
                literature_only_entity_ids=literature_only,
                unassigned_entity_ids=ambiguous_ids + secondary_project_ids + explicit_project_ids,
                source_entity_count=len(records),
                project_entity_count=len(project_records),
            )

        by_id = {
            record["entity_id"]: record
            for record in family_records
        }

        if self.llm is None:
            return LockFamilySynthesis(
                literature_only_entity_ids=literature_only,
                unassigned_entity_ids=ambiguous_ids + secondary_project_ids + explicit_project_ids + list(by_id),
                source_entity_count=len(records),
                project_entity_count=len(project_records),
            )

        try:
            raw = self.llm.extract_json(
                LOCK_FAMILY_SYSTEM,
                _build_prompt(family_records),
            )
            data = parse_json_payload(raw)
        except Exception:
            return LockFamilySynthesis(
                literature_only_entity_ids=literature_only,
                unassigned_entity_ids=ambiguous_ids + secondary_project_ids + explicit_project_ids + list(by_id),
                source_entity_count=len(records),
                project_entity_count=len(project_records),
            )

        raw_families = data.get("families") or []
        if not isinstance(raw_families, list):
            raw_families = []

        used = set()
        families: List[LockFamily] = []

        for raw_family in raw_families:
            if not isinstance(raw_family, dict):
                continue

            requested_ids = raw_family.get("member_entity_ids") or []
            member_ids = []

            for entity_id in requested_ids:
                entity_id = str(entity_id).strip()

                if entity_id not in by_id:
                    continue

                if entity_id in used:
                    continue

                member_ids.append(entity_id)

            if not member_ids:
                continue

            if len(member_ids) == 1:
                continue

            split_groups = _split_on_explicit_source_structure(
                member_ids,
                by_id,
            )

            if len(split_groups) > 1:
                print(
                    "[EnnoDiagnostic][LockFamily] garde-fou structure source : "
                    "une famille proposée par le LLM contenait plusieurs "
                    "verrous explicitement distincts ; séparation appliquée."
                )

                for group_ids in split_groups:
                    if len(group_ids) < 2:
                        continue

                    for entity_id in group_ids:
                        used.add(entity_id)

                    families.append(
                        _build_guarded_family(
                            group_ids,
                            by_id,
                            rationale=(
                                "Famille séparée automatiquement car la source "
                                "distingue explicitement plusieurs verrous. "
                                "Aucune qualification n'a été modifiée."
                            ),
                        )
                    )

                continue

            for entity_id in member_ids:
                used.add(entity_id)

            member_records = [
                by_id[entity_id]
                for entity_id in member_ids
            ]

            evidence_ids = sorted({
                evidence_id
                for record in member_records
                for evidence_id in (record.get("evidence_ids") or [])
            })

            title = str(
                raw_family.get("title") or ""
            ).strip()

            statement = str(
                raw_family.get("statement") or ""
            ).strip()

            rationale = str(
                raw_family.get("rationale") or ""
            ).strip()

            if not title:
                title = "Verrou technique"

            if not statement:
                statement = max(
                    member_records,
                    key=lambda x: len(x.get("statement") or ""),
                ).get("statement") or title

            families.append(
                LockFamily(
                    family_id=_family_id(member_ids),
                    title=title,
                    statement=statement,
                    status=_family_status(member_records),
                    member_entity_ids=member_ids,
                    evidence_ids=evidence_ids,
                    rationale=rationale,
                    needs_review=any(
                        bool(record.get("needs_review"))
                        for record in member_records
                    ),
                )
            )

        model_unassigned = data.get("unassigned_entity_ids") or []

        # Les signaux projet non destinés aux familles principales restent
        # conservés ici. Ils ne sont ni supprimés ni transformés en verrou.
        unassigned = list(ambiguous_ids) + list(secondary_project_ids) + explicit_project_ids

        for entity_id in model_unassigned:
            entity_id = str(entity_id).strip()
            if entity_id in by_id and entity_id not in used:
                used.add(entity_id)
                unassigned.append(entity_id)

        # Sécurité : tout candidat oublié par le LLM reste conservé.
        for entity_id in by_id:
            if entity_id not in used:
                unassigned.append(entity_id)

        return LockFamilySynthesis(
            families=families,
            literature_only_entity_ids=literature_only,
            unassigned_entity_ids=unassigned,
            source_entity_count=len(records),
            project_entity_count=len(project_records),
        )
