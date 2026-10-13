# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from core.deps import require_superadmin
from db.models import User
from services.experience_memory_v2_service import build_uploaded_cir
from services.sharepoint_audit_service import (
    get_sharepoint_audit,
    get_sharepoint_audit_item,
    import_configuration_status,
    list_import_folders,
    list_sharepoint_audits,
    mark_audit_item_indexed,
    memory_identity_conflict,
    require_index_confirmation,
    require_manifest_confirmation,
    run_sharepoint_audit,
    validate_staged_path,
)


router = APIRouter(prefix="/cir-memory/import-inbox", tags=["cir-memory-import-inbox"])


@router.get("/configuration")
def import_inbox_configuration(_: User = Depends(require_superadmin)):
    return import_configuration_status()


@router.get("/folders")
def import_inbox_folders(
    parent: str = Query(""),
    provider: str = Query("inbox"),
    _: User = Depends(require_superadmin),
):
    try:
        return list_import_folders(parent=parent, provider_name=provider)
    except (ValueError, FileNotFoundError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/scans")
def import_inbox_scans(
    limit: int = Query(20, ge=1, le=100),
    _: User = Depends(require_superadmin),
):
    return {"ok": True, "scans": list_sharepoint_audits(limit=limit)}


@router.get("/scans/{scan_id}")
def import_inbox_scan(scan_id: str, _: User = Depends(require_superadmin)):
    try:
        return get_sharepoint_audit(scan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/scans")
def start_import_inbox_scan(
    payload: dict[str, Any] = Body(default={}),
    current_user: User = Depends(require_superadmin),
):
    if payload.get("confirm_read_only_audit") is not True:
        raise HTTPException(status_code=400, detail="Confirmez explicitement l'audit en lecture seule.")

    provider = str(payload.get("provider") or "fake").strip().lower()
    if provider in {"inbox", "power_automate", "onedrive", "real"} and payload.get("acknowledge_professional_copy_folder_read") is not True:
        raise HTTPException(
            status_code=400,
            detail="Confirmez la lecture du dossier de copies professionnel.",
        )
    if provider in {"inbox", "power_automate", "onedrive", "real"} and not str(payload.get("relative_folder") or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Choisissez d'abord un client ou un sous-dossier. Le scan global de tous les clients est désactivé.",
        )
    try:
        max_files = payload.get("max_files")
        if max_files is not None:
            max_files = max(1, min(int(max_files), 20_000))
        result = run_sharepoint_audit(
            provider_name=provider,
            initiated_by=f"{current_user.id}:{current_user.email}",
            deep_scan=bool(payload.get("deep_scan", False)),
            max_files=max_files,
            relative_folder=payload.get("relative_folder") or "",
        )
    except (ValueError, FileNotFoundError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail={"message": "Audit du dossier d'import incomplet.", "run": result})
    return result


@router.post("/scans/{scan_id}/items/{item_id}/index")
def index_import_inbox_item(
    scan_id: str,
    item_id: str,
    payload: dict[str, Any] = Body(default={}),
    _: User = Depends(require_superadmin),
):
    """Action locale explicite : extraction puis indexation dans Memory V2.

    Cette route n'appelle aucun service Microsoft et n'écrit jamais dans le dossier source.
    Le CIR source n'est pas conservé comme copie permanente dans le dépôt.
    """
    try:
        require_index_confirmation(payload.get("confirm_local_memory_changes"))
        run = get_sharepoint_audit(scan_id)
        require_manifest_confirmation(run, payload.get("confirm_manifest_sha256"))
        if run.get("provider") == "fake":
            raise ValueError("Les documents du faux dossier Power Automate ne peuvent pas être indexés dans la mémoire de production.")
        item = get_sharepoint_audit_item(scan_id, item_id)
        if item.get("classification") not in {"cir_final_confirmed", "cir_probable"}:
            raise ValueError("Seuls les CIR confirmés ou probables peuvent être indexés.")
        if item.get("recommended_version") is not True or item.get("index_eligible") is not True:
            raise ValueError(
                "Ce document n'est pas la version finale recommandée ou nécessite une revue avant indexation."
            )
        if item.get("indexed"):
            raise ValueError("Ce document est déjà indexé.")
        staged_path = validate_staged_path(item)
        identity = item.get("detected_identity") or {}
        organisme = str(payload.get("organisme") or identity.get("organisme") or "").strip()
        project = str(payload.get("project") or identity.get("project") or "").strip()
        subproject = str(payload.get("subproject") or identity.get("subproject") or "").strip()
        year = str(payload.get("year") or identity.get("year") or "").strip()
        if not organisme or not project or not year:
            raise ValueError("Entreprise, projet et année doivent être confirmés avant l'indexation.")
        conflict = memory_identity_conflict(
            digest=str(item.get("sha256") or ""),
            organisme=organisme,
            project=project,
            subproject=subproject,
            year=year,
        )
        if conflict == "same_hash":
            raise ValueError("Ce fichier exact existe déjà dans Memory V2.")
        if conflict == "same_identity_other_version":
            raise ValueError(
                "Une autre version existe déjà pour cet organisme, ce projet et cette année. "
                "Archivez d'abord cette ancienne version depuis l'onglet Bibliothèque."
            )
        result = build_uploaded_cir(
            staged_path,
            organisme=organisme,
            project=project,
            subproject=subproject,
            year=year,
            vision_mode="text_only",
            formula_mode="off",
        )
        chunks_count = int(result.get("chunks_count") or 0)
        cards_count = int(result.get("cards_count") or 0)
        if not result.get("ok") or chunks_count <= 0 or cards_count <= 0:
            raise RuntimeError(
                "Extraction terminée sans passages/cartes exploitables ; le document n'est pas marqué comme indexé."
            )
        updated = mark_audit_item_indexed(
            scan_id,
            item_id,
            result=result,
            identity={
                "organisme": organisme,
                "project": project,
                "subproject": subproject,
                "year": year,
            },
        )
        return {
            "ok": True,
            "source_created": False,
            "source_updated": False,
            "source_moved": False,
            "source_deleted": False,
            "item": updated,
            "memory_result": result,
        }
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Indexation locale impossible : {exc}") from exc
