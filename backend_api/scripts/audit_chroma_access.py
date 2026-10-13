# -*- coding: utf-8 -*-
"""Audit statique bloquant des accès Chroma EnnoSmart."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_PARTS = {
    ".git", ".venv", ".venv-mcp", ".venv_py314", "node_modules", "archive",
    "storage", "outputs", ".chroma_finish_backup", ".chroma_hotfix2_backup",
    "__pycache__",
}
DIRECT_CLIENT_ALLOWED = {
    "modules/RAG/chroma_client.py",
    "backend_api/scripts/migrate_chroma_to_service.py",
    "backend_api/scripts/test_chroma_isolation.py",
    "backend_api/scripts/audit_chroma_access.py",
}
UNSCOPED_STORE_ALLOWED = {
    "backend_api/scripts/migrate_chroma_to_service.py",
    "backend_api/scripts/test_chroma_isolation.py",
    "backend_api/scripts/audit_chroma_access.py",
}
GLOBAL_NAME_ALLOWED = {
    # L'audit lui-même contient la chaîne à rechercher.
    "backend_api/scripts/audit_chroma_access.py",
}
def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def skipped(path: Path) -> bool:
    return any(
        part in SKIP_PARTS
        or (part.startswith(".chroma_") and part.endswith("_backup"))
        or (part.startswith(".storage_") and part.endswith("_backup"))
        for part in path.parts
    )


def call_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def main() -> int:
    violations: list[str] = []
    direct_re = re.compile(r"chromadb\.(?:PersistentClient|HttpClient|Client)\s*\(")

    for path in ROOT.rglob("*.py"):
        if skipped(path):
            continue
        rp = rel(path)
        try:
            # utf-8-sig retire proprement le BOM U+FEFF sans toucher au fichier.
            text = path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            violations.append(f"{rp}: lecture impossible: {exc}")
            continue

        if rp not in DIRECT_CLIENT_ALLOWED and direct_re.search(text):
            violations.append(f"{rp}: client chromadb direct interdit")

        if "ennosmart_memory_v2_global" in text and rp not in GLOBAL_NAME_ALLOWED:
            violations.append(f"{rp}: ancienne collection Memory V2 globale encore référencée")

        try:
            tree = ast.parse(text, filename=rp)
        except SyntaxError as exc:
            violations.append(f"{rp}:{exc.lineno}: syntaxe Python invalide: {exc.msg}")
            continue

        if rp in UNSCOPED_STORE_ALLOWED:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or call_name(node) != "RAGVectorStore":
                continue
            kws = {kw.arg for kw in node.keywords if kw.arg}
            if not {"scope_metadata", "collection_namespace"}.issubset(kws):
                violations.append(
                    f"{rp}:{getattr(node, 'lineno', '?')}: "
                    "RAGVectorStore sans scope_metadata + collection_namespace"
                )

    if violations:
        print("[ECHEC] Audit Chroma runtime :")
        for item in sorted(set(violations)):
            print(" -", item)
        return 1

    print("[OK] Audit Chroma runtime : aucun accès non scoped dans le runtime API/workers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
