from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\EnnoSmart")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.prompts import (
    SEMANTIC_EXTRACTOR_SYSTEM,
    build_extractor_user_prompt,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.runtime_adapters import (
    EnnoSmartLLMAdapter,
)

PASSAGE = """
Wang et al. montrent que la méthode LEVER reste limitée par sa dépendance à des cas
de test pertinents et par la qualité des signaux d'exécution.

Dans nos expérimentations, les premiers essais zero-shot sur des projets Java ont
produit des tests incorrects ou non compilables lorsque le modèle ne disposait pas
du contexte complet du projet et de ses dépendances.
""".strip()

def main():
    llm = EnnoSmartLLMAdapter(
        request_name="ennodiagnostic:v2:smoke:source_role",
        temperature=0.0,
        max_output_tokens=900,
        retries=1,
    )

    user_prompt = build_extractor_user_prompt(
        document_name="smoke_source_role.txt",
        document_mode="raw",
        section_title="Test provenance",
        context_before="",
        text=PASSAGE,
        context_after="",
    )

    raw = llm.extract_json(
        SEMANTIC_EXTRACTOR_SYSTEM,
        user_prompt,
    )
    data = raw if isinstance(raw, dict) else json.loads(raw)
    locks = data.get("locks") or []

    print("=" * 88)
    print("SMOKE TEST SOURCE_ROLE")
    print("=" * 88)

    for lock in locks:
        print()
        print("STATEMENT   :", lock.get("statement"))
        print("SOURCE_ROLE :", lock.get("source_role"))
        print("EVIDENCE    :", lock.get("evidence_quote"))

    literature_ok = any(
        "lever" in str(lock.get("statement", "")).lower()
        and lock.get("source_role") == "literature"
        for lock in locks
    )

    project_ok = any(
        (
            "zero-shot" in str(lock.get("statement", "")).lower()
            or "non compil" in str(lock.get("statement", "")).lower()
            or "contexte" in str(lock.get("statement", "")).lower()
        )
        and lock.get("source_role") == "project_evidence"
        for lock in locks
    )

    print()
    print("-" * 88)
    print("LEVER externe -> literature          :", "OK" if literature_ok else "ECHEC")
    print("Essais du projet -> project_evidence :", "OK" if project_ok else "ECHEC")

    if literature_ok and project_ok:
        print()
        print("SMOKE TEST SOURCE_ROLE REUSSI")
        return

    raise SystemExit("SMOKE TEST SOURCE_ROLE A VERIFIER")

if __name__ == "__main__":
    main()
