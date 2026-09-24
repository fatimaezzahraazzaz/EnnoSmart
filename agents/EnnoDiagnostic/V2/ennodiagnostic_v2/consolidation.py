from __future__ import annotations

import hashlib
import re
from typing import Iterable, List

from .schemas import CanonicalEntity, EvidenceRef, ExtractionItem
from .semantic_extractor import LLMClientProtocol, parse_json_payload


CONSOLIDATION_SYSTEM = """
Tu es l'?tape de consolidation globale d'EnnoDiagnostic.

Tu re?ois des CANDIDATS VERROUS d?j? extraits de plusieurs passages
et ?ventuellement de plusieurs documents.

Ta t?che est UNIQUEMENT de regrouper les candidats qui d?crivent
la m?me incertitude scientifique ou technique sous-jacente.

R?gles imp?ratives :

1. Ne supprime AUCUN candidat.
2. Chaque item_id doit appara?tre exactement une fois.
3. Si deux candidats sont seulement li?s mais d?crivent des probl?mes
   techniques diff?rents, garde-les s?par?s.
4. Une exigence de validation ne doit pas automatiquement ?tre fusionn?e
   avec un probl?me de reproductibilit?.
5. Une exigence de s?ret? ne doit pas automatiquement ?tre fusionn?e
   avec un probl?me d'int?gration.
6. Si tu h?sites, garde les candidats s?par?s.
7. Si une source distingue explicitement "premier verrou",
   "deuxi?me verrou", "troisi?me verrou", etc., consid?re ces ?l?ments
   comme des probl?mes distincts. Ne fusionne jamais deux num?ros
   diff?rents provenant du m?me document.
8. Partager le m?me contexte industriel ou les m?mes mots-cl?s
   ne suffit pas pour fusionner deux probl?mes.
9. Fusionne seulement lorsque les candidats d?crivent r?ellement
   la m?me incertitude sous-jacente ou des manifestations du m?me probl?me.
10. Le canonical_statement doit seulement synth?tiser les preuves fournies.
8. N'ajoute aucune nouvelle difficult? qui n'est pas pr?sente dans les preuves.
9. Ne d?cide PAS encore si le candidat est r?ellement ?ligible CIR.
10. Ne d?cide PAS encore si c'est un faux verrou : la qualification aura lieu ensuite.

JSON attendu uniquement :

{
  "clusters": [
    {
      "canonical_statement": "...",
      "item_ids": ["it_x", "it_y"],
      "reason": "Pourquoi ces candidats d?crivent le m?me probl?me"
    }
  ]
}
""".strip()



_EXPLICIT_LOCK_MARKERS = [
    (r"\b(?:premier|1er|1er\.|verrou\s*1)\b", "1"),
    (r"\b(?:deuxi[e?]me|2[e?]me|2e|verrou\s*2)\b", "2"),
    (r"\b(?:troisi[e?]me|3[e?]me|3e|verrou\s*3)\b", "3"),
    (r"\b(?:quatri[e?]me|4[e?]me|4e|verrou\s*4)\b", "4"),
    (r"\b(?:cinqui[e?]me|5[e?]me|5e|verrou\s*5)\b", "5"),
    (r"\b(?:sixi[e?]me|6[e?]me|6e|verrou\s*6)\b", "6"),
    (r"\bdernier\s+verrou\b", "last"),
]


def _explicit_lock_marker(item: ExtractionItem):
    """
    Retourne l'identifiant explicite d'un verrou quand la source
    distingue clairement premier/deuxi?me/troisi?me verrou, etc.

    Ce marqueur sert uniquement ? emp?cher une fusion destructrice.
    """
    text = " ".join([
        str(item.statement or ""),
        str(item.evidence.quote or ""),
    ]).lower()

    # On exige un contexte de verrou explicite pour ?viter qu'un simple
    # "deuxi?me test" soit interpr?t? comme un identifiant de verrou.
    if "verrou" not in text:
        return None

    for pattern, marker in _EXPLICIT_LOCK_MARKERS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return marker

    return None


def _cluster_violates_explicit_separation(source_items):
    """
    Deux verrous explicitement distingu?s dans LE MEME DOCUMENT
    ne peuvent jamais ?tre fusionn?s automatiquement.
    """
    by_document = {}

    for item in source_items:
        marker = _explicit_lock_marker(item)

        if marker is None:
            continue

        by_document.setdefault(
            item.evidence.document_id,
            set(),
        ).add(marker)

    return any(
        len(markers) > 1
        for markers in by_document.values()
    )

def _entity_id(statement: str) -> str:
    digest = hashlib.sha1(statement.strip().lower().encode("utf-8")).hexdigest()[:12]
    return f"lock_{digest}"


def _dedupe_evidence(evidences: List[EvidenceRef]) -> List[EvidenceRef]:
    seen = set()
    out = []

    for evidence in evidences:
        if evidence.evidence_id in seen:
            continue

        seen.add(evidence.evidence_id)
        out.append(evidence)

    return out


def _fallback_singleton(item: ExtractionItem, reason: str) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=_entity_id(item.statement),
        kind="lock",
        statement=item.statement,
        source_item_ids=[item.item_id],
        evidences=[item.evidence],
        document_ids=[item.evidence.document_id],
        needs_review=True,
        metadata={
            "consolidation": "singleton_fallback",
            "consolidation_reason": reason,
        },
    )


def _build_prompt(items: List[ExtractionItem]) -> str:
    blocks = []

    for item in items:
        quote = str(item.evidence.quote or "").strip()

        # On garde assez de preuve pour comprendre le verrou,
        # sans envoyer des passages ?normes au LLM.
        if len(quote) > 900:
            quote = quote[:900] + " [...]"

        blocks.append(
            "\n".join(
                [
                    f"ITEM_ID: {item.item_id}",
                    f"DOCUMENT: {item.evidence.document_name}",
                    f"EXPLICITNESS: {item.explicitness}",
                    f"STATEMENT: {item.statement}",
                    f"PREUVE: {quote}",
                ]
            )
        )

    return (
        "CANDIDATS VERROUS ? CONSOLIDER :\n\n"
        + "\n\n---\n\n".join(blocks)
    )


class GlobalLockConsolidator:
    def __init__(self, llm: LLMClientProtocol):
        self.llm = llm

    def consolidate(
        self,
        items: Iterable[ExtractionItem],
    ) -> List[CanonicalEntity]:

        locks = [item for item in items if item.kind == "lock"]

        if not locks:
            return []

        if len(locks) == 1:
            item = locks[0]

            return [
                CanonicalEntity(
                    entity_id=_entity_id(item.statement),
                    kind="lock",
                    statement=item.statement,
                    source_item_ids=[item.item_id],
                    evidences=[item.evidence],
                    document_ids=[item.evidence.document_id],
                    needs_review=item.needs_review,
                    metadata={
                        "consolidation": "singleton",
                        "source_count": 1,
                    },
                )
            ]

        by_id = {item.item_id: item for item in locks}

        try:
            raw = self.llm.extract_json(
                CONSOLIDATION_SYSTEM,
                _build_prompt(locks),
            )

            data = parse_json_payload(raw)

        except Exception as exc:
            # S?curit? : en cas d'?chec LLM, on ne perd rien.
            return [
                _fallback_singleton(
                    item,
                    f"consolidation_llm_error:{type(exc).__name__}",
                )
                for item in locks
            ]

        clusters = data.get("clusters") or []

        if not isinstance(clusters, list):
            clusters = []

        entities: List[CanonicalEntity] = []
        already_used = set()

        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue

            requested_ids = cluster.get("item_ids") or []

            valid_ids = []

            for item_id in requested_ids:
                item_id = str(item_id)

                if item_id not in by_id:
                    continue

                if item_id in already_used:
                    continue

                valid_ids.append(item_id)
                already_used.add(item_id)

            if not valid_ids:
                continue

            source_items = [by_id[item_id] for item_id in valid_ids]

            # Garde-fou non-LLM :
            # si la source elle-m?me distingue explicitement plusieurs
            # verrous num?rot?s dans le m?me document, on refuse leur fusion.
            if _cluster_violates_explicit_separation(source_items):
                for source_item in source_items:
                    entities.append(
                        _fallback_singleton(
                            source_item,
                            "explicit_source_locks_must_remain_separate",
                        )
                    )
                continue

            canonical_statement = str(
                cluster.get("canonical_statement") or ""
            ).strip()

            if not canonical_statement:
                canonical_statement = max(
                    source_items,
                    key=lambda x: len(x.statement),
                ).statement

            evidences = _dedupe_evidence(
                [item.evidence for item in source_items]
            )

            documents = sorted(
                {evidence.document_id for evidence in evidences}
            )

            entities.append(
                CanonicalEntity(
                    entity_id=_entity_id(canonical_statement),
                    kind="lock",
                    statement=canonical_statement,
                    source_item_ids=valid_ids,
                    evidences=evidences,
                    document_ids=documents,
                    needs_review=any(
                        item.needs_review for item in source_items
                    ),
                    metadata={
                        "consolidation": "semantic_global",
                        "source_count": len(source_items),
                        "document_count": len(documents),
                        "consolidation_reason": str(
                            cluster.get("reason") or ""
                        ).strip(),
                    },
                )
            )

        # Tr?s important :
        # si le LLM oublie un candidat, on le conserve comme singleton.
        for item in locks:
            if item.item_id not in already_used:
                entities.append(
                    _fallback_singleton(
                        item,
                        "candidate_missing_from_llm_response",
                    )
                )

        return entities
