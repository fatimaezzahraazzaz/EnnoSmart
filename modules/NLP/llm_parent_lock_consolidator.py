# -*- coding: utf-8 -*-
"""
Post-Frascati parent-lock consolidation.

This module only receives already detected / filtered / Frascati-assessed
technical locks. It never receives the raw passage catalogue and it never
modifies the Frascati project assessment.

Fail-closed behaviour:
- API error -> original groups
- invalid JSON -> original groups
- unknown IDs -> original groups
- missing/duplicate coverage -> original groups
- no fixed target number of locks
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Sequence
import json
import os
import re


VERSION = "post_frascati_parent_lock_consolidation_v1_20260916"
LOG_PREFIX = "[EnnoDiagnostic][PARENT_LOCK_CONSOLIDATION]"


def _group_id(group: Mapping[str, Any]) -> str:
    return str(group.get("lock_group_id") or group.get("passage_id") or "").strip()


def _safe_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0]


def _compact_group(group: Mapping[str, Any]) -> Dict[str, Any]:
    documents = []
    for item in group.get("supporting_documents") or []:
        if isinstance(item, Mapping):
            name = str(item.get("document") or "").strip()
            if name:
                documents.append(name)
        elif item:
            documents.append(str(item))

    assessment = group.get("frascati_assessment") or {}
    return {
        "id": _group_id(group),
        "title": _safe_text(group.get("text"), 700),
        "analysis": _safe_text(group.get("analysis_text"), 1800),
        "documents": documents[:12],
        "semantic_roles": list(group.get("source_semantic_roles") or [])[:12],
        "evidence_count": int(group.get("evidence_count") or 0),
        "frascati_recommendation": int(
            group.get("frascati_recommendation")
            or group.get("frascati_decision")
            or assessment.get("eligibility_recommendation")
            or 0
        ),
        "risk_level": group.get("frascati_risk_level") or assessment.get("risk_level"),
    }


SYSTEM_PROMPT = """Tu es un consolidateur de verrous scientifiques/techniques.

Tu reçois UNIQUEMENT des verrous déjà détectés, filtrés et évalués par le
pipeline EnnoDiagnostic. Tu n'as pas le droit de refaire l'éligibilité CIR,
de modifier Frascati, ni d'inventer un nouveau verrou absent des entrées.

Ton seul rôle est de construire des VERROUS PARENTS conceptuels.

Décisions autorisées :
- SAME_PARENT_LOCK : A et B expriment la même incertitude sous-jacente.
  Résoudre complètement l'incertitude centrale de A résoudrait
  substantiellement celle de B.
- SUPPORT_OF : un élément est principalement une manifestation plus étroite,
  une méthode, un résultat, une condition expérimentale, une conséquence ou
  une preuve d'un verrou parent. Il doit rester traçable mais ne doit pas être
  affiché comme verrou principal indépendant.
- DISTINCT_LOCK : les deux éléments nécessitent des réponses scientifiques ou
  techniques différentes. Ils restent des parents distincts.

Règles strictes :
1. Même domaine, même modèle, même dataset ou vocabulaire proche ne suffit
   jamais pour fusionner.
2. Aucun nombre cible de verrous. Ne cherche jamais à produire 3, 4, etc.
3. Chaque ID d'entrée doit apparaître EXACTEMENT UNE FOIS dans toute la sortie,
   soit dans member_ids, soit dans support_ids.
4. representative_id doit appartenir à member_ids.
5. N'invente aucun ID.
6. Un groupe qui reste seul est parfaitement acceptable.
7. Préfère DISTINCT_LOCK lorsqu'une fusion conceptuelle n'est pas démontrée.
8. Ne reformule pas des faits et ne crée pas de contenu scientifique nouveau.

Retourne uniquement un objet JSON :
{
  "parents": [
    {
      "representative_id": "ID",
      "member_ids": ["ID", "..."],
      "support_ids": ["ID", "..."],
      "reason": "raison courte"
    }
  ]
}
"""


def _openai_json(payload: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError(f"openai_import_failed: {exc}") from exc

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing")

    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    model = (
        os.getenv("ENNOSMART_OPENAI_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or "gpt-4.1-mini"
    )
    kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)

    user_prompt = (
        "GROUPES A CONSOLIDER:\n"
        + json.dumps(list(payload), ensure_ascii=False, separators=(",", ":"))
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("root_json_not_object")
    return data


def _validate_plan(plan: Mapping[str, Any], input_ids: Sequence[str]) -> List[Dict[str, Any]]:
    parents = plan.get("parents")
    if not isinstance(parents, list) or not parents:
        raise ValueError("parents_missing_or_empty")

    allowed = set(input_ids)
    seen: List[str] = []
    normalized: List[Dict[str, Any]] = []

    for raw in parents:
        if not isinstance(raw, Mapping):
            raise ValueError("parent_not_object")
        rep = str(raw.get("representative_id") or "").strip()
        members = [str(x).strip() for x in (raw.get("member_ids") or []) if str(x).strip()]
        supports = [str(x).strip() for x in (raw.get("support_ids") or []) if str(x).strip()]
        reason = _safe_text(raw.get("reason"), 500)

        if not rep or rep not in members:
            raise ValueError("representative_not_member")
        local = members + supports
        if len(local) != len(set(local)):
            raise ValueError("duplicate_inside_parent")
        unknown = set(local) - allowed
        if unknown:
            raise ValueError(f"unknown_ids={sorted(unknown)}")

        seen.extend(local)
        normalized.append({
            "representative_id": rep,
            "member_ids": members,
            "support_ids": supports,
            "reason": reason,
        })

    if len(seen) != len(set(seen)):
        raise ValueError("id_used_in_multiple_parents")
    if set(seen) != allowed:
        raise ValueError(f"incomplete_coverage_missing={sorted(allowed - set(seen))}")
    return normalized


def _passage_key(item: Mapping[str, Any]) -> str:
    passage_id = str(item.get("passage_id") or "").strip()
    if passage_id:
        return passage_id
    return "|".join([
        str(item.get("document") or ""),
        str(item.get("sentence_start") or ""),
        _safe_text(item.get("text"), 300),
    ])


def _merge_parent(
    spec: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
    order: int,
) -> Dict[str, Any]:
    rep_id = str(spec["representative_id"])
    representative = deepcopy(dict(by_id[rep_id]))

    member_ids = list(spec["member_ids"])
    support_ids = list(spec["support_ids"])
    source_ids = member_ids + support_ids
    source_groups = [by_id[x] for x in source_ids]

    passage_map: Dict[str, Dict[str, Any]] = {}
    document_counts: Dict[str, int] = {}

    for group in source_groups:
        for passage in group.get("supporting_passages") or []:
            if isinstance(passage, Mapping):
                passage_map[_passage_key(passage)] = deepcopy(dict(passage))

        for doc in group.get("supporting_documents") or []:
            if isinstance(doc, Mapping):
                name = str(doc.get("document") or "").strip()
                count = int(doc.get("passage_count") or 1)
            else:
                name = str(doc or "").strip()
                count = 1
            if name:
                document_counts[name] = document_counts.get(name, 0) + count

    representative["source_group_ids"] = source_ids
    representative["conceptual_member_group_ids"] = member_ids
    representative["attached_support_group_ids"] = support_ids
    representative["conceptual_group_count"] = len(member_ids)
    representative["parent_consolidation_reason"] = spec.get("reason") or ""
    representative["parent_consolidation_version"] = VERSION
    representative["derived_view"] = VERSION
    representative["display_as_main_lock"] = True
    representative["needs_human_validation"] = True
    representative["supporting_passages"] = list(passage_map.values())
    representative["evidence_count"] = len(passage_map)
    representative["supporting_documents"] = [
        {"document": name, "passage_count": count}
        for name, count in sorted(document_counts.items())
    ]
    representative["pre_parent_consolidation_group_count"] = len(source_groups)
    representative["parent_display_order"] = order
    return representative


def consolidate_parent_locks(groups: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    original = [deepcopy(dict(g)) for g in groups if isinstance(g, Mapping)]
    input_ids = [_group_id(g) for g in original]

    if len(original) <= 1:
        return {
            "valid": True,
            "version": VERSION,
            "groups": original,
            "audit": {
                "version": VERSION,
                "input_count": len(original),
                "output_count": len(original),
                "fallback": False,
                "reason": "nothing_to_consolidate",
            },
        }

    if not all(input_ids) or len(input_ids) != len(set(input_ids)):
        return {
            "valid": False,
            "version": VERSION,
            "groups": original,
            "audit": {
                "version": VERSION,
                "input_count": len(original),
                "output_count": len(original),
                "fallback": True,
                "reason": "missing_or_duplicate_input_ids",
            },
        }

    payload = [_compact_group(g) for g in original]
    try:
        specs = _validate_plan(_openai_json(payload), input_ids)
        by_id = {_group_id(g): g for g in original}
        merged = [_merge_parent(spec, by_id, i + 1) for i, spec in enumerate(specs)]

        print(f"{LOG_PREFIX} input={len(original)} output={len(merged)}")
        for spec, group in zip(specs, merged):
            print(
                f"{LOG_PREFIX} parent={_group_id(group)} "
                f"members={spec['member_ids']} supports={spec['support_ids']}"
            )

        return {
            "valid": True,
            "version": VERSION,
            "groups": merged,
            "audit": {
                "version": VERSION,
                "input_count": len(original),
                "output_count": len(merged),
                "fallback": False,
                "plan": specs,
            },
        }

    except Exception as exc:
        print(f"{LOG_PREFIX} fallback=true error={type(exc).__name__}: {exc}")
        return {
            "valid": False,
            "version": VERSION,
            "groups": original,
            "audit": {
                "version": VERSION,
                "input_count": len(original),
                "output_count": len(original),
                "fallback": True,
                "reason": f"{type(exc).__name__}: {exc}",
            },
        }
