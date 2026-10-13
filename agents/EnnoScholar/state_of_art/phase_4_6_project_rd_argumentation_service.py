# -*- coding: utf-8 -*-
from __future__ import annotations

"""
phase_4_6_project_rd_argumentation_service.py

EnnoScholar — Phase 4.6 : Project-first R&D argumentation
Version V1.5 COMPLETE — compatible Phase 4.5 V2.3

Rôle :
- construire un argumentaire CIR centré projet/verrou ;
- ne pas rédiger l'état de l'art final ;
- conserver toutes les références sélectionnées par le consultant ;
- exposer à Phase 5 les détails techniques extraits en Phase 4.5 :
  technical_detail_profile, open_domain_technical_evidence,
  result_method_test_links, result_claims, metrics_and_values ;
- éviter que Phase 5 recopie les phrases répétitives de 4.6.

Garanties :
- pas de hardcoding métier/projet ;
- pas de pondération/exclusion dans Phase 4.6 ;
- use_llm accepté mais non nécessaire : fallback déterministe robuste ;
- compatible appels positionnels et keyword-only.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..contracts import ContractError, assert_same_verrous, build_confirmed_contract
from modules.common.runtime_paths import storage_root

ROOT_DIR = Path(os.getenv("ENNOSMART_ROOT_DIR") or os.getenv("ENNOSMART_ROOT") or Path(__file__).resolve().parents[3])
OUTPUT_PAYLOAD_TYPE = "project_rd_argumentation_payload_v1_5_project_first_phase45_v23_no_repetition"

PROJECT_SECTION_KEYS = [
    "section_1_besoin_projet",
    "section_2_pourquoi_besoin_pose_verrou",
    "section_3_ce_que_etat_art_sait_deja_faire",
    "section_4_pourquoi_etat_art_ne_suffit_pas",
    "section_5_gap_rd",
    "section_6_travaux_experimentaux_necessaires",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return " ".join(clean_text(x) for x in value if clean_text(x))
    if isinstance(value, dict):
        for key in [
            "text", "value", "label", "title", "name", "summary", "resume",
            "description", "content", "reasoning", "principle", "mechanism",
            "technical_principle", "method_name", "method_or_concept",
            "technical_family", "objective", "objectif", "verrou", "gap",
            "constraint", "limit", "work", "why_needed",
        ]:
            txt = clean_text(value.get(key))
            if txt:
                return txt
        return ""
    s = str(value)
    s = s.replace("\u00a0", " ").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def clean_sentence(value: Any) -> str:
    s = clean_text(value)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    return s


def truncate(value: Any, limit: int = 1200) -> str:
    txt = clean_sentence(value)
    if len(txt) <= limit:
        return txt
    cut = txt[: max(0, limit - 1)].rstrip()
    last = max(cut.rfind("."), cut.rfind(";"), cut.rfind(","))
    if last > limit * 0.55:
        return cut[: last + 1].strip()
    return cut + "…"


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    return [value]


def unique_clean_list(values: List[Any], *, limit: Optional[int] = None) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values or []:
        txt = clean_sentence(value)
        if not txt:
            continue
        key = re.sub(r"\W+", "", txt.lower())[:220]
        if key in seen:
            continue
        seen.add(key)
        out.append(txt)
        if limit and len(out) >= limit:
            break
    return out


def fs_slug(value: Any) -> str:
    s = clean_text(value).lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    s = s.translate(tr)
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def normalize_citation_label(value: Any) -> str:
    txt = clean_text(value).strip()
    if not txt:
        return ""
    m = re.search(r"\[?\b(A\d+)\b\]?", txt, flags=re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b(\d+)\b", txt)
    if m:
        return f"A{m.group(1)}"
    return txt.upper().strip("[] ")


def citation_bracket(label: Any) -> str:
    c = normalize_citation_label(label)
    return f"[{c}]" if c else ""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def year_dir(organisme: str, project: str, year: str) -> Path:
    persistent_storage = storage_root()
    org_raw = clean_text(organisme)
    project_raw = clean_text(project)
    candidates = [
        persistent_storage / "organismes" / org_raw / "projects" / project_raw / "years" / str(year),
        persistent_storage / "organismes" / fs_slug(org_raw) / "projects" / fs_slug(project_raw) / "years" / str(year),
        persistent_storage / "organismes" / org_raw / "projects" / project_raw.replace("-", "_") / "years" / str(year),
        persistent_storage / "organismes" / fs_slug(org_raw) / "projects" / project_raw.replace("-", "_").lower() / "years" / str(year),
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[1]


def payload_root(organisme: str, project: str, year: str) -> Path:
    return year_dir(organisme, project, year) / "ennoscholar" / "state_of_art_payload"


def default_selection_payload_path(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "selection_payload.json"


def default_article_cards_payload_path(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "article_cards" / "article_cards_payload.json"


def default_scientific_gap_payload_path(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "phase_4_scientific_gap" / "gap_scientific_payload.json"


def default_scientific_reasoning_payload_path(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "phase_4_5_scientific_reasoning" / "scientific_reasoning_payload.json"


def output_dir(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "phase_4_6_project_rd_argumentation"

# ---------------------------------------------------------------------------
# Extraction generic containers
# ---------------------------------------------------------------------------

def find_list_container(payload: Any, preferred_keys: List[str]) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in preferred_keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            vals = list(value.values())
            if vals and all(isinstance(x, dict) for x in vals):
                return vals
    for value in payload.values():
        if isinstance(value, dict):
            found = find_list_container(value, preferred_keys)
            if found:
                return found
    return []


def normalize_article_card(card: Dict[str, Any], index: int) -> Dict[str, Any]:
    citation = normalize_citation_label(
        card.get("citation_label") or card.get("citation") or card.get("citation_id")
        or card.get("citation_token") or card.get("label") or card.get("ref")
        or card.get("article_ref") or f"A{index}"
    )
    authors = card.get("authors") or card.get("author") or []
    authors_list = [clean_text(a) for a in as_list(authors) if clean_text(a)] if not isinstance(authors, str) else [a.strip() for a in re.split(r",|;", authors) if a.strip()]
    fiche = card.get("fiche_article") if isinstance(card.get("fiche_article"), dict) else {}
    return {
        "citation_label": citation,
        "title": clean_sentence(card.get("title") or card.get("paper_title") or card.get("name") or fiche.get("label")),
        "authors": authors_list,
        "year": clean_text(card.get("year") or card.get("publication_year")),
        "abstract": clean_sentence(card.get("abstract_for_writer") or card.get("abstract_fr") or card.get("abstract") or card.get("summary") or card.get("resume")),
        "technical_family": clean_sentence(card.get("technical_family") or card.get("family") or card.get("tag")),
        "method_name": clean_sentence(card.get("method_name") or card.get("method") or card.get("method_or_concept") or fiche.get("methode")),
        "technical_principle": clean_sentence(card.get("technical_principle") or card.get("principle") or card.get("approach") or fiche.get("apport_scientifique") or card.get("abstract_for_writer") or card.get("abstract")),
        "mechanism": clean_sentence(card.get("mechanism") or card.get("how_it_works") or card.get("methodology") or fiche.get("methode")),
        "results": clean_sentence(card.get("results") or card.get("findings") or card.get("conclusion") or fiche.get("resultat")),
        "relevance": clean_sentence(card.get("relevance") or card.get("impact_on_verrou") or card.get("why_relevant") or card.get("reason")),
        "quality_status": clean_sentence(card.get("quality_status") or card.get("confidence") or card.get("quality")),
        "raw": card,
    }


def extract_article_cards(article_cards_payload: Dict[str, Any], selection_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_cards = find_list_container(article_cards_payload, ["article_cards", "cards", "articles", "papers", "selected_articles", "items", "results"])
    if not raw_cards:
        for verrou in as_list(selection_payload.get("verrous")):
            if not isinstance(verrou, dict):
                continue
            for key in ["articles_directs", "articles_connexes", "articles_fondamentaux", "selected_articles"]:
                raw_cards += as_list(verrou.get(key))
    out: List[Dict[str, Any]] = []
    for i, raw in enumerate(raw_cards, 1):
        if isinstance(raw, dict):
            card = normalize_article_card(raw, i)
            if card.get("citation_label"):
                out.append(card)
    seen = set()
    dedup = []
    for card in out:
        c = normalize_citation_label(card.get("citation_label"))
        if not c or c in seen:
            continue
        seen.add(c)
        dedup.append(card)
    return dedup


def cards_by_citation(article_cards: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {normalize_citation_label(c.get("citation_label")): c for c in article_cards if normalize_citation_label(c.get("citation_label"))}

# ---------------------------------------------------------------------------
# Phase 4.5 reading
# ---------------------------------------------------------------------------

def get_reasoning_items(scientific_reasoning_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(scientific_reasoning_payload, dict):
        return []
    for key in ["verrous_reasoning", "reasoning_by_verrou", "verrous", "items", "results", "argumentations"]:
        items = scientific_reasoning_payload.get(key)
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    if scientific_reasoning_payload.get("verrou_id") or scientific_reasoning_payload.get("verrou_title"):
        return [scientific_reasoning_payload]
    return []


def writer_plan(reasoning_item: Dict[str, Any]) -> Dict[str, Any]:
    for key in ["writer_plan_for_phase_5", "writer_plan", "phase5_writing_blueprint"]:
        wp = reasoning_item.get(key)
        if isinstance(wp, dict):
            return wp
    return {}


def citation_tier(citation: str, wp: Dict[str, Any]) -> str:
    c = normalize_citation_label(citation)
    groups = [
        ("core", ["core_citations", "direct_citations", "direct_citations_from_families"]),
        ("important", ["important_citations"]),
        ("support", ["support_citations"]),
        ("context_low_confidence", ["low_confidence_citations", "low_confidence"]),
    ]
    for tier, keys in groups:
        for key in keys:
            vals = [normalize_citation_label(x) for x in as_list(wp.get(key))]
            if c in vals:
                return tier
    return "unspecified"


def extract_technical_detail_profile(item: Dict[str, Any], card: Dict[str, Any]) -> Dict[str, Any]:
    """Lit les nouveaux blocs Phase 4.5 V2.1/V2.2/V2.3 sans supposer leur schéma exact."""
    candidates = []
    for src in [item, item.get("technical_detail_profile"), item.get("technical_method_analysis"), card.get("raw") if isinstance(card.get("raw"), dict) else {}, card]:
        if isinstance(src, dict):
            if isinstance(src.get("technical_detail_profile"), dict):
                candidates.append(src.get("technical_detail_profile"))
            candidates.append(src)
    merged: Dict[str, Any] = {}
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        for key in [
            "procedure_type_detected", "scientific_method_profile", "method_and_experimental_protocol",
            "measurable_parameters_by_unit", "training_hyperparameters", "data_and_protocol",
            "evaluation_metrics", "implementation_parameters", "open_domain_technical_evidence",
            "missing_details", "no_value_invented",
        ]:
            val = cand.get(key)
            if val not in (None, "", [], {}) and key not in merged:
                merged[key] = val
        # V2.2/V2.3 result links can be embedded in profile or method itself.
        for key in ["result_method_test_links", "result_claims"]:
            val = cand.get(key)
            if val not in (None, "", [], {}) and key not in merged:
                merged[key] = val
    if merged:
        merged.setdefault("profile_type", "phase_4_6_v1_5_forwarded_from_phase_4_5")
        merged.setdefault("no_value_invented", True)
    return merged


def extract_result_method_test_links(item: Dict[str, Any], technical_profile: Dict[str, Any]) -> Dict[str, Any]:
    srcs = [item, technical_profile, item.get("result_method_test_links"), technical_profile.get("result_method_test_links")]
    for src in srcs:
        if isinstance(src, dict) and (src.get("result_claims") or src.get("has_linked_results") is not None):
            claims = src.get("result_claims") or []
            return {
                "has_linked_results": bool(src.get("has_linked_results") or claims),
                "result_claims": as_list(claims)[:12],
                "source": clean_text(src.get("source") or "phase_4_5_result_method_test_links"),
                "no_value_invented": True,
            }
    claims = technical_profile.get("result_claims") or item.get("result_claims") or []
    return {
        "has_linked_results": bool(claims),
        "result_claims": as_list(claims)[:12],
        "source": "phase_4_5_result_claims_fallback",
        "no_value_invented": True,
    }


def summarize_technical_profile_for_phase5(profile: Dict[str, Any], limit_items: int = 10) -> List[str]:
    out: List[str] = []
    if not isinstance(profile, dict) or not profile:
        return out

    def add(label: str, val: Any, max_chars: int = 380):
        txt = clean_sentence(val)
        if txt:
            out.append(f"{label}: {truncate(txt, max_chars)}")

    # Domain-agnostic summaries.
    for key, label in [
        ("procedure_type_detected", "type de méthode/procédé détecté"),
        ("scientific_method_profile", "profil de méthode scientifique"),
        ("method_and_experimental_protocol", "protocole ou étapes"),
        ("measurable_parameters_by_unit", "paramètres mesurables/unités"),
        ("training_hyperparameters", "hyperparamètres si modèle entraîné"),
        ("data_and_protocol", "données/protocole"),
        ("evaluation_metrics", "métriques/critères"),
        ("implementation_parameters", "paramètres d'implémentation"),
        ("open_domain_technical_evidence", "preuves techniques non classées"),
    ]:
        val = profile.get(key)
        if isinstance(val, dict):
            # keep compact but no loss: use up to 4 populated sub-fields.
            parts = []
            for sk, sv in val.items():
                st = clean_sentence(sv)
                if st:
                    parts.append(f"{sk}={truncate(st, 180)}")
                if len(parts) >= 4:
                    break
            add(label, "; ".join(parts), 520)
        elif isinstance(val, list):
            add(label, "; ".join(clean_sentence(x) for x in val[:4] if clean_sentence(x)), 520)
        else:
            add(label, val, 420)
        if len(out) >= limit_items:
            break
    return unique_clean_list(out, limit=limit_items)


def summarize_result_links_for_phase5(links: Dict[str, Any], limit_claims: int = 6) -> List[Dict[str, Any]]:
    claims = []
    if not isinstance(links, dict):
        return claims
    for idx, claim in enumerate(as_list(links.get("result_claims")), 1):
        if not isinstance(claim, dict):
            txt = clean_sentence(claim)
            if txt:
                claims.append({"result_id": f"R{idx}", "result_text": truncate(txt, 450), "no_value_invented": True})
            continue
        metrics = claim.get("metrics_and_values") if isinstance(claim.get("metrics_and_values"), dict) else {}
        claims.append({
            "result_id": clean_text(claim.get("result_id") or f"R{idx}"),
            "result_text": truncate(claim.get("result_text") or claim.get("text") or claim.get("claim"), 520),
            "result_type": clean_text(claim.get("result_type")),
            "metrics_and_values": metrics,
            "raw_numeric_or_value_mentions": as_list(metrics.get("raw_numeric_or_value_mentions"))[:8] if isinstance(metrics, dict) else [],
            "linked_method_or_technology_context": as_list(claim.get("linked_method_or_technology_context"))[:4],
            "linked_test_or_validation_context": as_list(claim.get("linked_test_or_validation_context"))[:4],
            "link_confidence": clean_text(claim.get("link_confidence") or "unknown"),
            "no_value_invented": True,
        })
        if len(claims) >= limit_claims:
            break
    return claims


def method_subject(item: Dict[str, Any], card: Dict[str, Any]) -> str:
    return clean_sentence(
        item.get("subject_label") or item.get("method_name") or item.get("method_or_concept")
        or item.get("technical_family") or card.get("method_name") or card.get("technical_family")
        or card.get("title") or "cette approche"
    )


def normalize_method(item: Dict[str, Any], index: int, wp: Dict[str, Any], card_map: Dict[str, Dict[str, Any]], source: str) -> Dict[str, Any]:
    citation = normalize_citation_label(item.get("citation_label") or item.get("citation") or item.get("article_ref") or item.get("ref") or item.get("source_citation"))
    card = card_map.get(citation, {})
    subject = method_subject(item, card)
    technical_profile = extract_technical_detail_profile(item, card)
    result_links = extract_result_method_test_links(item, technical_profile)
    technical_summary = summarize_technical_profile_for_phase5(technical_profile)
    result_summary = summarize_result_links_for_phase5(result_links)

    concept_limits = []
    for key in ["concept_limits", "limits", "limitations"]:
        concept_limits += [clean_text(x) for x in as_list(item.get(key)) if clean_text(x)]
    trans_limits = []
    for key in ["transposability_limits", "project_limits", "limits_for_project"]:
        trans_limits += [clean_text(x) for x in as_list(item.get(key)) if clean_text(x)]

    tier = clean_sentence(item.get("priority_tier") or item.get("tier") or citation_tier(citation, wp))
    usage = clean_sentence(item.get("usage_type") or item.get("usage") or item.get("evidence_type"))
    if not usage:
        usage = "related_evidence" if tier in {"important", "support", "context_low_confidence"} else "direct_evidence"

    return {
        "method_uid": clean_sentence(item.get("method_uid") or item.get("id") or f"M{index:02d}_{citation}_{fs_slug(subject)[:40]}"),
        "citation_label": citation,
        "citation": citation_bracket(citation),
        "subject_label": subject,
        "method_name": clean_sentence(item.get("method_name") or item.get("method_or_concept") or card.get("method_name") or subject),
        "technical_family": clean_sentence(item.get("technical_family") or item.get("family_label") or card.get("technical_family")),
        "priority_tier": tier or "unspecified",
        "usage_type": usage,
        "source": source,
        "article_title": clean_sentence(card.get("title") or item.get("article_title")),
        "article_year": clean_text(card.get("year") or item.get("article_year")),
        "article_authors": card.get("authors") or item.get("article_authors") or [],
        "technical_principle": clean_sentence(item.get("technical_principle") or item.get("principle") or card.get("technical_principle") or card.get("abstract")),
        "mechanism": clean_sentence(item.get("mechanism") or card.get("mechanism")),
        "scientific_results": clean_sentence(item.get("results") or item.get("reported_results") or item.get("findings") or card.get("results")),
        "concept_limits": unique_clean_list(concept_limits, limit=6),
        "transposability_limits": unique_clean_list(trans_limits, limit=6),
        "impact_on_verrou": clean_sentence(item.get("impact_on_verrou") or item.get("relevance") or card.get("relevance")),
        "remaining_uncertainty": clean_sentence(item.get("remaining_uncertainty") or item.get("uncertainty") or item.get("open_gap")),
        "quality_status": clean_sentence(item.get("quality_status") or card.get("quality_status")),
        # New Phase 4.5 V2.3 material for Phase 5.
        "technical_detail_profile": technical_profile,
        "technical_detail_summary_for_phase5": technical_summary,
        "result_method_test_links": result_links,
        "result_method_test_summary_for_phase5": result_summary,
        "result_claims_for_phase5": result_summary,
        "phase5_usage_rules": {
            "do_not_copy_phase46_sections_verbatim": True,
            "use_technical_details_to_enrich_state_of_art": True,
            "link_result_to_method_test_metric_when_available": True,
            "if_metric_detected_empty_use_raw_numeric_mentions_and_result_text": True,
            "no_value_invented": True,
        },
    }


def extract_all_methods(reasoning_item: Dict[str, Any], article_cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    wp = writer_plan(reasoning_item)
    card_map = cards_by_citation(article_cards)
    containers: List[tuple[str, Any]] = []
    for key in [
        "technical_methods_reasoning", "technical_methods_to_explain", "methods", "method_cards",
        "article_methods", "state_of_art_positioning", "scientific_methods", "selected_methods",
        "technical_detail_matrix",
    ]:
        if isinstance(reasoning_item.get(key), list):
            containers.append((key, reasoning_item.get(key)))
    if isinstance(wp.get("technical_methods_to_explain"), list):
        containers.append(("writer_plan.technical_methods_to_explain", wp.get("technical_methods_to_explain")))
    if isinstance(wp.get("technical_detail_matrix"), list):
        containers.append(("writer_plan.technical_detail_matrix", wp.get("technical_detail_matrix")))

    methods: List[Dict[str, Any]] = []
    for source, items in containers:
        for item in as_list(items):
            if isinstance(item, dict):
                m = normalize_method(item, len(methods) + 1, wp, card_map, source)
                if m.get("citation_label"):
                    methods.append(m)

    allowed: List[str] = []
    for key in [
        "allowed_citations", "all_citations_from_families", "prioritized_citations",
        "core_citations", "important_citations", "support_citations", "low_confidence_citations",
        "direct_citations_from_families", "related_citations_from_families",
    ]:
        allowed += [normalize_citation_label(x) for x in as_list(wp.get(key))]
    if not allowed:
        for card in article_cards:
            allowed.append(normalize_citation_label(card.get("citation_label")))

    existing = {normalize_citation_label(m.get("citation_label")) for m in methods}
    for citation in unique_clean_list(allowed):
        c = normalize_citation_label(citation)
        if not c or c in existing:
            continue
        card = card_map.get(c, {})
        item = {
            "citation_label": c,
            "subject_label": card.get("method_name") or card.get("technical_family") or card.get("title"),
            "technical_principle": card.get("technical_principle") or card.get("abstract"),
            "mechanism": card.get("mechanism"),
            "results": card.get("results"),
            "priority_tier": citation_tier(c, wp),
            "usage_type": "related_evidence" if citation_tier(c, wp) in {"important", "support", "context_low_confidence"} else "direct_evidence",
        }
        methods.append(normalize_method(item, len(methods) + 1, wp, card_map, "article_cards_fallback"))
        existing.add(c)

    by_citation: Dict[str, Dict[str, Any]] = {}
    for m in methods:
        c = normalize_citation_label(m.get("citation_label"))
        if not c:
            continue
        score = sum(1 for key in ["technical_principle", "mechanism", "scientific_results", "impact_on_verrou", "remaining_uncertainty"] if m.get(key))
        score += len(m.get("technical_detail_summary_for_phase5") or []) * 2
        score += len(m.get("result_claims_for_phase5") or []) * 2
        score += len(m.get("concept_limits") or []) + len(m.get("transposability_limits") or [])
        prev = by_citation.get(c)
        if not prev:
            by_citation[c] = m
        else:
            prev_score = sum(1 for key in ["technical_principle", "mechanism", "scientific_results", "impact_on_verrou", "remaining_uncertainty"] if prev.get(key))
            prev_score += len(prev.get("technical_detail_summary_for_phase5") or []) * 2
            prev_score += len(prev.get("result_claims_for_phase5") or []) * 2
            prev_score += len(prev.get("concept_limits") or []) + len(prev.get("transposability_limits") or [])
            if score > prev_score:
                by_citation[c] = m
    return list(by_citation.values())

# ---------------------------------------------------------------------------
# Project context Phase 1
# ---------------------------------------------------------------------------

def selection_verrous(selection_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    vals = selection_payload.get("verrous")
    return [v for v in vals if isinstance(v, dict)] if isinstance(vals, list) else []


def match_selection_verrou(selection_payload: Dict[str, Any], reasoning_item: Dict[str, Any]) -> Dict[str, Any]:
    rid = clean_text(reasoning_item.get("verrou_id") or reasoning_item.get("id"))
    title = clean_sentence(reasoning_item.get("verrou_title") or reasoning_item.get("title") or reasoning_item.get("verrou"))
    title_key = fs_slug(title)
    for v in selection_verrous(selection_payload):
        vid = clean_text(v.get("verrou_id") or v.get("id"))
        if rid and vid and rid == vid:
            return v
    for v in selection_verrous(selection_payload):
        vt = clean_sentence(v.get("verrou_title") or v.get("title") or v.get("verrou"))
        if title_key and fs_slug(vt) == title_key:
            return v
    return selection_verrous(selection_payload)[0] if selection_verrous(selection_payload) else {}


def extract_constraints_from_context(ctx: Dict[str, Any], vctx: Dict[str, Any]) -> List[Dict[str, str]]:
    constraints: List[Dict[str, str]] = []
    for src_name, src in [("project_context_structured", ctx), ("verrou_context_structured", vctx)]:
        if not isinstance(src, dict):
            continue
        for key in ["contraintes_projet", "contraintes", "constraints", "contraintes_associees"]:
            for item in as_list(src.get(key)):
                if isinstance(item, dict):
                    c = clean_sentence(item.get("constraint") or item.get("text") or item.get("limite") or item.get("description"))
                    why = clean_sentence(item.get("why_it_matters_for_verrou") or item.get("impact") or item.get("reason"))
                    source = clean_sentence(item.get("source") or src_name)
                else:
                    c = clean_sentence(item)
                    why = "Cette contrainte conditionne la démonstration que le verrou reste ouvert dans le contexte du projet."
                    source = src_name
                if c:
                    constraints.append({"constraint": c, "source": source, "why_it_matters_for_verrou": why})
    seen = set()
    out = []
    for c in constraints:
        key = re.sub(r"\W+", "", c["constraint"].lower())[:180]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= 12:
            break
    return out


def extract_project_context(selection_payload: Dict[str, Any], reasoning_item: Dict[str, Any]) -> Dict[str, Any]:
    selected_verrou = match_selection_verrou(selection_payload, reasoning_item)
    top_ctx = selection_payload.get("project_context_structured") if isinstance(selection_payload.get("project_context_structured"), dict) else {}
    verrou_ctx = selected_verrou.get("project_context_structured") if isinstance(selected_verrou.get("project_context_structured"), dict) else {}
    verrou_specific = selected_verrou.get("verrou_context_structured") if isinstance(selected_verrou.get("verrou_context_structured"), dict) else {}
    ctx: Dict[str, Any] = {}
    for src in [top_ctx, verrou_ctx]:
        if isinstance(src, dict):
            ctx.update({k: v for k, v in src.items() if v not in [None, "", [], {}]})
    verrou_title = clean_sentence(reasoning_item.get("verrou_title") or selected_verrou.get("verrou_title") or selected_verrou.get("objectif_rd") or selected_verrou.get("objectif_r&d"))
    project_need = clean_sentence(verrou_specific.get("besoin_associe") or ctx.get("besoin_projet") or ctx.get("project_need") or selected_verrou.get("contexte_projet") or reasoning_item.get("project_need"))
    objective = clean_sentence(verrou_specific.get("objectif_rd") or selected_verrou.get("objectif_rd") or selected_verrou.get("objectif_r&d") or ctx.get("objectif_technique") or ctx.get("project_objective") or verrou_title)
    problem = clean_sentence(verrou_specific.get("probleme_a_resoudre") or verrou_specific.get("limite_identifiee") or selected_verrou.get("justification") or selected_verrou.get("contexte_projet") or reasoning_item.get("verrou_rd_formulation"))
    why_not_simple = clean_sentence(verrou_specific.get("pourquoi_ce_n_est_pas_un_simple_developpement") or "Le point à démontrer ne se limite pas à appliquer une méthode existante : il faut vérifier son comportement, ses limites de transposition et sa généralisation dans les conditions propres du projet.")
    what_to_verify = clean_sentence(verrou_specific.get("ce_que_l_etat_de_l_art_devra_verifier") or "L’état de l’art doit établir ce que les travaux existants savent déjà faire, puis montrer ce qu’ils ne démontrent pas dans le contexte précis du dossier.")
    uncertainty = clean_sentence(ctx.get("incertitude_rd") or verrou_specific.get("limite_identifiee") or problem)
    criteria = unique_clean_list([x for x in as_list(ctx.get("criteres_validation"))] + [x for x in as_list(verrou_specific.get("criteres_de_succes"))], limit=12)
    constraints = extract_constraints_from_context(ctx, verrou_specific)
    return {
        "available": bool(project_need or problem or constraints or criteria),
        "organisme": clean_sentence(selection_payload.get("organisme") or selection_payload.get("client")),
        "project": clean_sentence(selection_payload.get("project") or selection_payload.get("project_name")),
        "year": clean_sentence(selection_payload.get("year")),
        "domain_label": clean_sentence(selection_payload.get("domain_label")),
        "verrou_id": clean_sentence(reasoning_item.get("verrou_id") or selected_verrou.get("verrou_id") or selected_verrou.get("id")),
        "verrou_title": verrou_title,
        "project_need": project_need,
        "project_objective": objective,
        "technical_context": clean_sentence(ctx.get("contexte_technique") or selected_verrou.get("contexte_projet")),
        "verrou_problem": problem,
        "why_not_simple_engineering": why_not_simple,
        "what_state_of_art_must_verify": what_to_verify,
        "constraints": constraints,
        "validation_criteria": criteria,
        "rd_uncertainty": uncertainty,
        "trace": {"from_selection_payload": True, "from_verrou_context_structured": bool(verrou_specific)},
    }


def extract_phase4_gap_text(gap_payload: Dict[str, Any], reasoning_item: Dict[str, Any]) -> str:
    candidates: List[str] = []
    for src in [reasoning_item, gap_payload]:
        if not isinstance(src, dict):
            continue
        for key in ["source_gap_summary_for_phase_5", "scientific_gap_summary", "rd_gap", "gap_rd", "gap_summary", "rd_justification_summary", "argumentation", "summary", "synthesis"]:
            val = src.get(key)
            if isinstance(val, dict):
                for sub in ["summary", "gap", "text", "reasoning", "rd_gap", "scientific_gap_summary", "rd_justification_summary"]:
                    txt = clean_sentence(val.get(sub))
                    if txt:
                        candidates.append(txt)
            else:
                txt = clean_sentence(val)
                if txt:
                    candidates.append(txt)
    return "\n".join(unique_clean_list(candidates, limit=8)).strip()

# ---------------------------------------------------------------------------
# Argumentation deterministic
# ---------------------------------------------------------------------------

def method_short_contribution(m: Dict[str, Any]) -> str:
    parts = [m.get("technical_principle"), m.get("mechanism"), m.get("scientific_results"), m.get("impact_on_verrou")]
    txt = " ".join(clean_sentence(x) for x in parts if clean_sentence(x))
    return truncate(txt or m.get("article_title") or m.get("subject_label"), 850)


def method_limits(m: Dict[str, Any]) -> List[str]:
    return unique_clean_list(as_list(m.get("concept_limits")) + as_list(m.get("transposability_limits")) + [m.get("remaining_uncertainty")], limit=8)


def all_citations_text(methods: List[Dict[str, Any]], limit: Optional[int] = None) -> str:
    selected = methods if limit is None else methods[:limit]
    return ", ".join(citation_bracket(m.get("citation_label")) for m in selected if m.get("citation_label"))


def synthesize_state_of_art_sentence(methods: List[Dict[str, Any]]) -> str:
    if not methods:
        return "L'état de l'art fournit des approches méthodologiques exploitables, mais leur transposition doit être vérifiée dans le contexte du projet."
    subjects = unique_clean_list([m.get("subject_label") or m.get("method_name") or m.get("technical_family") for m in methods], limit=6)
    subject_text = ", ".join(subjects) if subjects else "des approches méthodologiques relatives au verrou"
    return (
        f"Les références sélectionnées par le consultant montrent que l'état de l'art dispose déjà de travaux mobilisables autour de {subject_text}. "
        "Ces travaux apportent des principes, protocoles, métriques ou résultats utiles, mais ils ne constituent pas encore une démonstration directe de validité dans les conditions propres du projet."
    )


def concise_constraints(project_context: Dict[str, Any], limit: int = 5) -> List[str]:
    out = []
    for c in project_context.get("constraints") or []:
        txt = clean_sentence(c.get("constraint") if isinstance(c, dict) else c)
        if not txt:
            continue
        low = txt.lower()
        if txt.lower() == "contrainte projet" or "réponse provisoire" in low:
            continue
        if len(txt) > 350 and not any(k in low for k in ["démontrer", "valider", "définir", "comparer", "vérifier", "robustesse", "généralisation", "représent"]):
            continue
        out.append(txt)
    return unique_clean_list(out, limit=limit)


def build_default_project_sections(project_context: Dict[str, Any], methods: List[Dict[str, Any]], phase4_gap_text: str) -> Dict[str, str]:
    project_need = clean_sentence(project_context.get("project_need"))
    project_objective = clean_sentence(project_context.get("project_objective"))
    verrou_problem = clean_sentence(project_context.get("verrou_problem"))
    why_not_simple = clean_sentence(project_context.get("why_not_simple_engineering"))
    what_verify = clean_sentence(project_context.get("what_state_of_art_must_verify"))
    uncertainty = clean_sentence(project_context.get("rd_uncertainty"))
    criteria = unique_clean_list(project_context.get("validation_criteria") or [], limit=6)
    constraints = concise_constraints(project_context, limit=5)

    section_1 = project_need or "Le projet présente un besoin technique à sécuriser avant de pouvoir considérer le verrou comme levé."
    if project_objective and project_objective.lower() not in section_1.lower():
        section_1 += f" L'objectif technique associé est : {project_objective}."

    section_2 = verrou_problem or "Ce besoin pose un verrou car il ne suffit pas d'appliquer une méthode existante sans démontrer sa validité dans le contexte réel du dossier."
    if why_not_simple:
        section_2 += f" {why_not_simple}"
    if constraints:
        section_2 += " Les contraintes projet à prendre en compte sont notamment : " + " ".join(constraints[:4])

    section_3 = synthesize_state_of_art_sentence(methods)
    section_3 += " Les citations seront conservées dans evidence_by_citation et devront être utilisées par Phase 5 comme preuves intégrées, pas comme plan article-par-article."

    if constraints or criteria:
        section_4 = "Ces acquis ne suffisent pas à lever le verrou, car ils doivent encore être confrontés aux contraintes et critères propres du dossier."
        if constraints:
            section_4 += " Contraintes : " + " ".join(constraints[:4])
        if criteria:
            section_4 += " Critères de validation : " + ", ".join(criteria[:6]) + "."
    else:
        section_4 = "Ces acquis ne suffisent pas à lever le verrou, car ils ne démontrent pas, à eux seuls, la représentativité, la robustesse et la généralisation dans les conditions propres du dossier."
    if what_verify:
        section_4 += f" {what_verify}"

    if phase4_gap_text and len(phase4_gap_text) > 80:
        section_5 = truncate(phase4_gap_text, 1200)
        section_5 += " Ce gap doit être compris comme un écart entre les acquis scientifiques disponibles et la démonstration encore attendue dans le dossier projet."
    else:
        section_5 = "Le gap R&D réside dans la démonstration expérimentale que les approches disponibles peuvent répondre au besoin du projet dans ses propres conditions d'utilisation."
        if uncertainty:
            section_5 += f" {uncertainty}"

    section_6 = "Les travaux expérimentaux nécessaires doivent viser à qualifier la solution dans le contexte réel du projet : définition des paramètres de validation, comparaison avec des références ou mesures, analyse des limites de généralisation et vérification de la robustesse des résultats."
    if criteria:
        section_6 += " Ils doivent notamment couvrir : " + ", ".join(criteria[:6]) + "."

    return {
        "section_1_besoin_projet": section_1,
        "section_2_pourquoi_besoin_pose_verrou": section_2,
        "section_3_ce_que_etat_art_sait_deja_faire": section_3,
        "section_4_pourquoi_etat_art_ne_suffit_pas": section_4,
        "section_5_gap_rd": section_5,
        "section_6_travaux_experimentaux_necessaires": section_6,
    }


def evidence_from_method(m: Dict[str, Any]) -> Dict[str, Any]:
    limits = method_limits(m)
    what_shows = method_short_contribution(m)
    not_prove = "Cette référence ne démontre pas à elle seule que le verrou est levé dans les conditions spécifiques du projet."
    if limits:
        not_prove += " Limites ou précautions à considérer : " + "; ".join(truncate(x, 220) for x in limits[:4]) + "."
    return {
        "citation": citation_bracket(m.get("citation_label")),
        "priority_tier": m.get("priority_tier") or "unspecified",
        "usage_type": m.get("usage_type") or "evidence",
        "quality_status": m.get("quality_status") or "unspecified",
        "subject_label": m.get("subject_label"),
        "article_title": m.get("article_title"),
        "evidence_role_in_argument": (
            "Référence sélectionnée par le consultant : à conserver comme appui scientifique dans le raisonnement projet. "
            "Phase 4.6 ne la pondère pas et ne la retire pas ; Phase 5 devra l'intégrer finement dans la rédaction."
        ),
        "what_state_of_art_shows": what_shows,
        "what_it_does_not_prove_for_project": not_prove,
        "transposition_limits": limits,
        "technical_detail_summary_for_phase5": m.get("technical_detail_summary_for_phase5") or [],
        "result_method_test_summary_for_phase5": m.get("result_method_test_summary_for_phase5") or [],
        "result_claims_for_phase5": m.get("result_claims_for_phase5") or [],
        "technical_detail_profile_for_traceability": m.get("technical_detail_profile") or {},
        "use_in_sections": [
            "section_3_ce_que_etat_art_sait_deja_faire",
            "section_4_pourquoi_etat_art_ne_suffit_pas",
            "phase_5_redaction_detaillee",
        ],
        "phase5_instruction": (
            "Utiliser les détails techniques et result_claims pour enrichir l'état de l'art. "
            "Ne pas recopier les sections Phase 4.6 mot pour mot. Relier résultat -> méthode -> test -> métrique si disponible."
        ),
    }


def default_unresolved_limits(project_context: Dict[str, Any], methods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    constraints = [c.get("constraint") for c in project_context.get("constraints", []) if isinstance(c, dict) and c.get("constraint")]
    citations = [citation_bracket(m.get("citation_label")) for m in methods if m.get("citation_label")]
    out = []
    for c in unique_clean_list(constraints, limit=4):
        out.append({
            "limit": c,
            "supported_by": citations,
            "project_impact": "Cette limite maintient le besoin d'une démonstration propre au projet, même si les références scientifiques fournissent des appuis utiles.",
        })
    if not out:
        out.append({
            "limit": "La transposition au contexte projet reste à démontrer expérimentalement.",
            "supported_by": citations,
            "project_impact": "Le verrou ne peut pas être considéré comme levé sans validation dans les conditions du dossier.",
        })
    return out


def default_experimental_work(project_context: Dict[str, Any], methods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    criteria = unique_clean_list(project_context.get("validation_criteria") or [], limit=6)
    citations = [citation_bracket(m.get("citation_label")) for m in methods if m.get("citation_label")]
    why = "Pour démontrer que les acquis de l'état de l'art sont effectivement transposables au contexte du projet."
    if criteria:
        why += " Critères à couvrir : " + ", ".join(criteria[:6]) + "."
    return [{
        "work": "Validation expérimentale et comparaison dans le contexte propre du projet.",
        "why_needed": why,
        "linked_citations": citations,
    }]


def required_citations(methods: List[Dict[str, Any]]) -> List[str]:
    return unique_clean_list([normalize_citation_label(m.get("citation_label")) for m in methods])


def references_from_methods(methods: List[Dict[str, Any]]) -> List[str]:
    refs = []
    for m in methods:
        citation = citation_bracket(m.get("citation_label"))
        title = clean_sentence(m.get("article_title") or m.get("subject_label"))
        authors = ", ".join([clean_sentence(a) for a in as_list(m.get("article_authors")) if clean_sentence(a)][:4])
        year = clean_text(m.get("article_year"))
        parts = [citation]
        if title:
            parts.append(" — " + title)
        if authors:
            parts.append(", " + authors)
        if year:
            parts.append(", " + year)
        refs.append("".join(parts))
    return unique_clean_list(refs)


def complete_project_first_argumentation(project_context: Dict[str, Any], methods: List[Dict[str, Any]], phase4_gap_text: str, verrou_id: str, verrou_title: str) -> Dict[str, Any]:
    sections = build_default_project_sections(project_context, methods, phase4_gap_text)
    req = required_citations(methods)
    return {
        "ok": True,
        "verrou_id": verrou_id,
        "verrou_title": verrou_title,
        "project_rd_argument_sections": sections,
        "project_context_constraints_used": [
            {
                "constraint": clean_sentence(c.get("constraint")),
                "why_it_matters_for_verrou": clean_sentence(c.get("why_it_matters_for_verrou")) or "Cette contrainte conditionne la démonstration du verrou.",
                "source": clean_sentence(c.get("source")),
            }
            for c in as_list(project_context.get("constraints"))
            if isinstance(c, dict) and clean_sentence(c.get("constraint"))
        ],
        "evidence_by_citation": [evidence_from_method(m) for m in methods if normalize_citation_label(m.get("citation_label"))],
        "unresolved_project_limits": default_unresolved_limits(project_context, methods),
        "rd_gap": sections["section_5_gap_rd"],
        "experimental_work_needed": default_experimental_work(project_context, methods),
        "phase5_project_first_writing_blueprint": {
            "writing_logic": "Besoin projet -> verrou -> acquis état de l'art -> insuffisances projet -> gap R&D -> travaux expérimentaux.",
            "do_not_write_as_article_list": True,
            "do_not_copy_project_rd_argument_sections_verbatim": True,
            "phase5_must_use_evidence_by_citation_details": True,
            "phase5_must_link_result_to_method_test_metric": True,
            "phase5_must_avoid_repeating_phase46_phrases": True,
            "citation_strategy": "Citer les références sélectionnées dans les paragraphes comme appuis, sans les transformer en titres ou fiches.",
            "technical_detail_fields_to_use": [
                "technical_detail_summary_for_phase5",
                "result_method_test_summary_for_phase5",
                "result_claims_for_phase5",
            ],
        },
        "mandatory_citation_coverage": [citation_bracket(c) for c in req],
        "warnings": [],
    }

# ---------------------------------------------------------------------------
# Guard / Markdown
# ---------------------------------------------------------------------------

def citations_in_text(value: Any) -> List[str]:
    txt = clean_text(value)
    return unique_clean_list([m.group(1).upper() for m in re.finditer(r"\[(A\d+)\]", txt, flags=re.I)])


def is_article_list_style(value: Any) -> bool:
    txt = clean_text(value)
    if not txt:
        return False
    citation_starts = len(re.findall(r"(?:^|\n)\s*(?:[-*]\s*)?\[A\d+\]", txt, flags=re.I))
    article_words = len(re.findall(r"\b(l['’]article|cet article|cette source|A\d+ propose|\[A\d+\].{0,40}propose)\b", txt, flags=re.I))
    return citation_starts >= 3 or article_words >= 5


def article_list_style_score(argumentation: Dict[str, Any]) -> Dict[str, Any]:
    sections = argumentation.get("project_rd_argument_sections") if isinstance(argumentation.get("project_rd_argument_sections"), dict) else {}
    section_text = "\n".join(clean_text(sections.get(k)) for k in PROJECT_SECTION_KEYS)
    citation_starts = len(re.findall(r"(?:^|\n)\s*(?:[-*]\s*)?\[A\d+\]", section_text, flags=re.I))
    article_phrases = len(re.findall(r"\b(l['’]article|cet article|cette source|A\d+ propose|\[A\d+\].{0,50}propose)\b", section_text, flags=re.I))
    return {"citation_line_starts": citation_starts, "article_phrases": article_phrases, "detected": citation_starts >= 3 or article_phrases >= 5}


def validate_argumentation(argumentation: Dict[str, Any], methods: List[Dict[str, Any]], project_context: Dict[str, Any]) -> Dict[str, Any]:
    req = required_citations(methods)
    evidence = argumentation.get("evidence_by_citation") if isinstance(argumentation.get("evidence_by_citation"), list) else []
    present = unique_clean_list([normalize_citation_label(e.get("citation")) for e in evidence if isinstance(e, dict)])
    missing = [c for c in req if c not in present]
    sections = argumentation.get("project_rd_argument_sections") if isinstance(argumentation.get("project_rd_argument_sections"), dict) else {}
    missing_sections = [k for k in PROJECT_SECTION_KEYS if not clean_text(sections.get(k))]
    style = article_list_style_score(argumentation)
    score = 100
    if missing:
        score -= 30
    if missing_sections:
        score -= 30
    if style.get("detected"):
        score -= 25
    if not argumentation.get("project_context_constraints_used"):
        score -= 5
    score = max(0, min(100, score))
    return {
        "ok": not missing and not missing_sections and score >= 65 and not style.get("detected"),
        "required_citations": req,
        "present_citations": present,
        "missing_citations": missing,
        "missing_sections": missing_sections,
        "project_first_score": score,
        "article_list_style": style,
        "has_project_constraints": bool(argumentation.get("project_context_constraints_used")),
        "all_consultant_selected_references_kept": len(missing) == 0,
        "rules": {
            "plan_is_project_first_not_article_first": True,
            "articles_are_evidence_not_structure": True,
            "no_evidence_weighting_in_phase_4_6": True,
            "phase45_v23_technical_details_forwarded_to_phase5": True,
            "no_domain_hardcoding": True,
        },
    }


def build_markdown_summary(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Phase 4.6 — Argumentaire CIR project-first")
    lines.append("")
    lines.append(f"Payload: `{payload.get('payload_type')}`")
    lines.append(f"OK: `{payload.get('ok')}`")
    lines.append("")
    for arg in payload.get("argumentations", []):
        if not isinstance(arg, dict):
            continue
        aj = arg.get("argumentation_json") if isinstance(arg.get("argumentation_json"), dict) else {}
        guard = arg.get("guard") or {}
        sections = aj.get("project_rd_argument_sections") if isinstance(aj.get("project_rd_argument_sections"), dict) else {}
        lines.append(f"## {clean_text(arg.get('verrou_title'))}")
        lines.append("")
        lines.append(f"- Verrou ID: `{arg.get('verrou_id')}`")
        lines.append(f"- Citations requises: {', '.join(guard.get('required_citations', []))}")
        lines.append(f"- Citations présentes: {', '.join(guard.get('present_citations', []))}")
        lines.append(f"- Manquantes: {', '.join(guard.get('missing_citations', [])) or 'aucune'}")
        lines.append(f"- Score project-first: `{guard.get('project_first_score')}`")
        lines.append(f"- Article-list détecté: `{(guard.get('article_list_style') or {}).get('detected')}`")
        lines.append(f"- Toutes les références consultant conservées: `{guard.get('all_consultant_selected_references_kept')}`")
        lines.append("")
        labels = {
            "section_1_besoin_projet": "1. Besoin projet",
            "section_2_pourquoi_besoin_pose_verrou": "2. Pourquoi ce besoin pose un verrou",
            "section_3_ce_que_etat_art_sait_deja_faire": "3. Ce que l’état de l’art sait déjà faire",
            "section_4_pourquoi_etat_art_ne_suffit_pas": "4. Pourquoi cela ne suffit pas dans le contexte projet",
            "section_5_gap_rd": "5. Gap R&D",
            "section_6_travaux_experimentaux_necessaires": "6. Travaux expérimentaux nécessaires",
        }
        for key in PROJECT_SECTION_KEYS:
            lines.append(f"### {labels[key]}")
            lines.append(truncate(sections.get(key), 4000) or "Non renseigné.")
            lines.append("")
        evidence = aj.get("evidence_by_citation") or []
        if evidence:
            lines.append("### Annexe — références sélectionnées et détails transmis à Phase 5")
            for e in evidence:
                if not isinstance(e, dict):
                    continue
                detail_count = len(e.get("technical_detail_summary_for_phase5") or [])
                result_count = len(e.get("result_claims_for_phase5") or [])
                subject = clean_sentence(e.get("subject_label") or e.get("article_title"))
                lines.append(f"- **{e.get('citation')}** — {subject} — détails techniques: `{detail_count}` — résultats liés: `{result_count}`")
            lines.append("")
    return "\n".join(lines).strip() + "\n"

# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_phase_4_6_project_rd_argumentation(
    organisme: Optional[str] = None,
    project: Optional[str] = None,
    year: Optional[str] = None,
    *,
    provider: Optional[str] = None,
    selection_payload_path: Optional[str] = None,
    article_cards_payload_path: Optional[str] = None,
    fewshot_payload_path: Optional[str] = None,
    scientific_gap_payload_path: Optional[str] = None,
    scientific_reasoning_payload_path: Optional[str] = None,
    use_llm: bool = False,
    dry_run: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    organisme = organisme or kwargs.pop("organisme", None)
    project = project or kwargs.pop("project", None)
    year = year or kwargs.pop("year", None)
    requested_output_path = kwargs.pop("output_path", None)
    requested_markdown_output_path = kwargs.pop("markdown_output_path", None)
    if not organisme or not project or not year:
        raise TypeError("run_phase_4_6_project_rd_argumentation nécessite organisme, project et year.")

    provider = provider or os.getenv("ENNOSMART_PHASE46_LLM_PROVIDER", "none")
    selection_path = Path(selection_payload_path) if selection_payload_path else default_selection_payload_path(organisme, project, str(year))
    cards_path = Path(article_cards_payload_path) if article_cards_payload_path else default_article_cards_payload_path(organisme, project, str(year))
    gap_path = Path(scientific_gap_payload_path) if scientific_gap_payload_path else default_scientific_gap_payload_path(organisme, project, str(year))
    reasoning_path = Path(scientific_reasoning_payload_path) if scientific_reasoning_payload_path else default_scientific_reasoning_payload_path(organisme, project, str(year))

    selection_payload = read_json(selection_path, {}) or {}
    article_cards_payload = read_json(cards_path, {}) or {}
    gap_payload = read_json(gap_path, {}) or {}
    reasoning_payload = read_json(reasoning_path, {}) or {}

    try:
        verrou_contract = build_confirmed_contract(
            selection_payload,
            source_path=str(selection_path),
        )
    except ContractError as exc:
        return {
            **exc.as_dict(),
            "phase": "phase_4_6_project_rd_argumentation",
            "payload_type": OUTPUT_PAYLOAD_TYPE,
            "input_paths": {
                "selection_payload": str(selection_path),
                "article_cards_payload": str(cards_path),
                "scientific_gap_payload": str(gap_path),
                "scientific_reasoning_payload": str(reasoning_path),
            },
        }

    article_cards = extract_article_cards(article_cards_payload, selection_payload)
    reasoning_items = get_reasoning_items(reasoning_payload)
    if not reasoning_items:
        for v in verrou_contract["verrous"]:
            reasoning_items.append({
                "verrou_id": v.get("verrou_id"),
                "verrou_title": v.get("verrou_title"),
            })
    if not reasoning_items:
        return {
            "ok": False,
            "phase": "phase_4_6_project_rd_argumentation",
            "payload_type": OUTPUT_PAYLOAD_TYPE,
            "generated_at": now_iso(),
            "error": "Aucun verrou/reasoning item trouvé. Relancer Phase 1 puis Phase 4.5.",
            "input_paths": {
                "selection_payload": str(selection_path),
                "article_cards_payload": str(cards_path),
                "scientific_gap_payload": str(gap_path),
                "scientific_reasoning_payload": str(reasoning_path),
            },
        }

    try:
        assert_same_verrous(
            verrou_contract["verrous"],
            reasoning_items,
            observed_name="Phase 4.5",
        )
    except ContractError as exc:
        return {
            **exc.as_dict(),
            "phase": "phase_4_6_project_rd_argumentation",
            "payload_type": OUTPUT_PAYLOAD_TYPE,
        }

    argumentations: List[Dict[str, Any]] = []
    for reasoning_item in reasoning_items:
        verrou_id = clean_text(reasoning_item.get("verrou_id") or reasoning_item.get("id"))
        project_context = extract_project_context(selection_payload, reasoning_item)
        # A confirmed title is a contract identity, not prose to reformat.
        # Removing the space before ':' makes Phase 4.7 reject the same lock.
        verrou_title = clean_text(
            reasoning_item.get("verrou_title")
            or reasoning_item.get("title")
            or reasoning_item.get("verrou")
            or project_context.get("verrou_title")
        )
        project_context["verrou_title"] = verrou_title
        if not verrou_id or not verrou_title:
            return {
                "ok": False,
                "status": "invalid_confirmed_verrou",
                "phase": "phase_4_6_project_rd_argumentation",
                "message": "Chaque verrou doit conserver son identifiant et son titre confirmés.",
            }
        methods = extract_all_methods(reasoning_item, article_cards)
        phase4_gap_text = extract_phase4_gap_text(gap_payload, reasoning_item)
        argumentation_json = complete_project_first_argumentation(project_context, methods, phase4_gap_text, verrou_id, verrou_title)
        argumentation_json["references_utilisees"] = references_from_methods(methods)
        guard = validate_argumentation(argumentation_json, methods, project_context)
        argumentations.append({
            "ok": bool(guard.get("ok")),
            "verrou_id": verrou_id,
            "verrou_title": verrou_title,
            "llm_provider": provider,
            "llm_used": False,
            "llm_error": "LLM désactivé ou non nécessaire en V1.5 déterministe." if not use_llm else "LLM ignoré dans cette version complète pour éviter les répétitions.",
            "project_first_completion_used": True,
            "methods_count": len(methods),
            "methods": methods,
            "project_context_used": project_context,
            "argumentation_json": argumentation_json,
            "guard": guard,
        })

    ok = all(a.get("ok") for a in argumentations) if argumentations else False
    out_dir = output_dir(organisme, project, str(year))
    output_path = (
        Path(requested_output_path)
        if requested_output_path
        else out_dir / "project_rd_argumentation_payload.json"
    )
    markdown_output_path = (
        Path(requested_markdown_output_path)
        if requested_markdown_output_path
        else out_dir / "project_rd_argumentation_summary.md"
    )

    payload = {
        "ok": ok,
        "phase": "phase_4_6_project_rd_argumentation",
        "step": "run_phase_4_6_project_rd_argumentation",
        "payload_type": OUTPUT_PAYLOAD_TYPE,
        "generated_at": now_iso(),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "verrou_fingerprint": verrou_contract["verrou_fingerprint"],
        "dry_run": dry_run,
        "input_paths": {
            "selection_payload": str(selection_path),
            "article_cards_payload": str(cards_path),
            "scientific_gap_payload": str(gap_path),
            "scientific_reasoning_payload": str(reasoning_path),
        },
        "argumentations": argumentations,
        "rules": {
            "phase_4_6_role": "build_project_first_rd_argumentation_not_final_writing",
            "articles_are_evidence_not_structure": True,
            "article_cards_as_only_scientific_proof": True,
            "phase_4_5_as_mandatory_evidence_source": True,
            "phase45_v23_technical_details_forwarded_to_phase5": True,
            "do_not_copy_project_rd_argument_sections_verbatim_in_phase5": True,
            "llm_must_not_select_articles": True,
            "all_methods_must_be_covered_in_evidence_by_citation": True,
            "no_domain_hardcoding": True,
        },
        "output_path": str(output_path),
        "markdown_output_path": str(markdown_output_path),
    }
    if not dry_run:
        write_json(output_path, payload)
        write_text(markdown_output_path, build_markdown_summary(payload))
    return payload


run_phase_4_6 = run_phase_4_6_project_rd_argumentation
build_project_rd_argumentation_payload = run_phase_4_6_project_rd_argumentation


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="EnnoScholar Phase 4.6 — Project-first R&D argumentation")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()
    result = run_phase_4_6_project_rd_argumentation(
        organisme=args.organisme,
        project=args.project,
        year=args.year,
        provider=args.provider,
        use_llm=not args.no_llm,
    )
    print(json.dumps({
        "ok": result.get("ok"),
        "payload_type": result.get("payload_type"),
        "output_path": result.get("output_path"),
        "markdown_output_path": result.get("markdown_output_path"),
    }, ensure_ascii=False, indent=2))
