# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import py_compile
import sys


REQUIRED = {
    "agents/EnnoScholar/guided_research/lot1/domain/enums.py": [
        'ADD_VERROU_AND_SEARCH = "add_verrou_and_search"',
    ],
    "agents/EnnoScholar/guided_research/lot1/domain/models.py": [
        "verrous: list[dict[str, Any]]",
    ],
    "agents/EnnoScholar/guided_research/lot1/conversation_understanding_service.py": [
        "class _ConsultantVerrouPayload",
        "verrous: list[_ConsultantVerrouPayload]",
        "ConsultantIntent.ADD_VERROU_AND_SEARCH",
        "current_verrous",
        "verrous=[",
    ],
    "agents/EnnoScholar/guided_research/application/guided_research_agent.py": [
        "def _add_consultant_verrou_and_search",
        "def _invalidate_plan_after_verrou_scope_change",
        "create_or_reuse_consultant_verrou",
        "list_latest_diagnostic_verrous_for_chat",
        "ConsultantIntent.ADD_VERROU_AND_SEARCH",
    ],
    "backend_api/services/consultant_verrou_service.py": [
        "def create_or_reuse_consultant_verrou",
        "def list_latest_diagnostic_verrous_for_chat",
        'consultant_status="garde"',
        '"automatic_verrou_creation": False',
        '"scientific_support_status": "pending_research"',
    ],
}


def verify(root: Path) -> list[str]:
    errors: list[str] = []
    for relative, markers in REQUIRED.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"MISSING_FILE: {path}")
            continue
        text = path.read_text(encoding="utf-8-sig")
        for marker in markers:
            if marker not in text:
                errors.append(f"MISSING_MARKER: {relative}: {marker}")
        try:
            ast.parse(text, filename=str(path))
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:
            errors.append(f"PYTHON_SYNTAX_ERROR: {relative}: {exc}")

    service = root / "backend_api/services/consultant_verrou_service.py"
    if service.is_file():
        text = service.read_text(encoding="utf-8-sig")
        forbidden = [
            'score=1.0',
            'tag_cir="eligible"',
            'tag_cir="eligible_cir"',
        ]
        for marker in forbidden:
            if marker in text:
                errors.append(f"FORBIDDEN_AUTOVALIDATION: {marker}")

    agent = root / "agents/EnnoScholar/guided_research/application/guided_research_agent.py"
    if agent.is_file():
        text = agent.read_text(encoding="utf-8-sig")
        if 'request["target_verrous"] = [str(verrou_id)]' not in text:
            errors.append("MISSING_TARGET_LINK: les recherches ne sont pas rattachées au nouveau verrou")
        if "plan précédemment approuvé a été invalidé" not in text:
            errors.append("MISSING_PLAN_INVALIDATION_MESSAGE")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="C:/EnnoSmart")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    errors = verify(root)
    if errors:
        print("CONSULTANT_VERROU_CHAT_VERIFY_FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("CONSULTANT_VERROU_CHAT_VERIFY_OK")
    print("Contrôles : intention, payload, persistance, rattachement recherche, garde-fous et syntaxe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
