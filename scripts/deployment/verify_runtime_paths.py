from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
for candidate in (PROJECT_ROOT, PROJECT_ROOT / "backend_api"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from modules.common.runtime_paths import (  # noqa: E402
    code_root,
    data_root,
    experience_memory_root,
    outputs_root,
    storage_root,
    uploads_root,
)
from modules.RAG.chroma_client import (  # noqa: E402
    chroma_connection_info,
    chroma_http_enabled,
    create_chroma_client,
)


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _quick_check_chroma(path: Path) -> str:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
        return str(row[0] if row else "unknown")
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--skip-chroma-check", action="store_true")
    args = parser.parse_args()

    code = code_root().resolve()
    paths = {
        "code": code,
        "data": data_root().resolve(),
        "storage": storage_root().resolve(),
        "outputs": outputs_root().resolve(),
        "uploads": uploads_root().resolve(),
        "experience_memory": experience_memory_root().resolve(),
    }

    for name, path in paths.items():
        print(f"{name}={path}")

    errors: list[str] = []
    for name in ("data", "storage", "outputs", "uploads", "experience_memory"):
        if _inside(paths[name], code):
            errors.append(f"{name} pointe encore dans le depot : {paths[name]}")

    if not args.read_only:
        paths["data"].mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                prefix="ennosmart-write-check-",
                dir=paths["data"],
                delete=True,
            ) as stream:
                stream.write(b"ok")
                stream.flush()
        except Exception as exc:
            errors.append(f"volume persistant non inscriptible : {exc}")

    nlp_files = list(paths["storage"].rglob("nlp_result.json")) if paths["storage"].exists() else []
    chroma_files = list(paths["storage"].rglob("chroma.sqlite3")) if paths["storage"].exists() else []
    print(f"nlp_result_count={len(nlp_files)}")
    print(f"legacy_chroma_sqlite_count={len(chroma_files)}")
    chroma_info = chroma_connection_info()
    print(f"chroma_mode={chroma_info.get('mode')}")
    print(f"chroma_location={chroma_info.get('location')}")
    print(f"chroma_scope_enforced={chroma_info.get('scope_enforced')}")

    if not args.skip_chroma_check:
        if chroma_http_enabled():
            try:
                heartbeat = create_chroma_client().heartbeat()
                print(f"chroma_heartbeat={heartbeat}")
                if not chroma_info.get("scope_enforced"):
                    errors.append("Le cloisonnement Chroma HTTP n'est pas activé.")
            except Exception as exc:
                errors.append(f"Service Chroma HTTP indisponible : {exc}")
        else:
            for chroma_path in chroma_files:
                try:
                    result = _quick_check_chroma(chroma_path)
                    print(f"chroma={chroma_path} quick_check={result}")
                    if result.lower() != "ok":
                        errors.append(f"Chroma invalide : {chroma_path} ({result})")
                except Exception as exc:
                    errors.append(f"Chroma illisible : {chroma_path} ({exc})")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("RUNTIME_STORAGE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
