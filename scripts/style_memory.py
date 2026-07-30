# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(r"C:\EnnoSmart")
STORAGE_DIR = BASE_DIR / "storage" / "organismes"
OUTPUTS_DIR = BASE_DIR / "outputs" / "safe_rag_upload"
V2_ROOT = BASE_DIR / "storage" / "experience_memory_v2"


def clean_text(x: Any) -> str:
    x = str(x or "")
    x = x.replace("\r\n", "\n").replace("\r", "\n")
    x = re.sub(r"[ \t]+", " ", x)
    x = re.sub(r"\n{3,}", "\n\n", x)
    return x.strip()


def strip_accents(x: Any) -> str:
    s = unicodedata.normalize("NFKD", str(x or ""))
    return "".join(c for c in s if not unicodedata.combining(c))


def slug(x: Any) -> str:
    s = strip_accents(x).lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_") or "unknown"


def short_text(text: Any, limit: int = 1600) -> str:
    text = clean_text(text)
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def organisme_dir(organisme: str) -> Path:
    return STORAGE_DIR / slug(organisme)


def project_year_dir(organisme: str, project: str, year: str) -> Path:
    return organisme_dir(organisme) / "projects" / slug(project) / "years" / str(year)


def style_memory_dir(organisme: str) -> Path:
    return organisme_dir(organisme) / "cir_style_memory"


def style_memory_path(organisme: str) -> Path:
    return V2_ROOT / "chroma"


def reformulation_output_path(organisme: str, project: str, year: str) -> Path:
    return project_year_dir(organisme, project, year) / "diagnostics" / "reformulation_rnd_style_cir.json"


def current_nlp_default_path(organisme: str, project: str, year: str) -> Path:
    return OUTPUTS_DIR / organisme / project / str(year) / "nlp_result.json"


def _safe_meta(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata") if isinstance(item, dict) else {}
    return meta if isinstance(meta, dict) else {}


def _item_text(item: Dict[str, Any]) -> str:
    return clean_text(item.get("text") or item.get("source_text") or item.get("content") or item.get("excerpt") or "")


def _get_retriever(organisme: str, project: str = "", year: str = ""):
    from modules.EXPERIENCE_MEMORY.memory_v2_retriever import ExperienceMemoryV2Retriever
    return ExperienceMemoryV2Retriever(organisme=organisme, project=project, year=year)


def load_style_memory(organisme: str) -> Dict[str, Any]:
    try:
        r = _get_retriever(organisme)
        status = r.status()
        catalog = status.get("catalog") or {}
        role_counts = catalog.get("role_counts") or {}
        return {
            "version": "cir_style_memory_adapter_v2",
            "ok": True,
            "organisme": organisme,
            "source": "experience_memory_v2",
            "memory_path": str(style_memory_path(organisme)),
            "principle": "Mémoire rédactionnelle issue de Memory V2. Utilisation limitée au style.",
            "stats": {
                "examples_count": role_counts.get("style", 0),
                "roles": role_counts,
                "projects": catalog.get("projects") or [],
                "domains": catalog.get("domain_counts") or {},
            },
            "examples": [],
            "raw_status": status,
        }
    except Exception as exc:
        return {
            "version": "cir_style_memory_adapter_v2",
            "ok": False,
            "organisme": organisme,
            "source": "experience_memory_v2",
            "memory_path": str(style_memory_path(organisme)),
            "error": str(exc),
            "stats": {},
            "examples": [],
        }


def retrieve_style_examples(
    organisme: str,
    target_role: str,
    query_text: str,
    project: str = "",
    top_k: int = 5,
    target_domain_key: str = "unknown",
    strict_domain: bool = True,
) -> List[Dict[str, Any]]:
    try:
        r = _get_retriever(organisme, project=project)
        items = r.retrieve_style_examples(
            target_role=target_role,
            query_text=query_text or target_role,
            top_k=top_k,
            organism_only=True,
        )
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    for item in items:
        meta = _safe_meta(item)
        text = _item_text(item)
        if not text:
            continue
        role = clean_text(meta.get("style_role") or meta.get("role") or target_role)
        out.append({
            "example_id": meta.get("chunk_id") or meta.get("rag_chunk_id") or item.get("id"),
            "organisme": meta.get("organisme") or organisme,
            "project": meta.get("project") or project,
            "year": meta.get("year") or meta.get("annee") or "",
            "source_file": meta.get("source_file") or meta.get("document") or "",
            "role": role,
            "domain_key": meta.get("main_domain") or meta.get("domain_key") or target_domain_key,
            "domain_label": meta.get("domains") or meta.get("domain_label") or "",
            "section_title": meta.get("section_title") or "",
            "text": text,
            "style_match_score": item.get("style_match_score", 0),
            "validated": True,
            "use_for_style_only": True,
            "warning": "Exemple Memory V2 utilisé uniquement pour le style.",
            "metadata": meta,
        })
    return out[:top_k]


def build_style_block(examples: List[Dict[str, Any]], max_chars_per_example: int = 900) -> str:
    if not examples:
        return "Aucun exemple de style CIR disponible dans Memory V2."
    lines = [
        "EXEMPLES DE STYLE CIR VALIDÉS — MEMORY V2",
        "Ces exemples servent uniquement à imiter le style. Ne jamais copier les faits historiques.",
    ]
    for i, ex in enumerate(examples, start=1):
        lines.append(f"\n[STYLE {i}] rôle={ex.get('role')} | projet={ex.get('project')} | année={ex.get('year')}")
        if ex.get("section_title"):
            lines.append(f"Titre section : {ex.get('section_title')}")
        lines.append("Extrait de style :")
        lines.append(short_text(ex.get("text"), max_chars_per_example))
    return "\n".join(lines).strip()


def register_cir_style_from_nlp_result(*args, **kwargs) -> Dict[str, Any]:
    return {"ok": False, "deprecated": True, "message": "La mémoire de style est maintenant alimentée par Memory V2."}


def rewrite_diagnostic_with_style_memory(*args, **kwargs) -> Dict[str, Any]:
    return {"ok": False, "deprecated": True, "message": "La reformulation avec style est maintenant gérée par EnnoDiagnostic + Memory V2."}


def generate_section_with_style(*args, **kwargs) -> Dict[str, Any]:
    return {"ok": False, "deprecated": True, "message": "Utilise EnnoDiagnosticAgent avec Memory V2."}
