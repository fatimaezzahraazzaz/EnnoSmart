from __future__ import annotations

DIAGNOSTIC_SERVICE_VERSION = "v146_memory_safe_prepare_sources"

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import gc
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
    """
    V139 : retourne le rapport existant le plus récent, pas le premier candidat.

    Pourquoi :
    plusieurs chemins peuvent coexister :
    - ancien output_dir/diagnostics/...
    - nouveau ProjectStore years/<year>/ennodiagnostic/ennodiagnostic_report.json
    - nouveau ProjectStore years/<year>/diagnostics/ennodiagnostic_report.json

    Si on prend le premier chemin existant, le frontend peut afficher un ancien
    rapport même si l'agent vient de produire la bonne sortie.
    """
    existing: List[Path] = []
    for path in paths:
        try:
            if path.exists() and path.is_file():
                existing.append(path)
        except Exception:
            continue

    if not existing:
        return None

    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except Exception:
            return 0.0

    return max(existing, key=mtime)


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


def read_diagnostic_bundle(project: Project, *, compact: bool = False) -> Dict[str, Any]:
    paths = diagnostic_paths(project)
    existing_reports = [str(p) for p in _report_candidates(project, paths["output_dir"]) if p.exists() and p.is_file()]

    if compact:
        # La vue React est reconstruite depuis le rapport officiel. Charger ici
        # nlp_result/chunks/comparaisons (plusieurs dizaines de Mo) ne change pas
        # l'affichage et bloquait inutilement chaque ouverture de page.
        return sanitize_json_value({
            "output_dir": str(paths["output_dir"]),
            "report": load_json_file(paths["report"]),
            "report_path_used": str(paths["report"]) if paths["report"].exists() else None,
            "report_candidates_found": existing_reports,
            "nlp_path_used": str(paths["nlp_result"]) if paths["nlp_result"].exists() else None,
            "rag_chunks_path": str(paths["rag_chunks"]) if paths["rag_chunks"].exists() else None,
            "files_found": {
                "report": paths["report"].exists(),
                "nlp_result": paths["nlp_result"].exists(),
                "rag_chunks": paths["rag_chunks"].exists(),
                "selected_verrous": paths["selected_verrous"].exists(),
                "comparison_cir_vs_raw": paths["comparison_cir_vs_raw"].exists(),
                "rag_report": paths["rag_report"].exists(),
            },
        })

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
    Documents bruts -> extraction -> NLP/Frascati -> RAG/Chroma.

    V146 memory-safe :
    - le resultat NLP complet est ecrit une seule fois sur disque ;
    - il est utilise pour l'indexation, puis libere autant que possible ;
    - la valeur retournee au backend reste compacte (stats uniquement) ;
    - le frontend ne recoit jamais tous les passages/chunks NLP.
    """
    ensure_ennosmart_imports()

    from modules.NLP.document_loader import load_documents
    from modules.NLP.pipeline_route import run_nlp_pipeline_routed
    from modules.RAG.indexer import index_nlp_result

    ps = get_project_store(project)

    print("[prepare-sources][1/6] Reconstruction des documents", flush=True)
    copied_from_db = copy_uploaded_docs_to_project_store(db, project)

    allowed_ext = _supported_raw_extensions_from_extraction_router()
    raw_paths: List[str] = []
    skipped_paths: List[str] = []

    if ps.documents_raw_dir.exists():
        for path in ps.documents_raw_dir.rglob("*"):
            if _is_supported_raw_document(path, allowed_ext):
                raw_paths.append(str(path))
            elif path.is_file():
                skipped_paths.append(str(path))

    raw_paths = sorted(dict.fromkeys(raw_paths))

    if not raw_paths:
        raise RuntimeError(
            f"Aucun document brut supporte trouve dans : {ps.documents_raw_dir}. "
            f"Extensions acceptees : {sorted(allowed_ext)}"
        )

    print(
        f"[prepare-sources][2/6] Extraction de {len(raw_paths)} document(s)",
        flush=True,
    )

    documents = load_documents(
        raw_paths,
        use_ennosmart_extraction=True,
        include_cir_final=False,
    )

    if not documents:
        raise RuntimeError("Aucun texte exploitable extrait des documents bruts.")

    # V7 - conserver le texte complet extrait avant la réduction NLP.
    # Le chat RAG utilise ce corpus documentaire complet comme preuve primaire.
    try:
        from modules.RAG.full_document_corpus import persist_full_documents_for_chat
        full_document_corpus_report = persist_full_documents_for_chat(
            store=ps,
            documents=documents,
        )
        print(
            "[prepare-sources][fulltext] Corpus complet : "
            f"documents={full_document_corpus_report.get('documents_count')}, "
            f"chars={full_document_corpus_report.get('total_chars')}",
            flush=True,
        )
    except Exception as exc:
        # Le diagnostic principal reste disponible même si ce corpus auxiliaire
        # ne peut pas être écrit.
        print(f"[prepare-sources][fulltext] WARNING: {exc}", flush=True)

    documents_loaded_count = len(documents)
    print(
        f"[prepare-sources][3/6] NLP/Frascati sur {documents_loaded_count} document(s)",
        flush=True,
    )

    nlp_result_raw = run_nlp_pipeline_routed(
        documents=documents,
        document_modes=None,
        max_candidates=int(os.getenv("ENNOSMART_NLP_MAX_CANDIDATES", "700")),
        include_state_of_art_in_candidates=True,
    )

    # Les documents extraits ne sont plus necessaires apres le NLP.
    del documents
    gc.collect()

    if not isinstance(nlp_result_raw, dict):
        raise RuntimeError(
            "Le pipeline NLP n'a pas retourne un dictionnaire exploitable."
        )

    # Une seule normalisation JSON du resultat complet.
    nlp_result = sanitize_json_value(nlp_result_raw)
    del nlp_result_raw
    gc.collect()

    stats = (
        sanitize_json_value(nlp_result.get("stats") or {})
        if isinstance(nlp_result, dict)
        else {}
    )

    # Garantie explicite pour le bouton Agent-only.
    nlp_path = ps.nlp_dir / "nlp_result.json"
    print(
        f"[prepare-sources][4/6] Sauvegarde NLP : {nlp_path}",
        flush=True,
    )
    save_json(nlp_path, nlp_result)

    print("[prepare-sources][5/6] Indexation RAG / Chroma", flush=True)
    index_report_raw = index_nlp_result(
        organisme=project.organisme,
        project=project.project_name,
        nlp_result=nlp_result,
        reset=True,
        year=_year(project),
    )
    index_report = sanitize_json_value(index_report_raw or {})

    # Le gros resultat NLP reste sur disque. On ne le renvoie pas a FastAPI.
    del nlp_result
    gc.collect()

    print(
        "[prepare-sources][6/6] Termine : "
        f"candidats={stats.get('raw_candidates')}, "
        f"kept={stats.get('raw_kept')}, "
        f"verrous={stats.get('merged_verrous')}, "
        f"chunks={index_report.get('chunks_indexed')}",
        flush=True,
    )

    return {
        "documents_copied_from_db": copied_from_db,
        "documents_used_paths": raw_paths,
        "documents_used_count": len(raw_paths),
        "documents_loaded_count": documents_loaded_count,
        "documents_skipped_paths": skipped_paths,
        "documents_skipped_count": len(skipped_paths),
        "allowed_extensions": sorted(allowed_ext),
        # Compatibilite avec le reste du backend, sans contenu NLP massif.
        "nlp_result": {"stats": stats},
        "nlp_stats": stats,
        "nlp_result_path": str(nlp_path),
        "index_report": index_report,
    }

def _legacy_agent_project_root(project: Project) -> Path:
    r"""
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
# V142 — persistance complète et atomique du diagnostic
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


def _clean_text(value: Any, limit: int = 0) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def _section_key(title: Any) -> str:
    value = str(title or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    value = value.translate(tr)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_")


def _markdown_sections(markdown: str) -> tuple[Dict[str, str], Dict[str, str]]:
    markdown = _clean_text(markdown)
    if not markdown:
        return {}, {}

    matches = list(re.finditer(r"(?m)^#{2,6}\s+(.+?)\s*$", markdown))
    by_key: Dict[str, str] = {}
    by_title: Dict[str, str] = {}

    for index, match in enumerate(matches):
        title = _clean_text(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        if not title or not body:
            continue
        by_title[title] = body
        by_key[_section_key(title)] = body

    return by_key, by_title


def _merge_non_empty_text_dict(target: Dict[str, str], source: Any, normalize_keys: bool = False) -> None:
    if not isinstance(source, dict):
        return
    for key, value in source.items():
        if not isinstance(value, str) or not value.strip():
            continue
        final_key = _section_key(key) if normalize_keys else str(key)
        if final_key and not target.get(final_key):
            target[final_key] = value.strip()


def _extract_final_verrous_from_report(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extrait uniquement les verrous finaux acceptés par l'agent."""
    if not isinstance(report, dict):
        return []

    synthesis = report.get("verrou_synthesis_report")
    synthesis = synthesis if isinstance(synthesis, dict) else {}

    candidates = (
        synthesis.get("llm_reformulated_verrous")
        or synthesis.get("final_items")
        or synthesis.get("accepted_items")
        or synthesis.get("final_verrous")
        or report.get("llm_reformulated_verrous")
        or report.get("consultant_verrous_cir")
        or report.get("verrous_reformules")
        or []
    )

    if not isinstance(candidates, list):
        return []

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for raw in candidates:
        if not isinstance(raw, dict):
            continue

        source_json = raw.get("source_json") if isinstance(raw.get("source_json"), dict) else {}
        title = _clean_text(
            raw.get("title")
            or raw.get("titre")
            or raw.get("verrou")
            or raw.get("llm_title")
            or raw.get("verrou_title")
            or source_json.get("title"),
            500,
        )
        if not title:
            continue

        key = re.sub(r"\s+", " ", title.lower()).strip()
        if key in seen:
            continue
        seen.add(key)

        justification = _clean_text(
            raw.get("consultant_explanation")
            or raw.get("why_agent_found_verrou")
            or raw.get("why_not_simple_engineering")
            or raw.get("justification")
            or raw.get("description")
            or raw.get("scientific_lock")
            or raw.get("text")
            or source_json.get("consultant_explanation")
            or source_json.get("evidence_summary"),
            5000,
        )

        score = (
            raw.get("score")
            if raw.get("score") is not None
            else raw.get("frascati_score")
            if raw.get("frascati_score") is not None
            else raw.get("confidence")
            if raw.get("confidence") is not None
            else source_json.get("score")
        )

        try:
            score = float(score) if score is not None else None
            if score is not None and (math.isnan(score) or math.isinf(score)):
                score = None
            elif score is not None and score > 1 and score <= 2.5:
                score = score / 2.0
            elif score is not None and score > 2.5 and score <= 100:
                score = score / 100.0
        except Exception:
            score = None

        source_document = _clean_text(
            raw.get("source_document")
            or raw.get("document")
            or raw.get("filename")
            or source_json.get("source_document")
            or source_json.get("document"),
            1200,
        )

        out.append(sanitize_json_value({
            **raw,
            "title": title,
            "titre": title,
            "verrou": title,
            "description": justification,
            "justification": justification,
            "text": raw.get("text") or justification,
            "score": score,
            "source_document": source_document,
            "consultant_status": raw.get("consultant_status") or raw.get("status") or "en_attente",
            "needs_human_validation": True,
            "source_json": {
                **source_json,
                "full_agent_item": sanitize_json_value(raw),
                "source_document": source_document or source_json.get("source_document"),
                "consultant_explanation": raw.get("consultant_explanation") or source_json.get("consultant_explanation") or justification,
                "scientific_lock": raw.get("scientific_lock") or source_json.get("scientific_lock"),
                "why_not_simple_engineering": raw.get("why_not_simple_engineering") or source_json.get("why_not_simple_engineering"),
                "evidence_summary": raw.get("evidence_summary") or source_json.get("evidence_summary"),
                "sources": raw.get("sources") or source_json.get("sources"),
                "source_ids": raw.get("source_ids") or source_json.get("source_ids"),
                "persistence_version": "v143_complete_db_persistence",
            },
        }))

    return out


def extract_complete_diagnostic_snapshot(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Construit la représentation persistée complète du diagnostic.

    Rien n'est perdu :
    - rapport agent complet ;
    - Markdown complet ;
    - toutes les sections par clé et par titre ;
    - cartes ;
    - verrous finaux ;
    - Frascati, IA, mémoire, Chroma et traçabilité.
    """
    report = sanitize_json_value(report if isinstance(report, dict) else {})
    diagnostic = report.get("diagnostic") if isinstance(report.get("diagnostic"), dict) else {}
    static = report.get("static_diagnostic") if isinstance(report.get("static_diagnostic"), dict) else {}

    markdown = _clean_text(
        diagnostic.get("content")
        or report.get("report_markdown")
        or report.get("content")
        or static.get("markdown")
        or ""
    )

    markdown_by_key, markdown_by_title = _markdown_sections(markdown)
    sections_by_key: Dict[str, str] = {}
    sections_by_title: Dict[str, str] = {}

    _merge_non_empty_text_dict(sections_by_key, report.get("diagnostic_sections_by_key"), normalize_keys=True)
    _merge_non_empty_text_dict(sections_by_key, static.get("sections_by_key"), normalize_keys=True)
    _merge_non_empty_text_dict(sections_by_key, report.get("report_sections"), normalize_keys=True)
    _merge_non_empty_text_dict(sections_by_key, markdown_by_key, normalize_keys=True)

    _merge_non_empty_text_dict(sections_by_title, report.get("diagnostic_sections"))
    _merge_non_empty_text_dict(sections_by_title, static.get("sections"))
    _merge_non_empty_text_dict(sections_by_title, markdown_by_title)

    # Toutes les sections titrées sont aussi disponibles par clé normalisée.
    for title, body in sections_by_title.items():
        key = _section_key(title)
        if key and body and not sections_by_key.get(key):
            sections_by_key[key] = body

    aliases = {
        "lecture_frascati": ["lecture_frascati", "lecture_frascati_du_dossier"],
        "justification_frascati": ["justification_frascati", "justification_frascati_du_score", "justification_du_score_frascati"],
        "memoire_v2": ["memoire_v2"],
        "synthese_strategique": ["synthese_strategique", "synthese_strategique_du_projet", "synthese"],
        "objectif_global": ["objectif_global", "objectif_global_reformule", "objectif_global_du_projet", "objectif"],
        "verrous_rnd": ["verrous_rnd", "verrous_cir", "verrous_cir_consolides", "verrous_r_d_signaux_de_verrous", "verrous"],
        "demarche_detectee": ["demarche_detectee", "demarche_experimentale_detectee", "demarche_experimentale", "demarche"],
        "resultats_metriques": ["resultats_metriques", "resultats_et_metriques_disponibles", "resultats_metriques_disponibles", "resultats"],
        "parametres_contraintes": ["parametres_contraintes", "parametres_et_contraintes_techniques", "parametres_techniques", "parametres"],
        "points_validation": ["points_validation", "points_a_valider", "points_a_valider_par_le_consultant", "validation"],
    }

    canonical: Dict[str, str] = {}
    for canonical_key, candidates in aliases.items():
        for candidate in candidates:
            value = sections_by_key.get(candidate)
            if isinstance(value, str) and value.strip():
                canonical[canonical_key] = value.strip()
                break

    cards = report.get("diagnostic_cards") or static.get("cards") or []
    if not isinstance(cards, list):
        cards = []

    final_verrous = _extract_final_verrous_from_report(report)

    return sanitize_json_value({
        "snapshot_version": "v143_complete_db_persistence",
        "generated_at": report.get("generated_at") or datetime.utcnow().isoformat(),
        "mode": report.get("mode"),
        "status": report.get("status") or diagnostic.get("status"),
        "report_markdown": markdown,
        "sections_by_key": sections_by_key,
        "sections_by_title": sections_by_title,
        "canonical_sections": canonical,
        "sections_count": len(sections_by_key),
        "section_titles_count": len(sections_by_title),
        "diagnostic_cards": cards,
        "diagnostic_cards_count": len(cards),
        "final_verrous": final_verrous,
        "final_verrous_count": len(final_verrous),
        "frascati_summary": report.get("frascati_summary") or {},
        "frascati_justification": report.get("frascati_justification") or {},
        "ai_detection_report": report.get("ai_detection_report_runtime") or report.get("ai_detection_report") or {},
        "style_memory_report": report.get("style_memory_report") or {},
        "cir_memory_report": report.get("cir_memory_report") or {},
        "inputs_status": report.get("inputs_status") or {},
        "pipeline_before_agent": report.get("pipeline_before_agent") or {},
        "verrou_synthesis_report": report.get("verrou_synthesis_report") or {},
        "chroma_sections": report.get("chroma_sections") or {},
        "source_paths": {
            "output_path": report.get("output_path"),
        },
    })


def _report_from_pipeline_result(pipeline_result: Any) -> Dict[str, Any]:
    if not isinstance(pipeline_result, dict):
        return {}
    candidates = [
        pipeline_result.get("report"),
        (pipeline_result.get("script_or_pipeline_result") or {}).get("report") if isinstance(pipeline_result.get("script_or_pipeline_result"), dict) else None,
        (pipeline_result.get("bundle") or {}).get("report") if isinstance(pipeline_result.get("bundle"), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    if any(key in pipeline_result for key in ["verrou_synthesis_report", "diagnostic_sections", "static_diagnostic", "frascati_summary"]):
        return pipeline_result
    return {}


def _set_model_attr_if_exists(obj: Any, names: str | List[str], value: Any) -> None:
    if isinstance(names, str):
        names = [names]
    for name in names:
        if hasattr(obj, name):
            try:
                setattr(obj, name, value)
                return
            except Exception:
                pass


def _build_complete_run_payload(
    *,
    report: Dict[str, Any],
    project: Project,
    pipeline_name: str,
    button: str,
    prepare_report: Optional[Dict[str, Any]] = None,
    pipeline_result: Optional[Dict[str, Any]] = None,
    bundle_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snapshot = extract_complete_diagnostic_snapshot(report)
    return sanitize_json_value({
        "persistence_version": "v143_complete_db_persistence",
        "saved_at": datetime.utcnow().isoformat(),
        "button": button,
        "pipeline": pipeline_name,
        "project": {
            "id": project.id,
            "organisme": project.organisme,
            "project_name": project.project_name,
            "year": _year(project),
        },
        # Source officielle complète : aucune section n'est reconstruite depuis Chroma.
        "report": report,
        "script_or_pipeline_result": {
            "report": report,
            "pipeline_metadata": pipeline_result or {},
        },
        "diagnostic_snapshot": snapshot,
        "report_markdown": snapshot.get("report_markdown"),
        "report_sections": snapshot.get("canonical_sections") or {},
        "diagnostic_sections_by_key": snapshot.get("sections_by_key") or {},
        "diagnostic_sections": snapshot.get("sections_by_title") or {},
        "diagnostic_cards": snapshot.get("diagnostic_cards") or [],
        "final_verrous_snapshot": snapshot.get("final_verrous") or [],
        "prepare_sources_report": prepare_report or {},
        "bundle_metadata": bundle_metadata or {},
    })


def _previous_consultant_statuses(db: Session, project_id: int, current_run_id: Optional[int]) -> Dict[str, str]:
    query = (
        db.query(Verrou)
        .join(DiagnosticRun, Verrou.diagnostic_run_id == DiagnosticRun.id)
        .filter(DiagnosticRun.project_id == project_id)
    )
    if current_run_id is not None:
        query = query.filter(Verrou.diagnostic_run_id != current_run_id)
    rows = query.order_by(DiagnosticRun.created_at.desc(), Verrou.created_at.desc()).all()

    out: Dict[str, str] = {}
    for row in rows:
        title = re.sub(r"\s+", " ", str(getattr(row, "title", "") or "").lower()).strip()
        status = str(getattr(row, "consultant_status", "") or "").strip()
        if title and status and title not in out:
            out[title] = status
    return out


def _to_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _pick_report_from_run_data(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    candidates = [
        data.get("report"),
        (data.get("script_or_pipeline_result") or {}).get("report") if isinstance(data.get("script_or_pipeline_result"), dict) else None,
        (data.get("diagnostic_snapshot") or {}).get("report") if isinstance(data.get("diagnostic_snapshot"), dict) else None,
        (data.get("bundle") or {}).get("report") if isinstance(data.get("bundle"), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}



def sync_verrous_from_diagnostic(
    db: Session,
    run: DiagnosticRun,
    *,
    commit: bool = True,
    preserve_previous_decisions: bool = True,
) -> List[Verrou]:
    """
    V145 — synchronisation du snapshot du run courant, sans suppression
    de l'historique des autres DiagnosticRun.

    Pourquoi :
    - les anciens Verrou peuvent être référencés par Article.verrou_id ;
    - les supprimer provoque une ForeignKeyViolation PostgreSQL ;
    - EnnoScholar doit filtrer le dernier DiagnosticRun, pas détruire
      les anciennes lignes.
    """
    data = sanitize_json_value(run.raw_result_json or {})
    report = _pick_report_from_run_data(data if isinstance(data, dict) else {})
    candidates = _extract_final_verrous_from_report(report)

    if not candidates and isinstance(data, dict):
        snapshot = (
            data.get("diagnostic_snapshot")
            if isinstance(data.get("diagnostic_snapshot"), dict)
            else {}
        )
        snapshot_items = snapshot.get("final_verrous")
        if isinstance(snapshot_items, list):
            candidates = [
                item for item in snapshot_items if isinstance(item, dict)
            ]

    if not candidates:
        raise RuntimeError(
            "Aucun verrou final disponible dans le diagnostic courant."
        )

    # Inclut aussi le run courant afin qu'une resynchronisation conserve
    # les décisions déjà prises sur des titres identiques.
    previous_statuses = (
        _previous_consultant_statuses(
            db,
            int(run.project_id),
            None,
        )
        if preserve_previous_decisions
        else {}
    )

    # On remplace uniquement les lignes du run courant.
    # Les anciens runs restent intacts pour l'audit et leurs articles
    # conservent leurs clés étrangères valides.
    deleted_current_count = (
        db.query(Verrou)
        .filter(Verrou.diagnostic_run_id == int(run.id))
        .delete(synchronize_session=False)
    )
    db.flush()

    created: List[Verrou] = []

    for item in candidates:
        title = _clean_text(
            item.get("title")
            or item.get("titre")
            or item.get("verrou"),
            500,
        )
        if not title:
            continue

        source_json = (
            item.get("source_json")
            if isinstance(item.get("source_json"), dict)
            else {}
        )
        source_json = sanitize_json_value({
            **source_json,
            "full_persisted_verrou": item,
            "diagnostic_run_id": run.id,
            "project_id": run.project_id,
            "persistence_version": "v145_latest_only_without_history_delete",
            "snapshot_policy": "replace_current_run_only",
        })

        score = _to_float(item.get("score"))
        normalized_title = re.sub(r"\s+", " ", title.lower()).strip()
        status_value = (
            previous_statuses.get(normalized_title)
            or item.get("consultant_status")
            or item.get("status")
            or "en_attente"
        )
        if status_value not in {
            "garde",
            "rejete",
            "reformuler",
            "en_attente",
        }:
            status_value = "en_attente"

        verrou = Verrou(
            diagnostic_run_id=run.id,
            title=title,
            tag_cir=item.get("tag_cir") or item.get("decision") or "À valider",
            score=score,
            justification=_clean_text(
                item.get("justification")
                or item.get("description")
                or item.get("consultant_explanation")
                or item.get("text"),
                10000,
            ),
            source_json=source_json,
        )
        _set_model_attr_if_exists(verrou, "consultant_status", status_value)
        _set_model_attr_if_exists(verrou, "needs_human_validation", True)
        _set_model_attr_if_exists(
            verrou,
            ["source_document", "document"],
            item.get("source_document"),
        )
        _set_model_attr_if_exists(
            verrou,
            ["source_excerpt", "excerpt"],
            item.get("source_excerpt") or item.get("evidence_summary"),
        )

        db.add(verrou)
        created.append(verrou)

    db.flush()

    if not created:
        raise RuntimeError(
            "Le diagnostic contient des candidats, mais aucun verrou "
            "n'a pu être persisté."
        )

    payload = sanitize_json_value(run.raw_result_json or {})
    if isinstance(payload, dict):
        payload["verrous_db_sync"] = {
            "ok": True,
            "mode": "replace_current_run_only",
            "project_id": run.project_id,
            "run_id": run.id,
            "deleted_current_count": int(deleted_current_count or 0),
            "history_preserved": True,
            "count": len(created),
            "synced_at": datetime.utcnow().isoformat(),
            "items": [
                {
                    "id": getattr(verrou, "id", None),
                    "title": getattr(verrou, "title", None),
                    "consultant_status": getattr(
                        verrou,
                        "consultant_status",
                        "en_attente",
                    ),
                }
                for verrou in created
            ],
        }
        run.raw_result_json = payload

    print(
        "[EnnoDiagnostic][V145_CURRENT_RUN_SNAPSHOT] "
        f"project_id={run.project_id} "
        f"run_id={run.id} "
        f"deleted_current={int(deleted_current_count or 0)} "
        f"created_current={len(created)} "
        "history_preserved=true"
    )

    if commit:
        db.commit()
        db.refresh(run)
        for verrou in created:
            db.refresh(verrou)

    return created


def _persist_complete_run(
    db: Session,
    project: Project,
    *,
    report: Dict[str, Any],
    status_value: str,
    pipeline_name: str,
    button: str,
    report_path: Optional[str],
    nlp_result_path: Optional[str],
    selected_verrous_path: Optional[str],
    prepare_report: Optional[Dict[str, Any]] = None,
    pipeline_result: Optional[Dict[str, Any]] = None,
    bundle_metadata: Optional[Dict[str, Any]] = None,
) -> DiagnosticRun:
    if not isinstance(report, dict) or not report:
        raise RuntimeError("Rapport EnnoDiagnostic vide : rien à enregistrer en base.")

    payload = _build_complete_run_payload(
        report=report,
        project=project,
        pipeline_name=pipeline_name,
        button=button,
        prepare_report=prepare_report,
        pipeline_result=pipeline_result,
        bundle_metadata=bundle_metadata,
    )

    run = DiagnosticRun(
        project_id=project.id,
        status=status_value,
        report_path=report_path,
        nlp_result_path=nlp_result_path,
        selected_verrous_path=selected_verrous_path,
        raw_result_json=payload,
        completed_at=datetime.utcnow(),
    )

    # Compatibilité avec une future migration possédant des colonnes dédiées.
    snapshot = payload.get("diagnostic_snapshot") or {}
    _set_model_attr_if_exists(run, ["report_markdown", "content"], snapshot.get("report_markdown"))
    _set_model_attr_if_exists(run, ["report_sections_json", "diagnostic_sections_json"], snapshot.get("sections_by_key"))
    _set_model_attr_if_exists(run, ["display_json", "diagnostic_snapshot_json"], snapshot)

    try:
        db.add(run)
        db.flush()  # obtient run.id avant la création des Verrou
        synced = sync_verrous_from_diagnostic(db, run, commit=False)
        if not synced:
            raise RuntimeError(
                "Le rapport a été généré mais aucun verrou final n'a été trouvé dans "
                "verrou_synthesis_report.llm_reformulated_verrous."
            )
        project.status = "Diagnostic terminé"
        db.commit()
        db.refresh(run)
        return run
    except Exception:
        db.rollback()
        raise


def prepare_ennodiagnostic_sources(db: Session, project: Project) -> Dict[str, Any]:
    """
    Prepare les sources sans LLM.

    V146 :
    - le rapport sauvegarde et la reponse HTTP sont compacts ;
    - aucun passage NLP complet n'est renvoye au navigateur ;
    - l'agent suivant recupere seulement les stats et les chemins utiles.
    """
    nlp_rag = run_nlp_and_rag(db, project)
    ps = get_project_store(project)

    nlp_stats = sanitize_json_value(
        nlp_rag.get("nlp_stats")
        or (nlp_rag.get("nlp_result") or {}).get("stats")
        or {}
    )
    index_report = sanitize_json_value(nlp_rag.get("index_report") or {})

    # Structure minimale attendue par run_ennodiagnostic_agent_only() et
    # run_true_ennodiagnostic_agent(). Le gros nlp_result.json reste sur disque.
    compact_pipeline = {
        "documents_used_count": nlp_rag.get("documents_used_count"),
        "documents_loaded_count": nlp_rag.get("documents_loaded_count"),
        "documents_used_paths": nlp_rag.get("documents_used_paths") or [],
        "documents_skipped_count": nlp_rag.get("documents_skipped_count"),
        "nlp_result": {"stats": nlp_stats},
        "nlp_stats": nlp_stats,
        "index_report": index_report,
        "nlp_result_path": nlp_rag.get("nlp_result_path"),
    }

    result = {
        "ok": True,
        "step": "prepare_sources",
        "pipeline": "documents_raw_extraction_nlp_frascati_rag_chroma",
        "response_policy": "compact_v146_no_full_nlp_payload",
        "project": {
            "id": project.id,
            "organisme": project.organisme,
            "project_name": project.project_name,
            "year": _year(project),
        },
        "documents_used_count": compact_pipeline["documents_used_count"],
        "documents_loaded_count": compact_pipeline["documents_loaded_count"],
        "documents_used_paths": compact_pipeline["documents_used_paths"],
        "nlp_stats": nlp_stats,
        "index_report": index_report,
        "paths": {
            "project_dir": str(ps.project_dir),
            "raw_dir": str(ps.documents_raw_dir),
            "nlp_result": str(ps.nlp_dir / "nlp_result.json"),
            "rag_chunks": str(ps.rag_dir / "chunks.json"),
            "prepare_report": str(_prepare_report_path(project)),
        },
        "raw_pipeline_result": compact_pipeline,
        "generated_at": datetime.utcnow().isoformat(),
    }

    # Sauvegarde compacte : pas de deuxieme copie de tous les passages NLP.
    save_json(_prepare_report_path(project), result)

    project.status = "Sources preparees"
    db.commit()

    print(
        "[prepare-sources] Reponse HTTP compacte prete",
        flush=True,
    )
    return result

def run_ennodiagnostic_agent_only(db: Session, project: Project) -> DiagnosticRun:
    """
    Lance l'agent depuis Chroma puis sauvegarde atomiquement :
    DiagnosticRun complet + toutes les sections + verrous DB.
    """
    ps = get_project_store(project)
    rag_chunks = ps.rag_dir / "chunks.json"
    nlp_result = ps.nlp_dir / "nlp_result.json"

    if not rag_chunks.exists() or not nlp_result.exists():
        raise RuntimeError(
            "Sources non préparées. Lance d'abord /diagnostic/prepare-sources "
            "ou utilise /diagnostic/run."
        )

    prepare_report = _load_prepare_report(project)
    prior_pipeline = (
        prepare_report.get("raw_pipeline_result")
        if isinstance(prepare_report.get("raw_pipeline_result"), dict)
        else None
    )
    report = run_true_ennodiagnostic_agent(project, prior_pipeline=prior_pipeline)
    paths = diagnostic_paths(project)

    return _persist_complete_run(
        db,
        project,
        report=report,
        status_value="completed_agent_only_v142",
        pipeline_name="chroma_score_ia_style_memory_llm_ennodiagnostic_complete_db",
        button="ennodiagnostic_agent_only",
        report_path=str(paths["report"]) if paths["report"].exists() else str(ps.project_dir / "ennodiagnostic" / "ennodiagnostic_report.json"),
        nlp_result_path=str(nlp_result),
        selected_verrous_path=str(paths["selected_verrous"]) if paths["selected_verrous"].exists() else None,
        prepare_report=prepare_report,
        pipeline_result={"agent_only": True},
        bundle_metadata={
            "report_path_used": str(paths["report"]),
            "nlp_path_used": str(nlp_result),
            "rag_chunks_path": str(rag_chunks),
        },
    )


def create_diagnostic_run_from_files(db: Session, project: Project) -> DiagnosticRun:
    """Importe un rapport existant et synchronise immédiatement toutes ses sections et verrous."""
    paths = diagnostic_paths(project)
    bundle = read_diagnostic_bundle(project)
    report = bundle.get("report") if isinstance(bundle.get("report"), dict) else {}

    if not report:
        raise RuntimeError("Aucun rapport EnnoDiagnostic existant à importer.")

    return _persist_complete_run(
        db,
        project,
        report=report,
        status_value="completed_from_existing_files_v142",
        pipeline_name="import_existing_complete_db",
        button="import_existing",
        report_path=str(paths["report"]) if paths["report"].exists() else None,
        nlp_result_path=str(paths["nlp_result"]) if paths["nlp_result"].exists() else None,
        selected_verrous_path=str(paths["selected_verrous"]) if paths["selected_verrous"].exists() else None,
        pipeline_result={"import_existing": True},
        bundle_metadata={
            "report_path_used": bundle.get("report_path_used"),
            "report_candidates_found": bundle.get("report_candidates_found"),
            "files_found": bundle.get("files_found"),
        },
    )


def run_ennodiagnostic(db: Session, project: Project) -> DiagnosticRun:
    """Un seul bouton : extraction -> NLP -> RAG -> agent -> DB complète -> verrous DB."""
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
        pipeline_result = sanitize_json_value(pipeline_result)
        report = _report_from_pipeline_result(pipeline_result)
        if not report:
            bundle = read_diagnostic_bundle(project)
            report = bundle.get("report") if isinstance(bundle.get("report"), dict) else {}
    else:
        pipeline_result = run_full_ennodiagnostic_pipeline(db, project)
        pipeline_result = sanitize_json_value(pipeline_result)
        report = _report_from_pipeline_result(pipeline_result)

    paths = diagnostic_paths(project)
    prepare_report = _load_prepare_report(project)

    return _persist_complete_run(
        db,
        project,
        report=report,
        status_value="completed_full_v142",
        pipeline_name="extraction_nlp_frascati_rag_chroma_llm_complete_db",
        button="single_ennodiagnostic_button",
        report_path=str(paths["report"]) if paths["report"].exists() else None,
        nlp_result_path=str(paths["nlp_result"]) if paths["nlp_result"].exists() else None,
        selected_verrous_path=str(paths["selected_verrous"]) if paths["selected_verrous"].exists() else None,
        prepare_report=prepare_report,
        pipeline_result=pipeline_result,
        bundle_metadata={
            "report_path_used": str(paths["report"]),
            "nlp_path_used": str(paths["nlp_result"]),
            "rag_chunks_path": str(paths["rag_chunks"]),
        },
    )
