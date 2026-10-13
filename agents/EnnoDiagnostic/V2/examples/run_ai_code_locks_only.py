from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

ROOT = Path(r"C:\EnnoSmart")
DOCX_PATH = ROOT / "AI_CODE_CIR_FINAL.docx"
OUTPUT_PATH = (
    ROOT / "agents" / "EnnoDiagnostic" / "V2" / "examples"
    / "ai_code_locks_only_result.json"
)

for p in [
    ROOT,
    ROOT / "agents" / "EnnoDiagnostic" / "V2",
]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.chunking import build_chunks
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import PipelineConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.consolidation import GlobalLockConsolidator
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.lock_family_synthesis import LockFamilySynthesizer
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.qualification import LockQualifier
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.runtime_adapters import EnnoSmartLLMAdapter
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.semantic_extractor import SemanticExtractor


def banner(title: str):
    print("\n" + "=" * 96)
    print(title)
    print("=" * 96)


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {k: jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if hasattr(value, "__dict__"):
        return {
            str(k): jsonable(v)
            for k, v in vars(value).items()
            if not str(k).startswith("_")
        }
    return str(value)


def extract_docx_text(path: Path) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    doc = Document(str(path))
    blocks = []

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            p = Paragraph(child, doc)
            text = str(p.text or "").strip()
            if text:
                blocks.append(text)
        elif isinstance(child, CT_Tbl):
            table = Table(child, doc)
            for row in table.rows:
                values = []
                seen = set()
                for cell in row.cells:
                    value = " ".join(str(cell.text or "").split()).strip()
                    if value and value not in seen:
                        seen.add(value)
                        values.append(value)
                if values:
                    blocks.append(" | ".join(values))

    return "\n\n".join(blocks).strip()


def llm(name: str, tokens: int):
    return EnnoSmartLLMAdapter(
        request_name=name,
        temperature=0.0,
        max_output_tokens=tokens,
        retries=2,
    )


def main():
    if not DOCX_PATH.exists():
        raise FileNotFoundError(DOCX_PATH)

    text = extract_docx_text(DOCX_PATH)
    config = PipelineConfig()

    document = DocumentInput(
        document_id="ai_code_locks_only",
        name=DOCX_PATH.name,
        text=text,
        declared_mode="raw",
        metadata={"source_path": str(DOCX_PATH)},
    )

    banner("1/5 - CHUNKING")
    chunks = build_chunks(document, config.chunk)
    print("CHUNKS :", len(chunks))

    banner("2/5 - EXTRACTION")
    extractions = SemanticExtractor(
        llm("ennodiagnostic:v2:ai_code:locks_only:semantic", 4500)
    ).extract(chunks)

    raw_locks = [x for x in extractions if x.kind == "lock"]
    print("VERROUS BRUTS :", len(raw_locks))

    raw_roles = Counter(
        str((x.metadata or {}).get("source_role") or "ABSENT")
        for x in raw_locks
    )
    print("SOURCE_ROLE - VERROUS BRUTS :")
    for role, count in sorted(raw_roles.items()):
        print(f"  {role:<18}: {count}")

    banner("3/5 - CONSOLIDATION")
    entities = GlobalLockConsolidator(
        llm("ennodiagnostic:v2:ai_code:locks_only:consolidation", 6000)
    ).consolidate(extractions)

    locks = [x for x in entities if x.kind == "lock"]
    print("VERROUS CONSOLIDES :", len(locks))

    entity_roles = Counter()
    for entity in locks:
        md = entity.metadata or {}
        roles = md.get("source_roles") or []
        if md.get("has_project_evidence") is True:
            entity_roles["has_project_evidence=True"] += 1
        elif roles == ["literature"] or set(roles) == {"literature"}:
            entity_roles["literature_only_explicit"] += 1
        else:
            entity_roles["ambiguous_or_unknown"] += 1

    print("PROVENANCE - VERROUS CONSOLIDES :")
    for key, count in entity_roles.items():
        print(f"  {key:<30}: {count}")

    banner("4/5 - QUALIFICATION")
    locks = LockQualifier(
        llm("ennodiagnostic:v2:ai_code:locks_only:qualification", 9000)
    ).qualify(locks, chunks=chunks)

    statuses = Counter(
        str((x.metadata or {}).get("qualification_status") or "NOT_EVALUATED")
        for x in locks
    )
    for status, count in statuses.items():
        print(f"  {status:<24}: {count}")

    banner("5/5 - FAMILLES PRINCIPALES")
    families = LockFamilySynthesizer(
        llm("ennodiagnostic:v2:ai_code:locks_only:families", 5000)
    ).synthesize(locks)

    print("FAMILLES PRINCIPALES :", len(families.families))
    print("LITTERATURE UNIQUEMENT :", len(families.literature_only_entity_ids))
    print("NON ASSIGNES / AMBIGUS / AUTRES :", len(families.unassigned_entity_ids))

    by_id = {x.entity_id: x for x in locks}

    for i, family in enumerate(families.families, 1):
        print("\n" + "-" * 96)
        print(f"FAMILLE {i} : {family.title}")
        print("STATUT :", family.status)
        print("SYNTHESE :", family.statement)
        print("SOUS-VERROUS :", len(family.member_entity_ids))
        for entity_id in family.member_entity_ids:
            entity = by_id.get(entity_id)
            if entity:
                md = entity.metadata or {}
                print(
                    "  -",
                    md.get("qualification_status"),
                    "|",
                    entity.statement,
                )

    payload = {
        "source_file": str(DOCX_PATH),
        "chunks": jsonable(chunks),
        "raw_lock_extractions": jsonable(raw_locks),
        "qualified_lock_entities": jsonable(locks),
        "lock_families": jsonable(families),
        "summary": {
            "chunks": len(chunks),
            "raw_locks": len(raw_locks),
            "raw_source_roles": dict(raw_roles),
            "consolidated_locks": len(locks),
            "qualification_counts": dict(statuses),
            "main_families": len(families.families),
            "literature_only": len(families.literature_only_entity_ids),
            "unassigned": len(families.unassigned_entity_ids),
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    banner("TERMINE - AUCUN SCORE CALCULE")
    print("JSON :", OUTPUT_PATH)


if __name__ == "__main__":
    main()
