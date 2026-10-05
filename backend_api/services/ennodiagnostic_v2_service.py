from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.full_runtime import (
    build_full_runtime,
    parse_domain_tags,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput


FULLTEXT_DIRNAME = "fulltext_rag_v1"
FULLTEXT_MANIFEST_FILENAME = "manifest.json"


def _year(project: Any) -> str:
    value = str(getattr(project, "year", "") or "").strip()
    return value


def get_v2_project_store(project: Any):
    """
    Construit le même ProjectStore que le backend EnnoSmart.
    Aucun accès à l'ancien EnnoDiagnostic V1.
    """
    from modules.RAG.project_store import ProjectStore

    return ProjectStore(
        organisme=getattr(project, "organisme", ""),
        project=getattr(project, "project_name", ""),
        subproject=getattr(project, "subproject_name", None),
        year=_year(project),
    ).ensure()


def v2_fulltext_corpus_dir(project_store: Any) -> Path:
    return Path(project_store.documents_processed_dir) / FULLTEXT_DIRNAME


def load_v2_fulltext_manifest(project_store: Any) -> Dict[str, Any]:
    """Lit le manifeste du corpus fulltext sans dépendre du NLP V1."""
    path = v2_fulltext_corpus_dir(project_store) / FULLTEXT_MANIFEST_FILENAME
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_v2_document_inputs(project_store: Any) -> List[DocumentInput]:
    """
    Lit le corpus texte complet déjà extrait par le backend :
    documents/processed/fulltext_rag_v1/*.json

    Aucun PDF/DOCX n'est relu ici.
    Aucun filtrage sémantique n'est appliqué.
    """
    corpus_dir = v2_fulltext_corpus_dir(project_store)

    if not corpus_dir.exists():
        raise RuntimeError(
            "Corpus texte complet EnnoDiagnostic introuvable. "
            "La préparation des documents doit être exécutée avant EnnoDiagnostic V2."
        )

    inputs: List[DocumentInput] = []

    for path in sorted(corpus_dir.glob("*.json")):
        if path.name.lower() == "manifest.json":
            continue

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(
                f"Impossible de lire le corpus V2 : {path.name}"
            ) from exc

        if not isinstance(payload, dict):
            continue

        # Le CIR final humain peut rester dans le stockage documentaire, mais
        # ne doit jamais servir d'entrée ni de vérité au diagnostic V2.
        if str(payload.get("content_origin") or "").strip().lower().startswith("cir_final") or str(
            payload.get("document_type") or ""
        ).strip().lower().startswith("cir_final"):
            continue

        text = str(payload.get("text") or "").strip()
        if not text:
            continue

        document_name = str(
            payload.get("document")
            or payload.get("file_name")
            or path.stem
        ).strip()

        document_id = str(
            payload.get("document_id")
            or payload.get("text_sha256")
            or path.stem
        ).strip()

        metadata: Dict[str, Any] = {
            "source_path": payload.get("source_path"),
            "extension": payload.get("extension"),
            "loader": payload.get("loader"),
            "document_type": payload.get("document_type"),
            "content_origin": payload.get("content_origin"),
            "source_policy": payload.get("source_policy"),
            "sections_count": payload.get("sections_count"),
            "fulltext_corpus_path": str(path),
        }

        inputs.append(
            DocumentInput(
                document_id=document_id,
                name=document_name,
                text=text,
                declared_mode="raw",
                metadata=metadata,
            )
        )

    if not inputs:
        raise RuntimeError(
            "Le corpus texte complet existe, mais aucun document exploitable "
            "n'a été trouvé pour EnnoDiagnostic V2."
        )

    return inputs


def build_v2_runtime_context(project: Any) -> Dict[str, Any]:
    """
    Prépare les informations de contexte venant du frontend/backend.

    domain_label peut contenir plusieurs domaines :
    'Intelligence artificielle & Data | Informatique & Logiciels'
    """
    domain_tags = parse_domain_tags(
        getattr(project, "domain_label", None)
    )

    return {
        "project_id": getattr(project, "id", None),
        "organisme": getattr(project, "organisme", None),
        "project_name": getattr(project, "project_name", None),
        "subproject_name": getattr(project, "subproject_name", None),
        "year": getattr(project, "year", None),
        "domain_label": getattr(project, "domain_label", None),
        "domain_tags": domain_tags,
    }


def run_ennodiagnostic_v2(project: Any) -> Dict[str, Any]:
    """
    Point d'entrée backend du nouvel EnnoDiagnostic V2.

    Pipeline :
    texte complet déjà extrait
      -> DocumentInput
      -> tags domaine
      -> full_runtime V2
      -> résultat JSON sérialisable

    Cette fonction n'utilise PAS l'ancien EnnoDiagnosticAgent.
    """
    project_store = get_v2_project_store(project)
    documents = load_v2_document_inputs(project_store)
    context = build_v2_runtime_context(project)

    runtime = build_full_runtime(
        domain_tags=context["domain_tags"],
        # Hors des répertoires nettoyés lors d'une relance diagnostic.
        fast_cache_dir=str(Path(project_store.project_dir) / "analysis_cache" / "global_fast"),
    )

    result = runtime.run(documents)
    payload = result.to_dict()

    required_sections = {
        "core",
        "lock_families",
        "project_synthesis",
        "rnd_approach",
        "project_frascati",
        "scoring",
    }
    missing_sections = sorted(required_sections.difference(payload))
    if missing_sections:
        raise RuntimeError(
            "Sortie EnnoDiagnostic V2 incomplète : "
            + ", ".join(missing_sections)
        )

    manifest = load_v2_fulltext_manifest(project_store)

    return {
        "ok": True,
        "status": "completed",
        "engine": "ennodiagnostic_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_context": context,
        "documents_count": len(documents),
        "fulltext_corpus": {
            "version": manifest.get("version"),
            "documents_count": manifest.get("documents_count", len(documents)),
            "total_chars": manifest.get("total_chars"),
            "path": str(v2_fulltext_corpus_dir(project_store)),
        },
        "diagnostic_v2": payload,
    }
