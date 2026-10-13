"""Start the existing EnnoSmart API with V4, using an explicit environment profile."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    from dotenv import load_dotenv
    if not args.env_file.is_file():
        parser.error("Environment profile missing; specify --env-file or create .env from .env.example.")
    load_dotenv(args.env_file, override=False)
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=args.port)
