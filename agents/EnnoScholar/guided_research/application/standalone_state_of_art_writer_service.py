# -*- coding: utf-8 -*-
from __future__ import annotations

"""Rédaction d'une revue autonome quand aucun diagnostic Agent 1 n'existe.

Ce writer ne fabrique ni DiagnosticRun ni verrou officiel. Il travaille sur le
contexte de la conversation et sur les sources explicitement acceptées. Le plan
est produit à partir du corpus et de la demande, sans nombre de sections fixe.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from modules.LLM.llm_client import LLMClient

from ...consultant_plan_service import write_json


def _clean(value: Any, limit: int = 20000) -> str:
    return re.sub(
        r"\s+", " ", str(value or "").replace("\x00", " ")
    ).strip()[:limit]


def _extract_json(value: Any) -> dict[str, Any]:
    raw = str(value or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
    return {}


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _evidence_texts(card: Mapping[str, Any]) -> list[str]:
    """Normalise les variantes historiques du champ de preuves des Article Cards.

    Les cartes extractives récentes utilisent ``evidence`` comme un objet de
    provenance et rangent les passages scientifiques dans des listes dédiées.
    Les anciennes cartes exposaient directement une liste sous ``evidence``.
    Le writer doit accepter les deux formes sans tenter de découper un mapping.
    """

    raw: Any = (
        card.get("evidence_units")
        or card.get("extracted_evidence")
        or card.get("key_scientific_passages")
        or []
    )
    if not raw:
        legacy = card.get("evidence")
        raw = legacy if isinstance(legacy, (list, tuple, str)) else []

    if isinstance(raw, Mapping):
        raw_items: list[Any] = []
        for key in (
            "items",
            "units",
            "claims",
            "passages",
            "results",
            "limitations",
        ):
            value = raw.get(key)
            if isinstance(value, (list, tuple)):
                raw_items.extend(value)
            elif isinstance(value, str):
                raw_items.append(value)
    elif isinstance(raw, (list, tuple)):
        raw_items = list(raw)
    elif isinstance(raw, str):
        raw_items = [raw]
    else:
        raw_items = []

    output: list[str] = []
    for item in raw_items:
        if isinstance(item, Mapping):
            value = (
                item.get("text")
                or item.get("claim")
                or item.get("passage")
                or item.get("content")
            )
        else:
            value = item
        cleaned = _clean(value, 1800)
        if cleaned and cleaned not in output:
            output.append(cleaned)
        if len(output) >= 12:
            break
    return output


def _plan_rows(approved_plan: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(approved_plan or []):
        if not isinstance(raw, Mapping):
            continue
        title = _clean(raw.get("title"), 700)
        if not title:
            continue
        try:
            level = max(1, min(5, int(raw.get("level") or 1)))
        except (TypeError, ValueError):
            level = 1
        rows.append({
            "section_id": _clean(raw.get("section_id"), 120) or f"S{index + 1}",
            "title": title,
            "objective": _clean(raw.get("objective"), 2500),
            "level": level,
            "parent_id": _clean(raw.get("parent_id"), 120) or None,
            "order": index + 1,
        })
    return rows


def _apply_plan_metadata(
    draft: dict[str, Any],
    approved_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    """Réapplique la hiérarchie contractuelle sans réécrire le texte du LLM."""

    if not approved_plan:
        return draft
    sections = draft.get("sections")
    if not isinstance(sections, list) or len(sections) != len(approved_plan):
        return draft
    for section, plan_row in zip(sections, approved_plan):
        if not isinstance(section, dict):
            continue
        if _clean(section.get("title"), 700).casefold() != _clean(
            plan_row.get("title"), 700
        ).casefold():
            continue
        section["section_id"] = plan_row.get("section_id")
        section["level"] = plan_row.get("level") or 1
        section["parent_id"] = plan_row.get("parent_id")
    return draft


def _card_rows(
    cards_payload: Mapping[str, Any],
    selected_sources: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    cards: list[Mapping[str, Any]] = []
    for key in ("cards", "article_cards", "items", "articles"):
        value = cards_payload.get(key)
        if isinstance(value, list):
            cards = [row for row in value if isinstance(row, Mapping)]
            break

    accepted = [
        dict(row)
        for row in selected_sources
        if isinstance(row, Mapping)
        and _clean(row.get("consultant_decision"), 40).casefold()
        == "accepted"
    ]
    candidate_ids = {
        _clean(row.get("candidate_id"), 200)
        for row in accepted
        if _clean(row.get("candidate_id"), 200)
    }
    prepared_article_ids = {
        str((row.get("fulltext_preparation") or {}).get("article_id"))
        for row in accepted
        if isinstance(row.get("fulltext_preparation"), Mapping)
        and (row.get("fulltext_preparation") or {}).get("usable_as_scientific_evidence")
    }

    rows: list[dict[str, Any]] = []
    for card in cards:
        candidate_id = _clean(card.get("guided_candidate_id"), 200)
        article_id = str(card.get("article_id") or "")
        if candidate_ids and candidate_id not in candidate_ids and article_id not in prepared_article_ids:
            continue
        row = {
            "source_id": f"S{len(rows) + 1}",
            "candidate_id": candidate_id,
            "article_id": card.get("article_id"),
            "title": _clean(card.get("title"), 700),
            "authors": list(card.get("authors") or [])[:12],
            "year": card.get("year"),
            "doi": _clean(card.get("doi"), 500),
            "url": _clean(card.get("url"), 1500),
            "verrou_ids": [
                _clean(value, 120)
                for value in (
                    card.get("verrou_ids")
                    or card.get("target_verrous")
                    or []
                )
                if _clean(value, 120)
            ],
            "scientific_problem": _clean(
                card.get("probleme_scientifique")
                or card.get("scientific_problem"),
                1800,
            ),
            "objective": _clean(
                card.get("objectif") or card.get("objective"), 1800
            ),
            "method": _clean(
                card.get("methode")
                or card.get("method")
                or card.get("technical_principle"),
                3500,
            ),
            "results": _clean(
                card.get("resultats")
                or card.get("results")
                or card.get("main_results"),
                3500,
            ),
            "limitations": _clean(
                card.get("limites")
                or card.get("limitations")
                or card.get("limite_pour_notre_projet"),
                3500,
            ),
            "impact_on_verrou": _clean(
                card.get("impact_on_verrou")
                or card.get("transposition_to_project"),
                2500,
            ),
            "evidence": _evidence_texts(card),
        }
        if row["title"]:
            rows.append(row)
    return rows


def _guard(
    draft: Mapping[str, Any],
    *,
    sources: list[dict[str, Any]],
    verrous: list[dict[str, Any]],
    approved_plan: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    sections = draft.get("sections")
    if not isinstance(sections, list) or not sections:
        sections = []
        errors.append("missing_sections")
    allowed_citations = {row["source_id"] for row in sources}
    expected_verrous = {
        _clean(row.get("id"), 120) for row in verrous if _clean(row.get("id"), 120)
    }
    covered_verrous: set[str] = set()
    used_citations: set[str] = set()
    paragraph_errors: list[dict[str, Any]] = []

    expected_plan_titles = [
        _clean(row.get("title"), 700)
        for row in (approved_plan or [])
        if _clean(row.get("title"), 700)
    ]
    actual_plan_titles = [
        _clean(row.get("title"), 700)
        for row in sections
        if isinstance(row, Mapping) and _clean(row.get("title"), 700)
    ]
    if expected_plan_titles and [value.casefold() for value in actual_plan_titles] != [
        value.casefold() for value in expected_plan_titles
    ]:
        errors.append("approved_plan_not_respected")

    for section_index, section in enumerate(sections):
        if not isinstance(section, Mapping):
            errors.append("invalid_section")
            continue
        title = _clean(section.get("title"), 500)
        if not title:
            errors.append("missing_section_title")
        section_verrous = {
            _clean(value, 120)
            for value in (section.get("verrou_ids") or [])
            if _clean(value, 120) in expected_verrous
        }
        covered_verrous.update(section_verrous)
        paragraphs = section.get("paragraphs")
        if not isinstance(paragraphs, list) or not paragraphs:
            errors.append("empty_section")
            continue
        for paragraph_index, paragraph in enumerate(paragraphs):
            if not isinstance(paragraph, Mapping):
                paragraph_errors.append({
                    "section": section_index,
                    "paragraph": paragraph_index,
                    "error": "invalid_paragraph",
                })
                continue
            text = _clean(paragraph.get("text"), 12000)
            citations = {
                _clean(value, 40)
                for value in (paragraph.get("citations") or [])
                if _clean(value, 40)
            }
            unknown = sorted(citations - allowed_citations)
            if len(text) < 40:
                paragraph_errors.append({
                    "section": section_index,
                    "paragraph": paragraph_index,
                    "error": "paragraph_too_short",
                })
            if not citations:
                paragraph_errors.append({
                    "section": section_index,
                    "paragraph": paragraph_index,
                    "error": "uncited_paragraph",
                })
            if unknown:
                paragraph_errors.append({
                    "section": section_index,
                    "paragraph": paragraph_index,
                    "error": "unknown_citations",
                    "values": unknown,
                })
            used_citations.update(citations & allowed_citations)

    missing_verrous = sorted(expected_verrous - covered_verrous)
    if missing_verrous:
        errors.append("missing_verrou_coverage")
    if paragraph_errors:
        errors.append("invalid_evidence_attribution")
    if not used_citations:
        errors.append("no_citation_used")
    return {
        "ok": not errors,
        "passed": not errors,
        "errors": list(dict.fromkeys(errors)),
        "paragraph_errors": paragraph_errors,
        "allowed_citations": sorted(allowed_citations),
        "used_citations": sorted(used_citations),
        "expected_verrous": sorted(expected_verrous),
        "covered_verrous": sorted(covered_verrous),
        "missing_verrous": missing_verrous,
        "expected_plan_titles": expected_plan_titles,
        "actual_plan_titles": actual_plan_titles,
    }


def _markdown(
    draft: Mapping[str, Any],
    *,
    sources: list[dict[str, Any]],
) -> str:
    lines = [f"# {_clean(draft.get('title'), 700) or 'État de l’art scientifique'}"]
    for section in draft.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        try:
            level = max(1, min(5, int(section.get("level") or 1)))
        except (TypeError, ValueError):
            level = 1
        lines.extend(["", f"{'#' * (level + 1)} {_clean(section.get('title'), 700)}"])
        for paragraph in section.get("paragraphs") or []:
            if not isinstance(paragraph, Mapping):
                continue
            text = _clean(paragraph.get("text"), 20000)
            citations = [
                _clean(value, 40)
                for value in (paragraph.get("citations") or [])
                if _clean(value, 40)
            ]
            if text:
                lines.extend(["", text + (f" [{', '.join(citations)}]" if citations else "")])
    lines.extend(["", "## Références"])
    for source in sources:
        authors = ", ".join(_clean(value, 150) for value in source.get("authors") or [])
        identity = ". ".join(
            value
            for value in (
                authors,
                str(source.get("year") or "").strip(),
                _clean(source.get("title"), 700),
            )
            if value
        )
        locator = _clean(source.get("doi") or source.get("url"), 1500)
        lines.append(
            f"- [{source['source_id']}] {identity}"
            + (f". {locator}" if locator else "")
        )
    return "\n".join(lines).strip() + "\n"


def _deterministic_evidence_draft(
    *,
    project_brief: Mapping[str, Any],
    verrous: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    approved_plan: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produit une version publiable sans appel LLM ni affirmation ajoutée.

    Cette récupération est volontairement sobre : elle reformule uniquement les
    champs déjà présents dans les Article Cards. Elle permet à une saturation du
    fournisseur LLM ou à un JSON mal formé de ne pas bloquer toute la rédaction.
    """

    project_name = _clean(
        project_brief.get("project_name")
        or project_brief.get("name")
        or project_brief.get("title"),
        500,
    )
    sections: list[dict[str, Any]] = []
    plan = approved_plan or []
    section_seeds: list[dict[str, Any]] = plan or [
        {
            "title": verrou.get("title"),
            "level": 1,
            "section_id": f"V{index + 1}",
            "verrou_ids": [verrou.get("id")],
        }
        for index, verrou in enumerate(verrous)
    ]
    for section_seed in section_seeds:
        target_ids = {
            _clean(value, 120)
            for value in (section_seed.get("verrou_ids") or [])
            if _clean(value, 120)
        }
        section_verrous = [
            row for row in verrous if not target_ids or _clean(row.get("id"), 120) in target_ids
        ] or verrous
        verrou = section_verrous[0]
        verrou_id = _clean(verrou.get("id"), 120)
        verrou_title = _clean(verrou.get("title"), 1200)
        directly_linked = [
            source
            for source in sources
            if verrou_id in {
                _clean(value, 120)
                for value in (source.get("verrou_ids") or [])
            }
        ]
        evidence_sources = directly_linked or sources
        paragraphs: list[dict[str, Any]] = []
        for source in evidence_sources:
            source_title = _clean(source.get("title"), 700)
            year = _clean(source.get("year"), 20)
            identity = (
                f'La publication « {source_title} »'
                + (f" ({year})" if year else "")
            )
            evidence_parts: list[str] = []
            field_labels = (
                ("scientific_problem", "Le problème scientifique étudié est"),
                ("objective", "L'objectif annoncé est"),
                ("method", "L'approche décrite repose sur"),
                ("results", "Les résultats rapportés indiquent"),
                ("limitations", "Les limites explicites sont"),
                ("impact_on_verrou", "La transposition au verrou est décrite ainsi"),
            )
            for field, label in field_labels:
                value = _clean(source.get(field), 2200)
                if value:
                    evidence_parts.append(f"{label} : {value}")
            if not evidence_parts:
                evidence_parts.append(
                    "l'Article Card validée ne contient pas assez d'éléments "
                    "structurés pour soutenir une conclusion scientifique plus précise"
                )
            text = (
                f"{identity} est examinée au regard du verrou « {verrou_title} ». "
                + " ".join(evidence_parts)
                + "."
            )
            paragraphs.append({
                "text": text,
                "citations": [_clean(source.get("source_id"), 40)],
            })
        sections.append({
            "section_id": _clean(section_seed.get("section_id"), 120),
            "title": _clean(section_seed.get("title"), 700) or verrou_title,
            "level": section_seed.get("level") or 1,
            "parent_id": section_seed.get("parent_id"),
            "verrou_ids": [
                _clean(row.get("id"), 120) for row in section_verrous
            ],
            "paragraphs": paragraphs,
        })

    title = "État de l'art scientifique"
    if project_name:
        title += f" — {project_name}"
    return {"title": title, "sections": sections}


def run_standalone_state_of_art_writer(
    *,
    llm: LLMClient,
    project_brief: Mapping[str, Any],
    verrous: Iterable[Mapping[str, Any]],
    review_scope: str,
    selected_sources: Iterable[Mapping[str, Any]],
    cards_payload: Mapping[str, Any],
    output_dir: str | Path,
    revision_request: str = "",
    current_markdown: str = "",
    approved_plan: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    output_root = Path(output_dir)
    markdown_path = output_root / "state_of_art.md"
    payload_path = output_root / "state_of_art_payload.json"
    rejected_path = output_root / "state_of_art_rejected.md"
    active_verrous = [
        {
            "id": _clean(row.get("id"), 120),
            "title": _clean(row.get("title"), 1200),
            "justification": _clean(row.get("justification"), 3500),
            "supporting_context": _clean(row.get("supporting_context"), 5000),
        }
        for row in verrous
        if isinstance(row, Mapping)
        and _clean(row.get("id"), 120)
        and _clean(row.get("title"), 1200)
    ]
    sources = _card_rows(cards_payload, selected_sources)
    plan = _plan_rows(approved_plan)
    if not active_verrous:
        return {
            "ok": False,
            "status": "missing_standalone_verrou",
            "message": "Aucun verrou de conversation n'est défini.",
            "markdown_output_path": "",
        }
    if not sources:
        return {
            "ok": False,
            "status": "missing_verified_article_cards",
            "message": (
                "Aucune Article Card vérifiée ne correspond aux sources acceptées. "
                "Validez puis préparez au moins une publication scientifique."
            ),
            "markdown_output_path": "",
        }

    schema = {
        "title": "standalone_state_of_art_draft",
        "type": "object",
        "required": ["title", "sections"],
        "properties": {
            "title": {"type": "string"},
            "sections": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["title", "verrou_ids", "paragraphs"],
                    "properties": {
                        "section_id": {"type": "string"},
                        "title": {"type": "string"},
                        "level": {"type": "integer"},
                        "parent_id": {"type": ["string", "null"]},
                        "verrou_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "paragraphs": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "required": ["text", "citations"],
                                "properties": {
                                    "text": {"type": "string"},
                                    "citations": {
                                        "type": "array",
                                        "minItems": 1,
                                        "items": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }
    prompt = f"""
Tu rédiges un état de l'art scientifique de niveau consultant à partir des seules
preuves structurées fournies. Le projet n'a pas de diagnostic Agent 1 : n'invente
donc aucun fait projet absent du CONTEXTE DÉCLARÉ.

CONTEXTE DÉCLARÉ
{json.dumps(dict(project_brief), ensure_ascii=False)}

PORTÉE
{_clean(review_scope, 40)}

VERROUS À COUVRIR
{json.dumps(active_verrous, ensure_ascii=False)}

PLAN VALIDÉ PAR LE CONSULTANT
{json.dumps(plan, ensure_ascii=False)}

SOURCES AUTORISÉES ET PREUVES
{json.dumps(sources, ensure_ascii=False)}

DEMANDE DE RÉVISION
{_clean(revision_request, 5000)}

BROUILLON PRÉCÉDENT, seulement en cas de révision
{_clean(current_markdown, 16000) if revision_request else ''}

Si PLAN VALIDÉ PAR LE CONSULTANT n'est pas vide, respecte exactement son ordre,
ses titres et sa hiérarchie : alimente ce plan avec les preuves sans le remplacer.
Sinon seulement, construis librement le plan qui convient à l'histoire scientifique
réellement portée par les sources.
Pour une portée per_verrou, reste centré sur le verrou demandé. Pour une portée
global, articule les convergences, différences et dépendances entre tous les verrous.
Compare les familles de méthodes, leurs protocoles, résultats, limites et conditions
de transférabilité. Défends le caractère non résolu du verrou uniquement lorsque les
preuves le permettent et signale explicitement l'absence de preuve directe.

Retourne uniquement le JSON du schéma. Chaque section doit indiquer les verrou_ids
exacts qu'elle couvre. Chaque paragraphe contient des citations choisies uniquement
parmi S1, S2, etc. N'invente aucune citation, valeur numérique, méthode ou conclusion.
Une source connexe ne devient jamais une validation directe du contexte projet.
""".strip()

    attempts: list[dict[str, Any]] = []
    draft: dict[str, Any] = {}
    guard: dict[str, Any] = {"ok": False, "errors": ["writer_not_run"]}
    previous_raw = ""
    writer_mode = "llm"
    for attempt in range(2):
        current_prompt = prompt
        if attempt:
            current_prompt = f"""
Répare le brouillon structuré ci-dessous sans ajouter de faits ni de sources.
Corrige uniquement les violations du garde-fou et retourne le JSON complet.

ERREURS
{json.dumps(guard, ensure_ascii=False)}

BROUILLON
{_clean(previous_raw, 30000)}

CONTRAT ORIGINAL
{prompt}
""".strip()
        try:
            raw = llm.generate(
                current_prompt,
                temperature=0.05,
                max_output_tokens=12000,
                retries=1,
                json_mode=True,
                response_schema=schema,
                request_name=(
                    "ennoscholar:standalone_state_of_art:writer"
                    if attempt == 0
                    else "ennoscholar:standalone_state_of_art:guard_repair"
                ),
            )
        except Exception as exc:
            attempts.append({
                "attempt": attempt + 1,
                "status": "provider_unavailable",
                "error_type": type(exc).__name__,
            })
            break
        previous_raw = raw
        draft = _apply_plan_metadata(_extract_json(raw), plan)
        guard = _guard(
            draft,
            sources=sources,
            verrous=active_verrous,
            approved_plan=plan,
        )
        attempts.append({"attempt": attempt + 1, "guard": guard})
        if guard.get("ok"):
            break

    if not guard.get("ok"):
        draft = _deterministic_evidence_draft(
            project_brief=project_brief,
            verrous=active_verrous,
            sources=sources,
            approved_plan=plan,
        )
        draft = _apply_plan_metadata(draft, plan)
        guard = _guard(
            draft,
            sources=sources,
            verrous=active_verrous,
            approved_plan=plan,
        )
        writer_mode = "deterministic_evidence_only"
        attempts.append({
            "status": "automatic_evidence_recovery",
            "guard": guard,
        })

    markdown = _markdown(draft, sources=sources) if draft else ""
    word_count = len(re.findall(r"\b[\wÀ-ÿ'’-]+\b", markdown))
    minimum_consultant_words = 900 if len(active_verrous) == 1 else 1400
    consultant_quality_ready = bool(
        guard.get("ok") and word_count >= minimum_consultant_words
    )
    result = {
        "ok": bool(guard.get("ok")),
        "status": "ok" if guard.get("ok") else "evidence_attribution_incomplete",
        "payload_type": "standalone_guided_state_of_art_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "review_scope": review_scope,
        "project_brief": dict(project_brief),
        "verrous": active_verrous,
        "approved_plan": plan,
        "sources": sources,
        "draft_json": draft,
        "guard": guard,
        "attempts": attempts,
        "writer_mode": writer_mode,
        "quality": {
            "consultant_quality_ready": consultant_quality_ready,
            "word_count": word_count,
            "minimum_consultant_words": minimum_consultant_words,
            "issues": (
                []
                if consultant_quality_ready
                else ["document_too_short_for_consultant_depth"]
                if guard.get("ok")
                else ["evidence_attribution_incomplete"]
            ),
        },
        "markdown": markdown if guard.get("ok") else "",
        "markdown_output_path": str(markdown_path) if guard.get("ok") else "",
        "rejected_markdown_output_path": (
            str(rejected_path) if markdown and not guard.get("ok") else ""
        ),
        "payload_path": str(payload_path),
    }
    write_json(payload_path, result)
    if guard.get("ok"):
        _write_text(markdown_path, markdown)
    elif markdown:
        _write_text(rejected_path, markdown)
    return result


__all__ = ["run_standalone_state_of_art_writer"]
