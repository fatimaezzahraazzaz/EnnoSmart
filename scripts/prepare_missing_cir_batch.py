# -*- coding: utf-8 -*-
from __future__ import annotations

"""Prépare, sans indexer, les CIR finaux OneDrive absents de Memory V2.

La commande par défaut est strictement une préparation locale :

* OneDrive n'est utilisé que pour ``list/read/hash`` ;
* aucune écriture n'est autorisée sous ``SOURCE_ROOT`` ;
* Memory V2 et Chroma ne sont jamais ouverts en écriture ;
* les copies locales déjà produites par l'audit sont réutilisées ;
* les autres sources retenues sont copiées dans un magasin local par SHA-256 ;
* un manifeste signé doit être explicitement approuvé avant une future
  indexation (l'indexation n'est volontairement pas implémentée ici).
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import difflib
import hashlib
import html
import json
import os
from pathlib import Path, PurePath
import re
import shutil
import subprocess
import sys
import unicodedata
from typing import Any, Iterable
import zipfile


SOURCE_ROOT = Path(
    os.getenv(
        "POWER_AUTOMATE_IMPORT_ROOT",
        r"C:\Users\dell\OneDrive - ENNODEV\ENNODEV - Clients",
    )
).resolve()
DATA_ROOT = Path(os.getenv("ENNOSMART_DATA_ROOT", r"C:\EnnoSmartData")).resolve()
ITEMS_ROOT = DATA_ROOT / "power_automate_import" / "items"
AUDIT_STAGING_ROOT = DATA_ROOT / "power_automate_import" / "staging"
MEMORY_RUNS_ROOT = DATA_ROOT / "experience_memory_v2" / "runs"
OUTPUT_ROOT = Path(
    os.getenv(
        "ENNOSMART_MISSING_CIR_PREPARATION_ROOT",
        str(DATA_ROOT / "cir_index_preparation"),
    )
).resolve()
OBJECTS_ROOT = OUTPUT_ROOT / "objects"
MANIFEST_PATH = OUTPUT_ROOT / "missing_cir_prepared_manifest.json"
SUMMARY_PATH = OUTPUT_ROOT / "preparation_summary.json"
PDFTOTEXT = Path(
    os.getenv(
        "PDFTOTEXT_BIN",
        r"C:\EnnoSmart\poppler-25.12.0\Library\bin\pdftotext.exe",
    )
)

FINAL_DIR_MARKERS = (
    "dossier technique final",
    "dossier technique finale",
    "dossiers finaux",
    "dossier final",
    "version finale dossier",
    "version finale du dossier",
    "version finale",
    "versions finales",
    "livraison dts",
)
RAW_DIR_MARKERS = (
    "elements recus",
    "elements envoyes",
    "elements de travail",
    "donnees recues",
    "documents recus",
    "dts intermediaires",
)
EXCLUDED_PATH_MARKERS = (
    "valorisation financiere",
    "valorisation finale",
    "cerfa",
    "agrement",
    "contrats",
    "factures",
    "bulletins de paie",
    "bulletins salaires",
    "bulletins de salaires",
    "cv et diplomes",
    "cv et diplome",
    "dossier financier",
    "frais de personnel",
    "justificatifs financiers",
    "piece comptable",
    "pieces comptables",
    "subvention",
    "controle fiscal",
    "control fiscal",
)
EXCLUDED_NAME_MARKERS = (
    "2069",
    "cerfa",
    "procedure depot",
    "bon de livraison",
    "courrier accompagnement",
    "fiche d accompagnement",
    "rapport de stage",
    "diplome",
    "curriculum",
    "salaire",
    "bulletin",
    "attestation",
    "subvention",
    "kbis",
    "notice",
    "risque ",
    "annexe",
    "memoire ccc",
    "reponses suite",
    "reponse pr ",
)
DRAFT_MARKERS = (
    "brouillon",
    "draft",
    " trame",
    "template",
    "modele",
    " old",
    "/old/",
    "archive",
    "intermediaire",
    "a relire",
)
REVIEW_BLOCKERS = (
    "a renseigner",
    "questions en commentaires",
    "contributions a revoir",
    "non synthetique",
    "en l etat",
    "demande administration",
    "demande de l administration",
    "demande remboursement",
    "dossier pour remboursement",
    "courrier accompagnement",
    "saisie conciliateur",
    "saisie tribunal",
    "litige",
    "memoire ccc",
    "reponses suite",
    "reponse pr",
    "argumentaire",
    "cr de la reunion",
    "compte rendu",
    "doc client",
)
GENERIC_PROJECT_WORDS = {
    "cir", "cii", "dt", "dossier", "technique", "justificatif", "final",
    "finale", "version", "vf", "document", "credit", "impot", "recherche",
    "operation", "projet", "ed", "edition", "complet", "regroupe", "annexe",
    "rapport", "redaction", "last", "clean", "synthese",
}
GENERIC_PROJECT_DIR_MARKERS = (
    "redaction technique",
    "dossier technique",
    "dossier justificatif",
    "nouveau dossier",
    "version finale",
    "versions finales",
    "rapport final",
    "demande administration",
    "controle fiscal",
    "collecte justificatifs",
    "reponse",
    "elements de travail",
    "dts finaux",
    "livraison",
    "cir ",
    "cii ",
)
SUPPORTED = {".pdf", ".docx"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def long_path(path: str | Path) -> str:
    value = os.path.abspath(str(path))
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return "\\\\?\\" + value
    return value


def normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("\\", "/")
    return re.sub(r"[^a-z0-9/]+", " ", text).strip()


def words(value: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalise(value))


def key(value: Any) -> str:
    return "".join(words(value))


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def assert_local_write_path(path: Path) -> None:
    if is_within(path, SOURCE_ROOT):
        raise PermissionError(f"Écriture interdite dans OneDrive : {path}")
    if not is_within(path, OUTPUT_ROOT):
        raise PermissionError(f"Écriture hors de la zone de préparation : {path}")


def write_json(path: Path, payload: Any) -> None:
    assert_local_write_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(long_path(path), "rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iter_json_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for directory in os.scandir(root):
        if not directory.is_dir(follow_symlinks=False):
            continue
        for entry in os.scandir(directory.path):
            if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".json"):
                yield Path(entry.path)


def classify_cir_document(text: str, *, file_name: str, source_path: str) -> dict[str, Any]:
    content = normalise(text)
    context = normalise(f"{file_name} {source_path}")
    rules = [
        ("cir", r"credit d[ ]*impot recherche|\bcir\b", 0.30, "identity"),
        ("dossier", r"dossier justificatif", 0.20, "identity"),
        ("etat_art", r"etat de l[ ]*art", 0.11, "structure"),
        ("verrou", r"verrou(?:x)? (?:scientifique|technique|technologique)", 0.14, "structure"),
        ("travaux", r"travaux (?:de recherche|realises)|operations? de (?:recherche|r d)", 0.12, "structure"),
        ("resultats", r"resultats? (?:obtenus|des travaux)|contribution scientifique", 0.08, "structure"),
        ("frascati", r"frascati|eligibilite (?:au )?cir", 0.08, "structure"),
        ("personnel", r"personnel de recherche|chercheurs? et techniciens?", 0.05, "structure"),
        ("final", r"version finale|document final|cir final", 0.08, "final"),
    ]
    score = 0.0
    groups: set[str] = set()
    found: list[str] = []
    for label, pattern, weight, group in rules:
        if re.search(pattern, content):
            score += weight
            groups.add(group)
            found.append(label)
    if re.search(r"\bcir\b|credit.?impot.?recherche", context):
        score += 0.04
    draft = bool(re.search(
        r"\bbrouillon\b|\bdraft\b|version de travail|a relire|relecture",
        content + " " + context,
    ))
    if draft:
        score -= 0.18
    score = round(max(0.0, min(score, 0.99)), 2)
    structural_count = sum(
        label in {"etat_art", "verrou", "travaux", "resultats", "frascati", "personnel"}
        for label in found
    )
    if draft and score >= 0.35:
        classification = "cir_draft"
    elif score >= 0.72 and "identity" in groups and structural_count >= 2:
        classification = "cir_final_confirmed"
    elif score >= 0.38:
        classification = "cir_probable"
    else:
        classification = "client_document"
    return {
        "classification": classification,
        "confidence": score,
        "structural_signals_count": structural_count,
        "draft_signal": draft,
        "content_cir": "cir" in found,
        "preview_excerpt": re.sub(r"\s+", " ", text).strip()[:900],
    }


def extract_preview(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        with zipfile.ZipFile(long_path(path)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
        xml = re.sub(r"</w:p>|</w:tr>", "\n", xml)
        return html.unescape(re.sub(r"<[^>]+>", " ", xml))[:80_000]
    if not PDFTOTEXT.is_file():
        raise FileNotFoundError(f"pdftotext introuvable : {PDFTOTEXT}")
    with open(long_path(path), "rb") as source:
        completed = subprocess.run(
            [str(PDFTOTEXT), "-f", "1", "-l", "12", "-", "-"],
            stdin=source,
            capture_output=True,
            timeout=300,
            check=False,
        )
    if completed.returncode not in {0, 1} and not completed.stdout:
        detail = completed.stderr.decode("utf-8", errors="ignore")[-500:]
        raise RuntimeError(f"Extraction PDF impossible : {detail}")
    return completed.stdout.decode("utf-8", errors="ignore")[:80_000]


def enumerate_candidates() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["rg", "--files", "-g", "*.pdf", "-g", "*.docx", str(SOURCE_ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode not in {0, 1}:
        raise RuntimeError(completed.stderr.strip() or "Énumération OneDrive impossible")
    rows: list[dict[str, Any]] = []
    for source_text in completed.stdout.splitlines():
        source = Path(source_text)
        name = source.name
        if name.startswith("~$") or source.suffix.lower() not in SUPPORTED:
            continue
        relative = os.path.relpath(str(source), str(SOURCE_ROOT))
        client = relative.split(os.sep)[0]
        normalized_path = "/" + normalise(relative) + "/"
        normalized_name = normalise(source.stem)
        if key(client) == "ccc":
            continue
        if any(marker in normalized_path for marker in EXCLUDED_PATH_MARKERS):
            continue
        if any(marker in normalized_name for marker in EXCLUDED_NAME_MARKERS):
            continue
        if any(marker in normalized_path for marker in DRAFT_MARKERS):
            continue
        final_directory = any(marker in normalized_path for marker in FINAL_DIR_MARKERS)
        has_cir = bool(re.search(r"(^| )cir( |$)", normalized_name))
        has_final = bool(re.search(r"(^| )(vf|final|finale|definitif|valide)( |$)", normalized_name))
        has_dt = bool(re.search(r"(^| )(dt|dossier technique|dossier justificatif)( |$)", normalized_name))
        dossier_name = "dossier technique" in normalized_name or "dossier justificatif" in normalized_name
        explicit_final = has_final and (has_cir or has_dt)
        if not (final_directory or explicit_final or (dossier_name and "cir" in normalized_path)):
            continue
        score = (
            (6 if final_directory else 0)
            + (3 if has_cir else 0)
            + (4 if has_final else 0)
            + (3 if has_dt else 0)
            - (4 if any(marker in normalized_path for marker in RAW_DIR_MARKERS) else 0)
        )
        try:
            stat = os.stat(long_path(source))
            size = int(stat.st_size)
            mtime_ns = int(stat.st_mtime_ns)
        except OSError:
            size = 0
            mtime_ns = 0
        rows.append({
            "path": str(source),
            "relative": relative,
            "name": name,
            "normalized_name": normalise(name),
            "client": client,
            "size": size,
            "mtime_ns": mtime_ns,
            "path_score": score,
            "strong_path": bool(final_directory or explicit_final),
        })
    return rows


def load_cached_items() -> dict[str, list[dict[str, Any]]]:
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in iter_json_files(ITEMS_ROOT) or []:
        item = read_json(path, {})
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("file_name") or "")
        if name:
            by_name[normalise(name)].append(item)
    return by_name


def match_cached(candidate: dict[str, Any], options: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_rank: tuple[int, int, int, int] = (-1, -1, -1, -1)
    normalized_relative = normalise(candidate["relative"])
    for item in options:
        mtime_ns = int(item.get("mtime_ns") or 0)
        sizes = {
            int(item.get("size") or 0),
            int((item.get("source_signature_after_read") or {}).get("size") or 0),
            int((item.get("source_signature_final") or {}).get("size") or 0),
        }
        source_path = normalise(item.get("source_path") or "")
        rank = (
            1 if source_path and normalized_relative.endswith(source_path) else 0,
            1 if mtime_ns and candidate["mtime_ns"] and abs(mtime_ns - candidate["mtime_ns"]) < 2_000_000_000 else 0,
            1 if candidate["size"] and candidate["size"] in sizes else 0,
            int(item.get("preview_chars") or 0),
        )
        if rank > best_rank:
            best = item
            best_rank = rank
    return best if best is not None and sum(best_rank[:3]) >= 1 else None


def cached_is_accepted(candidate: dict[str, Any], item: dict[str, Any]) -> bool:
    classification = str(item.get("classification") or "")
    confidence = float(item.get("confidence") or 0)
    structural = int(item.get("structural_signals_count") or 0)
    content_cir = any(
        signal.get("source") == "contenu"
        and ("imp" in normalise(signal.get("label")) or normalise(signal.get("label")).startswith("cr"))
        for signal in (item.get("signals") or [])
    )
    return bool(
        not item.get("draft_signal")
        and (
            classification == "cir_final_confirmed"
            or (
                classification == "cir_probable"
                and confidence >= 0.52
                and structural >= 2
                and content_cir
                and candidate["strong_path"]
            )
        )
    )


def direct_is_accepted(candidate: dict[str, Any], analysis: dict[str, Any]) -> bool:
    return bool(
        not analysis.get("draft_signal")
        and (
            analysis.get("classification") == "cir_final_confirmed"
            or (
                analysis.get("classification") == "cir_probable"
                and float(analysis.get("confidence") or 0) >= 0.52
                and int(analysis.get("structural_signals_count") or 0) >= 2
                and analysis.get("content_cir") is True
                and candidate["strong_path"]
            )
        )
    )


def detect_year(relative: str, name: str) -> str:
    stem = re.sub(
        r"(?<!\d)20\d{2}[-_. ](?:0?[1-9]|1[0-2])[-_. ](?:0?[1-9]|[12]\d|3[01])(?!\d)",
        " ",
        Path(name).stem,
    )
    file_years = re.findall(r"(?<!\d)(20[0-3]\d)(?!\d)", stem)
    if file_years:
        return file_years[-1]
    for part in reversed(PurePath(relative).parts[:-1]):
        years = re.findall(r"(?<!\d)(20[0-3]\d)(?!\d)", part)
        if years:
            return years[-1]
    return "unknown"


def clean_project(value: str, client: str = "", organisme: str = "") -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    for identity in (client, organisme):
        if not identity:
            continue
        text = re.sub(re.escape(identity), " ", text, flags=re.IGNORECASE)
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9]+", identity):
            if len(token) >= 3:
                text = re.sub(rf"\b{re.escape(token)}\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:19|20)\d{2}\b|\b\d{8}\b", " ", text)
    text = re.sub(
        r"\b(?:CIR|CII|DT|VF\s*\d*|V\s*\d+(?:[.,]\d+)*|ED\s*\d+(?:[.,]\d+)*)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(
        r"\b(?:dossier|technique|justificatif|version|finale?|definitif|valide|regroupe|complet|rapport|last|clean|synthese)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^\s*\d+[. ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-_()")
    if key(client) == "scalian":
        text = re.sub(r"^\s*(?:scalian\s*)?ds\b", " ", text, flags=re.IGNORECASE).strip()
    meaningful = [token for token in words(text) if token not in GENERIC_PROJECT_WORDS]
    return text.title() if meaningful else ""


def infer_organisme(candidate: dict[str, Any]) -> str:
    parts = PurePath(candidate["relative"]).parts
    organisme = candidate["client"]
    if len(parts) > 2 and re.match(r"^\d+[. _-]", parts[1]):
        organisme = re.sub(r"^\d+[. _-]*", "", parts[1]).strip() or organisme
    return organisme


def infer_project(candidate: dict[str, Any], organisme: str, year: str) -> str:
    project = clean_project(Path(candidate["name"]).stem, candidate["client"], organisme)
    if project:
        return project
    parts = PurePath(candidate["relative"]).parts[1:-1]
    for part in reversed(parts):
        cleaned = re.sub(r"^\d+[. _-]*", "", part).strip()
        normalized = normalise(cleaned)
        if not normalized or normalized in {normalise(candidate["client"]), normalise(organisme), year}:
            continue
        if any(marker in normalized for marker in GENERIC_PROJECT_DIR_MARKERS):
            continue
        return cleaned
    excerpt = str((candidate.get("analysis") or {}).get("preview_excerpt") or "")
    match = re.search(
        r"(?:intitule du projet|fiche descriptive du projet)\s*[:\-]?\s*(.{3,120})",
        normalise(excerpt),
    )
    return match.group(1).strip() if match else f"Dossier CIR {year}"


def project_tokens(value: str) -> set[str]:
    return {
        token[:-1] if len(token) > 4 and token.endswith("s") else token
        for token in words(value)
        if token not in GENERIC_PROJECT_WORDS
    }


def project_similarity(left: str, right: str) -> float:
    left_tokens = project_tokens(left)
    right_tokens = project_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    if left_tokens == right_tokens:
        return 1.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    sequence = difflib.SequenceMatcher(
        None,
        " ".join(sorted(left_tokens)),
        " ".join(sorted(right_tokens)),
    ).ratio()
    if len(left_tokens) == len(right_tokens) == 1:
        return 1.0 if left_tokens == right_tokens else sequence * 0.6
    return max(jaccard, sequence)


def load_indexed_runs() -> tuple[list[dict[str, Any]], set[str]]:
    rows: list[dict[str, Any]] = []
    hashes: set[str] = set()
    if not MEMORY_RUNS_ROOT.is_dir():
        return rows, hashes
    for entry in os.scandir(MEMORY_RUNS_ROOT):
        if not entry.is_file(follow_symlinks=False) or not entry.name.lower().endswith(".json"):
            continue
        run = read_json(Path(entry.path), {})
        if not isinstance(run, dict) or not run.get("ok"):
            continue
        digest = str(run.get("source_hash") or "").lower()
        if digest:
            hashes.add(digest)
        rows.append(run)
    return rows, hashes


def cluster_accepted(accepted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_hash: dict[str, dict[str, Any]] = {}
    for row in accepted:
        digest = str(row.get("sha256") or row["relative"])
        previous = best_by_hash.get(digest)
        rank = (row["path_score"], row["mtime_ns"], row["size"])
        if previous is None or rank > (
            previous["path_score"], previous["mtime_ns"], previous["size"]
        ):
            best_by_hash[digest] = row

    groups: list[dict[str, Any]] = []
    for row in sorted(
        best_by_hash.values(),
        key=lambda value: (normalise(value["client"]), value["year"], normalise(value["project"])),
    ):
        group = next((
            current
            for current in groups
            if key(current["client"]) == key(row["client"])
            and current["year"] == row["year"]
            and project_similarity(current["project"], row["project"]) >= 0.86
        ), None)
        if group is None:
            groups.append({
                "client": row["client"],
                "organisme": row["organisme"],
                "year": row["year"],
                "project": row["project"],
                "files": [row],
            })
        else:
            group["files"].append(row)
            if (
                len(row["project"]) < len(group["project"])
                and not row["project"].startswith("Dossier CIR")
            ):
                group["project"] = row["project"]
    return groups


def filter_missing_groups(
    groups: list[dict[str, Any]],
    indexed_runs: list[dict[str, Any]],
    indexed_hashes: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    indexed_labels: list[tuple[str, str, str]] = []
    wildcards: list[tuple[str, str]] = []
    for run in indexed_runs:
        organisme = str(run.get("organisme") or "")
        year = str(run.get("year") or "")
        file_name = str(run.get("file") or run.get("file_name") or "")
        labels: list[str] = []
        for value in (
            clean_project(str(run.get("project") or ""), organisme, organisme),
            clean_project(Path(file_name).stem, organisme, organisme),
        ):
            if value and all(project_similarity(value, existing) < 0.90 for existing in labels):
                labels.append(value)
        generic = not labels or all(
            project_tokens(label) <= {"ds", "group", "groupe"}
            for label in labels
        )
        if generic and int(run.get("chunks_count") or 0) >= 100:
            wildcards.append((key(organisme), year))
        indexed_labels.extend((key(organisme), year, label) for label in labels)

    missing: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    for group in groups:
        exact = any(
            str(row.get("sha256") or "").lower() in indexed_hashes
            for row in group["files"]
        )
        semantic = False
        if not exact:
            client_key = key(group["client"])
            organisme_key = key(group["organisme"])
            for indexed_organisme, year, project in indexed_labels:
                if year != group["year"]:
                    continue
                same_organisme = (
                    indexed_organisme in {client_key, organisme_key}
                    or (
                        len(indexed_organisme) >= 5
                        and (
                            indexed_organisme in client_key
                            or client_key in indexed_organisme
                            or indexed_organisme in organisme_key
                            or organisme_key in indexed_organisme
                        )
                    )
                )
                if same_organisme and project_similarity(group["project"], project) >= 0.78:
                    semantic = True
                    break
            if not semantic:
                semantic = any(
                    year == group["year"]
                    and indexed_organisme in {client_key, organisme_key}
                    for indexed_organisme, year in wildcards
                )
        group["coverage"] = "exact" if exact else ("semantic" if semantic else "")
        (covered if group["coverage"] else missing).append(group)
    return missing, covered


def local_staged_candidate(row: dict[str, Any]) -> Path | None:
    analysis = row.get("analysis") or {}
    for raw in (analysis.get("index_staged_path"), analysis.get("staged_path")):
        if not raw:
            continue
        path = Path(str(raw))
        if not path.is_file() or is_within(path, SOURCE_ROOT):
            continue
        try:
            if str(row.get("sha256") or "") and sha256_file(path) == row["sha256"]:
                return path.resolve()
        except OSError:
            continue
    return None


def safe_file_name(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "document")).strip(" ._")
    return (cleaned or "document")[:160]


def copy_source_to_object(row: dict[str, Any]) -> tuple[Path, str, int]:
    source = Path(row["path"])
    if not is_within(source, SOURCE_ROOT):
        raise PermissionError(f"Source hors OneDrive autorisé : {source}")
    expected = str(row.get("sha256") or "").lower()
    for attempt in range(2):
        before = os.stat(long_path(source))
        temporary_dir = OBJECTS_ROOT / "pending"
        temporary = temporary_dir / f"{hashlib.md5(str(source).encode('utf-8')).hexdigest()}.tmp"
        assert_local_write_path(temporary)
        temporary_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        copied = 0
        try:
            with open(long_path(source), "rb") as reader, temporary.open("wb") as writer:
                for block in iter(lambda: reader.read(4 * 1024 * 1024), b""):
                    digest.update(block)
                    writer.write(block)
                    copied += len(block)
            after = os.stat(long_path(source))
            current = digest.hexdigest()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                temporary.unlink(missing_ok=True)
                if attempt == 0:
                    continue
                raise RuntimeError("La source a changé pendant la copie locale")
            if expected and current != expected:
                # Les placeholders OneDrive peuvent exposer une ancienne taille avant
                # hydratation. Le flux copié est la référence finale vérifiée.
                row["sha256_previous"] = expected
            destination = (
                OBJECTS_ROOT
                / current[:2]
                / current
                / safe_file_name(source.name)
            )
            assert_local_write_path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_file():
                if sha256_file(destination) != current:
                    raise RuntimeError(f"Objet local existant corrompu : {destination}")
                temporary.unlink(missing_ok=True)
            else:
                temporary.replace(destination)
            if sha256_file(destination) != current:
                raise RuntimeError("La copie locale ne correspond pas à son empreinte")
            return destination.resolve(), current, copied
        finally:
            temporary.unlink(missing_ok=True)
    raise RuntimeError(f"Copie locale impossible : {source}")


def review_flags(group: dict[str, Any], row: dict[str, Any]) -> list[str]:
    combined = normalise(f"{row['relative']} {row['name']} {group['project']}")
    flags: list[str] = []
    if any(marker in combined for marker in REVIEW_BLOCKERS):
        flags.append("administrative_or_review_marker")
    normalized_name = normalise(row["name"])
    if re.search(r"(^| )cii( |$)", normalized_name) and not re.search(r"(^| )cir( |$)", normalized_name):
        flags.append("cii_only_filename")
    tokens = project_tokens(group["project"])
    if not tokens or group["project"].startswith("Dossier CIR"):
        flags.append("generic_project_identity")
    if group["year"] == "unknown":
        flags.append("unknown_year")
    return sorted(set(flags))


def manifest_fingerprint(items: list[dict[str, Any]]) -> str:
    payload = [
        {
            "sha256": item.get("sha256"),
            "organisme": item.get("organisme"),
            "project": item.get("project"),
            "year": item.get("year"),
            "prepared_source_path": item.get("prepared_source_path"),
            "index_in_chroma": item.get("index_in_chroma"),
        }
        for item in items
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prepare() -> dict[str, Any]:
    if not SOURCE_ROOT.is_dir():
        raise FileNotFoundError(SOURCE_ROOT)
    if is_within(OUTPUT_ROOT, SOURCE_ROOT) or is_within(SOURCE_ROOT, OUTPUT_ROOT):
        raise PermissionError("La préparation locale doit être séparée de OneDrive")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    print("[1/5] Énumération OneDrive en lecture seule…", flush=True)
    candidates = enumerate_candidates()
    print(f"Candidats chemin/nom : {len(candidates)}", flush=True)

    print("[2/5] Rapprochement avec les analyses locales…", flush=True)
    cached_by_name = load_cached_items()
    indexed_runs, indexed_hashes = load_indexed_runs()
    accepted: list[dict[str, Any]] = []
    direct: list[dict[str, Any]] = []
    rejected = 0
    for candidate in candidates:
        cached = match_cached(candidate, cached_by_name.get(candidate["normalized_name"], []))
        if cached is None:
            direct.append(candidate)
            continue
        if cached_is_accepted(candidate, cached):
            candidate["analysis"] = cached
            candidate["sha256"] = str(cached.get("sha256") or "").lower()
            accepted.append(candidate)
        else:
            rejected += 1
    print(
        f"Acceptés via cache : {len(accepted)} | lectures directes : {len(direct)} | rejetés : {rejected}",
        flush=True,
    )

    print("[3/5] Lecture directe des candidats sans cache…", flush=True)
    inspection_errors: list[dict[str, str]] = []
    for index, candidate in enumerate(direct, start=1):
        if index == 1 or index % 20 == 0:
            print(f"[{index}/{len(direct)}] {candidate['client']} | {candidate['name']}", flush=True)
        try:
            preview = extract_preview(Path(candidate["path"]))
            analysis = classify_cir_document(
                preview,
                file_name=candidate["name"],
                source_path=candidate["relative"],
            )
            candidate["analysis"] = analysis
            if direct_is_accepted(candidate, analysis):
                candidate["sha256"] = sha256_file(Path(candidate["path"]))
                accepted.append(candidate)
            else:
                rejected += 1
        except Exception as exc:
            inspection_errors.append({"source_path": candidate["relative"], "error": str(exc)})

    for row in accepted:
        row["year"] = detect_year(row["relative"], row["name"])
        row["organisme"] = infer_organisme(row)
        row["project"] = infer_project(row, row["organisme"], row["year"])

    print("[4/5] Déduplication et comparaison Memory V2…", flush=True)
    groups = cluster_accepted(accepted)
    missing, covered = filter_missing_groups(groups, indexed_runs, indexed_hashes)
    missing.sort(key=lambda group: (
        normalise(group["client"]),
        group["year"],
        normalise(group["project"]),
    ))
    print(
        f"Groupes absents : {len(missing)} | groupes déjà couverts : {len(covered)}",
        flush=True,
    )

    print("[5/5] Préparation des sources locales vérifiées…", flush=True)
    prepared_items: list[dict[str, Any]] = []
    preparation_errors: list[dict[str, str]] = []
    reused = 0
    copied = 0
    copied_bytes = 0
    for index, group in enumerate(missing, start=1):
        canonical = max(
            group["files"],
            key=lambda row: (row["path_score"], row["mtime_ns"], row["size"]),
        )
        flags = review_flags(group, canonical)
        if index == 1 or index % 10 == 0:
            print(
                f"[{index}/{len(missing)}] PREP {group['client']} | {group['year']} | {group['project']}",
                flush=True,
            )
        try:
            prepared = local_staged_candidate(canonical)
            mode = "reuse_existing_audit_staging"
            digest = str(canonical.get("sha256") or "").lower()
            size = int(canonical.get("size") or 0)
            if prepared is None:
                prepared, digest, size = copy_source_to_object(canonical)
                canonical["sha256"] = digest
                mode = "copied_from_onedrive_read_only"
                copied += 1
                copied_bytes += size
            else:
                reused += 1
            alternatives = [
                {
                    "source_path": row["path"],
                    "file_name": row["name"],
                    "sha256": row.get("sha256"),
                    "classification": (row.get("analysis") or {}).get("classification"),
                    "confidence": (row.get("analysis") or {}).get("confidence"),
                }
                for row in group["files"]
                if row is not canonical
            ]
            item = {
                "client": group["client"],
                "organisme": group["organisme"],
                "project": group["project"],
                "year": group["year"],
                "source_path": canonical["path"],
                "source_relative": canonical["relative"],
                "file_name": canonical["name"],
                "sha256": digest,
                "sha256_previous": canonical.get("sha256_previous"),
                "size_bytes": size,
                "classification": (canonical.get("analysis") or {}).get("classification"),
                "confidence": (canonical.get("analysis") or {}).get("confidence"),
                "prepared_source_path": str(prepared),
                "preparation_mode": mode,
                "review_flags": flags,
                "review_status": "review_required" if flags else "ready",
                "index_in_chroma": not bool(flags),
                "alternative_versions": alternatives,
                "source_policy": "onedrive_strict_read_only",
                "source_write_operations": 0,
            }
            prepared_items.append(item)
        except Exception as exc:
            preparation_errors.append({
                "client": group["client"],
                "project": group["project"],
                "year": group["year"],
                "source_path": canonical["path"],
                "error": str(exc),
            })

    fingerprint = manifest_fingerprint(prepared_items)
    counts = {
        "path_candidates": len(candidates),
        "accepted_files": len(accepted),
        "covered_project_year_groups": len(covered),
        "missing_project_year_groups": len(missing),
        "prepared_groups": len(prepared_items),
        "index_ready_groups": sum(1 for item in prepared_items if item["index_in_chroma"]),
        "review_required_groups": sum(1 for item in prepared_items if not item["index_in_chroma"]),
        "reused_local_audit_copies": reused,
        "copied_from_onedrive_read_only": copied,
        "copied_bytes": copied_bytes,
        "inspection_errors": len(inspection_errors),
        "preparation_errors": len(preparation_errors),
    }
    manifest = {
        "ok": not bool(preparation_errors),
        "version": "missing_cir_prepared_manifest_v1",
        "created_at": now_iso(),
        "source_root": str(SOURCE_ROOT),
        "source_policy": "strict_read_only",
        "source_write_operations": 0,
        "memory_v2_write_operations": 0,
        "chroma_write_operations": 0,
        "approval_required_before_index": True,
        "manifest_sha256": fingerprint,
        "counts": counts,
        "inspection_errors": inspection_errors,
        "preparation_errors": preparation_errors,
        "items": prepared_items,
    }
    write_json(MANIFEST_PATH, manifest)
    summary = {
        "ok": manifest["ok"],
        "manifest": str(MANIFEST_PATH),
        "manifest_sha256": fingerprint,
        "counts": counts,
        "source_write_operations": 0,
        "memory_v2_write_operations": 0,
        "chroma_write_operations": 0,
    }
    write_json(SUMMARY_PATH, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def status() -> dict[str, Any]:
    manifest = read_json(MANIFEST_PATH, {})
    items = manifest.get("items") or [] if isinstance(manifest, dict) else []
    actual = manifest_fingerprint(items) if items else ""
    return {
        "manifest": str(MANIFEST_PATH),
        "exists": MANIFEST_PATH.is_file(),
        "counts": manifest.get("counts") or {},
        "manifest_sha256": manifest.get("manifest_sha256"),
        "fingerprint_valid": bool(actual and actual == manifest.get("manifest_sha256")),
        "approval_required_before_index": manifest.get("approval_required_before_index"),
        "source_write_operations": manifest.get("source_write_operations"),
        "memory_v2_write_operations": manifest.get("memory_v2_write_operations"),
        "chroma_write_operations": manifest.get("chroma_write_operations"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prépare les CIR absents sans indexer Chroma.")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if args.prepare:
        prepare()
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
