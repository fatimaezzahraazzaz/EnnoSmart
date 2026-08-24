from __future__ import annotations

"""Reclasse des artefacts Memory V2 sans relire ni modifier le document source.

Exemple :
    python scripts/migrate_memory_v2_identity.py \
      --mapping "source_id|6NAPSE GROUP|CEVAA|APACHE|2024"

Les fichiers JSON sont remplacés atomiquement. Aucune copie de sauvegarde n'est
créée dans le dépôt et aucun chemin OneDrive n'est ouvert par ce script.
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env", override=False)
    load_dotenv(ROOT_DIR / "backend_api" / ".env", override=False)
except Exception:
    pass

ENGINE_PATH = ROOT_DIR / "scripts" / "experience_memory_v2_engine.py"
ENGINE_MODULE_NAME = "_ennosmart_memory_v2_migration_engine"
engine_spec = importlib.util.spec_from_file_location(ENGINE_MODULE_NAME, ENGINE_PATH)
if engine_spec is None or engine_spec.loader is None:
    raise ImportError(f"Moteur Memory V2 introuvable : {ENGINE_PATH}")
engine = importlib.util.module_from_spec(engine_spec)
sys.modules[ENGINE_MODULE_NAME] = engine
engine_spec.loader.exec_module(engine)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".tmp",
        prefix=f".{path.name}.",
        dir=path.parent,
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _parse_mapping(raw: str) -> dict[str, str]:
    parts = [part.strip() for part in str(raw or "").split("|")]
    if len(parts) != 5 or not all(parts[index] for index in (0, 1, 2, 4)):
        raise ValueError(
            "Mapping invalide. Format attendu : source_id|organisme|projet|sous-projet|année"
        )
    source_id, organisme, project, subproject, year = parts
    if not engine.is_year(year):
        raise ValueError(f"Année invalide pour {source_id}: {year}")
    return {
        "source_id": source_id,
        "organisme": organisme,
        "project": project,
        "subproject": subproject,
        "year": year,
    }


def _rewrite_tree(value: Any, identity: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_rewrite_tree(item, identity) for item in value]
    if not isinstance(value, dict):
        return value

    out = {key: _rewrite_tree(item, identity) for key, item in value.items()}
    carries_identity = (
        "organisme" in out
        and "project" in out
        and ("year" in out or "annee" in out)
    )
    for key in ("organisme", "project", "year"):
        if key in out:
            out[key] = identity[key]
    if carries_identity or "subproject" in out:
        out["subproject"] = identity["subproject"]
    if "annee" in out:
        out["annee"] = identity["year"]
    if "organisme_slug" in out:
        out["organisme_slug"] = engine.slugify(identity["organisme"])
    if "project_slug" in out:
        out["project_slug"] = engine.slugify(identity["project"])
    if "subproject_slug" in out or identity["subproject"]:
        out["subproject_slug"] = engine.slugify(identity["subproject"]) if identity["subproject"] else ""
    if "project_id" in out:
        out["project_id"] = engine.slugify(
            " ".join(part for part in (identity["project"], identity["subproject"]) if part)
        )
    if "relation_key_project" in out:
        out["relation_key_project"] = "::".join((
            engine.slugify(identity["organisme"]),
            engine.slugify(identity["project"]),
            engine.slugify(identity["subproject"]) if identity["subproject"] else "",
            identity["year"],
        ))
    if "source_path" in out and out.get("source_file"):
        out["source_path"] = str(out["source_file"])
    return out


def _artifact_paths(source_id: str) -> list[Path]:
    return [
        engine.V2_NLP_DIR / f"{source_id}.nlp_result.json",
        engine.V2_CHUNKS_DIR / f"{source_id}.chunks_v2.json",
        engine.V2_CARDS_DIR / f"{source_id}.cards.json",
        engine.V2_RUNS_DIR / f"{source_id}.run_v2.json",
    ]


def _prepare(identity: dict[str, str]) -> tuple[list[tuple[Path, Any]], str]:
    paths = _artifact_paths(identity["source_id"])
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Artefacts incomplets : " + ", ".join(missing))

    prepared: list[tuple[Path, Any]] = []
    source_hash = ""
    for path in paths:
        payload = _rewrite_tree(_read_json(path), identity)
        if path.name.endswith(".run_v2.json") and isinstance(payload, dict):
            payload.update(identity)
            payload["file"] = str(payload.get("file_name") or Path(str(payload.get("file") or "")).name)
            payload["source_copy_retained"] = False
            source_hash = str(payload.get("source_hash") or "").strip().lower()
        prepared.append((path, payload))
    return prepared, source_hash


def _audit_updates(
    audit_root: Path,
    *,
    identities_by_hash: dict[str, dict[str, str]],
) -> list[tuple[Path, Any]]:
    if not identities_by_hash or not (audit_root / "runs").is_dir():
        return []
    prepared: list[tuple[Path, Any]] = []
    for run_path in (audit_root / "runs").glob("*.json"):
        run = _read_json(run_path)
        if not isinstance(run, dict):
            continue
        changed = False
        scan_id = str(run.get("scan_id") or run_path.stem)
        for item in run.get("items") or []:
            if not isinstance(item, dict):
                continue
            identity = identities_by_hash.get(str(item.get("sha256") or "").lower())
            if not identity:
                continue
            item["detected_identity"] = {
                key: identity[key] for key in ("organisme", "project", "subproject", "year")
            }
            if item.get("indexed"):
                item["indexed_identity"] = dict(item["detected_identity"])
            item_path = audit_root / "items" / scan_id / f"{item.get('external_id')}.json"
            if item_path.is_file():
                item_payload = _read_json(item_path)
                if isinstance(item_payload, dict):
                    item_payload["detected_identity"] = dict(item["detected_identity"])
                    if item.get("indexed"):
                        item_payload["indexed_identity"] = dict(item["detected_identity"])
                    prepared.append((item_path, item_payload))
            changed = True
        if changed:
            prepared.append((run_path, run))
    return prepared


def _rebuild_global_chroma_recoverably() -> dict[str, Any]:
    """Construit le Chroma global à côté, puis archive l'ancien sans le supprimer."""
    active_chroma = engine.V2_CHROMA_DIR
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    staging_chroma = engine.V2_ROOT / f"_chroma_global_staging_{timestamp}"
    archive_root = engine.V2_ROOT / "_legacy_chroma_collections"
    archived_chroma = archive_root / f"chroma_before_single_global_{timestamp}"
    if staging_chroma.exists() or archived_chroma.exists():
        raise FileExistsError("Un chemin de reconstruction existe déjà ; relancez dans une seconde.")

    helper = ROOT_DIR / "scripts" / "rebuild_memory_v2_global.py"
    completed = subprocess.run(
        [sys.executable, str(helper), "--chroma-dir", str(staging_chroma)],
        cwd=str(ROOT_DIR),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "La construction isolée de Chroma a échoué : "
            + (completed.stderr or completed.stdout or f"code {completed.returncode}")[-4000:]
        )
    rebuild = json.loads(completed.stdout)

    database = staging_chroma / "chroma.sqlite3"
    if not database.is_file():
        raise RuntimeError("La nouvelle base Chroma globale n'a pas été créée.")

    archive_root.mkdir(parents=True, exist_ok=True)
    active_was_archived = False
    try:
        if active_chroma.exists():
            shutil.move(str(active_chroma), str(archived_chroma))
            active_was_archived = True
        shutil.move(str(staging_chroma), str(active_chroma))
        rebuild.setdefault("outputs", {})["chroma"] = str(active_chroma)
        rebuild.setdefault("chroma_reports", {})["preserved_previous_chroma"] = (
            str(archived_chroma) if active_was_archived else ""
        )
        engine.write_json(engine.V2_CATALOG, rebuild)
    except Exception:
        failed_chroma = archive_root / f"failed_global_rebuild_{timestamp}"
        if active_was_archived and active_chroma.exists():
            shutil.move(str(active_chroma), str(failed_chroma))
        if active_was_archived and archived_chroma.exists() and not active_chroma.exists():
            shutil.move(str(archived_chroma), str(active_chroma))
        raise
    return rebuild


def migrate(mappings: list[dict[str, str]], *, audit_root: Path, dry_run: bool) -> dict[str, Any]:
    prepared: list[tuple[Path, Any]] = []
    rows: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    identities_by_hash: dict[str, dict[str, str]] = {}
    for identity in mappings:
        artifacts, source_hash = _prepare(identity)
        if source_hash:
            identities_by_hash[source_hash] = identity
        for path, payload in artifacts:
            resolved = path.resolve()
            if resolved in seen_paths:
                raise ValueError(f"Le même fichier serait modifié deux fois : {resolved}")
            seen_paths.add(resolved)
            prepared.append((path, payload))
        rows.append({**identity, "source_hash": source_hash, "artifact_files_count": len(artifacts)})

    for path, payload in _audit_updates(
        audit_root,
        identities_by_hash=identities_by_hash,
    ):
        resolved = path.resolve()
        if resolved in seen_paths:
            raise ValueError(f"Le même fichier serait modifié deux fois : {resolved}")
        seen_paths.add(resolved)
        prepared.append((path, payload))

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "v2_root": str(engine.V2_ROOT),
            "mappings": rows,
            "files_to_update": [str(path) for path, _ in prepared],
            "one_drive_touched": False,
        }

    rollback_paths = {
        engine.V2_CATALOG,
        engine.V2_GLOBAL_GRAPH,
        engine.V2_RELATIONS_DIR / "relations_global.json",
    }
    originals = {
        path: path.read_bytes()
        for path in ({item_path for item_path, _ in prepared} | rollback_paths)
        if path.is_file()
    }
    try:
        for path, payload in prepared:
            _atomic_write_json(path, payload)
        rebuild = _rebuild_global_chroma_recoverably()
    except Exception:
        for path, content in originals.items():
            path.write_bytes(content)
        raise

    return {
        "ok": True,
        "dry_run": False,
        "v2_root": str(engine.V2_ROOT),
        "mappings": rows,
        "files_updated": len(prepared),
        "chroma_collection": "ennosmart_memory_v2_global",
        "legacy_collections_removed": (rebuild.get("chroma_reports") or {}).get(
            "removed_legacy_collections", []
        ),
        "preserved_previous_chroma": (rebuild.get("chroma_reports") or {}).get(
            "preserved_previous_chroma", ""
        ),
        "one_drive_touched": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mapping",
        action="append",
        required=True,
        help="source_id|organisme|projet|sous-projet|année (répétable)",
    )
    parser.add_argument(
        "--audit-root",
        default=os.getenv("POWER_AUTOMATE_AUDIT_ROOT", ""),
        help="Racine des journaux d'import à réaligner (facultatif).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    audit_root = Path(args.audit_root).resolve() if args.audit_root else engine.V2_ROOT / "_no_audit"
    result = migrate(
        [_parse_mapping(value) for value in args.mapping],
        audit_root=audit_root,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
