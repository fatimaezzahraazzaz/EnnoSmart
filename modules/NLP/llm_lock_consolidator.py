# -*- coding: utf-8 -*-
from __future__ import annotations

"""Consolidation globale des verrous par un seul appel LLM.

Pipeline cible :
FastJudge -> déduplication mécanique -> LLM global -> Frascati -> humain.

Ce module ne calcule aucune similarité cosinus, aucun linkage et aucun NLI
pairwise. Le LLM décide conceptuellement des verrous parents en regardant tous
les candidats ensemble. La sortie est ensuite validée de façon déterministe :
aucun ID inventé n'est accepté et tout candidat direct oublié devient un
singleton plutôt que d'être fusionné arbitrairement.
"""

import hashlib
import json
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from modules.LLM.llm_client import LLMClient

from .evidence_contract import normalize_text_key, passage_identity
from .lock_consolidation_prompt import (
    LOCK_CONSOLIDATION_SCHEMA,
    PROMPT_VERSION,
    build_prompt,
)

VERSION = "llm_global_lock_consolidator_v2_20260916"
REQUEST_NAME = "ennodiagnostic:global_lock_consolidation"

_DIRECT_ROLES = {"verrou"}
_SUPPORT_ROLES = {"limite", "resultat", "methode", "parametre", "contribution", "objectif"}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "oui", "on"}


def _role(item: Mapping[str, Any]) -> str:
    return str(
        item.get("original_model_role")
        or item.get("semantic_role")
        or item.get("role")
        or ""
    ).strip().lower()


def _score(item: Mapping[str, Any]) -> float:
    for key in (
        "lock_evidence_score",
        "lock_candidate_score",
        "verrou_score",
        "rank_score",
        "frascati_score",
    ):
        try:
            value = item.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _text(item: Mapping[str, Any]) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(item.get("analysis_text") or item.get("text") or "").strip(),
    )


def _document_key(item: Mapping[str, Any]) -> str:
    return str(item.get("source_path") or item.get("document") or "").strip().lower()


def _span(item: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    try:
        start_raw = item.get("sentence_start")
        if start_raw is None:
            return None
        start = int(start_raw)
        size = max(1, int(item.get("window_size") or 1))
        return start, start + size
    except (TypeError, ValueError):
        return None


def _token_set(value: str) -> Set[str]:
    return {
        token
        for token in normalize_text_key(value).split()
        if len(token) >= 3
    }


def _mechanical_duplicate(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Déduplique seulement des fenêtres manifestement identiques/chevauchantes.

    Cette fonction n'essaie PAS de décider si deux incertitudes ont le même sens.
    Elle retire uniquement les doublons techniques produits par le fenêtrage NLP.
    """
    if _document_key(left) != _document_key(right):
        return False

    left_text = normalize_text_key(_text(left))
    right_text = normalize_text_key(_text(right))
    if left_text and left_text == right_text:
        return True

    ls, rs = _span(left), _span(right)
    if not ls or not rs:
        return False
    overlap = max(0, min(ls[1], rs[1]) - max(ls[0], rs[0]))
    base = max(1, min(ls[1] - ls[0], rs[1] - rs[0]))
    if (overlap / base) < 0.80:
        return False

    lt, rt = _token_set(left_text), _token_set(right_text)
    union = lt | rt
    lexical_overlap = (len(lt & rt) / len(union)) if union else 0.0
    return lexical_overlap >= 0.72


def _is_direct(item: Mapping[str, Any], explicit_direct_ids: Set[str]) -> bool:
    pid = passage_identity(item)
    if pid in explicit_direct_ids:
        return True
    return bool(
        _truthy(item.get("direct_lock_candidate"))
        or _truthy(item.get("project_lock_seed"))
        or _truthy(item.get("lock_candidate_explicit"))
        or _role(item) in _DIRECT_ROLES
    )


def _is_support_candidate(item: Mapping[str, Any]) -> bool:
    return bool(
        _truthy(item.get("supporting_lock_evidence"))
        or _truthy(item.get("lock_support"))
        or _truthy(item.get("support_evidence"))
    )


def _prepare_items(
    items: Sequence[Mapping[str, Any]],
    direct_candidates: Optional[Sequence[Mapping[str, Any]]],
) -> Tuple[List[Dict[str, Any]], Set[str], List[Dict[str, Any]]]:
    explicit_direct_ids = {
        passage_identity(item)
        for item in (direct_candidates or [])
        if isinstance(item, Mapping)
    }

    all_items: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for raw in list(items or []) + list(direct_candidates or []):
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        pid = passage_identity(item)
        item["passage_id"] = pid
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        all_items.append(item)

    direct_ids = {
        passage_identity(item)
        for item in all_items
        if _is_direct(item, explicit_direct_ids)
    }

    # On envoie au LLM tous les candidats directs + les preuves déjà signalées
    # comme supports par le pipeline amont. Les autres passages restent hors de
    # la décision de regroupement et ne peuvent pas créer de verrou.
    active = [
        item
        for item in all_items
        if passage_identity(item) in direct_ids or _is_support_candidate(item)
    ]

    # Si aucun support n'a été pré-marqué, on conserve quelques limites/résultats
    # les mieux scorés. Cela évite de perdre une preuve évidente sans réintroduire
    # un moteur de clustering complexe.
    if not any(_is_support_candidate(item) for item in active):
        extras = [
            item
            for item in all_items
            if passage_identity(item) not in direct_ids and _role(item) in _SUPPORT_ROLES
        ]
        extras.sort(key=lambda x: (_score(x), len(_text(x))), reverse=True)
        active.extend(extras[:12])

    return active, direct_ids, all_items


def _dedupe_active(
    active: Sequence[Mapping[str, Any]],
    direct_ids: Set[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]], Dict[str, str]]:
    ordered = sorted(
        (dict(item) for item in active),
        key=lambda x: (
            passage_identity(x) in direct_ids,
            _score(x),
            len(_text(x)),
        ),
        reverse=True,
    )
    kept: List[Dict[str, Any]] = []
    aliases: Dict[str, List[str]] = {}
    duplicate_to_rep: Dict[str, str] = {}

    for item in ordered:
        pid = passage_identity(item)
        representative = next(
            (other for other in kept if _mechanical_duplicate(item, other)),
            None,
        )
        if representative is None:
            kept.append(item)
            aliases.setdefault(pid, [])
            continue
        rep_id = passage_identity(representative)
        aliases.setdefault(rep_id, []).append(pid)
        duplicate_to_rep[pid] = rep_id

    return kept, aliases, duplicate_to_rep


def _compact_record(item: Mapping[str, Any], direct_ids: Set[str]) -> Dict[str, Any]:
    pid = passage_identity(item)
    text = _text(item)
    before = re.sub(r"\s+", " ", str(item.get("context_before") or "").strip())
    after = re.sub(r"\s+", " ", str(item.get("context_after") or "").strip())
    return {
        "id": pid,
        "kind": "LOCK_CANDIDATE" if pid in direct_ids else "SUPPORT_CANDIDATE",
        "document": str(item.get("document") or item.get("source_path") or "")[:180],
        "section": str(item.get("section_title") or "")[:220],
        "role": _role(item),
        "score_hint": round(_score(item), 4),
        "text": text[:900],
        "context_before": before[-260:],
        "context_after": after[:260],
    }


def _records_json(records: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(list(records), ensure_ascii=False, separators=(",", ":"))
    if len(payload) <= 24500:
        return payload

    compact = []
    for record in records:
        value = dict(record)
        value["text"] = str(value.get("text") or "")[:520]
        value["context_before"] = ""
        value["context_after"] = ""
        compact.append(value)
    payload = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    if len(payload) <= 24500:
        return payload

    compact2 = []
    for record in compact:
        value = dict(record)
        value["text"] = str(value.get("text") or "")[:300]
        value["document"] = str(value.get("document") or "")[:80]
        value["section"] = str(value.get("section") or "")[:100]
        compact2.append(value)
    return json.dumps(compact2, ensure_ascii=False, separators=(",", ":"))


def _parse_json_response(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    if not isinstance(parsed, Mapping):
        raise ValueError("La réponse LLM de consolidation n'est pas un objet JSON.")
    return dict(parsed)


def _stable_group_id(member_ids: Iterable[str], support_ids: Iterable[str]) -> str:
    raw = "|".join(sorted(set(member_ids) | set(support_ids)))
    return "llm_lock_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]


def _expand_ids(ids: Iterable[str], aliases: Mapping[str, Sequence[str]]) -> List[str]:
    output: List[str] = []
    for pid in ids:
        if pid not in output:
            output.append(pid)
        for alias in aliases.get(pid, []) or []:
            if alias not in output:
                output.append(alias)
    return output


def _build_group(
    *,
    canonical: str,
    member_ids: Sequence[str],
    support_ids: Sequence[str],
    reason: str,
    confidence: float,
    by_id: Mapping[str, Mapping[str, Any]],
    aliases: Mapping[str, Sequence[str]],
    model_name: str,
    fallback_reason: Optional[str] = None,
) -> Dict[str, Any]:
    expanded_members = _expand_ids(member_ids, aliases)
    expanded_supports = _expand_ids(support_ids, aliases)
    passage_ids = list(dict.fromkeys(expanded_members + expanded_supports))
    passages: List[Dict[str, Any]] = []
    for pid in passage_ids:
        source = by_id.get(pid)
        if source is None:
            continue
        item = dict(source)
        item["llm_lock_assignment"] = (
            "member" if pid in expanded_members else "support"
        )
        passages.append(item)

    roles = sorted({_role(item) for item in passages if _role(item)})
    documents = sorted({
        str(item.get("document") or item.get("source_path") or "").strip()
        for item in passages
        if str(item.get("document") or item.get("source_path") or "").strip()
    })
    title = re.sub(r"\s+", " ", str(canonical or "").strip())
    if not title and expanded_members:
        title = _text(by_id.get(expanded_members[0], {}))[:500]

    group_id = _stable_group_id(expanded_members, expanded_supports)
    return {
        "lock_group_id": group_id,
        "group_id": group_id,
        "text": title,
        "analysis_text": title,
        "representative_text": title,
        "canonical_uncertainty": title,
        "supporting_passages": passages,
        "member_passage_ids": expanded_members,
        "support_passage_ids": expanded_supports,
        "source_semantic_roles": roles,
        "documents": documents,
        "document_count": len(documents),
        "candidate_count": len(expanded_members),
        "project_lock_seed_count": sum(
            bool(by_id.get(pid, {}).get("project_lock_seed"))
            for pid in expanded_members
        ),
        "display_as_main_lock": True,
        "technical_scope": (
            "project_structuring_lock"
            if len(expanded_members) > 1 or len(documents) > 1
            else "lock_to_validate"
        ),
        "technical_classification": "verrou_potentiel",
        "final_role": "verrou_potentiel",
        "needs_human_validation": True,
        "llm_consolidation": {
            "version": VERSION,
            "prompt_version": PROMPT_VERSION,
            "model": model_name,
            "reason": str(reason or "").strip(),
            "confidence": round(max(0.0, min(1.0, float(confidence or 0.0))), 4),
            "fallback_reason": fallback_reason,
        },
    }


def _singleton_group(
    pid: str,
    by_id: Mapping[str, Mapping[str, Any]],
    aliases: Mapping[str, Sequence[str]],
    model_name: str,
    reason: str,
) -> Dict[str, Any]:
    item = by_id.get(pid, {})
    canonical = _text(item) or "Incertitude technique à valider"
    return _build_group(
        canonical=canonical,
        member_ids=[pid],
        support_ids=[],
        reason="Candidat conservé séparément par sécurité.",
        confidence=0.0,
        by_id=by_id,
        aliases=aliases,
        model_name=model_name,
        fallback_reason=reason,
    )


def consolidate_locks(
    items: Sequence[Mapping[str, Any]],
    *,
    direct_candidates: Optional[Sequence[Mapping[str, Any]]] = None,
    llm_client: Optional[LLMClient] = None,
    llm_callable: Optional[Callable[[str, Mapping[str, Any]], Any]] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Regroupe globalement les candidats FastJudge en verrous principaux.

    ``llm_callable`` sert aux tests : callable(prompt, schema) -> dict/JSON.
    En production, le LLMClient central EnnoSmart est utilisé afin de conserver
    les hooks budget, la concurrence, le provider et la configuration existante.
    """
    active, direct_ids_original, all_items = _prepare_items(items, direct_candidates)
    by_id: Dict[str, Dict[str, Any]] = {
        passage_identity(item): dict(item)
        for item in all_items
    }

    if not direct_ids_original:
        return {
            "version": VERSION,
            "method": "fastjudge_then_global_llm_consolidation",
            "groups": [],
            "groups_count": 0,
            "candidate_passages": all_items,
            "deduplicated_windows": [],
            "audit": {
                "prompt_version": PROMPT_VERSION,
                "reason": "no_direct_lock_candidate",
                "fallback_used": False,
            },
        }

    kept, aliases, duplicate_to_rep = _dedupe_active(active, direct_ids_original)
    representative_direct_ids: Set[str] = set()
    for pid in direct_ids_original:
        representative_direct_ids.add(duplicate_to_rep.get(pid, pid))

    # Les représentants réellement transmis au LLM.
    kept_ids = {passage_identity(item) for item in kept}
    representative_direct_ids &= kept_ids
    records = [_compact_record(item, representative_direct_ids) for item in kept]

    max_items = max(8, int(os.getenv("ENNOSMART_LOCK_GROUPING_MAX_ITEMS", "60") or 60))
    if len(records) > max_items:
        # Tous les candidats directs restent prioritaires. Les supports excédentaires
        # sont coupés ; on ne tronque jamais silencieusement un verrou direct.
        direct_records = [r for r in records if r["kind"] == "LOCK_CANDIDATE"]
        support_records = [r for r in records if r["kind"] != "LOCK_CANDIDATE"]
        if len(direct_records) <= max_items:
            records = direct_records + support_records[: max_items - len(direct_records)]
        else:
            records = direct_records  # le prompt sera compacté fortement ci-dessous

    records_payload = _records_json(records)
    prompt = build_prompt(records_payload)

    lock_model = str(model or os.getenv("ENNOSMART_LOCK_GROUPING_MODEL") or "").strip() or None
    client = llm_client
    model_name = lock_model or "central_default"
    raw_response: Any = None
    generation_meta: Dict[str, Any] = {}
    llm_error: Optional[str] = None

    try:
        if llm_callable is not None:
            raw_response = llm_callable(prompt, LOCK_CONSOLIDATION_SCHEMA)
            model_name = model_name if model_name != "central_default" else "test_callable"
        else:
            if client is None:
                client = LLMClient(model=lock_model)
            model_name = str(client.model_for_request(REQUEST_NAME) or lock_model or "unknown")
            raw_response = client.generate(
                prompt,
                temperature=0.0,
                max_output_tokens=3800,
                retries=1,
                max_input_tokens=8000,
                json_mode=True,
                request_name=REQUEST_NAME,
                response_schema=LOCK_CONSOLIDATION_SCHEMA,
            )
            generation_meta = client.get_last_generation_meta()
        proposal = _parse_json_response(raw_response)
    except Exception as exc:  # fail-safe : surtout ne pas sur-fusionner
        proposal = {"locks": [], "unassigned_ids": []}
        llm_error = f"{type(exc).__name__}: {exc}"

    known_kept_ids = {str(record["id"]) for record in records}
    known_direct_ids = {
        str(record["id"])
        for record in records
        if record.get("kind") == "LOCK_CANDIDATE"
    }
    known_support_ids = known_kept_ids - known_direct_ids

    used_ids: Set[str] = set()
    unknown_ids: List[str] = []
    duplicate_assignments: List[str] = []
    groups: List[Dict[str, Any]] = []

    for raw_lock in proposal.get("locks") or []:
        if not isinstance(raw_lock, Mapping):
            continue
        raw_members = [str(x) for x in (raw_lock.get("member_ids") or []) if str(x)]
        raw_supports = [str(x) for x in (raw_lock.get("support_ids") or []) if str(x)]

        members: List[str] = []
        supports: List[str] = []
        for pid in raw_members:
            if pid not in known_kept_ids:
                unknown_ids.append(pid)
                continue
            if pid not in known_direct_ids:
                # Un support ne peut jamais devenir verrou principal.
                unknown_ids.append(pid)
                continue
            if pid in used_ids:
                duplicate_assignments.append(pid)
                continue
            used_ids.add(pid)
            members.append(pid)

        for pid in raw_supports:
            if pid not in known_kept_ids:
                unknown_ids.append(pid)
                continue
            if pid in used_ids:
                duplicate_assignments.append(pid)
                continue
            used_ids.add(pid)
            supports.append(pid)

        if not members:
            # Un groupe sans verrou principal n'est jamais accepté.
            for pid in supports:
                used_ids.discard(pid)
            continue

        try:
            confidence = float(raw_lock.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        groups.append(_build_group(
            canonical=str(raw_lock.get("canonical_uncertainty") or "").strip(),
            member_ids=members,
            support_ids=supports,
            reason=str(raw_lock.get("reason") or "").strip(),
            confidence=confidence,
            by_id=by_id,
            aliases=aliases,
            model_name=model_name,
        ))

    # Sécurité capitale : tout candidat direct non affecté reste un verrou séparé.
    # Ainsi une réponse LLM incomplète ne peut jamais faire disparaître un verrou.
    missing_direct_ids = sorted(known_direct_ids - used_ids)
    fallback_reason = "llm_error" if llm_error else "llm_unassigned_direct_candidate"
    for pid in missing_direct_ids:
        groups.append(_singleton_group(
            pid,
            by_id=by_id,
            aliases=aliases,
            model_name=model_name,
            reason=fallback_reason,
        ))
        used_ids.add(pid)

    # Les candidats directs dédupliqués dont le représentant a été transmis sont
    # automatiquement récupérés via aliases dans le groupe du représentant.
    dedup_audit = [
        {"passage_id": duplicate, "duplicate_of": representative}
        for duplicate, representative in sorted(duplicate_to_rep.items())
    ]

    # Annote la vue candidate sans supprimer les passages sources.
    candidate_passages: List[Dict[str, Any]] = []
    for item in all_items:
        value = dict(item)
        pid = passage_identity(value)
        if pid in duplicate_to_rep:
            value["deduplicated_into"] = duplicate_to_rep[pid]
        candidate_passages.append(value)

    return {
        "version": VERSION,
        "method": "fastjudge_then_mechanical_dedup_then_single_global_llm_consolidation",
        "groups": groups,
        "groups_count": len(groups),
        "candidate_passages": candidate_passages,
        "deduplicated_windows": dedup_audit,
        "audit": {
            "version": VERSION,
            "prompt_version": PROMPT_VERSION,
            "model": model_name,
            "input_items_count": len(all_items),
            "active_items_count": len(active),
            "llm_records_count": len(records),
            "direct_candidates_count": len(direct_ids_original),
            "groups_count": len(groups),
            "unknown_or_invalid_ids": sorted(set(unknown_ids)),
            "duplicate_assignments": sorted(set(duplicate_assignments)),
            "missing_direct_ids_fallback": missing_direct_ids,
            "llm_error": llm_error,
            "fallback_used": bool(llm_error or missing_direct_ids),
            "generation_meta": generation_meta,
        },
    }
