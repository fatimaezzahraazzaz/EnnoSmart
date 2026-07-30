# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import Any

from api.routes.guided_research import build_guided_research_router
from services.guided_research_service import ensure_guided_research_tables


def _find_dependency(attribute: str):
    candidates = (
        "api.deps",
        "api.dependencies",
        "dependencies",
        "core.dependencies",
        "core.deps",
        "auth.dependencies",
    )
    errors: list[str] = []
    for module_name in candidates:
        try:
            module = importlib.import_module(module_name)
            value = getattr(module, attribute, None)
            if value is not None:
                return value
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")
    raise ImportError(
        f"Dépendance {attribute} introuvable. Vérifie guided_research_bootstrap.py. "
        + " | ".join(errors)
    )


def _find_engine() -> Any:
    for module_name in ("db.database", "database", "core.database"):
        try:
            module = importlib.import_module(module_name)
            engine = getattr(module, "engine", None)
            if engine is not None:
                return engine
        except Exception:
            continue
    return None


def register_guided_research(app: Any, *, prefix: str = "/api") -> None:
    """Enregistre le chat et crée ses deux tables si nécessaire."""
    get_db = _find_dependency("get_db")
    get_current_user = _find_dependency("get_current_user")
    engine = _find_engine()
    if engine is not None:
        ensure_guided_research_tables(engine)
    router = build_guided_research_router(
        get_db_dependency=get_db,
        get_current_user_dependency=get_current_user,
    )
    app.include_router(router, prefix=prefix)
