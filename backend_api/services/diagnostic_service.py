from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import math
import os
import re
import shutil
import sys

from sqlalchemy.orm import Session, undefer

from core.config import settings
from db.models import DiagnosticRun, Project, Verrou
from services.file_service import load_json_file, project_output_dir, run_optional_ai_script

try:
    from db.models import Document
except Exception:  # pragma: no cover
    Document = None  # type: ignore


# ============================================================
# JSON safety PostgreSQL
# ============================================================

def sanitize_json_value(value: Any) -> Any:
    """PostgreSQL refuse NaN / Infinity dans JSONB."""
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): sanitize_json_value(v) for k, v in value.items() if k is not None}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_json_value(v) for v in value]
    return str(value)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize_json_value(data), ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# EnnoSmart AI imports / paths
# ============================================================

def ennosmart_base_dir() -> Path:
    return Path(os.getenv("ENNOSMART_BASE_DIR", r"C:\EnnoSmart"))


def ensure_ennosmart_imports() -> Path:
    base_dir = ennosmart_base_dir()
    for candidate in [base_dir, base_dir / "backend_api"]:
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    return base_dir


def _slug(value: Any, default: str = "unknown") -> str:
    value = str(value or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    value = value.translate(tr)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:80] or default


def _safe_segment(value: Any, default: str = "unknown") -> str:
    value = str(value or "").strip() or default
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    return re.sub(r"\s+", " ", value)


def _year(project: Project) -> str:
    raw = str(project.year or "").strip()
    return raw or str(datetime.now().year)


def get_project_store(project: Project):
    ensure_ennosmart_imports()
    from modules.RAG.project_store import ProjectStore

    return ProjectStore(
        organisme=project.organisme,
        project=project.project_name,
        year=_year(project),
    ).ensure()


# ============================================================
# Documents uploadés → storage IA
# ============================================================

def get_uploaded_document_paths(db: Session, project: Project) -> List[str]:
    """
    Compatibilité ancienne : retourne les chemins disque si certains documents
    existent encore physiquement sur disque.
    """
    paths: List[str] = []
    if Document is None:
        return paths

    docs = (
        db.query(Document)
        .filter(Document.project_id == project.id)
        .order_by(Document.created_at.asc())
        .all()
    )

    for doc in docs:
        file_path = getattr(doc, "file_path", None)
        if file_path and not str(file_path).startswith("db://") and Path(str(file_path)).exists():
            paths.append(str(file_path))

    return paths


def copy_uploaded_docs_to_project_store(db: Session, project: Project) -> List[str]:
    """
    Source officielle des documents = table documents.

    Cas nouveau :
    - documents.file_data contient le fichier complet BYTEA
    - on reconstruit les fichiers dans ProjectStore.documents_raw_dir
    - le NLP/RAG continue ensuite à travailler sur des vrais fichiers locaux

    Cas ancien :
    - si file_path pointe encore vers un fichier disque réel, on le copie aussi.
    """
    ps = get_project_store(project)
    ps.documents_raw_dir.mkdir(parents=True, exist_ok=True)

    copied: List[str] = []

    if Document is None:
        return copied

    docs = (
        db.query(Document)
        .options(undefer(Document.file_data))
        .filter(Document.project_id == project.id)
        .order_by(Document.created_at.asc())
        .all()
    )

    for doc in docs:
        stored_name = getattr(doc, "stored_filename", None) or getattr(doc, "filename", None) or f"document_{doc.id}"
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", str(stored_name)).strip() or f"document_{doc.id}"
        dst = ps.documents_raw_dir / safe_name

        file_data = getattr(doc, "file_data", None)

        if file_data:
            dst.write_bytes(bytes(file_data))
            copied.append(str(dst))
            continue

        file_path = getattr(doc, "file_path", None)
        if file_path and not str(file_path).startswith("db://"):
            src_path = Path(str(file_path))
            if src_path.exists() and src_path.is_file():
                if src_path.resolve() != dst.resolve():
                    shutil.copy2(src_path, dst)
                copied.append(str(dst))

    print(f"✅ Documents reconstruits depuis PostgreSQL vers raw : {len(copied)}")
    for path in copied[:20]:
        print(f"   - {path}")

    return copied

# ============================================================
# Extensions supportées par le router d'extraction
# ============================================================

def _supported_raw_extensions_from_extraction_router() -> set[str]:
    """
    Liste officielle des extensions acceptées par EnnoDiagnostic.

    Important :
    - le backend ne doit pas avoir une petite liste manuelle (.pdf/.docx seulement) ;
    - il lit directement ce que modules/extraction/router.py expose ;
    - fallback complet si l'import du router échoue.
    """
    ensure_ennosmart_imports()

    fallback = {
        # PDF
        ".pdf",

        # Office / documents
        ".docx", ".doc", ".docm",
        ".pptx", ".ppt", ".pptm",
        ".xlsx", ".xls", ".xlsm", ".csv",

        # Texte / données
        ".txt", ".md", ".json",

        # Emails
        ".eml", ".msg",

        # Images
        ".png", ".jpg", ".jpeg", ".tiff", ".tif",
        ".bmp", ".gif", ".webp", ".svg",

        # Audio
        ".mp3", ".wav", ".m4a", ".aac", ".flac",
        ".ogg", ".opus", ".wma",

        # Vidéo
        ".mp4", ".mov", ".avi", ".mkv", ".webm",
        ".mpeg", ".mpg", ".3gp",
    }

    try:
        from modules.extraction.router import EXTENSION_MAP, AUDIO_VIDEO_EXTENSIONS

        exts = set(EXTENSION_MAP.keys()) | set(AUDIO_VIDEO_EXTENSIONS)

        # Extensions utiles pour debug / exports texte.
        exts |= {".txt", ".md", ".json"}

        # Formats Office macro. Même si le router les détecte parfois par magic bytes,
        # on les accepte explicitement côté backend.
        exts |= {".docm", ".pptm"}

        return {str(ext).lower() for ext in exts if str(ext).startswith(".")}

    except Exception:
        return fallback


def _is_supported_raw_document(path: Path, allowed_ext: set[str]) -> bool:
    """Filtre robuste des fichiers bruts avant load_documents()."""
    if not path.is_file():
        return False

    name = path.name.strip()
    low = name.lower()

    # Fichiers temporaires Office / OS.
    if name.startswith("~$"):
        return False

    if low in {"thumbs.db", ".ds_store", "desktop.ini"}:
        return False

    return path.suffix.lower() in allowed_ext

# ============================================================
# Diagnostic paths
# ============================================================

def _report_candidates(project: Project, output_dir: Optional[Path] = None) -> List[Path]:
    output_dir = output_dir or project_output_dir(project)
    base_dir = ennosmart_base_dir()
    org_raw = _safe_segment(project.organisme)
    proj_raw = _safe_segment(project.project_name)
    year_raw = _year(project)
    org_slug = _slug(project.organisme, "organisme")
    proj_slug = _slug(project.project_name, "projet")
    year_slug = _slug(year_raw, "year")

    candidates: List[Path] = [
        output_dir / "ennodiagnostic_report.json",
        output_dir / "diagnostic_ennodiagnostic.json",
        output_dir / "diagnostic_ennodiagnostic_sections.json",
        output_dir / "ennodiagnostic" / "ennodiagnostic_report.json",
        output_dir / "diagnostics" / "ennodiagnostic_report.json",
        output_dir / "diagnostics" / "diagnostic_ennodiagnostic.json",
        output_dir / "diagnostics" / "diagnostic_ennodiagnostic_sections.json",

        # Nouveau ProjectStore avec année
        base_dir / "storage" / "organismes" / org_slug / "projects" / proj_slug / "years" / year_slug / "ennodiagnostic" / "ennodiagnostic_report.json",
        base_dir / "storage" / "organismes" / org_slug / "projects" / proj_slug / "years" / year_slug / "diagnostics" / "ennodiagnostic_report.json",
        base_dir / "storage" / "organismes" / org_slug / "projects" / proj_slug / "years" / year_slug / "diagnostics" / "diagnostic_ennodiagnostic.json",
        base_dir / "storage" / "organismes" / org_slug / "projects" / proj_slug / "years" / year_slug / "diagnostics" / "diagnostic_ennodiagnostic_sections.json",

        # Ancien storage sans année
        base_dir / "storage" / "organismes" / org_slug / "projects" / proj_slug / "diagnostics" / "ennodiagnostic_report.json",
        base_dir / "storage" / "organismes" / org_slug / "projects" / proj_slug / "diagnostics" / "diagnostic_ennodiagnostic.json",
        base_dir / "storage" / "organismes" / org_slug / "projects" / proj_slug / "diagnostics" / "diagnostic_ennodiagnostic_sections.json",

        # Ancien Streamlit / safe_rag_upload
        base_dir / "outputs" / "safe_rag_upload" / org_raw / proj_raw / year_raw / "ennodiagnostic" / "ennodiagnostic_report.json",
        base_dir / "outputs" / "safe_rag_upload" / org_raw / proj_raw / year_raw / "ennodiagnostic_report.json",
        base_dir / "outputs" / "safe_rag_upload" / org_raw / proj_raw / year_raw / "diagnostics" / "diagnostic_ennodiagnostic.json",
    ]

    seen = set()
    out: List[Path] = []
    for p in candidates:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _first_existing(paths: List[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists() and path.is_file():
            return path
    return None


def diagnostic_paths(project: Project) -> Dict[str, Path]:
    output_dir = project_output_dir(project)
    report_existing = _first_existing(_report_candidates(project, output_dir))

    try:
        ps = get_project_store(project)
        nlp_result = ps.nlp_dir / "nlp_result.json"
        rag_chunks = ps.rag_dir / "chunks.json"
        default_report = ps.diagnostics_dir / "ennodiagnostic_report.json"
    except Exception:
        nlp_result = output_dir / "nlp_result.json"
        rag_chunks = output_dir / "rag" / "chunks.json"
        default_report = output_dir / "ennodiagnostic" / "ennodiagnostic_report.json"

    return {
        "output_dir": output_dir,
        "report": report_existing or default_report,
        "nlp_result": nlp_result,
        "rag_chunks": rag_chunks,
        "selected_verrous": output_dir / "selected_verrous_for_scholar.json",
        "comparison_cir_vs_raw": output_dir / "comparison_cir_vs_raw.json",
        "rag_report": output_dir / "rag_report.json",
    }


def read_diagnostic_bundle(project: Project) -> Dict[str, Any]:
    paths = diagnostic_paths(project)
    existing_reports = [str(p) for p in _report_candidates(project, paths["output_dir"]) if p.exists() and p.is_file()]

    return sanitize_json_value({
        "output_dir": str(paths["output_dir"]),
        "report": load_json_file(paths["report"]),
        "report_path_used": str(paths["report"]) if paths["report"].exists() else None,
        "report_candidates_found": existing_reports,
        "nlp_result": load_json_file(paths["nlp_result"]),
        "nlp_path_used": str(paths["nlp_result"]) if paths["nlp_result"].exists() else None,
        "rag_chunks_path": str(paths["rag_chunks"]) if paths["rag_chunks"].exists() else None,
        "selected_verrous": load_json_file(paths["selected_verrous"]),
        "comparison_cir_vs_raw": load_json_file(paths["comparison_cir_vs_raw"]),
        "rag_report": load_json_file(paths["rag_report"]),
        "files_found": {
            "report": paths["report"].exists(),
            "nlp_result": paths["nlp_result"].exists(),
            "rag_chunks": paths["rag_chunks"].exists(),
            "selected_verrous": paths["selected_verrous"].exists(),
            "comparison_cir_vs_raw": paths["comparison_cir_vs_raw"].exists(),
            "rag_report": paths["rag_report"].exists(),
        },
    })


# ============================================================
# Full AI pipeline
# ============================================================

def run_nlp_and_rag(db: Session, project: Project) -> Dict[str, Any]:
    """
    Lance :
    documents bruts → extraction/load_documents → NLP routed → FrascatiGuard → RAG indexation Chroma.

    Correction V14 :
    - ne prend pas seulement les fichiers enregistrés en base PostgreSQL ;
    - prend tous les vrais documents présents dans ProjectStore.documents_raw_dir ;
    - évite donc le cas où seuls Détails_*.txt et Explications_*.txt sont analysés ;
    - logge clairement le nombre de documents utilisés.
    """
    ensure_ennosmart_imports()

    from modules.NLP.document_loader import load_documents
    from modules.NLP.pipeline_route import run_nlp_pipeline_routed
    from modules.RAG.indexer import index_nlp_result

    ps = get_project_store(project)

    # 1) On copie quand même les documents uploadés depuis la DB vers storage/.../documents/raw.
    copied_from_db = copy_uploaded_docs_to_project_store(db, project)

    # 2) Mais la source principale devient le dossier raw complet du projet.
    # La liste vient du router d'extraction EnnoSmart pour accepter les mêmes types :
    # PDF, Office, Excel, emails, images, audio, vidéo, etc.
    allowed_ext = _supported_raw_extensions_from_extraction_router()

    raw_paths: List[str] = []
    skipped_paths: List[str] = []

    if ps.documents_raw_dir.exists():
        for path in ps.documents_raw_dir.rglob("*"):
            if _is_supported_raw_document(path, allowed_ext):
                raw_paths.append(str(path))
            elif path.is_file():
                skipped_paths.append(str(path))

    # 3) Dédoublonnage + tri stable.
    raw_paths = sorted(list(dict.fromkeys(raw_paths)))

    if not raw_paths:
        raise RuntimeError(
            f"Aucun document brut supporté trouvé dans : {ps.documents_raw_dir}. "
            f"Extensions acceptées : {sorted(allowed_ext)}"
        )

    print("=" * 80)
    print(f"📄 EnnoDiagnostic - documents bruts utilisés : {len(raw_paths)}")
    print(f"📁 Dossier raw : {ps.documents_raw_dir}")
    print(f"✅ Extensions acceptées par extraction router : {', '.join(sorted(allowed_ext))}")

    for p in raw_paths[:30]:
        print(f"   - {p}")

    if len(raw_paths) > 30:
        print(f"   ... +{len(raw_paths) - 30} autres fichiers")

    if skipped_paths:
        print(f"⚠️ Fichiers ignorés car extension non supportée : {len(skipped_paths)}")
        for p in skipped_paths[:10]:
            print(f"   - {p}")

    print("=" * 80)

    # 4) Extraction documents.
    documents = load_documents(
        raw_paths,
        use_ennosmart_extraction=True,
        include_cir_final=False,
    )

    if not documents:
        raise RuntimeError("Aucun texte exploitable extrait des documents bruts.")

    print(f"✅ Documents chargés par l'extraction : {len(documents)}")

    # 5) NLP routed + FrascatiGuard.
    nlp_result = run_nlp_pipeline_routed(
        documents=documents,
        document_modes=None,
        max_candidates=int(os.getenv("ENNOSMART_NLP_MAX_CANDIDATES", "700")),
        include_state_of_art_in_candidates=True,
    )
    nlp_result = sanitize_json_value(nlp_result)

    stats = nlp_result.get("stats", {}) if isinstance(nlp_result, dict) else {}
    print(
        "✅ NLP terminé : "
        f"candidats={stats.get('raw_candidates')}, "
        f"kept={stats.get('raw_kept')}, "
        f"verrous={stats.get('merged_verrous')}"
    )

    # 6) Indexation RAG / Chroma.
    index_report = index_nlp_result(
        organisme=project.organisme,
        project=project.project_name,
        nlp_result=nlp_result,
        reset=True,
        year=_year(project),
    )
    print(
        "✅ RAG indexé : "
        f"chunks_prepared={index_report.get('chunks_prepared')}, "
        f"chunks_indexed={index_report.get('chunks_indexed')}"
    )

    return sanitize_json_value({
        "documents_copied_from_db": copied_from_db,
        "documents_used_paths": raw_paths,
        "documents_used_count": len(raw_paths),
        "documents_loaded_count": len(documents),
        "documents_skipped_paths": skipped_paths,
        "documents_skipped_count": len(skipped_paths),
        "allowed_extensions": sorted(allowed_ext),
        "nlp_result": nlp_result,
        "index_report": index_report,
    })


def _legacy_agent_project_root(project: Project) -> Path:
    """
    Racine attendue par l'ancien ai_content_detector.py de l'agent :
    C:\EnnoSmart\storage\organismes\{org}\projects\{project}

    Le backend récent travaille avec :
    C:\EnnoSmart\storage\organismes\{org}\projects\{project}\years\{year}

    On adapte le backend pour fournir au détecteur IA de l'agent ses fichiers
    au format qu'il attend, sans modifier l'agent.
    """
    base_dir = ennosmart_base_dir()
    return (
        base_dir
        / "storage"
        / "organismes"
        / _slug(project.organisme, "organisme")
        / "projects"
        / _slug(project.project_name, "projet")
    )


def _copy_if_exists(src: Path, dst: Path) -> bool:
    try:
        if src.exists() and src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return True
    except Exception:
        pass
    return False


def _mirror_year_files_for_agent_ai(project: Project) -> Dict[str, Any]:
    """
    Adaptation backend -> agent, sans toucher à l'agent.

    L'agent IA lit :
    - project_root/nlp/nlp_result.json
    - project_root/rag/chunks.json
    - project_root/documents/processed

    Le backend met les vrais fichiers dans years/{year}.
    Donc on copie les fichiers utiles depuis years/{year} vers l'ancien root projet.
    """
    ps = get_project_store(project)
    legacy_root = _legacy_agent_project_root(project)

    report: Dict[str, Any] = {
        "policy": "backend_adapter_for_existing_agent_ai_detector",
        "year_root": str(ps.project_dir),
        "legacy_agent_root": str(legacy_root),
        "copied": [],
        "notes": [
            "Aucune modification de agents/EnnoDiagnostic/ai_content_detector.py.",
            "Le backend recopie les fichiers NLP/RAG au format attendu par l'agent.",
        ],
    }

    mappings = [
        (ps.nlp_dir / "nlp_result.json", legacy_root / "nlp" / "nlp_result.json"),
        (ps.rag_dir / "chunks.json", legacy_root / "rag" / "chunks.json"),
    ]

    for src, dst in mappings:
        ok = _copy_if_exists(src, dst)
        report["copied"].append({"src": str(src), "dst": str(dst), "ok": ok})

    # Processed dir optionnel : on le recopie seulement s'il existe.
    try:
        src_dir = ps.documents_processed_dir
        dst_dir = legacy_root / "documents" / "processed"
        if src_dir.exists() and src_dir.is_dir():
            dst_dir.mkdir(parents=True, exist_ok=True)
            count = 0
            for file in src_dir.rglob("*"):
                if file.is_file():
                    rel = file.relative_to(src_dir)
                    target = dst_dir / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, target)
                    count += 1
            report["processed_files_copied"] = count
        else:
            report["processed_files_copied"] = 0
    except Exception as e:
        report["processed_copy_error"] = str(e)

    return sanitize_json_value(report)


def _normalize_ai_report(raw: Dict[str, Any]) -> Dict[str, Any]:
    ai = raw.get("ai_detection") if isinstance(raw, dict) else {}
    ai = ai if isinstance(ai, dict) else {}
    return sanitize_json_value({
        "ok": True,
        "summary": {
            "average_ai_score": ai.get("global_ai_score"),
            "average_ai_percentage": ai.get("global_ai_percentage"),
            "risk_level": ai.get("risk_level"),
            "passages_count": ai.get("total_passages_analyzed"),
            "suspected_passages_count": ai.get("suspected_passages_count"),
            "high_count": ai.get("high_risk_passages_count"),
            "medium_count": ai.get("medium_risk_passages_count"),
            "low_count": ai.get("low_risk_passages_count"),
        },
        **raw,
    })


def run_ai_detector_if_enabled(project: Project, agent_out_dir: Path) -> Dict[str, Any]:
    """
    Lance le détecteur IA existant dans agents/EnnoDiagnostic.

    Important :
    - On ne modifie pas l'agent.
    - On ne passe pas year=... au service de l'agent.
    - Le backend prépare seulement les fichiers au format attendu par l'agent.
    """
    if os.getenv("ENNOSMART_RUN_AI_DETECTOR", "1").strip() != "1":
        return {"ok": False, "skipped": True, "message": "Détection IA désactivée."}

    ensure_ennosmart_imports()

    try:
        from agents.EnnoDiagnostic.ai_content_detector import EnnoAIDetectionService

        adapter_report = _mirror_year_files_for_agent_ai(project)

        # API originale de ton agent : pas de paramètre year.
        service = EnnoAIDetectionService(
            organisme=project.organisme,
            project=project.project_name,
            allow_rag_fallback=True,
        )

        raw_report = service.run(save=True)
        normalized = _normalize_ai_report(raw_report)
        normalized["backend_adapter"] = adapter_report

        # L'agent EnnoDiagnostic lit ce fichier via self.diagnostic_dir.
        save_json(agent_out_dir / "ennodiagnostic" / "ai_detection_report.json", normalized)

        # On garde aussi une copie backend/reporting.
        save_json(agent_out_dir / "diagnostics" / "ai_detection_report.json", normalized)

        # Et une copie dans le ProjectStore annuel pour le front/latest.
        try:
            ps = get_project_store(project)
            save_json(ps.diagnostics_dir / "ai_detection_report.json", normalized)
            save_json(ps.project_dir / "ennodiagnostic" / "ai_detection_report.json", normalized)
        except Exception:
            pass

        summary = normalized.get("summary") or {}
        print(
            "✅ Score IA documentaire : "
            f"score={summary.get('average_ai_percentage')}%, "
            f"niveau={summary.get('risk_level')}, "
            f"passages={summary.get('passages_count')}, "
            f"suspects={summary.get('suspected_passages_count')}"
        )

        return normalized

    except Exception as e:
        print(f"⚠ Score IA indisponible : {e}")
        return {"ok": False, "error": str(e), "message": "Score IA indisponible."}


def run_true_ennodiagnostic_agent(project: Project, prior_pipeline: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Chroma → EnnoDiagnosticAgent → LLM reformulation R&D/CIR."""
    ensure_ennosmart_imports()

    from agents.EnnoDiagnostic.ennodiagnostic_agent import EnnoDiagnosticAgent

    ps = get_project_store(project)
    out_dir = ps.project_dir

    ai_detection_runtime = run_ai_detector_if_enabled(project, out_dir)
    use_llm = os.getenv("ENNOSMART_DIAG_USE_LLM", "1").strip() != "0"

    agent = EnnoDiagnosticAgent(
        organisme=project.organisme,
        project=project.project_name,
        year=_year(project),
        out_dir=str(out_dir),
        use_llm=use_llm,
    )

    report = sanitize_json_value(agent.generate_diagnostic(save=True))
    report = sanitize_json_value({
        **report,
        "pipeline_before_agent": {
            "documents_loaded_count": (prior_pipeline or {}).get("documents_loaded_count"),
            "index_report": (prior_pipeline or {}).get("index_report"),
            "nlp_stats": ((prior_pipeline or {}).get("nlp_result") or {}).get("stats"),
        },
        "ai_detection_report_runtime": ai_detection_runtime,
    })

    save_json(ps.diagnostics_dir / "ennodiagnostic_report.json", report)
    save_json(ps.diagnostics_dir / "diagnostic_ennodiagnostic.json", report)
    return report


def run_full_ennodiagnostic_pipeline(db: Session, project: Project) -> Dict[str, Any]:
    nlp_rag = run_nlp_and_rag(db, project)
    report = run_true_ennodiagnostic_agent(project, prior_pipeline=nlp_rag)
    return sanitize_json_value({
        "nlp_rag": {
            "documents_loaded_count": nlp_rag.get("documents_loaded_count"),
            "index_report": nlp_rag.get("index_report"),
            "nlp_stats": (nlp_rag.get("nlp_result") or {}).get("stats"),
        },
        "report": report,
    })



# ============================================================
# Simple backend flow
# ============================================================

def _prepare_report_path(project: Project) -> Path:
    ps = get_project_store(project)
    return ps.diagnostics_dir / "prepare_sources_report.json"


def _load_prepare_report(project: Project) -> Dict[str, Any]:
    path = _prepare_report_path(project)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def prepare_ennodiagnostic_sources(db: Session, project: Project) -> Dict[str, Any]:
    """
    Étape 1 simple :
    documents/raw -> extraction -> NLP -> Frascati -> RAG/Chroma.

    Cette fonction ne lance PAS l'agent LLM.
    Elle prépare seulement les sources que l'agent EnnoDiagnostic va lire.
    """
    nlp_rag = run_nlp_and_rag(db, project)
    ps = get_project_store(project)

    result = sanitize_json_value({
        "ok": True,
        "step": "prepare_sources",
        "pipeline": "documents_raw_extraction_nlp_frascati_rag_chroma",
        "project": {
            "id": project.id,
            "organisme": project.organisme,
            "project_name": project.project_name,
            "year": _year(project),
        },
        "documents_used_count": nlp_rag.get("documents_used_count"),
        "documents_loaded_count": nlp_rag.get("documents_loaded_count"),
        "documents_used_paths": nlp_rag.get("documents_used_paths"),
        "nlp_stats": (nlp_rag.get("nlp_result") or {}).get("stats"),
        "index_report": nlp_rag.get("index_report"),
        "paths": {
            "project_dir": str(ps.project_dir),
            "raw_dir": str(ps.documents_raw_dir),
            "nlp_result": str(ps.nlp_dir / "nlp_result.json"),
            "rag_chunks": str(ps.rag_dir / "chunks.json"),
            "prepare_report": str(_prepare_report_path(project)),
        },
        "raw_pipeline_result": nlp_rag,
        "generated_at": datetime.utcnow().isoformat(),
    })

    save_json(_prepare_report_path(project), result)

    project.status = "Sources préparées"
    db.commit()

    return result


def run_ennodiagnostic_agent_only(db: Session, project: Project) -> DiagnosticRun:
    """
    Étape 2 simple :
    lance seulement l'agent EnnoDiagnostic depuis les sources déjà indexées dans Chroma.

    Cette fonction ne refait PAS extraction/NLP/RAG.
    Elle lit Chroma + score IA + mémoire CIR si disponibles, puis génère le rapport.
    """
    ps = get_project_store(project)

    # Vérification minimale : le RAG doit déjà exister.
    rag_chunks = ps.rag_dir / "chunks.json"
    nlp_result = ps.nlp_dir / "nlp_result.json"

    if not rag_chunks.exists() or not nlp_result.exists():
        raise RuntimeError(
            "Sources non préparées. Lance d'abord /diagnostic/prepare-sources "
            "ou utilise /diagnostic/run pour tout faire en une seule fois."
        )

    prepare_report = _load_prepare_report(project)
    prior_pipeline = prepare_report.get("raw_pipeline_result") if isinstance(prepare_report.get("raw_pipeline_result"), dict) else None

    report = run_true_ennodiagnostic_agent(project, prior_pipeline=prior_pipeline)

    paths = diagnostic_paths(project)

    run = DiagnosticRun(
        project_id=project.id,
        status="completed_agent_only",
        report_path=str(paths["report"]) if paths["report"].exists() else str(ps.diagnostics_dir / "ennodiagnostic_report.json"),
        nlp_result_path=str(nlp_result),
        selected_verrous_path=str(paths["selected_verrous"]) if paths["selected_verrous"].exists() else None,
        raw_result_json=sanitize_json_value({
            "button": "ennodiagnostic_agent_only",
            "pipeline": "chroma_score_ia_style_memory_llm_reformulation_ennodiagnostic_agent",
            "prepare_sources_report": prepare_report,
            "script_or_pipeline_result": {
                "report": report,
            },
            "bundle": read_diagnostic_bundle(project),
        }),
        completed_at=datetime.utcnow(),
    )

    db.add(run)
    project.status = "Diagnostic terminé"
    db.commit()
    db.refresh(run)

    return run


# ============================================================
# DB Run
# ============================================================

def create_diagnostic_run_from_files(db: Session, project: Project) -> DiagnosticRun:
    paths = diagnostic_paths(project)
    bundle = read_diagnostic_bundle(project)

    run = DiagnosticRun(
        project_id=project.id,
        status="completed_from_existing_files" if bundle.get("report") or bundle.get("nlp_result") else "no_result_found",
        report_path=str(paths["report"]) if paths["report"].exists() else None,
        nlp_result_path=str(paths["nlp_result"]) if paths["nlp_result"].exists() else None,
        selected_verrous_path=str(paths["selected_verrous"]) if paths["selected_verrous"].exists() else None,
        raw_result_json=sanitize_json_value(bundle),
        completed_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def run_ennodiagnostic(db: Session, project: Project) -> DiagnosticRun:
    """Un seul bouton : extraction → NLP → RAG → EnnoDiagnosticAgent."""
    output_dir = project_output_dir(project)
    output_dir.mkdir(parents=True, exist_ok=True)

    if getattr(settings, "ENNODIAGNOSTIC_SCRIPT", None):
        pipeline_result = run_optional_ai_script(
            script_path=settings.ENNODIAGNOSTIC_SCRIPT,
            args=[
                "--organisme", project.organisme,
                "--project", project.project_name,
                "--year", str(project.year),
                "--output-dir", str(output_dir),
            ],
            timeout_seconds=settings.AI_RUN_TIMEOUT_SECONDS,
        )
    else:
        pipeline_result = run_full_ennodiagnostic_pipeline(db, project)

    pipeline_result = sanitize_json_value(pipeline_result)
    run = create_diagnostic_run_from_files(db, project)
    run.raw_result_json = sanitize_json_value({
        "button": "single_ennodiagnostic_button",
        "pipeline": "extraction_nlp_frascati_rag_chroma_llm_ennodiagnostic",
        "script_or_pipeline_result": pipeline_result,
        "bundle": run.raw_result_json,
    })
    project.status = "Diagnostic terminé" if run.status != "no_result_found" else "Diagnostic à lancer"
    db.commit()
    db.refresh(run)
    return run

# ============================================================
# Sync verrous propres pour validation consultant
# ============================================================

def _clean_text(value: Any, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].strip()


def _to_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _normalize_verrou_score(value: Any) -> Optional[float]:
    """
    Normalise le score d'un verrou sur 0..1.

    Cas rencontrés :
    - 0.68  -> 0.68  (déjà normalisé)
    - 1.86  -> 0.93  (score agrégé V122 sur échelle ~0..2)
    - 93    -> 0.93  (pourcentage déjà calculé)
    """
    score = _to_float(value)
    if score is None or score < 0:
        return None
    if score <= 1:
        return score
    if score <= 2.5:
        return round(score / 2.0, 4)
    if score <= 100:
        return round(score / 100.0, 4)
    return None


def _score_percent(score: Optional[float]) -> Optional[float]:
    normalized = _normalize_verrou_score(score)
    if normalized is None:
        return None
    return round(normalized * 100, 2)


def _tag_from_score(score: Optional[float], decision: Optional[str] = None) -> str:
    if score is not None:
        if score >= 0.68:
            return "PERTINENT POUR CIR"
        if score >= 0.45:
            return "MOYEN POUR CIR"

    if decision:
        return decision

    return "À vérifier"


def _pick_report_from_run_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Récupère uniquement le rapport agent propre.
    On évite volontairement les NLP bruts, selected_verrous, candidates, etc.
    """
    candidates = [
        (((data.get("script_or_pipeline_result") or {}).get("report")) if isinstance(data.get("script_or_pipeline_result"), dict) else None),
        (((data.get("bundle") or {}).get("report")) if isinstance(data.get("bundle"), dict) else None),
        data.get("report"),
    ]

    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate

    return {}


def _is_fallback_llm_verrou_item(item: Dict[str, Any]) -> bool:
    source_json = item.get("source_json") if isinstance(item.get("source_json"), dict) else {}
    joined = " ".join(
        str(value or "")
        for value in [
            item.get("title"),
            item.get("justification"),
            item.get("text"),
            item.get("source"),
            source_json.get("source"),
            source_json.get("llm_block"),
        ]
    ).lower()

    return (
        "fallback_grouped_rag_verrou_synthesis" in joined
        or "signal technique candidat extrait des preuves rag/nlp" in joined
        or "la reformulation llm dédiée n'a pas produit de json exploitable" in joined
        or "la reformulation llm dediee n'a pas produit de json exploitable" in joined
    )


def _collect_clean_chroma_verrous_from_report(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Source prioritaire pour la validation consultant :
    report.llm_reformulated_verrous

    Pourquoi ?
    - Frascati/NLP donne des signaux bruts et des scores.
    - EnnoDiagnostic LLM reformule ces signaux en titres CIR lisibles.
    - Le consultant doit valider les titres reformulés, pas les thèmes bruts Chroma.

    Fallback conservé : report.chroma_sections.verrous si l'ancien agent ne fournit pas
    encore llm_reformulated_verrous.
    """

    def normalize_candidate(item: Dict[str, Any], source_name: str) -> Optional[Dict[str, Any]]:
        title = _clean_text(
            item.get("title")
            or item.get("llm_title")
            or item.get("verrou_title")
            or item.get("theme_label"),
            500,
        )
        if not title:
            return None

        if _is_fallback_llm_verrou_item(item):
            return None

        source_json = item.get("source_json") if isinstance(item.get("source_json"), dict) else dict(item)
        score = _normalize_verrou_score(
            item.get("verrou_score")
            or item.get("score")
            or item.get("frascati_score")
            or source_json.get("verrou_score")
            or source_json.get("score")
            or source_json.get("frascati_score")
        )
        decision = _clean_text(item.get("frascati_decision") or item.get("decision") or "verrou_a_verifier", 100)
        tag = item.get("tag_cir") or _tag_from_score(score, decision)

        source_json["validation_source"] = source_name
        source_json["display_title_source"] = "LLM reformulation" if source_name == "report.llm_reformulated_verrous" else "Chroma fallback"

        return sanitize_json_value({
            "title": title,
            "tag_cir": tag,
            "score": score,
            "justification": _clean_text(item.get("justification") or item.get("text") or item.get("llm_block"), 1800),
            "document": _clean_text(item.get("document") or "Sources Chroma", 800),
            "text": _clean_text(item.get("text") or item.get("justification") or "", 2500),
            "frascati_decision": decision,
            "consultant_status": item.get("consultant_status") or "a_valider",
            "source_json": sanitize_json_value(source_json),
        })

    # 1) Nouveau flux V122 : titres reformulés par l'agent EnnoDiagnostic.
    # Priorité absolue : les chunks RAG ne doivent pas remplacer ces verrous.
    llm_items = report.get("llm_reformulated_verrous") or report.get("consultant_verrous_cir")
    if isinstance(llm_items, list) and llm_items:
        clean: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in llm_items:
            if not isinstance(item, dict):
                continue
            normalized = normalize_candidate(item, "report.llm_reformulated_verrous")
            if not normalized:
                continue
            key = str(normalized.get("title") or "").lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            clean.append(normalized)
        if clean:
            return clean

    # 2) Fallback ancien flux : chunks Chroma rôle verrou.
    sections = report.get("chroma_sections")
    if not isinstance(sections, dict):
        return []

    raw_items = sections.get("verrous")
    if not isinstance(raw_items, list):
        return []

    clean: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}

        theme_id = _clean_text(meta.get("theme_id"), 150)
        theme_label = _clean_text(meta.get("theme_label"), 250)
        final_role = _clean_text(meta.get("final_role"), 250)

        title = theme_label or final_role
        if not title:
            text_preview = _clean_text(item.get("text") or item.get("source_text"), 180)
            title = text_preview or "Verrou à vérifier"

        dedupe_key = (theme_id or title).lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        text = _clean_text(item.get("text") or item.get("source_text"), 2000)
        document = _clean_text(meta.get("document") or item.get("document") or "Document non précisé", 500)
        decision = _clean_text(meta.get("frascati_decision") or meta.get("verrou_candidate_level"), 100)
        score = _normalize_verrou_score(meta.get("frascati_score") or meta.get("score") or meta.get("rank_score"))
        tag = _tag_from_score(score, decision)

        theme_question = _clean_text(meta.get("theme_question"), 1000)
        source_categories = _clean_text(meta.get("source_categories"), 500)
        supporting = meta.get("supporting_passages_count")

        justification_parts = []
        if theme_question:
            justification_parts.append(theme_question)
        if decision:
            justification_parts.append(f"Frascati : {decision}")
        if score is not None:
            justification_parts.append(f"Score Frascati : {_score_percent(score)}%")
        if source_categories:
            justification_parts.append(f"Catégories sources : {source_categories}")
        if supporting is not None:
            justification_parts.append(f"Passages sources : {supporting}")

        clean.append(
            sanitize_json_value(
                {
                    "title": title,
                    "tag_cir": tag,
                    "score": score,
                    "justification": " ; ".join(justification_parts) or text[:500],
                    "document": document,
                    "text": text,
                    "theme_id": theme_id,
                    "theme_label": theme_label,
                    "frascati_decision": decision,
                    "source_json": {
                        "source": "report.chroma_sections.verrous",
                        "validation_source": "fallback_chroma_sections_verrous",
                        "text": text,
                        "metadata": meta,
                    },
                }
            )
        )

    return clean

def sync_verrous_from_diagnostic(db: Session, run: DiagnosticRun) -> List[Verrou]:
    """
    Synchronisation propre pour l'onglet validation consultant.

    Correction :
    - avant : collectait report + nlp_result + selected_verrous + candidates,
      donc passages bruts et doublons entraient en base;
    - maintenant : prend en priorité report.llm_reformulated_verrous ;
    - fallback seulement si l'ancien agent ne fournit aucun verrou reformulé ;
    - supprime les anciens verrous du même run avant de recréer la liste propre.
    """
    data = sanitize_json_value(run.raw_result_json or {})
    if not isinstance(data, dict):
        data = {}

    report = _pick_report_from_run_data(data)
    candidates = _collect_clean_chroma_verrous_from_report(report)

    # Nettoyage du run courant pour éviter doublons si le frontend rappelle sync-verrous.
    db.query(Verrou).filter(Verrou.diagnostic_run_id == run.id).delete()
    db.flush()

    created: List[Verrou] = []

    for item in candidates:
        title = _clean_text(item.get("title"), 500)
        if not title:
            continue

        verrou = Verrou(
            diagnostic_run_id=run.id,
            title=title,
            tag_cir=item.get("tag_cir"),
            score=_to_float(item.get("score")),
            justification=item.get("justification"),
            source_json=sanitize_json_value(item.get("source_json") or item),
        )

        db.add(verrou)
        created.append(verrou)

    db.commit()

    for verrou in created:
        db.refresh(verrou)

    return created
