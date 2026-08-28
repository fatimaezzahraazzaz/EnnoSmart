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
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from typing import Any, Iterable, Protocol
from uuid import uuid4
import zipfile

from core.config import settings


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_ROOT = ROOT_DIR / "storage" / "power_automate_import"
DEFAULT_FAKE_ROOT = ROOT_DIR / "tests" / "fixtures" / "fake_power_automate_inbox"
DIRECT_EXTRACT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
SUPPORTED_EXTENSIONS = DIRECT_EXTRACT_EXTENSIONS | {".doc"}
SCAN_LOCK = threading.Lock()
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

FINAL_MARKERS = (
    "version finale", "versions finales", "dossier technique final",
    "rapport final", "cir final", "final consultant", "valide client",
)
DRAFT_MARKERS = (
    "brouillon", "draft", "version de travail", "a relire", "relecture",
    "commentaires", "ancienne version", "old", "archive",
)
GENERIC_PROJECT_DIRS = {
    "cir", "cii", "dossier technique", "dossier justificatif", "redaction technique",
    "version finale", "versions finales", "final", "livrable", "livrables",
    "documents", "document", "annee", "archive", "archives",
}


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


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _assert_safe_local_storage(storage_root: Path, source_root: Path | None) -> None:
    """Garantit qu'aucun artefact local ne peut être écrit dans OneDrive."""
    storage = storage_root.resolve()
    if source_root is None:
        return
    source = source_root.resolve()
    if _is_within(storage, source) or _is_within(source, storage):
        raise PermissionError(
            "Configuration dangereuse : la zone d'audit et la source OneDrive doivent être deux arborescences séparées."
        )


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


@dataclass(frozen=True)
class ImportFolderItem:
    external_id: str
    name: str
    source_path: str
    size: int
    mime_type: str = "application/octet-stream"
    etag: str = ""
    last_modified: str = ""
    mtime_ns: int = 0
    file_attributes: int = 0


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
        source_library_root: Path | None = None,
        recursive: bool = True,
        allowed_relative_paths: Iterable[str] | None = None,
    ):
        self.provider_name = provider_name
        self.source_scope = str(source_scope or "").strip("/\\")
        self.source_root = source_root.resolve()
        self.source_library_root = (source_library_root or source_root).resolve()
        self.recursive = bool(recursive)
        self.allowed_relative_paths = {
            Path(str(value)).as_posix().strip("/")
            for value in (allowed_relative_paths or [])
            if str(value or "").strip("/\\")
        }
        self.last_read_modes: dict[str, str] = {}
        if not self.source_root.is_dir():
            raise FileNotFoundError(f"Dossier de copies Power Automate introuvable : {self.source_root}")

    def list_items(self) -> list[ImportFolderItem]:
        items: list[ImportFolderItem] = []
        if self.allowed_relative_paths:
            candidates = [
                self.source_root / Path(relative)
                for relative in sorted(self.allowed_relative_paths)
            ]
        elif self.recursive:
            candidates = list(self.source_root.rglob("*"))
        else:
            candidates = list(self.source_root.iterdir())

        for path in sorted(candidate for candidate in candidates if candidate.is_file()):
            try:
                path.resolve().relative_to(self.source_root)
            except ValueError:
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS or path.name.startswith("~$"):
                continue
            relative = path.relative_to(self.source_root).as_posix()
            stat = path.stat()
            items.append(
                ImportFolderItem(
                    external_id=hashlib.sha256(relative.encode("utf-8")).hexdigest()[:32],
                    name=path.name,
                    source_path=relative,
                    size=stat.st_size,
                    mime_type=_mime_for(path),
                    # Ne pas hasher ici : le hash hydraterait tous les placeholders
                    # OneDrive avant même que l'utilisateur ait besoin du contenu.
                    etag="",
                    last_modified=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                    mtime_ns=int(stat.st_mtime_ns),
                    file_attributes=int(getattr(stat, "st_file_attributes", 0) or 0),
                )
            )
        return items

    def source_signature(self, item: ImportFolderItem) -> dict[str, int]:
        target = (self.source_root / Path(item.source_path)).resolve()
        try:
            target.relative_to(self.source_root)
        except ValueError as exc:
            raise ValueError("Chemin hors du dossier d'import autorisé.") from exc
        stat = target.stat()
        return {
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "file_attributes": int(getattr(stat, "st_file_attributes", 0) or 0),
        }

    def read_content(self, item: ImportFolderItem) -> bytes:
        target = (self.source_root / Path(item.source_path)).resolve()
        try:
            target.relative_to(self.source_root)
        except ValueError as exc:
            raise ValueError("Chemin hors du dossier d'import autorisé.") from exc
        if not target.is_file():
            raise FileNotFoundError(item.source_path)
        content, mode = _read_source_bytes_with_retry(target)
        self.last_read_modes[item.external_id] = mode
        return content


def _read_bytes_direct(path: Path) -> bytes:
    return path.read_bytes()


def _read_bytes_via_local_copy(path: Path) -> bytes:
    """Force l'hydratation Windows via une copie temporaire hors OneDrive."""
    with tempfile.TemporaryDirectory(prefix="ennosmart_onedrive_read_") as directory:
        local_copy = Path(directory) / _safe_name(path.name, "document")
        shutil.copyfile(path, local_copy)
        return local_copy.read_bytes()


def _read_source_bytes_with_retry(path: Path) -> tuple[bytes, str]:
    """Tolère les retours transitoires EINVAL des placeholders OneDrive."""
    errors: list[OSError] = []
    for attempt in range(3):
        try:
            return _read_bytes_direct(path), "direct"
        except OSError as exc:
            errors.append(exc)
            if os.name == "nt":
                try:
                    return _read_bytes_via_local_copy(path), "windows_local_copy_fallback"
                except OSError as fallback_exc:
                    errors.append(fallback_exc)
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
    detail = " | ".join(str(error) for error in errors[-3:])
    raise OSError(f"Lecture OneDrive impossible après 3 tentatives : {detail}")


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
        ".doc": "application/msword",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(path.suffix.lower(), "application/octet-stream")


def _libreoffice_binary() -> Path | None:
    configured = str(getattr(settings, "LIBREOFFICE_BIN", "") or os.getenv("LIBREOFFICE_BIN") or "").strip()
    candidates = [
        configured,
        shutil.which("soffice") or "",
        shutil.which("libreoffice") or "",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for raw in candidates:
        if raw and Path(raw).is_file():
            return Path(raw).resolve()
    return None


def legacy_doc_converter_status() -> dict[str, Any]:
    binary = _libreoffice_binary()
    return {
        "available": binary is not None,
        "name": "LibreOffice headless",
        "path": str(binary) if binary else "",
        "source_policy": "conversion_on_local_copy_only",
    }


def _convert_legacy_doc_copy(staged_doc: Path) -> Path:
    """Convertit uniquement la copie locale .doc ; ne reçoit jamais un chemin source."""
    binary = _libreoffice_binary()
    if binary is None:
        raise RuntimeError(
            "Ancien Word .doc détecté : LibreOffice headless doit être installé sur le serveur pour convertir la copie locale."
        )
    output_dir = (staged_doc.parent / "converted").resolve()
    profile_dir = (staged_doc.parent / "libreoffice_profile").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(binary),
        f"-env:UserInstallation={profile_dir.as_uri()}",
        "--headless",
        "--convert-to", "docx",
        "--outdir", str(output_dir),
        str(staged_doc.resolve()),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        shell=False,
    )
    converted = output_dir / f"{staged_doc.stem}.docx"
    if completed.returncode != 0 or not converted.is_file():
        detail = (completed.stderr or completed.stdout or "conversion sans résultat").strip()[-600:]
        raise RuntimeError(f"Conversion locale .doc impossible : {detail}")
    return converted


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
    subproject = first([
        r"(?:sous[\s-]*projet|sous[\s-]*opération|sous[\s-]*operation)\s*[:\-]\s*([^\r\n]{2,120})",
    ])
    return {"organisme": organisme, "project": project, "subproject": subproject, "year": year}


def _without_full_dates(value: Any) -> str:
    return re.sub(
        r"(?<!\d)(?:19|20)\d{2}[-_. ](?:0?[1-9]|1[0-2])[-_. ](?:0?[1-9]|[12]\d|3[01])(?!\d)",
        " ",
        str(value or ""),
    )


def _safe_identity_label(value: Any, fallback: str = "") -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" .-_/\\")
    cleaned = re.sub(r"[<>:\"|?*]+", " ", cleaned)
    return (cleaned or fallback)[:120]


def _identity_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalise(value)) or "unknown"


def _identity_group_key(identity: dict[str, Any]) -> str:
    return "::".join((
        _identity_key(identity.get("organisme")),
        _identity_key(identity.get("project")),
        _identity_key(identity.get("subproject")),
        str(identity.get("year") or "").strip(),
    ))


def _all_relative_parts(source_scope: str, source_path: str) -> list[str]:
    combined = "/".join(
        part.strip("/\\")
        for part in (str(source_scope or ""), str(source_path or ""))
        if part.strip("/\\")
    )
    return [part for part in re.split(r"[/\\]+", combined) if part]


def _detect_path_year(parts: list[str], file_name: str, detected_year: Any) -> str:
    for part in reversed(parts[:-1]):
        raw = part.strip()
        if YEAR_RE.fullmatch(raw):
            return raw
    file_years = YEAR_RE.findall(_without_full_dates(Path(file_name).stem))
    if file_years:
        return file_years[-1]
    for part in reversed(parts[:-1]):
        years = YEAR_RE.findall(_without_full_dates(part))
        if years:
            return years[-1]
    value = str(detected_year or "").strip()
    return value if YEAR_RE.fullmatch(value) else ""


def _meaningful_project_folder(parts: list[str], organisme: str, year: str) -> str:
    parents = parts[:-1]
    year_index = next((index for index in range(len(parents) - 1, -1, -1) if parents[index].strip() == year), -1)
    search = parents[:year_index] if year_index > 0 else parents
    for part in reversed(search):
        cleaned = re.sub(r"^\d+[. _-]*", "", part).strip()
        normalized = _normalise(cleaned)
        if not normalized or normalized == _normalise(organisme) or YEAR_RE.fullmatch(cleaned):
            continue
        if re.fullmatch(r"(?:cir|cii|cf)?\s*(?:19|20)\d{2}(?:\s+.*)?", normalized):
            continue
        if normalized in GENERIC_PROJECT_DIRS or any(marker in normalized for marker in FINAL_MARKERS):
            continue
        if any(marker in normalized for marker in DRAFT_MARKERS):
            continue
        return _safe_identity_label(cleaned)
    return ""


def _project_from_filename(file_name: str, *identity_labels: str) -> str:
    value = unicodedata.normalize("NFKC", _without_full_dates(Path(file_name).stem)).replace("_", " ")
    for label in identity_labels:
        if not str(label or "").strip():
            continue
        value = re.sub(re.escape(label), " ", value, flags=re.IGNORECASE)
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9]+", label):
            if len(token) >= 3:
                value = re.sub(rf"\b{re.escape(token)}\b", " ", value, flags=re.IGNORECASE)
    value = YEAR_RE.sub(" ", value)
    value = re.sub(
        r"\b(?:annexe|annex|pi[eè]ce\s+jointe)\s*(?:n[°o]\s*)?\d+[a-z]?\b",
        " ", value, flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b(?:CIR|CII|DT|VF\s*\d*|V\s*\d+(?:[.,]\d+)*|ED(?:ITION)?\s*\d+(?:[.,]\d+)*|FINAL(?:E)?|DEFINITIF|VALIDE)\b",
        " ", value, flags=re.IGNORECASE,
    )
    value = re.sub(r"\b(?:dossier|technique|justificatif|version|document|rapport|complet)\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"[-–—]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .-_")
    known_aliases = {
        "corpalux": "Corplaux",
        "corplaux": "Corplaux",
    }
    alias = known_aliases.get(_normalise(value))
    if alias:
        return alias
    return _safe_identity_label(value.title()) if len(_normalise(value)) >= 3 else ""


def infer_audit_identity(
    *,
    source_scope: str,
    source_path: str,
    file_name: str,
    detected: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Le classement organisme/projet/sous-projet/année prime sur le texte.

    Un dossier numéroté directement sous l'organisme (par exemple
    ``6NAPSE GROUP/1. CEVAA``) représente le projet. L'opération portée par le
    nom du CIR (APACHE, VECAME...) devient alors un sous-projet facultatif.
    """
    detected = detected or {}
    parts = _all_relative_parts(source_scope, source_path)
    identity_prefix_count = 1
    organisme_source = parts[0] if parts else detected.get("organisme")
    fixed_project = ""
    # Certains organismes rangent leurs projets au deuxième niveau sous la
    # forme « 1. CEVAA ». Le groupe reste l'organisme et CEVAA est le projet.
    if len(parts) > 1 and re.match(r"^\d+[. _-]+", parts[1]):
        numbered_project = re.sub(r"^\d+[. _-]*", "", parts[1]).strip()
        if numbered_project:
            fixed_project = _safe_identity_label(numbered_project)
            identity_prefix_count = 2
    organisme = _safe_identity_label(organisme_source, "Entreprise à confirmer")
    identity_parts = parts[identity_prefix_count:]
    year = _detect_path_year(identity_parts, file_name, detected.get("year"))
    project = fixed_project or _meaningful_project_folder(identity_parts, organisme, year)
    subproject = ""

    if fixed_project:
        subproject = _project_from_filename(file_name, organisme, project)
        detected_operation = _safe_identity_label(detected.get("subproject") or detected.get("project"))
        if not subproject and detected_operation and _normalise(detected_operation) != _normalise(project):
            subproject = detected_operation
    else:
        if not project:
            project = _project_from_filename(file_name, organisme)
        if not project:
            candidate = _safe_identity_label(detected.get("project"))
            if candidate and len(candidate) <= 90:
                project = candidate

    if not project:
        project = f"Dossier CIR {year}" if year else "Projet à confirmer"
    return {"organisme": organisme, "project": project, "subproject": subproject, "year": year}


def _version_numbers(file_name: str) -> list[int]:
    raw = _normalise(Path(file_name).stem)
    matches = re.findall(r"(?<![a-z0-9])(?:vf|version|edition|ed|v)\s*[-_. ]?\s*(\d+(?:[._-]\d+)*)", raw)
    values: list[int] = []
    for match in matches:
        values.extend(int(value) for value in re.findall(r"\d+", match))
    if not values and re.search(r"(?<![a-z0-9])(?:vf|finale?|definitif|valide)(?![a-z0-9])", raw):
        values.append(1)
    return values[:6]


def _embedded_date_rank(file_name: str) -> int:
    dates = re.findall(
        r"(?<!\d)((?:19|20)\d{2})[-_. ](0?[1-9]|1[0-2])[-_. ](0?[1-9]|[12]\d|3[01])(?!\d)",
        Path(file_name).stem,
    )
    if not dates:
        return 0
    year, month, day = dates[-1]
    return int(year) * 10_000 + int(month) * 100 + int(day)


def _selection_rank(item: dict[str, Any]) -> tuple[Any, ...]:
    context = _normalise(f"{item.get('name')} {item.get('source_path')}")
    suffix = Path(str(item.get("name") or "")).suffix.lower()
    return (
        1 if item.get("classification") == "cir_final_confirmed" else 0,
        1 if any(marker in context for marker in FINAL_MARKERS) else 0,
        tuple(int(value) for value in (item.get("version_numbers") or [])),
        int(item.get("embedded_date_rank") or 0),
        2 if suffix == ".pdf" else 1 if suffix == ".docx" else 0,
        int(item.get("mtime_ns") or 0),
        float(item.get("confidence") or 0),
        int(item.get("size") or 0),
    )


def _memory_records() -> tuple[set[str], set[str]]:
    memory_root = Path(
        os.getenv("ENNOSMART_EXPERIENCE_MEMORY_V2_DIR")
        or settings.ENNOSMART_EXPERIENCE_MEMORY_V2_DIR
    )
    hashes: set[str] = set()
    groups: set[str] = set()
    runs_dir = memory_root / "runs"
    for path in runs_dir.glob("*.run_v2.json") if runs_dir.is_dir() else []:
        payload = _json_read(path, {})
        if not isinstance(payload, dict) or not payload.get("ok"):
            continue
        digest = str(payload.get("source_hash") or "").strip().lower()
        if digest:
            hashes.add(digest)
        groups.add(_identity_group_key({
            "organisme": payload.get("organisme"),
            "project": payload.get("project"),
            "subproject": payload.get("subproject"),
            "year": payload.get("year"),
        }))
    return hashes, groups


def apply_final_version_policy(items: list[dict[str, Any]]) -> dict[str, int]:
    """Choisit une seule version recommandée par organisme/projet/année."""
    memory_hashes, memory_groups = _memory_records()
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        item.setdefault("recommended_version", False)
        item.setdefault("index_eligible", False)
        item["version_numbers"] = _version_numbers(str(item.get("name") or ""))
        item["embedded_date_rank"] = _embedded_date_rank(str(item.get("name") or ""))
        identity = item.get("detected_identity") or {}
        group = _identity_group_key(identity)
        item["selection_group"] = group
        digest = str(item.get("sha256") or "").lower()
        item["already_in_memory_by_hash"] = bool(digest and digest in memory_hashes)
        item["already_in_memory_by_identity"] = bool(group in memory_groups)
        if (
            item.get("classification") in {"cir_final_confirmed", "cir_probable"}
            and item.get("indexable") is True
            and identity.get("organisme")
            and identity.get("project")
            and YEAR_RE.fullmatch(str(identity.get("year") or ""))
        ):
            groups.setdefault(group, []).append(item)
        else:
            item["selection_status"] = "review_required" if "cir" in str(item.get("classification")) else "not_a_final_cir"

    recommended = alternatives = exact_duplicates = memory_conflicts = 0
    for group_items in groups.values():
        unique_by_hash: dict[str, dict[str, Any]] = {}
        duplicates: list[dict[str, Any]] = []
        for item in sorted(group_items, key=_selection_rank, reverse=True):
            digest = str(item.get("sha256") or "")
            if digest in unique_by_hash:
                item["duplicate_of_external_id"] = unique_by_hash[digest].get("external_id")
                item["selection_status"] = "exact_duplicate"
                duplicates.append(item)
                exact_duplicates += 1
            else:
                unique_by_hash[digest] = item
        ranked = sorted(unique_by_hash.values(), key=_selection_rank, reverse=True)
        if not ranked:
            continue
        chosen = ranked[0]
        chosen["recommended_version"] = True
        chosen["alternative_external_ids"] = [str(item.get("external_id") or "") for item in ranked[1:] + duplicates]
        chosen["alternative_versions_count"] = len(chosen["alternative_external_ids"])
        if chosen.get("already_in_memory_by_hash"):
            chosen["selection_status"] = "already_in_memory"
        elif chosen.get("already_in_memory_by_identity"):
            chosen["selection_status"] = "memory_version_conflict"
            memory_conflicts += 1
        else:
            chosen["selection_status"] = "recommended"
            chosen["index_eligible"] = True
            recommended += 1
        for item in ranked[1:]:
            item["selection_status"] = "older_alternative"
            item["recommended_version"] = False
            item["index_eligible"] = False
            alternatives += 1

    return {
        "recommended_for_index": recommended,
        "older_alternatives": alternatives,
        "exact_duplicates": exact_duplicates,
        "memory_version_conflicts": memory_conflicts,
    }


def audit_manifest_fingerprint(run: dict[str, Any]) -> str:
    payload = [
        {
            "external_id": str(item.get("external_id") or ""),
            "sha256": str(item.get("sha256") or ""),
            "identity": item.get("detected_identity") or {},
            "classification": str(item.get("classification") or ""),
            "recommended_version": bool(item.get("recommended_version")),
            "index_eligible": bool(item.get("index_eligible")),
        }
        for item in run.get("items") or []
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_manifest_confirmation(run: dict[str, Any], value: Any) -> None:
    expected = str(run.get("manifest_sha256") or "")
    actual = audit_manifest_fingerprint(run)
    if not expected or expected != actual:
        raise PermissionError("Le manifeste du scan a changé ; relancez le scan avant toute indexation.")
    if str(value or "").strip().lower() != expected.lower():
        raise PermissionError("Confirmez explicitement la signature du manifeste affiché.")


def memory_identity_conflict(
    *, digest: str, organisme: Any, project: Any, subproject: Any = "", year: Any,
) -> str:
    hashes, groups = _memory_records()
    if str(digest or "").lower() in hashes:
        return "same_hash"
    group = _identity_group_key({
        "organisme": organisme,
        "project": project,
        "subproject": subproject,
        "year": year,
    })
    if group in groups:
        return "same_identity_other_version"
    return ""


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


def _professional_import_root() -> Path:
    raw = str(settings.POWER_AUTOMATE_IMPORT_ROOT or "").strip()
    if not raw:
        raise FileNotFoundError("Le dossier professionnel POWER_AUTOMATE_IMPORT_ROOT n'est pas configuré.")
    root = Path(raw)
    if not root.is_dir():
        raise FileNotFoundError(f"Dossier professionnel introuvable : {root}")
    return root


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
        root = _professional_import_root()
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
        return LocalReadOnlyImportProvider(
            scope_root,
            provider_name="fake",
            source_scope=scope,
            source_library_root=root,
        )
    if name in {"inbox", "power_automate", "onedrive", "real"}:
        root = _professional_import_root()
        scope_root, scope = _resolve_scope(root, relative_folder)
        return LocalReadOnlyImportProvider(
            scope_root,
            provider_name="power_automate_inbox",
            source_scope=scope,
            source_library_root=root,
        )
    raise ValueError("Source d'import Power Automate inconnue.")


def import_configuration_status() -> dict[str, Any]:
    fake_root = Path(settings.POWER_AUTOMATE_FAKE_ROOT or str(DEFAULT_FAKE_ROOT))
    import_root_raw = str(settings.POWER_AUTOMATE_IMPORT_ROOT or "").strip()
    import_root = Path(import_root_raw) if import_root_raw else None
    audit_root = Path(settings.POWER_AUTOMATE_AUDIT_ROOT or str(DEFAULT_AUDIT_ROOT))
    storage_separated = True
    storage_error = ""
    if import_root is not None and import_root.is_dir():
        try:
            _assert_safe_local_storage(audit_root, import_root)
        except PermissionError as exc:
            storage_separated = False
            storage_error = str(exc)
    return {
        "ok": True,
        "mode": "power_automate_local_inbox",
        "credentials_required": False,
        "client_id_required": False,
        "client_secret_required": False,
        "import_folder_configured": bool(import_root is not None and import_root.is_dir()),
        "import_root": str(import_root) if import_root is not None else "",
        "fake_available": fake_root.is_dir(),
        "fake_root": str(fake_root),
        "audit_root": str(audit_root),
        "storage_separated_from_source": storage_separated,
        "storage_configuration_error": storage_error,
        "legacy_doc_converter": legacy_doc_converter_status(),
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
    source_library_root = getattr(provider, "source_library_root", source_root)
    _assert_safe_local_storage(root, source_library_root if isinstance(source_library_root, Path) else None)
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
                        signature_reader = getattr(provider, "source_signature", None)
                        post_read_signature = signature_reader(source_item) if callable(signature_reader) else None
                        listed_signature = {
                            "size": int(source_item.size),
                            "mtime_ns": int(source_item.mtime_ns or 0),
                            "file_attributes": int(source_item.file_attributes or 0),
                        }
                        item_payload.update({
                            "sha256": digest,
                            "source_signature_listed": listed_signature,
                            "source_signature_after_read": post_read_signature,
                            "source_hydrated_during_read": bool(
                                post_read_signature
                                and (
                                    int(post_read_signature.get("size") or 0) != listed_signature["size"]
                                    or (
                                    listed_signature["mtime_ns"] > 0
                                    and int(post_read_signature.get("mtime_ns") or 0) != listed_signature["mtime_ns"]
                                    )
                                    or int(post_read_signature.get("file_attributes") or 0) != listed_signature["file_attributes"]
                                )
                            ),
                            "source_read_mode": (getattr(provider, "last_read_modes", {}) or {}).get(
                                source_item.external_id, "provider"
                            ),
                        })
                        cache_record_path = cache_dir / f"{digest}.json"
                        cached = _json_read(cache_record_path, None)
                        cached_path = Path(str((cached or {}).get("staged_path") or "")) if isinstance(cached, dict) else Path()
                        cached_index_path = Path(str((cached or {}).get("index_staged_path") or "")) if isinstance(cached, dict) else Path()
                        cache_reusable = bool(
                            isinstance(cached, dict)
                            and cached.get("cache_schema_version") == 2
                            and cached_path.is_file()
                            and _sha256_file(cached_path) == digest
                            and cached_path.name == _safe_name(source_item.name, "document")
                            and (
                                source_item.name.lower().endswith(".doc") is False
                                or (cached.get("indexable") is True and cached_index_path.is_file())
                            )
                        )
                        if cache_reusable:
                            for key in (
                                "classification", "confidence", "signals", "detected_identity",
                                "preview_excerpt", "preview_chars", "needs_ocr", "extraction_mode",
                                "staged_path", "index_staged_path", "index_sha256", "sha256",
                                "indexable", "legacy_doc", "legacy_doc_conversion",
                                "errors",
                            ):
                                item_payload[key] = cached.get(key)
                            item_payload["detected_identity"] = infer_audit_identity(
                                source_scope=str(getattr(provider, "source_scope", "") or ""),
                                source_path=source_item.source_path,
                                file_name=source_item.name,
                                detected=item_payload.get("detected_identity") or {},
                            )
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
                            if _sha256_file(staged_path) != digest:
                                raise RuntimeError("La copie locale ne correspond pas au fichier lu ; audit refusé.")
                            preview_path = staged_path
                            index_path = staged_path
                            legacy_doc = staged_path.suffix.lower() == ".doc"
                            conversion: dict[str, Any] = {"required": legacy_doc, "ok": not legacy_doc}
                            conversion_error = ""
                            if legacy_doc:
                                try:
                                    index_path = _convert_legacy_doc_copy(staged_path)
                                    preview_path = index_path
                                    conversion.update({"ok": True, "output": str(index_path), "converter": "LibreOffice headless"})
                                except Exception as exc:
                                    conversion_error = str(exc)
                                    conversion.update({"ok": False, "error": conversion_error})

                            if legacy_doc and not conversion.get("ok"):
                                item_payload.update({
                                    "classification": "legacy_doc_requires_converter",
                                    "confidence": 0.0,
                                    "signals": [],
                                    "detected_identity": infer_audit_identity(
                                        source_scope=str(getattr(provider, "source_scope", "") or ""),
                                        source_path=source_item.source_path,
                                        file_name=source_item.name,
                                    ),
                                    "preview_excerpt": "",
                                    "preview_chars": 0,
                                    "needs_ocr": False,
                                    "extraction_mode": "legacy_doc_pending_conversion",
                                    "errors": [conversion_error],
                                    "indexable": False,
                                })
                            else:
                                preview = extract_preview(preview_path, deep_scan=deep_scan)
                                classification = classify_cir_document(
                                    preview["text"],
                                    file_name=source_item.name,
                                    source_path=source_item.source_path,
                                )
                                classification["detected_identity"] = infer_audit_identity(
                                    source_scope=str(getattr(provider, "source_scope", "") or ""),
                                    source_path=source_item.source_path,
                                    file_name=source_item.name,
                                    detected=classification.get("detected_identity") or {},
                                )
                                item_payload.update(classification)
                                item_payload.update({
                                    "preview_excerpt": preview["preview_excerpt"],
                                    "preview_chars": preview["chars"],
                                    "needs_ocr": preview["needs_ocr"],
                                    "extraction_mode": preview["extraction_mode"],
                                    "errors": preview["errors"],
                                    "indexable": not bool(preview["needs_ocr"]),
                                })
                            index_digest = _sha256_file(index_path) if index_path.is_file() else ""
                            item_payload.update({
                                "sha256": digest,
                                "staged_path": str(staged_path),
                                "index_staged_path": str(index_path) if index_path.is_file() else "",
                                "index_sha256": index_digest,
                                "legacy_doc": legacy_doc,
                                "legacy_doc_conversion": conversion,
                                "deduplicated": False,
                                "is_new_content": True,
                                "source_policy": "power_automate_copy_read_only",
                            })
                            _json_write(cache_record_path, {
                                **{
                                    key: item_payload.get(key)
                                    for key in (
                                    "classification", "confidence", "signals", "detected_identity",
                                    "preview_excerpt", "preview_chars", "needs_ocr", "extraction_mode",
                                    "staged_path", "index_staged_path", "index_sha256", "sha256",
                                    "indexable", "legacy_doc", "legacy_doc_conversion",
                                    "errors",
                                    )
                                },
                                "cache_schema_version": 2,
                            })
                        staged = Path(str(item_payload.get("staged_path") or ""))
                        final_signature = signature_reader(source_item) if callable(signature_reader) else post_read_signature
                        item_payload["source_signature_final"] = final_signature
                        item_payload["source_copy_verified"] = bool(
                            staged.is_file() and _sha256_file(staged) == digest
                        )
                        item_payload["source_metadata_stable_after_read"] = bool(
                            post_read_signature is None or final_signature == post_read_signature
                        )
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

            selection_counts = apply_final_version_policy(run["items"])
            # La politique de sélection ajoute des champs au manifeste : réécrire
            # les fiches locales une fois la comparaison inter-versions terminée.
            for item in run["items"]:
                item_id = _safe_name(str(item.get("external_id") or ""), "item")
                _json_write(items_dir / f"{item_id}.json", item)

            # Les fichiers volontairement ignorés (trop volumineux, non pris en
            # charge) n'ont pas de copie locale à vérifier. L'intégrité porte
            # uniquement sur les fichiers effectivement lus et hashés.
            source_integrity_verified = all(
                bool(item.get("source_copy_verified"))
                and bool(item.get("source_metadata_stable_after_read"))
                for item in run["items"]
                if item.get("sha256")
            )
            counts: dict[str, int] = {}
            for item in run["items"]:
                key = str(item.get("classification") or "unknown")
                counts[key] = counts.get(key, 0) + 1
            run.update({
                "ok": True,
                "status": "completed",
                "completed_at": _utc_now(),
                "source_integrity_verified": source_integrity_verified,
                "counts": {
                    "discovered": len(source_items),
                    "audited": len(run["items"]),
                    "new_content": sum(1 for item in run["items"] if item.get("is_new_content")),
                    "deduplicated": sum(1 for item in run["items"] if item.get("deduplicated")),
                    "legacy_doc": sum(1 for item in run["items"] if item.get("legacy_doc")),
                    "legacy_doc_conversion_required": sum(
                        1 for item in run["items"]
                        if item.get("legacy_doc") and not (item.get("legacy_doc_conversion") or {}).get("ok")
                    ),
                    **selection_counts,
                    **counts,
                },
            })
            run["manifest_sha256"] = audit_manifest_fingerprint(run)
            run["approval_required_before_index"] = True
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
                "subproject": str((identity or {}).get("subproject") or "").strip(),
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
    subproject: Any = "",
    audit_root: Path | None = None,
) -> dict[str, Any]:
    """Désolidarise les audits de la mémoire supprimée, sans toucher à l'inbox."""
    root = (audit_root or Path(settings.POWER_AUTOMATE_AUDIT_ROOT or str(DEFAULT_AUDIT_ROOT))).resolve()
    wanted = (
        _normalise(organisme),
        _normalise(project),
        _normalise(subproject),
        str(year or "").strip(),
    )
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
                _normalise(identity.get("subproject")),
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
    path = Path(str(item.get("index_staged_path") or item.get("staged_path") or "")).resolve()
    try:
        path.relative_to(staged_root)
    except ValueError as exc:
        raise ValueError("Copie locale hors de la zone d'audit autorisée.") from exc
    if not path.is_file():
        raise FileNotFoundError("Copie locale d'audit introuvable.")
    expected_hash = str(item.get("index_sha256") or item.get("sha256") or "")
    if expected_hash and _sha256_file(path) != expected_hash:
        raise ValueError("La copie locale a changé depuis le scan ; indexation refusée.")
    return path
