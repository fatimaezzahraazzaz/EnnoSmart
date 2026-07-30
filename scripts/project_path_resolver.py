# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List


def strip_accents(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c))


def canonical_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", strip_accents(value).lower())


def slug_underscore(value: Any) -> str:
    s = strip_accents(value).lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_") or "default"


def slug_hyphen(value: Any) -> str:
    s = strip_accents(value).lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or "default"


def slug_keep(value: Any) -> str:
    s = strip_accents(value).lower().strip()
    s = re.sub(r"[^a-z0-9_\-]+", "_", s)
    s = re.sub(r"_+", "_", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("_-") or "default"


def candidate_names(value: Any) -> List[str]:
    raw = str(value or "").strip()
    values = [
        raw,
        raw.lower(),
        slug_keep(raw),
        slug_underscore(raw),
        slug_hyphen(raw),
        slug_underscore(raw.replace("-", "_")),
        slug_hyphen(raw.replace("_", "-")),
    ]
    out, seen = [], set()
    for v in values:
        v = str(v or "").strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def resolve_child_dir(parent: Path, wanted: Any, create_if_missing: bool = False) -> Path:
    parent = Path(parent)
    wanted_key = canonical_key(wanted)
    for name in candidate_names(wanted):
        p = parent / name
        if p.exists() and p.is_dir():
            return p
    if parent.exists():
        for child in parent.iterdir():
            if child.is_dir() and canonical_key(child.name) == wanted_key:
                return child
    p = parent / slug_underscore(wanted)
    if create_if_missing:
        p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_organisme_dir(base_dir: Path, organisme: Any, create_if_missing: bool = False) -> Path:
    return resolve_child_dir(Path(base_dir) / "storage" / "organismes", organisme, create_if_missing)


def resolve_project_root(base_dir: Path, organisme: Any, project: Any, create_if_missing: bool = False) -> Path:
    org_dir = resolve_organisme_dir(base_dir, organisme, create_if_missing)
    return resolve_child_dir(org_dir / "projects", project, create_if_missing)


def resolve_year_root(base_dir: Path, organisme: Any, project: Any, year: Any, create_if_missing: bool = False) -> Path:
    p = resolve_project_root(base_dir, organisme, project, create_if_missing) / "years" / str(year)
    if create_if_missing:
        p.mkdir(parents=True, exist_ok=True)
    return p


def project_identity(base_dir: Path, organisme: Any, project: Any, year: Any = "") -> Dict[str, Any]:
    project_root = resolve_project_root(base_dir, organisme, project, False)
    year_root = project_root / "years" / str(year) if year else None
    return {
        "organisme_input": organisme,
        "project_input": project,
        "year_input": year,
        "project_slug": project_root.name,
        "project_root": str(project_root),
        "year_root": str(year_root) if year_root else "",
        "project_root_exists": project_root.exists(),
        "year_root_exists": year_root.exists() if year_root else False,
    }
