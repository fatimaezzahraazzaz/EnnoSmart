# -*- coding: utf-8 -*-
from __future__ import annotations

"""
routers/cir_memory.py — Routes API CIR Memory Builder V1

Version corrigée :
- Corrige l'erreur PostgreSQL : projects.year est en VARCHAR dans ta DB.
- Ajoute la route création rapide projet :
  POST /cir-memory/projects/quick-create
- Compatible avec consultant_id, user_id, owner_id, created_by_id.
"""

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from core.deps import get_current_user, get_db
from db.models import Article, Project, ScholarRun, User
from services.project_service import get_project_for_user
from services.cir_memory_service import (
    build_all_memory_for_project,
    build_validated_memory_from_cir_final,
    build_working_memory_from_diagnostic,
    build_working_memory_from_scholar,
    cir_memory_paths,
    clean_text,
    ensure_memory_dirs,
    get_project_memory_status,
    rebuild_organism_memory_index,
    search_organism_memory,
    slugify,
)


router = APIRouter(tags=["cir-memory"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _articles_for_project(db: Session, project_id: int) -> List[Article]:
    return (
        db.query(Article)
        .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
        .filter(ScholarRun.project_id == project_id)
        .order_by(Article.created_at.desc())
        .all()
    )


def _project_to_dict(project: Project) -> Dict[str, Any]:
    return {
        "id": getattr(project, "id", None),
        "consultant_id": getattr(project, "consultant_id", None),
        "organisme": getattr(project, "organisme", None),
        "project_name": getattr(project, "project_name", None),
        "year": getattr(project, "year", None),
        "domain_label": getattr(project, "domain_label", None),
        "status": getattr(project, "status", None),
        "ai_folder": getattr(project, "ai_folder", None),
    }


def _column_python_type(column: Any) -> Any:
    try:
        return column.type.python_type
    except Exception:
        return None


def _value_for_column(column: Any, value: Any) -> Any:
    """
    Convertit la valeur selon le type réel SQLAlchemy.
    Important ici : dans ta DB, projects.year est VARCHAR,
    donc il faut envoyer "2025" et pas 2025.
    """
    py_type = _column_python_type(column)

    if value is None:
        return None

    if py_type is str:
        return str(value)

    if py_type is int:
        try:
            return int(value)
        except Exception:
            return 0

    if py_type is float:
        try:
            return float(value)
        except Exception:
            return 0.0

    if py_type is bool:
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "oui", "on"}

    return value


def _set_if_column(values: Dict[str, Any], columns: Dict[str, Any], name: str, value: Any) -> None:
    if name in columns:
        values[name] = _value_for_column(columns[name], value)


def _get_year_db_value(columns: Dict[str, Any], year_int: int) -> Any:
    if "year" not in columns:
        return year_int
    return _value_for_column(columns["year"], year_int)


def _filter_owner_if_exists(q: Any, current_user: User) -> Any:
    """
    Applique le filtre utilisateur si une colonne propriétaire existe.
    Ton modèle Project a consultant_id d'après le traceback.
    """
    if hasattr(Project, "consultant_id"):
        return q.filter(Project.consultant_id == current_user.id)

    if hasattr(Project, "user_id"):
        return q.filter(Project.user_id == current_user.id)

    if hasattr(Project, "owner_id"):
        return q.filter(Project.owner_id == current_user.id)

    if hasattr(Project, "created_by_id"):
        return q.filter(Project.created_by_id == current_user.id)

    return q


# ---------------------------------------------------------------------------
# Création rapide projet depuis interface Mémoire CIR
# ---------------------------------------------------------------------------

@router.post("/cir-memory/projects/quick-create")
def quick_create_project_for_cir_memory(
    payload: Dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Création rapide d'un projet/année depuis l'interface Mémoire CIR.

    Payload accepté :
    {
      "organisme": "Scalian",
      "project_name": "AI-Code",
      "year": 2025,
      "domain_label": "IA / Génie logiciel"
    }
    """

    organisme = clean_text(
        payload.get("organisme")
        or payload.get("client")
        or payload.get("organization")
        or payload.get("organisme_name"),
        255,
    )

    project_name = clean_text(
        payload.get("project_name")
        or payload.get("project")
        or payload.get("name")
        or payload.get("title"),
        255,
    )

    year_raw = payload.get("year")
    try:
        year_int = int(year_raw)
    except Exception:
        year_int = 0

    domain_label = clean_text(
        payload.get("domain_label")
        or payload.get("domain")
        or payload.get("domaine"),
        255,
    )

    if not organisme:
        raise HTTPException(status_code=400, detail="organisme obligatoire.")

    if not project_name:
        raise HTTPException(status_code=400, detail="project_name obligatoire.")

    if year_int < 1900 or year_int > 2100:
        raise HTTPException(status_code=400, detail="year invalide. Exemple : 2025.")

    mapper = sa_inspect(Project)
    columns = {c.name: c for c in mapper.columns}

    year_db_value = _get_year_db_value(columns, year_int)

    # Évite les doublons exacts.
    q = db.query(Project)

    if hasattr(Project, "organisme"):
        q = q.filter(Project.organisme == organisme)

    if hasattr(Project, "project_name"):
        q = q.filter(Project.project_name == project_name)

    if hasattr(Project, "year"):
        # Correction principale : si year est VARCHAR, year_db_value = "2025"
        q = q.filter(Project.year == year_db_value)

    q = _filter_owner_if_exists(q, current_user)

    try:
        existing = q.first()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Recherche doublon projet impossible : {exc}",
        )

    if existing:
        return {
            "ok": True,
            "created": False,
            "message": "Projet déjà existant.",
            "project": _project_to_dict(existing),
            **_project_to_dict(existing),
        }

    values: Dict[str, Any] = {}

    # Colonnes principales de ton modèle.
    _set_if_column(values, columns, "consultant_id", current_user.id)
    _set_if_column(values, columns, "user_id", current_user.id)
    _set_if_column(values, columns, "owner_id", current_user.id)
    _set_if_column(values, columns, "created_by_id", current_user.id)

    _set_if_column(values, columns, "organisme", organisme)
    _set_if_column(values, columns, "project_name", project_name)
    _set_if_column(values, columns, "year", year_int)
    _set_if_column(values, columns, "domain_label", domain_label or None)
    _set_if_column(values, columns, "status", "created")

    # Compatibilité avec d'autres noms possibles.
    _set_if_column(values, columns, "client", organisme)
    _set_if_column(values, columns, "client_name", organisme)
    _set_if_column(values, columns, "organization", organisme)
    _set_if_column(values, columns, "organisme_name", organisme)

    _set_if_column(values, columns, "name", project_name)
    _set_if_column(values, columns, "title", project_name)
    _set_if_column(values, columns, "project", project_name)
    _set_if_column(values, columns, "project_title", project_name)

    _set_if_column(values, columns, "domain", domain_label or None)
    _set_if_column(values, columns, "domaine", domain_label or None)

    if "is_active" in columns:
        _set_if_column(values, columns, "is_active", True)

    # ai_folder si ton modèle l'exige ou si tu veux un nom stable.
    if "ai_folder" in columns and not values.get("ai_folder"):
        folder = f"{slugify(organisme)}__{slugify(project_name)}__{year_int}"
        _set_if_column(values, columns, "ai_folder", folder)

    # Remplit les colonnes NOT NULL sans default.
    for name, col in columns.items():
        if name in values or name == "id":
            continue

        has_default = col.default is not None or col.server_default is not None
        if col.nullable or has_default:
            continue

        lname = name.lower()
        py_type = _column_python_type(col)

        if lname in {"consultant_id", "user_id", "owner_id", "created_by_id"}:
            values[name] = _value_for_column(col, current_user.id)
        elif "organisme" in lname or "client" in lname or "organization" in lname:
            values[name] = _value_for_column(col, organisme)
        elif "project" in lname or "projet" in lname or lname in {"name", "title"}:
            values[name] = _value_for_column(col, project_name)
        elif "year" in lname or "annee" in lname:
            values[name] = _value_for_column(col, year_int)
        elif "domain" in lname or "domaine" in lname:
            values[name] = _value_for_column(col, domain_label or "Non défini")
        elif "status" in lname:
            values[name] = _value_for_column(col, "created")
        elif "folder" in lname:
            values[name] = _value_for_column(
                col,
                f"{slugify(organisme)}__{slugify(project_name)}__{year_int}",
            )
        elif py_type is int:
            values[name] = 0
        elif py_type is bool:
            values[name] = True
        elif py_type is float:
            values[name] = 0.0
        else:
            values[name] = ""

    try:
        project = Project(**values)
        db.add(project)
        db.commit()
        db.refresh(project)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Création rapide projet impossible : {exc}",
        )

    # Prépare directement les dossiers mémoire.
    try:
        ensure_memory_dirs(project)
    except Exception:
        pass

    return {
        "ok": True,
        "created": True,
        "message": "Projet créé depuis l'interface Mémoire CIR.",
        "project": _project_to_dict(project),
        **_project_to_dict(project),
    }


# ---------------------------------------------------------------------------
# Routes mémoire CIR projet
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/cir-memory/status")
def get_cir_memory_status(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    return get_project_memory_status(project)


@router.post("/projects/{project_id}/cir-memory/upload-final")
async def upload_final_cir_and_build_memory(
    project_id: int,
    file: UploadFile = File(...),
    rebuild_index: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload un CIR final consultant pour ce projet/année puis construit la mémoire validée.
    Formats V1 : .docx, .pdf, .txt, .md
    """
    project = get_project_for_user(db, project_id, current_user)
    paths = cir_memory_paths(project)

    filename = clean_text(file.filename or "cir_final.docx", 240)
    suffix = Path(filename).suffix.lower()

    if suffix not in {".docx", ".pdf", ".txt", ".md"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format non supporté en V1. Utilise .docx, .pdf, .txt ou .md.",
        )

    safe_name = slugify(Path(filename).stem, default="cir_final") + suffix
    target = paths["cir_final_dir"] / safe_name
    target.parent.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide.")

    target.write_bytes(content)

    result = build_validated_memory_from_cir_final(
        project=project,
        cir_file_path=target,
        rebuild_index=rebuild_index,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)

    return result


@router.post("/projects/{project_id}/cir-memory/build-validated")
def build_validated_cir_memory(
    project_id: int,
    rebuild_index: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Reconstruit la mémoire validée depuis le CIR final déjà présent dans le stockage.
    """
    project = get_project_for_user(db, project_id, current_user)

    result = build_validated_memory_from_cir_final(
        project=project,
        cir_file_path=None,
        rebuild_index=rebuild_index,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)

    return result


@router.post("/projects/{project_id}/cir-memory/build-working/diagnostic")
def build_working_memory_diagnostic(
    project_id: int,
    rebuild_index: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Construit la mémoire de travail depuis le dernier rapport EnnoDiagnostic.
    """
    project = get_project_for_user(db, project_id, current_user)

    result = build_working_memory_from_diagnostic(
        project=project,
        rebuild_index=rebuild_index,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)

    return result


@router.post("/projects/{project_id}/cir-memory/build-working/scholar")
def build_working_memory_scholar(
    project_id: int,
    rebuild_index: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Construit la mémoire de travail depuis EnnoScholar :
    - bibliography_memory.json
    - generated_state_of_art.json
    - working_chunks.json
    """
    project = get_project_for_user(db, project_id, current_user)
    articles = _articles_for_project(db, project.id)

    result = build_working_memory_from_scholar(
        project=project,
        articles_from_db=articles,
        rebuild_index=rebuild_index,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)

    return result


@router.post("/projects/{project_id}/cir-memory/build-all")
def build_all_cir_memory(
    project_id: int,
    include_validated: bool = Query(True),
    include_working: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Build complet V1 :
    - validated depuis CIR final si disponible
    - working depuis EnnoDiagnostic / EnnoScholar
    - rebuild index organisme
    """
    project = get_project_for_user(db, project_id, current_user)
    articles = _articles_for_project(db, project.id)

    return build_all_memory_for_project(
        project=project,
        articles_from_db=articles,
        include_validated=include_validated,
        include_working=include_working,
    )


@router.post("/projects/{project_id}/cir-memory/rebuild-index")
def rebuild_index(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Reconstruit l'index global de l'organisme :
    tous les projets / toutes les années.
    """
    project = get_project_for_user(db, project_id, current_user)
    return rebuild_organism_memory_index(project)


@router.post("/projects/{project_id}/cir-memory/search")
def search_memory(
    project_id: int,
    payload: Dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Recherche dans la mémoire globale de l'organisme du projet courant.

    Exemple payload :
    {
      "query": "tenue au feu REI 60 paroi biosourcée",
      "roles": ["etat_art", "verrou"],
      "memory_statuses": ["validated"],
      "source_types": ["cir_final_consultant"],
      "top_k": 8
    }
    """
    project = get_project_for_user(db, project_id, current_user)

    query = clean_text(payload.get("query"), 1000)
    if not query:
        raise HTTPException(status_code=400, detail="query obligatoire.")

    roles = payload.get("roles")
    if roles is not None and not isinstance(roles, list):
        roles = []

    memory_statuses = payload.get("memory_statuses")
    if memory_statuses is not None and not isinstance(memory_statuses, list):
        memory_statuses = []

    source_types = payload.get("source_types")
    if source_types is not None and not isinstance(source_types, list):
        source_types = []

    try:
        top_k = int(payload.get("top_k") or 8)
    except Exception:
        top_k = 8

    top_k = max(1, min(top_k, 30))

    return search_organism_memory(
        project=project,
        query=query,
        roles=roles,
        memory_statuses=memory_statuses,
        source_types=source_types,
        top_k=top_k,
    )


@router.get("/projects/{project_id}/cir-memory/index")
def get_memory_index(
    project_id: int,
    rebuild: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne un résumé de l'index mémoire de l'organisme.
    """
    project = get_project_for_user(db, project_id, current_user)

    if rebuild:
        rebuild_organism_memory_index(project)

    paths = cir_memory_paths(project)

    def load(path: Path) -> Dict[str, Any]:
        try:
            import json

            if not path.exists():
                return {}

            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    knowledge = load(paths["organism_knowledge_index"])
    style = load(paths["organism_style_index"])
    chunks = load(paths["organism_chunks_index"])
    working = load(paths["organism_working_index"])
    articles = load(paths["organism_articles_index"])
    global_index = load(paths["organism_global_search_index"])

    return {
        "ok": True,
        "project_id": project.id,
        "organisme": project.organisme,
        "organism_dir": str(paths["organism_dir"]),
        "counts": {
            "validated_knowledge_entries": len(knowledge.get("entries") or []),
            "validated_style_examples": len(style.get("examples") or []),
            "validated_chunks": len(chunks.get("chunks") or []),
            "working_chunks": len(working.get("chunks") or []),
            "articles": len(articles.get("items") or []),
            "global_items": len(global_index.get("items") or []),
        },
        "paths": {
            "knowledge_index": str(paths["organism_knowledge_index"]),
            "style_index": str(paths["organism_style_index"]),
            "chunks_index": str(paths["organism_chunks_index"]),
            "working_index": str(paths["organism_working_index"]),
            "articles_index": str(paths["organism_articles_index"]),
            "global_search_index": str(paths["organism_global_search_index"]),
        },
    }


# ---------------------------------------------------------------------------
# Routes Chroma RAG mémoire V2
# ---------------------------------------------------------------------------

@router.post("/projects/{project_id}/cir-memory/store-chroma")
def store_chroma_for_project(
    project_id: int,
    reset_project: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    from services.cir_memory_service import store_project_memory_in_chroma
    return store_project_memory_in_chroma(project, reset_project=reset_project)


@router.post("/projects/{project_id}/cir-memory/rag-search")
def rag_search_memory(
    project_id: int,
    payload: Dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    from services.cir_memory_service import search_organism_memory_chroma

    query = clean_text(payload.get("query"), 1000)
    if not query:
        raise HTTPException(status_code=400, detail="query obligatoire.")

    roles = payload.get("roles")
    if roles is not None and not isinstance(roles, list):
        roles = []

    memory_statuses = payload.get("memory_statuses")
    if memory_statuses is not None and not isinstance(memory_statuses, list):
        memory_statuses = []

    source_types = payload.get("source_types")
    if source_types is not None and not isinstance(source_types, list):
        source_types = []

    try:
        top_k = int(payload.get("top_k") or 8)
    except Exception:
        top_k = 8
    top_k = max(1, min(top_k, 30))

    return search_organism_memory_chroma(
        project=project,
        query=query,
        roles=roles,
        memory_statuses=memory_statuses,
        source_types=source_types,
        top_k=top_k,
    )
