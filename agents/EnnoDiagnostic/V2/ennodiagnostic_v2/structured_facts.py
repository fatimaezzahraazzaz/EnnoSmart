"""Listes de faits projet traçables, indépendantes du texte libre de synthèse."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional

from .schemas import CanonicalEntity, ExtractionItem, TraceablePoint
from .semantic_extractor import LLMClientProtocol, parse_json_payload


FACT_KINDS = ("method", "demarche", "parameter", "metric", "result")

DEMARCHE_REVIEW_SYSTEM = """
Tu sélectionnes les étapes techniques/scientifiques réellement attribuées au
projet dans un dossier R&D brut. Retourne uniquement {"keep_ids": ["F1", ...]}.
Chaque identifiant est indépendant. Ne retiens que l'action du projet sur les
données, modèles, algorithmes, prototypes, expériences, comparaisons ou tests.
Une action prévue peut être retenue ; elle ne devient pas réalisée.
Écarte les descriptions d'articles et d'équipes externes, les simples noms de
modèles, objectifs, mesures, résultats, procédures administratives et rédaction
documentaire. Une étape d'état de l'art réalisée par le projet peut être retenue,
mais les travaux des auteurs cités restent de la littérature.
Garde séparées les étapes techniques distinctes. Ne vise aucun nombre fixe.
Si la citation ne permet pas d'attribuer le fait au projet, écarte-le.
""".strip()


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip()


def _project_sources(entity: CanonicalEntity, items_by_id: dict) -> List[ExtractionItem]:
    return [
        item for source_id in entity.source_item_ids
        if (item := items_by_id.get(source_id)) is not None
        and (item.metadata or {}).get("source_role") == "project_evidence"
    ]


def _point(entity: CanonicalEntity, sources: List[ExtractionItem]) -> TraceablePoint:
    # Si une ancienne entité mélange les provenances, son texte canonique peut
    # venir de la littérature : dans ce cas, on reprend le fait projet source.
    statement = entity.statement if len(sources) == len(entity.source_item_ids) else sources[0].statement
    evidence_ids = list(dict.fromkeys(
        item.evidence.evidence_id for item in sources if item.evidence.evidence_id
    ))
    documents = list(dict.fromkeys(
        (item.evidence.document_id, item.evidence.document_name) for item in sources
    ))
    temporal = {item.temporal_status for item in sources}
    return TraceablePoint(
        text=statement,
        evidence_ids=evidence_ids,
        temporal_status=next(iter(temporal)) if len(temporal) == 1 else "unclear",
        source_role="project_evidence",
        sources=[{"document_id": doc_id, "document_name": name} for doc_id, name in documents],
        original_item_ids=[item.item_id for item in sources],
    )


def _select_demarches(
    candidates: List[tuple[TraceablePoint, List[ExtractionItem]]],
    llm: Optional[LLMClientProtocol],
) -> List[TraceablePoint]:
    if not candidates:
        return []
    if llm is None:
        # Sans contrôle sémantique, préserver les faits candidats et leurs preuves.
        return [point for point, _ in candidates]

    selected = []
    for start in range(0, len(candidates), 18):
        batch = candidates[start:start + 18]
        blocks = []
        for index, (point, sources) in enumerate(batch, 1):
            item = sources[0]
            blocks.append(
                f"ID: F{index}\nDOCUMENT: {item.evidence.document_name}\n"
                f"SECTION: {item.evidence.section_title}\n"
                f"TEMPORALITE: {point.temporal_status}\n"
                f"FAIT: {point.text}\nPASSAGE: {item.evidence.quote[:900]}"
            )
        try:
            data = parse_json_payload(llm.extract_json(
                DEMARCHE_REVIEW_SYSTEM, "\n\n---\n\n".join(blocks)
            ))
            if not isinstance(data, dict) or not isinstance(data.get("keep_ids"), list):
                raise ValueError("invalid_demarche_review")
            keep = set(data["keep_ids"])
            selected.extend(point for index, (point, _) in enumerate(batch, 1)
                            if f"F{index}" in keep)
        except Exception:
            # Une panne du contrôle ne doit pas effacer silencieusement le lot.
            selected.extend(point for point, _ in batch)
    return selected


def build_project_fact_lists(
    extractions: Iterable[ExtractionItem],
    entities: Iterable[CanonicalEntity],
    llm: Optional[LLMClientProtocol] = None,
) -> dict[str, List[TraceablePoint]]:
    items_by_id = {item.item_id: item for item in extractions}
    canonical = list(entities)
    represented = {source_id for entity in canonical for source_id in entity.source_item_ids}
    for item in items_by_id.values():
        if item.kind in FACT_KINDS and item.item_id not in represented:
            canonical.append(CanonicalEntity(
                entity_id=item.item_id,
                kind=item.kind,
                statement=item.statement,
                source_item_ids=[item.item_id],
                evidences=[item.evidence],
                document_ids=[item.evidence.document_id],
            ))
    output: dict[str, List[TraceablePoint]] = {kind: [] for kind in FACT_KINDS}
    demarche_candidates = []
    seen = {kind: set() for kind in FACT_KINDS}

    for entity in canonical:
        if entity.kind not in output:
            continue
        sources = _project_sources(entity, items_by_id)
        if not sources:
            continue
        point = _point(entity, sources)
        if not point.text or not point.evidence_ids:
            continue
        key = (_norm(point.text), point.temporal_status if entity.kind == "demarche" else "")
        if key in seen[entity.kind]:
            # Des présentations répétées peuvent fournir plusieurs preuves.
            existing = next(p for p in output[entity.kind] if
                            (_norm(p.text), p.temporal_status if entity.kind == "demarche" else "") == key)
            existing.evidence_ids = list(dict.fromkeys(existing.evidence_ids + point.evidence_ids))
            existing.sources = list({(s["document_id"], s["document_name"]): s
                                     for s in existing.sources + point.sources}.values())
            existing.original_item_ids = list(dict.fromkeys(
                existing.original_item_ids + point.original_item_ids
            ))
            continue
        seen[entity.kind].add(key)
        output[entity.kind].append(point)
        if entity.kind == "demarche":
            demarche_candidates.append((point, sources))

    output["demarche"] = _select_demarches(demarche_candidates, llm)
    return output
