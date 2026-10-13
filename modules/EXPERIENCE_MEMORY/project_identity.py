# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Project identity resolver for EnnoSmart.

Objectif :
- Ne PAS imposer un seul nom canonique affiché.
- Utiliser le nom du projet tel qu'il existe dans la base/mémoire V2.
- Mais comparer intelligemment AI-Radar / AI_RADAR / AI RADAR / ai_radar.

Exemple :
Si la V2 contient "Scalian::AI_Radar::2025", et que l'interface envoie "AI-RADAR",
le resolver retourne "AI_Radar" pour chercher dans la mémoire.
"""

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.common.runtime_paths import code_root, experience_memory_root


ROOT_DIR = code_root()
V2_ROOT = experience_memory_root()
CATALOG_V2 = V2_ROOT / "catalog_v2.json"


def normalize_key(value: Any) -> str:
    """
    Clé de comparaison seulement.
    Ne doit pas remplacer le nom affiché/base.
    """
    s = str(value or "").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "_").replace("'", "_")
    s = s.replace("-", "_")
    s = re.sub(r"[^A-Za-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s or "unknown"


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _project_tokens_from_catalog(catalog: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Retourne les projets tels qu'ils existent dans la V2.
    Format attendu dans catalog.projects :
      "Scalian::AI_Radar::2025"
    """
    out: List[Dict[str, str]] = []

    projects = catalog.get("projects") or []
    if not isinstance(projects, list):
        return out

    for item in projects:
        raw = str(item or "").strip()
        parts = raw.split("::")

        if len(parts) == 3:
            organisme, project, year = parts
        else:
            organisme, project, year = "", raw, ""

        if project:
            out.append(
                {
                    "organisme": organisme,
                    "project": project,        # nom exact dans la base
                    "year": str(year or ""),
                    "raw": raw,
                    "organisme_key": normalize_key(organisme),
                    "project_key": normalize_key(project),
                }
            )

    return out


def resolve_project_name_from_v2(
    organisme: Any,
    project: Any,
    year: Any = "",
    catalog_path: Path = CATALOG_V2,
) -> str:
    """
    Retourne le nom du projet tel qu'il est stocké dans la base V2.

    Si aucun match n'est trouvé, retourne le nom donné en entrée.
    """
    input_org_key = normalize_key(organisme)
    input_project_key = normalize_key(project)
    input_year = str(year or "").strip()

    catalog = _read_json(catalog_path, {}) or {}
    tokens = _project_tokens_from_catalog(catalog)

    if not tokens:
        return str(project or "").strip()

    # 1) Match exact organisme + projet + année
    for t in tokens:
        if (
            t["organisme_key"] == input_org_key
            and t["project_key"] == input_project_key
            and (not input_year or t["year"] == input_year)
        ):
            return t["project"]

    # 2) Match exact projet + année
    for t in tokens:
        if t["project_key"] == input_project_key and (not input_year or t["year"] == input_year):
            return t["project"]

    # 3) Match exact organisme + projet, peu importe année
    for t in tokens:
        if t["organisme_key"] == input_org_key and t["project_key"] == input_project_key:
            return t["project"]

    # 4) Match projet seulement, peu importe organisme/année
    for t in tokens:
        if t["project_key"] == input_project_key:
            return t["project"]

    return str(project or "").strip()


def resolve_organisme_name_from_v2(
    organisme: Any,
    catalog_path: Path = CATALOG_V2,
) -> str:
    """
    Retourne le nom organisme tel qu'il existe dans la V2.
    """
    input_org_key = normalize_key(organisme)
    catalog = _read_json(catalog_path, {}) or {}
    tokens = _project_tokens_from_catalog(catalog)

    for t in tokens:
        if t["organisme_key"] == input_org_key and t["organisme"]:
            return t["organisme"]

    organisms = catalog.get("organisms") or []
    if isinstance(organisms, list):
        for org in organisms:
            if normalize_key(org) == input_org_key:
                return str(org)

    return str(organisme or "").strip()


def resolve_identity_from_v2(
    organisme: Any,
    project: Any,
    year: Any = "",
    catalog_path: Path = CATALOG_V2,
) -> Dict[str, str]:
    """
    Retourne les noms exacts V2 + clés de comparaison.
    """
    org_base = resolve_organisme_name_from_v2(organisme, catalog_path=catalog_path)
    project_base = resolve_project_name_from_v2(
        organisme=org_base or organisme,
        project=project,
        year=year,
        catalog_path=catalog_path,
    )

    return {
        "organisme_input": str(organisme or "").strip(),
        "project_input": str(project or "").strip(),
        "year": str(year or "").strip(),
        "organisme_base": org_base,
        "project_base": project_base,
        "organisme_key": normalize_key(org_base or organisme),
        "project_key": normalize_key(project_base or project),
    }


if __name__ == "__main__":
    tests = ["AI-RADAR", "AI_RADAR", "AI Radar", "ai_radar", "Ai-Radar"]
    for t in tests:
        print(t, "=>", resolve_project_name_from_v2("Scalian", t, "2025"))
