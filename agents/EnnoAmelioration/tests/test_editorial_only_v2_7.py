from __future__ import annotations

from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.application.semantic_routing_service import SemanticRoutingService
from agents.EnnoAmelioration.application.traceability_service import build_revision_trace
from agents.EnnoAmelioration.domain.models import SectionFunction, TargetScope


EDITORIAL_PROMPT = """Améliore uniquement la rédaction et la structure de cette section.
Je veux rendre la motivation de la démarche expérimentale plus claire et améliorer l’enchaînement des idées.
Conserve strictement toutes les informations techniques déjà présentes dans la section.
N’ajoute aucun nouvel argument scientifique, aucune méthode, aucun résultat et aucune information absente du texte actuel.
Ne lance aucune recherche scientifique.
N’utilise aucune nouvelle source.
Ne lance pas EnnoScholar.
Propose uniquement une nouvelle version rédactionnelle de cette section."""


def test_negative_argument_clause_does_not_trigger_argumentation_or_diagnostic():
    decision = understand_instruction(EDITORIAL_PROMPT, TargetScope.SECTION)

    assert decision.editorial_only is True
    assert decision.strict_fact_preservation is True
    assert decision.needs_diagnostic is False
    assert decision.needs_project_evidence is False
    assert decision.needs_scholar is False
    assert decision.needs_new_research is False
    assert decision.forbids_new_research is True
    assert decision.forbids_scholar is True
    assert [intent.value for intent in decision.intents] == ["clarity", "style", "structure"]


def test_method_role_cannot_reactivate_diagnostic_in_editorial_only_mode(monkeypatch):
    service = SemanticRoutingService()
    monkeypatch.setattr(
        service,
        "_fastjudge",
        lambda rows: {
            "target": {
                "function": SectionFunction.METHOD,
                "confidence": 0.99,
                "classifier": "test",
            }
        },
    )
    monkeypatch.setattr(service, "_semantic_functions", lambda rows: {})

    decision = service.route(
        EDITORIAL_PROMPT,
        TargetScope.SECTION,
        "ADASCA est dédié à l'entraînement de modèles ATR.",
    )

    assert decision.section_function == SectionFunction.METHOD
    assert decision.editorial_only is True
    assert decision.specialist_route.value == "writer"
    assert decision.needs_diagnostic is False
    assert decision.needs_scholar is False
    assert decision.section_plan[0].route.value == "writer"


def test_paraphrases_are_not_reported_as_insert_delete_pairs():
    original = """ADASCA est, de base, spécifiquement dédié à l’entraînement de modèle ATR.

La problématique d’ATD diffère de celle d’ATR étant donné qu’en plus d’une classe associée à chaque cible, leur nombre ainsi que leur position au sein d’une image constitue la principale complexité de la problématique d’ATD.

La redéfinition d’ADASCA pour l’adapter à la problématique d’ATD a pu être décomposée en trois points : • Développement d’une nouvelle opération d’incrustation automatique d’une ou plusieurs cibles simulées dans un fond mesuré. • Labellisation automatique pour la problématique d’ATD en générant automatique les valeurs de bounding boxes des cibles incrustées. • Gestion et développement d’opérations d’augmentation de données spécifique à la problématique d’ATD."""
    improved = """ADASCA est initialement dédié à l’entraînement de modèles ATR.

Contrairement à l’ATR où chaque cible est associée à une classe, l’ATD porte aussi sur le nombre de cibles et leur position au sein de l’image.

La refonte d’ADASCA pour l’ATD s’est articulée autour de trois axes principaux : • Le développement d’une nouvelle opération d’incrustation automatique d’une ou plusieurs cibles simulées dans un fond mesuré ; • La labellisation automatique spécifique à l’ATD, permettant de générer automatiquement les valeurs des bounding boxes des cibles incrustées ; • La gestion et le développement d’opérations d’augmentation de données adaptées à la problématique d’ATD."""

    routing = understand_instruction(EDITORIAL_PROMPT, TargetScope.SECTION)
    trace = build_revision_trace(
        original,
        improved,
        routing,
        {"cir_style": {"available": False}},
    )

    assert trace["changes"]
    assert {change["operation"] for change in trace["changes"]} == {"replace"}
    assert not any(
        claim.get("severity") == "review" for claim in trace["unsupported_claims"]
    )


def test_real_new_paragraph_is_flagged_in_strict_fact_mode():
    original = "ADASCA est dédié à l’entraînement de modèles ATR."
    improved = (
        original
        + "\n\nCette approche garantit une meilleure robustesse opérationnelle dans tous les environnements réels."
    )
    routing = understand_instruction(EDITORIAL_PROMPT, TargetScope.SECTION)
    trace = build_revision_trace(
        original,
        improved,
        routing,
        {"cir_style": {"available": False}},
    )

    assert any(change["operation"] == "insert" for change in trace["changes"])
    assert any(claim.get("severity") == "review" for claim in trace["unsupported_claims"])


def test_real_test1_diff_is_presented_as_replacements_not_false_insert_delete():
    original = """Motivation et démarche expérimentale Cette tâche a pour objectif le développement d’un pipeline permettant la génération en ligne de données synthétiques, propice à l’entraînement d’algorithme de détection deep learning, avec augmentation de données.

L’algorithme d’apprentissage profond ADASCA développé lors de travaux interne SCALIAN a servi de point de départ à la résolution de cette tâche.

ADASCA est, de base, spécifiquement dédié à l’entraînement de modèle ATR.

La problématique d’ATD diffère de celle d’ATR étant donné qu’en plus d’une classe associée à chaque cible, leur nombre ainsi que leur position au sein d’une image constitue la principale complexité de la problématique d’ATD.

La redéfinition d’ADASCA pour l’adapter à la problématique d’ATD a pu être décomposée en trois points : • Développement d’une nouvelle opération d’incrustation automatique d’une ou plusieurs cibles simulées dans un fond mesuré. • Labellisation automatique pour la problématique d’ATD en générant automatique les valeurs de bounding boxes des cibles incrustées. • Gestion et développement d’opérations d’augmentation de données spécifique à la problématique d’ATD."""
    improved = """Motivation et démarche expérimentale Cette tâche vise à développer un pipeline capable de générer en ligne des données synthétiques, adaptées à l’entraînement d’algorithmes de détection par deep learning avec augmentation de données.

Le point de départ retenu est l’algorithme d’apprentissage profond ADASCA, élaboré lors de travaux internes chez SCALIAN.

Initialement conçu pour l’entraînement de modèles ATR (Automatic Target Recognition), ADASCA a été réadapté pour répondre à la problématique différente de l’ATD (Automatic Target Detection).

En effet, contrairement à l’ATR où chaque cible est associée à une classe, l’ATD présente une complexité supplémentaire : la détection porte non seulement sur les classes, mais aussi sur le nombre de cibles et leur position au sein de l’image.

La refonte d’ADASCA pour l’ATD s’est articulée autour de trois axes principaux : • Le développement d’une nouvelle opération d’incrustation automatique d’une ou plusieurs cibles simulées dans un fond mesuré ; • La labellisation automatique spécifique à l’ATD, permettant de générer automatiquement les valeurs des bounding boxes des cibles incrustées ; • La gestion et le développement d’opérations d’augmentation de données adaptées à la problématique d’ATD."""

    routing = understand_instruction(EDITORIAL_PROMPT, TargetScope.SECTION)
    trace = build_revision_trace(
        original,
        improved,
        routing,
        {"cir_style": {"available": False}},
    )

    assert trace["changes"]
    assert all(change["operation"] == "replace" for change in trace["changes"])
