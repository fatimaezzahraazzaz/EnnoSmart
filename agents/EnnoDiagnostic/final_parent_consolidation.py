# -*- coding: utf-8 -*-
"""
Unique final parent consolidation for EnnoDiagnostic.

This runs AFTER the agent has already produced its current locks.
Frascati is never recomputed here.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Sequence
import hashlib
import re
import unicodedata

from modules.NLP.llm_parent_lock_consolidator import consolidate_parent_locks

VERSION = "ennodiag_single_final_parent_consolidation_v1_2_20260916"
LOG = "[EnnoDiagnostic][FINAL_PARENT_ONLY]"


def _clean(value: Any, limit: int = 4000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip()
    return text


def _stable_id(item: Mapping[str, Any], index: int) -> str:
    for key in ("group_id", "cluster_id", "continuity_current_id", "lock_group_id", "passage_id"):
        value = _clean(item.get(key), 240)
        if value:
            return value
    basis = "|".join([
        _clean(item.get("title") or item.get("titre") or item.get("verrou"), 500),
        str(index),
    ])
    digest = hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"final_lock_{index:03d}_{digest}"


def _title(item: Mapping[str, Any]) -> str:
    return _clean(
        item.get("title")
        or item.get("titre")
        or item.get("verrou")
        or item.get("text"),
        900,
    )


def _analysis(item: Mapping[str, Any]) -> str:
    return _clean(
        item.get("scientific_lock")
        or item.get("consultant_explanation")
        or item.get("why_agent_found_verrou")
        or item.get("why_not_simple_engineering")
        or item.get("justification")
        or item.get("description")
        or item.get("evidence_summary")
        or item.get("text"),
        2600,
    )


def _source_document(src: Mapping[str, Any]) -> str:
    return _clean(
        src.get("document")
        or src.get("source_document")
        or src.get("filename")
        or src.get("file"),
        1000,
    )


def _adapt_for_consolidator(item: Mapping[str, Any], index: int) -> Dict[str, Any]:
    out = deepcopy(dict(item))
    gid = _stable_id(item, index)
    out["lock_group_id"] = gid
    out["text"] = _title(item)
    out["analysis_text"] = _analysis(item)

    if not out.get("supporting_passages"):
        passages = []
        for src in item.get("sources") or []:
            if not isinstance(src, Mapping):
                continue
            passages.append({
                "passage_id": _clean(src.get("evidence_id") or src.get("passage_id"), 300),
                "document": _source_document(src),
                "text": _clean(src.get("excerpt") or src.get("text"), 1800),
            })
        out["supporting_passages"] = passages

    if not out.get("supporting_documents"):
        docs: Dict[str, int] = {}
        for src in item.get("sources") or []:
            if not isinstance(src, Mapping):
                continue
            name = _source_document(src)
            if name:
                docs[name] = docs.get(name, 0) + 1
        source_doc = _clean(item.get("source_document"), 1000)
        if source_doc:
            docs[source_doc] = docs.get(source_doc, 0) + 1
        out["supporting_documents"] = [
            {"document": name, "passage_count": count}
            for name, count in docs.items()
        ]

    return out


def _dedupe_sources(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        for src in row.get("sources") or []:
            if not isinstance(src, Mapping):
                continue
            key = (
                _clean(src.get("evidence_id") or src.get("passage_id"), 300),
                _source_document(src),
                _clean(src.get("excerpt") or src.get("text"), 500),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(deepcopy(dict(src)))
    return out


def _history_match_blob(item: Mapping[str, Any]) -> str:
    """Texte sémantique dédié au rattachement historique.

    Pour un parent consolidé, ``historical_match_text`` contient les titres,
    incertitudes et preuves de TOUS ses membres. Il n'est jamais utilisé pour
    le score Frascati ni pour créer/supprimer un verrou courant.
    """
    parts = [
        _title(item),
        _analysis(item),
        _clean(item.get("historical_match_text"), 14000),
        _clean(item.get("parent_consolidation_reason"), 2500),
    ]
    for src in (item.get("sources") or [])[:16]:
        if isinstance(src, Mapping):
            parts.append(_clean(src.get("excerpt") or src.get("text"), 1200))
    return " ".join(part for part in parts if part)


def _norm_tokens(value: Any) -> set[str]:
    original = _clean(value, 5000)
    # Acronymes techniques courts (SAR, ATR, IoU, 3D...) : ils sont souvent
    # très discriminants et ne doivent pas disparaître à cause de la longueur.
    acronyms = {
        token.lower()
        for token in re.findall(r"\b(?:[A-Z0-9]{2,6}|[A-Z][a-z]?[A-Z][A-Za-z0-9]*)\b", original)
        if len(token) >= 2
    }
    raw = unicodedata.normalize("NFKD", original.lower())
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    words = re.findall(r"[a-z0-9][a-z0-9_+-]{2,}", raw)
    stop = {
        "avec","dans","pour","sans","sous","entre","ainsi","cette","comme","plus","moins",
        "des","les","une","sur","est","sont","the","and","with","from","that","this",
        "verrou","verrous","incertitude","incertitudes","technique","techniques",
        "scientifique","scientifiques","projet","travaux","analyse","resultat","resultats",
        "methode","methodes","systeme","systemes","validation",
    }
    normal = {w for w in words if len(w) >= 4 and w not in stop}
    return normal | {token for token in acronyms if token not in stop}


def _attach_history_conservatively(
    parents: List[Dict[str, Any]],
    history_cards: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    attached = []
    unmatched = []

    parent_tokens = []
    for parent in parents:
        parent_tokens.append(_norm_tokens(_history_match_blob(parent)))

    common = set.intersection(*parent_tokens) if len(parent_tokens) >= 2 else set()

    for hist in history_cards:
        ht = _norm_tokens(_history_match_blob(hist))
        ht -= common

        ranked = []
        for idx, tokens in enumerate(parent_tokens):
            pt = tokens - common
            shared = ht & pt
            containment = len(shared) / max(1, min(len(ht), len(pt))) if ht and pt else 0.0
            ranked.append((len(shared), containment, idx, sorted(shared)[:12]))

        ranked.sort(reverse=True)
        best = ranked[0] if ranked else (0, 0.0, -1, [])
        second = ranked[1] if len(ranked) > 1 else (0, 0.0, -1, [])

        strong = (
            best[2] >= 0
            and best[0] >= 2
            and best[1] >= 0.12
            and (best[0] > second[0] or best[1] >= second[1] + 0.08)
        )

        if strong:
            target = parents[best[2]]
            target.setdefault("historical_supports", []).append(deepcopy(dict(hist)))
            source_json = (
                deepcopy(target.get("source_json"))
                if isinstance(target.get("source_json"), Mapping)
                else {}
            )
            source_json.setdefault("historical_supports", []).append({
                "title": _title(hist),
                "continuity_percentage": hist.get("continuity_percentage"),
                "historical_continuity": hist.get("historical_continuity"),
            })
            target["source_json"] = source_json
            print(
                "[EnnoDiagnostic][HISTORY_PARENT_MATCH] "
                f"history={_title(hist)[:120]} -> parent={_title(target)[:160]} "
                f"shared={best[0]} containment={best[1]:.3f}",
                flush=True,
            )
            attached.append({
                "history_title": _title(hist),
                "parent_title": _title(target),
                "parent_member_ids": list(
                    target.get("conceptual_member_group_ids") or []
                ),
                "shared_terms": best[3],
                "shared_count": best[0],
                "containment": round(best[1], 4),
                "matching_basis": "full_parent_members_and_sources_v1_2",
            })
        else:
            unmatched.append(deepcopy(dict(hist)))

    return {"attached": attached, "unmatched": unmatched}


def consolidate_final_parent_view(
    atomic_verrous: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    atomic = [deepcopy(dict(x)) for x in atomic_verrous if isinstance(x, Mapping)]

    history_cards = [x for x in atomic if bool(x.get("historical_memory_card"))]
    current = [x for x in atomic if not bool(x.get("historical_memory_card"))]

    adapted = [
        _adapt_for_consolidator(item, i)
        for i, item in enumerate(current, start=1)
    ]

    result = consolidate_parent_locks(adapted)
    valid = bool(result.get("valid"))
    consolidated = (
        [deepcopy(dict(x)) for x in (result.get("groups") or []) if isinstance(x, Mapping)]
        if valid else adapted
    )

    by_id = {
        _stable_id(item, i): item
        for i, item in enumerate(current, start=1)
    }

    parents: List[Dict[str, Any]] = []
    for merged in consolidated:
        source_ids = [
            _clean(x, 240)
            for x in (merged.get("source_group_ids") or [merged.get("lock_group_id")])
            if _clean(x, 240)
        ]
        members = [by_id[x] for x in source_ids if x in by_id]

        rep_id = _clean(merged.get("lock_group_id"), 240)
        representative = deepcopy(by_id.get(rep_id) or (members[0] if members else merged))

        representative["lock_group_id"] = rep_id or _stable_id(representative, len(parents) + 1)
        representative["group_id"] = (
            _clean(representative.get("group_id"), 240)
            or representative["lock_group_id"]
        )
        representative["title"] = _title(representative) or _clean(merged.get("text"), 900)
        representative["titre"] = representative["title"]
        representative["verrou"] = representative["title"]

        representative["conceptual_member_group_ids"] = list(
            merged.get("conceptual_member_group_ids") or [representative["lock_group_id"]]
        )
        representative["attached_support_group_ids"] = list(
            merged.get("attached_support_group_ids") or []
        )
        representative["source_group_ids"] = source_ids
        representative["parent_consolidation_reason"] = merged.get("parent_consolidation_reason")
        representative["parent_consolidation_version"] = merged.get("parent_consolidation_version")
        representative["final_parent_consolidation_version"] = VERSION
        representative["sources"] = _dedupe_sources(members)
        representative["source_ids"] = list(dict.fromkeys(
            _clean(s.get("evidence_id") or s.get("passage_id"), 300)
            for s in representative["sources"]
            if isinstance(s, Mapping)
            and _clean(s.get("evidence_id") or s.get("passage_id"), 300)
        ))

        representative["parent_component_scores"] = [
            member.get("score")
            for member in members
            if member.get("score") is not None
        ]

        # V1.2: matching historique sur le contenu COMPLET du parent.
        # On agrège tous les membres fusionnés pour que, par exemple, un parent
        # représenté par G3 conserve aussi les signaux sémantiques de G4/G5.
        _history_member_parts = []
        for _member in members:
            _history_member_parts.extend([
                _title(_member),
                _analysis(_member),
                _clean(_member.get("evidence_summary"), 1800),
            ])
            for _src in (_member.get("sources") or [])[:8]:
                if isinstance(_src, Mapping):
                    _history_member_parts.append(
                        _clean(_src.get("excerpt") or _src.get("text"), 1000)
                    )
        representative["historical_match_text"] = _clean(
            " ".join(part for part in _history_member_parts if part),
            14000,
        )

        representative["frascati_recomputed_after_parent_consolidation"] = False
        representative["needs_human_validation"] = True

        source_json = (
            deepcopy(representative.get("source_json"))
            if isinstance(representative.get("source_json"), Mapping)
            else {}
        )
        source_json["final_parent_consolidation"] = {
            "version": VERSION,
            "member_group_ids": representative["conceptual_member_group_ids"],
            "support_group_ids": representative["attached_support_group_ids"],
            "reason": representative.get("parent_consolidation_reason"),
            "frascati_recomputed": False,
        }
        representative["source_json"] = source_json
        parents.append(representative)

    history_report = _attach_history_conservatively(parents, history_cards)

    audit = {
        "version": VERSION,
        "atomic_count": len(atomic),
        "current_input_count": len(current),
        "history_cards_count": len(history_cards),
        "output_parent_count": len(parents),
        "valid": valid,
        "llm_parent_audit": result.get("audit") or {},
        "history_attachment": history_report,
        "legacy_scientific_axis_used": False,
        "frascati_recomputed": False,
    }

    print(
        f"{LOG} atomic={len(atomic)} current={len(current)} "
        f"output={len(parents)} history={len(history_cards)} valid={valid}",
        flush=True,
    )

    return {
        "valid": valid,
        "version": VERSION,
        "display_verrous": parents,
        "parent_verrous": parents,
        "current_atomic_verrous": current,
        "history_cards": history_cards,
        "audit": audit,
    }
