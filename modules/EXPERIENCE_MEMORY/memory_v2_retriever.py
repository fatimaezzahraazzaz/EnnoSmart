# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Memory V2 retriever for EnnoDiagnostic.
Use historical CIR memory only as context/style/continuity, never as current factual proof.
Place in: C:\\EnnoSmart\\modules\\EXPERIENCE_MEMORY\\memory_v2_retriever.py
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.common.runtime_paths import code_root, experience_memory_root

ROOT_DIR = code_root()
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env", override=False)
    load_dotenv(ROOT_DIR / "backend_api" / ".env", override=False)
except Exception:
    pass
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

V2_ROOT = experience_memory_root()
V2_CHROMA_DIR = V2_ROOT / "chroma"


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
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def meta_of(src: Dict[str, Any]) -> Dict[str, Any]:
    meta = src.get("metadata") if isinstance(src, dict) else None
    return meta if isinstance(meta, dict) else {}


def source_text(src: Dict[str, Any]) -> str:
    if not isinstance(src, dict):
        return ""
    return clean_text(src.get("text") or src.get("source_text") or src.get("content") or src.get("excerpt") or "")


def source_doc(src: Dict[str, Any]) -> str:
    m = meta_of(src)
    return clean_text(m.get("document") or m.get("source_file") or "")


def source_project_key(src: Dict[str, Any]) -> str:
    m = meta_of(src)
    return f"{m.get('organisme')}::{m.get('project')}::{m.get('subproject') or ''}::{m.get('year')}"


class MemoryV2Retriever:
    def __init__(
        self,
        organisme: str,
        project: str = "",
        year: str = "",
        chroma_dir: Optional[Path] = None,
        subproject: str = "",
    ):
        self.organisme = clean_text(organisme)
        self.project = clean_text(project)
        self.subproject = clean_text(subproject)
        self.year = str(year or "")
        self.chroma_dir = Path(chroma_dir or V2_CHROMA_DIR)
        try:
            from modules.RAG.vector_store import RAGVectorStore
            self.vector_store = RAGVectorStore(self.chroma_dir)
            self.available = True
            self.error = ""
        except Exception as exc:
            self.vector_store = None
            self.available = False
            self.error = str(exc)

    @property
    def global_collection(self) -> str:
        return "ennosmart_memory_v2_global"

    @property
    def organism_collection(self) -> str:
        # Compatibilité d'API : Memory V2 ne conserve plus de collection par
        # organisme. Le filtrage est appliqué aux métadonnées du résultat.
        return self.global_collection

    def _identity_affinity(self, metadata: Dict[str, Any]) -> int:
        """Priorise le même projet/sous-projet sans exclure les projets proches."""
        historical_project = clean_text(metadata.get("project"))
        historical_subproject = clean_text(metadata.get("subproject"))
        current_combined = clean_text(" ".join(
            value for value in (self.project, self.subproject) if value
        ))
        historical_combined = clean_text(" ".join(
            value for value in (historical_project, historical_subproject) if value
        ))
        if not current_combined:
            return 0

        current_slug = slug(current_combined)
        historical_slug = slug(historical_combined)
        if current_slug == historical_slug:
            return 100

        score = 0
        if historical_project and slug(historical_project) == slug(self.project):
            score += 60
        current_tokens = set(current_slug.split("_"))
        historical_tokens = set(historical_slug.split("_"))
        score += 10 * len(current_tokens & historical_tokens)
        if historical_subproject and slug(historical_subproject) in current_tokens:
            score += 30
        return score

    def search(self, query: str, role: Optional[str] = None, memory_class: Optional[str] = None, top_k: int = 8,
               same_organisme_only: bool = True, exclude_current_year: bool = True) -> List[Dict[str, Any]]:
        if not self.available or self.vector_store is None:
            return []
        collection = self.global_collection
        try:
            results = self.vector_store.search(
                collection_name=collection,
                query=query,
                role_filter=role or None,
                top_k=top_k * 5,
                metadata_filter={"organisme": self.organisme} if same_organisme_only and self.organisme else None,
                oversample=8,
            )
        except TypeError:
            try:
                results = self.vector_store.search(collection_name=collection, query=query, top_k=top_k * 5, role_filter=role or None)
            except Exception:
                return []
        except Exception:
            return []

        ranked, seen = [], set()
        for vector_rank, src in enumerate(results or []):
            m = meta_of(src)
            if not m:
                continue
            if same_organisme_only and self.organisme and slug(m.get("organisme")) != slug(self.organisme):
                continue
            if memory_class and clean_text(m.get("memory_class") or m.get("memory_type_v2")) != memory_class:
                continue
            if exclude_current_year and self.year and str(m.get("year")) == str(self.year):
                continue
            m["historical_memory"] = True
            m["memory_v2_usage"] = "context_only_not_current_fact"
            m["warning"] = "Memory V2: historical context/style only, not current factual proof."
            affinity = self._identity_affinity(m)
            m["memory_v2_identity_affinity"] = affinity
            src["metadata"] = m
            sig = (source_project_key(src), clean_text(m.get("role")), truncate(source_text(src), 220))
            if sig in seen:
                continue
            seen.add(sig)
            ranked.append((affinity, vector_rank, src))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [src for _, _, src in ranked[:top_k]]

    def build_query_from_current_sources(self, sections: Dict[str, List[Dict[str, Any]]], max_chars: int = 3500) -> str:
        parts = []
        for key in ["objectifs", "verrous", "limites", "methodes", "resultats", "parametres"]:
            for src in (sections.get(key) or [])[:5]:
                txt = source_text(src)
                if txt:
                    parts.append(f"[{key}] {txt}")
        return truncate("\n".join(parts), max_chars)

    def retrieve_diagnostic_memory(self, sections: Dict[str, List[Dict[str, Any]]], top_k_per_role: int = 5) -> Dict[str, Any]:
        query = self.build_query_from_current_sources(sections)
        if not query:
            return {"ok": False, "available": self.available, "error": self.error, "prompt_block": "Memory V2 non interrogée."}
        by_role = {}
        for role in ["objectif", "verrou", "limite", "methode", "resultat", "contribution"]:
            by_role[role] = self.search(query, role=role, top_k=top_k_per_role, same_organisme_only=True, exclude_current_year=True)
        style_examples = self.search(query, role="style", memory_class="style", top_k=8, same_organisme_only=True, exclude_current_year=True)
        similar_projects = self._summarize_similar_projects(by_role)
        prompt_block = self.build_prompt_block(similar_projects, by_role, style_examples)
        return {
            "ok": True,
            "available": self.available,
            "organisme": self.organisme,
            "project": self.project,
            "year": self.year,
            "query_preview": truncate(query, 900),
            "similar_projects": similar_projects,
            "by_role": by_role,
            "style_examples": style_examples,
            "prompt_block": prompt_block,
            "principle": "Memory V2 = continuity/similarity/style only. Current RAG remains the only factual proof.",
        }

    def _summarize_similar_projects(self, by_role: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        acc: Dict[str, Dict[str, Any]] = {}
        for role, arr in by_role.items():
            for src in arr or []:
                m = meta_of(src)
                key = source_project_key(src)
                item = acc.setdefault(key, {
                    "project_key": key,
                    "organisme": m.get("organisme"),
                    "project": m.get("project"),
                    "subproject": m.get("subproject") or "",
                    "year": m.get("year"),
                    "roles": {},
                    "documents": set(),
                    "examples": [],
                    "score": 0,
                })
                item["roles"][role] = item["roles"].get(role, 0) + 1
                if source_doc(src):
                    item["documents"].add(source_doc(src))
                if len(item["examples"]) < 3:
                    item["examples"].append({"role": role, "section_title": m.get("section_title"), "text": truncate(source_text(src), 380)})
                item["score"] += 1
        out = []
        for item in acc.values():
            item["documents"] = sorted(list(item["documents"]))[:5]
            out.append(item)
        out.sort(key=lambda x: x["score"], reverse=True)
        return out[:8]

    def build_prompt_block(self, similar_projects: List[Dict[str, Any]], by_role: Dict[str, List[Dict[str, Any]]], style_examples: List[Dict[str, Any]]) -> str:
        lines = [
            "## Mémoire V2 — projets similaires / continuité / style",
            "Règles : anciens CIR = contexte historique/style, jamais preuve factuelle du dossier courant.",
            "Utiliser pour repérer continuité, nouveauté, risque de faux positif et reformulation CIR.",
            "",
            "### Projets historiques proches",
        ]
        if not similar_projects:
            lines.append("- Aucun projet proche trouvé.")
        for i, p in enumerate(similar_projects[:6], 1):
            roles = ", ".join(f"{k}:{v}" for k, v in (p.get("roles") or {}).items())
            hierarchy = " / ".join(str(value) for value in (
                p.get("organisme"), p.get("project"), p.get("subproject"), p.get("year")
            ) if value)
            lines.append(f"- M{i} | {hierarchy} | score={p.get('score')} | rôles={roles}")
            for ex in p.get("examples", [])[:2]:
                lines.append(f"  - {ex.get('role')} : {truncate(ex.get('text'), 260)}")
        lines.append("\n### Verrous / limites historiques proches")
        for role in ["verrou", "limite"]:
            for i, src in enumerate((by_role.get(role) or [])[:5], 1):
                m = meta_of(src)
                lines.append(f"- {role.upper()} {i} | {m.get('project')} {m.get('year')} | {truncate(source_text(src), 330)}")
        lines.append("\n### Méthodes / résultats historiques proches")
        for role in ["methode", "resultat", "contribution"]:
            for i, src in enumerate((by_role.get(role) or [])[:3], 1):
                m = meta_of(src)
                lines.append(f"- {role.upper()} {i} | {m.get('project')} {m.get('year')} | {truncate(source_text(src), 280)}")
        lines.append("\n### Exemples de style consultant")
        if not style_examples:
            lines.append("- Aucun exemple de style trouvé.")
        for i, src in enumerate(style_examples[:5], 1):
            m = meta_of(src)
            lines.append(f"- STYLE {i} | {m.get('project')} {m.get('year')} | {truncate(source_text(src), 430)}")
        return "\n".join(lines).strip()


def retrieve_memory_v2_for_diagnostic(organisme: str, project: str, year: str, sections: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    return MemoryV2Retriever(organisme=organisme, project=project, year=year).retrieve_diagnostic_memory(sections)
