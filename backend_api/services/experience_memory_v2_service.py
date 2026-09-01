# -*- coding: utf-8 -*-
from __future__ import annotations

"""Façade backend de la vraie mémoire CIR ``experience_memory_v2``.

La mémoire V2 est volontairement indépendante de la table PostgreSQL
``projects`` : elle représente le corpus validé du cabinet, partagé par les
agents, et non les dossiers opérationnels affectés aux consultants.
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from core.config import settings
from modules.common.runtime_paths import code_root


ROOT_DIR = code_root()
V2_ROOT = Path(
    settings.ENNOSMART_EXPERIENCE_MEMORY_V2_DIR
)
ORGANISMES_DIR = Path(
    settings.ENNOSMART_MEMORY_V2_ROOT
)
V2_CATALOG = V2_ROOT / "catalog_v2.json"
V2_CHROMA_DIR = V2_ROOT / "chroma"
V2_RUNS_DIR = V2_ROOT / "runs"
V2_CARDS_DIR = V2_ROOT / "cards"

SUPPORTED_EXTENSIONS = {".docx", ".pdf", ".txt", ".md"}
FORBIDDEN_SEGMENT = re.compile(r"[\\/:*?\"<>|]")
BUILD_LOCK = threading.RLock()
ENGINE_MODULE_NAME = "_ennosmart_experience_memory_v2_engine"
ENGINE_PATH = ROOT_DIR / "scripts" / "experience_memory_v2_engine.py"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _clean(value: Any, limit: int = 255) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _identity_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", text) or "unknown"


def _project_key(organisme: Any, project: Any, year: Any, subproject: Any = "") -> str:
    return "::".join((
        _identity_key(organisme),
        _identity_key(project),
        _identity_key(subproject),
        _clean(year, 12),
    ))


def _catalog_identity(raw: Any) -> Dict[str, str] | None:
    parts = str(raw or "").split("::")
    if len(parts) == 3:
        organisme, project, year = parts
        return {"organisme": organisme, "project": project, "subproject": "", "year": year}
    if len(parts) == 4:
        organisme, project, subproject, year = parts
        return {
            "organisme": organisme,
            "project": project,
            "subproject": subproject,
            "year": year,
        }
    return None


def _safe_segment(value: Any, label: str) -> str:
    segment = _clean(value)
    if not segment or segment in {".", ".."} or FORBIDDEN_SEGMENT.search(segment):
        raise ValueError(f"{label} invalide.")
    return segment


def _is_year(value: Any) -> bool:
    raw = _clean(value, 12)
    return bool(re.fullmatch(r"(?:19|20)\d{2}", raw))


def _is_final_cir_path(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return "cir_final_consultant" in parts or "cir_final" in parts


def _iter_final_cir_files() -> Iterable[Path]:
    if not ORGANISMES_DIR.is_dir():
        return
    for path in ORGANISMES_DIR.rglob("*"):
        if (
            path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and not path.name.startswith("~$")
            and _is_final_cir_path(path)
        ):
            yield path


def _identity_from_library_path(path: Path) -> Dict[str, str]:
    parts = list(path.parts)
    lower = [part.lower() for part in parts]
    organisme = project = year = "unknown"
    subproject = ""
    try:
        index = lower.index("organismes")
        organisme = parts[index + 1]
    except Exception:
        pass
    try:
        index = lower.index("projects")
        project = parts[index + 1]
    except Exception:
        pass
    try:
        index = lower.index("years")
        year = parts[index + 1]
    except Exception:
        pass
    try:
        index = lower.index("subprojects")
        subproject = parts[index + 1]
    except Exception:
        pass
    return {
        "organisme": organisme,
        "project": project,
        "subproject": subproject,
        "year": year,
    }


def _library_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in _iter_final_cir_files() or []:
        identity = _identity_from_library_path(path)
        stat = path.stat()
        rows.append(
            {
                **identity,
                "file_name": path.name,
                "file_path": str(path),
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
        )
    rows.sort(key=lambda row: (
        row["organisme"].lower(),
        row["project"].lower(),
        row.get("subproject", "").lower(),
        row["year"],
        row["file_name"],
    ))
    return rows


def _run_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not V2_RUNS_DIR.is_dir():
        return rows
    for path in sorted(V2_RUNS_DIR.glob("*.run_v2.json")):
        run = _read_json(path, {})
        if not isinstance(run, dict) or not run.get("ok"):
            continue
        rows.append(
            {
                "source_id": run.get("source_id"),
                "source_hash": run.get("source_hash"),
                "organisme": _clean(run.get("organisme")) or "unknown",
                "project": _clean(run.get("project")) or "unknown",
                "subproject": _clean(run.get("subproject")),
                "year": _clean(run.get("year"), 12) or "unknown",
                "file_name": run.get("file_name"),
                "file_path": run.get("file"),
                "chunks_count": int(run.get("chunks_count") or 0),
                "cards_count": int(run.get("cards_count") or 0),
                "role_counts": run.get("role_counts") or {},
                "memory_counts": run.get("memory_counts") or {},
                "domain_counts": run.get("domain_counts") or {},
                "elapsed_seconds": run.get("elapsed_seconds"),
                "indexed_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return rows


def _chroma_collections() -> List[Dict[str, Any]]:
    database = V2_CHROMA_DIR / "chroma.sqlite3"
    if not database.is_file():
        return []
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=2)
        rows = connection.execute(
            """
            SELECT c.name, COUNT(e.id)
            FROM collections c
            JOIN segments s ON s.collection = c.id AND s.scope = 'METADATA'
            LEFT JOIN embeddings e ON e.segment_id = s.id
            GROUP BY c.id, c.name
            ORDER BY c.name
            """
        ).fetchall()
        return [{"name": str(name), "items_count": int(count or 0)} for name, count in rows]
    except Exception:
        return []
    finally:
        if connection is not None:
            connection.close()


def _runtime_dependencies() -> Dict[str, bool]:
    return {
        "chromadb": importlib.util.find_spec("chromadb") is not None,
        "sentence_transformers": importlib.util.find_spec("sentence_transformers") is not None,
    }


def get_memory_v2_catalog() -> Dict[str, Any]:
    """Retourne la bibliothèque réelle, les index V2 et l'état de Chroma."""
    catalog = _read_json(V2_CATALOG, {})
    if not isinstance(catalog, dict):
        catalog = {}

    merged: Dict[str, Dict[str, Any]] = {}
    for raw in catalog.get("projects") or []:
        identity = _catalog_identity(raw)
        if not identity:
            continue
        key = _project_key(
            identity["organisme"], identity["project"], identity["year"], identity["subproject"]
        )
        merged[key] = {
            **identity,
            "indexed": True,
            "source_files": [],
            "chunks_count": 0,
            "cards_count": 0,
            "role_counts": {},
            "memory_counts": {},
            "domain_counts": {},
        }

    for run in _run_rows():
        key = _project_key(run["organisme"], run["project"], run["year"], run.get("subproject"))
        row = merged.setdefault(
            key,
            {
                "organisme": run["organisme"],
                "project": run["project"],
                "subproject": run.get("subproject") or "",
                "year": run["year"],
                "indexed": True,
                "source_files": [],
            },
        )
        row.update({
            k: v for k, v in run.items()
            if k not in {"organisme", "project", "subproject", "year", "file_path", "file_name"}
        })
        row["indexed"] = True
        if run.get("file_name"):
            row["indexed_file_name"] = run["file_name"]
            row["indexed_file_path"] = run.get("file_path")

    for source in _library_rows():
        key = _project_key(
            source["organisme"], source["project"], source["year"], source.get("subproject")
        )
        row = merged.setdefault(
            key,
            {
                "organisme": source["organisme"],
                "project": source["project"],
                "subproject": source.get("subproject") or "",
                "year": source["year"],
                "indexed": False,
                "chunks_count": 0,
                "cards_count": 0,
                "role_counts": {},
                "memory_counts": {},
                "domain_counts": {},
                "source_files": [],
            },
        )
        row.setdefault("source_files", []).append(source)
        indexed_name = _clean(row.get("indexed_file_name")).lower()
        if indexed_name and indexed_name == _clean(source.get("file_name")).lower():
            row["source_indexed"] = True

    projects = list(merged.values())
    for row in projects:
        row["id"] = _project_key(
            row["organisme"], row["project"], row["year"], row.get("subproject")
        )
        row["status"] = "indexed" if row.get("indexed") else "pending"
        row["source_count"] = len(row.get("source_files") or [])
    projects.sort(key=lambda row: (
        str(row["organisme"]).lower(),
        str(row["project"]).lower(),
        str(row.get("subproject") or "").lower(),
        -int(row["year"]) if str(row["year"]).isdigit() else 0,
    ))

    collections = _chroma_collections()
    global_collection = next((item for item in collections if item["name"] == "ennosmart_memory_v2_global"), None)
    dependencies = _runtime_dependencies()
    organisms = sorted(
        {str(row["organisme"]) for row in projects if row.get("organisme")}
        | {str(value) for value in catalog.get("organisms") or [] if value}
    )

    return {
        "ok": True,
        "version": catalog.get("version") or "v2_final",
        "source": "experience_memory_v2",
        "updated_at": catalog.get("updated_at"),
        "paths": {
            "v2_root": str(V2_ROOT),
            "catalog": str(V2_CATALOG),
            "chroma": str(V2_CHROMA_DIR),
            "inside_code_repository": V2_ROOT.resolve().is_relative_to(ROOT_DIR.resolve()),
            "source_documents": "not_retained",
        },
        "stats": {
            "organisms_count": len(organisms),
            "projects_count": len(projects),
            "indexed_projects_count": sum(1 for row in projects if row.get("indexed")),
            "pending_projects_count": sum(1 for row in projects if not row.get("indexed")),
            "chunks_count": int(catalog.get("chunks_count") or 0),
            "cards_count": int(catalog.get("cards_count") or 0),
            "relations_count": int(catalog.get("relations_count") or 0),
            "vector_items_count": int((global_collection or {}).get("items_count") or 0),
        },
        "organisms": organisms,
        "projects": projects,
        "role_counts": catalog.get("role_counts") or {},
        "domain_counts": catalog.get("domain_counts") or {},
        "vector_db": {
            "exists": (V2_CHROMA_DIR / "chroma.sqlite3").is_file(),
            "collection": "ennosmart_memory_v2_global",
            "collections": collections,
            "mode": "single_global_collection",
            "source_file_copy_policy": "disabled",
            "runtime_dependencies": dependencies,
            "runtime_ready": all(dependencies.values()),
        },
        "ai_connections": {
            "ennodiagnostic": (ROOT_DIR / "modules" / "EXPERIENCE_MEMORY" / "memory_v2_retriever.py").is_file(),
            "cir_comparison": (ROOT_DIR / "modules" / "CIR_MEMORY" / "cir_memory_v2_adapter.py").is_file(),
            "writing_style": (ROOT_DIR / "modules" / "CIR_STYLE_MEMORY" / "style_memory.py").is_file(),
            "usage_rule": "Contexte historique, comparaison et style uniquement ; jamais preuve factuelle du projet courant.",
        },
    }


def create_library_slot(
    organisme: Any,
    project: Any,
    year: Any,
    subproject: Any = "",
) -> Dict[str, Any]:
    organisme_name = _safe_segment(organisme, "Entreprise")
    project_name = _safe_segment(project, "Projet")
    subproject_name = _safe_segment(subproject, "Sous-projet") if _clean(subproject) else ""
    year_value = _clean(year, 12)
    if not _is_year(year_value):
        raise ValueError("Année invalide.")
    return {
        "ok": True,
        "created": False,
        "logical_only": True,
        "organisme": organisme_name,
        "project": project_name,
        "subproject": subproject_name,
        "year": year_value,
        "upload_dir": None,
        "message": "Identité validée. Aucun dossier ni copie de document n'a été créé.",
    }


def save_upload_to_temp(filename: str, content: bytes) -> Path:
    if not content:
        raise ValueError("Fichier vide.")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError("Format non supporté. Utilise PDF, DOCX, TXT ou MD.")
    safe_stem = re.sub(r"[^A-Za-z0-9_. -]+", "_", Path(filename).stem).strip(" ._") or "cir_final"
    temp_dir = V2_ROOT / "_tmp_uploads" / hashlib.sha256(content).hexdigest()[:16]
    temp_dir.mkdir(parents=True, exist_ok=True)
    target = temp_dir / f"{safe_stem}{suffix}"
    target.write_bytes(content)
    return target


def _load_engine():
    """Charge le moteur racine sans être masqué par ``backend_api/scripts``."""
    if not ENGINE_PATH.is_file():
        raise FileNotFoundError(f"Moteur Memory V2 introuvable : {ENGINE_PATH}")

    cached = sys.modules.get(ENGINE_MODULE_NAME)
    if cached is not None and Path(str(getattr(cached, "__file__", ""))).resolve() == ENGINE_PATH.resolve():
        return cached

    root_value = str(ROOT_DIR)
    if not sys.path or sys.path[0] != root_value:
        try:
            sys.path.remove(root_value)
        except ValueError:
            pass
        sys.path.insert(0, root_value)

    spec = importlib.util.spec_from_file_location(ENGINE_MODULE_NAME, ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossible de charger le moteur Memory V2 : {ENGINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[ENGINE_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(ENGINE_MODULE_NAME, None)
        raise
    return module


def build_uploaded_cir(
    temp_file: Path,
    *,
    organisme: Any,
    project: Any,
    year: Any,
    subproject: Any = "",
    vision_mode: str = "text_only",
    formula_mode: str = "off",
    rebuild_catalog: bool = True,
    reset_chroma: bool = True,
) -> Dict[str, Any]:
    # Valide l'identité sans créer de dossier visible. La bibliothèque ne doit
    # être matérialisée que par le moteur, pendant l'indexation effective.
    organisme_name = _safe_segment(organisme, "Entreprise")
    project_name = _safe_segment(project, "Projet")
    subproject_name = _safe_segment(subproject, "Sous-projet") if _clean(subproject) else ""
    year_value = _clean(year, 12)
    if not _is_year(year_value):
        raise ValueError("Année invalide.")
    engine = _load_engine()
    with BUILD_LOCK:
        result = engine.build_cir_final_v2(
            temp_file,
            organisme=organisme_name,
            project=project_name,
            subproject=subproject_name,
            year=year_value,
            copy_to_library=False,
            # En lot, la reconstruction globale est différée jusqu'au dernier
            # document afin de ne pas réencoder tout le corpus à chaque CIR.
            reset_chroma=bool(reset_chroma),
            vision_mode=vision_mode if vision_mode in {"text_only", "auto", "fast", "full"} else "text_only",
            formula_mode=formula_mode if formula_mode in {"off", "fast", "explain"} else "off",
            rebuild_catalog=bool(rebuild_catalog),
        )
    result["catalog"] = get_memory_v2_catalog()
    return result


def _resolve_existing_source(
    organisme: Any,
    project: Any,
    year: Any,
    file_name: Any = "",
    subproject: Any = "",
) -> Path:
    wanted_key = _project_key(organisme, project, year, subproject)
    wanted_name = _clean(file_name).lower()
    candidates: List[Path] = []
    for path in _iter_final_cir_files() or []:
        identity = _identity_from_library_path(path)
        if _project_key(
            identity["organisme"], identity["project"], identity["year"], identity.get("subproject")
        ) != wanted_key:
            continue
        if wanted_name and path.name.lower() != wanted_name:
            continue
        candidates.append(path)
    if not candidates:
        raise FileNotFoundError("CIR final introuvable dans la bibliothèque.")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def process_existing_cir(
    organisme: Any,
    project: Any,
    year: Any,
    file_name: Any = "",
    subproject: Any = "",
) -> Dict[str, Any]:
    source = _resolve_existing_source(organisme, project, year, file_name, subproject)
    identity = _identity_from_library_path(source)
    engine = _load_engine()
    with BUILD_LOCK:
        result = engine.build_cir_final_v2(
            source,
            organisme=identity["organisme"],
            project=identity["project"],
            subproject=identity.get("subproject") or "",
            year=identity["year"],
            copy_to_library=False,
            reset_chroma=True,
            vision_mode="text_only",
            formula_mode="off",
        )
    result["catalog"] = get_memory_v2_catalog()
    return result


def rebuild_memory_v2() -> Dict[str, Any]:
    engine = _load_engine()
    with BUILD_LOCK:
        result = engine.rebuild_global_graph_and_catalog(reset_chroma=True)
    return {"ok": True, "result": result, "catalog": get_memory_v2_catalog()}


def _assert_inside(path: Path, parent: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(parent.resolve())
    except ValueError as exc:
        raise ValueError(f"Chemin hors de la zone Memory V2 autorisée : {resolved}") from exc
    return resolved


def _artifact_source_ids_for_project(
    organisme: str,
    project: str,
    year: str,
    subproject: str = "",
) -> set[str]:
    wanted = _project_key(organisme, project, year, subproject)
    source_ids: set[str] = set()

    for run_path in V2_RUNS_DIR.glob("*.run_v2.json") if V2_RUNS_DIR.is_dir() else []:
        run = _read_json(run_path, {})
        if isinstance(run, dict) and _project_key(
            run.get("organisme"), run.get("project"), run.get("year"), run.get("subproject")
        ) == wanted:
            source_ids.add(str(run.get("source_id") or run_path.name.removesuffix(".run_v2.json")))

    # Couvre aussi un traitement interrompu après les chunks mais avant le run.
    for chunk_path in V2_ROOT.joinpath("chunks").glob("*.chunks_v2.json") if V2_ROOT.joinpath("chunks").is_dir() else []:
        chunks = _read_json(chunk_path, [])
        if not isinstance(chunks, list) or not chunks:
            continue
        metadata = dict((chunks[0] or {}).get("metadata") or {}) if isinstance(chunks[0], dict) else {}
        if _project_key(
            metadata.get("organisme"),
            metadata.get("project"),
            metadata.get("year") or metadata.get("annee"),
            metadata.get("subproject"),
        ) == wanted:
            source_ids.add(chunk_path.name.removesuffix(".chunks_v2.json"))
    return {value for value in source_ids if value}


def remove_memory_v2_project(
    organisme: Any,
    project: Any,
    year: Any,
    *,
    subproject: Any = "",
    confirmation: Any,
) -> Dict[str, Any]:
    """Retire un projet de toute la mémoire active, sans toucher à la boîte d'import."""
    if str(confirmation or "").strip() != "SUPPRIMER_DE_MEMORY_V2":
        raise PermissionError("Confirmation explicite requise : SUPPRIMER_DE_MEMORY_V2")

    organisme_name = _safe_segment(organisme, "Entreprise")
    project_name = _safe_segment(project, "Projet")
    subproject_name = _safe_segment(subproject, "Sous-projet") if _clean(subproject) else ""
    year_value = _clean(year, 12)
    if not _is_year(year_value):
        raise ValueError("Année invalide.")

    wanted = _project_key(organisme_name, project_name, year_value, subproject_name)
    source_dirs: list[Path] = []
    for source in _iter_final_cir_files() or []:
        identity = _identity_from_library_path(source)
        if _project_key(
            identity["organisme"], identity["project"], identity["year"], identity.get("subproject")
        ) == wanted:
            current_dir = _assert_inside(source.parent, ORGANISMES_DIR)
            if current_dir not in source_dirs:
                source_dirs.append(current_dir)

    source_ids = _artifact_source_ids_for_project(
        organisme_name, project_name, year_value, subproject_name
    )
    artifact_paths: list[Path] = []
    suffixes = {
        "extraction": ".extraction.json",
        "nlp": ".nlp_result.json",
        "chunks": ".chunks_v2.json",
        "cards": ".cards.json",
        "runs": ".run_v2.json",
    }
    for source_id in source_ids:
        for folder, suffix in suffixes.items():
            candidate = _assert_inside(V2_ROOT / folder / f"{source_id}{suffix}", V2_ROOT)
            if candidate.is_file():
                artifact_paths.append(candidate)

    catalog = _read_json(V2_CATALOG, {})
    catalog_has_project = any(
        identity is not None
        and _project_key(
            identity["organisme"], identity["project"], identity["year"], identity["subproject"]
        ) == wanted
        for raw in (catalog.get("projects") or [])
        for identity in [_catalog_identity(raw)]
    ) if isinstance(catalog, dict) else False

    if not source_dirs and not artifact_paths and not catalog_has_project:
        raise FileNotFoundError("Projet introuvable dans Memory V2.")

    deletion_id = f"removed_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.sha1(wanted.encode('utf-8')).hexdigest()[:8]}"
    archive_root = _assert_inside(V2_ROOT / "_removed_projects" / deletion_id, V2_ROOT)
    moved: list[dict[str, str]] = []

    with BUILD_LOCK:
        try:
            for source_dir in source_dirs:
                relative = source_dir.relative_to(ORGANISMES_DIR.resolve())
                destination = archive_root / "library" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source_dir), str(destination))
                moved.append({"kind": "library", "from": str(source_dir), "to": str(destination)})

            for artifact in sorted(set(artifact_paths)):
                relative = artifact.relative_to(V2_ROOT.resolve())
                destination = archive_root / "artifacts" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(artifact), str(destination))
                moved.append({"kind": "artifact", "from": str(artifact), "to": str(destination)})

            engine = _load_engine()
            rebuild = engine.rebuild_global_graph_and_catalog(reset_chroma=True)
        except Exception:
            # La suppression est transactionnelle : si Chroma/catalogue ne peut
            # pas être reconstruit, tous les éléments sont remis à leur place.
            for item in reversed(moved):
                original = Path(item["from"])
                archived = Path(item["to"])
                if not archived.exists():
                    continue
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(archived), str(original))
            try:
                _load_engine().rebuild_global_graph_and_catalog(reset_chroma=True)
            except Exception:
                pass
            raise

        report = {
            "ok": True,
            "removed_from_active_memory": True,
            "organisme": organisme_name,
            "project": project_name,
            "subproject": subproject_name,
            "year": year_value,
            "source_ids": sorted(source_ids),
            "moved_items_count": len(moved),
            "archive_root": str(archive_root),
            "recoverable": True,
            "sharepoint_modified": False,
            "power_automate_inbox_modified": False,
            "rebuild": rebuild,
            "removed_at": datetime.now().isoformat(timespec="seconds"),
        }
        report_path = archive_root / "removal_report.json"
        report_path.write_text(json.dumps(report | {"moved": moved}, ensure_ascii=False, indent=2), encoding="utf-8")

    report["catalog"] = get_memory_v2_catalog()
    return report


def search_memory_v2(
    query: Any,
    *,
    organisme: Any = "",
    role: Any = "",
    top_k: int = 8,
) -> Dict[str, Any]:
    question = _clean(query, 3000)
    if not question:
        raise ValueError("Question obligatoire.")
    top_k = max(1, min(int(top_k), 30))
    role_value = _clean(role, 50)
    engine = _load_engine()
    collection = "ennosmart_memory_v2_global"
    result = engine.search_v2(
        question,
        collection=collection,
        top_k=top_k,
        role=role_value,
        organisme=_clean(organisme),
    )
    result["usage_rule"] = "Contexte historique et style uniquement ; pas une preuve factuelle du dossier courant."
    return result


def project_cards(
    organisme: Any,
    project: Any,
    year: Any,
    limit: int = 40,
    subproject: Any = "",
) -> Dict[str, Any]:
    wanted = _project_key(organisme, project, year, subproject)
    cards: List[Dict[str, Any]] = []
    for path in sorted(V2_CARDS_DIR.glob("*.cards.json")) if V2_CARDS_DIR.is_dir() else []:
        data = _read_json(path, [])
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            if _project_key(
                item.get("organisme"), item.get("project"), item.get("year"), item.get("subproject")
            ) == wanted:
                cards.append(item)
                if len(cards) >= max(1, min(limit, 200)):
                    return {"ok": True, "count": len(cards), "cards": cards}
    return {"ok": True, "count": len(cards), "cards": cards}
