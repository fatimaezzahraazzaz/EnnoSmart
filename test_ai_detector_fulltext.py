from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend_api"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from modules.AI_DETECTOR import ai_detector
from services import diagnostic_service


def _write_fulltext_corpus(root: Path) -> tuple[Path, dict[str, str]]:
    corpus_dir = root / "documents" / "processed" / "fulltext_rag_v1"
    corpus_dir.mkdir(parents=True)
    documents = {
        "rapport_a.docx": (
            "Dans le cadre de cette étude, il est important de noter que "
            "plusieurs essais ont été réalisés à 20 °C puis à 45 °C. "
            "Les résultats montrent que le débit varie selon la pression. "
        ) * 8,
        "rapport_b.pdf": (
            "Le prototype a été testé sur douze configurations. "
            "Chaque mesure a été répétée trois fois afin de comparer les écarts. "
            "La température et la pression ont été enregistrées pendant l'essai. "
        ) * 7,
    }
    records = []
    for index, (document, text) in enumerate(documents.items(), start=1):
        payload = {
            "schema_version": "full_document_corpus_v1",
            "document": document,
            "document_id": f"doc_{index}",
            "source_path": str(root / "raw" / document),
            "extension": Path(document).suffix,
            "content_origin": "raw_document",
            "text": text,
        }
        path = corpus_dir / f"{index:03d}_{Path(document).stem}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        records.append({"document": document, "path": str(path)})

    (corpus_dir / "manifest.json").write_text(
        json.dumps({"documents_count": len(records), "documents": records}),
        encoding="utf-8",
    )
    (corpus_dir / "nlp_result.json").write_text(
        json.dumps({"text": "Ce faux NLP ne doit jamais être analysé." * 20}),
        encoding="utf-8",
    )
    return corpus_dir, documents


def test_fulltext_loader_ignores_nlp_and_keeps_traceability(tmp_path: Path):
    corpus_dir, documents = _write_fulltext_corpus(tmp_path)

    passages = ai_detector.extract_passages_from_fulltext_corpus(
        corpus_dir,
        min_chars=40,
        max_chars=240,
    )

    assert len(passages) > len(documents)
    assert {item["document"] for item in passages} == set(documents)
    assert all(item["source"] == "fulltext_corpus_v1" for item in passages)
    assert all(item["pack_key"] == "fulltext_rag_v1" for item in passages)
    assert all(item["document_id"] for item in passages)
    assert all(item["source_path"] for item in passages)
    assert all(item["char_end"] > item["char_start"] >= 0 for item in passages)
    assert not any("faux NLP" in item["text"] for item in passages)


def test_ai_report_is_built_from_fulltext_only(tmp_path: Path):
    corpus_dir, documents = _write_fulltext_corpus(tmp_path)
    output_path = tmp_path / "ai_detection_report.json"

    with (
        patch.object(ai_detector, "load_env", return_value=None),
        patch.object(
            ai_detector,
            "model_ai_score",
            return_value={"score": None, "label": "model_unavailable"},
        ),
        patch.dict(
            "os.environ",
            {
                "AI_DETECTOR_MIN_CHARS": "40",
                "AI_DETECTOR_MAX_CHARS": "240",
            },
        ),
    ):
        report = ai_detector.run_ai_detection_on_fulltext_corpus(
            corpus_dir,
            output_path,
        )

    assert output_path.is_file()
    assert report["ok"] is True
    assert report["source"] == "fulltext_rag_v1"
    assert report["loader_report"]["nlp_result_used"] is False
    assert report["loader_report"]["rag_chunks_used"] is False
    assert set(report["summary"]["documents"]) == set(documents)
    assert report["summary"]["passages_count"] == len(report["passages"])
    assert report["ai_detection"]["total_passages_analyzed"] > 0
    assert all(item["document_id"] for item in report["passages"])


def test_backend_ai_adapter_uses_project_fulltext_corpus(tmp_path: Path):
    year_root = tmp_path / "year_2026"
    corpus_dir, _ = _write_fulltext_corpus(year_root)
    store = SimpleNamespace(
        project_root_dir=tmp_path,
        project_dir=year_root,
        documents_processed_dir=year_root / "documents" / "processed",
        diagnostics_dir=year_root / "diagnostics",
    )
    project = SimpleNamespace(
        id=7,
        organisme="Organisation test",
        project_name="Projet test",
        subproject_name=None,
        year="2026",
    )

    with (
        patch.object(diagnostic_service, "get_project_store", return_value=store),
        patch.object(ai_detector, "load_env", return_value=None),
        patch.object(
            ai_detector,
            "model_ai_score",
            return_value={"score": None, "label": "model_unavailable"},
        ),
        patch.dict(
            "os.environ",
            {
                "ENNOSMART_RUN_AI_DETECTOR": "1",
                "AI_DETECTOR_MIN_CHARS": "40",
                "AI_DETECTOR_MAX_CHARS": "240",
            },
        ),
    ):
        report = diagnostic_service.run_ai_detector_if_enabled(
            project,
            year_root,
        )

    assert report["ok"] is True
    assert report["source"] == "fulltext_rag_v1"
    assert report["backend_adapter"]["policy"] == "direct_fulltext_corpus_v2"
    assert Path(report["backend_adapter"]["fulltext_corpus_dir"]) == corpus_dir
    assert report["loader_report"]["nlp_result_used"] is False
    assert (year_root / "diagnostics" / "ai_detection_report.json").is_file()
