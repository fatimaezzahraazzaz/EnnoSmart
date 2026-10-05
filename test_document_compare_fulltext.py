from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend_api"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from modules.DOCUMENT_COMPARE import document_compare
from routers.diagnostic import _resolve_project_fulltext_file


def _write_fulltext_document(
    corpus_dir: Path,
    *,
    index: int,
    document: str,
    text: str,
) -> Path:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    path = corpus_dir / f"{index:03d}_{Path(document).stem}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "full_document_corpus_v1",
                "document": document,
                "document_id": f"doc_{index}",
                "source_path": str(corpus_dir.parent.parent / "raw" / document),
                "extension": Path(document).suffix,
                "text": text,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _build_corpus(tmp_path: Path) -> tuple[Path, Path, Path]:
    corpus_dir = tmp_path / "documents" / "processed" / "fulltext_rag_v1"
    common = (
        "[SECTION : Objectif]\n\n"
        "Le prototype doit maintenir une pression stable pendant les essais.\n\n"
        "[SECTION : Résultats]\n\n"
    )
    path_a = _write_fulltext_document(
        corpus_dir,
        index=1,
        document="Etude_thermique_v1.docx",
        text=common + "La température mesurée est de 42 °C et le débit atteint 18 m3/h.",
    )
    path_b = _write_fulltext_document(
        corpus_dir,
        index=2,
        document="Etude_thermique_v2.docx",
        text=common + "La température mesurée est de 47 °C et le débit atteint 20 m3/h.",
    )
    _write_fulltext_document(
        corpus_dir,
        index=3,
        document="Budget_commercial.xlsx",
        text="[SECTION : Budget]\n\nPrévisions commerciales et calendrier de facturation.",
    )
    (corpus_dir / "manifest.json").write_text(
        json.dumps({"documents_count": 3}),
        encoding="utf-8",
    )
    return corpus_dir, path_a, path_b


def test_fulltext_pair_discovery_uses_document_metadata(tmp_path: Path):
    corpus_dir, _, _ = _build_corpus(tmp_path)
    output_dir = tmp_path / "document_compare"

    index = document_compare.auto_compare_fulltext_pairs(
        str(corpus_dir),
        str(output_dir),
        min_similarity=0.70,
        include_medium=True,
        force=True,
    )

    assert index["ok"] is True
    assert index["input_policy"] == "fulltext_corpus_v2"
    assert index["nlp_result_used"] is False
    assert index["pairs_count"] == 1
    pair = index["pairs"][0]
    assert pair["name_a"] == "Etude_thermique_v1.docx"
    assert pair["name_b"] == "Etude_thermique_v2.docx"
    assert Path(pair["file_a"]).parent == corpus_dir
    assert Path(pair["file_b"]).parent == corpus_dir
    assert (output_dir / "auto_compare_index.json").is_file()


def test_document_comparison_reads_fulltext_without_reextracting(tmp_path: Path):
    corpus_dir, path_a, path_b = _build_corpus(tmp_path)
    output_dir = tmp_path / "reports"

    report = document_compare.compare_pair_to_report(
        str(path_a),
        str(path_b),
        str(output_dir),
        force=True,
    )

    assert report["ok"] is True
    assert report["input_policy"] == "fulltext_corpus_v2"
    assert report["doc_a"]["loader"] == "fulltext_rag_v1"
    assert report["doc_b"]["loader"] == "fulltext_rag_v1"
    assert report["doc_a"]["document"] == "Etude_thermique_v1.docx"
    assert report["doc_b"]["document"] == "Etude_thermique_v2.docx"
    assert report["summary"]["different_count"] >= 1
    assert Path(report["_report_path"]).is_file()
    assert corpus_dir in path_a.parents and corpus_dir in path_b.parents


def test_backend_restricts_comparison_to_project_fulltext(tmp_path: Path):
    corpus_dir, path_a, _ = _build_corpus(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"text": "hors projet"}), encoding="utf-8")

    assert _resolve_project_fulltext_file(corpus_dir, str(path_a)) == path_a.resolve()
    with pytest.raises(HTTPException) as exc_info:
        _resolve_project_fulltext_file(corpus_dir, str(outside))
    assert exc_info.value.status_code == 400
