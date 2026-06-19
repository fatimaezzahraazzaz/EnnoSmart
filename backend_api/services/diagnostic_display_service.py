from __future__ import annotations

from typing import Any, Dict, List, Optional
import re


# ============================================================
# Helpers
# ============================================================

def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm_spaces(value: Any) -> str:
    return re.sub(r"[ \t]+", " ", _safe_str(value)).strip()


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
    value = _safe_str(value)
    value = re.sub(r"^\s*#+\s*", "", value)
    return value.strip()


def _section_key(title: str) -> str:
    title = title.lower().strip()
    title = title.replace("é", "e").replace("è", "e").replace("ê", "e").replace("ë", "e")
    title = title.replace("à", "a").replace("â", "a")
    title = title.replace("î", "i").replace("ï", "i")
    title = title.replace("ô", "o").replace("ù", "u").replace("û", "u")
    title = title.replace("ç", "c")
    title = re.sub(r"[^a-z0-9]+", "_", title)
    return re.sub(r"_+", "_", title).strip("_")


def _extract_markdown_sections(markdown: str) -> Dict[str, str]:
    """
    Parse un rapport du type Streamlit / Markdown :

    ## Lecture Frascati du dossier
    ...
    ## Synthèse stratégique du projet
    ...

    Retourne les sections par clé normalisée.
    """
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
        body = markdown[start:end].strip()
        sections[_section_key(title)] = body

    return sections


def _split_bullets(section: str) -> List[str]:
    """
    Transforme une section markdown en lignes affichables.
    Si la section est complexe, on garde aussi une version lisible.
    """
    section = _clean_text(section)
    if not section:
        return []

    lines = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("|"):
            continue
        line = re.sub(r"^[-*•]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = line.strip()
        if line and not re.match(r"^-{3,}$", line):
            lines.append(line)

    # Si très peu de lignes, garder le bloc entier.
    if len(lines) <= 1:
        return [section]

    return lines


def _source_from_report(report: Dict[str, Any], bundle: Dict[str, Any]) -> str:
    if report:
        mode = report.get("mode")
        if mode:
            return "ennodiagnostic_agent"
    if bundle.get("nlp_result"):
        return "nlp_result"
    return "non_disponible"


def _normalize_ai_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    ai = report.get("ai_detection_report_runtime") or report.get("ai_detection_report") or {}
    ai = _as_dict(ai)

    summary = ai.get("summary")
    if isinstance(summary, dict):
        return summary

    nested = ai.get("ai_detection")
    if isinstance(nested, dict):
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
    out = []
    for item in _as_list(items)[:max_items]:
        text = _item_text(item)
        if text:
            out.append(text)
    return out


def _fallback_from_chroma(chroma_sections: Dict[str, Any], key: str) -> List[str]:
    return _items_to_text_list(chroma_sections.get(key), max_items=12)


# ============================================================
# Main display builder
# ============================================================

def build_diagnostic_display(project: Any, bundle: Dict[str, Any]) -> Dict[str, Any]:
    """
    Construction de la vue propre EnnoDiagnostic pour React.

    Correction V18 :
    - le frontend doit afficher le même rapport que Streamlit ;
    - donc la source prioritaire devient report.diagnostic.content ;
    - les objectifs/verrous bruts NLP ne remplacent plus la reformulation LLM ;
    - l'onglet Diagnostic CIR peut afficher `report_markdown` complet.
    """
    bundle = _as_dict(bundle)
    report = _as_dict(bundle.get("report"))
    nlp_result = _as_dict(bundle.get("nlp_result"))

    diagnostic = _as_dict(report.get("diagnostic"))
    report_markdown = _clean_text(
        diagnostic.get("content")
        or report.get("content")
        or report.get("report_markdown")
        or ""
    )

    sections = _extract_markdown_sections(report_markdown)

    chroma_sections = _as_dict(report.get("chroma_sections"))
    frascati_summary = _as_dict(report.get("frascati_summary"))
    inputs_status = _as_dict(report.get("inputs_status"))

    pipeline = _as_dict(report.get("pipeline_before_agent"))
    index_report = _as_dict(pipeline.get("index_report"))
    nlp_stats = _as_dict(pipeline.get("nlp_stats")) or _as_dict(nlp_result.get("stats"))

    frascati_text = sections.get("lecture_frascati_du_dossier", "")
    summary = sections.get("synthese_strategique_du_projet", "")
    objective = sections.get("objectif_global_reformule", "")
    verrous_text = sections.get("verrous_r_d_signaux_de_verrous", "") or sections.get("verrous_r_d_signaux_de_verrous", "")
    methodes_section = sections.get("demarche_experimentale_detectee", "")
    resultats_section = sections.get("resultats_et_metriques_disponibles", "")
    params_section = sections.get("parametres_et_contraintes_techniques", "")
    validation_section = sections.get("points_a_valider_par_le_consultant", "")

    # Fallback uniquement si le rapport LLM n'existe pas.
    if not summary:
        summary = "Lance EnnoDiagnostic pour générer la synthèse stratégique reformulée par LLM."

    if not objective:
        objective_items = _fallback_from_chroma(chroma_sections, "objectifs")
        if objective_items:
            objective = "## Objectifs détectés\n" + "\n".join(f"- {x}" for x in objective_items)
        else:
            objective = "Objectif global non disponible."

    if not verrous_text:
        verrou_items = _fallback_from_chroma(chroma_sections, "verrous")
        if verrou_items:
            verrous_text = "## Verrous / incertitudes détectés\n" + "\n".join(f"- {x}" for x in verrou_items)

    methodes = _split_bullets(methodes_section) if methodes_section else _fallback_from_chroma(chroma_sections, "methodes")
    resultats = _split_bullets(resultats_section) if resultats_section else _fallback_from_chroma(chroma_sections, "resultats")
    parametres = _split_bullets(params_section) if params_section else _fallback_from_chroma(chroma_sections, "parametres")
    limites = _split_bullets(validation_section) if validation_section else _fallback_from_chroma(chroma_sections, "limites")

    # Rapport complet affichable dans l'onglet Diagnostic CIR.
    if not report_markdown:
        blocks = []
        if frascati_text:
            blocks.append("## Lecture Frascati du dossier\n" + frascati_text)
        if summary:
            blocks.append("## Synthèse stratégique du projet\n" + summary)
        if objective:
            blocks.append("## Objectif global reformulé\n" + objective)
        if verrous_text:
            blocks.append("## Verrous R&D / signaux de verrous\n" + verrous_text)
        if methodes_section:
            blocks.append("## Démarche expérimentale détectée\n" + methodes_section)
        if resultats_section:
            blocks.append("## Résultats et métriques disponibles\n" + resultats_section)
        if params_section:
            blocks.append("## Paramètres et contraintes techniques\n" + params_section)
        if validation_section:
            blocks.append("## Points à valider par le consultant\n" + validation_section)
        report_markdown = "\n\n".join(blocks).strip()

    return {
        "source": _source_from_report(report, bundle),
        "domain": getattr(project, "domain_label", None) or report.get("domain") or report.get("domain_label"),
        "status": diagnostic.get("status") or report.get("status"),
        "summary": summary,
        "objective": objective,
        "frascati_text": frascati_text,
        "verrous_text": verrous_text,
        "methodes": methodes,
        "resultats": resultats,
        "parametres": parametres,
        "limites": limites,
        "report_markdown": report_markdown,
        "report_sections": {
            "lecture_frascati": frascati_text,
            "synthese": summary,
            "objectif": objective,
            "verrous": verrous_text,
            "demarche": methodes_section,
            "resultats": resultats_section,
            "parametres": params_section,
            "points_validation": validation_section,
        },
        "frascati_summary": frascati_summary,
        "ai_summary": _normalize_ai_summary(report),
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
    }
