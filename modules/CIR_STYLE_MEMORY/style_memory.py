# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Compatibility adapter:
CIR_STYLE_MEMORY lit maintenant les exemples de style depuis Memory V2.

Source unique :
<racine-projet>/storage/experience_memory_v2

Important :
- pas de base style séparée ;
- pas de copie des anciens CIR ;
- les exemples servent uniquement au style ;
- les faits doivent toujours venir du dossier courant.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(
    os.getenv("ENNOSMART_BASE_DIR")
    or os.getenv("ENNOSMART_ROOT")
    or Path(__file__).resolve().parents[2]
)

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

V2_ROOT = Path(
    os.getenv(
        "ENNOSMART_EXPERIENCE_MEMORY_V2_DIR",
        str(ROOT_DIR / "storage" / "experience_memory_v2"),
    )
)

V2_CATALOG = V2_ROOT / "catalog_v2.json"


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def clean_text(x: Any) -> str:
    return str(x or "").strip()


def truncate(x: Any, n: int = 800) -> str:
    s = re.sub(r"\s+", " ", clean_text(x)).strip()
    return s if len(s) <= n else s[:n].rstrip() + "..."


def slug(x: Any) -> str:
    s = str(x or "").strip().lower()

    tr = str.maketrans(
        "àâäéèêëîïôöùûüç’'",
        "aaaeeeeiioouuuc__",
    )

    s = s.translate(tr)
    s = re.sub(r"[^a-z0-9_\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")

    return s or "unknown"


def _read_json(path: Path, default=None):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass

    return default


def style_memory_path(organisme: str) -> Path:
    """
    Chemin virtuel conservé pour compatibilité avec l'ancien agent.
    On ne crée plus une vraie base style séparée ici.
    """
    return V2_ROOT / "style_memory_adapter" / f"{slug(organisme)}_style_from_v2.json"



def reformulation_output_path(
    organisme: str,
    project: str = "",
    year: str = "",
    filename: str = "style_reformulation_output.json",
) -> Path:
    """
    Compatibilité avec l'ancien EnnoDiagnostic.

    Ancien agent attendait cette fonction pour savoir où écrire/lire
    une sortie de reformulation style. Avec Memory V2, ce fichier n'est
    qu'un snapshot de debug : la vraie mémoire reste dans experience_memory_v2.
    """
    base = V2_ROOT / "style_memory_adapter" / slug(organisme)

    if project:
        base = base / slug(project)

    if year:
        base = base / str(year)

    base.mkdir(parents=True, exist_ok=True)
    return base / filename


def style_reformulation_output_path(
    organisme: str,
    project: str = "",
    year: str = "",
    filename: str = "style_reformulation_output.json",
) -> Path:
    """
    Alias legacy : certaines versions de l'agent importent ce nom.
    """
    return reformulation_output_path(
        organisme=organisme,
        project=project,
        year=year,
        filename=filename,
    )


def save_reformulation_output(
    organisme: str,
    payload: Dict[str, Any],
    project: str = "",
    year: str = "",
    filename: str = "style_reformulation_output.json",
) -> Path:
    """
    Sauvegarde debug compatible avec l'ancien flux.
    """
    path = reformulation_output_path(
        organisme=organisme,
        project=project,
        year=year,
        filename=filename,
    )
    path.write_text(json.dumps(payload or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_reformulation_output(
    organisme: str,
    project: str = "",
    year: str = "",
    filename: str = "style_reformulation_output.json",
) -> Dict[str, Any]:
    """
    Lecture debug compatible avec l'ancien flux.
    """
    return _read_json(
        reformulation_output_path(
            organisme=organisme,
            project=project,
            year=year,
            filename=filename,
        ),
        {},
    ) or {}




# ---------------------------------------------------------------------
# API compatible ancienne mémoire
# ---------------------------------------------------------------------

def load_style_memory(organisme: str) -> Dict[str, Any]:
    catalog = _read_json(V2_CATALOG, {}) or {}

    return {
        "version": "style_memory_v2_adapter",
        "organisme": organisme,
        "source": "experience_memory_v2",
        "memory_path": str(style_memory_path(organisme)),
        "v2_root": str(V2_ROOT),
        "v2_catalog": str(V2_CATALOG),
        "principle": (
            "Les exemples de style sont lus depuis Memory V2. "
            "Ils servent uniquement au style et jamais comme preuves factuelles."
        ),
        "stats": {
            "chunks_count": catalog.get("chunks_count", 0),
            "cards_count": catalog.get("cards_count", 0),
            "roles": catalog.get("role_counts", {}),
            "domains": catalog.get("domain_counts", {}),
            "projects": catalog.get("projects", []),
        },
        "examples": [],
    }


def register_cir_style_from_nlp_result(
    organisme: str,
    project: str,
    year: str,
    nlp_result_path: Optional[str | Path] = None,
    source_file: str = "",
    max_examples_per_role: int = 8,
) -> Dict[str, Any]:
    """
    Compatibilité avec l'ancien EnnoDiagnostic.

    Ancien comportement :
    - créer storage/organismes/<organisme>/cir_style_memory/style_memory.json

    Nouveau comportement :
    - ne rien créer ;
    - Memory V2 contient déjà les chunks role=style ;
    - cette fonction retourne seulement un rapport OK pour éviter de casser l'agent.
    """
    memory = load_style_memory(organisme)

    return {
        "ok": True,
        "version": "style_memory_v2_adapter_register_compat",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year),
        "source": "experience_memory_v2",
        "nlp_result_path": str(nlp_result_path) if nlp_result_path else None,
        "source_file": source_file,
        "examples_found": 0,
        "examples_added": 0,
        "examples_updated_domain": 0,
        "examples_skipped_duplicates": 0,
        "memory_path": str(style_memory_path(organisme)),
        "memory_stats": memory.get("stats", {}),
        "message": (
            "Ancienne API conservée. Aucun JSON style séparé n'est généré. "
            "Les exemples de style sont récupérés directement depuis Memory V2."
        ),
    }


# ---------------------------------------------------------------------
# Retrieval style depuis Memory V2
# ---------------------------------------------------------------------

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
        from modules.EXPERIENCE_MEMORY.memory_v2_retriever import MemoryV2Retriever
    except Exception:
        return []

    retriever = MemoryV2Retriever(
        organisme=organisme,
        project=project,
    )

    if not retriever.available:
        return []

    try:
        results = retriever.search(
            query=query_text or f"style rédaction CIR {target_role}",
            role="style",
            memory_class="style",
            top_k=max(top_k * 4, 12),
            same_organisme_only=True,
            exclude_current_year=False,
        )
    except TypeError:
        # Compatibilité avec les anciennes signatures :
        # search(query_text, role=..., memory_class=..., top_k=...)
        results = retriever.search(
            query_text or f"style rédaction CIR {target_role}",
            role="style",
            memory_class="style",
            top_k=max(top_k * 4, 12),
            same_organisme_only=True,
            exclude_current_year=False,
        )

    out: List[Dict[str, Any]] = []

    # Première passe : rôle style proche du rôle cible
    for i, src in enumerate(results):
        meta = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}

        style_role = clean_text(
            meta.get("style_role")
            or meta.get("relation_key_role")
            or meta.get("role")
        )

        if target_role and style_role and style_role not in {
            "style",
            target_role,
            "autre",
        }:
            continue

        text = clean_text(src.get("text") or src.get("source_text"))

        if not text:
            continue

        out.append(
            {
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
                "warning": (
                    "Exemple Memory V2 utilisé uniquement pour le style. "
                    "Ne jamais utiliser comme preuve factuelle du dossier courant."
                ),
                "metadata": meta,
            }
        )

        if len(out) >= top_k:
            break

    # Fallback : si le filtre style_role est trop strict
    if not out:
        for i, src in enumerate(results[:top_k]):
            meta = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}

            text = clean_text(src.get("text") or src.get("source_text"))

            if not text:
                continue

            style_role = clean_text(
                meta.get("style_role")
                or meta.get("relation_key_role")
                or meta.get("role")
            )

            out.append(
                {
                    "example_id": meta.get("chunk_id") or src.get("id") or f"v2_style_fallback_{i}",
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
                    "warning": (
                        "Exemple Memory V2 utilisé uniquement pour le style. "
                        "Ne jamais utiliser comme preuve factuelle du dossier courant."
                    ),
                    "metadata": meta,
                }
            )

    return out


def build_style_block(
    examples: List[Dict[str, Any]],
    max_chars_per_example: int = 900,
) -> str:
    if not examples:
        return "Aucun exemple de style CIR disponible depuis Memory V2."

    lines = [
        "EXEMPLES DE STYLE CIR VALIDÉS — MEMORY V2",
        (
            "Ces exemples servent uniquement au style, à la structure argumentative "
            "et au vocabulaire. Ne jamais copier les faits historiques."
        ),
    ]

    for i, ex in enumerate(examples, 1):
        lines.append(
            f"\n[STYLE {i}] "
            f"rôle={ex.get('role')} | "
            f"style_role={ex.get('style_role')} | "
            f"projet={ex.get('project')} | "
            f"année={ex.get('year')} | "
            f"domaine={ex.get('domain_label')}"
        )

        if ex.get("section_title"):
            lines.append(f"Titre section : {ex.get('section_title')}")

        lines.append("Extrait de style :")
        lines.append(truncate(ex.get("text"), max_chars_per_example))

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------
# Fonctions legacy optionnelles
# ---------------------------------------------------------------------

def save_style_memory(organisme: str, memory: Dict[str, Any]) -> Path:
    """
    Compatibilité seulement.
    On écrit un petit snapshot debug, mais la vraie mémoire reste Memory V2.
    """
    path = style_memory_path(organisme)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(memory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def empty_memory(organisme: str) -> Dict[str, Any]:
    return load_style_memory(organisme)


def recompute_stats(memory: Dict[str, Any]) -> Dict[str, Any]:
    return memory or {}


# ---------------------------------------------------------------------
# Réécriture / adaptation style V2
# ---------------------------------------------------------------------

def _extract_sections_from_markdown(text: str) -> Dict[str, str]:
    content = clean_text(text)
    if not content:
        return {}
    sections: Dict[str, str] = {}
    current_title = "Diagnostic"
    current_parts: List[str] = []
    for line in content.splitlines():
        m = re.match(r"^\s*#{1,3}\s+(.+?)\s*$", line)
        if m:
            if current_parts:
                sections[current_title] = "\n".join(current_parts).strip()
            current_title = m.group(1).strip()
            current_parts = []
        else:
            current_parts.append(line)
    if current_parts:
        sections[current_title] = "\n".join(current_parts).strip()
    return sections


def _section_to_target_role(section_title: str) -> str:
    t = slug(section_title)
    if "verrou" in t or "incertitude" in t:
        return "verrou"
    if "objectif" in t:
        return "objectif"
    if "demarche" in t or "methode" in t or "experimental" in t:
        return "methode"
    if "resultat" in t or "metrique" in t:
        return "resultat"
    if "etat_art" in t or "frascati" in t:
        return "etat_art"
    if "synthese" in t or "strategique" in t:
        return "synthese"
    return "style"


def _build_style_context_for_diagnostic(
    organisme: str,
    project: str,
    diagnostic_text: str,
    top_k_per_role: int = 2,
) -> Dict[str, Any]:
    sections = _extract_sections_from_markdown(diagnostic_text)
    examples_by_role: Dict[str, List[Dict[str, Any]]] = {}
    roles_seen: set[str] = set()

    for title, body in sections.items():
        role = _section_to_target_role(title)
        if role in roles_seen:
            continue
        roles_seen.add(role)
        examples = retrieve_style_examples(
            organisme=organisme,
            target_role=role,
            query_text=f"{title}\n{body[:1200]}",
            project=project,
            top_k=top_k_per_role,
            strict_domain=False,
        )
        if examples:
            examples_by_role[role] = examples

    all_examples: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for arr in examples_by_role.values():
        for ex in arr:
            eid = clean_text(ex.get("example_id")) or clean_text(ex.get("text"))[:80]
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            all_examples.append(ex)

    return {
        "ok": True,
        "organisme": organisme,
        "project": project,
        "sections_count": len(sections),
        "examples_count": len(all_examples),
        "examples_by_role_count": {k: len(v) for k, v in examples_by_role.items()},
        "examples_by_role": examples_by_role,
        "examples": all_examples,
        "style_block": build_style_block(all_examples, max_chars_per_example=700),
        "principle": (
            "Memory V2 est utilisée uniquement comme mémoire de style. "
            "Les preuves factuelles restent celles du dossier courant/RAG courant."
        ),
    }


def _soft_style_rewrite_without_llm(diagnostic_text: str) -> str:
    text = clean_text(diagnostic_text)
    if not text:
        return text
    replacements = {
        "## Signaux de verrous R&D candidats": "## Verrous R&D candidats à valider",
        "## Démarche expérimentale détectée": "## Démarche expérimentale identifiée",
        "## Résultats et métriques disponibles": "## Résultats, métriques et éléments de validation",
        "## Points à valider par le consultant": "## Points de validation consultant",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def rewrite_diagnostic_with_style_memory(
    diagnostic_text: str,
    organisme: str,
    project: str = "",
    year: str = "",
    llm_client: Any = None,
    max_prompt_chars: int = 12000,
    top_k_per_role: int = 2,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    API attendue par EnnoDiagnostic.
    Utilise Memory V2 uniquement comme guide de style.
    Ne doit jamais ajouter de preuves/faits qui ne sont pas dans diagnostic_text.
    """
    project = project or clean_text(kwargs.get("project_name") or kwargs.get("current_project"))
    llm_client = llm_client or kwargs.get("llm") or kwargs.get("client")

    original = clean_text(diagnostic_text)
    memory = load_style_memory(organisme)

    style_context = _build_style_context_for_diagnostic(
        organisme=organisme,
        project=project,
        diagnostic_text=original,
        top_k_per_role=top_k_per_role,
    )
    examples = style_context.get("examples", []) or []
    style_block = style_context.get("style_block", "")

    if not original:
        return {
            "ok": False,
            "rewritten": "",
            "content": "",
            "text": "",
            "diagnostic": "",
            "error": "diagnostic_text vide",
            "memory_path": str(style_memory_path(organisme)),
            "stats": memory.get("stats", {}),
            "examples_count": len(examples),
            "examples_by_role_count": style_context.get("examples_by_role_count", {}),
            "principle": style_context.get("principle"),
        }

    if not examples:
        fallback_text = _soft_style_rewrite_without_llm(original)
        return {
            "ok": True,
            "rewritten": fallback_text,
            "content": fallback_text,
            "text": fallback_text,
            "diagnostic": fallback_text,
            "changed": fallback_text != original,
            "used_llm": False,
            "source": "memory_v2_style_adapter_no_examples",
            "memory_path": str(style_memory_path(organisme)),
            "stats": memory.get("stats", {}),
            "examples_count": 0,
            "examples_by_role_count": {},
            "principle": style_context.get("principle"),
            "message": "Aucun exemple de style V2 trouvé. Texte conservé avec ajustements légers.",
            "error": None,
        }

    prompt = f"""
Tu es un assistant de rédaction CIR.

Objectif :
Réécrire le diagnostic ci-dessous uniquement pour améliorer le style CIR,
la clarté, la structure argumentative et le vocabulaire consultant.

Règles strictes :
- Ne jamais ajouter de fait nouveau.
- Ne jamais inventer de preuve, chiffre, source, document ou résultat.
- Ne jamais utiliser les exemples Memory V2 comme preuve factuelle.
- Les exemples Memory V2 servent uniquement au style.
- Conserver les verrous, scores, décisions, documents sources et points à valider.
- Garder une structure Markdown claire.
- Si une information est incertaine, conserver le statut "à valider par le consultant".

{style_block}

DIAGNOSTIC À RÉÉCRIRE :
{original}
""".strip()

    if len(prompt) > max_prompt_chars:
        keep = max(2000, max_prompt_chars - len(style_block) - 1400)
        prompt = f"""
Tu es un assistant de rédaction CIR.

Réécris uniquement le style du diagnostic suivant.
N'ajoute aucun fait. Les exemples Memory V2 servent uniquement au style.

{style_block}

DIAGNOSTIC À RÉÉCRIRE :
{original[:keep]}
""".strip()

    rewritten = ""
    llm_error = None

    if llm_client is not None:
        try:
            if hasattr(llm_client, "generate"):
                rewritten = llm_client.generate(prompt)
            elif hasattr(llm_client, "chat"):
                rewritten = llm_client.chat(prompt)
            elif hasattr(llm_client, "complete"):
                rewritten = llm_client.complete(prompt)
            elif callable(llm_client):
                rewritten = llm_client(prompt)

            if isinstance(rewritten, dict):
                rewritten = (
                    rewritten.get("content")
                    or rewritten.get("text")
                    or rewritten.get("answer")
                    or json.dumps(rewritten, ensure_ascii=False)
                )
            rewritten = clean_text(rewritten)
        except Exception as exc:
            rewritten = ""
            llm_error = str(exc)
    else:
        llm_error = "Aucun llm_client fourni."

    used_llm = bool(rewritten)
    if not rewritten:
        rewritten = _soft_style_rewrite_without_llm(original)

    return {
        "ok": True,
        "rewritten": rewritten,
        "content": rewritten,
        "text": rewritten,
        "diagnostic": rewritten,
        "changed": rewritten != original,
        "used_llm": used_llm,
        "source": "memory_v2_style_adapter",
        "version": "style_memory_v2_rewrite_adapter",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "organisme": organisme,
        "project": project,
        "year": str(year or ""),
        "memory_path": str(style_memory_path(organisme)),
        "stats": memory.get("stats", {}),
        "examples_count": len(examples),
        "examples_by_role_count": style_context.get("examples_by_role_count", {}),
        "examples": examples[: min(len(examples), 10)],
        "principle": style_context.get("principle"),
        "prompt_chars": len(prompt),
        "error": llm_error,
        "message": (
            "Diagnostic réécrit avec Memory V2 comme guide de style."
            if used_llm
            else "LLM indisponible : diagnostic conservé avec ajustements légers."
        ),
    }


def build_style_report_for_diagnostic(
    organisme: str,
    project: str,
    diagnostic_text: str,
    year: str = "",
    top_k_per_role: int = 2,
) -> Dict[str, Any]:
    memory = load_style_memory(organisme)
    style_context = _build_style_context_for_diagnostic(
        organisme=organisme,
        project=project,
        diagnostic_text=diagnostic_text,
        top_k_per_role=top_k_per_role,
    )
    return {
        "ok": True,
        "available": True,
        "source": "memory_v2_style_adapter",
        "organisme": organisme,
        "project": project,
        "year": str(year or ""),
        "memory_path": str(style_memory_path(organisme)),
        "stats": memory.get("stats", {}),
        "examples_count": style_context.get("examples_count", 0),
        "examples_by_role_count": style_context.get("examples_by_role_count", {}),
        "principle": style_context.get("principle"),
        "examples": style_context.get("examples", [])[:10],
        "error": None,
    }

