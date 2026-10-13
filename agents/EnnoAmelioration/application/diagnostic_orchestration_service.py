from __future__ import annotations

"""Orchestration ciblée d'EnnoDiagnostic depuis EnnoAmel.

V2.5 — EnnoDiagnostic V2, corpus ciblé isolé :

* EnnoAmel n'analyse JAMAIS automatiquement les documents bruts du projet
  lorsqu'un consultant travaille sur un texte/CIR chargé dans EnnoAmel.
* Le corpus Diagnostic de ce tour vient exclusivement du texte actuellement
  fourni à EnnoAmel : section cible + contexte CIR local utile.
* Ce corpus est conservé en fulltext, indexé dans un espace RAG isolé,
  puis analysé par EnnoDiagnostic V2 (Fast Path et fallback sémantique V2).
* Un diagnostic historique du projet peut être conservé comme contexte secondaire
  consultatif, mais il ne remplace jamais le diagnostic ciblé et ses verrous ne
  sont pas injectés dans la recherche EnnoScholar de ce tour.

Ainsi : EnnoAmel orchestre réellement fulltext -> EnnoDiagnostic V2 -> EnnoScholar sans
réimplémenter les modèles et sans confondre le CIR à améliorer avec les documents
bruts déjà attachés au projet.
"""

import hashlib
import importlib
import json
import re
from datetime import datetime
from pathlib import Path

from modules.common.runtime_paths import code_root
from typing import Any

from ..domain.models import ImprovementRequest, TargetScope
from .agent_adapters import diagnostic_context


SCOPED_DIAGNOSTIC_VERSION = "v2_5_ennoamel_scoped_global_fast"


class DiagnosticOrchestrationError(RuntimeError):
    pass


def _diagnostic_service() -> Any:
    errors: list[str] = []
    for module_name in (
        "services.diagnostic_service",
        "backend_api.services.diagnostic_service",
    ):
        try:
            return importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - dépend du backend réel
            errors.append(f"{module_name}: {exc}")
    raise DiagnosticOrchestrationError(
        "Le service backend EnnoDiagnostic est introuvable. " + " | ".join(errors)
    )


def _clean(value: Any, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip()
    return text


def _year(project: Any) -> str:
    for attr in (
        "year",
        "annee",
        "cir_year",
        "exercise_year",
        "fiscal_year",
        "project_year",
    ):
        value = getattr(project, attr, None)
        match = re.search(r"\b(19|20)\d{2}\b", str(value or ""))
        if match:
            return match.group(0)
    return str(datetime.now().year)


def _project_name(project: Any, request: ImprovementRequest) -> str:
    return _clean(
        request.project_name
        or getattr(project, "project_name", None)
        or getattr(project, "name", None)
        or getattr(project, "title", None)
        or f"project_{getattr(project, 'id', 'unknown')}",
        180,
    ) or "project_unknown"


def _organisation(project: Any) -> str:
    for attr in ("organisme", "organization", "organisation", "client_name", "company_name"):
        value = _clean(getattr(project, attr, None), 180)
        if value:
            return value
    return "ennoamel"


def _scope_hash(project: Any, request: ImprovementRequest) -> str:
    basis = "\n".join(
        [
            str(getattr(project, "id", "")),
            request.target_section_id or "",
            request.target_section_title or "",
            request.target_text or "",
            # Le document courant est inclus pour invalider le cache lorsqu'un
            # autre CIR contient une section textuellement identique.
            hashlib.sha256((request.full_text or "").encode("utf-8", errors="ignore")).hexdigest(),
            SCOPED_DIAGNOSTIC_VERSION,
        ]
    )
    return hashlib.sha256(basis.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _local_cir_context(request: ImprovementRequest, flank_chars: int = 14000) -> str:
    """Retourne uniquement le contexte du CIR fourni à EnnoAmel, jamais les RAW DB."""

    target = str(request.target_text or "")
    full = str(request.full_text or "")
    if not full or full == target:
        return ""

    # Pour une cible document complet, le target est déjà le corpus : aucun
    # deuxième document redondant n'est nécessaire.
    if request.target_scope == TargetScope.FULL_DOCUMENT:
        return ""

    index = full.find(target) if target else -1
    if index < 0:
        return ""

    start = max(0, index - flank_chars)
    end = min(len(full), index + len(target) + flank_chars)
    context = full[start:end]

    # Retirer la section cible évite de la compter deux fois dans le corpus.
    local_index = context.find(target)
    if local_index >= 0:
        context = context[:local_index] + "\n" + context[local_index + len(target):]
    return context.strip()


def _virtual_documents(request: ImprovementRequest, scope_key: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Construit des documents virtuels à partir du CIR courant dans EnnoAmel."""

    target_text = str(request.target_text or "").strip()
    if not target_text:
        raise DiagnosticOrchestrationError(
            "La section/texte cible EnnoAmel est vide : EnnoDiagnostic ciblé ne peut pas être lancé."
        )

    section_label = _clean(
        request.target_section_title or request.target_section_id or "section_cible",
        220,
    )
    primary_name = f"ennoamel_section_{scope_key}.txt"
    documents: list[dict[str, Any]] = [
        {
            "document": primary_name,
            "file_name": primary_name,
            "source_path": f"ennoamel://scope/{scope_key}/target",
            "text": target_text,
            "section_title": section_label,
            "document_type": "pre_cir_client",
            "source_policy": "core_or_useful",
            "content_origin": "ennoamel_cir_target",
            "pre_cir_client": True,
            "needs_human_validation": True,
            "validation_status": "consultant_required",
            "document_weight": 1.30,
            "source_weight": 1.30,
        }
    ]
    modes = {primary_name: "pre_cir"}

    local_context = _local_cir_context(request)
    if local_context:
        context_name = f"ennoamel_cir_context_{scope_key}.txt"
        documents.append(
            {
                "document": context_name,
                "file_name": context_name,
                "source_path": f"ennoamel://scope/{scope_key}/local-cir-context",
                "text": local_context,
                "section_title": "Contexte CIR local autour de la section cible",
                "document_type": "pre_cir_client",
                "source_policy": "context_only",
                "content_origin": "ennoamel_cir_local_context",
                "pre_cir_client": True,
                "needs_human_validation": True,
                "validation_status": "consultant_required",
                "document_weight": 1.05,
                "source_weight": 1.05,
            }
        )
        modes[context_name] = "pre_cir"

    return documents, modes


def _ensure_imports() -> None:
    root = code_root()
    import sys

    for path in (root, root / "backend_api"):
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _scoped_project_store(organisation: str, scope_project: str, year: str) -> Any:
    _ensure_imports()
    try:
        from modules.RAG.project_store import ProjectStore
    except Exception as exc:  # pragma: no cover
        raise DiagnosticOrchestrationError(f"ProjectStore EnnoSmart indisponible : {exc}") from exc
    return ProjectStore(
        organisme=organisation,
        project=scope_project,
        year=year,
    ).ensure()


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")






def _extract_final_locks(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Use the same qualified V2 locks as the main diagnostic, including an empty list."""
    service = _diagnostic_service()
    locks = service._extract_v2_verrous_from_report(report)
    if not isinstance(locks, list):
        raise DiagnosticOrchestrationError("Sortie verrou V2 ciblée invalide.")
    return [dict(row) for row in locks if isinstance(row, dict)]


def _context_from_scoped_run(
    *,
    request: ImprovementRequest,
    scope_key: str,
    scope_project: str,
    analysis: dict[str, Any],
    report: dict[str, Any],
    index_report: dict[str, Any],
    report_path: str,
    cache_hit: bool,
    project_background: dict[str, Any] | None,
) -> dict[str, Any]:
    locks = _extract_final_locks(report)
    evidence_items: list[dict[str, Any]] = []
    verrou_payload: list[dict[str, Any]] = []

    for index, row in enumerate(locks[:30], start=1):
        source_json = row.get("source_json") if isinstance(row.get("source_json"), dict) else {}
        title = _clean(
            row.get("title")
            or row.get("titre")
            or row.get("verrou")
            or row.get("llm_title")
            or row.get("verrou_title")
            or source_json.get("scientific_lock"),
            500,
        )
        if not title:
            continue
        text = _clean(
            row.get("justification")
            or row.get("description")
            or row.get("text")
            or source_json.get("evidence_summary"),
            7000,
        )
        raw_id = _clean(
            row.get("id")
            or row.get("entity_id")
            or row.get("lock_group_id")
            or source_json.get("lock_group_id"),
            120,
        )
        lock_id = raw_id or f"{scope_key}-{index}"
        evidence_id = f"D:verrou:scope-{lock_id}"
        verrou_payload.append(
            {
                "id": f"scope-{lock_id}",
                "title": title,
                "score": row.get("score") or row.get("frascati_score"),
                "justification": text,
                "consultant_status": row.get("consultant_status") or "en_attente",
                "needs_human_validation": True,
                "source_refs": [f"ennoamel://scope/{scope_key}/target"],
                "evidence_text": text,
                "source_json": {
                    **source_json,
                    "scope_key": scope_key,
                    "source_origin": "ennoamel_current_cir",
                    "not_from_project_raw_documents": True,
                },
            }
        )
        evidence_items.append(
            {
                "evidence_id": evidence_id,
                "type": "diagnostic_lock",
                "title": title,
                "text": text,
                "source_refs": [f"ennoamel://scope/{scope_key}/target"],
                "fact_eligible": True,
                "consultant_status": row.get("consultant_status") or "en_attente",
                "needs_human_validation": True,
                "source_origin": "ennoamel_current_cir",
            }
        )

    frascati = analysis.get("project_frascati") or {}
    core = analysis.get("core") or {}
    stats = core.get("audit") or {}
    domain = (report.get("project_context") or {}).get("domain_detection") or {}

    background_summary: dict[str, Any] = {}
    if isinstance(project_background, dict) and project_background.get("available"):
        background_summary = {
            "available": True,
            "diagnostic_run_id": project_background.get("diagnostic_run_id"),
            "domain_detection": project_background.get("domain_detection") or {},
            "verrous_count": len(project_background.get("verrous") or []),
            "policy": (
                "Contexte projet historique secondaire uniquement. Les verrous de ce diagnostic "
                "ne sont pas injectés dans la recherche ciblée tant qu'ils ne proviennent pas du "
                "CIR/texte actuellement analysé par EnnoAmel."
            ),
        }

    return {
        "available": bool(verrou_payload or evidence_items),
        # Un rapport sans verrou reste un diagnostic exécuté et exploitable
        # comme conclusion négative. ``available`` continue d'indiquer la
        # présence de signaux de verrou ; ``completed`` évite de relancer le
        # pipeline simplement parce que la liste est vide.
        "completed": True,
        "agent": "EnnoDiagnostic",
        "diagnostic_run_id": f"scoped:{scope_key}",
        "status": "scoped_complete" if (verrou_payload or evidence_items) else "scoped_no_lock",
        "scope": "ennoamel_current_input",
        "scope_key": scope_key,
        "scope_project": scope_project,
        "source_kind": "current_cir_or_section_supplied_to_ennoamel",
        "project_raw_documents_used": False,
        "domain_detection": domain,
        "verrous": verrou_payload,
        "diagnostic_sections": {},
        "frascati_summary": {
            "summary": frascati.get("summary") or "",
            "criteria": frascati.get("criteria") or [],
            "qualified_lock_groups_count": len(verrou_payload),
        },
        "evidence_items": evidence_items,
        "scoped_engine": "ennodiagnostic_v2",
        "scoped_analysis_stats": stats,
        "scoped_nlp_stats": stats,  # Existing consumer field; no V1 NLP execution.
        "scoped_index_report": index_report,
        "scoped_report_path": report_path,
        "cache_hit": cache_hit,
        "project_background": background_summary,
        "policy": "scoped_current_cir_first_advisory_only_no_eligibility_claim",
    }


def _run_scoped_diagnostic(
    db: Any,
    project: Any,
    request: ImprovementRequest,
    *,
    force_refresh: bool = False,
    allow_empty: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Exécute le fulltext + RAG + EnnoDiagnostic V2 sur le CIR courant d'EnnoAmel."""

    _ensure_imports()
    scope_key = _scope_hash(project, request)
    organisation = _organisation(project)
    base_project = re.sub(r"[^A-Za-z0-9_-]+", "_", _project_name(project, request)).strip("_")[:80] or "project"
    scope_project = f"{base_project}__ennoamel_scope_{scope_key}"
    year = _year(project)
    ps = _scoped_project_store(organisation, scope_project, year)

    analysis_path = ps.diagnostics_dir / "diagnostic_v2_analysis.json"
    report_path = ps.diagnostics_dir / "ennodiagnostic_report.json"
    manifest_path = ps.diagnostics_dir / "ennoamel_scope_manifest.json"

    # Le diagnostic scoped est immuable pour un hash de texte donné : réutiliser
    # le cache évite de repayer analyse V2/LLM à chaque message de la même section.
    if not force_refresh and analysis_path.exists() and report_path.exists() and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("scope_key") == scope_key and manifest.get("version") == SCOPED_DIAGNOSTIC_VERSION:
                analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
                report = json.loads(report_path.read_text(encoding="utf-8"))
                background = diagnostic_context(db, project, request.target_text)
                context = _context_from_scoped_run(
                    request=request,
                    scope_key=scope_key,
                    scope_project=scope_project,
                    analysis=analysis,
                    report=report,
                    index_report=manifest.get("index_report") or {},
                    report_path=str(report_path),
                    cache_hit=True,
                    project_background=background,
                )
                if context.get("available") or allow_empty:
                    return context, {
                        "agent": "EnnoDiagnostic",
                        "mode": (
                            "reuse_initial_cir_diagnostic"
                            if request.target_scope == TargetScope.FULL_DOCUMENT
                            else "reuse_scoped_ennoamel_input"
                        ),
                        "executed": False,
                        "cache_hit": True,
                        "scope_key": scope_key,
                        "project_raw_documents_used": False,
                        "diagnostic_run_id": context.get("diagnostic_run_id"),
                    }
        except Exception:
            pass

    try:
        from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.full_runtime import build_full_runtime, parse_domain_tags
        from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
        from modules.RAG.full_document_corpus import persist_full_documents_for_chat
        from modules.RAG.indexer import index_fulltext_corpus
    except Exception as exc:  # pragma: no cover
        raise DiagnosticOrchestrationError(
            f"Les composants EnnoDiagnostic V2/RAG ciblés sont indisponibles : {type(exc).__name__}: {exc}"
        ) from exc

    documents, modes = _virtual_documents(request, scope_key)
    for document in documents:
        document["document_id"] = hashlib.sha256(
            document["source_path"].encode("utf-8")
        ).hexdigest()[:24]
    print(
        "[EnnoAmel][ScopedDiagnostic] "
        f"engine=ennodiagnostic_v2 scope={scope_key} source=ennoamel_current_cir docs={len(documents)} "
        "project_raw_documents_used=0",
        flush=True,
    )
    try:
        persist_full_documents_for_chat(store=ps, documents=documents)
        index_report = index_fulltext_corpus(
            organisme=organisation, project=scope_project, reset=True, year=year,
        ) or {}
        inputs = [DocumentInput(
            document_id=document["document_id"],
            name=document["document"], text=document["text"],
            declared_mode=modes[document["document"]],
            metadata={key: value for key, value in document.items() if key != "text"},
        ) for document in documents]
        domain_tags = parse_domain_tags(getattr(project, "domain_label", None))
        runtime = build_full_runtime(
            domain_tags=domain_tags,
            fast_cache_dir=str(ps.project_dir / "analysis_cache" / "global_fast"),
        )
        analysis = runtime.run(inputs).to_dict()
        report = {
            "ok": True, "engine": "ennodiagnostic_v2", "status": "completed",
            "diagnostic_v2": analysis,
            "project_context": {
                "domain_tags": domain_tags,
                "domain_detection": {
                    "display_label": " | ".join(domain_tags),
                    "source": "project_domain_tags",
                    "confidence": 0.0,
                } if domain_tags else {},
            },
        }
    except Exception as exc:
        raise DiagnosticOrchestrationError(
            "EnnoDiagnostic V2 ciblé sur la section/CIR EnnoAmel a échoué : "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    _save_json(analysis_path, analysis)
    _save_json(report_path, report)
    _save_json(
        manifest_path,
        {
            "version": SCOPED_DIAGNOSTIC_VERSION,
            "scope_key": scope_key,
            "source": "ennoamel_current_cir_or_section",
            "project_raw_documents_used": False,
            "target_section_id": request.target_section_id,
            "target_section_title": request.target_section_title,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "index_report": index_report,
        },
    )

    # Le diagnostic global existant reste seulement un contexte secondaire. On
    # ne l'exécute jamais ici et on n'envoie pas ses verrous à Scholar.
    try:
        background = diagnostic_context(db, project, request.target_text)
    except Exception:
        background = {}

    context = _context_from_scoped_run(
        request=request,
        scope_key=scope_key,
        scope_project=scope_project,
        analysis=analysis,
        report=report,
        index_report=index_report if isinstance(index_report, dict) else {},
        report_path=str(report_path),
        cache_hit=False,
        project_background=background,
    )
    if not context.get("available") and not allow_empty:
        raise DiagnosticOrchestrationError(
            "EnnoDiagnostic a bien analysé le texte/CIR courant, mais aucun signal de verrou "
            "exploitable n'a été identifié pour cette cible. EnnoScholar ne sera pas lancé "
            "sur une requête inventée."
        )

    return context, {
        "agent": "EnnoDiagnostic",
        "mode": (
            "fresh_initial_cir_diagnostic"
            if request.target_scope == TargetScope.FULL_DOCUMENT
            else "fresh_scoped_ennoamel_input"
        ),
        "executed": True,
        "cache_hit": False,
        "scope_key": scope_key,
        "scope_project": scope_project,
        "source_kind": "current_cir_or_section_supplied_to_ennoamel",
        "project_raw_documents_used": False,
        "diagnostic_run_id": context.get("diagnostic_run_id"),
    }


def ensure_diagnostic_context(
    db: Any,
    project: Any,
    request: ImprovementRequest,
    *,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retourne le contexte Diagnostic de LA CIBLE EnnoAmel.

    Contrairement à V2.2, cette fonction n'appelle jamais
    ``run_ennodiagnostic(db, project)``. Cette fonction backend analyse les
    documents bruts attachés au projet, qui peuvent ne contenir ni le CIR chargé
    dans EnnoAmel ni la section choisie par le consultant.

    Le bon corpus est le CIR/texte actuellement fourni à EnnoAmel.
    """

    return _run_scoped_diagnostic(
        db,
        project,
        request,
        force_refresh=force_refresh,
    )


def ensure_initial_diagnostic_context(
    db: Any,
    project: Any,
    request: ImprovementRequest,
    *,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Diagnostic structuré unique du CIR complet, résultat vide inclus.

    Le hash porte sur la version active complète. Un rapport concluant à
    l'absence de verrou est donc lui aussi mis en cache et ne déclenche jamais
    une seconde exécution par section.
    """

    if request.target_scope != TargetScope.FULL_DOCUMENT:
        request = request.model_copy(
            update={
                "target_scope": TargetScope.FULL_DOCUMENT,
                "target_text": request.full_text,
                "target_section_id": None,
                "target_section_title": None,
            }
        )
    return _run_scoped_diagnostic(
        db,
        project,
        request,
        force_refresh=force_refresh,
        allow_empty=True,
    )
