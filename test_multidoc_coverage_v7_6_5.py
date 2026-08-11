# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
from collections import defaultdict

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
    _clean,
    _document_key,
    _source_document,
)

QUESTION = (
    "Pour chacun des documents du projet, indique l'objectif principal, "
    "la méthode ou l'outil étudié, le résultat expérimental le plus important "
    "et la principale limite identifiée. Sépare clairement la réponse par "
    "document et cite au moins une source provenant de chaque document."
)

service = DiagnosticRAGChatService(
    organisme="Scalian",
    project="AI-RADAR",
    year="2025",
)

print("=" * 100)
print("TEST MULTI-DOCUMENT V7.6.5 - SANS APPEL LLM")
print("=" * 100)
print("PYTHON  =", sys.executable)
print("VERSION =", CHAT_SERVICE_VERSION)
print("MODE_EXHAUSTIF =", service._wants_exhaustive_multi_document_coverage(QUESTION))

documents = service.available_documents()

print()
print("DOCUMENTS_AVAILABLE =", len(documents))
for index, document in enumerate(documents, start=1):
    print(
        f"D{index} | id={document.get('document_id')} | "
        f"name={document.get('document_name')}"
    )

sources, subquestions, report = service._retrieve_all_documents_with_coverage(
    question=QUESTION,
    history=(),
    per_document_limit=4,
)

print()
print("SOUS_QUESTIONS =", len(subquestions))
for index, item in enumerate(subquestions, start=1):
    print(f"Q{index} =", item)

print()
print("RAPPORT PAR DOCUMENT")
print("-" * 100)
for item in report:
    print(
        f"{item.get('document_name')} | "
        f"sources={item.get('source_count')} | "
        f"candidate_found={item.get('candidate_found')}"
    )

by_document = defaultdict(list)
for source in sources:
    by_document[_document_key(_source_document(source))].append(source)

expected = {
    _document_key(item.get("document_name")): _clean(item.get("document_name"))
    for item in documents
    if _document_key(item.get("document_name"))
}

covered = set(by_document)
missing = [
    name
    for key, name in expected.items()
    if key not in covered
]

print()
print("TOTAL_SOURCES =", len(sources))
print("DOCUMENTS_COVERED =", len(covered))
print("DOCUMENTS_EXPECTED =", len(expected))
print("MISSING_DOCUMENTS =", missing)

for key, name in expected.items():
    print()
    print("=" * 100)
    print("DOCUMENT =", name)
    print("=" * 100)
    for index, source in enumerate(by_document.get(key, []), start=1):
        meta = source.get("metadata") or {}
        print(
            f"S{index} | section={meta.get('section_title')} | "
            f"paragraph={meta.get('paragraph_index')} | "
            f"role={meta.get('role')} | "
            f"score={source.get('_chat_score')}"
        )
        print(_clean(source.get("text"))[:650])

print()
print("=" * 100)

if not service._wants_exhaustive_multi_document_coverage(QUESTION):
    raise SystemExit("FAIL: le mode multi-document exhaustif n'a pas été détecté.")

if not expected:
    raise SystemExit("FAIL: aucun document disponible.")

if missing:
    raise SystemExit(
        "FAIL: documents non couverts: " + " | ".join(missing)
    )

print("MULTIDOC_COVERAGE_TEST_OK")
print("=" * 100)
