from __future__ import annotations

from io import BytesIO

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.EnnoAmelioration.application.agent import EnnoAmeliorationAgent
from agents.EnnoAmelioration.application.audit_service import audit_text
from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.application.section_parser import (
    infer_section_from_instruction,
    parse_sections,
    repair_section_boundaries,
    resolve_target,
)
from agents.EnnoAmelioration.application.writer_service import (
    _match_editorial_format,
    validate_conservative_revision,
)
from agents.EnnoAmelioration.domain.models import (
    ImprovementIntent,
    ImprovementRequest,
    TargetScope,
)
from db.database import Base
from db.models import Document as StoredDocument
from db.models import ImprovementVersion, Project, ScholarRun, User
from services import improvement_service


class FakeWriter:
    def rewrite(self, request, routing, audit, evidence):
        return request.target_text.rstrip() + "\n\nVersion améliorée et mieux articulée.", {
            "provider": "fake",
            "model": "test",
        }


class CountingWriter(FakeWriter):
    def __init__(self):
        self.calls = 0

    def rewrite(self, request, routing, audit, evidence):
        self.calls += 1
        return super().rewrite(request, routing, audit, evidence)


class FakeScholarLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def generate(self, *args, **kwargs):
        import json

        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)

    def get_last_generation_meta(self):
        return {
            "provider": "fake",
            "model": "scholar-test",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }


def _database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _project(db):
    user = User(
        full_name="Consultante Test",
        email="amelioration@example.test",
        hashed_password="test",
        role="consultant",
    )
    db.add(user)
    db.flush()
    project = Project(
        consultant_id=user.id,
        organisme="Test",
        project_name="Projet révision",
        year="2026",
        domain_label="Ingénierie",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return user, project


def test_sections_are_derived_from_document_structure():
    sections = parse_sections("# Contexte\nTexte.\n\n## Méthodes\nEssais.\n\n3. Résultats\nMesures.")
    assert [section.title for section in sections] == ["Contexte", "Méthodes", "Résultats"]
    assert [section.level for section in sections] == [1, 2, 1]


def test_pdf_table_of_contents_is_not_treated_as_document_sections():
    text = (
        "Table des matieres\n"
        "1. Operation de recherche ................................ 3\n"
        "1.1. Contexte scientifique ............................... 4\n"
        "1.2. Incertitude sur la transferabilite des\n"
        "representations apprises ................................ 6\n\n"
        "1. OPERATION DE RECHERCHE\n"
        "1.1. Contexte scientifique\n"
        "Le contexte reel du projet est presente ici.\n\n"
        "1.2. Incertitude sur la transferabilite des representations apprises\n"
        "La difficulte scientifique est decrite dans le corps du document.\n\n"
        "1. Cette phrase est un element de liste et non une section,\n"
        "2. cette phrase poursuit la meme enumeration.\n"
    )

    sections = parse_sections(text)

    titles = [section.title for section in sections]
    assert titles.count("OPERATION DE RECHERCHE") == 1
    assert titles.count("Contexte scientifique") == 1
    assert titles.count(
        "Incertitude sur la transferabilite des representations apprises"
    ) == 1
    assert all("element de liste" not in title for title in titles)
    assert next(
        section for section in sections if section.title == "OPERATION DE RECHERCHE"
    ).start > text.index("1. Operation de recherche")


def test_style_cir_instruction_does_not_select_an_unrelated_rd_heading():
    sections = parse_sections(
        "1.1. Contexte\nTexte.\n\n"
        "1.2. Indicateurs de R&D\nIndicateurs.\n\n"
        "1.3. Conclusion\nConclusion."
    )

    inferred = infer_section_from_instruction(
        "Reformule dans un style consultant CIR/R&D.",
        sections,
    )

    assert inferred is None


def test_plain_consultant_headings_are_detected_without_markdown():
    text = (
        "Contexte du projet\n\n"
        "Le projet vise à prédire les dérives thermiques.\n\n"
        "Verrou scientifique\n\n"
        "La capacité de généralisation reste incertaine.\n\n"
        "Méthode expérimentale\n\n"
        "Des essais seront conduits dans plusieurs conditions."
    )
    sections = parse_sections(text)
    assert [section.title for section in sections] == [
        "Contexte du projet",
        "Verrou scientifique",
        "Méthode expérimentale",
    ]


def test_plain_paragraphs_remain_selectable_when_there_are_no_headings():
    text = (
        "Le projet vise à prédire les dérives thermiques à partir de plusieurs capteurs.\n\n"
        "La généralisation du modèle à de nouveaux équipements reste incertaine.\n\n"
        "Des essais compareront plusieurs régimes de fonctionnement."
    )
    sections = parse_sections(text)
    assert len(sections) == 3
    assert all(not section.title.startswith("#") for section in sections)


def test_routing_calls_only_the_needed_specialists():
    style = understand_instruction("Rends ce passage plus fluide.", TargetScope.SECTION)
    assert not style.needs_diagnostic
    assert not style.needs_scholar

    cir = understand_instruction("Renforce la démonstration d'éligibilité CIR.", TargetScope.SECTION)
    assert cir.needs_diagnostic
    assert not cir.needs_new_research

    research = understand_instruction("Recherche de nouvelles publications pour argumenter ce passage.", TargetScope.SECTION)
    assert research.needs_scholar
    assert research.needs_new_research


def test_rewrite_with_validated_sources_does_not_restart_research():
    routing = understand_instruction(
        (
            "Réécris maintenant la section avec uniquement les publications validées "
            "dont les Article Cards sont disponibles, sans lancer de nouvelle recherche."
        ),
        TargetScope.SECTION,
    )

    assert routing.needs_scholar
    assert not routing.needs_new_research
    assert ImprovementIntent.RESEARCH not in routing.intents


def test_explicit_research_after_a_negative_clause_is_still_understood():
    routing = understand_instruction(
        (
            "Ne lance pas de recherche générale, mais recherche uniquement des comparaisons "
            "expérimentales sur le protocole ciblé."
        ),
        TargetScope.SECTION,
    )

    assert routing.needs_new_research


def test_audit_is_generic_and_flags_untraced_metrics():
    routing = understand_instruction(
        "Renforce avec les publications validées.",
        TargetScope.SECTION,
    )
    findings = audit_text("Le modèle atteint 94 % de précision. Le résultat reste variable.", routing)
    assert "untraced_metrics" in {finding.code for finding in findings}


def test_original_is_preserved_until_candidate_is_accepted(monkeypatch):
    db = _database()
    user, project = _project(db)
    monkeypatch.setattr(
        improvement_service,
        "_AGENT",
        EnnoAmeliorationAgent(writer=FakeWriter()),
    )

    original_text = "# Contexte\nLe système présente une incertitude."
    session = improvement_service.create_session(
        db,
        project,
        user_id=user.id,
        title="Test versions",
        source_text=original_text,
        target_scope="section",
    )
    original_id = session.active_version_id
    section_id = session.context_json["sections"][0]["section_id"]

    session, candidate = improvement_service.send_message(
        db,
        project,
        session.id,
        message="Améliore la clarté sans changer les faits.",
        target_scope="section",
        target_section_id=section_id,
    )
    assert candidate is not None
    assert session.active_version_id == original_id
    assert candidate.status == "candidate"

    improvement_service.decide_version(
        db,
        project.id,
        session.id,
        candidate.id,
        decision="accepted",
    )
    versions = (
        db.query(ImprovementVersion)
        .filter(ImprovementVersion.session_id == session.id)
        .order_by(ImprovementVersion.version_number)
        .all()
    )
    assert versions[0].status == "original"
    assert versions[0].content == original_text
    assert versions[1].status == "accepted"
    assert improvement_service.get_session(db, project.id, session.id).active_version_id == versions[1].id


def test_replacing_a_section_preserves_the_next_heading_boundary():
    from agents.EnnoAmelioration.application.section_parser import replace_target

    text = (
        "# Verrou scientifique\n\nTexte initial.\n\n"
        "# Méthode expérimentale\n\nProtocole initial."
    )
    section = parse_sections(text)[0]
    improved = "# Verrou scientifique\n\nTexte renforcé."
    result = replace_target(text, section.content, improved)
    assert "Texte renforcé.\n\n# Méthode expérimentale" in result
    assert "renforcé.#" not in result
    assert [row.title for row in parse_sections(result)] == [
        "Verrou scientifique",
        "Méthode expérimentale",
    ]


def test_selecting_a_parent_section_includes_all_its_subsections():
    text = (
        "1.3. État de l'art et verrous\nIntroduction générale.\n\n"
        "1.3.1. Analyse externe\nAnalyse détaillée.\n\n"
        "1.3.1.1. Données\nCorps sur les données.\n\n"
        "1.3.2. Verrous\nCorps sur les verrous.\n\n"
        "1.4. Travaux\nCorps des travaux."
    )
    sections = parse_sections(text)
    parent = next(section for section in sections if section.title == "Analyse externe")

    target, resolved = resolve_target(text, sections, section_id=parent.section_id)

    assert resolved is not None
    assert "1.3.1.1. Données" in target
    assert "Corps sur les données." in target
    assert "1.3.2. Verrous" not in target


def test_chat_infers_numbered_section_and_includes_only_its_descendants():
    text = (
        "1.3. État de l'art et verrous\nIntroduction générale.\n\n"
        "1.3.1. Analyse de l'état de l'art\nAnalyse détaillée.\n\n"
        "1.3.1.1. Données disponibles\nCorps sur les données.\n\n"
        "1.3.2. Verrous scientifiques\nCorps sur les verrous."
    )
    sections = parse_sections(text)
    inferred = infer_section_from_instruction(
        "Améliore exclusivement la section 1.3.1 Analyse de l'état de l'art.",
        sections,
    )

    assert inferred is not None
    assert inferred.title == "Analyse de l'état de l'art"
    target, _ = resolve_target(text, sections, section_id=inferred.section_id)
    assert "1.3.1.1. Données disponibles" in target
    assert "1.3.2. Verrous scientifiques" not in target


def test_chat_infers_section_from_actual_title_without_manual_selection():
    sections = parse_sections(
        "Contexte du projet\n\nTexte.\n\n"
        "Analyse de l'état de l'art\n\nCorps scientifique.\n\n"
        "Méthode expérimentale\n\nProtocole."
    )

    inferred = infer_section_from_instruction(
        "Renforce l'argumentation de l'analyse de l'état de l'art.",
        sections,
    )

    assert inferred is not None
    assert inferred.title == "Analyse de l'état de l'art"


def test_message_target_overrides_the_section_previously_opened_in_ui(monkeypatch):
    db = _database()
    user, project = _project(db)
    monkeypatch.setattr(
        improvement_service,
        "_AGENT",
        EnnoAmeliorationAgent(writer=FakeWriter()),
    )
    text = (
        "1.3. État de l'art et verrous\nIntroduction.\n\n"
        "1.3.1. Analyse de l'état de l'art\nAnalyse scientifique.\n\n"
        "1.3.2. Verrous scientifiques\nVerrous à préserver."
    )
    session = improvement_service.create_session(
        db,
        project,
        user_id=user.id,
        source_text=text,
        target_scope="section",
    )
    wrong_ui_section = next(
        row for row in session.context_json["sections"]
        if row["title"] == "État de l'art et verrous"
    )

    updated, candidate = improvement_service.send_message(
        db,
        project,
        session.id,
        message="Clarifie uniquement la section 1.3.1.",
        target_scope="section",
        target_section_id=wrong_ui_section["section_id"],
        target_section_title=wrong_ui_section["title"],
    )

    assert candidate is not None, (
        updated.state,
        [(row.intent, row.content) for row in updated.messages[-2:]],
    )
    assert updated.target_section_title == "Analyse de l'état de l'art"
    assert "Analyse scientifique.\n\nVersion améliorée" in candidate.content
    assert "Verrous à préserver." in candidate.content


def test_focused_research_uses_real_subsections_and_publication_ceiling():
    db = _database()
    _, project = _project(db)
    from agents.EnnoAmelioration.domain.models import ImprovementRequest

    request = ImprovementRequest(
        instruction=(
            "Recherche des preuves expérimentales. Aucune publication "
            "postérieure au 31 décembre 2024."
        ),
        full_text="",
        target_text=(
            "1.3.1. Analyse\nIntroduction.\n\n"
            "1.3.1.1. Données disponibles\nCorpus et protocoles.\n\n"
            "1.3.1.2. Robustesse des modèles\nRésultats sous changement de régime."
        ),
        target_scope=TargetScope.SECTION,
        target_section_title="Analyse",
        project_name=project.project_name,
        project_domain=project.domain_label or "",
    )

    payload = improvement_service._focused_research_requests(project, request)

    assert len(payload) == 2
    assert {row["section_titles"][0] for row in payload} == {
        "Données disponibles",
        "Robustesse des modèles",
    }
    assert all(row["publication_year_max"] == 2024 for row in payload)
    assert all(row["query_kind"] == "scientific_evidence" for row in payload)


def test_repairs_a_heading_already_glued_by_an_older_version():
    broken = (
        "La robustesse doit être démontrée.# Méthode expérimentale\n\n"
        "Le protocole suit."
    )

    repaired = repair_section_boundaries(broken)

    assert repaired == (
        "La robustesse doit être démontrée.\n\n# Méthode expérimentale\n\n"
        "Le protocole suit."
    )
    assert [section.title for section in parse_sections(repaired)] == [
        "Préambule",
        "Méthode expérimentale",
    ]


def test_plain_consultant_format_cannot_be_replaced_by_markdown():
    original = "Contexte du projet\n\nLe système doit rester robuste."
    rewritten = "# Contexte du projet\n\nLe **système** doit rester robuste."

    assert _match_editorial_format(original, rewritten) == (
        "Contexte du projet\n\nLe système doit rester robuste."
    )


def test_extraction_markers_are_removed_without_creating_markdown():
    raw = "[SECTION : Contexte du projet]\nLe projet évolue.\n\n[PAGE 2]\nSuite du texte."
    cleaned = improvement_service._clean_extracted_document_text(raw)
    assert "[SECTION" not in cleaned
    assert "[PAGE" not in cleaned
    assert "#" not in cleaned
    assert cleaned.startswith("Contexte du projet\n\n")


def test_pdf_table_of_contents_is_not_used_as_document_sections():
    extracted = (
        "Page de garde\n\nTable des matières\n"
        "1. Contexte ........................................ 5\n"
        "1.1. Objectif ...................................... 6\n"
        "2. Méthode ......................................... 9\n"
        "En-tête répétée\nPied de page\n"
        "1. Contexte\nLe projet contient ici son texte complet sur plusieurs paragraphes.\n\n"
        "1.1. Objectif\nL'objectif scientifique est décrit ici.\n\n"
        "2. Méthode\nLa méthode expérimentale est détaillée ici."
    )

    cleaned = improvement_service._clean_extracted_document_text(extracted)
    sections = parse_sections(cleaned)

    assert "Table des matières" not in cleaned
    assert "texte complet" in cleaned
    assert [section.title for section in sections][-3:] == ["Contexte", "Objectif", "Méthode"]
    assert not any("...." in section.title for section in sections)


def test_proposed_research_source_is_actionable_and_opens_publication_site():
    sources = improvement_service._public_research_sources(
        [
            {
                "candidate_id": "C1",
                "title": "Publication expérimentale",
                "doi": "10.1000/example",
                "url": "https://publisher.example/article.pdf",
                "pdf_url": "https://publisher.example/article.pdf",
                "consultant_decision": "proposed",
            }
        ]
    )

    assert sources[0]["consultant_decision"] == "pending"
    assert sources[0]["site_url"] == "https://doi.org/10.1000/example"
    assert sources[0]["pdf_url"] == "https://publisher.example/article.pdf"


def test_uploaded_word_cir_is_extracted_as_plain_structured_text():
    docx_module = pytest.importorskip("docx")
    db = _database()
    user, project = _project(db)
    word = docx_module.Document()
    word.add_heading("Contexte du projet", level=1)
    word.add_paragraph("Le projet vise à anticiper une dérive thermique.")
    word.add_heading("Verrou scientifique", level=1)
    word.add_paragraph("La généralisation à de nouveaux équipements reste incertaine.")
    buffer = BytesIO()
    word.save(buffer)
    payload = buffer.getvalue()
    stored = StoredDocument(
        project_id=project.id,
        filename="cir_test.docx",
        stored_filename="cir_test.docx",
        file_path="db://documents/test-word",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_size=len(payload),
        document_type="Word",
        upload_status="importé_en_base",
        file_data=payload,
        file_sha256="0" * 64,
        storage_mode="database",
    )
    db.add(stored)
    db.commit()
    db.refresh(stored)

    session = improvement_service.create_session(
        db,
        project,
        user_id=user.id,
        source_document_id=stored.id,
        target_scope="section",
    )
    original = next(row for row in session.versions if row.status == "original")
    assert "[SECTION" not in original.content
    assert "#" not in original.content
    assert [row["title"] for row in session.context_json["sections"]] == [
        "Contexte du projet",
        "Verrou scientifique",
    ]


def test_explicit_research_waits_for_source_validation(monkeypatch):
    db = _database()
    user, project = _project(db)
    monkeypatch.setattr(
        improvement_service,
        "_AGENT",
        EnnoAmeliorationAgent(writer=FakeWriter()),
    )
    monkeypatch.setattr(
        improvement_service,
        "_start_typed_research_inside_improvement",
        lambda *args, **kwargs: {
            "ok": True,
            "guided_session_id": "scholar-test",
            "assistant_message": "Sources candidates prêtes pour validation.",
        },
    )
    session = improvement_service.create_session(
        db,
        project,
        user_id=user.id,
        source_text="# Verrou\nUne incertitude reste à lever.",
        target_scope="section",
    )
    session, candidate = improvement_service.send_message(
        db,
        project,
        session.id,
        message="Recherche de nouvelles publications puis améliore ce verrou.",
        target_scope="section",
        target_section_id=session.context_json["sections"][0]["section_id"],
    )
    assert candidate is None
    assert session.state == "awaiting_evidence"


def test_validated_source_rewrite_never_starts_another_handoff(monkeypatch):
    import agents.EnnoAmelioration.application.agent as agent_module
    import agents.EnnoScholar.state_of_art.existing_review_enrichment_service as scholar_editor

    db = _database()
    user, project = _project(db)
    monkeypatch.setattr(
        improvement_service,
        "_AGENT",
        EnnoAmeliorationAgent(writer=FakeWriter()),
    )

    def unexpected_handoff(*args, **kwargs):
        raise AssertionError("A new research handoff must not be started")

    monkeypatch.setattr(
        improvement_service,
        "_start_typed_research_inside_improvement",
        unexpected_handoff,
    )
    monkeypatch.setattr(
        agent_module,
        "scholar_context",
        lambda *args, **kwargs: {
            "available": True,
            "agent": "EnnoScholar",
            "evidence": [{"citation_id": "A1", "title": "Source validée"}],
        },
    )

    def fake_scholar_addition(**kwargs):
        section = kwargs["sections"][0]
        return (
            [
                {
                    "section_id": section["section_id"],
                    "anchor": "Analyse initiale.",
                    "content": (
                        "La publication validée précise les conditions expérimentales "
                        "et les limites de transférabilité qui renforcent cette analyse [A1]."
                    ),
                    "citations": ["A1"],
                }
            ],
            {
                "agent": "EnnoScholar",
                "strategy": "scientific_anchored_additions_only",
            },
        )

    monkeypatch.setattr(
        scholar_editor,
        "generate_state_of_art_additions",
        fake_scholar_addition,
    )
    session = improvement_service.create_session(
        db,
        project,
        user_id=user.id,
        source_text=(
            "1.3.1. Analyse de l'état de l'art\nAnalyse initiale.\n\n"
            "1.3.2. Verrous scientifiques\nVerrous inchangés."
        ),
        target_scope="section",
    )

    updated, candidate = improvement_service.send_message(
        db,
        project,
        session.id,
        message=(
            "Réécris maintenant exclusivement la section 1.3.1 Analyse de l'état de l'art "
            "avec uniquement les publications validées dont les Article Cards sont disponibles, "
            "sans lancer de nouvelle recherche."
        ),
        target_scope="full_document",
    )

    assert candidate is not None
    assert updated.state == "candidate_ready"
    assert updated.target_section_title == "Analyse de l'état de l'art"
    assert "Verrous inchangés." in candidate.content


def test_research_sources_are_decided_inside_improvement(monkeypatch):
    db = _database()
    user, project = _project(db)
    session = improvement_service.create_session(
        db,
        project,
        user_id=user.id,
        source_text="Verrou scientifique\n\nUne incertitude reste à lever.",
        target_scope="section",
    )
    session.context_json = {
        **dict(session.context_json or {}),
        "scholar_handoff": {"guided_session_id": "guided-test"},
    }
    db.commit()

    import services.guided_research_service as guided_service

    monkeypatch.setattr(guided_service, "decide_guided_research_sources", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(
        guided_service,
        "read_guided_research_session",
        lambda *args, **kwargs: {
            "session": {"state": "brief_parsed"},
            "artifacts": {
                "selected_sources": [
                    {
                        "candidate_id": "C1",
                        "title": "Publication test",
                        "year": 2025,
                        "consultant_decision": "accepted",
                        "fulltext_preparation": {
                            "usable_as_scientific_evidence": True,
                            "article_card_ready": True,
                            "ready_for_writing": True,
                        },
                    }
                ]
            },
        },
    )
    updated = improvement_service.decide_research_sources(
        db,
        project,
        session.id,
        candidate_ids=["C1"],
        decision="accepted",
    )
    assert updated.state == "evidence_ready"
    assert updated.context_json["research_sources"][0]["title"] == "Publication test"
    assert updated.context_json["research_sources"][0]["article_card_ready"] is True


def test_conversation_evidence_bundle_excludes_stale_project_cards(monkeypatch):
    db = _database()
    user, project = _project(db)
    session = improvement_service.create_session(
        db,
        project,
        user_id=user.id,
        source_text="Analyse scientifique\n\nTexte.",
        target_scope="section",
    )
    session.context_json = {
        **dict(session.context_json or {}),
        "accepted_research_sources": [
            {
                "candidate_id": "SAR-1",
                "title": "Publication SAR validée",
                "consultant_decision": "accepted",
                "article_id": 101,
                "article_card_ready": True,
            }
        ],
    }
    db.commit()

    import services.article_card_builder as card_builder

    monkeypatch.setattr(
        card_builder,
        "get_article_cards_payload",
        lambda *args, **kwargs: {
            "cards": [
                {"article_id": 101, "title": "Publication SAR validée"},
                {"article_id": 999, "title": "Ancienne publication hors conversation"},
            ]
        },
    )

    bundle = improvement_service._accepted_evidence_bundle(db, project, session)

    assert bundle["article_ids"] == [101]
    assert [source["title"] for source in bundle["sources"]] == [
        "Publication SAR validée"
    ]


def test_evidence_bundle_resumes_the_guided_research_scope(monkeypatch):
    db = _database()
    user, project = _project(db)
    session = improvement_service.create_session(
        db,
        project,
        user_id=user.id,
        source_text="Verrou scientifique\n\nUne incertitude reste a lever.",
        target_scope="section",
    )
    session.context_json = {
        **dict(session.context_json or {}),
        "scholar_handoff": {
            "guided_session_id": "guided-current",
            "corpus_scope_id": session.id,
        },
        # Statut public ancien : la source de verite Guided Research doit le
        # remplacer au prochain tour de redaction.
        "accepted_research_sources": [
            {
                "candidate_id": "C1",
                "title": "Publication validee",
                "consultant_decision": "accepted",
                "article_id": 101,
                "article_card_ready": False,
            }
        ],
    }
    db.commit()

    import services.article_card_builder as card_builder
    import services.guided_research_service as guided_service

    monkeypatch.setattr(
        guided_service,
        "read_guided_research_session",
        lambda *args, **kwargs: {
            "session": {"state": "ready_to_write"},
            "artifacts": {
                "selected_sources": [
                    {
                        "candidate_id": "C1",
                        "title": "Publication validee",
                        "consultant_decision": "accepted",
                        "fulltext_preparation": {
                            "article_id": 101,
                            "ready_for_writing": True,
                            "article_card_ready": True,
                        },
                    }
                ]
            },
        },
    )
    requested_scopes = []

    def cards_for_scope(project_arg, scope_id=None):
        requested_scopes.append(scope_id)
        return {
            "cards": (
                [{"article_id": 101, "title": "Publication validee"}]
                if scope_id == "guided-current"
                else []
            )
        }

    monkeypatch.setattr(card_builder, "get_article_cards_payload", cards_for_scope)

    bundle = improvement_service._accepted_evidence_bundle(db, project, session)

    assert requested_scopes[0] == "guided-current"
    assert bundle["corpus_scope_id"] == "guided-current"
    assert bundle["article_ids"] == [101]
    assert bundle["sources"][0]["article_card_ready"] is True


def test_each_improvement_conversation_gets_a_distinct_private_scholar_run():
    db = _database()
    _, project = _project(db)
    from services.guided_research_source_preparation_service import (
        _get_or_create_improvement_scholar_run,
    )

    first = _get_or_create_improvement_scholar_run(
        db,
        project,
        "conversation-thermo",
        "guided-thermo",
    )
    second = _get_or_create_improvement_scholar_run(
        db,
        project,
        "conversation-radar",
        "guided-radar",
    )
    first_again = _get_or_create_improvement_scholar_run(
        db,
        project,
        "conversation-thermo",
        "guided-thermo-2",
    )

    assert first.id != second.id
    assert first_again.id == first.id
    assert first.status == second.status == "improvement_corpus"


def test_improvement_run_never_becomes_ennoscholar_current_selection():
    db = _database()
    _, project = _project(db)
    from services.scholar_selection_scope import get_current_scholar_run

    canonical = ScholarRun(project_id=project.id, status="completed", raw_result_json={})
    private = ScholarRun(
        project_id=project.id,
        status="improvement_corpus",
        raw_result_json={"corpus_scope_id": "conversation-radar"},
    )
    db.add(canonical)
    db.commit()
    db.add(private)
    db.commit()

    assert get_current_scholar_run(db, project).id == canonical.id


def test_long_full_document_is_rewritten_in_complete_batches():
    from agents.EnnoAmelioration.domain.models import ImprovementRequest

    text = "\n\n".join(
        f"# Section {index}\n" + (f"Contenu scientifique {index}. " * 500)
        for index in range(1, 5)
    )
    writer = CountingWriter()
    agent = EnnoAmeliorationAgent(writer=writer)
    routing = understand_instruction("Améliore tout le document.", TargetScope.FULL_DOCUMENT)
    request = ImprovementRequest(
        instruction="Améliore tout le document.",
        full_text=text,
        target_text=text,
        target_scope=TargetScope.FULL_DOCUMENT,
        sections=parse_sections(text),
    )
    improved_target, improved_full, generation = agent._rewrite_target(request, routing, [], {})
    assert writer.calls > 1
    assert all(f"# Section {index}" in improved_full for index in range(1, 5))
    assert generation["call_count"] == writer.calls
    assert improved_target == improved_full


def test_long_section_with_subsections_is_rewritten_in_complete_batches():
    target = "\n\n".join(
        f"## 1.3.1.{index}. Sous-section {index}\n"
        + (f"Preuve scientifique {index}. " * 600)
        for index in range(1, 4)
    )
    full_text = target + "\n\n## 1.3.2. Verrous\nTexte à préserver."
    writer = CountingWriter()
    agent = EnnoAmeliorationAgent(writer=writer)
    routing = understand_instruction(
        "Réécris la section avec les publications validées.",
        TargetScope.SECTION,
    )
    request = ImprovementRequest(
        instruction="Réécris la section avec les publications validées.",
        full_text=full_text,
        target_text=target,
        target_scope=TargetScope.SECTION,
        target_section_title="Analyse de l'état de l'art",
        sections=parse_sections(full_text),
    )

    improved_target, improved_full, generation = agent._rewrite_target(
        request,
        routing,
        [],
        {},
    )

    assert writer.calls > 1
    assert generation["call_count"] == writer.calls
    assert all(f"Sous-section {index}" in improved_target for index in range(1, 4))
    assert "## 1.3.2. Verrous\nTexte à préserver." in improved_full


def test_conservation_guard_rejects_destructive_summary_and_unknown_citation():
    original = (
        "1.3.1. Analyse de l'état de l'art\n"
        + ("Le protocole conserve les faits, les limites et les résultats documentés. " * 40)
        + "\nFigure 7 présente le résultat de 95 %.\n"
        + "1 Auteur, Titre scientifique, 2021.\n"
    )
    proposal = (
        "1.3.1. Analyse de l'état de l'art\n"
        "La littérature montre globalement de bonnes performances [A12]."
    )

    issues = validate_conservative_revision(
        original,
        proposal,
        allowed_citation_ids={"A1", "A6"},
        enrichment_requested=True,
    )

    assert any(issue.startswith("contraction_excessive") for issue in issues)
    assert any(issue.startswith("references_perdues") for issue in issues)
    assert any(issue.startswith("renvois_visuels_perdus") for issue in issues)
    assert any(issue.startswith("mesures_perdues") for issue in issues)
    assert "citations_non_autorisees:A12" in issues


def test_agent3_merges_scholar_addition_without_rewriting_existing_text():
    target = (
        "1.3.1. Analyse de l'état de l'art\n"
        "Introduction scientifique à conserver.\n\n"
        "1.3.1.1. Données SAR\n"
        "Le corpus MSTAR est utilisé dans les travaux existants.\n\n"
        "1.3.1.2. Limites\n"
        "La transférabilité demeure incertaine."
    )
    sections = parse_sections(target)
    data_section = next(row for row in sections if row.title == "Données SAR")
    addition = (
        "Une comparaison complémentaire documente le protocole et ses limites "
        "dans les seules conditions évaluées [A1]."
    )

    merged = EnnoAmeliorationAgent._merge_scholar_additions(
        target,
        sections,
        [
            {
                "section_id": data_section.section_id,
                "anchor": "Le corpus MSTAR est utilisé dans les travaux existants.",
                "content": addition,
            }
        ],
    )

    assert "Introduction scientifique à conserver." in merged
    assert "Le corpus MSTAR est utilisé dans les travaux existants." in merged
    assert addition in merged
    assert "1.3.1.2. Limites\nLa transférabilité demeure incertaine." in merged


def test_state_of_art_revision_is_routed_to_ennoscholar_when_cards_are_ready():
    request = ImprovementRequest(
        instruction="Renforce l'argumentation avec les Article Cards validées.",
        full_text="1.3.1. Analyse de l'état de l'art\nTexte.",
        target_text="1.3.1. Analyse de l'état de l'art\nTexte.",
        target_scope=TargetScope.SECTION,
        target_section_title="Analyse de l'état de l'art",
    )
    routing = understand_instruction(
        "Renforce l'argumentation avec les Article Cards validées.",
        TargetScope.SECTION,
    )

    assert EnnoAmeliorationAgent._is_state_of_art_revision(
        request,
        routing,
        {
            "scholar": {
                "available": True,
                "evidence": [{"citation_id": "A1"}],
            }
        },
    )


def test_ennoscholar_generates_only_anchored_scientific_additions():
    from agents.EnnoScholar.state_of_art.existing_review_enrichment_service import (
        generate_state_of_art_additions,
    )

    target = (
        "1.3.1.1. Données SAR\n"
        "Le corpus existant couvre des conditions expérimentales limitées."
    )
    section = parse_sections(target)[0]
    addition = (
        "La publication validée complète cette analyse en distinguant le protocole "
        "d'apprentissage du protocole d'évaluation. Elle décrit aussi les conditions "
        "dans lesquelles le résultat est observé, les limites du corpus et les "
        "précautions nécessaires avant toute transposition au contexte opérationnel. "
        "Cette preuve soutient donc une comparaison méthodologique, sans démontrer à "
        "elle seule la transférabilité au projet [A1]."
    )
    fake_llm = FakeScholarLLM(
        {
            "additions": [
                {
                    "section_id": section.section_id,
                    "anchor": "Le corpus existant couvre des conditions expérimentales limitées.",
                    "content": addition,
                    "citations": ["A1"],
                }
            ],
            "uncovered_sections": [],
        }
    )

    additions, meta = generate_state_of_art_additions(
        target_text=target,
        sections=[section.model_dump(mode="json")],
        instruction="Renforce l'argumentation avec les preuves validées.",
        project_name="AI-RADAR",
        project_domain="SAR",
        evidence_rows=[
            {
                "citation_id": "A1",
                "title": "Publication expérimentale",
                "method": "Protocole d'apprentissage et d'évaluation séparés.",
                "results": "Résultat observé dans le corpus étudié.",
                "limits": "Transférabilité non démontrée.",
            }
        ],
        llm=fake_llm,
    )

    assert fake_llm.calls == 1
    assert additions[0]["content"] == addition
    assert meta["agent"] == "EnnoScholar"
    assert meta["strategy"] == "scientific_anchored_additions_only"


def test_agent3_writes_state_of_art_with_scholar_used_only_as_evidence(monkeypatch):
    import agents.EnnoAmelioration.application.agent as agent_module
    import agents.EnnoScholar.state_of_art.existing_review_enrichment_service as scholar_editor

    db = _database()
    _, project = _project(db)
    target = (
        "1.3.1. Analyse de l'état de l'art\n"
        "Le texte consultant doit rester présent.\n\n"
        "1.3.1.1. Données SAR\n"
        "Le corpus public possède des conditions de validité limitées."
    )
    sections = parse_sections(target)
    data_section = next(row for row in sections if row.title == "Données SAR")
    addition = (
        "La preuve validée précise le protocole expérimental, distingue les données "
        "d'apprentissage des données d'évaluation et documente les limites observées. "
        "Elle renforce ainsi la comparaison, tout en laissant explicitement ouverte "
        "la question de la transférabilité au projet [A1]."
    )
    monkeypatch.setattr(
        agent_module,
        "scholar_context",
        lambda *args, **kwargs: {
            "available": True,
            "agent": "EnnoScholar",
            "evidence": [{"citation_id": "A1", "title": "Source validée"}],
        },
    )
    monkeypatch.setattr(
        scholar_editor,
        "generate_state_of_art_additions",
        lambda **kwargs: (
            [
                {
                    "section_id": data_section.section_id,
                    "anchor": "Le corpus public possède des conditions de validité limitées.",
                    "content": addition,
                    "citations": ["A1"],
                }
            ],
            {
                "agent": "EnnoScholar",
                "strategy": "scientific_anchored_additions_only",
            },
        ),
    )
    generic_writer = CountingWriter()
    request = ImprovementRequest(
        instruction="Améliore cette section sans supprimer le texte existant.",
        full_text=target,
        target_text=target,
        target_scope=TargetScope.SECTION,
        target_section_title="Analyse de l'état de l'art",
        project_name=project.project_name,
        project_domain=project.domain_label or "",
    )

    result = EnnoAmeliorationAgent(writer=generic_writer).improve(
        db,
        project,
        request,
    )

    assert result.ok
    assert result.state.value == "candidate_ready"
    assert generic_writer.calls == 1
    assert "Le texte consultant doit rester présent." in result.improved_target
    assert "Le corpus public possède des conditions de validité limitées." in result.improved_target
    assert addition not in result.improved_target
    assert result.generation["provider"] == "fake"
    assert result.generation["agent"] == "EnnoAmelioration"
    assert result.generation["writing_owner"] == "EnnoAmelioration"
