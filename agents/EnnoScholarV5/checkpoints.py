"""Official savers on the application's database, isolated from V4 data."""
from contextlib import contextmanager
from pathlib import Path
import os
from experiments.ennoscholar_v5.checkpoints import sqlite_checkpoints, postgres_checkpoints

@contextmanager
def application_checkpoints(engine):
    if engine.dialect.name == "postgresql":
        dsn = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        with postgres_checkpoints(dsn, schema="ennoscholar_v5_langgraph", setup=True) as saver:
            yield saver
    elif engine.dialect.name == "sqlite":
        # Scientific state uses the application DB; SQLite checkpoint history has
        # its own file, avoiding collisions with any other LangGraph application.
        root = Path(os.getenv("ENNOSCHOLAR_V5_CHECKPOINT_DIRECTORY", str(Path(__file__).resolve().parents[2] / "backend_api/storage/scholar_v5/checkpoints")))
        root.mkdir(parents=True, exist_ok=True)
        with sqlite_checkpoints(root / "langgraph.sqlite3") as saver:
            yield saver
    else:
        raise ValueError("V5 supports the EnnoSmart PostgreSQL or SQLite database only")
