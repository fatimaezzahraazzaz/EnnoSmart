from __future__ import annotations

"""Automatisation locale et non destructive de l'alimentation Memory V2.

Deux flux sont disponibles :

* ``pilot`` : uniquement CEVAA / CORPLAUX / 2024 ;
* ``all`` : tous les dossiers de versions finales détectés sous la racine client.

Sans ``--apply-latest``, le script réalise seulement les scans, classe les
versions et produit un manifeste signé. Aucune indexation n'est implicite.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
BACKEND_DIR = ROOT_DIR / "backend_api"
for entry in (str(ROOT_DIR), str(SCRIPT_DIR), str(BACKEND_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env", override=False)
    load_dotenv(BACKEND_DIR / ".env", override=False)
except Exception:
    pass

from core.config import settings
from services.experience_memory_v2_service import build_uploaded_cir
from services.sharepoint_audit_service import (
    LocalReadOnlyImportProvider,
    _embedded_date_rank,
    _version_numbers,
    apply_final_version_policy,
    get_sharepoint_audit,
    get_sharepoint_audit_item,
    infer_audit_identity,
    mark_audit_item_indexed,
    memory_identity_conflict,
    require_manifest_confirmation,
    run_sharepoint_audit,
    validate_staged_path,
)
from migrate_memory_v2_identity import _rebuild_global_chroma_recoverably


PILOT_SCOPE = "6NAPSE GROUP/1. CEVAA/CIR 2024/Dossier technique/Versions finales"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc"}
FINAL_FOLDER_MARKERS = (
    "version finale",
    "versions finales",
    "dossier final",
    "dossiers finaux",
    "dossier technique final",
    "livraison dts",
)
TECHNICAL_FOLDER_MARKERS = (
    "dossier technique",
    "dossier justificatif",
)
EXCLUDED_FOLDER_MARKERS = (
    "brouillon",
    "draft",
    "backup",
    "sauvegarde",
    "archive",
    "ancien",
    "old",
    "integration",
    "intégration",
    "test",
    "valorisation financiere",
    "dossier financier",
    "facture",
    "cerfa",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("\\", "/")
    return re.sub(r"[^a-z0-9/]+", " ", text).strip()


def identity_key(identity: dict[str, Any]) -> str:
    return "::".join(
        re.sub(r"[^a-z0-9]+", "", normalise(identity.get(key))) or "unknown"
        for key in ("organisme", "project", "subproject")
    ) + f"::{str(identity.get('year') or '').strip()}"


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


SOURCE_ROOT = Path(settings.POWER_AUTOMATE_IMPORT_ROOT).resolve()
AUDIT_ROOT = Path(settings.POWER_AUTOMATE_AUDIT_ROOT).resolve()
AUTOMATION_ROOT = AUDIT_ROOT / "automation"


def assert_safe_configuration() -> None:
    if not SOURCE_ROOT.is_dir():
        raise FileNotFoundError(
            "POWER_AUTOMATE_IMPORT_ROOT est absent ou introuvable dans .env."
        )
    if is_inside(AUDIT_ROOT, SOURCE_ROOT) or is_inside(AUTOMATION_ROOT, SOURCE_ROOT):
        raise PermissionError("Les journaux d'automatisation ne peuvent pas être stockés dans OneDrive.")
    memory_root = Path(settings.ENNOSMART_EXPERIENCE_MEMORY_V2_DIR).resolve()
    if is_inside(memory_root, SOURCE_ROOT):
        raise PermissionError("Memory V2 ne peut pas être stockée dans OneDrive.")


def atomic_write_json(path: Path, payload: Any) -> None:
    if is_inside(path, SOURCE_ROOT):
        raise PermissionError(f"Écriture interdite dans la source OneDrive : {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".tmp",
        prefix=f".{path.name}.",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def manifest_fingerprint(payload: dict[str, Any]) -> str:
    canonical = {
        "mode": payload.get("mode"),
        "source_root": payload.get("source_root"),
        "source_integrity_verified": payload.get("source_integrity_verified"),
        "items": [
            {
                "scan_id": item.get("scan_id"),
                "item_id": item.get("item_id"),
                "scan_manifest_sha256": item.get("scan_manifest_sha256"),
                "sha256": item.get("sha256"),
                "classification": item.get("classification"),
                "identity": item.get("identity"),
                "selection_status": item.get("selection_status"),
                "index_eligible": bool(item.get("index_eligible")),
            }
            for item in payload.get("items") or []
        ],
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def latest_manifest_path(mode: str) -> Path:
    return AUTOMATION_ROOT / "manifests" / f"{mode}_latest.json"


def ledger_path(mode: str) -> Path:
    return AUTOMATION_ROOT / "ledgers" / f"{mode}_ledger.json"


def candidate_file_name(name: str) -> bool:
    stem = normalise(Path(name).stem)
    has_cir = bool(re.search(r"(^| )cir( |$)", stem))
    has_final = bool(re.search(r"(^| )(vf[0-9]*|final|finale|definitif|valide)( |$)", stem))
    technical_final = ("dossier technique" in stem or "dossier justificatif" in stem) and has_final
    return (has_cir and has_final) or technical_final


def _candidate_rank(candidate: dict[str, Any]) -> tuple[Any, ...]:
    suffix = Path(str(candidate.get("name") or "")).suffix.lower()
    return (
        1 if candidate.get("folder_kind") == "final" else 0,
        1 if candidate_file_name(str(candidate.get("name") or "")) else 0,
        tuple(_version_numbers(str(candidate.get("name") or ""))),
        int(_embedded_date_rank(str(candidate.get("name") or ""))),
        2 if suffix == ".pdf" else 1 if suffix == ".docx" else 0,
        int(candidate.get("mtime_ns") or 0),
        int(candidate.get("size") or 0),
    )


def discover_candidate_scopes(max_scopes: int | None = None) -> list[dict[str, Any]]:
    """Présélectionne une version finale par projet sans ouvrir les fichiers.

    Ordre de préférence : dossier de versions finales, puis dossier technique
    ou justificatif. Les autres versions sont conservées comme replis et ne sont
    ouvertes que si la meilleure version est illisible ou non indexable.
    """
    candidates: list[dict[str, Any]] = []
    for current, directory_names, file_names in os.walk(SOURCE_ROOT, topdown=True, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(SOURCE_ROOT)
        relative_normalized = normalise(relative.as_posix())
        if any(marker in relative_normalized for marker in EXCLUDED_FOLDER_MARKERS):
            directory_names[:] = []
            continue

        directory_names[:] = [
            name for name in directory_names
            if not any(marker in normalise(name) for marker in EXCLUDED_FOLDER_MARKERS)
        ]
        supported_names = [
            name for name in file_names
            if Path(name).suffix.lower() in SUPPORTED_EXTENSIONS and not name.startswith("~$")
        ]
        if not supported_names:
            continue
        final_folder = any(marker in relative_normalized for marker in FINAL_FOLDER_MARKERS)
        technical_folder = any(
            marker in relative_normalized for marker in TECHNICAL_FOLDER_MARKERS
        )
        if final_folder:
            selected_names = supported_names
            folder_kind = "final"
        elif technical_folder:
            selected_names = [name for name in supported_names if candidate_file_name(name)]
            folder_kind = "technical"
        else:
            selected_names = []

        for name in selected_names:
            path = current_path / name
            try:
                stat = path.stat()
                size = int(stat.st_size)
                mtime_ns = int(stat.st_mtime_ns)
            except OSError:
                size = 0
                mtime_ns = 0
            identity = infer_audit_identity(
                source_scope=relative.as_posix(),
                source_path=name,
                file_name=name,
            )
            group = identity_key(identity)
            if not re.fullmatch(r"(?:19|20)\d{2}", str(identity.get("year") or "")):
                # Ne jamais fusionner deux dossiers dont l'année n'est pas sûre.
                group = f"{group}::{normalise(relative.as_posix())}::{normalise(name)}"
            candidates.append({
                "scope": relative.as_posix(),
                "name": name,
                "identity": identity,
                "identity_group": group,
                "folder_kind": folder_kind,
                "size": size,
                "mtime_ns": mtime_ns,
            })

    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        grouped.setdefault(str(candidate["identity_group"]), []).append(candidate)

    scopes: list[dict[str, Any]] = []
    for group in sorted(grouped, key=normalise):
        group_candidates = grouped[group]
        final_candidates = sorted(
            (item for item in group_candidates if item["folder_kind"] == "final"),
            key=_candidate_rank,
            reverse=True,
        )
        technical_candidates = sorted(
            (item for item in group_candidates if item["folder_kind"] == "technical"),
            key=_candidate_rank,
            reverse=True,
        )
        ordered = final_candidates + technical_candidates
        if not ordered:
            continue
        primary = ordered[0]
        scopes.append({
            "scope": primary["scope"],
            "files": [primary["name"]],
            "recursive": False,
            "identity_hint": primary["identity"],
            "candidate_versions_count": len(ordered),
            "alternatives": [
                {"scope": item["scope"], "files": [item["name"]], "recursive": False}
                for item in ordered[1:]
            ],
        })
        if max_scopes is not None and len(scopes) >= max(0, max_scopes):
            break
    return scopes


def _audit_scopes(
    scopes: Iterable[str | dict[str, Any]],
    *,
    mode: str,
    include_probable: bool,
    pilot_subproject: str = "",
    partial_scan: bool = False,
) -> dict[str, Any]:
    all_items: list[dict[str, Any]] = []
    scans: list[dict[str, Any]] = []
    total_source_writes = 0
    integrity = True
    scopes = list(scopes)
    for index, scope_entry in enumerate(scopes, start=1):
        if isinstance(scope_entry, dict):
            variants = [
                {"scope": scope_entry.get("scope"), "files": scope_entry.get("files") or []},
                *(scope_entry.get("alternatives") or []),
            ]
            candidate_versions_count = int(
                scope_entry.get("candidate_versions_count") or len(variants)
            )
            runs = []
            for attempt, variant in enumerate(variants, start=1):
                scope = str(variant.get("scope") or "").strip("/\\")
                allowed_files = [
                    str(value).strip("/\\")
                    for value in (variant.get("files") or [])
                    if str(value or "").strip("/\\")
                ]
                scope_root = (SOURCE_ROOT / Path(scope)).resolve()
                if not is_inside(scope_root, SOURCE_ROOT):
                    raise PermissionError(f"Périmètre hors OneDrive autorisé : {scope}")
                provider = LocalReadOnlyImportProvider(
                    scope_root,
                    provider_name="power_automate_inbox",
                    source_scope=scope,
                    source_library_root=SOURCE_ROOT,
                    recursive=False,
                    allowed_relative_paths=allowed_files,
                )
                suffix = (
                    f" — repli {attempt}/{len(variants)}"
                    if attempt > 1 else ""
                )
                print(
                    f"[{index}/{len(scopes)}] Scan final prioritaire : "
                    f"{scope} / {allowed_files[0] if allowed_files else '?'}{suffix}",
                    flush=True,
                )
                run = run_sharepoint_audit(
                    provider=provider,
                    audit_root=AUDIT_ROOT,
                    initiated_by=f"local-automation-{mode}",
                    deep_scan=False,
                )
                runs.append(run)
                usable = any(
                    item.get("classification") == "cir_final_confirmed"
                    and item.get("indexable") is True
                    for item in (run.get("items") or [])
                )
                if not usable and include_probable:
                    usable = any(
                        item.get("classification") == "cir_probable"
                        and item.get("indexable") is True
                        for item in (run.get("items") or [])
                    )
                if usable:
                    break
        else:
            scope = str(scope_entry).strip("/\\")
            print(f"[{index}/{len(scopes)}] Scan lecture seule : {scope}", flush=True)
            runs = [run_sharepoint_audit(
                provider_name="inbox",
                initiated_by=f"local-automation-{mode}",
                deep_scan=False,
                relative_folder=scope,
            )]
            candidate_versions_count = 1
        for run in runs:
            total_source_writes += int(run.get("source_write_operations") or 0)
            integrity = integrity and run.get("source_integrity_verified") is True
            scans.append({
                "scan_id": run.get("scan_id"),
                "source_scope": run.get("source_scope"),
                "manifest_sha256": run.get("manifest_sha256"),
                "counts": run.get("counts") or {},
                "source_write_operations": run.get("source_write_operations"),
            })
            for item in run.get("items") or []:
                copied = dict(item)
                copied["_scan_id"] = run.get("scan_id")
                copied["_scan_manifest_sha256"] = run.get("manifest_sha256")
                copied["_source_scope"] = run.get("source_scope")
                copied["_metadata_alternative_versions_count"] = max(
                    0, candidate_versions_count - 1
                )
                all_items.append(copied)

    # Refait la sélection sur l'ensemble des scans : une seule version finale
    # par organisme/projet/sous-projet/année, même si elle existe dans deux dossiers.
    selection_counts = apply_final_version_policy(all_items)
    selected: list[dict[str, Any]] = []
    for item in all_items:
        identity = dict(item.get("detected_identity") or {})
        if item.get("recommended_version") is not True:
            continue
        if item.get("classification") == "cir_probable" and not include_probable:
            continue
        if item.get("classification") not in {"cir_final_confirmed", "cir_probable"}:
            continue
        if pilot_subproject and normalise(identity.get("subproject")) != normalise(pilot_subproject):
            continue
        selected.append({
            "scan_id": item.get("_scan_id"),
            "item_id": item.get("external_id"),
            "scan_manifest_sha256": item.get("_scan_manifest_sha256"),
            "source_scope": item.get("_source_scope"),
            "name": item.get("name"),
            "sha256": item.get("sha256"),
            "classification": item.get("classification"),
            "confidence": item.get("confidence"),
            "identity": {
                "organisme": str(identity.get("organisme") or "").strip(),
                "project": str(identity.get("project") or "").strip(),
                "subproject": str(identity.get("subproject") or "").strip(),
                "year": str(identity.get("year") or "").strip(),
            },
            "selection_status": item.get("selection_status"),
            "index_eligible": bool(item.get("index_eligible")),
            "alternative_versions_count": max(
                int(item.get("alternative_versions_count") or 0),
                int(item.get("_metadata_alternative_versions_count") or 0),
            ),
        })

    selected.sort(key=lambda item: identity_key(item["identity"]))
    manifest = {
        "ok": True,
        "version": "cir_memory_local_automation_v1",
        "mode": mode,
        "created_at": now_iso(),
        "source_root": str(SOURCE_ROOT),
        "audit_root": str(AUDIT_ROOT),
        "memory_root": str(settings.ENNOSMART_EXPERIENCE_MEMORY_V2_DIR),
        "source_policy": "strict_read_only",
        "source_write_operations": total_source_writes,
        "source_integrity_verified": integrity,
        "include_probable": include_probable,
        "partial_scan": partial_scan,
        "scans": scans,
        "selection_counts": selection_counts,
        "counts": {
            "scopes": len(scopes),
            "files_audited": len(all_items),
            "selected_final_versions": len(selected),
            "ready_to_index": sum(1 for item in selected if item["index_eligible"]),
            "already_in_memory": sum(
                1 for item in selected if item["selection_status"] == "already_in_memory"
            ),
            "blocked_conflicts": sum(
                1 for item in selected if item["selection_status"] == "memory_version_conflict"
            ),
        },
        "items": selected,
    }
    manifest["manifest_sha256"] = manifest_fingerprint(manifest)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    history_path = AUTOMATION_ROOT / "manifests" / f"{mode}_{timestamp}.json"
    atomic_write_json(history_path, manifest)
    atomic_write_json(latest_manifest_path(mode), manifest)
    manifest["manifest_path"] = str(latest_manifest_path(mode))
    return manifest


def scan_pilot(include_probable: bool = False) -> dict[str, Any]:
    manifest = _audit_scopes(
        [PILOT_SCOPE],
        mode="pilot_corplaux",
        include_probable=include_probable,
        pilot_subproject="CORPLAUX",
    )
    if len(manifest.get("items") or []) != 1:
        raise RuntimeError(
            "Le pilote doit retenir exactement un CIR CORPLAUX. Vérifiez le manifeste avant toute indexation."
        )
    return manifest


def scan_all(*, include_probable: bool = False, max_scopes: int | None = None) -> dict[str, Any]:
    scopes = discover_candidate_scopes(max_scopes=max_scopes)
    if not scopes:
        raise FileNotFoundError("Aucun dossier de version finale n'a été détecté.")
    return _audit_scopes(
        scopes,
        mode="all_clients",
        include_probable=include_probable,
        partial_scan=max_scopes is not None,
    )


def _load_valid_manifest(mode: str) -> dict[str, Any]:
    path = latest_manifest_path(mode)
    manifest = read_json(path, None)
    if not isinstance(manifest, dict):
        raise FileNotFoundError(f"Manifeste introuvable : {path}. Lancez d'abord le scan.")
    expected = str(manifest.get("manifest_sha256") or "")
    actual = manifest_fingerprint(manifest)
    if not expected or expected != actual:
        raise PermissionError("Le manifeste d'automatisation a changé. Relancez le scan.")
    if manifest.get("source_integrity_verified") is not True:
        raise PermissionError("L'intégrité en lecture seule n'a pas été validée.")
    if int(manifest.get("source_write_operations") or 0) != 0:
        raise PermissionError("Une écriture source a été détectée ; indexation annulée.")
    if manifest.get("mode") == "all_clients" and manifest.get("partial_scan") is True:
        raise PermissionError(
            "Ce manifeste provient d'un test --max-scopes et ne peut pas être appliqué. "
            "Relancez le scan global sans limite."
        )
    return manifest


def _memory_run_by_hash(digest: str) -> dict[str, Any] | None:
    runs_dir = Path(settings.ENNOSMART_EXPERIENCE_MEMORY_V2_DIR) / "runs"
    for path in runs_dir.glob("*.run_v2.json") if runs_dir.is_dir() else []:
        run = read_json(path, {})
        if (
            isinstance(run, dict)
            and run.get("ok")
            and str(run.get("source_hash") or "").lower() == str(digest or "").lower()
        ):
            return run
    return None


def apply_latest(mode: str, *, confirmation: str) -> dict[str, Any]:
    expected_confirmation = (
        "INDEXER_CORPLAUX" if mode == "pilot_corplaux" else "INDEXER_TOUT_LE_CORPUS"
    )
    if str(confirmation or "").strip() != expected_confirmation:
        raise PermissionError(f"Confirmation obligatoire : {expected_confirmation}")
    manifest = _load_valid_manifest(mode)
    ledger_file = ledger_path(mode)
    ledger = read_json(ledger_file, {"version": "cir_memory_automation_ledger_v1", "items": {}})
    if not isinstance(ledger, dict):
        ledger = {"version": "cir_memory_automation_ledger_v1", "items": {}}
    previous_manifest = str(ledger.get("manifest_sha256") or "")
    if previous_manifest and previous_manifest != manifest["manifest_sha256"]:
        history = AUTOMATION_ROOT / "ledgers" / "history" / (
            f"{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        atomic_write_json(history, ledger)
        ledger = {"version": "cir_memory_automation_ledger_v1", "items": {}}
    ledger.setdefault("items", {})
    ledger["manifest_sha256"] = manifest["manifest_sha256"]

    built_results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    errors: list[dict[str, Any]] = []
    for index, entry in enumerate(manifest.get("items") or [], start=1):
        digest = str(entry.get("sha256") or "").lower()
        if not entry.get("index_eligible"):
            ledger["items"][digest] = {
                "status": entry.get("selection_status") or "not_eligible",
                "name": entry.get("name"),
                "identity": entry.get("identity"),
                "updated_at": now_iso(),
            }
            continue
        identity = dict(entry.get("identity") or {})
        print(
            f"[{index}/{len(manifest.get('items') or [])}] INDEX "
            + " › ".join(value for value in (
                identity.get("organisme"), identity.get("project"),
                identity.get("subproject"), identity.get("year"),
            ) if value),
            flush=True,
        )
        try:
            run = get_sharepoint_audit(str(entry.get("scan_id") or ""))
            require_manifest_confirmation(run, entry.get("scan_manifest_sha256"))
            item = get_sharepoint_audit_item(
                str(entry.get("scan_id") or ""), str(entry.get("item_id") or "")
            )
            if str(item.get("sha256") or "").lower() != digest:
                raise PermissionError("Le hash du document ne correspond plus au manifeste global.")
            staged_path = validate_staged_path(item)
            conflict = memory_identity_conflict(
                digest=digest,
                organisme=identity.get("organisme"),
                project=identity.get("project"),
                subproject=identity.get("subproject") or "",
                year=identity.get("year"),
            )
            if conflict == "same_hash":
                previous = ledger["items"].get(digest) or {}
                pending_run = (
                    _memory_run_by_hash(digest)
                    if previous.get("status") == "artifacts_built_pending_global_chroma"
                    else None
                )
                if pending_run:
                    built_results.append((entry, pending_run))
                    previous["updated_at"] = now_iso()
                    ledger["items"][digest] = previous
                else:
                    ledger["items"][digest] = {
                        "status": "already_in_memory",
                        "name": entry.get("name"),
                        "identity": identity,
                        "updated_at": now_iso(),
                    }
                continue
            if conflict == "same_identity_other_version":
                raise RuntimeError("Une autre version existe déjà pour cette identité.")

            result = build_uploaded_cir(
                staged_path,
                organisme=identity.get("organisme"),
                project=identity.get("project"),
                subproject=identity.get("subproject") or "",
                year=identity.get("year"),
                vision_mode="text_only",
                formula_mode="off",
                rebuild_catalog=False,
                reset_chroma=False,
            )
            if not result.get("ok") or int(result.get("cards_count") or 0) <= 0:
                raise RuntimeError("Indexation terminée sans carte exploitable.")
            built_results.append((entry, result))
            ledger["items"][digest] = {
                "status": "artifacts_built_pending_global_chroma",
                "name": entry.get("name"),
                "identity": identity,
                "source_id": result.get("source_id"),
                "chunks_count": result.get("chunks_count"),
                "cards_count": result.get("cards_count"),
                "updated_at": now_iso(),
            }
        except Exception as exc:
            failure = {
                "status": "error",
                "name": entry.get("name"),
                "identity": identity,
                "error": str(exc),
                "updated_at": now_iso(),
            }
            ledger["items"][digest] = failure
            errors.append(failure)
        ledger["updated_at"] = now_iso()
        atomic_write_json(ledger_file, ledger)

    rebuild: dict[str, Any] = {"ok": True, "skipped": True, "reason": "Aucun nouveau CIR."}
    if errors:
        rebuild = {
            "ok": False,
            "skipped": True,
            "reason": "Au moins un CIR a échoué ; l'ancien Chroma actif est conservé.",
        }
    elif built_results:
        print("Construction isolée de la collection Chroma globale…", flush=True)
        rebuild = _rebuild_global_chroma_recoverably()
        for entry, result in built_results:
            identity = dict(entry.get("identity") or {})
            mark_audit_item_indexed(
                str(entry.get("scan_id") or ""),
                str(entry.get("item_id") or ""),
                result=result,
                identity=identity,
            )
            digest = str(entry.get("sha256") or "").lower()
            ledger["items"][digest]["status"] = "indexed"
            ledger["items"][digest]["indexed_at"] = now_iso()

    statuses = Counter(
        str(item.get("status") or "unknown") for item in ledger.get("items", {}).values()
    )
    report = {
        "ok": not errors and bool(rebuild.get("ok")),
        "mode": mode,
        "completed_at": now_iso(),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "statuses": dict(statuses),
        "errors": errors,
        "rebuild": rebuild,
        "source_write_operations": 0,
        "one_drive_modified": False,
        "chroma_collection": "ennosmart_memory_v2_global",
    }
    ledger["updated_at"] = now_iso()
    ledger["final_report"] = report
    atomic_write_json(ledger_file, ledger)
    return report


def status() -> dict[str, Any]:
    output: dict[str, Any] = {
        "source_root": str(SOURCE_ROOT),
        "audit_root": str(AUDIT_ROOT),
        "memory_root": str(settings.ENNOSMART_EXPERIENCE_MEMORY_V2_DIR),
        "source_write_operations": 0,
    }
    for mode in ("pilot_corplaux", "all_clients"):
        manifest = read_json(latest_manifest_path(mode), {})
        ledger = read_json(ledger_path(mode), {})
        output[mode] = {
            "manifest_path": str(latest_manifest_path(mode)),
            "manifest_exists": latest_manifest_path(mode).is_file(),
            "manifest_sha256": manifest.get("manifest_sha256"),
            "counts": manifest.get("counts") or {},
            "ledger_statuses": dict(Counter(
                str(item.get("status") or "unknown")
                for item in (ledger.get("items") or {}).values()
            )),
        }
    return output


def _print_scan_result(result: dict[str, Any], mode: str) -> None:
    print(json.dumps({
        "ok": result.get("ok"),
        "mode": mode,
        "manifest_path": result.get("manifest_path"),
        "manifest_sha256": result.get("manifest_sha256"),
        "counts": result.get("counts"),
        "items": [
            {
                "name": item.get("name"),
                "identity": item.get("identity"),
                "classification": item.get("classification"),
                "selection_status": item.get("selection_status"),
                "index_eligible": item.get("index_eligible"),
            }
            for item in result.get("items") or []
        ],
        "source_write_operations": result.get("source_write_operations"),
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("pilot", "all"):
        command = subparsers.add_parser(name)
        command.add_argument("--apply-latest", action="store_true")
        command.add_argument("--confirm", default="")
        command.add_argument("--include-probable", action="store_true")
        if name == "all":
            command.add_argument("--max-scopes", type=int, default=None)
    subparsers.add_parser("status")
    args = parser.parse_args()

    assert_safe_configuration()
    if args.command == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0

    mode = "pilot_corplaux" if args.command == "pilot" else "all_clients"
    if args.apply_latest:
        report = apply_latest(mode, confirmation=args.confirm)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if report.get("ok") else 2

    if args.command == "pilot":
        result = scan_pilot(include_probable=bool(args.include_probable))
    else:
        result = scan_all(
            include_probable=bool(args.include_probable),
            max_scopes=args.max_scopes,
        )
    _print_scan_result(result, mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
