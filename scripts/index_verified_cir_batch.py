# -*- coding: utf-8 -*-
from __future__ import annotations

"""Indexation reprenable du manifeste CIR vérifié dans Memory V2/Chroma.

Hiérarchie produite : entreprise / projet / sous-projet (facultatif) /
année / CIR. Les sources sont exclusivement les copies locales préparées.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import sys
import unicodedata
from typing import Any


ROOT = Path(r"C:\EnnoSmart")
for entry in (ROOT, ROOT / "scripts", ROOT / "backend_api"):
    value = str(entry)
    if value not in sys.path:
        sys.path.insert(0, value)

import automate_cir_memory as automation
import prepare_missing_cir_batch as preparer
import verify_blocked_cir_batch as verifier


PREPARATION_ROOT = Path(r"C:\EnnoSmartData\cir_index_preparation").resolve()
MANIFEST_PATH = PREPARATION_ROOT / "index_ready_verified_manifest.json"
LEDGER_PATH = PREPARATION_ROOT / "verified_index_ledger.json"
REPORT_PATH = PREPARATION_ROOT / "verified_index_report.json"
BACKUPS_ROOT = PREPARATION_ROOT / "index_backups"
COLLECTION = "ennosmart_memory_v2_global"
CONFIRMATION = "INDEXER_526_CIR"
INVALID_SEGMENT = re.compile(r"[\\/:*?\"<>|]")

# Faux positifs découverts pendant le second contrôle intégral des 526 copies
# locales. Les empreintes rendent l'exclusion indépendante du nom ou du chemin.
# Aucun de ces documents n'est un CIR final consultant exploitable.
EXTENDED_VERIFICATION_EXCLUSIONS = {
    "39bf701ac4cd0d2a9b3db54477febe5a1da4b41de58af1c5ad62d468fb1c0fdf": "modèle générique ministériel CIR",
    "2ab4092be7cd73b73b8d23c826b3b55fbef8da4d59693ec2c01443028729e4e1": "courrier d'accompagnement administratif",
    "e5e18a941da9175d8b1fb71bf589f9d088c7d3dda547aa90ef301f1a76f57e34": "modèle générique ministériel CIR",
    "789bd2a814566aa1a95e32be3f6a78a9daa9a05cb3116c33a18e888fbe9a0cc1": "trame générique de dossier justificatif CIR",
    "2e376483f60e04f8c534460f755082db430a5d1a5bfb27b6df4e178ceaa85b1a": "dossier CII uniquement",
    "e278e0d7a645a0d8d90758a56b29c47e083b9cf700a3f5637dc194bd0d49ec89": "dossier CII uniquement",
    "00d6d5977694235e2a44fcbcc5d81fdeb8ec2df8e266dd89dabc90759478bd69": "dossier CII uniquement",
    "543aa1713b66e47c9a6d6457d53bbe9722d36e628bd2e61db24b17221f430052": "compte rendu d'audit",
    "17c7b56d447de5134e4ec3415aac9c7bb6f1db723e1ab94e8bd9641831c40d38": "réponses à des questions de mobilisation",
    "c364369d6b1f5e62a02a8e67d7a40724a3cb7daed4b6cb19fb3d4916f1dfffdc": "compte rendu de mission",
}

# Cas dont le fichier porte un nom générique, mais dont le contenu local a été
# relu et fournit une identité de projet fiable.
VERIFIED_OPERATION_OVERRIDES = {
    "400b2f5b60cf6e11b6655b572ffd26387063ae4a52fd374622ece6b6993dcdbb": "FAM-COR",
    "cb6e6deffdd773094348e6904398dbc21ec06faeaf3df543746fd7386d86032d": "GREEN IT",
}

# Corrections confirmées quand l'ancienne détection avait pris une date d'envoi
# ou de modification pour l'exercice fiscal du CIR.
VERIFIED_YEAR_OVERRIDES = {
    "ffe360536b580a2bf818dbb570c25fab76652bfc9056b002a940f9480857a5fe": "2022",
    "8de00eed2e98ed9c94ceea8c08c4cd5ca12c4f05892290ad5ea7b21db148f8de": "2017",
}


SCALIAN_PROGRAMS = {
    "alyotech": "ALYOTECH",
    "equert": "EQUERT",
    "etop": "ETOP",
    "eurogiciel": "EUROGICIEL",
    "evosys": "EVOSYS",
    "scalian dpc": "Scalian DPC",
    "scalian ds": "Scalian DS",
    "scalian op": "Scalian OP",
}
SMART4_PROGRAMS = {
    "lrt groupe": "LRT GROUPE",
    "seraap": "SERAAP",
    "sibylone": "SIBYLONE",
    "solent": "SOLENT",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("\\", "/")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def identity_key(identity: dict[str, str]) -> str:
    return "::".join((
        normalise(identity["enterprise"]).replace(" ", ""),
        normalise(identity["project"]).replace(" ", ""),
        normalise(identity.get("subproject") or "").replace(" ", ""),
        identity["year"],
    ))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write_json(path: Path, payload: Any) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(automation.SOURCE_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise PermissionError(f"Écriture interdite dans OneDrive : {resolved}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_label(value: Any, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" .-_\t\r\n")
    text = INVALID_SEGMENT.sub(" - ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-_\t\r\n")
    return (text or fallback)[:240]


def strip_numbered_prefix(value: str) -> str:
    return re.sub(r"^\s*\d+[. _-]*", "", str(value or "")).strip()


def clean_operation(item: dict[str, Any], enterprise: str, program: str = "") -> str:
    value = preparer.clean_project(
        str(item.get("project") or ""),
        enterprise,
        program,
    )
    override = VERIFIED_OPERATION_OVERRIDES.get(str(item.get("sha256") or "").lower())
    if override:
        return override
    # Les anciens fichiers contiennent souvent des marqueurs accolés
    # (CIR2024), des dates de livraison ou des commentaires de relecture.
    value = re.sub(r"\b(?:cir|cii|dt)\s*\d{2,4}\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:19|20)\d{2}\b", " ", value)
    value = re.sub(r"\b\d{1,2}[ ./_-]\d{1,2}[ ./_-](?:19|20)?\d{2}\b", " ", value)
    value = re.sub(r"\b(?:v|vf|ed)\s*\d*(?:[.,]\d+)*\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\b(?:rev(?:ue)?|ultimate|corrig[eé]e?|mise [àa] jour|sans figures?|avec figures?|ad|envoy[eé]|vu)\b.*$",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"^\s*(?:projet\s*\d*|it|pits|rf)\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:pdf[.]?io|copie|synth[eè]se)\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\bpart\s*\d+(?:\s*&\s*\d+)*\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" .-_()")
    if len(value) > 120 or normalise(value).startswith("pageref toc"):
        value = preparer.clean_project(Path(str(item.get("file_name") or "")).stem, enterprise, program)
        value = re.sub(r"\b(?:cir|cii|dt)\s*\d{2,4}\b|\b(?:19|20)\d{2}\b", " ", value, flags=re.IGNORECASE)
        value = re.sub(r"^\s*(?:it|pits|rf)\b", " ", value, flags=re.IGNORECASE)
        value = re.sub(r"\s+", " ", value).strip(" .-_()")
    value_words = normalise(value).split()
    if value_words and all(word in {"veille", "ok"} or word.isdigit() for word in value_words):
        value = ""
    return safe_label(value)


def hierarchy_for(item: dict[str, Any]) -> dict[str, str]:
    parts = PureWindowsPath(str(item.get("source_relative") or "")).parts
    enterprise = safe_label(item.get("client") or item.get("organisme"), "Entreprise inconnue")
    second = parts[1] if len(parts) > 1 else ""
    second_key = normalise(strip_numbered_prefix(second))
    program = ""

    enterprise_key = normalise(enterprise)
    if enterprise_key == "6napse group" and second:
        candidate = strip_numbered_prefix(second)
        if candidate and not re.fullmatch(r"(?:19|20)\d{2}", candidate):
            program = safe_label(candidate)
    elif enterprise_key == "scalian":
        if second_key in SCALIAN_PROGRAMS:
            program = SCALIAN_PROGRAMS[second_key]
        elif second_key == "dossiers scalian relus" and len(parts) > 2:
            program = SCALIAN_PROGRAMS.get(normalise(parts[2]), safe_label(parts[2]))
        elif second_key == "cir 2014 toutes entites":
            file_key = normalise(item.get("file_name"))
            if "eurogiciel" in file_key or "euro ing" in file_key:
                program = "EUROGICIEL"
    elif enterprise_key == "smart4 engineering" and second_key in SMART4_PROGRAMS:
        program = SMART4_PROGRAMS[second_key]
    elif enterprise_key == "polymont it services" and second_key == "novia":
        program = "NOVIA"
    elif enterprise_key == "ciel mon radis" and second_key == "charles perroud":
        program = "CHARLES PERROUD"
    elif enterprise_key == "segula" and "stf" in second_key:
        program = "STF"

    operation = clean_operation(item, enterprise, program)
    if program:
        project = safe_label(program)
        subproject = "" if not operation or preparer.project_similarity(operation, project) >= 0.90 else operation
    else:
        project = operation or "Dossier CIR"
        subproject = safe_label(item.get("subproject") or "")

    year = VERIFIED_YEAR_OVERRIDES.get(
        str(item.get("sha256") or "").lower(),
        str(item.get("year") or "").strip(),
    )
    if not re.fullmatch(r"(?:19|20)\d{2}", year):
        raise ValueError(f"Année invalide pour {item.get('file_name')} : {year}")
    if not enterprise or not project:
        raise ValueError(f"Hiérarchie incomplète : {item.get('file_name')}")
    return {
        "enterprise": enterprise,
        "project": project,
        "subproject": subproject,
        "year": year,
        "document_type": "CIR",
    }


def load_manifest(approved_sha256: str = "") -> dict[str, Any]:
    manifest = read_json(MANIFEST_PATH, {})
    if not isinstance(manifest, dict) or not manifest.get("items"):
        raise FileNotFoundError(MANIFEST_PATH)
    actual = verifier.manifest_fingerprint(manifest["items"])
    expected = str(manifest.get("manifest_sha256") or "").lower()
    if not expected or actual.lower() != expected:
        raise PermissionError("L'empreinte du manifeste vérifié est invalide.")
    if approved_sha256 and approved_sha256.lower() != expected:
        raise PermissionError(f"Signature attendue : {expected}")
    if manifest.get("approval_required_before_index") is not True:
        raise PermissionError("Le manifeste ne porte pas le verrou d'approbation attendu.")
    if int(manifest.get("onedrive_write_operations") or 0) != 0:
        raise PermissionError("Le manifeste signale une écriture OneDrive.")
    return manifest


def memory_records() -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    hashes: dict[str, dict[str, Any]] = {}
    identities: dict[str, list[dict[str, Any]]] = {}
    runs_root = Path(automation.settings.ENNOSMART_EXPERIENCE_MEMORY_V2_DIR) / "runs"
    for path in runs_root.glob("*.run_v2.json") if runs_root.is_dir() else []:
        run = read_json(path, {})
        if not isinstance(run, dict) or not run.get("ok"):
            continue
        digest = str(run.get("source_hash") or "").lower()
        if digest:
            hashes[digest] = run
        identity = {
            "enterprise": safe_label(run.get("organisme")),
            "project": safe_label(run.get("project")),
            "subproject": safe_label(run.get("subproject") or ""),
            "year": str(run.get("year") or ""),
        }
        identities.setdefault(identity_key(identity), []).append(run)
    return hashes, identities


def canonical_rank(entry: dict[str, Any]) -> tuple[int, int, int, str]:
    item = entry["source_item"]
    path = normalise(item.get("source_relative"))
    score = 0
    if any(marker in path for marker in (
        "dossier technique final", "version finale", "versions finales", "dts finaux",
    )):
        score += 10
    if "elements de travail" in path or "elements recus" in path:
        score -= 3
    if re.search(r"(?:^|[^a-z0-9])vf(?:[^a-z0-9]|$)", normalise(item.get("file_name"))):
        score += 3
    if re.search(r"\bpart\s*\d+(?:\s*&\s*\d+)*\b", normalise(item.get("file_name"))):
        score -= 12
    return (
        score,
        int(item.get("size_bytes") or 0),
        1 if item.get("verification_status") == "approved_for_index" else 0,
        str(item.get("sha256") or ""),
    )


def build_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    source_root = automation.SOURCE_ROOT.resolve()
    mapped: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in manifest["items"]:
        path = Path(item["prepared_source_path"]).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        try:
            path.relative_to(source_root)
        except ValueError:
            pass
        else:
            raise PermissionError(f"Source d'indexation située dans OneDrive : {path}")
        digest = sha256_file(path)
        if digest.lower() != str(item.get("sha256") or "").lower():
            raise PermissionError(f"SHA-256 incorrect : {path}")
        exclusion_reason = EXTENDED_VERIFICATION_EXCLUSIONS.get(digest.lower())
        if exclusion_reason:
            excluded.append({
                "sha256": digest,
                "file_name": item["file_name"],
                "prepared_source_path": str(path),
                "source_relative": item.get("source_relative"),
                "status": "excluded_after_extended_verification",
                "reason": exclusion_reason,
            })
            continue
        identity = hierarchy_for(item)
        mapped.append({
            "sha256": digest,
            "file_name": item["file_name"],
            "prepared_source_path": str(path),
            "source_relative": item.get("source_relative"),
            "identity": identity,
            "source_item": item,
        })

    by_identity: dict[str, list[dict[str, Any]]] = {}
    for entry in mapped:
        by_identity.setdefault(identity_key(entry["identity"]), []).append(entry)

    chosen: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for key, entries in by_identity.items():
        ranked = sorted(entries, key=canonical_rank, reverse=True)
        chosen.append(ranked[0])
        for duplicate in ranked[1:]:
            duplicates.append({
                **{name: duplicate[name] for name in (
                    "sha256", "file_name", "prepared_source_path", "source_relative", "identity",
                )},
                "status": "duplicate_identity_in_batch",
                "kept_sha256": ranked[0]["sha256"],
                "identity_key": key,
            })

    indexed_hashes, indexed_identities = memory_records()
    plan_items: list[dict[str, Any]] = []
    for entry in sorted(chosen, key=lambda row: (
        normalise(row["identity"]["enterprise"]),
        normalise(row["identity"]["project"]),
        normalise(row["identity"]["subproject"]),
        row["identity"]["year"],
        row["sha256"],
    )):
        digest = entry["sha256"].lower()
        key = identity_key(entry["identity"])
        if digest in indexed_hashes:
            status = "already_indexed_same_hash"
            existing = indexed_hashes[digest]
        elif key in indexed_identities:
            status = "already_represented_same_identity"
            existing = indexed_identities[key][0]
        else:
            status = "pending"
            existing = None
        plan_items.append({
            **{name: entry[name] for name in (
                "sha256", "file_name", "prepared_source_path", "source_relative", "identity",
            )},
            "identity_key": key,
            "status": status,
            "existing_source_hash": str((existing or {}).get("source_hash") or ""),
            "existing_file_name": str((existing or {}).get("file_name") or ""),
        })

    status_counts = Counter(item["status"] for item in plan_items)
    return {
        "version": "verified_cir_index_plan_v1",
        "created_at": now_iso(),
        "manifest_sha256": manifest["manifest_sha256"],
        "requested_items": len(manifest["items"]),
        "excluded_after_extended_verification": len(excluded),
        "index_candidate_items": len(mapped),
        "hierarchy_items": len(mapped),
        "with_subproject": sum(1 for entry in mapped if entry["identity"]["subproject"]),
        "duplicate_identity_in_batch": len(duplicates),
        "statuses": dict(status_counts),
        "items": plan_items,
        "duplicates": duplicates,
        "excluded": excluded,
        "onedrive_write_operations": 0,
    }


def backup_memory(ledger: dict[str, Any]) -> dict[str, Any]:
    existing = ledger.get("backup") or {}
    if existing.get("completed") and Path(existing.get("path") or "").is_dir():
        return existing
    memory_root = Path(automation.settings.ENNOSMART_EXPERIENCE_MEMORY_V2_DIR).resolve()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = BACKUPS_ROOT / f"before_{ledger['manifest_sha256'][:12]}_{stamp}"
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)

    copied: list[dict[str, Any]] = []
    for name in ("chroma", "relations"):
        source = memory_root / name
        if source.is_dir():
            target = destination / name
            shutil.copytree(source, target)
            copied.append({"source": str(source), "target": str(target), "type": "directory"})
    for name in ("catalog_v2.json", "global_memory_graph.json"):
        source = memory_root / name
        if source.is_file():
            target = destination / name
            shutil.copy2(source, target)
            copied.append({"source": str(source), "target": str(target), "type": "file"})

    inventory = {}
    for name in ("runs", "cards", "chunks", "nlp", "extraction"):
        root = memory_root / name
        inventory[name] = sorted(path.name for path in root.glob("*") if path.is_file())
    backup = {
        "completed": True,
        "created_at": now_iso(),
        "path": str(destination),
        "copied": copied,
        "artifact_inventory": inventory,
    }
    atomic_write_json(destination / "backup_manifest.json", backup)
    ledger["backup"] = backup
    atomic_write_json(LEDGER_PATH, ledger)
    return backup


def load_or_create_ledger(manifest: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    ledger = read_json(LEDGER_PATH, {})
    if isinstance(ledger, dict) and ledger.get("manifest_sha256"):
        if ledger["manifest_sha256"] != manifest["manifest_sha256"]:
            raise PermissionError("Un ledger d'un autre manifeste existe déjà.")
        # Le précontrôle peut encore affiner une identité avant la première
        # reconstruction globale. Réconcilier le plan est sûr tant que Chroma
        # n'a pas été publié ; les artefacts devenus obsolètes sont déplacés
        # vers une quarantaine récupérable, jamais supprimés.
        previous_report = ledger.get("final_report") or {}
        can_reconcile = (
            not previous_report
            or (
                previous_report.get("ok") is False
                and (previous_report.get("rebuild") or {}).get("skipped") is True
            )
        )
        if can_reconcile:
            old_items = ledger.get("items") or {}
            new_items = {item["sha256"]: dict(item) for item in plan["items"]}
            obsolete: list[dict[str, Any]] = []
            for digest, old in old_items.items():
                old_status = str(old.get("status") or "")
                new = new_items.get(digest)
                same_identity = bool(new and new.get("identity_key") == old.get("identity_key"))
                if old_status == "artifacts_built_pending_global_chroma" and same_identity:
                    for key in ("status", "source_id", "chunks_count", "cards_count", "built_at"):
                        if key in old:
                            new[key] = old[key]
                elif old_status == "artifacts_built_pending_global_chroma":
                    obsolete.append(old)

            if obsolete:
                memory_root = Path(automation.settings.ENNOSMART_EXPERIENCE_MEMORY_V2_DIR).resolve()
                quarantine_root = (
                    PREPARATION_ROOT / "quarantine" / manifest["manifest_sha256"][:12]
                ).resolve()
                moved: list[dict[str, str]] = []
                allowed_directories = {"runs", "cards", "chunks", "nlp", "extraction", "relations"}
                for old in obsolete:
                    source_id = safe_label(old.get("source_id") or "")
                    if not source_id:
                        raise RuntimeError("Artefact obsolète sans source_id ; reprise refusée.")
                    for directory in sorted(allowed_directories):
                        root = (memory_root / directory).resolve()
                        if root.parent != memory_root:
                            raise PermissionError(f"Répertoire Memory inattendu : {root}")
                        for source in root.glob(f"{source_id}.*") if root.is_dir() else []:
                            resolved = source.resolve()
                            if resolved.parent != root or not source.is_file():
                                raise PermissionError(f"Cible de quarantaine invalide : {resolved}")
                            target = quarantine_root / source_id / directory / source.name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            if target.exists():
                                raise FileExistsError(target)
                            shutil.move(str(resolved), str(target))
                            moved.append({"source": str(resolved), "target": str(target)})
                ledger.setdefault("reconciliations", []).append({
                    "at": now_iso(),
                    "reason": "correction d'identité avant publication Chroma",
                    "obsolete_records": [old.get("sha256") for old in obsolete],
                    "quarantined_files": moved,
                })

            ledger["plan_summary"] = {
                key: plan[key] for key in (
                    "requested_items", "excluded_after_extended_verification",
                    "index_candidate_items", "hierarchy_items", "with_subproject",
                    "duplicate_identity_in_batch", "statuses",
                )
            }
            ledger["duplicates"] = plan["duplicates"]
            ledger["excluded"] = plan["excluded"]
            ledger["items"] = new_items
            ledger["updated_at"] = now_iso()
            atomic_write_json(LEDGER_PATH, ledger)
        return ledger
    ledger = {
        "version": "verified_cir_index_ledger_v1",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "manifest_path": str(MANIFEST_PATH),
        "manifest_sha256": manifest["manifest_sha256"],
        "collection": COLLECTION,
        "plan_summary": {
            key: plan[key] for key in (
                "requested_items", "excluded_after_extended_verification",
                "index_candidate_items", "hierarchy_items", "with_subproject",
                "duplicate_identity_in_batch", "statuses",
            )
        },
        "duplicates": plan["duplicates"],
        "excluded": plan["excluded"],
        "items": {item["sha256"]: item for item in plan["items"]},
        "onedrive_write_operations": 0,
    }
    atomic_write_json(LEDGER_PATH, ledger)
    return ledger


def apply(manifest_sha256: str, confirmation: str) -> dict[str, Any]:
    if confirmation != CONFIRMATION:
        raise PermissionError(f"Confirmation obligatoire : {CONFIRMATION}")
    automation.assert_safe_configuration()
    manifest = load_manifest(manifest_sha256)
    plan = build_plan(manifest)
    ledger = load_or_create_ledger(manifest, plan)
    backup = backup_memory(ledger)
    items = list(ledger["items"].values())
    total_pending = sum(item.get("status") in {"pending", "error", "artifacts_built_pending_global_chroma"} for item in items)
    print(json.dumps({
        "manifest_sha256": manifest["manifest_sha256"],
        "requested": len(manifest["items"]),
        "excluded_after_extended_verification": len(ledger.get("excluded") or []),
        "planned_records": len(items),
        "pending_or_resumable": total_pending,
        "duplicates_in_batch": len(ledger.get("duplicates") or []),
        "backup": backup["path"],
    }, ensure_ascii=False), flush=True)

    errors: list[dict[str, Any]] = []
    pending_for_chroma: list[dict[str, Any]] = []
    built_now = 0
    resumed = 0
    for index, record in enumerate(items, 1):
        status = str(record.get("status") or "")
        identity = record["identity"]
        if status in {"already_indexed_same_hash", "already_represented_same_identity", "indexed"}:
            continue
        print(
            f"[{index}/{len(items)}] {identity['enterprise']} > {identity['project']}"
            + (f" > {identity['subproject']}" if identity.get("subproject") else "")
            + f" > {identity['year']} > CIR | {record['file_name']}",
            flush=True,
        )
        digest = record["sha256"].lower()
        try:
            local_path = Path(record["prepared_source_path"]).resolve()
            try:
                local_path.relative_to(automation.SOURCE_ROOT.resolve())
            except ValueError:
                pass
            else:
                raise PermissionError("La source locale pointe dans OneDrive.")
            if sha256_file(local_path) != digest:
                raise PermissionError("L'empreinte locale a changé.")

            if status == "artifacts_built_pending_global_chroma":
                run = automation._memory_run_by_hash(digest)
                if not run:
                    record["status"] = "pending"
                else:
                    pending_for_chroma.append({"record": record, "run": run})
                    resumed += 1
                    continue

            conflict = automation.memory_identity_conflict(
                digest=digest,
                organisme=identity["enterprise"],
                project=identity["project"],
                subproject=identity.get("subproject") or "",
                year=identity["year"],
            )
            if conflict == "same_hash":
                run = automation._memory_run_by_hash(digest)
                if record.get("status") in {"error", "pending"} and run:
                    record["status"] = "artifacts_built_pending_global_chroma"
                    pending_for_chroma.append({"record": record, "run": run})
                    resumed += 1
                else:
                    record["status"] = "already_indexed_same_hash"
                ledger["updated_at"] = now_iso()
                atomic_write_json(LEDGER_PATH, ledger)
                continue
            if conflict == "same_identity_other_version":
                record["status"] = "already_represented_same_identity"
                ledger["updated_at"] = now_iso()
                atomic_write_json(LEDGER_PATH, ledger)
                continue

            result = automation.build_uploaded_cir(
                local_path,
                organisme=identity["enterprise"],
                project=identity["project"],
                subproject=identity.get("subproject") or "",
                year=identity["year"],
                vision_mode="text_only",
                formula_mode="off",
                rebuild_catalog=False,
                reset_chroma=False,
            )
            if not result.get("ok") or int(result.get("cards_count") or 0) <= 0:
                raise RuntimeError("Aucune carte exploitable produite.")
            if str(result.get("source_hash") or "").lower() != digest:
                raise RuntimeError("Le moteur a retourné une autre empreinte source.")
            record.update({
                "status": "artifacts_built_pending_global_chroma",
                "source_id": result.get("source_id"),
                "chunks_count": result.get("chunks_count"),
                "cards_count": result.get("cards_count"),
                "built_at": now_iso(),
            })
            pending_for_chroma.append({"record": record, "run": result})
            built_now += 1
            print(
                f"  OK chunks={result.get('chunks_count')} cards={result.get('cards_count')}",
                flush=True,
            )
        except Exception as exc:
            record["status"] = "error"
            record["error"] = str(exc)
            record["error_at"] = now_iso()
            errors.append({
                "sha256": digest,
                "file_name": record["file_name"],
                "identity": identity,
                "error": str(exc),
            })
            print(f"  ERROR {exc}", flush=True)
        ledger["updated_at"] = now_iso()
        atomic_write_json(LEDGER_PATH, ledger)

    rebuild: dict[str, Any] = {
        "ok": not errors,
        "skipped": True,
        "reason": "Aucun nouvel artefact à intégrer." if not pending_for_chroma else "Erreurs présentes.",
    }
    if errors:
        print(f"CHROMA NON MODIFIÉ : {len(errors)} erreur(s) à résoudre.", flush=True)
    elif pending_for_chroma:
        print(
            f"Tous les artefacts sont prêts ({len(pending_for_chroma)}). "
            "Reconstruction isolée de Chroma…",
            flush=True,
        )
        rebuild = automation._rebuild_global_chroma_recoverably()
        if not rebuild.get("ok"):
            raise RuntimeError("La reconstruction globale Chroma a échoué.")
        indexed_at = now_iso()
        for pending in pending_for_chroma:
            pending["record"]["status"] = "indexed"
            pending["record"]["indexed_at"] = indexed_at
        ledger["updated_at"] = now_iso()
        atomic_write_json(LEDGER_PATH, ledger)

    statuses = Counter(str(item.get("status") or "unknown") for item in ledger["items"].values())
    report = {
        "ok": not errors and bool(rebuild.get("ok")),
        "completed_at": now_iso(),
        "manifest_sha256": manifest["manifest_sha256"],
        "collection": COLLECTION,
        "hierarchy": "entreprise/projet/sous-projet(si présent)/année/CIR",
        "requested_items": len(manifest["items"]),
        "excluded_after_extended_verification": len(ledger.get("excluded") or []),
        "extended_verification_exclusions": ledger.get("excluded") or [],
        "duplicates_in_batch": len(ledger.get("duplicates") or []),
        "excluded_after_extended_verification": len(ledger.get("excluded") or []),
        "built_now": built_now,
        "resumed_before_chroma": resumed,
        "statuses": dict(statuses),
        "errors": errors,
        "backup": backup,
        "rebuild": rebuild,
        "onedrive_write_operations": 0,
        "one_drive_modified": False,
    }
    ledger["final_report"] = report
    ledger["updated_at"] = now_iso()
    atomic_write_json(LEDGER_PATH, ledger)
    atomic_write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str), flush=True)
    return report


def status() -> dict[str, Any]:
    ledger = read_json(LEDGER_PATH, {}) or {}
    statuses = Counter(str(item.get("status") or "unknown") for item in (ledger.get("items") or {}).values())
    return {
        "ledger_exists": LEDGER_PATH.is_file(),
        "manifest_sha256": ledger.get("manifest_sha256"),
        "collection": ledger.get("collection"),
        "statuses": dict(statuses),
        "duplicates_in_batch": len(ledger.get("duplicates") or []),
        "backup": (ledger.get("backup") or {}).get("path"),
        "final_report": ledger.get("final_report"),
        "one_drive_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--approve-manifest-sha256", default="")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.preflight:
        manifest = load_manifest(args.approve_manifest_sha256)
        plan = build_plan(manifest)
        print(json.dumps({key: plan[key] for key in (
            "manifest_sha256", "requested_items", "hierarchy_items", "with_subproject",
            "excluded_after_extended_verification", "index_candidate_items",
            "duplicate_identity_in_batch", "statuses", "duplicates", "excluded",
        )}, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.apply:
        report = apply(args.approve_manifest_sha256, args.confirm)
        return 0 if report.get("ok") else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
