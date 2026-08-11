# -*- coding: utf-8 -*-
from __future__ import annotations

"""Inventaire non destructif des copies déposées par Power Automate.

Le module sépare volontairement trois responsabilités :

1. lire un dossier OneDrive professionnel synchronisé (réel ou factice) ;
2. créer un audit local et classifier les documents ;
3. laisser l'indexation Memory V2 à une action explicite séparée.

Ce module n'appelle ni SharePoint, ni Microsoft Graph. Power Automate est seul
responsable de déposer des copies dans le dossier d'import. EnnoSmart ouvre les
fichiers en lecture binaire et ne modifie jamais le dossier source.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import threading
import unicodedata
from typing import Any, Iterable, Protocol
from uuid import uuid4
import zipfile

from core.config import settings


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_ROOT = ROOT_DIR / "storage" / "power_automate_import"
DEFAULT_FAKE_ROOT = ROOT_DIR / "tests" / "fixtures" / "fake_power_automate_inbox"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
SCAN_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _safe_name(value: str, fallback: str = "document") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_. -]+", "_", str(value or "")).strip(" ._")
    return (cleaned or fallback)[:180]


def _normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.lower()).strip()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_tree(root: Path) -> dict[str, dict[str, Any]]:
    if not root.is_dir():
        return {}
    snapshot: dict[str, dict[str, Any]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        snapshot[relative] = {
            "sha256": _sha256_file(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return snapshot


@dataclass(frozen=True)
class ImportFolderItem:
    external_id: str
    name: str
    source_path: str
    size: int
    mime_type: str = "application/octet-stream"
    etag: str = ""
    last_modified: str = ""


class ReadOnlyImportProvider(Protocol):
    provider_name: str

    def list_items(self) -> list[ImportFolderItem]: ...

    def read_content(self, item: ImportFolderItem) -> bytes: ...


class LocalReadOnlyImportProvider:
    """Lecteur de dossier local sans méthode d'écriture, déplacement ou suppression."""

    def __init__(
        self,
        source_root: Path,
        *,
        provider_name: str = "power_automate_inbox",
        source_scope: str = "",
    ):
        self.provider_name = provider_name
        self.source_scope = str(source_scope or "").strip("/\\")
        self.source_root = source_root.resolve()
        if not self.source_root.is_dir():
            raise FileNotFoundError(f"Dossier de copies Power Automate introuvable : {self.source_root}")

    def list_items(self) -> list[ImportFolderItem]:
        items: list[ImportFolderItem] = []
        for path in sorted(candidate for candidate in self.source_root.rglob("*") if candidate.is_file()):
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS or path.name.startswith("~$"):
                continue
            relative = path.relative_to(self.source_root).as_posix()
            digest = _sha256_file(path)
            stat = path.stat()
            items.append(
                ImportFolderItem(
                    external_id=hashlib.sha256(relative.encode("utf-8")).hexdigest()[:32],
                    name=path.name,
                    source_path=relative,
                    size=stat.st_size,
                    mime_type=_mime_for(path),
                    etag=digest,
                    last_modified=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                )
            )
        return items

    def read_content(self, item: ImportFolderItem) -> bytes:
        target = (self.source_root / Path(item.source_path)).resolve()
        try:
            target.relative_to(self.source_root)
        except ValueError as exc:
            raise ValueError("Chemin hors du dossier d'import autorisé.") from exc
        if not target.is_file():
            raise FileNotFoundError(item.source_path)
        return target.read_bytes()


def assert_source_operation_allowed(operation: str) -> None:
    """Barrière explicite utilisée par les tests et les futurs appels métier."""
    if _normalise(operation) not in {"list", "read", "hash"}:
        raise PermissionError(
            "Le dossier d'import est en lecture seule : création, modification, déplacement et suppression interdits."
        )


def _mime_for(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(path.suffix.lower(), "application/octet-stream")


def _tree_digest(root: Path) -> str:
    payload = json.dumps(_snapshot_tree(root), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _extract_docx_preview(path: Path, max_chars: int) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    xml = re.sub(r"</w:p>|</w:tr>", "\n", xml)
    text = re.sub(r"<[^>]+>", " ", xml)
    return html.unescape(re.sub(r"[ \t]+", " ", text))[:max_chars]


def _extract_pdf_preview(path: Path, max_chars: int, max_pages: int) -> str:
    import fitz

    parts: list[str] = []
    with fitz.open(path) as document:
        indexes = list(range(min(max_pages, len(document))))
        if len(document) > max_pages:
            indexes.extend(index for index in range(max(max_pages, len(document) - 2), len(document)) if index not in indexes)
        for index in indexes:
            parts.append(f"[PAGE {index + 1}]\n{document[index].get_text('text')}")
            if sum(len(part) for part in parts) >= max_chars:
                break
    return "\n".join(parts)[:max_chars]


def extract_preview(path: Path, *, deep_scan: bool = False, max_chars: int = 80_000) -> dict[str, Any]:
    suffix = path.suffix.lower()
    errors: list[str] = []
    text = ""
    extraction_mode = "preview"
    try:
        if suffix in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
        elif suffix == ".docx":
            text = _extract_docx_preview(path, max_chars)
        elif suffix == ".pdf":
            text = _extract_pdf_preview(path, max_chars, max_pages=12)
        else:
            errors.append(f"Format non pris en charge : {suffix}")
    except Exception as exc:
        errors.append(f"Extraction rapide impossible : {exc}")

    needs_ocr = suffix == ".pdf" and len(_normalise(text)) < 300
    if deep_scan and (needs_ocr or len(text) < 800):
        try:
            if str(ROOT_DIR) not in sys.path:
                sys.path.insert(0, str(ROOT_DIR))
            from modules.extraction.base import SourceTag
            from modules.extraction.router import extract

            result = extract(path, source_tag=SourceTag.ARCHIVE, vision_mode="text_only", formula_mode="off")
            full_text = "\n".join(result.text_chunks or [])
            if full_text.strip():
                text = full_text[:max_chars]
                extraction_mode = "deep"
                needs_ocr = False
            errors.extend(str(error) for error in (result.extraction_errors or [])[:5])
        except Exception as exc:
            errors.append(f"Analyse approfondie impossible : {exc}")

    return {
        "text": text,
        "preview_excerpt": re.sub(r"\s+", " ", text).strip()[:900],
        "chars": len(text),
        "needs_ocr": needs_ocr,
        "extraction_mode": extraction_mode,
        "errors": errors,
    }


def classify_cir_document(text: str, *, file_name: str = "", source_path: str = "") -> dict[str, Any]:
    content = _normalise(text)
    weak_context = _normalise(f"{file_name} {source_path}")
    signals: list[dict[str, Any]] = []

    rules = [
        ("Crédit d'impôt recherche", r"credit d[ '\u2019-]*impot recherche|\bcir\b", 0.30, "identity"),
        ("Dossier justificatif", r"dossier justificatif", 0.20, "identity"),
        ("État de l'art", r"etat de l[ '\u2019-]*art", 0.11, "structure"),
        ("Verrou scientifique ou technique", r"verrou(?:x)? (?:scientifique|technique|technologique)", 0.14, "structure"),
        ("Travaux ou opérations de R&D", r"travaux (?:de recherche|realises)|operations? de (?:recherche|r&d)", 0.12, "structure"),
        ("Résultats et contributions", r"resultats? (?:obtenus|des travaux)|contribution scientifique", 0.08, "structure"),
        ("Référentiel Frascati", r"frascati|eligibilite (?:au )?cir", 0.08, "structure"),
        ("Personnel de recherche", r"personnel de recherche|chercheurs? et techniciens?", 0.05, "structure"),
        ("Version finale", r"version finale|document final|cir final", 0.08, "final"),
    ]
    score = 0.0
    groups: set[str] = set()
    for label, pattern, weight, group in rules:
        if re.search(pattern, content):
            score += weight
            groups.add(group)
            signals.append({"label": label, "source": "contenu", "weight": weight})

    if re.search(r"\bcir\b|credit.?impot.?recherche", weak_context):
        score += 0.04
        signals.append({"label": "Nom ou chemin évocateur", "source": "métadonnées", "weight": 0.04})

    draft = bool(re.search(r"\bbrouillon\b|\bdraft\b|version de travail|a relire|relecture", content + " " + weak_context))
    if draft:
        score -= 0.18
        signals.append({"label": "Marqueur de brouillon/relecture", "source": "contenu ou nom", "weight": -0.18})

    score = round(max(0.0, min(score, 0.99)), 2)
    structural_count = sum(1 for item in signals if item["weight"] > 0 and item["label"] in {
        "État de l'art", "Verrou scientifique ou technique", "Travaux ou opérations de R&D",
        "Résultats et contributions", "Référentiel Frascati", "Personnel de recherche",
    })
    has_identity = "identity" in groups

    if draft and score >= 0.35:
        classification = "cir_draft"
    elif score >= 0.72 and has_identity and structural_count >= 2:
        classification = "cir_final_confirmed"
    elif score >= 0.38:
        classification = "cir_probable"
    else:
        classification = "client_document"

    return {
        "classification": classification,
        "confidence": score,
        "signals": signals,
        "draft_signal": draft,
        "structural_signals_count": structural_count,
        "detected_identity": detect_document_identity(text, file_name=file_name, source_path=source_path),
    }


def detect_document_identity(text: str, *, file_name: str = "", source_path: str = "") -> dict[str, str]:
    raw = str(text or "")[:20_000]
    context = f"{raw}\n{file_name}\n{source_path}"

    def first(patterns: Iterable[str]) -> str:
        for pattern in patterns:
            match = re.search(pattern, raw, flags=re.IGNORECASE)
            if match:
                return re.sub(r"\s+", " ", match.group(1)).strip(" :-\t")[:120]
        return ""

    years = re.findall(r"\b(?:20[0-3]\d)\b", context)
    year = years[0] if years else ""
    organisme = first([
        r"(?:entreprise|société|societe|organisme|client)\s*[:\-]\s*([^\r\n]{2,100})",
    ])
    project = first([
        r"(?:nom du projet|projet|opération de recherche|operation de recherche)\s*[:\-]\s*([^\r\n]{2,120})",
    ])
    return {"organisme": organisme, "project": project, "year": year}


def _resolve_scope(root: Path, relative_folder: Any = "") -> tuple[Path, str]:
    root = root.resolve()
    relative = str(relative_folder or "").strip().replace("\\", "/").strip("/")
    relative_path = Path(relative) if relative else Path()
    if relative_path.is_absolute() or any(part in {".", ".."} for part in relative_path.parts):
        raise ValueError("Dossier de navigation invalide.")
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Dossier hors de la bibliothèque professionnelle autorisée.") from exc
    if not target.is_dir():
        raise FileNotFoundError(f"Dossier professionnel introuvable : {relative or root}")
    return target, relative_path.as_posix() if relative else ""


def list_import_folders(
    *,
    parent: Any = "",
    provider_name: str = "inbox",
) -> dict[str, Any]:
    """Liste un seul niveau de dossiers sans lire ni modifier les fichiers."""
    name = _normalise(provider_name).replace(" ", "_")
    if name in {"fake", "factice", "local"}:
        root = Path(settings.POWER_AUTOMATE_FAKE_ROOT or str(DEFAULT_FAKE_ROOT))
    elif name in {"inbox", "power_automate", "onedrive", "real"}:
        root = Path(settings.POWER_AUTOMATE_IMPORT_ROOT)
    else:
        raise ValueError("Source d'import inconnue.")

    current, current_relative = _resolve_scope(root, parent)
    folders: list[dict[str, Any]] = []
    for child in sorted(current.iterdir(), key=lambda item: item.name.casefold()):
        try:
            if not child.is_dir():
                continue
            resolved = child.resolve()
            resolved.relative_to(root.resolve())
            entries = list(child.iterdir())
            folders.append({
                "name": child.name,
                "relative_path": resolved.relative_to(root.resolve()).as_posix(),
                "has_children": any(entry.is_dir() for entry in entries),
                "supported_files_direct": sum(
                    1 for entry in entries
                    if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS and not entry.name.startswith("~$")
                ),
            })
        except (OSError, ValueError):
            continue

    direct_entries = list(current.iterdir())
    parent_relative = Path(current_relative).parent.as_posix() if current_relative else ""
    if parent_relative == ".":
        parent_relative = ""
    return {
        "ok": True,
        "provider": "fake" if name in {"fake", "factice", "local"} else "power_automate_inbox",
        "root": str(root.resolve()),
        "current": current_relative,
        "parent": parent_relative,
        "depth": len(Path(current_relative).parts) if current_relative else 0,
        "folders": folders,
        "supported_files_direct": sum(
            1 for entry in direct_entries
            if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS and not entry.name.startswith("~$")
        ),
        "source_write_operations": 0,
    }


def _provider_from_environment(provider_name: str, relative_folder: Any = "") -> ReadOnlyImportProvider:
    name = _normalise(provider_name).replace(" ", "_")
    if name in {"fake", "factice", "local"}:
        root = Path(settings.POWER_AUTOMATE_FAKE_ROOT or str(DEFAULT_FAKE_ROOT))
        scope_root, scope = _resolve_scope(root, relative_folder)
        return LocalReadOnlyImportProvider(scope_root, provider_name="fake", source_scope=scope)
    if name in {"inbox", "power_automate", "onedrive", "real"}:
        root = Path(settings.POWER_AUTOMATE_IMPORT_ROOT)
        scope_root, scope = _resolve_scope(root, relative_folder)
        return LocalReadOnlyImportProvider(scope_root, provider_name="power_automate_inbox", source_scope=scope)
    raise ValueError("Source d'import Power Automate inconnue.")


def import_configuration_status() -> dict[str, Any]:
    fake_root = Path(settings.POWER_AUTOMATE_FAKE_ROOT or str(DEFAULT_FAKE_ROOT))
    import_root = Path(settings.POWER_AUTOMATE_IMPORT_ROOT)
    return {
        "ok": True,
        "mode": "power_automate_local_inbox",
        "credentials_required": False,
        "client_id_required": False,
        "client_secret_required": False,
        "import_folder_configured": import_root.is_dir(),
        "import_root": str(import_root),
        "fake_available": fake_root.is_dir(),
        "fake_root": str(fake_root),
        "audit_root": str(Path(settings.POWER_AUTOMATE_AUDIT_ROOT or str(DEFAULT_AUDIT_ROOT))),
        "safety": {
            "source_operations": ["list", "read", "hash"],
            "sharepoint_write_enabled": False,
            "source_create_enabled": False,
            "source_update_enabled": False,
            "source_move_enabled": False,
            "source_delete_enabled": False,
            "automatic_source_delete": False,
            "automatic_memory_delete": False,
            "scan_indexes_memory": False,
            "index_requires_explicit_confirmation": True,
        },
    }


def run_sharepoint_audit(
    *,
    provider_name: str = "fake",
    initiated_by: str = "superadmin",
    deep_scan: bool = False,
    max_files: int | None = None,
    relative_folder: Any = "",
    provider: ReadOnlyImportProvider | None = None,
    audit_root: Path | None = None,
) -> dict[str, Any]:
    root = (audit_root or Path(settings.POWER_AUTOMATE_AUDIT_ROOT or str(DEFAULT_AUDIT_ROOT))).resolve()
    provider = provider or _provider_from_environment(provider_name, relative_folder)
    scan_id = f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    run_path = root / "runs" / f"{scan_id}.json"
    items_dir = root / "items" / scan_id
    staging_dir = root / "staging"
    cache_dir = root / "documents"

    source_root = getattr(provider, "source_root", None)
    before = _snapshot_tree(source_root) if isinstance(source_root, Path) else None
    run: dict[str, Any] = {
        "ok": False,
        "scan_id": scan_id,
        "provider": provider.provider_name,
        "mode": "read_only_audit",
        "status": "running",
        "started_at": _utc_now(),
        "initiated_by": initiated_by,
        "source_scope": str(getattr(provider, "source_scope", relative_folder) or "").replace("\\", "/").strip("/"),
        "deep_scan": bool(deep_scan),
        "credentials_used": False,
        "network_calls": 0,
        "source_write_operations": 0,
        "source_create_operations": 0,
        "source_update_operations": 0,
        "source_move_operations": 0,
        "source_delete_operations": 0,
        "memory_index_operations": 0,
        "items": [],
        "errors": [],
    }
    _json_write(run_path, run)

    with SCAN_LOCK:
        try:
            assert_source_operation_allowed("list")
            source_items = provider.list_items()
            if max_files is not None:
                source_items = source_items[: max(0, int(max_files))]
            max_bytes = int(settings.POWER_AUTOMATE_MAX_FILE_MB or 100) * 1024 * 1024

            for source_item in source_items:
                item_payload = asdict(source_item)
                item_payload.update({
                    "scan_id": scan_id,
                    "review_status": "pending",
                    "indexed": False,
                    "errors": [],
                })
                try:
                    if Path(source_item.name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                        item_payload.update({"classification": "unsupported", "confidence": 1.0})
                    elif source_item.size > max_bytes:
                        item_payload.update({
                            "classification": "too_large",
                            "confidence": 1.0,
                            "errors": [f"Fichier supérieur à la limite locale de {max_bytes // (1024 * 1024)} Mo."],
                        })
                    else:
                        assert_source_operation_allowed("read")
                        content = provider.read_content(source_item)
                        digest = _sha256_bytes(content)
                        assert_source_operation_allowed("hash")
                        cache_record_path = cache_dir / f"{digest}.json"
                        cached = _json_read(cache_record_path, None)
                        if isinstance(cached, dict) and Path(str(cached.get("staged_path") or "")).is_file():
                            for key in (
                                "classification", "confidence", "signals", "detected_identity",
                                "preview_excerpt", "preview_chars", "needs_ocr", "extraction_mode",
                                "staged_path", "sha256",
                            ):
                                item_payload[key] = cached.get(key)
                            item_payload.update({
                                "deduplicated": True,
                                "is_new_content": False,
                                "source_policy": "read_only_reused_hash",
                            })
                        else:
                            target_dir = staging_dir / digest[:2] / digest
                            target_dir.mkdir(parents=True, exist_ok=True)
                            staged_path = target_dir / _safe_name(source_item.name, "document")
                            staged_path.write_bytes(content)
                            preview = extract_preview(staged_path, deep_scan=deep_scan)
                            classification = classify_cir_document(
                                preview["text"],
                                file_name=source_item.name,
                                source_path=source_item.source_path,
                            )
                            item_payload.update(classification)
                            item_payload.update({
                                "sha256": digest,
                                "staged_path": str(staged_path),
                                "preview_excerpt": preview["preview_excerpt"],
                                "preview_chars": preview["chars"],
                                "needs_ocr": preview["needs_ocr"],
                                "extraction_mode": preview["extraction_mode"],
                                "errors": preview["errors"],
                                "deduplicated": False,
                                "is_new_content": True,
                                "source_policy": "power_automate_copy_read_only",
                            })
                            _json_write(cache_record_path, {
                                key: item_payload.get(key)
                                for key in (
                                    "classification", "confidence", "signals", "detected_identity",
                                    "preview_excerpt", "preview_chars", "needs_ocr", "extraction_mode",
                                    "staged_path", "sha256",
                                )
                            })
                except Exception as exc:
                    item_payload.update({
                        "classification": "scan_error",
                        "confidence": 0.0,
                        "errors": [str(exc)],
                    })

                item_id = _safe_name(source_item.external_id, "item")
                item_path = items_dir / f"{item_id}.json"
                item_payload["item_record_path"] = str(item_path)
                _json_write(item_path, item_payload)
                run["items"].append(item_payload)

            after = _snapshot_tree(source_root) if isinstance(source_root, Path) else None
            source_integrity_verified = before == after if before is not None else None
            counts: dict[str, int] = {}
            for item in run["items"]:
                key = str(item.get("classification") or "unknown")
                counts[key] = counts.get(key, 0) + 1
            run.update({
                "ok": True,
                "status": "completed",
                "completed_at": _utc_now(),
                "source_integrity_verified": source_integrity_verified,
                "source_snapshot_before": before if before is not None else None,
                "source_snapshot_after": after if after is not None else None,
                "counts": {
                    "discovered": len(source_items),
                    "audited": len(run["items"]),
                    "new_content": sum(1 for item in run["items"] if item.get("is_new_content")),
                    "deduplicated": sum(1 for item in run["items"] if item.get("deduplicated")),
                    **counts,
                },
            })
        except Exception as exc:
            run.update({"ok": False, "status": "failed", "completed_at": _utc_now()})
            run["errors"].append(str(exc))
        finally:
            _json_write(run_path, run)
    return run


def list_sharepoint_audits(*, limit: int = 20, audit_root: Path | None = None) -> list[dict[str, Any]]:
    root = (audit_root or Path(settings.POWER_AUTOMATE_AUDIT_ROOT or str(DEFAULT_AUDIT_ROOT))).resolve()
    runs: list[dict[str, Any]] = []
    for path in sorted((root / "runs").glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True) if (root / "runs").is_dir() else []:
        payload = _json_read(path, {})
        if isinstance(payload, dict):
            compact = {key: value for key, value in payload.items() if key not in {"items", "source_snapshot_before", "source_snapshot_after"}}
            compact["items_count"] = len(payload.get("items") or [])
            runs.append(compact)
        if len(runs) >= max(1, min(int(limit), 100)):
            break
    return runs


def get_sharepoint_audit(scan_id: str, *, audit_root: Path | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"scan_[A-Za-z0-9_]+", str(scan_id or "")):
        raise ValueError("Identifiant de scan invalide.")
    root = (audit_root or Path(settings.POWER_AUTOMATE_AUDIT_ROOT or str(DEFAULT_AUDIT_ROOT))).resolve()
    payload = _json_read(root / "runs" / f"{scan_id}.json", None)
    if not isinstance(payload, dict):
        raise FileNotFoundError("Audit Power Automate introuvable.")
    return payload


def get_sharepoint_audit_item(scan_id: str, item_id: str, *, audit_root: Path | None = None) -> dict[str, Any]:
    run = get_sharepoint_audit(scan_id, audit_root=audit_root)
    for item in run.get("items") or []:
        if str(item.get("external_id")) == str(item_id):
            return item
    raise FileNotFoundError("Document d'audit introuvable.")


def require_index_confirmation(value: Any) -> None:
    if str(value or "").strip() != "INDEXER_DANS_MEMORY_V2":
        raise PermissionError("Confirmation explicite requise : INDEXER_DANS_MEMORY_V2")


def mark_audit_item_indexed(
    scan_id: str,
    item_id: str,
    *,
    result: dict[str, Any],
    identity: dict[str, Any] | None = None,
    audit_root: Path | None = None,
) -> dict[str, Any]:
    root = (audit_root or Path(settings.POWER_AUTOMATE_AUDIT_ROOT or str(DEFAULT_AUDIT_ROOT))).resolve()
    run = get_sharepoint_audit(scan_id, audit_root=root)
    updated: dict[str, Any] | None = None
    for item in run.get("items") or []:
        if str(item.get("external_id")) != str(item_id):
            continue
        item.update({
            "review_status": "approved_and_indexed",
            "indexed": True,
            "indexed_at": _utc_now(),
            "indexed_identity": {
                "organisme": str((identity or {}).get("organisme") or "").strip(),
                "project": str((identity or {}).get("project") or "").strip(),
                "year": str((identity or {}).get("year") or "").strip(),
            },
            "index_result": {
                "ok": bool(result.get("ok")),
                "chunks_count": int(result.get("chunks_count") or 0),
                "cards_count": int(result.get("cards_count") or 0),
            },
        })
        updated = item
        _json_write(root / "items" / scan_id / f"{_safe_name(item_id, 'item')}.json", item)
        break
    if updated is None:
        raise FileNotFoundError("Document d'audit introuvable.")
    run["memory_index_operations"] = int(run.get("memory_index_operations") or 0) + 1
    _json_write(root / "runs" / f"{scan_id}.json", run)
    return updated


def mark_matching_items_memory_removed(
    organisme: Any,
    project: Any,
    year: Any,
    *,
    audit_root: Path | None = None,
) -> dict[str, Any]:
    """Désolidarise les audits de la mémoire supprimée, sans toucher à l'inbox."""
    root = (audit_root or Path(settings.POWER_AUTOMATE_AUDIT_ROOT or str(DEFAULT_AUDIT_ROOT))).resolve()
    wanted = (_normalise(organisme), _normalise(project), str(year or "").strip())
    updated_items = 0
    updated_runs = 0

    for run_path in (root / "runs").glob("*.json") if (root / "runs").is_dir() else []:
        run = _json_read(run_path, {})
        if not isinstance(run, dict):
            continue
        run_changed = False
        scan_id = str(run.get("scan_id") or run_path.stem)
        for item in run.get("items") or []:
            if not isinstance(item, dict) or not item.get("indexed"):
                continue
            identity = item.get("indexed_identity") or item.get("detected_identity") or {}
            candidate = (
                _normalise(identity.get("organisme")),
                _normalise(identity.get("project")),
                str(identity.get("year") or "").strip(),
            )
            if candidate != wanted:
                continue
            item.update({
                "review_status": "memory_removed",
                "indexed": False,
                "memory_removed_at": _utc_now(),
            })
            item.pop("index_result", None)
            item_path = root / "items" / scan_id / f"{_safe_name(str(item.get('external_id') or ''), 'item')}.json"
            _json_write(item_path, item)
            updated_items += 1
            run_changed = True
        if run_changed:
            _json_write(run_path, run)
            updated_runs += 1

    return {
        "ok": True,
        "audit_items_updated": updated_items,
        "audit_runs_updated": updated_runs,
        "source_modified": False,
    }


def validate_staged_path(item: dict[str, Any], *, audit_root: Path | None = None) -> Path:
    root = (audit_root or Path(settings.POWER_AUTOMATE_AUDIT_ROOT or str(DEFAULT_AUDIT_ROOT))).resolve()
    staged_root = (root / "staging").resolve()
    path = Path(str(item.get("staged_path") or "")).resolve()
    try:
        path.relative_to(staged_root)
    except ValueError as exc:
        raise ValueError("Copie locale hors de la zone d'audit autorisée.") from exc
    if not path.is_file():
        raise FileNotFoundError("Copie locale d'audit introuvable.")
    if item.get("sha256") and _sha256_file(path) != item["sha256"]:
        raise ValueError("La copie locale a changé depuis le scan ; indexation refusée.")
    return path
