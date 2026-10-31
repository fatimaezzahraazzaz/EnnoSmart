"""Official savers, same graph. Production setup is explicit, never automatic."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


@contextmanager
def sqlite_checkpoints(path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    with sqlite3.connect(str(Path(path)), check_same_thread=False) as connection:
        yield SqliteSaver(connection, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))


@contextmanager
def postgres_checkpoints(dsn, *, schema="ennoscholar_v5_experiment", setup=False):
    """An administrator supplies an isolated database/schema and credentials.

    Does not read .env or create a production schema. setup=True is an explicit
    migration on the supplied disposable/experimental database, never V4 tables.
    The official saver, not the prototype, owns checkpoint serialisation/tables.
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    if not schema.startswith("ennoscholar_v5_") or not schema.replace("_", "").isalnum():
        raise ValueError("An isolated V5 schema is required")
    with psycopg.connect(dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row) as connection:
        if setup:
            connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        saver = PostgresSaver(connection, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
        if setup:
            saver.setup()
        yield saver
