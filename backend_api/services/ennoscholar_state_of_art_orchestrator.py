# -*- coding: utf-8 -*-
from __future__ import annotations
# ENNOSCHOLAR_V169_1_PROJECT_PERSISTENT_CORPUS
from modules.LLM.usage_budget import budgeted_pipeline

"""
services/ennoscholar_state_of_art_orchestrator.py

Backend EnnoScholar — orchestration et lecture de l'état de l'art unifié Phase 5.
Version V11 — preuves atomiques et rédaction vérifiée :
- lit state_of_art_draft_payload.json + state_of_art_draft.md ;
- expose les garde-fous, la traçabilité paragraphe-preuves, la couverture des
  verrous et le statut de qualité consultant ;
- lance le pipeline complet jusqu'à Phase 5 ;
- bloque immédiatement si les phases 4.5, 4.6, 4.7 ou 5 sont invalides ;
- conserve une réponse stable pour le frontend.
"""

import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from services.article_card_builder import is_article_card_ready_for_writing

ROOT = Path(
    os.getenv("ENNOSMART_ROOT")
    or os.getenv("ENNOSMART_PROJECT_ROOT")
    or Path(__file__).resolve().parents[2]
)


# ============================================================
# Helpers chemins / fichiers
# ============================================================

def _ensure_root_on_path() -> None:
    root_str = str(ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    repl = {
        "à": "a", "á": "a", "â": "a", "ä": "a", "ã": "a",
        "ç": "c",
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "î": "i", "ï": "i", "ì": "i", "í": "i",
        "ô": "o", "ö": "o", "ò": "o", "ó": "o",
        "ù": "u", "û": "u", "ü": "u", "ú": "u",
        "ÿ": "y", "ñ": "n", "’": "_", "'": "_", "-": "_", " ": "_",
    }
    for a, b in repl.items():
        text = text.replace(a, b)
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return default
    except Exception:
        return default


def _state_of_art_base(project: Any) -> Path:
    return (
        ROOT
        / "storage"
        / "organismes"
        / _slug(getattr(project, "organisme", ""))
        / "projects"
        / _slug(getattr(project, "project_name", ""))
        / "years"
        / str(getattr(project, "year", ""))
        / "ennoscholar"
        / "state_of_art_payload"
    )


def _phase_paths(project: Any) -> Dict[str, Path]:
    base = _state_of_art_base(project)
    phase5_dir = base / "phase_5_state_of_art_writer"
    return {
        "base": base,
        "selection_payload": base / "selection_payload.json",
        "article_cards_payload": base / "article_cards" / "article_cards_payload.json",
        "consultant_plan_contract": base / "consultant_plan_contract.json",
        "guided_research_sources": base / "guided_research_sources.json",
        "phase3_fewshot_payload": base / "phase_3_style_memory" / "fewshot_payload.json",
        "phase3_style_profile_payload": base / "phase_3_style_memory" / "style_profile_payload.json",
        "phase3_style_signature_payload": base / "phase_3_style_memory" / "style_signature_v11.json",
        "phase3_argumentation_profile_payload": base / "phase_3_style_memory" / "argumentation_profile_payload.json",
        "phase4_gap_payload": base / "phase_4_scientific_gap" / "gap_scientific_payload.json",
        "phase45_scientific_reasoning_payload": base / "phase_4_5_scientific_reasoning" / "scientific_reasoning_payload.json",
        "phase46_project_argumentation_payload": base / "phase_4_6_project_rd_argumentation" / "project_rd_argumentation_payload.json",
        "phase47_scientific_narrative_payload": base / "phase_4_7_scientific_narrative" / "scientific_narrative_payload.json",
        "phase5_dir": phase5_dir,
        "phase5_payload": phase5_dir / "state_of_art_draft_payload.json",
        "phase5_markdown": phase5_dir / "state_of_art_draft.md",
        "phase5_deterministic_markdown": phase5_dir / "state_of_art_draft_deterministic.md",
        "phase5_llm_raw_markdown": phase5_dir / "state_of_art_draft_llm_raw.md",
        "phase5_hybrid_markdown": phase5_dir / "state_of_art_draft_hybrid_llm.md",
        "phase5_prompts_dir": phase5_dir / "prompts",
        "phase5_llm_outputs_dir": phase5_dir / "llm_outputs",
    }


def _path_dict(paths: Dict[str, Path]) -> Dict[str, str]:
    return {k: str(v) for k, v in paths.items() if isinstance(v, Path)}


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return list(value.values())
    if value is None:
        return []
    return [value]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _collect_output_files(paths: Dict[str, Path], max_files: int = 80) -> List[Dict[str, Any]]:
    roots = [paths.get("phase5_dir"), paths.get("phase5_prompts_dir"), paths.get("phase5_llm_outputs_dir")]
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        if not isinstance(root, Path) or not root.exists():
            continue
        try:
            files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".json", ".txt"}]
        except Exception:
            files = []
        for p in sorted(files, key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                st = p.stat()
                rel = str(p.relative_to(paths["phase5_dir"])) if paths.get("phase5_dir") and p.is_relative_to(paths["phase5_dir"]) else p.name
                items.append({
                    "name": p.name,
                    "relative_path": rel,
                    "path": str(p),
                    "size": st.st_size,
                    "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                    "kind": "markdown" if p.suffix.lower() == ".md" else "json" if p.suffix.lower() == ".json" else "text",
                })
            except Exception:
                items.append({"name": p.name, "path": str(p), "kind": p.suffix.lower().strip(".")})
            if len(items) >= max_files:
                return items
    return items


# ============================================================
# Normalisation payload Phase 5 unifié pour frontend
# ============================================================

def _normalize_legacy_draft_for_frontend(draft: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    """Compatibilité avec les anciens payloads contenant drafts[]."""
    if not isinstance(draft, dict):
        draft = {}
    draft_json = draft.get("draft_json") if isinstance(draft.get("draft_json"), dict) else draft
    sections = draft_json.get("sections") if isinstance(draft_json.get("sections"), dict) else {}
    method_chains = draft_json.get("method_evidence_chains") or draft.get("method_evidence_chains") or []
    if not isinstance(method_chains, list):
        method_chains = []
    polish = draft.get("polish") or {}
    return {
        "index": index,
        "ok": bool(draft.get("ok", draft_json.get("ok", False))),
        "verrou_id": str(draft.get("verrou_id") or draft_json.get("verrou_id") or ""),
        "verrou_title": draft.get("verrou_title") or draft_json.get("verrou_title") or f"Verrou {index + 1}",
        "draft_title": draft_json.get("draft_title") or f"État de l’art — Verrou {index + 1}",
        "sections": sections,
        "method_evidence_chains": method_chains,
        "method_evidence_chains_count": len(method_chains),
        "citations_used": draft_json.get("citations_used") or draft.get("citations_used") or [],
        "references_utilisees": draft_json.get("references_utilisees") or draft.get("references_utilisees") or [],
        "guard": draft.get("guard") or {},
        "polish": {
            "enabled": bool(polish.get("enabled")),
            "accepted": polish.get("accepted"),
            "reason": polish.get("reason"),
            "provider": polish.get("provider"),
            "guard": polish.get("guard") if isinstance(polish.get("guard"), dict) else {},
            "preservation": polish.get("preservation") if isinstance(polish.get("preservation"), dict) else {},
        },
        "phase47_blueprint_used": bool(draft.get("phase47_blueprint_used")),
        "phase47_payload_type": draft.get("phase47_payload_type"),
        "phase_4_6_argumentation_used": draft.get("phase_4_6_argumentation_used") or {},
        "methods_from_phase_4_5": draft.get("methods_from_phase_4_5") or [],
        "methods_from_phase_4_5_count": draft.get("methods_from_phase_4_5_count") or 0,
    }


def _normalize_v11_sections_for_frontend(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Adapte les sections V11 tout en conservant le contrat frontend historique."""
    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    out: List[Dict[str, Any]] = []
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            continue
        paragraphs = [
            {
                "paragraph_id": str(row.get("paragraph_id") or ""),
                "text": str(row.get("text") or ""),
                "claim_ids": [str(x) for x in _as_list(row.get("claim_ids"))],
            }
            for row in _as_list(section.get("paragraphs"))
            if isinstance(row, dict)
        ]
        evidence_map = [
            row for row in _as_list(section.get("sentence_evidence_map"))
            if isinstance(row, dict)
        ]
        validation = [
            row for row in _as_list(section.get("validation"))
            if isinstance(row, dict)
        ]
        citations = sorted({
            str(citation)
            for row in evidence_map
            for citation in _as_list(row.get("citations"))
            if str(citation).strip()
        })
        display_number = str(
            section.get("display_number")
            or section.get("section_number")
            or index
        )
        section_title = (
            section.get("title")
            or section.get("section_title")
            or section.get("verrou_title")
            or f"Section {display_number}"
        )
        out.append({
            "index": index,
            "display_number": display_number,
            "section_id": str(section.get("section_id") or f"S{index}"),
            "parent_id": section.get("parent_id"),
            "level": _safe_int(section.get("level"), 1),
            "section_mode": section.get("section_mode") or "scientific_evidence",
            "required_dimensions": [str(x) for x in _as_list(section.get("required_dimensions"))],
            "coverage_status": section.get("coverage") or section.get("coverage_status"),
            "search_provenance": section.get("search_provenance") or {},
            "ok": bool(section.get("ok")),
            "verrou_id": str(section.get("verrou_id") or ""),
            "verrou_title": section_title,
            "draft_title": section_title,
            "sections": {"paragraphs": paragraphs},
            "paragraphs": paragraphs,
            "paragraphs_count": len(paragraphs),
            "sentence_evidence_map": evidence_map,
            "validation": validation,
            "citations_used": citations,
            "references_utilisees": citations,
            "guard": {
                "passed": bool(section.get("ok")),
                "errors": section.get("errors") or [],
                "validation": validation,
                "style": section.get("section_style_report") or {},
            },
            "polish": {
                "enabled": section.get("mode") == "llm_verified",
                "accepted": bool(section.get("consultant_quality_ready")),
                "reason": section.get("mode"),
                "provider": None,
                "guard": {},
                "preservation": {},
            },
            "writer_mode": section.get("mode"),
            "consultant_quality_ready": bool(section.get("consultant_quality_ready")),
            "section_style_report": section.get("section_style_report") or {},
            "attempts": section.get("attempts") or [],
            "independent_verifier": section.get("independent_verifier") or {},
        })
    return out


def _normalize_verrou_coverage_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    guard = payload.get("guard") if isinstance(payload.get("guard"), dict) else {}
    rows = guard.get("per_verrou_coverage") if isinstance(guard.get("per_verrou_coverage"), list) else []
    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        required = [str(x) for x in _as_list(row.get("required_citations"))]
        detected = [str(x) for x in _as_list(row.get("detected_citations_in_verrou_section"))]
        missing = [str(x) for x in _as_list(row.get("missing_citations"))]
        out.append({
            "index": _safe_int(row.get("verrou_index"), idx),
            "ok": bool(row.get("coverage_ok")) and not missing,
            "verrou_id": str(row.get("verrou_id") or ""),
            "verrou_title": row.get("verrou_title") or f"Verrou {idx}",
            "required_citations": required,
            "detected_citations": detected,
            "missing_citations": missing,
            "required_count": len(required),
            "detected_count": len(detected),
            "guard": row,
        })
    return out


def _extract_unified_summary(payload: Dict[str, Any], markdown: str) -> Dict[str, Any]:
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    guard = payload.get("guard") if isinstance(payload.get("guard"), dict) else {}
    polish = payload.get("polish") if isinstance(payload.get("polish"), dict) else {}
    payload_type = str(payload.get("payload_type") or "")
    if payload_type == "state_of_art_draft_payload_canonical_global_v1":
        quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
        writer_mode = str(payload.get("writer_used") or quality.get("writer_mode") or "")
        llm_report = payload.get("llm") if isinstance(payload.get("llm"), dict) else {}
        llm_meta: Dict[str, Any] = {}
        for section in _as_list(llm_report.get("sections")):
            if not isinstance(section, dict):
                continue
            for attempt in _as_list(section.get("attempts")):
                if not isinstance(attempt, dict):
                    continue
                candidate = attempt.get("llm")
                if isinstance(candidate, dict) and (
                    candidate.get("model") or candidate.get("provider")
                ):
                    llm_meta = candidate
                    break
            if llm_meta:
                break
        detected = [
            str(value)
            for value in _as_list(guard.get("citations_detected"))
            if str(value).strip()
        ]
        required = [
            str(value)
            for value in _as_list(guard.get("coverage_required_citations"))
            if str(value).strip()
        ]
        consultant_ready = bool(quality.get("consultant_quality_ready"))
        llm_used = writer_mode.startswith("llm_")
        word_count = _safe_int(quality.get("word_count"), 0)
        section_count = _safe_int(stats.get("sections_count"), 0)
        verrou_count = _safe_int(stats.get("verrous_count"), 0)
        return {
            "phase5_ok": bool(payload.get("ok")),
            "status": payload.get("status"),
            "payload_type": payload_type,
            "markdown_chars": len(markdown or ""),
            "word_count": word_count,
            "words_count": word_count,
            "article_cards_count": _safe_int(stats.get("article_cards_count"), 0),
            "raw_evidence_units_count": _safe_int(stats.get("evidence_units_count"), 0),
            "selected_main_citations_count": _safe_int(
                stats.get("citations_used_count"),
                len(detected),
            ),
            "coverage_required_count": _safe_int(
                guard.get("coverage_required_count"),
                len(required),
            ),
            "citations_detected_count": _safe_int(
                guard.get("citations_detected_count"),
                len(detected),
            ),
            "missing_required_citations_count": len(
                _as_list(guard.get("missing_required_citations"))
            ),
            "unknown_citations_count": len(
                _as_list(guard.get("unknown_citations"))
            ),
            "verrous_written": verrou_count,
            "verrou_sections_count": verrou_count,
            "document_sections_count": section_count,
            "verrou_coverage_ok": bool(guard.get("verrou_coverage_ok")),
            "strict_ok": bool(guard.get("passed"))
            and not _as_list(guard.get("errors")),
            "single_unified_state_of_art": True,
            "llm_used": llm_used,
            "llm_used_in_final": llm_used,
            "llm_generated": llm_used,
            "llm_reason": quality.get("note"),
            "llm_provider": llm_meta.get("provider"),
            "llm_model": llm_meta.get("model"),
            "final_source": writer_mode,
            "accepted_sections": section_count if consultant_ready else 0,
            "rejected_sections": 0 if consultant_ready else section_count,
            "anti_copy_guard_enabled": True,
            "too_mechanical": False,
            "too_repetitive": False,
            "quality_score": _safe_int(
                quality.get("consultant_quality_score"),
                100 if consultant_ready else 0,
            ),
            "forbidden_counts": {},
            "consultant_quality_ready": consultant_ready,
            "unsupported_numeric_values": guard.get("unsupported_numeric_values") or [],
        }
    v11_sections = _normalize_v11_sections_for_frontend(payload)
    if "v11" in payload_type.lower() or v11_sections:
        quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
        evidence_rows = [
            row for row in _as_list(payload.get("sentence_evidence_map"))
            if isinstance(row, dict)
        ]
        detected_citations = sorted({
            str(citation)
            for row in evidence_rows
            for citation in _as_list(row.get("citations"))
            if str(citation).strip()
        })
        section_citations = {
            citation
            for section in v11_sections
            for citation in section.get("citations_used") or []
        }
        detected_citations = sorted(set(detected_citations) | section_citations)
        consultant_ready = bool(quality.get("consultant_quality_ready"))
        writer_mode = str(quality.get("writer_mode") or "")
        accepted_sections = sum(bool(row.get("consultant_quality_ready")) for row in v11_sections)
        return {
            "phase5_ok": bool(payload.get("ok")),
            "status": payload.get("status"),
            "payload_type": payload_type,
            "markdown_chars": len(markdown or ""),
            "article_cards_count": 0,
            "raw_evidence_units_count": len(evidence_rows),
            "selected_main_citations_count": len(detected_citations),
            "coverage_required_count": len(detected_citations),
            "citations_detected_count": len(detected_citations),
            "missing_required_citations_count": len(_as_list(guard.get("missing_claims_from_final_mapping"))),
            "unknown_citations_count": 0,
            "verrous_written": len(v11_sections),
            "verrou_sections_count": len(v11_sections),
            "verrou_coverage_ok": not bool(guard.get("section_failures")),
            "strict_ok": bool(guard.get("passed")) and not _as_list(guard.get("errors")),
            "single_unified_state_of_art": True,
            "llm_used": writer_mode == "llm",
            "llm_used_in_final": any(row.get("writer_mode") == "llm_verified" for row in v11_sections),
            "llm_generated": writer_mode == "llm",
            "llm_reason": quality.get("note"),
            "llm_provider": None,
            "llm_model": None,
            "final_source": "v11_verified_sections" if consultant_ready else "v11_not_consultant_ready",
            "accepted_sections": accepted_sections,
            "rejected_sections": max(0, len(v11_sections) - accepted_sections),
            "anti_copy_guard_enabled": True,
            "too_mechanical": any(
                "paragraphs_too_short_for_consultant_density"
                in _as_list((row.get("section_style_report") or {}).get("issues"))
                for row in v11_sections
            ),
            "too_repetitive": any(
                "article_catalogue_no_cross_article_synthesis"
                in _as_list((row.get("section_style_report") or {}).get("issues"))
                for row in v11_sections
            ),
            "quality_score": 100 if consultant_ready else 0,
            "forbidden_counts": {},
            "consultant_quality_ready": consultant_ready,
            "unsupported_numeric_values": guard.get("unsupported_numeric_values") or [],
        }
    style = guard.get("style_quality") if isinstance(guard.get("style_quality"), dict) else {}
    forbidden_counts = style.get("forbidden_counts") if isinstance(style.get("forbidden_counts"), dict) else {}
    blocks = polish.get("blocks") or polish.get("section_reports") or []
    if not isinstance(blocks, list):
        blocks = []
    accepted_blocks = [b for b in blocks if isinstance(b, dict) and b.get("accepted") is True]
    rejected_blocks = [b for b in blocks if isinstance(b, dict) and b.get("accepted") is False]
    required_count = _safe_int(stats.get("coverage_required_count") or guard.get("coverage_required_count"), 0)
    detected_count = _safe_int(stats.get("citations_detected_count") or guard.get("citations_detected_count"), 0)
    verrous_count = _safe_int(stats.get("verrou_sections_count") or len(_normalize_verrou_coverage_rows(payload)), 0)
    citations_total = _safe_int(stats.get("selected_main_citations_count") or stats.get("article_cards_count") or required_count, 0)
    return {
        "phase5_ok": bool(payload.get("ok")),
        "status": payload.get("status"),
        "payload_type": payload.get("payload_type"),
        "markdown_chars": len(markdown or ""),
        "article_cards_count": _safe_int(stats.get("article_cards_count"), 0),
        "raw_evidence_units_count": _safe_int(stats.get("raw_evidence_units_count"), 0),
        "selected_main_citations_count": citations_total,
        "coverage_required_count": required_count,
        "citations_detected_count": detected_count,
        "missing_required_citations_count": len(_as_list(guard.get("missing_required_citations"))),
        "unknown_citations_count": len(_as_list(guard.get("unknown_citations"))),
        "verrous_written": verrous_count,
        "verrou_sections_count": verrous_count,
        "verrou_coverage_ok": bool(stats.get("verrou_coverage_ok") or guard.get("verrou_coverage_ok")),
        "strict_ok": bool(guard.get("ok")) and not _as_list(guard.get("missing_required_citations")),
        "single_unified_state_of_art": bool(stats.get("single_unified_state_of_art", True)),
        "llm_used": bool(polish.get("llm_used_in_final") or polish.get("llm_generated") or polish.get("enabled")),
        "llm_used_in_final": bool(polish.get("llm_used_in_final")),
        "llm_generated": bool(polish.get("llm_generated") or polish.get("enabled")),
        "llm_reason": polish.get("reason"),
        "llm_provider": polish.get("provider") or (polish.get("llm_meta") or {}).get("provider"),
        "llm_model": polish.get("model") or polish.get("model_used") or (polish.get("llm_meta") or {}).get("model"),
        "final_source": polish.get("final_source"),
        "accepted_sections": polish.get("accepted_sections") or len(accepted_blocks),
        "rejected_sections": polish.get("rejected_sections") or len(rejected_blocks),
        "anti_copy_guard_enabled": bool(polish.get("anti_copy_guard_enabled")),
        "too_mechanical": bool(style.get("too_mechanical")),
        "too_repetitive": bool(style.get("too_repetitive")),
        "quality_score": style.get("quality_score"),
        "forbidden_counts": forbidden_counts,
    }


def _normalize_state_of_art_view(project: Any, phase5_payload: Dict[str, Any], markdown: str, paths: Dict[str, Path]) -> Dict[str, Any]:
    if not isinstance(phase5_payload, dict):
        phase5_payload = {}
    stats = phase5_payload.get("stats") if isinstance(phase5_payload.get("stats"), dict) else {}
    guard = phase5_payload.get("guard") if isinstance(phase5_payload.get("guard"), dict) else {}
    polish = phase5_payload.get("polish") if isinstance(phase5_payload.get("polish"), dict) else {}
    quality = phase5_payload.get("quality") if isinstance(phase5_payload.get("quality"), dict) else {}
    drafts = phase5_payload.get("drafts") if isinstance(phase5_payload.get("drafts"), list) else []
    v11_sections = _normalize_v11_sections_for_frontend(phase5_payload)
    canonical_global = (
        phase5_payload.get("payload_type")
        == "state_of_art_draft_payload_canonical_global_v1"
    )

    if canonical_global:
        verrous = _normalize_verrou_coverage_rows(phase5_payload)
    elif v11_sections:
        verrous = v11_sections
    elif drafts:
        verrous = [_normalize_legacy_draft_for_frontend(draft, idx) for idx, draft in enumerate(drafts)]
    else:
        verrous = _normalize_verrou_coverage_rows(phase5_payload)

    citations_detected = guard.get("citations_detected") or stats.get("selected_main_citations") or []
    if v11_sections:
        citations_detected = sorted({
            citation
            for section in v11_sections
            for citation in section.get("citations_used") or []
        })
    if not isinstance(citations_detected, list):
        citations_detected = []

    summary = _extract_unified_summary(phase5_payload, markdown)
    output_files = _collect_output_files(paths)

    return {
        "ok": bool(phase5_payload.get("ok")) and bool(markdown),
        "payload_type": phase5_payload.get("payload_type"),
        "generated_at": phase5_payload.get("generated_at") or phase5_payload.get("created_at"),
        "project": {
            "id": getattr(project, "id", None),
            "organisme": getattr(project, "organisme", None),
            "project_name": getattr(project, "project_name", None),
            "year": getattr(project, "year", None),
        },
        "summary": summary,
        "stats": stats,
        "guard": guard,
        "polish": polish,
        "llm": {
            "used": summary.get("llm_used"),
            "used_in_final": summary.get("llm_used_in_final"),
            "generated": summary.get("llm_generated"),
            "reason": summary.get("llm_reason"),
            "provider": summary.get("llm_provider"),
            "model": summary.get("llm_model"),
            "final_source": summary.get("final_source"),
            "accepted_sections": summary.get("accepted_sections"),
            "rejected_sections": summary.get("rejected_sections"),
        },
        "verrous": verrous,
        "citations": {
            "required": (
                guard.get("coverage_required_citations") or []
                if canonical_global
                else (
                    citations_detected
                    if v11_sections
                    else guard.get("coverage_required_citations") or stats.get("selected_main_citations") or []
                )
            ),
            "detected": citations_detected,
            "missing": (
                guard.get("missing_required_citations") or []
                if canonical_global
                else (
                    guard.get("missing_claims_from_final_mapping") or []
                    if v11_sections
                    else guard.get("missing_required_citations") or []
                )
            ),
            "unknown": guard.get("unknown_citations") or [],
        },
        "quality": (
            quality
            if canonical_global or v11_sections
            else guard.get("style_quality") or {}
        ),
        "sentence_evidence_map": phase5_payload.get("sentence_evidence_map") or [],
        "markdown": markdown,
        "paths": _path_dict(paths),
        "output_files": output_files,
        "raw_payload": phase5_payload,
    }


# ============================================================
# Lecture latest/history pour API frontend
# ============================================================

def read_latest_state_of_art(project: Any) -> Dict[str, Any]:
    paths = _phase_paths(project)
    payload = _read_json(paths["phase5_payload"], {})
    guard = payload.get("guard") if isinstance(payload.get("guard"), dict) else {}
    payload_valid = bool(payload) and bool(payload.get("ok", True)) and (
        not guard
        or bool(guard.get("passed", guard.get("ok", False)))
        and not _as_list(guard.get("errors"))
    )
    markdown = (
        _read_text(paths["phase5_markdown"])
        if payload_valid
        else ""
    )
    view = _normalize_state_of_art_view(project, payload, markdown, paths)
    return {
        "ok": payload_valid and bool(markdown),
        "report": view,
        "state_of_art_view": view,
        "markdown": markdown,
        "payload": payload,
        "paths": _path_dict(paths),
    }


def get_state_of_art_history(project: Any) -> Dict[str, Any]:
    latest = read_latest_state_of_art(project)
    report = latest.get("report") or {}
    if not latest.get("ok"):
        return {"ok": True, "reports": []}
    generated_at = report.get("generated_at") or datetime.now().isoformat(timespec="seconds")
    run_id = generated_at or "latest"
    summary = report.get("summary") or {}
    return {
        "ok": True,
        "reports": [
            {
                "run_id": run_id,
                "generated_at": generated_at,
                "updated_at": generated_at,
                "summary": summary,
                "report": report,
                "markdown": report.get("markdown") or "",
                "state_of_art_report_path": (latest.get("paths") or {}).get("phase5_markdown"),
                "selection_payload": _read_json(_phase_paths(project)["selection_payload"], {}),
            }
        ],
    }


# ============================================================
# Orchestrateur pipeline complet après sélection consultant
# ============================================================


def _phase5_consultant_failure(
    payload: Dict[str, Any],
    *,
    previous_available: bool,
) -> Dict[str, Any]:
    """Traduit un arrêt technique en état conversationnel actionnable."""
    status = str(payload.get("status") or "writing_not_published").strip()
    if status in {
        "writing_source_count_mismatch",
        "writing_source_selection_incomplete",
    }:
        message = (
            "La sélection exacte demandée contient des références qui ne sont "
            "pas encore toutes disponibles sous forme de texte intégral et "
            "d'Article Card. Je conserve le plan et les sources prêtes ; "
            "complétez les sources manquantes ou demandez d'utiliser toutes "
            "les sources actuellement validées."
        )
        next_action = "resolve_source_selection"
    elif status.startswith("partial_revision_"):
        message = str(payload.get("message") or (
            "La révision ciblée ne peut pas être appliquée à la version existante. "
            "Aucune rédaction globale n'a été lancée."
        ))
        next_action = "resolve_revision_target"
    elif status == "cost_budget_reached":
        message = (
            "J'ai arrêté la génération avant un nouvel appel payant "
            "parce que le plafond de coût configuré a été atteint. "
            "Les sections déjà validées et les checkpoints sont "
            "conservés. Vous pouvez reprendre sans recommencer les "
            "étapes terminées, ou augmenter explicitement le plafond."
        )
        next_action = "resume_or_raise_cost_limit"
    elif status == "insufficient_evidence":
        message = (
            "Le corpus ne contient pas encore assez de textes intégraux et "
            "d'Article Cards exploitables pour produire une rédaction sourcée. "
            "Les sources déjà prêtes sont conservées ; préparez les sources "
            "signalées comme indisponibles puis relancez."
        )
        next_action = "prepare_missing_sources"
    elif status in {
        "consultant_plan_required",
        "consultant_plan_not_approved",
        "consultant_plan_hash_mismatch",
        "plan_not_approved",
        "writing_not_authorized",
    }:
        message = (
            "Le plan de référence n'a pas d'approbation et d'autorisation de "
            "rédaction valides. Confirmez le plan actuel dans le chat, "
            "puis relancez sans recommencer la recherche."
        )
        next_action = "validate_plan"
    elif "verrou" in status or "contract" in status:
        # A contract mismatch does not establish that the consultant changed
        # their plan, and this branch does not resynchronize anything. Repeating
        # plan approval cannot repair inconsistent prepared lock identities.
        message = (
            "La rédaction est bloquée par une incohérence entre les données "
            "préparées et les verrous de référence. Votre plan et votre corpus "
            "sont conservés. Cette incohérence doit être corrigée ; revalider "
            "le même plan ne la résout pas."
        )
        next_action = "resolve_contract_inconsistency"
    else:
        message = (
            "Je n'ai pas publié cette tentative, car certaines parties doivent "
            "encore être mieux reliées aux publications validées. Votre corpus "
            "et votre plan sont conservés ; vous pouvez poursuivre directement "
            "dans le chat."
        )
        next_action = "review_evidence"

    if previous_available:
        message = "Je n'ai pas remplacé la version précédente. " + message
    return {
        "status": status,
        "assistant_message": message,
        "next_action": next_action,
    }



@budgeted_pipeline(run_type="ennoscholar_state_of_art")
def generate_state_of_art_after_consultant_selection(
    db: Session,
    project: Any,
    force_phase3: bool = True,
    force_article_cards: bool = False,
    **legacy_options: Any,
) -> Dict[str, Any]:
    """
    Pipeline final : lit Phase 1/2 existantes en lecture seule, puis exécute
    Phases 3/4/4.5/4.6/4.7 -> Phase 5.
    La sélection, le fulltext, l'OCR et les Article Cards ne sont jamais reconstruits ici.
    Le retour est directement exploitable par le frontend.
    """
    _ensure_root_on_path()

    # BEGIN ENNOSCHOLAR_CONVERSATION_SCOPE_V4
    guided_session_id = str(
        legacy_options.pop("guided_session_id", "") or ""
    ).strip() or None
    conversation_context = None
    paths = _phase_paths(project)

    if guided_session_id:
        from services.ennoscholar_conversation_state_service import prepare_conversation_run

        conversation_context = prepare_conversation_run(
            db,
            project,
            guided_session_id,
        )
        paths.update(conversation_context.get("path_overrides") or {})
    # END ENNOSCHOLAR_CONVERSATION_SCOPE_V4

    base = paths["base"]
    if legacy_options:
        print(
            "[EnnoScholar][SOA][WARN] Options LLM runtime ignorées : "
            + ", ".join(sorted(str(key) for key in legacy_options))
            + ". La configuration LLM vient uniquement du client central et du .env."
        )
    print("=" * 90)
    print(f"[EnnoScholar][SOA] START project_id={getattr(project, 'id', None)}")
    print(f"[EnnoScholar][SOA] base={base}")

    # 1/2. Entrées scientifiques déjà préparées — LECTURE SEULE.
    # Cette fonction appartient à la dernière section « État de l’art rédigé ».
    # Elle ne doit jamais refaire la sélection, le fulltext, l'OCR ou les Article Cards.
    print("[EnnoScholar][SOA] Phase 1/2 READ-ONLY START existing artifacts")
    if force_article_cards:
        print(
            "[EnnoScholar][SOA][WARN] force_article_cards ignoré dans la génération finale. "
            "La reconstruction appartient à l'étape Préparation état de l'art."
        )

    # Le workflow 1 exige toujours son artefact Phase 1 canonique. Une
    # conversation guidée autonome possède en revanche un verrou et une vue de corpus
    # projet persistante : son handoff Phase 1 est matérialisé plus bas dans son runtime.
    if not guided_session_id and not paths["selection_payload"].exists():
        raise RuntimeError(
            "Phase 1 existante introuvable. Retourne dans Préparation état de l'art : "
            f"{paths['selection_payload']}"
        )
    if guided_session_id and conversation_context:
        from services.ennoscholar_project_corpus_service import (
            get_conversation_corpus_cards_payload,
            get_project_corpus_cards_payload,
        )

        guided_snapshot = dict(conversation_context.get("snapshot") or {})
        guided_context = dict(guided_snapshot.get("context") or {})
        active_verrou_ids = (
            list(guided_context.get("active_verrou_ids") or [])
            if str(guided_context.get("review_scope") or "") == "per_verrou"
            else []
        )
        standalone_chat = (
            str(guided_context.get("operating_mode") or "").strip().casefold()
            == "standalone_chat"
        )
        if standalone_chat:
            article_cards_payload = get_conversation_corpus_cards_payload(
                db,
                project,
                session_id=guided_session_id,
                corpus_scope_id=str(
                    guided_context.get("corpus_scope_id") or guided_session_id
                ),
                active_verrou_ids=active_verrou_ids,
            )
        else:
            article_cards_payload = get_project_corpus_cards_payload(
                db,
                project,
                active_verrou_ids=active_verrou_ids,
            )
    else:
        from services.article_card_builder import get_article_cards_payload

        article_cards_payload = get_article_cards_payload(
            project,
            db=db,
            scope_id=None,
            scholar_run_id=None,
        )

    # BEGIN ENNOSCHOLAR_VERROU_SCOPE_LOCK_V4
    if guided_session_id and conversation_context:
        from services.ennoscholar_conversation_state_service import (
            build_conversation_phase1_payload,
            materialize_scoped_runtime,
        )

        original_selection_payload = _read_json(
            _state_of_art_base(project) / "selection_payload.json",
            {},
        )
        snapshot_context = dict(
            (conversation_context.get("snapshot") or {}).get("context") or {}
        )
        standalone_chat = (
            str(snapshot_context.get("operating_mode") or "").strip().casefold()
            == "standalone_chat"
        )
        if (
            standalone_chat
            or not isinstance(original_selection_payload, dict)
            or not list(original_selection_payload.get("verrous") or [])
        ):
            original_selection_payload = build_conversation_phase1_payload(
                project=project,
                conversation_context=conversation_context,
                article_cards_payload=article_cards_payload,
            )
            print(
                "[EnnoScholar][SOA] Phase 1 SESSION HANDOFF materialized "
                f"session_id={guided_session_id} "
                f"verrous={len(original_selection_payload.get('verrous') or [])} "
                f"articles={len(original_selection_payload.get('selected_articles') or [])}"
            )
        scoped_runtime = materialize_scoped_runtime(
            conversation_context=conversation_context,
            selection_payload=original_selection_payload,
            article_cards_payload=article_cards_payload,
        )
        article_cards_payload = scoped_runtime["article_cards_payload"]
        paths["selection_payload"] = scoped_runtime["selection_path"]
        paths["article_cards_payload"] = scoped_runtime["article_cards_path"]

        scope_manifest = scoped_runtime.get("scope_manifest") or {}
        if (
            scope_manifest.get("active")
            and int(scope_manifest.get("kept_cards_count") or 0) <= 0
        ):
            raise RuntimeError(
                "verrou_scope_no_article: aucun Article Card rattaché "
                "au verrou demandé dans cette conversation."
            )
    else:
        scoped_runtime = None
    # END ENNOSCHOLAR_VERROU_SCOPE_LOCK_V4

    if not isinstance(article_cards_payload, dict) or not article_cards_payload.get("cards"):
        raise RuntimeError(
            "Article Cards existantes introuvables en base. "
            "Conserve au moins un article avec texte intégral avant la rédaction."
        )
    article_cards_db_uri = str(
        article_cards_payload.get("payload_path")
        or f"db://projects/{int(project.id)}/article_cards_payload"
    )
    if guided_session_id and conversation_context:
        runtime_cards_path = Path(paths["article_cards_payload"])
    else:
        runtime_dir = Path(tempfile.gettempdir()) / "ennosmart_runtime_article_cards"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        runtime_cards_path = runtime_dir / f"project_{int(project.id)}_article_cards_payload.json"
        runtime_cards_path.write_text(
            json.dumps(article_cards_payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths["article_cards_payload"] = runtime_cards_path

    readonly_fingerprints_before = {
        "selection_sha256": _sha256(paths["selection_payload"]),
        "article_cards_sha256": _sha256(paths["article_cards_payload"]),
    }
    selection_payload = _read_json(paths["selection_payload"], {})
    if not isinstance(selection_payload, dict) or not selection_payload:
        raise RuntimeError("selection_payload.json existant est vide ou invalide.")
    if not isinstance(article_cards_payload, dict) or not article_cards_payload:
        raise RuntimeError("article_cards_payload.json existant est vide ou invalide.")

    cards = article_cards_payload.get("cards") or article_cards_payload.get("article_cards") or []
    if not isinstance(cards, list):
        cards = []
    if not cards:
        raise RuntimeError(
            "Aucune Article Card existante. Termine la préparation des articles avant la rédaction."
        )

    invalid_for_writing: List[Dict[str, Any]] = []
    for card in cards:
        ready, reason = is_article_card_ready_for_writing(card)
        if not ready:
            invalid_for_writing.append({
                "article_id": card.get("article_id") if isinstance(card, dict) else None,
                "citation_id": card.get("citation_id") if isinstance(card, dict) else None,
                "reason": reason,
            })
    if invalid_for_writing:
        raise RuntimeError(
            "Article Cards existantes non prêtes pour la rédaction : "
            f"{invalid_for_writing[:10]}"
        )

    selection_changed = False
    artifact_sync = {
        "ok": True,
        "mode": "read_only_existing",
        "selection_changed": False,
        "removed_article_ids": [],
        "added_article_ids": [],
    }
    selected_articles_count = int(
        selection_payload.get("selected_articles_count")
        or selection_payload.get("articles_count")
        or article_cards_payload.get("selected_articles_count")
        or len(cards)
    )
    writing_ready_cards_count = len(cards)
    excluded_from_writing_count = int(
        article_cards_payload.get("excluded_from_writing_count")
        or max(0, selected_articles_count - writing_ready_cards_count)
    )
    writing_eligibility = {
        "selected_articles_count": selected_articles_count,
        "writing_ready_cards_count": writing_ready_cards_count,
        "excluded_from_writing_count": excluded_from_writing_count,
        "writing_ready_article_ids": article_cards_payload.get("writing_ready_article_ids") or [],
        "excluded_article_ids": article_cards_payload.get("excluded_article_ids") or [],
        "rule": "existing_verified_article_cards_only",
        "all_cards_verified": True,
        "phase1_rebuilt": False,
        "phase2_rebuilt": False,
        "external_research_started": False,
        "fulltext_processing_started": False,
        "input_fingerprints": readonly_fingerprints_before,
    }
    fulltext_resolution = {
        "ok": True,
        "mode": "not_reexecuted_existing_cards_only",
        "text_extracted_count": writing_ready_cards_count,
    }
    direct_extraction = dict(fulltext_resolution)
    print(
        "[EnnoScholar][SOA] Phase 1/2 READ-ONLY OK "
        f"selected={selected_articles_count} cards={writing_ready_cards_count}"
    )

    # 3. Phase 3 — style memory / few-shot CIR
    # IMPORTANT : la vraie Phase 3 est conservée dans :
    # modules.CIR_STYLE_MEMORY.cir_style_fewshot.phase_3_style_fewshot_service
    print("[EnnoScholar][SOA] Phase 3 START style_memory_fewshot")
    from modules.CIR_STYLE_MEMORY.cir_style_fewshot.phase_3_style_fewshot_service import (
        build_phase_3_style_memory,
    )

    phase3_result = build_phase_3_style_memory(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        phase1_payload=selection_payload if isinstance(selection_payload, dict) else None,
        phase1_payload_path=paths["selection_payload"],
        force=force_phase3,
        # Un nouveau projet lancé depuis le chat peut ne disposer d'aucun
        # historique rédactionnel. Dans ce seul cas, la Phase 3 adopte les
        # templates scientifiques neutres au lieu de bloquer les Phases 4/5.
        # Le workflow 1 conserve le comportement strict historique.
        allow_empty_style_memory=bool(guided_session_id),
    )
    if not isinstance(phase3_result, dict) or not phase3_result.get("ok"):
        raise RuntimeError(f"Phase 3 échouée : {phase3_result}")
    print("[EnnoScholar][SOA] Phase 3 OK")

    # 3B. Signature stylistique V11 — métriques et mouvements rhétoriques
    # uniquement. Les exemples Phase 3 ne deviennent jamais des preuves.
    print("[EnnoScholar][SOA] Phase 3B START consultant_style_signature")
    from agents.EnnoScholar.state_of_art.phase_3_style_signature_service import (
        run_phase_3_style_signature,
    )

    phase3_style_signature_result = run_phase_3_style_signature(
        fewshot_payload_path=paths["phase3_fewshot_payload"],
        output_path=paths["phase3_style_signature_payload"],
    )
    if (
        not isinstance(phase3_style_signature_result, dict)
        or not phase3_style_signature_result.get("ok")
        or not paths["phase3_style_signature_payload"].exists()
    ):
        raise RuntimeError(
            "Phase 3B échouée : signature stylistique V11 absente ou invalide : "
            f"{phase3_style_signature_result}"
        )
    print(
        "[EnnoScholar][SOA] Phase 3B OK "
        f"path={paths['phase3_style_signature_payload']}"
    )

    # 4. Phase 4 — gap scientifique
    # IMPORTANT :
    # On ne modifie pas les fichiers agents.EnnoScholar.state_of_art.*.
    # Le backend appelle les vrais noms de fonctions exposés par tes services.
    print("[EnnoScholar][SOA] Phase 4 START scientific_gap")
    from agents.EnnoScholar.state_of_art.phase_4_scientific_gap_service import (
        build_scientific_gap_payload,
    )

    phase4_result = build_scientific_gap_payload(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        selection_payload_path=paths["selection_payload"],
        article_cards_payload_path=paths["article_cards_payload"],
        fewshot_payload_path=paths["phase3_fewshot_payload"],
        output_path=paths["phase4_gap_payload"],
    )
    if not isinstance(phase4_result, dict) or not phase4_result.get("ok"):
        raise RuntimeError(f"Phase 4 échouée : {phase4_result}")
    if not paths["phase4_gap_payload"].exists():
        raise RuntimeError(f"Phase 4 échouée : gap_scientific_payload.json introuvable : {paths['phase4_gap_payload']}")
    print("[EnnoScholar][SOA] Phase 4 OK")

    # 5. Phase 4.5 — scientific reasoning
    print("[EnnoScholar][SOA] Phase 4.5 START scientific_reasoning")

    from agents.EnnoScholar.state_of_art.phase_4_5_scientific_reasoning_builder_service import (
        run_phase_4_5_scientific_reasoning,
    )

    phase45_result = run_phase_4_5_scientific_reasoning(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        gap_payload_path=paths["phase4_gap_payload"],
        article_cards_payload_path=paths["article_cards_payload"],
        argumentation_payload_path=paths["phase3_argumentation_profile_payload"],
        output_path=paths["phase45_scientific_reasoning_payload"],
    )
    if not isinstance(phase45_result, dict) or not phase45_result.get("ok"):
        raise RuntimeError(f"Phase 4.5 échouée : {phase45_result}")
    if not paths["phase45_scientific_reasoning_payload"].exists():
        raise RuntimeError(f"Phase 4.5 échouée : scientific_reasoning_payload.json introuvable : {paths['phase45_scientific_reasoning_payload']}")
    print("[EnnoScholar][SOA] Phase 4.5 OK")

    # 6. Phase 4.6 — argumentation projet R&D
    print("[EnnoScholar][SOA] Phase 4.6 START project_rd_argumentation")
    from agents.EnnoScholar.state_of_art.phase_4_6_project_rd_argumentation_service import (
        run_phase_4_6_project_rd_argumentation,
    )

    phase46_result = run_phase_4_6_project_rd_argumentation(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        selection_payload_path=str(paths["selection_payload"]),
        article_cards_payload_path=str(paths["article_cards_payload"]),
        fewshot_payload_path=str(paths["phase3_fewshot_payload"]),
        scientific_gap_payload_path=str(paths["phase4_gap_payload"]),
        scientific_reasoning_payload_path=str(paths["phase45_scientific_reasoning_payload"]),
        output_path=str(paths["phase46_project_argumentation_payload"]),
        markdown_output_path=str(
            paths["phase46_project_argumentation_payload"].with_name(
                "project_rd_argumentation_summary.md"
            )
        ),
        use_llm=False,
        dry_run=False,
    )
    print(
        "[EnnoScholar][SOA] Phase 4.6 OUTPUT "
        f"path={phase46_result.get('output_path') if isinstance(phase46_result, dict) else None}"
    )
    if not isinstance(phase46_result, dict):
        raise RuntimeError(
            f"Phase 4.6 échouée : réponse invalide de type {type(phase46_result).__name__}"
        )

    if not paths["phase46_project_argumentation_payload"].exists():
        raise RuntimeError(
            "Phase 4.6 échouée : project_rd_argumentation_payload.json introuvable : "
            f"{paths['phase46_project_argumentation_payload']}"
        )

    phase46_argumentations = [
        item
        for item in (phase46_result.get("argumentations") or [])
        if isinstance(item, dict)
    ]

    if not phase46_argumentations:
        raise RuntimeError(
            "Phase 4.6 échouée : aucune argumentation exploitable n'a été produite. "
            f"Détail : {phase46_result.get('error') or 'aucun verrou/reasoning item'}"
        )

    phase46_failed = [
        {
            "verrou_id": item.get("verrou_id"),
            "verrou_title": item.get("verrou_title"),
            "missing_citations": (item.get("guard") or {}).get("missing_citations") or [],
            "missing_sections": (item.get("guard") or {}).get("missing_sections") or [],
            "article_list_style": ((item.get("guard") or {}).get("article_list_style") or {}).get("detected"),
            "project_first_score": (item.get("guard") or {}).get("project_first_score"),
        }
        for item in phase46_argumentations
        if not item.get("ok")
    ]

    if not phase46_result.get("ok") or phase46_failed:
        raise RuntimeError(
            "Phase 4.6 bloquée par les garde-fous V11 : "
            f"global_guard={json.dumps(phase46_result.get('guard') or {}, ensure_ascii=False)} "
            f"failed={json.dumps(phase46_failed, ensure_ascii=False)}"
        )
    print("[EnnoScholar][SOA] Phase 4.6 OK")

    # 7. Phase 4.7 — narrative
    print("[EnnoScholar][SOA] Phase 4.7 START scientific_narrative")
    from agents.EnnoScholar.state_of_art.phase_4_7_scientific_narrative_builder import (
        build_scientific_narrative_payload,
    )

    phase47_result = build_scientific_narrative_payload(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        phase_4_5_path=str(paths["phase45_scientific_reasoning_payload"]),
        phase_4_6_path=str(paths["phase46_project_argumentation_payload"]),
        # Le payload de sélection est le contrat canonique en lecture seule
        # des verrous pour cette exécution. Il remplace l'ancien
        # confirmed_verrous.json lorsqu'il n'existe pas.
        confirmed_verrous_path=str(paths["selection_payload"]),
        consultant_plan_path=str(paths["consultant_plan_contract"]),
        output_path=str(paths["phase47_scientific_narrative_payload"]),
        markdown_output_path=str(
            paths["phase47_scientific_narrative_payload"].with_name(
                "scientific_narrative_summary.md"
            )
        ),
        dry_run=False,
    )

    print(
        "[EnnoScholar][SOA] Phase 4.7 OUTPUT "
        f"path={phase47_result.get('output_path') if isinstance(phase47_result, dict) else None}"
    )

    if not isinstance(phase47_result, dict):
        raise RuntimeError(
            f"Phase 4.7 échouée : réponse invalide de type {type(phase47_result).__name__}"
        )

    if not paths["phase47_scientific_narrative_payload"].exists():
        raise RuntimeError(
            "Phase 4.7 échouée : scientific_narrative_payload.json introuvable : "
            f"{paths['phase47_scientific_narrative_payload']}"
        )

    phase47_has_content = bool(
        phase47_result.get("verrous_count")
        or phase47_result.get("verrou_index")
        or phase47_result.get("verrou_sections_for_phase5")
        or phase47_result.get("project_specific_method_story_units")
        or phase47_result.get("global_writer_blueprint")
        or phase47_result.get("per_verrou_writer_blueprints")
    )

    if not phase47_has_content:
        raise RuntimeError(
            "Phase 4.7 échouée : payload créé mais aucune structure narrative "
            f"exploitable. Détail : {phase47_result.get('error') or phase47_result.get('quality')}"
        )

    if not phase47_result.get("ok"):
        raise RuntimeError(
            "Phase 4.7 bloquée par les garde-fous V11 : "
            f"guard={json.dumps(phase47_result.get('guard') or {}, ensure_ascii=False)}"
        )
    print("[EnnoScholar][SOA] Phase 4.7 OK")

    # 8. Phase 5 — writer avec LLM optionnel
    print("[EnnoScholar][SOA] Phase 5 START state_of_art_writer")
    from agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import (
        run_phase_5_state_of_art_writer,
    )

    phase5_result = run_phase_5_state_of_art_writer(
        organisme=project.organisme,
        project=project.project_name,
        year=str(project.year),
        selection_payload_path=paths["selection_payload"],
        article_cards_payload_path=paths["article_cards_payload"],
        fewshot_payload_path=paths["phase3_fewshot_payload"],
        style_profile_payload_path=paths["phase3_style_signature_payload"],
        argumentation_profile_payload_path=paths["phase3_argumentation_profile_payload"],
        scientific_reasoning_payload_path=paths["phase45_scientific_reasoning_payload"],
        phase46_project_argumentation_payload_path=paths["phase46_project_argumentation_payload"],
        phase47_scientific_narrative_payload_path=paths["phase47_scientific_narrative_payload"],
        consultant_plan_contract_path=paths["consultant_plan_contract"],
        guided_research_sources_path=paths["guided_research_sources"],
        output_path=paths["phase5_payload"],
        markdown_output_path=paths["phase5_markdown"],
        guided_conversation=bool(guided_session_id),
        dry_run=False,
    )
    if not isinstance(phase5_result, dict):
        raise RuntimeError(
            f"Phase 5 échouée : réponse invalide de type {type(phase5_result).__name__}"
        )
    if phase5_result.get("ok") and not paths["phase5_payload"].exists():
        raise RuntimeError(f"Phase 5 échouée : state_of_art_draft_payload.json introuvable : {paths['phase5_payload']}")

    phase5_payload = (
        phase5_result
        if not phase5_result.get("ok")
        else _read_json(paths["phase5_payload"], phase5_result)
    )
    phase5_guard = phase5_payload.get("guard") if isinstance(phase5_payload.get("guard"), dict) else {}
    phase5_quality = phase5_payload.get("quality") if isinstance(phase5_payload.get("quality"), dict) else {}
    if (
        not phase5_result.get("ok")
        or not phase5_payload.get("ok")
        or not phase5_guard.get("passed")
        or phase5_guard.get("errors")
    ):
        # Le contrôle reste strict, mais son vocabulaire technique ne remonte
        # jamais dans le chat consultant. La dernière version valide, si elle
        # existe, reste inchangée et la tentative est conservée séparément pour
        # diagnostic développeur.
        print(
            "[EnnoScholar][SOA][INTERNAL] Draft non publié "
            f"status={phase5_payload.get('status') or phase5_result.get('status')} "
            f"message={phase5_payload.get('message') or phase5_result.get('message') or ''} "
            f"guard={json.dumps(phase5_guard, ensure_ascii=False)} "
            f"rejected={phase5_payload.get('rejected_markdown_output_path') or ''}"
        )
        if guided_session_id and conversation_context:
            previous_draft = dict(
                (conversation_context.get("snapshot") or {}).get("draft")
                or {}
            )
            previous_available = bool(
                str(previous_draft.get("markdown") or "").strip()
            )
        else:
            previous = read_latest_state_of_art(project)
            previous_available = bool(previous.get("ok"))
        public_failure = _phase5_consultant_failure(
            phase5_payload,
            previous_available=previous_available,
        )
        return {
            "ok": False,
            "status": public_failure["status"],
            "assistant_message": public_failure["assistant_message"],
            "next_action": public_failure["next_action"],
            "previous_draft_preserved": previous_available,
            "retryable": True,
            "guided_session_id": guided_session_id,
            "failure_phase": "phase_5_state_of_art_writer",
            "internal_status": (
                phase5_payload.get("status")
                or phase5_result.get("status")
            ),
            "project": {
                "id": project.id,
                "organisme": project.organisme,
                "project_name": project.project_name,
                "year": project.year,
            },
        }
    # Une profondeur ou un style perfectible reste une information éditoriale,
    # pas une raison de masquer un document dont les affirmations et citations
    # ont passé les contrôles scientifiques. Le consultant peut ensuite demander
    # un approfondissement ciblé depuis le chat.
    markdown = _read_text(paths["phase5_markdown"])
    if not markdown.strip():
        raise RuntimeError("Phase 5 échouée : state_of_art_draft.md est vide.")

    # BEGIN ENNOSCHOLAR_CIR_EDITORIAL_VALIDATOR_V4
    editorial_result = None
    state_of_art_version = None

    if guided_session_id and conversation_context:
        from agents.EnnoScholar.state_of_art.cir_editorial_validator_service import (
            run_cir_editorial_validation,
        )
        from services.ennoscholar_conversation_state_service import (
            archive_conversation_state_of_art,
        )

        evidence_payload = _read_json(
            paths["phase5_dir"] / "normalized_evidence_units.json",
            {},
        )
        evidence_units = (
            evidence_payload.get("items")
            if isinstance(evidence_payload, dict)
            else []
        ) or []

        plan_contract_runtime = _read_json(
            paths["consultant_plan_contract"],
            {},
        )

        editorial_result = run_cir_editorial_validation(
            phase5_payload=phase5_payload,
            plan_contract=plan_contract_runtime,
            article_cards_payload=article_cards_payload,
            evidence_units=evidence_units,
        )

        editorial_report_path = (
            paths["phase5_dir"] / "cir_editorial_report_v4.json"
        )
        editorial_report_path.parent.mkdir(parents=True, exist_ok=True)
        editorial_report_path.write_text(
            json.dumps(
                editorial_result.get("report") or {},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        # Dans le workflow conversationnel, le rapport éditorial est un
        # diagnostic interne. Le nouveau brouillon Phase 5 est publié tel qu'il
        # a été généré : il n'est ni bloqué, ni remplacé par une version nettoyée
        # ou sélectionnée par les contrôleurs. Le consultant peut ensuite le
        # faire évoluer naturellement dans le chat.
        phase5_payload = dict(phase5_payload)
        phase5_payload["editorial_diagnostic"] = {
            "internal_only": True,
            "ok": bool(editorial_result.get("ok")),
            "issues_count": len(
                (editorial_result.get("report") or {}).get("blocking_issues")
                or []
            ),
            "publication_policy": "guided_iterative_new_draft_as_generated",
        }

        paths["phase5_payload"].write_text(
            json.dumps(
                phase5_payload,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        paths["phase5_markdown"].write_text(markdown, encoding="utf-8")

        scope_manifest = (
            scoped_runtime.get("scope_manifest")
            if isinstance(scoped_runtime, dict)
            else {}
        ) or {}

        state_of_art_version = archive_conversation_state_of_art(
            project=project,
            session_id=guided_session_id,
            markdown=markdown,
            payload=phase5_payload,
            editorial_report=editorial_result.get("report") or {},
            scope_manifest=scope_manifest,
        )
    # END ENNOSCHOLAR_CIR_EDITORIAL_VALIDATOR_V4
    readonly_fingerprints_after = {
        "selection_sha256": _sha256(paths["selection_payload"]),
        "article_cards_sha256": _sha256(paths["article_cards_payload"]),
    }
    if readonly_fingerprints_after != readonly_fingerprints_before:
        raise RuntimeError(
            "Violation du mode lecture seule : selection_payload.json ou article_cards_payload.json "
            "a été modifié pendant les Phases 3→5."
        )
    state_of_art_view = _normalize_state_of_art_view(project, phase5_payload, markdown, paths)

    print(f"[EnnoScholar][SOA] Phase 5 OK={phase5_result.get('ok')} path={paths['phase5_markdown']}")
    print("[EnnoScholar][SOA] END")
    print("=" * 90)

    summary = state_of_art_view.get("summary") or {}
    if not guided_session_id:
        runtime_cards_path.unlink(missing_ok=True)
    return {
        "ok": bool(phase5_payload.get("ok")) and bool(markdown),
        "project": {
            "id": project.id,
            "organisme": project.organisme,
            "project_name": project.project_name,
            "year": project.year,
        },
        "status": {
            "selection_ok": bool(selection_payload),
            "selection_changed": selection_changed,
            "selection_artifacts_synced": bool(artifact_sync.get("ok")),
            "phase1_rebuilt": False,
            "phase2_rebuilt": False,
            "phase1_phase2_read_only": True,
            "external_research_started": False,
            "fulltext_resolution_ok": bool(fulltext_resolution.get("ok", True)),
            "direct_extraction_ok": bool(direct_extraction.get("ok", True)),
            "article_cards_ok": bool(article_cards_payload.get("ok", True)),
            "selected_articles_count": writing_eligibility.get("selected_articles_count"),
            "writing_ready_articles_count": writing_eligibility.get("writing_ready_cards_count"),
            "excluded_from_writing_count": writing_eligibility.get("excluded_from_writing_count"),
            "phase3_ok": phase3_result.get("ok"),
            "phase3_style_signature_ok": phase3_style_signature_result.get("ok"),
            "phase4_ok": phase4_result.get("ok"),
            "phase45_ok": phase45_result.get("ok"),
            "phase46_ok": phase46_result.get("ok"),
            "phase47_ok": phase47_result.get("ok"),
            "phase5_ok": phase5_payload.get("ok"),
            "strict_ok": summary.get("strict_ok"),
            "coverage_required_count": summary.get("coverage_required_count"),
            "citations_detected_count": summary.get("citations_detected_count"),
            "verrou_coverage_ok": summary.get("verrou_coverage_ok"),
            "llm_used_in_final": summary.get("llm_used_in_final"),
            "consultant_quality_ready": summary.get("consultant_quality_ready"),
            "unsupported_numeric_values": summary.get("unsupported_numeric_values") or [],
        },
        "writing_eligibility": writing_eligibility,
        "readonly_fingerprints_before": readonly_fingerprints_before,
        "readonly_fingerprints_after": readonly_fingerprints_after,
        "markdown": markdown,
        "state_of_art_view": state_of_art_view,
        "report": state_of_art_view,
        "guided_session_id": guided_session_id,
        "state_of_art_version": state_of_art_version,
        "editorial_validation": (
            editorial_result.get("report")
            if isinstance(editorial_result, dict)
            else None
        ),
        "scope_lock": (
            scoped_runtime.get("scope_manifest")
            if isinstance(scoped_runtime, dict)
            else None
        ),
        "paths": {
            "selection_payload": str(paths["selection_payload"]),
            "article_cards_payload": article_cards_db_uri,
            "phase3_style_signature_payload": str(paths["phase3_style_signature_payload"]),
            "phase4_gap_payload": str(paths["phase4_gap_payload"]),
            "phase45_scientific_reasoning_payload": str(paths["phase45_scientific_reasoning_payload"]),
            "phase46_project_argumentation_payload": str(paths["phase46_project_argumentation_payload"]),
            "phase47_scientific_narrative_payload": str(paths["phase47_scientific_narrative_payload"]),
            "state_of_art_markdown": str(paths["phase5_markdown"]),
            "state_of_art_payload": str(paths["phase5_payload"]),
            "sentence_evidence_map": str(paths["phase5_dir"] / "sentence_evidence_map.json"),
            "quality_report_v11": str(paths["phase5_dir"] / "quality_report_v11.json"),
        },
        "phase_results": {
            "selection": selection_payload,
            "artifact_sync": artifact_sync,
            "fulltext_resolution": fulltext_resolution,
            "direct_extraction": direct_extraction,
            "article_cards": article_cards_payload,
            "writing_eligibility": writing_eligibility,
            "phase3": phase3_result,
            "phase3_style_signature": phase3_style_signature_result,
            "phase4": phase4_result,
            "phase45": phase45_result,
            "phase46": phase46_result,
            "phase47": phase47_result,
            "phase5": phase5_result,
            "phase5_payload_type": phase5_payload.get("payload_type") or phase5_result.get("payload_type"),
        },
    }
