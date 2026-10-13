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

WORKFLOW_VERSION = "ennoamel_cir_progressive_v3_11"

_SEVERITY = {"low": 1, "medium": 2, "high": 3, "blocking": 4}
_CITATION_RE = re.compile(
    r"\[[A-Za-z]?\d+(?:\s*[-,;]\s*[A-Za-z]?\d+)*\]"
    r"|\([A-ZÀ-Ý][^)]*\b(?:19|20)\d{2}\)"
    r"|https?://\S+|\bdoi\s*:\s*\S+",
    flags=re.I,
)
_IMMUTABLE_RE = re.compile(
    r"(?ms)^\[BLOC DOCUMENT IMMUTABLE\s+id=\"[^\"]+\"[^\]]*\]\s*$"
    r".*?^\[/BLOC DOCUMENT IMMUTABLE\]\s*$"
)


def _sha(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="ignore")).hexdigest()


def _words(text: str) -> list[str]:
    return re.findall(r"\b[\wÀ-ÿ'-]+\b", str(text or ""))


def _sentences(text: str) -> list[str]:
    return [
        row.strip()
        for row in re.split(r"(?<=[.!?])\s+", str(text or "").strip())
        if row.strip()
    ]


def _body_span(section: ParsedSection, source: str) -> tuple[int, int]:
    """Retourne la zone éditable du corps, sans inclure le titre de section.

    Le parser conserve le titre au début de ``section.content``. On protège donc
    la première ligne lorsqu'elle correspond à un vrai titre détecté. Cette règle
    est structurelle et ne connaît aucun plan CIR particulier.
    """

    start, end = int(section.start), int(section.end)
    content = source[start:end]
    first_break = content.find("\n")
    if first_break < 0:
        return start, end
    first_line = content[:first_break].strip()
    title = str(section.title or "").strip()
    normalized_first = re.sub(r"\s+", " ", first_line).casefold()
    normalized_title = re.sub(r"\s+", " ", title).casefold()
    if normalized_title and normalized_title in normalized_first:
        body_start = start + first_break + 1
        while body_start < end and source[body_start] in "\r\n \t":
            body_start += 1
        return body_start, end
    return start, end


def _paragraph_spans(source: str, start: int, end: int) -> list[tuple[int, int]]:
    region = source[start:end]
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"(?ms)(?P<p>\S.*?\S|\S)(?=\n[ \t]*\n|\Z)", region):
        left = start + match.start("p")
        right = start + match.end("p")
        value = source[left:right].strip()
        if not value or _IMMUTABLE_RE.fullmatch(value):
            continue
        spans.append((left, right))
    if not spans and source[start:end].strip():
        spans.append((start, end))
    return spans


def split_document_into_units(
    source: str,
    sections: Iterable[ParsedSection],
    *,
    min_chars: int = 90,
) -> list[dict[str, Any]]:
    """Découpe le CIR en paragraphes stables reliés à leur section réelle.

    Les offsets restent ceux de la VERSION ACTIVE de base. Les réécritures sont
    ensuite conservées comme patches séparés ; on ne modifie jamais cette base.
    """

    text = str(source or "")
    output: list[dict[str, Any]] = []
    paragraph_number = 0
    for section in sections:
        body_start, body_end = _body_span(section, text)
        if body_end <= body_start:
            continue
        spans = _paragraph_spans(text, body_start, body_end)
        # Un PDF peut contenir de petits fragments isolés. On rattache les
        # fragments très courts au bloc voisin plutôt que de lancer une recherche
        # scientifique sur une ligne orpheline.
        merged: list[tuple[int, int]] = []
        for left, right in spans:
            value = text[left:right].strip()
            if merged and len(value) < min_chars:
                prev_left, _ = merged[-1]
                merged[-1] = (prev_left, right)
            else:
                merged.append((left, right))
        for local_index, (left, right) in enumerate(merged, start=1):
            value = text[left:right].strip()
            if not value or len(_words(value)) < 6:
                continue
            paragraph_number += 1
            unit_id = "par-" + hashlib.sha1(
                f"{section.section_id}:{left}:{right}:{_sha(value)}".encode("utf-8")
            ).hexdigest()[:14]
            output.append(
                {
                    "unit_id": unit_id,
                    "ordinal": paragraph_number,
                    "section_id": section.section_id,
                    "section_title": section.title,
                    "section_level": section.level,
                    "paragraph_in_section": local_index,
                    "start": left,
                    "end": right,
                    "source_sha256": _sha(text[left:right]),
                    "source_chars": right - left,
                    "status": "pending",
                    "action": "keep",
                    "weak": False,
                    "needs_research": False,
                    "needs_editorial_rewrite": False,
                    "weakness_reasons": [],
                    "audit": [],
                    "research": {},
                    "generation": {},
                }
            )
    return output


def _plan_for_section(
    section_id: str,
    routing: RoutingDecision,
) -> SectionRoutingPlan | None:
    return next(
        (row for row in routing.section_plan if str(row.section_id) == str(section_id)),
        None,
    )


def _scientific_source_gap(text: str, plan: SectionRoutingPlan | None) -> bool:
    if plan is None or not plan.needs_scholar:
        return False
    words = len(_words(text))
    if words < 45:
        return False
    citations = len(_CITATION_RE.findall(text))
    # L'objectif n'est pas d'exiger artificiellement une bibliographie dans
    # chaque paragraphe, seulement de repérer les passages scientifiques longs
    # dont aucune preuve identifiable n'est attachée au bloc courant.
    return citations == 0


def annotate_units(
    source: str,
    units: list[dict[str, Any]],
    routing: RoutingDecision,
) -> list[dict[str, Any]]:
    """Détermine KEEP / REWRITE / RESEARCH sans appel LLM supplémentaire.

    Le classifieur sémantique de document a déjà produit ``routing.section_plan``.
    On réutilise ce plan pour chaque paragraphe, puis ``audit_text`` pour décider
    si le passage est réellement faible. Ainsi le parcours complet n'ajoute pas
    un appel de classification payant par paragraphe.
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
    research_allowed = not routing.forbids_new_research and not routing.forbids_scholar

    for unit in units:
        text = source[int(unit["start"]): int(unit["end"])]
        plan = _plan_for_section(str(unit.get("section_id") or ""), routing)
        unit_routing = route_for_section(plan, routing) if plan is not None else routing
        findings = audit_text(text, unit_routing)
        serial = [row.model_dump(mode="json") for row in findings]
        max_severity = max(
            (_SEVERITY.get(str(row.severity).casefold(), 0) for row in findings),
            default=0,
        )
        scientific_gap = _scientific_source_gap(text, plan)
        weak = bool(max_severity >= _SEVERITY["medium"] or scientific_gap)
        reasons = [
            str(row.code)
            for row in findings
            if _SEVERITY.get(str(row.severity).casefold(), 0) >= _SEVERITY["medium"]
        ]
        if scientific_gap:
            reasons.append("scientific_source_gap")

        needs_research = bool(
            weak
            and scientific_requested
            and research_allowed
            and plan is not None
            and plan.needs_scholar
            and scientific_gap
        )
        needs_editorial = bool(weak and style_requested)

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
                "weakness_reasons": list(dict.fromkeys(reasons)),
                "audit": serial,
                "section_function": (
                    plan.function.value if plan is not None else routing.section_function.value
                ),
                "section_route": (
                    plan.route.value if plan is not None else routing.specialist_route.value
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
    units = split_document_into_units(base_text, sections)
    annotate_units(base_text, units, routing)
    return {
        "version": WORKFLOW_VERSION,
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
            "failed": 0,
        },
        "last_progress": {},
    }


def apply_patches(base_text: str, patches: Iterable[dict[str, Any]]) -> str:
    """Reconstruit le brouillon sans modifier la version active stockée."""

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
            raise ValueError(f"Patch hors limites: {start}:{end}")
        current = text[start:end]
        if expected and _sha(current) != expected:
            raise ValueError(
                "Le texte de base du paragraphe a changé ; le workflow doit être "
                "recalculé depuis la version active."
            )
        text = text[:start] + str(row.get("replacement") or "") + text[end:]
    return text


def unit_source(base_text: str, unit: dict[str, Any]) -> str:
    start, end = int(unit["start"]), int(unit["end"])
    value = str(base_text or "")[start:end]
    if _sha(value) != str(unit.get("source_sha256") or ""):
        raise ValueError("Le paragraphe ne correspond plus à la version active de base.")
    return value


def find_unit(workflow: dict[str, Any], unit_id: str) -> dict[str, Any] | None:
    return next(
        (row for row in (workflow.get("units") or []) if row.get("unit_id") == unit_id),
        None,
    )


def current_unit(workflow: dict[str, Any]) -> dict[str, Any] | None:
    units = list(workflow.get("units") or [])
    cursor = max(0, int(workflow.get("cursor") or 0))
    return units[cursor] if cursor < len(units) else None


def advance_cursor(workflow: dict[str, Any]) -> None:
    workflow["cursor"] = int(workflow.get("cursor") or 0) + 1


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
            "replacement_sha256": _sha(replacement),
            "mode": mode,
        }
    )
    unit["status"] = "strengthened" if mode == "scientific" else "rewritten"
    unit["generation"] = dict(generation or {})
    stats = workflow.setdefault("stats", {})
    key = "strengthened" if mode == "scientific" else "rewritten"
    stats[key] = int(stats.get(key) or 0) + 1


def mark_kept(workflow: dict[str, Any], unit: dict[str, Any]) -> None:
    if unit.get("status") != "kept":
        unit["status"] = "kept"
        stats = workflow.setdefault("stats", {})
        stats["kept"] = int(stats.get("kept") or 0) + 1


def max_auto_writes_per_turn() -> int:
    try:
        return max(1, int(os.getenv("ENNOAMEL_PROGRESSIVE_MAX_WRITES_PER_TURN", "3")))
    except Exception:
        return 3


def progress_label(workflow: dict[str, Any], unit: dict[str, Any] | None) -> str:
    total = int((workflow.get("stats") or {}).get("total") or len(workflow.get("units") or []))
    if unit is None:
        return f"Parcours terminé ({total}/{total})"
    ordinal = int(unit.get("ordinal") or int(workflow.get("cursor") or 0) + 1)
    section = str(unit.get("section_title") or "").strip()
    suffix = f" — {section}" if section else ""
    return f"Paragraphe {ordinal}/{total}{suffix}"


def workflow_public_summary(workflow: dict[str, Any]) -> dict[str, Any]:
    stats = dict(workflow.get("stats") or {})
    return {
        "version": workflow.get("version"),
        "phase": workflow.get("phase"),
        "active": bool(workflow.get("active")),
        "cursor": int(workflow.get("cursor") or 0),
        "stats": stats,
        "current": (
            {
                "unit_id": current_unit(workflow).get("unit_id"),
                "ordinal": current_unit(workflow).get("ordinal"),
                "section_id": current_unit(workflow).get("section_id"),
                "section_title": current_unit(workflow).get("section_title"),
                "status": current_unit(workflow).get("status"),
                "action": current_unit(workflow).get("action"),
            }
            if current_unit(workflow)
            else None
        ),
    }
