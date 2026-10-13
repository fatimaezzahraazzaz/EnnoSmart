from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Iterable

from .audit_service import audit_text
from .semantic_routing_service import route_for_section
from ..domain.models import (
    ImprovementIntent,
    ParsedSection,
    RoutingDecision,
    SectionFunction,
    SectionRoutingPlan,
)

WORKFLOW_VERSION = "ennoamel_cir_section_progressive_v3_19"

_SEVERITY = {"low": 1, "medium": 2, "high": 3, "blocking": 4}
_CITATION_RE = re.compile(
    r"\[[A-Za-z]?\d+(?:\s*[-,;]\s*[A-Za-z]?\d+)*\]"
    r"|\([A-ZÀ-Ý][^)]*\b(?:19|20)\d{2}\)"
    r"|https?://\S+|\bdoi\s*:\s*\S+",
    flags=re.I,
)

# Fonctions de section où un manque de preuve peut raisonnablement conduire
# à une recherche scientifique dans le parcours complet.
# Aucun titre CIR/projet n'est codé en dur : on s'appuie uniquement sur la
# classification sémantique déjà produite par le routeur.
_SCIENTIFIC_FUNCTIONS = {
    SectionFunction.SCIENTIFIC_LANDSCAPE,
    SectionFunction.UNCERTAINTY,
    SectionFunction.METHOD,
    SectionFunction.RESULT,
    SectionFunction.LIMITATION,
    SectionFunction.CONTRIBUTION,
    SectionFunction.SYNTHESIS,
}

# CONTEXT peut être argumentatif, mais l'absence de citation seule ne suffit pas
# à déclencher une recherche automatique.
_ARGUMENTATIVE_FUNCTIONS = {
    *_SCIENTIFIC_FUNCTIONS,
    SectionFunction.CONTEXT,
}


def _sha(text: str) -> str:
    return hashlib.sha256(
        str(text or "").encode("utf-8", errors="ignore")
    ).hexdigest()


def _words(text: str) -> list[str]:
    return re.findall(r"\b[\wÀ-ÿ'-]+\b", str(text or ""))


def _visible_section_ref(
    section: ParsedSection,
    source: str,
) -> str:
    """Numéro visible de section (ex. 1.3.1.6) si le document en possède un."""
    first_line = str(source[int(section.start) : int(section.end)]).lstrip().splitlines()
    heading = first_line[0] if first_line else ""
    match = re.match(
        r"^\s*(?:#{1,6}\s*)?(?P<ref>\d+(?:\.\d+)+)\.?\s+",
        heading,
    )
    if match:
        return str(match.group("ref") or "").strip()
    simple = re.match(
        r"^\s*(?:#{1,6}\s*)?(?P<ref>\d+)[.)]\s+",
        heading,
    )
    return str(simple.group("ref") or "").strip() if simple else ""


def _section_body_span(
    section: ParsedSection,
    source: str,
) -> tuple[int, int]:
    """Retourne la section telle que parsée, titre compris.

    `parse_sections` découpe déjà le document au prochain titre détecté.
    Les unités sont donc non chevauchantes. On garde le titre dans l'unité pour
    que la réécriture d'une section reste structurée comme en mode section
    manuel.
    """
    return int(section.start), int(section.end)


def split_document_into_units(
    source: str,
    sections: Iterable[ParsedSection],
    *,
    min_words: int = 5,
) -> list[dict[str, Any]]:
    """Découpe le CIR en vraies sections, jamais en paragraphes.

    Une unité = exactement une section issue de `parse_sections`.
    Les offsets restent ceux de la VERSION ACTIVE, ce qui permet de reconstruire
    une seule candidate complète à la fin sans modifier la source active.
    """
    text = str(source or "")
    output: list[dict[str, Any]] = []

    for section in sections:
        left, right = _section_body_span(section, text)
        if right <= left:
            continue

        value = text[left:right]
        if not value.strip():
            continue

        # Une section titre-seul reste connue du workflow mais n'a aucune raison
        # de déclencher un Writer ou Scholar.
        content_words = len(_words(value))
        ordinal = len(output) + 1

        unit_id = "sec-" + hashlib.sha1(
            f"{section.section_id}:{left}:{right}:{_sha(value)}".encode("utf-8")
        ).hexdigest()[:14]

        output.append(
            {
                "unit_id": unit_id,
                "ordinal": ordinal,
                "section_id": section.section_id,
                "section_ref": _visible_section_ref(section, text),
                "section_title": section.title,
                "section_level": section.level,
                "start": left,
                "end": right,
                "source_sha256": _sha(value),
                "source_chars": right - left,
                "source_words": content_words,
                "status": "pending",
                "action": "keep",
                "weak": False,
                "needs_research": False,
                "needs_editorial_rewrite": False,
                "weakness_reasons": [],
                "audit": [],
                "research": {},
                "generation": {},
                "title_only": content_words < min_words,
            }
        )

    return output


def _plan_for_section(
    section_id: str,
    routing: RoutingDecision,
) -> SectionRoutingPlan | None:
    return next(
        (
            row
            for row in routing.section_plan
            if str(row.section_id) == str(section_id)
        ),
        None,
    )


def _scientific_source_gap(
    text: str,
    plan: SectionRoutingPlan | None,
) -> bool:
    """Détecte un manque de preuve à l'échelle SECTION.

    On ne lance pas Scholar uniquement parce qu'une courte section n'a pas de
    citation. Cela évite notamment de rechercher des articles pour des titres,
    métadonnées, introductions administratives ou petites transitions.
    """
    if plan is None or not plan.needs_scholar:
        return False
    if plan.function not in _SCIENTIFIC_FUNCTIONS:
        return False

    words = len(_words(text))
    if words < 80:
        return False

    return len(_CITATION_RE.findall(text)) == 0


def annotate_units(
    source: str,
    units: list[dict[str, Any]],
    routing: RoutingDecision,
) -> list[dict[str, Any]]:
    """Décide KEEP / REWRITE / RESEARCH à l'échelle section.

    Le routeur global a déjà produit `section_plan`. On réutilise ce plan sans
    refaire un appel de classification par section.
    """
    intents = set(routing.intents)

    style_requested = bool(
        intents
        & {
            ImprovementIntent.CLARITY,
            ImprovementIntent.STYLE,
            ImprovementIntent.STRUCTURE,
            ImprovementIntent.CONCISION,
            ImprovementIntent.GENERAL_REVISION,
        }
    ) or routing.editorial_only

    scientific_requested = bool(
        intents
        & {
            ImprovementIntent.ARGUMENTATION,
            ImprovementIntent.SCIENTIFIC_ENRICHMENT,
            ImprovementIntent.RESEARCH,
            ImprovementIntent.CIR_ELIGIBILITY,
            ImprovementIntent.GENERAL_REVISION,
        }
    ) and not routing.editorial_only

    research_allowed = (
        not routing.forbids_new_research
        and not routing.forbids_scholar
    )

    for unit in units:
        text = source[int(unit["start"]) : int(unit["end"])]
        plan = _plan_for_section(
            str(unit.get("section_id") or ""),
            routing,
        )

        if unit.get("title_only"):
            unit.update(
                {
                    "weak": False,
                    "action": "keep",
                    "needs_research": False,
                    "needs_editorial_rewrite": False,
                    "weakness_reasons": [],
                    "audit": [],
                    "section_function": (
                        plan.function.value
                        if plan is not None
                        else routing.section_function.value
                    ),
                    "section_route": (
                        plan.route.value
                        if plan is not None
                        else routing.specialist_route.value
                    ),
                }
            )
            continue

        unit_routing = (
            route_for_section(plan, routing)
            if plan is not None
            else routing
        )
        findings = audit_text(text, unit_routing)
        serial = [
            row.model_dump(mode="json")
            for row in findings
        ]

        max_severity = max(
            (
                _SEVERITY.get(
                    str(row.severity).casefold(),
                    0,
                )
                for row in findings
            ),
            default=0,
        )

        editorial_weak = bool(
            max_severity >= _SEVERITY["medium"]
        )
        source_gap = _scientific_source_gap(
            text,
            plan,
        )

        function = (
            plan.function
            if plan is not None
            else routing.section_function
        )

        # Les défauts purement rédactionnels ne doivent jamais suffire à lancer
        # Scholar. Le routeur doit déjà demander Scholar ET l'audit doit pointer
        # un problème de fond/traçabilité, ou la section scientifique doit être
        # suffisamment longue sans preuve identifiable.
        editorial_only_codes = {
            "long_sentences",
            "repetition",
            "weak_narrative_links",
            "strengthen_precision",
        }
        scientific_audit_weak = any(
            _SEVERITY.get(
                str(row.severity).casefold(),
                0,
            )
            >= _SEVERITY["medium"]
            and str(row.code) not in editorial_only_codes
            for row in findings
        )

        scientifically_weak = bool(
            scientific_requested
            and research_allowed
            and plan is not None
            and plan.needs_scholar
            and function in _ARGUMENTATIVE_FUNCTIONS
            and len(_words(text)) >= 60
            and (
                source_gap
                or scientific_audit_weak
            )
        )

        weak = bool(
            editorial_weak
            or source_gap
            or scientifically_weak
        )

        reasons = [
            str(row.code)
            for row in findings
            if _SEVERITY.get(
                str(row.severity).casefold(),
                0,
            )
            >= _SEVERITY["medium"]
        ]
        if source_gap:
            reasons.append("scientific_source_gap")
        if scientifically_weak:
            reasons.append("section_scientific_strengthening_needed")

        needs_research = scientifically_weak
        needs_editorial = bool(
            weak
            and style_requested
        )

        if needs_research:
            action = "research"
        elif needs_editorial:
            action = "rewrite"
        else:
            action = "keep"

        unit.update(
            {
                "weak": weak,
                "action": action,
                "needs_research": needs_research,
                "needs_editorial_rewrite": needs_editorial,
                "weakness_reasons": list(
                    dict.fromkeys(reasons)
                ),
                "audit": serial,
                "section_function": function.value,
                "section_route": (
                    plan.route.value
                    if plan is not None
                    else routing.specialist_route.value
                ),
            }
        )

    return units


def build_workflow(
    *,
    base_text: str,
    base_version_id: str,
    base_version_number: int | None,
    instruction: str,
    sections: list[ParsedSection],
    routing: RoutingDecision,
) -> dict[str, Any]:
    units = split_document_into_units(
        base_text,
        sections,
    )
    annotate_units(
        base_text,
        units,
        routing,
    )

    return {
        "version": WORKFLOW_VERSION,
        "granularity": "section",
        "active": True,
        "phase": "running",
        "base_version_id": str(base_version_id),
        "base_version_number": base_version_number,
        "base_sha256": _sha(base_text),
        "instruction": str(instruction or "").strip(),
        "cursor": 0,
        "units": units,
        "patches": [],
        "routing": routing.model_dump(mode="json"),
        "stats": {
            "total": len(units),
            "kept": 0,
            "rewritten": 0,
            "strengthened": 0,
            "research_waits": 0,
            "research_without_sources": 0,
            "failed": 0,
        },
        "last_progress": {},
    }


def apply_patches(
    base_text: str,
    patches: Iterable[dict[str, Any]],
) -> str:
    """Reconstruit la candidate complète depuis la version active."""
    text = str(base_text or "")
    rows = sorted(
        [dict(row) for row in patches],
        key=lambda row: int(row.get("start") or 0),
        reverse=True,
    )

    for row in rows:
        start = int(row.get("start") or 0)
        end = int(row.get("end") or 0)
        expected = str(row.get("source_sha256") or "")

        if start < 0 or end < start or end > len(text):
            raise ValueError(
                f"Patch de section hors limites: {start}:{end}"
            )

        current = text[start:end]
        if expected and _sha(current) != expected:
            raise ValueError(
                "Le texte de base de la section a changé ; "
                "le workflow doit repartir de la version active."
            )

        text = (
            text[:start]
            + str(row.get("replacement") or "")
            + text[end:]
        )

    return text


def unit_source(
    base_text: str,
    unit: dict[str, Any],
) -> str:
    start = int(unit["start"])
    end = int(unit["end"])
    value = str(base_text or "")[start:end]

    if _sha(value) != str(
        unit.get("source_sha256") or ""
    ):
        raise ValueError(
            "La section ne correspond plus à la version active de base."
        )

    return value


def find_unit(
    workflow: dict[str, Any],
    unit_id: str,
) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in (workflow.get("units") or [])
            if row.get("unit_id") == unit_id
        ),
        None,
    )


def current_unit(
    workflow: dict[str, Any],
) -> dict[str, Any] | None:
    units = list(
        workflow.get("units") or []
    )
    cursor = max(
        0,
        int(workflow.get("cursor") or 0),
    )
    return (
        units[cursor]
        if cursor < len(units)
        else None
    )


def advance_cursor(
    workflow: dict[str, Any],
) -> None:
    workflow["cursor"] = int(
        workflow.get("cursor") or 0
    ) + 1


def add_patch(
    workflow: dict[str, Any],
    unit: dict[str, Any],
    replacement: str,
    *,
    mode: str,
    generation: dict[str, Any] | None = None,
) -> None:
    workflow.setdefault("patches", []).append(
        {
            "unit_id": unit["unit_id"],
            "start": int(unit["start"]),
            "end": int(unit["end"]),
            "source_sha256": unit["source_sha256"],
            "replacement": str(replacement or ""),
            "replacement_sha256": _sha(
                replacement
            ),
            "mode": mode,
        }
    )

    unit["status"] = (
        "strengthened"
        if mode == "scientific"
        else "rewritten"
    )
    unit["generation"] = dict(
        generation or {}
    )

    stats = workflow.setdefault(
        "stats",
        {},
    )
    key = (
        "strengthened"
        if mode == "scientific"
        else "rewritten"
    )
    stats[key] = int(
        stats.get(key) or 0
    ) + 1


def mark_kept(
    workflow: dict[str, Any],
    unit: dict[str, Any],
) -> None:
    if unit.get("status") != "kept":
        unit["status"] = "kept"
        stats = workflow.setdefault(
            "stats",
            {},
        )
        stats["kept"] = int(
            stats.get("kept") or 0
        ) + 1


def max_auto_writes_per_turn() -> int:
    """Nombre de SECTIONS rédigées automatiquement avant checkpoint."""
    try:
        return max(
            1,
            int(
                os.getenv(
                    "ENNOAMEL_PROGRESSIVE_MAX_WRITES_PER_TURN",
                    "3",
                )
            ),
        )
    except Exception:
        return 3


def progress_label(
    workflow: dict[str, Any],
    unit: dict[str, Any] | None,
) -> str:
    total = int(
        (workflow.get("stats") or {}).get("total")
        or len(workflow.get("units") or [])
    )

    if unit is None:
        return f"Parcours terminé ({total}/{total})"

    ordinal = int(
        unit.get("ordinal")
        or int(workflow.get("cursor") or 0) + 1
    )
    section_ref = str(
        unit.get("section_ref") or ""
    ).strip()
    section_title = str(
        unit.get("section_title") or ""
    ).strip()

    label = " — ".join(
        value
        for value in (section_ref, section_title)
        if value
    )
    suffix = f" — {label}" if label else ""

    return (
        f"Section {ordinal}/{total}{suffix}"
    )


def workflow_public_summary(
    workflow: dict[str, Any],
) -> dict[str, Any]:
    stats = dict(
        workflow.get("stats") or {}
    )
    unit = current_unit(workflow)

    return {
        "version": workflow.get("version"),
        "granularity": "section",
        "phase": workflow.get("phase"),
        "active": bool(
            workflow.get("active")
        ),
        "cursor": int(
            workflow.get("cursor") or 0
        ),
        "stats": stats,
        "current": (
            {
                "unit_id": unit.get("unit_id"),
                "ordinal": unit.get("ordinal"),
                "section_id": unit.get("section_id"),
                "section_ref": unit.get("section_ref"),
                "section_title": unit.get("section_title"),
                "status": unit.get("status"),
                "action": unit.get("action"),
            }
            if unit
            else None
        ),
    }
