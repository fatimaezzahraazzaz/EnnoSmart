# -*- coding: utf-8 -*-
from __future__ import annotations

"""
phase_4_7_scientific_narrative_builder.py

EnnoScholar — Phase 4.7 : Scientific Narrative Builder
Version V2.1 — Macro scientific knowledge base + consultant narrative blueprint

Rôle
----
Cette phase ne rédige pas l'état de l'art final.
Elle construit une couche globale de raisonnement scientifique à partir de :
- Phase 4.5 : scientific_reasoning_payload.json
- Phase 4.6 : project_rd_argumentation_payload.json

Objectif
--------
Passer d'une logique "verrou par verrou" à une vision transversale :
- familles scientifiques fusionnées ;
- concepts unifiés ;
- comparaisons contrôlées ;
- limites communes ;
- consensus / contradictions ;
- progression scientifique ;
- raisonnement entre verrous ;
- blueprint pour Phase 5.

Garanties
---------
- Ne lit pas les PDF/articles bruts.
- Ne supprime aucune citation sélectionnée.
- Ne crée pas de preuve scientifique nouvelle.
- Ne rédige pas le texte final.
- Utilise les sorties 4.5 et 4.6 comme sources structurées.
- Aucun hardcoding domaine : logique par similarité textuelle, familles, concepts, citations.

Sorties
-------
.../ennoscholar/state_of_art_payload/phase_4_7_scientific_narrative/
    scientific_narrative_payload.json
    scientific_narrative_summary.md
"""

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..contracts import (
    ContractError,
    assert_same_verrous,
    build_confirmed_contract,
    extract_verrou_items,
    load_confirmed_contract,
    resolve_approved_plan,
)
from ..storage_paths import (
    confirmed_verrous_path as default_confirmed_verrous_contract_path,
    consultant_plan_path as default_consultant_plan_contract_path,
)

ROOT_DIR = Path(os.getenv("ENNOSMART_ROOT_DIR") or os.getenv("ENNOSMART_ROOT") or r"C:\EnnoSmart")
OUTPUT_PAYLOAD_TYPE = "scientific_narrative_payload_v3_4_verrou_sections_fixed"


# ============================================================
# Helpers généraux
# ============================================================

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
            "constraint", "limit", "work", "why_needed", "family_label",
            "concept_label", "subject_label",
        ]:
            txt = clean_text(value.get(key))
            if txt:
                return txt
        return ""
    s = str(value)
    s = s.replace("\u00a0", " ")
    s = s.replace("\r", "\n")
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
    return txt[: max(0, limit - 1)].rstrip() + "…"


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def unique_clean_list(values: List[Any], *, limit: Optional[int] = None) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        txt = clean_sentence(value)
        if not txt:
            continue
        key = re.sub(r"\W+", "", txt.lower())[:180]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(txt)
        if limit and len(out) >= limit:
            break
    return out


def fs_slug(value: Any) -> str:
    s = clean_text(value).lower()
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


def text_tokens(value: Any) -> List[str]:
    s = clean_text(value).lower()
    s = re.sub(r"[^a-z0-9à-öø-ÿ_\-/ ]+", " ", s)
    tokens = [t.strip("_-/") for t in s.split() if len(t.strip("_-/")) >= 3]
    stop = {
        "les", "des", "une", "dans", "pour", "avec", "sans", "sur", "aux", "par", "qui", "que",
        "est", "sont", "être", "etre", "plus", "moins", "cela", "cette", "projet", "verrou",
        "article", "articles", "source", "sources", "méthode", "méthodes", "method", "methods",
        "données", "donnees", "data", "approche", "approches", "résultat", "resultat", "results",
        "technique", "scientifique", "validation", "contexte", "phase", "preuve", "preuves",
        "limite", "limites", "concept", "concepts", "modèle", "model", "models",
    }
    return [t for t in tokens if t not in stop]


def jaccard_similarity(a: Any, b: Any) -> float:
    ta = set(text_tokens(a))
    tb = set(text_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, len(ta | tb))


def canonical_label(value: Any) -> str:
    txt = clean_sentence(value)
    if not txt:
        return ""
    txt = re.sub(r"\s+/\s+", " / ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def normalize_concept_key(value: Any) -> str:
    txt = clean_sentence(value).lower()
    txt = txt.replace("modèles", "modeles")
    txt = txt.replace("modèle", "modele")
    txt = txt.replace("méthodes", "methodes")
    txt = txt.replace("méthode", "methode")
    txt = txt.replace("réseaux", "reseaux")
    txt = txt.replace("neurones", "neuronnes")
    txt = re.sub(r"\b(method|methode|methods|methodes|approach|approche|technique|techniques)\b", "", txt)
    txt = re.sub(r"[^a-z0-9à-öø-ÿ]+", "_", txt)
    txt = re.sub(r"_+", "_", txt).strip("_")
    return txt


def safe_ratio(num: int, den: int) -> float:
    return round(num / den, 3) if den else 0.0


# ============================================================
# Paths
# ============================================================

def year_dir(organisme: str, project: str, year: str) -> Path:
    storage_root = Path(os.getenv("ENNOSMART_STORAGE_ROOT", str(ROOT_DIR / "storage")))
    org_raw = clean_text(organisme)
    project_raw = clean_text(project)
    candidates = [
        storage_root / "organismes" / org_raw / "projects" / project_raw / "years" / str(year),
        storage_root / "organismes" / fs_slug(org_raw) / "projects" / fs_slug(project_raw) / "years" / str(year),
        storage_root / "organismes" / org_raw / "projects" / project_raw.replace("-", "_") / "years" / str(year),
        storage_root / "organismes" / org_raw / "projects" / project_raw.replace("_", "-") / "years" / str(year),
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[1]


def payload_root(organisme: str, project: str, year: str) -> Path:
    return year_dir(organisme, project, year) / "ennoscholar" / "state_of_art_payload"


def default_phase_4_5_path(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "phase_4_5_scientific_reasoning" / "scientific_reasoning_payload.json"


def default_phase_4_6_path(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "phase_4_6_project_rd_argumentation" / "project_rd_argumentation_payload.json"


def output_dir(organisme: str, project: str, year: str) -> Path:
    return payload_root(organisme, project, year) / "phase_4_7_scientific_narrative"


# ============================================================
# Extraction Phase 4.5
# ============================================================

def get_verrous_reasoning(phase45: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(phase45, dict):
        return []
    for key in ["verrous_reasoning", "reasoning_by_verrou", "verrous", "items", "results", "argumentations"]:
        value = phase45.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    if phase45.get("verrou_id") or phase45.get("verrou_title"):
        return [phase45]
    return []


def extract_approach_families(verrou: Dict[str, Any]) -> List[Dict[str, Any]]:
    families: List[Dict[str, Any]] = []
    for key in ["approach_families", "families_to_synthesize", "families", "family_graph"]:
        vals = verrou.get(key)
        if isinstance(vals, list):
            families.extend([x for x in vals if isinstance(x, dict)])
    wp = verrou.get("writer_plan_for_phase_5") if isinstance(verrou.get("writer_plan_for_phase_5"), dict) else {}
    vals = wp.get("families_to_synthesize")
    if isinstance(vals, list):
        families.extend([x for x in vals if isinstance(x, dict)])
    return families


def extract_technical_methods(verrou: Dict[str, Any]) -> List[Dict[str, Any]]:
    methods: List[Dict[str, Any]] = []
    for key in [
        "technical_methods_reasoning", "technical_methods", "technical_methods_to_explain",
        "concept_limit_matrix", "concept_deepening", "methods", "method_cards",
    ]:
        vals = verrou.get(key)
        if isinstance(vals, list):
            methods.extend([x for x in vals if isinstance(x, dict)])
    enrich = verrou.get("consultant_reasoning_enrichment") if isinstance(verrou.get("consultant_reasoning_enrichment"), dict) else {}
    for key in ["concept_deepening", "technical_methods", "concept_limit_matrix"]:
        vals = enrich.get(key)
        if isinstance(vals, list):
            methods.extend([x for x in vals if isinstance(x, dict)])
    return methods


def extract_comparisons(verrou: Dict[str, Any]) -> List[Dict[str, Any]]:
    comps: List[Dict[str, Any]] = []
    for key in ["comparison_guard_matrix", "comparison_graph", "comparisons"]:
        vals = verrou.get(key)
        if isinstance(vals, list):
            comps.extend([x for x in vals if isinstance(x, dict)])
    enrich = verrou.get("consultant_reasoning_enrichment") if isinstance(verrou.get("consultant_reasoning_enrichment"), dict) else {}
    vals = enrich.get("comparison_guard_matrix")
    if isinstance(vals, list):
        comps.extend([x for x in vals if isinstance(x, dict)])
    return comps


def extract_limit_causality(verrou: Dict[str, Any]) -> List[Dict[str, Any]]:
    limits: List[Dict[str, Any]] = []
    for key in ["technical_limit_causality", "limit_causality", "shared_limitations", "limitations"]:
        vals = verrou.get(key)
        if isinstance(vals, list):
            limits.extend([x for x in vals if isinstance(x, dict)])
    enrich = verrou.get("consultant_reasoning_enrichment") if isinstance(verrou.get("consultant_reasoning_enrichment"), dict) else {}
    vals = enrich.get("technical_limit_causality")
    if isinstance(vals, list):
        limits.extend([x for x in vals if isinstance(x, dict)])
    return limits


def extract_demonstration_chain(verrou: Dict[str, Any]) -> Dict[str, Any]:
    for key in ["demonstration_chain", "scientific_demonstration", "cir_demonstration_chain"]:
        val = verrou.get(key)
        if isinstance(val, dict):
            return val
    enrich = verrou.get("consultant_reasoning_enrichment") if isinstance(verrou.get("consultant_reasoning_enrichment"), dict) else {}
    val = enrich.get("demonstration_chain")
    return val if isinstance(val, dict) else {}


# ============================================================
# Extraction Phase 4.6
# ============================================================

def get_project_argumentations(phase46: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(phase46, dict):
        return []
    vals = phase46.get("argumentations")
    if isinstance(vals, list):
        return [x for x in vals if isinstance(x, dict)]
    if phase46.get("argumentation_json") or phase46.get("verrou_id"):
        return [phase46]
    return []


def argumentation_json(arg: Dict[str, Any]) -> Dict[str, Any]:
    val = arg.get("argumentation_json")
    if isinstance(val, dict):
        return val
    return arg


def argumentation_sections(arg: Dict[str, Any]) -> Dict[str, str]:
    aj = argumentation_json(arg)
    sec = aj.get("project_rd_argument_sections")
    if isinstance(sec, dict):
        return {str(k): clean_sentence(v) for k, v in sec.items()}
    return {}


# ============================================================
# Normalisation des verrous
# ============================================================

def verrou_id_of(verrou: Dict[str, Any], fallback: int = 1) -> str:
    verrou_id = clean_sentence(verrou.get("verrou_id") or verrou.get("id"))
    if not verrou_id:
        raise ContractError(
            "missing_verrou_id",
            "La Phase 4.7 refuse de créer un identifiant de verrou.",
            {"index": fallback},
        )
    return verrou_id


def verrou_title_of(verrou: Dict[str, Any]) -> str:
    title = clean_sentence(
        verrou.get("verrou_title")
        or verrou.get("title")
        or verrou.get("objectif_rd")
        or verrou.get("objective")
    )
    if not title:
        raise ContractError(
            "missing_verrou_title",
            "La Phase 4.7 refuse de créer un titre de verrou.",
            {"verrou_id": clean_sentence(verrou.get("verrou_id") or verrou.get("id"))},
        )
    return title


def build_verrou_index(phase45: Dict[str, Any], phase46: Dict[str, Any]) -> List[Dict[str, Any]]:
    reasoning_items = get_verrous_reasoning(phase45)
    arg_items = get_project_argumentations(phase46)

    arg_by_id: Dict[str, Dict[str, Any]] = {}
    arg_by_title: Dict[str, Dict[str, Any]] = {}
    for i, arg in enumerate(arg_items, 1):
        vid = clean_sentence(arg.get("verrou_id") or argumentation_json(arg).get("verrou_id"))
        vt = clean_sentence(arg.get("verrou_title") or argumentation_json(arg).get("verrou_title"))
        if not vid or not vt:
            raise ContractError(
                "invalid_phase46_verrou",
                "La Phase 4.6 contient un verrou sans identifiant ou titre confirmé.",
                {"index": i},
            )
        if vid:
            arg_by_id[vid] = arg
        if vt:
            arg_by_title[fs_slug(vt)] = arg

    out: List[Dict[str, Any]] = []
    for i, verrou in enumerate(reasoning_items, 1):
        vid = verrou_id_of(verrou, i)
        vt = verrou_title_of(verrou)
        arg = arg_by_id.get(vid) or {}
        out.append({
            "verrou_id": vid,
            "verrou_title": vt,
            "phase_4_5": verrou,
            "phase_4_6": arg,
            "argumentation_json": argumentation_json(arg) if arg else {},
            "project_sections": argumentation_sections(arg) if arg else {},
        })

    if not out:
        for i, arg in enumerate(arg_items, 1):
            aj = argumentation_json(arg)
            out.append({
                "verrou_id": verrou_id_of({**aj, **arg}, i),
                "verrou_title": verrou_title_of({**aj, **arg}),
                "phase_4_5": {},
                "phase_4_6": arg,
                "argumentation_json": aj,
                "project_sections": argumentation_sections(arg),
            })
    return out


# ============================================================
# Family Builder
# ============================================================

def family_label_from_obj(obj: Dict[str, Any]) -> str:
    return canonical_label(
        obj.get("family_label")
        or obj.get("technical_family")
        or obj.get("family")
        or obj.get("label")
        or obj.get("name")
    )


def family_key(label: str) -> str:
    return normalize_concept_key(label)


def citations_from_obj(obj: Dict[str, Any]) -> List[str]:
    vals: List[Any] = []
    for key in [
        "all_citations", "direct_citations", "related_citations", "methodological_citations",
        "background_citations", "citations", "supported_by", "linked_citations",
    ]:
        vals += as_list(obj.get(key))
    if obj.get("citation_label"):
        vals.append(obj.get("citation_label"))
    if obj.get("citation"):
        vals.append(obj.get("citation"))
    out = [normalize_citation_label(x) for x in vals if normalize_citation_label(x)]
    return unique_clean_list(out)


def build_family_graph(verrou_index: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}

    for verrou in verrou_index:
        vid = verrou["verrou_id"]
        vt = verrou["verrou_title"]
        p45 = verrou.get("phase_4_5") or {}
        for fam in extract_approach_families(p45):
            label = family_label_from_obj(fam)
            if not label:
                continue
            key = family_key(label)
            bucket = buckets.setdefault(key, {
                "family_id": key,
                "family_label": label,
                "aliases": [],
                "verrous": [],
                "citations": [],
                "direct_citations": [],
                "related_citations": [],
                "methods": [],
                "signals": [],
                "strengths": [],
                "weaknesses": [],
                "source_count": 0,
            })
            bucket["aliases"].append(label)
            bucket["verrous"].append({"verrou_id": vid, "verrou_title": vt})
            bucket["citations"] += citations_from_obj(fam)
            bucket["direct_citations"] += [normalize_citation_label(x) for x in as_list(fam.get("direct_citations")) if normalize_citation_label(x)]
            bucket["related_citations"] += [normalize_citation_label(x) for x in as_list(fam.get("related_citations")) if normalize_citation_label(x)]
            bucket["signals"] += [clean_sentence(x) for x in as_list(fam.get("signals")) if clean_sentence(x)]
            bucket["source_count"] += 1
            for m in as_list(fam.get("technical_methods")):
                if isinstance(m, dict):
                    bucket["methods"].append({
                        "citation_label": normalize_citation_label(m.get("citation_label") or m.get("citation")),
                        "method_or_concept": clean_sentence(m.get("method_name") or m.get("concept_label") or m.get("method_or_concept")),
                        "principle": truncate(m.get("technical_principle") or m.get("principle"), 700),
                        "verrou_id": vid,
                    })

        # Ajouter les méthodes qui ont une technical_family mais pas forcément présentes dans approach_families.
        for meth in extract_technical_methods(p45):
            label = family_label_from_obj(meth)
            if not label:
                continue
            key = family_key(label)
            bucket = buckets.setdefault(key, {
                "family_id": key,
                "family_label": label,
                "aliases": [],
                "verrous": [],
                "citations": [],
                "direct_citations": [],
                "related_citations": [],
                "methods": [],
                "signals": [],
                "strengths": [],
                "weaknesses": [],
                "source_count": 0,
            })
            bucket["aliases"].append(label)
            bucket["verrous"].append({"verrou_id": vid, "verrou_title": vt})
            bucket["citations"] += citations_from_obj(meth)
            usage = clean_sentence(meth.get("usage_type"))
            cit = normalize_citation_label(meth.get("citation_label") or meth.get("citation"))
            if usage == "direct_evidence" and cit:
                bucket["direct_citations"].append(cit)
            elif cit:
                bucket["related_citations"].append(cit)
            bucket["methods"].append({
                "citation_label": cit,
                "method_or_concept": clean_sentence(meth.get("method_name") or meth.get("concept_label") or meth.get("method_or_concept")),
                "principle": truncate(meth.get("technical_principle") or meth.get("principle"), 700),
                "verrou_id": vid,
            })
            bucket["source_count"] += 1

    graph: List[Dict[str, Any]] = []
    for bucket in buckets.values():
        bucket["aliases"] = unique_clean_list(bucket["aliases"], limit=8)
        bucket["verrous"] = dedup_verrou_refs(bucket["verrous"])
        bucket["citations"] = unique_clean_list(bucket["citations"])
        bucket["direct_citations"] = unique_clean_list(bucket["direct_citations"])
        bucket["related_citations"] = unique_clean_list(bucket["related_citations"])
        bucket["signals"] = unique_clean_list(bucket["signals"], limit=20)
        bucket["methods"] = dedup_methods(bucket["methods"])
        bucket["coverage"] = {
            "verrous_count": len(bucket["verrous"]),
            "citations_count": len(bucket["citations"]),
            "direct_citations_count": len(bucket["direct_citations"]),
            "related_citations_count": len(bucket["related_citations"]),
        }
        bucket["phase5_usage_instruction"] = (
            "Présenter cette famille comme un bloc scientifique transversal. "
            "Ne pas répéter les mêmes concepts dans chaque verrou ; citer les articles en appui."
        )
        graph.append(bucket)

    return sorted(graph, key=lambda x: (x["coverage"]["verrous_count"], x["coverage"]["citations_count"]), reverse=True)


def dedup_verrou_refs(items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    seen = set()
    out: List[Dict[str, str]] = []
    for item in items:
        vid = clean_sentence(item.get("verrou_id"))
        vt = clean_sentence(item.get("verrou_title"))
        key = vid or fs_slug(vt)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"verrou_id": vid, "verrou_title": vt})
    return out


def dedup_methods(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for item in items:
        key = (normalize_citation_label(item.get("citation_label")), normalize_concept_key(item.get("method_or_concept")))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ============================================================
# Concept Builder
# ============================================================

def concept_label_from_method(m: Dict[str, Any]) -> str:
    return canonical_label(
        m.get("concept_label")
        or m.get("method_name")
        or m.get("method_or_concept")
        or m.get("subject_label")
        or m.get("technical_family")
    )


def concept_definition(m: Dict[str, Any]) -> str:
    return truncate(
        m.get("technical_principle")
        or m.get("principle")
        or m.get("mechanism")
        or m.get("principle_to_explain")
        or m.get("mechanism_to_explain")
        or m.get("what_state_of_art_shows"),
        900,
    )


def build_concept_graph(verrou_index: List[Dict[str, Any]], family_graph: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}

    for verrou in verrou_index:
        vid = verrou["verrou_id"]
        vt = verrou["verrou_title"]
        for m in extract_technical_methods(verrou.get("phase_4_5") or {}):
            label = concept_label_from_method(m)
            if not label:
                continue
            key = normalize_concept_key(label)
            if len(key) < 2:
                continue
            bucket = buckets.setdefault(key, {
                "concept_id": key,
                "concept_label": label,
                "aliases": [],
                "definition_candidates": [],
                "citations": [],
                "families": [],
                "verrous": [],
                "principles": [],
                "mechanisms": [],
                "limits": [],
                "usage_types": [],
            })
            bucket["aliases"].append(label)
            bucket["citations"] += citations_from_obj(m)
            fam = family_label_from_obj(m)
            if fam:
                bucket["families"].append(fam)
            bucket["verrous"].append({"verrou_id": vid, "verrou_title": vt})
            definition = concept_definition(m)
            if definition:
                bucket["definition_candidates"].append(definition)
            for key2, target in [
                ("technical_principle", "principles"),
                ("principle", "principles"),
                ("mechanism", "mechanisms"),
                ("mechanism_to_explain", "mechanisms"),
            ]:
                val = truncate(m.get(key2), 700)
                if val:
                    bucket[target].append(val)
            for lim_key in ["concept_limits", "transposability_limits", "limits_to_explain", "transposability_to_explain"]:
                bucket["limits"] += [truncate(x, 500) for x in as_list(m.get(lim_key)) if clean_sentence(x)]
            if m.get("usage_type"):
                bucket["usage_types"].append(clean_sentence(m.get("usage_type")))

    # Fusion douce : si deux concepts sont très proches lexicalement, les garder séparés sauf si même citation ou libellé presque inclus.
    graph: List[Dict[str, Any]] = []
    for bucket in buckets.values():
        aliases = unique_clean_list(bucket["aliases"], limit=10)
        label = choose_best_label(aliases)
        defs = unique_clean_list(bucket["definition_candidates"], limit=5)
        bucket["concept_label"] = label or bucket["concept_label"]
        bucket["aliases"] = aliases
        bucket["definition"] = defs[0] if defs else ""
        bucket["definition_candidates"] = defs
        bucket["citations"] = unique_clean_list(bucket["citations"])
        bucket["families"] = unique_clean_list(bucket["families"], limit=8)
        bucket["verrous"] = dedup_verrou_refs(bucket["verrous"])
        bucket["principles"] = unique_clean_list(bucket["principles"], limit=5)
        bucket["mechanisms"] = unique_clean_list(bucket["mechanisms"], limit=5)
        bucket["limits"] = unique_clean_list(bucket["limits"], limit=8)
        bucket["usage_types"] = unique_clean_list(bucket["usage_types"], limit=6)
        bucket["coverage"] = {
            "verrous_count": len(bucket["verrous"]),
            "citations_count": len(bucket["citations"]),
            "families_count": len(bucket["families"]),
        }
        bucket["phase5_usage_instruction"] = (
            "Expliquer ce concept une seule fois si plusieurs verrous le mobilisent. "
            "Puis rappeler seulement son rôle spécifique dans chaque verrou."
        )
        graph.append(bucket)

    return sorted(graph, key=lambda x: (x["coverage"]["verrous_count"], x["coverage"]["citations_count"]), reverse=True)


def choose_best_label(labels: List[str]) -> str:
    if not labels:
        return ""
    # Préférer label court mais informatif, pas phrase longue.
    sorted_labels = sorted(labels, key=lambda x: (0 if 2 <= len(x.split()) <= 5 else 1, len(x)))
    return sorted_labels[0]


# ============================================================
# Comparison Graph
# ============================================================

def build_comparison_graph(verrou_index: List[Dict[str, Any]], family_graph: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    comps: List[Dict[str, Any]] = []
    seen = set()

    # 1) Reprendre les comparaisons autorisées par Phase 4.5.
    for verrou in verrou_index:
        vid = verrou["verrou_id"]
        vt = verrou["verrou_title"]
        for c in extract_comparisons(verrou.get("phase_4_5") or {}):
            a = normalize_citation_label(c.get("a") or c.get("citation_a") or c.get("family_a"))
            b = normalize_citation_label(c.get("b") or c.get("citation_b") or c.get("family_b"))
            label_a = clean_sentence(c.get("a_method_or_family") or c.get("family_a") or c.get("method_a") or a)
            label_b = clean_sentence(c.get("b_method_or_family") or c.get("family_b") or c.get("method_b") or b)
            key = tuple(sorted([a or normalize_concept_key(label_a), b or normalize_concept_key(label_b)]))
            if not key or key in seen:
                continue
            seen.add(key)
            comps.append({
                "comparison_id": "cmp_" + fs_slug("_".join(key))[:80],
                "scope": "within_or_cross_verrou",
                "source": "phase_4_5_comparison_guard_matrix",
                "verrous": [{"verrou_id": vid, "verrou_title": vt}],
                "a": a,
                "b": b,
                "a_label": label_a,
                "b_label": label_b,
                "comparison_type": clean_sentence(c.get("comparison_type") or "cautious_comparison"),
                "comparability_score": c.get("comparability_score"),
                "evidence_basis": unique_clean_list(as_list(c.get("evidence_basis")), limit=10),
                "allowed_comparison_instruction": clean_sentence(c.get("allowed_comparison_instruction")),
                "forbidden_claims": unique_clean_list(as_list(c.get("forbidden_claims")), limit=8),
                "phase5_usage_instruction": "Comparer uniquement sur les dimensions explicitement sourcées. Ne pas affirmer de supériorité sans métrique.",
            })

    # 2) Ajouter comparaisons transversales entre familles proches si plusieurs familles existent.
    fams = family_graph[:8]
    for i in range(len(fams)):
        for j in range(i + 1, len(fams)):
            fa, fb = fams[i], fams[j]
            sim = jaccard_similarity(fa.get("family_label"), fb.get("family_label"))
            shared_verrous = shared_verrou_ids(fa.get("verrous") or [], fb.get("verrous") or [])
            shared_citations = sorted(set(fa.get("citations") or []) & set(fb.get("citations") or []))
            if not shared_verrous and sim < 0.15 and not shared_citations:
                continue
            key = tuple(sorted([fa.get("family_id"), fb.get("family_id")]))
            if key in seen:
                continue
            seen.add(key)
            comps.append({
                "comparison_id": "cmp_family_" + fs_slug("_".join(key))[:80],
                "scope": "cross_family",
                "source": "phase_4_7_family_overlap",
                "family_a": fa.get("family_label"),
                "family_b": fb.get("family_label"),
                "comparison_type": "cautious_family_comparison",
                "comparability_score": round(sim + len(shared_verrous) * 0.2 + len(shared_citations) * 0.1, 3),
                "evidence_basis": unique_clean_list([
                    "shared_verrou" if shared_verrous else "",
                    "shared_citation" if shared_citations else "",
                    "lexical_family_overlap" if sim >= 0.15 else "",
                ]),
                "shared_verrous": shared_verrous,
                "shared_citations": shared_citations,
                "allowed_comparison_instruction": "Comparer comme familles complémentaires ou partiellement recouvrantes, sans hiérarchie non prouvée.",
                "forbidden_claims": [
                    "Ne pas dire qu'une famille est meilleure sans métrique explicite.",
                    "Ne pas inventer une chronologie de progrès si elle n'est pas sourcée.",
                    "Ne pas déplacer une citation vers un verrou où elle n'a pas été sélectionnée.",
                ],
                "phase5_usage_instruction": "Utiliser pour transitions et mise en perspective globale, pas comme preuve de performance.",
            })

    return sorted(comps, key=lambda x: float(x.get("comparability_score") or 0), reverse=True)[:20]


def shared_verrou_ids(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> List[str]:
    sa = {clean_sentence(x.get("verrou_id")) for x in a if isinstance(x, dict)}
    sb = {clean_sentence(x.get("verrou_id")) for x in b if isinstance(x, dict)}
    return sorted(x for x in (sa & sb) if x)


# ============================================================
# Shared limitations / consensus / contradictions
# ============================================================

def limitation_type_from_text(text: str) -> str:
    low = clean_sentence(text).lower()
    rules = [
        ("data_dependency", ["data", "données", "dataset", "jeu de données", "labeled", "unlabeled", "représent"]),
        ("protocol_validation", ["protocole", "validation", "benchmark", "métrique", "mesure", "évaluation", "experiment"]),
        ("robustness_generalization", ["robust", "général", "general", "corruption", "noise", "bruit", "variation"]),
        ("computational_complexity", ["comput", "coût", "temps", "complex", "prohibitive", "ressource"]),
        ("transposability", ["transpos", "real-world", "contexte", "conditions", "appliquer", "apply", "domain"]),
        ("model_assumption", ["hypoth", "assumption", "model", "param", "architecture"]),
    ]
    for label, kws in rules:
        if any(k in low for k in kws):
            return label
    return "generic_technical_limit"


def build_shared_limitations(verrou_index: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for verrou in verrou_index:
        vid = verrou["verrou_id"]
        vt = verrou["verrou_title"]
        p45 = verrou.get("phase_4_5") or {}
        for lim in extract_limit_causality(p45):
            cause = clean_sentence(lim.get("cause_from_source") or lim.get("limit") or lim.get("source_limit") or lim.get("text"))
            impact = clean_sentence(lim.get("impact_on_current_verrou") or lim.get("impact_on_verrou") or lim.get("project_impact"))
            method = clean_sentence(lim.get("method_or_concept") or lim.get("concept_label") or lim.get("method_name"))
            citation = normalize_citation_label(lim.get("citation_label") or lim.get("citation"))
            ltype = clean_sentence(lim.get("limit_type")) or limitation_type_from_text(" ".join([cause, impact]))
            bucket = buckets.setdefault(ltype, {
                "limitation_type": ltype,
                "label": human_limit_label(ltype),
                "verrous": [],
                "citations": [],
                "methods_or_concepts": [],
                "causes": [],
                "impacts": [],
            })
            bucket["verrous"].append({"verrou_id": vid, "verrou_title": vt})
            if citation:
                bucket["citations"].append(citation)
            if method:
                bucket["methods_or_concepts"].append(method)
            if cause:
                bucket["causes"].append(truncate(cause, 600))
            if impact:
                bucket["impacts"].append(truncate(impact, 600))

        # Limites venant de Phase 4.6.
        aj = verrou.get("argumentation_json") or {}
        for lim in as_list(aj.get("unresolved_project_limits")):
            if not isinstance(lim, dict):
                continue
            text = clean_sentence(lim.get("limit"))
            ltype = limitation_type_from_text(text)
            bucket = buckets.setdefault(ltype, {
                "limitation_type": ltype,
                "label": human_limit_label(ltype),
                "verrous": [],
                "citations": [],
                "methods_or_concepts": [],
                "causes": [],
                "impacts": [],
            })
            bucket["verrous"].append({"verrou_id": vid, "verrou_title": vt})
            bucket["citations"] += [normalize_citation_label(x) for x in as_list(lim.get("supported_by")) if normalize_citation_label(x)]
            if text:
                bucket["causes"].append(truncate(text, 600))
            if lim.get("project_impact"):
                bucket["impacts"].append(truncate(lim.get("project_impact"), 600))

    out: List[Dict[str, Any]] = []
    for b in buckets.values():
        b["verrous"] = dedup_verrou_refs(b["verrous"])
        b["citations"] = unique_clean_list(b["citations"])
        b["methods_or_concepts"] = unique_clean_list(b["methods_or_concepts"], limit=10)
        b["causes"] = unique_clean_list(b["causes"], limit=8)
        b["impacts"] = unique_clean_list(b["impacts"], limit=8)
        b["coverage"] = {
            "verrous_count": len(b["verrous"]),
            "citations_count": len(b["citations"]),
        }
        b["phase5_usage_instruction"] = "Formuler cette limite comme faiblesse transversale si elle touche plusieurs verrous ; sinon comme limite locale."
        out.append(b)
    return sorted(out, key=lambda x: (x["coverage"]["verrous_count"], x["coverage"]["citations_count"]), reverse=True)


def human_limit_label(limit_type: str) -> str:
    mapping = {
        "data_dependency": "Dépendance aux données et à leur représentativité",
        "protocol_validation": "Validation expérimentale et protocole d'évaluation",
        "robustness_generalization": "Robustesse et généralisation",
        "computational_complexity": "Complexité ou coût de mise en œuvre",
        "transposability": "Transposabilité au contexte projet",
        "model_assumption": "Hypothèses de modèle ou de paramétrage",
        "generic_technical_limit": "Incertitude technique générique",
    }
    return mapping.get(limit_type, limit_type.replace("_", " "))


def build_scientific_consensus(family_graph: List[Dict[str, Any]], shared_limitations: List[Dict[str, Any]], concept_graph: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    consensus: List[Dict[str, Any]] = []
    for fam in family_graph:
        if fam.get("coverage", {}).get("citations_count", 0) >= 2:
            consensus.append({
                "type": "family_recurrence",
                "claim": f"La famille « {fam.get('family_label')} » revient comme axe scientifique structurant.",
                "families": [fam.get("family_label")],
                "citations": fam.get("citations") or [],
                "verrous": fam.get("verrous") or [],
                "confidence": "medium" if fam.get("coverage", {}).get("verrous_count", 0) <= 1 else "high",
                "phase5_usage_instruction": "Peut être utilisé pour introduire une famille d'approches, sans affirmer un consensus de performance.",
            })
    for lim in shared_limitations:
        if lim.get("coverage", {}).get("citations_count", 0) >= 2:
            consensus.append({
                "type": "shared_limitation",
                "claim": f"Plusieurs sources convergent vers une limite liée à : {lim.get('label')}.",
                "limitation_type": lim.get("limitation_type"),
                "citations": lim.get("citations") or [],
                "verrous": lim.get("verrous") or [],
                "confidence": "medium" if lim.get("coverage", {}).get("verrous_count", 0) <= 1 else "high",
                "phase5_usage_instruction": "Utiliser pour construire le passage des acquis vers le gap R&D.",
            })
    return consensus[:20]


def build_scientific_contradictions(comparison_graph: List[Dict[str, Any]], shared_limitations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    contradictions: List[Dict[str, Any]] = []
    for comp in comparison_graph:
        if comp.get("comparison_type") in {"cautious_comparison", "cautious_family_comparison"}:
            contradictions.append({
                "type": "unresolved_comparison",
                "claim": "Des approches sont comparables seulement avec prudence ; aucune supériorité ne peut être affirmée sans métrique explicite.",
                "comparison_id": comp.get("comparison_id"),
                "a": comp.get("a_label") or comp.get("family_a"),
                "b": comp.get("b_label") or comp.get("family_b"),
                "evidence_basis": comp.get("evidence_basis") or [],
                "phase5_usage_instruction": "Présenter comme tension méthodologique, pas comme contradiction factuelle forte.",
            })
    for lim in shared_limitations:
        if lim.get("limitation_type") in {"robustness_generalization", "protocol_validation", "transposability"}:
            contradictions.append({
                "type": "claim_vs_validation_gap",
                "claim": f"Les approches apportent des mécanismes utiles, mais leur validation reste limitée sur : {lim.get('label')}.",
                "limitation_type": lim.get("limitation_type"),
                "citations": lim.get("citations") or [],
                "phase5_usage_instruction": "Utiliser pour expliquer pourquoi l'état de l'art ne clôt pas le verrou.",
            })
    return contradictions[:20]


# ============================================================
# Cross-verrou reasoning / progression / story
# ============================================================

def build_cross_verrou_reasoning(verrou_index: List[Dict[str, Any]], family_graph: List[Dict[str, Any]], shared_limitations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if len(verrou_index) <= 1:
        v = verrou_index[0] if verrou_index else {}
        out.append({
            "type": "single_verrou_project",
            "claim": "Le dossier comporte un seul verrou structurant ; la narration globale doit donc approfondir ce verrou plutôt que créer des transitions artificielles entre verrous.",
            "verrous": [{"verrou_id": v.get("verrou_id"), "verrou_title": v.get("verrou_title")} ] if v else [],
            "phase5_usage_instruction": "Écrire une narration globale centrée sur les familles, concepts, limites et gap du verrou unique.",
        })
        return out

    for fam in family_graph:
        verrous = fam.get("verrous") or []
        if len(verrous) >= 2:
            out.append({
                "type": "shared_family_between_verrous",
                "claim": f"La famille « {fam.get('family_label')} » relie plusieurs verrous et peut servir de fil conducteur transversal.",
                "family": fam.get("family_label"),
                "verrous": verrous,
                "citations": fam.get("citations") or [],
                "phase5_usage_instruction": "Introduire cette famille une seule fois, puis expliquer son rôle par verrou.",
            })
    for lim in shared_limitations:
        verrous = lim.get("verrous") or []
        if len(verrous) >= 2:
            out.append({
                "type": "shared_limitation_between_verrous",
                "claim": f"La limite « {lim.get('label')} » traverse plusieurs verrous et peut soutenir une démonstration R&D globale.",
                "limitation_type": lim.get("limitation_type"),
                "verrous": verrous,
                "citations": lim.get("citations") or [],
                "phase5_usage_instruction": "Utiliser comme justification globale des travaux R&D.",
            })
    return out[:20]


def build_scientific_progression(family_graph: List[Dict[str, Any]], concept_graph: List[Dict[str, Any]], comparison_graph: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    progression: List[Dict[str, Any]] = []

    # Progression conceptuelle générique : contexte -> familles directes -> familles génératives -> évaluation/robustesse -> limites.
    # Pas de hardcoding domaine : on classe par mots structurels et couverture.
    fams = family_graph[:]

    def fam_rank(f: Dict[str, Any]) -> Tuple[int, int, str]:
        label = clean_sentence(f.get("family_label")).lower()
        if any(k in label for k in ["évaluation", "evaluation", "robust", "général", "general"]):
            stage = 4
        elif any(k in label for k in ["augmentation", "generation", "génération", "synthetic", "synth"]):
            stage = 3
        elif any(k in label for k in ["classification", "apprentissage", "learning", "cnn", "deep"]):
            stage = 2
        else:
            stage = 1
        return (stage, -int(f.get("coverage", {}).get("citations_count", 0)), label)

    for idx, fam in enumerate(sorted(fams, key=fam_rank), 1):
        progression.append({
            "step": idx,
            "stage": stage_name(fam_rank(fam)[0]),
            "family": fam.get("family_label"),
            "citations": fam.get("citations") or [],
            "verrous": fam.get("verrous") or [],
            "role_in_story": role_for_family(fam.get("family_label")),
        })

    if not progression and concept_graph:
        for idx, concept in enumerate(concept_graph[:8], 1):
            progression.append({
                "step": idx,
                "stage": "conceptual_axis",
                "concept": concept.get("concept_label"),
                "citations": concept.get("citations") or [],
                "role_in_story": "Concept à introduire dans l'histoire scientifique globale.",
            })
    return progression


def stage_name(stage: int) -> str:
    return {
        1: "foundational_or_signal_modeling",
        2: "learning_and_classification_methods",
        3: "augmentation_or_generation_methods",
        4: "evaluation_robustness_and_limits",
    }.get(stage, "scientific_axis")


def role_for_family(label: Any) -> str:
    low = clean_sentence(label).lower()
    if any(k in low for k in ["évaluation", "evaluation", "robust", "général", "general"]):
        return "Sert à discuter la validation, la robustesse, la généralisation et les limites de l'état de l'art."
    if any(k in low for k in ["augmentation", "generation", "génération", "synthetic", "synth"]):
        return "Sert à expliquer les mécanismes de génération ou diversification des données."
    if any(k in low for k in ["classification", "apprentissage", "learning"]):
        return "Sert à cadrer les modèles d'apprentissage ou de classification mobilisables."
    return "Sert à introduire un axe scientifique structurant du dossier."


def build_remaining_unknowns(verrou_index: List[Dict[str, Any]], shared_limitations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unknowns: List[Dict[str, Any]] = []
    for verrou in verrou_index:
        vid = verrou["verrou_id"]
        vt = verrou["verrou_title"]
        aj = verrou.get("argumentation_json") or {}
        rd_gap = clean_sentence(aj.get("rd_gap"))
        if rd_gap:
            unknowns.append({
                "scope": "verrou",
                "verrou_id": vid,
                "verrou_title": vt,
                "unknown": truncate(rd_gap, 900),
                "source": "phase_4_6_rd_gap",
            })
        sections = verrou.get("project_sections") or {}
        gap_sec = clean_sentence(sections.get("section_5_gap_rd"))
        if gap_sec and gap_sec != rd_gap:
            unknowns.append({
                "scope": "verrou",
                "verrou_id": vid,
                "verrou_title": vt,
                "unknown": truncate(gap_sec, 900),
                "source": "phase_4_6_section_5_gap_rd",
            })
    for lim in shared_limitations[:8]:
        unknowns.append({
            "scope": "global_limitation",
            "limitation_type": lim.get("limitation_type"),
            "unknown": f"Incertitude transversale liée à : {lim.get('label')}",
            "citations": lim.get("citations") or [],
            "verrous": lim.get("verrous") or [],
            "source": "phase_4_7_shared_limitations",
        })
    # Dédup
    seen = set()
    out = []
    for u in unknowns:
        key = re.sub(r"\W+", "", clean_sentence(u.get("unknown")).lower())[:180]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out[:20]


def build_scientific_story(
    verrou_index: List[Dict[str, Any]],
    family_graph: List[Dict[str, Any]],
    concept_graph: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
    scientific_progression: List[Dict[str, Any]],
    remaining_unknowns: List[Dict[str, Any]],
) -> Dict[str, Any]:
    verrou_titles = [v.get("verrou_title") for v in verrou_index if v.get("verrou_title")]
    top_families = [f.get("family_label") for f in family_graph[:5] if f.get("family_label")]
    top_concepts = [c.get("concept_label") for c in concept_graph[:8] if c.get("concept_label")]
    top_limits = [l.get("label") for l in shared_limitations[:5] if l.get("label")]

    if len(verrou_index) <= 1:
        context = (
            "Le dossier est structuré autour d'un verrou principal. La narration scientifique doit donc partir du besoin de validation du verrou, "
            "introduire les familles d'approches identifiées, expliquer leurs mécanismes, puis montrer pourquoi leurs limites laissent subsister un besoin R&D."
        )
    else:
        context = (
            "Le dossier contient plusieurs verrous qui doivent être reliés par une même logique scientifique. "
            "La narration doit éviter une succession indépendante de sections et construire un fil conducteur transversal."
        )

    return {
        "story_type": "global_scientific_narrative_blueprint_not_final_text",
        "context": context,
        "verrous_to_cover": unique_clean_list(verrou_titles),
        "main_scientific_families": unique_clean_list(top_families),
        "main_concepts_to_explain_once": unique_clean_list(top_concepts),
        "main_shared_limitations": unique_clean_list(top_limits),
        "scientific_progression": scientific_progression,
        "current_state": (
            "L'état de l'art fournit des familles d'approches et des concepts mobilisables, mais ceux-ci doivent être discutés comme acquis partiels, non comme démonstration directe du projet."
        ),
        "remaining_problem": (
            "Le problème restant porte sur la validation, la transposition et la robustesse dans les conditions propres du dossier."
        ),
        "recommended_story_arc": [
            "Partir du besoin scientifique et du verrou, pas des articles.",
            "Introduire les familles principales une seule fois.",
            "Expliquer les concepts/méthodes les plus structurants avec leurs mécanismes.",
            "Comparer uniquement les familles ou méthodes explicitement comparables.",
            "Faire apparaître les limites communes : données, validation, robustesse, transposabilité, protocole.",
            "Relier ces limites au gap R&D et aux travaux expérimentaux nécessaires.",
            "Revenir ensuite aux spécificités de chaque verrou sans répéter les mêmes définitions.",
        ],
        "anti_hallucination_rules": [
            "Ne pas inventer de chronologie historique si les dates ou étapes ne sont pas disponibles.",
            "Ne pas affirmer qu'une méthode est meilleure sans métrique explicite.",
            "Ne pas déplacer une citation vers un verrou où elle n'a pas été sélectionnée.",
            "Ne pas utiliser la Phase 4.7 comme source scientifique ; elle organise seulement les sources 4.5/4.6.",
        ],
        "remaining_unknowns_summary": [truncate(u.get("unknown"), 400) for u in remaining_unknowns[:8]],
    }


# ============================================================
# Phase 5 writer blueprint
# ============================================================

def build_phase5_writer_blueprint(
    verrou_index: List[Dict[str, Any]],
    scientific_story: Dict[str, Any],
    family_graph: List[Dict[str, Any]],
    concept_graph: List[Dict[str, Any]],
    comparison_graph: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
    cross_verrou_reasoning: List[Dict[str, Any]],
) -> Dict[str, Any]:
    recommended_order = [
        "global_scientific_context",
        "main_families_and_concepts",
        "controlled_comparisons",
        "shared_limitations_and_gap",
        "project_rd_argumentation_by_verrou",
        "experimental_work_needed",
        "cir_synthesis",
    ]
    if len(verrou_index) <= 1:
        recommended_order = [
            "verrou_scientific_context",
            "main_families_and_concepts",
            "controlled_comparisons",
            "limitations_and_gap_for_this_verrou",
            "project_rd_argumentation",
            "experimental_work_needed",
            "cir_synthesis",
        ]

    return {
        "blueprint_type": "phase5_global_writer_blueprint",
        "writing_logic": "Passer d'un raisonnement local par verrou à une narration scientifique globale structurée.",
        "recommended_order": recommended_order,
        "concepts_to_explain_first": [
            {
                "concept": c.get("concept_label"),
                "citations": c.get("citations") or [],
                "families": c.get("families") or [],
                "instruction": c.get("phase5_usage_instruction"),
            }
            for c in concept_graph[:10]
        ],
        "families_to_introduce_once": [
            {
                "family": f.get("family_label"),
                "citations": f.get("citations") or [],
                "verrous": f.get("verrous") or [],
                "instruction": f.get("phase5_usage_instruction"),
            }
            for f in family_graph[:8]
        ],
        "comparisons_to_make": [
            {
                "comparison_id": c.get("comparison_id"),
                "a": c.get("a_label") or c.get("family_a"),
                "b": c.get("b_label") or c.get("family_b"),
                "type": c.get("comparison_type"),
                "instruction": c.get("allowed_comparison_instruction"),
            }
            for c in comparison_graph[:8]
        ],
        "limitations_to_turn_into_gap": [
            {
                "label": l.get("label"),
                "type": l.get("limitation_type"),
                "citations": l.get("citations") or [],
                "verrous": l.get("verrous") or [],
            }
            for l in shared_limitations[:8]
        ],
        "cross_verrou_transitions": [
            {
                "type": x.get("type"),
                "claim": x.get("claim"),
                "instruction": x.get("phase5_usage_instruction"),
            }
            for x in cross_verrou_reasoning[:8]
        ],
        "transition_templates": [
            "Après avoir établi les mécanismes disponibles dans l'état de l'art, la question devient celle de leur transposition dans les conditions du projet.",
            "Cette famille d'approches constitue un socle utile, mais ses limites de validation empêchent de conclure directement à la levée du verrou.",
            "Les travaux connexes élargissent le cadrage méthodologique, sans remplacer la démonstration expérimentale propre au dossier.",
            "La convergence des limites observées justifie de formuler le gap comme une incertitude résiduelle à traiter par les travaux R&D.",
        ],
        "forbidden_patterns": [
            "Ne pas écrire une liste article par article.",
            "Ne pas commencer les paragraphes par [A1], [A2], etc.",
            "Ne pas répéter la définition d'un même concept dans plusieurs verrous.",
            "Ne pas transformer les articles connexes en preuves directes.",
            "Ne pas conclure à une performance supérieure sans résultat comparatif explicite.",
        ],
    }


# ============================================================
# Quality
# ============================================================

def compute_quality(payload: Dict[str, Any]) -> Dict[str, Any]:
    warnings: List[str] = []
    score = 100.0

    if not payload.get("family_graph"):
        score -= 18
        warnings.append("family_graph vide")
    if not payload.get("concept_graph"):
        score -= 18
        warnings.append("concept_graph vide")
    if not payload.get("shared_limitations"):
        score -= 12
        warnings.append("shared_limitations vide")
    if not payload.get("phase5_writer_blueprint"):
        score -= 15
        warnings.append("phase5_writer_blueprint manquant")
    if not payload.get("scientific_story", {}).get("recommended_story_arc"):
        score -= 10
        warnings.append("recommended_story_arc manquant")
    if not payload.get("cross_verrou_reasoning"):
        score -= 5
        warnings.append("cross_verrou_reasoning faible ou vide")

    # Cohérence citations : aucune citation issue des familles/concepts ne doit être perdue dans global_citation_index.
    all_cits = set()
    for f in payload.get("family_graph") or []:
        all_cits |= set(f.get("citations") or [])
    for c in payload.get("concept_graph") or []:
        all_cits |= set(c.get("citations") or [])
    indexed = set((payload.get("global_citation_index") or {}).keys())
    missing = sorted(all_cits - indexed)
    if missing:
        score -= min(10, len(missing) * 2)
        warnings.append("citations non indexées: " + ", ".join(missing[:10]))

    level = "good" if score >= 85 else "medium" if score >= 65 else "weak"
    return {
        "score": round(max(0, min(100, score)), 2),
        "level": level,
        "warnings": warnings,
    }


# ============================================================
# Citation index
# ============================================================

def build_global_citation_index(verrou_index: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}
    for verrou in verrou_index:
        vid = verrou["verrou_id"]
        vt = verrou["verrou_title"]
        for m in extract_technical_methods(verrou.get("phase_4_5") or {}):
            cit = normalize_citation_label(m.get("citation_label") or m.get("citation"))
            if not cit:
                continue
            item = idx.setdefault(cit, {
                "citation": citation_bracket(cit),
                "verrous": [],
                "families": [],
                "concepts": [],
                "usage_types": [],
                "titles": [],
            })
            item["verrous"].append({"verrou_id": vid, "verrou_title": vt})
            fam = family_label_from_obj(m)
            if fam:
                item["families"].append(fam)
            concept = concept_label_from_method(m)
            if concept:
                item["concepts"].append(concept)
            if m.get("usage_type"):
                item["usage_types"].append(clean_sentence(m.get("usage_type")))
            if m.get("article_title"):
                item["titles"].append(clean_sentence(m.get("article_title")))
        aj = verrou.get("argumentation_json") or {}
        for e in as_list(aj.get("evidence_by_citation")):
            if not isinstance(e, dict):
                continue
            cit = normalize_citation_label(e.get("citation") or e.get("citation_label"))
            if not cit:
                continue
            item = idx.setdefault(cit, {
                "citation": citation_bracket(cit),
                "verrous": [],
                "families": [],
                "concepts": [],
                "usage_types": [],
                "titles": [],
            })
            item["verrous"].append({"verrou_id": vid, "verrou_title": vt})
            if e.get("subject_label"):
                item["concepts"].append(clean_sentence(e.get("subject_label")))
            if e.get("usage_type"):
                item["usage_types"].append(clean_sentence(e.get("usage_type")))
            if e.get("article_title"):
                item["titles"].append(clean_sentence(e.get("article_title")))
    for item in idx.values():
        item["verrous"] = dedup_verrou_refs(item["verrous"])
        item["families"] = unique_clean_list(item["families"], limit=8)
        item["concepts"] = unique_clean_list(item["concepts"], limit=8)
        item["usage_types"] = unique_clean_list(item["usage_types"], limit=8)
        item["titles"] = unique_clean_list(item["titles"], limit=3)
    return dict(sorted(idx.items(), key=lambda kv: kv[0]))


# ============================================================
# Markdown summary
# ============================================================

def build_markdown_summary(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Phase 4.7 — Scientific Narrative Builder")
    lines.append("")
    lines.append(f"Payload: `{payload.get('payload_type')}`")
    lines.append(f"OK: `{payload.get('ok')}`")
    q = payload.get("quality") or {}
    lines.append(f"Quality: `{q.get('score')}` / `{q.get('level')}`")
    lines.append("")

    story = payload.get("scientific_story") or {}
    lines.append("## Scientific story")
    lines.append("")
    lines.append(story.get("context") or "")
    lines.append("")
    lines.append("### Story arc")
    for s in story.get("recommended_story_arc") or []:
        lines.append(f"- {s}")
    lines.append("")

    lines.append("## Family graph")
    for f in payload.get("family_graph") or []:
        lines.append(f"- **{f.get('family_label')}** — citations: {', '.join(f.get('citations') or [])} — verrous: {len(f.get('verrous') or [])}")
    lines.append("")

    lines.append("## Concept graph")
    for c in (payload.get("concept_graph") or [])[:15]:
        lines.append(f"- **{c.get('concept_label')}** — citations: {', '.join(c.get('citations') or [])} — familles: {', '.join(c.get('families') or [])}")
    lines.append("")

    lines.append("## Shared limitations")
    for l in payload.get("shared_limitations") or []:
        lines.append(f"- **{l.get('label')}** — citations: {', '.join(l.get('citations') or [])} — verrous: {len(l.get('verrous') or [])}")
    lines.append("")

    lines.append("## Comparisons")
    for c in (payload.get("comparison_graph") or [])[:10]:
        a = c.get("a_label") or c.get("family_a") or c.get("a")
        b = c.get("b_label") or c.get("family_b") or c.get("b")
        lines.append(f"- **{a}** vs **{b}** — {c.get('comparison_type')} — score `{c.get('comparability_score')}`")
    lines.append("")

    lines.append("## Phase 5 blueprint")
    bp = payload.get("phase5_writer_blueprint") or {}
    lines.append("Recommended order:")
    for step in bp.get("recommended_order") or []:
        lines.append(f"- {step}")
    lines.append("")

    if q.get("warnings"):
        lines.append("## Warnings")
        for w in q.get("warnings") or []:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"




# ============================================================
# V2 — Scientific Knowledge Base Builder
# ============================================================

INTERNAL_FAMILY_LABEL_WARNING = (
    "Les familles scientifiques sont des outils de structuration internes. "
    "Phase 5 ne doit pas écrire explicitement 'famille X' sauf si le style demandé l'exige."
)


def is_placeholder_limit(text: Any) -> bool:
    s = clean_sentence(text).lower()
    if not s:
        return True
    bad = [
        "les auteurs ne formulent pas de limitation explicite",
        "non explicitement",
        "not explicitly",
        "aucune limitation",
        "pas de limitation explicite",
    ]
    return any(b in s for b in bad)


def clean_limit_sentence(text: Any, limit: int = 520) -> str:
    s = clean_sentence(text)
    if is_placeholder_limit(s):
        return ""
    # Évite de faire remonter des bouts de paragraphes OCR trop longs ou trop bruts.
    s = re.sub(r"\[[0-9]+\]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return truncate(s, limit)


def citation_sort_key(label: Any) -> Tuple[int, str]:
    c = normalize_citation_label(label)
    m = re.search(r"A(\d+)", c)
    return (int(m.group(1)) if m else 999999, c)


def safe_citations(labels: List[Any], limit: Optional[int] = None) -> List[str]:
    vals = unique_clean_list([normalize_citation_label(x) for x in labels if normalize_citation_label(x)])
    vals = sorted(vals, key=citation_sort_key)
    return vals[:limit] if limit else vals


def method_label_from_any(obj: Dict[str, Any]) -> str:
    return clean_sentence(
        obj.get("method_or_concept")
        or obj.get("method_name")
        or obj.get("concept_label")
        or obj.get("subject_label")
        or obj.get("label")
        or obj.get("name")
    )


def principle_from_any(obj: Dict[str, Any]) -> str:
    return truncate(
        obj.get("technical_principle")
        or obj.get("principle")
        or obj.get("mechanism")
        or obj.get("definition")
        or obj.get("definition_candidates")
        or obj.get("principles")
        or obj.get("mechanisms"),
        900,
    )


def family_for_concept(concept: Dict[str, Any], family_graph: List[Dict[str, Any]]) -> str:
    fams = as_list(concept.get("families"))
    if fams:
        return clean_sentence(fams[0])
    label = concept.get("concept_label")
    best = ""
    best_score = 0.0
    for f in family_graph:
        score = jaccard_similarity(label, f.get("family_label"))
        for m in as_list(f.get("methods")):
            score = max(score, jaccard_similarity(label, m.get("method_or_concept")))
        if score > best_score:
            best_score = score
            best = clean_sentence(f.get("family_label"))
    return best if best_score >= 0.08 else ""


def infer_scientific_role(concept: Dict[str, Any], family_label: str = "") -> str:
    label = clean_sentence(concept.get("concept_label"))
    text = " ".join([
        label,
        family_label,
        clean_sentence(concept.get("definition")),
        clean_sentence(concept.get("principles")),
        clean_sentence(concept.get("mechanisms")),
    ]).lower()
    if any(k in text for k in ["uncertainty", "incertitude", "robust", "corruption", "evaluation", "évaluation", "général", "general"]):
        return "qualifier la robustesse, l'incertitude et la capacité de généralisation des modèles"
    if any(k in text for k in ["synthetic", "synthétique", "augmentation", "augment", "generation", "génération", "diffusion", "adversarial", "masque", "mask"]):
        return "augmenter la diversité ou la représentativité des données d'apprentissage"
    if any(k in text for k in ["sparse", "signal", "représentation", "representation", "phase", "scatter"]):
        return "modéliser ou représenter le signal afin de générer ou exploiter des observations pertinentes"
    if any(k in text for k in ["classification", "cnn", "deep learning", "apprentissage profond", "réseau", "neural"]):
        return "apprendre des représentations discriminantes pour la classification ou la reconnaissance"
    return "apporter un mécanisme scientifique utile au traitement du verrou"


def infer_why_used(concept: Dict[str, Any], project_need: str = "") -> str:
    role = infer_scientific_role(concept, clean_sentence(as_list(concept.get("families"))[0]) if as_list(concept.get("families")) else "")
    if project_need:
        return f"Ce concept est mobilisable car il contribue à {role}, ce qui répond partiellement au besoin projet suivant : {truncate(project_need, 350)}"
    return f"Ce concept est mobilisable car il contribue à {role}."


def extract_clean_limitations_for_concept(concept: Dict[str, Any], shared_limitations: List[Dict[str, Any]]) -> List[str]:
    label = clean_sentence(concept.get("concept_label"))
    cites = set(safe_citations(concept.get("citations") or []))
    vals: List[str] = []
    for lim in as_list(concept.get("limits")):
        cleaned = clean_limit_sentence(lim)
        if cleaned:
            vals.append(cleaned)
    for shared in shared_limitations:
        scites = set(safe_citations(shared.get("citations") or []))
        methods = " ".join(clean_sentence(x) for x in as_list(shared.get("methods_or_concepts"))).lower()
        if cites & scites or (label and label.lower() in methods):
            if shared.get("label"):
                vals.append(clean_sentence(shared.get("label")))
            for impact in as_list(shared.get("impacts")):
                cleaned = clean_limit_sentence(impact)
                if cleaned:
                    vals.append(cleaned)
    return unique_clean_list(vals, limit=5)


def build_scientific_knowledge_base(
    verrou_index: List[Dict[str, Any]],
    family_graph: List[Dict[str, Any]],
    concept_graph: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    V2: transforme les concepts/familles en connaissances scientifiques rédigeables.
    Le but n'est plus de dire 'A1 fait X', mais de fournir à Phase 5 :
    - un concept ;
    - son principe ;
    - son rôle scientifique ;
    - pourquoi il est utile ;
    - ses limites ;
    - son lien au verrou/projet ;
    - les citations qui le soutiennent.
    """
    project_need = ""
    project_gap = ""
    if verrou_index:
        aj = verrou_index[0].get("argumentation_json") or {}
        project_need = clean_sentence(aj.get("project_need") or aj.get("technical_need") or aj.get("need") or "")
        project_gap = clean_sentence(aj.get("rd_gap") or aj.get("gap") or "")
        sections = verrou_index[0].get("project_sections") or {}
        project_need = project_need or clean_sentence(sections.get("section_1_project_need") or sections.get("positionnement") or "")
        project_gap = project_gap or clean_sentence(sections.get("section_5_gap_rd") or "")

    items: List[Dict[str, Any]] = []
    for concept in concept_graph:
        label = clean_sentence(concept.get("concept_label"))
        if not label:
            continue
        family = family_for_concept(concept, family_graph)
        principle = principle_from_any(concept)
        limits = extract_clean_limitations_for_concept(concept, shared_limitations)
        citations = safe_citations(concept.get("citations") or [])
        verrous = dedup_verrou_refs(as_list(concept.get("verrous")))
        role = infer_scientific_role(concept, family)
        why_used = infer_why_used(concept, project_need)
        usable_limit = limits[0] if limits else "La littérature ne suffit pas à démontrer la transposition de ce mécanisme dans les conditions propres du projet."
        items.append({
            "knowledge_id": normalize_concept_key(label),
            "concept": label,
            "internal_family": family,
            "scientific_role": role,
            "technical_principle": principle,
            "why_it_matters_for_project": why_used,
            "how_to_explain_in_text": build_concept_explanation_sentence(label, principle, role),
            "advantages_or_contribution": infer_contribution_sentences(label, principle, role),
            "limitations": limits,
            "main_limitation_to_discuss": usable_limit,
            "gap_link": truncate(project_gap, 650) if project_gap else "À rattacher au besoin de validation, de robustesse et de transposition propre au projet.",
            "citations": citations,
            "verrous": verrous,
            "usage_types": unique_clean_list(concept.get("usage_types") or [], limit=5),
            "writer_instruction": (
                "Expliquer ce concept comme une idée scientifique dans un paragraphe argumentatif. "
                "Ne pas le présenter comme une fiche article. Ne pas afficher le nom de la famille. "
                "Utiliser les citations uniquement comme appui."
            ),
        })

    # Ajouter une connaissance synthétique par famille uniquement si elle regroupe plusieurs concepts.
    # Ces entrées sont internes et servent à organiser l'histoire, pas à afficher des titres de familles.
    for fam in family_graph:
        methods = [m for m in as_list(fam.get("methods")) if isinstance(m, dict)]
        if len(methods) < 2:
            continue
        label = clean_sentence(fam.get("family_label"))
        if not label:
            continue
        cites = safe_citations(fam.get("citations") or [], limit=8)
        method_names = unique_clean_list([method_label_from_any(m) for m in methods if method_label_from_any(m)], limit=8)
        principle = synthesize_family_principle(label, methods)
        items.append({
            "knowledge_id": "family_axis_" + normalize_concept_key(label),
            "concept": label,
            "is_internal_grouping_axis": True,
            "internal_family": label,
            "scientific_role": role_for_family(label),
            "technical_principle": principle,
            "why_it_matters_for_project": f"Cet axe regroupe plusieurs mécanismes utiles pour traiter le verrou, notamment : {', '.join(method_names[:5])}.",
            "how_to_explain_in_text": principle,
            "advantages_or_contribution": ["Il permet d'introduire plusieurs approches sans les résumer article par article."],
            "limitations": [],
            "main_limitation_to_discuss": "Ces approches doivent rester discutées comme des acquis partiels tant que leur validation n'est pas démontrée dans le contexte projet.",
            "citations": cites,
            "verrous": dedup_verrou_refs(as_list(fam.get("verrous"))),
            "writer_instruction": (
                "Utiliser cet axe pour organiser le paragraphe, mais ne pas écrire 'famille'. "
                "Introduire l'idée, puis citer quelques travaux représentatifs."
            ),
        })

    # Tri : d'abord concepts spécifiques, puis axes internes, en privilégiant les citations directes/couverture.
    def rank(item: Dict[str, Any]) -> Tuple[int, int, str]:
        internal = 1 if item.get("is_internal_grouping_axis") else 0
        return (internal, -len(item.get("citations") or []), clean_sentence(item.get("concept")).lower())

    return sorted(items, key=rank)[:40]


def build_concept_explanation_sentence(label: str, principle: str, role: str) -> str:
    if principle:
        return f"{label} peut être présenté comme un mécanisme visant à {role}. Son principe repose sur {truncate(principle, 450)}"
    return f"{label} doit être expliqué comme un mécanisme visant à {role}, en s'appuyant uniquement sur les citations disponibles."


def infer_contribution_sentences(label: str, principle: str, role: str) -> List[str]:
    out = [f"Contribue à {role}."]
    if principle:
        out.append("Fournit un mécanisme technique exploitable pour cadrer l'état de l'art, sans suffire à démontrer la levée du verrou.")
    return out


def synthesize_family_principle(label: str, methods: List[Dict[str, Any]]) -> str:
    names = unique_clean_list([method_label_from_any(m) for m in methods if method_label_from_any(m)], limit=6)
    principles = unique_clean_list([principle_from_any(m) for m in methods if principle_from_any(m)], limit=3)
    if principles:
        return f"Cet axe regroupe des approches telles que {', '.join(names[:4])}, qui cherchent à {role_for_family(label).lower()} Les mécanismes disponibles incluent notamment : {principles[0]}"
    if names:
        return f"Cet axe regroupe des approches telles que {', '.join(names[:4])}, mobilisées pour {role_for_family(label).lower()}"
    return f"Cet axe scientifique sert à {role_for_family(label).lower()}"


def build_conceptual_comparison_graph(
    comparison_graph: List[Dict[str, Any]],
    scientific_knowledge_base: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_citation: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_label: Dict[str, Dict[str, Any]] = {}
    for item in scientific_knowledge_base:
        by_label[normalize_concept_key(item.get("concept"))] = item
        for c in safe_citations(item.get("citations") or []):
            by_citation[c].append(item)

    out: List[Dict[str, Any]] = []
    seen = set()
    for comp in comparison_graph:
        # Ignorer les fausses comparaisons faibles entre familles avec score très bas.
        score = float(comp.get("comparability_score") or 0)
        if comp.get("scope") == "cross_family" and score < 1.0:
            continue
        a_cit = normalize_citation_label(comp.get("a"))
        b_cit = normalize_citation_label(comp.get("b"))
        a_label = clean_sentence(comp.get("a_label") or comp.get("family_a") or a_cit)
        b_label = clean_sentence(comp.get("b_label") or comp.get("family_b") or b_cit)
        a_item = (by_citation.get(a_cit) or [by_label.get(normalize_concept_key(a_label), {})])[0]
        b_item = (by_citation.get(b_cit) or [by_label.get(normalize_concept_key(b_label), {})])[0]
        ca = clean_sentence(a_item.get("concept") or a_label)
        cb = clean_sentence(b_item.get("concept") or b_label)
        if not ca or not cb or ca == cb:
            continue
        key = tuple(sorted([normalize_concept_key(ca), normalize_concept_key(cb)]))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "comparison_id": "concept_cmp_" + "_".join(key)[:90],
            "concept_a": ca,
            "concept_b": cb,
            "comparison_type": comp.get("comparison_type"),
            "comparability_score": score,
            "shared_dimension": infer_shared_dimension(a_item, b_item, comp),
            "how_to_write": build_comparison_instruction(ca, cb, a_item, b_item),
            "forbidden_claims": comp.get("forbidden_claims") or [],
            "citations": safe_citations((a_item.get("citations") or []) + (b_item.get("citations") or []), limit=4),
            "source_comparison_id": comp.get("comparison_id"),
        })
    return out[:10]


def infer_shared_dimension(a: Dict[str, Any], b: Dict[str, Any], comp: Dict[str, Any]) -> str:
    txt = " ".join([a.get("scientific_role", ""), b.get("scientific_role", ""), " ".join(comp.get("evidence_basis") or [])]).lower()
    if "transpos" in txt:
        return "transposition au contexte projet"
    if "limit" in txt or "robust" in txt:
        return "limites et robustesse"
    if "mechanism" in txt or "principle" in txt:
        return "mécanisme scientifique"
    return "objectif scientifique commun"


def build_comparison_instruction(ca: str, cb: str, a: Dict[str, Any], b: Dict[str, Any]) -> str:
    return (
        f"Comparer {ca} et {cb} comme deux idées scientifiques, pas comme deux résumés d'articles. "
        "Présenter ce que chaque mécanisme apporte au raisonnement, puis la limite commune qui empêche de conclure directement. "
        "Ne pas affirmer de supériorité sans métrique explicite."
    )


def build_consultant_style_progression(
    verrou_index: List[Dict[str, Any]],
    scientific_knowledge_base: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Construit une progression façon consultant : contexte -> données -> méthodes -> validation -> insuffisance."""
    # Les items sont classés sans domaine fixe, à partir de leur rôle scientifique.
    def stage_for_item(item: Dict[str, Any]) -> Tuple[int, str]:
        txt = " ".join([item.get("concept", ""), item.get("scientific_role", ""), item.get("technical_principle", "")]).lower()
        if any(k in txt for k in ["signal", "représent", "represent", "donnée", "data", "sparse"]):
            return (1, "cadrage_des_donnees_et_representations")
        if any(k in txt for k in ["classification", "apprentissage", "learning", "cnn", "réseau", "neural"]):
            return (2, "modeles_d_apprentissage")
        if any(k in txt for k in ["augmentation", "synthetic", "synth", "generation", "génération", "diffusion", "adversarial"]):
            return (3, "generation_et_augmentation")
        if any(k in txt for k in ["robust", "incertitude", "uncertainty", "corruption", "evaluation", "évaluation"]):
            return (4, "evaluation_robustesse_generalisation")
        return (2, "axe_scientifique")

    ordered = sorted(scientific_knowledge_base, key=lambda x: (stage_for_item(x)[0], x.get("is_internal_grouping_axis", False), clean_sentence(x.get("concept")).lower()))
    progression: List[Dict[str, Any]] = []
    used_concepts = set()
    for item in ordered:
        concept = clean_sentence(item.get("concept"))
        if not concept or concept in used_concepts:
            continue
        # Ne pas mettre trop d'axes internes dans la progression finale.
        if item.get("is_internal_grouping_axis") and len([p for p in progression if p.get("type") == "internal_axis"]) >= 3:
            continue
        st_num, st_label = stage_for_item(item)
        progression.append({
            "step": len(progression) + 1,
            "stage": st_label,
            "type": "internal_axis" if item.get("is_internal_grouping_axis") else "concept",
            "concept": concept,
            "role": item.get("scientific_role"),
            "explanation_focus": item.get("how_to_explain_in_text"),
            "citations": safe_citations(item.get("citations") or [], limit=3),
            "writer_transition": transition_for_stage(st_num),
        })
        used_concepts.add(concept)
        if len(progression) >= 12:
            break

    # Ajouter une étape finale de limites/gap.
    if shared_limitations:
        progression.append({
            "step": len(progression) + 1,
            "stage": "insuffisances_et_gap_rd",
            "type": "gap_synthesis",
            "concept": "limites de transposition et besoin de validation",
            "role": "montrer pourquoi les acquis de la littérature ne lèvent pas le verrou",
            "explanation_focus": "Relier les limites communes aux travaux expérimentaux nécessaires dans le contexte projet.",
            "citations": safe_citations(sum([as_list(l.get("citations")) for l in shared_limitations[:3]], []), limit=4),
            "writer_transition": "Ces acquis doivent ensuite être confrontés aux limites de représentativité, de robustesse et de validation propres au projet.",
        })
    return progression


def transition_for_stage(stage: int) -> str:
    if stage == 1:
        return "Le raisonnement doit d'abord cadrer les données, leur représentation et leur adéquation au phénomène étudié."
    if stage == 2:
        return "Une fois les données caractérisées, les modèles d'apprentissage peuvent être discutés comme moyens d'extraction et de classification."
    if stage == 3:
        return "La question devient ensuite celle de l'enrichissement ou de la génération des données pour compenser les limites des jeux disponibles."
    if stage == 4:
        return "La dernière étape consiste à interroger la robustesse, la généralisation et la validité expérimentale de ces approches."
    return "Cette étape sert de transition dans la progression scientifique."


def build_consultant_storyline(
    verrou_index: List[Dict[str, Any]],
    scientific_knowledge_base: List[Dict[str, Any]],
    conceptual_comparisons: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    titles = unique_clean_list([v.get("verrou_title") for v in verrou_index if v.get("verrou_title")])
    progression = build_consultant_style_progression(verrou_index, scientific_knowledge_base, shared_limitations)
    limitations = [clean_sentence(l.get("label")) for l in shared_limitations[:5] if clean_sentence(l.get("label"))]
    return {
        "storyline_type": "consultant_scientific_storyline_not_final_text",
        "goal": "Produire une histoire scientifique continue, pas un assemblage de résumés d'articles.",
        "verrous_to_defend": titles,
        "narrative_principle": (
            "Partir du problème scientifique et des données, introduire progressivement les mécanismes utiles, "
            "puis montrer pourquoi les limites de représentativité, de transposition et de validation maintiennent le verrou R&D."
        ),
        "progression": progression,
        "comparisons_as_ideas": conceptual_comparisons,
        "limitations_to_turn_into_argument": limitations,
        "opening_strategy": (
            "Commencer par situer le domaine et le besoin de validation. Ne pas commencer par 'A1' ou par une liste d'articles."
        ),
        "closing_strategy": (
            "Conclure sur l'écart entre les mécanismes disponibles dans l'état de l'art et les preuves attendues dans le contexte propre du projet."
        ),
        "style_reference_rules": [
            "Les concepts doivent être expliqués naturellement dans le texte, pas définis sous forme de glossaire.",
            "Les familles ne doivent pas apparaître comme des catégories visibles ; elles servent uniquement à organiser la progression.",
            "Les citations doivent appuyer des affirmations, pas structurer les paragraphes.",
            "Éviter de répéter le même mécanisme dans plusieurs sections ; utiliser ensuite 'ces approches', 'ces mécanismes', 'ces stratégies'.",
        ],
    }


def build_phase5_consultant_blueprint_v2(
    verrou_index: List[Dict[str, Any]],
    scientific_knowledge_base: List[Dict[str, Any]],
    consultant_storyline: Dict[str, Any],
    conceptual_comparisons: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    project_need = ""
    project_gap = ""
    if verrou_index:
        aj = verrou_index[0].get("argumentation_json") or {}
        project_need = clean_sentence(aj.get("project_need") or aj.get("technical_need") or aj.get("need"))
        project_gap = clean_sentence(aj.get("rd_gap") or aj.get("gap"))
        sections = verrou_index[0].get("project_sections") or {}
        project_need = project_need or clean_sentence(sections.get("section_1_project_need"))
        project_gap = project_gap or clean_sentence(sections.get("section_5_gap_rd"))

    return {
        "blueprint_type": "phase5_consultant_writer_blueprint_v2_concept_first",
        "core_change": "Phase 5 doit écrire à partir des connaissances/concepts, pas à partir des articles.",
        "project_need": project_need,
        "project_gap": project_gap,
        "visible_section_order": [
            "cadrage_scientifique_du_domaine",
            "donnees_et_representativite",
            "methodes_d_apprentissage_et_generation",
            "robustesse_validation_et_generalisation",
            "insuffisances_de_l_etat_de_l_art",
            "gap_rd_et_verrou_maintenu",
        ],
        "knowledge_to_use_in_order": [
            {
                "concept": p.get("concept"),
                "stage": p.get("stage"),
                "explanation_focus": p.get("explanation_focus"),
                "citations": p.get("citations") or [],
            }
            for p in consultant_storyline.get("progression") or []
        ],
        "conceptual_comparisons_to_integrate": conceptual_comparisons[:6],
        "limitations_to_integrate": [
            {
                "label": l.get("label"),
                "impact": unique_clean_list(l.get("impacts") or [], limit=2),
                "citations": safe_citations(l.get("citations") or [], limit=3),
            }
            for l in shared_limitations[:6]
        ],
        "do_not_write": [
            "Ne pas écrire 'famille augmentation de données...' comme titre visible.",
            "Ne pas écrire 'La narration scientifique doit...'.",
            "Ne pas écrire 'cet article présente...' pour chaque source.",
            "Ne pas répéter 'ne suffit pas à démontrer' dans chaque phrase.",
            "Ne pas regrouper plus de 3 citations dans une phrase.",
        ],
        "write_like_consultant": [
            "Construire une progression : domaine → données → modèles → génération/augmentation → validation → insuffisances.",
            "Expliquer les mots-clés par leur mécanisme et leur rôle, sans format glossaire.",
            "Utiliser les articles comme preuves ponctuelles, jamais comme plan.",
            "Faire apparaître le verrou comme conséquence logique des limites de l'état de l'art.",
        ],
    }


def build_markdown_summary(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Phase 4.7 — Scientific Knowledge & Narrative Builder V2")
    lines.append("")
    lines.append(f"Payload: `{payload.get('payload_type')}`")
    lines.append(f"OK: `{payload.get('ok')}`")
    q = payload.get("quality") or {}
    lines.append(f"Quality: `{q.get('score')}` / `{q.get('level')}`")
    lines.append("")

    story = payload.get("consultant_storyline") or {}
    lines.append("## Consultant storyline")
    lines.append(clean_sentence(story.get("narrative_principle")) or "Non disponible")
    lines.append("")
    lines.append("### Progression à donner à Phase 5")
    for p in story.get("progression") or []:
        cites = ", ".join(p.get("citations") or [])
        lines.append(f"- **{p.get('stage')}** — {p.get('concept')} — citations: {cites}")
    lines.append("")

    lines.append("## Scientific knowledge base")
    for item in (payload.get("scientific_knowledge_base") or [])[:12]:
        cites = ", ".join(item.get("citations") or [])
        internal = " *(axe interne)*" if item.get("is_internal_grouping_axis") else ""
        lines.append(f"- **{item.get('concept')}**{internal} — rôle: {item.get('scientific_role')} — citations: {cites}")
    lines.append("")

    lines.append("## Comparaisons conceptuelles")
    for c in (payload.get("conceptual_comparison_graph") or [])[:8]:
        lines.append(f"- **{c.get('concept_a')}** vs **{c.get('concept_b')}** — {c.get('shared_dimension')} — citations: {', '.join(c.get('citations') or [])}")
    lines.append("")

    lines.append("## Limites à transformer en gap")
    for l in (payload.get("shared_limitations") or [])[:8]:
        lines.append(f"- **{l.get('label')}** — citations: {', '.join(l.get('citations') or [])}")
    lines.append("")

    bp = payload.get("phase5_consultant_blueprint") or {}
    lines.append("## Phase 5 consultant blueprint")
    lines.append("Sections visibles recommandées:")
    for s in bp.get("visible_section_order") or []:
        lines.append(f"- {s}")
    lines.append("")
    lines.append("Règles importantes:")
    for r in bp.get("write_like_consultant") or []:
        lines.append(f"- {r}")
    lines.append("")

    if q.get("warnings"):
        lines.append("## Warnings")
        for w in q.get("warnings") or []:
            lines.append(f"- {w}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def compute_quality(payload: Dict[str, Any]) -> Dict[str, Any]:
    warnings: List[str] = []
    score = 100.0
    if not payload.get("scientific_knowledge_base"):
        score -= 24
        warnings.append("scientific_knowledge_base vide")
    if not payload.get("consultant_storyline", {}).get("progression"):
        score -= 18
        warnings.append("consultant_storyline.progression vide")
    if not payload.get("phase5_consultant_blueprint"):
        score -= 16
        warnings.append("phase5_consultant_blueprint manquant")
    if not payload.get("conceptual_comparison_graph"):
        score -= 6
        warnings.append("conceptual_comparison_graph vide ou faible")
    if not payload.get("shared_limitations"):
        score -= 12
        warnings.append("shared_limitations vide")
    if not payload.get("concept_graph"):
        score -= 10
        warnings.append("concept_graph vide")
    if len(payload.get("verrou_index") or []) <= 1:
        warnings.append("Un seul verrou : la logique multi-verrous sera surtout visible sur un dossier plus large.")
    score = max(0.0, min(100.0, score))
    level = "good" if score >= 80 else "medium" if score >= 60 else "weak"
    return {"score": round(score, 2), "level": level, "warnings": warnings}



# ============================================================
# V2.1 — Macro-concept knowledge base, sans hardcoding domaine
# ============================================================

GENERIC_STAGE_ORDER = [
    "cadrage_scientifique_du_domaine",
    "donnees_et_representativite",
    "methodes_modeles_et_apprentissage",
    "generation_augmentation_et_transformation",
    "robustesse_validation_et_generalisation",
    "insuffisances_et_gap_rd",
]


def merge_text_for_classification(*values: Any) -> str:
    return " ".join(clean_sentence(v) for v in values if clean_sentence(v)).lower()


def evidence_semantic_profile(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Profil sémantique générique.
    Important : aucun concept métier spécifique n'est codé ici.
    On classe seulement par fonctions scientifiques générales : données, modèles,
    génération/augmentation, validation/robustesse, gap.
    """
    txt = merge_text_for_classification(
        item.get("concept_label") or item.get("concept") or item.get("family_label"),
        item.get("definition"),
        item.get("technical_principle"),
        item.get("scientific_role"),
        item.get("internal_family"),
        item.get("families"),
        item.get("principles"),
        item.get("mechanisms"),
        item.get("limits"),
    )

    buckets = {
        "domain": [
            "domaine", "contexte", "probl", "phénom", "phenom", "système", "systeme", "application",
            "objectif", "besoin", "technical need", "scientific context",
        ],
        "data": [
            "donnée", "donnee", "data", "dataset", "base", "échantillon", "echantillon", "représent", "represent",
            "signal", "image", "feature", "caractéristique", "caracteristique", "mesure", "simulation", "synthet",
            "distribution", "variabilité", "variabilite",
        ],
        "model": [
            "modèle", "modele", "model", "classification", "reconnaissance", "prediction", "prédiction",
            "apprentissage", "learning", "neur", "réseau", "reseau", "architecture", "entrainement", "training",
        ],
        "generation": [
            "augmentation", "génération", "generation", "synthetic", "synthétique", "synthetique", "transformation",
            "variant", "advers", "diffusion", "masque", "mask", "occlusion", "perturb", "rendu", "render",
        ],
        "validation": [
            "validation", "évaluation", "evaluation", "robust", "général", "general", "incertitude",
            "uncertainty", "métrique", "metrique", "benchmark", "protocole", "test", "performance", "corruption",
        ],
        "gap": [
            "limite", "insuffis", "gap", "verrou", "transposition", "représentativité", "representativite",
            "non résolu", "non resolu", "preuve", "démonstration", "demonstration", "incertitude résiduelle",
        ],
    }
    scores = {k: 0 for k in buckets}
    for stage, keys in buckets.items():
        for k in keys:
            if k in txt:
                scores[stage] += 1
    # Heuristique générique : l'évaluation/gap doit primer si des limites sont présentes.
    if item.get("limitations") or item.get("limits"):
        scores["validation"] += 1
    return {"text": txt, "scores": scores}


def generic_stage_for_evidence(item: Dict[str, Any]) -> str:
    profile = evidence_semantic_profile(item)
    scores = profile["scores"]
    # Ordre de priorité narratif, pas domaine.
    if scores["gap"] >= 3:
        return "insuffisances_et_gap_rd"
    if scores["validation"] >= 2:
        return "robustesse_validation_et_generalisation"
    if scores["generation"] >= 2:
        return "generation_augmentation_et_transformation"
    if scores["model"] >= 2:
        return "methodes_modeles_et_apprentissage"
    if scores["data"] >= 2:
        return "donnees_et_representativite"
    return "cadrage_scientifique_du_domaine"


def stage_title_generic(stage: str) -> str:
    mapping = {
        "cadrage_scientifique_du_domaine": "cadrage scientifique du domaine",
        "donnees_et_representativite": "données, représentativité et conditions d'observation",
        "methodes_modeles_et_apprentissage": "méthodes d'apprentissage et modèles",
        "generation_augmentation_et_transformation": "génération, augmentation et transformation des données",
        "robustesse_validation_et_generalisation": "robustesse, validation et généralisation",
        "insuffisances_et_gap_rd": "insuffisances de l'état de l'art et gap R&D",
    }
    return mapping.get(stage, stage.replace("_", " "))


def generic_stage_goal(stage: str) -> str:
    mapping = {
        "cadrage_scientifique_du_domaine": "situer le problème scientifique avant d'introduire les travaux existants",
        "donnees_et_representativite": "expliquer pourquoi la qualité, la diversité et la représentativité des données conditionnent la validité des méthodes",
        "methodes_modeles_et_apprentissage": "présenter les modèles comme des moyens d'extraire ou d'apprendre des représentations utiles",
        "generation_augmentation_et_transformation": "montrer comment les données peuvent être enrichies, transformées ou générées pour couvrir davantage de cas",
        "robustesse_validation_et_generalisation": "discuter les conditions dans lesquelles les résultats restent fiables hors du contexte d'entraînement",
        "insuffisances_et_gap_rd": "transformer les limites de la littérature en incertitude scientifique résiduelle justifiant les travaux R&D",
    }
    return mapping.get(stage, "organiser une étape de la narration scientifique")


def generic_transition_for_stage(stage: str) -> str:
    mapping = {
        "cadrage_scientifique_du_domaine": "Le texte doit d'abord expliquer le problème scientifique et le besoin auquel répond le projet.",
        "donnees_et_representativite": "La discussion doit ensuite montrer que les données ne sont pas de simples entrées, mais une condition de validité scientifique.",
        "methodes_modeles_et_apprentissage": "Une fois les données cadrées, les modèles peuvent être introduits comme mécanismes d'apprentissage ou de classification.",
        "generation_augmentation_et_transformation": "Lorsque les données disponibles sont insuffisantes, les approches de génération ou d'augmentation deviennent un axe de réponse possible.",
        "robustesse_validation_et_generalisation": "Ces mécanismes doivent ensuite être évalués selon leur robustesse, leur généralisation et leur protocole de validation.",
        "insuffisances_et_gap_rd": "La conclusion doit faire apparaître l'écart entre ce que l'état de l'art rend possible et ce que le projet doit encore démontrer.",
    }
    return mapping.get(stage, "Cette étape sert de transition dans le raisonnement scientifique.")


def macro_label_from_stage_and_evidence(stage: str, evidences: List[Dict[str, Any]]) -> str:
    """Libellé générique, sans noms de méthodes ni noms d'articles."""
    return stage_title_generic(stage)


def evidence_is_method_level(item: Dict[str, Any]) -> bool:
    """Détecte si un item ressemble à une méthode/article spécifique plutôt qu'à une idée macro."""
    label = clean_sentence(item.get("concept_label") or item.get("concept"))
    if not label:
        return False
    if item.get("is_internal_grouping_axis"):
        return False
    words = label.split()
    # Court + majuscules / noms propres = probablement méthode spécifique.
    has_upper = any(ch.isupper() for ch in label)
    return len(words) <= 5 and has_upper


def build_macro_scientific_knowledge_base(
    verrou_index: List[Dict[str, Any]],
    family_graph: List[Dict[str, Any]],
    concept_graph: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    V2.1 : produit des macro-connaissances consultant-level.
    Les articles/méthodes deviennent des preuves et exemples, jamais des unités narratives principales.
    """
    # Sources exploitables : concepts + familles + limites.
    evidence_items: List[Dict[str, Any]] = []

    for c in concept_graph:
        label = clean_sentence(c.get("concept_label"))
        if not label:
            continue
        family = family_for_concept(c, family_graph)
        item = {
            "type": "concept_evidence",
            "label": label,
            "concept_label": label,
            "family": family,
            "principle": principle_from_any(c),
            "definition": c.get("definition"),
            "citations": safe_citations(c.get("citations") or []),
            "limitations": extract_clean_limitations_for_concept(c, shared_limitations),
            "verrous": dedup_verrou_refs(as_list(c.get("verrous"))),
            "usage_types": unique_clean_list(c.get("usage_types") or [], limit=5),
            "is_method_level": evidence_is_method_level({"concept": label}),
        }
        item["stage"] = generic_stage_for_evidence({
            "concept": label,
            "internal_family": family,
            "definition": item["definition"],
            "technical_principle": item["principle"],
            "limitations": item["limitations"],
        })
        evidence_items.append(item)

    for f in family_graph:
        label = clean_sentence(f.get("family_label"))
        if not label:
            continue
        methods = [m for m in as_list(f.get("methods")) if isinstance(m, dict)]
        item = {
            "type": "family_axis_evidence",
            "label": label,
            "concept_label": label,
            "family": label,
            "principle": synthesize_family_principle(label, methods),
            "citations": safe_citations(f.get("citations") or []),
            "limitations": [],
            "verrous": dedup_verrou_refs(as_list(f.get("verrous"))),
            "supporting_methods": unique_clean_list([method_label_from_any(m) for m in methods if method_label_from_any(m)], limit=8),
            "is_method_level": False,
        }
        item["stage"] = generic_stage_for_evidence({
            "concept": label,
            "technical_principle": item["principle"],
            "scientific_role": role_for_family(label),
        })
        evidence_items.append(item)

    # Les limites alimentent surtout la dernière partie.
    for l in shared_limitations:
        label = clean_sentence(l.get("label"))
        if not label:
            continue
        evidence_items.append({
            "type": "limitation_evidence",
            "label": label,
            "concept_label": label,
            "family": "",
            "principle": " ; ".join(unique_clean_list(l.get("impacts") or l.get("causes") or [], limit=2)),
            "citations": safe_citations(l.get("citations") or []),
            "limitations": [label] + unique_clean_list(l.get("impacts") or [], limit=3),
            "verrous": dedup_verrou_refs(as_list(l.get("verrous"))),
            "stage": "insuffisances_et_gap_rd" if "gap" in merge_text_for_classification(label, l.get("impacts")) else "robustesse_validation_et_generalisation",
            "is_method_level": False,
        })

    buckets: Dict[str, Dict[str, Any]] = {}
    for e in evidence_items:
        stage = e.get("stage") or "cadrage_scientifique_du_domaine"
        bucket = buckets.setdefault(stage, {
            "knowledge_id": "macro_" + fs_slug(stage),
            "macro_concept": macro_label_from_stage_and_evidence(stage, []),
            "stage": stage,
            "visible_title_suggestion": stage_title_generic(stage),
            "scientific_role": generic_stage_goal(stage),
            "evidence_items": [],
            "supporting_concepts": [],
            "supporting_citations": [],
            "supporting_families_internal": [],
            "limitations": [],
            "verrous": [],
        })
        bucket["evidence_items"].append(e)
        if e.get("type") != "family_axis_evidence":
            bucket["supporting_concepts"].append(e.get("label"))
        if e.get("family"):
            bucket["supporting_families_internal"].append(e.get("family"))
        bucket["supporting_citations"] += safe_citations(e.get("citations") or [])
        bucket["limitations"] += unique_clean_list(e.get("limitations") or [], limit=6)
        bucket["verrous"] += dedup_verrou_refs(as_list(e.get("verrous")))

    macro_items: List[Dict[str, Any]] = []
    for stage in GENERIC_STAGE_ORDER:
        if stage not in buckets:
            continue
        b = buckets[stage]
        evidences = b.pop("evidence_items")
        concepts = unique_clean_list(b.get("supporting_concepts") or [], limit=10)
        families = unique_clean_list(b.get("supporting_families_internal") or [], limit=6)
        citations = safe_citations(b.get("supporting_citations") or [], limit=10)
        limitations = unique_clean_list(b.get("limitations") or [], limit=8)
        verrous = dedup_verrou_refs(as_list(b.get("verrous")))

        # Synthèse des principes sans citer chaque article.
        principles = unique_clean_list([e.get("principle") for e in evidences if clean_sentence(e.get("principle"))], limit=4)
        representative_examples = [
            {
                "label": e.get("label"),
                "citations": safe_citations(e.get("citations") or [], limit=3),
                "why_used": truncate(e.get("principle"), 350),
            }
            for e in evidences
            if e.get("label") and e.get("type") in {"concept_evidence", "family_axis_evidence"}
        ][:8]

        b.update({
            "macro_concept": macro_label_from_stage_and_evidence(stage, evidences),
            "stage": stage,
            "visible_title_suggestion": stage_title_generic(stage),
            "scientific_role": generic_stage_goal(stage),
            "technical_principle_synthesis": build_macro_principle_synthesis(stage, principles, concepts),
            "how_to_explain_in_text": build_macro_explanation(stage, principles, concepts),
            "supporting_concepts": concepts,
            "supporting_citations": citations,
            "supporting_families_internal": families,
            "representative_evidence": representative_examples,
            "limitations": limitations,
            "verrous": verrous,
            "writer_instruction": (
                "Écrire un paragraphe de raisonnement scientifique continu. "
                "Ne pas écrire les noms des familles comme catégories visibles. "
                "Ne pas résumer les articles un par un ; utiliser les citations comme appuis ponctuels."
            ),
            "anti_repetition_instruction": (
                "Une fois les mécanismes introduits, utiliser des reprises comme 'ces approches', "
                "'ces mécanismes' ou 'ces stratégies' au lieu de répéter les noms."
            ),
        })
        macro_items.append(b)

    # Toujours ajouter un item gap si des limites existent et non déjà couvert.
    if shared_limitations and not any(x.get("stage") == "insuffisances_et_gap_rd" for x in macro_items):
        cites = safe_citations(sum([as_list(l.get("citations")) for l in shared_limitations], []), limit=8)
        macro_items.append({
            "knowledge_id": "macro_insuffisances_et_gap_rd",
            "macro_concept": stage_title_generic("insuffisances_et_gap_rd"),
            "stage": "insuffisances_et_gap_rd",
            "visible_title_suggestion": stage_title_generic("insuffisances_et_gap_rd"),
            "scientific_role": generic_stage_goal("insuffisances_et_gap_rd"),
            "technical_principle_synthesis": "Les limites de représentativité, de transposition, de validation ou de généralisation doivent être transformées en argument R&D.",
            "how_to_explain_in_text": "Le texte doit montrer que les acquis de la littérature cadrent le problème, mais ne suffisent pas à démontrer la validité dans le contexte propre du projet.",
            "supporting_concepts": unique_clean_list([l.get("label") for l in shared_limitations if l.get("label")], limit=8),
            "supporting_citations": cites,
            "supporting_families_internal": [],
            "representative_evidence": [],
            "limitations": unique_clean_list([l.get("label") for l in shared_limitations if l.get("label")], limit=8),
            "verrous": dedup_verrou_refs(sum([as_list(l.get("verrous")) for l in shared_limitations], [])),
            "writer_instruction": "Conclure par une démonstration du verrou restant, pas par une liste de limites.",
            "anti_repetition_instruction": "Formuler une seule fois le gap global, puis rattacher les preuves nécessaires.",
        })

    return sorted(macro_items, key=lambda x: GENERIC_STAGE_ORDER.index(x.get("stage")) if x.get("stage") in GENERIC_STAGE_ORDER else 99)


def build_macro_principle_synthesis(stage: str, principles: List[str], concepts: List[str]) -> str:
    goal = generic_stage_goal(stage)
    if principles:
        return f"Cette étape vise à {goal}. Les mécanismes identifiés indiquent notamment que {truncate(' ; '.join(principles[:2]), 650)}"
    if concepts:
        return f"Cette étape vise à {goal}. Elle s'appuie sur plusieurs notions ou méthodes sélectionnées comme preuves ponctuelles."
    return f"Cette étape vise à {goal}."


def build_macro_explanation(stage: str, principles: List[str], concepts: List[str]) -> str:
    title = stage_title_generic(stage)
    if principles:
        return f"La section « {title} » doit expliquer le rôle scientifique de ces mécanismes avant de citer les sources. Elle peut s'appuyer sur : {truncate(' ; '.join(principles[:3]), 700)}"
    if concepts:
        return f"La section « {title} » doit regrouper les notions suivantes dans un raisonnement continu : {', '.join(concepts[:6])}."
    return f"La section « {title} » doit être rédigée comme une transition scientifique vers le gap R&D."


def build_conceptual_comparison_graph_v21(
    comparison_graph: List[Dict[str, Any]],
    macro_knowledge_base: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Compare les idées/stages, pas seulement les méthodes."""
    stage_by_concept: Dict[str, str] = {}
    for macro in macro_knowledge_base:
        for c in as_list(macro.get("supporting_concepts")):
            stage_by_concept[normalize_concept_key(c)] = macro.get("stage")

    out: List[Dict[str, Any]] = []
    seen = set()
    for comp in comparison_graph:
        a_label = clean_sentence(comp.get("a_label") or comp.get("family_a") or comp.get("a"))
        b_label = clean_sentence(comp.get("b_label") or comp.get("family_b") or comp.get("b"))
        if not a_label or not b_label:
            continue
        stage_a = stage_by_concept.get(normalize_concept_key(a_label), "")
        stage_b = stage_by_concept.get(normalize_concept_key(b_label), "")
        if stage_a and stage_b and stage_a == stage_b:
            comparison_level = "intra_axis_comparison"
            macro_scope = stage_title_generic(stage_a)
        elif stage_a or stage_b:
            comparison_level = "cross_axis_comparison"
            macro_scope = "transition entre " + " et ".join([stage_title_generic(s) for s in [stage_a, stage_b] if s])
        else:
            comparison_level = "conceptual_comparison"
            macro_scope = "transposition au contexte projet"
        key = tuple(sorted([normalize_concept_key(a_label), normalize_concept_key(b_label), macro_scope]))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "comparison_id": "macro_cmp_" + fs_slug("_".join(key))[:90],
            "comparison_level": comparison_level,
            "concept_a": a_label,
            "concept_b": b_label,
            "macro_scope": macro_scope,
            "shared_dimension": "conditions de transposition, validation ou représentativité",
            "how_to_write": (
                f"Comparer {a_label} et {b_label} comme deux idées utiles au raisonnement, non comme deux fiches articles. "
                "La comparaison doit servir à expliquer ce que chaque mécanisme apporte et ce qui reste à valider dans le contexte projet."
            ),
            "forbidden_claims": comp.get("forbidden_claims") or [
                "Ne pas affirmer de supériorité sans métrique explicite.",
                "Ne pas transformer une citation connexe en preuve directe.",
                "Ne pas construire une chronologie non sourcée.",
            ],
            "citations": safe_citations([comp.get("a"), comp.get("b")] + as_list(comp.get("citations")), limit=4),
            "source_comparison_id": comp.get("comparison_id"),
        })
        if len(out) >= 8:
            break
    return out


def build_consultant_storyline_v21(
    verrou_index: List[Dict[str, Any]],
    macro_knowledge_base: List[Dict[str, Any]],
    conceptual_comparisons: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    titles = unique_clean_list([v.get("verrou_title") for v in verrou_index if v.get("verrou_title")])
    progression: List[Dict[str, Any]] = []
    for idx, item in enumerate(macro_knowledge_base, 1):
        progression.append({
            "step": idx,
            "stage": item.get("stage"),
            "macro_concept": item.get("macro_concept"),
            "visible_title_suggestion": item.get("visible_title_suggestion"),
            "role": item.get("scientific_role"),
            "explanation_focus": item.get("how_to_explain_in_text"),
            "supporting_concepts_internal": item.get("supporting_concepts") or [],
            "citations": safe_citations(item.get("supporting_citations") or [], limit=4),
            "writer_transition": generic_transition_for_stage(item.get("stage")),
            "do_not_write_as_list": True,
        })
    limitations = [clean_sentence(l.get("label")) for l in shared_limitations[:6] if clean_sentence(l.get("label"))]
    return {
        "storyline_type": "consultant_scientific_storyline_v2_1_macro_concepts_not_final_text",
        "goal": "Produire une histoire scientifique continue à partir de macro-concepts, pas d'une liste d'articles ni d'une liste de méthodes.",
        "verrous_to_defend": titles,
        "narrative_principle": (
            "Partir du problème scientifique, expliquer les données et les conditions de représentativité, "
            "introduire les modèles et les mécanismes de génération ou d'augmentation, puis démontrer pourquoi "
            "les limites de validation et de généralisation maintiennent un gap R&D."
        ),
        "progression": progression,
        "comparisons_as_ideas": conceptual_comparisons,
        "limitations_to_turn_into_argument": limitations,
        "opening_strategy": "Commencer par le domaine, le besoin de validation et les données ; ne jamais commencer par une citation ou un article.",
        "closing_strategy": "Finir sur l'écart entre les acquis de l'état de l'art et les preuves expérimentales attendues dans le contexte du projet.",
        "style_reference_rules": [
            "Les familles sont internes et ne doivent pas apparaître comme titres visibles.",
            "Les méthodes spécifiques doivent être citées comme exemples ou preuves ponctuelles, pas comme plan de rédaction.",
            "Les concepts doivent être expliqués par leur mécanisme, leur rôle et leur limite de transposition.",
            "Éviter la répétition : après une première explication, employer 'ces approches', 'ces mécanismes' ou 'ces stratégies'.",
            "Ne pas écrire un paragraphe par article.",
        ],
    }


def extract_project_need_and_gap(verrou_index: List[Dict[str, Any]]) -> Tuple[str, str]:
    needs: List[str] = []
    gaps: List[str] = []
    for v in verrou_index:
        aj = v.get("argumentation_json") or {}
        sections = v.get("project_sections") or {}
        for key in ["project_need", "technical_need", "need", "project_context", "objective", "objectif"]:
            if clean_sentence(aj.get(key)):
                needs.append(clean_sentence(aj.get(key)))
        for key in ["section_1_project_need", "positionnement_scientifique_du_verrou", "section_2_project_context"]:
            if clean_sentence(sections.get(key)):
                needs.append(clean_sentence(sections.get(key)))
        for key in ["rd_gap", "gap", "project_gap", "remaining_gap"]:
            if clean_sentence(aj.get(key)):
                gaps.append(clean_sentence(aj.get(key)))
        for key in ["section_5_gap_rd", "gap_scientifique_technique_justifiant_les_travaux_rd", "section_6_gap"]:
            if clean_sentence(sections.get(key)):
                gaps.append(clean_sentence(sections.get(key)))
    return truncate(" ".join(unique_clean_list(needs, limit=3)), 1200), truncate(" ".join(unique_clean_list(gaps, limit=3)), 1200)


def build_phase5_consultant_blueprint_v21(
    verrou_index: List[Dict[str, Any]],
    macro_knowledge_base: List[Dict[str, Any]],
    consultant_storyline: Dict[str, Any],
    conceptual_comparisons: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    project_need, project_gap = extract_project_need_and_gap(verrou_index)
    visible_order = [x.get("stage") for x in macro_knowledge_base if x.get("stage")]
    visible_order = [s for s in GENERIC_STAGE_ORDER if s in visible_order]
    return {
        "blueprint_type": "phase5_consultant_writer_blueprint_v2_1_macro_concept_first",
        "core_change": "Phase 5 doit écrire à partir de macro-concepts scientifiques et utiliser les articles comme preuves ponctuelles.",
        "project_need": project_need,
        "project_gap": project_gap,
        "visible_section_order": visible_order,
        "section_guidance": [
            {
                "section_id": item.get("stage"),
                "title_suggestion": item.get("visible_title_suggestion"),
                "goal": item.get("scientific_role"),
                "must_explain": item.get("technical_principle_synthesis"),
                "supporting_concepts_internal": item.get("supporting_concepts") or [],
                "supporting_citations": safe_citations(item.get("supporting_citations") or [], limit=5),
                "avoid": [
                    "ne pas afficher le nom des familles internes",
                    "ne pas faire une sous-section par article",
                    "ne pas répéter les mêmes mécanismes dans les sections suivantes",
                ],
            }
            for item in macro_knowledge_base
        ],
        "conceptual_comparisons_to_integrate": conceptual_comparisons[:6],
        "limitations_to_integrate": [
            {
                "label": l.get("label"),
                "impact": unique_clean_list(l.get("impacts") or [], limit=2),
                "citations": safe_citations(l.get("citations") or [], limit=3),
            }
            for l in shared_limitations[:6]
        ],
        "do_not_write": [
            "Ne pas écrire les familles comme des catégories visibles.",
            "Ne pas écrire 'La narration scientifique doit...'.",
            "Ne pas écrire 'cet article présente...' pour chaque source.",
            "Ne pas écrire un paragraphe par article ou par méthode.",
            "Ne pas regrouper plus de 3 citations dans une phrase.",
            "Ne pas affirmer de performance supérieure sans métrique explicite.",
        ],
        "write_like_consultant": [
            "Construire une progression : domaine → données → modèles → génération/augmentation → validation → insuffisances.",
            "Expliquer les mots-clés par leur mécanisme et leur rôle, sans format glossaire.",
            "Utiliser les articles comme preuves ponctuelles, jamais comme plan.",
            "Faire apparaître le verrou comme conséquence logique des limites de l'état de l'art.",
            "Réduire la répétition en utilisant des reprises anaphoriques après la première explication.",
        ],
    }


def build_markdown_summary(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Phase 4.7 — Scientific Knowledge & Narrative Builder V2.1")
    lines.append("")
    lines.append(f"Payload: `{payload.get('payload_type')}`")
    lines.append(f"OK: `{payload.get('ok')}`")
    q = payload.get("quality") or {}
    lines.append(f"Quality: `{q.get('score')}` / `{q.get('level')}`")
    lines.append("")

    story = payload.get("consultant_storyline") or {}
    lines.append("## Consultant storyline")
    lines.append(clean_sentence(story.get("narrative_principle")) or "Non disponible")
    lines.append("")
    lines.append("### Progression macro à donner à Phase 5")
    for p in story.get("progression") or []:
        cites = ", ".join(p.get("citations") or [])
        lines.append(f"- **{p.get('stage')}** — {p.get('macro_concept')} — citations: {cites}")
    lines.append("")

    lines.append("## Macro scientific knowledge base")
    for item in (payload.get("macro_scientific_knowledge_base") or payload.get("scientific_knowledge_base") or [])[:12]:
        cites = ", ".join(item.get("supporting_citations") or item.get("citations") or [])
        concepts = ", ".join((item.get("supporting_concepts") or [])[:5])
        lines.append(f"- **{item.get('macro_concept') or item.get('concept')}** — rôle: {item.get('scientific_role')} — concepts internes: {concepts} — citations: {cites}")
    lines.append("")

    lines.append("## Comparaisons conceptuelles")
    for c in (payload.get("conceptual_comparison_graph") or [])[:8]:
        lines.append(f"- **{c.get('concept_a')}** vs **{c.get('concept_b')}** — {c.get('macro_scope') or c.get('shared_dimension')} — citations: {', '.join(c.get('citations') or [])}")
    lines.append("")

    lines.append("## Limites à transformer en gap")
    for l in (payload.get("shared_limitations") or [])[:8]:
        lines.append(f"- **{l.get('label')}** — citations: {', '.join(l.get('citations') or [])}")
    lines.append("")

    bp = payload.get("phase5_consultant_blueprint") or {}
    lines.append("## Phase 5 consultant blueprint")
    lines.append("Sections visibles recommandées:")
    for s in bp.get("visible_section_order") or []:
        lines.append(f"- {s}")
    lines.append("")
    lines.append("Règles importantes:")
    for r in bp.get("write_like_consultant") or []:
        lines.append(f"- {r}")
    lines.append("")

    if q.get("warnings"):
        lines.append("## Warnings")
        for w in q.get("warnings") or []:
            lines.append(f"- {w}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def compute_quality(payload: Dict[str, Any]) -> Dict[str, Any]:
    warnings: List[str] = []
    score = 100.0
    primary = payload.get("macro_scientific_knowledge_base") or payload.get("scientific_knowledge_base")
    if not primary:
        score -= 26
        warnings.append("macro_scientific_knowledge_base vide")
    if not payload.get("consultant_storyline", {}).get("progression"):
        score -= 18
        warnings.append("consultant_storyline.progression vide")
    if not payload.get("phase5_consultant_blueprint"):
        score -= 16
        warnings.append("phase5_consultant_blueprint manquant")
    if not payload.get("shared_limitations"):
        score -= 12
        warnings.append("shared_limitations vide")
    if not payload.get("concept_graph"):
        score -= 6
        warnings.append("concept_graph vide")
    # Warning non bloquant : certains macro-concepts peuvent manquer selon les articles.
    stages = {x.get("stage") for x in (primary or [])}
    if len(stages) < 3:
        score -= 6
        warnings.append("progression macro courte : vérifier si les articles couvrent assez le domaine")
    if len(payload.get("verrou_index") or []) <= 1:
        warnings.append("Un seul verrou : la logique multi-verrous sera surtout visible sur un dossier plus large.")
    score = max(0.0, min(100.0, score))
    level = "good" if score >= 80 else "medium" if score >= 60 else "weak"
    return {"score": round(score, 2), "level": level, "warnings": warnings}


# ============================================================
# Main runner
# ============================================================

def build_scientific_narrative_payload(
    *,
    organisme: str,
    project: str,
    year: str,
    phase_4_5_path: Optional[str] = None,
    phase_4_6_path: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    p45_path = Path(phase_4_5_path) if phase_4_5_path else default_phase_4_5_path(organisme, project, year)
    p46_path = Path(phase_4_6_path) if phase_4_6_path else default_phase_4_6_path(organisme, project, year)

    phase45 = read_json(p45_path, {}) or {}
    phase46 = read_json(p46_path, {}) or {}

    verrou_index = build_verrou_index(phase45, phase46)
    if not verrou_index:
        return {
            "ok": False,
            "phase": "phase_4_7_scientific_narrative_builder",
            "payload_type": OUTPUT_PAYLOAD_TYPE,
            "generated_at": now_iso(),
            "organisme": organisme,
            "project": project,
            "year": year,
            "error": "Aucun verrou trouvé dans Phase 4.5 ou Phase 4.6.",
            "input_paths": {"phase_4_5": str(p45_path), "phase_4_6": str(p46_path)},
        }

    family_graph = build_family_graph(verrou_index)
    concept_graph = build_concept_graph(verrou_index, family_graph)
    comparison_graph = build_comparison_graph(verrou_index, family_graph)
    shared_limitations = build_shared_limitations(verrou_index)
    scientific_consensus = build_scientific_consensus(family_graph, shared_limitations, concept_graph)
    scientific_contradictions = build_scientific_contradictions(comparison_graph, shared_limitations)
    cross_verrou_reasoning = build_cross_verrou_reasoning(verrou_index, family_graph, shared_limitations)
    scientific_progression = build_scientific_progression(family_graph, concept_graph, comparison_graph)
    remaining_unknowns = build_remaining_unknowns(verrou_index, shared_limitations)
    scientific_story = build_scientific_story(
        verrou_index,
        family_graph,
        concept_graph,
        shared_limitations,
        scientific_progression,
        remaining_unknowns,
    )
    phase5_writer_blueprint = build_phase5_writer_blueprint(
        verrou_index,
        scientific_story,
        family_graph,
        concept_graph,
        comparison_graph,
        shared_limitations,
        cross_verrou_reasoning,
    )
    citation_index = build_global_citation_index(verrou_index)

    # V3.4 ? Vue explicite des verrous pour Phase 5
    verrou_sections_for_phase5 = _v34_build_verrou_sections_for_phase5(
        verrou_index=verrou_index,
        citation_index=citation_index,
    )
    verrou_coverage_summary = _v34_build_verrou_coverage_summary(
        verrou_sections_for_phase5=verrou_sections_for_phase5,
        citation_index=citation_index,
    )

    # V2.1 — macro-connaissance scientifique orientée consultant, non article-level.
    macro_scientific_knowledge_base = build_macro_scientific_knowledge_base(
        verrou_index,
        family_graph,
        concept_graph,
        shared_limitations,
    )
    # Conserver une base conceptuelle fine en fallback / compatibilité, mais Phase 5 doit prioriser la macro-base.
    scientific_knowledge_base = build_scientific_knowledge_base(
        verrou_index,
        family_graph,
        concept_graph,
        shared_limitations,
    )
    conceptual_comparison_graph = build_conceptual_comparison_graph_v21(
        comparison_graph,
        macro_scientific_knowledge_base,
    )
    consultant_storyline = build_consultant_storyline_v21(
        verrou_index,
        macro_scientific_knowledge_base,
        conceptual_comparison_graph,
        shared_limitations,
    )
    phase5_consultant_blueprint = build_phase5_consultant_blueprint_v21(
        verrou_index,
        macro_scientific_knowledge_base,
        consultant_storyline,
        conceptual_comparison_graph,
        shared_limitations,
    )

    out_dir = output_dir(organisme, project, year)
    output_path = out_dir / "scientific_narrative_payload.json"
    markdown_output_path = out_dir / "scientific_narrative_summary.md"

    payload: Dict[str, Any] = {
        "ok": True,
        "phase": "phase_4_7_scientific_narrative_builder",
        "step": "build_scientific_narrative_payload",
        "payload_type": OUTPUT_PAYLOAD_TYPE,
        "generated_at": now_iso(),
        "organisme": organisme,
        "project": project,
        "year": year,
        "dry_run": dry_run,
        "input_paths": {
            "phase_4_5_scientific_reasoning": str(p45_path),
            "phase_4_6_project_rd_argumentation": str(p46_path),
        },
        "verrous_count": len(verrou_index),
        "verrou_index": [
            {
                "verrou_id": v.get("verrou_id"),
                "verrou_title": v.get("verrou_title"),
                "has_phase_4_5": bool(v.get("phase_4_5")),
                "has_phase_4_6": bool(v.get("phase_4_6")),
            }
            for v in verrou_index
        ],
        "scientific_story": scientific_story,
        # V2.1 fields: ce sont les champs à exploiter prioritairement par Phase 5.
        "macro_scientific_knowledge_base": macro_scientific_knowledge_base,
        # Compatibilité : base fine conservée, mais non prioritaire pour la rédaction.
        "scientific_knowledge_base": scientific_knowledge_base,
        "conceptual_comparison_graph": conceptual_comparison_graph,
        "consultant_storyline": consultant_storyline,
        "phase5_consultant_blueprint": phase5_consultant_blueprint,
        "family_graph": family_graph,
        "concept_graph": concept_graph,
        "comparison_graph": comparison_graph,
        "shared_limitations": shared_limitations,
        "scientific_consensus": scientific_consensus,
        "scientific_contradictions": scientific_contradictions,
        "cross_verrou_reasoning": cross_verrou_reasoning,
        "scientific_progression": scientific_progression,
        "remaining_unknowns": remaining_unknowns,
        "global_citation_index": citation_index,
        "phase5_writer_blueprint": phase5_writer_blueprint,
        "rules": {
            "phase_4_7_role": "build_global_scientific_narrative_not_final_writing",
            "uses_phase_4_5": True,
            "uses_phase_4_6": True,
            "does_not_read_raw_articles": True,
            "does_not_create_new_scientific_evidence": True,
            "does_not_move_citations_between_verrous": True,
            "no_domain_hardcoding": True,
            "phase5_must_treat_phase47_as_reasoning_not_source": True,
            "v2_1_macro_concept_knowledge_base_is_primary": True,
            "v2_concept_knowledge_base_kept_for_compatibility": True,
            "families_are_internal_not_visible_sections": True,
            "articles_are_evidence_not_narrative_units": True,
            "concepts_must_be_explained_as_mechanisms_not_keywords": True,
            "macro_concepts_are_writer_units": True,
            "method_level_concepts_are_evidence_only": True,
        },
        "output_path": str(output_path),
        "markdown_output_path": str(markdown_output_path),
    }
    payload["quality"] = compute_quality(payload)
    payload["ok"] = payload["quality"]["score"] >= 60

    if not dry_run:
        write_json(output_path, payload)
        write_text(markdown_output_path, build_markdown_summary(payload))

    return payload



# ============================================================
# V2.2 — Evidence-chain consultant narrative overrides
# ============================================================

OUTPUT_PAYLOAD_TYPE = "scientific_narrative_payload_v2_2_evidence_chain_consultant_storyline"


def _v22_first_text(*values: Any, limit: int = 900) -> str:
    for v in values:
        if isinstance(v, list):
            for x in v:
                txt = _v22_first_text(x, limit=limit)
                if txt:
                    return txt
        elif isinstance(v, dict):
            txt = clean_sentence(v.get("text") or v.get("paragraph") or v.get("value") or v.get("content"))
            if txt:
                return truncate(txt, limit)
        else:
            txt = clean_sentence(v)
            if txt:
                return truncate(txt, limit)
    return ""


def _v22_bucket_text(method: Dict[str, Any], bucket: str, *, max_items: int = 2, limit_each: int = 520) -> str:
    """Retourne quelques paragraphes originaux de Phase 2/4.5 pour un bucket donné."""
    eb = method.get("evidence_extracts_for_reasoning") or method.get("evidence_extracts_for_phase_4_5") or {}
    if not isinstance(eb, dict):
        eb = {}
    vals = eb.get(bucket) or []
    out: List[str] = []
    for x in as_list(vals):
        txt = ""
        if isinstance(x, dict):
            txt = clean_sentence(x.get("text") or x.get("paragraph") or x.get("content"))
        else:
            txt = clean_sentence(x)
        if txt:
            out.append(truncate(txt, limit_each))
        if len(out) >= max_items:
            break
    return " ".join(out)


def _v22_method_stage(method: Dict[str, Any]) -> str:
    txt = merge_text_for_classification(
        method.get("method_name"),
        method.get("concept_label"),
        method.get("technical_family"),
        method.get("technical_principle"),
        method.get("mechanism"),
        method.get("dataset_context"),
        method.get("validation_protocol"),
        method.get("reported_results"),
        method.get("phase2_evidence_for_reasoning"),
    )
    if any(k in txt for k in ["dataset", "data-set", "training set", "test set", "mesure", "measurements", "represent", "représent"]):
        data_score = 1
    else:
        data_score = 0
    if any(k in txt for k in ["augment", "synthetic", "synth", "génér", "generation", "transformation", "mask", "adversarial", "diffusion"]):
        return "generation_augmentation_et_transformation"
    if any(k in txt for k in ["validation", "evaluation", "évaluation", "test", "robust", "generalization", "généralisation", "performance"]):
        # Si l'approche parle aussi fortement d'augmentation, elle reste dans génération ; sinon validation.
        if not any(k in txt for k in ["augment", "synthetic", "synth", "generation", "génération"]):
            return "robustesse_validation_et_generalisation"
    if any(k in txt for k in ["cnn", "deep", "neural", "classification", "learning", "apprentissage", "classifier"]):
        return "methodes_modeles_et_apprentissage"
    if any(k in txt for k in ["sparse", "signal", "phase-history", "phase history", "scattering", "representation", "représentation"]):
        return "donnees_et_representativite"
    if data_score:
        return "donnees_et_representativite"
    return "methodes_modeles_et_apprentissage"


def _v22_method_story_unit(method: Dict[str, Any], verrou: Dict[str, Any]) -> Dict[str, Any]:
    cit = normalize_citation_label(method.get("citation_label") or method.get("citation"))
    concept = clean_sentence(
        method.get("method_name")
        or method.get("concept_label")
        or method.get("method_or_concept")
        or method.get("subject_label")
        or method.get("article_title")
        or f"méthode {cit}"
    )
    problem = _v22_first_text(
        method.get("problem_context"),
        _v22_bucket_text(method, "problem"),
        method.get("impact_on_verrou"),
        verrou.get("verrou_title"),
        limit=700,
    )
    solution = _v22_first_text(
        method.get("solution_context"),
        _v22_bucket_text(method, "solution"),
        method.get("technical_principle"),
        limit=700,
    )
    principle = _v22_first_text(
        method.get("technical_principle"),
        method.get("principle"),
        _v22_bucket_text(method, "method"),
        method.get("mechanism"),
        limit=850,
    )
    mechanism = _v22_first_text(
        method.get("mechanism"),
        _v22_bucket_text(method, "workflow"),
        _v22_bucket_text(method, "method"),
        limit=950,
    )
    data_context = _v22_first_text(
        method.get("dataset_context"),
        _v22_bucket_text(method, "dataset"),
        limit=850,
    )
    validation = _v22_first_text(
        method.get("validation_protocol"),
        _v22_bucket_text(method, "validation"),
        limit=850,
    )
    results = _v22_first_text(
        method.get("reported_results") or method.get("scientific_results") or method.get("results"),
        _v22_bucket_text(method, "results"),
        limit=850,
    )
    limits = unique_clean_list(
        as_list(method.get("concept_limits"))
        + as_list(method.get("transposability_limits"))
        + [method.get("remaining_uncertainty"), _v22_bucket_text(method, "limitations", max_items=1, limit_each=520)],
        limit=5,
    )
    return {
        "unit_type": "method_story_unit",
        "citation_label": cit,
        "citation": citation_bracket(cit),
        "usage_type": clean_sentence(method.get("usage_type")),
        "priority_score": method.get("priority_score") or method.get("weight", {}).get("score") if isinstance(method.get("weight"), dict) else method.get("priority_score"),
        "stage": _v22_method_stage(method),
        "verrou_id": verrou.get("verrou_id"),
        "verrou_title": verrou.get("verrou_title"),
        "concept": concept,
        "technical_family_internal": clean_sentence(method.get("technical_family") or method.get("family_label")),
        "problem_to_solve": problem,
        "solution_or_contribution": solution,
        "scientific_principle": principle,
        "mechanism_chain": mechanism,
        "data_context": data_context,
        "validation_logic": validation,
        "demonstrated_or_reported_result": results,
        "limits_or_transposition_points": limits,
        "project_gap_link": _v22_first_text(method.get("impact_on_verrou"), method.get("remaining_uncertainty"), limit=650),
        "phase2_original_evidence_available": bool(method.get("evidence_extracts_for_reasoning") or method.get("phase2_evidence_for_reasoning")),
        "writer_instruction": (
            "Transformer cette unité en raisonnement consultant : problème → mécanisme → données/protocole → résultat/limite → lien au verrou. "
            "Ne pas recopier les paragraphes bruts et ne pas écrire une fiche article."
        ),
    }


def _v22_collect_method_story_units(verrou_index: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    units: List[Dict[str, Any]] = []
    seen = set()
    for verrou in verrou_index:
        p45 = verrou.get("phase_4_5") or {}
        methods = extract_technical_methods(p45)
        # Les methods peuvent aussi être dans approach_families. On les ajoute pour ne rien perdre.
        for fam in extract_approach_families(p45):
            for m in as_list(fam.get("technical_methods")):
                if isinstance(m, dict):
                    mm = dict(m)
                    mm.setdefault("technical_family", fam.get("family_label") or fam.get("technical_family"))
                    methods.append(mm)
        for method in methods:
            cit = normalize_citation_label(method.get("citation_label") or method.get("citation"))
            concept = clean_sentence(method.get("method_name") or method.get("concept_label") or method.get("method_or_concept"))
            key = (verrou.get("verrou_id"), cit, normalize_concept_key(concept))
            if not cit or key in seen:
                continue
            seen.add(key)
            units.append(_v22_method_story_unit(method, verrou))
    order = {s: i for i, s in enumerate(GENERIC_STAGE_ORDER)}
    return sorted(units, key=lambda x: (order.get(x.get("stage"), 99), citation_sort_key(x.get("citation_label"))))


def _v22_stage_sections_from_units(units: List[Dict[str, Any]], shared_limitations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for u in units:
        grouped[u.get("stage") or "methodes_modeles_et_apprentissage"].append(u)
    sections: List[Dict[str, Any]] = []
    for stage in GENERIC_STAGE_ORDER:
        us = grouped.get(stage) or []
        if not us and stage != "insuffisances_et_gap_rd":
            continue
        cits = safe_citations(sum([as_list(u.get("citation_label")) for u in us], []), limit=8)
        concepts = unique_clean_list([u.get("concept") for u in us if u.get("concept")], limit=8)
        if stage == "insuffisances_et_gap_rd":
            lim_labels = unique_clean_list([l.get("label") for l in shared_limitations if isinstance(l, dict)], limit=6)
            lim_cits = safe_citations(sum([as_list(l.get("citations")) for l in shared_limitations if isinstance(l, dict)], []), limit=8)
            cits = safe_citations(cits + lim_cits, limit=10)
            concepts = concepts or lim_labels
        sections.append({
            "stage": stage,
            "visible_title_suggestion": stage_title_generic(stage),
            "writing_goal": generic_stage_goal(stage),
            "transition": generic_transition_for_stage(stage),
            "concepts_to_weave": concepts,
            "citations_to_use": cits,
            "story_units": us[:8],
            "paragraph_logic": [
                "ouvrir par le besoin scientifique, pas par une citation",
                "expliquer le mécanisme avec des verbes d'action",
                "relier données, modèle et protocole de validation",
                "terminer par la limite ou la transposition non démontrée",
            ],
            "consultant_style_template": (
                "Pour répondre à ce problème, la littérature explore <mécanisme>. "
                "En d'autres termes, il s'agit de <principe scientifique>. "
                "Les données ou variantes produites sont ensuite exploitées pour <validation/modèle>. "
                "La limite restante porte sur <gap/protocole/transposition>."
            ),
        })
    return sections


def _v22_build_consultant_narrative_chain(
    verrou_index: List[Dict[str, Any]],
    units: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    stage_sections = _v22_stage_sections_from_units(units, shared_limitations)
    all_cits = safe_citations([u.get("citation_label") for u in units if u.get("citation_label")])
    titles = unique_clean_list([v.get("verrou_title") for v in verrou_index if v.get("verrou_title")])
    return {
        "chain_type": "consultant_story_chain_problem_method_data_validation_gap",
        "writer_language": "fr",
        "source_evidence_language": "mixed_original_paragraphs_fr_en",
        "verrous": titles,
        "global_arc": [
            "partir du verrou et du besoin de validation",
            "présenter les données et leur représentativité comme condition de validité",
            "introduire les modèles/mécanismes comme réponses partielles",
            "expliquer les stratégies de génération, augmentation ou transformation",
            "discuter validation, robustesse et généralisation",
            "conclure sur le gap R&D et les travaux expérimentaux nécessaires",
        ],
        "stage_sections": stage_sections,
        "method_story_units": units,
        "all_citations": all_cits,
        "paragraph_model": {
            "style_target": "paragraphe consultant CIR fluide, en français, sans plan article-par-article",
            "example_logic_not_to_copy": (
                "Pour résoudre ce problème, une approche peut apprendre une transformation entre deux distributions, "
                "produire des données affinées, entraîner un classifieur, puis tester la généralisation sur des mesures de référence."
            ),
            "mandatory_reasoning_order": "problème → principe → mécanisme → données → validation → limite/gap",
        },
        "anti_patterns": [
            "Ne pas écrire une fiche par article.",
            "Ne pas écrire les paragraphes originaux anglais tels quels.",
            "Ne pas afficher les familles internes comme titres visibles.",
            "Ne pas citer sans expliquer le mécanisme soutenu par la citation.",
        ],
    }


def build_phase5_consultant_blueprint_v22(
    verrou_index: List[Dict[str, Any]],
    macro_knowledge_base: List[Dict[str, Any]],
    consultant_storyline: Dict[str, Any],
    conceptual_comparisons: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
    narrative_chain: Dict[str, Any],
) -> Dict[str, Any]:
    base = build_phase5_consultant_blueprint_v21(
        verrou_index,
        macro_knowledge_base,
        consultant_storyline,
        conceptual_comparisons,
        shared_limitations,
    )
    base["blueprint_type"] = "phase5_consultant_writer_blueprint_v2_2_evidence_chain_story"
    base["core_change"] = (
        "Phase 5 doit rédiger une histoire scientifique enchaînée à partir des unités problème→mécanisme→données→validation→gap, "
        "et non à partir d'une liste d'articles ou de familles."
    )
    base["consultant_narrative_chain"] = narrative_chain
    base["story_units_to_write"] = narrative_chain.get("method_story_units") or []
    base["stage_sections"] = narrative_chain.get("stage_sections") or []
    base["write_like_consultant"] = [
        "Rédiger en français, même si les preuves sources sont en anglais.",
        "Construire des paragraphes fluides : problème → principe → mécanisme → données → validation → limite.",
        "Utiliser les articles comme preuves ponctuelles, jamais comme plan.",
        "Relier les méthodes entre elles par leur rôle scientifique : données, modèles, génération/augmentation, validation.",
        "Faire apparaître le verrou comme conséquence logique des limites de représentativité, robustesse et transposition.",
    ]
    base["sentence_logic_templates"] = [
        "Pour répondre à cette difficulté, les travaux existants mobilisent <mécanisme> afin de <objectif scientifique>.",
        "En d'autres termes, l'enjeu consiste à <principe scientifique>, puis à vérifier <condition de validation>.",
        "Les données ou variantes obtenues peuvent ensuite être utilisées pour <modèle / classifieur / protocole>, mais leur généralisation reste conditionnée par <limite>.",
        "Cette progression montre que l'état de l'art fournit des mécanismes utiles, sans démontrer leur suffisance dans le contexte propre du projet.",
    ]
    return base


def build_scientific_narrative_payload(
    *,
    organisme: str,
    project: str,
    year: str,
    phase_4_5_path: Optional[str] = None,
    phase_4_6_path: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    p45_path = Path(phase_4_5_path) if phase_4_5_path else default_phase_4_5_path(organisme, project, year)
    p46_path = Path(phase_4_6_path) if phase_4_6_path else default_phase_4_6_path(organisme, project, year)

    phase45 = read_json(p45_path, {}) or {}
    phase46 = read_json(p46_path, {}) or {}

    verrou_index = build_verrou_index(phase45, phase46)
    if not verrou_index:
        return {
            "ok": False,
            "phase": "phase_4_7_scientific_narrative_builder",
            "payload_type": OUTPUT_PAYLOAD_TYPE,
            "generated_at": now_iso(),
            "organisme": organisme,
            "project": project,
            "year": year,
            "error": "Aucun verrou trouvé dans Phase 4.5 ou Phase 4.6.",
            "input_paths": {"phase_4_5": str(p45_path), "phase_4_6": str(p46_path)},
        }

    family_graph = build_family_graph(verrou_index)
    concept_graph = build_concept_graph(verrou_index, family_graph)
    comparison_graph = build_comparison_graph(verrou_index, family_graph)
    shared_limitations = build_shared_limitations(verrou_index)
    scientific_consensus = build_scientific_consensus(family_graph, shared_limitations, concept_graph)
    scientific_contradictions = build_scientific_contradictions(comparison_graph, shared_limitations)
    cross_verrou_reasoning = build_cross_verrou_reasoning(verrou_index, family_graph, shared_limitations)
    scientific_progression = build_scientific_progression(family_graph, concept_graph, comparison_graph)
    remaining_unknowns = build_remaining_unknowns(verrou_index, shared_limitations)
    scientific_story = build_scientific_story(
        verrou_index,
        family_graph,
        concept_graph,
        shared_limitations,
        scientific_progression,
        remaining_unknowns,
    )

    macro_scientific_knowledge_base = build_macro_scientific_knowledge_base(
        verrou_index,
        family_graph,
        concept_graph,
        shared_limitations,
    )
    scientific_knowledge_base = build_scientific_knowledge_base(
        verrou_index,
        family_graph,
        concept_graph,
        shared_limitations,
    )
    conceptual_comparison_graph = build_conceptual_comparison_graph_v21(
        comparison_graph,
        macro_scientific_knowledge_base,
    )
    consultant_storyline = build_consultant_storyline_v21(
        verrou_index,
        macro_scientific_knowledge_base,
        conceptual_comparison_graph,
        shared_limitations,
    )

    method_story_units = _v22_collect_method_story_units(verrou_index)
    consultant_narrative_chain = _v22_build_consultant_narrative_chain(
        verrou_index,
        method_story_units,
        shared_limitations,
    )
    phase5_consultant_blueprint = build_phase5_consultant_blueprint_v22(
        verrou_index,
        macro_scientific_knowledge_base,
        consultant_storyline,
        conceptual_comparison_graph,
        shared_limitations,
        consultant_narrative_chain,
    )

    phase5_writer_blueprint = build_phase5_writer_blueprint(
        verrou_index,
        scientific_story,
        family_graph,
        concept_graph,
        comparison_graph,
        shared_limitations,
        cross_verrou_reasoning,
    )
    citation_index = build_global_citation_index(verrou_index)

    # V3.4 : vue explicite pour la partie "Verrous et incertitudes" dans l'état de l'art unique.
    verrou_sections_for_phase5 = _v34_build_verrou_sections_for_phase5(
        verrou_index=verrou_index,
        units=raw_units,
        axes=axes,
        shared_limitations=refined_limitations,
    )
    phase5_consultant_blueprint = _v34_enrich_phase5_blueprint_with_verrou_sections(
        phase5_consultant_blueprint,
        verrou_sections_for_phase5,
    )
    phase5_writer_blueprint = _v34_enrich_phase5_blueprint_with_verrou_sections(
        phase5_writer_blueprint,
        verrou_sections_for_phase5,
    )
    verrou_coverage_summary = _v34_verrou_coverage_summary(verrou_sections_for_phase5)

    out_dir = output_dir(organisme, project, year)
    output_path = out_dir / "scientific_narrative_payload.json"
    markdown_output_path = out_dir / "scientific_narrative_summary.md"

    payload: Dict[str, Any] = {
        "ok": True,
        "phase": "phase_4_7_scientific_narrative_builder",
        "step": "build_scientific_narrative_payload",
        "payload_type": OUTPUT_PAYLOAD_TYPE,
        "generated_at": now_iso(),
        "organisme": organisme,
        "project": project,
        "year": year,
        "dry_run": dry_run,
        "input_paths": {
            "phase_4_5_scientific_reasoning": str(p45_path),
            "phase_4_6_project_rd_argumentation": str(p46_path),
        },
        "verrous_count": len(verrou_index),
        "verrou_index": [
            {
                "verrou_id": v.get("verrou_id"),
                "verrou_title": v.get("verrou_title"),
                "has_phase_4_5": bool(v.get("phase_4_5")),
                "has_phase_4_6": bool(v.get("phase_4_6")),
            }
            for v in verrou_index
        ],
        "scientific_story": scientific_story,
        "consultant_narrative_chain": consultant_narrative_chain,
        "method_story_units": method_story_units,
        "macro_scientific_knowledge_base": macro_scientific_knowledge_base,
        "scientific_knowledge_base": scientific_knowledge_base,
        "conceptual_comparison_graph": conceptual_comparison_graph,
        "consultant_storyline": consultant_storyline,
        "phase5_consultant_blueprint": phase5_consultant_blueprint,
        "family_graph": family_graph,
        "concept_graph": concept_graph,
        "comparison_graph": comparison_graph,
        "shared_limitations": shared_limitations,
        "scientific_consensus": scientific_consensus,
        "scientific_contradictions": scientific_contradictions,
        "cross_verrou_reasoning": cross_verrou_reasoning,
        "scientific_progression": scientific_progression,
        "remaining_unknowns": remaining_unknowns,
        "global_citation_index": citation_index,
        "phase5_writer_blueprint": phase5_writer_blueprint,
        "rules": {
            "phase_4_7_role": "build_evidence_chain_for_final_writer_not_final_writing",
            "uses_phase_4_5": True,
            "uses_phase_4_6": True,
            "uses_phase2_original_evidence_bank_through_phase45": True,
            "does_not_create_new_scientific_evidence": True,
            "does_not_move_citations_between_verrous": True,
            "no_domain_hardcoding": True,
            "families_are_internal_not_visible_sections": True,
            "articles_are_evidence_not_narrative_units": True,
            "method_story_units_are_primary_for_phase5": True,
            "mandatory_reasoning_order": "problem_to_solve -> scientific_principle -> mechanism_chain -> data_context -> validation_logic -> limit_gap",
            "final_writer_language": "fr",
        },
        "output_path": str(output_path),
        "markdown_output_path": str(markdown_output_path),
    }
    payload["quality"] = compute_quality(payload)
    if not method_story_units:
        payload["quality"]["score"] = max(0, payload["quality"]["score"] - 25)
        payload["quality"].setdefault("warnings", []).append("method_story_units vide: Phase 5 risque de manquer de matière narrative.")
    payload["ok"] = payload["quality"]["score"] >= 60

    if not dry_run:
        write_json(output_path, payload)
        write_text(markdown_output_path, build_markdown_summary(payload))

    return payload




# ============================================================
# V3.0 — Project-specific evidence-chain narrative, no hardcoding
# ============================================================

OUTPUT_PAYLOAD_TYPE = "scientific_narrative_payload_v3_0_project_specific_evidence_chain_no_hardcoding"


def _v30_clean_list(values: Any, *, limit: Optional[int] = None, max_chars: int = 700) -> List[str]:
    """Flatten + clean, without dropping unusual technical evidence."""
    out: List[str] = []
    stack = list(as_list(values))
    while stack:
        item = stack.pop(0)
        if item is None:
            continue
        if isinstance(item, dict):
            preferred = [
                "text", "paragraph", "content", "value", "summary", "claim", "result_text",
                "method", "protocol", "metric", "parameter", "unit", "label", "name", "title",
            ]
            picked = False
            for k in preferred:
                txt = clean_sentence(item.get(k))
                if txt:
                    out.append(truncate(txt, max_chars))
                    picked = True
                    break
            if not picked:
                for v in item.values():
                    if isinstance(v, (str, int, float)):
                        txt = clean_sentence(v)
                        if txt:
                            out.append(truncate(txt, max_chars))
                            break
        elif isinstance(item, (list, tuple)):
            stack = list(item) + stack
        else:
            txt = clean_sentence(item)
            if txt:
                out.append(truncate(txt, max_chars))
    return unique_clean_list(out, limit=limit)


def _v30_get_path(obj: Any, *paths: str) -> Any:
    """Read first available nested path like 'a.b.c'."""
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur.get(part)
            else:
                ok = False
                break
        if ok and cur not in [None, "", [], {}]:
            return cur
    return None


def _v30_metrics_from_any(value: Any) -> Dict[str, Any]:
    """Collect all metrics and numeric values without requiring a known domain."""
    texts = _v30_clean_list(value, limit=40, max_chars=700)
    joined = "\n".join(texts)
    numeric_patterns = [
        r"\b\d+(?:[\.,]\d+)?\s*(?:%|percent|MPa|GPa|kPa|Pa|N|kN|mN|kg|g|mg|µg|ug|L|mL|µL|ul|mol|mmol|°C|K|s|min|h|ms|Hz|kHz|MHz|GHz|V|mV|A|mA|W|kW|m|cm|mm|µm|um|nm|px|ppm|ppb)\b",
        r"\b(?:accuracy|precision|recall|f1|f-score|auc|rmse|mae|mse|r2|r²|yield|rendement|strength|résistance|resistance|modulus|module|porosity|porosité|viscosity|viscosité|conductivity|conductivité|error|erreur)\s*(?:of|=|:|à|de|around|about|almost|environ|près de)?\s*\d+(?:[\.,]\d+)?\s*%?",
        r"\b\d+(?:[\.,]\d+)?\s*(?:x|×)\s*\d+(?:[\.,]\d+)?\b",
        r"\b[<>≤≥]\s*\d+(?:[\.,]\d+)?\s*\w*\b",
        r"\b\d+(?:[\.,]\d+)?\s*(?:±|\+/-)\s*\d+(?:[\.,]\d+)?\s*\w*\b",
    ]
    raw_values: List[str] = []
    for pat in numeric_patterns:
        raw_values += [m.group(0) for m in re.finditer(pat, joined, flags=re.I)]
    metric_terms = []
    metric_kw = r"\b(accuracy|precision|recall|f1|f-score|auc|rmse|mae|mse|r2|r²|yield|rendement|résistance|resistance|strength|module|modulus|porosity|porosité|viscosity|viscosité|conductivity|conductivité|error|erreur|loss|score|ratio|rate|taux|gain|improvement|amélioration|amelioration)\b"
    for m in re.finditer(metric_kw, joined, flags=re.I):
        start = max(0, m.start() - 80)
        end = min(len(joined), m.end() + 120)
        metric_terms.append(joined[start:end].replace("\n", " "))
    return {
        "metrics_detected_or_mentioned": unique_clean_list(metric_terms, limit=20),
        "raw_numeric_or_value_mentions": unique_clean_list(raw_values, limit=30),
        "all_metric_source_texts": texts[:20],
        "metric_extraction_status": "metric_or_numeric_value_found" if raw_values or metric_terms else "no_explicit_metric_value_detected",
        "no_value_discarded_because_unclassified": True,
    }


def _v30_open_domain_evidence(profile: Dict[str, Any], method: Dict[str, Any], evidence46: Dict[str, Any]) -> Dict[str, Any]:
    open_ev = _v30_get_path(profile, "open_domain_technical_evidence") or {}
    if not isinstance(open_ev, dict):
        open_ev = {}
    raw_snippets = []
    for path in [
        "unclassified_technical_snippets",
        "explicit_numeric_expressions",
        "equations_or_formulae",
        "named_variables_or_parameters",
        "technical_detail_summary_for_phase5",
        "result_method_test_summary_for_phase5",
        "result_claims_for_phase5",
    ]:
        raw_snippets += _v30_clean_list(_v30_get_path(open_ev, path), limit=20)
        raw_snippets += _v30_clean_list(_v30_get_path(method, path), limit=20)
        raw_snippets += _v30_clean_list(_v30_get_path(evidence46, path), limit=20)
    return {
        "unclassified_technical_snippets": unique_clean_list(raw_snippets, limit=30),
        "explicit_numeric_expressions": unique_clean_list(
            _v30_clean_list(open_ev.get("explicit_numeric_expressions"), limit=20)
            + _v30_clean_list(method.get("explicit_numeric_expressions"), limit=20),
            limit=30,
        ),
        "equations_or_formulae": unique_clean_list(
            _v30_clean_list(open_ev.get("equations_or_formulae"), limit=20)
            + _v30_clean_list(method.get("equations_or_formulae"), limit=20),
            limit=20,
        ),
        "named_variables_or_parameters": unique_clean_list(
            _v30_clean_list(open_ev.get("named_variables_or_parameters"), limit=20)
            + _v30_clean_list(method.get("named_variables_or_parameters"), limit=20),
            limit=30,
        ),
        "has_open_domain_evidence": bool(raw_snippets or open_ev),
    }


def _v30_collect_phase46_evidence_by_citation(verrou: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    aj = verrou.get("argumentation_json") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for ev in as_list(aj.get("evidence_by_citation")):
        if not isinstance(ev, dict):
            continue
        c = normalize_citation_label(ev.get("citation") or ev.get("citation_label"))
        if c:
            out[c] = ev
    return out


def _v30_result_claims(profile: Dict[str, Any], method: Dict[str, Any], evidence46: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = []
    for source_name, src in [
        ("profile", profile),
        ("method", method),
        ("phase46", evidence46),
    ]:
        if not isinstance(src, dict):
            continue
        vals = []
        vals += as_list(_v30_get_path(src, "result_method_test_links.result_claims"))
        vals += as_list(_v30_get_path(src, "technical_detail_profile.result_method_test_links.result_claims"))
        vals += as_list(src.get("result_claims"))
        vals += as_list(src.get("result_claims_for_phase5"))
        vals += as_list(src.get("result_method_test_summary_for_phase5"))
        vals += as_list(src.get("reported_results"))
        vals += as_list(src.get("scientific_results"))
        vals += as_list(src.get("results"))
        for i, val in enumerate(vals, 1):
            if not val:
                continue
            if isinstance(val, dict):
                result_text = clean_sentence(
                    val.get("result_text") or val.get("claim") or val.get("text") or val.get("summary") or val.get("value")
                )
                method_ctx = _v30_clean_list(
                    val.get("linked_method_or_technology_context") or val.get("method_context") or val.get("method") or val.get("technology"),
                    limit=6,
                )
                test_ctx = _v30_clean_list(
                    val.get("linked_test_or_validation_context") or val.get("test_context") or val.get("validation") or val.get("protocol"),
                    limit=6,
                )
                metrics = val.get("metrics_and_values") if isinstance(val.get("metrics_and_values"), dict) else _v30_metrics_from_any(val)
            else:
                result_text = clean_sentence(val)
                method_ctx = []
                test_ctx = []
                metrics = _v30_metrics_from_any(val)
            if not result_text and not metrics.get("raw_numeric_or_value_mentions"):
                continue
            candidates.append({
                "result_id": f"R{len(candidates)+1}",
                "source": source_name,
                "result_text": truncate(result_text, 800),
                "metrics_and_values": metrics,
                "linked_method_or_technology_context": method_ctx,
                "linked_test_or_validation_context": test_ctx,
                "link_confidence": "high" if method_ctx and test_ctx else ("medium" if method_ctx or test_ctx else "low_unlinked_but_kept"),
                "no_result_discarded_because_metric_empty": True,
            })
    # dédup by result text + values
    seen = set()
    out = []
    for r in candidates:
        key = normalize_concept_key(r.get("result_text") + " " + " ".join(r.get("metrics_and_values", {}).get("raw_numeric_or_value_mentions") or []))[:180]
        if not key or key in seen:
            continue
        seen.add(key)
        r["result_id"] = f"R{len(out)+1}"
        out.append(r)
        if len(out) >= 8:
            break
    return out


def _v30_collect_technical_details(profile: Dict[str, Any], method: Dict[str, Any], evidence46: Dict[str, Any]) -> Dict[str, Any]:
    detail_sources = [profile, method, evidence46]
    def collect(*keys: str, limit: int = 20) -> List[str]:
        vals: List[str] = []
        for src in detail_sources:
            if not isinstance(src, dict):
                continue
            for key in keys:
                vals += _v30_clean_list(_v30_get_path(src, key), limit=limit)
        return unique_clean_list(vals, limit=limit)

    protocol = collect(
        "scientific_method_profile",
        "method_and_experimental_protocol",
        "method_and_experimental_protocol.protocol_steps",
        "method_and_experimental_protocol.experimental_protocol",
        "validation_protocol",
        "data_and_protocol",
        "data_and_protocol.protocol",
        "technical_detail_summary_for_phase5",
        limit=25,
    )
    parameters = collect(
        "measurable_parameters_by_unit",
        "implementation_parameters",
        "training_hyperparameters",
        "method_parameters",
        "parameters",
        "named_variables_or_parameters",
        limit=25,
    )
    instruments = collect(
        "instrumentation",
        "scientific_method_profile.instrumentation",
        "implementation_parameters.instrumentation",
        limit=15,
    )
    material_or_data = collect(
        "materials_samples_components",
        "data_and_protocol.dataset",
        "data_and_protocol.samples",
        "dataset_context",
        "data_context",
        "sample_context",
        limit=20,
    )
    simulation_or_computation = collect(
        "simulation_numerical_parameters",
        "implementation_parameters.simulation",
        "implementation_parameters.mesh",
        "implementation_parameters.solver",
        limit=20,
    )
    metrics = _v30_metrics_from_any([
        collect("evaluation_metrics", "reported_results", "scientific_results", "results", limit=20),
        profile,
        method,
        evidence46,
    ])
    return {
        "method_or_process_details": collect("scientific_method_profile", "method_name", "method_or_concept", "technical_family", limit=20),
        "protocol_or_test_details": protocol,
        "materials_data_or_samples": material_or_data,
        "parameters_conditions_or_settings": parameters,
        "instrumentation_or_tools": instruments,
        "simulation_or_implementation_details": simulation_or_computation,
        "metrics_and_values": metrics,
        "missing_details": collect("missing_details", limit=20),
        "technical_detail_sources_present": [
            k for k in [
                "technical_detail_profile",
                "technical_detail_matrix",
                "result_method_test_links",
                "phase46_evidence_details",
            ]
            if (
                (k == "technical_detail_profile" and profile)
                or (k == "technical_detail_matrix" and method.get("technical_detail_matrix"))
                or (k == "result_method_test_links" and (method.get("result_method_test_links") or profile.get("result_method_test_links")))
                or (k == "phase46_evidence_details" and evidence46)
            )
        ],
        "no_detail_discarded_because_unclassified": True,
    }


def _v30_unit_richness(unit: Dict[str, Any]) -> int:
    score = 0
    fields = [
        "problem_to_solve", "scientific_principle", "mechanism_chain", "data_context",
        "validation_logic", "demonstrated_or_reported_result", "limits_or_transposition_points",
    ]
    score += sum(1 for f in fields if clean_sentence(unit.get(f))) * 5
    td = unit.get("technical_detail_profile_for_phase5") or {}
    for key in [
        "protocol_or_test_details", "materials_data_or_samples", "parameters_conditions_or_settings",
        "instrumentation_or_tools", "simulation_or_implementation_details",
    ]:
        score += min(12, len(td.get(key) or []) * 3)
    rv = td.get("metrics_and_values") or {}
    score += min(15, len(rv.get("raw_numeric_or_value_mentions") or []) * 3)
    score += min(15, len(unit.get("result_claims_linked_to_method_test_metric") or []) * 5)
    if (unit.get("open_domain_technical_evidence") or {}).get("has_open_domain_evidence"):
        score += 8
    return score


def _v30_build_story_unit(method: Dict[str, Any], verrou: Dict[str, Any], evidence46: Dict[str, Any]) -> Dict[str, Any]:
    base = _v22_method_story_unit(method, verrou)
    profile = method.get("technical_detail_profile") if isinstance(method.get("technical_detail_profile"), dict) else {}
    if not profile and isinstance(method.get("technical_details"), dict):
        profile = method.get("technical_details") or {}
    if not profile and isinstance(method.get("raw"), dict):
        raw_profile = method.get("raw", {}).get("technical_detail_profile")
        if isinstance(raw_profile, dict):
            profile = raw_profile

    details = _v30_collect_technical_details(profile, method, evidence46)
    result_claims = _v30_result_claims(profile, method, evidence46)
    open_domain = _v30_open_domain_evidence(profile, method, evidence46)

    # Recover Phase 4.6 summaries that were added for Phase 5.
    phase46_summaries = {
        "technical_detail_summary_for_phase5": _v30_clean_list(evidence46.get("technical_detail_summary_for_phase5"), limit=12),
        "result_method_test_summary_for_phase5": _v30_clean_list(evidence46.get("result_method_test_summary_for_phase5"), limit=12),
        "result_claims_for_phase5": _v30_clean_list(evidence46.get("result_claims_for_phase5"), limit=12),
    }

    # Keep all old V2.2 fields, then add explicit no-loss V3 fields.
    base.update({
        "unit_type": "project_specific_evidence_chain_unit_v3",
        "technical_detail_profile_for_phase5": details,
        "result_claims_linked_to_method_test_metric": result_claims,
        "open_domain_technical_evidence": open_domain,
        "phase46_summaries_for_phase5": phase46_summaries,
        "must_exploit_in_phase5": {
            "method_or_process": bool(details.get("method_or_process_details") or base.get("concept")),
            "protocol_or_test": bool(details.get("protocol_or_test_details") or base.get("validation_logic")),
            "parameters_or_conditions": bool(details.get("parameters_conditions_or_settings")),
            "metrics_or_values": bool((details.get("metrics_and_values") or {}).get("raw_numeric_or_value_mentions") or (details.get("metrics_and_values") or {}).get("metrics_detected_or_mentioned")),
            "results_linked_to_method_test": bool(result_claims),
            "open_domain_unclassified_evidence": bool(open_domain.get("has_open_domain_evidence")),
        },
        "writer_instruction": (
            "Phase 5 doit utiliser cette unité comme chaîne technique : méthode/procédé → paramètres/conditions → protocole/test → résultat/métrique → limite/transposition. "
            "Ne pas perdre les détails non classés ; les intégrer prudemment ou les conserver en annexe de raisonnement."
        ),
    })
    base["evidence_richness_score"] = _v30_unit_richness(base)
    return base


def _v30_collect_project_specific_story_units(verrou_index: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    units: List[Dict[str, Any]] = []
    seen = set()
    for verrou in verrou_index:
        p45 = verrou.get("phase_4_5") or {}
        evidence46_by_cit = _v30_collect_phase46_evidence_by_citation(verrou)
        methods = extract_technical_methods(p45)
        for fam in extract_approach_families(p45):
            for m in as_list(fam.get("technical_methods")):
                if isinstance(m, dict):
                    mm = dict(m)
                    mm.setdefault("technical_family", fam.get("family_label") or fam.get("technical_family"))
                    methods.append(mm)
        for method in methods:
            cit = normalize_citation_label(method.get("citation_label") or method.get("citation"))
            concept = clean_sentence(method.get("method_name") or method.get("concept_label") or method.get("method_or_concept") or method.get("subject_label"))
            key = (verrou.get("verrou_id"), cit, normalize_concept_key(concept or method.get("article_title")))
            if not cit or key in seen:
                continue
            seen.add(key)
            evidence46 = evidence46_by_cit.get(cit, {})
            units.append(_v30_build_story_unit(method, verrou, evidence46))
    order = {s: i for i, s in enumerate(GENERIC_STAGE_ORDER)}
    return sorted(units, key=lambda x: (order.get(x.get("stage"), 99), -int(x.get("evidence_richness_score") or 0), citation_sort_key(x.get("citation_label"))))


def _v30_axis_label_seed(unit: Dict[str, Any]) -> str:
    # Prefer actual project/article concepts, not predefined stages.
    candidates = [
        unit.get("technical_family_internal"),
        unit.get("concept"),
        unit.get("scientific_principle"),
        unit.get("mechanism_chain"),
        unit.get("data_context"),
    ]
    for c in candidates:
        txt = clean_sentence(c)
        if txt:
            # Keep a compact seed. It is internal; visible title will be generated later.
            words = [w for w in text_tokens(txt) if len(w) >= 4][:8]
            if words:
                return " ".join(words[:6])
            return truncate(txt, 90)
    return clean_sentence(unit.get("stage") or "axe scientifique")


def _v30_group_units_into_project_axes(units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    axes: List[Dict[str, Any]] = []
    for unit in units:
        seed = _v30_axis_label_seed(unit)
        chosen = None
        best = 0.0
        for axis in axes:
            score = max(
                jaccard_similarity(seed, axis.get("axis_seed")),
                jaccard_similarity(unit.get("concept"), " ".join(axis.get("concepts") or [])),
                jaccard_similarity(unit.get("technical_family_internal"), " ".join(axis.get("internal_families") or [])),
            )
            if score > best:
                best = score
                chosen = axis
        if chosen is None or best < 0.12:
            chosen = {
                "axis_id": f"axis_{len(axes)+1:02d}",
                "axis_seed": seed,
                "story_role": unit.get("stage") or "project_specific_axis",
                "concepts": [],
                "internal_families": [],
                "citations": [],
                "verrous": [],
                "units": [],
                "technical_details_to_exploit": [],
                "result_metric_links_to_exploit": [],
                "open_domain_evidence_to_keep": [],
            }
            axes.append(chosen)
        chosen["units"].append(unit)
        chosen["concepts"].append(unit.get("concept"))
        chosen["internal_families"].append(unit.get("technical_family_internal"))
        chosen["citations"].append(unit.get("citation_label"))
        chosen["verrous"].append({"verrou_id": unit.get("verrou_id"), "verrou_title": unit.get("verrou_title")})
        td = unit.get("technical_detail_profile_for_phase5") or {}
        for key in [
            "protocol_or_test_details", "materials_data_or_samples", "parameters_conditions_or_settings",
            "instrumentation_or_tools", "simulation_or_implementation_details",
        ]:
            chosen["technical_details_to_exploit"] += _v30_clean_list(td.get(key), limit=20)
        for rc in as_list(unit.get("result_claims_linked_to_method_test_metric")):
            if isinstance(rc, dict):
                chosen["result_metric_links_to_exploit"].append({
                    "citation": unit.get("citation"),
                    "result_text": rc.get("result_text"),
                    "metrics_and_values": rc.get("metrics_and_values"),
                    "linked_method_or_technology_context": rc.get("linked_method_or_technology_context"),
                    "linked_test_or_validation_context": rc.get("linked_test_or_validation_context"),
                    "link_confidence": rc.get("link_confidence"),
                })
        od = unit.get("open_domain_technical_evidence") or {}
        chosen["open_domain_evidence_to_keep"] += _v30_clean_list(od.get("unclassified_technical_snippets"), limit=15)

    for axis in axes:
        axis["concepts"] = unique_clean_list(axis.get("concepts") or [], limit=12)
        axis["internal_families"] = unique_clean_list(axis.get("internal_families") or [], limit=8)
        axis["citations"] = safe_citations(axis.get("citations") or [], limit=12)
        axis["verrous"] = dedup_verrou_refs(axis.get("verrous") or [])
        axis["technical_details_to_exploit"] = unique_clean_list(axis.get("technical_details_to_exploit") or [], limit=15)
        axis["open_domain_evidence_to_keep"] = unique_clean_list(axis.get("open_domain_evidence_to_keep") or [], limit=12)
        axis["evidence_richness_score"] = sum(int(u.get("evidence_richness_score") or 0) for u in axis.get("units") or [])
        axis["visible_title_suggestion"] = _v30_make_project_axis_title(axis)
        axis["writer_goal"] = _v30_axis_writer_goal(axis)
        axis["writer_instruction"] = (
            "Rédiger un paragraphe propre à ce projet à partir des unités techniques ci-dessous. "
            "Le titre proposé est dynamique ; il doit refléter les concepts réellement présents, pas une catégorie générique."
        )
    # Narrative order: group by existing scientific role but use dynamic axes.
    order = {s: i for i, s in enumerate(GENERIC_STAGE_ORDER)}
    return sorted(axes, key=lambda a: (order.get(a.get("story_role"), 50), -int(a.get("evidence_richness_score") or 0)))


def _v30_make_project_axis_title(axis: Dict[str, Any]) -> str:
    concepts = [c for c in unique_clean_list(axis.get("concepts") or [], limit=4) if len(clean_sentence(c)) <= 90]
    families = [f for f in unique_clean_list(axis.get("internal_families") or [], limit=3) if len(clean_sentence(f)) <= 90]
    base_items = concepts or families
    if base_items:
        if len(base_items) == 1:
            return base_items[0]
        return " / ".join(base_items[:2])
    seed = clean_sentence(axis.get("axis_seed"))
    return truncate(seed or stage_title_generic(axis.get("story_role")), 90)


def _v30_axis_writer_goal(axis: Dict[str, Any]) -> str:
    has_results = bool(axis.get("result_metric_links_to_exploit"))
    has_params = bool(axis.get("technical_details_to_exploit"))
    title = axis.get("visible_title_suggestion") or "cet axe"
    parts = [f"Expliquer l'axe « {title} » comme une étape de l'histoire scientifique du projet."]
    if has_params:
        parts.append("Intégrer les paramètres, conditions, protocoles ou instruments explicitement extraits.")
    if has_results:
        parts.append("Relier les résultats à la méthode, au test/protocole et aux métriques disponibles.")
    parts.append("Conclure sur ce que cela ne démontre pas encore pour le verrou CIR.")
    return " ".join(parts)


def _v30_build_project_storyline(verrou_index: List[Dict[str, Any]], axes: List[Dict[str, Any]], shared_limitations: List[Dict[str, Any]]) -> Dict[str, Any]:
    project_need, project_gap = extract_project_need_and_gap(verrou_index)
    titles = unique_clean_list([v.get("verrou_title") for v in verrou_index if v.get("verrou_title")])
    progression = []
    for i, axis in enumerate(axes, 1):
        progression.append({
            "step": i,
            "axis_id": axis.get("axis_id"),
            "visible_title_suggestion": axis.get("visible_title_suggestion"),
            "story_role": axis.get("story_role"),
            "writer_goal": axis.get("writer_goal"),
            "concepts_to_weave": axis.get("concepts") or [],
            "citations": axis.get("citations") or [],
            "must_use_details": axis.get("technical_details_to_exploit")[:6],
            "must_use_result_metric_links": axis.get("result_metric_links_to_exploit")[:4],
            "transition_to_next": _v30_transition_between_axes(axis, axes[i] if i < len(axes) else None),
        })
    limitations = [clean_sentence(l.get("label")) for l in shared_limitations if isinstance(l, dict) and clean_sentence(l.get("label"))]
    return {
        "storyline_type": "project_specific_scientific_storyline_v3_no_hardcoding",
        "goal": "Définir l'histoire scientifique propre au projet à partir des preuves techniques Phase 4.5 et de l'argumentaire Phase 4.6.",
        "project_need": project_need,
        "project_gap": project_gap,
        "verrous_to_defend": titles,
        "narrative_principle": (
            "La progression doit être déduite des axes, méthodes, paramètres, protocoles, résultats et limites réellement extraits. "
            "Aucun plan domaine prédéfini ne doit remplacer les preuves du dossier."
        ),
        "project_axes_progression": progression,
        "limitations_to_turn_into_gap": unique_clean_list(limitations, limit=8),
        "opening_strategy": "Commencer par le besoin projet et le verrou, puis introduire le premier axe technique réellement dominant.",
        "closing_strategy": "Terminer par l'écart entre résultats/méthodes disponibles et validation encore attendue dans le contexte propre du projet.",
        "strict_rules": [
            "Ne pas utiliser un plan hardcodé par domaine.",
            "Ne pas perdre les résultats même si metrics_detected est vide : regarder raw_numeric_or_value_mentions et result_text.",
            "Ne pas perdre les preuves techniques non classées : utiliser open_domain_technical_evidence.",
            "Ne pas écrire une fiche par article ; transformer les unités en chaîne méthode → protocole → résultat → limite.",
            "Ne pas copier les sections Phase 4.6 ; les utiliser seulement pour le gap CIR et les contraintes projet.",
        ],
    }


def _v30_transition_between_axes(current: Dict[str, Any], nxt: Optional[Dict[str, Any]]) -> str:
    cur = clean_sentence(current.get("visible_title_suggestion"))
    if not nxt:
        return "Cette étape doit conduire vers la formulation des limites de transposition et du gap R&D."
    nn = clean_sentence(nxt.get("visible_title_suggestion"))
    return f"Après avoir expliqué {cur}, la narration doit montrer comment cet acquis conduit à discuter {nn}."


def _v30_build_phase5_blueprint(verrou_index: List[Dict[str, Any]], axes: List[Dict[str, Any]], storyline: Dict[str, Any], shared_limitations: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "blueprint_type": "phase5_writer_blueprint_v3_project_specific_axes_evidence_chain",
        "core_change": "Phase 5 doit suivre les axes dynamiques du projet et exploiter les chaînes techniques détaillées, pas un plan générique.",
        "project_need": storyline.get("project_need"),
        "project_gap": storyline.get("project_gap"),
        "visible_section_order": [a.get("axis_id") for a in axes] + ["insuffisances_et_gap_rd"],
        "section_guidance": [
            {
                "section_id": a.get("axis_id"),
                "title_suggestion": a.get("visible_title_suggestion"),
                "goal": a.get("writer_goal"),
                "concepts_to_weave": a.get("concepts") or [],
                "citations_to_use": a.get("citations") or [],
                "technical_details_to_use": a.get("technical_details_to_exploit")[:10],
                "result_metric_links_to_use": a.get("result_metric_links_to_exploit")[:6],
                "open_domain_evidence_to_keep": a.get("open_domain_evidence_to_keep")[:6],
                "story_units": a.get("units")[:6],
                "must_not": [
                    "ne pas écrire une fiche par article",
                    "ne pas ignorer les paramètres/conditions si présents",
                    "ne pas ignorer les résultats si metrics_detected est vide",
                    "ne pas afficher les familles internes comme plan figé",
                ],
            }
            for a in axes
        ] + [{
            "section_id": "insuffisances_et_gap_rd",
            "title_suggestion": "Insuffisances de l'état de l'art et gap R&D",
            "goal": "Transformer les limites et non-transpositions en justification CIR.",
            "limitations_to_integrate": [
                {
                    "label": l.get("label"),
                    "citations": safe_citations(l.get("citations") or [], limit=4),
                    "impacts": unique_clean_list(l.get("impacts") or [], limit=3),
                }
                for l in shared_limitations[:8]
            ],
        }],
        "mandatory_reasoning_order_per_paragraph": "concept/méthode → paramètres/conditions → protocole/test → résultat/métrique → limite/transposition → lien CIR",
        "technical_no_loss_policy": {
            "use_raw_numeric_or_value_mentions_when_metrics_detected_empty": True,
            "use_open_domain_technical_evidence_when_unclassified": True,
            "keep_missing_details_as_uncertainty_not_as_failure": True,
            "never_invent_missing_parameters": True,
        },
        "do_not_write": [
            "Ne pas écrire 'La narration scientifique doit...'.",
            "Ne pas copier les phrases répétitives de Phase 4.6.",
            "Ne pas regrouper plus de 3 citations dans une phrase.",
            "Ne pas affirmer une performance ou supériorité sans métrique explicite.",
        ],
    }


def _v30_quality(payload: Dict[str, Any]) -> Dict[str, Any]:
    warnings: List[str] = []
    score = 100.0
    units = payload.get("project_specific_method_story_units") or []
    axes = payload.get("project_specific_story_axes") or []
    if not units:
        score -= 35
        warnings.append("Aucune unité technique issue de Phase 4.5 : Phase 5 risque d'être générique.")
    if not axes:
        score -= 25
        warnings.append("Aucun axe narratif dynamique construit.")
    with_details = [u for u in units if (u.get("must_exploit_in_phase5") or {}).get("parameters_or_conditions") or (u.get("must_exploit_in_phase5") or {}).get("protocol_or_test")]
    with_results = [u for u in units if (u.get("must_exploit_in_phase5") or {}).get("results_linked_to_method_test")]
    with_metrics = [u for u in units if (u.get("must_exploit_in_phase5") or {}).get("metrics_or_values")]
    with_open = [u for u in units if (u.get("must_exploit_in_phase5") or {}).get("open_domain_unclassified_evidence")]
    if units and len(with_details) / max(1, len(units)) < 0.35:
        warnings.append("Peu d'unités avec protocole/paramètres explicites ; vérifier Phase 4.5 ou Article Cards.")
    if units and not with_results:
        warnings.append("Aucun résultat relié méthode-test-métrique détecté ; Phase 5 doit rester prudente.")
        score -= 8
    if units and not with_metrics:
        warnings.append("Aucune métrique/valeur brute détectée ; ne pas inventer de performance.")
    if not payload.get("phase5_consultant_blueprint"):
        score -= 15
        warnings.append("Blueprint Phase 5 manquant.")
    if not payload.get("shared_limitations"):
        score -= 8
        warnings.append("Limites partagées faibles ou absentes.")
    level = "good" if score >= 80 else "medium" if score >= 60 else "weak"
    return {
        "score": round(max(0.0, min(100.0, score)), 2),
        "level": level,
        "warnings": warnings,
        "coverage": {
            "story_units_count": len(units),
            "axes_count": len(axes),
            "units_with_protocol_or_parameters": len(with_details),
            "units_with_result_method_test_links": len(with_results),
            "units_with_metrics_or_values": len(with_metrics),
            "units_with_open_domain_evidence": len(with_open),
        },
    }


def build_markdown_summary(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Phase 4.7 — Project-specific Scientific Narrative V3")
    lines.append("")
    lines.append(f"Payload: `{payload.get('payload_type')}`")
    lines.append(f"OK: `{payload.get('ok')}`")
    q = payload.get("quality") or {}
    lines.append(f"Quality: `{q.get('score')}` / `{q.get('level')}`")
    cov = q.get("coverage") or {}
    if cov:
        lines.append(f"Coverage: units `{cov.get('story_units_count')}`, axes `{cov.get('axes_count')}`, results `{cov.get('units_with_result_method_test_links')}`, metrics/values `{cov.get('units_with_metrics_or_values')}`")
    lines.append("")

    story = payload.get("project_specific_storyline") or {}
    lines.append("## Histoire scientifique propre au projet")
    lines.append(clean_sentence(story.get("narrative_principle")) or "Non disponible")
    lines.append("")
    lines.append("### Axes dynamiques à donner à Phase 5")
    for p in story.get("project_axes_progression") or []:
        lines.append(f"- **{p.get('visible_title_suggestion')}** — citations: {', '.join(p.get('citations') or [])}")
        if p.get("must_use_result_metric_links"):
            lines.append(f"  - résultats liés à exploiter: `{len(p.get('must_use_result_metric_links') or [])}`")
        if p.get("must_use_details"):
            lines.append(f"  - détails techniques à exploiter: `{len(p.get('must_use_details') or [])}`")
    lines.append("")

    lines.append("## Unités méthode → test → résultat → limite")
    for u in (payload.get("project_specific_method_story_units") or [])[:15]:
        flags = u.get("must_exploit_in_phase5") or {}
        lines.append(f"- **{u.get('citation')}** — {u.get('concept')} — stage `{u.get('stage')}` — richness `{u.get('evidence_richness_score')}`")
        bits = []
        if flags.get("protocol_or_test"):
            bits.append("protocole/test")
        if flags.get("parameters_or_conditions"):
            bits.append("paramètres")
        if flags.get("metrics_or_values"):
            bits.append("métriques/valeurs")
        if flags.get("results_linked_to_method_test"):
            bits.append("résultat lié")
        if flags.get("open_domain_unclassified_evidence"):
            bits.append("preuves non classées")
        if bits:
            lines.append("  - à exploiter: " + ", ".join(bits))
    lines.append("")

    lines.append("## Limites à transformer en gap")
    for l in (payload.get("shared_limitations") or [])[:8]:
        lines.append(f"- **{l.get('label')}** — citations: {', '.join(l.get('citations') or [])}")
    lines.append("")

    if q.get("warnings"):
        lines.append("## Warnings")
        for w in q.get("warnings") or []:
            lines.append(f"- {w}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_scientific_narrative_payload(
    *,
    organisme: str,
    project: str,
    year: str,
    phase_4_5_path: Optional[str] = None,
    phase_4_6_path: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    p45_path = Path(phase_4_5_path) if phase_4_5_path else default_phase_4_5_path(organisme, project, year)
    p46_path = Path(phase_4_6_path) if phase_4_6_path else default_phase_4_6_path(organisme, project, year)

    phase45 = read_json(p45_path, {}) or {}
    phase46 = read_json(p46_path, {}) or {}

    verrou_index = build_verrou_index(phase45, phase46)
    if not verrou_index:
        return {
            "ok": False,
            "phase": "phase_4_7_scientific_narrative_builder",
            "payload_type": OUTPUT_PAYLOAD_TYPE,
            "generated_at": now_iso(),
            "organisme": organisme,
            "project": project,
            "year": year,
            "error": "Aucun verrou trouvé dans Phase 4.5 ou Phase 4.6.",
            "input_paths": {"phase_4_5": str(p45_path), "phase_4_6": str(p46_path)},
        }

    # Graphs kept for compatibility and audit.
    family_graph = build_family_graph(verrou_index)
    concept_graph = build_concept_graph(verrou_index, family_graph)
    comparison_graph = build_comparison_graph(verrou_index, family_graph)
    shared_limitations = build_shared_limitations(verrou_index)
    scientific_consensus = build_scientific_consensus(family_graph, shared_limitations, concept_graph)
    scientific_contradictions = build_scientific_contradictions(comparison_graph, shared_limitations)
    cross_verrou_reasoning = build_cross_verrou_reasoning(verrou_index, family_graph, shared_limitations)
    scientific_progression = build_scientific_progression(family_graph, concept_graph, comparison_graph)
    remaining_unknowns = build_remaining_unknowns(verrou_index, shared_limitations)
    scientific_story = build_scientific_story(
        verrou_index,
        family_graph,
        concept_graph,
        shared_limitations,
        scientific_progression,
        remaining_unknowns,
    )

    project_specific_units = _v30_collect_project_specific_story_units(verrou_index)
    project_axes = _v30_group_units_into_project_axes(project_specific_units)
    project_storyline = _v30_build_project_storyline(verrou_index, project_axes, shared_limitations)
    phase5_consultant_blueprint = _v30_build_phase5_blueprint(verrou_index, project_axes, project_storyline, shared_limitations)

    # Legacy fields still generated so old Phase 5 does not break, but V3 fields are primary.
    macro_scientific_knowledge_base = build_macro_scientific_knowledge_base(
        verrou_index,
        family_graph,
        concept_graph,
        shared_limitations,
    )
    scientific_knowledge_base = build_scientific_knowledge_base(
        verrou_index,
        family_graph,
        concept_graph,
        shared_limitations,
    )
    conceptual_comparison_graph = build_conceptual_comparison_graph_v21(
        comparison_graph,
        macro_scientific_knowledge_base,
    )
    consultant_storyline = build_consultant_storyline_v21(
        verrou_index,
        macro_scientific_knowledge_base,
        conceptual_comparison_graph,
        shared_limitations,
    )
    phase5_writer_blueprint = build_phase5_writer_blueprint(
        verrou_index,
        scientific_story,
        family_graph,
        concept_graph,
        comparison_graph,
        shared_limitations,
        cross_verrou_reasoning,
    )
    citation_index = build_global_citation_index(verrou_index)

    out_dir = output_dir(organisme, project, year)
    output_path = out_dir / "scientific_narrative_payload.json"
    markdown_output_path = out_dir / "scientific_narrative_summary.md"

    payload: Dict[str, Any] = {
        "ok": True,
        "phase": "phase_4_7_scientific_narrative_builder",
        "step": "build_scientific_narrative_payload",
        "payload_type": OUTPUT_PAYLOAD_TYPE,
        "generated_at": now_iso(),
        "organisme": organisme,
        "project": project,
        "year": year,
        "dry_run": dry_run,
        "input_paths": {
            "phase_4_5_scientific_reasoning": str(p45_path),
            "phase_4_6_project_rd_argumentation": str(p46_path),
        },
        "verrous_count": len(verrou_index),
        "verrou_index": [
            {
                "verrou_id": v.get("verrou_id"),
                "verrou_title": v.get("verrou_title"),
                "has_phase_4_5": bool(v.get("phase_4_5")),
                "has_phase_4_6": bool(v.get("phase_4_6")),
            }
            for v in verrou_index
        ],
        # V3 primary fields for Phase 5.
        "project_specific_storyline": project_storyline,
        "project_specific_story_axes": project_axes,
        "project_specific_method_story_units": project_specific_units,
        "phase5_consultant_blueprint": phase5_consultant_blueprint,
        # Compatibility / audit fields.
        "scientific_story": scientific_story,
        "macro_scientific_knowledge_base": macro_scientific_knowledge_base,
        "scientific_knowledge_base": scientific_knowledge_base,
        "conceptual_comparison_graph": conceptual_comparison_graph,
        "consultant_storyline": consultant_storyline,
        "family_graph": family_graph,
        "concept_graph": concept_graph,
        "comparison_graph": comparison_graph,
        "shared_limitations": shared_limitations,
        "scientific_consensus": scientific_consensus,
        "scientific_contradictions": scientific_contradictions,
        "cross_verrou_reasoning": cross_verrou_reasoning,
        "scientific_progression": scientific_progression,
        "remaining_unknowns": remaining_unknowns,
        "global_citation_index": citation_index,
        "phase5_writer_blueprint": phase5_writer_blueprint,
        "rules": {
            "phase_4_7_role": "define_project_specific_story_and_evidence_chain_not_final_writing",
            "uses_phase_4_5": True,
            "uses_phase_4_6": True,
            "uses_phase45_v23_technical_details": True,
            "uses_phase46_phase5_summaries": True,
            "does_not_create_new_scientific_evidence": True,
            "does_not_move_citations_between_verrous": True,
            "no_domain_hardcoding": True,
            "project_specific_axes_are_primary": True,
            "technical_no_loss_policy_enabled": True,
            "open_domain_unclassified_evidence_kept": True,
            "results_must_be_linked_to_method_test_metric_when_available": True,
            "metrics_empty_does_not_mean_result_empty": True,
            "phase5_must_not_copy_phase46_sections": True,
            "final_writer_language": "fr",
        },
        "output_path": str(output_path),
        "markdown_output_path": str(markdown_output_path),
    }
    payload["quality"] = _v30_quality(payload)
    payload["ok"] = payload["quality"]["score"] >= 60

    if not dry_run:
        write_json(output_path, payload)
        write_text(markdown_output_path, build_markdown_summary(payload))

    return payload


# ============================================================
# V3.1 — Project-specific strict axes, no weak evidence as main axis
# ============================================================

OUTPUT_PAYLOAD_TYPE = "scientific_narrative_payload_v3_1_project_specific_strict_axes_no_weak_main"

_V31_GENERIC_LABEL_PATTERNS = [
    r"concept\s+ou\s+m[ée]thode", r"concept\s+non\s+nomm[ée]", r"m[ée]thode\s+non\s+nomm[ée]",
    r"scientific\s+concept", r"methodological\s+context", r"contexte\s+m[ée]thodologique\s+g[ée]n[ée]ral",
    r"validation,?\s+robustesse\s+et\s+g[ée]n[ée]ralisation",
]

_V31_STOP_TERMS = {
    "article", "articles", "method", "methods", "methode", "methodes", "méthode", "méthodes", "approach", "approche",
    "scientifique", "scientific", "concept", "concepts", "technique", "techniques", "result", "results", "résultat", "resultat",
    "validation", "protocole", "test", "tests", "model", "models", "modèle", "modele", "projet", "verrou", "phase",
    "data", "données", "donnees", "evidence", "preuve", "preuves", "source", "sources", "context", "contexte",
    "based", "using", "pour", "avec", "dans", "sur", "entre", "sans", "plus", "moins", "this", "that",
}


def _v31_norm_token(t: Any) -> str:
    s = clean_sentence(t).lower()
    s = s.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a").replace("ù", "u").replace("ç", "c")
    s = re.sub(r"[^a-z0-9_+\-/]+", "", s)
    return s.strip("_-/")


def _v31_tokens(text: Any) -> List[str]:
    toks = []
    for t in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9_+\-/]{2,}", clean_sentence(text)):
        nt = _v31_norm_token(t)
        if not nt or nt in _V31_STOP_TERMS or len(nt) < 2:
            continue
        toks.append(nt)
    return toks


def _v31_unit_text(unit: Dict[str, Any], *, include_open: bool = True) -> str:
    parts: List[str] = []
    for key in [
        "concept", "technical_family_internal", "scientific_principle", "mechanism_chain", "data_context",
        "validation_logic", "demonstrated_or_reported_result", "project_gap_link", "problem_to_solve",
        "solution_or_contribution",
    ]:
        parts.append(clean_sentence(unit.get(key)))
    td = unit.get("technical_detail_profile_for_phase5") or {}
    if isinstance(td, dict):
        for key in [
            "method_or_process_details", "protocol_or_test_details", "materials_data_or_samples",
            "parameters_conditions_or_settings", "instrumentation_or_tools", "simulation_or_implementation_details",
        ]:
            parts += _v30_clean_list(td.get(key), limit=10, max_chars=450)
    for rc in as_list(unit.get("result_claims_linked_to_method_test_metric")):
        if isinstance(rc, dict):
            parts.append(clean_sentence(rc.get("result_text")))
            parts += _v30_clean_list(rc.get("linked_method_or_technology_context"), limit=4, max_chars=300)
            parts += _v30_clean_list(rc.get("linked_test_or_validation_context"), limit=4, max_chars=300)
            mv = rc.get("metrics_and_values") if isinstance(rc.get("metrics_and_values"), dict) else {}
            parts += _v30_clean_list(mv.get("raw_numeric_or_value_mentions"), limit=8, max_chars=120)
            parts += _v30_clean_list(mv.get("metrics_detected_or_mentioned"), limit=6, max_chars=250)
    if include_open:
        od = unit.get("open_domain_technical_evidence") or {}
        if isinstance(od, dict):
            parts += _v30_clean_list(od.get("unclassified_technical_snippets"), limit=8, max_chars=300)
            parts += _v30_clean_list(od.get("explicit_numeric_expressions"), limit=8, max_chars=120)
            parts += _v30_clean_list(od.get("named_variables_or_parameters"), limit=8, max_chars=120)
    return " ".join(x for x in parts if x)


def _v31_project_anchor_terms(verrou_index: List[Dict[str, Any]], units: List[Dict[str, Any]]) -> List[str]:
    """Build dominant project vocabulary from project context + high-richness units.
    No domain terms are hardcoded; anchors come from current dossier text.
    """
    texts: List[str] = []
    for v in verrou_index:
        texts.append(v.get("verrou_title") or "")
        aj = v.get("argumentation_json") or {}
        texts += [aj.get("rd_gap") or "", aj.get("verrou_title") or ""]
        sections = v.get("project_sections") or {}
        for k in ["section_1_besoin_projet", "section_2_pourquoi_besoin_pose_verrou", "section_3_ce_que_etat_art_sait_deja_faire", "section_5_gap_rd"]:
            texts.append(sections.get(k) or "")
    # Only richer units to avoid polluting anchors with weak/off-domain sources.
    for u in sorted(units, key=lambda x: -int(x.get("evidence_richness_score") or 0))[: max(10, min(28, len(units)))]:
        texts.append(_v31_unit_text(u, include_open=False))
    counts = Counter()
    for txt in texts:
        counts.update(_v31_tokens(txt))
    anchors = [t for t, c in counts.most_common(45) if c >= 2 or (len(t) <= 6 and c >= 1)]
    return anchors[:35]


def _v31_label_is_weak(label: Any) -> bool:
    s = clean_sentence(label)
    if not s:
        return True
    low = s.lower()
    if any(re.search(p, low, flags=re.I) for p in _V31_GENERIC_LABEL_PATTERNS):
        return True
    toks = _v31_tokens(s)
    # One short ambiguous token such as "CP" should not become an axis by itself.
    if len(toks) <= 1 and len(s) <= 4:
        return True
    if len(s.split()) > 14:
        return True
    return False


def _v31_project_affinity(unit: Dict[str, Any], anchors: List[str]) -> Dict[str, Any]:
    unit_tokens = set(_v31_tokens(_v31_unit_text(unit, include_open=False)))
    anchor_set = set(anchors)
    matched = sorted(unit_tokens & anchor_set)
    ratio = len(matched) / max(1, min(len(unit_tokens), len(anchor_set)))
    return {"score": round(ratio, 3), "matched_terms": matched[:12]}


def _v31_specificity(unit: Dict[str, Any]) -> int:
    score = int(unit.get("evidence_richness_score") or 0)
    label = clean_sentence(unit.get("concept"))
    if _v31_label_is_weak(label):
        score -= 25
    flags = unit.get("must_exploit_in_phase5") or {}
    for k, add in [
        ("protocol_or_test", 7),
        ("parameters_or_conditions", 7),
        ("metrics_or_values", 8),
        ("results_linked_to_method_test", 10),
        ("open_domain_unclassified_evidence", 4),
    ]:
        if flags.get(k):
            score += add
    if unit.get("usage_type") == "direct_evidence":
        score += 6
    return max(0, score)


def _v31_unit_main_status(unit: Dict[str, Any], anchors: List[str]) -> Dict[str, Any]:
    affinity = _v31_project_affinity(unit, anchors)
    specificity = _v31_specificity(unit)
    weak_label = _v31_label_is_weak(unit.get("concept"))
    has_result = bool((unit.get("must_exploit_in_phase5") or {}).get("results_linked_to_method_test"))
    has_protocol = bool((unit.get("must_exploit_in_phase5") or {}).get("protocol_or_test"))
    has_metric = bool((unit.get("must_exploit_in_phase5") or {}).get("metrics_or_values"))
    if weak_label and affinity["score"] < 0.12:
        status = "traceability_only_weak_label"
    elif affinity["score"] < 0.045 and specificity < 88:
        status = "traceability_only_low_project_affinity"
    elif not (has_result or has_protocol or has_metric) and affinity["score"] < 0.08:
        status = "support_only_insufficient_chain"
    else:
        status = "main_story_candidate"
    return {
        "main_status": status,
        "project_affinity": affinity,
        "specificity_score": specificity,
        "weak_label": weak_label,
        "use_as_main_axis": status == "main_story_candidate",
    }


def _v31_role_from_unit(unit: Dict[str, Any]) -> str:
    """Infer scientific function from extracted evidence, not from citation labels."""
    txt = _v31_unit_text(unit).lower()
    td = unit.get("technical_detail_profile_for_phase5") or {}
    has_protocol = bool(_v30_clean_list(td.get("protocol_or_test_details"), limit=3))
    has_params = bool(_v30_clean_list(td.get("parameters_conditions_or_settings"), limit=3))
    has_samples = bool(_v30_clean_list(td.get("materials_data_or_samples"), limit=3)) or bool(clean_sentence(unit.get("data_context")))
    has_results = bool(unit.get("result_claims_linked_to_method_test_metric")) or bool(clean_sentence(unit.get("demonstrated_or_reported_result")))
    # Generic scientific functions. These are not project-specific terms; they identify the role of the evidence.
    if any(k in txt for k in ["cpu", "gpu", "runtime", "memory", "mémoire", "compute", "calcul", "implementation", "implémentation", "mesh", "maillage", "solver", "solveur", "complexity", "complexité"]):
        return "contraintes_de_calcul_implementation_et_parametrage"
    if any(k in txt for k in ["synthetic", "synthétique", "synthetique", "generation", "génération", "augmentation", "transformation", "mask", "masque", "adversarial", "variant", "simulation", "render", "rendu"]):
        return "generation_transformation_et_enrichissement_des_cas"
    if any(k in txt for k in ["classification", "classifier", "classifieur", "recognition", "reconnaissance", "learning", "apprentissage", "model", "modèle", "modele", "architecture", "network", "réseau", "reseau", "training", "entrainement"]):
        return "methodes_modeles_et_mecanismes_de_decision"
    if has_results or has_protocol or any(k in txt for k in ["validation", "evaluation", "évaluation", "metric", "métrique", "accuracy", "error", "rmse", "robust", "generalization", "généralisation", "benchmark", "test"]):
        return "protocoles_tests_resultats_et_validite"
    if has_samples or any(k in txt for k in ["data", "dataset", "donnée", "donnee", "sample", "échantillon", "echantillon", "material", "matériau", "materiau", "signal", "image", "represent", "représent"]):
        return "donnees_echantillons_representativite"
    if has_params:
        return "parametres_conditions_et_variables_techniques"
    return "mecanismes_scientifiques_extraits_du_dossier"


def _v31_axis_title(role: str, units: List[Dict[str, Any]], anchors: List[str]) -> str:
    texts = []
    for u in units[:8]:
        texts.append(clean_sentence(u.get("concept")))
        texts.append(clean_sentence(u.get("technical_family_internal")))
    counts = Counter()
    anchor_set = set(anchors)
    for txt in texts:
        for t in _v31_tokens(txt):
            if t in anchor_set or len(t) >= 5:
                counts[t] += 1
    top_terms = [t for t, c in counts.most_common(5) if t not in _V31_STOP_TERMS]
    pretty_terms = []
    # Keep original casing from concepts when possible.
    for term in top_terms:
        found = ""
        for txt in texts:
            m = re.search(rf"\b{re.escape(term)}\b", txt, flags=re.I)
            if m:
                found = m.group(0)
                break
        pretty_terms.append(found or term.upper() if len(term) <= 4 else term)
    topic = " / ".join(unique_clean_list(pretty_terms, limit=3))
    role_titles = {
        "donnees_echantillons_representativite": "Représentativité des données et conditions d'observation",
        "methodes_modeles_et_mecanismes_de_decision": "Méthodes, modèles et mécanismes de décision",
        "generation_transformation_et_enrichissement_des_cas": "Génération, transformation et enrichissement des cas",
        "protocoles_tests_resultats_et_validite": "Protocoles de test, résultats et validité expérimentale",
        "contraintes_de_calcul_implementation_et_parametrage": "Contraintes de calcul, implémentation et paramétrage",
        "parametres_conditions_et_variables_techniques": "Paramètres, conditions et variables techniques",
        "mecanismes_scientifiques_extraits_du_dossier": "Mécanismes scientifiques extraits du dossier",
    }
    base = role_titles.get(role, role.replace("_", " "))
    return f"{base} — {topic}" if topic else base


def _v31_build_strict_project_axes(units: List[Dict[str, Any]], shared_limitations: List[Dict[str, Any]], verrou_index: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    anchors = _v31_project_anchor_terms(verrou_index, units)
    main_units: List[Dict[str, Any]] = []
    trace_units: List[Dict[str, Any]] = []
    for u in units:
        uu = dict(u)
        status = _v31_unit_main_status(uu, anchors)
        uu["v31_story_filter"] = status
        uu["v31_functional_role"] = _v31_role_from_unit(uu)
        if status["use_as_main_axis"]:
            main_units.append(uu)
        else:
            trace_units.append(uu)
    if not main_units and units:
        # Fallback: keep top 5 richest to avoid empty output, but flag it.
        main_units = sorted([dict(u, v31_story_filter=_v31_unit_main_status(u, anchors), v31_functional_role=_v31_role_from_unit(u)) for u in units], key=lambda x: -int(x.get("evidence_richness_score") or 0))[:5]
        trace_units = [u for u in units if u not in main_units]

    role_order = [
        "donnees_echantillons_representativite",
        "generation_transformation_et_enrichissement_des_cas",
        "methodes_modeles_et_mecanismes_de_decision",
        "protocoles_tests_resultats_et_validite",
        "contraintes_de_calcul_implementation_et_parametrage",
        "parametres_conditions_et_variables_techniques",
        "mecanismes_scientifiques_extraits_du_dossier",
    ]
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for u in main_units:
        grouped[u.get("v31_functional_role") or "mecanismes_scientifiques_extraits_du_dossier"].append(u)
    axes: List[Dict[str, Any]] = []
    for role in role_order:
        us = grouped.get(role) or []
        if not us:
            continue
        us = sorted(us, key=lambda x: (-int((x.get("v31_story_filter") or {}).get("specificity_score") or 0), citation_sort_key(x.get("citation_label"))))
        td: List[str] = []
        results: List[Dict[str, Any]] = []
        open_ev: List[str] = []
        concepts: List[str] = []
        for u in us:
            concepts.append(u.get("concept"))
            profile = u.get("technical_detail_profile_for_phase5") or {}
            if isinstance(profile, dict):
                for key in ["protocol_or_test_details", "materials_data_or_samples", "parameters_conditions_or_settings", "instrumentation_or_tools", "simulation_or_implementation_details", "method_or_process_details"]:
                    td += _v30_clean_list(profile.get(key), limit=5, max_chars=350)
            for rc in as_list(u.get("result_claims_linked_to_method_test_metric"))[:3]:
                if isinstance(rc, dict):
                    results.append({
                        "citation": u.get("citation"),
                        "result_text": truncate(rc.get("result_text"), 450),
                        "metrics_and_values": rc.get("metrics_and_values"),
                        "linked_method_or_technology_context": rc.get("linked_method_or_technology_context"),
                        "linked_test_or_validation_context": rc.get("linked_test_or_validation_context"),
                        "link_confidence": rc.get("link_confidence"),
                    })
            od = u.get("open_domain_technical_evidence") or {}
            open_ev += _v30_clean_list(od.get("unclassified_technical_snippets"), limit=4, max_chars=300)
        axis = {
            "axis_id": f"axis_{len(axes)+1:02d}",
            "axis_type": role,
            "visible_title_suggestion": _v31_axis_title(role, us, anchors),
            "dominant_project_terms": [t for t in anchors if any(t in _v31_tokens(_v31_unit_text(u, include_open=False)) for u in us)][:10],
            "concepts_to_weave": unique_clean_list(concepts, limit=10),
            "citations": safe_citations([u.get("citation_label") for u in us], limit=12),
            "story_units_main": us[:8],
            "story_units_support": us[8:14],
            "technical_details_to_exploit": unique_clean_list(td, limit=18),
            "result_metric_links_to_exploit": results[:10],
            "open_domain_evidence_to_keep": unique_clean_list(open_ev, limit=10),
            "writer_goal": _v31_writer_goal_for_axis(role),
            "writer_instruction": (
                "Écrire un paragraphe/une sous-section fondée sur les détails techniques réels de Phase 4.5 : méthode/procédé, paramètres, protocole/test, résultat et limite. "
                "Ne pas transformer les références faibles ou peu alignées en axe principal."
            ),
        }
        axes.append(axis)
    # Attach traceability units by citation; not used as main story axes.
    trace_units = sorted(trace_units, key=lambda x: (-int(x.get("evidence_richness_score") or 0), citation_sort_key(x.get("citation_label"))))
    return axes, trace_units, anchors


def _v31_writer_goal_for_axis(role: str) -> str:
    mapping = {
        "donnees_echantillons_representativite": "Montrer comment les données, échantillons, signaux ou matériaux conditionnent la validité de l'état de l'art.",
        "generation_transformation_et_enrichissement_des_cas": "Expliquer les mécanismes d'enrichissement, génération ou transformation et les relier aux cas couverts ou non couverts.",
        "methodes_modeles_et_mecanismes_de_decision": "Présenter les méthodes ou modèles comme mécanismes scientifiques, avec leurs conditions d'utilisation et leurs limites.",
        "protocoles_tests_resultats_et_validite": "Relier explicitement protocole/test, résultat, métrique ou valeur brute, puis discuter la validité de transposition.",
        "contraintes_de_calcul_implementation_et_parametrage": "Expliquer les contraintes de calcul, implémentation, paramétrage ou simulation comme conditions de faisabilité et de représentativité.",
        "parametres_conditions_et_variables_techniques": "Mettre en avant les paramètres, variables ou conditions expérimentales qui déterminent les résultats.",
        "mecanismes_scientifiques_extraits_du_dossier": "Expliquer les mécanismes réellement extraits du dossier en évitant toute généralité non sourcée.",
    }
    return mapping.get(role, "Construire un axe de narration à partir des preuves techniques réellement extraites.")


def _v31_refine_shared_limitations(shared_limitations: List[Dict[str, Any]], axes: List[Dict[str, Any]], trace_units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    main_cits = set(sum([as_list(a.get("citations")) for a in axes], []))
    trace_cits = set(normalize_citation_label(u.get("citation_label")) for u in trace_units if normalize_citation_label(u.get("citation_label")))
    refined: List[Dict[str, Any]] = []
    for l in shared_limitations:
        if not isinstance(l, dict):
            continue
        cits = safe_citations(l.get("citations") or [])
        main = safe_citations([c for c in cits if c in main_cits], limit=12)
        trace = safe_citations([c for c in cits if c in trace_cits or c not in main_cits], limit=20)
        if not main and cits:
            main = cits[:4]
            trace = cits[4:]
        ll = dict(l)
        ll["citations"] = main
        ll["citations_traceability_only_not_main_gap_proof"] = trace
        ll["phase5_usage_instruction"] = (
            "Utiliser les citations principales pour soutenir la limite. Les citations de traçabilité sont conservées, mais ne doivent pas devenir preuves principales du gap."
        )
        refined.append(ll)
    return refined


def _v31_build_storyline(verrou_index: List[Dict[str, Any]], axes: List[Dict[str, Any]], trace_units: List[Dict[str, Any]], shared_limitations: List[Dict[str, Any]], anchors: List[str]) -> Dict[str, Any]:
    project_need, project_gap = extract_project_need_and_gap(verrou_index)
    progression = []
    for i, a in enumerate(axes, 1):
        progression.append({
            "step": i,
            "axis_id": a.get("axis_id"),
            "visible_title_suggestion": a.get("visible_title_suggestion"),
            "axis_type": a.get("axis_type"),
            "writer_goal": a.get("writer_goal"),
            "dominant_project_terms": a.get("dominant_project_terms") or [],
            "citations": a.get("citations") or [],
            "technical_details_to_use": a.get("technical_details_to_exploit")[:8],
            "result_metric_links_to_use": a.get("result_metric_links_to_exploit")[:5],
            "transition_to_next": _v31_transition(a, axes[i] if i < len(axes) else None),
        })
    return {
        "storyline_type": "project_specific_scientific_storyline_v3_1_strict_axes",
        "goal": "Construire l'histoire scientifique du projet à partir des unités techniques robustes et alignées, sans axes faibles ou hors domaine comme preuves principales.",
        "project_need": project_need,
        "project_gap": project_gap,
        "dominant_project_terms": anchors[:20],
        "project_axes_progression": progression,
        "traceability_only_units_not_to_use_as_main_axes": [
            {
                "citation": u.get("citation"),
                "concept": u.get("concept"),
                "reason": (u.get("v31_story_filter") or {}).get("main_status"),
                "project_affinity": (u.get("v31_story_filter") or {}).get("project_affinity"),
            }
            for u in trace_units[:20]
        ],
        "limitations_to_turn_into_gap": [clean_sentence(l.get("label")) for l in shared_limitations[:8] if clean_sentence(l.get("label"))],
        "strict_rules": [
            "Ne pas utiliser les unités traceability_only comme preuves principales dans le texte final.",
            "Ne pas afficher des axes génériques si un axe spécifique peut être formulé à partir des termes du projet.",
            "Ne pas perdre les résultats : utiliser result_text et raw_numeric_or_value_mentions même si metrics_detected est vide.",
            "Ne pas perdre les paramètres/protocoles : utiliser technical_details_to_exploit et open_domain_evidence_to_keep.",
            "Ne pas faire une fiche par article : transformer les unités en raisonnement méthode → test → résultat → limite.",
        ],
    }


def _v31_transition(cur: Dict[str, Any], nxt: Optional[Dict[str, Any]]) -> str:
    if not nxt:
        return "Cette étape doit conduire vers les limites de transposition et le gap R&D du projet."
    return f"Après {cur.get('visible_title_suggestion')}, la narration peut passer à {nxt.get('visible_title_suggestion')} pour montrer la progression des acquis vers leur validation."


def _v31_build_phase5_blueprint(verrou_index: List[Dict[str, Any]], axes: List[Dict[str, Any]], storyline: Dict[str, Any], refined_limitations: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "blueprint_type": "phase5_writer_blueprint_v3_1_project_specific_strict_axes",
        "core_change": "Phase 5 doit suivre uniquement les axes principaux V3.1 et utiliser les unités faibles en traçabilité, pas comme preuves centrales.",
        "project_need": storyline.get("project_need"),
        "project_gap": storyline.get("project_gap"),
        "visible_section_order": [a.get("axis_id") for a in axes] + ["insuffisances_et_gap_rd"],
        "section_guidance": [
            {
                "section_id": a.get("axis_id"),
                "title_suggestion": a.get("visible_title_suggestion"),
                "axis_type": a.get("axis_type"),
                "goal": a.get("writer_goal"),
                "dominant_project_terms": a.get("dominant_project_terms") or [],
                "citations_to_use_as_main_support": a.get("citations") or [],
                "technical_details_to_use": a.get("technical_details_to_exploit")[:12],
                "result_metric_links_to_use": a.get("result_metric_links_to_exploit")[:8],
                "open_domain_evidence_to_keep": a.get("open_domain_evidence_to_keep")[:8],
                "story_units_main": a.get("story_units_main")[:6],
                "must_not": [
                    "ne pas utiliser les citations traceability_only comme preuves principales",
                    "ne pas écrire un paragraphe par article",
                    "ne pas ignorer les résultats qualitatifs si les métriques sont vides",
                    "ne jamais inventer un paramètre absent",
                ],
            }
            for a in axes
        ] + [{
            "section_id": "insuffisances_et_gap_rd",
            "title_suggestion": "Insuffisances de l'état de l'art et gap R&D",
            "goal": "Transformer les limites prouvées par les axes principaux en justification CIR.",
            "limitations_to_integrate": [
                {
                    "label": l.get("label"),
                    "main_citations": safe_citations(l.get("citations") or [], limit=5),
                    "traceability_only_citations": safe_citations(l.get("citations_traceability_only_not_main_gap_proof") or [], limit=10),
                    "impacts": unique_clean_list(l.get("impacts") or [], limit=3),
                }
                for l in refined_limitations[:8]
            ],
        }],
        "technical_no_loss_policy": {
            "keep_traceability_units": True,
            "but_do_not_use_weak_or_low_affinity_units_as_main_axes": True,
            "use_raw_numeric_or_value_mentions_when_metrics_detected_empty": True,
            "use_open_domain_technical_evidence_when_unclassified": True,
            "never_invent_missing_parameters": True,
        },
    }


def _v31_quality(payload: Dict[str, Any]) -> Dict[str, Any]:
    warnings: List[str] = []
    score = 100.0
    units = payload.get("project_specific_method_story_units") or []
    axes = payload.get("project_specific_story_axes") or []
    trace = payload.get("traceability_only_units") or []
    if not units:
        score -= 35
        warnings.append("Aucune unité technique issue de Phase 4.5.")
    if not axes:
        score -= 30
        warnings.append("Aucun axe principal V3.1 construit.")
    if axes and len(axes) < 3 and len(units) >= 15:
        score -= 6
        warnings.append("Axes principaux peu nombreux par rapport au volume d'unités ; vérifier le filtrage.")
    if trace:
        warnings.append(f"{len(trace)} unité(s) conservée(s) en traçabilité seulement pour éviter les preuves faibles/hors alignement en axe principal.")
    weak_axes = [a for a in axes if _v31_label_is_weak(a.get("visible_title_suggestion"))]
    if weak_axes:
        score -= 12
        warnings.append("Certains axes ont encore un titre faible/générique.")
    with_results = sum(1 for a in axes if a.get("result_metric_links_to_exploit"))
    if axes and with_results == 0:
        score -= 8
        warnings.append("Aucun axe principal avec résultat lié méthode-test-métrique.")
    level = "good" if score >= 80 else "medium" if score >= 60 else "weak"
    return {
        "score": round(max(0.0, min(100.0, score)), 2),
        "level": level,
        "warnings": warnings,
        "coverage": {
            "story_units_count": len(units),
            "main_axes_count": len(axes),
            "traceability_only_units_count": len(trace),
            "axes_with_result_links": with_results,
            "axes_with_technical_details": sum(1 for a in axes if a.get("technical_details_to_exploit")),
        },
    }


def build_markdown_summary(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Phase 4.7 — Project-specific Scientific Narrative V3.1")
    lines.append("")
    lines.append(f"Payload: `{payload.get('payload_type')}`")
    lines.append(f"OK: `{payload.get('ok')}`")
    q = payload.get("quality") or {}
    lines.append(f"Quality: `{q.get('score')}` / `{q.get('level')}`")
    cov = q.get("coverage") or {}
    if cov:
        lines.append(f"Coverage: units `{cov.get('story_units_count')}`, main axes `{cov.get('main_axes_count')}`, traceability-only `{cov.get('traceability_only_units_count')}`, axes with results `{cov.get('axes_with_result_links')}`")
    lines.append("")
    story = payload.get("project_specific_storyline") or {}
    lines.append("## Histoire scientifique propre au projet — axes principaux")
    lines.append(clean_sentence(story.get("goal")) or "Non disponible")
    lines.append("")
    lines.append("### Axes V3.1 à donner à Phase 5")
    for p in story.get("project_axes_progression") or []:
        lines.append(f"- **{p.get('visible_title_suggestion')}** — citations principales: {', '.join(p.get('citations') or [])}")
        if p.get("technical_details_to_use"):
            lines.append(f"  - détails techniques à exploiter: `{len(p.get('technical_details_to_use') or [])}`")
        if p.get("result_metric_links_to_use"):
            lines.append(f"  - résultats/métriques liés à exploiter: `{len(p.get('result_metric_links_to_use') or [])}`")
    lines.append("")
    trace = story.get("traceability_only_units_not_to_use_as_main_axes") or []
    if trace:
        lines.append("## Unités conservées en traçabilité seulement")
        for u in trace[:15]:
            lines.append(f"- **{u.get('citation')}** — {u.get('concept')} — raison: `{u.get('reason')}`")
        lines.append("")
    lines.append("## Limites à transformer en gap")
    for l in (payload.get("shared_limitations") or [])[:8]:
        main = ", ".join(l.get("citations") or [])
        trace_c = ", ".join((l.get("citations_traceability_only_not_main_gap_proof") or [])[:8])
        lines.append(f"- **{l.get('label')}** — citations principales: {main}")
        if trace_c:
            lines.append(f"  - traçabilité seulement: {trace_c}")
    lines.append("")
    if q.get("warnings"):
        lines.append("## Warnings")
        for w in q.get("warnings") or []:
            lines.append(f"- {w}")
    return "\n".join(lines).strip() + "\n"


def build_scientific_narrative_payload(
    *,
    organisme: str,
    project: str,
    year: str,
    phase_4_5_path: Optional[str] = None,
    phase_4_6_path: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    p45_path = Path(phase_4_5_path) if phase_4_5_path else default_phase_4_5_path(organisme, project, year)
    p46_path = Path(phase_4_6_path) if phase_4_6_path else default_phase_4_6_path(organisme, project, year)
    phase45 = read_json(p45_path, {}) or {}
    phase46 = read_json(p46_path, {}) or {}
    verrou_index = build_verrou_index(phase45, phase46)
    if not verrou_index:
        return {
            "ok": False,
            "phase": "phase_4_7_scientific_narrative_builder",
            "payload_type": OUTPUT_PAYLOAD_TYPE,
            "generated_at": now_iso(),
            "organisme": organisme,
            "project": project,
            "year": year,
            "error": "Aucun verrou trouvé dans Phase 4.5 ou Phase 4.6.",
            "input_paths": {"phase_4_5": str(p45_path), "phase_4_6": str(p46_path)},
        }

    family_graph = build_family_graph(verrou_index)
    concept_graph = build_concept_graph(verrou_index, family_graph)
    comparison_graph = build_comparison_graph(verrou_index, family_graph)
    shared_limitations_raw = build_shared_limitations(verrou_index)
    scientific_consensus = build_scientific_consensus(family_graph, shared_limitations_raw, concept_graph)
    scientific_contradictions = build_scientific_contradictions(comparison_graph, shared_limitations_raw)
    cross_verrou_reasoning = build_cross_verrou_reasoning(verrou_index, family_graph, shared_limitations_raw)
    scientific_progression = build_scientific_progression(family_graph, concept_graph, comparison_graph)
    remaining_unknowns = build_remaining_unknowns(verrou_index, shared_limitations_raw)
    scientific_story = build_scientific_story(verrou_index, family_graph, concept_graph, shared_limitations_raw, scientific_progression, remaining_unknowns)

    # Raw units from V3.0 extraction, then strict V3.1 filtering/grouping.
    raw_units = _v30_collect_project_specific_story_units(verrou_index)
    axes, trace_units, anchors = _v31_build_strict_project_axes(raw_units, shared_limitations_raw, verrou_index)
    refined_limitations = _v31_refine_shared_limitations(shared_limitations_raw, axes, trace_units)
    project_storyline = _v31_build_storyline(verrou_index, axes, trace_units, refined_limitations, anchors)
    phase5_consultant_blueprint = _v31_build_phase5_blueprint(verrou_index, axes, project_storyline, refined_limitations)

    # Keep previous macro/base fields for compatibility.
    macro_scientific_knowledge_base = build_macro_scientific_knowledge_base(verrou_index, family_graph, concept_graph, refined_limitations)
    scientific_knowledge_base = build_scientific_knowledge_base(verrou_index, family_graph, concept_graph, refined_limitations)
    conceptual_comparison_graph = build_conceptual_comparison_graph_v21(comparison_graph, macro_scientific_knowledge_base)
    consultant_storyline = build_consultant_storyline_v21(verrou_index, macro_scientific_knowledge_base, conceptual_comparison_graph, refined_limitations)
    phase5_writer_blueprint = build_phase5_writer_blueprint(verrou_index, scientific_story, family_graph, concept_graph, comparison_graph, refined_limitations, cross_verrou_reasoning)
    citation_index = build_global_citation_index(verrou_index)

    out_dir = output_dir(organisme, project, year)
    output_path = out_dir / "scientific_narrative_payload.json"
    markdown_output_path = out_dir / "scientific_narrative_summary.md"
    payload: Dict[str, Any] = {
        "ok": True,
        "phase": "phase_4_7_scientific_narrative_builder",
        "step": "build_scientific_narrative_payload",
        "payload_type": OUTPUT_PAYLOAD_TYPE,
        "generated_at": now_iso(),
        "organisme": organisme,
        "project": project,
        "year": year,
        "dry_run": dry_run,
        "input_paths": {
            "phase_4_5_scientific_reasoning": str(p45_path),
            "phase_4_6_project_rd_argumentation": str(p46_path),
        },
        "verrous_count": len(verrou_index),
        "verrou_index": [
            {
                "verrou_id": v.get("verrou_id"),
                "verrou_title": v.get("verrou_title"),
                "has_phase_4_5": bool(v.get("phase_4_5")),
                "has_phase_4_6": bool(v.get("phase_4_6")),
            }
            for v in verrou_index
        ],
        "project_specific_storyline": project_storyline,
        "project_specific_story_axes": axes,
        "project_specific_method_story_units": raw_units,
        "traceability_only_units": trace_units,
        "dominant_project_terms": anchors,
        "phase5_consultant_blueprint": phase5_consultant_blueprint,
        "verrou_sections_for_phase5": verrou_sections_for_phase5,
        "verrou_coverage_summary": verrou_coverage_summary,
        "scientific_story": scientific_story,
        "macro_scientific_knowledge_base": macro_scientific_knowledge_base,
        "scientific_knowledge_base": scientific_knowledge_base,
        "conceptual_comparison_graph": conceptual_comparison_graph,
        "consultant_storyline": consultant_storyline,
        "family_graph": family_graph,
        "concept_graph": concept_graph,
        "comparison_graph": comparison_graph,
        "shared_limitations": refined_limitations,
        "scientific_consensus": scientific_consensus,
        "scientific_contradictions": scientific_contradictions,
        "cross_verrou_reasoning": cross_verrou_reasoning,
        "scientific_progression": scientific_progression,
        "remaining_unknowns": remaining_unknowns,
        "global_citation_index": citation_index,
        "phase5_writer_blueprint": phase5_writer_blueprint,
        "rules": {
            "phase_4_7_role": "define_project_specific_story_axes_with_strict_evidence_filter_not_final_writing",
            "uses_phase_4_5": True,
            "uses_phase_4_6": True,
            "uses_phase45_v23_technical_details": True,
            "does_not_create_new_scientific_evidence": True,
            "does_not_move_citations_between_verrous": True,
            "no_domain_hardcoding": True,
            "project_specific_axes_are_primary": True,
            "weak_or_low_affinity_units_kept_as_traceability_not_main_axes": True,
            "technical_no_loss_policy_enabled": True,
            "results_must_be_linked_to_method_test_metric_when_available": True,
            "metrics_empty_does_not_mean_result_empty": True,
            "phase5_must_not_copy_phase46_sections": True,
            "final_writer_language": "fr",
        },
        "output_path": str(output_path),
        "markdown_output_path": str(markdown_output_path),
    }
    payload["quality"] = _v31_quality(payload)
    payload["ok"] = payload["quality"]["score"] >= 60
    if not dry_run:
        write_json(output_path, payload)
        write_text(markdown_output_path, build_markdown_summary(payload))
    return payload



# ============================================================
# V3.2 — Balanced project story axes, no generic/hardcoded plan
# ============================================================

OUTPUT_PAYLOAD_TYPE = "scientific_narrative_payload_v3_2_balanced_project_story_axes"

_V32_EXTRA_STOP_TERMS = _V31_STOP_TERMS | {
    "de", "du", "la", "le", "l", "et", "en", "un", "une", "des", "au", "aux", "a", "the", "of", "for", "to", "from", "by", "or", "and",
    "generation", "génération", "augmentation", "transformation", "enrichissement", "cas", "donnees", "données", "data",
    "methodes", "méthodes", "modeles", "modèles", "model", "models", "contraintes", "calcul", "implementation", "implémentation",
    "parametrage", "paramétrage", "validation", "robustesse", "generalisation", "généralisation", "resultats", "résultats",
    "based", "using", "multi", "category", "image", "images", "classification", "recognition", "automatic", "target"
}


def _v32_tokens(text: Any) -> List[str]:
    toks = []
    for t in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9_+\-/]{2,}", clean_sentence(text)):
        nt = _v31_norm_token(t)
        if not nt or nt in _V32_EXTRA_STOP_TERMS or len(nt) < 2:
            continue
        toks.append(nt)
    return toks


def _v32_keyword_hits(txt: str, keywords: List[str]) -> int:
    low = txt.lower()
    return sum(1 for k in keywords if k in low)


def _v32_role_scores(unit: Dict[str, Any]) -> Dict[str, int]:
    """Score plusieurs fonctions scientifiques au lieu d'un first-match.

    Important : les rôles sont génériques (données, modèles, génération, validation,
    calcul, paramètres). Les termes projet viennent du payload, pas du code.
    """
    txt = _v31_unit_text(unit).lower()
    td = unit.get("technical_detail_profile_for_phase5") or {}
    has_protocol = bool(isinstance(td, dict) and _v30_clean_list(td.get("protocol_or_test_details"), limit=4))
    has_params = bool(isinstance(td, dict) and _v30_clean_list(td.get("parameters_conditions_or_settings"), limit=4))
    has_samples = bool(isinstance(td, dict) and _v30_clean_list(td.get("materials_data_or_samples"), limit=4)) or bool(clean_sentence(unit.get("data_context")))
    has_results = bool(unit.get("result_claims_linked_to_method_test_metric")) or bool(clean_sentence(unit.get("demonstrated_or_reported_result")))
    has_metrics = bool((unit.get("must_exploit_in_phase5") or {}).get("metrics_or_values"))

    scores = {
        "donnees_echantillons_representativite": _v32_keyword_hits(txt, [
            "data", "dataset", "training set", "test set", "sample", "échantillon", "echantillon", "mesure", "measure",
            "signal", "image", "distribution", "represent", "représent", "variability", "variabilité", "observation"
        ]) + (2 if has_samples else 0),
        "methodes_modeles_et_mecanismes_de_decision": _v32_keyword_hits(txt, [
            "classification", "classifier", "classifieur", "recognition", "reconnaissance", "learning", "apprentissage",
            "model", "modèle", "modele", "architecture", "network", "réseau", "reseau", "training", "entrainement",
            "cnn", "neural", "feature", "descriptor"
        ]),
        "generation_transformation_et_enrichissement_des_cas": _v32_keyword_hits(txt, [
            "synthetic", "synthétique", "synthetique", "generation", "génération", "augmentation", "augment", "transformation",
            "variant", "adversarial", "mask", "masque", "perturb", "simulation", "render", "rendu", "domain randomization"
        ]),
        "protocoles_tests_resultats_et_validite": _v32_keyword_hits(txt, [
            "validation", "evaluation", "évaluation", "test", "benchmark", "metric", "métrique", "metrique", "accuracy", "error",
            "rmse", "r2", "precision", "recall", "robust", "generalization", "généralisation", "performance", "baseline", "compare"
        ]) + (2 if has_protocol else 0) + (2 if has_results else 0) + (1 if has_metrics else 0),
        "contraintes_de_calcul_implementation_et_parametrage": _v32_keyword_hits(txt, [
            "cpu", "gpu", "runtime", "memory", "mémoire", "compute", "calcul", "implementation", "implémentation",
            "mesh", "maillage", "solver", "solveur", "complexity", "complexité", "latency", "cuda", "parallel"
        ]),
        "parametres_conditions_et_variables_techniques": _v32_keyword_hits(txt, [
            "parameter", "paramètre", "parametre", "hyperparameter", "condition", "setting", "threshold", "seuil", "temperature", "pression",
            "duration", "durée", "frequence", "frequency", "dimension", "resolution", "batch", "epoch", "learning rate"
        ]) + (2 if has_params else 0),
        "mecanismes_scientifiques_extraits_du_dossier": 1,
    }

    # Éviter que CPU/GPU pollue des articles dont le cœur est génération/modèle/validation.
    compute = scores["contraintes_de_calcul_implementation_et_parametrage"]
    non_compute_max = max(
        scores["generation_transformation_et_enrichissement_des_cas"],
        scores["methodes_modeles_et_mecanismes_de_decision"],
        scores["protocoles_tests_resultats_et_validite"],
        scores["donnees_echantillons_representativite"],
    )
    if compute < 3 or compute < non_compute_max:
        scores["contraintes_de_calcul_implementation_et_parametrage"] = max(0, compute - 3)
    return scores


def _v32_roles_for_unit(unit: Dict[str, Any]) -> List[Tuple[str, int]]:
    scores = _v32_role_scores(unit)
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], GENERIC_STAGE_ORDER.index(kv[0]) if kv[0] in GENERIC_STAGE_ORDER else 50, kv[0]))
    primary_score = ordered[0][1] if ordered else 0
    roles: List[Tuple[str, int]] = []
    for role, score in ordered:
        if role == "mecanismes_scientifiques_extraits_du_dossier":
            continue
        # Garder le rôle principal et les rôles secondaires fortement justifiés.
        if score >= max(2, primary_score - 2):
            roles.append((role, score))
        elif role == "protocoles_tests_resultats_et_validite" and score >= 4:
            roles.append((role, score))
    if not roles:
        roles = [(ordered[0][0], ordered[0][1])] if ordered else [("mecanismes_scientifiques_extraits_du_dossier", 1)]
    return roles[:3]


def _v32_primary_role(unit: Dict[str, Any]) -> str:
    return _v32_roles_for_unit(unit)[0][0]


def _v32_pretty_project_terms(units: List[Dict[str, Any]], anchors: List[str], limit: int = 3) -> List[str]:
    texts: List[str] = []
    for u in units[:10]:
        texts += [clean_sentence(u.get("concept")), clean_sentence(u.get("technical_family_internal")), clean_sentence(u.get("data_context"))]
    counts = Counter()
    original: Dict[str, str] = {}
    anchor_set = set(anchors)
    for txt in texts:
        for raw in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9_+\-/]{2,}", txt):
            nt = _v31_norm_token(raw)
            if not nt or nt in _V32_EXTRA_STOP_TERMS or len(nt) < 3:
                continue
            # Garder soit les termes dominants du projet, soit des acronymes/termes spécifiques.
            if nt in anchor_set or raw.isupper() or any(ch.isdigit() for ch in raw) or len(nt) >= 5:
                counts[nt] += 1
                original.setdefault(nt, raw)
    out = []
    for term, _count in counts.most_common(8):
        val = original.get(term, term.upper() if len(term) <= 4 else term)
        if val.lower() not in _V32_EXTRA_STOP_TERMS:
            out.append(val)
    return unique_clean_list(out, limit=limit)


def _v32_axis_title(role: str, units: List[Dict[str, Any]], anchors: List[str]) -> str:
    role_titles = {
        "donnees_echantillons_representativite": "Données, représentativité et conditions d'observation",
        "methodes_modeles_et_mecanismes_de_decision": "Méthodes, modèles et mécanismes de décision",
        "generation_transformation_et_enrichissement_des_cas": "Génération, transformation et enrichissement des cas",
        "protocoles_tests_resultats_et_validite": "Protocoles de test, résultats et validité expérimentale",
        "contraintes_de_calcul_implementation_et_parametrage": "Contraintes de calcul, implémentation et paramétrage",
        "parametres_conditions_et_variables_techniques": "Paramètres, conditions et variables techniques",
        "mecanismes_scientifiques_extraits_du_dossier": "Mécanismes scientifiques extraits du dossier",
    }
    base = role_titles.get(role, role.replace("_", " "))
    terms = _v32_pretty_project_terms(units, anchors, limit=3)
    # Ne pas salir le titre si les termes trouvés sont trop pauvres ou déjà contenus dans le titre fonctionnel.
    terms = [t for t in terms if len(_v31_norm_token(t)) >= 3 and _v31_norm_token(t) not in _V32_EXTRA_STOP_TERMS]
    if not terms:
        return base
    joined = " / ".join(terms)
    if len(joined) < 4:
        return base
    return f"{base} — {joined}"


def _v32_collect_axis_material(us: List[Dict[str, Any]]) -> Tuple[List[str], List[Dict[str, Any]], List[str], List[str]]:
    td: List[str] = []
    results: List[Dict[str, Any]] = []
    open_ev: List[str] = []
    concepts: List[str] = []
    for u in us:
        concepts.append(u.get("concept"))
        profile = u.get("technical_detail_profile_for_phase5") or {}
        if isinstance(profile, dict):
            for key in [
                "protocol_or_test_details", "materials_data_or_samples", "parameters_conditions_or_settings",
                "instrumentation_or_tools", "simulation_or_implementation_details", "method_or_process_details",
            ]:
                td += _v30_clean_list(profile.get(key), limit=5, max_chars=350)
        for rc in as_list(u.get("result_claims_linked_to_method_test_metric"))[:3]:
            if isinstance(rc, dict):
                results.append({
                    "citation": u.get("citation"),
                    "result_text": truncate(rc.get("result_text"), 450),
                    "metrics_and_values": rc.get("metrics_and_values"),
                    "linked_method_or_technology_context": rc.get("linked_method_or_technology_context"),
                    "linked_test_or_validation_context": rc.get("linked_test_or_validation_context"),
                    "link_confidence": rc.get("link_confidence"),
                })
        od = u.get("open_domain_technical_evidence") or {}
        if isinstance(od, dict):
            open_ev += _v30_clean_list(od.get("unclassified_technical_snippets"), limit=4, max_chars=300)
            open_ev += _v30_clean_list(od.get("explicit_numeric_expressions"), limit=4, max_chars=120)
    return unique_clean_list(td, limit=20), results[:12], unique_clean_list(open_ev, limit=10), unique_clean_list(concepts, limit=12)


def _v32_build_balanced_project_axes(units: List[Dict[str, Any]], shared_limitations: List[Dict[str, Any]], verrou_index: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    anchors = _v31_project_anchor_terms(verrou_index, units)
    main_units: List[Dict[str, Any]] = []
    trace_units: List[Dict[str, Any]] = []
    for u in units:
        uu = dict(u)
        status = _v31_unit_main_status(uu, anchors)
        uu["v31_story_filter"] = status
        uu["v32_role_scores"] = _v32_role_scores(uu)
        uu["v32_roles"] = [{"role": r, "score": s} for r, s in _v32_roles_for_unit(uu)]
        uu["v32_primary_role"] = _v32_primary_role(uu)
        if status["use_as_main_axis"]:
            main_units.append(uu)
        else:
            trace_units.append(uu)
    if not main_units and units:
        main_units = sorted([dict(u, v31_story_filter=_v31_unit_main_status(u, anchors), v32_role_scores=_v32_role_scores(u), v32_primary_role=_v32_primary_role(u)) for u in units], key=lambda x: -int(x.get("evidence_richness_score") or 0))[:6]
        trace_units = [u for u in units if u not in main_units]

    role_order = [
        "donnees_echantillons_representativite",
        "methodes_modeles_et_mecanismes_de_decision",
        "generation_transformation_et_enrichissement_des_cas",
        "protocoles_tests_resultats_et_validite",
        "contraintes_de_calcul_implementation_et_parametrage",
        "parametres_conditions_et_variables_techniques",
        "mecanismes_scientifiques_extraits_du_dossier",
    ]
    grouped_main: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    grouped_support: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for u in main_units:
        roles = _v32_roles_for_unit(u)
        if roles:
            grouped_main[roles[0][0]].append(u)
            for r, s in roles[1:]:
                grouped_support[r].append(u)

    axes: List[Dict[str, Any]] = []
    used_axis_citations: set = set()
    for role in role_order:
        mains = grouped_main.get(role) or []
        supports = [u for u in grouped_support.get(role, []) if normalize_citation_label(u.get("citation_label")) not in {normalize_citation_label(x.get("citation_label")) for x in mains}]
        us = mains + supports
        if not us:
            continue
        # Éviter un axe calcul parasite : il doit être réellement dominant ou avoir au moins deux unités dédiées.
        if role == "contraintes_de_calcul_implementation_et_parametrage":
            dedicated = [u for u in mains if (u.get("v32_role_scores") or {}).get(role, 0) >= 3]
            if len(dedicated) < 2:
                continue
            us = dedicated + supports[:3]
        # Ne pas faire un axe principal avec une seule preuve faible sauf si elle est très riche et spécifique.
        if len(us) < 2 and int(us[0].get("evidence_richness_score") or 0) < 82:
            continue
        us = sorted(us, key=lambda x: (-int((x.get("v31_story_filter") or {}).get("specificity_score") or 0), citation_sort_key(x.get("citation_label"))))
        td, results, open_ev, concepts = _v32_collect_axis_material(us)
        cits_all = safe_citations([u.get("citation_label") for u in us], limit=14)
        primary_cits = safe_citations([u.get("citation_label") for u in mains], limit=10)
        duplicate_cits = sorted(set(cits_all) & used_axis_citations, key=citation_sort_key)
        used_axis_citations |= set(primary_cits or cits_all)
        axis = {
            "axis_id": f"axis_{len(axes)+1:02d}",
            "axis_type": role,
            "axis_generation_mode": "v3_2_balanced_multi_role_from_phase45_evidence",
            "visible_title_suggestion": _v32_axis_title(role, us, anchors),
            "dominant_project_terms": [t for t in anchors if any(t in _v32_tokens(_v31_unit_text(u, include_open=False)) for u in us)][:10],
            "concepts_to_weave": concepts,
            "citations": primary_cits or cits_all,
            "support_citations": safe_citations([c for c in cits_all if c not in (primary_cits or [])], limit=8),
            "citations_also_used_in_other_axes": safe_citations(list(duplicate_cits), limit=8),
            "story_units_main": mains[:8],
            "story_units_support": supports[:8],
            "technical_details_to_exploit": td,
            "result_metric_links_to_exploit": results,
            "open_domain_evidence_to_keep": open_ev,
            "writer_goal": _v31_writer_goal_for_axis(role),
            "writer_instruction": (
                "Écrire une étape de l'histoire scientifique propre au projet à partir des détails Phase 4.5. "
                "Relier explicitement méthode/procédé, paramètres, protocole/test, résultat/métrique et limite. "
                "Ne pas transformer les unités de traçabilité en preuves principales."
            ),
        }
        axes.append(axis)

    # Si tout est encore trop compressé, ajouter un axe validation depuis les résultats/protocoles, sans inventer.
    if len(axes) < 3 and len(main_units) >= 12:
        existing = {a.get("axis_type") for a in axes}
        candidates = [u for u in main_units if (u.get("must_exploit_in_phase5") or {}).get("results_linked_to_method_test") or (u.get("must_exploit_in_phase5") or {}).get("protocol_or_test")]
        if candidates and "protocoles_tests_resultats_et_validite" not in existing:
            us = sorted(candidates, key=lambda x: (-int(x.get("evidence_richness_score") or 0), citation_sort_key(x.get("citation_label"))))[:10]
            td, results, open_ev, concepts = _v32_collect_axis_material(us)
            axes.append({
                "axis_id": f"axis_{len(axes)+1:02d}",
                "axis_type": "protocoles_tests_resultats_et_validite",
                "axis_generation_mode": "v3_2_forced_validation_axis_from_existing_result_links",
                "visible_title_suggestion": _v32_axis_title("protocoles_tests_resultats_et_validite", us, anchors),
                "dominant_project_terms": [t for t in anchors if any(t in _v32_tokens(_v31_unit_text(u, include_open=False)) for u in us)][:10],
                "concepts_to_weave": concepts,
                "citations": safe_citations([u.get("citation_label") for u in us], limit=10),
                "support_citations": [],
                "citations_also_used_in_other_axes": [],
                "story_units_main": us[:8],
                "story_units_support": [],
                "technical_details_to_exploit": td,
                "result_metric_links_to_exploit": results,
                "open_domain_evidence_to_keep": open_ev,
                "writer_goal": _v31_writer_goal_for_axis("protocoles_tests_resultats_et_validite"),
                "writer_instruction": "Axe ajouté car les résultats/protocoles existent dans Phase 4.5 ; Phase 5 doit les exploiter sans inventer de métriques.",
            })

    trace_units = sorted(trace_units, key=lambda x: (-int(x.get("evidence_richness_score") or 0), citation_sort_key(x.get("citation_label"))))
    return axes, trace_units, anchors


def _v32_refine_shared_limitations(shared_limitations: List[Dict[str, Any]], axes: List[Dict[str, Any]], trace_units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    main_cits = set(sum([as_list(a.get("citations")) for a in axes], []))
    support_cits = set(sum([as_list(a.get("support_citations")) for a in axes], []))
    trace_cits = set(normalize_citation_label(u.get("citation_label")) for u in trace_units if normalize_citation_label(u.get("citation_label")))
    refined: List[Dict[str, Any]] = []
    for l in shared_limitations:
        if not isinstance(l, dict):
            continue
        cits = safe_citations(l.get("citations") or [])
        main = safe_citations([c for c in cits if c in main_cits], limit=6)
        support = safe_citations([c for c in cits if c in support_cits and c not in main], limit=6)
        trace = safe_citations([c for c in cits if c in trace_cits or (c not in main and c not in support)], limit=20)
        if not main and cits:
            # Garder quelques preuves, mais limiter pour éviter un gap gigantesque et générique.
            main = cits[:min(4, len(cits))]
            trace = cits[min(4, len(cits)):]
        ll = dict(l)
        ll["citations"] = main
        ll["support_citations_not_main"] = support
        ll["citations_traceability_only_not_main_gap_proof"] = trace
        ll["phase5_usage_instruction"] = (
            "Utiliser seulement les citations principales pour soutenir la limite dans le texte. "
            "Les citations support/traçabilité restent conservées, mais ne doivent pas allonger le gap ni devenir preuves centrales."
        )
        refined.append(ll)
    return refined


def _v32_build_storyline(verrou_index: List[Dict[str, Any]], axes: List[Dict[str, Any]], trace_units: List[Dict[str, Any]], shared_limitations: List[Dict[str, Any]], anchors: List[str]) -> Dict[str, Any]:
    project_need, project_gap = extract_project_need_and_gap(verrou_index)
    progression = []
    for i, a in enumerate(axes, 1):
        progression.append({
            "step": i,
            "axis_id": a.get("axis_id"),
            "visible_title_suggestion": a.get("visible_title_suggestion"),
            "axis_type": a.get("axis_type"),
            "writer_goal": a.get("writer_goal"),
            "dominant_project_terms": a.get("dominant_project_terms") or [],
            "citations": a.get("citations") or [],
            "support_citations": a.get("support_citations") or [],
            "technical_details_to_use": a.get("technical_details_to_exploit")[:8],
            "result_metric_links_to_use": a.get("result_metric_links_to_exploit")[:5],
            "transition_to_next": _v31_transition(a, axes[i] if i < len(axes) else None),
        })
    return {
        "storyline_type": "project_specific_scientific_storyline_v3_2_balanced_axes",
        "goal": "Construire l'histoire scientifique du projet avec plusieurs axes équilibrés issus des preuves techniques Phase 4.5, sans plan prédéfini ni axes faibles.",
        "project_need": project_need,
        "project_gap": project_gap,
        "dominant_project_terms": anchors[:20],
        "project_axes_progression": progression,
        "traceability_only_units_not_to_use_as_main_axes": [
            {
                "citation": u.get("citation"),
                "concept": u.get("concept"),
                "reason": (u.get("v31_story_filter") or {}).get("main_status"),
                "project_affinity": (u.get("v31_story_filter") or {}).get("project_affinity"),
            }
            for u in trace_units[:20]
        ],
        "limitations_to_turn_into_gap": [clean_sentence(l.get("label")) for l in shared_limitations[:8] if clean_sentence(l.get("label"))],
        "strict_rules": [
            "Ne pas utiliser les unités traceability_only comme preuves principales dans le texte final.",
            "Ne pas considérer la présence de CPU/GPU comme axe calcul si ce n'est pas le rôle dominant de la preuve.",
            "Ne pas produire un seul axe fourre-tout : distinguer données, modèles, génération, validation, calcul ou paramètres quand les preuves le justifient.",
            "Ne pas perdre les résultats : utiliser result_text et raw_numeric_or_value_mentions même si metrics_detected est vide.",
            "Ne pas perdre les paramètres/protocoles : utiliser technical_details_to_exploit et open_domain_evidence_to_keep.",
        ],
    }


def _v32_build_phase5_blueprint(verrou_index: List[Dict[str, Any]], axes: List[Dict[str, Any]], storyline: Dict[str, Any], refined_limitations: List[Dict[str, Any]]) -> Dict[str, Any]:
    base = _v31_build_phase5_blueprint(verrou_index, axes, storyline, refined_limitations)
    base["blueprint_type"] = "phase5_writer_blueprint_v3_2_balanced_project_story_axes"
    base["core_change"] = "Phase 5 doit suivre les axes équilibrés V3.2 : plusieurs fonctions scientifiques réelles du dossier, pas un axe fourre-tout ni un plan générique."
    base["axis_balance_policy"] = {
        "roles_are_inferred_from_phase45_evidence": True,
        "multi_role_units_allowed_as_support": True,
        "compute_axis_requires_dominant_compute_evidence": True,
        "axis_titles_cleaned_from_generic_tokens": True,
        "gap_citations_are_limited_to_main_support": True,
    }
    return base


def _v32_quality(payload: Dict[str, Any]) -> Dict[str, Any]:
    q = _v31_quality(payload)
    warnings = list(q.get("warnings") or [])
    score = float(q.get("score") or 0)
    axes = payload.get("project_specific_story_axes") or []
    units = payload.get("project_specific_method_story_units") or []
    dirty_titles = [a.get("visible_title_suggestion") for a in axes if re.search(r"\b(de|du|et|generation / de|augmentation / de)\b\s*$", clean_sentence(a.get("visible_title_suggestion")), flags=re.I)]
    if dirty_titles:
        score -= 8
        warnings.append("Certains titres d'axes restent sales ou génériques.")
    role_types = {a.get("axis_type") for a in axes}
    if len(role_types) < min(3, len(axes)) and len(units) >= 20:
        score -= 6
        warnings.append("Diversité fonctionnelle des axes encore faible.")
    if len(axes) >= 3:
        warnings = [w for w in warnings if "Axes principaux peu nombreux" not in w]
    return {"score": round(max(0.0, min(100.0, score)), 2), "level": "good" if score >= 80 else "medium" if score >= 60 else "weak", "warnings": warnings}


def build_markdown_summary(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Phase 4.7 — Project-specific Scientific Narrative V3.2")
    lines.append("")
    lines.append(f"Payload: `{payload.get('payload_type')}`")
    lines.append(f"OK: `{payload.get('ok')}`")
    q = payload.get("quality") or {}
    lines.append(f"Quality: `{q.get('score')}` / `{q.get('level')}`")
    cov = payload.get("coverage") or {}
    if cov:
        lines.append(f"Coverage: units `{cov.get('story_units_count')}`, main axes `{cov.get('main_axes_count')}`, traceability-only `{cov.get('traceability_only_units_count')}`, axes with results `{cov.get('axes_with_result_links')}`, axes with technical details `{cov.get('axes_with_technical_details')}`")
    lines.append("")
    story = payload.get("project_specific_storyline") or {}
    lines.append("## Histoire scientifique propre au projet — axes équilibrés")
    lines.append(story.get("goal") or "Construire l'histoire scientifique à partir des preuves du dossier.")
    lines.append("")
    lines.append("### Axes V3.2 à donner à Phase 5")
    for a in payload.get("project_specific_story_axes") or []:
        lines.append(f"- **{a.get('visible_title_suggestion')}** — citations principales: {', '.join(a.get('citations') or [])}")
        if a.get("support_citations"):
            lines.append(f"  - citations support: {', '.join((a.get('support_citations') or [])[:8])}")
        lines.append(f"  - détails techniques à exploiter: `{len(a.get('technical_details_to_exploit') or [])}`")
        lines.append(f"  - résultats/métriques liés à exploiter: `{len(a.get('result_metric_links_to_exploit') or [])}`")
    lines.append("")
    trace = story.get("traceability_only_units_not_to_use_as_main_axes") or []
    if trace:
        lines.append("## Unités conservées en traçabilité seulement")
        for u in trace[:20]:
            lines.append(f"- **{u.get('citation')}** — {u.get('concept')} — raison: `{u.get('reason')}`")
        lines.append("")
    lines.append("## Limites à transformer en gap")
    for l in payload.get("shared_limitations") or []:
        main = ", ".join(l.get("citations") or [])
        sup = ", ".join((l.get("support_citations_not_main") or [])[:8])
        trace_c = ", ".join((l.get("citations_traceability_only_not_main_gap_proof") or [])[:8])
        lines.append(f"- **{l.get('label')}** — citations principales: {main}")
        if sup:
            lines.append(f"  - support: {sup}")
        if trace_c:
            lines.append(f"  - traçabilité seulement: {trace_c}")
    lines.append("")
    if q.get("warnings"):
        lines.append("## Warnings")
        for w in q.get("warnings") or []:
            lines.append(f"- {w}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_scientific_narrative_payload(
    *,
    organisme: str,
    project: str,
    year: str,
    phase_4_5_path: Optional[str] = None,
    phase_4_6_path: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    p45_path = Path(phase_4_5_path) if phase_4_5_path else default_phase_4_5_path(organisme, project, year)
    p46_path = Path(phase_4_6_path) if phase_4_6_path else default_phase_4_6_path(organisme, project, year)
    phase45 = read_json(p45_path, {}) or {}
    phase46 = read_json(p46_path, {}) or {}
    verrou_index = build_verrou_index(phase45, phase46)
    if not verrou_index:
        return {
            "ok": False,
            "phase": "phase_4_7_scientific_narrative_builder",
            "payload_type": OUTPUT_PAYLOAD_TYPE,
            "generated_at": now_iso(),
            "organisme": organisme,
            "project": project,
            "year": year,
            "error": "Aucun verrou trouvé dans Phase 4.5 ou Phase 4.6.",
            "input_paths": {"phase_4_5": str(p45_path), "phase_4_6": str(p46_path)},
        }

    family_graph = build_family_graph(verrou_index)
    concept_graph = build_concept_graph(verrou_index, family_graph)
    comparison_graph = build_comparison_graph(verrou_index, family_graph)
    shared_limitations_raw = build_shared_limitations(verrou_index)
    scientific_consensus = build_scientific_consensus(family_graph, shared_limitations_raw, concept_graph)
    scientific_contradictions = build_scientific_contradictions(comparison_graph, shared_limitations_raw)
    cross_verrou_reasoning = build_cross_verrou_reasoning(verrou_index, family_graph, shared_limitations_raw)
    scientific_progression = build_scientific_progression(family_graph, concept_graph, comparison_graph)
    remaining_unknowns = build_remaining_unknowns(verrou_index, shared_limitations_raw)
    scientific_story = build_scientific_story(verrou_index, family_graph, concept_graph, shared_limitations_raw, scientific_progression, remaining_unknowns)

    raw_units = _v30_collect_project_specific_story_units(verrou_index)
    axes, trace_units, anchors = _v32_build_balanced_project_axes(raw_units, shared_limitations_raw, verrou_index)
    refined_limitations = _v32_refine_shared_limitations(shared_limitations_raw, axes, trace_units)
    project_storyline = _v32_build_storyline(verrou_index, axes, trace_units, refined_limitations, anchors)
    phase5_consultant_blueprint = _v32_build_phase5_blueprint(verrou_index, axes, project_storyline, refined_limitations)

    macro_scientific_knowledge_base = build_macro_scientific_knowledge_base(verrou_index, family_graph, concept_graph, refined_limitations)
    scientific_knowledge_base = build_scientific_knowledge_base(verrou_index, family_graph, concept_graph, refined_limitations)
    conceptual_comparison_graph = build_conceptual_comparison_graph_v21(comparison_graph, macro_scientific_knowledge_base)
    consultant_storyline = build_consultant_storyline_v21(verrou_index, macro_scientific_knowledge_base, conceptual_comparison_graph, refined_limitations)
    phase5_writer_blueprint = build_phase5_writer_blueprint(verrou_index, scientific_story, family_graph, concept_graph, comparison_graph, refined_limitations, cross_verrou_reasoning)
    citation_index = build_global_citation_index(verrou_index)

    out_dir = output_dir(organisme, project, year)
    output_path = out_dir / "scientific_narrative_payload.json"
    markdown_output_path = out_dir / "scientific_narrative_summary.md"
    payload: Dict[str, Any] = {
        "ok": True,
        "phase": "phase_4_7_scientific_narrative_builder",
        "step": "build_scientific_narrative_payload",
        "payload_type": OUTPUT_PAYLOAD_TYPE,
        "generated_at": now_iso(),
        "organisme": organisme,
        "project": project,
        "year": year,
        "dry_run": dry_run,
        "input_paths": {
            "phase_4_5_scientific_reasoning": str(p45_path),
            "phase_4_6_project_rd_argumentation": str(p46_path),
        },
        "verrous_count": len(verrou_index),
        "verrou_index": [
            {
                "verrou_id": v.get("verrou_id"),
                "verrou_title": v.get("verrou_title"),
                "has_phase_4_5": bool(v.get("phase_4_5")),
                "has_phase_4_6": bool(v.get("phase_4_6")),
            }
            for v in verrou_index
        ],
        "project_specific_storyline": project_storyline,
        "project_specific_story_axes": axes,
        "project_specific_method_story_units": raw_units,
        "traceability_only_units": trace_units,
        "dominant_project_terms": anchors,
        "phase5_consultant_blueprint": phase5_consultant_blueprint,
        "scientific_story": scientific_story,
        "macro_scientific_knowledge_base": macro_scientific_knowledge_base,
        "scientific_knowledge_base": scientific_knowledge_base,
        "conceptual_comparison_graph": conceptual_comparison_graph,
        "consultant_storyline": consultant_storyline,
        "family_graph": family_graph,
        "concept_graph": concept_graph,
        "comparison_graph": comparison_graph,
        "shared_limitations": refined_limitations,
        "scientific_consensus": scientific_consensus,
        "scientific_contradictions": scientific_contradictions,
        "cross_verrou_reasoning": cross_verrou_reasoning,
        "scientific_progression": scientific_progression,
        "remaining_unknowns": remaining_unknowns,
        "global_citation_index": citation_index,
        "phase5_writer_blueprint": phase5_writer_blueprint,
        "rules": {
            "phase_4_7_role": "define_balanced_project_specific_story_axes_not_final_writing",
            "uses_phase_4_5": True,
            "uses_phase_4_6": True,
            "uses_phase45_v23_technical_details": True,
            "does_not_create_new_scientific_evidence": True,
            "does_not_move_citations_between_verrous": True,
            "no_domain_hardcoding": True,
            "project_specific_axes_are_primary": True,
            "balanced_axis_roles_from_evidence": True,
            "compute_axis_requires_dominant_compute_evidence": True,
            "weak_or_low_affinity_units_kept_as_traceability_not_main_axes": True,
            "technical_no_loss_policy_enabled": True,
            "results_must_be_linked_to_method_test_metric_when_available": True,
            "metrics_empty_does_not_mean_result_empty": True,
            "phase5_must_not_copy_phase46_sections": True,
            "final_writer_language": "fr",
        },
        "coverage": {
            "story_units_count": len(raw_units),
            "main_axes_count": len(axes),
            "traceability_only_units_count": len(trace_units),
            "axes_with_result_links": sum(1 for a in axes if a.get("result_metric_links_to_exploit")),
            "axes_with_technical_details": sum(1 for a in axes if a.get("technical_details_to_exploit")),
            "result_links_total": sum(len(a.get("result_metric_links_to_exploit") or []) for a in axes),
            "technical_details_total": sum(len(a.get("technical_details_to_exploit") or []) for a in axes),
        },
        "output_path": str(output_path),
        "markdown_output_path": str(markdown_output_path),
    }
    payload["quality"] = _v32_quality(payload)
    payload["ok"] = payload["quality"]["score"] >= 60
    if not dry_run:
        write_json(output_path, payload)
        write_text(markdown_output_path, build_markdown_summary(payload))
    return payload


# ============================================================
# V3.3 — Clean project story titles + explicit narrative order
# ============================================================

OUTPUT_PAYLOAD_TYPE = "scientific_narrative_payload_v3_3_clean_titles_explicit_story_order"

# Ces mots ne doivent jamais polluer un intitulé visible d'axe.
# Ils sont génériques et ne dépendent pas d'un domaine métier.
_V33_AXIS_DIRTY_TERMS = _V32_EXTRA_STOP_TERMS | {
    "with", "are", "was", "were", "been", "being", "this", "that", "these", "those",
    "into", "onto", "than", "then", "when", "where", "which", "whose", "will", "can",
    "may", "could", "would", "should", "used", "use", "uses", "study", "paper", "approach",
    "approaches", "based", "via", "towards", "toward", "between", "across", "case", "cases",
    "new", "novel", "efficient", "effective", "improved", "proposed", "method", "methods",
    "technique", "techniques", "framework", "system", "systems", "analysis", "result", "results",
    "performance", "multi", "category", "image", "images", "automatic", "target", "recognition",
}

_V33_AXIS_BASE_TITLES = {
    "donnees_echantillons_representativite": "Données, représentativité et conditions d'observation",
    "methodes_modeles_et_mecanismes_de_decision": "Méthodes, modèles et mécanismes de décision",
    "generation_transformation_et_enrichissement_des_cas": "Génération, transformation et enrichissement des cas",
    "protocoles_tests_resultats_et_validite": "Protocoles de test, résultats et validité expérimentale",
    "contraintes_de_calcul_implementation_et_parametrage": "Contraintes de calcul, implémentation et paramétrage",
    "parametres_conditions_et_variables_techniques": "Paramètres, conditions et variables techniques",
    "mecanismes_scientifiques_extraits_du_dossier": "Mécanismes scientifiques extraits du dossier",
}

_V33_NARRATIVE_PRIORITY = {
    "donnees_echantillons_representativite": 10,
    "methodes_modeles_et_mecanismes_de_decision": 20,
    "generation_transformation_et_enrichissement_des_cas": 30,
    "parametres_conditions_et_variables_techniques": 40,
    "contraintes_de_calcul_implementation_et_parametrage": 45,
    "protocoles_tests_resultats_et_validite": 50,
    "mecanismes_scientifiques_extraits_du_dossier": 60,
}


def _v33_norm_term(value: Any) -> str:
    s = clean_sentence(value)
    s = s.strip("-_/.,;:()[]{}")
    return _v31_norm_token(s)


def _v33_is_dirty_term(value: Any) -> bool:
    raw = clean_sentence(value)
    if not raw:
        return True
    norm = _v33_norm_term(raw)
    if not norm or norm in _V33_AXIS_DIRTY_TERMS:
        return True
    if len(norm) < 3 and not raw.isupper():
        return True
    # Éliminer les fragments d'anglais génériques ou suffixes de titres.
    if re.fullmatch(r"(?:with|are|based|using|for|from|the|and|of|to|in|on|by|a|an)", raw, flags=re.I):
        return True
    # Éliminer les faux termes composés uniquement de stopwords.
    tokens = [_v33_norm_term(t) for t in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9_+\-/]{2,}", raw)]
    if tokens and all(t in _V33_AXIS_DIRTY_TERMS for t in tokens):
        return True
    return False


def _v33_candidate_terms_from_unit(unit: Dict[str, Any]) -> List[str]:
    values: List[Any] = [
        unit.get("concept"),
        unit.get("technical_family_internal"),
        unit.get("method_or_process"),
        unit.get("scientific_principle"),
        unit.get("data_context"),
        unit.get("validation_logic"),
    ]
    must = unit.get("must_exploit_in_phase5") if isinstance(unit.get("must_exploit_in_phase5"), dict) else {}
    values += as_list(must.get("metrics_or_values"))[:4]
    profile = unit.get("technical_detail_profile_for_phase5") if isinstance(unit.get("technical_detail_profile_for_phase5"), dict) else {}
    for key in ["materials_data_or_samples", "method_or_process_details", "protocol_or_test_details", "parameters_conditions_or_settings"]:
        values += as_list(profile.get(key))[:3]

    terms: List[str] = []
    for value in values:
        txt = clean_sentence(value)
        if not txt:
            continue
        # Garder acronyms, termes avec chiffres, et expressions courtes fréquentes.
        for raw in re.findall(r"\b[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9_+\-/]{1,}\b", txt):
            norm = _v33_norm_term(raw)
            if _v33_is_dirty_term(raw):
                continue
            if raw.isupper() and len(raw) >= 2:
                terms.append(raw)
            elif any(ch.isdigit() for ch in raw) and len(raw) >= 2:
                terms.append(raw)
            elif len(norm) >= 5:
                terms.append(raw)
    return unique_clean_list(terms, limit=20)


def _v33_dominant_terms_for_axis(axis: Dict[str, Any], all_axes: List[Dict[str, Any]], *, limit: int = 4) -> List[str]:
    """Extrait des concepts dominants réels sans salir les titres.

    On ne force aucun nom métier : les termes viennent seulement des unités de preuve.
    Les termes trop fréquents dans tous les axes sont gardés dans les détails mais pas
    utilisés pour différencier le titre.
    """
    units = [u for u in as_list(axis.get("units")) if isinstance(u, dict)]
    local = Counter()
    original: Dict[str, str] = {}
    for u in units:
        for term in _v33_candidate_terms_from_unit(u):
            norm = _v33_norm_term(term)
            if _v33_is_dirty_term(term):
                continue
            local[norm] += 1
            # Préférer la forme acronymique si disponible.
            prev = original.get(norm)
            if not prev or term.isupper() or len(term) < len(prev):
                original[norm] = term

    if not local:
        return []

    axis_presence = Counter()
    for a in all_axes:
        seen_norms = set()
        for u in as_list(a.get("units")):
            if isinstance(u, dict):
                seen_norms |= {_v33_norm_term(t) for t in _v33_candidate_terms_from_unit(u) if not _v33_is_dirty_term(t)}
        for n in seen_norms:
            axis_presence[n] += 1

    scored: List[Tuple[float, str]] = []
    for norm, count in local.items():
        # Pénaliser les termes présents partout, sauf acronymes très informatifs.
        presence = axis_presence.get(norm, 1)
        form = original.get(norm, norm)
        acronym_bonus = 1.2 if form.isupper() and len(form) >= 2 else 0.0
        digit_bonus = 0.8 if any(ch.isdigit() for ch in form) else 0.0
        specificity_bonus = min(1.0, max(0.0, (len(norm) - 4) / 8))
        overused_penalty = 1.0 if presence >= max(2, len(all_axes) - 1) else 0.0
        score = count * 2.0 + acronym_bonus + digit_bonus + specificity_bonus - overused_penalty
        scored.append((score, norm))

    out: List[str] = []
    for _score, norm in sorted(scored, reverse=True):
        val = original.get(norm, norm)
        if not _v33_is_dirty_term(val):
            out.append(val)
        if len(out) >= limit:
            break
    return unique_clean_list(out, limit=limit)


def _v33_clean_axis_title(axis: Dict[str, Any], all_axes: List[Dict[str, Any]]) -> str:
    role = clean_sentence(axis.get("axis_type") or axis.get("story_role"))
    base = _V33_AXIS_BASE_TITLES.get(role) or stage_title_generic(role) or "Axe scientifique du dossier"
    terms = _v33_dominant_terms_for_axis(axis, all_axes, limit=3)

    # Les titres visibles doivent rester propres. Les concepts dominants sont exposés
    # dans dominant_concepts_for_writer, pas forcément dans le titre.
    if not terms:
        return base
    clean_terms = [t for t in terms if not _v33_is_dirty_term(t)]
    # Ne pas ajouter un suffixe s'il contient seulement un terme global répété partout.
    if len(clean_terms) == 1:
        norm = _v33_norm_term(clean_terms[0])
        if sum(1 for a in all_axes if norm in {_v33_norm_term(x) for x in _v33_dominant_terms_for_axis(a, all_axes, limit=5)}) >= len(all_axes) - 1:
            return base
    suffix = " / ".join(clean_terms[:3])
    if suffix and not re.search(r"\b(with|are|based|using|of|for|and|the)\b", suffix, flags=re.I):
        return f"{base} — {suffix}"
    return base


def _v33_clean_and_enrich_axes(axes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for axis in axes:
        a = dict(axis)
        dom = _v33_dominant_terms_for_axis(a, axes, limit=6)
        old_title = clean_sentence(a.get("visible_title_suggestion"))
        new_title = _v33_clean_axis_title(a, axes)
        a["visible_title_suggestion_raw_v32"] = old_title
        a["visible_title_suggestion"] = new_title
        a["dominant_concepts_for_writer"] = dom
        a["title_cleaning"] = {
            "old_title": old_title,
            "new_title": new_title,
            "dirty_title_removed": old_title != new_title,
            "no_stopword_suffix": True,
            "dominant_concepts_kept_separately": True,
        }
        # Les concepts internes restent disponibles pour Phase 5, mais le titre est propre.
        a["writer_goal"] = _v33_writer_goal_for_axis(a)
        enriched.append(a)

    return sorted(
        enriched,
        key=lambda x: (
            _V33_NARRATIVE_PRIORITY.get(x.get("axis_type"), 99),
            -len(x.get("result_metric_links_to_exploit") or []),
            -len(x.get("technical_details_to_exploit") or []),
        ),
    )


def _v33_writer_goal_for_axis(axis: Dict[str, Any]) -> str:
    title = clean_sentence(axis.get("visible_title_suggestion")) or "cet axe"
    concepts = axis.get("dominant_concepts_for_writer") or []
    concept_txt = ", ".join(concepts[:4])
    results = len(axis.get("result_metric_links_to_exploit") or [])
    details = len(axis.get("technical_details_to_exploit") or [])
    parts = [f"Expliquer {title.lower()} à partir des preuves techniques réellement extraites."]
    if concept_txt:
        parts.append(f"Concepts ou termes dominants à intégrer naturellement : {concept_txt}.")
    if details:
        parts.append("Utiliser les paramètres, protocoles, matériaux, outils ou conditions disponibles pour enrichir le raisonnement.")
    if results:
        parts.append("Relier les résultats aux méthodes, tests ou métriques disponibles sans inventer de valeur.")
    parts.append("Ne pas écrire une fiche article et ne pas recopier les intitulés internes de Phase 4.7.")
    return " ".join(parts)


def _v33_axis_transition(current: Dict[str, Any], nxt: Optional[Dict[str, Any]]) -> str:
    cur = clean_sentence(current.get("visible_title_suggestion"))
    if not nxt:
        return "Cette étape doit conduire vers la formulation des limites de transposition et du gap R&D restant."
    nn = clean_sentence(nxt.get("visible_title_suggestion"))
    cur_role = current.get("axis_type")
    next_role = nxt.get("axis_type")
    if cur_role == "donnees_echantillons_representativite":
        return f"Après avoir cadré les données et leur représentativité, la narration peut introduire {nn.lower()} comme réponse méthodologique possible."
    if cur_role == "methodes_modeles_et_mecanismes_de_decision":
        return f"Après les méthodes et modèles existants, le texte peut montrer comment {nn.lower()} cherche à couvrir davantage de cas ou à renforcer la validation."
    if cur_role == "generation_transformation_et_enrichissement_des_cas":
        return f"Ces stratégies d'enrichissement doivent ensuite être évaluées à travers {nn.lower()}, afin de mesurer leur validité réelle."
    if next_role == "protocoles_tests_resultats_et_validite":
        return f"Les mécanismes présentés ne prennent de valeur CIR que s'ils sont reliés à {nn.lower()}."
    return f"La progression peut passer de {cur.lower()} vers {nn.lower()} pour construire une histoire scientifique continue."


def _v33_build_story_order(axes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = _v33_clean_and_enrich_axes(axes)
    story_order: List[Dict[str, Any]] = []
    for i, axis in enumerate(ordered):
        nxt = ordered[i + 1] if i + 1 < len(ordered) else None
        story_order.append({
            "order": i + 1,
            "axis_id": axis.get("axis_id"),
            "axis_type": axis.get("axis_type"),
            "title": axis.get("visible_title_suggestion"),
            "narrative_function": _v33_narrative_function(axis),
            "dominant_concepts_to_weave": axis.get("dominant_concepts_for_writer") or [],
            "primary_citations": safe_citations(axis.get("citations") or [], limit=6),
            "support_citations": safe_citations(axis.get("support_citations") or [], limit=6),
            "must_use_technical_details_count": len(axis.get("technical_details_to_exploit") or []),
            "must_use_result_links_count": len(axis.get("result_metric_links_to_exploit") or []),
            "transition_to_next": _v33_axis_transition(axis, nxt),
        })
    story_order.append({
        "order": len(story_order) + 1,
        "axis_id": "insuffisances_et_gap_rd",
        "axis_type": "insuffisances_et_gap_rd",
        "title": "Insuffisances de l'état de l'art et gap R&D",
        "narrative_function": "Transformer les limites observées en justification du verrou R&D et des travaux expérimentaux nécessaires.",
        "dominant_concepts_to_weave": [],
        "primary_citations": [],
        "support_citations": [],
        "transition_to_next": "Conclure sur les validations attendues dans le contexte précis du projet.",
    })
    return story_order


def _v33_narrative_function(axis: Dict[str, Any]) -> str:
    role = axis.get("axis_type")
    mapping = {
        "donnees_echantillons_representativite": "Montrer que la validité des méthodes dépend des données, des observations, des échantillons ou des conditions disponibles.",
        "methodes_modeles_et_mecanismes_de_decision": "Présenter les méthodes et mécanismes existants comme acquis scientifiques mobilisables, sans les confondre avec une preuve projet.",
        "generation_transformation_et_enrichissement_des_cas": "Expliquer comment la littérature cherche à enrichir ou transformer les cas disponibles pour couvrir davantage de situations.",
        "protocoles_tests_resultats_et_validite": "Relier les protocoles, résultats, métriques et tests aux limites de validation et de généralisation.",
        "contraintes_de_calcul_implementation_et_parametrage": "Discuter les contraintes de calcul ou d'implémentation seulement lorsqu'elles structurent réellement la faisabilité technique.",
        "parametres_conditions_et_variables_techniques": "Mettre en avant les paramètres, variables et conditions qui influencent la reproductibilité ou la transposition.",
    }
    return mapping.get(role, "Organiser un axe scientifique issu des preuves techniques du dossier.")


def _v33_build_structured_story_plan(
    verrou_index: List[Dict[str, Any]],
    axes: List[Dict[str, Any]],
    refined_limitations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    titles = unique_clean_list([v.get("verrou_title") for v in verrou_index if clean_sentence(v.get("verrou_title"))], limit=10)
    order = _v33_build_story_order(axes)
    limitation_labels = unique_clean_list([l.get("label") for l in refined_limitations if isinstance(l, dict) and clean_sentence(l.get("label"))], limit=6)
    limitation_cits = safe_citations(sum([as_list(l.get("citations")) for l in refined_limitations if isinstance(l, dict)], []), limit=10)
    return {
        "plan_type": "structured_scientific_story_plan_not_final_text",
        "verrous_to_defend": titles,
        "story_order": order,
        "opening_intent": "Introduire le besoin scientifique du projet et le verrou avant toute citation, puis annoncer que l'état de l'art fournit des acquis partiels.",
        "existing_methods_intent": "Présenter les méthodes et mécanismes existants comme réponses scientifiques déjà explorées, en citant seulement les sources qui soutiennent chaque affirmation.",
        "technical_enrichment_intent": "Exploiter les paramètres, protocoles, métriques, résultats et preuves ouvertes issus de Phase 4.5 pour éviter une rédaction générique.",
        "limitations_intent": "Montrer que les limites de données, de protocole, de robustesse, de transposition ou de paramétrage empêchent de conclure directement à la levée du verrou.",
        "gap_transition_intent": "Faire apparaître le gap R&D comme conséquence logique de l'écart entre les acquis de la littérature et les preuves attendues dans le contexte projet.",
        "limitations_to_turn_into_gap": limitation_labels,
        "citations_for_gap_discussion": limitation_cits,
        "no_final_text_warning": "Ce plan guide Phase 5 mais ne doit pas être copié mot à mot comme état de l'art final.",
    }


def _v33_enrich_phase5_blueprint(base: Dict[str, Any], axes: List[Dict[str, Any]], story_plan: Dict[str, Any]) -> Dict[str, Any]:
    bp = dict(base or {})
    bp["blueprint_type"] = "phase5_consultant_writer_blueprint_v3_3_clean_titles_explicit_story_order"
    bp["core_change"] = (
        "Phase 5 doit suivre l'ordre narratif explicite V3.3, utiliser les axes propres et les concepts dominants, "
        "puis exploiter les détails techniques Phase 4.5 sans recopier les phrases de Phase 4.6."
    )
    bp["story_order"] = story_plan.get("story_order") or []
    bp["structured_scientific_story_plan"] = story_plan
    bp["visible_section_order"] = [x.get("axis_id") for x in story_plan.get("story_order") or []]
    bp["section_blueprint"] = [
        {
            "section_id": a.get("axis_id"),
            "title_suggestion": a.get("visible_title_suggestion"),
            "axis_type": a.get("axis_type"),
            "goal": a.get("writer_goal"),
            "dominant_concepts_to_weave": a.get("dominant_concepts_for_writer") or [],
            "primary_citations": safe_citations(a.get("citations") or [], limit=6),
            "support_citations": safe_citations(a.get("support_citations") or [], limit=6),
            "must_use_technical_details": a.get("technical_details_to_exploit")[:8],
            "must_use_result_metric_links": a.get("result_metric_links_to_exploit")[:5],
            "avoid": [
                "ne pas afficher les suffixes sales extraits des titres d'articles",
                "ne pas écrire un paragraphe par article",
                "ne pas inventer une métrique si elle n'existe pas dans result_metric_links_to_exploit",
                "ne pas utiliser les unités traceability_only comme preuves principales",
            ],
        }
        for a in axes
    ]
    bp["title_policy"] = {
        "visible_titles_cleaned": True,
        "dirty_terms_removed": sorted(list(_V33_AXIS_DIRTY_TERMS))[:80],
        "dominant_concepts_are_context_not_dirty_suffix": True,
    }
    bp["citation_policy"] = {
        "primary_citations_support_main_claims": True,
        "support_citations_extend_context": True,
        "traceability_only_citations_must_not_be_main_gap_proof": True,
        "max_citations_per_sentence": 3,
    }
    return bp


def _v33_quality(payload: Dict[str, Any]) -> Dict[str, Any]:
    base = _v32_quality(payload)
    score = float(base.get("score") or 0)
    warnings = list(base.get("warnings") or [])
    axes = payload.get("project_specific_story_axes") or []
    story_plan = payload.get("project_specific_storyline", {}).get("structured_scientific_story_plan") or {}
    dirty_titles: List[str] = []
    for a in axes:
        title = clean_sentence(a.get("visible_title_suggestion"))
        if re.search(r"\b(with|are|based|using|of|for|and|the)\b", title, flags=re.I):
            dirty_titles.append(title)
        if re.search(r"/\s*(with|are|of|for|and|the)\b", title, flags=re.I):
            dirty_titles.append(title)
    if dirty_titles:
        score -= min(18, len(dirty_titles) * 6)
        warnings.append("titres d'axes encore pollués: " + "; ".join(unique_clean_list(dirty_titles, limit=3)))
    if not story_plan.get("story_order"):
        score -= 12
        warnings.append("story_order explicite manquant")
    if len(story_plan.get("story_order") or []) < max(2, len(axes)):
        score -= 6
        warnings.append("story_order trop court par rapport aux axes")
    # Warning non bloquant : aucun concept dominant séparé.
    if axes and not any(a.get("dominant_concepts_for_writer") for a in axes):
        warnings.append("aucun concept dominant séparé des titres ; Phase 5 utilisera surtout les détails techniques")
    level = "good" if score >= 80 else "medium" if score >= 60 else "weak"
    return {"score": round(max(0, min(100, score)), 2), "level": level, "warnings": unique_clean_list(warnings, limit=20)}


def build_markdown_summary(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Phase 4.7 — Project-specific Scientific Narrative V3.3")
    lines.append("")
    lines.append(f"Payload: `{payload.get('payload_type')}`")
    lines.append(f"OK: `{payload.get('ok')}`")
    q = payload.get("quality") or {}
    cov = payload.get("coverage") or {}
    lines.append(f"Quality: `{q.get('score')}` / `{q.get('level')}`")
    lines.append(
        f"Coverage: units `{cov.get('story_units_count')}`, main axes `{cov.get('main_axes_count')}`, "
        f"traceability-only `{cov.get('traceability_only_units_count')}`, axes with results `{cov.get('axes_with_result_links')}`, "
        f"axes with technical details `{cov.get('axes_with_technical_details')}`"
    )
    lines.append("")
    story = payload.get("project_specific_storyline") or {}
    plan = story.get("structured_scientific_story_plan") or {}
    lines.append("## Histoire scientifique propre au projet — V3.3")
    lines.append(story.get("narrative_principle") or "Construire l'histoire scientifique à partir des preuves techniques Phase 4.5, avec des titres propres et un ordre narratif explicite.")
    lines.append("")
    lines.append("### Ordre narratif explicite pour Phase 5")
    for step in plan.get("story_order") or []:
        title = step.get("title")
        cits = ", ".join(step.get("primary_citations") or [])
        concepts = ", ".join((step.get("dominant_concepts_to_weave") or [])[:4])
        lines.append(f"{step.get('order')}. **{title}**")
        if concepts:
            lines.append(f"   - concepts à intégrer: {concepts}")
        if cits:
            lines.append(f"   - citations principales: {cits}")
        if step.get("transition_to_next"):
            lines.append(f"   - transition: {step.get('transition_to_next')}")
    lines.append("")
    lines.append("### Axes V3.3 à donner à Phase 5")
    for a in payload.get("project_specific_story_axes") or []:
        lines.append(f"- **{a.get('visible_title_suggestion')}** — citations principales: {', '.join(a.get('citations') or [])}")
        if a.get("dominant_concepts_for_writer"):
            lines.append(f"  - concepts dominants: {', '.join(a.get('dominant_concepts_for_writer') or [])}")
        if a.get("support_citations"):
            lines.append(f"  - citations support: {', '.join((a.get('support_citations') or [])[:8])}")
        lines.append(f"  - détails techniques à exploiter: `{len(a.get('technical_details_to_exploit') or [])}`")
        lines.append(f"  - résultats/métriques liés à exploiter: `{len(a.get('result_metric_links_to_exploit') or [])}`")
        raw_title = a.get("visible_title_suggestion_raw_v32")
        if raw_title and raw_title != a.get("visible_title_suggestion"):
            lines.append(f"  - titre V3.2 nettoyé: `{raw_title}`")
    lines.append("")
    trace = payload.get("traceability_only_units") or []
    if trace:
        lines.append("## Unités conservées en traçabilité seulement")
        for u in trace[:20]:
            lines.append(f"- **{u.get('citation')}** — {u.get('concept')} — raison: `{u.get('reason')}`")
        lines.append("")
    lines.append("## Limites à transformer en gap")
    for l in payload.get("shared_limitations") or []:
        main = ", ".join(l.get("citations") or [])
        sup = ", ".join((l.get("support_citations_not_main") or [])[:8])
        trace_c = ", ".join((l.get("citations_traceability_only_not_main_gap_proof") or [])[:8])
        lines.append(f"- **{l.get('label')}** — citations principales: {main}")
        if sup:
            lines.append(f"  - support: {sup}")
        if trace_c:
            lines.append(f"  - traçabilité seulement: {trace_c}")
    lines.append("")
    if q.get("warnings"):
        lines.append("## Warnings")
        for w in q.get("warnings") or []:
            lines.append(f"- {w}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"



# ============================================================
# V3.4 — Verrous sections for Phase 5 unified state of art
# ============================================================
# Objectif : garder la logique actuelle d'un état de l'art unique,
# mais fournir à Phase 5 une section structurée "Verrous et incertitudes"
# où chaque verrou est défendu avec ses citations propres.
# Cette couche ne rédige pas le texte final et ne crée pas de preuve nouvelle.

PHASE_4_7_V3_4_VERROU_SECTIONS_FOR_PHASE5 = True
OUTPUT_PAYLOAD_TYPE = "scientific_narrative_payload_v3_4_unified_state_of_art_with_verrou_sections"


def _v34_writer_plan(verrou: Dict[str, Any]) -> Dict[str, Any]:
    p45 = verrou.get("phase_4_5") if isinstance(verrou.get("phase_4_5"), dict) else {}
    wp = p45.get("writer_plan_for_phase_5") if isinstance(p45.get("writer_plan_for_phase_5"), dict) else {}
    return wp


def _v34_allowed_citations_for_verrou(verrou: Dict[str, Any], units: List[Dict[str, Any]]) -> List[str]:
    """
    Couverture obligatoire par verrou.
    Priorité : Phase 4.5 writer_plan_for_phase_5.coverage_required_citations,
    puis allowed/all/main/support, puis unités 4.7.
    """
    wp = _v34_writer_plan(verrou)
    vals: List[Any] = []
    for key in [
        "coverage_required_citations",
        "allowed_citations",
        "all_citations_from_families",
        "direct_citations_from_families",
        "related_citations_from_families",
        "main_citations",
        "supporting_citations",
        "core_citations",
        "important_citations",
        "support_citations",
        "low_confidence_citations",
    ]:
        vals += as_list(wp.get(key))

    vid = clean_sentence(verrou.get("verrou_id"))
    vt = clean_sentence(verrou.get("verrou_title"))
    for u in units:
        if clean_sentence(u.get("verrou_id")) == vid or clean_sentence(u.get("verrou_title")) == vt:
            vals.append(u.get("citation_label") or u.get("citation"))

    return safe_citations(vals)


def _v34_citation_groups_for_verrou(verrou: Dict[str, Any], units: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    wp = _v34_writer_plan(verrou)
    direct = safe_citations(wp.get("direct_citations_from_families") or wp.get("main_citations") or [])
    related = safe_citations(wp.get("related_citations_from_families") or wp.get("supporting_citations") or [])
    core = safe_citations(wp.get("core_citations") or [])
    important = safe_citations(wp.get("important_citations") or [])
    support = safe_citations(wp.get("support_citations") or [])
    low = safe_citations(wp.get("low_confidence_citations") or [])

    # Récupération méthodologique/background depuis families_to_synthesize si disponible.
    methodo: List[str] = []
    background: List[str] = []
    for fam in as_list(wp.get("families_to_synthesize")):
        if not isinstance(fam, dict):
            continue
        methodo += as_list(fam.get("methodological_citations"))
        background += as_list(fam.get("background_citations"))

    allowed = _v34_allowed_citations_for_verrou(verrou, units)
    already = set(direct + related + methodo + background)
    # Toute citation non classée reste obligatoire mais prudente.
    fallback_context = [c for c in allowed if c not in already]

    return {
        "required_citations": allowed,
        "direct_citations": direct,
        "related_citations": related,
        "methodological_citations": safe_citations(methodo),
        "background_citations": safe_citations(background + fallback_context),
        "core_citations": core,
        "important_citations": important,
        "support_citations": support,
        "low_confidence_citations": low,
    }


def _v34_units_for_verrou(verrou: Dict[str, Any], units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    vid = clean_sentence(verrou.get("verrou_id"))
    vt = clean_sentence(verrou.get("verrou_title"))
    selected = [
        u for u in units
        if clean_sentence(u.get("verrou_id")) == vid or clean_sentence(u.get("verrou_title")) == vt
    ]
    # Ne pas limiter : chaque article sélectionné doit exister dans la section verrou.
    return sorted(
        selected,
        key=lambda u: (
            _v34_usage_rank(u.get("usage_type")),
            -int(u.get("evidence_richness_score") or 0),
            citation_sort_key(u.get("citation_label") or u.get("citation")),
        ),
    )


def _v34_usage_rank(usage: Any) -> int:
    u = clean_sentence(usage).lower()
    if u == "direct_evidence":
        return 0
    if u == "related_evidence":
        return 1
    if u in {"methodological_evidence", "methodological_context"}:
        return 2
    if u in {"background_evidence", "background_context", "weak_context"}:
        return 3
    return 4


def _v34_project_sections(verrou: Dict[str, Any]) -> Dict[str, str]:
    sections = verrou.get("project_sections") if isinstance(verrou.get("project_sections"), dict) else {}
    aj = verrou.get("argumentation_json") if isinstance(verrou.get("argumentation_json"), dict) else {}
    return {
        "project_need": clean_sentence(
            aj.get("project_need") or aj.get("technical_need") or sections.get("section_1_project_need") or sections.get("positionnement")
        ),
        "state_of_art_gap": clean_sentence(
            aj.get("state_of_art_gap") or sections.get("section_4_state_of_art_limits") or sections.get("section_5_gap_rd")
        ),
        "rd_gap": clean_sentence(
            aj.get("rd_gap") or aj.get("gap") or sections.get("section_5_gap_rd")
        ),
        "experimental_work_needed": clean_sentence(
            aj.get("experimental_work_needed") or sections.get("section_6_work_needed") or sections.get("section_6_travaux_rd")
        ),
    }


def _v34_unit_for_phase5(unit: Dict[str, Any]) -> Dict[str, Any]:
    details = unit.get("technical_detail_profile_for_phase5") if isinstance(unit.get("technical_detail_profile_for_phase5"), dict) else {}
    must = unit.get("must_exploit_in_phase5") if isinstance(unit.get("must_exploit_in_phase5"), dict) else {}
    return {
        "citation_label": normalize_citation_label(unit.get("citation_label") or unit.get("citation")),
        "citation": citation_bracket(unit.get("citation_label") or unit.get("citation")),
        "usage_type": clean_sentence(unit.get("usage_type")),
        "concept": clean_sentence(unit.get("concept")),
        "technical_family": clean_sentence(unit.get("technical_family_internal")),
        "scientific_principle": truncate(unit.get("scientific_principle"), 700),
        "mechanism_chain": truncate(unit.get("mechanism_chain"), 700),
        "data_context": truncate(unit.get("data_context"), 650),
        "validation_logic": truncate(unit.get("validation_logic"), 650),
        "reported_result": truncate(unit.get("demonstrated_or_reported_result"), 650),
        "limits_or_transposition_points": unique_clean_list(as_list(unit.get("limits_or_transposition_points")), limit=5),
        "project_gap_link": truncate(unit.get("project_gap_link"), 650),
        "technical_details_to_use": {
            "protocol_or_test_details": unique_clean_list(as_list(details.get("protocol_or_test_details")), limit=6),
            "parameters_conditions_or_settings": unique_clean_list(as_list(details.get("parameters_conditions_or_settings")), limit=6),
            "materials_data_or_samples": unique_clean_list(as_list(details.get("materials_data_or_samples")), limit=6),
            "instrumentation_or_tools": unique_clean_list(as_list(details.get("instrumentation_or_tools")), limit=6),
            "simulation_or_implementation_details": unique_clean_list(as_list(details.get("simulation_or_implementation_details")), limit=6),
        },
        "result_claims_to_use": as_list(unit.get("result_claims_linked_to_method_test_metric"))[:4],
        "open_domain_evidence_to_keep": unit.get("open_domain_technical_evidence") or {},
        "must_exploit_flags": must,
        "evidence_richness_score": unit.get("evidence_richness_score"),
        "writer_instruction": (
            "Utiliser cette unité pour défendre le verrou, sans fiche article : "
            "concept/méthode → données/protocole → résultat/limite → incertitude restante."
        ),
    }


def _v34_axes_for_verrou(verrou: Dict[str, Any], axes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    vid = clean_sentence(verrou.get("verrou_id"))
    vt = clean_sentence(verrou.get("verrou_title"))
    out: List[Dict[str, Any]] = []
    for axis in axes:
        axis_verrous = axis.get("verrous") or []
        if any(clean_sentence(v.get("verrou_id")) == vid or clean_sentence(v.get("verrou_title")) == vt for v in axis_verrous if isinstance(v, dict)):
            out.append({
                "axis_id": axis.get("axis_id"),
                "title_suggestion": axis.get("visible_title_suggestion"),
                "axis_type": axis.get("axis_type"),
                "citations": safe_citations(axis.get("citations") or []),
                "support_citations": safe_citations(axis.get("support_citations") or []),
                "writer_goal": axis.get("writer_goal"),
                "dominant_concepts_for_writer": axis.get("dominant_concepts_for_writer") or [],
            })
    return out


def _v34_limitations_for_verrou(verrou: Dict[str, Any], shared_limitations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    vid = clean_sentence(verrou.get("verrou_id"))
    vt = clean_sentence(verrou.get("verrou_title"))
    out: List[Dict[str, Any]] = []
    for lim in shared_limitations:
        if not isinstance(lim, dict):
            continue
        refs = lim.get("verrous") or []
        if refs and not any(clean_sentence(v.get("verrou_id")) == vid or clean_sentence(v.get("verrou_title")) == vt for v in refs if isinstance(v, dict)):
            continue
        out.append({
            "label": lim.get("label"),
            "limitation_type": lim.get("limitation_type"),
            "citations": safe_citations(lim.get("citations") or []),
            "support_citations": safe_citations(lim.get("support_citations_not_main") or []),
            "impacts": unique_clean_list(lim.get("impacts") or [], limit=4),
            "causes": unique_clean_list(lim.get("causes") or [], limit=4),
        })
    return out[:10]


def _v34_build_verrou_sections_for_phase5(
    verrou_index: List[Dict[str, Any]],
    units: List[Dict[str, Any]],
    axes: List[Dict[str, Any]],
    shared_limitations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    for idx, verrou in enumerate(verrou_index, 1):
        groups = _v34_citation_groups_for_verrou(verrou, units)
        v_units = _v34_units_for_verrou(verrou, units)
        present = safe_citations([u.get("citation_label") or u.get("citation") for u in v_units])
        required = groups.get("required_citations") or []
        missing = [c for c in required if c not in present]
        psections = _v34_project_sections(verrou)
        sections.append({
            "section_type": "verrou_section_inside_unified_state_of_art",
            "section_id": f"verrou_{idx}",
            "verrou_id": verrou.get("verrou_id"),
            "verrou_index": idx,
            "verrou_title": verrou.get("verrou_title"),
            "visible_title_suggestion": f"Verrou {idx} — {verrou.get('verrou_title')}",
            "project_need": psections.get("project_need"),
            "state_of_art_gap": psections.get("state_of_art_gap"),
            "rd_gap": psections.get("rd_gap"),
            "experimental_work_needed": psections.get("experimental_work_needed"),
            "citation_coverage": {
                "required_citations": required,
                "present_citations_from_story_units": present,
                "missing_citations_from_story_units": missing,
                "direct_citations": groups.get("direct_citations") or [],
                "related_citations": groups.get("related_citations") or [],
                "methodological_citations": groups.get("methodological_citations") or [],
                "background_citations": groups.get("background_citations") or [],
                "coverage_ok": len(missing) == 0,
            },
            "axes_to_reuse_without_repeating_definitions": _v34_axes_for_verrou(verrou, axes),
            "article_units_to_cover": [_v34_unit_for_phase5(u) for u in v_units],
            "limitations_to_turn_into_uncertainties": _v34_limitations_for_verrou(verrou, shared_limitations),
            "writer_goal": (
                "Défendre ce verrou dans un état de l'art unique : rappeler le problème scientifique, "
                "mobiliser toutes les citations requises du verrou selon leur rôle, montrer les acquis de la littérature, "
                "puis expliquer pourquoi les limites et la non-transposabilité maintiennent une incertitude R&D."
            ),
            "writing_policy": {
                "not_a_separate_state_of_art": True,
                "part_of_single_unified_state_of_art": True,
                "must_cover_all_required_citations": True,
                "direct_as_main_proof": True,
                "related_as_support_or_transposability": True,
                "methodological_background_as_context": True,
                "do_not_move_citations_to_other_verrous": True,
                "do_not_write_article_by_article": True,
            },
        })
    return sections


def _v34_enrich_phase5_blueprint_with_verrou_sections(
    blueprint: Dict[str, Any],
    verrou_sections: List[Dict[str, Any]],
) -> Dict[str, Any]:
    bp = dict(blueprint or {})
    existing_order = list(bp.get("visible_section_order") or [])
    if "verrous_et_incertitudes_scientifiques" not in existing_order:
        # Insérer juste avant le gap / synthèse si possible.
        insert_at = len(existing_order)
        for marker in ["insuffisances_et_gap_rd", "cir_synthesis", "synthese_cir"]:
            if marker in existing_order:
                insert_at = existing_order.index(marker)
                break
        existing_order.insert(insert_at, "verrous_et_incertitudes_scientifiques")
    bp["visible_section_order"] = existing_order
    bp["verrou_sections_inside_unified_state_of_art"] = verrou_sections
    bp["verrou_coverage_policy"] = {
        "single_unified_state_of_art": True,
        "add_verrou_sections_inside_same_document": True,
        "not_seven_separate_state_of_art": True,
        "must_cover_citations_per_verrou": True,
        "citation_source": "Phase 4.5 coverage_required_citations + Phase 4.7 story units",
        "instruction_for_phase5": (
            "Rédiger un seul état de l'art. Après les sections globales, ajouter une partie "
            "'Verrous et incertitudes scientifiques' contenant une sous-section par verrou. "
            "Chaque sous-section doit couvrir ses required_citations sans déplacer les citations vers un autre verrou."
        ),
    }
    bp["section_guidance"] = list(bp.get("section_guidance") or []) + [{
        "section_id": "verrous_et_incertitudes_scientifiques",
        "title_suggestion": "Verrous et incertitudes scientifiques, techniques et technologiques",
        "goal": "Défendre chaque verrou avec les articles qui lui sont associés, dans le même état de l'art global.",
        "verrou_sections": [
            {
                "verrou_id": v.get("verrou_id"),
                "title_suggestion": v.get("visible_title_suggestion"),
                "required_citations": (v.get("citation_coverage") or {}).get("required_citations") or [],
                "direct_citations": (v.get("citation_coverage") or {}).get("direct_citations") or [],
                "related_citations": (v.get("citation_coverage") or {}).get("related_citations") or [],
                "methodological_citations": (v.get("citation_coverage") or {}).get("methodological_citations") or [],
                "background_citations": (v.get("citation_coverage") or {}).get("background_citations") or [],
            }
            for v in verrou_sections
        ],
        "must_not": [
            "ne pas transformer cette partie en sept états de l'art séparés",
            "ne pas déplacer une citation d'un verrou vers un autre verrou",
            "ne pas ignorer les citations méthodologiques/fondamentales ; les utiliser en cadrage",
            "ne pas rédiger une fiche article par article",
        ],
    }]
    return bp


def _v34_verrou_coverage_summary(verrou_sections: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_required = 0
    total_present = 0
    missing_by_verrou: Dict[str, List[str]] = {}
    for v in verrou_sections:
        cov = v.get("citation_coverage") or {}
        required = cov.get("required_citations") or []
        present = cov.get("present_citations_from_story_units") or []
        missing = cov.get("missing_citations_from_story_units") or []
        total_required += len(required)
        total_present += len(present)
        if missing:
            missing_by_verrou[str(v.get("verrou_id") or v.get("verrou_index"))] = missing
    return {
        "verrous_sections_count": len(verrou_sections),
        "required_citations_total_links": total_required,
        "present_citations_total_links": total_present,
        "missing_citations_by_verrou": missing_by_verrou,
        "coverage_ok": not bool(missing_by_verrou),
    }

def build_scientific_narrative_payload(
    *,
    organisme: str,
    project: str,
    year: str,
    phase_4_5_path: Optional[str] = None,
    phase_4_6_path: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    p45_path = Path(phase_4_5_path) if phase_4_5_path else default_phase_4_5_path(organisme, project, year)
    p46_path = Path(phase_4_6_path) if phase_4_6_path else default_phase_4_6_path(organisme, project, year)
    phase45 = read_json(p45_path, {}) or {}
    phase46 = read_json(p46_path, {}) or {}
    verrou_index = build_verrou_index(phase45, phase46)
    if not verrou_index:
        return {
            "ok": False,
            "phase": "phase_4_7_scientific_narrative_builder",
            "payload_type": OUTPUT_PAYLOAD_TYPE,
            "generated_at": now_iso(),
            "organisme": organisme,
            "project": project,
            "year": year,
            "error": "Aucun verrou trouvé dans Phase 4.5 ou Phase 4.6.",
            "input_paths": {"phase_4_5": str(p45_path), "phase_4_6": str(p46_path)},
        }

    family_graph = build_family_graph(verrou_index)
    concept_graph = build_concept_graph(verrou_index, family_graph)
    comparison_graph = build_comparison_graph(verrou_index, family_graph)
    shared_limitations_raw = build_shared_limitations(verrou_index)
    scientific_consensus = build_scientific_consensus(family_graph, shared_limitations_raw, concept_graph)
    scientific_contradictions = build_scientific_contradictions(comparison_graph, shared_limitations_raw)
    cross_verrou_reasoning = build_cross_verrou_reasoning(verrou_index, family_graph, shared_limitations_raw)
    scientific_progression = build_scientific_progression(family_graph, concept_graph, comparison_graph)
    remaining_unknowns = build_remaining_unknowns(verrou_index, shared_limitations_raw)
    scientific_story = build_scientific_story(verrou_index, family_graph, concept_graph, shared_limitations_raw, scientific_progression, remaining_unknowns)

    raw_units = _v30_collect_project_specific_story_units(verrou_index)
    axes_v32, trace_units, anchors = _v32_build_balanced_project_axes(raw_units, shared_limitations_raw, verrou_index)
    axes = _v33_clean_and_enrich_axes(axes_v32)
    refined_limitations = _v32_refine_shared_limitations(shared_limitations_raw, axes, trace_units)
    project_storyline = _v32_build_storyline(verrou_index, axes, trace_units, refined_limitations, anchors)
    story_plan = _v33_build_structured_story_plan(verrou_index, axes, refined_limitations)
    project_storyline["structured_scientific_story_plan"] = story_plan
    project_storyline["story_order"] = story_plan.get("story_order") or []
    project_storyline["narrative_principle"] = (
        "Construire l'histoire scientifique du projet à partir des axes propres, des concepts dominants, "
        "des détails techniques, des protocoles, des résultats et des limites réellement extraits. "
        "Les titres visibles sont nettoyés et l'ordre narratif est explicite pour Phase 5."
    )

    base_blueprint = _v32_build_phase5_blueprint(verrou_index, axes, project_storyline, refined_limitations)
    phase5_consultant_blueprint = _v33_enrich_phase5_blueprint(base_blueprint, axes, story_plan)

    macro_scientific_knowledge_base = build_macro_scientific_knowledge_base(verrou_index, family_graph, concept_graph, refined_limitations)
    scientific_knowledge_base = build_scientific_knowledge_base(verrou_index, family_graph, concept_graph, refined_limitations)
    conceptual_comparison_graph = build_conceptual_comparison_graph_v21(comparison_graph, macro_scientific_knowledge_base)
    consultant_storyline = build_consultant_storyline_v21(verrou_index, macro_scientific_knowledge_base, conceptual_comparison_graph, refined_limitations)
    phase5_writer_blueprint = build_phase5_writer_blueprint(verrou_index, scientific_story, family_graph, concept_graph, comparison_graph, refined_limitations, cross_verrou_reasoning)
    citation_index = build_global_citation_index(verrou_index)

    # V3.4 FIX — construire explicitement les sections de verrous pour Phase 5
    # Important : la Phase 5 reste un seul état de l'art unifié, mais elle reçoit
    # une vue par verrou pour couvrir toutes les citations obligatoires.
    verrou_sections_for_phase5 = _v34_build_verrou_sections_for_phase5(
        verrou_index=verrou_index,
        units=raw_units,
        axes=axes,
        shared_limitations=refined_limitations,
    )
    verrou_coverage_summary = _v34_verrou_coverage_summary(verrou_sections_for_phase5)

    # Enrichir le blueprint consultant sans casser les champs existants.
    phase5_consultant_blueprint = _v34_enrich_phase5_blueprint_with_verrou_sections(
        phase5_consultant_blueprint,
        verrou_sections_for_phase5,
    )

    out_dir = output_dir(organisme, project, year)
    output_path = out_dir / "scientific_narrative_payload.json"
    markdown_output_path = out_dir / "scientific_narrative_summary.md"
    payload: Dict[str, Any] = {
        "ok": True,
        "phase": "phase_4_7_scientific_narrative_builder",
        "step": "build_scientific_narrative_payload",
        "payload_type": OUTPUT_PAYLOAD_TYPE,
        "generated_at": now_iso(),
        "organisme": organisme,
        "project": project,
        "year": year,
        "dry_run": dry_run,
        "input_paths": {
            "phase_4_5_scientific_reasoning": str(p45_path),
            "phase_4_6_project_rd_argumentation": str(p46_path),
        },
        "verrous_count": len(verrou_index),
        "verrou_index": [
            {
                "verrou_id": v.get("verrou_id"),
                "verrou_title": v.get("verrou_title"),
                "has_phase_4_5": bool(v.get("phase_4_5")),
                "has_phase_4_6": bool(v.get("phase_4_6")),
            }
            for v in verrou_index
        ],
        "project_specific_storyline": project_storyline,
        "project_specific_story_axes": axes,
        "project_specific_method_story_units": raw_units,
        "traceability_only_units": trace_units,
        "dominant_project_terms": anchors,
        "phase5_consultant_blueprint": phase5_consultant_blueprint,
        "verrou_sections_for_phase5": verrou_sections_for_phase5,
        "verrou_coverage_summary": verrou_coverage_summary,
        "scientific_story": scientific_story,
        "macro_scientific_knowledge_base": macro_scientific_knowledge_base,
        "scientific_knowledge_base": scientific_knowledge_base,
        "conceptual_comparison_graph": conceptual_comparison_graph,
        "consultant_storyline": consultant_storyline,
        "family_graph": family_graph,
        "concept_graph": concept_graph,
        "comparison_graph": comparison_graph,
        "shared_limitations": refined_limitations,
        "scientific_consensus": scientific_consensus,
        "scientific_contradictions": scientific_contradictions,
        "cross_verrou_reasoning": cross_verrou_reasoning,
        "scientific_progression": scientific_progression,
        "remaining_unknowns": remaining_unknowns,
        "global_citation_index": citation_index,
        "phase5_writer_blueprint": phase5_writer_blueprint,
        "rules": {
            "phase_4_7_role": "define_clean_project_specific_story_order_not_final_writing",
            "uses_phase_4_5": True,
            "uses_phase_4_6": True,
            "uses_phase45_v23_technical_details": True,
            "does_not_create_new_scientific_evidence": True,
            "does_not_move_citations_between_verrous": True,
            "no_domain_hardcoding": True,
            "project_specific_axes_are_primary": True,
            "balanced_axis_roles_from_evidence": True,
            "axis_titles_cleaned_from_dirty_tokens": True,
            "dominant_concepts_separated_from_visible_titles": True,
            "explicit_story_order_for_phase5": True,
            "structured_scientific_story_plan_for_phase5": True,
            "verrou_sections_inside_unified_state_of_art_for_phase5": True,
            "must_cover_citations_per_verrou": True,
            "not_seven_separate_state_of_art": True,
            "weak_or_low_affinity_units_kept_as_traceability_not_main_axes": True,
            "technical_no_loss_policy_enabled": True,
            "results_must_be_linked_to_method_test_metric_when_available": True,
            "metrics_empty_does_not_mean_result_empty": True,
            "phase5_must_not_copy_phase46_sections": True,
            "final_writer_language": "fr",
        },
        "coverage": {
            "story_units_count": len(raw_units),
            "main_axes_count": len(axes),
            "traceability_only_units_count": len(trace_units),
            "axes_with_result_links": sum(1 for a in axes if a.get("result_metric_links_to_exploit")),
            "axes_with_technical_details": sum(1 for a in axes if a.get("technical_details_to_exploit")),
            "result_links_total": sum(len(a.get("result_metric_links_to_exploit") or []) for a in axes),
            "technical_details_total": sum(len(a.get("technical_details_to_exploit") or []) for a in axes),
            "story_order_steps": len(story_plan.get("story_order") or []),
            "titles_cleaned_count": sum(1 for a in axes if (a.get("title_cleaning") or {}).get("dirty_title_removed")),
            "verrou_sections_count": len(verrou_sections_for_phase5),
            "verrou_required_citations_total_links": verrou_coverage_summary.get("required_citations_total_links"),
            "verrou_present_citations_total_links": verrou_coverage_summary.get("present_citations_total_links"),
            "verrou_coverage_ok": verrou_coverage_summary.get("coverage_ok"),
        },
        "output_path": str(output_path),
        "markdown_output_path": str(markdown_output_path),
    }
    payload["quality"] = _v33_quality(payload)
    payload["ok"] = payload["quality"]["score"] >= 60
    if not dry_run:
        write_json(output_path, payload)
        write_text(markdown_output_path, build_markdown_summary(payload))
    return payload


# ============================================================
# V4.0 — Dual writer blueprints for consultant UI mode choice
# ============================================================
# But : distinguer l'architecture AVANT la Phase 5.
# - Mode global : un état de l'art unique couvrant tous les verrous.
# - Mode per_verrou : un état de l'art spécifique par verrou, basé sur ses citations.
# - Mode both : les deux sorties.
# Cette phase ne rédige toujours pas le livrable final : elle prépare les blueprints.

OUTPUT_PAYLOAD_TYPE = "scientific_narrative_payload_v4_0_dual_writer_blueprints_global_or_per_verrou"

_v40_previous_build_scientific_narrative_payload = build_scientific_narrative_payload


def _v40_writer_mode_from_env() -> str:
    raw = clean_text(os.getenv("ENNOSMART_STATE_OF_ART_MODE") or os.getenv("ENNOSMART_PHASE5_STATE_OF_ART_MODE") or "global").lower()
    raw = raw.replace("-", "_").strip()
    aliases = {
        "single": "global",
        "unique": "global",
        "unified": "global",
        "global_only": "global",
        "par_verrou": "per_verrou",
        "perverrou": "per_verrou",
        "by_verrou": "per_verrou",
        "verrou": "per_verrou",
        "verrous": "per_verrou",
        "separate": "per_verrou",
        "all": "both",
        "dual": "both",
    }
    mode = aliases.get(raw, raw)
    return mode if mode in {"global", "per_verrou", "both"} else "global"




def collect_citations_from_obj(obj: Any) -> List[str]:
    """Collecte récursive robuste des citations [A1] dans un objet arbitraire."""
    vals: List[Any] = []
    if isinstance(obj, dict):
        for key in [
            "required_citations", "direct_citations", "related_citations",
            "methodological_citations", "background_citations", "citations",
            "citation_labels", "main_citations", "support_citations",
            "linked_citations", "all_citations", "citation_label", "citation",
        ]:
            vals += as_list(obj.get(key))
        for v in obj.values():
            if isinstance(v, (dict, list, tuple)):
                vals += collect_citations_from_obj(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            vals += collect_citations_from_obj(x)
    else:
        vals.append(obj)
    return safe_citations(vals)


def _v40_citation_set_from_section(section: Dict[str, Any]) -> List[str]:
    cov = section.get("citation_coverage") if isinstance(section.get("citation_coverage"), dict) else {}
    vals: List[Any] = []
    for key in [
        "required_citations",
        "direct_citations",
        "related_citations",
        "methodological_citations",
        "background_citations",
        "present_citations_from_story_units",
    ]:
        vals += as_list(cov.get(key))
    vals += collect_citations_from_obj(section)
    return safe_citations(vals)


def _v40_unit_matches_citations(unit: Dict[str, Any], citations: List[str]) -> bool:
    wanted = set(safe_citations(citations))
    if not wanted:
        return False
    found = set(citations_from_obj(unit)) | set(safe_citations([unit.get("citation_label"), unit.get("citation")]))
    return bool(wanted & found)


def _v40_filter_units_for_citations(units: List[Dict[str, Any]], citations: List[str], limit: int = 40) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for u in units or []:
        if not isinstance(u, dict) or not _v40_unit_matches_citations(u, citations):
            continue
        cits = safe_citations(citations_from_obj(u) + [u.get("citation_label"), u.get("citation")])
        key = (tuple(cits), fs_slug(clean_sentence(u.get("method_or_concept") or u.get("concept") or u.get("article_title") or u.get("title")))[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def _v40_project_sections_compact(section: Dict[str, Any]) -> Dict[str, str]:
    return {
        "project_need": clean_sentence(section.get("project_need")),
        "state_of_art_gap": clean_sentence(section.get("state_of_art_gap")),
        "rd_gap": clean_sentence(section.get("rd_gap")),
        "experimental_work_needed": clean_sentence(section.get("experimental_work_needed")),
    }


def _v40_build_global_writer_blueprint(payload: Dict[str, Any]) -> Dict[str, Any]:
    consultant_bp = payload.get("phase5_consultant_blueprint") if isinstance(payload.get("phase5_consultant_blueprint"), dict) else {}
    story = payload.get("project_specific_storyline") if isinstance(payload.get("project_specific_storyline"), dict) else {}
    return {
        "blueprint_type": "phase5_global_writer_blueprint_v4_0",
        "writer_mode": "global",
        "goal": "Rédiger un seul état de l'art global couvrant l'ensemble des verrous du dossier.",
        "source_payload_type": payload.get("payload_type"),
        "verrous_count": payload.get("verrous_count"),
        "verrou_index": payload.get("verrou_index") or [],
        "storyline": story,
        "consultant_blueprint": consultant_bp,
        "global_axes": payload.get("project_specific_story_axes") or [],
        "global_story_units": payload.get("project_specific_method_story_units") or [],
        "shared_limitations": payload.get("shared_limitations") or [],
        "global_citation_index": payload.get("global_citation_index") or {},
        "rules": {
            "single_unified_state_of_art": True,
            "one_state_of_art_per_verrou": False,
            "may_include_verrou_subsections_inside_same_document": True,
            "do_not_create_scientific_evidence": True,
            "do_not_move_citations_between_verrous": True,
            "no_domain_hardcoding": True,
        },
    }


def _v40_build_per_verrou_writer_blueprints(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    sections = payload.get("verrou_sections_for_phase5") if isinstance(payload.get("verrou_sections_for_phase5"), list) else []
    all_units = payload.get("project_specific_method_story_units") if isinstance(payload.get("project_specific_method_story_units"), list) else []
    shared_limits = payload.get("shared_limitations") if isinstance(payload.get("shared_limitations"), list) else []
    citation_index = payload.get("global_citation_index") if isinstance(payload.get("global_citation_index"), dict) else {}
    blueprints: List[Dict[str, Any]] = []
    for idx, sec in enumerate(sections, 1):
        if not isinstance(sec, dict):
            continue
        cov = sec.get("citation_coverage") if isinstance(sec.get("citation_coverage"), dict) else {}
        required = safe_citations(cov.get("required_citations") or _v40_citation_set_from_section(sec))
        direct = safe_citations(cov.get("direct_citations") or [])
        related = safe_citations(cov.get("related_citations") or [])
        methodological = safe_citations(cov.get("methodological_citations") or [])
        background = safe_citations(cov.get("background_citations") or [])
        article_units = sec.get("article_units_to_cover") if isinstance(sec.get("article_units_to_cover"), list) else []
        if not article_units:
            article_units = _v40_filter_units_for_citations(all_units, required)
        local_limits = sec.get("limitations_to_turn_into_uncertainties") if isinstance(sec.get("limitations_to_turn_into_uncertainties"), list) else []
        if not local_limits:
            local_limits = [l for l in shared_limits if _v40_unit_matches_citations(l, required)][:8]
        title = clean_sentence(sec.get("verrou_title") or sec.get("visible_title_suggestion"))
        verrou_id = clean_sentence(sec.get("verrou_id"))
        if not title or not verrou_id:
            raise ContractError(
                "invalid_phase47_verrou_section",
                "Une section de verrou Phase 4.7 a perdu son identifiant ou son titre confirmé.",
                {"index": idx},
            )
        blueprints.append({
            "blueprint_type": "phase5_per_verrou_writer_blueprint_v4_0",
            "writer_mode": "per_verrou",
            "verrou_index": sec.get("verrou_index") or idx,
            "verrou_id": verrou_id,
            "verrou_title": title,
            "visible_title_suggestion": clean_sentence(sec.get("visible_title_suggestion") or f"Verrou {idx} — {title}"),
            "project_problem": _v40_project_sections_compact(sec),
            "citation_policy": {
                "required_citations": required,
                "direct_citations": direct,
                "related_citations": related,
                "methodological_citations": methodological,
                "background_citations": background,
                "coverage_ok_in_blueprint": bool(cov.get("coverage_ok", not bool(cov.get("missing_citations_from_story_units")))),
                "missing_citations_from_story_units": cov.get("missing_citations_from_story_units") or [],
            },
            "article_units_to_write_from": article_units,
            "limitations_to_turn_into_gap": local_limits,
            "references_metadata": {c: citation_index.get(c, {}) for c in required},
            "writing_goal": (
                "Rédiger un état de l'art spécifique à ce verrou à partir de ses articles et citations uniquement. "
                "Le texte doit expliquer le problème scientifique, les méthodes existantes, leurs apports, leurs limites, "
                "puis le gap R&D propre au verrou."
            ),
            "writing_policy": {
                "one_state_of_art_for_this_verrou": True,
                "do_not_use_global_markdown_as_source": True,
                "do_not_copy_global_sections": True,
                "use_only_required_or_linked_citations": True,
                "do_not_move_citations_to_other_verrous": True,
                "do_not_create_new_citations": True,
                "do_not_invent_methods_or_examples": True,
                "no_domain_hardcoding": True,
                "article_cards_are_only_scientific_sources": True,
                "phase46_project_argumentation_is_context_not_scientific_proof": True,
            },
        })
    return blueprints


def _v40_enrich_payload_with_dual_writer_blueprints(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict) or not payload.get("ok"):
        return payload
    global_bp = _v40_build_global_writer_blueprint(payload)
    per_bps = _v40_build_per_verrou_writer_blueprints(payload)
    mode = _v40_writer_mode_from_env()
    payload["payload_type"] = OUTPUT_PAYLOAD_TYPE
    payload["writer_mode_requested"] = mode
    payload["available_writer_modes"] = ["global", "per_verrou", "both"]
    payload["global_writer_blueprint"] = global_bp
    payload["per_verrou_writer_blueprints"] = per_bps
    payload["dual_writer_blueprints"] = {
        "blueprint_type": "phase47_dual_mode_blueprints_for_phase5_v4_0",
        "selected_by_front_button": True,
        "default_mode": "global",
        "available_modes": ["global", "per_verrou", "both"],
        "mode_requested": mode,
        "global_writer_blueprint": global_bp,
        "per_verrou_writer_blueprints": per_bps,
        "front_contract": {
            "field_name": "state_of_art_mode",
            "allowed_values": ["global", "per_verrou", "both"],
            "global_label": "État de l’art global",
            "per_verrou_label": "État de l’art par verrou",
            "both_label": "Les deux",
        },
    }
    rules = payload.get("rules") if isinstance(payload.get("rules"), dict) else {}
    rules.update({
        "dual_writer_modes_available": True,
        "phase47_builds_blueprints_before_phase5_writing": True,
        "global_mode_single_unified_state_of_art": True,
        "per_verrou_mode_one_state_of_art_per_verrou": True,
        "per_verrou_mode_uses_phase45_and_phase46_directly_not_global_markdown": True,
    })
    payload["rules"] = rules
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    coverage.update({
        "per_verrou_writer_blueprints_count": len(per_bps),
        "dual_writer_blueprints_ready": bool(per_bps),
    })
    payload["coverage"] = coverage
    return payload


def build_scientific_narrative_payload(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    explicit_confirmed_path = kwargs.pop("confirmed_verrous_path", None)
    explicit_plan_path = kwargs.pop("consultant_plan_path", None)
    requested_output_path = kwargs.pop("output_path", None)
    requested_markdown_output_path = kwargs.pop("markdown_output_path", None)
    organisme = kwargs.get("organisme") if "organisme" in kwargs else (args[0] if len(args) > 0 else "")
    project = kwargs.get("project") if "project" in kwargs else (args[1] if len(args) > 1 else "")
    year = kwargs.get("year") if "year" in kwargs else (args[2] if len(args) > 2 else "")
    p45_path = Path(
        kwargs.get("phase_4_5_path")
        or kwargs.get("scientific_reasoning_payload_path")
        or default_phase_4_5_path(organisme, project, str(year))
    )
    p46_path = Path(
        kwargs.get("phase_4_6_path")
        or kwargs.get("phase46_project_argumentation_payload_path")
        or default_phase_4_6_path(organisme, project, str(year))
    )
    phase45 = read_json(p45_path, {}) or {}
    phase46 = read_json(p46_path, {}) or {}

    try:
        confirmed_path = (
            Path(explicit_confirmed_path)
            if explicit_confirmed_path
            else default_confirmed_verrous_contract_path(organisme, project, str(year))
        )
        if not confirmed_path.is_file():
            raise ContractError(
                "confirmed_verrous_missing",
                "La Phase 4.7 exige le contrat EnnoDiagnostic confirmed_verrous.json.",
                {"expected_path": str(confirmed_path)},
            )
        contract = load_confirmed_contract(confirmed_path)
        assert_same_verrous(
            contract["verrous"],
            extract_verrou_items(phase45),
            observed_name="Phase 4.5",
        )
        assert_same_verrous(
            contract["verrous"],
            extract_verrou_items(phase46),
            observed_name="Phase 4.6",
        )
        payload = _v40_previous_build_scientific_narrative_payload(*args, **kwargs)
        if payload.get("ok"):
            assert_same_verrous(
                contract["verrous"],
                payload.get("verrou_index") or [],
                observed_name="Phase 4.7",
            )
    except ContractError as exc:
        return {
            **exc.as_dict(),
            "phase": "phase_4_7_scientific_narrative_builder",
            "payload_type": "scientific_narrative_payload_contract_error_v1",
            "input_paths": {
                "phase_4_5": str(p45_path),
                "phase_4_6": str(p46_path),
            },
        }

    payload["payload_type"] = "scientific_narrative_payload_canonical_global_v1"
    if not payload.get("ok"):
        payload.setdefault("status", "insufficient_narrative_quality")
    payload["verrou_fingerprint"] = contract["verrou_fingerprint"]
    payload["writer_mode_requested"] = "global"
    payload["available_writer_modes"] = ["global"]
    payload["canonical_verrous"] = [
        {
            "verrou_id": item["verrou_id"],
            "verrou_title": item["verrou_title"],
        }
        for item in contract["verrous"]
    ]
    evidence_sufficiency_by_verrou: List[Dict[str, Any]] = []
    for section in payload.get("verrou_sections_for_phase5") or []:
        if not isinstance(section, dict):
            continue
        coverage = (
            section.get("citation_coverage")
            if isinstance(section.get("citation_coverage"), dict)
            else {}
        )
        direct = safe_citations(coverage.get("direct_citations") or [])
        related = safe_citations(coverage.get("related_citations") or [])
        methodological = safe_citations(
            coverage.get("methodological_citations") or []
        )
        background = safe_citations(
            coverage.get("background_citations") or []
        )
        status = (
            "directly_supported"
            if direct
            else "insufficient_direct_evidence"
            if related or methodological or background
            else "no_scientific_evidence"
        )
        evidence_sufficiency_by_verrou.append(
            {
                "verrou_id": clean_sentence(section.get("verrou_id")),
                "verrou_title": clean_sentence(
                    section.get("verrou_title")
                ),
                "evidence_status": status,
                "direct_citations": direct,
                "related_citations": related,
                "methodological_citations": methodological,
                "background_citations": background,
                "requires_insufficiency_disclosure": (
                    status != "directly_supported"
                ),
            }
        )
    payload["evidence_sufficiency_by_verrou"] = (
        evidence_sufficiency_by_verrou
    )
    quality = (
        payload.get("quality")
        if isinstance(payload.get("quality"), dict)
        else {}
    )
    unsupported_verrous = [
        row
        for row in evidence_sufficiency_by_verrou
        if row.get("evidence_status") != "directly_supported"
    ]
    quality["direct_evidence_complete"] = not unsupported_verrous
    quality["verrous_without_direct_evidence"] = [
        {
            "verrou_id": row.get("verrou_id"),
            "verrou_title": row.get("verrou_title"),
            "evidence_status": row.get("evidence_status"),
        }
        for row in unsupported_verrous
    ]
    quality["warnings"] = unique_clean_list(
        [
            *(quality.get("warnings") or []),
            *(
                [
                    "Un ou plusieurs verrous ne disposent d'aucune preuve "
                    "scientifique directe; la Phase 5 doit déclarer cette "
                    "insuffisance au lieu de transformer les sources connexes "
                    "en preuves."
                ]
                if unsupported_verrous
                else []
            ),
        ]
    )
    payload["quality"] = quality
    payload.setdefault("rules", {}).update(
        {
            "single_canonical_global_writer": True,
            "verrou_contract_checked_before_and_after_phase47": True,
            "title_similarity_fallback_for_verrous_forbidden": True,
            "direct_and_related_evidence_roles_preserved_for_phase5": True,
            "missing_direct_evidence_is_a_disclosure_not_a_fake_citation": True,
        }
    )

    plan_path = (
        Path(explicit_plan_path)
        if explicit_plan_path
        else default_consultant_plan_contract_path(organisme, project, str(year))
    )
    if plan_path.is_file():
        try:
            plan_contract = read_json(plan_path, {}) or {}
            payload["approved_consultant_plan"] = resolve_approved_plan(
                plan_contract,
                require_writing_authorization=False,
            )
            payload["consultant_plan_contract_path"] = str(plan_path)
            payload["consultant_plan_approval_hash"] = plan_contract.get("approval_hash")
        except ContractError as exc:
            return {
                **exc.as_dict(),
                "phase": "phase_4_7_scientific_narrative_builder",
                "payload_type": "scientific_narrative_payload_contract_error_v1",
                "consultant_plan_contract_path": str(plan_path),
            }

    if isinstance(payload, dict):
        if requested_output_path:
            payload["output_path"] = str(Path(requested_output_path))
        if requested_markdown_output_path:
            payload["markdown_output_path"] = str(Path(requested_markdown_output_path))
    if isinstance(payload, dict) and not payload.get("dry_run"):
        out_path = payload.get("output_path")
        md_path = payload.get("markdown_output_path")
        if out_path:
            write_json(out_path, payload)
        if md_path:
            try:
                write_text(md_path, build_markdown_summary(payload))
            except Exception:
                pass
    return payload


# Alias compatibilité
run_phase_4_7_scientific_narrative = build_scientific_narrative_payload
run_phase_4_7 = build_scientific_narrative_payload


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="EnnoScholar Phase 4.7 — Scientific Narrative Builder V3.1")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--phase-4-5-path", default=None)
    parser.add_argument("--phase-4-6-path", default=None)
    parser.add_argument("--state-of-art-mode", choices=["global"], default="global")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = build_scientific_narrative_payload(
        organisme=args.organisme,
        project=args.project,
        year=args.year,
        phase_4_5_path=args.phase_4_5_path,
        phase_4_6_path=args.phase_4_6_path,
        dry_run=args.dry_run,
    )
    print(json.dumps({
        "ok": result.get("ok"),
        "payload_type": result.get("payload_type"),
        "quality": result.get("quality"),
        "verrous_count": result.get("verrous_count"),
        "units": len(result.get("project_specific_method_story_units") or []),
        "main_axes": len(result.get("project_specific_story_axes") or []),
        "traceability_only": len(result.get("traceability_only_units") or []),
        "verrou_sections": len(result.get("verrou_sections_for_phase5") or []),
        "verrou_coverage": result.get("verrou_coverage_summary"),
        "output_path": result.get("output_path"),
        "markdown_output_path": result.get("markdown_output_path"),
    }, ensure_ascii=False, indent=2))

