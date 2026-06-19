import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

try:
    import psycopg2
except ImportError as exc:
    raise SystemExit(
        "psycopg2-binary n'est pas installé. Lance : pip install psycopg2-binary"
    ) from exc


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DEFAULT_DATABASE_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/ennosmart"


def main():
    database_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    url = make_url(database_url)

    database_name = url.database

    if not database_name:
        raise SystemExit("DATABASE_URL ne contient pas de nom de base.")

    conn = psycopg2.connect(
        dbname="postgres",
        user=url.username,
        password=url.password,
        host=url.host or "localhost",
        port=url.port or 5432,
    )

    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (database_name,),
            )
            exists = cur.fetchone()

            if exists:
                print(f"Base PostgreSQL déjà existante : {database_name}")
                return

            cur.execute(f'CREATE DATABASE "{database_name}"')
            print(f"Base PostgreSQL créée : {database_name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
