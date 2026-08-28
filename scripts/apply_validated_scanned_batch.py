from __future__ import annotations

import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(r"C:\EnnoSmart")

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend_api"))

import automate_cir_memory as a


COVERAGE = Path(
    r"C:\EnnoSmartData\power_automate_import"
    r"\automation\coverage\coverage_scanned.csv"
)

LEDGER = Path(
    r"C:\EnnoSmartData\power_automate_import"
    r"\automation\validated_scanned_batch_ledger.json"
)

UNSAFE_MARKERS = (
    "bon de livraison",
    "facture",
    "risque corplaux",
    "risque corpalux",
)


def norm(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(
        c for c in text
        if not unicodedata.combining(c)
    )
    text = text.lower().replace("\\", "/")
    return re.sub(r"\s+", " ", text).strip()


def basename(value):
    return norm(str(value or "").replace("\\", "/").split("/")[-1])


def load_ledger():
    if not LEDGER.is_file():
        return {
            "version": "validated_scanned_batch_v1",
            "items": {},
        }

    try:
        data = json.loads(
            LEDGER.read_text(encoding="utf-8")
        )
        if isinstance(data, dict):
            data.setdefault("items", {})
            return data
    except Exception:
        pass

    return {
        "version": "validated_scanned_batch_v1",
        "items": {},
    }


def save_ledger(data):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
        ) + "\n",
        encoding="utf-8",
    )
    tmp.replace(LEDGER)


def find_staged_item(file_name, expected_scope):
    items_root = a.AUDIT_ROOT / "items"

    exact = []
    preferred = []

    for item_path in items_root.glob("*/*.json"):
        try:
            item = json.loads(
                item_path.read_text(encoding="utf-8")
            )
        except Exception:
            continue

        if norm(item.get("name")) != norm(file_name):
            continue

        staged_raw = str(
            item.get("staged_path") or ""
        ).strip()

        if not staged_raw:
            continue

        staged = Path(staged_raw)

        if not staged.is_file():
            continue

        scan_id = item_path.parent.name

        record = {
            "item_path": item_path,
            "item": item,
            "staged": staged,
            "scan_id": scan_id,
        }

        exact.append(record)

        context = norm(
            " ".join(
                str(item.get(key) or "")
                for key in (
                    "source_scope",
                    "source_path",
                    "path",
                    "relative_path",
                    "original_path",
                )
            )
        )

        wanted_scope = norm(expected_scope)

        if wanted_scope and (
            wanted_scope in context
            or context in wanted_scope
        ):
            preferred.append(record)

    candidates = preferred or exact

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["item_path"].stat().st_mtime,
        reverse=True,
    )

    return candidates[0]


if not COVERAGE.is_file():
    raise SystemExit(
        f"coverage_scanned.csv introuvable : {COVERAGE}"
    )


with COVERAGE.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as handle:
    rows = list(csv.DictReader(handle))


scanned = [
    row for row in rows
    if norm(row.get("status")) == "scanned"
]


safe = []
excluded = []


for row in scanned:

    file_name = str(row.get("file") or "").strip()

    matched = str(
        row.get("matched_local_copy") or ""
    ).strip()

    context = norm(
        f"{file_name} {matched} "
        f"{row.get('project')} "
        f"{row.get('subproject')}"
    )

    reason = ""

    # ---------------------------------------------
    # 1. Documents administratifs / non-CIR
    # ---------------------------------------------
    marker = next(
        (
            value for value in UNSAFE_MARKERS
            if value in context
        ),
        None,
    )

    if marker:
        reason = f"document exclu : {marker}"

    # ---------------------------------------------
    # 2. Fichier attendu != fichier réellement copié
    # ---------------------------------------------
    elif matched and basename(file_name) != basename(matched):
        reason = (
            "mismatch fichier attendu / copie locale : "
            f"{file_name} <> "
            f"{matched.replace(chr(92), '/').split('/')[-1]}"
        )

    if reason:
        excluded.append((row, reason))
        continue

    # ---------------------------------------------
    # Correction ancienne structure EVOSYS
    # ---------------------------------------------
    if (
        norm(row.get("organisme")) == "scalian"
        and norm(row.get("project")) in {
            "cir-2016",
            "cir-2017",
        }
        and "/evosys/" in (
            "/" + norm(row.get("scope")) + "/"
        )
    ):
        row = dict(row)
        row["project"] = "EVOSYS"
        row["subproject"] = ""

    safe.append(row)


print("=" * 100)
print("LOT SCANNE VALIDE - MEMORY V2")
print("=" * 100)

print()
print("Scannes bruts :", len(scanned))
print("Retenus       :", len(safe))
print("Exclus        :", len(excluded))

print()
print("DOCUMENTS EXCLUS :")

for row, reason in excluded:
    print(
        "EXCLUDE ->",
        row.get("organisme"),
        "/",
        row.get("project"),
        "/",
        row.get("year"),
        "->",
        row.get("file"),
    )
    print("          ", reason)


print()
print("=" * 100)
print("DEBUT INDEXATION")
print("=" * 100)


ledger = load_ledger()

skipped = []
built = []
pending = []
errors = []


for index, row in enumerate(safe, start=1):

    identity = {
        "organisme": str(
            row.get("organisme") or ""
        ).strip(),
        "project": str(
            row.get("project") or ""
        ).strip(),
        "subproject": str(
            row.get("subproject") or ""
        ).strip(),
        "year": str(
            row.get("year") or ""
        ).strip(),
    }

    file_name = str(
        row.get("file") or ""
    ).strip()

    print()
    print("-" * 100)
    print(
        f"[{index}/{len(safe)}] "
        + " / ".join(
            x for x in (
                identity["organisme"],
                identity["project"],
                identity["subproject"],
                identity["year"],
            )
            if x
        )
    )
    print(file_name)

    found = find_staged_item(
        file_name,
        row.get("scope"),
    )

    if not found:
        message = (
            f"Copie locale staging introuvable : "
            f"{file_name}"
        )

        print("[ERROR]", message)

        errors.append({
            "identity": identity,
            "file": file_name,
            "error": message,
        })
        continue

    item = found["item"]
    staged = found["staged"]
    scan_id = found["scan_id"]

    # ---------------------------------------------
    # Sécurité OneDrive
    # ---------------------------------------------
    try:
        staged.resolve().relative_to(
            a.SOURCE_ROOT.resolve()
        )

        message = (
            "Le chemin staging est dans OneDrive. "
            "Refus immédiat."
        )

        print("[SECURITY ERROR]", message)

        errors.append({
            "identity": identity,
            "file": file_name,
            "error": message,
        })
        continue

    except ValueError:
        pass

    digest = str(
        item.get("sha256") or ""
    ).strip().lower()

    if not digest:
        print("[ERROR] SHA256 absent")
        errors.append({
            "identity": identity,
            "file": file_name,
            "error": "SHA256 absent",
        })
        continue

    previous = (
        ledger.get("items", {}).get(digest)
        or {}
    )

    conflict = a.memory_identity_conflict(
        digest=digest,
        organisme=identity["organisme"],
        project=identity["project"],
        subproject=identity["subproject"],
        year=identity["year"],
    )

    # ---------------------------------------------
    # Reprise après interruption avant Chroma
    # ---------------------------------------------
    if (
        conflict == "same_hash"
        and previous.get("status")
        == "artifacts_built_pending_global_chroma"
    ):
        run = a._memory_run_by_hash(digest)

        if run:
            print(
                "[RESUME] Artefacts déjà construits, "
                "en attente de Chroma."
            )

            pending.append({
                "identity": identity,
                "digest": digest,
                "run": run,
                "item": item,
                "scan_id": scan_id,
                "item_id": str(
                    item.get("external_id") or ""
                ),
            })

            continue

    # ---------------------------------------------
    # Déjà réellement traité
    # ---------------------------------------------
    if conflict == "same_hash":

        print(
            "[SKIP] Déjà présent dans Memory V2."
        )

        skipped.append({
            "identity": identity,
            "file": file_name,
            "digest": digest,
        })

        ledger["items"][digest] = {
            "status": "already_in_memory",
            "identity": identity,
            "file": file_name,
        }

        save_ledger(ledger)
        continue

    # ---------------------------------------------
    # Conflit de version
    # ---------------------------------------------
    if conflict == "same_identity_other_version":

        message = (
            "Une autre version existe déjà "
            "pour cette identité."
        )

        print("[BLOCKED]", message)

        errors.append({
            "identity": identity,
            "file": file_name,
            "error": message,
        })

        ledger["items"][digest] = {
            "status": "blocked_conflict",
            "identity": identity,
            "file": file_name,
        }

        save_ledger(ledger)
        continue

    # ---------------------------------------------
    # Nouveau CIR
    # ---------------------------------------------
    print("[NEW] Construction Memory V2...")

    try:
        result = a.build_uploaded_cir(
            staged,
            organisme=identity["organisme"],
            project=identity["project"],
            subproject=identity["subproject"],
            year=identity["year"],
            vision_mode="text_only",
            formula_mode="off",
            rebuild_catalog=False,
            reset_chroma=False,
        )

        if not result.get("ok"):
            raise RuntimeError(
                "build_uploaded_cir : ok=false"
            )

        cards = int(
            result.get("cards_count") or 0
        )

        if cards <= 0:
            raise RuntimeError(
                "Aucune card générée"
            )

        print(
            "[OK]",
            "chunks=",
            result.get("chunks_count"),
            "cards=",
            cards,
        )

        record = {
            "identity": identity,
            "digest": digest,
            "run": result,
            "item": item,
            "scan_id": scan_id,
            "item_id": str(
                item.get("external_id") or ""
            ),
        }

        built.append(record)
        pending.append(record)

        ledger["items"][digest] = {
            "status":
                "artifacts_built_pending_global_chroma",
            "identity": identity,
            "file": file_name,
            "source_id": result.get("source_id"),
            "chunks_count":
                result.get("chunks_count"),
            "cards_count":
                result.get("cards_count"),
        }

        save_ledger(ledger)

    except Exception as exc:

        print("[ERROR BUILD]", exc)

        errors.append({
            "identity": identity,
            "file": file_name,
            "error": str(exc),
        })

        ledger["items"][digest] = {
            "status": "error",
            "identity": identity,
            "file": file_name,
            "error": str(exc),
        }

        save_ledger(ledger)


print()
print("=" * 100)
print("DECISION CHROMA")
print("=" * 100)


if not pending:

    print(
        "[SKIP CHROMA] Aucun nouvel artefact "
        "à intégrer."
    )

else:

    print(
        f"{len(pending)} projet(s) "
        "à intégrer dans Chroma."
    )

    print(
        "Reconstruction globale isolée..."
    )

    rebuild = (
        a._rebuild_global_chroma_recoverably()
    )

    if not rebuild.get("ok"):
        raise RuntimeError(
            "Reconstruction globale Chroma échouée."
        )

    print()
    print("[OK] CHROMA RECONSTRUIT")
    print(
        "Vecteurs :",
        rebuild.get("chunks_count"),
    )

    for record in pending:

        digest = record["digest"]

        ledger["items"].setdefault(
            digest,
            {},
        )

        ledger["items"][digest]["status"] = (
            "indexed"
        )

        try:
            if (
                record["scan_id"]
                and record["item_id"]
            ):
                a.mark_audit_item_indexed(
                    record["scan_id"],
                    record["item_id"],
                    result=record["run"],
                    identity=record["identity"],
                )

        except Exception as exc:
            print(
                "[WARN] Audit non marqué :",
                exc,
            )

    save_ledger(ledger)


print()
print("=" * 100)
print("RESUME FINAL")
print("=" * 100)

print("Scannes bruts          :", len(scanned))
print("Exclus sécurité        :", len(excluded))
print("Lot retenu             :", len(safe))
print("Déjà indexés / SKIP    :", len(skipped))
print("Nouveaux construits    :", len(built))
print("Repris avant Chroma    :", len(pending) - len(built))
print("Erreurs / conflits     :", len(errors))


if errors:
    print()
    print("ERREURS :")

    for error in errors:
        identity = error["identity"]

        print(
            "ERROR ->",
            identity["organisme"],
            "/",
            identity["project"],
            "/",
            identity["subproject"],
            "/",
            identity["year"],
            "->",
            error["error"],
        )


print()
print("OneDrive modifié : NON")
print(
    "Collection : ennosmart_memory_v2_global"
)
print(
    "Ledger :",
    LEDGER,
)
