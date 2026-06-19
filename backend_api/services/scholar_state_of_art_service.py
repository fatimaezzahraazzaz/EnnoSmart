# -*- coding: utf-8 -*-
from __future__ import annotations

"""
services/scholar_state_of_art_service.py — V44

Alignement React/Backend avec l'Agent 2 EnnoScholar :
- Le consultant garde/rejette les articles côté frontend.
- Seuls les articles consultant_status='garde' servent à rédiger.
- Rédaction par verrou, citations contrôlées [A1], [A2].
- Garde-fou : si aucun Direct/Connexe sélectionné, on bloque sauf force=True.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
import json

from sqlalchemy.orm import Session

from db.models import Article, ScholarRun, Project


def _safe_float(x: Any) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return 0.0


def _sanitize(value: Any) -> Any:
    # évite les NaN/objets non sérialisables dans les réponses API
    try:
        import math
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, tuple):
        return [_sanitize(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: str | Path, data: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_sanitize(data), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def latest_scholar_run(db: Session, project: Project) -> ScholarRun | None:
    return (
        db.query(ScholarRun)
        .filter(ScholarRun.project_id == project.id)
        .order_by(ScholarRun.created_at.desc())
        .first()
    )


def _report_from_run(run: ScholarRun | None) -> Dict[str, Any]:
    if not run:
        return {}
    data = run.raw_result_json or {}
    if isinstance(data, dict):
        if isinstance(data.get("report"), dict):
            return data["report"]
        if isinstance(data.get("bundle"), dict) and isinstance(data["bundle"].get("report"), dict):
            return data["bundle"]["report"]
        if isinstance(data.get("results"), list):
            return data
    if run.report_path:
        return _read_json(run.report_path, {}) or {}
    return {}


def _project_scholar_dir(project: Project) -> Path:
    # On réutilise le service existant si disponible, sinon fallback storage local.
    try:
        from services.scholar_service import scholar_paths
        return scholar_paths(project)["scholar_dir"]
    except Exception:
        base = Path("storage") / "organismes" / str(project.organisme) / "projects" / str(project.project_name) / "years" / str(project.year) / "ennoscholar"
        base.mkdir(parents=True, exist_ok=True)
        return base


def _article_original(article: Article) -> Dict[str, Any]:
    src = article.source_json if isinstance(article.source_json, dict) else {}
    out = dict(src)
    out.update({
        "db_article_id": article.id,
        "title": article.title,
        "year": article.year,
        "source": article.source,
        "tag": article.tag_article or src.get("tag"),
        "tag_article": article.tag_article or src.get("tag_article"),
        "relevance_score": article.score if article.score is not None else src.get("relevance_score"),
        "score": article.score if article.score is not None else src.get("score"),
        "url": article.url or src.get("url"),
        "doi": article.doi or src.get("doi"),
        "consultant_status": article.consultant_status,
    })
    return out


def build_state_of_art_selection_payload(db: Session, project: Project) -> Dict[str, Any]:
    run = latest_scholar_run(db, project)
    report = _report_from_run(run)
    results = report.get("results") or []

    if not run or not results:
        return {
            "ok": False,
            "reason": "Aucun rapport EnnoScholar trouvé. Lance d'abord EnnoScholar sur les verrous gardés.",
            "organisme": project.organisme,
            "project": project.project_name,
            "year": str(project.year),
            "verrous": [],
        }

    kept_articles = (
        db.query(Article)
        .filter(Article.scholar_run_id == run.id)
        .filter(Article.consultant_status == "garde")
        .all()
    )

    by_verrou: Dict[int | None, List[Article]] = {}
    for a in kept_articles:
        by_verrou.setdefault(a.verrou_id, []).append(a)

    verrous = []
    for result in results:
        if not isinstance(result, dict):
            continue
        raw_vid = result.get("verrou_id")
        try:
            vid = int(raw_vid) if raw_vid is not None and str(raw_vid).isdigit() else None
        except Exception:
            vid = None

        selected = [_article_original(a) for a in by_verrou.get(vid, [])]
        tags = [str(a.get("tag") or a.get("tag_article") or "") for a in selected]
        direct_connexe_count = sum(1 for t in tags if t in {"Direct", "Connexe"})

        ready = bool(selected) and direct_connexe_count > 0
        if not selected:
            readiness_reason = "Aucun article gardé par le consultant pour ce verrou."
        elif direct_connexe_count == 0:
            readiness_reason = "Les articles gardés sont uniquement Fondamentaux : rédaction CIR automatique déconseillée."
        else:
            readiness_reason = "Sélection suffisante pour une rédaction contrôlée."

        verrous.append({
            "verrou_id": result.get("verrou_id"),
            "verrou_title": result.get("verrou_title"),
            "verrou_text": result.get("verrou_text"),
            "scientific_intent": result.get("scientific_intent") or {},
            "decision": result.get("decision"),
            "scientific_support_score": result.get("scientific_support_score"),
            "gap_analysis": result.get("gap_analysis"),
            "selected_articles": selected,
            "selected_articles_count": len(selected),
            "direct_connexe_count": direct_connexe_count,
            "state_of_art_ready": ready,
            "readiness_reason": readiness_reason,
        })

    payload = {
        "ok": True,
        "agent": "EnnoScholar",
        "payload_type": "selected_articles_for_state_of_art",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "organisme": project.organisme,
        "project": project.project_name,
        "year": str(project.year),
        "scholar_run_id": run.id,
        "domain_detection": report.get("domain_detection") or {},
        "diagnostic_context": report.get("diagnostic_context") or {},
        "verrous": verrous,
        "summary": {
            "verrous_total": len(verrous),
            "verrous_ready": sum(1 for v in verrous if v.get("state_of_art_ready")),
            "selected_articles_total": sum(int(v.get("selected_articles_count") or 0) for v in verrous),
        },
    }
    return _sanitize(payload)


def write_state_of_art_from_kept_articles(
    db: Session,
    project: Project,
    writer_mode: str = "auto",
    force: bool = False,
) -> Dict[str, Any]:
    payload = build_state_of_art_selection_payload(db, project)
    if not payload.get("ok"):
        return payload

    if not force:
        ready_count = payload.get("summary", {}).get("verrous_ready", 0)
        if ready_count <= 0:
            return {
                "ok": False,
                "reason": "Aucun verrou prêt pour rédaction. Garde au moins un article Direct ou Connexe par verrou, ou utilise force=true pour générer un brouillon de contrôle.",
                "selection_payload": payload,
            }

        # on ne rédige que les verrous prêts
        payload["verrous"] = [v for v in payload.get("verrous") or [] if v.get("state_of_art_ready")]

    scholar_dir = _project_scholar_dir(project)
    selection_path = scholar_dir / "selected_articles_consultant.json"
    output_path = scholar_dir / "ennoscholar_state_of_art_report.json"
    _write_json(selection_path, payload)

    try:
        try:
            from agents.EnnoScholar.scholar_agent import run_state_of_art_writer_from_selection
        except Exception:
            from modules.EnnoScholar.scholar_agent import run_state_of_art_writer_from_selection

        report = run_state_of_art_writer_from_selection(
            selection_payload_path=selection_path,
            out_dir=scholar_dir,
            writer_mode=writer_mode,
        )
        report["ok"] = True
        report["selection_summary"] = payload.get("summary")
        report["outputs"] = report.get("outputs") or {}
        report["outputs"]["selection_payload"] = str(selection_path)
        report["outputs"]["state_of_art_report"] = str(output_path)
        _write_json(output_path, report)
        return _sanitize(report)

    except Exception as exc:
        return {
            "ok": False,
            "reason": f"Erreur rédaction état de l'art : {exc}",
            "selection_payload_path": str(selection_path),
        }


def read_latest_state_of_art(project: Project) -> Dict[str, Any]:
    scholar_dir = _project_scholar_dir(project)
    path = scholar_dir / "ennoscholar_state_of_art_report.json"
    data = _read_json(path, {}) or {}
    return {
        "ok": bool(data),
        "path": str(path),
        "report": data,
    }
