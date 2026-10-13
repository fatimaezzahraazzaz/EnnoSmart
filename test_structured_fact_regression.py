"""Régressions génériques des faits structurés EnnoDiagnostic V2."""

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import FusionConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.fusion import fuse_items
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.project_synthesis import ProjectSynthesizer
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.provenance import Passage, role_for_quote
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import Chunk, EvidenceRef, ExtractionItem
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.semantic_extractor import CallableLLMAdapter, SemanticExtractor
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.structured_facts import build_project_fact_lists


def fact(number, kind, statement, *, role="project_evidence", quote=None):
    evidence = EvidenceRef(
        evidence_id=f"ev_{number}", document_id="doc", document_name="source_DE.txt",
        chunk_id=f"chunk_{number}", quote=quote or statement, quote_verified=True,
    )
    return ExtractionItem(
        item_id=f"it_{number}", kind=kind, statement=statement, evidence=evidence,
        metadata={"source_role": role},
    )


def test_structured_lists_survive_empty_llm_lists_and_keep_sources():
    items = [
        fact(1, "method", "Recherche vectorielle sur des représentations apprises"),
        fact(2, "parameter", "Choix du modèle comparé entre essais"),
        fact(3, "metric", "Taux de réussite mesuré"),
        fact(4, "result", "Taux de réussite observé : 18,21 %"),
        fact(5, "method", "Méthode d'une publication externe", role="literature"),
    ]
    entities = fuse_items(items, FusionConfig())
    synthesis = ProjectSynthesizer(CallableLLMAdapter(
        lambda _system, _prompt: {
            "methods": [], "parameters": [], "metrics": [], "results": [],
        }
    )).synthesize(items, entities)
    assert [x.text for x in synthesis.methods] == [items[0].statement]
    assert [x.text for x in synthesis.parameters] == [items[1].statement]
    assert [x.text for x in synthesis.metrics] == [items[2].statement]
    assert [x.text for x in synthesis.results] == [items[3].statement]
    assert synthesis.methods[0].source_role == "project_evidence"
    assert synthesis.methods[0].sources[0]["document_name"] == "source_DE.txt"
    assert synthesis.results[0].evidence_ids == ["ev_4"]


def test_distinct_measures_models_steps_and_results_do_not_fuse():
    items = [
        fact(1, "metric", "Couverture des lignes"),
        fact(2, "metric", "Couverture des branches"),
        fact(3, "demarche", "Exécuter les essais"),
        fact(4, "demarche", "Évaluer les essais"),
        fact(5, "demarche", "Analyser les informations d'échec"),
        fact(6, "parameter", "Modèle Alpha"),
        fact(7, "parameter", "Modèle Beta"),
        fact(8, "result", "Modèle Alpha : score mesuré de 0,65"),
        fact(9, "result", "Modèle Beta : score mesuré de 0,71"),
        fact(10, "demarche", "Exécuter les essais"),
    ]
    entities = fuse_items(items, FusionConfig())
    counts = {kind: sum(entity.kind == kind for entity in entities)
              for kind in {item.kind for item in items}}
    assert counts == {"metric": 2, "demarche": 3, "parameter": 2, "result": 2}
    duplicate = next(entity for entity in entities if entity.statement == "Exécuter les essais")
    assert set(duplicate.source_item_ids) == {"it_3", "it_10"}


def test_variable_review_extracts_distinct_parameters_without_losing_demarche():
    source = (
        "Nous avons comparé différentes configurations : présence de la source complète, "
        "activation des dépendances et taille du contexte."
    )
    def llm(system, _prompt):
        if "Tu classes la provenance" in system:
            return {"passages": [{"id": "P1", "role": "project_evidence"}]}
        if "Tu relis des faits extraits" in system:
            return {"parameters": [
                {"statement": "Présence de la source complète", "evidence_quote": "présence de la source complète"},
                {"statement": "Activation des dépendances", "evidence_quote": "activation des dépendances"},
                {"statement": "Taille du contexte", "evidence_quote": "taille du contexte"},
            ]}
        return {"demarches": [{
            "statement": "Comparer différentes configurations : source complète, dépendances, taille du contexte",
            "evidence_quote": source,
        }]}
    chunk = Chunk(chunk_id="chunk", document_id="doc", document_name="source_DE.txt", text=source)
    items = SemanticExtractor(CallableLLMAdapter(llm)).extract_chunk(chunk)
    assert sum(item.kind == "demarche" for item in items) == 1
    parameters = [item for item in items if item.kind == "parameter"]
    assert [item.statement for item in parameters] == [
        "Présence de la source complète", "Activation des dépendances", "Taille du contexte"
    ]
    assert all(item.metadata["source_role"] == "project_evidence" for item in parameters)


def test_explicit_publication_attribution_cannot_be_project_evidence():
    quote = "Les auteurs ont obtenu une amélioration mesurée."
    passages = [Passage("P1", quote, 0, len(quote), "project_evidence")]
    assert role_for_quote(quote, quote, passages) == "literature"


def test_distinct_project_steps_are_not_capped_at_five():
    items = [fact(index, "demarche", f"Étape technique distincte numéro {index}")
             for index in range(1, 9)]
    items.append(fact(9, "demarche", "Étape décrite par une étude externe", role="literature"))
    entities = fuse_items(items, FusionConfig())
    def reviewer(system, prompt):
        assert "Tu sélectionnes les étapes techniques" in system
        assert "Étape décrite par une étude externe" not in prompt
        return {"keep_ids": [f"F{index}" for index in range(1, 9)]}
    lists = build_project_fact_lists(items, entities, CallableLLMAdapter(reviewer))
    assert len(lists["demarche"]) == 8
    assert all(point.source_role == "project_evidence" for point in lists["demarche"])


def test_fact_level_review_keeps_published_method_and_result_in_literature():
    source = (
        "Cet article décrit une méthode de génération. "
        "Le modèle a obtenu une précision de 71 %."
    )
    def llm(system, _prompt):
        if "Tu classes la provenance" in system:
            return {"passages": [
                {"id": "P1", "role": "project_evidence"},
                {"id": "P2", "role": "project_evidence"},
            ]}
        if "Tu vérifies la provenance" in system:
            # Les identifiants proviennent des faits fournis au relecteur.
            import re
            identifiers = re.findall(r"\bit_[a-f0-9]{14}\b", _prompt)
            return {"roles": [{"item_id": value, "role": "literature"}
                              for value in dict.fromkeys(identifiers)]}
        return {
            "methods": [{"statement": "Méthode de génération publiée",
                         "evidence_quote": "Cet article décrit une méthode de génération."}],
            "results": [{"statement": "Précision publiée : 71 %",
                         "evidence_quote": "Le modèle a obtenu une précision de 71 %."}],
        }
    chunk = Chunk(chunk_id="published", document_id="doc", document_name="revue.txt", text=source)
    items = SemanticExtractor(CallableLLMAdapter(llm)).extract_chunk(chunk)
    assert {item.metadata["source_role"] for item in items} == {"literature"}
    lists = build_project_fact_lists(items, fuse_items(items, FusionConfig()))
    assert lists["method"] == []
    assert lists["result"] == []
