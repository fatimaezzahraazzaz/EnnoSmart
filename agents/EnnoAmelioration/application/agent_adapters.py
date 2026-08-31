from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


def _compact(value: Any, limit: int = 1000) -> str:
    if isinstance(value, str):
        return " ".join(value.split())[:limit]
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)[:limit]
        except Exception:
            return str(value)[:limit]
    return str(value or "")[:limit]


def _source_refs(value: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    """Extrait la provenance sans imposer le schéma d'un pipeline donné."""

    refs: list[dict[str, Any]] = []
    queue = [value]
    while queue and len(refs) < limit:
        current = queue.pop(0)
        if isinstance(current, list):
            queue.extend(current[:40])
            continue
        if not isinstance(current, dict):
            continue
        ref = {
            key: current.get(key)
            for key in (
                "source_id",
                "document_id",
                "document_name",
                "filename",
                "page",
                "page_number",
                "passage_id",
                "chunk_id",
                "path",
            )
            if current.get(key) not in (None, "")
        }
        if ref and ref not in refs:
            refs.append(ref)
        queue.extend(
            nested for nested in current.values() if isinstance(nested, (dict, list))
        )
    return refs


def _diagnostic_snapshot(run: Any) -> dict[str, Any]:
    raw = run.raw_result_json if isinstance(run.raw_result_json, dict) else {}
    snapshot = raw.get("diagnostic_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        return snapshot
    candidates = [
        raw.get("report"),
        (raw.get("script_or_pipeline_result") or {}).get("report")
        if isinstance(raw.get("script_or_pipeline_result"), dict)
        else None,
        raw,
    ]
    report = next((item for item in candidates if isinstance(item, dict) and item), {})
    if not report:
        return {}
    try:
        from services.diagnostic_service import extract_complete_diagnostic_snapshot

        return extract_complete_diagnostic_snapshot(report)
    except Exception:
        return {}




def _find_domain_detection(value: Any, *, max_depth: int = 8) -> dict[str, Any]:
    """Retrouve le vrai ``domain_detection`` produit par le pipeline NLP.

    Le schéma des résultats EnnoDiagnostic a évolué plusieurs fois. Cette
    recherche reste volontairement générique : elle parcourt le résultat brut
    et privilégie un objet explicitement nommé ``domain_detection`` plutôt que
    de reconstruire le domaine depuis le nom du projet ou le titre de section.
    """

    wanted_keys = {
        "display_label",
        "broad_domain_label",
        "main_domain_label",
        "sub_domain_label",
        "domain_label_niv1",
        "domain_label_niv2",
        "domain_label_niv3",
        "domain_key",
        "domain_code",
        "domain_code_niv1",
        "domain_code_niv2",
        "domain_code_niv3",
    }

    seen: set[int] = set()

    def walk(obj: Any, depth: int) -> dict[str, Any]:
        if depth > max_depth:
            return {}
        if isinstance(obj, (dict, list)):
            marker = id(obj)
            if marker in seen:
                return {}
            seen.add(marker)

        if isinstance(obj, dict):
            explicit = obj.get("domain_detection")
            if isinstance(explicit, dict) and explicit:
                return dict(explicit)

            if len(wanted_keys.intersection(obj.keys())) >= 2:
                candidate = {
                    key: obj.get(key)
                    for key in wanted_keys
                    if obj.get(key) not in (None, "", [], {})
                }
                if candidate:
                    return candidate

            priority = (
                "nlp_result",
                "nlp",
                "analysis",
                "report",
                "diagnostic_snapshot",
                "script_or_pipeline_result",
                "result",
                "payload",
            )
            for key in priority:
                child = obj.get(key)
                if isinstance(child, (dict, list)):
                    found = walk(child, depth + 1)
                    if found:
                        return found
            for child in obj.values():
                if isinstance(child, (dict, list)):
                    found = walk(child, depth + 1)
                    if found:
                        return found

        elif isinstance(obj, list):
            for child in obj[:100]:
                if isinstance(child, (dict, list)):
                    found = walk(child, depth + 1)
                    if found:
                        return found
        return {}

    return walk(value, 0)


def _search_terms(text: str) -> set[str]:
    stopwords = {
        "article",
        "articles",
        "approche",
        "approaches",
        "amelioration",
        "ameliorer",
        "analyse",
        "application",
        "applications",
        "cette",
        "comme",
        "complete",
        "contexte",
        "dans",
        "demontre",
        "developpement",
        "dossier",
        "etude",
        "evaluation",
        "experimental",
        "experimentale",
        "generale",
        "important",
        "justification",
        "methode",
        "methodes",
        "modelisation",
        "modele",
        "modeles",
        "nouvelle",
        "pour",
        "pertinence",
        "pertinent",
        "projet",
        "publication",
        "publications",
        "redaction",
        "recherche",
        "renforcement",
        "renforcer",
        "resultat",
        "resultats",
        "scientifique",
        "scientifiques",
        "source",
        "sources",
        "avec",
        "sans",
        "section",
        "simulation",
        "systeme",
        "systemes",
        "technique",
        "techniques",
        "travaux",
        "utilise",
        "validation",
        "verrou",
        "verrous",
    }
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char)).casefold()
    return {
        token
        for token in re.findall(r"\b[a-z0-9-]{5,}\b", normalized)
        if token not in stopwords
    }


def _normalized_searchable(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _compact(value, 24000))
    return "".join(char for char in text if not unicodedata.combining(char)).casefold()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item or "").strip()
    ))


def _target_bindings(card: dict[str, Any], source_json: dict[str, Any]) -> list[dict[str, Any]]:
    rows = card.get("target_bindings") or source_json.get("target_bindings") or []
    if not isinstance(rows, list):
        rows = []
    bindings = [dict(row) for row in rows if isinstance(row, dict)]
    target_ids = _string_list(
        card.get("research_target_ids") or source_json.get("research_target_ids") or []
    )
    section_ids = _string_list(
        card.get("section_ids") or source_json.get("section_ids") or []
    )
    known = {
        str(row.get("research_target_id") or row.get("parent_section_id") or "").strip()
        for row in bindings
    }
    for target_id in target_ids:
        if target_id not in known:
            bindings.append({"research_target_id": target_id})
            known.add(target_id)
    for section_id in section_ids:
        if section_id not in known:
            bindings.append({"parent_section_id": section_id})
            known.add(section_id)
    return bindings


def _binding_ids(bindings: list[dict[str, Any]]) -> set[str]:
    return {
        str(value).strip()
        for row in bindings
        for value in (row.get("research_target_id"), row.get("parent_section_id"))
        if str(value or "").strip()
    }


def diagnostic_context(db: Any, project: Any, target_text: str) -> dict[str, Any]:
    """Adapte le dernier diagnostic du projet sans le recalculer ni le muter."""

    from db.models import DiagnosticRun, Verrou

    run = (
        db.query(DiagnosticRun)
        .filter(DiagnosticRun.project_id == project.id)
        .order_by(DiagnosticRun.created_at.desc(), DiagnosticRun.id.desc())
        .first()
    )
    if run is None:
        return {
            "available": False,
            "agent": "EnnoDiagnostic",
            "reason": "Aucun diagnostic n'est disponible pour ce projet.",
            "recommendation": "La révision reste possible, mais aucune conclusion d'éligibilité ne sera ajoutée.",
            "evidence_items": [],
        }

    try:
        from services.consultant_verrou_service import get_latest_diagnostic_verrous

        rows = get_latest_diagnostic_verrous(db, int(project.id))
    except Exception:
        rows = (
            db.query(Verrou)
            .filter(Verrou.diagnostic_run_id == run.id)
            .order_by(Verrou.score.desc(), Verrou.created_at.asc())
            .all()
        )
    kept = [row for row in rows if str(row.consultant_status or "").casefold() == "garde"] or rows
    snapshot = _diagnostic_snapshot(run)
    raw_result = run.raw_result_json if isinstance(run.raw_result_json, dict) else {}
    domain_detection = (
        _find_domain_detection(raw_result)
        or _find_domain_detection(snapshot)
    )
    evidence_items: list[dict[str, Any]] = []
    verrou_payload: list[dict[str, Any]] = []
    for row in kept[:20]:
        source_json = row.source_json if isinstance(row.source_json, dict) else {}
        text = "\n".join(
            part
            for part in (
                str(row.title or "").strip(),
                _compact(row.justification, 1800),
                _compact(source_json.get("evidence_summary"), 1800),
                _compact(source_json.get("scientific_lock"), 1400),
                _compact(source_json.get("why_not_simple_engineering"), 1400),
                _compact(
                    source_json.get("passages")
                    or source_json.get("sources")
                    or source_json.get("evidence"),
                    3000,
                ),
            )
            if str(part or "").strip()
        )
        source_refs = _source_refs(source_json)
        verrou_payload.append(
            {
                "id": row.id,
                "title": row.title,
                "tag_cir": row.tag_cir,
                "score": row.score,
                "justification": _compact(row.justification, 1800),
                "consultant_status": row.consultant_status,
                "source_refs": source_refs,
                "evidence_text": text[:7000],
            }
        )
        evidence_items.append(
            {
                "evidence_id": f"D:verrou:{row.id}",
                "type": "diagnostic_lock",
                "title": str(row.title or ""),
                "text": text,
                "source_refs": source_refs,
                "fact_eligible": True,
                "consultant_status": row.consultant_status,
            }
        )

    terms = _search_terms(target_text)
    canonical = snapshot.get("canonical_sections") if isinstance(snapshot, dict) else {}
    scored_sections: list[tuple[int, str, str]] = []
    if isinstance(canonical, dict):
        for key, value in canonical.items():
            body = str(value or "").strip()
            if body:
                scored_sections.append(
                    (sum(1 for term in terms if term in body.casefold()), str(key), body)
                )
    scored_sections.sort(key=lambda item: item[0], reverse=True)
    diagnostic_sections: dict[str, str] = {}
    for _, key, body in scored_sections[:8]:
        diagnostic_sections[key] = body[:5000]
        evidence_items.append(
            {
                "evidence_id": f"D:section:{key}",
                "type": "diagnostic_section",
                "title": key,
                "text": body[:5000],
                "source_refs": _source_refs(snapshot.get("source_paths") or {}),
                "fact_eligible": True,
            }
        )

    return {
        "available": True,
        "agent": "EnnoDiagnostic",
        "diagnostic_run_id": run.id,
        "status": run.status,
        "domain_detection": domain_detection,
        "verrous": verrou_payload,
        "diagnostic_sections": diagnostic_sections,
        "frascati_summary": snapshot.get("frascati_summary") or {},
        "frascati_justification": snapshot.get("frascati_justification") or {},
        "points_to_validate": diagnostic_sections.get("points_validation", ""),
        "evidence_items": evidence_items,
        "policy": "advisory_only_no_eligibility_claim",
    }


def scholar_context(
    db: Any,
    project: Any,
    target_text: str,
    instruction: str,
    *,
    allowed_article_ids: list[int] | None = None,
    evidence_scope_id: str | None = None,
    target_section_id: str | None = None,
    target_section_title: str | None = None,
    include_all_accepted: bool = False,
    authorized_cards: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Lit seulement la sélection consultant et ses preuves scientifiques prêtes."""

    from services.article_card_builder import get_article_cards_payload
    from services.scholar_selection_scope import get_current_selected_articles

    if allowed_article_ids is not None:
        from db.models import Article, ScholarRun

        allowed = {int(value) for value in allowed_article_ids}
        query = (
            db.query(Article)
            .join(ScholarRun, Article.scholar_run_id == ScholarRun.id)
            .filter(ScholarRun.project_id == project.id)
            .filter(Article.id.in_(allowed))
        )
        if authorized_cards is None:
            query = query.filter(Article.consultant_status == "garde")
        selected = query.all()
    else:
        selected = get_current_selected_articles(db, project)
    if not selected:
        return {
            "available": False,
            "agent": "EnnoScholar",
            "reason": "Aucune référence scientifique validée n'est disponible pour cette conversation.",
            "requires_research": True,
            "evidence_items": [],
        }

    if authorized_cards is not None:
        payload = {"cards": authorized_cards}
    else:
        payload = get_article_cards_payload(project, scope_id=evidence_scope_id)
        if evidence_scope_id and not (payload.get("cards") or []):
            payload = get_article_cards_payload(project)
    cards = [row for row in (payload.get("cards") or []) if isinstance(row, dict)]
    selected_ids = {int(row.id) for row in selected}
    terms = _search_terms(
        " ".join((target_section_title or "", target_text or "", instruction or ""))
    )
    explicit_closed_corpus = allowed_article_ids is not None
    wanted_target_id = str(target_section_id or "").strip()
    article_by_id = {int(row.id): row for row in selected}
    candidates: list[tuple[int, dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
    for card in cards:
        article_id = card.get("article_id") or (card.get("identity") or {}).get("article_id")
        try:
            numeric_article_id = int(article_id)
            if numeric_article_id not in selected_ids:
                continue
        except (TypeError, ValueError):
            continue
        article = article_by_id.get(numeric_article_id)
        source_json = article.source_json if article and isinstance(article.source_json, dict) else {}
        bindings = _target_bindings(card, source_json)
        mapped_ids = _binding_ids(bindings)
        mapped_to_target = bool(wanted_target_id and wanted_target_id in mapped_ids)
        if wanted_target_id and mapped_ids and not mapped_to_target:
            continue
        searchable = _normalized_searchable({"card": card, "source": source_json})
        lexical_score = sum(1 for term in terms if term in searchable)
        if not explicit_closed_corpus and not mapped_to_target and lexical_score < 2:
            continue
        candidates.append((lexical_score, card, source_json, bindings))
    candidates.sort(key=lambda item: item[0], reverse=True)

    evidence: list[dict[str, Any]] = []
    for lexical_score, card, source_json, bindings in (candidates if include_all_accepted else candidates[:12]):
        identity = card.get("identity") or {}
        article_id = card.get("article_id") or identity.get("article_id")
        article = article_by_id.get(int(article_id)) if str(article_id or "").isdigit() else None
        authors = (
            identity.get("authors")
            or card.get("authors")
            or source_json.get("authors")
            or source_json.get("author")
            or []
        )
        if isinstance(authors, str):
            authors = [item.strip() for item in re.split(r"[,;]", authors) if item.strip()]
        evidence.append(
            {
                "article_id": article_id,
                "citation_id": card.get("citation_id")
                or identity.get("citation_id")
                or (article.tag_article if article else None),
                "title": identity.get("title")
                or card.get("title")
                or (article.title if article else "Publication validée"),
                "year": identity.get("year")
                or card.get("year")
                or (article.year if article else None),
                "authors": list(authors)[:12] if isinstance(authors, list) else [],
                "doi": identity.get("doi")
                or card.get("doi")
                or (article.doi if article else None),
                "method": _compact(
                    card.get("technical_method_analysis") or card.get("methods"), 1400
                ),
                "results": _compact(
                    card.get("results")
                    or card.get("key_results")
                    or card.get("article_evidence_bank"),
                    1800,
                ),
                "limits": _compact(
                    card.get("concept_limits") or card.get("limitations"), 1400
                ),
                "impact": _compact(
                    card.get("impact_on_verrou")
                    or card.get("technical_narrative_capsule"),
                    1400,
                ),
                "source_url": article.url if article else None,
                "section_ids": list(dict.fromkeys([
                    *_string_list(card.get("section_ids") or source_json.get("section_ids") or []),
                    *[
                        str(row.get("parent_section_id") or "").strip()
                        for row in bindings
                        if str(row.get("parent_section_id") or "").strip()
                    ],
                ])),
                "research_target_ids": list(dict.fromkeys([
                    *_string_list(card.get("research_target_ids") or source_json.get("research_target_ids") or []),
                    *[
                        str(row.get("research_target_id") or "").strip()
                        for row in bindings
                        if str(row.get("research_target_id") or "").strip()
                    ],
                ])),
                "target_bindings": bindings,
                "allowed_claim_scope": {
                    "target_bindings": bindings,
                    "rule": "exact_target_only_no_cross_section_reuse",
                },
                "section_context_gate": card.get("section_context_gate") or source_json.get("section_context_gate") or {},
                "reuse_lexical_score": lexical_score,
                "citation_required": False,
            }
        )

    evidence_items: list[dict[str, Any]] = []
    for row in evidence:
        citation_id = str(row.get("citation_id") or "").strip()
        evidence_id = citation_id or f"S:article:{row.get('article_id')}"
        evidence_items.append(
            {
                "evidence_id": evidence_id,
                "citation_id": citation_id or None,
                "type": "scholar_article_card",
                "title": row.get("title"),
                "authors": row.get("authors") or [],
                "year": row.get("year"),
                "doi": row.get("doi"),
                "url": row.get("source_url"),
                "section_ids": row.get("section_ids") or [],
                "research_target_ids": row.get("research_target_ids") or [],
                "target_bindings": row.get("target_bindings") or [],
                "allowed_claim_scope": row.get("allowed_claim_scope") or {},
                "section_context_gate": row.get("section_context_gate") or {},
                "citation_required": False,
                "text": "\n".join(
                    str(row.get(key) or "")
                    for key in ("title", "method", "results", "limits", "impact")
                ),
                "fact_eligible": True,
            }
        )

    return {
        "available": bool(evidence),
        "agent": "EnnoScholar",
        "selected_article_count": len(selected),
        "writing_ready_card_count": len(evidence),
        "evidence": evidence,
        "evidence_items": evidence_items,
        "proof_policy": "target_scoped_validated_cards_no_zero_relevance_reuse_v3_25",
        "reuse_filter": {
            "explicit_closed_corpus": explicit_closed_corpus,
            "target_section_id": wanted_target_id or None,
            "meaningful_terms": sorted(terms),
            "eligible_count": len(evidence),
            "minimum_lexical_hits_for_unmapped_reuse": 2,
        },
        "reason": (
            None
            if evidence
            else "Les références validées existantes ne sont pas reliées à cette cible scientifique."
        ),
        "requires_research": not bool(evidence),
    }
