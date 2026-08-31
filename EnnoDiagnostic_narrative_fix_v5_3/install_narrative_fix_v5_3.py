# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

TARGETS = ['agents/EnnoDiagnostic/ennodiagnostic_agent.py', 'agents/EnnoDiagnostic/diagnostic_static_presenter.py', 'modules/NLP/demarche_legibility.py', 'modules/NLP/frascati_assessment.py']
NEW_FILES = ["agents/EnnoDiagnostic/project_fact_gate.py"]
EXPECTED_V52_SHA256 = {'agents/EnnoDiagnostic/ennodiagnostic_agent.py': 'a42b7c1b1c4444244389a6cbe9f878a7540aa024691f7481443c5f575f832045', 'agents/EnnoDiagnostic/diagnostic_static_presenter.py': '5ce398168bc3efba63e3c08612050a340ae01d1c835e5490664b8a36b70daf7f', 'modules/NLP/demarche_legibility.py': 'f66555e0a1e6569c9e499a7169ab88dbd1429cbad724c858bc070cd4b27e7c86', 'modules/NLP/frascati_assessment.py': 'e16b85a536f632e62e12ac19ed11a5b794449cd517f27a73dd82a7e97307d785'}
LOCK_FILES = ['agents/EnnoDiagnostic/consultant_verrou_synthesizer.py', 'agents/EnnoDiagnostic/scientific_axis_synthesizer.py']
LOCK_AGENT_FUNCTIONS = ['_load_nlp_lock_group_sources', '_load_recovered_missing_lock_candidates', 'retrieve_all_sections']

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def function_source_hash(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = int(node.lineno) - 1
            end = int(getattr(node, "end_lineno", node.lineno))
            blob = "".join(lines[start:end]).encode("utf-8")
            return hashlib.sha256(blob).hexdigest()
    raise RuntimeError(f"Fonction de garde introuvable: {name} dans {path}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--allow-non-v52", action="store_true",
                    help="Autorise un fichier cible différent de la V5.2 attendue. À utiliser seulement après vérification manuelle.")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    payload = Path(__file__).resolve().parent / "files"
    backup = repo / ".ennosmart_patch_backups" / ("narrative_fix_v5_3_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    backup.mkdir(parents=True, exist_ok=False)

    # Préflight : ce correctif est construit SUR V5.2. On refuse d'écraser
    # silencieusement une version locale inconnue.
    mismatches = []
    for rel, expected in EXPECTED_V52_SHA256.items():
        path = repo / rel
        if not path.exists():
            mismatches.append(f"ABSENT {rel}")
            continue
        actual = sha256(path)
        if actual != expected:
            mismatches.append(f"{rel} sha={actual} attendu={expected}")
    if mismatches and not args.allow_non_v52:
        print("[STOP] La base locale n'est pas exactement la V5.2 attendue.", file=sys.stderr)
        for row in mismatches:
            print(" - " + row, file=sys.stderr)
        print("Aucun fichier n'a été remplacé.", file=sys.stderr)
        raise SystemExit(3)

    # Garde des verrous AVANT : fichiers spécialisés + fonctions de récupération
    # dans l'agent. Ils doivent être identiques APRES l'installation.
    lock_file_hashes_before = {}
    for rel in LOCK_FILES:
        path = repo / rel
        if path.exists():
            lock_file_hashes_before[rel] = sha256(path)

    agent_before = repo / "agents/EnnoDiagnostic/ennodiagnostic_agent.py"
    lock_func_hashes_before = {
        name: function_source_hash(agent_before, name)
        for name in LOCK_AGENT_FUNCTIONS
    }

    # Backup complet des fichiers remplacés et du module gate s'il existait.
    for rel in [*TARGETS, *NEW_FILES]:
        src = repo / rel
        if src.exists():
            dst = backup / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    (backup / "lock_guard_before.json").write_text(
        json.dumps({
            "lock_files": lock_file_hashes_before,
            "agent_lock_functions": lock_func_hashes_before,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] Backup créé : {backup}")

    copied = []
    try:
        for rel in [*TARGETS, *NEW_FILES]:
            src = payload / rel
            if not src.exists():
                raise RuntimeError(f"Payload absent: {src}")
            dst = repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(dst)
            print(f"[OK] Remplacé : {rel}")

        for path in copied:
            py_compile.compile(str(path), doraise=True)
        print("[OK] Compilation Python réussie.")

        # Garde des verrous APRES.
        for rel, before_hash in lock_file_hashes_before.items():
            after_hash = sha256(repo / rel)
            if after_hash != before_hash:
                raise RuntimeError(f"GARDE VERROUS: {rel} a changé alors qu'il ne devait pas être touché.")

        agent_after = repo / "agents/EnnoDiagnostic/ennodiagnostic_agent.py"
        for name, before_hash in lock_func_hashes_before.items():
            after_hash = function_source_hash(agent_after, name)
            if after_hash != before_hash:
                raise RuntimeError(f"GARDE VERROUS: fonction {name} modifiée.")

        print("[OK] GARDE VERROUS : fichiers de synthèse inchangés.")
        print("[OK] GARDE VERROUS : détection/récupération des verrous dans l'agent inchangée.")
        print("[OK] Correctif narratif V5.3 installé.")
        print(f"[INFO] Rollback : {backup}")
    except Exception:
        # Rollback des cibles uniquement. Les fichiers verrous n'ont jamais été copiés.
        for rel in TARGETS:
            src = backup / rel
            if src.exists():
                shutil.copy2(src, repo / rel)
        for rel in NEW_FILES:
            src = backup / rel
            dst = repo / rel
            if src.exists():
                shutil.copy2(src, dst)
            elif dst.exists():
                dst.unlink()
        print("[ROLLBACK] Les fichiers cibles ont été restaurés.", file=sys.stderr)
        raise

if __name__ == "__main__":
    main()
