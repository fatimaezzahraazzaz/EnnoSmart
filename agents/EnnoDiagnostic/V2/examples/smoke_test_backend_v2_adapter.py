from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend_api.services.ennodiagnostic_v2_service import (
    build_v2_runtime_context,
    load_v2_document_inputs,
)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    processed = root / "processed"
    corpus = processed / "fulltext_rag_v1"
    corpus.mkdir(parents=True)

    (corpus / "001_doc.json").write_text(
        json.dumps(
            {
                "document": "AI_CODE_CIR_FINAL.docx",
                "document_id": "42",
                "source_path": "C:/fake/AI_CODE_CIR_FINAL.docx",
                "extension": ".docx",
                "document_type": "Élément de travail",
                "text": (
                    "Le projet vise à générer automatiquement des tests unitaires Java. "
                    "Des essais ont montré plusieurs cas non compilables."
                ),
                "sections_count": 2,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    (corpus / "manifest.json").write_text(
        json.dumps({"documents_count": 1}),
        encoding="utf-8",
    )

    fake_store = SimpleNamespace(
        documents_processed_dir=processed,
    )

    documents = load_v2_document_inputs(fake_store)

    assert len(documents) == 1
    assert documents[0].document_id == "42"
    assert documents[0].name == "AI_CODE_CIR_FINAL.docx"
    assert "tests unitaires Java" in documents[0].text

    project = SimpleNamespace(
        id=7,
        organisme="SCALIAN",
        project_name="AI-CODE",
        subproject_name=None,
        year="2024",
        domain_label=(
            "Intelligence artificielle & Data | "
            "Informatique & Logiciels"
        ),
    )

    context = build_v2_runtime_context(project)

    assert context["domain_tags"] == [
        "Intelligence artificielle & Data",
        "Informatique & Logiciels",
    ]

    print("=" * 80)
    print("SMOKE TEST BACKEND -> ENNODIAGNOSTIC V2")
    print("=" * 80)
    print("Documents :", len(documents))
    print("Nom :", documents[0].name)
    print("Domaines :", context["domain_tags"])
    print()
    print("SMOKE TEST BACKEND V2 ADAPTER REUSSI")
