# -*- coding: utf-8 -*-
from __future__ import annotations

"""Collecte locale en lecture seule des CIR finaux synchronisés.

Le script ne modifie jamais ``SOURCE_ROOT``. Il produit d'abord un manifeste
auditable, exclut deux projets de démonstration de Chroma, puis indexe les CIR
retenus avec une reconstruction globale unique et reprenable.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import unicodedata
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend_api"
SCRIPTS = ROOT / "scripts"
for entry in (str(ROOT), str(BACKEND), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from services.sharepoint_audit_service import classify_cir_document, extract_preview
from experience_memory_v2_engine import build_cir_final_v2, rebuild_global_graph_and_catalog


SOURCE_ROOT = Path(
    os.getenv(
        "POWER_AUTOMATE_IMPORT_ROOT",
        r"C:\Users\dell\OneDrive - Ennodev\ENNODEV - Clients",
    )
).resolve()
OUTPUT_ROOT = ROOT / "storage" / "cir_corpus_collection"
DISCOVERY_JSONL = OUTPUT_ROOT / "discovery_items.jsonl"
MANIFEST_PATH = OUTPUT_ROOT / "final_cir_manifest.json"
INDEX_LEDGER_PATH = OUTPUT_ROOT / "index_ledger.json"
DEMO_ROOT = ROOT / "storage" / "demo_projects"
MEMORY_RUNS = ROOT / "storage" / "experience_memory_v2" / "runs"

SUPPORTED = {".pdf", ".docx"}
YEAR_RE = re.compile(r"\b(20[0-3]\d)\b")

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
HARD_EXCLUDED_PATH = (
    "valorisation financiere",
    "valorisation finale",
    "cerfa",
    "agrement",
    "contrats",
    "factures",
    "bulletins de paie",
)
HARD_EXCLUDED_NAME = (
    "2069",
    "cerfa",
    "procedure depot",
    "bon de livraison",
    "courrier accompagnement",
    "fiche d accompagnement",
    "rapport de stage",
    "diplome",
    "curriculum",
    "kbis",
    "notice",
    "risque ",
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
GENERIC_PROJECT_WORDS = {
    "cir", "cii", "dt", "dossier", "technique", "justificatif", "final",
    "finale", "version", "vf", "document", "credit", "impot", "recherche",
}

DEMO_SPECS = (
    {
        "slug": "cevaa_apache_2024",
        "organisme_hint": "CEVAA",
        "project_key": "apache",
        "year": "2024",
        "cir_relative": "6NAPSE GROUP/1. CEVAA/CIR 2024/Dossier technique/Versions finales/CEVAA_APACHE_CIR-2024_VF.pdf",
        "raw_relative": "6NAPSE GROUP/1. CEVAA/CIR 2024/Dossier technique/Eléments reçus/APACHE",
        "title": "CEVAA — APACHE — 2024",
    },
    {
        "slug": "cevaa_vecame_2024",
        "organisme_hint": "CEVAA",
        "project_key": "vecame",
        "year": "2024",
        "cir_relative": "6NAPSE GROUP/1. CEVAA/CIR 2024/Dossier technique/Versions finales/CEVAA_CIR_2024_VECAME_VF.pdf",
        "raw_relative": "6NAPSE GROUP/1. CEVAA/CIR 2024/Dossier technique/Eléments reçus/VECAME",
        "title": "CEVAA — VECAME — 2024",
    },
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("\\", "/")
    return re.sub(r"[^a-z0-9/]+", " ", text).strip()


def key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", norm(value))


def safe_label(value: Any, fallback: str) -> str:
    label = re.sub(r"[\\/:*?\"<>|]+", " ", str(value or ""))
    label = re.sub(r"\s+", " ", label).strip(" .-_")
    return (label or fallback)[:120]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except Exception:
                continue
    return rows


def relative_text(path: Path) -> str:
    return path.resolve().relative_to(SOURCE_ROOT).as_posix()


def candidate_score(path: Path) -> tuple[int, list[str]]:
    relative = relative_text(path)
    normalized_path = "/" + norm(relative) + "/"
    normalized_name = norm(path.stem)
    reasons: list[str] = []
    score = 0

    if any(marker in normalized_path for marker in HARD_EXCLUDED_PATH):
        return -100, ["excluded_business_folder"]
    if any(marker in normalized_name for marker in HARD_EXCLUDED_NAME):
        return -100, ["excluded_non_technical_file"]
    if any(marker in normalized_path for marker in DRAFT_MARKERS):
        return -100, ["excluded_draft_or_archive"]

    final_directory = any(marker in normalized_path for marker in FINAL_DIR_MARKERS)
    name_has_cir = bool(re.search(r"(^| )cir( |$)", normalized_name))
    name_has_final = bool(re.search(r"(^| )(vf|final|finale|definitif|valide)( |$)", normalized_name))
    dossier_name = "dossier technique" in normalized_name or "dossier justificatif" in normalized_name

    if final_directory:
        score += 6
        reasons.append("final_directory")
    if name_has_cir:
        score += 3
        reasons.append("cir_filename")
    if name_has_final:
        score += 4
        reasons.append("final_filename")
    if dossier_name:
        score += 3
        reasons.append("technical_dossier_filename")
    if any(marker in normalized_path for marker in RAW_DIR_MARKERS):
        score -= 4
        reasons.append("working_or_received_folder")

    if not final_directory and not (name_has_cir and name_has_final) and not (dossier_name and "cir" in normalized_path):
        return -100, ["no_final_signal"]
    return score, reasons


def iter_candidates() -> Iterable[tuple[Path, int, list[str]]]:
    for path in SOURCE_ROOT.rglob("*"):
        try:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED or path.name.startswith("~$"):
                continue
            score, reasons = candidate_score(path)
            if score >= 5:
                yield path, score, reasons
        except (OSError, ValueError):
            continue


def detect_year(path: Path, detected: dict[str, Any]) -> str:
    file_years = YEAR_RE.findall(path.stem)
    if file_years:
        return file_years[-1]
    parts = path.relative_to(SOURCE_ROOT).parts
    for part in reversed(parts[:-1]):
        years = YEAR_RE.findall(part)
        if years:
            return years[-1]
    value = str(detected.get("year") or "")
    return value if YEAR_RE.fullmatch(value) else "unknown"


def plausible_detected_label(value: Any) -> bool:
    text = safe_label(value, "")
    normalized = norm(text)
    return bool(text and 2 <= len(text) <= 100 and normalized not in {
        "nom", "projet", "client", "entreprise", "societe", "raison sociale", "denomination",
    })


def infer_organisme(path: Path, detected: dict[str, Any]) -> tuple[str, str]:
    source_client = path.relative_to(SOURCE_ROOT).parts[0]
    detected_org = detected.get("organisme")
    if plausible_detected_label(detected_org):
        return safe_label(detected_org, source_client), source_client
    # Plusieurs groupes rangent leurs filiales au deuxième niveau.
    parts = path.relative_to(SOURCE_ROOT).parts
    if len(parts) > 2 and re.match(r"^\d+[. _-]", parts[1]):
        subsidiary = re.sub(r"^\d+[. _-]*", "", parts[1]).strip()
        if subsidiary:
            return safe_label(subsidiary, source_client), source_client
    return safe_label(source_client, "Entreprise inconnue"), source_client


def infer_project(path: Path, detected: dict[str, Any], organisme: str, year: str) -> str:
    detected_project = detected.get("project")
    if plausible_detected_label(detected_project):
        return safe_label(detected_project, path.stem)

    value = unicodedata.normalize("NFKC", path.stem).replace("_", " ")
    value = re.sub(re.escape(organisme), " ", value, flags=re.IGNORECASE)
    source_client = path.relative_to(SOURCE_ROOT).parts[0]
    value = re.sub(re.escape(source_client), " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:19|20)\d{2}\b", " ", value)
    value = re.sub(r"\b(?:CIR|CII|DT|VF)\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:dossier|technique|justificatif|version|finale?|regroupe)\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:ed|v)\s*\d+(?:[.,]\d+)*\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b\d{8}\b", " ", value)
    value = re.sub(r"[-–—]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .-_")
    meaningful = [token for token in norm(value).split() if token not in GENERIC_PROJECT_WORDS]
    if not meaningful:
        value = organisme
    return safe_label(value.title(), f"Projet {year}")


def discover(*, reset: bool = False, limit: int | None = None, deep_ocr: bool = False) -> dict[str, Any]:
    if not SOURCE_ROOT.is_dir():
        raise FileNotFoundError(SOURCE_ROOT)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    if reset and DISCOVERY_JSONL.is_file():
        DISCOVERY_JSONL.unlink()

    previous = read_jsonl(DISCOVERY_JSONL)
    done = {str(row.get("source_path")) for row in previous}
    candidates = list(iter_candidates())
    if limit is not None:
        candidates = candidates[: max(0, limit)]
    print(f"Candidates chemin/nom : {len(candidates)} | déjà inspectés : {len(done)}", flush=True)

    for index, (path, path_score, path_reasons) in enumerate(candidates, start=1):
        source_path = str(path)
        if source_path in done:
            continue
        row: dict[str, Any] = {
            "source_path": source_path,
            "source_relative": relative_text(path),
            "file_name": path.name,
            "size_bytes": path.stat().st_size,
            "path_score": path_score,
            "path_reasons": path_reasons,
            "inspected_at": now_iso(),
        }
        try:
            preview = extract_preview(path, deep_scan=False)
            if deep_ocr and preview.get("needs_ocr"):
                preview = extract_preview(path, deep_scan=True)
            classification = classify_cir_document(
                str(preview.get("text") or ""),
                file_name=path.name,
                source_path=relative_text(path),
            )
            detected = classification.get("detected_identity") or {}
            organisme, source_client = infer_organisme(path, detected)
            year = detect_year(path, detected)
            project = infer_project(path, detected, organisme, year)
            project_group_key = key(infer_project(path, {}, organisme, year))
            confirmed = classification.get("classification") == "cir_final_confirmed"
            has_content_cir = any(
                signal.get("label") == "Crédit d'impôt recherche" and signal.get("source") == "contenu"
                for signal in (classification.get("signals") or [])
            )
            probable_strong = (
                classification.get("classification") == "cir_probable"
                and int(classification.get("structural_signals_count") or 0) >= 2
                and float(classification.get("confidence") or 0) >= 0.52
                and path_score >= 7
                and has_content_cir
            )
            accepted = bool((confirmed or probable_strong) and year != "unknown" and not classification.get("draft_signal"))
            row.update({
                "accepted": accepted,
                "classification": classification.get("classification"),
                "confidence": classification.get("confidence"),
                "signals": classification.get("signals") or [],
                "structural_signals_count": classification.get("structural_signals_count"),
                "needs_ocr": preview.get("needs_ocr"),
                "extraction_mode": preview.get("extraction_mode"),
                "preview_chars": preview.get("chars"),
                "preview_excerpt": preview.get("preview_excerpt"),
                "errors": preview.get("errors") or [],
                "detected_identity": detected,
                "organisme": organisme,
                "source_client": source_client,
                "project": project,
                "project_group_key": project_group_key,
                "year": year,
            })
            if accepted:
                row["sha256"] = sha256_file(path)
                row["selection_score"] = round(
                    path_score + float(classification.get("confidence") or 0) * 10 + (2 if path.suffix.lower() == ".pdf" else 1),
                    2,
                )
        except Exception as exc:
            row.update({"accepted": False, "classification": "inspection_error", "errors": [str(exc)]})
        append_jsonl(DISCOVERY_JSONL, row)
        if index % 25 == 0 or row.get("accepted"):
            print(
                f"[{index}/{len(candidates)}] {row.get('classification')} accepted={row.get('accepted')} "
                f"{row['source_relative']}",
                flush=True,
            )

    rows = read_jsonl(DISCOVERY_JSONL)
    accepted_rows = [row for row in rows if row.get("accepted")]

    # Déduplication 1 : contenu strictement identique.
    best_by_hash: dict[str, dict[str, Any]] = {}
    for row in accepted_rows:
        digest = str(row.get("sha256") or "")
        current = best_by_hash.get(digest)
        if not current or float(row.get("selection_score") or 0) > float(current.get("selection_score") or 0):
            best_by_hash[digest] = row

    # Déduplication 2 : une seule version finale par entreprise/projet/année.
    canonical: dict[str, dict[str, Any]] = {}
    for row in best_by_hash.values():
        group = f"{key(row.get('organisme'))}::{row.get('project_group_key') or key(row.get('project'))}::{row.get('year')}"
        current = canonical.get(group)
        if not current or float(row.get("selection_score") or 0) > float(current.get("selection_score") or 0):
            canonical[group] = row

    selected = sorted(
        canonical.values(),
        key=lambda row: (norm(row.get("organisme")), str(row.get("year")), norm(row.get("project"))),
    )
    holdout_keys = {(spec["project_key"], spec["year"]) for spec in DEMO_SPECS}
    for row in selected:
        row["demo_holdout"] = (str(row.get("project_group_key") or key(row.get("project"))), str(row.get("year"))) in holdout_keys and "cevaa" in key(row.get("organisme"))
        row["index_in_chroma"] = not row["demo_holdout"]

    manifest = {
        "ok": True,
        "version": "final_cir_corpus_v1",
        "created_at": now_iso(),
        "source_root": str(SOURCE_ROOT),
        "source_policy": "strict_read_only",
        "source_write_operations": 0,
        "selection_policy": "final technical CIR only; drafts, raw client files, finance, CERFA and admin files excluded",
        "counts": {
            "inspected": len(rows),
            "accepted_before_dedup": len(accepted_rows),
            "unique_hashes": len(best_by_hash),
            "selected_final_cir": len(selected),
            "to_index": sum(1 for row in selected if row["index_in_chroma"]),
            "demo_holdouts": sum(1 for row in selected if row["demo_holdout"]),
        },
        "classification_counts": dict(Counter(str(row.get("classification") or "unknown") for row in rows)),
        "items": selected,
    }
    write_json(MANIFEST_PATH, manifest)
    print(json.dumps({"manifest": str(MANIFEST_PATH), **manifest["counts"]}, ensure_ascii=False, indent=2), flush=True)
    return manifest


def setup_demos() -> dict[str, Any]:
    demos: list[dict[str, Any]] = []
    for spec in DEMO_SPECS:
        target = DEMO_ROOT / spec["slug"]
        input_target = target / "input_documents"
        ground_truth_target = target / "ground_truth_do_not_index"
        cir_source = SOURCE_ROOT / Path(spec["cir_relative"])
        raw_source = SOURCE_ROOT / Path(spec["raw_relative"])
        if not cir_source.is_file() or not raw_source.is_dir():
            raise FileNotFoundError(f"Démonstration incomplète : {spec['title']}")
        input_target.mkdir(parents=True, exist_ok=True)
        ground_truth_target.mkdir(parents=True, exist_ok=True)

        copied_inputs: list[str] = []
        for source in raw_source.rglob("*"):
            if not source.is_file() or source.name.startswith("~$"):
                continue
            relative = source.relative_to(raw_source)
            destination = input_target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied_inputs.append(str(destination))

        cir_destination = ground_truth_target / cir_source.name
        shutil.copy2(cir_source, cir_destination)
        demo_manifest = {
            "ok": True,
            "title": spec["title"],
            "organisme": spec["organisme_hint"],
            "project": spec["project_key"].upper(),
            "year": spec["year"],
            "input_documents": copied_inputs,
            "ground_truth_cir": str(cir_destination),
            "ground_truth_sha256": sha256_file(cir_source),
            "excluded_from_chroma": True,
            "agent_rule": "Use input_documents only. Compare the result to ground_truth_do_not_index after generation.",
            "source_modified": False,
            "created_at": now_iso(),
        }
        write_json(target / "demo_manifest.json", demo_manifest)
        demos.append(demo_manifest)
    report = {"ok": True, "demos": demos, "source_write_operations": 0}
    write_json(DEMO_ROOT / "demo_catalog.json", report)
    return report


def existing_hashes() -> set[str]:
    hashes: set[str] = set()
    if not MEMORY_RUNS.is_dir():
        return hashes
    for path in MEMORY_RUNS.glob("*.run_v2.json"):
        run = read_json(path, {})
        if isinstance(run, dict) and run.get("ok") and run.get("source_hash"):
            hashes.add(str(run["source_hash"]))
    return hashes


def index_manifest(*, max_items: int | None = None) -> dict[str, Any]:
    manifest = read_json(MANIFEST_PATH, None)
    if not isinstance(manifest, dict):
        raise FileNotFoundError(f"Manifeste introuvable : {MANIFEST_PATH}")
    ledger = read_json(INDEX_LEDGER_PATH, {"version": "final_cir_index_v1", "items": {}})
    ledger.setdefault("items", {})
    already_in_memory = existing_hashes()
    items = [row for row in manifest.get("items") or [] if row.get("index_in_chroma")]
    if max_items is not None:
        items = items[: max(0, max_items)]

    for index, row in enumerate(items, start=1):
        digest = str(row.get("sha256") or "")
        previous = ledger["items"].get(digest) or {}
        if previous.get("status") in {"indexed", "already_indexed"}:
            continue
        if digest in already_in_memory:
            ledger["items"][digest] = {
                "status": "already_indexed",
                "source_path": row.get("source_path"),
                "updated_at": now_iso(),
            }
            write_json(INDEX_LEDGER_PATH, ledger)
            continue

        path = Path(str(row.get("source_path") or ""))
        print(
            f"[{index}/{len(items)}] INDEX {row.get('organisme')} | {row.get('project')} | {row.get('year')} | {path.name}",
            flush=True,
        )
        try:
            result = build_cir_final_v2(
                path,
                organisme=safe_label(row.get("organisme"), "Entreprise inconnue"),
                project=safe_label(row.get("project"), path.stem),
                year=str(row.get("year")),
                copy_to_library=True,
                reset_chroma=False,
                vision_mode="text_only",
                formula_mode="off",
                rebuild_catalog=False,
            )
            if not result.get("ok") or int(result.get("cards_count") or 0) <= 0:
                raise RuntimeError("Indexation sans carte exploitable")
            ledger["items"][digest] = {
                "status": "indexed",
                "source_path": str(path),
                "organisme": result.get("organisme"),
                "project": result.get("project"),
                "year": result.get("year"),
                "source_id": result.get("source_id"),
                "chunks_count": result.get("chunks_count"),
                "cards_count": result.get("cards_count"),
                "elapsed_seconds": result.get("elapsed_seconds"),
                "updated_at": now_iso(),
            }
            already_in_memory.add(digest)
        except Exception as exc:
            ledger["items"][digest] = {
                "status": "error",
                "source_path": str(path),
                "error": str(exc),
                "updated_at": now_iso(),
            }
        ledger["updated_at"] = now_iso()
        write_json(INDEX_LEDGER_PATH, ledger)

    print("Reconstruction globale du catalogue, du graphe et de Chroma…", flush=True)
    rebuild = rebuild_global_graph_and_catalog(reset_chroma=True)
    statuses = Counter(str(item.get("status")) for item in ledger["items"].values())
    report = {
        "ok": True,
        "completed_at": now_iso(),
        "statuses": dict(statuses),
        "rebuild": rebuild,
        "demo_projects_excluded": [spec["title"] for spec in DEMO_SPECS],
        "source_write_operations": 0,
    }
    ledger["final_report"] = report
    write_json(INDEX_LEDGER_PATH, ledger)
    return report


def status() -> dict[str, Any]:
    manifest = read_json(MANIFEST_PATH, {})
    ledger = read_json(INDEX_LEDGER_PATH, {})
    return {
        "source_root": str(SOURCE_ROOT),
        "manifest_exists": MANIFEST_PATH.is_file(),
        "manifest_counts": manifest.get("counts") or {},
        "index_statuses": dict(Counter(str(item.get("status")) for item in (ledger.get("items") or {}).values())),
        "demo_catalog_exists": (DEMO_ROOT / "demo_catalog.json").is_file(),
        "source_write_operations": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collecte sûre des CIR finaux et construction Memory V2/Chroma.")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--reset-discovery", action="store_true")
    parser.add_argument("--setup-demos", action="store_true")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-index-items", type=int, default=None)
    parser.add_argument("--deep-ocr", action="store_true")
    args = parser.parse_args()

    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if args.all or args.discover:
        discover(reset=args.reset_discovery, limit=args.limit, deep_ocr=args.deep_ocr)
    if args.all or args.setup_demos:
        print(json.dumps(setup_demos(), ensure_ascii=False, indent=2))
    if args.all or args.index:
        print(json.dumps(index_manifest(max_items=args.max_index_items), ensure_ascii=False, indent=2))
    if not any((args.all, args.discover, args.setup_demos, args.index, args.status)):
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
