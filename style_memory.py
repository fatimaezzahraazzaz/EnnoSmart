# -*- coding: utf-8 -*-
from __future__ import annotations

"""Compatibility adapter: CIR_STYLE_MEMORY now reads style examples from Memory V2."""

import json, os, re, sys
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(os.getenv("ENNOSMART_BASE_DIR") or os.getenv("ENNOSMART_ROOT") or Path(__file__).resolve().parent)
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
V2_ROOT = Path(os.getenv("ENNOSMART_EXPERIENCE_MEMORY_V2_DIR", str(ROOT_DIR / "storage" / "experience_memory_v2")))
V2_CATALOG = V2_ROOT / "catalog_v2.json"


def clean_text(x: Any) -> str:
    return str(x or "").strip()


def truncate(x: Any, n: int = 800) -> str:
    s = re.sub(r"\s+", " ", clean_text(x)).strip()
    return s if len(s) <= n else s[:n].rstrip() + "..."


def slug(x: Any) -> str:
    s = str(x or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9_\-]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_") or "unknown"


def style_memory_path(organisme: str) -> Path:
    return V2_ROOT / "style_memory_adapter" / f"{slug(organisme)}_style_from_v2.json"


def _read_json(path: Path, default=None):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def load_style_memory(organisme: str) -> Dict[str, Any]:
    catalog = _read_json(V2_CATALOG, {}) or {}
    return {
        "version": "style_memory_v2_adapter",
        "organisme": organisme,
        "source": "experience_memory_v2",
        "memory_path": str(style_memory_path(organisme)),
        "stats": {
            "chunks_count": catalog.get("chunks_count", 0),
            "cards_count": catalog.get("cards_count", 0),
            "roles": catalog.get("role_counts", {}),
            "domains": catalog.get("domain_counts", {}),
            "projects": catalog.get("projects", []),
        },
        "examples": [],
    }


def retrieve_style_examples(organisme: str, target_role: str, query_text: str, project: str = "", top_k: int = 5,
                            target_domain_key: str = "unknown", strict_domain: bool = True) -> List[Dict[str, Any]]:
    try:
        from modules.EXPERIENCE_MEMORY.memory_v2_retriever import MemoryV2Retriever
    except Exception:
        return []
    retriever = MemoryV2Retriever(organisme=organisme, project=project)
    if not retriever.available:
        return []
    results = retriever.search(query_text or f"style CIR {target_role}", role="style", memory_class="style", top_k=max(top_k * 3, 10), same_organisme_only=True, exclude_current_year=False)
    out = []
    for i, src in enumerate(results):
        meta = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}
        style_role = clean_text(meta.get("style_role") or meta.get("role"))
        if target_role and style_role and style_role not in {"style", target_role}:
            continue
        text = clean_text(src.get("text") or src.get("source_text"))
        if not text:
            continue
        out.append({
            "example_id": meta.get("chunk_id") or src.get("id") or f"v2_style_{i}",
            "organisme": meta.get("organisme") or organisme,
            "project": meta.get("project") or "",
            "year": meta.get("year") or "",
            "source_file": meta.get("source_file") or meta.get("document") or "",
            "role": target_role or style_role or "style",
            "style_role": style_role,
            "domain_key": meta.get("main_domain") or "unknown",
            "domain_label": meta.get("main_domain") or "unknown",
            "section_title": meta.get("section_title") or "",
            "text": text,
            "style_match_score": src.get("score") or meta.get("importance") or 0,
            "use_for_style_only": True,
            "warning": "Memory V2 style only; never factual proof.",
            "metadata": meta,
        })
        if len(out) >= top_k:
            break
    return out


def build_style_block(examples: List[Dict[str, Any]], max_chars_per_example: int = 900) -> str:
    if not examples:
        return "Aucun exemple de style CIR disponible depuis Memory V2."
    lines = [
        "EXEMPLES DE STYLE CIR VALIDÉS — MEMORY V2",
        "Ces exemples servent uniquement au style et à la structure argumentative. Ne jamais copier les faits historiques.",
    ]
    for i, ex in enumerate(examples, 1):
        lines.append(f"\n[STYLE {i}] rôle={ex.get('role')} | projet={ex.get('project')} | année={ex.get('year')} | domaine={ex.get('domain_label')}")
        if ex.get("section_title"):
            lines.append(f"Titre section : {ex.get('section_title')}")
        lines.append("Extrait de style :")
        lines.append(truncate(ex.get("text"), max_chars_per_example))
    return "\n".join(lines).strip()
