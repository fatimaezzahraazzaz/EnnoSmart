from __future__ import annotations

import json
import sys
import pytest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend_api"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from modules.RAG.config import FULLTEXT_CHAT_COLLECTION_SUFFIX
from modules.RAG.diagnostic_chat_service import DiagnosticRAGChatService


def _service_without_constructor(tmp_path: Path) -> DiagnosticRAGChatService:
    processed = tmp_path / "documents" / "processed"
    nlp_dir = tmp_path / "nlp"
    processed.mkdir(parents=True, exist_ok=True)
    nlp_dir.mkdir(parents=True, exist_ok=True)

    service = DiagnosticRAGChatService.__new__(DiagnosticRAGChatService)
    service.store = SimpleNamespace(
        documents_processed_dir=processed,
        nlp_dir=nlp_dir,
    )
    service.base_collection_name = "ennosmart_org_project_2026"
    service.raw_collection_name = service.base_collection_name + FULLTEXT_CHAT_COLLECTION_SUFFIX
    return service


def _write_fulltext(service: DiagnosticRAGChatService) -> Path:
    corpus = service.store.documents_processed_dir / "fulltext_rag_v1"
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / "001_rapport.json").write_text(
        json.dumps(
            {
                "document": "rapport.docx",
                "document_id": "doc_1",
                "text": "Preuve documentaire technique " * 30,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (corpus / "manifest.json").write_text("{}", encoding="utf-8")
    return corpus


def test_v2_chat_uses_only_fulltext_collection_when_corpus_exists(tmp_path: Path):
    service = _service_without_constructor(tmp_path)
    _write_fulltext(service)

    assert service._fulltext_available() is True
    assert service._active_document_collections() == [service.raw_collection_name]
    assert service.base_collection_name not in service._active_document_collections()


def test_legacy_chat_keeps_old_collection_fallback_without_fulltext(tmp_path: Path):
    service = _service_without_constructor(tmp_path)

    assert service._fulltext_available() is False
    assert service._active_document_collections() == [
        service.base_collection_name,
        service.raw_collection_name,
    ]


def test_v2_chat_fingerprint_is_independent_from_legacy_nlp(tmp_path: Path):
    service = _service_without_constructor(tmp_path)
    _write_fulltext(service)

    before = service._raw_fingerprint()
    (service.store.nlp_dir / "nlp_result.json").write_text(
        json.dumps({"legacy": "ancien NLP" * 100}),
        encoding="utf-8",
    )
    after = service._raw_fingerprint()

    assert before == after


@pytest.mark.parametrize("stale", [False, True])
def test_status_reads_fulltext_manifest(tmp_path, monkeypatch, stale):
    from routers import diagnostic_chat as router
    from modules.RAG.indexer import FULLTEXT_INDEX_MANIFEST, FULLTEXT_INDEX_VERSION
    from modules.RAG.config import EMBEDDING_MODEL_NAME

    service = _service_without_constructor(tmp_path)
    _write_fulltext(service)
    store = service.store
    store.collection_name = service.base_collection_name
    store.rag_dir = tmp_path / "rag"
    store.rag_dir.mkdir()
    store.chroma_dir = tmp_path / "chroma"
    store.ensure = lambda: store
    (store.rag_dir / FULLTEXT_INDEX_MANIFEST).write_text(json.dumps({
        "version": FULLTEXT_INDEX_VERSION,
        "fingerprint": "obsolete" if stale else service._raw_fingerprint(),
        "collection_name": service.raw_collection_name,
        "embedding_model": EMBEDDING_MODEL_NAME,
    }), encoding="utf-8")
    monkeypatch.setattr(router, "ProjectStore", lambda **kwargs: store)
    monkeypatch.setattr(router, "create_chroma_client", lambda *args: SimpleNamespace(
        get_collection=lambda name: SimpleNamespace(count=lambda: 12)))
    monkeypatch.setattr(router, "chroma_connection_info", lambda *args: {})
    project = SimpleNamespace(organisme="org", project_name="project", year=2026)
    result = router._project_collection_status(project)
    assert result["fulltext_index_current"] is (not stale)
    assert result["chunks_count"] == (0 if stale else 12)
    assert result["index_source"] == "fulltext_rag_v1"


def test_chat_reuses_v2_indexer_without_legacy_rebuild(tmp_path, monkeypatch):
    from modules.RAG import diagnostic_chat_service as chat
    service = _service_without_constructor(tmp_path)
    _write_fulltext(service)
    service.store.organisme_name = "org"
    service.store.project_name = "project"
    service.store.subproject_name = "sub"
    service.store.year = 2026
    calls = []
    monkeypatch.setattr(chat, "index_fulltext_corpus", lambda **kw: calls.append(kw) or {"reused": True})
    service._build_raw_chunks = lambda: pytest.fail("Legacy chunk builder must not run")
    assert service.ensure_raw_index() == {"reused": True}
    assert calls == [{"organisme": "org", "project": "project", "subproject": "sub", "year": 2026}]


def _chat_with_mock_retrieval(tmp_path, monkeypatch):
    service = _service_without_constructor(tmp_path)
    documents = [
        {"document_id": str(i), "document_name": f"document_{i}.docx"}
        for i in range(1, 4)
    ]
    service.available_documents = lambda: documents
    service.ensure_raw_index = lambda: {"reused": True}
    queried = []
    def retrieve(question, history, top_k, document_scope):
        queried.append(document_scope)
        selected = documents if document_scope is None else [document_scope]
        return ([{
            "id": f"chunk_{d['document_id']}",
            "text": f"Le document {d['document_id']} décrit des essais techniques et leurs observations mesurées.",
            "metadata": {"document": d["document_name"], "document_id": d["document_id"],
                         "source_kind": "client_raw", "passage_id": f"p{d['document_id']}"},
        } for d in selected], [question])
    service._retrieve_project_sources_with_coverage = retrieve
    service._experimental_fulltext_source = lambda **kwargs: None
    service._prioritize_experimental_context = lambda question, sources: sources
    service._retrieve_diagnostic_sources = lambda *args, **kwargs: pytest.fail("No diagnostic source in explicit scope")
    prompts = []
    service.llm = SimpleNamespace(
        generate=lambda prompt, **kwargs: prompts.append(prompt) or "Les essais sont documentés [E1, E2].",
        get_last_generation_meta=lambda: {"model": "mock"},
    )
    service._align_and_renumber_citations = lambda answer, evidence, **kwargs: (answer, list(evidence), {})
    return service, documents, queried, prompts


def test_multiple_documents_use_only_selected_sources(tmp_path, monkeypatch):
    service, docs, queried, prompts = _chat_with_mock_retrieval(tmp_path, monkeypatch)
    result = service.answer(question="Quels essais sont décrits ?", history=[], diagnostic_payload={},
                            document_scopes=[docs[0], docs[1], docs[0]])
    assert [scope["document_id"] for scope in queried] == ["1", "2"]
    assert {source["document_id"] for source in result["sources"]} == {"1", "2"}
    assert result["retrieval"]["multi_document_expected_count"] == 2
    assert result["retrieval"]["multi_document_missing_documents"] == []
    assert "document_3.docx" not in prompts[0]
    assert "EXCLUSIVEMENT sur les documents sélectionnés" in prompts[0]
    assert {source["passage_id"] for source in result["sources"]} == {"p1", "p2"}


@pytest.mark.parametrize("multiple_contract", [False, True])
def test_single_document_and_legacy_contract(tmp_path, monkeypatch, multiple_contract):
    service, docs, queried, prompts = _chat_with_mock_retrieval(tmp_path, monkeypatch)
    scope = {"document_scopes": [docs[1]]} if multiple_contract else {"document_scope": docs[1]}
    result = service.answer(question="Quels essais ?", history=[], diagnostic_payload={}, **scope)
    assert {source["document_id"] for source in result["sources"]} == {"2"}
    assert "document_1.docx" not in prompts[0]


def test_unknown_selected_document_does_not_expand_to_all(tmp_path, monkeypatch):
    service, docs, queried, prompts = _chat_with_mock_retrieval(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="absent ou ambigu"):
        service.answer(question="Quels essais ?", history=[], diagnostic_payload={},
                       document_scopes=[{"document_id": "999", "document_name": docs[0]["document_name"]}])
    assert queried == []
    assert prompts == []


def test_all_documents_remain_available(tmp_path, monkeypatch):
    service, docs, queried, prompts = _chat_with_mock_retrieval(tmp_path, monkeypatch)
    result = service.answer(question="Quels essais ?", history=[], diagnostic_payload={})
    assert {source["document_id"] for source in result["sources"]} == {"1", "2", "3"}


def test_project_question_does_not_auto_scope_to_partially_matching_filename(tmp_path):
    service = _service_without_constructor(tmp_path)
    service.available_documents = lambda: [
        {"document_id": "1", "document_name": "Résultats X.xlsx"},
        {"document_id": "2", "document_name": "Démarche expérimentale.docx"},
    ]
    assert service._resolve_document_scope(
        "Quels sont les objectifs, méthodes et résultats du projet X ?", None,
    ) is None
    assert service._resolve_document_scope("Quels sont les résultats du projet X ?", None) is None


def test_explicit_document_name_keeps_document_scope(tmp_path):
    service = _service_without_constructor(tmp_path)
    service.available_documents = lambda: [
        {"document_id": "1", "document_name": "Résultats X.xlsx"},
        {"document_id": "2", "document_name": "Démarche expérimentale.docx"},
    ]
    assert service._resolve_document_scope(
        "Que contient le fichier Résultats X.xlsx ?", None,
    )["document_id"] == "1"
    assert service._resolve_document_scope(
        "Que contient ce document ?", {"document_id": "2", "document_name": "Démarche expérimentale.docx"},
    )["document_id"] == "2"
    assert service._resolve_document_scope(
        "Que contient le fichier Résultats X.xlsx du projet X ?", None,
    )["document_id"] == "1"
    assert service._resolve_document_scope("Résume tous les documents du projet X", None) is None


def test_result_table_cannot_claim_all_multi_question_needs():
    service = DiagnosticRAGChatService
    questions = [
        "objectif principal du document",
        "methode ou outil principal decrit dans le document",
        "resultat ou performance principale rapporte dans le document",
        "principal verrou ou incertitude identifie dans le document",
    ]
    evidence = [{
        "evidence_id": "E1", "excerpt": "Modèle | Stratégie | Compilability | Coverage\n"
        "Modèle A | Zero-shot Learning | 0.65 | 0.10",
        "passage_origin": "ambiguous_or_mixed", "coverage_unit_indices": [0, 1, 2, 3],
    }]
    plan = service._coverage_plan(questions, evidence, "Résume le projet X")
    assert [item["candidate_found"] for item in plan] == [False, True, True, False]


def test_missing_objective_expands_retrieval_to_other_project_document(tmp_path):
    service = _service_without_constructor(tmp_path)
    service._coverage_subquestions = lambda question: [
        "objectif principal du document", "resultat ou performance principale rapporte dans le document",
    ]
    result = {
        "id": "result", "text": "Stratégie | Compilability | Coverage\nZero-shot | 0.65 | 0.10",
        "metadata": {"document": "Résultats X.xlsx", "source_kind": "client_raw"},
    }
    objective = {
        "id": "objective", "text": "Le projet vise à générer des tests unitaires fiables.",
        "metadata": {"document": "Cadrage X.docx", "source_kind": "client_raw"},
    }
    calls = []
    def retrieve(question, history, *, top_k, document_scope):
        calls.append((question, top_k, document_scope))
        if "objectif" in question:
            return [result] if top_k == 7 else [objective, result]
        return [result]
    service._retrieve_with_local_neighbors = retrieve
    service._retrieve_project_sources = lambda *args, **kwargs: [result]
    sources, questions = service._retrieve_project_sources_with_coverage(
        "Objectif et résultat du projet X", [], top_k=8, document_scope=None,
    )
    assert any(top_k == 18 and scope is None for _, top_k, scope in calls)
    assert {item["id"] for item in sources} == {"objective", "result"}
    evidence = service._evidence_catalog(sources)
    assert all(item["candidate_found"] for item in service._coverage_plan(
        questions, evidence, "Objectif et résultat du projet X",
    ))


def test_external_result_is_not_project_result_support():
    source = {
        "text": "L'article rapporte une couverture mesurée de 80 %.",
        "metadata": {"role": "resultat", "source_kind": "client_raw"},
        "_passage_provenance": {"origin": "external_literature"},
    }
    assert not DiagnosticRAGChatService._coverage_passage_matches(
        source, "resultat ou performance rapporte", "Résultats du projet X",
    )


def test_article_passage_with_ambiguous_metadata_is_not_project_result():
    source = {
        "text": "L'étude a inclus 20 articles. Les résultats montrent une couverture de 80 %.",
        "metadata": {"source_kind": "client_raw"},
        "_passage_provenance": {"origin": "ambiguous_or_mixed"},
    }
    assert not DiagnosticRAGChatService._coverage_passage_matches(
        source, "resultat ou performance rapporte", "Résultats du projet X",
    )
    study_summary = {
        "text": "Une approche utilisée dans 14 études utilisant le few-shot améliore les tests.",
        "metadata": {"source_kind": "client_raw"},
        "_passage_provenance": {"origin": "ambiguous_or_mixed"},
    }
    assert not DiagnosticRAGChatService._coverage_passage_matches(
        study_summary, "methode ou outil principal decrit", "Méthodes du projet X",
    )


def test_planned_gain_is_not_an_observed_result():
    source = {
        "text": "Le projet vise à générer des tests. Gains de productivité envisagés.",
        "metadata": {"source_kind": "client_raw"},
    }
    assert not DiagnosticRAGChatService._coverage_passage_matches(
        source, "resultat ou performance rapporte", "Résultats du projet X",
    )


def test_metric_definition_and_model_size_are_not_measured_results():
    source = {
        "text": "Modèle Q2-7B. Métriques prévues : taux de compilation, couverture des lignes.",
        "metadata": {"source_kind": "client_raw"},
    }
    assert not DiagnosticRAGChatService._coverage_passage_matches(
        source, "resultat ou performance rapporte", "Résultats du projet X",
    )


def test_consultant_eligibility_discussion_is_not_project_method():
    source = {
        "text": "Nous avons besoin de traces écrites pour la cotation et l'éligibilité du dossier. "
        "Ce sont des comparaisons des méthodes de l'état de l'art.",
        "metadata": {"source_kind": "client_raw"},
        "_passage_provenance": {"origin": "ambiguous_or_mixed"},
    }
    assert not DiagnosticRAGChatService._coverage_passage_matches(
        source, "methode ou outil principal decrit", "Méthodes du projet X",
    )


def test_multi_part_result_can_recover_existing_measurement_table(tmp_path, monkeypatch):
    from modules.RAG import diagnostic_chat_service as chat
    service = _service_without_constructor(tmp_path)
    service._fulltext_available = lambda: True
    service._coverage_subquestions = lambda question: [
        "objectif principal du document", "resultat ou performance principale rapporte dans le document",
    ]
    objective = {
        "id": "objective", "text": "Le projet vise à générer des tests unitaires fiables.",
        "metadata": {"document": "Cadrage X.docx", "source_kind": "client_raw"},
    }
    table = {
        "id": "table", "text": "[TABLEAU] Modèle | Compilability | Coverage\nModèle A | 0.65 | 0.10",
        "metadata": {"document": "Mesures X.xlsx", "source_kind": "client_raw"},
    }
    service._retrieve_with_local_neighbors = lambda question, history, *, top_k, document_scope: (
        [objective] if "objectif" in question else []
    )
    service._retrieve_project_sources = lambda *args, **kwargs: []
    service._rank_and_dedupe_sources = lambda **kwargs: kwargs["candidates"]
    monkeypatch.setattr(chat, "fulltext_corpus_to_chunks", lambda store: [table])
    sources, _ = service._retrieve_project_sources_with_coverage(
        "Objectif et résultat du projet X", [], top_k=8, document_scope=None,
    )
    assert {item["id"] for item in sources} == {"objective", "table"}


def test_v2_synthesis_is_available_as_secondary_analysis():
    from modules.RAG.diagnostic_chat_service import _iter_diagnostic_sections
    result = list(_iter_diagnostic_sections({"diagnostic_v2": {
        "project_synthesis": {"objective": "Décrire les essais et leurs résultats documentés."},
    }}))
    assert any("project_synthesis" in title and "résultats documentés" in body for title, body in result)


def test_selected_document_coverage_is_balanced_before_catalog_limit(tmp_path):
    service = _service_without_constructor(tmp_path)
    docs = [{"document_id": str(i), "document_name": f"doc{i}.docx"} for i in range(7)]
    passages = [
        "Une expérimentation confronte plusieurs algorithmes et leurs performances observées.",
        "La modélisation formalise une architecture logicielle avec des contraintes particulières.",
        "Les ressources mémoire limitent les volumes exploitables dans cet environnement.",
        "Un protocole reproductible contrôle les mesures et les configurations choisies.",
    ]
    def retrieve(question, history, top_k, document_scope):
        return ([{"text": text, "metadata": {"document_id": document_scope["document_id"],
                                                "document": document_scope["document_name"]}}
                 for text in passages], [question])
    service._retrieve_project_sources_with_coverage = retrieve
    sources, _, report = service._retrieve_all_documents_with_coverage(
        question="Quels travaux ?", history=[], document_scopes=docs)
    assert len(sources) == 28
    evidence = service._evidence_catalog(sources, max_sources=20)
    assert {item["document_id"] for item in evidence} == {str(i) for i in range(7)}
    assert all(row["candidate_found"] for row in report)


def test_numeric_question_recovers_measurement_table_and_header(tmp_path, monkeypatch):
    from modules.RAG import diagnostic_chat_service as chat

    service = _service_without_constructor(tmp_path)
    service._fulltext_available = lambda: True
    document = "Benchmark des tests unitaires Java.docx"
    header = {
        "id": "header", "text": "Modèle | Prompting | Compilability | Line Coverage | Branch Coverage",
        "metadata": {"document": document, "document_id": "2", "source_kind": "client_raw",
                     "section_title": "Mesures", "paragraph_index": 0,
                     "char_start": 0, "char_end": 100},
    }
    measured = {
        "id": "measure", "text": "RAG | Model A | 59.24 | 18.21 | 17.31\nRAG | Model B | 58.76 | 18.11 | 17.22",
        "metadata": {"document": document, "document_id": "2", "source_kind": "client_raw",
                     "section_title": "Mesures", "paragraph_index": 1,
                     "char_start": 100, "char_end": 200},
    }
    unrelated = {
        "id": "other", "text": "RAG | Model C | 99.99 | 90.11 | 88.22",
        "metadata": {"document": "Autre projet.docx", "document_id": "999", "source_kind": "client_raw"},
    }
    monkeypatch.setattr(chat, "fulltext_corpus_to_chunks", lambda store: [header, measured, unrelated])
    question = "Quelles stratégies de prompting ont été testées en Java et quels résultats de compilabilité et couverture ont été obtenus ?"
    result = service._measured_table_sources(question, None)
    assert [item["id"] for item in result] == ["header", "measure"]
    assert result[1]["text"] == measured["text"]
    assert result[1]["metadata"]["verified_measurement_table"] is True
    assert chat._evidence_nature(result[1]) == "resultat_mesure"
    evidence = service._evidence_catalog(result)
    assert {item["document_id"] for item in evidence} == {"2"}
    assert any("59.24" in item["excerpt"] for item in evidence)
    assert service._measured_table_sources(question, {"document_id": "999", "document_name": "Autre projet.docx"}) == []
    assert service._measured_table_sources("Quel est l'objectif général de ce travail ?", None) == []


def test_numeric_recovery_does_not_claim_an_ambiguous_table_is_project_work(tmp_path, monkeypatch):
    from modules.RAG import diagnostic_chat_service as chat

    service = _service_without_constructor(tmp_path)
    service._fulltext_available = lambda: True
    document = "Benchmark de mesures.docx"
    chunks = [
        {"id": "h", "text": "Modèle | Stratégie | Compilability | Coverage",
         "metadata": {"document": document, "document_id": "4", "source_kind": "client_raw"}},
        {"id": "r", "text": "Modèle X | RAG | 59.24 | 18.21 | 17.31",
         "metadata": {"document": document, "document_id": "4", "source_kind": "client_raw"}},
    ]
    monkeypatch.setattr(chat, "fulltext_corpus_to_chunks", lambda store: chunks)
    recovered = service._measured_table_sources(
        "Quels résultats de compilabilité et couverture des modèles ?", None,
    )
    assert recovered
    evidence = service._evidence_catalog(recovered)
    numeric = next(item for item in evidence if "59.24" in item["excerpt"])
    assert numeric["evidence_nature"] == "resultat_mesure"
    assert numeric["passage_origin"] == "ambiguous_or_mixed"
    assert numeric["attribution_policy"] == "ambiguous_use_cautious_attribution"


def test_numeric_row_uses_nearest_preceding_table_header(tmp_path, monkeypatch):
    from modules.RAG import diagnostic_chat_service as chat

    service = _service_without_constructor(tmp_path)
    service._fulltext_available = lambda: True
    doc = "Mesures comparatives.docx"
    def chunk(identifier, text, position):
        return {"id": identifier, "text": text,
                "metadata": {"document": doc, "document_id": "8", "source_kind": "client_raw",
                             "paragraph_index": position}}
    chunks = [
        chunk("external_header", "Compilation | Coverage | Article externe | 2024", 0),
        chunk("project_header", "Compilation | Coverage | Modèle | Stratégie", 1),
        chunk("measures", "RAG | Modèle A | 59.24 | 18.21 | 17.31", 2),
    ]
    monkeypatch.setattr(chat, "fulltext_corpus_to_chunks", lambda store: chunks)
    found = service._measured_table_sources(
        "Quels résultats de compilation et coverage des modèles ?", None,
    )
    assert [item["id"] for item in found] == ["project_header", "measures"]


def test_numeric_recovery_works_for_other_metrics_and_project_scope(tmp_path, monkeypatch):
    from modules.RAG import diagnostic_chat_service as chat

    service = _service_without_constructor(tmp_path)
    service._fulltext_available = lambda: True
    chunks = [
        {"id": "other_header", "text": "Algorithme | Accuracy | Latency",
         "metadata": {"document": "Essais géométriques.xlsx", "document_id": "8",
                      "source_kind": "client_raw", "paragraph_index": 0}},
        {"id": "other_values", "text": "Méthode A | 86.24 | 12.50 | 11.30",
         "metadata": {"document": "Essais géométriques.xlsx", "document_id": "8",
                      "source_kind": "client_raw", "paragraph_index": 1}},
        {"id": "foreign_header", "text": "Algorithme | Accuracy | Latency",
         "metadata": {"document": "Autre dossier.xlsx", "document_id": "99",
                      "source_kind": "client_raw", "paragraph_index": 0}},
        {"id": "foreign_values", "text": "Méthode B | 99.91 | 0.11 | 0.22",
         "metadata": {"document": "Autre dossier.xlsx", "document_id": "99",
                      "source_kind": "client_raw", "paragraph_index": 1}},
    ]
    monkeypatch.setattr(chat, "fulltext_corpus_to_chunks", lambda store: chunks)
    scope = {"document_id": "8", "document_name": "Essais géométriques.xlsx"}
    found = service._measured_table_sources(
        "Quels résultats de précision et de latence pour les algorithmes ?", scope,
    )
    assert [item["id"] for item in found] == ["other_header", "other_values"]
    assert all(item["metadata"]["document_id"] == "8" for item in found)


def test_numeric_recovery_accepts_one_measured_column(tmp_path, monkeypatch):
    from modules.RAG import diagnostic_chat_service as chat

    service = _service_without_constructor(tmp_path)
    service._fulltext_available = lambda: True
    chunks = [
        {"id": "header", "text": "Approche | Accuracy",
         "metadata": {"document": "Mesures.xlsx", "document_id": "5", "paragraph_index": 0}},
        {"id": "value", "text": "Méthode A | 86.24\nMéthode B | 79.10",
         "metadata": {"document": "Mesures.xlsx", "document_id": "5", "paragraph_index": 1}},
    ]
    monkeypatch.setattr(chat, "fulltext_corpus_to_chunks", lambda store: chunks)
    found = service._measured_table_sources("Quels résultats de précision ont été mesurés ?", None)
    assert [item["id"] for item in found] == ["header", "value"]
    assert found[1]["metadata"]["verified_measurement_table"] is True


@pytest.mark.parametrize("document_type", [
    "etat_art_bibliographie", "norme_reglementation", "template_formulaire", "administratif",
])
@pytest.mark.parametrize("question", [
    "De quoi parlent les documents clients ?", "Quels objectifs sont décrits dans ce document ?",
])
def test_raw_document_type_does_not_discard_technical_passages(document_type, question):
    passage = {
        "text": "Le dossier décrit un outil de supervision et un programme de recherche.",
        "metadata": {"source_kind": "client_raw", "document_type": document_type},
    }
    assert DiagnosticRAGChatService._is_noise_for_question(passage, question) is False
    passage["metadata"]["source_kind"] = "nlp_rag"
    assert DiagnosticRAGChatService._is_noise_for_question(passage, question) is True


def test_bibliographic_passage_stays_literature_in_chat_evidence():
    passage = {
        "id": "bibliographic_passage",
        "text": "Cet article propose une méthode publiée par les auteurs.",
        "metadata": {"source_kind": "client_raw", "document_type": "etat_art_bibliographie",
                     "document": "Revue.docx", "document_id": "12"},
    }
    assert DiagnosticRAGChatService._is_noise_for_question(passage, "De quoi parle ce document ?") is False
    evidence = DiagnosticRAGChatService._evidence_catalog([passage])
    assert evidence[0]["passage_origin"] == "external_literature"


def test_reference_list_noise_still_filtered_for_technical_question():
    passage = {
        "text": "Références bibliographiques : première publication, deuxième publication.",
        "metadata": {"source_kind": "client_raw", "document_type": "etat_art_bibliographie"},
    }
    assert DiagnosticRAGChatService._is_noise_for_question(passage, "Quels essais ont été réalisés ?") is True
    assert DiagnosticRAGChatService._is_noise_for_question(passage, "Quelle bibliographie est fournie ?") is False


@pytest.mark.parametrize("scope", [None, {"document_id": "12", "document_name": "Dossier.docx"}])
def test_mixed_raw_document_reaches_ranker_without_losing_scope(tmp_path, monkeypatch, scope):
    from modules.RAG import diagnostic_chat_service as chat
    service = _service_without_constructor(tmp_path)
    monkeypatch.setattr(chat, "_cosine_rank", lambda query, texts: [0.7] * len(texts))
    passage = {
        "id": "project_passage",
        "text": "Le dossier décrit un outil de supervision et un programme de recherche.",
        "metadata": {"source_kind": "client_raw", "document_type": "etat_art_bibliographie",
                     "document": "Dossier.docx", "document_id": "12"},
    }
    foreign = {**passage, "id": "other_document", "metadata": {
        **passage["metadata"], "document_id": "99", "document": "Autre.docx"}}
    found = service._rank_and_dedupe_sources(
        question="De quoi parlent les documents clients ?", candidates=[passage, foreign],
        document_scope=scope, top_k=8,
    )
    assert any(item["id"] == "project_passage" for item in found)
    if scope:
        assert all(item["metadata"]["document_id"] == "12" for item in found)
