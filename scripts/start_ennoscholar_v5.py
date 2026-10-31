"""Explicit local test startup; no scientific run or recovery on startup."""
import argparse
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "experiments/ennoscholar_v5/.deps"), str(ROOT / "backend_api"), str(ROOT)]


def main(argv=None):
    from agents.EnnoScholarV5.startup import configure_local_v4, LOCAL_V4_ROOT, LOCAL_V4_OPENAI
    from agents.EnnoScholarV5.config import ConfigurationError
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("local-v4", "env"), default="local-v4",
        help="local-v4 : installation locale existante ; env : profil applicatif explicite (PostgreSQL possible)")
    parser.add_argument("--env-file", type=Path, default=Path(r"D:\EnnoSmart\.env"))
    parser.add_argument("--database", type=Path, default=LOCAL_V4_ROOT / "ennoscholar_v4_test.db")
    parser.add_argument("--openai-env-file", type=Path, default=LOCAL_V4_OPENAI)
    parser.add_argument("--check-config", action="store_true", help="Contrôle en lecture seule ; ne démarre pas le backend.")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args(argv)
    try:
        if args.profile == "local-v4":
            info = configure_local_v4(ROOT, args.env_file, args.database, args.openai_env_file)
        else:
            if not args.env_file.is_file():
                raise ValueError("Profil applicatif absent : fournir --env-file.")
            from dotenv import load_dotenv
            from sqlalchemy.engine import make_url
            load_dotenv(args.env_file, override=False)
            raw_url = os.getenv("DATABASE_URL")
            if not raw_url:
                raise ValueError("DATABASE_URL explicite requis pour --profile env.")
            url = make_url(raw_url)
            if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
                database = Path(url.database)
                if not database.is_absolute():
                    database = args.env_file.resolve().parent / database
                    os.environ["DATABASE_URL"] = url.set(database=str(database.resolve())).render_as_string(hide_password=False)
            os.environ.pop("ENNOSCHOLAR_V5_LOCAL_V4_DATABASE", None)
            os.environ["ENNOSCHOLAR_V5_TEST_ENABLED"] = "1"
            # Deliberately omit credentials and DSN from diagnostic output.
            info = {"profile": "env", "database_kind": url.get_backend_name(), "shared_profile": str(args.env_file.resolve())}
            from agents.EnnoScholarV5.config import runtime_config
            runtime_config()  # Same resource validation as the V5 service; no client/run.
    except (ValueError,ConfigurationError) as exc:
        parser.exit(2, "Configuration V5 refusée : "+str(exc)+"\n")
    print(json.dumps(info, ensure_ascii=False, indent=2), flush=True)
    if args.check_config:
        return 0
    from agents.EnnoScholarV4.service import hold_background
    hold_background()
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
