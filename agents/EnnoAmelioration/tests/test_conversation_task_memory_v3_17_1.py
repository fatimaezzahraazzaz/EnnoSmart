from agents.EnnoAmelioration.application.conversation_task_memory_v317 import (
    evolve_task_memory,
    is_resume_message,
    recover_contract_from_history,
)

def route(*intents, needs_scholar=False, needs_new_research=False):
    return {"intents": list(intents), "needs_scholar": needs_scholar, "needs_new_research": needs_new_research}

def test_resume_message():
    assert is_resume_message("ok redige la section")
    assert is_resume_message("c'est bon rédige")

def test_save_style_argumentation():
    m, _ = evolve_task_memory(
        existing_memory=None,
        raw_message="améliore le style et renforce l'argumentation de 1.3.1.6",
        routing=route("style", "argumentation", "research", needs_scholar=True, needs_new_research=True),
        section_id="1.3.1.6", section_title="État de l'art interne", scope="section",
        has_accepted_sources=False, analyzed_history=[],
    )
    assert m["active_contract"]["style_requested"]
    assert m["active_contract"]["argumentation_requested"]

def test_resume_keeps_full_contract():
    m, _ = evolve_task_memory(
        existing_memory=None,
        raw_message="améliore le style et renforce l'argumentation de 1.3.1.6",
        routing=route("style", "argumentation", "research", needs_scholar=True, needs_new_research=True),
        section_id="1.3.1.6", section_title="État de l'art interne", scope="section",
        has_accepted_sources=False, analyzed_history=[],
    )
    m2, effective = evolve_task_memory(
        existing_memory=m,
        raw_message="ok redige la section",
        routing=route("general_revision"),
        section_id="1.3.1.6", section_title="État de l'art interne", scope="section",
        has_accepted_sources=True, analyzed_history=[],
    )
    assert "améliore le style" in effective
    assert "renforce l'argumentation" in effective
    assert m2["history"][-1]["resume_from_contract"]

def test_recovery_old_conversation():
    h = [{
        "content": "améliore le style et aussi renforce l'argumentation de 1.3.1.6",
        "target_scope": "section",
        "target_section_id": "1.3.1.6",
        "target_section_title": "État de l'art interne",
        "routing": route("style", "argumentation", "research", needs_scholar=True, needs_new_research=True),
    }]
    c = recover_contract_from_history(h, "1.3.1.6", "État de l'art interne", "section")
    assert c["style_requested"]
    assert c["argumentation_requested"]

def test_new_target_replaces_contract():
    m, _ = evolve_task_memory(
        existing_memory=None,
        raw_message="renforce 1.3.1.6",
        routing=route("argumentation"),
        section_id="1.3.1.6", section_title="État de l'art interne", scope="section",
        has_accepted_sources=False, analyzed_history=[],
    )
    m2, _ = evolve_task_memory(
        existing_memory=m,
        raw_message="améliore le style de 1.2.1",
        routing=route("style"),
        section_id="1.2.1", section_title="Contexte", scope="section",
        has_accepted_sources=False, analyzed_history=[],
    )
    assert m2["active_contract"]["target_section_id"] == "1.2.1"

def test_supplementary_style_is_merged():
    m, _ = evolve_task_memory(
        existing_memory=None,
        raw_message="renforce scientifiquement X",
        routing=route("argumentation", "research", needs_scholar=True, needs_new_research=True),
        section_id="X", section_title="X", scope="section",
        has_accepted_sources=False, analyzed_history=[],
    )
    m2, effective = evolve_task_memory(
        existing_memory=m,
        raw_message="améliore aussi le style et la clarté",
        routing=route("style", "clarity"),
        section_id="X", section_title="X", scope="section",
        has_accepted_sources=True, analyzed_history=[],
    )
    assert "renforce scientifiquement" in effective
    assert "améliore aussi le style" in effective
    assert m2["active_contract"]["style_requested"]


def test_anchored_enrichment_rejects_missing_accepted_source(monkeypatch):
    from agents.EnnoScholar.state_of_art import existing_review_enrichment_service as svc

    monkeypatch.setattr(svc, "_FORBIDDEN_OUTPUT_RE", __import__("re").compile(r"$^"))
    sections = [{"section_id": "S1", "content": "Phrase ancre unique dans la section."}]
    evidence = {
        "A1": "Paper A1 evidence without numbers.",
        "A2": "Paper A2 evidence without numbers.",
        "A3": "Paper A3 evidence without numbers.",
    }
    payload = {
        "additions": [
            {
                "section_id": "S1",
                "anchor": "Phrase ancre unique dans la section.",
                "content": (
                    "Cette formulation scientifique suffisamment longue présente un argument "
                    "documenté et traçable à partir de la première preuve acceptée, sans ajouter "
                    "de valeur numérique ni de conclusion extérieure au corpus fourni. Elle "
                    "reste volontairement descriptive afin de satisfaire le contrat de preuve "
                    "et de permettre la vérification déterministe de la couverture [A1]."
                ),
            }
        ]
    }
    _, errors = svc._validate_additions(
        payload,
        target_text=sections[0]["content"],
        sections=sections,
        evidence_by_citation=evidence,
    )
    assert "sources_acceptees_non_utilisees:A2,A3" in errors


def test_anchored_enrichment_accepts_all_accepted_sources(monkeypatch):
    from agents.EnnoScholar.state_of_art import existing_review_enrichment_service as svc

    monkeypatch.setattr(svc, "_FORBIDDEN_OUTPUT_RE", __import__("re").compile(r"$^"))
    sections = [{"section_id": "S1", "content": "Phrase ancre unique dans la section."}]
    evidence = {
        "A1": "Paper A1 evidence without numbers.",
        "A2": "Paper A2 evidence without numbers.",
        "A3": "Paper A3 evidence without numbers.",
    }
    payload = {
        "additions": [
            {
                "section_id": "S1",
                "anchor": "Phrase ancre unique dans la section.",
                "content": (
                    "Cette synthèse scientifique suffisamment développée relie les différentes "
                    "preuves validées au même axe d'analyse sans introduire de résultat numérique "
                    "ou d'élément étranger. Les travaux sont mobilisés conjointement pour soutenir "
                    "une justification traçable et directement vérifiable dans le corpus accepté, "
                    "tout en conservant une formulation prudente et publiable [A1][A2][A3]."
                ),
            }
        ]
    }
    accepted, errors = svc._validate_additions(
        payload,
        target_text=sections[0]["content"],
        sections=sections,
        evidence_by_citation=evidence,
    )
    assert accepted
    assert not any(error.startswith("sources_acceptees_non_utilisees") for error in errors)


def test_resume_does_not_replay_original_research_command():
    memory, _ = evolve_task_memory(
        existing_memory=None,
        raw_message="améliore le style et renforce l'argumentation et fais une recherche",
        routing=routing("style", "argumentation", "research", needs_scholar=True, needs_new_research=True),
        section_id="1.3.1.6",
        section_title="État de l'art interne",
        scope="section",
        has_accepted_sources=False,
        analyzed_history=[],
    )
    _, effective = evolve_task_memory(
        existing_memory=memory,
        raw_message="ok redige la section",
        routing=routing("general_revision"),
        section_id="1.3.1.6",
        section_title="État de l'art interne",
        scope="section",
        has_accepted_sources=True,
        analyzed_history=[],
    )
    assert "style rédactionnel" in effective
    assert "argumentation" in effective
    assert "fais une recherche" not in effective
    assert "TOUTES les preuves acceptées" in effective
