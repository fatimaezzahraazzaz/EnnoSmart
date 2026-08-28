# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

BACKUP_SUFFIX = ".before-ai-click-fix"


def backup(path: Path) -> Path:
    target = path.with_name(path.name + BACKUP_SUFFIX)
    if not target.exists():
        shutil.copy2(path, target)
    return target


def replace_function(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"Début de bloc introuvable : {start_marker}")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"Fin de bloc introuvable : {end_marker}")
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def patch_source_documents_dialog(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    if "parent_passage_id?: string | null" not in text:
        needle = "  passage_id?: string | null\n"
        if needle not in text:
            raise RuntimeError("Champ passage_id introuvable dans SourceEvidence.")
        text = text.replace(
            needle,
            needle
            + "  parent_passage_id?: string | null\n"
            + "  original_passage_id?: string | null\n",
            1,
        )

    virtual_function = r"""function virtualDocumentFromEvidence(
  evidence: SourceEvidence,
): DbSourceDocument | null {
  const sourcePath = String(
    evidence.source_path ||
      (evidence.metadata && typeof evidence.metadata === "object"
        ? evidence.metadata.source_path || evidence.metadata.path
        : "") ||
      "",
  ).trim()

  const rawName = evidenceDocumentName(evidence) || sourcePath
  const filename = cleanDisplayDocumentName(rawName)
  const excerpt = evidenceText(evidence)

  const passageIdentity = String(
    evidence.parent_passage_id ||
      evidence.original_passage_id ||
      evidence.passage_id ||
      evidence.rag_chunk_id ||
      evidence.evidence_id ||
      "",
  ).trim()

  const identity =
    sourcePath ||
    filename ||
    passageIdentity ||
    excerpt.slice(0, 240)

  if (!identity) return null

  return {
    id: stableNegativeId(identity),
    project_id: 0,
    filename: filename || "Document source",
    stored_filename: filename || null,
    content_type: inferVirtualContentType(sourcePath || filename),
    document_type: "source_documentaire",
    upload_status: "resolved_from_evidence",
    storage_mode: sourcePath
      ? "source_path"
      : passageIdentity
        ? "passage_resolution"
        : "excerpt_resolution",
    has_file_data: false,
  }
}"""

    text = replace_function(
        text,
        "function virtualDocumentFromEvidence(",
        "function findBestDocument(",
        virtual_function,
    )

    old_document_id = "              document_id: Number(document.id) > 0 ? document.id : null,"
    new_document_id = (
        "              document_id:\n"
        "                Number(document.id) > 0\n"
        "                  ? document.id\n"
        "                  : evidenceDocumentId(selectedEvidence),"
    )
    if old_document_id in text:
        text = text.replace(old_document_id, new_document_id, 1)
    elif "evidenceDocumentId(selectedEvidence)" not in text:
        raise RuntimeError("Construction document_id du preview introuvable.")

    old_passage = (
        "              passage_id:\n"
        "                selectedEvidence.passage_id ||\n"
        "                selectedEvidence.rag_chunk_id ||\n"
        "                selectedEvidence.evidence_id ||\n"
        "                null,"
    )
    new_passage = (
        "              passage_id:\n"
        "                selectedEvidence.parent_passage_id ||\n"
        "                selectedEvidence.original_passage_id ||\n"
        "                selectedEvidence.passage_id ||\n"
        "                selectedEvidence.rag_chunk_id ||\n"
        "                selectedEvidence.evidence_id ||\n"
        "                null,"
    )
    if old_passage in text:
        text = text.replace(old_passage, new_passage, 1)
    elif "selectedEvidence.parent_passage_id" not in text:
        raise RuntimeError("Construction passage_id du preview introuvable.")

    if text == original:
        return False

    backup(path)
    path.write_text(text, encoding="utf-8")
    return True


RAG_HELPER = r"""
def _positive_int_or_none(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _rag_chunk_text(chunk: Dict[str, Any]) -> str:
    if not isinstance(chunk, dict):
        return ""
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    raw_item = chunk.get("raw_item") if isinstance(chunk.get("raw_item"), dict) else {}
    return clean_excerpt(
        chunk.get("source_text")
        or chunk.get("source_text_original")
        or chunk.get("excerpt")
        or raw_item.get("source_text")
        or raw_item.get("text")
        or chunk.get("text")
        or metadata.get("source_text")
        or ""
    )


def _rag_chunk_identifiers(chunk: Dict[str, Any]) -> set[str]:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    raw_item = chunk.get("raw_item") if isinstance(chunk.get("raw_item"), dict) else {}

    values = [
        chunk.get("id"),
        chunk.get("passage_id"),
        chunk.get("rag_chunk_id"),
        metadata.get("rag_chunk_id"),
        metadata.get("passage_id"),
        metadata.get("original_passage_id"),
        metadata.get("parent_rag_chunk_id"),
        raw_item.get("passage_id"),
        raw_item.get("id"),
    ]
    return {
        str(value).strip()
        for value in values
        if value not in (None, "")
    }


def _find_rag_trace_for_request(
    project: Any,
    payload: SourceHighlightRequest,
) -> Dict[str, Any]:
    try:
        project_store = get_project_store(project)
    except Exception:
        return {}

    candidates = [
        Path(project_store.rag_dir) / "chunks.json",
        Path(project_store.project_dir) / "rag" / "chunks.json",
    ]

    rag_path = next(
        (path for path in candidates if path.exists() and path.is_file()),
        None,
    )
    if rag_path is None:
        return {}

    try:
        data = json.loads(rag_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}

    if isinstance(data, dict):
        chunks = data.get("chunks") or data.get("items") or []
    elif isinstance(data, list):
        chunks = data
    else:
        chunks = []

    wanted_id = str(payload.passage_id or "").strip()
    wanted_excerpt = normalize_text(clean_excerpt(payload.excerpt))

    best: Dict[str, Any] = {}
    best_score = -1

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue

        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        raw_item = chunk.get("raw_item") if isinstance(chunk.get("raw_item"), dict) else {}

        score = 0

        if wanted_id:
            identifiers = _rag_chunk_identifiers(chunk)
            if wanted_id in identifiers:
                score = 10_000

        if wanted_excerpt:
            chunk_text = normalize_text(_rag_chunk_text(chunk))
            if chunk_text:
                if wanted_excerpt == chunk_text:
                    score = max(score, 9_000)
                elif wanted_excerpt in chunk_text or chunk_text in wanted_excerpt:
                    common_length = min(len(wanted_excerpt), len(chunk_text))
                    if common_length >= 60:
                        score = max(score, 7_000 + min(common_length, 1500))
                else:
                    probe = wanted_excerpt[:220]
                    if len(probe) >= 80 and probe in chunk_text:
                        score = max(score, 6_000 + len(probe))

        if metadata.get("is_supporting_passage") is True:
            score += 100

        if score > best_score:
            best_score = score
            best = {
                "metadata": metadata,
                "raw_item": raw_item,
            }

    if best_score < 6_000:
        return {}

    metadata = best.get("metadata") or {}
    raw_item = best.get("raw_item") or {}

    def first(*values: Any) -> Any:
        for value in values:
            if value not in (None, ""):
                return value
        return None

    return {
        "document_id": first(
            metadata.get("document_id"),
            raw_item.get("document_id"),
            raw_item.get("doc_id"),
            raw_item.get("file_id"),
        ),
        "document": first(
            metadata.get("document"),
            raw_item.get("document"),
            raw_item.get("file_name"),
            raw_item.get("filename"),
        ),
        "source_path": first(
            metadata.get("source_path"),
            raw_item.get("source_path"),
            raw_item.get("path"),
            raw_item.get("file_path"),
        ),
        "page_number": first(
            metadata.get("page_number"),
            raw_item.get("page_number"),
            raw_item.get("page"),
        ),
        "paragraph_index": first(
            metadata.get("paragraph_index"),
            raw_item.get("paragraph_index"),
            raw_item.get("paragraph"),
        ),
        "char_start": first(
            metadata.get("char_start"),
            raw_item.get("char_start"),
            raw_item.get("start_char"),
            raw_item.get("start"),
        ),
        "char_end": first(
            metadata.get("char_end"),
            raw_item.get("char_end"),
            raw_item.get("end_char"),
            raw_item.get("end"),
        ),
    }


def _enrich_payload_from_rag_trace(
    project: Any,
    payload: SourceHighlightRequest,
) -> None:
    trace = _find_rag_trace_for_request(project, payload)
    if not trace:
        return

    if not payload.source_path and trace.get("source_path"):
        payload.source_path = str(trace["source_path"])

    document_name = str(trace.get("document") or "").strip()
    if not payload.source_name and document_name:
        payload.source_name = document_name
    if not payload.document_name and document_name:
        payload.document_name = document_name

    if not payload.document_id:
        payload.document_id = _positive_int_or_none(trace.get("document_id"))

    if payload.page_number is None:
        payload.page_number = _positive_int_or_none(trace.get("page_number"))

    for attr in ("paragraph_index", "char_start", "char_end"):
        if getattr(payload, attr) is None:
            try:
                value = trace.get(attr)
                if value not in (None, ""):
                    setattr(payload, attr, int(value))
            except Exception:
                pass
"""


def patch_source_highlight(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    if "def _find_rag_trace_for_request(" not in text:
        marker = "\ndef resolve_document_path(\n"
        pos = text.find(marker)
        if pos < 0:
            raise RuntimeError("resolve_document_path introuvable.")
        text = text[:pos] + "\n" + RAG_HELPER.strip() + "\n\n" + text[pos + 1:]

    signature_pattern = re.compile(
        r"(def resolve_document_path\(\n"
        r"    \*,\n"
        r"    db: Session,\n"
        r"    project: Any,\n"
        r"    payload: SourceHighlightRequest,\n"
        r"\) -> Path:\n)"
    )
    match = signature_pattern.search(text)
    if not match:
        raise RuntimeError("Signature resolve_document_path inattendue.")

    function_start = match.end()
    nearby = text[function_start:function_start + 250]
    if "_enrich_payload_from_rag_trace(project, payload)" not in nearby:
        text = (
            text[:function_start]
            + "    _enrich_payload_from_rag_trace(project, payload)\n\n"
            + text[function_start:]
        )

    old_block = (
        "    if payload.document_id:\n"
        "        document = _query_project_document(db, project.id, payload.document_id)\n"
        "        if document is None:\n"
        "            raise HTTPException(status_code=404, detail=\"Document PostgreSQL introuvable pour ce projet.\")\n"
        "        materialized = _materialize_db_document(document, project.id)\n"
        "        if materialized:\n"
        "            return materialized\n"
    )
    new_block = (
        "    if payload.document_id:\n"
        "        document = _query_project_document(db, project.id, payload.document_id)\n"
        "        if document is not None:\n"
        "            materialized = _materialize_db_document(document, project.id)\n"
        "            if materialized:\n"
        "                return materialized\n"
    )
    if old_block in text:
        text = text.replace(old_block, new_block, 1)
    elif "Document PostgreSQL introuvable pour ce projet." in text:
        raise RuntimeError(
            "Le bloc document_id existe mais sa forme a changé ; correction manuelle requise."
        )

    if text == original:
        return False

    backup(path)
    path.write_text(text, encoding="utf-8")
    return True


def patch_diagnosis_page(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    control_pos = text.find('value="controle-ia"')
    if control_pos < 0:
        control_pos = text.find("aiPassages.slice")

    evidence_pos = text.find("const evidence: SourceEvidence = {", control_pos)
    if evidence_pos < 0:
        return False

    brace_end = evidence_pos + len("const evidence: SourceEvidence = {")
    probe = text[brace_end:brace_end + 120]

    if "...item," not in probe:
        text = text[:brace_end] + "\n                      ...item," + text[brace_end:]

    if text == original:
        return False

    backup(path)
    path.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").expanduser().resolve()

    paths = {
        "source-documents-dialog.tsx": root
        / "frontend"
        / "components"
        / "ennosmart"
        / "source-documents-dialog.tsx",
        "source_highlight.py": root
        / "backend_api"
        / "routers"
        / "source_highlight.py",
        "diagnosis-page.tsx": root
        / "frontend"
        / "components"
        / "ennosmart"
        / "diagnosis-page.tsx",
    }

    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        print("Fichier(s) introuvable(s) :")
        for item in missing:
            print(" -", item)
        return 2

    print("EnnoSmart - correction citations IA cliquables")
    print("Racine :", root)
    print()

    actions = [
        ("source-documents-dialog.tsx", patch_source_documents_dialog),
        ("source_highlight.py", patch_source_highlight),
        ("diagnosis-page.tsx", patch_diagnosis_page),
    ]

    try:
        for name, func in actions:
            path = paths[name]
            changed = func(path)
            prefix = "CORRIGE" if changed else "DEJA CORRIGE / INCHANGE"
            print(prefix, ":", path)
    except Exception as exc:
        print()
        print("ERREUR :", exc)
        print("Backups :", BACKUP_SUFFIX)
        return 1

    print()
    print("Correction terminee.")
    print("Redemarre le backend et actualise/redemarre le frontend.")
    print("Puis clique sur [1], [2] ou [3].")
    print()
    print("Resolution : citation -> passage/extrait -> chunks.json -> document -> surlignage")
    print("Normalement, pas besoin de relancer tout EnnoDiagnostic pour tester ce clic.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
