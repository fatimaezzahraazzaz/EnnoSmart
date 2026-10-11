from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

from agents.EnnoScholarV2.citations import parse_output
from conftest import message

ROOT = Path(__file__).resolve().parents[2]


def test_scholar_v1_package_and_all_three_clients_still_importable():
    for name in ("agents.EnnoScholar", "agents.EnnoScholar.semantic_scholar_client", "agents.EnnoScholar.openalex_client", "agents.EnnoScholar.arxiv_client"):
        assert importlib.import_module(name)


def route_decorators(path):
    return [ast.dump(d) for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for d in node.decorator_list if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"]


def test_existing_routes_present_and_router_addition_is_independent():
    scholar = route_decorators(ROOT / "backend_api/routers/scholar.py")
    diagnostic = route_decorators(ROOT / "backend_api/routers/diagnostic.py")
    assert scholar and diagnostic
    main = (ROOT / "backend_api/main.py").read_text(encoding="utf-8")
    assert "app.include_router(scholar.router)" in main and "app.include_router(diagnostic.router)" in main
    assert "app.include_router(scholar_v2.router)" in main
    v2 = (ROOT / "backend_api/routers/scholar_v2.py").read_text()
    assert "get_current_user" in v2 and "get_project" not in v2 and "DiagnosticRun" not in v2


def test_v2_no_v1_chroma_diagnostic_imports_or_database_schema_changes():
    for path in (ROOT / "agents/EnnoScholarV2").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(name.startswith(("agents.EnnoScholar.", "agents.EnnoDiagnostic", "chromadb", "sqlalchemy")) for name in imports)


def test_sidebar_route_and_existing_frontend_preserved():
    shell = (ROOT / "frontend/components/ennosmart/app-shell.tsx").read_text(encoding="utf-8")
    assert 'label: "EnnoScholar"' in shell and 'label: "EnnoDiagnostic"' in shell
    assert 'label: "EnnoScholar V2"' in shell and 'case "scholar-v2"' in shell
    assert shell.count('"scholar-v2",') >= 3
    page = (ROOT / "frontend/app/ennoscholar-v2/page.tsx").read_text(encoding="utf-8")
    assert 'initialPage="scholar-v2"' in page and "getMe" in page
    chat = (ROOT / "frontend/components/ennosmart/ennoscholar-v2-chat.tsx").read_text(encoding="utf-8")
    assert "Nouvelle conversation" in chat and "Recherche en cours" in chat
    assert "sessionStorage" not in chat and "localStorage" not in chat


def test_multiblock_citation_offsets_and_exact_urls():
    output = [message("😀 Premier bloc"), message("Suite [1]", [{"type": "url_citation", "url": "https://example.org/paper?a=1&b=2#part", "title": "Source", "start_index": 6, "end_index": 9}])]
    answer, citations = parse_output(output)
    assert answer[citations[0].start_index:citations[0].end_index] == "[1]"
    assert citations[0].url.endswith("?a=1&b=2#part")


def test_invalid_citation_offset_retains_clickable_source_without_false_position():
    _, citations = parse_output([message("Short", [{"type": "url_citation", "url": "https://example.org", "start_index": 99, "end_index": 100}])])
    assert citations[0].start_index is None and citations[0].cited


def test_model_configuration_is_only_in_v2_config_and_examples():
    for path in (ROOT / "agents/EnnoScholarV2").rglob("*.py"):
        if path.name != "config.py":
            assert 'os.getenv("ENNOSCHOLAR_V2_MODEL"' not in path.read_text()
