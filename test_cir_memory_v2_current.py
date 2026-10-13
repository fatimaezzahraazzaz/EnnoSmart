from __future__ import annotations

import sys
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend_api"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from modules.CIR_MEMORY import cir_memory


def _current_v2_verrou() -> dict:
    entity = {
        "entity_id": "lock_v2_1",
        "kind": "lock",
        "statement": "La stabilité du modèle varie fortement selon le corpus et la configuration.",
        "evidences": [
            {
                "evidence_id": "ev_1",
                "document_id": "doc_1",
                "document_name": "rapport_technique.docx",
                "source_path": "documents/raw/rapport_technique.docx",
                "chunk_id": "chunk_1",
                "quote": "Les performances varient fortement entre les corpus testés.",
                "char_start": 120,
                "char_end": 181,
            }
        ],
        "metadata": {
            "qualification_status": "NEEDS_REFORMULATION",
            "qualification_reason": "L'incertitude est documentée mais l'énoncé doit être précisé.",
            "suggested_statement": "Maîtriser la stabilité inter-corpus du modèle malgré les variations de configuration.",
        },
    }
    return {
        "id": 41,
        "title": entity["metadata"]["suggested_statement"],
        "consultant_status": "en_attente",
        "source_json": {
            "engine": "ennodiagnostic_v2",
            "entity_id": "lock_v2_1",
            "qualification_status": "NEEDS_REFORMULATION",
            "v2_entity": entity,
        },
    }


def _previous_item() -> dict:
    return {
        "id": "previous_2025_1",
        "role": "verrou",
        "text": "La robustesse du modèle variait selon les jeux de données et les conditions de test.",
        "section_title": "Robustesse inter-jeux de données",
        "previous_source": "cir_final_previous",
        "previous_cir_sections_count": 4,
    }


def test_v2_verrou_keeps_entity_status_and_documentary_evidence():
    items = cir_memory.grouped_verrous_to_current_items([_current_v2_verrou()])

    assert len(items) == 1
    item = items[0]
    assert item["source_contract"] == "ennodiagnostic_v2"
    assert item["pack_key"] == "v2_entities"
    assert item["section_key"] == "v2_entities"
    assert item["entity_id"] == "lock_v2_1"
    assert item["qualification_status"] == "NEEDS_REFORMULATION"
    assert item["section_title"].startswith("Maîtriser la stabilité inter-corpus")
    assert item["document"] == "rapport_technique.docx"
    assert item["source_path"] == "documents/raw/rapport_technique.docx"
    assert item["primary_evidence"]["evidence_id"] == "ev_1"
    assert item["primary_evidence"]["char_start"] == 120
    assert item["primary_evidence"]["char_end"] == 181


def test_v2_comparison_never_resolves_or_reads_current_nlp_result(tmp_path: Path):
    report_path = tmp_path / "cir_memory_comparison_report.json"

    with (
        patch.object(
            cir_memory,
            "resolve_current_nlp_result_path",
            side_effect=AssertionError("Le V2 ne doit pas résoudre nlp_result.json"),
        ),
        patch.object(cir_memory, "comparison_report_path", return_value=report_path),
    ):
        report = cir_memory.compare_current_raw_with_cir_memory(
            organisme="Organisation test",
            project="Projet test",
            year="2026",
            current_verrous=[_current_v2_verrou()],
            previous_memory=(["2025"], [_previous_item()]),
            top_k=1,
            shortlist_size=5,
        )

    assert report["ok"] is True
    assert report["has_previous_cir"] is True
    assert report["nlp_result_path"] is None
    assert report["current_input_policy"] == "diagnostic_v2_postgresql_verrous"
    assert report["current_pack_source"] == "diagnostic_v2_postgresql_verrous"
    assert report["summary"]["fallback_to_nlp_role_verrou"] is False
    assert report["comparison_status"] == "completed"
    assert report["summary"]["current_verrous_count"] == 1
    assert len(report["verrou_comparisons"]) == 1
    assert report_path.is_file()


def test_explicit_empty_v2_verrous_does_not_fallback_to_old_diagnostic(tmp_path: Path):
    report_path = tmp_path / "cir_memory_empty.json"

    with (
        patch.object(
            cir_memory,
            "resolve_current_nlp_result_path",
            side_effect=AssertionError("Aucun fallback NLP autorisé pour un résultat V2 explicite"),
        ),
        patch.object(
            cir_memory,
            "load_grouped_verrous_from_ennodiagnostic_report",
            side_effect=AssertionError("Un run V2 vide ne doit pas réutiliser un ancien rapport"),
        ),
        patch.object(cir_memory, "comparison_report_path", return_value=report_path),
    ):
        report = cir_memory.compare_current_raw_with_cir_memory(
            organisme="Organisation test",
            project="Projet test",
            year="2026",
            current_verrous=[],
            previous_memory=([], []),
        )

    assert report["ok"] is True
    assert report["nlp_result_path"] is None
    assert report["summary"]["current_verrous_count"] == 0
    assert report["summary"]["current_verrous_source"] == "diagnostic_v2_postgresql_verrous_empty"
    assert report["summary"]["fallback_to_nlp_role_verrou"] is False
    assert report["comparison_status"] == "no_previous_cir"


def test_previous_cir_without_current_locks_is_completed_not_missing(tmp_path: Path):
    with patch.object(cir_memory, "comparison_report_path", return_value=tmp_path / "empty.json"):
        report = cir_memory.compare_current_raw_with_cir_memory(
            "Organisation test", "Projet test", "2026", current_verrous=[],
            previous_memory=(["2025"], [_previous_item()]),
        )
    assert report["has_previous_cir"] is True
    assert report["comparison_status"] == "no_current_locks"
    assert report["verrou_comparisons"] == []


def test_discovery_reads_exact_memory_passages_without_local_cir_directory(tmp_path: Path):
    chunks = tmp_path / "memory" / "chunks"
    chunks.mkdir(parents=True)
    text = "La robustesse du modèle variait selon les jeux de données et les conditions de test."
    records = [
        {"id": "p1", "text": text, "metadata": {
            "organisme": "Organisation test", "project": "Projet test", "year": "2025",
            "role": "verrou", "document": "cir_2025.docx", "memory_class": "knowledge",
        }},
        {"id": "style1", "text": "Style sans preuve projet. " * 5, "metadata": {
            "organisme": "Organisation test", "project": "Projet test", "year": "2025",
            "role": "style", "memory_class": "style",
        }},
    ]
    (chunks / "source.chunks_v2.json").write_text(json.dumps(records), encoding="utf-8")
    with (
        patch.object(cir_memory, "EXPERIENCE_MEMORY_V2_DIR", tmp_path / "memory"),
        patch.object(cir_memory, "STORAGE_DIR", tmp_path / "no_local_upload"),
    ):
        available = cir_memory.list_available_previous_cirs("Organisation test", "Projet test", "2026")
        years, passages = cir_memory.load_previous_cir_memory_items("Organisation test", "Projet test", "2026")
        assert available[0]["year"] == "2025"
        assert available[0]["source"] == "experience_memory_v2"
        assert available[0]["passages_count"] == len(passages) == 1
        assert years == ["2025"]
        assert passages[0]["text"] == text
        assert cir_memory.list_available_previous_cirs("Autre organisation", "Projet test", "2026") == []
        assert cir_memory.list_available_previous_cirs("Organisation test", "Autre projet", "2026") == []
        assert cir_memory.list_available_previous_cirs("Organisation test", "Projet test", "2025") == []


def test_catalog_and_summary_cards_alone_do_not_claim_available_evidence(tmp_path: Path):
    (tmp_path / "cards").mkdir()
    (tmp_path / "catalog_v2.json").write_text(json.dumps({"projects": ["Org::Projet::::2025"]}))
    (tmp_path / "cards" / "card.cards.json").write_text(json.dumps([{
        "organisme": "Org", "project": "Projet", "year": "2025",
        "card_type": "verrou", "summary": "Résumé artificiellement abrégé. " * 5,
    }]))
    with (
        patch.object(cir_memory, "EXPERIENCE_MEMORY_V2_DIR", tmp_path),
        patch.object(cir_memory, "STORAGE_DIR", tmp_path / "missing"),
    ):
        assert cir_memory.list_available_previous_cirs("Org", "Projet", "2026") == []


def test_local_manual_previous_cir_still_discovered(tmp_path: Path):
    with (
        patch.object(cir_memory, "EXPERIENCE_MEMORY_V2_DIR", tmp_path / "missing"),
        patch.object(cir_memory, "STORAGE_DIR", tmp_path),
    ):
        path = cir_memory.cir_final_report_path("Org", "Projet", "2025")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"items": [_previous_item()]}))
        available = cir_memory.list_available_previous_cirs("Org", "Projet", "2026")
    assert available[0]["year"] == "2025"
    assert available[0]["source"] == "local_cir_final"


def _project():
    return SimpleNamespace(organisme="Organisation test", project_name="Projet test", year="2026", subproject_name=None)


def _v2_report():
    entity = _current_v2_verrou()["source_json"]["v2_entity"]
    rejected = {**entity, "entity_id": "rejected", "metadata": {"qualification_status": "REQUIREMENT_ONLY"}}
    return {"engine": "ennodiagnostic_v2", "diagnostic_v2": {"core": {"entities": [entity, rejected]}}}


def test_automatic_comparison_uses_only_current_qualified_entities_and_exact_evidence():
    from services import diagnostic_service
    with patch.object(cir_memory, "compare_current_raw_with_cir_memory", return_value={"ok": True, "comparison_status": "completed"}) as compare:
        diagnostic_service.run_previous_cir_comparison(_project(), _v2_report())
    submitted = compare.call_args.kwargs["current_verrous"]
    assert len(submitted) == 1
    assert submitted[0]["qualification_status"] == "NEEDS_REFORMULATION"
    assert submitted[0]["source_json"]["v2_entity"]["evidences"][0]["quote"] == _current_v2_verrou()["source_json"]["v2_entity"]["evidences"][0]["quote"]


def test_comparison_error_is_explicit_and_replaces_stale_saved_report(tmp_path: Path):
    from services import diagnostic_service
    path = tmp_path / "comparison.json"
    path.write_text('{"comparisons":[{"old":true}]}')
    with (
        patch.object(cir_memory, "compare_current_raw_with_cir_memory", side_effect=OSError("Mémoire inaccessible")),
        patch.object(cir_memory, "comparison_report_path", return_value=path),
    ):
        report = diagnostic_service.run_previous_cir_comparison(_project(), _v2_report())
    assert report["comparison_status"] == "error"
    assert report["error"] == "Mémoire inaccessible"
    assert json.loads(path.read_text())["comparisons"] == []


def test_diagnostic_runs_comparison_automatically_and_persists_business_view():
    from services import diagnostic_service, ennodiagnostic_v2_service
    comparison = {"ok": True, "comparison_status": "completed", "has_previous_cir": True, "verrou_comparisons": [{"test": True}]}
    with (
        patch.object(ennodiagnostic_v2_service, "run_ennodiagnostic_v2", return_value=_v2_report()),
        patch.object(diagnostic_service, "run_ai_detector_if_enabled", return_value={"skipped": True}),
        patch.object(diagnostic_service, "run_previous_cir_comparison", return_value=comparison) as compare,
    ):
        report = diagnostic_service.run_true_ennodiagnostic_agent(_project())
    assert compare.call_count == 1
    assert report["cir_memory_report"] == comparison
    assert report["independent_modules"]["cir_memory"] == "completed"
    snapshot = diagnostic_service.extract_complete_diagnostic_snapshot(report)
    assert snapshot["cir_memory_report"]["comparison_status"] == "completed"
    assert diagnostic_service._compact_report_for_database(report, snapshot)["cir_memory_report"] == snapshot["cir_memory_report"]


def _project_fact(kind: str, index: int = 0):
    proof = {**_current_v2_verrou()["source_json"]["v2_entity"]["evidences"][0],
             "quote_verified": True, "evidence_id": f"ev_{kind}_{index}"}
    return {"id": f"{kind}_{index}", "kind": kind, "source_role": "project_evidence",
            "statement": "La stabilité du modèle varie fortement selon le corpus et la configuration.",
            "temporal_status": "performed", "evidences": [proof]}


def test_all_seven_categories_are_compared_without_changing_lock_summary(tmp_path: Path):
    kinds = ["objective", "method", "demarche", "parameter", "metric", "result"]
    with patch.object(cir_memory, "comparison_report_path", return_value=tmp_path / "all.json"):
        report = cir_memory.compare_current_raw_with_cir_memory(
            "Org", "Projet", "2026", current_verrous=[_current_v2_verrou()],
            current_project_facts=[_project_fact(k) for k in kinds],
            previous_memory=(["2025"], [_previous_item()]),
        )
    assert len(report["comparisons"]) == 7
    assert len(report["verrou_comparisons"]) == 1
    assert report["summary"]["current_verrous_count"] == 1
    assert set(report["summary"]["current_items_by_kind"].values()) == {1}
    saved = json.loads((tmp_path / "all.json").read_text())
    assert len(saved["comparisons"]) == 7


def test_non_lock_facts_are_compared_even_when_no_lock_was_retained(tmp_path: Path):
    with patch.object(cir_memory, "comparison_report_path", return_value=tmp_path / "facts.json"):
        report = cir_memory.compare_current_raw_with_cir_memory(
            "Org", "Projet", "2026", current_verrous=[],
            current_project_facts=[_project_fact("demarche"), _project_fact("result")],
            previous_memory=(["2025"], [_previous_item()]),
        )
    assert report["comparison_status"] == "completed"
    assert len(report["comparisons"]) == 2
    assert report["verrou_comparisons"] == []


def test_method_cannot_match_a_result_only_because_words_are_identical():
    method = cir_memory.project_facts_to_current_items([_project_fact("method")])[0]
    result = {**_previous_item(), "role": "resultat", "text": method["text"]}
    comparison = cir_memory.compare_one(method, [result])
    assert comparison["best_match"] is None
    assert comparison["decision"]["status"] == "new_or_not_found"


def test_project_fact_conversion_rejects_literature_and_unverified_quotes():
    good = _project_fact("result")
    literature = {**_project_fact("result", 1), "source_role": "literature"}
    invalid = _project_fact("result", 2)
    invalid["evidences"][0]["quote_verified"] = False
    items = cir_memory.project_facts_to_current_items([good, literature, invalid])
    assert len(items) == 1
    assert items[0]["evidences"] == good["evidences"]
    assert items[0]["evidence_ids"] == ["ev_result_0"]


def test_project_lists_resolve_existing_verified_proofs_without_inventing_facts():
    from services import diagnostic_service
    fact = _project_fact("result")
    source = {"item_id": "result_item", "kind": "result", "evidence": fact["evidences"][0], "metadata": {"source_role": "project_evidence"}}
    report = {"engine": "ennodiagnostic_v2", "extractions": [source], "project_synthesis": {"results": [
        {"text": "Résultat du projet", "evidence_ids": ["ev_result_0"], "source_role": "project_evidence"},
        {"text": "Résultat d’article", "evidence_ids": ["ev_result_0"], "source_role": "literature"},
        {"text": "Sans preuve", "evidence_ids": ["invented"], "source_role": "project_evidence"},
    ]}}
    items = diagnostic_service._extract_v2_project_facts_for_cir_memory(report)
    assert len(items) == 1
    assert items[0]["statement"] == "Résultat du projet"
    assert items[0]["evidences"] == fact["evidences"]


def test_non_lock_comparisons_are_kept_in_compact_persisted_report():
    from services import diagnostic_service
    comparison = {"ok": True, "comparison_status": "completed", "has_previous_cir": True,
                  "comparisons": [{"current_item": {"kind": "result"}}], "verrou_comparisons": []}
    report = {**_v2_report(), "cir_memory_report": comparison}
    snapshot = diagnostic_service.extract_complete_diagnostic_snapshot(report)
    saved = diagnostic_service._compact_report_for_database(report, snapshot)
    assert saved["cir_memory_report"]["comparisons"] == comparison["comparisons"]
