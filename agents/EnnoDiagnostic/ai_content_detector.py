# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import json
import math
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from modules.common.runtime_paths import data_root, organism_memory_root

from dotenv import load_dotenv


# ============================================================
# ENV
# ============================================================

def _load_project_env() -> None:
    candidates = [
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            load_dotenv(env_path, override=True)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except Exception:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return value in {"1", "true", "yes", "on", "oui"}


def _str_env(name: str, default: str) -> str:
    try:
        return str(os.getenv(name, default) or default)
    except Exception:
        return default


# ============================================================
# Helpers texte / chemins
# ============================================================

def clean_text(text: Any) -> str:
    text = str(text or "")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def safe_name(x: Any) -> str:
    """
    Slug stable pour IDs.
    IMPORTANT : on normalise '-' en '_' pour éviter le bug ai-radar vs ai_radar.
    """
    x = str(x or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    x = x.translate(tr)
    x = x.replace("-", "_")
    x = re.sub(r"[^a-z0-9_]+", "_", x)
    x = re.sub(r"_+", "_", x).strip("_")
    return x or "default"


def safe_name_keep_dash(x: Any) -> str:
    """Variante legacy : conserve '-' si un ancien dossier existe avec tiret."""
    x = str(x or "").strip().lower()
    tr = str.maketrans("àâäéèêëîïôöùûüç’'", "aaaeeeeiioouuuc__")
    x = x.translate(tr)
    x = re.sub(r"[^a-z0-9_\-]+", "_", x)
    x = re.sub(r"_+", "_", x).strip("_")
    return x or "default"


def _name_variants(x: Any) -> List[str]:
    raw = str(x or "").strip()
    vals = [
        raw,
        safe_name(raw),
        safe_name_keep_dash(raw),
        safe_name(raw).replace("_", "-"),
        safe_name_keep_dash(raw).replace("-", "_"),
        raw.replace("-", "_"),
        raw.replace("_", "-"),
        raw.lower(),
        raw.upper(),
    ]
    out: List[str] = []
    seen = set()
    for v in vals:
        v = str(v or "").strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def text_hash(text: str) -> str:
    return hashlib.sha1(
        clean_text(text).encode("utf-8", errors="ignore")
    ).hexdigest()[:16]


def split_long_text(text: str, max_chars: int = 2500) -> List[str]:
    """Compatibilité avec l'ancienne API."""
    return [item["text"] for item in split_long_text_with_offsets(text, max_chars)]


def split_long_text_with_offsets(
    text: str,
    max_chars: int = 2500,
) -> List[Dict[str, Any]]:
    """
    Découpe le texte tout en conservant une position locale approximative.

    Le frontend peut ensuite :
    - utiliser source_text_original / excerpt pour retrouver le texte ;
    - utiliser char_start / char_end si le passage source les possédait déjà.
    """
    normalized = clean_text(text)
    if not normalized:
        return []

    if len(normalized) <= max_chars:
        return [{"text": normalized, "local_char_start": 0, "local_char_end": len(normalized)}]

    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    chunks: List[str] = []
    current = ""

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue

        if len(current) + len(sent) + 1 <= max_chars:
            current = (current + " " + sent).strip()
        else:
            if current:
                chunks.append(current)
            current = sent

    if current:
        chunks.append(current)

    fixed: List[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            fixed.append(chunk)
        else:
            for i in range(0, len(chunk), max_chars):
                part = chunk[i:i + max_chars].strip()
                if part:
                    fixed.append(part)

    out: List[Dict[str, Any]] = []
    cursor = 0
    for chunk in fixed:
        start = normalized.find(chunk, cursor)
        if start < 0:
            start = normalized.find(chunk)
        if start < 0:
            start = cursor
        end = start + len(chunk)

        out.append(
            {
                "text": chunk,
                "local_char_start": start,
                "local_char_end": end,
            }
        )
        cursor = max(cursor, end)

    return out


def _read_json_safe(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        pass
    return default


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any, default: Any = "") -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return default


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        parsed = int(value)
        return parsed
    except Exception:
        return None


def _traceability_from_item(item: Any) -> Dict[str, Any]:
    """
    Extrait les informations nécessaires à SourceEvidenceCitations.

    La donnée peut se trouver :
    - directement dans l'item ;
    - dans item.metadata ;
    - dans item.source / item.source_json ;
    - dans metadata.source.
    """
    if not isinstance(item, dict):
        return {}

    metadata = _dict(item.get("metadata"))
    source = _dict(item.get("source"))
    source_json = _dict(item.get("source_json"))
    metadata_source = _dict(metadata.get("source"))

    document = str(
        _first(
            item.get("document"),
            item.get("document_name"),
            item.get("source_document"),
            item.get("filename"),
            item.get("file_name"),
            item.get("source_name"),
            source.get("document"),
            source.get("document_name"),
            source.get("source_document"),
            source.get("filename"),
            source_json.get("document"),
            source_json.get("document_name"),
            source_json.get("source_document"),
            source_json.get("filename"),
            metadata.get("document"),
            metadata.get("document_name"),
            metadata.get("source_document"),
            metadata.get("filename"),
            metadata.get("file_name"),
            metadata_source.get("document"),
            metadata_source.get("filename"),
            default="",
        )
        or ""
    ).strip()

    filename = str(
        _first(
            item.get("filename"),
            item.get("file_name"),
            source.get("filename"),
            source_json.get("filename"),
            metadata.get("filename"),
            metadata.get("file_name"),
            document,
            default="",
        )
        or ""
    ).strip()

    source_path = str(
        _first(
            item.get("source_path"),
            item.get("path"),
            item.get("file_path"),
            source.get("source_path"),
            source.get("path"),
            source_json.get("source_path"),
            source_json.get("path"),
            metadata.get("source_path"),
            metadata.get("path"),
            metadata.get("file_path"),
            metadata_source.get("source_path"),
            metadata_source.get("path"),
            default="",
        )
        or ""
    ).strip()

    section_title = str(
        _first(
            item.get("section_title"),
            item.get("section"),
            source.get("section_title"),
            source_json.get("section_title"),
            metadata.get("section_title"),
            metadata.get("section"),
            default="",
        )
        or ""
    ).strip()

    document_id = _first(
        item.get("document_id"),
        source.get("document_id"),
        source_json.get("document_id"),
        metadata.get("document_id"),
        metadata_source.get("document_id"),
        default=None,
    )

    rag_chunk_id = _first(
        item.get("rag_chunk_id"),
        metadata.get("rag_chunk_id"),
        source.get("rag_chunk_id"),
        source_json.get("rag_chunk_id"),
        default=None,
    )

    passage_id = _first(
        item.get("passage_id"),
        metadata.get("passage_id"),
        metadata.get("original_passage_id"),
        source.get("passage_id"),
        source_json.get("passage_id"),
        default=None,
    )

    page_number = _first(
        item.get("page_number"),
        item.get("page"),
        source.get("page_number"),
        metadata.get("page_number"),
        metadata.get("page"),
        default=None,
    )

    paragraph_index = _first(
        item.get("paragraph_index"),
        source.get("paragraph_index"),
        metadata.get("paragraph_index"),
        default=None,
    )

    char_start = _first(
        item.get("char_start"),
        item.get("start_char"),
        item.get("start"),
        source.get("char_start"),
        metadata.get("char_start"),
        metadata.get("start_char"),
        metadata.get("start"),
        default=None,
    )

    char_end = _first(
        item.get("char_end"),
        item.get("end_char"),
        item.get("end"),
        source.get("char_end"),
        metadata.get("char_end"),
        metadata.get("end_char"),
        metadata.get("end"),
        default=None,
    )

    sentence_start = _first(
        item.get("sentence_start"),
        source.get("sentence_start"),
        metadata.get("sentence_start"),
        default=None,
    )

    section_path = str(
        _first(
            item.get("section_path"),
            source.get("section_path"),
            metadata.get("section_path"),
            default="",
        )
        or ""
    ).strip()

    role = str(
        _first(
            item.get("role"),
            source.get("role"),
            metadata.get("role"),
            default="",
        )
        or ""
    ).strip()

    return {
        "document_id": document_id,
        "document": document,
        "document_name": document,
        "filename": filename,
        "source_path": source_path,
        "rag_chunk_id": rag_chunk_id,
        "original_passage_id": passage_id,
        "page_number": page_number,
        "paragraph_index": paragraph_index,
        "char_start": char_start,
        "char_end": char_end,
        "sentence_start": sentence_start,
        "section_title": section_title,
        "section_path": section_path,
        "source_role": role,
        "metadata": metadata,
    }


# ============================================================
# Loader STRICT : contenu extrait uniquement, avant LLM
# ============================================================

class EnnoExtractedContentLoader:
    """
    Charge uniquement des passages issus du contenu extrait ou des passages NLP source.

    V195 :
    - conserve la traçabilité document / passage pour l'interface [1], [2], ... ;
    - conserve document_id, source_path, page, paragraphe et offsets s'ils existent ;
    - résout aussi les informations stockées dans metadata/source/source_json ;
    - garde la compatibilité avec les anciens nlp_result.json.
    """

    SOURCE_TEXT_KEYS = [
        "raw_text",
        "original_text",
        "source_text",
        "source_text_original",
        "extracted_text",
        "text",
        "text_excerpt",
        "excerpt",
        "passage",
        "passage_source",
        "content",
        "body",
    ]

    GENERATED_TEXT_KEYS = [
        "reformulation",
        "summary",
        "resume",
        "synthesis",
        "synthese",
        "diagnostic",
        "diagnostic_complet",
        "llm_output",
        "answer",
        "content_generated",
        "generated_text",
    ]

    IMPORTANT_PACKS = [
        "objectifs_locaux",
        "verrous_rnd_locaux",
        "methodes_locales",
        "resultats_locaux",
        "limites_locales",
        "contributions_locales",
        "etat_art_local",
        "parametres_locaux",
        "contraintes_locales",
        "preuves_locales",
    ]

    DOCUMENT_LIST_KEYS = [
        "documents",
        "raw_documents",
        "processed_documents",
        "extracted_documents",
        "documents_extracted",
        "input_documents",
    ]

    def __init__(
        self,
        organisme: str,
        project: str,
        year: Optional[str] = None,
        base_dir: Optional[Path] = None,
        allow_rag_fallback: Optional[bool] = None,
        subproject: Optional[str] = None,
    ):
        _load_project_env()

        self.organisme_input = str(organisme or "")
        self.project_input = str(project or "")
        self.subproject_input = str(subproject or "").strip()
        self.year = str(
            year or os.getenv("ENNOSMART_PROJECT_YEAR", "") or ""
        ).strip()

        self.organisme = safe_name(organisme)
        self.project = safe_name(project)
        self.subproject = (
            safe_name(self.subproject_input)
            if self.subproject_input
            else ""
        )

        self.base_dir = Path(base_dir) if base_dir else data_root()
        self.storage_root = organism_memory_root()

        if allow_rag_fallback is None:
            allow_rag_fallback = (
                os.getenv("AI_DETECTOR_ALLOW_RAG_FALLBACK", "1") == "1"
            )
        self.allow_rag_fallback = bool(allow_rag_fallback)

        self.project_root = self._resolve_project_root()
        self.year_root = self._resolve_year_root()

        self.nlp_paths = self._candidate_nlp_paths()
        self.rag_chunks_paths = self._candidate_rag_paths()
        self.processed_dirs = self._candidate_processed_dirs()

        self.loader_report: Dict[str, Any] = {
            "input_policy": "extracted_content_before_llm_only",
            "traceability_version": "V195",
            "organisme_input": self.organisme_input,
            "project_input": self.project_input,
            "subproject_input": self.subproject_input,
            "year": self.year,
            "organisme_id": self.organisme,
            "project_id": self.project,
            "subproject_id": self.subproject,
            "project_root": str(self.project_root),
            "year_root": str(self.year_root) if self.year_root else "",
            "nlp_paths_checked": [str(p) for p in self.nlp_paths],
            "rag_chunks_paths_checked": [str(p) for p in self.rag_chunks_paths],
            "processed_dirs_checked": [str(p) for p in self.processed_dirs],
            "allow_rag_fallback": self.allow_rag_fallback,
            "used_sources": [],
            "excluded_generated_fields": self.GENERATED_TEXT_KEYS,
            "notes": [
                "Le loader ne lit jamais diagnostic_ennodiagnostic.json.",
                "Les champs générés/reformulés sont ignorés.",
                "Le score IA porte sur les textes sources extraits avant reformulation LLM.",
                "V195 : les métadonnées documentaires sont conservées jusqu'au frontend.",
            ],
        }

    # --------------------------
    # Résolution chemins
    # --------------------------

    def _resolve_project_root(self) -> Path:
        org_variants = _name_variants(self.organisme_input)
        project_variants = _name_variants(self.project_input)
        subproject_variants = _name_variants(self.subproject_input)

        candidates: List[Path] = []
        for org in org_variants:
            for proj in project_variants:
                parent = self.storage_root / org / "projects" / proj
                if subproject_variants:
                    for subproject in subproject_variants:
                        candidates.append(
                            parent / "subprojects" / subproject
                        )
                else:
                    candidates.append(parent)

        existing = [p for p in candidates if p.exists()]
        for p in existing:
            if (
                (p / "years").exists()
                or (p / "nlp").exists()
                or (p / "rag").exists()
            ):
                return p

        if existing:
            return existing[0]

        for org_dir in self.storage_root.glob("*"):
            if not org_dir.is_dir():
                continue
            if safe_name(org_dir.name) != self.organisme:
                continue

            projects_dir = org_dir / "projects"
            if not projects_dir.exists():
                continue

            for proj_dir in projects_dir.glob("*"):
                if (
                    proj_dir.is_dir()
                    and safe_name(proj_dir.name) == self.project
                ):
                    if not self.subproject:
                        return proj_dir

                    subprojects_dir = proj_dir / "subprojects"
                    if not subprojects_dir.exists():
                        continue

                    for subproject_dir in subprojects_dir.glob("*"):
                        if (
                            subproject_dir.is_dir()
                            and safe_name(subproject_dir.name)
                            == self.subproject
                        ):
                            return subproject_dir

        fallback = self.storage_root / self.organisme / "projects" / self.project
        if self.subproject:
            fallback = fallback / "subprojects" / self.subproject
        return fallback

    def _resolve_year_root(self) -> Optional[Path]:
        years_dir = self.project_root / "years"
        if not years_dir.exists():
            return None

        if self.year:
            direct = years_dir / self.year
            if direct.exists():
                return direct

        year_dirs = [p for p in years_dir.iterdir() if p.is_dir()]
        if not year_dirs:
            return None

        def year_score(p: Path) -> Tuple[int, str]:
            try:
                y = int(re.findall(r"\d{4}", p.name)[0])
            except Exception:
                y = 0
            has_data = int(
                (p / "nlp").exists()
                or (p / "rag").exists()
                or (p / "documents").exists()
            )
            return (has_data, f"{y:04d}")

        return sorted(year_dirs, key=year_score, reverse=True)[0]

    def _candidate_nlp_paths(self) -> List[Path]:
        paths: List[Path] = []
        if self.year_root:
            paths.append(self.year_root / "nlp" / "nlp_result.json")

        paths.append(self.project_root / "nlp" / "nlp_result.json")

        years_dir = self.project_root / "years"
        if years_dir.exists():
            for year_dir in sorted(years_dir.glob("*"), reverse=True):
                paths.append(year_dir / "nlp" / "nlp_result.json")

        return list(dict.fromkeys(paths))

    def _candidate_rag_paths(self) -> List[Path]:
        paths: List[Path] = []
        if self.year_root:
            paths.append(self.year_root / "rag" / "chunks.json")

        paths.append(self.project_root / "rag" / "chunks.json")

        years_dir = self.project_root / "years"
        if years_dir.exists():
            for year_dir in sorted(years_dir.glob("*"), reverse=True):
                paths.append(year_dir / "rag" / "chunks.json")

        return list(dict.fromkeys(paths))

    def _candidate_processed_dirs(self) -> List[Path]:
        paths: List[Path] = []
        if self.year_root:
            paths.append(self.year_root / "documents" / "processed")

        paths.append(self.project_root / "documents" / "processed")

        years_dir = self.project_root / "years"
        if years_dir.exists():
            for year_dir in sorted(years_dir.glob("*"), reverse=True):
                paths.append(year_dir / "documents" / "processed")

        return list(dict.fromkeys(paths))

    # --------------------------
    # Load passages
    # --------------------------

    def load_passages(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []

        nlp_loaded = False
        for nlp_path in self.nlp_paths:
            if not nlp_path.exists():
                continue

            nlp_data = _read_json_safe(nlp_path, {}) or {}
            nlp_passages = self._load_from_nlp(nlp_data)
            passages.extend(nlp_passages)
            nlp_loaded = True

            self.loader_report["used_sources"].append(
                {
                    "source": "nlp_result",
                    "path": str(nlp_path),
                    "passages_loaded": len(nlp_passages),
                }
            )

            if nlp_passages:
                break

        if not passages:
            for processed_dir in self.processed_dirs:
                loaded = self._load_from_processed_dir(processed_dir)
                passages.extend(loaded)

                self.loader_report["used_sources"].append(
                    {
                        "source": "documents_processed",
                        "path": str(processed_dir),
                        "passages_loaded": len(loaded),
                    }
                )

                if loaded:
                    break

        if not passages and self.allow_rag_fallback:
            for rag_path in self.rag_chunks_paths:
                if not rag_path.exists():
                    continue

                rag_passages = self._load_from_rag_chunks(rag_path)
                passages.extend(rag_passages)

                self.loader_report["used_sources"].append(
                    {
                        "source": "rag_chunks_source_fallback",
                        "path": str(rag_path),
                        "passages_loaded": len(rag_passages),
                        "warning": (
                            "fallback accepté uniquement si les chunks "
                            "proviennent des sources extraites"
                        ),
                    }
                )

                if rag_passages:
                    break

        passages = self._dedupe_passages(passages)

        documents = sorted(
            {
                str(p.get("document", "")).strip()
                for p in passages
                if p.get("document")
            }
        )

        traceable = [
            p
            for p in passages
            if (
                p.get("document_id")
                or p.get("document")
                or p.get("source_path")
            )
        ]

        self.loader_report["nlp_found"] = nlp_loaded
        self.loader_report["total_passages_after_dedupe"] = len(passages)
        self.loader_report["documents_covered"] = documents
        self.loader_report["documents_covered_count"] = len(documents)
        self.loader_report["traceable_passages_count"] = len(traceable)
        self.loader_report["traceable_passages_share"] = round(
            len(traceable) / max(1, len(passages)),
            4,
        )

        return passages, self.loader_report

    def _is_generated_like(self, item: Dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False

        joined = " ".join(
            str(item.get(k, ""))
            for k in [
                "source",
                "origin",
                "content_origin",
                "text_origin",
                "generation_origin",
                "created_by",
                "producer",
            ]
        ).lower()

        bad = [
            "llm",
            "generated",
            "reformulation",
            "diagnostic",
            "gemini",
            "openrouter",
            "chatgpt",
        ]

        return any(marker in joined for marker in bad)

    def _extract_source_text_from_item(self, item: Any) -> str:
        if isinstance(item, str):
            return clean_text(item)

        if not isinstance(item, dict):
            return ""

        if self._is_generated_like(item):
            return ""

        # Priorité au texte direct de l'item.
        direct_parts: List[str] = []
        for key in self.SOURCE_TEXT_KEYS:
            if key in self.GENERATED_TEXT_KEYS:
                continue

            value = item.get(key)
            if isinstance(value, str):
                text = clean_text(value)
                if len(text) > 20:
                    direct_parts.append(text)

        if direct_parts:
            # Évite de concaténer plusieurs preuves documentaires dans un seul
            # passage lorsque l'item possède déjà son vrai texte source.
            unique: List[str] = []
            seen = set()
            for value in direct_parts:
                key = text_hash(value[:600])
                if key not in seen:
                    seen.add(key)
                    unique.append(value)
            return clean_text(" ".join(unique))

        # Fallback anciens JSON : recherche dans les sous-preuves.
        nested_parts: List[str] = []
        for key in [
            "evidence",
            "source",
            "sources",
            "supporting_passages",
            "preuves",
        ]:
            value = item.get(key)

            if isinstance(value, str):
                text = clean_text(value)
                if len(text) > 20:
                    nested_parts.append(text)

            elif isinstance(value, list):
                for sub in value:
                    sub_text = self._extract_source_text_from_item(sub)
                    if sub_text:
                        nested_parts.append(sub_text)

            elif isinstance(value, dict):
                sub_text = self._extract_source_text_from_item(value)
                if sub_text:
                    nested_parts.append(sub_text)

        return clean_text(" ".join(nested_parts))

    def _get_doc_name(self, item: Dict[str, Any], default: str = "") -> str:
        trace = _traceability_from_item(item)
        return str(trace.get("document") or default or "")

    def _get_section(self, item: Dict[str, Any]) -> str:
        trace = _traceability_from_item(item)
        return str(trace.get("section_title") or "")

    def _build_passage(
        self,
        *,
        item: Any,
        passage_id: str,
        pack: str,
        role: str,
        text: str,
        source: str,
        default_document: str = "",
        forced_source_path: str = "",
    ) -> Dict[str, Any]:
        trace = _traceability_from_item(item)

        document = str(
            trace.get("document")
            or default_document
            or ""
        ).strip()

        section = str(trace.get("section_title") or "").strip()

        passage = {
            "passage_id": passage_id,
            "rag_chunk_id": trace.get("rag_chunk_id"),
            "pack": pack,
            "role": role,
            "document_id": trace.get("document_id"),
            "document": document,
            "document_name": document,
            "filename": trace.get("filename") or document,
            "source_path": trace.get("source_path") or forced_source_path,
            "page_number": trace.get("page_number"),
            "paragraph_index": trace.get("paragraph_index"),
            "char_start": trace.get("char_start"),
            "char_end": trace.get("char_end"),
            "sentence_start": trace.get("sentence_start"),
            "section": section,
            "section_title": section,
            "section_path": trace.get("section_path") or "",
            "text": clean_text(text),
            "source_text_original": clean_text(text),
            "excerpt": clean_text(text),
            "source": source,
            "text_origin": "extracted_before_llm",
            "metadata": trace.get("metadata") or {},
        }

        # Ne pas écraser un id source explicite.
        if trace.get("original_passage_id"):
            passage["original_passage_id"] = trace.get("original_passage_id")

        return passage

    def _load_from_nlp(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []

        if not isinstance(data, dict):
            return passages

        # Documents complets.
        for doc_key in self.DOCUMENT_LIST_KEYS:
            docs = data.get(doc_key)
            if not isinstance(docs, list):
                continue

            for idx, doc in enumerate(docs):
                if not isinstance(doc, dict):
                    continue

                text = self._extract_source_text_from_item(doc)
                if not text:
                    continue

                passage_id = str(
                    doc.get("passage_id")
                    or doc.get("id")
                    or doc.get("document_id")
                    or f"{doc_key}_{idx}"
                )

                passages.append(
                    self._build_passage(
                        item=doc,
                        passage_id=passage_id,
                        pack="document_extrait",
                        role="document_extrait",
                        text=text,
                        source="nlp_result_document_source",
                        default_document=f"document_{idx}",
                    )
                )

        # Packs top-level.
        for pack in self.IMPORTANT_PACKS:
            items = data.get(pack) or []
            passages.extend(
                self._load_items_from_pack(
                    items,
                    pack,
                    "nlp_result_pack_source",
                )
            )

        # Evidence packs.
        for evidence_key in [
            "evidence_pack_for_ennodiagnostic",
            "evidence_pack_before_frascati",
            "qualified_pack_for_ennodiagnostic",
        ]:
            pack_obj = data.get(evidence_key) or {}

            if not isinstance(pack_obj, dict):
                continue

            for pack, items in pack_obj.items():
                if isinstance(items, list):
                    passages.extend(
                        self._load_items_from_pack(
                            items,
                            str(pack),
                            f"nlp_{evidence_key}",
                        )
                    )

        sections = data.get("sections") or []
        if isinstance(sections, list):
            passages.extend(
                self._load_items_from_pack(
                    sections,
                    "sections",
                    "nlp_sections_source",
                )
            )

        return passages

    def _load_items_from_pack(
        self,
        items: Any,
        pack: str,
        source: str,
        inherited_item: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []

        if not isinstance(items, list):
            return passages

        for idx, item in enumerate(items):
            if isinstance(item, str):
                text = clean_text(item)
                source_item: Any = self._inherit_traceability(
                    {},
                    inherited_item,
                )
            elif isinstance(item, dict):
                text = clean_text(
                    self._extract_source_text_from_item(item)
                )
                source_item = self._inherit_traceability(
                    item,
                    inherited_item,
                )
            else:
                continue

            if not text:
                continue

            if isinstance(source_item, dict):
                passage_id = str(
                    source_item.get("passage_id")
                    or source_item.get("id")
                    or source_item.get("rag_chunk_id")
                    or _dict(source_item.get("metadata")).get("passage_id")
                    or _dict(source_item.get("metadata")).get("rag_chunk_id")
                    or ""
                )
            else:
                passage_id = ""

            if not passage_id:
                passage_id = f"{pack}_{idx}_{text_hash(text[:300])}"

            passages.append(
                self._build_passage(
                    item=source_item,
                    passage_id=passage_id,
                    pack=pack,
                    role=self._pack_to_role(pack),
                    text=text,
                    source=source,
                )
            )

        return passages

    def _inherit_traceability(
        self,
        item: Dict[str, Any],
        parent: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Conserve la source d'un document dans ses sections extraites."""
        child = dict(item or {})
        if not isinstance(parent, dict) or not parent:
            return child

        parent_trace = _traceability_from_item(parent)
        inherited = {
            "document_id": parent_trace.get("document_id"),
            "document": parent_trace.get("document"),
            "document_name": parent_trace.get("document_name"),
            "filename": parent_trace.get("filename"),
            "source_path": parent_trace.get("source_path"),
        }

        for key, value in inherited.items():
            if value not in (None, "") and child.get(key) in (None, ""):
                child[key] = value

        parent_metadata = _dict(parent.get("metadata"))
        child_metadata = _dict(child.get("metadata"))
        if parent_metadata or child_metadata:
            child["metadata"] = {
                **parent_metadata,
                **child_metadata,
            }

        return child

    def _load_from_processed_dir(
        self,
        processed_dir: Path,
    ) -> List[Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []

        if not processed_dir.exists():
            return passages

        for path in sorted(processed_dir.rglob("*")):
            if not path.is_file():
                continue

            if path.name.lower().startswith("diagnostic_"):
                continue

            if path.suffix.lower() not in {".json", ".txt"}:
                continue

            try:
                if path.suffix.lower() == ".txt":
                    text = clean_text(
                        path.read_text(
                            encoding="utf-8",
                            errors="ignore",
                        )
                    )

                    if text:
                        passages.append(
                            self._build_passage(
                                item={},
                                passage_id=f"processed_txt_{text_hash(str(path))}",
                                pack="document_extrait",
                                role="document_extrait",
                                text=text,
                                source="processed_txt_source",
                                default_document=path.name,
                                forced_source_path=str(path),
                            )
                        )
                else:
                    data = _read_json_safe(path, None)
                    passages.extend(
                        self._extract_from_any_json(
                            data,
                            source_file=path.name,
                        )
                    )
            except Exception:
                continue

        return passages

    def _extract_from_any_json(
        self,
        data: Any,
        source_file: str,
    ) -> List[Dict[str, Any]]:
        passages: List[Dict[str, Any]] = []

        if isinstance(data, dict):
            text = self._extract_source_text_from_item(data)

            if text:
                passages.append(
                    self._build_passage(
                        item=data,
                        passage_id=(
                            str(
                                data.get("passage_id")
                                or data.get("id")
                                or ""
                            )
                            or f"processed_json_{text_hash(source_file + text[:100])}"
                        ),
                        pack="document_extrait",
                        role="document_extrait",
                        text=text,
                        source="processed_json_source",
                        default_document=source_file,
                    )
                )

            for key, value in data.items():
                if key in self.GENERATED_TEXT_KEYS:
                    continue

                if isinstance(value, list):
                    passages.extend(
                        self._load_items_from_pack(
                            value,
                            str(key),
                            "processed_json_source",
                            inherited_item=data,
                        )
                    )

        elif isinstance(data, list):
            passages.extend(
                self._load_items_from_pack(
                    data,
                    "document_extrait",
                    "processed_json_source",
                )
            )

        return passages

    def _load_from_rag_chunks(
        self,
        rag_path: Path,
    ) -> List[Dict[str, Any]]:
        data = _read_json_safe(rag_path, [])

        if isinstance(data, dict):
            chunks = data.get("chunks") or data.get("items") or []
        elif isinstance(data, list):
            chunks = data
        else:
            chunks = []

        passages: List[Dict[str, Any]] = []

        for idx, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue

            metadata = _dict(chunk.get("metadata"))

            if (
                self._is_generated_like(chunk)
                or self._is_generated_like(metadata)
            ):
                continue

            text = clean_text(
                chunk.get("source_text")
                or chunk.get("source_text_original")
                or chunk.get("excerpt")
                or chunk.get("text")
                or ""
            )

            if not text:
                continue

            trace_item = {
                **metadata,
                **chunk,
                "metadata": metadata,
            }

            passage_id = str(
                chunk.get("passage_id")
                or chunk.get("id")
                or metadata.get("passage_id")
                or metadata.get("original_passage_id")
                or metadata.get("rag_chunk_id")
                or f"rag_{idx}"
            )

            passage = self._build_passage(
                item=trace_item,
                passage_id=passage_id,
                pack=str(metadata.get("pack_key") or ""),
                role=str(
                    metadata.get("role")
                    or self._pack_to_role(
                        str(metadata.get("pack_key") or "")
                    )
                ),
                text=text,
                source="rag_chunks_source_fallback",
            )

            passage["text_origin"] = (
                "extracted_before_llm_via_rag_chunk"
            )
            passages.append(passage)

        return passages

    def _pack_to_role(self, pack: str) -> str:
        pack = str(pack or "").lower()

        if "objectif" in pack:
            return "objectif"
        if "verrou" in pack:
            return "verrou"
        if "methode" in pack or "méthode" in pack:
            return "methode"
        if "resultat" in pack or "résultat" in pack:
            return "resultat"
        if "limite" in pack:
            return "limite"
        if "etat_art" in pack or "état" in pack:
            return "etat_art"
        if "parametre" in pack or "paramètre" in pack:
            return "parametre"
        if "contrainte" in pack:
            return "contrainte"
        if "section" in pack:
            return "section"

        return "autre"

    def _dedupe_passages(
        self,
        passages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        seen = set()
        out: List[Dict[str, Any]] = []

        for passage in passages:
            text = clean_text(passage.get("text", ""))
            if not text:
                continue

            # Document + texte afin de ne pas supprimer à tort deux passages
            # identiques provenant de deux documents distincts.
            identity = (
                f"{passage.get('document_id') or ''}|"
                f"{passage.get('document') or ''}|"
                f"{text[:1200].lower()}"
            )
            key = text_hash(identity)

            if key in seen:
                continue

            seen.add(key)
            out.append(passage)

        return out


# ============================================================
# Heuristiques simples
# ============================================================

class AIHeuristicScorer:
    GENERIC_PATTERNS = [
        r"\bvise à\b",
        r"\ba pour objectif\b",
        r"\bs'inscrit dans\b",
        r"\bdans le cadre de\b",
        r"\bpermet de\b",
        r"\baméliorer\b",
        r"\boptimiser\b",
        r"\brévolutionner\b",
        r"\bmettre en place\b",
        r"\bsolution innovante\b",
        r"\bapproche innovante\b",
        r"\bgains? de productivité\b",
        r"\bqualité\b",
        r"\brobustesse\b",
        r"\bpertinence\b",
        r"\benjeux\b",
        r"\bstratégique\b",
    ]

    EVIDENCE_PATTERNS = [
        r"\b\d+(\,\d+|\.\d+)?\s?%\b",
        r"\b\d+\s?(ms|s|min|h|jours?|semaines?|mois|µm|μm|mg|ml|cm²|cm2)\b",
        r"\bp\s?<\s?0[,.]\d+\b",
        r"\bn\s?=\s?\d+\b",
        r"\bprototype\b",
        r"\btesté\b",
        r"\bmesuré\b",
        r"\bdosé\b",
        r"\brésultat\b",
        r"\bexpérience\b",
        r"\bessai\b",
        r"\btableau\b",
        r"\bfigure\b",
    ]

    def score(self, text: str) -> Dict[str, Any]:
        text = clean_text(text)
        lower = text.lower()
        reasons: List[str] = []
        score = 0.0

        if len(text) < 120:
            return {
                "heuristic_score": 0.0,
                "reasons": [
                    "passage trop court pour une analyse heuristique fiable"
                ],
            }

        sentences = [
            sentence.strip()
            for sentence in re.split(r"[.!?]", text)
            if len(sentence.strip()) > 20
        ]

        if sentences:
            lengths = [len(sentence.split()) for sentence in sentences]
            avg_len = sum(lengths) / max(1, len(lengths))

            if avg_len > 24:
                score += 0.12
                reasons.append("phrases longues et très structurées")

            if len(sentences) >= 4:
                variance = (
                    sum((value - avg_len) ** 2 for value in lengths)
                    / len(lengths)
                )
                if math.sqrt(variance) < 8:
                    score += 0.10
                    reasons.append(
                        "longueur des phrases très régulière"
                    )

        generic_hits = sum(
            1
            for pattern in self.GENERIC_PATTERNS
            if re.search(pattern, lower, flags=re.IGNORECASE)
        )

        if generic_hits >= 4:
            score += 0.18
            reasons.append(
                "plusieurs formulations génériques ou institutionnelles"
            )
        elif generic_hits >= 2:
            score += 0.10
            reasons.append("quelques formulations génériques")

        words = re.findall(r"\b[a-zA-ZÀ-ÿ]{5,}\b", lower)
        if len(words) > 60:
            frequency: Dict[str, int] = {}
            for word in words:
                frequency[word] = frequency.get(word, 0) + 1

            repeated = [
                word
                for word, count in frequency.items()
                if count >= 4
            ]

            if len(repeated) >= 4:
                score += 0.12
                reasons.append(
                    "répétition de plusieurs termes importants"
                )

        evidence_hits = sum(
            1
            for pattern in self.EVIDENCE_PATTERNS
            if re.search(pattern, text, flags=re.IGNORECASE)
        )

        if evidence_hits == 0 and len(text) > 400:
            score += 0.18
            reasons.append(
                "peu ou pas de preuves techniques concrètes détectées"
            )
        elif evidence_hits <= 1 and len(text) > 700:
            score += 0.10
            reasons.append(
                "peu d'éléments techniques vérifiables"
            )

        connectors = [
            "ainsi",
            "cependant",
            "par ailleurs",
            "en outre",
            "de plus",
            "dans ce contexte",
            "afin de",
            "en particulier",
        ]

        connector_hits = sum(
            1 for connector in connectors if connector in lower
        )

        if connector_hits >= 4:
            score += 0.10
            reasons.append(
                "enchaînement rédactionnel très fluide et standardisé"
            )

        score = max(0.0, min(1.0, score))

        if not reasons:
            reasons.append(
                "aucun signal heuristique fort détecté"
            )

        return {
            "heuristic_score": round(score, 4),
            "reasons": reasons,
        }


# ============================================================
# ModernBERT Detector
# ============================================================

class ModernBERTAITextDetector:
    def __init__(
        self,
        model_name: Optional[str] = None,
        max_chars: Optional[int] = None,
        min_chars: Optional[int] = None,
        model_weight: Optional[float] = None,
        heuristic_weight: Optional[float] = None,
    ):
        _load_project_env()

        self.model_name = model_name or os.getenv(
            "AI_DETECTOR_MODEL",
            "AICodexLab/answerdotai-ModernBERT-base-ai-detector",
        )
        self.max_chars = max_chars or _int_env(
            "AI_DETECTOR_MAX_CHARS",
            2500,
        )
        self.min_chars = min_chars or _int_env(
            "AI_DETECTOR_MIN_CHARS",
            120,
        )
        self.threshold_medium = _float_env(
            "AI_DETECTOR_THRESHOLD_MEDIUM",
            0.45,
        )
        self.threshold_high = _float_env(
            "AI_DETECTOR_THRESHOLD_HIGH",
            0.70,
        )
        self.model_weight = model_weight or _float_env(
            "AI_DETECTOR_MODEL_WEIGHT",
            0.75,
        )
        self.heuristic_weight = heuristic_weight or _float_env(
            "AI_DETECTOR_HEURISTIC_WEIGHT",
            0.25,
        )
        self.device_name = os.getenv(
            "AI_DETECTOR_DEVICE",
            "cpu",
        ).lower().strip()
        self.max_tokens = max(
            64,
            _int_env("AI_DETECTOR_MAX_TOKENS", 512),
        )

        self.heuristic = AIHeuristicScorer()
        self.tokenizer = None
        self.model = None
        self.torch_device = None
        self._torch = None
        self._model_load_error: Optional[str] = None
        self._model_error_reported = False

        # Conservé pour compatibilité avec l'ancien code.
        self.pipeline = None

    def load_model(self):
        if self.pipeline is not None:
            return self.pipeline

        if self._model_load_error:
            raise RuntimeError(self._model_load_error)

        try:
            import torch

            try:
                from transformers import (
                    AutoModelForSequenceClassification,
                    AutoTokenizer,
                )
            except ImportError:
                from transformers.models.auto.modeling_auto import (
                    AutoModelForSequenceClassification,
                )
                from transformers.models.auto.tokenization_auto import (
                    AutoTokenizer,
                )

            use_cuda = (
                self.device_name == "cuda"
                and torch.cuda.is_available()
            )

            self.torch_device = torch.device(
                "cuda" if use_cuda else "cpu"
            )
            device_label = str(self.torch_device)

            load_kwargs: Dict[str, Any] = {}
            if _bool_env("AI_DETECTOR_OFFLINE", False):
                load_kwargs["local_files_only"] = True

            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                **load_kwargs,
            )
            self.model = (
                AutoModelForSequenceClassification
                .from_pretrained(
                    self.model_name,
                    **load_kwargs,
                )
            )

            self.model.to(self.torch_device)
            self.model.eval()

            self._torch = torch
            self.pipeline = self._predict_direct

            print(
                f"✅ AI detector chargé sur {device_label} "
                "(inférence directe, sans pipeline)"
            )
            return self.pipeline

        except Exception as exc:
            self._model_load_error = (
                f"Chargement du modèle IA impossible : {exc}"
            )
            raise RuntimeError(
                self._model_load_error
            ) from exc

    def _predict_direct(
        self,
        text: str,
    ) -> List[Dict[str, Any]]:
        if (
            self.tokenizer is None
            or self.model is None
            or self._torch is None
        ):
            raise RuntimeError(
                "Le modèle IA n'est pas chargé."
            )

        tokenizer_limit = getattr(
            self.tokenizer,
            "model_max_length",
            self.max_tokens,
        )

        try:
            tokenizer_limit = int(tokenizer_limit)
        except Exception:
            tokenizer_limit = self.max_tokens

        if (
            tokenizer_limit <= 0
            or tokenizer_limit > 100_000
        ):
            tokenizer_limit = self.max_tokens

        max_length = max(
            64,
            min(self.max_tokens, tokenizer_limit),
        )

        encoded = self.tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=max_length,
            padding=False,
        )

        encoded = {
            key: value.to(self.torch_device)
            for key, value in encoded.items()
        }

        with self._torch.inference_mode():
            logits = self.model(**encoded).logits
            probabilities = self._torch.softmax(
                logits,
                dim=-1,
            )[0]

        best_index = int(
            self._torch.argmax(probabilities).item()
        )

        id2label = (
            getattr(self.model.config, "id2label", {})
            or {}
        )

        label = id2label.get(
            best_index,
            id2label.get(
                str(best_index),
                f"LABEL_{best_index}",
            ),
        )

        return [
            {
                "label": str(label),
                "score": float(
                    probabilities[best_index].item()
                ),
            }
        ]

    def _model_score_to_ai_score(
        self,
        outputs: Any,
    ) -> Tuple[float, str, float]:
        if (
            isinstance(outputs, list)
            and outputs
            and isinstance(outputs[0], list)
        ):
            outputs = outputs[0]

        if isinstance(outputs, dict):
            outputs = [outputs]

        if not isinstance(outputs, list) or not outputs:
            return 0.0, "unknown", 0.0

        best = outputs[0]
        label = str(best.get("label", "unknown"))
        confidence = float(best.get("score", 0.0))
        lower = label.lower()

        if any(
            marker in lower
            for marker in ["ai", "generated", "machine", "llm"]
        ):
            return confidence, label, confidence

        if "human" in lower or "real" in lower:
            return 1.0 - confidence, label, confidence

        if lower in ["label_1", "1"]:
            return confidence, label, confidence

        if lower in ["label_0", "0"]:
            return 1.0 - confidence, label, confidence

        return confidence, label, confidence

    def _risk_level(self, score: float) -> str:
        if score >= self.threshold_high:
            return "élevé"
        if score >= self.threshold_medium:
            return "moyen"
        return "faible"

    def analyze_text(self, text: str) -> Dict[str, Any]:
        text = clean_text(text)

        if len(text) < self.min_chars:
            return {
                "ai_score": 0.0,
                "model_ai_score": 0.0,
                "heuristic_score": 0.0,
                "risk_level": "faible",
                "model_label": "too_short",
                "model_confidence": 0.0,
                "reasons": ["passage trop court"],
            }

        truncated = text[:self.max_chars]

        try:
            model = self.load_model()
            raw_outputs = model(truncated)

            (
                model_ai_score,
                model_label,
                model_confidence,
            ) = self._model_score_to_ai_score(
                raw_outputs
            )

        except Exception as exc:
            if not self._model_error_reported:
                print(
                    "⚠ Modèle IA indisponible, "
                    f"score heuristique conservé : {exc}"
                )
                self._model_error_reported = True

            model_ai_score = 0.0
            model_label = "model_error"
            model_confidence = 0.0

        heuristic_result = self.heuristic.score(text)
        heuristic_score = float(
            heuristic_result["heuristic_score"]
        )

        if model_label == "model_error":
            final_score = heuristic_score
        else:
            final_score = (
                self.model_weight * model_ai_score
                + self.heuristic_weight * heuristic_score
            )

        final_score = max(
            0.0,
            min(1.0, final_score),
        )

        return {
            "ai_score": round(final_score, 4),
            "model_ai_score": round(model_ai_score, 4),
            "heuristic_score": round(heuristic_score, 4),
            "risk_level": self._risk_level(final_score),
            "model_label": model_label,
            "model_confidence": round(
                model_confidence,
                4,
            ),
            "reasons": heuristic_result.get(
                "reasons",
                [],
            ),
        }

    def analyze_passages(
        self,
        passages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        V195 : la sortie IA conserve les coordonnées documentaires.

        C'est ce bloc qui rend possible la chaîne :
        passage suspect -> [1] -> document -> surlignage.
        """
        analyzed: List[Dict[str, Any]] = []

        trace_keys = [
            "document_id",
            "document",
            "document_name",
            "filename",
            "source_path",
            "page_number",
            "paragraph_index",
            "sentence_start",
            "section",
            "section_title",
            "section_path",
            "pack",
            "role",
            "source",
            "text_origin",
            "rag_chunk_id",
            "metadata",
        ]

        for idx, passage in enumerate(passages):
            original_text = clean_text(
                passage.get("text", "")
            )

            if not original_text:
                continue

            parent_id = str(
                passage.get("passage_id")
                or passage.get("original_passage_id")
                or idx
            )

            base_char_start = _int_or_none(
                passage.get("char_start")
            )
            base_char_end = _int_or_none(
                passage.get("char_end")
            )

            chunks = split_long_text_with_offsets(
                original_text,
                max_chars=self.max_chars,
            )

            for chunk_index, chunk in enumerate(chunks):
                subtext = clean_text(
                    chunk.get("text", "")
                )

                if len(subtext) < self.min_chars:
                    continue

                score_data = self.analyze_text(subtext)

                local_start = _int_or_none(
                    chunk.get("local_char_start")
                )
                local_end = _int_or_none(
                    chunk.get("local_char_end")
                )

                if (
                    base_char_start is not None
                    and local_start is not None
                ):
                    char_start = (
                        base_char_start + local_start
                    )
                else:
                    char_start = passage.get(
                        "char_start"
                    )

                if (
                    base_char_start is not None
                    and local_end is not None
                ):
                    char_end = (
                        base_char_start + local_end
                    )
                else:
                    char_end = passage.get(
                        "char_end"
                    )

                item: Dict[str, Any] = {
                    "passage_id": (
                        f"{parent_id}_{chunk_index}"
                    ),
                    "parent_passage_id": parent_id,
                    "original_passage_id": (
                        passage.get(
                            "original_passage_id"
                        )
                        or parent_id
                    ),
                    "text": subtext,
                    "text_excerpt": subtext,
                    "source_text_original": subtext,
                    "excerpt": subtext,
                    "char_start": char_start,
                    "char_end": char_end,
                    "local_char_start": local_start,
                    "local_char_end": local_end,
                    **score_data,
                    "needs_human_validation": (
                        score_data["risk_level"]
                        in ["moyen", "élevé"]
                    ),
                }

                for key in trace_keys:
                    if key in passage:
                        item[key] = passage.get(key)

                # Garanties pour les noms attendus par le frontend.
                document = str(
                    item.get("document")
                    or item.get("document_name")
                    or item.get("filename")
                    or ""
                ).strip()

                if document:
                    item["document"] = document
                    item["document_name"] = (
                        item.get("document_name")
                        or document
                    )
                    item["filename"] = (
                        item.get("filename")
                        or document
                    )

                # On remet les offsets calculés après la copie des traces.
                item["char_start"] = char_start
                item["char_end"] = char_end

                analyzed.append(item)

        analyzed = sorted(
            analyzed,
            key=lambda item: item.get(
                "ai_score",
                0,
            ),
            reverse=True,
        )

        global_score = self._global_score(
            analyzed
        )

        suspected = [
            item
            for item in analyzed
            if item.get("risk_level")
            in ["moyen", "élevé"]
        ]

        traceable_suspected = [
            item
            for item in suspected
            if (
                item.get("document_id")
                or item.get("document")
                or item.get("source_path")
            )
        ]

        return {
            "generated_at": (
                datetime.now().isoformat(
                    timespec="seconds"
                )
            ),
            "traceability_version": "V195",
            "model_name": self.model_name,
            "device": self.device_name,
            "input_policy": (
                "extracted_content_before_llm_only"
            ),
            "global_ai_score": round(
                global_score,
                4,
            ),
            "global_ai_percentage": round(
                global_score * 100,
                2,
            ),
            "risk_level": self._risk_level(
                global_score
            ),
            "total_passages_analyzed": len(
                analyzed
            ),
            "suspected_passages_count": len(
                suspected
            ),
            "traceable_suspected_passages_count": (
                len(traceable_suspected)
            ),
            "high_risk_passages_count": len(
                [
                    item
                    for item in analyzed
                    if item.get("risk_level")
                    == "élevé"
                ]
            ),
            "medium_risk_passages_count": len(
                [
                    item
                    for item in analyzed
                    if item.get("risk_level")
                    == "moyen"
                ]
            ),
            "low_risk_passages_count": len(
                [
                    item
                    for item in analyzed
                    if item.get("risk_level")
                    == "faible"
                ]
            ),
            "passages": analyzed,
            "suspected_passages": suspected,
            "disclaimer": (
                "Ce score indique une suspicion de contenu "
                "généré ou reformulé par IA. "
                "Il porte uniquement sur les contenus extraits "
                "avant reformulation LLM. "
                "Il ne constitue pas une preuve certaine et "
                "doit être validé humainement."
            ),
        }

    def _global_score(
        self,
        analyzed: List[Dict[str, Any]],
    ) -> float:
        if not analyzed:
            return 0.0

        total_weight = 0.0
        weighted_sum = 0.0

        for item in analyzed:
            text_len = len(
                item.get("text", "")
            )
            length_weight = min(
                3.0,
                max(1.0, text_len / 600),
            )
            score = float(
                item.get("ai_score", 0.0)
            )
            weight = length_weight * (
                1.0 + score
            )

            weighted_sum += score * weight
            total_weight += weight

        return weighted_sum / max(
            1e-6,
            total_weight,
        )


# ============================================================
# Service projet complet
# ============================================================

class EnnoAIDetectionService:
    def __init__(
        self,
        organisme: str,
        project: str,
        year: Optional[str] = None,
        allow_rag_fallback: Optional[bool] = None,
        subproject: Optional[str] = None,
    ):
        _load_project_env()

        self.organisme_input = organisme
        self.project_input = project
        self.subproject_input = str(subproject or "").strip()
        self.year = str(
            year
            or os.getenv(
                "ENNOSMART_PROJECT_YEAR",
                "",
            )
            or ""
        ).strip()

        self.organisme = safe_name(organisme)
        self.project = safe_name(project)

        self.base_dir = data_root()

        self.loader = EnnoExtractedContentLoader(
            organisme=organisme,
            project=project,
            subproject=self.subproject_input,
            year=self.year,
            base_dir=self.base_dir,
            allow_rag_fallback=allow_rag_fallback,
        )

        self.project_root = (
            self.loader.project_root
        )
        self.year_root = (
            self.loader.year_root
        )

        self.diagnostics_dir = (
            self.year_root
            or self.project_root
        ) / "diagnostics"

        self.diagnostics_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.detector = (
            ModernBERTAITextDetector()
        )

    def run(
        self,
        save: bool = True,
    ) -> Dict[str, Any]:
        passages, loader_report = (
            self.loader.load_passages()
        )

        report = (
            self.detector.analyze_passages(
                passages
            )
        )

        result = {
            "ok": True,
            "organisme_id": self.organisme,
            "project_id": self.project,
            "subproject_id": safe_name(self.subproject_input)
            if self.subproject_input
            else "",
            "year": self.year,
            "input_policy": (
                "extracted_content_before_llm_only"
            ),
            "traceability_version": "V195",
            "loader_report": loader_report,
            "ai_detection": report,
            "detector_runtime": {
                "model_available": (
                    self.detector.pipeline
                    is not None
                    and not self.detector
                    ._model_load_error
                ),
                "fallback_heuristic_used": bool(
                    self.detector
                    ._model_load_error
                ),
                "inference_mode": (
                    "direct_auto_model_without_pipeline"
                ),
            },
            "summary": {
                "average_ai_score": (
                    report.get(
                        "global_ai_score",
                        0.0,
                    )
                ),
                "average_ai_percentage": (
                    report.get(
                        "global_ai_percentage",
                        0.0,
                    )
                ),
                "risk_level": report.get(
                    "risk_level",
                    "faible",
                ),
                "passages_count": report.get(
                    "total_passages_analyzed",
                    0,
                ),
                "suspected_passages_count": (
                    report.get(
                        "suspected_passages_count",
                        0,
                    )
                ),
                "traceable_suspected_passages_count": (
                    report.get(
                        "traceable_suspected_passages_count",
                        0,
                    )
                ),
                "high_count": report.get(
                    "high_risk_passages_count",
                    0,
                ),
                "medium_count": report.get(
                    "medium_risk_passages_count",
                    0,
                ),
                "low_count": report.get(
                    "low_risk_passages_count",
                    0,
                ),
            },
        }

        if save:
            self.save_report(result)

        return result

    def save_report(
        self,
        result: Dict[str, Any],
    ) -> Path:
        path = (
            self.diagnostics_dir
            / "ai_detection_report.json"
        )

        path.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return path


# ============================================================
# CLI test rapide
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "EnnoSmart AI Detection - "
            "extracted content only + source traceability"
        )
    )

    parser.add_argument(
        "--organisme",
        required=True,
    )
    parser.add_argument(
        "--project",
        required=True,
    )
    parser.add_argument(
        "--subproject",
        default="",
    )
    parser.add_argument(
        "--year",
        default="",
    )
    parser.add_argument(
        "--no-rag-fallback",
        action="store_true",
    )

    args = parser.parse_args()

    service = EnnoAIDetectionService(
        organisme=args.organisme,
        project=args.project,
        subproject=args.subproject,
        year=args.year,
        allow_rag_fallback=(
            not args.no_rag_fallback
        ),
    )

    result = service.run(save=True)

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )
