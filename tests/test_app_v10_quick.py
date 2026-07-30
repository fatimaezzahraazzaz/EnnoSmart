# -*- coding: utf-8 -*-
from pathlib import Path
import ast

p = Path("app_multi_docs_extraction_nlp.py")
if not p.exists():
    raise FileNotFoundError("app_multi_docs_extraction_nlp.py introuvable")

code = p.read_text(encoding="utf-8")
ast.parse(code)

required = [
    "V10 universel",
    "consolidate_project_v10",
    "passages_utiles_consultant",
    "roles_cir",
    "methodes_protocoles",
    "parametres_etudies",
    "variables_mesurees",
    "raw_project_pipeline_v10_universal",
]

for r in required:
    assert r in code, f"Manquant: {r}"

for forbidden in ["from modules.NLP.router import", "NLPConfig", "process_document_fn"]:
    assert forbidden not in code, f"Ancienne dépendance encore présente: {forbidden}"

print("TEST PASSED - app V10 OK")
