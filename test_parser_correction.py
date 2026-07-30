# test_parser_correction.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ajoute le chemin racine pour importer les modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.guided_research.consultant_brief_parser import ConsultantBriefParser
from modules.guided_research.domain.enums import ConsultantIntent, GuidedResearchTargetMode, RequestedEntityType


def test_concept_promotion():
    """Vérifie que le parseur promeut CONCEPT en SCIENTIFIC_CONCEPT quand le contexte est scientifique."""
    parser = ConsultantBriefParser(use_llm=True)

    # Cas 1 : concept scientifique explicite
    prompt_scientifique = """
    Explique le concept de dérive thermique dans les semi-conducteurs,
    ses mécanismes physiques et ses conséquences. Cette partie concerne le verrou 1.
    """
    result_scientifique = parser.parse(
        prompt_scientifique,
        intent=ConsultantIntent.DESCRIBE_REQUIREMENTS,
        default_output_mode=GuidedResearchTargetMode.GLOBAL,
    )
    brief_sci = result_scientifique.brief

    # Cherche le topic "dérive thermique" ou "thermal drift"
    topic_sci = None
    for topic in brief_sci.requested_topics:
        if "thermique" in topic.name or "thermal" in topic.name:
            topic_sci = topic
            break

    assert topic_sci is not None, "Le concept scientifique n'a pas été détecté."
    assert topic_sci.entity_type == RequestedEntityType.SCIENTIFIC_CONCEPT, (
        f"Type incorrect pour concept scientifique: {topic_sci.entity_type}"
    )

    # Cas 2 : concept non scientifique (marketing, design)
    prompt_non_scientifique = """
    Explique le concept de design minimaliste dans l'industrie du meuble,
    ses origines et son impact sur les consommateurs.
    """
    result_non_sci = parser.parse(
        prompt_non_scientifique,
        intent=ConsultantIntent.DESCRIBE_REQUIREMENTS,
        default_output_mode=GuidedResearchTargetMode.GLOBAL,
    )
    brief_non = result_non_sci.brief

    # Cherche un topic "design minimaliste" ou "minimalist"
    topic_non = None
    for topic in brief_non.requested_topics:
        if "minimaliste" in topic.name or "minimalist" in topic.name:
            topic_non = topic
            break

    # Il se peut que le parseur ne le détecte pas du tout (car pas de majuscules, pas de suffixe technique)
    # Dans ce cas, on considère que c'est acceptable. Si détecté, il ne doit pas être SCIENTIFIC_CONCEPT.
    if topic_non is not None:
        assert topic_non.entity_type != RequestedEntityType.SCIENTIFIC_CONCEPT, (
            "Un concept non scientifique ne doit pas être promu en SCIENTIFIC_CONCEPT."
        )

    # Vérification des contraintes générales
    assert brief_sci.use_previous_cir is False, "use_previous_cir doit rester False"
    assert brief_sci.previous_years == [], "previous_years doit être vide"
    assert len(brief_sci.requested_sections) > 0, "Au moins une section doit être créée"
    assert result_scientifique.parser == "llm", "Le parseur doit utiliser le LLM"

    print("Tous les tests de promotion de concept sont passés.")


def test_full_prompt_radar():
    """Reprend le prompt original du test PowerShell pour valider la correction complète."""
    PROMPT = """
    Construis le plan scientifique suivant sans rédiger le texte final.

    Première partie : expliquer le concept thermal domain drift,
    ses mécanismes, ses conséquences et ses limites. Cette partie
    concerne le premier verrou.

    Deuxième partie : présenter le dataset NovaSAR-27, son protocole
    de constitution, ses conditions d'apprentissage et de test,
    ses biais et ses limites. Cette partie concerne le premier verrou.

    Troisième partie : expliquer le simulateur EchoForge-X,
    ses principes, ses hypothèses, ses résultats expérimentaux
    et ses limites. Présenter également la méthode Sim2Measure
    utilisée pour rapprocher les données simulées et mesurées.
    Cette partie concerne le deuxième verrou.

    Quatrième partie : présenter le modèle d'intelligence artificielle
    FusionRadarNet, son architecture, son protocole d'entraînement,
    ses performances et ses limites. Cette partie concerne le
    troisième verrou.

    Utilise en priorité les articles déjà sélectionnés. Recherche des
    sources complémentaires lorsque les preuves existantes sont
    insuffisantes. N'invente aucune information.
    """

    parser = ConsultantBriefParser(use_llm=True)
    result = parser.parse(
        PROMPT,
        intent=ConsultantIntent.DESCRIBE_REQUIREMENTS,
        default_output_mode=GuidedResearchTargetMode.GLOBAL,
    )
    brief = result.brief

    # Vérifications principales
    assert result.parser == "llm", f"Le parseur doit utiliser le LLM, parser={result.parser}"
    assert brief.use_previous_cir is False, "use_previous_cir doit rester False"
    assert brief.previous_years == [], "previous_years doit être vide"
    assert len(brief.requested_sections) == 4, f"4 sections attendues, obtenu {len(brief.requested_sections)}"

    # Vérifier les types des entités
    expected_types = {
        "thermal domain drift": RequestedEntityType.SCIENTIFIC_CONCEPT,
        "novasar-27": RequestedEntityType.DATASET,
        "echoforge-x": RequestedEntityType.SCIENTIFIC_SOFTWARE,
        "sim2measure": RequestedEntityType.SCIENTIFIC_METHOD,
        "fusionradarnet": RequestedEntityType.AI_MODEL,
    }

    detected = {}
    for topic in brief.requested_topics:
        detected[topic.name.casefold()] = topic.entity_type

    for name, expected in expected_types.items():
        key = name.casefold()
        assert key in detected, f"Entité {name} non détectée"
        assert detected[key] == expected, (
            f"Type incorrect pour {name}: attendu={expected}, obtenu={detected[key]}"
        )

    # Vérifier les verrous
    verrous = set()
    for topic in brief.requested_topics:
        verrous.update(topic.target_verrous)
    expected_verrous = {"verrou_1", "verrou_2", "verrou_3"}
    assert expected_verrous.issubset(verrous), f"Verrous manquants: {expected_verrous - verrous}"

    print("Test complet du prompt radar réussi.")


if __name__ == "__main__":
    test_concept_promotion()
    test_full_prompt_radar()
    print("Tous les tests sont validés.")