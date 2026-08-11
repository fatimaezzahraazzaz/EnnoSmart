# -*- coding: utf-8 -*-
from __future__ import annotations

"""Persistance du corpus documentaire complet pour le chat RAG.

Ce module conserve, après l'extraction et avant la réduction NLP, le texte
intégral des documents du projet. Il ne remplace ni le NLP ni le RAG principal.
Il fournit seulement une source documentaire complète au chat EnnoDiagnostic.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


CORPUS_VERSION = "full_document_corpus_v1"
CORPUS_DIRNAME = "fulltext_rag_v1"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_filename(value: str, default: str = "document") -> str:
    name = Path(str(value or default)).name
    stem = re.sub(r"[^A-Za-z0-9À-ÿ._-]+", "_", name).strip("._")
    return stem[:150] or default


def _hash(value: str, n: int = 12) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()[:n]


def _split_sections(text: str) -> List[Dict[str, Any]]:
    """Découpe les marqueurs produits par modules.extraction.text.office.

    Si aucun marqueur [SECTION : ...] n'existe, le document sera indexé via son
    champ texte complet par le chat. Aucun contenu n'est supprimé ici.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    pattern = re.compile(r"(?m)^\s*\[SECTION\s*:\s*(.+?)\]\s*$", flags=re.I)
    matches = list(pattern.finditer(raw))
    if not matches:
        return []

    sections: List[Dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        body = raw[start:end].strip()
        if not body:
            continue
        sections.append({
            "section_title": match.group(1).strip(),
            "text": body,
            "char_start": start,
            "char_end": end,
            "paragraph_index": index,
        })
    return sections


def persist_full_documents_for_chat(*, store: Any, documents: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Sauvegarde le texte complet extrait dans documents/processed/fulltext_rag_v1.

    L'appel est volontairement placé juste après ``load_documents()`` : à ce
    moment-là, l'extraction complète est encore disponible et n'a pas encore été
    réduite par le pipeline NLP en candidats/evidence packs.
    """
    target_dir = Path(store.documents_processed_dir) / CORPUS_DIRNAME
    target_dir.mkdir(parents=True, exist_ok=True)

    # Nettoyer uniquement les fichiers générés par ce module, jamais les autres
    # artefacts du dossier documents/processed.
    for path in target_dir.glob("*.json"):
        try:
            path.unlink()
        except Exception:
            pass

    records: List[Dict[str, Any]] = []
    total_chars = 0

    for index, raw in enumerate(documents or []):
        if not isinstance(raw, Mapping):
            continue
        text = _clean(raw.get("text"))
        if not text:
            continue

        document = _clean(raw.get("document") or raw.get("file_name") or f"document_{index + 1}")
        source_path = _clean(raw.get("source_path"))
        sections = _split_sections(text)
        payload = {
            "schema_version": CORPUS_VERSION,
            "project_id": _clean(getattr(store, "project_id", "")),
            "year": _clean(getattr(store, "year", "")),
            "document": Path(document).name,
            "document_id": _clean(raw.get("document_id")),
            "source_path": source_path,
            "extension": _clean(raw.get("extension")),
            "loader": _clean(raw.get("loader")),
            "document_type": _clean(raw.get("document_type")),
            "content_origin": _clean(raw.get("content_origin")),
            "source_policy": _clean(raw.get("source_policy")),
            "chars": len(text),
            "text_sha256": hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
            "text": text,
            "sections": sections,
            "sections_count": len(sections),
        }

        safe = _safe_filename(document, f"document_{index + 1}")
        output_path = target_dir / f"{index + 1:03d}_{safe}_{_hash(source_path or document)}.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        total_chars += len(text)
        records.append({
            "document": Path(document).name,
            "path": str(output_path),
            "chars": len(text),
            "sections_count": len(sections),
        })

    manifest = {
        "version": CORPUS_VERSION,
        "documents_count": len(records),
        "total_chars": total_chars,
        "documents": records,
    }
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest
