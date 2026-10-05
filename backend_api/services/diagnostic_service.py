from __future__ import annotations

DIAGNOSTIC_SERVICE_VERSION = "v151_ennodiagnostic_v2_only"

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

from sqlalchemy.orm import Session

from db.models import DiagnosticRun, Project, Verrou
from services.document_corpus_service import (
    CORPUS_DIAGNOSTIC,
    diagnostic_corpus_manifest,
    documents_for_corpus,
)
from services.document_storage_service import read_document_bytes
from services.file_service import load_json_file, project_output_dir
from services.diagnostic_reset_service import exclusive_project_diagnostic
from services.project_artifact_service import (
    artifact_uri,
    get_json_artifact,
    json_artifact_exists,
    save_json_artifact,
)
from modules.common.runtime_paths import code_root, data_root

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
    return data_root()


def ensure_ennosmart_imports() -> Path:
    base_dir = code_root()
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
        subproject=getattr(project, "subproject_name", None),
        year=_year(project),
    ).ensure()


NLP_ARTIFACT_KEY = "nlp/nlp_result.json"
RAG_CHUNKS_ARTIFACT_KEY = "rag/chunks.json"
PREPARE_REPORT_ARTIFACT_KEY = "diagnostics/prepare_sources_report.json"






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

    docs = documents_for_corpus(db, project.id, CORPUS_DIAGNOSTIC)

    for doc in docs:
        file_path = getattr(doc, "file_path", None)
        if file_path and not str(file_path).startswith("db://") and Path(str(file_path)).exists():
            paths.append(str(file_path))

    return paths


def copy_uploaded_docs_to_project_store(
    db: Session,
    project: Project,
    documents: Optional[List[Any]] = None,
) -> List[str]:
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

    docs = list(documents) if documents is not None else documents_for_corpus(
        db,
        project.id,
        CORPUS_DIAGNOSTIC,
        load_file_data=True,
    )

    # Le dossier raw est une copie de travail reconstructible. On retire
    # uniquement les copies DB qui ne font pas partie du corpus Diagnostic ;
    # les originaux PostgreSQL ne sont jamais supprimés.
    selected_ids = {int(doc.id) for doc in docs}
    all_project_docs = (
        db.query(Document)
        .filter(Document.project_id == project.id)
        .all()
    )
    removed_working_copies: List[str] = []
    for doc in all_project_docs:
        if int(doc.id) in selected_ids:
            continue
        stored_name = getattr(doc, "stored_filename", None) or getattr(doc, "filename", None) or f"document_{doc.id}"
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", str(stored_name)).strip() or f"document_{doc.id}"
        stale_path = ps.documents_raw_dir / safe_name
        try:
            if stale_path.exists() and stale_path.is_file():
                stale_path.unlink()
                removed_working_copies.append(str(stale_path))
        except OSError as exc:
            print(f"[prepare-sources] Copie raw non supprimée {stale_path}: {exc}")

    for doc in docs:
        stored_name = getattr(doc, "stored_filename", None) or getattr(doc, "filename", None) or f"document_{doc.id}"
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", str(stored_name)).strip() or f"document_{doc.id}"
        dst = ps.documents_raw_dir / safe_name

        try:
            dst.write_bytes(read_document_bytes(doc))
            copied.append(str(dst))
        except (FileNotFoundError, IOError) as exc:
            print(f"[prepare-sources] Document indisponible id={doc.id}: {exc}")

    print(f"✅ Documents reconstruits depuis PostgreSQL vers raw : {len(copied)}")
    for path in copied[:20]:
        print(f"   - {path}")
    if removed_working_copies:
        print(
            "[prepare-sources] Copies de travail hors corpus retirées : "
            f"{len(removed_working_copies)}"
        )

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


def _diagnostic_document_metadata(
    project_store: Any,
    documents: List[Any],
) -> Dict[str, Dict[str, Any]]:
    """Relie chaque copie de travail à sa déclaration PostgreSQL.

    L'affectation explicite au corpus Diagnostic signifie « preuve courante ».
    Cette règle est générique et empêche une détection automatique de type CIR
    de faire disparaître un fichier que le consultant a choisi d'analyser.
    """
    metadata: Dict[str, Dict[str, Any]] = {}
    for document in documents or []:
        stored_name = (
            getattr(document, "stored_filename", None)
            or getattr(document, "filename", None)
            or f"document_{getattr(document, 'id', 'unknown')}"
        )
        safe_name = (
            re.sub(r'[\\/:*?"<>|]+', "_", str(stored_name)).strip()
            or f"document_{getattr(document, 'id', 'unknown')}"
        )
        raw_path = project_store.documents_raw_dir / safe_name
        metadata[str(raw_path.resolve())] = {
            "document_id": getattr(document, "id", None),
            "declared_document_type": str(
                getattr(document, "document_type", None) or "Document brut"
            ),
            "declared_corpus": CORPUS_DIAGNOSTIC,
            "declared_mode": "raw",
            "diagnostic_corpus_selected": True,
        }
    return metadata




def _reset_generated_diagnostic_artifacts(project_store: Any) -> Dict[str, Any]:
    """Supprime les seuls artefacts recalculables du périmètre courant.

    Les documents bruts, le CIR final, la Memory V2 et les données en base sont
    volontairement conservés. Une suppression incomplète fait échouer la
    préparation afin d'interdire tout mélange silencieux ancien/nouveau.
    """
    project_root = Path(project_store.project_dir).resolve()
    generated_directories = [
        Path(project_store.documents_processed_dir),
        Path(project_store.nlp_dir),
        # Windows / Chroma :
        # ne pas supprimer rag_dir car rag/chroma/chroma.sqlite3 peut rester
        # ouvert par chromadb.PersistentClient dans le processus.
        # index_nlp_result(..., reset=True) réinitialise ensuite la COLLECTION.
        Path(project_store.diagnostics_dir),
        project_root / "ennodiagnostic",
        project_root / "cir_memory",
    ]
    generated_files = [
        # Fichier RAG régénérable. Le dossier rag/chroma reste intact.
        Path(project_store.rag_dir) / "chunks.json",
        project_root / "selected_verrous_for_scholar.json",
        project_root / "comparison_cir_vs_raw.json",
        project_root / "rag_report.json",
    ]

    removed: List[str] = []
    for target in [*generated_directories, *generated_files]:
        resolved = target.resolve()
        try:
            resolved.relative_to(project_root)
        except ValueError as exc:
            raise RuntimeError(
                f"Refus de supprimer un artefact hors projet : {resolved}"
            ) from exc
        if resolved == project_root:
            raise RuntimeError(
                "Refus de supprimer la racine du projet pendant la relance."
            )
        if not resolved.exists():
            continue
        if resolved.is_dir() and not resolved.is_symlink():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
        removed.append(str(resolved))

    project_store.ensure()
    return {
        "version": "diagnostic_generated_artifacts_reset_v2_chroma_safe",
        "scope": {
            "project_dir": str(project_root),
            "collection_name": project_store.collection_name,
        },
        "removed": removed,
        "removed_count": len(removed),
        "preserved": [
            str(Path(project_store.documents_raw_dir).resolve()),
            str(Path(project_store.metadata_path).resolve()),
            str(Path(project_store.chroma_dir).resolve()),
        ],
        "database_history_deleted": False,
        "memory_v2_deleted": False,
        "raw_documents_deleted": False,
        "chroma_filesystem_deleted": False,
        "chroma_collection_reset_later": True,
    }

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
    subproject_slug = _slug(
        getattr(project, "subproject_name", None),
        "",
    ) if str(getattr(project, "subproject_name", None) or "").strip() else ""
    year_slug = _slug(year_raw, "year")

    project_scope = (
        base_dir / "storage" / "organismes" / org_slug / "projects" / proj_slug
    )
    if subproject_slug:
        project_scope = project_scope / "subprojects" / subproject_slug
    year_scope = project_scope / "years" / year_slug

    candidates: List[Path] = [
        year_scope / "ennodiagnostic" / "ennodiagnostic_report.json",
        year_scope / "diagnostics" / "ennodiagnostic_report.json",
        year_scope / "diagnostics" / "diagnostic_ennodiagnostic.json",
        year_scope / "diagnostics" / "diagnostic_ennodiagnostic_sections.json",
    ]

    # Un sous-projet ne doit jamais relire les sorties historiques du projet
    # parent : elles peuvent appartenir à un autre périmètre fonctionnel.
    if not subproject_slug:
        candidates.extend(
            [
                output_dir / "ennodiagnostic_report.json",
                output_dir / "diagnostic_ennodiagnostic.json",
                output_dir / "diagnostic_ennodiagnostic_sections.json",
                output_dir / "ennodiagnostic" / "ennodiagnostic_report.json",
                output_dir / "diagnostics" / "ennodiagnostic_report.json",
                output_dir
                / "diagnostics"
                / "diagnostic_ennodiagnostic.json",
                output_dir
                / "diagnostics"
                / "diagnostic_ennodiagnostic_sections.json",
                # Nouveau ProjectStore avec année.
                base_dir
                / "storage"
                / "organismes"
                / org_slug
                / "projects"
                / proj_slug
                / "years"
                / year_slug
                / "ennodiagnostic"
                / "ennodiagnostic_report.json",
                base_dir
                / "storage"
                / "organismes"
                / org_slug
                / "projects"
                / proj_slug
                / "years"
                / year_slug
                / "diagnostics"
                / "ennodiagnostic_report.json",
                base_dir
                / "storage"
                / "organismes"
                / org_slug
                / "projects"
                / proj_slug
                / "years"
                / year_slug
                / "diagnostics"
                / "diagnostic_ennodiagnostic.json",
                base_dir
                / "storage"
                / "organismes"
                / org_slug
                / "projects"
                / proj_slug
                / "years"
                / year_slug
                / "diagnostics"
                / "diagnostic_ennodiagnostic_sections.json",
                # Ancien storage sans année.
                base_dir
                / "storage"
                / "organismes"
                / org_slug
                / "projects"
                / proj_slug
                / "diagnostics"
                / "ennodiagnostic_report.json",
                base_dir
                / "storage"
                / "organismes"
                / org_slug
                / "projects"
                / proj_slug
                / "diagnostics"
                / "diagnostic_ennodiagnostic.json",
                base_dir
                / "storage"
                / "organismes"
                / org_slug
                / "projects"
                / proj_slug
                / "diagnostics"
                / "diagnostic_ennodiagnostic_sections.json",
                # Ancien Streamlit / safe_rag_upload.
                base_dir
                / "outputs"
                / "safe_rag_upload"
                / org_raw
                / proj_raw
                / year_raw
                / "ennodiagnostic"
                / "ennodiagnostic_report.json",
                base_dir
                / "outputs"
                / "safe_rag_upload"
                / org_raw
                / proj_raw
                / year_raw
                / "ennodiagnostic_report.json",
                base_dir
                / "outputs"
                / "safe_rag_upload"
                / org_raw
                / proj_raw
                / year_raw
                / "diagnostics"
                / "diagnostic_ennodiagnostic.json",
            ]
        )

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


def _report_with_latest_ai_detection(
    project: Project,
    report_path: Path,
) -> tuple[Dict[str, Any], Optional[Path]]:
    """Expose un recalcul IA récent sans imposer un diagnostic complet."""
    report = load_json_file(report_path)
    if not isinstance(report, dict):
        report = {}

    try:
        ps = get_project_store(project)
        ai_path = _first_existing(
            [
                ps.project_dir / "ennodiagnostic" / "ai_detection_report.json",
                ps.diagnostics_dir / "ai_detection_report.json",
            ]
        )
    except Exception:
        ai_path = None

    if ai_path is None:
        return report, None

    try:
        report_mtime = report_path.stat().st_mtime if report_path.exists() else 0.0
        ai_mtime = ai_path.stat().st_mtime
    except Exception:
        report_mtime = 0.0
        ai_mtime = 0.0

    # Pendant un pipeline complet, le rapport principal est enregistré après le
    # score IA et contient déjà la même donnée. En revanche, un recalcul IA seul
    # doit devenir visible immédiatement dans /diagnostic/latest.
    if ai_mtime < report_mtime:
        return report, None

    ai_report = load_json_file(ai_path)
    if not isinstance(ai_report, dict) or not ai_report.get("ok"):
        return report, None

    merged = dict(report)
    merged["ai_detection_report_runtime"] = ai_report
    return merged, ai_path


def read_diagnostic_bundle(project: Project, *, compact: bool = False) -> Dict[str, Any]:
    paths = diagnostic_paths(project)
    existing_reports = [str(p) for p in _report_candidates(project, paths["output_dir"]) if p.exists() and p.is_file()]
    report, ai_report_path = _report_with_latest_ai_detection(
        project,
        paths["report"],
    )

    if compact:
        # La vue React est reconstruite depuis le rapport officiel. Charger ici
        # nlp_result/chunks/comparaisons (plusieurs dizaines de Mo) ne change pas
        # l'affichage et bloquait inutilement chaque ouverture de page.
        # Le rapport est un JSON déjà désérialisé. Ne pas le parcourir une
        # première fois ici : la route compacte sélectionne ensuite les seuls
        # champs publics avant l'unique sanitation finale.
        return {
            "output_dir": str(paths["output_dir"]),
            "report": report,
            "report_path_used": str(paths["report"]) if paths["report"].exists() else None,
            "ai_detection_report_path_used": str(ai_report_path) if ai_report_path else None,
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
        }

    return sanitize_json_value({
        "output_dir": str(paths["output_dir"]),
        "report": report,
        "report_path_used": str(paths["report"]) if paths["report"].exists() else None,
        "ai_detection_report_path_used": str(ai_report_path) if ai_report_path else None,
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

def _prepare_ennodiagnostic_v2_sources(
    db: Session,
    project: Project,
) -> Dict[str, Any]:
    """Extrait une seule fois le corpus complet requis par EnnoDiagnostic V2.

    Cette préparation ne lance ni le NLP V1, ni Frascati V1, ni Chroma. Les
    modules historiques seront raccordés séparément au corpus fulltext.
    """
    ensure_ennosmart_imports()

    from modules.NLP.document_loader import load_documents
    from modules.RAG.full_document_corpus import persist_full_documents_for_chat
    from services.diagnostic_reset_service import reset_previous_agent_runs

    ps = get_project_store(project)
    database_reset = reset_previous_agent_runs(
        db,
        project,
        ps,
        status="Préparation EnnoDiagnostic V2 en cours",
    )
    reset_report = _reset_generated_diagnostic_artifacts(ps)
    reset_report["database_history_deleted"] = True
    reset_report["agent_runs_reset"] = database_reset

    print("[EnnoDiagnostic V2][1/4] Reconstruction des documents", flush=True)
    selected_documents = documents_for_corpus(
        db,
        project.id,
        CORPUS_DIAGNOSTIC,
        load_file_data=True,
    )
    corpus_manifest = diagnostic_corpus_manifest(db, project.id)
    copied_from_db = copy_uploaded_docs_to_project_store(
        db,
        project,
        selected_documents,
    )

    allowed_ext = _supported_raw_extensions_from_extraction_router()
    raw_paths: List[str] = []
    skipped_paths: List[str] = []
    for value in copied_from_db:
        path = Path(value)
        if _is_supported_raw_document(path, allowed_ext):
            raw_paths.append(str(path))
        elif path.is_file():
            skipped_paths.append(str(path))
    raw_paths = sorted(dict.fromkeys(raw_paths))

    if not raw_paths:
        raise RuntimeError(
            f"Aucun document brut supporté trouvé dans : {ps.documents_raw_dir}. "
            f"Extensions acceptées : {sorted(allowed_ext)}"
        )

    print(
        f"[EnnoDiagnostic V2][2/4] Extraction fulltext de {len(raw_paths)} document(s)",
        flush=True,
    )
    documents = load_documents(
        raw_paths,
        use_ennosmart_extraction=True,
        include_cir_final=False,
        metadata_by_path=_diagnostic_document_metadata(ps, selected_documents),
        vision_mode="text_only",
        formula_mode=os.getenv("ENNOSMART_DIAGNOSTIC_FORMULA_MODE", "fast"),
    )
    if not documents:
        raise RuntimeError("Aucun texte exploitable extrait des documents bruts.")

    visual_asset_report: Dict[str, Any] = {
        "assets_count": 0,
        "vision_model_used": False,
        "indexed_for_diagnostic": False,
    }
    try:
        from modules.extraction.visual.asset_retention import persist_visual_assets

        visual_asset_report = persist_visual_assets(
            store=ps,
            documents=[
                {
                    "document": Path(source_path).name,
                    "source_path": source_path,
                }
                for source_path in raw_paths
            ],
        )
    except Exception as exc:
        print(f"[EnnoDiagnostic V2][visuals] WARNING: {exc}", flush=True)

    print("[EnnoDiagnostic V2][3/4] Persistance du corpus fulltext", flush=True)
    try:
        fulltext_report = persist_full_documents_for_chat(
            store=ps,
            documents=documents,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Impossible de sauvegarder le corpus fulltext V2 : {exc}"
        ) from exc
    finally:
        del documents
        gc.collect()

    if int(fulltext_report.get("documents_count") or 0) <= 0:
        raise RuntimeError(
            "Le corpus fulltext V2 a été créé sans document exploitable."
        )

    print(
        "[EnnoDiagnostic V2][4/4] Sources prêtes : "
        f"documents={fulltext_report.get('documents_count')}, "
        f"chars={fulltext_report.get('total_chars')}",
        flush=True,
    )
    return sanitize_json_value({
        "documents_copied_from_db": copied_from_db,
        "documents_used_paths": raw_paths,
        "documents_used_count": len(raw_paths),
        "documents_loaded_count": fulltext_report.get("documents_count"),
        "documents_skipped_paths": skipped_paths,
        "documents_skipped_count": len(skipped_paths),
        "allowed_extensions": sorted(allowed_ext),
        "corpus_manifest": corpus_manifest,
        "reset_report": reset_report,
        "visual_asset_report": visual_asset_report,
        "fulltext_corpus": fulltext_report,
        "fulltext_corpus_path": str(
            Path(ps.documents_processed_dir) / "fulltext_rag_v1"
        ),
    })









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


def run_ai_detector_if_enabled(
    project: Project,
    agent_out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Lance le module historique AI_DETECTOR sur le corpus fulltext V2.

    Il ne lit ni ``nlp_result.json`` ni les sorties générées par le LLM.
    """
    if os.getenv("ENNOSMART_RUN_AI_DETECTOR", "1").strip() != "1":
        return {"ok": False, "skipped": True, "message": "Détection IA désactivée."}

    ensure_ennosmart_imports()

    try:
        from modules.AI_DETECTOR import run_ai_detection_on_fulltext_corpus

        project_store = get_project_store(project)
        output_root = Path(agent_out_dir or project_store.project_dir)
        fulltext_corpus_dir = (
            Path(project_store.documents_processed_dir)
            / "fulltext_rag_v1"
        )
        adapter_report = {
            "policy": "direct_fulltext_corpus_v2",
            "project_root": str(project_store.project_root_dir),
            "year_root": str(project_store.project_dir),
            "fulltext_corpus_dir": str(fulltext_corpus_dir),
            "legacy_mirror_used": False,
            "note": (
                "Le détecteur lit exclusivement documents/processed/"
                "fulltext_rag_v1 et ignore nlp_result.json."
            ),
        }

        raw_report = run_ai_detection_on_fulltext_corpus(
            fulltext_corpus_dir,
        )
        normalized = _normalize_ai_report(raw_report)
        normalized["backend_adapter"] = adapter_report

        # L'agent EnnoDiagnostic lit ce fichier via self.diagnostic_dir.
        save_json(output_root / "ennodiagnostic" / "ai_detection_report.json", normalized)

        # On garde aussi une copie backend/reporting.
        save_json(output_root / "diagnostics" / "ai_detection_report.json", normalized)

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



def run_rag_fulltext_index_if_enabled(project: Project) -> Dict[str, Any]:
    """Indexe le corpus fulltext V2 dans la collection Chroma dédiée au chat.

    Le diagnostic ne doit pas redevenir dépendant du RAG : une erreur Chroma
    est donc exposée dans le rapport mais n'annule pas l'intelligence V2.
    """
    if os.getenv("ENNOSMART_RUN_RAG_INDEX", "1").strip() != "1":
        return {
            "ok": False,
            "skipped": True,
            "source": "fulltext_rag_v1",
            "nlp_result_used": False,
            "message": "Indexation RAG fulltext désactivée.",
        }

    ensure_ennosmart_imports()
    try:
        from modules.RAG.indexer import index_fulltext_corpus

        return sanitize_json_value(
            index_fulltext_corpus(
                organisme=project.organisme,
                project=project.project_name,
                subproject=getattr(project, "subproject_name", None),
                year=_year(project),
                reset=True,
            )
        )
    except Exception as exc:
        print(f"[RAG fulltext][WARN] {exc}", flush=True)
        return {
            "ok": False,
            "skipped": False,
            "source": "fulltext_rag_v1",
            "nlp_result_used": False,
            "error": str(exc),
        }


def _extract_v2_project_facts_for_cir_memory(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Réutilise les listes projet validées et leurs preuves, sans réextraction."""
    payload = _v2_diagnostic_payload(report)
    core = payload.get("core") or payload
    synthesis = report.get("project_synthesis") or payload.get("project_synthesis") or {}
    extractions = core.get("extractions") or []
    entities = core.get("entities") or []
    evidence_by_id = {}
    source_by_id = {}
    for item in extractions:
        if not isinstance(item, dict):
            continue
        source_by_id[item.get("item_id")] = item
        evidence = item.get("evidence") or {}
        if evidence.get("evidence_id"):
            evidence_by_id[evidence["evidence_id"]] = evidence
    for entity in entities:
        for evidence in entity.get("evidences") or []:
            if evidence.get("evidence_id"):
                evidence_by_id[evidence["evidence_id"]] = evidence

    output = []
    for kind, key in (("method", "methods"), ("demarche", "demarches"),
                      ("parameter", "parameters"), ("metric", "metrics"), ("result", "results")):
        for index, point in enumerate(synthesis.get(key) or []):
            if not isinstance(point, dict) or point.get("source_role") != "project_evidence":
                continue
            evidence_ids = point.get("evidence_ids") or []
            if not evidence_ids or any(
                eid not in evidence_by_id or not evidence_by_id[eid].get("quote_verified")
                for eid in evidence_ids
            ):
                continue
            output.append({
                "id": f"v2_{kind}_{index}", "kind": kind, "statement": point.get("text"),
                "source_role": "project_evidence", "temporal_status": point.get("temporal_status", "unclear"),
                "original_item_ids": point.get("original_item_ids") or [],
                "evidences": [evidence_by_id[eid] for eid in evidence_ids],
            })

    for entity in entities:
        if entity.get("kind") != "objective":
            continue
        metadata = entity.get("metadata") or {}
        roles = metadata.get("evidence_source_roles") or {}
        sources = [source_by_id[sid] for sid in entity.get("source_item_ids") or [] if sid in source_by_id]
        source_roles = {
            (s.get("evidence") or {}).get("evidence_id"): (s.get("metadata") or {}).get("source_role")
            for s in sources
        }
        proofs = [e for e in entity.get("evidences") or []
                  if e.get("quote_verified") and roles.get(e.get("evidence_id"), source_roles.get(e.get("evidence_id"))) == "project_evidence"]
        if not proofs:
            continue
        project_sources = [s for s in sources if (s.get("metadata") or {}).get("source_role") == "project_evidence"]
        statement = entity.get("statement")
        if len(project_sources) != len(sources) and project_sources:
            statement = project_sources[0].get("statement")
        temporal = {s.get("temporal_status", "unclear") for s in project_sources}
        output.append({
            "id": entity.get("entity_id"), "kind": "objective", "statement": statement,
            "source_role": "project_evidence", "temporal_status": next(iter(temporal)) if len(temporal) == 1 else "unclear",
            "original_item_ids": entity.get("source_item_ids") or [], "evidences": proofs,
        })
    return output


def run_previous_cir_comparison(project: Project, report: Dict[str, Any]) -> Dict[str, Any]:
    """Compare les faits projet et verrous qualifiés courants, sans nouveau LLM."""
    from modules.CIR_MEMORY.cir_memory import (
        compare_current_raw_with_cir_memory,
        comparison_report_path,
        write_json,
    )

    scope = {
        "organisme": project.organisme,
        "project": project.project_name,
        "year": _year(project),
        "subproject": str(getattr(project, "subproject_name", "") or "").strip(),
    }
    try:
        return sanitize_json_value(compare_current_raw_with_cir_memory(
            **scope, current_verrous=_extract_v2_verrous_from_report(report),
            current_project_facts=_extract_v2_project_facts_for_cir_memory(report),
        ))
    except Exception as exc:
        # Une erreur de comparaison ne doit ni invalider le diagnostic, ni
        # laisser afficher silencieusement les rapprochements d'un ancien run.
        failed = {
            "ok": False, "comparison_status": "error", "error": str(exc),
            "generated_at": datetime.utcnow().isoformat(),
            "current_year": _year(project), "comparisons": [], "verrou_comparisons": [],
        }
        try:
            write_json(comparison_report_path(**scope), failed)
        except Exception as storage_exc:
            failed["storage_error"] = str(storage_exc)
        print(f"[EnnoDiagnostic][CIR_MEMORY][WARN] {exc}", flush=True)
        return failed


def run_true_ennodiagnostic_agent(
    project: Project,
    prior_pipeline: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Point d'entrée backend EnnoDiagnostic.

    V2 reste le moteur de diagnostic. La détection IA est ensuite exécutée
    comme module indépendant sur le même corpus fulltext. La comparaison CIR
    est calculée après le diagnostic, sans intervenir dans son raisonnement.
    """
    ensure_ennosmart_imports()

    from services.ennodiagnostic_v2_service import run_ennodiagnostic_v2

    print(
        "[EnnoDiagnostic V2] Lancement du nouveau moteur...",
        flush=True,
    )

    v2_report = sanitize_json_value(
        run_ennodiagnostic_v2(project)
    )

    diagnostic_v2 = (
        v2_report.get("diagnostic_v2")
        if isinstance(v2_report, dict)
        else {}
    ) or {}

    scoring = (
        diagnostic_v2.get("scoring")
        if isinstance(diagnostic_v2, dict)
        else {}
    ) or {}

    project_synthesis = (
        diagnostic_v2.get("project_synthesis")
        if isinstance(diagnostic_v2, dict)
        else {}
    ) or {}

    lock_families = (
        diagnostic_v2.get("lock_families")
        if isinstance(diagnostic_v2, dict)
        else {}
    ) or {}

    core = (
        diagnostic_v2.get("core")
        if isinstance(diagnostic_v2, dict)
        else {}
    ) or {}

    rnd_approach = (
        diagnostic_v2.get("rnd_approach")
        if isinstance(diagnostic_v2, dict)
        else {}
    ) or {}

    project_frascati = (
        diagnostic_v2.get("project_frascati")
        if isinstance(diagnostic_v2, dict)
        else {}
    ) or {}

    report = {
        **v2_report,
        "version": (
            diagnostic_v2.get("version")
            or "ennodiagnostic_v2"
        ),
        "project_summary": project_synthesis,
        "summary": project_synthesis.get(
            "project_summary"
        ),
        "eligibility": scoring.get(
            "eligibility"
        ) or {},
        "defensibility": scoring.get(
            "defensibility"
        ) or {},
        "risk_level": scoring.get(
            "risk_level"
        ),
        "potential_level": scoring.get(
            "potential_level"
        ),
        "scoring": scoring,
        "lock_families": lock_families,
        "project_synthesis": project_synthesis,
        "rnd_approach": rnd_approach,
        "project_frascati": project_frascati,
        "entities": core.get(
            "entities"
        ) or [],
        "extractions": core.get(
            "extractions"
        ) or [],
        "pipeline_before_agent": {
            "pipeline": "fulltext_to_ennodiagnostic_v2",
            "documents_loaded_count": (
                prior_pipeline or {}
            ).get("documents_loaded_count"),
            "fulltext_corpus": (
                prior_pipeline or {}
            ).get("fulltext_corpus") or {},
        },
        "independent_modules": {
            "ai_detector": "pending",
            "document_compare": "not_run",
            "cir_memory": "not_run",
            "rag": "not_run",
        },
    }

    cir_report = run_previous_cir_comparison(project, report)
    report["cir_memory_report"] = cir_report
    report["independent_modules"]["cir_memory"] = (
        cir_report.get("comparison_status") or "completed"
        if cir_report.get("ok") else "error"
    )

    ai_report = sanitize_json_value(
        run_ai_detector_if_enabled(project)
    )
    report["ai_detection_report"] = ai_report
    if ai_report.get("ok"):
        report["independent_modules"]["ai_detector"] = "completed_fulltext"
    elif ai_report.get("skipped"):
        report["independent_modules"]["ai_detector"] = "skipped"
    else:
        report["independent_modules"]["ai_detector"] = "error"

    print(
        "[EnnoDiagnostic V2] Terminé : "
        f"eligibilite={((report.get('eligibility') or {}).get('score'))}, "
        f"defendabilite={((report.get('defensibility') or {}).get('score'))}, "
        f"domaines={((report.get('project_context') or {}).get('domain_tags'))}",
        flush=True,
    )

    return sanitize_json_value(report)

def run_full_ennodiagnostic_pipeline(db: Session, project: Project) -> Dict[str, Any]:
    preparation = _prepare_ennodiagnostic_v2_sources(db, project)
    prepare_record = sanitize_json_value({
        "ok": True,
        "step": "prepare_sources",
        "engine": "ennodiagnostic_v2",
        "pipeline": "documents_raw_extraction_fulltext_v2",
        "documents_used_count": preparation.get("documents_used_count"),
        "documents_loaded_count": preparation.get("documents_loaded_count"),
        "documents_used_paths": preparation.get("documents_used_paths") or [],
        "corpus_manifest": preparation.get("corpus_manifest") or {},
        "fulltext_corpus": preparation.get("fulltext_corpus") or {},
        "raw_pipeline_result": {
            "pipeline": "fulltext_to_ennodiagnostic_v2",
            "documents_loaded_count": preparation.get("documents_loaded_count"),
            "fulltext_corpus": preparation.get("fulltext_corpus") or {},
            "fulltext_corpus_path": preparation.get("fulltext_corpus_path"),
            "corpus_manifest": preparation.get("corpus_manifest") or {},
        },
        "generated_at": datetime.utcnow().isoformat(),
    })
    save_json_artifact(
        db,
        project.id,
        PREPARE_REPORT_ARTIFACT_KEY,
        prepare_record,
        artifact_kind="diagnostic_prepare_report",
    )

    print("[EnnoDiagnostic V2][RAG] Indexation fulltext / Chroma", flush=True)
    rag_index_report = run_rag_fulltext_index_if_enabled(project)

    report = run_true_ennodiagnostic_agent(
        project,
        prior_pipeline=preparation,
    )
    report["rag_index_report"] = rag_index_report
    independent = report.get("independent_modules")
    if not isinstance(independent, dict):
        independent = {}
        report["independent_modules"] = independent
    if rag_index_report.get("ok"):
        independent["rag"] = "completed_fulltext"
    elif rag_index_report.get("skipped"):
        independent["rag"] = "skipped"
    else:
        independent["rag"] = "error"

    return sanitize_json_value({
        "v2_preparation": {
            "documents_loaded_count": preparation.get("documents_loaded_count"),
            "fulltext_corpus": preparation.get("fulltext_corpus") or {},
            "fulltext_corpus_path": preparation.get("fulltext_corpus_path"),
            "corpus_manifest": preparation.get("corpus_manifest") or {},
            "rag_index": rag_index_report,
        },
        "prepare_report": prepare_record,
        "rag_index_report": rag_index_report,
        "report": report,
    })



# ============================================================
# V142 — persistance complète et atomique du diagnostic
# ============================================================


def _prepare_report_path(project: Project) -> Path:
    ps = get_project_store(project)
    return ps.diagnostics_dir / "prepare_sources_report.json"


def _load_prepare_report(db: Session, project: Project) -> Dict[str, Any]:
    stored = get_json_artifact(
        db,
        project.id,
        PREPARE_REPORT_ARTIFACT_KEY,
        default=None,
    )
    if isinstance(stored, dict):
        return stored
    path = _prepare_report_path(project)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                save_json_artifact(
                    db,
                    project.id,
                    PREPARE_REPORT_ARTIFACT_KEY,
                    data,
                    artifact_kind="diagnostic_prepare_report",
                    metadata={"migrated_from": str(path)},
                )
                path.unlink(missing_ok=True)
                return data
            return {}
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



def _recovered_lock_display_score(
    item: Dict[str, Any],
    source_json: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """Score d'affichage sûr pour un verrou récupéré après l'étape Frascati.

    Important :
    - ne crée aucun verrou ;
    - ne modifie aucun group_id ;
    - ne modifie pas le score Frascati amont ;
    - ne s'applique qu'aux groupes produits par le mécanisme générique
      ``recovered_constraint_*`` déjà existant ;
    - utilise la confiance de qualification du groupe déjà calculée par
      EnnoDiagnostic (``cluster_role_confidence``).

    Ainsi un verrou récupéré ne reste plus visuellement à 0 % uniquement
    parce qu'il a été découvert après le calcul Frascati.
    """
    source_json = source_json if isinstance(source_json, dict) else {}
    full = source_json.get("full_persisted_verrou")
    full = full if isinstance(full, dict) else {}

    group_id = str(
        item.get("group_id")
        or item.get("cluster_id")
        or full.get("group_id")
        or full.get("cluster_id")
        or ""
    ).strip()

    # Ciblage strict du mécanisme de récupération déjà existant.
    if not group_id.startswith("recovered_constraint_"):
        return None

    for holder in (item, full):
        try:
            value = float(holder.get("cluster_role_confidence"))
        except Exception:
            continue
        if 0.0 < value <= 1.0:
            return round(value, 4)

    return None


def _v2_diagnostic_payload(report: Dict[str, Any]) -> Dict[str, Any]:
    """Retourne la sortie native V2, sans fallback vers les formats V1."""
    if not isinstance(report, dict):
        return {}
    payload = report.get("diagnostic_v2")
    if isinstance(payload, dict) and payload:
        return payload
    if report.get("engine") == "ennodiagnostic_v2":
        # Accepte aussi une vue V2 aplatie pour les imports/outils de test.
        return report
    return {}


def _is_v2_report(report: Dict[str, Any]) -> bool:
    return bool(_v2_diagnostic_payload(report))


def _extract_v2_verrous_from_report(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convertit les entités verrou V2 qualifiées en lignes métier ``Verrou``.

    Les familles restent une vue de synthèse sauvegardée dans le rapport. Les
    décisions consultant portent sur les verrous précis (entités membres).
    """
    payload = _v2_diagnostic_payload(report)
    if not payload:
        return []

    core = payload.get("core") if isinstance(payload.get("core"), dict) else {}
    entities = core.get("entities") or payload.get("entities") or []
    if not isinstance(entities, list):
        entities = []

    family_block = (
        payload.get("lock_families")
        if isinstance(payload.get("lock_families"), dict)
        else {}
    )
    families = family_block.get("families") or []
    if not isinstance(families, list):
        families = []

    family_by_entity_id: Dict[str, Dict[str, Any]] = {}
    for family in families:
        if not isinstance(family, dict):
            continue
        for entity_id in family.get("member_entity_ids") or []:
            key = str(entity_id or "").strip()
            if key and key not in family_by_entity_id:
                family_by_entity_id[key] = family

    accepted_statuses = {"VALID_LOCK", "NEEDS_REFORMULATION"}
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for entity in entities:
        if not isinstance(entity, dict) or entity.get("kind") != "lock":
            continue

        entity_id = str(entity.get("entity_id") or "").strip()
        metadata = (
            entity.get("metadata")
            if isinstance(entity.get("metadata"), dict)
            else {}
        )
        status = str(
            metadata.get("qualification_status") or "NOT_EVALUATED"
        ).strip().upper()
        family = family_by_entity_id.get(entity_id, {})

        # Contrat V2 strict : seules les entités qualifiées VALID_LOCK ou
        # NEEDS_REFORMULATION deviennent des lignes Verrou PostgreSQL.
        # Une famille est uniquement une vue d’affichage et ne peut jamais
        # promouvoir une entité exclue par sa qualification.
        if status not in accepted_statuses:
            continue

        original_statement = _clean_text(entity.get("statement"), 5000)
        suggested_statement = _clean_text(
            metadata.get("suggested_statement"),
            5000,
        )
        title = _clean_text(
            suggested_statement
            if status == "NEEDS_REFORMULATION" and suggested_statement
            else original_statement,
            500,
        )
        if not title:
            continue

        dedupe_key = entity_id or re.sub(r"\s+", " ", title.casefold()).strip()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        evidences = entity.get("evidences") or []
        if not isinstance(evidences, list):
            evidences = []
        evidence_ids = [
            str(evidence.get("evidence_id"))
            for evidence in evidences
            if isinstance(evidence, dict) and evidence.get("evidence_id")
        ]
        source_documents = list(dict.fromkeys(
            _clean_text(
                evidence.get("document_name") or evidence.get("document_id"),
                500,
            )
            for evidence in evidences
            if isinstance(evidence, dict)
            and _clean_text(
                evidence.get("document_name") or evidence.get("document_id")
            )
        ))
        justification = _clean_text(
            metadata.get("qualification_reason")
            or family.get("rationale")
            or original_statement,
            5000,
        )

        out.append(sanitize_json_value({
            "title": title,
            "titre": title,
            "verrou": title,
            "description": justification,
            "justification": justification,
            "text": original_statement or justification,
            "tag_cir": (
                "PERTINENT"
                if status == "VALID_LOCK"
                else "A_REFORMULER"
            ),
            "score": None,
            "consultant_status": "en_attente",
            "source_document": ", ".join(source_documents),
            "needs_human_validation": True,
            "entity_id": entity_id,
            "family_id": family.get("family_id"),
            "family_title": family.get("title"),
            "qualification_status": status,
            "source_json": {
                "engine": "ennodiagnostic_v2",
                "v2_entity": entity,
                "v2_family": family or None,
                "entity_id": entity_id,
                "family_id": family.get("family_id"),
                "qualification_status": status,
                "qualification_reason": metadata.get("qualification_reason"),
                "evidence_ids": evidence_ids,
                "source_documents": source_documents,
                "persistence_version": "ennodiagnostic_v2_backend_1",
            },
        }))

    return out


def _extract_final_verrous_from_report(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extrait les verrous persistables, V2 d'abord puis legacy à l'import."""
    if not isinstance(report, dict):
        return []

    # Un rapport identifié V2 ne doit jamais retomber silencieusement sur les
    # anciens champs final_verrous/llm_reformulated_verrous.
    if _is_v2_report(report):
        return _extract_v2_verrous_from_report(report)

    synthesis = report.get("verrou_synthesis_report")
    synthesis = synthesis if isinstance(synthesis, dict) else {}

    candidates = (
        # La racine du rapport est la sortie finale de l'agent (après les
        # réconciliations et consolidations). Le rapport du synthétiseur reste
        # une piste d'audit et ne doit jamais reprendre la priorité.
        report.get("llm_reformulated_verrous")
        or report.get("consultant_verrous_cir")
        or synthesis.get("final_items")
        or synthesis.get("llm_reformulated_verrous")
        or synthesis.get("accepted_items")
        or synthesis.get("final_verrous")
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

        # V5.7 SCORE-ONLY:
        # Un verrou récupéré après Frascati peut avoir score=0 uniquement parce
        # qu'aucun assessment Frascati n'existait encore pour son nouveau group_id.
        # On conserve ce 0 dans upstream_frascati_score ; seul le score de carte
        # utilise la confiance de qualification déjà calculée.
        if score is None or score <= 0:
            recovered_score = _recovered_lock_display_score(raw, source_json)
            if recovered_score is not None:
                score = recovered_score
                source_json = {
                    **source_json,
                    "display_score_source": "recovered_cluster_role_confidence",
                    "display_score_semantics": "diagnostic_candidate_confidence",
                }

        source_document = _clean_text(
            raw.get("source_document")
            or raw.get("document")
            or raw.get("filename")
            or source_json.get("source_document")
            or source_json.get("document"),
            1200,
        )

        compact_agent_item = {
            key: raw.get(key)
            for key in (
                "title",
                "titre",
                "verrou",
                "tag_cir",
                "decision",
                "score",
                "frascati_score",
                "consultant_status",
                "status",
                "consultant_explanation",
                "why_agent_found_verrou",
                "why_not_simple_engineering",
                "scientific_lock",
                "evidence_summary",
                "source_document",
                "document",
                "group_id",
                "cluster_id",
                "candidate_origin",
                "historical_memory_card",
                "historical_gap_recovered",
                "historical_continuity",
                "continuity_percentage",
            )
            if raw.get(key) not in (None, "", [], {})
        }
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
                # Compatibilité frontend sans recopier récursivement le verrou
                # complet et toutes ses preuves dans son propre source_json.
                "full_agent_item": compact_agent_item,
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
    v2 = _v2_diagnostic_payload(report)
    v2_core = v2.get("core") if isinstance(v2.get("core"), dict) else {}
    v2_scoring = (
        v2.get("scoring") if isinstance(v2.get("scoring"), dict) else {}
    )
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

    frascati = report.get("frascati_summary")
    frascati = frascati if isinstance(frascati, dict) else {}
    compact_frascati = {
        key: value
        for key, value in frascati.items()
        if key not in {"eligibility_evidence_report", "group_assessments"}
    }

    ai_report = report.get("ai_detection_report_runtime") or report.get("ai_detection_report") or {}
    ai_report = ai_report if isinstance(ai_report, dict) else {}
    ai_detection = ai_report.get("ai_detection")
    ai_detection = ai_detection if isinstance(ai_detection, dict) else {}
    compact_ai_report = {
        "ok": ai_report.get("ok"),
        "summary": ai_report.get("summary") or {},
        "top_passages": (
            ai_report.get("top_passages")
            or ai_detection.get("suspected_passages")
            or ai_detection.get("passages")
            or []
        )[:10],
        "ai_detection": {
            key: value
            for key, value in ai_detection.items()
            if key not in {"passages", "suspected_passages", "all_passages"}
        },
    }
    cir_memory = report.get("cir_memory_report")
    cir_memory = cir_memory if isinstance(cir_memory, dict) else {}
    compact_cir_memory = {
        key: cir_memory.get(key)
        for key in (
            "ok",
            "available",
            "comparison_status",
            "error",
            "has_previous_cir",
            "previous_cir_years_used",
            "previous_years",
            "summary",
            "new_or_not_found",
            "evolution_or_partial_continuity",
            "continuity_strong",
            "verrou_comparisons",
            "comparisons",
            "strict_axis_continuity",
            "historical_family_coverage",
        )
        if cir_memory.get(key) not in (None, "", [], {})
    }
    synthesis = report.get("verrou_synthesis_report")
    synthesis = synthesis if isinstance(synthesis, dict) else {}
    compact_synthesis = {
        key: synthesis.get(key)
        for key in ("ok", "version", "status", "summary", "counts", "telemetry")
        if synthesis.get(key) not in (None, "", [], {})
    }

    return sanitize_json_value({
        "snapshot_version": "v149_compact_db_plus_gzip_artifact",
        "generated_at": report.get("generated_at") or datetime.utcnow().isoformat(),
        "engine": report.get("engine"),
        "mode": report.get("mode"),
        "status": report.get("status") or diagnostic.get("status"),
        # Sorties métier natives EnnoDiagnostic V2. Elles restent directement
        # requêtables dans DiagnosticRun.raw_result_json ; les chunks complets
        # restent uniquement dans l'artefact gzip du rapport intégral.
        "eligibility": report.get("eligibility") or v2_scoring.get("eligibility") or {},
        "defensibility": report.get("defensibility") or v2_scoring.get("defensibility") or {},
        "risk_level": report.get("risk_level") or v2_scoring.get("risk_level"),
        "potential_level": report.get("potential_level") or v2_scoring.get("potential_level"),
        "scoring": report.get("scoring") or v2_scoring,
        "lock_families": report.get("lock_families") or v2.get("lock_families") or {},
        "entities": report.get("entities") or v2_core.get("entities") or [],
        "extractions": report.get("extractions") or v2_core.get("extractions") or [],
        "project_synthesis": report.get("project_synthesis") or v2.get("project_synthesis") or {},
        "rnd_approach": report.get("rnd_approach") or v2.get("rnd_approach") or {},
        "project_frascati": report.get("project_frascati") or v2.get("project_frascati") or {},
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
        "frascati_summary": compact_frascati,
        "frascati_justification": report.get("frascati_justification") or {},
        "ai_detection_report": compact_ai_report,
        "style_memory_report": report.get("style_memory_report") or {},
        "cir_memory_report": compact_cir_memory,
        "inputs_status": report.get("inputs_status") or {},
        "pipeline_before_agent": report.get("pipeline_before_agent") or {},
        "verrou_synthesis_report": compact_synthesis,
        "chroma_sections": {},
        "source_paths": {
            "output_path": report.get("output_path"),
        },
    })


def _compact_report_for_database(
    report: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """Vue métier lisible sans dupliquer les énormes preuves internes."""

    return sanitize_json_value({
        "ok": report.get("ok"),
        "status": report.get("status"),
        "engine": report.get("engine"),
        "version": report.get("version"),
        "mode": report.get("mode"),
        "generated_at": report.get("generated_at"),
        "organisme": report.get("organisme"),
        "project": report.get("project"),
        "year": report.get("year"),
        "diagnostic": {
            "status": report.get("status") or "completed",
            "content": snapshot.get("report_markdown") or "",
        },
        "report_markdown": snapshot.get("report_markdown") or "",
        "diagnostic_sections_by_key": snapshot.get("sections_by_key") or {},
        "diagnostic_sections": snapshot.get("sections_by_title") or {},
        "report_sections": snapshot.get("canonical_sections") or {},
        "eligibility": snapshot.get("eligibility") or {},
        "defensibility": snapshot.get("defensibility") or {},
        "risk_level": snapshot.get("risk_level"),
        "potential_level": snapshot.get("potential_level"),
        "scoring": snapshot.get("scoring") or {},
        "lock_families": snapshot.get("lock_families") or {},
        "entities": snapshot.get("entities") or [],
        "extractions": snapshot.get("extractions") or [],
        "project_synthesis": snapshot.get("project_synthesis") or {},
        "rnd_approach": snapshot.get("rnd_approach") or {},
        "project_frascati": snapshot.get("project_frascati") or {},
        "cir_memory_report": snapshot.get("cir_memory_report") or {},
        # Les autres données métier sont dans ``diagnostic_snapshot`` au même
        # niveau de l'enveloppe ; ne pas les recopier une seconde fois ici.
        "inputs_status": report.get("inputs_status") or {},
        "telemetry": report.get("telemetry") or {},
        "storage_policy": "compact_business_view_full_report_in_gzip_artifact",
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
    compact_report = _compact_report_for_database(report, snapshot)
    compact_pipeline_metadata = {
        key: value
        for key, value in (pipeline_result or {}).items()
        if key not in {"report", "bundle", "script_or_pipeline_result"}
    }
    return sanitize_json_value({
        "persistence_version": "v149_compact_db_plus_gzip_artifact",
        "saved_at": datetime.utcnow().isoformat(),
        "button": button,
        "pipeline": pipeline_name,
        "project": {
            "id": project.id,
            "organisme": project.organisme,
            "project_name": project.project_name,
            "subproject_name": getattr(project, "subproject_name", None),
            "year": _year(project),
        },
        # Vue métier requêtable. Le rapport brut complet est ajouté comme
        # artefact gzip après l'obtention de l'identifiant du run.
        "report": compact_report,
        "script_or_pipeline_result": {
            "pipeline_metadata": compact_pipeline_metadata,
        },
        "diagnostic_snapshot": snapshot,
        "prepare_sources_report": prepare_report or {},
        "bundle_metadata": bundle_metadata or {},
    })


def _cleanup_diagnostic_json_workfiles(project: Project) -> list[str]:
    """Supprime seulement les JSON reconstructibles après le commit DB."""

    store = get_project_store(project)
    root = store.project_dir.resolve()
    removed: list[str] = []
    for directory in (
        store.project_dir / "ennodiagnostic",
        store.diagnostics_dir,
    ):
        resolved = directory.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise RuntimeError(f"Dossier de travail diagnostic hors projet : {directory}")
        if not directory.exists():
            continue
        for path in directory.rglob("*.json"):
            file_resolved = path.resolve()
            if not file_resolved.is_relative_to(root):
                raise RuntimeError(f"Artefact diagnostic hors projet : {path}")
            path.unlink(missing_ok=True)
            removed.append(str(path))
        for child in sorted(directory.rglob("*"), reverse=True):
            if child.is_dir():
                try:
                    child.rmdir()
                except OSError:
                    pass
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed


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
        if _is_v2_report(report):
            # Un diagnostic V2 sans verrou qualifié reste un résultat métier
            # valide (par exemple dossier purement ingénierie). Il ne doit pas
            # être transformé en erreur HTTP 422.
            return []
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

    # On remplace uniquement les lignes agent du run courant. Les ajouts
    # explicites du consultant sont des données métier et ne doivent jamais
    # disparaître lors d'une resynchronisation du même run.
    # Les anciens runs restent intacts pour l'audit et leurs articles
    # conservent leurs clés étrangères valides.
    current_rows = (
        db.query(Verrou)
        .filter(Verrou.diagnostic_run_id == int(run.id))
        .all()
    )
    manual_current = []
    agent_current_ids = []
    for current_row in current_rows:
        current_source = (
            current_row.source_json
            if isinstance(current_row.source_json, dict)
            else {}
        )
        is_manual = bool(
            current_source.get("manual_verrou")
            or current_source.get("supplementary_verrou")
            or (
                current_source.get("human_validated") is True
                and current_source.get("automatic_verrou_creation") is False
            )
        )
        if is_manual:
            manual_current.append(current_row)
        else:
            agent_current_ids.append(int(current_row.id))

    deleted_current_count = 0
    if agent_current_ids:
        deleted_current_count = (
            db.query(Verrou)
            .filter(Verrou.id.in_(agent_current_ids))
            .delete(synchronize_session=False)
        )
    db.flush()

    created: List[Verrou] = []
    manual_titles = {
        re.sub(r"\s+", " ", str(row.title or "").lower()).strip()
        for row in manual_current
        if str(row.title or "").strip()
    }

    for item in candidates:
        title = _clean_text(
            item.get("title")
            or item.get("titre")
            or item.get("verrou"),
            500,
        )
        if not title:
            continue

        normalized_title = re.sub(r"\s+", " ", title.lower()).strip()
        if normalized_title in manual_titles:
            # La formulation humaine est prioritaire et reste déjà auto-gardée.
            continue

        source_json = (
            item.get("source_json")
            if isinstance(item.get("source_json"), dict)
            else {}
        )
        source_json = sanitize_json_value({
            **source_json,
            "full_persisted_verrou": {
                key: item.get(key)
                for key in (
                    "title",
                    "tag_cir",
                    "score",
                    "consultant_status",
                    "justification",
                    "description",
                    "consultant_explanation",
                    "why_agent_found_verrou",
                    "why_not_simple_engineering",
                    "scientific_lock",
                    "evidence_summary",
                    "source_document",
                    "group_id",
                    "cluster_id",
                    "candidate_origin",
                    "historical_memory_card",
                    "historical_gap_recovered",
                    "historical_continuity",
                    "continuity_percentage",
                )
                if item.get(key) not in (None, "", [], {})
            },
            "diagnostic_run_id": run.id,
            "project_id": run.project_id,
            "persistence_version": "v145_latest_only_without_history_delete",
            "snapshot_policy": "replace_current_run_only",
        })

        score = _to_float(item.get("score"))
        if score is None or score <= 0:
            recovered_score = _recovered_lock_display_score(item, source_json)
            if recovered_score is not None:
                score = recovered_score
                source_json = sanitize_json_value({
                    **source_json,
                    "display_score_source": "recovered_cluster_role_confidence",
                    "display_score_semantics": "diagnostic_candidate_confidence",
                    "upstream_score_preserved": item.get("upstream_frascati_score"),
                })

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

    synced_rows = [*manual_current, *created]
    if not synced_rows:
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
            "count": len(synced_rows),
            "manual_preserved_count": len(manual_current),
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
                for verrou in synced_rows
            ],
        }
        run.raw_result_json = payload

    print(
        "[EnnoDiagnostic][V145_CURRENT_RUN_SNAPSHOT] "
        f"project_id={run.project_id} "
        f"run_id={run.id} "
        f"deleted_current={int(deleted_current_count or 0)} "
        f"created_current={len(created)} "
        f"manual_preserved={len(manual_current)} "
        "history_preserved=true"
    )

    if commit:
        db.commit()
        db.refresh(run)
        for verrou in synced_rows:
            db.refresh(verrou)

    return synced_rows


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
        full_report_artifact = save_json_artifact(
            db,
            project.id,
            "diagnostics/latest_full_report.json",
            report,
            artifact_kind="diagnostic_full_report",
            metadata={
                "diagnostic_run_id": int(run.id),
                "status": status_value,
                "generated_at": report.get("generated_at"),
            },
        )
        payload = dict(payload)
        payload["full_report_artifact"] = full_report_artifact
        compact_report = dict(payload.get("report") or {})
        compact_report["full_report_artifact_uri"] = full_report_artifact["uri"]
        payload["report"] = compact_report
        run.report_path = full_report_artifact["uri"]
        run.raw_result_json = payload
        synced = sync_verrous_from_diagnostic(db, run, commit=False)
        if not synced and not _is_v2_report(report):
            raise RuntimeError(
                "Le rapport a été généré mais aucun verrou final n'a été trouvé dans "
                "verrou_synthesis_report.llm_reformulated_verrous."
            )
        project.status = "Diagnostic terminé"
        db.commit()
        db.refresh(run)
        try:
            removed = _cleanup_diagnostic_json_workfiles(project)
            if removed:
                print(
                    "[EnnoDiagnostic][DB_ARTIFACT_CLEANUP] "
                    f"removed_json={len(removed)}",
                    flush=True,
                )
        except Exception as cleanup_exc:
            # La donnée officielle est déjà commitée. Un échec de ménage ne doit
            # jamais invalider le diagnostic ; il sera repris au prochain reset.
            print(
                f"[EnnoDiagnostic][DB_ARTIFACT_CLEANUP][WARN] {cleanup_exc}",
                flush=True,
            )
        return run
    except Exception:
        db.rollback()
        raise


@exclusive_project_diagnostic
def prepare_ennodiagnostic_sources(db: Session, project: Project) -> Dict[str, Any]:
    """Prépare uniquement le fulltext utilisé par EnnoDiagnostic V2."""
    preparation = _prepare_ennodiagnostic_v2_sources(db, project)
    ps = get_project_store(project)

    result = {
        "ok": True,
        "step": "prepare_sources",
        "engine": "ennodiagnostic_v2",
        "pipeline": "documents_raw_extraction_fulltext_v2",
        "response_policy": "compact_fulltext_manifest_no_document_text",
        "project": {
            "id": project.id,
            "organisme": project.organisme,
            "project_name": project.project_name,
            "subproject_name": getattr(project, "subproject_name", None),
            "year": _year(project),
        },
        "documents_used_count": preparation.get("documents_used_count"),
        "documents_loaded_count": preparation.get("documents_loaded_count"),
        "documents_used_paths": preparation.get("documents_used_paths") or [],
        "corpus_manifest": preparation.get("corpus_manifest") or {},
        "reset_report": preparation.get("reset_report") or {},
        "visual_asset_report": preparation.get("visual_asset_report") or {},
        "fulltext_corpus": preparation.get("fulltext_corpus") or {},
        "paths": {
            "project_dir": str(ps.project_dir),
            "raw_dir": str(ps.documents_raw_dir),
            "fulltext_corpus": preparation.get("fulltext_corpus_path"),
            "visual_assets_manifest": str(
                ps.documents_processed_dir
                / "visual_assets_v1"
                / "manifest.json"
            ),
            "prepare_report": artifact_uri(
                project.id,
                PREPARE_REPORT_ARTIFACT_KEY,
            ),
        },
        "raw_pipeline_result": {
            "pipeline": "fulltext_to_ennodiagnostic_v2",
            "documents_used_count": preparation.get("documents_used_count"),
            "documents_loaded_count": preparation.get("documents_loaded_count"),
            "documents_used_paths": preparation.get("documents_used_paths") or [],
            "fulltext_corpus": preparation.get("fulltext_corpus") or {},
            "fulltext_corpus_path": preparation.get("fulltext_corpus_path"),
            "corpus_manifest": preparation.get("corpus_manifest") or {},
        },
        "generated_at": datetime.utcnow().isoformat(),
    }

    # Sauvegarde compacte en base : aucune copie JSON permanente sur le VPS.
    save_json_artifact(
        db,
        project.id,
        PREPARE_REPORT_ARTIFACT_KEY,
        result,
        artifact_kind="diagnostic_prepare_report",
    )

    project.status = "Sources V2 préparées"
    db.commit()

    print(
        "[prepare-sources] Reponse HTTP compacte prete",
        flush=True,
    )
    return result

@exclusive_project_diagnostic
def run_ennodiagnostic_agent_only(db: Session, project: Project) -> DiagnosticRun:
    """
    Lance V2 depuis le corpus fulltext déjà préparé puis sauvegarde le résultat.
    """
    ps = get_project_store(project)
    from services.ennodiagnostic_v2_service import load_v2_fulltext_manifest

    fulltext_manifest = load_v2_fulltext_manifest(ps)
    fulltext_ready = bool(
        int(fulltext_manifest.get("documents_count") or 0) > 0
    )
    if not fulltext_ready:
        raise RuntimeError(
            "Corpus fulltext V2 non préparé. Lance d'abord "
            "/diagnostic/prepare-sources ou utilise /diagnostic/run."
        )

    prepare_report = _load_prepare_report(db, project)
    prepared_manifest = (
        prepare_report.get("corpus_manifest")
        if isinstance(prepare_report.get("corpus_manifest"), dict)
        else {}
    )
    current_manifest = diagnostic_corpus_manifest(db, project.id)
    if (
        not prepared_manifest.get("fingerprint")
        or prepared_manifest.get("fingerprint") != current_manifest.get("fingerprint")
    ):
        raise RuntimeError(
            "Le corpus Diagnostic a changé depuis la dernière préparation. "
            "Cliquez sur « Préparer les sources » avant de relancer EnnoDiagnostic."
        )
    prior_pipeline = (
        prepare_report.get("raw_pipeline_result")
        if isinstance(prepare_report.get("raw_pipeline_result"), dict)
        else None
    )
    from services.diagnostic_reset_service import reset_previous_agent_runs
    reset_previous_agent_runs(db, project, ps)
    report = run_true_ennodiagnostic_agent(
        project,
        prior_pipeline=prior_pipeline,
    )

    return _persist_complete_run(
        db,
        project,
        report=report,
        status_value="completed_ennodiagnostic_v2",
        pipeline_name="fulltext_ennodiagnostic_v2_complete_db",
        button="ennodiagnostic_agent_only",
        report_path=artifact_uri(
            project.id,
            "diagnostics/latest_full_report.json",
        ),
        nlp_result_path=None,
        selected_verrous_path=None,
        prepare_report=prepare_report,
        pipeline_result={
            "agent_only": True,
            "engine": "ennodiagnostic_v2",
            "fulltext_corpus": fulltext_manifest,
        },
        bundle_metadata={
            "report_path_used": artifact_uri(
                project.id,
                "diagnostics/latest_full_report.json",
            ),
            "fulltext_corpus_path": str(
                Path(ps.documents_processed_dir) / "fulltext_rag_v1"
            ),
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
        report_path=artifact_uri(
            project.id,
            "diagnostics/latest_full_report.json",
        ),
        nlp_result_path=str(paths["nlp_result"]) if paths["nlp_result"].exists() else None,
        selected_verrous_path=str(paths["selected_verrous"]) if paths["selected_verrous"].exists() else None,
        pipeline_result={"import_existing": True},
        bundle_metadata={
            "report_path_used": bundle.get("report_path_used"),
            "report_candidates_found": bundle.get("report_candidates_found"),
            "files_found": bundle.get("files_found"),
        },
    )


@exclusive_project_diagnostic
def run_ennodiagnostic(db: Session, project: Project) -> DiagnosticRun:
    """Un seul bouton backend : extraction fulltext -> RAG si nécessaire -> V2 -> persistance DB."""
    pipeline_result = sanitize_json_value(
        run_full_ennodiagnostic_pipeline(db, project)
    )
    report = _report_from_pipeline_result(pipeline_result)
    prepare_report = (
        pipeline_result.get("prepare_report")
        if isinstance(pipeline_result.get("prepare_report"), dict)
        else _load_prepare_report(db, project)
    )
    ps = get_project_store(project)

    return _persist_complete_run(
        db,
        project,
        report=report,
        status_value="completed_ennodiagnostic_v2",
        pipeline_name="extraction_fulltext_ennodiagnostic_v2_complete_db",
        button="single_ennodiagnostic_button",
        report_path=artifact_uri(
            project.id,
            "diagnostics/latest_full_report.json",
        ),
        nlp_result_path=None,
        selected_verrous_path=None,
        prepare_report=prepare_report,
        pipeline_result=pipeline_result,
        bundle_metadata={
            "report_path_used": artifact_uri(
                project.id,
                "diagnostics/latest_full_report.json",
            ),
            "fulltext_corpus_path": str(
                Path(ps.documents_processed_dir) / "fulltext_rag_v1"
            ),
        },
    )
