from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import Any, Dict
from pathlib import Path
from datetime import datetime
import re
import json
import importlib.util
import shutil

from core.deps import get_current_user, get_db
from db.models import DiagnosticRun, User, Verrou
from schemas.diagnostic import DiagnosticRead, VerrouDecisionRequest, VerrouRead
from services.diagnostic_display_service import build_diagnostic_display
from services.diagnostic_service import (
    create_diagnostic_run_from_files,
    prepare_ennodiagnostic_sources,
    read_diagnostic_bundle,
    run_ennodiagnostic,
    run_ennodiagnostic_agent_only,
    sanitize_json_value,
    sync_verrous_from_diagnostic,
    ensure_ennosmart_imports,
)
from services.project_service import get_project_for_user


router = APIRouter(tags=["ennodiagnostic"])



def _latest_run_for_project(db: Session, project_id: int) -> DiagnosticRun | None:
    return (
        db.query(DiagnosticRun)
        .filter(DiagnosticRun.project_id == project_id)
        .order_by(DiagnosticRun.created_at.desc())
        .first()
    )


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _section_key(title: str) -> str:
    title = title.lower().strip()
    table = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    title = title.translate(table)
    title = re.sub(r"[^a-z0-9]+", "_", title)
    return re.sub(r"_+", "_", title).strip("_")


def _extract_sections(markdown: str) -> Dict[str, str]:
    markdown = _clean(markdown)
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", markdown))
    if not matches:
        return {}

    sections: Dict[str, str] = {}
    for i, match in enumerate(matches):
        title = _section_key(match.group(1))
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        sections[title] = markdown[start:end].strip()

    return sections


def _replace_section(markdown: str, section_title: str, body: str) -> str:
    """
    Remplace une section Markdown de manière robuste :
    - accepte espaces après le titre ;
    - accepte CRLF ;
    - conserve toutes les autres sections.
    """
    markdown = _clean(markdown).replace("\r\n", "\n").replace("\r", "\n")
    section_title_escaped = re.escape(section_title)
    pattern = re.compile(
        rf"(?ms)^##\s+{section_title_escaped}[^\n]*\n.*?(?=^##\s+|\Z)"
    )
    new_block = f"## {section_title}  \n{body.strip()}\n\n"

    if pattern.search(markdown):
        return pattern.sub(new_block, markdown).strip()

    return (markdown.rstrip() + "\n\n" + new_block).strip()


def _normalize_agent_report_content(content: str) -> str:
    """Normalisation générique du rapport agent, sans forcer d'objectif projet."""
    return _clean(content)


def _extract_latest_run_report(latest_run: DiagnosticRun | None) -> tuple[Dict[str, Any], str]:
    if not latest_run or not latest_run.raw_result_json:
        return {}, ""

    raw = sanitize_json_value(latest_run.raw_result_json)
    if not isinstance(raw, dict):
        return {}, ""

    pipeline = _as_dict(raw.get("script_or_pipeline_result"))
    report = _as_dict(pipeline.get("report"))
    diagnostic = _as_dict(report.get("diagnostic"))

    content = _clean(
        diagnostic.get("content")
        or report.get("report_markdown")
        or report.get("content")
        or ""
    )

    return report, content


def _force_display_from_latest_run(display: Dict[str, Any], latest_run: DiagnosticRun | None, project=None) -> Dict[str, Any]:
    display = dict(display or {})
    report, content = _extract_latest_run_report(latest_run)

    if not content:
        return display

    content = _normalize_agent_report_content(content)
    sections = _extract_sections(content)

    display["source"] = "ennodiagnostic_agent"
    display["report_markdown"] = content
    display["summary"] = sections.get("synthese_strategique_du_projet", display.get("summary", ""))
    display["objective"] = sections.get("objectif_global_reformule", display.get("objective", ""))
    display["frascati_text"] = sections.get("lecture_frascati_du_dossier", display.get("frascati_text", ""))
    display["verrous_text"] = sections.get("verrous_r_d_signaux_de_verrous", display.get("verrous_text", ""))

    display["report_sections"] = {
        "lecture_frascati": sections.get("lecture_frascati_du_dossier", ""),
        "synthese": sections.get("synthese_strategique_du_projet", ""),
        "objectif": sections.get("objectif_global_reformule", ""),
        "verrous": sections.get("verrous_r_d_signaux_de_verrous", ""),
        "demarche": sections.get("demarche_experimentale_detectee", ""),
        "resultats": sections.get("resultats_et_metriques_disponibles", ""),
        "parametres": sections.get("parametres_et_contraintes_techniques", ""),
        "comparaison_cir": sections.get("comparaison_avec_le_cir_precedent", ""),
        "points_validation": sections.get("points_a_valider_par_le_consultant", ""),
    }

    if report:
        display["frascati_summary"] = report.get("frascati_summary") or display.get("frascati_summary") or {}
        display["inputs_status"] = report.get("inputs_status") or display.get("inputs_status") or {}
        display["chroma_sections"] = report.get("chroma_sections") or display.get("chroma_sections") or {}

        pipeline = _as_dict(report.get("pipeline_before_agent"))
        nlp_stats = _as_dict(pipeline.get("nlp_stats"))
        index_report = _as_dict(pipeline.get("index_report"))

        display["pipeline_stats"] = {
            "documents_loaded_count": pipeline.get("documents_loaded_count"),
            "raw_candidates": nlp_stats.get("raw_candidates"),
            "raw_kept": nlp_stats.get("raw_kept"),
            "merged_verrous": nlp_stats.get("merged_verrous"),
            "chunks_prepared": index_report.get("chunks_prepared"),
            "chunks_indexed": index_report.get("chunks_indexed"),
        }

    display["forced_from_latest_run"] = True

    # Comparaison documentaire brute exposée au frontend.
    doc_compare_index = {}
    doc_compare_dir_value = None
    try:
        from services.diagnostic_service import get_project_store

        if project is not None:
            ps = get_project_store(project)
            doc_compare_dir = ps.project_dir / "document_compare"
            doc_compare_dir_value = str(doc_compare_dir)
            index_path = doc_compare_dir / "auto_compare_index.json"

            if index_path.exists():
                try:
                    doc_compare_index = json.loads(index_path.read_text(encoding="utf-8"))
                except Exception:
                    doc_compare_index = {}
    except Exception:
        doc_compare_index = {}

    if isinstance(doc_compare_index, dict) and doc_compare_index.get("ok"):
        display["document_compare"] = doc_compare_index
        display["document_compare_ok"] = True
        display["document_compare_pairs_count"] = doc_compare_index.get("pairs_count", 0)
        display["document_compare_pairs"] = doc_compare_index.get("pairs") or []
        display["document_compare_output_dir"] = doc_compare_index.get("output_dir") or doc_compare_dir_value
    else:
        display["document_compare"] = {}
        display["document_compare_ok"] = False
        display["document_compare_pairs_count"] = 0
        display["document_compare_pairs"] = []
        display["document_compare_output_dir"] = doc_compare_dir_value
        display["document_compare_debug_path"] = doc_compare_dir_value

    # Comparaison avec le CIR précédent exposée au frontend.
    cir_memory_report = {}
    if report:
        cir_memory_report = report.get("cir_memory_report") or {}

    if isinstance(cir_memory_report, dict):
        cir_summary = cir_memory_report.get("summary") or {}
        display["cir_memory"] = cir_memory_report
        display["cir_memory_ok"] = bool(cir_memory_report.get("ok"))
        display["cir_memory_has_previous"] = bool(cir_memory_report.get("has_previous_cir"))
        display["cir_memory_previous_years"] = cir_memory_report.get("previous_cir_years_used") or []
        display["cir_memory_summary"] = cir_summary
        display["cir_memory_project_novelty_score"] = cir_summary.get("project_novelty_score")
        display["cir_memory_signal"] = cir_summary.get("frascati_context_signal")
        display["cir_memory_explanation"] = cir_summary.get("frascati_context_explanation")
        display["cir_memory_new_verrous"] = cir_memory_report.get("new_or_not_found") or []
        display["cir_memory_evolutions"] = cir_memory_report.get("evolution_or_partial_continuity") or []
        display["cir_memory_continuities"] = cir_memory_report.get("continuity_strong") or []
        display["cir_memory_verrou_comparisons"] = cir_memory_report.get("verrou_comparisons") or []
    else:
        display["cir_memory"] = {}
        display["cir_memory_ok"] = False
        display["cir_memory_has_previous"] = False
        display["cir_memory_previous_years"] = []
        display["cir_memory_summary"] = {}
        display["cir_memory_project_novelty_score"] = None
        display["cir_memory_signal"] = None
        display["cir_memory_explanation"] = None
        display["cir_memory_new_verrous"] = []
        display["cir_memory_evolutions"] = []
        display["cir_memory_continuities"] = []
        display["cir_memory_verrou_comparisons"] = []

    # Mémoire rédactionnelle CIR exposée au frontend.
    style_memory_report = {}
    if report:
        style_memory_report = report.get("style_memory_report") or {}

    if isinstance(style_memory_report, dict):
        display["style_memory"] = style_memory_report
        display["style_memory_ok"] = bool(style_memory_report.get("ok"))
        display["style_memory_examples_count"] = style_memory_report.get("examples_count", 0)
        display["style_memory_roles"] = style_memory_report.get("examples_by_role_count", {})
        display["style_memory_stats"] = style_memory_report.get("stats", {})
    else:
        display["style_memory"] = {}
        display["style_memory_ok"] = False
        display["style_memory_examples_count"] = 0
        display["style_memory_roles"] = {}
        display["style_memory_stats"] = {}

    # Score IA documentaire exposé au frontend.
    ai_report = {}
    if report:
        ai_report = (
            report.get("ai_detection_report_runtime")
            or report.get("ai_detection_report")
            or {}
        )

    if isinstance(ai_report, dict):
        summary = ai_report.get("summary") or {}
        ai_detection = ai_report.get("ai_detection") or {}
        top_passages = (
            ai_report.get("top_passages")
            or ai_detection.get("suspected_passages")
            or ai_detection.get("passages")
            or []
        )

        display["ai_detection"] = ai_report
        display["ai_score"] = summary.get("average_ai_percentage") or ai_detection.get("global_ai_percentage")
        display["ai_risk_level"] = summary.get("risk_level") or ai_detection.get("risk_level")
        display["ai_suspected_passages"] = top_passages[:10] if isinstance(top_passages, list) else []
    else:
        display["ai_detection"] = {}
        display["ai_score"] = None
        display["ai_risk_level"] = None
        display["ai_suspected_passages"] = []
    return display


def _merge_latest_run_into_bundle(bundle: dict, latest_run: DiagnosticRun | None) -> dict:
    bundle = dict(bundle or {})

    if latest_run and latest_run.raw_result_json:
        raw = sanitize_json_value(latest_run.raw_result_json)
        bundle["run_raw_result_json"] = raw

        if isinstance(raw, dict):
            pipeline = raw.get("script_or_pipeline_result")
            if isinstance(pipeline, dict) and isinstance(pipeline.get("report"), dict):
                bundle["report"] = pipeline["report"]

    return sanitize_json_value(bundle)


@router.get("/projects/{project_id}/diagnostic/latest")
def get_latest_diagnostic(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    latest_run = _latest_run_for_project(db, project.id)

    bundle = read_diagnostic_bundle(project)
    bundle = _merge_latest_run_into_bundle(bundle, latest_run)

    display = build_diagnostic_display(project, bundle)

    # On privilégie le rapport agent le plus récent si disponible.
    display = _force_display_from_latest_run(display, latest_run, project=project)

    latest_run_dump = None
    latest_verrous = []

    if latest_run:
        latest_run_dump = DiagnosticRead.model_validate(latest_run).model_dump()
        latest_verrous = (
            db.query(Verrou)
            .filter(Verrou.diagnostic_run_id == latest_run.id)
            .order_by(Verrou.created_at.asc())
            .all()
        )

    return sanitize_json_value(
        {
            "project": {
                "id": project.id,
                "organisme": project.organisme,
                "project_name": project.project_name,
                "year": project.year,
                "domain_label": project.domain_label,
                "status": project.status,
            },
            "latest_run": latest_run_dump,
            "bundle": bundle,
            "display": display,
            "validation_verrous": [
                VerrouRead.model_validate(v).model_dump() for v in latest_verrous
            ],
            "source_policy": {
                "diagnostic_display_source": "latest_run.raw_result_json.script_or_pipeline_result.report.diagnostic.content",
                "validation_source": "validation_verrous or /projects/{id}/verrous",
                "note": "Flow simple : prepare-sources prépare extraction/NLP/RAG ; run-agent lance seulement l’agent EnnoDiagnostic. /diagnostic/run garde le mode complet en un bouton.",
            },
        }
    )


@router.post("/projects/{project_id}/diagnostic/import-existing", response_model=DiagnosticRead)
def import_existing_diagnostic(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    run = create_diagnostic_run_from_files(db, project)
    return run






def _load_document_compare_module():
    """
    Charge DOCUMENT_COMPARE de manière robuste.

    Pourquoi :
    quand le backend est lancé depuis C:\EnnoSmart\backend_api, Python peut voir
    le package modules mais pas le sous-package DOCUMENT_COMPARE si le dossier
    n'est pas bien déclaré/copié. Cette fonction tente :
    1) import normal modules.DOCUMENT_COMPARE.document_compare
    2) import direct depuis C:\EnnoSmart\modules\DOCUMENT_COMPARE\document_compare.py
    """
    try:
        ensure_ennosmart_imports()
    except Exception:
        pass

    try:
        from modules.DOCUMENT_COMPARE import document_compare as mod
        return mod
    except Exception as import_error:
        # Import direct par chemin fichier.
        candidates = []

        try:
            # C:\EnnoSmart\backend_api\routers\diagnostic.py -> C:\EnnoSmart
            candidates.append(Path(__file__).resolve().parents[2] / "modules" / "DOCUMENT_COMPARE" / "document_compare.py")
        except Exception:
            pass

        candidates.append(Path(r"C:\EnnoSmart\modules\DOCUMENT_COMPARE\document_compare.py"))

        for path in candidates:
            if path.exists():
                spec = importlib.util.spec_from_file_location("ennosmart_document_compare_direct", str(path))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    return mod

        raise RuntimeError(
            "Module DOCUMENT_COMPARE introuvable. Copie le dossier "
            "C:\\EnnoSmart\\modules\\DOCUMENT_COMPARE avec __init__.py et document_compare.py. "
            f"Erreur import initiale : {import_error}"
        )



def _safe_upload_filename(filename: str) -> str:
    filename = Path(filename or "document").name
    filename = re.sub(r"[^\wÀ-ÿ ._()\\-]+", "_", filename, flags=re.UNICODE)
    filename = re.sub(r"_+", "_", filename).strip("._ ")
    return filename or "document"


def _manual_compare_upload_dir(project) -> Path:
    from services.diagnostic_service import get_project_store

    ps = get_project_store(project)
    root = ps.project_dir / "document_compare" / "manual_uploads"
    root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out = root / stamp
    out.mkdir(parents=True, exist_ok=True)
    return out


def _save_upload_file(upload: UploadFile, target_dir: Path, prefix: str) -> Path:
    if not upload or not upload.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Fichier {prefix} manquant.",
        )

    safe = _safe_upload_filename(upload.filename)
    target = target_dir / f"{prefix}__{safe}"

    with target.open("wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)

    return target


def _resolve_project_raw_file(raw_dir, value: str):
    """
    Accepte soit un chemin complet issu de l'index, soit un nom de fichier.
    Empêche de comparer un fichier hors du dossier raw du projet.
    """
    from pathlib import Path

    if not value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chemin de fichier manquant.",
        )

    raw_root = Path(raw_dir).resolve()
    p = Path(value)

    if not p.is_absolute():
        p = raw_root / value

    p = p.resolve()

    try:
        p.relative_to(raw_root)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fichier hors du dossier raw du projet.",
        )

    if not p.exists() or not p.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Fichier introuvable : {p.name}",
        )

    return p


@router.get("/projects/{project_id}/diagnostic/document-compare/auto-pairs")
def get_document_compare_pairs(
    project_id: int,
    min_similarity: float = Query(0.70, ge=0.0, le=1.0),
    include_medium: bool = Query(True),
    force: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne l'index des paires de documents bruts comparables.
    Si l'index n'existe pas ou force=true, il le recrée.
    """
    project = get_project_for_user(db, project_id, current_user)

    try:
        from services.diagnostic_service import get_project_store
        doc_compare = _load_document_compare_module()
        auto_compare_project_pairs = doc_compare.auto_compare_project_pairs

        ps = get_project_store(project)
        raw_dir = ps.documents_raw_dir
        output_dir = ps.project_dir / "document_compare"
        index_path = output_dir / "auto_compare_index.json"

        if index_path.exists() and not force:
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
                return sanitize_json_value(data)
            except Exception:
                pass

        report = auto_compare_project_pairs(
            project_uploaded_dir=str(raw_dir),
            output_dir=str(output_dir),
            min_similarity=min_similarity,
            include_medium=include_medium,
            force=force,
        )
        return sanitize_json_value(report)

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Détection des paires documentaires impossible : {exc}",
        )


@router.post("/projects/{project_id}/diagnostic/document-compare/auto-pairs")
def run_document_compare_pairs(
    project_id: int,
    payload: Dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Force la détection des paires de documents bruts comparables.
    """
    project = get_project_for_user(db, project_id, current_user)
    payload = payload or {}

    try:
        from services.diagnostic_service import get_project_store
        doc_compare = _load_document_compare_module()
        auto_compare_project_pairs = doc_compare.auto_compare_project_pairs

        ps = get_project_store(project)
        raw_dir = ps.documents_raw_dir
        output_dir = ps.project_dir / "document_compare"

        report = auto_compare_project_pairs(
            project_uploaded_dir=str(raw_dir),
            output_dir=str(output_dir),
            min_similarity=float(payload.get("min_similarity", 0.70)),
            include_medium=bool(payload.get("include_medium", True)),
            force=bool(payload.get("force", True)),
        )
        return sanitize_json_value(report)

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Détection des paires documentaires impossible : {exc}",
        )



@router.post("/projects/{project_id}/diagnostic/document-compare/upload-pair")
def upload_and_compare_document_pair(
    project_id: int,
    file_a: UploadFile = File(...),
    file_b: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Mode Streamlit-like :
    l'utilisateur charge manuellement 2 documents A/B,
    puis le backend les compare immédiatement.
    """
    project = get_project_for_user(db, project_id, current_user)

    try:
        doc_compare = _load_document_compare_module()
        compare_pair_to_report = doc_compare.compare_pair_to_report

        upload_dir = _manual_compare_upload_dir(project)
        path_a = _save_upload_file(file_a, upload_dir, "A")
        path_b = _save_upload_file(file_b, upload_dir, "B")

        from services.diagnostic_service import get_project_store
        ps = get_project_store(project)
        output_dir = ps.project_dir / "document_compare" / "manual_reports"
        output_dir.mkdir(parents=True, exist_ok=True)

        report = compare_pair_to_report(
            file_a=str(path_a),
            file_b=str(path_b),
            output_dir=str(output_dir),
            force=True,
        )

        report["manual_upload"] = {
            "ok": True,
            "file_a_original": file_a.filename,
            "file_b_original": file_b.filename,
            "file_a_saved": str(path_a),
            "file_b_saved": str(path_b),
            "upload_dir": str(upload_dir),
            "output_dir": str(output_dir),
        }

        return sanitize_json_value(report)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Comparaison manuelle impossible : {exc}",
        )



@router.post("/projects/{project_id}/diagnostic/document-compare/compare-pair")
def compare_document_pair(
    project_id: int,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Compare deux documents bruts A/B.
    Le body accepte :
    - file_a + file_b
    ou
    - pair_index, en utilisant auto_compare_index.json.
    """
    project = get_project_for_user(db, project_id, current_user)

    try:
        from services.diagnostic_service import get_project_store
        doc_compare = _load_document_compare_module()
        compare_pair_to_report = doc_compare.compare_pair_to_report
        auto_compare_project_pairs = doc_compare.auto_compare_project_pairs

        ps = get_project_store(project)
        raw_dir = ps.documents_raw_dir
        output_dir = ps.project_dir / "document_compare"
        output_dir.mkdir(parents=True, exist_ok=True)

        file_a = payload.get("file_a")
        file_b = payload.get("file_b")

        if (file_a is None or file_b is None) and payload.get("pair_index") is not None:
            index_path = output_dir / "auto_compare_index.json"
            if index_path.exists():
                index = json.loads(index_path.read_text(encoding="utf-8"))
            else:
                index = auto_compare_project_pairs(
                    project_uploaded_dir=str(raw_dir),
                    output_dir=str(output_dir),
                    min_similarity=0.70,
                    include_medium=True,
                    force=False,
                )

            pairs = index.get("pairs") or []
            pair_index = int(payload.get("pair_index"))
            if pair_index < 0 or pair_index >= len(pairs):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="pair_index invalide.",
                )

            pair = pairs[pair_index]
            file_a = pair.get("file_a")
            file_b = pair.get("file_b")

        path_a = _resolve_project_raw_file(raw_dir, str(file_a or ""))
        path_b = _resolve_project_raw_file(raw_dir, str(file_b or ""))

        report = compare_pair_to_report(
            file_a=str(path_a),
            file_b=str(path_b),
            output_dir=str(output_dir),
            force=bool(payload.get("force", True)),
        )

        return sanitize_json_value(report)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Comparaison documentaire impossible : {exc}",
        )


@router.post("/projects/{project_id}/diagnostic/cir-memory/compare")
def compare_with_previous_cir(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lance uniquement la comparaison avec le CIR précédent :
    dossier courant NLP/Frascati vs CIR final mémoire N-1.
    """
    project = get_project_for_user(db, project_id, current_user)

    try:
        from services.diagnostic_service import get_project_store
        from modules.CIR_MEMORY.cir_memory import compare_current_raw_with_cir_memory

        ps = get_project_store(project)
        nlp_path = ps.nlp_dir / "nlp_result.json"

        if not nlp_path.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="nlp_result.json introuvable. Lance d'abord prepare-sources.",
            )

        report = compare_current_raw_with_cir_memory(
            organisme=project.organisme,
            project=project.project_name,
            year=str(project.year),
            nlp_result_path=nlp_path,
        )

        return sanitize_json_value(report)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Comparaison CIR précédent impossible : {exc}",
        )




@router.post("/projects/{project_id}/cir-previous/compare-current")
def compare_current_with_previous_cir_independent(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lance uniquement la comparaison CIR précédent.

    Cette route ne lance pas EnnoDiagnostic, ne relance pas le LLM diagnostic,
    ne relance pas le score IA et ne refait pas le NLP.
    Elle compare le nlp_result.json courant déjà préparé avec la mémoire CIR N-1.
    """
    project = get_project_for_user(db, project_id, current_user)

    try:
        from services.diagnostic_service import get_project_store
        from modules.CIR_MEMORY.cir_memory import compare_current_raw_with_cir_memory

        ps = get_project_store(project)
        nlp_path = ps.nlp_dir / "nlp_result.json"

        if not nlp_path.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="nlp_result.json introuvable. Lance d'abord Préparer les sources.",
            )

        report = compare_current_raw_with_cir_memory(
            organisme=project.organisme,
            project=project.project_name,
            year=str(project.year),
            nlp_result_path=nlp_path,
        )

        return sanitize_json_value(report)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Comparaison CIR précédent impossible : {exc}",
        )


@router.get("/projects/{project_id}/cir-previous/comparison-latest")
def get_latest_previous_cir_comparison(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lit le dernier rapport de comparaison CIR précédent sauvegardé.
    Ne lance aucun calcul.
    """
    project = get_project_for_user(db, project_id, current_user)

    try:
        from modules.CIR_MEMORY.cir_memory import comparison_report_path

        path = comparison_report_path(project.organisme, project.project_name, str(project.year))
        if not path.exists():
            return sanitize_json_value({
                "ok": False,
                "missing": True,
                "has_previous_cir": False,
                "message": "Aucune comparaison CIR précédent sauvegardée pour ce projet.",
                "report_path": str(path),
            })

        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Rapport CIR précédent illisible : {exc}",
            )

        if isinstance(report, dict):
            report["report_path"] = str(path)
            report["loaded_from_saved_report"] = True

        return sanitize_json_value(report)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lecture comparaison CIR précédent impossible : {exc}",
        )

@router.post("/projects/{project_id}/diagnostic/prepare-sources")
def prepare_diagnostic_sources(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Étape 1 :
    upload/raw documents -> extraction -> NLP -> Frascati -> RAG/Chroma.

    Ne lance pas le LLM.
    """
    project = get_project_for_user(db, project_id, current_user)
    return prepare_ennodiagnostic_sources(db, project)


@router.post("/projects/{project_id}/diagnostic/run-agent", response_model=DiagnosticRead)
def run_diagnostic_agent_only(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Étape 2 :
    lance seulement EnnoDiagnosticAgent.generate_diagnostic()
    depuis les sources déjà préparées dans Chroma.
    """
    project = get_project_for_user(db, project_id, current_user)
    run = run_ennodiagnostic_agent_only(db, project)
    return run


@router.post("/projects/{project_id}/diagnostic/run", response_model=DiagnosticRead)
def run_diagnostic(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    run = run_ennodiagnostic(db, project)
    return run


@router.post("/projects/{project_id}/diagnostic/{run_id}/sync-verrous", response_model=list[VerrouRead])
def sync_verrous(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    run = (
        db.query(DiagnosticRun)
        .filter(DiagnosticRun.id == run_id, DiagnosticRun.project_id == project.id)
        .first()
    )

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnostic run introuvable.",
        )

    return sync_verrous_from_diagnostic(db, run)


@router.get("/projects/{project_id}/verrous", response_model=list[VerrouRead])
def list_verrous(
    project_id: int,
    latest_only: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    query = (
        db.query(Verrou)
        .join(DiagnosticRun, Verrou.diagnostic_run_id == DiagnosticRun.id)
        .filter(DiagnosticRun.project_id == project.id)
    )

    if latest_only:
        latest_run = _latest_run_for_project(db, project.id)
        if not latest_run:
            return []
        query = query.filter(Verrou.diagnostic_run_id == latest_run.id)

    verrous = query.order_by(Verrou.created_at.desc()).all()

    seen: set[str] = set()
    clean: list[Verrou] = []

    for verrou in verrous:
        key = (verrou.title or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        clean.append(verrou)

    return clean


@router.patch("/projects/{project_id}/verrous/{verrou_id}/decision", response_model=VerrouRead)
def update_verrou_decision(
    project_id: int,
    verrou_id: int,
    payload: VerrouDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)

    verrou = (
        db.query(Verrou)
        .join(DiagnosticRun, Verrou.diagnostic_run_id == DiagnosticRun.id)
        .filter(Verrou.id == verrou_id, DiagnosticRun.project_id == project.id)
        .first()
    )

    if not verrou:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verrou introuvable.",
        )

    allowed = {"garde", "rejete", "reformuler", "en_attente"}
    if payload.consultant_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Statut invalide. Valeurs autorisées : {sorted(allowed)}",
        )

    verrou.consultant_status = payload.consultant_status
    db.commit()
    db.refresh(verrou)
    return verrou


# ============================================================
# CIR précédent / mémoire CIR finale
# ============================================================

def _extract_text_from_cir_file(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")

    if suffix == ".docx":
        try:
            from docx import Document as DocxDocument
        except Exception as exc:
            raise RuntimeError("python-docx est requis pour lire les fichiers DOCX.") from exc

        doc = DocxDocument(str(path))
        parts: list[str] = []

        for p in doc.paragraphs:
            txt = (p.text or "").strip()
            if txt:
                parts.append(txt)

        for table in doc.tables:
            for row in table.rows:
                cells = [clean for cell in row.cells if (clean := (cell.text or "").strip())]
                if cells:
                    parts.append(" | ".join(cells))

        return "\n".join(parts)

    if suffix == ".pdf":
        # On essaie d'abord pypdf, puis PyPDF2 si l'environnement l'utilise.
        reader = None
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
        except Exception:
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(str(path))
            except Exception as exc:
                raise RuntimeError("pypdf ou PyPDF2 est requis pour lire les fichiers PDF.") from exc

        parts = []
        for page in reader.pages:
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            if txt.strip():
                parts.append(txt.strip())
        return "\n".join(parts)

    raise RuntimeError("Format non supporté. Utilise PDF, DOCX ou TXT pour le CIR final précédent.")


def _role_pack_for_cir_text(text: str) -> str:
    low = text.lower()

    if any(k in low for k in ["verrou", "incertitude", "difficulté", "difficulte", "problématique", "problematique", "non transfér", "non transfer", "non transposable"]):
        return "verrous_rnd_locaux"

    if any(k in low for k in ["objectif", "performances à atteindre", "performances a atteindre", "vise à", "vise a", "débit", "debit", "300 bars", "point de rosée", "point de rosee"]):
        return "objectifs_locaux"

    if any(k in low for k in ["état de l’art", "etat de l'art", "littérature", "litterature", "brevet", "article scientifique", "solutions existantes", "insuffisance"]):
        return "etat_art_local"

    if any(k in low for k in ["essai", "essais", "simulation", "mesure", "relevé", "releve", "modélisation", "modelisation", "calcul", "analyse", "prototype", "développement", "developpement"]):
        return "methodes_locales"

    if any(k in low for k in ["résultat", "resultat", "conclusion", "montré", "montre", "constaté", "constate", "permis", "atteint", "réduit", "reduit", "validé", "valide"]):
        return "resultats_locaux"

    if any(k in low for k in ["contrainte", "exigence", "limite", "non conforme", "insuffisant", "risque"]):
        return "limites_locales"

    if re.search(r"\b\d+(?:[,.]\d+)?\s*(bar|bars|kg|mm|°c|db|hz|rpm|m3/h|%)\b", low):
        return "parametres_locaux"

    return "contributions_locales"


def _split_cir_final_into_items(text: str, filename: str) -> Dict[str, Any]:
    """
    Extraction légère pour mémoire CIR final : pas de Frascati, pas de détection de verrous nouveaux.
    On structure seulement les passages du CIR final N-1 pour que la comparaison N vs N-1 puisse fonctionner.
    """
    pack_keys = [
        "objectifs_locaux",
        "verrous_rnd_locaux",
        "methodes_locales",
        "resultats_locaux",
        "limites_locales",
        "contributions_locales",
        "etat_art_local",
        "parametres_locaux",
    ]
    role_by_pack = {
        "objectifs_locaux": "objectif",
        "verrous_rnd_locaux": "verrou",
        "methodes_locales": "methode",
        "resultats_locaux": "resultat",
        "limites_locales": "limite",
        "contributions_locales": "contribution",
        "etat_art_local": "etat_art",
        "parametres_locaux": "parametre",
    }

    pack: Dict[str, list[dict[str, Any]]] = {k: [] for k in pack_keys}
    items: list[dict[str, Any]] = []

    cleaned = re.sub(r"\r\n?", "\n", text or "")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    paragraphs = [p.strip() for p in re.split(r"\n{2,}|(?=^\d+(?:\.\d+)*\.\s+)", cleaned, flags=re.M)]

    current_title = "CIR final précédent"
    counter = 0
    seen: set[str] = set()

    for raw in paragraphs:
        p = raw.strip()
        if not p:
            continue

        first_line = p.split("\n", 1)[0].strip()
        if re.match(r"^\d+(?:\.\d+)*\.?\s+.{4,120}$", first_line):
            current_title = first_line[:180]

        # On évite les pages, pieds de page et bouts trop courts.
        p = re.sub(r"(?i).*confidentiel\s*page\s*\d+", "", p).strip()
        p = re.sub(r"(?i)Ce document est la propriété.*", "", p).strip()
        if len(p) < 80:
            continue

        key = re.sub(r"\W+", " ", p.lower())[:260]
        if key in seen:
            continue
        seen.add(key)

        pack_key = _role_pack_for_cir_text(p)
        role = role_by_pack.get(pack_key, "general")
        counter += 1

        item = {
            "id": f"cir_prev_{counter}",
            "role": role,
            "pack_key": pack_key,
            "text": p[:2500],
            "document": filename,
            "section_title": current_title,
            "section_type": "cir_final_precedent",
            "section_label": current_title,
            "source_type": "previous_cir_final_without_frascati",
            "content_origin": "cir_final_uploaded_by_consultant",
            "quality_status": "memory_only_no_frascati",
        }
        pack[pack_key].append(item)
        items.append(item)

        if len(items) >= 180:
            break

    return {"pack": pack, "items": items}


def _roles_count(items: list[dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        role = str(item.get("role") or "unknown")
        out[role] = out.get(role, 0) + 1
    return dict(sorted(out.items()))


@router.post("/projects/{project_id}/cir-previous/upload-final")
def upload_previous_cir_final(
    project_id: int,
    year: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Ajoute un CIR final N-1 comme mémoire CIR.

    Important : ce fichier n'est PAS traité comme document brut de l'année courante.
    Il sert uniquement à comparer le projet courant avec le CIR final précédent.
    """
    project = get_project_for_user(db, project_id, current_user)
    year = str(year or "").strip()

    if not re.fullmatch(r"\d{4}", year):
        raise HTTPException(status_code=400, detail="L'année du CIR précédent doit être au format YYYY, par exemple 2022.")

    if str(year) == str(project.year):
        raise HTTPException(status_code=400, detail="Le CIR précédent doit avoir une année différente de l'année du projet courant.")

    ensure_ennosmart_imports()

    try:
        from modules.CIR_MEMORY.cir_memory import cir_final_dir, cir_final_report_path, comparison_report_path, compare_current_raw_with_cir_memory, write_json
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Module CIR_MEMORY indisponible : {exc}")

    safe_name = _safe_upload_filename(file.filename or f"cir_final_{year}")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt"}:
        raise HTTPException(status_code=400, detail="Format non supporté. Utilise PDF, DOCX ou TXT.")

    raw_dir = cir_final_dir(project.organisme, project.project_name, year) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / safe_name

    with raw_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        text = _extract_text_from_cir_file(raw_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lecture du CIR final impossible : {exc}")

    if len((text or "").strip()) < 200:
        raise HTTPException(status_code=400, detail="Le texte extrait du CIR final est trop court. Vérifie que le fichier n'est pas scanné sans OCR.")

    structured = _split_cir_final_into_items(text, safe_name)
    items = structured["items"]
    pack = structured["pack"]

    if not items:
        raise HTTPException(status_code=400, detail="Aucun passage exploitable n'a été extrait du CIR final précédent.")

    report = {
        "ok": True,
        "version": "cir_previous_upload_front_backend_v40",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": project.organisme,
        "project": project.project_name,
        "year": year,
        "current_project_year": str(project.year),
        "cir_final_file": str(raw_path),
        "rule": "CIR final précédent = mémoire CIR uniquement, sans FrascatiGuard et sans injection comme document brut courant.",
        "items_count": len(items),
        "roles": _roles_count(items),
        "items": items,
        "evidence_pack_before_frascati": pack,
    }

    out_path = cir_final_report_path(project.organisme, project.project_name, year)
    write_json(out_path, sanitize_json_value(report))

    # Écrit aussi un nlp_result mémoire pour audit humain.
    nlp_memory_path = cir_final_dir(project.organisme, project.project_name, year) / "cir_final_nlp_memory.json"
    write_json(nlp_memory_path, sanitize_json_value({
        "ok": True,
        "pipeline_type": "cir_final_memory_without_frascati",
        "source_file": str(raw_path),
        "evidence_pack_before_frascati": pack,
        "items": items,
    }))

    comparison = None
    try:
        current_nlp = diagnostic_paths(project)["nlp_result"]
        if current_nlp.exists():
            comparison = compare_current_raw_with_cir_memory(
                organisme=project.organisme,
                project=project.project_name,
                year=str(project.year),
                nlp_result_path=current_nlp,
            )
    except Exception as exc:
        comparison = {
            "ok": False,
            "error": str(exc),
            "note": "Le CIR précédent est enregistré. Relance Préparer les sources puis EnnoDiagnostic pour recalculer la comparaison.",
        }

    return sanitize_json_value({
        "ok": True,
        "message": "CIR final précédent enregistré comme mémoire CIR.",
        "previous_cir_year": year,
        "file": str(raw_path),
        "report_path": str(out_path),
        "items_count": len(items),
        "roles": _roles_count(items),
        "comparison_after_upload": comparison,
    })


@router.get("/projects/{project_id}/cir-previous")
def list_previous_cir_finals(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    ensure_ennosmart_imports()

    try:
        from modules.CIR_MEMORY.cir_memory import STORAGE_DIR, slug, cir_final_report_path
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Module CIR_MEMORY indisponible : {exc}")

    years_root = STORAGE_DIR / slug(project.organisme) / "projects" / slug(project.project_name) / "years"
    if not years_root.exists():
        return {"ok": True, "items": []}

    items = []
    for year_dir in sorted([p for p in years_root.iterdir() if p.is_dir()], reverse=True):
        year = year_dir.name
        if str(year) == str(project.year):
            continue
        report_path = cir_final_report_path(project.organisme, project.project_name, year)
        if not report_path.exists():
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            report = {}
        items.append({
            "year": year,
            "ok": bool(report.get("ok")),
            "items_count": report.get("items_count"),
            "roles": report.get("roles") or {},
            "file": report.get("cir_final_file"),
            "report_path": str(report_path),
            "generated_at": report.get("generated_at"),
        })

    return sanitize_json_value({"ok": True, "items": items})
