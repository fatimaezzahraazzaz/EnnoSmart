"""Package current application sources, including untracked V2 code, without runtime data."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = (
    "agents", "modules", "backend_api", "frontend", "scripts", "deploy",
    "config", "integration", "mcp_servers", "docs",
)
ROOT_FILES = (
    ".dockerignore", ".gitignore", ".env.example", ".env.windows.example",
    "README.md", "DEPLOIEMENT_OVH.md", "pyproject.toml", "requirements.txt",
    "requirements-optional.txt", "docker-compose.ovh.yml", "docker-compose.windows.yml",
    "docker-compose.chroma.local.yml", "run_backend.ps1", "run_legal_fulltext_mcp.ps1",
    "run_server.ps1", "start_celery_worker.ps1", "start_cir_worker_windows.ps1", "start_redis.ps1",
)
SKIP_DIRS = {
    "node_modules", ".next", "__pycache__", ".git", ".pytest_cache", "tests",
    "examples", "benchmark", "storage", "outputs", "logs", "chroma", "uploads", "tmp",
}
EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".md", ".txt",
    ".json", ".toml", ".yml", ".yaml", ".ps1", ".sh", ".lock", ".png", ".svg",
    ".ico", ".jpg", ".jpeg", ".webp", ".html", ".jinja", ".jinja2", ".j2",
}


def eligible(relative: Path) -> bool:
    if any(part in SKIP_DIRS for part in relative.parts):
        return False
    name = relative.name
    if name.startswith(".env"):
        return name.endswith(".example")
    if name.startswith(("test_", "run_backend_")) or any(
        marker in name for marker in ("_before_", "_backup", ".bak", ".broken", "_regression")
    ):
        return False
    if relative.parts[:3] == ("modules", "NLP", "data"):
        return name == "domains.json"
    if relative.suffix in {".pem", ".key", ".crt", ".p12", ".pfx"}:
        return False
    return relative.suffix in EXTENSIONS or name in {"Dockerfile.backend", "Dockerfile.frontend"}


def package(output: Path) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    candidates = [ROOT / name for name in ROOT_FILES]
    candidates += [p for name in SOURCE_DIRS for p in (ROOT / name).rglob("*")
                   if p.is_file() and eligible(p.relative_to(ROOT))]
    candidates.append(ROOT / "models/fastjudge/fastjudge_linearsvc_C025.joblib")
    manifest = []
    for path in sorted(set(candidates)):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or linked release source: {path.relative_to(ROOT)}")
        relative = path.relative_to(ROOT)
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        manifest.append({"path": relative.as_posix(), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    payload = {"created_at": datetime.now(timezone.utc).isoformat(), "engine": "ennodiagnostic_v2",
               "files": manifest}
    (source / "release-manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    archive = output / "ennosmart-v2-source.tgz"
    with tarfile.open(archive, "w:gz") as stream:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                stream.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS.txt").write_text(f"{sha}  {archive.name}\n", encoding="ascii")
    return {"archive": str(archive), "files": len(manifest), "sha256": sha, "bytes": archive.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "releases" /
                        ("v2_" + datetime.now().strftime("%Y%m%d_%H%M%S")))
    args = parser.parse_args()
    print(json.dumps(package(args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
