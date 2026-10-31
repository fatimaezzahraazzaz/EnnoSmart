"""Local V4 installation settings, without executing its launcher or starting work."""
from __future__ import annotations
import os
from contextlib import closing
from pathlib import Path
import sqlite3

LOCAL_V4_ROOT = Path(r"C:\Users\hp\.codex\worktrees\ennoscholar-v4\EnnoSmart")
LOCAL_V4_OPENAI = Path(r"C:\Users\hp\.codex\runtime\ennoscholar-v4\openai.local.env")
SHARED_KEYS = {"OPENAI_BASE_URL", "SEMANTIC_SCHOLAR_API_KEY", "OPENALEX_API_KEY", "SECRET_KEY",
    "ALGORITHM", "ACCESS_TOKEN_EXPIRE_MINUTES", "REFRESH_TOKEN_EXPIRE_DAYS"}
MISSION_KEYS = {"ENNOSCHOLAR_V5_MISSION_BUDGET_USD", "ENNOSCHOLAR_V5_MISSION_MAX_TOKENS",
    "ENNOSCHOLAR_V5_MISSION_MAX_SECONDS", 'ENNOSCHOLAR_V5_MISSION_MAX_TOOLS',
    'ENNOSCHOLAR_V5_MISSION_MAX_WEB', 'ENNOSCHOLAR_V5_SESSION_BUDGET_USD'}
# Per-run ceilings a test operator may LOWER from the launching shell (never from a
# profile file, never above the V4 defaults); every other ENNOSCHOLAR_V4_* is removed.
RUN_CAP_FIELDS = {"ENNOSCHOLAR_V4_MAX_TOKENS": "max_tokens", "ENNOSCHOLAR_V4_MAX_COST_USD": "max_cost_usd",
    "ENNOSCHOLAR_V4_MAX_TOOL_CALLS": "max_tool_calls", "ENNOSCHOLAR_V4_TIMEOUT_SECONDS": "timeout_seconds",
    "ENNOSCHOLAR_V4_MAX_WEB_CALLS": "max_web_calls", "ENNOSCHOLAR_V4_WEB_SEARCH_ENABLED": "web_search_enabled"}


def lowered_run_caps(environ, ignored=None):
    """Per-run caps from the shell, kept only when lower or equal to the V4 defaults.

    Any other value (raised, zero, not a number, web search enabled) is ignored, as
    the launcher always did with ENNOSCHOLAR_V4_*, and reported in `ignored`."""
    import math
    from agents.EnnoScholarV4.config import ScholarV4Config
    defaults, caps = ScholarV4Config(), {}
    ignored = {} if ignored is None else ignored
    for key, field in RUN_CAP_FIELDS.items():
        raw = environ.get(key)
        if raw is None or raw == "":
            continue
        default = getattr(defaults, field)
        if isinstance(default, bool):
            if raw.strip().lower() not in {"false", "0", "no"}:
                ignored[field] = "seule la désactivation (false) est transmise"
                continue
            caps[key] = "false"
            continue
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(key+' doit être un plafond positif fini.') from None
        integer = isinstance(default, int)
        # V4 requires every ceiling to be positive (web search is turned off by its own flag).
        if not math.isfinite(value) or (integer and not value.is_integer()) or value <= 0:
            raise ValueError(key+' doit être un plafond positif fini, entier si nécessaire.')
        if value > default:
            ignored[field] = f"hors plafond abaissé (0 < valeur <= {default})"
            continue
        caps[key] = str(int(value)) if integer else str(value)
    if caps:
        # The lowered ceilings must form a configuration V4 accepts, checked before any
        # environment change (a refused one would only fail at the first request).
        from dataclasses import replace
        from agents.EnnoScholarV4.config import ConfigurationError
        typed = {RUN_CAP_FIELDS[k]: (False if RUN_CAP_FIELDS[k] == "web_search_enabled" else type(getattr(defaults, RUN_CAP_FIELDS[k]))(
            float(v) if isinstance(getattr(defaults, RUN_CAP_FIELDS[k]), float) else int(v))) for k, v in caps.items()}
        try:
            replace(defaults, api_key="validation-only", **typed).validate()
        except ConfigurationError as exc:
            raise ValueError("Plafonds de test refusés par la configuration V4 : " + str(exc)) from None
    return caps


def verify_existing_database(path):
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Base locale V4 existante requise : " + str(path))
    try:
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError:
        raise ValueError("Le fichier indiqué n'est pas une base SQLite V4 lisible.") from None
    if not {"users", "projects", "scholar_v4_sessions", "scholar_v4_runs"} <= tables:
        raise ValueError("La base indiquée ne contient pas les tables de l'installation locale V4.")
    return path


def configure_local_v4(root, shared_profile, database, openai_profile, *, environ=None, read_env=None):
    """Match the existing local launcher: shared auth/provider fields, local key only.

    Validate everything before changing the process environment. No configuration
    file, source database, scientific record or resource ceiling is written.
    """
    from agents.EnnoScholarV4.model_policy import MODEL
    if read_env is None:
        from dotenv import dotenv_values
        read_env = dotenv_values
    environ = os.environ if environ is None else environ
    shared_profile, openai_profile = Path(shared_profile), Path(openai_profile)
    if not shared_profile.is_file():
        raise ValueError("Profil général introuvable : " + str(shared_profile))
    if not openai_profile.is_file():
        raise ValueError("Profil OpenAI local V4 introuvable : " + str(openai_profile))
    database = verify_existing_database(database)
    shared, local = read_env(shared_profile), read_env(openai_profile)
    if not local.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY absent du profil OpenAI local V4 ; aucun repli sur le profil partagé.")
    if local.get("ENNOSCHOLAR_V4_MODEL") != MODEL:
        raise ValueError("Le profil OpenAI local doit conserver " + MODEL + ".")
    source_root = database.parent
    runtime = source_root / ".codex_tmp/phase1_manual_runtime"
    selected = {key: value for key, value in shared.items() if key in SHARED_KEYS and value}
    # Shell authorisation takes precedence, as in load_dotenv(override=False).
    # Only the documented mission caps cross the local profile boundary.
    for key in MISSION_KEYS:
        if key not in environ and shared.get(key):
            selected[key] = shared[key]
    ignored_caps = {}
    caps = lowered_run_caps(environ, ignored_caps)
    from .config import positive, ConfigurationError
    for key in MISSION_KEYS:
        raw = environ.get(key, selected.get(key, ''))
        if raw:
            try:
                value = positive(raw,key,integer=key.endswith(('MAX_TOKENS','MAX_TOOLS','MAX_WEB')))
            except ConfigurationError as exc:
                raise ValueError(str(exc)) from None
            ceiling = 48 if key.endswith('MAX_TOOLS') else 4 if key.endswith('MAX_WEB') else None
            if ceiling and value>ceiling:
                raise ValueError(key+' dépasse le plafond existant.')
            # Integer caps are normalised ('2000000.0' -> '2000000'); a validated
            # dollar cap keeps the consultant's own spelling.
            selected[key] = str(value) if isinstance(value, int) else str(raw).strip()
    selected.update(OPENAI_API_KEY=local["OPENAI_API_KEY"], ENNOSCHOLAR_V4_MODEL=MODEL,
        DATABASE_URL="sqlite:///"+database.as_posix(),
        ENNOSMART_ROOT=str(Path(root).resolve()), ENNOSMART_BASE_DIR=str(Path(root).resolve()),
        ENNOSMART_DATA_ROOT=str(runtime/"data"), ENNOSMART_STORAGE_ROOT=str(runtime/"storage"),
        ENNOSCHOLAR_V4_PDF_DIRECTORY=str(source_root/"backend_api/storage/scholar_v4/pdf"),
        ENNOSCHOLAR_V5_TEST_ENABLED="1", ENNOSCHOLAR_V5_LOCAL_V4_DATABASE="1",
        FRONTEND_URL="http://127.0.0.1:3000", CORS_ORIGINS="http://127.0.0.1:3000,http://localhost:3000",
        PYTHONDONTWRITEBYTECODE="1")
    for key in list(environ):
        if key == "OPENAI_API_KEY" or key.startswith("ENNOSCHOLAR_V4_"):
            del environ[key]
    environ.update(selected)
    environ.update(caps)
    return {"profile": "local-v4", "database": str(database), "model": MODEL,
        "openai_profile": str(openai_profile.resolve()), "openai_key_source": "local_profile_only",
        "shared_profile": str(shared_profile.resolve()), "startup_schema": "V5 tables only",
        "mission_enabled": bool(environ.get('ENNOSCHOLAR_V5_MISSION_BUDGET_USD')),
        "mission_budget_usd": float(environ['ENNOSCHOLAR_V5_MISSION_BUDGET_USD'])
            if environ.get('ENNOSCHOLAR_V5_MISSION_BUDGET_USD') else None,
        "run_caps": {RUN_CAP_FIELDS[k]: v for k, v in sorted(caps.items())}, "ignored_run_caps": ignored_caps}


def create_local_v5_schema(engine, metadata):
    """Only add V5 tables to the existing local DB; no shared/V4 migrations.

    mode=rw also prevents SQLite silently recreating the database if it is moved
    after the initial configuration check. This applies only to this V5 engine.
    """
    from sqlalchemy import event
    if engine.dialect.name != "sqlite":
        raise ValueError("Le profil local-v4 exige la base SQLite locale existante.")
    path = Path(engine.url.database).resolve()
    if not path.is_file():
        raise ValueError("Base locale déplacée ou absente : " + str(path))

    def existing_database_only(dialect, connection_record, cargs, cparams):
        cargs[0] = path.as_uri()+"?mode=rw"
        cparams["uri"] = True

    event.listen(engine, "do_connect", existing_database_only)
    engine.dispose()
    metadata.create_all(bind=engine, tables=[table for table in metadata.tables.values()
        if table.name.startswith("scholar_v5_")])
