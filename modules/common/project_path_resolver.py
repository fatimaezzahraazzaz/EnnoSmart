# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, List


def strip_accents(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c))


def canonical_key(value: Any) -> str:
    s = strip_accents(value).lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def slug_underscore(value: Any) -> str:
    s = strip_accents(value).lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_") or "default"


def slug_hyphen(value: Any) -> str:
    s = strip_accents(value).lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or "default"


def candidate_names(value: Any) -> List[str]:
    raw = str(value or "").strip()
    vals = [
        raw,
        raw.lower(),
        slug_underscore(raw),
        slug_hyphen(raw),
        slug_underscore(raw.replace("-", "_")),
        slug_hyphen(raw.replace("_", "-")),
        str(raw).upper(),
    ]
    out: List[str] = []
    seen = set()
    for v in vals:
        v = str(v or "").strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _data_score(path: Path) -> int:
    if not path.exists() or not path.is_dir():
        return -1

    score = 0

    if (path / "years").exists():
        score += 300
        try:
            for y in (path / "years").iterdir():
                if not y.is_dir():
                    continue
                if (y / "nlp" / "nlp_result.json").exists():
                    score += 100
                if (y / "rag" / "chunks.json").exists():
                    score += 100
                if (y / "documents" / "raw").exists():
                    score += 50
                if (y / "documents" / "processed").exists():
                    score += 30
        except Exception:
            pass

    if (path / "nlp" / "nlp_result.json").exists():
        score += 150
    if (path / "rag" / "chunks.json").exists():
        score += 150
    if (path / "documents" / "raw").exists():
        score += 60
    if (path / "documents" / "processed").exists():
        score += 40

    return score


def resolve_child_dir(parent: Path, wanted: Any, create_if_missing: bool = False) -> Path:
    parent = Path(parent)
    wanted_key = canonical_key(wanted)
    candidates: List[Path] = []

    for name in candidate_names(wanted):
        p = parent / name
        if p.exists() and p.is_dir():
            candidates.append(p)

    if parent.exists():
        for child in parent.iterdir():
            if child.is_dir() and canonical_key(child.name) == wanted_key:
                candidates.append(child)

    if candidates:
        unique: List[Path] = []
        seen = set()
        for p in candidates:
            key = str(p.resolve()).lower()
            if key not in seen:
                seen.add(key)
                unique.append(p)

        unique.sort(
            key=lambda p: (
                _data_score(p),
                1 if p.name == slug_underscore(wanted) else 0,
                1 if p.name.lower() == str(wanted).lower() else 0,
            ),
            reverse=True,
        )
        return unique[0]

    fallback = parent / slug_underscore(wanted)
    if create_if_missing:
        fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def resolve_organisme_dir(base_dir: Path, organisme: Any, create_if_missing: bool = False) -> Path:
    return resolve_child_dir(Path(base_dir) / "storage" / "organismes", organisme, create_if_missing)


def resolve_project_root(base_dir: Path, organisme: Any, project: Any, create_if_missing: bool = False) -> Path:
    org_dir = resolve_organisme_dir(base_dir, organisme, create_if_missing)
    return resolve_child_dir(org_dir / "projects", project, create_if_missing)


def resolve_year_root(base_dir: Path, organisme: Any, project: Any, year: Any, create_if_missing: bool = False) -> Path:
    project_root = resolve_project_root(base_dir, organisme, project, create_if_missing)
    p = project_root / "years" / str(year)
    if create_if_missing:
        p.mkdir(parents=True, exist_ok=True)
    return p
