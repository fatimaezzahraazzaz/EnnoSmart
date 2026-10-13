# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
from typing import Any, Dict, Optional

from modules.RAG.project_store import ProjectStore


def reset_project_storage(
    organisme: str,
    project: str,
    year: Optional[str | int] = None,
    annee: Optional[str | int] = None,
    recreate: bool = True,
) -> Dict[str, Any]:
    ps = ProjectStore(organisme, project, year=year, annee=annee)
    project_dir = ps.project_dir

    existed = project_dir.exists()

    if existed:
        shutil.rmtree(project_dir, ignore_errors=True)

    if recreate:
        ps.ensure()

    return {
        "organisme_id": ps.organisme_id,
        "project_id": ps.project_id,
        "year": ps.year,
        "annee": ps.year,
        "project_dir": str(project_dir),
        "existed_before": existed,
        "reset_done": True,
        "recreated": recreate,
    }


def clean_project_generated_outputs(
    organisme: str,
    project: str,
    year: Optional[str | int] = None,
    annee: Optional[str | int] = None,
    keep_raw_documents: bool = True,
) -> Dict[str, Any]:
    ps = ProjectStore(organisme, project, year=year, annee=annee).ensure()

    targets = [
        ps.documents_processed_dir,
        ps.nlp_dir,
        ps.rag_dir,
        ps.diagnostics_dir,
    ]

    if not keep_raw_documents:
        targets.append(ps.documents_raw_dir)

    removed = []

    for p in targets:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(str(p))

    ps.ensure()

    return {
        "organisme_id": ps.organisme_id,
        "project_id": ps.project_id,
        "year": ps.year,
        "annee": ps.year,
        "project_dir": str(ps.project_dir),
        "removed": removed,
        "keep_raw_documents": keep_raw_documents,
        "reset_done": True,
    }
