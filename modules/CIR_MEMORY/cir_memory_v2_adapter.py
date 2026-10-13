# -*- coding: utf-8 -*-
from __future__ import annotations

"""Memory V2 continuity adapter for the existing CIR previous/continuity tab."""

import json, os, re, sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.common.runtime_paths import code_root, organism_memory_root, outputs_root

ROOT_DIR = code_root()
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def clean_text(x: Any) -> str:
    return str(x or "").strip()


def truncate(x: Any, n: int = 700) -> str:
    s = re.sub(r"\s+", " ", clean_text(x)).strip()
    return s if len(s) <= n else s[:n].rstrip() + "..."


def slug(x: Any) -> str:
    s = str(x or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9_\-]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_") or "unknown"


def read_json(path: Path, default=None):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _current_nlp_path(organisme: str, project: str, year: str) -> Path:
    return outputs_root() / organisme / project / str(year) / "nlp_result.json"


def _comparison_path(organisme: str, project: str, year: str) -> Path:
    return organism_memory_root() / slug(organisme) / "projects" / slug(project) / "years" / str(year) / "cir_memory" / "memory_v2_continuity_report.json"


def _safe_pack(pack: Any) -> Dict[str, List[Dict[str, Any]]]:
    keys = ["objectifs_locaux", "verrous_rnd_locaux", "methodes_locales", "resultats_locaux", "limites_locales", "contributions_locales", "etat_art_local", "parametres_locaux"]
    out = {k: [] for k in keys}
    if isinstance(pack, dict):
        for k in keys:
            if isinstance(pack.get(k), list):
                out[k] = [x for x in pack[k] if isinstance(x, dict)]
    return out


def _get_current_pack(nlp: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    if not isinstance(nlp, dict):
        return _safe_pack({})
    fg = nlp.get("frascati_guard")
    if isinstance(fg, dict) and isinstance(fg.get("qualified_pack_for_ennodiagnostic"), dict):
        return _safe_pack(fg["qualified_pack_for_ennodiagnostic"])
    for key in ["multi_document_evidence_pack_for_ennodiagnostic", "merged_evidence_pack_for_ennodiagnostic", "evidence_pack_for_ennodiagnostic", "merged_evidence_pack_before_frascati", "evidence_pack_before_frascati"]:
        if isinstance(nlp.get(key), dict):
            return _safe_pack(nlp[key])
    return _safe_pack({})


def _item_text(item: Dict[str, Any]) -> str:
    return "\n".join([clean_text(item.get("section_title") or item.get("title")), clean_text(item.get("text") or item.get("source_text"))]).strip()


def _build_query_from_pack(pack: Dict[str, List[Dict[str, Any]]], max_chars: int = 4000) -> str:
    parts = []
    role_map = {"objectifs_locaux": "objectif", "verrous_rnd_locaux": "verrou", "methodes_locales": "methode", "resultats_locaux": "resultat", "limites_locales": "limite", "contributions_locales": "contribution"}
    for key, role in role_map.items():
        for item in (pack.get(key) or [])[:5]:
            txt = _item_text(item)
            if txt:
                parts.append(f"[{role}] {txt}")
    return truncate("\n".join(parts), max_chars)


def compare_current_with_memory_v2(organisme: str, project: str, year: str, nlp_result_path: Optional[str | Path] = None, top_k: int = 10) -> Dict[str, Any]:
    nlp_path = Path(nlp_result_path) if nlp_result_path else _current_nlp_path(organisme, project, year)
    nlp = read_json(nlp_path, {})
    pack = _get_current_pack(nlp)
    query = _build_query_from_pack(pack)
    try:
        from modules.EXPERIENCE_MEMORY.memory_v2_retriever import MemoryV2Retriever
    except Exception as exc:
        report = {"ok": False, "error": f"memory_v2_retriever introuvable : {exc}", "has_previous_cir": False, "comparisons": [], "verrou_comparisons": []}
        write_json(_comparison_path(organisme, project, year), report)
        return report
    retriever = MemoryV2Retriever(organisme=organisme, project=project, year=year)
    if not query:
        report = {"ok": True, "version": "cir_memory_v2_adapter", "has_previous_cir": False, "message": "Aucune source courante suffisante pour comparer.", "comparisons": [], "verrou_comparisons": []}
        write_json(_comparison_path(organisme, project, year), report)
        return report
    roles = ["objectif", "verrou", "limite", "methode", "resultat", "contribution"]
    by_role = {role: retriever.search(query=query, role=role, top_k=top_k, same_organisme_only=True, exclude_current_year=True) for role in roles}
    similar_projects = retriever._summarize_similar_projects(by_role)
    comparisons = []
    for role, arr in by_role.items():
        for src in arr:
            meta = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}
            comparisons.append({
                "role": role,
                "decision": {"status": "memory_v2_similarity", "label": "Similarité ou continuité potentielle issue de Memory V2", "continuity_score": meta.get("importance"), "novelty_score": None},
                "current_item": {"role": role, "text": query[:900], "source_type": "current_project_query_from_nlp"},
                "best_match": {"previous_candidate": {"role": meta.get("role"), "text": truncate(src.get("text") or src.get("source_text"), 1100), "document": meta.get("document"), "source_path": meta.get("source_path"), "year": meta.get("year"), "project": meta.get("project"), "organisme": meta.get("organisme"), "section_title": meta.get("section_title")}, "similarity_score": meta.get("importance"), "similarity_details": {"source": "memory_v2_chroma", "main_domain": meta.get("main_domain"), "keywords": meta.get("keywords")}},
            })
    report = {"ok": True, "version": "cir_memory_v2_adapter", "generated_at": datetime.now().isoformat(timespec="seconds"), "organisme": organisme, "project": project, "current_year": str(year), "has_previous_cir": bool(comparisons), "source": "experience_memory_v2", "nlp_result_path": str(nlp_path), "summary": {"similar_projects_count": len(similar_projects), "comparisons_count": len(comparisons), "verrou_count": len(by_role.get("verrou") or []), "frascati_context_signal": "memory_v2_similarity"}, "similar_projects": similar_projects, "comparisons": comparisons, "verrou_comparisons": [c for c in comparisons if c.get("role") == "verrou"]}
    write_json(_comparison_path(organisme, project, year), report)
    return report


load_or_create_cir_memory_comparison = compare_current_with_memory_v2
