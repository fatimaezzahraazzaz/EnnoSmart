# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import py_compile
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ENV_VALUES = {
    "ENNOSMART_PHASE5_REUSE_VALIDATED_DRAFT": "0",
    "ENNOSMART_PHASE5_ALLOW_PAID_REGEN": "1",
    "ENNOSMART_GPT41_MIN_GAP_SECONDS": "45",
    "ENNOSMART_OPENAI_429_MAX_RETRIES": "5",
    "ENNOSMART_OPENAI_429_RETRY_BUFFER_SECONDS": "2.0",
    "ENNOSMART_PHASE5_MAX_SECTION_ATTEMPTS": "2",
    "ENNOSMART_PHASE5_MAX_SECTION_RETRIES": "1",
    "ENNOSMART_PHASE5_MAX_REPAIR_ATTEMPTS": "1",
}

OUTPUT_PATTERNS = (
    "state_of_art_draft*",
    "*candidate*.json",
    "*candidate*.md",
    "*progress*.json",
    "*progress*.md",
    "*completion*.json",
    "*completion*.md",
)

PRESERVE_NAMES = {
    "unified_writer_blueprint_used.json",
    "normalized_evidence_units.json",
}


def slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    for source, target in {
        "à":"a","á":"a","â":"a","ä":"a","ã":"a","ç":"c",
        "é":"e","è":"e","ê":"e","ë":"e","î":"i","ï":"i",
        "ô":"o","ö":"o","ù":"u","û":"u","ü":"u","’":"_",
        "'":"_","-":"_"," ":"_",
    }.items():
        text = text.replace(source, target)
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_") or "unknown"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def set_env_values(path: Path, values: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    output = []
    seen = set()

    for line in lines:
        match = re.match(
            r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=",
            line,
        )
        if match and match.group(1) in values:
            key = match.group(1)
            if key not in seen:
                output.append(f"{key}={values[key]}")
                seen.add(key)
            continue
        output.append(line)

    for key, value in values.items():
        if key not in seen:
            output.append(f"{key}={value}")

    path.write_text(
        "\n".join(output).rstrip() + "\n",
        encoding="utf-8",
    )


def validate_snapshot(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError("Snapshot consultant invalide.")
    if payload.get("validated_by_consultant") is not True:
        raise RuntimeError(
            "Le snapshot n'est pas validé par le consultant."
        )
    sections = payload.get("sections")
    if not isinstance(sections, list) or len(sections) != 7:
        raise RuntimeError(
            f"Le snapshot doit contenir 7 sections, trouvé: "
            f"{len(sections) if isinstance(sections, list) else 0}"
        )
    for index, section in enumerate(sections, 1):
        if (
            not isinstance(section, dict)
            or not str(section.get("section_id") or "").strip()
            or not str(section.get("title") or "").strip()
        ):
            raise RuntimeError(
                f"Section consultant {index} incomplète."
            )
    return payload


def evidence_count(path: Path) -> int:
    payload = read_json(path)
    if isinstance(payload, list):
        return len([row for row in payload if isinstance(row, dict)])
    if isinstance(payload, dict):
        for key in ("items", "evidence_units", "normalized_evidence_units"):
            value = payload.get(key)
            if isinstance(value, list):
                return len([row for row in value if isinstance(row, dict)])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\EnnoSmart")
    parser.add_argument("--organisme", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--year", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    base = (
        root / "storage" / "organismes" / slug(args.organisme)
        / "projects" / slug(args.project) / "years" / str(args.year)
        / "ennoscholar" / "state_of_art_payload"
    )
    writer_dir = base / "phase_5_state_of_art_writer"
    snapshot_path = base / "consultant_plan_snapshot.json"
    phase47_path = (
        base / "phase_4_7_scientific_narrative"
        / "scientific_narrative_payload.json"
    )
    blueprint_path = writer_dir / "unified_writer_blueprint_used.json"
    evidence_path = writer_dir / "normalized_evidence_units.json"
    writer_path = (
        root / "agents" / "EnnoScholar" / "state_of_art"
        / "phase_5_state_of_art_writer_service.py"
    )

    required = (
        snapshot_path,
        phase47_path,
        blueprint_path,
        evidence_path,
        writer_path,
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(
            "PREPARE_PAID_REGEN_BLOCKED — fichiers absents:\n- "
            + "\n- ".join(missing)
        )

    snapshot = validate_snapshot(snapshot_path)
    count = evidence_count(evidence_path)
    if count < 1:
        raise SystemExit("Aucune unité de preuve normalisée.")

    py_compile.compile(str(writer_path), doraise=True)
    writer_text = writer_path.read_text(encoding="utf-8")
    markers = {
        "stability_v15":
            "BEGIN ENNOSMART_PHASE5_STABILITY_GATE_V1_5"
            in writer_text,
        "quality_v16":
            "BEGIN ENNOSMART_PHASE5_PUBLIC_MARKDOWN_QUALITY_GATE_V1_6"
            in writer_text,
        "paid_once_v17":
            "BEGIN ENNOSMART_PHASE5_PAID_REGEN_ONCE_V1_7"
            in writer_text,
        "quality_recovery":
            "BEGIN ENNOSMART_PHASE5_QUALITY_RECOVERY_V1"
            in writer_text,
    }
    failed = [name for name, ok in markers.items() if not ok]
    if failed:
        raise SystemExit(
            "PREPARE_PAID_REGEN_BLOCKED — correctifs absents: "
            + ", ".join(failed)
        )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = (
        root / "_backups"
        / f"phase5_before_paid_regen_v1_7_{stamp}"
    )
    backup_writer_dir = backup_dir / "phase_5_state_of_art_writer"
    backup_writer_dir.mkdir(parents=True, exist_ok=True)

    archived = []
    removed = []
    if writer_dir.exists():
        for path in writer_dir.iterdir():
            if not path.is_file():
                continue
            if path.name in PRESERVE_NAMES:
                continue
            if not any(path.match(pattern) for pattern in OUTPUT_PATTERNS):
                continue

            target = backup_writer_dir / path.name
            shutil.copy2(path, target)
            archived.append(str(path))
            path.unlink()
            removed.append(str(path))

    env_path = root / ".env"
    if env_path.exists():
        shutil.copy2(env_path, backup_dir / ".env")
    set_env_values(env_path, ENV_VALUES)

    arm_path = base / "phase5_paid_regen_once_v1_7.arm.json"
    arm = {
        "armed": True,
        "armed_at": datetime.now().isoformat(),
        "organisme": args.organisme,
        "project": args.project,
        "year": str(args.year),
        "consultant_snapshot_sha256":
            snapshot.get("snapshot_sha256"),
        "sections": len(snapshot["sections"]),
        "evidence_units": count,
        "backup_dir": str(backup_dir),
        "archived_outputs": archived,
        "one_paid_phase5_run_only": True,
    }
    arm_path.write_text(
        json.dumps(arm, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=" * 116)
    print("PHASE5_PAID_REGEN_ONCE_V1_7_ARMED")
    print(f"Plan consultant : {len(snapshot['sections'])} sections")
    print(f"Evidence units  : {count}")
    print(f"Sorties archivées: {len(archived)}")
    for path in archived:
        print(f"  - {path}")
    print(f"Backup          : {backup_dir}")
    print(f"Arm file        : {arm_path}")
    print("Réutilisation   : désactivée")
    print("Régénération    : autorisée une seule fois")
    print("LLM calls ici   : 0")
    print("=" * 116)
    print(
        "Redémarre maintenant le backend puis écris dans le chat: "
        "relance la rédaction"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
