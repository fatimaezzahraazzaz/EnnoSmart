"""Launch the real local EnnoSmart API with the appropriate environment profile."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port invalide.")
    from dotenv import load_dotenv
    profile = args.env_file or (ROOT/".env.native" if (ROOT/".env.native").is_file() else ROOT/".env")
    load_dotenv(profile, override=False)
    os.environ.setdefault("CORS_ORIGINS", "http://localhost:3002,http://127.0.0.1:3002")
    os.environ.setdefault("ENNOSCHOLAR_V31_PAID_CALLS_AUTHORIZED", "false")
    sys.path[:0] = [str(ROOT), str(ROOT/"backend_api")]
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__": main()
