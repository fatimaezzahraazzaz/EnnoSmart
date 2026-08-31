# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import hashlib
import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

PATCH_FILES = [
    "agents/EnnoDiagnostic/ennodiagnostic_agent.py",
    "agents/EnnoDiagnostic/diagnostic_static_presenter.py",
    "agents/EnnoDiagnostic/narrative_evidence_balancer.py",
]

LOCK_FILES = [
    "agents/EnnoDiagnostic/consultant_verrou_synthesizer.py",
    "agents/EnnoDiagnostic/scientific_axis_synthesizer.py",
]

PROTECTED_AGENT_FUNCTIONS = {
    "_load_nlp_lock_group_sources": "90f837e050491465facadfc30ae731f99b04edf8228a8581adf5ce27dd73b58e",
    "_load_recovered_missing_lock_candidates": "13c933e890313ed9691da1aaf0c8e9278f39c435b0b2de6c4c28f6b937191895",
    "build_llm_reformulated_verrous": "a1594c3cae3bfc049500ee18937a1993173b45aed93b1df68aaba32312c1f7ef",
    "_enrich_verrous_with_frascati": "e2c4fc83f02af0ff587ef7381f27cb1532151c4f88fe209a3fd6c6db258c5a86",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def function_hashes(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines(True)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in PROTECTED_AGENT_FUNCTIONS:
            source = "".join(lines[node.lineno - 1: node.end_lineno])
            out[node.name] = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return out


def assert_expected_lock_functions(path: Path, *, label: str) -> None:
    found = function_hashes(path)
    missing = [name for name in PROTECTED_AGENT_FUNCTIONS if name not in found]
    if missing:
        raise RuntimeError(f"{label}: fonctions verrou introuvables: {missing}")
    changed = {
        name: (PROTECTED_AGENT_FUNCTIONS[name], found[name])
        for name in PROTECTED_AGENT_FUNCTIONS
        if found[name] != PROTECTED_AGENT_FUNCTIONS[name]
    }
    if changed:
        raise RuntimeError(
            f"{label}: la logique verrou locale ne correspond plus à la base protégée V5.5 ; "
            f"installation annulée pour ne pas écraser des changements. Détails={changed}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Installe EnnoDiagnostic V5.6 : objectif précis, démarches cross-role, "
            "résultats expérimentaux corroborés et paramètres nettoyés, sans modifier les verrous."
        )
    )
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    payload = Path(__file__).resolve().parent / "files"
    backup = repo / ".ennosmart_patch_backups" / (
        "narrative_precision_v5_6_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )

    for rel in LOCK_FILES:
        if not (repo / rel).exists():
            raise RuntimeError(f"Fichier verrou local attendu absent: {repo / rel}")
    for rel in PATCH_FILES:
        if not (payload / rel).exists():
            raise RuntimeError(f"Fichier du paquet absent: {payload / rel}")
    for rel in PATCH_FILES[:2]:
        if not (repo / rel).exists():
            raise RuntimeError(f"Fichier local attendu absent: {repo / rel}")

    local_agent = repo / "agents/EnnoDiagnostic/ennodiagnostic_agent.py"
    payload_agent = payload / "agents/EnnoDiagnostic/ennodiagnostic_agent.py"
    assert_expected_lock_functions(local_agent, label="AVANT")
    assert_expected_lock_functions(payload_agent, label="PAYLOAD")

    lock_hashes_before = {rel: sha256_file(repo / rel) for rel in LOCK_FILES}
    protected_before = function_hashes(local_agent)

    backup.mkdir(parents=True, exist_ok=False)
    existed_before: dict[str, bool] = {}
    for rel in PATCH_FILES:
        src = repo / rel
        existed_before[rel] = src.exists()
        if src.exists():
            dst = backup / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    (backup / "INSTALL_MANIFEST.txt").write_text(
        "\n".join(
            ["LOCK FILE HASHES BEFORE:"]
            + [f"{rel}={digest}" for rel, digest in lock_hashes_before.items()]
            + ["", "PROTECTED AGENT FUNCTION HASHES BEFORE:"]
            + [f"{name}={digest}" for name, digest in protected_before.items()]
            + ["", "FILES EXISTED BEFORE:"]
            + [f"{rel}={value}" for rel, value in existed_before.items()]
        ),
        encoding="utf-8",
    )

    print(f"[OK] Backup créé : {backup}")
    try:
        copied: list[Path] = []
        for rel in PATCH_FILES:
            src = payload / rel
            dst = repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(dst)
            print(f"[OK] Installé : {rel}")

        for path in copied:
            py_compile.compile(str(path), doraise=True)
        print("[OK] Compilation Python réussie.")

        lock_hashes_after = {rel: sha256_file(repo / rel) for rel in LOCK_FILES}
        if lock_hashes_after != lock_hashes_before:
            raise RuntimeError("Un fichier de synthèse des verrous a changé pendant l'installation.")
        assert_expected_lock_functions(local_agent, label="APRES")
        if function_hashes(local_agent) != protected_before:
            raise RuntimeError("Une fonction protégée de détection/récupération des verrous a changé.")

        print("[OK] GARDE VERROUS : 2 fichiers de synthèse inchangés.")
        print("[OK] GARDE VERROUS : 4 fonctions critiques strictement inchangées.")
        print("[OK] Correctif V5.6 installé : objectif + démarches + résultats + paramètres.")
        print("[OK] Performance V5.5 conservée : aucun nouveau Chroma ni appel LLM ajouté par le balancer.")
        print(f"[INFO] Rollback disponible : {backup}")
    except Exception:
        print("[ERREUR] Installation V5.6 interrompue. Restauration...", file=sys.stderr)
        for rel in PATCH_FILES:
            src = backup / rel
            dst = repo / rel
            if existed_before.get(rel):
                if src.exists():
                    shutil.copy2(src, dst)
            else:
                try:
                    if dst.exists():
                        dst.unlink()
                except Exception as exc:
                    print(f"[ROLLBACK WARN] {rel}: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
