from __future__ import annotations

DIAGNOSTIC_DISPLAY_VERSION = "v143_complete_sections"

from typing import Any, Dict, List, Optional
import re


# ============================================================
# Helpers
# ============================================================

def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any, limit: Optional[int] = None) -> str:
    text = _safe_str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def _strip_md_title(value: str) -> str:
    return re.sub(r"^\s*#+\s*", "", _safe_str(value)).strip()


def _section_key(title: str) -> str:
    title = _safe_str(title).lower()
    table = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    title = title.translate(table)
    title = re.sub(r"[^a-z0-9]+", "_", title)
    return re.sub(r"_+", "_", title).strip("_")


def _extract_markdown_sections(markdown: str) -> Dict[str, str]:
    markdown = _clean_text(markdown)
    if not markdown:
        return {}

    pattern = re.compile(r"(?m)^##\s+(.+?)\s*$")
    matches = list(pattern.finditer(markdown))
    if not matches:
        return {}

    sections: Dict[str, str] = {}
    for i, match in enumerate(matches):
        title = _strip_md_title(match.group(1))
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        sections[_section_key(title)] = markdown[start:end].strip()
    return sections


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _pick_section(
    sections_by_key: Dict[str, Any],
    sections_by_title: Dict[str, Any],
    markdown_sections: Dict[str, str],
    keys: List[str],
    titles: List[str],
    legacy_keys: List[str],
) -> str:
    for key in keys:
        value = sections_by_key.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)

    for title in titles:
        value = sections_by_title.get(title)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)

    wanted = {_section_key(x) for x in [*keys, *titles, *legacy_keys]}
    for key, value in markdown_sections.items():
        if key in wanted and value:
            return _clean_text(value)

    for wanted_key in wanted:
        for key, value in markdown_sections.items():
            if value and (wanted_key in key or key in wanted_key):
                return _clean_text(value)

    return ""


def _split_bullets(section: str) -> List[str]:
    section = _clean_text(section)
    if not section:
        return []

    lines: List[str] = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line or line.startswith("|"):
            continue
        line = re.sub(r"^[-*•]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = line.strip()
        if line and not re.match(r"^-{3,}$", line):
            lines.append(line)

    if len(lines) <= 1:
        return [section]
    return lines


def _source_from_report(report: Dict[str, Any], bundle: Dict[str, Any]) -> str:
    if report:
        return "ennodiagnostic_agent"
    if bundle.get("nlp_result"):
        return "nlp_result"
    return "non_disponible"


def _normalize_ai_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    ai = _as_dict(report.get("ai_detection_report_runtime") or report.get("ai_detection_report") or {})
    summary = ai.get("summary")
    if isinstance(summary, dict):
        return summary

    nested = _as_dict(ai.get("ai_detection"))
    if nested:
        return {
            "average_ai_score": nested.get("global_ai_score"),
            "average_ai_percentage": nested.get("global_ai_percentage"),
            "risk_level": nested.get("risk_level"),
            "passages_count": nested.get("total_passages_analyzed"),
            "suspected_passages_count": nested.get("suspected_passages_count"),
            "high_count": nested.get("high_risk_passages_count"),
            "medium_count": nested.get("medium_risk_passages_count"),
            "low_count": nested.get("low_risk_passages_count"),
        }
    return {}


def _item_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        meta = _as_dict(item.get("metadata"))
        text = item.get("text") or item.get("source_text") or item.get("content") or item.get("description")
        doc = meta.get("document") or item.get("document")
        if text and doc:
            return f"{_clean_text(text)}\n\nSource : {doc}"
        if text:
            return _clean_text(text)
    return ""


def _items_to_text_list(items: Any, max_items: int = 12) -> List[str]:
    out: List[str] = []
    for item in _as_list(items)[:max_items]:
        text = _item_text(item)
        if text:
            out.append(text)
    return out


def _fallback_from_chroma(chroma_sections: Dict[str, Any], key: str) -> List[str]:
    return _items_to_text_list(chroma_sections.get(key), max_items=12)


def _extract_final_verrous(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Source unique officielle pour le frontend.

    Le frontend ne doit plus recalculer/parcourir tout le JSON :
    il consomme display.validation_verrous / display.llm_reformulated_verrous.
    """
    report = _as_dict(report)
    verrou_report = _as_dict(report.get("verrou_synthesis_report"))

    candidates = (
        verrou_report.get("llm_reformulated_verrous")
        or verrou_report.get("final_items")
        or verrou_report.get("accepted_items")
        or verrou_report.get("final_verrous")
        or report.get("llm_reformulated_verrous")
        or report.get("consultant_verrous_cir")
        or report.get("verrous_reformules")
        or report.get("verrous")
        or []
    )

    if not isinstance(candidates, list):
        return []

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for index, raw in enumerate(candidates):
        if not isinstance(raw, dict):
            continue

        source_json = _as_dict(raw.get("source_json"))
        title = _first_text(
            raw.get("title"),
            raw.get("titre"),
            raw.get("verrou"),
            raw.get("name"),
            raw.get("lock_title"),
            source_json.get("title"),
        )
        if not title:
            continue

        key = re.sub(r"\s+", " ", title.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)

        justification = _first_text(
            raw.get("consultant_explanation"),
            raw.get("why_agent_found_verrou"),
            raw.get("why_not_simple_engineering"),
            raw.get("justification"),
            raw.get("description"),
            raw.get("scientific_lock"),
            raw.get("text"),
            source_json.get("consultant_explanation"),
            source_json.get("why_not_simple_engineering"),
            source_json.get("evidence_summary"),
        )

        source_document = _first_text(
            raw.get("source_document"),
            raw.get("document"),
            raw.get("filename"),
            source_json.get("source_document"),
            source_json.get("document"),
            source_json.get("filename"),
        )

        score = (
            raw.get("score")
            if raw.get("score") is not None
            else raw.get("frascati_score")
            if raw.get("frascati_score") is not None
            else raw.get("confidence")
            if raw.get("confidence") is not None
            else source_json.get("score")
        )

        item = {
            **raw,
            "id": raw.get("id") or raw.get("verrou_id"),
            "title": title,
            "titre": title,
            "verrou": title,
            "description": justification,
            "justification": raw.get("justification") or justification,
            "text": raw.get("text") or justification,
            "score": score,
            "consultant_status": raw.get("consultant_status") or raw.get("status") or "en_attente",
            "tag_cir": raw.get("tag_cir") or raw.get("decision") or "À valider",
            "source_json": {
                **source_json,
                "source_document": source_document or source_json.get("source_document"),
                "document": source_document or source_json.get("document"),
                "evidence_summary": raw.get("evidence_summary") or source_json.get("evidence_summary"),
                "scientific_lock": raw.get("scientific_lock") or source_json.get("scientific_lock"),
                "why_not_simple_engineering": raw.get("why_not_simple_engineering") or source_json.get("why_not_simple_engineering"),
                "consultant_explanation": raw.get("consultant_explanation") or source_json.get("consultant_explanation") or justification,
                "frontend_source": "backend_display_service_v143",
                "frontend_json_only": not bool(raw.get("id") or raw.get("verrou_id")),
            },
            "can_decide": bool(raw.get("id") or raw.get("verrou_id") or raw.get("can_decide")),
            "is_db_synced": bool(raw.get("id") or raw.get("verrou_id") or raw.get("is_db_synced")),
        }
        out.append(item)

    return out


# ============================================================
# Main display builder
# ============================================================

def build_diagnostic_display(project: Any, bundle: Dict[str, Any]) -> Dict[str, Any]:
    """
    V139 — Construction unique de la vue EnnoDiagnostic pour React.

    Principe :
    - le backend choisit et normalise la sortie officielle ;
    - le frontend ne doit plus reconstruire les sections/verrous par heuristiques ;
    - la route /diagnostic/latest renvoie directement display.report_sections
      et display.validation_verrous.
    """
    bundle = _as_dict(bundle)
    run_raw = _as_dict(bundle.get("run_raw_result_json"))
    snapshot = _as_dict(
        bundle.get("diagnostic_snapshot")
        or run_raw.get("diagnostic_snapshot")
        or _as_dict(bundle.get("raw_result_json")).get("diagnostic_snapshot")
    )
    report = _as_dict(
        bundle.get("report")
        or run_raw.get("report")
        or _as_dict(run_raw.get("script_or_pipeline_result")).get("report")
    )
    nlp_result = _as_dict(bundle.get("nlp_result"))

    diagnostic = _as_dict(report.get("diagnostic"))
    static_diagnostic = _as_dict(report.get("static_diagnostic"))

    # V142 : fusion sans perte de toutes les sections persistées et agent.
    sections_by_key: Dict[str, Any] = {}
    for source in [
        snapshot.get("sections_by_key"),
        run_raw.get("diagnostic_sections_by_key"),
        report.get("diagnostic_sections_by_key"),
        static_diagnostic.get("sections_by_key"),
    ]:
        if isinstance(source, dict):
            for key, value in source.items():
                if isinstance(value, str) and value.strip() and not sections_by_key.get(_section_key(key)):
                    sections_by_key[_section_key(key)] = value.strip()

    sections_by_title: Dict[str, Any] = {}
    for source in [
        snapshot.get("sections_by_title"),
        run_raw.get("diagnostic_sections"),
        report.get("diagnostic_sections"),
        static_diagnostic.get("sections"),
    ]:
        if isinstance(source, dict):
            for key, value in source.items():
                if isinstance(value, str) and value.strip() and not sections_by_title.get(str(key)):
                    sections_by_title[str(key)] = value.strip()

    diagnostic_cards = (
        snapshot.get("diagnostic_cards")
        or run_raw.get("diagnostic_cards")
        or report.get("diagnostic_cards")
        or static_diagnostic.get("cards")
        or []
    )
    if not isinstance(diagnostic_cards, list):
        diagnostic_cards = []

    report_markdown = _clean_text(
        snapshot.get("report_markdown")
        or run_raw.get("report_markdown")
        or diagnostic.get("content")
        or report.get("content")
        or report.get("report_markdown")
        or static_diagnostic.get("markdown")
        or ""
    )
    markdown_sections = _extract_markdown_sections(report_markdown)

    chroma_sections = _as_dict(report.get("chroma_sections"))
    frascati_summary = _as_dict(report.get("frascati_summary"))
    inputs_status = _as_dict(report.get("inputs_status"))

    pipeline = _as_dict(report.get("pipeline_before_agent"))
    index_report = _as_dict(pipeline.get("index_report"))
    nlp_stats = _as_dict(pipeline.get("nlp_stats")) or _as_dict(nlp_result.get("stats"))

    frascati_text = _pick_section(
        sections_by_key,
        sections_by_title,
        markdown_sections,
        ["lecture_frascati"],
        ["Lecture Frascati du dossier"],
        ["lecture_frascati_du_dossier"],
    )
    frascati_justification_text = _pick_section(
        sections_by_key,
        sections_by_title,
        markdown_sections,
        ["justification_frascati"],
        ["Justification Frascati du score", "Justification du score Frascati"],
        ["justification_frascati_du_score"],
    )
    summary = _pick_section(
        sections_by_key,
        sections_by_title,
        markdown_sections,
        ["synthese_strategique"],
        ["Synthèse stratégique", "Synthèse stratégique du projet"],
        ["synthese_strategique_du_projet", "synthese"],
    )
    objective = _pick_section(
        sections_by_key,
        sections_by_title,
        markdown_sections,
        ["objectif_global"],
        ["Objectif global", "Objectif global reformulé"],
        ["objectif_global_reformule", "objectif"],
    )
    verrous_text = _pick_section(
        sections_by_key,
        sections_by_title,
        markdown_sections,
        ["verrous_rnd", "verrous_cir"],
        ["Verrous CIR consolidés", "Verrous R&D / signaux de verrous", "Verrous R&D"],
        ["verrous_r_d_signaux_de_verrous", "verrous_cir_consolides", "verrous"],
    )
    methodes_section = _pick_section(
        sections_by_key,
        sections_by_title,
        markdown_sections,
        ["demarche_detectee"],
        ["Démarche détectée", "Démarche expérimentale détectée"],
        ["demarche_experimentale_detectee", "demarche"],
    )
    resultats_section = _pick_section(
        sections_by_key,
        sections_by_title,
        markdown_sections,
        ["resultats_metriques"],
        ["Résultats / métriques", "Résultats et métriques disponibles"],
        ["resultats_et_metriques_disponibles", "resultats"],
    )
    params_section = _pick_section(
        sections_by_key,
        sections_by_title,
        markdown_sections,
        ["parametres_contraintes"],
        ["Paramètres et contraintes techniques", "Paramètres techniques"],
        ["parametres_et_contraintes_techniques", "parametres"],
    )
    validation_section = _pick_section(
        sections_by_key,
        sections_by_title,
        markdown_sections,
        ["points_validation"],
        ["Points à valider", "Points à valider par le consultant"],
        ["points_a_valider_par_le_consultant", "validation"],
    )

    if not summary:
        summary = "Lance EnnoDiagnostic pour générer la synthèse stratégique reformulée par LLM."

    if not objective:
        objective_items = _fallback_from_chroma(chroma_sections, "objectifs")
        objective = "## Objectifs détectés\n" + "\n".join(f"- {x}" for x in objective_items) if objective_items else "Objectif global non disponible."

    final_verrous = _extract_final_verrous(report)
    if not final_verrous:
        snapshot_verrous = snapshot.get("final_verrous")
        if isinstance(snapshot_verrous, list):
            final_verrous = [item for item in snapshot_verrous if isinstance(item, dict)]

    if not verrous_text and final_verrous:
        blocks = []
        for i, verrou in enumerate(final_verrous, 1):
            title = _clean_text(verrou.get("title"))
            justification = _clean_text(verrou.get("justification") or verrou.get("description"), 600)
            blocks.append(f"{i}. {title}" + (f"\n{justification}" if justification else ""))
        verrous_text = "\n\n".join(blocks)

    if not verrous_text:
        verrou_items = _fallback_from_chroma(chroma_sections, "verrous")
        if verrou_items:
            verrous_text = "## Verrous / incertitudes détectés\n" + "\n".join(f"- {x}" for x in verrou_items)

    methodes = _split_bullets(methodes_section) if methodes_section else _fallback_from_chroma(chroma_sections, "methodes")
    resultats = _split_bullets(resultats_section) if resultats_section else _fallback_from_chroma(chroma_sections, "resultats")
    parametres = _split_bullets(params_section) if params_section else _fallback_from_chroma(chroma_sections, "parametres")
    limites = _split_bullets(validation_section) if validation_section else _fallback_from_chroma(chroma_sections, "limites")

    if not report_markdown:
        blocks = []
        if frascati_text:
            blocks.append("## Lecture Frascati du dossier\n" + frascati_text)
        if frascati_justification_text:
            blocks.append("## Justification Frascati du score\n" + frascati_justification_text)
        if summary:
            blocks.append("## Synthèse stratégique du projet\n" + summary)
        if objective:
            blocks.append("## Objectif global reformulé\n" + objective)
        if verrous_text:
            blocks.append("## Verrous CIR consolidés\n" + verrous_text)
        if methodes_section:
            blocks.append("## Démarche expérimentale détectée\n" + methodes_section)
        if resultats_section:
            blocks.append("## Résultats et métriques disponibles\n" + resultats_section)
        if params_section:
            blocks.append("## Paramètres et contraintes techniques\n" + params_section)
        if validation_section:
            blocks.append("## Points à valider par le consultant\n" + validation_section)
        report_markdown = "\n\n".join(blocks).strip()

    report_sections = {
        "lecture_frascati": frascati_text,
        "justification_frascati": frascati_justification_text,
        "synthese": summary,
        "objectif": objective,
        "verrous": verrous_text,
        "signaux_de_verrous": verrous_text,
        "demarche": methodes_section,
        "resultats": resultats_section,
        "parametres": params_section,
        "points_validation": validation_section,
    }
    # Expose également chaque section agent, sans la perdre derrière les alias.
    for section_key, section_text in sections_by_key.items():
        if isinstance(section_text, str) and section_text.strip():
            report_sections.setdefault(section_key, section_text.strip())

    ai_summary = _normalize_ai_summary(report)
    ai_report = _as_dict(report.get("ai_detection_report_runtime") or report.get("ai_detection_report") or {})
    top_passages = []
    ai_detection_nested = _as_dict(ai_report.get("ai_detection"))
    for candidate in [
        ai_report.get("top_passages"),
        ai_detection_nested.get("suspected_passages"),
        ai_detection_nested.get("passages"),
    ]:
        if isinstance(candidate, list):
            top_passages = candidate[:10]
            break

    return {
        "source": _source_from_report(report, bundle),
        "display_version": "v142_complete_sections_display",
        "domain": getattr(project, "domain_label", None) or report.get("domain") or report.get("domain_label"),
        "status": diagnostic.get("status") or report.get("status"),
        "summary": summary,
        "objective": objective,
        "frascati_text": frascati_text,
        "frascati_justification_text": frascati_justification_text,
        "verrous_text": verrous_text,
        "methodes": methodes,
        "resultats": resultats,
        "parametres": parametres,
        "limites": limites,
        "report_markdown": report_markdown,
        "report_sections": report_sections,
        "diagnostic_sections_by_key": sections_by_key,
        "diagnostic_sections": sections_by_title,
        "all_sections_by_key": sections_by_key,
        "all_sections_by_title": sections_by_title,
        "sections_count": len(sections_by_key),
        "diagnostic_cards": diagnostic_cards,
        "database_snapshot": snapshot,
        "static_diagnostic": static_diagnostic,
        "validation_verrous": final_verrous,
        "validation_verrous_preview": final_verrous,
        "llm_reformulated_verrous": final_verrous,
        "consultant_verrous_cir": final_verrous,
        "consultant_validation_source": "backend_display_service_v143",
        "consultant_validation_enabled": any(bool(v.get("can_decide") or v.get("is_db_synced")) for v in final_verrous),
        "verrou_synthesis_report": report.get("verrou_synthesis_report") or {},
        "frascati_summary": frascati_summary,
        "ai_summary": ai_summary,
        "ai_detection": ai_report,
        "ai_score": ai_summary.get("average_ai_percentage"),
        "ai_risk_level": ai_summary.get("risk_level"),
        "ai_suspected_passages": top_passages,
        "inputs_status": inputs_status,
        "chroma_sections": chroma_sections,
        "pipeline_stats": {
            "documents_used_count": pipeline.get("documents_used_count"),
            "documents_loaded_count": pipeline.get("documents_loaded_count"),
            "raw_candidates": nlp_stats.get("raw_candidates"),
            "raw_kept": nlp_stats.get("raw_kept"),
            "merged_verrous": nlp_stats.get("merged_verrous"),
            "chunks_prepared": index_report.get("chunks_prepared"),
            "chunks_indexed": index_report.get("chunks_indexed"),
        },
        "source_policy": {
            "single_source_of_truth": True,
            "frontend_should_not_reparse_raw_json": True,
            "official_sections": "display.report_sections + display.diagnostic_sections_by_key",
            "official_verrous": "display.validation_verrous",
        },
    }
