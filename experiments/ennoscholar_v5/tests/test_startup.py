"""Offline regression of local startup: existing SQLite, local-only key, V5-only DDL."""
import hashlib
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, inspect
from sqlalchemy.exc import OperationalError
from agents.EnnoScholarV5.startup import configure_local_v4, create_local_v5_schema


@pytest.fixture
def local_install(tmp_path):
    source = tmp_path/"local-v4"
    source.mkdir()
    database = source/"existing.sqlite3"
    with closing(sqlite3.connect(database)) as db:
        for name in ("users", "projects", "scholar_v4_sessions", "scholar_v4_runs"):
            db.execute(f"CREATE TABLE {name}(id TEXT PRIMARY KEY, data TEXT)")
            db.execute(f"INSERT INTO {name} VALUES(?, ?)", ("kept", "Existing scientific data"))
        db.commit()
    shared, local = tmp_path/"shared-profile.txt", tmp_path/"local-openai.txt"
    shared.write_text("offline fixture")
    local.write_text("offline fixture")
    values = {shared: {"DATABASE_URL": "postgresql://unused@127.0.0.1:5432/unused", "OPENAI_API_KEY": "shared-must-not-be-used",
        "SECRET_KEY": "fixture-auth", "ENNOSCHOLAR_V4_MAX_COST_USD": "100", "ENNOSMART_DATA_ROOT": "/srv/deployment"},
        local: {"OPENAI_API_KEY": "local-fixture-only", "ENNOSCHOLAR_V4_MODEL": "gpt-6.1-sol"}}
    return tmp_path, source, shared, local, database, values


def configure(installation, environ):
    root, source, shared, local, database, values = installation
    return configure_local_v4(root/"v5", shared, database, local, environ=environ, read_env=lambda p: values[p])


def test_local_profile_matches_existing_v4_database_not_shared_postgres(local_install):
    root, source, shared, local, database, values = local_install
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    env = {"DATABASE_URL": "postgresql://inherited@127.0.0.1:5432/unused", "OPENAI_API_KEY": "inherited-wrong",
        "ENNOSCHOLAR_V4_MAX_COST_USD": "100", "UNRELATED": "preserved"}
    info = configure(local_install, env)
    assert env["DATABASE_URL"] == "sqlite:///"+database.resolve().as_posix()
    assert env["OPENAI_API_KEY"] == "local-fixture-only"
    assert env["ENNOSCHOLAR_V4_MODEL"] == "gpt-6.1-sol"
    assert "ENNOSCHOLAR_V4_MAX_COST_USD" not in env  # Same default ceilings as the local V4 launcher.
    assert env["SECRET_KEY"] == "fixture-auth" and env["UNRELATED"] == "preserved"
    assert env["ENNOSMART_DATA_ROOT"] == str(source/".codex_tmp/phase1_manual_runtime/data")
    assert env["ENNOSCHOLAR_V4_PDF_DIRECTORY"] == str(source/"backend_api/storage/scholar_v4/pdf")
    assert env["ENNOSCHOLAR_V5_LOCAL_V4_DATABASE"] == "1"
    assert "local-fixture-only" not in json.dumps(info) and "shared-must-not-be-used" not in json.dumps(info)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("problem", ["missing_database", "empty_database", "wrong_database", "missing_local_profile", "missing_key", "wrong_model"])
def test_bad_local_setup_is_rejected_without_fallback_or_environment_mutation(local_install, problem):
    root, source, shared, local, database, values = local_install
    if problem == "missing_database":
        database.unlink()
    elif problem == "empty_database":
        database.write_bytes(b"")
    elif problem == "wrong_database":
        database.write_bytes(b"not a database")
    elif problem == "missing_local_profile":
        local.unlink()
    elif problem == "missing_key":
        values[local].pop("OPENAI_API_KEY")
    else:
        values[local]["ENNOSCHOLAR_V4_MODEL"] = "another-model"
    env = {"DATABASE_URL": "old", "OPENAI_API_KEY": "inherited"}
    with pytest.raises(ValueError):
        configure(local_install, env)
    assert env == {"DATABASE_URL": "old", "OPENAI_API_KEY": "inherited"}
    if problem == "missing_database":
        assert not database.exists()


def test_local_startup_creates_only_v5_tables_and_preserves_existing_schema_and_rows(local_install):
    root, source, shared, local, database, values = local_install
    engine = create_engine("sqlite:///"+database.as_posix())
    md = MetaData()
    Table("users", md, Column("id", Integer, primary_key=True), Column("unwanted_new_column", String))
    Table("unrelated_missing_table", md, Column("id", Integer, primary_key=True))
    Table("scholar_v5_sessions", md, Column("id", Integer, primary_key=True))
    with closing(sqlite3.connect(database)) as db:
        original = db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        rows = {name: db.execute(f"SELECT * FROM {name}").fetchall() for name, _ in original}
    create_local_v5_schema(engine, md)
    assert "scholar_v5_sessions" in inspect(engine).get_table_names()
    assert "unrelated_missing_table" not in inspect(engine).get_table_names()
    with closing(sqlite3.connect(database)) as db:
        for name, sql in original:
            assert db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0] == sql
            assert db.execute(f"SELECT * FROM {name}").fetchall() == rows[name]
    engine.dispose()


def test_sqlite_connection_cannot_recreate_database_if_removed_after_configuration(local_install):
    root, source, shared, local, database, values = local_install
    engine = create_engine("sqlite:///"+database.as_posix())
    create_local_v5_schema(engine, MetaData())
    engine.dispose()
    database.unlink()
    with pytest.raises(OperationalError):
        with engine.connect():
            pass
    assert not database.exists()


def test_check_config_never_imports_or_starts_application(local_install, monkeypatch, capsys):
    import agents.EnnoScholarV5.startup as startup
    path = Path(__file__).resolve().parents[3]/"scripts/start_ennoscholar_v5.py"
    spec = importlib.util.spec_from_file_location("v5_launcher_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    main = module.main
    info = {"profile": "local-v4", "model": "gpt-6.1-sol", "openai_key_source": "local_profile_only"}
    stub = Mock(return_value=info)
    monkeypatch.setattr(startup, "configure_local_v4", stub)
    import builtins
    real_import = builtins.__import__
    def no_server(name, *args, **kwargs):
        if name in {"uvicorn", "main", "agents.EnnoScholarV4.service"}:
            raise AssertionError("Application must not be started by a configuration check")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_server)
    assert main(["--check-config"]) == 0
    assert stub.call_count == 1
    assert json.loads(capsys.readouterr().out) == info
