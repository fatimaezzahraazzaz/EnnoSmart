"""Check unchanged legacy code against the exact starting commit, not assumptions."""
import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "46c2665"


def test_legacy_agents_routes_and_pages_are_byte_for_byte_unchanged():
    paths = ["agents/EnnoScholar", "agents/EnnoScholarV2", "agents/EnnoScholarV3", "agents/EnnoScholarV31",
        "agents/EnnoDiagnostic", "agents/EnnoAmelioration", "backend_api/core", "backend_api/db/models.py",
        "backend_api/routers/scholar.py", "backend_api/routers/scholar_v2.py", "backend_api/routers/scholar_v3.py",
        "backend_api/routers/scholar_v31.py", "backend_api/routers/diagnostic.py", "backend_api/routers/improvement.py",
        "backend_api/routers/auth.py", "frontend/app/ennoscholar-v2", "frontend/app/ennoscholar-v3", "frontend/app/ennoscholar-v3-1",
        "frontend/components/ennosmart/ennoscholar-v2-chat.tsx", "frontend/components/ennosmart/ennoscholar-v3-chat.tsx",
        "frontend/components/ennosmart/ennoscholar-v31-chat.tsx"]
    result = subprocess.run(["git", "diff", "--exit-code", BASE, "--", *paths], cwd=ROOT, capture_output=True)
    assert result.returncode == 0, result.stdout.decode(errors="replace")


def test_sidebar_preserves_all_legacy_entries_and_v4_requires_no_project():
    shell = (ROOT / "frontend/components/ennosmart/app-shell.tsx").read_text(encoding="utf-8")
    for name in ["EnnoScholar", "EnnoScholar V2", "EnnoScholar V3", "EnnoScholar V3.1", "EnnoDiagnostic", "EnnoAmelioration", "EnnoScholar V4"]:
        assert f'label: "{name}"' in shell
    main = (ROOT / "backend_api/main.py").read_text(encoding="utf-8")
    for name in ["scholar", "scholar_v2", "scholar_v3", "scholar_v31", "diagnostic", "improvement", "auth", "scholar_v4"]:
        assert f"app.include_router({name}.router)" in main
    for path in (ROOT / "agents/EnnoScholarV4").rglob("*.py"):
        imports = [node.module or "" for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(node, ast.ImportFrom)]
        assert not any(name.startswith(("agents.EnnoDiagnostic", "chromadb", "langgraph", "crewai", "autogen")) for name in imports)


def test_remote_urls_reject_private_addresses_and_credentials(monkeypatch):
    import socket
    import time
    import pytest
    from agents.EnnoScholarV4.network import resolve_public
    for address in ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "fe80::1", "::ffff:127.0.0.1"]:
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, ip=address: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))])
        with pytest.raises(ValueError):
            resolve_public("https://example.org/paper", time.monotonic()+3)
    for url in ["file:///secret", "https://user:password@example.org/paper", "https://example.org:8000/secret"]:
        with pytest.raises(ValueError):
            resolve_public(url, time.monotonic()+3)
