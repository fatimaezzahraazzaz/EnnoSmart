from __future__ import annotations

import difflib
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import case, func
from sqlalchemy.orm import Session, undefer

from agents.EnnoAmelioration import EnnoAmeliorationAgent
from agents.EnnoAmelioration.application.section_parser import (
    infer_section_from_instruction,
    parse_sections,
    repair_section_boundaries,
    resolve_target,
)
from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.domain.models import (
    ImprovementRequest,
    ImprovementState,
    TargetScope,
)
from db.models import (
    Document,
    ImprovementMessage,
    ImprovementSession,
    ImprovementVersion,
    Project,
)


_AGENT: EnnoAmeliorationAgent | None = None


def get_improvement_agent() -> EnnoAmeliorationAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = EnnoAmeliorationAgent()
    return _AGENT


def _utcnow() -> datetime:
    return datetime.utcnow()


def _new_id() -> str:
    return str(uuid.uuid4())


def _extract_document_payload(
    db: Session,
    project: Project,
    document_id: int,
) -> tuple[Document, str, dict[str, Any]]:
    document = (
        db.query(Document)
        .options(undefer(Document.file_data))
        .filter(Document.id == document_id, Document.project_id == project.id)
        .first()
    )
    if document is None:
        raise LookupError("Document introuvable dans ce projet.")

    suffix = Path(document.filename or document.stored_filename or "document.txt").suffix.lower()
    file_bytes = bytes(document.file_data or b"")
    if suffix in {".txt", ".md"} and file_bytes:
        return document, file_bytes.decode("utf-8", errors="ignore"), {
            "version": "ennoamelioration_document_structure_v1",
            "source_format": suffix.lstrip("."),
            "asset_count": 0,
            "preservation": {
                "source_binary_immutable": True,
                "layout_master": "original_document",
                "revision_mode": "text_patches_only",
                "protected_asset_blocks": False,
            },
        }

    if not file_bytes and document.file_path and not str(document.file_path).startswith("db://"):
        path = Path(document.file_path)
        if path.exists():
            file_bytes = path.read_bytes()
    if not file_bytes:
        raise ValueError("Le document ne contient aucune donnée extractible.")

    with tempfile.TemporaryDirectory(prefix="ennoamelioration_") as temp_dir:
        temp_path = Path(temp_dir) / (document.filename or f"document{suffix}")
        temp_path.write_bytes(file_bytes)
        structure: dict[str, Any] = {}
        try:
            from agents.EnnoAmelioration.application.document_structure_service import (
                extract_layout_preserving_document,
            )

            text, structure = extract_layout_preserving_document(temp_path)
            if not text.strip():
                raise ValueError("L'extraction structurée n'a retourné aucun bloc de texte.")
        except Exception:
            try:
                from modules.extraction.router import extract

                result = extract(temp_path, vision_mode="text_only", formula_mode="off")
                text = "\n\n".join(
                    str(chunk).strip()
                    for chunk in result.text_chunks
                    if str(chunk).strip()
                )
                structure = {
                    "version": "ennoamelioration_document_structure_v1",
                    "source_format": suffix.lstrip("."),
                    "asset_count": 0,
                    "extraction_engine": "generic_router_fallback",
                    "preservation": {
                        "source_binary_immutable": True,
                        "layout_master": "original_document",
                        "revision_mode": "text_patches_only",
                        "protected_asset_blocks": False,
                    },
                }
                if not text.strip():
                    raise ValueError("Le moteur principal n'a retourné aucun bloc de texte.")
            except Exception:
                from services.cir_memory_service import extract_text_from_file

                text = extract_text_from_file(temp_path)
                structure = {
                    "version": "ennoamelioration_document_structure_v1",
                    "source_format": suffix.lstrip("."),
                    "asset_count": 0,
                    "extraction_engine": "plain_text_fallback",
                    "preservation": {
                        "source_binary_immutable": True,
                        "layout_master": "original_document",
                        "revision_mode": "text_patches_only",
                        "protected_asset_blocks": False,
                    },
                }
    if not text.strip():
        raise ValueError("L'extraction du document n'a produit aucun texte exploitable.")
    return document, _clean_extracted_document_text(text), structure


def _extract_document_text(db: Session, project: Project, document_id: int) -> tuple[Document, str]:
    """Compatibilité interne : expose le texte sans perdre le payload structuré."""

    document, text, _ = _extract_document_payload(db, project, document_id)
    return document, text


def _clean_extracted_document_text(text: str) -> str:
    """Retire les balises techniques d'extraction sans fabriquer de Markdown."""

    value = str(text or "").replace("\r\n", "\n")
    value = re.sub(
        r"(?mi)^\[SECTION\s*:\s*(?P<title>[^\]]+)\]\s*$",
        lambda match: match.group("title").strip() + "\n",
        value,
    )
    value = re.sub(r"(?mi)^\[PAGE\s+\d+\](?:\s*\[[^\]]+\])?\s*$", "", value)
    value = re.sub(r"(?mi)^\[(?:DOCX|DOCUMENT|SOURCE)[^\]]*\]\s*$", "", value)
    value = re.sub(r"\n[ \t]+\n", "\n\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return _strip_table_of_contents(value).strip()


def _strip_table_of_contents(text: str) -> str:
    """Retire un sommaire paginé sans retirer les vraies sections du CIR.

    Les PDF Word contiennent souvent plusieurs pages dont les lignes pointillées
    ressemblent à des titres. Elles formaient alors de petites « sections » de
    quelques caractères avant le corps réel du document.
    """

    source = str(text or "")
    label = re.search(
        r"(?im)^[ \t]*(?:table\s+des\s+mati[eè]res|sommaire|table\s+of\s+contents)[ \t]*$",
        source,
    )
    if not label:
        return source
    window_end = min(len(source), label.end() + 45000)
    window = source[label.end():window_end]
    entries = list(
        re.finditer(
            r"(?m)^[ \t]*(?:\d+(?:\.\d+)*\.?|[IVXLC]+\.?)"
            r"[ \t]+[^\n]{3,}?(?:\.{3,}|…{2,})[ \t]*\d+(?:\s*[-–]\s*\d+)?[ \t]*$",
            window,
            flags=re.I,
        )
    )
    if len(entries) < 3:
        return source
    search_from = label.end() + entries[-1].end()
    body_heading = re.search(
        r"(?m)^[ \t]*\d+(?:\.\d+)*\.[ \t]+[^\n.][^\n]{2,180}[ \t]*$",
        source[search_from: min(len(source), search_from + 16000)],
    )
    if not body_heading:
        return source
    body_start = search_from + body_heading.start()
    return source[:label.start()].rstrip() + "\n\n" + source[body_start:].lstrip()


def _is_direct_pdf_url(value: Any) -> bool:
    url = str(value or "").strip()
    if not url:
        return False
    path = urlparse(url).path.casefold()
    return path.endswith(".pdf") or "/pdf/" in path or path.endswith("/document")


def _publication_site_url(source: dict[str, Any]) -> str | None:
    """Choisit la page de publication, jamais le téléchargement PDF si évitable."""

    rows = [source]
    rows.extend(row for row in (source.get("raw_payloads") or []) if isinstance(row, dict))
    for row in rows:
        for key in (
            "landing_page_url",
            "landing_url",
            "html_url",
            "publication_url",
            "documentation_url",
            "repository_url",
        ):
            candidate = str(row.get(key) or "").strip()
            if candidate.startswith(("http://", "https://")) and not _is_direct_pdf_url(candidate):
                return candidate

    doi = str(source.get("doi") or "").strip()
    if doi:
        doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
        return f"https://doi.org/{doi}"

    for row in rows:
        paper_id = str(row.get("paper_id") or "").strip()
        if "arxiv.org/" in paper_id:
            return re.sub(r"/pdf/", "/abs/", paper_id).removesuffix(".pdf")
        candidate = str(row.get("url") or row.get("resolved_url") or "").strip()
        if not candidate.startswith(("http://", "https://")):
            continue
        if "arxiv.org/pdf/" in candidate:
            return candidate.replace("/pdf/", "/abs/").removesuffix(".pdf")
        if re.search(r"https?://(?:[^/]+\.)?hal\.science/.+/document$", candidate, re.I) or re.search(
            r"https?://hal\.archives-ouvertes\.fr/.+/document$", candidate, re.I
        ):
            return candidate.rsplit("/document", 1)[0]
        if not _is_direct_pdf_url(candidate):
            return candidate
    return None


def _sections_payload(text: str) -> list[dict[str, Any]]:
    return [section.model_dump(mode="json") for section in parse_sections(text)]


def _add_message(
    db: Session,
    session_id: str,
    role: str,
    content: str,
    *,
    intent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ImprovementMessage:
    row = ImprovementMessage(
        id=_new_id(),
        session_id=session_id,
        role=role,
        content=content,
        intent=intent,
        metadata_json=metadata or {},
        created_at=_utcnow(),
    )
    db.add(row)
    return row


def _version_content(session: ImprovementSession) -> str:
    if session.active_version_id:
        for version in session.versions:
            if version.id == session.active_version_id:
                return str(version.content or "")
    if session.versions:
        accepted = [row for row in session.versions if row.status in {"accepted", "original"}]
        return str((accepted[-1] if accepted else session.versions[-1]).content or "")
    return ""


def _working_version(session: ImprovementSession) -> ImprovementVersion | None:
    """Utilise la dernière proposition pour une demande d'ajustement, sans la publier."""

    candidates = [row for row in session.versions if row.status == "candidate"]
    if candidates:
        return max(candidates, key=lambda row: row.version_number)
    if session.active_version_id:
        active = next((row for row in session.versions if row.id == session.active_version_id), None)
        if active is not None:
            return active
    return session.versions[-1] if session.versions else None


def _make_diff(before: str, after: str) -> dict[str, Any]:
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="version_active",
            tofile="proposition",
            lineterm="",
            n=3,
        )
    )
    matcher = difflib.SequenceMatcher(a=before, b=after)
    changes = []
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changes.append(
            {
                "operation": tag,
                "before": before[a0:a1],
                "after": after[b0:b1],
            }
        )
    return {
        "unified": "\n".join(lines),
        "changes": changes[:300],
        "similarity": round(matcher.ratio(), 4),
        "before_chars": len(before),
        "after_chars": len(after),
    }


_RESEARCH_QUERY_STOPWORDS = {
    "afin", "ainsi", "alors", "analyse", "article", "articles", "avec",
    "cette", "comme", "dans", "depuis", "des", "donc", "elle", "elles",
    "entre", "etat", "faire", "faut", "leur", "leurs", "mais", "nous",
    "pour", "projet", "recherche", "scientifique", "section", "selon",
    "sont", "sous", "texte", "tout", "toute", "toutes", "tous", "une",
    "vers", "vous", "from", "into", "that", "the", "their", "this", "with",
}


def _publication_year_ceiling(instruction: str) -> int | None:
    """Extrait une borne temporelle seulement si la demande parle de date/publication."""

    value = str(instruction or "")
    temporal_cues = re.compile(
        r"(?i)publication|publi[ée]e?|post[ée]rieur|ant[ée]rieur|avant|jusqu|"
        r"au plus tard|temporalit[ée]|31\s+d[ée]cembre"
    )
    if not temporal_cues.search(value):
        return None
    years = [
        int(year)
        for year in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", value)
    ]
    return max(years) if years else None


def _salient_query_terms(value: str, *, limit: int = 8) -> list[str]:
    normalized = re.sub(r"[^A-Za-zÀ-ÿ0-9+/-]+", " ", str(value or "").casefold())
    counts: dict[str, int] = {}
    order: list[str] = []
    for token in normalized.split():
        token = token.strip("-/")
        if len(token) < 4 or token in _RESEARCH_QUERY_STOPWORDS or token.isdigit():
            continue
        if token not in counts:
            order.append(token)
            counts[token] = 0
        counts[token] += 1
    ranked = sorted(order, key=lambda token: (-counts[token], order.index(token)))
    return ranked[:limit]


def _instruction_topics(instruction: str) -> list[str]:
    """Repère les thèmes énumérés par le consultant sans vocabulaire métier imposé."""

    value = " ".join(str(instruction or "").split())
    matches = re.findall(
        r"(?i)(?:th[èe]mes?|notamment|prioritairement)\s*(?:suivants?)?\s*:\s*([^.!?]+)",
        value,
    )
    output: list[str] = []
    for match in matches:
        for item in re.split(r"\s*[,;]\s*|\s+et\s+", match):
            cleaned = item.strip(" :-")
            if 3 <= len(cleaned) <= 180 and cleaned.casefold() not in {
                row.casefold() for row in output
            }:
                output.append(cleaned)
    return output


def _focused_research_requests(
    project: Project,
    request: ImprovementRequest,
) -> list[dict[str, Any]]:
    """Décompose une cible large à partir de ses vrais sous-titres et de la demande."""

    parsed = parse_sections(request.target_text)
    topics: list[tuple[str, str]] = []
    if len(parsed) > 1:
        for index, section in enumerate(parsed):
            has_child = any(
                following.level > section.level
                for following in parsed[index + 1:index + 2]
            )
            if has_child:
                continue
            topics.append((section.title, section.content))
    for topic in _instruction_topics(request.instruction):
        if topic.casefold() not in {title.casefold() for title, _ in topics}:
            topics.append((topic, ""))
    if not topics:
        topics = [(
            request.target_section_title or "passage scientifique à renforcer",
            request.target_text,
        )]

    ceiling = _publication_year_ceiling(request.instruction)
    context = [
        value
        for value in (
            request.project_domain,
            request.project_name,
            request.target_section_title,
        )
        if str(value or "").strip()
    ]
    payload: list[dict[str, Any]] = []
    seen: set[str] = set()
    for title, content in topics:
        query_parts = [
            str(request.project_domain or "").strip(),
            str(title or "").strip(),
            *_salient_query_terms(content),
            "experimental validation methods results limitations",
        ]
        query = " ".join(part for part in query_parts if part).strip()
        key = re.sub(r"\W+", " ", query.casefold()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        row: dict[str, Any] = {
            "query": query[:900],
            "entity_name": str(title or "").strip()[:300],
            "entity_type": "scientific_concept",
            "query_kind": "scientific_evidence",
            "require_direct_evidence": False,
            "section_ids": [request.target_section_id] if request.target_section_id else [],
            "section_titles": [title] if title else [],
            "requested_dimensions": [
                "methods",
                "experimental protocols",
                "quantitative results",
                "contradictory evidence",
                "validity conditions",
                "limitations",
                "transferability",
            ],
            "target_context_dimensions": context,
            "source_preferences": ["scientific_articles"],
        }
        if ceiling is not None:
            row["publication_year_max"] = ceiling
        payload.append(row)
        if len(payload) >= 8:
            break
    return payload


def _start_scholar_handoff(
    db: Session,
    project: Project,
    session: ImprovementSession,
    request: ImprovementRequest,
) -> dict[str, Any]:
    """Délègue la recherche à EnnoScholar sans lui déléguer la révision finale."""

    from services.guided_research_service import (
        create_guided_research_session,
        read_guided_research_session,
        run_guided_research_requests,
        send_guided_research_message,
    )

    target_mode = (
        "full_cir_improvement"
        if request.target_scope == TargetScope.FULL_DOCUMENT
        else "section_improvement"
    )
    scholar_session = create_guided_research_session(
        db,
        project,
        user_id=session.created_by_user_id,
        target_mode=target_mode,
        entry_module="ennoamel",
        context_updates={
            "improvement_session_id": session.id,
            "corpus_scope_id": session.id,
            "operating_mode": "improvement_conversation",
            "corpus_isolation_policy": "one_improvement_conversation_one_corpus",
        },
    )
    scholar_session_id = str(scholar_session.get("session_id") or "")
    delegated_prompt = f"""Recherche scientifique déléguée par EnnoAmelioration.

Projet ou dossier de la conversation : {request.project_name or 'non précisé'}
Domaine déduit de la conversation : {request.project_domain or 'non précisé'}
Cible à renforcer : {request.target_section_title or request.target_scope.value}
Demande du consultant : {request.instruction}

Passage concerné :
{request.target_text[:14000]}

Lance une recherche scientifique ciblée sur les affirmations, méthodes ou verrous à renforcer. Présente d'abord les sources candidates pour validation du consultant. Ne rédige pas encore la nouvelle version du texte et n'utilise aucune source non validée comme preuve."""
    response = send_guided_research_message(
        db,
        project,
        session_id=scholar_session_id,
        message=delegated_prompt,
    )
    snapshot = read_guided_research_session(db, scholar_session_id)
    sources = _public_research_sources(
        list((snapshot.get("artifacts") or {}).get("selected_sources") or [])
    )
    fallback_requests: list[dict[str, Any]] = []
    fallback_result: dict[str, Any] | None = None
    if not sources:
        fallback_requests = _focused_research_requests(project, request)
        if fallback_requests:
            fallback_result = run_guided_research_requests(
                db,
                project,
                session_id=scholar_session_id,
                requests_payload=fallback_requests,
            )
            snapshot = read_guided_research_session(db, scholar_session_id)
            sources = _public_research_sources(
                list((snapshot.get("artifacts") or {}).get("selected_sources") or [])
            )
    return {
        "ok": True,
        "guided_session_id": scholar_session_id,
        "corpus_scope_id": session.id,
        "state": response.get("state"),
        "next_action": response.get("next_action"),
        "assistant_message": (
            f"La recherche ciblée a proposé {len(sources)} source(s) candidate(s). "
            "Consultez-les dans l'onglet Sources, puis gardez uniquement celles qui doivent étayer la révision."
            if sources
            else (
                "La recherche ciblée n'a pas encore produit de source candidate exploitable. "
                "Vous pouvez préciser l'angle scientifique dans cette conversation et relancer la recherche."
            )
        ),
        "sources": sources,
        "internal_response": {
            "state": response.get("state"),
            "next_action": response.get("next_action"),
            "focused_fallback_used": bool(fallback_requests),
            "focused_request_count": len(fallback_requests),
            "focused_candidate_count": len(
                (fallback_result or {}).get("candidates") or []
            ),
        },
        "policy": "candidate_sources_require_consultant_validation_before_revision",
    }


def _public_research_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Vue consultant des résultats, sans exposer l'agent ni son orchestration."""

    public: list[dict[str, Any]] = []
    for row in sources:
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        abstract = (
            row.get("abstract_fr")
            or row.get("abstract")
            or row.get("content_excerpt")
            or row.get("summary")
            or ""
        )
        reason = (
            row.get("reason")
            or row.get("relevance_reason")
            or row.get("consultant_reason")
            or row.get("direct_evidence_reason")
            or row.get("role_reason")
            or ""
        )
        fulltext = dict(row.get("fulltext_preparation") or {})
        raw_decision = str(row.get("consultant_decision") or "").strip().casefold()
        decision = (
            "accepted"
            if raw_decision in {"accept", "accepted", "garde", "kept"}
            else "rejected"
            if raw_decision in {"reject", "rejected", "rejete", "rejeté", "ecarte", "écarté"}
            else "pending"
        )
        site_url = _publication_site_url(row)
        pdf_url = str(row.get("pdf_url") or "").strip() or None
        if not pdf_url and _is_direct_pdf_url(row.get("url")):
            pdf_url = str(row.get("url") or "").strip() or None
        public.append(
            {
                "candidate_id": candidate_id,
                "candidate_kind": row.get("candidate_kind") or "scientific_article",
                "title": row.get("title") or "Source sans titre",
                "authors": list(row.get("authors") or []),
                "year": row.get("year"),
                "doi": row.get("doi"),
                "url": site_url or pdf_url,
                "site_url": site_url,
                "pdf_url": pdf_url,
                "provider": (
                    row.get("provider")
                    or row.get("source")
                    or ", ".join(str(value) for value in (row.get("source_providers") or []) if value)
                    or None
                ),
                "abstract": " ".join(str(abstract).split())[:1800],
                "reason": " ".join(str(reason).split())[:1000],
                "relevance_role": row.get("relevance_role"),
                "direct_evidence": bool(row.get("direct_evidence")),
                "consultant_decision": decision,
                "fulltext_status": fulltext.get("status"),
                "article_id": fulltext.get("article_id") or row.get("article_id"),
                "article_card_ready": bool(
                    fulltext.get("ready_for_writing")
                    or fulltext.get("article_card_ready")
                ),
            }
        )
    return public


def _research_handoff_from_agent_result(
    result: Any,
    improvement_session: ImprovementSession,
) -> dict[str, Any] | None:
    """Projette la recherche interne directement dans la conversation Agent 3.

    Le moteur EnnoScholar et son stockage Guided Research restent des details
    d'orchestration. Le consultant voit, valide et reutilise les articles dans
    EnnoAmelioration; aucune navigation vers le frontend EnnoScholar n'est
    necessaire.
    """

    research = dict(getattr(result, "research", None) or {})
    guided_session_id = str(research.get("session_id") or "").strip()
    if not guided_session_id:
        return None
    sources = _public_research_sources(list(research.get("candidates") or []))
    return {
        "ok": True,
        "guided_session_id": guided_session_id,
        "corpus_scope_id": guided_session_id,
        "state": research.get("state") or "waiting_consultant_feedback",
        "next_action": "review_sources" if sources else "refine_research",
        "assistant_message": (
            f"La recherche scientifique ciblee a propose {len(sources)} source(s) "
            "candidate(s). Elles sont disponibles dans l'onglet Sources de cette "
            "amelioration. Gardez celles qui doivent etayer la redaction, puis "
            "demandez la nouvelle version dans ce meme chat."
        ),
        "sources": sources,
        "internal_response": {
            "orchestration_owner": "EnnoAmelioration",
            "research_engine": research.get("engine") or "ennoscholar_core",
            "research_mode": research.get("research_mode"),
            "candidate_count": len(sources),
            "improvement_session_id": improvement_session.id,
        },
        "policy": "candidate_sources_require_consultant_validation_before_revision",
        "frontend_owner": "ennoamelioration",
        "writing_owner": "ennoamelioration",
    }


def _start_typed_research_inside_improvement(
    db: Session,
    project: Project,
    improvement_session: ImprovementSession,
    request: ImprovementRequest,
    result: Any,
) -> dict[str, Any]:
    """Lance le moteur scientifique via la strategie de section d'Agent 3."""

    from agents.EnnoAmelioration.application.research_orchestration_service import (
        launch_targeted_guided_research,
    )

    research_request = request.model_copy(
        update={
            "research_target_type": result.routing.section_function.value,
            "research_section_plan": result.routing.section_plan,
        }
    )
    research = launch_targeted_guided_research(
        db,
        project,
        research_request,
        diagnostic_package=(result.evidence.get("diagnostic") or {}),
        diagnostic_orchestration=(
            result.evidence.get("diagnostic_orchestration") or {}
        ),
    )
    projected = result.model_copy(update={"research": research})
    handoff = _research_handoff_from_agent_result(projected, improvement_session)
    if handoff is None:
        raise RuntimeError(
            "La recherche scientifique interne n'a retourne aucune session exploitable."
        )
    return handoff


def _accepted_evidence_bundle(
    db: Session,
    project: Project,
    session: ImprovementSession,
) -> dict[str, Any]:
    """Isole les preuves de cette conversation des anciens corpus du projet."""

    context = dict(session.context_json or {})
    source_rows = [
        dict(row)
        for row in (context.get("accepted_research_sources") or [])
        if isinstance(row, dict) and row.get("consultant_decision") == "accepted"
    ]
    guided_session_id = str(
        (context.get("scholar_handoff") or {}).get("guided_session_id") or ""
    ).strip()
    handoff_scope_id = str(
        (context.get("scholar_handoff") or {}).get("corpus_scope_id") or ""
    ).strip()
    conversation_scope_id = str(context.get("corpus_scope_id") or "").strip()
    corpus_scope_id = handoff_scope_id or conversation_scope_id
    if not source_rows:
        source_rows = [
            dict(row)
            for row in (context.get("research_sources") or [])
            if isinstance(row, dict) and row.get("consultant_decision") == "accepted"
        ]

    # La session Guided Research reste la source de verite de la preparation
    # des publications. La vue publique stockee dans la conversation peut avoir
    # ete projetee avant la fin de l'extraction plein texte, ou contenir un
    # statut ancien apres un tour de chat interrompu.
    if guided_session_id:
        try:
            from services.guided_research_service import read_guided_research_session

            snapshot = read_guided_research_session(db, guided_session_id)
            guided_sources = [
                dict(row)
                for row in (
                    (snapshot.get("artifacts") or {}).get("selected_sources") or []
                )
                if isinstance(row, dict)
                and row.get("consultant_decision") == "accepted"
            ]
            if guided_sources:
                source_rows = guided_sources
        except Exception:
            # Compatibilite avec les anciennes sessions : les donnees deja
            # isolees dans la conversation restent utilisables en fallback.
            pass

    # Compatibilité pour une conversation ayant subi l'ancien bug : le second
    # handoff avait écrasé le premier dans context_json. On récupère alors la
    # dernière recherche EnnoAmelioration de ce consultant contenant des choix.
    if not source_rows:
        try:
            from agents.EnnoScholar.guided_research.lot1.domain.models import (
                GuidedResearchSessionORM,
            )

            rows = (
                db.query(GuidedResearchSessionORM)
                .filter(GuidedResearchSessionORM.project_id == project.id)
                .filter(GuidedResearchSessionORM.entry_module == "ennoamel")
                .order_by(GuidedResearchSessionORM.updated_at.desc())
                .limit(30)
                .all()
            )
            for row in rows:
                if (
                    session.created_by_user_id is not None
                    and row.created_by_user_id is not None
                    and int(row.created_by_user_id) != int(session.created_by_user_id)
                ):
                    continue
                accepted = [
                    dict(source)
                    for source in (row.selected_sources_json or [])
                    if isinstance(source, dict)
                    and source.get("consultant_decision") == "accepted"
                ]
                if accepted:
                    source_rows = accepted
                    guided_session_id = str(row.id)
                    corpus_scope_id = str(
                        (row.context_json or {}).get("corpus_scope_id")
                        or corpus_scope_id
                        or row.id
                    )
                    break
        except Exception:
            source_rows = []

    if not source_rows:
        return {
            "guided_session_id": guided_session_id or None,
            "corpus_scope_id": corpus_scope_id or None,
            "sources": [],
            "article_ids": [],
        }

    resolved_card_scope_id = ""
    try:
        from services.article_card_builder import get_article_cards_payload

        cards = []
        # Les fiches produites pendant la recherche sont rangees sous l'ID de
        # la session Guided Research. Cet ID doit donc primer sur l'ID de la
        # conversation d'amelioration, qui isole un autre niveau du flux.
        scope_candidates = list(
            dict.fromkeys(
                value
                for value in (
                    guided_session_id,
                    handoff_scope_id,
                    conversation_scope_id,
                )
                if value
            )
        )
        for scope_id in scope_candidates:
            scoped_payload = get_article_cards_payload(project, scope_id=scope_id)
            cards = list(scoped_payload.get("cards") or [])
            if cards:
                resolved_card_scope_id = scope_id
                break
        if not cards:
            scoped_payload = get_article_cards_payload(project)
            cards = list(scoped_payload.get("cards") or [])
    except Exception:
        cards = []
    card_article_ids: set[int] = set()
    for card in cards:
        if not isinstance(card, dict):
            continue
        identity = card.get("identity") if isinstance(card.get("identity"), dict) else {}
        try:
            card_article_ids.add(int(card.get("article_id") or identity.get("article_id")))
        except (TypeError, ValueError):
            continue

    ready_article_ids: list[int] = []
    normalized_sources: list[dict[str, Any]] = []
    for source in source_rows:
        prepared = dict(source.get("fulltext_preparation") or {})
        try:
            article_id = int(source.get("article_id") or prepared.get("article_id"))
        except (TypeError, ValueError):
            article_id = 0
        # Une fiche article n'est creee dans ce corpus qu'apres preparation de
        # la preuve. Sa presence est plus fiable qu'un booleen public ancien.
        actually_ready = bool(article_id and article_id in card_article_ids)
        prepared.update(
            {
                "article_id": article_id or None,
                "article_card_ready": actually_ready,
                "ready_for_writing": actually_ready,
            }
        )
        normalized = {**source, "fulltext_preparation": prepared}
        normalized_sources.append(normalized)
        if actually_ready:
            ready_article_ids.append(article_id)

    return {
        "guided_session_id": guided_session_id or None,
        "corpus_scope_id": (
            resolved_card_scope_id
            or guided_session_id
            or corpus_scope_id
            or None
        ),
        "sources": _public_research_sources(normalized_sources),
        "article_ids": list(dict.fromkeys(ready_article_ids)),
    }


def decide_research_sources(
    db: Session,
    project: Project,
    session_id: str,
    *,
    candidate_ids: list[str],
    decision: str,
    reason: str = "",
) -> ImprovementSession:
    """Valide les sources liées sans faire quitter EnnoAmelioration."""

    session = get_session(db, project.id, session_id)
    context = dict(session.context_json or {})
    handoff = dict(context.get("scholar_handoff") or {})
    guided_session_id = str(handoff.get("guided_session_id") or "").strip()
    if not guided_session_id:
        raise LookupError("Aucune recherche scientifique n'est liée à cette conversation.")

    from services.guided_research_service import (
        decide_guided_research_sources,
        read_guided_research_session,
    )

    decide_guided_research_sources(
        db,
        project,
        session_id=guided_session_id,
        candidate_ids=candidate_ids,
        decision=decision,
        reason=reason,
        prepare_after_acceptance=True,
    )
    snapshot = read_guided_research_session(db, guided_session_id)
    sources = _public_research_sources(
        list((snapshot.get("artifacts") or {}).get("selected_sources") or [])
    )
    accepted = [row for row in sources if row.get("consultant_decision") == "accepted"]
    rejected = [row for row in sources if row.get("consultant_decision") == "rejected"]
    ready = [row for row in accepted if row.get("article_card_ready") is True]
    handoff.update(
        {
            "sources": sources,
            "accepted_count": len(accepted),
            "ready_count": len(ready),
            "rejected_count": len(rejected),
            "state": (snapshot.get("session") or {}).get("state"),
            "assistant_message": (
                f"Sélection mise à jour : {len(accepted)} source(s) gardée(s) et "
                f"{len(rejected)} écartée(s)."
            ),
        }
    )
    session.context_json = {
        **context,
        "scholar_handoff": handoff,
        "research_sources": sources,
        "accepted_research_sources": accepted,
    }
    session.state = "evidence_ready" if ready else "awaiting_evidence"
    session.updated_at = _utcnow()
    _add_message(
        db,
        session.id,
        "assistant",
        (
            f"Sélection enregistrée : {len(accepted)} source(s) gardée(s), dont {len(ready)} "
            "preuve(s) réellement prête(s) à étayer la révision. "
            + (
                "Vous pouvez me demander de réécrire la section avec ces preuves validées."
                if ready
                else "La rédaction scientifique reste en attente d'au moins une preuve exploitable."
            )
            if accepted
            else "La sélection est enregistrée. Aucune source n'est encore gardée pour la révision."
        ),
        intent="research_sources_decided",
        metadata={
            "candidate_ids": candidate_ids,
            "decision": decision,
            "accepted_count": len(accepted),
            "ready_count": len(ready),
            "rejected_count": len(rejected),
        },
    )
    db.commit()
    db.refresh(session)
    return session


def create_session(
    db: Session,
    project: Project,
    *,
    user_id: int | None,
    title: str | None = None,
    source_text: str | None = None,
    source_document_id: int | None = None,
    target_scope: str = "section",
    target_section_id: str | None = None,
    target_section_title: str | None = None,
) -> ImprovementSession:
    document: Document | None = None
    document_structure: dict[str, Any] = {}
    text = str(source_text or "").strip()
    if source_document_id is not None:
        document, extracted, document_structure = _extract_document_payload(
            db,
            project,
            source_document_id,
        )
        if not text:
            text = extracted

    improvement_session_id = _new_id()
    session_title = (
        title or (document.filename if document else None) or "Nouvelle amélioration"
    )[:255]
    session = ImprovementSession(
        id=improvement_session_id,
        project_id=project.id,
        created_by_user_id=user_id,
        title=session_title,
        state="target_identification" if not text else "audit",
        target_scope=target_scope,
        target_section_id=target_section_id,
        target_section_title=target_section_title,
        source_document_id=document.id if document else source_document_id,
        context_json={
            "sections": _sections_payload(text),
            "source_kind": "document" if document else "pasted_text" if text else "empty",
            "document_structure": document_structure,
            "document_preservation": dict(
                document_structure.get("preservation") or {}
            ),
            "corpus_scope_id": improvement_session_id,
            "conversation_project_label": str(project.project_name or ""),
            "conversation_project_domain": str(project.domain_label or ""),
            "project_identity": {
                "project_id": project.id,
                "organisme": project.organisme,
                "project_name": project.project_name,
                "year": project.year,
                "domain": project.domain_label,
            },
            "corpus_isolation_policy": "one_improvement_conversation_one_corpus",
        },
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(session)
    db.flush()

    if text:
        original = ImprovementVersion(
            id=_new_id(),
            session_id=session.id,
            version_number=1,
            status="original",
            content=text,
            parent_version_id=None,
            instruction="Version source importée",
            diff_json={},
            audit_json={},
            evidence_json={},
            generation_json={"source": "consultant"},
            created_at=_utcnow(),
        )
        db.add(original)
        db.flush()
        session.active_version_id = original.id

    greeting = (
        "Le texte est chargé. Décrivez librement la partie à améliorer : je peux reconnaître son numéro, "
        "son titre ou son thème dans le document. La sélection manuelle reste facultative, et chaque "
        "proposition restera séparée de l'original jusqu'à votre validation."
        if text
        else "Collez un texte ou choisissez un document du projet, puis indiquez l'amélioration souhaitée."
    )
    _add_message(db, session.id, "assistant", greeting, intent="session_created")
    db.commit()
    db.refresh(session)
    return session


def list_sessions(db: Session, project_id: int, limit: int = 50) -> list[ImprovementSession]:
    return (
        db.query(ImprovementSession)
        .filter(ImprovementSession.project_id == project_id)
        .order_by(ImprovementSession.updated_at.desc(), ImprovementSession.created_at.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )


def list_session_summaries(db: Session, project_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """Liste les conversations sans charger leurs graphes messages/versions.

    ``serialize_session(..., detailed=False)`` accedait auparavant aux deux
    relations lazy pour chaque ligne : cela produisait un N+1 et chargeait le
    contenu complet de toutes les versions uniquement pour afficher 240
    caracteres de preview.
    """

    message_counts = (
        db.query(
            ImprovementMessage.session_id.label("session_id"),
            func.count(ImprovementMessage.id).label("message_count"),
        )
        .group_by(ImprovementMessage.session_id)
        .subquery()
    )
    version_counts = (
        db.query(
            ImprovementVersion.session_id.label("session_id"),
            func.sum(
                case((ImprovementVersion.status == "candidate", 1), else_=0)
            ).label("candidate_count"),
        )
        .group_by(ImprovementVersion.session_id)
        .subquery()
    )

    rows = (
        db.query(
            ImprovementSession.id,
            ImprovementSession.project_id,
            ImprovementSession.title,
            ImprovementSession.state,
            ImprovementSession.target_scope,
            ImprovementSession.target_section_id,
            ImprovementSession.target_section_title,
            ImprovementSession.source_document_id,
            ImprovementSession.active_version_id,
            ImprovementSession.created_at,
            ImprovementSession.updated_at,
            func.coalesce(message_counts.c.message_count, 0),
            func.coalesce(version_counts.c.candidate_count, 0),
            ImprovementVersion.version_number,
            func.substr(ImprovementVersion.content, 1, 240),
        )
        .outerjoin(message_counts, message_counts.c.session_id == ImprovementSession.id)
        .outerjoin(version_counts, version_counts.c.session_id == ImprovementSession.id)
        .outerjoin(ImprovementVersion, ImprovementVersion.id == ImprovementSession.active_version_id)
        .filter(ImprovementSession.project_id == project_id)
        .order_by(
            ImprovementSession.updated_at.desc(),
            ImprovementSession.created_at.desc(),
        )
        .limit(max(1, min(limit, 100)))
        .all()
    )

    return [
        {
            "session_id": row[0],
            "project_id": row[1],
            "title": row[2],
            "state": row[3],
            "target_scope": row[4],
            "target_section_id": row[5],
            "target_section_title": row[6],
            "source_document_id": row[7],
            "active_version_id": row[8],
            "created_at": row[9].isoformat() if row[9] else None,
            "updated_at": row[10].isoformat() if row[10] else None,
            "message_count": int(row[11] or 0),
            "candidate_count": int(row[12] or 0),
            "active_version_number": row[13],
            "preview": str(row[14] or ""),
        }
        for row in rows
    ]


def get_session(db: Session, project_id: int, session_id: str) -> ImprovementSession:
    session = (
        db.query(ImprovementSession)
        .filter(ImprovementSession.id == session_id, ImprovementSession.project_id == project_id)
        .first()
    )
    if session is None:
        raise LookupError("Conversation d'amélioration introuvable.")
    return session


def send_message(
    db: Session,
    project: Project,
    session_id: str,
    *,
    message: str,
    selected_text: str | None = None,
    target_scope: str | None = None,
    target_section_id: str | None = None,
    target_section_title: str | None = None,
) -> tuple[ImprovementSession, ImprovementVersion | None]:
    session = get_session(db, project.id, session_id)
    working_version = _working_version(session)
    full_text = str(working_version.content or "") if working_version else _version_content(session)
    # Les conversations créées avant la correction peuvent déjà contenir un
    # titre collé à la phrase précédente. On le répare dans la proposition ; la
    # version active ne change qu'après acceptation explicite du consultant.
    full_text = repair_section_boundaries(full_text)
    if str((session.context_json or {}).get("source_kind") or "") == "document":
        # Répare également les imports PDF déjà créés : le sommaire ne devient
        # pas une série de fausses sections dans la prochaine proposition.
        full_text = _clean_extracted_document_text(full_text)
    scope = TargetScope(target_scope or session.target_scope or "section")
    sections = parse_sections(full_text)
    inferred_section = (
        infer_section_from_instruction(message, sections)
        if not (selected_text and selected_text.strip())
        else None
    )
    effective_section_id = (
        inferred_section.section_id
        if inferred_section is not None
        else target_section_id or session.target_section_id
    )
    effective_section_title = (
        inferred_section.title
        if inferred_section is not None
        else target_section_title or session.target_section_title
    )
    resolved_text, resolved_section = resolve_target(
        full_text,
        sections,
        section_id=effective_section_id,
        section_title=effective_section_title,
        selected_text=selected_text,
    )
    effective_scope = scope
    if selected_text and selected_text.strip():
        effective_scope = TargetScope.SELECTION
    elif inferred_section is not None:
        effective_scope = TargetScope.SECTION
    elif resolved_section is None and scope == TargetScope.SECTION:
        effective_scope = TargetScope.FULL_DOCUMENT

    preliminary_routing = understand_instruction(message, effective_scope)
    evidence_bundle: dict[str, Any] = {
        "guided_session_id": None,
        "corpus_scope_id": None,
        "sources": [],
        "article_ids": [],
    }
    session_context = dict(session.context_json or {})
    has_linked_accepted_sources = bool(
        session_context.get("accepted_research_sources")
        or any(
            isinstance(row, dict)
            and row.get("consultant_decision") == "accepted"
            for row in (session_context.get("research_sources") or [])
        )
    )
    if (
        not preliminary_routing.needs_new_research
        and not preliminary_routing.forbids_scholar
        and (preliminary_routing.needs_scholar or has_linked_accepted_sources)
    ):
        evidence_bundle = _accepted_evidence_bundle(db, project, session)

    _add_message(
        db,
        session.id,
        "consultant",
        message.strip(),
        intent="improvement_request",
        metadata={
            "target_scope": effective_scope.value,
            "target_section_id": resolved_section.section_id if resolved_section else effective_section_id,
            "target_section_title": resolved_section.title if resolved_section else effective_section_title,
            "target_inferred_from_message": inferred_section is not None,
        },
    )

    request = ImprovementRequest(
        instruction=message.strip(),
        full_text=full_text,
        target_text=resolved_text,
        target_scope=effective_scope,
        target_section_id=resolved_section.section_id if resolved_section else effective_section_id,
        target_section_title=resolved_section.title if resolved_section else effective_section_title,
        project_name=str(
            (session.context_json or {}).get("conversation_project_label")
            or project.project_name
            or ""
        ),
        project_domain=str(
            (session.context_json or {}).get("conversation_project_domain")
            or project.domain_label
            or ""
        ),
        evidence_article_ids=(
            list(evidence_bundle.get("article_ids") or []) or None
        ),
        evidence_scope_id=str(
            evidence_bundle.get("corpus_scope_id") or ""
        ) or None,
        sections=sections,
    )
    try:
        result = get_improvement_agent().improve(db, project, request)
    except Exception as exc:
        db.rollback()
        raise RuntimeError(f"La rédaction n'a pas abouti : {exc}") from exc

    research_handoff: dict[str, Any] | None = None
    scholar_payload = result.evidence.get("scholar") if isinstance(result.evidence, dict) else None
    needs_scholar_handoff = bool(
        result.routing.needs_scholar
        and not result.routing.forbids_scholar
        and not result.routing.forbids_new_research
        and (
            result.routing.needs_new_research
            or not isinstance(scholar_payload, dict)
            or not scholar_payload.get("available")
        )
    )
    # Si Agent 3 a deja orchestre le moteur scientifique, reutiliser exactement
    # cette session et ces candidats. Relancer un handoff ici produirait une
    # seconde recherche et ferait diverger les resultats affiches.
    research_handoff = _research_handoff_from_agent_result(result, session)
    if research_handoff is not None:
        result.evidence["research_handoff"] = research_handoff
        result.assistant_message = str(research_handoff["assistant_message"])
        result = result.model_copy(
            update={"ok": True, "state": ImprovementState.AWAITING_EVIDENCE}
        )
    elif result.requires_confirmation and needs_scholar_handoff:
        try:
            research_handoff = _start_typed_research_inside_improvement(
                db,
                project,
                session,
                request,
                result,
            )
            result.evidence["research_handoff"] = research_handoff
            if research_handoff.get("assistant_message"):
                result.assistant_message = str(research_handoff["assistant_message"])
            if research_handoff.get("ok"):
                # Le handoff a effectivement pris en charge la recherche : la
                # conversation attend désormais la validation des sources,
                # même si une première tentative directe avait échoué.
                result = result.model_copy(
                    update={
                        "ok": True,
                        "state": ImprovementState.AWAITING_EVIDENCE,
                    }
                )
        except Exception as exc:
            # Un échec réseau/LLM d'EnnoScholar ne doit jamais faire croire que
            # le texte a été modifié ou faire disparaître la conversation.
            research_handoff = {
                "ok": False,
                "error": str(exc),
                "policy": "no_revision_without_validated_sources",
            }
            result.evidence["research_handoff"] = research_handoff
            result.assistant_message = (
                "La demande de recherche est enregistrée, mais le moteur de recherche n'a pas pu "
                "retourner les sources candidates pour le moment. Le texte original reste intact ; "
                "vous pouvez relancer cette demande sans recréer la conversation."
            )

    candidate: ImprovementVersion | None = None
    if result.ok and result.improved_full_text:
        number = max([row.version_number for row in session.versions] or [0]) + 1
        for row in session.versions:
            if row.status == "candidate":
                row.status = "superseded"
        candidate = ImprovementVersion(
            id=_new_id(),
            session_id=session.id,
            version_number=number,
            status="candidate",
            content=result.improved_full_text,
            parent_version_id=working_version.id if working_version else session.active_version_id,
            instruction=message.strip(),
            diff_json=_make_diff(full_text, result.improved_full_text),
            audit_json={"findings": [item.model_dump(mode="json") for item in result.audit]},
            evidence_json=result.evidence,
            generation_json={
                **result.generation,
                "document_preservation": dict(
                    (session.context_json or {}).get("document_preservation") or {}
                ),
                "structured_result": {
                    "changes": result.changes,
                    "sources_used": result.sources_used,
                    "agents_used": result.agents_used,
                    "unsupported_claims": result.unsupported_claims,
                    "questions_for_consultant": result.questions_for_consultant,
                    "diagnostic_used": result.routing.needs_diagnostic,
                    "scholar_used": result.routing.needs_scholar,
                    "cir_memory_used": bool(
                        (result.evidence.get("cir_style") or {}).get("guidance_injected")
                        and (result.evidence.get("cir_style") or {}).get(
                            "selected_pattern_ids"
                        )
                    ),
                    "document_layout_preserved": bool(
                        session.source_document_id
                        and (
                            (session.context_json or {}).get("document_preservation")
                            or {}
                        ).get("source_binary_immutable")
                    ),
                },
            },
            created_at=_utcnow(),
        )
        db.add(candidate)
        session.state = "candidate_ready"
    else:
        session.state = result.state.value

    session.target_scope = effective_scope.value
    session.target_section_id = resolved_section.section_id if resolved_section else effective_section_id
    session.target_section_title = resolved_section.title if resolved_section else effective_section_title
    previous_context = dict(session.context_json or {})
    resolved_handoff = previous_context.get("scholar_handoff")
    resolved_sources = list(previous_context.get("research_sources") or [])
    accepted_sources = list(previous_context.get("accepted_research_sources") or [])
    if research_handoff is not None:
        resolved_handoff = research_handoff
        resolved_sources = list(research_handoff.get("sources") or [])
    elif evidence_bundle.get("sources"):
        accepted_sources = list(evidence_bundle.get("sources") or [])
        resolved_sources = accepted_sources
        resolved_handoff = {
            **(
                dict(resolved_handoff)
                if isinstance(resolved_handoff, dict)
                else {}
            ),
            "guided_session_id": evidence_bundle.get("guided_session_id"),
            "corpus_scope_id": evidence_bundle.get("corpus_scope_id"),
            "sources": accepted_sources,
            "accepted_count": len(accepted_sources),
            "ready_count": len(evidence_bundle.get("article_ids") or []),
            "recovered_validated_corpus": True,
        }

    session.context_json = {
        **previous_context,
        "sections": _sections_payload(full_text),
        "last_routing": result.routing.model_dump(mode="json"),
        "last_audit": [item.model_dump(mode="json") for item in result.audit],
        "last_trace": {
            "changes": result.changes,
            "sources_used": result.sources_used,
            "agents_used": result.agents_used,
            "unsupported_claims": result.unsupported_claims,
            "questions_for_consultant": result.questions_for_consultant,
        },
        "scholar_handoff": resolved_handoff,
        "research_sources": resolved_sources,
        "accepted_research_sources": accepted_sources,
    }
    session.updated_at = _utcnow()
    _add_message(
        db,
        session.id,
        "assistant",
        result.assistant_message,
        intent=result.state.value,
        metadata={
            "candidate_version_id": candidate.id if candidate else None,
            "routing": result.routing.model_dump(mode="json"),
            "requires_confirmation": result.requires_confirmation,
            "agents_used": result.agents_used,
            "unsupported_claims": result.unsupported_claims,
            "questions_for_consultant": result.questions_for_consultant,
        },
    )
    db.commit()
    db.refresh(session)
    return session, candidate


def decide_version(
    db: Session,
    project_id: int,
    session_id: str,
    version_id: str,
    *,
    decision: str,
    reason: str | None = None,
) -> ImprovementSession:
    session = get_session(db, project_id, session_id)
    version = next((row for row in session.versions if row.id == version_id), None)
    if version is None:
        raise LookupError("Version introuvable dans cette conversation.")
    if version.status != "candidate":
        raise ValueError("Seule une proposition en attente peut être acceptée ou rejetée.")

    version.decided_at = _utcnow()
    if decision == "accepted":
        for row in session.versions:
            if row.id != version.id and row.status == "accepted":
                row.status = "superseded"
        version.status = "accepted"
        session.active_version_id = version.id
        session.state = "published"
        session.context_json = {**dict(session.context_json or {}), "sections": _sections_payload(version.content)}
        response = f"La version {version.version_number} est maintenant la version active. L'original reste conservé."
    else:
        version.status = "rejected"
        session.state = "review"
        response = f"La version {version.version_number} a été rejetée. Le texte actif n'a pas changé."
    session.updated_at = _utcnow()
    _add_message(
        db,
        session.id,
        "assistant",
        response,
        intent=f"version_{decision}",
        metadata={"version_id": version.id, "reason": reason or ""},
    )
    db.commit()
    db.refresh(session)
    return session


def restore_version(
    db: Session,
    project_id: int,
    session_id: str,
    version_id: str,
    *,
    reason: str | None = None,
) -> ImprovementSession:
    session = get_session(db, project_id, session_id)
    version = next((row for row in session.versions if row.id == version_id), None)
    if version is None or version.status == "rejected":
        raise LookupError("Cette version ne peut pas être restaurée.")
    session.active_version_id = version.id
    session.state = "published"
    session.context_json = {**dict(session.context_json or {}), "sections": _sections_payload(version.content)}
    session.updated_at = _utcnow()
    _add_message(
        db,
        session.id,
        "assistant",
        f"La version {version.version_number} a été restaurée comme version active.",
        intent="version_restored",
        metadata={"version_id": version.id, "reason": reason or ""},
    )
    db.commit()
    db.refresh(session)
    return session


def delete_session(db: Session, project_id: int, session_id: str) -> None:
    session = get_session(db, project_id, session_id)
    db.delete(session)
    db.commit()


def serialize_session(session: ImprovementSession, *, detailed: bool = True) -> dict[str, Any]:
    versions = list(session.versions or [])
    active = next((row for row in versions if row.id == session.active_version_id), None)
    candidates = [row for row in versions if row.status == "candidate"]
    data: dict[str, Any] = {
        "session_id": session.id,
        "project_id": session.project_id,
        "title": session.title,
        "state": session.state,
        "target_scope": session.target_scope,
        "target_section_id": session.target_section_id,
        "target_section_title": session.target_section_title,
        "source_document_id": session.source_document_id,
        "active_version_id": session.active_version_id,
        "active_version_number": active.version_number if active else None,
        "candidate_count": len(candidates),
        "message_count": len(session.messages or []),
        "preview": str((active.content if active else "") or "")[:240],
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }
    if not detailed:
        return data
    data.update(
        {
            "context": dict(session.context_json or {}),
            "messages": [
                {
                    "message_id": row.id,
                    "role": row.role,
                    "content": row.content,
                    "intent": row.intent,
                    "metadata": dict(row.metadata_json or {}),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in session.messages or []
            ],
            "versions": [
                {
                    "version_id": row.id,
                    "version_number": row.version_number,
                    "status": row.status,
                    "content": row.content,
                    "parent_version_id": row.parent_version_id,
                    "instruction": row.instruction,
                    "diff": dict(row.diff_json or {}),
                    "audit": dict(row.audit_json or {}),
                    "evidence": dict(row.evidence_json or {}),
                    "generation": dict(row.generation_json or {}),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "decided_at": row.decided_at.isoformat() if row.decided_at else None,
                    "is_active": row.id == session.active_version_id,
                }
                for row in versions
            ],
        }
    )
    return data
