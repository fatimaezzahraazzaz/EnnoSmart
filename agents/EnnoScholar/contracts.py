# -*- coding: utf-8 -*-
from __future__ import annotations

"""Contrats métier partagés par EnnoScholar.

Ce module est volontairement indépendant du domaine scientifique. Il protège
les deux entrées qui ne doivent jamais être reconstruites silencieusement :

* les verrous confirmés par EnnoDiagnostic et le consultant ;
* le plan éventuellement modifié et validé dans le chat consultant.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class ContractError(ValueError):
    """Erreur bloquante de cohérence entre les phases."""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "status": self.code,
            "message": self.message,
            "details": self.details,
        }


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(filter(None, (clean_text(item) for item in value))).strip()
    if isinstance(value, dict):
        for key in ("text", "value", "title", "label", "name", "description"):
            text = clean_text(value.get(key))
            if text:
                return text
        return ""
    text = str(value).replace("\u00a0", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise ContractError(
            "confirmed_verrous_missing",
            f"Le fichier de verrous confirmés est introuvable : {source}",
            {"path": str(source)},
        )
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ContractError(
            "invalid_json",
            f"Le fichier JSON est illisible : {source}",
            {"path": str(source), "error": repr(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise ContractError(
            "invalid_contract_root",
            "La racine du contrat doit être un objet JSON.",
            {"path": str(source)},
        )
    return data


def parse_aliases(value: Any = None) -> Dict[str, str]:
    """Lit un mapping d'alias transmis explicitement par l'appelant.

    Formats acceptés :
    * ``{"ancien": "canonique"}``
    * ``["ancien=canonique", "x=y"]``
    * ``"ancien=canonique,x=y"``

    Les alias sont volontairement absents par défaut. Un alias propre à un
    projet ne doit jamais être appliqué implicitement aux autres projets via
    une variable d'environnement globale.
    """

    if value is None:
        value = ""
    if isinstance(value, Mapping):
        pairs = value.items()
    else:
        raw_items: Iterable[Any]
        if isinstance(value, (list, tuple, set)):
            raw_items = value
        else:
            raw_items = re.split(r"[,;\n]+", clean_text(value))
        parsed: List[Tuple[str, str]] = []
        for item in raw_items:
            text = clean_text(item)
            if not text:
                continue
            match = re.match(r"^\s*([^=:\s]+)\s*(?:=|->|:)\s*([^=:\s]+)\s*$", text)
            if not match:
                raise ContractError(
                    "invalid_alias",
                    f"Alias de verrou invalide : {text!r}",
                    {"expected": "ancien=canonique"},
                )
            parsed.append((match.group(1), match.group(2)))
        pairs = parsed

    aliases: Dict[str, str] = {}
    for raw_source, raw_target in pairs:
        source = clean_text(raw_source)
        target = clean_text(raw_target)
        if not source or not target or source == target:
            raise ContractError(
                "invalid_alias",
                f"Alias de verrou invalide : {source!r} -> {target!r}",
            )
        aliases[source] = target
    return aliases


def _explicit_verrou_items(
    payload: Mapping[str, Any],
    keys: Sequence[str],
) -> List[Dict[str, Any]]:
    """Lit une collection explicitement déclarée de verrous.

    Les versions historiques d'EnnoScholar ont sérialisé ces collections soit
    sous forme de liste, soit sous forme de dictionnaire indexé par
    ``verrou_id``. Cette compatibilité reste fail-closed : seules les clés
    métier énumérées par l'appelant sont inspectées.
    """

    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        if isinstance(value, Mapping):
            items: List[Dict[str, Any]] = []
            for raw_id, raw_item in value.items():
                if not isinstance(raw_item, Mapping):
                    continue
                item = dict(raw_item)
                if not clean_text(
                    item.get("verrou_id")
                    or item.get("id")
                    or item.get("lock_id")
                ):
                    item["verrou_id"] = clean_text(raw_id)
                items.append(item)
            if items:
                return items
    return []


def extract_verrou_items(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Extrait uniquement une liste explicitement déclarée de verrous."""

    if not isinstance(payload, Mapping):
        return []
    direct = _explicit_verrou_items(
        payload,
        (
            "confirmed_verrous",
            "canonical_verrous",
            "verrous",
            "selected_verrous",
            "verrous_selectionnes",
            "verrous_confirmes",
            "validated_verrous",
            "verrous_gap_analysis",
            "verrous_reasoning",
            "reasoning_by_verrou",
            "argumentations",
            "verrou_index",
            "verrou_sections_for_phase5",
        ),
    )
    if direct:
        return direct
    for key in (
        "data",
        "result",
        "payload",
        "diagnostic",
        "selection",
        "global_writer_blueprint",
        "consultant_blueprint",
        "dual_writer_blueprints",
        "phase5_blueprint",
    ):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            found = extract_verrou_items(nested)
            if found:
                return found
    return []


def _item_identity(item: Mapping[str, Any]) -> Tuple[str, str]:
    nested = item.get("argumentation_json")
    nested = nested if isinstance(nested, Mapping) else {}
    verrou_id = clean_text(
        item.get("verrou_id")
        or item.get("id")
        or item.get("lock_id")
        or nested.get("verrou_id")
        or nested.get("id")
    )
    title = clean_text(
        item.get("verrou_title")
        or item.get("title")
        or item.get("name")
        or item.get("label")
        or item.get("objectif_rd")
        or item.get("objective")
        or nested.get("verrou_title")
        or nested.get("title")
    )
    return verrou_id, title


def normalize_confirmed_verrous(
    items: Sequence[Mapping[str, Any]],
    *,
    aliases: Any = None,
) -> List[Dict[str, Any]]:
    alias_map = parse_aliases(aliases)
    normalized: List[Dict[str, Any]] = []
    by_id: Dict[str, Dict[str, Any]] = {}

    for index, raw in enumerate(items, 1):
        verrou_id, title = _item_identity(raw)
        if not verrou_id:
            raise ContractError(
                "missing_verrou_id",
                "Un verrou confirmé ne possède pas d'identifiant.",
                {"index": index},
            )
        if not title:
            raise ContractError(
                "missing_verrou_title",
                f"Le verrou confirmé {verrou_id!r} ne possède pas de titre.",
                {"index": index, "verrou_id": verrou_id},
            )
        canonical_id = alias_map.get(verrou_id, verrou_id)
        item = dict(raw)
        item["verrou_id"] = canonical_id
        item["verrou_title"] = title
        item["original_verrou_id"] = verrou_id
        item["canonicalized_from_alias"] = verrou_id != canonical_id

        previous = by_id.get(canonical_id)
        if previous is not None:
            previous_from_alias = bool(previous.get("canonicalized_from_alias"))
            if previous_from_alias and not item["canonicalized_from_alias"]:
                canonical_item = item
                for key, value in previous.items():
                    if isinstance(value, list):
                        target = canonical_item.setdefault(key, [])
                        if isinstance(target, list):
                            for child in value:
                                if child not in target:
                                    target.append(child)
                normalized[normalized.index(previous)] = canonical_item
                by_id[canonical_id] = canonical_item
                previous = canonical_item
            elif previous["verrou_title"] != title and not item["canonicalized_from_alias"]:
                raise ContractError(
                    "duplicate_verrou_id_with_different_title",
                    f"L'identifiant {canonical_id!r} possède plusieurs titres.",
                    {
                        "verrou_id": canonical_id,
                        "first_title": previous["verrou_title"],
                        "second_title": title,
                    },
                )
            previous.setdefault("merged_aliases", []).append(verrou_id)
            for key, value in item.items():
                if isinstance(value, list):
                    target = previous.setdefault(key, [])
                    if isinstance(target, list):
                        for child in value:
                            if child not in target:
                                target.append(child)
            continue
        by_id[canonical_id] = item
        normalized.append(item)

    if not normalized:
        raise ContractError(
            "no_confirmed_verrous",
            "Aucun verrou confirmé n'a été fourni à EnnoScholar.",
        )

    target_ids = set(alias_map.values())
    missing_targets = sorted(target_ids - set(by_id))
    if missing_targets:
        raise ContractError(
            "invalid_alias_target",
            "Un alias pointe vers un verrou canonique absent.",
            {"missing_targets": missing_targets},
        )
    return normalized


def verrou_fingerprint(verrous: Sequence[Mapping[str, Any]]) -> str:
    compact = [
        {
            "verrou_id": clean_text(item.get("verrou_id")),
            "verrou_title": clean_text(item.get("verrou_title")),
        }
        for item in verrous
    ]
    serialized = json.dumps(compact, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_confirmed_contract(
    payload: Mapping[str, Any],
    *,
    aliases: Any = None,
    source_path: str = "",
) -> Dict[str, Any]:
    items = extract_verrou_items(payload)
    verrous = normalize_confirmed_verrous(items, aliases=aliases)
    return {
        "ok": True,
        "payload_type": "ennoscholar_confirmed_verrous_contract_v1",
        "source_path": source_path,
        "verrous": verrous,
        "verrous_count": len(verrous),
        "verrou_fingerprint": verrou_fingerprint(verrous),
        "rules": {
            "ids_and_titles_are_immutable": True,
            "automatic_verrou_creation_forbidden": True,
            "nlp_reconstruction_forbidden": True,
        },
    }


def load_confirmed_contract(path: str | Path, *, aliases: Any = None) -> Dict[str, Any]:
    return build_confirmed_contract(
        _read_json(path),
        aliases=aliases,
        source_path=str(Path(path)),
    )


def assert_same_verrous(
    expected: Sequence[Mapping[str, Any]],
    observed: Sequence[Mapping[str, Any]],
    *,
    observed_name: str,
) -> None:
    expected_pairs = [
        (clean_text(item.get("verrou_id")), clean_text(item.get("verrou_title")))
        for item in expected
    ]
    observed_pairs = [
        _item_identity(item)
        for item in observed
    ]
    if expected_pairs != observed_pairs:
        raise ContractError(
            "verrou_contract_mismatch",
            f"Les verrous de {observed_name} ne correspondent pas exactement aux verrous confirmés.",
            {
                "expected": expected_pairs,
                "observed": observed_pairs,
                "expected_fingerprint": verrou_fingerprint(
                    [{"verrou_id": i, "verrou_title": t} for i, t in expected_pairs]
                ),
                "observed_fingerprint": verrou_fingerprint(
                    [{"verrou_id": i, "verrou_title": t} for i, t in observed_pairs]
                ),
            },
        )


def _normalize_plan_section(item: Mapping[str, Any], index: int) -> Dict[str, Any]:
    title = clean_text(
        item.get("title")
        or item.get("section_title")
        or item.get("heading")
        or item.get("label")
        or item.get("name")
    )
    if not title:
        raise ContractError(
            "missing_plan_section_title",
            f"La section {index} du plan n'a pas de titre.",
            {"index": index},
        )
    objective = clean_text(
        item.get("objective")
        or item.get("objectif")
        or item.get("goal")
        or item.get("description")
        or item.get("instructions")
    )
    section_id = clean_text(item.get("section_id") or item.get("id") or f"section_{index}")
    verrou_ids = item.get("verrou_ids") or item.get("linked_verrou_ids") or []
    if isinstance(verrou_ids, str):
        verrou_ids = re.split(r"[,;\s]+", verrou_ids)
    def _string_list(*names: str) -> List[str]:
        raw: Any = []
        for name in names:
            if item.get(name) is not None:
                raw = item.get(name)
                break
        if isinstance(raw, str):
            raw = re.split(r"[\n;]+", raw)
        if not isinstance(raw, (list, tuple, set)):
            raw = [raw] if raw else []
        return [
            clean_text(value)
            for value in raw
            if clean_text(value)
        ]
    try:
        level = max(1, min(6, int(item.get("level") or 1)))
    except Exception:
        level = 1
    try:
        target_words = int(item.get("target_words")) if item.get("target_words") else None
    except Exception:
        target_words = None
    return {
        "section_id": section_id,
        "order": index,
        "title": title,
        "objective": objective,
        "verrou_ids": [clean_text(value) for value in verrou_ids if clean_text(value)],
        "parent_id": clean_text(item.get("parent_id")) or None,
        "level": level,
        "target_words": max(100, min(20000, target_words)) if target_words else None,
        "instructions": _string_list("instructions", "writing_instructions"),
        "required_dimensions": _string_list(
            "required_dimensions", "dimensions", "must_cover"
        ),
        "visual_requirements": _string_list(
            "visual_requirements", "visuals", "figures"
        ),
        "source_preferences": _string_list(
            "source_preferences", "source_types"
        ),
    }


def normalize_plan_sections(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, Mapping):
        for key in ("sections", "items", "plan"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if not isinstance(value, list):
        return []
    return [
        _normalize_plan_section(item, index)
        for index, item in enumerate(value, 1)
        if isinstance(item, Mapping)
    ]


def plan_hash(sections: Sequence[Mapping[str, Any]]) -> str:
    data = [
        {
            "order": index,
            "title": clean_text(section.get("title")),
            "objective": clean_text(section.get("objective")),
            "verrou_ids": list(section.get("verrou_ids") or []),
            "parent_id": clean_text(section.get("parent_id")) or None,
            "level": int(section.get("level") or 1),
            "target_words": section.get("target_words"),
            "instructions": list(section.get("instructions") or []),
            "required_dimensions": list(section.get("required_dimensions") or []),
            "visual_requirements": list(section.get("visual_requirements") or []),
            "source_preferences": list(section.get("source_preferences") or []),
        }
        for index, section in enumerate(sections, 1)
    ]
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_plan_contract(
    *,
    proposed_plan: Any,
    consultant_edited_plan: Any = None,
    approve: bool = False,
    approved_by: str = "",
    writing_authorized: bool = False,
    plan_version: int = 1,
) -> Dict[str, Any]:
    proposed = normalize_plan_sections(proposed_plan)
    edited = normalize_plan_sections(consultant_edited_plan) if consultant_edited_plan is not None else []
    effective = edited or proposed
    if not effective:
        raise ContractError("empty_plan", "Le plan consultant ne contient aucune section.")
    approved_plan = effective if approve else []
    return {
        "ok": True,
        "payload_type": "ennoscholar_consultant_plan_contract_v1",
        "plan_version": int(plan_version),
        "proposed_plan": proposed,
        "consultant_edited_plan": edited,
        "approved_plan": approved_plan,
        "approval_hash": plan_hash(approved_plan) if approved_plan else "",
        "approved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat() if approve else "",
        "approved_by": clean_text(approved_by),
        "writing_authorized": bool(approve and writing_authorized),
    }


def resolve_approved_plan(
    payload: Mapping[str, Any],
    *,
    require_writing_authorization: bool = True,
) -> List[Dict[str, Any]]:
    approved = normalize_plan_sections(payload.get("approved_plan"))
    if not approved:
        raise ContractError(
            "consultant_plan_not_approved",
            "La Phase 5 attend un plan consultant approuvé.",
        )
    declared_hash = clean_text(payload.get("approval_hash"))
    actual_hash = plan_hash(approved)
    if declared_hash != actual_hash:
        raise ContractError(
            "consultant_plan_hash_mismatch",
            "Le plan a été modifié après sa validation. Une nouvelle validation est requise.",
            {"declared_hash": declared_hash, "actual_hash": actual_hash},
        )
    if require_writing_authorization and not bool(payload.get("writing_authorized")):
        raise ContractError(
            "writing_not_authorized",
            "L'ordre explicite de rédaction n'a pas été enregistré.",
        )
    return approved
