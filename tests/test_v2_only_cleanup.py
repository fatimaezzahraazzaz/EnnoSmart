from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]

from agents.EnnoAmelioration.application import diagnostic_orchestration_service as scoped
from agents.EnnoAmelioration.domain.models import ImprovementRequest, TargetScope
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2 import full_runtime
from modules.RAG import full_document_corpus, indexer
from services import diagnostic_service


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network or LLM calls are allowed in cleanup tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def test_v1_agent_removed_and_backend_v2_only():
    assert importlib.util.find_spec("agents.EnnoDiagnostic.ennodiagnostic_agent") is None
    assert {p.name for p in (ROOT / "agents/EnnoDiagnostic").glob("*.py")} == {"__init__.py"}
    assert not hasattr(diagnostic_service, "run_nlp_and_rag")
    assert not hasattr(diagnostic_service, "_mirror_year_files_for_agent_ai")


def test_shared_memory_gate_still_loaded():
    from modules.NLP import demarche_legibility, frascati_assessment, project_fact_gate
    assert demarche_legibility.gate_project_fact is project_fact_gate.gate_project_fact
    assert frascati_assessment._gate_external_or_reference is project_fact_gate.is_external_or_reference


@pytest.fixture
def scoped_run(tmp_path, monkeypatch):
    store = SimpleNamespace(
        project_dir=tmp_path, diagnostics_dir=tmp_path / "diagnostics",
        documents_processed_dir=tmp_path / "documents/processed",
    )
    store.diagnostics_dir.mkdir()
    monkeypatch.setattr(scoped, "_scoped_project_store", lambda *args: store)
    monkeypatch.setattr(scoped, "diagnostic_context", lambda *args: {})
    monkeypatch.setattr(scoped, "_diagnostic_service", lambda: diagnostic_service)
    index = Mock(return_value={"ok": True, "chunks_indexed": 2})
    monkeypatch.setattr(indexer, "index_fulltext_corpus", index)
    analysis = {
        "core": {"audit": {"facts_count": 2}, "entities": [
            {"entity_id": "accepted", "kind": "lock", "statement": "Stabilité imprévisible",
             "evidences": [{"evidence_id": "ev1", "document_id": "doc", "document_name": "section.txt",
                            "quote": "Les résultats sont instables", "quote_verified": True}],
             "metadata": {"qualification_status": "VALID_LOCK", "qualification_reason": "Essai documenté"}},
            {"entity_id": "rejected", "kind": "lock", "statement": "Délai court",
             "evidences": [], "metadata": {"qualification_status": "ENGINEERING_DIFFICULTY"}},
        ]},
        "project_frascati": {"summary": "Incertitude documentée", "criteria": []},
        "lock_families": {"families": []},
    }
    runtime = Mock()
    runtime.run.return_value.to_dict.return_value = analysis
    build = Mock(return_value=runtime)
    monkeypatch.setattr(full_runtime, "build_full_runtime", build)
    project = SimpleNamespace(id=9, organisme="test", project_name="Projet", year="2026", domain_label="Logiciels")
    request = ImprovementRequest(
        instruction="Analyser la section", full_text="Contexte local. Les résultats sont instables. Autre contexte.",
        target_text="Les résultats sont instables.", target_scope=TargetScope.SECTION,
    )
    return store, index, runtime, build, project, request, analysis


def test_scoped_diagnostic_uses_v2_and_only_current_input(scoped_run):
    store, index, runtime, build, project, request, analysis = scoped_run
    context, execution = scoped._run_scoped_diagnostic(None, project, request)
    assert execution["executed"] and not context["project_raw_documents_used"]
    assert context["scoped_engine"] == "ennodiagnostic_v2"
    assert len(context["verrous"]) == 1
    assert context["verrous"][0]["id"] == "scope-accepted"
    assert context["verrous"][0]["source_json"]["evidence_ids"] == ["ev1"]
    assert context["domain_detection"]["display_label"] == "Logiciels"
    inputs = runtime.run.call_args.args[0]
    assert inputs[0].text == request.target_text
    assert len(inputs) == 2 and all(d.declared_mode == "pre_cir" for d in inputs)
    assert all(d.metadata["source_path"].startswith("ennoamel://scope/") for d in inputs)
    persisted = [json.loads(p.read_text("utf-8")) for p in
                 (store.documents_processed_dir / "fulltext_rag_v1").glob("*.json") if p.name != "manifest.json"]
    assert {r["document_id"] for r in persisted} == {d.document_id for d in inputs}
    assert build.call_args.kwargs["fast_cache_dir"] == str(store.project_dir / "analysis_cache/global_fast")
    index.assert_called_once()


def test_scoped_cache_skips_all_analysis_and_index_calls(scoped_run):
    _store, index, runtime, _build, project, request, _analysis = scoped_run
    scoped._run_scoped_diagnostic(None, project, request)
    context, execution = scoped._run_scoped_diagnostic(None, project, request)
    assert context["cache_hit"] and not execution["executed"]
    runtime.run.assert_called_once()
    index.assert_called_once()


def test_text_change_invalidates_scoped_cache(scoped_run):
    _store, index, runtime, _build, project, request, _analysis = scoped_run
    scoped._run_scoped_diagnostic(None, project, request)
    changed = request.model_copy(update={"target_text": "Une autre section présente une difficulté."})
    _context, execution = scoped._run_scoped_diagnostic(None, project, changed)
    assert execution["executed"] and runtime.run.call_count == 2


def test_empty_qualified_locks_never_promotes_rejected_candidate(scoped_run):
    _store, _index, _runtime, _build, project, request, analysis = scoped_run
    analysis["core"]["entities"] = [analysis["core"]["entities"][1]]
    context, execution = scoped._run_scoped_diagnostic(None, project, request, allow_empty=True)
    assert context["completed"] and context["verrous"] == []
    assert context["status"] == "scoped_no_lock" and execution["executed"]


def test_failed_v2_scoped_run_has_no_v1_fallback(scoped_run):
    _store, _index, runtime, _build, project, request, _analysis = scoped_run
    runtime.run.side_effect = RuntimeError("V2 unavailable")
    with pytest.raises(scoped.DiagnosticOrchestrationError, match="V2 unavailable"):
        scoped._run_scoped_diagnostic(None, project, request)
