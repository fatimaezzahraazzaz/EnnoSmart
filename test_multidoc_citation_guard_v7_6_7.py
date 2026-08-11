# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys

ROOT = r"C:\EnnoSmart"
BACKEND = r"C:\EnnoSmart\backend_api"
FFMPEG = r"C:\ffmpeg\bin"

os.chdir(ROOT)
for value in (ROOT, BACKEND):
    if value not in sys.path:
        sys.path.insert(0, value)

_ffmpeg_handle = None
if os.path.isdir(FFMPEG):
    os.environ["PATH"] = FFMPEG + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        _ffmpeg_handle = os.add_dll_directory(FFMPEG)

from modules.RAG.diagnostic_chat_service import (
    CHAT_SERVICE_VERSION,
    DiagnosticRAGChatService,
    _document_key,
)

print("=" * 100)
print("TEST GARDE FINALE CITATIONS MULTI-DOCUMENT V7.6.7 - SANS LLM")
print("=" * 100)
print("VERSION =", CHAT_SERVICE_VERSION)

# Simulation du cas réel observé :
# 4 documents attendus, seulement 3 cités dans la réponse initiale.
documents = [
    "article_Salsa_Scalian_DGA_v6_(submitted_version)_05924a579f5a.pdf",
    "Données pour constitution du dossier R&D AI-RADAR 2025 (SCALIAN)_c1f54a41d41f.msg",
    "R26554-1.1 - TACOS Validation Salsa 1_42252443453b.docx",
    "R26752-0.1 - TACOS Rapport d'etudes_fb43fc292403.docm",
]

evidence = [
    {
        "evidence_id": "E1",
        "document": documents[0],
        "excerpt": "preuve pdf",
        "section_title": "Résultats",
    },
    {
        "evidence_id": "E5",
        "document": documents[1],
        "excerpt": "preuve msg",
        "section_title": "Contexte",
    },
    {
        "evidence_id": "E8",
        "document": documents[2],
        "excerpt": "preuve docx",
        "section_title": "Méthodologie",
    },
    {
        "evidence_id": "E12",
        "document": documents[3],
        "excerpt": "preuve docm",
        "section_title": "Résultats",
    },
]

multi_document_plan = [
    {
        "document_name": documents[0],
        "candidate_found": True,
        "candidate_evidence_ids": ["E1"],
    },
    {
        "document_name": documents[1],
        "candidate_found": True,
        "candidate_evidence_ids": ["E5"],
    },
    {
        "document_name": documents[2],
        "candidate_found": True,
        "candidate_evidence_ids": ["E8"],
    },
    {
        "document_name": documents[3],
        "candidate_found": True,
        "candidate_evidence_ids": ["E12"],
    },
]

selected_sources = [
    {
        "evidence_id": "E1",
        "original_evidence_id": "E1",
        "document": documents[0],
    },
    {
        "evidence_id": "E2",
        "original_evidence_id": "E5",
        "document": documents[1],
    },
    {
        "evidence_id": "E3",
        "original_evidence_id": "E8",
        "document": documents[2],
    },
]

answer = f"""Document: {documents[0]} [E1]
- Résumé PDF.

Document: {documents[1]} [E2]
- Résumé MSG.

Document: {documents[2]} [E3]
- Résumé DOCX.

Document: R26752-0.1 - TACOS Rapport d’études_fb43fc292403.docm
- Résumé DOCM sans citation.
"""

before = DiagnosticRAGChatService._document_coverage_from_sources(selected_sources)

final_answer, final_sources, report = (
    DiagnosticRAGChatService._ensure_multi_document_citation_coverage(
        answer=answer,
        selected_sources=selected_sources,
        evidence=evidence,
        multi_document_plan=multi_document_plan,
        max_sources=8,
    )
)

after = DiagnosticRAGChatService._document_coverage_from_sources(final_sources)
expected = {_document_key(name) for name in documents}

print()
print("BEFORE_COVERED =", len(before), "/", len(expected))
print("AFTER_COVERED  =", len(after & expected), "/", len(expected))
print("MISSING_AFTER  =", sorted(expected - after))
print("REPORT =", report)

print()
print("ANSWER_FINAL")
print("-" * 100)
print(final_answer)
print("-" * 100)

if len(before & expected) != 3:
    raise SystemExit("FAIL: le scénario de départ n'est pas 3/4.")

if not expected.issubset(after):
    raise SystemExit("FAIL: la garde n'a pas obtenu 4/4.")

if report.get("added_count") != 1:
    raise SystemExit(
        f"FAIL: added_count={report.get('added_count')} au lieu de 1."
    )

if report.get("unresolved_documents"):
    raise SystemExit(
        "FAIL: documents non résolus: "
        + " | ".join(report["unresolved_documents"])
    )

if "[E4]" not in final_answer:
    raise SystemExit("FAIL: la citation du 4e document n'est pas visible.")

print()
print("FINAL_MULTIDOC_CITATION_GUARD_TEST_OK")
print("=" * 100)
