# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

from sqlalchemy.orm import Session

from db.models import Article, Project, ScholarRun
from services.diagnostic_service import sanitize_json_value, ensure_ennosmart_imports
from services.scholar_service import scholar_paths, read_json, write_json


def _clean(value: Any, max_chars: int = 4000) -> str:
    text = str(value or "").strip()
    if max_chars and len(text) > max_chars:
        return text[:max_chars].rsplit(" ", 1)[0] + "..."
    return text


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _latest_scholar_run(db: Session, project: Project) -> Optional[ScholarRun]:
    return (
        db.query(ScholarRun)
        .filter(ScholarRun.project_id == project.id)
        .order_by(ScholarRun.created_at.desc())
        .first()
    )


def _report_from_run(run: Optional[ScholarRun]) -> Dict[str, Any]:
    if not run:
        return {}
    raw = run.raw_result_json or {}
    if isinstance(raw, dict) and isinstance(raw.get("report"), dict):
        return raw["report"]
    if run.report_path:
        data = read_json(run.report_path, {})
        if isinstance(data, dict):
            return data
    return raw if isinstance(raw, dict) else {}


def _result_by_verrou_id(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in report.get("results") or []:
        if not isinstance(r, dict):
            continue
        vid = r.get("verrou_id")
        if vid is not None:
            out[str(vid)] = r
    return out


def _article_to_selected_payload(article: Article) -> Dict[str, Any]:
    src = article.source_json if isinstance(article.source_json, dict) else {}

    # On conserve au maximum le JSON original d'EnnoScholar, car il contient
    # abstract, reason, fields_of_study, source, DOI, etc.
    item = dict(src)
    item.update({
        "db_article_id": article.id,
        "article_id": article.id,
        "consultant_selected": True,
        "selected": True,
        "consultant_status": article.consultant_status,
        "title": item.get("title") or article.title,
        "year": item.get("year") or article.year,
        "source": item.get("source") or article.source,
        "tag": item.get("tag") or item.get("tag_article") or article.tag_article,
        "relevance_score": (
            item.get("relevance_score")
            if item.get("relevance_score") is not None
            else article.score
        ),
        "url": item.get("url") or article.url,
        "doi": item.get("doi") or article.doi,
    })

    return sanitize_json_value(item)


def _kept_articles_by_verrou(db: Session, run: Optional[ScholarRun]) -> Dict[str, List[Article]]:
    if not run:
        return {}

    articles = (
        db.query(Article)
        .filter(Article.scholar_run_id == run.id)
        .filter(Article.consultant_status == "garde")
        .order_by(Article.verrou_id.asc().nullslast(), Article.score.desc().nullslast(), Article.year.desc().nullslast())
        .all()
    )

    grouped: Dict[str, List[Article]] = {}
    for a in articles:
        key = str(a.verrou_id or "unknown")
        grouped.setdefault(key, []).append(a)
    return grouped


def _has_direct_or_connexe(articles: List[Dict[str, Any]]) -> bool:
    for a in articles:
        tag = str(a.get("tag") or a.get("tag_article") or "").strip().lower()
        if tag in {"direct", "connexe"}:
            return True
    return False


def _is_technical_catalog_article(article: Dict[str, Any]) -> bool:
    """
    Les entrées HAL / ISO / ASTM / Zenodo / ASME sont des catalogues ou sources
    techniques à consulter. Elles ne doivent pas être envoyées au writer comme
    articles scientifiques, sinon le LLM rédige sur des portails au lieu des papiers.
    """
    source = str(article.get("source") or "").strip().lower()
    source_type = str(article.get("source_type") or "").strip().lower()
    paper_id = str(article.get("paper_id") or "").strip().lower()
    tag = str(article.get("tag") or article.get("tag_article") or "").strip().lower()

    return (
        source == "technical_catalog"
        or source_type == "technical_reference"
        or paper_id.startswith("tech:")
        or tag == "technique"
    )


def _is_scientific_selected_article(article: Dict[str, Any]) -> bool:
    if not isinstance(article, dict):
        return False
    if _is_technical_catalog_article(article):
        return False
    title = _clean(article.get("title"), 500)
    if not title:
        return False
    tag = str(article.get("tag") or article.get("tag_article") or article.get("classification") or "").strip().lower()
    # On accepte aussi les Fondamentaux gardés volontairement par le consultant.
    return tag in {"direct", "connexe", "fondamental"}


def _article_sort_key(article: Dict[str, Any]) -> tuple:
    tag = str(article.get("tag") or article.get("tag_article") or "").strip().lower()
    tag_order = {"direct": 0, "connexe": 1, "fondamental": 2}.get(tag, 9)
    try:
        score = float(article.get("relevance_score") or article.get("score") or 0)
    except Exception:
        score = 0.0
    try:
        year = int(article.get("year") or 0)
    except Exception:
        year = 0
    return (tag_order, -score, -year, _clean(article.get("title"), 300).lower())


def _normalize_frontend_payload(payload: Dict[str, Any], project: Project) -> Dict[str, Any]:
    """
    Le frontend peut envoyer :
    {
      organisme, project, year,
      verrous: [{verrou_id, verrou_title, scientific_intent, selected_articles: [...]}]
    }

    On normalise juste les statuts pour que scholar_agent._select_articles_from_verrou_item
    les considère comme sélectionnés.
    """
    payload = dict(payload or {})
    payload.setdefault("organisme", project.organisme)
    payload.setdefault("project", project.project_name)
    payload.setdefault("year", str(project.year))
    payload.setdefault("payload_type", "selected_articles_for_state_of_art")

    verrous = []
    for v in payload.get("verrous") or []:
        if not isinstance(v, dict):
            continue

        selected = []
        technical_sources = []
        for a in v.get("selected_articles") or v.get("articles") or []:
            if not isinstance(a, dict):
                continue
            item = dict(a)
            item["consultant_selected"] = True
            item["selected"] = True

            if _is_technical_catalog_article(item):
                technical_sources.append(item)
                continue

            if _is_scientific_selected_article(item):
                selected.append(item)

        selected = sorted(selected, key=_article_sort_key)

        vv = dict(v)
        vv["selected_articles"] = selected
        vv["technical_sources_excluded_from_writer"] = technical_sources
        vv["selected_articles_count"] = len(selected)
        vv["technical_sources_excluded_count"] = len(technical_sources)
        vv["has_direct_or_connexe"] = _has_direct_or_connexe(selected)
        verrous.append(vv)

    payload["verrous"] = verrous
    return sanitize_json_value(payload)


def build_state_of_art_selection_payload(db: Session, project: Project) -> Dict[str, Any]:
    """
    Construit le payload de rédaction à partir des articles réellement gardés
    par le consultant dans la base.

    Important :
    - on garde aussi les Fondamentaux sélectionnés ;
    - on bloque seulement si aucun Direct/Connexe n'est présent, sauf force=True
      dans la fonction de rédaction.
    """
    run = _latest_scholar_run(db, project)
    report = _report_from_run(run)
    result_map = _result_by_verrou_id(report)
    grouped_articles = _kept_articles_by_verrou(db, run)

    verrous: List[Dict[str, Any]] = []
    total_selected = 0
    total_direct_connexe = 0

    for verrou_id, articles in grouped_articles.items():
        selected_articles_raw = [_article_to_selected_payload(a) for a in articles]
        selected_articles = sorted(
            [a for a in selected_articles_raw if _is_scientific_selected_article(a)],
            key=_article_sort_key,
        )
        technical_sources_excluded = [a for a in selected_articles_raw if _is_technical_catalog_article(a)]

        total_selected += len(selected_articles)
        total_direct_connexe += sum(
            1 for a in selected_articles
            if str(a.get("tag") or a.get("tag_article") or "").lower() in {"direct", "connexe"}
        )

        report_item = result_map.get(verrou_id, {})
        first_validation = {}
        if selected_articles:
            sj = selected_articles[0].get("verrou_scientific_validation")
            if isinstance(sj, dict):
                first_validation = sj

        title = (
            report_item.get("verrou_title")
            or first_validation.get("verrou_title")
            or f"Verrou scientifique {verrou_id}"
        )

        verrous.append({
            "verrou_id": verrou_id,
            "verrou_title": title,
            "verrou_text": report_item.get("verrou_text") or "",
            "scientific_intent": report_item.get("scientific_intent") or {"verrou_title": title},
            "decision": report_item.get("decision") or first_validation.get("scientific_decision"),
            "scientific_support_score": report_item.get("scientific_support_score"),
            "gap_analysis": report_item.get("gap_analysis") or first_validation.get("gap_analysis"),
            "selected_articles": selected_articles,
            "selected_articles_count": len(selected_articles),
            "technical_sources_excluded_from_writer": technical_sources_excluded,
            "technical_sources_excluded_count": len(technical_sources_excluded),
            "has_direct_or_connexe": _has_direct_or_connexe(selected_articles),
        })

    payload = {
        "agent": "EnnoScholar",
        "payload_type": "selected_articles_for_state_of_art",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "organisme": project.organisme,
        "project": project.project_name,
        "year": str(project.year),
        "project_id": project.id,
        "latest_scholar_run_id": run.id if run else None,
        "domain_detection": report.get("domain_detection") or {},
        "diagnostic_context": report.get("diagnostic_context") or {},
        "verrous": verrous,
        "summary": {
            "verrous_with_selection": len(verrous),
            "selected_articles_count": total_selected,
            "direct_connexe_selected_count": total_direct_connexe,
        },
        "ok": True,
    }

    return sanitize_json_value(payload)


def _write_markdown_report(path: Path, report: Dict[str, Any]) -> None:
    parts: List[str] = []
    for r in report.get("results") or []:
        if not isinstance(r, dict):
            continue
        title = _clean(r.get("verrou_title"), 800)
        soa = r.get("state_of_art") if isinstance(r.get("state_of_art"), dict) else {}
        draft = _clean(soa.get("draft") or r.get("draft"), 50000)
        if title:
            parts.append(f"# {title}\n")
        if draft:
            parts.append(draft)
        parts.append("\n\n---\n")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts).strip(), encoding="utf-8")


def write_state_of_art_from_kept_articles(
    db: Session,
    project: Project,
    writer_mode: str = "auto",
    force: bool = False,
    frontend_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Lance la rédaction EnnoScholar V134 depuis :
    - le payload frontend si fourni ;
    - sinon les articles consultant_status='garde' stockés en base.

    Sauvegarde les résultats dans le dossier officiel du projet :
    .../ennoscholar/selected_articles_payload.json
    .../ennoscholar/ennoscholar_state_of_art_report.json
    .../ennoscholar/ennoscholar_state_of_art.md
    """
    ensure_ennosmart_imports()

    paths = scholar_paths(project)
    scholar_dir = paths["scholar_dir"]

    if frontend_payload:
        selection_payload = _normalize_frontend_payload(frontend_payload, project)
    else:
        selection_payload = build_state_of_art_selection_payload(db, project)

    verrous = [v for v in selection_payload.get("verrous") or [] if isinstance(v, dict)]
    if not verrous:
        return {
            "ok": False,
            "reason": (
                "Aucun article scientifique sélectionné pour la rédaction. "
                "Les sources technical_catalog comme HAL, ISO, ASTM, Zenodo ou ASME sont exclues du writer. "
                "Garde au moins un vrai article Direct, Connexe ou Fondamental."
            ),
            "selection_payload": selection_payload,
        }

    if not force:
        blocked = [
            v.get("verrou_title") or v.get("verrou_id")
            for v in verrous
            if not _has_direct_or_connexe(v.get("selected_articles") or [])
        ]
        if blocked:
            return {
                "ok": False,
                "reason": (
                    "Certains verrous n'ont aucun article Direct ou Connexe gardé. "
                    "Ajoute au moins un Direct/Connexe ou relance avec force=true."
                ),
                "blocked_verrous": blocked,
                "selection_payload": selection_payload,
            }

    selection_path = scholar_dir / "selected_articles_payload.json"
    report_path = scholar_dir / "ennoscholar_state_of_art_report.json"
    md_path = scholar_dir / "ennoscholar_state_of_art.md"

    write_json(selection_path, selection_payload)

    try:
        from agents.EnnoScholar.scholar_agent import EnnoScholarAgent
    except Exception:
        from agents.EnnoScholar.scholar_agent import EnnoScholarAgent

    agent = EnnoScholarAgent(
        use_semantic_scholar=False,
        use_openalex=False,
        use_arxiv=False,
        offline_dry_run=True,
    )

    report = agent.run_writer_from_selection(
        selection_payload=selection_payload,
        writer_mode=writer_mode,
    )

    report["ok"] = True
    report["project_id"] = project.id
    report["outputs"] = {
        "selection_payload": str(selection_path),
        "state_of_art_report": str(report_path),
        "state_of_art_markdown": str(md_path),
    }

    write_json(report_path, report)
    _write_markdown_report(md_path, report)

    # Compatibilité frontend : certains composants lisent response.results /
    # response.verrous_written directement, d'autres lisent response.report.results.
    # On renvoie donc les deux formes.
    return sanitize_json_value({
        "ok": True,
        "message": "État de l'art généré.",
        "agent": report.get("agent"),
        "version": report.get("version"),
        "mode": report.get("mode"),
        "writer_mode": report.get("writer_mode"),
        "verrous_written": report.get("verrous_written", 0),
        "results": report.get("results") or [],
        "selection_payload": selection_payload,
        "report": report,
        "outputs": report["outputs"],
    })


def read_latest_state_of_art(project: Project) -> Dict[str, Any]:
    paths = scholar_paths(project)
    scholar_dir = paths["scholar_dir"]
    report_path = scholar_dir / "ennoscholar_state_of_art_report.json"
    selection_path = scholar_dir / "selected_articles_payload.json"
    md_path = scholar_dir / "ennoscholar_state_of_art.md"

    report = read_json(report_path, {})
    selection = read_json(selection_path, {})
    markdown = ""
    if md_path.exists():
        try:
            markdown = md_path.read_text(encoding="utf-8")
        except Exception:
            markdown = ""

    return sanitize_json_value({
        "ok": bool(report),
        "files_found": {
            "state_of_art_report": report_path.exists(),
            "selection_payload": selection_path.exists(),
            "state_of_art_markdown": md_path.exists(),
        },
        "paths": {
            "state_of_art_report": str(report_path),
            "selection_payload": str(selection_path),
            "state_of_art_markdown": str(md_path),
        },
        "report": report,
        "selection_payload": selection,
        "markdown": markdown,
    })
