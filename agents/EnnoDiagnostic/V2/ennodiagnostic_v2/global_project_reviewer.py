"""Relecture trans-documentaire des candidats, sans modifier les faits bruts."""

from __future__ import annotations

import re
import hashlib
import json
import os
import tempfile
import unicodedata
from collections import defaultdict
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Iterable
from pathlib import Path

from .config import GlobalReviewerConfig
from .schemas import CanonicalEntity, Chunk, ExtractionItem
from .semantic_extractor import LLMClientProtocol, parse_json_payload


KINDS = {"objective", "lock", "method", "demarche", "parameter", "metric", "result"}
ROLES = {"project_evidence", "literature", "admin", "context", "unknown"}
PROJECT_LIST_KINDS = {"method", "demarche", "parameter", "metric", "result"}

REVIEW_SYSTEM = """
Tu es le relecteur global d'un dossier R&D constitué UNIQUEMENT de documents bruts.
Tu reçois les candidats avec leurs citations exactes et, si le budget le permet,
un index global et le plan des documents. Ces repères servent à comprendre ensemble les documents,
notamment qui a effectué les travaux. Les citations des candidats sont les seules
preuves autorisées pour leurs décisions. Un passage d'article demeure literature,
même si sa limite ressemble à un verrou ou à un résultat du projet.

Pour CHAQUE item_id du lot, rends exactement une décision, sans ID supplémentaire :
{"decisions":[{"item_id":"...","original_item_ids":["..."],"evidence_ids":["..."],
"source_role":"project_evidence|literature|admin|context|unknown",
"kind":"objective|lock|method|demarche|parameter|metric|result",
"project_admission":true,"uncertain":false,"duplicate_of":null,
"related_project_evidence_ids":[],"related_lock_ids":[],"reason":"..."}]}.

METHOD = mécanisme/protocole/architecture technique effectivement utilisé par le projet.
DEMARCHE = étape de recherche du projet (étudier, comparer, expérimenter, tester...).
PARAMETER = variable ou configuration choisie ou modifiée.
METRIC = grandeur mesurée. RESULT = valeur ou constat effectivement obtenu.
Une intention de mesurer n'est pas un résultat. Préserve la temporalité fournie.
N'admets dans le projet que les faits explicitement attribuables à ce projet.
Si l'attribution ou la catégorie est douteuse, uncertain=true et project_admission=false.
project_admission indique l'attribution, indépendamment d'un éventuel doublon :
un doublon projet conserve project_admission=true et référence son équivalent.
Ne crée aucun fait ni preuve. Pour chaque décision, original_item_ids doit
contenir seulement son item_id et evidence_ids seulement son evidence_id source.
N'invente ni citation, ni document. duplicate_of ne peut désigner qu'un autre item_id de
la liste des item_id qui exprime réellement le même fait ; sinon null. Pour les candidats lock,
la décision est seulement OBSERVATOIRE : identifie éventuellement les preuves
projet et les autres candidats lock liés, sans statuer sur l'éligibilité CIR.
""".strip()

RECONCILIATION_SYSTEM = (
    REVIEW_SYSTEM
    + "\n\nPASSE FINALE OBLIGATOIRE : compare les décisions provisoires de lots différents. "
    "Cherche les doublons inter-lots, les formulations différentes d'une même méthode "
    "ou démarche, les contradictions de provenance et de catégorie, et distingue "
    "les résultats identiques des expériences réellement différentes. "
    "Les décisions provisoires ne sont pas des preuves : utilise les citations exactes "
    "des candidats et des faits apparentés. Retourne UNE décision finale pour chaque "
    "item_id CIBLE demandé, sans candidat supplémentaire. Si la preuve ne tranche pas, "
    "uncertain=true et project_admission=false."
)

THEMATIC_SYSTEM = """
Organise les faits candidats d'un dossier R&D en thèmes techniques pour préparer
une relecture globale. Un même thème peut réunir des faits provenant de plusieurs
documents et de catégories ou provenances locales différentes. Regroupe aussi
les formulations sémantiquement équivalentes qui n'utilisent pas les mêmes mots.
N'évalue pas encore si un fait est vrai, projet ou littérature. Ne supprime rien.
Retourne uniquement {"themes":[{"theme_id":"T1","item_ids":["..."]}]}.
Chaque item_id fourni doit figurer exactement une fois ; n'en invente aucun.
""".strip()


def _clean_role(value: object) -> str:
    role = str(value or "unknown").strip().lower()
    return "context" if role == "contextual" else role if role in ROLES else "unknown"


def _round_robin(items: list[ExtractionItem]) -> list[ExtractionItem]:
    by_doc: dict[str, list[ExtractionItem]] = defaultdict(list)
    for item in items:
        by_doc[item.evidence.document_id].append(item)
    ordered = []
    while any(by_doc.values()):
        for document_id in sorted(by_doc):
            if by_doc[document_id]:
                ordered.append(by_doc[document_id].pop(0))
    return ordered


def _document_map(chunks: Iterable[Chunk]) -> str:
    sections: dict[str, list[str]] = defaultdict(list)
    names: dict[str, str] = {}
    for chunk in chunks:
        doc_id = chunk.document_id
        names[doc_id] = chunk.document_name
        if chunk.section_title and chunk.section_title not in sections[doc_id]:
            sections[doc_id].append(chunk.section_title)
    return "\n".join(
        f"DOCUMENT {doc_id} | {names[doc_id]} | sections: "
        + " ; ".join(sections[doc_id])
        for doc_id in sorted(names)
    )


def _global_index(items: list[ExtractionItem], budget: int) -> str:
    """Index d'orientation facultatif ; les preuves intégrales restent dans les lots."""
    for statement_limit in (180, 90, 0):
        lines = []
        for item in items:
            statement = re.sub(r"\s+", " ", item.statement)
            headline = f" | {statement[:statement_limit]}" if statement_limit else ""
            lines.append(
                f"{item.item_id} | {item.kind} | {item.evidence.document_id} "
                f"| {item.evidence.evidence_id} | "
                f"{_clean_role((item.metadata or {}).get('source_role'))}{headline}"
            )
        result = "\n".join(lines)
        if len(result) <= budget:
            return result
    return ""


_THEME_STOP = {
    "avec", "dans", "pour", "cette", "leurs", "leurs", "notre", "projet",
    "solution", "resultat", "resultats", "methode", "methodes", "approche",
    "approches", "technique", "techniques", "article", "articles", "etude",
    "study", "paper", "model", "modele", "models", "systeme", "systemes",
    "between", "using", "their", "these", "this", "that", "from", "result",
    "results", "method", "methods", "project", "travaux", "utilisation",
    "utiliser", "plusieurs", "differentes", "differents", "permet", "permettre",
}


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(char for char in value if not unicodedata.combining(char))


def _theme_terms(item: ExtractionItem) -> set[str]:
    text = " ".join((item.statement, item.technical_object, item.unresolved_question))
    words = re.findall(r"[a-z0-9]{4,}", _fold(text))
    return {word.rstrip("s") for word in words if word not in _THEME_STOP}


def _relatedness(left: ExtractionItem, right: ExtractionItem) -> float:
    if left.evidence.evidence_id == right.evidence.evidence_id:
        return 1.0
    a, b = _theme_terms(left), _theme_terms(right)
    overlap = len(a & b) / min(len(a), len(b)) if a and b else 0.0
    sequence = SequenceMatcher(None, _fold(left.statement), _fold(right.statement)).ratio()
    return max(overlap, sequence if sequence >= 0.78 else 0.0)


def _thematic_components(items: list[ExtractionItem]) -> list[list[ExtractionItem]]:
    """Relie les formulations proches sans tenir compte du document ni du type local."""
    parent = list(range(len(items)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _relatedness(items[i], items[j]) >= 0.34:
                parent[root(j)] = root(i)
    groups: dict[int, list[ExtractionItem]] = defaultdict(list)
    for i, item in enumerate(items):
        groups[root(i)].append(item)
    # Les composants sont thématiques ; chaque composant entrelace ses documents.
    return sorted(
        (_round_robin(group) for group in groups.values()),
        key=lambda group: (-len({item.evidence.document_id for item in group}), -len(group), group[0].item_id),
    )


def _decision_capacity(max_output_tokens: int) -> int:
    # Inclut une marge pour le raisonnement élevé et les justifications JSON.
    return max(1, (int(max_output_tokens) - 900) // 250)


def _fits(prompt: str, system: str, count: int, config: GlobalReviewerConfig) -> bool:
    return (
        count <= _decision_capacity(config.max_output_tokens)
        and len(prompt) + len(system) + 180 <= config.max_prompt_chars
    )


def _candidate_block(item: ExtractionItem) -> str:
    evidence = item.evidence
    return (
        f"ITEM_ID: {item.item_id}\nTYPE_LOCAL: {item.kind}\n"
        f"PROVENANCE_LOCALE: {_clean_role((item.metadata or {}).get('source_role'))}\n"
        f"DOCUMENT_ID: {evidence.document_id}\nDOCUMENT: {evidence.document_name}\n"
        f"SECTION: {evidence.section_title}\nCHUNK_ID: {evidence.chunk_id}\n"
        f"TEMPORALITE: {item.temporal_status}\nFAIT: {item.statement}\n"
        f"EVIDENCE_ID: {evidence.evidence_id}\n"
        f"CITATION_VERIFIEE: {evidence.quote_verified}\nCITATION_EXACTE: {evidence.quote}"
    )


class GlobalProjectReviewer:
    def __init__(self, llm: LLMClientProtocol, config: GlobalReviewerConfig):
        self.llm = llm
        self.config = config

    def _plan_themes(self, items: list[ExtractionItem]) -> tuple[list[list[ExtractionItem]], str]:
        """Plan sémantique du modèle ; regroupement déterministe si indisponible."""
        fallback = _thematic_components(items)
        prompt = "FAITS À ORGANISER (la classification finale utilisera les preuves exactes) :\n" + "\n".join(
            f"{item.item_id} | document={item.evidence.document_id} | "
            f"type_local={item.kind} | provenance_locale="
            f"{_clean_role((item.metadata or {}).get('source_role'))} | "
            f"objet={item.technical_object} | fait={item.statement}"
            for item in items
        )
        if len(prompt) + len(THEMATIC_SYSTEM) + 180 > self.config.max_prompt_chars:
            return fallback, "deterministic"
        if len(items) > max(1, self.config.max_output_tokens // 30):
            return fallback, "deterministic"
        try:
            data = parse_json_payload(self.llm.extract_json(THEMATIC_SYSTEM, prompt))
            themes = data.get("themes") if isinstance(data, dict) else None
            if not isinstance(themes, list) or not themes:
                raise ValueError("invalid_theme_plan")
            by_id = {item.item_id: item for item in items}
            used = []
            groups = []
            for theme in themes:
                if not isinstance(theme, dict) or not isinstance(theme.get("item_ids"), list):
                    raise ValueError("invalid_theme_group")
                ids = [str(value) for value in theme["item_ids"]]
                if not ids or any(item_id not in by_id for item_id in ids):
                    raise ValueError("unknown_theme_item_id")
                used.extend(ids)
                groups.append(_round_robin([by_id[item_id] for item_id in ids]))
            if len(used) != len(set(used)) or set(used) != set(by_id):
                raise ValueError("incomplete_theme_coverage")
            groups.sort(key=lambda group: (
                -len({item.evidence.document_id for item in group}),
                -len(group), group[0].item_id,
            ))
            return groups, "model"
        except Exception:
            return fallback, "deterministic"

    @staticmethod
    def _initial_prompt(context: str, targets: list[ExtractionItem]) -> str:
        return (
            context
            + "\nITEM_ID CIBLES (une décision par identifiant) : "
            + ", ".join(item.item_id for item in targets)
            + "\n\nPASSAGES EXACTS DES CIBLES :\n"
            + "\n\n---\n\n".join(_candidate_block(item) for item in targets)
        )

    @staticmethod
    def _compact_context(
        items: list[ExtractionItem],
        document_map: str,
        entity_index: str,
        budget: int,
    ) -> str:
        index = _global_index(items, max(300, budget // 3))
        parts = [f"DOSSIER : {len(items)} faits, "
                 f"{len({item.evidence.document_id for item in items})} documents."]
        if len(document_map) <= budget // 3:
            parts.append("PLAN DES DOCUMENTS :\n" + document_map)
        else:
            parts.append(
                "Le plan complet dépasse le budget d'orientation. "
                "Les documents, sections et citations exacts figurent dans chaque lot."
            )
        if index:
            parts.append("INDEX GLOBAL D'ORIENTATION (les citations des lots font foi) :\n" + index)
        else:
            parts.append(
                "Index global trop long pour un appel. Le registre exhaustif des item_id "
                "reste en Python ; chaque fait est soumis avec sa citation exacte."
            )
        if len(entity_index) <= budget // 4:
            parts.append("GROUPES TECHNIQUES INITIAUX :\n" + entity_index)
        return "\n\n".join(parts) + "\n\n"

    @staticmethod
    def _draft_line(item: ExtractionItem, decision: dict, limit: int = 140) -> str:
        statement = re.sub(r"\s+", " ", item.statement)
        return (
            f"{item.item_id} | lot={decision['initial_batch']} | "
            f"{decision['source_role']} | {decision['reviewed_kind']} | "
            f"projet={decision['project_admission']} | "
            f"doublon={decision['duplicate_of'] or '-'} | "
            f"document={item.evidence.document_id} | {statement[:limit]}"
        )

    def _request_decisions(
        self,
        system: str,
        prompt: str,
        targets: list[ExtractionItem],
        by_id: dict[str, ExtractionItem],
        original_entities_by_item: dict[str, list[str]],
        evidence_to_item: dict[str, list[ExtractionItem]],
    ) -> dict[str, dict]:
        if not _fits(prompt, system, len(targets), self.config):
            raise ValueError("review_prompt_exceeds_budget")
        raw = self.llm.extract_json(system, prompt)
        audit_entry = {
            "stage": "reconciliation" if system == RECONCILIATION_SYSTEM else "initial",
            "target_item_ids": [item.item_id for item in targets],
            "response": raw,
            "validation_errors": {},
        }
        client = getattr(self.llm, "client", None)
        if client is not None and hasattr(client, "get_last_generation_meta"):
            meta = client.get_last_generation_meta()
            if isinstance(meta, dict):
                audit_entry["generation"] = {key: meta.get(key) for key in (
                    "provider", "model", "response_model", "response_id", "elapsed_seconds",
                    "prompt_tokens", "completion_tokens", "total_tokens",
                )}
        self._review_audit.append(audit_entry)
        payload = parse_json_payload(raw)
        rows = payload.get("decisions") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("missing_decisions")
        expected = {item.item_id for item in targets}
        ids = [str(row.get("item_id") or "") for row in rows if isinstance(row, dict)]
        if len(ids) != len(rows) or len(set(ids)) != len(ids) or not set(ids) <= expected:
            raise ValueError("invalid_or_extra_item_ids")
        normalized = {}
        errors = audit_entry["validation_errors"]
        for row in rows:
            item_id = str(row["item_id"])
            try:
                normalized[item_id] = self._normalize_decision(
                    row, by_id[item_id], by_id, original_entities_by_item,
                    evidence_to_item,
                )
            except ValueError as exc:
                errors[item_id] = str(exc)
        for item in targets:
            if item.item_id not in normalized:
                errors.setdefault(item.item_id, f"missing_decision:{item.item_id}")
        if errors:
            # Les décisions valides du lot sont conservées. Seuls les items
            # invalides sont renvoyés avec leur citation exacte.
            for item_id, first_error in list(errors.items()):
                item = by_id[item_id]
                retry_entry = None
                retry_prompt = self._initial_prompt(
                    "REPRISE CIBLEE. Corrige la décision invalide : " + first_error + "\n\n",
                    [item],
                )
                if not _fits(retry_prompt, system, 1, self.config):
                    retry_prompt = self._initial_prompt("", [item])
                    if not _fits(retry_prompt, system, 1, self.config):
                        raise ValueError(f"targeted_retry_exceeds_budget:{item_id}")
                try:
                    retry_raw = self.llm.extract_json(system, retry_prompt)
                    retry_entry = {
                        "stage": "targeted_retry", "target_item_ids": [item_id],
                        "response": retry_raw, "validation_errors": {},
                    }
                    self._review_audit.append(retry_entry)
                    retry_payload = parse_json_payload(retry_raw)
                    retry_rows = retry_payload.get("decisions") if isinstance(retry_payload, dict) else None
                    if (
                        not isinstance(retry_rows, list)
                        or len(retry_rows) != 1
                        or not isinstance(retry_rows[0], dict)
                        or retry_rows[0].get("item_id") != item_id
                    ):
                        raise ValueError(f"invalid_targeted_item_ids:{item_id}")
                    normalized[item_id] = self._normalize_decision(
                        retry_rows[0], item, by_id, original_entities_by_item,
                        evidence_to_item,
                    )
                except Exception as exc:
                    if retry_entry is not None:
                        retry_entry["validation_errors"][item_id] = str(exc)
                    normalized[item_id] = self._needs_review_decision(
                        item, original_entities_by_item,
                        f"{first_error}; retry: {type(exc).__name__}:{exc}",
                    )
                # Une indisponibilité de l'API est systémique : le fallback
                # historique conserve alors les réponses déjà reçues dans l'audit.
        return normalized

    @staticmethod
    def _needs_review_decision(item, original_entities_by_item, reason: str) -> dict:
        return {
            "item_id": item.item_id,
            "original_item_ids": [item.item_id],
            "original_entity_ids": list(original_entities_by_item.get(item.item_id, [])),
            "original_kind": item.kind,
            "reviewed_kind": item.kind,
            "original_source_role": _clean_role((item.metadata or {}).get("source_role")),
            "source_role": "unknown", "project_admission": False,
            "needs_review": True, "duplicate_of": None,
            "review_recovery": "targeted_retry_failed",
            "evidence_ids": [item.evidence.evidence_id],
            "document_id": item.evidence.document_id,
            "document_name": item.evidence.document_name,
            "quote": item.evidence.quote,
            "temporal_status": item.temporal_status,
            "related_project_evidence_ids": [], "related_lock_ids": [],
            "reason": "Décision non validée après reprise ciblée : " + reason,
        }

    @staticmethod
    def _normalize_decision(row, item, by_id, original_entities_by_item, evidence_to_item) -> dict:
        item_id = item.item_id
        role = str(row.get("source_role") or "").strip().lower()
        kind = str(row.get("kind") or "").strip().lower()
        if role not in ROLES or kind not in KINDS:
            raise ValueError(f"invalid_role_or_kind:{item_id}")
        if not isinstance(row.get("project_admission"), bool) or not isinstance(row.get("uncertain"), bool):
            raise ValueError(f"invalid_admission_or_uncertainty:{item_id}")
        if row.get("original_item_ids") != [item_id]:
            raise ValueError(f"invalid_original_item_ids:{item_id}")
        if row.get("evidence_ids") != [item.evidence.evidence_id]:
            raise ValueError(f"invalid_evidence_ids:{item_id}")
        if "quote" in row and row["quote"] != item.evidence.quote:
            raise ValueError(f"invented_quote:{item_id}")
        if "document_id" in row and row["document_id"] != item.evidence.document_id:
            raise ValueError(f"invented_document:{item_id}")
        uncertain = row["uncertain"]
        admission = row["project_admission"]
        if admission and (role != "project_evidence" or uncertain or not item.evidence.quote_verified):
            raise ValueError(f"unsupported_project_admission:{item_id}")
        if admission and kind == "result" and item.temporal_status == "planned":
            raise ValueError(f"planned_result_admitted:{item_id}")
        duplicate_of = str(row.get("duplicate_of") or "").strip() or None
        if duplicate_of and (duplicate_of not in by_id or duplicate_of == item_id):
            raise ValueError(f"invalid_duplicate_reference:{item_id}")
        linked_evidence = row.get("related_project_evidence_ids")
        linked_locks = row.get("related_lock_ids")
        if not isinstance(linked_evidence, list) or not isinstance(linked_locks, list):
            raise ValueError(f"invalid_related_ids:{item_id}")
        linked_evidence = list(dict.fromkeys(str(value) for value in linked_evidence))
        linked_locks = list(dict.fromkeys(str(value) for value in linked_locks))
        if any(
            not any(other.evidence.quote_verified for other in evidence_to_item.get(evidence_id, []))
            for evidence_id in linked_evidence
        ):
            raise ValueError(f"invalid_related_evidence:{item_id}")
        if any(lock_id not in by_id or by_id[lock_id].kind != "lock" for lock_id in linked_locks):
            raise ValueError(f"invalid_related_lock:{item_id}")
        reason = str(row.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"missing_review_reason:{item_id}")
        return {
            "item_id": item_id,
            "original_item_ids": [item_id],
            "original_entity_ids": list(original_entities_by_item.get(item_id, [])),
            "original_kind": item.kind,
            "reviewed_kind": kind,
            "original_source_role": _clean_role((item.metadata or {}).get("source_role")),
            "source_role": role,
            "project_admission": admission,
            "needs_review": uncertain or role == "unknown" or not item.evidence.quote_verified,
            "duplicate_of": duplicate_of,
            "evidence_ids": [item.evidence.evidence_id],
            "document_id": item.evidence.document_id,
            "document_name": item.evidence.document_name,
            "quote": item.evidence.quote,
            "temporal_status": item.temporal_status,
            "related_project_evidence_ids": linked_evidence,
            "related_lock_ids": linked_locks,
            "reason": reason,
        }

    def _review_with_recovery(
        self,
        system: str,
        targets: list[ExtractionItem],
        prompt_factory,
        by_id: dict[str, ExtractionItem],
        original_entities_by_item: dict[str, list[str]],
        evidence_to_item: dict[str, list[ExtractionItem]],
    ) -> list[tuple[list[ExtractionItem], dict[str, dict]]]:
        try:
            prompt = prompt_factory(targets)
            return [(
                targets,
                self._request_decisions(
                    system, prompt, targets, by_id,
                    original_entities_by_item, evidence_to_item,
                ),
            )]
        except Exception:
            if len(targets) == 1:
                raise
            middle = len(targets) // 2
            return (
                self._review_with_recovery(
                    system, targets[:middle], prompt_factory, by_id,
                    original_entities_by_item, evidence_to_item,
                )
                + self._review_with_recovery(
                    system, targets[middle:], prompt_factory, by_id,
                    original_entities_by_item, evidence_to_item,
                )
            )

    def _pack_themes(self, components: list[list[ExtractionItem]], prompt_factory, system: str) -> list[list[ExtractionItem]]:
        batches: list[list[ExtractionItem]] = []
        current: list[ExtractionItem] = []
        for component in components:
            for item in component:
                proposal = current + [item]
                fits = False
                try:
                    fits = _fits(prompt_factory(proposal), system, len(proposal), self.config)
                except ValueError:
                    pass
                if not fits and current:
                    batches.append(current)
                    current = [item]
                    if not _fits(prompt_factory(current), system, 1, self.config):
                        raise ValueError(f"single_candidate_exceeds_context_budget:{item.item_id}")
                elif not fits:
                    raise ValueError(f"single_candidate_exceeds_context_budget:{item.item_id}")
                else:
                    current = proposal
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _cross_batch_anchors(
        targets: list[ExtractionItem],
        items: list[ExtractionItem],
        batch_by_id: dict[str, int],
        theme_by_id: dict[str, int],
    ) -> list[ExtractionItem]:
        target_ids = {item.item_id for item in targets}
        scored: dict[str, tuple[float, ExtractionItem]] = {}
        for target in targets:
            candidates = [
                (max(
                    _relatedness(target, other),
                    1.0 if theme_by_id[target.item_id] == theme_by_id[other.item_id] else 0.0,
                ), other)
                for other in items
                if other.item_id not in target_ids
                and batch_by_id[other.item_id] != batch_by_id[target.item_id]
            ]
            candidates.sort(key=lambda pair: (-pair[0], pair[1].item_id))
            for score, other in candidates[:3]:
                if score >= 0.20:
                    previous = scored.get(other.item_id)
                    if previous is None or score > previous[0]:
                        scored[other.item_id] = (score, other)
        return [
            pair[1] for pair in sorted(
                scored.values(), key=lambda pair: (-pair[0], pair[1].item_id)
            )
        ]

    def _reconcile_prompt(
        self,
        targets: list[ExtractionItem],
        common: str,
        drafts: dict[str, dict],
        all_items: list[ExtractionItem],
        batch_by_id: dict[str, int],
        theme_by_id: dict[str, int],
    ) -> str:
        target_blocks = "\n\n---\n\n".join(
            self._draft_line(item, drafts[item.item_id])
            + "\n" + _candidate_block(item)
            for item in targets
        )
        prompt = (
            common
            + "\nDECISIONS FINALES DEMANDEES UNIQUEMENT POUR : "
            + ", ".join(item.item_id for item in targets)
            + "\n\nCIBLES, DECISIONS PROVISOIRES ET PREUVES EXACTES :\n"
            + target_blocks
        )
        if not _fits(prompt, RECONCILIATION_SYSTEM, len(targets), self.config):
            raise ValueError("reconciliation_targets_exceed_budget")
        anchors = self._cross_batch_anchors(targets, all_items, batch_by_id, theme_by_id)
        for anchor in anchors:
            block = (
                "\n\nFAIT APPARENTE D'UN AUTRE LOT (CONTEXTE, PAS UNE CIBLE) :\n"
                + self._draft_line(anchor, drafts[anchor.item_id])
                + "\n" + _candidate_block(anchor)
            )
            if _fits(prompt + block, RECONCILIATION_SYSTEM, len(targets), self.config):
                prompt += block
            elif any(
                _relatedness(anchor, target) >= 0.34
                or theme_by_id[anchor.item_id] == theme_by_id[target.item_id]
                for target in targets
            ):
                # Une contradiction potentielle ne peut être tranchée sans preuve.
                raise ValueError("cross_batch_evidence_exceeds_budget")
        return prompt

    def _reconcile(
        self,
        items: list[ExtractionItem],
        drafts: dict[str, dict],
        components: list[list[ExtractionItem]],
        batch_by_id: dict[str, int],
        document_map: str,
        entity_index: str,
        by_id: dict[str, ExtractionItem],
        original_entities_by_item: dict[str, list[str]],
        evidence_to_item: dict[str, list[ExtractionItem]],
    ) -> tuple[dict[str, dict], int]:
        theme_by_id = {
            item.item_id: number
            for number, component in enumerate(components)
            for item in component
        }
        full_context = (
            f"PLAN MULTI-DOCUMENTS :\n{document_map}\n\n"
            f"GROUPES INITIAUX :\n{entity_index}\n\n"
            "DECISIONS PROVISOIRES DE TOUS LES LOTS :\n"
            + "\n".join(self._draft_line(item, drafts[item.item_id]) for item in items)
            + "\n\n"
        )
        try:
            full_prompt = self._reconcile_prompt(
                items, full_context, drafts, items, batch_by_id, theme_by_id
            )
        except ValueError:
            full_prompt = ""
        if full_prompt and _fits(full_prompt, RECONCILIATION_SYSTEM, len(items), self.config):
            groups = self._review_with_recovery(
                RECONCILIATION_SYSTEM, items,
                lambda targets: self._reconcile_prompt(
                    targets, full_context, drafts, items, batch_by_id, theme_by_id
                ),
                by_id, original_entities_by_item, evidence_to_item,
            )
        else:
            compact = self._compact_context(
                items, document_map, entity_index, self.config.max_prompt_chars // 4
            )
            draft_index = "\n".join(
                self._draft_line(item, drafts[item.item_id], limit=75)
                for item in items
            )
            if len(draft_index) <= self.config.max_prompt_chars // 6:
                compact += "INDEX GLOBAL DES DECISIONS PROVISOIRES :\n" + draft_index + "\n\n"
            contexts = [
                compact,
                self._compact_context(items, document_map, entity_index,
                                      self.config.max_prompt_chars // 6),
                f"DOSSIER GLOBAL : {len(items)} faits et "
                f"{len({item.evidence.document_id for item in items})} documents.\n\n",
            ]
            planned = None
            factory = None
            for context in contexts:
                candidate_factory = lambda targets, context=context: self._reconcile_prompt(
                    targets, context, drafts, items, batch_by_id, theme_by_id
                )
                try:
                    planned = self._pack_themes(components, candidate_factory, RECONCILIATION_SYSTEM)
                    factory = candidate_factory
                    break
                except ValueError:
                    continue
            if planned is None or factory is None:
                raise ValueError("reconciliation_evidence_exceeds_context_budget")
            groups = []
            for batch in planned:
                groups.extend(self._review_with_recovery(
                    RECONCILIATION_SYSTEM, batch, factory, by_id,
                    original_entities_by_item, evidence_to_item,
                ))
        final = {}
        for _, decisions in groups:
            if set(final) & set(decisions):
                raise ValueError("reconciliation_duplicate_item_id")
            final.update(decisions)
        if set(final) != set(by_id):
            raise ValueError("reconciliation_coverage_incomplete")
        return final, len(groups)

    @staticmethod
    def _validate_final(
        decisions: dict[str, dict],
        items: list[ExtractionItem],
        evidence_to_item: dict[str, list[ExtractionItem]],
    ) -> None:
        by_id = {item.item_id: item for item in items}
        if set(decisions) != set(by_id):
            raise ValueError("final_item_id_coverage_incomplete")
        for item_id, decision in decisions.items():
            item = by_id[item_id]
            if decision["source_role"] not in ROLES or decision["reviewed_kind"] not in KINDS:
                raise ValueError(f"invalid_final_enum:{item_id}")
            if decision["project_admission"] and (
                decision["source_role"] != "project_evidence"
                or decision["needs_review"]
                or not item.evidence.quote_verified
            ):
                raise ValueError(f"unsupported_final_project_admission:{item_id}")
            if decision["original_item_ids"] != [item_id]:
                raise ValueError(f"invalid_final_original_item_id:{item_id}")
            if decision["evidence_ids"] != [item.evidence.evidence_id]:
                raise ValueError(f"invalid_final_evidence_id:{item_id}")
            if decision["quote"] != item.evidence.quote or decision["document_id"] != item.evidence.document_id:
                raise ValueError(f"invalid_final_source:{item_id}")
            for evidence_id in decision["related_project_evidence_ids"]:
                if not any(
                    decisions[other.item_id]["source_role"] == "project_evidence"
                    and decisions[other.item_id]["project_admission"]
                    and other.evidence.quote_verified
                    for other in evidence_to_item.get(evidence_id, [])
                ):
                    raise ValueError(f"related_evidence_not_project:{item_id}")
            duplicate_id = decision["duplicate_of"]
            if duplicate_id:
                other = decisions[duplicate_id]
                if (
                    decision["reviewed_kind"], decision["source_role"],
                    decision["project_admission"], decision["temporal_status"]
                ) != (
                    other["reviewed_kind"], other["source_role"],
                    other["project_admission"], other["temporal_status"]
                ):
                    raise ValueError(f"incompatible_final_duplicate:{item_id}")
                visited = {item_id}
                while duplicate_id:
                    if duplicate_id in visited:
                        raise ValueError("final_duplicate_cycle")
                    visited.add(duplicate_id)
                    duplicate_id = decisions[duplicate_id]["duplicate_of"]

    @staticmethod
    def _quarantine_dependent_decisions(
        decisions: dict[str, dict],
        by_id: dict[str, ExtractionItem],
        original_entities_by_item: dict[str, list[str]],
        evidence_to_item: dict[str, list[ExtractionItem]],
    ) -> None:
        """Évite qu'une décision validée dépende d'un item mis en revue manuelle."""
        changed = True
        while changed:
            changed = False
            recovered = {
                item_id for item_id, row in decisions.items()
                if row.get("review_recovery") == "targeted_retry_failed"
            }
            for item_id, row in list(decisions.items()):
                if item_id in recovered:
                    continue
                dependent = row["duplicate_of"] if row["duplicate_of"] in recovered else None
                for evidence_id in row["related_project_evidence_ids"]:
                    source_items = evidence_to_item.get(evidence_id, [])
                    if source_items and all(other.item_id in recovered for other in source_items):
                        dependent = evidence_id
                        break
                if dependent:
                    decisions[item_id] = GlobalProjectReviewer._needs_review_decision(
                        by_id[item_id], original_entities_by_item,
                        f"dépendance à une décision non validée : {dependent}",
                    )
                    changed = True

    def review_cached(
        self, chunks, extractions, entities, *, cache_dir=None, corpus_fingerprint="",
    ) -> dict:
        """Cache only complete targeted reviews; revalidate exact evidence on replay."""
        items = list(extractions)
        chunks, entities = list(chunks), list(entities)
        signature = {
            "version": "fast-targeted-review-v1", "corpus": corpus_fingerprint,
            "prompt": REVIEW_SYSTEM + RECONCILIATION_SYSTEM,
            "provider": self.config.provider, "model": self.config.global_reasoner_model,
            "effort": self.config.reasoning_effort,
            "items": [(i.item_id, i.kind, i.statement, i.temporal_status,
                       i.evidence.evidence_id, i.evidence.document_id, i.evidence.quote,
                       i.evidence.quote_verified, i.metadata.get("source_role")) for i in items],
        }
        key = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = Path(cache_dir) / f"targeted_review_{key}.json" if cache_dir and corpus_fingerprint else None
        if path and path.is_file():
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
                if result.get("status") != "complete" or result.get("coverage") != 1.0:
                    raise ValueError("invalid_cached_review")
                by_id = {item.item_id: item for item in items}
                evidence_to_item = defaultdict(list)
                for item in items:
                    evidence_to_item[item.evidence.evidence_id].append(item)
                normalized = {}
                for row in result["decisions"]:
                    item = by_id[row["item_id"]]
                    if item.item_id in normalized or row.get("temporal_status") != item.temporal_status:
                        raise ValueError("invalid_cached_identity_or_temporality")
                    raw = {**row, "kind": row["reviewed_kind"], "uncertain": row["needs_review"]}
                    normalized[item.item_id] = self._normalize_decision(
                        raw, item, by_id, {}, evidence_to_item,
                    )
                self._validate_final(normalized, items, evidence_to_item)
                return {**result, "decisions": [normalized[i.item_id] for i in items], "cache_hit": True}
            except (OSError, ValueError, TypeError, KeyError):
                pass
        result = self.review(chunks, items, entities)
        if path and result.get("status") == "complete" and result.get("coverage") == 1.0:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
                    json.dump(result, handle, ensure_ascii=False)
                    temporary = Path(handle.name)
                os.replace(temporary, path)
            except OSError:
                pass
        return {**result, "cache_hit": False}

    def review(
        self,
        chunks: Iterable[Chunk],
        extractions: Iterable[ExtractionItem],
        entities: Iterable[CanonicalEntity],
    ) -> dict:
        items = list(extractions)
        chunks = list(chunks)
        entities = list(entities)
        self._review_audit = []
        base = {
            "status": "fallback", "model": self.config.global_reasoner_model,
            "provider": self.config.provider,
            "reasoning_effort": self.config.reasoning_effort,
            "submitted_count": len(items), "reviewed_count": 0,
            "coverage": 0.0, "decisions": [], "lock_observations": [],
            "error": "", "audit": {"responses": self._review_audit},
        }
        by_id = {item.item_id: item for item in items}
        if len(by_id) != len(items):
            return {**base, "error": "duplicate_input_item_ids"}
        if not items:
            return {**base, "status": "complete", "coverage": 1.0, "mode": "global"}
        evidence_to_item: dict[str, list[ExtractionItem]] = defaultdict(list)
        for item in items:
            evidence_to_item[item.evidence.evidence_id].append(item)
        original_entities_by_item: dict[str, list[str]] = defaultdict(list)
        for entity in entities:
            for item_id in entity.source_item_ids:
                original_entities_by_item[item_id].append(entity.entity_id)
        document_map = _document_map(chunks)
        entity_index = "\n".join(
            f"{entity.entity_id} | {entity.kind} | {','.join(entity.source_item_ids)}"
            for entity in entities
        )
        full_context = (
            f"PLAN MULTI-DOCUMENTS :\n{document_map}\n\n"
            f"GROUPES ISSUS DE LA CONSOLIDATION :\n{entity_index}\n\n"
        )
        full_prompt = self._initial_prompt(full_context, items)
        try:
            components = _thematic_components(items)
            theme_plan_source = "not_needed"
            if _fits(full_prompt, REVIEW_SYSTEM, len(items), self.config):
                groups = self._review_with_recovery(
                    REVIEW_SYSTEM, items,
                    lambda targets: self._initial_prompt(full_context, targets),
                    by_id, original_entities_by_item, evidence_to_item,
                )
                if len(groups) == 1:
                    final = groups[0][1]
                    self._quarantine_dependent_decisions(
                        final, by_id, original_entities_by_item, evidence_to_item,
                    )
                    self._validate_final(final, items, evidence_to_item)
                    ordered = [final[item.item_id] for item in items]
                    return {
                        **base, "status": "complete", "mode": "global",
                        "reviewed_count": len(ordered), "coverage": 1.0,
                        "decisions": ordered,
                        "lock_observations": [d for d in ordered if d["original_kind"] == "lock"],
                        "batch_count": 1, "reconciliation_calls": 0, "error": "",
                    }
                components, theme_plan_source = self._plan_themes(items)
            else:
                components, theme_plan_source = self._plan_themes(items)
                planned = None
                factory = None
                contexts = [
                    self._compact_context(items, document_map, entity_index,
                                          self.config.max_prompt_chars // 2),
                    self._compact_context(items, document_map, entity_index,
                                          self.config.max_prompt_chars // 6),
                    f"DOSSIER GLOBAL : {len(items)} faits et "
                    f"{len({item.evidence.document_id for item in items})} documents.\n\n",
                ]
                for context in contexts:
                    candidate_factory = lambda targets, context=context: self._initial_prompt(context, targets)
                    try:
                        planned = self._pack_themes(components, candidate_factory, REVIEW_SYSTEM)
                        factory = candidate_factory
                        break
                    except ValueError:
                        continue
                if planned is None or factory is None:
                    raise ValueError("candidate_evidence_exceeds_context_budget")
                groups = []
                for batch in planned:
                    groups.extend(self._review_with_recovery(
                        REVIEW_SYSTEM, batch, factory, by_id,
                        original_entities_by_item, evidence_to_item,
                    ))
            drafts: dict[str, dict] = {}
            batch_by_id: dict[str, int] = {}
            for batch_number, (batch, decisions) in enumerate(groups, 1):
                if set(drafts) & set(decisions):
                    raise ValueError("initial_duplicate_item_id")
                for item in batch:
                    batch_by_id[item.item_id] = batch_number
                    decisions[item.item_id]["initial_batch"] = batch_number
                drafts.update(decisions)
            if set(drafts) != set(by_id):
                raise ValueError("initial_coverage_incomplete")
            final, reconcile_count = self._reconcile(
                items, drafts, components, batch_by_id, document_map, entity_index,
                by_id, original_entities_by_item, evidence_to_item,
            )
            # Une reprise ciblée échouée ne peut être réadmise par la passe
            # finale, même si cette dernière produit une décision plausible.
            for item_id, draft in drafts.items():
                if draft.get("review_recovery") == "targeted_retry_failed":
                    final[item_id] = draft
            self._quarantine_dependent_decisions(
                final, by_id, original_entities_by_item, evidence_to_item,
            )
            self._validate_final(final, items, evidence_to_item)
            ordered = [final[item.item_id] for item in items]
            return {
                **base, "status": "complete", "mode": "multi_lot",
                "reviewed_count": len(ordered), "coverage": 1.0,
                "decisions": ordered,
                "lock_observations": [d for d in ordered if d["original_kind"] == "lock"],
                "batch_count": len(groups),
                "reconciliation_calls": reconcile_count,
                "theme_plan_source": theme_plan_source,
                "error": "",
            }
        except Exception as exc:
            # Aucun résultat partiel n'est activé ; toutes les sources brutes restent intactes.
            return {**base, "error": f"{type(exc).__name__}:{exc}"}

def reviewed_fact_view(
    extractions: Iterable[ExtractionItem],
    qualified_entities: Iterable[CanonicalEntity],
    review: dict,
) -> tuple[list[ExtractionItem], list[CanonicalEntity]]:
    """Vue de synthèse ; les ExtractionItem et CanonicalEntity d'origine restent intacts."""
    if review.get("status") != "complete" or review.get("coverage") != 1.0:
        raise ValueError("global_review_not_complete")
    originals = list(extractions)
    decisions = {row["item_id"]: row for row in review.get("decisions") or []}
    if set(decisions) != {item.item_id for item in originals}:
        raise ValueError("global_review_coverage_mismatch")

    view_items = []
    for item in originals:
        if item.kind == "lock":
            view_items.append(item)
            continue
        decision = decisions[item.item_id]
        # Un nouveau candidat verrou est réservé à la phase 2 : aucune
        # qualification ou synthèse de verrou nouvelle pendant cette phase.
        if decision["reviewed_kind"] == "lock":
            continue
        if not decision["project_admission"] and decision["source_role"] != "literature":
            continue
        metadata = dict(item.metadata)
        metadata.update({
            "source_role": decision["source_role"],
            "project_admission": decision["project_admission"],
            "global_reviewed": True,
            "original_item_id": item.item_id,
            "original_kind": item.kind,
            "original_source_role": decision["original_source_role"],
        })
        view_items.append(replace(
            item,
            kind=decision["reviewed_kind"],
            needs_review=(
                decision["needs_review"] if (item.metadata or {}).get("fast_path")
                else item.needs_review or decision["needs_review"]
            ),
            metadata=metadata,
        ))

    groups: dict[str, list[ExtractionItem]] = defaultdict(list)
    by_view_id = {item.item_id: item for item in view_items}
    for item in view_items:
        if item.kind == "lock" or item.metadata.get("source_role") != "project_evidence":
            continue
        root_id = item.item_id
        while decisions[root_id]["duplicate_of"]:
            root_id = decisions[root_id]["duplicate_of"]
        if root_id not in by_view_id:
            raise ValueError("duplicate_root_not_in_project_view")
        groups[root_id].append(item)

    view_entities = [entity for entity in qualified_entities if entity.kind == "lock"]
    for root_id, members in groups.items():
        root = by_view_id[root_id]
        evidences = list({item.evidence.evidence_id: item.evidence for item in members}.values())
        view_entities.append(CanonicalEntity(
            entity_id=f"reviewed_{root_id}",
            kind=root.kind,
            statement=root.statement,
            source_item_ids=[item.item_id for item in members],
            evidences=evidences,
            document_ids=sorted({item.evidence.document_id for item in members}),
            needs_review=any(item.needs_review for item in members),
            metadata={"global_reviewed": True, "original_item_ids": [item.item_id for item in members]},
        ))
    return view_items, view_entities
