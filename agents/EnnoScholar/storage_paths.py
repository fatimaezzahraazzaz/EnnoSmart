# -*- coding: utf-8 -*-
from __future__ import annotations

"""Résolution unique des chemins EnnoScholar."""

import os
import re
import unicodedata
from pathlib import Path

from modules.common.runtime_paths import code_root, storage_root as runtime_storage_root


def slug(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_") or "unknown"


def root_dir() -> Path:
    configured = os.getenv("ENNOSMART_ROOT_DIR")
    return Path(configured) if configured else code_root()


def storage_root() -> Path:
    return runtime_storage_root()


def project_root(organisme: str, project: str, year: str) -> Path:
    return (
        storage_root()
        / "organismes"
        / slug(organisme)
        / "projects"
        / slug(project)
        / "years"
        / str(year)
    )


def state_of_art_root(organisme: str, project: str, year: str) -> Path:
    return project_root(organisme, project, year) / "ennoscholar" / "state_of_art_payload"


def confirmed_verrous_path(organisme: str, project: str, year: str) -> Path:
    override = os.getenv("ENNOSCHOLAR_CONFIRMED_VERROUS_PATH")
    if override:
        return Path(override)
    return project_root(organisme, project, year) / "ennodiagnostic" / "confirmed_verrous.json"


def consultant_plan_path(organisme: str, project: str, year: str) -> Path:
    override = os.getenv("ENNOSCHOLAR_CONSULTANT_PLAN_PATH")
    if override:
        return Path(override)
    return state_of_art_root(organisme, project, year) / "consultant_plan_contract.json"


def guided_sources_path(organisme: str, project: str, year: str) -> Path:
    override = os.getenv("ENNOSCHOLAR_GUIDED_SOURCES_PATH")
    if override:
        return Path(override)
    return state_of_art_root(organisme, project, year) / "guided_research_sources.json"
