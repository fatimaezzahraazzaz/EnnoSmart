from __future__ import annotations

import re
import unicodedata
from typing import Any

POLICY_VERSION = "ennoamel_conversation_task_memory_v3_17"
_MEANINGFUL = {
    "clarity", "style", "structure", "concision", "argumentation",
    "cir_eligibility", "scientific_enrichment", "research",
}
_GENERIC = {"general_revision", "candidate_revision", "small_talk"}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


def _intent(value: Any) -> str:
    raw = getattr(value, "value", value)
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("name") or ""
    return _norm(raw).replace(" ", "_")


def _intents(routing: Any) -> list[str]:
    rows = routing.get("intents", []) if isinstance(routing, dict) else getattr(routing, "intents", [])
    out = []
    for row in rows or []:
        value = _intent(row)
        if value and value not in out:
            out.append(value)
    return out


def _flag(routing: Any, key: str) -> bool:
    return bool(routing.get(key)) if isinstance(routing, dict) else bool(getattr(routing, key, False))


def _target_key(section_id: str | None, section_title: str | None, scope: str | None) -> str:
    return f"{_norm(scope)}|{_norm(section_id)}|{_norm(section_title)}"


def is_resume_message(message: str | None) -> bool:
    text = _norm(message)
    if not text or len(text) > 180:
        return False
    patterns = (
        r"^(?:ok|d'accord|dac|c'est bon|cest bon|tres bien|parfait)?\s*"
        r"(?:maintenant\s+)?(?:redige|rediger|reecris|reecrire|fais|fait|genere|propose)"
        r"(?:\s+(?:la|le|cette|ce|une|un)\s+(?:section|version|proposition|texte))?\s*[.!?]*$",
        r"^(?:ok|d'accord|dac|c'est bon|cest bon|tres bien|parfait|vas-y|vas y|go)\s*[.!?]*$",
    )
    return any(re.match(p, text, re.I) for p in patterns)


def _substantive(routing: Any) -> bool:
    return bool(set(_intents(routing)).intersection(_MEANINGFUL))


def _same_target(contract: dict[str, Any] | None, section_id: str | None, section_title: str | None, scope: str | None) -> bool:
    if not isinstance(contract, dict):
        return False
    if str(contract.get("target_key") or "") == _target_key(section_id, section_title, scope):
        return True
    sid = _norm(section_id)
    old_sid = _norm(contract.get("target_section_id"))
    if sid and old_sid and sid == old_sid:
        return True
    title = _norm(section_title)
    old_title = _norm(contract.get("target_section_title"))
    return bool(title and old_title and title == old_title)


def _make_contract(instruction: str, routing: Any, section_id: str | None, section_title: str | None, scope: str | None, origin: str) -> dict[str, Any]:
    intents = _intents(routing)
    return {
        "policy_version": POLICY_VERSION,
        "origin": origin,
        "instruction": str(instruction or "").strip(),
        "intents": intents,
        "target_scope": str(scope or ""),
        "target_section_id": section_id,
        "target_section_title": section_title,
        "target_key": _target_key(section_id, section_title, scope),
        "style_requested": "style" in intents,
        "clarity_requested": "clarity" in intents,
        "structure_requested": "structure" in intents,
        "argumentation_requested": "argumentation" in intents,
        "scientific_enrichment_requested": ("scientific_enrichment" in intents or "research" in intents),
        "research_requested": "research" in intents,
        "needs_scholar": _flag(routing, "needs_scholar"),
        "needs_new_research": _flag(routing, "needs_new_research"),
    }


def _merge(contract: dict[str, Any], message: str, routing: Any) -> dict[str, Any]:
    out = dict(contract)
    instruction = str(out.get("instruction") or "").strip()
    message = str(message or "").strip()
    if message and _norm(message) not in _norm(instruction):
        instruction = (instruction + "\n\nDEMANDE COMPLÉMENTAIRE DU CONSULTANT\n" + message).strip()
    intents = list(out.get("intents") or [])
    for item in _intents(routing):
        if item not in intents and item not in _GENERIC:
            intents.append(item)
    out.update({
        "instruction": instruction,
        "intents": intents,
        "style_requested": bool(out.get("style_requested") or "style" in intents),
        "clarity_requested": bool(out.get("clarity_requested") or "clarity" in intents),
        "structure_requested": bool(out.get("structure_requested") or "structure" in intents),
        "argumentation_requested": bool(out.get("argumentation_requested") or "argumentation" in intents),
        "scientific_enrichment_requested": bool(out.get("scientific_enrichment_requested") or "scientific_enrichment" in intents or "research" in intents),
        "research_requested": bool(out.get("research_requested") or "research" in intents),
        "needs_scholar": bool(out.get("needs_scholar") or _flag(routing, "needs_scholar")),
        "needs_new_research": bool(out.get("needs_new_research") or _flag(routing, "needs_new_research")),
        "origin": "conversation_merged",
    })
    return out


def recover_contract_from_history(history: list[dict[str, Any]] | None, section_id: str | None, section_title: str | None, scope: str | None) -> dict[str, Any] | None:
    wanted_sid = _norm(section_id)
    wanted_title = _norm(section_title)
    collected = []
    for row in reversed(list(history or [])):
        if not isinstance(row, dict):
            continue
        row_sid = _norm(row.get("target_section_id"))
        row_title = _norm(row.get("target_section_title"))
        if wanted_sid and row_sid and row_sid != wanted_sid:
            if collected:
                break
            continue
        if not wanted_sid and wanted_title and row_title and row_title != wanted_title:
            if collected:
                break
            continue
        if not _substantive(row.get("routing") or {}):
            continue
        collected.append(row)
    if not collected:
        return None
    collected.reverse()
    first = collected[0]
    contract = _make_contract(
        str(first.get("content") or ""),
        first.get("routing") or {},
        section_id or first.get("target_section_id"),
        section_title or first.get("target_section_title"),
        scope or first.get("target_scope"),
        "recovered_from_conversation_history",
    )
    for row in collected[1:]:
        contract = _merge(contract, str(row.get("content") or ""), row.get("routing") or {})
    return contract


def evolve_task_memory(*, existing_memory: dict[str, Any] | None, raw_message: str, routing: Any, section_id: str | None, section_title: str | None, scope: str | None, has_accepted_sources: bool, analyzed_history: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], str]:
    memory = dict(existing_memory or {})
    contract = dict(memory.get("active_contract") or {}) if isinstance(memory.get("active_contract"), dict) else None

    if not _same_target(contract, section_id, section_title, scope):
        contract = recover_contract_from_history(analyzed_history, section_id, section_title, scope)

    resume = bool(
        has_accepted_sources
        and contract
        and _same_target(contract, section_id, section_title, scope)
        and is_resume_message(raw_message)
    )

    if resume:
        actions = []
        if contract.get("style_requested"):
            actions.append("améliorer réellement le style rédactionnel")
        if contract.get("clarity_requested"):
            actions.append("améliorer la clarté")
        if contract.get("structure_requested"):
            actions.append("améliorer la structure et les transitions")
        if contract.get("argumentation_requested"):
            actions.append("renforcer l'argumentation et les justifications")
        if contract.get("scientific_enrichment_requested"):
            actions.append("renforcer le fond scientifique avec le corpus déjà validé")
        if not actions:
            actions.append("exécuter la demande d'amélioration mémorisée")
        target = (
            str(contract.get("target_section_id") or "").strip()
            or str(contract.get("target_section_title") or "").strip()
            or "la cible active"
        )
        effective = (
            "CONTRAT MÉMORISÉ DE LA DEMANDE DU CONSULTANT\n"
            f"Cible : {target}\n"
            "Actions à appliquer maintenant :\n- "
            + "\n- ".join(actions)
            + "\n\nLes publications ont déjà été sélectionnées et validées. "
              "Utilise TOUTES les preuves acceptées sans exception. "
              "N'ouvre pas un nouveau cycle EnnoScholar et n'effectue aucune nouvelle collecte de publications.\n\n"
              "Le dernier message court ne remplace aucune des actions mémorisées.\n"
            + "Message actuel : " + str(raw_message or "").strip()
        ).strip()
    elif contract and _same_target(contract, section_id, section_title, scope) and _substantive(routing) and has_accepted_sources:
        contract = _merge(contract, raw_message, routing)
        actions = []
        if contract.get("style_requested"):
            actions.append("améliorer réellement le style rédactionnel")
        if contract.get("clarity_requested"):
            actions.append("améliorer la clarté")
        if contract.get("structure_requested"):
            actions.append("améliorer la structure et les transitions")
        if contract.get("argumentation_requested"):
            actions.append("renforcer l'argumentation et les justifications")
        if contract.get("scientific_enrichment_requested"):
            actions.append("renforcer le fond scientifique avec le corpus déjà validé")
        effective = (
            "CONTRAT MÉMORISÉ DE LA DEMANDE DU CONSULTANT\n"
            + "Actions à appliquer maintenant :\n- "
            + "\n- ".join(actions or ["exécuter la demande d'amélioration mémorisée"])
            + "\n\nUtilise TOUTES les preuves acceptées sans exception. "
              "N'ouvre pas un nouveau cycle EnnoScholar et n'effectue aucune nouvelle collecte de publications.\n"
            + "Message actuel : " + str(raw_message or "").strip()
        ).strip()
    elif _substantive(routing):
        contract = _make_contract(raw_message, routing, section_id, section_title, scope, "current_consultant_request")
        effective = str(raw_message or "").strip()
    elif contract and _same_target(contract, section_id, section_title, scope) and has_accepted_sources:
        effective = (
            str(contract.get("instruction") or "").strip()
            + "\n\nSUIVI DU CONSULTANT\n"
            + str(raw_message or "").strip()
        ).strip()
    else:
        effective = str(raw_message or "").strip()

    history = [dict(x) for x in (memory.get("history") or []) if isinstance(x, dict)]
    history.append({
        "raw_message": str(raw_message or "").strip(),
        "effective_instruction": effective,
        "target_scope": scope,
        "target_section_id": section_id,
        "target_section_title": section_title,
        "intents": _intents(routing),
        "has_accepted_sources": bool(has_accepted_sources),
        "resume_from_contract": resume,
    })
    memory.update({
        "policy_version": POLICY_VERSION,
        "active_contract": contract,
        "history": history[-40:],
        "last_effective_instruction": effective,
    })
    return memory, effective
