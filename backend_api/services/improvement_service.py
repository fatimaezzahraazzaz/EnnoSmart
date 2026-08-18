from __future__ import annotations

from agents.EnnoAmelioration.application.auto_evidence_selector_v320 import (
    bind_prepared_sources,
    build_traceable_evidence,
    select_sources,
)

from agents.EnnoAmelioration.application.conversation_task_memory_v317 import evolve_task_memory

from agents.EnnoAmelioration.application.conversation_scope_v3161 import (
    ACTION_CANCEL_PROGRESSIVE_FOR_TARGET,
    ACTION_NORMAL,
    ACTION_RESUME_PROGRESSIVE,
    ACTION_START_PROGRESSIVE,
    effective_scope_value,
    is_progressive_continue_message,
    message_explicitly_requests_full_document,
    progressive_action,
)

import difflib
import hashlib
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
from agents.EnnoAmelioration.application.research_context_bridge_v310 import (
    build_research_conversation_context,
)
# ENNOMEL_RESEARCH_CONVERSATION_MEMORY_V3_7
# ENNOMEL_MESSAGE_ROUTING_ONLY_V3_8
from agents.EnnoAmelioration.application.intention_service import understand_instruction
from agents.EnnoAmelioration.application.cir_section_progressive_v320 import (
    DIAGNOSTIC_POLICY_VERSION,
    WORKFLOW_VERSION,
    add_patch as progressive_add_patch,
    advance_cursor as progressive_advance_cursor,
    apply_patches as progressive_apply_patches,
    build_workflow as progressive_build_workflow,
    current_unit as progressive_current_unit,
    mark_kept as progressive_mark_kept,
    max_auto_writes_per_turn as progressive_max_auto_writes_per_turn,
    progress_label as progressive_progress_label,
    refresh_pending_units_for_current_policy as progressive_refresh_pending_units,
    unit_is_title_only as progressive_unit_is_title_only,
    unit_source as progressive_unit_source,
    workflow_public_summary as progressive_workflow_public_summary,
)
from agents.EnnoAmelioration.application.markdown_service import (
    normalize_markdown_text,
)
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


class ProgressiveUnitUnwritable(RuntimeError):
    """Résultat contrôlé non exploitable : garder la section et continuer."""


INITIAL_CIR_DIAGNOSTIC_POLICY = DIAGNOSTIC_POLICY_VERSION


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



def _pasted_section_is_whole_target(
    *,
    source_kind: str,
    scope: TargetScope,
    selected_text: str | None,
    requested_section_id: str | None,
    requested_section_title: str | None,
    inferred_section: Any,
) -> bool:
    return bool(
        source_kind == "pasted_text"
        and scope == TargetScope.SECTION
        and not str(selected_text or "").strip()
        and not str(requested_section_id or "").strip()
        and not str(requested_section_title or "").strip()
        and inferred_section is None
    )


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


def _working_version(
    session: ImprovementSession,
    *,
    prefer_candidate: bool = False,
) -> ImprovementVersion | None:
    """Choisit explicitement la base de travail sans publier une candidate.

    Règle V3.5 :
    - une nouvelle demande repart de la version active ;
    - la dernière candidate n'est reprise que si la demande est reconnue comme
      une correction/continuation explicite de cette proposition ;
    - une candidate non validée ne devient jamais silencieusement la source
      d'une amélioration suivante.
    """

    if prefer_candidate:
        candidates = [row for row in session.versions if row.status == "candidate"]
        if candidates:
            return max(candidates, key=lambda row: row.version_number)

    if session.active_version_id:
        active = next(
            (row for row in session.versions if row.id == session.active_version_id),
            None,
        )
        if active is not None:
            return active

    accepted = [
        row
        for row in session.versions
        if row.status in {"accepted", "original"}
    ]
    if accepted:
        return max(accepted, key=lambda row: row.version_number)

    return None


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
                "selection_origin": row.get("selection_origin"),
                "auto_selected": bool(row.get("auto_selected")),
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
            "queries": list(research.get("queries") or []),
            "research_context": dict(research.get("research_context") or {}),
            "research_mode": research.get("research_mode"),
            "target_verrous": list(research.get("target_verrous") or []),
            "research_target_ids": list(research.get("research_target_ids") or []),
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
        conversation_context=build_research_conversation_context(
            dict(improvement_session.context_json or {}),
            previous_target_section_id=improvement_session.target_section_id,
            current_target_section_id=research_request.target_section_id,
            current_target_section_title=research_request.target_section_title,
            consultant_feedback=research_request.instruction,
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
    # V3.12 : ce fallback historique n'est permis que lorsqu'aucune
    # recherche courante n'est liée à la conversation.
    if not source_rows and not guided_session_id:
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
    progressive = _progressive_context(session)
    if progressive and progressive.get("active"):
        current = progressive_current_unit(progressive)
        if current is not None and current.get("action") == "research":
            research_meta = dict(current.get("research") or {})
            research_meta.update(
                {
                    "guided_session_id": guided_session_id,
                    "accepted_sources": accepted,
                    "ready_article_ids": [
                        row.get("article_id")
                        for row in ready
                        if row.get("article_id")
                    ],
                    "accepted_count": len(accepted),
                    "ready_count": len(ready),
                }
            )
            current["research"] = research_meta
            current["status"] = "evidence_ready" if ready else "awaiting_sources"
            progressive["phase"] = (
                "evidence_ready" if ready else "awaiting_sources"
            )
            _progressive_store(session, progressive)

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
    # Une section collée est conservée comme Markdown source.
    # Du texte simple reste parfaitement valide en Markdown.
    text = normalize_markdown_text(source_text)
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


# BEGIN ENNOAMEL_CIR_PROGRESSIVE_V3_11

def _progressive_active_version(session: ImprovementSession) -> ImprovementVersion | None:
    """Version acceptée active = unique source de vérité du parcours CIR."""

    if session.active_version_id:
        active = next(
            (row for row in session.versions if row.id == session.active_version_id),
            None,
        )
        if active is not None:
            return active
    accepted = [
        row for row in session.versions if row.status in {"accepted", "original"}
    ]
    if accepted:
        return max(accepted, key=lambda row: row.version_number)
    return None


def _progressive_context(session: ImprovementSession) -> dict[str, Any] | None:
    value = (session.context_json or {}).get("cir_progressive_workflow")
    if not isinstance(value, dict) or value.get("version") != WORKFLOW_VERSION:
        return None
    return dict(value)


def _progressive_store(
    session: ImprovementSession,
    workflow: dict[str, Any],
    *,
    handoff: dict[str, Any] | None | object = ...,
    research_sources: list[dict[str, Any]] | None | object = ...,
    accepted_sources: list[dict[str, Any]] | None | object = ...,
) -> None:
    context = dict(session.context_json or {})
    context["cir_progressive_workflow"] = workflow
    if handoff is not ...:
        context["scholar_handoff"] = handoff
    if research_sources is not ...:
        context["research_sources"] = list(research_sources or [])
    if accepted_sources is not ...:
        context["accepted_research_sources"] = list(accepted_sources or [])
    session.context_json = context
    session.updated_at = _utcnow()


def _progressive_base_text(
    session: ImprovementSession,
    workflow: dict[str, Any],
) -> tuple[ImprovementVersion, str]:
    version_id = str(workflow.get("base_version_id") or "")
    version = next((row for row in session.versions if row.id == version_id), None)
    if version is None:
        raise RuntimeError(
            "La version active utilisée pour démarrer le parcours CIR n'existe plus."
        )
    text = str(version.content or "")
    if hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest() != str(
        workflow.get("base_sha256") or ""
    ):
        raise RuntimeError(
            "La version active de base a changé pendant le parcours. "
            "Relancez l'amélioration du CIR depuis la version active courante."
        )
    return version, text


def _ensure_progressive_initial_diagnostic(
    db: Session,
    project: Project,
    session: ImprovementSession,
    workflow: dict[str, Any],
) -> dict[str, Any]:
    """Résout une seule fois le diagnostic structuré de la version active."""

    current = dict(workflow.get("initial_diagnostic") or {})
    if (
        current.get("completed") is True
        and str(current.get("base_sha256") or "")
        == str(workflow.get("base_sha256") or "")
    ):
        return current

    _, base_text = _progressive_base_text(session, workflow)
    request = ImprovementRequest(
        instruction=(
            "Produis le diagnostic initial structuré du CIR complet. "
            "Ce diagnostic sera mis en cache et ne sera pas recalculé section "
            "par section. N'effectue aucune recherche bibliographique."
        ),
        full_text=base_text,
        target_text=base_text,
        target_scope=TargetScope.FULL_DOCUMENT,
        project_name=str(project.project_name or ""),
        project_domain=str(project.domain_label or ""),
        allow_scoped_diagnostic=False,
    )

    context: dict[str, Any]
    orchestration: dict[str, Any]
    try:
        from agents.EnnoAmelioration.application.diagnostic_orchestration_service import (
            ensure_initial_diagnostic_context,
        )

        context, orchestration = ensure_initial_diagnostic_context(
            db,
            project,
            request,
        )
        context = {
            **dict(context or {}),
            "completed": True,
            "scope": "initial_full_cir",
            "cache_policy": INITIAL_CIR_DIAGNOSTIC_POLICY,
        }
        error = None
    except Exception as exc:
        # Une panne du Diagnostic ne doit pas provoquer une boucle d'appels ni
        # empêcher Scholar de travailler directement depuis le texte réel.
        context = {
            "available": False,
            "completed": True,
            "agent": "EnnoDiagnostic",
            "status": "initial_diagnostic_failed",
            "scope": "initial_full_cir",
            "verrous": [],
            "evidence_items": [],
            "domain_detection": {},
            "reason": f"{exc.__class__.__name__}: {exc}",
            "cache_policy": INITIAL_CIR_DIAGNOSTIC_POLICY,
        }
        orchestration = {
            "agent": "EnnoDiagnostic",
            "mode": "initial_failed_cached_no_retry_per_section",
            "executed": False,
            "cache_hit": False,
        }
        error = context["reason"]

    record = {
        "completed": True,
        "policy_version": INITIAL_CIR_DIAGNOSTIC_POLICY,
        "base_sha256": workflow.get("base_sha256"),
        "execution_count": 1,
        "pipeline_execution_count": int(bool(orchestration.get("executed"))),
        "cache_hit": bool(orchestration.get("cache_hit")),
        "context": context,
        "orchestration": orchestration,
        "error": error,
    }
    workflow["initial_diagnostic"] = record
    stats = workflow.setdefault("stats", {})
    stats["initial_diagnostic_runs"] = 1
    workflow["last_progress"] = {
        "stage": "initial_diagnostic_cached",
        "available": bool(context.get("available")),
        "completed": True,
    }
    _progressive_store(session, workflow)
    db.commit()
    db.refresh(session)
    return record


def _progressive_request_for_unit(
    *,
    workflow: dict[str, Any],
    base_text: str,
    unit: dict[str, Any],
    project: Project,
    instruction: str,
    article_ids: list[int] | None = None,
    evidence_scope_id: str | None = None,
) -> ImprovementRequest:
    """Construit exactement la même requête qu'un traitement manuel de section."""
    from agents.EnnoAmelioration.domain.models import ParsedSection

    target = progressive_unit_source(base_text, unit)
    section_id = str(unit.get("section_id") or "") or None
    section_title = str(unit.get("section_title") or "") or None
    section_level = int(unit.get("section_level") or 1)

    local_section = ParsedSection(
        section_id=section_id or "progressive-section",
        title=section_title or "Section courante",
        level=section_level,
        start=0,
        end=len(target),
        content=target,
    )

    ambiguous = bool(unit.get("diagnostic_ambiguous"))
    initial_diagnostic = dict(workflow.get("initial_diagnostic") or {})
    cached_context = (
        dict(initial_diagnostic.get("context") or {})
        if initial_diagnostic.get("completed") and not ambiguous
        else None
    )
    cached_orchestration = (
        dict(initial_diagnostic.get("orchestration") or {})
        if cached_context is not None
        else None
    )

    return ImprovementRequest(
        instruction=instruction.strip(),
        full_text=target,
        target_text=target,
        target_scope=TargetScope.SECTION,
        target_section_id=section_id,
        target_section_title=section_title,
        project_name=str(project.project_name or ""),
        project_domain=str(project.domain_label or ""),
        evidence_article_ids=list(article_ids or []) or None,
        evidence_scope_id=str(evidence_scope_id or "") or None,
        diagnostic_context_override=cached_context,
        diagnostic_orchestration_override=cached_orchestration,
        # Le diagnostic section-scoped est l'exception : il n'est permis que
        # lorsque la classification de la section est réellement ambiguë.
        allow_scoped_diagnostic=ambiguous,
        sections=[local_section],
    )



def _progressive_research_instruction(
    workflow: dict[str, Any],
    unit: dict[str, Any],
) -> str:
    title = str(unit.get("section_title") or "").strip()
    section_ref = str(unit.get("section_ref") or "").strip()
    label = " — ".join(value for value in (section_ref, title) if value)

    return (
        "Tu es dans le workflow automatique d'amélioration du CIR complet, "
        "mais la CIBLE EFFECTIVE DE CE TOUR est UNE SEULE SECTION.\n\n"
        f"SECTION COURANTE : {label or 'section active'}\n\n"
        "Analyse uniquement cette section. Elle a été détectée comme faible sur "
        "le fond et nécessite un renforcement scientifique. Lance une recherche "
        "scientifique ciblée à partir du contenu exact de cette section. "
        "Présente les sources candidates au consultant puis ARRÊTE la rédaction "
        "pour attendre sa sélection humaine. Ne recherche rien pour les autres "
        "sections et ne rédige pas encore la nouvelle version."
    ).strip()


def _progressive_auto_research_instruction(
    workflow: dict[str, Any],
    unit: dict[str, Any],
) -> str:
    title = str(unit.get("section_title") or "").strip()
    section_ref = str(unit.get("section_ref") or "").strip()
    label = " — ".join(value for value in (section_ref, title) if value)
    return (
        "Workflow automatique d'amélioration du CIR complet. La cible est "
        "UNIQUEMENT la section courante.\n\n"
        f"SECTION COURANTE : {label or 'section active'}\n\n"
        "Cette section présente un étayage scientifique faible. Lance "
        "immédiatement une NOUVELLE recherche EnnoScholar ciblée depuis son "
        "texte exact. Ne relance pas EnnoDiagnostic : utilise uniquement le "
        "diagnostic initial en cache s'il est disponible. Retourne les articles "
        "candidats au workflow automatique ; ne demande aucun choix au "
        "consultant et ne rédige pas encore. Le workflow sélectionnera les "
        "articles pertinents, tentera leur extraction plein texte et ne gardera "
        "que les Article Cards réellement exploitables."
    ).strip()


def _progressive_auto_write_instruction(
    workflow: dict[str, Any],
    unit: dict[str, Any],
) -> str:
    title = str(unit.get("section_title") or "").strip()
    section_ref = str(unit.get("section_ref") or "").strip()
    label = " — ".join(value for value in (section_ref, title) if value)
    return (
        "Workflow automatique d'amélioration du CIR complet. Rédige UNIQUEMENT "
        "la section courante.\n\n"
        f"SECTION COURANTE : {label or 'section active'}\n\n"
        "Utilise seulement les articles automatiquement sélectionnés dont le "
        "texte intégral ou l'Article Card a été vérifié et rendu disponible "
        "pour ce tour. Ne relance ni EnnoDiagnostic ni EnnoScholar. Renforce les "
        "arguments faibles avec des preuves traçables, conserve les faits et "
        "références existants, et n'ajoute aucune affirmation non soutenue."
    ).strip()



def _progressive_write_instruction(
    workflow: dict[str, Any],
    unit: dict[str, Any],
    *,
    scientific: bool,
) -> str:
    title = str(unit.get("section_title") or "").strip()
    section_ref = str(unit.get("section_ref") or "").strip()
    label = " — ".join(value for value in (section_ref, title) if value)

    if scientific:
        style_clause = (
            " Améliore également le style, la clarté et les transitions des "
            "passages faibles de cette section, tout en conservant les passages "
            "déjà solides."
            if unit.get("needs_editorial_rewrite")
            else ""
        )
        return (
            "Tu es dans le workflow automatique d'amélioration du CIR complet, "
            "mais tu dois rédiger UNIQUEMENT la section courante.\n\n"
            f"SECTION COURANTE : {label or 'section active'}\n\n"
            "Renforce les arguments faibles uniquement avec les sources déjà "
            "validées pour CETTE section. Utilise toutes les preuves acceptées "
            "qui ont été rendues disponibles pour ce tour. Ne relance aucune "
            "recherche. Conserve tous les faits, nombres, références, figures, "
            "tableaux, résultats et arguments existants. N'affaiblis ni ne "
            "supprime les passages déjà solides. Ajoute seulement des arguments "
            "réellement soutenus par les preuves validées."
            + style_clause
        ).strip()

    return (
        "Tu es dans le workflow automatique d'amélioration du CIR complet, "
        "mais tu dois rédiger UNIQUEMENT la section courante.\n\n"
        f"SECTION COURANTE : {label or 'section active'}\n\n"
        "Améliore le style, la clarté et les transitions uniquement là où cette "
        "section en a besoin. Conserve les passages déjà solides et tous les "
        "faits existants. Ne lance aucune recherche et n'ajoute aucun argument "
        "scientifique nouveau."
    ).strip()



def _progressive_no_source_fallback(
    db: Session,
    session: ImprovementSession,
    workflow: dict[str, Any],
    unit: dict[str, Any],
    *,
    reason: str,
) -> None:
    """Une recherche vide ne bloque jamais le parcours complet."""
    research = dict(unit.get("research") or {})
    research.update(
        {
            "candidate_count": int(
                research.get("candidate_count") or 0
            ),
            "accepted_sources": [],
            "ready_article_ids": [],
            "warning": "no_scientific_source_found",
            "warning_detail": str(reason or "")[:600],
        }
    )
    unit["research"] = research
    unit["status"] = "pending"

    stats = workflow.setdefault("stats", {})
    stats["research_without_sources"] = int(
        stats.get("research_without_sources") or 0
    ) + 1

    if unit.get("needs_editorial_rewrite"):
        unit["action"] = "rewrite"
        next_action = (
            "Je poursuis donc avec une amélioration éditoriale à faits constants "
            "pour cette section, sans inventer de justification scientifique."
        )
    else:
        unit["action"] = "keep"
        next_action = (
            "Je conserve donc cette section telle quelle et je poursuis le CIR, "
            "sans inventer de justification scientifique."
        )

    workflow["phase"] = "running"
    workflow["last_progress"] = {
        "unit_id": unit.get("unit_id"),
        "stage": "research_without_sources",
    }
    _progressive_store(
        session,
        workflow,
        handoff=None,
        research_sources=[],
        accepted_sources=[],
    )
    session.state = "audit"

    _add_message(
        db,
        session.id,
        "assistant",
        (
            f"{progressive_progress_label(workflow, unit)} : aucune source "
            "scientifique exploitable n'a été trouvée pour ce renforcement. "
            + next_action
        ),
        intent="progressive_cir_section_research_empty",
        metadata={
            "workflow": progressive_workflow_public_summary(workflow),
            "unit_id": unit.get("unit_id"),
            "reason": str(reason or "")[:600],
        },
    )
    db.commit()
    db.refresh(session)


def _progressive_scientific_write_fallback(
    db: Session,
    session: ImprovementSession,
    workflow: dict[str, Any],
    unit: dict[str, Any],
    *,
    reason: str,
) -> None:
    """Traite un échec de rédaction sans prétendre que les sources manquent."""

    research = dict(unit.get("research") or {})
    ready_article_ids = [
        int(value)
        for value in (research.get("ready_article_ids") or [])
        if int(value) > 0
    ]
    research.update(
        {
            "warning": "scientific_revision_not_validated",
            "warning_detail": str(reason or "")[:600],
            "ready_article_ids": list(dict.fromkeys(ready_article_ids)),
        }
    )
    unit["research"] = research
    unit["status"] = "pending"

    stats = workflow.setdefault("stats", {})
    stats["scientific_write_failures"] = int(
        stats.get("scientific_write_failures") or 0
    ) + 1

    if unit.get("needs_editorial_rewrite"):
        unit["action"] = "rewrite"
        next_action = (
            "Je poursuis avec l'amélioration éditoriale à faits constants, "
            "sans perdre le texte original ni transformer les sources en "
            "affirmations non validées."
        )
    else:
        unit["action"] = "keep"
        next_action = (
            "Je conserve la section originale et poursuis le CIR, sans "
            "présenter cette tentative comme un renforcement validé."
        )

    workflow["phase"] = "running"
    workflow["last_progress"] = {
        "unit_id": unit.get("unit_id"),
        "stage": "scientific_revision_not_validated",
    }
    _progressive_store(
        session,
        workflow,
        handoff=None,
        research_sources=[],
        accepted_sources=[],
    )
    session.state = "audit"

    _add_message(
        db,
        session.id,
        "assistant",
        (
            f"{progressive_progress_label(workflow, unit)} : "
            f"{len(ready_article_ids)} source(s) ont bien été préparée(s), "
            "mais la révision scientifique n'a pas passé tous les contrôles. "
            + next_action
        ),
        intent="progressive_cir_scientific_revision_not_validated",
        metadata={
            "workflow": progressive_workflow_public_summary(workflow),
            "unit_id": unit.get("unit_id"),
            "ready_article_ids": ready_article_ids,
            "reason": str(reason or "")[:600],
        },
    )
    db.commit()
    db.refresh(session)


def _progressive_unwritable_fallback(
    db: Session,
    session: ImprovementSession,
    workflow: dict[str, Any],
    unit: dict[str, Any],
    *,
    reason: str,
) -> None:
    """Isole un échec sûr à l'unité au lieu de casser tout le CIR."""
    progressive_mark_kept(
        workflow,
        unit,
    )
    unit["action"] = "keep"
    unit["generation"] = {
        **dict(unit.get("generation") or {}),
        "skipped": True,
        "skip_policy": "preserve_original_and_continue",
        "skip_reason": str(reason or "")[:1200],
    }
    stats = workflow.setdefault("stats", {})
    stats["unwritable_kept"] = int(
        stats.get("unwritable_kept") or 0
    ) + 1
    progressive_advance_cursor(workflow)
    workflow["phase"] = "running"
    workflow["last_progress"] = {
        "unit_id": unit.get("unit_id"),
        "stage": "unwritable_kept",
        "reason": str(reason or "")[:600],
    }
    _progressive_store(
        session,
        workflow,
        handoff=None,
        research_sources=[],
        accepted_sources=[],
    )
    session.state = "audit"
    _add_message(
        db,
        session.id,
        "assistant",
        (
            f"{progressive_progress_label(workflow, unit)} n'a pas fourni "
            "de base suffisamment exploitable pour une réécriture sûre. "
            "Je conserve son texte original et je poursuis le CIR."
        ),
        intent="progressive_cir_unit_preserved",
        metadata={
            "workflow": progressive_workflow_public_summary(workflow),
            "unit_id": unit.get("unit_id"),
            "reason": str(reason or "")[:600],
            "policy": "preserve_original_and_continue",
        },
    )
    db.commit()
    db.refresh(session)


def _automatic_prepare_selected_sources(
    db: Session,
    project: Project,
    handoff: dict[str, Any],
    sources: list[dict[str, Any]],
    selected_candidate_ids: list[str],
) -> dict[str, Any]:
    """Accepte côté agent, extrait et filtre les sources réellement prêtes."""

    available_candidate_ids = {
        str(row.get("candidate_id") or "").strip()
        for row in sources
        if str(row.get("candidate_id") or "").strip()
    }
    candidate_ids = [
        str(value or "").strip()
        for value in selected_candidate_ids
        if str(value or "").strip() in available_candidate_ids
    ]
    candidate_ids = list(dict.fromkeys(candidate_ids))
    if not candidate_ids:
        raise RuntimeError(
            "AUTO_EVIDENCE_SELECTED_CANDIDATES_NOT_MAPPABLE"
        )

    guided_session_id = str(handoff.get("guided_session_id") or "").strip()
    if not guided_session_id:
        raise RuntimeError("AUTO_EVIDENCE_GUIDED_SESSION_MISSING")

    from services.guided_research_service import (
        decide_guided_research_sources,
        read_guided_research_session,
    )

    decision = decide_guided_research_sources(
        db,
        project,
        session_id=guided_session_id,
        candidate_ids=candidate_ids,
        decision="accepted",
        reason=(
            "Sélection automatique EnnoAmel : pertinence directe pour la "
            "faiblesse scientifique de la section courante."
        ),
        prepare_after_acceptance=True,
        decision_actor="ennoamel_auto",
    )
    snapshot = read_guided_research_session(
        db,
        guided_session_id,
    )
    prepared_sources = _public_research_sources(
        list(
            (snapshot.get("artifacts") or {}).get("selected_sources")
            or []
        )
    )
    ready_sources = [
        row
        for row in prepared_sources
        if row.get("consultant_decision") == "accepted"
        and row.get("article_card_ready") is True
        and row.get("article_id") is not None
    ]
    ready_article_ids = list(
        dict.fromkeys(
            int(row["article_id"])
            for row in ready_sources
            if int(row["article_id"]) > 0
        )
    )
    return {
        "candidate_ids": candidate_ids,
        "decision": decision,
        "prepared_sources": prepared_sources,
        "ready_sources": ready_sources,
        "ready_article_ids": ready_article_ids,
    }


def _progressive_launch_current_research(
    db: Session,
    project: Project,
    session: ImprovementSession,
    workflow: dict[str, Any],
    unit: dict[str, Any],
) -> tuple[ImprovementSession, ImprovementVersion | None]:
    """V3.20 : recherche + sélection automatique + rédaction.

    Il n'existe plus d'état awaiting_sources dans le mode CIR complet.
    """
    _, base_text = _progressive_base_text(
        session,
        workflow,
    )

    request = _progressive_request_for_unit(
        workflow=workflow,
        base_text=base_text,
        unit=unit,
        project=project,
        instruction=_progressive_auto_research_instruction(
            workflow,
            unit,
        ),
    )

    try:
        result = get_improvement_agent().improve(
            db,
            project,
            request,
        )
        handoff = _research_handoff_from_agent_result(
            result,
            session,
        )

        if handoff is None:
            scholar_payload = (
                result.evidence.get("scholar")
                if isinstance(result.evidence, dict)
                else None
            )
            needs_handoff = bool(
                result.routing.needs_scholar
                and not result.routing.forbids_scholar
                and not result.routing.forbids_new_research
                and (
                    result.routing.needs_new_research
                    or not isinstance(scholar_payload, dict)
                    or not scholar_payload.get("available")
                )
            )
            if needs_handoff:
                handoff = _start_typed_research_inside_improvement(
                    db,
                    project,
                    session,
                    request,
                    result,
                )
    except Exception as exc:
        _progressive_no_source_fallback(
            db,
            session,
            workflow,
            unit,
            reason=f"{exc.__class__.__name__}: {exc}",
        )
        return _progressive_advance(
            db,
            project,
            session,
            workflow,
        )

    if handoff is None or not handoff.get("ok"):
        _progressive_no_source_fallback(
            db,
            session,
            workflow,
            unit,
            reason="Aucune session scientifique exploitable n'a été créée.",
        )
        return _progressive_advance(
            db,
            project,
            session,
            workflow,
        )

    sources = [
        dict(row)
        for row in (handoff.get("sources") or [])
        if isinstance(row, dict)
    ]

    if not sources:
        _progressive_no_source_fallback(
            db,
            session,
            workflow,
            unit,
            reason="La recherche ciblée a retourné 0 source candidate.",
        )
        return _progressive_advance(
            db,
            project,
            session,
            workflow,
        )

    target_text = progressive_unit_source(
        base_text,
        unit,
    )

    selection = select_sources(
        section_text=target_text,
        section_title=str(
            unit.get("section_title") or ""
        ),
        weakness_reasons=list(
            unit.get("weakness_reasons") or []
        ),
        candidate_sources=sources,
        max_selected=3,
    )

    selected_ids = [
        int(value)
        for value in (
            selection.get("selected_article_ids") or []
        )
        if int(value) > 0
    ]
    selected_candidate_ids = [
        str(value or "").strip()
        for value in (
            selection.get("selected_candidate_ids") or []
        )
        if str(value or "").strip()
    ]

    unit["research"] = {
        "guided_session_id": handoff.get("guided_session_id"),
        "corpus_scope_id": handoff.get("corpus_scope_id"),
        "candidate_count": len(sources),
        "selection_mode": "automatic",
        "auto_selection": selection,
        "accepted_sources": [],
        "ready_article_ids": [],
    }

    workflow["last_progress"] = {
        "unit_id": unit.get("unit_id"),
        "stage": "auto_source_selection",
    }

    if not selected_candidate_ids:
        _progressive_no_source_fallback(
            db,
            session,
            workflow,
            unit,
            reason=(
                "Aucune publication candidate n'a été jugée suffisamment "
                "directe pour cette section par le sélecteur automatique."
            ),
        )
        return _progressive_advance(
            db,
            project,
            session,
            workflow,
        )

    try:
        preparation = _automatic_prepare_selected_sources(
            db,
            project,
            handoff,
            sources,
            selected_candidate_ids,
        )
    except Exception as exc:
        _progressive_no_source_fallback(
            db,
            session,
            workflow,
            unit,
            reason=(
                "La présélection était pertinente, mais aucune extraction "
                "scientifique vérifiée n'a pu être préparée : "
                f"{exc.__class__.__name__}: {exc}"
            ),
        )
        return _progressive_advance(
            db,
            project,
            session,
            workflow,
        )

    ready_article_ids = list(
        preparation.get("ready_article_ids") or []
    )
    ready_sources = list(
        preparation.get("ready_sources") or []
    )
    prepared_sources = list(
        preparation.get("prepared_sources") or []
    )
    selection = bind_prepared_sources(
        selection=selection,
        prepared_sources=ready_sources,
    )
    selection = {
        **selection,
        "preparation": {
            "candidate_ids": list(
                preparation.get("candidate_ids") or []
            ),
            "ready_article_ids": ready_article_ids,
            "ready_count": len(ready_article_ids),
            "decision_actor": "ennoamel_auto",
            "fulltext_and_article_card_required": True,
        },
    }
    unit["research"].update(
        {
            "auto_selection": selection,
            "accepted_sources": ready_sources,
            "ready_article_ids": ready_article_ids,
            "prepared_candidate_count": len(prepared_sources),
            "selection_actor": "ennoamel_auto",
        }
    )

    if not ready_article_ids:
        _progressive_no_source_fallback(
            db,
            session,
            workflow,
            unit,
            reason=(
                "Les articles présélectionnés n'ont produit aucun texte "
                "intégral vérifié ni aucune Article Card prête."
            ),
        )
        return _progressive_advance(
            db,
            project,
            session,
            workflow,
        )

    _progressive_store(
        session,
        workflow,
        # On garde le handoff uniquement pendant le traitement de CETTE section.
        handoff=handoff,
        research_sources=prepared_sources,
        accepted_sources=ready_sources,
    )
    session.state = "evidence_ready"

    _add_message(
        db,
        session.id,
        "assistant",
        (
            f"{progressive_progress_label(workflow, unit)} : "
            f"{len(sources)} publication(s) candidate(s) analysée(s) "
            f"automatiquement ; {len(selected_candidate_ids)} présélectionnée(s), puis "
            f"{len(ready_article_ids)} retenue(s) après extraction vérifiée. "
            "Je rédige maintenant avec ces seules preuves exploitables."
        ),
        intent="progressive_cir_auto_evidence_selected",
        metadata={
            "workflow": progressive_workflow_public_summary(workflow),
            "unit_id": unit.get("unit_id"),
            "candidate_count": len(sources),
            "preselected_article_ids": selected_ids,
            "preselected_candidate_ids": selected_candidate_ids,
            "ready_article_ids": ready_article_ids,
            "selection_actor": "ennoamel_auto",
            "selection": selection,
            "granularity": "section",
        },
    )
    db.commit()
    db.refresh(session)

    try:
        _progressive_write_current_unit(
            db,
            project,
            session,
            workflow,
            unit,
            scientific=True,
            auto_article_ids=ready_article_ids,
            auto_selection=selection,
        )
    except Exception as exc:
        # Ici les Article Cards sont déjà prêtes. Ne jamais convertir un échec
        # de rédaction/contrôle en faux message « aucune source exploitable ».
        _progressive_scientific_write_fallback(
            db,
            session,
            workflow,
            unit,
            reason=f"{exc.__class__.__name__}: {exc}",
        )

    return _progressive_advance(
        db,
        project,
        session,
        workflow,
    )




def _progressive_write_current_unit(
    db: Session,
    project: Project,
    session: ImprovementSession,
    workflow: dict[str, Any],
    unit: dict[str, Any],
    *,
    scientific: bool,
    auto_article_ids: list[int] | None = None,
    auto_selection: dict[str, Any] | None = None,
) -> None:
    _, base_text = _progressive_base_text(
        session,
        workflow,
    )

    article_ids: list[int] = []
    evidence_scope_id: str | None = None
    guided_session_id: str | None = None

    research = dict(unit.get("research") or {})

    if scientific:
        # V3.20 : en CIR complet, la sélection est automatique. Le chemin
        # manuel reste disponible uniquement si auto_article_ids n'est pas fourni.
        if auto_article_ids is not None:
            article_ids = [
                int(value)
                for value in auto_article_ids
                if int(value) > 0
            ]
            evidence_scope_id = (
                str(research.get("corpus_scope_id") or "")
                or None
            )
            guided_session_id = (
                str(research.get("guided_session_id") or "")
                or None
            )
            if not article_ids:
                raise RuntimeError(
                    "AUTO_EVIDENCE_EMPTY_SELECTION"
                )
        else:
            bundle = _accepted_evidence_bundle(
                db,
                project,
                session,
            )
            article_ids = list(
                bundle.get("article_ids") or []
            )
            evidence_scope_id = (
                str(bundle.get("corpus_scope_id") or "")
                or None
            )
            guided_session_id = (
                str(bundle.get("guided_session_id") or "")
                or None
            )

            if not article_ids:
                unit["status"] = "awaiting_sources"
                workflow["phase"] = "awaiting_sources"
                raise RuntimeError(
                    "Aucune preuve validée et prête n'est disponible pour "
                    "la section courante."
                )

    request = _progressive_request_for_unit(
        workflow=workflow,
        base_text=base_text,
        unit=unit,
        project=project,
        instruction=(
            _progressive_auto_write_instruction(
                workflow,
                unit,
            )
            if scientific and auto_article_ids is not None
            else _progressive_write_instruction(
                workflow,
                unit,
                scientific=scientific,
            )
        ),
        article_ids=article_ids,
        evidence_scope_id=evidence_scope_id,
    )

    if scientific:
        # Double verrou contre V3.12/V3.14 : le tour de rédaction ne doit jamais
        # être réinterprété comme une nouvelle demande de recherche.
        request = request.model_copy(
            update={
                "research_choice": "USE_EXISTING_SOURCES",
                "guided_research_session_id": guided_session_id,
            }
        )

    result = get_improvement_agent().improve(
        db,
        project,
        request,
    )

    if not result.ok or not str(
        result.improved_target or ""
    ).strip():
        raise ProgressiveUnitUnwritable(
            result.assistant_message
            or "Le Writer n'a pas produit de version exploitable de la section."
        )

    final_trace: dict[str, Any] = {}

    if scientific and auto_article_ids is not None:
        final_trace = build_traceable_evidence(
            result=result,
            selection=dict(auto_selection or {}),
        )

        # Une source n'est "retenue automatiquement" que si Article Cards /
        # extraction a effectivement produit une preuve exploitable.
        if int(final_trace.get("writing_ready_count") or 0) <= 0:
            # Les Article Cards ont déjà été préparées et transmises au writer.
            # Un rattachement incomplet dans revision_integrity est désormais une
            # alerte consultative, jamais un pare-feu qui détruit la candidate.
            advisory_sources = list(
                final_trace.get("advisory_sources") or []
            )
            final_trace.update(
                {
                    "auto_accepted": advisory_sources,
                    "auto_accepted_article_ids": list(
                        dict.fromkeys(
                            int(row.get("article_id"))
                            for row in advisory_sources
                            if row.get("article_id")
                        )
                    ),
                    "auto_accepted_candidate_ids": list(
                        dict.fromkeys(
                            str(row.get("candidate_id") or "")
                            for row in advisory_sources
                            if str(row.get("candidate_id") or "").strip()
                        )
                    ),
                    "writing_ready_count": len(advisory_sources),
                    "traceability_complete": False,
                    "control_mode": "advisory_only",
                    "advisory_warnings": [
                        "Le contrôle automatique n'a pas relié chaque passage "
                        "à son extrait avec certitude. La proposition reste "
                        "visible pour validation du consultant."
                    ],
                }
            )
        else:
            final_trace["traceability_complete"] = True
            final_trace["control_mode"] = "advisory_only"

        research.update(
            {
                "selection_mode": "automatic",
                "auto_selection": dict(auto_selection or {}),
                "final_evidence": final_trace,
                "accepted_sources": list(
                    final_trace.get("auto_accepted") or []
                ),
                "ready_article_ids": list(
                    final_trace.get("auto_accepted_article_ids") or []
                ),
            }
        )
        unit["research"] = research

        stats = workflow.setdefault("stats", {})
        stats["auto_evidence_sections"] = int(
            stats.get("auto_evidence_sections") or 0
        ) + 1
        stats["auto_sources_used"] = int(
            stats.get("auto_sources_used") or 0
        ) + int(final_trace.get("writing_ready_count") or 0)

    elif scientific:
        bundle = _accepted_evidence_bundle(
            db,
            project,
            session,
        )
        research.update(
            {
                "selection_mode": "human",
                "ready_article_ids": list(
                    bundle.get("article_ids") or []
                ),
                "accepted_sources": list(
                    bundle.get("sources") or []
                ),
                "corpus_scope_id": evidence_scope_id,
            }
        )
        unit["research"] = research

    progressive_add_patch(
        workflow,
        unit,
        str(result.improved_target),
        mode=(
            "scientific"
            if scientific
            else "editorial"
        ),
        generation={
            **dict(result.generation or {}),
            "sources_used": list(
                result.sources_used or []
            ),
            "unsupported_claims": list(
                result.unsupported_claims or []
            ),
            "questions_for_consultant": list(
                result.questions_for_consultant or []
            ),
            "auto_evidence": final_trace,
        },
    )

    progressive_advance_cursor(workflow)
    workflow["phase"] = "running"
    workflow["last_progress"] = {
        "unit_id": unit.get("unit_id"),
        "stage": (
            "strengthened_auto"
            if scientific and auto_article_ids is not None
            else "strengthened"
            if scientific
            else "rewritten"
        ),
    }

    # Isolation stricte des sources entre sections.
    _progressive_store(
        session,
        workflow,
        handoff=None,
        research_sources=[],
        accepted_sources=[],
    )
    session.state = "audit"

    if scientific and auto_article_ids is not None:
        kept = int(
            final_trace.get("writing_ready_count") or 0
        )
        candidate_count = int(
            (auto_selection or {}).get("candidate_count") or 0
        )
        progress_message = (
            f"{progressive_progress_label(workflow, unit)} renforcée "
            f"automatiquement : {candidate_count} publication(s) examinée(s), "
            f"{kept} source(s) réellement retenue(s) avec preuve exploitable. "
            "Je passe à la section suivante."
        )
        intent = "progressive_cir_auto_evidence_completed"
    else:
        progress_message = (
            f"{progressive_progress_label(workflow, unit)} "
            + (
                "renforcée avec les sources validées."
                if scientific
                else "améliorée à faits constants, sans recherche."
            )
            + " Je passe à la section suivante."
        )
        intent = "progressive_cir_unit_completed"

    _add_message(
        db,
        session.id,
        "assistant",
        progress_message,
        intent=intent,
        metadata={
            "workflow": progressive_workflow_public_summary(workflow),
            "unit_id": unit.get("unit_id"),
            "mode": (
                "scientific_auto"
                if scientific and auto_article_ids is not None
                else "scientific"
                if scientific
                else "editorial"
            ),
            "granularity": "section",
            "auto_evidence": final_trace,
        },
    )

    db.commit()
    db.refresh(session)




def _progressive_source_identity(row: dict[str, Any]) -> str:
    article_id = str(row.get("article_id") or "").strip()
    if article_id:
        return f"article:{article_id}"
    candidate_id = str(row.get("candidate_id") or "").strip()
    if candidate_id:
        return f"candidate:{candidate_id}"
    return "title:" + re.sub(
        r"\s+",
        " ",
        str(row.get("title") or "").strip().casefold(),
    )


def _progressive_unit_sources(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """Construit la vue article + passage utilisée dans le comparatif."""

    research = dict(unit.get("research") or {})
    selection = dict(research.get("auto_selection") or {})
    final_evidence = dict(research.get("final_evidence") or {})
    rows: list[dict[str, Any]] = []
    for value in (
        selection.get("selected") or [],
        research.get("accepted_sources") or [],
        final_evidence.get("advisory_sources") or [],
        final_evidence.get("auto_accepted") or [],
    ):
        rows.extend(
            dict(row)
            for row in value
            if isinstance(row, dict)
        )

    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _progressive_source_identity(row)
        if key == "title:":
            continue
        current = dict(merged.get(key) or {})
        for field, value in row.items():
            if value not in (None, "", [], {}):
                current[field] = value
        merged[key] = current

    output: list[dict[str, Any]] = []
    for row in merged.values():
        excerpt = str(
            row.get("evidence_excerpt")
            or row.get("evidence_text")
            or row.get("quote")
            or row.get("abstract")
            or row.get("abstract_or_snippet")
            or ""
        ).strip()
        output.append(
            {
                **row,
                "evidence_id": (
                    str(row.get("citation_id") or "").strip()
                    or f"S:{row.get('article_id') or row.get('candidate_id')}:article"
                ),
                "evidence_excerpt": excerpt[:5000],
                "section_id": unit.get("section_id"),
                "section_ref": unit.get("section_ref"),
                "section_title": unit.get("section_title"),
                "control_mode": "advisory_only",
            }
        )
    return output


def _progressive_structured_result(
    workflow: dict[str, Any],
    base_text: str,
) -> dict[str, Any]:
    """Sérialise passages, articles et alertes pour le comparatif frontend."""

    patches = {
        str(row.get("unit_id") or ""): dict(row)
        for row in (workflow.get("patches") or [])
        if isinstance(row, dict)
    }
    changes: list[dict[str, Any]] = []
    all_sources: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    questions: list[str] = []

    for unit in workflow.get("units") or []:
        if not isinstance(unit, dict):
            continue
        patch = patches.get(str(unit.get("unit_id") or ""))
        if patch is None:
            continue
        sources = _progressive_unit_sources(unit)
        for source in sources:
            all_sources[_progressive_source_identity(source)] = source

        mode = str(patch.get("mode") or "editorial")
        changes.append(
            {
                "change_id": f"section-{unit.get('unit_id')}",
                "operation": (
                    "renforcement scientifique"
                    if mode == "scientific"
                    else "amélioration éditoriale"
                ),
                "before": progressive_unit_source(base_text, unit),
                "after": str(patch.get("replacement") or ""),
                "reason": (
                    "Section renforcée avec les articles et extraits préparés ; "
                    "les alertes automatiques restent soumises au consultant."
                    if mode == "scientific"
                    else "Formulation améliorée à faits constants."
                ),
                "section_id": unit.get("section_id"),
                "section_ref": unit.get("section_ref"),
                "section_title": unit.get("section_title"),
                "evidence_refs": [
                    str(source.get("evidence_id") or "")
                    for source in sources
                    if str(source.get("evidence_id") or "").strip()
                ],
                "sources": sources,
            }
        )

        generation = dict(unit.get("generation") or {})
        for row in generation.get("unsupported_claims") or []:
            if isinstance(row, dict):
                warnings.append({**row, "severity": "warning"})
        raw_warnings = list(generation.get("quality_warnings") or [])
        raw_warnings.extend(generation.get("conservation_issues") or [])
        raw_warnings.extend(generation.get("integrity_warnings") or [])
        raw_warnings.extend(
            dict(unit.get("research") or {})
            .get("final_evidence", {})
            .get("advisory_warnings", [])
        )
        for index, warning in enumerate(raw_warnings, start=1):
            value = str(warning or "").strip()
            if value:
                warnings.append(
                    {
                        "change_id": f"section-{unit.get('unit_id')}-warning-{index}",
                        "claim": "",
                        "reason": value,
                        "severity": "warning",
                    }
                )
        for question in generation.get("questions_for_consultant") or []:
            value = str(question or "").strip()
            if value and value not in questions:
                questions.append(value)

    unique_warnings: list[dict[str, Any]] = []
    seen_warnings: set[str] = set()
    for warning in warnings:
        key = str(warning.get("reason") or warning.get("claim") or "").strip()
        if not key or key in seen_warnings:
            continue
        seen_warnings.add(key)
        unique_warnings.append(warning)

    if unique_warnings:
        questions.insert(
            0,
            "Les contrôles automatiques sont consultatifs : vérifiez les alertes "
            "dans le comparatif avant d'accepter ou de demander une correction.",
        )

    return {
        "changes": changes,
        "sources_used": list(all_sources.values()),
        "agents_used": [
            "EnnoAmelioration",
            *(["EnnoScholar"] if all_sources else []),
        ],
        "unsupported_claims": unique_warnings,
        "questions_for_consultant": questions,
        "scholar_used": bool(all_sources),
        "blocking": False,
        "has_warnings": bool(unique_warnings),
        "quality_control_mode": "advisory_only",
    }


def _progressive_finalize(
    db: Session,
    session: ImprovementSession,
    workflow: dict[str, Any],
) -> tuple[ImprovementSession, ImprovementVersion | None]:
    base_version, base_text = _progressive_base_text(
        session,
        workflow,
    )
    final_text = progressive_apply_patches(
        base_text,
        workflow.get("patches") or [],
    )

    workflow["phase"] = "completed"
    workflow["active"] = False
    workflow["cursor"] = len(
        workflow.get("units") or []
    )

    _progressive_store(
        session,
        workflow,
        handoff=None,
        research_sources=[],
        accepted_sources=[],
    )

    candidate: ImprovementVersion | None = None

    if final_text != base_text:
        structured_result = _progressive_structured_result(
            workflow,
            base_text,
        )
        scholar_sources = list(
            structured_result.get("sources_used") or []
        )
        number = (
            max(
                [
                    row.version_number
                    for row in session.versions
                ]
                or [0]
            )
            + 1
        )

        for row in session.versions:
            if row.status == "candidate":
                row.status = "superseded"

        candidate = ImprovementVersion(
            id=_new_id(),
            session_id=session.id,
            version_number=number,
            status="candidate",
            content=final_text,
            parent_version_id=base_version.id,
            instruction=str(
                workflow.get("instruction") or ""
            ),
            diff_json=_make_diff(
                base_text,
                final_text,
            ),
            audit_json={
                "progressive_sections": [
                    {
                        "unit_id": row.get("unit_id"),
                        "section_id": row.get("section_id"),
                        "section_ref": row.get("section_ref"),
                        "section_title": row.get("section_title"),
                        "status": row.get("status"),
                        "action": row.get("action"),
                        "weakness_reasons": (
                            row.get("weakness_reasons") or []
                        ),
                        "audit": row.get("audit") or [],
                    }
                    for row in (
                        workflow.get("units") or []
                    )
                ]
            },
            evidence_json={
                "policy": "section_scoped_validated_sources_only",
                "scholar": {
                    "available": bool(scholar_sources),
                    "selected_article_count": len(scholar_sources),
                    "writing_ready_card_count": len(scholar_sources),
                    "evidence": scholar_sources,
                    "control_mode": "advisory_only",
                },
                "sections": [
                    {
                        "unit_id": row.get("unit_id"),
                        "section_id": row.get("section_id"),
                        "section_ref": row.get("section_ref"),
                        "section_title": row.get("section_title"),
                        "research": row.get("research") or {},
                    }
                    for row in (
                        workflow.get("units") or []
                    )
                    if row.get("research")
                ],
            },
            generation_json={
                "strategy": WORKFLOW_VERSION,
                "granularity": "section",
                "workflow": progressive_workflow_public_summary(
                    workflow
                ),
                "document_preservation": dict(
                    (session.context_json or {}).get(
                        "document_preservation"
                    )
                    or {}
                ),
                "base_version_id": base_version.id,
                "active_version_modified": False,
                "quality_control_mode": "advisory_only",
                "structured_result": structured_result,
            },
            created_at=_utcnow(),
        )

        db.add(candidate)
        session.state = "candidate_ready"
    else:
        session.state = "review"

    stats = dict(
        workflow.get("stats") or {}
    )

    _add_message(
        db,
        session.id,
        "assistant",
        (
            "Parcours du CIR terminé section par section. "
            f"{stats.get('kept', 0)} section(s) conservée(s), "
            f"{stats.get('rewritten', 0)} améliorée(s) sans recherche, "
            f"{stats.get('strengthened', 0)} renforcée(s) avec des sources "
            "validées et "
            f"{stats.get('research_without_sources', 0)} section(s) sans "
            "source exploitable signalée(s). "
            + (
                "Une seule candidate du CIR complet est prête pour votre "
                "validation ; la version active n'a pas été remplacée."
                if candidate is not None
                else "Aucune modification n'a été nécessaire ; la version "
                "active est conservée."
            )
        ),
        intent="progressive_cir_completed",
        metadata={
            "candidate_version_id": (
                candidate.id
                if candidate
                else None
            ),
            "workflow": progressive_workflow_public_summary(
                workflow
            ),
            "granularity": "section",
        },
    )

    db.commit()
    db.refresh(session)
    return session, candidate



def _progressive_advance(
    db: Session,
    project: Project,
    session: ImprovementSession,
    workflow: dict[str, Any],
) -> tuple[ImprovementSession, ImprovementVersion | None]:
    automatic_writes = 0
    max_writes = progressive_max_auto_writes_per_turn()
    _, workflow_base_text = _progressive_base_text(
        session,
        workflow,
    )
    if progressive_refresh_pending_units(
        workflow,
        workflow_base_text,
    ):
        _progressive_store(session, workflow)
        db.commit()
        db.refresh(session)

    # Une seule résolution structurée sur le CIR complet. Tous les tours non
    # ambigus réutilisent ce cache ; seul un tour marqué ambigu peut demander un
    # ScopedDiagnostic.
    _ensure_progressive_initial_diagnostic(
        db,
        project,
        session,
        workflow,
    )

    while True:
        unit = progressive_current_unit(workflow)

        if unit is None:
            return _progressive_finalize(
                db,
                session,
                workflow,
            )

        status = str(
            unit.get("status") or "pending"
        )
        action = str(
            unit.get("action") or "keep"
        )

        # Auto-réparation des workflows déjà persistés par l'ancienne V3.20 :
        # le titre était compté comme contenu et pouvait être envoyé à
        # EnnoDiagnostic/Writer. Aucun nouvel appel LLM n'est nécessaire.
        if progressive_unit_is_title_only(
            workflow_base_text,
            unit,
        ):
            unit.update(
                {
                    "title_only": True,
                    "weak": False,
                    "action": "keep",
                    "needs_research": False,
                    "needs_editorial_rewrite": False,
                    "weakness_reasons": [],
                    "audit": [],
                    "diagnostic_ambiguous": False,
                    "diagnostic_policy": "title_only_no_diagnostic",
                }
            )
            action = "keep"

        if status in {
            "kept",
            "rewritten",
            "strengthened",
        }:
            progressive_advance_cursor(
                workflow
            )
            continue

        if action == "keep":
            progressive_mark_kept(
                workflow,
                unit,
            )
            progressive_advance_cursor(
                workflow
            )
            workflow["last_progress"] = {
                "unit_id": unit.get("unit_id"),
                "stage": "kept",
            }
            _progressive_store(
                session,
                workflow,
            )
            continue

        if action == "research":
            if status in {
                "evidence_ready",
                "awaiting_sources",
            }:
                if status == "evidence_ready":
                    try:
                        _progressive_write_current_unit(
                            db,
                            project,
                            session,
                            workflow,
                            unit,
                            scientific=True,
                        )
                    except ProgressiveUnitUnwritable as exc:
                        _progressive_unwritable_fallback(
                            db,
                            session,
                            workflow,
                            unit,
                            reason=str(exc),
                        )
                        continue
                    automatic_writes += 1

                    if automatic_writes >= max_writes:
                        current = progressive_current_unit(
                            workflow
                        )
                        _progressive_store(
                            session,
                            workflow,
                        )
                        _add_message(
                            db,
                            session.id,
                            "assistant",
                            (
                                f"J'ai traité {automatic_writes} section(s) "
                                "sur ce tour. "
                                + (
                                    f"Prochaine étape : "
                                    f"{progressive_progress_label(workflow, current)}. "
                                    if current is not None
                                    else ""
                                )
                                + "Le traitement continue automatiquement en arrière-plan."
                            ),
                            intent="progressive_cir_budget_checkpoint",
                            metadata={
                                "workflow": progressive_workflow_public_summary(
                                    workflow
                                ),
                                "max_auto_writes": max_writes,
                                "granularity": "section",
                            },
                        )
                        db.commit()
                        db.refresh(session)
                        return session, None
                    continue

                _progressive_store(
                    session,
                    workflow,
                )
                session.state = "awaiting_evidence"
                db.commit()
                db.refresh(session)
                return session, None

            return _progressive_launch_current_research(
                db,
                project,
                session,
                workflow,
                unit,
            )

        if action == "rewrite":
            try:
                _progressive_write_current_unit(
                    db,
                    project,
                    session,
                    workflow,
                    unit,
                    scientific=False,
                )
            except ProgressiveUnitUnwritable as exc:
                _progressive_unwritable_fallback(
                    db,
                    session,
                    workflow,
                    unit,
                    reason=str(exc),
                )
                continue
            automatic_writes += 1

            if automatic_writes >= max_writes:
                current = progressive_current_unit(
                    workflow
                )
                _progressive_store(
                    session,
                    workflow,
                )
                _add_message(
                    db,
                    session.id,
                    "assistant",
                    (
                        f"J'ai traité {automatic_writes} section(s) sur ce tour. "
                        + (
                            f"Prochaine étape : "
                            f"{progressive_progress_label(workflow, current)}. "
                            if current is not None
                            else ""
                        )
                        + "Le traitement continue automatiquement en arrière-plan."
                    ),
                    intent="progressive_cir_budget_checkpoint",
                    metadata={
                        "workflow": progressive_workflow_public_summary(
                            workflow
                        ),
                        "max_auto_writes": max_writes,
                        "granularity": "section",
                    },
                )
                db.commit()
                db.refresh(session)
                return session, None

            continue

        progressive_mark_kept(
            workflow,
            unit,
        )
        progressive_advance_cursor(
            workflow
        )



def _start_or_resume_progressive_cir(
    db: Session,
    project: Project,
    session: ImprovementSession,
    *,
    message: str,
    effective_scope: TargetScope,
    preliminary_routing: Any,
) -> tuple[ImprovementSession, ImprovementVersion | None] | None:
    workflow = _progressive_context(session)

    is_small_talk = any(
        str(getattr(intent, "value", intent)) == "small_talk"
        for intent in (getattr(preliminary_routing, "intents", []) or [])
    )

    explicit_full_document_request = message_explicitly_requests_full_document(message)

    # Même si la session est restée FULL_DOCUMENT à cause de l'ancien bug,
    # une section citée explicitement dans le message doit prendre la priorité.
    explicit_target_present = False
    try:
        active_for_target = _progressive_active_version(session)
        target_text = str(active_for_target.content or "") if active_for_target else ""
        target_sections = parse_sections(target_text) if target_text else []
        explicit_target_present = (
            infer_section_from_instruction(message, target_sections) is not None
        )
    except Exception:
        explicit_target_present = False

    action = progressive_action(
        workflow_active=bool(workflow and workflow.get("active")),
        explicit_target_present=explicit_target_present,
        explicit_full_document_request=explicit_full_document_request,
        message=message,
        small_talk=is_small_talk,
    )

    if workflow and workflow.get("active"):
        if action == ACTION_NORMAL:
            return None

        if action == ACTION_CANCEL_PROGRESSIVE_FOR_TARGET:
            workflow["active"] = False
            workflow["phase"] = "cancelled_by_targeted_request"
            workflow["last_progress"] = {
                "stage": "cancelled_by_targeted_request",
                "message": str(message or "")[:500],
            }
            _progressive_store(session, workflow)
            session.state = (
                "evidence_ready"
                if (
                    (session.context_json or {}).get("accepted_research_sources")
                    or (session.context_json or {}).get("research_sources")
                )
                else "review"
            )
            db.commit()
            db.refresh(session)
            return None

        # ACTION_RESUME_PROGRESSIVE
        unit = progressive_current_unit(workflow)
        if unit is not None and unit.get("action") == "research":
            current_context = dict(session.context_json or {})
            accepted = list(current_context.get("accepted_research_sources") or [])
            ready = [
                row for row in accepted
                if row.get("article_card_ready") is True
            ]
            if ready:
                unit["status"] = "evidence_ready"
                unit.setdefault("research", {})["accepted_sources"] = accepted
                unit["research"]["ready_article_ids"] = [
                    row.get("article_id")
                    for row in ready
                    if row.get("article_id")
                ]
                workflow["phase"] = "evidence_ready"
                _progressive_store(session, workflow)

        return _progressive_advance(db, project, session, workflow)

    # Aucun nouveau parcours complet sans demande explicite dans ce message.
    if action != ACTION_START_PROGRESSIVE:
        return None

    active = _progressive_active_version(session)
    if active is None:
        return None

    base_text = str(active.content or "")
    sections = parse_sections(base_text)
    routing = get_improvement_agent().routing_service.route(
        message,
        TargetScope.FULL_DOCUMENT,
        base_text,
        section_id=None,
        section_title=None,
        sections=sections,
    )
    workflow = progressive_build_workflow(
        base_text=base_text,
        base_version_id=active.id,
        base_version_number=active.version_number,
        instruction=message,
        sections=sections,
        routing=routing,
    )
    _progressive_store(
        session,
        workflow,
        handoff=None,
        research_sources=[],
        accepted_sources=[],
    )
    session.target_scope = TargetScope.FULL_DOCUMENT.value
    session.target_section_id = None
    session.target_section_title = None
    session.state = "audit"

    _add_message(
        db,
        session.id,
        "assistant",
        (
            "Je traite le CIR progressivement à partir de la version active. "
            f"{len(workflow.get('units') or [])} section(s) ont été identifiées. "
            "Un diagnostic structuré initial est calculé une seule fois et mis "
            "en cache ; un diagnostic ciblé n'est permis que pour une section "
            "ambiguë. "
            "Une section suffisamment solide est conservée ; une section faible sur la "
            "forme est améliorée sans recherche ; une section faible nécessitant une "
            "preuve scientifique déclenche directement EnnoScholar. L'agent "
            "sélectionne alors les articles pertinents, tente leur extraction "
            "et ne rédige qu'avec les preuves réellement exploitables, sans "
            "demander une sélection au consultant."
        ),
        intent="progressive_cir_started",
        metadata={"workflow": progressive_workflow_public_summary(workflow)},
    )
    db.commit()
    db.refresh(session)
    return _progressive_advance(db, project, session, workflow)


# END ENNOAMEL_CIR_PROGRESSIVE_V3_11

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

    # V3.5 — une candidate non validée ne devient jamais la base implicite.
    # En cas d'ambiguïté, la version active est toujours privilégiée.
    initial_scope = TargetScope(target_scope or session.target_scope or "section")
    continuation_hint = understand_instruction(message, initial_scope)
    working_version = _working_version(
        session,
        prefer_candidate=bool(continuation_hint.candidate_revision),
    )
    full_text = (
        str(working_version.content or "")
        if working_version is not None
        else _version_content(session)
    )
    # Les conversations créées avant la correction peuvent déjà contenir un
    # titre collé à la phrase précédente. On le répare dans la proposition ; la
    # version active ne change qu'après acceptation explicite du consultant.
    full_text = repair_section_boundaries(full_text)
    if str((session.context_json or {}).get("source_kind") or "") == "document":
        # Répare également les imports PDF déjà créés : le sommaire ne devient
        # pas une série de fausses sections dans la prochaine proposition.
        full_text = _clean_extracted_document_text(full_text)
    scope = initial_scope
    sections = parse_sections(full_text)
    inferred_section = (
        infer_section_from_instruction(message, sections)
        if not (selected_text and selected_text.strip())
        else None
    )
    source_kind = str((session.context_json or {}).get("source_kind") or "")
    pasted_whole_section = _pasted_section_is_whole_target(
        source_kind=source_kind,
        scope=scope,
        selected_text=selected_text,
        requested_section_id=target_section_id,
        requested_section_title=target_section_title,
        inferred_section=inferred_section,
    )

    if pasted_whole_section:
        resolved_text = full_text
        resolved_section = sections[0] if sections else None
        effective_section_id = (
            resolved_section.section_id if resolved_section is not None else None
        )
        effective_section_title = (
            resolved_section.title if resolved_section is not None else None
        )
        effective_scope = TargetScope.SECTION
    else:
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
        effective_scope = TargetScope(
        effective_scope_value(
            requested_scope=scope.value,
            selected_text_present=bool(selected_text and selected_text.strip()),
            resolved_section_present=resolved_section is not None,
        )
    )
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
    analyzed_history_for_memory: list[dict[str, Any]] = []
    for history_row in list(session.messages or [])[-40:]:
        if str(getattr(history_row, 'role', '') or '') != 'consultant':
            continue
        history_meta = dict(getattr(history_row, 'metadata_json', None) or {})
        history_scope_raw = str(history_meta.get('target_scope') or effective_scope.value)
        try:
            history_scope = TargetScope(history_scope_raw)
        except Exception:
            history_scope = effective_scope
        history_routing = understand_instruction(str(getattr(history_row, 'content', '') or ''), history_scope)
        analyzed_history_for_memory.append({
            'content': str(getattr(history_row, 'content', '') or ''),
            'target_scope': history_scope.value,
            'target_section_id': history_meta.get('target_section_id'),
            'target_section_title': history_meta.get('target_section_title'),
            'routing': history_routing.model_dump(mode='json'),
        })
    task_memory, effective_instruction = evolve_task_memory(
        existing_memory=(session_context.get('improvement_task_memory') if isinstance(session_context.get('improvement_task_memory'), dict) else None),
        raw_message=message,
        routing=preliminary_routing,
        section_id=(resolved_section.section_id if resolved_section is not None else effective_section_id),
        section_title=(resolved_section.title if resolved_section is not None else effective_section_title),
        scope=effective_scope.value,
        has_accepted_sources=has_linked_accepted_sources,
        analyzed_history=analyzed_history_for_memory,
    )
    if effective_instruction.strip() != message.strip():
        print('[EnnoAmel][TaskMemory] resume_contract=True ' + f'target={(resolved_section.section_id if resolved_section else effective_section_id)!r} ' + f'raw={message[:100]!r}')

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
            "working_base_version_id": (
                working_version.id if working_version is not None else None
            ),
            "working_base_status": (
                working_version.status if working_version is not None else None
            ),
            "candidate_continuation": bool(continuation_hint.candidate_revision),
        },
    )

    progressive_result = _start_or_resume_progressive_cir(
        db,
        project,
        session,
        message=message.strip(),
        effective_scope=effective_scope,
        preliminary_routing=preliminary_routing,
    )
    if progressive_result is not None:
        return progressive_result

    request = ImprovementRequest(
        instruction=effective_instruction.strip(),
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
        resolved_handoff = {
            **research_handoff,
            "target_section_id": (
                resolved_section.section_id if resolved_section else effective_section_id
            ),
            "target_section_title": (
                resolved_section.title if resolved_section else effective_section_title
            ),
            "target_sha256": hashlib.sha256(
                str(resolved_text or "").encode("utf-8", errors="ignore")
            ).hexdigest(),
            "fresh_research_cycle": True,
        }
        resolved_sources = list(research_handoff.get("sources") or [])
        accepted_sources = []
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
        "improvement_task_memory": task_memory,
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


def background_advance_full_cir(
    db: Session,
    project: Project,
    session_id: str,
) -> tuple[ImprovementSession, ImprovementVersion | None]:
    """Reprend directement le workflow CIR V3.20 sans passer par le chat.

    Cette fonction est réservée au worker Celery/LangGraph. Elle évite de
    fabriquer des messages consultant "continue" et ne repasse donc pas par le
    routage conversationnel à chaque checkpoint technique.
    """
    session = get_session(
        db,
        project.id,
        str(session_id),
    )
    workflow = _progressive_context(session)

    if not workflow:
        return session, None

    version = str(
        workflow.get("version") or ""
    )
    if version and not version.endswith("v3_20"):
        raise RuntimeError(
            "Le worker background V3.21 exige un workflow CIR V3.20. "
            f"Workflow trouvé : {version}"
        )

    if not workflow.get("active"):
        candidate = next(
            (
                row
                for row in session.versions
                if row.status == "candidate"
            ),
            None,
        )
        return session, candidate

    return _progressive_advance(
        db,
        project,
        session,
        workflow,
    )
