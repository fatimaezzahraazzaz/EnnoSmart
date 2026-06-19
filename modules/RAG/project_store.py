# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .config import ORGANISMES_DIR


def slugify(value: str, default: str = "unknown") -> str:
    value = str(value or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    value = value.translate(tr)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:80] or default


def normalize_year(year: Optional[str | int] = None) -> str:
    if year is None or str(year).strip() == "":
        return str(datetime.now().year)

    raw = str(year).strip()
    m = re.search(r"(20\d{2}|19\d{2})", raw)
    if m:
        return m.group(1)

    return slugify(raw, default=str(datetime.now().year))


class ProjectStore:
    """
    Stockage :
    storage/organismes/{organisme_id}/projects/{project_id}/years/{year}/
        documents/raw
        documents/processed
        nlp/nlp_result.json
        rag/chunks.json
        rag/chroma
        diagnostics
        metadata.json
    """

    def __init__(
        self,
        organisme: str,
        project: str,
        year: Optional[str | int] = None,
        annee: Optional[str | int] = None,
        **kwargs: Any,
    ):
        self.organisme_name = organisme
        self.project_name = project
        self.year = normalize_year(year if year is not None else annee)
        self.annee = self.year

        self.organisme_id = slugify(organisme, default="organisme")
        self.project_id = slugify(project, default="projet")
        self.year_id = slugify(self.year, default=str(datetime.now().year))

        self.organisme_dir = ORGANISMES_DIR / self.organisme_id
        self.project_root_dir = self.organisme_dir / "projects" / self.project_id
        self.project_dir = self.project_root_dir / "years" / self.year_id

        self.documents_raw_dir = self.project_dir / "documents" / "raw"
        self.documents_processed_dir = self.project_dir / "documents" / "processed"
        self.nlp_dir = self.project_dir / "nlp"
        self.rag_dir = self.project_dir / "rag"
        self.chroma_dir = self.rag_dir / "chroma"
        self.diagnostics_dir = self.project_dir / "diagnostics"
        self.metadata_path = self.project_dir / "metadata.json"

    def ensure(self) -> "ProjectStore":
        for p in [
            self.documents_raw_dir,
            self.documents_processed_dir,
            self.nlp_dir,
            self.rag_dir,
            self.chroma_dir,
            self.diagnostics_dir,
        ]:
            p.mkdir(parents=True, exist_ok=True)

        if not self.metadata_path.exists():
            self.write_metadata({})
        return self

    def write_metadata(self, extra: Optional[Dict[str, Any]] = None) -> Path:
        self.project_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "organisme_name": self.organisme_name,
            "organisme_id": self.organisme_id,
            "project_name": self.project_name,
            "project_id": self.project_id,
            "year": self.year,
            "annee": self.year,
            "year_id": self.year_id,
            "project_root_dir": str(self.project_root_dir),
            "project_year_dir": str(self.project_dir),
            "created_or_updated_at": datetime.now().isoformat(timespec="seconds"),
        }

        if extra:
            data.update(extra)

        self.metadata_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.metadata_path

    def save_json(self, relative_path: str, data: Dict[str, Any]) -> Path:
        path = self.project_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read_json(self, relative_path: str) -> Dict[str, Any]:
        path = self.project_dir / relative_path
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
