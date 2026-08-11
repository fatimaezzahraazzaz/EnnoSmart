from __future__ import annotations

"""Orchestration ciblée d'EnnoDiagnostic depuis EnnoAmel.

V2.3 — règle d'architecture :

* EnnoAmel n'analyse JAMAIS automatiquement les documents bruts du projet
  lorsqu'un consultant travaille sur un texte/CIR chargé dans EnnoAmel.
* Le corpus Diagnostic de ce tour vient exclusivement du texte actuellement
  fourni à EnnoAmel : section cible + contexte CIR local utile.
* Ce corpus est envoyé au vrai pipeline NLP/Frascati, indexé dans un espace RAG
  isolé, puis reformulé par le vrai EnnoDiagnosticAgent.
* Un diagnostic historique du projet peut être conservé comme contexte secondaire
  consultatif, mais il ne remplace jamais le diagnostic ciblé et ses verrous ne
  sont pas injectés dans la recherche EnnoScholar de ce tour.

Ainsi : EnnoAmel orchestre réellement NLP -> EnnoDiagnostic -> EnnoScholar sans
réimplémenter les modèles et sans confondre le CIR à améliorer avec les documents
bruts déjà attachés au projet.
"""

import hashlib
import importlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain.models import ImprovementRequest, TargetScope
from .agent_adapters import diagnostic_context


SCOPED_DIAGNOSTIC_VERSION = "v2_3_ennoamel_scoped_cir_input"


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

    # Retirer la section cible évite de la compter deux fois dans le NLP.
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
    root = Path(os.getenv("ENNOSMART_BASE_DIR", os.getenv("ENNOSMART_ROOT", r"C:\EnnoSmart")))
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


def _candidate_groups_from_nlp(nlp_result: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    frascati = nlp_result.get("frascati_guard") or {}
    if isinstance(frascati, dict):
        for key in ("verrous_probables", "verrous_a_verifier", "technical_lock_groups"):
            rows = frascati.get(key) or []
            if isinstance(rows, list):
                groups.extend(dict(row) for row in rows if isinstance(row, dict))

    pack = nlp_result.get("multi_document_evidence_pack_for_ennodiagnostic") or {}
    if isinstance(pack, dict):
        rows = pack.get("verrous_rnd_locaux") or []
        if isinstance(rows, list):
            groups.extend(dict(row) for row in rows if isinstance(row, dict))

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in groups:
        key = _clean(row.get("lock_group_id") or row.get("passage_id") or row.get("candidate_group_label"), 500)
        if not key:
            key = hashlib.sha1(repr(row).encode("utf-8", errors="ignore")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _fallback_extract_locks(nlp_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Fallback NLP uniquement si le LLM EnnoDiagnostic ne retourne pas de liste structurée."""

    locks: list[dict[str, Any]] = []
    for row in _candidate_groups_from_nlp(nlp_result):
        if row.get("display_as_main_lock") is False or row.get("display_as_lock") is False:
            continue
        decision = _clean(row.get("frascati_decision"), 80)
        if decision and decision not in {"verrou_probable", "verrou_a_verifier"}:
            continue
        title = _clean(
            row.get("candidate_group_label")
            or row.get("title")
            or row.get("section_title")
            or row.get("text"),
            500,
        )
        if not title:
            continue
        supports = [item for item in (row.get("supporting_passages") or []) if isinstance(item, dict)]
        representative = _clean(
            row.get("text") or row.get("source_text") or row.get("excerpt"),
            4000,
        )
        evidence_text = "\n".join(
            part
            for part in [
                representative,
                *[
                    _clean(item.get("text") or item.get("excerpt"), 1200)
                    for item in supports[:6]
                ],
            ]
            if part
        )
        locks.append(
            {
                "title": title,
                "justification": evidence_text,
                "text": evidence_text,
                "score": row.get("frascati_score"),
                "consultant_status": "en_attente",
                "needs_human_validation": True,
                "source_json": {
                    "scientific_lock": title,
                    "evidence_summary": evidence_text,
                    "lock_group_id": row.get("lock_group_id") or row.get("passage_id"),
                    "supporting_passages": supports,
                    "frascati_decision": row.get("frascati_decision"),
                    "source": "scoped_nlp_fallback",
                },
            }
        )
    return locks


def _extract_final_locks(report: dict[str, Any], nlp_result: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        service = _diagnostic_service()
        extractor = getattr(service, "_extract_final_verrous_from_report", None)
        if callable(extractor):
            locks = extractor(report)
            if isinstance(locks, list) and locks:
                return [dict(row) for row in locks if isinstance(row, dict)]
    except Exception:
        pass

    synthesis = report.get("verrou_synthesis_report") if isinstance(report, dict) else {}
    if not isinstance(synthesis, dict):
        synthesis = {}
    candidates = (
        synthesis.get("llm_reformulated_verrous")
        or synthesis.get("final_items")
        or synthesis.get("accepted_items")
        or synthesis.get("final_verrous")
        or report.get("llm_reformulated_verrous")
        or report.get("consultant_verrous_cir")
        or report.get("verrous_reformules")
        or []
    ) if isinstance(report, dict) else []
    if isinstance(candidates, list) and candidates:
        return [dict(row) for row in candidates if isinstance(row, dict)]
    return _fallback_extract_locks(nlp_result)


def _context_from_scoped_run(
    *,
    request: ImprovementRequest,
    scope_key: str,
    scope_project: str,
    nlp_result: dict[str, Any],
    report: dict[str, Any],
    index_report: dict[str, Any],
    report_path: str,
    cache_hit: bool,
    project_background: dict[str, Any] | None,
) -> dict[str, Any]:
    locks = _extract_final_locks(report, nlp_result)
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

    frascati = nlp_result.get("frascati_guard") if isinstance(nlp_result, dict) else {}
    if not isinstance(frascati, dict):
        frascati = {}

    domain = nlp_result.get("domain_detection") if isinstance(nlp_result, dict) else {}
    if not isinstance(domain, dict):
        domain = {}

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
            "risk_report": frascati.get("risk_report") or {},
            "qualified_lock_groups_count": nlp_result.get("stats", {}).get("qualified_lock_groups")
            if isinstance(nlp_result.get("stats"), dict)
            else None,
        },
        "evidence_items": evidence_items,
        "scoped_nlp_stats": nlp_result.get("stats") or {},
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Exécute le vrai NLP + RAG + EnnoDiagnostic sur le CIR courant d'EnnoAmel."""

    _ensure_imports()
    scope_key = _scope_hash(project, request)
    organisation = _organisation(project)
    base_project = re.sub(r"[^A-Za-z0-9_-]+", "_", _project_name(project, request)).strip("_")[:80] or "project"
    scope_project = f"{base_project}__ennoamel_scope_{scope_key}"
    year = _year(project)
    ps = _scoped_project_store(organisation, scope_project, year)

    nlp_path = ps.nlp_dir / "nlp_result.json"
    report_path = ps.diagnostics_dir / "ennodiagnostic_report.json"
    manifest_path = ps.diagnostics_dir / "ennoamel_scope_manifest.json"

    # Le diagnostic scoped est immuable pour un hash de texte donné : réutiliser
    # le cache évite de repayer NLP/LLM à chaque message de la même section.
    if not force_refresh and nlp_path.exists() and report_path.exists() and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("scope_key") == scope_key and manifest.get("version") == SCOPED_DIAGNOSTIC_VERSION:
                nlp_result = json.loads(nlp_path.read_text(encoding="utf-8"))
                report = json.loads(report_path.read_text(encoding="utf-8"))
                background = diagnostic_context(db, project, request.target_text)
                context = _context_from_scoped_run(
                    request=request,
                    scope_key=scope_key,
                    scope_project=scope_project,
                    nlp_result=nlp_result,
                    report=report,
                    index_report=manifest.get("index_report") or {},
                    report_path=str(report_path),
                    cache_hit=True,
                    project_background=background,
                )
                if context.get("available"):
                    return context, {
                        "agent": "EnnoDiagnostic",
                        "mode": "reuse_scoped_ennoamel_input",
                        "executed": False,
                        "scope_key": scope_key,
                        "project_raw_documents_used": False,
                        "diagnostic_run_id": context.get("diagnostic_run_id"),
                    }
        except Exception:
            pass

    try:
        from modules.NLP.pipeline_route import run_nlp_pipeline_routed
        from modules.RAG.indexer import index_nlp_result
        from agents.EnnoDiagnostic.ennodiagnostic_agent import EnnoDiagnosticAgent
    except Exception as exc:  # pragma: no cover
        raise DiagnosticOrchestrationError(
            "Les composants NLP/RAG/EnnoDiagnostic ciblés sont indisponibles : "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    documents, modes = _virtual_documents(request, scope_key)
    print(
        "[EnnoAmel][ScopedDiagnostic] "
        f"scope={scope_key} source=ennoamel_current_cir docs={len(documents)} "
        "project_raw_documents_used=0",
        flush=True,
    )
    try:
        nlp_result = run_nlp_pipeline_routed(
            documents=documents,
            document_modes=modes,
            max_candidates=int(os.getenv("ENNOAMEL_SCOPED_NLP_MAX_CANDIDATES", "260")),
            include_state_of_art_in_candidates=True,
            organisme=organisation,
            project=scope_project,
            year=year,
        )
    except Exception as exc:
        raise DiagnosticOrchestrationError(
            "Le NLP/Frascati ciblé sur la section/CIR EnnoAmel a échoué : "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(nlp_result, dict):
        raise DiagnosticOrchestrationError("Le NLP ciblé n'a pas retourné de résultat exploitable.")
    _save_json(nlp_path, nlp_result)

    try:
        index_report = index_nlp_result(
            organisme=organisation,
            project=scope_project,
            nlp_result=nlp_result,
            reset=True,
            year=year,
        ) or {}
    except Exception as exc:
        raise DiagnosticOrchestrationError(
            "L'indexation RAG ciblée du texte EnnoAmel a échoué : "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    try:
        agent = EnnoDiagnosticAgent(
            organisme=organisation,
            project=scope_project,
            year=year,
            use_llm=True,
            # La mémoire de style d'un autre projet n'est pas une preuve du CIR
            # courant. Pour ce diagnostic de routage, on reste local.
            use_style_memory=False,
        )
        report = agent.generate_diagnostic(save=True)
    except Exception as exc:
        raise DiagnosticOrchestrationError(
            "Le vrai EnnoDiagnostic ciblé sur la section/CIR EnnoAmel a échoué : "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(report, dict):
        raise DiagnosticOrchestrationError("EnnoDiagnostic ciblé n'a pas retourné de rapport exploitable.")
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
        nlp_result=nlp_result,
        report=report,
        index_report=index_report if isinstance(index_report, dict) else {},
        report_path=str(report_path),
        cache_hit=False,
        project_background=background,
    )
    if not context.get("available"):
        raise DiagnosticOrchestrationError(
            "EnnoDiagnostic a bien analysé le texte/CIR courant, mais aucun signal de verrou "
            "exploitable n'a été identifié pour cette cible. EnnoScholar ne sera pas lancé "
            "sur une requête inventée."
        )

    return context, {
        "agent": "EnnoDiagnostic",
        "mode": "fresh_scoped_ennoamel_input",
        "executed": True,
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
