# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any, Dict, List

from db.database import SessionLocal
from db.models import DiagnosticRun, Verrou


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _to_float(value: Any):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _extract_report(raw_result_json: Any) -> Dict[str, Any]:
    raw = _as_dict(raw_result_json)
    if not raw:
        return {}

    pipeline = _as_dict(raw.get("script_or_pipeline_result"))
    report = _as_dict(pipeline.get("report"))
    if report:
        return report

    report = _as_dict(raw.get("report"))
    if report:
        return report

    if raw.get("verrou_synthesis_report") or raw.get("llm_reformulated_verrous"):
        return raw

    return {}


def _extract_final_items(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    verrou_report = _as_dict(report.get("verrou_synthesis_report"))

    candidates = (
        verrou_report.get("llm_reformulated_verrous")
        or verrou_report.get("final_items")
        or verrou_report.get("final_verrous")
        or report.get("llm_reformulated_verrous")
        or report.get("consultant_verrous_cir")
        or report.get("verrous_reformules")
        or report.get("verrous")
        or []
    )

    if not isinstance(candidates, list):
        return []

    final: List[Dict[str, Any]] = []
    seen = set()

    for item in candidates:
        if not isinstance(item, dict):
            continue

        title = _clean(
            item.get("title")
            or item.get("titre")
            or item.get("verrou")
            or item.get("name")
        )

        if not title:
            continue

        key = title.lower()
        if key in seen:
            continue

        seen.add(key)

        justification = _clean(
            item.get("consultant_explanation")
            or item.get("why_agent_found_verrou")
            or item.get("justification")
            or item.get("text")
            or item.get("scientific_lock")
        )

        normalized = dict(item)
        normalized["title"] = title
        normalized["titre"] = title
        normalized["verrou"] = title
        normalized["justification"] = justification
        normalized["text"] = justification
        normalized["consultant_status"] = _clean(item.get("consultant_status")) or "en_attente"
        normalized["needs_human_validation"] = True
        normalized["source"] = _clean(item.get("source")) or "llm_reformulated_verrous_final"

        final.append(normalized)

    return final


def _set_if_column(obj: Any, columns: set[str], name: str, value: Any) -> None:
    if name in columns:
        setattr(obj, name, value)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python force_resync_final_verrous_v133.py <PROJECT_ID>")
        return 2

    project_id = int(sys.argv[1])
    db = SessionLocal()

    try:
        latest_run = (
            db.query(DiagnosticRun)
            .filter(DiagnosticRun.project_id == project_id)
            .order_by(DiagnosticRun.created_at.desc())
            .first()
        )

        if not latest_run:
            print(f"ERREUR: aucun DiagnosticRun trouvé pour project_id={project_id}")
            return 1

        report = _extract_report(latest_run.raw_result_json)
        items = _extract_final_items(report)

        verrou_report = _as_dict(report.get("verrou_synthesis_report"))

        print("DiagnosticRun:", latest_run.id)
        print("verrou_synthesis_report.mode:", verrou_report.get("mode"))
        print("verrou_synthesis_report.final_count:", verrou_report.get("final_count"))
        print("items extraits:", len(items))

        if not items:
            print("ERREUR: aucune liste finale de verrous trouvée dans le dernier run.")
            return 1

        old_count = (
            db.query(Verrou)
            .filter(Verrou.diagnostic_run_id == latest_run.id)
            .count()
        )

        (
            db.query(Verrou)
            .filter(Verrou.diagnostic_run_id == latest_run.id)
            .delete(synchronize_session=False)
        )

        columns = set(Verrou.__table__.columns.keys())
        created = 0

        for item in items:
            v = Verrou()

            title = item["title"]
            justification = item.get("justification") or item.get("text") or ""

            score = _to_float(
                item.get("score")
                or item.get("frascati_score")
                or item.get("confidence")
            )

            tag_cir = (
                _clean(item.get("tag_cir"))
                or _clean(item.get("decision"))
                or _clean(item.get("frascati_decision"))
                or "À examiner"
            )

            status = _clean(item.get("consultant_status")) or "en_attente"

            _set_if_column(v, columns, "diagnostic_run_id", latest_run.id)
            _set_if_column(v, columns, "title", title)
            _set_if_column(v, columns, "tag_cir", tag_cir)
            _set_if_column(v, columns, "score", score)
            _set_if_column(v, columns, "consultant_status", status)
            _set_if_column(v, columns, "justification", justification)
            _set_if_column(v, columns, "source_json", item)
            _set_if_column(v, columns, "created_at", datetime.utcnow())
            _set_if_column(v, columns, "updated_at", datetime.utcnow())

            db.add(v)
            created += 1

        db.commit()

        print(f"OK: anciens verrous supprimés={old_count}, nouveaux verrous créés={created}")
        print("Titres synchronisés:")

        for i, item in enumerate(items, start=1):
            print(f"  {i}. {item['title']}")

        return 0

    except Exception as exc:
        db.rollback()
        print("ERREUR:", repr(exc))
        return 1

    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
