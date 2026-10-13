# -*- coding: utf-8 -*-
from __future__ import annotations

"""
cir_style_retriever.py

Phase 3 — Dynamic Few-shot Retrieval

Rôle :
- récupérer dynamiquement des exemples CIR similaires depuis Memory V2 ;
- utiliser uniquement les fonctions existantes de CIR_STYLE_MEMORY ;
- préparer une sortie "style_memory" exploitable par le Style Extractor ;
- ne jamais utiliser Memory V2 comme preuve scientifique.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.common.runtime_paths import organism_memory_root


ROOT_DIR = organism_memory_root()


# ============================================================
# Import des fonctions existantes, sans duplication
# ============================================================

def _load_existing_style_adapter():
    """
    On utilise le module existant :
    modules.CIR_STYLE_MEMORY.style_memory
    """

    import_errors = []

    candidates = [
        "modules.CIR_STYLE_MEMORY.style_memory",
    ]

    for module_name in candidates:
        try:
            module = __import__(
                module_name,
                fromlist=[
                    "retrieve_style_examples",
                    "build_style_block",
                    "clean_text",
                    "truncate",
                    "slug",
                ],
            )

            return {
                "module_name": module_name,
                "retrieve_style_examples": getattr(module, "retrieve_style_examples"),
                "build_style_block": getattr(module, "build_style_block"),
                "clean_text": getattr(module, "clean_text"),
                "truncate": getattr(module, "truncate"),
                "slug": getattr(module, "slug"),
            }

        except Exception as exc:
            import_errors.append(f"{module_name}: {exc}")

    raise ImportError(
        "Impossible d'importer le module CIR_STYLE_MEMORY existant. "
        "Vérifie le fichier modules/CIR_STYLE_MEMORY/style_memory.py. "
        "Erreurs : " + " | ".join(import_errors)
    )


_STYLE = _load_existing_style_adapter()

retrieve_style_examples = _STYLE["retrieve_style_examples"]
build_style_block = _STYLE["build_style_block"]
clean_text = _STYLE["clean_text"]
truncate = _STYLE["truncate"]
legacy_slug = _STYLE["slug"]


# ============================================================
# Helpers locaux Phase 3
# ============================================================

STYLE_TARGET_ROLES = [
    "etat_art",
    "limite",
    "verrou",
    "contribution",
    "objectif",
]


def fs_slug(value: Any) -> str:
    """
    Slug de fichier stable, insensible aux séparateurs et aux accents.
    """
    s = str(value or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def _read_json(path: str | Path, default=None):
    path = Path(path)
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def style_memory_output_path(organisme: str, project: str, year: str) -> Path:
    return (
        ROOT_DIR
        / fs_slug(organisme)
        / "projects"
        / fs_slug(project)
        / "years"
        / str(year)
        / "ennoscholar"
        / "state_of_art_payload"
        / "phase_3_style_memory"
        / "style_memory_payload.json"
    )


def _field_text(obj: Dict[str, Any], *keys: str) -> str:
    """
    Lecture robuste d'un champ.
    Gère string, dict, list.
    """
    if not isinstance(obj, dict):
        return ""

    for key in keys:
        value = obj.get(key)

        if value is None:
            continue

        if isinstance(value, str):
            txt = clean_text(value)
            if txt:
                return txt

        if isinstance(value, (int, float)):
            return str(value)

        if isinstance(value, dict):
            for sub_key in ["name", "title", "label", "value", "text", "project_name"]:
                txt = clean_text(value.get(sub_key))
                if txt:
                    return txt

        if isinstance(value, list):
            parts = []
            for item in value[:5]:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    txt = _field_text(
                        item,
                        "title",
                        "name",
                        "label",
                        "text",
                        "abstract",
                        "summary",
                    )
                    if txt:
                        parts.append(txt)
            joined = clean_text(" | ".join(parts))
            if joined:
                return joined

    return ""


def _recursive_interesting_texts(obj: Any, max_items: int = 80) -> List[str]:
    """
    Fallback si la structure du JSON Phase 1 change.
    On récupère seulement des champs utiles pour construire la query.
    """
    interesting_keys = {
        "verrou_title",
        "verrou",
        "title",
        "objectif",
        "objectif_rd",
        "objectif_r&d",
        "objectif_r_d",
        "contexte",
        "contexte_projet",
        "domain_label",
        "abstract",
        "abstract_fr",
        "abstract_original",
        "abstract_for_writer",
        "summary",
        "resume",
        "description",
        "problem",
        "limites",
        "limitations",
    }

    found: List[str] = []

    def walk(x: Any):
        if len(found) >= max_items:
            return

        if isinstance(x, dict):
            for k, v in x.items():
                if len(found) >= max_items:
                    return

                key = str(k).strip()
                if key in interesting_keys:
                    if isinstance(v, str):
                        txt = clean_text(v)
                        if txt and len(txt) >= 20:
                            found.append(txt)
                    elif isinstance(v, (int, float)):
                        found.append(str(v))
                    elif isinstance(v, dict):
                        txt = _field_text(v, "title", "name", "label", "text", "summary")
                        if txt and len(txt) >= 20:
                            found.append(txt)

                if isinstance(v, (dict, list)):
                    walk(v)

        elif isinstance(x, list):
            for item in x[:30]:
                walk(item)

    walk(obj)

    out: List[str] = []
    seen = set()
    for txt in found:
        key = re.sub(r"\W+", " ", txt.lower()).strip()[:220]
        if key in seen:
            continue
        seen.add(key)
        out.append(txt)

    return out


def build_query_from_phase1_payload(phase1_payload: Dict[str, Any]) -> str:
    """
    Construit une requête à partir du payload Phase 1.
    Cette requête sert seulement à retrouver des exemples de style similaires.
    """

    if not isinstance(phase1_payload, dict) or not phase1_payload:
        return ""

    parts: List[str] = []

    project_name = _field_text(
        phase1_payload,
        "project_name",
        "project",
        "project_label",
    )
    organisme = _field_text(
        phase1_payload,
        "organisme",
        "organism",
        "client",
    )
    domain = _field_text(
        phase1_payload,
        "domain_label",
        "domain",
        "main_domain",
    )

    if organisme:
        parts.append(f"Organisme : {organisme}")
    if project_name:
        parts.append(f"Projet : {project_name}")
    if domain:
        parts.append(f"Domaine : {domain}")

    diagnostic_context = phase1_payload.get("diagnostic_context")
    if isinstance(diagnostic_context, dict):
        for key in ["objectif_global", "resume_strategique", "contexte_projet"]:
            value = _field_text(diagnostic_context, key)
            if value:
                parts.append(f"[diagnostic_{key}] {truncate(value, 900)}")

    selection_summary = phase1_payload.get("selection_summary")
    if isinstance(selection_summary, dict):
        value = _field_text(selection_summary, "summary", "text", "description")
        if value:
            parts.append(f"[selection_summary] {truncate(value, 900)}")

    verrous = phase1_payload.get("verrous") or phase1_payload.get("locks") or []
    if isinstance(verrous, dict):
        verrous = list(verrous.values())

    for verrou in verrous or []:
        if not isinstance(verrou, dict):
            continue

        verrou_title = _field_text(
            verrou,
            "verrou_title",
            "title",
            "name",
            "label",
        )
        objectif = _field_text(
            verrou,
            "objectif_rd",
            "objectif_r&d",
            "objectif_r_d",
            "objectif",
        )
        contexte = _field_text(
            verrou,
            "contexte_projet",
            "contexte",
            "context",
            "description",
        )

        if verrou_title:
            parts.append(f"[verrou] {verrou_title}")

        if objectif:
            parts.append(f"[objectif] {objectif}")

        if contexte:
            parts.append(f"[contexte] {truncate(contexte, 1200)}")

        for article_key in [
            "articles_directs",
            "articles_connexes",
            "articles_fondamentaux",
            "direct_articles",
            "related_articles",
            "fundamental_articles",
        ]:
            articles = verrou.get(article_key) or []
            if isinstance(articles, dict):
                articles = list(articles.values())

            for article in articles[:4]:
                if not isinstance(article, dict):
                    continue

                title = _field_text(article, "title", "name")
                abstract = _field_text(
                    article,
                    "abstract_for_writer",
                    "abstract_fr",
                    "abstract_original",
                    "abstract",
                    "summary",
                    "resume",
                )

                if title:
                    parts.append(f"[article] {title}")
                if abstract:
                    parts.append(f"[abstract] {truncate(abstract, 700)}")

    query = truncate("\n".join(parts), 5000)

    # Fallback robuste si le JSON Phase 1 a une structure différente.
    if not query:
        fallback_texts = _recursive_interesting_texts(phase1_payload)
        query = truncate("\n".join(fallback_texts), 5000)

    return query


def normalize_style_example(
    example: Dict[str, Any],
    target_role: str,
    index: int,
) -> Dict[str, Any]:
    """
    Normalise la sortie de retrieve_style_examples()
    pour obtenir style_memory[] propre.
    """

    return {
        "memory_id": example.get("example_id") or f"style_memory_{index}",
        "target_role": target_role,
        "role": example.get("role") or target_role,
        "style_role": example.get("style_role"),
        "text": clean_text(example.get("text")),
        "organisme": example.get("organisme"),
        "project": example.get("project"),
        "year": example.get("year"),
        "source_file": example.get("source_file"),
        "section_title": example.get("section_title"),
        "domain_key": example.get("domain_key"),
        "domain_label": example.get("domain_label"),
        "style_match_score": example.get("style_match_score"),
        "usage": "style_only",
        "memory_as_proof": False,
        "can_be_cited": False,
        "warning": (
            "Exemple Memory V2 utilisé uniquement pour le style. "
            "Ne jamais utiliser comme preuve scientifique."
        ),
        "metadata": example.get("metadata", {}),
    }


def retrieve_dynamic_style_memory(
    organisme: str,
    project: str,
    year: str,
    phase1_payload_path: Optional[str | Path] = None,
    phase1_payload: Optional[Dict[str, Any]] = None,
    query_text: Optional[str] = None,
    top_k_per_role: int = 3,
    max_total_examples: int = 12,
    output_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """
    Fonction principale du premier fichier Phase 3.

    Sortie :
    - style_memory[] : exemples récupérés depuis Memory V2
    - style_block : bloc texte déjà formaté par ton code existant
    """

    query_source = "unknown"

    if query_text:
        query = clean_text(query_text)
        query_source = "manual_query"
    else:
        payload = phase1_payload or {}
        if not payload and phase1_payload_path:
            payload = _read_json(phase1_payload_path, {}) or {}

        query = build_query_from_phase1_payload(payload)
        query_source = "phase1_payload"

    out_path = Path(output_path) if output_path else style_memory_output_path(
        organisme=organisme,
        project=project,
        year=year,
    )

    if not query:
        result = {
            "ok": False,
            "phase": "phase_3_dynamic_fewshot_style",
            "step": "cir_style_retriever",
            "status": "empty_query",
            "message": "Impossible de construire une requête depuis le payload Phase 1.",
            "adapter_used": _STYLE["module_name"],
            "query_source": query_source,
            "phase1_payload_path": str(phase1_payload_path) if phase1_payload_path else None,
            "style_memory": [],
            "style_block": "",
            "memory_as_proof": False,
            "output_path": str(out_path),
        }
        _write_json(out_path, result)
        return result

    raw_examples: List[Dict[str, Any]] = []
    normalized: List[Dict[str, Any]] = []

    seen = set()
    idx = 1

    for target_role in STYLE_TARGET_ROLES:
        examples = retrieve_style_examples(
            organisme=organisme,
            project=project,
            target_role=target_role,
            query_text=query,
            top_k=top_k_per_role,
            strict_domain=False,
        )

        for ex in examples:
            text = clean_text(ex.get("text"))
            if len(text) < 120:
                continue

            dedupe_key = re.sub(r"\W+", " ", text.lower()).strip()[:300]
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            raw_examples.append(ex)
            normalized.append(
                normalize_style_example(
                    example=ex,
                    target_role=target_role,
                    index=idx,
                )
            )
            idx += 1

            if len(normalized) >= max_total_examples:
                break

        if len(normalized) >= max_total_examples:
            break

    style_block = build_style_block(
        raw_examples,
        max_chars_per_example=900,
    )

    result = {
        "ok": True,
        "phase": "phase_3_dynamic_fewshot_style",
        "step": "cir_style_retriever",
        "payload_type": "style_memory_payload_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "adapter_used": _STYLE["module_name"],
        "query_source": query_source,
        "phase1_payload_path": str(phase1_payload_path) if phase1_payload_path else None,
        "query_preview": truncate(query, 1200),
        "roles_requested": STYLE_TARGET_ROLES,
        "style_memory_count": len(normalized),
        "style_memory": normalized,
        "style_block": style_block,
        "rules": {
            "usage": "style_only",
            "memory_as_proof": False,
            "can_be_cited": False,
            "scientific_sources_allowed": "article_cards_only",
        },
        "output_path": str(out_path),
    }

    _write_json(out_path, result)
    return result
