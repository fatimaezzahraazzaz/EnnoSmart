from pathlib import Path
import os
from sqlalchemy import create_engine, text, inspect

ROOT = Path(__file__).resolve().parent
env_path = ROOT / ".env"

def load_env_file(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_env_file(env_path)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/ennosmart"
)

engine = create_engine(DATABASE_URL)

tables_to_clear = [
    "articles",
    "scholar_runs",
    "verrous",
    "diagnostic_runs",
    "documents",
    "projects",
]

with engine.begin() as conn:
    inspector = inspect(conn)
    existing = set(inspector.get_table_names())

    tables = [t for t in tables_to_clear if t in existing]

    if not tables:
        print("Aucune table projet trouvée.")
    else:
        sql = "TRUNCATE TABLE " + ", ".join(tables) + " RESTART IDENTITY CASCADE;"
        print("Suppression des données :", tables)
        conn.execute(text(sql))
        print("✅ Base nettoyée : projets, documents, diagnostics, verrous, scholar, articles supprimés.")
        print("✅ Les utilisateurs sont conservés.")
