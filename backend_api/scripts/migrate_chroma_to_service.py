"""Copie les anciens Chroma embarqués vers le service Chroma HTTP unique.

Le mode par défaut est un inventaire strictement en lecture seule. Avec
``--apply``, chaque collection est copiée par lots puis vérifiée (ids + portée).
Les anciens dossiers ne sont supprimés qu'avec
``--delete-legacy-after-verify`` et seulement après vérification complète.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Iterable


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
for candidate in (PROJECT_ROOT, BACKEND_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from modules.RAG.chroma_client import (  # noqa: E402
    chroma_connection_info,
    chroma_http_enabled,
    create_chroma_client,
)
from modules.common.runtime_paths import storage_root  # noqa: E402


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _legacy_databases(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("chroma.sqlite3")
        if path.is_file() and _inside(path, root)
    )


def _sqlite_collection_names(database: Path) -> list[str]:
    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        rows = connection.execute(
            "SELECT name FROM collections ORDER BY name"
        ).fetchall()
        return [str(row[0]) for row in rows]
    except sqlite3.DatabaseError:
        return []
    finally:
        connection.close()


def _scope_from_path(chroma_dir: Path, root: Path) -> Dict[str, str]:
    """Déduit la portée depuis l'arborescence ProjectStore historique."""

    try:
        parts = list(chroma_dir.resolve().relative_to(root.resolve()).parts)
    except ValueError:
        return {}

    try:
        org_index = parts.index("organismes")
        organisme_id = parts[org_index + 1]
    except (ValueError, IndexError):
        return {}

    # Mémoire organisme : isolée de tous les autres organismes, mais conçue
    # pour fournir du contexte historique entre projets du même organisme.
    if "projects" not in parts[org_index + 2 :]:
        raw_scope = f"organism|{organisme_id}"
        return {
            "ennosmart_scope_type": "organism_memory",
            "ennosmart_scope_id": hashlib.sha256(raw_scope.encode("utf-8")).hexdigest()[:24],
            "ennosmart_organisme_id": organisme_id,
        }

    try:
        project_index = parts.index("projects", org_index + 2)
        project_id = parts[project_index + 1]
        year_index = parts.index("years", project_index + 2)
        year_id = parts[year_index + 1]
    except (ValueError, IndexError):
        return {}

    subproject_id = "-"
    try:
        subproject_index = parts.index("subprojects", project_index + 2, year_index)
        subproject_id = parts[subproject_index + 1]
    except (ValueError, IndexError):
        pass

    raw_scope = "|".join([organisme_id, project_id, subproject_id, year_id])
    return {
        "ennosmart_scope_type": "project",
        "ennosmart_scope_id": hashlib.sha256(raw_scope.encode("utf-8")).hexdigest()[:24],
        "ennosmart_organisme_id": organisme_id,
        "ennosmart_project_id": project_id,
        "ennosmart_subproject_id": subproject_id,
        "ennosmart_year_id": year_id,
    }


def _with_scope(metadata: Any, scope: Dict[str, str]) -> Dict[str, Any]:
    output = dict(metadata or {}) if isinstance(metadata, dict) else {}
    output.update(scope)
    return output


def _as_list(value: Any) -> Any:
    return value.tolist() if hasattr(value, "tolist") else value


def _copy_collection(
    source: Any,
    target_client: Any,
    *,
    scope: Dict[str, str],
    batch_size: int,
) -> Dict[str, Any]:
    name = str(source.name)
    source_count = int(source.count())
    source_metadata = dict(getattr(source, "metadata", None) or {})
    target_metadata = {**source_metadata, **scope}
    target = target_client.get_or_create_collection(
        name=name,
        metadata=target_metadata or None,
    )

    if scope and int(target.count()) > 0:
        existing = dict(getattr(target, "metadata", None) or {})
        mismatches = {
            key: (existing.get(key), expected)
            for key, expected in scope.items()
            if existing.get(key) not in (None, expected)
        }
        if mismatches:
            raise RuntimeError(
                f"Collision de portée pour la collection {name}: {mismatches}"
            )

    copied = 0
    for offset in range(0, source_count, batch_size):
        payload = source.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "metadatas", "embeddings"],
        )
        ids = [str(value) for value in payload.get("ids") or []]
        if not ids:
            continue
        kwargs: Dict[str, Any] = {"ids": ids}
        documents = payload.get("documents")
        embeddings = _as_list(payload.get("embeddings"))
        metadatas = payload.get("metadatas")
        if documents is not None:
            kwargs["documents"] = documents
        if embeddings is not None:
            kwargs["embeddings"] = embeddings
        if metadatas is not None or scope:
            values = list(metadatas or [{} for _ in ids])
            kwargs["metadatas"] = [
                _with_scope(values[index] if index < len(values) else {}, scope)
                for index in range(len(ids))
            ]
        target.upsert(**kwargs)

        verified = target.get(ids=ids, include=["metadatas"])
        verified_ids = {str(value) for value in verified.get("ids") or []}
        if verified_ids != set(ids):
            raise RuntimeError(
                f"Vérification des ids échouée pour {name} à l'offset {offset}."
            )
        for metadata in verified.get("metadatas") or []:
            if any((metadata or {}).get(key) != value for key, value in scope.items()):
                raise RuntimeError(
                    f"Vérification de portée échouée pour {name} à l'offset {offset}."
                )
        copied += len(ids)

    return {
        "name": name,
        "source_count": source_count,
        "copied_and_verified": copied,
        "target_count": int(target.count()),
        "scope": scope,
    }


def _validated_legacy_dir(database: Path, root: Path) -> Path:
    directory = database.parent.resolve()
    if directory.name.lower() != "chroma":
        raise RuntimeError(f"Dossier Chroma inattendu, suppression refusée : {directory}")
    if not _inside(directory, root) or directory == root.resolve():
        raise RuntimeError(f"Suppression hors storage refusée : {directory}")
    if not (directory / "chroma.sqlite3").is_file():
        raise RuntimeError(f"Base Chroma absente, suppression refusée : {directory}")
    return directory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--delete-legacy-after-verify", action="store_true")
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args()
    if args.delete_legacy_after_verify and not args.apply:
        parser.error("--delete-legacy-after-verify exige --apply")

    root = storage_root().resolve()
    databases = _legacy_databases(root)
    inventory = [
        {
            "database": str(database),
            "bytes": database.stat().st_size,
            "collections": _sqlite_collection_names(database),
            "scope": _scope_from_path(database.parent, root),
        }
        for database in databases
    ]

    if not args.apply:
        print(json.dumps({
            "mode": "dry-run",
            "storage_root": str(root),
            "legacy_databases": len(databases),
            "sqlite_bytes": sum(row["bytes"] for row in inventory),
            "inventory": inventory,
            "note": "Aucune écriture et aucune suppression effectuée.",
        }, ensure_ascii=False, indent=2))
        return 0

    if not chroma_http_enabled():
        raise RuntimeError(
            "Migration refusée : ENNOSMART_CHROMA_MODE=http est obligatoire."
        )
    target_client = create_chroma_client()
    heartbeat = target_client.heartbeat()
    batch_size = max(10, min(int(args.batch_size), 2000))
    results: list[Dict[str, Any]] = []
    deleted: list[str] = []

    import chromadb

    for database in databases:
        directory = database.parent
        scope = _scope_from_path(directory, root)
        source_client = chromadb.PersistentClient(path=str(directory))
        collections = source_client.list_collections()
        copied = [
            _copy_collection(
                collection,
                target_client,
                scope=scope,
                batch_size=batch_size,
            )
            for collection in collections
        ]
        results.append({
            "database": str(database),
            "collections": copied,
            "verified": True,
        })
        if args.delete_legacy_after_verify:
            target = _validated_legacy_dir(database, root)
            # Libère les handles SQLite avant la suppression du répertoire.
            del collections
            del source_client
            shutil.rmtree(target)
            deleted.append(str(target))

    print(json.dumps({
        "mode": "apply",
        "target": chroma_connection_info(),
        "heartbeat": heartbeat,
        "legacy_databases": len(databases),
        "collections_copied": sum(len(row["collections"]) for row in results),
        "deleted_legacy_directories": deleted,
        "results": results,
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

